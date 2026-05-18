"""
G7_TRADING_VALUE diagnostics for Logic Lab (Phase 18–19).

Phase 19: replay uses session-cumulative TradingValue on the board (G7 source).
Tracks old incremental TV vs new session cumulative for before/after comparison.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from src.kabu_signal_engine import MIN_TRADING_VALUE, MIN_TRADING_VOLUME

G7_SOURCE_SESSION = "session_cumulative_trading_value"
G7_SOURCE_INCREMENTAL = "incremental_trading_value"


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


def _dist(values: list[float]) -> dict[str, Optional[float]]:
    return {
        "p50": _percentile(values, 50),
        "p75": _percentile(values, 75),
        "p90": _percentile(values, 90),
        "count": len(values),
    }


def build_csv_minute_lookup(df: Any, *, events_per_minute: int) -> dict[datetime, dict[str, float]]:
    """Map UTC minute -> CSV bar volume/close and TV estimates."""
    lookup: dict[datetime, dict[str, float]] = {}
    n_sub = max(1, int(events_per_minute))
    session_cum_tv = 0.0
    prev_minute_tv: Optional[float] = None
    for _, row in df.iterrows():
        ts = row["timestamp"]
        if hasattr(ts, "to_pydatetime"):
            bar_ts = ts.to_pydatetime()
        elif isinstance(ts, datetime):
            bar_ts = ts
        else:
            bar_ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        else:
            bar_ts = bar_ts.astimezone(timezone.utc)
        key = bar_ts.replace(second=0, microsecond=0)
        try:
            vol = float(row["volume"])
        except (TypeError, ValueError):
            vol = 0.0
        close = float(row["close"])
        bar_tv = vol * close
        session_cum_tv += bar_tv
        tv_accel = None
        if prev_minute_tv is not None and prev_minute_tv > 0:
            tv_accel = bar_tv / prev_minute_tv
        prev_minute_tv = bar_tv
        lookup[key] = {
            "bar_volume": vol,
            "bar_close": close,
            "bar_trading_value": bar_tv,
            "incr_trading_value_est": (vol / float(n_sub)) * close,
            "session_cumulative_trading_value": session_cum_tv,
            "minute_trading_value": bar_tv,
            "tv_acceleration_ratio": tv_accel if tv_accel is not None else 0.0,
        }
    return lookup


def _minute_key(ts: datetime) -> datetime:
    t = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return t.replace(second=0, microsecond=0)


def _board_float(board: Mapping[str, Any], key: str) -> Optional[float]:
    v = board.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class G7DiagnosticAccumulator:
    """Per profile×symbol×day; merged at write time."""

    threshold_tv: float = MIN_TRADING_VALUE
    threshold_tvol: float = MIN_TRADING_VOLUME
    g7_source: str = G7_SOURCE_SESSION
    eval_count: int = 0
    missing_count: int = 0
    zero_count: int = 0
    below_threshold_count: int = 0
    unknown_count: int = 0
    g7_reject_count: int = 0
    old_incr_pass_count: int = 0
    new_session_pass_count: int = 0
    diag_g7b_minute_below_count: int = 0
    diag_g7c_accel_count: int = 0
    board_tv_all: list[float] = field(default_factory=list)
    board_tvol_all: list[float] = field(default_factory=list)
    old_incr_tv_all: list[float] = field(default_factory=list)
    new_session_tv_all: list[float] = field(default_factory=list)
    minute_tv_all: list[float] = field(default_factory=list)
    csv_bar_tv_all: list[float] = field(default_factory=list)
    csv_incr_tv_all: list[float] = field(default_factory=list)
    board_tv_on_g7_reject: list[float] = field(default_factory=list)
    csv_bar_tv_on_g7_reject: list[float] = field(default_factory=list)
    csv_missing_minute_count: int = 0

    def record_eval(
        self,
        rd: Mapping[str, Any],
        rejects: list[str],
        *,
        board: Mapping[str, Any],
        csv_meta: Optional[Mapping[str, float]],
    ) -> None:
        self.eval_count += 1
        tv = rd.get("trading_value")
        tvol = rd.get("trading_volume")
        incr_tv = _board_float(board, "IncrementalTradingValue")
        minute_tv = _board_float(board, "MinuteTradingValue")
        if incr_tv is None:
            incr_tv = _board_float(board, "TradingValue") if self.g7_source == G7_SOURCE_INCREMENTAL else None

        tv_f: Optional[float] = None
        if tv is not None:
            try:
                tv_f = float(tv)
                self.board_tv_all.append(tv_f)
                self.new_session_tv_all.append(tv_f)
            except (TypeError, ValueError):
                pass
        if incr_tv is not None:
            self.old_incr_tv_all.append(incr_tv)
            if incr_tv >= self.threshold_tv:
                self.old_incr_pass_count += 1
        if tv_f is not None and tv_f >= self.threshold_tv:
            self.new_session_pass_count += 1
        if minute_tv is not None:
            self.minute_tv_all.append(minute_tv)
            if minute_tv < self.threshold_tv:
                self.diag_g7b_minute_below_count += 1
        if tvol is not None:
            try:
                self.board_tvol_all.append(float(tvol))
            except (TypeError, ValueError):
                pass

        csv_bar_tv: Optional[float] = None
        if csv_meta:
            csv_bar_tv = float(csv_meta.get("bar_trading_value", 0))
            self.csv_bar_tv_all.append(csv_bar_tv)
            self.csv_incr_tv_all.append(float(csv_meta.get("incr_trading_value_est", 0)))
            accel = csv_meta.get("tv_acceleration_ratio")
            if accel is not None and float(accel) >= 1.25:
                self.diag_g7c_accel_count += 1
        else:
            self.csv_missing_minute_count += 1

        reject_set = {str(r) for r in rejects}
        if "G7_TRADING_VALUE_UNKNOWN" in reject_set:
            self.unknown_count += 1
            self.g7_reject_count += 1

        if "G7_TRADING_VALUE" in reject_set:
            self.g7_reject_count += 1
            if tv_f is None:
                self.missing_count += 1
            elif tv_f == 0.0:
                self.zero_count += 1
            elif tv_f < self.threshold_tv:
                self.below_threshold_count += 1
            if tv_f is not None:
                self.board_tv_on_g7_reject.append(tv_f)
            if csv_bar_tv is not None:
                self.csv_bar_tv_on_g7_reject.append(csv_bar_tv)

    def merge(self, other: "G7DiagnosticAccumulator") -> None:
        self.eval_count += other.eval_count
        self.missing_count += other.missing_count
        self.zero_count += other.zero_count
        self.below_threshold_count += other.below_threshold_count
        self.unknown_count += other.unknown_count
        self.g7_reject_count += other.g7_reject_count
        self.old_incr_pass_count += other.old_incr_pass_count
        self.new_session_pass_count += other.new_session_pass_count
        self.diag_g7b_minute_below_count += other.diag_g7b_minute_below_count
        self.diag_g7c_accel_count += other.diag_g7c_accel_count
        self.csv_missing_minute_count += other.csv_missing_minute_count
        self.board_tv_all.extend(other.board_tv_all)
        self.board_tvol_all.extend(other.board_tvol_all)
        self.old_incr_tv_all.extend(other.old_incr_tv_all)
        self.new_session_tv_all.extend(other.new_session_tv_all)
        self.minute_tv_all.extend(other.minute_tv_all)
        self.csv_bar_tv_all.extend(other.csv_bar_tv_all)
        self.csv_incr_tv_all.extend(other.csv_incr_tv_all)
        self.board_tv_on_g7_reject.extend(other.board_tv_on_g7_reject)
        self.csv_bar_tv_on_g7_reject.extend(other.csv_bar_tv_on_g7_reject)


def summarize_g7(acc: G7DiagnosticAccumulator) -> dict[str, Any]:
    tv_all = acc.board_tv_all
    tv_g7 = acc.board_tv_on_g7_reject
    csv_bar = acc.csv_bar_tv_all
    thr = acc.threshold_tv
    eval_n = acc.eval_count or 1

    pass_rate_old = acc.old_incr_pass_count / eval_n if acc.old_incr_tv_all else None
    pass_rate_new = acc.new_session_pass_count / eval_n if acc.new_session_tv_all else None

    p50_all = _percentile(tv_all, 50)
    p90_all = _percentile(tv_all, 90)

    is_data_quality = False
    notes: list[str] = []
    if acc.g7_reject_count > 0:
        if (acc.missing_count + acc.unknown_count) / acc.g7_reject_count > 0.2:
            is_data_quality = True
            notes.append("high_missing_or_unknown_rate")

    possible_strict = False
    if (
        pass_rate_old is not None
        and pass_rate_new is not None
        and pass_rate_old < 0.05
        and pass_rate_new > 0.3
        and not is_data_quality
    ):
        possible_strict = False
        notes.append("g7_definition_fixed_session_cumulative_active")

    return {
        "g7_source": acc.g7_source,
        "g7_threshold": thr,
        "g7_pass_rate": pass_rate_new,
        "pass_rate_old": pass_rate_old,
        "pass_rate_new": pass_rate_new,
        "session_cumulative_trading_value_p50": _percentile(acc.new_session_tv_all, 50),
        "session_cumulative_trading_value_p75": _percentile(acc.new_session_tv_all, 75),
        "session_cumulative_trading_value_p90": _percentile(acc.new_session_tv_all, 90),
        "minute_trading_value_p50": _percentile(acc.minute_tv_all, 50),
        "minute_trading_value_p75": _percentile(acc.minute_tv_all, 75),
        "minute_trading_value_p90": _percentile(acc.minute_tv_all, 90),
        "old_incremental_tv_distribution": _dist(acc.old_incr_tv_all),
        "new_session_cumulative_tv_distribution": _dist(acc.new_session_tv_all),
        "diag_g7b_minute_trading_value_below_threshold_count": acc.diag_g7b_minute_below_count,
        "diag_g7c_tv_acceleration_spike_count": acc.diag_g7c_accel_count,
        "threshold": thr,
        "threshold_trading_volume": acc.threshold_tvol,
        "eval_count": acc.eval_count,
        "g7_reject_count": acc.g7_reject_count,
        "missing_count": acc.missing_count,
        "zero_count": acc.zero_count,
        "below_threshold_count": acc.below_threshold_count,
        "unknown_count": acc.unknown_count,
        "csv_minute_lookup_miss_count": acc.csv_missing_minute_count,
        "board_trading_value_p50": p50_all,
        "board_trading_value_p75": _percentile(tv_all, 75),
        "board_trading_value_p90": p90_all,
        "board_trading_volume_p50": _percentile(acc.board_tvol_all, 50),
        "board_trading_volume_p75": _percentile(acc.board_tvol_all, 75),
        "board_trading_volume_p90": _percentile(acc.board_tvol_all, 90),
        "csv_bar_trading_value_p50": _percentile(csv_bar, 50),
        "csv_bar_trading_value_p75": _percentile(csv_bar, 75),
        "csv_bar_trading_value_p90": _percentile(csv_bar, 90),
        "g7_reject_board_tv_p50": _percentile(tv_g7, 50),
        "g7_reject_board_tv_p75": _percentile(tv_g7, 75),
        "g7_reject_board_tv_p90": _percentile(tv_g7, 90),
        "below_threshold_pct_of_g7_rejects": (
            (acc.below_threshold_count / acc.g7_reject_count * 100.0) if acc.g7_reject_count else None
        ),
        "unknown_pct_of_evals": (acc.unknown_count / eval_n * 100.0) if acc.eval_count else None,
        "top_reject_is_data_quality_issue": is_data_quality,
        "possible_threshold_too_strict": possible_strict,
        "diagnosis_notes": ";".join(notes) if notes else "",
    }


def reject_detail_row(profile: str, reason: str, acc: G7DiagnosticAccumulator, count: int) -> dict[str, Any]:
    """Extended rejects_by_profile row."""
    row: dict[str, Any] = {
        "profile": profile,
        "reject_reason": reason,
        "count": count,
        "missing_count": "",
        "zero_count": "",
        "below_threshold_count": "",
        "p50": "",
        "p75": "",
        "p90": "",
        "threshold": "",
        "g7_source": acc.g7_source if reason.startswith("G7") else "",
    }
    if reason in ("G7_TRADING_VALUE", "G7_TRADING_VALUE_UNKNOWN"):
        g7 = summarize_g7(acc)
        row["threshold"] = g7["threshold"]
        if reason == "G7_TRADING_VALUE":
            row["missing_count"] = acc.missing_count
            row["zero_count"] = acc.zero_count
            row["below_threshold_count"] = acc.below_threshold_count
            row["p50"] = g7["g7_reject_board_tv_p50"]
            row["p75"] = g7["g7_reject_board_tv_p75"]
            row["p90"] = g7["g7_reject_board_tv_p90"]
        else:
            row["missing_count"] = acc.unknown_count
            row["p50"] = g7["board_trading_value_p50"]
            row["p75"] = g7["board_trading_value_p75"]
            row["p90"] = g7["board_trading_value_p90"]
    return row


def _load_entries_before(native_root: Path) -> dict[str, int]:
    """Phase 18 reference run entries (baseline / continuation_v1)."""
    ref = native_root / "results" / "research" / "logic_lab" / "20260517" / "run_033853" / "profile_summary.json"
    if not ref.is_file():
        return {}
    try:
        data = json.loads(ref.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, int] = {}
    for row in data.get("profiles_summary") or []:
        pname = str(row.get("profile", ""))
        if pname in ("baseline", "continuation_v1"):
            out[pname] = int(row.get("entry_signal_count") or row.get("entry_count") or 0)
    return out


def build_g7_definition_fix_report(
    *,
    profile_summaries: list[dict[str, Any]],
    g7_by_profile: dict[str, G7DiagnosticAccumulator],
    native_root: Path,
    num_days: int = 1,
) -> dict[str, Any]:
    entries_before = _load_entries_before(native_root)
    profiles: dict[str, Any] = {}
    for row in profile_summaries:
        pname = str(row.get("profile", ""))
        g7_acc = g7_by_profile.get(pname)
        if g7_acc is None or not g7_acc.eval_count:
            continue
        g7s = summarize_g7(g7_acc)
        entries_after = int(row.get("entry_signal_count") or row.get("entry_count") or 0)
        eb = entries_before.get(pname)
        profiles[pname] = {
            **g7s,
            "entries_before": eb,
            "entries_after": entries_after,
            "entries_per_day_before": (eb / num_days) if eb is not None and num_days > 0 else None,
            "entries_per_day_after": row.get("entries_per_day"),
            "g7_reject_count": g7_acc.g7_reject_count,
            "top_reject_reason": row.get("top_reject_reason"),
        }
    return {
        "phase": 19,
        "g7_definition": "session_cumulative_trading_value_on_board_TradingValue",
        "g7_threshold_yen": MIN_TRADING_VALUE,
        "entries_before_reference_run": "logic_lab/20260517/run_033853",
        "profiles": profiles,
    }
