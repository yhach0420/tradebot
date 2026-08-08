"""E1_X33C runner: baseline economics + latency attribution (research/paper only)."""
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

import numpy as np

from research.e1_x28_executable_joint.board import verify_board_mapping
from research.e1_x31_population_direction.identity import ab_identity, reproduce_population
from research.e1_x32_upstream_attribution.eval_stages import load_boards_for_symbols
from research.e1_x33b_neutral_anchor.neutral import (
    candidate_symbols_by_day,
    evaluate_neutral,
    planned_neutral_anchors,
)

from . import (
    ANALYSIS_ID,
    ANCHOR_ID,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    EXPECTED_EPISODES,
    EXPECTED_EXEC_300,
    EXPECTED_EXEC_600,
    FORBIDDEN_FROM,
    HISTORICAL_DAYS,
    LATENCY_MATERIAL,
    LATENCY_MATERIAL_BPS,
    LATENCY_MINOR,
    LATENCY_SEC_INSUFFICIENT,
    LATENCY_SEC_PRIMARY,
    LATENCY_UNRESOLVED,
    MANIFEST_SHA,
    SOURCE_X33B_RUN,
    VERDICT_EXEC,
    VERDICT_MIXED,
    VERDICT_PRICE,
)
from .aggregate import (
    day_decomposition,
    density_audit,
    dist_stats,
    episode_mean,
    market_state_fields,
    spread_summary,
    ss_key,
    weighting_table,
    balanced_mean,
)
from .latency import loss_share, run_latency_scenarios, waterfall_bps
from .publish import publish
from .quotes import board_resolution_audit, evaluate_episode

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x33c_baseline_economics"
X33B_OUT = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x33c_baseline_economics.py"
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


def _verify_manifest_sha() -> dict[str, Any]:
    p = X33B_OUT / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json"
    body = json.loads(p.read_text(encoding="utf-8"))
    sha = body.get("sha256")
    # recompute excluding sha field (freeze convention)
    raw = {k: v for k, v in body.items() if k != "sha256"}
    recomputed = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "path": str(p),
        "sha256": sha,
        "recomputed": recomputed,
        "match_expected": sha == MANIFEST_SHA,
        "recompute_ok": recomputed == sha,
    }


def _evaluate_baseline(planned, boards) -> list[dict[str, Any]]:
    rows = []
    for i, a in enumerate(planned):
        board = boards.get((a["date"], a["symbol"]))
        if board is None or board["t"].size == 0:
            continue
        ep = evaluate_episode(
            board,
            date=a["date"],
            session=a["session"],
            signal_t=float(a["grid_epoch"]),
            entry_delay=0.0,
            exit_delay=0.0,
        )
        if not ep.get("ok"):
            continue
        rec = {
            "date": a["date"],
            "symbol": a["symbol"],
            "session": a["session"],
            "signal_t": float(a["grid_epoch"]),
            **{k: v for k, v in ep.items() if k != "ok"},
            "ok": True,
        }
        rows.append(rec)
        if (i + 1) % 2000 == 0:
            print(f"  baseline {i+1}/{len(planned)} -> {len(rows)} ok", flush=True)
    return rows


def _verdict(mid600: float | None, drag600: float | None, lat1: float | None) -> tuple[str, str]:
    mid = mid600 if mid600 is not None else 0.0
    drag = drag600 if drag600 is not None else 0.0
    # Case A: mid near >=0 but exec drag strongly negative
    # Case B: mid clearly negative
    # Case C: both
    mid_neg = mid < -1.0
    mid_near_nonneg = mid >= -1.0
    drag_dom = drag < -3.0
    if mid_near_nonneg and drag_dom:
        v = VERDICT_EXEC
    elif mid_neg and not drag_dom:
        v = VERDICT_PRICE
    elif mid_neg and drag_dom:
        v = VERDICT_MIXED
    elif mid_neg:
        v = VERDICT_MIXED if abs(drag) > 1.0 else VERDICT_PRICE
    else:
        v = VERDICT_EXEC if drag_dom else VERDICT_MIXED

    if lat1 is None:
        tag = LATENCY_UNRESOLVED
    elif abs(lat1) >= LATENCY_MATERIAL_BPS:
        tag = LATENCY_MATERIAL
    else:
        tag = LATENCY_MINOR
    return v, tag


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x33c_econ_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok"), mapping
    assert mapping.get("mapping_sha") == BOARD_MAPPING_SHA

    man = _verify_manifest_sha()
    assert man["match_expected"], man
    assert man["recompute_ok"], man
    print(f"  manifest SHA OK {MANIFEST_SHA[:16]}...", flush=True)

    print("=== population / planned anchors ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"], ab_pop
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    print(f"  planned={len(planned)} days={len(HISTORICAL_DAYS)}", flush=True)

    pairs = sorted({(a["date"], a["symbol"]) for a in planned})
    assert all(d < FORBIDDEN_FROM for d, _ in pairs)
    boards = load_boards_for_symbols(pairs)
    resolution = board_resolution_audit(boards)
    print(f"  board resolution: med_dt={resolution.get('median_dt_sec')} insuff={resolution.get('insufficient_delays_sec')}", flush=True)

    # X33B identity: evaluate_neutral exec returns
    print("=== X33B exec reproduce ===", flush=True)
    neu_a = evaluate_neutral(planned, boards)
    neu_b = evaluate_neutral(planned, boards)
    from research.e1_x33b_neutral_anchor.analyze import summarize_arm
    sum_a = summarize_arm(neu_a)
    sum_b = summarize_arm(neu_b)
    ab_neu = {
        "episodes_match": sum_a["episodes"] == sum_b["episodes"],
        "ret300_match": sum_a.get("ret300_episode") == sum_b.get("ret300_episode"),
        "ret600_match": sum_a.get("ret600_episode") == sum_b.get("ret600_episode"),
        "ret300": sum_a.get("ret300_episode"),
        "ret600": sum_a.get("ret600_episode"),
        "ret300_balanced": sum_a.get("ret300_balanced"),
        "ret600_balanced": sum_a.get("ret600_balanced"),
        "match_x33b_300": abs(float(sum_a["ret300_episode"]) - EXPECTED_EXEC_300) < 1e-9,
        "match_x33b_600": abs(float(sum_a["ret600_episode"]) - EXPECTED_EXEC_600) < 1e-9,
        "episodes": sum_a["episodes"],
    }
    print(f"  X33B reproduce episodes={ab_neu['episodes']} exec300={ab_neu['ret300']} exec600={ab_neu['ret600']}", flush=True)
    assert ab_neu["match_x33b_300"] and ab_neu["match_x33b_600"], ab_neu
    assert ab_neu["episodes"] == EXPECTED_EPISODES

    print("=== baseline economics (mid/spread/drag) ===", flush=True)
    base = _evaluate_baseline(planned, boards)
    # Align exec with X33B: our exec_* should match neu returns on same keys
    neu_map = {
        (e["date"], e["symbol"], e["session"], float(e["signal_t"])): e
        for e in neu_a
    }
    match_n = 0
    mismatch = 0
    for r in base:
        key = (r["date"], r["symbol"], r["session"], float(r["signal_t"]))
        n = neu_map.get(key)
        if n is None:
            continue
        if r.get("exec_valid_300") and n.get("return_300_valid"):
            if abs(float(r["exec_300"]) - float(n["return_300"])) < 1e-6:
                match_n += 1
            else:
                mismatch += 1
    exec_identity = {
        "matched_episodes_300": match_n,
        "mismatch_300": mismatch,
        "ok": mismatch == 0 and match_n > 3000,
    }
    print(f"  exec identity vs X33B: {exec_identity}", flush=True)

    market_state_fields(base)
    spreads = spread_summary(base)

    metric_keys = []
    for H in (60, 180, 300, 600, 900):
        metric_keys += [f"mid_{H}", f"exec_{H}", f"drag_{H}", f"spread_only_drag_{H}", f"residual_drag_{H}"]
    weights = weighting_table(base, metric_keys)

    base_summary = {
        "n": len(base),
        "entry_half_spread_mean": (spreads["entry_half_spread_bps"] or {}).get("mean"),
        "entry_spread_mean": (spreads["entry_spread_bps"] or {}).get("mean"),
    }
    for H in (300, 600):
        base_summary[f"mid_{H}_episode"] = episode_mean(base, f"mid_{H}")
        base_summary[f"exec_{H}_episode"] = episode_mean(base, f"exec_{H}")
        base_summary[f"drag_{H}_episode"] = episode_mean(base, f"drag_{H}")
        base_summary[f"spread_only_drag_{H}_episode"] = episode_mean(base, f"spread_only_drag_{H}")
        base_summary[f"residual_drag_{H}_episode"] = episode_mean(base, f"residual_drag_{H}")
        base_summary[f"exit_half_spread_{H}_mean"] = (
            spreads["exit_half_spread_bps"].get(str(H)) or {}
        ).get("mean")
        base_summary[f"mid_{H}_ss"] = balanced_mean(base, f"mid_{H}", ss_key)
        base_summary[f"exec_{H}_ss"] = balanced_mean(base, f"exec_{H}", ss_key)
        base_summary[f"drag_{H}_ss"] = balanced_mean(base, f"drag_{H}", ss_key)

    dens = density_audit(base)

    # Latency: primary + marginal 0.5 if not insufficient
    delays = list(LATENCY_SEC_PRIMARY)
    insuff = set(float(x) for x in (resolution.get("insufficient_delays_sec") or []))
    if 0.5 not in insuff:
        delays = sorted(set(delays + [0.5]))
    delays = [d for d in delays if d > 0]
    print(f"=== latency scenarios {delays} (skip insuff {LATENCY_SEC_INSUFFICIENT}) ===", flush=True)
    latency = run_latency_scenarios(planned, boards, base, delays)
    latency["resolution"] = resolution
    latency["insufficient_reported"] = list(LATENCY_SEC_INSUFFICIENT)
    latency["zero_identity"] = {
        "note": "delay=0 is baseline; drag(0)=0 by construction",
        "ok": True,
    }

    # day-level with latency 1s
    lat1_days = ((latency.get("by_delay") or {}).get("1.0") or {}).get("entry_drag600_by_day") or {}
    days = day_decomposition(
        base,
        latency_by_day={d: {"entry_latency_drag_600_1s": v} for d, v in lat1_days.items()},
    )

    wf = waterfall_bps(base_summary, latency)
    shares = loss_share(base_summary, latency)

    lat1_600 = (((latency.get("by_delay") or {}).get("1.0") or {}).get("entry_latency_drag_600") or {}).get("mean")
    lat2_600 = (((latency.get("by_delay") or {}).get("2.0") or {}).get("entry_latency_drag_600") or {}).get("mean")
    lat5_600 = (((latency.get("by_delay") or {}).get("5.0") or {}).get("entry_latency_drag_600") or {}).get("mean")
    lat1_300 = (((latency.get("by_delay") or {}).get("1.0") or {}).get("entry_latency_drag_300") or {}).get("mean")

    verdict, lat_tag = _verdict(
        base_summary.get("mid_600_episode"),
        base_summary.get("drag_600_episode"),
        lat1_600,
    )

    # Answers Q1-Q5
    mid600 = base_summary.get("mid_600_episode")
    drag600 = base_summary.get("drag_600_episode")
    exec600 = base_summary.get("exec_600_episode")
    if mid600 is not None and drag600 is not None:
        if abs(drag600) >= abs(mid600) and mid600 >= -1.0:
            q1 = (
                f"Execution cost dominant: mid600={mid600:.4f} bps near/non-neg, "
                f"drag600={drag600:.4f} bps explains most of exec600={exec600:.4f}."
            )
        elif mid600 < -1.0 and abs(drag600) >= abs(mid600):
            q1 = (
                f"Mixed but execution drag larger in magnitude: mid600={mid600:.4f}, "
                f"drag600={drag600:.4f}, exec600={exec600:.4f}."
            )
        elif mid600 < -1.0:
            q1 = (
                f"Price direction primary: mid600={mid600:.4f} bps negative; "
                f"drag600={drag600:.4f}; exec600={exec600:.4f}."
            )
        else:
            q1 = (
                f"mid600={mid600:.4f}, drag600={drag600:.4f}, exec600={exec600:.4f} "
                f"— see waterfall."
            )
    else:
        q1 = "UNRESOLVED"

    gap = dens.get("gap_episode_minus_balanced_600")
    corr = dens.get("corr_count_vs_ret600")
    q2 = (
        f"Episode mean exec600={exec600:.4f} vs SS-balanced={base_summary.get('exec_600_ss'):.4f} "
        f"(gap episode−balanced={gap}). corr(episode_count, ret600)={corr}. "
        + (
            "High-density symbol-sessions are relatively less-negative, lifting episode mean vs equal SS weight."
            if gap is not None and gap > 0 and (corr or 0) > 0
            else "Weighting shifts mass toward weaker symbol-sessions under equal SS weight."
        )
    )

    gain1 = None if lat1_600 is None else float(-lat1_600)
    gain2 = None if lat2_600 is None else float(-lat2_600)
    gain5 = None if lat5_600 is None else float(-lat5_600)
    q3 = f"Removing 1s entry latency: estimated gain ≈ {gain1} bps on 600s EXEC (and {-lat1_300 if lat1_300 is not None else None} on 300s)."
    q4 = f"2s ≈ {gain2} bps; 5s ≈ {gain5} bps (600s entry-latency removal)."
    loss_abs = abs(exec600) if exec600 is not None and exec600 < 0 else None
    pct1 = None if loss_abs is None or gain1 is None or loss_abs < 1e-12 else 100.0 * abs(gain1) / loss_abs
    q5 = (
        f"Code/runtime latency optimization reclaimable vs |exec600| loss: "
        f"~{pct1:.2f}% at 1s (latency_tag={lat_tag})."
        if pct1 is not None else "UNRESOLVED"
    )

    x34 = (
        "X34 should treat expected move vs execution cost explicitly; "
        "do not invent spread/vol/TOD gates from this diagnostic alone — nest in CV. "
        + (
            "Absolute-rise selection remains primary if price direction adverse."
            if verdict in (VERDICT_PRICE, VERDICT_MIXED) else
            "Cost-aware edge sizing / selection more than raw mid-up filters."
        )
    )

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "latency_tag": lat_tag,
        "anchor_id": ANCHOR_ID,
        "manifest_sha": MANIFEST_SHA,
        "manifest_verify": man,
        "source_x33b_run": SOURCE_X33B_RUN,
        "x33b_exec_reproduce": ab_neu,
        "exec_identity_vs_x33b": exec_identity,
        "board_resolution": resolution,
        "episode_mean": {
            "mid300": base_summary.get("mid_300_episode"),
            "mid600": base_summary.get("mid_600_episode"),
            "exec300": base_summary.get("exec_300_episode"),
            "exec600": base_summary.get("exec_600_episode"),
        },
        "symbol_session_balanced": {
            "mid300": base_summary.get("mid_300_ss"),
            "mid600": base_summary.get("mid_600_ss"),
            "exec300": base_summary.get("exec_300_ss"),
            "exec600": base_summary.get("exec_600_ss"),
        },
        "entry_spread_bps": spreads["entry_spread_bps"],
        "entry_half_spread_bps": spreads["entry_half_spread_bps"],
        "exit_half_spread_bps": spreads["exit_half_spread_bps"],
        "execution_drag": {
            "300": base_summary.get("drag_300_episode"),
            "600": base_summary.get("drag_600_episode"),
            "300_ss": base_summary.get("drag_300_ss"),
            "600_ss": base_summary.get("drag_600_ss"),
        },
        "spread_only_drag": {
            "300": base_summary.get("spread_only_drag_300_episode"),
            "600": base_summary.get("spread_only_drag_600_episode"),
        },
        "residual_execution_drag": {
            "300": base_summary.get("residual_drag_300_episode"),
            "600": base_summary.get("residual_drag_600_episode"),
        },
        "weighting": weights,
        "density_audit": {k: v for k, v in dens.items() if k != "sample_rows"},
        "day_level": days,
        "latency": latency,
        "waterfall": wf,
        "loss_share": shares,
        "estimated_gain_remove_latency_600": {
            "1s": gain1, "2s": gain2, "5s": gain5,
        },
        "answers": {"Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4, "Q5": q5},
        "recommended_x34_implications": x34,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "no_strategy_search": True,
        "no_runtime_change": True,
        "no_entry_change": True,
        "no_exit_strategy": True,
        "no_short": True,
        "no_interpolation": True,
        "mapping_vs_latency_separation": {
            "mapping_tolerance_sec": 5.0,
            "note": "5s window is quote availability contract, not processing latency",
        },
        "safety": {
            "research_paper_only": True,
            "submit_cancel_live": "0/0/0",
            "discord_production": False,
        },
        "population_n": len(rows_pop),
        "baseline_episodes": len(base),
        "ab_determinism": {
            "neutral_a_b": ab_neu["episodes_match"] and ab_neu["ret300_match"] and ab_neu["ret600_match"],
            "population": ab_pop,
        },
    }

    # interim for tests before pytest
    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "latency_tag": lat_tag,
        "manifest_sha": MANIFEST_SHA,
        "opened_20260810": False,
        "no_strategy_search": True,
        "no_runtime_change": True,
        "no_entry_change": True,
        "no_exit_strategy": True,
        "no_short": True,
        "no_interpolation": True,
        "submit_cancel_live": "0/0/0",
        "x33b_exec_reproduce": ab_neu,
        "exec_identity_vs_x33b": exec_identity,
        "episode_mean": report["episode_mean"],
        "symbol_session_balanced": report["symbol_session_balanced"],
        "entry_spread_bps": report["entry_spread_bps"],
        "entry_half_spread_bps": report["entry_half_spread_bps"],
        "exit_half_spread_bps": report["exit_half_spread_bps"],
        "execution_drag": report["execution_drag"],
        "weighting": weights,
        "day_level": days,
        "latency": latency,
        "latency_primary_delays": delays,
        "insufficient_delays": list(LATENCY_SEC_INSUFFICIENT),
        "answers": report["answers"],
        "waterfall": wf,
        "board_resolution": resolution,
        "ab_determinism": report["ab_determinism"],
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id, "verdict": verdict, "latency_tag": lat_tag,
            "mid600": base_summary.get("mid_600_episode"),
            "exec600": exec600, "drag600": drag600,
            "gain_1s": gain1, "gain_2s": gain2, "gain_5s": gain5,
        }],
        "day_level": days,
        "density_top": dens.get("sample_rows") or [],
        "spread_stats": [
            {"metric": "entry_spread", **spreads["entry_spread_bps"]},
            {"metric": "entry_half", **spreads["entry_half_spread_bps"]},
        ],
        "latency_1s": [((latency.get("by_delay") or {}).get("1.0") or {})],
        "waterfall_600": (wf.get("600") or {}).get("steps_bps") or [],
        "weighting_600": [
            {"metric": k, **(weights.get(k) or {})}
            for k in ("mid_600", "exec_600", "drag_600")
        ],
    }
    # publish before tests so fixtures see full report
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    publish(OUT, report, sheets)
    print(f"=== DONE verdict={verdict} latency_tag={lat_tag} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "latency_tag": lat_tag,
        "episode": report["episode_mean"],
        "ss_bal": report["symbol_session_balanced"],
        "gains": report["estimated_gain_remove_latency_600"],
        "Q1": q1[:120],
    }, indent=2, default=str))
    return report


if __name__ == "__main__":
    main()
