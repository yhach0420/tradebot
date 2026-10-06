"""Offline short downside discovery. LEGACY_DEV only. Runtime 0/0/0. No live short."""
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
from research.post_open_causal_downside_mechanism_discovery_v1 import (
    ANALYSIS_ID,
    CASE_PARITY,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    FEATURE_IDS,
    KIND,
    MAX_RESEARCH_DATE,
    TRUE_OOS,
)
from research.post_open_causal_downside_mechanism_discovery_v1.analyze import (
    block_s2,
    decide,
    execution_populations,
    feature_integrity,
    fit_tree_short,
    lobo_short,
    oof_ranking,
    short_population,
    univariate_short,
)
from research.post_open_causal_downside_mechanism_discovery_v1.harvest import load_parent_population, overlay_short
from research.post_open_causal_downside_mechanism_discovery_v1.interpret import interpret
from research.post_open_causal_downside_mechanism_discovery_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.post_open_causal_downside_mechanism_discovery_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.post_open_causal_downside_mechanism_discovery_v1.spec import (
    already_executed_check,
    parent_row_parity,
    pin_parent,
    pin_short_w5,
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
        "RUNTIME_SHORT": False,
        "NEW_ENTRY": False,
        "CAP5": False,
        "OCCUPANCY": False,
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
    par = dict(report.get("parent_parity") or {})
    obs = dict(par.get("observed") or {})
    pop = dict(report.get("population") or {})
    exec_pops = dict(report.get("execution_populations") or {})
    s0 = dict(exec_pops.get("S0") or {})
    s1 = dict(exec_pops.get("S1") or {})
    s2 = dict(exec_pops.get("S2") or {})
    w5 = dict(report.get("short_w5_pin") or {})
    uni = list(report.get("univariate") or [])
    stable = [u for u in uni if u.get("mechanism_candidate")]
    tree = dict(report.get("tree") or {})
    leaves = [L for L in list(tree.get("leaves") or []) if L.get("qualifying")]
    best = max(list(tree.get("leaves") or []) or [{}], key=lambda L: float(L.get("mean_markout") or -1e18))
    leaf = leaves[0] if leaves else best
    rank = dict(report.get("ranking") or {})
    slices = dict(rank.get("slices") or {})
    lobo_pack = dict(report.get("lobo") or {})
    interp = dict(report.get("interpretation") or {})
    d = dict(report.get("decision") or {})
    blocks = dict(report.get("blocks") or {})
    return {
        "1_parent_parity": par.get("ok"),
        "2_anchor_N": pop.get("anchor_n"),
        "3_executable_N": pop.get("executable_n"),
        "4_future_leakage_N": report.get("future_leak_n"),
        "5_MID_10m_mean_median": {"mean": pop.get("MID_RETURN_10M_mean"), "median": pop.get("MID_RETURN_10M_median")},
        "6_Long_X1_mean_median_parity": {
            "mean": obs.get("EXEC_MARKOUT_10M_mean"),
            "median": obs.get("EXEC_MARKOUT_10M_median"),
            "ok": par.get("mean_ok") and par.get("median_ok"),
        },
        "7_Short_X1_mean_median": {"mean": pop.get("SHORT_EXEC_MARKOUT_10M_mean"), "median": pop.get("SHORT_EXEC_MARKOUT_10M_median")},
        "8_Short_X1_positive_rate": pop.get("SHORT_EXEC_POSITIVE_rate"),
        "9_DOWN_DOMINANT_rate": pop.get("DOWN_DOMINANT_rate"),
        "10_trusted_short_passive_existed": w5.get("trusted_short_passive_existed"),
        "11_short_W5_spec_SHA": w5.get("source_sha256"),
        "12_short_W5_fill_N_rate": {"n": exec_pops.get("SHORT_W5_fill_n"), "rate": exec_pops.get("SHORT_W5_fill_rate")},
        "13_S0_mean_median": {"mean": s0.get("mean"), "median": s0.get("median")},
        "14_S1_mean_median": {"mean": s1.get("mean"), "median": s1.get("median")},
        "15_S2_mean_median": {"mean": s2.get("mean"), "median": s2.get("median")},
        "16_passive_short_improvement": exec_pops.get("S2_minus_S1_mean"),
        "17_positive_S2_block_N": blocks.get("positive_S2_block_n"),
        "18_stable_short_features": [u.get("feature") for u in stable],
        "19_B1_B5_directions": {u.get("feature"): u.get("block_markout_signs") for u in stable[:10]},
        "20_top_symbol_exclusion": {u.get("feature"): u.get("top_symbol_excluded_holds") for u in stable[:10]},
        "21_leave_one_day": {u.get("feature"): u.get("leave_one_day_holds") for u in stable[:10]},
        "22_OOF_ranking_Spearman": rank.get("spearman_oof"),
        "23_Short_Top10_markout": slices.get("SHORT_TOP10"),
        "24_Short_Top5": slices.get("SHORT_TOP5"),
        "25_Short_Top3": slices.get("SHORT_TOP3"),
        "26_Short_Top1": slices.get("SHORT_TOP1"),
        "27_ranking_monotonic": rank.get("monotonic"),
        "28_tree_rules": tree.get("rules") if tree.get("ok") else tree,
        "29_qualifying_leaf_N": tree.get("qualifying_leaf_n"),
        "30_best_leaf_mean_median": {"mean": leaf.get("mean_markout"), "median": leaf.get("median_markout")},
        "31_best_leaf_B1_B5": leaf.get("block_mean_markout"),
        "32_LOBO_positive_fold_N": lobo_pack.get("confirm_positive_fold_n"),
        "33_clear_downside_mechanism": str(d.get("CASE") or "") == "A",
        "34_exact_mechanism_sentence": interp.get("MARKET_MECHANISM"),
        "35_SHORT_ENTRY_thesis_derivable": bool(interp.get("ENTRY_THESIS")),
        "36_thesis_failure_EXIT_derivable": bool(interp.get("TECHNICAL_EXIT")),
        "37_strategy_precommitted": False,
        "38_TRUE_OOS": False,
        "39_CERTIFIED": False,
        "40_VERDICT": d.get("VERDICT"),
        "41_NEXT": d.get("NEXT"),
        "feature_n": len(FEATURE_IDS),
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
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 SHORT DISCOVERY NO RUNTIME SHORT NO V5", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)

    reused = already_executed_check()
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_RESULT=true", flush=True)
        print("STOP.", flush=True)
        return 0

    parent = pin_parent()
    w5 = pin_short_w5()
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if not parent.get("ok"):
        raise RuntimeError(f"PARENT_PIN_FAIL {parent}")
    if not w5.get("ok"):
        raise RuntimeError(f"SHORT_W5_PIN_FAIL {w5}")

    print("PHASE PARENT POPULATION", flush=True)
    har = load_parent_population()
    if not har.get("ok"):
        raise RuntimeError(f"PARENT_HARVEST_FAIL {har.get('blocker')}")
    parent_rows = list(har.get("rows") or [])
    parity = parent_row_parity(parent_rows)
    print(f"PHASE PARITY {parity.get('ok')} {parity.get('observed')}", flush=True)
    if not parity.get("ok"):
        after = snapshot(phase="POST")
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "KIND": KIND,
            "TRUE_OOS": TRUE_OOS,
            "CERTIFIED": CERTIFIED,
            "parent": parent,
            "short_w5_pin": w5,
            "parent_parity": parity,
            "decision": {"CASE": "STOP", "VERDICT": CASE_PARITY, "NEXT": "FIX_PARENT_POPULATION_BEFORE_SHORT_DISCOVERY"},
            "interpretation": {"MARKET_MECHANISM": "Parent clock-anchor parity failed. STOP.", "ENTRY_THESIS": None, "TECHNICAL_EXIT": None},
            "safety": _safety(),
            "isolation_before": before,
            "isolation_after": after,
            "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(),
            "hashes": {"SOURCE_SHA256": source_sha256()},
        }
        _publish(report)
        print(f"VERDICT {CASE_PARITY}", flush=True)
        print("STOP.", flush=True)
        return 0

    print("PHASE SHORT OVERLAY", flush=True)
    over = overlay_short(parent_rows)
    if not over.get("ok"):
        raise RuntimeError(f"SHORT_OVERLAY_FAIL {over.get('blocker')}")
    rows = list(over.get("rows") or [])
    leak = int(over.get("future_leak_n") or 0)
    if leak != 0:
        raise RuntimeError(f"FUTURE_LEAK:{leak}")

    pop = short_population(rows)
    integ = feature_integrity(rows)
    exec_pops = execution_populations(rows)
    blocks = block_s2(rows)
    uni = univariate_short(rows)
    stable = [str(u.get("feature")) for u in uni if u.get("mechanism_candidate")]
    rank = oof_ranking(rows, features=stable)
    tree = fit_tree_short(rows, features=stable)
    lobo_pack = lobo_short(rows, features=stable) if int(tree.get("qualifying_leaf_n") or 0) >= 1 else {"TRUE_OOS": False, "folds": [], "confirm_positive_fold_n": 0}
    decision = decide(pop=pop, exec_pops=exec_pops, tree=tree, lobo_pack=lobo_pack, rank=rank)
    interp = interpret(decision=decision, pop=pop, exec_pops=exec_pops, uni=uni, tree=tree)
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
        "short_w5_pin": w5,
        "parent_parity": parity,
        "hashes": {"SOURCE_SHA256": source_sha256(), "SHORT_W5_SHA256": w5.get("source_sha256")},
        "data_boundary": {
            "ALLOWED": list(DEVELOPMENT_DAYS),
            "SESSION": "AM",
            "HOLDOUT_READ": False,
            "STRESS_READ": False,
            "FUTURE_READ": False,
        },
        "population": pop,
        "feature_integrity": integ,
        "execution_populations": exec_pops,
        "blocks": blocks,
        "univariate": uni,
        "ranking": rank,
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
