"""
Phase381: Winner profile review — Period B profit source analysis (Stack C).

Focus on what makes winners, not cutting losers. Shadow only, no production changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase360_eother_classification import entry_time_bucket
from research.phase374_dynamic40_universe_quality_review import (
    _norm_symbol,
    _universe_path_candidates,
    dynamic40_monitored_from_universe,
    load_universe_csv,
    rank_bucket,
)
from research.phase377_daily_regime_breakdown import PERIOD_B_END, PERIOD_B_START, PRIMARY_STACK
from research.phase379_380_period_b_eval import (
    cohens_d,
    evaluate_variant_shadow,
    in_period_b,
    is_low_mfe_stop,
    production_candidate_pass,
)
from research.phase379_low_mfe_stophit_deep_review import load_session_period_b_trades
from research.phase367_low_mfe_residual_forensic import enrich_residual_trade

JST = ZoneInfo("Asia/Tokyo")
LOW_MFE_THRESHOLD_PCT = 0.3
REFERENCE_FEATURES = (
    "entry_momentum_score",
    "entry_rise_1min_pct",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_vwap_dev_pct",
    "day_high_distance_pct",
    "price_range_position",
    "entry_imbalance_percentile",
    "trading_value",
    "turnover_proxy",
    "hold_sec",
    "peak_mfe_pct",
    "peak_mae_pct",
)

MFE_BANDS = (
    ("peak_mfe_lt_0.3", None, 0.3),
    ("peak_mfe_0.3_to_0.6", 0.3, 0.6),
    ("peak_mfe_0.6_to_1.0", 0.6, 1.0),
    ("peak_mfe_ge_1.0", 1.0, None),
)

HOLD_BANDS = (
    ("lt_60s", 0.0, 60.0),
    ("60_to_300s", 60.0, 300.0),
    ("300_to_900s", 300.0, 900.0),
    ("ge_900s", 900.0, None),
)

WINNER_TYPES = (
    ("trend_follow_winner", "trailing_mfe_exit", 0.6),
    ("overlap_winner", "overlap_replaced", None),
    ("quick_winner", None, None),
    ("late_winner", None, None),
    ("high_range_winner", None, None),
    ("low_board_winner", None, None),
)

FEATURES_CSV_FIELDS = [
    "feature",
    "cohort",
    "count",
    "missing_count",
    "mean",
    "median",
    "p25",
    "p75",
]

TOP_TRADES_FIELDS = [
    "rank",
    "top_n_set",
    "day_key",
    "symbol",
    "universe_group",
    "dynamic40_rank_bucket",
    "session_kind",
    "entry_time",
    "time_bucket",
    "pnl_yen_100",
    "pnl_pct",
    "exit_reason_canonical",
    "peak_mfe_pct",
    "peak_mae_pct",
    "hold_seconds",
    "entry_momentum_score",
    "entry_rise_5min_pct",
    "entry_vwap_dev_pct",
    "entry_imbalance_percentile",
    "board_dynamic_tier",
    "price_range_position",
]

COMPARE_FIELDS = [
    "feature",
    "winning_mean",
    "winning_median",
    "low_mfe_stop_mean",
    "low_mfe_stop_median",
    "mean_diff",
    "median_diff",
    "effect_size",
    "direction",
    "reference_only",
]

BY_EXIT_FIELDS = ["cohort", "exit_reason_canonical", "trade_count", "total_pnl_yen_100", "share_of_pnl", "share_of_count"]
BY_UNIVERSE_FIELDS = ["cohort", "universe_group", "trade_count", "total_pnl_yen_100", "share_of_pnl", "win_rate"]
BY_TIME_FIELDS = ["cohort", "time_bucket", "trade_count", "total_pnl_yen_100", "share_of_pnl", "win_rate"]
BY_RANK_FIELDS = ["cohort", "rank_bucket", "trade_count", "total_pnl_yen_100", "share_of_pnl", "win_rate"]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _feature_val(trade: Mapping[str, Any], feature: str) -> Optional[float]:
    if feature == "hold_sec":
        return _float(trade.get("hold_sec")) or _float(trade.get("hold_duration_sec"))
    if feature == "entry_rise_1min_pct":
        return _float(trade.get("entry_rise_1min_pct"))
    return _float(trade.get(feature))


def _board_tier(trade: Mapping[str, Any]) -> str:
    return str(trade.get("board_dynamic_tier") or trade.get("board_dynamic_trailing_tier") or "unknown")


def enrich_trade_with_rank(
    trade: Mapping[str, Any],
    acc: Mapping[str, str],
    *,
    session_meta: Mapping[str, Any],
    reports_dir: Path,
) -> dict[str, Any]:
    from small_paper.limit_up_proximity_entry_guard_shadow import _infer_session_kind, _load_session_summary

    row = enrich_residual_trade(trade, acc)
    sess_dir = Path(str(session_meta["session_dir"]))
    day = str(row.get("day_key") or session_meta.get("day_key") or "")
    session_kind = str(row.get("session_kind") or session_meta.get("session_kind") or "")
    summary = session_meta.get("summary") or _load_session_summary(sess_dir)
    universe_all: dict[str, dict[str, str]] = {}
    for candidate in _universe_path_candidates(day, session_kind, summary, reports_dir):
        universe_all = load_universe_csv(candidate)
        if universe_all:
            break
    mon = dynamic40_monitored_from_universe(universe_all)
    sym = _norm_symbol(str(row.get("symbol") or ""))
    dyn = mon.get(sym, {})
    row["dynamic_rank"] = dyn.get("dynamic_rank")
    row["dynamic40_rank_bucket"] = (
        dyn.get("rank_bucket") if str(row.get("universe_group") or "") == "dynamic40" else "core10_or_other"
    )
    row["time_bucket"] = row.get("entry_time_bucket") or entry_time_bucket(str(row.get("entry_time") or ""))
    row["board_dynamic_tier"] = _board_tier(row)
    row["hold_seconds"] = _float(row.get("hold_sec")) or _float(row.get("hold_duration_sec"))
    return row


def load_session_winner_profile_trades(
    session_meta: Mapping[str, Any], *, reports_dir: Path
) -> dict[str, Any]:
    from research.phase365_production_stack_validation import load_session_production_stack_trades
    from research.phase366_stophit_reclassification import production_kept_trades
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    day = str(session_meta.get("day_key") or session_meta.get("day") or "")
    if not in_period_b(day):
        return {"error": "outside_period_b", "trades": [], "all_trades": []}

    base = load_session_production_stack_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {**base, "trades": [], "all_trades": []}

    sess_dir = Path(str(session_meta["session_dir"]))
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    for t in production_kept_trades(base):
        key = (t.get("symbol", ""), t.get("entry_time", ""))
        acc = accepted.get(key, {})
        row = enrich_trade_with_rank(t, acc, session_meta=session_meta, reports_dir=reports_dir)
        row["day_key"] = day
        trades.append(row)

    return {
        **base,
        "day_key": day,
        "all_trades": trades,
        "trade_count": len(trades),
        "error": "",
    }


def is_winning(trade: Mapping[str, Any]) -> bool:
    yen = _float(trade.get("pnl_yen_100"))
    return yen is not None and yen > 0


def feature_stats(trades: Sequence[Mapping[str, Any]], *, cohort: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in REFERENCE_FEATURES:
        vals = [v for t in trades if (v := _feature_val(t, feat)) is not None]
        missing = len(trades) - len(vals)
        if not vals:
            rows.append(
                {
                    "feature": feat,
                    "cohort": cohort,
                    "count": 0,
                    "missing_count": missing,
                    "mean": None,
                    "median": None,
                    "p25": None,
                    "p75": None,
                }
            )
            continue
        qs = statistics.quantiles(vals, n=4) if len(vals) >= 4 else [min(vals), statistics.median(vals), max(vals)]
        rows.append(
            {
                "feature": feat,
                "cohort": cohort,
                "count": len(vals),
                "missing_count": missing,
                "mean": round(statistics.mean(vals), 4),
                "median": round(statistics.median(vals), 4),
                "p25": round(qs[0], 4) if len(vals) >= 4 else round(min(vals), 4),
                "p75": round(qs[2], 4) if len(vals) >= 4 else round(max(vals), 4),
            }
        )
    return rows


def compare_winning_vs_low_mfe(
    winning: Sequence[Mapping[str, Any]], low_mfe: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in REFERENCE_FEATURES:
        wvals = [v for t in winning if (v := _feature_val(t, feat)) is not None]
        lvals = [v for t in low_mfe if (v := _feature_val(t, feat)) is not None]
        if not wvals or not lvals:
            continue
        wm, lm = statistics.mean(wvals), statistics.mean(lvals)
        wmed, lmed = statistics.median(wvals), statistics.median(lvals)
        effect = cohens_d(wvals, lvals)
        direction = "higher_in_winners" if wm > lm else "lower_in_winners" if wm < lm else "equal"
        rows.append(
            {
                "feature": feat,
                "winning_mean": round(wm, 4),
                "winning_median": round(wmed, 4),
                "low_mfe_stop_mean": round(lm, 4),
                "low_mfe_stop_median": round(lmed, 4),
                "mean_diff": round(wm - lm, 4),
                "median_diff": round(wmed - lmed, 4),
                "effect_size": effect,
                "direction": direction,
                "reference_only": feat == "peak_mfe_pct",
            }
        )
    rows.sort(key=lambda r: abs(_float(r.get("effect_size")) or 0.0), reverse=True)
    return rows


def _mfe_band(peak: Optional[float]) -> str:
    p = peak if peak is not None else 0.0
    for label, lo, hi in MFE_BANDS:
        if hi is None and lo is not None and p >= lo:
            return label
        if lo is not None and hi is not None and lo <= p < hi:
            return label
    return "peak_mfe_lt_0.3"


def _hold_band(hold: Optional[float]) -> str:
    if hold is None:
        return "unknown"
    h = float(hold)
    for label, lo, hi in HOLD_BANDS:
        if hi is None and h >= lo:
            return label
        if lo <= h < (hi if hi is not None else 1e18):
            return label
    return "ge_900s"


def classify_winner_type(trade: Mapping[str, Any]) -> Optional[str]:
    if not is_winning(trade):
        return None
    reason = str(trade.get("exit_reason_canonical") or "")
    peak = _float(trade.get("peak_mfe_pct")) or 0.0
    hold = _float(trade.get("hold_seconds"))
    pos = _float(trade.get("price_range_position")) or 0.0
    tier = _board_tier(trade)
    if reason == "trailing_mfe_exit" and peak >= 0.6:
        return "trend_follow_winner"
    if reason == "overlap_replaced":
        return "overlap_winner"
    if hold is not None and hold < 300:
        return "quick_winner"
    if hold is not None and hold >= 900:
        return "late_winner"
    if pos >= 0.7:
        return "high_range_winner"
    if tier == "board_low":
        return "low_board_winner"
    return None


def top_set_composition(trades: Sequence[Mapping[str, Any]], top_n: int) -> dict[str, Any]:
    sorted_trades = sorted(
        trades,
        key=lambda t: float(_float(t.get("pnl_yen_100")) or 0.0),
        reverse=True,
    )[:top_n]
    total_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in sorted_trades)
    exit_ctr = Counter(str(t.get("exit_reason_canonical") or "") for t in sorted_trades)
    uni_ctr = Counter(str(t.get("universe_group") or "") for t in sorted_trades)
    sk_ctr = Counter(str(t.get("session_kind") or "").lower() for t in sorted_trades)
    rank_ctr = Counter(str(t.get("dynamic40_rank_bucket") or "") for t in sorted_trades)
    mfe_ctr = Counter(_mfe_band(_float(t.get("peak_mfe_pct"))) for t in sorted_trades)
    hold_ctr = Counter(_hold_band(_float(t.get("hold_seconds"))) for t in sorted_trades)
    return {
        "top_n": top_n,
        "trade_count": len(sorted_trades),
        "total_pnl_yen_100": round(total_pnl, 2),
        "exit_reason_counts": dict(exit_ctr),
        "exit_reason_pnl": {
            k: round(
                sum(
                    float(_float(t.get("pnl_yen_100")) or 0.0)
                    for t in sorted_trades
                    if str(t.get("exit_reason_canonical") or "") == k
                ),
                2,
            )
            for k in exit_ctr
        },
        "dynamic40_share": round(uni_ctr.get("dynamic40", 0) / len(sorted_trades), 4) if sorted_trades else None,
        "core10_share": round(uni_ctr.get("core10", 0) / len(sorted_trades), 4) if sorted_trades else None,
        "am_share": round(sk_ctr.get("am", 0) / len(sorted_trades), 4) if sorted_trades else None,
        "pm_share": round(sk_ctr.get("pm", 0) / len(sorted_trades), 4) if sorted_trades else None,
        "rank_bucket_counts": dict(rank_ctr),
        "mfe_band_counts": dict(mfe_ctr),
        "hold_band_counts": dict(hold_ctr),
    }


def top_trade_rows(trades: Sequence[Mapping[str, Any]], top_n_sets: Sequence[int]) -> list[dict[str, Any]]:
    sorted_all = sorted(trades, key=lambda t: float(_float(t.get("pnl_yen_100")) or 0.0), reverse=True)
    rows: list[dict[str, Any]] = []
    for n in top_n_sets:
        for i, t in enumerate(sorted_all[:n], start=1):
            rows.append(
                {
                    "rank": i,
                    "top_n_set": n,
                    "day_key": t.get("day_key"),
                    "symbol": t.get("symbol"),
                    "universe_group": t.get("universe_group"),
                    "dynamic40_rank_bucket": t.get("dynamic40_rank_bucket"),
                    "session_kind": t.get("session_kind"),
                    "entry_time": t.get("entry_time"),
                    "time_bucket": t.get("time_bucket"),
                    "pnl_yen_100": _float(t.get("pnl_yen_100")),
                    "pnl_pct": _float(t.get("pnl_pct")),
                    "exit_reason_canonical": t.get("exit_reason_canonical"),
                    "peak_mfe_pct": _float(t.get("peak_mfe_pct")),
                    "peak_mae_pct": _float(t.get("peak_mae_pct")),
                    "hold_seconds": _float(t.get("hold_seconds")),
                    "entry_momentum_score": _float(t.get("entry_momentum_score")),
                    "entry_rise_5min_pct": _float(t.get("entry_rise_5min_pct")),
                    "entry_vwap_dev_pct": _float(t.get("entry_vwap_dev_pct")),
                    "entry_imbalance_percentile": _float(t.get("entry_imbalance_percentile")),
                    "board_dynamic_tier": _board_tier(t),
                    "price_range_position": _float(t.get("price_range_position")),
                }
            )
    return rows


def _rollup(
    trades: Sequence[Mapping[str, Any]],
    *,
    cohort: str,
    key_fn,
    key_name: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list] = defaultdict(list)
    for t in trades:
        groups[str(key_fn(t))].append(t)
    total_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades)
    total_n = len(trades)
    rows = []
    for key, grp in sorted(groups.items(), key=lambda x: sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in x[1]), reverse=True):
        pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in grp), 2)
        wins = sum(1 for t in grp if is_winning(t))
        rows.append(
            {
                "cohort": cohort,
                key_name: key,
                "trade_count": len(grp),
                "total_pnl_yen_100": pnl,
                "share_of_pnl": round(pnl / total_pnl, 4) if abs(total_pnl) > 1e-6 else None,
                "win_rate": round(wins / len(grp), 4) if grp else None,
                "share_of_count": round(len(grp) / total_n, 4) if total_n else None,
            }
        )
    return rows


def winner_profile_score(trade: Mapping[str, Any]) -> int:
    score = 0
    imb = _float(trade.get("entry_imbalance_percentile"))
    mom = _float(trade.get("entry_momentum_score"))
    rb = str(trade.get("dynamic40_rank_bucket") or "")
    if imb is not None and imb < 30:
        score += 2
    if mom is not None and mom >= 0.25:
        score += 1
    if rb in ("rank_21_30", "rank_31_40"):
        score += 2
    if _board_tier(trade) == "board_low":
        score += 1
    if str(trade.get("session_kind") or "").lower() == "pm":
        score += 1
    return score


def shadow_variants() -> list[dict[str, Any]]:
    return [
        {
            "variant_id": "A_preserve_rank_21_40_dynamic",
            "label": "Keep dynamic40 rank 21-40 only (remove rank 1-20 dynamic)",
            "block": lambda t: str(t.get("universe_group") or "") == "dynamic40"
            and str(t.get("dynamic40_rank_bucket") or "") in ("rank_1_10", "rank_11_20"),
        },
        {
            "variant_id": "B_preserve_pm_session",
            "label": "Remove AM trades (preserve PM profit source)",
            "block": lambda t: str(t.get("session_kind") or "").lower() == "am",
        },
        {
            "variant_id": "C_preserve_low_imbalance_winners",
            "label": "Remove dynamic40 with imbalance_pctile >= 70 (keep low-imbalance winners)",
            "block": lambda t: str(t.get("universe_group") or "") == "dynamic40"
            and (_float(t.get("entry_imbalance_percentile")) or 0.0) >= 70,
        },
        {
            "variant_id": "D_winner_profile_score_ge4",
            "label": "Remove trades with winner_profile_score < 4",
            "block": lambda t: winner_profile_score(t) < 4,
        },
        {
            "variant_id": "E_confirm_overlap_cut",
            "label": "Simulate cutting overlap_replaced (should hurt — preservation check)",
            "block": lambda t: str(t.get("exit_reason_canonical") or "") == "overlap_replaced",
        },
        {
            "variant_id": "F_confirm_trailing_cut",
            "label": "Simulate cutting trailing_mfe_exit (should hurt — preservation check)",
            "block": lambda t: str(t.get("exit_reason_canonical") or "") == "trailing_mfe_exit",
        },
    ]


def evaluate_shadow_variants(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in shadow_variants():
        metrics = evaluate_variant_shadow(trades, variant_id=spec["variant_id"], would_block=spec["block"])
        removed_losses = sum(
            1 for t in trades if spec["block"](t) and (_float(t.get("pnl_yen_100")) or 0.0) < 0
        )
        rows.append(
            {
                "variant_id": spec["variant_id"],
                "label": spec["label"],
                "added_or_kept_trade_count": len(trades) - metrics["removed_trade_count"],
                "missed_loss_count": removed_losses,
                **{k: metrics.get(k) for k in metrics if k != "day_deltas"},
            }
        )
    return rows


def build_report(summary: Mapping[str, Any]) -> str:
    j = summary.get("final_judgment") or {}
    lines = [
        "# Phase381 Winner Profile Review",
        "",
        f"**期間:** {PERIOD_B_START}–{PERIOD_B_END} | **Stack:** {PRIMARY_STACK}",
        "",
        "## 結論",
        "",
        f"- **勝ちトレード主因:** {j.get('primary_profit_driver')}",
        f"- **次に増やすべき特徴:** {j.get('expand_profile')}",
        f"- **消してはいけない特徴:** {j.get('preserve_profile')}",
        f"- **改善優先:** {j.get('priority_recommendation')}",
        "",
        f"- winning_count: {summary.get('winning_count')}",
        f"- trailing_mfe_exit_pnl: {j.get('trailing_mfe_pnl')}",
        f"- overlap_replaced_pnl: {j.get('overlap_pnl')}",
        f"- board_low_winner_count: {j.get('board_low_winner_count')}",
        "",
        "## 利益源分類",
        "",
    ]
    for k, v in (summary.get("winner_type_counts") or {}).items():
        lines.append(f"- {k}: count={v.get('count')} pnl={v.get('total_pnl_yen_100')}")
    lines.extend(["", "## Shadow variants", ""])
    for row in summary.get("shadow_variants") or []:
        lines.append(
            f"- {row.get('variant_id')}: delta={row.get('delta_yen')} "
            f"candidate={row.get('production_candidate')}"
        )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase381WinnerProfileReview:
    reports_dir: Path
    all_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase381_winner_profile_summary.json",
            "features": self.reports_dir / "phase381_winner_profile_features.csv",
            "top_trades": self.reports_dir / "phase381_winner_profile_top_trades.csv",
            "by_exit": self.reports_dir / "phase381_winner_profile_by_exit_reason.csv",
            "by_universe": self.reports_dir / "phase381_winner_profile_by_universe.csv",
            "by_time": self.reports_dir / "phase381_winner_profile_by_time_bucket.csv",
            "by_rank": self.reports_dir / "phase381_winner_profile_by_rank_bucket.csv",
            "report": self.reports_dir / "phase381_winner_profile_report.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.all_trades.extend(result.get("all_trades") or [])

    def analyze(self) -> dict[str, Any]:
        winning = [t for t in self.all_trades if is_winning(t)]
        low_mfe = [t for t in self.all_trades if is_low_mfe_stop(t)]
        trailing = [t for t in self.all_trades if str(t.get("exit_reason_canonical") or "") == "trailing_mfe_exit"]
        overlap = [t for t in self.all_trades if str(t.get("exit_reason_canonical") or "") == "overlap_replaced"]
        overlap_win = [t for t in overlap if is_winning(t)]

        feature_rows: list[dict[str, Any]] = []
        for label, subset in (
            ("winning", winning),
            ("all_accepted", self.all_trades),
            ("trailing_mfe_exit", trailing),
            ("overlap_replaced_winning", overlap_win),
            ("low_mfe_stop_hit", low_mfe),
        ):
            feature_rows.extend(feature_stats(subset, cohort=label))

        compare_rows = compare_winning_vs_low_mfe(winning, low_mfe)
        top_compositions = {str(n): top_set_composition(winning, n) for n in (10, 20, 50, 100)}
        top_rows = top_trade_rows(winning, (10, 20, 50, 100))

        winner_types: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "total_pnl_yen_100": 0.0})
        for t in winning:
            wt = classify_winner_type(t)
            if wt:
                winner_types[wt]["count"] += 1
                winner_types[wt]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
        for k in winner_types:
            winner_types[k]["total_pnl_yen_100"] = round(winner_types[k]["total_pnl_yen_100"], 2)

        by_exit = []
        for cohort, subset in (
            ("winning", winning),
            ("all_accepted", self.all_trades),
            ("top100_profit", sorted(winning, key=lambda t: float(_float(t.get("pnl_yen_100")) or 0.0), reverse=True)[:100]),
        ):
            by_exit.extend(
                _rollup(subset, cohort=cohort, key_fn=lambda t: t.get("exit_reason_canonical") or "other", key_name="exit_reason_canonical")
            )

        by_universe = []
        for cohort, subset in (("winning", winning), ("all_accepted", self.all_trades)):
            by_universe.extend(
                _rollup(subset, cohort=cohort, key_fn=lambda t: t.get("universe_group") or "other", key_name="universe_group")
            )

        by_time = []
        for cohort, subset in (("winning", winning), ("all_accepted", self.all_trades)):
            by_time.extend(
                _rollup(subset, cohort=cohort, key_fn=lambda t: t.get("time_bucket") or "other", key_name="time_bucket")
            )

        by_rank = []
        for cohort, subset in (("winning", winning), ("all_accepted", self.all_trades)):
            by_rank.extend(
                _rollup(
                    subset,
                    cohort=cohort,
                    key_fn=lambda t: t.get("dynamic40_rank_bucket") or "rank_unknown",
                    key_name="rank_bucket",
                )
            )

        shadow_rows = evaluate_shadow_variants(self.all_trades)

        trailing_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in trailing), 2)
        overlap_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in overlap), 2)
        overlap_win_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in overlap_win), 2)
        d40_win = [t for t in winning if str(t.get("universe_group") or "") == "dynamic40"]
        c10_win = [t for t in winning if str(t.get("universe_group") or "") == "core10"]
        am_win = [t for t in winning if str(t.get("session_kind") or "").lower() == "am"]
        pm_win = [t for t in winning if str(t.get("session_kind") or "").lower() == "pm"]
        board_low_win = [t for t in winning if _board_tier(t) == "board_low"]

        total_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in self.all_trades), 2)
        phase377_ref = {}
        p377 = self.reports_dir / "phase377_daily_regime_breakdown_summary.json"
        if p377.is_file():
            data = json.loads(p377.read_text(encoding="utf-8"))
            phase377_ref = (data.get("period_metrics") or {}).get("period_b_20260528_20260612", {}).get(PRIMARY_STACK, {})

        entry_compare = [r for r in compare_rows if not r.get("reference_only")][:5]
        expand_hints = [r["feature"] for r in entry_compare if r.get("direction") == "higher_in_winners"][:3]

        candidates = [r for r in shadow_rows if r.get("production_candidate")]
        priority = "勝ちを増やす / EXIT維持 / Universe維持"
        if not candidates:
            priority = "負けを削る方向は禁止 — 勝ち源泉の維持（EXIT overlap/trailing）と Universe/rank 21-40 維持を優先"

        final_judgment = {
            "primary_profit_driver": "trailing_mfe_exit + overlap_replaced + dynamic40 rank_21_40",
            "trailing_mfe_pnl": trailing_pnl,
            "overlap_pnl": overlap_pnl,
            "overlap_winning_pnl": overlap_win_pnl,
            "dynamic40_winning_pnl": round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in d40_win), 2),
            "core10_winning_pnl": round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in c10_win), 2),
            "am_winning_pnl": round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in am_win), 2),
            "pm_winning_pnl": round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in pm_win), 2),
            "board_low_winner_count": len(board_low_win),
            "expand_profile": expand_hints or ["low_imbalance_pctile", "dynamic40_rank_21_40", "pm_session"],
            "preserve_profile": ["trailing_mfe_exit", "overlap_replaced", "board_low", "rank_21_40_dynamic"],
            "priority_recommendation": priority,
            "production_shadow_candidates": [r["variant_id"] for r in candidates],
            "next_phase_hypotheses": [
                "dynamic40 backup rank 21-40 preservation shadow",
                "PM session entry capacity expansion",
                "low imbalance_pctile entry boost (not board_low reject)",
                "overlap_replace policy maintain",
                "trailing_mfe EXIT maintain",
            ],
        }

        return {
            "phase": 381,
            "title": "Winner profile review",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "period": {"start": PERIOD_B_START, "end": PERIOD_B_END},
            "stack_id": PRIMARY_STACK,
            "trade_count": len(self.all_trades),
            "winning_count": len(winning),
            "total_pnl_yen_100": total_pnl,
            "winner_type_counts": dict(winner_types),
            "top_profit_compositions": top_compositions,
            "winning_vs_low_mfe_compare": compare_rows[:20],
            "shadow_variants": shadow_rows,
            "final_judgment": final_judgment,
            "consistency_checks": {
                "phase377_trade_count": phase377_ref.get("trade_count"),
                "phase377_total_pnl": phase377_ref.get("total_pnl_yen_100"),
                "trade_count_matches": len(self.all_trades) == int(phase377_ref.get("trade_count") or -1),
                "total_pnl_matches": total_pnl == _float(phase377_ref.get("total_pnl_yen_100")),
            },
            "_feature_rows": feature_rows,
            "_compare_rows": compare_rows,
            "_top_rows": top_rows,
            "_by_exit": by_exit,
            "_by_universe": by_universe,
            "_by_time": by_time,
            "_by_rank": by_rank,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        _write_csv(paths["features"], list(result["_feature_rows"]), FEATURES_CSV_FIELDS)
        _write_csv(paths["top_trades"], list(result["_top_rows"]), TOP_TRADES_FIELDS)
        _write_csv(paths["by_exit"], list(result["_by_exit"]), BY_EXIT_FIELDS)
        _write_csv(paths["by_universe"], list(result["_by_universe"]), BY_UNIVERSE_FIELDS)
        _write_csv(paths["by_time"], list(result["_by_time"]), BY_TIME_FIELDS)
        _write_csv(paths["by_rank"], list(result["_by_rank"]), BY_RANK_FIELDS)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"].write_text(build_report(payload), encoding="utf-8")
        return paths
