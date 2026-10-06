"""Offline SIMPLE_TECH V25 corrected execution baseline reconciliation. V24 verdict frozen. No Capture control."""
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
    V24_OUT,
    V25_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v24_harvest import V22_CACHE, load_v22_day_rows
from research.simple_tech_redesign.v25_analyze import decide, summarize
from research.simple_tech_redesign.v25_harvest import (
    V25_CACHE,
    load_v25_day_cache,
    replay_reconcile_day,
    save_v25_day_cache,
)
from research.simple_tech_redesign.v25_publish import REQUIRED_KEYS, build_markdown, write_artifacts
from research.simple_tech_redesign.v25_spec import (
    ANALYSIS_ID,
    B1_SIGNAL_N_EXPECTED,
    CANONICAL_FRESHNESS_SEC,
    CORRECTED_FILL_HASH_EXPECTED,
    CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS,
    DEVELOPMENT_ENTRY_STACK,
    E4_CHANGED,
    E4_WAIT_BUDGET_SEC,
    ENTRY_CHANGED,
    EXIT_CHANGED,
    FIXED180_PNL_USED,
    FRESHNESS_THRESHOLD_CHANGED,
    LEGACY_126_38_88_REQUIRED,
    PARENT_SPEC_SHA256_EXPECTED,
    PNL_EVAL,
    RESEARCH_PARALLELISM,
    RUNTIME_CANDIDATE,
    SIGNAL_SET_HASH_EXPECTED,
    SIZING_CHANGED,
    SPECIAL_CASES,
    TRUE_OOS,
    V24_SPEC_SHA256_EXPECTED,
    V24_VERDICT_EXPECTED,
    canonical_v25_spec,
    spec_sha256_v25,
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
    "EXIT_SIM_N",
    "REPRICE_N",
    "CHASE_N",
    "FALLBACK_MARKET_N",
    "OPTIMISTIC_FILL_N",
    "TOUCH_FILL_N",
    "QUEUE_FILL_N",
    "V22_WRITE_N",
    "V24_WRITE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v25_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V25_BOARD_CLOCK_CAUSALITY_UNRESOLVED",
            "TRUE_OOS": False,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "FRESHNESS_THRESHOLD_CHANGED": False,
            "NEXT": msg,
            "STRATEGY_RULE_PARITY": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V25_SPEC_SHA256": v25_sha,
        }
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "_markdown": build_markdown({"required": req, "decision": {"CASE": "C"}}),
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
    spec = canonical_v25_spec()
    v25_sha = spec_sha256_v25(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v25_reconcile={v25_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)
    if (
        ENTRY_CHANGED
        or E4_CHANGED
        or FRESHNESS_THRESHOLD_CHANGED
        or EXIT_CHANGED
        or SIZING_CHANGED
        or PNL_EVAL
        or FIXED180_PNL_USED
        or LEGACY_126_38_88_REQUIRED
        or CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS
        or bool(TRUE_OOS)
        or RUNTIME_CANDIDATE
        or abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) > 1e-12
        or abs(float(E4_WAIT_BUDGET_SEC) - 5.0) > 1e-12
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)

    v24_req = dict(_load(V24_OUT / "report.json").get("required") or {})
    if str(v24_req.get("VERDICT") or "") != V24_VERDICT_EXPECTED:
        return _stop("STOP. V24 official verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)
    if str(v24_req.get("V24_SPEC_SHA256") or "") != V24_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V24 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)
    if str(v24_req.get("E4_FILL_SET_HASH_CORRECTED") or "") != CORRECTED_FILL_HASH_EXPECTED:
        return _stop("STOP. V24 corrected fill hash freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps] + [str(V22_CACHE), str(V22_OUT / "report.json"), str(V24_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)

    v22_by_day: dict[str, list[dict[str, Any]]] = {}
    v22_n = 0
    for cap in caps:
        day = str(cap["date"])
        day_rows = load_v22_day_rows(day)
        if not day_rows:
            return _stop(f"STOP. V22 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)
        v22_by_day[day] = day_rows
        v22_n += len(day_rows)
    if v22_n != int(B1_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. V22 signal n={v22_n} expected {B1_SIGNAL_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v25_sha=v25_sha)

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
            "V24_WRITE_N",
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
        cache_path = V25_CACHE / f"day_{day}.json"
        cached = load_v25_day_cache(cache_path, v25_sha)
        if cached and int(cached.get("signal_n") or 0) == len(day_sigs) and len(list(cached.get("rows") or [])) == len(day_sigs):
            print(f"{day} v25 reconcile cache hit rows={len(day_sigs)}", flush=True)
            rows.extend(list(cached.get("rows") or []))
            _absorb(cached)
            continue
        body = replay_reconcile_day(
            {
                "date": day,
                "capture_path": cap.get("capture_path"),
                "signals": day_sigs,
                "spec_sha": v25_sha,
            }
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. Reconcile replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v25_sha=v25_sha,
            )
        save_v25_day_cache(cache_path, body)
        rows.extend(list(body.get("rows") or []))
        _absorb(body)

    sig_hash = set_hash(signal_tuples(rows))
    signal_parity = sig_hash == SIGNAL_SET_HASH_EXPECTED and len(rows) == int(B1_SIGNAL_N_EXPECTED)
    summary = summarize(rows)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED") and reporting.get("REPORTING_SEMANTICS_PASS"))
    decision = decide(
        summary,
        signal_parity=signal_parity,
        leak_ok=leak_ok,
        ni_ok=ni_ok,
        future_board_n=int(leak.get("FUTURE_BOARD_CARRYBACK_N") or 0),
        future_ts_n=int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0),
        future_quote_n=int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0),
        queue_n=int(leak.get("QUEUE_ASSUMPTION_N") or 0),
        touch_n=int(leak.get("OPTIMISTIC_TOUCH_N") or 0),
    )
    specials = dict(summary.get("specials") or {})
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "STRATEGY_RULE_PARITY": decision.get("STRATEGY_RULE_PARITY"),
        "SIGNAL_N": summary.get("SIGNAL_N"),
        "CORRECTED_EXECUTION_EVALUABLE_N": summary.get("CORRECTED_EXECUTION_EVALUABLE_N"),
        "CORRECTED_EXECUTION_UNEVALUABLE_N": summary.get("CORRECTED_EXECUTION_UNEVALUABLE_N"),
        "CORRECTED_E4_FILLED_N": summary.get("CORRECTED_E4_FILLED_N"),
        "CORRECTED_E4_NONFILLED_N": summary.get("CORRECTED_E4_NONFILLED_N"),
        "CORRECTED_FILL_HASH_PARITY": decision.get("CORRECTED_FILL_HASH_PARITY"),
        "CORRECTED_FILL_HASH": summary.get("CORRECTED_FILL_HASH"),
        "SPECIAL_CASE_5985": specials.get("SPECIAL_CASE_5985"),
        "SPECIAL_CASE_3696": specials.get("SPECIAL_CASE_3696"),
        "SPECIAL_CASE_6890": specials.get("SPECIAL_CASE_6890"),
        "SPECIAL_CASE_6703": specials.get("SPECIAL_CASE_6703"),
        "SPECIAL_CASE_4401": specials.get("SPECIAL_CASE_4401"),
        "SPECIAL_CASE_581A": specials.get("SPECIAL_CASE_581A"),
        "BOARD_CLOCK_CAUSALITY_PASS": decision.get("BOARD_CLOCK_CAUSALITY_PASS"),
        "FUTURE_BOARD_CARRYBACK_N": int(leak.get("FUTURE_BOARD_CARRYBACK_N") or 0),
        "FUTURE_TIMESTAMP_CARRYBACK_N": int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0),
        "FUTURE_QUOTE_CARRYBACK_N": int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0),
        "ENTRY_CHANGED": False,
        "E4_CHANGED": False,
        "FRESHNESS_THRESHOLD_CHANGED": False,
        "TRUE_OOS": False,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "SIGNAL_SET_HASH": sig_hash,
        "V24_VERDICT_FROZEN": V24_VERDICT_EXPECTED,
        "V24_SPEC_SHA256": V24_SPEC_SHA256_EXPECTED,
        "V25_SPEC_SHA256": v25_sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
        "CANONICAL_FRESHNESS_SEC": float(CANONICAL_FRESHNESS_SEC),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "BOARD_CLOCK_SOURCE_COUNTS": summary.get("BOARD_CLOCK_SOURCE_COUNTS"),
        "PNL_USED_FOR_ACCEPT": False,
        "LEGACY_126_38_88_REQUIRED": False,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "summary": {k: v for k, v in summary.items() if k != "specials"},
        "specials": specials,
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    clock_sheet = [
        {
            "date": r.get("date"),
            "symbol": r.get("symbol"),
            "t0": r.get("t0"),
            "BOARD_CLOCK_SOURCE": r.get("BOARD_CLOCK_SOURCE"),
            "board_age_t0_sec": r.get("board_age_t0_sec"),
            "price_age_t0_sec": r.get("price_age_t0_sec"),
            "ingress_age_t0_sec": r.get("ingress_age_t0_sec"),
            "executable_signal": r.get("executable_signal"),
            "e4_filled": r.get("e4_filled"),
            "funnel_reason": r.get("funnel_reason"),
        }
        for r in rows
    ]
    write_artifacts(
        report,
        {
            "Precommit": kv_rows(
                {
                    "ANALYSIS_ID": ANALYSIS_ID,
                    "V25_SPEC_SHA256": v25_sha,
                    "V24_VERDICT_FROZEN": V24_VERDICT_EXPECTED,
                    "LEGACY_126_38_88_REQUIRED": False,
                    "ENTRY_CHANGED": False,
                    "E4_CHANGED": False,
                    "FRESHNESS_THRESHOLD_CHANGED": False,
                    "PNL_EVAL": False,
                }
            ),
            "Identity": kv_rows(
                {
                    "STRATEGY_RULE_PARITY": signal_parity,
                    "SIGNAL_SET_HASH": sig_hash,
                    "CORRECTED_FILL_HASH": summary.get("CORRECTED_FILL_HASH"),
                    "CORRECTED_FILL_HASH_EXPECTED": CORRECTED_FILL_HASH_EXPECTED,
                    "CORRECTED_FILL_HASH_PARITY": decision.get("CORRECTED_FILL_HASH_PARITY"),
                }
            ),
            "Funnel": kv_rows(
                {
                    "SIGNAL_N": summary.get("SIGNAL_N"),
                    "CORRECTED_EXECUTION_EVALUABLE_N": summary.get("CORRECTED_EXECUTION_EVALUABLE_N"),
                    "CORRECTED_EXECUTION_UNEVALUABLE_N": summary.get("CORRECTED_EXECUTION_UNEVALUABLE_N"),
                    "CORRECTED_E4_FILLED_N": summary.get("CORRECTED_E4_FILLED_N"),
                    "CORRECTED_E4_NONFILLED_N": summary.get("CORRECTED_E4_NONFILLED_N"),
                    "BOARD_CLOCK_SOURCE_COUNTS": summary.get("BOARD_CLOCK_SOURCE_COUNTS"),
                }
            ),
            "SpecialCases": [specials.get(str(x["id"])) or {"id": x["id"], "empty": True} for x in SPECIAL_CASES],
            "ClockAudit": clock_sheet or [{"empty": True}],
            "Decision": kv_rows(decision),
            "Reporting": kv_rows(reporting),
            "Integrity": kv_rows(leak),
            "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
        },
    )
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} SIGNAL_N={summary.get('SIGNAL_N')} "
        f"EVAL={summary.get('CORRECTED_EXECUTION_EVALUABLE_N')} "
        f"FILLED={summary.get('CORRECTED_E4_FILLED_N')} "
        f"HASH_PARITY={decision.get('CORRECTED_FILL_HASH_PARITY')} "
        f"VERDICT={decision.get('VERDICT')} ni={ni_ok} out={V25_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    ok = decision.get("CASE") == "A" and ni_ok and signal_parity
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
