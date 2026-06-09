#!/usr/bin/env python3
"""
Build facility Folium maps (grid + off-grid) and export gas→off-grid flip samples.

Cohort: COMPUTED, LCP-matched (spatial_nearest | id_direct), exclude E-PRTR 1(c).
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import folium
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
INPUT_MATCH = REPO / "inputs" / "eprtr_lcp_matched.csv"
GRID_CSV = REPO / "grid_electricity_run" / "outputs" / "lcoh_results.csv"
OFF_CSV = REPO / "off_grid_electricity_run" / "outputs" / "lcoh_results.csv"
OUT_REPO = REPO / "outputs"
OUT_GRID = REPO / "grid_electricity_run" / "outputs" / "maps"
OUT_OFF = REPO / "off_grid_electricity_run" / "outputs" / "maps"

PATHWAY_COLORS = {
    "natural_gas": "#4C72B0",
    "heat_pump": "#55A868",
    "heat_battery": "#C44E52",
}
PATHWAY_LABELS = {
    "natural_gas": "Natural gas",
    "heat_pump": "Heat pump",
    "heat_battery": "Heat battery",
}
LCP_MATCHED = {"spatial_nearest", "id_direct"}


def load_cohort(csv_path: Path) -> pd.DataFrame:
    match_meta = pd.read_csv(INPUT_MATCH, usecols=["eprtr_facility_id", "match_method"])
    raw = pd.read_csv(csv_path).merge(match_meta, on="eprtr_facility_id", how="left")
    is_computed = raw["verification_status"] == "COMPUTED"
    is_lcp = raw["match_method"].isin(LCP_MATCHED)
    is_not_1c = ~raw["eprtr_activity"].astype(str).str.match(r"^1\(c\)", na=False)
    return raw[is_computed & is_lcp & is_not_1c].copy()


def marker_radius(mwh_th: float) -> float:
    if pd.isna(mwh_th) or mwh_th <= 0:
        return 4.0
    return min(18.0, max(4.0, 3.0 * math.log10(mwh_th) - 5))


def fmt(val, decimals: int = 1, suffix: str = "") -> str:
    return "—" if pd.isna(val) else f"{val:,.{decimals}f}{suffix}"


def build_popup_grid(row: pd.Series) -> str:
    lc = row.get("least_cost_pathway", "")
    lc_lbl = PATHWAY_LABELS.get(lc, lc)
    lc_clr = PATHWAY_COLORS.get(lc, "#888")
    gas_l = fmt(row["lcoh_natural_gas_EUR_MWhth"])
    hb_l = fmt(row["lcoh_heat_battery_EUR_MWhth"])
    hp_l = (
        fmt(row["lcoh_heat_pump_EUR_MWhth"])
        if pd.notna(row.get("lcoh_heat_pump_EUR_MWhth"))
        else "N/A — temp. ineligible"
    )
    d_hb = (
        f'<span style="color:#888">Δ {fmt(row["delta_vs_gas_hb_EUR_MWhth"], suffix=" EUR/MWh_th")}</span>'
        if pd.notna(row.get("delta_vs_gas_hb_EUR_MWhth"))
        else ""
    )
    d_hp = (
        f'<span style="color:#888">Δ {fmt(row["delta_vs_gas_hp_EUR_MWhth"], suffix=" EUR/MWh_th")}</span>'
        if pd.notna(row.get("delta_vs_gas_hp_EUR_MWhth"))
        else ""
    )
    return f"""
<div style="font-family:sans-serif;font-size:12px;width:340px;line-height:1.55">
<b style="font-size:13px">{row['eprtr_facility_name']}</b><br>
<span style="color:#666">{row['eprtr_country']} · {row['eprtr_activity']} · {int(row['analysis_year'])}</span>
<hr style="margin:5px 0">
<b>Heat:</b> {fmt(row['annual_heat_demand_MWh_th']/1000, 1, ' GWh_th/yr')}<br>
<b>Band:</b> {row['process_heat_temp_band']}
<hr style="margin:5px 0">
<b>LCOH (EUR/MWh_th):</b> gas {gas_l} | HB {hb_l} {d_hb} | HP {hp_l} {d_hp}<br>
<b>Least-cost:</b> <span style="color:{lc_clr};font-weight:bold">{lc_lbl}</span><br>
<b>Prices:</b> grid elec {fmt(row.get('elec_price_used_EUR_MWh'))} | gas {fmt(row.get('gas_price_used_EUR_MWh'))} EUR/MWh
</div>"""


def build_popup_off(row: pd.Series) -> str:
    lc = row.get("least_cost_pathway", "")
    lc_lbl = PATHWAY_LABELS.get(lc, lc)
    lc_clr = PATHWAY_COLORS.get(lc, "#888")
    return f"""
<div style="font-family:sans-serif;font-size:12px;width:340px;line-height:1.55">
<b style="font-size:13px">{row['eprtr_facility_name']}</b><br>
<span style="color:#666">{row['eprtr_country']} · {int(row['analysis_year'])}</span>
<hr style="margin:5px 0">
<b>LCOH:</b> gas {fmt(row['lcoh_natural_gas_EUR_MWhth'])} | HB {fmt(row['lcoh_heat_battery_EUR_MWhth'])}<br>
<b>Least-cost:</b> <span style="color:{lc_clr};font-weight:bold">{lc_lbl}</span><br>
<b>Off-grid elec:</b> HB PV {fmt(row.get('elec_price_hb_EUR_MWh'))} | HP PV+BESS {fmt(row.get('elec_price_hp_EUR_MWh'))} EUR/MWh
</div>"""


def build_popup_flip(row: pd.Series) -> str:
    return f"""
<div style="font-family:sans-serif;font-size:12px;width:360px;line-height:1.55">
<b style="font-size:13px">{row['facility_name']}</b><br>
<span style="color:#666">{row['country']} · {row['temp_band']}</span>
<hr style="margin:5px 0">
<b>Grid winner:</b> {PATHWAY_LABELS.get(row['least_cost_pathway_grid'], row['least_cost_pathway_grid'])}<br>
<b>Off-grid winner:</b> {PATHWAY_LABELS.get(row['least_cost_pathway_off'], row['least_cost_pathway_off'])}<br>
<b>Gas LCOH (grid):</b> {fmt(row['lcoh_gas_grid'])} → <b>HB LCOH (off):</b> {fmt(row['lcoh_hb_off'])}<br>
<b>Grid elec / off PV-only:</b> {fmt(row['elec_grid_EUR_MWh'])} / {fmt(row['elec_off_hb_EUR_MWh'])} EUR/MWh<br>
<b>HB savings vs grid gas:</b> {fmt(row['hb_savings_vs_grid_gas_EUR_MWhth'], suffix=' EUR/MWh_th')}
</div>"""


def add_legend(m: folium.Map) -> None:
    legend_html = """
<div style="position:fixed;bottom:40px;left:40px;z-index:1000;
            background:white;padding:12px 16px;border-radius:8px;
            border:1px solid #ccc;font-family:sans-serif;font-size:12px;line-height:1.9">
    <b>Least-cost pathway</b><br>
    <span style="color:#4C72B0;font-size:16px">&#9679;</span> Natural gas<br>
    <span style="color:#55A868;font-size:16px">&#9679;</span> Heat pump<br>
    <span style="color:#C44E52;font-size:16px">&#9679;</span> Heat battery<br>
    <hr style="margin:5px 0">
    <span style="color:#888;font-size:11px">Size ∝ log(heat demand)</span>
</div>"""
    m.get_root().html.add_child(folium.Element(legend_html))


def save_pathway_map(
    df: pd.DataFrame,
    out_dir: Path,
    filename_stem: str,
    popup_builder,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    map_df = df.copy()
    map_df["eprtr_lat"] = pd.to_numeric(map_df["eprtr_lat"], errors="coerce")
    map_df["eprtr_lon"] = pd.to_numeric(map_df["eprtr_lon"], errors="coerce")
    map_df = map_df.dropna(subset=["eprtr_lat", "eprtr_lon", "lcoh_natural_gas_EUR_MWhth"])

    m = folium.Map(location=[51.5, 10.0], zoom_start=5, tiles="CartoDB positron")
    for _, row in map_df.iterrows():
        lc = row.get("least_cost_pathway", "")
        color = PATHWAY_COLORS.get(lc, "#888888")
        folium.CircleMarker(
            location=[row["eprtr_lat"], row["eprtr_lon"]],
            radius=marker_radius(row["annual_heat_demand_MWh_th"]),
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.72,
            weight=0.8,
            popup=folium.Popup(popup_builder(row), max_width=360),
            tooltip=folium.Tooltip(
                f"<b>{row['eprtr_facility_name']}</b><br>"
                f"{PATHWAY_LABELS.get(lc, lc)}",
                sticky=False,
            ),
        ).add_to(m)
    add_legend(m)
    path = out_dir / f"{filename_stem}_{len(map_df)}fac_{date.today().isoformat()}.html"
    m.save(str(path))
    print(f"  Saved {path}")
    return path


def save_flip_map(flip: pd.DataFrame, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    map_df = flip.dropna(subset=["lat", "lon"]).copy()
    m = folium.Map(location=[51.5, 10.0], zoom_start=5, tiles="CartoDB positron")
    for _, row in map_df.iterrows():
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=marker_radius(row["heat_MWh"]),
            color="#C44E52",
            fill=True,
            fill_color="#C44E52",
            fill_opacity=0.75,
            weight=1,
            popup=folium.Popup(build_popup_flip(row), max_width=380),
            tooltip=folium.Tooltip(f"<b>{row['facility_name']}</b><br>grid gas → off HB", sticky=False),
        ).add_to(m)
    legend = """
<div style="position:fixed;bottom:40px;left:40px;z-index:1000;background:white;padding:10px 14px;
border-radius:8px;border:1px solid #ccc;font-family:sans-serif;font-size:12px">
<b>Grid gas → off-grid heat battery</b><br>
<span style="color:#C44E52;font-size:16px">&#9679;</span> Flip cohort (LCP matched, excl. 1(c))
</div>"""
    m.get_root().html.add_child(folium.Element(legend))
    path = out_dir / f"gas_to_offgrid_hb_flip_map_{len(map_df)}fac_{date.today().isoformat()}.html"
    m.save(str(path))
    print(f"  Saved {path}")
    return path


def export_flip_samples(grid: pd.DataFrame, off: pd.DataFrame) -> pd.DataFrame:
    m = grid.merge(
        off,
        on="eprtr_facility_id",
        how="inner",
        suffixes=("_grid", "_off"),
    )
    flip = m[
        (m["least_cost_pathway_grid"] == "natural_gas")
        & (m["least_cost_pathway_off"] == "heat_battery")
    ].copy()
    flip["facility_name"] = flip["eprtr_facility_name_grid"]
    flip["country"] = flip["eprtr_country_grid"]
    flip["iso3"] = flip["iso3_country_grid"]
    flip["temp_band"] = flip["process_heat_temp_band_grid"]
    flip["heat_MWh"] = flip["annual_heat_demand_MWh_th_grid"]
    flip["lat"] = flip["eprtr_lat_grid"]
    flip["lon"] = flip["eprtr_lon_grid"]
    flip["lcoh_gas_grid"] = flip["lcoh_natural_gas_EUR_MWhth_grid"]
    flip["lcoh_hb_grid"] = flip["lcoh_heat_battery_EUR_MWhth_grid"]
    flip["lcoh_hb_off"] = flip["lcoh_heat_battery_EUR_MWhth_off"]
    flip["lcoh_gas_off"] = flip["lcoh_natural_gas_EUR_MWhth_off"]
    flip["elec_grid_EUR_MWh"] = flip["elec_price_used_EUR_MWh"]
    flip["elec_off_hb_EUR_MWh"] = flip["elec_price_hb_EUR_MWh"]
    flip["elec_off_hp_EUR_MWh"] = flip["elec_price_hp_EUR_MWh"]
    flip["gas_EUR_MWh"] = flip["gas_price_used_EUR_MWh_grid"]
    flip["hb_savings_vs_grid_gas_EUR_MWhth"] = flip["lcoh_gas_grid"] - flip["lcoh_hb_off"]
    flip["elec_ratio_grid_to_offhb"] = flip["elec_grid_EUR_MWh"] / flip["elec_off_hb_EUR_MWh"]

    cols = [
        "eprtr_facility_id",
        "facility_name",
        "country",
        "iso3",
        "analysis_year_grid",
        "eprtr_activity_grid",
        "temp_band",
        "heat_MWh",
        "match_method",
        "least_cost_pathway_grid",
        "least_cost_pathway_off",
        "lcoh_gas_grid",
        "lcoh_hb_grid",
        "lcoh_hb_off",
        "hb_savings_vs_grid_gas_EUR_MWhth",
        "elec_grid_EUR_MWh",
        "elec_off_hb_EUR_MWh",
        "elec_ratio_grid_to_offhb",
        "gas_EUR_MWh",
        "bnef_data_source",
        "lat",
        "lon",
    ]
    if "match_method" not in flip.columns:
        flip["match_method"] = flip.get("match_method_grid", flip.get("match_method_off"))
    return flip[cols].sort_values("hb_savings_vs_grid_gas_EUR_MWhth", ascending=False)


def main() -> None:
    print("Loading cohorts (COMPUTED, LCP matched, excl. 1(c))...")
    grid = load_cohort(GRID_CSV)
    off = load_cohort(OFF_CSV)
    print(f"  Grid: {len(grid):,}  Off: {len(off):,}")

    print("\nMaps:")
    save_pathway_map(
        grid,
        OUT_GRID,
        "lcoh_facility_map_grid_lcp_matched_excl_1c",
        build_popup_grid,
    )
    save_pathway_map(
        off,
        OUT_OFF,
        "lcoh_facility_map_offgrid_lcp_matched_excl_1c",
        build_popup_off,
    )

    print("\nGas → off-grid HB flip samples:")
    flip = export_flip_samples(grid, off)
    OUT_REPO.mkdir(parents=True, exist_ok=True)
    all_path = OUT_REPO / f"gas_to_offgrid_hb_flip_samples_{len(flip)}fac_{date.today().isoformat()}.csv"
    flip.to_csv(all_path, index=False)
    print(f"  All flips ({len(flip):,}): {all_path}")

    # Curated samples: top heat demand + top HB savings + diverse countries
    top_heat = flip.nlargest(25, "heat_MWh")
    top_save = flip.nlargest(25, "hb_savings_vs_grid_gas_EUR_MWhth")
    diverse = (
        flip.sort_values("hb_savings_vs_grid_gas_EUR_MWhth", ascending=False)
        .groupby("iso3", group_keys=False)
        .head(3)
    )
    sample = pd.concat([top_heat, top_save, diverse]).drop_duplicates("eprtr_facility_id")
    sample_path = OUT_REPO / f"gas_to_offgrid_hb_flip_samples_curated_{len(sample)}fac_{date.today().isoformat()}.csv"
    sample.to_csv(sample_path, index=False)
    print(f"  Curated ({len(sample):,}): {sample_path}")

    save_flip_map(flip, OUT_REPO / "maps")
    print("\nDone.")


if __name__ == "__main__":
    main()
