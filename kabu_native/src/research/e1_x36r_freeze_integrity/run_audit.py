"""E1_X36R runner — exact freeze + concentration reconciliation."""
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
from research.e1_x36_joint_allocator.panel import enrich_events

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
    FINAL_FEATURE_SET,
    FINAL_FEATURES,
    FINAL_REG,
    FORBIDDEN_FROM,
    SOURCE_X36_RUN,
    V1_SHA,
)
from .concentration import (
    concentration_reconcile,
    d1_contribution_removal,
    d2_candidate_removal_replay,
    loso_285a_detail,
)
from .identity import final_refit_identity, reproduce_cross_fitted
from .provenance import document_provenance
from .publish import publish
from .serialize import fit_a1_fill, serialize_fill_model, training_panel_fingerprint
from .verdict import decide_verdict, freeze_v1r

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x36r_freeze_integrity"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"
X35R = NATIVE / "results" / "research" / "e1_x35r_exit_contract"
X36 = NATIVE / "results" / "research" / "e1_x36_joint_allocator"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x36r_freeze_integrity.py"
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
    v1 = json.loads((X36 / "PASSIVE_FIXED600_FULL_STRATEGY_V1.json").read_text(encoding="utf-8"))
    x36 = json.loads((X36 / "report.json").read_text(encoding="utf-8"))
    return {
        "entry_ok": _sha_ok(entry, ENTRY_SHA),
        "anchor_ok": _sha_ok(anchor, ANCHOR_SHA),
        "exec_ok": _sha_ok(pol, EXEC_SHA),
        "exit_ok": _sha_ok(exit_m, EXIT_SHA),
        "v1_ok": _sha_ok(v1, V1_SHA),
        "x36_run": x36.get("run_id"),
        "x36_ok": x36.get("run_id") == SOURCE_X36_RUN,
        "x36_capital": x36.get("capital_diagnostic"),
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x36r_freeze_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok") and mapping.get("mapping_sha") == BOARD_MAPPING_SHA
    ver = _verify()
    assert all(ver[k] for k in ("entry_ok", "anchor_ok", "exec_ok", "exit_ok", "v1_ok", "x36_ok")), ver
    print("  upstream SHA binds OK", flush=True)

    provenance = document_provenance()
    print(f"  provenance_ok={provenance['provenance_ok']} selected={provenance.get('selected_feature_set')}/{provenance.get('selected_reg')}", flush=True)

    print("=== panel ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"]
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    boards = load_boards_for_symbols(sorted({(a["date"], a["symbol"]) for a in planned}))
    raw = build_events(planned, boards)
    assert len(raw) == EXPECTED_SIGNALS
    panel = enrich_events(raw, boards)
    assert sum(1 for e in panel if e.get("filled")) == EXPECTED_FILLS
    assert not any(e["date"] >= FORBIDDEN_FROM for e in panel)
    print(f"  panel n={len(panel)}", flush=True)

    print("=== cross-fitted replay identity (frozen OUTER_SPECS) ===", flush=True)
    cross = reproduce_cross_fitted(panel)
    print(f"  identity_pass={cross['identity_vs_x36']['pass']} observed={cross['identity_vs_x36']['observed']}", flush=True)

    print("=== final H14 refit serialize ===", flush=True)
    # deterministic row order for fingerprint / fit
    panel_ord = sorted(panel, key=lambda e: (e["date"], float(e["signal_time"]), str(e["symbol"])))
    fit = fit_a1_fill(panel_ord, feature_set=FINAL_FEATURE_SET, reg=FINAL_REG)
    assert fit.get("kind") == "fill"
    ser = serialize_fill_model(fit, train=panel_ord)
    panel_fp = training_panel_fingerprint(panel_ord, FINAL_FEATURES)
    assert panel_fp["contains_20260810_plus"] is False
    print(f"  model_sha={ser['model_artifact_sha256'][:16]}... panel_sha={panel_fp['sha256'][:16]}...", flush=True)
    print(f"  coef={ser['coefficients']}", flush=True)
    print(f"  intercept={ser['intercept']}", flush=True)

    final_id = final_refit_identity(panel_ord, fit, ser)
    print(f"  final_refit_identity_pass={final_id['pass']} max_delta={final_id['max_absolute_score_delta']}", flush=True)

    # A/B serialize determinism
    fit_b = fit_a1_fill(panel_ord, feature_set=FINAL_FEATURE_SET, reg=FINAL_REG)
    ser_b = serialize_fill_model(fit_b, train=panel_ord)
    ab_ok = ser["model_artifact_sha256"] == ser_b["model_artifact_sha256"] and ab_pop["ok"]

    print("=== concentration + 285A ===", flush=True)
    conc = concentration_reconcile(cross["events"])
    print(
        f"  max_share={conc['max_symbol_contrib_share']:.4f} "
        f"285A_net/total={conc['symbol_285A']['share_of_total_net']:.4f}",
        flush=True,
    )
    d1 = d1_contribution_removal(cross["events"])
    print(f"  D1 remaining_pnl={d1['remaining_total_pnl_yen']} pos={d1['positive_days']} opp={d1['opp_bps_per_signal']}", flush=True)
    d2 = d2_candidate_removal_replay(panel)
    print(f"  D2 pnl={d2['total_pnl_yen']} fills={d2['fills']} pos={d2['positive_days']} opp={d2['opp_bps_per_signal']}", flush=True)
    loso285 = loso_285a_detail(cross["events"])
    print(f"  LOSO 285A opp={loso285['opp_bps']} pnl={loso285['total_pnl_yen']} pos={loso285['positive_days']}", flush=True)

    orig_pnl = float(cross["summary"].get("total_pnl_yen") or 0.0)
    decision = decide_verdict(
        provenance=provenance,
        final_id=final_id,
        cross_id=cross,
        conc=conc,
        d1=d1,
        d2=d2,
        orig_pnl=orig_pnl,
    )
    print(f"  verdict={decision['verdict']}", flush=True)

    capital = ver.get("x36_capital") or {
        "max_concurrent_notional_yen": 6759000.0,
        "max_pending_reserved_notional_yen": 6828000.0,
        "qualification": "diagnostic_only_not_LIVE_DEPLOYABLE_not_SAFE_CAPITAL_CONFIRMED",
    }
    if isinstance(capital, dict):
        capital = {
            **capital,
            "qualification": "diagnostic_only_not_LIVE_DEPLOYABLE_not_SAFE_CAPITAL_CONFIRMED",
        }

    manifest = None
    if decision.get("freeze"):
        manifest = freeze_v1r(
            ser=ser,
            panel_fp=panel_fp,
            provenance=provenance,
            cross_summary={
                "total_pnl_yen": cross["summary"].get("total_pnl_yen"),
                "opp_bps": cross["summary"].get("opp_bps_per_signal"),
            },
            capital=capital,
        )
        (OUT / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8",
        )
        # also store model artifact alone
        (OUT / "allocator_model_artifact.json").write_text(
            json.dumps({k: v for k, v in ser.items() if not str(k).startswith("_")}, indent=2, default=str),
            encoding="utf-8",
        )
    else:
        for name in ("PASSIVE_FIXED600_FULL_STRATEGY_V1R.json", "allocator_model_artifact.json"):
            p = OUT / name
            if p.exists():
                p.unlink()

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": decision["verdict"],
        "verdict_detail": decision,
        "source_x36_run": SOURCE_X36_RUN,
        "v1_sha": V1_SHA,
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "anchor_sha": ANCHOR_SHA,
        "source_verify": ver,
        "provenance": provenance,
        "serialized_model": {k: v for k, v in ser.items() if not str(k).startswith("_")},
        "training_panel_fingerprint": panel_fp,
        "final_refit_identity": final_id,
        "cross_fitted_identity": cross["identity_vs_x36"],
        "cross_fitted_summary": {
            k: cross["summary"].get(k)
            for k in (
                "admitted", "fills", "total_pnl_yen", "opp_bps_per_signal", "pf",
                "positive_days", "ss_balanced", "hard_cap_violations",
            )
        },
        "fold_model_shas": {
            b: v.get("model_artifact_sha256") for b, v in cross["fold_models"].items()
        },
        "concentration": conc,
        "d1_285A": d1,
        "d2_285A": d2,
        "loso_285A": loso285,
        "capital_diagnostic": capital,
        "no_model_retune": True,
        "no_new_allocator_search": True,
        "no_symbol_identity_feature": True,
        "no_runtime_change": True,
        "no_short": True,
        "performance_sot": "X36_CROSS_FITTED",
        "final_refit_in_sample_not_evidence": True,
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
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
        "source_x36_run": SOURCE_X36_RUN,
        "v1_sha": V1_SHA,
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "anchor_sha": ANCHOR_SHA,
        "provenance_ok": provenance.get("provenance_ok"),
        "final_selection_provenance": provenance,
        "coefficients": ser.get("coefficients"),
        "intercept": ser.get("intercept"),
        "feature_order": ser.get("feature_order"),
        "preprocessing": ser.get("preprocessing"),
        "training_panel_sha": panel_fp.get("sha256"),
        "model_artifact_sha": ser.get("model_artifact_sha256"),
        "score_replay_pass": final_id.get("pass"),
        "admission_identity": final_id.get("admission_identity"),
        "cross_fitted_identity_pass": cross["identity_vs_x36"].get("pass"),
        "cross_fitted_summary": report["cross_fitted_summary"],
        "concentration_formula": conc.get("formula_existing"),
        "max_symbol_contrib_share": conc.get("max_symbol_contrib_share"),
        "285A_net_pnl": conc["symbol_285A"]["net_pnl_yen"],
        "285A_share_of_total_net": conc["symbol_285A"]["share_of_total_net"],
        "d1_285A": d1,
        "d2_285A": d2,
        "loso_285A": loso285,
        "no_symbol_identity_feature": True,
        "no_model_retune": True,
        "no_runtime_change": True,
        "opened_20260810": False,
        "contains_20260810": False,
        "submit_cancel_live": "0/0/0",
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "recommended_next": decision.get("next"),
        "ab_determinism": report["ab_determinism"],
        "capital_diagnostic": capital,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "cross_id": cross["identity_vs_x36"].get("pass"),
            "final_id": final_id.get("pass"),
            "285A_net_share": conc["symbol_285A"]["share_of_total_net"],
            "d1_pnl": d1.get("remaining_total_pnl_yen"),
            "d2_pnl": d2.get("total_pnl_yen"),
            "v1r": bool(manifest),
        }],
        "coef": [{"feature": f, "coef": c} for f, c in zip(ser["feature_order"], ser["coefficients"])],
        "concentration": [conc["symbol_285A"]],
        "d1": [d1],
        "d2": [d2],
        "loso285": [loso285],
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
        "cross_id": cross["identity_vs_x36"].get("pass"),
        "manifest": bool(manifest),
        "sha": (manifest or {}).get("sha256"),
        "next": decision.get("next"),
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
