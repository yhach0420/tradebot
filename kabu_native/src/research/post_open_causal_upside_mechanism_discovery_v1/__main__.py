"""Offline post-open causal upside discovery. LEGACY_DEV only. Runtime 0/0/0."""
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
from research.post_open_causal_upside_mechanism_discovery_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    FEATURE_IDS,
    KIND,
    MAX_RESEARCH_DATE,
    MAX_TREE_FEATURES,
    TRUE_OOS,
)
from research.post_open_causal_upside_mechanism_discovery_v1.analyze import (
    decide,
    feature_integrity,
    fit_tree,
    interactions,
    lobo,
    population,
    univariate,
)
from research.post_open_causal_upside_mechanism_discovery_v1.harvest import harvest_upside
from research.post_open_causal_upside_mechanism_discovery_v1.interpret import interpret
from research.post_open_causal_upside_mechanism_discovery_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.post_open_causal_upside_mechanism_discovery_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.post_open_causal_upside_mechanism_discovery_v1.spec import already_executed_check, pin_parent, source_sha256

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
        "EXACT_CLOSED_ENTRY_REUSE": False,
        "CANDIDATE_STRATEGY_N": 0,
        "TEN_MIN_USED_AS_STRATEGY_EXIT": False,
        "MBO_WORK": False,
        "FUTURES_WORK": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "FULL_CAUSAL_ECONOMICS_THIS_RUN": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    pop = dict(report.get("population") or {})
    integ = list(report.get("feature_integrity") or [])
    uni = list(report.get("univariate") or [])
    stable = [u for u in uni if u.get("mechanism_candidate")]
    top10 = (stable or uni)[:10]
    tree = dict(report.get("tree") or {})
    leaves = [L for L in list(tree.get("leaves") or []) if L.get("qualifying")]
    leaf = leaves[0] if leaves else {}
    lobo_pack = dict(report.get("lobo") or {})
    interp = dict(report.get("interpretation") or {})
    fam = dict(interp.get("family_useful") or {})
    d = dict(report.get("decision") or {})
    leak = int(report.get("future_leak_n") or 0)
    return {
        "1_anchor_N": pop.get("anchor_n"),
        "2_executable_anchor_N": pop.get("executable_n"),
        "3_day_symbol_N": {"day_n": pop.get("day_n"), "symbol_n": pop.get("symbol_n")},
        "4_feature_integrity_PASS": all(float(r.get("finite_rate") or 0) >= 0 for r in integ) and leak == 0,
        "5_future_leakage_N": leak,
        "6_population_EXEC_MARKOUT_10M": {"mean": pop.get("EXEC_MARKOUT_10M_mean"), "median": pop.get("EXEC_MARKOUT_10M_median")},
        "7_population_UP_DOMINANT_rate": pop.get("UP_DOMINANT_rate"),
        "8_population_EXEC_POSITIVE_rate": pop.get("EXEC_POSITIVE_rate"),
        "9_stable_univariate_TOP10": [
            {"feature": u.get("feature"), "spearman_markout": u.get("spearman_markout"), "candidate": u.get("mechanism_candidate")}
            for u in top10
        ],
        "10_B1_B5_signs": {u.get("feature"): u.get("block_markout_signs") for u in top10},
        "11_top_symbol_exclusion": {u.get("feature"): u.get("top_symbol_excluded_holds") for u in top10},
        "12_leave_one_day": {u.get("feature"): u.get("leave_one_day_holds") for u in top10},
        "13_horizon_5_15": {u.get("feature"): u.get("horizon_5_15_holds") for u in top10},
        "14_tree_rules": tree.get("rules") if tree.get("ok") else tree,
        "15_qualifying_leaf_N": tree.get("qualifying_leaf_n"),
        "16_qualifying_leaf_coverage": {"day_n": leaf.get("day_n"), "symbol_n": leaf.get("symbol_n")},
        "17_qualifying_leaf_markout": {"mean": leaf.get("mean_markout"), "median": leaf.get("median_markout")},
        "18_qualifying_leaf_UP_DOMINANT": leaf.get("UP_DOMINANT_rate"),
        "19_qualifying_leaf_EXEC_POSITIVE": leaf.get("EXEC_POSITIVE_rate"),
        "20_qualifying_leaf_B1_B5": leaf.get("block_mean_markout"),
        "21_LOBO_feature_recurrence": lobo_pack.get("feature_same_direction_fold_n"),
        "22_LOBO_confirm_markout_positive_folds": lobo_pack.get("confirm_positive_fold_n"),
        "23_strongest_2way": (list(report.get("interactions") or [])[:1] or [None])[0],
        "24_market_wide_useful": fam.get("market"),
        "25_depth_useful": fam.get("depth"),
        "26_trade_participation_useful": fam.get("participation"),
        "27_quote_activity_useful": fam.get("quote"),
        "28_price_path_useful": fam.get("price"),
        "29_prior_session_useful": fam.get("prevclose"),
        "30_CalcPrice_useful": fam.get("calc"),
        "31_UnderOver_useful": fam.get("under_over"),
        "32_single_symbol_concentration": not all(bool(u.get("top_symbol_excluded_holds")) for u in top10) if top10 else None,
        "33_single_day_concentration": not all(bool(u.get("leave_one_day_holds")) for u in top10) if top10 else None,
        "34_clear_mechanism_found": found_flag(d),
        "35_market_mechanism_sentence": interp.get("MARKET_MECHANISM"),
        "36_ENTRY_thesis_derivable": bool(interp.get("ENTRY_THESIS")),
        "37_Technical_EXIT_derivable": bool(interp.get("TECHNICAL_EXIT")),
        "38_exact_CLOSED_duplicate": interp.get("EXACT_CLOSED_DUPLICATE"),
        "39_Complete_Strategy_precommitted": interp.get("COMPLETE_STRATEGY_PRECOMMITTED"),
        "40_VERDICT": d.get("VERDICT"),
        "41_NEXT": d.get("NEXT"),
        "feature_n": len(FEATURE_IDS),
    }


def found_flag(d: dict[str, Any]) -> bool:
    return str(d.get("VERDICT") or "").endswith("FOUND_V1")


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
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 UPSIDE DISCOVERY NO STRATEGY NO V5 RESCUE", flush=True)
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

    print("PHASE HARVEST CLOCK ANCHORS", flush=True)
    har = harvest_upside()
    if not har.get("ok"):
        raise RuntimeError(f"HARVEST_FAIL {har.get('blocker')}")
    rows = list(har.get("rows") or [])
    leak = int(har.get("future_leak_n") or 0)
    print(f"PHASE ROWS {len(rows)} leak={leak}", flush=True)
    if leak != 0:
        raise RuntimeError(f"FUTURE_LEAK:{leak}")

    pop = population(rows)
    print("PHASE POP", pop.get("executable_n"), pop.get("primary_n"), pop.get("EXEC_MARKOUT_10M_mean"), flush=True)
    integ = feature_integrity(rows)
    uni = univariate(rows)
    stable = [u["feature"] for u in uni if u.get("mechanism_candidate")]
    feat_model = stable[:MAX_TREE_FEATURES] or [u["feature"] for u in uni[:MAX_TREE_FEATURES]]
    print("PHASE TREE", feat_model, flush=True)
    tree = fit_tree(rows, features=feat_model)
    lobo_pack = lobo(rows, candidates=feat_model)
    inter = interactions(rows, features=feat_model)
    decision = decide(uni=uni, tree=tree, lobo_pack=lobo_pack)
    found = str(decision.get("VERDICT") or "").endswith("FOUND_V1")
    interp = interpret(uni=uni, tree=tree, inter=inter, found=found)
    decision["INTERPRETATION"] = interp.get("MARKET_MECHANISM")

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
        "hashes": {"SOURCE_SHA256": source_sha256()},
        "data_boundary": {
            "ALLOWED": list(DEVELOPMENT_DAYS),
            "SESSION": "AM",
            "ANCHORS": ["09:10-11:10 / 10m"],
            "HOLDOUT_READ": False,
            "STRESS_READ": False,
            "FUTURE_READ": False,
        },
        "population": pop,
        "feature_integrity": integ,
        "univariate": uni,
        "tree": tree,
        "lobo": lobo_pack,
        "interactions": inter,
        "interpretation": interp,
        "future_leak_n": leak,
        "decision": decision | {"WRITE_OVERLAP_N": overlap},
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
