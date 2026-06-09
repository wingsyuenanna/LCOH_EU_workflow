import pandas as pd
import numpy as np

def get_peak_hours_binary(site_id, solar_profile_df, peak_cutoff=0.05, folder="inputs/demand",
                          start_year=2016, end_year=2024, flat_block=False, flat_block_load_mw=None):
    """
    Returns binary array indicating when gas is allowed based on peak demand cutoff.
    
    Parameters
    ----------
    site_id : int
        Site number matching CSV file.
    solar_profile_df : pd.DataFrame
        Must have a 'timestamp' column.
    peak_cutoff : float
        Fraction of hours considered peak (gas not allowed). Example: 0.05 = top 5%.
    folder : str
        Folder containing per-site demand CSVs.
    start_year, end_year : int
        Years to filter the demand data.
    flat_block : bool
        If True, returns gas allowed all hours and flat demand profile.
        If False, loads demand data and restricts gas during peak hours.
    flat_block_load_mw : float, optional
        Constant load (MW) for flat block mode. If None and flat_block=True, raises error.
    
    Returns
    -------
    gas_allowed : np.ndarray
        If flat_block=True: Array of all 1s (gas allowed all hours)
        If flat_block=False: 1 = gas allowed, 0 = gas blocked during peak
    aligned_demand : np.ndarray
        If flat_block=True: Array of constant value (flat_block_load_mw)
        If flat_block=False: Hourly demand values aligned to solar_profile_df timestamps
    """
    
    # FLAT BLOCK MODE: Gas always allowed, constant demand
    if flat_block:
        if flat_block_load_mw is None:
            raise ValueError("flat_block=True requires flat_block_load_mw to be specified")
        
        num_hours = len(solar_profile_df)
        gas_allowed = np.ones(num_hours, dtype=int)  # All 1s (gas always allowed)
        aligned_demand = np.full(num_hours, flat_block_load_mw)  # Constant MW value
        
        print(f"Flat block mode: Gas allowed all {num_hours} hours")
        print(f"Flat demand profile: {flat_block_load_mw} MW for all hours")
        return gas_allowed, aligned_demand
    
    # PEAK RESTRICTION MODE: Load demand and restrict gas during peaks
    df = load_site_demand(site_id, folder, start_year, end_year)
    df_sorted = df.sort_values('timestamp').reset_index(drop=True)
    solar_sorted = solar_profile_df.sort_values('timestamp').reset_index(drop=True)
    
    # Keep solar timestamps exactly as the reference 
    # Solar timestamps are correct, and all values are filled; meanwhile, some demand hours are missing 
    
    # Assign cumcount to demand duplicates
    df_sorted['hour_index'] = df_sorted.groupby('timestamp').cumcount()

    # Assign cumcount to solar duplicates
    solar_sorted['hour_index'] = solar_sorted.groupby('timestamp').cumcount()

    # Merge on timestamp + hour_index
    aligned_df = pd.merge(
        solar_sorted[['timestamp', 'hour_index']],
        df_sorted[['timestamp', 'hour_index', 'Load MW']],
        on=['timestamp', 'hour_index'],
        how='left'
    )

    # Fill missing demand values (forward/backward fill)
    # So the missing hours were added from the solar profile, and now are filled in with the values above/below
    aligned_df['Load MW'] = aligned_df['Load MW'].ffill().bfill()

    # Extract numpy array for the optimizer
    aligned_demand = aligned_df['Load MW'].values   
    
    # Threshold for peak hours
    threshold = np.percentile(aligned_demand, 100 * (1 - peak_cutoff))
    
    # Binary: 1 = gas allowed, 0 = gas blocked DURING PEAKS
    gas_allowed = (aligned_demand < threshold).astype(int)
    
    print(f"Peak restriction mode: Gas blocked during top {peak_cutoff*100}% demand hours (>{threshold:.1f} MW)")
    print(f"  - Hours with gas allowed: {gas_allowed.sum()} ({gas_allowed.sum()/len(gas_allowed)*100:.1f}%)")
    print(f"  - Hours with gas blocked: {(1-gas_allowed).sum()} ({(1-gas_allowed).sum()/len(gas_allowed)*100:.1f}%)")

    return gas_allowed, aligned_demand


def load_site_demand(site_id, folder, start_year, end_year):
    """
    Load demand data for a specific site.
    
    Parameters
    ----------
    site_id : int
        Site number.
    folder : str
        Path to folder containing demand CSV files.
    start_year, end_year : int
        Year range to filter.
    
    Returns
    -------
    pd.DataFrame
        Demand data with 'timestamp' and 'Load MW' columns.
    """
    import os
    filepath = os.path.join(folder, f"site_{site_id}_demand.csv")
    
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Demand file not found: {filepath}")
    
    df = pd.read_csv(filepath, parse_dates=['timestamp'])
    df = df[(df['timestamp'].dt.year >= start_year) & (df['timestamp'].dt.year <= end_year)]
    
    return df