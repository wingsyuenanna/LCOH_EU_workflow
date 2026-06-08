# Off-grid electricity LCOH run

Variant of the EU industrial heat LCOH workflow where **heat pump** and **heat battery** pathways use **off-grid solar + storage electricity** from BNEF country cost data, not Eurostat grid industrial tariffs.

## Inputs

Shared data live in the **repo root** `../inputs/` (no local `inputs/` folder in this run directory):

| File | Role |
|------|------|
| `../inputs/eprtr_lcp_matched.csv` | Facilities + LCP fuel match |
| `../inputs/bnef_country_costs.csv` | BNEF LCOE/Capex by country |
| `outputs/cached_data/eurostat_gas_prices_all_years.csv` | Gas prices (unchanged from grid run) |

## Off-grid electricity prices

The electricity input price differs by pathway because **the heat battery already provides the storage function** that a BESS would otherwise serve:

| Pathway | Electricity price | Rationale |
|---------|------------------|-----------|
| Heat battery | `LCOE_PV` only | Heat battery stores energy as heat — BESS not needed and must not be double-counted |
| Heat pump | `LCOE_PV + LCOE_BESS_4h` | Heat pump has no intrinsic storage, BESS required to bridge PV intermittency |

For each facility (ISO3 + `analysis_year` → nearest BNEF year **2025** or **2030**):

```
# Heat battery charging electricity
elec_hb_EUR/MWh = LCOE_PV_USD/MWh × 0.921

# Heat pump electricity
elec_hp_EUR/MWh = (LCOE_PV + LCOE_BESS_4h)_USD/MWh × 0.921
```

Technologies in `bnef_country_costs.csv`: `PV fixed-axis`, `Utility-scale battery (4h)`.

## Heat battery CAPEX in BNEF file

Rows **`Industrial heat battery (thermal)`** (394 rows, 197 countries × 2025/2030) provide:

- **CAPEX**: €150/kW_th (2025), −10% by 2030 — from LCOH spec A7 / NREL Wikoff firebrick TES
- **LCOE column**: levelized **CAPEX + fixed O&M only** ($/MWh_th at 85% CF) — excludes charging electricity (added separately at PV-only rate)

Re-generate HB rows: `python3 ../scripts/add_heat_battery_to_bnef.py`

## Run

```bash
cd off_grid_electricity_run
python3 lcoh_calculation.py
```

Outputs go to `./outputs/`.

## Output columns (electricity prices)

| Column | Description |
|--------|-------------|
| `elec_price_hp_EUR_MWh` | PV + BESS LCOE used for heat pump |
| `elec_price_hb_EUR_MWh` | PV-only LCOE used for heat battery charging |

## Compare to grid run

| | `grid_electricity_run` | `off_grid_electricity_run` |
|--|------------------------|----------------------------|
| HP energy price | Eurostat industrial electricity | BNEF PV + 4h BESS |
| HB energy price | Eurostat industrial electricity | BNEF PV only (no BESS) |
| Gas | Eurostat | Eurostat |
| HB CAPEX | Fixed A7 (€150/kW_th) | BNEF per country when available |

---

## Solar Feasibility Extension (115-facility subset)

A site-level solar feasibility analysis is run for the **115 facilities** that have:
- `match_method == id_direct`
- `eprtr_activity != 1(c)` (non-power sector)
- `eprtr_co2_year == 2024`

### Scripts (`scripts/`)

| Script | Purpose | Status |
|--------|---------|--------|
| `pull_pvgis_115.py` | Fetch PVGIS ERA5 hourly solar profiles for all 115 sites; compute annual capacity factor | **Complete** |
| `solar_feasibility_lcoh.py` | Compute site-specific PV LCOE and HB LCOH from PVGIS CF; calculate required land; assess land feasibility | **Complete** |
| `process_land_availability.py` | Post-process GEE Drive export into `suitable_land.csv` | Pending GEE run |

### Inputs for the 115-facility analysis

| File | Source | Status |
|------|--------|--------|
| `../inputs/gee_facilities_115.csv` | Filtered from `eprtr_lcp_matched.csv` | Ready |
| `outputs/pvgis_solar_cf_115.csv` | PVGIS API via `pull_pvgis_115.py` | **Available** |
| `outputs/pvgis_hourly/` | 115 hourly CSVs, 8760 rows each | **Available** |
| `outputs/suitable_land.csv` | GEE ESA WorldCover land cover (10km buffer, slope <5°, WDPA masked) | **Pending GEE** |
| `outputs/solar_feasibility_115.csv` | Final output joining all of the above | **Available** (land columns = PENDING) |

### Site-specific LCOE methodology

The BNEF country-level LCOE embeds a national-average capacity factor. For site-specific accuracy, we substitute the PVGIS site CF:

```
site_LCOE (EUR/MWh_e) = BNEF_annual_cost ($/kW/yr) × EUR/USD
                        ─────────────────────────────────────────
                         PVGIS_CF × 8760 / 1000  (MWh/kW/yr)
```

This keeps BNEF's financing (CRF, WACC) and CAPEX, but replaces the implied CF with the site-specific ERA5 value.

HB LCOH = fixed_capex_om + site_LCOE / η_hb   (η_hb = 0.90)

### Land feasibility methodology

```
electricity_needed (MWh_e/yr) = heat_demand_MWh_th / η_hb
solar_needed (MW_p)           = electricity_needed / (PVGIS_CF × 8760)
land_needed (km²)             = solar_needed / 10 MW/km²   [utility-scale density]
```

Land feasibility requires GEE output (`suitable_land.csv`):
- `FEASIBLE`           : `suitable_land_km2 >= land_needed_km2`
- `INFEASIBLE`         : `suitable_land_km2 < land_needed_km2`
- `LAND_DATA_PENDING`  : GEE output not yet available — rerun `solar_feasibility_lcoh.py` after `process_land_availability.py`

GEE land suitability excludes:
- Protected areas (WDPA: Designated, Inscribed, Established) + 100 m buffer
- Slope > 5° (SRTM)
- Water, snow/ice, wetlands, mangroves, built-up

Suitable classes summed: **grassland + shrubland + bare/sparse vegetation + cropland**
Cropland (ESA WorldCover class 40) is included to reflect agrivoltaic potential.
Many of the sites are agricultural processors (sugar beet, starch) surrounded by
their own feedstock farmland — co-location of solar and agriculture is appropriate.

### How to complete the land feasibility step

1. Upload `../inputs/gee_facilities_115.csv` to GEE as a FeatureCollection asset
   (set latitude/longitude columns accordingly in the upload dialog)
2. Run the GEE script:
   ```bash
   # install earthengine-api first: pip install earthengine-api && earthengine authenticate
   python /path/to/gee_facility_calculate_available_area.py \
     --asset users/YOUR_GEE_USERNAME/eprtr_115_sites \
     --buffer-m 10000
   ```
3. Download the exported CSV from Google Drive (`LandCover_Area_Categorized_10km`)
4. Process into `suitable_land.csv`:
   ```bash
   python scripts/process_land_availability.py --gee-output /path/to/downloaded.csv
   ```
5. Rerun feasibility (land columns will now be populated):
   ```bash
   python scripts/solar_feasibility_lcoh.py
   ```

### Key results (LCOH — land feasibility pending)

| Metric | Value |
|--------|-------|
| Sites with LCOH computed | 110 / 115 |
| Site-specific PV LCOE range | 20–50 EUR/MWh_e |
| Site-specific HB LCOH range | 24–58 EUR/MWh_th |
| HB cheaper than gas | 110 / 110 computed sites |
| Sites requiring > 314 km² land (> 10 km buffer area) | see `solar_feasibility_115.csv` |
| FEASIBLE (sufficient land within 10 km) | 81 / 110 |
| INFEASIBLE (land-constrained) | 29 / 110 |
