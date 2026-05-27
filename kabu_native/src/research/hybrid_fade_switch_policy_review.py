"""
Phase 139: Hybrid-timeline fade switch policy what-if (block / cooldown / priority).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_switch_policy_review import (
    COOLDOWN_MIN_TICKS,
    COOLDOWN_REACCEL_PNL_EPS,
    PNL_EPS,
    _fade_pairs,
    _pnl_current,
    _pnl_keep_old,
    _priority_allow,
)
from research.hybrid_live_replay import HYBRID_MODE_ID, build_hybrid_session
from research.mfe_mae_exit_review import as_float, parse_ts
from research.range_hold_exit_review import _breakdown_on_tick
from research.fade_watch_shadow import _pnl
from research.replay_fidelity_review import _norm_session_id


def load_candidate_events(sdir: Path) -> list[dict[str, Any]]:
    jsonl = sdir / "small_paper_events.jsonl"
    csvp = sdir / "small_paper_events.csv"
    out: list[dict[str, Any]] = []
    if jsonl.is_file():
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                if str(e.get("event_type") or "") != "candidate":
                    continue
                out.append(e)
    elif csvp.is_file():
        with csvp.open(encoding="utf-8", newline="") as f:
            for e in csv.DictReader(f):
                if str(e.get("event_type") or "") != "candidate":
                    continue
                out.append(e)
    out.sort(key=lambda r: parse_ts(str(r.get("event_time") or r.get("entry_time") or "")))
    return out


def enrich_new_features_from_candidates(
    pair: Mapping[str, Any],
    candidate_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    new_sym = str(pair.get("new_symbol") or "")
    new_ts = parse_ts(str(pair.get("new_entry_time") or ""))
    snap: Optional[dict[str, Any]] = None
    best_d = 1e18
    window = 120
    snap_window = 15
    cands: list[tuple[float, str, float]] = []
    for e in candidate_events:
        ts = parse_ts(str(e.get("event_time") or e.get("entry_time") or ""))
        d = abs(ts - new_ts)
        if d > window:
            continue
        sym = str(e.get("symbol") or "")
        q = as_float(e.get("continuation_quality_score"))
        if q is not None and sym:
            cands.append((ts, sym, q))
        if sym == new_sym and d <= snap_window and d < best_d:
            best_d = d
            snap = dict(e)
    out: dict[str, Any] = {
        "new_favorable": None,
        "new_momentum": None,
        "new_vol_liq": None,
        "new_candidate_rank": None,
        "new_entry_gap_proxy": float(pair.get("switch_gap_sec") or 0),
    }
    if snap:
        out["new_favorable"] = as_float(snap.get("favorable_continuation"))
        out["new_momentum"] = as_float(snap.get("momentum_continuation_score"))
        out["new_vol_liq"] = as_float(snap.get("daytrade_suitability_score"))
        qc_raw = snap.get("quality_components_json") or ""
        if qc_raw:
            try:
                qc = json.loads(qc_raw)
                out["new_favorable"] = out["new_favorable"] or as_float(
                    qc.get("favorable_continuation")
                )
            except json.JSONDecodeError:
                pass
    latest: dict[str, tuple[float, float]] = {}
    for ts, sym, q in cands:
        if sym not in latest or ts >= latest[sym][0]:
            latest[sym] = (ts, q)
    ranked = sorted(latest.items(), key=lambda x: x[1][1], reverse=True)
    for i, (sym, _) in enumerate(ranked, start=1):
        if sym == new_sym:
            out["new_candidate_rank"] = i
            break
    if out["new_candidate_rank"] is None and new_sym in latest:
        out["new_candidate_rank"] = len(ranked) + 1
    return out


SCENARIO_A = "A_current"
SCENARIO_B = "B_fade_switch_block"
SCENARIO_C = "C_fade_switch_cooldown"
SCENARIO_D = "D_fade_switch_priority"

SCENARIO_KEYS = (SCENARIO_A, SCENARIO_B, SCENARIO_C, SCENARIO_D)


def _cooldown_allow_phase139(
    pair: Mapping[str, Any],
    old_timeline: Sequence[tuple[float, float]],
) -> tuple[bool, str, int]:
    """
    Allow switch after old-symbol post-fade state confirms:
    breakdown, reacceleration, or no_post_fade_ticks (event-based, not fixed time).
    """
    old_close_ts = parse_ts(str(pair.get("old_close_time") or ""))
    new_entry_ts = parse_ts(str(pair.get("new_entry_time") or ""))
    old_entry_px = as_float(pair.get("old_entry_price")) or 0.0
    fade_price = as_float(pair.get("old_close_price")) or old_entry_px
    if old_entry_px <= 0 or old_close_ts <= 0:
        return False, "insufficient_data", 0

    exit_pnl = as_float(pair.get("old_pnl_at_exit")) or 0.0
    peak_pnl = exit_pnl
    post_low = fade_price
    peak_price = fade_price
    ticks = 0
    fade_momentum: Optional[float] = None

    for ts, px in old_timeline:
        if ts <= old_close_ts:
            continue
        if ts > new_entry_ts:
            break
        ticks += 1
        pnl = _pnl(old_entry_px, px)
        prev_peak_px = peak_price
        if px > peak_price:
            peak_price = px
        if px < post_low:
            post_low = px
        if pnl > peak_pnl + 1e-9:
            peak_pnl = pnl
        new_high = px > prev_peak_px + 1e-9 and px > fade_price
        breakdown = _breakdown_on_tick(
            px=px,
            pnl=pnl,
            mom=None,
            fade_momentum=fade_momentum,
            fade_price=fade_price,
            recent_low=post_low,
            peak_pnl=peak_pnl,
            post_low=post_low,
            prev_post_low=post_low,
            new_high_since_fade=new_high,
        )
        reaccel = pnl >= exit_pnl + COOLDOWN_REACCEL_PNL_EPS and ticks >= COOLDOWN_MIN_TICKS
        if breakdown:
            return True, "old_breakdown_confirmed", ticks
        if reaccel:
            return True, "old_reacceleration_confirmed", ticks

    if ticks == 0:
        return True, "old_no_post_fade_ticks", 0
    return False, "cooldown_active", ticks


def _evaluate_pair_hybrid(
    pair: Mapping[str, Any],
    *,
    old_timeline: Sequence[tuple[float, float]],
    priority_rule: str = "quality_gap_and_rank",
) -> dict[str, Any]:
    cur = _pnl_current(pair)
    keep = _pnl_keep_old(pair)
    new_se = float(pair.get("new_pnl_after_switch") or 0)

    cooldown_allow, cooldown_reason, cooldown_ticks = _cooldown_allow_phase139(
        pair, old_timeline
    )
    priority_allow = _priority_allow(pair, priority_rule)

    scenarios = {
        SCENARIO_A: cur,
        SCENARIO_B: keep,
        SCENARIO_C: cur if cooldown_allow else keep,
        SCENARIO_D: cur if priority_allow else keep,
    }

    row = {
        **pair,
        "replay_mode": HYBRID_MODE_ID,
        "current_pnl_proxy": cur,
        "keep_old_pnl_proxy": keep,
        "delta_keep_vs_current": round(keep - cur, 4),
        "priority_allow_switch": priority_allow,
        "cooldown_allow_switch": cooldown_allow,
        "cooldown_release_reason": cooldown_reason,
        "cooldown_ticks_before_new": cooldown_ticks,
        **{f"pnl_{k}": v for k, v in scenarios.items()},
    }
    truth = str(pair.get("switch_classification") or "")
    row["both_bad_avoided"] = (
        truth == "switch_wrong"
        and cur < -PNL_EPS
        and new_se < -PNL_EPS
        and keep > cur + PNL_EPS
    )
    return row


def _scenario_aggregate(rows: Sequence[Mapping[str, Any]], scenario_key: str) -> dict[str, Any]:
    pnl_key = f"pnl_{scenario_key}"
    pnls = [float(r[pnl_key]) for r in rows]
    deltas = [float(r[pnl_key]) - float(r["current_pnl_proxy"]) for r in rows]

    blocked = sum(
        1 for r in rows if float(r[pnl_key]) == float(r["keep_old_pnl_proxy"])
    )
    wrong_avoided = missed_good = 0
    for r, d in zip(rows, deltas):
        if float(r[pnl_key]) != float(r["keep_old_pnl_proxy"]):
            continue
        truth = str(r.get("switch_classification") or "")
        if truth == "switch_wrong" and d > PNL_EPS:
            wrong_avoided += 1
        if truth == "switch_correct" and d < -PNL_EPS:
            missed_good += 1

    truths = [str(r.get("switch_classification") or "") for r in rows]
    return {
        "scenario_id": scenario_key,
        "fade_switch_count": len(rows),
        "total_pnl_proxy": round(sum(pnls), 4),
        "avg_pnl_proxy": round(statistics.mean(pnls), 4) if pnls else None,
        "delta_total_vs_A_current": round(sum(deltas), 4),
        "avg_delta_new_minus_old": round(statistics.mean(deltas), 4) if deltas else None,
        "old_kept_count": blocked,
        "new_accepted_count": len(rows) - blocked,
        "switch_block_count": blocked,
        "avoided_bad_new": wrong_avoided,
        "missed_good_new": missed_good,
        "wrong_avoided_count": wrong_avoided,
        "correct_rate_vs_truth": round(
            sum(1 for t in truths if t == "switch_correct") / len(truths), 4
        )
        if truths
        else None,
        "wrong_rate_vs_truth": round(
            sum(1 for t in truths if t == "switch_wrong") / len(truths), 4
        )
        if truths
        else None,
        "both_bad_avoided_count": sum(1 for r in rows if r.get("both_bad_avoided")),
    }


def _by_exit_reason(rows: Sequence[Mapping[str, Any]], scenario_key: str) -> list[dict[str, Any]]:
    pnl_key = f"pnl_{scenario_key}"
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r.get("old_exit_reason") or "")].append(r)
    out: list[dict[str, Any]] = []
    for reason, rs in sorted(by.items()):
        pnls = [float(r[pnl_key]) for r in rs]
        deltas = [float(r[pnl_key]) - float(r["current_pnl_proxy"]) for r in rs]
        out.append(
            {
                "scenario_id": scenario_key,
                "old_exit_reason": reason,
                "count": len(rs),
                "total_pnl_proxy": round(sum(pnls), 4),
                "delta_total_vs_A": round(sum(deltas), 4),
                "wrong_rate": round(
                    sum(1 for r in rs if r.get("switch_classification") == "switch_wrong")
                    / len(rs),
                    4,
                ),
            }
        )
    return out


def _by_session(rows: Sequence[Mapping[str, Any]], scenario_key: str) -> list[dict[str, Any]]:
    pnl_key = f"pnl_{scenario_key}"
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by[_norm_session_id(str(r.get("session_id") or ""))].append(r)
    out: list[dict[str, Any]] = []
    for sid, rs in sorted(by.items()):
        pnls = [float(r[pnl_key]) for r in rs]
        deltas = [float(r[pnl_key]) - float(r["current_pnl_proxy"]) for r in rs]
        out.append(
            {
                "scenario_id": scenario_key,
                "session_id": sid,
                "fade_switch_count": len(rs),
                "total_pnl_proxy": round(sum(pnls), 4),
                "delta_total_vs_A": round(sum(deltas), 4),
            }
        )
    return out


def determine_verdict(scenarios: Sequence[Mapping[str, Any]]) -> tuple[str, list[str]]:
    by_id = {s["scenario_id"]: s for s in scenarios}
    a = by_id.get(SCENARIO_A) or {}
    b = by_id.get(SCENARIO_B) or {}
    c = by_id.get(SCENARIO_C) or {}
    d = by_id.get(SCENARIO_D) or {}

    notes: list[str] = []
    b_delta = float(b.get("delta_total_vs_A_current") or 0)
    c_delta = float(c.get("delta_total_vs_A_current") or 0)
    d_delta = float(d.get("delta_total_vs_A_current") or 0)
    notes.append(f"B_delta={b_delta:.2f} C_delta={c_delta:.2f} D_delta={d_delta:.2f}")

    best_delta = max(b_delta, c_delta, d_delta)
    if best_delta <= 1.0:
        return "current_switch_best", notes + ["no scenario beats A by meaningful margin"]

    if b_delta >= max(c_delta, d_delta) and b_delta > 5:
        return "fade_switch_policy_promising", notes + ["block best under hybrid timeline"]

    if c_delta > b_delta and c_delta > 3:
        return "fade_switch_policy_promising", notes + ["cooldown best"]

    if d_delta > 3 and int(d.get("new_accepted_count") or 0) > 5:
        return "fade_switch_policy_promising", notes + ["priority retains selective switches"]

    if c_delta <= 1 and b_delta <= 1:
        return "cooldown_not_helpful", notes

    if d_delta <= b_delta:
        return "need_priority_model", notes + ["priority rules need refinement"]

    return "fade_switch_policy_promising", notes


def analyze_hybrid_fade_switch_policies(
    session_dirs: Sequence[Path],
    *,
    priority_rule: str = "quality_gap_and_rank",
    phase134_pairs_path: Optional[Path] = None,
) -> dict[str, Any]:
    from research.mfe_mae_exit_review import build_price_timeline_from_events_csv

    session_dirs = [Path(s) for s in session_dirs]
    pairs = _fade_pairs(session_dirs)

    session_by_id: dict[str, Path] = {}
    for sdir in session_dirs:
        sid = _norm_session_id(
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        session_by_id[sid] = sdir

    pairs_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        sid = _norm_session_id(str(p.get("session_id") or ""))
        pairs_by_session[sid].append(dict(p))

    enriched: list[dict[str, Any]] = []
    for sid, ps in pairs_by_session.items():
        sdir = session_by_id.get(sid)
        if not sdir:
            continue
        build_hybrid_session(sdir)
        candidate_events = load_candidate_events(sdir)
        old_symbols = {str(p.get("old_symbol") or "") for p in ps if str(p.get("old_symbol") or "")}
        old_tl_map = build_price_timeline_from_events_csv(
            sdir / "small_paper_events.csv", old_symbols
        )
        for p in ps:
            extra = enrich_new_features_from_candidates(p, candidate_events)
            merged = {**p, **extra}
            old_sym = str(merged.get("old_symbol") or "")
            enriched.append(
                _evaluate_pair_hybrid(
                    merged,
                    old_timeline=old_tl_map.get(old_sym, []),
                    priority_rule=priority_rule,
                )
            )

    scenarios = [_scenario_aggregate(enriched, k) for k in SCENARIO_KEYS]
    summary_rows: list[dict[str, Any]] = []
    for sc in scenarios:
        summary_rows.extend(_by_exit_reason(enriched, sc["scenario_id"]))
        summary_rows.extend(_by_session(enriched, sc["scenario_id"]))

    verdict, notes = determine_verdict(scenarios)

    phase134_ref: dict[str, Any] = {}
    if phase134_pairs_path and phase134_pairs_path.is_file():
        with phase134_pairs_path.open(encoding="utf-8", newline="") as f:
            p134 = list(csv.DictReader(f))
        phase134_ref = {
            "phase134_pair_count": len(p134),
            "hybrid_pair_count": len(enriched),
            "pair_count_match": len(p134) == len(enriched),
        }

    cooldown_reasons = Counter(
        str(r.get("cooldown_release_reason") or "") for r in enriched if r.get("cooldown_allow_switch")
    )

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "replay_mode": HYBRID_MODE_ID,
        "fade_switch_count": len(enriched),
        "scenarios": scenarios,
        "pairs": enriched,
        "summary_rows": summary_rows,
        "phase134_reference": phase134_ref,
        "cooldown_release_reason_counts": dict(cooldown_reasons),
        "session_count": len(pairs_by_session),
    }
