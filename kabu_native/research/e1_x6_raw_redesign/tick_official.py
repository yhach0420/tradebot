"""Official symbol-class determination for the tick resolver (Phase A-R2 §8).

Class comes from the read-only JPX-derived master (scale_category / 規模区分):
TOPIX Core30 / Large70 / Mid400 => NARROW_TOPIX500, TOPIX Small / non-TOPIX
common stock => OTHER. Empirical 9-day increments are used ONLY as a cross
check: any contradiction (or ETF/REIT/missing master row) leaves the symbol
UNRESOLVED => P1_R2_BLOCKED. `BOTH_CONSISTENT_COARSER_CHOSEN` is never used as
a final determination. No 0.1-yen fallback exists.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Optional

from .store import sha256_file
from .tick_resolver import CLASS_NARROW, CLASS_OTHER, tick_size

TOPIX500_SCALES = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")


def master_path(repo_root: Path) -> Path:
    return repo_root / "data" / "jpx" / "all_symbols.csv"


def load_master(repo_root: Path) -> dict[str, dict[str, str]]:
    fp = master_path(repo_root)
    out: dict[str, dict[str, str]] = {}
    with fp.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = str(row.get("symbol") or "").strip()
            if code.endswith(".T"):
                code = code[:-2]
            if code:
                out[code] = row
    return out


def official_class(row: Optional[dict[str, str]]) -> tuple[Optional[str], str]:
    """(class or None, reason). ETF/REIT/missing => unresolved (never fallback)."""
    if row is None:
        return None, "NOT_IN_MASTER"
    if str(row.get("is_etf", "")).lower() == "true":
        return None, "ETF_QUOTATION_TABLE_NOT_COVERED"
    if str(row.get("is_reit", "")).lower() == "true":
        return None, "REIT_QUOTATION_TABLE_NOT_COVERED"
    scale = str(row.get("scale_category") or "").strip()
    if scale in TOPIX500_SCALES:
        return CLASS_NARROW, f"OFFICIAL_SCALE:{scale}"
    return CLASS_OTHER, f"OFFICIAL_SCALE:{scale or 'NON_TOPIX'}"


def empirical_check(
    symbol_class: str,
    band_min_increments: dict[str, float],
) -> tuple[bool, str]:
    """Verify observed increments are exact multiples of the official-class
    tick in every observed band (empirical evidence used as CROSS-CHECK only)."""
    for pk, inc in band_min_increments.items():
        price = max(float(pk), 0.1)
        t = tick_size(symbol_class, price)
        ratio = inc / t
        if ratio < 0.999:
            return False, f"OBSERVED_INCREMENT_FINER_THAN_OFFICIAL_TICK@{pk}:{inc}<{t}"
        if abs(ratio - round(ratio)) > 1e-6:
            return False, f"OBSERVED_INCREMENT_NOT_MULTIPLE@{pk}:{inc}%{t}"
    return True, "CONSISTENT"


def classify_universe_official(
    repo_root: Path,
    universe: list[str],
    tick_evidence: dict[str, dict[str, list]],
) -> dict[str, Any]:
    master = load_master(repo_root)
    rows: dict[str, Any] = {}
    unresolved: list[str] = []
    for sym in sorted(universe):
        cls, reason = official_class(master.get(sym))
        check_ok, check_msg = True, "NO_OBSERVATIONS"
        obs = tick_evidence.get(sym, {})
        if cls is not None and obs:
            check_ok, check_msg = empirical_check(cls, {k: v[0] for k, v in obs.items()})
        if cls is None or not check_ok:
            unresolved.append(sym)
        rows[sym] = {
            "class": cls if (cls is not None and check_ok) else None,
            "official_reason": reason,
            "empirical_check": check_msg,
            "observations": int(sum(v[1] for v in obs.values())) if obs else 0,
        }
    return {
        "master_path": str(master_path(repo_root)),
        "master_sha256": sha256_file(master_path(repo_root)),
        "rule": (
            "official scale_category decides the class (Core30/Large70/Mid400 => "
            "NARROW_TOPIX500, else OTHER for common stock); observed increments are "
            "cross-check only; ETF/REIT/missing/contradiction => UNRESOLVED => "
            "P1_R2_BLOCKED (no 0.1-yen fallback)"
        ),
        "symbol_classes": rows,
        "unresolved": unresolved,
    }
