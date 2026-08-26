"""Incremental X14 10s T1 + C1. Same as-of rules as P2-1. No ENTRY."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.dynamic_anchor_p2_0b import CONFIRMED, SESSION_INCOMPLETE
from research.dynamic_anchor_p2_0b.contract import SymbolMachine, t1_raw
from research.dynamic_anchor_p2_1 import CAPTURE_BOUNDARY_INCOMPLETE
from research.dynamic_anchor_p2_1.capture_ticks import _f, _ts
from research.dynamic_anchor_p2_1.engine import confirm_c1
from research.e1_x14_board_independent_signal import (
    AM_END,
    PM_START,
    PRICE_FRESH_MAX,
    VALUE_FRESH_MAX,
    VOLUME_FRESH_MAX,
    VWAP_FRESH_MAX,
)
from research.e1_x14_board_independent_signal.features import attach_relative_strength
from research.e1_x14_board_independent_signal.grid import _empty_grid, _hm, session_grid_times
from small_paper.v1r_live_dual_lane import canonical_symbol_key


def tick_from_payload(
    et: float, pay: dict[str, Any], prev_vol: Optional[float]
) -> tuple[Optional[dict[str, Any]], Optional[float]]:
    px = _f(pay.get("CurrentPrice"))
    if px is None or px <= 0:
        return None, prev_vol
    vol = _f(pay.get("TradingVolume"))
    val = _f(pay.get("TradingValue"))
    vwap = _f(pay.get("VWAP"))
    px_t = _ts(pay.get("CurrentPriceTime")) or et
    vol_t = _ts(pay.get("TradingVolumeTime")) or et
    val_t = _ts(pay.get("TradingValueTime")) or et
    vol_reset = bool(vol is not None and prev_vol is not None and vol + 1e-9 < prev_vol)
    nv = vol if vol is not None else prev_vol
    return {
        "t": et,
        "price": px,
        "vol": vol,
        "value": val,
        "vwap": vwap,
        "price_t": px_t,
        "vol_t": vol_t,
        "value_t": val_t,
        "vwap_t": et,
        "vol_reset": vol_reset,
    }, nv


def _asof(arr: np.ndarray, target: float) -> int:
    return int(np.searchsorted(arr, target, side="right") - 1)


def _t1_path_fields(
    row: dict[str, Any],
    times: np.ndarray,
    prices: np.ndarray,
    vols: np.ndarray,
    *,
    pm_start: float,
) -> None:
    """return_60s + volume_delta_60s — same as-of formulas as attach_path_volume_features."""
    if row.get("quality_status") != "OK" or row.get("CurrentPrice") is None:
        row["feature_status"] = "FEATURE_NOT_EVALUABLE"
        return
    g = float(row["grid_epoch"])
    sess = row["session"]
    px = float(row["CurrentPrice"])

    def px_at_lag(sec: int) -> Optional[float]:
        i = _asof(times, g - sec)
        if i < 0:
            return None
        if sess == "PM" and float(times[i]) < pm_start:
            return None
        return float(prices[i])

    def vol_at_lag(sec: int) -> Optional[float]:
        i = _asof(times, g - sec)
        if i < 0 or not np.isfinite(vols[i]):
            return None
        if sess == "PM" and float(times[i]) < pm_start:
            return None
        return float(vols[i])

    p60 = px_at_lag(60)
    row["return_60s"] = ((px / p60) - 1.0) if (p60 is not None and p60 > 0) else None
    v0 = row.get("TradingVolume")
    v1 = vol_at_lag(60)
    if v0 is None or v1 is None or float(v0) + 1e-9 < float(v1):
        row["volume_delta_60s"] = None
    else:
        row["volume_delta_60s"] = float(v0) - float(v1)
    row["feature_status"] = "OK"


class IncrementalT1:
    """Causal T1/C1 on the same event watermark as Current ingest."""

    def __init__(self, *, day: str, universe: list[str], capture_class: str) -> None:
        self.day = day
        self.capture_class = capture_class
        self.universe = [canonical_symbol_key(s) for s in universe]
        self.src = f"capture_{day}"
        self.grids_spec = session_grid_times(day)
        self.grid_i = 0
        self.ticks: dict[str, list[dict[str, Any]]] = {s: [] for s in self.universe}
        self.prev_vol: dict[str, Optional[float]] = {s: None for s in self.universe}
        self.t_arr: dict[str, np.ndarray] = {s: np.zeros(0, dtype=float) for s in self.universe}
        self.px_arr: dict[str, np.ndarray] = {s: np.zeros(0, dtype=float) for s in self.universe}
        self.vol_arr: dict[str, np.ndarray] = {s: np.zeros(0, dtype=float) for s in self.universe}
        self.pt_arr: dict[str, np.ndarray] = {s: np.zeros(0, dtype=float) for s in self.universe}
        self.vt_arr: dict[str, np.ndarray] = {s: np.zeros(0, dtype=float) for s in self.universe}
        self.val_arr: dict[str, np.ndarray] = {s: np.zeros(0, dtype=float) for s in self.universe}
        self.valt_arr: dict[str, np.ndarray] = {s: np.zeros(0, dtype=float) for s in self.universe}
        self.vwap_arr: dict[str, np.ndarray] = {s: np.zeros(0, dtype=float) for s in self.universe}
        self.vwapt_arr: dict[str, np.ndarray] = {s: np.zeros(0, dtype=float) for s in self.universe}
        self.synced: dict[str, int] = {s: 0 for s in self.universe}
        self.rows: dict[str, list[dict[str, Any]]] = {s: [] for s in self.universe}
        self.machines: dict[str, dict[str, SymbolMachine]] = {
            s: {"AM": SymbolMachine(s), "PM": SymbolMachine(s)} for s in self.universe
        }
        self.sess_idx: dict[str, dict[str, int]] = {s: {"AM": 0, "PM": 0} for s in self.universe}
        self.triggers: list[dict[str, Any]] = []
        self.confirms: list[dict[str, Any]] = []
        self.pending_confirmed: list[dict[str, Any]] = []
        self.persist_refire = 0
        self.grid_n = 0
        self.eval_n = 0
        self.raw_true_n = 0
        self.last_capture_t: Optional[float] = None
        self.checkpoint_future_leak = 0
        self.decision_snapshot_future_leak = 0
        self.am_end = _hm(day, AM_END).timestamp()
        self.pm_start = _hm(day, PM_START).timestamp()

    def note_watermark(self, et: float) -> None:
        self.last_capture_t = et if self.last_capture_t is None else max(self.last_capture_t, et)

    def append_tick(self, *, symbol: str, et: float, pay: dict[str, Any]) -> None:
        s = canonical_symbol_key(symbol)
        if s not in self.ticks:
            return
        row, pv = tick_from_payload(et, pay, self.prev_vol[s])
        self.prev_vol[s] = pv
        if row is not None:
            self.ticks[s].append(row)

    def _sync(self, s: str) -> None:
        n = len(self.ticks[s])
        k = self.synced[s]
        if n <= k:
            return
        extra = self.ticks[s][k:]

        def cat(old: np.ndarray, vals: list[float]) -> np.ndarray:
            a = np.asarray(vals, dtype=float)
            return a if old.size == 0 else np.concatenate([old, a])

        self.t_arr[s] = cat(self.t_arr[s], [x["t"] for x in extra])
        self.px_arr[s] = cat(self.px_arr[s], [x["price"] for x in extra])
        self.vol_arr[s] = cat(self.vol_arr[s], [np.nan if x["vol"] is None else float(x["vol"]) for x in extra])
        self.pt_arr[s] = cat(self.pt_arr[s], [x["price_t"] for x in extra])
        self.vt_arr[s] = cat(self.vt_arr[s], [x["vol_t"] for x in extra])
        self.val_arr[s] = cat(self.val_arr[s], [np.nan if x["value"] is None else float(x["value"]) for x in extra])
        self.valt_arr[s] = cat(self.valt_arr[s], [x["value_t"] for x in extra])
        self.vwap_arr[s] = cat(self.vwap_arr[s], [np.nan if x["vwap"] is None else float(x["vwap"]) for x in extra])
        self.vwapt_arr[s] = cat(self.vwapt_arr[s], [x["vwap_t"] for x in extra])
        self.synced[s] = n

    def evaluate_grids_until(self, watermark: float) -> None:
        while self.grid_i < len(self.grids_spec):
            sess, gt = self.grids_spec[self.grid_i]
            g = gt.timestamp()
            if g + 1e-12 >= float(watermark):
                break
            self._eval_one_grid(sess, gt)
            self.grid_i += 1

    def finalize(self, last_et: Optional[float]) -> None:
        while self.grid_i < len(self.grids_spec):
            sess, gt = self.grids_spec[self.grid_i]
            self._eval_one_grid(sess, gt)
            self.grid_i += 1
        last = last_et if last_et is not None else self.last_capture_t
        for s in self.universe:
            for sess, sm in self.machines[s].items():
                if sm.state == "ANCHOR_ACTIVE" and sm.active is not None:
                    self._finish_active(s, sess, sm, last)

    def _grid_row(self, s: str, sess: str, gt) -> dict[str, Any]:
        g = gt.timestamp()
        self._sync(s)
        times = self.t_arr[s]
        if times.size == 0:
            return _empty_grid(self.day, sess, gt, s, self.src, "NO_PRIOR_TICK")
        i = _asof(times, g)
        if i < 0:
            return _empty_grid(self.day, sess, gt, s, self.src, "NO_PRIOR_TICK")
        if sess == "PM" and float(times[i]) < self.pm_start:
            return _empty_grid(self.day, sess, gt, s, self.src, "SESSION_CROSS_FILL_BLOCKED")
        if sess == "AM" and float(times[i]) > self.am_end + 60:
            return _empty_grid(self.day, sess, gt, s, self.src, "SESSION_MISMATCH")
        px_age = g - float(self.pt_arr[s][i])
        vol_ok = np.isfinite(self.vol_arr[s][i])
        val_ok = np.isfinite(self.val_arr[s][i])
        vwap_ok = np.isfinite(self.vwap_arr[s][i])
        vol_age = g - float(self.vt_arr[s][i]) if vol_ok else None
        val_age = g - float(self.valt_arr[s][i]) if val_ok else None
        vwap_age = g - float(self.vwapt_arr[s][i]) if vwap_ok else None
        reasons = []
        ok = True
        if px_age > PRICE_FRESH_MAX:
            reasons.append("PRICE_STALE")
            ok = False
        if vol_age is None or vol_age > VOLUME_FRESH_MAX:
            reasons.append("VOLUME_STALE_OR_MISSING")
            ok = False
        if val_age is None or val_age > VALUE_FRESH_MAX:
            reasons.append("VALUE_STALE_OR_MISSING")
            ok = False
        if vwap_age is None or vwap_age > VWAP_FRESH_MAX:
            reasons.append("VWAP_STALE_OR_MISSING")
            ok = False
        return {
            "date": self.day,
            "session": sess,
            "grid_time": gt.isoformat(),
            "grid_epoch": g,
            "symbol": s,
            "CurrentPrice": float(self.px_arr[s][i]),
            "TradingVolume": None if not vol_ok else float(self.vol_arr[s][i]),
            "TradingValue": None if not val_ok else float(self.val_arr[s][i]),
            "VWAP": None if not vwap_ok else float(self.vwap_arr[s][i]),
            "price_age_sec": px_age,
            "volume_age_sec": vol_age,
            "value_age_sec": val_age,
            "vwap_age_sec": vwap_age,
            "source_identity": self.src,
            "quality_status": "OK" if ok else "FEATURE_NOT_EVALUABLE",
            "quality_reasons": reasons,
            "vol_reset_flag": False,
            "_tick_idx": i,
            "source_tick_t": float(times[i]),
        }

    def _eval_one_grid(self, sess: str, gt) -> None:
        g = gt.timestamp()
        epoch_rows: dict[str, list[dict[str, Any]]] = {}
        for s in self.universe:
            row = self._grid_row(s, sess, gt)
            self.rows[s].append(row)
            if self.t_arr[s].size:
                _t1_path_fields(row, self.t_arr[s], self.px_arr[s], self.vol_arr[s], pm_start=self.pm_start)
            elif "feature_status" not in row:
                row["feature_status"] = "FEATURE_NOT_EVALUABLE"
            epoch_rows[s] = [row]
        attach_relative_strength(epoch_rows)
        for s in self.universe:
            r = self.rows[s][-1]
            self.grid_n += 1
            idx = self.sess_idx[s][sess]
            sm = self.machines[s][sess]
            if sm.state == "ANCHOR_ACTIVE" and sm.active is not None and g + 1e-12 >= float(sm.active.t1):
                self._finish_active(s, sess, sm, self.last_capture_t)
            prev_raw = sm.prev_raw
            raw = t1_raw(r)
            if (
                r.get("feature_status") == "OK"
                and r.get("relative_status") == "OK"
                and int(r.get("rs_universe_n") or 0) >= 20
            ):
                try:
                    if np.isfinite(float(r.get("volume_percentile_60s"))):
                        self.eval_n += 1
                except (TypeError, ValueError):
                    pass
            if raw:
                self.raw_true_n += 1
            fired = sm.on_grid(raw=raw, grid_epoch=g, day=self.day)
            if fired is not None and prev_raw is True and raw is True:
                self.persist_refire += 1
            if fired is not None:
                self.triggers.append({
                    "date": self.day,
                    "session": sess,
                    "symbol": s,
                    "t0": float(fired.t0),
                    "grid_index": idx,
                    "vol_percentile_60s": r.get("volume_percentile_60s"),
                    "peer_n": r.get("rs_universe_n"),
                    "previous_raw": False,
                    "current_raw": True,
                    "source_tick_t": r.get("source_tick_t"),
                    "grid_time": r.get("grid_time"),
                })
                if fired.status == SESSION_INCOMPLETE:
                    self.confirms.append({
                        "date": self.day,
                        "session": sess,
                        "symbol": s,
                        "t0": fired.t0,
                        "t1": fired.t1,
                        "status": SESSION_INCOMPLETE,
                        "reason": "T1_AFTER_SESSION_END",
                        "trend_slope": None,
                        "endpoint_return": None,
                        "p0": None,
                        "p10": None,
                        "fail_checkpoint": None,
                        "decision_fire_time": None,
                        "snapshot_cutoff": fired.t1,
                        "checkpoint_future_leak": 0,
                        "vol_percentile_60s": r.get("volume_percentile_60s"),
                    })
            self.sess_idx[s][sess] = idx + 1

    def _finish_active(
        self,
        symbol: str,
        session: str,
        sm: SymbolMachine,
        last_capture_t: Optional[float],
    ) -> Optional[dict[str, Any]]:
        anc = sm.active
        if anc is None:
            return None
        t1 = float(anc.t1)
        self._sync(symbol)
        if last_capture_t is not None and float(last_capture_t) + 1e-12 < t1:
            sm.close_active(CAPTURE_BOUNDARY_INCOMPLETE)
            row = {
                "date": self.day,
                "session": session,
                "symbol": symbol,
                "t0": anc.t0,
                "t1": t1,
                "status": CAPTURE_BOUNDARY_INCOMPLETE,
                "reason": "LAST_CAPTURE_BEFORE_T1",
                "trend_slope": None,
                "endpoint_return": None,
                "p0": None,
                "p10": None,
                "fail_checkpoint": None,
                "decision_fire_time": None,
                "snapshot_cutoff": t1,
                "checkpoint_future_leak": 0,
                "vol_percentile_60s": None,
            }
            self.confirms.append(row)
            return row
        r = confirm_c1(
            symbol=symbol,
            t0=anc.t0,
            t1=t1,
            times=self.t_arr[symbol],
            prices=self.px_arr[symbol],
        )
        sm.close_active(r["status"])
        self.checkpoint_future_leak += int(r.get("checkpoint_future_leak") or 0)
        volp = None
        for tr in reversed(self.triggers):
            if tr["symbol"] == symbol and abs(float(tr["t0"]) - float(anc.t0)) < 1e-9:
                volp = tr.get("vol_percentile_60s")
                break
        row = {
            "date": self.day,
            "session": session,
            "symbol": symbol,
            "t0": anc.t0,
            "t1": t1,
            "status": r["status"],
            "reason": r.get("reason"),
            "trend_slope": r.get("trend_slope"),
            "endpoint_return": r.get("endpoint_return"),
            "p0": r.get("p0"),
            "p10": r.get("p10"),
            "fail_checkpoint": r.get("fail_checkpoint"),
            "decision_fire_time": None,
            "snapshot_cutoff": t1,
            "checkpoint_future_leak": r.get("checkpoint_future_leak") or 0,
            "vol_percentile_60s": volp,
        }
        self.confirms.append(row)
        if r["status"] == CONFIRMED:
            self.pending_confirmed.append(row)
        return row

    def due_confirmed(self, event_t: float) -> list[dict[str, Any]]:
        due: list[dict[str, Any]] = []
        still: list[dict[str, Any]] = []
        for c in self.pending_confirmed:
            if float(c["t1"]) + 1e-12 < float(event_t):
                c["decision_fire_time"] = float(event_t)
                if float(event_t) <= float(c["snapshot_cutoff"]) + 1e-12:
                    self.decision_snapshot_future_leak += 1
                due.append(c)
            else:
                still.append(c)
        self.pending_confirmed = still
        return due
