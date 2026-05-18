"""
Phase 38: Risk layer validation — clustering and drawdown by regime.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Mapping, Optional, Sequence

from research.entry_v2 import MOMENTUM_V13_COMBINED_REFERENCE
from research.research_exit_criteria import _as_float


def build_risk_layer_report(
    trades: Sequence[Mapping[str, Any]],
    *,
    focus_profile: str = MOMENTUM_V13_COMBINED_REFERENCE,
    day_regimes: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    grp = sorted(
        [t for t in trades if str(t.get("profile")) == focus_profile],
        key=lambda t: (str(t.get("trade_date", "")), str(t.get("entry_time", ""))),
    )
    pnls = [_as_float(t.get("pnl_pct")) or 0.0 for t in grp]

    max_loss_cluster = 0
    cur_loss = 0
    for p in pnls:
        if p < -0.08:
            cur_loss += 1
            max_loss_cluster = max(max_loss_cluster, cur_loss)
        else:
            cur_loss = 0

    max_consec_loss = 0
    streak = 0
    for p in pnls:
        if p < 0:
            streak += 1
            max_consec_loss = max(max_consec_loss, streak)
        else:
            streak = 0

    collapse_exits = [
        t
        for t in grp
        if str(t.get("exit_reason", "")).startswith(
            ("momentum_continuation_loss", "continuation_weakness", "momentum_decay")
        )
    ]
    collapse_cluster = 0
    cur_c = 0
    for t in grp:
        if t in collapse_exits:
            cur_c += 1
            collapse_cluster = max(collapse_cluster, cur_c)
        else:
            cur_c = 0

    by_day: dict[str, float] = defaultdict(float)
    for t in grp:
        d = str(t.get("trade_date", ""))[:10]
        by_day[d] += _as_float(t.get("pnl_pct")) or 0.0

    by_regime: dict[str, list[float]] = defaultdict(list)
    if day_regimes:
        for t in grp:
            d = str(t.get("trade_date", ""))[:10]
            reg = (day_regimes.get(d) or {}).get("regime", "unknown")
            by_regime[reg].append(_as_float(t.get("pnl_pct")) or 0.0)

    regime_dd: dict[str, Any] = {}
    for reg, rpnls in by_regime.items():
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in rpnls:
            cum += p
            peak = max(peak, cum)
            max_dd = min(max_dd, cum - peak)
        regime_dd[reg] = {
            "trade_count": len(rpnls),
            "total_pnl_pct": sum(rpnls),
            "max_drawdown_pct": max_dd,
        }

    worst_day = min(by_day.values()) if by_day else None
    day_pnl_cv = None
    if len(by_day) >= 2:
        vals = list(by_day.values())
        m = statistics.mean(vals)
        if abs(m) > 1e-9:
            day_pnl_cv = statistics.pstdev(vals) / abs(m)

    acceptable = (
        max_loss_cluster <= 4
        and max_consec_loss <= 6
        and collapse_cluster <= 3
        and (worst_day is None or worst_day > -2.5)
    )

    return {
        "phase": 38,
        "focus_profile": focus_profile,
        "max_loss_clustering": max_loss_cluster,
        "max_consecutive_losers": max_consec_loss,
        "continuation_collapse_cluster": collapse_cluster,
        "worst_day_pnl_pct": worst_day,
        "day_pnl_cv": day_pnl_cv,
        "regime_drawdown": regime_dd,
        "risk_clustering_acceptable": acceptable,
        "diagnosis": (
            "risk_exposure_issue"
            if acceptable and (statistics.mean(pnls) if pnls else 0) < 0
            else (
                "monetization_issue"
                if acceptable
                else "risk_clustering_elevated"
            )
        ),
    }
