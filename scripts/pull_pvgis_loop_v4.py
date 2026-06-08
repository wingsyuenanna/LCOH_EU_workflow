# Anna Yuen
# last updated 2/8/2026
# Purpose: parallel processing to pull PVGIS data from a list of locations and store in parquet files. 
# PVGIS documentation: https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/getting-started-pvgis/api-non-interactive-service_en#ref-1-basics

from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.utils import ClientError
import requests
import pandas as pd
from io import StringIO
from timezonefinder import TimezoneFinder
import os 
from time import sleep
from pathlib import Path
import shutil
import logging
from datetime import datetime
import threading
import boto3
import io

_SOLAR_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SOLAR_ROOT.parent

# =============================================================================
# Logging Configuration
# =============================================================================
log_folder = _SOLAR_ROOT / "logs"
log_folder.mkdir(parents=True, exist_ok=True)
log_filename = log_folder / f"pvgis_pull_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("="*80)
logger.info("Starting PVGIS data pull script")
logger.info("="*80)

# =============================================================================
# Configuration
# =============================================================================
start_year = 2023
end_year = 2023
input_file = _REPO_ROOT / "flatblock_optimization" / "inputs" / "sites.csv"
# input_file = '/home/ec2-user/industrial_facility_db/Global_Energy_Monitor/gem_plant_level_all.csv'
output_folder = str(_SOLAR_ROOT / "solar_data_parquet_check")

lat_col = 'lat'
lon_col = 'lon'

S3_BUCKET = "annaiecc"
S3_PREFIX = "solar_data_parquet_check"
s3_client = boto3.client('s3')

OVERWRITE = False
CSV = False
PARQUET = True

data_folder = _SOLAR_ROOT / "solar_data_parquet_check"
data_folder.mkdir(parents=True, exist_ok=True)

# Check for existing profiles
existing_source_ids = [f.stem.split('_', 1)[0] for f in data_folder.iterdir() if f.is_file()]
logger.info(f"Found {len(existing_source_ids)} existing PV profiles")

logger.info(f"Configuration:")
logger.info(f"  - Years: {start_year} to {end_year}")
logger.info(f"  - Input file: {input_file}")
logger.info(f"  - Output folder: {output_folder}")
logger.info(f"  - Overwrite mode: {OVERWRITE}")
logger.info(f"  - CSV output: {CSV}")
logger.info(f"  - Parquet output: {PARQUET}")

# =============================================================================
# Load and Filter Sites
# =============================================================================
logger.info("Loading site data...")
sites_df = pd.read_csv(input_file)
original_count = len(sites_df)
logger.info(f"Loaded {original_count} total records from input file")

sites_df = sites_df[['source_id', 'source_name', lat_col, lon_col]].drop_duplicates(subset='source_id')
logger.info(f"After deduplication: {len(sites_df)} unique sites")

sites_df = sites_df.dropna(subset=[lat_col, lon_col])
logger.info(f"After removing missing coordinates: {len(sites_df)} sites")

if not OVERWRITE:
    sites_df = sites_df[~sites_df['source_id'].isin(existing_source_ids)]
    logger.info(f"Filtering out existing profiles: {len(sites_df)} sites remaining to process")

num_sites = sites_df.shape[0]
logger.info(f"Final site count to process: {num_sites}")

# =============================================================================
# Pre-compute Timezones
# =============================================================================
logger.info("Pre-computing timezones for all locations...")
tf = TimezoneFinder()

# Round coordinates to ~1km precision for caching
sites_df['lat_rounded'] = sites_df[lat_col].round(2)
sites_df['lon_rounded'] = sites_df[lon_col].round(2)

unique_coords = sites_df[['lat_rounded', 'lon_rounded']].drop_duplicates()
logger.info(f"Found {len(unique_coords)} unique coordinate pairs (from {num_sites} sites)")

# Build timezone cache
timezone_cache = {}
for idx, row in unique_coords.iterrows():
    lat = row['lat_rounded']
    lon = row['lon_rounded']
    tz = tf.timezone_at(lat=lat, lng=lon)
    timezone_cache[(lat, lon)] = tz

logger.info(f"✓ Timezone cache built with {len(timezone_cache)} entries")

# Map timezones to sites
sites_df['timezone'] = sites_df.apply(
    lambda row: timezone_cache.get((row['lat_rounded'], row['lon_rounded'])), 
    axis=1
)

# Log timezone distribution
tz_counts = sites_df['timezone'].value_counts()
logger.info(f"Timezone distribution (top 10):")
for tz, count in tz_counts.head(10).items():
    logger.info(f"  {tz}: {count} sites")

# Thread-safe counters and locks
active_tasks_lock = threading.Lock()
active_tasks = 0
parquet_write_lock = threading.Lock()

# =============================================================================
# PVGIS Data Fetching Function
# =============================================================================
def get_optimal_azimuth(lat):
    return 180 if lat < 0 else 0

def upload_to_s3(local_path, s3_path):
    """Uploads a file or directory to S3."""
    try:
        if os.path.isfile(local_path):
            s3_client.upload_file(str(local_path), S3_BUCKET, s3_path)
        elif os.path.isdir(local_path):
            for root, dirs, files in os.walk(local_path):
                for file in files:
                    local_file = Path(root) / file
                    # Create relative path for S3
                    relative_path = local_file.relative_to(local_path.parent)
                    s3_key = f"{S3_PREFIX}/{relative_path}"
                    s3_client.upload_file(str(local_file), S3_BUCKET, s3_key)
        return True
    except ClientError as e:
        logger.error(f"S3 Upload Failed: {e}")
        return False

def fetch_pvgis_data(row, start_year, end_year, data_folder, overwrite, csv_output, parquet_output, timezone_cache):
    """Fetch and process PVGIS solar generation data for a single site."""
    global active_tasks
    
    source_id = row['source_id']
    source_name = row['source_name']
    lat = float(row[lat_col])
    lon = float(row[lon_col])
    lat_rounded = row['lat_rounded']
    lon_rounded = row['lon_rounded']
    
    # Track active tasks
    with active_tasks_lock:
        active_tasks += 1
        current_active = active_tasks
    
    logger.info(f"[{source_id}] 🔄 Started processing {source_name} (lat={lat:.4f}, lon={lon:.4f}) | Active tasks: {current_active}")
    
    try:
        # Build PVGIS API request
        url = "https://re.jrc.ec.europa.eu/api/seriescalc"
        params = {
            lat_col: lat,
            lon_col: lon,
            'raddatabase': 'PVGIS-ERA5',
            'pvcalculation': 1,
            'peakpower': 1,
            'loss': 0,
            'angle': abs(lat),
            'aspect': get_optimal_azimuth(lat),
            'startyear': start_year,
            'endyear': end_year,
            'outputformat': 'csv'
        }
        
        # Fetch data from API
        try:
            logger.debug(f"[{source_id}] Sending API request...")
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            logger.info(f"[{source_id}] ✓ API request successful")
        except Exception as e:
            logger.error(f"[{source_id}] API request failed: {str(e)}")
            return {'success': False, 'source_id': source_id, 'source_name': source_name, 'error': str(e)}
        
        # Parse CSV response
        lines = r.text.splitlines()
        header_line = None
        for i, line in enumerate(lines):
            if line.startswith("time,"):
                header_line = i
                break
        
        if header_line is None:
            logger.error(f"[{source_id}] No CSV header found in response")
            return {'success': False, 'source_id': source_id, 'source_name': source_name, 'error': 'No CSV header found'}
        
        header_lines = lines[:header_line]
        csv_text = "\n".join(lines[header_line:])
        df = pd.read_csv(StringIO(csv_text))
        
        logger.info(f"[{source_id}] Parsed {len(df)} rows from CSV")
        
        # Validate required columns
        if 'time' not in df.columns or 'P' not in df.columns:
            logger.error(f"[{source_id}] Missing required columns. Available: {df.columns.tolist()}")
            return {'success': False, 'source_id': source_id, 'source_name': source_name, 'error': 'Missing columns'}
        
        # Convert timestamps to UTC
        logger.info(f"[{source_id}] Converting timestamps...")
        df['time'] = pd.to_datetime(df['time'], format='%Y%m%d:%H%M', errors='coerce')
        df = df.dropna(subset=['time']).set_index('time').tz_localize('UTC')
        
        # Convert to local timezone
        local_tz = timezone_cache.get((lat_rounded, lon_rounded))
        if local_tz:
            logger.info(f"[{source_id}] Converting to timezone: {local_tz}")
            df = df.tz_convert(local_tz)
            df.index = df.index.tz_localize(None)
        else:
            logger.warning(f"[{source_id}] No timezone found in cache, keeping UTC")
        
        # Round to nearest hour and remove duplicates
        logger.info(f"[{source_id}] Rounding timestamps to nearest hour...")
        df.index = df.index.floor('h')
        
        duplicates_before = df.index.duplicated().sum()
        if duplicates_before > 0:
            logger.info(f"[{source_id}] Found {duplicates_before} duplicate timestamps, keeping first...")
            df = df[~df.index.duplicated(keep='first')]
            logger.info(f"[{source_id}] ✓ Deduplication complete, {len(df)} unique timestamps")
        
        # Filter to target year
        logger.info(f"[{source_id}] Filtering to year {start_year}...")
        rows_before_filter = len(df)
        df = df[df.index.year == start_year]
        logger.info(f"[{source_id}] Filtered: {len(df)} rows (from {rows_before_filter})")
        
        # Normalize PV output (W to kW per kWp)
        logger.info(f"[{source_id}] Normalizing PV output...")
        df['P_kWperkWp'] = pd.to_numeric(df['P'], errors='coerce') / 1000.0
        df = df[['P_kWperkWp']]
        
        # Reindex to complete hourly range
        logger.info(f"[{source_id}] Reindexing to full hourly range...")
        full_range = pd.date_range(
            start=f"{start_year}-01-01 00:00:00", 
            end=f"{end_year}-12-31 23:00:00", 
            freq='h'
        )
        df = df.reindex(full_range).fillna(0)
        df.index.name = 'timestamp'
        
        # Add metadata columns
        df = df.reset_index().rename(columns={"index": "timestamp"})
        df["source_id"] = source_id
        df["source_name"] = source_name
        df["year"] = df["timestamp"].dt.year
        df = df[["source_id", "source_name", "year", "timestamp", "P_kWperkWp"]]
        
        logger.info(f"[{source_id}] Final dataframe: {df.shape}")
        
        # Write output files (with lock to prevent concurrent write issues)
        logger.info(f"[{source_id}] Waiting for file write lock...")
        with parquet_write_lock:
            logger.debug(f"[{source_id}] File write lock acquired")
            
            # Remove existing partition if overwriting
            if overwrite:
                partition_path = data_folder / f"source_id={source_id}" / f"year={start_year}"
                if partition_path.exists():
                    logger.debug(f"[{source_id}] Removing existing partition...")
                    shutil.rmtree(partition_path)
            
            if parquet_output:
                try:
                    logger.info(f"[{source_id}] Preparing S3 upload (In-Memory)...")
                    
                    parquet_buffer = io.BytesIO()
                    
                    df.to_parquet(parquet_buffer, engine="pyarrow", compression="zstd", index=False)
                    
                    # Matches the Hive partitioning style: source_id=X/year=Y/file.parquet
                    s3_key = f"{S3_PREFIX}/source_id={source_id}/year={start_year}/{source_id}_{start_year}.parquet"
                    
                    s3_client.put_object(
                        Bucket=S3_BUCKET,
                        Key=s3_key,
                        Body=parquet_buffer.getvalue()
                    )
                    
                    logger.info(f"[{source_id}] ✅ Successfully uploaded to s3://{S3_BUCKET}/{s3_key}")
                    
                except Exception as e:
                    logger.error(f"[{source_id}] S3 Upload failed: {str(e)}")
                    return {'success': False, 'source_id': source_id, 'error': f'S3 failure: {str(e)}'}
            
            # Write CSV file
            if csv_output:
                try:
                    output_file = os.path.join(output_folder, f"{source_id}_pv_profile_{start_year}-{end_year}.csv")
                    logger.debug(f"[{source_id}] Writing CSV file...")
                    with open(output_file, "w") as f:
                        f.write(f"plant name: {source_id}\n")
                        for line in header_lines:
                            f.write(line + "\n")
                        df.to_csv(f)
                    logger.info(f"[{source_id}] ✓ CSV written")
                except Exception as e:
                    logger.error(f"[{source_id}] CSV write failed: {str(e)}")
                    return {'success': False, 'source_id': source_id, 'source_name': source_name, 'error': f'CSV write failed: {str(e)}'}
        
        logger.info(f"[{source_id}] ✅ Completed")
        return {'success': True, 'source_id': source_id, 'source_name': source_name}
    
    except Exception as e:
        logger.error(f"[{source_id}] Unexpected error: {str(e)}", exc_info=True)
        return {'success': False, 'source_id': source_id, 'source_name': source_name, 'error': f'Unexpected error: {str(e)}'}
    
    finally:
        with active_tasks_lock:
            active_tasks -= 1
            final_active = active_tasks
        logger.debug(f"[{source_id}] Task finished | Active tasks: {final_active}")

# =============================================================================
# Parallel Processing
# =============================================================================
max_workers = 10
logger.info(f"Starting parallel processing with {max_workers} workers...")
start_time = datetime.now()

successful = 0
failed = 0
errors = []

with ThreadPoolExecutor(max_workers=max_workers) as executor:
    logger.info(f"Submitting {num_sites} tasks to executor...")
    
    # Submit all tasks
    future_to_site = {
        executor.submit(
            fetch_pvgis_data, 
            row, 
            start_year, 
            end_year, 
            data_folder, 
            OVERWRITE, 
            CSV, 
            PARQUET,
            timezone_cache
        ): (row['source_id'], row['source_name'])
        for idx, row in sites_df.iterrows()
    }
    
    logger.info(f"All {num_sites} tasks submitted")
    
    # Process completed tasks
    completed = 0
    for future in as_completed(future_to_site):
        completed += 1
        remaining = num_sites - completed
        result = future.result()
        
        # Calculate progress metrics
        elapsed = datetime.now() - start_time
        avg_time_per_site = elapsed / completed if completed > 0 else elapsed
        estimated_remaining = avg_time_per_site * remaining
        
        with active_tasks_lock:
            current_active = active_tasks
        
        # Track results
        if result['success']:
            successful += 1
            print(f"[{completed}/{num_sites}] ✓ {result['source_id']} {result.get('source_name', '')} | {remaining} left | Active: {current_active}/{max_workers} | ETA: {estimated_remaining}")
        else:
            failed += 1
            error_info = {
                'source_id': result['source_id'],
                'source_name': result.get('source_name', ''),
                'error': result['error']
            }
            errors.append(error_info)
            print(f"[{completed}/{num_sites}] ✗ {result['source_id']} failed: {result['error']} | {remaining} left | Active: {current_active}/{max_workers} | ETA: {estimated_remaining}")
        
        # Log periodic progress milestones
        if completed % max(1, num_sites // 10) == 0 or completed % 50 == 0:
            logger.info("="*60)
            logger.info(f"MILESTONE: {completed}/{num_sites} sites processed ({100*completed/num_sites:.1f}%)")
            logger.info(f"  Remaining: {remaining} sites")
            logger.info(f"  Active tasks: {current_active}/{max_workers}")
            logger.info(f"  Successful: {successful}")
            logger.info(f"  Failed: {failed}")
            logger.info(f"  Elapsed: {elapsed}")
            logger.info(f"  ETA: {estimated_remaining}")
            logger.info("="*60)

# =============================================================================
# Summary
# =============================================================================
end_time = datetime.now()
duration = end_time - start_time

logger.info("="*80)
logger.info("Processing complete!")
logger.info(f"Total sites processed: {completed}")
logger.info(f"Successful: {successful}")
logger.info(f"Failed: {failed}")
logger.info(f"Success rate: {100*successful/completed:.1f}%")
logger.info(f"Total time: {duration}")
logger.info(f"Average time per site: {duration / num_sites if num_sites > 0 else 0}")
logger.info("="*80)

# Save error log if there were failures
if errors:
    logger.warning(f"Failed sites ({len(errors)}):")
    for error in errors:
        logger.warning(f"  - {error['source_id']} ({error['source_name']}): {error['error']}")
    
    error_file = log_folder / f"errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    pd.DataFrame(errors).to_csv(error_file, index=False)
    logger.info(f"Error details saved to: {error_file}")

logger.info(f"Log file saved to: {log_filename}")
print(f"\nAll processing complete! Check log file at: {log_filename}")