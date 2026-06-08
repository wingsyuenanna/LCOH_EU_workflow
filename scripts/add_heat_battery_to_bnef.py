#!/usr/bin/env python3
"""Append Industrial heat battery (thermal) rows to inputs/bnef_country_costs.csv."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BNEF_PATH = ROOT / "inputs" / "bnef_country_costs.csv"

HB_TECH = "Industrial heat battery (thermal)"
CAPEX_EUR_KW_TH_2025 = 150.0  # LCOH workflow A7 / NREL Wikoff et al. 2025
CAPEX_REDUCTION_2030 = 0.90  # 10% learning by 2030
OM_FRAC = 0.005  # A9_hb
CF_THERMAL = 0.85  # A_CF
EUR_USD = 0.921  # A13
STORAGE_HOURS = 8  # design point in A7 derivation


def levelized_capex_om_usd(capex_usd_kw: float, crf: float) -> tuple[float, float, float]:
    fom = capex_usd_kw * OM_FRAC
    annual_cost = capex_usd_kw * crf + fom
    mwh_per_kw_yr = CF_THERMAL * 8760 / 1000
    lcoe = annual_cost / mwh_per_kw_yr
    return fom, annual_cost, lcoe


def main():
    df = pd.read_csv(BNEF_PATH)
    if HB_TECH in df["technology"].values:
        print(f"{HB_TECH} rows already present ({(df.technology == HB_TECH).sum()}); skipping.")
        return

    ref = df[df["technology"] == "PV fixed-axis"].copy()
    new_rows = []
    for year in (2025, 2030):
        learning = 1.0 if year == 2025 else CAPEX_REDUCTION_2030
        capex_eur = CAPEX_EUR_KW_TH_2025 * learning
        capex_usd = capex_eur / EUR_USD
        for _, row in ref[ref["year"] == year].iterrows():
            crf = float(row["crf"])
            fom, annual_cost, lcoe = levelized_capex_om_usd(capex_usd, crf)
            new_rows.append(
                {
                    "iso3_country": row["iso3_country"],
                    "year": year,
                    "technology": HB_TECH,
                    "income_group": row["income_group"],
                    "wb_region": row["wb_region"],
                    "capex_$/kw": round(capex_usd, 2),
                    "fom_$/kw/yr": round(fom, 4),
                    "vom_$/mwh": 0.0,
                    "lcoe_$/mwh": round(lcoe, 4),
                    "wacc_nominal": row["wacc_nominal"],
                    "crf": crf,
                    "annual_cost_$/kw/yr": round(annual_cost, 4),
                    "data_source": "estimated_LCOH_A7_NREL_Wikoff_2025",
                    "capex_source": "nrel_firebrick_tes_via_lcoh_spec",
                    "crf_source": row["crf_source"],
                    "proxy_peers": row.get("proxy_peers", ""),
                    "country_name": row["country_name"],
                }
            )

    out = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    out.to_csv(BNEF_PATH, index=False)
    print(f"Wrote {len(new_rows)} rows ({HB_TECH}) to {BNEF_PATH} (total {len(out)} rows).")


if __name__ == "__main__":
    main()
