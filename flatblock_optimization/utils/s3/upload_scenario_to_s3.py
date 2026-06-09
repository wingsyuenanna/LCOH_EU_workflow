#!/usr/bin/env python3
"""
Upload or download flatblock scenario results and logs to/from S3.

Keeps the same folder layout on S3 for easy navigation:
  s3://<bucket>/<prefix>/<scenario_name>/<scenario_folder>/results/
  s3://<bucket>/<prefix>/<scenario_name>/<scenario_folder>/logs/

Usage:
  Upload (from repo root or ``flatblock_optimization/``):
    python flatblock_optimization/utils/s3/upload_scenario_to_s3.py upload all_sites_v1
    python flatblock_optimization/utils/s3/upload_scenario_to_s3.py upload all_sites_v1 \\
      --bucket my-bucket --prefix flatblock_results

  Default S3 layout: ``s3://<bucket>/flatblock_results/<scenario>/scenario_<source_id>/{results,logs}/``
  (Legacy ``scenario_<scenario>_<source_id>/`` is still discovered for upload.)
  Re-uploading overwrites existing keys for those paths.

  Download:
    python upload_scenario_to_s3.py download all_sites_v1
    python upload_scenario_to_s3.py download all_sites_v1 --scenarios-dir /path/to/scenarios

  Environment (optional):
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or use aws configure)
    FLATBLOCK_S3_BUCKET   - default bucket name
    FLATBLOCK_S3_PREFIX   - default prefix (e.g. "flatblock_results" or "")
    FLATBLOCK_SCENARIO    - default scenario name if you omit the positional SCENARIO
                            (e.g. ``export FLATBLOCK_SCENARIO=all_sites_v1`` then
                            ``python ... upload`` uploads that tree under scenarios/)
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError


def get_s3_client():
    return boto3.client("s3")


def _bucket_prefix_defaults():
    return (
        os.environ.get("FLATBLOCK_S3_BUCKET", "annaiecc"),
        os.environ.get("FLATBLOCK_S3_PREFIX", "flatblock_results"),
    )


def _default_scenario_from_env() -> Optional[str]:
    v = os.environ.get("FLATBLOCK_SCENARIO")
    return v if v else None


def download_dir(
    s3_client,
    bucket: str,
    s3_prefix: str,
    local_dir: Path,
    dry_run: bool = False,
) -> int:
    """Download objects under s3_prefix into local_dir (key path preserved). Returns number of files."""
    paginator = s3_client.get_paginator("list_objects_v2")
    local_dir = local_dir.resolve()
    count = 0
    prefix = s3_prefix.rstrip("/") + "/"
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            key = obj["Key"]
            if key.endswith("/"):
                continue
            rel = key[len(prefix) :]
            local_path = local_dir / rel
            local_path.parent.mkdir(parents=True, exist_ok=True)
            if dry_run:
                print(f"  [dry-run] {rel}")
                count += 1
                continue
            try:
                s3_client.download_file(bucket, key, str(local_path))
                print(f"  downloaded: {rel}")
                count += 1
            except ClientError as e:
                print(f"  ERROR downloading {key}: {e}", file=sys.stderr)
    return count


def _flatblock_root() -> Path:
    """``flatblock_optimization/`` (parent of ``utils/s3/``)."""
    return Path(__file__).resolve().parents[2]


def _scenario_run_folders(scenario_base: Path, scenario_name: str) -> List[Path]:
    """
    Directories for each site run under ``scenarios/<scenario_name>/``.

    New layout: ``scenario_<source_id>`` (numeric ``source_id``). Legacy: ``scenario_<scenario_name>_<source_id>``.
    """
    legacy_prefix = f"scenario_{scenario_name}_"
    short_site = re.compile(r"^scenario_\d+$")
    found: List[Path] = []
    for d in sorted(scenario_base.iterdir()):
        if not d.is_dir():
            continue
        n = d.name
        if n.startswith(legacy_prefix):
            found.append(d)
        elif short_site.match(n):
            found.append(d)
    return found


def get_results_from_s3(
    scenario: str,
    bucket: Optional[str] = None,
    prefix: Optional[str] = None,
    scenarios_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> int:
    """
    Download all results and logs for a scenario from S3 into the local scenarios folder.

    Uses the same layout as upload: <prefix>/<scenario>/<scenario_folder>/results|logs/

    Returns number of files downloaded.
    """
    b, p = bucket or _bucket_prefix_defaults()[0], prefix or _bucket_prefix_defaults()[1]
    base = scenarios_dir if scenarios_dir is not None else _flatblock_root() / "scenarios"
    scenario_base = base / scenario
    scenario_base.mkdir(parents=True, exist_ok=True)

    s3_client = get_s3_client()
    s3_prefix = f"{p.rstrip('/')}/{scenario}/"
    total = 0
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=b, Prefix=s3_prefix):
        for obj in page.get("Contents") or []:
            key = obj["Key"]
            if key.endswith("/"):
                continue
            rel = key[len(s3_prefix) :]
            local_path = scenario_base / rel
            local_path.parent.mkdir(parents=True, exist_ok=True)
            if dry_run:
                print(f"  [dry-run] {rel}")
                total += 1
            else:
                try:
                    s3_client.download_file(b, key, str(local_path))
                    print(f"  downloaded: {rel}")
                    total += 1
                except ClientError as e:
                    print(f"  ERROR downloading {key}: {e}", file=sys.stderr)
    return total


def upload_dir(s3_client, bucket: str, s3_prefix: str, local_dir: Path, dry_run: bool = False) -> int:
    """Upload a directory tree to S3. Returns number of files uploaded."""
    local_dir = local_dir.resolve()
    if not local_dir.is_dir():
        return 0
    count = 0
    for f in local_dir.rglob("*"):
        if f.is_file():
            rel = f.relative_to(local_dir)
            key = f"{s3_prefix.rstrip('/')}/{rel.as_posix()}"
            if dry_run:
                print(f"  [dry-run] s3://{bucket}/{key}")
                count += 1
                continue
            try:
                s3_client.upload_file(str(f), bucket, key)
                print(f"  uploaded: {rel}")
                count += 1
            except ClientError as e:
                print(f"  ERROR uploading {f}: {e}", file=sys.stderr)
    return count


def _add_common_args(parser):
    parser.add_argument(
        "scenario",
        nargs="?",
        default=None,
        help="Scenario directory name under scenarios/ (e.g. all_sites_v1, fix_solar_profile). "
        "If omitted, uses env FLATBLOCK_SCENARIO.",
    )
    parser.add_argument(
        "--bucket",
        default=None,
        help="S3 bucket (default: env FLATBLOCK_S3_BUCKET or 'annaiecc').",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="S3 key prefix (default: env FLATBLOCK_S3_PREFIX or 'flatblock_results').",
    )
    parser.add_argument(
        "--scenarios-dir",
        default=None,
        help="Path to scenarios directory (default: flatblock_optimization/scenarios).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without uploading/downloading.",
    )


def cmd_upload(args):
    bucket = args.bucket or _bucket_prefix_defaults()[0]
    prefix = args.prefix or _bucket_prefix_defaults()[1]
    base = Path(args.scenarios_dir) if args.scenarios_dir else _flatblock_root() / "scenarios"
    scenario_base = base / args.scenario
    if not scenario_base.is_dir():
        print(f"Scenario directory not found: {scenario_base}", file=sys.stderr)
        return 1

    scenario_folders = _scenario_run_folders(scenario_base, args.scenario)
    if not scenario_folders:
        print(
            f"No scenario folders found under {scenario_base} "
            f"(expected scenario_<source_id>/ or legacy scenario_{args.scenario}_<source_id>/)",
            file=sys.stderr,
        )
        return 1

    s3_client = get_s3_client()
    s3_base = f"{prefix.rstrip('/')}/{args.scenario}"
    total = 0
    print(f"Uploading scenario '{args.scenario}' to s3://{bucket}/{s3_base}/")
    if args.dry_run:
        print("(dry-run: no files will be uploaded)")
    for folder in scenario_folders:
        folder_name = folder.name
        for sub, subdir in (("results", folder / "results"), ("logs", folder / "logs")):
            if subdir.is_dir():
                total += upload_dir(
                    s3_client, bucket, f"{s3_base}/{folder_name}/{sub}", subdir, dry_run=args.dry_run
                )
    print(f"Done. Total files uploaded: {total}")
    return 0


def cmd_download(args):
    bucket = args.bucket or _bucket_prefix_defaults()[0]
    prefix = args.prefix or _bucket_prefix_defaults()[1]
    scenarios_dir = Path(args.scenarios_dir) if args.scenarios_dir else None
    print(f"Downloading scenario '{args.scenario}' from s3://{bucket}/{prefix}/")
    if args.dry_run:
        print("(dry-run: no files will be downloaded)")
    total = get_results_from_s3(
        args.scenario,
        bucket=bucket,
        prefix=prefix,
        scenarios_dir=scenarios_dir,
        dry_run=args.dry_run,
    )
    print(f"Done. Total files downloaded: {total}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Upload or download flatblock scenario results and logs to/from S3 (same folder layout)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Command")
    up = subparsers.add_parser("upload", help="Upload scenario results and logs to S3")
    _add_common_args(up)
    up.set_defaults(func=cmd_upload)
    down = subparsers.add_parser("download", help="Download scenario results and logs from S3")
    _add_common_args(down)
    down.set_defaults(func=cmd_download)

    args = parser.parse_args()
    if args.command in ("upload", "download"):
        if args.scenario is None:
            args.scenario = _default_scenario_from_env()
        if not args.scenario:
            parser.error(
                "scenario name required: pass it after the command "
                "(e.g. upload all_sites_v1) or set FLATBLOCK_SCENARIO"
            )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
