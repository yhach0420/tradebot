"""
Phase 135: Fade-switch cooldown shadow replay A/B vs combined_structural_exit_v1.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_switch_cooldown_shadow import (
    FADE_SWITCH_TRIGGER_REASONS,
    POLICY_FADE_SWITCH_COOLDOWN_SHADOW,
)
from research.fade_switch_policy_review import FADE_EXIT_REASONS
from research.mfe_mae_exit_review import as_float, parse_ts
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from research.structural_observer_review import (
    StructuralTrade,
    _load_events,
    _session_end_time,
    _summarize_structural_trades,
    replay_combined_structural_exit,
)
from research.switch_old_vs_new_review import MAX_PAIR_SEC, PNL_EPS

IMPROVE_EPS = 0.001


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return round(sum(wins) / gl, 4)


def _metrics(trades: Sequence[StructuralTrade]) -> dict[str, Any]:
    m = _summarize_structural_trades(trades)
    pnls = [float(t.realized_pnl_pct) for t in trades]
    return {
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl": m.get("structural_avg_pnl"),
        "pf": m.get("structural_pf"),
        "win_rate": m.get("structural_win_rate"),
        "trade_count": m.get("structural_trade_count"),
    }


def _trade_to_row(t: StructuralTrade) -> dict[str, Any]:
    return {
        "symbol": t.symbol,
        "entry_time": t.entry_time,
        "close_time": t.close_time,
        "entry_price": t.entry_price,
        "close_price": t.close_price,
        "close_reason": t.close_reason,
        "realized_pnl_pct": t.realized_pnl_pct,
        "mfe_pct": t.mfe_pct,
        "continuation_quality_score": t.entry_quality,
    }


def _count_fade_switches(trades: Sequence[StructuralTrade]) -> int:
    rows = [_trade_to_row(t) for t in trades]
    n = 0
    for old in rows:
        reason = str(old.get("close_reason") or "")
        if reason not in FADE_EXIT_REASONS:
            continue
        old_sym = str(old.get("symbol") or "")
        old_close_ts = parse_ts(str(old.get("close_time") or ""))
        if not old_sym or old_close_ts <= 0:
            continue
        for new in rows:
            if str(new.get("symbol") or "") == old_sym:
                continue
            new_ts = parse_ts(str(new.get("entry_time") or ""))
            if new_ts <= old_close_ts:
                continue
            if new_ts - old_close_ts <= MAX_PAIR_SEC:
                n += 1
                break
    return n


def _switch_block_outcomes(
    log_b: Sequence[Mapping[str, Any]],
    trades_a: Sequence[StructuralTrade],
) -> tuple[int, int, int]:
    """Blocking good if A's skipped entry would have lost; bad if it would have won."""
    a_by_sym_time = {(t.symbol, t.entry_time): t for t in trades_a}
    improved = worsened = unchanged = 0
    for ev in log_b:
        if str(ev.get("event_kind") or "") != "fade_switch_blocked":
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        ta = a_by_sym_time.get((sym, ent))
        if not ta:
            continue
        pa = float(ta.realized_pnl_pct)
        if pa < -IMPROVE_EPS:
            improved += 1
        elif pa > IMPROVE_EPS:
            worsened += 1
        else:
            unchanged += 1
    return improved, worsened, unchanged


def _trade_details(
    trades_a: Sequence[StructuralTrade],
    trades_b: Sequence[StructuralTrade],
    log_b: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    a_keys = {(t.symbol, t.entry_time) for t in trades_a}
    b_keys = {(t.symbol, t.entry_time) for t in trades_b}
    blocked = {
        (str(e.get("symbol") or ""), str(e.get("entry_time") or ""))
        for e in log_b
        if str(e.get("event_kind") or "") == "fade_switch_blocked"
    }
    rows: list[dict[str, Any]] = []
    all_keys = sorted(a_keys | b_keys)
    a_map = {(t.symbol, t.entry_time): t for t in trades_a}
    b_map = {(t.symbol, t.entry_time): t for t in trades_b}
    for key in all_keys:
        ta = a_map.get(key)
        tb = b_map.get(key)
        if not ta and not tb:
            continue
        pa = float(ta.realized_pnl_pct) if ta else None
        pb = float(tb.realized_pnl_pct) if tb else None
        delta = round((pb or 0) - (pa or 0), 4) if pa is not None and pb is not None else None
        rows.append(
            {
                "symbol": key[0],
                "entry_time": key[1],
                "in_A": ta is not None,
                "in_B": tb is not None,
                "fade_switch_blocked": key in blocked,
                "A_close_reason": ta.close_reason if ta else "",
                "B_close_reason": tb.close_reason if tb else "",
                "A_pnl": pa,
                "B_pnl": pb,
                "pnl_delta_B_minus_A": delta,
                "block_improved": key in blocked and pa is not None and (pb is None or (pb or 0) < pa),
            }
        )
    return rows


def determine_verdict(comparison: Mapping[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    delta = float(comparison.get("delta_total_pnl") or 0)
    blocks = int(comparison.get("switch_block_count") or 0)
    block_imp = int(comparison.get("switch_block_improved") or 0)
    block_worse = int(comparison.get("switch_block_worsened") or 0)
    block_decided = block_imp + block_worse
    block_worse_rate = block_worse / block_decided if block_decided else None
    notes.append(
        f"delta={delta:.4f} blocks={blocks} block_imp={block_imp} block_worse={block_worse}"
    )

    phase134_delta = float(comparison.get("phase134_cooldown_delta") or 0)
    if blocks < 50:
        notes.append(
            f"replay blocked {blocks} switches vs Phase134 ~137 kept; "
            "structural replay accepts all events (cap=3 switch path not modeled)"
        )
        return "review_only_gain_not_reproducible", notes

    if delta > 2.0 and (block_worse_rate is None or block_worse_rate <= 0.35):
        return "cooldown_shadow_promising", notes

    if delta > 0.5:
        return "cooldown_shadow_promising", notes + ["marginal replay gain"]

    if delta <= 0.1 or phase134_delta > 50:
        return "review_only_gain_not_reproducible", notes + [
            "Phase134 counterfactual gain not reproduced in event replay"
        ]

    if block_worse_rate is not None and block_worse_rate > 0.45:
        return "current_switch_best", notes + ["blocked switches often worse than allowing"]

    return "review_only_gain_not_reproducible", notes


def analyze_fade_switch_cooldown_shadow(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
) -> dict[str, Any]:
    all_a: list[StructuralTrade] = []
    all_b: list[StructuralTrade] = []
    per_session: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    total_blocks = 0

    for sdir in session_dirs:
        sdir = Path(sdir)
        events = _load_events(sdir)
        if not events:
            continue
        session_id = (
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        interval = float(getattr(pilot_config, "poll_interval_sec", None) or 5.0)
        session_end = _session_end_time(events)

        trades_a, _ = replay_combined_structural_exit(
            events,
            pilot_config=pilot_config,
            poll_interval_sec=interval,
            session_end=session_end,
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        )
        trades_b, log_b = replay_combined_structural_exit(
            events,
            pilot_config=pilot_config,
            poll_interval_sec=interval,
            session_end=session_end,
            structural_exit_policy=POLICY_FADE_SWITCH_COOLDOWN_SHADOW,
        )

        ma = _metrics(trades_a)
        mb = _metrics(trades_b)
        switch_a = _count_fade_switches(trades_a)
        switch_b = _count_fade_switches(trades_b)
        blocks = sum(1 for e in log_b if str(e.get("event_kind") or "") == "fade_switch_blocked")
        cooldown_entered = sum(
            1 for e in log_b if str(e.get("event_kind") or "") == "fade_switch_cooldown_entered"
        )
        block_imp, block_worse, block_unch = _switch_block_outcomes(log_b, trades_a)
        total_blocks += blocks

        all_a.extend(trades_a)
        all_b.extend(trades_b)
        details.extend(_trade_details(trades_a, trades_b, log_b))

        per_session.append(
            {
                "session_id": session_id,
                "A_current": ma,
                "B_fade_switch_cooldown_shadow": mb,
                "switch_count_A": switch_a,
                "switch_count_B": switch_b,
                "switch_block_count": blocks,
                "fade_cooldown_entered_count": cooldown_entered,
                "switch_block_improved": block_imp,
                "switch_block_worsened": block_worse,
                "delta_total_pnl": round(float(mb["total_pnl"]) - float(ma["total_pnl"]), 4),
            }
        )

    ma = _metrics(all_a)
    mb = _metrics(all_b)
    switch_a = _count_fade_switches(all_a)
    switch_b = _count_fade_switches(all_b)
    block_imp = sum(int(s.get("switch_block_improved") or 0) for s in per_session)
    block_worse = sum(int(s.get("switch_block_worsened") or 0) for s in per_session)

    comparison = {
        **ma,
        "B_total_pnl": mb["total_pnl"],
        "B_pf": mb["pf"],
        "B_win_rate": mb["win_rate"],
        "B_trade_count": mb["trade_count"],
        "delta_total_pnl": round(float(mb["total_pnl"]) - float(ma["total_pnl"]), 4),
        "switch_count_A": switch_a,
        "switch_count_B": switch_b,
        "switch_block_count": total_blocks,
        "switch_block_improved": block_imp,
        "switch_block_worsened": block_worse,
        "phase134_cooldown_delta": 93.4824,
        "fade_exit_reasons": sorted(FADE_SWITCH_TRIGGER_REASONS),
        "release_signals": [
            "breakdown_detected",
            "new_high_after_fade",
            "new_mfe_created",
            "momentum_recovery",
            "giveback_exceeded",
        ],
    }

    verdict, notes = determine_verdict(comparison)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "comparison": comparison,
        "sessions": per_session,
        "trade_details": details,
        "session_count": len(per_session),
    }
