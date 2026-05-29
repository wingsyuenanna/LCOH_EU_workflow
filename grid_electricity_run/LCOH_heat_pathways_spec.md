# Research Spec: Levelized Cost of Heat (LCOH) Comparison for Electric Heat vs Natural Gas (EU E-PRTR Facilities)

**Version:** 1.3.1  
**Date:** 2026-05-28  
**Status:** Draft

---

## 1. Objective

For each EU industrial facility in the **E-PRTR-derived input dataset** (facility + fuel use), calculate and compare the levelized cost of heat (LCOH) for **eligible** pathways given inferred or reported process-heat temperature:

1. Electrified heat via **heat pump** — **low-temperature process heat only** (Section 3.5)
2. Electrified heat via **heat battery** (electric charging + thermal discharge) — all temperature bands where process requirement ≤ reference max discharge temperature (A20; default 1500 °C for Rondo)
3. **Natural gas** baseline — all temperature bands (always computed when heat demand is in scope)

The workflow must assign a process-heat temperature band per facility, skip ineligible pathway calculations (rather than treating them as infinitely expensive), and compare `least_cost_pathway` across **eligible** pathways only.

The workflow must retrieve current and recent fuel/electricity prices, capex and opex from approved web sources, convert all scenarios to a consistent unit basis, and produce transparent, auditable outputs where each assumption and external value is tied to a published document or official data source.

---

## 2. Input Data (E-PRTR + Matched Fuel Use)

**Primary input file:** `inputs/eprtr_lcp_matched.csv`

Each row represents one facility record containing E-PRTR facility metadata plus matched fuel-use totals (in TJ) from the linked combustion dataset fields prefixed with `lcp_`.

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
| `process_heat_temp_C`                       | If present; reported or externally joined useful-heat supply temperature (°C)                                                                                    |


### 2.3 Accepted Table Shapes

This input is expected in **wide format** (one row per facility) with fuel columns in TJ. The workflow may normalize internally to a long format (fuel rows) for calculations, but must preserve the original wide values for traceability.

### 2.4 Derived Fields Required for LCOH

The workflow must produce (per facility) the following derived fields before any LCOH calculations:

- `annual_heat_demand_MWh_th` (useful heat in scope)
- `analysis_year` (default = `eprtr_co2_year`)
- `iso3_country` (if missing, derived from `eprtr_country`)
- `current_fuel` (baseline fuel, derived from `lcp_*_TJ` fuel mix)
- `process_heat_temp_band` (`low` | `mid` | `high`)
- `process_heat_temp_C` (nullable; representative supply temperature when known or inferred)
- `temp_provenance` (see Section 3.5.3)
- `pathways_evaluated` (comma-separated eligible pathway codes, e.g. `gas,hb`)
- `pathways_excluded` (ineligible pathways with reason codes)

Derivation rules for heat demand are defined in Section 6.6. Temperature band and pathway eligibility are defined in Sections 3.5 and 6.7.

---

## 3. Scope & Boundaries

### 3.1 Geography

- Primary scope: facilities in EU E-PRTR-derived dataset
- Price sourcing: country-level where available; regional fallback (e.g., EU hub) allowed with flag

### 3.2 Technologies in Scope

- **Heat pump pathway** (electric input to useful heat via COP) — use one published industrial high-temperature heat pump example for COP, capex, and opex. **In scope only for the Low temperature band** (Section 3.5); do not analyze heat pumps for mid- or high-temperature processes.
- **Heat battery pathway** (electric input; charging/discharging losses included) — use **Rondo** (or equivalent) public charge/discharge metrics, including **maximum discharge temperature** (Rondo documents discharge up to **1500 °C**; capture URL in assumptions log). Eligible for **Low, Mid, and High** bands when `process_heat_temp_C` is unknown or ≤ A20. Heat pumps remain excluded for mid/high processes; heat batteries are not.
- **Natural gas pathway** (fuel input adjusted by boiler efficiency) — use one published gas boiler example for capex and opex. **Baseline for all temperature bands.**

### 3.3 Cost Boundary

LCOH includes:

- Annualized capital cost
- Fixed O&M (opex)
- Variable O&M (if non-zero)
- Energy/fuel cost
- Carbon cost (if carbon pricing is enabled)

LCOH excludes unless explicitly added:

- Network reinforcement costs
- Major process redesign beyond thermal supply
- Tax treatment beyond explicitly modeled policy inputs

### 3.4 Functional Unit

- Primary output unit: `EUR/MWh_th useful heat`
- Secondary optional unit: `USD/MMBtu`

### 3.5 Process Heat Temperature and Pathway Eligibility

Electrification technologies are not interchangeable across temperature levels. The workflow must assign each facility a **process heat temperature band** and compute LCOH only for eligible pathways.

#### 3.5.1 Temperature bands (default taxonomy)

| Band    | Typical useful-heat supply temperature | Representative uses                                              |
| ------- | -------------------------------------- | ---------------------------------------------------------------- |
| **Low** | ≤ 120 °C                               | Drying, washing, low-pressure steam, hot water, many food plants   |
| **Mid** | > 120 °C and ≤ 400 °C                  | Medium-pressure steam, chemicals, paper, parts of metals finishing |
| **High** | > 400 °C                              | Cement clinker, glass melting, primary metals, high-temp furnaces |

Bands refer to **useful heat delivery temperature** at the process boundary (not flue gas or ambient).

#### 3.5.2 Technology eligibility (mandatory)

| Pathway      | Low band | Mid band | High band |
| ------------ | -------- | -------- | --------- |
| Heat pump    | Eligible | Not eligible — skip HP LCOH | Not eligible — skip HP LCOH |
| Heat battery | Eligible if process temp ≤ A20 (or unknown) | Eligible if process temp ≤ A20 (or unknown) | Eligible if process temp ≤ A20 (or unknown) |
| Natural gas  | Eligible | Eligible | Eligible |

**Heat pump rule:** Do not compute `lcoh_heat_pump_EUR_MWhth` for Mid or High band facilities. Set the value to null and add flag `[HP_NOT_APPLICABLE_TEMPERATURE]`. Do not include heat pump in `least_cost_pathway` for these facilities.

**Heat battery rule:** Eligible across all temperature bands when the required process temperature is ≤ A20 (default **1500 °C**, per Rondo public specification). If `process_heat_temp_C` exceeds A20, skip HB LCOH, set null, and flag `[HB_NOT_APPLICABLE_TEMPERATURE]`. When temperature is inferred only from band (A21) and no `process_heat_temp_C` is set, evaluate HB for all bands (including High).

#### 3.5.3 Assigning temperature band per facility

Priority order:

1. **Reported temperature** — if `process_heat_temp_C` is present in input or from an external join, map to band using Section 3.5.1 thresholds (≤120 °C → low; >120–400 °C → mid; >400 °C → high). Use A20 only for heat-battery pathway eligibility, not band assignment; set `temp_provenance = TEMP_FROM_DATA`.
2. **Activity/sector mapping** — map `eprtr_activity` (and `nace_code` if available) to `low` / `mid` / `high` using assumption **A21**; set `temp_provenance = TEMP_INFERRED_FROM_ACTIVITY`.
3. **Conservative default** — if activity is missing or unmapped, assign **mid**; set `temp_provenance = TEMP_DEFAULT_MID_BAND` (heat pump skipped; heat battery and gas still evaluated if otherwise eligible).

Log mapping rationale and literature/source for A21 overrides.

#### 3.5.4 Interaction with COP and reference technologies

- Heat pump COP (A3) applies **only** when `process_heat_temp_band = low` and `process_heat_temp_C` (if set) ≤ A19.
- Heat battery roundtrip efficiency and capex (A4, A7) apply when eligible under Section 3.5.2 (including High band facilities such as cement and steel, unless `process_heat_temp_C` > A20).
- Natural gas baseline (A5, A8) applies for all bands.

---

## 4. Key Questions Per Facility

The workflow must answer:

1. What is the facility’s annual useful heat demand in scope?
2. What process heat temperature band was assigned, and which pathways were excluded as ineligible?
3. What are the computed LCOH values for each **eligible** pathway (heat pump, heat battery, natural gas)?
4. Which **eligible** pathway is lowest-cost under base assumptions?
5. What share of each LCOH is energy/fuel vs capex vs O&M?
6. Which assumptions most drive the result (sensitivity)?
7. Which external fuel/power prices were used, from what source, and for what date range?
8. Can each assumption be traced to a published document and verification status?

---

## 5. Approved Web Sources for Price and Assumption Retrieval

The agent must use source tiers and record source metadata for every external numeric value.

### 5.1 Primary Sources (Preferred)


| Source                                                    | Data Type              | Typical Use                                                                                             |
| --------------------------------------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------- |
| E-PRTR (European Pollutant Release and Transfer Register) | Regulatory database    | Facility identifiers, location, activity classification, reported energy/fuel-related fields if present |
| Eurostat energy price datasets                            | Statistical database   | Country-level industrial gas/electricity prices                                                         |
| ACER / EU market monitoring                               | Regulatory publication | Power/gas market context and benchmarks                                                                 |
| ENTSO-E transparency data                                 | Power market data      | Electricity market validation                                                                           |
| National energy regulators / ministries                   | Official releases      | Country-specific tariff/fuel benchmark                                                                  |
| IEA / IRENA / World Bank data portals                     | Institutional datasets | Cross-country fallback benchmarks                                                                       |
| ECB reference exchange rates                              | Official FX            | Currency normalization                                                                                  |
| Peer-reviewed journals                                    | Academic evidence      | Technology performance/cost assumptions                                                                 |


### 5.2 Secondary Sources (Allowed, Must Be Flagged)

- Industry reports citing primary datasets
- Consultancy reports with transparent methodology
- Exchange or hub commentary pages

### 5.3 Disallowed for Quantitative Inputs

- Unsourced blogs, marketing pages, and news-only values without traceable primary dataset

### 5.4 Source Hierarchy Rule

If secondary source data is used, the agent must attempt to trace to primary. If not traceable, mark:
`[UNVERIFIED_SECONDARY_ONLY]`
and do not use it to overwrite an available primary-source value.

---

## 6. Calculation Framework

### 6.1 Core LCOH Formula

For each pathway `p`:

`LCOH_p = (Annualized_CAPEX_p + Fixed_O&M_p + Variable_O&M_p + Energy_Cost_p + Carbon_Cost_p) / Annual_Useful_Heat_MWh_th`

### 6.2 Annualization

`Annualized_CAPEX_p = CAPEX_p * CRF(r, n)`

`CRF(r, n) = r(1+r)^n / ((1+r)^n - 1)`

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

- `Energy_Cost_HP = Elec_MWh_HP * Electricity_Price_(EUR/MWh)`
- `Energy_Cost_HB = Elec_MWh_HB * Electricity_Price_(EUR/MWh)`
- `Energy_Cost_Gas = Gas_MWh_fuel * Gas_Price_(EUR/MWh_LHV_or_HHV_adjusted)`

### 6.5 Carbon Cost (Optional Toggle)

- If enabled:  
`Carbon_Cost_p = Emissions_tCO2e_p * Carbon_Price_(EUR/tCO2e)`
- Must document emission factors and whether scope includes combustion only or lifecycle.

### 6.6 Deriving Useful Heat Demand from E-PRTR Fuel Use (Critical)

Because E-PRTR-derived datasets commonly provide **fuel use** but not directly **useful heat output**, the workflow must define a consistent method to infer `annual_heat_demand_MWh_th` from fuel energy.

Unit conversion required throughout:

- `1 TJ = 277.7777778 MWh`

#### 6.6.1 Preferred: Use `lcp_substitutable_thermal_TJ` if Available

If `lcp_substitutable_thermal_TJ` is present and > 0:

- Set `annual_heat_demand_MWh_th = lcp_substitutable_thermal_TJ * 277.7777778`
- Flag provenance: `[HEAT_DEMAND_FROM_SUBSTITUTABLE_THERMAL]`

#### 6.6.2 Fallback: Infer Useful Heat from Natural Gas Fuel Use

If `lcp_substitutable_thermal_TJ` is missing/zero but `lcp_NaturalGas_TJ` is present and > 0:

- Set `annual_heat_demand_MWh_th = (lcp_NaturalGas_TJ * 277.7777778) * gas_boiler_efficiency`
- Set `current_fuel = natural_gas`
- Flag provenance: `[HEAT_DEMAND_INFERRED_FROM_GAS_FUEL]`

#### 6.6.3 Fallback: Infer from `lcp_total_TJ` Using Heat Share Allocation

If neither `lcp_substitutable_thermal_TJ` nor `lcp_NaturalGas_TJ` can be used, but `lcp_total_TJ` is present and > 0:

- Infer in-scope useful heat as:
  - `annual_heat_demand_MWh_th = (lcp_total_TJ * 277.7777778) * heat_share_of_fuel * baseline_efficiency`
  - `heat_share_of_fuel` is assumption A16 (activity/sector-based)
  - `baseline_efficiency` defaults to A5 unless a better mapping exists by dominant fuel
- Flag provenance: `[HEAT_DEMAND_INFERRED_FROM_TOTAL_FUEL]` and `[HEAT_SHARE_DEFAULT_USED]` if activity/sector mapping is missing.

#### 6.6.4 Heat-in-Scope Allocation (When Needed)

If fuel use includes both process energy and heat and no substitutable thermal is reported:

- Apply an allocation factor `heat_share_of_fuel` (assumption A16) by `eprtr_activity` (and/or `nace_code` if available) where possible.
- Log: fuel energy → allocation → efficiency → useful heat.
- If allocation is required but activity/sector is missing, use a conservative default allocation and flag `[HEAT_SHARE_DEFAULT_USED]`.

### 6.7 Pathway Eligibility Gating

Before Sections 6.1–6.4 for pathway `p`:

1. Resolve `process_heat_temp_band` and `process_heat_temp_C` per Section 3.5.3.
2. Determine eligibility:
   - `heat_pump`: only if band = `low` and (`process_heat_temp_C` is null or `process_heat_temp_C` ≤ A19).
   - `heat_battery`: if `process_heat_temp_C` is null or `process_heat_temp_C` ≤ A20 (any band, including `high`).
   - `natural_gas`: always when `annual_heat_demand_MWh_th` > 0.
3. If pathway `p` is not eligible, skip CAPEX/O&M/energy/carbon for `p`; set `lcoh_*` to null; append exclusion reason to `pathways_excluded`.
4. Set `pathways_evaluated` to the list of eligible pathway codes (e.g. `gas`, `gas,hb`, `gas,hp,hb`).
5. For `least_cost_pathway`, use minimum LCOH over **eligible** pathways only. If only gas is eligible, `least_cost_pathway = natural_gas`.
6. For `delta_vs_gas_hp_EUR_MWhth` and `delta_vs_gas_hb_EUR_MWhth`, compute only when both gas and the respective electrified pathway are eligible; otherwise null.

---

## 7. Assumptions Register (Mandatory Traceability)

Every hardcoded/default value requires an assumption ID and source citation.


| ID  | Assumption                                                      | Default Value               | Unit                    | Source Requirement                                            |
| --- | --------------------------------------------------------------- | --------------------------- | ----------------------- | ------------------------------------------------------------- |
| A1  | Real discount rate                                              | 8%                          | %                       | Published guidance (e.g., IEA/industry finance benchmark)     |
| A2  | Project life                                                    | 20                          | years                   | Technology-economic reference                                 |
| A3  | Heat pump COP (industrial)                                      | 2.8                         | ratio                   | Peer-reviewed or institutional report                         |
| A4  | Heat battery roundtrip efficiency                               | 0.90                        | ratio                   | Vendor-agnostic literature/source                             |
| A5  | Gas boiler efficiency                                           | 0.90                        | ratio                   | Engineering standard/source                                   |
| A6  | Heat pump CAPEX                                                 | project input or benchmark  | EUR/kW_th               | Published cost source by year                                 |
| A7  | Heat battery CAPEX                                              | project input or benchmark  | EUR/kW_th or EUR/kWh_th | Published cost source by year                                 |
| A8  | Natural gas boiler CAPEX                                        | project input or benchmark  | EUR/kW_th               | Published cost source                                         |
| A9  | Fixed O&M (HP/HB/Gas)                                           | pathway-specific            | % capex/yr              | Published source                                              |
| A10 | Carbon price                                                    | policy-year specific        | EUR/tCO2e               | Official policy source                                        |
| A11 | Electricity emission factor                                     | country/year-specific       | tCO2e/MWh               | Official inventory/database                                   |
| A12 | Natural gas emission factor                                     | default or country-specific | tCO2e/MWh_fuel          | Official inventory/database                                   |
| A13 | Currency conversion basis                                       | ECB daily/monthly average   | FX rate                 | ECB URL required                                              |
| A14 | Data staleness threshold                                        | 24 months                   | months                  | Methodological rule                                           |
| A15 | Verification tolerance                                          | 15%                         | % deviation             | Methodological rule                                           |
| A16 | Heat share of reported fuel use (when heat output not reported) | sector-dependent default    | fraction                | Published benchmark by sector or documented conservative rule |
| A17 | Gas energy basis normalization (LHV vs HHV)                     | LHV unless documented       | basis                   | Documented conversion source or explicit metadata rule        |
| A18 | Treatment of non-gas fuels in baseline                          | exclude unless mapped       | rule                    | Methodological rule + rationale                               |
| A19 | Max useful heat temperature for heat pump pathway                 | 120                         | °C                      | Technology limit / industrial HP literature                   |
| A20 | Max discharge temperature for reference heat battery (Rondo)      | 1500                        | °C                      | Rondo public specification (discharge up to 1500 °C); cite URL in assumptions log |
| A21 | `eprtr_activity` → process heat temperature band                  | see table below             | band                    | Sector heat surveys, IEA/industrial decarb literature       |


#### A21 Default mapping (`eprtr_activity` → band)

Use exact match on `eprtr_activity` string as in E-PRTR. If no match, use parent prefix (e.g. `3(c)(i)` → try `3(c)(i)`, then `3(c)`, then `3`) before defaulting to **mid**.

| `eprtr_activity` prefix or code | Band   | Rationale (summary) |
| ------------------------------- | ------ | ------------------- |
| `1` (energy / combustion)       | mid    | Mixed boilers and CHP; HP rarely applicable site-wide |
| `2` (metals production)         | high   | Furnaces, smelting, hot rolling |
| `3` (mineral industry)          | high   | Cement, lime, glass, ceramics calcination |
| `4` (chemical industry)           | mid    | Steam-driven processes, typically &lt; 400 °C |
| `5` (waste / metals-related)    | high   | Incineration and high-temp treatment where dominant |
| `6` (other industrial)          | mid    | Sector-heterogeneous; refine with sub-code if known |
| `7` (paper and wood)            | mid    | Paper drying/steam often 120–200 °C |
| `8` (food and drink)            | low    | Pasteurization, cleaning, low-pressure steam |
| `9` (surface treatment)         | mid    | Baths and ovens often mid-temperature |

**Sub-code overrides** (apply before parent prefix fallback):

| `eprtr_activity` | Band | Notes |
| ---------------- | ---- | ----- |
| `8(b)`, `8(b)(i)`, `8(b)(ii)`, `8(c)` | low | Food, drink, agricultural processing |
| `3(c)`, `3(c)(i)`, `3(c)(ii)`, `3(c)(iii)` | high | Cement and mineral products |
| `5(b)`, `5(a)`, `6(b)`, `6(a)` | high | Iron, steel, non-ferrous metals |
| `2(b)`, `2(c)`, `2(e)(i)`, `2(e)(ii)` | high | Ferrous metal processes |
| `1(c)` | mid | Combustion installations (generic industrial heat) |

Document any facility-specific override in `assumptions_log.md` with source citation.

Rules:

1. Record source URL/DOI and access date for each assumption.
2. If source cannot be verified: `[ASSUMPTION_SOURCE_UNVERIFIED]`.
3. If value updated during run, log old/new values: `[ASSUMPTION_VALUE_UPDATED]`.

---

## 8. Web Retrieval Protocol (Fuel and Power Prices)

### Phase A — Build Query Set

For each facility:

1. Construct query keys using country, year, and energy type (`industrial electricity price`, `industrial natural gas price`, etc.).
2. Prioritize official dataset APIs/download pages.
3. If E-PRTR data includes country-specific context (e.g., member state registry links), include them as candidate sources for tariff structures.

### Phase B — Retrieve Candidate Values

1. Pull latest available and at least one historical comparator (e.g., previous year average).
2. Capture metadata: source name, table/series ID, value, unit, year/month coverage, URL.

### Phase C — Normalize

1. Convert all prices to `EUR/MWh` using documented conversion factors.
2. Normalize gas basis (LHV vs HHV) and flag conversion assumption used.
3. Apply FX conversion with ECB rate when source currency differs from reporting currency.

### Phase D — Select Final Value

1. Choose value by hierarchy:
  - Country-specific official value
  - Regional official proxy
  - International institutional proxy (flagged)
2. If data age exceeds A14, apply stale-data flag: `[PRICE_DATA_STALE]`.

### Phase E — Log and Attach

1. Write all candidate and selected values to `price_sources_log.csv`.
2. Include rejected-value rationale (unit mismatch, outdated, unverifiable source, etc.).

---

## 9. Verification Workflow (Assumptions + Published Documents)

The verification process must explicitly tie numbers to published sources.

### 9.1 Assumption Verification

- Validate each assumption ID in Section 7 against a retrievable publication.
- For each assumption, store:
  - `assumption_id`
  - value used
  - source title
  - source type/tier
  - publication year
  - URL/DOI
  - access date
  - verification status (`CONFIRMED`, `UPDATED`, `UNVERIFIED`)

### 9.2 Price Verification

- Confirm each selected electricity/gas price has:
  - source URL
  - exact series/table reference where possible
  - period coverage
  - unit and conversion path to EUR/MWh
- Cross-check selected value against at least one alternate source or prior-year value.
- If deviation > A15 from comparator, flag `[PRICE_OUTLIER_CHECK_REQUIRED]`.

### 9.3 Calculation Traceability

Each facility-pathway result must be reproducible via logged formula + inputs + assumptions + sources.
No output row may exist without provenance.

---

## 10. Required Outputs

### 10.1 Facility Results Table

File: `outputs/lcoh_results.csv`

Required columns:

- `eprtr_facility_id`, `eprtr_facility_name`, `eprtr_country`, `iso3_country`, `analysis_year`
- `eprtr_activity`, `eprtr_lat`, `eprtr_lon`
- `process_heat_temp_band`, `process_heat_temp_C`, `temp_provenance`
- `pathways_evaluated`, `pathways_excluded`
- `annual_heat_demand_MWh_th`
- `annual_heat_demand_provenance`
- `natural_gas_fuel_energy_MWh` (derived from `lcp_NaturalGas_TJ`; null if missing)
- `lcp_total_TJ`, `lcp_substitutable_thermal_TJ`
- `lcoh_heat_pump_EUR_MWhth` (null if not eligible; flag `[HP_NOT_APPLICABLE_TEMPERATURE]`)
- `lcoh_heat_battery_EUR_MWhth` (null if not eligible; flag `[HB_NOT_APPLICABLE_TEMPERATURE]` when temp-limited)
- `lcoh_natural_gas_EUR_MWhth`
- `least_cost_pathway` (minimum over **eligible** pathways only)
- `delta_vs_gas_hp_EUR_MWhth`
- `delta_vs_gas_hb_EUR_MWhth`
- `elec_price_used_EUR_MWh`
- `gas_price_used_EUR_MWh`
- `assumptions_used`
- `verification_status`
- `flags`

### 10.2 Assumption Log

File: `outputs/assumptions_log.md`  
Contains full assumption register with source links and verification status.

### 10.3 Price Source Log

File: `outputs/price_sources_log.csv`  
Contains all retrieved candidate and selected price values plus metadata.

### 10.4 Verification Report

File: `outputs/verification_report.md`  
Contains checklist outcomes, unresolved flags, and documentation completeness score.

### 10.5 Method Summary

File: `outputs/method_summary.md`  
Human-readable narrative of formulas, assumptions, and decision rules.

---

## 11. Verification Checklist

Complete before publishing final outputs:

### Data & Units

- Required input columns present
- All energy and currency units normalized and documented
- No null-driven calculations without explicit flag

### Source Traceability

- Every assumption used has a URL/DOI or is flagged unverified
- Every selected electricity/gas price has source metadata and retrieval date
- Secondary-only sources are flagged and not silently promoted

### Calculation Integrity

- LCOH formula applied consistently across all **eligible** pathways
- Process heat temperature band assigned and `temp_provenance` flagged for every facility
- No heat pump LCOH computed for Mid or High band (or when `process_heat_temp_C` > A19)
- Heat battery skipped when process temperature exceeds A20
- `least_cost_pathway` excludes ineligible technologies; null HP LCOH not treated as lowest cost
- CRF inputs (`r`, `n`) logged per facility
- Efficiency/COP values within plausible range and source-backed

### Verification Against Published Documents

- Each assumption entry references a published document or official dataset
- Each external price input can be traced to a specific publication/table/series
- All updates from defaults are logged with rationale

### Reporting Completeness

- `lcoh_results.csv` generated for all valid facilities
- `assumptions_log.md`, `price_sources_log.csv`, and `verification_report.md` generated
- All unresolved issues listed in `flags`

---

## 12. Agent Behavioral Constraints

- Do not hallucinate numeric values; if unavailable, flag and stop pathway-specific result if required data is missing.
- Do not compute heat pump LCOH when `process_heat_temp_band` is `mid` or `high`, or when `process_heat_temp_C` > A19.
- Do not compute heat battery LCOH only when `process_heat_temp_C` > A20 (default 1500 °C). Do not exclude heat battery by temperature band alone.
- Do not treat null ineligible-pathway LCOH as infinitely expensive in rankings; exclude from `least_cost_pathway`.
- Do not use uncited defaults in final calculations.
- Do not mix units without explicit conversion logging.
- Do not overwrite primary-source values with secondary-source values without explicit flag.
- Preserve complete provenance for every reported LCOH value.

---

*End of Research Spec v1.3.1*