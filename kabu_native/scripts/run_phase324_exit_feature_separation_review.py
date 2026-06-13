#!/usr/bin/env python3
"""
Phase324: EXIT candidate feature separation — good exits vs stop_hit.

Good exit: trailing_mfe_exit, overlap_replaced_review, session_close
Bad exit:  stop_hit

Output: phase324_exit_feature_separation_review.json
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
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase324_exit_feature_separation_review.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")

SESSIONS = {
    "am": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_080642",
    "pm": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_122548",
}

GOOD_EXIT_REASONS = frozenset(
    {
        "trailing_mfe_exit",
        "overlap_replaced_review",
        "morning_session_close",
        "afternoon_session_close",
    }
)
BAD_EXIT_REASON = "stop_hit"

FEATURE_CATEGORIES: dict[str, dict[str, Any]] = {
    "vwap": {
        "label": "VWAP乖離",
        "features": {
            "entry_vwap_dev_pct": {"label": "entry_vwap_dev_pct", "source": "exit"},
            "abs_entry_vwap_dev_pct": {"label": "abs_entry_vwap_dev_pct", "source": "derived"},
        },
    },
    "imbalance": {
        "label": "板imbalance",
        "features": {
            "entry_order_book_imbalance": {"label": "entry_order_book_imbalance", "source": "exit"},
            "entry_imbalance_percentile": {"label": "entry_imbalance_percentile", "source": "exit"},
        },
    },
    "volume": {
        "label": "出来高",
        "features": {
            "trading_value": {"label": "trading_value", "source": "accepted", "transform": "log10"},
            "turnover_proxy": {"label": "turnover_proxy", "source": "accepted"},
        },
    },
    "quality": {
        "label": "quality",
        "features": {
            "continuation_quality_score": {"label": "continuation_quality_score", "source": "accepted"},
            "entry_expectancy_score_v2": {"label": "entry_expectancy_score_v2", "source": "exit"},
        },
    },
    "hold_time": {
        "label": "保有時間",
        "features": {
            "hold_sec": {"label": "hold_sec", "source": "exit"},
            "hold_min": {"label": "hold_min", "source": "derived"},
        },
    },
}


@dataclass
class TradeRow:
    session: str
    symbol: str
    entry_time: str
    exit_time: str
    exit_reason: str
    label_good_exit: int
    pnl_pct: float
    pnl_yen_100: float
    features: dict[str, Optional[float]]


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


def _roc_auc(y_true: list[int], scores: list[float]) -> Optional[float]:
    pos = [s for s, y in zip(scores, y_true) if y == 1]
    neg = [s for s, y in zip(scores, y_true) if y == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def _oriented_auc(y_true: list[int], scores: list[float]) -> dict[str, Any]:
    raw = _roc_auc(y_true, scores)
    if raw is None:
        return {"auc": None, "auc_raw": None, "direction": None}
    flipped = 1.0 - raw
    if raw >= flipped:
        return {
            "auc": round(raw, 4),
            "auc_raw": round(raw, 4),
            "direction": "higher_is_good_exit",
        }
    return {
        "auc": round(flipped, 4),
        "auc_raw": round(raw, 4),
        "direction": "lower_is_good_exit",
    }


def _group_stats(values: list[float], labels: list[int]) -> dict[str, Any]:
    good = [v for v, y in zip(values, labels) if y == 1]
    bad = [v for v, y in zip(values, labels) if y == 0]
    if not good or not bad:
        return {}
    gm, bm = statistics.mean(good), statistics.mean(bad)
    pooled = statistics.pstdev(values) or 1e-9
    cohens_d = (gm - bm) / pooled
    return {
        "good_exit_mean": round(gm, 4),
        "stop_hit_mean": round(bm, 4),
        "good_exit_median": round(statistics.median(good), 4),
        "stop_hit_median": round(statistics.median(bad), 4),
        "mean_delta_good_minus_stop": round(gm - bm, 4),
        "cohens_d": round(cohens_d, 4),
    }


def _load_trades() -> list[TradeRow]:
    from replay.pnl_yen import compute_pnl_yen_100

    out: list[TradeRow] = []
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
                key = (sym, ent)
                if et == "accepted":
                    accepted[key] = row
                elif et == "observer_exit":
                    exits.append(row)

        for row in exits:
            reason = str(row.get("exit_reason") or "unknown")
            if reason not in GOOD_EXIT_REASONS and reason != BAD_EXIT_REASON:
                continue
            acc = accepted.get((str(row.get("symbol") or ""), str(row.get("entry_time") or "")), {})
            entry = _float(row.get("entry_price")) or 0.0
            exit_p = _float(row.get("exit_price")) or _float(row.get("current_price")) or 0.0
            pnl_pct = _float(row.get("pnl_pct"))
            if pnl_pct is None and entry > 0:
                pnl_pct = (exit_p - entry) / entry * 100.0

            vwap = _float(row.get("entry_vwap_dev_pct"))
            hold = _float(row.get("hold_sec"))
            tv = _float(acc.get("trading_value"))

            feats: dict[str, Optional[float]] = {
                "entry_vwap_dev_pct": vwap,
                "abs_entry_vwap_dev_pct": abs(vwap) if vwap is not None else None,
                "entry_order_book_imbalance": _float(row.get("entry_order_book_imbalance")),
                "entry_imbalance_percentile": _float(row.get("entry_imbalance_percentile")),
                "trading_value": tv,
                "log10_trading_value": math.log10(tv) if tv and tv > 0 else None,
                "turnover_proxy": _float(acc.get("turnover_proxy")),
                "continuation_quality_score": _float(acc.get("continuation_quality_score")),
                "entry_expectancy_score_v2": _float(row.get("entry_expectancy_score_v2")),
                "hold_sec": hold,
                "hold_min": (hold / 60.0) if hold is not None else None,
            }

            out.append(
                TradeRow(
                    session=session_label,
                    symbol=str(row.get("symbol") or ""),
                    entry_time=str(row.get("entry_time") or ""),
                    exit_time=str(row.get("exit_time") or row.get("event_time") or ""),
                    exit_reason=reason,
                    label_good_exit=1 if reason in GOOD_EXIT_REASONS else 0,
                    pnl_pct=float(pnl_pct or 0.0),
                    pnl_yen_100=compute_pnl_yen_100(entry, exit_p),
                    features=feats,
                )
            )
    return out


def _feature_key_map() -> dict[str, tuple[str, str]]:
    mapping: dict[str, tuple[str, str]] = {}
    for cat_key, cat in FEATURE_CATEGORIES.items():
        for feat_key, meta in cat["features"].items():
            actual = feat_key
            if feat_key == "trading_value":
                actual = "log10_trading_value"
            mapping[feat_key] = (cat_key, actual)
    return mapping


def _analyze_feature(
    trades: list[TradeRow],
    feature_name: str,
) -> dict[str, Any]:
    pairs = [
        (t.features[feature_name], t.label_good_exit)
        for t in trades
        if t.features.get(feature_name) is not None
    ]
    if len(pairs) < 10:
        return {"feature": feature_name, "n": len(pairs), "auc": None}
    values = [p[0] for p in pairs]
    labels = [p[1] for p in pairs]
    oriented = _oriented_auc(labels, values)
    return {
        "feature": feature_name,
        "n": len(pairs),
        "missing_count": len(trades) - len(pairs),
        **oriented,
        **_group_stats(values, labels),
    }


def _category_ranking(trades: list[TradeRow]) -> list[dict[str, Any]]:
    categories: list[dict[str, Any]] = []
    for cat_key, cat in FEATURE_CATEGORIES.items():
        feat_results: list[dict[str, Any]] = []
        for feat_key, meta in cat["features"].items():
            actual_key = feat_key
            if feat_key == "trading_value":
                actual_key = "log10_trading_value"
            elif feat_key == "hold_min":
                actual_key = "hold_min"
            elif feat_key == "abs_entry_vwap_dev_pct":
                actual_key = "abs_entry_vwap_dev_pct"
            result = _analyze_feature(trades, actual_key)
            result["display_label"] = meta["label"]
            feat_results.append(result)
        valid = [r for r in feat_results if r.get("auc") is not None]
        if not valid:
            best = {"auc": None, "feature": None}
        else:
            best = max(valid, key=lambda r: float(r["auc"]))
        categories.append(
            {
                "category": cat_key,
                "label": cat["label"],
                "best_feature": best.get("feature"),
                "best_feature_label": best.get("display_label", best.get("feature")),
                "auc": best.get("auc"),
                "direction": best.get("direction"),
                "group_stats": {
                    k: best.get(k)
                    for k in (
                        "good_exit_mean",
                        "stop_hit_mean",
                        "good_exit_median",
                        "stop_hit_median",
                        "mean_delta_good_minus_stop",
                        "cohens_d",
                    )
                    if best.get(k) is not None
                },
                "features": feat_results,
            }
        )
    categories.sort(key=lambda c: float(c.get("auc") or 0), reverse=True)
    for i, row in enumerate(categories, 1):
        row["rank"] = i
    return categories


def _exit_reason_breakdown(trades: list[TradeRow]) -> dict[str, Any]:
    by_reason: dict[str, list[TradeRow]] = {}
    for t in trades:
        by_reason.setdefault(t.exit_reason, []).append(t)
    rows = []
    for reason, grp in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        rows.append(
            {
                "exit_reason": reason,
                "count": len(grp),
                "avg_pnl_pct": round(statistics.mean(t.pnl_pct for t in grp), 4),
                "total_pnl_yen_100": round(sum(t.pnl_yen_100 for t in grp), 2),
                "avg_hold_sec": round(
                    statistics.mean(t.features["hold_sec"] for t in grp if t.features.get("hold_sec") is not None),
                    1,
                )
                if any(t.features.get("hold_sec") is not None for t in grp)
                else None,
                "avg_entry_vwap_dev_pct": round(
                    statistics.mean(
                        t.features["entry_vwap_dev_pct"]
                        for t in grp
                        if t.features.get("entry_vwap_dev_pct") is not None
                    ),
                    4,
                ),
            }
        )
    return {"by_exit_reason": rows}


def main() -> int:
    _bootstrap()
    trades = _load_trades()
    if not trades:
        print("no trades loaded", file=sys.stderr)
        return 1

    labels = [t.label_good_exit for t in trades]
    good_n = sum(labels)
    stop_n = len(labels) - good_n

    ranking = _category_ranking(trades)
    all_features: list[dict[str, Any]] = []
    for cat in ranking:
        for feat in cat["features"]:
            if feat.get("auc") is not None:
                all_features.append(
                    {
                        "feature": feat["feature"],
                        "category": cat["label"],
                        "auc": feat["auc"],
                        "direction": feat.get("direction"),
                    }
                )
    all_features.sort(key=lambda r: float(r["auc"]), reverse=True)

    feature_separation_ranking = [
        {
            "rank": i,
            "category": row["label"],
            "category_key": row["category"],
            "best_feature": row["best_feature_label"],
            "auc": row["auc"],
            "direction": row["direction"],
            "interpretation": _interpret(row),
        }
        for i, row in enumerate(ranking, 1)
        if row.get("auc") is not None
    ]

    report = {
        "phase": 324,
        "title": "exit_feature_separation_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "analysis only; no logic changes",
        "target_date": DAY,
        "label_definition": {
            "good_exit_label": 1,
            "good_exit_reasons": sorted(GOOD_EXIT_REASONS),
            "bad_exit_label": 0,
            "bad_exit_reason": BAD_EXIT_REASON,
        },
        "cohort": {
            "trade_count": len(trades),
            "good_exit_count": good_n,
            "stop_hit_count": stop_n,
            "good_exit_rate": round(good_n / len(trades), 4),
        },
        "feature_separation_ranking": feature_separation_ranking,
        "all_feature_auc": all_features,
        "category_detail": ranking,
        "exit_reason_breakdown": _exit_reason_breakdown(trades),
        "verdict": {
            "top_separator": feature_separation_ranking[0] if feature_separation_ranking else None,
            "weakest_separator": feature_separation_ranking[-1] if feature_separation_ranking else None,
            "conclusion": _conclusion(feature_separation_ranking),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"trades={len(trades)} good={good_n} stop={stop_n}")
    for row in feature_separation_ranking:
        print(f"  {row['rank']}位 {row['category']} AUC {row['auc']} ({row['best_feature']})")
    return 0


def _interpret(cat_row: dict[str, Any]) -> str:
    feat = cat_row.get("best_feature_label") or ""
    direction = cat_row.get("direction") or ""
    stats = cat_row.get("group_stats") or {}
    if not stats:
        return ""
    if direction == "lower_is_good_exit":
        return f"{feat} が低いほど good_exit (good_mean={stats.get('good_exit_mean')}, stop_mean={stats.get('stop_hit_mean')})"
    return f"{feat} が高いほど good_exit (good_mean={stats.get('good_exit_mean')}, stop_mean={stats.get('stop_hit_mean')})"


def _conclusion(ranking: list[dict[str, Any]]) -> str:
    if not ranking:
        return "insufficient data"
    top = ranking[0]
    weak = ranking[-1]
    return (
        f"Best separator: {top['category']} (AUC {top['auc']}); "
        f"weakest: {weak['category']} (AUC {weak['auc']}). "
        f"No single feature strongly separates good exits from stop_hit on this day."
        if float(top["auc"]) < 0.7
        else f"Strong separator: {top['category']} (AUC {top['auc']})"
    )


if __name__ == "__main__":
    raise SystemExit(main())
