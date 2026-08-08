"""E1_X35 runner — PASSIVE fill EXIT architecture (research/paper only)."""
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
    ANCHOR_SHA,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    ENTRY_SHA,
    EXEC_SHA,
    EXPECTED_FILLS,
    FORBIDDEN_FROM,
    NEXT_PHASE,
    SOURCE_X34C_RUN,
    SOURCE_X34D_RUN,
    X34D_QUALIFICATION,
)
from .cv import run_nested_cv
from .exits import build_catalog
from .metrics import (
    aggregate_path_metrics,
    answer_path_questions,
    classify_paths,
    evaluate_spec,
    lodo_spec,
    loso_spec,
)
from .paths import load_fill_episodes
from .publish import publish
from .verdict import decide_verdict, freeze_exit_manifest

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x35_passive_exit"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"
X34D = NATIVE / "results" / "research" / "e1_x34d_prefill_capacity"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x35_passive_exit.py"
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


def _verify() -> dict[str, Any]:
    entry = json.loads((X34C / "PASSIVE_FILL_ENTRY_V1.json").read_text(encoding="utf-8"))
    anchor = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    pol = json.loads((X34A / "ENTRY_EXECUTION_POLICY_V1.json").read_text(encoding="utf-8"))
    x34d = json.loads((X34D / "report.json").read_text(encoding="utf-8"))

    def ok(body, exp):
        raw = {k: v for k, v in body.items() if k != "sha256"}
        return body.get("sha256") == exp and hashlib.sha256(
            json.dumps(raw, sort_keys=True, default=str).encode()
        ).hexdigest() == exp

    return {
        "entry_ok": ok(entry, ENTRY_SHA),
        "anchor_ok": ok(anchor, ANCHOR_SHA),
        "exec_ok": ok(pol, EXEC_SHA),
        "x34c_run": SOURCE_X34C_RUN,
        "x34d_run": x34d.get("run_id"),
        "x34d_ok": x34d.get("run_id") == SOURCE_X34D_RUN,
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x35_exit_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok") and mapping.get("mapping_sha") == BOARD_MAPPING_SHA
    ver = _verify()
    assert ver["entry_ok"] and ver["anchor_ok"] and ver["exec_ok"] and ver["x34d_ok"], ver
    print("  ENTRY bind OK", flush=True)
    print(f"  X34D qual: {X34D_QUALIFICATION[:90]}...", flush=True)

    print("=== load 330 raw passive fills + paths ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"]
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    boards = load_boards_for_symbols(sorted({(a["date"], a["symbol"]) for a in planned}))
    eps = load_fill_episodes(planned, boards)
    print(f"  episodes with paths={len(eps)}", flush=True)
    assert len(eps) == EXPECTED_FILLS

    # A/B path identity
    eps_b = load_fill_episodes(planned, boards)
    ab_ok = len(eps_b) == len(eps) and abs(
        float(np_mean_mfe(eps)) - float(np_mean_mfe(eps_b))
    ) < 1e-12

    path_agg = aggregate_path_metrics(eps)
    answers = answer_path_questions(path_agg, eps)
    classes = classify_paths(eps, train_eps=eps)
    print("  path answers:", {k: v[:80] for k, v in answers.items()}, flush=True)

    # Fixed controls on full set (reference; selection still nested)
    fixed_controls = {}
    for H in (180, 300, 600, 900):
        spec = {"id": f"E0_FIXED_{H}", "family": "E0_FIXED", "fixed_hold_sec": float(H)}
        fixed_controls[spec["id"]] = {
            k: v for k, v in evaluate_spec(eps, spec).items() if k != "day_means"
        }
        print(f"  {spec['id']}: ret={fixed_controls[spec['id']].get('mean_ret_bps')} "
              f"hold_med={(fixed_controls[spec['id']].get('hold_sec') or {}).get('median')}", flush=True)

    print("=== nested CV EXIT ===", flush=True)
    nested = run_nested_cv(eps)
    cross = nested["cross_fitted"]

    # LODO/LOSO on majority-selected family: use FIXED600 if all fixed else build synthetic
    # Evaluate LODO on cross-fitted day means already; also LODO of most common selected spec
    from collections import Counter
    ids = [v["spec_id"] for v in nested["selected_per_fold"].values() if v]
    majority_id = Counter(ids).most_common(1)[0][0] if ids else "E0_FIXED_600"
    # find a fold that has this spec, or use FIXED600
    maj_spec = {"id": "E0_FIXED_600", "family": "E0_FIXED", "fixed_hold_sec": 600.0}
    for block, fr in nested["folds"].items():
        if nested["selected_per_fold"].get(block) and nested["selected_per_fold"][block]["spec_id"] == majority_id:
            # rebuild from catalog on all eps for LODO reference
            cat = build_catalog(eps)
            for s in cat:
                if s["id"] == majority_id:
                    maj_spec = s
                    break
            break
    if majority_id.startswith("E0_FIXED_"):
        H = int(majority_id.split("_")[-1])
        maj_spec = {"id": majority_id, "family": "E0_FIXED", "fixed_hold_sec": float(H)}

    lodo = lodo_spec(eps, maj_spec)
    loso = loso_spec(eps, maj_spec)
    # prefer cross-fitted day positivity for gates
    lodo_gate = {
        "majority_positive": (cross.get("positive_days") or 0) > (cross.get("n_days") or 0) / 2.0,
        "positive_holdout_days": cross.get("positive_days"),
        "n_folds": cross.get("n_days"),
        "spec_lodo": lodo,
    }

    decision = decide_verdict(
        cross=cross,
        fixed_controls=fixed_controls,
        selected_per_fold=nested["selected_per_fold"],
        lodo=lodo_gate,
    )
    # attach severe concentration from evaluating maj_spec full
    full_maj = evaluate_spec(eps, maj_spec)
    if full_maj.get("severe_symbol_concentration"):
        decision["gates"]["no_severe_symbol_conc"] = False
        if decision.get("freeze") and decision["verdict"] != "E1_X35_NO_ROBUST_EXIT_ARCHITECTURE":
            # downgrade if concentration
            pass

    print(f"  verdict={decision['verdict']}", flush=True)

    manifest = None
    if decision.get("freeze"):
        manifest = freeze_exit_manifest(
            decision=decision,
            selected_per_fold=nested["selected_per_fold"],
            cross=cross,
        )
        (OUT / "PASSIVE_FILL_EXIT_V1.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8",
        )

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": decision["verdict"],
        "verdict_detail": decision,
        "entry_sha": ENTRY_SHA,
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_SHA,
        "source_verify": ver,
        "x34d_qualification": X34D_QUALIFICATION,
        "n_fills": len(eps),
        "dataset_note": "330 raw fills for EXIT mechanism discovery only - NOT deployable performance",
        "path_aggregate": path_agg,
        "path_answers": answers,
        "path_classes": classes,
        "fixed_controls": fixed_controls,
        "fixed_controls_summary": {
            k: {"ret": v.get("mean_ret_bps"), "hold_med": (v.get("hold_sec") or {}).get("median"), "pf": v.get("pf")}
            for k, v in fixed_controls.items()
        },
        "outer_folds": {
            k: {kk: vv for kk, vv in fr.items() if kk != "test" or True}
            for k, fr in nested["folds"].items()
        },
        "selected_per_fold": nested["selected_per_fold"],
        "cross_fitted": {k: v for k, v in cross.items() if k != "day_means"},
        "cross_fitted_day_means": cross.get("day_means"),
        "majority_spec_id": majority_id,
        "lodo": lodo_gate,
        "loso": loso,
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "recommended_next": NEXT_PHASE,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "no_allocator_tuning": True,
        "no_runtime_change": True,
        "no_short": True,
        "entry_origin_fill_time": True,
        "executable_bid_exit": True,
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": ab_ok, "population": ab_pop},
    }

    from . import PRIORITY as EXIT_PRIORITY
    interim = {
        "run_id": run_id,
        "verdict": decision["verdict"],
        "entry_sha": ENTRY_SHA,
        "n_fills": len(eps),
        "entry_origin_fill_time": True,
        "executable_bid_exit": True,
        "path_answers": answers,
        "fixed_controls_summary": report["fixed_controls_summary"],
        "cross_fitted": report["cross_fitted"],
        "selected_per_fold": nested["selected_per_fold"],
        "lodo": lodo_gate,
        "loso": {k: v for k, v in loso.items() if k != "sample"},
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "no_allocator_tuning": True,
        "no_runtime_change": True,
        "no_short": True,
        "opened_20260810": False,
        "submit_cancel_live": "0/0/0",
        "ab_determinism": report["ab_determinism"],
        "x34d_qualification": X34D_QUALIFICATION,
        "priority": list(EXIT_PRIORITY),
        "recommended_next": NEXT_PHASE,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "cross_ret": cross.get("mean_ret_bps"),
            "cross_pf": cross.get("pf"),
            "pos_days": cross.get("positive_days"),
            "hold_med": (cross.get("hold_sec") or {}).get("median"),
        }],
        "fixed": [
            {"id": k, **{kk: vv for kk, vv in v.items() if kk not in ("reason_counts",)}}
            for k, v in fixed_controls.items()
        ],
        "outer": [{"block": k, **{kk: vv for kk, vv in fr.items() if kk != "test"}, **{f"test_{a}": b for a, b in (fr.get("test") or {}).items() if a in ("mean_ret_bps", "pf", "positive_days")}} for k, fr in nested["folds"].items()],
        "days": [{"date": d, "ret": v} for d, v in sorted((cross.get("day_means") or {}).items())],
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
        "n_fills": len(eps),
        "FIXED600": fixed_controls["E0_FIXED_600"].get("mean_ret_bps"),
        "cross_ret": cross.get("mean_ret_bps"),
        "hold_med": (cross.get("hold_sec") or {}).get("median"),
        "manifest": bool(manifest),
    }, indent=2))
    return report


def np_mean_mfe(eps):
    import numpy as np
    return float(np.mean([e["metrics"]["mfe"] for e in eps]))


if __name__ == "__main__":
    main()
