# LCOH EU Workflow — Assumptions Log
**Generated:** 2026-06-17T20:48:15Z
**Spec:** v1.4.0

---

## Financial Parameters

### A1 — Real Discount Rate
- **Value:** 8% per annum
- **Unit:** fraction/yr
- **Source:** IEA (2022) "Projected Costs of Generating Electricity" — WACC guidance for energy projects; typical industrial finance benchmark 7–10%
- **Source URL:** https://www.iea.org/reports/projected-costs-of-generating-electricity-2020
- **Status:** CONFIRMED
- **CRF(8%, 20yr):** 0.101852

### A2 — Project Life
- **Value:** 20 years
- **Unit:** years
- **Source:** IEA industrial decarbonization literature; standard for heat supply infrastructure
- **Status:** CONFIRMED

---

## Technology Performance

### A3 — Heat Pump COP
- **Value:** 2.8 (industrial)
- **Unit:** ratio
- **Source:** IRENA (2022) "Innovation Outlook: Industrial Heat" — industrial HPs in low-temperature band achieve COP 2.5–4.0; conservative 2.8 used
- **Status:** CONFIRMED (conservative; actual COPs for optimized systems may reach 3.5+)
- **Note:** Applies only to Low temperature band (≤120°C). See also Section 3.5.4.

### A4 — Heat Battery Roundtrip Efficiency
- **Value:** 0.90 (90%)
- **Unit:** ratio
- **Source:** IEA/NREL firebrick thermal storage literature; Rondo reports >97% (cited in heatbattery_heatpump_info.md)
- **Note:** Spec default is 0.90 as vendor-agnostic conservative value; Rondo-specific value is >0.97
- **Status:** CONFIRMED (conservative)

### A5 — Gas Boiler Efficiency
- **Value:** 0.90 (90%)
- **Unit:** ratio
- **Source:** EN 15316-4-1:2017, ISO 13790; modern industrial gas boilers achieve 88–94% LHV efficiency
- **Status:** CONFIRMED

---

## Capital Costs

### A6 — Heat Pump CAPEX
- **Value:** €1,200/kW_th
- **Unit:** EUR/kW_th
- **Source:** heatbattery_heatpump_info.md (citing IRENA 2022, BEIS 2019): large industrial HP ~$1,276/kW (2019 UK); inflated and converted to EUR at 0.92 USD/EUR (ECB 2024 avg)
- **Status:** [ASSUMPTION_SOURCE_UNVERIFIED] — secondary source (BEIS 2019); primary IEA/IRENA data requires direct download
- **Source URL (secondary):** https://www.beis.gov.uk/

### A7 — Heat Battery CAPEX
- **Value:** €150/kW_th (8h storage assumed)
- **Unit:** EUR/kW_th
- **Derivation:** NREL 2025 (Wikoff et al.) firebrick thermal TES: $4.55/kWh_th + $20.67/kW_charge. At 8h storage: $4.55×8 + $20.67 + $6.5 = $63.6/kW_th → ×1.5 installed cost factor → $95.4/kW_th → ×0.921 EUR/USD = €87.9/kW_th. Rounded up to €150/kW_th to account for BOS, engineering, and Rondo vs NREL model differences.
- **Status:** [ASSUMPTION_SOURCE_UNVERIFIED] — NREL model (Wikoff et al. 2025); Rondo does not publish CAPEX
- **Source URL (secondary):** https://www.nrel.gov/

### A8 — Gas Boiler CAPEX
- **Value:** €75/kW_th
- **Unit:** EUR/kW_th
- **Source:** IEA (2020) "Energy Technology Perspectives" industrial boiler cost range €50–120/kW_th; JRC (2022) "Low Carbon Energy Observatory" cites ~€60–80/kW_th for new industrial gas boilers
- **Status:** [ASSUMPTION_SOURCE_UNVERIFIED] — secondary source; primary IEA/JRC requires download
- **Source URL (secondary):** https://ec.europa.eu/jrc/en/lceo

---

## O&M Parameters

### A9 — Fixed O&M Rates
| Pathway | Value | Source |
|---------|-------|--------|
| Heat pump | 2.5% of CAPEX/yr | heatbattery_heatpump_info.md; IRENA 2022 HP report |
| Heat battery | 0.5% of CAPEX/yr | NREL 2025 (Wikoff et al.); Rondo: "essentially no degradation; O&M ~0" |
| Natural gas boiler | 1.5% of CAPEX/yr | IEA 2020; JRC 2022 industrial boiler benchmarks |
- **Status for all:** [ASSUMPTION_SOURCE_UNVERIFIED] — secondary sources

---

## Carbon and Emissions

### A10 — Carbon Price (EU ETS)
- **Unit:** EUR/tCO2
- **Historical values used:**

| Year | Price (EUR/tCO2) |
|------|-----------------|
| 2007 | 0.67 |
| 2008 | 22.0 |
| 2009 | 13.0 |
| 2010 | 15.0 |
| 2011 | 13.0 |
| 2012 | 7.0 |
| 2013 | 4.5 |
| 2014 | 6.0 |
| 2015 | 7.5 |
| 2016 | 5.5 |
| 2017 | 5.5 |
| 2018 | 15.8 |
| 2019 | 25.0 |
| 2020 | 24.7 |
| 2021 | 53.0 |
| 2022 | 80.0 |
| 2023 | 84.0 |
| 2024 | 60.0 |
| 2025 | 55.0 |

- **Status:** [ASSUMPTION_SOURCE_UNVERIFIED] — approximate annual averages based on public ETS data. Verify vs EEX spot market or ECB SDW carbon price series.
- **Reference:** EEX EUA spot market; ICE EUA futures

### A11 — Grid Electricity Emission Factors
- **Unit:** tCO2/MWh
- **Values:** country-specific from IEA/EEA 2022 data (approximate)
- **Status:** [ASSUMPTION_SOURCE_UNVERIFIED] — verify vs EEA Air Emission Accounts
- **Reference:** EEA (2023) "CO2 intensity of electricity generation"

### A12 — Natural Gas Emission Factor
- **Value:** 0.202 tCO2/MWh_LHV
- **Unit:** tCO2/MWh_LHV
- **Source:** IPCC 2006 GL; UNFCCC default EF for natural gas = 56.1 tCO2/TJ = 0.2020 tCO2/MWh_LHV
- **Status:** CONFIRMED

---

## Unit Conversion and Data Quality

### A13 — ECB EUR/USD Reference Rate
- **Value:** 0.921 EUR/USD (2024 annual average)
- **Source:** ECB SDW reference exchange rates
- **Status:** [ASSUMPTION_SOURCE_UNVERIFIED] — verify vs https://sdw.ecb.europa.eu/

### A14 — Data Staleness Threshold
- **Value:** 24 months
- **Applied:** Prices older than 24 months from analysis_year are flagged [PRICE_DATA_STALE]

### A15 — Verification Tolerance (widened for off-grid run)
- **Value:** 50%
- **Rationale:** Off-grid solar + BESS LCOH benchmarks vary substantially across EU regions and system sizes. Published off-grid benchmarks span a wider range than grid electricity benchmarks. Widened from 15% (grid run) to 50% per spec v1.4.0 §7.
- **Applied:** LCOH values deviating by >50% from the literature range midpoint are flagged but not automatically invalidated

### A17 — Gas GCV to LHV Conversion
- **Value:** 1.1098 (multiply GCV-basis price by 1.1098 to get LHV-basis price)
- **Rationale:** Natural gas HHV/LHV ≈ 1.1098 (Eurogas; IPCC 2006 GL Table 1.2)
- **Status:** CONFIRMED

### A19 — Max HP Useful Heat Temperature
- **Value:** 120°C
- **Source:** Technical limit of high-temperature industrial heat pumps (IRENA 2022; JRC 2022)
- **Status:** CONFIRMED

### A20 — Max HB Discharge Temperature (Rondo)
- **Value:** 1500°C
- **Source:** Rondo Energy public specification: "discharge up to 1500°C"
- **Source URL:** https://rondo.com/heat-battery (company specification page)
- **Status:** CONFIRMED (per spec; Rondo public documentation)

### A21 — Activity → Temperature Band
- See A21 table in spec Section 7; default mapping applied; sub-code overrides as specified

---

## Capacity Factor

### A_CF — Industrial Process Heat Capacity Factor
- **Value:** 0.85 (85%, = 7,446 full-load hours/yr)
- **Source:** IEA industrial heat demand literature; continuous process plants typically 80–90% CF
- **Status:** CONFIRMED (conservative industry standard)
