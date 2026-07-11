"""Phase670 — Flat weak + range reject forward shadow validation (research)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    load_all_full_period_trades,
)
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.phase666_breakout_initiation_analysis import (
    BIG_WINNER_YEN,
    _build_accept_index,
    _class_metrics,
    _is_mfe0,
    _is_no_progress,
    _is_stop_hit,
)
from research.phase665_pretrend_shape_analysis import _build_price_index_canonical
from research.phase667_flat_vwap_volume_refinement import _enrich_trade_full
from research.phase668_existing_shadow_adoption_review import _filter_canonical
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.flat_weak_range_forward_shadow import (
    BIG_WINNER_YEN as SHADOW_BIG_WINNER_YEN,
    evaluate_flat_weak_range_shadow,
    would_block_flat_weak_range_shadow,
)
from small_paper.pbv2_flat_band_entry_guard import would_block_flat_band_mainline

PHASE670_VERDICT = "phase670_flat_weak_range_forward_shadow_ready"
REPORT_DIR_NAME = "phase670_flat_weak_range_forward_shadow"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
DISK_USAGE_MAX_PCT = 75.0
MIN_FORWARD_DAYS = 5
PREFERRED_FORWARD_DAYS = 10
ADOPT_DELTA_PF_MIN = 0.03
ADOPT_IMPROVED_DAY_RATE_MIN = 0.60

MAINLINE_CFG = SimpleNamespace(
    pbv2_flat_band_mainline_enabled=True,
    pbv2_flat_band_shadow_enabled=False,
    pbv2_flat_band_shadow_apply_pool="PBV2_ONLY",
    pbv2_flat_band_shadow_rise5_flat_min_pct=0.0,
    pbv2_flat_band_shadow_rise5_flat_max_pct=0.5,
    pbv2_flat_band_shadow_rise10_flat_min_pct=-0.5,
    pbv2_flat_band_shadow_rise10_flat_max_pct=0.5,
    pbv2_flat_band_shadow_overheat_rise5_pct=2.0,
)


def _session_bucket(trade: Mapping[str, Any]) -> str:
    mins = trade.get("minutes_from_open")
    try:
        m = float(mins) if mins is not None else None
    except (TypeError, ValueError):
        m = None
    if m is None:
        return str(trade.get("session_kind") or "unknown")
    if m < 150:
        return "AM"
    if m >= 210:
        return "PM"
    return "lunch"


def _post_flat_band_mainline_trades(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for t in trades:
        blocked, _ = would_block_flat_band_mainline(MAINLINE_CFG, t)
        if not blocked:
            kept.append(dict(t))
    return kept


def _enrich_all(trades: list[dict[str, Any]], *, repo_root: Path) -> list[dict[str, Any]]:
    price_idx = _build_price_index_canonical(repo_root)
    accept_idx = _build_accept_index()
    return [_enrich_trade_full(dict(t), price_idx=price_idx, accept_idx=accept_idx) for t in trades]


def _shadow_trade_row(trade: Mapping[str, Any]) -> dict[str, Any]:
    blocked, reason = evaluate_flat_weak_range_shadow(trade)
    actual = float(trade.get("pnl_yen_100") or 0)
    shadow = 0.0 if blocked else actual
    return {
        "day": trade.get("day"),
        "symbol": trade.get("symbol"),
        "entry_type": trade.get("entry_type") or trade.get("entry_pool"),
        "session_bucket": _session_bucket(trade),
        "pretrend_shape": trade.get("pretrend_shape"),
        "flat_subclass": trade.get("breakout_class") if trade.get("pretrend_shape") == "E" else "",
        "breakout_class": trade.get("breakout_class"),
        "flat_weak_range_shadow_candidate": True,
        "flat_weak_range_shadow_block": blocked,
        "flat_weak_range_shadow_reason": reason,
        "actual_pnl_yen_100": round(actual, 2),
        "shadow_pnl_yen_100": round(shadow, 2),
        "delta_yen": round(shadow - actual, 2),
        "blocked_winner": bool(blocked and actual > 0),
        "blocked_loser": bool(blocked and actual < 0),
        "blocked_big_winner": bool(blocked and actual >= SHADOW_BIG_WINNER_YEN),
        "stop_hit": _is_stop_hit(trade),
        "no_progress": _is_no_progress(trade),
        "mfe0": _is_mfe0(trade),
    }


def _portfolio_from_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not trades:
        return _class_metrics([])
    blocked = [t for t in trades if would_block_flat_weak_range_shadow(t)]
    kept = [t for t in trades if not would_block_flat_weak_range_shadow(t)]
    base = _class_metrics(list(trades))
    kept_m = _class_metrics(kept)
    blocked_w = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) > 0)
    blocked_l = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0)
    blocked_bw = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) >= BIG_WINNER_YEN)
    bw_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in blocked if float(t.get("pnl_yen_100") or 0) >= BIG_WINNER_YEN)
    delta_pnl = round(float(kept_m.get("total_pnl_yen_100") or 0) - float(base.get("total_pnl_yen_100") or 0), 2)
    base_pf = base.get("profit_factor")
    kept_pf = kept_m.get("profit_factor")
    delta_pf = (
        round(float(kept_pf or 0) - float(base_pf or 0), 4)
        if base_pf is not None and kept_pf is not None
        else None
    )
    return {
        "entry_count": len(trades),
        "blocked_count": len(blocked),
        "kept_count": len(kept),
        "baseline_pnl_yen": base.get("total_pnl_yen_100"),
        "shadow_pnl_yen": kept_m.get("total_pnl_yen_100"),
        "delta_pnl_yen": delta_pnl,
        "baseline_pf": base_pf,
        "shadow_pf": kept_pf,
        "delta_pf": delta_pf,
        "blocked_winners": blocked_w,
        "blocked_losers": blocked_l,
        "blocked_big_winners": blocked_bw,
        "blocked_big_winner_pnl": round(bw_pnl, 2),
        "baseline_stop_hit_rate": base.get("stop_hit_rate"),
        "shadow_stop_hit_rate": kept_m.get("stop_hit_rate"),
        "stop_hit_reduction": round(
            float(base.get("stop_hit_rate") or 0) - float(kept_m.get("stop_hit_rate") or 0), 4
        ),
        "baseline_no_progress_rate": base.get("no_progress_exit_rate"),
        "shadow_no_progress_rate": kept_m.get("no_progress_exit_rate"),
        "no_progress_reduction": round(
            float(base.get("no_progress_exit_rate") or 0) - float(kept_m.get("no_progress_exit_rate") or 0),
            4,
        ),
        "baseline_mfe0_rate": base.get("mfe0_rate"),
        "shadow_mfe0_rate": kept_m.get("mfe0_rate"),
        "mfe0_reduction": round(float(base.get("mfe0_rate") or 0) - float(kept_m.get("mfe0_rate") or 0), 4),
    }


def _daily_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        sub = by_day[day]
        base_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in sub)
        kept = [t for t in sub if not would_block_flat_weak_range_shadow(t)]
        shadow_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in kept)
        blocked = [t for t in sub if would_block_flat_weak_range_shadow(t)]
        rows.append(
            {
                "day": day,
                "entry_count": len(sub),
                "blocked_count": len(blocked),
                "baseline_pnl_yen": round(base_pnl, 2),
                "shadow_pnl_yen": round(shadow_pnl, 2),
                "delta_pnl_yen": round(shadow_pnl - base_pnl, 2),
                "improved_day": shadow_pnl >= base_pnl,
                "blocked_winners": sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) > 0),
                "blocked_losers": sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0),
            }
        )
    return rows


def _adopt_assessment(portfolio: Mapping[str, Any], daily: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    improved = [d for d in daily if d.get("improved_day")]
    improved_rate = round(len(improved) / len(daily), 4) if daily else 0.0
    checks = {
        "delta_pnl_positive": float(portfolio.get("delta_pnl_yen") or 0) > 0,
        "delta_pf_ge_003": float(portfolio.get("delta_pf") or -999) >= ADOPT_DELTA_PF_MIN,
        "blocked_losers_ge_winners": int(portfolio.get("blocked_losers") or 0)
        >= int(portfolio.get("blocked_winners") or 0),
        "improved_days_ge_60pct": improved_rate >= ADOPT_IMPROVED_DAY_RATE_MIN,
        "big_winner_blocked_not_excessive": int(portfolio.get("blocked_big_winners") or 0) <= max(
            1, int(portfolio.get("blocked_losers") or 0) // 3
        ),
        "min_forward_days_met": len(daily) >= MIN_FORWARD_DAYS,
        "preferred_forward_days_met": len(daily) >= PREFERRED_FORWARD_DAYS,
    }
    adopt_candidate = all(
        checks[k]
        for k in (
            "delta_pnl_positive",
            "delta_pf_ge_003",
            "blocked_losers_ge_winners",
            "improved_days_ge_60pct",
            "big_winner_blocked_not_excessive",
        )
    )
    return {
        "checks": checks,
        "improved_day_rate": improved_rate,
        "adopt_candidate": adopt_candidate,
        "recommendation": "ADOPT_CANDIDATE" if adopt_candidate else "CONTINUE_FORWARD_SHADOW",
    }


def _write_decision_md(*, report: Mapping[str, Any]) -> None:
    assess = report.get("adopt_assessment") or {}
    port = report.get("portfolio") or {}
    lines = [
        "# Phase670 — Flat Weak + Range Forward Shadow",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        f"**Recommendation:** `{assess.get('recommendation')}`",
        "",
        "## Universe",
        "",
        f"- Canonical days: {report.get('trading_day_count')}",
        f"- Post flat-band mainline entries: {report.get('post_flat_band_entry_count')}",
        f"- Flat-band mainline blocked (historical): {report.get('flat_band_mainline_blocked_count')}",
        "",
        "## Shadow effect (post flat-band)",
        "",
        f"- Blocked: {port.get('blocked_count')} / {port.get('entry_count')}",
        f"- ΔPnL: {port.get('delta_pnl_yen'):+,.0f}",
        f"- ΔPF: {port.get('delta_pf'):+.4f}" if port.get("delta_pf") is not None else "- ΔPF: —",
        f"- Blocked W/L: {port.get('blocked_winners')}/{port.get('blocked_losers')}",
        f"- stop_hit↓: {port.get('stop_hit_reduction')}",
        f"- no_progress↓: {port.get('no_progress_reduction')}",
        f"- MFE0↓: {port.get('mfe0_reduction')}",
        "",
        "## ADOPT checks",
        "",
        f"```json\n{json.dumps(assess.get('checks'), ensure_ascii=False, indent=2)}\n```",
        "",
        "## Constraints",
        "",
        "- Forward shadow only; no mainline reject",
        "- Flat-band mainline unchanged",
        "- rise5 / vwap / EXIT T2/T3 remain disabled",
        "",
    ]
    (REPORT_ROOT / "phase670_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_forward_shadow_audit(*, skip_slow: bool = True) -> dict[str, Any]:
    del skip_slow
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    repo_root = resolve_kabu_root(NATIVE_ROOT)
    trades, sessions = load_all_full_period_trades(repo_root / "results" / "small_paper")
    trades, _sessions = _filter_canonical([dict(t) for t in trades], sessions)
    flat_blocked = sum(1 for t in trades if would_block_flat_band_mainline(MAINLINE_CFG, t)[0])
    post_flat = _post_flat_band_mainline_trades(trades)
    enriched = _enrich_all(post_flat, repo_root=repo_root)
    trade_rows = [_shadow_trade_row(t) for t in enriched]
    daily = _daily_rows(enriched)
    portfolio = _portfolio_from_trades(enriched)
    adopt = _adopt_assessment(portfolio, daily)
    disk_after = _disk_usage_pct(NATIVE_ROOT)

    report: dict[str, Any] = {
        "verdict": PHASE670_VERDICT,
        "entry_count": len(trades),
        "post_flat_band_entry_count": len(post_flat),
        "flat_band_mainline_blocked_count": flat_blocked,
        "trading_day_count": len({t.get("day") for t in post_flat}),
        "canonical_days": list(CANONICAL_DAYS),
        "flat_weak_range_shadow_enabled": True,
        "portfolio": portfolio,
        "adopt_assessment": adopt,
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": disk_after,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase670_flat_weak_range_forward_shadow_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        REPORT_ROOT / "phase670_flat_weak_range_forward_shadow_daily.csv",
        [
            "day",
            "entry_count",
            "blocked_count",
            "baseline_pnl_yen",
            "shadow_pnl_yen",
            "delta_pnl_yen",
            "improved_day",
            "blocked_winners",
            "blocked_losers",
        ],
        daily,
    )
    _write_csv(
        REPORT_ROOT / "phase670_flat_weak_range_forward_shadow_trades.csv",
        [
            "day",
            "symbol",
            "entry_type",
            "session_bucket",
            "pretrend_shape",
            "flat_subclass",
            "breakout_class",
            "flat_weak_range_shadow_candidate",
            "flat_weak_range_shadow_block",
            "flat_weak_range_shadow_reason",
            "actual_pnl_yen_100",
            "shadow_pnl_yen_100",
            "delta_yen",
            "blocked_winner",
            "blocked_loser",
            "blocked_big_winner",
            "stop_hit",
            "no_progress",
            "mfe0",
        ],
        trade_rows,
    )
    _write_decision_md(report=report)
    return report


def main() -> None:
    report = run_forward_shadow_audit()
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "blocked": report["portfolio"]["blocked_count"],
                "delta_pnl": report["portfolio"]["delta_pnl_yen"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
