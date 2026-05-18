"""
G5_ROLLING_HIGH effectiveness diagnostics for Logic Lab (Phase 20).

Compares pass vs reject on forward price path and realized trades (no threshold changes).
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

FORWARD_HORIZON_SEC = 30 * 60.0
EXTENDED_HORIZON_SEC = 6.5 * 3600.0  # rest of session proxy


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _board_price(board: Mapping[str, Any]) -> Optional[float]:
    v = board.get("CurrentPrice")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


@dataclass
class ForwardPath:
    max_favorable_pct: float = 0.0
    max_adverse_pct: float = 0.0
    mfe_ge_0_3: bool = False
    mfe_ge_0_5: bool = False
    high_updated: bool = False
    breakout_continuation: bool = False
    time_to_g5_pass_sec: Optional[float] = None
    time_to_breakout_sec: Optional[float] = None
    breakout_duration_sec: float = 0.0
    breakout_failure: bool = False


@dataclass
class EventForwardCache:
    """Per symbol-day precomputed suffix extrema + short-horizon scan helper."""

    events: Sequence[Any]
    prices: list[Optional[float]]
    ts_sec: list[float]
    max_px_suffix: list[float]
    min_px_suffix: list[float]
    max_sess_high_suffix: list[float]

    @classmethod
    def build(cls, events: Sequence[Any]) -> "EventForwardCache":
        n = len(events)
        prices: list[Optional[float]] = [None] * n
        ts_sec: list[float] = [0.0] * n
        sess: list[float] = [0.0] * n
        for i, ev in enumerate(events):
            prices[i] = _board_price(ev.board)
            ts = ev.ts
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts_sec[i] = ts.timestamp()
            sh = _as_float(ev.board.get("HighPrice"))
            sess[i] = sh if sh is not None else (prices[i] or 0.0)

        max_suf = [0.0] * n
        min_suf = [0.0] * n
        high_suf = [0.0] * n
        for i in range(n - 1, -1, -1):
            px = prices[i]
            if i == n - 1:
                max_suf[i] = px if px is not None else 0.0
                min_suf[i] = px if px is not None else 0.0
                high_suf[i] = sess[i]
            else:
                max_suf[i] = max(px, max_suf[i + 1]) if px is not None else max_suf[i + 1]
                min_suf[i] = min(px, min_suf[i + 1]) if px is not None else min_suf[i + 1]
                high_suf[i] = max(sess[i], high_suf[i + 1])
        return cls(
            events=events,
            prices=prices,
            ts_sec=ts_sec,
            max_px_suffix=max_suf,
            min_px_suffix=min_suf,
            max_sess_high_suffix=high_suf,
        )

    def paths(
        self,
        start_idx: int,
        *,
        px0: float,
        rolling_high_5m: Optional[float],
        trigger_level: Optional[float],
        session_high0: Optional[float],
        horizon_sec: float = FORWARD_HORIZON_SEC,
    ) -> tuple[ForwardPath, ForwardPath]:
        high0 = session_high0 if session_high0 is not None else px0
        ext = ForwardPath()
        ext.max_favorable_pct = _pct_change(self.max_px_suffix[start_idx], px0)
        ext.max_adverse_pct = _pct_change(self.min_px_suffix[start_idx], px0)
        ext.mfe_ge_0_3 = ext.max_favorable_pct >= 0.3
        ext.mfe_ge_0_5 = ext.max_favorable_pct >= 0.5
        ext.high_updated = self.max_sess_high_suffix[start_idx] > high0 + 1e-9

        short = self._scan_short(
            start_idx,
            px0=px0,
            rolling_high_5m=rolling_high_5m,
            trigger_level=trigger_level,
            horizon_sec=horizon_sec,
        )
        ext.breakout_continuation = short.breakout_continuation
        ext.breakout_failure = short.breakout_failure
        return short, ext

    def _scan_short(
        self,
        start_idx: int,
        *,
        px0: float,
        rolling_high_5m: Optional[float],
        trigger_level: Optional[float],
        horizon_sec: float,
    ) -> ForwardPath:
        fp = ForwardPath()
        t0 = self.ts_sec[start_idx]
        t_limit = t0 + horizon_sec
        max_px = px0
        min_px = px0
        above_trigger_since: Optional[float] = None
        last_above_trigger: Optional[float] = None
        had_breakout = False
        failed = False

        for j in range(start_idx + 1, len(self.events)):
            if self.ts_sec[j] > t_limit:
                break
            px = self.prices[j]
            if px is None:
                continue
            dt = self.ts_sec[j] - t0
            max_px = max(max_px, px)
            min_px = min(min_px, px)
            if (
                fp.time_to_g5_pass_sec is None
                and rolling_high_5m is not None
                and px > float(rolling_high_5m)
            ):
                fp.time_to_g5_pass_sec = dt
            if trigger_level is not None and trigger_level > 0:
                if px >= float(trigger_level):
                    had_breakout = True
                    if above_trigger_since is None:
                        above_trigger_since = self.ts_sec[j]
                    last_above_trigger = self.ts_sec[j]
                    if fp.time_to_breakout_sec is None:
                        fp.time_to_breakout_sec = dt
                elif had_breakout and px < float(trigger_level):
                    failed = True

        fp.max_favorable_pct = _pct_change(max_px, px0)
        fp.max_adverse_pct = _pct_change(min_px, px0)
        fp.mfe_ge_0_3 = fp.max_favorable_pct >= 0.3
        fp.mfe_ge_0_5 = fp.max_favorable_pct >= 0.5
        fp.breakout_failure = failed
        if above_trigger_since is not None and last_above_trigger is not None:
            fp.breakout_duration_sec = last_above_trigger - above_trigger_since
            fp.breakout_continuation = fp.breakout_duration_sec >= 30.0
        return fp


def compute_forward_path(
    events: Sequence[Any],
    start_idx: int,
    *,
    px0: float,
    rolling_high_5m: Optional[float],
    trigger_level: Optional[float],
    session_high0: Optional[float],
    horizon_sec: float = FORWARD_HORIZON_SEC,
    extended_horizon_sec: float = EXTENDED_HORIZON_SEC,
    cache: Optional[EventForwardCache] = None,
) -> tuple[ForwardPath, ForwardPath]:
    """Return (short_horizon, extended_session) forward paths."""
    if cache is not None:
        return cache.paths(
            start_idx,
            px0=px0,
            rolling_high_5m=rolling_high_5m,
            trigger_level=trigger_level,
            session_high0=session_high0,
            horizon_sec=horizon_sec,
        )
    c = EventForwardCache.build(events)
    return c.paths(
        start_idx,
        px0=px0,
        rolling_high_5m=rolling_high_5m,
        trigger_level=trigger_level,
        session_high0=session_high0,
        horizon_sec=horizon_sec,
    )


def g5_classify(rejects: list[str]) -> str:
    rs = {str(r) for r in rejects}
    if "G5_ROLLING_HIGH" in rs:
        return "reject"
    if "G5_ROLLING_HIGH_UNAVAILABLE" in rs or "G5_ROLLING_HIGH_INSUFFICIENT" in rs:
        return "unavailable"
    if "REST_ONLY_NO_PUSH_HISTORY" in rs:
        return "unavailable"
    return "pass"


@dataclass
class G5BucketStats:
    count: int = 0
    forward_mfe: list[float] = field(default_factory=list)
    forward_mae: list[float] = field(default_factory=list)
    mfe_ge_0_3: int = 0
    mfe_ge_0_5: int = 0
    high_update: int = 0
    breakout_continuation: int = 0
    time_to_g5_pass: list[float] = field(default_factory=list)
    time_to_breakout: list[float] = field(default_factory=list)
    breakout_duration: list[float] = field(default_factory=list)
    breakout_failure: int = 0
    trade_pnls: list[float] = field(default_factory=list)
    trade_mfes: list[float] = field(default_factory=list)
    trade_maes: list[float] = field(default_factory=list)
    trade_holds_min: list[float] = field(default_factory=list)
    trade_breakout_fail: int = 0
    candidates: int = 0

    def record_forward(self, fp_short: ForwardPath, fp_ext: ForwardPath) -> None:
        self.count += 1
        self.forward_mfe.append(fp_ext.max_favorable_pct)
        self.forward_mae.append(fp_ext.max_adverse_pct)
        if fp_ext.mfe_ge_0_3:
            self.mfe_ge_0_3 += 1
        if fp_ext.mfe_ge_0_5:
            self.mfe_ge_0_5 += 1
        if fp_ext.high_updated:
            self.high_update += 1
        if fp_ext.breakout_continuation:
            self.breakout_continuation += 1
        if fp_short.time_to_g5_pass_sec is not None:
            self.time_to_g5_pass.append(fp_short.time_to_g5_pass_sec)
        if fp_short.time_to_breakout_sec is not None:
            self.time_to_breakout.append(fp_short.time_to_breakout_sec)
        if fp_short.breakout_duration_sec > 0:
            self.breakout_duration.append(fp_short.breakout_duration_sec)
        if fp_short.breakout_failure:
            self.breakout_failure += 1

    def record_trade(
        self,
        *,
        pnl_pct: float,
        mfe_pct: float,
        mae_pct: float,
        hold_min: float,
        exit_reason: str,
    ) -> None:
        self.trade_pnls.append(pnl_pct)
        self.trade_mfes.append(mfe_pct)
        self.trade_maes.append(mae_pct)
        self.trade_holds_min.append(hold_min)
        if exit_reason == "breakout_failure":
            self.trade_breakout_fail += 1


@dataclass
class G5DiagnosticAccumulator:
    profile: str = ""
    g5_pass: G5BucketStats = field(default_factory=G5BucketStats)
    g5_reject: G5BucketStats = field(default_factory=G5BucketStats)
    g5_unavailable: int = 0
    eval_count: int = 0
    candidates_after_g5: int = 0
    trades_after_g5: int = 0
    extended_rows: list[dict[str, Any]] = field(default_factory=list)
    symbol_stats: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def record_eval(
        self,
        *,
        trade_date: str,
        symbol: str,
        event_time: datetime,
        rejects: list[str],
        rd: Mapping[str, Any],
        forward_short: ForwardPath,
        forward_ext: ForwardPath,
        is_candidate: bool,
    ) -> None:
        self.eval_count += 1
        cls = g5_classify(rejects)
        px = rd.get("current_price")
        if cls == "unavailable":
            self.g5_unavailable += 1
            return

        bucket = self.g5_reject if cls == "reject" else self.g5_pass
        bucket.record_forward(forward_short, forward_ext)

        sym = self.symbol_stats[symbol]
        sym[f"g5_{cls}_count"] += 1

        if cls == "reject":
            row = {
                "profile": self.profile,
                "trade_date": trade_date,
                "symbol": symbol,
                "event_time": event_time.isoformat(),
                "current_price": px,
                "rolling_high_5m": rd.get("rolling_high_5m"),
                "trigger_level": rd.get("trigger_level"),
                "breakout_event": rd.get("breakout_event"),
                "forward_mfe_pct": forward_ext.max_favorable_pct,
                "rejected_then_mfe_0_3": forward_ext.mfe_ge_0_3,
                "rejected_then_mfe_0_5": forward_ext.mfe_ge_0_5,
                "rejected_then_breakout_continuation": forward_ext.breakout_continuation,
                "rejected_then_high_update": forward_ext.high_updated,
                "time_to_g5_pass_sec": forward_short.time_to_g5_pass_sec,
                "time_to_breakout_sec": forward_short.time_to_breakout_sec,
            }
            self.extended_rows.append(row)

        if cls == "pass" and is_candidate:
            self.candidates_after_g5 += 1
            self.g5_pass.candidates += 1

    def record_trade_entry(self) -> None:
        self.trades_after_g5 += 1

    def record_closed_trade(
        self,
        *,
        symbol: str,
        pnl_pct: float,
        mfe_pct: float,
        mae_pct: float,
        hold_min: float,
        exit_reason: str,
    ) -> None:
        self.g5_pass.record_trade(
            pnl_pct=pnl_pct,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            hold_min=hold_min,
            exit_reason=exit_reason,
        )

    def merge(self, other: "G5DiagnosticAccumulator") -> None:
        self.eval_count += other.eval_count
        self.g5_unavailable += other.g5_unavailable
        self.candidates_after_g5 += other.candidates_after_g5
        self.trades_after_g5 += other.trades_after_g5
        self.extended_rows.extend(other.extended_rows)
        for sym, d in other.symbol_stats.items():
            for k, v in d.items():
                self.symbol_stats[sym][k] += v
        _merge_bucket(self.g5_pass, other.g5_pass)
        _merge_bucket(self.g5_reject, other.g5_reject)


def _merge_bucket(a: G5BucketStats, b: G5BucketStats) -> None:
    a.count += b.count
    a.forward_mfe.extend(b.forward_mfe)
    a.forward_mae.extend(b.forward_mae)
    a.mfe_ge_0_3 += b.mfe_ge_0_3
    a.mfe_ge_0_5 += b.mfe_ge_0_5
    a.high_update += b.high_update
    a.breakout_continuation += b.breakout_continuation
    a.time_to_g5_pass.extend(b.time_to_g5_pass)
    a.time_to_breakout.extend(b.time_to_breakout)
    a.breakout_duration.extend(b.breakout_duration)
    a.breakout_failure += b.breakout_failure
    a.trade_pnls.extend(b.trade_pnls)
    a.trade_mfes.extend(b.trade_mfes)
    a.trade_maes.extend(b.trade_maes)
    a.trade_holds_min.extend(b.trade_holds_min)
    a.trade_breakout_fail += b.trade_breakout_fail
    a.candidates += b.candidates


def _median(xs: list[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def _mean(xs: list[float]) -> Optional[float]:
    return statistics.mean(xs) if xs else None


def _profit_factor(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    losses = sum(p for p in pnls if p < 0)
    if losses == 0:
        return None if wins == 0 else float("inf")
    return wins / abs(losses)


def _rate(num: int, den: int) -> Optional[float]:
    return (num / den) if den > 0 else None


def _bucket_summary(b: G5BucketStats, *, label: str) -> dict[str, Any]:
    n = b.count
    return {
        "label": label,
        "count": n,
        "forward_avg_mfe_pct": _mean(b.forward_mfe),
        "forward_median_mfe_pct": _median(b.forward_mfe),
        "forward_mfe_ge_0_3_rate": _rate(b.mfe_ge_0_3, n),
        "forward_mfe_ge_0_5_rate": _rate(b.mfe_ge_0_5, n),
        "forward_high_update_rate": _rate(b.high_update, n),
        "forward_breakout_continuation_rate": _rate(b.breakout_continuation, n),
        "median_time_to_g5_pass_sec": _median(b.time_to_g5_pass),
        "median_time_to_breakout_sec": _median(b.time_to_breakout),
        "median_breakout_duration_sec": _median(b.breakout_duration),
        "breakout_failure_rate": _rate(b.breakout_failure, n),
        "trade_count": len(b.trade_pnls),
        "trade_avg_pnl_pct": _mean(b.trade_pnls),
        "trade_median_pnl_pct": _median(b.trade_pnls),
        "trade_profit_factor": _profit_factor(b.trade_pnls),
        "trade_mfe_ge_0_3_rate": _rate(
            sum(1 for x in b.trade_mfes if x >= 0.3), len(b.trade_mfes)
        ),
        "trade_mfe_ge_0_5_rate": _rate(
            sum(1 for x in b.trade_mfes if x >= 0.5), len(b.trade_mfes)
        ),
        "trade_avg_mae_pct": _mean(b.trade_maes),
        "trade_avg_hold_min": _mean(b.trade_holds_min),
        "trade_breakout_failure_rate": _rate(b.trade_breakout_fail, len(b.trade_pnls)),
        "candidates": b.candidates,
    }


def summarize_g5(acc: G5DiagnosticAccumulator) -> dict[str, Any]:
    g5_pass_count = acc.g5_pass.count
    g5_reject_count = acc.g5_reject.count
    g5_evaluable = g5_pass_count + g5_reject_count
    g5_pass_rate = (g5_pass_count / g5_evaluable) if g5_evaluable else None

    pass_s = _bucket_summary(acc.g5_pass, label="pass")
    reject_s = _bucket_summary(acc.g5_reject, label="reject")

    reject_mfe_03 = reject_s.get("forward_mfe_ge_0_3_rate")
    reject_mfe_05 = reject_s.get("forward_mfe_ge_0_5_rate")
    pass_pf = pass_s.get("trade_profit_factor")
    pass_fwd_mfe = pass_s.get("forward_mfe_ge_0_3_rate")
    reject_fwd_mfe = reject_s.get("forward_mfe_ge_0_3_rate")

    g5_rejected_mfe_rate = reject_mfe_03
    g5_rejected_high_update_rate = reject_s.get("forward_high_update_rate")

    g5_possible_overfilter = False
    notes: list[str] = []
    if reject_mfe_03 is not None and reject_mfe_03 >= 0.25:
        g5_possible_overfilter = True
        notes.append("high_reject_forward_mfe_0_3")
    if reject_s.get("forward_high_update_rate") is not None and reject_s["forward_high_update_rate"] >= 0.35:
        notes.append("high_reject_session_high_update")
    if g5_pass_rate is not None and g5_pass_rate < 0.05:
        notes.append("very_low_g5_pass_rate")

    g5_is_alpha_positive = False
    if pass_pf is not None and pass_pf > 1.0:
        g5_is_alpha_positive = True
        notes.append("pass_trades_pf_above_1")
    elif (
        pass_fwd_mfe is not None
        and reject_fwd_mfe is not None
        and pass_fwd_mfe > reject_fwd_mfe + 0.05
    ):
        g5_is_alpha_positive = True
        notes.append("pass_forward_mfe_beats_reject")
    if g5_possible_overfilter and g5_is_alpha_positive:
        notes.append("mixed_alpha_and_opportunity_cost")

    return {
        "g5_pass_count": g5_pass_count,
        "g5_reject_count": g5_reject_count,
        "g5_pass_rate": g5_pass_rate,
        "g5_unavailable_count": acc.g5_unavailable,
        "candidates_after_g5": acc.candidates_after_g5,
        "trades_after_g5": acc.trades_after_g5,
        "pass": pass_s,
        "reject": reject_s,
        "rejected_then_mfe_0_3_rate": reject_mfe_03,
        "rejected_then_mfe_0_5_rate": reject_mfe_05,
        "rejected_then_breakout_continuation_rate": reject_s.get("forward_breakout_continuation_rate"),
        "rejected_then_high_update_rate": g5_rejected_high_update_rate,
        "g5_is_alpha_positive": g5_is_alpha_positive,
        "g5_possible_overfilter": g5_possible_overfilter,
        "g5_rejected_mfe_rate": g5_rejected_mfe_rate,
        "g5_pass_pf": pass_pf,
        "diagnosis_notes": ";".join(notes) if notes else "",
    }


def build_g5_diagnostic_report(
    g5_by_profile: dict[str, G5DiagnosticAccumulator],
) -> dict[str, Any]:
    return {
        "phase": 20,
        "gate": "G5_ROLLING_HIGH",
        "description": "price must exceed 5m rolling high (push history)",
        "profiles": {p: summarize_g5(acc) for p, acc in g5_by_profile.items() if acc.eval_count},
    }


def write_g5_csv_outputs(
    out_dir: Any,
    g5_by_profile: dict[str, G5DiagnosticAccumulator],
) -> None:
    from pathlib import Path

    root = Path(out_dir)

    pass_reject_rows: list[dict[str, Any]] = []
    for pname, acc in g5_by_profile.items():
        s = summarize_g5(acc)
        for side in ("pass", "reject"):
            b = s[side]
            pass_reject_rows.append(
                {
                    "profile": pname,
                    "side": side,
                    "count": b["count"],
                    "forward_avg_mfe_pct": b.get("forward_avg_mfe_pct"),
                    "forward_median_mfe_pct": b.get("forward_median_mfe_pct"),
                    "forward_mfe_ge_0_3_rate": b.get("forward_mfe_ge_0_3_rate"),
                    "forward_mfe_ge_0_5_rate": b.get("forward_mfe_ge_0_5_rate"),
                    "forward_high_update_rate": b.get("forward_high_update_rate"),
                    "forward_breakout_continuation_rate": b.get("forward_breakout_continuation_rate"),
                    "median_time_to_g5_pass_sec": b.get("median_time_to_g5_pass_sec"),
                    "median_time_to_breakout_sec": b.get("median_time_to_breakout_sec"),
                    "median_breakout_duration_sec": b.get("median_breakout_duration_sec"),
                    "breakout_failure_rate": b.get("breakout_failure_rate"),
                    "trade_profit_factor": b.get("trade_profit_factor"),
                    "trade_avg_pnl_pct": b.get("trade_avg_pnl_pct"),
                    "trade_median_pnl_pct": b.get("trade_median_pnl_pct"),
                    "trade_mfe_ge_0_3_rate": b.get("trade_mfe_ge_0_3_rate"),
                    "trade_avg_mae_pct": b.get("trade_avg_mae_pct"),
                    "trade_avg_hold_min": b.get("trade_avg_hold_min"),
                }
            )
    if pass_reject_rows:
        with (root / "g5_pass_vs_reject.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(pass_reject_rows[0].keys()))
            w.writeheader()
            w.writerows(pass_reject_rows)

    ext_rows: list[dict[str, Any]] = []
    for acc in g5_by_profile.values():
        ext_rows.extend(acc.extended_rows)
    if ext_rows:
        with (root / "g5_rejected_but_extended.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ext_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(ext_rows)

    sym_rows: list[dict[str, Any]] = []
    for pname, acc in g5_by_profile.items():
        for sym, stats in sorted(acc.symbol_stats.items()):
            sym_rows.append(
                {
                    "profile": pname,
                    "symbol": sym,
                    "g5_pass_count": stats.get("g5_pass_count", 0),
                    "g5_reject_count": stats.get("g5_reject_count", 0),
                }
            )
    if sym_rows:
        with (root / "g5_symbol_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(sym_rows[0].keys()))
            w.writeheader()
            w.writerows(sym_rows)
