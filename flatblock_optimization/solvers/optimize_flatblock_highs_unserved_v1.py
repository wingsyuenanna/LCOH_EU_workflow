# optimize_flatblock_highs_unserved.py
# Solar + Battery optimization using HiGHS solver
# MODIFICATION: Allows unserved energy during peak hours only (when gas is blocked)

import os 
import pandas as pd
import numpy as np
import highspy
from highspy import Highs, HighsStatus, HighsModelStatus, HighsLp, HighsModel, ObjSense, HighsVarType

import time

def run_flatblock_optimization_highs(
    site_id,
    availability_factor,
    site_params,
    solar_profile_df,
    demand_profile,
    input_costs,
    load_target,
    VoLL=0  # Value of Lost Load ($/MWh)
        # Higher VoLL → optimizer builds more infrastructure to avoid unserved energy
        # Lower VoLL → optimizer accepts more unserved energy if infrastructure is too expensive
    ):
    
    ######################### Load inputs and initial calcs #########################
    
    # Extract financial and tech costs  
    fin_params   = input_costs["financial"]
    solar_params = input_costs["solar"]
    batt_params  = input_costs["battery"]

    CRF = fin_params["CRF"]

    solar_capex_per_kW = solar_params["Capex"]
    solar_om_per_MWyr = solar_params["Fixed O&M"]

    battery_capex_per_kWh = (batt_params["Pack"] + batt_params["Rack"] + batt_params["BOS + EMS"] + batt_params["EPC"])
    battery_capex_per_kW   = batt_params["PCS + Overhead"]
    battery_om_per_MWyr = batt_params["Fixed O&M"]
    battery_rte = 0.95
    inv_eff = 0.967

    solar_potential_MW = site_params["potential_mw_solar_15"]
    
    # Calculate load - LIMIT TO ONE YEAR
    solar_profile_df_year = solar_profile_df[solar_profile_df["year"] == solar_profile_df["year"].iloc[0]].copy()
    solar_profile = solar_profile_df_year["P_kWperkWp"].to_numpy(dtype=float)
    hours = len(solar_profile)
    P_load_MW = load_target
    total_load_MWh = P_load_MW * hours
    n_years = 1
    
    print(f"\n{'='*70}")
    print(f"Building optimization model for site {site_id}")
    print(f"{'='*70}")
    print(f"Load target: {load_target} MW")
    print(f"Availability factor: {availability_factor*100}% RE minimum")
    print(f"VoLL (unserved energy penalty): ${VoLL:,.0f}/MWh")
    print(f"{'='*70}\n")

    ######################### Start to build optimization #########################

    # Annual cost functions
    annual_solar_cost_func = lambda S: (solar_capex_per_kW*1000*S)*CRF + solar_om_per_MWyr*S
    annual_batt_cost_func  = lambda B_MW, B_MWh: (battery_capex_per_kW*1000*B_MW + battery_capex_per_kWh*1000*B_MWh)*CRF + battery_om_per_MWyr*B_MW

    # BESS efficiencies 
    eta_charge = eta_discharge = np.sqrt(battery_rte)

    # Initialize HiGHS model
    h = Highs()
    h.setOptionValue("log_to_console", True)
    h.setOptionValue("mip_rel_gap", 0.01)
    
    ######################### Define variables #########################
    
    var_idx = 0
    var_names = {}
    var_types = []
    var_lower = []
    var_upper = []
    obj_coeffs = []
    
    def add_var(name, lb=0.0, ub=float('inf'), is_integer=False, obj_coeff=0.0):
        nonlocal var_idx
        var_names[name] = var_idx
        var_lower.append(lb)
        var_upper.append(ub)
        var_types.append(1 if is_integer else 0)
        obj_coeffs.append(obj_coeff)
        var_idx += 1
        return var_names[name]
    
    # Capacity decision variables
    idx_S = add_var("S_MW")
    idx_B_MW = add_var("BESS_MW")
    idx_B_MWh = add_var("BESS_MWh")
    
    # Hourly operational variables
    idx_solar_used = [add_var(f"solar_used_{t}") for t in range(hours)]
    idx_charge = [add_var(f"charge_{t}") for t in range(hours)]
    idx_discharge = [add_var(f"discharge_{t}") for t in range(hours)]
    idx_soc = [add_var(f"soc_{t}") for t in range(hours)]
    # idx_bess_mode = [add_var(f"bess_mode_{t}", is_integer=True, ub=1.0) for t in range(hours)]
    
    # Unserved energy variables
    idx_unserved = [add_var(f"unserved_{t}") for t in range(hours)]
    
    print(f"Total variables: {var_idx}")
    
    ######################### Define constraints (sparse) #########################
    
    # Each constraint is stored as ({var_index: coefficient, ...}, lower_bound, upper_bound)
    constraints = []
    
    def add_constraint(sparse_coeffs: dict, lower=-float('inf'), upper=float('inf')):
        """
        Add a constraint using a sparse dict of {var_index: coefficient}.
        lower <= sum(coeff * x[var]) <= upper
        """
        constraints.append((sparse_coeffs, lower, upper))
    
    # --- Capacity constraints ---

    # Solar potential: S <= solar_potential_MW
    add_constraint({idx_S: 1.0}, upper=solar_potential_MW)
    
    # Battery max duration: B_MWh <= 6 * B_MW
    add_constraint({idx_B_MWh: 1.0, idx_B_MW: -6.0}, upper=0.0)
    
    # # Don't want to limit the battery size because it might be required with no gas turbines
    # Battery sizing: B_MW <= S  =>  B_MW - S <= 0
    # add_constraint({idx_B_MW: 1.0, idx_S: -1.0}, upper=0.0)
    
    # # Big-M value for BESS mode binary constraints
    # M = max(solar_potential_MW, 1000)

    # --- Hourly constraints ---
    for hr in range(hours):
        # Amount of solar used has to be less than amount of solar produced 
        # Solar usage: solar_used[hr] <= solar_profile[hr] * S
        add_constraint(
            {idx_solar_used[hr]: 1.0, idx_S: -solar_profile[hr]},
            upper=0.0
        )
        
        # Energy balance (equality):
        # (solar_used - charge + discharge) * inv_eff + unserved == P_load_MW
        add_constraint(
            {
                idx_solar_used[hr]: inv_eff,
                idx_charge[hr]:    -inv_eff,
                idx_discharge[hr]:  inv_eff,
                idx_unserved[hr]:   1.0,
            },
            lower=P_load_MW,
            upper=P_load_MW
        )
        
        if hr == 0:
        # initial SOC: soc[0] = charge[0]*eta_c - discharge[0]/eta_d
            add_constraint(
                {
                    idx_soc[0]:        1.0,
                    idx_charge[0]:    -eta_charge,
                    idx_discharge[0]:  1.0 / eta_discharge,
                },
                lower=0.0,
                upper=0.0
            )
        else:
            # Normal SOC dynamics
            add_constraint(
                {
                    idx_soc[hr]:      1.0,
                    idx_soc[hr-1]:   -1.0,
                    idx_charge[hr]:  -eta_charge,
                    idx_discharge[hr]: 1.0 / eta_discharge,
                },
                lower=0.0,
                upper=0.0
            )
        
        # SOC upper limit: soc[hr] <= B_MWh
        add_constraint({idx_soc[hr]: 1.0, idx_B_MWh: -1.0}, upper=0.0)
        
        # BESS mode — charge/discharge mutual exclusion via binary bess_mode[hr]:
        #   bess_mode = 1 => charging allowed, discharging blocked
        #   bess_mode = 0 => discharging allowed, charging blocked

        # # charge[hr] <= M * bess_mode[hr]
        # add_constraint(
        #     {idx_charge[hr]: 1.0, idx_bess_mode[hr]: -M},
        #     upper=0.0
        # )
        
        # charge[hr] <= B_MW
        add_constraint({idx_charge[hr]: 1.0, idx_B_MW: -1.0}, upper=0.0)
        
        # # discharge[hr] <= M * (1 - bess_mode[hr])
        # add_constraint(
        #     {idx_discharge[hr]: 1.0, idx_bess_mode[hr]: M},
        #     upper=M
        # )
        
        # discharge[hr] <= B_MW
        add_constraint({idx_discharge[hr]: 1.0, idx_B_MW: -1.0}, upper=0.0)
    
    # --- Availability constraint ---
    # sum(unserved[hr] for all hr) <= (1 - availability_factor) * P_load_MW * hours
    #
    # Unserved energy represents energy not supplied by renewables/storage,
    # so it counts against the RE availability requirement.
    max_unserved_energy_total = (1 - availability_factor) * P_load_MW * hours
    add_constraint(
        {idx_unserved[hr]: 1.0 for hr in range(hours)},
        upper=max_unserved_energy_total
    )
    
    print(f"Total constraints: {len(constraints)}")
    
    # print(f"max_unserved_energy_total: {max_unserved_energy_total}")
    # print(f"solar_potential_MW: {solar_potential_MW}")
    print(f"P_load_MW: {P_load_MW}")
    print(f"hours: {hours}")
    ######################### Build objective function #########################
    
    annual_load_MWh = total_load_MWh / n_years
    
    # Capital cost coefficients (annualised, normalised to $/MWh of load)
    solar_cost_per_mw = (solar_capex_per_kW * 1000) * CRF + solar_om_per_MWyr
    batt_mw_cost = (battery_capex_per_kW * 1000) * CRF + battery_om_per_MWyr
    batt_mwh_cost = (battery_capex_per_kWh * 1000) * CRF
    
    obj_coeffs[idx_S]     = solar_cost_per_mw / annual_load_MWh
    obj_coeffs[idx_B_MW]  = batt_mw_cost      / annual_load_MWh
    obj_coeffs[idx_B_MWh] = batt_mwh_cost     / annual_load_MWh
    
    # Unserved energy penalty
    for hr in range(hours):
        obj_coeffs[idx_unserved[hr]] += VoLL / annual_load_MWh
    
    ######################### Assemble sparse matrix and solve #########################
    
    num_constraints = len(constraints)
    constraint_lower = []
    constraint_upper = []
    a_start = [0]
    a_index = []
    a_value = []
    
    for sparse_coeffs, lower, upper in constraints:
        constraint_lower.append(lower)
        constraint_upper.append(upper)
        # Only iterate over non-zero entries — no large zero-filled list needed
        for col_idx, coeff in sparse_coeffs.items():
            a_index.append(col_idx)
            a_value.append(coeff)
        a_start.append(len(a_index))

    lp = HighsLp()
    lp.num_col_ = int(var_idx)
    lp.num_row_ = int(num_constraints)
    lp.a_matrix_.format_ = highspy.MatrixFormat.kRowwise
    lp.sense_ = ObjSense.kMinimize
    lp.offset_ = 0.0
    lp.col_cost_ = np.array(obj_coeffs, dtype=np.float64)
    lp.col_lower_ = np.array(var_lower, dtype=np.float64)
    lp.col_upper_ = np.array(var_upper, dtype=np.float64)
    lp.row_lower_ = np.array(constraint_lower, dtype=np.float64)
    lp.row_upper_ = np.array(constraint_upper, dtype=np.float64)
    lp.a_matrix_.start_ = np.array(a_start, dtype=np.int32)
    lp.a_matrix_.index_ = np.array(a_index, dtype=np.int32)
    lp.a_matrix_.value_ = np.array(a_value, dtype=np.float64)
    lp.a_matrix_.num_col_ = int(var_idx)
    lp.a_matrix_.num_row_ = int(num_constraints)
    lp.integrality_ = [HighsVarType.kInteger if v == 1 else HighsVarType.kContinuous for v in var_types]


    model = HighsModel()
    model.lp_ = lp

    print("\nRunning feasibility diagnostic...")
    print(f"Sample solar profile (first 24 hrs): {solar_profile[:24]}")
    print(f"Max solar profile value: {solar_profile.max()}")
    print(f"Mean solar profile value: {solar_profile.mean()}")
    print(f"Hours with solar > 0: {np.sum(solar_profile > 0)}")
    print(f"availability_factor: {availability_factor}")
    # print(f"max_unserved_energy_total: {max_unserved_energy_total}")
    print(f"Total load MWh: {total_load_MWh}")
    print(f"Max possible solar MWh (if S=1MW): {solar_profile.sum()}")
    print(f"For load to be feasible with S=1MW, need unserved >= {total_load_MWh - solar_profile.sum() * inv_eff:.0f} MWh")
    # print(f"But max_unserved_energy_total = {max_unserved_energy_total:.0f} MWh")
    # print(f"Feasible? {total_load_MWh - solar_profile.sum() * inv_eff <= max_unserved_energy_total}")

    total_night_load = np.sum(solar_profile == 0) * P_load_MW
    print(f"Night hours: {np.sum(solar_profile == 0)}")
    print(f"Load during zero-solar hours: {total_night_load} MWh")
    # print(f"Allowed unserved: {max_unserved_energy_total} MWh")

    h.passModel(model)
    print("Solving optimization problem...")
    start_time = time.time()
    
    h.run()
    
    elapsed_time = time.time() - start_time
    print(f"Optimization finished for site {site_id} in {elapsed_time:.2f} seconds")
    
    model_status = h.getModelStatus()
    
    if model_status != HighsModelStatus.kOptimal:
        print(f"⚠️ Optimization not optimal for site {site_id}. Status: {model_status}")
        return None, None, False
    
    ######################### Extract results #########################
    
    solution = h.getSolution()
    
    S_opt    = solution.col_value[idx_S]
    B_MW_opt = solution.col_value[idx_B_MW]
    B_MWh_opt = solution.col_value[idx_B_MWh]
    
    # Extract hourly results
    solar_available = solar_profile * S_opt
    solar_used = np.array([solution.col_value[idx_solar_used[hr]] for hr in range(hours)])
    curtailment = np.maximum(solar_available - solar_used, 0)
    
    charge_vals    = np.array([solution.col_value[idx_charge[hr]]    for hr in range(hours)])
    discharge_vals = np.array([solution.col_value[idx_discharge[hr]] for hr in range(hours)])
    soc_vals       = np.array([solution.col_value[idx_soc[hr]]       for hr in range(hours)])
    unserved_vals  = np.array([solution.col_value[idx_unserved[hr]]  for hr in range(hours)])
    
    # Build hourly results DataFrame
    hourly_df = pd.DataFrame({
        "Year":  solar_profile_df_year["year"].values,
        "Month": solar_profile_df_year["timestamp"].dt.month,
        "Day":   solar_profile_df_year["timestamp"].dt.day,
        "Hour":  solar_profile_df_year["timestamp"].dt.hour,
        "Load_MW": P_load_MW,
        f"Solar_available_MW ({S_opt:.1f} MW)": solar_available,
        "Solar_used_MW":    solar_used,
        "BESS_charge_MW":   charge_vals,
        "BESS_discharge_MW": discharge_vals,
        f"SOC_MWh ({B_MW_opt:.1f} MW/{B_MWh_opt:.1f} MWh)": soc_vals,
        "Curtail_MW":   curtailment,
        "Unserved_MW":  unserved_vals,
    })
    
    if demand_profile is not None:
        hourly_df["System_demand_MW"] = demand_profile[:hours]
        
    # Calculate costs and LCOE
    total_unserved_energy = unserved_vals.sum()
    total_unserved_cost   = total_unserved_energy * VoLL
    annual_unserved_cost  = total_unserved_cost / n_years
    
    annual_solar_cost = annual_solar_cost_func(S_opt)
    annual_batt_cost  = annual_batt_cost_func(B_MW_opt, B_MWh_opt)
    
    total_lcoe = (annual_solar_cost + annual_batt_cost + annual_unserved_cost) / annual_load_MWh
    
    # Reliability metrics
    energy_served = total_load_MWh - total_unserved_energy
    reliability   = (energy_served / total_load_MWh) * 100
    unserved_hours = int(np.sum(unserved_vals > 0.01))
    
    print(f"\n{'='*70}")
    print(f"OPTIMIZATION RESULTS - Site {site_id}")
    print(f"{'='*70}")
    print(f"System Sizing:")
    print(f"  Solar:   {S_opt:.2f} MW")
    print(f"  Battery: {B_MW_opt:.2f} MW / {B_MWh_opt:.2f} MWh")
    print(f"\nReliability:")
    print(f"  Energy served:     {energy_served:,.0f} MWh ({reliability:.2f}%)")
    print(f"  Unserved energy:   {total_unserved_energy:,.0f} MWh ({100-reliability:.2f}%)")
    print(f"  Unserved hours:    {unserved_hours} hours")
    print(f"\nCosts (LCOE breakdown):")
    print(f"  Solar:    ${annual_solar_cost/annual_load_MWh:.2f}/MWh")
    print(f"  Battery:  ${annual_batt_cost/annual_load_MWh:.2f}/MWh")
    print(f"  Unserved: ${annual_unserved_cost/annual_load_MWh:.2f}/MWh")
    print(f"  TOTAL:    ${total_lcoe:.2f}/MWh")
    print(f"{'='*70}\n")
    
    results = {
        "site":                   site_id,
        "load_MW":                load_target,
        "S_opt_MW":               S_opt,
        "Battery_capacity_MW":    B_MW_opt,
        "Battery_energy_MWh":     B_MWh_opt,
        "LCOE_total_$perMWh":     total_lcoe,
        "LCOE_solar_$perMWh":     annual_solar_cost / annual_load_MWh,
        "LCOE_batt_$perMWh":      annual_batt_cost  / annual_load_MWh,
        "LCOE_unserved_$perMWh":  annual_unserved_cost / annual_load_MWh,
        "Reliability_%":          reliability,
        "Unserved_MWh":           total_unserved_energy,
        "Unserved_hours":         unserved_hours,
    }
    
    return results, hourly_df, True


if __name__ == "__main__":
    print("HiGHS-based optimization with unserved energy (peak hours only)")
    print("Install HiGHS with: pip install highspy")