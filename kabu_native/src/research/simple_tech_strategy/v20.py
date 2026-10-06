"""Offline SIMPLE_TECH V20 frozen-stack portfolio economics. No ENTRY/EXIT change. No C14. Sealed AM only."""
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
    PAPER_OPERATED,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
)
from research.simple_tech_entry_family.harvest import load_day_cache, sealed_day_caps
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.isolation import V8_OUT, V10_OUT, V13_OUT
from research.simple_tech_entry_family.publish import kv_rows
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
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
from research.simple_tech_exit_family.isolation import V18_OUT, V19_OUT
from research.simple_tech_exit_family.v14_analyze import entry_stack_ok
from research.simple_tech_exit_family.v14_harvest import _e4
from research.simple_tech_exit_family.v19_analyze import load_v18_official_trades
from research.simple_tech_strategy.isolation import (
    TODAY,
    V20_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_strategy.v20_analyze import (
    concentration_yen,
    decision_case,
    economics_gate,
    efficiency_pack,
    execution_audit,
    fill_input_hash,
    frequency_pack,
    numeric_lock,
    occupancy_audit,
    pnl_pack,
    stitch_mtm,
    v19_identity_parity,
)
from research.simple_tech_strategy.v20_harvest import (
    V18_CACHE,
    V19_CACHE,
    V20_CACHE,
    load_v19_day_trades,
    load_v20_day_cache,
    replay_v20_day,
    save_v20_day_cache,
)
from research.simple_tech_strategy.v20_publish import REQUIRED_KEYS, build_markdown, flatten_v20_trade, write_artifacts
from research.simple_tech_strategy.v20_spec import (
    ACTUAL_EXIT_SET_HASH_EXPECTED,
    ANALYSIS_ID,
    ANNUALIZE,
    ANNUALIZED,
    B1_EXECUTABLE_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    BB_RETUNE,
    BOARD_ADDED,
    C14_USED,
    DEVELOPMENT_ENTRY_STACK,
    DEVELOPMENT_EXIT_POLICY,
    DEVELOPMENT_STRATEGY_STACK,
    E4_FILL_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    E4_UNFILLED_N_EXPECTED,
    ELIGIBLE_SET_HASH_EXPECTED,
    EMA_RETUNE,
    ENTRY_CERTIFIED,
    ENTRY_CHANGED,
    EXIT_CERTIFIED,
    EXIT_CHANGED,
    FILL_EVIDENCE,
    GRID_SEARCH,
    HOLD_SEARCH,
    HOLD_SEC,
    ML_USED,
    NEW_INDICATOR,
    NEW_PROFIT_TARGET_GATE,
    PA_ADDED,
    PARENT_SPEC_SHA256_EXPECTED,
    POSITION_CAP_IN_FROZEN_STACK,
    PRE_FEE_PNL,
    PROFIT_TARGET,
    RCI_RETUNE,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    RUNTIME_CANDIDATE,
    SIGNAL_SET_HASH_EXPECTED,
    SPREAD_GATE,
    STOP_TRAILING,
    STRATEGY_CERTIFIED,
    SYMBOL_EXCLUSION,
    THRESHOLD_SEARCH,
    TOD_EXCLUSION,
    TRUE_OOS,
    V8_SPEC_SHA256_EXPECTED,
    V10_SPEC_SHA256_EXPECTED,
    V12_SPEC_SHA256_EXPECTED,
    V13_SPEC_SHA256_EXPECTED,
    V18_CACHE_COPY,
    V18_SPEC_SHA256_EXPECTED,
    V19_SPEC_SHA256_EXPECTED,
    V19_VERDICT_EXPECTED,
    VOLUME_ADDED,
    WAIT_CHANGED,
    canonical_v20_spec,
    spec_sha256_v20,
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
    "ENTRY_CHANGE_N",
    "EXIT_CHANGE_N",
    "HOLD_SEARCH_N",
    "POSITION_CAP_N",
    "SAME_SYMBOL_MERGE_N",
    "V18_CACHE_COPY_N",
    "FEE_ASSUMPTION_N",
    "ANNUALIZE_N",
    "THRESHOLD_SEARCH_N",
    "GRID_SEARCH_N",
    "ML_USE_N",
    "NEW_INDICATOR_N",
    "PROFIT_TARGET_N",
    "SYMBOL_EXCLUSION_N",
    "TOD_EXCLUSION_N",
    "SPREAD_GATE_N",
    "STOP_TRAILING_N",
    "WAIT_CHANGE_N",
    "V13_WRITE_N",
    "V18_WRITE_N",
    "V19_WRITE_N",
    "PRE_FILL_EVENT_N",
    "UNFILLED_VIRTUAL_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v20_sha: str, extra: dict[str, Any] | None = None) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V20_INVALID",
            "NEXT": msg,
            "TRUE_OOS": False,
            "STRATEGY_CERTIFIED": False,
            "DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS": False,
            "FORWARD_OOS_ELIGIBLE": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V20_SPEC_SHA256": v20_sha,
        }
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "extra": extra or {},
        "_markdown": build_markdown({"required": req}),
    }
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v20_spec()
    v20_sha = spec_sha256_v20(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    for k in (
        "TREND_BIT_MISMATCH_N",
        "MISSING_ENTRY_QUOTE_N",
        "MISSING_EXIT_QUOTE_N",
        "MISSING_ENTRY_QTY_N",
        "MISSING_EXIT_QTY_N",
        "TIMESTAMP_INVERSION_N",
        "FUTURE_USE_N",
        "ITAYOSE_SKIP_N",
        "SPECIAL_SKIP_N",
        "INVALID_SKIP_N",
        "FUTURE_QUOTE_N",
        "FILL_N",
    ):
        leak[k] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} v20={v20_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or ENTRY_CERTIFIED
        or EXIT_CERTIFIED
        or RUNTIME_CANDIDATE
        or STRATEGY_CERTIFIED
        or ENTRY_CHANGED
        or EXIT_CHANGED
        or HOLD_SEARCH
        or EMA_RETUNE
        or RCI_RETUNE
        or BB_RETUNE
        or BOARD_ADDED
        or VOLUME_ADDED
        or PA_ADDED
        or WAIT_CHANGED
        or SYMBOL_EXCLUSION
        or TOD_EXCLUSION
        or SPREAD_GATE
        or STOP_TRAILING
        or PROFIT_TARGET
        or NEW_PROFIT_TARGET_GATE
        or ANNUALIZE
        or ANNUALIZED
        or V18_CACHE_COPY
        or C14_USED
        or ML_USED
        or GRID_SEARCH
        or THRESHOLD_SEARCH
        or NEW_INDICATOR
        or POSITION_CAP_IN_FROZEN_STACK
        or float(HOLD_SEC) != 180.0
        or not PRE_FEE_PNL
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)

    if str((_load(V8_OUT / "report.json").get("required") or {}).get("V8_SPEC_SHA256") or "") != V8_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V8 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if str((_load(V10_OUT / "report.json").get("required") or {}).get("V10_SPEC_SHA256") or "") != V10_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V10 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    v13_req = dict((_load(V13_OUT / "report.json").get("required") or {}))
    if str(v13_req.get("V13_SPEC_SHA256") or "") != V13_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V13 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    stack_ok = entry_stack_ok(v13_req)
    if not stack_ok:
        return _stop("STOP. V13 development freeze stack mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    v18_req = dict((_load(V18_OUT / "report.json").get("required") or {}))
    if str(v18_req.get("V18_SPEC_SHA256") or "") != V18_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V18 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    v19_req = dict((_load(V19_OUT / "report.json").get("required") or {}))
    if str(v19_req.get("V19_SPEC_SHA256") or "") != V19_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V19 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if str(v19_req.get("VERDICT") or "") != V19_VERDICT_EXPECTED:
        return _stop("STOP. V19 official verdict mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if str(v19_req.get("DEVELOPMENT_STRATEGY_STACK") or "") != DEVELOPMENT_STRATEGY_STACK:
        return _stop("STOP. V19 strategy stack mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if str(v19_req.get("ACTUAL_EXIT_SET_HASH") or "") != ACTUAL_EXIT_SET_HASH_EXPECTED:
        return _stop("STOP. V19 ACTUAL_EXIT_SET_HASH mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    freeze_ok = bool(
        v19_req.get("ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT")
        and v19_req.get("ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT")
        and v19_req.get("EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT")
        and v19_req.get("EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT")
    )
    if not freeze_ok:
        return _stop("STOP. V19 development freeze flags not all true.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [
            str(V12_CACHE),
            str(V13_OUT / "report.json"),
            str(V18_OUT / "report.json"),
            str(V19_OUT / "report.json"),
            str(V19_OUT / "audit.xlsx"),
            str(V19_CACHE),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)

    v8_sigs: list[dict[str, Any]] = []
    v12_rows: list[dict[str, Any]] = []
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        v8_body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not v8_body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
        for r in list(v8_body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            if is_b1(rec):
                v8_sigs.append(rec)
        v12_day = load_day_cache(V12_CACHE / f"day_{day}.json", V12_SPEC_SHA256_EXPECTED)
        if not v12_day:
            return _stop(f"STOP. V12 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
        v12_rows.extend(list(v12_day.get("rows") or []))
    leak["TREND_BIT_MISMATCH_N"] = mismatch
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
    if len(v8_sigs) != int(B1_SIGNAL_N_EXPECTED) or len(v12_rows) != int(B1_SIGNAL_N_EXPECTED):
        return _stop("STOP. Signal n mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)

    exe = [r for r in v12_rows if r.get("executable_signal")]
    fills_src = [r for r in exe if _e4(r).get("filled")]
    unfilled_n = len(exe) - len(fills_src)
    sig_hash = set_hash(signal_tuples(v12_rows))
    elig_hash = set_hash(eligible_tuples(v12_rows))
    fill_hash = set_hash(fill_tuples(exe))
    sig_ok = sig_hash == SIGNAL_SET_HASH_EXPECTED and set_hash(signal_tuples(v8_sigs)) == SIGNAL_SET_HASH_EXPECTED
    elig_ok = elig_hash == ELIGIBLE_SET_HASH_EXPECTED and len(exe) == int(B1_EXECUTABLE_N_EXPECTED)
    fill_ok = fill_hash == E4_FILL_SET_HASH_EXPECTED and len(fills_src) == int(E4_FILLED_N_EXPECTED)
    if unfilled_n != int(E4_UNFILLED_N_EXPECTED):
        return _stop("STOP. Fill identity n mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)

    v19_official = load_v18_official_trades(V19_OUT / "audit.xlsx")
    if len(v19_official) != int(E4_FILLED_N_EXPECTED):
        return _stop(f"STOP. V19 official trades n={len(v19_official)}.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)

    path_rows: list[dict[str, Any]] = []
    mtm_days: list[tuple[str, list[dict[str, Any]]]] = []
    for cap in caps:
        day = str(cap["date"])
        v19_body = load_v19_day_trades(day)
        day_trades = list(v19_body.get("rows") or [])
        if not v19_body:
            return _stop(f"STOP. V19 independent cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
        cache_path = V20_CACHE / f"day_{day}.json"
        if V18_CACHE in cache_path.parents or cache_path.parent == V18_CACHE:
            leak["V18_CACHE_COPY_N"] = 1
            return _stop("STOP. V20 attempted V18 cache path.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
        cached = load_v20_day_cache(cache_path, v20_sha)
        if cached and len(list(cached.get("rows") or [])) == len(day_trades):
            print(f"{day} v20 cache hit fills={len(day_trades)}", flush=True)
            path_rows.extend(list(cached.get("rows") or []))
            mtm_days.append((day, list(cached.get("mtm_points") or [])))
            for k, v in dict(cached.get("leak") or {}).items():
                if k in leak:
                    leak[k] = int(leak.get(k) or 0) + int(v or 0)
            continue
        body = replay_v20_day(
            {"date": day, "capture_path": cap.get("capture_path"), "trades": day_trades, "spec_sha": v20_sha}
        )
        if not body.get("ok"):
            return _stop(f"STOP. V20 MTM harvest failed {day}: {body.get('blocker')}.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)
        save_v20_day_cache(cache_path, body)
        path_rows.extend(list(body.get("rows") or []))
        mtm_days.append((day, list(body.get("mtm_points") or [])))
        for k, v in dict(body.get("leak") or {}).items():
            if k in leak:
                leak[k] = int(leak.get(k) or 0) + int(v or 0)

    if len(path_rows) != int(E4_FILLED_N_EXPECTED):
        return _stop(f"STOP. Path rows {len(path_rows)} != {E4_FILLED_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v20_sha=v20_sha)

    ident = v19_identity_parity(path_rows, v19_official)
    input_hash = fill_input_hash(path_rows)
    policy_ok = str(v19_req.get("EXIT_POLICY") or v18_req.get("EXIT_POLICY") or "") == DEVELOPMENT_EXIT_POLICY
    stack_parity = bool(
        stack_ok
        and str(v19_req.get("DEVELOPMENT_STRATEGY_STACK") or "") == DEVELOPMENT_STRATEGY_STACK
        and fill_ok
        and ident.get("ACTUAL_EXIT_SET_HASH_PARITY")
        and ident.get("TRADE_BY_TRADE_V19_PARITY")
        and policy_ok
    )
    identity_ok = bool(stack_parity and sig_ok and elig_ok and fill_ok)

    occ = occupancy_audit(path_rows)
    if int(occ.get("SAME_SYMBOL_MERGE_N") or 0):
        leak["SAME_SYMBOL_MERGE_N"] = int(occ["SAME_SYMBOL_MERGE_N"])
    freq = frequency_pack(path_rows, list(ELIGIBLE_DAYS))
    pnl = pnl_pack(path_rows, list(ELIGIBLE_DAYS))
    conc = concentration_yen(path_rows, list(ELIGIBLE_DAYS))
    day_totals = {str(r["date"]): float(r["net_pnl"]) for r in list(conc.get("daily") or [])}
    mtm = stitch_mtm(mtm_days, day_totals)
    eff = efficiency_pack(pnl, occ, freq, path_rows)
    exe_audit = execution_audit(path_rows)
    lock = numeric_lock(pnl)

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS") and lock.get("V19_NUMERIC_LOCK") and ident.get("TRADE_BY_TRADE_V19_PARITY"))
    gate = economics_gate(pnl=pnl, conc=conc, occ=occ, integrity_ok=integ_ok)
    dec = decision_case(identity_ok=identity_ok, integrity_ok=integ_ok, occ=occ, pnl=pnl, gate=gate)

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
        "V18_SPEC_SHA256": V18_SPEC_SHA256_EXPECTED,
        "V19_SPEC_SHA256": V19_SPEC_SHA256_EXPECTED,
        "V20_SPEC_SHA256": v20_sha,
        "STRATEGY_STACK_PARITY": bool(stack_parity),
        "TRADE_N": pnl.get("TRADE_N"),
        "TRADES_PER_ELIGIBLE_DAY": freq.get("TRADES_PER_ELIGIBLE_DAY"),
        "ACTIVE_DAY_N": freq.get("ACTIVE_DAY_N"),
        "NO_TRADE_DAY_N": freq.get("NO_TRADE_DAY_N"),
        "MAX_CONCURRENT_POSITIONS": occ.get("MAX_CONCURRENT_POSITIONS"),
        "MEAN_CONCURRENT_WHEN_ACTIVE": occ.get("MEAN_CONCURRENT_WHEN_ACTIVE"),
        "SAME_SYMBOL_OVERLAP_N": occ.get("SAME_SYMBOL_OVERLAP_N"),
        "ENTRY_WHILE_SAME_SYMBOL_ALREADY_OPEN_N": occ.get("ENTRY_WHILE_SAME_SYMBOL_ALREADY_OPEN_N"),
        "PEAK_GROSS_NOTIONAL_YEN": occ.get("PEAK_GROSS_NOTIONAL_YEN"),
        "MEAN_GROSS_NOTIONAL_YEN": occ.get("MEAN_GROSS_NOTIONAL_YEN"),
        "PORTFOLIO_CONSTRUCTION_UNRESOLVED": occ.get("PORTFOLIO_CONSTRUCTION_UNRESOLVED"),
        "TOTAL_PNL_YEN_100": pnl.get("TOTAL_PNL_YEN_100"),
        "AVG_PNL_PER_TRADE": pnl.get("AVG_PNL_PER_TRADE"),
        "MEDIAN_PNL_PER_TRADE": pnl.get("MEDIAN_PNL_PER_TRADE"),
        "AVG_PNL_PER_ELIGIBLE_DAY": pnl.get("AVG_PNL_PER_ELIGIBLE_DAY"),
        "MEDIAN_DAILY_PNL": pnl.get("MEDIAN_DAILY_PNL"),
        "WIN_N": pnl.get("WIN_N"),
        "LOSS_N": pnl.get("LOSS_N"),
        "WIN_RATE": pnl.get("WIN_RATE"),
        "GROSS_PROFIT": pnl.get("GROSS_PROFIT"),
        "GROSS_LOSS": pnl.get("GROSS_LOSS"),
        "PF": pnl.get("PROFIT_FACTOR"),
        "REALIZED_MAX_DD": pnl.get("REALIZED_CLOSE_EQUITY_MAX_DD_YEN"),
        "MARK_TO_MARKET_MAX_DD": mtm.get("MARK_TO_MARKET_MAX_DD_YEN"),
        "NET_TO_MAX_DD": eff.get("NET_TO_MAX_DD"),
        "PNL_PER_TRADE": eff.get("PNL_PER_TRADE"),
        "PNL_PER_ELIGIBLE_DAY": eff.get("PNL_PER_ELIGIBLE_DAY"),
        "PNL_PER_ACTIVE_DAY": eff.get("PNL_PER_ACTIVE_DAY"),
        "PNL_PER_PEAK_NOTIONAL": eff.get("PNL_PER_PEAK_NOTIONAL"),
        "TURNOVER_YEN": eff.get("TURNOVER_YEN"),
        "CAPITAL_UTILIZATION": eff.get("CAPITAL_UTILIZATION"),
        "POSITIVE_DAY_N": conc.get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N": conc.get("NEGATIVE_DAY_N"),
        "BEST_DAY_PNL": conc.get("BEST_DAY_PNL"),
        "WORST_DAY_PNL": conc.get("WORST_DAY_PNL"),
        "EX_BEST_DAY_TOTAL_PNL": conc.get("EX_BEST_DAY_TOTAL_PNL"),
        "EX_TOP3_DAY_TOTAL_PNL": conc.get("EX_TOP3_DAY_TOTAL_PNL"),
        "LODO_MIN_TOTAL_PNL": conc.get("LODO_MIN_TOTAL_PNL"),
        "LODO_MEDIAN_TOTAL_PNL": conc.get("LODO_MEDIAN_TOTAL_PNL"),
        "TOP_SYMBOL_PNL_SHARE": conc.get("TOP_SYMBOL_PNL_SHARE"),
        "TOP3_SYMBOL_PNL_SHARE": conc.get("TOP3_SYMBOL_PNL_SHARE"),
        "TOP_DAY_PNL_SHARE": conc.get("TOP_DAY_PNL_SHARE"),
        "TOP3_DAY_PNL_SHARE": conc.get("TOP3_DAY_PNL_SHARE"),
        "DROP_TOP_SYMBOL_TOTAL_PNL": conc.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
        "DROP_TOP3_SYMBOL_TOTAL_PNL": conc.get("DROP_TOP3_SYMBOL_TOTAL_PNL"),
        "DROP_TOP_DAY_TOTAL_PNL": conc.get("DROP_TOP_DAY_TOTAL_PNL"),
        "DROP_TOP3_DAY_TOTAL_PNL": conc.get("DROP_TOP3_DAY_TOTAL_PNL"),
        "EXECUTION_EVIDENCE_LIMITED": exe_audit.get("EXECUTION_EVIDENCE_LIMITED"),
        "PRE_FEE_PNL": True,
        "FEE_MODEL": None,
        "DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS": dec.get("DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS"),
        "FORWARD_OOS_ELIGIBLE": dec.get("FORWARD_OOS_ELIGIBLE"),
        "ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT": True,
        "ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT": True,
        "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT": True,
        "EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT": True,
        "DEVELOPMENT_STRATEGY_STACK": DEVELOPMENT_STRATEGY_STACK,
        "CASE": dec.get("CASE"),
        "TRUE_OOS": bool(TRUE_OOS),
        "ENTRY_CERTIFIED": False,
        "EXIT_CERTIFIED": False,
        "STRATEGY_CERTIFIED": False,
        "RUNTIME_CANDIDATE": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": dec.get("VERDICT"),
        "NEXT": dec.get("NEXT"),
        "E4_FILL_SET_HASH": fill_hash,
        "ACTUAL_EXIT_SET_HASH": ident.get("ACTUAL_EXIT_SET_HASH"),
        "EXIT_INPUT_FILL_SET_HASH": input_hash,
        "FILL_EVIDENCE": FILL_EVIDENCE,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "identity": ident,
        "occupancy": occ,
        "frequency": freq,
        "pnl": pnl,
        "concentration": {k: v for k, v in conc.items() if k not in {"daily", "symbols", "lodo"}},
        "mtm": mtm,
        "efficiency": eff,
        "execution": exe_audit,
        "numeric_lock": lock,
        "gate": gate,
        "decision": dec,
        "reporting": reporting,
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH", "LIVE_PIDS")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH", "LIVE_PIDS")},
        "leak": leak,
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "true_oos": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "V20_SPEC_SHA256": v20_sha,
                "V19_SPEC_SHA256": V19_SPEC_SHA256_EXPECTED,
                "DEVELOPMENT_STRATEGY_STACK": DEVELOPMENT_STRATEGY_STACK,
                "PRE_FEE_PNL": True,
                "FEE_MODEL": None,
                "ANNUALIZED": False,
                "TRUE_OOS": False,
            }
        ),
        "Identity": kv_rows(
            {
                "STRATEGY_STACK_PARITY": stack_parity,
                "E4_FILL_SET_HASH_PARITY": fill_ok,
                "ACTUAL_EXIT_SET_HASH_PARITY": ident.get("ACTUAL_EXIT_SET_HASH_PARITY"),
                "TRADE_BY_TRADE_V19_PARITY": ident.get("TRADE_BY_TRADE_V19_PARITY"),
                "EXIT_MISMATCH_N": ident.get("EXIT_MISMATCH_N"),
                "FILLED_N": len(path_rows),
            }
        ),
        "Construction": kv_rows(occ),
        "Frequency": kv_rows({k: v for k, v in freq.items() if k != "daily_counts"}),
        "PnL": kv_rows(pnl),
        "Drawdown": kv_rows({**mtm, "REALIZED_CLOSE_EQUITY_MAX_DD_YEN": pnl.get("REALIZED_CLOSE_EQUITY_MAX_DD_YEN")}),
        "Efficiency": kv_rows(eff),
        "Days": list(conc.get("daily") or []) or [{"empty": True}],
        "Symbols": list(conc.get("symbols") or []) or [{"empty": True}],
        "LODO": list(conc.get("lodo") or []) or [{"empty": True}],
        "Execution": kv_rows(exe_audit),
        "Trades": [flatten_v20_trade(r) for r in path_rows] or [{"empty": True}],
        "Reporting": kv_rows(reporting),
        "Integrity": kv_rows(leak),
        "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
    }
    write_artifacts(report, sheets)
    if str((_load(V13_OUT / "report.json").get("required") or {}).get("DEVELOPMENT_ENTRY_STACK") or "") != DEVELOPMENT_ENTRY_STACK:
        print("STOP. V13 official stack mutated.", flush=True)
        return 2
    v19_after = _load(V19_OUT / "report.json")
    if str((v19_after.get("required") or {}).get("V19_SPEC_SHA256") or "") != V19_SPEC_SHA256_EXPECTED:
        print("STOP. V19 official report mutated.", flush=True)
        return 2
    if str((v19_after.get("required") or {}).get("VERDICT") or "") != V19_VERDICT_EXPECTED:
        print("STOP. V19 official verdict mutated.", flush=True)
        return 2
    print(
        f"DONE verdict={req.get('VERDICT')} case={req.get('CASE')} "
        f"pnl={req.get('TOTAL_PNL_YEN_100')} concurrent={req.get('MAX_CONCURRENT_POSITIONS')} "
        f"unresolved={req.get('PORTFOLIO_CONSTRUCTION_UNRESOLVED')} ni={ni_ok} out={V20_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
