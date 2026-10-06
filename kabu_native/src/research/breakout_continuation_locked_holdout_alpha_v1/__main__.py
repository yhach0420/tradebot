"""Offline Breakout locked-holdout MID alpha. Freeze PRIMARY first. Stress sealed. No Runtime write."""
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
from research.breakout_continuation_locked_holdout_alpha_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEV_EXPECTED_MID_180,
    DEV_EXPECTED_MID_300,
    DEVELOPMENT_DAYS,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    PRIMARY_FAMILY,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
    VWAP_HOLDOUT_ALLOWED,
)
from research.breakout_continuation_locked_holdout_alpha_v1.analyze import (
    alpha_gates,
    build_answers,
    coverage_gates,
    decide,
    dev_parity,
    integrity_ok,
    leakage_n,
    pack_pop,
)
from research.breakout_continuation_locked_holdout_alpha_v1.harvest import (
    AUDIT,
    freeze_path,
    harvest_holdout,
    load_dev_breakout_rows,
)
from research.breakout_continuation_locked_holdout_alpha_v1.isolation import (
    CACHE,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.breakout_continuation_locked_holdout_alpha_v1.publish import build_markdown, kv_rows, write_artifacts
from research.breakout_continuation_locked_holdout_alpha_v1.spec import canonical_spec, rule_sha256, source_sha256, spec_sha256
from research.new_entry_breakout_continuation_v1.rule import EXACT_RULE_TEXT

JST = ZoneInfo("Asia/Tokyo")


def _slim(p: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in p.items() if k not in ("daily",)}


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = {
        "answers": kv_rows(report.get("answers") or {}),
        "precommit": kv_rows(report.get("precommit") or {}),
        "pin": kv_rows(report.get("pin") or {}),
        "development": kv_rows(_slim(report.get("development") or {})),
        "holdout": kv_rows(_slim(report.get("holdout") or {})),
        "holdout_daily": list((report.get("holdout") or {}).get("daily") or [{"empty": True}]),
        "unconditional": kv_rows(_slim(report.get("unconditional") or {})),
        "gates": kv_rows((report.get("alpha_gates") or {}).get("gates") or {}),
        "coverage": kv_rows(_slim(report.get("coverage") or {})),
        "leakage": kv_rows(report.get("leakage") or {}),
        "decision": kv_rows(report.get("decision") or {}),
        "safety": kv_rows(report.get("safety") or {}),
    }
    write_artifacts(report, sheets)


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "VWAP_HOLDOUT_OPENED": False,
        "EXECUTION_DESIGN": False,
        "EXIT_DESIGN": False,
        "FULL_CAUSAL": False,
        "SIZING": False,
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE BREAKOUT LOCKED HOLDOUT ALPHA V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("PRIMARY BREAKOUT frozen from DEV only. Stress sealed. VWAP holdout sealed.", flush=True)

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(before.get("ACTIVE_CAPTURE_PATH") or ""), str(before.get("ACTIVE_PAPER_SESSION") or ""))
    if overlap:
        AUDIT["SPLIT_LEAKAGE_N"] += 1

    out_rep = OUT / "report.json"
    if out_rep.is_file():
        prev = json.loads(out_rep.read_text(encoding="utf-8"))
        if str(prev.get("ANALYSIS_ID") or "") == ANALYSIS_ID:
            print("ALREADY_EXECUTED. Reusing OUT. No re-run.", flush=True)
            print(f"VERDICT {((prev.get('decision') or {}).get('VERDICT'))}", flush=True)
            print("STOP.", flush=True)
            return 0

    spec = canonical_spec()
    sha = spec_sha256(spec)
    src_sha = source_sha256()
    r_sha = rule_sha256()
    print(f"SPEC_SHA256 {sha}", flush=True)
    print(f"SOURCE_SHA256 {src_sha}", flush=True)
    print(f"RULE_SHA256 {r_sha}", flush=True)
    print(f"RULE {EXACT_RULE_TEXT}", flush=True)

    print("PHASE DEV REUSE - no recapture, no holdout", flush=True)
    try:
        dev_rows = load_dev_breakout_rows()
    except Exception as exc:
        decision = decide(integrity=False, coverage=None, alpha=None, hold=None)
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "precommit": spec,
            "pin": {"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha, "RULE_SHA256": r_sha},
            "leakage": leakage_n(),
            "decision": decision,
            "STOP_REASON": str(exc),
            "safety": _safety(),
            "isolation_before": before,
        }
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    dev = pack_pop(dev_rows, days=list(DEVELOPMENT_DAYS), label="BREAKOUT_DEV_REUSE", signal_n=len(dev_rows))
    parity = dev_parity(dev)
    print(
        f"DEV parity={parity} sig={dev.get('SIGNAL_N')} exe={dev.get('EVALUABLE_N')} "
        f"MID180={dev.get('MEAN_MID_180')} MID300={dev.get('MEAN_MID_300')}",
        flush=True,
    )
    integ = integrity_ok() and overlap == 0 and parity
    if not integ:
        decision = decide(integrity=False, coverage=None, alpha=None, hold=None)
        after = snapshot(phase="POST")
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "precommit": spec,
            "pin": {"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha, "RULE_SHA256": r_sha, "PRIMARY_SELECTION_FROZEN_BEFORE_HOLDOUT": False},
            "development": dev,
            "DEV_PARITY_OK": parity,
            "leakage": leakage_n(),
            "decision": decision,
            "safety": _safety(),
            "isolation_before": before,
            "isolation_after": after,
            "isolation_advanced": advanced(before, after),
        }
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 0

    freeze_ts = datetime.now(JST).isoformat(timespec="seconds")
    freeze_body = {
        "PRIMARY_FAMILY": PRIMARY_FAMILY,
        "PRIMARY_SELECTION_FROZEN_BEFORE_HOLDOUT": True,
        "ENTRY_RULE_FROZEN": True,
        "VWAP_HOLDOUT_ALLOWED": VWAP_HOLDOUT_ALLOWED,
        "FREEZE_TIMESTAMP": freeze_ts,
        "SPEC_SHA256": sha,
        "SOURCE_SHA256": src_sha,
        "RULE_SHA256": r_sha,
        "EXACT_RULE_TEXT": EXACT_RULE_TEXT,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "LOCKED_HOLDOUT_DAYS": list(LOCKED_HOLDOUT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "ANALYSIS_ID": ANALYSIS_ID,
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    freeze_path().write_text(json.dumps(freeze_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FREEZE {freeze_ts} PRIMARY={PRIMARY_FAMILY} spec={sha}", flush=True)
    print("PHASE LOCKED_HOLDOUT - first use after freeze. Stress not streamed.", flush=True)

    hold_h = harvest_holdout()
    if not hold_h.get("ok"):
        decision = decide(integrity=False, coverage=None, alpha=None, hold=None)
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "precommit": spec,
            "pin": freeze_body,
            "development": dev,
            "DEV_PARITY_OK": True,
            "HOLDOUT_OPENED": True,
            "leakage": leakage_n(),
            "decision": decision,
            "STOP_REASON": str(hold_h.get("blocker")),
            "safety": _safety(),
            "isolation_before": before,
        }
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    hold = pack_pop(list(hold_h.get("rows") or []), days=list(LOCKED_HOLDOUT_DAYS), label="BREAKOUT_HOLDOUT")
    unc = pack_pop(list(hold_h.get("unconditional") or []), days=list(LOCKED_HOLDOUT_DAYS), label="UNCONDITIONAL_HOLDOUT")
    cov = coverage_gates(hold, dict(hold_h.get("day_ok") or {}))
    ag = alpha_gates(hold, unc)
    ratio180 = (float(hold["MEAN_MID_180"]) / float(DEV_EXPECTED_MID_180)) if hold.get("MEAN_MID_180") is not None else None
    ratio300 = (float(hold["MEAN_MID_300"]) / float(DEV_EXPECTED_MID_300)) if hold.get("MEAN_MID_300") is not None else None
    print(
        f"HOLDOUT exe={hold.get('EVALUABLE_N')} MID180={hold.get('MEAN_MID_180')} MID300={hold.get('MEAN_MID_300')} "
        f"cov={cov.get('PASS')} alpha={ag.get('PASS')} tail={ag.get('TAIL_FRAGILE')}",
        flush=True,
    )
    integ2 = integrity_ok()
    decision = decide(integrity=integ2, coverage=cov, alpha=ag, hold=hold)
    after = snapshot(phase="POST")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PRIMARY_FAMILY": PRIMARY_FAMILY,
        "precommit": spec,
        "pin": freeze_body,
        "development": dev,
        "DEV_PARITY_OK": True,
        "holdout": hold,
        "unconditional": unc,
        "coverage": cov,
        "alpha_gates": ag,
        "RATIO180": ratio180,
        "RATIO300": ratio300,
        "HOLDOUT_OPENED": True,
        "STRESS_OPENED": False,
        "VWAP_HOLDOUT_OPENED": False,
        "leakage": leakage_n(),
        "decision": decision,
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
    }
    _publish(report)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
