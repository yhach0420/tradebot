"""A2/C1 complete causal strategy candidates. Discovery only. Runtime 0/0/0."""
from __future__ import annotations

import hashlib
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
from research.support_resistance_mechanism_to_complete_strategy_v1 import ANALYSIS_ID, PROGRAM_ID
from research.support_resistance_mechanism_to_complete_strategy_v1.analyze import build_answers, build_report_body
from research.support_resistance_mechanism_to_complete_strategy_v1.bind import bind_prior
from research.support_resistance_mechanism_to_complete_strategy_v1.isolation import (
    CACHE,
    FOUNDATION_OUT,
    MATCHED_OUT,
    OUT,
    PARENT_OUT,
    REBUILD_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.support_resistance_mechanism_to_complete_strategy_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.support_resistance_mechanism_to_complete_strategy_v1.spec import source_sha256

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
        "NEW_PAID_DATA": False,
        "PROMOTED": False,
        "NO_PNL_PARAMETER_TUNING": True,
        "DETECTOR_RETUNED": False,
        "MATCHABILITY_USED_AS_ENTRY_FILTER": False,
        "PNL_BASED_RULE_CHANGE": False,
        "SAME_BAR_ENTRY_N": 0,
        "TARGET_FUTURE_LEAKAGE_N": 0,
        "DELAYED_CAP_ENTRY_N": 0,
        "DELAYED_SAME_SYMBOL_ENTRY_N": 0,
        "EXIT_RETROACTIVE_N": 0,
        "B_D_PROMOTED": False,
        "A1_MERGED_WITH_A2": False,
        "EXTRA_ENTRY_FILTERS": False,
        "EXTRA_EXIT_SEARCH": False,
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
        "proven": bind.get("proven"),
        "not_proven": bind.get("not_proven"),
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "parent_artifacts_preserved": bind.get("parent_artifacts_preserved"),
        "detector_retuned": False,
        "evidence_A2": bind.get("evidence_A2"),
        "evidence_C1": bind.get("evidence_C1"),
        "c_holm_p": bind.get("c_holm_p"),
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
    print("SAFETY submit/cancel/live=0/0/0 SR_COMPLETE_STRATEGY FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = {k: _fingerprint(p) for k, p in {"parent": PARENT_OUT, "matched": MATCHED_OUT, "rebuild": REBUILD_OUT, "foundation": FOUNDATION_OUT}.items()}
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    body = build_report_body(bind)
    after = snapshot(phase="POST")
    safety = _safety()
    safety["SAME_BAR_ENTRY_N"] = int(body.get("same_bar_entry_n") or 0)
    safety["TARGET_FUTURE_LEAKAGE_N"] = int(body.get("target_future_leakage_n") or 0)
    safety["DELAYED_CAP_ENTRY_N"] = int(body.get("delayed_cap_entry_n") or 0)
    safety["DELAYED_SAME_SYMBOL_ENTRY_N"] = int(body.get("delayed_same_symbol_entry_n") or 0)
    safety["EXIT_RETROACTIVE_N"] = int(body.get("exit_retroactive_n") or 0)
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Do A2 and C1 remain complete causal strategy candidates after thesis-aligned EXIT and CAP occupancy?",
            "NON_GOALS": [
                "open Confirmation or Frozen Validation",
                "use matchability as an ENTRY filter",
                "retune detector / zone width / swing confirmation",
                "add time/symbol/sector/long-only filters after PnL",
                "mine EMA/VWAP/ATR/fixed-time exits",
                "promote A1 as primary strategy",
                "apply Kabu50",
                "claim actual Bid/Ask or short borrow",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "DETECTOR_SHA256": ((bind.get("freeze") or {}).get("DETECTOR_SHA256")),
            "STATE_MACHINE_SHA256": ((bind.get("freeze") or {}).get("STATE_MACHINE_SHA256")),
            "STRATEGY_SHA256": ((bind.get("freeze") or {}).get("STRATEGY_SHA256")),
        },
        "bind": _slim_bind(bind),
        **body,
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    _publish(report)
    for k, p in {"parent": PARENT_OUT, "matched": MATCHED_OUT, "rebuild": REBUILD_OUT, "foundation": FOUNDATION_OUT}.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    if int(report.get("same_bar_entry_n") or 0) != 0:
        raise RuntimeError("SAME_BAR_ENTRY_N_NOT_ZERO")
    if int(report.get("target_future_leakage_n") or 0) != 0:
        raise RuntimeError("TARGET_FUTURE_LEAKAGE_N_NOT_ZERO")
    if int(report.get("delayed_cap_entry_n") or 0) != 0:
        raise RuntimeError("DELAYED_CAP_ENTRY_N_NOT_ZERO")
    if int(report.get("delayed_same_symbol_entry_n") or 0) != 0:
        raise RuntimeError("DELAYED_SAME_SYMBOL_ENTRY_N_NOT_ZERO")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
