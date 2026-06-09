"""
combine_results.py — Aggregate all per-site summary CSVs into one table.

Usage (from flatblock_optimization/):
    python combine_results.py \
        --scenario-name eu_heat_tes_v1 \
        --sites-csv inputs/sites.csv \
        --out-csv ../outputs/flatblock_eu_heat_tes_v1_results.csv

Outputs one row per site with optimization results + metadata from sites.csv.
LCOE is converted from USD to EUR using ECB 2024 average rate (0.921 USD/EUR).
"""
from __future__ import annotations
import argparse
import pandas as pd
from pathlib import Path

EUR_PER_USD = 0.921  # ECB 2024 average


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-name", default="eu_heat_tes_v1")
    parser.add_argument("--scenarios-root", default="scenarios")
    parser.add_argument("--sites-csv", default="inputs/sites.csv")
    parser.add_argument(
        "--out-csv",
        default="../outputs/flatblock_eu_heat_tes_v1_results.csv",
    )
    args = parser.parse_args()

    root = Path(args.scenarios_root) / args.scenario_name
    if not root.is_dir():
        raise FileNotFoundError(f"Scenario root not found: {root}")

    sites = pd.read_csv(args.sites_csv)

    records = []
    infeasible = []
    missing = []

    for site_dir in sorted(root.iterdir()):
        if not site_dir.is_dir() or not site_dir.name.startswith("scenario_"):
            continue
        results_dir = site_dir / "results"
        # Find summary CSV
        summaries = list(results_dir.glob("summary_site*.csv")) if results_dir.exists() else []
        infeas = list(results_dir.glob("INFEASIBLE_site*.txt")) if results_dir.exists() else []

        if summaries:
            df = pd.read_csv(summaries[0])
            records.append(df.iloc[0].to_dict())
        elif infeas:
            # Read site_id from the text file
            text = infeas[0].read_text()
            sid = text.split(" - ")[0].strip()
            infeasible.append({"site": sid, "reason": "optimizer_infeasible"})
        else:
            missing.append(site_dir.name)

    print(f"Completed: {len(records)} | Optimizer-infeasible: {len(infeasible)} | Missing results: {len(missing)}")

    if not records:
        print("No results to combine.")
        return

    res = pd.DataFrame(records)

    # Merge with sites metadata
    res = res.merge(
        sites[[
            "source_id", "eprtr_facility_name", "iso3_country",
            "eprtr_lat", "eprtr_lon", "eprtr_activity",
            "process_heat_temp_band", "annual_heat_demand_MWh_th",
            "potential_mw_solar", "suitable_land_adj_km2",
            "land_feasibility_adj", "overlap_n_neighbours",
        ]],
        left_on="site",
        right_on="source_id",
        how="left",
    )

    # Convert LCOE: USD/MWh → EUR/MWh
    for col in ["LCOE_total_$perMWh", "LCOE_solar_$perMWh", "LCOE_batt_$perMWh"]:
        eur_col = col.replace("$perMWh", "EUR_MWh")
        res[eur_col] = (res[col] * EUR_PER_USD).round(2)

    # Solar overbuilding ratio (installed MW / load MW)
    res["solar_overbuilding_ratio"] = (res["S_opt_MW"] / res["load_MW"]).round(1)

    # Flag sites where optimal solar exceeds available land
    res["solar_exceeds_land"] = res["S_opt_MW"] > res["potential_mw_solar"]

    # Save
    out_path = Path(args.out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out_path, index=False)
    print(f"Saved {len(res)} rows → {out_path}")

    # Summary stats
    print(f"\n=== LCOE Summary (EUR/MWh_th) ===")
    print(res["LCOE_total_EUR_MWh"].describe().round(1))

    by_country = res.groupby("iso3_country")["LCOE_total_EUR_MWh"].agg(["count", "mean", "min", "max"]).round(1)
    print(f"\n=== By Country ===")
    print(by_country.to_string())

    feasible_only = res[res["land_feasibility_adj"] == "FEASIBLE"]
    print(f"\n=== FEASIBLE sites only (n={len(feasible_only)}) ===")
    print(f"Mean LCOE: {feasible_only['LCOE_total_EUR_MWh'].mean():.1f} EUR/MWh_th")
    print(f"Range: {feasible_only['LCOE_total_EUR_MWh'].min():.1f} – {feasible_only['LCOE_total_EUR_MWh'].max():.1f}")

    exceed = res[res["solar_exceeds_land"]]
    print(f"\n=== Sites where optimal solar > available land ({len(exceed)}) ===")
    if not exceed.empty:
        print(exceed[["site","iso3_country","S_opt_MW","potential_mw_solar","land_feasibility_adj","LCOE_total_EUR_MWh"]].to_string(index=False))

    if infeasible:
        print(f"\n=== Optimizer-infeasible sites ===")
        for s in infeasible:
            print(f"  {s}")
    if missing:
        print(f"\n=== Missing results (no run output) ===")
        for m in missing:
            print(f"  {m}")


if __name__ == "__main__":
    main()
