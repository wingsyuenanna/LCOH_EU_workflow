#!/usr/bin/env python3
"""
One-time: upload local ``scenarios/fix_solar_profile/`` site runs to S3 under the
``all_sites_v1`` key layout (overwrites existing keys for those paths).

Destination (defaults):
  s3://<bucket>/<prefix>/all_sites_v1/scenario_all_sites_v1_<source_id>/{results,logs}/

Source folder names supported:
  ``scenario_<source_id>`` and legacy ``scenario_fix_solar_profile_<source_id>``.
If both exist for the same id, the shorter ``scenario_<id>`` tree is used and a warning is printed.

Run from ``flatblock_optimization/``:

  python utils/s3/one_time_upload_fix_solar_as_all_sites_v1.py --dry-run
  python utils/s3/one_time_upload_fix_solar_as_all_sites_v1.py

Same env as ``upload_scenario_to_s3.py``: FLATBLOCK_S3_BUCKET, FLATBLOCK_S3_PREFIX, AWS creds.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import upload_scenario_to_s3 as up  # noqa: E402

SHORT = re.compile(r"^scenario_(\d+)$")
LEGACY_FIX = re.compile(r"^scenario_fix_solar_profile_(\d+)$")


def _site_id_from_folder(name: str) -> Optional[str]:
    m = LEGACY_FIX.match(name)
    if m:
        return m.group(1)
    m = SHORT.match(name)
    if m:
        return m.group(1)
    return None


def _choose_one_folder_per_site(folders: List[Path]) -> Dict[str, Path]:
    """Map site_id -> local scenario folder; resolve duplicates."""
    by_site: Dict[str, List[Path]] = {}
    for d in folders:
        if not d.is_dir():
            continue
        sid = _site_id_from_folder(d.name)
        if sid is None:
            continue
        by_site.setdefault(sid, []).append(d)

    out: Dict[str, Path] = {}
    for sid in sorted(by_site.keys(), key=lambda x: int(x)):
        candidates = by_site[sid]
        if len(candidates) == 1:
            out[sid] = candidates[0]
            continue
        preferred = next((p for p in candidates if SHORT.match(p.name)), None)
        chosen = preferred or candidates[0]
        out[sid] = chosen
        others = [p.name for p in candidates if p != chosen]
        print(
            f"Warning: multiple folders for site {sid}; using {chosen.name} (skipped: {others})",
            file=sys.stderr,
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload fix_solar_profile local results to S3 as all_sites_v1 / scenario_all_sites_v1_*."
    )
    parser.add_argument(
        "--source-scenario",
        default="fix_solar_profile",
        help="Local scenarios subdirectory to read (default: fix_solar_profile).",
    )
    parser.add_argument(
        "--s3-scenario",
        default="all_sites_v1",
        help="S3 path segment under prefix (default: all_sites_v1).",
    )
    parser.add_argument(
        "--bucket",
        default=None,
        help="S3 bucket (default: FLATBLOCK_S3_BUCKET or annaiecc).",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="S3 key prefix (default: FLATBLOCK_S3_PREFIX or flatblock_results).",
    )
    parser.add_argument(
        "--scenarios-dir",
        default=None,
        help="Path to scenarios directory (default: flatblock_optimization/scenarios).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print S3 keys only; do not upload.",
    )
    args = parser.parse_args()

    bucket = args.bucket or up._bucket_prefix_defaults()[0]
    prefix = args.prefix or up._bucket_prefix_defaults()[1]
    base = Path(args.scenarios_dir) if args.scenarios_dir else up._flatblock_root() / "scenarios"
    scenario_base = base / args.source_scenario
    if not scenario_base.is_dir():
        print(f"Source scenario directory not found: {scenario_base}", file=sys.stderr)
        return 1

    site_to_folder = _choose_one_folder_per_site(list(scenario_base.iterdir()))
    if not site_to_folder:
        print(f"No scenario_* site folders under {scenario_base}", file=sys.stderr)
        return 1

    s3_client = up.get_s3_client()
    s3_scenario = args.s3_scenario
    total = 0
    print(
        f"Uploading {len(site_to_folder)} sites from {scenario_base} → "
        f"s3://{bucket}/{prefix.rstrip('/')}/{s3_scenario}/scenario_{s3_scenario}_<id>/"
    )
    if args.dry_run:
        print("(dry-run)")

    for site_id, folder in site_to_folder.items():
        dest_name = f"scenario_{s3_scenario}_{site_id}"
        s3_base = f"{prefix.rstrip('/')}/{s3_scenario}/{dest_name}"
        print(f"  site {site_id}: {folder.name} → .../{dest_name}/")
        for sub, subdir in (("results", folder / "results"), ("logs", folder / "logs")):
            if subdir.is_dir():
                total += up.upload_dir(
                    s3_client,
                    bucket,
                    f"{s3_base}/{sub}",
                    subdir,
                    dry_run=args.dry_run,
                )

    print(f"Done. Total files {'that would be uploaded' if args.dry_run else 'uploaded'}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
