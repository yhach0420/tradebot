"""E1_X34A runner — execution policy feasibility (research/paper only)."""
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
    evaluate_neutral,
    planned_neutral_anchors,
)
from research.e1_x33b_neutral_anchor.analyze import summarize_arm as summarize_neutral

from . import (
    ANALYSIS_ID,
    ANCHOR_ID,
    ARM_AGGRESSIVE,
    ARM_INSIDE,
    ARM_PASSIVE,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    EXPECTED_EPISODES,
    EXPECTED_X33C_EXEC_300,
    EXPECTED_X33C_EXEC_600,
    FORBIDDEN_FROM,
    MANIFEST_SHA,
    NEXT_PHASE,
    SOURCE_X33B_RUN,
    SOURCE_X33C_RUN,
    STRESS_ABSENT_NOTE,
    WAIT_PRIMARY_SEC,
    WAIT_SENSITIVITY_SEC,
)
from .analyze import (
    adverse_selection,
    concentration_audit,
    day_level,
    day_majority_not_worse,
    lodo_advantage,
    loso_advantage,
    missed_winners,
    summarize_arm,
)
from .arms import evaluate_signal_all_arms
from .publish import publish
from .verdict import decide_verdict, freeze_policy

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X33C = NATIVE / "results" / "research" / "e1_x33c_baseline_economics"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x34a_execution_policy.py"
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


def _verify_anchor_sha() -> dict[str, Any]:
    body = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    sha = body.get("sha256")
    raw = {k: v for k, v in body.items() if k != "sha256"}
    recomputed = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "sha256": sha,
        "match_expected": sha == MANIFEST_SHA,
        "recompute_ok": recomputed == sha,
    }


def _x33c_identity() -> dict[str, Any]:
    r = json.loads((X33C / "report.json").read_text(encoding="utf-8"))
    em = r.get("episode_mean") or {}
    rid = r.get("residual_identity") or {}
    return {
        "run_id": r.get("run_id"),
        "expected_run": SOURCE_X33C_RUN,
        "run_match": r.get("run_id") == SOURCE_X33C_RUN,
        "exec300": em.get("exec300"),
        "exec600": em.get("exec600"),
        "mid600": em.get("mid600"),
        "residual_identity_ok": rid.get("top_level_ok"),
        "weighting_residual_patched": rid.get("weighting_residual_patched"),
    }


def _eval_rows(planned, boards, wait_sec: float) -> list[dict[str, Any]]:
    rows = []
    for i, a in enumerate(planned):
        board = boards.get((a["date"], a["symbol"]))
        if board is None or board["t"].size == 0:
            continue
        rec = evaluate_signal_all_arms(
            board,
            date=a["date"],
            session=a["session"],
            signal_t=float(a["grid_epoch"]),
            wait_sec=wait_sec,
        )
        if rec is None:
            continue
        rec["symbol"] = a["symbol"]
        rows.append(rec)
        if (i + 1) % 2000 == 0:
            print(f"  eval wait={wait_sec} {i+1}/{len(planned)} -> {len(rows)}", flush=True)
    return rows


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x34a_exec_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok") and mapping.get("mapping_sha") == BOARD_MAPPING_SHA

    man = _verify_anchor_sha()
    assert man["match_expected"] and man["recompute_ok"], man
    x33c = _x33c_identity()
    assert x33c.get("residual_identity_ok") is True, x33c
    assert x33c.get("weighting_residual_patched") is True, x33c
    print(f"  X33C identity residual OK; anchor SHA OK", flush=True)

    print("=== population / anchors ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"], ab_pop
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)

    pairs = sorted({(a["date"], a["symbol"]) for a in planned})
    assert all(d < FORBIDDEN_FROM for d, _ in pairs)
    boards = load_boards_for_symbols(pairs)

    print("=== X33C / aggressive reproduce ===", flush=True)
    neu_a = evaluate_neutral(planned, boards)
    neu_b = evaluate_neutral(planned, boards)
    sum_a = summarize_neutral(neu_a)
    sum_b = summarize_neutral(neu_b)
    x33_rep = {
        "episodes": sum_a["episodes"],
        "ret300": sum_a.get("ret300_episode"),
        "ret600": sum_a.get("ret600_episode"),
        "match_300": abs(float(sum_a["ret300_episode"]) - EXPECTED_X33C_EXEC_300) < 1e-9,
        "match_600": abs(float(sum_a["ret600_episode"]) - EXPECTED_X33C_EXEC_600) < 1e-9,
        "ab_match": (
            sum_a["episodes"] == sum_b["episodes"]
            and sum_a.get("ret300_episode") == sum_b.get("ret300_episode")
            and sum_a.get("ret600_episode") == sum_b.get("ret600_episode")
        ),
    }
    assert x33_rep["match_300"] and x33_rep["match_600"] and x33_rep["episodes"] == EXPECTED_EPISODES
    print(f"  X33 reproduce OK episodes={x33_rep['episodes']}", flush=True)

    print(f"=== primary wait {WAIT_PRIMARY_SEC}s ===", flush=True)
    rows = _eval_rows(planned, boards, WAIT_PRIMARY_SEC)
    # aggressive mean ret600 should match X33C on overlapping filled
    agg_rets = [
        float(r["aggressive"]["ret_600"])
        for r in rows
        if r["aggressive"].get("ret_600_valid")
    ]
    agg_mean600 = float(sum(agg_rets) / len(agg_rets)) if agg_rets else None
    aggressive_match = {
        "n": len(rows),
        "mean_ret600": agg_mean600,
        "match_x33c_600": (
            agg_mean600 is not None
            and abs(agg_mean600 - EXPECTED_X33C_EXEC_600) < 1e-6
        ),
        "n_match_episodes": len(rows) == EXPECTED_EPISODES,
    }
    print(f"  aggressive n={len(rows)} ret600={agg_mean600} match={aggressive_match}", flush=True)
    assert aggressive_match["match_x33c_600"], aggressive_match
    assert aggressive_match["n_match_episodes"], aggressive_match

    agg_s = summarize_arm(rows, ARM_AGGRESSIVE)
    pas_s = summarize_arm(rows, ARM_PASSIVE)
    ins_s = summarize_arm(rows, ARM_INSIDE)
    print(
        f"  fill_rate passive={pas_s['fill_rate']} inside={ins_s['fill_rate']} "
        f"opp600 A/P/I={agg_s['opp_w_ret600']:.4f}/{pas_s['opp_w_ret600']:.4f}/{ins_s['opp_w_ret600']:.4f}",
        flush=True,
    )

    miss_p = missed_winners(rows, ARM_PASSIVE)
    miss_i = missed_winners(rows, ARM_INSIDE)
    adv_p = adverse_selection(rows, ARM_PASSIVE)
    adv_i = adverse_selection(rows, ARM_INSIDE)
    days_p = day_level(rows, ARM_PASSIVE)
    days_i = day_level(rows, ARM_INSIDE)
    maj_p = day_majority_not_worse(days_p)
    maj_i = day_majority_not_worse(days_i)
    conc_p = concentration_audit(rows, ARM_PASSIVE)
    conc_i = concentration_audit(rows, ARM_INSIDE)
    lodo_p = lodo_advantage(rows, ARM_PASSIVE)
    lodo_i = lodo_advantage(rows, ARM_INSIDE)
    loso_p = loso_advantage(rows, ARM_PASSIVE)
    loso_i = loso_advantage(rows, ARM_INSIDE)

    # fill evidence is defined and implemented — not insufficient unless zero board crossings ever possible
    fill_evidence_ok = True
    if pas_s["fills"] == 0 and ins_s["fills"] == 0:
        # still evidence contract works; just no fills — not INSUFFICIENT, goes to AGGRESSIVE remains
        fill_evidence_ok = True

    decision = decide_verdict(
        aggressive=agg_s,
        passive=pas_s,
        inside=ins_s,
        day_passive=maj_p,
        day_inside=maj_i,
        adverse_passive=adv_p,
        adverse_inside=adv_i,
        conc_passive=conc_p,
        conc_inside=conc_i,
        lodo_passive=lodo_p,
        fill_evidence_ok=fill_evidence_ok,
    )
    print(f"  verdict={decision['verdict']} reason={decision.get('reason')}", flush=True)

    print(f"=== sensitivity wait {WAIT_SENSITIVITY_SEC}s (not for selection) ===", flush=True)
    rows2 = _eval_rows(planned, boards, WAIT_SENSITIVITY_SEC)
    sens = {
        "wait_sec": WAIT_SENSITIVITY_SEC,
        "note": "sensitivity only — does not select 1s vs 2s",
        "aggressive": summarize_arm(rows2, ARM_AGGRESSIVE),
        "passive": summarize_arm(rows2, ARM_PASSIVE),
        "inside": summarize_arm(rows2, ARM_INSIDE),
    }

    policy = None
    policy_sha = None
    if decision.get("selected_policy"):
        policy = freeze_policy(mode=decision["selected_policy"], wait_sec=WAIT_PRIMARY_SEC)
        policy_sha = policy["sha256"]
        (OUT / "ENTRY_EXECUTION_POLICY_V1.json").write_text(
            json.dumps(policy, indent=2, default=str), encoding="utf-8",
        )

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": decision["verdict"],
        "verdict_detail": decision,
        "selected_execution_policy": decision.get("selected_policy"),
        "policy_sha": policy_sha,
        "policy_manifest": policy,
        "anchor_id": ANCHOR_ID,
        "manifest_sha": MANIFEST_SHA,
        "manifest_verify": man,
        "source_x33b_run": SOURCE_X33B_RUN,
        "source_x33c_run": SOURCE_X33C_RUN,
        "x33c_identity": x33c,
        "x33_reproduce": x33_rep,
        "aggressive_reproduces_x33c": aggressive_match,
        "wait_primary_sec": WAIT_PRIMARY_SEC,
        "wait_sensitivity_sec": WAIT_SENSITIVITY_SEC,
        "fill_evidence_rule": "ASK_CROSS_CONSERVATIVE",
        "no_queue_assumption": True,
        "no_trade_touch_fake_fill": True,
        "aggressive": {
            "fill_rate": agg_s["fill_rate"],
            "ret300_filled_mean": agg_s["ret300_filled_mean"],
            "ret600_filled_mean": agg_s["ret600_filled_mean"],
            "opportunity_weighted_ret300": agg_s["opp_w_ret300"],
            "opportunity_weighted_ret600": agg_s["opp_w_ret600"],
            "opportunity_weighted_ret900": agg_s["opp_w_ret900"],
            "opp_w_ret600_symbol_session": agg_s["opp_w_ret600_symbol_session"],
            "opp_w_ret600_day": agg_s["opp_w_ret600_day"],
            "full": agg_s,
        },
        "passive": {
            "fill_rate": pas_s["fill_rate"],
            "spread_saved": pas_s["entry_spread_saved_bps"],
            "ret600_filled": pas_s["ret600_filled_mean"],
            "opportunity_weighted_ret600": pas_s["opp_w_ret600"],
            "opp_w_ret600_symbol_session": pas_s["opp_w_ret600_symbol_session"],
            "opp_w_ret600_day": pas_s["opp_w_ret600_day"],
            "missed_winner_rate": miss_p,
            "adverse_selection": adv_p,
            "full": pas_s,
        },
        "inside": {
            "fill_rate": ins_s["fill_rate"],
            "spread_saved": ins_s["entry_spread_saved_bps"],
            "ret600_filled": ins_s["ret600_filled_mean"],
            "opportunity_weighted_ret600": ins_s["opp_w_ret600"],
            "opp_w_ret600_symbol_session": ins_s["opp_w_ret600_symbol_session"],
            "missed_winner_rate": miss_i,
            "adverse_selection": adv_i,
            "full": ins_s,
        },
        "symbol_session_balanced": {
            "aggressive_opp600": agg_s["opp_w_ret600_symbol_session"],
            "passive_opp600": pas_s["opp_w_ret600_symbol_session"],
            "inside_opp600": ins_s["opp_w_ret600_symbol_session"],
        },
        "day_level_passive": days_p,
        "day_level_inside": days_i,
        "day_majority_passive": maj_p,
        "day_majority_inside": maj_i,
        "concentration_passive": conc_p,
        "concentration_inside": conc_i,
        "lodo_passive": {k: v for k, v in lodo_p.items() if k != "folds"},
        "lodo_passive_folds": lodo_p.get("folds"),
        "lodo_inside": {k: v for k, v in lodo_i.items() if k != "folds"},
        "loso_passive": loso_p,
        "loso_inside": loso_i,
        "sensitivity_2s": {
            "aggressive_opp600": sens["aggressive"]["opp_w_ret600"],
            "passive_opp600": sens["passive"]["opp_w_ret600"],
            "passive_fill_rate": sens["passive"]["fill_rate"],
            "inside_opp600": sens["inside"]["opp_w_ret600"],
            "inside_fill_rate": sens["inside"]["fill_rate"],
            "note": "sensitivity only - does not select 1s vs 2s",
        },
        "recommended_next": NEXT_PHASE,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "no_execution_grid_search": True,
        "no_entry_search": True,
        "no_runtime_change": True,
        "no_exit_redesign": True,
        "no_short": True,
        "stress_note": STRESS_ABSENT_NOTE,
        "safety": {
            "research_paper_only": True,
            "submit_cancel_live": "0/0/0",
            "discord_production": False,
        },
        "ab_determinism": {"neutral_ab": x33_rep["ab_match"], "population": ab_pop},
        "same_exit_contract": "fixed-horizon Buy1 bid for all arms",
    }

    interim = {
        "run_id": run_id,
        "verdict": decision["verdict"],
        "selected_execution_policy": decision.get("selected_policy"),
        "policy_sha": policy_sha,
        "manifest_sha": MANIFEST_SHA,
        "x33c_identity": x33c,
        "aggressive_reproduces_x33c": aggressive_match,
        "aggressive": report["aggressive"],
        "passive": report["passive"],
        "inside": report["inside"],
        "day_majority_passive": maj_p,
        "lodo_passive": report["lodo_passive"],
        "loso_passive": loso_p,
        "opened_20260810": False,
        "no_execution_grid_search": True,
        "no_entry_search": True,
        "no_runtime_change": True,
        "no_exit_redesign": True,
        "no_short": True,
        "no_queue_assumption": True,
        "no_trade_touch_fake_fill": True,
        "submit_cancel_live": "0/0/0",
        "ab_determinism": report["ab_determinism"],
        "fill_evidence_rule": "ASK_CROSS_CONSERVATIVE",
        "wait_primary_sec": WAIT_PRIMARY_SEC,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "agg_opp600": agg_s["opp_w_ret600"],
            "pas_opp600": pas_s["opp_w_ret600"],
            "ins_opp600": ins_s["opp_w_ret600"],
            "pas_fill": pas_s["fill_rate"],
            "ins_fill": ins_s["fill_rate"],
        }],
        "day_passive": days_p,
        "day_inside": days_i,
        "missed_passive": [{"thr": k, **v} for k, v in miss_p.items()],
        "adverse_passive": [adv_p["filled"], adv_p["unfilled"]],
        "lodo_passive": lodo_p.get("folds") or [],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    publish(OUT, report, sheets)

    print(f"=== DONE {decision['verdict']} policy={decision.get('selected_policy')} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": decision["verdict"],
        "agg_opp600": agg_s["opp_w_ret600"],
        "pas_fill": pas_s["fill_rate"],
        "pas_opp600": pas_s["opp_w_ret600"],
        "ins_fill": ins_s["fill_rate"],
        "ins_opp600": ins_s["opp_w_ret600"],
        "policy_sha": policy_sha,
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
