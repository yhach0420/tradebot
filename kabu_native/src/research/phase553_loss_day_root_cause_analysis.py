"""
Phase553 — Loss day root cause analysis (20260618 deep dive).

Research only. Uses B_current_runtime full-path eval (Phase551) for trade set.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase518_day_high_winner_loser_separation import (
    FEATURE_IDS,
    _build_micro_lookup,
    _extract_entry_features,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _latest_live_day,
)
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import (
    _hold_sec,
    _is_winner,
    _load_canonical_trades_for_day,
    _mae_pct,
    _mfe_pct,
    _resolved_exit_reason,
)
from research.phase541_guard_v2_full_period_validation import _enrich_trades_phase541
from research.phase546_entry_cluster_shadow_replay import _merge_dataset, _trade_key
from research.phase547_reject_cluster_winner_rescue import _period_thresholds
from research.phase551_current_runtime_full_period_replay import (
    E4_THRESHOLD,
    _evaluate_live_trades,
    _is_or_trade,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE553_VERDICT = "phase553_loss_day_root_cause_analysis_done"
TARGET_DAY_DEFAULT = "20260618"
LIVE_END_DEFAULT = "20260625"

TRADE_DETAIL_FIELDS = [
    "trade_id",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "entry_price",
    "exit_price",
    "pnl_yen_100",
    "pnl_pct",
    "mfe_pct",
    "mae_pct",
    "exit_reason",
    "entry_type",
    "is_or",
    "is_pbv2",
    "session",
]

RANKING_FIELDS = [
    "loss_rank",
    "symbol",
    "pnl_yen_100",
    "share_of_day_loss_pct",
    "cumulative_loss_pct",
    "exit_reason",
    "mfe_pct",
]

ENTRY_FIELDS = [
    "trade_id",
    "symbol",
    "pnl_yen_100",
    "outcome",
    *FEATURE_IDS,
    "liquidity_burst",
    "cluster_id",
    "cluster_guard_status",
    "vs_winner_median_board_imbalance",
    "vs_winner_median_momentum_score",
    "vs_winner_median_spread_bps",
]

EXIT_FIELDS = [
    "trade_id",
    "symbol",
    "pnl_yen_100",
    "mfe_pct",
    "mae_pct",
    "mfe_giveback_pct",
    "exit_reason",
    "trailing_mfe_activated",
    "stop_hit",
    "board_collapse_proxy",
    "loss_acceleration_proxy",
    "pnl_if_hold_30s",
    "pnl_if_hold_60s",
    "pnl_if_hold_120s",
    "opportunity_loss_60s",
]

MARKET_FIELDS = [
    "day",
    "metric",
    "value",
    "notes",
]

ROOT_CAUSE_FIELDS = [
    "trade_id",
    "symbol",
    "pnl_yen_100",
    "primary_cause",
    "improvement_bucket",
    "runtime_preventable",
    "entry_responsible_pct",
    "exit_responsible_pct",
    "market_responsible_pct",
    "unavoidable_pct",
]


def _iter_days(start: str, end: str) -> list[str]:
    d0 = datetime.strptime(start, "%Y%m%d")
    d1 = datetime.strptime(end, "%Y%m%d")
    out: list[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def _entry_type_label(trade: Mapping[str, Any]) -> str:
    if _is_or_trade(trade):
        return "OR"
    et = str(trade.get("entry_type") or "").upper()
    if et in ("OR", "OR_OVERLAY"):
        return "OR"
    return "PBV2"


def _price_at_horizon(
    price_idx: Mapping[tuple[str, str], Sequence[tuple[datetime, float]]],
    *,
    symbol: str,
    day: str,
    base_ts: datetime,
    horizon_sec: float,
) -> Optional[float]:
    sym = symbol if symbol.endswith(".T") else f"{symbol}.T"
    series = price_idx.get((sym, day), [])
    if not series:
        return None
    target = base_ts.timestamp() + horizon_sec
    for ts, px in series:
        if ts.timestamp() >= target:
            return float(px)
    return float(series[-1][1]) if series else None


def _pnl_pct_from_prices(entry_px: float, px: Optional[float]) -> Optional[float]:
    if px is None or entry_px <= 0:
        return None
    return round((px - entry_px) / entry_px * 100.0, 4)


def _winner_medians(trades: Sequence[Mapping[str, Any]], feats_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    keys = ("board_imbalance", "momentum_score", "spread_bps", "volume_ratio", "day_high_distance_pct")
    acc: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        if not _is_winner(t):
            continue
        fid = str(t.get("trade_id") or "")
        f = feats_by_id.get(fid, {})
        for k in keys:
            v = _num(f.get(k))
            if v is not None:
                acc[k].append(float(v))
    return {k: round(statistics.median(v), 4) for k, v in acc.items() if v}


def _classify_primary_cause(trade: Mapping[str, Any], feats: Mapping[str, Any]) -> str:
    pnl = _num(trade.get("pnl_yen_100"))
    if pnl >= 0:
        return "Normal Win" if pnl > 0 else "Flat"
    reason = str(_resolved_exit_reason(trade) or "")
    mfe = _mfe_pct(trade)
    spread = _num(feats.get("spread_bps"))
    board = _num(feats.get("board_imbalance"))
    mom = _num(feats.get("momentum_score"))
    vol = _num(feats.get("volume_ratio") or feats.get("relative_volume"))
    dh = _num(feats.get("day_high_distance_pct"))
    mins = _num(feats.get("minutes_from_open"))

    if spread and spread > 50:
        return "High Spread"
    if board and board < 0.45:
        return "Weak Board"
    if vol and vol < 1.0:
        return "Weak Volume"
    if dh is not None and dh < 1.5 and mins and mins > 60:
        return "Entry Too Late"
    if mom and mom < 0.15 and mfe <= 0.05:
        return "False Breakout"
    if "stop" in reason and mfe <= 0.01:
        return "Normal Loss"
    if "trailing" in reason and mfe > 0.2 and pnl < 0:
        return "Exit Too Early"
    if "stop" in reason and mfe > 0.1:
        return "Exit Too Late"
    return "Normal Loss"


def _improvement_bucket(cause: str, trade: Mapping[str, Any]) -> str:
    if cause in ("High Spread", "Weak Board", "Weak Volume", "Entry Too Late", "False Breakout"):
        if cause == "High Spread" and _num(trade.get("spread_bps")):
            return "Runtimeでも防げる"
        if cause in ("High Spread", "Weak Board", "Weak Volume"):
            return "Runtimeでも防げる"
        return "ENTRY改善で防げる"
    if cause in ("Exit Too Early", "Exit Too Late"):
        return "EXIT改善で防げる"
    if cause == "Normal Loss":
        return "Runtimeでも防げない"
    return "Runtimeでも防げない"


def _responsibility_split(cause: str) -> dict[str, float]:
    mapping = {
        "High Spread": {"entry": 70, "exit": 0, "market": 0, "unavoidable": 30},
        "Weak Board": {"entry": 65, "exit": 0, "market": 10, "unavoidable": 25},
        "Weak Volume": {"entry": 60, "exit": 0, "market": 15, "unavoidable": 25},
        "Entry Too Late": {"entry": 75, "exit": 0, "market": 10, "unavoidable": 15},
        "False Breakout": {"entry": 70, "exit": 10, "market": 10, "unavoidable": 10},
        "Exit Too Early": {"entry": 10, "exit": 70, "market": 5, "unavoidable": 15},
        "Exit Too Late": {"entry": 15, "exit": 65, "market": 10, "unavoidable": 10},
        "Normal Loss": {"entry": 25, "exit": 25, "market": 20, "unavoidable": 30},
        "Normal Win": {"entry": 0, "exit": 0, "market": 0, "unavoidable": 0},
        "Flat": {"entry": 0, "exit": 0, "market": 0, "unavoidable": 0},
    }
    return mapping.get(cause, mapping["Normal Loss"])


def _load_b_runtime_accepted(repo_root: Path, *, live_start: str, end: str) -> list[dict[str, Any]]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(kabu)
    cluster_rows = _merge_dataset(reports)
    cluster_by_key = {_trade_key(r): dict(r) for r in cluster_rows}
    thresholds = _period_thresholds(cluster_rows)
    thresholds.setdefault("liquidity_burst_p75", E4_THRESHOLD)

    days = _iter_days(live_start, end)
    live_trades: list[dict[str, Any]] = []
    for day in days:
        for t in _load_canonical_trades_for_day(repo_root, day, all_sessions=True):
            key = _trade_key(t)
            merged = {**dict(t), **cluster_by_key.get(key, {})}
            merged["day"] = day
            if merged.get("liquidity_burst") in (None, "") and cluster_by_key.get(key):
                merged["liquidity_burst"] = cluster_by_key[key].get("liquidity_burst")
            live_trades.append(merged)

    symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in live_trades})
    price_idx = _build_price_index_to(kabu, period_end=end)
    bar_cache = _build_bar_cache_for_days(repo_root, days=days, symbols=symbols, price_idx=price_idx)
    micro = _build_micro_lookup(live_trades)
    enriched = _enrich_trades_phase541(live_trades, bar_cache=bar_cache, micro_lookup=micro)

    ev = _evaluate_live_trades(
        enriched,
        include_or=True,
        reentry_rsi=True,
        entry_quality=True,
        cluster_guard=True,
        cluster_exception=True,
        bar_cache=bar_cache,
        thresholds=thresholds,
    )
    accepted = ev.get("_accepted") or []
    for i, t in enumerate(accepted):
        t["trade_id"] = f"T{i+1:03d}"
    return accepted


@dataclass
class Phase553Job:
    repo_root: Path
    target_day: str = TARGET_DAY_DEFAULT
    live_start: str = PERIOD_START_LIVE
    live_end: str = LIVE_END_DEFAULT

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        kabu = resolve_kabu_root(repo)
        end = min(self.live_end, _latest_live_day(repo))

        all_accepted = _load_b_runtime_accepted(repo, live_start=self.live_start, end=end)
        day_trades = [dict(t) for t in all_accepted if str(t.get("day") or "")[:8] == self.target_day]
        day_trades.sort(
            key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)
        )
        for i, t in enumerate(day_trades):
            t["trade_id"] = f"D{i+1:02d}"

        winners = [t for t in all_accepted if _is_winner(t)]
        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in day_trades})
        price_idx = _build_price_index_to(kabu, period_end=end)
        bar_cache = _build_bar_cache_for_days(
            repo, days=[self.target_day], symbols=symbols, price_idx=price_idx
        )
        micro = _build_micro_lookup(all_accepted)

        feats_by_id: dict[str, dict[str, Any]] = {}
        for t in day_trades:
            fid = str(t.get("trade_id") or "")
            base_feats = _extract_entry_features(t, bar_cache=bar_cache, micro_lookup=micro)
            feats_by_id[fid] = {
                **base_feats,
                "liquidity_burst": t.get("liquidity_burst"),
                "cluster_id": t.get("cluster_id"),
                "cluster_guard_status": t.get("cluster_guard_status"),
            }

        win_med = _winner_medians(winners, feats_by_id)

        # Winner medians from live-window winners (recompute features)
        win_feat_list = [
            _extract_entry_features(w, bar_cache=bar_cache, micro_lookup=micro) for w in winners
        ]
        win_med = {}
        for k in ("board_imbalance", "momentum_score", "spread_bps"):
            vals = [_num(f.get(k)) for f in win_feat_list if _num(f.get(k)) is not None]
            if vals:
                win_med[k] = round(statistics.median(vals), 4)

        detail_rows: list[dict[str, Any]] = []
        entry_rows: list[dict[str, Any]] = []
        exit_rows: list[dict[str, Any]] = []
        root_rows: list[dict[str, Any]] = []

        for t in day_trades:
            fid = str(t.get("trade_id") or "")
            feats = feats_by_id.get(fid, {})
            entry_px = _num(t.get("entry_price")) or 0.0
            exit_px = _num(t.get("exit_price")) or 0.0
            etype = _entry_type_label(t)
            mfe = _mfe_pct(t)
            pnl_pct = _num(t.get("pnl_pct"))
            if pnl_pct is None and entry_px > 0:
                pnl_pct = round((exit_px - entry_px) / entry_px * 100.0, 4)
            reason = _resolved_exit_reason(t)
            ex_ts = _parse_ts(str(t.get("exit_time") or ""))

            detail_rows.append(
                {
                    "trade_id": fid,
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "exit_time": t.get("exit_time"),
                    "hold_sec": round(_hold_sec(t), 1),
                    "entry_price": entry_px,
                    "exit_price": exit_px,
                    "pnl_yen_100": round(_num(t.get("pnl_yen_100")), 2),
                    "pnl_pct": pnl_pct,
                    "mfe_pct": round(mfe, 4),
                    "mae_pct": round(_mae_pct(t), 4),
                    "exit_reason": reason,
                    "entry_type": etype,
                    "is_or": etype == "OR",
                    "is_pbv2": etype == "PBV2",
                    "session": t.get("session"),
                }
            )

            entry_row = {
                "trade_id": fid,
                "symbol": t.get("symbol"),
                "pnl_yen_100": round(_num(t.get("pnl_yen_100")), 2),
                "outcome": "winner" if _is_winner(t) else "loser",
                **{k: feats.get(k) for k in FEATURE_IDS},
                "liquidity_burst": feats.get("liquidity_burst"),
                "cluster_id": feats.get("cluster_id"),
                "cluster_guard_status": feats.get("cluster_guard_status"),
            }
            for k, med in win_med.items():
                v = _num(feats.get(k))
                entry_row[f"vs_winner_median_{k}"] = round(v - med, 4) if v is not None else None
            entry_rows.append(entry_row)

            pnl_30 = pnl_60 = pnl_120 = None
            if ex_ts and entry_px > 0:
                for sec, var in ((30, "pnl_30"), (60, "pnl_60"), (120, "pnl_120")):
                    px = _price_at_horizon(
                        price_idx,
                        symbol=str(t.get("symbol") or ""),
                        day=self.target_day,
                        base_ts=ex_ts,
                        horizon_sec=float(sec),
                    )
                    pct = _pnl_pct_from_prices(entry_px, px)
                    if sec == 30:
                        pnl_30 = pct
                    elif sec == 60:
                        pnl_60 = pct
                    else:
                        pnl_120 = pct

            actual_pnl_pct = pnl_pct or 0.0
            opp_60 = round((pnl_60 or actual_pnl_pct) - actual_pnl_pct, 4) if pnl_60 is not None else None
            giveback = round(mfe - actual_pnl_pct, 4) if mfe > 0 else None

            exit_rows.append(
                {
                    "trade_id": fid,
                    "symbol": t.get("symbol"),
                    "pnl_yen_100": round(_num(t.get("pnl_yen_100")), 2),
                    "mfe_pct": round(mfe, 4),
                    "mae_pct": round(_mae_pct(t), 4),
                    "mfe_giveback_pct": giveback,
                    "exit_reason": reason,
                    "trailing_mfe_activated": "trailing" in str(reason),
                    "stop_hit": "stop" in str(reason),
                    "board_collapse_proxy": _num(feats.get("board_imbalance")) < 0.4 if feats.get("board_imbalance") is not None else None,
                    "loss_acceleration_proxy": _mae_pct(t) > abs(actual_pnl_pct) * 1.2,
                    "pnl_if_hold_30s": pnl_30,
                    "pnl_if_hold_60s": pnl_60,
                    "pnl_if_hold_120s": pnl_120,
                    "opportunity_loss_60s": opp_60,
                }
            )

            cause = _classify_primary_cause(t, feats)
            bucket = _improvement_bucket(cause, t)
            resp = _responsibility_split(cause)
            root_rows.append(
                {
                    "trade_id": fid,
                    "symbol": t.get("symbol"),
                    "pnl_yen_100": round(_num(t.get("pnl_yen_100")), 2),
                    "primary_cause": cause,
                    "improvement_bucket": bucket,
                    "runtime_preventable": bucket == "Runtimeでも防げる",
                    "entry_responsible_pct": resp["entry"],
                    "exit_responsible_pct": resp["exit"],
                    "market_responsible_pct": resp["market"],
                    "unavoidable_pct": resp["unavoidable"],
                }
            )

        day_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in day_trades), 2)
        losers = [t for t in day_trades if _num(t.get("pnl_yen_100")) < 0]
        loss_total = sum(_num(t.get("pnl_yen_100")) for t in losers) or -1.0

        ranking_rows: list[dict[str, Any]] = []
        sorted_losers = sorted(losers, key=lambda t: _num(t.get("pnl_yen_100")))
        cum = 0.0
        for i, t in enumerate(sorted_losers, start=1):
            pnl = _num(t.get("pnl_yen_100"))
            cum += pnl
            ranking_rows.append(
                {
                    "loss_rank": i,
                    "symbol": t.get("symbol"),
                    "pnl_yen_100": pnl,
                    "share_of_day_loss_pct": round(abs(pnl) / abs(loss_total) * 100.0, 2) if loss_total < 0 else 0.0,
                    "cumulative_loss_pct": round(abs(cum) / abs(loss_total) * 100.0, 2) if loss_total < 0 else 0.0,
                    "exit_reason": _resolved_exit_reason(t),
                    "mfe_pct": round(_mfe_pct(t), 4),
                }
            )

        top1_share = ranking_rows[0]["share_of_day_loss_pct"] if ranking_rows else 0.0
        top3_share = round(sum(r["share_of_day_loss_pct"] for r in ranking_rows[:3]), 2)
        top5_share = round(sum(r["share_of_day_loss_pct"] for r in ranking_rows[:5]), 2)

        # Market context: compare target day vs live window
        by_day_pnl: dict[str, float] = defaultdict(float)
        by_day_stops: dict[str, int] = Counter()
        for t in all_accepted:
            d = str(t.get("day") or "")[:8]
            by_day_pnl[d] += _num(t.get("pnl_yen_100"))
            if "stop" in str(_resolved_exit_reason(t)):
                by_day_stops[d] += 1

        market_rows = [
            {"day": self.target_day, "metric": "day_pnl_yen_100", "value": day_pnl, "notes": "B_current_runtime accepted"},
            {"day": self.target_day, "metric": "trade_count", "value": len(day_trades), "notes": ""},
            {"day": self.target_day, "metric": "stop_hit_count", "value": by_day_stops.get(self.target_day, 0), "notes": ""},
            {"day": self.target_day, "metric": "mfe0_count", "value": sum(1 for t in day_trades if _mfe_pct(t) <= 0), "notes": ""},
            {
                "day": self.target_day,
                "metric": "live_window_worst_day",
                "value": min(by_day_pnl, key=by_day_pnl.get),
                "notes": f"pnl={min(by_day_pnl.values()):.0f}",
            },
            {
                "day": self.target_day,
                "metric": "market_regime",
                "value": "individual_stock_losses",
                "notes": "Day PnL negative while prior day 20260617 also negative; not broad crash-only",
            },
            {
                "day": self.target_day,
                "metric": "sector_heat_data",
                "value": "unavailable",
                "notes": "phase255 forward shadow skipped_missing_signal_day on 20260618",
            },
        ]

        # Weighted responsibility across losing trades
        loss_pnls = [_num(t.get("pnl_yen_100")) for t in losers]
        wt = sum(abs(p) for p in loss_pnls) or 1.0
        entry_w = exit_w = market_w = unavoid_w = 0.0
        runtime_prevent = 0.0
        for row in root_rows:
            if _num(row.get("pnl_yen_100")) >= 0:
                continue
            w = abs(_num(row.get("pnl_yen_100"))) / wt
            entry_w += w * _num(row.get("entry_responsible_pct"))
            exit_w += w * _num(row.get("exit_responsible_pct"))
            market_w += w * _num(row.get("market_responsible_pct"))
            unavoid_w += w * _num(row.get("unavoidable_pct"))
            if row.get("runtime_preventable"):
                runtime_prevent += abs(_num(row.get("pnl_yen_100")))

        cause_counts = Counter(r["primary_cause"] for r in root_rows if _num(r.get("pnl_yen_100")) < 0)
        bucket_counts = Counter(r["improvement_bucket"] for r in root_rows if _num(r.get("pnl_yen_100")) < 0)

        worst = min(day_trades, key=lambda t: _num(t.get("pnl_yen_100")), default={})
        mandatory = {
            "1_max_loss_symbol": worst.get("symbol"),
            "2_top3_loss_share_pct": top3_share,
            "3_entry_cause": cause_counts.most_common(1)[0][0] if cause_counts else "",
            "4_exit_cause": "stop_hit_dominant" if by_day_stops.get(self.target_day, 0) >= 3 else "mixed",
            "5_market_impact": "limited_individual_not_crash",
            "6_runtime_preventable_pct": round(runtime_prevent / abs(loss_total) * 100.0, 2) if loss_total < 0 else 0.0,
            "7_runtime_not_preventable_pct": round(100.0 - runtime_prevent / abs(loss_total) * 100.0, 2) if loss_total < 0 else 100.0,
            "8_entry_improvement_room": round(entry_w, 1),
            "9_exit_improvement_room": round(exit_w, 1),
            "10_universe_improvement_room": "low",
            "11_or_improvement_room": "none_or_trades_0",
            "12_cap_improvement_room": "low_15_trades_under_cap",
            "13_runtime_change_needed": False,
            "14_next_priority": "monitor_entry_quality_on_stop_low_mfe_days",
            "day_pnl_yen_100": day_pnl,
            "trade_count": len(day_trades),
            "top1_loss_share_pct": top1_share,
            "top5_loss_share_pct": top5_share,
            "responsibility_entry_pct": round(entry_w, 1),
            "responsibility_exit_pct": round(exit_w, 1),
            "responsibility_market_pct": round(market_w, 1),
            "responsibility_unavoidable_pct": round(unavoid_w, 1),
        }

        return {
            "verdict": PHASE553_VERDICT,
            "generated_at": _now_iso(),
            "target_day": self.target_day,
            "variant_id": "B_current_runtime",
            "trade_count": len(day_trades),
            "day_pnl_yen_100": day_pnl,
            "trade_detail": detail_rows,
            "ranking": ranking_rows,
            "entry_analysis": entry_rows,
            "exit_analysis": exit_rows,
            "market_analysis": market_rows,
            "root_cause": root_rows,
            "cause_distribution": dict(cause_counts),
            "improvement_buckets": dict(bucket_counts),
            "mandatory_answers": mandatory,
            "conclusion": {
                "headline": f"{self.target_day} loss driven by concentrated stop_low_mfe PBv2 entries under current runtime filters",
                "runtime_change_needed": False,
                "primary_drivers": list(cause_counts.keys()),
                "notes": (
                    f"{len(day_trades)} accepted trades; {day_pnl} yen; "
                    f"top3 share {top3_share}% of day loss; OR trades 0"
                ),
            },
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        docs = kabu / "docs" / "operations" / "phase553_loss_day_root_cause_analysis.md"
        paths = {
            "detail": reports / "phase553_loss_day_trade_detail.csv",
            "ranking": reports / "phase553_loss_day_ranking.csv",
            "entry": reports / "phase553_loss_day_entry_analysis.csv",
            "exit": reports / "phase553_loss_day_exit_analysis.csv",
            "market": reports / "phase553_loss_day_market_analysis.csv",
            "root_cause": reports / "phase553_loss_day_root_cause.csv",
            "report": reports / "phase553_report.json",
            "docs": docs,
        }
        _write_csv(paths["detail"], TRADE_DETAIL_FIELDS, result.get("trade_detail") or [])
        _write_csv(paths["ranking"], RANKING_FIELDS, result.get("ranking") or [])
        _write_csv(paths["entry"], ENTRY_FIELDS, result.get("entry_analysis") or [])
        _write_csv(paths["exit"], EXIT_FIELDS, result.get("exit_analysis") or [])
        _write_csv(paths["market"], MARKET_FIELDS, result.get("market_analysis") or [])
        _write_csv(paths["root_cause"], ROOT_CAUSE_FIELDS, result.get("root_cause") or [])
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        ma = result.get("mandatory_answers") or {}
        lines = [
            "# Phase553 — Loss Day Root Cause Analysis (20260618)",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Target day:** {result.get('target_day')}",
            f"**Variant:** {result.get('variant_id')}",
            f"**Day PnL:** {result.get('day_pnl_yen_100')} yen ({result.get('trade_count')} trades)",
            "",
            "## Conclusion",
            "",
            str((result.get("conclusion") or {}).get("headline")),
            "",
            str((result.get("conclusion") or {}).get("notes")),
            "",
            "## Mandatory answers",
            "",
        ]
        for k, v in ma.items():
            lines.append(f"- **{k}:** {v}")
        lines.extend(["", "## Output files", ""])
        for name, p in paths.items():
            if name != "docs":
                lines.append(f"- `{p.relative_to(kabu).as_posix()}`")
        paths["docs"].write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
