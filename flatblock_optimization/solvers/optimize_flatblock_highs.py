# optimize_flatblock_highs.py
# Solar + Battery + CCGT optimization using HiGHS solver (open-source alternative to Gurobi)

import os 
import pandas as pd
import numpy as np
import highspy
from highspy import Highs, HighsStatus, HighsModelStatus
import time

def run_flatblock_optimization_highs(
    site_id,
    availability_factor,
    site_params,
    solar_profile_df,
    demand_profile, # what the actual demand by hour is
    gas_allowed_profile,
    input_costs,
    load_target # load we are aiming for. Solar (after inverter losses) + Battery discharge + Gas generation = Load Target
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
    inv_eff = 0.967 # inverter efficiency

    # Extract gas and site info
    ct_cap = site_params["ct_capacity_mw"]
    ct_vc  = site_params["ct_vc"] # $/MWh
    ccgt_cap = site_params["ccgt_capacity_mw"] 
    ccgt_vc  = site_params["ccgt_vc"] # $/MWh
    total_gas_cap = ct_cap + ccgt_cap 

    solar_potential_MW = site_params["potential_mw_solar"]

    # Battery chargin constraint 
    M = max(1000, solar_potential_MW)
     
    # Gas ramping constraints
    ccgt_ramp_rate = 0.6 * ccgt_cap  # 60% per hour ramp limit
    ccgt_min_run = 0.4 * ccgt_cap    # 40% of capacity when on
    ccgt_startup_cost = 40           # $/MW-startup
    ccgt_shutdown_cost = 10          # $/MW-shutdown

    ct_ramp_rate = 1.0 * ct_cap     # 100% per hour (effectively unconstrained)
    ct_min_run = 0.2 * ct_cap       # 20% of capacity when on
    ct_startup_cost = 10            # $/MW-startup
    ct_shutdown_cost = 5            # $/MW-shutdown
    
    # Calculate load - LIMIT TO ONE YEAR
    solar_profile_df_year = solar_profile_df[solar_profile_df["year"] == solar_profile_df["year"].iloc[0]].copy()
    solar_profile = solar_profile_df_year["solar_profile"].to_numpy(dtype=float)
    hours = len(solar_profile)
    P_load_MW = load_target
    total_load_MWh = P_load_MW * hours
    n_years = 1  # Single year analysis
    
    # Adjust gas_allowed_profile if needed
    if gas_allowed_profile is not None:
        gas_allowed_profile = gas_allowed_profile[:hours]

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
    # h.setOptionValue("threads", 22)  # HiGHS uses threads automatically
    
    print(f"Building optimization model for site {site_id}...")
    
    ######################### Define variables #########################
    
    # Variable indices tracking
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
        var_types.append(1 if is_integer else 0)  # 1=integer, 0=continuous
        obj_coeffs.append(obj_coeff)
        var_idx += 1
        return var_names[name]
    
    # Capacity decision variables
    idx_S = add_var("S_MW")
    idx_B_MW = add_var("BESS_MW")
    idx_B_MWh = add_var("BESS_MWh")
    
    # Hourly operational variables
    idx_solar_used = [add_var(f"solar_used_{h}") for h in range(hours)]
    idx_charge = [add_var(f"charge_{h}") for h in range(hours)]
    idx_discharge = [add_var(f"discharge_{h}") for h in range(hours)]
    idx_soc = [add_var(f"soc_{h}") for h in range(hours)]
    idx_bess_mode = [add_var(f"bess_mode_{h}", is_integer=True, ub=1.0) for h in range(hours)]
    
    # CT variables
    if ct_cap > 0:
        idx_ct = [add_var(f"ct_{h}") for h in range(hours)]
        idx_ct_on = [add_var(f"ct_on_{h}", is_integer=True, ub=1.0) for h in range(hours)]
        idx_ct_startup = [add_var(f"ct_startup_{h}") for h in range(hours)]
        idx_ct_shutdown = [add_var(f"ct_shutdown_{h}") for h in range(hours)]
    
    # CCGT variables
    if ccgt_cap > 0:
        idx_ccgt = [add_var(f"ccgt_{h}") for h in range(hours)]
        idx_ccgt_on = [add_var(f"ccgt_on_{h}", is_integer=True, ub=1.0) for h in range(hours)]
        idx_ccgt_startup = [add_var(f"ccgt_startup_{h}") for h in range(hours)]
        idx_ccgt_shutdown = [add_var(f"ccgt_shutdown_{h}") for h in range(hours)]
    
    print(f"Total variables: {var_idx}")
    
    ######################### Define constraints #########################
    
    constraints = []
    
    def add_constraint(coeffs, lower=-float('inf'), upper=float('inf')):
        """Add a constraint: lower <= sum(coeffs[i] * x[i]) <= upper"""
        constraints.append((coeffs, lower, upper))
    
    # Solar potential constraint: S <= solar_potential_MW
    coeffs = [0.0] * var_idx
    coeffs[idx_S] = 1.0
    add_constraint(coeffs, upper=solar_potential_MW)
    
    # Battery duration constraint: B_MWh <= B_MW * 6
    coeffs = [0.0] * var_idx
    coeffs[idx_B_MWh] = 1.0
    coeffs[idx_B_MW] = -6.0
    add_constraint(coeffs, upper=0.0)
    
    # Battery sizing constraint: B_MW <= S
    coeffs = [0.0] * var_idx
    coeffs[idx_B_MW] = 1.0
    coeffs[idx_S] = -1.0
    add_constraint(coeffs, upper=0.0)
    
    # Hourly constraints
    for hr in range(hours):
        # Solar usage constraint: solar_used[h] <= S * solar_profile[h]
        coeffs = [0.0] * var_idx
        coeffs[idx_solar_used[hr]] = 1.0
        coeffs[idx_S] = -solar_profile[hr]
        add_constraint(coeffs, upper=0.0)
        
        # Energy balance: (solar_used - charge + discharge) * inv_eff + ct + ccgt == P_load_MW
        coeffs = [0.0] * var_idx
        coeffs[idx_solar_used[hr]] = inv_eff
        coeffs[idx_charge[hr]] = -inv_eff
        coeffs[idx_discharge[hr]] = inv_eff
        if ct_cap > 0:
            coeffs[idx_ct[hr]] = 1.0
        if ccgt_cap > 0:
            coeffs[idx_ccgt[hr]] = 1.0
        add_constraint(coeffs, lower=P_load_MW, upper=P_load_MW)
        
        # SOC dynamics
        prev_hr = hours - 1 if hr == 0 else hr - 1
        coeffs = [0.0] * var_idx
        coeffs[idx_soc[hr]] = 1.0
        coeffs[idx_soc[prev_hr]] = -1.0
        coeffs[idx_charge[hr]] = -eta_charge
        coeffs[idx_discharge[hr]] = 1.0 / eta_discharge # inversing battery rte when discharging
        add_constraint(coeffs, lower=0.0, upper=0.0)
        
        # SOC upper limit: soc[h] <= B_MWh
        coeffs = [0.0] * var_idx
        coeffs[idx_soc[hr]] = 1.0
        coeffs[idx_B_MWh] = -1.0
        add_constraint(coeffs, upper=0.0)
        
        # === CHARGING CONSTRAINTS ===
        # charge[h] ≤ M × bess_mode[h]
        # Forces charge = 0 when bess_mode = 0
        coeffs = [0.0] * var_idx
        coeffs[idx_charge[hr]] = 1.0
        coeffs[idx_bess_mode[hr]] = -M
        add_constraint(coeffs, upper=0.0)
        
        # charge[h] ≤ B_MW
        # Limits charging to battery power capacity
        coeffs = [0.0] * var_idx
        coeffs[idx_charge[hr]] = 1.0
        coeffs[idx_B_MW] = -1.0
        add_constraint(coeffs, upper=0.0)
        
        # === DISCHARGING CONSTRAINTS ===
        # discharge[h] ≤ M × (1 - bess_mode[h])
        # Forces discharge = 0 when bess_mode = 1
        # Rearranged: discharge[h] + M × bess_mode[h] ≤ M
        coeffs = [0.0] * var_idx
        coeffs[idx_discharge[hr]] = 1.0
        coeffs[idx_bess_mode[hr]] = M
        add_constraint(coeffs, upper=M)
        
        # discharge[h] ≤ B_MW
        # Limits discharging to battery power capacity
        coeffs = [0.0] * var_idx
        coeffs[idx_discharge[hr]] = 1.0
        coeffs[idx_B_MW] = -1.0
        add_constraint(coeffs, upper=0.0)
        
    # CT constraints
    if ct_cap > 0:
        for hr in range(hours):
            prev_hr = hours - 1 if hr == 0 else hr - 1
            
            # Ramp up: ct[h] - ct[h-1] <= ct_ramp_rate
            coeffs = [0.0] * var_idx
            coeffs[idx_ct[hr]] = 1.0
            coeffs[idx_ct[prev_hr]] = -1.0
            add_constraint(coeffs, upper=ct_ramp_rate)
            
            # Ramp down: ct[h-1] - ct[h] <= ct_ramp_rate
            coeffs = [0.0] * var_idx
            coeffs[idx_ct[prev_hr]] = 1.0
            coeffs[idx_ct[hr]] = -1.0
            add_constraint(coeffs, upper=ct_ramp_rate)
            
            # Minimum run: ct[h] >= ct_min_run * ct_on[h]
            coeffs = [0.0] * var_idx
            coeffs[idx_ct[hr]] = 1.0
            coeffs[idx_ct_on[hr]] = -ct_min_run
            add_constraint(coeffs, lower=0.0)
            
            # Maximum output
            if gas_allowed_profile is not None:
                max_ct = ct_cap * gas_allowed_profile[hr]
            else:
                max_ct = ct_cap
            
            coeffs = [0.0] * var_idx
            coeffs[idx_ct[hr]] = 1.0
            coeffs[idx_ct_on[hr]] = -max_ct
            add_constraint(coeffs, upper=0.0)
            
            # Startup tracking: ct_startup[h] >= ct[h] - ct[h-1]
            coeffs = [0.0] * var_idx
            coeffs[idx_ct_startup[hr]] = 1.0
            coeffs[idx_ct[hr]] = -1.0
            coeffs[idx_ct[prev_hr]] = 1.0
            add_constraint(coeffs, lower=0.0)
            
            # Shutdown tracking: ct_shutdown[h] >= ct[h-1] - ct[h]
            coeffs = [0.0] * var_idx
            coeffs[idx_ct_shutdown[hr]] = 1.0
            coeffs[idx_ct[prev_hr]] = -1.0
            coeffs[idx_ct[hr]] = 1.0
            add_constraint(coeffs, lower=0.0)
    
    # CCGT constraints
    if ccgt_cap > 0:
        for hr in range(hours):
            prev_hr = hours - 1 if hr == 0 else hr - 1
            
            # Ramp up
            coeffs = [0.0] * var_idx
            coeffs[idx_ccgt[hr]] = 1.0
            coeffs[idx_ccgt[prev_hr]] = -1.0
            add_constraint(coeffs, upper=ccgt_ramp_rate)
            
            # Ramp down
            coeffs = [0.0] * var_idx
            coeffs[idx_ccgt[prev_hr]] = 1.0
            coeffs[idx_ccgt[hr]] = -1.0
            add_constraint(coeffs, upper=ccgt_ramp_rate)
            
            # Minimum run
            coeffs = [0.0] * var_idx
            coeffs[idx_ccgt[hr]] = 1.0
            coeffs[idx_ccgt_on[hr]] = -ccgt_min_run
            add_constraint(coeffs, lower=0.0)
            
            # Maximum output
            if gas_allowed_profile is not None:
                max_ccgt = ccgt_cap * gas_allowed_profile[hr]
            else:
                max_ccgt = ccgt_cap
            
            coeffs = [0.0] * var_idx
            coeffs[idx_ccgt[hr]] = 1.0
            coeffs[idx_ccgt_on[hr]] = -max_ccgt
            add_constraint(coeffs, upper=0.0)
            
            # Startup tracking
            coeffs = [0.0] * var_idx
            coeffs[idx_ccgt_startup[hr]] = 1.0
            coeffs[idx_ccgt[hr]] = -1.0
            coeffs[idx_ccgt[prev_hr]] = 1.0
            add_constraint(coeffs, lower=0.0)
            
            # Shutdown tracking
            coeffs = [0.0] * var_idx
            coeffs[idx_ccgt_shutdown[hr]] = 1.0
            coeffs[idx_ccgt[prev_hr]] = -1.0
            coeffs[idx_ccgt[hr]] = 1.0
            add_constraint(coeffs, lower=0.0)
    
    # Availability constraint: total gas generation <= (1 - availability_factor) * load
    max_gas_energy_total = (1 - availability_factor) * P_load_MW * hours
    coeffs = [0.0] * var_idx
    if ct_cap > 0:
        for hr in range(hours):
            coeffs[idx_ct[hr]] = 1.0
    if ccgt_cap > 0:
        for hr in range(hours):
            coeffs[idx_ccgt[hr]] = 1.0
    add_constraint(coeffs, upper=max_gas_energy_total)
    
    print(f"Total constraints: {len(constraints)}")
    
    ######################### Build objective function #########################
    
    # We need to linearize the objective since it contains products of variables
    # Add auxiliary variables for S, B_MW, B_MWh costs (approximated or linearized)
    
    # For simplicity, we'll use a piecewise linear approximation
    # Or we can reformulate using additional variables and constraints
    
    # Simplified approach: assume we're minimizing a linear combination
    # This requires reformulation for nonlinear costs
    
    # For now, let's create a simplified linear objective
    
    annual_load_MWh = total_load_MWh / n_years
    
    # Linear approximation coefficients (you may need to adjust these)
    solar_cost_per_mw = (solar_capex_per_kW * 1000) * CRF + solar_om_per_MWyr
    batt_mw_cost = (battery_capex_per_kW * 1000) * CRF + battery_om_per_MWyr
    batt_mwh_cost = (battery_capex_per_kWh * 1000) * CRF
    
    # Update objective coefficients
    obj_coeffs[idx_S] = solar_cost_per_mw / annual_load_MWh
    obj_coeffs[idx_B_MW] = batt_mw_cost / annual_load_MWh
    obj_coeffs[idx_B_MWh] = batt_mwh_cost / annual_load_MWh
    
    # Add fuel costs
    if ct_cap > 0:
        for hr in range(hours):
            obj_coeffs[idx_ct[hr]] += ct_vc / annual_load_MWh
            obj_coeffs[idx_ct_startup[hr]] += ct_startup_cost / annual_load_MWh
            obj_coeffs[idx_ct_shutdown[hr]] += ct_shutdown_cost / annual_load_MWh
    
    if ccgt_cap > 0:
        for hr in range(hours):
            obj_coeffs[idx_ccgt[hr]] += ccgt_vc / annual_load_MWh
            obj_coeffs[idx_ccgt_startup[hr]] += ccgt_startup_cost / annual_load_MWh
            obj_coeffs[idx_ccgt_shutdown[hr]] += ccgt_shutdown_cost / annual_load_MWh
    
    ######################### Setup and solve #########################
    
    # Convert constraints to matrix form
    num_constraints = len(constraints)
    constraint_lower = []
    constraint_upper = []
    a_start = [0]
    a_index = []
    a_value = []
    
    for coeffs_list, lower, upper in constraints:
        constraint_lower.append(lower)
        constraint_upper.append(upper)
        
        for idx, coeff in enumerate(coeffs_list):
            if abs(coeff) > 1e-10:  # Only add non-zero coefficients
                a_index.append(idx)
                a_value.append(coeff)
        a_start.append(len(a_index))
    
    # Pass the problem to HiGHS
    h.passModel(
        num_col=var_idx,
        num_row=num_constraints,
        sense=1,  # 1 = minimize
        offset=0,
        col_cost=obj_coeffs,
        col_lower=var_lower,
        col_upper=var_upper,
        row_lower=constraint_lower,
        row_upper=constraint_upper,
        a_start=a_start,
        a_index=a_index,
        a_value=a_value,
        integrality=var_types
    )
    
    print("Solving optimization problem...")
    start_time = time.time()
    
    status = h.run()
    
    elapsed_time = time.time() - start_time
    print(f"Optimization finished for site {site_id} in {elapsed_time:.2f} seconds")
    
    model_status = h.getModelStatus()
    
    if model_status != HighsModelStatus.kOptimal:
        print(f"⚠️ Optimization not optimal for site {site_id}. Status: {model_status}")
        return None, None, False
    
    ######################### Extract results #########################
    
    solution = h.getSolution()
    
    S_opt = solution.col_value[idx_S]
    B_MW_opt = solution.col_value[idx_B_MW]
    B_MWh_opt = solution.col_value[idx_B_MWh]
    
    # Extract hourly results
    solar_available = solar_profile * S_opt
    solar_used = np.array([solution.col_value[idx_solar_used[hr]] for hr in range(hours)])
    curtailment = solar_available - solar_used
    curtailment = np.maximum(curtailment, 0)
    
    charge_vals = np.array([solution.col_value[idx_charge[hr]] for hr in range(hours)])
    discharge_vals = np.array([solution.col_value[idx_discharge[hr]] for hr in range(hours)])
    soc_vals = np.array([solution.col_value[idx_soc[hr]] for hr in range(hours)])
    
    ct_vals = np.array([solution.col_value[idx_ct[hr]] for hr in range(hours)]) if ct_cap > 0 else np.zeros(hours)
    ccgt_vals = np.array([solution.col_value[idx_ccgt[hr]] for hr in range(hours)]) if ccgt_cap > 0 else np.zeros(hours)
    
    # Build hourly results DataFrame
    hourly_df = pd.DataFrame({
        "Year": solar_profile_df_year["year"].values,
        "Month": solar_profile_df_year["timestamp"].dt.month,
        "Day": solar_profile_df_year["timestamp"].dt.day,
        "Hour": solar_profile_df_year["timestamp"].dt.hour,
        "Load_MW": P_load_MW,
        f"Solar_available_MW ({S_opt:.1f} MW)": solar_available,
        "Solar_used_MW": solar_used,
        "BESS_charge_MW": charge_vals,
        "BESS_discharge_MW": discharge_vals,
        f"SOC_MWh ({B_MW_opt:.1f} MW/{B_MWh_opt:.1f} MWh)": soc_vals,
        "Curtail_MW": curtailment,
        "CT_MWh": ct_vals,
        "CCGT_MWh": ccgt_vals,
    })
    
    if demand_profile is not None:
        hourly_df["System_demand_MW"] = demand_profile[:hours]
        if gas_allowed_profile is not None:
            hourly_df["Gas_allowed"] = gas_allowed_profile
    
    # Calculate costs and LCOE
    total_gas_fuel = (ct_vals.sum() * ct_vc + ccgt_vals.sum() * ccgt_vc)
    annual_gas_fuel = total_gas_fuel / n_years
    
    annual_solar_cost = annual_solar_cost_func(S_opt)
    annual_batt_cost = annual_batt_cost_func(B_MW_opt, B_MWh_opt)
    
    total_lcoe = (annual_solar_cost + annual_batt_cost + annual_gas_fuel) / annual_load_MWh
    
    print(f"Optimized results for site {site_id}:")
    print(f"  Solar MW: {S_opt:.2f} MW")
    print(f"  Battery MW: {B_MW_opt:.2f} MW")
    print(f"  Battery MWh: {B_MWh_opt:.2f} MWh")
    print(f"  Load is {load_target:.2f} MW out of installed gas capacity of {total_gas_cap:.2f} MW")
    print(f"  LCOE: ${total_lcoe:.2f}/MWh")
    
    results = {
        "site": site_id,
        "load_MW": load_target,
        "total_gas_capacity": total_gas_cap,
        "S_opt_MW": S_opt,
        "Battery_capacity_MW": B_MW_opt,
        "Battery_energy_MWh": B_MWh_opt,
        "LCOE_total_$perMWh": total_lcoe,
        "LCOE_solar_$perMWh": annual_solar_cost / annual_load_MWh,
        "LCOE_batt_$perMWh": annual_batt_cost / annual_load_MWh,
        "LCOE_gas_$perMWh": annual_gas_fuel / annual_load_MWh
    }
    
    return results, hourly_df, True


if __name__ == "__main__":
    # Example usage
    print("HiGHS-based optimization script loaded successfully")
    print("Install HiGHS with: pip install highspy")
