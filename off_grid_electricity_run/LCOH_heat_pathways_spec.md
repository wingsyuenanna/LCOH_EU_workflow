# Research Spec: LCOH Comparison — Off-Grid Solar + BESS Electricity (EU E-PRTR Facilities)

**Version:** 1.4.0
**Date:** 2026-05-29
**Status:** Draft
**Variant:** `off_grid_electricity_run`

---

## 1. Objective

For each EU industrial facility in the **E-PRTR-derived input dataset**, calculate and compare the levelized cost of heat (LCOH) for **eligible** pathways, using **off-grid solar PV + battery storage** as the electricity source for electrification:

1. Electrified heat via **heat pump** — low-temperature process heat only (Section 3.5)
2. Electrified heat via **heat battery** (electric charging + thermal discharge) — all temperature bands where process temperature ≤ reference max discharge temperature (A20; default 1500 °C for Rondo)
3. **Natural gas** baseline — all temperature bands (always computed when heat demand is in scope)

**Key distinction from grid run:** Electricity cost for pathways 1 and 2 is derived from country-level off-grid solar PV + 4-hour BESS levelized cost of electricity (LCOE) from the **BNEF country cost dataset** (`inputs/bnef_country_costs.csv`). Grid electricity tariffs are not used. Gas prices remain Eurostat-sourced.

The workflow must assign a process-heat temperature band per facility, skip ineligible pathway calculations (rather than treating them as infinitely expensive), and compare `least_cost_pathway` across **eligible** pathways only. Each result row must include a `lcoh_confidence` rating (Section 6.8) and the verification pass must confirm that computed LCOH values are within the range of published literature benchmarks (Section 9).

---

## 2. Input Data (E-PRTR + Matched Fuel Use)

**Primary input file:** `inputs/eprtr_lcp_matched.csv`

Each row represents one facility record containing E-PRTR facility metadata plus matched fuel-use totals (in TJ) from the linked combustion dataset, with fields prefixed with `lcp_`.

### 2.1 Required Columns (Minimum)


| Column                   | Description                                         |
| ------------------------ | --------------------------------------------------- |
| `eprtr_facility_id`      | Facility identifier (stable key)                    |
| `eprtr_facility_name`    | Facility name                                       |
| `eprtr_country`          | Country name as provided in the dataset             |
| `eprtr_activity`         | E-PRTR activity code                                |
| `eprtr_lat`, `eprtr_lon` | Facility coordinates                                |
| `eprtr_co2_year`         | Reporting year (used as `analysis_year` by default) |
| `lcp_total_TJ`           | Total matched energy use (TJ)                       |


### 2.2 Strongly Recommended Columns (Improve Fidelity)


| Column                                      | Description                                                                                                                                                        |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `eprtr_base_id`                             | Base E-PRTR identifier (auditability)                                                                                                                              |
| `eprtr_co2_t`                               | Annual CO₂ emissions (tonnes)                                                                                                                                      |
| `match_method`, `match_km`                  | LCP matching method and distance                                                                                                                                   |
| `lcp_base_id`, `lcp_inspire_id`, `lcp_city` | Linked combustion dataset identifiers                                                                                                                              |
| Fuel-by-type TJ columns                     | Any of: `lcp_NaturalGas_TJ`, `lcp_Coal_TJ`, `lcp_Lignite_TJ`, `lcp_OtherSolidFuels_TJ`, `lcp_LiquidFuels_TJ`, `lcp_Peat_TJ`, `lcp_OtherGases_TJ`, `lcp_Biomass_TJ` |
| `lcp_substitutable_thermal_TJ`              | Thermal energy eligible for electrification (TJ)                                                                                                                   |
| `iso3_country`                              | If present; otherwise derive from `eprtr_country` via mapping table                                                                                                |
| `process_heat_temp_C`                       | If present; reported or externally joined useful-heat supply temperature (°C)                                                                                      |


### 2.3 Accepted Table Shapes

Wide format (one row per facility) with fuel columns in TJ. The workflow may normalize internally to long format for calculations but must preserve original wide values for traceability.

### 2.4 Derived Fields Required for LCOH

The workflow must produce (per facility) the following derived fields before any LCOH calculations:

- `annual_heat_demand_MWh_th`
- `analysis_year` (default = `eprtr_co2_year`)
- `iso3_country` (if missing, derived from `eprtr_country`)
- `current_fuel` (dominant fuel from `lcp_*_TJ` fuel mix)
- `process_heat_temp_band` (`low` | `mid` | `high`)
- `process_heat_temp_C` (nullable)
- `temp_provenance`
- `pathways_evaluated` (comma-separated eligible pathway codes)
- `pathways_excluded` (ineligible pathways with reason codes)

---

## 3. Scope & Boundaries

### 3.1 Geography

- Primary scope: facilities in EU E-PRTR-derived dataset
- Electricity cost sourcing: country-level from BNEF CSV (ISO3 matched); DEU used as fallback when country is absent from BNEF
- Gas price sourcing: country-level where available; EU27 hub fallback allowed with flag

### 3.2 Technologies in Scope

- **Heat pump pathway** (electric input to useful heat via COP) — in scope only for the Low temperature band (Section 3.5)
- **Heat battery pathway** (electric input; charging/discharging losses included) — use Rondo (or equivalent) public charge/discharge metrics including maximum discharge temperature (A20 = 1500 °C). Eligible for Low, Mid, and High bands when `process_heat_temp_C` is unknown or ≤ A20
- **Natural gas pathway** (fuel input adjusted by boiler efficiency) — baseline for all temperature bands

### 3.3 Cost Boundary

LCOH includes:

- Annualized capital cost
- Fixed O&M
- Variable O&M (if non-zero)
- Energy/fuel cost (electricity from off-grid solar + BESS; gas from Eurostat)
- Carbon cost (if carbon pricing is enabled)

LCOH excludes:

- Grid connection and network costs (explicitly off-grid scenario)
- Land acquisition for solar array
- Major process redesign beyond thermal supply
- Tax treatment beyond explicitly modeled policy inputs

### 3.4 Functional Unit

- Primary output unit: `EUR/MWh_th useful heat`

### 3.5 Process Heat Temperature and Pathway Eligibility

#### 3.5.1 Temperature bands


| Band     | Useful-heat supply temperature | Representative uses                                               |
| -------- | ------------------------------ | ----------------------------------------------------------------- |
| **Low**  | ≤ 120 °C                       | Drying, washing, low-pressure steam, hot water, food plants       |
| **Mid**  | > 120 °C and ≤ 400 °C          | Medium-pressure steam, chemicals, paper, metals finishing         |
| **High** | > 400 °C                       | Cement clinker, glass melting, primary metals, high-temp furnaces |


#### 3.5.2 Technology eligibility (mandatory)


| Pathway      | Low band                                    | Mid band                                    | High band                                   |
| ------------ | ------------------------------------------- | ------------------------------------------- | ------------------------------------------- |
| Heat pump    | Eligible                                    | Not eligible — skip, set null               | Not eligible — skip, set null               |
| Heat battery | Eligible if process temp ≤ A20 (or unknown) | Eligible if process temp ≤ A20 (or unknown) | Eligible if process temp ≤ A20 (or unknown) |
| Natural gas  | Eligible                                    | Eligible                                    | Eligible                                    |


**Heat pump rule:** Do not compute `lcoh_heat_pump_EUR_MWhth` for Mid or High band facilities. Set null; flag `[HP_NOT_APPLICABLE_TEMPERATURE]`.

**Heat battery rule:** Eligible across all temperature bands when the required process temperature is ≤ A20. If `process_heat_temp_C` exceeds A20, set null; flag `[HB_NOT_APPLICABLE_TEMPERATURE]`. When temperature is inferred only from band (A21) and no `process_heat_temp_C` is set, evaluate HB for all bands.

#### 3.5.3 Assigning temperature band per facility

Priority order:

1. **Reported temperature** — if `process_heat_temp_C` is present, map to band using thresholds in 3.5.1; set `temp_provenance = TEMP_FROM_DATA`
2. **Activity/sector mapping** — map `eprtr_activity` using assumption A21; set `temp_provenance = TEMP_INFERRED_FROM_ACTIVITY`
3. **Conservative default** — if activity is missing or unmapped, assign **mid**; set `temp_provenance = TEMP_DEFAULT_MID_BAND`

---

## 4. Key Questions Per Facility

The workflow must answer:

1. What is the facility's annual useful heat demand in scope?
2. What process heat temperature band was assigned, and which pathways were excluded as ineligible?
3. What are the computed LCOH values for each **eligible** pathway?
4. Which **eligible** pathway is lowest-cost under base assumptions?
5. What share of each LCOH is energy cost vs capex vs O&M?
6. What off-grid electricity cost was used, from which BNEF country row, and for which year?
7. Are the computed LCOH values within published literature ranges (Section 9.2)?
8. What is the confidence rating for this result (Section 6.8)?

---

## 5. Data Sources

### 5.1 Electricity Cost — BNEF Country Costs CSV

**File:** `inputs/bnef_country_costs.csv`

This is the **primary and sole source** for off-grid electricity cost. Do not retrieve electricity prices from the web.

#### 5.1.1 BNEF CSV column structure


| Column                | Description                                                          |
| --------------------- | -------------------------------------------------------------------- |
| `iso3_country`        | ISO3 country code                                                    |
| `year`                | Cost year (2025 or 2030)                                             |
| `technology`          | Technology label (see Section 5.1.2)                                 |
| `income_group`        | World Bank income group                                              |
| `wb_region`           | World Bank region                                                    |
| `capex_$/kw`          | CAPEX in USD/kW (AC for PV; DC for BESS; thermal for heat battery)   |
| `fom_$/kw/yr`         | Fixed O&M in USD/kW/yr                                               |
| `vom_$/mwh`           | Variable O&M in USD/MWh                                              |
| `lcoe_$/mwh`          | Levelized cost in USD/MWh (see technology-specific notes below)      |
| `wacc_nominal`        | Nominal WACC used (blank if proxied from peers)                      |
| `crf`                 | Capital recovery factor applied                                      |
| `annual_cost_$/kw/yr` | Annualized total cost per kW capacity                                |
| `data_source`         | `bnef` = direct BNEF data; `proxied` = estimated from peer countries |
| `capex_source`        | How CAPEX was estimated                                              |
| `crf_source`          | How CRF/WACC was estimated                                           |
| `proxy_peers`         | `                                                                    |
| `country_name`        | Human-readable country name                                          |


#### 5.1.2 Technology labels used in this run


| Label                               | Used for                                                                       |
| ----------------------------------- | ------------------------------------------------------------------------------ |
| `PV fixed-axis`                     | Solar PV LCOE — used for **both** heat pump and heat battery electricity cost  |
| `Utility-scale battery (4h)`        | 4-hour BESS LCOE — used **only** for the heat pump pathway (see Section 5.1.3) |
| `Industrial heat battery (thermal)` | Heat battery CAPEX (EUR/kW_th); see `inputs/bnef_heat_battery_notes.md`        |


#### 5.1.3 Off-grid electricity cost derivation

The electricity input price differs by pathway because the **heat battery already provides the storage function** that a BESS would otherwise serve:

- **Heat battery pathway:** PV charges the heat battery directly. The heat battery stores energy as heat and dispatches on demand. No BESS is required — the thermal store replaces it. Electricity cost is **PV LCOE only**.
- **Heat pump pathway:** The heat pump has no intrinsic storage and requires dispatchable electricity. A BESS is needed to bridge PV intermittency. Electricity cost is **PV + BESS LCOE**.

```
# Heat battery charging electricity (PV only)
elec_hb_USD_MWh  = lcoe_PV_USD_MWh
elec_hb_EUR_MWh  = elec_hb_USD_MWh × A13

# Heat pump electricity (PV + BESS)
elec_hp_USD_MWh  = lcoe_PV_USD_MWh + lcoe_BESS_USD_MWh
elec_hp_EUR_MWh  = elec_hp_USD_MWh × A13
```

Both `lcoe_PV` and `lcoe_BESS` are read from `bnef_country_costs.csv` for the matching `iso3_country` and `year`.

**Year mapping:** BNEF provides costs for 2025 and 2030. Map `analysis_year` to the nearest BNEF year (≤ 2027 → 2025; ≥ 2028 → 2030). Flag with `[PRICE_YEAR_PROXIED_TO_{bnef_year}]` when a proxy year is used.

**Country fallback:** If `iso3_country` is absent from BNEF, use `DEU` as the fallback. Flag with `[PRICE_FALLBACK_DEU]`.

**BNEF data quality flag:** Record `bnef_data_source` (the `data_source` column from the PV row) in each output row. This field drives the confidence rating in Section 6.8.

#### 5.1.4 Heat battery CAPEX from BNEF

When a `Industrial heat battery (thermal)` row exists for the facility's country and year, use `capex_$/kw × A13` as the heat battery CAPEX in EUR/kW_th (overriding the A7 default). When absent, fall back to `DEU` row, then to A7 default. Log which source was used.

### 5.2 Gas Prices — Eurostat

**Dataset:** Eurostat nrg_pc_203, Band I3 (10,000–99,999 GJ/yr), excluding VAT  
**Cached file:** `outputs/cached_data/eurostat_gas_prices_all_years.csv`  
**Retrieval:** Eurostat SDMX API or download page; re-fetch if cache is older than A14 threshold  
**Unit conversion:** EUR/kWh GCV → EUR/MWh LHV (multiply by 1000 × A17)

See Section 8 for full gas price retrieval protocol.

### 5.3 Other Data Sources


| Source                  | Purpose                                                                    |
| ----------------------- | -------------------------------------------------------------------------- |
| ECB SDW reference rates | EUR/USD conversion (A13)                                                   |
| EEA / IEA               | Grid electricity emission factors for carbon cost on electrification (A11) |
| EU ETS (EEX / ICE)      | Carbon price by year (A10) — applies to gas pathway only                   |


---

## 6. Calculation Framework

### 6.1 Core LCOH Formula

For each eligible pathway `p`:

```
LCOH_p = (Annualized_CAPEX_p + Fixed_O&M_p + Variable_O&M_p + Energy_Cost_p + Carbon_Cost_p)
         / Annual_Useful_Heat_MWh_th
```

### 6.2 Annualization

```
Annualized_CAPEX_p = CAPEX_p × CRF(r, n)
CRF(r, n) = r(1+r)^n / ((1+r)^n − 1)
```

where `r` is real discount rate and `n` is project life in years.

### 6.3 Pathway-Specific Energy Input

Apply only to pathways that pass eligibility gating (Section 6.7).

- Heat pump electricity use (Low band only):
`Elec_MWh_HP = Annual_Useful_Heat_MWh_th / COP`
- Heat battery electricity use:
`Elec_MWh_HB = Annual_Useful_Heat_MWh_th / Roundtrip_Efficiency`
- Natural gas fuel use:
`Gas_MWh_fuel = Annual_Useful_Heat_MWh_th / Boiler_Efficiency`

### 6.4 Energy Cost Terms

Electricity prices differ by pathway (Section 5.1.3). Grid tariffs are not used.

```
Energy_Cost_HP  = Elec_MWh_HP  × elec_hp_EUR_MWh   (PV + BESS LCOE)
Energy_Cost_HB  = Elec_MWh_HB  × elec_hb_EUR_MWh   (PV LCOE only — no BESS)
Energy_Cost_Gas = Gas_MWh_fuel × Gas_Price_EUR_MWh_LHV
```

**Note on heat battery energy cost:** The heat battery LCOH has two components: (a) the annualized capex + FOM of the thermal storage unit (from BNEF or A7 default), and (b) the electricity cost for charging, priced at PV LCOE only. The BESS LCOE is excluded because the heat battery itself is the storage device — adding BESS cost would double-count storage. The BNEF `lcoe_$/mwh` for the heat battery row covers only (a); charging electricity is added separately at the PV-only rate.

### 6.5 Carbon Cost (Optional Toggle)

If enabled (A_ENABLE_CARBON = True):

```
Carbon_Cost_gas = (Gas_MWh_fuel / Boiler_Efficiency) × A12 × Carbon_Price_EUR_tCO2
```

Carbon cost on electrification pathways is not added separately; any grid emission cost is considered internal to the off-grid system and not modeled here.

### 6.6 Deriving Useful Heat Demand from E-PRTR Fuel Use

Unit conversion throughout: `1 TJ = 277.7777778 MWh`

#### 6.6.1 Preferred: `lcp_substitutable_thermal_TJ`

If present and > 0:

- `annual_heat_demand_MWh_th = lcp_substitutable_thermal_TJ × 277.7777778`
- Provenance flag: `[HEAT_DEMAND_FROM_SUBSTITUTABLE_THERMAL]`

#### 6.6.2 Fallback: `lcp_NaturalGas_TJ`

If `lcp_substitutable_thermal_TJ` missing/zero but `lcp_NaturalGas_TJ` > 0:

- `annual_heat_demand_MWh_th = lcp_NaturalGas_TJ × 277.7777778 × gas_boiler_efficiency`
- Provenance flag: `[HEAT_DEMAND_INFERRED_FROM_GAS_FUEL]`

#### 6.6.3 Fallback: `lcp_total_TJ`

If neither above, but `lcp_total_TJ` > 0:

- `annual_heat_demand_MWh_th = lcp_total_TJ × 277.7777778 × heat_share_of_fuel × baseline_efficiency`
- Provenance flags: `[HEAT_DEMAND_INFERRED_FROM_TOTAL_FUEL]` + `[HEAT_SHARE_DEFAULT_USED]`

### 6.7 Pathway Eligibility Gating

Before computing LCOH for pathway `p`:

1. Resolve `process_heat_temp_band` and `process_heat_temp_C` per Section 3.5.3
2. Determine eligibility:
  - `heat_pump`: only if `band = low` and (`process_heat_temp_C` is null or ≤ A19)
  - `heat_battery`: if `process_heat_temp_C` is null or ≤ A20 (any band)
  - `natural_gas`: always when `annual_heat_demand_MWh_th` > 0
3. If not eligible: set `lcoh_*` to null; append exclusion reason to `pathways_excluded`
4. `least_cost_pathway` = minimum LCOH over **eligible** pathways only; null HP/HB are excluded from ranking

### 6.8 Confidence Rating

Every result row must include `lcoh_confidence` ∈ {`HIGH`, `MEDIUM`, `LOW`, `NOT_COMPUTED`}.

**NOT_COMPUTED** — when all LCOH values are null (no heat data or all pathways excluded).

**LOW** — assign LOW if any of the following apply:

- `bnef_data_source = "proxied"` and `crf_source = "peer_mean"` with `capex_source = "global_mean"` (least specific proxy — covers a wide peer group of dissimilar countries)
- `temp_provenance = TEMP_DEFAULT_MID_BAND` (activity missing or unmapped)
- `annual_heat_demand_provenance = HEAT_DEMAND_INFERRED_FROM_TOTAL_FUEL` (weakest heat demand derivation)
- Any `[PRICE_FALLBACK_*]` flag present (country not in BNEF or Eurostat — EU/DEU average used)
- Any `[PRICE_NOT_AVAILABLE]` flag present
- Computed LCOH falls outside plausibility range for its band (Section 9.2 literature check flags this)

**HIGH** — assign HIGH only if all of the following hold:

- `bnef_data_source = "bnef"` (direct BNEF country data, not peer-proxied)
- `temp_provenance = TEMP_FROM_DATA` (facility has a reported process temperature)
- `annual_heat_demand_provenance = HEAT_DEMAND_FROM_SUBSTITUTABLE_THERMAL`
- No `[PRICE_FALLBACK_*]`, `[PRICE_NOT_AVAILABLE]`, or `[PRICE_YEAR_PROXIED_*]` flags
- Computed LCOH values pass the literature plausibility check (Section 9.2)

**MEDIUM** — all remaining cases that are neither HIGH nor LOW (typical case for proxied BNEF + activity-inferred temperature or gas-fuel-inferred heat demand).

Apply the rating independently of pathway eligibility — i.e., a HIGH-confidence result may still have null HP LCOH if HP is temperature-ineligible.

---

## 7. Assumptions Register


| ID              | Assumption                                           | Default Value            | Unit          | Source Requirement                                                 |
| --------------- | ---------------------------------------------------- | ------------------------ | ------------- | ------------------------------------------------------------------ |
| A1              | Real discount rate                                   | 8%                       | %             | IEA/industry finance benchmark                                     |
| A2              | Project life                                         | 20                       | years         | Technology-economic reference                                      |
| A3              | Heat pump COP (industrial)                           | 2.8                      | ratio         | Peer-reviewed or institutional report                              |
| A4              | Heat battery roundtrip efficiency                    | 0.90                     | ratio         | Vendor-agnostic literature                                         |
| A5              | Gas boiler efficiency                                | 0.90                     | ratio         | Engineering standard                                               |
| A6              | Heat pump CAPEX                                      | project benchmark        | EUR/kW_th     | Published cost source by year                                      |
| A7              | Heat battery CAPEX (default)                         | 150                      | EUR/kW_th     | NREL 2025 (Wikoff et al.) firebrick TES; used when BNEF row absent |
| A8              | Natural gas boiler CAPEX                             | 75                       | EUR/kW_th     | IEA/JRC published cost range                                       |
| A9              | Fixed O&M (HP/HB/Gas)                                | pathway-specific         | % capex/yr    | Published source                                                   |
| A10             | Carbon price                                         | policy-year specific     | EUR/tCO2e     | EU ETS annual average (EEX/ICE)                                    |
| A11             | Electricity emission factor                          | country/year-specific    | tCO2e/MWh     | EEA Air Emission Accounts                                          |
| A12             | Natural gas emission factor                          | 0.202                    | tCO2e/MWh_LHV | IPCC 2006 GL; UNFCCC default                                       |
| A13             | ECB EUR/USD reference rate                           | 0.921 (2024 annual avg)  | EUR/USD       | ECB SDW                                                            |
| A14             | Data staleness threshold                             | 24                       | months        | Methodological rule                                                |
| A15             | Verification tolerance (literature plausibility)     | 50%                      | % deviation   | Wider band appropriate for off-grid vs published benchmarks        |
| A16             | Heat share of reported fuel use                      | 0.85                     | fraction      | Conservative sector default                                        |
| A17             | Gas GCV/LHV conversion                               | 1.1098                   | ratio         | Eurogas; IPCC 2006 GL Table 1.2                                    |
| A18             | Treatment of non-gas fuels in baseline               | exclude unless mapped    | rule          | Methodological rule                                                |
| A19             | Max useful heat temperature for heat pump            | 120                      | °C            | IRENA 2022; JRC 2022 industrial HP technical limit                 |
| A20             | Max discharge temperature for reference heat battery | 1500                     | °C            | Rondo public specification                                         |
| A21             | `eprtr_activity` → process heat temperature band     | see table in Section 7.1 | band          | Sector heat surveys, IEA/industrial decarb literature              |
| A_CF            | Industrial process heat capacity factor              | 0.85                     | fraction      | IEA industrial heat demand literature                              |
| A_ENABLE_CARBON | Carbon cost on gas pathway                           | True                     | bool          | Policy modeling choice                                             |


**Note on A15:** The tolerance for the literature plausibility check (Section 9.2) is widened to 50% compared to the grid run (15%), because published off-grid solar + storage LCOH benchmarks vary substantially across EU regions and system sizes. Results outside 50% of the expected band median should be flagged but are not automatically invalidated.

### 7.1 A21 Default Mapping (`eprtr_activity` → temperature band)

Use exact match on `eprtr_activity`. If no match, try progressively shorter prefixes, then top-level digit.


| `eprtr_activity` prefix | Band | Rationale                            |
| ----------------------- | ---- | ------------------------------------ |
| `1`                     | mid  | Mixed boilers and CHP                |
| `2`                     | high | Furnaces, smelting, hot rolling      |
| `3`                     | high | Cement, lime, glass, ceramics        |
| `4`                     | mid  | Steam-driven chemical processes      |
| `5`                     | high | Incineration and high-temp treatment |
| `6`                     | mid  | Sector-heterogeneous                 |
| `7`                     | mid  | Paper drying and steam               |
| `8`                     | low  | Food, drink, low-pressure steam      |
| `9`                     | mid  | Surface treatment baths and ovens    |


Sub-code overrides (apply before parent prefix fallback):


| `eprtr_activity`                           | Band | Notes                                |
| ------------------------------------------ | ---- | ------------------------------------ |
| `8(b)`, `8(b)(i)`, `8(b)(ii)`, `8(c)`      | low  | Food, drink, agricultural processing |
| `3(c)`, `3(c)(i)`, `3(c)(ii)`, `3(c)(iii)` | high | Cement and mineral products          |
| `5(b)`, `5(a)`, `6(b)`, `6(a)`             | high | Iron, steel, non-ferrous metals      |
| `2(b)`, `2(c)`, `2(e)(i)`, `2(e)(ii)`      | high | Ferrous metal processes              |
| `1(c)`                                     | mid  | Generic industrial combustion        |


---

## 8. Gas Price Retrieval Protocol

Electricity prices are read from `inputs/bnef_country_costs.csv` — no web retrieval. This section covers **gas prices only**.

### Phase A — Build Query Set

For each unique country in the facility dataset, construct a query for:

- Eurostat nrg_pc_203, Band I3 (10,000–99,999 GJ/yr), excluding VAT
- `analysis_year` and immediately prior year as comparator

### Phase B — Retrieve Candidate Values

1. Pull latest available Eurostat value and at least one historical comparator
2. Capture metadata: source name, series ID, value, unit, year/month coverage, URL

### Phase C — Normalize

1. Convert from EUR/kWh GCV to EUR/MWh LHV: multiply by 1000 × A17 (1.1098)
2. Apply FX conversion with ECB rate if source is in non-EUR currency

### Phase D — Select Final Value

Hierarchy:

1. Country-specific Eurostat value
2. EU27 hub average (flagged)
3. If data age exceeds A14: apply `[PRICE_DATA_STALE]`

### Phase E — Log and Attach

Write all candidate and selected values to `price_sources_log.csv`.

---

## 9. Verification Workflow

Verification in this run focuses on **whether the computed LCOH results are plausible given published literature**, rather than independently re-sourcing assumption references (which are validated in the assumptions log). Section 9.1 remains for assumption completeness; Section 9.2 is the primary new focus.

### 9.1 Assumption Completeness Check

For each assumption ID in Section 7, confirm:

- A value is recorded in the assumptions register
- A source is cited (or flagged `[ASSUMPTION_SOURCE_UNVERIFIED]`)
- Any deviation from the default is logged with old/new values

### 9.2 Literature Plausibility Check (Primary Verification)

Compare the distribution of computed LCOH results against published benchmarks for EU industrial heat. Flag individual facility results that deviate by more than A15 (50%) from the band median of the benchmark range.

#### 9.2.1 Published benchmark ranges

The following ranges represent the expected span of LCOH values for EU industrial facilities under the off-grid solar + BESS electricity scenario. These are not design targets; they are plausibility guardrails.

**Natural gas baseline LCOH** (EU industrial, inclusive of capex + O&M + ETS carbon):

- Expected range: 30–100 EUR/MWh_th
- Source basis: Eurostat industrial gas prices 2022–2024 (~25–60 EUR/MWh LHV); gas boiler CAPEX amortization; EU ETS at 55–84 EUR/tCO2 (2023–2024)
- Flag if computed value < 20 or > 130 EUR/MWh_th as `[GAS_LCOH_OUT_OF_RANGE]`

**Heat pump LCOH** (industrial, low temperature, off-grid electricity, EU):

- Expected range: 60–180 EUR/MWh_th
- Source basis: HP CAPEX (EUR 800–1,500/kW_th); COP 2.5–3.5; off-grid PV+BESS electricity 50–120 EUR/MWh (most EU countries, 2025 BNEF); 20-year amortization
- Flag if computed value < 40 or > 250 EUR/MWh_th as `[HP_LCOH_OUT_OF_RANGE]`

**Heat battery LCOH** (Rondo-type, charged from off-grid solar + BESS, EU):

- Expected range: 60–200 EUR/MWh_th
- Source basis: HB CAPEX (EUR 100–200/kW_th); roundtrip efficiency 0.90; off-grid electricity 50–120 EUR/MWh; high band HB LCOH will be higher due to temperature tolerance premium
- Flag if computed value < 40 or > 280 EUR/MWh_th as `[HB_LCOH_OUT_OF_RANGE]`

**Off-grid PV + BESS electricity LCOE** (EU, 2025, PV fixed-axis + 4h BESS):

- Expected range: 40–130 USD/MWh (most EU countries based on BNEF NEO 2025)
- Southern Europe (ES, PT, IT, GR): lower end 40–70 USD/MWh
- Northern and Central Europe (DE, PL, SE): mid-to-upper 70–120 USD/MWh
- Flag if computed `off_grid_elec_EUR_MWh` converts to < 35 or > 160 USD/MWh as `[ELEC_LCOE_OUT_OF_RANGE]`

#### 9.2.2 Verification procedure

For each pathway and temperature band group:

1. Compute the median and interquartile range (IQR) of computed LCOH values across all facilities in the group
2. Identify facilities where the LCOH deviates by more than A15 (50%) from the band's **literature range midpoint** (not the computed median — use the literature midpoint from 9.2.1)
3. For each flagged facility, check whether the outlier is explained by:
  - Extreme off-grid electricity price (check `bnef_data_source` and flag quality)
  - Very high or low annual heat demand (check heat demand provenance)
  - Unusual temperature or activity combination
4. Log each flag in `verification_report.md` with the computed value, the benchmark range, and the percentage deviation

#### 9.2.3 Cross-run comparison (if grid run outputs exist)

If `grid_electricity_run/outputs/lcoh_results.csv` exists, compare the off-grid LCOH for each matched facility against the grid run result:

- For heat pump and heat battery: off-grid LCOH should generally be **higher** than grid LCOH for Northern/Central EU countries (where grid electricity is cheaper than off-grid solar+BESS) and potentially **lower** for Southern EU countries with high solar resource
- Flag facilities where off-grid LCOH is lower than grid LCOH in Northern EU (`eprtr_country` in {Germany, Poland, Netherlands, Belgium, Denmark, Sweden, Finland}) as `[OFFGRID_CHEAPER_THAN_GRID_UNEXPECTED]` for manual review
- Flag facilities where off-grid LCOH is more than 3× the grid LCOH as `[OFFGRID_PREMIUM_EXCESSIVE]`

### 9.3 Calculation Traceability

Each facility-pathway result must be reproducible via logged formula + inputs + assumptions + sources. No output row may exist without provenance.

---

## 10. Required Outputs

### 10.1 Facility Results Table

**File:** `outputs/lcoh_results.csv`

Required columns:


| Column                          | Description                                                       |
| ------------------------------- | ----------------------------------------------------------------- |
| `eprtr_facility_id`             |                                                                   |
| `eprtr_facility_name`           |                                                                   |
| `eprtr_country`                 |                                                                   |
| `iso3_country`                  |                                                                   |
| `analysis_year`                 |                                                                   |
| `eprtr_activity`                |                                                                   |
| `eprtr_lat`, `eprtr_lon`        |                                                                   |
| `process_heat_temp_band`        |                                                                   |
| `process_heat_temp_C`           |                                                                   |
| `temp_provenance`               |                                                                   |
| `pathways_evaluated`            | Comma-separated eligible pathway codes                            |
| `pathways_excluded`             | Ineligible pathways with reason codes                             |
| `annual_heat_demand_MWh_th`     |                                                                   |
| `annual_heat_demand_provenance` |                                                                   |
| `natural_gas_fuel_energy_MWh`   |                                                                   |
| `lcp_total_TJ`                  |                                                                   |
| `lcp_substitutable_thermal_TJ`  |                                                                   |
| `lcoh_heat_pump_EUR_MWhth`      | Null if not eligible                                              |
| `lcoh_heat_battery_EUR_MWhth`   | Null if not eligible                                              |
| `lcoh_natural_gas_EUR_MWhth`    |                                                                   |
| `least_cost_pathway`            | Minimum over eligible pathways only                               |
| `delta_vs_gas_hp_EUR_MWhth`     | HP LCOH minus gas LCOH; null if either is null                    |
| `delta_vs_gas_hb_EUR_MWhth`     | HB LCOH minus gas LCOH; null if either is null                    |
| `elec_price_hp_EUR_MWh`         | Off-grid LCOE for heat pump: PV + BESS in EUR/MWh                 |
| `elec_price_hb_EUR_MWh`         | Off-grid LCOE for heat battery: PV only in EUR/MWh (no BESS)      |
| `bnef_data_source`              | `bnef` or `proxied` (from BNEF CSV `data_source` column, PV row)  |
| `bnef_year_used`                | 2025 or 2030 (BNEF year matched to analysis_year)                 |
| `gas_price_used_EUR_MWh`        |                                                                   |
| `lcoh_confidence`               | `HIGH` / `MEDIUM` / `LOW` / `NOT_COMPUTED` per Section 6.8        |
| `assumptions_used`              |                                                                   |
| `verification_status`           | `COMPUTED` / `PRICE_GAP` / `UNVERIFIED_INPUT` / `SKIPPED_NO_HEAT` |
| `flags`                         | Pipe-delimited list of all flags                                  |


### 10.2 Assumption Log

**File:** `outputs/assumptions_log.md`

Full assumption register with source links and verification status.

### 10.3 Price Source Log

**File:** `outputs/price_sources_log.csv`

All retrieved candidate and selected gas price values, and BNEF electricity entries, with metadata (source, year, unit, conversion path).

### 10.4 Verification Report

**File:** `outputs/verification_report.md`

Contains:

- Checklist outcomes (Section 11)
- Literature plausibility check results per temperature band and pathway (Section 9.2)
- Distribution of confidence ratings across facilities
- Cross-run comparison results if grid run outputs exist (Section 9.2.3)
- Unresolved flags and outstanding issues

### 10.5 Method Summary

**File:** `outputs/method_summary.md`

Human-readable narrative of formulas, assumptions, and decision rules. Must include:

- BNEF year used and coverage (% of facilities with direct BNEF vs proxied data)
- Distribution of computed off-grid electricity LCOE values across EU countries
- Summary statistics for LCOH by pathway and temperature band

---

## 11. Verification Checklist

Complete before publishing final outputs.

### Data & Units

- Required input columns present in `eprtr_lcp_matched.csv`
- BNEF CSV contains rows for `PV fixed-axis`, `Utility-scale battery (4h)`, and `Industrial heat battery (thermal)` for all or most EU ISO3 codes
- Heat pump electricity cost = PV LCOE + BESS LCOE (USD/MWh) × A13
- Heat battery electricity cost = PV LCOE only (USD/MWh) × A13 — BESS excluded because heat battery provides storage
- Heat battery CAPEX: BNEF row used where available; A7 default (€150/kW_th) used as fallback — documented per facility
- All energy and currency units normalized and documented
- No null-driven calculations without explicit flag

### Source Traceability

- BNEF electricity prices: `bnef_data_source` recorded for every facility with an electricity price
- Gas prices: every selected price has a source URL, series ID, year, and unit
- A7 default use flagged wherever BNEF heat battery row is absent

### Calculation Integrity

- LCOH formula applied consistently across all eligible pathways
- Temperature band assigned and `temp_provenance` set for every facility with heat demand
- No heat pump LCOH computed for Mid or High band (or when `process_heat_temp_C` > A19)
- Heat battery skipped only when `process_heat_temp_C` > A20 (1500 °C); not skipped by band alone
- `least_cost_pathway` excludes ineligible technologies; null LCOH not treated as lowest cost
- `lcoh_confidence` populated for every row

### Literature Plausibility (Primary Verification)

- Natural gas LCOH: all computed values checked against 30–100 EUR/MWh_th benchmark; outliers documented
- Heat pump LCOH: all computed values checked against 60–180 EUR/MWh_th benchmark; outliers documented
- Heat battery LCOH: all computed values checked against 60–200 EUR/MWh_th benchmark; outliers documented
- Off-grid electricity LCOE: all values checked against 40–130 USD/MWh benchmark; outliers documented
- Outlier count and explanations listed in `verification_report.md`
- Cross-run comparison performed if `grid_electricity_run/outputs/lcoh_results.csv` exists

### Confidence Distribution

- `lcoh_confidence` distribution reported (count of HIGH / MEDIUM / LOW / NOT_COMPUTED)
- HIGH-confidence result count is consistent with number of facilities with direct BNEF data and reported process temperature

### Reporting Completeness

- `lcoh_results.csv` generated for all valid facilities with `lcoh_confidence` column populated
- `assumptions_log.md`, `price_sources_log.csv`, and `verification_report.md` generated
- All unresolved issues listed in `flags` and summarized in verification report

---

## 12. Agent Behavioral Constraints

- Do not retrieve electricity prices from the web. Use `inputs/bnef_country_costs.csv` as the sole electricity cost source.A8
- Do not hallucinate BNEF values. If a country is absent from the BNEF CSV, use the DEU fallback and flag `[PRICE_FALLBACK_DEU]`.
- Do not compute heat pump LCOH when `process_heat_temp_band` is `mid` or `high`, or when `process_heat_temp_C` > A19.
- Do not compute heat battery LCOH when `process_heat_temp_C` > A20 (1500 °C). Do not exclude heat battery by band alone.
- Do not treat null ineligible-pathway LCOH as infinitely expensive in rankings; exclude from `least_cost_pathway`.
- Do not omit the `lcoh_confidence` column or leave it null.
- Do not omit the literature plausibility check. If published benchmark data is unavailable, state this explicitly and document the gap rather than skipping the check silently.
- Do not mix units without explicit conversion logging.
- Preserve complete provenance for every reported LCOH value.

---

*End of Research Spec v1.4.0*