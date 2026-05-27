#!/usr/bin/env python3
"""Phase 149: TSE alpha symbol support in Core/watchlist validation."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _run_cases() -> dict[str, Any]:
    from universe.core_watchlist import (
        _TSE_ALPHA_RE,
        _TSE_NUMERIC_RE,
        can_add_to_core,
        can_replace_core,
        load_core_state,
        normalize_watch_symbol,
        save_core_state,
        validate_watch_symbol,
    )

    accept = [
        "7203.T",
        "9984.T",
        "186A.T",
        "130A.T",
        "153A.T",
        "186a.t",
        "7203",
        "186A",
    ]
    reject = ["12345.T", "186AA.T", "18A.T", "ABCD.T", "7203.TX", "72030.T", ""]

    accept_results = []
    for raw in accept:
        sym = normalize_watch_symbol(raw)
        ok, reason = validate_watch_symbol(raw)
        accept_results.append(
            {
                "input": raw,
                "normalized": sym,
                "valid": ok,
                "reject_reason": reason,
            }
        )

    reject_results = []
    for raw in reject:
        sym = normalize_watch_symbol(raw)
        ok, reason = validate_watch_symbol(raw)
        reject_results.append(
            {
                "input": raw,
                "normalized": sym,
                "valid": ok,
                "reject_reason": reason,
            }
        )

    replace_raw = "3436.T,4188.T,186A.T,130A.T,153A.T"
    rep_ok, rep_ordered, rep_reject, rep_msg = can_replace_core(replace_raw)
    add_ok, add_reject, add_msg = can_add_to_core(["3436.T"], "186A.T")

    # Temp watchlist: persist replace including 186A.T
    persist_ok = False
    persist_symbols: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        (tmp_root / "discord_issue_bot").mkdir(parents=True)
        save_core_state(tmp_root, rep_ordered if rep_ok else [])
        state = load_core_state(tmp_root)
        persist_symbols = list(state.symbols)
        persist_ok = rep_ok and "186A.T" in persist_symbols

    accept_pass = all(r["valid"] for r in accept_results)
    reject_pass = all(not r["valid"] for r in reject_results)
    regression_pass = all(
        r["valid"]
        for r in accept_results
        if r["input"] in ("7203.T", "9984.T", "7203")
    )

    if not accept_pass or not reject_pass or not rep_ok or not persist_ok:
        verdict = "validation_regression"
        notes = []
        if not accept_pass:
            notes.append("expected symbols failed validation")
        if not reject_pass:
            notes.append("invalid symbols incorrectly accepted")
        if not regression_pass:
            notes.append("numeric TSE regression failed")
        if not rep_ok:
            notes.append(f"replace failed: {rep_reject} {rep_msg}")
        if not persist_ok:
            notes.append("replace persist check failed")
    else:
        verdict = "tse_alpha_symbol_supported"
        notes = ["186A.T and peers accepted; numeric symbols unchanged"]

    return {
        "phase": 149,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_options": {
            "A": "tse_alpha_symbol_supported",
            "B": "validation_regression",
        },
        "verdict_notes": notes,
        "patterns": {
            "numeric": _TSE_NUMERIC_RE.pattern,
            "alpha": _TSE_ALPHA_RE.pattern,
            "description": "4-digit .T OR 3-digit + letter .T",
        },
        "accept_cases": accept_results,
        "reject_cases": reject_results,
        "replace_test": {
            "input": replace_raw,
            "ok": rep_ok,
            "ordered": rep_ordered,
            "reject": rep_reject,
            "message": rep_msg,
        },
        "add_test": {
            "input": "186A.T",
            "existing": ["3436.T"],
            "ok": add_ok,
            "reject": add_reject,
            "message": add_msg,
        },
        "persist_test": {
            "ok": persist_ok,
            "symbols": persist_symbols,
        },
        "commands_using_validator": [
            "!core add",
            "!core replace",
            "!watch add",
            "!watch replace",
        ],
        "module": "kabu_native/src/universe/core_watchlist.py",
    }


def main() -> int:
    _bootstrap()
    report = _run_cases()
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "phase149_symbol_validation_update.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "path": str(out.relative_to(ROOT))}, ensure_ascii=True))
    return 0 if report["verdict"] == "tse_alpha_symbol_supported" else 1


if __name__ == "__main__":
    raise SystemExit(main())
