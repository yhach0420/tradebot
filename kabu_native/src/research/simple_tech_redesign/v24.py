"""Offline SIMPLE_TECH V24 board freshness semantics correction + frozen E4 replay. No Capture control."""
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
    V23_OUT,
    V24_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v24_analyze import decide, diagnose_parity_breaks, parity, summarize
from research.simple_tech_redesign.v24_harvest import (
    V22_CACHE,
    V24_CACHE,
    load_v22_day_rows,
    load_v24_day_cache,
    replay_corrected_day,
    save_v24_day_cache,
)
from research.simple_tech_redesign.v24_publish import REQUIRED_KEYS, build_markdown, write_artifacts
from research.simple_tech_redesign.v24_spec import (
    ANALYSIS_ID,
    B1_EXECUTABLE_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    CANONICAL_FRESHNESS_SEC,
    CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS,
    DEVELOPMENT_ENTRY_STACK,
    E4_CHANGED,
    E4_FILL_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    E4_UNFILLED_N_EXPECTED,
    E4_WAIT_BUDGET_SEC,
    ELIGIBLE_SET_HASH_EXPECTED,
    ENTRY_CHANGED,
    EXIT_CHANGED,
    FILL_REPLAY_ON_FUTURE_QUOTE,
    FIXED180_PNL_USED,
    FRESHNESS_THRESHOLD_CHANGED,
    FUTURE_QUOTE_CARRY_BACK,
    FUTURE_TIMESTAMP_CARRY_BACK,
    HORIZON_SELECTION,
    PARENT_SPEC_SHA256_EXPECTED,
    PNL_EVAL,
    RESEARCH_PARALLELISM,
    RUNTIME_CANDIDATE,
    SIGNAL_SET_HASH_EXPECTED,
    SIZING_CHANGED,
    STALE_N_EXPECTED,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V13_SPEC_SHA256_EXPECTED,
    V22_SPEC_SHA256_EXPECTED,
    V22_VERDICT_EXPECTED,
    V23_SPEC_SHA256_EXPECTED,
    V23_VERDICT_EXPECTED,
    VIRTUAL_FILL,
    canonical_v24_spec,
    spec_sha256_v24,
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
    "FILL_REPLAY_ON_FUTURE_QUOTE_N",
    "PNL_EVAL_N",
    "FIXED180_PNL_N",
    "THRESHOLD_SEARCH_N",
    "FRESHNESS_CHANGE_N",
    "ENTRY_RULE_CHANGE_N",
    "E4_CHANGE_N",
    "FUTURE_QUOTE_CARRY_BACK_N",
    "FUTURE_BOARD_CARRYBACK_N",
    "FUTURE_TIMESTAMP_CARRYBACK_N",
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
    "EXIT_SIM_N",
    "REPRICE_N",
    "CHASE_N",
    "FALLBACK_MARKET_N",
    "OPTIMISTIC_FILL_N",
    "TOUCH_FILL_N",
    "QUEUE_FILL_N",
    "V22_WRITE_N",
    "V23_WRITE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v24_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V24_BOARD_CLOCK_UNRESOLVED",
            "TRUE_OOS": False,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "FRESHNESS_THRESHOLD_CHANGED": False,
            "EXIT_CHANGED": False,
            "SIZING_CHANGED": False,
            "NEXT": msg,
            "PARENT_SPEC_SHA256": parent_sha,
            "V24_SPEC_SHA256": v24_sha,
            "STRATEGY_STACK_PARITY": False,
            "NON_INTERFERENCE_PASS": False,
        }
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "_markdown": build_markdown({"required": req, "decision": {"CASE": "D"}}),
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
    spec = canonical_v24_spec()
    v24_sha = spec_sha256_v24(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v24_corrected={v24_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if (
        ENTRY_CHANGED
        or E4_CHANGED
        or FRESHNESS_THRESHOLD_CHANGED
        or EXIT_CHANGED
        or SIZING_CHANGED
        or THRESHOLD_SEARCH
        or VIRTUAL_FILL
        or FILL_REPLAY_ON_FUTURE_QUOTE
        or PNL_EVAL
        or FIXED180_PNL_USED
        or HORIZON_SELECTION
        or FUTURE_QUOTE_CARRY_BACK
        or FUTURE_TIMESTAMP_CARRY_BACK
        or CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS
        or bool(TRUE_OOS)
        or RUNTIME_CANDIDATE
        or abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) > 1e-12
        or abs(float(E4_WAIT_BUDGET_SEC) - 5.0) > 1e-12
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)

    v22_req = dict(_load(V22_OUT / "report.json").get("required") or {})
    v23_req = dict(_load(V23_OUT / "report.json").get("required") or {})
    if str(v22_req.get("VERDICT") or "") != V22_VERDICT_EXPECTED:
        return _stop("STOP. V22 verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if str(v22_req.get("V22_SPEC_SHA256") or "") != V22_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V22 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if str(v23_req.get("VERDICT") or "") != V23_VERDICT_EXPECTED:
        return _stop("STOP. V23 verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if str(v23_req.get("V23_SPEC_SHA256") or "") != V23_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V23 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if int(v22_req.get("SIGNAL_N") or 0) != int(B1_SIGNAL_N_EXPECTED):
        return _stop("STOP. V22 SIGNAL_N freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if int(v22_req.get("EXECUTION_EVALUABLE_N") or 0) != int(B1_EXECUTABLE_N_EXPECTED):
        return _stop("STOP. V22 evaluable freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if int(v22_req.get("E4_FILLED_N") or 0) != int(E4_FILLED_N_EXPECTED):
        return _stop("STOP. V22 fill freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if int(v22_req.get("E4_NONFILLED_N") or 0) != int(E4_UNFILLED_N_EXPECTED):
        return _stop("STOP. V22 nonfill freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    if int(v22_req.get("EXECUTION_UNEVALUABLE_N") or 0) != int(STALE_N_EXPECTED):
        return _stop("STOP. V22 stale N freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [str(V22_CACHE), str(V22_OUT / "report.json"), str(V23_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)

    v22_rows: list[dict[str, Any]] = []
    v22_by_day: dict[str, list[dict[str, Any]]] = {}
    for cap in caps:
        day = str(cap["date"])
        day_rows = load_v22_day_rows(day)
        if not day_rows:
            return _stop(f"STOP. V22 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v24_sha=v24_sha)
        v22_by_day[day] = day_rows
        v22_rows.extend(day_rows)
    if len(v22_rows) != int(B1_SIGNAL_N_EXPECTED):
        return _stop(
            f"STOP. V22 signal n={len(v22_rows)} expected {B1_SIGNAL_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v24_sha=v24_sha,
        )

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
            "V23_WRITE_N",
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
        cache_path = V24_CACHE / f"day_{day}.json"
        cached = load_v24_day_cache(cache_path, v24_sha)
        if cached and int(cached.get("signal_n") or 0) == len(day_sigs) and len(list(cached.get("rows") or [])) == len(day_sigs):
            print(f"{day} v24 corrected cache hit rows={len(day_sigs)}", flush=True)
            rows.extend(list(cached.get("rows") or []))
            _absorb(cached)
            continue
        body = replay_corrected_day(
            {
                "date": day,
                "capture_path": cap.get("capture_path"),
                "signals": day_sigs,
                "spec_sha": v24_sha,
            }
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. Corrected replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v24_sha=v24_sha,
            )
        save_v24_day_cache(cache_path, body)
        rows.extend(list(body.get("rows") or []))
        _absorb(body)

    sig_hash = set_hash(signal_tuples(rows))
    signal_parity = sig_hash == SIGNAL_SET_HASH_EXPECTED and len(rows) == int(B1_SIGNAL_N_EXPECTED)
    summary = summarize(rows)
    par = parity(rows, summary)
    diag = diagnose_parity_breaks(rows)
    identity_ok = bool(signal_parity and par.get("SIGNAL_N_OK") and par.get("FORMER_STALE_N_OK"))
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    decision = decide(
        summary,
        par,
        leak_ok=leak_ok,
        identity_ok=identity_ok,
        future_board_n=int(leak.get("FUTURE_BOARD_CARRYBACK_N") or 0),
        future_ts_n=int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0),
        cpt_as_board_n=int(leak.get("CURRENT_PRICE_TIME_AS_BOARD_FRESH_N") or 0),
    )
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS"))
    stack_parity = bool(signal_parity and par.get("LEGACY_EVALUABLE_PARITY") and par.get("LEGACY_E4_FILL_PARITY") and par.get("LEGACY_E4_NONFILL_PARITY"))
    if not integ_ok:
        decision["CASE"] = "D"
        decision["VERDICT"] = "SIMPLE_TECH_V24_BOARD_CLOCK_UNRESOLVED"
        decision["NEXT"] = "STOP. Identity or non-interference failed."

    slim_summary = {k: v for k, v in summary.items() if k not in {"legacy_eval_rows", "legacy_fill_v22", "legacy_nonfill_v22", "new_fill_rows", "recovered_rows"}}
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "STRATEGY_STACK_PARITY": bool(stack_parity),
        "SIGNAL_N": slim_summary.get("SIGNAL_N"),
        "BOARD_FRESHNESS_CLOCK_SOURCE_COUNTS": slim_summary.get("BOARD_FRESHNESS_CLOCK_SOURCE_COUNTS"),
        "LEGACY_EVALUABLE_PARITY": par.get("LEGACY_EVALUABLE_PARITY"),
        "LEGACY_E4_FILL_PARITY": par.get("LEGACY_E4_FILL_PARITY"),
        "LEGACY_E4_NONFILL_PARITY": par.get("LEGACY_E4_NONFILL_PARITY"),
        "CORRECTED_EXECUTION_EVALUABLE_N": slim_summary.get("CORRECTED_EXECUTION_EVALUABLE_N"),
        "CORRECTED_EXECUTION_UNEVALUABLE_N": slim_summary.get("CORRECTED_EXECUTION_UNEVALUABLE_N"),
        "LEGACY_EVALUABLE_N": slim_summary.get("LEGACY_EVALUABLE_N"),
        "RECOVERED_FROM_STALE_N": slim_summary.get("RECOVERED_FROM_STALE_N"),
        "CORRECTED_E4_FILLED_N": slim_summary.get("CORRECTED_E4_FILLED_N"),
        "CORRECTED_E4_NONFILLED_N": slim_summary.get("CORRECTED_E4_NONFILLED_N"),
        "NEW_FILL_FROM_FORMER_STALE_N": slim_summary.get("NEW_FILL_FROM_FORMER_STALE_N"),
        "NEW_NONFILL_FROM_FORMER_STALE_N": slim_summary.get("NEW_NONFILL_FROM_FORMER_STALE_N"),
        "NEW_FILL_DAY_N": slim_summary.get("NEW_FILL_DAY_N"),
        "NEW_FILL_SYMBOL_N": slim_summary.get("NEW_FILL_SYMBOL_N"),
        "FILLS_PER_DAY": slim_summary.get("FILLS_PER_DAY"),
        "NEW_FILLS_PER_DAY": slim_summary.get("NEW_FILLS_PER_DAY"),
        "FUTURE_BOARD_CARRYBACK_N": int(leak.get("FUTURE_BOARD_CARRYBACK_N") or 0),
        "FUTURE_TIMESTAMP_CARRYBACK_N": int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0),
        "ENTRY_CHANGED": False,
        "E4_CHANGED": False,
        "FRESHNESS_THRESHOLD_CHANGED": False,
        "EXIT_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "SIGNAL_SET_HASH": sig_hash,
        "ELIGIBLE_SET_HASH": ELIGIBLE_SET_HASH_EXPECTED,
        "E4_FILL_SET_HASH_LEGACY": E4_FILL_SET_HASH_EXPECTED,
        "E4_FILL_SET_HASH_CORRECTED": par.get("corrected_fill_hash"),
        "V22_VERDICT": V22_VERDICT_EXPECTED,
        "V23_VERDICT": V23_VERDICT_EXPECTED,
        "V22_SPEC_SHA256": V22_SPEC_SHA256_EXPECTED,
        "V23_SPEC_SHA256": V23_SPEC_SHA256_EXPECTED,
        "V24_SPEC_SHA256": v24_sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
        "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
        "CANONICAL_FRESHNESS_SEC": float(CANONICAL_FRESHNESS_SEC),
        "IDENTITY_OK": bool(identity_ok),
        "RECOVERED_DAY_N": slim_summary.get("RECOVERED_DAY_N"),
        "RECOVERED_SYMBOL_N": slim_summary.get("RECOVERED_SYMBOL_N"),
        "UNEVAL_SPLIT": slim_summary.get("UNEVAL_SPLIT"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "summary": slim_summary,
        "parity": par,
        "diagnosis": diag,
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    recovered_sheet = [
        {
            "date": r.get("date"),
            "symbol": r.get("symbol"),
            "t0": r.get("t0"),
            "former_stale": r.get("former_stale"),
            "executable_signal": r.get("executable_signal"),
            "e4_filled": r.get("e4_filled"),
            "BOARD_FRESHNESS_CLOCK_SOURCE": r.get("BOARD_FRESHNESS_CLOCK_SOURCE"),
            "board_fresh_sec": r.get("board_fresh_sec"),
            "PRICE_FRESHNESS_SEC": r.get("PRICE_FRESHNESS_SEC"),
            "ask_reason": r.get("ask_reason"),
            "funnel_reason": r.get("funnel_reason"),
            "limit_price": dict(r.get("e4") or {}).get("limit_price"),
            "fill_t": dict(r.get("e4") or {}).get("fill_t"),
            "fill_price": dict(r.get("e4") or {}).get("fill_price"),
            "v22_cohort": r.get("v22_cohort"),
        }
        for r in rows
        if r.get("former_stale")
    ]
    new_fill_sheet = [r for r in recovered_sheet if r.get("e4_filled")]
    write_artifacts(
        report,
        {
            "Precommit": kv_rows(
                {
                    "ANALYSIS_ID": ANALYSIS_ID,
                    "V24_SPEC_SHA256": v24_sha,
                    "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
                    "ENTRY_CHANGED": False,
                    "E4_CHANGED": False,
                    "FRESHNESS_THRESHOLD_CHANGED": False,
                    "CANONICAL_FRESHNESS_SEC": float(CANONICAL_FRESHNESS_SEC),
                    "CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS": False,
                }
            ),
            "Identity": kv_rows(
                {
                    "STRATEGY_STACK_PARITY": stack_parity,
                    "SIGNAL_SET_HASH": sig_hash,
                    "LEGACY_EVALUABLE_PARITY": par.get("LEGACY_EVALUABLE_PARITY"),
                    "LEGACY_E4_FILL_PARITY": par.get("LEGACY_E4_FILL_PARITY"),
                    "LEGACY_E4_NONFILL_PARITY": par.get("LEGACY_E4_NONFILL_PARITY"),
                }
            ),
            "Funnel": kv_rows(slim_summary),
            "LegacyParity": kv_rows(par),
            "RecoveredStale": recovered_sheet or [{"empty": True}],
            "NewFills": new_fill_sheet or [{"empty": True}],
            "ClockSources": kv_rows(
                {
                    "BOARD_FRESHNESS_CLOCK_SOURCE_COUNTS": slim_summary.get("BOARD_FRESHNESS_CLOCK_SOURCE_COUNTS"),
                    "FORMER_STALE_CLOCK_SOURCE_COUNTS": slim_summary.get("FORMER_STALE_CLOCK_SOURCE_COUNTS"),
                }
            ),
            "Diagnosis": kv_rows(diag),
            "Decision": kv_rows(decision),
            "Reporting": kv_rows(reporting),
            "Integrity": kv_rows(leak),
            "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
        },
    )
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} SIGNAL_N={slim_summary.get('SIGNAL_N')} "
        f"EVAL={slim_summary.get('CORRECTED_EXECUTION_EVALUABLE_N')} "
        f"FILLED={slim_summary.get('CORRECTED_E4_FILLED_N')} "
        f"RECOVERED={slim_summary.get('RECOVERED_FROM_STALE_N')} "
        f"NEW_FILL={slim_summary.get('NEW_FILL_FROM_FORMER_STALE_N')} "
        f"LEGACY_EVAL={par.get('LEGACY_EVALUABLE_PARITY')} "
        f"LEGACY_FILL={par.get('LEGACY_E4_FILL_PARITY')} "
        f"VERDICT={decision.get('VERDICT')} ni={ni_ok} out={V24_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if ni_ok and identity_ok and par.get("LEGACY_EVALUABLE_PARITY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
