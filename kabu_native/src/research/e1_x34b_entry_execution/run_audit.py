"""E1_X34B runner — cost-aware ENTRY × execution routing nested CV."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x28_executable_joint.board import verify_board_mapping
from research.e1_x31_population_direction.identity import ab_identity, reproduce_population
from research.e1_x32_upstream_attribution.eval_stages import load_boards_for_symbols
from research.e1_x33b_neutral_anchor.neutral import (
    candidate_symbols_by_day,
    planned_neutral_anchors,
)

from . import (
    ANALYSIS_ID,
    ANCHOR_ID,
    ANCHOR_SHA,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    EXEC_POLICY_SHA,
    FORBIDDEN_FROM,
    NEXT_PHASE,
    SOURCE_X34A_RUN,
    WAIT_PASSIVE_SEC,
)
from .cv import run_nested_cv
from .metrics import baseline_decisions, summarize_decisions
from .panel import build_panel
from .publish import publish
from .robust import lodo_crossfit, loso_crossfit, route_profiles
from .verdict import decide_verdict, freeze_manifests

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x34b_entry_execution"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x34b_entry_execution.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(tp), "-q", "--tb=line"],
        cwd=str(NATIVE), capture_output=True, text=True, env=env,
    )
    out = (p.stdout or "") + (p.stderr or "")
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", out)
    if m2:
        failed = int(m2.group(1))
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-2000:]}


def _verify_shas() -> dict[str, Any]:
    anchor = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    pol = json.loads((X34A / "ENTRY_EXECUTION_POLICY_V1.json").read_text(encoding="utf-8"))
    a_raw = {k: v for k, v in anchor.items() if k != "sha256"}
    p_raw = {k: v for k, v in pol.items() if k != "sha256"}
    return {
        "anchor_sha": anchor.get("sha256"),
        "anchor_match": anchor.get("sha256") == ANCHOR_SHA,
        "anchor_recompute_ok": hashlib.sha256(
            json.dumps(a_raw, sort_keys=True, default=str).encode()
        ).hexdigest() == anchor.get("sha256"),
        "exec_sha": pol.get("sha256"),
        "exec_match": pol.get("sha256") == EXEC_POLICY_SHA,
        "exec_recompute_ok": hashlib.sha256(
            json.dumps(p_raw, sort_keys=True, default=str).encode()
        ).hexdigest() == pol.get("sha256"),
        "passive_wait_sec": pol.get("wait_window_sec"),
        "x34a_run": SOURCE_X34A_RUN,
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x34b_entry_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok") and mapping.get("mapping_sha") == BOARD_MAPPING_SHA
    shas = _verify_shas()
    assert shas["anchor_match"] and shas["anchor_recompute_ok"], shas
    assert shas["exec_match"] and shas["exec_recompute_ok"], shas
    assert float(shas["passive_wait_sec"]) == WAIT_PASSIVE_SEC
    print("  SHA bind OK (anchor + PASSIVE policy)", flush=True)

    print("=== population / neutral anchors ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"], ab_pop
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    pairs = sorted({(a["date"], a["symbol"]) for a in planned})
    assert all(d < FORBIDDEN_FROM for d, _ in pairs)
    boards = load_boards_for_symbols(pairs)

    print("=== panel (outcomes + pre-entry features) ===", flush=True)
    panel = build_panel(planned, boards)
    print(f"  panel n={len(panel)} passive_fills={sum(1 for r in panel if r['PASSIVE_FILL'])}", flush=True)
    assert len(panel) == 3453

    # A/B determinism smoke on outcomes means
    agg600 = float(sum(r["AGG_NET_600"] for r in panel) / len(panel))
    pas600 = float(sum(r["PASSIVE_NET_600"] for r in panel) / len(panel))
    print(f"  B1 AGG_ALL opp600={agg600:.4f} B2 PAS_ALL opp600={pas600:.4f}", flush=True)

    print("=== baselines ===", flush=True)
    b0 = summarize_decisions(panel, baseline_decisions(panel, "SKIP_ALL"), label="B0")
    b1 = summarize_decisions(panel, baseline_decisions(panel, "AGGRESSIVE_ALL"), label="B1")
    b2 = summarize_decisions(panel, baseline_decisions(panel, "PASSIVE_ALL"), label="B2")
    baselines = {"B0_SKIP_ALL": b0, "B1_AGGRESSIVE_ALL": b1, "B2_PASSIVE_ALL": b2}

    print("=== nested CV ===", flush=True)
    nested = run_nested_cv(panel)
    cross = nested["cross_fitted"]
    cross_rows = nested["cross_rows"]
    cross_dec = nested["cross_decisions"]

    # oracle diagnostic (not for selection)
    oracle_nets = [
        max(float(r["AGG_NET_600"]), float(r["PASSIVE_NET_600"]), 0.0) for r in panel
    ]
    oracle = {
        "mean_opp600": float(sum(oracle_nets) / len(oracle_nets)),
        "note": "ORACLE_BEST_OF_AGG_PASSIVE diagnostic only — not used for ENTRY/routing",
    }

    lodo = lodo_crossfit(cross_rows, cross_dec)
    loso = loso_crossfit(cross_rows, cross_dec)
    profiles = route_profiles(cross_rows, cross_dec)

    decision = decide_verdict(
        cross=cross,
        baselines=baselines,
        lodo=lodo,
        selected_per_fold=nested["selected_per_fold"],
    )
    print(f"  verdict={decision['verdict']} gates={decision.get('gates')}", flush=True)

    entry_m = router_m = None
    if decision.get("freeze"):
        entry_m, router_m = freeze_manifests(
            selected_per_fold=nested["selected_per_fold"],
            cross=cross,
            verdict=decision["verdict"],
        )
        (OUT / "ENTRY_V3_MANIFEST.json").write_text(
            json.dumps(entry_m, indent=2, default=str), encoding="utf-8",
        )
        (OUT / "ENTRY_EXECUTION_ROUTER_V1.json").write_text(
            json.dumps(router_m, indent=2, default=str), encoding="utf-8",
        )

    routed_minus = None
    if cross.get("opp_w_ret600") is not None and b2.get("opp_w_ret600") is not None:
        routed_minus = float(cross["opp_w_ret600"] - b2["opp_w_ret600"])

    # slim fold test summaries for report
    folds_slim = {}
    for k, fr in nested["folds"].items():
        folds_slim[k] = {
            "train_days": fr["train_days"],
            "test_days": fr["test_days"],
            "n_survivors": fr["n_survivors"],
            "selected": fr["selected"],
            "survivor_ids_top": fr.get("survivor_ids"),
            "test_opp600": (fr.get("test") or {}).get("opp_w_ret600"),
            "test_ss600": (fr.get("test") or {}).get("ss_balanced_ret600"),
            "test_selected": (fr.get("test") or {}).get("selected"),
            "test_agg": (fr.get("test") or {}).get("aggressive_count"),
            "test_pas": (fr.get("test") or {}).get("passive_signal_count"),
            "test_pas_fills": (fr.get("test") or {}).get("passive_fill_count"),
            "test_pf": (fr.get("test") or {}).get("pf_equiv_600"),
        }

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": decision["verdict"],
        "verdict_detail": decision,
        "source_x34a_run": SOURCE_X34A_RUN,
        "anchor_id": ANCHOR_ID,
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_POLICY_SHA,
        "sha_verify": shas,
        "population": len(panel),
        "passive_contract_unchanged": True,
        "wait_passive_sec": WAIT_PASSIVE_SEC,
        "no_fill_as_feature": True,
        "no_future_feature": True,
        "oracle_not_used_for_selection": True,
        "oracle_diagnostic": oracle,
        "outer_folds": folds_slim,
        "selected_per_fold": nested["selected_per_fold"],
        "catalog_size": nested["catalog_size"],
        "cross_fitted": {k: v for k, v in cross.items() if k != "day_means"},
        "cross_fitted_day_means": cross.get("day_means"),
        "baselines": {
            k: {kk: vv for kk, vv in sm.items() if kk != "day_means"}
            for k, sm in baselines.items()
        },
        "baselines_summary": {
            "B0": b0.get("opp_w_ret600"),
            "B1": b1.get("opp_w_ret600"),
            "B2": b2.get("opp_w_ret600"),
            "B3_ROUTED": cross.get("opp_w_ret600"),
        },
        "routed_minus_passive_all": routed_minus,
        "route_contribution": {
            "AGGRESSIVE": cross.get("agg_route_contrib_600"),
            "PASSIVE": cross.get("pas_route_contrib_600"),
        },
        "route_profiles": profiles,
        "lodo": {k: v for k, v in lodo.items() if k != "folds"},
        "lodo_folds": lodo.get("folds"),
        "loso": loso,
        "manifest_created": bool(entry_m),
        "entry_v3_sha": (entry_m or {}).get("sha256"),
        "router_sha": (router_m or {}).get("sha256"),
        "recommended_next": NEXT_PHASE if decision.get("freeze") else "REVISIT_ENTRY_OR_ACCEPT_PASSIVE_ALL_BASELINE",
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "no_runtime_change": True,
        "no_exit": True,
        "no_short": True,
        "no_execution_param_tuning": True,
        "safety": {
            "research_paper_only": True,
            "submit_cancel_live": "0/0/0",
            "discord_production": False,
        },
        "ab_determinism": {
            "population": ab_pop,
            "panel_n": len(panel),
            "agg_mean_600": agg600,
            "pas_mean_600": pas600,
        },
    }

    interim = {
        "run_id": run_id,
        "verdict": decision["verdict"],
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_POLICY_SHA,
        "population": len(panel),
        "cross_fitted": report["cross_fitted"],
        "baselines_summary": report["baselines_summary"],
        "routed_minus_passive_all": routed_minus,
        "selected_per_fold": nested["selected_per_fold"],
        "lodo": report["lodo"],
        "loso": {k: v for k, v in loso.items() if k != "sample"},
        "oracle_not_used_for_selection": True,
        "no_fill_as_feature": True,
        "no_future_feature": True,
        "no_runtime_change": True,
        "no_exit": True,
        "no_short": True,
        "opened_20260810": False,
        "submit_cancel_live": "0/0/0",
        "manifest_created": bool(entry_m),
        "passive_contract_unchanged": True,
        "ab_determinism": report["ab_determinism"],
        "gates": decision.get("gates"),
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "B2": b2.get("opp_w_ret600"),
            "B3": cross.get("opp_w_ret600"),
            "delta": routed_minus,
            "ss": cross.get("ss_balanced_ret600"),
            "pf": cross.get("pf_equiv_600"),
            "pos_days": cross.get("positive_days"),
        }],
        "outer_folds": [{"block": k, **v} for k, v in folds_slim.items()],
        "day_means": [{"date": d, "opp600": v} for d, v in sorted((cross.get("day_means") or {}).items())],
        "route_profiles": [
            {"route": k, **v} for k, v in profiles.items() if isinstance(v, dict)
        ],
        "lodo": lodo.get("folds") or [],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    publish(OUT, report, sheets)

    print(f"=== DONE {decision['verdict']} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": decision["verdict"],
        "B1": b1.get("opp_w_ret600"),
        "B2": b2.get("opp_w_ret600"),
        "B3": cross.get("opp_w_ret600"),
        "delta_vs_pas": routed_minus,
        "manifest": bool(entry_m),
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
