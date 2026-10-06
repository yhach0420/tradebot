"""Offline SIMPLE_TECH V22 ENTRY coverage RCA. Frozen T3+E4. No ENTRY/EXIT/sizing change. No Capture control."""
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
from research.simple_tech_entry_family.harvest import load_day_cache, sealed_day_caps
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.isolation import V13_OUT
from research.simple_tech_entry_family.publish import kv_rows
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v6_harvest import V6_CACHE
from research.simple_tech_entry_family.v7_harvest import V7_CACHE
from research.simple_tech_entry_family.v8_harvest import V8_CACHE
from research.simple_tech_entry_family.v9_harvest import attach_bits
from research.simple_tech_entry_family.v11_harvest import is_b1
from research.simple_tech_entry_family.v12_harvest import V12_CACHE
from research.simple_tech_entry_family.v13_analyze import (
    eligible_tuples,
    fill_tuples,
    reporting_semantics,
    set_hash,
    signal_tuples,
)
from research.simple_tech_redesign.isolation import (
    TODAY,
    V22_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v22_analyze import (
    classify_deficiency,
    cohort_pack,
    eligible_mean_parity,
    fill_tuples_e4,
    funnel_counts,
    missed_opportunity_structure,
    tf_state_summary,
)
from research.simple_tech_redesign.v22_harvest import (
    V22_CACHE,
    load_v22_day_cache,
    replay_coverage_day,
    save_v22_day_cache,
)
from research.simple_tech_redesign.v22_publish import REQUIRED_KEYS, build_markdown, write_artifacts
from research.simple_tech_redesign.v22_spec import (
    B1_EXECUTABLE_N_EXPECTED,
    B1_MARKOUT_180_EXPECTED,
    B1_MARKOUT_300_EXPECTED,
    B1_MARKOUT_60_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    BB_CHANGED,
    BOARD_THRESHOLD_SEARCH,
    C14_USED,
    DEVELOPMENT_ENTRY_STACK,
    E4_FILL_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    E4_UNFILLED_N_EXPECTED,
    ELIGIBLE_SET_HASH_EXPECTED,
    EMA_CHANGED,
    ENTRY_CHANGED,
    EXIT_CHANGED,
    FIXED180_PNL_USED_FOR_ENTRY,
    NEW_INDICATOR,
    PARITY_ABS_TOL,
    PARENT_SPEC_SHA256_EXPECTED,
    POSITION_SIZING_SPEC_FROZEN,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    RUNTIME_CANDIDATE,
    SIGNAL_SET_HASH_EXPECTED,
    SIZING_CHANGED,
    SIZING_DIRECTION_LOCK,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V6_SPEC_SHA256_EXPECTED,
    V7_SPEC_SHA256_EXPECTED,
    V8_SPEC_SHA256_EXPECTED,
    V12_SPEC_SHA256_EXPECTED,
    V13_SPEC_SHA256_EXPECTED,
    VIRTUAL_FILL_COHORT_C,
    VOLUME_THRESHOLD_SEARCH,
    WAIT_SEARCH,
    canonical_v22_spec,
    spec_sha256_v22,
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
    "C14_REPLAY_N",
    "EXIT_SIM_N",
    "ENTRY_RULE_CHANGE_N",
    "THRESHOLD_SEARCH_N",
    "VIRTUAL_FILL_C_N",
    "FIXED180_PNL_N",
    "EMA_CHANGE_N",
    "BB_CHANGE_N",
    "RCI_CHANGE_N",
    "WAIT_SEARCH_N",
    "VOLUME_THRESHOLD_SEARCH_N",
    "BOARD_THRESHOLD_SEARCH_N",
    "NEW_INDICATOR_N",
    "SIZING_CHANGE_N",
    "V13_WRITE_N",
    "STRATEGY_V22_WRITE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _key(row: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(row.get("date") or ""),
        str(row.get("symbol") or "").replace(".T", ""),
        int(round(float(row.get("t0") or 0.0) * 1000.0)),
    )


def _close(a: Any, b: Any, tol: float = PARITY_ABS_TOL) -> bool:
    try:
        return abs(float(a) - float(b)) <= float(tol)
    except (TypeError, ValueError):
        return False


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v22_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": "SIMPLE_TECH_V22_ENTRY_COVERAGE_RCA",
            "VERDICT": "SIMPLE_TECH_V22_ENTRY_COVERAGE_RCA_INVALID",
            "PRIMARY_ENTRY_COVERAGE_DEFICIENCY": "DATA_EXECUTION_EVIDENCE_LIMITED",
            "CASE": "E",
            "NEXT": msg,
            "TRUE_OOS": False,
            "STRATEGY_STACK_PARITY": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V22_SPEC_SHA256": v22_sha,
        }
    )
    report = {
        "analysis_id": "SIMPLE_TECH_V22_ENTRY_COVERAGE_RCA",
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "_markdown": build_markdown({"required": req}),
    }
    write_artifacts(
        report,
        {"Precommit": kv_rows({"blocker": msg}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)},
    )
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v22_spec()
    v22_sha = spec_sha256_v22(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v22_coverage={v22_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or RUNTIME_CANDIDATE
        or POSITION_SIZING_SPEC_FROZEN
        or ENTRY_CHANGED
        or EXIT_CHANGED
        or SIZING_CHANGED
        or THRESHOLD_SEARCH
        or EMA_CHANGED
        or BB_CHANGED
        or RCI_CHANGED
        or WAIT_SEARCH
        or VOLUME_THRESHOLD_SEARCH
        or BOARD_THRESHOLD_SEARCH
        or NEW_INDICATOR
        or C14_USED
        or VIRTUAL_FILL_COHORT_C
        or FIXED180_PNL_USED_FOR_ENTRY
        or bool(TRUE_OOS)
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)

    v13_req = dict((_load(V13_OUT / "report.json").get("required") or {}))
    if str(v13_req.get("V13_SPEC_SHA256") or "") != V13_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V13 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if str(v13_req.get("DEVELOPMENT_ENTRY_STACK") or "") != DEVELOPMENT_ENTRY_STACK:
        return _stop("STOP. V13 entry stack mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [str(V8_CACHE), str(V12_CACHE), str(V7_CACHE), str(V13_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)

    v8_sigs: list[dict[str, Any]] = []
    v12_rows: list[dict[str, Any]] = []
    v12_by_day: dict[str, list[dict[str, Any]]] = {}
    v6_by: dict[tuple[str, str, int], dict[str, Any]] = {}
    v7_tf1: list[dict[str, Any]] = []
    mismatch = 0
    v7_ok = True
    for cap in caps:
        day = str(cap["date"])
        v8_body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not v8_body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
        v8_day: dict[tuple[str, str, int], dict[str, Any]] = {}
        for r in list(v8_body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            v8_day[_key(rec)] = rec
            if is_b1(rec):
                v8_sigs.append(rec)
        v6_body = load_day_cache(V6_CACHE / f"day_{day}.json", V6_SPEC_SHA256_EXPECTED)
        for r in list((v6_body or {}).get("rows") or []):
            v6_by[_key(r)] = r
        v7_body = load_day_cache(V7_CACHE / f"day_{day}.json", V7_SPEC_SHA256_EXPECTED)
        if not v7_body:
            v7_ok = False
        else:
            for r in list(v7_body.get("rows") or []):
                if str(r.get("tf") or "") == "TF1":
                    v7_tf1.append(r)
        v12_day = load_day_cache(V12_CACHE / f"day_{day}.json", V12_SPEC_SHA256_EXPECTED)
        if not v12_day:
            return _stop(f"STOP. V12 cache missing or SHA drifted {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
        day_rows = []
        for r in list(v12_day.get("rows") or []):
            rec = dict(r)
            extra = v8_day.get(_key(rec)) or {}
            v6 = v6_by.get(_key(rec)) or {}
            rec["trend"] = extra.get("trend", rec.get("trend"))
            rec["pullback"] = extra.get("pullback", rec.get("pullback"))
            rec["rci"] = extra.get("rci", rec.get("rci"))
            rec["pa"] = extra.get("pa", rec.get("pa"))
            rec["volume"] = extra.get("volume", rec.get("volume"))
            rec["board_ok"] = extra.get("board_ok", rec.get("board_ok"))
            rec["PQ1"] = v6.get("PQ1")
            rec["PQ3"] = extra.get("PQ3", v6.get("PQ3"))
            rec["TQ1"] = extra.get("TQ1", v6.get("TQ1"))
            rec["TQ2"] = extra.get("TQ2", v6.get("TQ2"))
            day_rows.append(rec)
        v12_by_day[day] = day_rows
        v12_rows.extend(day_rows)
    leak["TREND_BIT_MISMATCH_N"] = mismatch
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if len(v8_sigs) != int(B1_SIGNAL_N_EXPECTED) or len(v12_rows) != int(B1_SIGNAL_N_EXPECTED):
        return _stop(
            f"STOP. Signal n v8={len(v8_sigs)} v12={len(v12_rows)} expected {B1_SIGNAL_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v22_sha=v22_sha,
        )

    def _absorb_day_leak(body: dict[str, Any]) -> None:
        skip = set(INTEGRITY_ZERO) | {
            "ACTIVE_CAPTURE_INPUT_N",
            "RESEARCH_INPUT_ACTIVE_FILE_N",
            "TREND_BIT_MISMATCH_N",
        }
        for k, v in dict(body.get("leak") or {}).items():
            if not str(k).endswith("_N") or k in skip:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                leak[k] = int(leak.get(k) or 0) + int(v)

    replay_rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_rows = v12_by_day[day]
        cache_path = V22_CACHE / f"day_{day}.json"
        cached = load_v22_day_cache(cache_path, v22_sha)
        if cached and len(list(cached.get("rows") or [])) == len(day_rows):
            print(f"{day} v22 coverage cache hit rows={len(day_rows)}", flush=True)
            replay_rows.extend(list(cached.get("rows") or []))
            _absorb_day_leak(cached)
            continue
        body = replay_coverage_day(
            {
                "date": day,
                "capture_path": cap.get("capture_path"),
                "signals": day_rows,
                "spec_sha": v22_sha,
            }
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. Coverage replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v22_sha=v22_sha,
            )
        save_v22_day_cache(cache_path, body)
        replay_rows.extend(list(body.get("rows") or []))
        _absorb_day_leak(body)

    sig_v8 = signal_tuples(v8_sigs)
    sig_v12 = signal_tuples(v12_rows)
    sig_rep = signal_tuples(replay_rows)
    signal_parity = sig_v8 == sig_v12 == sig_rep
    exe_v12 = [r for r in v12_rows if r.get("executable_signal")]
    exe_rep = [r for r in replay_rows if r.get("executable_signal")]
    eligible_parity = len(exe_v12) == int(B1_EXECUTABLE_N_EXPECTED) == len(exe_rep)
    fills_v12 = fill_tuples(exe_v12)
    fills_rep = fill_tuples_e4(replay_rows)
    e4_fill_parity = len(fills_v12) == int(E4_FILLED_N_EXPECTED) == len(fills_rep) and fills_v12 == fills_rep
    sig_hash = set_hash(sig_v12)
    elig_hash = set_hash(eligible_tuples(v12_rows))
    fill_hash = set_hash(fills_v12)
    fill_hash_rep = set_hash(fills_rep)
    hash_ok = (
        sig_hash == SIGNAL_SET_HASH_EXPECTED
        and elig_hash == ELIGIBLE_SET_HASH_EXPECTED
        and fill_hash == E4_FILL_SET_HASH_EXPECTED
        and fill_hash_rep == E4_FILL_SET_HASH_EXPECTED
    )
    stack_parity = bool(
        signal_parity
        and eligible_parity
        and e4_fill_parity
        and hash_ok
        and len(replay_rows) == int(B1_SIGNAL_N_EXPECTED)
        and sum(1 for r in replay_rows if r.get("cohort") == "B") == int(E4_UNFILLED_N_EXPECTED)
    )
    means = eligible_mean_parity(replay_rows)
    markout_parity = bool(
        _close(means.get("ASK_BID_60_MEAN"), B1_MARKOUT_60_EXPECTED)
        and _close(means.get("ASK_BID_180_MEAN"), B1_MARKOUT_180_EXPECTED)
        and _close(means.get("ASK_BID_300_MEAN"), B1_MARKOUT_300_EXPECTED)
    )
    if int(leak.get("FUTURE_BOARD_N") or 0) > 0:
        markout_parity = False

    days = [str(c["date"]) for c in caps]
    funnel = funnel_counts(replay_rows)
    pack_a = cohort_pack(replay_rows, "A", days)
    pack_b = cohort_pack(replay_rows, "B", days)
    pack_c = cohort_pack(replay_rows, "C", days)
    miss = missed_opportunity_structure(v7_tf1, days) if v7_ok else {"Q3_NEAR_MISS_PULLBACK": False, "Q4_COMPLEMENTARY_ARCHETYPE": False}
    state_a = tf_state_summary([r for r in replay_rows if r.get("cohort") == "A"])
    state_b = tf_state_summary([r for r in replay_rows if r.get("cohort") == "B"])
    state_c = tf_state_summary([r for r in replay_rows if r.get("cohort") == "C"])
    identity_ok = bool(stack_parity and markout_parity)
    decision = classify_deficiency(
        identity_ok=identity_ok,
        funnel=funnel,
        pack_a=pack_a,
        pack_b=pack_b,
        pack_c=pack_c,
        miss=miss,
        v7_loaded=bool(v7_ok and v7_tf1),
    )
    if int(leak.get("VIRTUAL_FILL_C_N") or 0) or int(leak.get("FIXED180_PNL_N") or 0):
        decision["PRIMARY_ENTRY_COVERAGE_DEFICIENCY"] = "DATA_EXECUTION_EVIDENCE_LIMITED"
        decision["CASE"] = "E"
        decision["NEXT"] = "STOP. Virtual fill or 180 EXIT PnL leak."

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS") and stack_parity)

    primary = str(decision.get("PRIMARY_ENTRY_COVERAGE_DEFICIENCY") or "DATA_EXECUTION_EVIDENCE_LIMITED")
    verdict = f"SIMPLE_TECH_V22_{primary}"
    if not integ_ok or not identity_ok:
        verdict = "SIMPLE_TECH_V22_ENTRY_COVERAGE_RCA_INVALID"
        primary = "DATA_EXECUTION_EVIDENCE_LIMITED"
        decision["PRIMARY_ENTRY_COVERAGE_DEFICIENCY"] = primary
        decision["CASE"] = "E"
        decision["NEXT"] = "STOP. Identity or non-interference failed."

    req = {
        "ANALYSIS_ID": "SIMPLE_TECH_V22_ENTRY_COVERAGE_RCA",
        "STRATEGY_STACK_PARITY": bool(stack_parity),
        "SIGNAL_N": funnel.get("SIGNAL_N"),
        "EXECUTION_EVALUABLE_N": funnel.get("EXECUTION_EVALUABLE_N"),
        "EXECUTION_UNEVALUABLE_N": funnel.get("EXECUTION_UNEVALUABLE_N"),
        "E4_FILLED_N": funnel.get("E4_FILLED_N"),
        "E4_NONFILLED_N": funnel.get("E4_NONFILLED_N"),
        "FUNNEL_REASON_COUNTS": funnel.get("FUNNEL_REASON_COUNTS"),
        "UNEVAL_SPLIT": funnel.get("UNEVAL_SPLIT"),
        "E4_NONFILL_CLASS_COUNTS": funnel.get("E4_NONFILL_CLASS_COUNTS"),
        "ENTRY_STAGE_COUNTS": funnel.get("ENTRY_STAGE_COUNTS"),
        "FILLED_PATH_METRICS": pack_a.get("shape"),
        "NONFILLED_PATH_METRICS": pack_b.get("shape"),
        "1M_3M_5M_STATE_SUMMARY": {"A": state_a, "B": state_b, "C_T0_ONLY": state_c},
        "MISSED_OPPORTUNITY_STRUCTURE": miss,
        "PRIMARY_ENTRY_COVERAGE_DEFICIENCY": primary,
        "TRUE_OOS": False,
        "VERDICT": verdict,
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "SIGNAL_SET_HASH": sig_hash,
        "ELIGIBLE_SET_HASH": elig_hash,
        "E4_FILL_SET_HASH": fill_hash,
        "PARENT_SPEC_SHA256": parent_sha,
        "V22_SPEC_SHA256": v22_sha,
        "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
        "FIXED180_STATUS": "HISTORICAL_BENCHMARK_ONLY_NOT_FINAL_EXIT",
        "POSITION_SIZING_SPEC_FROZEN": False,
        "RUNTIME_CANDIDATE": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "SIZING_DIRECTION_LOCK": SIZING_DIRECTION_LOCK,
        "MARKOUT_PARITY": markout_parity,
        "ELIGIBLE_PATH_MEANS": means,
    }
    report = {
        "analysis_id": "SIMPLE_TECH_V22_ENTRY_COVERAGE_RCA",
        "required": req,
        "decision": decision,
        "cohort_a": pack_a,
        "cohort_b": pack_b,
        "cohort_c": pack_c,
        "funnel": funnel,
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
                    "ANALYSIS_ID": "SIMPLE_TECH_V22_ENTRY_COVERAGE_RCA",
                    "V22_SPEC_SHA256": v22_sha,
                    "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
                    "ENTRY_CHANGED": False,
                    "EXIT_CHANGED": False,
                    "THRESHOLD_SEARCH": False,
                }
            ),
            "Identity": kv_rows(
                {
                    "STRATEGY_STACK_PARITY": stack_parity,
                    "SIGNAL_SET_HASH": sig_hash,
                    "ELIGIBLE_SET_HASH": elig_hash,
                    "E4_FILL_SET_HASH": fill_hash,
                    "MARKOUT_PARITY": markout_parity,
                }
            ),
            "Funnel": kv_rows(funnel),
            "CohortA": kv_rows({k: pack_a.get(k) for k in ("COHORT", "N", "DAY_N", "PATH_COVERAGE_MIN", "PQ1_MEAN", "TQ1_MEAN")}),
            "CohortB": kv_rows({k: pack_b.get(k) for k in ("COHORT", "N", "DAY_N", "PATH_COVERAGE_MIN", "PQ1_MEAN", "TQ1_MEAN", "nonfill_class")}),
            "CohortC": kv_rows({k: pack_c.get(k) for k in ("COHORT", "N", "DAY_N", "uneval_class")}),
            "FilledPath": kv_rows({"shape": pack_a.get("shape")}),
            "NonfilledPath": kv_rows({"shape": pack_b.get("shape")}),
            "State1m3m5m": kv_rows({"A": state_a, "B": state_b, "C_T0_ONLY": state_c}),
            "MissedOpportunity": kv_rows(miss),
            "Decision": kv_rows(decision),
            "Reporting": kv_rows(reporting),
            "Integrity": kv_rows(leak),
            "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
        },
    )
    print(
        f"FINAL ANALYSIS_ID=SIMPLE_TECH_V22_ENTRY_COVERAGE_RCA "
        f"SIGNAL_N={funnel.get('SIGNAL_N')} EVAL={funnel.get('EXECUTION_EVALUABLE_N')} "
        f"FILLED={funnel.get('E4_FILLED_N')} NONFILL={funnel.get('E4_NONFILLED_N')} "
        f"PRIMARY={primary} VERDICT={verdict} ni={ni_ok} out={V22_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if ni_ok and identity_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
