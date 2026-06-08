"""
PVGIS pull for the 115 id_direct / non-1(c) / 2024 facilities.

Calls the PVGIS ERA5 API in parallel (10 workers), saves:
  - outputs/pvgis_solar_cf_115.csv   — one row per site, annual capacity factor
  - outputs/pvgis_hourly/            — one CSV per site, 8760 hourly kW/kWp rows

Adapted from pull_pvgis_loop_v4.py; S3 and parquet removed; local output only.
"""

import csv
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import StringIO
from pathlib import Path
from time import sleep

import pandas as pd
import requests
from timezonefinder import TimezoneFinder

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent          # off_grid_electricity_run/
REPO_ROOT  = BASE_DIR.parent
INPUT_CSV  = REPO_ROOT / "inputs" / "gee_facilities_115.csv"
OUT_DIR    = BASE_DIR / "outputs"
HOURLY_DIR = OUT_DIR / "pvgis_hourly"
CF_CSV     = OUT_DIR / "pvgis_solar_cf_115.csv"
LOG_DIR    = BASE_DIR / "outputs"

OUT_DIR.mkdir(parents=True, exist_ok=True)
HOURLY_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ────────────────────────────────────────────────────────────────────
PVGIS_YEAR  = 2023          # most recent full year in PVGIS-ERA5
MAX_WORKERS = 10
RETRY_LIMIT = 3
RETRY_DELAY = 5             # seconds between retries

# ── Logging ───────────────────────────────────────────────────────────────────
log_file = LOG_DIR / f"pvgis_pull_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── Thread-safe state ─────────────────────────────────────────────────────────
_lock = threading.Lock()
_write_lock = threading.Lock()
_active = 0


def _get_existing():
    # Reverse the slash-sanitization to recover original source_ids
    return {f.stem.replace("_", "/", 2) for f in HOURLY_DIR.glob("*.csv")}


def fetch_one(row, tz_cache, year, existing):
    global _active
    source_id   = row["source_id"]
    source_name = row["source_name"]
    lat         = float(row["latitude"])
    lon         = float(row["longitude"])
    lat_r       = round(lat, 2)
    lon_r       = round(lon, 2)

    with _lock:
        _active += 1
        active_now = _active

    log.info(f"[{source_id}] start  lat={lat:.4f} lon={lon:.4f}  active={active_now}")

    try:
        params = {
            "lat":          lat,
            "lon":          lon,
            "raddatabase":  "PVGIS-ERA5",
            "pvcalculation": 1,
            "peakpower":    1,
            "loss":         0,
            "angle":        abs(lat),
            "aspect":       0 if lat >= 0 else 180,
            "startyear":    year,
            "endyear":      year,
            "outputformat": "csv",
        }

        resp = None
        for attempt in range(1, RETRY_LIMIT + 1):
            try:
                resp = requests.get(
                    "https://re.jrc.ec.europa.eu/api/seriescalc",
                    params=params, timeout=60,
                )
                resp.raise_for_status()
                break
            except Exception as e:
                log.warning(f"[{source_id}] attempt {attempt} failed: {e}")
                if attempt < RETRY_LIMIT:
                    sleep(RETRY_DELAY)
                else:
                    return {"source_id": source_id, "source_name": source_name,
                            "success": False, "error": str(e)}

        # Parse response — find the "time," header line
        lines = resp.text.splitlines()
        header_idx = next((i for i, l in enumerate(lines) if l.startswith("time,")), None)
        if header_idx is None:
            return {"source_id": source_id, "source_name": source_name,
                    "success": False, "error": "no CSV header in response"}

        df = pd.read_csv(StringIO("\n".join(lines[header_idx:])))
        df["time"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M", errors="coerce")
        df = df.dropna(subset=["time"]).set_index("time").tz_localize("UTC")

        # Convert to local time (affects hourly profile alignment, not annual sum)
        local_tz = tz_cache.get((lat_r, lon_r))
        if local_tz:
            df = df.tz_convert(local_tz)
        df.index = df.index.tz_localize(None).floor("h")
        df = df[~df.index.duplicated(keep="first")]
        df = df[df.index.year == year]

        # Normalise: W → kW per kWp
        df["P_kWperkWp"] = pd.to_numeric(df["P"], errors="coerce") / 1000.0

        # Reindex to full 8760 hours — select column first so pandas 2.x
        # fill doesn't reject int 0 on mixed-type DataFrame
        full_range = pd.date_range(f"{year}-01-01", periods=8760, freq="h")
        df = df[["P_kWperkWp"]].reindex(full_range).fillna(0.0)
        df.index.name = "timestamp"

        annual_kwh_per_kwp = df["P_kWperkWp"].sum()          # kWh/kWp/yr
        annual_cf          = annual_kwh_per_kwp / 8760        # dimensionless

        # Write hourly CSV — sanitize source_id to remove path-unsafe chars
        safe_id = source_id.replace("/", "_").replace("\\", "_")
        hourly_path = HOURLY_DIR / f"{safe_id}.csv"
        with _write_lock:
            df.reset_index().assign(source_id=source_id, source_name=source_name)\
              .to_csv(hourly_path, index=False)

        log.info(f"[{source_id}] done  CF={annual_cf:.4f}  kWh/kWp={annual_kwh_per_kwp:.1f}")
        return {
            "source_id":          source_id,
            "source_name":        source_name,
            "latitude":           lat,
            "longitude":          lon,
            "pvgis_year":         year,
            "annual_kwh_per_kwp": round(annual_kwh_per_kwp, 2),
            "annual_cf":          round(annual_cf, 6),
            "hourly_file":        str(hourly_path),
            "success":            True,
            "error":              "",
        }

    except Exception as e:
        log.error(f"[{source_id}] unexpected error: {e}", exc_info=True)
        return {"source_id": source_id, "source_name": source_name,
                "success": False, "error": str(e)}
    finally:
        with _lock:
            _active -= 1


def main():
    log.info("=" * 70)
    log.info("PVGIS pull — 115 EPRTR facilities")
    log.info(f"  Input:  {INPUT_CSV}")
    log.info(f"  Output: {CF_CSV}")
    log.info(f"  Year:   {PVGIS_YEAR}")
    log.info("=" * 70)

    sites = pd.read_csv(INPUT_CSV)
    sites = sites.dropna(subset=["latitude", "longitude"]).drop_duplicates("source_id")
    log.info(f"Loaded {len(sites)} sites")

    # Skip sites already completed
    existing = _get_existing()
    pending  = sites[~sites["source_id"].isin(existing)]
    skipped  = len(sites) - len(pending)
    if skipped:
        log.info(f"Skipping {skipped} already-complete sites; {len(pending)} remaining")

    # Build timezone cache
    tf = TimezoneFinder()
    tz_cache = {}
    for _, r in pending.iterrows():
        key = (round(float(r["latitude"]), 2), round(float(r["longitude"]), 2))
        if key not in tz_cache:
            tz_cache[key] = tf.timezone_at(lat=key[0], lng=key[1])
    log.info(f"Timezone cache: {len(tz_cache)} unique coordinates")

    results = []
    n = len(pending)
    start = datetime.now()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(fetch_one, row, tz_cache, PVGIS_YEAR, existing): row["source_id"]
            for _, row in pending.iterrows()
        }
        for done_i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            elapsed = datetime.now() - start
            eta = elapsed / done_i * (n - done_i)
            status = "OK" if result["success"] else "FAIL"
            log.info(f"[{done_i}/{n}] {status}  {result['source_id']}  ETA {eta}")

    # Write summary CSV
    ok_rows  = [r for r in results if r["success"]]
    err_rows = [r for r in results if not r["success"]]

    summary_fields = [
        "source_id", "source_name", "latitude", "longitude",
        "pvgis_year", "annual_kwh_per_kwp", "annual_cf", "hourly_file", "error",
    ]
    with open(CF_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=summary_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    log.info("=" * 70)
    log.info(f"Complete: {len(ok_rows)} OK  {len(err_rows)} failed")
    if ok_rows:
        cfs = [r["annual_cf"] for r in ok_rows]
        log.info(f"  CF range: {min(cfs):.3f} – {max(cfs):.3f}  avg {sum(cfs)/len(cfs):.3f}")
    log.info(f"Summary CSV: {CF_CSV}")
    log.info(f"Hourly CSVs: {HOURLY_DIR}")
    if err_rows:
        log.warning(f"Failed sites:")
        for r in err_rows:
            log.warning(f"  {r['source_id']}: {r['error']}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
