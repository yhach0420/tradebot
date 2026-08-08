"""E1_X17 sealed historical prospective runner."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    CANDIDATE_ID,
    DOCUMENT_ID,
    EXPECTED_PRECOMMIT_SHA,
    FORBIDDEN_DAY,
    HIST_A2_VS_A1,
    SEAL_MODE,
    SOURCE_RUN,
    TARGET_DAY,
    VERDICT_FAILED,
    VERDICT_INSUFFICIENT,
    VERDICT_MIXED,
    VERDICT_SEAL_FAIL,
    VERDICT_SUPPORTED,
    VWAP_UPPER_LIMIT_BPS,
)
from .construct import construct_c0
from .evaluate import (
    a3_a4_diagnostic,
    cohort_metrics,
    freshness_diagnostics,
    historical_direction,
    primary_gate,
)
from .publish import publish
from .seal import verify_seal

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x17_vwap_reject_prospective"


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:12000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x17_vwap_reject_prospective.py"
    import os
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=line"],
        cwd=str(NATIVE), capture_output=True, text=True, env=env,
    )
    out = (p.stdout or "") + (p.stderr or "")
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", out)
    if m2:
        failed = int(m2.group(1))
    return {
        "exit_code": p.returncode, "passed": passed, "failed": failed,
        "total": passed + failed or 1,
        "rows": [{"test": "pytest_suite",
                  "outcome": "PASSED" if p.returncode == 0 else "FAILED",
                  "detail": out[-2500:]}],
        "output": out[-4000:],
    }


def run(*, force_construct: bool = False, allow_rereport: bool = False) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    run_id = f"e1x17_prospective_{now.strftime('%Y%m%d_%H%M%S')}_A"

    # --- Seal BEFORE opening 20260803 for alpha ---
    if allow_rereport and (OUT / "report.json").exists():
        (OUT / "report.json").unlink()
        for extra in ("report.md", "audit.xlsx"):
            p = OUT / extra
            if p.exists():
                p.unlink()

    seal = verify_seal()
    (OUT / "_seal.json").write_text(json.dumps(seal, indent=2, default=str), encoding="utf-8")

    if not seal["ok"]:
        report = {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "verdict": VERDICT_SEAL_FAIL,
            "seal_mode": SEAL_MODE,
            "target_day": TARGET_DAY,
            "seal": seal,
            "precommit_sha_match": False,
            "supports": {},
            "safety": _safety(opened=False),
            "_sheets": {
                "SealIntegrity": _kv(seal),
                "ChangeLog": [{"at": now.isoformat(), "note": "seal integrity failed — did not open 20260803"}],
            },
        }
        tests = {"exit_code": 0, "passed": 0, "failed": 0, "total": 0, "rows": []}
        det = {"ab_match": True, "note": "seal_fail_short_circuit"}
        publish(report, tests, det, OUT)
        print(json.dumps({"run_id": run_id, "verdict": VERDICT_SEAL_FAIL, "reasons": seal["reasons"]}, indent=2))
        return report

    # --- Open TARGET_DAY once for sealed historical prospective ---
    rows = construct_c0(force=force_construct)
    c0 = cohort_metrics(rows, "in_C0")
    eval_ok = cohort_metrics(rows, "in_VWAP_evaluable")
    not_eval = cohort_metrics(rows, "in_VWAP_not_evaluable")
    a2 = cohort_metrics(rows, "in_A2")
    rej = cohort_metrics(rows, "in_A2_Rejected")

    # Missing separated — not mixed into reject effect
    missing_block = {
        "C0_baseline": c0,
        "VWAP_evaluable": eval_ok,
        "VWAP_not_evaluable": not_eval,
        "A2_pass": a2,
        "A2_rejected": rej,
        "note": "not_evaluable excluded from A2/Rejected; not mixed into reject effect",
    }

    gate = primary_gate(c0, a2, rej)
    fresh = freshness_diagnostics(rows)
    hist = historical_direction(a2, c0)
    a34 = a3_a4_diagnostic(rows)

    if gate.get("insufficient"):
        verdict = VERDICT_INSUFFICIENT
    elif gate["status"] == "PASS":
        verdict = VERDICT_SUPPORTED
    elif gate["status"] == "MIXED":
        verdict = VERDICT_MIXED
    else:
        verdict = VERDICT_FAILED

    next_step = None
    if verdict == VERDICT_SUPPORTED:
        next_step = {
            "entry_candidate": "RPFE C0 + VWAP late-chase rejection",
            "next": [
                "ask ENTRY / bid path evaluation",
                "spread / execution cost",
                "100株損益",
                "ENTRY後のEXIT設計",
            ],
        }
    else:
        next_step = {"A2_frozen": False, "note": "stop — do not advance ENTRY path"}

    # Determinism
    m1 = {"c0": c0, "a2": a2, "rej": rej, "gate": gate}
    m2 = {
        "c0": cohort_metrics(rows, "in_C0"),
        "a2": cohort_metrics(rows, "in_A2"),
        "rej": cohort_metrics(rows, "in_A2_Rejected"),
        "gate": primary_gate(c0, a2, rej),
    }
    h1, h2 = sha256_obj(m1), sha256_obj(m2)
    det = {"ab_match": h1 == h2, "hash_a": h1, "hash_b": h2}

    supports = {
        "C0": c0["support"],
        "VWAP_evaluable": eval_ok["support"],
        "VWAP_not_evaluable": not_eval["support"],
        "A2": a2["support"],
        "A2_Rejected": rej["support"],
    }

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "seal_ok": True,
        "precommit_sha_match": True,
        "expected_precommit_sha": EXPECTED_PRECOMMIT_SHA,
        "supports": supports,
        "gate": gate,
        "freshness_sensitive": fresh.get("VWAP_REJECT_FRESHNESS_SENSITIVE"),
        "target_day": TARGET_DAY,
        "forbidden_day": FORBIDDEN_DAY,
        "opened_20260803": True,
        "opened_20260804": False,
        "candidate_rule": (
            f"C0 anchor AND distance_from_vwap_bps evaluable "
            f"AND distance_from_vwap_bps <= {VWAP_UPPER_LIMIT_BPS}"
        ),
        "threshold": VWAP_UPPER_LIMIT_BPS,
        "no_rebound_in_candidate": True,
        "no_activity_in_candidate": True,
        "hist_frozen": HIST_A2_VS_A1,
        "one_anchor_per_episode": True,
        "n_rows": len(rows),
        "n_episodes_unique": len({r["rpfe_episode_id"] for r in rows}),
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    tests = _run_tests()
    safety = _safety(opened=True)

    sheets = {
        "SealIntegrity": _kv(seal),
        "PrecommitIdentity": _kv(seal.get("precommit") or {}),
        "SourceIdentity": _kv({
            "source_run": SOURCE_RUN,
            "candidate_id": CANDIDATE_ID,
            "target_day": TARGET_DAY,
            "seal_mode": SEAL_MODE,
            "raw": seal.get("source_raw"),
        }),
        "C0Construction": _kv({
            "contract": "X15 RPFE episode + C0 canonical first-candidate anchor",
            "one_episode_max_one_c0": True,
            "session_boundary": "AM < 12:00 / PM otherwise; no cross-session",
            "n_c0_ok": c0["support"],
            "n_rows": len(rows),
        }),
        "CandidateContract": _kv({
            "candidate_id": CANDIDATE_ID,
            "exact_rule": interim["candidate_rule"],
            "threshold": VWAP_UPPER_LIMIT_BPS,
            "no_retune": True,
            "no_rebound": True,
            "no_activity": True,
        }),
        "PrimaryOutcomes": [c0, eval_ok, a2],
        "RejectedOutcomes": [rej, not_eval],
        "HistoricalComparison": _kv(hist),
        "FreshnessDiagnostics": _kv(fresh),
        "A3A4Diagnostics": _kv(a34),
        "ProspectiveGate": _kv(gate),
        "ChangeLog": [{"at": now.isoformat(), "note": "E1_X17 sealed historical prospective 20260803 open-once"}],
    }

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "source_run": SOURCE_RUN,
        "candidate_id": CANDIDATE_ID,
        "verdict": verdict,
        "seal_mode": SEAL_MODE,
        "target_day": TARGET_DAY,
        "seal": seal,
        "precommit_sha_match": True,
        "supports": supports,
        "missing_separated": missing_block,
        "primary_outcomes": {"C0": c0, "A2": a2, "A2_Rejected": rej},
        "a2_vs_c0": {
            "forward_return_180s": (a2.get("forward_return_180s"), c0.get("forward_return_180s")),
            "MFE_180s": (a2.get("MFE_180s"), c0.get("MFE_180s")),
            "MAE_180s": (a2.get("MAE_180s"), c0.get("MAE_180s")),
            "touch": (a2.get("plus5_before_minus5"), c0.get("plus5_before_minus5")),
            "NoProgress": (a2.get("NO_PROGRESS_300S"), c0.get("NO_PROGRESS_300S")),
        },
        "rejected_vs_a2": {
            "forward_return_180s": (rej.get("forward_return_180s"), a2.get("forward_return_180s")),
            "touch": (rej.get("plus5_before_minus5"), a2.get("plus5_before_minus5")),
            "MAE_180s": (rej.get("MAE_180s"), a2.get("MAE_180s")),
            "NoProgress": (rej.get("NO_PROGRESS_300S"), a2.get("NO_PROGRESS_300S")),
        },
        "prospective_gate": gate,
        "freshness_diagnostics": fresh,
        "historical_comparison": hist,
        "a3_a4_diagnostic": a34,
        "next_step": next_step,
        "threshold": VWAP_UPPER_LIMIT_BPS,
        "safety": safety,
        "_sheets": sheets,
    }
    shas = publish(report, tests, det, OUT)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "supports": supports,
        "gate": gate["status"],
        "tests": f"{tests['passed']}/{tests['total']}",
        "ab": det["ab_match"],
        "shas": shas,
    }, indent=2))
    return report


def _safety(*, opened: bool) -> dict[str, Any]:
    return {
        "submit_cancel_live": "0/0/0",
        "mainline_changed": False,
        "production_yaml_changed": False,
        "ENTRY_changed": False,
        "EXIT_changed": False,
        "Universe_changed": False,
        "20260803_opened": opened,
        "20260804_opened": False,
        "Prospective_consumed": opened,  # this sealed historical open consumes the reserved day
        "Shadow": False,
        "Forward": False,
        "Paper_connection": False,
        "Discord": False,
        "paper_trade_only": True,
        "seal_mode": SEAL_MODE,
    }


if __name__ == "__main__":
    force = "--force" in sys.argv
    rereport = "--allow-rereport" in sys.argv
    run(force_construct=force, allow_rereport=rereport)
