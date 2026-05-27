"""
Phase 136: cap=3 entry replay review — validate fade switch under realistic ExposureGate.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.cap3_entry_replay import (
    FADE_SWITCH_SCENARIOS,
    SCENARIO_BLOCK,
    SCENARIO_COOLDOWN,
    SCENARIO_CURRENT,
    Cap3ReplayResult,
    simulate_cap3_entry_replay,
    summarize_scenario,
)
from research.exposure_gate import ExposureGate, ExposureGateConfig
from research.fade_switch_policy_review import FADE_EXIT_REASONS
from research.mfe_mae_exit_review import discover_sessions, parse_ts
from research.small_paper_performance_review import _load_events, _parse_ts
from research.structural_observer_review import _session_end_time
from research.switch_old_vs_new_review import MAX_PAIR_SEC, PNL_EPS

PHASE134_PAIRS_CSV = Path("kabu_native/results/reports/phase134_fade_switch_pairs.csv")
IMPROVE_EPS = 0.001


def _norm_session_id(session_id: str) -> str:
    return str(session_id or "").replace("\\", "/").strip()


def _load_phase134_pairs(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _extract_fade_switches(result: Cap3ReplayResult) -> list[dict[str, Any]]:
    switches: list[dict[str, Any]] = []
    fades = [p for p in result.closed_positions if p.close_reason in FADE_EXIT_REASONS]
    for old in fades:
        for acc in result.accepted:
            if acc["symbol"] == old.symbol:
                continue
            new_ts = parse_ts(str(acc["entry_time"]))
            if old.close_ts <= new_ts <= old.close_ts + MAX_PAIR_SEC:
                switches.append(
                    {
                        "session_id": result.session_id,
                        "old_symbol": old.symbol,
                        "new_symbol": acc["symbol"],
                        "old_exit_reason": old.close_reason,
                        "old_close_time": old.close_time,
                        "new_entry_time": acc["entry_time"],
                        "switch_gap_sec": round(new_ts - old.close_ts, 1),
                    }
                )
                break
    return switches


def _match_phase134_pairs(
    sim_switches: Sequence[Mapping[str, Any]],
    phase134: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> list[dict[str, Any]]:
    sid_norm = _norm_session_id(session_id)
    p134_sess = [
        p for p in phase134 if _norm_session_id(str(p.get("session_id") or "")) == sid_norm
    ]
    diagnostics: list[dict[str, Any]] = []
    matched_sim = set()

    for p in p134_sess:
        old_sym = str(p.get("old_symbol") or "")
        new_sym = str(p.get("new_symbol") or "")
        old_close = str(p.get("old_close_time") or "")
        key = (old_sym, new_sym, old_close)
        hit = None
        p134_close_ts = parse_ts(old_close)
        for s in sim_switches:
            if s.get("old_symbol") != old_sym or s.get("new_symbol") != new_sym:
                continue
            sim_close_ts = parse_ts(str(s.get("old_close_time") or ""))
            if abs(sim_close_ts - p134_close_ts) <= 120:
                hit = s
                matched_sim.add(id(s))
                break
        if hit:
            diagnostics.append(
                {
                    "session_id": session_id,
                    "old_symbol": old_sym,
                    "new_symbol": new_sym,
                    "matched": True,
                    "reason_unmatched": "",
                    "phase134_old_exit_reason": p.get("old_exit_reason"),
                    "sim_old_exit_reason": hit.get("old_exit_reason"),
                    "phase134_gap_sec": p.get("switch_gap_sec"),
                    "sim_gap_sec": hit.get("switch_gap_sec"),
                }
            )
        else:
            diagnostics.append(
                {
                    "session_id": session_id,
                    "old_symbol": old_sym,
                    "new_symbol": new_sym,
                    "matched": False,
                    "reason_unmatched": "not_in_cap3_replay_switches",
                    "phase134_old_exit_reason": p.get("old_exit_reason"),
                    "sim_old_exit_reason": "",
                    "phase134_gap_sec": p.get("switch_gap_sec"),
                    "sim_gap_sec": "",
                }
            )

    for s in sim_switches:
        if id(s) not in matched_sim:
            diagnostics.append(
                {
                    "session_id": session_id,
                    "old_symbol": s.get("old_symbol"),
                    "new_symbol": s.get("new_symbol"),
                    "matched": False,
                    "reason_unmatched": "extra_sim_switch_not_in_phase134",
                    "phase134_old_exit_reason": "",
                    "sim_old_exit_reason": s.get("old_exit_reason"),
                    "phase134_gap_sec": "",
                    "sim_gap_sec": s.get("switch_gap_sec"),
                }
            )
    return diagnostics


def _block_outcomes(
    events: Sequence[Mapping[str, Any]],
    result_a: Cap3ReplayResult,
    result_b: Cap3ReplayResult,
) -> dict[str, Any]:
    """Compare blocked entries (B vs A) using virtual PnL from A accepted paths."""
    a_by_key = {(str(a["symbol"]), str(a["entry_time"])): a for a in result_a.accepted}
    missed_good = avoided_bad = 0
    for ev in result_b.event_log:
        if str(ev.get("event_kind") or "") != "fade_switch_blocked":
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        closed = next(
            (
                p
                for p in result_a.closed_positions
                if p.symbol == sym and p.entry_time == ent
            ),
            None,
        )
        if closed:
            pnl = float(closed.realized_pnl_pct)
            if pnl > IMPROVE_EPS:
                missed_good += 1
            elif pnl < -IMPROVE_EPS:
                avoided_bad += 1
        elif (sym, ent) not in a_by_key:
            pass
    return {"missed_good_new": missed_good, "avoided_bad_new": avoided_bad}


def _old_kept_benefit(
    result_a: Cap3ReplayResult,
    result_b: Cap3ReplayResult,
    *,
    phase134_pairs: Sequence[Mapping[str, Any]],
) -> float:
    """Sum of (old_session_end - new) delta when B blocked vs A took switch."""
    benefit = 0.0
    for ev in result_b.event_log:
        if str(ev.get("event_kind") or "") != "fade_switch_blocked":
            continue
        cooled = str(ev.get("cooldown_symbol") or "")
        new_sym = str(ev.get("symbol") or "")
        pf = next((p for p in result_a.closed_positions if p.symbol == cooled), None)
        new_p = next(
            (
                p
                for p in result_a.closed_positions
                if p.symbol == new_sym and p.entry_time == ev.get("entry_time")
            ),
            None,
        )
        if pf and new_p:
            benefit += float(pf.realized_pnl_pct) - float(new_p.realized_pnl_pct)
    return round(benefit, 4)


def determine_verdict(
    summary_rows: Sequence[Mapping[str, Any]],
    match_stats: Mapping[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    by_sc = {r["scenario"]: r for r in summary_rows}
    a = by_sc.get(SCENARIO_CURRENT) or {}
    b = by_sc.get(SCENARIO_COOLDOWN) or {}
    c = by_sc.get(SCENARIO_BLOCK) or {}

    match_rate = float(match_stats.get("match_rate") or 0)
    notes.append(
        f"phase134_match_rate={match_rate:.1%} "
        f"A_switch={a.get('switch_count')} B_switch={b.get('switch_count')} "
        f"B_block={b.get('switch_block_count')}"
    )

    delta_b = float(b.get("total_pnl_proxy") or 0) - float(a.get("total_pnl_proxy") or 0)
    delta_c = float(c.get("total_pnl_proxy") or 0) - float(a.get("total_pnl_proxy") or 0)
    block_n = int(b.get("switch_block_count") or 0)
    notes.append(f"delta_B={delta_b:.4f} delta_C={delta_c:.4f} blocks={block_n}")

    if match_rate < 0.25:
        notes.append("low phase134 pair match — compare aggregate switch counts")
        if delta_b > 5.0 and block_n >= 50:
            return "fade_switch_cooldown_promising_under_cap3", notes + [
                "cap3 replay shows cooldown benefit despite pair-level mismatch"
            ]
        return "replay_mismatch_with_phase134", notes

    if delta_b > 2.0 and block_n >= 20:
        return "fade_switch_cooldown_promising_under_cap3", notes

    if max(delta_b, delta_c) <= 0.5:
        return "cooldown_not_helpful_under_realistic_gate", notes

    if block_n < 10:
        return "need_live_session_replay_engine_fix", notes

    return "cooldown_not_helpful_under_realistic_gate", notes


def analyze_cap3_entry_replay(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    phase134_pairs_path: Path,
) -> dict[str, Any]:
    from small_paper.discord_notifier import observer_tracker_config_from_pilot

    phase134_all = _load_phase134_pairs(phase134_pairs_path)
    gate_cfg = ExposureGateConfig(
        profile=str(pilot_config.profile),
        min_continuation_quality=float(pilot_config.min_continuation_quality),
        max_concurrent_positions=int(pilot_config.max_concurrent_positions),
        reject_below_quality=bool(pilot_config.reject_below_quality),
        min_above_median_quality=float(getattr(pilot_config, "min_above_median_quality", 0.42)),
    )
    allowed_windows = pilot_config.allowed_windows()
    exit_cfg = observer_tracker_config_from_pilot(pilot_config)
    exit_cfg.structural_exit_policy = "combined_structural_exit_v1"

    all_events: list[dict[str, Any]] = []
    scenario_summaries: list[dict[str, Any]] = []
    match_diagnostics: list[dict[str, Any]] = []
    per_session: list[dict[str, Any]] = []
    results_by_session_scenario: dict[tuple[str, str], Cap3ReplayResult] = {}

    for sdir in session_dirs:
        sdir = Path(sdir)
        events = _load_events(sdir)
        if not events:
            continue
        session_id = (
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        session_end = _session_end_time(events)
        session_end_ts = parse_ts(session_end)

        sess_rows: dict[str, dict[str, Any]] = {}
        for scenario in FADE_SWITCH_SCENARIOS:
            gate = ExposureGate(gate_cfg, allowed_windows=allowed_windows)
            res = simulate_cap3_entry_replay(
                events,
                session_id=session_id,
                scenario=scenario,
                gate=gate,
                exit_cfg=exit_cfg,
                session_end=session_end,
                session_end_ts=session_end_ts,
            )
            results_by_session_scenario[(session_id, scenario)] = res
            summ = summarize_scenario(res)
            sess_rows[scenario] = summ
            scenario_summaries.append(summ)

        res_a = results_by_session_scenario[(session_id, SCENARIO_CURRENT)]
        sim_sw = _extract_fade_switches(res_a)
        match_diagnostics.extend(_match_phase134_pairs(sim_sw, phase134_all, session_id=session_id))

        res_b = results_by_session_scenario[(session_id, SCENARIO_COOLDOWN)]
        block_out = _block_outcomes(events, res_a, res_b)
        kept = _old_kept_benefit(res_a, res_b, phase134_pairs=phase134_all)

        per_session.append(
            {
                "session_id": session_id,
                **{f"{k}_pnl": v.get("total_pnl_proxy") for k, v in sess_rows.items()},
                **{f"{k}_switch": v.get("switch_count") for k, v in sess_rows.items()},
                "B_switch_block_count": sess_rows[SCENARIO_COOLDOWN].get("switch_block_count"),
                "matched_phase134_in_session": sum(
                    1 for d in match_diagnostics if d.get("session_id") == session_id and d.get("matched")
                ),
                **block_out,
                "old_kept_benefit": kept,
            }
        )
        all_events.extend(res_a.event_log)
        all_events.extend(res_b.event_log)

    agg: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(float))
    agg_int: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    release_counts: Counter[str] = Counter()

    for row in scenario_summaries:
        sc = str(row["scenario"])
        agg[sc]["total_pnl_proxy"] += float(row.get("total_pnl_proxy") or 0)
        agg_int[sc]["accepted_count"] += int(row.get("accepted_count") or 0)
        agg_int[sc]["rejected_max_concurrent_count"] += int(
            row.get("rejected_max_concurrent_count") or 0
        )
        agg_int[sc]["switch_count"] += int(row.get("switch_count") or 0)
        agg_int[sc]["switch_block_count"] += int(row.get("switch_block_count") or 0)
        for k, v in (row.get("release_reason_counts") or {}).items():
            release_counts[f"{sc}:{k}"] += int(v)

    summary_rows: list[dict[str, Any]] = []
    a_pnl = float(agg[SCENARIO_CURRENT]["total_pnl_proxy"])
    for sc in FADE_SWITCH_SCENARIOS:
        pnls = []
        for (sid, ssc), res in results_by_session_scenario.items():
            if ssc == sc:
                pnls.extend([p.realized_pnl_pct for p in res.closed_positions])
        summary_rows.append(
            {
                "scenario": sc,
                "total_pnl_proxy": round(float(agg[sc]["total_pnl_proxy"]), 4),
                "delta_vs_A": round(float(agg[sc]["total_pnl_proxy"]) - a_pnl, 4),
                "pf_proxy": _profit_factor_from_sessions(results_by_session_scenario, sc),
                "accepted_count": agg_int[sc]["accepted_count"],
                "rejected_max_concurrent_count": agg_int[sc]["rejected_max_concurrent_count"],
                "switch_count": agg_int[sc]["switch_count"],
                "switch_block_count": agg_int[sc]["switch_block_count"],
                "release_reason_counts": {
                    k.split(":", 1)[1]: v
                    for k, v in release_counts.items()
                    if k.startswith(f"{sc}:")
                },
            }
        )

    matched = sum(1 for d in match_diagnostics if d.get("matched"))
    p134_sess = len(phase134_all)
    match_stats = {
        "phase134_pair_count": p134_sess,
        "matched_switch_count": matched,
        "unmatched_switch_count": p134_sess - matched,
        "match_rate": round(matched / p134_sess, 4) if p134_sess else None,
    }

    block_imp = sum(int(s.get("avoided_bad_new") or 0) for s in per_session)
    block_miss = sum(int(s.get("missed_good_new") or 0) for s in per_session)
    kept_sum = round(sum(float(s.get("old_kept_benefit") or 0) for s in per_session), 4)

    for row in summary_rows:
        if row["scenario"] == SCENARIO_COOLDOWN:
            row["missed_good_new"] = block_miss
            row["avoided_bad_new"] = block_imp
            row["old_kept_benefit"] = kept_sum

    verdict, notes = determine_verdict(summary_rows, match_stats)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "summary_rows": summary_rows,
        "match_stats": match_stats,
        "sessions": per_session,
        "event_log": all_events,
        "match_diagnostics": match_diagnostics,
        "session_count": len(per_session),
    }


def _profit_factor_from_sessions(
    results: Mapping[tuple[str, str], Cap3ReplayResult],
    scenario: str,
) -> Optional[float]:
    pnls = [
        p.realized_pnl_pct
        for (sid, sc), res in results.items()
        if sc == scenario
        for p in res.closed_positions
    ]
    if not pnls:
        return None
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return round(sum(wins) / gl, 4)
