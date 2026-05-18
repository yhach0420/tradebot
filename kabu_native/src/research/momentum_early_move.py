"""
Phase 26: Early Adverse Move (EAM) path tracking and winner/loser analysis.

Fixed post-entry horizons (global, not per-symbol/day/time).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

EARLY_HORIZONS_SEC: tuple[int, ...] = (15, 30, 60, 90, 180)

# First-move detection thresholds (% from entry)
ADV_FIRST_PCT = -0.05
FAV_FIRST_PCT = 0.05
RECOVER_FROM_ADV_PCT = 0.02


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _vwap_distance_pct(price: float, vwap: Optional[float]) -> Optional[float]:
    if vwap is None or vwap <= 0:
        return None
    return _pct_change(price, vwap)


@dataclass
class EarlyMoveRuntime:
    """Incremental early-path state while position is open."""

    entry_price: float
    entry_ts_sec: float
    entry_imbalance: Optional[float] = None
    entry_vwap_dist_pct: Optional[float] = None
    entry_momentum_pct: Optional[float] = None
    entry_minute_tv: Optional[float] = None
    max_favorable_pct: float = 0.0
    max_adverse_pct: float = 0.0
    adverse_first_sec: Optional[float] = None
    favorable_first_sec: Optional[float] = None
    had_adverse_flush: bool = False
    recovered_after_adverse: bool = False
    recovery_hold_events: int = 0
    early_cut_exited: bool = False
    recovery_or_cut_evaluated: bool = False
    recovery_or_cut_held: bool = False
    _horizons_done: set[int] = field(default_factory=set)
    snapshots: dict[int, dict[str, Any]] = field(default_factory=dict)
    _last_imb: Optional[float] = None
    _last_vwap_dist: Optional[float] = None
    _last_momentum_pct: Optional[float] = None
    _last_minute_tv: Optional[float] = None

    def update(
        self,
        *,
        ts_sec: float,
        price: float,
        board_imbalance: Optional[float],
        vwap: Optional[float],
        volume_delta_30s: Optional[float],
        minute_trading_value: Optional[float],
    ) -> None:
        fav = _pct_change(price, self.entry_price)
        self.max_favorable_pct = max(self.max_favorable_pct, fav)
        self.max_adverse_pct = min(self.max_adverse_pct, fav)
        elapsed = ts_sec - self.entry_ts_sec

        if fav <= ADV_FIRST_PCT and self.adverse_first_sec is None:
            self.adverse_first_sec = elapsed
            self.had_adverse_flush = True
        if fav >= FAV_FIRST_PCT and self.favorable_first_sec is None:
            self.favorable_first_sec = elapsed

        if self.had_adverse_flush and fav >= RECOVER_FROM_ADV_PCT:
            self.recovered_after_adverse = True

        vwap_dist = _vwap_distance_pct(price, vwap)
        mom = None
        if self.entry_momentum_pct is not None:
            mom = fav
        elif self._last_momentum_pct is not None:
            mom = fav

        self._last_imb = board_imbalance
        self._last_vwap_dist = vwap_dist
        self._last_momentum_pct = mom
        if minute_trading_value is not None:
            self._last_minute_tv = minute_trading_value

        imb_chg = None
        if self.entry_imbalance is not None and board_imbalance is not None:
            imb_chg = float(board_imbalance) - float(self.entry_imbalance)

        vwap_chg = None
        if self.entry_vwap_dist_pct is not None and vwap_dist is not None:
            vwap_chg = vwap_dist - float(self.entry_vwap_dist_pct)

        tv_chg = None
        if self.entry_minute_tv and minute_trading_value:
            tv_chg = (minute_trading_value - self.entry_minute_tv) / self.entry_minute_tv

        for h in EARLY_HORIZONS_SEC:
            if h in self._horizons_done or elapsed < h:
                continue
            self._horizons_done.add(h)
            self.snapshots[h] = {
                "elapsed_sec": h,
                "max_adverse_pct": self.max_adverse_pct,
                "max_favorable_pct": self.max_favorable_pct,
                "price_pct_from_entry": fav,
                "board_imbalance": board_imbalance,
                "board_imbalance_change": imb_chg,
                "vwap_distance_pct": vwap_dist,
                "vwap_distance_change": vwap_chg,
                "momentum_pct_from_entry": fav,
                "minute_trading_value": minute_trading_value,
                "minute_tv_change_ratio": tv_chg,
                "volume_delta_30s": volume_delta_30s,
            }

    def current_momentum_pct(self, price: float) -> float:
        return _pct_change(price, self.entry_price)

    def current_vwap_change(self, vwap_dist: Optional[float]) -> Optional[float]:
        if self.entry_vwap_dist_pct is not None and vwap_dist is not None:
            return vwap_dist - float(self.entry_vwap_dist_pct)
        return None

    def snapshot_60s(self) -> Optional[dict[str, Any]]:
        return self.snapshots.get(60)

    def finalize(self) -> dict[str, Any]:
        flat: dict[str, Any] = {
            "adverse_first_sec": self.adverse_first_sec,
            "favorable_first_sec": self.favorable_first_sec,
            "had_adverse_flush": self.had_adverse_flush,
            "recovered_after_adverse": self.recovered_after_adverse,
            "max_adverse_pct_early": self.max_adverse_pct,
            "max_favorable_pct_early": self.max_favorable_pct,
            "recovery_hold_count": self.recovery_hold_events,
            "early_cut_count": 1 if self.early_cut_exited else 0,
            "recovery_or_cut_held": self.recovery_or_cut_held,
        }
        for h, snap in self.snapshots.items():
            for k, v in snap.items():
                flat[f"early_{h}s_{k}"] = v
        return flat

    @classmethod
    def from_entry(
        cls,
        *,
        entry_price: float,
        entry_ts_sec: float,
        entry_snap: Mapping[str, Any],
    ) -> "EarlyMoveRuntime":
        return cls(
            entry_price=entry_price,
            entry_ts_sec=entry_ts_sec,
            entry_imbalance=_as_float(entry_snap.get("board_imbalance_entry")),
            entry_vwap_dist_pct=_as_float(entry_snap.get("vwap_distance_pct")),
            entry_momentum_pct=_as_float(entry_snap.get("price_momentum_pct")),
            entry_minute_tv=_as_float(entry_snap.get("minute_trading_value")),
        )


def _dist(vals: Sequence[float]) -> dict[str, Any]:
    if not vals:
        return {"count": 0, "p50": None, "mean": None}
    s = sorted(vals)
    return {"count": len(s), "p50": statistics.median(s), "mean": statistics.mean(s)}


def _horizon_compare(
    rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    winners = [r for r in rows if float(r["pnl_pct"]) > 0]
    losers = [r for r in rows if float(r["pnl_pct"]) < 0]

    def _slice(grp: Sequence[Mapping[str, Any]], h: int, field: str) -> list[float]:
        out: list[float] = []
        key = f"early_{h}s_{field}"
        for r in grp:
            v = r.get(key)
            if v is not None:
                out.append(float(v))
        return out

    horizons: dict[str, Any] = {}
    for h in EARLY_HORIZONS_SEC:
        horizons[f"{h}s"] = {
            "winners": {
                "max_adverse_pct": _dist(_slice(winners, h, "max_adverse_pct")),
                "max_favorable_pct": _dist(_slice(winners, h, "max_favorable_pct")),
                "board_imbalance_change": _dist(_slice(winners, h, "board_imbalance_change")),
                "vwap_distance_change": _dist(_slice(winners, h, "vwap_distance_change")),
                "momentum_pct_from_entry": _dist(_slice(winners, h, "momentum_pct_from_entry")),
                "minute_tv_change_ratio": _dist(_slice(winners, h, "minute_tv_change_ratio")),
            },
            "losers": {
                "max_adverse_pct": _dist(_slice(losers, h, "max_adverse_pct")),
                "max_favorable_pct": _dist(_slice(losers, h, "max_favorable_pct")),
                "board_imbalance_change": _dist(_slice(losers, h, "board_imbalance_change")),
                "vwap_distance_change": _dist(_slice(losers, h, "vwap_distance_change")),
                "momentum_pct_from_entry": _dist(_slice(losers, h, "momentum_pct_from_entry")),
                "minute_tv_change_ratio": _dist(_slice(losers, h, "minute_tv_change_ratio")),
            },
        }

    def _rate(grp: Sequence[Mapping[str, Any]], attr: str) -> Optional[float]:
        if not grp:
            return None
        return sum(1 for r in grp if r.get(attr)) / len(grp)

    return {
        "label": label,
        "trade_count": len(rows),
        "winners": len(winners),
        "losers": len(losers),
        "adverse_first_rate": _rate(rows, "adverse_first_sec"),
        "favorable_first_rate": _rate(rows, "favorable_first_sec"),
        "adverse_then_recover_rate": _rate(rows, "recovered_after_adverse"),
        "early_flush_recovery_rate": _rate(
            [r for r in rows if r.get("had_adverse_flush")], "recovered_after_adverse"
        ),
        "horizons": horizons,
    }


def build_early_move_analysis(trade_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [r for r in trade_rows if r.get("early_60s_max_adverse_pct") is not None or r.get("adverse_first_sec") is not None]
    if not rows and trade_rows:
        rows = list(trade_rows)
    overall = _horizon_compare(rows, label="all")
    mae_before_mfe = sum(
        1
        for r in rows
        if float(r.get("mae_pct", 0)) < -0.08
        and float(r.get("mfe_pct", 0)) < 0.1
        and float(r.get("pnl_pct", 0)) < 0
    )
    losers_n = sum(1 for r in rows if float(r["pnl_pct"]) < 0)
    notes: list[str] = []
    if overall.get("adverse_first_rate") and overall["adverse_first_rate"] > 0.5:
        notes.append("losers_likely_early_adverse_type")
    lw = _horizon_compare([r for r in rows if float(r["pnl_pct"]) < 0], label="losers_only")
    wn = _horizon_compare([r for r in rows if float(r["pnl_pct"]) > 0], label="winners_only")
    if lw.get("horizons", {}).get("60s", {}).get("losers", {}).get("max_adverse_pct", {}).get("p50"):
        ladv = lw["horizons"]["60s"]["losers"]["max_adverse_pct"]["p50"]
        wadv = wn.get("horizons", {}).get("60s", {}).get("winners", {}).get("max_adverse_pct", {}).get("p50")
        if ladv is not None and wadv is not None and ladv < wadv - 0.05:
            notes.append("60s_adverse_separates_losers_from_winners")
    if losers_n and mae_before_mfe / losers_n > 0.4:
        notes.append("mae_before_mfe_suggests_entry_and_early_exit_both_relevant")
    imb_exits = [r for r in rows if str(r.get("exit_reason")) == "board_imbalance_deterioration"]
    if imb_exits:
        imb_adv = statistics.mean(
            float(r.get("early_60s_max_adverse_pct") or r.get("max_adverse_pct_early") or 0)
            for r in imb_exits
        )
        notes.append(f"imbalance_exit_avg_60s_adverse={imb_adv:.4f}")
        if imb_adv > -0.08:
            notes.append("imbalance_exit_often_exit_timing_not_entry_only")
    return {
        "phase": 26,
        "trade_count": len(rows),
        "overall": overall,
        "winners_only": wn,
        "losers_only": lw,
        "mae_before_mfe_count": mae_before_mfe,
        "mae_before_mfe_rate": (mae_before_mfe / losers_n) if losers_n else None,
        "diagnosis_notes": notes,
        "hypothesis": (
            "ENTRY時点ではwinner/loser判別が難しいが、ENTRY直後の逆行・板悪化に差がある。"
            "ENTRY後監視（early protection）で損失を抑えられるか検証する。"
        ),
    }
