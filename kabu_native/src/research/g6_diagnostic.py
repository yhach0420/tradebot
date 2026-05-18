"""
G6_VOLUME_DELTA effectiveness diagnostics for Logic Lab (Phase 21).

Diagnoses 30s volume delta vs dynamic threshold (no threshold changes).
Includes G5×G6 intersection counts.
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

from research.g5_diagnostic import ForwardPath, g5_classify
from src.kabu_signal_engine import (
    VOLUME_DELTA_FLOOR,
    VOLUME_DELTA_TRADING_VALUE_RATIO,
    volume_threshold,
)

def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


G6_DEFINITION = {
    "gate": "G6_VOLUME_DELTA",
    "volume_delta_30s": (
        "Sum of TradingVolume cumulative deltas in the last 30s from PUSH history "
        "(replay: synthetic sub-bar volume steps)."
    ),
    "minute_trading_value": (
        "Board MinuteTradingValue (1m bar volume×close). Replay uses this for "
        "volume_threshold scaling; production uses session TradingValue when absent."
    ),
    "threshold": (
        f"max({VOLUME_DELTA_FLOOR}, minute_trading_value × {VOLUME_DELTA_TRADING_VALUE_RATIO} "
        "× tier_B_mult if tier B)."
    ),
    "volume_ratio": "volume_delta_30s / threshold when threshold > 0",
    "previous_window_volume": (
        "p75 of 30s volume deltas over prior 30 minutes (benchmark; not the gate input)."
    ),
    "current_minute_volume": "CSV 1m bar volume when lookup available; else null",
}


def _board_float(board: Mapping[str, Any], key: str) -> Optional[float]:
    v = board.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def g6_classify(rejects: list[str]) -> str:
    rs = {str(r) for r in rejects}
    if "G6_VOLUME_DELTA" in rs:
        return "reject"
    if "G6_VOLUME_DELTA_UNKNOWN" in rs or "G6_VOLUME_DELTA_UNAVAILABLE" in rs:
        return "unavailable"
    if "REST_ONLY_NO_PUSH_HISTORY" in rs:
        return "unavailable"
    return "pass"


def _g6_reject_subtype(
    vol_delta: Optional[float], threshold: Optional[float], rejects: list[str]
) -> str:
    rs = {str(r) for r in rejects}
    if "G6_VOLUME_DELTA_UNKNOWN" in rs:
        return "missing"
    if "G6_VOLUME_DELTA_UNAVAILABLE" in rs or "REST_ONLY_NO_PUSH_HISTORY" in rs:
        return "unavailable"
    if "G6_VOLUME_DELTA" not in rs:
        return "pass"
    if vol_delta is None:
        return "missing"
    if vol_delta == 0.0:
        return "zero"
    if threshold is not None and vol_delta < threshold:
        return "below_threshold"
    return "below_threshold"


@dataclass
class G6BucketStats:
    count: int = 0
    forward_mfe: list[float] = field(default_factory=list)
    forward_mae: list[float] = field(default_factory=list)
    mfe_ge_0_3: int = 0
    mfe_ge_0_5: int = 0
    high_update: int = 0
    breakout_continuation: int = 0
    breakout_failure: int = 0
    trade_pnls: list[float] = field(default_factory=list)
    trade_mfes: list[float] = field(default_factory=list)
    trade_maes: list[float] = field(default_factory=list)
    trade_holds_min: list[float] = field(default_factory=list)
    trade_breakout_fail: int = 0
    candidates: int = 0
    volume_delta_samples: list[float] = field(default_factory=list)
    threshold_samples: list[float] = field(default_factory=list)
    volume_ratio_samples: list[float] = field(default_factory=list)
    missing_count: int = 0
    zero_count: int = 0
    below_threshold_count: int = 0

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
        if fp_short.breakout_failure:
            self.breakout_failure += 1

    def record_metrics(
        self,
        *,
        vol_delta: Optional[float],
        threshold: Optional[float],
        subtype: str,
    ) -> None:
        if vol_delta is not None:
            self.volume_delta_samples.append(vol_delta)
        if threshold is not None:
            self.threshold_samples.append(threshold)
        if (
            vol_delta is not None
            and threshold is not None
            and threshold > 0
        ):
            self.volume_ratio_samples.append(vol_delta / threshold)
        if subtype == "missing":
            self.missing_count += 1
        elif subtype == "zero":
            self.zero_count += 1
        elif subtype == "below_threshold":
            self.below_threshold_count += 1

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
class G6DiagnosticAccumulator:
    profile: str = ""
    tier: str = "B"
    g6_pass: G6BucketStats = field(default_factory=G6BucketStats)
    g6_reject: G6BucketStats = field(default_factory=G6BucketStats)
    g6_unavailable: int = 0
    eval_count: int = 0
    candidates_after_g6: int = 0
    trades_after_g6: int = 0
    g5_pass_g6_reject: int = 0
    g5_reject_g6_pass: int = 0
    g5_g6_both_pass: int = 0
    g5_g6_both_reject: int = 0
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
        board: Mapping[str, Any],
        forward_short: ForwardPath,
        forward_ext: ForwardPath,
        is_candidate: bool,
        vol_p75_30m: Optional[float],
        csv_bar_volume: Optional[float],
        signal_cfg: Any,
    ) -> None:
        self.eval_count += 1
        g5c = g5_classify(rejects)
        g6c = g6_classify(rejects)

        minute_tv = _board_float(board, "MinuteTradingValue")
        if minute_tv is None:
            minute_tv = _as_float(rd.get("trading_value"))
        vol_delta = _as_float(rd.get("volume_delta_30s"))
        thr = volume_threshold(minute_tv, tier=self.tier, cfg=signal_cfg)
        subtype = _g6_reject_subtype(vol_delta, thr, rejects)

        if g6c == "unavailable":
            self.g6_unavailable += 1
            return

        bucket = self.g6_reject if g6c == "reject" else self.g6_pass
        bucket.record_forward(forward_short, forward_ext)
        bucket.record_metrics(vol_delta=vol_delta, threshold=thr, subtype=subtype)

        sym = self.symbol_stats[symbol]
        sym[f"g6_{g6c}_count"] += 1

        if g5c != "unavailable":
            if g5c == "pass" and g6c == "reject":
                self.g5_pass_g6_reject += 1
            elif g5c == "reject" and g6c == "pass":
                self.g5_reject_g6_pass += 1
            elif g5c == "pass" and g6c == "pass":
                self.g5_g6_both_pass += 1
            elif g5c == "reject" and g6c == "reject":
                self.g5_g6_both_reject += 1

        if g6c == "reject":
            ratio = (vol_delta / thr) if vol_delta is not None and thr and thr > 0 else None
            self.extended_rows.append(
                {
                    "profile": self.profile,
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "event_time": event_time.isoformat(),
                    "g5_state": g5c,
                    "volume_delta_30s": vol_delta,
                    "minute_trading_value": minute_tv,
                    "threshold": thr,
                    "volume_ratio": ratio,
                    "previous_window_volume_p75": vol_p75_30m,
                    "current_minute_volume": csv_bar_volume,
                    "reject_subtype": subtype,
                    "forward_mfe_pct": forward_ext.max_favorable_pct,
                    "rejected_then_mfe_0_3": forward_ext.mfe_ge_0_3,
                    "rejected_then_mfe_0_5": forward_ext.mfe_ge_0_5,
                    "rejected_then_breakout_continuation": forward_ext.breakout_continuation,
                    "rejected_then_high_update": forward_ext.high_updated,
                }
            )

        if g6c == "pass" and is_candidate:
            self.candidates_after_g6 += 1
            self.g6_pass.candidates += 1

    def record_trade_entry(self) -> None:
        self.trades_after_g6 += 1

    def record_closed_trade(
        self,
        *,
        pnl_pct: float,
        mfe_pct: float,
        mae_pct: float,
        hold_min: float,
        exit_reason: str,
    ) -> None:
        self.g6_pass.record_trade(
            pnl_pct=pnl_pct,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            hold_min=hold_min,
            exit_reason=exit_reason,
        )

    def merge(self, other: "G6DiagnosticAccumulator") -> None:
        self.eval_count += other.eval_count
        self.g6_unavailable += other.g6_unavailable
        self.candidates_after_g6 += other.candidates_after_g6
        self.trades_after_g6 += other.trades_after_g6
        self.g5_pass_g6_reject += other.g5_pass_g6_reject
        self.g5_reject_g6_pass += other.g5_reject_g6_pass
        self.g5_g6_both_pass += other.g5_g6_both_pass
        self.g5_g6_both_reject += other.g5_g6_both_reject
        self.extended_rows.extend(other.extended_rows)
        for sym, d in other.symbol_stats.items():
            for k, v in d.items():
                self.symbol_stats[sym][k] += v
        _merge_g6_bucket(self.g6_pass, other.g6_pass)
        _merge_g6_bucket(self.g6_reject, other.g6_reject)


def _merge_g6_bucket(a: G6BucketStats, b: G6BucketStats) -> None:
    a.count += b.count
    a.forward_mfe.extend(b.forward_mfe)
    a.forward_mae.extend(b.forward_mae)
    a.mfe_ge_0_3 += b.mfe_ge_0_3
    a.mfe_ge_0_5 += b.mfe_ge_0_5
    a.high_update += b.high_update
    a.breakout_continuation += b.breakout_continuation
    a.breakout_failure += b.breakout_failure
    a.trade_pnls.extend(b.trade_pnls)
    a.trade_mfes.extend(b.trade_mfes)
    a.trade_maes.extend(b.trade_maes)
    a.trade_holds_min.extend(b.trade_holds_min)
    a.trade_breakout_fail += b.trade_breakout_fail
    a.candidates += b.candidates
    a.volume_delta_samples.extend(b.volume_delta_samples)
    a.threshold_samples.extend(b.threshold_samples)
    a.volume_ratio_samples.extend(b.volume_ratio_samples)
    a.missing_count += b.missing_count
    a.zero_count += b.zero_count
    a.below_threshold_count += b.below_threshold_count


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


def _dist(values: list[float]) -> dict[str, Optional[float]]:
    return {
        "p50": _median(values),
        "p75": _percentile(values, 75),
        "p90": _percentile(values, 90),
        "count": len(values),
    }


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def _bucket_summary(b: G6BucketStats, *, label: str) -> dict[str, Any]:
    n = b.count
    return {
        "label": label,
        "count": n,
        "missing_count": b.missing_count,
        "zero_count": b.zero_count,
        "below_threshold_count": b.below_threshold_count,
        "volume_delta_30s_distribution": _dist(b.volume_delta_samples),
        "threshold_distribution": _dist(b.threshold_samples),
        "volume_ratio_distribution": _dist(b.volume_ratio_samples),
        "forward_avg_mfe_pct": _mean(b.forward_mfe),
        "forward_median_mfe_pct": _median(b.forward_mfe),
        "forward_mfe_ge_0_3_rate": _rate(b.mfe_ge_0_3, n),
        "forward_mfe_ge_0_5_rate": _rate(b.mfe_ge_0_5, n),
        "forward_high_update_rate": _rate(b.high_update, n),
        "forward_breakout_continuation_rate": _rate(b.breakout_continuation, n),
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


def summarize_g6(acc: G6DiagnosticAccumulator) -> dict[str, Any]:
    g6_pass_count = acc.g6_pass.count
    g6_reject_count = acc.g6_reject.count
    g6_evaluable = g6_pass_count + g6_reject_count
    g6_pass_rate = (g6_pass_count / g6_evaluable) if g6_evaluable else None

    pass_s = _bucket_summary(acc.g6_pass, label="pass")
    reject_s = _bucket_summary(acc.g6_reject, label="reject")

    reject_mfe_03 = reject_s.get("forward_mfe_ge_0_3_rate")
    pass_pf = pass_s.get("trade_profit_factor")
    pass_fwd_mfe = pass_s.get("forward_mfe_ge_0_3_rate")
    reject_fwd_mfe = reject_s.get("forward_mfe_ge_0_3_rate")
    pass_bf = pass_s.get("breakout_failure_rate")
    reject_bf = reject_s.get("breakout_failure_rate")

    g6_possible_overfilter = False
    notes: list[str] = []
    if reject_mfe_03 is not None and reject_mfe_03 >= 0.25:
        g6_possible_overfilter = True
        notes.append("high_reject_forward_mfe_0_3")
    if g6_pass_rate is not None and g6_pass_rate < 0.15:
        notes.append("low_g6_pass_rate")

    g6_is_alpha_positive = False
    if pass_pf is not None and pass_pf > 1.0:
        g6_is_alpha_positive = True
        notes.append("pass_trades_pf_above_1")
    elif (
        pass_fwd_mfe is not None
        and reject_fwd_mfe is not None
        and pass_fwd_mfe > reject_fwd_mfe + 0.05
        and pass_bf is not None
        and reject_bf is not None
        and pass_bf < reject_bf
    ):
        g6_is_alpha_positive = True
        notes.append("pass_forward_mfe_better_and_lower_bf_rate")

    return {
        "g6_definition": G6_DEFINITION,
        "g6_pass_count": g6_pass_count,
        "g6_reject_count": g6_reject_count,
        "g6_pass_rate": g6_pass_rate,
        "g6_unavailable_count": acc.g6_unavailable,
        "candidates_after_g6": acc.candidates_after_g6,
        "trades_after_g6": acc.trades_after_g6,
        "g5_g6_intersection": {
            "g5_pass_g6_reject": acc.g5_pass_g6_reject,
            "g5_reject_g6_pass": acc.g5_reject_g6_pass,
            "g5_g6_both_pass": acc.g5_g6_both_pass,
            "g5_g6_both_reject": acc.g5_g6_both_reject,
        },
        "g5_g6_both_pass_count": acc.g5_g6_both_pass,
        "pass": pass_s,
        "reject": reject_s,
        "rejected_then_mfe_0_3_rate": reject_mfe_03,
        "rejected_then_mfe_0_5_rate": reject_s.get("forward_mfe_ge_0_5_rate"),
        "rejected_then_breakout_continuation_rate": reject_s.get("forward_breakout_continuation_rate"),
        "rejected_then_high_update_rate": reject_s.get("forward_high_update_rate"),
        "g6_is_alpha_positive": g6_is_alpha_positive,
        "g6_possible_overfilter": g6_possible_overfilter,
        "g6_rejected_mfe_rate": reject_mfe_03,
        "g6_pass_pf": pass_pf,
        "diagnosis_notes": ";".join(notes) if notes else "",
    }


def build_g6_diagnostic_report(
    g6_by_profile: dict[str, G6DiagnosticAccumulator],
) -> dict[str, Any]:
    return {
        "phase": 21,
        "profiles": {p: summarize_g6(acc) for p, acc in g6_by_profile.items() if acc.eval_count},
    }


def write_g6_csv_outputs(
    out_dir: Any,
    g6_by_profile: dict[str, G6DiagnosticAccumulator],
) -> None:
    from pathlib import Path

    root = Path(out_dir)

    pass_reject_rows: list[dict[str, Any]] = []
    for pname, acc in g6_by_profile.items():
        s = summarize_g6(acc)
        for side in ("pass", "reject"):
            b = s[side]
            pass_reject_rows.append(
                {
                    "profile": pname,
                    "side": side,
                    **{k: b.get(k) for k in b if k != "label"},
                }
            )
    if pass_reject_rows:
        with (root / "g6_pass_vs_reject.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(pass_reject_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(pass_reject_rows)

    ext_rows: list[dict[str, Any]] = []
    for acc in g6_by_profile.values():
        ext_rows.extend(acc.extended_rows)
    if ext_rows:
        with (root / "g6_rejected_but_extended.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ext_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(ext_rows)

    sym_rows: list[dict[str, Any]] = []
    for pname, acc in g6_by_profile.items():
        for sym, stats in sorted(acc.symbol_stats.items()):
            sym_rows.append(
                {
                    "profile": pname,
                    "symbol": sym,
                    "g6_pass_count": stats.get("g6_pass_count", 0),
                    "g6_reject_count": stats.get("g6_reject_count", 0),
                }
            )
    if sym_rows:
        with (root / "g6_symbol_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(sym_rows[0].keys()))
            w.writeheader()
            w.writerows(sym_rows)

    ix_rows: list[dict[str, Any]] = []
    for pname, acc in g6_by_profile.items():
        s = summarize_g6(acc)
        ix = s["g5_g6_intersection"]
        ix_rows.append({"profile": pname, **ix})
    if ix_rows:
        with (root / "g5_g6_intersection.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ix_rows[0].keys()))
            w.writeheader()
            w.writerows(ix_rows)
