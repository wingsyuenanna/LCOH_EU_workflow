# generate_scenarios.py
# Build scenario folders and jobscripts for run_scenario.py.
# Each scenario uses the site's iso3_country so BNEF and battery costs match that country.
#
# Sample commands (run from ``flatblock_optimization/``):
#   # Li-ion (default):
#   python generate_scenarios.py \
#     --scenario-name all_sites_v1 \
#     --scenarios-csv inputs/sites.csv \
#     --sites-csv inputs/sites.csv
#
#   # Heat storage (TES), CPI-escalated CAPEX from base-year input:
#   python generate_scenarios.py \
#     --scenario-name heat_battery_iron_steel \
#     --storage-type heat \
#     --scenarios-csv inputs/sites_usa_iron_steel.csv \
#     --sites-csv inputs/sites_usa_iron_steel.csv
#
# # from flatblock_optimization/
# python generate_scenarios.py \
#   --scenario-name heat_battery_iron_steel \
#   --storage-type heat \
#   --scenarios-csv inputs/sites_usa_iron_steel.csv \
#   --sites-csv inputs/sites_usa_iron_steel.csv \
#   --project-start 2025 \
#   --input-bnef-costs ../BNEF/output/bnef_solar_pv_costs_v5.csv \
#   --input-battery-costs inputs/inpur_heat_battery_cost.csv \
#   --base-load-mw 100 \
#   --heat-rte 0.97 \
#   --heat-max-hours 12 \
#   --heat-tmax-c 1500 \
#   --heat-tmin-c 1200 \
#   --heat-cd-ratio 4.0

import argparse
import pandas as pd
from pathlib import Path

# Paths
log_root = "logs"
root = "./"

DEFAULT_AVAILABILITY = 0.90
SCENARIO_VERSION = "fix_solar_profile"
SOLAR_START_YEAR = 2023
SOLAR_END_YEAR = 2023
PEAK_DEMAND_CUTOFF = 0
DEFAULT_PROJECT_START = 2025

def main():
    parser = argparse.ArgumentParser(
        description="Generate scenario folders and jobscripts for run_scenario.py. "
        "Each jobscript passes the site's iso3_country so BNEF/battery costs match that country."
    )
    parser.add_argument(
        "--scenarios-csv",
        default="inputs/sites.csv",
        help="CSV with source_id and iso3_country (and optionally availability, years). Default: inputs/sites.csv",
    )
    parser.add_argument(
        "--scenarios-root",
        default=None,
        help="Root for scenario_* folders (default: scenarios/<SCENARIO_VERSION>).",
    )
    parser.add_argument(
        "--sites-csv",
        default=None,
        help="Sites CSV to pass to run_scenario.py (default: same as --scenarios-csv).",
    )
    parser.add_argument(
        "--project-start",
        type=int,
        default=DEFAULT_PROJECT_START,
        help="Project start year for cost lookup (default: 2025).",
    )
    parser.add_argument(
        "--input-bnef-costs",
        default="../BNEF/output/bnef_solar_pv_costs_v5.csv",
        help="Unified BNEF CSV path for jobscript (relative to run_scenario cwd; align with run_scenario.py defaults).",
    )
    parser.add_argument(
        "--input-battery-costs",
        default="../BNEF/output/battery_2025_cost.csv",
        help="Battery component CSV path for jobscript (relative to run_scenario cwd; use BNEF/output/... if run from repo root).",
    )
    parser.add_argument(
        "--scenario-name",
        default=SCENARIO_VERSION,
        metavar="NAME",
        help=f"Scenario directory under scenarios/ (default: {SCENARIO_VERSION}). Example: heat_battery_v1",
    )
    parser.add_argument(
        "--base-load-mw",
        type=float,
        default=100.0,
        help="Base flat load target passed to run_scenario.py before optional temperature scaling.",
    )
    parser.add_argument(
        "--use-temperature-demand-scaling",
        action="store_true",
        help="Append --use-temperature-demand-scaling to jobscripts (heat mode only).",
    )
    parser.add_argument(
        "--storage-type",
        choices=("liion", "heat"),
        default="liion",
        help="liion: default BESS model. heat: append --storage-type heat and heat runtime options to each jobscript.",
    )
    parser.add_argument("--heat-rte", type=float, default=0.88, help="TES round-trip efficiency (heat mode).")
    parser.add_argument(
        "--heat-max-hours",
        type=float,
        default=12.0,
        help="Max TES duration E/P in hours (heat mode).",
    )
    parser.add_argument(
        "--heat-tmax-c",
        type=float,
        default=1500.0,
        help="TES tank maximum temperature bound T_max in C (heat mode).",
    )
    parser.add_argument(
        "--heat-tmin-c",
        type=float,
        default=1200.0,
        help="TES minimum usable/process temperature bound T_min in C (heat mode).",
    )
    parser.add_argument(
        "--heat-cd-ratio",
        type=float,
        default=4.0,
        help="Minimum TES charge/discharge power ratio P_charge/P_discharge (heat mode).",
    )
    args = parser.parse_args()

    scenarios_csv = Path(args.scenarios_csv)
    scenarios_root = Path(args.scenarios_root or f"scenarios/{args.scenario_name}")
    scenarios_root.mkdir(parents=True, exist_ok=True)

    sites_csv = args.sites_csv or args.scenarios_csv

    if args.storage_type == "heat":
        print("Heat (TES) mode: thermal CAPEX/OPEX/lifetime/discount/efficiency read from battery params file by year.")

    df = pd.read_csv(scenarios_csv)
    if "iso3_country" not in df.columns:
        raise ValueError(
            f"{scenarios_csv} must have an 'iso3_country' column so costs can be matched by country."
        )

    for idx, row in df.iterrows():
        site_id = row["source_id"]
        try:
            site_folder = str(int(site_id))
        except (TypeError, ValueError):
            # Replace filesystem-unsafe chars so site_folder is a plain directory name
            site_folder = str(site_id).strip().replace("/", "_").replace("\\", "_").replace(" ", "_")
        iso3_country = row["iso3_country"]
        avail = row.get("availability", DEFAULT_AVAILABILITY)
        solar_start = int(row.get("solar_start_year", SOLAR_START_YEAR))
        solar_end = int(row.get("solar_end_year", SOLAR_END_YEAR))
        peak_cutoff = row.get("peak_demand_cutoff", PEAK_DEMAND_CUTOFF)
        project_start = int(row.get("project_start", args.project_start))
        # Per-site load: use load_mw column if present, else fall back to CLI --base-load-mw
        site_load_mw = float(row["load_mw"]) if "load_mw" in row.index and pd.notna(row.get("load_mw")) else args.base_load_mw
        # Per-site process temperature for TES T_min: use process_temp_c column if present
        site_process_temp = row.get("process_temp_c", None)
        site_process_temp_arg = f"    --heat-process-temperature-c {float(site_process_temp):.1f} \\\n" if pd.notna(site_process_temp) else ""

        # Per-site folder name is scenario_<source_id>; scenario set (e.g. fix_solar_profile) is the parent dir.
        scenario_folder = scenarios_root / f"scenario_{site_folder}"
        scenario_folder.mkdir(exist_ok=True)

        scenario_dir_str = str(scenario_folder)
        results_folder_str = f"{scenario_dir_str}/results"

        jobscript_path = scenario_folder / "jobscript.sh"
        heat_lines = ""
        if args.storage_type == "heat":
            temp_scale_line = (
                "    --use-temperature-demand-scaling \\\n"
                if args.use_temperature_demand_scaling
                else ""
            )
            # Include --heat-tmin-c only when no per-site process temperature is set;
            # if per-site temp is present (site_process_temp_arg), let it govern T_min.
            tmin_line = (
                "" if site_process_temp_arg
                else f"    --heat-tmin-c {args.heat_tmin_c} \\\n"
            )
            heat_lines = f"""    --storage-type heat \\
    --heat-rte {args.heat_rte} \\
    --heat-max-hours {args.heat_max_hours} \\
    --heat-tmax-c {args.heat_tmax_c} \\
    --heat-cd-ratio {args.heat_cd_ratio} \\
{tmin_line}{site_process_temp_arg}{temp_scale_line}    --base-load-mw {site_load_mw} \\
"""
        with open(jobscript_path, "w") as f:
            f.write(f"""#!/bin/bash
python run_scenario.py \\
    --site-id {site_id} \\
    --iso3-country {iso3_country} \\
    --scenario-dir {scenario_dir_str} \\
    --availability {avail} \\
    --solar-start {solar_start} \\
    --solar-end {solar_end} \\
    --project-start {project_start} \\
    --peak-demand-cutoff {peak_cutoff} \\
    --sites-csv {sites_csv} \\
    --input-bnef-costs {args.input_bnef_costs} \\
    --input-battery-costs {args.input_battery_costs} \\
{heat_lines}    --results-folder {results_folder_str}

date
""")

    print(f"Generated {len(df)} jobscripts under {scenarios_root}/")
    print("Each jobscript uses --iso3-country from the scenarios CSV so BNEF and battery costs match the site country.")


if __name__ == "__main__":
    main()
