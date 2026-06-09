#!/usr/bin/env python3
"""
Generate sector maps (Power, Power-coal only, Manufacturing, Mineral extraction) from
optimization results and save as HTML files. Color = total LCOE ($/MWh): green = low, red = high.
For the coal-only map, sites must have source_type containing "coal" (e.g. from sites CSV
or include source_type in --combined-csv).

Usage:
  From flatblock_optimization/scenarios/ with results in ./all_sites_v1/:
    python flatblock_optimization/utils/maps/map_results_by_sector.py --scenario all_sites_v1 --sites-csv ../inputs/sites_sample_countries.csv

  Using a pre-aggregated combined CSV (e.g. exported from the notebook):
    python flatblock_optimization/utils/maps/map_results_by_sector.py --combined-csv flatblock_optimization/outputs/combined_results_all_sites_v1.csv

  Defaults: --scenario all_sites_v1, --scenarios-dir flatblock_optimization/scenarios,
            --sites-csv inputs/sites_sample_countries.csv,
            -o <repo>/data_visualization/maps_flatblock_lcoe (absolute LCOE / flatblock results).
  Comparison maps (solar+battery vs BNEF): map_ccgt_vs_solar_storage.py →
  data_visualization/maps_ccgt_vs_solar_battery; map_coal_vs_solar_storage.py →
  data_visualization/maps_coal_vs_solar_battery (needs Coal_BNEF_LCOE_* in facility_master).
  EU wholesale vs solar+battery: map_eu_wholesale_vs_solar_battery.py →
  data_visualization/maps_eu_wholesale_vs_solar_battery (uses raw_data monthly EUR/MWh prices).
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional

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
    format_capacity_short,
    load_combined_or_aggregate,
    repo_root,
    resolve_scenarios_dir_and_sites,
)

DEFAULT_FLATBLOCK_MAPS_DIR = repo_root() / "data_visualization" / "maps_flatblock_lcoe"

# Column used for marker color (green=low, red=high). Use total LCOE, not solar or battery alone.
COLUMN_LCOE_FOR_COLOR = "LCOE_total_$perMWh"
# Same continuous colormap as map_ccgt_vs_solar_storage.py (RdYlGn_r).
LCOE_CMAP = plt.cm.RdYlGn_r


def make_sector_map(
    df: pd.DataFrame,
    sector_value: str,
    extra_filters: Optional[Dict[str, str]] = None,
):
    """Build a folium map for the given sector. Returns (map, n_facilities, vmin, vmax).

    extra_filters: optional dict, e.g. {"source_type_contains": "coal"} to restrict
    to facilities whose source_type (str) contains the given substring (case-insensitive).
    """
    map_df = df.dropna(subset=["lat", "lon"]).copy()
    if "sector" in map_df.columns and sector_value is not None:
        map_df = map_df[
            map_df["sector"].astype(str).str.strip().str.lower() == sector_value.lower()
        ]
    if extra_filters and "source_type_contains" in extra_filters:
        needle = extra_filters["source_type_contains"].strip().lower()
        if "source_type" in map_df.columns and needle:
            mask = map_df["source_type"].astype(str).str.lower().str.contains(needle, na=False)
            map_df = map_df.loc[mask]
        else:
            map_df = map_df.iloc[0:0]  # no source_type column or empty needle -> no rows
    if map_df.empty:
        m = folium.Map(location=[20, 0], zoom_start=2, tiles="CartoDB positron")
        return m, 0, None, None

    lcoe = map_df[COLUMN_LCOE_FOR_COLOR]
    # Use 2nd and 98th percentiles as color scale bounds so a few extreme outliers
    # don't compress the rest into a narrow band (all same color).
    vmin, vmax = float(lcoe.quantile(0.02)), float(lcoe.quantile(0.98))
    map_df = map_df.copy()
    # Linear 0–1: low LCOE → green, high LCOE → red (same mapping as CCGT map’s diff_norm).
    lcoe_norm = lcoe.clip(vmin, vmax).sub(vmin).div(max(vmax - vmin, 1e-6))
    cmap = LCOE_CMAP
    map_df["color"] = lcoe_norm.map(lambda n: mcolors.to_hex(cmap(float(n))))

    center_lat = map_df["lat"].median()
    center_lon = map_df["lon"].median()
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=2,
        tiles="CartoDB positron",
    )
    # Add each facility as its own circle (no MarkerCluster) so dot color = LCOE, not count.
    for _, row in map_df.iterrows():
        cap = row.get("capacity", None)
        units = row.get("capacity_units", "")
        short_val = format_capacity_short(cap)
        if short_val != "—" and units and pd.notna(units) and str(units).strip():
            cap_str = f"{short_val} {str(units).strip()}"
        else:
            cap_str = short_val
        popup = (
            f"<b>Site {int(row['site'])}</b> ({row.get('iso3_country', '')})<br>"
            f"Capacity: {cap_str}<br>"
            f"LCOE total: ${row['LCOE_total_$perMWh']:.1f}/MWh<br>"
            f"LCOE solar: ${row['LCOE_solar_$perMWh']:.1f} | batt: ${row['LCOE_batt_$perMWh']:.1f}<br>"
            f"Reliability: {row['Reliability_%']:.1f}%"
        )
        m.add_child(
            folium.CircleMarker(
                location=[row["lat"], row["lon"]],
                radius=5,
                color=row["color"],
                fill=True,
                fillColor=row["color"],
                fillOpacity=0.75,
                popup=folium.Popup(popup, max_width=280),
            )
        )

    # Legend: continuous gradient like map_ccgt_vs_solar_storage.py (25 × RdYlGn_r segments).
    ticks = [
        vmin,
        vmin + 0.25 * (vmax - vmin),
        vmin + 0.5 * (vmax - vmin),
        vmin + 0.75 * (vmax - vmin),
        vmax,
    ]
    cmap = LCOE_CMAP
    segments = "".join(
        f'<div style="flex:1; background:{mcolors.to_hex(cmap(i / 24))}; min-height:14px; border:1px solid #333;"></div>'
        for i in range(25)
    )
    labels = "".join(
        f'<div style="flex:1; font-size:10px; text-align:center;">{v:.0f}</div>' for v in ticks
    )
    legend_html = (
        '<div style="position:fixed; bottom:20px; left:10px; z-index:9999; '
        'background:white; padding:10px; border:1px solid #888; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,0.2);">'
        '<b style="display:block; margin-bottom:6px;">LCOE total ($/MWh)</b>'
        '<div style="display:flex; width:220px; margin-bottom:4px;">' + segments + "</div>"
        '<div style="display:flex; width:220px;">' + labels + "</div>"
        '<div style="font-size:9px; color:#666; margin-top:4px;">Green: low LCOE | Red: high LCOE</div>'
        '<div style="font-size:9px; color:#666; margin-top:4px;">Scale: 2nd–98th percentile of site LCOE</div>'
        '<div style="font-size:9px; color:#666; margin-top:4px;"><b>Capacity</b> (in popup): short form + units<br>e.g. 86M MW, 1.2k W</div>'
        "</div>"
    )
    m.get_root().html.add_child(folium.Element(legend_html))
    return m, len(map_df), vmin, vmax


SECTOR_MAPS = [
    ("power", "Power"),
    ("power_coal", "Power (coal only)", {"source_type_contains": "coal"}),
    ("manufacturing", "Manufacturing"),
    ("mineral_extraction", "Mineral extraction"),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save sector maps (Power, Manufacturing, Mineral extraction) as HTML. Color = LCOE."
    )
    parser.add_argument(
        "--scenario",
        default="all_sites_v1",
        help="Scenario name (e.g. all_sites_v1). Ignored if --combined-csv is set.",
    )
    parser.add_argument(
        "--scenarios-dir",
        type=Path,
        default=None,
        help="Path to scenarios directory (default: flatblock_optimization/scenarios).",
    )
    parser.add_argument(
        "--sites-csv",
        type=Path,
        default=None,
        help="Path to sites CSV with source_id, iso3_country, lat, lon, sector. Default: ../inputs/sites_sample_countries.csv",
    )
    parser.add_argument(
        "--combined-csv",
        type=Path,
        default=None,
        help="If set, load pre-aggregated results from this CSV (must include sector, lat, lon, LCOE columns).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_FLATBLOCK_MAPS_DIR,
        help=(
            "Directory to write map_<sector>.html files "
            f"(default: {DEFAULT_FLATBLOCK_MAPS_DIR})."
        ),
    )
    args = parser.parse_args()

    args.scenarios_dir, args.sites_csv = resolve_scenarios_dir_and_sites(
        args.scenarios_dir, args.sites_csv
    )

    combined_required = ["lat", "lon", "LCOE_total_$perMWh", "Reliability_%"]
    combined_df = load_combined_or_aggregate(
        combined_csv=args.combined_csv,
        scenario=args.scenario,
        scenarios_dir=args.scenarios_dir,
        sites_csv=args.sites_csv,
        combined_required_cols=combined_required,
    )
    if args.combined_csv is not None:
        if "sector" not in combined_df.columns:
            combined_df["sector"] = None
        if "source_type" not in combined_df.columns:
            combined_df["source_type"] = None

    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for item in SECTOR_MAPS:
        sector_key = item[0]
        title = item[1]
        extra_filters = item[2] if len(item) > 2 else None
        m, n, vmin, vmax = make_sector_map(combined_df, sector_key, extra_filters=extra_filters)
        out_name = f"map_{sector_key}.html"
        out_path = args.output_dir / out_name
        m.save(str(out_path))
        print(f"Saved {title} ({n} facilities): {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
