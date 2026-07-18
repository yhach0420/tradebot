"""
Phase670: Flat weak + range reject forward shadow (no ENTRY block).

Shadow counterfactual on mainline-accepted entries only.
Blocks when flat_weak_refined and/or flat_range_breakout (Phase667 spec).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from research.phase631_profit_source_attribution import _num
from research.phase665_pretrend_shape_analysis import classify_pretrend_shape
from research.phase666_breakout_initiation_analysis import classify_breakout_initiation
from research.phase667_flat_vwap_volume_refinement import _flat_weak_refined

REASON_FLAT_WEAK_REFINED = "flat_weak_refined"
REASON_FLAT_RANGE_BREAKOUT = "flat_range_breakout"
REASON_BOTH = "both"

BIG_WINNER_YEN = 5000.0

ENTRY_FIELD_KEYS = (
    "flat_weak_range_shadow_candidate",
    "flat_weak_range_shadow_block",
    "flat_weak_range_shadow_reason",
    "pretrend_shape",
    "flat_subclass",
    "breakout_class",
)

EXIT_EXTRA_FIELD_KEYS = (
    "actual_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_yen",
    "blocked_winner",
    "blocked_loser",
    "blocked_big_winner",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def flat_weak_range_shadow_enabled(config: Any) -> bool:
    return bool(getattr(config, "flat_weak_range_shadow_enabled", False))


def _session_bucket(fields: Mapping[str, Any]) -> str:
    mins = _float(fields.get("minutes_from_open"))
    if mins is None:
        sk = str(fields.get("session_kind") or "").lower()
        if sk in ("am", "pm"):
            return sk.upper()
        return "unknown"
    if mins < 150:
        return "AM"
    if mins >= 210:
        return "PM"
    return "lunch"


def infer_pretrend_shape(trade: Mapping[str, Any]) -> str:
    shape = str(trade.get("pretrend_shape") or "")
    if shape in ("A", "B", "C", "D", "E", "F"):
        return shape
    feat = {
        "computed": True,
        "r300_sec": _float(trade.get("entry_rise_5min_pct")),
        "r600_sec": _float(trade.get("entry_rise_10min_pct")),
        "r900_sec": _float(trade.get("entry_rise_15min_pct")),
        "r60_sec": _float(trade.get("r60_sec")),
        "r120_sec": _float(trade.get("r120_sec")),
        "vwap_dev_pct": _float(trade.get("vwap_dev_pct") or trade.get("entry_vwap_dev_pct")),
        "high_update_5min": int(trade.get("high_update_5min") or 0),
        "high_update_10min": int(trade.get("high_update_10min") or 0),
        "low_update_5min": int(trade.get("low_update_5min") or 0),
        "low_update_10min": int(trade.get("low_update_10min") or 0),
    }
    if feat["r300_sec"] is None and feat["r600_sec"] is None:
        return "U"
    return classify_pretrend_shape(feat)


def infer_breakout_class(trade: Mapping[str, Any], *, pretrend_shape: str) -> str:
    existing = str(trade.get("breakout_class") or "")
    if existing in ("A", "B", "C", "D", "E", "F", "NA"):
        return existing
    feat = {
        "r60_sec": _float(trade.get("r60_sec")),
        "r120_sec": _float(trade.get("r120_sec")),
        "day_high_distance_pct": _float(trade.get("day_high_distance_pct")),
        "recent_low_break": bool(trade.get("recent_low_break")),
        "recent_high_break": bool(trade.get("recent_high_break") or trade.get("entry_high_break_recent")),
        "vwap_cross_down": bool(trade.get("vwap_cross_down")),
        "vwap_cross_up": bool(trade.get("vwap_cross_up")),
        "vwap_dev_pct": _float(trade.get("vwap_dev_pct") or trade.get("entry_vwap_dev_pct")),
        "range_expansion": bool(trade.get("range_expansion")),
        "day_high_update": bool(trade.get("day_high_update")),
        "volume_spike": bool(trade.get("volume_spike")),
        "board_improvement": bool(trade.get("board_improvement")),
        "board_imbalance_jump": bool(trade.get("board_imbalance_jump")),
    }
    return classify_breakout_initiation(feat, pretrend_shape=pretrend_shape)


def is_flat_range_breakout(trade: Mapping[str, Any]) -> bool:
    pretrend = infer_pretrend_shape(trade)
    if pretrend != "E":
        return False
    breakout = infer_breakout_class(trade, pretrend_shape=pretrend)
    return breakout == "A"


def is_flat_weak_refined_shadow(trade: Mapping[str, Any]) -> bool:
    row = dict(trade)
    if not row.get("pretrend_shape"):
        row["pretrend_shape"] = infer_pretrend_shape(row)
    if row.get("vwap_dev_pct") is None and row.get("entry_vwap_dev_pct") is not None:
        row["vwap_dev_pct"] = row.get("entry_vwap_dev_pct")
    return _flat_weak_refined(row)


def flat_subclass_label(trade: Mapping[str, Any], *, reason: str) -> str:
    if reason == REASON_BOTH:
        return "flat_weak_refined+flat_range_breakout"
    if reason == REASON_FLAT_WEAK_REFINED:
        return "flat_weak_refined"
    if reason == REASON_FLAT_RANGE_BREAKOUT:
        return "flat_range_breakout"
    pretrend = infer_pretrend_shape(trade)
    if pretrend == "E":
        return infer_breakout_class(trade, pretrend_shape=pretrend)
    return pretrend


def evaluate_flat_weak_range_shadow(trade: Mapping[str, Any]) -> tuple[bool, str]:
    weak = is_flat_weak_refined_shadow(trade)
    range_bo = is_flat_range_breakout(trade)
    if weak and range_bo:
        return True, REASON_BOTH
    if weak:
        return True, REASON_FLAT_WEAK_REFINED
    if range_bo:
        return True, REASON_FLAT_RANGE_BREAKOUT
    return False, ""


def would_block_flat_weak_range_shadow(trade: Mapping[str, Any]) -> bool:
    blocked, _ = evaluate_flat_weak_range_shadow(trade)
    return blocked


def compute_flat_weak_range_shadow_fields(config: Any, trade: Mapping[str, Any]) -> dict[str, Any]:
    pretrend = infer_pretrend_shape(trade)
    breakout = infer_breakout_class(trade, pretrend_shape=pretrend) if pretrend == "E" else "NA"
    base = {
        "flat_weak_range_shadow_candidate": False,
        "flat_weak_range_shadow_block": False,
        "flat_weak_range_shadow_reason": "",
        "pretrend_shape": pretrend,
        "breakout_class": breakout,
        "flat_subclass": "",
    }
    if not flat_weak_range_shadow_enabled(config):
        return base
    blocked, reason = evaluate_flat_weak_range_shadow({**trade, "pretrend_shape": pretrend, "breakout_class": breakout})
    return {
        **base,
        "flat_weak_range_shadow_candidate": True,
        "flat_weak_range_shadow_block": blocked,
        "flat_weak_range_shadow_reason": reason,
        "flat_subclass": flat_subclass_label(trade, reason=reason),
    }


def enrich_exit_flat_weak_range_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    blocked = _bool(entry_shadow.get("flat_weak_range_shadow_block"))
    actual_yen = round(compute_pnl_yen_100(entry_price, exit_price), 2)
    shadow_yen = 0.0 if blocked else actual_yen
    return {
        "flat_weak_range_shadow_candidate": entry_shadow.get("flat_weak_range_shadow_candidate"),
        "flat_weak_range_shadow_block": blocked,
        "flat_weak_range_shadow_reason": entry_shadow.get("flat_weak_range_shadow_reason", ""),
        "pretrend_shape": entry_shadow.get("pretrend_shape"),
        "flat_subclass": entry_shadow.get("flat_subclass"),
        "breakout_class": entry_shadow.get("breakout_class"),
        "actual_pnl_yen_100": actual_yen,
        "shadow_pnl_yen_100": shadow_yen,
        "delta_yen": round(shadow_yen - actual_yen, 2),
        "blocked_winner": bool(blocked and actual_yen > 0),
        "blocked_loser": bool(blocked and actual_yen < 0),
        "blocked_big_winner": bool(blocked and actual_yen >= BIG_WINNER_YEN),
        "stop_hit": exit_reason == "stop_hit",
    }


def _pf(yens: list[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else 999.0
    return round(gp / gl, 4)


@dataclass
class _BucketStats:
    target_count: int = 0
    block_count: int = 0
    actual_pnl: float = 0.0
    shadow_pnl: float = 0.0
    blocked_winners: int = 0
    blocked_losers: int = 0
    blocked_big_winners: int = 0
    stop_hit_actual: int = 0
    stop_hit_shadow: int = 0
    no_progress_actual: int = 0
    no_progress_shadow: int = 0
    mfe0_actual: int = 0
    mfe0_shadow: int = 0


def _sym_entry_key(symbol: str, entry_time: str) -> str:
    return f"{symbol}|{entry_time}"


@dataclass
class FlatWeakRangeForwardShadowCounters:
    flat_weak_range_shadow_target_count: int = 0
    flat_weak_range_shadow_block_count: int = 0
    flat_weak_range_shadow_kept_count: int = 0
    actual_total_pnl_yen_100: float = 0.0
    shadow_total_pnl_yen_100: float = 0.0
    blocked_winners: int = 0
    blocked_losers: int = 0
    blocked_big_winners: int = 0
    stop_hit_count_actual: int = 0
    stop_hit_count_shadow: int = 0
    no_progress_count_actual: int = 0
    no_progress_count_shadow: int = 0
    mfe0_count_actual: int = 0
    mfe0_count_shadow: int = 0
    exit_join_count: int = 0
    exit_join_miss_count: int = 0
    _actual_yens: list[float] = field(default_factory=list)
    _shadow_yens: list[float] = field(default_factory=list)
    _buckets: dict[str, _BucketStats] = field(default_factory=dict)
    # Phase687W59: immutable join books (not FIFO)
    _open_by_position_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    _open_by_sym_entry: dict[str, dict[str, Any]] = field(default_factory=dict)
    _exited_keys: set[str] = field(default_factory=set)

    def _bucket(self, name: str) -> _BucketStats:
        if name not in self._buckets:
            self._buckets[name] = _BucketStats()
        return self._buckets[name]

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        if not _bool(fields.get("flat_weak_range_shadow_candidate")):
            return
        self.flat_weak_range_shadow_target_count += 1
        bucket = self._bucket(_session_bucket(fields))
        bucket.target_count += 1
        blocked = _bool(fields.get("flat_weak_range_shadow_block"))
        if blocked:
            self.flat_weak_range_shadow_block_count += 1
            bucket.block_count += 1
        else:
            self.flat_weak_range_shadow_kept_count += 1
        snap = {
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": blocked,
            "flat_weak_range_shadow_reason": fields.get("flat_weak_range_shadow_reason") or "",
            "minutes_from_open": fields.get("minutes_from_open"),
            "session_kind": fields.get("session_kind"),
            "symbol": str(fields.get("symbol") or ""),
            "entry_time": str(fields.get("entry_time") or ""),
            "decision_id": str(fields.get("decision_id") or fields.get("candidate_id") or ""),
        }
        pid = str(fields.get("position_id") or fields.get("observer_position_id") or "")
        if pid:
            self._open_by_position_id[pid] = snap
        sk = _sym_entry_key(snap["symbol"], snap["entry_time"])
        if snap["symbol"] and snap["entry_time"]:
            self._open_by_sym_entry[sk] = snap
        elif snap["decision_id"]:
            self._open_by_sym_entry[f"decision|{snap['decision_id']}"] = snap

    def bind_position(
        self,
        *,
        position_id: str,
        symbol: str,
        entry_time: str,
        decision_id: str = "",
    ) -> None:
        """Bind immutable position_id after official register (accept may precede id)."""
        pid = str(position_id or "")
        if not pid:
            return
        sk = _sym_entry_key(str(symbol or ""), str(entry_time or ""))
        snap = self._open_by_sym_entry.get(sk)
        if snap is None and decision_id:
            snap = self._open_by_sym_entry.get(f"decision|{decision_id}")
        if snap is None:
            return
        bound = dict(snap)
        bound["position_id"] = pid
        bound["symbol"] = str(symbol or snap.get("symbol") or "")
        bound["entry_time"] = str(entry_time or snap.get("entry_time") or "")
        self._open_by_position_id[pid] = bound
        if bound["symbol"] and bound["entry_time"]:
            self._open_by_sym_entry[_sym_entry_key(bound["symbol"], bound["entry_time"])] = bound

    def _lookup_open(self, row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        pid = str(row.get("position_id") or row.get("observer_position_id") or "")
        if pid and pid in self._open_by_position_id:
            return self._open_by_position_id.get(pid)
        sk = _sym_entry_key(str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        if sk in self._open_by_sym_entry:
            return self._open_by_sym_entry.get(sk)
        did = str(row.get("decision_id") or row.get("candidate_id") or "")
        if did:
            return self._open_by_sym_entry.get(f"decision|{did}")
        return None

    def record_exit(self, row: Mapping[str, Any]) -> None:
        open_snap = self._lookup_open(row)
        candidate = _bool(row.get("flat_weak_range_shadow_candidate"))
        if not candidate and open_snap is not None:
            # W59 join fix: EXIT row missing FWR fields → recover from open book
            candidate = _bool(open_snap.get("flat_weak_range_shadow_candidate"))
            row = {
                **dict(row),
                "flat_weak_range_shadow_candidate": open_snap.get("flat_weak_range_shadow_candidate"),
                "flat_weak_range_shadow_block": open_snap.get("flat_weak_range_shadow_block"),
                "flat_weak_range_shadow_reason": open_snap.get("flat_weak_range_shadow_reason"),
                "minutes_from_open": row.get("minutes_from_open") or open_snap.get("minutes_from_open"),
                "session_kind": row.get("session_kind") or open_snap.get("session_kind"),
            }
        if not candidate:
            self.exit_join_miss_count += 1
            return
        pid = str(row.get("position_id") or row.get("observer_position_id") or "")
        sk = _sym_entry_key(str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        dedupe = pid or sk
        if dedupe and dedupe in self._exited_keys:
            return
        if dedupe:
            self._exited_keys.add(dedupe)
        self.exit_join_count += 1
        delta = _float(row.get("delta_yen"))
        shadow_yen = _float(row.get("shadow_pnl_yen_100"))
        actual_yen = _float(row.get("actual_pnl_yen_100"))
        blocked = _bool(row.get("flat_weak_range_shadow_block"))
        if actual_yen is None or shadow_yen is None:
            # recompute from prices when join recovered without enrich fields
            ep = _float(row.get("entry_price"))
            xp = _float(row.get("exit_price") or row.get("current_price"))
            if ep is not None and xp is not None and ep > 0:
                from replay.pnl_yen import compute_pnl_yen_100

                actual_yen = round(compute_pnl_yen_100(ep, xp), 2)
                shadow_yen = 0.0 if blocked else actual_yen
                delta = round(shadow_yen - actual_yen, 2)
        if actual_yen is None:
            actual_yen = round((shadow_yen or 0.0) - (delta or 0.0), 2)
        if shadow_yen is None:
            shadow_yen = 0.0 if blocked else actual_yen
        bucket = self._bucket(_session_bucket(row))
        self.actual_total_pnl_yen_100 = round(self.actual_total_pnl_yen_100 + actual_yen, 2)
        self.shadow_total_pnl_yen_100 = round(self.shadow_total_pnl_yen_100 + shadow_yen, 2)
        self._actual_yens.append(actual_yen)
        self._shadow_yens.append(shadow_yen)
        bucket.actual_pnl = round(bucket.actual_pnl + actual_yen, 2)
        bucket.shadow_pnl = round(bucket.shadow_pnl + shadow_yen, 2)
        if blocked:
            if actual_yen > 0:
                self.blocked_winners += 1
                bucket.blocked_winners += 1
            elif actual_yen < 0:
                self.blocked_losers += 1
                bucket.blocked_losers += 1
            if actual_yen >= BIG_WINNER_YEN:
                self.blocked_big_winners += 1
                bucket.blocked_big_winners += 1
        stop = _bool(row.get("stop_hit")) or str(row.get("exit_reason") or "") == "stop_hit"
        no_prog = str(row.get("exit_reason") or "") == "no_progress_exit"
        mfe = _float(row.get("peak_mfe_pct"))
        mfe0 = mfe is not None and float(mfe) <= 0.0
        if stop:
            self.stop_hit_count_actual += 1
            bucket.stop_hit_actual += 1
            if not blocked:
                self.stop_hit_count_shadow += 1
                bucket.stop_hit_shadow += 1
        if no_prog:
            self.no_progress_count_actual += 1
            bucket.no_progress_actual += 1
            if not blocked:
                self.no_progress_count_shadow += 1
                bucket.no_progress_shadow += 1
        if mfe0:
            self.mfe0_count_actual += 1
            bucket.mfe0_actual += 1
            if not blocked:
                self.mfe0_count_shadow += 1
                bucket.mfe0_shadow += 1
        if pid:
            self._open_by_position_id.pop(pid, None)
        if sk:
            self._open_by_sym_entry.pop(sk, None)

    def summary_fields(self) -> dict[str, Any]:
        n = self.flat_weak_range_shadow_target_count
        delta = round(self.shadow_total_pnl_yen_100 - self.actual_total_pnl_yen_100, 2)
        actual_pf = _pf(self._actual_yens)
        shadow_pf = _pf(self._shadow_yens)
        delta_pf = (
            round(float(shadow_pf or 0) - float(actual_pf or 0), 4)
            if actual_pf is not None and shadow_pf is not None
            else None
        )
        return {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": self.flat_weak_range_shadow_target_count,
            "flat_weak_range_shadow_block_count": self.flat_weak_range_shadow_block_count,
            "flat_weak_range_shadow_kept_count": self.flat_weak_range_shadow_kept_count,
            "flat_weak_range_shadow_completed": self.exit_join_count,
            "flat_weak_range_shadow_exit_join_count": self.exit_join_count,
            "flat_weak_range_shadow_exit_join_miss_count": self.exit_join_miss_count,
            "flat_weak_range_shadow_actual_total_pnl_yen_100": self.actual_total_pnl_yen_100,
            "flat_weak_range_shadow_total_pnl_yen_100": self.shadow_total_pnl_yen_100,
            "flat_weak_range_shadow_delta_yen": delta,
            "flat_weak_range_shadow_blocked_winners": self.blocked_winners,
            "flat_weak_range_shadow_blocked_losers": self.blocked_losers,
            "flat_weak_range_shadow_blocked_big_winners": self.blocked_big_winners,
            "flat_weak_range_shadow_stop_hit_count_actual": self.stop_hit_count_actual,
            "flat_weak_range_shadow_stop_hit_count_shadow": self.stop_hit_count_shadow,
            "flat_weak_range_shadow_stop_hit_reduction": (
                self.stop_hit_count_actual - self.stop_hit_count_shadow
            ),
            "flat_weak_range_shadow_no_progress_count_actual": self.no_progress_count_actual,
            "flat_weak_range_shadow_no_progress_count_shadow": self.no_progress_count_shadow,
            "flat_weak_range_shadow_no_progress_reduction": (
                self.no_progress_count_actual - self.no_progress_count_shadow
            ),
            "flat_weak_range_shadow_mfe0_count_actual": self.mfe0_count_actual,
            "flat_weak_range_shadow_mfe0_count_shadow": self.mfe0_count_shadow,
            "flat_weak_range_shadow_mfe0_reduction": self.mfe0_count_actual - self.mfe0_count_shadow,
            "flat_weak_range_shadow_actual_pf": actual_pf,
            "flat_weak_range_shadow_shadow_pf": shadow_pf,
            "flat_weak_range_shadow_delta_pf": delta_pf,
            "flat_weak_range_shadow_block_rate": round(self.flat_weak_range_shadow_block_count / n, 4)
            if n
            else None,
        }


def build_flat_weak_range_forward_shadow_counters(config: Any) -> Optional[FlatWeakRangeForwardShadowCounters]:
    if not flat_weak_range_shadow_enabled(config):
        return None
    return FlatWeakRangeForwardShadowCounters()


def format_flat_weak_range_shadow_discord_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary.get("flat_weak_range_shadow_enabled"):
        return []
    return [
        "FlatWeak+Range Shadow:",
        (
            f"target={summary.get('flat_weak_range_shadow_target_count', 0)} "
            f"block={summary.get('flat_weak_range_shadow_block_count', 0)} "
            f"completed={summary.get('flat_weak_range_shadow_completed', 0)}"
        ),
        (
            f"blocked W/L: {summary.get('flat_weak_range_shadow_blocked_winners', 0)}/"
            f"{summary.get('flat_weak_range_shadow_blocked_losers', 0)} "
            f"big_win={summary.get('flat_weak_range_shadow_blocked_big_winners', 0)}"
        ),
        (
            f"ΔPnL={summary.get('flat_weak_range_shadow_delta_yen', 0)} "
            f"ΔPF={summary.get('flat_weak_range_shadow_delta_pf')}"
        ),
        (
            f"stop_hit↓={summary.get('flat_weak_range_shadow_stop_hit_reduction', 0)} "
            f"no_progress↓={summary.get('flat_weak_range_shadow_no_progress_reduction', 0)} "
            f"MFE0↓={summary.get('flat_weak_range_shadow_mfe0_reduction', 0)}"
        ),
    ]
