"""Offline SIMPLE_TECH V28 ADDED-only 3m EMA persistence K=6 EXIT. No Capture control. No K search."""
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
    V26_OUT,
    V27_OUT,
    V28_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v24_harvest import V22_CACHE, load_v22_day_rows
from research.simple_tech_redesign.v27_analyze import research_fill_tuples
from research.simple_tech_redesign.v28_analyze import decide, summarize
from research.simple_tech_redesign.v28_harvest import (
    V28_CACHE,
    load_v28_day_cache,
    replay_policy_day,
    save_v28_day_cache,
)
from research.simple_tech_redesign.v28_publish import REQUIRED_KEYS, build_markdown, write_artifacts
from research.simple_tech_redesign.v28_spec import (
    ANALYSIS_ID,
    ASK_FALLBACK_CHANGED,
    B1_SIGNAL_N_EXPECTED,
    CANONICAL_FRESHNESS_SEC,
    COMBINATION_SEARCH,
    CORE_TECH_EXIT,
    CORRECTED_FILL_HASH_EXPECTED,
    E4_CHANGED,
    E4_WAIT_BUDGET_SEC,
    ENTRY_CHANGED,
    FIXED180_PNL_USED,
    FRESHNESS_THRESHOLD_CHANGED,
    K_SEARCH,
    PARENT_SPEC_SHA256_EXPECTED,
    PERSISTENCE_K,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    RESEARCH_PARALLELISM,
    RUNTIME_CANDIDATE,
    SIZING_CHANGED,
    SIGNAL_SET_HASH_EXPECTED,
    THRESHOLD_SEARCH,
    TIME_STOP_USED,
    TRUE_OOS,
    V26_SPEC_SHA256_FROZEN,
    V26_VERDICT_FROZEN,
    V27_PRIMARY_EXPECTED,
    V27_SPEC_SHA256_EXPECTED,
    V27_VERDICT_EXPECTED,
    canonical_v28_spec,
    spec_sha256_v28,
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
    "FUTURE_BOARD_CARRYBACK_N",
    "FUTURE_QUOTE_CARRYBACK_N",
    "QUEUE_ASSUMPTION_N",
    "OPTIMISTIC_TOUCH_N",
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
    "ENTRY_RULE_CHANGE_N",
    "E4_CHANGE_N",
    "FRESHNESS_CHANGE_N",
    "THRESHOLD_SEARCH_N",
    "K_SEARCH_N",
    "BAR_COUNT_SEARCH_N",
    "COMBINATION_SEARCH_N",
    "WAIT_EXTENSION_N",
    "REPRICE_N",
    "CHASE_N",
    "SECOND_FALLBACK_N",
    "FALLBACK_MARKET_N",
    "TIME_STOP_N",
    "EXIT_COMBINATION_N",
    "MIXED_TF_RULE_N",
    "CORE_TECH_EXIT_N",
    "PRE_DECISION_EXIT_N",
    "PRE_BAR_EXIT_N",
    "LAST_QUOTE_CARRYBACK_TECH_N",
    "VWAP_N",
    "EXIT_PRICE_OPT_N",
    "DELAY_OPT_N",
    "BB_EXIT_N",
    "RCI_EXIT_N",
    "VOLUME_EXIT_N",
    "MFE_EXIT_N",
    "V22_WRITE_N",
    "V26_WRITE_N",
    "V27_WRITE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v28_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V28_INVALID",
            "TRUE_OOS": False,
            "ENTRY_CHANGED": False,
            "SIZING_CHANGED": False,
            "NEXT": msg,
            "V27_FILL_IDENTITY_PARITY": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V28_SPEC_SHA256": v28_sha,
            "PERSISTENCE_K": int(PERSISTENCE_K),
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
    spec = canonical_v28_spec()
    v28_sha = spec_sha256_v28(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v28_policy={v28_sha[:12]} k={PERSISTENCE_K}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    if (
        ENTRY_CHANGED
        or E4_CHANGED
        or ASK_FALLBACK_CHANGED
        or FRESHNESS_THRESHOLD_CHANGED
        or SIZING_CHANGED
        or FIXED180_PNL_USED
        or TIME_STOP_USED
        or K_SEARCH
        or COMBINATION_SEARCH
        or THRESHOLD_SEARCH
        or CORE_TECH_EXIT
        or bool(TRUE_OOS)
        or RUNTIME_CANDIDATE
        or int(PERSISTENCE_K) != 6
        or abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) > 1e-12
        or abs(float(E4_WAIT_BUDGET_SEC) - 5.0) > 1e-12
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)

    v26_req = dict(_load(V26_OUT / "report.json").get("required") or {})
    if str(v26_req.get("VERDICT") or "") != V26_VERDICT_FROZEN:
        return _stop("STOP. V26 official verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    if str(v26_req.get("V26_SPEC_SHA256") or "") != V26_SPEC_SHA256_FROZEN:
        return _stop("STOP. V26 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    v27_req = dict(_load(V27_OUT / "report.json").get("required") or {})
    if str(v27_req.get("VERDICT") or "") != V27_VERDICT_EXPECTED:
        return _stop("STOP. V27 official verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    if str(v27_req.get("V27_SPEC_SHA256") or "") != V27_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V27 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    prim = dict(v27_req.get("PRIMARY_EXIT_STATE_MECHANISM") or {})
    if str(prim.get("name") or "") != V27_PRIMARY_EXPECTED:
        return _stop("STOP. V27 primary mechanism freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [str(V22_CACHE), str(V22_OUT / "report.json"), str(V26_OUT / "report.json"), str(V27_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)

    v22_by_day: dict[str, list[dict[str, Any]]] = {}
    v22_n = 0
    for cap in caps:
        day = str(cap["date"])
        day_rows = load_v22_day_rows(day)
        if not day_rows:
            return _stop(f"STOP. V22 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)
        v22_by_day[day] = day_rows
        v22_n += len(day_rows)
    if v22_n != int(B1_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. V22 signal n={v22_n} expected {B1_SIGNAL_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v28_sha=v28_sha)

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
            "V26_WRITE_N",
            "V27_WRITE_N",
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
        cache_path = V28_CACHE / f"day_{day}.json"
        cached = load_v28_day_cache(cache_path, v28_sha)
        if cached and int(cached.get("signal_n") or 0) == len(day_sigs) and len(list(cached.get("rows") or [])) == len(day_sigs):
            print(f"{day} v28 policy cache hit rows={len(day_sigs)}", flush=True)
            rows.extend(list(cached.get("rows") or []))
            _absorb(cached)
            continue
        body = replay_policy_day(
            {
                "date": day,
                "capture_path": cap.get("capture_path"),
                "signals": day_sigs,
                "spec_sha": v28_sha,
            }
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. Policy replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v28_sha=v28_sha,
            )
        save_v28_day_cache(cache_path, body)
        rows.extend(list(body.get("rows") or []))
        _absorb(body)

    sig_hash = set_hash(signal_tuples(rows))
    signal_parity = sig_hash == SIGNAL_SET_HASH_EXPECTED and len(rows) == int(B1_SIGNAL_N_EXPECTED)
    fill_identity = set_hash(research_fill_tuples(rows)) == RESEARCH_FILL_SET_HASH_EXPECTED
    summary = summarize(rows)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(
        leak_ok
        and reporting.get("RUNTIME_PID_UNCHANGED")
        and reporting.get("CAPTURE_PID_UNCHANGED")
        and reporting.get("REPORTING_SEMANTICS_PASS")
    )
    core_parity_ok = bool(summary.get("CORE_ENTRY_PARITY") and summary.get("CORE_EXIT_CONTROL_PARITY") and summary.get("CORE_PNL_PARITY"))
    exit_miss_ok = (
        int((summary.get("CORE_CONTROL_ECONOMICS") or {}).get("EXIT_MISS_N") or 0) == 0
        and int((summary.get("ADDED_CONTROL_ECONOMICS") or {}).get("EXIT_MISS_N") or 0) == 0
        and int((summary.get("ADDED_TREATMENT_ECONOMICS") or {}).get("EXIT_MISS_N") or 0) == 0
    )
    decision = decide(
        summary,
        signal_parity=signal_parity,
        fill_identity=fill_identity,
        leak_ok=leak_ok,
        ni_ok=ni_ok,
        core_parity_ok=core_parity_ok,
        exit_miss_ok=exit_miss_ok,
    )
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "V27_FILL_IDENTITY_PARITY": decision.get("V27_FILL_IDENTITY_PARITY"),
        "CORE_FILL_N": summary.get("CORE_FILL_N"),
        "ADDED_FILL_N": summary.get("ADDED_FILL_N"),
        "PERSISTENCE_K": int(PERSISTENCE_K),
        "TECH_EXIT_N": summary.get("TECH_EXIT_N"),
        "SESSION_CLOSE_EXIT_N": summary.get("SESSION_CLOSE_EXIT_N"),
        "TECH_EXIT_DAY_N": summary.get("TECH_EXIT_DAY_N"),
        "TECH_EXIT_SYMBOL_N": summary.get("TECH_EXIT_SYMBOL_N"),
        "PATH_TYPE_EXIT_RATES": summary.get("PATH_TYPE_EXIT_RATES"),
        "ADDED_CONTROL_ECONOMICS": summary.get("ADDED_CONTROL_ECONOMICS"),
        "ADDED_TREATMENT_ECONOMICS": summary.get("ADDED_TREATMENT_ECONOMICS"),
        "DELTA_TOTAL_PNL": decision.get("DELTA_TOTAL_PNL"),
        "DELTA_PF": decision.get("DELTA_PF"),
        "DELTA_MAX_DD": decision.get("DELTA_MAX_DD"),
        "DELTA_EX_BEST_DAY": decision.get("DELTA_EX_BEST_DAY"),
        "DELTA_EX_TOP3_DAY": decision.get("DELTA_EX_TOP3_DAY"),
        "DELTA_DROP_TOP_SYMBOL": decision.get("DELTA_DROP_TOP_SYMBOL"),
        "CORE_PNL_PARITY": summary.get("CORE_PNL_PARITY"),
        "CORE_ENTRY_PARITY": summary.get("CORE_ENTRY_PARITY"),
        "CORE_EXIT_CONTROL_PARITY": summary.get("CORE_EXIT_CONTROL_PARITY"),
        "COMBINED_232_ECONOMICS": summary.get("COMBINED_232_ECONOMICS"),
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "SIGNAL_SET_HASH": sig_hash,
        "CORRECTED_FILL_HASH": summary.get("CORRECTED_FILL_HASH"),
        "RESEARCH_FILL_SET_HASH": summary.get("RESEARCH_FILL_SET_HASH"),
        "V26_VERDICT_FROZEN": V26_VERDICT_FROZEN,
        "V27_VERDICT_FROZEN": V27_VERDICT_EXPECTED,
        "V27_SPEC_SHA256": V27_SPEC_SHA256_EXPECTED,
        "V28_SPEC_SHA256": v28_sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "LOSS_EPISODE_COUNT": summary.get("LOSS_EPISODE_COUNT"),
        "K6_REACHED_N": summary.get("K6_REACHED_N"),
        "K6_RECOVERED_BEFORE_TRIGGER_N": summary.get("K6_RECOVERED_BEFORE_TRIGGER_N"),
        "PATH_TYPE_PNL_DELTA": summary.get("PATH_TYPE_PNL_DELTA"),
        "Q1": decision.get("Q1_BAD_CAPTURE"),
        "Q2_GOOD": decision.get("Q2_GOOD_RATE"),
        "Q2_DIP": decision.get("Q2_DIP_RATE"),
        "Q3": decision.get("Q3_ADDED_IMPROVED"),
        "EXIT_FROZEN": False,
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
    write_artifacts(
        report,
        {
            "Precommit": kv_rows(
                {
                    "ANALYSIS_ID": ANALYSIS_ID,
                    "V28_SPEC_SHA256": v28_sha,
                    "PERSISTENCE_K": int(PERSISTENCE_K),
                    "POLICY_ID": "ADDED_ONLY_3M_EMA_PERSISTENCE_K6",
                    "K_SEARCH": False,
                    "CORE_TECH_EXIT": False,
                    "EXIT_FROZEN": False,
                }
            ),
            "Identity": kv_rows(
                {
                    "V27_FILL_IDENTITY_PARITY": decision.get("V27_FILL_IDENTITY_PARITY"),
                    "SIGNAL_SET_HASH": sig_hash,
                    "RESEARCH_FILL_SET_HASH": summary.get("RESEARCH_FILL_SET_HASH"),
                    "CORE_FILL_N": summary.get("CORE_FILL_N"),
                    "ADDED_FILL_N": summary.get("ADDED_FILL_N"),
                }
            ),
            "ExitCounts": kv_rows(
                {
                    "TECH_EXIT_N": summary.get("TECH_EXIT_N"),
                    "SESSION_CLOSE_EXIT_N": summary.get("SESSION_CLOSE_EXIT_N"),
                    "TECH_EXIT_DAY_N": summary.get("TECH_EXIT_DAY_N"),
                    "TECH_EXIT_SYMBOL_N": summary.get("TECH_EXIT_SYMBOL_N"),
                    "LOSS_EPISODE_COUNT": summary.get("LOSS_EPISODE_COUNT"),
                    "K6_REACHED_N": summary.get("K6_REACHED_N"),
                    "K6_RECOVERED_BEFORE_TRIGGER_N": summary.get("K6_RECOVERED_BEFORE_TRIGGER_N"),
                }
            ),
            "PathRates": kv_rows(summary.get("PATH_TYPE_EXIT_RATES") or {}),
            "AddedControl": kv_rows(summary.get("ADDED_CONTROL_ECONOMICS") or {}),
            "AddedTreatment": kv_rows(summary.get("ADDED_TREATMENT_ECONOMICS") or {}),
            "Deltas": kv_rows(
                {
                    "DELTA_TOTAL_PNL": summary.get("DELTA_TOTAL_PNL"),
                    "DELTA_PF": summary.get("DELTA_PF"),
                    "DELTA_MAX_DD": summary.get("DELTA_MAX_DD"),
                    "DELTA_EX_BEST_DAY": summary.get("DELTA_EX_BEST_DAY"),
                    "DELTA_EX_TOP3_DAY": summary.get("DELTA_EX_TOP3_DAY"),
                    "DELTA_DROP_TOP_SYMBOL": summary.get("DELTA_DROP_TOP_SYMBOL"),
                }
            ),
            "CoreParity": kv_rows(
                {
                    "CORE_ENTRY_PARITY": summary.get("CORE_ENTRY_PARITY"),
                    "CORE_EXIT_CONTROL_PARITY": summary.get("CORE_EXIT_CONTROL_PARITY"),
                    "CORE_PNL_PARITY": summary.get("CORE_PNL_PARITY"),
                    "CORE_CONTROL": summary.get("CORE_CONTROL_ECONOMICS"),
                }
            ),
            "Combined232": kv_rows(summary.get("COMBINED_232_ECONOMICS") or {}),
            "Decision": kv_rows(decision),
            "Reporting": kv_rows(reporting),
            "Integrity": kv_rows(leak),
            "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
        },
    )
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} FILLS={summary.get('TOTAL_RESEARCH_FILL_N')} "
        f"CORE={summary.get('CORE_FILL_N')} ADDED={summary.get('ADDED_FILL_N')} "
        f"K={PERSISTENCE_K} TECH_EXIT={summary.get('TECH_EXIT_N')} "
        f"DELTA_PNL={summary.get('DELTA_TOTAL_PNL')} VERDICT={decision.get('VERDICT')} "
        f"ni={ni_ok} out={V28_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    ok = decision.get("CASE") in {"A", "B", "C", "D"} and ni_ok and signal_parity and fill_identity
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
