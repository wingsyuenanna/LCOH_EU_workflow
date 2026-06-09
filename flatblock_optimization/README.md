# Flatblock optimization

Run solar + storage (+ optional gas) site optimizations with country-matched BNEF costs, batch jobs, maps, and S3 uploads.

## Layout

| Path | Purpose |
|------|---------|
| `run_scenario.py` | CLI entry: load site, costs, solar; call HiGHS solver |
| `solvers/` | HiGHS optimization implementations (`optimize_flatblock_highs*.py`) |
| `generate_scenarios.py` | Generate `scenarios/<name>/scenario_<id>/jobscript.sh` per site |
| `run_all_jobs.sh` | Parallel run of jobscripts + optional S3 upload |
| `inputs/` | `sites.csv`, `compile_sites.py`, sample generators |
| `scenarios/` | Per-scenario, per-site folders with `jobscript.sh`, `results/`, `logs/`; `aggregate_scenario.py` builds a combined CSV |
| `outputs/` | Combined CSVs (e.g. `combined_results_all_sites_v1.csv`) |
| `utils/loaders/` | `load_solar`, `load_site`, `load_re_costs`, `load_demand` |
| `utils/maps/` | Sector / comparison Folium maps + `map_common` |
| `utils/analysis/` | `neighbor_lcoe_contrast`, `combine_scenario_summaries`, `compare_two_site_results` |
| `utils/s3/` | `upload_scenario_to_s3`, one-off upload helpers |
| `notebooks/` | Analysis notebooks |
| `docs/` | Design notes (e.g. `optimization_diagram.md`) |
| `logs/` | Ad hoc run logs (gitignored patterns recommended) |

Run from **`flatblock_optimization/`** so paths like `inputs/`, `utils/`, and `scenarios/` resolve as documented in jobscripts.

**Storage technology:** By default `run_scenario.py` uses the Li-ion model (`solvers/optimize_flatblock_highs_unserved_v2.py`). For **thermal storage (TES)**, pass `--storage-type heat` and set **`--heat-energy-capex-per-mwh`** (overnight $/MWh of storage energy capacity; annualized with BNEF CRF unless `--heat-capex-includes-crf`). Power CAPEX and FOM are optional. See `solvers/optimize_flatblock_highs_heat_battery.py`.

**US sites + `heat_battery_v1` scenario:** (1) Build a US sites list: `python inputs/compile_us_sites.py -o inputs/sites_usa.csv` (add `--manufacturing-only` or `--sector manufacturing` to restrict sector; optional `--top-n 500` caps count; sorts by `co2e_20yr` when limiting). (2) Generate jobscripts with TES CAPEX escalated from **$12.86/MWh @ 2022** to **2025 USD** via CPI-U:  
`python generate_scenarios.py --scenario-name heat_battery_v1 --storage-type heat --scenarios-csv inputs/sites_usa.csv --sites-csv inputs/sites_usa.csv`  
(3) Set `SCENARIO_NUM=heat_battery_v1` in `run_all_jobs.sh` and run from `flatblock_optimization/`. Override escalation with `--heat-energy-capex-per-mwh` if needed.  
(4) **Combined results** (like `combined_results_all_sites_v1.csv`):  
`python scenarios/aggregate_scenario.py --scenario heat_battery_v1 -o outputs/combined_results_heat_battery_v1.csv`  
Optional: `--sites-csv inputs/sites_usa_mfg.csv` to keep only sites from that list. Uses `map_common.aggregate_scenarios` (supports `scenario_<id>/` folders) and merges `views/facility_master_v6.csv`.

## Quick links

- Batch runs: `bash run_all_jobs.sh` (set `SCENARIO_NUM` inside the script).
- Regenerate combined results: `python utils/analysis/combine_scenario_summaries.py --help`
- Maps: `python utils/maps/map_results_by_sector.py`, `utils/maps/map_ccgt_vs_solar_storage.py`, `utils/maps/map_coal_vs_solar_storage.py`
