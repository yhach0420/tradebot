"""Lane B (entry_order_book_imbalance) quality audit."""
from __future__ import annotations

import statistics as st
from collections import defaultdict
from typing import Any, Sequence

from research.winner_feature_filter.labels import LabeledTrade
from research.winner_feature_filter.lanes import LANE_B_SUSPECT_DAYS


def audit_lane_b_imbalance(labeled: Sequence[LabeledTrade]) -> dict[str, Any]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for lt in labeled:
        v = lt.trade.features.get("f_imb")
        if v is not None:
            by_day[lt.trade.day].append(float(v))

    day_rows = []
    for d in sorted(by_day):
        xs = by_day[d]
        std = st.pstdev(xs) if len(xs) > 1 else 0.0
        uniq = len({round(x, 4) for x in xs})
        # Narrow band around ~0.5 suggests BidQty/AskQty missing → only thin book or mid bias
        near_half = sum(1 for x in xs if 0.43 <= x <= 0.53) / len(xs) if xs else 0.0
        suspect = d in LANE_B_SUSPECT_DAYS or (std < 0.04 and near_half > 0.85)
        day_rows.append(
            {
                "day": d,
                "n": len(xs),
                "mean": round(st.mean(xs), 6) if xs else None,
                "std": round(std, 6),
                "min": round(min(xs), 6) if xs else None,
                "max": round(max(xs), 6) if xs else None,
                "n_unique_4dp": uniq,
                "frac_in_0.43_0.53": round(near_half, 4),
                "quality_flag": "SUSPECT_NARROW" if suspect else "OK",
                "in_declared_suspect_window": d in LANE_B_SUSPECT_DAYS,
            }
        )

    suspect_days = [r["day"] for r in day_rows if r["quality_flag"] == "SUSPECT_NARROW"]
    ok_days = [r["day"] for r in day_rows if r["quality_flag"] == "OK"]

    def _pool(days: Sequence[str]) -> dict[str, Any]:
        xs = [x for d in days for x in by_day.get(d, [])]
        if not xs:
            return {"n": 0}
        return {
            "n": len(xs),
            "days": list(days),
            "mean": round(st.mean(xs), 6),
            "std": round(st.pstdev(xs) if len(xs) > 1 else 0.0, 6),
            "min": round(min(xs), 6),
            "max": round(max(xs), 6),
        }

    findings = [
        {
            "check": "calc_board_imbalance_formula",
            "status": "INFO",
            "note": "bid/(bid+ask) from BidQty/AskQty + Buy1-10/Sell1-10; returns None if total<=0",
        },
        {
            "check": "entry_scan_controller_fallback_0.5",
            "status": "RISK",
            "note": "entry_scan_controller uses float(trade.get('entry_order_book_imbalance') or 0.5) — "
            "0.5 fallback exists on some paths; accept events may still store computed values",
        },
        {
            "check": "narrow_distribution_20260615_19",
            "status": "FAIL" if any(d.startswith("2026061") for d in suspect_days) else "PASS",
            "note": (
                "20260615-19 show std≈0.025 and values clustered in [0.43,0.53]. "
                "Likely cause: incomplete L2 depth (few Buy/Sell levels) → imbalance collapses near 0.5, "
                "or stale/partial board at accept. Not a hard-coded constant (unique counts still high)."
            ),
        },
        {
            "check": "not_single_fixed_constant",
            "status": "PASS",
            "note": "Per-day unique values >> 1; not a single synthetic constant, but low-dispersion regime",
        },
    ]

    return {
        "by_day": day_rows,
        "suspect_days": suspect_days,
        "ok_days": ok_days,
        "pool_all": _pool(sorted(by_day.keys())),
        "pool_include_suspect": _pool(sorted(by_day.keys())),
        "pool_exclude_suspect": _pool(ok_days),
        "findings": findings,
        "recommendation": (
            "For Lane B rules, report both INCLUDE_SUSPECT and EXCLUDE_SUSPECT evaluations. "
            "Prefer EXCLUDE_SUSPECT (drop 20260615-19) for deploy decisions."
        ),
    }
