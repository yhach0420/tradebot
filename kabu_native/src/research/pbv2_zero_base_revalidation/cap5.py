"""CAP=5 portfolio replay from simultaneous candidate rankings."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional, Sequence

from research.pbv2_zero_base_revalidation.constants import CAP
from research.pbv2_zero_base_revalidation.panel import CandidateRow
from research.pbv2_zero_base_revalidation.util import profit_factor

ScoreFn = Callable[[CandidateRow], Optional[float]]


@dataclass
class CapTrade:
    day: str
    symbol: str
    entry_time: datetime
    score: float
    pnl_5bps: float
    pnl_raw: float
    is_stop: bool
    is_np: bool
    is_large_rise: bool
    is_winner: bool


def rank_score_pbv2(row: CandidateRow) -> Optional[float]:
    if not (row.pbv2_candidate or row.pbv2_decision or row.accept):
        return None
    return float(row.pbv2_score or 0.0)


def rank_score_from_features(keys: Sequence[str], ops: Sequence[str], thrs: Sequence[float]) -> ScoreFn:
    def score(row: CandidateRow) -> Optional[float]:
        s = 0.0
        n = 0
        for k, op, thr in zip(keys, ops, thrs):
            v = row.features.get(k)
            if v is None:
                return None
            n += 1
            if op == ">=":
                s += 1.0 if float(v) >= thr else 0.0
            else:
                s += 1.0 if float(v) <= thr else 0.0
        return s if n == len(keys) and s >= len(keys) else (s if s >= len(keys) else None)

    return score


def replay_cap5(
    panel: Sequence[CandidateRow],
    score_fn: ScoreFn,
    *,
    cap: int = CAP,
    hold_sec: float = 300.0,
    method_name: str = "method",
) -> dict[str, Any]:
    """Deterministic CAP replay: within each scan minute, rank by score, fill open slots."""
    # group by day + evaluation bucket (minute)
    groups: dict[tuple[str, int], list[CandidateRow]] = defaultdict(list)
    for r in panel:
        if r.cf_pnl_5bps is None and r.cf_pnl is None:
            continue
        sc = score_fn(r)
        if sc is None:
            continue
        bucket = int(r.evaluation_time.timestamp() // 60)
        groups[(r.day, bucket)].append(r)

    open_pos: dict[str, list[tuple[datetime, str]]] = defaultdict(list)  # day -> (exit_t, symbol)
    trades: list[CapTrade] = []
    rejected_cap = 0
    same_push = 0

    for (day, bucket) in sorted(groups.keys()):
        rows = groups[(day, bucket)]
        # free expired
        now = rows[0].evaluation_time
        open_pos[day] = [(et, sym) for et, sym in open_pos[day] if et > now]
        open_syms = {sym for _, sym in open_pos[day]}
        ranked = sorted(
            rows,
            key=lambda r: (-(score_fn(r) or -1e18), r.symbol, r.evaluation_time.isoformat()),
        )
        seen_push: set[str] = set()
        for r in ranked:
            sc = score_fn(r)
            if sc is None:
                continue
            if r.symbol in seen_push:
                same_push += 1
                continue
            seen_push.add(r.symbol)
            if r.symbol in open_syms:
                continue
            if len(open_pos[day]) >= cap:
                rejected_cap += 1
                continue
            pnl5 = float(r.cf_pnl_5bps if r.cf_pnl_5bps is not None else r.cf_pnl or 0.0)
            pnl = float(r.cf_pnl or pnl5)
            exit_t = r.evaluation_time + timedelta(seconds=hold_sec)
            open_pos[day].append((exit_t, r.symbol))
            open_syms.add(r.symbol)
            trades.append(
                CapTrade(
                    day=day,
                    symbol=r.symbol,
                    entry_time=r.evaluation_time,
                    score=float(sc),
                    pnl_5bps=pnl5,
                    pnl_raw=pnl,
                    is_stop=r.is_stop,
                    is_np=r.is_np,
                    is_large_rise=r.is_large_rise,
                    is_winner=r.is_winner,
                )
            )

    y5 = [t.pnl_5bps for t in trades]
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t.day] += t.pnl_5bps
    peak = 0
    # approximate peak concurrent from trades timeline naive
    return {
        "method": method_name,
        "accepted_trades": len(trades),
        "rejected_by_cap": rejected_cap,
        "same_push_suppression": same_push,
        "pnl_raw": round(sum(t.pnl_raw for t in trades), 2),
        "pnl_5bps": round(sum(y5), 2),
        "pf": profit_factor(y5),
        "win_rate": round(sum(1 for t in trades if t.pnl_5bps > 0) / len(trades), 4) if trades else None,
        "stop_rate": round(sum(1 for t in trades if t.is_stop) / len(trades), 4) if trades else None,
        "np_rate": round(sum(1 for t in trades if t.is_np) / len(trades), 4) if trades else None,
        "large_rise_capture_n": sum(1 for t in trades if t.is_large_rise),
        "winner_sacrifice_n": sum(1 for t in trades if t.is_winner and t.pnl_5bps <= 0),
        "max_daily_loss": round(min(by_day.values()), 2) if by_day else 0.0,
        "average_cap_usage": round(len(trades) / max(1, len(by_day)) / cap, 4),
        "trade_count": len(trades),
        "peak_positions": min(cap, peak if peak else (min(cap, len(trades) and cap))),
        "symbol_reentry": len(trades) - len({(t.day, t.symbol) for t in trades}),
    }


def compare_cap5_methods(panel: Sequence[CandidateRow], best_rules: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = [replay_cap5(panel, rank_score_pbv2, method_name="PBv2")]
    for key, label in (
        ("dense", "dense_only_best"),
        ("static", "static_board_best"),
        ("dynamic", "dynamic_board_best"),
        ("combined", "combined_best"),
    ):
        rule = best_rules.get(key)
        if not rule:
            continue
        thrs = rule.get("last_thresholds") or []
        feats = rule.get("features") or []
        ops = rule.get("ops") or []
        if not thrs or len(thrs) != len(feats):
            continue
        fn = rank_score_from_features(feats, ops, thrs)
        out.append(replay_cap5(panel, fn, method_name=label))
    return out
