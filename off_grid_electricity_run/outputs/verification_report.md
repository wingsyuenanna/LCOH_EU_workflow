# LCOH EU Workflow — Verification Report (off-grid electricity)
**Generated:** 2026-06-08T18:20:26Z
**Spec:** v1.4.0

## Data & Units ✓
- [x] Required input columns present in eprtr_lcp_matched.csv
- [x] BNEF CSV contains PV fixed-axis, Utility-scale battery (4h), Industrial heat battery rows
- [x] Heat pump electricity cost = (PV + BESS) LCOE (USD/MWh) × A13 (0.921) → EUR/MWh
- [x] Heat battery electricity cost = PV-only LCOE (USD/MWh) × A13 — BESS excluded (heat battery provides storage)
- [x] Heat battery CAPEX: BNEF row used where available; A7 default (€150.0/kW_th) as fallback
- [x] TJ → MWh conversion: 1 TJ = 277.7777778 MWh (exact)
- [x] Gas prices converted from EUR/kWh GCV → EUR/kWh LHV (×1.1098)
- [x] No null-driven calculations without explicit flag
- [x] bnef_data_source recorded for every facility with an electricity price
- [x] BNEF year mapping: analysis_year ≤2027 → 2025; ≥2028 → 2030

## Source Traceability
- [x] Electricity prices: BNEF `inputs/bnef_country_costs.csv` — no web retrieval
- [x] bnef_data_source column populated in results (direct vs proxied)
- [x] Gas prices: Eurostat nrg_pc_203 (cached, retrieved 2026-05-28)
- [!] EU ETS prices: approximate annual averages — [ASSUMPTION_SOURCE_UNVERIFIED] — verify vs EEX/ICE
- [!] Grid electricity emission factors: [ASSUMPTION_SOURCE_UNVERIFIED] — verify vs EEA
- [!] Technology CAPEX/OPEX: see assumptions_log.md (some sources unverified)

## Calculation Integrity
- [x] LCOH formula applied consistently across all eligible pathways
- [x] Temperature band assigned for every facility with heat demand; temp_provenance set
- [x] HP LCOH null for all Mid/High band facilities — [HP_NOT_APPLICABLE_TEMPERATURE]
- [x] HB skipped only when process_heat_temp_C > 1500.0°C (A20); not by band alone
- [x] least_cost_pathway computed over eligible pathways only; null LCOH excluded
- [x] lcoh_confidence populated for every row
- [x] CRF inputs logged: r=0.08, n=20, CRF=0.101852

## Price Coverage
| Issue | Count |
|-------|-------|
| Facilities with gas LCOH but no gas price | 0 |
| Facilities with elec-eligible but no elec price | 0 |
| BNEF year proxied (analysis_year ≠ BNEF year) | 1759 |
| Fallback to EU27 gas average | 7 |
| Direct BNEF country data | 933 |
| Proxied BNEF country data | 826 |

## Literature Plausibility Check (Spec §9.2.1, A15=50%)

Benchmark ranges (EU industrial, off-grid PV+BESS scenario):

| Pathway | Expected range | Flag threshold | Out-of-range |
|---------|---------------|---------------|-------------|
| Natural gas LCOH | 30–100 EUR/MWh_th | <20 or >130 | 103 facilities |
| Heat pump LCOH | 60–180 EUR/MWh_th | <40 or >250 | 0 facilities |
| Heat battery LCOH | 60–200 EUR/MWh_th | <40 or >280 | 268 facilities |
| Off-grid elec LCOE | 40–130 USD/MWh | <35 or >160 USD/MWh | 136 facilities |

Source basis: BNEF NEO 2025 country-level PV/BESS costs; IEA/JRC industrial heat LCOH ranges; EU ETS carbon pricing.
Tolerance A15=50% applied (widened vs grid run 15%) due to variability in off-grid solar benchmarks across EU regions.

## Confidence Distribution (Spec §6.8)
| Rating | Count | Criteria |
|--------|-------|---------|
| HIGH | 0 | Direct BNEF + TEMP_FROM_DATA + substitutable thermal + no fallback/proxied flags |
| MEDIUM | 1203 | Proxied BNEF or activity-inferred temp, but no extreme data quality issues |
| LOW | 556 | Price fallback, default temp band, total-fuel heat demand, or out-of-range LCOH |
| NOT_COMPUTED | 1832 | No heat demand or all pathways excluded |

## Cross-run Comparison (Spec §9.2.3)
Grid run outputs: found.
Cross-run flagged facilities: 860 (see cross_run_comparison.csv).
Most common flag: OFFGRID_CHEAPER_THAN_GRID_UNEXPECTED — expected because off-grid PV-only electricity for heat battery (avg ~44 EUR/MWh) is well below grid industrial electricity tariffs in Northern/Central EU.

## Outstanding Flags / Unresolved Issues
- EU ETS prices (A10): approximate annual averages. Verify vs EEX spot market or ECB SDW. [ASSUMPTION_SOURCE_UNVERIFIED]
- Grid electricity emission factors (A11): IEA/EEA 2022 approximate values. [ASSUMPTION_SOURCE_UNVERIFIED]
- Heat pump CAPEX (A6): €1,200/kW_th (IRENA/BEIS 2019 adjusted). [ASSUMPTION_SOURCE_UNVERIFIED]
- Heat battery CAPEX (A7): €150/kW_th (NREL Wikoff et al. 2025 firebrick model). Rondo does not publish CAPEX. [ASSUMPTION_SOURCE_UNVERIFIED]
- Switzerland gas prices: not in Eurostat nrg_pc_203 → EU27 average fallback
- Iceland, Norway, Cyprus, Malta gas prices: not in nrg_pc_203 → EU27 average fallback
- PRICE_YEAR_PROXIED_TO_2025: most facilities have analysis_year 2017–2022, all mapped to BNEF 2025. This precludes HIGH confidence rating for any facility.

## Completeness
- [x] lcoh_results.csv — 3591 rows with lcoh_confidence column
- [x] price_sources_log.csv
- [x] assumptions_log.md
- [x] method_summary.md
- [x] verification_report.md
