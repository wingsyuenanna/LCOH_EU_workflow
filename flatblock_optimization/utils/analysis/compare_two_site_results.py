#!/usr/bin/env python3
"""
Compare flatblock scenario outputs for two sites: summary_site*.csv + hourly_site*.csv.

Focus: LCOE breakdown, sizing (S_opt, battery), and solar utilization / curtailment
that explain differences in LCOE_solar (which scales with annual solar $ / fixed load MWh).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def _find_col(df: pd.DataFrame, prefix: str) -> str | None:
    for c in df.columns:
        if c.startswith(prefix):
            return c
    return None


def load_hourly(path: Path) -> tuple[pd.DataFrame, str | None, str | None]:
    df = pd.read_csv(path)
    avail_col = _find_col(df, "Solar_available_MW")
    soc_col = _find_col(df, "SOC_MWh")
    return df, avail_col, soc_col


def hourly_metrics(
    df: pd.DataFrame,
    avail_col: str | None,
    soc_col: str | None,
    s_opt_summary: float,
) -> dict:
    if avail_col is None:
        raise ValueError("No Solar_available_MW* column in hourly file")

    used = df["Solar_used_MW"].astype(float)
    avail = df[avail_col].astype(float)
    curt = df["Curtail_MW"].astype(float) if "Curtail_MW" in df.columns else (avail - used).clip(lower=0)
    load = df["Load_MW"].astype(float)

    sum_avail = float(avail.sum())
    sum_used = float(used.sum())
    sum_curt = float(curt.sum())
    sum_load = float(load.sum())

    util = sum_used / sum_avail if sum_avail > 0 else np.nan
    curt_frac = sum_curt / sum_avail if sum_avail > 0 else np.nan
    solar_share = sum_used / sum_load if sum_load > 0 else np.nan

    # Normalized "profile integral" (MWh of CF if S_opt=1): sum(avail)/S_opt
    profile_mwh_per_unit_s = sum_avail / s_opt_summary if s_opt_summary else np.nan

    charge = df["BESS_charge_MW"].astype(float) if "BESS_charge_MW" in df.columns else pd.Series(0.0, index=df.index)
    discharge = (
        df["BESS_discharge_MW"].astype(float) if "BESS_discharge_MW" in df.columns else pd.Series(0.0, index=df.index)
    )
    unserved = df["Unserved_MW"].astype(float) if "Unserved_MW" in df.columns else pd.Series(0.0, index=df.index)

    out = {
        "hours": len(df),
        "sum_solar_avail_MWh": sum_avail,
        "sum_solar_used_MWh": sum_used,
        "sum_curtail_MWh": sum_curt,
        "sum_load_MWh": sum_load,
        "solar_util_used_over_avail": util,
        "curtail_frac_of_avail": curt_frac,
        "solar_used_over_load": solar_share,
        "profile_integral_MWh_per_MWac": profile_mwh_per_unit_s,
        "sum_bess_charge_MWh": float(charge.sum()),
        "sum_bess_discharge_MWh": float(discharge.sum()),
        "sum_unserved_MWh": float(unserved.sum()),
        "hours_unserved_gt001": int((unserved > 0.01).sum()),
        "mean_avail_when_curtail_pos": float(avail[curt > 0.01].mean()) if (curt > 0.01).any() else np.nan,
        "hours_curtail_pos": int((curt > 0.01).sum()),
    }
    if soc_col:
        soc = df[soc_col].astype(float)
        mx = float(soc.max())
        curt_mask = curt > 0.01
        if curt_mask.any():
            out["mean_soc_when_curtail"] = float(soc[curt_mask].mean())
            out["frac_curtail_hours_soc_ge_99pct_max"] = float((soc[curt_mask] >= 0.99 * mx).mean())
        else:
            out["mean_soc_when_curtail"] = np.nan
            out["frac_curtail_hours_soc_ge_99pct_max"] = np.nan
    return out


def load_summary(path: Path) -> pd.Series:
    df = pd.read_csv(path)
    if len(df) != 1:
        raise ValueError(f"Expected 1 row in {path}, got {len(df)}")
    return df.iloc[0]


def main() -> None:
    p = argparse.ArgumentParser(description="Compare two flatblock scenario result sets (summary + hourly).")
    p.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Folder containing summary_site<ID>.csv and hourly_site<ID>.csv",
    )
    p.add_argument("--site-a", type=int, required=True)
    p.add_argument("--site-b", type=int, required=True)
    args = p.parse_args()

    rd = args.results_dir.resolve()
    sa = load_summary(rd / f"summary_site{args.site_a}.csv")
    sb = load_summary(rd / f"summary_site{args.site_b}.csv")
    ha, ca, soc_a = load_hourly(rd / f"hourly_site{args.site_a}.csv")
    hb, cb, soc_b = load_hourly(rd / f"hourly_site{args.site_b}.csv")

    # Summary comparison
    summary_keys = [
        "load_MW",
        "S_opt_MW",
        "Battery_capacity_MW",
        "Battery_energy_MWh",
        "LCOE_total_$perMWh",
        "LCOE_solar_$perMWh",
        "LCOE_batt_$perMWh",
        "LCOE_unserved_$perMWh",
        "Reliability_%",
        "Unserved_MWh",
        "Unserved_hours",
    ]
    comp = pd.DataFrame({"A": sa[summary_keys], "B": sb[summary_keys]})
    comp["delta (B-A)"] = comp["B"] - comp["A"]
    comp["pct_vs_A"] = np.where(
        comp["A"].abs() > 1e-9,
        (comp["B"] - comp["A"]) / comp["A"].abs() * 100.0,
        np.nan,
    )

    print("=== Summary (A = site %s, B = site %s) ===" % (args.site_a, args.site_b))
    print(comp.to_string())
    print()

    ma = hourly_metrics(ha, ca, soc_a, float(sa["S_opt_MW"]))
    mb = hourly_metrics(hb, cb, soc_b, float(sb["S_opt_MW"]))
    hcomp = pd.DataFrame({"A": pd.Series(ma), "B": pd.Series(mb)})
    hcomp["delta (B-A)"] = hcomp["B"] - hcomp["A"]

    print("=== Hourly-derived metrics ===")
    print(hcomp.to_string())
    print()

    # LCOE_solar driver note
    s_a, s_b = float(sa["S_opt_MW"]), float(sb["S_opt_MW"])
    ls_a, ls_b = float(sa["LCOE_solar_$perMWh"]), float(sb["LCOE_solar_$perMWh"])
    load_mwh = float(sa["load_MW"]) * float(ma["hours"])  # assumes constant load & same hours

    print("=== LCOE_solar interpretation (unserved_v2 model) ===")
    print(
        "LCOE_solar = annual_solar_cost(S_opt) / annual_load_MWh. "
        "With the same BNEF $/kW and CRF for both sites, annual_solar_cost scales ~linearly with S_opt.\n"
        f"  Site A: S_opt={s_a:.2f} MW, LCOE_solar=${ls_a:.2f}/MWh\n"
        f"  Site B: S_opt={s_b:.2f} MW, LCOE_solar=${ls_b:.2f}/MWh\n"
        f"  Implied ratio LCOE_solar_B / LCOE_solar_A = {ls_b / ls_a:.4f}\n"
        f"  Implied ratio S_opt_B / S_opt_A         = {s_b / s_a:.4f}"
    )
    if load_mwh > 0:
        implied_cost_a = ls_a * load_mwh
        implied_cost_b = ls_b * load_mwh
        print(f"\n  annual_load_MWh (both, ~same if load_MW & hours match): {load_mwh:,.0f} MWh")
        print(f"  implied annual_solar_cost A: ${implied_cost_a:,.0f} | B: ${implied_cost_b:,.0f}")

    prof_a = ma["profile_integral_MWh_per_MWac"]
    prof_b = mb["profile_integral_MWh_per_MWac"]
    print("\n=== Solar *resource* shape (independent of S_opt) ===")
    ratio_prof = prof_b / prof_a if prof_a and prof_a > 0 and not np.isnan(prof_a) else np.nan
    print(
        f"  sum(Solar_available)/S_opt ≈ annual MWh if S_opt=1 MWac\n"
        f"  Site A: {prof_a:,.2f} | Site B: {prof_b:,.2f} | ratio B/A: {ratio_prof:.4f}"
    )
    if prof_a and prof_b and not np.isnan(ratio_prof) and abs(ratio_prof - 1.0) < 0.02:
        print("  (Profiles are very similar; LCOE_solar difference is likely dominated by S_opt / battery / unserved tradeoff, not raw PVGIS shape.)")
    elif prof_a and prof_b:
        print("  (Material difference in normalized profile integral — check PVGIS inputs / site coordinates / solar parquet for each source_id.)")

    util_a, util_b = ma["solar_util_used_over_avail"], mb["solar_util_used_over_avail"]
    print("\n=== Utilization & curtailment ===")
    print(f"  Solar used/available: A={util_a:.4f} | B={util_b:.4f}")
    print(f"  Curtail fraction:       A={ma['curtail_frac_of_avail']:.4f} | B={mb['curtail_frac_of_avail']:.4f}")


if __name__ == "__main__":
    main()
