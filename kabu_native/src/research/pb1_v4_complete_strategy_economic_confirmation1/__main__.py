"""PB1 V4 Complete Strategy Economic Confirmation 1. Runtime 0/0/0."""
from __future__ import annotations

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
from research.pb1_v4_complete_strategy_economic_confirmation1 import (
    ANALYSIS_ID,
    CASE_HASH,
    CASE_INVALID,
    CASE_PASS,
    NEXT_STOP,
    PROGRAM_ID,
)
from research.pb1_v4_complete_strategy_economic_confirmation1.decide import decide
from research.pb1_v4_complete_strategy_economic_confirmation1.gate import identity_gate
from research.pb1_v4_complete_strategy_economic_confirmation1.invariants import replay_invariants
from research.pb1_v4_complete_strategy_economic_confirmation1.isolation import (
    CACHE,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_complete_strategy_economic_confirmation1.metrics import economic_metrics
from research.pb1_v4_complete_strategy_economic_confirmation1.precommit import conf1_precommit
from research.pb1_v4_complete_strategy_economic_confirmation1.publish import build_answers, build_sheets, write_artifacts
from research.pb1_v4_complete_strategy_economic_confirmation1.replay_conf import walk_and_replay
from research.pb1_v4_complete_strategy_economic_confirmation1.spec import source_sha256 as harness_sha256
from research.pb1_v4_frozen_old_confirmation_blind_validation.dates import partition_dates

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "V4_CHANGED": False,
        "COMPLETE_STRATEGY_CHANGED": False,
        "THRESHOLD_RETUNED": False,
        "ENTRY_RETUNED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "submit/cancel/live": "0/0/0",
        "orders_submit": 0,
        "orders_cancel": 0,
        "orders_live": 0,
        "research_only": True,
    }


def _finish(report: dict[str, Any]) -> int:
    report["answers"] = build_answers(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    write_artifacts(report, sheets)
    ans = dict(report.get("answers") or {})
    print(f"VERDICT {ans.get('VERDICT')}", flush=True)
    print(f"NEXT {ans.get('NEXT')}", flush=True)
    print(f"ECONOMIC_EDGE_CONCENTRATED {ans.get('ECONOMIC_EDGE_CONCENTRATED')}", flush=True)
    print("STOP", flush=True)
    return 0 if str(ans.get("VERDICT") or "") == CASE_PASS else 1


def _base(*, now: str, gate: dict[str, Any], iso_before: dict[str, Any]) -> dict[str, Any]:
    return {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": now,
        "identity": gate,
        "harness_source_sha256": harness_sha256(),
        "isolation_before": iso_before,
        "safety": _safety(),
        "trades": [],
        "metrics": {},
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
    }


def main() -> int:
    set_research_priority_below_normal()
    now = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S%z")
    CACHE.mkdir(parents=True, exist_ok=True)
    iso_before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(iso_before.get("ACTIVE_CAPTURE") or ""), str(iso_before.get("PAPER_SESSION") or ""))
    gate = identity_gate()
    print(
        f"IDENTITY_OK {gate.get('ok')} cs={gate.get('COMPLETE_STRATEGY_SHA256')} machine={gate.get('machine_sha')}",
        flush=True,
    )
    if overlap != 0:
        report = _base(now=now, gate=gate, iso_before=iso_before)
        report["decision"] = {"VERDICT": CASE_INVALID, "NEXT": NEXT_STOP, "reason": "write_overlap"}
        return _finish(report)
    if not gate.get("ok"):
        report = _base(now=now, gate=gate, iso_before=iso_before)
        report["decision"] = {"VERDICT": CASE_HASH, "NEXT": NEXT_STOP, "reason": "identity_mismatch"}
        report["ECONOMIC_CONFIRMATION1_OPENED"] = False
        return _finish(report)

    bind = bind_prior()
    part = partition_dates(bind)
    if not bind.get("ok") or not part.get("ok"):
        report = _base(now=now, gate=gate, iso_before=iso_before)
        report["data_roles"] = part
        report["decision"] = {
            "VERDICT": CASE_INVALID,
            "NEXT": NEXT_STOP,
            "reason": bind.get("reason") or part.get("reason") or "bind_or_partition_failed",
        }
        return _finish(report)

    pre = conf1_precommit(
        machine_sha=str(gate.get("machine_sha")),
        source_inventory_sha=str(gate.get("source_inventory_sha")),
        complete_strategy_sha256=str(gate.get("COMPLETE_STRATEGY_SHA256")),
        confirmation_n=int(part.get("confirmation_n") or 0),
        confirmation_first=part.get("confirmation_first"),
        confirmation_last=part.get("confirmation_last"),
        lookback_n=int(part.get("lookback_n") or 0),
        symbol_n=len(list(bind.get("symbols") or [])),
    )
    (CACHE / "precommit.json").write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PRECOMMIT_SHA256 {pre.get('PRECOMMIT_SHA256')}", flush=True)
    print("ECONOMIC_OUTCOME_OPEN starting Old Confirmation Complete Strategy replay", flush=True)

    replay = walk_and_replay(bind=bind, part=part)
    report = _base(now=now, gate=gate, iso_before=iso_before)
    report["precommit"] = pre
    report["data_roles"] = {k: v for k, v in part.items() if k != "lookback_dates"}
    if not replay.get("ok"):
        report["replay"] = {k: v for k, v in replay.items() if k != "trades"}
        report["decision"] = {
            "VERDICT": CASE_INVALID,
            "NEXT": NEXT_STOP,
            "reason": replay.get("reason") or "replay_failed",
            "economic_verdict_issued": False,
        }
        return _finish(report)

    metrics = economic_metrics(replay=replay)
    inv = replay_invariants(replay=replay)
    decision = decide(invariants=inv, metrics=metrics)
    iso_after = snapshot(phase="POST")
    report.update(
        {
            "replay": {k: v for k, v in replay.items() if k not in {"trades", "blocked_rows"}},
            "trades": list(replay.get("trades") or []),
            "metrics": metrics,
            "invariants": inv,
            "decision": decision,
            "isolation_after": iso_after,
            "isolation_advanced": advanced(iso_before, iso_after),
            "out_dir": str(OUT).replace("\\", "/"),
        }
    )
    (CACHE / "replay_meta.json").write_text(
        json.dumps(
            {
                "e0_n": replay.get("e0_n"),
                "e1_n": replay.get("e1_n"),
                "trade_n": replay.get("trade_n"),
                "VERDICT": decision.get("VERDICT"),
                "ECONOMIC_EDGE_CONCENTRATED": decision.get("ECONOMIC_EDGE_CONCENTRATED"),
                "PRECOMMIT_SHA256": pre.get("PRECOMMIT_SHA256"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return _finish(report)


if __name__ == "__main__":
    raise SystemExit(main())
