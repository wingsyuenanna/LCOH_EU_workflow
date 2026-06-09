"""
Shared helpers for ``map_*.py`` scripts under ``flatblock_optimization/utils/maps``.

- Path defaults (repo root, scenarios dir, sites CSV, facility master)
- ``aggregate_scenarios``: load ``summary_site*.csv`` from scenario run folders
- ``format_capacity_short``: popup capacity strings
- ``load_combined_or_aggregate``: combined CSV vs scenario folders + sites merge
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import pandas as pd

# flatblock_optimization/
_FLATBLOCK = Path(__file__).resolve().parents[2]
# industrial_facility_db/
_REPO = _FLATBLOCK.parent

# Columns merged from sites CSV when building from scenario folders (intersection with CSV).
SITE_MERGE_COLS: Sequence[str] = (
    "source_id",
    "iso3_country",
    "lat",
    "lon",
    "sector",
    "source_type",
    "capacity",
    "capacity_units",
)


def flatblock_root() -> Path:
    return _FLATBLOCK


def repo_root() -> Path:
    return _REPO


def default_scenarios_dir() -> Path:
    return _FLATBLOCK / "scenarios"


def default_sites_csv() -> Path:
    return _FLATBLOCK / "inputs" / "sites_sample_countries.csv"


def default_facility_master() -> Path:
    v6 = _REPO / "views" / "facility_master_v6.csv"
    return v6 if v6.exists() else _REPO / "views" / "facility_master_v5.csv"


def aggregate_scenarios(base_path: str, scenario: str) -> pd.DataFrame:
    """Load all ``summary_site*.csv`` under ``base_path`` for the given scenario name."""
    root = Path(base_path)
    legacy = re.compile(rf"^scenario_{re.escape(str(scenario))}_(\d+)$")
    short = re.compile(r"^scenario_(\d+)$")
    all_dfs: List[pd.DataFrame] = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        m = legacy.match(folder.name)
        if not m:
            m = short.match(folder.name)
        if not m:
            continue
        site = m.group(1)
        fp = root / folder.name / "results" / f"summary_site{site}.csv"
        if not fp.exists():
            continue
        tmp = pd.read_csv(fp)
        tmp["scenario"] = scenario
        tmp["site"] = int(site)
        all_dfs.append(tmp)
    if not all_dfs:
        raise FileNotFoundError(
            f"No matching summary_site*.csv under {root} for scenario {scenario}"
        )
    return pd.concat(all_dfs, ignore_index=True)


def format_capacity_short(value: float) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    v = float(value)
    if v == 0:
        return "0"
    abs_v = abs(v)
    if abs_v >= 1e9:
        return f"{v / 1e9:.1f}B".replace(".0B", "B")
    if abs_v >= 1e6:
        return f"{v / 1e6:.1f}M".replace(".0M", "M")
    if abs_v >= 1e3:
        return f"{v / 1e3:.1f}k".replace(".0k", "k")
    return f"{v:.0f}" if v == int(v) else f"{v:.2f}"


def merge_sites_into_combined(combined_df: pd.DataFrame, sites_csv: Path) -> pd.DataFrame:
    """Merge site attributes from ``sites_csv`` into aggregated scenario results."""
    sites = pd.read_csv(sites_csv)
    cols = [c for c in SITE_MERGE_COLS if c in sites.columns]
    sites = sites[cols]
    out = combined_df.merge(sites, left_on="site", right_on="source_id", how="left")
    if "source_id" not in out.columns:
        out["source_id"] = out.get("site")
    return out


def load_combined_or_aggregate(
    *,
    combined_csv: Optional[Path],
    scenario: str,
    scenarios_dir: Path,
    sites_csv: Path,
    combined_required_cols: Sequence[str],
) -> pd.DataFrame:
    """
    Either read ``combined_csv`` (checking required columns) or aggregate scenario folders
    under ``scenarios_dir / scenario`` and merge ``sites_csv``.
    """
    if combined_csv is not None:
        if not combined_csv.exists():
            print(f"Error: --combined-csv not found: {combined_csv}", file=sys.stderr)
            sys.exit(1)
        df = pd.read_csv(combined_csv)
        for col in combined_required_cols:
            if col not in df.columns:
                print(f"Error: combined CSV must contain '{col}'", file=sys.stderr)
                sys.exit(1)
        return df

    scenario_base = scenarios_dir / scenario
    if not scenario_base.is_dir():
        print(f"Error: scenario directory not found: {scenario_base}", file=sys.stderr)
        sys.exit(1)
    agg = aggregate_scenarios(str(scenario_base), scenario)
    if not sites_csv.exists():
        print(f"Error: sites CSV not found: {sites_csv}", file=sys.stderr)
        sys.exit(1)
    return merge_sites_into_combined(agg, sites_csv)


def resolve_scenarios_dir_and_sites(
    scenarios_dir: Optional[Path],
    sites_csv: Optional[Path],
) -> Tuple[Path, Path]:
    """Default ``scenarios`` and ``sites_sample_countries`` under flatblock when omitted."""
    sd = scenarios_dir if scenarios_dir is not None else default_scenarios_dir()
    sc = sites_csv if sites_csv is not None else default_sites_csv()
    return sd, sc
