"""
Build ``sites.csv`` for flatblock from facility master, restricted to the top local LCOE outliers
vs nearby sites (same logic as ``utils/analysis/neighbor_lcoe_contrast.py``).

**2× neighbor LCOE (vs local median):** use the pre-enriched CSV and a ratio threshold. Column
``ratio_to_neighbor_median`` is (your LCOE) / (neighbor median LCOE). A value ≥ 2 means at least
double the neighbors’ median.

  python inputs/compile_sites.py \\
    --enriched-csv ../outputs/combined_results_all_sites_v1_neighbor_lcoe.csv \\
    --sort-by ratio_median --min-ratio-median 2 --top-n 500

Use a large ``--top-n`` if you want every site above the threshold (not only the top 40).

If you omit ``--enriched-csv``, neighbor metrics are recomputed from ``--combined-csv`` (same
radius/country logic as ``neighbor_lcoe_contrast.py`` defaults may differ from your saved file).

After regenerating ``sites.csv``, refresh RNinja hourly profiles on S3 for those ``source_id`` values
so ``run_scenario.py`` (default ``--solar-data-folder solar_data_parquet_v3``) picks them up:

  export RENEWABLES_NINJA_TOKEN=...
  python solar_profile/scripts/upload_rninja_parquet_to_s3.py \
    --sites-csv flatblock_optimization/inputs/sites.csv \
    --year 2023 --bucket annaiecc --prefix solar_data_parquet_v3

That overwrites ``s3://<bucket>/solar_data_parquet_v3/source_id=<id>/year=<year>/...`` for each site.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

MW_per_KM2_solar = 100

_FLATBLOCK = Path(__file__).resolve().parents[1]
_REPO_ROOT = _FLATBLOCK.parent
if str(_FLATBLOCK) not in sys.path:
    sys.path.insert(0, str(_FLATBLOCK))

from utils.analysis.neighbor_lcoe_contrast import (  # noqa: E402
    enrich_combined_df_with_neighbor_lcoe,
    top_neighbor_lcoe_outliers,
)

FACILITY_COLS = [
    "source_id",
    "source_name",
    "source_type",
    "iso3_country",
    "sector",
    "subsector",
    "lat",
    "lon",
    "co2e_20yr",
    "grassland_km2_5",
    "bare_sparse_km2_5",
    "grassland_km2_15",
    "bare_sparse_km2_15",
]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--facility-master",
        type=Path,
        default=_REPO_ROOT / "views" / "facility_master_v6.csv",
        help="Facility attributes (lat/lon, land buffers, …)",
    )
    p.add_argument(
        "--combined-csv",
        type=Path,
        default=_FLATBLOCK / "outputs" / "combined_results_all_sites_v1.csv",
        help="Combined flatblock results (used only if --enriched-csv is not set)",
    )
    p.add_argument(
        "--enriched-csv",
        type=Path,
        default=None,
        help="Precomputed neighbor metrics (e.g. combined_results_*_neighbor_lcoe.csv); skips recompute",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "sites.csv",
        help="Output sites CSV for flatblock inputs",
    )
    p.add_argument("--top-n", type=int, default=40, help="How many top outliers to keep (by --sort-by)")
    p.add_argument("--radius-km", type=float, default=80.0, help="Neighbor search radius (great-circle km)")
    p.add_argument(
        "--same-country-only",
        action="store_true",
        default=True,
        help="Only count neighbors in the same iso3_country (default: on)",
    )
    p.add_argument(
        "--no-same-country-only",
        action="store_false",
        dest="same_country_only",
        help="Count all neighbors within radius regardless of country",
    )
    p.add_argument("--min-neighbors", type=int, default=4, help="Minimum peers in radius to rank a site")
    p.add_argument(
        "--sort-by",
        choices=("abs_z", "ratio_median", "ratio_mean", "ratio_cheapest"),
        default="abs_z",
        help="Outlier ranking (default: |z| vs neighbor LCOE distribution)",
    )
    p.add_argument(
        "--min-ratio-median",
        type=float,
        default=None,
        help="Keep rows with ratio_to_neighbor_median >= this (e.g. 2 for 2× neighbors) before top-n",
    )
    p.add_argument(
        "--sector",
        type=str,
        default=None,
        help="If set, restrict combined CSV to this sector before neighbor metrics",
    )
    args = p.parse_args()

    if args.enriched_csv is not None:
        if not args.enriched_csv.is_file():
            print(f"Enriched CSV not found: {args.enriched_csv}", file=sys.stderr)
            return 1
        enriched = pd.read_csv(args.enriched_csv)
        if args.sector is not None and "sector" in enriched.columns:
            enriched = enriched[enriched["sector"].astype(str) == args.sector].copy()
    else:
        combined = pd.read_csv(args.combined_csv)
        enriched = enrich_combined_df_with_neighbor_lcoe(
            combined,
            radius_km=args.radius_km,
            same_country_only=args.same_country_only,
            sector=args.sector,
        )
    if args.min_ratio_median is not None:
        if "ratio_to_neighbor_median" not in enriched.columns:
            print("Enriched data missing column ratio_to_neighbor_median", file=sys.stderr)
            return 1
        enriched = enriched[enriched["ratio_to_neighbor_median"] >= args.min_ratio_median].copy()
    top = top_neighbor_lcoe_outliers(
        enriched,
        min_neighbors=args.min_neighbors,
        sort_by=args.sort_by,
        top_n=args.top_n,
    )
    want_ids = top["source_id"].astype(int).tolist()
    if not want_ids:
        print("No sites matched filters; not writing sites.csv", file=sys.stderr)
        return 1

    facilities = pd.read_csv(args.facility_master)
    miss = [c for c in FACILITY_COLS if c not in facilities.columns]
    if miss:
        print(f"Missing columns in facility master: {miss}", file=sys.stderr)
        return 1

    facilities = facilities[facilities["source_id"].isin(want_ids)].copy()
    found = set(facilities["source_id"].astype(int))
    missing = [sid for sid in want_ids if sid not in found]
    if missing:
        print(
            f"Warning: {len(missing)} top outlier source_id(s) missing from facility master "
            f"(first few: {missing[:8]})",
            file=sys.stderr,
        )
    order = {sid: i for i, sid in enumerate(want_ids)}
    facilities["_rank"] = facilities["source_id"].map(order)
    facilities = facilities.sort_values("_rank").drop(columns="_rank")

    facilities["potential_mw_solar_5"] = (
        facilities["grassland_km2_5"] + facilities["bare_sparse_km2_5"]
    ) * MW_per_KM2_solar
    facilities["potential_mw_solar_15"] = (
        facilities["grassland_km2_15"] + facilities["bare_sparse_km2_15"]
    ) * MW_per_KM2_solar

    out = facilities[FACILITY_COLS + ["potential_mw_solar_5", "potential_mw_solar_15"]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    extra = f" min_ratio_median>={args.min_ratio_median}" if args.min_ratio_median is not None else ""
    src = f"enriched={args.enriched_csv}" if args.enriched_csv else f"combined={args.combined_csv}"
    print(
        f"Wrote {len(out)} sites (top {args.top_n} by {args.sort_by},{extra} "
        f"{src} radius={args.radius_km} km min_neighbors={args.min_neighbors}) → {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
