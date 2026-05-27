"""
Phase 141: Fade switch block shadow A/B replay vs combined_structural_exit_v1.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_switch_block_shadow import (
    BLOCK_REASON,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_SWITCH_BLOCK_SHADOW,
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
PHASE139_BLOCK_DELTA = 72.7341


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return round(sum(wins) / gl, 4)


def _metrics(trades: Sequence[StructuralTrade]) -> dict[str, Any]:
    m = _summarize_structural_trades(trades)
    pnls = [float(t.realized_pnl_pct) for t in trades]
    return {
        "total_pnl_proxy": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl_proxy": m.get("structural_avg_pnl"),
        "PF_proxy": m.get("structural_pf"),
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


def _block_events(log_b: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for e in log_b:
        kind = str(e.get("event_kind") or "")
        if kind not in (
            "fade_switch_blocked",
            "fade_switch_block_entered",
            "fade_switch_same_symbol_allowed",
        ):
            continue
        rows.append(
            {
                "event_kind": kind,
                "session_id": e.get("session_id"),
                "blocked_new_symbol": e.get("blocked_new_symbol") or e.get("symbol"),
                "old_symbol": e.get("old_symbol"),
                "old_exit_reason": e.get("old_exit_reason") or e.get("fade_exit_reason"),
                "block_reason": e.get("block_reason") or BLOCK_REASON,
                "same_symbol_allowed": e.get("same_symbol_allowed"),
                "overlap_switch_exempted": e.get("overlap_switch_exempted"),
                "blocked_count": e.get("blocked_count"),
                "entry_time": e.get("entry_time"),
                "fade_exit_time": e.get("fade_exit_time"),
            }
        )
    return rows


def _switch_block_outcomes(
    log_b: Sequence[Mapping[str, Any]],
    trades_a: Sequence[StructuralTrade],
) -> tuple[int, int, int]:
    """avoided_bad / missed_good vs baseline trades on blocked entries."""
    a_by_key = {(t.symbol, t.entry_time): t for t in trades_a}
    avoided_bad = missed_good = neutral = 0
    for ev in log_b:
        if str(ev.get("event_kind") or "") != "fade_switch_blocked":
            continue
        sym = str(ev.get("symbol") or ev.get("blocked_new_symbol") or "")
        ent = str(ev.get("entry_time") or "")
        ta = a_by_key.get((sym, ent))
        if not ta:
            continue
        pa = float(ta.realized_pnl_pct)
        if pa < -IMPROVE_EPS:
            avoided_bad += 1
        elif pa > IMPROVE_EPS:
            missed_good += 1
        else:
            neutral += 1
    return avoided_bad, missed_good, neutral


def _by_exit_reason_delta(
    trades_a: Sequence[StructuralTrade],
    trades_b: Sequence[StructuralTrade],
) -> list[dict[str, Any]]:
    def _sum_by(trades: Sequence[StructuralTrade]) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for t in trades:
            out[str(t.close_reason or "")] += float(t.realized_pnl_pct)
        return out

    sa, sb = _sum_by(trades_a), _sum_by(trades_b)
    reasons = sorted(set(sa) | set(sb))
    rows: list[dict[str, Any]] = []
    for r in reasons:
        pa = round(sa.get(r, 0.0), 4)
        pb = round(sb.get(r, 0.0), 4)
        rows.append(
            {
                "close_reason": r,
                "A_total_pnl": pa,
                "B_total_pnl": pb,
                "delta_B_minus_A": round(pb - pa, 4),
            }
        )
    return rows


def _pair_level_block_proxy(session_dirs: Sequence[Path]) -> dict[str, Any]:
    """Hybrid-timeline fade-switch pair proxy (Phase139 comparable)."""
    from research.fade_switch_policy_review import _fade_pairs, _pnl_current, _pnl_keep_old

    pairs = _fade_pairs([Path(s) for s in session_dirs])
    if not pairs:
        return {"pair_count": 0}
    cur = [_pnl_current(p) for p in pairs]
    keep = [_pnl_keep_old(p) for p in pairs]
    return {
        "pair_count": len(pairs),
        "A_current_total_pnl_proxy": round(sum(cur), 4),
        "B_block_total_pnl_proxy": round(sum(keep), 4),
        "delta_block_vs_current": round(sum(keep) - sum(cur), 4),
        "phase139_delta_reference": PHASE139_BLOCK_DELTA,
        "reproduces_phase139_block": abs((sum(keep) - sum(cur)) - PHASE139_BLOCK_DELTA) < 1.0,
    }


def determine_verdict(
    comparison: Mapping[str, Any],
    pair_proxy: Mapping[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    delta = float(comparison.get("delta_total_pnl_proxy") or 0)
    blocks = int(comparison.get("blocked_switch_count") or 0)
    avoided = int(comparison.get("avoided_bad_new") or 0)
    missed = int(comparison.get("missed_good_new") or 0)
    block_decided = avoided + missed
    missed_rate = missed / block_decided if block_decided else None
    notes.append(
        f"delta={delta:.4f} blocks={blocks} avoided_bad={avoided} missed_good={missed}"
    )
    notes.append(f"phase139_block_delta_ref={PHASE139_BLOCK_DELTA:.4f}")

    pair_delta = float(pair_proxy.get("delta_block_vs_current") or 0)
    notes.append(f"pair_proxy_delta={pair_delta:.4f}")

    if blocks == 0:
        return "runner_support_missing", notes + ["no fade_switch_blocked events logged"]

    if pair_proxy.get("reproduces_phase139_block"):
        if missed_rate is not None and missed_rate > 0.55 and delta < 0:
            return "fade_switch_block_shadow_ready", notes + [
                "pair-level block matches Phase139; structural replay baseline differs"
            ]
        return "fade_switch_block_shadow_ready", notes + [
            "hybrid pair proxy reproduces Phase139 full-block gain"
        ]

    if delta < 5.0 and pair_delta < 30.0:
        return "block_not_reproducing_phase139", notes + [
            "structural replay and pair proxy both below Phase139 block gain"
        ]

    if missed_rate is not None and missed_rate > 0.55 and delta < PHASE139_BLOCK_DELTA * 0.5:
        return "too_many_good_switches_blocked", notes

    if delta >= 15.0 and (missed_rate is None or missed_rate <= 0.50):
        return "fade_switch_block_shadow_ready", notes

    if delta >= 5.0 and avoided >= missed:
        return "fade_switch_block_shadow_ready", notes + ["marginal replay gain; net block helps"]

    if missed > avoided * 1.2:
        return "too_many_good_switches_blocked", notes

    return "block_not_reproducing_phase139", notes


def analyze_fade_switch_block_shadow(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
) -> dict[str, Any]:
    all_a: list[StructuralTrade] = []
    all_b: list[StructuralTrade] = []
    per_session: list[dict[str, Any]] = []
    block_events: list[dict[str, Any]] = []
    total_blocks = 0
    accepted_a = accepted_b = 0

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

        trades_a, log_a = replay_combined_structural_exit(
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
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_SWITCH_BLOCK_SHADOW,
        )

        for row in _block_events(log_b):
            row["session_id"] = session_id
            block_events.append(row)

        ma = _metrics(trades_a)
        mb = _metrics(trades_b)
        switch_a = _count_fade_switches(trades_a)
        switch_b = _count_fade_switches(trades_b)
        blocks = sum(1 for e in log_b if str(e.get("event_kind") or "") == "fade_switch_blocked")
        block_entered = sum(
            1 for e in log_b if str(e.get("event_kind") or "") == "fade_switch_block_entered"
        )
        avoided, missed, _ = _switch_block_outcomes(log_b, trades_a)
        total_blocks += blocks
        acc_a = sum(1 for e in log_a if str(e.get("event_kind") or "") == "entry")
        acc_b = sum(1 for e in log_b if str(e.get("event_kind") or "") == "entry")
        accepted_a += acc_a
        accepted_b += acc_b

        all_a.extend(trades_a)
        all_b.extend(trades_b)

        per_session.append(
            {
                "session_id": session_id,
                "A_total_pnl_proxy": ma["total_pnl_proxy"],
                "B_total_pnl_proxy": mb["total_pnl_proxy"],
                "delta_B_minus_A": round(
                    float(mb["total_pnl_proxy"]) - float(ma["total_pnl_proxy"]), 4
                ),
                "A_trade_count": ma["trade_count"],
                "B_trade_count": mb["trade_count"],
                "fade_switch_count_A": switch_a,
                "fade_switch_count_B": switch_b,
                "blocked_switch_count": blocks,
                "fade_block_entered_count": block_entered,
                "avoided_bad_new": avoided,
                "missed_good_new": missed,
                "accepted_count_A": acc_a,
                "accepted_count_B": acc_b,
            }
        )

    ma = _metrics(all_a)
    mb = _metrics(all_b)
    avoided = sum(int(s.get("avoided_bad_new") or 0) for s in per_session)
    missed = sum(int(s.get("missed_good_new") or 0) for s in per_session)

    comparison = {
        "scenario_A": POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        "scenario_B": POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_SWITCH_BLOCK_SHADOW,
        **ma,
        "B_total_pnl_proxy": mb["total_pnl_proxy"],
        "B_PF_proxy": mb["PF_proxy"],
        "B_win_rate": mb["win_rate"],
        "B_trade_count": mb["trade_count"],
        "delta_total_pnl_proxy": round(
            float(mb["total_pnl_proxy"]) - float(ma["total_pnl_proxy"]), 4
        ),
        "avg_delta": round(
            (float(mb["total_pnl_proxy"]) - float(ma["total_pnl_proxy"]))
            / max(len(per_session), 1),
            4,
        ),
        "accepted_count_A": accepted_a,
        "accepted_count_B": accepted_b,
        "blocked_switch_count": total_blocks,
        "avoided_bad_new": avoided,
        "missed_good_new": missed,
        "fade_switch_count_A": _count_fade_switches(all_a),
        "fade_switch_count_B": _count_fade_switches(all_b),
        "phase139_reference": {
            "A_total_pnl_proxy": -65.63,
            "B_fade_switch_block_total_pnl_proxy": 7.1041,
            "delta_total_vs_A": PHASE139_BLOCK_DELTA,
            "fade_switch_pairs": 294,
        },
        "pair_level_block_proxy": _pair_level_block_proxy(session_dirs),
        "by_exit_reason_delta": _by_exit_reason_delta(all_a, all_b),
    }

    verdict, notes = determine_verdict(comparison, comparison["pair_level_block_proxy"])

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "comparison": comparison,
        "sessions": per_session,
        "block_events": block_events,
        "session_count": len(per_session),
    }
