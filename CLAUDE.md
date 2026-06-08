# LCOH EU Workflow — Claude Code Instructions

## Output directory conventions — DO NOT CHANGE

Each run variant writes outputs to its own `outputs/` subdirectory. Never redirect a run's
`OUT_DIR` to the project root or a sibling directory.

| Script | OUT_DIR | Must resolve to |
|--------|---------|-----------------|
| `grid_electricity_run/lcoh_calculation.py` | `os.path.join(BASE_DIR, "outputs")` | `grid_electricity_run/outputs/` |
| `off_grid_electricity_run/lcoh_calculation.py` | `os.path.join(BASE_DIR, "outputs")` | `off_grid_electricity_run/outputs/` |

**Never change `OUT_DIR` to `os.path.join(BASE_DIR, "..", "outputs")`.** That path points to
the project-root `outputs/` folder, which is reserved for cross-run artifacts
(`cross_run_comparison.csv`, `cached_data/`).

The linter previously made this mistake — it was intentionally reverted.

## Project-root outputs/

Only cross-run outputs belong here:

- `outputs/cross_run_comparison.csv` — grid vs off-grid comparison
- `outputs/cached_data/` — shared Eurostat price cache

Run-specific files (`lcoh_results.csv`, `assumptions_log.md`, `method_summary.md`,
`price_sources_log.csv`, `progress.log`, `verification_report.md`) must stay inside
the run's own `outputs/` subdirectory.

## Spec and column names

The off-grid run (spec v1.4.0) uses **two** electricity price columns, not one:

- `elec_price_hb_EUR_MWh` — PV LCOE only (heat battery provides its own storage)
- `elec_price_hp_EUR_MWh` — PV + 4h BESS LCOE (heat pump needs BESS)

Do not rename these back to `elec_price_used_EUR_MWh` or merge them into one column.
