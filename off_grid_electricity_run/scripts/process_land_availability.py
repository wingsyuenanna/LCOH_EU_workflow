"""
Post-GEE processing: land availability → suitable_land.csv

Run AFTER:
  1. inputs/gee_facilities_115.csv uploaded to GEE as a FeatureCollection
  2. gee_facility_calculate_available_area.py run with --buffer-m 10000
  3. GEE Drive export downloaded locally

Usage:
  python process_land_availability.py --gee-output <path/to/gee_drive_export.csv>

Output:
  off_grid_electricity_run/outputs/suitable_land.csv
"""

import argparse
import csv
import math
import os
import sys

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # off_grid_electricity_run/
REPO_ROOT  = os.path.dirname(BASE_DIR)
INPUT_CSV  = os.path.join(REPO_ROOT, "inputs", "eprtr_lcp_matched.csv")
LCOH_CSV   = os.path.join(BASE_DIR, "outputs", "lcoh_results.csv")
OUT_DIR    = os.path.join(BASE_DIR, "outputs")
OUT_FILE   = os.path.join(OUT_DIR, "suitable_land.csv")

BUFFER_M   = 10_000
BUFFER_KM2 = math.pi * (BUFFER_M / 1000) ** 2  # 314.16 km²

# ESA WorldCover classes included as solar-suitable (cropland excluded per spec)
SUITABLE_CLASSES = ["grassland_km2", "shrubland_km2", "bare_sparse_km2", "cropland_km2"]

# All land cover classes output by the GEE script
ALL_LC_CLASSES = [
    "tree_cover_km2", "shrubland_km2", "grassland_km2", "cropland_km2",
    "built_up_km2", "bare_sparse_km2", "snow_ice_km2", "water_km2",
    "wetland_km2", "mangroves_km2", "moss_lichen_km2",
]

OUTPUT_FIELDS = [
    "source_id",
    "source_name",
    "country",
    "eprtr_activity",
    "latitude",
    "longitude",
    "co2_t",
    "annual_heat_demand_MWh_th",
    "heat_demand_provenance",
    "buffer_m",
    "total_buffer_area_km2",
    "masked_area_km2",        # area passing slope + WDPA mask
    "mask_coverage_pct",      # masked / buffer total (quality indicator)
    # individual land cover classes (all reported by GEE, for reference)
    "tree_cover_km2",
    "shrubland_km2",
    "grassland_km2",
    "cropland_km2",           # reported but excluded from suitable
    "built_up_km2",
    "bare_sparse_km2",
    "snow_ice_km2",
    "water_km2",
    "wetland_km2",
    "mangroves_km2",
    "moss_lichen_km2",
    # computed
    "suitable_land_km2",      # grassland + shrubland + bare_sparse (post-mask)
    "suitable_pct_of_buffer", # suitable / buffer total
    "suitable_classes_used",  # which classes were summed
    "data_source",
]


def load_facilities():
    """Load the 115 filtered facilities from eprtr_lcp_matched.csv."""
    rows = {}
    with open(INPUT_CSV, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r["match_method"] == "id_direct"
                    and r["eprtr_activity"] != "1(c)"
                    and r.get("eprtr_co2_year") == "2024"):
                rows[r["eprtr_facility_id"]] = r
    return rows


def load_lcoh_results():
    """Load heat demand from the off-grid LCOH results."""
    heat = {}
    if not os.path.exists(LCOH_CSV):
        return heat
    with open(LCOH_CSV, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            fid = r["eprtr_facility_id"]
            heat[fid] = {
                "annual_heat_demand_MWh_th": r.get("annual_heat_demand_MWh_th", ""),
                "heat_demand_provenance":    r.get("annual_heat_demand_provenance", ""),
            }
    return heat


def safe_float(v, default=0.0):
    try:
        return float(v) if v and str(v).strip() not in ("", "None", "nan") else default
    except (ValueError, TypeError):
        return default


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gee-output", required=True,
                   help="Path to the CSV exported from GEE Drive (land cover areas per facility).")
    args = p.parse_args(argv)

    if not os.path.exists(args.gee_output):
        print(f"ERROR: GEE output file not found: {args.gee_output}", file=sys.stderr)
        return 1

    facilities = load_facilities()
    heat_map   = load_lcoh_results()

    if not facilities:
        print("ERROR: No facilities matched the filter (id_direct, non-1c, co2_year=2024). "
              "Check inputs/eprtr_lcp_matched.csv.", file=sys.stderr)
        return 1

    print(f"Loaded {len(facilities)} facilities from eprtr_lcp_matched.csv")
    print(f"Loaded heat demand for {len(heat_map)} facilities from lcoh_results.csv")

    # Load GEE output
    gee_rows = {}
    with open(args.gee_output, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sid = r.get("source_id", "").strip()
            if sid:
                gee_rows[sid] = r

    print(f"Loaded {len(gee_rows)} rows from GEE output: {args.gee_output}")

    results = []
    unmatched_gee = 0
    unmatched_fac = 0

    for fid, fac in facilities.items():
        gee = gee_rows.get(fid)
        if gee is None:
            print(f"  WARNING: {fid} not found in GEE output — skipping")
            unmatched_gee += 1
            continue

        # Land cover areas (km²) from GEE — already post-mask (slope + WDPA)
        lc = {cls: safe_float(gee.get(cls, 0)) for cls in ALL_LC_CLASSES}

        masked_area_km2  = sum(lc.values())
        suitable_land_km2 = sum(lc[cls] for cls in SUITABLE_CLASSES)
        mask_coverage_pct = round(masked_area_km2 / BUFFER_KM2 * 100, 1) if BUFFER_KM2 > 0 else None
        suitable_pct      = round(suitable_land_km2 / BUFFER_KM2 * 100, 1) if BUFFER_KM2 > 0 else None

        heat = heat_map.get(fid, {})

        row = {
            "source_id":                  fid,
            "source_name":                fac.get("eprtr_facility_name", ""),
            "country":                    fac.get("eprtr_country", ""),
            "eprtr_activity":             fac.get("eprtr_activity", ""),
            "latitude":                   fac.get("eprtr_lat", ""),
            "longitude":                  fac.get("eprtr_lon", ""),
            "co2_t":                      fac.get("eprtr_co2_t", ""),
            "annual_heat_demand_MWh_th":  heat.get("annual_heat_demand_MWh_th", ""),
            "heat_demand_provenance":     heat.get("heat_demand_provenance", ""),
            "buffer_m":                   BUFFER_M,
            "total_buffer_area_km2":      round(BUFFER_KM2, 2),
            "masked_area_km2":            round(masked_area_km2, 4),
            "mask_coverage_pct":          mask_coverage_pct,
            "tree_cover_km2":             round(lc["tree_cover_km2"], 4),
            "shrubland_km2":              round(lc["shrubland_km2"], 4),
            "grassland_km2":              round(lc["grassland_km2"], 4),
            "cropland_km2":               round(lc["cropland_km2"], 4),
            "built_up_km2":               round(lc["built_up_km2"], 4),
            "bare_sparse_km2":            round(lc["bare_sparse_km2"], 4),
            "snow_ice_km2":               round(lc["snow_ice_km2"], 4),
            "water_km2":                  round(lc["water_km2"], 4),
            "wetland_km2":                round(lc["wetland_km2"], 4),
            "mangroves_km2":              round(lc["mangroves_km2"], 4),
            "moss_lichen_km2":            round(lc["moss_lichen_km2"], 4),
            "suitable_land_km2":          round(suitable_land_km2, 4),
            "suitable_pct_of_buffer":     suitable_pct,
            "suitable_classes_used":      "+".join(SUITABLE_CLASSES),
            "data_source":                "ESA_WorldCover_v200_2021_slope5deg_WDPA",
        }
        results.append(row)

    # Facilities in filter but not in GEE output
    gee_ids_found = set(gee_rows.keys())
    for fid in facilities:
        if fid not in gee_ids_found:
            unmatched_fac += 1

    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    print(f"\nWrote {len(results)} rows to {OUT_FILE}")
    print(f"  Facilities missing from GEE output:  {unmatched_gee}")
    print(f"  GEE rows with no facility match:     {len(gee_rows) - len(results)}")

    # Quick summary
    if results:
        suitable_vals = [r["suitable_land_km2"] for r in results if r["suitable_land_km2"] != ""]
        if suitable_vals:
            avg = sum(suitable_vals) / len(suitable_vals)
            total = sum(suitable_vals)
            zero_suitable = sum(1 for v in suitable_vals if v == 0)
            print(f"\n  Suitable land summary (grassland + shrubland + bare_sparse):")
            print(f"    Total across 115 sites:  {total:.1f} km²")
            print(f"    Average per site:        {avg:.1f} km²")
            print(f"    Sites with zero suitable: {zero_suitable}")
            print(f"    Buffer area per site:     {BUFFER_KM2:.1f} km² (10 km radius)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
