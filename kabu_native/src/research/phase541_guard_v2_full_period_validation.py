"""
Phase541 — Guard v2 full-period validation (research only).

Re-validates Phase540 guard candidates across all live paper sessions.
No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _is_stop_low_mfe,
    _latest_live_day,
    _num,
)
from research.phase527_entry_quality_guard import _chron_pnls
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup
from research.phase540_no_progress_mfe0_entry_quality import (
    _day_return_rank,
    _duplicate_flags,
    _entry_type_label,
    _hold_sec,
    _is_mfe0,
    _is_no_progress,
    _is_winner,
    _load_canonical_trades_for_day,
    _mfe_pct,
    _phase540_entry_features,
    _resolved_exit_reason,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.canonical_summary import is_stop_exit

PHASE541_VERDICT = "phase541_guard_v2_full_period_validation_done"
PERIOD_START = PERIOD_START_LIVE
MAX_WORKERS = 4
BIG_WINNER_MFE_PCT = 1.0

FIVE_MIN_POSITION_MAX = 33.3333
MOVING_AVERAGE_POSITION_MAX = 0.1314
ADX_MAX = 30.0

GUARD_IDS: tuple[str, ...] = (
    "A_baseline",
    "G3_adx_le30",
    "G11_five_min_position",
    "G12_five_min_ma",
    "G13_adx_five_min",
    "G14_adx_ma",
)

SUMMARY_FIELDS = [
    "guard_id",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "win_rate",
    "avg_pnl_yen_100",
    "mfe0_count",
    "stop_low_mfe_count",
    "no_progress_count",
    "stop_hit_count",
    "trailing_mfe_count",
    "session_close_count",
    "net_improvement_yen_100",
]

DAILY_FIELDS = [
    "day",
    "guard_id",
    "daily_pnl_yen_100",
    "daily_pf",
    "daily_trade_count",
    "daily_mfe0_count",
    "daily_no_progress_count",
    "daily_lost_winner_count",
    "daily_prevented_mfe0_count",
    "baseline_daily_pnl_yen_100",
    "daily_net_improvement_yen_100",
]

BLOCKED_FIELDS = [
    "guard_id",
    "blocked_trade_count",
    "blocked_future_pnl_yen_100",
    "blocked_future_mfe_median",
    "lost_winner_count",
    "lost_big_winner_count",
    "lost_or_overlay_count",
    "lost_pbv2_count",
]

MFE0_REDUCTION_FIELDS = [
    "guard_id",
    "prevented_mfe0_count",
    "prevented_mfe0_pnl_yen_100",
    "remaining_mfe0_count",
    "mfe0_reduction_rate",
    "no_progress_reduction_rate",
    "stop_low_mfe_reduction_rate",
]

DEPENDENCY_FIELDS = [
    "guard_id",
    "top1_symbol_contribution_yen_100",
    "top3_symbol_contribution_yen_100",
    "top1_day_contribution_yen_100",
    "top3_day_contribution_yen_100",
    "top10_trade_exclusion_net_yen_100",
    "top3_symbol_exclusion_net_yen_100",
    "top3_day_exclusion_net_yen_100",
]

FEATURE_QUALITY_FIELDS = [
    "feature",
    "non_null_count",
    "missing_rate",
    "zero_rate",
    "notes",
]

SUCCESS_CRITERIA_FIELDS = [
    "guard_id",
    "pnl_gt_baseline",
    "pf_gte_baseline",
    "maxdd_lte_baseline",
    "mfe0_lt_baseline",
    "no_progress_lt_baseline",
    "lost_big_winner_ok",
    "improvement_day_rate_gte_60",
    "top3_symbol_exclusion_ok",
    "top3_day_exclusion_ok",
    "all_success",
]


def _iter_calendar_days(start: str, end: str) -> list[str]:
    d0 = datetime.strptime(start, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    d1 = datetime.strptime(end, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    out: list[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def _discover_live_days(repo_root: Path, *, start: str, end: str) -> list[str]:
    kabu = resolve_kabu_root(repo_root)
    found: set[str] = set()
    for root_name in ("small_paper", "paper_trade"):
        root = kabu / "results" / root_name
        if not root.is_dir():
            continue
        for p in root.iterdir():
            if not p.is_dir() or not p.name.isdigit():
                continue
            if p.name < start or p.name > end:
                continue
            if any(p.glob("live_session_*")):
                found.add(p.name)
    return sorted(found)


def _trade_key(trade: Mapping[str, Any]) -> str:
    return "|".join(
        [
            str(trade.get("day") or "")[:8],
            str(trade.get("session") or ""),
            str(trade.get("symbol") or ""),
            str(trade.get("entry_time") or ""),
        ]
    )


def _spread_bps_preferred(trade: Mapping[str, Any], feats: Mapping[str, Any]) -> Optional[float]:
    if trade.get("spread_bps") not in (None, ""):
        return round(_num(trade.get("spread_bps")), 4)
    sp = feats.get("spread_bps") if feats.get("spread_bps") is not None else feats.get("spread")
    return round(_num(sp), 4) if sp is not None else None


def _enrich_trades_phase541(
    trades: Sequence[Mapping[str, Any]],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
) -> list[dict[str, Any]]:
    day_return_ranks = _day_return_rank(trades, bar_cache)
    dup = _duplicate_flags(trades)
    enriched: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        feats = _phase540_entry_features(
            row, bar_cache=bar_cache, micro_lookup=micro_lookup, day_return_ranks=day_return_ranks
        )
        feats["spread_bps"] = _spread_bps_preferred(row, feats)
        row.update(feats)
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        row["duplicate_entry_observed"] = dup.get(key, False)
        row["entry_type"] = _entry_type_label(row)
        enriched.append(row)
    return enriched


def _guard_allows(guard_id: str, feats: Mapping[str, Any]) -> bool:
    adx = feats.get("adx14")
    fmp = feats.get("five_min_position")
    ma = feats.get("moving_average_position")

    def _adx_ok() -> bool:
        return adx is not None and float(adx) <= ADX_MAX

    def _fmp_ok() -> bool:
        return fmp is not None and float(fmp) <= FIVE_MIN_POSITION_MAX

    def _ma_ok() -> bool:
        return ma is not None and float(ma) <= MOVING_AVERAGE_POSITION_MAX

    if guard_id == "A_baseline":
        return True
    if guard_id == "G3_adx_le30":
        return _adx_ok()
    if guard_id == "G11_five_min_position":
        return _fmp_ok()
    if guard_id == "G12_five_min_ma":
        return _fmp_ok() and _ma_ok()
    if guard_id == "G13_adx_five_min":
        return _adx_ok() and _fmp_ok()
    if guard_id == "G14_adx_ma":
        return _adx_ok() and _ma_ok()
    return True


def _exit_bucket_counts(trades: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    stop_hit = 0
    trailing = 0
    session_close = 0
    no_progress = 0
    for t in trades:
        reason = _resolved_exit_reason(t)
        if is_stop_exit(t) or str(t.get("stop_hit") or "").lower() in ("true", "1"):
            stop_hit += 1
        if reason == "no_progress_exit":
            no_progress += 1
        elif reason in ("trailing_mfe_exit",):
            trailing += 1
        elif reason in ("afternoon_session_close", "session_close", "session_end", "morning_session_close"):
            session_close += 1
    return {
        "stop_hit_count": stop_hit,
        "trailing_mfe_count": trailing,
        "session_close_count": session_close,
        "no_progress_count": no_progress,
    }


def _metrics_bundle(
    guard_id: str,
    accepted: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    baseline_pnl: float,
) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    total = round(sum(pnls), 2)
    wins = sum(1 for p in pnls if p > 0)
    exits = _exit_bucket_counts(accepted)
    return {
        "guard_id": guard_id,
        "total_pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "trade_count": len(pnls),
        "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
        "avg_pnl_yen_100": round(total / len(pnls), 2) if pnls else 0.0,
        "mfe0_count": sum(1 for t in accepted if _is_mfe0(t)),
        "stop_low_mfe_count": sum(1 for t in accepted if _is_stop_low_mfe(t)),
        **exits,
        "net_improvement_yen_100": round(total - baseline_pnl, 2),
        "_accepted": list(accepted),
        "_blocked": list(blocked),
    }


def _run_day_guard(
    day: str,
    guard_id: str,
    day_trades: Sequence[Mapping[str, Any]],
    baseline_day_pnl: float,
) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for trade in day_trades:
        row = dict(trade)
        if _guard_allows(guard_id, row):
            accepted.append(row)
        else:
            blocked.append(row)
    met = _metrics_bundle(guard_id, accepted, blocked, baseline_day_pnl)
    blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
    return {
        "day": day,
        "guard_id": guard_id,
        "daily_pnl_yen_100": met["total_pnl_yen_100"],
        "daily_pf": met["profit_factor"],
        "daily_trade_count": met["trade_count"],
        "daily_mfe0_count": met["mfe0_count"],
        "daily_no_progress_count": met["no_progress_count"],
        "daily_lost_winner_count": sum(1 for p in blocked_pnls if p > 0),
        "daily_prevented_mfe0_count": sum(1 for t in blocked if _is_mfe0(t)),
        "baseline_daily_pnl_yen_100": round(baseline_day_pnl, 2),
        "daily_net_improvement_yen_100": met["net_improvement_yen_100"],
        **{k: v for k, v in met.items() if k.startswith("_")},
    }


def _aggregate_summary(
    raw: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    by_guard: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in raw:
        by_guard[str(row.get("guard_id") or "")].append(row)

    out: list[dict[str, Any]] = []
    for gid in GUARD_IDS:
        rows = by_guard.get(gid, [])
        if not rows:
            continue
        accepted: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for r in rows:
            accepted.extend(r.get("_accepted") or [])
            blocked.extend(r.get("_blocked") or [])
        met = _metrics_bundle(gid, accepted, blocked, baseline_pnl)
        out.append({k: v for k, v in met.items() if not k.startswith("_")})
    return out


def _blocked_summary(
    raw: Sequence[Mapping[str, Any]],
    *,
    baseline_mfe0: int,
    baseline_np: int,
    baseline_slm: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    blocked_rows: list[dict[str, Any]] = []
    mfe0_rows: list[dict[str, Any]] = []
    blocked_trade_details: list[dict[str, Any]] = []

    by_guard: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in raw:
        by_guard[str(row.get("guard_id") or "")].append(row)

    for gid in GUARD_IDS:
        if gid == "A_baseline":
            continue
        blocked: list[dict[str, Any]] = []
        accepted: list[dict[str, Any]] = []
        for r in by_guard.get(gid, []):
            blocked.extend(r.get("_blocked") or [])
            accepted.extend(r.get("_accepted") or [])
        blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
        blocked_mfe = [_mfe_pct(t) for t in blocked]
        lost_winners = [t for t in blocked if _is_winner(t)]
        lost_big = [t for t in blocked if _is_winner(t) and _mfe_pct(t) > BIG_WINNER_MFE_PCT]
        prevented_mfe0 = [t for t in blocked if _is_mfe0(t)]
        prevented_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in prevented_mfe0), 2)
        remaining_mfe0 = sum(1 for t in accepted if _is_mfe0(t))
        remaining_np = sum(1 for t in accepted if _is_no_progress(t))
        remaining_slm = sum(1 for t in accepted if _is_stop_low_mfe(t))

        blocked_rows.append(
            {
                "guard_id": gid,
                "blocked_trade_count": len(blocked),
                "blocked_future_pnl_yen_100": round(sum(blocked_pnls), 2),
                "blocked_future_mfe_median": round(statistics.median(blocked_mfe), 4) if blocked_mfe else None,
                "lost_winner_count": len(lost_winners),
                "lost_big_winner_count": len(lost_big),
                "lost_or_overlay_count": sum(1 for t in lost_winners if "OR" in _entry_type_label(t)),
                "lost_pbv2_count": sum(1 for t in lost_winners if _entry_type_label(t) == "PBV2"),
            }
        )
        mfe0_rows.append(
            {
                "guard_id": gid,
                "prevented_mfe0_count": len(prevented_mfe0),
                "prevented_mfe0_pnl_yen_100": prevented_pnl,
                "remaining_mfe0_count": remaining_mfe0,
                "mfe0_reduction_rate": round(
                    (baseline_mfe0 - remaining_mfe0) / baseline_mfe0, 4
                )
                if baseline_mfe0
                else 0.0,
                "no_progress_reduction_rate": round(
                    (baseline_np - remaining_np) / baseline_np, 4
                )
                if baseline_np
                else 0.0,
                "stop_low_mfe_reduction_rate": round(
                    (baseline_slm - remaining_slm) / baseline_slm, 4
                )
                if baseline_slm
                else 0.0,
            }
        )
        for t in blocked:
            blocked_trade_details.append(
                {
                    "guard_id": gid,
                    "day": t.get("day"),
                    "session": t.get("session"),
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "exit_reason": t.get("exit_reason"),
                    "pnl_yen_100": t.get("pnl_yen_100"),
                    "mfe_pct": round(_mfe_pct(t), 4),
                    "entry_type": _entry_type_label(t),
                    "is_winner": _is_winner(t),
                    "is_mfe0": _is_mfe0(t),
                    "is_big_winner": _is_winner(t) and _mfe_pct(t) > BIG_WINNER_MFE_PCT,
                }
            )
    return blocked_rows, mfe0_rows, blocked_trade_details


def _dependency_rows(
    raw: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    by_guard: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in raw:
        by_guard[str(row.get("guard_id") or "")].append(row)

    rows: list[dict[str, Any]] = []
    for gid in GUARD_IDS:
        if gid == "A_baseline":
            continue
        blocked: list[dict[str, Any]] = []
        accepted: list[dict[str, Any]] = []
        for r in by_guard.get(gid, []):
            blocked.extend(r.get("_blocked") or [])
            accepted.extend(r.get("_accepted") or [])
        net = round(sum(_num(t.get("pnl_yen_100")) for t in accepted) - baseline_pnl, 2)

        sym_delta: dict[str, float] = defaultdict(float)
        day_delta: dict[str, float] = defaultdict(float)
        for t in blocked:
            pnl = _num(t.get("pnl_yen_100"))
            sym = str(t.get("symbol") or "").replace(".T", "")
            day = str(t.get("day") or "")[:8]
            sym_delta[sym] -= pnl
            day_delta[day] -= pnl

        sym_sorted = sorted(sym_delta.items(), key=lambda x: x[1], reverse=True)
        day_sorted = sorted(day_delta.items(), key=lambda x: x[1], reverse=True)
        top1_sym = sym_sorted[0][1] if sym_sorted else 0.0
        top3_sym = round(sum(v for _, v in sym_sorted[:3]), 2)
        top1_day = day_sorted[0][1] if day_sorted else 0.0
        top3_day = round(sum(v for _, v in day_sorted[:3]), 2)

        blocked_by_loss = sorted(blocked, key=lambda t: _num(t.get("pnl_yen_100")))
        top10 = blocked_by_loss[:10]
        top10_excl_net = round(net + sum(_num(t.get("pnl_yen_100")) for t in top10), 2)
        top3_sym_excl = round(net - top3_sym, 2)
        top3_day_excl = round(net - top3_day, 2)

        rows.append(
            {
                "guard_id": gid,
                "top1_symbol_contribution_yen_100": round(top1_sym, 2),
                "top3_symbol_contribution_yen_100": top3_sym,
                "top1_day_contribution_yen_100": round(top1_day, 2),
                "top3_day_contribution_yen_100": top3_day,
                "top10_trade_exclusion_net_yen_100": top10_excl_net,
                "top3_symbol_exclusion_net_yen_100": top3_sym_excl,
                "top3_day_exclusion_net_yen_100": top3_day_excl,
            }
        )
    return rows


def _feature_quality_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    n = len(enriched) or 1
    checks = [
        ("spread_bps", "events spread_bps preferred"),
        ("momentum_score", ""),
        ("day_high_update_speed", ""),
        ("open_strength", ""),
    ]
    rows: list[dict[str, Any]] = []
    for feat, notes in checks:
        vals = [t.get(feat) for t in enriched]
        non_null = [v for v in vals if v is not None and v != ""]
        zeros = [v for v in non_null if _num(v) == 0.0]
        rows.append(
            {
                "feature": feat,
                "non_null_count": len(non_null),
                "missing_rate": round(1.0 - len(non_null) / n, 4),
                "zero_rate": round(len(zeros) / len(non_null), 4) if non_null else 0.0,
                "notes": notes,
            }
        )
    return rows


def _daily_stability(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by_guard: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        by_guard[str(row.get("guard_id") or "")].append(row)

    out: dict[str, dict[str, Any]] = {}
    for gid in GUARD_IDS:
        rows = by_guard.get(gid, [])
        if not rows:
            continue
        improvements = [_num(r.get("daily_net_improvement_yen_100")) for r in rows]
        positive_days = sum(1 for r in rows if _num(r.get("daily_pnl_yen_100")) > 0)
        improve_days = sum(1 for d in improvements if d > 0)
        out[gid] = {
            "positive_day_rate": round(positive_days / len(rows), 4) if rows else 0.0,
            "improvement_day_rate": round(improve_days / len(rows), 4) if rows else 0.0,
            "worst_day_delta": round(min(improvements), 2) if improvements else 0.0,
            "best_day_delta": round(max(improvements), 2) if improvements else 0.0,
        }
    return out


def _success_criteria(
    summary: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    dependency: Sequence[Mapping[str, Any]],
    stability: Mapping[str, Mapping[str, Any]],
    *,
    lost_big_winner_tolerance: int = 3,
) -> list[dict[str, Any]]:
    baseline = next((s for s in summary if s.get("guard_id") == "A_baseline"), {})
    blocked_by = {str(r.get("guard_id")): r for r in blocked}
    dep_by = {str(r.get("guard_id")): r for r in dependency}
    rows: list[dict[str, Any]] = []
    for s in summary:
        gid = str(s.get("guard_id") or "")
        if gid == "A_baseline":
            continue
        blk = blocked_by.get(gid, {})
        dep = dep_by.get(gid, {})
        stab = stability.get(gid, {})
        checks = {
            "pnl_gt_baseline": _num(s.get("total_pnl_yen_100")) > _num(baseline.get("total_pnl_yen_100")),
            "pf_gte_baseline": _num(s.get("profit_factor")) >= _num(baseline.get("profit_factor")),
            "maxdd_lte_baseline": _num(s.get("max_drawdown_yen_100")) <= _num(baseline.get("max_drawdown_yen_100")),
            "mfe0_lt_baseline": int(s.get("mfe0_count") or 0) < int(baseline.get("mfe0_count") or 0),
            "no_progress_lt_baseline": int(s.get("no_progress_count") or 0) < int(baseline.get("no_progress_count") or 0),
            "lost_big_winner_ok": int(blk.get("lost_big_winner_count") or 0) <= lost_big_winner_tolerance,
            "improvement_day_rate_gte_60": _num(stab.get("improvement_day_rate")) >= 0.6,
            "top3_symbol_exclusion_ok": _num(dep.get("top3_symbol_exclusion_net_yen_100")) > 0,
            "top3_day_exclusion_ok": _num(dep.get("top3_day_exclusion_net_yen_100")) > 0,
        }
        rows.append({"guard_id": gid, **checks, "all_success": all(checks.values())})
    return rows


def _mandatory_answers(
    summary: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    stability: Mapping[str, Mapping[str, Any]],
    success: Sequence[Mapping[str, Any]],
    enriched: Sequence[Mapping[str, Any]],
    dependency: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = next((s for s in summary if s.get("guard_id") == "A_baseline"), {})
    blocked_by = {str(r.get("guard_id")): r for r in blocked}
    success_by = {str(r.get("guard_id")): r for r in success}

    mfe0_trades = [t for t in enriched if _is_mfe0(t)]
    np_trades = [t for t in enriched if _is_no_progress(t)]
    total_loss = sum(_num(t.get("pnl_yen_100")) for t in enriched if _num(t.get("pnl_yen_100")) < 0)
    mfe0_loss = sum(_num(t.get("pnl_yen_100")) for t in mfe0_trades)
    np_loss = sum(_num(t.get("pnl_yen_100")) for t in np_trades)

    candidates = [g for g in GUARD_IDS if g != "A_baseline"]
    best = max(
        candidates,
        key=lambda g: (
            _num(next((s for s in summary if s.get("guard_id") == g), {}).get("net_improvement_yen_100")),
            int(success_by.get(g, {}).get("all_success", False)),
        ),
    )
    explainable = max(
        ("G3_adx_le30", "G13_adx_five_min", "G14_adx_ma"),
        key=lambda g: _num(
            next((s for s in summary if s.get("guard_id") == g), {}).get("net_improvement_yen_100")
        ),
    )

    g12_dep = next((d for d in dependency if d.get("guard_id") == "G12_five_min_ma"), {})
    g12_overfit = (
        abs(_num(g12_dep.get("top1_symbol_contribution_yen_100")))
        > abs(_num(next((s for s in summary if s.get("guard_id") == "G12_five_min_ma"), {}).get("net_improvement_yen_100", 1)))
        * 0.5
    )

    any_success = any(success_by.get(g, {}).get("all_success") for g in candidates)
    shadow_ready = any(
        success_by.get(g, {}).get("all_success")
        or (
            _num(next((s for s in summary if s.get("guard_id") == g), {}).get("net_improvement_yen_100")) > 0
            and _num(stability.get(g, {}).get("improvement_day_rate")) >= 0.5
        )
        for g in ("G3_adx_le30", "G13_adx_five_min", "G14_adx_ma")
    )

    max_lost_winners = max(int(blocked_by.get(g, {}).get("lost_winner_count") or 0) for g in candidates)

    def _guard_effective(gid: str) -> bool:
        s = next((x for x in summary if x.get("guard_id") == gid), {})
        return (
            _num(s.get("total_pnl_yen_100")) > _num(baseline.get("total_pnl_yen_100"))
            and _num(s.get("profit_factor")) >= _num(baseline.get("profit_factor"))
        )

    return {
        "1_g3_effective_full_period": _guard_effective("G3_adx_le30"),
        "2_g11_effective_full_period": _guard_effective("G11_five_min_position"),
        "3_g12_effective_full_period": _guard_effective("G12_five_min_ma"),
        "4_g12_overfit_risk": g12_overfit,
        "5_best_guard": best,
        "6_most_explainable_guard": explainable,
        "7_mfe0_primary_loss_driver": abs(mfe0_loss) >= abs(total_loss) * 0.25 if total_loss < 0 else False,
        "8_no_progress_primary_loss_driver": abs(np_loss) >= abs(total_loss) * 0.35 if total_loss < 0 else False,
        "9_winner_over_block": max_lost_winners > len(enriched) * 0.15,
        "10_shadow_forward_ready": shadow_ready,
        "11_production_adoption_candidate": any_success,
        "12_next_phase": (
            "Phase542: forward-shadow top guard(s) on new live days before Runtime wiring."
            if shadow_ready
            else "Extend live history; relax G12 thresholds; re-audit spread features."
        ),
        "baseline_pnl_yen_100": baseline.get("total_pnl_yen_100"),
        "baseline_trade_count": baseline.get("trade_count"),
        "mfe0_loss_yen_100": round(mfe0_loss, 2),
        "no_progress_loss_yen_100": round(np_loss, 2),
        "max_lost_winner_count": max_lost_winners,
    }


@dataclass
class Phase541Job:
    repo_root: Path
    period_start: str = PERIOD_START
    period_end: Optional[str] = None
    parallel: bool = True
    max_workers: int = MAX_WORKERS

    def run(self) -> dict[str, Any]:
        repo_root = self.repo_root.resolve()
        end = self.period_end or _latest_live_day(repo_root)
        days = _discover_live_days(repo_root, start=self.period_start, end=end)
        if not days:
            days = [d for d in _iter_calendar_days(self.period_start, end) if d >= self.period_start]

        kabu = resolve_kabu_root(repo_root)
        price_idx = _build_price_index_to(kabu, period_end=end)

        all_trades: list[dict[str, Any]] = []
        workers = min(max(1, self.max_workers), MAX_WORKERS)

        def _load_day(day: str) -> list[dict[str, Any]]:
            return _load_canonical_trades_for_day(repo_root, day, all_sessions=True)

        if self.parallel and len(days) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_load_day, d): d for d in days}
                for fut in as_completed(futs):
                    all_trades.extend(fut.result())
        else:
            for day in days:
                all_trades.extend(_load_day(day))

        if not all_trades:
            raise RuntimeError(f"no live trades found for Phase541 period {self.period_start}–{end}")

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in all_trades})
        bar_cache = _build_bar_cache_for_days(repo_root, days=days, symbols=symbols, price_idx=price_idx)
        micro_lookup = _build_micro_lookup(all_trades)
        enriched = _enrich_trades_phase541(all_trades, bar_cache=bar_cache, micro_lookup=micro_lookup)

        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in enriched:
            by_day[str(t.get("day") or "")[:8]].append(dict(t))

        baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in enriched), 2)
        baseline_mfe0 = sum(1 for t in enriched if _is_mfe0(t))
        baseline_np = sum(1 for t in enriched if _is_no_progress(t))
        baseline_slm = sum(1 for t in enriched if _is_stop_low_mfe(t))
        baseline_by_day = {
            day: round(sum(_num(t.get("pnl_yen_100")) for t in tr), 2) for day, tr in by_day.items()
        }

        jobs = [(day, gid) for day in sorted(by_day) for gid in GUARD_IDS]
        raw_details: list[dict[str, Any]] = []
        if self.parallel and jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(
                        _run_day_guard,
                        day,
                        gid,
                        by_day.get(day, []),
                        baseline_by_day.get(day, 0.0),
                    ): (day, gid)
                    for day, gid in jobs
                }
                for fut in as_completed(futs):
                    raw_details.append(fut.result())
        else:
            for day, gid in jobs:
                raw_details.append(
                    _run_day_guard(day, gid, by_day.get(day, []), baseline_by_day.get(day, 0.0))
                )

        daily_rows = [
            {k: v for k, v in row.items() if not k.startswith("_")} for row in raw_details
        ]
        summary = _aggregate_summary(raw_details, baseline_pnl=baseline_pnl)
        blocked_summary, mfe0_reduction, blocked_details = _blocked_summary(
            raw_details,
            baseline_mfe0=baseline_mfe0,
            baseline_np=baseline_np,
            baseline_slm=baseline_slm,
        )
        dependency = _dependency_rows(raw_details, baseline_pnl=baseline_pnl)
        feature_quality = _feature_quality_rows(enriched)
        stability = _daily_stability(daily_rows)
        success = _success_criteria(summary, blocked_summary, dependency, stability)
        mandatory = _mandatory_answers(
            summary, blocked_summary, stability, success, enriched, dependency
        )

        return {
            "verdict": PHASE541_VERDICT,
            "generated_at": _now_iso(),
            "period_start": self.period_start,
            "period_end": end,
            "live_days": days,
            "all_sessions": True,
            "trade_count": len(enriched),
            "parallel_workers": workers,
            "guard_summary": summary,
            "daily_stability": stability,
            "success_criteria": success,
            "mandatory_answers": mandatory,
            "guard_daily": daily_rows,
            "blocked_summary": blocked_summary,
            "blocked_trade_details": blocked_details,
            "mfe0_reduction": mfe0_reduction,
            "dependency": dependency,
            "feature_quality": feature_quality,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase541_guard_v2_full_period_summary.csv",
            "daily": reports / "phase541_guard_v2_daily.csv",
            "blocked": reports / "phase541_guard_v2_blocked_trades.csv",
            "mfe0_reduction": reports / "phase541_guard_v2_mfe0_reduction.csv",
            "dependency": reports / "phase541_guard_v2_dependency.csv",
            "feature_quality": reports / "phase541_feature_quality.csv",
            "report": reports / "phase541_report.json",
            "docs": kabu / "docs" / "operations" / "phase541_guard_v2_full_period_validation.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("guard_summary") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("guard_daily") or []))
        _write_csv(paths["blocked"], BLOCKED_FIELDS, list(result.get("blocked_summary") or []))
        _write_csv(paths["mfe0_reduction"], MFE0_REDUCTION_FIELDS, list(result.get("mfe0_reduction") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency") or []))
        _write_csv(paths["feature_quality"], FEATURE_QUALITY_FIELDS, list(result.get("feature_quality") or []))

        report_payload = {
            k: v
            for k, v in result.items()
            if k not in ("guard_daily", "blocked_trade_details")
        }
        paths["report"].write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    lines = [
        "# Phase541 — Guard v2 Full-Period Validation",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')} (all sessions)",
        f"**Trades:** {result.get('trade_count')}",
        "",
        "## Mandatory answers",
        "",
    ]
    labels = {
        "1_g3_effective_full_period": "1. G3 effective full period?",
        "2_g11_effective_full_period": "2. G11 effective full period?",
        "3_g12_effective_full_period": "3. G12 effective full period?",
        "4_g12_overfit_risk": "4. G12 overfit risk?",
        "5_best_guard": "5. Best guard",
        "6_most_explainable_guard": "6. Most explainable guard",
        "7_mfe0_primary_loss_driver": "7. MFE0 primary loss driver?",
        "8_no_progress_primary_loss_driver": "8. NoProgress primary loss driver?",
        "9_winner_over_block": "9. Winner over-block?",
        "10_shadow_forward_ready": "10. Shadow forward ready?",
        "11_production_adoption_candidate": "11. Production adoption candidate?",
        "12_next_phase": "12. Next phase",
    }
    for key, label in labels.items():
        lines.append(f"- **{label}** {ma.get(key)}")
    lines.extend(
        [
            "",
            "## Guards tested",
            "",
            "- A: baseline",
            "- G3: ADX14 <= 30",
            "- G11: five_min_position <= 33.3333",
            "- G12: five_min_position <= 33.3333 AND moving_average_position <= 0.1314",
            "- G13: ADX14 <= 30 AND five_min_position <= 33.3333",
            "- G14: ADX14 <= 30 AND moving_average_position <= 0.1314",
            "",
            "## Outputs",
            "",
            "- `results/reports/phase541_guard_v2_full_period_summary.csv`",
            "- `results/reports/phase541_guard_v2_daily.csv`",
            "- `results/reports/phase541_guard_v2_blocked_trades.csv`",
            "- `results/reports/phase541_guard_v2_mfe0_reduction.csv`",
            "- `results/reports/phase541_guard_v2_dependency.csv`",
            "- `results/reports/phase541_feature_quality.csv`",
            "- `results/reports/phase541_report.json`",
        ]
    )
    return "\n".join(lines) + "\n"
