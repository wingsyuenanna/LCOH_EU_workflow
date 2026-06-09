#!/bin/bash
#
# Run flatblock for every site under scenarios/<SCENARIO_NUM>/, then upload to S3.
#
# Sample commands (run from ``flatblock_optimization/``):
#   # 1) Generate jobscripts first
#   python generate_scenarios.py --scenario-name sites_heat_battery_mfg \
#     --storage-type heat --heat-energy-capex-per-mwh 15 \
#     --scenarios-csv inputs/sites_usa_mfg.csv --sites-csv inputs/sites_usa_mfg.csv
#
#   # 2) Choose scenario + concurrency and run all jobs
#   SCENARIO_NUM=sites_heat_battery_mfg MAX_JOBS=8 bash run_all_jobs.sh
#
# Typical flow (from this directory, ``flatblock_optimization/``):
#   python generate_scenarios.py --scenarios-csv inputs/sites.csv --sites-csv inputs/sites.csv
#   MAX_JOBS=8 bash run_all_jobs.sh
#
# US + thermal storage example: ``python inputs/compile_us_sites.py -o inputs/sites_usa.csv`` then
#   ``python generate_scenarios.py --scenario-name heat_battery_v1 --storage-type heat --scenarios-csv inputs/sites_usa.csv --sites-csv inputs/sites_usa.csv``
#   Set SCENARIO_NUM=heat_battery_v1 below.
#
# S3 destination (defaults): s3://annaiecc/flatblock_results/<SCENARIO_NUM>/scenario_<source_id>/
# Override: FLATBLOCK_S3_BUCKET, FLATBLOCK_S3_PREFIX, or pass flags to utils/s3/upload_scenario_to_s3.py manually.
# Re-upload overwrites the same keys; other source_ids left from older runs are not deleted automatically.
#
# To re-run a site that already has results, remove that folder's ``results/`` (or the whole scenario_* dir) first.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Define the base directory where your scenario folders live
BASE_DIR="scenarios"

# Set the scenario number to process (can override with SCENARIO_NUM env var)
SCENARIO_NUM=${SCENARIO_NUM:-eu_heat_tes_v1}

# Max number of parallel jobs (can override with environment variable MAX_JOBS)
MAX_JOBS=${MAX_JOBS:-24}

# Record script start time (for elapsed / ETA)
START_TIME=$(date +%s)

echo "Using MAX_JOBS=$MAX_JOBS on SCENARIO_NUM='$SCENARIO_NUM'."

# Helper: return 0 if results already exist for this scenario
has_results() {
    local scenario_dir="$1"
    local results_dir="$scenario_dir/results"

    if [ ! -d "$results_dir" ]; then
        return 1
    fi

    # Consider a scenario "done" if it has any summary/hourly/INFEASIBLE file
    if compgen -G "$results_dir/summary_site*.csv" > /dev/null 2>&1; then
        return 0
    fi
    if compgen -G "$results_dir/hourly_site*.csv" > /dev/null 2>&1; then
        return 0
    fi
    if compgen -G "$results_dir/INFEASIBLE_site*.txt" > /dev/null 2>&1; then
        return 0
    fi

    return 1
}

# First, count how many scenarios we will actually run (skip those with existing results)
TOTAL=0
# Site runs: scenario_<source_id>/ (also supports legacy scenario_<SCENARIO_NUM>_<source_id>/)
for scenario_dir in "$BASE_DIR"/${SCENARIO_NUM}/scenario_*; do
    if [ -d "$scenario_dir" ] && [ -f "$scenario_dir/jobscript.sh" ] && ! has_results "$scenario_dir"; then
        TOTAL=$((TOTAL + 1))
    fi
done

if [ "$TOTAL" -eq 0 ]; then
    echo "No pending scenarios to run for SCENARIO_NUM='$SCENARIO_NUM' under '$BASE_DIR' (all have results or none found)."
    echo "To upload existing results to S3: python utils/s3/upload_scenario_to_s3.py upload $SCENARIO_NUM"
    exit 0
fi

echo "Found $TOTAL pending scenarios for SCENARIO_NUM='$SCENARIO_NUM'."

COUNT=0          # how many jobs have been *started*
# FIFO of background PIDs (macOS ships Bash 3.2 — no ``wait -n``)
PIDS=""

# Temp file used as a simple shared counter across background jobs
PROGRESS_FILE="$(mktemp -t flatblock_progress.XXXXXX)"
echo 0 > "$PROGRESS_FILE"

# Helper to format seconds as H:MM:SS
format_duration() {
    local sec=$1
    local h=$((sec/3600))
    local m=$(((sec%3600)/60))
    local s=$((sec%60))
    printf "%d:%02d:%02d" "$h" "$m" "$s"
}

run_job() {
    local scenario_dir="$1"
    local folder_name="$2"
    local log_dir="$3"

    bash "$scenario_dir/jobscript.sh" > "$log_dir/out.log" 2> "$log_dir/err.log"

    # Safely update the shared completed counter and print a progress line
    {
        flock 9
        local current
        current=$(cat "$PROGRESS_FILE")
        current=$((current + 1))
        echo "$current" > "$PROGRESS_FILE"

        # Elapsed time since script start
        local now elapsed eta_per_job remaining eta_seconds
        now=$(date +%s)
        elapsed=$((now - START_TIME))

        # Simple ETA: average time per completed job * remaining
        if [ "$current" -gt 0 ]; then
            eta_per_job=$(( elapsed / current ))
            remaining=$(( TOTAL - current ))
            if [ "$remaining" -gt 0 ]; then
                eta_seconds=$(( eta_per_job * remaining ))
            else
                eta_seconds=0
            fi
        else
            eta_seconds=0
        fi

        local elapsed_str eta_str
        elapsed_str=$(format_duration "$elapsed")
        eta_str=$(format_duration "$eta_seconds")

        echo "Finished: $folder_name ($current/$TOTAL) | elapsed=${elapsed_str} | ETA≈${eta_str}"
    } 9<>"$PROGRESS_FILE"
}

for scenario_dir in "$BASE_DIR"/${SCENARIO_NUM}/scenario_*; do
    if [ -d "$scenario_dir" ] && [ -f "$scenario_dir/jobscript.sh" ]; then
        folder_name=$(basename "$scenario_dir")

        if has_results "$scenario_dir"; then
            echo "Skipping (results exist): $folder_name"
            continue
        fi

        LOG_DIR="$scenario_dir/logs"
        mkdir -p "$LOG_DIR"

        echo "Starting: $folder_name ($((COUNT + 1))/$TOTAL)"

        # Stay under MAX_JOBS by waiting for the oldest started job (FIFO)
        while [ "$(echo "$PIDS" | wc -w | tr -d ' ')" -ge "$MAX_JOBS" ]; do
            fpid=$(echo "$PIDS" | cut -d' ' -f1)
            rest=$(echo "$PIDS" | cut -s -d' ' -f2-)
            wait "$fpid"
            PIDS=$rest
        done

        run_job "$scenario_dir" "$folder_name" "$LOG_DIR" &
        pid=$!
        if [ -z "$PIDS" ]; then
            PIDS=$pid
        else
            PIDS="$PIDS $pid"
        fi
        COUNT=$((COUNT + 1))
    fi
done

# Wait for any remaining background jobs
for fpid in $PIDS; do
    wait "$fpid"
done

COMPLETED=$(cat "$PROGRESS_FILE")
rm -f "$PROGRESS_FILE"

echo "All pending scenarios processed. ($COMPLETED/$TOTAL)"

# Upload all results for this scenario to S3 (same folder layout)
if [ -n "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
    echo "Uploading scenario '$SCENARIO_NUM' results to S3..."
    if python "$SCRIPT_DIR/utils/s3/upload_scenario_to_s3.py" upload "$SCENARIO_NUM"; then
        echo "S3 upload completed."
    else
        echo "S3 upload failed (check AWS credentials and FLATBLOCK_S3_BUCKET if set)." >&2
    fi
fi