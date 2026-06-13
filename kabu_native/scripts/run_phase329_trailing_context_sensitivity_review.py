#!/usr/bin/env python3
"""
Phase329-lite: trailing_mfe context sensitivity — do market indicators suggest
dynamic activate/giveback?

Output: phase329_trailing_context_sensitivity_review.json
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase329_trailing_context_sensitivity_review.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")

SESSIONS = {
    "am": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_080642",
    "pm": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_122548",
}

TARGET_REASONS = frozenset({"trailing_mfe_exit", "stop_hit"})

SPLIT_DIMENSIONS = {
    "vwap": {
        "label": "VWAP",
        "field": "entry_vwap_dev_pct",
        "high_label": "VWAP高",
        "low_label": "VWAP低",
    },
    "board": {
        "label": "板imbalance",
        "field": "entry_imbalance_percentile",
        "high_label": "板高",
        "low_label": "板低",
    },
    "quality": {
        "label": "quality",
        "field": "continuation_quality_score",
        "high_label": "quality高",
        "low_label": "quality低",
    },
}


@dataclass
class Trade:
    session: str
    symbol: str
    entry_time: str
    exit_reason: str
    entry_price: float
    exit_price: float
    entry_vwap_dev_pct: Optional[float]
    entry_imbalance_percentile: Optional[float]
    trading_value: Optional[float]
    continuation_quality_score: Optional[float]
    peak_mfe_pct: float
    realized_pnl_pct: float
    pnl_yen_100: float


def _bootstrap() -> None:
    src = REPO / "kabu_native" / "src"
    for p in (src, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _median(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    return float(statistics.median(vals))


def _load_trades() -> list[Trade]:
    from replay.pnl_yen import compute_pnl_yen_100

    out: list[Trade] = []
    for session_label, session_dir in SESSIONS.items():
        path = session_dir / "small_paper_events.csv"
        if not path.is_file():
            continue
        accepted: dict[tuple[str, str], dict[str, str]] = {}
        exits: list[dict[str, str]] = []
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                et = row.get("event_type") or ""
                sym = str(row.get("symbol") or "")
                ent = str(row.get("entry_time") or "")
                if et == "accepted":
                    accepted[(sym, ent)] = row
                elif et == "observer_exit":
                    reason = str(row.get("exit_reason") or "")
                    if reason in TARGET_REASONS:
                        exits.append(row)

        for row in exits:
            acc = accepted.get((str(row.get("symbol") or ""), str(row.get("entry_time") or "")), {})
            entry = _float(row.get("entry_price")) or 0.0
            exit_p = _float(row.get("exit_price")) or _float(row.get("current_price")) or 0.0
            pnl = _float(row.get("pnl_pct"))
            if pnl is None and entry > 0:
                pnl = (exit_p - entry) / entry * 100.0
            out.append(
                Trade(
                    session=session_label,
                    symbol=str(row.get("symbol") or ""),
                    entry_time=str(row.get("entry_time") or ""),
                    exit_reason=str(row.get("exit_reason") or ""),
                    entry_price=entry,
                    exit_price=exit_p,
                    entry_vwap_dev_pct=_float(row.get("entry_vwap_dev_pct")),
                    entry_imbalance_percentile=_float(row.get("entry_imbalance_percentile")),
                    trading_value=_float(acc.get("trading_value")),
                    continuation_quality_score=_float(acc.get("continuation_quality_score")),
                    peak_mfe_pct=float(
                        _float(row.get("peak_mfe_pct"))
                        or _float(row.get("rolling_mfe_pct"))
                        or 0.0
                    ),
                    realized_pnl_pct=float(pnl or 0.0),
                    pnl_yen_100=compute_pnl_yen_100(entry, exit_p),
                )
            )
    return out


def _field_value(trade: Trade, field: str) -> Optional[float]:
    return getattr(trade, field, None)


def _cohort_stats(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "avg_peak_mfe_pct": None,
            "avg_realized_pnl_pct": None,
            "avg_pnl_yen_100": None,
            "total_pnl_yen_100": 0.0,
            "median_peak_mfe_pct": None,
            "median_realized_pnl_pct": None,
            "avg_capture_ratio": None,
        }
    peaks = [t.peak_mfe_pct for t in trades]
    pnls = [t.realized_pnl_pct for t in trades]
    yens = [t.pnl_yen_100 for t in trades]
    captures = [
        t.realized_pnl_pct / t.peak_mfe_pct
        for t in trades
        if t.peak_mfe_pct > 0.01
    ]
    return {
        "trade_count": len(trades),
        "avg_peak_mfe_pct": round(statistics.mean(peaks), 4),
        "avg_realized_pnl_pct": round(statistics.mean(pnls), 4),
        "avg_pnl_yen_100": round(statistics.mean(yens), 2),
        "total_pnl_yen_100": round(sum(yens), 2),
        "median_peak_mfe_pct": round(statistics.median(peaks), 4),
        "median_realized_pnl_pct": round(statistics.median(pnls), 4),
        "avg_capture_ratio": round(statistics.mean(captures), 4) if captures else None,
    }


def _split_trailing(
    trailing: list[Trade],
    *,
    dim_key: str,
    field: str,
) -> dict[str, Any]:
    vals = [_field_value(t, field) for t in trailing]
    clean = [v for v in vals if v is not None]
    if not clean:
        return {"dimension": dim_key, "split_threshold": None, "high": {}, "low": {}}

    threshold = _median(clean)
    high = [t for t in trailing if _field_value(t, field) is not None and _field_value(t, field) >= threshold]
    low = [t for t in trailing if _field_value(t, field) is not None and _field_value(t, field) < threshold]

    dim = SPLIT_DIMENSIONS[dim_key]
    return {
        "dimension": dim_key,
        "label": dim["label"],
        "field": field,
        "split_method": "median_within_trailing_mfe_exit",
        "split_threshold": round(threshold, 4) if threshold is not None else None,
        "high": {
            "bucket": dim["high_label"],
            **(_cohort_stats(high)),
        },
        "low": {
            "bucket": dim["low_label"],
            **(_cohort_stats(low)),
        },
        "high_vs_low_delta": {
            "avg_peak_mfe_pct": round(
                (_cohort_stats(high).get("avg_peak_mfe_pct") or 0)
                - (_cohort_stats(low).get("avg_peak_mfe_pct") or 0),
                4,
            ),
            "avg_realized_pnl_pct": round(
                (_cohort_stats(high).get("avg_realized_pnl_pct") or 0)
                - (_cohort_stats(low).get("avg_realized_pnl_pct") or 0),
                4,
            ),
            "avg_pnl_yen_100": round(
                (_cohort_stats(high).get("avg_pnl_yen_100") or 0)
                - (_cohort_stats(low).get("avg_pnl_yen_100") or 0),
                2,
            ),
            "avg_capture_ratio": (
                round(
                    (_cohort_stats(high).get("avg_capture_ratio") or 0)
                    - (_cohort_stats(low).get("avg_capture_ratio") or 0),
                    4,
                )
                if _cohort_stats(high).get("avg_capture_ratio") is not None
                else None
            ),
        },
    }


def _trailing_vs_stop_by_dimension(
    trailing: list[Trade],
    stop_hit: list[Trade],
    *,
    field: str,
) -> dict[str, Any]:
    trail_vals = [_field_value(t, field) for t in trailing if _field_value(t, field) is not None]
    stop_vals = [_field_value(t, field) for t in stop_hit if _field_value(t, field) is not None]
    return {
        "trailing_mfe_exit_mean": round(statistics.mean(trail_vals), 4) if trail_vals else None,
        "stop_hit_mean": round(statistics.mean(stop_vals), 4) if stop_vals else None,
        "mean_delta_trailing_minus_stop": round(
            statistics.mean(trail_vals) - statistics.mean(stop_vals), 4
        )
        if trail_vals and stop_vals
        else None,
        "trailing_mfe_exit_median": round(_median(trail_vals), 4) if trail_vals else None,
        "stop_hit_median": round(_median(stop_vals), 4) if stop_vals else None,
    }


def _dimension_score(split_block: dict[str, Any]) -> dict[str, float]:
    """Higher score = stronger context split within trailing winners."""
    delta = split_block.get("high_vs_low_delta") or {}
    peak_spread = abs(float(delta.get("avg_peak_mfe_pct") or 0))
    pnl_spread = abs(float(delta.get("avg_pnl_yen_100") or 0))
    capture_spread = abs(float(delta.get("avg_capture_ratio") or 0))
    return {
        "activate_relevance": peak_spread,
        "giveback_relevance": capture_spread + pnl_spread / 1000.0,
        "combined": peak_spread + capture_spread + pnl_spread / 2000.0,
    }


def _pick_candidate(scores: dict[str, dict[str, float]], key: str) -> tuple[str, dict[str, Any]]:
    ranked = sorted(scores.items(), key=lambda kv: float(kv[1].get(key) or 0), reverse=True)
    best_dim, best_score = ranked[0]
    second_dim, second_score = ranked[1] if len(ranked) > 1 else (None, 0)
    threshold = 0.05 if key == "activate_relevance" else 0.03
    if float(best_score.get(key) or 0) < threshold:
        return "none", {
            "reason": f"no dimension shows meaningful {key} spread within trailing_mfe_exit",
            "scores": scores,
        }
    if second_dim and abs(float(best_score.get(key) or 0) - float(second_score.get(key) or 0)) < threshold * 0.5:
        return best_dim, {
            "reason": f"weak lead; {best_dim} marginally best for {key}",
            "winner_score": best_score.get(key),
            "runner_up": second_dim,
            "scores": scores,
        }
    return best_dim, {
        "reason": f"{best_dim} shows strongest {key} split in trailing_mfe_exit cohort",
        "winner_score": best_score.get(key),
        "scores": scores,
    }


def _indicator_summary(trades: list[Trade]) -> dict[str, Any]:
    fields = [
        "entry_vwap_dev_pct",
        "entry_imbalance_percentile",
        "trading_value",
        "continuation_quality_score",
        "peak_mfe_pct",
        "realized_pnl_pct",
    ]
    block: dict[str, Any] = {}
    for f in fields:
        vals = [_field_value(t, f) if f != "peak_mfe_pct" and f != "realized_pnl_pct" else getattr(t, f) for t in trades]
        if f == "trading_value":
            vals = [math.log10(v) if v and v > 0 else None for v in vals]
        clean = [v for v in vals if v is not None]
        block[f] = {
            "n": len(clean),
            "mean": round(statistics.mean(clean), 4) if clean else None,
            "median": round(statistics.median(clean), 4) if clean else None,
        }
    block["pnl_yen_100"] = {
        "mean": round(statistics.mean(t.pnl_yen_100 for t in trades), 2),
        "total": round(sum(t.pnl_yen_100 for t in trades), 2),
    }
    return block


def main() -> int:
    _bootstrap()
    trades = _load_trades()
    trailing = [t for t in trades if t.exit_reason == "trailing_mfe_exit"]
    stop_hit = [t for t in trades if t.exit_reason == "stop_hit"]

    if len(trailing) != 46 or len(stop_hit) != 41:
        print(f"warn: trailing={len(trailing)} stop={len(stop_hit)}", file=sys.stderr)

    splits: dict[str, Any] = {}
    dim_scores: dict[str, dict[str, float]] = {}
    for dim_key, meta in SPLIT_DIMENSIONS.items():
        block = _split_trailing(trailing, dim_key=dim_key, field=meta["field"])
        splits[dim_key] = block
        dim_scores[dim_key] = _dimension_score(block)

    activate_candidate, activate_detail = _pick_candidate(dim_scores, "activate_relevance")
    giveback_candidate, giveback_detail = _pick_candidate(dim_scores, "giveback_relevance")

    report = {
        "phase": 329,
        "title": "trailing_context_sensitivity_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "analysis only; no entry/stop/exit changes; no new rules",
        "target_date": DAY,
        "cohorts": {
            "trailing_mfe_exit": {
                "trade_count": len(trailing),
                "total_pnl_yen_100": round(sum(t.pnl_yen_100 for t in trailing), 2),
                "indicators": _indicator_summary(trailing),
                "aggregate": _cohort_stats(trailing),
            },
            "stop_hit": {
                "trade_count": len(stop_hit),
                "total_pnl_yen_100": round(sum(t.pnl_yen_100 for t in stop_hit), 2),
                "indicators": _indicator_summary(stop_hit),
                "aggregate": _cohort_stats(stop_hit),
            },
        },
        "trailing_mfe_exit_context_splits": splits,
        "trailing_vs_stop_hit_indicator_comparison": {
            dim_key: _trailing_vs_stop_by_dimension(trailing, stop_hit, field=meta["field"])
            for dim_key, meta in SPLIT_DIMENSIONS.items()
        },
        "dimension_scores": dim_scores,
        "verdict": {
            "activate_dynamic_candidate": activate_candidate,
            "giveback_dynamic_candidate": giveback_candidate,
            "activate_detail": activate_detail,
            "giveback_detail": giveback_detail,
            "conclusion": _conclusion(activate_candidate, giveback_candidate, trailing, stop_hit, splits),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"trailing={len(trailing)} stop={len(stop_hit)} "
        f"activate={activate_candidate} giveback={giveback_candidate}"
    )
    return 0


def _conclusion(
    activate: str,
    giveback: str,
    trailing: list[Trade],
    stop_hit: list[Trade],
    splits: dict[str, Any],
) -> str:
    trail_yen = sum(t.pnl_yen_100 for t in trailing)
    if activate == "none" and giveback == "none":
        return (
            f"trailing_mfe_exit ({len(trailing)} trades, {trail_yen:+.0f} yen) shows no strong "
            "context split for dynamic activate/giveback on this day"
        )
    parts = []
    if activate != "none":
        sp = splits.get(activate, {}).get("high_vs_low_delta", {})
        parts.append(
            f"activate candidate={activate} (peak spread {sp.get('avg_peak_mfe_pct')})"
        )
    if giveback != "none":
        sp = splits.get(giveback, {}).get("high_vs_low_delta", {})
        parts.append(
            f"giveback candidate={giveback} (capture/yen spread {sp.get('avg_capture_ratio')}/{sp.get('avg_pnl_yen_100')})"
        )
    return "; ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
