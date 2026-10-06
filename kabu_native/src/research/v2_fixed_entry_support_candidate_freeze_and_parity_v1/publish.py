"""Freeze the fixed-entry-support candidate and prove trade-by-trade parity."""
from __future__ import annotations

import json
import pickle
import subprocess
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook

from research.event_time_impulse_complete_strategy_v2.identity import sha256_obj
from research.event_time_impulse_fixed_entry_support_candidate_v1.identity import bind
from research.event_time_impulse_fixed_entry_support_candidate_v1.simulate import self_check
from research.v2_fixed_entry_support_candidate_freeze_and_parity_v1.scan import scan

OUT = Path("results/research/v2_fixed_entry_support_candidate_freeze_and_parity_v1")
EXPECT_N, EXPECT_PNL, EXPECT_PF = 11446, 2520850.0, 1.7935186351045076


def _pf(rows: list[dict[str, Any]]) -> Optional[float]:
    loss = sum(-float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) < 0)
    gain = sum(float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) > 0)
    return gain / loss if loss else None


def _uid(row: dict[str, Any]) -> str:
    return f"{row['date']}|{row['session']}|{row['symbol']}|{int(row['signal_index'])}"


def _close(left: float, right: float, tol: float) -> bool:
    return abs(float(left) - float(right)) <= tol


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = float(sum(float(r["pnl_yen"]) for r in rows))
    return {"trade_n": len(rows), "pnl_yen": pnl, "pf": _pf(rows)}


def _compare(research: list[dict[str, Any]], impl: list[dict[str, Any]]) -> dict[str, Any]:
    left = {_uid(row): row for row in research}
    right = {_uid(row): row for row in impl}
    counts = {
        "missing": 0, "extra": 0, "entry_timestamp": 0, "entry_price": 0,
        "exit_timestamp": 0, "exit_price": 0, "exit_reason": 0, "pnl": 0,
        "support": 0, "reconfirm": 0, "occupancy": 0, "cap": 0,
        "same_symbol": 0, "reentry": 0, "session_close": 0,
    }
    first = None
    order = sorted(set(left) | set(right), key=lambda uid: _sort_key(uid, left, right))
    for uid in order:
        ref = left.get(uid)
        got = right.get(uid)
        if ref is None or got is None:
            kind = "missing" if got is None else "extra"
            counts[kind] += 1
            first = first or _divergence(uid, ref, got, kind)
            continue
        problems = []
        if not _close(ref["entry_t"], got["entry_t"], 1e-9):
            counts["entry_timestamp"] += 1
            problems.append("entry_timestamp")
        if not _close(ref["entry_px"], got["entry_px"], 1e-6):
            counts["entry_price"] += 1
            problems.append("entry_price")
        if not _close(ref["exit_t"], got["exit_t"], 1e-6):
            counts["exit_timestamp"] += 1
            problems.append("exit_timestamp")
        if not _close(ref["exit_px"], got["exit_px"], 1e-4):
            counts["exit_price"] += 1
            problems.append("exit_price")
        if str(ref["reason"]) != str(got["reason"]):
            counts["exit_reason"] += 1
            problems.append("exit_reason")
            if "SESSION_FLAT" in (str(ref["reason"]), str(got["reason"])):
                counts["session_close"] += 1
        if not _close(ref["pnl_yen"], got["pnl_yen"], 1e-6):
            counts["pnl"] += 1
            problems.append("pnl")
        supports = (float(ref["initial_support"]), float(ref["final_support"]), float(got["initial_support"]), float(got["final_support"]))
        if any(abs(value - supports[0]) > 1e-6 for value in supports):
            counts["support"] += 1
            problems.append("support")
        if int(ref["reconfirm_raises"]) != int(got["reconfirm_raises"]):
            counts["reconfirm"] += 1
            problems.append("reconfirm")
        if bool(ref["reentry"]) != bool(got["reentry"]):
            counts["reentry"] += 1
            problems.append("reentry")
        if problems and first is None:
            first = _divergence(uid, ref, got, problems[0])
    matched = len(set(left) & set(right))
    exact = all(value == 0 for value in counts.values()) and matched == len(research) == len(impl)
    return {"counts": counts, "exact_matched_trades": matched, "trade_by_trade_parity": exact, "first_divergence": first or "none"}


def _sort_key(uid: str, left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]) -> tuple:
    row = left.get(uid) or right[uid]
    return (str(row["date"]), str(row["session"]), float(row["entry_t"]), str(row["symbol"]), int(row["signal_index"]))


def _divergence(uid: str, ref: Optional[dict[str, Any]], got: Optional[dict[str, Any]], kind: str) -> dict[str, Any]:
    row = ref or got or {}
    return {
        "kind": kind,
        "signal_uid": uid,
        "timestamp": row.get("entry_t"),
        "symbol": row.get("symbol"),
        "research_state": None if ref is None else {k: ref[k] for k in ("reason", "entry_t", "exit_t", "entry_px", "exit_px", "pnl_yen", "initial_support", "final_support", "reconfirm_raises")},
        "implementation_state": None if got is None else {k: got[k] for k in ("reason", "entry_t", "exit_t", "entry_px", "exit_px", "pnl_yen", "initial_support", "final_support", "reconfirm_raises")},
    }


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unavailable"


def publish() -> dict[str, Any]:
    self_check()
    identity = bind()
    cache = OUT / "_scan.pkl"
    payload = pickle.loads(cache.read_bytes()) if cache.exists() else scan()
    if not cache.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(payload))
    research, impl = payload["research"], payload["implementation"]
    research_stats, impl_stats = _stats(research), _stats(impl)
    research_ok = research_stats["trade_n"] == EXPECT_N and abs(research_stats["pnl_yen"] - EXPECT_PNL) < 1e-6 and research_stats["pf"] is not None and abs(research_stats["pf"] - EXPECT_PF) < 1e-12
    impl_ok = impl_stats["trade_n"] == EXPECT_N and abs(impl_stats["pnl_yen"] - EXPECT_PNL) < 1e-6 and impl_stats["pf"] is not None and abs(impl_stats["pf"] - EXPECT_PF) < 1e-12
    compared = _compare(research, impl)
    support_ok = all(abs(float(r["initial_support"]) - float(r["final_support"])) <= 1e-6 for r in impl)
    parity = bool(research_ok and impl_ok and compared["trade_by_trade_parity"] and support_ok)
    verdict = "V2_FIXED_ENTRY_SUPPORT_IMPLEMENTATION_PARITY_PASS_V1" if parity else "V2_FIXED_ENTRY_SUPPORT_IMPLEMENTATION_PARITY_FAIL_V1"
    nxt = "PROMOTE_FIXED_ENTRY_SUPPORT_AS_NEXT_STRATEGY_CANDIDATE_V1" if parity else "REPAIR_IMPLEMENTATION_PARITY_ONLY"
    report = {
        "study": "V2_FIXED_ENTRY_SUPPORT_CANDIDATE_FREEZE_AND_PARITY_V1",
        "verdict": verdict,
        "next": nxt,
        "frozen_v2_status": "PREVIOUS_BASELINE",
        "candidate_status": "CURRENT_RESEARCH_SELECTED_CANDIDATE" if parity else "NOT_PROMOTED",
        "validation_status": "EXISTING_DATA_VALIDATED" if parity else "PARITY_FAILED",
        "implementation_status": "IMPLEMENTATION_PARITY_PROVEN" if parity else "IMPLEMENTATION_PARITY_NOT_PROVEN",
        "prospectively_validated": False,
        "candidate_id": identity["strategy"]["COMPLETE_STRATEGY_ID"],
        "entry_sha256": identity["entry_sha256"],
        "exit_sha256": identity["exit_sha256"],
        "complete_strategy_sha256": identity["complete_strategy_sha256"],
        "manifest_sha256": identity["manifest_sha256"],
        "source_tree_sha256": identity["manifest"]["source_tree_sha256"],
        "git_head": _git_head(),
        "known_limitation": "FIXED_SUPPORT_IMPROVEMENT_SYMBOL_CONCENTRATED",
        "known_limitation_recorded": True,
        "top10_whitelist_introduced": False,
        "frozen_v2_modified": False,
        "new_strategy_optimization": False,
        "new_data_acquired": False,
        "paper_executed": False,
        "submit_cancel_live": [0, 0, 0],
        "universe": {
            "research": payload["universe_id"],
            "implementation": payload["universe_id"],
            "symbol_n": payload["universe_n"],
            "dynamic40_applied": False,
            "not_registered_filter": False,
            "difference": None,
        },
        "research": research_stats,
        "implementation": impl_stats,
        "research_reproduction": bool(research_ok),
        "implementation_reproduction": bool(impl_ok),
        "active_support_invariant": bool(support_ok),
        **compared,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    wb = Workbook()
    wb.active.title = "summary"
    wb.active.append(["verdict", verdict, nxt])
    wb.active.append(["candidate", report["candidate_id"]])
    wb.active.append(["complete_strategy_sha256", report["complete_strategy_sha256"]])
    ws = wb.create_sheet("mismatches")
    ws.append(["field", "count"])
    for key, value in compared["counts"].items():
        ws.append([key, value])
    wb.save(OUT / "audit.xlsx")
    cache.unlink(missing_ok=True)
    print(f"VERDICT {verdict} NEXT {nxt} parity={parity}", flush=True)
    return report


def _markdown(report: dict[str, Any]) -> str:
    r, i = report["research"], report["implementation"]
    return "\n".join([
        f"# {report['study']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "Frozen V2 remains the previous baseline and was not modified.",
        f"Candidate status: {report['candidate_status']}. {report['validation_status']}. {report['implementation_status']}.",
        "This is not prospective validation.",
        "",
        f"Candidate {report['candidate_id']}",
        f"ENTRY_SHA {report['entry_sha256']}",
        f"EXIT_SHA {report['exit_sha256']}",
        f"COMPLETE_STRATEGY_SHA {report['complete_strategy_sha256']}",
        f"manifest SHA {report['manifest_sha256']}",
        "",
        f"Research {r['trade_n']} / {r['pnl_yen']} / {r['pf']}",
        f"Implementation {i['trade_n']} / {i['pnl_yen']} / {i['pf']}",
        f"Trade-by-trade parity {report['trade_by_trade_parity']}",
        f"Mismatches {json.dumps(report['counts'])}",
        f"First divergence {report['first_divergence']}",
        "",
        "Known limitation FIXED_SUPPORT_IMPROVEMENT_SYMBOL_CONCENTRATED is recorded. No symbol whitelist was added.",
        "Universe is the 105-symbol research observation set on both paths. Dynamic40 is not applied.",
        "submit/cancel/live = 0/0/0. Paper was not executed.",
        "",
    ])


if __name__ == "__main__":
    publish()
