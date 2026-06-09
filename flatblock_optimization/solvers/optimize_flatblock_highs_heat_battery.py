# optimize_flatblock_highs_heat_battery.py
# Solar + heat-battery (TES) optimization using HiGHS solver
#
# Same LP structure as optimize_flatblock_highs_unserved_v2 (AC bus, inverter efficiency,
# unserved cap), but thermal storage economics/efficiency come from ``input_costs['battery']``
# using year-specific rows loaded by ``load_re_costs``.
#
# Example (from flatblock_optimization/):
#   python run_scenario.py --storage-type heat \\
#     --site-id 110 --scenario-dir scenarios/my_run/scenario_110 \\
#     --iso3-country USA --availability 0.90 \\
#     --solar-start 2023 --solar-end 2023 --project-start 2025 --peak-demand-cutoff 0

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import highspy
from highspy import Highs, HighsModelStatus


def _crf(discount_rate: float, lifetime_years: int) -> float:
    """Capital recovery factor."""
    r = float(discount_rate)
    n = int(lifetime_years)
    if n <= 0:
        raise ValueError("lifetime_years must be > 0")
    if r <= 0:
        return 1.0 / n
    return r / (1.0 - (1.0 + r) ** (-n))


def _thermal_efficiency_by_temp(
    process_temperature_c: float | None, eff_le_200: float, eff_gt_200: float
) -> float:
    """Electricity->heat conversion efficiency by process temperature bucket."""
    if process_temperature_c is None:
        return float(eff_gt_200)
    t = float(process_temperature_c)
    return float(eff_le_200) if t <= 200.0 else float(eff_gt_200)


def _tes_usable_energy_fraction_linear(t_min_c: float, t_max_c: float) -> tuple[float, float]:
    """
    Linear enthalpy proxy on absolute temperature (0 °C reference): assume stored energy
    scales with tank temperature up to T_max. Then the fraction of **nameplate energy**
    E_TES that is **thermodynamically above** the process floor T_min is approximately::

        usable_frac ≈ 1 - T_min / T_max .

    That is the share of the tank's energy capacity that can participate in **usable**
    SOC (deliverable toward the process requirement), not a power limit. The complement
    f_min ≈ T_min / T_max acts like a fixed "cold" fraction of the enthalpy range in this
    linear model.

    Returns (f_min, usable_frac). H4 uses usable_frac so soc[t] <= usable_frac * E_TES.
    """
    t_max = float(max(t_max_c, 1e-6))
    t_min = float(max(0.0, t_min_c))
    if t_min >= t_max:
        raise ValueError(f"T_min ({t_min}°C) must be < T_max ({t_max}°C)")
    f_min = min(t_min / t_max, 0.9995)
    usable = 1.0 - f_min
    return f_min, usable


def run_flatblock_optimization_heat_battery_highs(
    site_id: int,
    availability_factor: float,
    site_params: Dict[str, Any],
    solar_profile_df: pd.DataFrame,
    demand_profile: Optional[np.ndarray],
    input_costs: Dict[str, Any],
    load_target: float,
    VoLL: float = 0.0,
    *,
    round_trip_efficiency: float = 0.95,
    project_start_year: int = 2025,  # retained in outputs for traceability
    process_temperature_c: float | None = None,
    max_storage_hours: float = 12.0,
    inverter_efficiency: float = 0.967,
    tes_temp_max_c: float | None = None,
    tes_temp_min_c: float | None = None,
    charge_discharge_ratio_min: float | None = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[pd.DataFrame], bool]:
    """
    Minimize LCOE for solar + **heat storage (TES)** + optional unserved energy.

    Parameters
    ----------
    round_trip_efficiency
        Electrical round-trip equivalent (charge and discharge symmetric via sqrt(RTE)).
    project_start_year
        Stored for traceability; thermal parameters are read from ``input_costs['battery']``.
    process_temperature_c
        Used for electricity->heat efficiency bucket and, if ``tes_temp_min_c`` is omitted,
        as default ``T_min`` for the usable stored-energy window.
    max_storage_hours
        ``E_storage <= hours * P_discharge`` (duration on firm output / discharge side).
    tes_temp_max_c, tes_temp_min_c
        Tank ceiling and process floor (°C). Defaults from ``input_costs['battery']``
        keys ``Thermal T_max C`` / ``Thermal T_min C``; ``T_min`` falls back to
        ``process_temperature_c`` when given.
    charge_discharge_ratio_min
        Minimum ``P_charge / P_discharge`` (e.g. 4.0). Default from battery CSV
        ``Thermal Charge/Discharge ratio min``.
    """
    ######################### Load inputs and initial calcs #########################

    fin_params = input_costs["financial"]
    solar_params = input_costs["solar"]

    CRF = fin_params["CRF"]
    solar_capex_per_kW = solar_params["Capex"]
    solar_om_per_MWyr = solar_params["Fixed O&M"]

    batt_params = input_costs.get("battery", {})
    thermal_cost_model = "battery_params"

    # Simple thermal inputs from battery params (year-specific from battery CSV).
    capex_kwh = float(batt_params.get("Thermal CAPEX $/kWh", 100.0))
    opex_frac = float(batt_params.get("Thermal OPEX frac/yr", 0.02))
    lifetime_years = int(round(float(batt_params.get("Thermal Lifetime yr", 25.0))))
    discount_rate = float(batt_params.get("Thermal Discount rate", 0.07))
    eff_le_200 = float(batt_params.get("Thermal Eff <=200C", 0.98))
    eff_gt_200 = float(batt_params.get("Thermal Eff >200C", 0.95))

    # Energy-only thermal model: annualized capex + opex fraction.
    e_capex = capex_kwh * 1000.0  # $/MWh
    p_capex = 0.0
    tes_om_per_MWyr = 0.0
    tes_crf = _crf(discount_rate, lifetime_years)
    tes_energy_annual_per_mwh = e_capex * (tes_crf + opex_frac)
    tes_power_annual_per_mw = 0.0

    rte = float(np.clip(round_trip_efficiency, 0.05, 0.999))
    eta_e2h = _thermal_efficiency_by_temp(process_temperature_c, eff_le_200, eff_gt_200)
    inv_eff = float(np.clip(inverter_efficiency, 0.5, 1.0))
    eta_charge = eta_discharge = float(np.sqrt(rte))
    h_max = float(max(0.5, max_storage_hours))

    solar_profile_df_year = solar_profile_df[
        solar_profile_df["year"] == solar_profile_df["year"].iloc[0]
    ].copy()
    solar_profile = solar_profile_df_year["P_kWperkWp"].to_numpy(dtype=float)
    hours = len(solar_profile)
    P_load_MW = load_target
    total_load_MWh = P_load_MW * hours
    annual_load_MWh = total_load_MWh  # n_years = 1

    # Rondo-style: tank ceiling / process floor (°C) and min charge:discharge power ratio.
    t_max_c = float(
        tes_temp_max_c if tes_temp_max_c is not None else batt_params.get("Thermal T_max C", 1500.0)
    )
    if tes_temp_min_c is not None:
        t_min_c = float(tes_temp_min_c)
    elif process_temperature_c is not None:
        t_min_c = float(process_temperature_c)
    else:
        t_min_c = float(batt_params.get("Thermal T_min C", 1200.0))
    if t_min_c >= t_max_c:
        t_min_c = float(0.999 * t_max_c)
        print(
            f"⚠️ T_min must be < T_max ({t_max_c:.0f}°C); using T_min={t_min_c:.1f}°C for usable window."
        )
    ratio_cd = charge_discharge_ratio_min
    if ratio_cd is None:
        ratio_cd = float(batt_params.get("Thermal Charge/Discharge ratio min", 4.0))
    ratio_cd = float(max(ratio_cd, 1e-6))

    f_min, usable_frac = _tes_usable_energy_fraction_linear(t_min_c, t_max_c)

    print(f"\n{'='*70}")
    print(f"Building HEAT STORAGE (TES) model for site {site_id}")
    print(
        f"Load target: {load_target} MW | Availability: {availability_factor*100}% | "
        f"VoLL: ${VoLL:,.0f}/MWh | RTE: {rte:.3f} | E->heat eff: {eta_e2h:.3f} | Max duration: {h_max:.1f} h"
    )
    crf_note = (
        f"battery params: life={lifetime_years}y, discount={discount_rate:.3f}, "
        f"opex_frac={opex_frac:.3f}"
    )
    print(
        f"TES CAPEX: energy ${e_capex:,.2f}/MWh, power ${p_capex:,.2f}/MW ({crf_note}); "
        f"FOM ${tes_om_per_MWyr:,.2f}/MW-yr"
    )
    print(f"TES cost model: {thermal_cost_model} | project_start={project_start_year} | process_temp_c={process_temperature_c}")
    print(
        f"TES geometry: T_max={t_max_c:.0f}°C, T_min={t_min_c:.0f}°C → "
        f"usable stored-energy fraction≈{usable_frac:.3f} (linear 0–T_max proxy); "
        f"P_charge ≥ {ratio_cd:.2f} × P_discharge; duration E ≤ {h_max:.1f} h × P_discharge; "
        f"firm constant process load = {P_load_MW:.2f} MW (intermittent charge via solar)."
    )
    print(f"{'='*70}\n")

    ######################### Cost coefficients #########################

    solar_cost_per_mw = (solar_capex_per_kW * 1000) * CRF + solar_om_per_MWyr
    tes_mw_cost = tes_power_annual_per_mw + tes_om_per_MWyr
    tes_mwh_cost = tes_energy_annual_per_mwh

    annual_solar_cost_func = (
        lambda S: (solar_capex_per_kW * 1000 * S) * CRF + solar_om_per_MWyr * S
    )

    ######################### Build model #########################

    h = Highs()
    h.setOptionValue("log_to_console", True)
    h.setOptionValue("mip_rel_gap", 0.01)
    h.silent()  # suppress verbose output during model building

    # --- Capacity variables (sizing, $ in objective) vs hourly dispatch below ---
    # P_charge / P_discharge: max AC power (MW) the equipment can move per hour.
    # charge[t] / discharge[t]: actual dispatch each hour (<= those caps). The optimizer
    # picks both sizes and hourly profiles.
    h.addVar(0, float("inf"))  # 0: S_MW
    h.addVar(0, float("inf"))  # 1: P_charge_MW
    h.addVar(0, float("inf"))  # 2: P_discharge_MW
    h.addVar(0, float("inf"))  # 3: E_TES_MWh nameplate thermal energy capacity

    # --- Hourly operational variables ---
    for _ in range(hours):
        h.addVar(0, float("inf"))  # solar_used
    for _ in range(hours):
        h.addVar(0, float("inf"))  # charge
    for _ in range(hours):
        h.addVar(0, float("inf"))  # discharge
    for _ in range(hours):
        h.addVar(0, float("inf"))  # soc (usable energy above T_min)
    for _ in range(hours):
        h.addVar(0, float("inf"))  # unserved

    # Column indices (order matches addVar calls above)
    ix_S_MW = 0
    ix_P_ch = 1
    ix_P_dis = 2
    ix_B_MWh = 3
    n_cap = 4

    ix_solar_used_start = n_cap
    ix_charge_start = n_cap + hours
    ix_discharge_start = n_cap + 2 * hours
    ix_soc_start = n_cap + 3 * hours
    ix_unserved_start = n_cap + 4 * hours

    # Set objective coefficients (minimize LCOE = cost / annual_load_MWh)
    h.changeColCost(ix_S_MW, solar_cost_per_mw / annual_load_MWh)
    pw = tes_mw_cost / annual_load_MWh
    h.changeColCost(ix_P_ch, pw)
    h.changeColCost(ix_P_dis, pw)
    h.changeColCost(ix_B_MWh, tes_mwh_cost / annual_load_MWh)
    for i in range(hours):
        h.changeColCost(ix_unserved_start + i, VoLL / annual_load_MWh)

    # Keep index lists for constraints and solution
    solar_used = list(range(ix_solar_used_start, ix_solar_used_start + hours))
    charge = list(range(ix_charge_start, ix_charge_start + hours))
    discharge = list(range(ix_discharge_start, ix_discharge_start + hours))
    soc = list(range(ix_soc_start, ix_soc_start + hours))
    unserved = list(range(ix_unserved_start, ix_unserved_start + hours))

    print(f"Variables built. Adding constraints...")

    INF = float("inf")
    max_unserved = (1 - availability_factor) * P_load_MW * hours

    ######################### Constraints #########################

    # --- Global capacity ---
    # Duration on discharge side: E_TES <= h_max * P_discharge. The firm load is met by
    # discharging at up to P_discharge; energy endurance at full **output** is E/P_dis.
    # Tying duration to P_charge instead would allow huge E with tiny P_dis—unable to
    # serve a constant MW load. (Charge can still be larger via P_charge >= ratio*P_dis.)
    h.addRow(-INF,0,2,np.array([ix_B_MWh, ix_P_dis], dtype=np.int32),np.array([1.0, -h_max], dtype=np.float64))

    # P_charge >= ratio_cd * P_discharge  =>  P_charge - ratio_cd * P_discharge >= 0
    h.addRow(0,INF,2,np.array([ix_P_ch, ix_P_dis], dtype=np.int32),np.array([1.0, -ratio_cd], dtype=np.float64))

    # --- Hourly constraints ---
    for t in range(hours):

        # (H1) Solar usage: solar_used[t] <= g[t]*S_MW
        h.addRow(-INF,0,2,np.array([solar_used[t], ix_S_MW], dtype=np.int32),np.array([1.0, -solar_profile[t]], dtype=np.float64))

        # (H2) AC bus energy balance (MW):
        #      inv_eff*solar_used - inv_eff*charge + inv_eff*discharge + unserved = P_load
        #      Constant firm process demand P_load_MW; unserved slack covers shortfalls.
        h.addRow(P_load_MW,P_load_MW,4,np.array([solar_used[t], charge[t], discharge[t], unserved[t]], dtype=np.int32),np.array([inv_eff, -inv_eff, inv_eff, 1.0], dtype=np.float64))

        # (H3) Usable SOC dynamics (MWh). eta_ch, eta_dis = sqrt(RTE). Let Q_in = eta_e2h*eta_ch*charge.
        #   t=0: soc[0] - Q_in[0] + discharge[0]/eta_dis = 0  =>  soc[0] = Q_in[0] - discharge[0]/eta_dis
        #   t>0: soc[t] - soc[t-1] - Q_in[t] + discharge[t]/eta_dis = 0
        if t == 0:
            h.addRow(0,0,3,np.array([soc[t], charge[t], discharge[t]], dtype=np.int32),np.array([1.0, -eta_e2h * eta_charge, 1.0 / eta_discharge], dtype=np.float64))
        else:
            h.addRow(0,0,4,np.array([soc[t], soc[t - 1], charge[t], discharge[t]], dtype=np.int32),np.array([1.0, -1.0, -eta_e2h * eta_charge, 1.0 / eta_discharge], dtype=np.float64))

        # (H4) Usable SOC ceiling: usable SOC <= usable_frac * E_TES
        h.addRow(-INF,0,2,np.array([soc[t], ix_B_MWh], dtype=np.int32),np.array([1.0, -usable_frac], dtype=np.float64))

        # (H5–H6) Charge / discharge power limits
        h.addRow(-INF,0,2,np.array([charge[t], ix_P_ch], dtype=np.int32),np.array([1.0, -1.0], dtype=np.float64))
        h.addRow(-INF,0,2,np.array([discharge[t], ix_P_dis], dtype=np.int32),np.array([1.0, -1.0], dtype=np.float64))

    # (C2) Unserved energy budget: sum_t unserved[t] <= max_unserved
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

    S_opt = h.variableValue(ix_S_MW)
    B_MWh_opt = h.variableValue(ix_B_MWh)
    P_ch_opt = float(h.variableValue(ix_P_ch))
    P_dis_opt = float(h.variableValue(ix_P_dis))
    B_MW_opt = P_dis_opt

    solar_available = solar_profile * S_opt
    solar_used_vals = np.array([h.variableValue(solar_used[t]) for t in range(hours)])
    charge_vals = np.array([h.variableValue(charge[t]) for t in range(hours)])
    discharge_vals = np.array([h.variableValue(discharge[t]) for t in range(hours)])
    soc_vals = np.array([h.variableValue(soc[t]) for t in range(hours)])
    unserved_vals = np.array([h.variableValue(unserved[t]) for t in range(hours)])
    curtailment = np.maximum(solar_available - solar_used_vals, 0)

    hourly_df = pd.DataFrame(
        {
            "Year": solar_profile_df_year["year"].values,
            "Month": solar_profile_df_year["timestamp"].dt.month,
            "Day": solar_profile_df_year["timestamp"].dt.day,
            "Hour": solar_profile_df_year["timestamp"].dt.hour,
            "Load_MW": P_load_MW,
            f"Solar_available_MW ({S_opt:.1f} MW)": solar_available,
            "Solar_used_MW": solar_used_vals,
            "TES_charge_MW": charge_vals,
            "TES_discharge_MW": discharge_vals,
            f"TES_usable_SOC_MWh (P_ch={P_ch_opt:.1f}, P_dis={P_dis_opt:.1f} MW, E={B_MWh_opt:.1f} MWh)": soc_vals,
            "Curtail_MW": curtailment,
            "Unserved_MW": unserved_vals,
        }
    )

    if demand_profile is not None:
        hourly_df["System_demand_MW"] = demand_profile[:hours]

    total_unserved_energy = unserved_vals.sum()
    annual_solar_cost = annual_solar_cost_func(S_opt)
    annual_tes_cost = (
        tes_energy_annual_per_mwh * B_MWh_opt
        + tes_mw_cost * (P_ch_opt + P_dis_opt)
        + tes_om_per_MWyr * (P_ch_opt + P_dis_opt)
    )
    annual_unserved_cost = total_unserved_energy * VoLL
    total_lcoe = (annual_solar_cost + annual_tes_cost + annual_unserved_cost) / annual_load_MWh

    energy_served = total_load_MWh - total_unserved_energy
    reliability = (energy_served / total_load_MWh) * 100
    unserved_hours = int(np.sum(unserved_vals > 0.01))

    print(f"\n{'='*70}")
    print(f"RESULTS (heat TES) - Site {site_id}")
    print(
        f"  Solar: {S_opt:.2f} MW | TES: P_charge={P_ch_opt:.2f} MW, P_discharge={P_dis_opt:.2f} MW, "
        f"E={B_MWh_opt:.2f} MWh (≤{h_max:.0f} h at discharge)"
    )
    print(
        f"  Reliability: {reliability:.2f}% | Unserved: {total_unserved_energy:,.0f} MWh ({unserved_hours} hrs)"
    )
    print(
        f"  LCOE — Solar: ${annual_solar_cost/annual_load_MWh:.2f} | "
        f"TES: ${annual_tes_cost/annual_load_MWh:.2f} | Total: ${total_lcoe:.2f}/MWh"
    )
    print(f"{'='*70}\n")

    results = {
        "site": site_id,
        "load_MW": load_target,
        "storage_technology": "heat_TES",
        "S_opt_MW": S_opt,
        "Heat_TES_power_MW": B_MW_opt,
        "Heat_TES_charge_power_MW": P_ch_opt,
        "Heat_TES_discharge_power_MW": P_dis_opt,
        "Heat_TES_energy_MWh": B_MWh_opt,
        "TES_temp_max_C": t_max_c,
        "TES_temp_min_C": t_min_c,
        "TES_usable_energy_fraction_linear": usable_frac,
        "TES_charge_discharge_ratio_min": ratio_cd,
        "TES_round_trip_efficiency": rte,
        "TES_electric_to_heat_efficiency": eta_e2h,
        "TES_max_storage_hours": h_max,
        "TES_cost_model": thermal_cost_model,
        "TES_project_start_year": int(project_start_year),
        "TES_process_temperature_c": process_temperature_c,
        "TES_capex_kwh_input": capex_kwh,
        "TES_opex_frac_input": opex_frac,
        "TES_lifetime_years_input": lifetime_years,
        "TES_discount_rate_input": discount_rate,
        "TES_energy_capex_input_$/MWh": e_capex,
        "TES_power_capex_input_$/MW": p_capex,
        "TES_fixed_om_input_$/MWyr": tes_om_per_MWyr,
        "TES_capex_input_includes_crf": False,
        # Same names as Li-ion runs so downstream CSV / maps keep working:
        "Battery_capacity_MW": B_MW_opt,
        "Battery_energy_MWh": B_MWh_opt,
        "LCOE_total_$perMWh": total_lcoe,
        "LCOE_solar_$perMWh": annual_solar_cost / annual_load_MWh,
        "LCOE_batt_$perMWh": annual_tes_cost / annual_load_MWh,
        "LCOE_TES_$perMWh": annual_tes_cost / annual_load_MWh,
        "LCOE_unserved_$perMWh": annual_unserved_cost / annual_load_MWh,
        "Reliability_%": reliability,
        "Unserved_MWh": total_unserved_energy,
        "Unserved_hours": unserved_hours,
    }

    return results, hourly_df, True


if __name__ == "__main__":
    print("HiGHS-based optimization with heat (thermal) storage (TES)")
    print("Install HiGHS with: pip install highspy")
