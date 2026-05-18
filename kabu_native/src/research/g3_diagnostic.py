"""
G3_VWAP_DIST effectiveness diagnostics for Logic Lab (Phase 22).

VWAP distance gate vs forward path and G5/G6 intersections (no threshold changes).
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

from research.g5_diagnostic import ForwardPath, g5_classify
from research.g6_diagnostic import g6_classify
from src.kabu_signal_engine import VWAP_DISTANCE_PCT_MIN

# 診断のみ: 通過側で「VWAP乖離が大きすぎる」高値掴みリスク帯（ゲートは変更しない）
VWAP_ABOVE_RISK_BAND_PCT = 1.5


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


G3_DEFINITION = {
    "gate": "G3_VWAP_DIST",
    "current_price": "Board CurrentPrice at eval",
    "vwap": "Board VWAP (session cumulative typical in replay)",
    "vwap_distance_pct": "(price - vwap) / vwap * 100 when vwap > 0",
    "threshold": f"vwap_distance_pct_min default {VWAP_DISTANCE_PCT_MIN}% (profile may override)",
    "reject_rule": "vwap_distance_pct < threshold -> G3_VWAP_DIST",
    "above_risk_band": (
        f"diagnostic only: vwap_distance_pct >= {VWAP_ABOVE_RISK_BAND_PCT}% on G3 pass "
        "(extended / chase risk, not a reject code)"
    ),
}


def g3_classify(rejects: list[str]) -> str:
    rs = {str(r) for r in rejects}
    if "G3_VWAP_DIST" in rs:
        return "reject"
    if "G3_VWAP_DIST_UNKNOWN" in rs:
        return "unavailable"
    return "pass"


def _gate_pass(cls: str) -> bool:
    return cls == "pass"


def _g3_reject_subtype(vwap_dist: Optional[float], threshold: float, rejects: list[str]) -> str:
    rs = {str(r) for r in rejects}
    if "G3_VWAP_DIST_UNKNOWN" in rs:
        return "missing"
    if "G3_VWAP_DIST" not in rs:
        return "pass"
    if vwap_dist is None:
        return "missing"
    if vwap_dist < threshold:
        return "below_threshold"
    return "below_threshold"


@dataclass
class G3BucketStats:
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
    vwap_dist_samples: list[float] = field(default_factory=list)
    missing_count: int = 0
    below_threshold_count: int = 0
    above_risk_band_count: int = 0

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

    def record_vwap(
        self,
        *,
        vwap_dist: Optional[float],
        subtype: str,
        threshold: float,
    ) -> None:
        if vwap_dist is not None:
            self.vwap_dist_samples.append(vwap_dist)
            if subtype == "pass" and vwap_dist >= VWAP_ABOVE_RISK_BAND_PCT:
                self.above_risk_band_count += 1
        if subtype == "missing":
            self.missing_count += 1
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
class G3DiagnosticAccumulator:
    profile: str = ""
    threshold_pct: float = VWAP_DISTANCE_PCT_MIN
    g3_pass: G3BucketStats = field(default_factory=G3BucketStats)
    g3_reject: G3BucketStats = field(default_factory=G3BucketStats)
    g3_unavailable: int = 0
    eval_count: int = 0
    candidates_after_g3: int = 0
    trades_after_g3: int = 0
    g3_g5_g6_all_pass_count: int = 0
    g3_pass_g5_reject: int = 0
    g3_pass_g6_reject: int = 0
    g5_g6_pass_g3_reject: int = 0
    three_gate_candidates: int = 0
    three_gate_entries: int = 0
    three_gate_trade_pnls: list[float] = field(default_factory=list)
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
        g3c = g3_classify(rejects)
        g5c = g5_classify(rejects)
        g6c = g6_classify(rejects)

        thr = self.threshold_pct
        vwap_dist = _as_float(rd.get("vwap_distance_pct"))
        price = _as_float(rd.get("current_price"))
        vwap = _as_float(rd.get("vwap"))
        subtype = _g3_reject_subtype(vwap_dist, thr, rejects)

        if g3c == "unavailable":
            self.g3_unavailable += 1
            return

        bucket = self.g3_reject if g3c == "reject" else self.g3_pass
        bucket.record_forward(forward_short, forward_ext)
        if g3c == "reject":
            bucket.record_vwap(vwap_dist=vwap_dist, subtype=subtype, threshold=thr)
        else:
            bucket.record_vwap(vwap_dist=vwap_dist, subtype="pass", threshold=thr)

        sym = self.symbol_stats[symbol]
        sym[f"g3_{g3c}_count"] += 1

        g3p, g5p, g6p = _gate_pass(g3c), _gate_pass(g5c), _gate_pass(g6c)
        if g3p and g5p and g6p:
            self.g3_g5_g6_all_pass_count += 1
            if is_candidate:
                self.three_gate_candidates += 1
        if g3p and not g5p and g5c != "unavailable":
            self.g3_pass_g5_reject += 1
        if g3p and not g6p and g6c != "unavailable":
            self.g3_pass_g6_reject += 1
        if g5p and g6p and not g3p:
            self.g5_g6_pass_g3_reject += 1

        if g3c == "reject":
            self.extended_rows.append(
                {
                    "profile": self.profile,
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "event_time": event_time.isoformat(),
                    "g5_state": g5c,
                    "g6_state": g6c,
                    "current_price": price,
                    "vwap": vwap,
                    "vwap_distance_pct": vwap_dist,
                    "threshold_pct": thr,
                    "reject_subtype": subtype,
                    "forward_mfe_pct": forward_ext.max_favorable_pct,
                    "rejected_then_mfe_0_3": forward_ext.mfe_ge_0_3,
                    "rejected_then_mfe_0_5": forward_ext.mfe_ge_0_5,
                    "rejected_then_breakout_continuation": forward_ext.breakout_continuation,
                    "rejected_then_high_update": forward_ext.high_updated,
                }
            )

        if g3c == "pass" and is_candidate:
            self.candidates_after_g3 += 1
            self.g3_pass.candidates += 1

    def record_trade_entry(self, *, g3_pass: bool, g5_pass: bool, g6_pass: bool) -> None:
        self.trades_after_g3 += 1
        if g3_pass and g5_pass and g6_pass:
            self.three_gate_entries += 1

    def record_closed_trade(
        self,
        *,
        pnl_pct: float,
        mfe_pct: float,
        mae_pct: float,
        hold_min: float,
        exit_reason: str,
        all_three_pass: bool,
    ) -> None:
        self.g3_pass.record_trade(
            pnl_pct=pnl_pct,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            hold_min=hold_min,
            exit_reason=exit_reason,
        )
        if all_three_pass:
            self.three_gate_trade_pnls.append(pnl_pct)

    def merge(self, other: "G3DiagnosticAccumulator") -> None:
        self.eval_count += other.eval_count
        self.g3_unavailable += other.g3_unavailable
        self.candidates_after_g3 += other.candidates_after_g3
        self.trades_after_g3 += other.trades_after_g3
        self.g3_g5_g6_all_pass_count += other.g3_g5_g6_all_pass_count
        self.g3_pass_g5_reject += other.g3_pass_g5_reject
        self.g3_pass_g6_reject += other.g3_pass_g6_reject
        self.g5_g6_pass_g3_reject += other.g5_g6_pass_g3_reject
        self.three_gate_candidates += other.three_gate_candidates
        self.three_gate_entries += other.three_gate_entries
        self.three_gate_trade_pnls.extend(other.three_gate_trade_pnls)
        self.extended_rows.extend(other.extended_rows)
        for sym, d in other.symbol_stats.items():
            for k, v in d.items():
                self.symbol_stats[sym][k] += v
        _merge_g3_bucket(self.g3_pass, other.g3_pass)
        _merge_g3_bucket(self.g3_reject, other.g3_reject)


def _merge_g3_bucket(a: G3BucketStats, b: G3BucketStats) -> None:
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
    a.vwap_dist_samples.extend(b.vwap_dist_samples)
    a.missing_count += b.missing_count
    a.below_threshold_count += b.below_threshold_count
    a.above_risk_band_count += b.above_risk_band_count


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


def _dist(values: list[float]) -> dict[str, Any]:
    return {
        "p50": _percentile(values, 50),
        "p75": _percentile(values, 75),
        "p90": _percentile(values, 90),
        "count": len(values),
    }


def _bucket_summary(b: G3BucketStats, *, label: str) -> dict[str, Any]:
    n = b.count
    return {
        "label": label,
        "count": n,
        "missing_count": b.missing_count,
        "below_threshold_count": b.below_threshold_count,
        "above_risk_band_count": b.above_risk_band_count,
        "vwap_distance_pct_distribution": _dist(b.vwap_dist_samples),
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


def summarize_g3(acc: G3DiagnosticAccumulator) -> dict[str, Any]:
    g3_pass_count = acc.g3_pass.count
    g3_reject_count = acc.g3_reject.count
    g3_evaluable = g3_pass_count + g3_reject_count
    g3_pass_rate = (g3_pass_count / g3_evaluable) if g3_evaluable else None

    pass_s = _bucket_summary(acc.g3_pass, label="pass")
    reject_s = _bucket_summary(acc.g3_reject, label="reject")

    reject_mfe_03 = reject_s.get("forward_mfe_ge_0_3_rate")
    pass_pf = pass_s.get("trade_profit_factor")
    pass_fwd_mfe = pass_s.get("forward_mfe_ge_0_3_rate")
    reject_fwd_mfe = reject_s.get("forward_mfe_ge_0_3_rate")
    pass_bf = pass_s.get("breakout_failure_rate")
    reject_bf = reject_s.get("breakout_failure_rate")

    three_gate_pf = _profit_factor(acc.three_gate_trade_pnls)

    g3_possible_overfilter = False
    notes: list[str] = []
    if reject_mfe_03 is not None and reject_mfe_03 >= 0.25:
        g3_possible_overfilter = True
        notes.append("high_reject_forward_mfe_0_3")
    if g3_pass_rate is not None and g3_pass_rate < 0.35:
        notes.append("low_g3_pass_rate")
    if pass_s.get("above_risk_band_count", 0) and g3_pass_count:
        risk_rate = pass_s["above_risk_band_count"] / g3_pass_count
        if risk_rate > 0.3:
            notes.append("high_pass_above_risk_vwap_band")

    g3_is_alpha_positive = False
    if pass_pf is not None and pass_pf > 1.0:
        g3_is_alpha_positive = True
        notes.append("pass_trades_pf_above_1")
    elif three_gate_pf is not None and three_gate_pf > 1.0:
        g3_is_alpha_positive = True
        notes.append("g3_g5_g6_all_pass_pf_above_1")
    elif (
        pass_fwd_mfe is not None
        and reject_fwd_mfe is not None
        and pass_fwd_mfe > reject_fwd_mfe + 0.05
        and pass_bf is not None
        and reject_bf is not None
        and pass_bf < reject_bf
    ):
        g3_is_alpha_positive = True
        notes.append("pass_forward_mfe_better_and_lower_bf_rate")

    return {
        "g3_definition": G3_DEFINITION,
        "g3_threshold_pct": acc.threshold_pct,
        "g3_pass_count": g3_pass_count,
        "g3_reject_count": g3_reject_count,
        "g3_pass_rate": g3_pass_rate,
        "g3_unavailable_count": acc.g3_unavailable,
        "candidates_after_g3": acc.candidates_after_g3,
        "trades_after_g3": acc.trades_after_g3,
        "g3_g5_g6_intersection": {
            "g3_g5_g6_all_pass": acc.g3_g5_g6_all_pass_count,
            "g3_pass_g5_reject": acc.g3_pass_g5_reject,
            "g3_pass_g6_reject": acc.g3_pass_g6_reject,
            "g5_g6_pass_g3_reject": acc.g5_g6_pass_g3_reject,
        },
        "g3_g5_g6_all_pass_count": acc.g3_g5_g6_all_pass_count,
        "three_gate_candidates": acc.three_gate_candidates,
        "three_gate_entries": acc.three_gate_entries,
        "three_gate_profit_factor": three_gate_pf,
        "pass": pass_s,
        "reject": reject_s,
        "rejected_then_mfe_0_3_rate": reject_mfe_03,
        "rejected_then_mfe_0_5_rate": reject_s.get("forward_mfe_ge_0_5_rate"),
        "rejected_then_breakout_continuation_rate": reject_s.get("forward_breakout_continuation_rate"),
        "rejected_then_high_update_rate": reject_s.get("forward_high_update_rate"),
        "g3_is_alpha_positive": g3_is_alpha_positive,
        "g3_possible_overfilter": g3_possible_overfilter,
        "g3_rejected_mfe_rate": reject_mfe_03,
        "g3_pass_pf": pass_pf,
        "diagnosis_notes": ";".join(notes) if notes else "",
    }


def build_g3_diagnostic_report(
    g3_by_profile: dict[str, G3DiagnosticAccumulator],
) -> dict[str, Any]:
    return {
        "phase": 22,
        "profiles": {p: summarize_g3(acc) for p, acc in g3_by_profile.items() if acc.eval_count},
    }


def write_g3_csv_outputs(
    out_dir: Any,
    g3_by_profile: dict[str, G3DiagnosticAccumulator],
) -> None:
    from pathlib import Path

    root = Path(out_dir)

    pass_reject_rows: list[dict[str, Any]] = []
    for pname, acc in g3_by_profile.items():
        s = summarize_g3(acc)
        for side in ("pass", "reject"):
            b = s[side]
            pass_reject_rows.append({"profile": pname, "side": side, **dict(b)})
    if pass_reject_rows:
        with (root / "g3_pass_vs_reject.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(pass_reject_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(pass_reject_rows)

    ext_rows: list[dict[str, Any]] = []
    for acc in g3_by_profile.values():
        ext_rows.extend(acc.extended_rows)
    if ext_rows:
        with (root / "g3_rejected_but_extended.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ext_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(ext_rows)

    sym_rows: list[dict[str, Any]] = []
    for pname, acc in g3_by_profile.items():
        for sym, stats in sorted(acc.symbol_stats.items()):
            sym_rows.append(
                {
                    "profile": pname,
                    "symbol": sym,
                    "g3_pass_count": stats.get("g3_pass_count", 0),
                    "g3_reject_count": stats.get("g3_reject_count", 0),
                }
            )
    if sym_rows:
        with (root / "g3_symbol_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(sym_rows[0].keys()))
            w.writeheader()
            w.writerows(sym_rows)

    ix_rows: list[dict[str, Any]] = []
    for pname, acc in g3_by_profile.items():
        s = summarize_g3(acc)
        ix = s["g3_g5_g6_intersection"]
        ix_rows.append(
            {
                "profile": pname,
                **ix,
                "three_gate_candidates": s.get("three_gate_candidates"),
                "three_gate_entries": s.get("three_gate_entries"),
                "three_gate_profit_factor": s.get("three_gate_profit_factor"),
            }
        )
    if ix_rows:
        with (root / "g3_g5_g6_intersection.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ix_rows[0].keys()))
            w.writeheader()
            w.writerows(ix_rows)
