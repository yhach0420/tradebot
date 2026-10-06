"""PB1 V4 Complete Strategy build. Runtime 0/0/0. Development binding only."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.pb1_v4_clarified_machine_correction_v4.bind import bind_prior
from research.pb1_v4_complete_strategy_build_and_economic_validation import (
    ANALYSIS_ID,
    CASE_FAIL,
    CASE_READY,
    NEXT_STOP,
    PROGRAM_ID,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation.analyze import development_metrics, freeze_decision
from research.pb1_v4_complete_strategy_build_and_economic_validation.gate import identity_gate
from research.pb1_v4_complete_strategy_build_and_economic_validation.inventory import execution_inventory, exit_inventory
from research.pb1_v4_complete_strategy_build_and_economic_validation.isolation import (
    CACHE,
    OUT,
    V4_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation.publish import build_answers, build_sheets, write_artifacts
from research.pb1_v4_complete_strategy_build_and_economic_validation.replay import load_walked, replay_development
from research.pb1_v4_complete_strategy_build_and_economic_validation.roles import data_roles
from research.pb1_v4_complete_strategy_build_and_economic_validation.spec import source_sha256 as harness_sha256

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    files = sorted(p for p in root.glob("*.py") if p.is_file())
    for p in files:
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _safety() -> dict[str, Any]:
    return {
        "V4_CHANGED": False,
        "SPEC_CHANGED": False,
        "THRESHOLD_RETUNED": False,
        "ENTRY_RETUNED": False,
        "OLD_CONFIRMATION_ECONOMIC_OPENED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "PNL_USED_FOR_FREEZE": False,
        "MFE_MAE_USED": False,
        "FUTURE_OUTCOME_USED": False,
        "submit/cancel/live": "0/0/0",
        "orders_submit": 0,
        "orders_cancel": 0,
        "orders_live": 0,
        "research_paper_only": True,
        "formal_paper_certification": False,
    }


def _finish(report: dict[str, Any]) -> int:
    report["answers"] = build_answers(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    write_artifacts(report, sheets)
    ans = dict(report.get("answers") or {})
    print(f"VERDICT {ans.get('VERDICT')}", flush=True)
    print(f"NEXT {ans.get('NEXT')}", flush=True)
    print("STOP", flush=True)
    return 0 if str(ans.get("VERDICT") or "") == CASE_READY else 1


def _base_report(*, now: str, gate: dict[str, Any], iso_before: dict[str, Any]) -> dict[str, Any]:
    return {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": now,
        "identity": gate,
        "harness_source_sha256": harness_sha256(),
        "v4_src_fingerprint": _fingerprint(V4_SRC),
        "isolation_before": iso_before,
        "safety": _safety(),
        "trades": [],
        "development_metrics": {"role": "DEVELOPMENT_BINDING_NOT_CERTIFICATION", "used_for_freeze": False},
        "ECONOMIC_CONFIRMATION1_OPENED": False,
        "ECONOMIC_CONFIRMATION2_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
    }


def main() -> int:
    set_research_priority_below_normal()
    now = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S%z")
    CACHE.mkdir(parents=True, exist_ok=True)
    iso_before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(iso_before.get("ACTIVE_CAPTURE") or ""), str(iso_before.get("PAPER_SESSION") or ""))
    gate = identity_gate()
    exec_inv = execution_inventory()
    exit_inv = exit_inventory()
    print(f"IDENTITY_OK {gate.get('ok')} machine={gate.get('machine_sha')}", flush=True)
    if overlap != 0:
        report = _base_report(now=now, gate=gate, iso_before=iso_before)
        report["decision"] = {"VERDICT": CASE_FAIL, "NEXT": NEXT_STOP, "reason": "write_overlap"}
        report["execution_inventory"] = exec_inv
        report["exit_inventory"] = exit_inv
        return _finish(report)
    if not gate.get("ok"):
        report = _base_report(now=now, gate=gate, iso_before=iso_before)
        report["decision"] = {"VERDICT": CASE_FAIL, "NEXT": NEXT_STOP, "reason": "identity_mismatch"}
        report["execution_inventory"] = exec_inv
        report["exit_inventory"] = exit_inv
        return _finish(report)

    bind = bind_prior()
    roles = data_roles(bind)
    if not bind.get("ok") or not roles.get("ok"):
        report = _base_report(now=now, gate=gate, iso_before=iso_before)
        report["data_roles"] = roles
        report["execution_inventory"] = exec_inv
        report["exit_inventory"] = exit_inv
        report["decision"] = {
            "VERDICT": CASE_FAIL,
            "NEXT": NEXT_STOP,
            "reason": bind.get("reason") or roles.get("reason") or "bind_or_roles_failed",
        }
        return _finish(report)

    walked = load_walked()
    print(f"WALKED e0={walked.get('e0_n')} e1={walked.get('e1_n')} ok={walked.get('ok')}", flush=True)
    if not walked.get("ok"):
        report = _base_report(now=now, gate=gate, iso_before=iso_before)
        report["data_roles"] = roles
        report["execution_inventory"] = exec_inv
        report["exit_inventory"] = exit_inv
        report["walked"] = {k: walked.get(k) for k in ("ok", "reason", "e0_n", "e1_n", "n_days", "walked_path")}
        report["decision"] = {"VERDICT": CASE_FAIL, "NEXT": NEXT_STOP, "reason": walked.get("reason")}
        return _finish(report)

    replay = replay_development(
        bind=bind,
        walked=walked,
        forbidden_dates=set(roles.get("forbidden_load_dates") or []),
    )
    trades = list(replay.get("trades") or [])
    metrics = development_metrics(trades)
    freeze = freeze_decision(
        identity=gate,
        roles=roles,
        walked=walked,
        replay=replay,
        exec_inv=exec_inv,
        exit_inv=exit_inv,
    )
    iso_after = snapshot(phase="POST")
    report = _base_report(now=now, gate=gate, iso_before=iso_before)
    report.update(
        {
            "data_roles": roles,
            "execution_inventory": exec_inv,
            "exit_inventory": exit_inv,
            "walked": {k: walked.get(k) for k in ("ok", "reason", "e0_n", "e1_n", "same_bar_entry_n", "n_days", "walked_path")},
            "replay": {k: v for k, v in replay.items() if k != "trades"},
            "trades": trades,
            "development_metrics": metrics,
            "decision": freeze,
            "isolation_after": iso_after,
            "isolation_advanced": advanced(iso_before, iso_after),
            "out_dir": str(OUT).replace("\\", "/"),
        }
    )
    (CACHE / "replay_meta.json").write_text(
        json.dumps(
            {
                "ok": bool(replay.get("ok")),
                "candidate_n": replay.get("candidate_n"),
                "trade_n": len(trades),
                "skip": replay.get("skip"),
                "max_concurrent": replay.get("max_concurrent"),
                "cap_blocked_n": replay.get("cap_blocked_n"),
                "same_symbol_blocked_n": replay.get("same_symbol_blocked_n"),
                "COMPLETE_STRATEGY_SHA256": freeze.get("COMPLETE_STRATEGY_SHA256"),
                "VERDICT": freeze.get("VERDICT"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return _finish(report)


if __name__ == "__main__":
    raise SystemExit(main())
