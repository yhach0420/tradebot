"""Offline FDG relative-geometry migration Full Causal. One frozen strategy. Runtime 0/0/0."""
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
from research.full_causal_fdg_relative_geometry_migration_v1 import (
    ANALYSIS_ID,
    CANARY_ROW_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    PRIMARY_DECISION_UNIT,
    STRATEGY_ID,
    TRUE_OOS,
)
from research.full_causal_fdg_relative_geometry_migration_v1.analyze import (
    build_answers,
    decide,
    public_row,
    slim_trades,
    structural_coverage,
)
from research.full_causal_fdg_relative_geometry_migration_v1.harvest import AUDIT, harvest_development
from research.full_causal_fdg_relative_geometry_migration_v1.integrity import run_integrity
from research.full_causal_fdg_relative_geometry_migration_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.full_causal_fdg_relative_geometry_migration_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.full_causal_fdg_relative_geometry_migration_v1.spec import (
    already_executed_check,
    freeze_strategy_spec,
    pin_object_parent,
    pin_static_parent,
    source_sha256,
)
from research.full_causal_mechanism_discovery_v1.analyze import canary_parity, evaluate_strategy
from research.simple_tech_entry_family.portfolio import _sym

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "HOLDOUT_OPENED": False,
        "PAPER_20260907_READ": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "STATIC_SPAN_RETUNE": False,
        "SESSION_EXIT_FIX_AS_RESCUE": False,
        "POST_RESULT_GEOMETRY_CHANGE": False,
        "POST_RESULT_ENTRY_CHANGE": False,
        "POST_RESULT_EXIT_CHANGE": False,
        "POST_RESULT_CAP_CHANGE": False,
        "POST_RESULT_THRESHOLD_CHANGE": False,
        "POST_RESULT_WINDOW_CHANGE": False,
        "POST_RESULT_SECOND_CANDIDATE": False,
        "MULTI_CANDIDATE_SELECTION": False,
        "RAW_SIGNAL_SCREENING": False,
        "PREOPEN_EXECUTION_VALID": False,
    }


def _objective() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Freeze one translation-invariant deep-rank relative geometry migration Full Causal strategy "
            "and evaluate on sealed DEV."
        ),
        "PRIMARY_DECISION_UNIT": PRIMARY_DECISION_UNIT,
        "SELECTED_OBJECT_ID": "FULL_DEPTH_GEOMETRY",
        "NON_GOALS": [
            "static span rescue",
            "span ratio",
            "span threshold",
            "quantity imbalance",
            "weighted depth",
            "PBv2 revival",
            "IOAR revival",
            "UEIA revival",
            "Board Dynamic revival",
            "L1 price-movement strategy",
            "k-level search",
            "time-window search",
            "persistence search",
            "EXIT search",
            "CAP optimization",
            "Sizing",
            "future/OOS validation",
        ],
        "STOP_IF_GOAL_MISMATCH": True,
        "STATIC_SPAN_FAMILY_CLOSED": True,
    }


def _object_boundary() -> dict[str, Any]:
    return {
        "SELECTED_OBJECT_ID": "FULL_DEPTH_GEOMETRY",
        "CLOSED_STATIC_STRATEGY_ID": "FDG_ASK_SPAN_GT_BID_SPAN_ONSET_X1_Z_SPAN_NORMALIZE",
        "STATIC_SPAN_FAMILY_CLOSED": True,
        "MECHANISM": "successive-snapshot relative geometry migration",
        "FORBIDDEN": [
            "ASK_SPAN>BID_SPAN variants",
            "span ratio",
            "span threshold",
            "qty imbalance",
            "k subset",
            "magnitude threshold",
            "persistence N",
            "time window",
            "bid-only / ask-only",
            "L1 analogue",
        ],
        "DEEP_RANKS_REQUIRED": True,
        "MULTI_CANDIDATE_SELECTION": False,
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE FDG RELATIVE GEOMETRY MIGRATION V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        AUDIT["SPLIT_LEAKAGE_N"] += 1

    parent_object = pin_object_parent()
    parent_static = pin_static_parent()
    if not parent_object.get("ok"):
        raise RuntimeError(f"OBJECT_PARENT_PIN_FAIL {parent_object}")
    if not parent_static.get("ok"):
        raise RuntimeError(f"STATIC_PARENT_PIN_FAIL {parent_static}")
    spec = freeze_strategy_spec()
    spec_sha = str(spec["FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1"])
    reused = already_executed_check(spec_sha=spec_sha)
    print(f"FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1 {spec_sha}", flush=True)
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_RESULT=true", flush=True)
        print("STOP.", flush=True)
        return 0

    print("PHASE DEV relative-geometry harvest (no economics pack yet)", flush=True)
    har = harvest_development()
    semantic = dict(har.get("semantic") or {})
    mig_rows = list(har.get("mig_rows") or [])
    canary_rows = list(har.get("canary_rows") or [])
    struct = structural_coverage(mig_rows, semantic)
    print(
        f"STRUCT signals={struct.get('FALSE_TO_TRUE_SIGNAL_N')} days={struct.get('SIGNAL_DAY_N')} "
        f"symbols={struct.get('SIGNAL_SYMBOL_N')} SC={struct.get('STRUCTURAL_PASS')}",
        flush=True,
    )
    integ = run_integrity(
        object_sha=str(parent_object.get("INFORMATION_OBJECT_SPEC_SHA256") or ""),
        spec=spec,
        static_parent=parent_static,
        rows=mig_rows,
        harvest_ok=bool(har.get("ok")),
    )
    print(f"INTEGRITY {integ.get('PASS_N')}/{integ.get('TOTAL_N')} ALL_PASS={integ.get('ALL_PASS')}", flush=True)

    canary_pack: dict[str, Any] = {"CANARY_ID": CANARY_ROW_ID, "HARD_PASS": False}
    if integ.get("ALL_PASS") and har.get("ok"):
        print("PHASE CANARY R2_X1_Z3", flush=True)
        canary_ev = evaluate_strategy(
            CANARY_ROW_ID,
            canary_rows,
            days=list(DEVELOPMENT_DAYS),
            meta={"MECHANISM_ID": "O3_RECLAIM_ACCEPT_NEXT__R2", "OPERATOR": "O3_RECLAIM_ACCEPT_NEXT", "SELECTABLE": False},
            compute_g6=True,
            compute_blocks=False,
        )
        canary_pack = canary_parity(canary_ev)
        print(
            f"CANARY signal={canary_ev.get('signal_n')} trades={canary_ev.get('trade_n')} "
            f"pnl={canary_ev.get('TOTAL_PNL')} HARD_PASS={canary_pack.get('HARD_PASS')}",
            flush=True,
        )

    ev = None
    trades: list[dict[str, Any]] = []
    economics_opened = False
    if not integ.get("ALL_PASS"):
        decision = decide(
            integrity_pass=False,
            canary_ok=False,
            structural_pass=False,
            coverage_ok=False,
            g15=False,
            g6=None,
            s1=None,
            s2=None,
        )
    elif not canary_pack.get("HARD_PASS"):
        decision = decide(
            integrity_pass=True,
            canary_ok=False,
            structural_pass=False,
            coverage_ok=False,
            g15=False,
            g6=None,
            s1=None,
            s2=None,
        )
    elif not struct.get("STRUCTURAL_PASS"):
        decision = decide(
            integrity_pass=True,
            canary_ok=True,
            structural_pass=False,
            coverage_ok=False,
            g15=False,
            g6=None,
            s1=None,
            s2=None,
        )
    else:
        print("PHASE FDG migration Full Causal economics", flush=True)
        economics_opened = True
        ev = evaluate_strategy(
            STRATEGY_ID,
            mig_rows,
            days=list(DEVELOPMENT_DAYS),
            meta={"MECHANISM_ID": STRATEGY_ID, "OPERATOR": "FDG_MIGRATION", "SELECTABLE": True},
            compute_g6=False,
            compute_blocks=False,
        )
        trades = slim_trades(list(ev.get("_trades") or []))
        g = dict(ev.get("g_table") or {})
        g15 = bool(ev.get("coverage_ok") and g.get("G1") and g.get("G2") and g.get("G3") and g.get("G4") and g.get("G5"))
        g6_ok = None
        s1 = s2 = None
        if ev.get("coverage_ok") and g15:
            top = str(ev.get("top_symbol") or "")
            print(f"PHASE G6 causal ex-top1 remove {top} before migration-state generation", flush=True)
            har6 = harvest_development(exclude_symbol=top, emit_canary_rows=False)
            ev6 = evaluate_strategy(
                STRATEGY_ID,
                list(har6.get("mig_rows") or []),
                days=list(DEVELOPMENT_DAYS),
                meta={"MECHANISM_ID": STRATEGY_ID, "OPERATOR": "FDG_MIGRATION", "SELECTABLE": True},
                compute_g6=False,
                compute_blocks=False,
            )
            causal_pnl = ev6.get("TOTAL_PNL")
            ev["CAUSAL_EX_TOP1_PNL"] = causal_pnl
            ev["CAUSAL_EX_TOP1_TRADE_N"] = ev6.get("trade_n")
            g["G6"] = causal_pnl is not None and float(causal_pnl) >= 0.0
            ev["g_table"] = g
            g6_ok = bool(g["G6"])
            from research.full_causal_mechanism_discovery_v1.analyze import block_pack

            ev["blocks"] = block_pack(mig_rows)
            s1 = bool(ev["blocks"].get("S1"))
            s2 = bool(ev["blocks"].get("S2"))
        decision = decide(
            integrity_pass=True,
            canary_ok=True,
            structural_pass=True,
            coverage_ok=bool(ev.get("coverage_ok")),
            g15=bool(g15) if ev.get("coverage_ok") else False,
            g6=g6_ok,
            s1=s1,
            s2=s2,
        )
        if ev is not None:
            ev.pop("_trades", None)

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
        "parent_object": parent_object,
        "parent_static": parent_static,
        "object_boundary": _object_boundary(),
        "spec": spec,
        "hashes": {
            "FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1": spec_sha,
            "SOURCE_SHA256": source_sha256(),
            "INFORMATION_OBJECT_SPEC_SHA256": parent_object.get("INFORMATION_OBJECT_SPEC_SHA256"),
        },
        "structural": struct,
        "integrity": integ,
        "canary": canary_pack,
        "evaluated": public_row(ev) if economics_opened and ev is not None else {},
        "trades": trades if economics_opened else [],
        "decision": decision
        | {
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SIZING": False,
            "POST_RESULT_GEOMETRY_CHANGE": False,
            "POST_RESULT_ENTRY_CHANGE": False,
            "POST_RESULT_EXIT_CHANGE": False,
            "POST_RESULT_CAP_CHANGE": False,
            "POST_RESULT_THRESHOLD_CHANGE": False,
            "POST_RESULT_WINDOW_CHANGE": False,
            "POST_RESULT_SECOND_CANDIDATE": False,
            "MULTI_CANDIDATE_SELECTION": False,
            "STATIC_SPAN_RETUNE": False,
            "SESSION_EXIT_FIX_AS_RESCUE": False,
            "FORBIDDEN_TRANSFORMATION_USED_N": 0,
        },
        "safety": _safety() | {"harvest_AUDIT": dict(AUDIT)},
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "ECONOMICS_OPENED": economics_opened,
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(),
        "STRATEGY_FROZEN_BEFORE_ECONOMICS": True,
    }
    _ = _sym
    _publish(report)
    print(f"VERDICT {decision['VERDICT']}", flush=True)
    print(f"NEXT {decision['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
