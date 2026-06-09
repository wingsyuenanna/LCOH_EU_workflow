#!/usr/bin/env python3
"""
Build or refresh ``outputs/combined_results_all_sites_v1.csv`` from flatblock ``summary_site*.csv`` files.

**Partial update (default):** Only ``source_id`` values that appear in your new run (under
``scenarios/fix_solar_profile/`` with a ``summary_site*.csv``) are replaced. Every other row is kept
unchanged from the baseline CSV. Do **not** pass ``--no-baseline`` if you want that behavior.

Flow for ``fix_solar_profile`` → patch existing combined file:

1. Aggregate summaries (``scenario_<source_id>/`` or legacy ``scenario_<name>_<id>/``)
   using ``--aggregate-as`` (must match legacy folder prefix; often same as ``--from-scenario``).
2. Set the ``scenario`` column: defaults to **``--from-scenario``** (e.g. ``fix_solar_profile_v2``).
   Pass ``--scenario-label all_sites_v1`` if you need the old tag for patched rows.
   **Note:** ``--aggregate-as`` only affects folder matching on disk, not the ``scenario`` column.
3. Merge facility columns from ``views/facility_master_v6.csv``.
4. Read ``--baseline`` (default ``outputs/combined_results_all_sites_v1.csv``): remove rows whose
   ``source_id`` is in the new batch, append the new rows, preserve column order.

Run from ``flatblock_optimization/``::

  python utils/analysis/combine_scenario_summaries.py --dry-run
  python utils/analysis/combine_scenario_summaries.py

Patch **only** rows whose ``source_id`` is listed in ``inputs/sites.csv`` (e.g. after a
``fix_solar_profile_v2`` run), leaving every other baseline row unchanged::

  python utils/analysis/combine_scenario_summaries.py \\
    --from-scenario fix_solar_profile_v2 \\
    --aggregate-as fix_solar_profile_v2 \\
    --sites-csv inputs/sites.csv

To rebuild the combined file from **only** scenario folders (no baseline; **all** rows come from disk)::

  python utils/analysis/combine_scenario_summaries.py --from-scenario all_sites_v1 --aggregate-as all_sites_v1 \\
    --no-baseline -o outputs/combined_results_all_sites_v1.csv
"""

import argparse
import io
import sys
from pathlib import Path

import pandas as pd

_FLATBLOCK = Path(__file__).resolve().parents[2]
_REPO = _FLATBLOCK.parent
if str(_FLATBLOCK) not in sys.path:
    sys.path.insert(0, str(_FLATBLOCK))

from utils.maps.map_common import aggregate_scenarios  # noqa: E402

FACILITY_COLS_FOR_MERGE = [
    "source_id",
    "iso3_country",
    "lat",
    "lon",
    "sector",
    "source_type",
    "capacity",
    "capacity_units",
]


def _load_new_combined(
    scenario_base: Path,
    aggregate_as: str,
    scenario_label: str,
    facility_master: Path,
) -> pd.DataFrame:
    agg = aggregate_scenarios(str(scenario_base), aggregate_as)
    agg["scenario"] = scenario_label

    fac = pd.read_csv(facility_master)
    miss = [c for c in FACILITY_COLS_FOR_MERGE if c not in fac.columns]
    if miss:
        raise SystemExit(f"facility master missing columns: {miss}")
    fac = fac[FACILITY_COLS_FOR_MERGE].copy()
    merged = agg.merge(fac, left_on="site", right_on="source_id", how="left")
    # If a site is missing from facility master, keep patch id from ``site``.
    if merged["source_id"].isna().any():
        merged["source_id"] = merged["source_id"].fillna(merged["site"])
    return merged


def _s3_client(aws_profile: str | None = None):
    try:
        import boto3
    except ImportError as e:
        raise SystemExit(
            "boto3 is required for S3 features. Install with: pip install boto3"
        ) from e
    if aws_profile:
        return boto3.Session(profile_name=aws_profile).client("s3")
    return boto3.client("s3")


def _upload_file_to_s3(local_path: Path, bucket: str, key: str, aws_profile: str | None = None) -> None:
    s3 = _s3_client(aws_profile)
    s3.upload_file(str(local_path), bucket, key)


def _load_new_combined_from_s3(
    *,
    bucket: str,
    prefix: str,
    scenario_label: str,
    facility_master: Path,
    aws_profile: str | None = None,
) -> pd.DataFrame:
    s3 = _s3_client(aws_profile)
    pfx = prefix.strip("/")
    if pfx:
        pfx = pfx + "/"

    # Collect all summary_site*.csv under the prefix
    keys = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": pfx, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            k = obj.get("Key", "")
            name = k.rsplit("/", 1)[-1]
            if name.startswith("summary_site") and name.endswith(".csv"):
                keys.append(k)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

    if not keys:
        raise SystemExit(
            f"No summary files found in s3://{bucket}/{pfx} "
            "(expected files like summary_site*.csv)."
        )

    frames = []
    for key in sorted(keys):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        df = pd.read_csv(io.BytesIO(body))
        if not df.empty:
            frames.append(df)
    if not frames:
        raise SystemExit(f"Found {len(keys)} summary CSVs in s3://{bucket}/{pfx}, but all were empty.")

    agg = pd.concat(frames, ignore_index=True)
    agg["scenario"] = scenario_label

    # Ensure "site" exists for merge (aggregate_scenarios convention)
    if "site" not in agg.columns:
        if "source_id" in agg.columns:
            agg["site"] = agg["source_id"]
        elif "Site" in agg.columns:
            agg["site"] = agg["Site"]
        else:
            raise SystemExit(
                "S3 summary CSVs must include either 'site' or 'source_id' column for merge."
            )

    fac = pd.read_csv(facility_master)
    miss = [c for c in FACILITY_COLS_FOR_MERGE if c not in fac.columns]
    if miss:
        raise SystemExit(f"facility master missing columns: {miss}")
    fac = fac[FACILITY_COLS_FOR_MERGE].copy()
    merged = agg.merge(fac, left_on="site", right_on="source_id", how="left")
    if merged["source_id"].isna().any():
        merged["source_id"] = merged["source_id"].fillna(merged["site"])
    return merged


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Combine flatblock summary CSVs. By default, patches the baseline: only source_ids "
            "with new summaries are replaced; all other baseline rows are unchanged. "
            "Use --sites-csv to limit replacements to a subset of source_ids."
        ),
        epilog=(
            "Examples: "
            "python utils/analysis/combine_scenario_summaries.py  "
            "|  python utils/analysis/combine_scenario_summaries.py --from-scenario fix_solar_profile_v2 "
            "--aggregate-as fix_solar_profile_v2 --sites-csv inputs/sites.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--scenarios-dir",
        type=Path,
        default=_FLATBLOCK / "scenarios",
        help="Parent of per-scenario folders (default: flatblock_optimization/scenarios).",
    )
    p.add_argument(
        "--from-scenario",
        default="fix_solar_profile",
        help="Scenario subfolder under --scenarios-dir to read (default: fix_solar_profile).",
    )
    p.add_argument(
        "--aggregate-as",
        default=None,
        help="Name passed to aggregate_scenarios for legacy folder matching "
        "(default: same as --from-scenario).",
    )
    p.add_argument(
        "--scenario-label",
        default=None,
        metavar="NAME",
        help="Value written in the scenario column for new/patched rows (default: same as --from-scenario).",
    )
    p.add_argument(
        "--label-as-from-scenario",
        action="store_true",
        help="Force scenario column to --from-scenario (overrides an explicit --scenario-label).",
    )
    p.add_argument(
        "--facility-master",
        type=Path,
        default=_REPO / "views" / "facility_master_v6.csv",
        help="Facility attributes for merge (default: views/facility_master_v6.csv).",
    )
    p.add_argument(
        "--baseline",
        type=Path,
        default=_FLATBLOCK / "outputs" / "combined_results_all_sites_v1.csv",
        help="Existing combined CSV to patch (rows with matching source_id replaced).",
    )
    p.add_argument(
        "--no-baseline",
        action="store_true",
        help="Ignore baseline: output ONLY sites under --from-scenario (does NOT keep other sites).",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_FLATBLOCK / "outputs" / "combined_results_all_sites_v1.csv",
        help="Output CSV path.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts only; do not write.",
    )
    p.add_argument(
        "--sites-csv",
        type=Path,
        default=None,
        help=(
            "If set, only replace baseline rows whose source_id appears in this CSV "
            "(must have a source_id column). Rows with no new summary under --from-scenario are skipped."
        ),
    )
    p.add_argument(
        "--output-only-sites-csv",
        action="store_true",
        help=(
            "After merge/patch, keep only rows whose source_id appears in --sites-csv. "
            "Useful when combined output should be limited to a specific input site list."
        ),
    )
    p.add_argument(
        "--use-s3-source",
        action="store_true",
        help=(
            "Read summary_site*.csv files from S3 as input source instead of local scenarios folder."
        ),
    )
    p.add_argument(
        "--upload-s3",
        action="store_true",
        help="Upload output CSV to S3 after writing.",
    )
    p.add_argument(
        "--s3-bucket",
        default="annaiecc",
        help="S3 bucket for upload (default: annaiecc).",
    )
    p.add_argument(
        "--s3-prefix",
        default="flatblock_results/heat_battery_iron_steel/",
        help=(
            "S3 key prefix used for S3 input source and upload destination "
            "(default: flatblock_results/heat_battery_iron_steel/). "
            "For upload, final key is <prefix>/<output filename> unless --s3-key is set."
        ),
    )
    p.add_argument(
        "--s3-key",
        default=None,
        help="Full S3 object key override. If set, --s3-prefix is ignored.",
    )
    p.add_argument(
        "--aws-profile",
        default=None,
        help=(
            "Optional AWS profile name from local ~/.aws/config (e.g., --aws-profile default). "
            "If omitted, boto3 uses its default credential chain."
        ),
    )
    args = p.parse_args()

    aggregate_as = args.aggregate_as or args.from_scenario
    if args.label_as_from_scenario:
        scenario_label = args.from_scenario
    elif args.scenario_label is not None:
        scenario_label = args.scenario_label
    else:
        scenario_label = args.from_scenario
    if not args.facility_master.is_file():
        print(f"Facility master not found: {args.facility_master}", file=sys.stderr)
        return 1

    if args.use_s3_source:
        scenario_base = None
        new_df = _load_new_combined_from_s3(
            bucket=args.s3_bucket,
            prefix=args.s3_prefix,
            scenario_label=scenario_label,
            facility_master=args.facility_master,
            aws_profile=args.aws_profile,
        )
    else:
        scenario_base = args.scenarios_dir / args.from_scenario
        if not scenario_base.is_dir():
            print(f"Scenario directory not found: {scenario_base}", file=sys.stderr)
            return 1
        new_df = _load_new_combined(
            scenario_base,
            aggregate_as=aggregate_as,
            scenario_label=scenario_label,
            facility_master=args.facility_master,
        )
    allow_ids = None
    if args.sites_csv is not None:
        if not args.sites_csv.is_file():
            print(f"--sites-csv not found: {args.sites_csv}", file=sys.stderr)
            return 1
        sites = pd.read_csv(args.sites_csv)
        if "source_id" not in sites.columns:
            print(f"{args.sites_csv} must contain source_id", file=sys.stderr)
            return 1
        allow = set(pd.to_numeric(sites["source_id"], errors="coerce").dropna().astype(int).tolist())
        allow_ids = allow
        sid_series = pd.to_numeric(new_df["source_id"], errors="coerce")
        new_df = new_df[sid_series.isin(list(allow))].copy()
        if new_df.empty:
            print(
                "No overlapping rows between scenario summaries and --sites-csv (check paths and results).",
                file=sys.stderr,
            )
            return 1
    patch_ids = pd.to_numeric(new_df["source_id"], errors="coerce").dropna().astype(int)
    new_ids = set(patch_ids.tolist())

    if args.no_baseline or not args.baseline.is_file():
        out = new_df
        if args.use_s3_source:
            print(
                f"Built {len(out)} rows from s3://{args.s3_bucket}/{args.s3_prefix.strip('/')}/ "
                "(no baseline merge)."
            )
        else:
            print(f"Built {len(out)} rows from {scenario_base} (no baseline merge).")
    else:
        base = pd.read_csv(args.baseline)
        if "source_id" not in base.columns:
            print("Baseline CSV must contain source_id.", file=sys.stderr)
            return 1
        base_ids = pd.to_numeric(base["source_id"], errors="coerce")
        keep = base[~base_ids.isin(list(new_ids))]
        out = pd.concat([keep, new_df], ignore_index=True)
        src_note = (
            f"s3://{args.s3_bucket}/{args.s3_prefix.strip('/')}/"
            if args.use_s3_source
            else str(scenario_base)
        )
        print(
            f"Patched baseline {args.baseline}: removed {len(base) - len(keep)} rows, "
            f"added {len(new_df)} from {src_note}; total {len(out)} rows."
        )

        # Same columns as baseline (extras from new only dropped; missing filled with NA)
        for c in base.columns:
            if c not in out.columns:
                out[c] = pd.NA
        extra = [c for c in out.columns if c not in base.columns]
        if extra:
            out = out.drop(columns=extra)
        out = out[base.columns]

    if args.output_only_sites_csv:
        if allow_ids is None:
            print("--output-only-sites-csv requires --sites-csv.", file=sys.stderr)
            return 1
        out_ids = pd.to_numeric(out["source_id"], errors="coerce")
        before = len(out)
        out = out[out_ids.isin(list(allow_ids))].copy()
        print(
            f"Filtered final output to --sites-csv list: kept {len(out)} of {before} rows."
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        print(f"[dry-run] would write {len(out)} rows → {args.output}")
        if args.upload_s3:
            if args.s3_key:
                key = args.s3_key
            else:
                prefix = args.s3_prefix.strip("/")
                key = f"{prefix}/{args.output.name}" if prefix else args.output.name
            print(f"[dry-run] would upload → s3://{args.s3_bucket}/{key}")
        return 0

    out.to_csv(args.output, index=False)
    print(f"Wrote {args.output}")
    if args.upload_s3:
        if args.s3_key:
            key = args.s3_key
        else:
            prefix = args.s3_prefix.strip("/")
            key = f"{prefix}/{args.output.name}" if prefix else args.output.name
        _upload_file_to_s3(args.output, args.s3_bucket, key, aws_profile=args.aws_profile)
        print(f"Uploaded to s3://{args.s3_bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
