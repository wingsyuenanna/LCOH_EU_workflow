# run_scenario.py
# Wrapper script to run a solar+storage+gas optimization scenario
from __future__ import annotations

# Example command:
# python run_scenario.py \
#   --site-id 731809 \
#   --scenario-dir scenarios/sites_heat_battery_mfg/scenario_731809 \
#   --iso3-country USA \
#   --availability 0.90 \
#   --solar-start 2023 \
#   --solar-end 2023 \
#   --project-start 2025 \
#   --peak-demand-cutoff 0 \
#   --sites-csv inputs/sites_usa_mfg.csv \
#   --input-bnef-costs ../BNEF/output/bnef_solar_pv_costs_v5.csv \
#   --input-battery-costs inputs/input_heat_battery_cost.csv \
#   --storage-type heat \
#   --heat-process-temperature-c 850 \
#   --heat-rte 0.97 \
#   --heat-max-hours 12 \
#   --heat-tmax-c 1500 \
#   --heat-tmin-c 850 \
#   --heat-cd-ratio 4.0

import os
import argparse
from pathlib import Path
import pandas as pd

# Repo root = parent of flatblock_optimization/ (so BNEF/ resolves when cwd is flatblock_optimization)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_cost_csv(arg_path: str, *, what: str) -> Path:
    """Resolve BNEF/battery CSV paths: cwd-relative, then repo-root-relative."""
    p = Path(arg_path).expanduser()
    if p.is_file():
        return p.resolve()
    cand = (Path.cwd() / p).resolve()
    if cand.is_file():
        return cand
    cand = (_REPO_ROOT / p).resolve()
    if cand.is_file():
        return cand
    raise FileNotFoundError(
        f"{what} not found: {arg_path!r}\n"
        f"  Tried: {(Path.cwd() / Path(arg_path)).resolve()}\n"
        f"  Tried: {_REPO_ROOT / Path(arg_path)}"
    )


def _infer_heat_demand_multiplier(site_params: dict) -> tuple[float, str]:
    """
    Determine heat demand multiplier from site data.

    Priority:
    1) explicit multiplier column (e.g. ``heat_demand_multiplier``)
    2) temperature bucket from ``*_temp_c`` style columns
    3) fallback 1.0
    """
    explicit_cols = (
        "heat_demand_multiplier",
        "demand_multiplier_temp",
        "temp_demand_multiplier",
    )
    for col in explicit_cols:
        if col in site_params and site_params[col] is not None:
            try:
                v = float(site_params[col])
                if v > 0:
                    return v, f"explicit {col}={v}"
            except (TypeError, ValueError):
                pass

    temp_cols = ("required_temp_c", "process_temp_c", "target_temp_c", "heat_temperature_c")
    temp_c = None
    temp_col = None
    for col in temp_cols:
        if col in site_params and site_params[col] is not None:
            try:
                temp_c = float(site_params[col])
                temp_col = col
                break
            except (TypeError, ValueError):
                pass

    if temp_c is None:
        return 1.0, "no multiplier/temperature column found"

    # Simple default buckets (override with explicit heat_demand_multiplier in CSV when needed)
    if temp_c < 200:
        mult = 1.00
    elif temp_c < 400:
        mult = 1.08
    elif temp_c < 700:
        mult = 1.18
    else:
        mult = 1.30
    return mult, f"{temp_col}={temp_c:.1f}C bucket"


def _infer_process_temperature_c(site_params: dict) -> tuple[float | None, str]:
    """Find process/required temperature from common column names."""
    temp_cols = ("required_temp_c", "process_temp_c", "target_temp_c", "heat_temperature_c")
    for col in temp_cols:
        if col in site_params and site_params[col] is not None:
            try:
                return float(site_params[col]), f"{col} from sites CSV"
            except (TypeError, ValueError):
                pass
    return None, "no temperature column found"


def main():
    parser = argparse.ArgumentParser(description="Run a scenario for solar+storage+gas optimization")
    parser.add_argument("--site-id", type=str, required=True, help="Site identifier (string source_id or integer)")
    parser.add_argument("--scenario-dir", required=True)
    parser.add_argument("--iso3-country", required=True)
    parser.add_argument("--availability", type=float, required=True)
    parser.add_argument("--solar-start", type=int, required=True)
    parser.add_argument("--solar-end", type=int, required=True)
    parser.add_argument("--project-start", type=int, required=True)
    parser.add_argument("--peak-demand-cutoff", type=float, required=True)
    parser.add_argument("--sites-csv", default="inputs/sites.csv")
    parser.add_argument("--solar-data-folder", default="solar_data_parquet_v3")
    parser.add_argument(
        "--solar-local-folder",
        default=str(_REPO_ROOT / "off_grid_electricity_run" / "outputs" / "pvgis_hourly"),
        help="Local directory of PVGIS hourly CSVs. Used instead of S3 when the folder exists.",
    )
    parser.add_argument("--demand-data-folder", default="inputs/demand")
    parser.add_argument(
        "--input-bnef-costs",
        default=str(_REPO_ROOT / "inputs" / "bnef_country_costs.csv"),
        help="Unified BNEF CSV (solar + battery FOM by iso3_country, year).",
    )
    parser.add_argument(
        "--input-battery-costs",
        default=str(_REPO_ROOT / "inputs" / "battery_tes_costs.csv"),
        help="TES cost CSV (Thermal CAPEX $/kWh, efficiencies, temperatures, etc. by year).",
    )
    parser.add_argument("--results-folder", default=None)
    parser.add_argument(
        "--base-load-mw",
        type=float,
        default=100.0,
        help="Base flat load target before optional temperature scaling (default: 100 MW).",
    )
    parser.add_argument(
        "--use-temperature-demand-scaling",
        action="store_true",
        help="If set in heat mode, scale load target using heat_demand_multiplier or required_temp_c buckets from sites CSV.",
    )
    parser.add_argument(
        "--storage-type",
        choices=("liion", "heat"),
        default="liion",
        help="liion: electrochemical BESS (default). heat: thermal storage (TES) with direct $/MWh CAPEX.",
    )
    parser.add_argument(
        "--heat-rte",
        type=float,
        default=0.97, # based on Rondo's numbers
        help="TES round-trip efficiency (only with --storage-type heat).",
    )
    parser.add_argument(
        "--heat-process-temperature-c",
        type=float,
        default=None,
        help="Optional process temperature override (C) for heat efficiency bucket. If omitted, tries sites CSV temperature columns.",
    )
    parser.add_argument(
        "--heat-max-hours",
        type=float,
        default=12.0,
        help="Max storage duration E/P on discharge side in hours (only with --storage-type heat).",
    )
    parser.add_argument(
        "--heat-tmax-c",
        type=float,
        default=None,
        help="Tank maximum temperature bound T_max (°C); default from battery CSV or 1500.",
    )
    parser.add_argument(
        "--heat-tmin-c",
        type=float,
        default=None,
        help="Minimum usable / process temperature T_min (°C); default: CLI/process temp or battery CSV.",
    )
    parser.add_argument(
        "--heat-cd-ratio",
        type=float,
        default=None,
        dest="heat_cd_ratio",
        help="Minimum P_charge / P_discharge (e.g. 4.0); default from battery CSV.",
    )

    args = parser.parse_args()

    bnef_csv = _resolve_cost_csv(args.input_bnef_costs, what="BNEF unified costs (--input-bnef-costs)")
    battery_csv = _resolve_cost_csv(args.input_battery_costs, what="Battery costs (--input-battery-costs)")

    scenario_dir = Path(args.scenario_dir).resolve()
    results_folder = Path(args.results_folder or scenario_dir / "results")
    results_folder.mkdir(parents=True, exist_ok=True)

    # Load site parameters
    from utils.loaders.load_site import load_site_params
    # site_columns = [
        # "ct_capacity_mw",
        # "ct_vc",
        # "ccgt_capacity_mw",
        # "ccgt_vc",
        # "potential_mw_solar"
    # ]
    site_params = load_site_params(
        site_id=args.site_id,
        sites_csv=args.sites_csv,
        # columns=site_columns
    )

    # Load costs: unified BNEF (solar + battery FOM by country) + battery component table (by year)
    from utils.loaders.load_re_costs import load_re_costs
    input_costs_dict = load_re_costs(
        str(bnef_csv),
        str(battery_csv),
        args.iso3_country,
        str(args.project_start),
    )
    print(f"Costs loaded for iso3_country={args.iso3_country}, project_start={args.project_start} (BNEF + battery components)")
    
    # gas_cap = site_params["ct_capacity_mw"] + site_params["ccgt_capacity_mw"]
    # print(f"Total gas capacity: {gas_cap} MW")
    
    # Load solar (local PVGIS hourly CSVs preferred; falls back to S3 if folder absent)
    from utils.loaders.load_solar import load_solar_profile
    solar_profile_df, solar_array = load_solar_profile(
        site_id=args.site_id,
        year=args.solar_start,
        bucket_name="annaiecc",
        folder_prefix=args.solar_data_folder,
        start_year=args.solar_start,
        end_year=args.solar_end,
        local_folder=args.solar_local_folder,
    )
    
    print(solar_profile_df.head())

    # Align demand and gas availability if peak cutoff is nonzero
    if args.peak_demand_cutoff > 0:
        from utils.loaders.load_demand import get_peak_hours_binary
        gas_allowed, aligned_demand = get_peak_hours_binary(
            site_id=args.site_id,
            solar_profile_df=solar_profile_df,
            peak_cutoff=args.peak_demand_cutoff,
            folder=args.demand_data_folder,
            start_year=args.solar_start,
            end_year=args.solar_end,
            flat_block=True,
            flat_block_load_mw=100
        )
    else:
        aligned_demand = None
        gas_allowed = None

    print("✅ Data loaded")

    # Run optimization
    if args.storage_type == "heat":
        from solvers.optimize_flatblock_highs_heat_battery import (
            run_flatblock_optimization_heat_battery_highs,
        )
        inferred_temp_c, inferred_temp_note = _infer_process_temperature_c(site_params)
        process_temp_c = args.heat_process_temperature_c
        if process_temp_c is None:
            process_temp_c = inferred_temp_c
        print(
            f"Heat process temperature: {process_temp_c} C "
            f"({'CLI override' if args.heat_process_temperature_c is not None else inferred_temp_note})"
        )

        def _run_opt(**kwargs):
            return run_flatblock_optimization_heat_battery_highs(
                **kwargs,
                round_trip_efficiency=args.heat_rte,
                project_start_year=args.project_start,
                process_temperature_c=process_temp_c,
                max_storage_hours=args.heat_max_hours,
                tes_temp_max_c=args.heat_tmax_c,
                tes_temp_min_c=args.heat_tmin_c,
                charge_discharge_ratio_min=args.heat_cd_ratio,
            )

    else:
        from solvers.optimize_flatblock_highs_unserved_v2 import run_flatblock_optimization_highs

        def _run_opt(**kwargs):
            return run_flatblock_optimization_highs(**kwargs)

    # load_target = gas_cap
    # step = max(10, gas_cap * 0.05) # MW; takes whichever is larger for tractability
    base_load_mw = float(args.base_load_mw)
    demand_multiplier = 1.0
    demand_multiplier_note = "disabled"
    if args.storage_type == "heat" and args.use_temperature_demand_scaling:
        demand_multiplier, demand_multiplier_note = _infer_heat_demand_multiplier(site_params)
    load_target_mw = base_load_mw * demand_multiplier
    if args.storage_type == "heat":
        print(
            f"Heat demand scaling: base_load={base_load_mw:.2f} MW × multiplier={demand_multiplier:.3f} "
            f"-> load_target={load_target_mw:.2f} MW ({demand_multiplier_note})"
        )
    step = load_target_mw * 0.05
    feasible = False
    
    while not feasible and load_target_mw > 0:
        print(f"Trying optimization with load = {load_target_mw} MW...")
        results, hourly_df, feasible = _run_opt(
            site_id=args.site_id,
            availability_factor=args.availability,
            site_params=site_params,
            solar_profile_df=solar_profile_df,
            demand_profile=aligned_demand,
            input_costs=input_costs_dict,
            load_target=load_target_mw,
        )
        
        if not feasible:
            print(f"Infeasible at load {load_target_mw} MW, decrementing by {step} MW")
            load_target_mw -= step
    if feasible:
        print(f"✅ Feasible optimization found at load {load_target_mw} MW")
        if results is not None:
            results["Base_load_MW"] = base_load_mw
            results["Heat_demand_multiplier"] = demand_multiplier
            results["Heat_demand_multiplier_note"] = demand_multiplier_note
    else:
        print("⚠️ No feasible solution found, even at load = 0 MW")
        results = None
        hourly_df = None

    # Sanitize site_id for use in filenames (replace / and other path-unsafe chars)
    site_id_safe = str(args.site_id).replace("/", "_").replace("\\", "_").replace(" ", "_")

    # Save results
    if results is not None and hourly_df is not None:
        summary_file = results_folder / f"summary_site{site_id_safe}.csv"
        pd.DataFrame([results]).to_csv(summary_file, index=False)
        hourly_file = results_folder / f"hourly_site{site_id_safe}.csv"
        hourly_df.to_csv(hourly_file, index=False)
        print(f"✅ Scenario complete. Results saved to {results_folder}")
    else:
        # Save an error file indicating infeasibility
        error_file = results_folder / f"INFEASIBLE_site{site_id_safe}.txt"
        with open(error_file, 'w') as f:
            f.write(f"Site {args.site_id} - NO FEASIBLE SOLUTION FOUND\n")
            f.write(f"Optimization attempted down to load = 0 MW\n")
            f.write(f"No feasible solution found at any load level.\n")
        print(f"⚠️ No feasible solution found. Error log saved to {error_file}")

if __name__ == "__main__":
    main()
