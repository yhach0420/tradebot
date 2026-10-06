"""Offline FDR DEV MID alpha. Stress sealed. Burned holdout sealed. No Runtime write."""
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
from research.new_entry_failed_breakdown_reclaim_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
)
from research.new_entry_failed_breakdown_reclaim_v1.analyze import (
    alpha_gates,
    build_answers,
    coverage_gates,
    decide,
    integrity_ok,
    leakage_n,
    pack_pop,
)
from research.new_entry_failed_breakdown_reclaim_v1.harvest import AUDIT, freeze_path, harvest_development
from research.new_entry_failed_breakdown_reclaim_v1.isolation import (
    CACHE,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.new_entry_failed_breakdown_reclaim_v1.publish import build_markdown, kv_rows, write_artifacts
from research.new_entry_failed_breakdown_reclaim_v1.rule import EXACT_RULE_TEXT
from research.new_entry_failed_breakdown_reclaim_v1.spec import (
    canonical_spec,
    duplicate_architecture,
    rule_sha256,
    signal_set_sha256,
    source_sha256,
    spec_sha256,
)

JST = ZoneInfo("Asia/Tokyo")


def _slim(p: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in p.items() if k not in ("daily",)}


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "HOLDOUT_OPENED": False,
        "EXECUTION_DESIGN": False,
        "EXIT_DESIGN": False,
        "FULL_CAUSAL": False,
        "SIZING": False,
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = {
        "answers": kv_rows(report.get("answers") or {}),
        "precommit": kv_rows(report.get("precommit") or {}),
        "pin": kv_rows(report.get("pin") or {}),
        "duplicate": kv_rows(report.get("duplicate") or {}),
        "development": kv_rows(_slim(report.get("development") or {})),
        "development_daily": list((report.get("development") or {}).get("daily") or [{"empty": True}]),
        "unconditional": kv_rows(_slim(report.get("unconditional") or {})),
        "cluster": kv_rows(((report.get("development") or {}).get("cluster") or {})),
        "gates": kv_rows((report.get("alpha_gates") or {}).get("gates") or {}),
        "coverage": kv_rows(_slim(report.get("coverage") or {})),
        "leakage": kv_rows(report.get("leakage") or {}),
        "decision": kv_rows(report.get("decision") or {}),
        "safety": kv_rows(report.get("safety") or {}),
    }
    write_artifacts(report, sheets)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE FDR DEV ALPHA V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("DEV only. Burned holdout sealed. Stress sealed. No execution design.", flush=True)

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
    dup = duplicate_architecture()
    print(f"SPEC_SHA256 {sha}", flush=True)
    print(f"SOURCE_SHA256 {src_sha}", flush=True)
    print(f"RULE_SHA256 {r_sha}", flush=True)
    print(f"RULE {EXACT_RULE_TEXT}", flush=True)
    print(f"DUPLICATE_ARCHITECTURE {dup.get('DUPLICATE_ARCHITECTURE')}", flush=True)

    if bool(dup.get("DUPLICATE_ARCHITECTURE")):
        decision = decide(integrity=False, coverage=None, alpha=None, dev=None)
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "precommit": spec,
            "pin": {"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha, "RULE_SHA256": r_sha},
            "duplicate": dup,
            "leakage": leakage_n(),
            "decision": decision,
            "STOP_REASON": "DUPLICATE_ARCHITECTURE",
            "safety": _safety(),
            "isolation_before": before,
        }
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    print("PHASE DEVELOPMENT - Stress not streamed. Burned holdout not streamed.", flush=True)
    har = harvest_development()
    if not har.get("ok"):
        decision = decide(integrity=False, coverage=None, alpha=None, dev=None)
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "precommit": spec,
            "pin": {"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha, "RULE_SHA256": r_sha},
            "duplicate": dup,
            "leakage": leakage_n(),
            "decision": decision,
            "STOP_REASON": str(har.get("blocker")),
            "safety": _safety(),
            "isolation_before": before,
        }
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    dev = pack_pop(list(har.get("rows") or []), days=list(DEVELOPMENT_DAYS), label="FDR_DEV")
    unc = pack_pop(list(har.get("unconditional") or []), days=list(DEVELOPMENT_DAYS), label="UNCONDITIONAL_DEV")
    cov = coverage_gates(dev, dict(har.get("day_ok") or {}))
    ag = alpha_gates(dev, unc)
    print(
        f"DEV exe={dev.get('EVALUABLE_N')} MID180={dev.get('MEAN_MID_180')} MID300={dev.get('MEAN_MID_300')} "
        f"cov={cov.get('PASS')} alpha={ag.get('PASS')} tail={ag.get('TAIL_FRAGILE')}",
        flush=True,
    )
    integ = integrity_ok()
    decision = decide(integrity=integ, coverage=cov, alpha=ag, dev=dev)
    sig_sha = signal_set_sha256(list(har.get("rows") or []))
    pin = {
        "SPEC_SHA256": sha,
        "SOURCE_SHA256": src_sha,
        "RULE_SHA256": r_sha,
        "SIGNAL_SET_SHA256": sig_sha,
        "ENTRY_RULE_FROZEN": bool(decision.get("CASE") == "A"),
        "ENTRY_BASE_CANDIDATE_FROZEN": False,
        "EXACT_RULE_TEXT": EXACT_RULE_TEXT,
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(timespec="seconds") if decision.get("CASE") == "A" else None,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
    }
    if decision.get("CASE") == "A":
        CACHE.mkdir(parents=True, exist_ok=True)
        freeze_path().write_text(json.dumps(pin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"FREEZE {pin['FREEZE_TIMESTAMP']} spec={sha} signals={sig_sha}", flush=True)
    after = snapshot(phase="POST")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "FAMILY": "FAILED_BREAKDOWN_RECLAIM",
        "precommit": spec,
        "pin": pin,
        "duplicate": dup,
        "development": dev,
        "unconditional": unc,
        "coverage": cov,
        "alpha_gates": ag,
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
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
