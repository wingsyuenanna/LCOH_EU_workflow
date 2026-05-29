"""
research_agent.py
-----------------
EU Off-Grid Solar + BESS Manufacturing Site Research Agent
Runs on AWS EC2, executes the research spec, writes structured outputs,
sends progress updates via Slack, and uploads results to S3.

Usage:
    python3 research_agent.py

Requirements:
    pip install anthropic boto3 requests python-dotenv
"""

import os
import json
import re
import sys
import time
import csv
import traceback
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

import anthropic
import boto3
import requests

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

load_dotenv()  # reads from .env file in the same directory

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")
SLACK_WEBHOOK_URL   = os.getenv("SLACK_WEBHOOK_URL")   # optional — leave blank to skip
S3_BUCKET_NAME      = os.getenv("S3_BUCKET_NAME")      # optional — leave blank to skip
S3_OUTPUT_PREFIX    = os.getenv("S3_OUTPUT_PREFIX", "eu-solar-research/")
AWS_REGION          = os.getenv("AWS_REGION", "eu-west-1")

MODEL               = "claude-sonnet-4-20250514"
MAX_TOKENS          = 8000
OUTPUT_DIR          = Path("outputs")
SPEC_FILE           = Path("eu_offgrid_solar_research_spec.md")
CHECKPOINT_FILE     = OUTPUT_DIR / "checkpoint.json"

# Marker Claude must emit when it cannot access a required official dataset
DATASET_UNAVAILABLE_MARKER = "DATASET_UNAVAILABLE"

# ─────────────────────────────────────────────
# OUTPUT FILE PATHS
# ─────────────────────────────────────────────

OUTPUT_DIR.mkdir(exist_ok=True)

FILES = {
    "findings":      OUTPUT_DIR / "findings_table.csv",
    "sources":       OUTPUT_DIR / "sources_log.md",
    "verification":  OUTPUT_DIR / "verification_report.md",
    "process_log":   OUTPUT_DIR / "process_log.txt",
    "summary":       OUTPUT_DIR / "summary_report.md",
}

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def log(step: str, status: str, detail: str = ""):
    """Write a timestamped entry to process_log.txt and print to console."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{timestamp}] {step} | {status}"
    if detail:
        line += f" | {detail}"
    print(line)
    with open(FILES["process_log"], "a") as f:
        f.write(line + "\n")


def notify(message: str):
    """Send a Slack notification. Silently skips if no webhook is configured."""
    if not SLACK_WEBHOOK_URL:
        return
    try:
        requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": f":satellite: *Research Agent* | {message}"},
            timeout=5,
        )
    except Exception as e:
        log("SLACK", "WARNING", f"Notification failed: {e}")


# ─────────────────────────────────────────────
# S3 UPLOAD
# ─────────────────────────────────────────────

def upload_to_s3(local_path: Path):
    """Upload a file to S3. Silently skips if no bucket is configured."""
    if not S3_BUCKET_NAME:
        return
    try:
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3_key = S3_OUTPUT_PREFIX + local_path.name
        s3.upload_file(str(local_path), S3_BUCKET_NAME, s3_key)
        log("S3", "UPLOADED", f"s3://{S3_BUCKET_NAME}/{s3_key}")
    except Exception as e:
        log("S3", "ERROR", str(e))


def upload_all_outputs():
    for path in FILES.values():
        if path.exists():
            upload_to_s3(path)


# ─────────────────────────────────────────────
# PAUSE / RESUME
# ─────────────────────────────────────────────

class PauseException(Exception):
    """Raised when the agent must stop and wait for manual restart."""
    def __init__(self, reason: str, phase: str = ""):
        self.reason = reason
        self.phase = phase
        super().__init__(reason)


def save_checkpoint(phase_outputs: dict, paused_at: str, reason: str):
    checkpoint = {
        "paused_at": paused_at,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "completed_phases": list(phase_outputs.keys()),
        "phase_outputs": phase_outputs,
    }
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2)
    log("CHECKPOINT", "SAVED", str(CHECKPOINT_FILE))


def load_checkpoint() -> dict | None:
    if not CHECKPOINT_FILE.exists():
        return None
    try:
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        log("CHECKPOINT", "LOADED", f"Resuming from '{data['paused_at']}' — reason: {data['reason']}")
        return data
    except Exception as e:
        log("CHECKPOINT", "ERROR", f"Could not load checkpoint: {e}")
        return None


def clear_checkpoint():
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        log("CHECKPOINT", "CLEARED", "All phases complete — checkpoint removed")


def pause_and_exit(phase_name: str, reason: str, phase_outputs: dict):
    """Save state, notify, and exit so the user can restart to resume."""
    save_checkpoint(phase_outputs, phase_name, reason)
    msg = (
        f":pause_button: *Research agent PAUSED* at *{phase_name}*\n"
        f"Reason: {reason}\n"
        f"Completed phases saved. Restart the script to resume."
    )
    log(phase_name, "PAUSED", reason)
    notify(msg)
    sys.exit(0)


# ─────────────────────────────────────────────
# ANTHROPIC CLIENT (with web search)
# ─────────────────────────────────────────────

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
}


def call_claude(system_prompt: str, user_prompt: str, use_web_search: bool = True) -> str:
    """
    Call the Claude API and return the full text response.
    Handles multi-block responses (text + tool_use blocks).
    """
    tools = [WEB_SEARCH_TOOL] if use_web_search else []

    kwargs = dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if tools:
        kwargs["tools"] = tools

    response = client.messages.create(**kwargs)

    # Collect all text blocks from the response
    text_parts = [
        block.text
        for block in response.content
        if hasattr(block, "text")
    ]
    return "\n".join(text_parts).strip()


# ─────────────────────────────────────────────
# SPEC LOADER
# ─────────────────────────────────────────────

def load_spec() -> str:
    """Load the research spec markdown file."""
    if not SPEC_FILE.exists():
        raise FileNotFoundError(
            f"Research spec not found at {SPEC_FILE}. "
            "Place eu_offgrid_solar_research_spec.md in the same directory."
        )
    return SPEC_FILE.read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# RESEARCH PHASES
# Each function = one phase from the spec.
# Returns the raw text output from the agent.
# ─────────────────────────────────────────────

SYSTEM_BASE = """You are a specialist research agent executing a structured research spec
for an academic study on EU off-grid solar PV and battery energy storage (BESS) potential
for manufacturing plants. Your role is to:
- Search for and extract data only from approved primary sources (PVGIS, IRENA, JRC, EEA, ENTSO-E, peer-reviewed journals)
- Record the exact source, publication date, and page/figure reference for every data point
- Never fabricate or infer quantitative values — if data is unavailable, say so explicitly
- Flag any data that is older than 5 years or comes from a secondary source
- Follow the research spec exactly
"""


def phase_1_resource_data(spec: str) -> str:
    log("PHASE 1", "START", "Solar resource data collection")
    notify("Phase 1 started: Solar resource data collection")

    prompt = f"""
You are executing Phase 1 of the following research spec:

{spec}

TASK — Phase 1: Resource Data Collection
Search PVGIS, Solargis, and IRENA for annual GHI (kWh/m²/yr) and solar PV capacity
factor data across EU NUTS-2 regions.

For each region where data is available, return a structured list with:
- Region name and NUTS-2 code
- GHI (kWh/m²/yr)
- Capacity factor (%)
- Source name, URL, and publication date
- Any divergence between PVGIS and IRENA values (flag if >10%)

Search queries to use:
1. PVGIS EU solar radiation NUTS-2 regions
2. IRENA EU solar capacity factor by country 2023 2024
3. Solargis GHI Europe regional annual irradiance

Return results in a clearly labelled markdown table followed by a "Flags" section
listing any divergences or missing data.
"""
    result = call_claude(SYSTEM_BASE, prompt, use_web_search=True)
    log("PHASE 1", "COMPLETE")
    return result


def phase_2_offgrid_literature(spec: str) -> str:
    log("PHASE 2", "START", "Off-grid potential literature search")
    notify("Phase 2 started: Off-grid literature search")

    prompt = f"""
You are executing Phase 2 of the following research spec:

{spec}

TASK — Phase 2: Off-Grid Potential Literature Search
Search for peer-reviewed papers and institutional reports on off-grid solar PV + BESS
potential for industrial or manufacturing use in the EU.

Use the following search queries (run each and log results):
1. "off-grid solar" "manufacturing" EU OR Europe
2. "solar PV" "BESS" "industrial" "off-grid" NUTS OR region
3. "microgrid" "manufacturing plant" "solar" Europe LCOE
4. "distributed solar" "battery storage" "industrial load" EU

For each relevant source found, extract:
- Region or country covered
- Published off-grid potential (GWh/yr or GW installable)
- BESS sizing ratio (hours of storage or MWh/MW)
- LCOE if reported (€/MWh, note year and WACC)
- Full citation (authors, title, journal/institution, year, DOI or URL)

Return as a structured markdown table per source, followed by a list of sources that
could not be traced to a primary reference (mark these [UNVERIFIED — SECONDARY ONLY]).
"""
    result = call_claude(SYSTEM_BASE, prompt, use_web_search=True)
    log("PHASE 2", "COMPLETE")
    return result


def phase_3_land_and_grid(spec: str) -> str:
    log("PHASE 3", "START", "Land and grid constraint mapping")
    notify("Phase 3 started: Land and grid constraints")

    prompt = f"""
You are executing Phase 3 of the following research spec:

{spec}

TASK — Phase 3: Land and Grid Constraint Mapping
Search for EU land availability and grid constraint data relevant to off-grid
solar + BESS site selection.

Sources to query:
1. EEA CORINE Land Cover — available land categories by NUTS-2 region
2. Natura 2000 protected area boundaries — identify exclusion zones
3. ENTSO-E — HV grid node locations and capacity headroom

For each NUTS-2 region (or country where NUTS-2 unavailable), extract:
- Available land area by CORINE class (agricultural, marginal, brownfield)
- Natura 2000 exclusion area (km² or % of region)
- Nearest HV grid node distance (km) if available
- Grid capacity headroom (MW) if available
- Any grid distance threshold used in literature to define "off-grid viable"

Return as structured markdown tables. Flag any region where grid data is unavailable.
"""
    result = call_claude(SYSTEM_BASE, prompt, use_web_search=True)
    log("PHASE 3", "COMPLETE")
    return result


def phase_4_lcoe_and_sizing(spec: str) -> str:
    log("PHASE 4", "START", "LCOE and BESS sizing analysis")
    notify("Phase 4 started: LCOE and BESS sizing analysis")

    prompt = f"""
You are executing Phase 4 of the following research spec:

{spec}

TASK — Phase 4: LCOE and BESS Sizing Analysis
Search for published LCOE estimates and BESS sizing data for off-grid solar + BESS
systems at industrial scale in the EU.

Extract from each source:
- LCOE range (€/MWh) — low / central / high
- Year of estimate
- Assumed WACC (%)
- System size (MW solar, MWh storage)
- Storage duration (hours)
- MWh/MW ratio
- Country or region
- Source name, year, DOI or URL

Normalise all values to €/MWh where possible. Flag estimates in other currencies
with original value and conversion note.

Check extracted LCOE values against the IRENA published range for EU utility-scale
solar + storage. Flag any estimate that falls outside the IRENA range without
an explanation in the source.

Return a comparison table and a "Plausibility Check" section.
"""
    result = call_claude(SYSTEM_BASE, prompt, use_web_search=True)
    log("PHASE 4", "COMPLETE")
    return result


def phase_5_case_studies(spec: str) -> str:
    log("PHASE 5", "START", "Case study identification")
    notify("Phase 5 started: Case study search")

    prompt = f"""
You are executing Phase 5 of the following research spec:

{spec}

TASK — Phase 5: Case Study Identification
Search for documented EU off-grid or near-off-grid solar + BESS projects at
industrial or manufacturing sites.

For each case study found, extract:
- Project name and location (country, region)
- Operator or developer
- Solar PV capacity (MW)
- BESS capacity (MWh) and duration (hrs)
- Manufacturing sector (chemicals, metals, food, etc.)
- Self-sufficiency rate (%) if reported
- Annual generation (GWh) if reported
- Commissioning year
- Reported LCOE or capex if available
- Source name, URL, publication date

Include only documented projects with a verifiable source. Do not infer or estimate
values not stated in the source. If a detail is not reported, write "Not reported".

Return as a structured markdown table with a source citation row per project.
"""
    result = call_claude(SYSTEM_BASE, prompt, use_web_search=True)
    log("PHASE 5", "COMPLETE")
    return result


def phase_6_verification(spec: str, all_phase_outputs: dict) -> str:
    log("PHASE 6", "START", "Verification and conflict check")
    notify("Phase 6 started: Verification pass")

    combined = "\n\n---\n\n".join(
        f"## {phase}\n{output}"
        for phase, output in all_phase_outputs.items()
    )

    prompt = f"""
You are executing Phase 6 (Verification) of the following research spec:

{spec}

Below are the outputs from Phases 1–5:

{combined}

TASK — Phase 6: Run the full verification checklist from Section 7 of the spec.

For each checklist item, state: PASS / FAIL / PARTIAL, then explain briefly.

Then produce:

1. **Conflict Log** — list every case where two sources give different values for the
   same region/metric. Show both values, both sources, and the magnitude of difference.

2. **Deviation Flags** — list every capacity factor or LCOE estimate that deviates
   >15% from the PVGIS or IRENA benchmark. Include the extracted value, the benchmark,
   and the % difference.

3. **Source Integrity Issues** — list any data point where:
   - The source is secondary and no primary trace was found
   - The source is older than 5 years
   - The citation is incomplete

4. **Confidence Ratings** — for each EU region with data, assign High / Medium / Low
   with a one-line rationale.

5. **Data Gaps** — list research questions from Section 3 that could not be answered,
   and explain why.

Return the full verification report in markdown format.
"""
    result = call_claude(SYSTEM_BASE, prompt, use_web_search=False)
    log("PHASE 6", "COMPLETE")
    return result


def phase_7_summary(spec: str, all_outputs: dict) -> str:
    log("PHASE 7", "START", "Summary report generation")
    notify("Phase 7 started: Writing summary report")

    combined = "\n\n---\n\n".join(
        f"## {phase}\n{output}"
        for phase, output in all_outputs.items()
    )

    prompt = f"""
You are completing the final output of a structured research project.
Using the verified findings below, write a summary report following the
structure in Section 8 of the research spec:

{spec}

Findings and verification output:
{combined}

Write the summary report in markdown with these sections:
1. Executive Summary (3–5 sentences)
2. Top Regions by Solar Potential (ranked table: region, GHI, capacity factor, confidence)
3. Off-Grid Viability Assessment (by region: land, grid distance, BESS feasibility)
4. LCOE Overview (range, median, sub-regional breakdown where available)
5. Case Studies (brief entries)
6. Data Gaps & Limitations
7. Recommended Next Steps for a human researcher

Be factual and conservative. Do not extrapolate beyond what the sources support.
Flag uncertainty where it exists.
"""
    result = call_claude(SYSTEM_BASE, prompt, use_web_search=False)
    log("PHASE 7", "COMPLETE")
    return result


# ─────────────────────────────────────────────
# OUTPUT WRITERS
# ─────────────────────────────────────────────

def write_sources_log(phase_outputs: dict):
    """Write a consolidated sources log from all phase outputs."""
    with open(FILES["sources"], "w", encoding="utf-8") as f:
        f.write("# Sources Log\n\n")
        f.write(f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}*\n\n")
        for phase, output in phase_outputs.items():
            f.write(f"---\n\n## {phase}\n\n{output}\n\n")


def write_verification_report(content: str):
    with open(FILES["verification"], "w", encoding="utf-8") as f:
        f.write("# Verification Report\n\n")
        f.write(f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}*\n\n")
        f.write(content)


def write_summary_report(content: str):
    with open(FILES["summary"], "w", encoding="utf-8") as f:
        f.write(content)


def write_findings_csv_placeholder():
    """
    Write a placeholder CSV with headers.
    In production: parse structured output from phases and populate rows.
    See CUSTOMISATION NOTE below.
    """
    headers = [
        "region", "nuts2_code", "country",
        "ghi_kwh_m2_yr", "capacity_factor_pct",
        "offgrid_potential_gwh_yr", "bess_duration_hrs", "bess_ratio_mwh_mw",
        "land_type", "land_area_km2",
        "lcoe_eur_mwh", "lcoe_year", "lcoe_wacc_pct",
        "grid_distance_km",
        "source_name", "source_type", "publication_date",
        "page_figure_ref", "source_url_doi",
        "confidence_rating", "flags",
    ]
    if not FILES["findings"].exists():
        with open(FILES["findings"], "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
        log("CSV", "CREATED", "findings_table.csv with headers (populate from phase outputs)")


# ─────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────────

def main():
    start_time = time.time()
    log("AGENT", "START", f"Model: {MODEL}")
    notify("Research agent started — EU Off-Grid Solar + BESS Study")

    # ── Load spec ──────────────────────────────
    try:
        spec = load_spec()
        log("SPEC", "LOADED", str(SPEC_FILE))
    except FileNotFoundError as e:
        log("SPEC", "ERROR", str(e))
        notify(f"ERROR: {e}")
        return

    # ── Initialise output files ────────────────
    write_findings_csv_placeholder()
    log("OUTPUTS", "INITIALISED", str(OUTPUT_DIR))

    # ── Run research phases ────────────────────
    phase_outputs = {}

    phases = [
        ("Phase 1 — Resource Data",        phase_1_resource_data),
        ("Phase 2 — Off-Grid Literature",   phase_2_offgrid_literature),
        ("Phase 3 — Land & Grid",           phase_3_land_and_grid),
        ("Phase 4 — LCOE & Sizing",         phase_4_lcoe_and_sizing),
        ("Phase 5 — Case Studies",          phase_5_case_studies),
    ]

    for phase_name, phase_fn in phases:
        try:
            result = phase_fn(spec)
            phase_outputs[phase_name] = result
            upload_to_s3(FILES["process_log"])  # upload log after each phase
            time.sleep(2)  # brief pause between API calls
        except Exception as e:
            error_detail = traceback.format_exc()
            log(phase_name, "ERROR", error_detail)
            notify(f"ERROR in {phase_name}: {e}")
            phase_outputs[phase_name] = f"ERROR: {e}\n\n{error_detail}"
            # Continue to next phase rather than aborting

    # ── Write sources log ──────────────────────
    write_sources_log(phase_outputs)
    log("SOURCES LOG", "WRITTEN", str(FILES["sources"]))

    # ── Verification pass ──────────────────────
    try:
        verification = phase_6_verification(spec, phase_outputs)
        write_verification_report(verification)
        log("VERIFICATION", "WRITTEN", str(FILES["verification"]))
        upload_to_s3(FILES["verification"])
    except Exception as e:
        log("VERIFICATION", "ERROR", traceback.format_exc())
        notify(f"ERROR in verification: {e}")

    # ── Summary report ─────────────────────────
    try:
        all_outputs = {**phase_outputs, "Phase 6 — Verification": verification}
        summary = phase_7_summary(spec, all_outputs)
        write_summary_report(summary)
        log("SUMMARY", "WRITTEN", str(FILES["summary"]))
        upload_to_s3(FILES["summary"])
    except Exception as e:
        log("SUMMARY", "ERROR", traceback.format_exc())
        notify(f"ERROR in summary: {e}")

    # ── Final upload of all files ──────────────
    upload_all_outputs()

    elapsed = round((time.time() - start_time) / 60, 1)
    log("AGENT", "COMPLETE", f"Total runtime: {elapsed} minutes")
    notify(f"Research agent complete — {elapsed} min. Check S3 or outputs/ for results.")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    main()


# ─────────────────────────────────────────────
# CUSTOMISATION NOTES
# ─────────────────────────────────────────────
#
# 1. FINDINGS TABLE (findings_table.csv)
#    The CSV is initialised with headers only. To populate it automatically,
#    add a parser that reads each phase's markdown table output and maps rows
#    to CSV columns. A simple approach: ask Claude to return phase outputs as
#    JSON instead of markdown tables, then write directly to CSV.
#    Example prompt addition: "Return results as a JSON array where each object
#    has keys matching these fields: [region, nuts2_code, ghi_kwh_m2_yr, ...]"
#
# 2. ADDING MORE EU REGIONS
#    To scope Phase 1 to specific countries, edit the phase_1_resource_data
#    prompt to list target countries explicitly.
#
# 3. SLACK NOTIFICATIONS
#    Set SLACK_WEBHOOK_URL in your .env file. Create a webhook at:
#    https://api.slack.com/messaging/webhooks
#
# 4. S3 UPLOADS
#    Set S3_BUCKET_NAME and S3_OUTPUT_PREFIX in your .env. Ensure the EC2
#    instance has an IAM role with s3:PutObject permission on your bucket.
#    No AWS credentials needed in code if using IAM instance role.
#
# 5. RUNNING IN BACKGROUND ON EC2
#    tmux new -s research
#    python3 research_agent.py
#    Ctrl+B then D to detach
#    tmux attach -t research  (to reconnect)
#
# 6. ESTIMATED RUNTIME
#    ~25–45 minutes depending on web search latency and number of sources found.
#    Each phase makes 1–3 API calls with web search enabled.