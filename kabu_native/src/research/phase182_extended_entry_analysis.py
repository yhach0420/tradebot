"""
Phase182: Extended entry analysis — why quality 0.75–0.80 band underperforms (post-hoc review).

Hypothesis: high quality scores correlate with already-extended entries, not true edge.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.phase181_entry_expectancy_review import (
    ROLLING_MFE_CAP,
    _float,
    _mean,
    _parse_ts,
    _pf,
    _price_at_offset,
    _push_dir_for_day,
    build_entry_trade_rows,
)

# Fixed extended-entry flag thresholds (documented priors — NOT tuned on review day)
RISE_5MIN_PCT_THRESHOLD = 1.0
RISE_10MIN_PCT_THRESHOLD = 1.5
DISTANCE_DAY_HIGH_PCT_MAX = 0.5
VWAP_DEVIATION_PCT_MIN = 2.0
HIGH_BREAK_RECENT_SEC = 60.0

QUALITY_BANDS = (
    ("0.70_0.75", 0.70, 0.75),
    ("0.75_0.80", 0.75, 0.80),
    ("ge_0.80", 0.80, 1.01),
)


@dataclass
class EnrichedTick:
    ts: float
    px: float
    vwap: Optional[float]
    board_high: Optional[float]


def _load_enriched_series(push_dir: Path, symbol: str) -> list[EnrichedTick]:
    path = push_dir / f"{symbol}.jsonl"
    if not path.is_file():
        path = push_dir / f"{symbol.replace('.T', '')}.jsonl"
    if not path.is_file():
        return []
    out: list[EnrichedTick] = []
    last_px: Optional[float] = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(str(rec.get("recorded_at") or ""))
            payload = rec.get("payload") or {}
            px = _float(payload.get("CurrentPrice")) or _float(payload.get("CalcPrice"))
            if px is None or px <= 0:
                if last_px is not None:
                    px = last_px
                else:
                    continue
            last_px = px
            out.append(
                EnrichedTick(
                    ts=ts,
                    px=float(px),
                    vwap=_float(payload.get("VWAP")),
                    board_high=_float(payload.get("HighPrice")),
                )
            )
    out.sort(key=lambda x: x.ts)
    return out


def _tick_at_or_before(series: Sequence[EnrichedTick], ts: float) -> Optional[EnrichedTick]:
    found: Optional[EnrichedTick] = None
    for t in series:
        if t.ts <= ts:
            found = t
        else:
            break
    return found


def _price_before(series: Sequence[EnrichedTick], ts: float, lookback_sec: float) -> Optional[float]:
    target = ts - lookback_sec
    found: Optional[float] = None
    for t in series:
        if t.ts <= target:
            found = t.px
        elif t.ts > ts:
            break
    return found


def _rise_pct(entry_px: float, prior_px: Optional[float]) -> Optional[float]:
    if prior_px is None or prior_px <= 0 or entry_px <= 0:
        return None
    return (entry_px - prior_px) / prior_px * 100.0


def _high_break_5min_recent(series: Sequence[EnrichedTick], entry_ts: float, entry_px: float) -> bool:
    cur_window = [t for t in series if entry_ts - 300 <= t.ts <= entry_ts]
    prev_window = [t for t in series if entry_ts - 600 <= t.ts < entry_ts - 300]
    if len(cur_window) < 2 or len(prev_window) < 2:
        return False
    m5 = max(t.px for t in cur_window)
    m5_prev = max(t.px for t in prev_window)
    if m5 <= m5_prev * 1.0001:
        return False
    if entry_px < m5 * 0.998:
        return False
    last_high_ts = max(t.ts for t in cur_window if t.px >= m5 * 0.998)
    return (entry_ts - last_high_ts) <= HIGH_BREAK_RECENT_SEC


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = _mean(xs) or 0.0
    my = _mean(ys) or 0.0
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx <= 0 or dy <= 0:
        return None
    return num / (dx * dy)


def extended_entry_flag(metrics: Mapping[str, Any]) -> bool:
    """Fixed composite stretch detection (feature-based, not time-based)."""
    if (metrics.get("rolling_mfe_pct") or 0) >= ROLLING_MFE_CAP:
        return True
    if (metrics.get("rise_5min_pct") or 0) >= RISE_5MIN_PCT_THRESHOLD:
        return True
    if (metrics.get("rise_10min_pct") or 0) >= RISE_10MIN_PCT_THRESHOLD:
        return True
    dist = metrics.get("distance_from_day_high_pct")
    if dist is not None and dist <= DISTANCE_DAY_HIGH_PCT_MAX:
        return True
    if (metrics.get("vwap_deviation_pct") or 0) >= VWAP_DEVIATION_PCT_MIN:
        return True
    if metrics.get("high_break_5min_recent"):
        return True
    return False


def _summarize_pnls(pnls: Sequence[float]) -> dict[str, Any]:
    if not pnls:
        return {"trade_count": 0}
    pf = _pf(pnls)
    return {
        "trade_count": len(pnls),
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(_mean(pnls) or 0.0, 4),
        "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
    }


def _feature_means(rows: Sequence[dict[str, Any]], keys: Sequence[str]) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for k in keys:
        xs = [_float(r.get(k)) for r in rows if _float(r.get(k)) is not None]
        out[k] = round(_mean(xs), 4) if xs else None
    return out


def evaluate_extended_entry_analysis(
    session_dir: Path,
    *,
    repo_root: Path,
    day_stamp: str,
) -> dict[str, Any]:
    trades = build_entry_trade_rows(session_dir, repo_root=repo_root, day_stamp=day_stamp)
    push_dir = _push_dir_for_day(day_stamp, repo_root)
    cache: dict[str, list[EnrichedTick]] = {}

    enriched_rows: list[dict[str, Any]] = []
    for t in trades:
        sym = t.symbol
        if sym not in cache:
            cache[sym] = _load_enriched_series(push_dir, sym)
        series = cache[sym]
        tick = _tick_at_or_before(series, t.entry_ts)
        entry_px = t.entry_price or (tick.px if tick else 0.0)
        vwap = tick.vwap if tick else None
        board_high = tick.board_high if tick else None

        rise_5 = _rise_pct(entry_px, _price_before(series, t.entry_ts, 300))
        rise_10 = _rise_pct(entry_px, _price_before(series, t.entry_ts, 600))
        dist_high: Optional[float] = None
        if board_high and board_high > 0 and entry_px > 0:
            dist_high = (board_high - entry_px) / board_high * 100.0
        vwap_dev: Optional[float] = None
        if vwap and vwap > 0 and entry_px > 0:
            vwap_dev = (entry_px - vwap) / vwap * 100.0
        hb_recent = _high_break_5min_recent(series, t.entry_ts, entry_px)

        row = t.to_dict()
        row.update(
            {
                "rise_5min_pct": round(rise_5, 4) if rise_5 is not None else None,
                "rise_10min_pct": round(rise_10, 4) if rise_10 is not None else None,
                "distance_from_day_high_pct": round(dist_high, 4) if dist_high is not None else None,
                "vwap_deviation_pct": round(vwap_dev, 4) if vwap_dev is not None else None,
                "high_break_5min_recent": hb_recent,
                "board_high_at_entry": board_high,
                "vwap_at_entry": vwap,
            }
        )
        row["extended_entry_flag"] = extended_entry_flag(row)
        enriched_rows.append(row)

    q_vals = [_float(r.get("continuation_quality_score")) for r in enriched_rows]
    mfe_vals = [_float(r.get("rolling_mfe_pct")) for r in enriched_rows]
    mom_vals = [_float(r.get("momentum_continuation_score")) for r in enriched_rows]
    valid = [
        (q, m, mo)
        for q, m, mo in zip(q_vals, mfe_vals, mom_vals)
        if q is not None and m is not None and mo is not None
    ]

    correlations = {
        "quality_vs_rolling_mfe": round(_pearson([x[0] for x in valid], [x[1] for x in valid]) or 0, 4)
        if valid
        else None,
        "quality_vs_momentum": round(_pearson([x[0] for x in valid], [x[2] for x in valid]) or 0, 4)
        if valid
        else None,
        "rolling_mfe_vs_momentum": round(_pearson([x[1] for x in valid], [x[2] for x in valid]) or 0, 4)
        if valid
        else None,
        "sample_size": len(valid),
    }

    band_7580 = [
        r
        for r in enriched_rows
        if _float(r.get("continuation_quality_score")) is not None
        and 0.75 <= float(r["continuation_quality_score"]) < 0.80
    ]
    valid_b = [
        (
            _float(r["continuation_quality_score"]),
            _float(r["rolling_mfe_pct"]),
            _float(r["momentum_continuation_score"]),
        )
        for r in band_7580
        if _float(r.get("rolling_mfe_pct")) is not None
        and _float(r.get("momentum_continuation_score")) is not None
    ]
    correlations_7580 = {
        "quality_vs_rolling_mfe": round(_pearson([x[0] for x in valid_b], [x[1] for x in valid_b]) or 0, 4)
        if len(valid_b) >= 3
        else None,
        "quality_vs_momentum": round(_pearson([x[0] for x in valid_b], [x[2] for x in valid_b]) or 0, 4)
        if len(valid_b) >= 3
        else None,
        "sample_size": len(valid_b),
    }

    quality_band_expectancy: dict[str, Any] = {}
    for name, lo, hi in QUALITY_BANDS:
        grp = [
            r
            for r in enriched_rows
            if _float(r.get("continuation_quality_score")) is not None
            and lo <= float(r["continuation_quality_score"]) < hi
        ]
        pnls = [float(r["pnl_pct"]) for r in grp]
        ext_rate = sum(1 for r in grp if r.get("extended_entry_flag")) / max(1, len(grp))
        quality_band_expectancy[name] = {
            **_summarize_pnls(pnls),
            "extended_entry_rate": round(ext_rate, 4),
            "avg_r30_sec": round(
                _mean([_float(r.get("r30_sec")) for r in grp if _float(r.get("r30_sec")) is not None]) or 0,
                4,
            ),
            "avg_r60_sec": round(
                _mean([_float(r.get("r60_sec")) for r in grp if _float(r.get("r60_sec")) is not None]) or 0,
                4,
            ),
            "avg_r120_sec": round(
                _mean([_float(r.get("r120_sec")) for r in grp if _float(r.get("r120_sec")) is not None]) or 0,
                4,
            ),
            "avg_rolling_mfe_pct": round(
                _mean([_float(r.get("rolling_mfe_pct")) for r in grp if _float(r.get("rolling_mfe_pct")) is not None])
                or 0,
                4,
            ),
            "avg_rise_5min_pct": round(
                _mean([_float(r.get("rise_5min_pct")) for r in grp if _float(r.get("rise_5min_pct")) is not None])
                or 0,
                4,
            ),
            "avg_vwap_deviation_pct": round(
                _mean(
                    [_float(r.get("vwap_deviation_pct")) for r in grp if _float(r.get("vwap_deviation_pct")) is not None]
                )
                or 0,
                4,
            ),
        }

    high_q_losers = [
        r
        for r in enriched_rows
        if (_float(r.get("continuation_quality_score")) or 0) >= 0.75 and float(r.get("pnl_pct") or 0) < 0
    ]
    high_q_winners = [
        r
        for r in enriched_rows
        if (_float(r.get("continuation_quality_score")) or 0) >= 0.75 and float(r.get("pnl_pct") or 0) > 0
    ]

    loser_features = _feature_means(
        high_q_losers,
        (
            "continuation_quality_score",
            "rolling_mfe_pct",
            "momentum_continuation_score",
            "rise_5min_pct",
            "rise_10min_pct",
            "distance_from_day_high_pct",
            "vwap_deviation_pct",
            "r30_sec",
            "r60_sec",
        ),
    )
    loser_features["extended_entry_flag_rate"] = round(
        sum(1 for r in high_q_losers if r.get("extended_entry_flag")) / max(1, len(high_q_losers)),
        4,
    )
    loser_features["high_break_5min_recent_rate"] = round(
        sum(1 for r in high_q_losers if r.get("high_break_5min_recent")) / max(1, len(high_q_losers)),
        4,
    )

    winner_features = _feature_means(
        high_q_winners,
        (
            "continuation_quality_score",
            "rolling_mfe_pct",
            "momentum_continuation_score",
            "rise_5min_pct",
            "rise_10min_pct",
            "distance_from_day_high_pct",
            "vwap_deviation_pct",
            "r30_sec",
            "r60_sec",
        ),
    )
    winner_features["extended_entry_flag_rate"] = round(
        sum(1 for r in high_q_winners if r.get("extended_entry_flag")) / max(1, len(high_q_winners)),
        4,
    )

    all_pnls = [float(r["pnl_pct"]) for r in enriched_rows]
    kept_b = [r for r in enriched_rows if not r.get("extended_entry_flag")]
    excl_b = [r for r in enriched_rows if r.get("extended_entry_flag")]

    band_pnls_a = [float(r["pnl_pct"]) for r in band_7580]
    band_kept_b = [r for r in band_7580 if not r.get("extended_entry_flag")]
    band_excl_b = [r for r in band_7580 if r.get("extended_entry_flag")]

    scenarios = {
        "A": {
            "description": "current (all accepted)",
            "all_trades": _summarize_pnls(all_pnls),
            "quality_0.75_0.80": _summarize_pnls(band_pnls_a),
        },
        "B": {
            "description": "exclude extended_entry_flag (stretch composite)",
            "all_trades": _summarize_pnls([float(r["pnl_pct"]) for r in kept_b]),
            "quality_0.75_0.80": _summarize_pnls([float(r["pnl_pct"]) for r in band_kept_b]),
            "excluded_count": len(excl_b),
            "excluded_total_pnl_pct": round(sum(float(r["pnl_pct"]) for r in excl_b), 4),
            "excluded_in_0.75_0.80": len(band_excl_b),
            "delta_total_pnl_vs_A": round(
                sum(float(r["pnl_pct"]) for r in kept_b) - sum(all_pnls),
                4,
            ),
            "delta_pf_0.75_0.80": None,
        },
    }
    pf_a = _summarize_pnls(band_pnls_a).get("profit_factor")
    pf_b = _summarize_pnls([float(r["pnl_pct"]) for r in band_kept_b]).get("profit_factor")
    if isinstance(pf_a, (int, float)) and isinstance(pf_b, (int, float)):
        scenarios["B"]["delta_pf_0.75_0.80"] = round(float(pf_b) - float(pf_a), 4)

    comp_counter: Counter[str] = Counter()
    for r in enriched_rows:
        if (r.get("rolling_mfe_pct") or 0) >= ROLLING_MFE_CAP:
            comp_counter["rolling_mfe_cap"] += 1
        if (r.get("rise_5min_pct") or 0) >= RISE_5MIN_PCT_THRESHOLD:
            comp_counter["rise_5min"] += 1
        if (r.get("rise_10min_pct") or 0) >= RISE_10MIN_PCT_THRESHOLD:
            comp_counter["rise_10min"] += 1
        dist = r.get("distance_from_day_high_pct")
        if dist is not None and dist <= DISTANCE_DAY_HIGH_PCT_MAX:
            comp_counter["near_day_high"] += 1
        if (r.get("vwap_deviation_pct") or 0) >= VWAP_DEVIATION_PCT_MIN:
            comp_counter["above_vwap"] += 1
        if r.get("high_break_5min_recent"):
            comp_counter["high_break_5min_recent"] += 1

    band_7580_ext_rate = sum(1 for r in band_7580 if r.get("extended_entry_flag")) / max(1, len(band_7580))
    other_ext_rate = sum(
        1 for r in enriched_rows if r not in band_7580 and r.get("extended_entry_flag")
    ) / max(1, len(enriched_rows) - len(band_7580))

    candidates = _derive_improvement_candidates(
        correlations=correlations,
        correlations_7580=correlations_7580,
        quality_band_expectancy=quality_band_expectancy,
        loser_features=loser_features,
        winner_features=winner_features,
        scenarios=scenarios,
        band_7580_ext_rate=band_7580_ext_rate,
    )

    summary_path = session_dir / "small_paper_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}

    return {
        "phase": 182,
        "mode": "extended_entry_analysis_post_hoc",
        "hypothesis": "High quality band rewards already-extended entries rather than fresh continuation.",
        "day_stamp": day_stamp,
        "session_dir": str(session_dir).replace("\\", "/"),
        "session_summary_snippet": {
            "accepted_count": summary.get("accepted_count"),
            "structural_exit_reason_counts": summary.get("structural_exit_reason_counts"),
        },
        "trade_count": len(enriched_rows),
        "fixed_thresholds": {
            "ROLLING_MFE_CAP": ROLLING_MFE_CAP,
            "RISE_5MIN_PCT_THRESHOLD": RISE_5MIN_PCT_THRESHOLD,
            "RISE_10MIN_PCT_THRESHOLD": RISE_10MIN_PCT_THRESHOLD,
            "DISTANCE_DAY_HIGH_PCT_MAX": DISTANCE_DAY_HIGH_PCT_MAX,
            "VWAP_DEVIATION_PCT_MIN": VWAP_DEVIATION_PCT_MIN,
            "HIGH_BREAK_RECENT_SEC": HIGH_BREAK_RECENT_SEC,
            "note": "Fixed priors only; not tuned on review day.",
        },
        "entry_feature_correlations": correlations,
        "entry_feature_correlations_quality_0.75_0.80": correlations_7580,
        "extended_flag_component_hits": dict(comp_counter),
        "quality_band_expectancy": quality_band_expectancy,
        "quality_0.75_0.80_deep_dive": {
            "trade_count": len(band_7580),
            "extended_entry_rate": round(band_7580_ext_rate, 4),
            "other_bands_extended_rate": round(other_ext_rate, 4),
            "profit_factor": _summarize_pnls(band_pnls_a).get("profit_factor"),
            "avg_rolling_mfe_pct": round(
                _mean(
                    [_float(r.get("rolling_mfe_pct")) for r in band_7580 if _float(r.get("rolling_mfe_pct")) is not None]
                )
                or 0,
                4,
            ),
            "avg_rise_5min_pct": round(
                _mean([_float(r.get("rise_5min_pct")) for r in band_7580 if _float(r.get("rise_5min_pct")) is not None])
                or 0,
                4,
            ),
            "avg_distance_from_day_high_pct": round(
                _mean(
                    [
                        _float(r.get("distance_from_day_high_pct"))
                        for r in band_7580
                        if _float(r.get("distance_from_day_high_pct")) is not None
                    ]
                )
                or 0,
                4,
            ),
            "trades": band_7580,
        },
        "high_quality_loser_common_features": loser_features,
        "high_quality_winner_common_features": winner_features,
        "post_hoc_scenarios": scenarios,
        "entry_quality_improvement_candidates": candidates,
        "trades": enriched_rows,
        "constraints": {
            "no_time_based_cooldown": True,
            "feature_based_only": True,
            "review_only": True,
            "no_parameter_search": True,
        },
    }


def _derive_improvement_candidates(
    *,
    correlations: dict[str, Any],
    correlations_7580: dict[str, Any],
    quality_band_expectancy: dict[str, Any],
    loser_features: dict[str, Any],
    winner_features: dict[str, Any],
    scenarios: dict[str, Any],
    band_7580_ext_rate: float,
) -> list[dict[str, Any]]:
    """Narrow to 1–3 feature-based shadow logging / gate hypotheses."""
    band = quality_band_expectancy.get("0.75_0.80") or {}
    pf_a = band.get("profit_factor")
    pf_b = ((scenarios.get("B") or {}).get("quality_0.75_0.80") or {}).get("profit_factor")
    delta_pf = (float(pf_b) - float(pf_a)) if isinstance(pf_a, (int, float)) and isinstance(pf_b, (int, float)) else None

    candidates: list[dict[str, Any]] = []

    c_q_mfe_all = correlations.get("quality_vs_rolling_mfe")
    if (c_q_mfe_all is not None and c_q_mfe_all > 0.25) or band_7580_ext_rate >= 0.75:
        candidates.append(
            {
                "id": 1,
                "name": "extended_entry_flag_shadow",
                "feature_basis": "extended_entry_flag composite (rolling_mfe, rise_5min/10min, vwap_dev, near_day_high, high_break)",
                "evidence": (
                    f"session quality↔rolling_mfe r={c_q_mfe_all}; "
                    f"0.75-0.80 extended_rate={band_7580_ext_rate:.2f}, PF={pf_a}→{pf_b} post-hoc B"
                ),
                "shadow_action": "Log extended_entry_flag + components on every accept; shadow-only penalty (no hard reject)",
                "post_hoc_delta_pf_0.75_0.80": delta_pf,
            }
        )

    c_q_mom = correlations_7580.get("quality_vs_momentum")
    if c_q_mom is not None and c_q_mom < -0.4:
        candidates.append(
            {
                "id": 2,
                "name": "high_quality_low_momentum_shadow_flag",
                "feature_basis": "continuation_quality high AND momentum_continuation low at entry",
                "evidence": f"0.75-0.80 band quality↔momentum r={c_q_mom} (high q with fading momentum)",
                "shadow_action": "Log quality_minus_momentum_gap; flag when q≥0.75 and momentum below session median",
                "note": "Feature interaction, not time cooldown",
            }
        )

    loser_r30 = loser_features.get("r30_sec")
    winner_r30 = winner_features.get("r30_sec")
    if loser_r30 is not None and winner_r30 is not None and loser_r30 < winner_r30:
        candidates.append(
            {
                "id": 3,
                "name": "extended_plus_early_adverse_shadow",
                "feature_basis": "extended_entry_flag AND entry-forward r30<0 (replay diagnostic)",
                "evidence": (
                    f"high-q losers r30={loser_r30} vs winners {winner_r30}; "
                    f"extended_flag_rate losers={loser_features.get('extended_entry_flag_rate')}"
                ),
                "shadow_action": "Shadow-log r30_sec at accept; flag stretch+immediate adverse combo",
                "note": "Diagnoses timing miss after extended entry — not re-entry cooldown",
            }
        )

    if len(candidates) > 3:
        candidates = candidates[:3]
    return candidates
