"""
Phase453 — Board Signal Quality Audit (research only).

Why Board:high PF > Board:mid? Eval pool:
Momentum:low + (Board:mid OR Board:high) + NOT High Drift, 20260529–20260619.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import (
    _chronological_pnls_from_log,
    _stop_rate_from_log,
    simulate_capacity_replay,
)
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
)
from research.phase451b_entry_shape_tournament_mid_high import (
    _board_bucket,
    _board_token,
    _passes_baseline_mid_high,
)
from research.phase400_holding_time_audit import normalize_exit_reason
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

SHAPE_CLASSES = ("uptrend", "opening_peak", "slow_opening_peak", "downtrend", "range", "other", "unknown")

IMBALANCE_BINS: tuple[tuple[str, float, Optional[float]], ...] = (
    ("0.53_0.56", 0.53, 0.56),
    ("0.56_0.60", 0.56, 0.60),
    ("0.60_0.65", 0.60, 0.65),
    ("0.65_plus", 0.65, None),
)

QUALITY_FIELDS = [
    "section",
    "bucket",
    "shape",
    "imbalance_bin",
    "count",
    "pnl_yen",
    "profit_factor",
    "win_rate",
    "stop_rate",
    "max_drawdown_yen",
    "avg_pnl_yen",
    "uptrend_rate",
    "correlation_imbalance_pnl",
    "correlation_imbalance_stop",
    "correlation_imbalance_uptrend",
]

TRADE_DETAIL_FIELDS = [
    "board_bucket",
    "symbol",
    "day",
    "entry_time",
    "entry_order_book_imbalance",
    "eod_shape_class",
    "pnl_yen",
    "exit_reason",
    "return_5min_pct",
    "return_10min_pct",
    "return_15min_pct",
    "return_30min_pct",
    "day_high_distance_pct",
    "entry_vwap_dev_pct",
    "entry_near_day_high_pct",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _pnl_yen(trade: Mapping[str, Any]) -> float:
    raw = trade.get("pnl_yen")
    if raw not in (None, ""):
        return float(raw)
    f100 = _float(trade.get("pnl_yen_100_float"))
    if f100 is not None:
        return round(f100, 2)
    y100 = _float(trade.get("pnl_yen_100"))
    if y100 is not None:
        return round(y100, 2)
    return 0.0


def _is_stop(trade: Mapping[str, Any]) -> bool:
    return normalize_exit_reason(str(trade.get("exit_reason") or "")) == "stop_hit"


def _eval_pool(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(t)
        for t in enriched
        if _passes_baseline_mid_high(t) and not guard_high_drift(t)
    ]


def _rise(trade: Mapping[str, Any], mins: int) -> Optional[float]:
    key = f"return_{mins}min_pct"
    alt = f"entry_rise_{mins}min_pct"
    return _float(trade.get(key)) or _float(trade.get(alt))


def _day_high_dist(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("day_high_distance_pct")) or _float(trade.get("entry_near_day_high_pct"))


def _entry_hour(trade: Mapping[str, Any]) -> Optional[str]:
    et = _parse_ts(str(trade.get("entry_time") or ""))
    if et is None:
        return None
    return et.astimezone(JST).strftime("%H:%M")


def _trade_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_pnl_yen(t) for t in trades]
    if not trades:
        return {
            "count": 0,
            "pnl_yen": 0.0,
            "profit_factor": None,
            "win_rate": None,
            "stop_rate": None,
            "max_drawdown_yen": 0.0,
            "avg_pnl_yen": 0.0,
            "uptrend_rate": None,
        }
    ordered = sorted(
        trades,
        key=lambda t: (
            _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(t.get("symbol") or ""),
        ),
    )
    chron_pnls = [_pnl_yen(t) for t in ordered]
    uptrend_n = sum(1 for t in trades if str(t.get("eod_shape_class") or "") == "uptrend")
    return {
        "count": len(trades),
        "pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": _win_rate(pnls),
        "stop_rate": round(sum(1 for t in trades if _is_stop(t)) / len(trades), 4),
        "max_drawdown_yen": _max_drawdown_yen(chron_pnls),
        "avg_pnl_yen": round(statistics.mean(pnls), 2),
        "uptrend_rate": round(uptrend_n / len(trades), 4),
    }


def _replay_metrics(
    trades: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    if not trades:
        return _trade_metrics([])
    state = simulate_capacity_replay(
        list(trades),
        np_shadows,
        mode=label,
        entry_block_fn=lambda _t: False,
        baseline_accepted_keys=set(),
    )
    chron = _chronological_pnls_from_log(state.trade_log)
    return {
        "count": len(trades),
        "candidate_count": len(trades),
        "accepted_count": state.accepted_trade_count,
        "pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "win_rate": _win_rate(chron),
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "avg_pnl_yen": round(statistics.mean(chron), 2) if chron else 0.0,
        "uptrend_rate": None,
    }


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x <= 0 or den_y <= 0:
        return None
    return round(num / (den_x * den_y), 4)


def _correlations(trades: Sequence[Mapping[str, Any]]) -> dict[str, Optional[float]]:
    pairs: list[tuple[float, float, float, float]] = []
    for t in trades:
        imb = _float(t.get("entry_order_book_imbalance"))
        if imb is None:
            continue
        pairs.append(
            (
                imb,
                _pnl_yen(t),
                1.0 if _is_stop(t) else 0.0,
                1.0 if str(t.get("eod_shape_class") or "") == "uptrend" else 0.0,
            )
        )
    if len(pairs) < 3:
        return {
            "correlation_imbalance_pnl": None,
            "correlation_imbalance_stop": None,
            "correlation_imbalance_uptrend": None,
        }
    imbs = [p[0] for p in pairs]
    return {
        "correlation_imbalance_pnl": _pearson(imbs, [p[1] for p in pairs]),
        "correlation_imbalance_stop": _pearson(imbs, [p[2] for p in pairs]),
        "correlation_imbalance_uptrend": _pearson(imbs, [p[3] for p in pairs]),
    }


def _imbalance_bin(val: Optional[float]) -> Optional[str]:
    if val is None:
        return None
    for label, lo, hi in IMBALANCE_BINS:
        if val >= lo and (hi is None or val < hi):
            return label
    return None


def _feature_summary(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def _mean(key_fn) -> Optional[float]:
        vals = [v for t in trades if (v := key_fn(t)) is not None]
        return round(statistics.mean(vals), 4) if vals else None

    hours: dict[str, int] = defaultdict(int)
    shapes: dict[str, int] = defaultdict(int)
    for t in trades:
        h = _entry_hour(t)
        if h:
            hours[h[:2] + "h"] += 1
        shapes[str(t.get("eod_shape_class") or "unknown")] += 1
    top_hour = max(hours, key=hours.get) if hours else None
    top_shape = max(shapes, key=shapes.get) if shapes else None
    return {
        "mean_r5": _mean(lambda t: _rise(t, 5)),
        "mean_r10": _mean(lambda t: _rise(t, 10)),
        "mean_r15": _mean(lambda t: _rise(t, 15)),
        "mean_r30": _mean(lambda t: _rise(t, 30)),
        "mean_day_high_distance_pct": _mean(_day_high_dist),
        "mean_entry_vwap_dev_pct": _mean(lambda t: _float(t.get("entry_vwap_dev_pct"))),
        "dominant_entry_hour_block": top_hour,
        "dominant_eod_shape": top_shape,
        "shape_distribution": dict(shapes),
    }


def _top_n(trades: Sequence[Mapping[str, Any]], *, n: int, winners: bool) -> list[dict[str, Any]]:
    ranked = sorted(trades, key=_pnl_yen, reverse=winners)
    if not winners:
        ranked = sorted(trades, key=_pnl_yen)
    out: list[dict[str, Any]] = []
    for t in ranked[:n]:
        out.append(
            {
                "board_bucket": _board_bucket(t),
                "symbol": t.get("symbol"),
                "day": t.get("day"),
                "entry_time": t.get("entry_time"),
                "entry_order_book_imbalance": t.get("entry_order_book_imbalance"),
                "eod_shape_class": t.get("eod_shape_class"),
                "pnl_yen": _pnl_yen(t),
                "exit_reason": t.get("exit_reason"),
                "return_5min_pct": _rise(t, 5),
                "return_10min_pct": _rise(t, 10),
                "return_15min_pct": _rise(t, 15),
                "return_30min_pct": _rise(t, 30),
                "day_high_distance_pct": _day_high_dist(t),
                "entry_vwap_dev_pct": t.get("entry_vwap_dev_pct"),
                "entry_near_day_high_pct": t.get("entry_near_day_high_pct"),
            }
        )
    return out


def _symbol_bucket_summary(pool: Sequence[Mapping[str, Any]], symbol: str) -> dict[str, Any]:
    sym_trades = [t for t in pool if str(t.get("symbol") or "") == symbol]
    if not sym_trades:
        return {"symbol": symbol, "in_pool": False, "count": 0}
    buckets = defaultdict(list)
    for t in sym_trades:
        buckets[_board_bucket(t)].append(t)
    return {
        "symbol": symbol,
        "in_pool": True,
        "count": len(sym_trades),
        "primary_bucket": max(buckets, key=lambda k: len(buckets[k])),
        "bucket_counts": {k: len(v) for k, v in buckets.items()},
        "bucket_pnl": {k: round(sum(_pnl_yen(t) for t in v), 2) for k, v in buckets.items()},
        "total_pnl_yen": round(sum(_pnl_yen(t) for t in sym_trades), 2),
    }


def _monotonic_pf(bins: Sequence[Mapping[str, Any]]) -> bool:
    pfs = [b.get("profit_factor") for b in bins if b.get("count", 0) > 0 and b.get("profit_factor") is not None]
    if len(pfs) < 2:
        return False
    return all(pfs[i] <= pfs[i + 1] for i in range(len(pfs) - 1))


def _verdict(
    *,
    mid_pf: Optional[float],
    high_pf: Optional[float],
    high_n: int,
    monotonic: bool,
    corr_pnl: Optional[float],
) -> str:
    if high_pf is None or mid_pf is None or high_n < 10:
        if high_pf is not None and mid_pf is not None and high_pf > mid_pf and high_n >= 5:
            return "board_signal_partial"
        return "board_signal_partial"
    pf_edge = high_pf - mid_pf
    if high_pf > mid_pf and pf_edge >= 0.05 and (monotonic or (corr_pnl is not None and corr_pnl > 0.05)):
        return "board_signal_real"
    if high_pf <= mid_pf or pf_edge < 0.02:
        return "board_signal_noise"
    return "board_signal_partial"


def run_phase453_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)
    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)
    pool = _eval_pool(enriched)

    mid_pool = [t for t in pool if _board_bucket(t) == "mid"]
    high_pool = [t for t in pool if _board_bucket(t) == "high"]

    part_a_mid_trade = _trade_metrics(mid_pool)
    part_a_high_trade = _trade_metrics(high_pool)
    part_a_mid_replay = _replay_metrics(mid_pool, np_shadows, label="board_mid_cap5")
    part_a_high_replay = _replay_metrics(high_pool, np_shadows, label="board_high_cap5")

    bucket_rows: list[dict[str, Any]] = []
    for bucket, trade_m, replay_m in (
        ("Board:mid", part_a_mid_trade, part_a_mid_replay),
        ("Board:high", part_a_high_trade, part_a_high_replay),
    ):
        bucket_rows.append(
            {
                "section": "PartA_trade_level",
                "bucket": bucket,
                **trade_m,
            }
        )
        bucket_rows.append(
            {
                "section": "PartA_cap5_replay",
                "bucket": bucket,
                **replay_m,
            }
        )

    shape_rows: list[dict[str, Any]] = []
    for bucket_label, subset in (("Board:mid", mid_pool), ("Board:high", high_pool)):
        by_shape: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in subset:
            shape = str(t.get("eod_shape_class") or "unknown")
            if shape not in SHAPE_CLASSES:
                shape = "other"
            by_shape[shape].append(t)
        for shape in SHAPE_CLASSES:
            grp = by_shape.get(shape, [])
            m = _trade_metrics(grp)
            shape_rows.append({"section": "PartB", "bucket": bucket_label, "shape": shape, **m})

    imb_rows: list[dict[str, Any]] = []
    for label, lo, hi in IMBALANCE_BINS:
        grp = [
            t
            for t in pool
            if (v := _float(t.get("entry_order_book_imbalance"))) is not None
            and v >= lo
            and (hi is None or v < hi)
        ]
        m = _trade_metrics(grp)
        imb_rows.append({"section": "PartE", "bucket": "all", "imbalance_bin": label, **m})

    corrs = _correlations(pool)

    high_win_top = _top_n(high_pool, n=20, winners=True)
    high_loss_top = _top_n(high_pool, n=20, winners=False)
    mid_win_top = _top_n(mid_pool, n=20, winners=True)
    mid_loss_top = _top_n(mid_pool, n=20, winners=False)

    high_win_feat = _feature_summary(sorted(high_pool, key=_pnl_yen, reverse=True)[: min(20, len(high_pool))])
    high_loss_feat = _feature_summary(sorted(high_pool, key=_pnl_yen)[: min(20, len(high_pool))])
    mid_win_feat = _feature_summary(sorted(mid_pool, key=_pnl_yen, reverse=True)[:20])
    mid_loss_feat = _feature_summary(sorted(mid_pool, key=_pnl_yen)[:20])

    sym6976 = _symbol_bucket_summary(pool, "6976.T")
    sym6920 = _symbol_bucket_summary(pool, "6920.T")

    monotonic = _monotonic_pf(imb_rows)
    mid_pf = part_a_mid_replay.get("profit_factor")
    high_pf = part_a_high_replay.get("profit_factor")
    verdict = _verdict(
        mid_pf=mid_pf if isinstance(mid_pf, (int, float)) else None,
        high_pf=high_pf if isinstance(high_pf, (int, float)) else None,
        high_n=len(high_pool),
        monotonic=monotonic,
        corr_pnl=corrs.get("correlation_imbalance_pnl"),
    )

    high_real = high_pf is not None and mid_pf is not None and float(high_pf) > float(mid_pf)
    runtime_candidates = []
    if high_real and len(high_pool) < 50:
        runtime_candidates.append("board_high_min_imbalance_gate (0.56+) — small-N PF edge may be real but needs live confirmation")
    if not monotonic:
        runtime_candidates.append("board_strength_tiered_entry — non-monotonic expectancy; use shape+imbalance combo not bucket alone")
    if corrs.get("correlation_imbalance_uptrend") and (corrs["correlation_imbalance_uptrend"] or 0) > 0.1:
        runtime_candidates.append("board_high + EOD-uptrend proxy (r15/r30 pass) — imbalance aligns with continuation")
    runtime_candidates.append("weak_shape_reject tuning — board signal strongest in uptrend; keep shape guard (Phase452)")
    if sym6976.get("primary_bucket") == "high":
        runtime_candidates.append("6976 board-high cohort review — symbol skew may inflate high-bucket PF")

    mandatory = {
        "1_board_mid_pf": mid_pf,
        "2_board_high_pf": high_pf,
        "3_board_high_superior_real": high_real and verdict != "board_signal_noise",
        "4_board_strength_monotonic": monotonic,
        "5_board_high_main_win_pattern": high_win_feat,
        "6_board_high_main_loss_pattern": high_loss_feat,
        "7_symbol_6976_bucket": sym6976,
        "8_symbol_6920_bucket": sym6920,
        "9_board_as_entry_center_ok": verdict in ("board_signal_real", "board_signal_partial"),
        "10_next_runtime_candidates": runtime_candidates,
        "verdict": verdict,
    }

    quality_rows = bucket_rows + shape_rows + imb_rows + [
        {
            "section": "PartF_correlation",
            "bucket": "all",
            "count": len(pool),
            **corrs,
        }
    ]

    detail_rows = high_win_top + high_loss_top + mid_win_top + mid_loss_top

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "eval_pool_count": len(pool),
        "eval_pool_definition": "Momentum:low + (Board:mid OR Board:high) + NOT high_drift",
        "part_a": {
            "trade_level": {"Board:mid": part_a_mid_trade, "Board:high": part_a_high_trade},
            "cap5_replay": {"Board:mid": part_a_mid_replay, "Board:high": part_a_high_replay},
        },
        "part_b": shape_rows,
        "part_c": {
            "board_high_win_top20_features": high_win_feat,
            "board_mid_win_top20_features": mid_win_feat,
        },
        "part_d": {
            "board_high_loss_top20_features": high_loss_feat,
            "board_mid_loss_top20_features": mid_loss_feat,
        },
        "part_e": imb_rows,
        "part_f": corrs,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "_quality_rows": quality_rows,
        "_detail_rows": detail_rows,
        "_top_lists": {
            "board_high_win_top20": high_win_top,
            "board_high_loss_top20": high_loss_top,
            "board_mid_win_top20": mid_win_top,
            "board_mid_loss_top20": mid_loss_top,
        },
    }


@dataclass
class Phase453Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase453_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "quality": reports / "phase453_board_signal_quality.csv",
            "bucket": reports / "phase453_board_bucket_analysis.csv",
            "summary": reports / "phase453_board_summary.json",
        }
        _write_csv(paths["quality"], QUALITY_FIELDS, list(result.get("_quality_rows") or []))
        _write_csv(paths["bucket"], TRADE_DETAIL_FIELDS, list(result.get("_detail_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report_path = doc_root / "docs" / "operations" / "phase453_board_signal_quality_audit.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        m = result.get("mandatory_answers") or {}
        pa = result.get("part_a") or {}
        cap5 = pa.get("cap5_replay") or {}
        mid = cap5.get("Board:mid") or {}
        high = cap5.get("Board:high") or {}
        hw = (result.get("part_c") or {}).get("board_high_win_top20_features") or {}
        hl = (result.get("part_d") or {}).get("board_high_loss_top20_features") or {}
        report_path.write_text(
            "\n".join(
                [
                    "# Phase453 — Board Signal Quality Audit",
                    "",
                    f"Generated: {result.get('generated_at')}",
                    f"Period: {result.get('period_start')}..{result.get('period_end')}",
                    f"Eval pool: {result.get('eval_pool_definition')} (n={result.get('eval_pool_count')})",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Board:mid PF (CAP5): **{m.get('1_board_mid_pf')}**",
                    f"2. Board:high PF (CAP5): **{m.get('2_board_high_pf')}**",
                    f"3. Board:high superiority real: **{m.get('3_board_high_superior_real')}**",
                    f"4. Board strength monotonic: **{m.get('4_board_strength_monotonic')}**",
                    f"5. Board:high win pattern: `{hw.get('dominant_eod_shape')}` / r15={hw.get('mean_r15')} / dist={hw.get('mean_day_high_distance_pct')}",
                    f"6. Board:high loss pattern: `{hl.get('dominant_eod_shape')}` / r15={hl.get('mean_r15')}",
                    f"7. 6976 bucket: **{m.get('7_symbol_6976_bucket')}**",
                    f"8. 6920 bucket: **{m.get('8_symbol_6920_bucket')}**",
                    f"9. Board as ENTRY center OK: **{m.get('9_board_as_entry_center_ok')}**",
                    f"10. Runtime candidates: {m.get('10_next_runtime_candidates')}",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## Part A — CAP5 replay",
                    "",
                    "| Bucket | Candidates | Accepted | PnL | PF | WinRate | StopRate | MaxDD |",
                    "|--------|------------|----------|-----|-----|---------|----------|-------|",
                    f"| Board:mid | {mid.get('candidate_count')} | {mid.get('accepted_count')} | {mid.get('pnl_yen')} | {mid.get('profit_factor')} | {mid.get('win_rate')} | {mid.get('stop_rate')} | {mid.get('max_drawdown_yen')} |",
                    f"| Board:high | {high.get('candidate_count')} | {high.get('accepted_count')} | {high.get('pnl_yen')} | {high.get('profit_factor')} | {high.get('win_rate')} | {high.get('stop_rate')} | {high.get('max_drawdown_yen')} |",
                    "",
                    "## Part F — Correlations (imbalance vs …)",
                    "",
                    f"{json.dumps(result.get('part_f') or {}, ensure_ascii=False)}",
                    "",
                    "Outputs: phase453_board_signal_quality.csv, phase453_board_bucket_analysis.csv, phase453_board_summary.json",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        paths["report"] = report_path
        return paths
