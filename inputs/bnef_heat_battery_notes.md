# Heat battery rows in `bnef_country_costs.csv`

Technology name: **`Industrial heat battery (thermal)`**

## Estimation method (2026-05-29)

Aligned with LCOH workflow assumption **A7** (NREL Wikoff et al. 2025 firebrick TES, 8 h storage, €150/kW_th installed).

| Field | 2025 | 2030 |
|-------|------|------|
| CAPEX (EUR/kW_th) | 150 | 135 (−10% learning) |
| CAPEX in file (`capex_$/kw`) | USD via ÷0.921 | same |
| Fixed O&M | 0.5% of CAPEX/yr (A9_hb) | same |
| CRF | Country-specific from BNEF PV row | same |
| `lcoe_$/mwh` | Levelized CAPEX + FOM only at **85% CF** | same |

**Not included in `lcoe_$/mwh`:** electricity for charging — in the off-grid run that is **BNEF PV + 4h BESS** LCOE, added in `off_grid_electricity_run/lcoh_calculation.py`.

## Regenerate rows

```bash
python3 scripts/add_heat_battery_to_bnef.py
```

(Safe to re-run only if HB rows are absent; script skips if already present.)
