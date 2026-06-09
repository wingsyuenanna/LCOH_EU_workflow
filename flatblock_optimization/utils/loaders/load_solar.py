# load_solar.py

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def _source_id_to_filename(source_id: str) -> str:
    """Map source_id (e.g. 'FR.CAED/7624.FACILITY') to its CSV filename."""
    return source_id.replace("/", "_") + ".csv"


def _load_local(source_id: str, local_folder: str | Path,
                start_year: int | None, end_year: int | None):
    """Load PVGIS hourly CSV from local folder."""
    folder = Path(local_folder)
    filename = _source_id_to_filename(source_id)
    path = folder / filename

    if not path.exists():
        raise FileNotFoundError(
            f"Local solar file not found: {path}\n"
            f"  Expected filename: {filename} (source_id '/' → '_')"
        )

    df = pd.read_csv(path)

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    if "P_kWperkWp" not in df.columns:
        raise ValueError(f"Expected 'P_kWperkWp' column not found in {path}")

    df["P_kWperkWp"] = df["P_kWperkWp"].astype(float)
    df["year"] = df["timestamp"].dt.year if "timestamp" in df.columns else start_year

    if start_year is not None:
        df = df[df["year"] >= start_year]
    if end_year is not None:
        df = df[df["year"] <= end_year]

    return df.reset_index(drop=True)


def _load_s3(source_id: str, year: int, bucket_name: str, folder_prefix: str,
             start_year: int | None, end_year: int | None):
    """Load solar profile from S3 parquet (original implementation)."""
    import boto3
    import duckdb

    s3_prefix = f"{folder_prefix}/source_id={source_id}/year={year}/"
    s3_client = boto3.client("s3")

    response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=s3_prefix)
    if "Contents" not in response:
        raise FileNotFoundError(f"No files found in S3: s3://{bucket_name}/{s3_prefix}")

    parquet_files = [o["Key"] for o in response["Contents"] if o["Key"].endswith(".parquet")]
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files in S3: s3://{bucket_name}/{s3_prefix}")

    dfs = []
    for file_key in parquet_files:
        obj = s3_client.get_object(Bucket=bucket_name, Key=file_key)
        parquet_content = obj["Body"].read()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            tmp.write(parquet_content)
            tmp_path = tmp.name
        try:
            df_part = duckdb.query(f"SELECT * FROM read_parquet('{tmp_path}')").df()
        finally:
            os.unlink(tmp_path)
        dfs.append(df_part)

    df = pd.concat(dfs, ignore_index=True)

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    if "P_kWperkWp" not in df.columns:
        raise ValueError("Expected 'P_kWperkWp' column not found in parquet files")

    df["P_kWperkWp"] = df["P_kWperkWp"].astype(float)
    df["year"] = df["timestamp"].dt.year if "timestamp" in df.columns else year

    if start_year is not None:
        df = df[df["year"] >= start_year]
    if end_year is not None:
        df = df[df["year"] <= end_year]

    return df.reset_index(drop=True)


def load_solar_profile(
    site_id: str,
    year: int,
    bucket_name: str = "annaiecc",
    folder_prefix: str = "solar_data_parquet_v3",
    start_year: int | None = None,
    end_year: int | None = None,
    local_folder: str | Path | None = None,
) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Load hourly solar capacity-factor profile for a site.

    Local-first: if ``local_folder`` is provided and the expected file exists,
    reads from the PVGIS hourly CSV directory (no AWS credentials required).
    Falls back to S3 parquet if no local folder is given.

    Local filename convention: source_id with '/' replaced by '_', e.g.
        'FR.CAED/7624.FACILITY' → 'FR.CAED_7624.FACILITY.csv'

    Parameters
    ----------
    site_id      : facility source_id string
    year         : solar data year (used for S3 path; local files are single-year)
    bucket_name  : S3 bucket (ignored when loading locally)
    folder_prefix: S3 folder prefix (ignored when loading locally)
    start_year   : filter rows to year >= start_year
    end_year     : filter rows to year <= end_year
    local_folder : path to directory containing PVGIS hourly CSV files;
                   if None or folder does not exist, falls back to S3
    """
    if local_folder is not None and Path(local_folder).is_dir():
        df = _load_local(site_id, local_folder, start_year, end_year)
    else:
        df = _load_s3(site_id, year, bucket_name, folder_prefix, start_year, end_year)

    solar_array = df["P_kWperkWp"].to_numpy(dtype=float)
    return df, solar_array
