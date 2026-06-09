# optimize_flatblock_highs_unserved.py
# Solar + Battery optimization using HiGHS solver
# MODIFICATION: Allows unserved energy during peak hours only (when gas is blocked)

import os 
import pandas as pd
import numpy as np
import highspy
from highspy import Highs, HighsModelStatus

import time

def run_flatblock_optimization_highs(
    site_id,
    availability_factor,
    site_params,
    solar_profile_df,
    demand_profile,
    input_costs,
    load_target,
    VoLL=0
    ):
    
    ######################### Load inputs and initial calcs #########################
    
    fin_params   = input_costs["financial"]
    solar_params = input_costs["solar"]
    batt_params  = input_costs["battery"]

    CRF                  = fin_params["CRF"]
    solar_capex_per_kW   = solar_params["Capex"]
    solar_om_per_MWyr    = solar_params["Fixed O&M"]
    battery_capex_per_kWh = (batt_params["Pack"] + batt_params["Rack"] + batt_params["BOS + EMS"] + batt_params["EPC"])
    battery_capex_per_kW  = batt_params["PCS + Overhead"]
    battery_om_per_MWyr  = batt_params["Fixed O&M"]
    battery_rte          = 0.95
    inv_eff              = 0.967
    eta_charge = eta_discharge = np.sqrt(battery_rte)

    solar_potential_MW   = site_params["potential_mw_solar_15"]

    solar_profile_df_year = solar_profile_df[solar_profile_df["year"] == solar_profile_df["year"].iloc[0]].copy()
    solar_profile = solar_profile_df_year["P_kWperkWp"].to_numpy(dtype=float)
    hours         = len(solar_profile)
    P_load_MW     = load_target
    total_load_MWh  = P_load_MW * hours
    annual_load_MWh = total_load_MWh  # n_years = 1

    print(f"\n{'='*70}")
    print(f"Building optimization model for site {site_id}")
    print(f"Load target: {load_target} MW | Availability: {availability_factor*100}% | VoLL: ${VoLL:,.0f}/MWh")
    print(f"{'='*70}\n")

    ######################### Cost coefficients #########################

    solar_cost_per_mw = (solar_capex_per_kW * 1000) * CRF + solar_om_per_MWyr
    batt_mw_cost      = (battery_capex_per_kW * 1000) * CRF + battery_om_per_MWyr
    batt_mwh_cost     = (battery_capex_per_kWh * 1000) * CRF

    annual_solar_cost_func = lambda S: (solar_capex_per_kW*1000*S)*CRF + solar_om_per_MWyr*S
    annual_batt_cost_func  = lambda B_MW, B_MWh: (battery_capex_per_kW*1000*B_MW + battery_capex_per_kWh*1000*B_MWh)*CRF + battery_om_per_MWyr*B_MW

    ######################### Build model #########################

    h = Highs()
    h.setOptionValue("log_to_console", True)
    h.setOptionValue("mip_rel_gap", 0.01)
    h.silent()  # suppress verbose output during model building

    # --- Capacity variables (objective set via changeColCost after adding) ---
    # highspy addVar(lower, upper) only; no obj_coeff argument
    h.addVar(0, float('inf'))   # 0: S_MW
    h.addVar(0, float('inf'))   # 1: B_MW
    h.addVar(0, float('inf'))   # 2: B_MWh

    # --- Hourly operational variables ---
    for _ in range(hours):
        h.addVar(0, float('inf'))   # solar_used
    for _ in range(hours):
        h.addVar(0, float('inf'))   # charge
    for _ in range(hours):
        h.addVar(0, float('inf'))   # discharge
    for _ in range(hours):
        h.addVar(0, float('inf'))   # soc
    for _ in range(hours):
        h.addVar(0, float('inf'))   # unserved

    # Column indices (order matches addVar calls above)
    ix_S_MW = 0
    ix_B_MW = 1
    ix_B_MWh = 2
    ix_solar_used_start = 3
    ix_charge_start = 3 + hours
    ix_discharge_start = 3 + 2 * hours
    ix_soc_start = 3 + 3 * hours
    ix_unserved_start = 3 + 4 * hours

    # Set objective coefficients (minimize LCOE = cost / annual_load_MWh)
    h.changeColCost(ix_S_MW, solar_cost_per_mw / annual_load_MWh)
    h.changeColCost(ix_B_MW, batt_mw_cost / annual_load_MWh)
    h.changeColCost(ix_B_MWh, batt_mwh_cost / annual_load_MWh)
    for i in range(hours):
        h.changeColCost(ix_unserved_start + i, VoLL / annual_load_MWh)

    # Keep index lists for constraints and solution
    S_MW = ix_S_MW
    B_MW = ix_B_MW
    B_MWh = ix_B_MWh
    solar_used = list(range(ix_solar_used_start, ix_solar_used_start + hours))
    charge = list(range(ix_charge_start, ix_charge_start + hours))
    discharge = list(range(ix_discharge_start, ix_discharge_start + hours))
    soc = list(range(ix_soc_start, ix_soc_start + hours))
    unserved = list(range(ix_unserved_start, ix_unserved_start + hours))

    print(f"Variables built. Adding constraints...")

    INF = float('inf')
    max_unserved = (1 - availability_factor) * P_load_MW * hours

    # --- Capacity constraints ---
    # B_MWh - 6*B_MW <= 0
    h.addRow(-INF, 0, 2, np.array([ix_B_MWh, ix_B_MW], dtype=np.int32), np.array([1.0, -6.0], dtype=np.float64))

    # --- Hourly constraints ---
    for t in range(hours):
        # Solar usage: solar_used[t] <= solar_profile[t] * S_MW  =>  solar_used[t] - solar_profile[t]*S_MW <= 0
        h.addRow(-INF, 0, 2,
                 np.array([solar_used[t], ix_S_MW], dtype=np.int32),
                 np.array([1.0, -solar_profile[t]], dtype=np.float64))

        # Energy balance: inv_eff*solar_used - inv_eff*charge + inv_eff*discharge + unserved == P_load_MW
        h.addRow(P_load_MW, P_load_MW, 4,
                 np.array([solar_used[t], charge[t], discharge[t], unserved[t]], dtype=np.int32),
                 np.array([inv_eff, -inv_eff, inv_eff, 1.0], dtype=np.float64))

        # SOC: soc[t] == eta_charge*charge[t] - discharge[t]/eta_discharge  (t=0) or soc[t] == soc[t-1] + ...
        if t == 0:
            h.addRow(0, 0, 3,
                     np.array([soc[t], charge[t], discharge[t]], dtype=np.int32),
                     np.array([1.0, -eta_charge, 1.0/eta_discharge], dtype=np.float64))
        else:
            h.addRow(0, 0, 4,
                     np.array([soc[t], soc[t-1], charge[t], discharge[t]], dtype=np.int32),
                     np.array([1.0, -1.0, -eta_charge, 1.0/eta_discharge], dtype=np.float64))

        # soc[t] <= B_MWh  =>  soc[t] - B_MWh <= 0
        h.addRow(-INF, 0, 2,
                 np.array([soc[t], ix_B_MWh], dtype=np.int32),
                 np.array([1.0, -1.0], dtype=np.float64))

        # charge[t] <= B_MW
        h.addRow(-INF, 0, 2,
                 np.array([charge[t], ix_B_MW], dtype=np.int32),
                 np.array([1.0, -1.0], dtype=np.float64))

        # discharge[t] <= B_MW
        h.addRow(-INF, 0, 2,
                 np.array([discharge[t], ix_B_MW], dtype=np.int32),
                 np.array([1.0, -1.0], dtype=np.float64))

    # --- Availability: sum(unserved) <= max_unserved
    unserved_cols = np.array(unserved, dtype=np.int32)
    unserved_coeffs = np.ones(hours, dtype=np.float64)
    h.addRow(-INF, max_unserved, hours, unserved_cols, unserved_coeffs)

    print(f"Constraints built. Solving...")

    ######################### Solve #########################

    h.setOptionValue("log_to_console", True)
    start_time = time.time()
    h.minimize()
    elapsed = time.time() - start_time
    print(f"Solved in {elapsed:.2f}s")

    if h.getModelStatus() != HighsModelStatus.kOptimal:
        print(f"⚠️ Not optimal for site {site_id}. Status: {h.getModelStatus()}")
        return None, None, False

    ######################### Extract results #########################

    S_opt    = h.variableValue(ix_S_MW)
    B_MW_opt = h.variableValue(ix_B_MW)
    B_MWh_opt = h.variableValue(ix_B_MWh)

    solar_available = solar_profile * S_opt
    solar_used_vals  = np.array([h.variableValue(solar_used[t])  for t in range(hours)])
    charge_vals      = np.array([h.variableValue(charge[t])      for t in range(hours)])
    discharge_vals   = np.array([h.variableValue(discharge[t])   for t in range(hours)])
    soc_vals         = np.array([h.variableValue(soc[t])         for t in range(hours)])
    unserved_vals    = np.array([h.variableValue(unserved[t])    for t in range(hours)])
    curtailment      = np.maximum(solar_available - solar_used_vals, 0)

    hourly_df = pd.DataFrame({
        "Year":  solar_profile_df_year["year"].values,
        "Month": solar_profile_df_year["timestamp"].dt.month,
        "Day":   solar_profile_df_year["timestamp"].dt.day,
        "Hour":  solar_profile_df_year["timestamp"].dt.hour,
        "Load_MW":                                          P_load_MW,
        f"Solar_available_MW ({S_opt:.1f} MW)":            solar_available,
        "Solar_used_MW":                                    solar_used_vals,
        "BESS_charge_MW":                                   charge_vals,
        "BESS_discharge_MW":                                discharge_vals,
        f"SOC_MWh ({B_MW_opt:.1f} MW/{B_MWh_opt:.1f} MWh)": soc_vals,
        "Curtail_MW":                                       curtailment,
        "Unserved_MW":                                      unserved_vals,
    })

    if demand_profile is not None:
        hourly_df["System_demand_MW"] = demand_profile[:hours]

    total_unserved_energy = unserved_vals.sum()
    annual_solar_cost     = annual_solar_cost_func(S_opt)
    annual_batt_cost      = annual_batt_cost_func(B_MW_opt, B_MWh_opt)
    annual_unserved_cost  = total_unserved_energy * VoLL
    total_lcoe            = (annual_solar_cost + annual_batt_cost + annual_unserved_cost) / annual_load_MWh

    energy_served  = total_load_MWh - total_unserved_energy
    reliability    = (energy_served / total_load_MWh) * 100
    unserved_hours = int(np.sum(unserved_vals > 0.01))

    print(f"\n{'='*70}")
    print(f"RESULTS - Site {site_id}")
    print(f"  Solar: {S_opt:.2f} MW | Battery: {B_MW_opt:.2f} MW / {B_MWh_opt:.2f} MWh")
    print(f"  Reliability: {reliability:.2f}% | Unserved: {total_unserved_energy:,.0f} MWh ({unserved_hours} hrs)")
    print(f"  LCOE — Solar: ${annual_solar_cost/annual_load_MWh:.2f} | Batt: ${annual_batt_cost/annual_load_MWh:.2f} | Total: ${total_lcoe:.2f}/MWh")
    print(f"{'='*70}\n")

    results = {
        "site":                  site_id,
        "load_MW":               load_target,
        "S_opt_MW":              S_opt,
        "Battery_capacity_MW":   B_MW_opt,
        "Battery_energy_MWh":    B_MWh_opt,
        "LCOE_total_$perMWh":    total_lcoe,
        "LCOE_solar_$perMWh":    annual_solar_cost  / annual_load_MWh,
        "LCOE_batt_$perMWh":     annual_batt_cost   / annual_load_MWh,
        "LCOE_unserved_$perMWh": annual_unserved_cost / annual_load_MWh,
        "Reliability_%":         reliability,
        "Unserved_MWh":          total_unserved_energy,
        "Unserved_hours":        unserved_hours,
    }

    return results, hourly_df, True
if __name__ == "__main__":
    print("HiGHS-based optimization with unserved energy (peak hours only)")
    print("Install HiGHS with: pip install highspy")