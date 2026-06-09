"""
Build a sites CSV for flatblock by filtering the facility master to one or more ISO3 countries
(default: **USA**), with the same land-based solar potential columns as ``compile_sites.py``.

Example:

  python inputs/compile_us_sites.py -o inputs/sites_usa.csv
  python inputs/compile_us_sites.py -o inputs/sites_usa_mfg.csv --manufacturing-only
  python inputs/compile_us_sites.py -o inputs/sites_usa_iron_steel.csv --manufacturing-only --iron-and-steel-only
  python inputs/compile_us_sites.py -o inputs/sites_usa_top500.csv --top-n 500 --sort-key co2e_20yr \\
      --sector manufacturing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

MW_per_KM2_solar = 100

_FLATBLOCK = Path(__file__).resolve().parents[1]
_REPO_ROOT = _FLATBLOCK.parent

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
        help="Facility attributes (must include iso3_country, land buffers, …)",
    )
    p.add_argument(
        "--iso3",
        action="append",
        dest="iso3_list",
        metavar="CODE",
        default=None,
        help="ISO3 country code to include (repeat for multiple). Default: USA only.",
    )
    p.add_argument(
        "--sector",
        action="append",
        dest="sector_list",
        metavar="NAME",
        default=None,
        help="Keep only rows where ``sector`` matches (case-insensitive; repeat for multiple). "
        "Facility master uses values such as manufacturing, power, mineral_extraction.",
    )
    p.add_argument(
        "--manufacturing-only",
        action="store_true",
        help="Shortcut: keep only ``sector == manufacturing`` (equivalent to --sector manufacturing).",
    )
    p.add_argument(
        "--subsector",
        action="append",
        dest="subsector_list",
        metavar="NAME",
        default=None,
        help="Keep only rows where ``subsector`` matches (case-insensitive; repeat for multiple).",
    )
    p.add_argument(
        "--iron-and-steel-only",
        action="store_true",
        help="Shortcut: keep only ``subsector == iron-and-steel``.",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_FLATBLOCK / "inputs" / "sites_usa.csv",
        help="Output CSV path",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=None,
        metavar="N",
        help="After filtering, keep only the top N rows by --sort-key (e.g. 500)",
    )
    p.add_argument(
        "--sort-key",
        choices=("co2e_20yr", "source_id"),
        default="co2e_20yr",
        help="Ranking when --top-n is set (default: largest CO2e first)",
    )
    args = p.parse_args()

    iso_filter = args.iso3_list if args.iso3_list else ["USA"]

    if not args.facility_master.is_file():
        print(f"Facility master not found: {args.facility_master}", file=sys.stderr)
        return 1

    facilities = pd.read_csv(args.facility_master)
    miss = [c for c in FACILITY_COLS if c not in facilities.columns]
    if miss:
        print(f"Missing columns in facility master: {miss}", file=sys.stderr)
        return 1

    fac = facilities[facilities["iso3_country"].astype(str).isin(iso_filter)].copy()
    if fac.empty:
        print(f"No rows with iso3_country in {iso_filter}", file=sys.stderr)
        return 1

    sector_filters: list[str] = []
    if args.sector_list:
        sector_filters.extend(args.sector_list)
    if args.manufacturing_only:
        sector_filters.append("manufacturing")
    if sector_filters:
        sector_set = {s.strip().lower() for s in sector_filters if s and str(s).strip()}
        fac = fac[fac["sector"].astype(str).str.lower().isin(sector_set)].copy()
        if fac.empty:
            print(
                f"No rows left after sector filter {sorted(sector_set)} (iso3 in {iso_filter})",
                file=sys.stderr,
            )
            return 1

    subsector_filters: list[str] = []
    if args.subsector_list:
        subsector_filters.extend(args.subsector_list)
    if args.iron_and_steel_only:
        subsector_filters.append("iron-and-steel")
    if subsector_filters:
        subsector_set = {s.strip().lower() for s in subsector_filters if s and str(s).strip()}
        fac = fac[fac["subsector"].astype(str).str.lower().isin(subsector_set)].copy()
        if fac.empty:
            print(
                f"No rows left after subsector filter {sorted(subsector_set)} (iso3 in {iso_filter})",
                file=sys.stderr,
            )
            return 1

    if args.sort_key == "co2e_20yr" and "co2e_20yr" in fac.columns:
        fac["_rank"] = pd.to_numeric(fac["co2e_20yr"], errors="coerce").fillna(0.0)
    else:
        fac["_rank"] = pd.to_numeric(fac["source_id"], errors="coerce").fillna(0)
    fac = fac.sort_values("_rank", ascending=False).drop(columns="_rank")

    if args.top_n is not None:
        fac = fac.head(int(args.top_n))

    fac["potential_mw_solar_5"] = (
        fac["grassland_km2_5"] + fac["bare_sparse_km2_5"]
    ) * MW_per_KM2_solar
    fac["potential_mw_solar_15"] = (
        fac["grassland_km2_15"] + fac["bare_sparse_km2_15"]
    ) * MW_per_KM2_solar

    out = fac[FACILITY_COLS + ["potential_mw_solar_5", "potential_mw_solar_15"]].copy()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)

    sector_note = ""
    if sector_filters:
        sector_note = f", sector in {sorted({s.strip().lower() for s in sector_filters})}"
    subsector_note = ""
    if subsector_filters:
        subsector_note = f", subsector in {sorted({s.strip().lower() for s in subsector_filters})}"
    print(
        f"Wrote {len(out)} sites (iso3 in {iso_filter}{sector_note}{subsector_note}"
        + (f", top {args.top_n} by {args.sort_key}" if args.top_n else "")
        + f") → {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
