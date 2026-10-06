"""Offline ISQ resolution Full Causal. LEGACY_DEV only. Runtime 0/0/0."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.harvest import harvest_development as harvest_canary
from research.full_causal_mechanism_discovery_v1.analyze import canary_parity, evaluate_strategy, public_row
from research.intraday_special_quote_resolution_full_strategy_v1 import (
    ANALYSIS_ID,
    CANARY_ROW_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    MAX_RESEARCH_DATE,
    OPENING_LINE_STATUS,
    POSITION_CAP_N,
    TRUE_OOS,
)
from research.intraday_special_quote_resolution_full_strategy_v1.analyze import (
    build_answers,
    decide,
    extra_counts,
    selection_key,
)
from research.intraday_special_quote_resolution_full_strategy_v1.duplicates import audit_duplicates
from research.intraday_special_quote_resolution_full_strategy_v1.harvest import harvest_isq
from research.intraday_special_quote_resolution_full_strategy_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.intraday_special_quote_resolution_full_strategy_v1.library import (
    candidate_meta,
    freeze_library_sha,
    freeze_strategy_sha,
    library_spec,
)
from research.intraday_special_quote_resolution_full_strategy_v1.publish import (
    SHEET_ORDER,
    _episode_row,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.intraday_special_quote_resolution_full_strategy_v1.semantics import prove_market_states
from research.intraday_special_quote_resolution_full_strategy_v1.spec import already_executed_check, pin_parent, source_sha256
from research.intraday_special_quote_resolution_full_strategy_v1.synthetic import run_synthetic_tests
from research.simple_tech_entry_family.portfolio import _sym

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "RUNTIME_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "PAPER_CHANGED": False,
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "QUARANTINE_OPENED": False,
        "PAPER_20260907_READ": False,
        "MBO_WORK": False,
        "FUTURES_WORK": False,
        "EXTERNAL_ACQUISITION": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "POST_RESULT_ENTRY_CHANGE": False,
        "POST_RESULT_EXIT_CHANGE": False,
        "POST_RESULT_THRESHOLD_CHANGE": False,
        "EXTRA_CANDIDATE_ADDED": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Complete Full Causal strategies from undirected intraday special-quote resolution "
            "using proven SPECIAL_QUOTE and CONTINUOUS_TRADING only. LEGACY_DEV only."
        ),
        "PRIMARY_DECISION_UNIT": "COMPLETE_FULL_CAUSAL_STRATEGY",
        "PARENT": "DELAYED_OPEN_BUY_SPECIAL_QUOTE_RELEASE_FULL_STRATEGY_V1",
        "ARCHITECTURE": "NON_OPENING POST_OPEN INTRADAY_SPECIAL_QUOTE_RESOLUTION",
        "OPENING_CURRENT_DATA_LINE_STATUS": OPENING_LINE_STATUS,
        "NON_GOALS": [
            "Opening",
            "preopen",
            "GU/GD",
            "Sign direction",
            "MBO",
            "futures",
            "Sizing",
            "EXIT search",
            "future validation",
        ],
        "STOP_IF_GOAL_MISMATCH": True,
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    for ev in list(report.get("candidate_evals") or []):
        ev.pop("_trades", None)
    write_artifacts(report, sheets)


def _g15(ev: dict[str, Any]) -> bool:
    g = dict(ev.get("g_table") or {})
    return bool(g.get("G1") and g.get("G2") and g.get("G3") and g.get("G4") and g.get("G5"))


def apply_causal_g6(ev: dict[str, Any], *, g6_cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    top = ev.get("top_symbol")
    g = dict(ev.get("g_table") or {})
    if not top:
        g["G6"] = False
        ev["g_table"] = g
        ev["CAUSAL_EX_TOP1_PNL"] = None
        ev["CAUSAL_EX_TOP1_METHOD"] = "NO_TOP_SYMBOL"
        ev["BASE_QUALIFIED"] = False
        return ev
    key = _sym({"symbol": top})
    if key not in g6_cache:
        print(f"PHASE G6 HARVEST exclude={key}", flush=True)
        har = harvest_isq(exclude_symbol=key)
        if not har.get("ok"):
            g["G6"] = False
            ev["g_table"] = g
            ev["G6_BLOCKER"] = har.get("blocker")
            ev["BASE_QUALIFIED"] = False
            return ev
        g6_cache[key] = har
    rows = list((g6_cache[key].get("rows_by") or {}).get(str(ev["STRATEGY_ID"])) or [])
    c_ev = evaluate_strategy(str(ev["STRATEGY_ID"]), rows, days=list(DEVELOPMENT_DAYS), compute_g6=False, compute_blocks=False)
    causal_pnl = float(c_ev.get("TOTAL_PNL") or 0.0)
    g["G6"] = causal_pnl >= 0.0
    ev["g_table"] = g
    ev["CAUSAL_EX_TOP1_PNL"] = causal_pnl
    ev["CAUSAL_EX_TOP1_PF"] = c_ev.get("PF")
    ev["CAUSAL_EX_TOP1_METHOD"] = "UNIVERSE_REMOVAL_BEFORE_EPISODE"
    blocks = ev.get("blocks") or {}
    ev["BASE_QUALIFIED"] = bool(
        ev.get("coverage_ok")
        and all(g.get(k) for k in ("G1", "G2", "G3", "G4", "G5", "G6"))
        and blocks.get("S1")
        and blocks.get("S2")
    )
    return ev


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 LEGACY_DEV ISQ RESOLUTION NO MBO NO FUTURE", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)

    reused = already_executed_check()
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_RESULT=true", flush=True)
        print("STOP.", flush=True)
        return 0

    parent = pin_parent()
    if not parent.get("ok"):
        raise RuntimeError(f"PARENT_PIN_FAIL {parent}")

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )

    dup = audit_duplicates()
    print("PHASE DUPLICATE eligible", dup.get("ELIGIBLE_CANDIDATE_IDS"), flush=True)
    spec = library_spec()
    lib_sha = freeze_library_sha(spec)
    precommit = {
        "ISQ_RESOLUTION_LIBRARY_SHA256": lib_sha,
        "PRECOMMIT_BEFORE_COUNTS": True,
        "PRECOMMIT_BEFORE_ECONOMICS": True,
        "SPEC": spec,
        "FROZEN": True,
    }
    print("PHASE PRECOMMIT", lib_sha, flush=True)

    tests = run_synthetic_tests()
    print("PHASE TESTS", tests.get("PASS_N"), "/30", flush=True)
    canary: dict[str, Any] = {}
    canary_ok = False
    if tests.get("ALL_PASS") and (dup.get("ELIGIBLE_CANDIDATE_IDS") or []):
        print("PHASE CANARY R2_X1_Z3", flush=True)
        har_c = harvest_canary()
        if not har_c.get("ok"):
            raise RuntimeError(f"CANARY_HARVEST_FAIL {har_c.get('blocker')}")
        canary_ev = evaluate_strategy(
            CANARY_ROW_ID,
            list((har_c.get("rows_by") or {}).get(CANARY_ROW_ID) or []),
            days=list(DEVELOPMENT_DAYS),
            meta={"SELECTABLE": False},
            compute_g6=False,
            compute_blocks=False,
        )
        canary = canary_parity(canary_ev)
        canary_ok = bool(canary.get("HARD_PASS"))

    proven = prove_market_states()
    raw_dates: list[str] = []
    structural: dict[str, Any] = {}
    integrity_n: int | None = None
    evals: list[dict[str, Any]] = []
    selected: dict[str, Any] = {}
    selection: dict[str, Any] = {}
    episodes_out: list[dict[str, Any]] = []
    signals_out: list[dict[str, Any]] = []
    coverage_any: bool | None = None
    robust_n: int | None = None

    stop_early = decide(dup=dup, tests_ok=bool(tests.get("ALL_PASS")), canary_ok=canary_ok)
    if stop_early["CASE"] in ("DUP", "TESTS", "PARITY"):
        decision = stop_early
    else:
        print("PHASE HARVEST ISQ", flush=True)
        har = harvest_isq()
        if not har.get("ok"):
            raise RuntimeError(f"ISQ_HARVEST_FAIL {har.get('blocker')}")
        raw_dates = list(DEVELOPMENT_DAYS)
        structural = dict(har.get("structural") or {})
        integrity_n = int(har.get("integrity_n") or 0)
        episodes_out = [_episode_row(e) for e in list(har.get("episodes") or [])]
        signals_out = list(har.get("signals") or [])
        if integrity_n > 0:
            decision = decide(dup=dup, tests_ok=True, canary_ok=True, integrity_n=integrity_n)
        else:
            g6_cache: dict[str, dict[str, Any]] = {}
            for cid in list(dup.get("ELIGIBLE_CANDIDATE_IDS") or []):
                rows = list((har.get("rows_by") or {}).get(cid) or [])
                ev = evaluate_strategy(
                    cid,
                    rows,
                    days=list(DEVELOPMENT_DAYS),
                    meta=candidate_meta(cid),
                    compute_g6=False,
                    compute_blocks=True,
                )
                extra = extra_counts(ev, rows)
                ev.update(extra)
                if ev.get("coverage_ok") and _g15(ev):
                    ev = apply_causal_g6(ev, g6_cache=g6_cache)
                else:
                    g = dict(ev.get("g_table") or {})
                    g["G6"] = None
                    ev["g_table"] = g
                    ev["BASE_QUALIFIED"] = False
                evals.append(ev)
                print(
                    f"EVAL {cid} trade={ev.get('trade_n')} cov={ev.get('coverage_ok')} "
                    f"pnl={ev.get('TOTAL_PNL')} robust={ev.get('BASE_QUALIFIED')}",
                    flush=True,
                )
            coverage_any = any(bool(e.get("coverage_ok")) for e in evals)
            robust = [e for e in evals if e.get("BASE_QUALIFIED")]
            robust_n = len(robust)
            if robust:
                robust.sort(key=selection_key)
                winner = robust[0]
                meta = candidate_meta(str(winner["STRATEGY_ID"]))
                selected = {
                    **meta,
                    "STRATEGY_ID": winner["STRATEGY_ID"],
                    "FULL_STRATEGY_SPEC_SHA256_ISQ_FINAL": freeze_strategy_sha(str(winner["STRATEGY_ID"]), lib_sha),
                    "LIBRARY_SHA": lib_sha,
                    "LOGIC_COMPLETE": True,
                    "CLASSIFICATION": "LEGACY_DEV_CONSTRUCTED_CANDIDATE",
                    "TRUE_OOS": False,
                    "CERTIFIED": False,
                }
                selection = {
                    "ROBUST_N": robust_n,
                    "SELECTED": winner["STRATEGY_ID"],
                    "TIEBREAK": "min_block, causal_ex_top1, EX_BEST_DAY, TOTAL_PNL, PF, abs MaxDD, single-thesis, lex ID",
                }
            decision = decide(
                dup=dup,
                tests_ok=True,
                canary_ok=True,
                integrity_n=integrity_n,
                coverage_any=coverage_any,
                robust_n=robust_n,
            )

    after = snapshot(phase="POST")
    if stress_path_touch_n([OUT, CACHE]) or holdout_path_touch_n([OUT, CACHE]):
        raise RuntimeError("SEALED_PATH_TOUCH")

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "REUSED_EXISTING_RESULT": False,
        "objective_alignment": _objective(),
        "parent": parent,
        "hashes": {"SOURCE_SHA256": source_sha256()},
        "data_boundary": {
            "ALLOWED": list(DEVELOPMENT_DAYS),
            "RAW_MARKET_DATES_OPENED": list(raw_dates) if raw_dates else (list(DEVELOPMENT_DAYS) if canary_ok else []),
            "HOLDOUT_READ": False,
            "STRESS_READ": False,
            "QUARANTINE_READ": False,
            "FUTURE_READ": False,
            "NOTE": "Canary reused sealed LEGACY_DEV harvest cache. ISQ harvest is a separate AM stream.",
        },
        "duplicate_check": dup,
        "state_semantics": proven,
        "trade_update_semantics": {
            "OBSERVED_TRADE_UPDATE": "TradingVolume finite and > LAST_SEEN AND CurrentPrice finite > 0",
            "CURRENT_PRICE_TIME_USED_AS_TRADE_ID": False,
            "CURRENT_PRICE_TIME_USED_AS_AVAILABILITY": False,
            "AVAILABILITY_CLOCK": "INGRESS",
        },
        "trade_bar_semantics": {
            "BAR": "ISQ_OBSERVED_TRADE_BAR_1M",
            "BUCKET": "JST 1-minute ingress-time",
            "CARRY_FORWARD_CURRENT_PRICE": False,
            "ONLY_OBSERVED_TRADE_UPDATE": True,
        },
        "unit_tests": tests,
        "canary": canary,
        "precommit": precommit,
        "structural": structural,
        "integrity": {
            "AFFECTED_ISQ_DATA_INTEGRITY_ERROR_N": integrity_n,
            "REQUIRE_ZERO_BEFORE_ECONOMICS": True,
        },
        "episodes": episodes_out,
        "signals": signals_out,
        "candidate_evals": evals,
        "execution": {"ID": EXECUTION_ID, "ENGINE": "canonical evaluate_execution X1", "USED": bool(evals)},
        "portfolio": {"CAP": int(POSITION_CAP_N), "USED": bool(evals)},
        "selection": selection,
        "selected_logic": selected,
        "decision": decision
        | {
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SIZING": False,
            "MBO_WORK": False,
            "WRITE_OVERLAP_N": overlap,
            "POST_RESULT_ENTRY_CHANGE": False,
            "POST_RESULT_EXIT_CHANGE": False,
        },
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    _publish(report)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
