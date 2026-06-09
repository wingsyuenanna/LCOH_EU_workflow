#!/usr/bin/env python3
"""
Find sites in ``combined_results_*`` CSVs whose total LCOE is unusual **relative to nearby sites**
(same optimization setup; geography + optional country/sector filters).

Neighbors are all other sites within ``--radius-km`` (great-circle). With ``--same-country-only``,
only same ``iso3_country`` are counted.

**Metrics (neighbor set excludes the focal site):**

- ``neighbor_mean_lcoe``, ``neighbor_median_lcoe``, ``neighbor_std_lcoe``
- ``ratio_to_neighbor_median``, ``ratio_to_neighbor_mean`` — focal LCOE / neighbor central tendency
- ``lcoe_z_vs_neighbors`` — (LCOE_i − mean(neighbors)) / std(neighbors); needs ≥2 neighbors and std > 0
- ``abs_z_vs_neighbors`` — |z|; use ``--sort-by abs_z`` to list strongest local outliers
- Legacy: ``ratio_to_cheapest_neighbor`` vs single cheapest peer in radius

Example (threshold: all sites with |z| ≥ 4 vs neighbors; optional cap):

  python flatblock_optimization/utils/analysis/neighbor_lcoe_contrast.py \
    --combined-csv flatblock_optimization/outputs/combined_results_all_sites_v1.csv \
    --radius-km 80 --same-country-only --min-neighbors 4 \
    --sort-by abs_z --min-abs-z 4

Example (absolute gap vs neighbor median, $/MWh; combine with --min-abs-z if desired):

  python ... --sort-by abs_z --min-lcoe-gap 30

Example (legacy: top 50 by |z|, no threshold):

  python ... --sort-by abs_z --top 50
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

EARTH_R_KM = 6371.0
LCOL = "LCOE_total_$perMWh"


def _haversine_rad(latlon_deg: np.ndarray) -> np.ndarray:
    return np.radians(latlon_deg.astype(float))


def enrich_combined_df_with_neighbor_lcoe(
    df: pd.DataFrame,
    *,
    radius_km: float = 50.0,
    same_country_only: bool = False,
    sector: Optional[str] = None,
    lcol: str = LCOL,
) -> pd.DataFrame:
    """
    Add neighbor-set LCOE metrics to each row of a combined flatblock results table.

    Raises ``ValueError`` if required columns are missing or too few rows remain after filters.
    """
    need = [lcol, "lat", "lon", "source_id", "iso3_country"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"Missing columns: {miss}")

    df = df.dropna(subset=[lcol, "lat", "lon"]).copy()
    if sector is not None:
        if "sector" not in df.columns:
            raise ValueError("CSV has no 'sector' column")
        df = df[df["sector"].astype(str) == sector]

    df = df.reset_index(drop=True)
    n = len(df)
    if n < 2:
        raise ValueError("Not enough rows after filters")

    coords = _haversine_rad(df[["lat", "lon"]].values)
    tree = BallTree(coords, metric="haversine")
    r_rad = radius_km / EARTH_R_KM

    neigh_idx, neigh_dist = tree.query_radius(coords, r=r_rad, return_distance=True)

    source_ids = df["source_id"].to_numpy()
    lcoe = df[lcol].to_numpy(dtype=float)
    iso = df["iso3_country"].to_numpy()

    ratio_to_min = np.full(n, np.nan)
    cheapest_neighbor = np.full(n, -1, dtype=np.int64)
    dist_to_cheapest_km = np.full(n, np.nan)

    neighbor_mean = np.full(n, np.nan)
    neighbor_median = np.full(n, np.nan)
    neighbor_std = np.full(n, np.nan)
    ratio_to_median = np.full(n, np.nan)
    ratio_to_mean = np.full(n, np.nan)
    lcoe_z = np.full(n, np.nan)
    n_nb = np.zeros(n, dtype=np.int32)

    for i in range(n):
        neighbors = neigh_idx[i]
        dists = neigh_dist[i]
        mask = neighbors != i
        neighbors = neighbors[mask]
        dists = dists[mask]
        if len(neighbors) == 0:
            continue

        if same_country_only:
            keep = iso[neighbors] == iso[i]
            neighbors = neighbors[keep]
            dists = dists[keep]

        if len(neighbors) == 0:
            continue

        n_nb[i] = len(neighbors)
        n_lcoe = lcoe[neighbors]

        mean_n = float(np.mean(n_lcoe))
        med_n = float(np.median(n_lcoe))
        std_n = float(np.std(n_lcoe, ddof=1)) if len(n_lcoe) > 1 else 0.0

        neighbor_mean[i] = mean_n
        neighbor_median[i] = med_n
        neighbor_std[i] = std_n if len(n_lcoe) > 1 else np.nan

        if med_n > 0:
            ratio_to_median[i] = lcoe[i] / med_n
        if mean_n > 0:
            ratio_to_mean[i] = lcoe[i] / mean_n

        std_eff = max(std_n, 0.02 * max(mean_n, 1e-9), 1e-9)
        if len(n_lcoe) > 1:
            lcoe_z[i] = (lcoe[i] - mean_n) / std_eff
        elif len(n_lcoe) == 1:
            lcoe_z[i] = 0.0 if abs(lcoe[i] - mean_n) < 1e-9 else (lcoe[i] - mean_n) / std_eff

        j_rel = int(np.argmin(n_lcoe))
        j = int(neighbors[j_rel])
        min_n = float(n_lcoe[j_rel])
        if min_n > 0:
            ratio_to_min[i] = lcoe[i] / min_n
            cheapest_neighbor[i] = j
            dist_to_cheapest_km[i] = float(dists[np.where(neighbors == j)[0][0]]) * EARTH_R_KM

    abs_z = np.abs(lcoe_z)
    with np.errstate(invalid="ignore"):
        outlier_score = np.where(np.isfinite(abs_z), abs_z, np.log1p(np.nan_to_num(ratio_to_median, nan=1.0)))

    out = df.copy()
    out["n_neighbors_in_radius"] = n_nb
    out["neighbor_mean_lcoe"] = neighbor_mean
    out["neighbor_median_lcoe"] = neighbor_median
    out["neighbor_std_lcoe"] = neighbor_std
    out["ratio_to_neighbor_median"] = ratio_to_median
    out["ratio_to_neighbor_mean"] = ratio_to_mean
    out["lcoe_z_vs_neighbors"] = lcoe_z
    out["abs_z_vs_neighbors"] = abs_z
    out["outlier_rank_score"] = outlier_score

    out["ratio_to_cheapest_neighbor"] = ratio_to_min
    out["cheapest_neighbor_source_id"] = np.where(
        cheapest_neighbor >= 0, source_ids[cheapest_neighbor], np.nan
    )
    out["distance_km_to_cheapest_neighbor"] = dist_to_cheapest_km
    return out


def top_neighbor_lcoe_outliers(
    enriched: pd.DataFrame,
    *,
    min_neighbors: int = 4,
    sort_by: str = "abs_z",
    top_n: Optional[int] = 40,
) -> pd.DataFrame:
    """
    Subset ``enriched`` to the strongest local outliers (same ranking as CLI ``--sort-by``).

    Default: finite z-score vs neighbors, at least ``min_neighbors`` peers, sort by |z| descending,
    then keep the first ``top_n`` rows (``top_n=None`` keeps all after sort).
    """
    if sort_by == "abs_z":
        req = {"n_neighbors_in_radius", "lcoe_z_vs_neighbors", "abs_z_vs_neighbors"}
    elif sort_by == "ratio_median":
        req = {"n_neighbors_in_radius", "ratio_to_neighbor_median"}
    elif sort_by == "ratio_mean":
        req = {"n_neighbors_in_radius", "ratio_to_neighbor_mean"}
    elif sort_by == "ratio_cheapest":
        req = {"n_neighbors_in_radius", "ratio_to_cheapest_neighbor"}
    else:
        raise ValueError(f"Unknown sort_by: {sort_by!r}")
    miss = req - set(enriched.columns)
    if miss:
        raise ValueError(f"enriched DataFrame missing columns: {sorted(miss)}")

    out_f = enriched.loc[enriched["n_neighbors_in_radius"] >= min_neighbors].copy()
    if sort_by == "abs_z":
        flagged = out_f[np.isfinite(out_f["lcoe_z_vs_neighbors"])].copy()
        flagged = flagged.sort_values("abs_z_vs_neighbors", ascending=False)
    elif sort_by == "ratio_median":
        flagged = out_f.dropna(subset=["ratio_to_neighbor_median"]).copy()
        flagged = flagged.sort_values("ratio_to_neighbor_median", ascending=False)
    elif sort_by == "ratio_mean":
        flagged = out_f.dropna(subset=["ratio_to_neighbor_mean"]).copy()
        flagged = flagged.sort_values("ratio_to_neighbor_mean", ascending=False)
    else:
        flagged = out_f[np.isfinite(out_f["ratio_to_cheapest_neighbor"])].copy()
        flagged = flagged.sort_values("ratio_to_cheapest_neighbor", ascending=False)
    if top_n is not None:
        flagged = flagged.head(top_n)
    return flagged


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--combined-csv",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "outputs" / "combined_results_all_sites_v1.csv",
        help="Path to combined flatblock results CSV",
    )
    p.add_argument("--radius-km", type=float, default=50.0, help="Great-circle search radius (km)")
    p.add_argument(
        "--same-country-only",
        action="store_true",
        help="Only count neighbors with the same iso3_country",
    )
    p.add_argument(
        "--sector",
        type=str,
        default=None,
        help="If set, only compare sites with this sector (e.g. power)",
    )
    p.add_argument(
        "--min-neighbors",
        type=int,
        default=1,
        help="Minimum neighbor count to keep a row in CSV (z-score needs ≥2 neighbors with spread)",
    )
    p.add_argument(
        "--min-ratio",
        type=float,
        default=1.25,
        help="When --sort-by ratio_cheapest: require ratio_to_cheapest_neighbor >= this",
    )
    p.add_argument(
        "--sort-by",
        choices=("abs_z", "ratio_median", "ratio_cheapest", "ratio_mean"),
        default="abs_z",
        help="Metric used to sort console output after optional threshold filters",
    )
    p.add_argument(
        "--min-abs-z",
        type=float,
        default=None,
        help="When --sort-by abs_z: only print rows with abs_z_vs_neighbors >= this (threshold mode)",
    )
    p.add_argument(
        "--min-ratio-median",
        type=float,
        default=None,
        help="When --sort-by ratio_median: only print rows with ratio_to_neighbor_median >= this",
    )
    p.add_argument(
        "--min-ratio-mean",
        type=float,
        default=None,
        help="When --sort-by ratio_mean: only print rows with ratio_to_neighbor_mean >= this",
    )
    p.add_argument(
        "--min-lcoe-gap",
        type=float,
        default=None,
        help="Require (focal LCOE − neighbor_median_lcoe) >= this $/MWh (applies before sort-by filters)",
    )
    p.add_argument(
        "--top",
        type=int,
        default=0,
        help="After sorting, print at most this many rows (0 = no cap). If no threshold is set for the chosen --sort-by, defaults to 50 rows for backward compatibility.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write per-site table (default: <combined_stem>_neighbor_lcoe.csv)",
    )
    p.add_argument(
        "--pairs-out",
        type=Path,
        default=None,
        help="Write cheapest-neighbor pairs for rows selected by ratio_cheapest path (optional)",
    )
    args = p.parse_args(argv)

    df = pd.read_csv(args.combined_csv)
    try:
        out = enrich_combined_df_with_neighbor_lcoe(
            df,
            radius_km=args.radius_km,
            same_country_only=args.same_country_only,
            sector=args.sector,
            lcol=LCOL,
        )
    except ValueError as e:
        raise SystemExit(str(e))
    n = len(out)
    n_nb = out["n_neighbors_in_radius"].to_numpy()

    keep = n_nb >= args.min_neighbors
    out_filtered = out.loc[keep].copy()
    gap_applied = False
    if args.min_lcoe_gap is not None:
        gap = out_filtered[LCOL] - out_filtered["neighbor_median_lcoe"]
        out_filtered = out_filtered[gap >= args.min_lcoe_gap].copy()
        gap_applied = True

    threshold_active = False
    if args.sort_by == "abs_z":
        flagged = out_filtered[np.isfinite(out_filtered["lcoe_z_vs_neighbors"])].copy()
        if args.min_abs_z is not None:
            flagged = flagged[flagged["abs_z_vs_neighbors"] >= args.min_abs_z]
            threshold_active = True
        flagged = flagged.sort_values("abs_z_vs_neighbors", ascending=False)
    elif args.sort_by == "ratio_median":
        flagged = out_filtered.dropna(subset=["ratio_to_neighbor_median"]).copy()
        if args.min_ratio_median is not None:
            flagged = flagged[flagged["ratio_to_neighbor_median"] >= args.min_ratio_median]
            threshold_active = True
        flagged = flagged.sort_values("ratio_to_neighbor_median", ascending=False)
    elif args.sort_by == "ratio_mean":
        flagged = out_filtered.dropna(subset=["ratio_to_neighbor_mean"]).copy()
        if args.min_ratio_mean is not None:
            flagged = flagged[flagged["ratio_to_neighbor_mean"] >= args.min_ratio_mean]
            threshold_active = True
        flagged = flagged.sort_values("ratio_to_neighbor_mean", ascending=False)
    else:
        flagged = out_filtered[np.isfinite(out_filtered["ratio_to_cheapest_neighbor"])].copy()
        flagged = flagged[flagged["ratio_to_cheapest_neighbor"] >= args.min_ratio]
        threshold_active = True
        flagged = flagged.sort_values("ratio_to_cheapest_neighbor", ascending=False)

    threshold_active = threshold_active or gap_applied
    lim = args.top if args.top > 0 else (None if threshold_active else 50)
    if lim is not None:
        flagged = flagged.head(lim)

    print(
        f"Rows: {n} | radius: {args.radius_km} km | same-country: {args.same_country_only} | "
        f"min-neighbors: {args.min_neighbors} | sort-by: {args.sort_by}"
    )
    print(f"Rows after min-neighbors filter: {int(keep.sum())}")
    print(
        f"Console table rows: {len(flagged)} | threshold mode: {threshold_active} | "
        f"min_abs_z={args.min_abs_z}, min_ratio_median={args.min_ratio_median}, "
        f"min_ratio_mean={args.min_ratio_mean}, min_lcoe_gap={args.min_lcoe_gap}, "
        f"min_ratio_cheapest={args.min_ratio}, "
        f"top_cap={args.top if args.top > 0 else ('none' if threshold_active else 50)}"
    )
    print()

    disp_cols = [
        c
        for c in [
            "source_id",
            "iso3_country",
            "lat",
            "lon",
            "sector",
            "source_type",
            LCOL,
            "n_neighbors_in_radius",
            "neighbor_median_lcoe",
            "neighbor_mean_lcoe",
            "ratio_to_neighbor_median",
            "lcoe_z_vs_neighbors",
            "abs_z_vs_neighbors",
            "ratio_to_cheapest_neighbor",
            "cheapest_neighbor_source_id",
        ]
        if c in flagged.columns
    ]
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 240)
    print(flagged[disp_cols].to_string(index=False))

    out_path = args.out
    if out_path is None:
        out_path = args.combined_csv.parent / f"{args.combined_csv.stem}_neighbor_lcoe.csv"
    out.to_csv(out_path, index=False)
    print(f"\nWrote full per-site metrics ({len(out)} rows): {out_path}")

    if args.pairs_out is not None and args.sort_by == "ratio_cheapest" and len(flagged) > 0:
        pairs = flagged.assign(
            expensive_source_id=flagged["source_id"],
            cheap_source_id=flagged["cheapest_neighbor_source_id"],
        )[
            [
                "expensive_source_id",
                "cheap_source_id",
                "distance_km_to_cheapest_neighbor",
                LCOL,
                "ratio_to_cheapest_neighbor",
                "iso3_country",
            ]
        ].rename(columns={LCOL: "LCOE_expensive"})
        cheap_lcoe_map = out.set_index("source_id")[LCOL]
        pairs["LCOE_cheap_neighbor"] = pairs["cheap_source_id"].map(cheap_lcoe_map)
        pairs.to_csv(args.pairs_out, index=False)
        print(f"Wrote pairs summary: {args.pairs_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
