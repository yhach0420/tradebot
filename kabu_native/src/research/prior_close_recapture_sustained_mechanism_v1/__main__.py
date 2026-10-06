"""Offline recapture success mechanism discovery. LEGACY_DEV only. Runtime 0/0/0."""
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
from research.prior_close_recapture_sustained_mechanism_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    FEATURE_IDS,
    KIND,
    LABEL_FAILED,
    LABEL_SUSTAINED,
    MAX_RESEARCH_DATE,
    MAX_TREE_FEATURES,
    TRUE_OOS,
)
from research.prior_close_recapture_sustained_mechanism_v1.analyze import (
    alpha_rows,
    decide,
    feature_integrity,
    fit_tree,
    label_summary,
    lobo,
    logistic_direction,
    monotonicity,
    reproduce_portfolio,
    univariate,
)
from research.prior_close_recapture_sustained_mechanism_v1.harvest import harvest_mechanism
from research.prior_close_recapture_sustained_mechanism_v1.interpret import interpret
from research.prior_close_recapture_sustained_mechanism_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.prior_close_recapture_sustained_mechanism_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.prior_close_recapture_sustained_mechanism_v1.spec import already_executed_check, pin_parent, source_sha256

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
        "V5_OUT_WRITTEN": False,
        "V5_RESCUE": False,
        "THRESHOLD_OPTIMIZATION": False,
        "MBO_WORK": False,
        "FUTURES_WORK": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "FULL_CAUSAL_ECONOMICS_THIS_RUN": False,
    }


def _pnl_by_label(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for lab in (LABEL_SUSTAINED, LABEL_FAILED):
        xs = [r for r in alpha_rows(rows) if r.get("alpha_label") == lab and r.get("pnl_yen_100") is not None]
        pnls = [float(r["pnl_yen_100"]) for r in xs]
        out[lab] = {
            "n_with_pnl": len(pnls),
            "sum": float(sum(pnls)) if pnls else None,
            "mean": (float(sum(pnls)) / len(pnls)) if pnls else None,
            "note": "Standalone X1 path PnL. Secondary validation only. Not used for feature selection.",
        }
    return out


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    lab = dict(report.get("labels") or {})
    port = dict(report.get("portfolio_reproduction") or {})
    uni = list(report.get("univariate") or [])
    stable = [u for u in uni if u.get("mechanism_candidate")]
    top5 = stable[:5] if stable else uni[:5]
    tree = dict(report.get("tree") or {})
    lobo_pack = dict(report.get("lobo") or {})
    interp = dict(report.get("interpretation") or {})
    d = dict(report.get("decision") or {})
    sus = dict(lab.get("SUSTAINED_RECAPTURE") or {})
    fail = dict(lab.get("FAILED_RECAPTURE") or {})
    return {
        "1_SUSTAINED_N": sus.get("n"),
        "2_FAILED_N": fail.get("n"),
        "3_label_days_symbols": {
            "SUSTAINED": {"day_n": sus.get("day_n"), "symbol_n": sus.get("symbol_n")},
            "FAILED": {"day_n": fail.get("day_n"), "symbol_n": fail.get("symbol_n")},
        },
        "4_technical_exit_group": {"n": port.get("technical_exit_n"), "pnl": port.get("technical_exit_pnl")},
        "5_session_hold_group": {
            "n": port.get("session_close_n"),
            "pnl": port.get("session_close_pnl"),
            "win_loss_flat": port.get("session_close_win_loss_flat"),
        },
        "6_univariate_TOP5": [
            {"feature": u.get("feature"), "direction": u.get("direction"), "candidate": u.get("mechanism_candidate")}
            for u in top5
        ],
        "7_B1_B5_direction": {u.get("feature"): u.get("block_signs") for u in top5},
        "8_top_symbol_excluded_holds": {u.get("feature"): u.get("top_symbol_excluded_holds") for u in top5},
        "9_day_excluded_holds": {u.get("feature"): u.get("leave_one_day_holds") for u in top5},
        "10_monotonic": [
            {"feature": m.get("feature"), "up": m.get("monotonic_up"), "down": m.get("monotonic_down"), "q4_q1": m.get("q4_minus_q1_sustained_rate")}
            for m in list(report.get("monotonicity") or [])
            if m.get("feature") in {u.get("feature") for u in top5}
        ],
        "11_shallow_tree_rule": tree.get("rules") if tree.get("ok") else tree,
        "12_lobo_same_feature": lobo_pack.get("feature_same_direction_fold_n"),
        "13_market_mechanism": interp.get("MARKET_MECHANISM"),
        "14_existing_closed_duplicate": interp.get("EXISTING_CLOSED_STRATEGY_DUPLICATE"),
        "15_ENTRY_causal_at_signal": interp.get("ENTRY_CAUSAL_AT_SIGNAL"),
        "16_EXIT_from_same_thesis": interp.get("EXIT_FROM_SAME_THESIS"),
        "17_complete_strategy_precommitted": interp.get("COMPLETE_STRATEGY_PRECOMMITTED"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
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
    print("SAFETY submit/cancel/live=0/0/0 MECHANISM_DISCOVERY_ONLY NO V5 RESCUE", flush=True)
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

    print("PHASE HARVEST X1-FILLABLE + PRE-ENTRY FEATURES", flush=True)
    har = harvest_mechanism()
    if not har.get("ok"):
        raise RuntimeError(f"HARVEST_FAIL {har.get('blocker')}")
    rows = list(har.get("rows") or [])
    print(f"PHASE ROWS {len(rows)} integ={har.get('integrity_n')}", flush=True)

    print("PHASE PORTFOLIO REPRODUCTION", flush=True)
    port = reproduce_portfolio(rows)
    print("tech", port.get("technical_exit_n"), "sess", port.get("session_close_n"), flush=True)

    labs = label_summary(rows)
    print("SUSTAINED", labs["SUSTAINED_RECAPTURE"]["n"], "FAILED", labs["FAILED_RECAPTURE"]["n"], flush=True)

    integ = feature_integrity(rows)
    uni = univariate(rows)
    stable = [u for u in uni if u.get("mechanism_candidate")]
    feat_for_model = [u["feature"] for u in stable][:MAX_TREE_FEATURES]
    if not feat_for_model:
        feat_for_model = [u["feature"] for u in uni[:MAX_TREE_FEATURES]]
    mono = monotonicity(rows, features=list(FEATURE_IDS))
    print("PHASE TREE", feat_for_model, flush=True)
    tree = fit_tree(rows, features=feat_for_model)
    logi = logistic_direction(rows, features=feat_for_model)
    lobo_pack = lobo(rows, candidates=feat_for_model)
    decision = decide(uni, tree=tree, lobo_pack=lobo_pack)
    # require LOBO non-flip among candidate features
    if decision.get("VERDICT") and stable and int(lobo_pack.get("direction_flip_n") or 0) > 0:
        decision["CASE"] = "NONE"
        decision["VERDICT"] = "PRIOR_CLOSE_RECAPTURE_NO_STABLE_SUCCESS_MECHANISM_V1"
        decision["NEXT"] = "NEXT_NON_OPENING_POST_OPEN_ARCHITECTURE_V6"
        decision["PREVIOUS_CLOSE_LINE_EXHAUSTED"] = True
    found = str(decision.get("VERDICT") or "").endswith("FOUND_V1")
    interp = interpret(uni=uni, tree=tree, found=found)
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
            "HOLDOUT_READ": False,
            "STRESS_READ": False,
            "FUTURE_READ": False,
        },
        "labels": labs,
        "portfolio_reproduction": port,
        "feature_integrity": integ,
        "univariate": uni,
        "monotonicity": mono,
        "tree": tree,
        "logistic": logi,
        "lobo": lobo_pack,
        "secondary_pnl_by_label": _pnl_by_label(rows),
        "interpretation": interp,
        "decision": decision | {"WRITE_OVERLAP_N": overlap},
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(),
        "integrity_n": har.get("integrity_n"),
    }
    _publish(report)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
