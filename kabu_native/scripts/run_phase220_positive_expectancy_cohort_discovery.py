#!/usr/bin/env python3
"""
Phase220: Positive expectancy cohort discovery (review only).

Find common entry features among winners vs losers; top-20% vs bottom-20% winners.
"""

from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase220_positive_expectancy_cohort_discovery.json"

NUMERIC_FEATURES: tuple[tuple[str, str], ...] = (
    ("trading_value", "trading_value"),
    ("entry_vwap_dev", "entry_vwap_dev_pct"),
    ("board_imbalance", "entry_order_book_imbalance"),
    ("quality", "continuation_quality_score"),
    ("momentum", "momentum_continuation_score"),
    ("continuation_duration", "max_continuation_duration"),
    ("current_price", "current_price"),
    ("tick_ratio", "tick_ratio_pct"),
)


def _load_phase217() -> Any:
    path = REPO / "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py"
    name = "phase217_loader_p220"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _vals(rows: list[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = _float(r.get(key))
        if v is not None:
            out.append(v)
    return out


def _mean(xs: list[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 6) if xs else None


def _median(xs: list[float]) -> Optional[float]:
    return round(statistics.median(xs), 6) if xs else None


def _stdev(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    return round(statistics.stdev(xs), 6)


def _cohen_d(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.stdev(a), statistics.stdev(b)
    pooled = math.sqrt(((len(a) - 1) * sa * sa + (len(b) - 1) * sb * sb) / (len(a) + len(b) - 2))
    if pooled <= 1e-12:
        return None
    return round((ma - mb) / pooled, 4)


def _compare(label: str, key: str, a: list[dict], b: list[dict]) -> dict[str, Any]:
    av, bv = _vals(a, key), _vals(b, key)
    ma, mb = _mean(av), _mean(bv)
    return {
        "feature": label,
        "field": key,
        "A_count": len(av),
        "B_count": len(bv),
        "A_mean": ma,
        "B_mean": mb,
        "A_median": _median(av),
        "B_median": _median(bv),
        "delta_A_minus_B": round(ma - mb, 6) if ma is not None and mb is not None else None,
        "cohen_d": _cohen_d(av, bv),
        "A_higher": ma is not None and mb is not None and ma > mb,
    }


def _augment_continuation_duration(mod: Any, rows: list[dict[str, Any]]) -> None:
    by_session: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_session.setdefault(str(r.get("session_id") or ""), []).append(r)
    for session_rel, sess_rows in by_session.items():
        if not session_rel:
            continue
        sdir = mod.BASE / session_rel
        accept: dict[tuple[str, str], dict[str, Any]] = {}
        for ev in mod._load_events(sdir):
            if ev.get("event_type") == "accepted":
                accept[(str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))] = ev
        for r in sess_rows:
            if r.get("max_continuation_duration") is not None:
                continue
            key = (str(r.get("symbol") or ""), str(r.get("entry_time") or ""))
            acc = accept.get(key, {})
            dur = mod._float(acc.get("max_continuation_duration"))
            if dur is not None:
                r["max_continuation_duration"] = dur


def _quantile_slice(rows: list[dict[str, Any]], q_low: float, q_high: float) -> list[dict[str, Any]]:
    if not rows:
        return []
    ranked = sorted(rows, key=lambda r: float(r.get("pnl_pct") or 0))
    n = len(ranked)
    i0 = max(0, int(n * q_low))
    i1 = min(n, max(i0 + 1, int(math.ceil(n * q_high))))
    return ranked[i0:i1]


def _entry_time_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        b = str(r.get("entry_time_bucket") or "unknown")
        buckets.setdefault(b, []).append(r)

    by_bucket: dict[str, Any] = {}
    for b, rs in sorted(buckets.items()):
        n = len(rs)
        wins = sum(1 for x in rs if float(x.get("pnl_pct") or 0) > 0)
        pnls = [float(x.get("pnl_pct") or 0) for x in rs]
        by_bucket[b] = {
            "trade_count": n,
            "win_rate": round(wins / n, 4) if n else None,
            "avg_pnl_pct": round(sum(pnls) / n, 4) if n else None,
            "total_pnl_pct": round(sum(pnls), 4),
        }

    ranked = sorted(
        [(b, v) for b, v in by_bucket.items() if v["trade_count"] >= 10],
        key=lambda x: float(x[1]["win_rate"] or 0),
        reverse=True,
    )
    return {
        "by_bucket": by_bucket,
        "top_win_rate_buckets_min_n10": [{"bucket": b, **v} for b, v in ranked[:5]],
        "bottom_win_rate_buckets_min_n10": [{"bucket": b, **v} for b, v in ranked[-5:]],
    }


def _cohort_summary(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    n = len(rows)
    if not n:
        return {"label": label, "trade_count": 0}
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    pf = round(wins / gl, 4) if gl > 0 else (None if wins <= 0 else float("inf"))
    return {
        "label": label,
        "trade_count": n,
        "share_of_all_pct": None,
        "profit_factor": pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "median_pnl_pct": round(statistics.median(pnls), 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4),
        "avg_mfe_pct": _mean(_vals(rows, "mfe_pct")),
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_phase217()
    mod = p217._load_phase213c_module()
    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    _augment_continuation_duration(mod, rows)

    winners = [r for r in rows if float(r.get("pnl_pct") or 0) > 0]
    losers = [r for r in rows if float(r.get("pnl_pct") or 0) <= 0]
    a_top = _quantile_slice(winners, 0.80, 1.0)
    a_bottom = _quantile_slice(winners, 0.0, 0.20)

    for s in (_cohort_summary(winners, "A_winners"), _cohort_summary(losers, "B_losers")):
        s["share_of_all_pct"] = round(100.0 * s["trade_count"] / max(1, len(rows)), 2)

    win_vs_lose = [_compare(lbl, key, winners, losers) for lbl, key in NUMERIC_FEATURES]
    top_vs_bottom = [_compare(lbl, key, a_top, a_bottom) for lbl, key in NUMERIC_FEATURES]

    ranked_win = sorted(
        [c for c in win_vs_lose if c.get("cohen_d") is not None],
        key=lambda x: abs(float(x["cohen_d"])),
        reverse=True,
    )
    ranked_top = sorted(
        [c for c in top_vs_bottom if c.get("cohen_d") is not None],
        key=lambda x: abs(float(x["cohen_d"])),
        reverse=True,
    )

    entry_winners = _entry_time_analysis(winners)
    entry_losers = _entry_time_analysis(losers)
    entry_compare = _entry_time_analysis(rows)

    am_pm: dict[str, Any] = {}
    for period in ("AM", "PM"):
        pr = [r for r in rows if r.get("session_period") == period]
        w = [r for r in pr if float(r.get("pnl_pct") or 0) > 0]
        lo = [r for r in pr if float(r.get("pnl_pct") or 0) <= 0]
        am_pm[period] = {
            "summary": {
                "all": _cohort_summary(pr, period),
                "winners": _cohort_summary(w, f"{period}_winners"),
                "losers": _cohort_summary(lo, f"{period}_losers"),
            },
            "top_features_win_vs_lose": sorted(
                [c for c in [_compare(l, k, w, lo) for l, k in NUMERIC_FEATURES] if c.get("cohen_d")],
                key=lambda x: abs(float(x["cohen_d"])),
                reverse=True,
            )[:5],
        }

    # Plain-language hints from top discriminating features
    hints: list[str] = []
    if ranked_win:
        top = ranked_win[0]
        hints.append(
            f"Winners vs losers: largest |d| on {top['feature']} "
            f"(A_mean={top['A_mean']}, B_mean={top['B_mean']}, d={top['cohen_d']})."
        )
    if ranked_top:
        t2 = ranked_top[0]
        hints.append(
            f"Big winners vs small winners: {t2['feature']} "
            f"(top_mean={t2['A_mean']}, bottom_mean={t2['B_mean']}, d={t2['cohen_d']})."
        )

    report = {
        "phase": 220,
        "mode": "positive_expectancy_cohort_discovery",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
        },
        "population": {
            "total_trades": len(rows),
            "groups": {
                "A_pnl_positive": _cohort_summary(winners, "A_pnl_positive"),
                "B_pnl_non_positive": _cohort_summary(losers, "B_pnl_non_positive"),
                "A_top20pct_by_pnl": {
                    **_cohort_summary(a_top, "A_top20pct"),
                    "pnl_range": {
                        "min": round(float(a_top[0]["pnl_pct"]), 4) if a_top else None,
                        "max": round(float(a_top[-1]["pnl_pct"]), 4) if a_top else None,
                    },
                    "share_of_winners_pct": round(100.0 * len(a_top) / max(1, len(winners)), 2),
                },
                "A_bottom20pct_by_pnl": {
                    **_cohort_summary(a_bottom, "A_bottom20pct"),
                    "pnl_range": {
                        "min": round(float(a_bottom[0]["pnl_pct"]), 4) if a_bottom else None,
                        "max": round(float(a_bottom[-1]["pnl_pct"]), 4) if a_bottom else None,
                    },
                    "share_of_winners_pct": round(100.0 * len(a_bottom) / max(1, len(winners)), 2),
                },
            },
        },
        "winners_vs_losers_feature_comparisons": win_vs_lose,
        "top20_vs_bottom20_winners_comparisons": top_vs_bottom,
        "top_discriminators_win_vs_lose": ranked_win[:10],
        "top_discriminators_big_vs_small_winners": ranked_top[:10],
        "entry_time_bucket": {
            "all_trades": entry_compare,
            "winners_only": entry_winners,
            "losers_only": entry_losers,
        },
        "am_pm_breakdown": am_pm,
        "feature_coverage": {
            lbl: round(
                sum(1 for r in rows if r.get(field) is not None and r.get(field) != "")
                / max(1, len(rows)),
                4,
            )
            for lbl, field in NUMERIC_FEATURES
        },
        "interpretation_hints": hints,
        "notes": [
            "Goal: find what makes winning trades, not stop avoidance.",
            "A_top20 / A_bottom20 = top/bottom 20% of winner cohort by realized pnl_pct.",
            "entry_time via 30-min-style entry_time_bucket from Phase217.",
        ],
    }
    report["population"]["groups"]["A_pnl_positive"]["share_of_all_pct"] = round(
        100.0 * len(winners) / max(1, len(rows)), 2
    )
    report["population"]["groups"]["B_pnl_non_positive"]["share_of_all_pct"] = round(
        100.0 * len(losers) / max(1, len(rows)), 2
    )

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} n={len(rows)} winners={len(winners)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
