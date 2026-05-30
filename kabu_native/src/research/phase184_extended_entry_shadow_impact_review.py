"""
Phase184: Extended entry shadow impact review (post-hoc / shadow review only).

Validates whether Phase183 extended_entry_shadow_flag correlates with worse expectancy.
Uses fixed Phase183 thresholds — no single-day tuning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.phase181_entry_expectancy_review import (
    EntryTradeRow,
    _float,
    _load_events,
    _mean,
    _pair_trades,
    _parse_ts,
    _pf,
    build_entry_trade_rows,
)
from small_paper.extended_entry_shadow import (
    RISE_5MIN_PCT_MIN,
    ROLLING_MFE_PCT_MIN,
    VWAP_DEV_PCT_MIN,
    append_price_tick,
    compute_entry_shadow_fields,
    enrich_exit_shadow_fields,
)

REASON_KEYS = ("rise_5min", "vwap_dev", "rolling_mfe", "high_break_recent")

FEATURE_KEYS = (
    "continuation_quality_score",
    "momentum_continuation_score",
    "entry_rise_5min_pct",
    "entry_vwap_dev_pct",
    "entry_rolling_mfe_pct",
    "entry_near_day_high_pct",
    "r30_sec",
    "r60_sec",
    "r120_sec",
    "hold_sec",
    "max_mfe_until_exit",
    "max_mae_until_exit",
)


@dataclass
class ShadowTradeRow:
    trade: EntryTradeRow
    shadow: dict[str, Any]

    @property
    def extended(self) -> bool:
        return bool(self.shadow.get("extended_entry_shadow_flag"))

    @property
    def reasons(self) -> list[str]:
        raw = str(self.shadow.get("extended_entry_shadow_reasons") or "")
        return [r.strip() for r in raw.split(";") if r.strip()]

    def reason_hit(self, reason: str) -> bool:
        if reason in self.reasons:
            return True
        if reason == "high_break_recent":
            return bool(self.shadow.get("entry_high_break_recent"))
        if reason == "rise_5min":
            v = _float(self.shadow.get("entry_rise_5min_pct"))
            return v is not None and v >= RISE_5MIN_PCT_MIN
        if reason == "vwap_dev":
            v = _float(self.shadow.get("entry_vwap_dev_pct"))
            return v is not None and v >= VWAP_DEV_PCT_MIN
        if reason == "rolling_mfe":
            v = _float(self.shadow.get("entry_rolling_mfe_pct"))
            return v is not None and v >= ROLLING_MFE_PCT_MIN
        return False

    def to_dict(self) -> dict[str, Any]:
        d = self.trade.to_dict()
        d.update(self.shadow)
        for key in ("r30_sec", "r60_sec", "r120_sec"):
            if getattr(self.trade, key, None) is not None:
                d[key] = round(float(getattr(self.trade, key)), 4)
        return d


def _load_push_payload_at_entry(push_path: Path, entry_ts: float) -> dict[str, Any]:
    payload: dict[str, Any] = {"CurrentPrice": None, "VWAP": None, "HighPrice": None}
    if not push_path.is_file():
        return payload
    with push_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ts = _parse_ts(str(rec.get("recorded_at") or ""))
            if ts > entry_ts:
                break
            pld = rec.get("payload") or {}
            payload = {
                "CurrentPrice": pld.get("CurrentPrice") or payload.get("CurrentPrice"),
                "VWAP": pld.get("VWAP"),
                "HighPrice": pld.get("HighPrice"),
            }
    return payload


def _load_price_ring(push_path: Path, *, up_to_ts: float) -> list[tuple[float, float]]:
    ring: list[tuple[float, float]] = []
    if not push_path.is_file():
        return ring
    with push_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ts = _parse_ts(str(rec.get("recorded_at") or ""))
            if ts > up_to_ts:
                break
            payload = rec.get("payload") or {}
            try:
                px = float(payload.get("CurrentPrice") or 0)
            except (TypeError, ValueError):
                px = 0.0
            if px > 0:
                append_price_tick(ring, ts=ts, px=px)
    return ring


def build_shadow_trade_rows(
    session_dir: Path,
    *,
    repo_root: Path,
    day_stamp: str,
) -> list[ShadowTradeRow]:
    trades = build_entry_trade_rows(session_dir, repo_root=repo_root, day_stamp=day_stamp)
    events = _load_events(session_dir)
    pairs = _pair_trades(events)
    acc_by_key = {
        (str(a.get("symbol") or ""), str(a.get("entry_time") or "")): a for a, _ in pairs
    }

    y = f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}"
    push_dir = repo_root / "kabu_native" / "data" / "push_jsonl" / y
    ring_cache: dict[str, list[tuple[float, float]]] = {}
    momentum_samples: list[float] = []
    out: list[ShadowTradeRow] = []

    for t in trades:
        sym = t.symbol
        push_path = push_dir / f"{sym}.jsonl"
        if not push_path.is_file():
            push_path = push_dir / f"{sym.replace('.T', '')}.jsonl"

        if sym not in ring_cache:
            ring_cache[sym] = _load_price_ring(push_path, up_to_ts=t.entry_ts + 7200)
        ring = [x for x in ring_cache.get(sym, []) if x[0] <= t.entry_ts]

        acc = acc_by_key.get((sym, t.entry_time), {})
        payload = _load_push_payload_at_entry(push_path, t.entry_ts)
        if payload.get("CurrentPrice") is None:
            payload["CurrentPrice"] = t.current_price or t.entry_price

        mom = t.momentum_continuation_score
        if mom is not None:
            momentum_samples.append(float(mom))

        entry_shadow = compute_entry_shadow_fields(
            trade=acc or t.to_dict(),
            payload=payload,
            price_ring=ring,
            entry_ts=t.entry_ts,
            session_momentum_samples=momentum_samples,
        )
        rich_ticks = [{"ts_epoch": ts, "price": px} for ts, px in ring_cache.get(sym, [])]
        exit_shadow = enrich_exit_shadow_fields(
            entry_shadow,
            rich_ticks=rich_ticks,
            entry_price=t.entry_price,
            entry_ts=t.entry_ts,
        )
        out.append(ShadowTradeRow(trade=t, shadow=exit_shadow))
    return out


def _summarize_group(rows: Sequence[ShadowTradeRow]) -> dict[str, Any]:
    if not rows:
        return {"trade_count": 0}
    pnls = [r.trade.pnl_pct for r in rows]
    n = len(rows)
    stop = sum(1 for r in rows if r.trade.exit_reason == "stop_hit")
    trail = sum(1 for r in rows if r.trade.exit_reason == "trailing_mfe_exit")
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(_mean(pnls) or 0.0, 4),
        "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4),
        "stop_hit_count": stop,
        "stop_hit_rate": round(stop / n, 4),
        "trailing_mfe_exit_count": trail,
        "trailing_mfe_exit_rate": round(trail / n, 4),
        "avg_r30_sec": round(
            _mean([r.trade.r30_sec for r in rows if r.trade.r30_sec is not None]) or 0, 4
        ),
        "avg_r60_sec": round(
            _mean([r.trade.r60_sec for r in rows if r.trade.r60_sec is not None]) or 0, 4
        ),
        "avg_r120_sec": round(
            _mean([r.trade.r120_sec for r in rows if r.trade.r120_sec is not None]) or 0, 4
        ),
    }


def _feature_value(row: ShadowTradeRow, key: str) -> Optional[float]:
    if key in ("hold_sec", "max_mfe_until_exit", "max_mae_until_exit"):
        return _float(getattr(row.trade, key, None))
    if key in ("r30_sec", "r60_sec", "r120_sec"):
        return _float(getattr(row.trade, key, None))
    if key.startswith("entry_"):
        return _float(row.shadow.get(key))
    if key in ("continuation_quality_score", "momentum_continuation_score"):
        return _float(getattr(row.trade, key, None))
    return _float(row.shadow.get(key) or getattr(row.trade, key, None))


def _feature_profile(rows: Sequence[ShadowTradeRow]) -> dict[str, Any]:
    if not rows:
        return {}
    out: dict[str, Any] = {"trade_count": len(rows)}
    for key in FEATURE_KEYS:
        clean = [_feature_value(r, key) for r in rows]
        clean = [v for v in clean if v is not None]
        out[f"avg_{key}"] = round(_mean(clean), 4) if clean else None
    out["high_quality_low_momentum_rate"] = round(
        sum(1 for r in rows if r.shadow.get("high_quality_low_momentum_shadow_flag")) / len(rows),
        4,
    )
    out["extended_plus_early_adverse_rate"] = round(
        sum(1 for r in rows if r.shadow.get("extended_plus_early_adverse_shadow_flag")) / len(rows),
        4,
    )
    return out


def _delta_vs(base: Mapping[str, Any], other: Mapping[str, Any]) -> dict[str, Any]:
    def _d(k: str) -> Optional[float]:
        a, b = base.get(k), other.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return round(float(b) - float(a), 4)
        return None

    return {
        "trade_count": _d("trade_count"),
        "total_pnl_pct": _d("total_pnl_pct"),
        "profit_factor": _d("profit_factor"),
        "stop_hit_rate": _d("stop_hit_rate"),
        "trailing_mfe_exit_rate": _d("trailing_mfe_exit_rate"),
        "avg_r30_sec": _d("avg_r30_sec"),
        "avg_r60_sec": _d("avg_r60_sec"),
        "avg_r120_sec": _d("avg_r120_sec"),
    }


def _scenario_exclude_reason(rows: Sequence[ShadowTradeRow], reason: str) -> list[ShadowTradeRow]:
    return [r for r in rows if not r.reason_hit(reason)]


def _pick_worst_reason(by_reason: dict[str, Any]) -> str:
    ranked: list[tuple[str, float, float]] = []
    for reason in REASON_KEYS:
        block = by_reason.get(reason) or {}
        pf = block.get("profit_factor")
        total = block.get("total_pnl_pct")
        if block.get("trade_count", 0) <= 0:
            continue
        ranked.append(
            (
                reason,
                float(pf) if isinstance(pf, (int, float)) else 999.0,
                float(total) if isinstance(total, (int, float)) else 0.0,
            )
        )
    if not ranked:
        return "vwap_dev"
    ranked.sort(key=lambda x: (x[1], x[2]))
    return ranked[0][0]


def _select_reject_candidate(
    *,
    flag_compare: dict[str, Any],
    by_reason: dict[str, Any],
    post_hoc: dict[str, Any],
    false_positive: dict[str, Any],
) -> dict[str, Any]:
    ext = flag_compare.get("extended_flag_true") or {}
    no_ext = flag_compare.get("extended_flag_false") or {}
    ext_pf = ext.get("profit_factor")
    no_ext_pf = no_ext.get("profit_factor")

    active_reasons = [
        r for r in REASON_KEYS if int((by_reason.get(r) or {}).get("trade_count") or 0) > 0
    ]
    worst_reason = by_reason.get("worst_reason") or _pick_worst_reason(by_reason)
    worst_block = by_reason.get(worst_reason) or {}

    scenario_rank: list[tuple[str, float, float, int]] = []
    for key, reason in (("B", "rise_5min"), ("C", "vwap_dev"), ("D", "rolling_mfe"), ("E", "high_break_recent")):
        sc = post_hoc.get(key) or {}
        excluded = int(sc.get("excluded_count") or 0)
        if excluded <= 0:
            continue
        delta_pf = sc.get("delta_profit_factor_vs_A")
        delta_pnl = sc.get("delta_total_pnl_pct_vs_A")
        scenario_rank.append(
            (
                reason,
                float(delta_pnl) if isinstance(delta_pnl, (int, float)) else 0.0,
                float(delta_pf) if isinstance(delta_pf, (int, float)) else 0.0,
                excluded,
            )
        )
    scenario_rank.sort(key=lambda x: (x[1], x[2]))
    best_post_hoc_reason = scenario_rank[-1][0] if scenario_rank else worst_reason

    composite_hurts = (
        isinstance(ext_pf, (int, float))
        and isinstance(no_ext_pf, (int, float))
        and float(ext_pf) < float(no_ext_pf) - 0.05
    )
    composite_helps = (
        isinstance(ext_pf, (int, float))
        and isinstance(no_ext_pf, (int, float))
        and float(ext_pf) > float(no_ext_pf) + 0.05
    )

    if composite_hurts:
        if best_post_hoc_reason == worst_reason:
            selected = f"extended_entry_shadow_{worst_reason}"
            rationale = (
                f"Composite extended flag underperforms (PF {ext_pf} vs {no_ext_pf}). "
                f"Component '{worst_reason}' has lowest PF ({worst_block.get('profit_factor')}) "
                f"and best post-hoc lift when excluded alone."
            )
        else:
            selected = "extended_entry_shadow_flag"
            rationale = (
                f"Composite extended flag underperforms (PF {ext_pf} vs {no_ext_pf}); "
                "multiple legs contribute — use full composite for reject candidate."
            )
    elif composite_helps:
        selected = f"extended_entry_shadow_{worst_reason}"
        rationale = (
            f"Composite flag is NOT worse on this session (extended PF {ext_pf} > non-extended {no_ext_pf}). "
            f"Reject candidate narrows to worst active component '{worst_reason}' "
            f"(PF {worst_block.get('profit_factor')}, post-hoc exclude delta_pnl "
            f"{(post_hoc.get('C' if worst_reason == 'vwap_dev' else 'D') or {}).get('delta_total_pnl_pct_vs_A')})."
        )
    else:
        selected = f"extended_entry_shadow_{best_post_hoc_reason}"
        rationale = (
            f"Composite split is mixed (extended PF {ext_pf}, non-extended {no_ext_pf}). "
            f"Post-hoc best single exclude: '{best_post_hoc_reason}'."
        )

    return {
        "selected_shadow_feature": selected,
        "rationale": rationale,
        "hypothesis_on_session": {
            "composite_extended_worse_than_non_extended": composite_hurts,
            "composite_extended_better_than_non_extended": composite_helps,
            "active_reasons_on_session": active_reasons,
        },
        "evidence": {
            "extended_flag_true_pf": ext_pf,
            "extended_flag_false_pf": no_ext_pf,
            "worst_reason_by_component_pf": worst_reason,
            "worst_reason_pf": worst_block.get("profit_factor"),
            "best_post_hoc_single_exclude": best_post_hoc_reason,
            "false_positive_rate": false_positive.get("rate_of_extended"),
            "post_hoc_F_worst_only": (post_hoc.get("F") or {}).get("excluded_reason"),
        },
        "next_step": "shadow-only reject candidate review (no hard reject until validated on additional sessions)",
    }


def evaluate_extended_entry_shadow_impact(
    session_dir: Path,
    *,
    repo_root: Path,
    day_stamp: str,
) -> dict[str, Any]:
    rows = build_shadow_trade_rows(session_dir, repo_root=repo_root, day_stamp=day_stamp)

    ext_rows = [r for r in rows if r.extended]
    no_ext_rows = [r for r in rows if not r.extended]
    flag_compare = {
        "extended_flag_true": _summarize_group(ext_rows),
        "extended_flag_false": _summarize_group(no_ext_rows),
        "delta_extended_minus_non_extended": _delta_vs(
            _summarize_group(no_ext_rows), _summarize_group(ext_rows)
        ),
    }

    by_reason: dict[str, Any] = {}
    for reason in REASON_KEYS:
        hit = [r for r in rows if r.reason_hit(reason)]
        by_reason[reason] = {
            **_summarize_group(hit),
            "share_of_all_trades": round(len(hit) / max(1, len(rows)), 4),
            "share_of_extended_trades": round(len(hit) / max(1, len(ext_rows)), 4),
        }
    worst = _pick_worst_reason(by_reason)
    by_reason["worst_reason"] = worst
    by_reason["ranking_by_profit_factor"] = sorted(
        REASON_KEYS,
        key=lambda r: float((by_reason.get(r) or {}).get("profit_factor") or 999),
    )

    ext_stop = [r for r in ext_rows if r.trade.exit_reason == "stop_hit"]
    ext_non_stop = [r for r in ext_rows if r.trade.exit_reason != "stop_hit"]
    ext_trail = [r for r in ext_rows if r.trade.exit_reason == "trailing_mfe_exit"]
    ext_non_trail = [r for r in ext_rows if r.trade.exit_reason != "trailing_mfe_exit"]

    ext_and_stop = {
        "trade_count": len(ext_stop),
        "summary": _summarize_group(ext_stop),
        "common_features": _feature_profile(ext_stop),
        "vs_extended_non_stop_hit": {
            "non_stop_summary": _summarize_group(ext_non_stop),
            "feature_delta": _delta_vs(_feature_profile(ext_non_stop), _feature_profile(ext_stop)),
        },
    }

    ext_and_trailing = {
        "trade_count": len(ext_trail),
        "summary": _summarize_group(ext_trail),
        "common_features": _feature_profile(ext_trail),
        "vs_extended_non_trailing_mfe": {
            "non_trailing_summary": _summarize_group(ext_non_trail),
            "feature_delta": _delta_vs(_feature_profile(ext_non_trail), _feature_profile(ext_trail)),
        },
    }

    fp = [r for r in ext_rows if r.trade.pnl_pct > 0]
    false_positive = {
        "count": len(fp),
        "rate_of_extended": round(len(fp) / max(1, len(ext_rows)), 4),
        "avg_pnl_pct": round(_mean([r.trade.pnl_pct for r in fp]) or 0.0, 4),
        "total_pnl_pct": round(sum(r.trade.pnl_pct for r in fp), 4),
        "note": "extended_entry_shadow_flag true but pnl_pct > 0",
    }

    baseline = _summarize_group(rows)
    post_hoc: dict[str, Any] = {
        "A": {
            "description": "current (all accepted)",
            **_summarize_group(rows),
            "excluded_count": 0,
        },
    }
    scenario_map = {
        "B": ("rise_5min only excluded (post-hoc)", "rise_5min"),
        "C": ("vwap_dev only excluded (post-hoc)", "vwap_dev"),
        "D": ("rolling_mfe only excluded (post-hoc)", "rolling_mfe"),
        "E": ("high_break_recent only excluded (post-hoc)", "high_break_recent"),
    }
    for key, (desc, reason) in scenario_map.items():
        kept = _scenario_exclude_reason(rows, reason)
        summ = _summarize_group(kept)
        post_hoc[key] = {
            "description": desc,
            "excluded_reason": reason,
            "excluded_count": len(rows) - len(kept),
            **summ,
            "delta_total_pnl_pct_vs_A": round(
                float(summ.get("total_pnl_pct") or 0) - float(baseline.get("total_pnl_pct") or 0),
                4,
            ),
            "delta_profit_factor_vs_A": None,
        }
        pf_a = baseline.get("profit_factor")
        pf_k = summ.get("profit_factor")
        if isinstance(pf_a, (int, float)) and isinstance(pf_k, (int, float)):
            post_hoc[key]["delta_profit_factor_vs_A"] = round(float(pf_k) - float(pf_a), 4)

    worst_only = worst
    kept_f = _scenario_exclude_reason(rows, worst_only)
    summ_f = _summarize_group(kept_f)
    post_hoc["F"] = {
        "description": "worst single condition only excluded (post-hoc)",
        "excluded_reason": worst_only,
        "excluded_count": len(rows) - len(kept_f),
        **summ_f,
        "delta_total_pnl_pct_vs_A": round(
            float(summ_f.get("total_pnl_pct") or 0) - float(baseline.get("total_pnl_pct") or 0),
            4,
        ),
        "delta_profit_factor_vs_A": None,
    }
    pf_a = baseline.get("profit_factor")
    pf_f = summ_f.get("profit_factor")
    if isinstance(pf_a, (int, float)) and isinstance(pf_f, (int, float)):
        post_hoc["F"]["delta_profit_factor_vs_A"] = round(float(pf_f) - float(pf_a), 4)

    reject_candidate = _select_reject_candidate(
        flag_compare=flag_compare,
        by_reason=by_reason,
        post_hoc=post_hoc,
        false_positive=false_positive,
    )

    summary_path = session_dir / "small_paper_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}

    return {
        "phase": 184,
        "mode": "extended_entry_shadow_impact_review",
        "hypothesis": "Phase183 extended_entry_shadow_flag marks entries with worse expectancy.",
        "day_stamp": day_stamp,
        "session_dir": str(session_dir).replace("\\", "/"),
        "session_summary_snippet": {
            "accepted_count": summary.get("accepted_count"),
            "structural_exit_reason_counts": summary.get("structural_exit_reason_counts"),
        },
        "trade_count": len(rows),
        "fixed_thresholds": {
            "RISE_5MIN_PCT_MIN": RISE_5MIN_PCT_MIN,
            "VWAP_DEV_PCT_MIN": VWAP_DEV_PCT_MIN,
            "ROLLING_MFE_PCT_MIN": ROLLING_MFE_PCT_MIN,
            "note": "Phase183 fixed thresholds; not tuned on review day.",
        },
        "constraints": {
            "hard_reject": False,
            "shadow_review_only": True,
            "no_single_day_optimization": True,
            "fixed_comparisons_only": True,
        },
        "extended_flag_vs_no_flag": flag_compare,
        "by_extended_reason": by_reason,
        "extended_and_stop_hit": ext_and_stop,
        "extended_and_trailing_mfe_exit": ext_and_trailing,
        "false_positive": false_positive,
        "post_hoc_scenarios": post_hoc,
        "reject_candidate_selection": reject_candidate,
        "trades": [r.to_dict() for r in rows],
    }
