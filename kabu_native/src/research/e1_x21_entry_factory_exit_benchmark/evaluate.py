"""Directional evaluation, benchmark EXIT economics, rankings (vectorized)."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Optional

import numpy as np

from . import BENCHMARK_EXITS, DISCOVERY, EVALUATION, STRESS_DAY


def _mean(xs: list[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


class PopArrays:
    """Precomputed columns for fast mask evaluation."""

    def __init__(self, rows: list[dict[str, Any]]):
        self.n = len(rows)
        self.dates = np.array([r["date"] for r in rows])
        self.symbols = np.array([r["symbol"] for r in rows])
        self.classes = np.array([r.get("outcome_class") or "UNCLASSIFIED" for r in rows])
        self.px = np.array([
            float(r["CurrentPrice"]) if r.get("CurrentPrice") is not None else np.nan
            for r in rows
        ], dtype=float)
        self.epoch = np.array([r.get("grid_epoch") or 0 for r in rows], dtype=float)
        self.cluster = np.array([str(r.get("cluster_id") or i) for i, r in enumerate(rows)])

        def col(key: str) -> np.ndarray:
            return np.array([
                float(r[key]) if r.get(key) is not None else np.nan for r in rows
            ], dtype=float)

        self.fr30 = col("forward_return_30s")
        self.fr60 = col("forward_return_60s")
        self.fr180 = col("forward_return_180s")
        self.fr300 = col("forward_return_300s")
        self.mfe60 = col("MFE_60s")
        self.mae60 = col("MAE_60s")
        self.mfe180 = col("MFE_180s")
        self.mae180 = col("MAE_180s")
        self.mfe300 = col("MFE_300s")
        self.mae300 = col("MAE_300s")
        self.p5 = col("plus5_before_minus5")
        self.p10 = col("plus10_before_minus10")
        self.ttp10 = col("time_to_plus10")

        self.ret: dict[str, np.ndarray] = {}
        self.hold: dict[str, np.ndarray] = {}
        self.reason: dict[str, np.ndarray] = {}

        self.ret["BX_H60"] = self.fr60.copy()
        self.hold["BX_H60"] = np.full(self.n, 60.0)
        self.reason["BX_H60"] = np.full(self.n, "horizon_60s", dtype=object)

        self.ret["BX_H180"] = self.fr180.copy()
        self.hold["BX_H180"] = np.full(self.n, 180.0)
        self.reason["BX_H180"] = np.full(self.n, "horizon_180s", dtype=object)

        self.ret["BX_H300"] = self.fr300.copy()
        self.hold["BX_H300"] = np.full(self.n, 300.0)
        self.reason["BX_H300"] = np.full(self.n, "horizon_300s", dtype=object)

        t_ret = np.full(self.n, np.nan)
        t_hold = np.full(self.n, 300.0)
        t_reason = np.full(self.n, "horizon_300s_fallback", dtype=object)
        win = np.isfinite(self.p10) & (self.p10 == 1.0)
        lose = np.isfinite(self.p10) & (self.p10 == 0.0)
        t_ret[win] = 0.0010
        t_ret[lose] = -0.0010
        t_hold[win] = np.where(np.isfinite(self.ttp10[win]), self.ttp10[win], 300.0)
        t_reason[win] = "touch_plus10"
        t_reason[lose] = "touch_minus10"
        neither = ~(win | lose) & np.isfinite(self.fr300)
        t_ret[neither] = self.fr300[neither]
        t_reason[neither] = "horizon_300s_fallback"
        self.ret["BX_TOUCH_10_10"] = t_ret
        self.hold["BX_TOUCH_10_10"] = t_hold
        self.reason["BX_TOUCH_10_10"] = t_reason

        self.pnl: dict[str, np.ndarray] = {}
        for eid in BENCHMARK_EXITS:
            self.pnl[eid] = self.px * self.ret[eid] * 100.0

        self.disc_mask = np.isin(self.dates, list(DISCOVERY))
        self.eval_mask = np.isin(self.dates, list(EVALUATION))
        self.stress_mask = self.dates == STRESS_DAY

    def directional(self, mask: np.ndarray) -> dict[str, Any]:
        idx = np.where(mask)[0]
        n = int(idx.size)
        if n == 0:
            return {"support": 0, "retention": 0.0}

        cls = self.classes[idx]
        w = int(np.sum(cls == "WINNER"))
        s = int(np.sum(cls == "STOP"))
        ws = w + s

        def mcol(arr: np.ndarray) -> Optional[float]:
            v = arr[idx]
            v = v[np.isfinite(v)]
            return float(np.mean(v)) if v.size else None

        return {
            "support": n,
            "retention": n / self.n,
            "days": int(len(set(self.dates[idx].tolist()))),
            "symbols": int(len(set(self.symbols[idx].tolist()))),
            "WINNER": w,
            "STOP": s,
            "NOPROGRESS": int(np.sum(cls == "NOPROGRESS")),
            "TWO_SIDED_VOLATILE": int(np.sum(cls == "TWO_SIDED_VOLATILE")),
            "UNCLASSIFIED": int(np.sum(cls == "UNCLASSIFIED")),
            "winner_stop_odds": (w / s) if s else (float("inf") if w else None),
            "stop_share_ws": (s / ws) if ws else None,
            "winner_share_ws": (w / ws) if ws else None,
            "forward_return_30s": mcol(self.fr30),
            "forward_return_60s": mcol(self.fr60),
            "forward_return_180s": mcol(self.fr180),
            "forward_return_300s": mcol(self.fr300),
            "MFE_60s": mcol(self.mfe60), "MAE_60s": mcol(self.mae60),
            "MFE_180s": mcol(self.mfe180), "MAE_180s": mcol(self.mae180),
            "MFE_300s": mcol(self.mfe300), "MAE_300s": mcol(self.mae300),
            "plus5_before_minus5": mcol(self.p5),
            "plus10_before_minus10": mcol(self.p10),
        }

    def period_metrics(self, mask: np.ndarray) -> dict[str, Any]:
        return {
            "DISCOVERY": self.directional(mask & self.disc_mask),
            "EVALUATION": self.directional(mask & self.eval_mask),
            "STRESS_20260803": self.directional(mask & self.stress_mask),
            "ALL": self.directional(mask),
        }

    def pair_economics(
        self, mask: np.ndarray, exit_id: str, *, with_fingerprint: bool = False
    ) -> dict[str, Any]:
        pnl = self.pnl[exit_id]
        ok = mask & np.isfinite(pnl)
        idx = np.where(ok)[0]
        trades = int(idx.size)
        if trades == 0:
            return {
                "exit_id": exit_id, "coverage": "DIRECTIONAL_ONLY", "trades": 0,
                "wins": 0, "losses": 0, "win_rate": None,
                "gross_pnl_yen_100": None, "net_pnl_yen_100": None,
                "profit_factor_yen_100": None, "avg_pnl_yen_100": None,
                "median_pnl_yen_100": None, "best_trade": None, "worst_trade": None,
                "max_drawdown_yen_100": 0.0, "positive_days": 0, "negative_days": 0,
                "median_daily_pnl": None,
                "ledger_fingerprint": hashlib.sha256(b"").hexdigest() if with_fingerprint else None,
            }
        vals = pnl[idx]
        wins = int(np.sum(vals > 0))
        losses = int(np.sum(vals < 0))
        order = np.lexsort((self.epoch[idx], self.dates[idx]))
        ordered = vals[order]
        cum = np.cumsum(ordered)
        peak = np.maximum.accumulate(cum)
        max_dd = float(np.min(cum - peak))
        # vectorized daily sums
        d = self.dates[idx]
        uniq, inv = np.unique(d, return_inverse=True)
        day_totals = np.bincount(inv, weights=vals)
        day_vals = day_totals.tolist()
        gp = float(np.sum(vals[vals > 0])) if wins else 0.0
        gl = float(abs(np.sum(vals[vals < 0]))) if losses else 0.0
        fp = None
        if with_fingerprint:
            parts = [
                f"{self.cluster[i]}|{self.reason[exit_id][i]}|{self.hold[exit_id][i]}|{self.ret[exit_id][i]}"
                for i in idx
            ]
            fp = hashlib.sha256("\n".join(parts).encode()).hexdigest()
        return {
            "exit_id": exit_id,
            "coverage": "DIRECTIONAL_ONLY",
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / trades,
            "gross_pnl_yen_100": float(np.sum(vals)),
            "net_pnl_yen_100": None,
            "profit_factor_yen_100": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else None),
            "avg_pnl_yen_100": float(np.mean(vals)),
            "median_pnl_yen_100": float(np.median(vals)),
            "best_trade": float(np.max(vals)),
            "worst_trade": float(np.min(vals)),
            "max_drawdown_yen_100": max_dd,
            "positive_days": int(np.sum(day_totals > 0)),
            "negative_days": int(np.sum(day_totals < 0)),
            "median_daily_pnl": float(np.median(day_totals)),
            "ledger_fingerprint": fp,
        }

    def all_exit_economics(self, mask: np.ndarray) -> dict[str, dict[str, Any]]:
        return {eid: self.pair_economics(mask, eid, with_fingerprint=False) for eid in BENCHMARK_EXITS}

    def exit_ledger_fingerprints(self) -> dict[str, str]:
        full = np.ones(self.n, dtype=bool)
        return {
            eid: self.pair_economics(full, eid, with_fingerprint=True)["ledger_fingerprint"]
            for eid in BENCHMARK_EXITS
        }


def directional_metrics(rows: list[dict[str, Any]], mask: np.ndarray) -> dict[str, Any]:
    return PopArrays(rows).directional(mask)


def period_metrics(rows: list[dict[str, Any]], mask: np.ndarray) -> dict[str, Any]:
    return PopArrays(rows).period_metrics(mask)


def exit_ledger_row(r: dict[str, Any], exit_id: str) -> dict[str, Any]:
    px = r.get("CurrentPrice")
    if exit_id == "BX_H60":
        ret = float(r["forward_return_60s"]) if r.get("forward_return_60s") is not None else None
        hold, reason = 60.0, "horizon_60s"
    elif exit_id == "BX_H180":
        ret = float(r["forward_return_180s"]) if r.get("forward_return_180s") is not None else None
        hold, reason = 180.0, "horizon_180s"
    elif exit_id == "BX_H300":
        ret = float(r["forward_return_300s"]) if r.get("forward_return_300s") is not None else None
        hold, reason = 300.0, "horizon_300s"
    else:
        p10 = r.get("plus10_before_minus10")
        if p10 is not None and float(p10) == 1.0:
            ret = 0.0010
            hold = float(r["time_to_plus10"]) if r.get("time_to_plus10") is not None else 300.0
            reason = "touch_plus10"
        elif p10 is not None and float(p10) == 0.0:
            ret = -0.0010
            hold, reason = 300.0, "touch_minus10"
        elif r.get("forward_return_300s") is not None:
            ret = float(r["forward_return_300s"])
            hold, reason = 300.0, "horizon_300s_fallback"
        else:
            ret, hold, reason = None, 300.0, "horizon_300s_fallback"
    exit_px = (float(px) * (1.0 + ret)) if (px is not None and ret is not None) else None
    return {
        "exit_id": exit_id,
        "exit_time_offset_sec": hold,
        "exit_price": exit_px,
        "exit_reason": reason,
        "hold_sec": hold,
        "signal_price": px,
        "entry_ask": None,
        "exit_bid": None,
        "coverage": "DIRECTIONAL_ONLY",
        "gross_return": ret,
        "gross_pnl_yen_100": (float(px) * ret * 100.0) if (px is not None and ret is not None) else None,
        "net_pnl_yen_100": None,
        "spread_cost": None,
    }


def pair_economics(rows: list[dict[str, Any]], mask: np.ndarray, exit_id: str) -> dict[str, Any]:
    return PopArrays(rows).pair_economics(mask, exit_id)


def exit_sensitivity(pair_econ: dict[str, dict[str, Any]]) -> str:
    signs = {}
    for eid, e in pair_econ.items():
        g = e.get("gross_pnl_yen_100")
        if g is None:
            continue
        signs[eid] = 1 if g > 0 else (-1 if g < 0 else 0)
    if not signs:
        return "ENTRY_PATH_WEAK"
    vals = list(signs.values())
    if all(v > 0 for v in vals):
        return "ENTRY_PATH_POSITIVE"
    if all(v <= 0 for v in vals):
        return "ENTRY_PATH_WEAK"
    h60 = signs.get("BX_H60", 0)
    h300 = signs.get("BX_H300", 0)
    touch = signs.get("BX_TOUCH_10_10", 0)
    if h60 > 0 and h300 <= 0:
        return "SHORT_HORIZON_ONLY"
    if h300 > 0 and h60 <= 0:
        return "LONG_HORIZON_ONLY"
    if touch > 0 and h60 <= 0 and h300 <= 0:
        return "TOUCH_EXIT_SENSITIVE"
    return "EXIT_SENSITIVE_MIXED"


def assign_status(dir_all: dict[str, Any], sens: str, base_odds: Optional[float]) -> str:
    odds = dir_all.get("winner_stop_odds")
    fr = dir_all.get("forward_return_180s")
    economic_like = sens in ("ENTRY_PATH_POSITIVE", "SHORT_HORIZON_ONLY", "LONG_HORIZON_ONLY")
    directional = False
    if odds is not None and base_odds is not None and np.isfinite(odds) and np.isfinite(base_odds):
        if odds > base_odds * 1.05:
            directional = True
    if fr is not None and fr > 0:
        directional = True
    if economic_like and directional:
        return "BENCHMARK_ECONOMIC_PROMISING"
    if directional:
        return "DIRECTIONAL_PROMISING"
    if sens == "EXIT_SENSITIVE_MIXED":
        return "EXIT_SENSITIVE_MIXED"
    if dir_all.get("support", 0) < 30:
        return "EXPERIMENTAL_CREATED"
    return "EXPERIMENTAL_WEAK"


def canonical_exit_identity() -> dict[str, Any]:
    return {
        "implementation_path": "src/small_paper/structural_exit_policies.py + observer_position_tracker.py",
        "function_class": "simulate_structural_policy / ObserverPositionTracker",
        "config_source": "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
        "enabled_exit_reasons": [
            "stop_hit", "no_progress_exit", "trailing_mfe_exit",
            "morning_session_close", "afternoon_session_close",
        ],
        "state_transitions": "stop → NoProgress → trailing_mfe → session_close",
        "parity_status": "CANONICAL_EXIT_PARITY_NOT_ESTABLISHED",
        "reason": (
            "X19 population lacks board/bid-ask and full observer tick replay; "
            "BX_CANONICAL_PAPER not added to benchmark set"
        ),
        "BX_CANONICAL_PAPER_included": False,
    }
