# LCOH EU Workflow — Method Summary
**Generated:** 2026-05-28T21:59:33Z
**Spec:** v1.3.1
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

- **Electricity**: Eurostat nrg_pc_205, Band IC (500–1999 MWh/yr), X_VAT, EUR/kWh
- **Gas**: Eurostat nrg_pc_203, Band I3 (10,000–99,999 GJ/yr), X_VAT, EUR/kWh (GCV→LHV ×1.1098)
- Carbon: EU ETS annual average prices by year (A10)

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
| Avg LCOH heat pump (EUR/MWh_th) | 79.3 |
| Avg LCOH heat battery (EUR/MWh_th) | 180.3 |
| Least-cost: natural gas | 1597 facilities |
| Least-cost: heat pump   | 19 facilities |
| Least-cost: heat battery | 143 facilities |

## Key Assumptions

- Capacity factor: 85% (industrial process heat)
- Carbon pricing: Enabled, EU ETS prices by year
- Gas LHV/HHV conversion: ×1.1098 applied to Eurostat GCV prices
