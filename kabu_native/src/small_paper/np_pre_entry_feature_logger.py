"""
Phase687: No-Progress Pre-Entry Board and Volume Forward Logger (logger only).

Collects compact pre-accept features for windows 10/30/60/120/300s:
  - price reacceleration
  - board persistence
  - volume-price sync

Hard rules:
  - no ENTRY/EXIT/PBv2/reject/ranking/IHC/order changes
  - no raw PUSH dump
  - 1 trade → 1 predictor row (+ separate outcome row at exit)
  - predictors use only source_ts <= accepted_at
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from screening.morning_screen import calc_board_imbalance

WINDOWS_SEC: tuple[int, ...] = (10, 30, 60, 120, 300)
RING_MAX_AGE_SEC = 360.0
MIN_TICKS_FOR_OK = 2

# Predictor-only keys written at accept (never include outcome fields).
PREDICTOR_META_KEYS = (
    "np_logger_row_id",
    "np_logger_ok",
    "np_feature_complete",
    "np_accepted_at",
    "np_max_source_ts",
    "np_future_leakage",
    "np_entry_live_computable",
    "symbol",
    "day",
    "session",
    "entry_time",
    "accepted_at",
    "entry_pool",
    "position_id",
)

OUTCOME_KEYS = (
    "np_logger_row_id",
    "symbol",
    "day",
    "session",
    "entry_time",
    "accepted_at",
    "exit_reason",
    "hold_sec",
    "pnl_yen_100",
    "pnl_pct",
    "is_no_progress_exit",
    "is_stop_hit",
    "is_winner",
    "is_loser",
    "is_big_winner",
    "source",
)

LEAKY_SUBSTRINGS = (
    "pnl",
    "mfe",
    "mae",
    "exit_",
    "hold_sec",
    "is_loser",
    "is_winner",
    "is_big_winner",
    "is_stop",
    "no_progress",
    "trailing",
    "shadow_pnl",
    "delta_yen",
    "outcome",
)

COLLECTION_GATES = {
    "DATA_COLLECTION_ONLY": 5,
    "FEATURE_STABILITY_REVIEW_ALLOWED": 5,
    "RULE_DISCOVERY_ALLOWED": 10,
}


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def np_pre_entry_feature_logger_enabled(config: Any) -> bool:
    return bool(getattr(config, "np_pre_entry_feature_logger_enabled", False))


def collection_gate_for_business_days(n_days: int) -> str:
    if n_days >= COLLECTION_GATES["RULE_DISCOVERY_ALLOWED"]:
        return "RULE_DISCOVERY_ALLOWED"
    if n_days >= COLLECTION_GATES["FEATURE_STABILITY_REVIEW_ALLOWED"]:
        return "FEATURE_STABILITY_REVIEW_ALLOWED"
    return "DATA_COLLECTION_ONLY"


def is_leaky_predictor_key(key: str) -> bool:
    k = str(key or "").lower()
    if k in PREDICTOR_META_KEYS:
        return False
    if k.startswith("np_") and any(p in k for p in ("ret_", "accel_", "slope_", "imb_", "bid_", "ask_", "tv_", "vol_", "ticks_")):
        return False
    return any(p in k for p in LEAKY_SUBSTRINGS)


# Compact board/volume snap: (ts, px, imb, bid_qty, ask_qty, trading_value)
BoardSnap = tuple[float, float, Optional[float], Optional[float], Optional[float], Optional[float]]


def extract_board_snap(payload: Mapping[str, Any], *, ts: float) -> Optional[BoardSnap]:
    px = _float(payload.get("CurrentPrice")) or 0.0
    if px <= 0 or ts <= 0:
        return None
    from small_paper.canonical_board import bid_ask_qty_for_mode, entry_imbalance_for_mode

    imb = entry_imbalance_for_mode(payload)
    if imb is None:
        imb = calc_board_imbalance(payload)
    bid, ask = bid_ask_qty_for_mode(payload)
    tv = _float(payload.get("TradingValue"))
    return (
        float(ts),
        float(px),
        round(float(imb), 6) if imb is not None else None,
        bid,
        ask,
        tv,
    )


def append_board_snap(
    ring: list[BoardSnap],
    snap: BoardSnap,
    *,
    max_age_sec: float = RING_MAX_AGE_SEC,
) -> None:
    ring.append(snap)
    cutoff = snap[0] - max_age_sec
    while ring and ring[0][0] < cutoff:
        ring.pop(0)


def _window_snaps(ring: Sequence[BoardSnap], *, accepted_at: float, window_sec: float) -> list[BoardSnap]:
    lo = accepted_at - window_sec
    # Strict: never include ticks after accepted_at.
    return [s for s in ring if lo <= s[0] <= accepted_at]


def _return_pct(start_px: float, end_px: float) -> Optional[float]:
    if start_px <= 0 or end_px <= 0:
        return None
    return round((end_px - start_px) / start_px * 100.0, 4)


def _slope_pct_per_min(snaps: Sequence[BoardSnap]) -> Optional[float]:
    pts = [(s[0], s[1]) for s in snaps if s[1] > 0]
    if len(pts) < 3:
        return None
    t0 = pts[0][0]
    xs = [(t - t0) / 60.0 for t, _ in pts]
    ys = [px for _, px in pts]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    if mean_y <= 0:
        return None
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den <= 0:
        return None
    return round((num / den) / mean_y * 100.0, 4)


def _accel(snaps: Sequence[BoardSnap]) -> Optional[float]:
    if len(snaps) < 4:
        return None
    mid = len(snaps) // 2
    early = snaps[:mid]
    late = snaps[mid:]
    if len(early) < 2 or len(late) < 2:
        return None
    e_ret = _return_pct(early[0][1], early[-1][1])
    l_ret = _return_pct(late[0][1], late[-1][1])
    if e_ret is None or l_ret is None:
        return None
    return round(l_ret - e_ret, 4)


def _chg(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round(end - start, 6)


def _pct_chg(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None or start == 0:
        return None
    return round((end - start) / abs(start) * 100.0, 4)


def _imb_persist(snaps: Sequence[BoardSnap]) -> Optional[float]:
    imbs = [s[2] for s in snaps if s[2] is not None]
    if len(imbs) < 2:
        return None
    start = imbs[0]
    if start is None:
        return None
    keep = sum(1 for v in imbs if v >= start - 1e-9)
    return round(keep / len(imbs), 4)


def _vol_price_sync(ret: Optional[float], tv_chg_pct: Optional[float]) -> Optional[float]:
    if ret is None or tv_chg_pct is None:
        return None
    if abs(ret) < 1e-9 or abs(tv_chg_pct) < 1e-9:
        return 0.0
    return 1.0 if (ret > 0) == (tv_chg_pct > 0) else -1.0


def _window_features(snaps: Sequence[BoardSnap], *, window_sec: int) -> dict[str, Any]:
    w = f"{window_sec}s"
    empty = {
        f"np_ret_{w}": None,
        f"np_accel_{w}": None,
        f"np_slope_{w}": None,
        f"np_imb_chg_{w}": None,
        f"np_imb_persist_{w}": None,
        f"np_bid_chg_{w}": None,
        f"np_ask_chg_{w}": None,
        f"np_tv_chg_pct_{w}": None,
        f"np_vol_price_sync_{w}": None,
        f"np_ticks_{w}": len(snaps),
    }
    if len(snaps) < MIN_TICKS_FOR_OK:
        return empty
    ret = _return_pct(snaps[0][1], snaps[-1][1])
    tv_chg = _pct_chg(snaps[0][5], snaps[-1][5])
    return {
        f"np_ret_{w}": ret,
        f"np_accel_{w}": _accel(snaps),
        f"np_slope_{w}": _slope_pct_per_min(snaps),
        f"np_imb_chg_{w}": _chg(snaps[0][2], snaps[-1][2]),
        f"np_imb_persist_{w}": _imb_persist(snaps),
        f"np_bid_chg_{w}": _chg(snaps[0][3], snaps[-1][3]),
        f"np_ask_chg_{w}": _chg(snaps[0][4], snaps[-1][4]),
        f"np_tv_chg_pct_{w}": tv_chg,
        f"np_vol_price_sync_{w}": _vol_price_sync(ret, tv_chg),
        f"np_ticks_{w}": len(snaps),
    }


def predictor_field_keys() -> tuple[str, ...]:
    keys: list[str] = list(PREDICTOR_META_KEYS)
    for w in WINDOWS_SEC:
        keys.extend(
            [
                f"np_ret_{w}s",
                f"np_accel_{w}s",
                f"np_slope_{w}s",
                f"np_imb_chg_{w}s",
                f"np_imb_persist_{w}s",
                f"np_bid_chg_{w}s",
                f"np_ask_chg_{w}s",
                f"np_tv_chg_pct_{w}s",
                f"np_vol_price_sync_{w}s",
                f"np_ticks_{w}s",
            ]
        )
    return tuple(keys)


def make_logger_row_id(*, symbol: str, accepted_at: str, entry_time: str) -> str:
    return f"{symbol}|{accepted_at or entry_time}"


def compute_np_pre_entry_predictor_row(
    *,
    trade: Mapping[str, Any],
    board_ring: Sequence[BoardSnap],
    accepted_at_ts: float,
    accepted_at_iso: str,
) -> dict[str, Any]:
    """Build 1 compact predictor row. Uses only snaps with ts <= accepted_at_ts."""
    symbol = str(trade.get("symbol") or "")
    entry_time = str(trade.get("entry_time") or trade.get("accepted_at") or accepted_at_iso)
    row_id = make_logger_row_id(symbol=symbol, accepted_at=accepted_at_iso, entry_time=entry_time)

    # Hard filter: drop any post-accept ticks (defensive).
    safe_ring = [s for s in board_ring if s[0] <= accepted_at_ts]
    max_src = max((s[0] for s in safe_ring), default=None)
    future_leak = bool(max_src is not None and max_src > accepted_at_ts + 1e-9)

    feats: dict[str, Any] = {}
    complete_windows = 0
    for w in WINDOWS_SEC:
        snaps = _window_snaps(safe_ring, accepted_at=accepted_at_ts, window_sec=float(w))
        part = _window_features(snaps, window_sec=w)
        feats.update(part)
        if int(part.get(f"np_ticks_{w}s") or 0) >= MIN_TICKS_FOR_OK:
            complete_windows += 1

    feature_complete = complete_windows >= 3 and not future_leak
    row = {
        "np_logger_row_id": row_id,
        "np_logger_ok": True,
        "np_feature_complete": feature_complete,
        "np_accepted_at": accepted_at_iso,
        "np_max_source_ts": max_src,
        "np_future_leakage": future_leak,
        "np_entry_live_computable": True,
        "symbol": symbol,
        "day": str(trade.get("day") or ""),
        "session": str(trade.get("session") or trade.get("session_kind") or ""),
        "entry_time": entry_time,
        "accepted_at": accepted_at_iso,
        "entry_pool": str(trade.get("entry_pool") or trade.get("entry_pool_selected") or "PBV2"),
        "position_id": str(trade.get("position_id") or ""),
        **feats,
    }
    # Enforce predictor/outcome separation.
    for k in list(row.keys()):
        if is_leaky_predictor_key(k) and k not in PREDICTOR_META_KEYS:
            raise ValueError(f"leaky predictor key forbidden: {k}")
    return row


def build_np_pre_entry_outcome_row(
    *,
    predictor_row: Mapping[str, Any],
    exit_row: Mapping[str, Any],
) -> dict[str, Any]:
    pnl = _float(exit_row.get("pnl_yen_100")) or _float(exit_row.get("actual_pnl_yen_100")) or 0.0
    exit_reason = str(exit_row.get("exit_reason") or "")
    return {
        "np_logger_row_id": predictor_row.get("np_logger_row_id"),
        "symbol": predictor_row.get("symbol") or exit_row.get("symbol"),
        "day": predictor_row.get("day") or exit_row.get("day"),
        "session": predictor_row.get("session") or exit_row.get("session"),
        "entry_time": predictor_row.get("entry_time") or exit_row.get("entry_time"),
        "accepted_at": predictor_row.get("accepted_at"),
        "exit_reason": exit_reason,
        "hold_sec": _float(exit_row.get("hold_sec")),
        "pnl_yen_100": pnl,
        "pnl_pct": _float(exit_row.get("pnl_pct")),
        "is_no_progress_exit": exit_reason == "no_progress_exit",
        "is_stop_hit": exit_reason == "stop_hit",
        "is_winner": pnl > 0,
        "is_loser": pnl < 0,
        "is_big_winner": pnl >= 5000.0,
        "source": "outcome_label",
    }


@dataclass
class NpPreEntryFeatureLoggerCounters:
    accept_count: int = 0
    feature_complete_count: int = 0
    outcome_count: int = 0
    leakage_blocked_count: int = 0
    pending_by_row_id: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record_accept(self, row: Mapping[str, Any]) -> None:
        self.accept_count += 1
        if row.get("np_feature_complete"):
            self.feature_complete_count += 1
        if row.get("np_future_leakage"):
            self.leakage_blocked_count += 1
        rid = str(row.get("np_logger_row_id") or "")
        if rid:
            self.pending_by_row_id[rid] = dict(row)

    def record_exit(self, exit_row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        symbol = str(exit_row.get("symbol") or "")
        entry_time = str(exit_row.get("entry_time") or "")
        accepted_at = str(exit_row.get("accepted_at") or exit_row.get("accepted_event_time") or "")
        rid = str(exit_row.get("np_logger_row_id") or "")
        if not rid:
            rid = make_logger_row_id(symbol=symbol, accepted_at=accepted_at, entry_time=entry_time)
        pred = self.pending_by_row_id.pop(rid, None)
        if pred is None:
            # Fallback: match by symbol + entry_time
            for k, v in list(self.pending_by_row_id.items()):
                if str(v.get("symbol")) == symbol and str(v.get("entry_time")) == entry_time:
                    pred = self.pending_by_row_id.pop(k)
                    break
        if pred is None:
            return None
        self.outcome_count += 1
        return build_np_pre_entry_outcome_row(predictor_row=pred, exit_row=exit_row)

    def summary_fields(self) -> dict[str, Any]:
        return {
            "np_pre_entry_feature_logger_enabled": True,
            "np_pre_entry_logger_accept_count": self.accept_count,
            "np_pre_entry_logger_feature_complete_count": self.feature_complete_count,
            "np_pre_entry_logger_outcome_count": self.outcome_count,
            "np_pre_entry_logger_leakage_blocked_count": self.leakage_blocked_count,
            "np_pre_entry_collection_gate": "DATA_COLLECTION_ONLY",
            "np_pre_entry_rule_discovery_allowed": False,
        }


def build_np_pre_entry_feature_logger_counters(
    config: Any,
) -> Optional[NpPreEntryFeatureLoggerCounters]:
    if not np_pre_entry_feature_logger_enabled(config):
        return None
    return NpPreEntryFeatureLoggerCounters()
