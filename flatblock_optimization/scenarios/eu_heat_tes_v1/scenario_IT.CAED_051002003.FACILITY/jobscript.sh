#!/bin/bash
python run_scenario.py \
    --site-id IT.CAED/051002003.FACILITY \
    --iso3-country ITA \
    --scenario-dir scenarios/eu_heat_tes_v1/scenario_IT.CAED_051002003.FACILITY \
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
    --heat-process-temperature-c 750.0 \
    --base-load-mw 0.4668 \
    --results-folder scenarios/eu_heat_tes_v1/scenario_IT.CAED_051002003.FACILITY/results

date
