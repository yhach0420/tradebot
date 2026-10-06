"""Offline X1 vs W5 execution-tax decomposition. LEGACY_DEV only. Runtime 0/0/0."""
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
from research.post_open_causal_upside_mechanism_discovery_v1 import DEVELOPMENT_DAYS, FEATURE_IDS, MAX_RESEARCH_DATE
from research.post_open_execution_tax_decomposition_v1 import (
    ANALYSIS_ID,
    CASE_IDENTITY,
    CASE_PARITY,
    CERTIFIED,
    KIND,
    TRUE_OOS,
)
from research.post_open_execution_tax_decomposition_v1.analyze import (
    block_comparison,
    common_endpoint,
    decide,
    fit_tree_w5,
    interactions_w5,
    lobo_w5,
    populations,
    selection_effect,
    univariate_w5,
)
from research.post_open_execution_tax_decomposition_v1.harvest import load_parent_population, overlay_w5
from research.post_open_execution_tax_decomposition_v1.interpret import interpret
from research.post_open_execution_tax_decomposition_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.post_open_execution_tax_decomposition_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.post_open_execution_tax_decomposition_v1.spec import (
    already_executed_check,
    parent_row_parity,
    pin_parent,
    pin_w5,
    source_sha256,
)

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
        "V5_RESCUE": False,
        "NEW_ENTRY": False,
        "CAP5": False,
        "OCCUPANCY": False,
        "REPRICE": False,
        "CHASE": False,
        "FALLBACK_ASK": False,
        "SYNTHETIC_FILL": False,
        "NONFILL_AS_PNL": False,
        "CANDIDATE_STRATEGY_N": 0,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "FULL_CAUSAL_ECONOMICS_THIS_RUN": False,
        "MBO_WORK": False,
        "FUTURES_WORK": False,
        "SIZING": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    pops = dict(report.get("populations") or {})
    p0 = dict(pops.get("P0") or {})
    p1 = dict(pops.get("P1") or {})
    p2 = dict(pops.get("P2") or {})
    sel = dict(report.get("selection") or {})
    blocks = dict(report.get("blocks") or {})
    tree = dict(report.get("tree") or {})
    leaves = [L for L in list(tree.get("leaves") or []) if L.get("qualifying")]
    leaf = leaves[0] if leaves else {}
    lobo_pack = dict(report.get("lobo") or {})
    interp = dict(report.get("interpretation") or {})
    d = dict(report.get("decision") or {})
    w5 = dict(report.get("w5_pin") or {})
    par = dict(report.get("parent_parity") or {})
    uni = list(report.get("univariate") or [])
    return {
        "1_parent_parity": par.get("ok"),
        "2_exact_days": list(DEVELOPMENT_DAYS),
        "3_future_read": False,
        "4_X1_identity": True,
        "5_W5_identity_proven": w5.get("ok"),
        "6_W5_source_SHA": w5.get("source_sha256"),
        "7_W5_fill_N_rate": {"n": pops.get("W5_fill_n"), "rate": pops.get("W5_fill_rate")},
        "8_W5_fill_day_N": pops.get("W5_fill_day_n"),
        "9_W5_fill_symbol_N": pops.get("W5_fill_symbol_n"),
        "10_P0_mean_median": {"mean": p0.get("mean"), "median": p0.get("median")},
        "11_P1_mean_median": {"mean": p1.get("mean"), "median": p1.get("median")},
        "12_P2_mean_median": {"mean": p2.get("mean"), "median": p2.get("median")},
        "13_P2_minus_P1_mean_bps": pops.get("P2_minus_P1_mean"),
        "14_P2_minus_P1_median_bps": pops.get("P2_minus_P1_median"),
        "15_W5_FILLED_X1_markout": (sel.get("W5_FILLED_X1") or {}).get("mean"),
        "16_W5_NONFILLED_X1_markout": (sel.get("W5_NOT_FILLED_X1") or {}).get("mean"),
        "17_passive_adverse_selection": sel.get("label"),
        "18_B1_B5_P1": {k: v.get("P1_mean") for k, v in dict(blocks.get("blocks") or {}).items()},
        "19_B1_B5_P2": {k: v.get("P2_mean") for k, v in dict(blocks.get("blocks") or {}).items()},
        "20_positive_P2_block_N": blocks.get("positive_P2_block_n"),
        "21_execution_alone_flips_population": d.get("EXECUTION_TAX_CAN_FLIP_POPULATION_EDGE"),
        "22_W5_stable_univariate": [u.get("feature") for u in uni if u.get("stable")],
        "23_best_2way": (list(report.get("interactions") or [])[:1] or [None])[0],
        "24_tree_rules": tree.get("rules") if tree.get("ok") else tree,
        "25_qualifying_leaf_N": tree.get("qualifying_leaf_n"),
        "26_leaf_mean_median": {"mean": leaf.get("mean_markout"), "median": leaf.get("median_markout")},
        "27_leaf_B1_B5": leaf.get("block_mean_markout"),
        "28_top_symbol_exclusion": leaf.get("top_symbol_excluded_mean_ge0"),
        "29_LOBO_positive_fold_N": lobo_pack.get("confirm_positive_fold_n"),
        "30_immediate_Ask_tax_material": (pops.get("P2_minus_P1_mean") or 0) > 1.0,
        "31_Capture_directional_information_recoverable": str(d.get("CASE") or "") == "A",
        "32_ENTRY_thesis_derivable": bool(interp.get("ENTRY_THESIS")),
        "33_Technical_EXIT_derivable": bool(interp.get("TECHNICAL_EXIT")),
        "34_strategy_precommitted": False,
        "35_TRUE_OOS": False,
        "36_CERTIFIED": False,
        "37_VERDICT": d.get("VERDICT"),
        "38_NEXT": d.get("NEXT"),
        "feature_n": len(FEATURE_IDS),
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def _stop_report(*, parent: dict[str, Any], w5: dict[str, Any], verdict: str, nxt: str, note: str, before: dict[str, Any], after: dict[str, Any], overlap: int) -> dict[str, Any]:
    decision = {
        "CASE": "STOP",
        "VERDICT": verdict,
        "NEXT": nxt,
        "NOTE": note,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "CANDIDATE_STRATEGY_N": 0,
        "PRECOMMIT_THIS_RUN": False,
        "WRITE_OVERLAP_N": overlap,
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "KIND": KIND,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "parent": parent,
        "w5_pin": w5,
        "parent_parity": {"ok": False, "note": note},
        "populations": {},
        "decision": decision,
        "interpretation": {"MARKET_MECHANISM": note, "ENTRY_THESIS": None, "TECHNICAL_EXIT": None},
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(),
        "hashes": {"SOURCE_SHA256": source_sha256()},
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 EXECUTION TAX DECOMPOSITION NO NEW ENTRY", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)

    reused = already_executed_check()
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_RESULT=true", flush=True)
        print("STOP.", flush=True)
        return 0

    parent = pin_parent()
    w5 = pin_w5()
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if not parent.get("ok"):
        raise RuntimeError(f"PARENT_PIN_FAIL {parent}")
    if not w5.get("ok"):
        after = snapshot(phase="POST")
        report = _stop_report(
            parent=parent,
            w5=w5,
            verdict=CASE_IDENTITY,
            nxt="RECOVER_CANONICAL_W5_BEFORE_INFORMATION_LIMIT",
            note="W5 exact identity not proven. STOP.",
            before=before,
            after=after,
            overlap=overlap,
        )
        _publish(report)
        print(f"VERDICT {CASE_IDENTITY}", flush=True)
        print("STOP.", flush=True)
        return 0

    print("PHASE PARENT POPULATION", flush=True)
    har = load_parent_population()
    if not har.get("ok"):
        raise RuntimeError(f"PARENT_HARVEST_FAIL {har.get('blocker')}")
    parent_rows = list(har.get("rows") or [])
    parity = parent_row_parity(parent_rows)
    print(f"PHASE PARITY {parity.get('ok')} {parity.get('observed')}", flush=True)
    if not parity.get("ok"):
        after = snapshot(phase="POST")
        report = _stop_report(
            parent=parent,
            w5=w5,
            verdict=CASE_PARITY,
            nxt="FIX_PARENT_POPULATION_BEFORE_EXECUTION_DECOMPOSITION",
            note="Parent clock-anchor parity failed. STOP.",
            before=before,
            after=after,
            overlap=overlap,
        )
        report["parent_parity"] = parity
        _publish(report)
        print(f"VERDICT {CASE_PARITY}", flush=True)
        print("STOP.", flush=True)
        return 0

    print("PHASE W5 OVERLAY", flush=True)
    over = overlay_w5(parent_rows)
    if not over.get("ok"):
        raise RuntimeError(f"W5_OVERLAY_FAIL {over.get('blocker')}")
    rows = list(over.get("rows") or [])
    leak = int(over.get("future_leak_n") or 0)
    if leak != 0:
        raise RuntimeError(f"FUTURE_LEAK:{leak}")

    pops = populations(rows)
    sel = selection_effect(rows)
    common = common_endpoint(rows)
    blocks = block_comparison(rows)
    uni = univariate_w5(rows)
    inter = interactions_w5(rows)
    tree = fit_tree_w5(rows)
    lobo_pack = lobo_w5(rows) if int(tree.get("qualifying_leaf_n") or 0) >= 1 else {"TRUE_OOS": False, "folds": [], "confirm_positive_fold_n": 0}
    decision = decide(pops=pops, blocks=blocks, sel=sel, tree=tree, lobo_pack=lobo_pack)
    interp = interpret(decision=decision, pops=pops, sel=sel, common=common)
    decision["INTERPRETATION"] = interp.get("MARKET_MECHANISM")
    decision["WRITE_OVERLAP_N"] = overlap

    after = snapshot(phase="POST")
    if stress_path_touch_n([OUT, CACHE]) or holdout_path_touch_n([OUT, CACHE]):
        raise RuntimeError("SEALED_PATH_TOUCH")

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "KIND": KIND,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "parent": parent,
        "w5_pin": w5,
        "parent_parity": parity,
        "hashes": {"SOURCE_SHA256": source_sha256(), "W5_SOURCE_SHA256": w5.get("source_sha256")},
        "data_boundary": {
            "ALLOWED": list(DEVELOPMENT_DAYS),
            "SESSION": "AM",
            "HOLDOUT_READ": False,
            "STRESS_READ": False,
            "FUTURE_READ": False,
        },
        "populations": pops,
        "selection": sel,
        "common_endpoint": common,
        "blocks": blocks,
        "univariate": uni,
        "interactions": inter,
        "tree": tree,
        "lobo": lobo_pack,
        "interpretation": interp,
        "future_leak_n": leak,
        "decision": decision,
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
