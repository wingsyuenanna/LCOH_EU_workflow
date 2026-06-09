#!/bin/bash
python run_scenario.py \
    --site-id FR.CAED/3814.FACILITY \
    --iso3-country FRA \
    --scenario-dir scenarios/eu_heat_tes_v1/scenario_FR.CAED_3814.FACILITY \
    --availability 0.9 \
    --solar-start 2023 \
    --solar-end 2023 \
    --project-start 2025 \
    --peak-demand-cutoff 0 \
    --sites-csv inputs/sites.csv \
    --input-bnef-costs ../inputs/bnef_country_costs.csv \
    --input-battery-costs ../inputs/battery_tes_costs.csv \
    --storage-type heat \
    --heat-rte 0.97 \
    --heat-max-hours 12.0 \
    --heat-tmax-c 1500.0 \
    --heat-cd-ratio 4.0 \
    --heat-process-temperature-c 350.0 \
    --base-load-mw 444.5062 \
    --results-folder scenarios/eu_heat_tes_v1/scenario_FR.CAED_3814.FACILITY/results

date
