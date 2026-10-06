"""Offline SIMPLE_TECH V26 joint coverage + technical EXIT RCA. V25 baseline frozen. No Capture control."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_entry_profit_improvement import (
    CANCEL_N,
    ELIGIBLE_DAYS,
    LIVE_ORDER_N,
    SESSION,
    SUBMIT_N,
)
from research.simple_tech_entry_family.harvest import sealed_day_caps
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.publish import kv_rows
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics, set_hash, signal_tuples
from research.simple_tech_redesign.isolation import (
    TODAY,
    V22_OUT,
    V25_OUT,
    V26_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v22_analyze import fill_tuples_e4
from research.simple_tech_redesign.v24_harvest import V22_CACHE, load_v22_day_rows
from research.simple_tech_redesign.v26_analyze import decide, summarize
from research.simple_tech_redesign.v26_harvest import (
    V26_CACHE,
    load_v26_day_cache,
    replay_joint_day,
    save_v26_day_cache,
)
from research.simple_tech_redesign.v26_publish import REQUIRED_KEYS, build_markdown, write_artifacts
from research.simple_tech_redesign.v26_spec import (
    ANALYSIS_ID,
    B1_SIGNAL_N_EXPECTED,
    CANONICAL_FRESHNESS_SEC,
    CORRECTED_FILL_HASH_EXPECTED,
    CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS,
    DEVELOPMENT_ENTRY_STACK,
    E4_CHANGED,
    E4_WAIT_BUDGET_SEC,
    ENTRY_CHANGED,
    ENTRY_SIGNAL_CHANGED,
    EXIT_POLICY_COMBINATION,
    FIXED180_PNL_USED,
    FRESHNESS_THRESHOLD_CHANGED,
    MARKET_ORDER,
    MIXED_TF_RULE,
    PARENT_SPEC_SHA256_EXPECTED,
    PNL_EVAL,
    RESEARCH_PARALLELISM,
    REPRICE_LOOP,
    RCI_LEVEL_THRESHOLD,
    RUNTIME_CANDIDATE,
    SECOND_FALLBACK,
    SIGNAL_SET_HASH_EXPECTED,
    SIZING_CHANGED,
    TIME_STOP_USED,
    TRUE_OOS,
    V25_SPEC_SHA256_EXPECTED,
    V25_VERDICT_EXPECTED,
    VOLUME_MULT_SEARCH,
    WAIT_EXTENSION,
    canonical_v26_spec,
    spec_sha256_v26,
)

INTEGRITY_ZERO = (
    "LIVE_PROCESS_CONTROL_CALL_N",
    "RUNTIME_WRITE_N",
    "CAPTURE_WRITE_N",
    "ADDITIONAL_WEBSOCKET_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "KABUS_RESTART_N",
    "CAPTURE_RESTART_N",
    "RUNTIME_RESTART_N",
    "VIRTUAL_FILL_N",
    "PNL_EVAL_N",
    "FIXED180_PNL_N",
    "FUTURE_BOARD_CARRYBACK_N",
    "FUTURE_TIMESTAMP_CARRYBACK_N",
    "FUTURE_QUOTE_CARRYBACK_N",
    "QUEUE_ASSUMPTION_N",
    "OPTIMISTIC_TOUCH_N",
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
    "ENTRY_RULE_CHANGE_N",
    "E4_CHANGE_N",
    "FRESHNESS_CHANGE_N",
    "THRESHOLD_SEARCH_N",
    "WAIT_EXTENSION_N",
    "REPRICE_N",
    "CHASE_N",
    "SECOND_FALLBACK_N",
    "FALLBACK_MARKET_N",
    "TIME_STOP_N",
    "EXIT_COMBINATION_N",
    "MIXED_TF_RULE_N",
    "RCI_LEVEL_THRESHOLD_N",
    "VOLUME_MULT_SEARCH_N",
    "V22_WRITE_N",
    "V25_WRITE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v26_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V26_INVALID",
            "TRUE_OOS": False,
            "ENTRY_SIGNAL_CHANGED": False,
            "NEXT": msg,
            "V25_BASELINE_PARITY": False,
            "JOINT_COVERAGE_EXIT_MECHANISM_FOUND": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V26_SPEC_SHA256": v26_sha,
        }
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "_markdown": build_markdown({"required": req, "decision": {"CASE": "E"}}),
    }
    write_artifacts(
        report,
        {"Precommit": kv_rows({"blocker": msg}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)},
    )
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v26_spec()
    v26_sha = spec_sha256_v26(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v26_joint={v26_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)
    if (
        ENTRY_CHANGED
        or ENTRY_SIGNAL_CHANGED
        or E4_CHANGED
        or FRESHNESS_THRESHOLD_CHANGED
        or EXIT_POLICY_COMBINATION
        or SIZING_CHANGED
        or PNL_EVAL
        or FIXED180_PNL_USED
        or TIME_STOP_USED
        or WAIT_EXTENSION
        or REPRICE_LOOP
        or SECOND_FALLBACK
        or MARKET_ORDER
        or RCI_LEVEL_THRESHOLD
        or VOLUME_MULT_SEARCH
        or MIXED_TF_RULE
        or CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS
        or bool(TRUE_OOS)
        or RUNTIME_CANDIDATE
        or abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) > 1e-12
        or abs(float(E4_WAIT_BUDGET_SEC) - 5.0) > 1e-12
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)

    v25_req = dict(_load(V25_OUT / "report.json").get("required") or {})
    if str(v25_req.get("VERDICT") or "") != V25_VERDICT_EXPECTED:
        return _stop("STOP. V25 official verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)
    if str(v25_req.get("V25_SPEC_SHA256") or "") != V25_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V25 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)
    if str(v25_req.get("CORRECTED_FILL_HASH") or "") != CORRECTED_FILL_HASH_EXPECTED:
        return _stop("STOP. V25 corrected fill hash freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps] + [str(V22_CACHE), str(V22_OUT / "report.json"), str(V25_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)

    v22_by_day: dict[str, list[dict[str, Any]]] = {}
    v22_n = 0
    for cap in caps:
        day = str(cap["date"])
        day_rows = load_v22_day_rows(day)
        if not day_rows:
            return _stop(f"STOP. V22 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)
        v22_by_day[day] = day_rows
        v22_n += len(day_rows)
    if v22_n != int(B1_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. V22 signal n={v22_n} expected {B1_SIGNAL_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v26_sha=v26_sha)

    def _absorb(body: dict[str, Any]) -> None:
        skip = {
            "ACTIVE_CAPTURE_INPUT_N",
            "RESEARCH_INPUT_ACTIVE_FILE_N",
            "LIVE_PROCESS_CONTROL_CALL_N",
            "RUNTIME_WRITE_N",
            "CAPTURE_WRITE_N",
            "ADDITIONAL_WEBSOCKET_N",
            "SUBMIT_N",
            "CANCEL_N",
            "LIVE_ORDER_N",
            "KABUS_RESTART_N",
            "CAPTURE_RESTART_N",
            "RUNTIME_RESTART_N",
            "V22_WRITE_N",
            "V25_WRITE_N",
        }
        for k, v in dict(body.get("leak") or {}).items():
            if not str(k).endswith("_N") or k in skip:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                leak[k] = int(leak.get(k) or 0) + int(v)

    rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_sigs = v22_by_day[day]
        cache_path = V26_CACHE / f"day_{day}.json"
        cached = load_v26_day_cache(cache_path, v26_sha)
        if cached and int(cached.get("signal_n") or 0) == len(day_sigs) and len(list(cached.get("rows") or [])) == len(day_sigs):
            print(f"{day} v26 joint cache hit rows={len(day_sigs)}", flush=True)
            rows.extend(list(cached.get("rows") or []))
            _absorb(cached)
            continue
        body = replay_joint_day(
            {
                "date": day,
                "capture_path": cap.get("capture_path"),
                "signals": day_sigs,
                "spec_sha": v26_sha,
            }
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. Joint replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v26_sha=v26_sha,
            )
        save_v26_day_cache(cache_path, body)
        rows.extend(list(body.get("rows") or []))
        _absorb(body)

    sig_hash = set_hash(signal_tuples(rows))
    signal_parity = sig_hash == SIGNAL_SET_HASH_EXPECTED and len(rows) == int(B1_SIGNAL_N_EXPECTED)
    core_hash_ok = set_hash(fill_tuples_e4(rows)) == CORRECTED_FILL_HASH_EXPECTED
    summary = summarize(rows)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED") and reporting.get("REPORTING_SEMANTICS_PASS"))
    future_n = (
        int(leak.get("FUTURE_BOARD_CARRYBACK_N") or 0)
        + int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0)
        + int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0)
    )
    decision = decide(
        summary,
        signal_parity=signal_parity,
        core_hash_ok=core_hash_ok,
        leak_ok=leak_ok,
        ni_ok=ni_ok,
        future_n=future_n,
    )
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "V25_BASELINE_PARITY": decision.get("V25_BASELINE_PARITY"),
        "SIGNAL_N": summary.get("SIGNAL_N"),
        "EXECUTION_EVALUABLE_N": summary.get("EXECUTION_EVALUABLE_N"),
        "CORE_E4_FILL_N": summary.get("CORE_E4_FILL_N"),
        "ASK_FALLBACK_ELIGIBLE_N": summary.get("ASK_FALLBACK_ELIGIBLE_N"),
        "ASK_FALLBACK_FILL_N": summary.get("ASK_FALLBACK_FILL_N"),
        "TOTAL_FILL_N": summary.get("TOTAL_FILL_N"),
        "ADDED_FILL_N": summary.get("ADDED_FILL_N"),
        "FILLS_PER_DAY": summary.get("FILLS_PER_DAY"),
        "CORE_PATH_SUMMARY": summary.get("CORE_PATH_SUMMARY"),
        "ADDED_PATH_SUMMARY": summary.get("ADDED_PATH_SUMMARY"),
        "1M_EXIT_PRIMITIVES": summary.get("1M_EXIT_PRIMITIVES"),
        "3M_EXIT_PRIMITIVES": summary.get("3M_EXIT_PRIMITIVES"),
        "5M_EXIT_PRIMITIVES": summary.get("5M_EXIT_PRIMITIVES"),
        "GOOD_CONTINUATION_N": summary.get("GOOD_CONTINUATION_N"),
        "EARLY_FAILURE_N": summary.get("EARLY_FAILURE_N"),
        "DIP_THEN_RECOVERY_N": summary.get("DIP_THEN_RECOVERY_N"),
        "PROFIT_THEN_FAILURE_N": summary.get("PROFIT_THEN_FAILURE_N"),
        "SUPPORTED_EXIT_PRIMITIVES": summary.get("SUPPORTED_EXIT_PRIMITIVES"),
        "PRIMARY_EXIT_PRIMITIVE": summary.get("PRIMARY_EXIT_PRIMITIVE"),
        "CORE_WINNER_PRESERVATION": decision.get("CORE_WINNER_PRESERVATION"),
        "JOINT_COVERAGE_EXIT_MECHANISM_FOUND": decision.get("JOINT_COVERAGE_EXIT_MECHANISM_FOUND"),
        "ENTRY_SIGNAL_CHANGED": False,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "SIGNAL_SET_HASH": sig_hash,
        "CORRECTED_FILL_HASH": summary.get("CORRECTED_FILL_HASH"),
        "V25_VERDICT_FROZEN": V25_VERDICT_EXPECTED,
        "V25_SPEC_SHA256": V25_SPEC_SHA256_EXPECTED,
        "V26_SPEC_SHA256": v26_sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
        "CANONICAL_FRESHNESS_SEC": float(CANONICAL_FRESHNESS_SEC),
        "FILLS_BY_DAY": summary.get("FILLS_BY_DAY"),
        "E4_NONFILL_N": summary.get("E4_NONFILL_N"),
        "Q1": decision.get("Q1_MATERIAL_COVERAGE"),
        "Q2": decision.get("Q2_ADDED_MFE_OPPORTUNITY"),
        "Q3": decision.get("Q3_FAILURE_IDENTIFIABLE"),
        "Q4": decision.get("Q4_CORE_WINNERS_PRESERVED"),
        "Q5": decision.get("Q5_JOINT_ARCHITECTURE_VALUE"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "summary": {k: v for k, v in summary.items() if k != "primitives"},
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    fill_sheet = []
    for r in rows:
        if not r.get("actual_filled"):
            continue
        p = dict(r.get("fill_path") or {})
        fill_sheet.append(
            {
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "t0": r.get("t0"),
                "fill_role": r.get("fill_role"),
                "fill_t": r.get("fill_t"),
                "fill_price": r.get("fill_price"),
                "path_type": r.get("path_type"),
                "mfe": p.get("mfe"),
                "mae": p.get("mae"),
                "bid_markout_60": p.get("bid_markout_60"),
                "bid_markout_180": p.get("bid_markout_180"),
                "bid_markout_300": p.get("bid_markout_300"),
                "bid_markout_600": p.get("bid_markout_600"),
            }
        )
    prim_sheet = []
    for tf_key in ("1M_EXIT_PRIMITIVES", "3M_EXIT_PRIMITIVES", "5M_EXIT_PRIMITIVES"):
        for pack in list(summary.get(tf_key) or []):
            row = dict(pack)
            row.pop("SUPPORT_GATES", None)
            prim_sheet.append(row)
    write_artifacts(
        report,
        {
            "Precommit": kv_rows(
                {
                    "ANALYSIS_ID": ANALYSIS_ID,
                    "V26_SPEC_SHA256": v26_sha,
                    "V25_VERDICT_FROZEN": V25_VERDICT_EXPECTED,
                    "COVERAGE_ARCHITECTURE": "E4_THEN_ASK_CROSS_W5",
                    "ENTRY_SIGNAL_CHANGED": False,
                    "E4_CHANGED": False,
                    "TIME_STOP_USED": False,
                    "PNL_EVAL": False,
                }
            ),
            "Identity": kv_rows(
                {
                    "V25_BASELINE_PARITY": decision.get("V25_BASELINE_PARITY"),
                    "SIGNAL_SET_HASH": sig_hash,
                    "CORRECTED_FILL_HASH": summary.get("CORRECTED_FILL_HASH"),
                    "CORE_HASH_OK": core_hash_ok,
                }
            ),
            "Coverage": kv_rows(
                {
                    "SIGNAL_N": summary.get("SIGNAL_N"),
                    "EXECUTION_EVALUABLE_N": summary.get("EXECUTION_EVALUABLE_N"),
                    "CORE_E4_FILL_N": summary.get("CORE_E4_FILL_N"),
                    "E4_NONFILL_N": summary.get("E4_NONFILL_N"),
                    "ASK_FALLBACK_ELIGIBLE_N": summary.get("ASK_FALLBACK_ELIGIBLE_N"),
                    "ASK_FALLBACK_FILL_N": summary.get("ASK_FALLBACK_FILL_N"),
                    "TOTAL_FILL_N": summary.get("TOTAL_FILL_N"),
                    "ADDED_FILL_N": summary.get("ADDED_FILL_N"),
                    "FILLS_PER_DAY": summary.get("FILLS_PER_DAY"),
                    "FILLS_BY_DAY": summary.get("FILLS_BY_DAY"),
                }
            ),
            "PathTypes": kv_rows(
                {
                    "CORE": summary.get("CORE_PATH_SUMMARY"),
                    "ADDED": summary.get("ADDED_PATH_SUMMARY"),
                    "GOOD_CONTINUATION_N": summary.get("GOOD_CONTINUATION_N"),
                    "EARLY_FAILURE_N": summary.get("EARLY_FAILURE_N"),
                    "DIP_THEN_RECOVERY_N": summary.get("DIP_THEN_RECOVERY_N"),
                    "PROFIT_THEN_FAILURE_N": summary.get("PROFIT_THEN_FAILURE_N"),
                }
            ),
            "ExitPrimitives": prim_sheet or [{"empty": True}],
            "Fills": fill_sheet or [{"empty": True}],
            "Daily": [{"date": d, "fill_n": n} for d, n in dict(summary.get("FILLS_BY_DAY") or {}).items()] or [{"empty": True}],
            "Decision": kv_rows(decision),
            "Reporting": kv_rows(reporting),
            "Integrity": kv_rows(leak),
            "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
        },
    )
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} SIGNAL_N={summary.get('SIGNAL_N')} "
        f"CORE={summary.get('CORE_E4_FILL_N')} ADDED={summary.get('ADDED_FILL_N')} "
        f"TOTAL={summary.get('TOTAL_FILL_N')} PRIMARY={decision.get('PRIMARY_EXIT_PRIMITIVE')} "
        f"VERDICT={decision.get('VERDICT')} ni={ni_ok} out={V26_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    ok = decision.get("CASE") in {"A", "B", "C", "D"} and ni_ok and signal_parity and core_hash_ok
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
