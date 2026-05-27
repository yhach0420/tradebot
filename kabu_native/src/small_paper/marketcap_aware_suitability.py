"""
Phase 82b: Market-cap-aware daytrade suitability (diagnostic only).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from small_paper.daytrade_suitability import (
    QUALITY_GATE,
    percentile_value,
    rank_normalize,
    summarize_trades,
    tier_ja,
)

MIDCAP_BONUS = 0.10


@dataclass
class TierThresholds:
    """Session-level cutoffs derived from quality-passing trade rows."""

    large_atr_p50: float = 0.0
    large_range_p50: float = 0.0
    large_atr_p25: float = 0.0
    large_range_p25: float = 0.0
    large_tv_p25: float = 0.0
    large_atr_median: float = 0.0
    large_range_median: float = 0.0
    mid_atr_p50: float = 0.0
    mid_range_p50: float = 0.0
    mid_atr_median: float = 0.0
    mid_range_median: float = 0.0
    mid_tv_p25: float = 0.0
    mid_turnover_p25: float = 0.0
    small_tv_p50: float = 0.0
    small_atr_p50: float = 0.0
    small_turnover_p25: float = 0.0
    markcap_aware_top50: float = 0.0
    raw: dict[str, float] = field(default_factory=dict)


def _vals(rows: Sequence[Mapping[str, Any]], field: str, *, tier: Optional[str] = None) -> list[float]:
    out: list[float] = []
    for r in rows:
        if tier and str(r.get("market_cap_tier")) != tier:
            continue
        v = r.get(field)
        if v is not None:
            out.append(float(v))
    return out


def build_tier_thresholds(qrows: Sequence[Mapping[str, Any]]) -> TierThresholds:
    large_atr = _vals(qrows, "atr_pct", tier="large")
    large_rng = _vals(qrows, "intraday_range_pct", tier="large")
    large_tv = _vals(qrows, "trading_value_jpy", tier="large")
    mid_atr = _vals(qrows, "atr_pct", tier="mid")
    mid_rng = _vals(qrows, "intraday_range_pct", tier="mid")
    mid_tv = _vals(qrows, "trading_value_jpy", tier="mid")
    mid_to = _vals(qrows, "turnover_proxy", tier="mid")
    small_tv = _vals(qrows, "trading_value_jpy", tier="small")
    small_atr = _vals(qrows, "atr_pct", tier="small")
    small_to = _vals(qrows, "turnover_proxy", tier="small")
    aware: list[float] = []

    th = TierThresholds()
    if large_atr:
        th.large_atr_p50 = percentile_value(large_atr, 0.50)
        th.large_atr_p25 = percentile_value(large_atr, 0.25)
        th.large_atr_median = statistics.median(large_atr)
    if large_rng:
        th.large_range_p50 = percentile_value(large_rng, 0.50)
        th.large_range_p25 = percentile_value(large_rng, 0.25)
        th.large_range_median = statistics.median(large_rng)
    if large_tv:
        th.large_tv_p25 = percentile_value(large_tv, 0.25)
    if mid_atr:
        th.mid_atr_p50 = percentile_value(mid_atr, 0.50)
        th.mid_atr_median = statistics.median(mid_atr)
    if mid_rng:
        th.mid_range_p50 = percentile_value(mid_rng, 0.50)
        th.mid_range_median = statistics.median(mid_rng)
    if mid_tv:
        th.mid_tv_p25 = percentile_value(mid_tv, 0.25)
    if mid_to:
        th.mid_turnover_p25 = percentile_value(mid_to, 0.25)
    if small_tv:
        th.small_tv_p50 = percentile_value(small_tv, 0.50)
    if small_atr:
        th.small_atr_p50 = percentile_value(small_atr, 0.50)
    if small_to:
        th.small_turnover_p25 = percentile_value(small_to, 0.25)
    return th


def _tier_rank_norms(
    rows: list[dict[str, Any]],
    field: str,
    tier: str,
) -> dict[int, Optional[float]]:
    idxs = [i for i, r in enumerate(rows) if r.get("market_cap_tier") == tier]
    sub_vals = [rows[i].get(field) for i in idxs]
    sub_norm = rank_normalize(sub_vals)
    return {idxs[i]: sub_norm[i] for i in range(len(idxs))}


def attach_marketcap_aware_scores(
    rows: list[dict[str, Any]],
    *,
    thresholds: TierThresholds,
) -> None:
    """Add marketcap_aware_suitability_score and tier pass flags in-place."""
    from small_paper.daytrade_suitability import attach_composite_scores

    attach_composite_scores(rows)
    tier_fields = ("atr_pct", "intraday_range_pct", "trading_value_jpy", "turnover_proxy")
    tier_norms: dict[str, dict[int, dict[str, Optional[float]]]] = {
        t: {i: {} for i, r in enumerate(rows) if r.get("market_cap_tier") == t}
        for t in ("large", "mid", "small")
    }
    for t in ("large", "mid", "small"):
        for f in tier_fields:
            mapping = _tier_rank_norms(rows, f, t)
            for idx, val in mapping.items():
                tier_norms[t][idx][f] = val

    scores_for_cutoff: list[float] = []
    for i, row in enumerate(rows):
        tier = str(row.get("market_cap_tier") or "unknown")
        atr = float(row.get("atr_pct") or 0)
        rng = float(row.get("intraday_range_pct") or 0)
        tv = float(row.get("trading_value_jpy") or 0)
        turnover = float(row.get("turnover_proxy") or 0)
        tn = tier_norms.get(tier, {}).get(i, {})

        na = tn.get("atr_pct")
        nr = tn.get("intraday_range_pct")
        nt = tn.get("trading_value_jpy")
        nv = tn.get("turnover_proxy")

        midcap_bonus = 0.0
        if tier == "mid":
            if atr >= thresholds.mid_atr_median and rng >= thresholds.mid_range_median:
                midcap_bonus = MIDCAP_BONUS

        if tier == "large":
            score = (
                0.35 * (na or 0)
                + 0.30 * (nr or 0)
                + 0.30 * (nt or 0)
                + 0.05 * (nv or 0)
            )
        elif tier == "mid":
            score = (
                0.40 * (na or 0)
                + 0.30 * (nr or 0)
                + 0.20 * (nt or 0)
                + 0.10 * (nv or 0)
                + midcap_bonus
            )
        elif tier == "small":
            score = (
                0.35 * (na or 0)
                + 0.25 * (nr or 0)
                + 0.25 * (nt or 0)
                + 0.15 * (nv or 0)
            )
        else:
            score = row.get("daytrade_suitability_score") or 0

        row["norm_atr_pct_tier"] = na
        row["norm_intraday_range_pct_tier"] = nr
        row["norm_trading_value_tier"] = nt
        row["norm_turnover_proxy_tier"] = nv
        row["midcap_bonus"] = round(midcap_bonus, 4)
        row["marketcap_aware_suitability_score"] = round(float(score), 6)
        scores_for_cutoff.append(float(score))

        row["low_vol_large_flag"] = tier == "large" and (
            atr < thresholds.large_atr_p25 and rng < thresholds.large_range_p25
        )
        row["small_liquidity_fail"] = tier == "small" and (
            tv < thresholds.small_tv_p50 if thresholds.small_tv_p50 > 0 else False
        )
        row["tier_daytrade_pass_j"] = passes_tier_rule_j(row, thresholds)

    if scores_for_cutoff:
        thresholds.markcap_aware_top50 = percentile_value(scores_for_cutoff, 0.50)


def passes_tier_rule_j(row: Mapping[str, Any], th: TierThresholds) -> bool:
    """Per-tier minimum volatility + liquidity (not one-size-fits-all)."""
    tier = str(row.get("market_cap_tier") or "")
    atr = float(row.get("atr_pct") or 0)
    rng = float(row.get("intraday_range_pct") or 0)
    tv = float(row.get("trading_value_jpy") or 0)
    turnover = float(row.get("turnover_proxy") or 0)

    if tier == "large":
        if tv < th.large_tv_p25 and th.large_tv_p25 > 0:
            return False
        if atr < th.large_atr_p25 and rng < th.large_range_p25:
            return False
        return (atr >= th.large_atr_p50 or rng >= th.large_range_p50) if th.large_atr_p50 else True

    if tier == "mid":
        if tv < th.mid_tv_p25 and th.mid_tv_p25 > 0:
            return False
        return (
            atr >= th.mid_atr_p50
            and rng >= th.mid_range_p50
            and (turnover >= th.mid_turnover_p25 if th.mid_turnover_p25 > 0 else True)
        )

    if tier == "small":
        if tv < th.small_tv_p50 and th.small_tv_p50 > 0:
            return False
        return atr >= th.small_atr_p50 and (
            turnover >= th.small_turnover_p25 if th.small_turnover_p25 > 0 else tv > 0
        )

    return True


def filter_policy_h(
    qrows: Sequence[Mapping[str, Any]],
    th: TierThresholds,
) -> list[dict[str, Any]]:
    """quality + marketcap-aware top 50%, exclude small liquidity fail."""
    return [
        dict(r)
        for r in qrows
        if not r.get("small_liquidity_fail")
        and (float(r.get("marketcap_aware_suitability_score") or 0) >= th.markcap_aware_top50)
    ]


def filter_policy_i(qrows: Sequence[Mapping[str, Any]], th: TierThresholds) -> list[dict[str, Any]]:
    return filter_policy_h(qrows, th)


def filter_policy_j(
    qrows: Sequence[Mapping[str, Any]],
    th: TierThresholds,
) -> list[dict[str, Any]]:
    return [dict(r) for r in qrows if r.get("tier_daytrade_pass_j")]


def _parse_ts(iso: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def simulate_cap3_large_max2(
    trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(trades, key=lambda t: _parse_ts(str(t.get("entry_time") or "")))
    open_slots: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for t in ordered:
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or "")) or ent + 3600
        tier = str(t.get("market_cap_tier") or "")
        open_slots = [s for s in open_slots if s["exit_ts"] > ent]
        large_n = sum(1 for s in open_slots if s["tier"] == "large")
        if len(open_slots) >= 3:
            continue
        if tier == "large" and large_n >= 2:
            continue
        open_slots.append({"ent_ts": ent, "exit_ts": ex, "tier": tier, "trade": dict(t)})
        kept.append(dict(t))
    return kept


def simulate_cap3_reserve_mid_slot(
    trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """cap=3; if full and all large, allow mid/small to displace weakest large by suitability."""
    ordered = sorted(trades, key=lambda t: _parse_ts(str(t.get("entry_time") or "")))
    open_slots: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    kept_keys: set[tuple[str, str]] = set()

    for t in ordered:
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or "")) or ent + 3600
        tier = str(t.get("market_cap_tier") or "")
        key = (str(t.get("symbol")), str(t.get("entry_time")))
        score = float(t.get("marketcap_aware_suitability_score") or 0)
        open_slots = [s for s in open_slots if s["exit_ts"] > ent]

        if len(open_slots) < 3:
            open_slots.append({"ent_ts": ent, "exit_ts": ex, "tier": tier, "score": score, "key": key})
            kept.append(dict(t))
            kept_keys.add(key)
            continue

        if tier in ("mid", "small"):
            larges = [s for s in open_slots if s["tier"] == "large"]
            if larges:
                worst = min(larges, key=lambda s: s["score"])
                if score > worst["score"]:
                    open_slots.remove(worst)
                    kept_keys.discard(worst["key"])
                    kept = [k for k in kept if (k["symbol"], k["entry_time"]) != worst["key"]]
                    open_slots.append(
                        {"ent_ts": ent, "exit_ts": ex, "tier": tier, "score": score, "key": key}
                    )
                    kept.append(dict(t))
                    kept_keys.add(key)
    return kept


def build_marketcap_aware_policy_grid(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
    baseline: Sequence[Mapping[str, Any]],
    eval_policy_fn: Any,
) -> list[dict[str, Any]]:
    qrows = [
        dict(r)
        for r in trade_rows
        if float(r.get("continuation_quality_score") or 0) >= QUALITY_GATE
    ]
    th = build_tier_thresholds(qrows)
    attach_marketcap_aware_scores(qrows, thresholds=th)

    grid: list[dict[str, Any]] = []
    grid.append(
        eval_policy_fn(
            "A_current_quality_only",
            list(baseline),
            session_id=session_id,
            baseline=baseline,
            threshold_note="quality>=0.70 baseline",
        )
    )

    h_rows = filter_policy_h(qrows, th)
    grid.append(
        eval_policy_fn(
            "H_marketcap_aware_suitability_top50",
            h_rows,
            session_id=session_id,
            baseline=baseline,
            threshold_note=f"aware>={th.markcap_aware_top50:.4f}",
        )
    )

    i_rows = filter_policy_i(qrows, th)
    grid.append(
        eval_policy_fn(
            "I_quality_and_marketcap_aware_top50",
            i_rows,
            session_id=session_id,
            baseline=baseline,
            threshold_note="same_as_H",
        )
    )

    j_rows = filter_policy_j(qrows, th)
    grid.append(
        eval_policy_fn(
            "J_tier_specific_vol_liquidity_rules",
            j_rows,
            session_id=session_id,
            baseline=baseline,
            threshold_note="large/mid/small tier rules",
        )
    )

    j_cap2 = simulate_cap3_large_max2(j_rows)
    grid.append(
        eval_policy_fn(
            "K_cap3_large_max2_mid_small_1",
            j_cap2,
            session_id=session_id,
            baseline=baseline,
            threshold_note="J_filter+cap3 large<=2",
        )
    )

    j_reserve = simulate_cap3_reserve_mid_slot(j_rows)
    grid.append(
        eval_policy_fn(
            "K_cap3_reserve_mid_small_slot",
            j_reserve,
            session_id=session_id,
            baseline=baseline,
            threshold_note="J_filter+displace_weak_large_for_mid_small",
        )
    )

    return grid, qrows, th


def midcap_candidate_review_rows(
    events: Sequence[Mapping[str, Any]],
    trade_rows: Sequence[Mapping[str, Any]],
    qrows: Sequence[Mapping[str, Any]],
    th: TierThresholds,
    *,
    session_id: str,
) -> list[dict[str, Any]]:
    sym_metrics: dict[str, dict[str, Any]] = {}
    for r in qrows:
        sym = str(r.get("symbol") or "")
        if not sym:
            continue
        score = float(r.get("marketcap_aware_suitability_score") or 0)
        prev = sym_metrics.get(sym)
        if prev is None or score > float(prev.get("marketcap_aware_suitability_score") or 0):
            sym_metrics[sym] = dict(r)
    by_sym: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "candidates_q70": 0,
            "accepted_events": 0,
            "rejected_q70": 0,
            "quality_scores": [],
        }
    )
    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        q = float(ev.get("continuation_quality_score") or 0)
        if et == "candidate" and q >= QUALITY_GATE:
            by_sym[sym]["candidates_q70"] += 1
            by_sym[sym]["quality_scores"].append(q)
        elif et == "accepted":
            by_sym[sym]["accepted_events"] += 1
        elif et == "rejected" and q >= QUALITY_GATE:
            by_sym[sym]["rejected_q70"] += 1

    rows: list[dict[str, Any]] = []
    for sym, st in sorted(by_sym.items()):
        tm = sym_metrics.get(sym) or {}
        if str(tm.get("market_cap_tier") or "") != "mid":
            continue
        passes_j = bool(tm.get("tier_daytrade_pass_j"))
        passes_h = (
            not tm.get("small_liquidity_fail")
            and float(tm.get("marketcap_aware_suitability_score") or 0) >= th.markcap_aware_top50
            if tm
            else False
        )
        accepted_trades = sum(
            1
            for r in trade_rows
            if r.get("symbol") == sym
            and float(r.get("continuation_quality_score") or 0) >= QUALITY_GATE
        )
        reason = []
        if not passes_j:
            reason.append("fails_tier_J")
        if not passes_h:
            reason.append("fails_tier_H")
        if st["candidates_q70"] > 0 and st["accepted_events"] == 0:
            reason.append("cap_or_competition")
        rows.append(
            {
                "session_id": session_id,
                "symbol": sym,
                "market_cap_tier": "mid",
                "tier_ja": tier_ja("mid"),
                "candidates_q70": st["candidates_q70"],
                "accepted_events": st["accepted_events"],
                "accepted_trades": accepted_trades,
                "rejected_q70_events": st["rejected_q70"],
                "accepted_rate_events": round(st["accepted_events"] / st["candidates_q70"], 4)
                if st["candidates_q70"]
                else None,
                "mean_quality_q70": round(statistics.mean(st["quality_scores"]), 4)
                if st["quality_scores"]
                else None,
                "atr_pct": tm.get("atr_pct"),
                "intraday_range_pct": tm.get("intraday_range_pct"),
                "trading_value_jpy": tm.get("trading_value_jpy"),
                "turnover_proxy": tm.get("turnover_proxy"),
                "marketcap_aware_suitability_score": tm.get("marketcap_aware_suitability_score"),
                "midcap_bonus": tm.get("midcap_bonus"),
                "passes_tier_J": passes_j,
                "passes_tier_H": passes_h,
                "tier_J_thresholds": (
                    f"atr>={th.mid_atr_p50:.2f} range>={th.mid_range_p50:.2f} tv>={th.mid_tv_p25:.0f}"
                ),
                "diagnosis_reason": "|".join(reason) if reason else "ok",
            }
        )
    return rows


def simulate_cap3_unfiltered(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(trades, key=lambda t: _parse_ts(str(t.get("entry_time") or "")))
    open_slots: list[tuple[float, float]] = []
    kept: list[dict[str, Any]] = []
    for t in ordered:
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or "")) or ent + 3600
        open_slots = [(a, b) for a, b in open_slots if b > ent]
        if len(open_slots) >= 3:
            continue
        open_slots.append((ent, ex))
        kept.append(dict(t))
    return kept


def tier_slot_allocation_summary(
    trade_rows: Sequence[Mapping[str, Any]],
    qrows: Sequence[Mapping[str, Any]],
    th: TierThresholds,
    *,
    session_id: str,
    baseline: Sequence[Mapping[str, Any]],
    eval_policy_fn: Any,
) -> list[dict[str, Any]]:
    from small_paper.daytrade_suitability import policy_impact

    j_rows = filter_policy_j(qrows, th)
    policies = (
        ("baseline_cap3", simulate_cap3_unfiltered(baseline)),
        ("J_no_cap_sim", list(j_rows)),
        ("K_large_max2", simulate_cap3_large_max2(j_rows)),
        ("K_reserve_mid_slot", simulate_cap3_reserve_mid_slot(j_rows)),
    )

    rows: list[dict[str, Any]] = []
    base_kept = simulate_cap3_unfiltered(baseline)
    base_sum = summarize_trades(base_kept)
    for pid, kept in policies:
        s = summarize_trades(kept)
        imp = policy_impact(baseline, kept)
        rows.append(
            {
                "session_id": session_id,
                "allocation_policy": pid,
                "filter_note": "quality>=0.70" if pid == "baseline_cap3" else "tier_J_rules",
                **s,
                **imp,
                "mid_share_delta_vs_baseline_cap3": round(
                    (s.get("mid_share") or 0) - (base_sum.get("mid_share") or 0),
                    4,
                ),
            }
        )
    return rows
