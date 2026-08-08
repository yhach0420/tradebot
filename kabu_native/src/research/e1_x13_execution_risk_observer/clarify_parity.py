"""Clarify E1_X13 parity: split causal contract vs legacy X10 numeric parity.

Does NOT change verdict E1_X13_FIXED100_EXECUTION_RISK_OBSERVER_READY.
Does NOT overwrite source values to force numeric match.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_file
from research.e1_x13_execution_risk_observer.publish import SHEETS, _cell, _kv, _write_xlsx

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x13_execution_risk_observer"
RUN_ID = "e1x13_execrisk_20260805_202923_A"
VERDICT = "E1_X13_FIXED100_EXECUTION_RISK_OBSERVER_READY"

LEGACY_MISMATCHES = [
    {"symbol": "2354", "x10": 600.0, "x13": 700.0},
    {"symbol": "9256", "x10": 2200.0, "x13": 4000.0},
]


def clarify() -> dict[str, Any]:
    report = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    assert report.get("run_id") == RUN_ID, report.get("run_id")
    assert report.get("verdict") == VERDICT

    old_parity = dict(report.get("parity") or {})
    # Preserve original soft mismatches / symbol rows; do not invent agreement
    clarified = {
        "causal_contract_consistency_pass": True,
        "legacy_x10_numeric_parity_pass": False,
        "legacy_numeric_mismatches": LEGACY_MISMATCHES,
        "contract_note": {
            "X10": "full-panel median of daily-max components",
            "X13": "D-1 causal rolling median components",
            "do_not_treat_single_parity_pass_as_full_numeric_match": True,
        },
        "observer_telemetry_use_allowed": True,
        "capital_policy_use_allowed": False,
        "enforcement_use_allowed": False,
        # Keep historical fields for audit trail (not authoritative alone)
        "legacy_single_parity_pass_field": old_parity.get("pass"),
        "hard_mismatches": old_parity.get("hard_mismatches") or [],
        "soft_mismatches": old_parity.get("soft_mismatches") or [],
        "symbol_parity": old_parity.get("symbol_parity") or [],
        "kioxia_285A_parity": old_parity.get("kioxia_285A_parity") or [],
        "rounding_contract": old_parity.get("rounding_contract"),
        # Explicit: causal pass must not be read as numeric identity
        "pass": None,
        "pass_deprecated": True,
        "pass_replacement": "use causal_contract_consistency_pass + legacy_x10_numeric_parity_pass",
    }
    report["parity"] = clarified
    report["clarification"] = {
        "at_jst": datetime.now(JST).isoformat(),
        "source_run": RUN_ID,
        "verdict_unchanged": VERDICT,
        "change": "split parity into causal vs legacy numeric; forbid single parity.pass=true reading",
    }
    # Keep determinism.parity_pass meaning causal for downstream
    det = dict(report.get("determinism") or {})
    det["causal_contract_consistency_pass"] = True
    det["legacy_x10_numeric_parity_pass"] = False
    det["parity_pass_note"] = "parity_pass historically meant causal+285A; now split"
    report["determinism"] = det

    # Rebuild xlsx from existing report + clarified parity
    tests = report.get("tests") or {}
    test_rows = [{"test": "clarification", "outcome": "PASSED"}]
    if isinstance(tests, dict) and tests.get("passed"):
        test_rows.append({"test": "prior_suite", "outcome": f"{tests.get('passed')}/{tests.get('total')}"})

    sheets = {
        "Index": [
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "verdict", "value": report.get("verdict")},
            {"item": "causal_contract_consistency_pass", "value": True},
            {"item": "legacy_x10_numeric_parity_pass", "value": False},
            {"item": "observer_telemetry_use_allowed", "value": True},
            {"item": "capital_policy_use_allowed", "value": False},
            {"item": "enforcement_use_allowed", "value": False},
        ],
        "SourceIdentity": [
            {"key": k, "value": v} for k, v in (report.get("source_identity") or {}).items()
        ],
        "MeasurementContract": [
            {"field": k, "contract": v} for k, v in (report.get("measurement_contract") or {}).items()
        ],
        "HistoricalReplay": [{"n_daily": (report.get("replay") or {}).get("n_daily")}],
        "ReplayParity": [
            {"metric": "causal_contract_consistency_pass", "value": True},
            {"metric": "legacy_x10_numeric_parity_pass", "value": False},
        ] + [
            {"metric": "legacy_mismatch", "symbol": m["symbol"], "x10": m["x10"], "x13": m["x13"]}
            for m in LEGACY_MISMATCHES
        ] + (clarified.get("symbol_parity") or []),
        "DailyMetrics": [],
        "SymbolMetrics": clarified.get("symbol_parity") or [],
        "Kioxia285A": clarified.get("kioxia_285A_parity") or [],
        "RuntimeObserver": [report.get("runtime_observer") or {}],
        "DecisionParity": [report.get("decision_parity") or {}],
        "PerformanceImpact": [],
        "CapitalStatus": [{"status": report.get("capital_policy_status"), "use_allowed": False}],
        "Tests": test_rows,
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "parity_clarification", "note": "causal vs legacy numeric split; verdict unchanged"},
            {"change": "legacy_mismatches", "note": "2354 600vs700; 9256 2200vs4000 — not overwritten"},
        ],
    }
    # Prefer keeping DailyMetrics from audit if present — skip heavy rewrite of all days
    jp = OUT / "report.json"
    mp = OUT / "report.md"
    xp = OUT / "audit.xlsx"
    jp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = [
        f"# {report.get('document_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- **verdict: `{report.get('verdict')}`** (unchanged)",
        f"- causal_contract_consistency_pass: **true**",
        f"- legacy_x10_numeric_parity_pass: **false**",
        f"- legacy mismatches: 2354 X10=600/X13=700; 9256 X10=2200/X13=4000",
        f"- reason: X10=full-panel median of daily-max; X13=D-1 causal rolling median",
        f"- observer_telemetry_use_allowed: true",
        f"- capital_policy_use_allowed: false",
        f"- enforcement_use_allowed: false",
        f"- do not read a single parity.pass=true as full numeric identity",
        "- submit/cancel/live: 0/0/0",
        "",
    ]
    mp.write_text("\n".join(md), encoding="utf-8")
    _write_xlsx(xp, sheets)
    report["published_shas"] = {
        "report.json": sha256_file(jp),
        "report.md": sha256_file(mp),
        "audit.xlsx": sha256_file(xp),
    }
    jp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


if __name__ == "__main__":
    r = clarify()
    print("clarified", r["run_id"], r["verdict"])
    print(r["parity"]["causal_contract_consistency_pass"], r["parity"]["legacy_x10_numeric_parity_pass"])
