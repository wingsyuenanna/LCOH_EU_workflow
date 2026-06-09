# load_gas.py

import pandas as pd

def load_site_params(site_id, sites_csv, columns=None):
    """
    Load site-specific parameters from a CSV for a given site_id.

    Parameters
    ----------
    site_id : int
        Row index or site identifier matching the site number.
    sites_csv : str
        Path to the sites CSV.
    columns : list of str, optional
        List of columns to extract. If None, all columns are returned.

    Returns
    -------
    dict
        Dictionary of site-specific parameter values.
    """
    # Read CSV starting from row 2 (skip first row after header)
    df = pd.read_csv(sites_csv, header=0)

    if columns is not None:
        df = df[columns]
        
    # #print(f"Number of sites in CSV: {len(df)}")
    # # Option 1: site_id matches row index starting at 1
    # if site_id > len(df):
    #     raise IndexError(f"site_id {site_id} exceeds number of sites in {sites_csv}")

    site_row = df[df['source_id'] == site_id].iloc[0] # only one match

    return site_row.to_dict()

