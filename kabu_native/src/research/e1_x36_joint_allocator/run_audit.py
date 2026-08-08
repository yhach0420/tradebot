"""E1_X36 runner — joint ENTRY×EXIT×allocator×hard-cap replay (research/paper only)."""
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
from research.e1_x34c_passive_deployability.events import build_events

from . import (
    ANALYSIS_ID,
    ANCHOR_SHA,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    ENTRY_SHA,
    EXEC_SHA,
    EXIT_SHA,
    EXPECTED_FILLS,
    EXPECTED_SIGNALS,
    FORBIDDEN_FROM,
    NEXT_PASS,
    SOURCE_X35R_RUN,
)
from .cv import run_baselines, run_nested_cv
from .metrics import (
    fill_return_decomposition,
    lodo_from_day_means,
    loso_sensitivity,
    score_quintile_diag,
)
from .models import fit_spec
from .panel import enrich_events
from .publish import publish
from .verdict import decide_verdict, freeze_manifest

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x36_joint_allocator"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"
X35R = NATIVE / "results" / "research" / "e1_x35r_exit_contract"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x36_joint_allocator.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src"), "PYTHONIOENCODING": "utf-8"}
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


def _sha_ok(body: dict, exp: str) -> bool:
    raw = {k: v for k, v in body.items() if k != "sha256"}
    return body.get("sha256") == exp and hashlib.sha256(
        json.dumps(raw, sort_keys=True, default=str).encode()
    ).hexdigest() == exp


def _verify() -> dict[str, Any]:
    entry = json.loads((X34C / "PASSIVE_FILL_ENTRY_V1.json").read_text(encoding="utf-8"))
    anchor = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    pol = json.loads((X34A / "ENTRY_EXECUTION_POLICY_V1.json").read_text(encoding="utf-8"))
    exit_m = json.loads((X35R / "PASSIVE_FIXED600_EXIT_BASELINE_V1.json").read_text(encoding="utf-8"))
    x35r = json.loads((X35R / "report.json").read_text(encoding="utf-8"))
    return {
        "entry_ok": _sha_ok(entry, ENTRY_SHA),
        "anchor_ok": _sha_ok(anchor, ANCHOR_SHA),
        "exec_ok": _sha_ok(pol, EXEC_SHA),
        "exit_ok": _sha_ok(exit_m, EXIT_SHA),
        "x35r_run": x35r.get("run_id"),
        "x35r_ok": x35r.get("run_id") == SOURCE_X35R_RUN,
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x36_joint_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok") and mapping.get("mapping_sha") == BOARD_MAPPING_SHA
    ver = _verify()
    assert all(ver[k] for k in ("entry_ok", "anchor_ok", "exec_ok", "exit_ok", "x35r_ok")), ver
    print("  all SHA binds OK", flush=True)

    print("=== population + boards + panel ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"]
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    boards = load_boards_for_symbols(sorted({(a["date"], a["symbol"]) for a in planned}))
    raw_events = build_events(planned, boards)
    print(f"  raw signals={len(raw_events)} fills={sum(1 for e in raw_events if e.get('filled'))}", flush=True)
    assert len(raw_events) == EXPECTED_SIGNALS
    assert sum(1 for e in raw_events if e.get("filled")) == EXPECTED_FILLS

    panel = enrich_events(raw_events, boards)
    n_exit = sum(1 for e in panel if e.get("canonical_exit_time") is not None)
    print(f"  panel enriched; canonical exits={n_exit}", flush=True)
    assert n_exit == EXPECTED_FILLS

    # A/B panel identity
    panel_b = enrich_events(raw_events, boards)
    ab_ok = len(panel_b) == len(panel) and abs(
        sum(e.get("OPPORTUNITY_VALUE_600") or 0 for e in panel)
        - sum(e.get("OPPORTUNITY_VALUE_600") or 0 for e in panel_b)
    ) < 1e-9

    print("=== neutral baselines (full 14d, canonical EXIT) ===", flush=True)
    baselines = run_baselines(panel)

    print("=== nested CV learned allocator ===", flush=True)
    nested = run_nested_cv(panel)
    cross = nested["cross_fitted"]

    # Learned cross vs ASC on same cross-fitted days: compare to full ASC baseline
    # Also evaluate ASC on cross events days only for fair-ish compare — use full ASC baseline as required
    decomp = fill_return_decomposition(
        cross, baselines["B1_ASC"], nested["cross_events"], baselines["B1_ASC"].get("_events") or []
    )

    lodo = lodo_from_day_means(cross.get("day_means_opp") or {})
    loso = loso_sensitivity(nested["cross_events"])

    # quintile diagnostics on cross events that have scores
    q_fill = score_quintile_diag(nested["cross_events"], label_key="FILL_1S")
    q_opp = score_quintile_diag(nested["cross_events"], label_key="OPPORTUNITY_VALUE_600")

    decision = decide_verdict(
        cross=cross,
        baselines=baselines,
        selected_per_fold=nested["selected_per_fold"],
        lodo=lodo,
        loso=loso,
    )
    print(f"  verdict={decision['verdict']}", flush=True)

    final_allocator = None
    if decision.get("freeze") and decision.get("freeze_as") == "LEARNED_ALLOCATOR":
        # majority family across folds
        from collections import Counter
        fams = [v["family"] for v in nested["selected_per_fold"].values()]
        maj = Counter(fams).most_common(1)[0][0]
        # use first fold with that family for feature/reg, else first
        spec = None
        for v in nested["selected_per_fold"].values():
            if v["family"] == maj:
                spec = {"family": v["family"], "feature_set": v["feature_set"], "reg": v["reg"]}
                break
        if spec is None:
            v0 = list(nested["selected_per_fold"].values())[0]
            spec = {"family": v0["family"], "feature_set": v0["feature_set"], "reg": v0["reg"]}
        fit = fit_spec(panel, spec)
        final_allocator = {
            "type": "LEARNED_HISTORICAL14_REFIT",
            "family": spec["family"],
            "feature_set": spec["feature_set"],
            "features": list(fit.get("features") or []),
            "reg": spec["reg"],
            "model_kind": fit.get("kind"),
            "training_procedure": "nested_CV_select_then_refit_all_historical14",
            "selected_per_fold": nested["selected_per_fold"],
            "note": "coefficients not serialized; architecture+features+reg frozen",
        }

    manifest = None
    if decision.get("freeze"):
        manifest = freeze_manifest(
            decision=decision,
            selected_per_fold=nested["selected_per_fold"],
            final_allocator=final_allocator,
            cross=cross if decision.get("freeze_as") == "LEARNED_ALLOCATOR" else baselines["B1_ASC"],
        )
        (OUT / "PASSIVE_FIXED600_FULL_STRATEGY_V1.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8",
        )
    else:
        mp = OUT / "PASSIVE_FIXED600_FULL_STRATEGY_V1.json"
        if mp.exists():
            mp.unlink()

    # strip heavy nested events from report
    baselines_light = {}
    for k, v in baselines.items():
        if isinstance(v, dict):
            baselines_light[k] = {kk: vv for kk, vv in v.items() if not str(kk).startswith("_")}
        else:
            baselines_light[k] = v

    learned_minus_asc = {
        "pnl_yen": float((cross.get("total_pnl_yen") or 0) - (baselines["B1_ASC"].get("total_pnl_yen") or 0)),
        "opp_bps": float((cross.get("opp_bps_per_signal") or 0) - (baselines["B1_ASC"].get("opp_bps_per_signal") or 0)),
    }
    learned_minus_hash = {
        "pnl_yen": float(
            (cross.get("total_pnl_yen") or 0) - (baselines["HASH_DIAG"].get("median_pnl_yen") or 0)
        ),
        "opp_bps": float(
            (cross.get("opp_bps_per_signal") or 0) - (baselines["HASH_DIAG"].get("median_opp_bps") or 0)
        ),
    }

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": decision["verdict"],
        "verdict_detail": decision,
        "anchor_sha": ANCHOR_SHA,
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "source_verify": ver,
        "population": {
            "signals": len(panel),
            "fills": EXPECTED_FILLS,
            "candidate_clocks": len({(e["date"], e["signal_time"]) for e in panel}),
        },
        "outer_folds": {
            k: {kk: vv for kk, vv in fr.items() if kk not in ("test_day_pnls",)}
            for k, fr in nested["folds"].items()
        },
        "selected_per_fold": nested["selected_per_fold"],
        "cross_fitted": {k: v for k, v in cross.items() if k not in ("day_means_opp", "day_pnls")},
        "cross_fitted_day_means": cross.get("day_means_opp"),
        "cross_fitted_day_pnls": cross.get("day_pnls"),
        "cross_fitted_summary": {
            "admitted": cross.get("admitted"),
            "blocked": cross.get("blocked"),
            "fills": cross.get("fills"),
            "fill_rate": cross.get("fill_rate_admitted"),
            "total_pnl_yen": cross.get("total_pnl_yen"),
            "opp_bps": cross.get("opp_bps_per_signal"),
            "bps_per_fill": cross.get("bps_per_fill"),
            "pf": cross.get("pf"),
            "positive_days": cross.get("positive_days"),
            "ss_balanced": cross.get("ss_balanced"),
            "day_balanced": cross.get("day_balanced"),
        },
        "baselines": baselines_light,
        "learned_minus_asc": learned_minus_asc,
        "learned_minus_hash_median": learned_minus_hash,
        "decomposition": decomp,
        "quintile_fill": q_fill,
        "quintile_opp": q_opp,
        "lodo": lodo,
        "loso": {k: v for k, v in loso.items() if k != "sample"},
        "concentration": {
            "max_symbol": cross.get("max_symbol_contrib_share"),
            "max_day": cross.get("max_day_contrib_share"),
            "top2_days": cross.get("top2_days"),
            "top5_symbols": cross.get("top5_symbols"),
        },
        "hard_cap_violations": cross.get("hard_cap_violations"),
        "capital_diagnostic": cross.get("capital"),
        "canonical_exit_only": True,
        "actual_exit_timestamp_occupancy": True,
        "pending_reservation": True,
        "no_occupancy_proxy_600s": True,
        "no_new_exit_search": True,
        "no_runtime_change": True,
        "no_short": True,
        "pre_entry_features_only": True,
        "no_fill_feature_leakage": True,
        "no_return_leakage": True,
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "final_allocator": final_allocator,
        "recommended_next": decision.get("next"),
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": ab_ok, "population": ab_pop},
    }

    interim = {
        "run_id": run_id,
        "verdict": decision["verdict"],
        "anchor_sha": ANCHOR_SHA,
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "n_signals": len(panel),
        "n_fills": EXPECTED_FILLS,
        "canonical_exit_only": True,
        "actual_exit_timestamp_occupancy": True,
        "pending_reservation": True,
        "open_plus_pending_cap": 5,
        "expiry_sec": 1.0,
        "duplicate_semantics": "no_overlap_replace",
        "pre_entry_features_only": True,
        "no_fill_feature_leakage": True,
        "no_return_leakage": True,
        "nested_outer_blind": True,
        "inner_lodo": True,
        "cohort_topk": True,
        "deterministic_tiebreak": "symbol_ascending",
        "cross_fitted_summary": report["cross_fitted_summary"],
        "baselines_pnl": {
            "SKIP": 0.0,
            "ASC": baselines["B1_ASC"].get("total_pnl_yen"),
            "DESC": baselines["B2_DESC"].get("total_pnl_yen"),
            "HASH": baselines["B3_HASH"].get("total_pnl_yen"),
            "HASH_median": baselines["HASH_DIAG"].get("median_pnl_yen"),
            "learned": cross.get("total_pnl_yen"),
        },
        "learned_minus_asc": learned_minus_asc,
        "learned_minus_hash_median": learned_minus_hash,
        "decomposition": decomp,
        "lodo": lodo,
        "loso_majority": loso.get("majority_positive"),
        "hard_cap_violations": cross.get("hard_cap_violations"),
        "capital_diagnostic": cross.get("capital"),
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "opened_20260810": False,
        "no_runtime_change": True,
        "no_short": True,
        "submit_cancel_live": "0/0/0",
        "ab_determinism": report["ab_determinism"],
        "recommended_next": decision.get("next"),
        "selected_per_fold": nested["selected_per_fold"],
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "pnl": cross.get("total_pnl_yen"),
            "opp": cross.get("opp_bps_per_signal"),
            "fills": cross.get("fills"),
            "pos_days": cross.get("positive_days"),
            "asc_pnl": baselines["B1_ASC"].get("total_pnl_yen"),
        }],
        "outer": [
            {"block": k, **fr["selected"], **{f"test_{a}": fr["test"].get(a) for a in (
                "total_pnl_yen", "opp_bps_per_signal", "fills", "admitted", "pf", "positive_days"
            )}}
            for k, fr in nested["folds"].items()
        ],
        "baselines": [
            {"name": k, "pnl": v.get("total_pnl_yen"), "opp": v.get("opp_bps_per_signal"),
             "fills": v.get("fills"), "pos": v.get("positive_days"), "pf": v.get("pf")}
            for k, v in baselines_light.items() if isinstance(v, dict) and "total_pnl_yen" in v
        ],
        "days": [
            {"date": d, "opp": (cross.get("day_means_opp") or {}).get(d),
             "pnl": (cross.get("day_pnls") or {}).get(d)}
            for d in sorted((cross.get("day_means_opp") or {}).keys())
        ],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    interim["tests"] = tests
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
    publish(OUT, report, sheets)

    print(f"=== DONE {decision['verdict']} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": decision["verdict"],
        "pnl": cross.get("total_pnl_yen"),
        "opp": cross.get("opp_bps_per_signal"),
        "asc_pnl": baselines["B1_ASC"].get("total_pnl_yen"),
        "hash_med": baselines["HASH_DIAG"].get("median_pnl_yen"),
        "manifest": bool(manifest),
        "next": decision.get("next"),
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
