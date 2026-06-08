# LCOH EU Workflow — Verification Report
**Generated:** 2026-06-08T18:20:25Z

## Data & Units ✓
- [x] Required input columns present in eprtr_lcp_matched.csv
- [x] TJ → MWh conversion: 1 TJ = 277.7777778 MWh (exact)
- [x] Gas prices converted from EUR/kWh GCV → EUR/kWh LHV (×1.1098)
- [x] EUR/kWh → EUR/MWh conversion documented (×1000)
- [x] No null-driven calculations without explicit flag

## Source Traceability
- [x] Electricity prices: Eurostat nrg_pc_205 (API, retrieved 2026-05-28)
- [x] Gas prices: Eurostat nrg_pc_203 (API, retrieved 2026-05-28)
- [!] Technology CAPEX/OPEX: see assumptions_log.md (some sources unverified)
- [!] EU ETS prices: approximate annual averages — [ASSUMPTION_SOURCE_UNVERIFIED] — verify vs EEX/ICE
- [!] Grid electricity emission factors: [ASSUMPTION_SOURCE_UNVERIFIED] — verify vs EEA

## Calculation Integrity
- [x] LCOH formula applied consistently across all eligible pathways
- [x] Temperature band assigned for every facility with heat demand
- [x] HP LCOH null for all Mid/High band facilities
- [x] HB skipped only when process_heat_temp_C > 1500.0°C (A20)
- [x] least_cost_pathway excludes ineligible technologies
- [x] CRF inputs logged: r=0.08, n=20, CRF=0.101852

## Price Coverage Issues
| Issue | Count |
|-------|-------|
| Facilities with gas LCOH but no gas price | 0 |
| Facilities with elec-eligible but no elec price | 0 |
| Price year proxied (nearest year used) | 7 |
| Fallback to EU27 average | 7 |

## Outstanding Flags / Unresolved Issues
- EU ETS prices (A10) based on approximate annual averages. Source: training-data knowledge. Must be verified against EEX spot market or ECB SDW. Flag: [ASSUMPTION_SOURCE_UNVERIFIED]
- Grid electricity emission factors (A11) from IEA/EEA 2022 approximate values. Flag: [ASSUMPTION_SOURCE_UNVERIFIED]
- Heat pump CAPEX (A6): €1,200/kW_th based on IRENA/BEIS 2019 data adjusted for inflation. Secondary source. Flag: [ASSUMPTION_SOURCE_UNVERIFIED]
- Heat battery CAPEX (A7): €150/kW_th based on NREL 2025 (Wikoff et al.) firebrick model. Rondo does not publish CAPEX. Flag: [ASSUMPTION_SOURCE_UNVERIFIED]
- Switzerland (CH) electricity and gas prices: not in Eurostat dataset → EU27 average used as fallback
- Iceland (IS), Norway (NO), Cyprus (CY), Malta (MT) gas prices: not in nrg_pc_203 → EU27 average fallback

## Completeness Score
- lcoh_results.csv: ✓ Generated
- price_sources_log.csv: ✓ Generated
- assumptions_log.md: see below
- method_summary.md: ✓ Generated
