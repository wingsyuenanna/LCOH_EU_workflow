"""
Solar feasibility and site-specific LCOH for the 115 id_direct / non-1(c) / 2024 facilities.

Inputs required:
  inputs/gee_facilities_115.csv              — 115 facility IDs + lat/lon
  inputs/bnef_country_costs.csv             — BNEF annual PV cost by country
  off_grid_electricity_run/outputs/pvgis_solar_cf_115.csv   — PVGIS site-specific CF
  off_grid_electricity_run/outputs/lcoh_results.csv         — existing heat demand + BNEF LCOH

Optional (land feasibility — provide when GEE Drive export is processed):
  off_grid_electricity_run/outputs/suitable_land.csv        — from process_land_availability.py
  If absent: land_feasibility columns are set to LAND_DATA_PENDING.

Outputs:
  off_grid_electricity_run/outputs/solar_feasibility_115.csv

Key methodology:
  Site-specific PV LCOE (EUR/MWh_e):
    = BNEF annual_cost ($/kW/yr) × EUR_per_USD / (PVGIS_CF × 8760 / 1000)
  This replaces the BNEF country-level LCOE (which embeds a generic CF) with the
  site-specific capacity factor from the PVGIS ERA5 API.

  Heat battery LCOH recalculation uses the same capex/OM components as the main
  off_grid run, but substitutes the site-specific PV LCOE for the energy term.

  Required land (km²):
    electricity_needed = heat_demand_MWh_th / eta_hb
    solar_capacity_MW  = electricity_needed / (CF × 8760)
    land_needed_km2    = solar_capacity_MW / SOLAR_DENSITY_MW_PER_KM2

  Land feasibility (when suitable_land.csv is available):
    FEASIBLE   : land_needed_km2 <= suitable_land_km2
    INFEASIBLE  : land_needed_km2 > suitable_land_km2

  Overlap adjustment (Option B — demand-proportional):
    For each pair of facilities whose 10 km buffers overlap (centres < 20 km
    apart), the geometric intersection area is computed analytically.  The
    suitable land within that intersection is estimated by averaging each
    facility's suitability fraction (suitable_land / total_buffer_area) applied
    to the overlap area.  The shared suitable land is then reallocated in
    proportion to each facility's heat demand.  Every facility's
    suitable_land_adj_km2 = suitable_land_km2 + Σ_j (demand_allocation_j −
    geographic_claim_j) across all overlapping neighbours j.  The total
    suitable land across any overlapping pair is conserved; only the split
    changes.  Original columns are kept for reference; _adj columns reflect
    the corrected allocation.
"""

import argparse
import csv
import math
import os
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPTS_DIR = Path(__file__).resolve().parent
OFFGRID_DIR = SCRIPTS_DIR.parent                             # off_grid_electricity_run/
REPO_ROOT   = OFFGRID_DIR.parent

FACILITIES_CSV = REPO_ROOT / "inputs" / "gee_facilities_115.csv"
BNEF_CSV       = REPO_ROOT / "inputs" / "bnef_country_costs.csv"
# PVGIS and land data always live in the off-grid outputs (shared by both runs)
PVGIS_CSV  = OFFGRID_DIR / "outputs" / "pvgis_solar_cf_115.csv"
LAND_CSV   = OFFGRID_DIR / "outputs" / "suitable_land.csv"

# Run-specific paths and column names — resolved in main() from --run argument
RUN_CONFIG = {
    "offgrid": {
        "lcoh_csv":      OFFGRID_DIR / "outputs" / "lcoh_results.csv",
        "out_csv":       OFFGRID_DIR / "outputs" / "solar_feasibility_115.csv",
        "elec_col":      "elec_price_hb_EUR_MWh",
        "label":         "offgrid_bnef_pv",
    },
    "grid": {
        "lcoh_csv":      REPO_ROOT / "grid_electricity_run" / "outputs" / "lcoh_results.csv",
        "out_csv":       REPO_ROOT / "grid_electricity_run" / "outputs" / "solar_feasibility_115.csv",
        "elec_col":      "elec_price_used_EUR_MWh",
        "label":         "grid_eurostat",
    },
}

# ── Assumptions (aligned with off_grid lcoh_calculation.py) ──────────────────
EUR_PER_USD          = 0.921    # A13 ECB 2024 avg
ETA_HB               = 0.90     # A4 heat battery roundtrip efficiency
COP_HP               = 2.8      # A3 heat pump COP
HB_CAPEX_EUR_KW      = 150.0   # A7 EUR/kW_th (default; BNEF country override if available)
HB_OM_FRAC           = 0.005   # A9_hb fraction of CAPEX/yr
DISCOUNT_RATE        = 0.08     # A1
PROJECT_LIFE         = 20       # A2 years
PROCESS_CF           = 0.85     # A_CF industrial capacity factor
BNEF_YEAR            = 2025     # maps to analysis_year ≤ 2027 (spec §5.1.3)
SOLAR_DENSITY_MW_KM2 = 10.0    # utility-scale PV density MW/km²
BUFFER_R_KM          = 10.0    # radius of GEE land buffer (must match buffer_m=10000)

OUTPUT_FIELDS = [
    # ── Run metadata ──
    "run_type",                          # offgrid_bnef_pv | grid_eurostat
    # ── Facility info ──
    "source_id", "source_name", "country", "iso3_country",
    "eprtr_activity", "latitude", "longitude",
    # ── Heat demand (from existing LCOH run) ──
    "annual_heat_demand_MWh_th", "heat_demand_provenance",
    # ── Solar resource ──
    "pvgis_year", "pvgis_annual_cf", "pvgis_kwh_per_kwp",
    # ── BNEF cost inputs ──
    "bnef_annual_cost_usd_kw_yr", "bnef_data_source",
    # ── Site-specific PV LCOE ──
    "site_pv_lcoe_eur_mwh",          # PVGIS CF + BNEF annual cost
    "bnef_country_lcoe_eur_mwh",     # original BNEF country-level LCOE (reference)
    "lcoe_delta_eur_mwh",            # site_lcoe - bnef_lcoe (+ve = site worse, -ve = site better)
    # ── Site-specific LCOH (heat battery) ──
    "lcoh_hb_site_specific_eur_mwhth",   # recomputed with PVGIS CF
    "lcoh_hb_bnef_eur_mwhth",            # original BNEF-based LCOH (reference)
    "lcoh_hb_delta_eur_mwhth",           # site_lcoh - bnef_lcoh
    "lcoh_gas_eur_mwhth",                # natural gas LCOH (unchanged reference)
    "lcoh_hb_vs_gas_delta_eur_mwhth",    # site HB LCOH - gas LCOH
    # ── Required solar infrastructure ──
    "electricity_needed_mwh_yr",     # heat_demand / eta_hb
    "solar_capacity_needed_mwp",     # electricity / (CF × 8760)
    "land_needed_km2",               # solar_MW / SOLAR_DENSITY
    # ── Land availability (from GEE — optional) ──
    "suitable_land_km2",             # grassland + shrubland + bare_sparse (post WDPA/slope mask)
    "land_feasibility",              # FEASIBLE | INFEASIBLE | LAND_DATA_PENDING | NOT_COMPUTABLE
    "land_surplus_deficit_km2",      # suitable - needed (+ve = surplus, -ve = deficit)
    # ── Overlap-adjusted land (Option B — demand-proportional) ──
    "overlap_n_neighbours",          # number of facilities whose buffers overlap with this one
    "overlap_area_total_km2",        # total geometric overlap area with all neighbours
    "suitable_land_adj_km2",         # after demand-weighted reallocation of shared zones
    "land_feasibility_adj",          # FEASIBLE | INFEASIBLE | NOT_COMPUTABLE (adjusted)
    "land_surplus_deficit_adj_km2",  # adjusted surplus/deficit
    # ── Metadata ──
    "computation_status",
    "notes",
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def crf(r, n):
    return r * (1 + r) ** n / ((1 + r) ** n - 1)

def safe_float(v, default=None):
    try:
        return float(v) if v not in (None, "", "None", "nan") else default
    except (ValueError, TypeError):
        return default

CRF_VAL     = crf(DISCOUNT_RATE, PROJECT_LIFE)
FULL_LOAD_H = PROCESS_CF * 8760   # 7446 hr/yr

def hb_capex_om_eur_mwhth(hb_capex_eur_kw=HB_CAPEX_EUR_KW):
    """Fixed capex + O&M component of HB LCOH (EUR/MWh_th) — independent of electricity price."""
    lcoh_capex = hb_capex_eur_kw * CRF_VAL / (FULL_LOAD_H / 1000.0)
    lcoh_om    = hb_capex_eur_kw * HB_OM_FRAC / (FULL_LOAD_H / 1000.0)
    return lcoh_capex + lcoh_om

def site_lcoe_eur_mwh(bnef_annual_cost_usd_kw_yr, pvgis_cf, vom_usd_mwh=0.0):
    """
    Compute site-specific PV LCOE from BNEF annual cost and PVGIS capacity factor.
    annual_cost ($/kW/yr) / (CF × 8760 / 1000 [MWh/kW/yr]) + VOM ($/MWh)  → $/MWh → EUR/MWh
    """
    if pvgis_cf is None or pvgis_cf <= 0:
        return None
    lcoe_usd = bnef_annual_cost_usd_kw_yr / (pvgis_cf * 8760 / 1000.0) + vom_usd_mwh
    return lcoe_usd * EUR_PER_USD

# ── Overlap adjustment helpers ────────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between two (lat, lon) points."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(1.0, a)))


def circle_overlap_km2(d, r):
    """Area of intersection of two equal circles with radius r whose centres are d apart."""
    if d >= 2 * r:
        return 0.0
    if d <= 0:
        return math.pi * r * r
    return 2 * r * r * math.acos(d / (2 * r)) - (d / 2) * math.sqrt(4 * r * r - d * d)


def apply_overlap_adjustments(rows, buffer_r_km=BUFFER_R_KM):
    """
    For every pair of facilities whose buffers overlap, reallocate the shared
    suitable land by heat demand ratio and write results back into each row.

    Method per pair (i, j):
      overlap_suitable = overlap_area × avg(S_i/B_i, S_j/B_j)
      alloc_i          = overlap_suitable × H_i / (H_i + H_j)
      geo_claim_i      = S_i × overlap_area / B_i
      adj_i           += alloc_i − geo_claim_i          (zero-sum across pair)
      adjusted_S_i     = max(0, S_i + Σ_j adj_i)

    Variables: S = suitable_land_km2, B = total_buffer_area_km2, H = heat demand.
    """
    default_buffer_area = math.pi * buffer_r_km ** 2

    # Pull numeric values needed for the computation from each row.
    # Rows store rounded floats or ""; safe_float handles both.
    def row_nums(r):
        return {
            "lat":      safe_float(r.get("latitude")),
            "lon":      safe_float(r.get("longitude")),
            "heat":     safe_float(r.get("annual_heat_demand_MWh_th")),
            "suitable": safe_float(r.get("suitable_land_km2")),   # "PENDING" → None
            "buffer":   safe_float(r.get("_total_buffer_km2")) or default_buffer_area,
        }

    nums   = {r["source_id"]: row_nums(r) for r in rows}
    adj    = {r["source_id"]: 0.0 for r in rows}
    n_nbrs = {r["source_id"]: 0   for r in rows}
    ov_tot = {r["source_id"]: 0.0 for r in rows}

    eligible_ids = [
        fid for fid, n in nums.items()
        if n["lat"] is not None and n["lon"] is not None
        and (n["heat"] or 0) > 0
        and n["suitable"] is not None
    ]

    for idx_i in range(len(eligible_ids)):
        for idx_j in range(idx_i + 1, len(eligible_ids)):
            fid_i, fid_j = eligible_ids[idx_i], eligible_ids[idx_j]
            ni, nj = nums[fid_i], nums[fid_j]

            d = haversine_km(ni["lat"], ni["lon"], nj["lat"], nj["lon"])
            overlap_area = circle_overlap_km2(d, buffer_r_km)
            if overlap_area <= 0:
                continue

            n_nbrs[fid_i] += 1
            n_nbrs[fid_j] += 1
            ov_tot[fid_i] += overlap_area
            ov_tot[fid_j] += overlap_area

            S_i, B_i = ni["suitable"], ni["buffer"]
            S_j, B_j = nj["suitable"], nj["buffer"]
            H_i, H_j = ni["heat"],     nj["heat"]

            frac_i = S_i / B_i if B_i > 0 else 0.0
            frac_j = S_j / B_j if B_j > 0 else 0.0

            # Best estimate of suitable land within the shared zone
            overlap_suitable = overlap_area * (frac_i + frac_j) / 2.0

            # Demand-weighted allocation
            H_total  = H_i + H_j
            alloc_i  = overlap_suitable * H_i / H_total
            alloc_j  = overlap_suitable * H_j / H_total

            # Each facility's current geographic "claim" to the overlap
            geo_i = S_i * (overlap_area / B_i) if B_i > 0 else 0.0
            geo_j = S_j * (overlap_area / B_j) if B_j > 0 else 0.0

            adj[fid_i] += alloc_i - geo_i
            adj[fid_j] += alloc_j - geo_j

    # Write adjusted values back into rows
    for r in rows:
        fid          = r["source_id"]
        n            = nums[fid]
        orig_suitable = n["suitable"]
        land_needed   = safe_float(r.get("land_needed_km2"))

        if orig_suitable is not None:
            adj_suitable = max(0.0, orig_suitable + adj[fid])
        else:
            adj_suitable = None

        if not (n["heat"] or 0):
            feas_adj    = "NOT_COMPUTABLE"
            surplus_adj = None
        elif adj_suitable is not None and land_needed is not None:
            surplus_adj = round(adj_suitable - land_needed, 2)
            feas_adj    = "FEASIBLE" if adj_suitable >= land_needed else "INFEASIBLE"
        else:
            feas_adj    = r.get("land_feasibility", "NOT_COMPUTABLE")
            surplus_adj = None

        r["overlap_n_neighbours"]          = n_nbrs[fid]
        r["overlap_area_total_km2"]        = round(ov_tot[fid], 4) if ov_tot[fid] > 0 else 0.0
        r["suitable_land_adj_km2"]         = round(adj_suitable, 4) if adj_suitable is not None else ""
        r["land_feasibility_adj"]          = feas_adj
        r["land_surplus_deficit_adj_km2"]  = surplus_adj if surplus_adj is not None else ""


# ── Load data ─────────────────────────────────────────────────────────────────
def load_csv_as_dict(path, key_col):
    out = {}
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            out[row[key_col]] = row
    return out

def load_bnef_pv(path, year=BNEF_YEAR):
    """Return {iso3: {annual_cost, lcoe, vom, data_source}} for PV fixed-axis at given year."""
    out = {}
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("technology") == "PV fixed-axis" and int(row.get("year", 0)) == year:
                out[row["iso3_country"]] = {
                    "annual_cost_usd": safe_float(row.get("annual_cost_$/kw/yr")),
                    "lcoe_usd":        safe_float(row.get("lcoe_$/mwh")),
                    "vom_usd":         safe_float(row.get("vom_$/mwh"), 0.0),
                    "data_source":     row.get("data_source", "proxied"),
                }
    return out

def load_bnef_hb_capex(path, year=BNEF_YEAR):
    """Return {iso3: capex_eur_kw} for industrial heat battery."""
    out = {}
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("technology") == "Industrial heat battery (thermal)" and int(row.get("year", 0)) == year:
                capex_usd = safe_float(row.get("capex_$/kw"))
                if capex_usd:
                    out[row["iso3_country"]] = capex_usd * EUR_PER_USD
    return out

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", choices=["grid", "offgrid"], default="offgrid",
                        help="Which LCOH run to use as the reference (default: offgrid)")
    args = parser.parse_args()

    cfg       = RUN_CONFIG[args.run]
    LCOH_CSV  = cfg["lcoh_csv"]
    OUT_CSV   = cfg["out_csv"]
    elec_col  = cfg["elec_col"]
    run_label = cfg["label"]

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    facilities = load_csv_as_dict(FACILITIES_CSV, "source_id")
    pvgis      = load_csv_as_dict(PVGIS_CSV, "source_id")
    lcoh       = load_csv_as_dict(LCOH_CSV, "eprtr_facility_id")
    land       = load_csv_as_dict(LAND_CSV, "source_id") if LAND_CSV.exists() else {}
    bnef_pv    = load_bnef_pv(BNEF_CSV)
    bnef_hb    = load_bnef_hb_capex(BNEF_CSV)

    land_available = bool(land)
    print(f"Run: {args.run} ({run_label})")
    print(f"LCOH source: {LCOH_CSV}")
    print(f"Loaded {len(facilities)} facilities")
    print(f"PVGIS CF data: {len(pvgis)} sites")
    print(f"LCOH results: {len(lcoh)} sites")
    print(f"Land data (GEE): {'available (%d sites)' % len(land) if land_available else 'NOT YET AVAILABLE — land columns will be LAND_DATA_PENDING'}")

    rows = []

    for fid, fac in facilities.items():
        iso3    = fac.get("iso3_country") or _country_to_iso3(fac.get("country", ""))
        pv_row  = pvgis.get(fid, {})
        lc_row  = lcoh.get(fid, {})
        ld_row  = land.get(fid, {}) if land_available else {}
        pv_bnef = bnef_pv.get(iso3, {})

        pvgis_cf          = safe_float(pv_row.get("annual_cf"))
        pvgis_kwh_per_kwp = safe_float(pv_row.get("annual_kwh_per_kwp"))
        heat_demand       = safe_float(lc_row.get("annual_heat_demand_MWh_th"))
        heat_prov         = lc_row.get("annual_heat_demand_provenance", "")
        bnef_hb_lcoh      = safe_float(lc_row.get("lcoh_heat_battery_EUR_MWhth"))
        bnef_elec_hb      = safe_float(lc_row.get(elec_col))
        gas_lcoh          = safe_float(lc_row.get("lcoh_natural_gas_EUR_MWhth"))

        bnef_annual_cost  = pv_bnef.get("annual_cost_usd")
        bnef_lcoe_usd     = pv_bnef.get("lcoe_usd")
        bnef_vom          = pv_bnef.get("vom_usd", 0.0) or 0.0
        bnef_src          = pv_bnef.get("data_source", "")
        bnef_lcoe_eur     = bnef_lcoe_usd * EUR_PER_USD if bnef_lcoe_usd else None

        # Site-specific LCOH for heat battery
        if pvgis_cf and bnef_annual_cost:
            s_lcoe = site_lcoe_eur_mwh(bnef_annual_cost, pvgis_cf, bnef_vom)
        else:
            s_lcoe = None

        hb_capex_eur = bnef_hb.get(iso3, HB_CAPEX_EUR_KW)
        fixed_hb     = hb_capex_om_eur_mwhth(hb_capex_eur)

        if s_lcoe is not None:
            s_hb_lcoh   = fixed_hb + (s_lcoe / ETA_HB)
            lcoe_delta  = round(s_lcoe - bnef_elec_hb, 3) if bnef_elec_hb else None
            hb_delta    = round(s_hb_lcoh - bnef_hb_lcoh, 3) if bnef_hb_lcoh is not None else None
            hb_vs_gas   = round(s_hb_lcoh - gas_lcoh, 3) if gas_lcoh is not None else None
            status      = "COMPUTED"
        else:
            s_hb_lcoh = hb_delta = lcoe_delta = hb_vs_gas = None
            status = "NOT_COMPUTABLE_MISSING_PVGIS_OR_BNEF" if heat_demand else "NOT_COMPUTABLE_NO_HEAT"

        # Required infrastructure
        if heat_demand and pvgis_cf:
            elec_needed = heat_demand / ETA_HB
            solar_mwp   = elec_needed / (pvgis_cf * 8760)
            land_needed = solar_mwp / SOLAR_DENSITY_MW_KM2
        else:
            elec_needed = solar_mwp = land_needed = None

        # Land feasibility
        suitable_land = safe_float(ld_row.get("suitable_land_km2")) if ld_row else None
        if not heat_demand:
            feasibility     = "NOT_COMPUTABLE"
            land_surplus    = None
        elif not land_available:
            feasibility     = "LAND_DATA_PENDING"
            land_surplus    = None
        elif suitable_land is not None and land_needed is not None:
            land_surplus    = round(suitable_land - land_needed, 2)
            feasibility     = "FEASIBLE" if suitable_land >= land_needed else "INFEASIBLE"
        else:
            feasibility     = "LAND_DATA_MISSING"
            land_surplus    = None

        notes = []
        if not bnef_annual_cost:
            notes.append(f"NO_BNEF_PV_FOR_{iso3}")
        if pvgis_cf and bnef_lcoe_eur:
            # note if site CF implies substantially different LCOE than BNEF country value
            if s_lcoe and abs(s_lcoe - bnef_lcoe_eur) / bnef_lcoe_eur > 0.20:
                notes.append(f"SITE_LCOE_DEVIATES_GT20PCT_FROM_BNEF")
        if not land_available:
            notes.append("GEE_LAND_DATA_NOT_YET_AVAILABLE")

        rows.append({
            "run_type":                          run_label,
            "source_id":                         fid,
            "source_name":                       fac.get("source_name", ""),
            "country":                           fac.get("country", ""),
            "iso3_country":                      iso3,
            "eprtr_activity":                    fac.get("eprtr_activity", ""),
            "latitude":                          fac.get("latitude", ""),
            "longitude":                         fac.get("longitude", ""),
            "annual_heat_demand_MWh_th":         round(heat_demand, 2) if heat_demand else "",
            "heat_demand_provenance":            heat_prov,
            "pvgis_year":                        pv_row.get("pvgis_year", ""),
            "pvgis_annual_cf":                   round(pvgis_cf, 6) if pvgis_cf else "",
            "pvgis_kwh_per_kwp":                 round(pvgis_kwh_per_kwp, 2) if pvgis_kwh_per_kwp else "",
            "bnef_annual_cost_usd_kw_yr":        round(bnef_annual_cost, 4) if bnef_annual_cost else "",
            "bnef_data_source":                  bnef_src,
            "site_pv_lcoe_eur_mwh":              round(s_lcoe, 3) if s_lcoe else "",
            "bnef_country_lcoe_eur_mwh":         round(bnef_lcoe_eur, 3) if bnef_lcoe_eur else "",
            "lcoe_delta_eur_mwh":                round(lcoe_delta, 3) if lcoe_delta is not None else "",
            "lcoh_hb_site_specific_eur_mwhth":   round(s_hb_lcoh, 3) if s_hb_lcoh else "",
            "lcoh_hb_bnef_eur_mwhth":            round(bnef_hb_lcoh, 3) if bnef_hb_lcoh is not None else "",
            "lcoh_hb_delta_eur_mwhth":           round(hb_delta, 3) if hb_delta is not None else "",
            "lcoh_gas_eur_mwhth":                round(gas_lcoh, 3) if gas_lcoh is not None else "",
            "lcoh_hb_vs_gas_delta_eur_mwhth":    round(hb_vs_gas, 3) if hb_vs_gas is not None else "",
            "electricity_needed_mwh_yr":         round(elec_needed, 1) if elec_needed else "",
            "solar_capacity_needed_mwp":         round(solar_mwp, 3) if solar_mwp else "",
            "land_needed_km2":                   round(land_needed, 4) if land_needed else "",
            "suitable_land_km2":                 round(suitable_land, 4) if suitable_land is not None else "PENDING",
            "land_feasibility":                  feasibility,
            "land_surplus_deficit_km2":          land_surplus if land_surplus is not None else "PENDING",
            "computation_status":                status,
            "notes":                             "|".join(notes),
            # internal field (not in OUTPUT_FIELDS) used by apply_overlap_adjustments
            "_total_buffer_km2":                 safe_float(ld_row.get("total_buffer_area_km2")),
        })

    # Apply demand-proportional overlap adjustment (Option B)
    print("\nComputing buffer-overlap adjustments (Option B — demand-proportional)...")
    apply_overlap_adjustments(rows)
    n_overlapping = sum(1 for r in rows if r.get("overlap_n_neighbours", 0) > 0)
    print(f"  Facilities with ≥1 overlapping neighbour: {n_overlapping}")

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # Print summary
    computed   = [r for r in rows if r["computation_status"] == "COMPUTED"]
    feasible   = [r for r in computed if r["land_feasibility"] == "FEASIBLE"]
    infeasible = [r for r in computed if r["land_feasibility"] == "INFEASIBLE"]
    pending    = [r for r in computed if r["land_feasibility"] == "LAND_DATA_PENDING"]
    feas_adj   = [r for r in computed if r.get("land_feasibility_adj") == "FEASIBLE"]
    inf_adj    = [r for r in computed if r.get("land_feasibility_adj") == "INFEASIBLE"]

    print(f"\n{'='*60}")
    print(f"Output: {OUT_CSV}")
    print(f"Total facilities:    {len(rows)}")
    print(f"LCOH computed:       {len(computed)}")
    if computed:
        lcoes = [r["site_pv_lcoe_eur_mwh"] for r in computed if r["site_pv_lcoe_eur_mwh"] != ""]
        lcoh_hbs = [r["lcoh_hb_site_specific_eur_mwhth"] for r in computed if r["lcoh_hb_site_specific_eur_mwhth"] != ""]
        deltas = [r["lcoh_hb_vs_gas_delta_eur_mwhth"] for r in computed if r["lcoh_hb_vs_gas_delta_eur_mwhth"] != ""]
        if lcoes:
            print(f"  Site PV LCOE (EUR/MWh): {min(lcoes):.1f} – {max(lcoes):.1f}  avg {sum(lcoes)/len(lcoes):.1f}")
        if lcoh_hbs:
            print(f"  Site HB LCOH (EUR/MWh_th): {min(lcoh_hbs):.1f} – {max(lcoh_hbs):.1f}  avg {sum(lcoh_hbs)/len(lcoh_hbs):.1f}")
        if deltas:
            cheaper = sum(1 for d in deltas if d < 0)
            print(f"  HB cheaper than gas: {cheaper}/{len(deltas)} sites")
    print(f"Land feasibility (unadjusted):")
    print(f"  FEASIBLE:          {len(feasible)}")
    print(f"  INFEASIBLE:        {len(infeasible)}")
    print(f"  LAND_DATA_PENDING: {len(pending)} (run GEE + process_land_availability.py then rerun)")
    if feas_adj or inf_adj:
        print(f"Land feasibility (overlap-adjusted, Option B):")
        print(f"  FEASIBLE:          {len(feas_adj)}")
        print(f"  INFEASIBLE:        {len(inf_adj)}")
    print(f"{'='*60}")


# ── Country → ISO3 fallback (mirrors lcoh_calculation.py) ────────────────────
_C2ISO3 = {
    "Austria": "AUT", "Belgium": "BEL", "Bulgaria": "BGR", "Croatia": "HRV",
    "Cyprus": "CYP", "Czechia": "CZE", "Denmark": "DNK", "Estonia": "EST",
    "Finland": "FIN", "France": "FRA", "Germany": "DEU", "Greece": "GRC",
    "Hungary": "HUN", "Iceland": "ISL", "Ireland": "IRL", "Italy": "ITA",
    "Latvia": "LVA", "Lithuania": "LTU", "Luxembourg": "LUX", "Malta": "MLT",
    "Netherlands": "NLD", "Norway": "NOR", "Poland": "POL", "Portugal": "PRT",
    "Romania": "ROU", "Serbia": "SRB", "Slovakia": "SVK", "Slovenia": "SVN",
    "Spain": "ESP", "Sweden": "SWE", "Switzerland": "CHE", "United Kingdom": "GBR",
}

def _country_to_iso3(country):
    return _C2ISO3.get(country, "???")


if __name__ == "__main__":
    main()
