# load_re_costs.py

import pandas as pd

# Technology labels in the unified BNEF output (bnef_solar_pv_costs_v4.csv)
SOLAR_TECH = "PV fixed-axis"
BATTERY_TECH = "Utility-scale battery (4h)"

# Row labels in the battery component CSV (BNEF/output/battery_2025_cost.csv)
BATTERY_ROW_PACK = "Pack"
BATTERY_ROW_RACK = "Rack"
BATTERY_ROW_BOS_EMS = "BOS + EMS"
BATTERY_ROW_EPC = "EPC"
BATTERY_ROW_PCS = "PCS + Overhead"

# Optional thermal storage rows in battery component CSV (year-specific).
THERMAL_ROW_CAPEX_KWH = "Thermal CAPEX $/kWh"
THERMAL_ROW_OPEX_PCT = "Thermal OPEX frac/yr"
THERMAL_ROW_LIFETIME_Y = "Thermal Lifetime yr"
THERMAL_ROW_DISCOUNT = "Thermal Discount rate"
THERMAL_ROW_EFF_LE_200 = "Thermal Eff <=200C"
THERMAL_ROW_EFF_GT_200 = "Thermal Eff >200C"
# Optional TES temperature / charge-discharge geometry (heat battery CSV; year columns).
THERMAL_ROW_T_MAX_C = "Thermal T_max C"
THERMAL_ROW_T_MIN_C = "Thermal T_min C"
THERMAL_ROW_CD_RATIO = "Thermal Charge/Discharge ratio min"


def load_re_costs(bnef_filepath, battery_filepath, iso_country, start_year):
    """
    Load solar, battery, and financial technology costs for a given ISO country
    and start year.

    - **Unified BNEF file** (bnef_filepath): solar CAPEX/O&M, financial CRF, and
      battery Fixed O&M by (iso3_country, year). Used for country-matched solar
      and battery FOM.
    - **Battery component file** (battery_filepath): Pack, Rack, BOS+EMS, EPC
      ($/kWh) and PCS+Overhead ($/kW) by year. Produced by
      BNEF/scripts/compile_bnef_costs.py (same pipeline as the unified CSV).
      Gives the full component breakdown so battery cost terms are non-zero.

    Returns a dictionary with keys: 'financial', 'solar', 'battery', compatible
    with ``solvers.optimize_flatblock_highs_unserved_v2`` and related optimizers.
    """
    df = pd.read_csv(bnef_filepath)
    year = int(start_year)

    # Solar + financial (CRF from PV row)
    solar_mask = (
        (df["iso3_country"] == iso_country)
        & (df["year"] == year)
        & (df["technology"] == SOLAR_TECH)
    )
    solar_rows = df[solar_mask]
    if solar_rows.empty:
        raise ValueError(
            f"No BNEF solar cost row for iso3_country='{iso_country}', year={year}, "
            f"technology='{SOLAR_TECH}' in {bnef_filepath}"
        )
    pv_row = solar_rows.iloc[0]

    fin_params = {"CRF": float(pv_row["crf"])}
    pv_capex = pv_row.get("capex_$/kw")
    pv_fom = pv_row.get("fom_$/kw/yr")
    if pd.isna(pv_capex) or pd.isna(pv_fom):
        # Fallback: use global median for this tech/year (e.g. BNEF row with no data)
        same_tech = df[(df["year"] == year) & (df["technology"] == SOLAR_TECH)]
        pv_capex = same_tech["capex_$/kw"].median() if pd.isna(pv_capex) else pv_capex
        pv_fom = same_tech["fom_$/kw/yr"].median() if pd.isna(pv_fom) else pv_fom
        if pd.isna(pv_capex) or pd.isna(pv_fom):
            raise ValueError(
                f"No valid solar cost for iso3_country='{iso_country}', year={year}. "
                f"Re-run BNEF/scripts/compile_bnef_costs.py to fill proxies."
            )
    solar_params = {
        "Capex": float(pv_capex),
        "Fixed O&M": float(pv_fom) * 1000.0,  # $/kW-yr -> $/MW-yr
    }

    # Battery: component breakdown from battery_2025_cost.csv (by year)
    batt_df = pd.read_csv(battery_filepath)
    year_col = str(year)
    if year_col not in batt_df.columns:
        available = [c for c in batt_df.columns if c.strip().isdigit()]
        raise ValueError(
            f"Year {year} not in battery cost file {battery_filepath}. "
            f"Available year columns: {available}"
        )

    def get_battery_row(label: str) -> float:
        row = batt_df[batt_df["Year"] == label]
        if row.empty:
            raise ValueError(
                f"Battery cost file {battery_filepath} has no row 'Year' = '{label}'"
            )
        return float(row[year_col].iloc[0])

    def get_optional_battery_row(label: str, default: float) -> float:
        row = batt_df[batt_df["Year"] == label]
        if row.empty:
            return float(default)
        v = row[year_col].iloc[0]
        if pd.isna(v):
            return float(default)
        return float(v)

    batt_params = {
        "Thermal CAPEX $/kWh":              get_battery_row(THERMAL_ROW_CAPEX_KWH),
        "Thermal OPEX frac/yr":             get_battery_row(THERMAL_ROW_OPEX_PCT),
        "Thermal Lifetime yr":              get_battery_row(THERMAL_ROW_LIFETIME_Y),
        "Thermal Discount rate":            get_battery_row(THERMAL_ROW_DISCOUNT),
        "Thermal Eff <=200C":               get_battery_row(THERMAL_ROW_EFF_LE_200),
        "Thermal Eff >200C":                get_battery_row(THERMAL_ROW_EFF_GT_200),
        "Thermal T_max C":                  get_battery_row(THERMAL_ROW_T_MAX_C),
        "Thermal T_min C":                  get_battery_row(THERMAL_ROW_T_MIN_C),
        "Thermal Charge/Discharge ratio min": get_battery_row(THERMAL_ROW_CD_RATIO),
    }

    # Battery Fixed O&M: country-matched from unified BNEF (same file as solar)
    batt_mask = (
        (df["iso3_country"] == iso_country)
        & (df["year"] == year)
        & (df["technology"] == BATTERY_TECH)
    )
    batt_bnef_rows = df[batt_mask]
    if batt_bnef_rows.empty:
        raise ValueError(
            f"No BNEF battery row for iso3_country='{iso_country}', year={year}, "
            f"technology='{BATTERY_TECH}' in {bnef_filepath}"
        )
    batt_bnef = batt_bnef_rows.iloc[0]
    fom_batt_kwyr = batt_bnef.get("fom_$/kw/yr")
    if pd.isna(fom_batt_kwyr):
        # Fallback: use global median battery FOM for this tech/year (e.g. BNEF row with no battery data)
        same_batt = df[(df["year"] == year) & (df["technology"] == BATTERY_TECH)]
        fom_batt_kwyr = same_batt["fom_$/kw/yr"].median()
        if pd.isna(fom_batt_kwyr):
            raise ValueError(
                f"No valid battery FOM for iso3_country='{iso_country}', year={year}. "
                f"Re-run BNEF/scripts/compile_bnef_costs.py to fill proxies."
            )
    batt_params["Fixed O&M"] = float(fom_batt_kwyr) * 1000.0  # $/kW-yr -> $/MW-yr

    return {"financial": fin_params, "solar": solar_params, "battery": batt_params}


## Legacy code (previous calculations for WACC, this is handled at the bnef layer now)
def calc_wacc(fin_params):
    """Calculate weighted average cost of capital (WACC) and add to fin_params."""
    R = fin_params["Debt ratio (%)"]
    E = fin_params["Cost of equity (%)"]
    D = fin_params["Cost of debt (bps)"]
    WACC = E * (1 - R) + (D * 0.0001) * R
    fin_params["WACC"] = WACC
    return fin_params


def calc_crf(fin_params, project_lifetime):
    """Calculate capital recovery factor (CRF) and add to fin_params."""
    wacc = fin_params["WACC"]
    CRF = (wacc * (1 + wacc) ** project_lifetime) / ((1 + wacc) ** project_lifetime - 1)
    fin_params["CRF"] = CRF
    return fin_params
