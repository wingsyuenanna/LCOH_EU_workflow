#!/usr/bin/env python3
"""
Create a facility map colored by LCOE difference between:
  - Solar + storage optimization result (LCOE_total_$perMWh), and
  - Country-level Coal BNEF LCOE from facility_master (requires
    Coal_BNEF_LCOE_{year}_$/MWh from master_file/compile_master_file.py).

Difference:
    lcoe_diff = LCOE_total_$perMWh - Coal_BNEF_LCOE_<year>_$/MWh

Interpretation:
  - Negative (green): solar+storage is cheaper than BNEF coal
  - Positive (red):   solar+storage is more expensive than BNEF coal

Coal LCOE is a country-level benchmark (same value for every site in that country). You can
compare solar+battery LCOE for any sector (power, manufacturing, mineral_extraction) to
that benchmark — the facility need not be a coal plant.

Source_type filter (coal plants only): **on by default only for --sector power**. For
manufacturing / mineral_extraction it is off by default. Use --no-coal-sites-only to
include all power-sector sites; use --coal-sites-only to force coal-plant filter for any sector.

Usage:
  python flatblock_optimization/utils/maps/map_coal_vs_solar_storage.py \\
    --combined-csv flatblock_optimization/outputs/combined_results_all_sites_v1.csv \\
    --facility-master-csv views/facility_master_v6.csv \\
    --year 2025

  # Manufacturing or mineral extraction (no coal source_type filter by default):
  python flatblock_optimization/utils/maps/map_coal_vs_solar_storage.py ... --sector manufacturing
  python flatblock_optimization/utils/maps/map_coal_vs_solar_storage.py ... --sector mineral_extraction

  # One HTML per sector (power, manufacturing, mineral_extraction):
  python flatblock_optimization/utils/maps/map_coal_vs_solar_storage.py ... --all-sectors
"""

import argparse
import sys
from pathlib import Path

_FLATBLOCK = Path(__file__).resolve().parents[2]
if str(_FLATBLOCK) not in sys.path:
    sys.path.insert(0, str(_FLATBLOCK))

import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import folium

from utils.maps.map_common import (
    default_facility_master,
    format_capacity_short,
    load_combined_or_aggregate,
    repo_root,
    resolve_scenarios_dir_and_sites,
)

DEFAULT_COAL_MAPS_DIR = repo_root() / "data_visualization" / "maps_coal_vs_solar_battery"


def build_map(df: pd.DataFrame, year: int):
    """Return (map_obj, n_facilities, vbound, cheaper_count, pricier_count)."""
    map_df = df.dropna(subset=["lat", "lon", "LCOE_total_$perMWh", "Coal_lcoe"]).copy()
    if map_df.empty:
        m = folium.Map(location=[20, 0], zoom_start=2, tiles="CartoDB positron")
        return m, 0, None, 0, 0

    map_df["lcoe_diff"] = map_df["LCOE_total_$perMWh"] - map_df["Coal_lcoe"]
    cheaper_count = int((map_df["lcoe_diff"] < 0).sum())
    pricier_count = int((map_df["lcoe_diff"] > 0).sum())

    vbound = float(map_df["lcoe_diff"].abs().quantile(0.98))
    if vbound <= 0 or pd.isna(vbound):
        vbound = 1.0
    vmin, vmax = -vbound, vbound

    cmap = plt.cm.RdYlGn_r
    map_df["diff_norm"] = map_df["lcoe_diff"].clip(vmin, vmax).sub(vmin).div(vmax - vmin)
    map_df["color"] = map_df["diff_norm"].map(lambda n: mcolors.to_hex(cmap(float(n))))

    center_lat = map_df["lat"].median()
    center_lon = map_df["lon"].median()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=2, tiles="CartoDB positron")

    for _, row in map_df.iterrows():
        cap = row.get("capacity", None)
        units = row.get("capacity_units", "")
        short_val = format_capacity_short(cap)
        if short_val != "—" and units and pd.notna(units) and str(units).strip():
            cap_str = f"{short_val} {str(units).strip()}"
        else:
            cap_str = short_val

        sign = "+" if row["lcoe_diff"] >= 0 else ""
        popup = (
            f"<b>Site {int(row['site'])}</b> ({row.get('iso3_country', '')})<br>"
            f"Source type: {row.get('source_type', '—')}<br>"
            f"Capacity: {cap_str}<br>"
            f"Solar+battery LCOE: ${row['LCOE_total_$perMWh']:.1f}/MWh<br>"
            f"Coal BNEF LCOE ({year}): ${row['Coal_lcoe']:.1f}/MWh<br>"
            f"<b>Diff (solar+batt - coal): {sign}${row['lcoe_diff']:.1f}/MWh</b>"
        )
        m.add_child(
            folium.CircleMarker(
                location=[row["lat"], row["lon"]],
                radius=5,
                color=row["color"],
                fill=True,
                fillColor=row["color"],
                fillOpacity=0.75,
                popup=folium.Popup(popup, max_width=320),
            )
        )

    ticks = [vmin, vmin / 2, 0.0, vmax / 2, vmax]
    segments = "".join(
        f'<div style="flex:1; background:{mcolors.to_hex(cmap(i / 24))}; min-height:14px; border:1px solid #333;"></div>'
        for i in range(25)
    )
    labels = "".join(f'<div style="flex:1; font-size:10px; text-align:center;">{v:+.0f}</div>' for v in ticks)
    legend_html = (
        '<div style="position:fixed; bottom:20px; left:10px; z-index:9999; '
        'background:white; padding:10px; border:1px solid #888; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,0.2);">'
        f'<b style="display:block; margin-bottom:6px;">Diff ($/MWh): Solar+batt - Coal BNEF ({year})</b>'
        '<div style="display:flex; width:220px; margin-bottom:4px;">' + segments + "</div>"
        '<div style="display:flex; width:220px;">' + labels + "</div>"
        '<div style="font-size:9px; color:#666; margin-top:4px;">Green: solar+batt cheaper | Red: coal cheaper</div>'
        '<div style="font-size:9px; color:#666; margin-top:4px;">Scale clipped to 98th percentile of |diff|</div>'
        "</div>"
    )
    m.get_root().html.add_child(folium.Element(legend_html))
    return m, len(map_df), vbound, cheaper_count, pricier_count


def compute_coal_sites_only_filter(args, sector: str) -> bool:
    """
    Restrict map to plants whose source_type contains 'coal' (coal-fired generators).

    Default: True only for sector power (typical coal comparison map). For manufacturing /
    mineral_extraction the default is False so all sites in that sector are shown.
    """
    if getattr(args, "no_coal_sites_only", False):
        return False
    if getattr(args, "coal_sites_only", False):
        return True
    return sector.strip().lower() == "power"


def prepare_combined_df(args) -> pd.DataFrame:
    args.scenarios_dir, args.sites_csv = resolve_scenarios_dir_and_sites(
        args.scenarios_dir, args.sites_csv
    )
    if args.facility_master_csv is None:
        args.facility_master_csv = default_facility_master()

    col_coal = f"Coal_BNEF_LCOE_{args.year}_$/MWh"

    combined_df = load_combined_or_aggregate(
        combined_csv=args.combined_csv,
        scenario=args.scenario,
        scenarios_dir=args.scenarios_dir,
        sites_csv=args.sites_csv,
        combined_required_cols=["site", "lat", "lon", "LCOE_total_$perMWh"],
    )
    if "source_id" not in combined_df.columns:
        combined_df["source_id"] = combined_df.get("site")

    if not args.facility_master_csv.exists():
        print(f"Error: --facility-master-csv not found: {args.facility_master_csv}", file=sys.stderr)
        sys.exit(1)

    fm = pd.read_csv(args.facility_master_csv, usecols=["source_id", col_coal])
    fm = fm.rename(columns={col_coal: "Coal_lcoe"})
    combined_df = combined_df.merge(fm, on="source_id", how="left")

    sector = args.sector
    if sector and sector.lower() != "all" and "sector" in combined_df.columns:
        combined_df = combined_df[
            combined_df["sector"].astype(str).str.strip().str.lower() == sector.lower()
        ]

    if compute_coal_sites_only_filter(args, sector or "") and "source_type" in combined_df.columns:
        st = combined_df["source_type"].astype(str).str.lower()
        combined_df = combined_df.loc[st.str.contains("coal", na=False)].copy()

    return combined_df


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Map difference between optimized solar+battery LCOE and BNEF coal LCOE."
    )
    parser.add_argument("--scenario", default="all_sites_v1", help="Scenario (ignored if --combined-csv).")
    parser.add_argument(
        "--scenarios-dir",
        type=Path,
        default=None,
        help="Scenarios root (default: flatblock_optimization/scenarios).",
    )
    parser.add_argument(
        "--sites-csv",
        type=Path,
        default=None,
        help="Sites CSV for scenario mode (default: inputs/sites_sample_countries.csv).",
    )
    parser.add_argument("--combined-csv", type=Path, default=None)
    parser.add_argument("--facility-master-csv", type=Path, default=None)
    parser.add_argument("--year", type=int, default=2025, choices=[2025, 2030])
    parser.add_argument(
        "--sector",
        default="power",
        help="Sector filter: power, manufacturing, mineral_extraction, or all (default: power).",
    )
    parser.add_argument(
        "--all-sectors",
        action="store_true",
        help="Write three maps: power, manufacturing, mineral_extraction (filenames include sector).",
    )
    parser.add_argument(
        "--no-coal-sites-only",
        action="store_true",
        help="For power sector: include all power sites, not only coal plants (source_type).",
    )
    parser.add_argument(
        "--coal-sites-only",
        action="store_true",
        help="Restrict to source_type containing coal (any sector). Overrides default for non-power.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_COAL_MAPS_DIR,
        help=f"Output directory (default: {DEFAULT_COAL_MAPS_DIR}).",
    )
    args = parser.parse_args()

    if args.all_sectors:
        sector_list = ["power", "manufacturing", "mineral_extraction"]
    else:
        sector_list = [args.sector]

    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for sec in sector_list:
        args.sector = sec
        combined_df = prepare_combined_df(args)
        m, n, vbound, cheaper_count, pricier_count = build_map(combined_df, args.year)

        if len(sector_list) == 1 and sec.lower() == "power":
            fname = f"map_coal_vs_solar_battery_diff_{args.year}.html"
        else:
            fname = f"map_coal_vs_solar_battery_diff_{args.year}_{sec}.html"
        out_path = args.output_dir / fname
        m.save(str(out_path))

        print(f"Saved map ({n} facilities): {out_path}")
        if vbound is not None:
            print(f"  Diff color bound (98th %ile |diff|): +/-{vbound:.1f} $/MWh")
        print(f"  Solar+battery cheaper than coal: {cheaper_count}")
        print(f"  Solar+battery more expensive than coal: {pricier_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
