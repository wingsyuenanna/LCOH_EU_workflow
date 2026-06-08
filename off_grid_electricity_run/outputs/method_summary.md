# LCOH EU Workflow — Method Summary (off-grid electricity)
**Generated:** 2026-06-08T18:20:26Z
**Spec:** v1.4.0 | **Variant:** off_grid_electricity_run
**Facilities in input:** 3591
**Facilities with heat demand:** 1759
**Facilities with gas LCOH computed:** 1759

## Formula

```
LCOH_p = (Annualized_CAPEX_p + Fixed_OM_p + Energy_Cost_p + Carbon_Cost_p)
         / Annual_Useful_Heat_MWh_th
```

Annualized CAPEX = CAPEX [EUR/kW_th] × CRF(r=8%, n=20yr)
                  / (CF × 8760 / 1000)   [EUR/MWh_th]

CRF(8%, 20yr) = 0.101852

## Technology Assumptions

| Parameter | Heat Pump | Heat Battery | Natural Gas |
|-----------|-----------|--------------|-------------|
| CAPEX (EUR/kW_th) | 1200.0 | 150.0 | 75.0 |
| Efficiency/COP | COP=2.8 | η=0.9 | η=0.9 |
| O&M (% CAPEX/yr) | 2.5% | 0.5% | 1.5% |
| Temp eligibility | Low band only (≤120.0°C) | All bands (≤1500.0°C) | All bands |

## Price Sources

- **Electricity — heat battery** (PV only): BNEF `inputs/bnef_country_costs.csv` — LCOE(PV fixed-axis) only. Heat battery is the storage device; BESS cost not added to avoid double-counting. USD/MWh × A13 (0.921) → EUR/MWh.
- **Electricity — heat pump** (PV + BESS): BNEF — LCOE(PV fixed-axis) + LCOE(Utility-scale battery 4h). Heat pump has no intrinsic storage so BESS is required. USD/MWh × A13 → EUR/MWh. BNEF years [2025, 2030]; analysis_year ≤2027 → BNEF 2025; ≥2028 → BNEF 2030. Flag [PRICE_YEAR_PROXIED_TO_{year}] when not an exact match.
- **Heat battery CAPEX**: BNEF row `Industrial heat battery (thermal)` when present (else A7 default €150.0/kW_th)
- **Gas**: Eurostat nrg_pc_203, Band I3 (10,000–99,999 GJ/yr), X_VAT, EUR/kWh (GCV→LHV ×1.1098)
- Carbon: EU ETS annual average prices by year (A10) on natural gas only

## Temperature Band Assignment

Priority: (1) reported process_heat_temp_C → (2) eprtr_activity A21 mapping → (3) default mid

| Band | Count | HP eligible | HB eligible |
|------|-------|-------------|-------------|
| low  | 47 | Yes | Yes |
| mid  | 1369 | No  | Yes |
| high | 343 | No  | Yes (if ≤1500°C) |

## Summary Results

| Metric | Value |
|--------|-------|
| Facilities with gas LCOH | 1759 |
| Facilities with HP LCOH | 47 |
| Facilities with HB LCOH | 1759 |
| Avg LCOH natural gas (EUR/MWh_th) | 80.7 |
| Avg LCOH heat pump (EUR/MWh_th) | 70.8 |
| Avg LCOH heat battery (EUR/MWh_th) | 45.2 |
| Least-cost: natural gas | 311 facilities |
| Least-cost: heat pump   | 0 facilities |
| Least-cost: heat battery | 1448 facilities |

## BNEF Data Coverage

| Type | Count |
|------|-------|
| Direct BNEF country data (`bnef_data_source = bnef`) | 933 facilities |
| Proxied BNEF data (`bnef_data_source = proxied`) | 826 facilities |

## Confidence Distribution (Spec §6.8)

| Rating | Count |
|--------|-------|
| HIGH | 0 |
| MEDIUM | 1203 |
| LOW | 556 |
| NOT_COMPUTED | 1832 |

## Literature Plausibility Check (Spec §9.2.1, tolerance A15=50%)

| Check | Range | Out-of-range count |
|-------|-------|--------------------|
| Natural gas LCOH | 30–100 EUR/MWh_th (flag <20 or >130) | 103 |
| Heat pump LCOH | 60–180 EUR/MWh_th (flag <40 or >250) | 0 |
| Heat battery LCOH | 60–200 EUR/MWh_th (flag <40 or >280) | 268 |
| Off-grid electricity LCOE | 40–130 USD/MWh (flag <35 or >160 USD/MWh) | 136 |

## Key Assumptions

- Capacity factor: 85% (industrial process heat)
- Verification tolerance (A15): 50% — widened from grid run (15%) because off-grid benchmarks vary substantially
- Carbon pricing: Enabled, EU ETS prices by year
- Gas LHV/HHV conversion: ×1.1098 applied to Eurostat GCV prices
