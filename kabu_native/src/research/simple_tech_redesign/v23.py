"""Offline SIMPLE_TECH V23 stale execution coverage RCA. Frozen ENTRY/E4/freshness. No Capture control."""
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
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.isolation import (
    TODAY,
    V22_OUT,
    V23_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v23_analyze import decide, summarize
from research.simple_tech_redesign.v23_harvest import (
    V22_CACHE,
    V23_CACHE,
    load_v22_stale_signals,
    load_v23_day_cache,
    replay_stale_clocks_day,
    save_v23_day_cache,
)
from research.simple_tech_redesign.v23_publish import REQUIRED_KEYS, build_markdown, write_artifacts
from research.simple_tech_redesign.v23_spec import (
    ANALYSIS_ID,
    B1_EXECUTABLE_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    CANONICAL_FRESHNESS_SEC,
    DEVELOPMENT_ENTRY_STACK,
    E4_CHANGED,
    E4_FILL_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    E4_UNFILLED_N_EXPECTED,
    ELIGIBLE_SET_HASH_EXPECTED,
    ENTRY_CHANGED,
    EXIT_CHANGED,
    FILL_REPLAY,
    FRESHNESS_THRESHOLD_CHANGED,
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
    V22_PRIMARY_EXPECTED,
    V22_SPEC_SHA256_EXPECTED,
    V22_VERDICT_EXPECTED,
    VIRTUAL_FILL,
    canonical_v23_spec,
    spec_sha256_v23,
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
    "FILL_REPLAY_N",
    "PNL_EVAL_N",
    "THRESHOLD_SEARCH_N",
    "FRESHNESS_CHANGE_N",
    "ENTRY_RULE_CHANGE_N",
    "E4_CHANGE_N",
    "FUTURE_QUOTE_CARRY_BACK_N",
    "FUTURE_RECEIVED_CARRY_BACK_N",
    "EXIT_SIM_N",
    "V22_WRITE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v23_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V23_STALE_CAUSE_UNRESOLVED",
            "PRIMARY_STALE_CAUSE": "UNRESOLVED",
            "TRUE_OOS": False,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "NEXT": msg,
            "PARENT_SPEC_SHA256": parent_sha,
            "V23_SPEC_SHA256": v23_sha,
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
    spec = canonical_v23_spec()
    v23_sha = spec_sha256_v23(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v23_stale={v23_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if (
        ENTRY_CHANGED
        or E4_CHANGED
        or FRESHNESS_THRESHOLD_CHANGED
        or EXIT_CHANGED
        or SIZING_CHANGED
        or THRESHOLD_SEARCH
        or VIRTUAL_FILL
        or FILL_REPLAY
        or PNL_EVAL
        or bool(TRUE_OOS)
        or RUNTIME_CANDIDATE
        or abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) > 1e-12
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)

    v22_rep = _load(V22_OUT / "report.json")
    v22_req = dict(v22_rep.get("required") or {})
    if str(v22_req.get("VERDICT") or "") != V22_VERDICT_EXPECTED:
        return _stop("STOP. V22 verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if str(v22_req.get("PRIMARY_ENTRY_COVERAGE_DEFICIENCY") or "") != V22_PRIMARY_EXPECTED:
        return _stop("STOP. V22 primary freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if str(v22_req.get("V22_SPEC_SHA256") or "") != V22_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V22 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if int(v22_req.get("SIGNAL_N") or 0) != int(B1_SIGNAL_N_EXPECTED):
        return _stop("STOP. V22 SIGNAL_N freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if int(v22_req.get("EXECUTION_EVALUABLE_N") or 0) != int(B1_EXECUTABLE_N_EXPECTED):
        return _stop("STOP. V22 evaluable freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if int(v22_req.get("E4_FILLED_N") or 0) != int(E4_FILLED_N_EXPECTED):
        return _stop("STOP. V22 fill freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    if int(v22_req.get("E4_NONFILLED_N") or 0) != int(E4_UNFILLED_N_EXPECTED):
        return _stop("STOP. V22 nonfill freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    uneval = int(v22_req.get("EXECUTION_UNEVALUABLE_N") or 0)
    if uneval != int(STALE_N_EXPECTED):
        return _stop("STOP. V22 unevaluable/stale N freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [str(V22_CACHE), str(V22_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)

    stale_sigs = load_v22_stale_signals(list(ELIGIBLE_DAYS))
    if len(stale_sigs) != int(STALE_N_EXPECTED):
        return _stop(
            f"STOP. V22 stale cohort n={len(stale_sigs)} expected {STALE_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v23_sha=v23_sha,
        )
    if any(str(r.get("ask_reason") or "") != "STALE" for r in stale_sigs):
        return _stop("STOP. V22 stale cohort contains non-STALE ask_reason.", pre=pre, leak=leak, parent_sha=parent_sha, v23_sha=v23_sha)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in stale_sigs:
        by_day.setdefault(str(r["date"]), []).append(r)

    def _absorb(body: dict[str, Any]) -> None:
        skip = set(INTEGRITY_ZERO) | {"ACTIVE_CAPTURE_INPUT_N", "RESEARCH_INPUT_ACTIVE_FILE_N"}
        for k, v in dict(body.get("leak") or {}).items():
            if not str(k).endswith("_N") or k in skip:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                leak[k] = int(leak.get(k) or 0) + int(v)

    rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_sigs = by_day.get(day) or []
        cache_path = V23_CACHE / f"day_{day}.json"
        cached = load_v23_day_cache(cache_path, v23_sha)
        if cached and int(cached.get("stale_n") or 0) == len(day_sigs) and len(list(cached.get("rows") or [])) == len(day_sigs):
            print(f"{day} v23 stale cache hit rows={len(day_sigs)}", flush=True)
            rows.extend(list(cached.get("rows") or []))
            _absorb(cached)
            continue
        if not day_sigs:
            save_v23_day_cache(
                cache_path,
                {"ok": True, "date": day, "spec_sha": v23_sha, "events_n": 0, "rows": [], "leak": {}, "stale_n": 0},
            )
            continue
        body = replay_stale_clocks_day(
            {
                "date": day,
                "capture_path": cap.get("capture_path"),
                "signals": day_sigs,
                "spec_sha": v23_sha,
            }
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. Stale clock replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v23_sha=v23_sha,
            )
        save_v23_day_cache(cache_path, body)
        rows.extend(list(body.get("rows") or []))
        _absorb(body)

    summary = summarize(rows)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    identity_ok = bool(summary.get("IDENTITY_OK"))
    decision = decide(summary, identity_ok=identity_ok, leak_ok=leak_ok)
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS"))
    if not integ_ok:
        decision["CASE"] = "D"
        decision["VERDICT"] = "SIMPLE_TECH_V23_STALE_CAUSE_UNRESOLVED"
        decision["PRIMARY_STALE_CAUSE"] = "UNRESOLVED"
        decision["NEXT"] = "STOP. Identity or non-interference failed."

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "STALE_N": summary.get("STALE_N"),
        "STALE_CLASS_COUNTS": summary.get("STALE_CLASS_COUNTS"),
        "STALE_CLASS_DAY_COUNTS": summary.get("STALE_CLASS_DAY_COUNTS"),
        "STALE_CLASS_SYMBOL_COUNTS": summary.get("STALE_CLASS_SYMBOL_COUNTS"),
        "EVENT_TIME_AGE_DISTRIBUTION": summary.get("EVENT_TIME_AGE_DISTRIBUTION"),
        "RECEIVED_TIME_AGE_DISTRIBUTION": summary.get("RECEIVED_TIME_AGE_DISTRIBUTION"),
        "TIME_TO_NEXT_FRESH_QUOTE": summary.get("TIME_TO_NEXT_FRESH_QUOTE"),
        "REAL_MARKET_STALE_N": summary.get("REAL_MARKET_STALE_N"),
        "AVOIDABLE_TIMESTAMP_OR_JOIN_STALE_N": summary.get("AVOIDABLE_TIMESTAMP_OR_JOIN_STALE_N"),
        "UNRESOLVED_N": summary.get("UNRESOLVED_N"),
        "PRIMARY_STALE_CAUSE": decision.get("PRIMARY_STALE_CAUSE"),
        "ENTRY_CHANGED": False,
        "E4_CHANGED": False,
        "TRUE_OOS": False,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "V22_VERDICT": V22_VERDICT_EXPECTED,
        "V22_SPEC_SHA256": V22_SPEC_SHA256_EXPECTED,
        "V23_SPEC_SHA256": v23_sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
        "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
        "SIGNAL_SET_HASH": SIGNAL_SET_HASH_EXPECTED,
        "ELIGIBLE_SET_HASH": ELIGIBLE_SET_HASH_EXPECTED,
        "E4_FILL_SET_HASH": E4_FILL_SET_HASH_EXPECTED,
        "CANONICAL_FRESHNESS_SEC": float(CANONICAL_FRESHNESS_SEC),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "IDENTITY_OK": bool(identity_ok),
        "NEW_TRADE_FROM_STALE_N": 0,
        "Q1_MAJORITY_REAL_MARKET_STALE": decision.get("Q1_MAJORITY_REAL_MARKET_STALE"),
        "Q2_AVOIDABLE_TIMESTAMP_OR_JOIN_MATERIAL": decision.get("Q2_AVOIDABLE_TIMESTAMP_OR_JOIN_MATERIAL"),
        "Q3_AVOIDABLE_MULTI_DAY_MULTI_SYMBOL": decision.get("Q3_AVOIDABLE_MULTI_DAY_MULTI_SYMBOL"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "summary": summary,
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    stale_sheet = [
        {
            "date": r.get("date"),
            "symbol": r.get("symbol"),
            "signal_time": r.get("signal_time"),
            "stale_class": r.get("stale_class"),
            "reason": r.get("reason"),
            "last_price_event_time": r.get("last_price_event_time"),
            "last_board_event_time": r.get("last_board_event_time"),
            "last_price_received_at": r.get("last_price_received_at"),
            "last_board_received_at": r.get("last_board_received_at"),
            "price_age_event_sec": r.get("price_age_event_sec"),
            "board_age_event_sec": r.get("board_age_event_sec"),
            "price_age_received_sec": r.get("price_age_received_sec"),
            "board_age_received_sec": r.get("board_age_received_sec"),
            "canonical_fresh_sec": r.get("canonical_fresh_sec"),
            "canonical_fresh_source": r.get("canonical_fresh_source"),
            "next_price_event_time": r.get("next_price_event_time"),
            "next_board_event_time": r.get("next_board_event_time"),
            "next_price_received_at": r.get("next_price_received_at"),
            "next_board_received_at": r.get("next_board_received_at"),
            "time_to_next_fresh_quote_sec": r.get("time_to_next_fresh_quote_sec"),
        }
        for r in rows
    ]
    write_artifacts(
        report,
        {
            "Precommit": kv_rows(
                {
                    "ANALYSIS_ID": ANALYSIS_ID,
                    "V23_SPEC_SHA256": v23_sha,
                    "V22_VERDICT": V22_VERDICT_EXPECTED,
                    "ENTRY_CHANGED": False,
                    "E4_CHANGED": False,
                    "FRESHNESS_THRESHOLD_CHANGED": False,
                    "CANONICAL_FRESHNESS_SEC": float(CANONICAL_FRESHNESS_SEC),
                    "VIRTUAL_FILL": False,
                    "FILL_REPLAY": False,
                    "PNL_EVAL": False,
                }
            ),
            "Identity": kv_rows(
                {
                    "STALE_N": summary.get("STALE_N"),
                    "IDENTITY_OK": identity_ok,
                    "SIGNAL_SET_HASH": SIGNAL_SET_HASH_EXPECTED,
                    "V22_SPEC_SHA256": V22_SPEC_SHA256_EXPECTED,
                }
            ),
            "StaleRows": stale_sheet or [{"empty": True}],
            "ClassCounts": kv_rows(
                {
                    "STALE_CLASS_COUNTS": summary.get("STALE_CLASS_COUNTS"),
                    "STALE_CLASS_DAY_COUNTS": summary.get("STALE_CLASS_DAY_COUNTS"),
                    "STALE_CLASS_SYMBOL_COUNTS": summary.get("STALE_CLASS_SYMBOL_COUNTS"),
                    "STALE_CLASS_COVERAGE": summary.get("STALE_CLASS_COVERAGE"),
                }
            ),
            "Distributions": kv_rows(
                {
                    "EVENT_TIME_AGE_DISTRIBUTION": summary.get("EVENT_TIME_AGE_DISTRIBUTION"),
                    "RECEIVED_TIME_AGE_DISTRIBUTION": summary.get("RECEIVED_TIME_AGE_DISTRIBUTION"),
                    "TIME_TO_NEXT_FRESH_QUOTE": summary.get("TIME_TO_NEXT_FRESH_QUOTE"),
                }
            ),
            "DaySymbol": kv_rows(
                {
                    "AVOIDABLE_DISTINCT_DAYS": summary.get("AVOIDABLE_DISTINCT_DAYS"),
                    "AVOIDABLE_DISTINCT_SYMBOLS": summary.get("AVOIDABLE_DISTINCT_SYMBOLS"),
                    "canonical_fresh_source_counts": summary.get("canonical_fresh_source_counts"),
                    "event_stamp_kind_counts": summary.get("event_stamp_kind_counts"),
                }
            ),
            "Decision": kv_rows(decision),
            "Reporting": kv_rows(reporting),
            "Integrity": kv_rows(leak),
            "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
        },
    )
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} STALE_N={summary.get('STALE_N')} "
        f"REAL={summary.get('REAL_MARKET_STALE_N')} AVOIDABLE={summary.get('AVOIDABLE_TIMESTAMP_OR_JOIN_STALE_N')} "
        f"UNRESOLVED={summary.get('UNRESOLVED_N')} PRIMARY={decision.get('PRIMARY_STALE_CAUSE')} "
        f"VERDICT={decision.get('VERDICT')} ni={ni_ok} out={V23_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if ni_ok and identity_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
