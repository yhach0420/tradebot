"""Stage membership loaders + coverage sanity."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from research.e1_x14_board_independent_signal.ticks import list_day_symbols

from . import HISTORICAL_DAYS, STRESS_DAYS_285A

NATIVE = Path(__file__).resolve().parents[3]


def _norm_sym(s: str) -> str:
    s = str(s).strip()
    if s.endswith(".T"):
        s = s[:-2]
    return s


def load_universe_symbols(day: str) -> set[str]:
    """Union of AM/PM/refresh universe CSVs for day (runtime SoT)."""
    ddir = NATIVE / "results" / "daily" / day / "runtime"
    out: set[str] = set()
    if not ddir.exists():
        return out
    for fp in sorted(ddir.glob(f"universe_core10_dynamic40_price_risk_*_{day}.csv")):
        with fp.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = _norm_sym(row.get("symbol") or row.get("symbol_key") or "")
                if not sym:
                    continue
                # keep passed=True if column present
                passed = row.get("passed")
                if passed is not None and str(passed).lower() in ("false", "0", "no"):
                    continue
                out.add(sym)
    return out


def load_captured_symbols(day: str) -> set[str]:
    return set(list_day_symbols(day))


def candidate_symbols_by_day(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {d: set() for d in HISTORICAL_DAYS}
    for r in rows:
        d = r["date"]
        if d in out:
            out[d].add(str(r["symbol"]))
    return out


def coverage_by_day(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cand = candidate_symbols_by_day(rows)
    recs = []
    for d in HISTORICAL_DAYS:
        cap = load_captured_symbols(d)
        uni = load_universe_symbols(d)
        uni_in_cap = uni & cap
        cs = cand[d]
        recs.append({
            "date": d,
            "captured_symbol_count": len(cap),
            "universe_csv_symbol_count": len(uni),
            "universe_intersect_captured": len(uni_in_cap),
            "capture_minus_universe": len(cap - uni),
            "universe_minus_capture": len(uni - cap),
            "candidate_symbol_count": len(cs),
            "candidate_in_universe": len(cs & uni),
            "candidate_in_captured": len(cs & cap),
            "285A": (
                "NOT_PRESENT" if d in STRESS_DAYS_285A and "285A" not in cs and "285A" not in cap
                else ("PRESENT" if "285A" in cs or "285A" in cap else "NOT_PRESENT")
            ),
            "market_label": "CAPTURED_MARKET_PROXY",
        })
    return recs
