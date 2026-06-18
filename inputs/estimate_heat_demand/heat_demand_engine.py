"""
heat_demand_engine.py
---------------------
Reads heat_demand_config.xlsx and applies it to eprtr_lcp_matched.csv.

Two routing regimes (set per-activity in the config's activity_control sheet):

  tech_split  : one shared fuel pool fed to several technologies by an assumed
                split. Used where the LCP data cannot tell you which unit burned
                a given TJ (refineries, chemicals). Applies the tech_split sheet
                to the TOTAL substitutable fuel.

  fuel_routing: each fuel maps to its own technology + epsilon. Used where IED
                Article 28 has already removed the direct-heating units, so the
                fuel type determines the technology (iron & steel, waste, metals).
                The "split" is the per-facility fuel mix itself.

Output:
  heat_demand_TJ = sum over technologies of  fuel_to_tech_TJ * eta * epsilon_tech
  This is the total useful heat, after efficiencies and technology splits -- the
  heat that an industrial heat battery would supply. No temperature gate: a
  1500 C battery can serve every technology in the dataset.
"""

import re
import pandas as pd
import numpy as np

CONFIG = "heat_demand_config.xlsx"
DATA = "eprtr_lcp_matched.csv"
OUT_FACILITY = "heat_demand_by_facility.csv"
OUT_SUMMARY = "heat_demand_summary.csv"

SUB_FUEL_COLS = {
    "NaturalGas":      "lcp_NaturalGas_TJ",
    "Coal":            "lcp_Coal_TJ",
    "Lignite":         "lcp_Lignite_TJ",
    "OtherSolidFuels": "lcp_OtherSolidFuels_TJ",
    "LiquidFuels":     "lcp_LiquidFuels_TJ",
    "Peat":            "lcp_Peat_TJ",
}

control = pd.read_excel(CONFIG, sheet_name="activity_control")
tech_split = pd.read_excel(CONFIG, sheet_name="tech_split")
fuel_routing = pd.read_excel(CONFIG, sheet_name="fuel_routing")

control["activity_pattern"] = control["activity_pattern"].astype(str)
tech_split["activity_pattern"] = tech_split["activity_pattern"].astype(str)
fuel_routing["activity_pattern"] = fuel_routing["activity_pattern"].astype(str)

for pat, grp in tech_split.groupby("activity_pattern"):
    s = grp["split_fraction"].sum()
    if abs(s - 1.0) > 1e-6:
        print(f"  WARNING: tech_split for {pat} sums to {s:.3f}, not 1.0")

def match_control(activity_code):
    for _, row in control.iterrows():
        if re.match(row["activity_pattern"], str(activity_code)):
            return row
    return None

df = pd.read_csv(DATA)
df = df[df["eprtr_activity"].notna()].copy()

records = []
for _, row in df.iterrows():
    act = str(row["eprtr_activity"])
    sub_total = row.get("lcp_substitutable_thermal_TJ")
    ctrl = match_control(act)

    base = {
        "eprtr_facility_id": row.get("eprtr_facility_id"),
        "eprtr_facility_name": row.get("eprtr_facility_name"),
        "eprtr_country": row.get("eprtr_country"),
        "eprtr_activity": act,
    }

    if ctrl is None or pd.isna(sub_total):
        records.append({**base,
                        "activity_label": ctrl["activity_label"] if ctrl is not None else "unmapped",
                        "eta": np.nan, "substitutable_TJ": np.nan,
                        "heat_demand_TJ": np.nan,
                        "note": "no LCP data" if ctrl is not None else "activity not in config"})
        continue

    eta = float(ctrl["eta"])
    mode = str(ctrl["routing_mode"]).strip()
    heat = 0.0

    if mode == "tech_split":
        splits = tech_split[tech_split["activity_pattern"] == ctrl["activity_pattern"]]
        for _, t in splits.iterrows():
            fuel_to_tech = sub_total * float(t["split_fraction"])
            heat += fuel_to_tech * eta * float(t["epsilon"])

    elif mode == "fuel_routing":
        routes = fuel_routing[fuel_routing["activity_pattern"] == ctrl["activity_pattern"]]
        route_lookup = {r["fuel"]: r for _, r in routes.iterrows()}
        for fuel, col in SUB_FUEL_COLS.items():
            fuel_TJ = row.get(col)
            if pd.isna(fuel_TJ) or fuel_TJ == 0:
                continue
            r = route_lookup.get(fuel)
            eps = float(r["epsilon"]) if r is not None else 0.83
            heat += fuel_TJ * eta * eps
    else:
        records.append({**base, "activity_label": ctrl["activity_label"],
                        "eta": eta, "substitutable_TJ": round(sub_total, 2),
                        "heat_demand_TJ": np.nan,
                        "note": f"unknown routing_mode '{mode}'"})
        continue

    records.append({
        **base,
        "activity_label": ctrl["activity_label"],
        "eta": eta,
        "routing_mode": mode,
        "substitutable_TJ": round(sub_total, 2),
        "heat_demand_TJ": round(heat, 2),
        "note": "",
    })

result = pd.DataFrame(records)
result.to_csv(OUT_FACILITY, index=False)

valid = result[result["heat_demand_TJ"].notna()]
summary = (valid.groupby(["eprtr_activity", "activity_label", "routing_mode"])
           .agg(n=("eprtr_facility_id", "count"),
                substitutable_TJ=("substitutable_TJ", "sum"),
                heat_demand_TJ=("heat_demand_TJ", "sum"))
           .reset_index()
           .sort_values("heat_demand_TJ", ascending=False))
summary.to_csv(OUT_SUMMARY, index=False)

total = summary[~summary["eprtr_activity"].str.match(r"^1\(c\)")]["heat_demand_TJ"].sum()
print(f"Total useful heat demand (excl. 1c): {total:,.0f} TJ")
print()
print(summary.head(20).to_string(index=False))
