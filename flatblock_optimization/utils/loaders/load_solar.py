# load_solar.py
# Need to aws configure

import pandas as pd
import numpy as np
import boto3
import duckdb
import tempfile
import os

def load_solar_profile(site_id, year, bucket_name, folder_prefix="inputs/solar", start_year=None, end_year=None):
    s3_prefix = f"{folder_prefix}/source_id={site_id}/year={year}/"
    s3_client = boto3.client('s3')

    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=s3_prefix)
        
        if 'Contents' not in response:
            raise FileNotFoundError(f"No files found in S3: s3://{bucket_name}/{s3_prefix}")
        
        parquet_files = [obj['Key'] for obj in response['Contents'] 
                        if obj['Key'].endswith('.parquet')]
        
        if not parquet_files:
            raise FileNotFoundError(f"No parquet files found in S3: s3://{bucket_name}/{s3_prefix}")
        
        dfs = []
        for file_key in parquet_files:
            # Download to a temp file and read with DuckDB
            obj = s3_client.get_object(Bucket=bucket_name, Key=file_key)
            parquet_content = obj['Body'].read()
            
            with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
                tmp.write(parquet_content)
                tmp_path = tmp.name
            
            try:
                df_part = duckdb.query(f"SELECT * FROM read_parquet('{tmp_path}')").df()
            finally:
                os.unlink(tmp_path)  # always clean up
            
            dfs.append(df_part)
        
        df = pd.concat(dfs, ignore_index=True)

    except Exception as e:
        raise Exception(f"Error loading from S3: {str(e)}")

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce')
    
    if "P_kWperkWp" not in df.columns:
        raise ValueError("Expected 'P_kWperkWp' column not found in parquet files")
    
    df["P_kWperkWp"] = df["P_kWperkWp"].astype(float)
    df["year"] = df["timestamp"].dt.year if "timestamp" in df.columns else year

    if start_year is not None:
        df = df[df["year"] >= start_year]
    if end_year is not None:
        df = df[df["year"] <= end_year]

    df = df.reset_index(drop=True)
    solar_array = df["P_kWperkWp"].to_numpy(dtype=float)

    return df, solar_array