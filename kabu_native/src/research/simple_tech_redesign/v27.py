"""Offline SIMPLE_TECH V27 EXIT state-sequence RCA. V26 fills frozen. No Capture control. No EXIT policy."""
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
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v24_harvest import V22_CACHE, load_v22_day_rows
from research.simple_tech_redesign.v27_analyze import decide, research_fill_tuples, summarize
from research.simple_tech_redesign.v27_harvest import (
    V27_CACHE,
    load_v27_day_cache,
    replay_sequence_day,
    save_v27_day_cache,
)
from research.simple_tech_redesign.v27_publish import REQUIRED_KEYS, build_markdown, write_artifacts
from research.simple_tech_redesign.v27_spec import (
    ANALYSIS_ID,
    ASK_FALLBACK_CHANGED,
    B1_SIGNAL_N_EXPECTED,
    BAR_COUNT_SEARCH,
    CANONICAL_FRESHNESS_SEC,
    COMBINATION_SEARCH,
    CORRECTED_FILL_HASH_EXPECTED,
    E4_CHANGED,
    E4_WAIT_BUDGET_SEC,
    ENTRY_CHANGED,
    EXIT_POLICY_CREATED,
    FIXED180_PNL_USED,
    FRESHNESS_THRESHOLD_CHANGED,
    MIXED_TF_RULE,
    PARENT_SPEC_SHA256_EXPECTED,
    PNL_EVAL,
    PRIMITIVE_CHANGED,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    RESEARCH_PARALLELISM,
    RUNTIME_CANDIDATE,
    SIZING_CHANGED,
    SIGNAL_SET_HASH_EXPECTED,
    THRESHOLD_SEARCH,
    TIME_STOP_USED,
    TRUE_OOS,
    V26_SPEC_SHA256_EXPECTED,
    V26_VERDICT_EXPECTED,
    canonical_v27_spec,
    spec_sha256_v27,
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
    "BAR_COUNT_SEARCH_N",
    "COMBINATION_SEARCH_N",
    "WAIT_EXTENSION_N",
    "REPRICE_N",
    "CHASE_N",
    "SECOND_FALLBACK_N",
    "FALLBACK_MARKET_N",
    "TIME_STOP_N",
    "EXIT_POLICY_N",
    "EXIT_COMBINATION_N",
    "MIXED_TF_RULE_N",
    "RCI_LEVEL_THRESHOLD_N",
    "VOLUME_MULT_SEARCH_N",
    "V22_WRITE_N",
    "V26_WRITE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v27_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V27_EXIT_STATE_SEQUENCE_UNRESOLVED",
            "TRUE_OOS": False,
            "ENTRY_CHANGED": False,
            "EXIT_POLICY_CREATED": False,
            "NEXT": msg,
            "V26_FILL_IDENTITY_PARITY": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V27_SPEC_SHA256": v27_sha,
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
    spec = canonical_v27_spec()
    v27_sha = spec_sha256_v27(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v27_sequence={v27_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)
    if (
        ENTRY_CHANGED
        or E4_CHANGED
        or ASK_FALLBACK_CHANGED
        or FRESHNESS_THRESHOLD_CHANGED
        or PRIMITIVE_CHANGED
        or EXIT_POLICY_CREATED
        or SIZING_CHANGED
        or PNL_EVAL
        or FIXED180_PNL_USED
        or TIME_STOP_USED
        or BAR_COUNT_SEARCH
        or COMBINATION_SEARCH
        or THRESHOLD_SEARCH
        or MIXED_TF_RULE
        or bool(TRUE_OOS)
        or RUNTIME_CANDIDATE
        or abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) > 1e-12
        or abs(float(E4_WAIT_BUDGET_SEC) - 5.0) > 1e-12
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)

    v26_req = dict(_load(V26_OUT / "report.json").get("required") or {})
    if str(v26_req.get("VERDICT") or "") != V26_VERDICT_EXPECTED:
        return _stop("STOP. V26 official verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)
    if str(v26_req.get("V26_SPEC_SHA256") or "") != V26_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V26 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)
    if str(v26_req.get("CORRECTED_FILL_HASH") or "") != CORRECTED_FILL_HASH_EXPECTED:
        return _stop("STOP. V26 core E4 fill hash freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps] + [str(V22_CACHE), str(V22_OUT / "report.json"), str(V26_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)

    v22_by_day: dict[str, list[dict[str, Any]]] = {}
    v22_n = 0
    for cap in caps:
        day = str(cap["date"])
        day_rows = load_v22_day_rows(day)
        if not day_rows:
            return _stop(f"STOP. V22 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)
        v22_by_day[day] = day_rows
        v22_n += len(day_rows)
    if v22_n != int(B1_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. V22 signal n={v22_n} expected {B1_SIGNAL_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v27_sha=v27_sha)

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
        cache_path = V27_CACHE / f"day_{day}.json"
        cached = load_v27_day_cache(cache_path, v27_sha)
        if cached and int(cached.get("signal_n") or 0) == len(day_sigs) and len(list(cached.get("rows") or [])) == len(day_sigs):
            print(f"{day} v27 sequence cache hit rows={len(day_sigs)}", flush=True)
            rows.extend(list(cached.get("rows") or []))
            _absorb(cached)
            continue
        body = replay_sequence_day(
            {
                "date": day,
                "capture_path": cap.get("capture_path"),
                "signals": day_sigs,
                "spec_sha": v27_sha,
            }
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. Sequence replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v27_sha=v27_sha,
            )
        save_v27_day_cache(cache_path, body)
        rows.extend(list(body.get("rows") or []))
        _absorb(body)

    sig_hash = set_hash(signal_tuples(rows))
    signal_parity = sig_hash == SIGNAL_SET_HASH_EXPECTED and len(rows) == int(B1_SIGNAL_N_EXPECTED)
    fill_identity = set_hash(research_fill_tuples(rows)) == RESEARCH_FILL_SET_HASH_EXPECTED
    summary = summarize(rows)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED") and reporting.get("REPORTING_SEMANTICS_PASS"))
    decision = decide(summary, signal_parity=signal_parity, fill_identity=fill_identity, leak_ok=leak_ok, ni_ok=ni_ok)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "V26_FILL_IDENTITY_PARITY": decision.get("V26_FILL_IDENTITY_PARITY"),
        "TOTAL_RESEARCH_FILL_N": summary.get("TOTAL_RESEARCH_FILL_N"),
        "CORE_FILL_N": summary.get("CORE_FILL_N"),
        "ADDED_FILL_N": summary.get("ADDED_FILL_N"),
        "EMA_STRUCTURE_EPISODES": summary.get("EMA_STRUCTURE_EPISODES"),
        "BB_STRUCTURE_EPISODES": summary.get("BB_STRUCTURE_EPISODES"),
        "ALL_PRIMITIVE_EPISODE_SUMMARY": summary.get("ALL_PRIMITIVE_EPISODE_SUMMARY"),
        "PATH_TYPE_DURATION_COMPARISON": summary.get("PATH_TYPE_DURATION_COMPARISON"),
        "PATH_TYPE_RECOVERY_COMPARISON": summary.get("PATH_TYPE_RECOVERY_COMPARISON"),
        "PATH_TYPE_PROPAGATION_COMPARISON": summary.get("PATH_TYPE_PROPAGATION_COMPARISON"),
        "CORE_STATE_SEQUENCE_SUMMARY": summary.get("CORE_STATE_SEQUENCE_SUMMARY"),
        "ADDED_STATE_SEQUENCE_SUMMARY": summary.get("ADDED_STATE_SEQUENCE_SUMMARY"),
        "PERSISTENCE_SUPPORTED": decision.get("PERSISTENCE_SUPPORTED"),
        "PROPAGATION_SUPPORTED": decision.get("PROPAGATION_SUPPORTED"),
        "NON_RECOVERY_SUPPORTED": decision.get("NON_RECOVERY_SUPPORTED"),
        "SUPPORTED_EXIT_STATE_MECHANISMS": decision.get("SUPPORTED_EXIT_STATE_MECHANISMS"),
        "PRIMARY_EXIT_STATE_MECHANISM": decision.get("PRIMARY_EXIT_STATE_MECHANISM"),
        "EXIT_POLICY_CREATED": False,
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
        "V26_VERDICT_FROZEN": V26_VERDICT_EXPECTED,
        "V26_SPEC_SHA256": V26_SPEC_SHA256_EXPECTED,
        "V27_SPEC_SHA256": v27_sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "Q1": decision.get("Q1_DURATION_SEPARATES"),
        "Q2": decision.get("Q2_PROPAGATION_SEPARATES"),
        "Q3": decision.get("Q3_RECOVERY_SEPARATES"),
        "Q4": decision.get("Q4_CORE_GOOD_NOT_DESTROYED"),
        "Q5": decision.get("Q5_SEQUENCE_MECHANISM"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "summary": {k: v for k, v in summary.items() if k != "tests"},
        "tests": summary.get("tests"),
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    test_sheet = []
    for t in list(summary.get("tests") or []):
        row = {
            "name": t.get("name"),
            "SUPPORTED": t.get("SUPPORTED"),
            "direction_ok": t.get("direction_ok"),
            "core_ok": t.get("core_ok"),
            "added_ok": t.get("added_ok"),
            "day_ok": t.get("day_ok"),
            "floors_ok": t.get("floors_ok"),
            "effect": t.get("effect"),
            "n": t.get("n"),
            "day": t.get("day"),
        }
        test_sheet.append(row)
    ep_sheet = []
    for p in list(summary.get("ALL_PRIMITIVE_EPISODE_SUMMARY") or []):
        ep_sheet.append(
            {
                "tf": p.get("tf"),
                "primitive": p.get("primitive"),
                "observable_n": p.get("observable_n"),
                "episode_trade_n": p.get("episode_trade_n"),
                "occurrence_rate": p.get("occurrence_rate"),
                "recovery_rate": p.get("recovery_rate"),
                "non_recovery_rate": p.get("non_recovery_rate"),
                "duration": p.get("duration"),
                "by_path": p.get("by_path"),
            }
        )
    write_artifacts(
        report,
        {
            "Precommit": kv_rows(
                {
                    "ANALYSIS_ID": ANALYSIS_ID,
                    "V27_SPEC_SHA256": v27_sha,
                    "V26_VERDICT_FROZEN": V26_VERDICT_EXPECTED,
                    "EXIT_POLICY_CREATED": False,
                    "BAR_COUNT_SEARCH": False,
                    "COMBINATION_SEARCH": False,
                    "PRIMARY_PRIMITIVES": "A_EMA_STRUCTURE_LOSS, D_BB_STRUCTURE_LOSS",
                }
            ),
            "Identity": kv_rows(
                {
                    "V26_FILL_IDENTITY_PARITY": decision.get("V26_FILL_IDENTITY_PARITY"),
                    "SIGNAL_SET_HASH": sig_hash,
                    "RESEARCH_FILL_SET_HASH": summary.get("RESEARCH_FILL_SET_HASH"),
                    "CORE_FILL_N": summary.get("CORE_FILL_N"),
                    "ADDED_FILL_N": summary.get("ADDED_FILL_N"),
                    "TOTAL_RESEARCH_FILL_N": summary.get("TOTAL_RESEARCH_FILL_N"),
                }
            ),
            "EpisodeSummary": ep_sheet or [{"empty": True}],
            "Duration": kv_rows(summary.get("PATH_TYPE_DURATION_COMPARISON") or {}),
            "Recovery": kv_rows(summary.get("PATH_TYPE_RECOVERY_COMPARISON") or {}),
            "Propagation": kv_rows(summary.get("PATH_TYPE_PROPAGATION_COMPARISON") or {}),
            "SupportTests": test_sheet or [{"empty": True}],
            "CoreVsAdded": kv_rows(
                {
                    "CORE": summary.get("CORE_STATE_SEQUENCE_SUMMARY"),
                    "ADDED": summary.get("ADDED_STATE_SEQUENCE_SUMMARY"),
                }
            ),
            "Decision": kv_rows(decision),
            "Reporting": kv_rows(reporting),
            "Integrity": kv_rows(leak),
            "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
        },
    )
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} FILLS={summary.get('TOTAL_RESEARCH_FILL_N')} "
        f"CORE={summary.get('CORE_FILL_N')} ADDED={summary.get('ADDED_FILL_N')} "
        f"PRIMARY={decision.get('PRIMARY_EXIT_STATE_MECHANISM')} "
        f"VERDICT={decision.get('VERDICT')} ni={ni_ok} out={V27_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    ok = decision.get("CASE") in {"A", "B"} and ni_ok and signal_parity and fill_identity
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
