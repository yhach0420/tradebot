"""Matched causal S/R first-interaction test. Frozen detector. Discovery only. No PnL. Runtime 0/0/0."""
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
from research.support_resistance_first_interaction_matched_causal_test_v1 import ANALYSIS_ID, PROGRAM_ID
from research.support_resistance_first_interaction_matched_causal_test_v1.analyze import build_answers, build_report_body
from research.support_resistance_first_interaction_matched_causal_test_v1.bind import bind_prior
from research.support_resistance_first_interaction_matched_causal_test_v1.isolation import (
    AUDIT_OUT,
    CACHE,
    FOUNDATION_OUT,
    OUT,
    REBUILD_OUT,
    ZONE_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.support_resistance_first_interaction_matched_causal_test_v1.publish import SHEET_ORDER, build_markdown, build_sheets, json_sanitize, write_artifacts
from research.support_resistance_first_interaction_matched_causal_test_v1.spec import source_sha256

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for p in sorted(root.iterdir()):
        if p.is_file():
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "KABU_50_APPLIED": False,
        "FROZEN_VALIDATION_OPENED": False,
        "OLD_CONFIRMATION_OPENED": False,
        "RCA_EXECUTED": False,
        "NEW_PAID_DATA": False,
        "PROMOTED": False,
        "V27_BOLTED": False,
        "NO_PNL_PARAMETER_TUNING": True,
        "DETECTOR_RETUNED": False,
        "X0_X1_USED_TO_SELECT_RULES": False,
        "FIVE_PP_CONTINUATION_SOLE_GATE": False,
        "IS_STRATEGY": False,
        "OLD_NO_INFO_REVIVED_AS_ECONOMIC_NULL": False,
        "SECONDARY_INFLUENCED_PRIMARY": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "parent_verdict": bind.get("parent_verdict"),
        "parent_primary_first_test_n": bind.get("parent_primary_first_test_n"),
        "research_pool_n": bind.get("research_pool_n"),
        "rca_deferred": bind.get("rca_deferred"),
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "rebuild_artifacts_preserved": bind.get("rebuild_artifacts_preserved"),
        "no_pnl_parameter_tuning": True,
        "detector_retuned": False,
        "split": {
            "split_sha256": split.get("split_sha256"),
            "discovery_n": len(list(split.get("discovery_dates") or [])),
            "discovery_first": (list(split.get("discovery_dates") or []) or [None])[0],
            "discovery_last": (list(split.get("discovery_dates") or []) or [None])[-1],
        },
        "blocks": {"ok": blocks.get("ok"), "block_sha256": blocks.get("block_sha256")},
        "symbol_n": len(list(bind.get("symbols") or [])),
        "freeze": bind.get("freeze"),
    }


def _write_pair_cache(pairs: list[dict[str, Any]]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "pairs.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in pairs:
            f.write(json.dumps(json_sanitize(r), ensure_ascii=False) + "\n")


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    assert_no_secret(sheets, where="sheets")
    write_artifacts(report, sheets)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 SR_MATCHED_CAUSAL NO_PNL FROZEN_VAL CLOSED CONFIRMATION CLOSED NOT_A_STRATEGY", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = {k: _fingerprint(p) for k, p in {"rebuild": REBUILD_OUT, "audit": AUDIT_OUT, "zone": ZONE_OUT, "foundation": FOUNDATION_OUT}.items()}
    bind = bind_prior()
    body = build_report_body(bind)
    pairs = list(body.pop("all_pairs", []) or [])
    if pairs:
        _write_pair_cache(pairs)
    after = snapshot(phase="POST")
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Do causally-known face-valid support/resistance zones change subsequent price behavior versus otherwise similar non-zone moves?",
            "NON_GOALS": [
                "PnL/X0/X1/PF/win-rate optimization",
                "open Confirmation or Frozen Validation",
                "retune the frozen detector",
                "mix role-flip candidates into the primary test",
                "treat 5pp continuation as the sole gate",
                "output a trading strategy",
                "revive MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1 as an economic null",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "DETECTOR_SHA256": ((bind.get("freeze") or {}).get("DETECTOR_SHA256")),
            "STATE_MACHINE_SHA256": ((bind.get("freeze") or {}).get("STATE_MACHINE_SHA256")),
            "REBUILD_SOURCE_SHA256": ((bind.get("freeze") or {}).get("REBUILD_SOURCE_SHA256")),
        },
        "bind": _slim_bind(bind),
        **body,
        "decision": dict(body.get("decision") or {}) | {"WRITE_OVERLAP_N": overlap},
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    _publish(report)
    for k, p in {"rebuild": REBUILD_OUT, "audit": AUDIT_OUT, "zone": ZONE_OUT, "foundation": FOUNDATION_OUT}.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
