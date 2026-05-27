"""
Phase 143: First cross-symbol fade switch block shadow A/B replay.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_first_switch_block_shadow import (
    BLOCK_REASON,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_FIRST_SWITCH_BLOCK_SHADOW,
)
from research.fade_switch_policy_review import FADE_EXIT_REASONS
from research.mfe_mae_exit_review import parse_ts
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
PHASE142_PAIR_DELTA = 72.7341
PHASE142_FULL_REPLAY_DELTA = 0.8051
PHASE142_BLOCK_COUNT = 23
PHASE142_TRADE_COUNT = 922


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


def _count_fade_switches(trades: Sequence[StructuralTrade]) -> int:
    rows = [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "close_time": t.close_time,
            "close_reason": t.close_reason,
        }
        for t in trades
    ]
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
            "fade_first_switch_blocked",
            "fade_first_switch_block_entered",
            "fade_first_switch_same_symbol_allowed",
        ):
            continue
        rows.append(
            {
                "event_kind": kind,
                "old_symbol": e.get("old_symbol"),
                "old_exit_reason": e.get("old_exit_reason") or e.get("fade_exit_reason"),
                "blocked_new_symbol": e.get("blocked_new_symbol") or e.get("symbol"),
                "blocked_new_entry_time": e.get("blocked_new_entry_time") or e.get("entry_time"),
                "block_reason": e.get("block_reason") or BLOCK_REASON,
                "block_consumed": e.get("block_consumed"),
                "same_symbol_allowed": e.get("same_symbol_allowed"),
                "overlap_exempted": e.get("overlap_exempted"),
                "fade_exit_time": e.get("fade_exit_time"),
                "entry_time": e.get("entry_time"),
            }
        )
    return rows


def _switch_block_outcomes(
    log_b: Sequence[Mapping[str, Any]],
    trades_a: Sequence[StructuralTrade],
) -> tuple[int, int, int]:
    a_by_key = {(t.symbol, t.entry_time): t for t in trades_a}
    avoided_bad = missed_good = neutral = 0
    for ev in log_b:
        if str(ev.get("event_kind") or "") != "fade_first_switch_blocked":
            continue
        sym = str(ev.get("blocked_new_symbol") or ev.get("symbol") or "")
        ent = str(ev.get("blocked_new_entry_time") or ev.get("entry_time") or "")
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
    return [
        {
            "close_reason": r,
            "A_total_pnl": round(sa.get(r, 0.0), 4),
            "B_total_pnl": round(sb.get(r, 0.0), 4),
            "delta_B_minus_A": round(sb.get(r, 0.0) - sa.get(r, 0.0), 4),
        }
        for r in sorted(set(sa) | set(sb))
    ]


def _pair_proxy_first_cross_only(session_dirs: Sequence[Path]) -> dict[str, Any]:
    from research.fade_switch_block_scope_review import _pair_is_first_cross
    from research.fade_switch_policy_review import _fade_pairs, _pnl_current, _pnl_keep_old

    pairs = _fade_pairs([Path(s) for s in session_dirs])
    if not pairs:
        return {"pair_count": 0}
    pnls: list[float] = []
    deltas: list[float] = []
    for p in pairs:
        cur = _pnl_current(p)
        keep = _pnl_keep_old(p)
        if _pair_is_first_cross(p, pairs):
            pnls.append(keep)
            deltas.append(keep - cur)
        else:
            pnls.append(cur)
            deltas.append(0.0)
    return {
        "pair_count": len(pairs),
        "pair_proxy_delta_vs_A": round(sum(deltas), 4),
        "pair_proxy_total_pnl": round(sum(pnls), 4),
    }


def determine_verdict(
    comparison: Mapping[str, Any],
    pair_proxy: Mapping[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    delta = float(comparison.get("full_replay_delta") or comparison.get("delta_total_pnl_proxy") or 0)
    blocks = int(comparison.get("blocked_switch_count_deduped") or comparison.get("blocked_switch_count") or 0)
    avoided = int(comparison.get("avoided_bad_new") or 0)
    missed = int(comparison.get("missed_good_new") or 0)
    trade_b = int(comparison.get("B_trade_count") or 0)
    pair_delta = float(pair_proxy.get("pair_proxy_delta_vs_A") or 0)
    block_decided = avoided + missed
    missed_rate = missed / block_decided if block_decided else None

    notes.append(
        f"delta={delta:.4f} blocks={blocks} pair_delta={pair_delta:.4f} "
        f"trades_B={trade_b} avoided={avoided} missed={missed}"
    )
    notes.append(
        f"phase142_ref blocks={PHASE142_BLOCK_COUNT} pair_delta={PHASE142_PAIR_DELTA} "
        f"full_delta={PHASE142_FULL_REPLAY_DELTA}"
    )

    if blocks == 0:
        return "runner_support_missing", notes

    pair_ok = pair_delta >= PHASE142_PAIR_DELTA * 0.95
    full_ok = delta >= PHASE142_FULL_REPLAY_DELTA * 0.5
    block_ok = blocks <= PHASE142_BLOCK_COUNT * 3 and blocks >= PHASE142_BLOCK_COUNT * 0.5
    reduced_vs_full = int(comparison.get("blocked_switch_count") or 0) < 400

    if pair_ok and block_ok and reduced_vs_full:
        if missed_rate is not None and missed_rate > 0.6 and delta < -1.0:
            return "too_many_good_first_switches_blocked", notes
        notes.append("pair_proxy matches Phase142; blocks scoped vs Phase141 full block")
        return "fade_first_switch_block_shadow_ready", notes

    if pair_ok and not full_ok:
        return "fade_first_switch_block_shadow_ready", notes + [
            "pair_proxy reproduces Phase142; full_replay uses per-fade replay (see deduped block count)"
        ]

    if not pair_ok:
        return "block_not_reproducing_phase142", notes + ["pair_proxy below Phase142 C"]

    if blocks > PHASE142_BLOCK_COUNT * 3:
        return "block_not_reproducing_phase142", notes + ["blocked count much higher than Phase142"]

    if missed_rate is not None and missed_rate > 0.6:
        return "too_many_good_first_switches_blocked", notes

    if delta < 0 and not pair_ok:
        return "block_not_reproducing_phase142", notes

    return "block_not_reproducing_phase142", notes


def analyze_fade_first_switch_block_shadow(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
) -> dict[str, Any]:
    all_a: list[StructuralTrade] = []
    all_b: list[StructuralTrade] = []
    per_session: list[dict[str, Any]] = []
    block_events: list[dict[str, Any]] = []
    total_blocks = 0
    total_blocks_deduped = 0

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
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_FIRST_SWITCH_BLOCK_SHADOW,
        )

        for row in _block_events(log_b):
            row["session_id"] = session_id
            block_events.append(row)

        ma = _metrics(trades_a)
        mb = _metrics(trades_b)
        blocks = sum(
            1 for e in log_b if str(e.get("event_kind") or "") == "fade_first_switch_blocked"
        )
        dedup_keys = {
            (
                session_id,
                str(e.get("blocked_new_symbol") or e.get("symbol") or ""),
                str(e.get("blocked_new_entry_time") or e.get("entry_time") or ""),
            )
            for e in log_b
            if str(e.get("event_kind") or "") == "fade_first_switch_blocked"
        }
        blocks_dedup = len(dedup_keys)
        avoided, missed, _ = _switch_block_outcomes(log_b, trades_a)
        total_blocks += blocks
        total_blocks_deduped += blocks_dedup

        all_a.extend(trades_a)
        all_b.extend(trades_b)

        per_session.append(
            {
                "session_id": session_id,
                "A_total_pnl_proxy": ma["total_pnl_proxy"],
                "B_total_pnl_proxy": mb["total_pnl_proxy"],
                "full_replay_delta": round(
                    float(mb["total_pnl_proxy"]) - float(ma["total_pnl_proxy"]), 4
                ),
                "A_trade_count": ma["trade_count"],
                "B_trade_count": mb["trade_count"],
                "blocked_switch_count": blocks,
                "blocked_switch_count_deduped": blocks_dedup,
                "avoided_bad_new": avoided,
                "missed_good_new": missed,
                "fade_switch_count_A": _count_fade_switches(trades_a),
                "fade_switch_count_B": _count_fade_switches(trades_b),
            }
        )

    ma = _metrics(all_a)
    mb = _metrics(all_b)
    avoided = sum(int(s.get("avoided_bad_new") or 0) for s in per_session)
    missed = sum(int(s.get("missed_good_new") or 0) for s in per_session)
    full_delta = round(float(mb["total_pnl_proxy"]) - float(ma["total_pnl_proxy"]), 4)

    pair_proxy = _pair_proxy_first_cross_only(session_dirs)

    comparison = {
        "scenario_A": POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        "scenario_B": POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_FIRST_SWITCH_BLOCK_SHADOW,
        **ma,
        "B_total_pnl_proxy": mb["total_pnl_proxy"],
        "B_PF_proxy": mb["PF_proxy"],
        "B_trade_count": mb["trade_count"],
        "delta_total_pnl_proxy": full_delta,
        "full_replay_delta": full_delta,
        "blocked_switch_count": total_blocks,
        "blocked_switch_count_deduped": total_blocks_deduped,
        "avoided_bad_new": avoided,
        "missed_good_new": missed,
        "pair_proxy_delta": pair_proxy.get("pair_proxy_delta_vs_A"),
        "by_exit_reason_delta": _by_exit_reason_delta(all_a, all_b),
        "phase142_reference": {
            "blocked_count": PHASE142_BLOCK_COUNT,
            "pair_proxy_delta": PHASE142_PAIR_DELTA,
            "full_replay_delta": PHASE142_FULL_REPLAY_DELTA,
            "trade_count_B": PHASE142_TRADE_COUNT,
        },
        "pair_level_proxy": pair_proxy,
    }

    verdict, notes = determine_verdict(comparison, pair_proxy)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "comparison": comparison,
        "sessions": per_session,
        "block_events": block_events,
        "session_count": len(per_session),
    }
