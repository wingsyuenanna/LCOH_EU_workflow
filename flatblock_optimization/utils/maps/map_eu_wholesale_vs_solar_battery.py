#!/usr/bin/env python3
"""
Create a facility map colored by LCOE difference between:
  - Solar + storage optimization result (LCOE_total_$perMWh, USD/MWh), and
  - Country-level European wholesale electricity price from monthly ENTSO-E-style data (EUR/MWh),
    converted to USD/MWh for comparison.

Difference (same sign convention as map_ccgt_vs_solar_storage.py):
    lcoe_diff = LCOE_total_$perMWh - wholesale_usd_per_mwh

  - Negative (green): solar+battery is cheaper than the wholesale benchmark
  - Positive (red):   solar+battery is more expensive than the wholesale benchmark

Only sites whose ``iso3_country`` appears in the wholesale CSV are shown (European markets
in ``raw_data/european_wholesale_electricity_price_data_monthly.csv``: EU members plus
e.g. Norway, Switzerland where present).

Usage:
  python flatblock_optimization/utils/maps/map_eu_wholesale_vs_solar_battery.py \\
    --combined-csv flatblock_optimization/outputs/combined_results_all_sites_v1.csv \\
    --facility-master-csv views/facility_master_v6.csv

  # Use median wholesale over the last 24 months in the file; set FX explicitly:
  python flatblock_optimization/utils/maps/map_eu_wholesale_vs_solar_battery.py \\
    --combined-csv flatblock_optimization/outputs/combined_results_all_sites_v1.csv \\
    --recent-months 24 --usd-per-eur 1.08

  # Sensitivity-style benchmarks (per-country over the time window); filename includes the stat:
  python flatblock_optimization/utils/maps/map_eu_wholesale_vs_solar_battery.py \\
    --combined-csv flatblock_optimization/outputs/combined_results_all_sites_v1.csv \\
    --price-stat p10

  # One HTML per sector (power, manufacturing, mineral_extraction):
  python flatblock_optimization/utils/maps/map_eu_wholesale_vs_solar_battery.py \\
    --combined-csv flatblock_optimization/outputs/combined_results_all_sites_v1.csv \\
    --all-sectors
"""

from __future__ import annotations

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

DEFAULT_EU_WHOLESALE_MAPS_DIR = repo_root() / "data_visualization" / "maps_eu_wholesale_vs_solar_battery"
DEFAULT_MONTHLY_WHOLESALE = repo_root() / "raw_data" / "european_wholesale_electricity_price_data_monthly.csv"
PRICE_COL_EUR = "Price (EUR/MWhe)"
ISO_COL = "ISO3 Code"

# Monthly prices per country → one scalar benchmark per ISO3 (for argparse choices).
WHOLESALE_PRICE_STATS: tuple[str, ...] = (
    "mean",
    "median",
    "min",
    "max",
    "p01",
    "p02",
    "p05",
    "p10",
    "p25",
    "p50",
    "p75",
    "p90",
    "p95",
    "p99",
)


def normalize_price_stat(stat: str) -> str:
    """Lowercase; ``p5`` / ``p05`` → ``p05`` for stable filenames."""
    s = stat.strip().lower()
    if len(s) >= 2 and s[0] == "p" and s[1:].isdigit():
        n = int(s[1:])
        if 1 <= n <= 99:
            return f"p{n:02d}"
    return s


def parse_price_stat_arg(value: str) -> str:
    """Argparse ``type=`` for ``--price-stat`` (accepts e.g. ``p5`` → ``p05``)."""
    n = normalize_price_stat(value)
    if n not in WHOLESALE_PRICE_STATS:
        opts = ", ".join(WHOLESALE_PRICE_STATS)
        raise argparse.ArgumentTypeError(
            f"invalid wholesale stat {value!r}; expected one of: {opts} (e.g. mean, median, p10, p90)"
        )
    return n


def load_wholesale_eur_by_iso3(
    monthly_csv: Path,
    *,
    recent_months: int | None,
    stat: str,
) -> pd.DataFrame:
    """
    Return columns: iso3_country, wholesale_eur_mwh.
    If ``recent_months`` is set, restrict to rows with Date >= (max Date - N months).
    ``stat``: mean, median, min, max, or p01..p99 (percentile of monthly values per ISO3).
    """
    if not monthly_csv.is_file():
        raise FileNotFoundError(f"Wholesale monthly CSV not found: {monthly_csv}")
    df = pd.read_csv(monthly_csv, parse_dates=["Date"])
    if ISO_COL not in df.columns or PRICE_COL_EUR not in df.columns:
        raise ValueError(f"{monthly_csv} must contain '{ISO_COL}' and '{PRICE_COL_EUR}'")
    df["price_eur"] = pd.to_numeric(df[PRICE_COL_EUR], errors="coerce")
    df = df.dropna(subset=["price_eur", ISO_COL])
    if recent_months is not None:
        end = df["Date"].max()
        start = end - pd.DateOffset(months=int(recent_months))
        df = df[df["Date"] >= start]
    g = df.groupby(ISO_COL, sort=True)["price_eur"]
    st = normalize_price_stat(stat)
    if st == "mean":
        vals = g.mean()
    elif st in ("median", "p50"):
        vals = g.median()
    elif st == "min":
        vals = g.min()
    elif st == "max":
        vals = g.max()
    elif len(st) == 3 and st[0] == "p" and st[1:].isdigit():
        q = int(st[1:]) / 100.0
        if not 0 < q < 1:
            raise ValueError(f"Percentile must be between p01 and p99, got {stat!r}")
        vals = g.quantile(q)
    else:
        raise ValueError(f"Unknown wholesale stat {stat!r}")
    out = vals.rename("wholesale_eur_mwh").reset_index()
    out = out.rename(columns={ISO_COL: "iso3_country"})
    return out


def wholesale_stat_human(st: str) -> str:
    """Short description for map legend / popups."""
    n = normalize_price_stat(st)
    if n.startswith("p") and len(n) == 3 and n[1:].isdigit():
        return f"{n} ({int(n[1:])}th %ile of monthly EUR/MWh)"
    if n == "mean":
        return "mean monthly EUR/MWh"
    if n in ("median", "p50"):
        return "median monthly EUR/MWh"
    if n == "min":
        return "min monthly EUR/MWh"
    if n == "max":
        return "max monthly EUR/MWh"
    return n


def ensure_iso3_country(combined_df: pd.DataFrame, facility_master_csv: Path) -> pd.DataFrame:
    """Attach ``iso3_country`` from facility master when missing or null."""
    if "source_id" not in combined_df.columns:
        combined_df = combined_df.copy()
        combined_df["source_id"] = combined_df.get("site")
    need = "iso3_country" not in combined_df.columns or combined_df["iso3_country"].isna().any()
    if not need:
        return combined_df
    if not facility_master_csv.is_file():
        raise FileNotFoundError(f"facility master not found: {facility_master_csv}")
    fm = pd.read_csv(facility_master_csv, usecols=["source_id", "iso3_country"])
    out = combined_df.merge(fm, on="source_id", how="left", suffixes=("", "_fm"))
    if "iso3_country_fm" in out.columns:
        out["iso3_country"] = out["iso3_country"].fillna(out["iso3_country_fm"])
        out = out.drop(columns=["iso3_country_fm"])
    return out


def build_map(df: pd.DataFrame, *, usd_per_eur: float, price_label: str):
    """
    Build map and return:
      (map_obj, n_facilities, vbound, cheaper_count, pricier_count)
    """
    map_df = df.dropna(
        subset=["lat", "lon", "LCOE_total_$perMWh", "wholesale_usd_per_mwh"]
    ).copy()
    if map_df.empty:
        m = folium.Map(location=[54.5, 15.0], zoom_start=4, tiles="CartoDB positron")
        return m, 0, None, 0, 0

    map_df["lcoe_diff"] = map_df["LCOE_total_$perMWh"] - map_df["wholesale_usd_per_mwh"]
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
    m = folium.Map(location=[center_lat, center_lon], zoom_start=4, tiles="CartoDB positron")

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
            f"Wholesale ({price_label}): "
            f"€{row['wholesale_eur_mwh']:.1f}/MWh → ${row['wholesale_usd_per_mwh']:.1f}/MWh "
            f"(USD/EUR={usd_per_eur:.4f})<br>"
            f"<b>Diff (solar+batt − wholesale): {sign}${row['lcoe_diff']:.1f}/MWh</b>"
        )
        m.add_child(
            folium.CircleMarker(
                location=[row["lat"], row["lon"]],
                radius=5,
                color=row["color"],
                fill=True,
                fillColor=row["color"],
                fillOpacity=0.75,
                popup=folium.Popup(popup, max_width=340),
            )
        )

    ticks = [vmin, vmin / 2, 0.0, vmax / 2, vmax]
    segments = "".join(
        f'<div style="flex:1; background:{mcolors.to_hex(cmap(i/24))}; min-height:14px; border:1px solid #333;"></div>'
        for i in range(25)
    )
    labels = "".join(
        f'<div style="flex:1; font-size:10px; text-align:center;">{v:+.0f}</div>' for v in ticks
    )
    legend_html = (
        '<div style="position:fixed; bottom:20px; left:10px; z-index:9999; '
        'background:white; padding:10px; border:1px solid #888; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,0.2);">'
        f'<b style="display:block; margin-bottom:6px;">Diff ($/MWh): Solar+batt − wholesale ({price_label})</b>'
        '<div style="display:flex; width:220px; margin-bottom:4px;">' + segments + "</div>"
        '<div style="display:flex; width:220px;">' + labels + "</div>"
        '<div style="font-size:9px; color:#666; margin-top:4px;">Green: solar+batt cheaper | Red: wholesale cheaper</div>'
        '<div style="font-size:9px; color:#666; margin-top:4px;">Wholesale EUR→USD via --usd-per-eur; scale clipped to 98th %ile |diff|</div>'
        "</div>"
    )
    m.get_root().html.add_child(folium.Element(legend_html))
    return m, len(map_df), vbound, cheaper_count, pricier_count


def prepare_df(args) -> tuple[pd.DataFrame, str]:
    args.scenarios_dir, args.sites_csv = resolve_scenarios_dir_and_sites(
        args.scenarios_dir, args.sites_csv
    )
    if args.facility_master_csv is None:
        args.facility_master_csv = default_facility_master()

    combined_df = load_combined_or_aggregate(
        combined_csv=args.combined_csv,
        scenario=args.scenario,
        scenarios_dir=args.scenarios_dir,
        sites_csv=args.sites_csv,
        combined_required_cols=["site", "lat", "lon", "LCOE_total_$perMWh"],
    )
    if "source_id" not in combined_df.columns:
        combined_df["source_id"] = combined_df.get("site")

    combined_df = ensure_iso3_country(combined_df, args.facility_master_csv)

    wholesale = load_wholesale_eur_by_iso3(
        args.wholesale_monthly_csv,
        recent_months=args.recent_months,
        stat=args.price_stat,
    )
    iso_with_price = set(wholesale["iso3_country"].astype(str))

    combined_df = combined_df.merge(wholesale, on="iso3_country", how="inner")
    combined_df["wholesale_usd_per_mwh"] = combined_df["wholesale_eur_mwh"] * float(args.usd_per_eur)

    if args.sector and args.sector.lower() != "all" and "sector" in combined_df.columns:
        combined_df = combined_df[
            combined_df["sector"].astype(str).str.strip().str.lower() == args.sector.lower()
        ]

    n_iso = len(iso_with_price)
    h = wholesale_stat_human(args.price_stat)
    if args.recent_months is None:
        price_label = f"{h}, all history, {n_iso} markets"
    else:
        price_label = f"{h}, last {args.recent_months} mo, {n_iso} markets"
    return combined_df, price_label


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Map solar+battery LCOE minus European wholesale prices (EUR converted to USD)."
    )
    parser.add_argument("--scenario", default="all_sites_v1", help="Scenario name (ignored if --combined-csv is set).")
    parser.add_argument("--scenarios-dir", type=Path, default=None)
    parser.add_argument("--sites-csv", type=Path, default=None)
    parser.add_argument("--combined-csv", type=Path, default=None)
    parser.add_argument("--facility-master-csv", type=Path, default=None)
    parser.add_argument(
        "--wholesale-monthly-csv",
        type=Path,
        default=DEFAULT_MONTHLY_WHOLESALE,
        help="Monthly wholesale prices (Country, ISO3, Date, EUR/MWh).",
    )
    parser.add_argument(
        "--recent-months",
        type=int,
        default=None,
        metavar="N",
        help="If set, use only the last N months of data (from max date); default uses full series.",
    )
    parser.add_argument(
        "--price-stat",
        dest="price_stat",
        type=parse_price_stat_arg,
        default=parse_price_stat_arg("median"),
        metavar="STAT",
        help=(
            "Wholesale benchmark per country: mean, median, min, max, or p01..p99 "
            "(percentile of monthly EUR/MWh in the window). Shorthand p5 → p05. "
            "Included in the output HTML filename."
        ),
    )
    parser.add_argument(
        "--usd-per-eur",
        type=float,
        default=1.09,
        help="Multiply EUR/MWh wholesale by this to compare to USD/MWh LCOE (override for live FX).",
    )
    parser.add_argument("--sector", default="power", help="Sector filter or 'all'.")
    parser.add_argument(
        "--all-sectors",
        action="store_true",
        help="Write three maps: power, manufacturing, mineral_extraction.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_EU_WHOLESALE_MAPS_DIR,
        help=f"Output directory for HTML (default: {DEFAULT_EU_WHOLESALE_MAPS_DIR}).",
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
        combined_df, price_label = prepare_df(args)
        m, n, vbound, cheaper_count, pricier_count = build_map(
            combined_df, usd_per_eur=args.usd_per_eur, price_label=price_label
        )

        slug = args.price_stat
        if len(sector_list) == 1 and sec.lower() == "power":
            fname = f"map_eu_wholesale_vs_solar_battery_diff_{slug}.html"
        else:
            fname = f"map_eu_wholesale_vs_solar_battery_diff_{slug}_{sec}.html"
        out_path = args.output_dir / fname
        m.save(str(out_path))

        print(f"Saved map ({n} facilities in wholesale-covered countries): {out_path}")
        if vbound is not None:
            print(f"  Diff color bound (98th %ile |diff|): +/-{vbound:.1f} $/MWh (USD)")
        print(f"  Solar+battery cheaper than wholesale benchmark: {cheaper_count}")
        print(f"  Solar+battery more expensive than wholesale benchmark: {pricier_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
