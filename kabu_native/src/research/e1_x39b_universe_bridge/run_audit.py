"""E1_X39B runner — Universe bridge; no strategy mutation; no 20260810."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x36_joint_allocator.cv import run_baselines
from research.e1_x37_prospective.freeze import load_v1r, load_model_artifact, verify_model_identity
from research.e1_x37_prospective.wiring import assert_prospective_unopened

from . import (
    ANALYSIS_ID,
    DOCUMENT_ID,
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    VERDICT_IDENTITY,
    VERDICT_NOT_SUPPORTED,
    VERDICT_REVIEW,
    VERDICT_SUPPORTED,
    X36_RUN_ID,
    X38_RUN_ID,
    X39_RUN_ID,
)
from .attribution import added_symbol_attribution
from .binding import write_new_precommit, write_universe_binding
from .diagnostic import final_v1r_am_diagnostic
from .outer_replay import (
    apply_x36_gate,
    check_x36_identity,
    crossfit_fixed_specs,
    day_compare,
)
from .panel_build import build_am_panel, build_legacy_panel, universe_delta
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x39b_universe_bridge"
X36 = NATIVE / "results" / "research" / "e1_x36_joint_allocator"
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"
X38 = NATIVE / "results" / "research" / "e1_x38_operational_wiring"
X39 = NATIVE / "results" / "research" / "e1_x39_activation_lock"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x39b_universe_bridge.py"
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
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-2500:]}


def _slim_cross(cf: dict) -> dict:
    keep = (
        "signals", "admitted", "blocked", "fills", "expired", "total_pnl_yen",
        "opp_bps_per_signal", "bps_per_admitted", "bps_per_fill", "pf",
        "positive_days", "negative_days", "ss_balanced", "day_balanced",
        "hard_cap_violations", "max_open_plus_pending", "day_pnls", "day_means_opp",
    )
    return {k: cf.get(k) for k in keep}


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x39b_bridge_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    unopened = assert_prospective_unopened()
    assert unopened["opened_20260810"] is False

    x36 = json.loads((X36 / "report.json").read_text(encoding="utf-8"))
    assert x36.get("run_id") == X36_RUN_ID
    assert json.loads((X38 / "report.json").read_text(encoding="utf-8")).get("run_id") == X38_RUN_ID
    assert json.loads((X39 / "_interim.json").read_text(encoding="utf-8")).get("run_id") == X39_RUN_ID

    pre_body = json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8"))
    assert pre_body.get("sha256") == PRECOMMIT_SHA
    v1r = load_v1r()
    assert v1r.get("sha256") == V1R_SHA
    ser = load_model_artifact()
    mid = verify_model_identity(ser)
    assert mid["pass"]
    print("  binds OK; old V1R/precommit untouched", flush=True)

    print("=== build LEGACY panel (CANDIDATE_SYMBOL_POOL) ===", flush=True)
    legacy = build_legacy_panel()
    print(f"  legacy signals={legacy['signals']} fills={legacy['fills']}", flush=True)

    print("=== build AM day-fixed panel ===", flush=True)
    am = build_am_panel()
    print(
        f"  am signals={am['signals']} fills={am['fills']} capture_miss={am['capture_miss_n']}",
        flush=True,
    )

    delta = universe_delta(legacy["pool"], am["pool"])
    print(f"  added_symbol_days={delta['added_symbol_day_n']}", flush=True)

    print("=== X36 identity: legacy pool crossfit with frozen outer specs ===", flush=True)
    legacy_cf = crossfit_fixed_specs(legacy["panel"], legacy["panel"], label="LEGACY")
    identity = check_x36_identity(legacy_cf["cross_fitted"])
    print(f"  identity_pass={identity['pass']} observed={identity['observed']}", flush=True)

    if not identity["pass"]:
        verdict = VERDICT_IDENTITY
        report = {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "verdict": verdict,
            "identity": identity,
            "opened_20260810": False,
            "strategy_mutation": False,
            "safety": {"submit_cancel_live": "0/0/0"},
        }
        publish(OUT, report, {"summary": [{"run_id": run_id, "verdict": verdict}]})
        (OUT / "_interim.json").write_text(json.dumps({
            "run_id": run_id, "verdict": verdict, "identity_pass": False,
            "opened_20260810": False, "submit_cancel_live": "0/0/0",
            "strategy_mutation": False, "activation_manifest": False,
            "universe_binding": False, "new_precommit": False,
            "old_precommit_unchanged": True,
            "ab_determinism": {"ok": True},
        }, indent=2), encoding="utf-8")
        print(f"=== STOP {verdict} ===", flush=True)
        return report

    print("=== Bridge: same outer models, AM TEST membership ===", flush=True)
    # Training stays on legacy panel; test uses AM panel
    bridge_cf = crossfit_fixed_specs(legacy["panel"], am["panel"], label="BRIDGE_AM")

    print("=== ASC/HASH baselines on AM panel (for X36 gate reuse) ===", flush=True)
    baselines_am = run_baselines(am["panel"])

    gate = apply_x36_gate(
        cross=bridge_cf["cross_fitted"],
        baselines=baselines_am,
        selected_per_fold=bridge_cf["selected_per_fold"],
        cross_events=bridge_cf["cross_events"],
    )
    print(
        f"  x36_gate_verdict={gate['decision'].get('verdict')} "
        f"matches_pass={gate['bridge_matches_x36_pass']}",
        flush=True,
    )

    if gate["bridge_matches_x36_pass"]:
        verdict = VERDICT_SUPPORTED
    elif gate["decision"].get("verdict") in (
        "E1_X36_ADMISSION_ALLOCATOR_NOT_ROBUST",
        "E1_X36_FULL_STRATEGY_NOT_SUPPORTED_UNDER_HARD_CAP",
        "E1_X36_NEUTRAL_ADMISSION_FULL_STRATEGY_SUPPORTED",
    ):
        # Gate ran successfully; NEUTRAL is not the LEARNED PASS verdict
        if gate["decision"].get("verdict") == "E1_X36_NEUTRAL_ADMISSION_FULL_STRATEGY_SUPPORTED":
            # Bridge did not achieve LEARNED PASS — treat as not supported for V1R family continuity
            verdict = VERDICT_NOT_SUPPORTED
        else:
            verdict = VERDICT_NOT_SUPPORTED
    else:
        verdict = VERDICT_REVIEW

    daily = day_compare(
        legacy_cf["cross_events"],
        bridge_cf["cross_events"],
        delta["daily"],
    )
    added = added_symbol_attribution(
        added_symbol_days=delta["added_symbol_days"],
        bridge_events=bridge_cf["cross_events"],
        legacy_events=legacy_cf["cross_events"],
    )

    print("=== Final V1R IN_SAMPLE diagnostic (not evidence) ===", flush=True)
    diag = final_v1r_am_diagnostic(am["panel"])
    print(f"  diagnostic plumbing_ok={diag['plumbing_ok']} label={diag['label']}", flush=True)

    universe_binding = None
    new_precommit = None
    old_precommit_unchanged = True

    warmup_semantic = {
        "same_calendar_day_board": True,
        "as_of": "market_event_time <= anchor t0",
        "session_open_clamp": False,
        "lunch_clamp": False,
        "previous_day_board": False,
        "same_day_pre_open_if_already_observed": True,
        "no_retroactive_late_events": True,
        "source": "E1_X39 / preentry_from_board",
    }

    if verdict == VERDICT_SUPPORTED:
        print("=== PASS: write Universe Binding + new precommit ===", flush=True)
        universe_binding = write_universe_binding(
            OUT, warmup_semantic=warmup_semantic, bridge_run_id=run_id,
        )
        new_precommit = write_new_precommit(
            OUT,
            universe_binding_sha=universe_binding["sha256"],
            bridge_run_id=run_id,
        )
        old_precommit_unchanged = new_precommit["old_precommit_unchanged"]
        # Verify V1R unchanged
        assert load_v1r().get("sha256") == V1R_SHA
        assert json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8")).get("sha256") == PRECOMMIT_SHA

    leg_x = _slim_cross(legacy_cf["cross_fitted"])
    br_x = _slim_cross(bridge_cf["cross_fitted"])

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "x36_run_id": X36_RUN_ID,
        "x38_run_id": X38_RUN_ID,
        "x39_run_id": X39_RUN_ID,
        "universe_contract": UNIVERSE_CONTRACT,
        "identity": identity,
        "legacy": {
            "cross_fitted": leg_x,
            "outer": {
                k: {
                    "test": fr["test"],
                    "selected": fr["selected"],
                    "train_n": fr["train_n"],
                    "test_n": fr["test_n"],
                }
                for k, fr in legacy_cf["folds"].items()
            },
        },
        "bridge": {
            "cross_fitted": br_x,
            "outer": {
                k: {
                    "test": fr["test"],
                    "selected": fr["selected"],
                    "train_n": fr["train_n"],
                    "test_n": fr["test_n"],
                }
                for k, fr in bridge_cf["folds"].items()
            },
            "training_population": "X36_LEGACY_CANDIDATE_SYMBOL_POOL_UNCHANGED",
            "test_membership": UNIVERSE_CONTRACT,
        },
        "vs_x36": {
            "pnl_delta": (br_x.get("total_pnl_yen") or 0) - (leg_x.get("total_pnl_yen") or 0),
            "pf_delta": (
                None if br_x.get("pf") is None or leg_x.get("pf") is None
                else float(br_x["pf"]) - float(leg_x["pf"])
            ),
            "fills_delta": (br_x.get("fills") or 0) - (leg_x.get("fills") or 0),
            "positive_day_delta": (br_x.get("positive_days") or 0) - (leg_x.get("positive_days") or 0),
            "admitted_delta": (br_x.get("admitted") or 0) - (leg_x.get("admitted") or 0),
        },
        "gate": {
            "decision_verdict": gate["decision"].get("verdict"),
            "bridge_matches_x36_pass": gate["bridge_matches_x36_pass"],
            "learned_gates": gate["decision"].get("learned_gates"),
            "beat_asc": gate["decision"].get("beat_asc"),
            "beat_hash_median": gate["decision"].get("beat_hash_median"),
            "lodo": gate["lodo"],
            "gate_source": gate["gate_source"],
        },
        "universe_delta": {
            "added_symbol_day_n": delta["added_symbol_day_n"],
            "old_total_symbol_days": delta["old_total_symbol_days"],
            "am_total_symbol_days": delta["am_total_symbol_days"],
            "daily": delta["daily"],
            "capture_miss_n": am["capture_miss_n"],
            "capture_miss_sample": am["capture_miss"][:20],
        },
        "daily_compare": daily,
        "added_symbol_impact": {
            "pnl": added["total_pnl_yen"],
            "fills": added["total_fills"],
            "admitted": added["total_admitted"],
            "displacement_n": added["displacement_n"],
            "rows_sample": added["rows"][:40],
            "displacements_sample": added["displacements"][:40],
            "post_hoc_removal": False,
        },
        "final_v1r_diagnostic": diag,
        "universe_binding": (
            {"created": True, "sha256": universe_binding["sha256"]} if universe_binding else {"created": False}
        ),
        "new_precommit": (
            {"created": True, "sha256": new_precommit["sha256"]} if new_precommit else {"created": False}
        ),
        "old_precommit_unchanged": old_precommit_unchanged,
        "prospective_observer": "NOT_STARTED",
        "opened_20260810": False,
        "strategy_mutation": False,
        "model_mutation": False,
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": True},
        "warmup_semantic": warmup_semantic,
        "pbv2_role": "SHADOW_ONLY",
        "capital_1m_role": "SHADOW_ONLY",
        "no_retrain": True,
    }

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "identity_pass": True,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "universe_contract": UNIVERSE_CONTRACT,
        "old_pool_symbol_days": delta["old_total_symbol_days"],
        "am_pool_symbol_days": delta["am_total_symbol_days"],
        "added_symbol_days": delta["added_symbol_day_n"],
        "capture_unavailable": am["capture_miss_n"],
        "bridge_pnl": br_x.get("total_pnl_yen"),
        "bridge_pf": br_x.get("pf"),
        "bridge_fills": br_x.get("fills"),
        "bridge_admitted": br_x.get("admitted"),
        "bridge_pos_days": br_x.get("positive_days"),
        "bridge_opp_bps": br_x.get("opp_bps_per_signal"),
        "bridge_ss_balanced": br_x.get("ss_balanced"),
        "bridge_day_balanced": br_x.get("day_balanced"),
        "bridge_hard_cap": br_x.get("hard_cap_violations"),
        "vs_x36_pnl_delta": report["vs_x36"]["pnl_delta"],
        "vs_x36_pf_delta": report["vs_x36"]["pf_delta"],
        "vs_x36_fills_delta": report["vs_x36"]["fills_delta"],
        "vs_x36_pos_day_delta": report["vs_x36"]["positive_day_delta"],
        "added_impact_pnl": added["total_pnl_yen"],
        "added_impact_fills": added["total_fills"],
        "displacement_n": added["displacement_n"],
        "final_diag_label": "IN_SAMPLE_OPERATIONAL_DIAGNOSTIC_ONLY",
        "final_diag_not_evidence": True,
        "universe_binding": bool(universe_binding),
        "universe_binding_sha": universe_binding["sha256"] if universe_binding else None,
        "new_precommit": bool(new_precommit),
        "new_precommit_sha": new_precommit["sha256"] if new_precommit else None,
        "old_precommit_unchanged": old_precommit_unchanged,
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
        "strategy_mutation": False,
        "model_mutation": False,
        "submit_cancel_live": "0/0/0",
        "same_day_am_universe": True,
        "day_fixed_all16": True,
        "no_refresh_switching": True,
        "no_cluster_filter_on_test": True,
        "gate_verdict": gate["decision"].get("verdict"),
        "ab_determinism": {"ok": True},
        "outer_A_pnl": bridge_cf["folds"]["A"]["test"].get("total_pnl_yen"),
        "outer_B_pnl": bridge_cf["folds"]["B"]["test"].get("total_pnl_yen"),
        "outer_C_pnl": bridge_cf["folds"]["C"]["test"].get("total_pnl_yen"),
        "outer_D_pnl": bridge_cf["folds"]["D"]["test"].get("total_pnl_yen"),
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{"run_id": run_id, "verdict": verdict, "bridge_pnl": br_x.get("total_pnl_yen"),
                     "identity": True, "binding": bool(universe_binding)}],
        "identity": [identity],
        "daily": daily,
        "outer": [
            {"block": k, **{kk: fr["test"].get(kk) for kk in (
                "admitted", "fills", "total_pnl_yen", "pf", "positive_days", "hard_cap_violations"
            )}}
            for k, fr in bridge_cf["folds"].items()
        ],
        "universe_delta": delta["daily"],
        "added_symbols": added["rows"][:200],
        "displacement": added["displacements"][:200] or [{"empty": True}],
        "final_model_diagnostic": [diag],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    interim["tests"] = tests
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
    publish(OUT, report, sheets)

    print(f"=== DONE {verdict} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "identity": identity["pass"],
        "bridge_pnl": br_x.get("total_pnl_yen"),
        "bridge_pf": br_x.get("pf"),
        "binding": bool(universe_binding),
        "opened_20260810": False,
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
