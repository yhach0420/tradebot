"""Unaffected-hypothesis parity vs invalid V1 discovery. No q/D3/candidate comparison."""
from __future__ import annotations

import json
import math
from typing import Any

from research.causal_driver_pb1.sector_breadth_corrected_discovery import AFFECTED_SECTOR_ID, PARITY_ATOL
from research.causal_driver_pb1.sector_breadth_corrected_discovery.isolation import DISC_V1_OUT

PARITY_FIELDS = ("n", "b_fx", "ci_lo", "ci_hi", "p_boot", "D1", "D2", "D4", "D5", "D6")


def _finite_close(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    try:
        fa = float(a)
        fb = float(b)
    except (TypeError, ValueError):
        return a == b
    if math.isnan(fa) and math.isnan(fb):
        return True
    if not math.isfinite(fa) or not math.isfinite(fb):
        return fa == fb
    return math.isclose(fa, fb, rel_tol=0.0, abs_tol=PARITY_ATOL)


def load_old_family() -> list[dict[str, Any]]:
    doc = json.loads((DISC_V1_OUT / "report.json").read_text(encoding="utf-8"))
    ev = doc.get("evaluation") or {}
    fam = list(ev.get("family") or [])
    if not fam:
        fam = list((ev.get("dev") or {}).get("family") or [])
    if not fam:
        fam = list((doc.get("answers") or {}).get("family") or [])
    return fam


def old_3650_zero_n() -> int:
    n = 0
    for r in load_old_family():
        if str(r.get("scope_id") or "") != f"SECTOR_{AFFECTED_SECTOR_ID}":
            continue
        if int(r.get("n") or 0) == 0:
            n += 1
    return n


def compare_unaffected(new_family: list[dict[str, Any]]) -> dict[str, Any]:
    old = {str(r.get("test_id")): r for r in load_old_family()}
    rows = []
    fail = []
    for rec in new_family:
        if rec.get("used_correction_branch") or str(rec.get("scope_id") or "") == f"SECTOR_{AFFECTED_SECTOR_ID}":
            continue
        tid = str(rec.get("test_id"))
        prev = old.get(tid)
        if prev is None:
            fail.append({"test_id": tid, "reason": "MISSING_IN_OLD_DISCOVERY"})
            rows.append({"test_id": tid, "pass": False, "reason": "MISSING_IN_OLD_DISCOVERY"})
            continue
        mismatches = []
        for k in PARITY_FIELDS:
            if not _finite_close(rec.get(k), prev.get(k)):
                mismatches.append({"field": k, "old": prev.get(k), "new": rec.get(k)})
        ok = not mismatches
        row = {"test_id": tid, "scope_id": rec.get("scope_id"), "metric": rec.get("metric"), "lookback": rec.get("lookback"), "horizon": rec.get("horizon"), "pass": ok}
        if mismatches:
            row["mismatches"] = mismatches
            fail.append(row)
        rows.append(row)
    return {
        "checked_n": len(rows),
        "fail_n": len(fail),
        "pass": not fail,
        "fails": fail[:32],
        "rows": rows,
    }
