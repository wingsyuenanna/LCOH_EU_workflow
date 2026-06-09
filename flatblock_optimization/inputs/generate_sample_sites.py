# generate_sample_sites.py
# Build a sample sites CSV from facility_master: one site per country for the
# top N capacity countries (optionally all countries), for use with
# generate_scenarios.py and run_scenario.py with BNEF country-specific costs.

import argparse
from pathlib import Path

import pandas as pd

MW_per_KM2_solar = 100  # same as compile_sites: ~100 MW/km² for solar


def main():
    parser = argparse.ArgumentParser(
        description="Generate sites_sample_countries.csv from facility master for flatblock_optimization."
    )
    parser.add_argument(
        "--sector",
        default=None,
        choices=["power", "mineral_extraction", "manufacturing"],
        help="Optional sector filter: power, mineral_extraction, or manufacturing. Default: no filter.",
    )
    parser.add_argument(
        "--facility-master",
        default=None,
        help="Path to facility_master CSV (default: ../views/facility_master_v5.csv when run from flatblock_optimization)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: flatblock_optimization/inputs/sites_sample_countries.csv)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Number of top-capacity countries to include. If omitted, include all countries.",
    )
    parser.add_argument(
        "--include-iso",
        action="append",
        default=[],
        dest="include_iso",
        help="Always include these ISO3 codes (e.g. CHN USA IND) even if not in top N. Can repeat.",
    )
    args = parser.parse_args()

    inputs_dir = Path(__file__).resolve().parent
    flatblock_dir = inputs_dir.parent
    repo_root = flatblock_dir.parent
    facility_path = Path(
        args.facility_master or repo_root / "views" / "facility_master_v5.csv"
    )
    out_path = Path(args.out or inputs_dir / "sites_sample_countries.csv")

    # Read all needed columns; capacity and capacity_units may be missing in some masters
    all_cols = [
        "source_id", "source_name", "source_type", "iso3_country",
        "sector", "subsector", "lat", "lon", "capacity", "capacity_units",
        "co2e_20yr", "grassland_km2_5", "bare_sparse_km2_5",
        "grassland_km2_15", "bare_sparse_km2_15",
    ]
    avail = pd.read_csv(facility_path, nrows=0).columns.tolist()
    usecols = [c for c in all_cols if c in avail]
    df = pd.read_csv(facility_path, usecols=usecols)
    if "capacity" not in df.columns:
        df["capacity"] = 0
    df["capacity"] = pd.to_numeric(df["capacity"], errors="coerce").fillna(0)
    for col in ["co2e_20yr", "grassland_km2_5", "bare_sparse_km2_5", "grassland_km2_15", "bare_sparse_km2_15"]:
        if col not in df.columns:
            df[col] = 0
    df[["grassland_km2_5", "bare_sparse_km2_5", "grassland_km2_15", "bare_sparse_km2_15"]] = (
        df[["grassland_km2_5", "bare_sparse_km2_5", "grassland_km2_15", "bare_sparse_km2_15"]].fillna(0)
    )
    df = df.loc[df["iso3_country"].notna() & (df["capacity"] > 0)].copy()
    if args.sector:
        if "sector" not in df.columns:
            raise ValueError(
                f"--sector was provided but 'sector' column is missing from {facility_path}"
            )
        df = df.loc[df["sector"] == args.sector].copy()

    no_filters = (args.sector is None) and (args.top_n is None) and (not args.include_iso)
    if no_filters:
        for col in ["grassland_km2_5", "bare_sparse_km2_5", "grassland_km2_15", "bare_sparse_km2_15"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        potential_15 = (df["grassland_km2_15"] + df["bare_sparse_km2_15"]) * MW_per_KM2_solar
        potential_15 = potential_15.where(potential_15 >= 1, df["capacity"].clip(lower=1))
        potential_5 = (df["grassland_km2_5"] + df["bare_sparse_km2_5"]) * MW_per_KM2_solar
        potential_5 = potential_5.where(potential_5 >= 1, potential_15)

        out_df = df.copy()
        out_df["potential_mw_solar_5"] = potential_5
        out_df["potential_mw_solar_15"] = potential_15
    else:
        by_country = (
            df.groupby("iso3_country", as_index=False)["capacity"]
            .sum()
            .sort_values("capacity", ascending=False)
        )
        if args.top_n is None:
            top_iso = by_country["iso3_country"].tolist()
            for iso in args.include_iso:
                iso = iso.strip().upper()
                if iso and iso not in top_iso:
                    top_iso.append(iso)
        else:
            n_include = len(args.include_iso)
            n_from_ranking = max(0, args.top_n - n_include)
            top_iso = by_country.head(n_from_ranking)["iso3_country"].tolist()
            for iso in args.include_iso:
                iso = iso.strip().upper()
                if iso and iso not in top_iso:
                    top_iso.append(iso)
            top_iso = top_iso[: args.top_n]

        rows = []
        for iso in top_iso:
            country_fac = df.loc[df["iso3_country"] == iso].nlargest(1, "capacity").iloc[0]
            g5 = float(country_fac.get("grassland_km2_5", 0) or 0)
            b5 = float(country_fac.get("bare_sparse_km2_5", 0) or 0)
            g15 = float(country_fac.get("grassland_km2_15", 0) or 0)
            b15 = float(country_fac.get("bare_sparse_km2_15", 0) or 0)
            potential_5 = (g5 + b5) * MW_per_KM2_solar
            potential_15 = (g15 + b15) * MW_per_KM2_solar
            if potential_15 < 1:
                potential_15 = max(1.0, float(country_fac.get("capacity", 0)))
            if potential_5 < 1:
                potential_5 = potential_15
            rows.append({
                "source_id": country_fac.get("source_id"),
                "source_name": country_fac.get("source_name"),
                "source_type": country_fac.get("source_type"),
                "iso3_country": iso,
                "sector": country_fac.get("sector"),
                "subsector": country_fac.get("subsector"),
                "lat": country_fac.get("lat"),
                "lon": country_fac.get("lon"),
                "capacity": float(country_fac.get("capacity", 0) or 0),
                "capacity_units": country_fac.get("capacity_units"),
                "co2e_20yr": country_fac.get("co2e_20yr", 0),
                "grassland_km2_5": g5,
                "bare_sparse_km2_5": b5,
                "grassland_km2_15": g15,
                "bare_sparse_km2_15": b15,
                "potential_mw_solar_5": potential_5,
                "potential_mw_solar_15": potential_15,
            })

        out_df = pd.DataFrame(rows)

    for col in ["source_id", "source_name", "source_type", "sector", "subsector", "lat", "lon", "capacity", "capacity_units", "co2e_20yr"]:
        if col not in out_df.columns:
            out_df[col] = None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    print(f"Wrote {len(out_df)} sites to {out_path}")
    preview = out_df[["source_id", "iso3_country", "source_name", "potential_mw_solar_15"]]
    if len(preview) > 50:
        preview = preview.head(50)
        print(preview.to_string(index=False))
        print("... (preview truncated; use the output CSV for full contents)")
    else:
        print(preview.to_string(index=False))


if __name__ == "__main__":
    main()
