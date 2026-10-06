"""Offline SIMPLE_TECH V29 terminal-failure sequence RCA. No Capture control. No EXIT policy. No K search."""
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
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics, set_hash, signal_tuples
from research.simple_tech_redesign.isolation import (
    TODAY,
    V22_OUT,
    V26_OUT,
    V27_OUT,
    V28_OUT,
    V29_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v24_harvest import V22_CACHE, load_v22_day_rows
from research.simple_tech_redesign.v27_analyze import research_fill_tuples
from research.simple_tech_redesign.v29_analyze import decide, summarize
from research.simple_tech_redesign.v29_harvest import (
    V29_CACHE,
    load_v29_day_cache,
    replay_sequence_day,
    save_v29_day_cache,
)
from research.simple_tech_redesign.v29_publish import REQUIRED_KEYS, build_markdown, build_sheets, write_artifacts
from research.simple_tech_redesign.v29_spec import (
    ANALYSIS_ID,
    ASK_FALLBACK_CHANGED,
    B1_SIGNAL_N_EXPECTED,
    CANONICAL_FRESHNESS_SEC,
    COMBINATION_SEARCH,
    E4_CHANGED,
    E4_WAIT_BUDGET_SEC,
    ENTRY_CHANGED,
    EXIT_POLICY_CREATED,
    FAMILY_B_SWING_AVAILABLE,
    FRESHNESS_THRESHOLD_CHANGED,
    K6_USED_FOR_SELECTION,
    K_SEARCH,
    PARENT_SPEC_SHA256_EXPECTED,
    PNL_SELECTION,
    PRIMITIVE_CHANGED,
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
    V27_SPEC_SHA256_FROZEN,
    V27_VERDICT_FROZEN,
    V28_K_ADOPTED,
    V28_SPEC_SHA256_EXPECTED,
    V28_VERDICT_EXPECTED,
    canonical_v29_spec,
    spec_sha256_v29,
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
    "K6_SELECTION_N",
    "BAR_COUNT_SEARCH_N",
    "COMBINATION_SEARCH_N",
    "PNL_SELECTION_N",
    "WAIT_EXTENSION_N",
    "REPRICE_N",
    "CHASE_N",
    "SECOND_FALLBACK_N",
    "FALLBACK_MARKET_N",
    "TIME_STOP_N",
    "EXIT_POLICY_N",
    "EXIT_COMBINATION_N",
    "MIXED_TF_RULE_N",
    "HH_HL_INVENTED_N",
    "V22_WRITE_N",
    "V26_WRITE_N",
    "V27_WRITE_N",
    "V28_WRITE_N",
)

FROZEN_OFFICIAL = (
    V26_OUT / "report.json",
    V26_OUT / "report.md",
    V26_OUT / "audit.xlsx",
    V27_OUT / "report.json",
    V27_OUT / "report.md",
    V27_OUT / "audit.xlsx",
    V28_OUT / "report.json",
    V28_OUT / "report.md",
    V28_OUT / "audit.xlsx",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _mtimes(paths: tuple[Path, ...]) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in paths:
        if p.is_file():
            out[str(p)] = float(p.stat().st_mtime)
    return out


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v29_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V29_INVALID",
            "TRUE_OOS": False,
            "ENTRY_CHANGED": False,
            "SIZING_CHANGED": False,
            "EXIT_POLICY_CREATED": False,
            "K6_USED_FOR_SELECTION": False,
            "NEXT": msg,
            "V27_FILL_IDENTITY_PARITY": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V29_SPEC_SHA256": v29_sha,
        }
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "summary": {},
        "decision": {"CASE": "E", "VERDICT": "SIMPLE_TECH_V29_INVALID", "NEXT": msg},
        "_markdown": build_markdown({"required": req, "decision": {"CASE": "E"}}),
    }
    write_artifacts(report, build_sheets(report, leak, {}))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v29_spec()
    v29_sha = spec_sha256_v29(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    frozen_mtime = _mtimes(FROZEN_OFFICIAL)
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v29_sequence={v29_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    if (
        ENTRY_CHANGED
        or E4_CHANGED
        or ASK_FALLBACK_CHANGED
        or FRESHNESS_THRESHOLD_CHANGED
        or PRIMITIVE_CHANGED
        or EXIT_POLICY_CREATED
        or SIZING_CHANGED
        or PNL_SELECTION
        or TIME_STOP_USED
        or K_SEARCH
        or COMBINATION_SEARCH
        or THRESHOLD_SEARCH
        or K6_USED_FOR_SELECTION
        or V28_K_ADOPTED
        or FAMILY_B_SWING_AVAILABLE
        or bool(TRUE_OOS)
        or RUNTIME_CANDIDATE
        or abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) > 1e-12
        or abs(float(E4_WAIT_BUDGET_SEC) - 5.0) > 1e-12
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)

    v26_req = dict(_load(V26_OUT / "report.json").get("required") or {})
    if str(v26_req.get("VERDICT") or "") != V26_VERDICT_FROZEN:
        return _stop("STOP. V26 official verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    if str(v26_req.get("V26_SPEC_SHA256") or "") != V26_SPEC_SHA256_FROZEN:
        return _stop("STOP. V26 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    v27_req = dict(_load(V27_OUT / "report.json").get("required") or {})
    if str(v27_req.get("VERDICT") or "") != V27_VERDICT_FROZEN:
        return _stop("STOP. V27 official verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    if str(v27_req.get("V27_SPEC_SHA256") or "") != V27_SPEC_SHA256_FROZEN:
        return _stop("STOP. V27 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    prim = dict(v27_req.get("PRIMARY_EXIT_STATE_MECHANISM") or {})
    if str(prim.get("name") or "") != V27_PRIMARY_EXPECTED:
        return _stop("STOP. V27 primary mechanism freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    v28_req = dict(_load(V28_OUT / "report.json").get("required") or {})
    if str(v28_req.get("VERDICT") or "") != V28_VERDICT_EXPECTED:
        return _stop("STOP. V28 official verdict freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    if str(v28_req.get("V28_SPEC_SHA256") or "") != V28_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V28 spec SHA freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [
            str(V22_CACHE),
            str(V22_OUT / "report.json"),
            str(V26_OUT / "report.json"),
            str(V27_OUT / "report.json"),
            str(V28_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)

    v22_by_day: dict[str, list[dict[str, Any]]] = {}
    v22_n = 0
    for cap in caps:
        day = str(cap["date"])
        day_rows = load_v22_day_rows(day)
        if not day_rows:
            return _stop(f"STOP. V22 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v29_sha=v29_sha)
        v22_by_day[day] = day_rows
        v22_n += len(day_rows)
    if v22_n != int(B1_SIGNAL_N_EXPECTED):
        return _stop(
            f"STOP. V22 signal n={v22_n} expected {B1_SIGNAL_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v29_sha=v29_sha,
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
            "V26_WRITE_N",
            "V27_WRITE_N",
            "V28_WRITE_N",
            "BOARD_EVENT_AFTER_EVENT_T_N",
        }
        for k, v in dict(body.get("leak") or {}).items():
            if not str(k).endswith("_N") or k in skip:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                leak[k] = int(leak.get(k) or 0) + int(v)
        leak["BOARD_EVENT_AFTER_EVENT_T_N"] = int(leak.get("BOARD_EVENT_AFTER_EVENT_T_N") or 0) + int(
            (body.get("leak") or {}).get("BOARD_EVENT_AFTER_EVENT_T_N") or 0
        )

    rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_sigs = v22_by_day[day]
        cache_path = V29_CACHE / f"day_{day}.json"
        cached = load_v29_day_cache(cache_path, v29_sha)
        if cached and int(cached.get("signal_n") or 0) == len(day_sigs) and len(list(cached.get("rows") or [])) == len(day_sigs):
            print(f"{day} v29 sequence cache hit rows={len(day_sigs)}", flush=True)
            rows.extend(list(cached.get("rows") or []))
            _absorb(cached)
            continue
        body = replay_sequence_day(
            {
                "date": day,
                "capture_path": cap.get("capture_path"),
                "signals": day_sigs,
                "spec_sha": v29_sha,
            }
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. Sequence replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v29_sha=v29_sha,
            )
        save_v29_day_cache(cache_path, body)
        rows.extend(list(body.get("rows") or []))
        _absorb(body)

    post_frozen = _mtimes(FROZEN_OFFICIAL)
    if post_frozen != frozen_mtime:
        for p, mt in post_frozen.items():
            if frozen_mtime.get(p) != mt:
                if "v26_" in p.replace("\\", "/"):
                    leak["V26_WRITE_N"] = int(leak.get("V26_WRITE_N") or 0) + 1
                elif "v27_" in p.replace("\\", "/"):
                    leak["V27_WRITE_N"] = int(leak.get("V27_WRITE_N") or 0) + 1
                elif "v28_" in p.replace("\\", "/"):
                    leak["V28_WRITE_N"] = int(leak.get("V28_WRITE_N") or 0) + 1

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
    decision = decide(
        summary,
        signal_parity=signal_parity,
        fill_identity=fill_identity,
        leak_ok=leak_ok,
        ni_ok=ni_ok,
    )
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "V27_FILL_IDENTITY_PARITY": decision.get("V27_FILL_IDENTITY_PARITY"),
        "V28_FILL_IDENTITY_PARITY": decision.get("V27_FILL_IDENTITY_PARITY"),
        "SIGNAL_N": summary.get("SIGNAL_N"),
        "EXECUTION_EVALUABLE_N": summary.get("EXECUTION_EVALUABLE_N"),
        "CORE_FILL_N": summary.get("CORE_FILL_N"),
        "ADDED_FILL_N": summary.get("ADDED_FILL_N"),
        "TOTAL_RESEARCH_FILL_N": summary.get("TOTAL_RESEARCH_FILL_N"),
        "DAMAGE_ONSET_N": summary.get("DAMAGE_ONSET_N"),
        "NO_DAMAGE_N": summary.get("NO_DAMAGE_N"),
        "K6_USED_FOR_SELECTION": False,
        "PNL_USED_FOR_SELECTION": False,
        "THRESHOLD_SEARCH": False,
        "COMBINATION_SEARCH": False,
        "FAMILY_B_SWING_AVAILABLE": summary.get("FAMILY_B_SWING_AVAILABLE"),
        "FAMILY_A_DIST": summary.get("FAMILY_A_DIST"),
        "FAMILY_B_DIST": summary.get("FAMILY_B_DIST"),
        "FAMILY_C_DIST": summary.get("FAMILY_C_DIST"),
        "SUPPORTED_SEQUENCES": decision.get("SUPPORTED_SEQUENCES"),
        "PRIMARY_EXIT_SEQUENCE_MECHANISM": decision.get("PRIMARY_EXIT_SEQUENCE_MECHANISM"),
        "MIXED_SEQUENCES": decision.get("MIXED_SEQUENCES"),
        "VS_V27_SIMPLE_RECOVERY": decision.get("VS_V27_SIMPLE_RECOVERY"),
        "CORE_DIRECTION_OK": decision.get("CORE_DIRECTION_OK"),
        "V28_TECH_EXIT_N": decision.get("V28_TECH_EXIT_N"),
        "V28_BY_PATH_N": decision.get("V28_BY_PATH_N"),
        "K6_RECOVERED_BEFORE_TRADE_N": decision.get("K6_RECOVERED_BEFORE_TRADE_N"),
        "K6_REACHED_N": summary.get("K6_REACHED_N"),
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
        "V26_VERDICT_FROZEN": V26_VERDICT_FROZEN,
        "V27_VERDICT_FROZEN": V27_VERDICT_FROZEN,
        "V28_VERDICT_FROZEN": V28_VERDICT_EXPECTED,
        "V26_SPEC_SHA256": V26_SPEC_SHA256_FROZEN,
        "V27_SPEC_SHA256": V27_SPEC_SHA256_FROZEN,
        "V28_SPEC_SHA256": V28_SPEC_SHA256_EXPECTED,
        "V29_SPEC_SHA256": v29_sha,
        "PARENT_SPEC_SHA256": parent_sha,
        "ORIGIN": "FIRST_3M_EMA_STRUCTURE_LOSS_ONSET",
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
    write_artifacts(report, build_sheets(report, leak, reporting))
    prim_name = (decision.get("PRIMARY_EXIT_SEQUENCE_MECHANISM") or {})
    if isinstance(prim_name, dict):
        prim_name = prim_name.get("seq_id")
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} FILLS={summary.get('TOTAL_RESEARCH_FILL_N')} "
        f"CORE={summary.get('CORE_FILL_N')} ADDED={summary.get('ADDED_FILL_N')} "
        f"DAMAGE={summary.get('DAMAGE_ONSET_N')} PRIMARY={prim_name} "
        f"VERDICT={decision.get('VERDICT')} ni={ni_ok} out={V29_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    ok = decision.get("CASE") in {"A", "B", "C"} and ni_ok and signal_parity and fill_identity
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
