"""
Phase454 — Board:mid Loss Pattern Audit (research only).

Identify common features of Board:mid losses that survive Phase439 High Drift
and Phase452 Weak Shape Reject. Population: Momentum:low + Board:mid only.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase450_momentum_redesign_shadow import _passes_baseline_entry
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

JST = ZoneInfo("Asia/Tokyo")
TARGET_SYMBOLS = ("6976.T", "6920.T", "4062.T")

LOSS_RANK_FIELDS = [
    "rank",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen",
    "guard_class",
    "high_drift_would_block",
    "weak_shape_would_block",
    "eod_shape_class",
    "exit_reason",
]

AUDIT_ROW_FIELDS = LOSS_RANK_FIELDS + [
    "return_5min_pct",
    "return_10min_pct",
    "return_15min_pct",
    "return_30min_pct",
    "day_high_distance_pct",
    "minutes_since_day_high_update",
    "entry_order_book_imbalance",
    "entry_vwap_dev_pct",
    "entry_hour",
]

CANDIDATE_FIELDS = [
    "rank",
    "feature",
    "d_group_mean",
    "winner_mean",
    "delta",
    "abs_delta",
    "direction",
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


def _map_runtime_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(trade)
    for src, dst in (
        ("return_5min_pct", "entry_rise_5min_pct"),
        ("return_10min_pct", "entry_rise_10min_pct"),
        ("return_15min_pct", "entry_rise_15min_pct"),
        ("return_30min_pct", "entry_rise_30min_pct"),
    ):
        if out.get(dst) is None and out.get(src) is not None:
            out[dst] = out[src]
    return out


def _weak_shape_block(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def _high_drift_block(trade: Mapping[str, Any]) -> bool:
    return guard_high_drift(trade)


def _guard_class(hd: bool, ws: bool) -> str:
    if hd and ws:
        return "C"
    if hd:
        return "A"
    if ws:
        return "B"
    return "D"


def _board_mid_pool(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(t) for t in enriched if _passes_baseline_entry(t)]


def _rise(trade: Mapping[str, Any], mins: int) -> Optional[float]:
    return _float(trade.get(f"return_{mins}min_pct")) or _float(trade.get(f"entry_rise_{mins}min_pct"))


def _day_high_dist(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("day_high_distance_pct")) or _float(trade.get("entry_near_day_high_pct"))


def _entry_hour(trade: Mapping[str, Any]) -> Optional[float]:
    et = _parse_ts(str(trade.get("entry_time") or ""))
    if et is None:
        return None
    return float(et.astimezone(JST).hour) + et.astimezone(JST).minute / 60.0


def _is_stop(trade: Mapping[str, Any]) -> bool:
    return normalize_exit_reason(str(trade.get("exit_reason") or "")) == "stop_hit"


def _trade_row(trade: Mapping[str, Any], *, rank: Optional[int] = None) -> dict[str, Any]:
    hd = _high_drift_block(trade)
    ws = _weak_shape_block(trade)
    gc = _guard_class(hd, ws)
    row = {
        "rank": rank,
        "symbol": trade.get("symbol"),
        "entry_time": trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),
        "pnl_yen": _pnl_yen(trade),
        "guard_class": gc,
        "high_drift_would_block": hd,
        "weak_shape_would_block": ws,
        "eod_shape_class": trade.get("eod_shape_class"),
        "exit_reason": trade.get("exit_reason"),
        "return_5min_pct": _rise(trade, 5),
        "return_10min_pct": _rise(trade, 10),
        "return_15min_pct": _rise(trade, 15),
        "return_30min_pct": _rise(trade, 30),
        "day_high_distance_pct": _day_high_dist(trade),
        "minutes_since_day_high_update": _float(trade.get("minutes_since_day_high_update")),
        "entry_order_book_imbalance": _float(trade.get("entry_order_book_imbalance")),
        "entry_vwap_dev_pct": _float(trade.get("entry_vwap_dev_pct")),
        "entry_hour": _entry_hour(trade),
    }
    return row


def _group_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_pnl_yen(t) for t in trades]
    if not trades:
        return {"count": 0, "pnl_yen": 0.0, "profit_factor": None, "stop_rate": None, "win_rate": None}
    ordered = sorted(
        trades,
        key=lambda t: (
            _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(t.get("symbol") or ""),
        ),
    )
    chron = [_pnl_yen(t) for t in ordered]
    return {
        "count": len(trades),
        "pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "stop_rate": round(sum(1 for t in trades if _is_stop(t)) / len(trades), 4),
        "win_rate": _win_rate(pnls),
        "max_drawdown_yen": _max_drawdown_yen(chron),
    }


def _feature_means(trades: Sequence[Mapping[str, Any]]) -> dict[str, Optional[float]]:
    specs = {
        "r5": lambda t: _rise(t, 5),
        "r10": lambda t: _rise(t, 10),
        "r15": lambda t: _rise(t, 15),
        "r30": lambda t: _rise(t, 30),
        "day_high_distance": _day_high_dist,
        "high_update_age": lambda t: _float(t.get("minutes_since_day_high_update")),
        "entry_order_book_imbalance": lambda t: _float(t.get("entry_order_book_imbalance")),
        "vwap_dev": lambda t: _float(t.get("entry_vwap_dev_pct")),
        "entry_hour": _entry_hour,
    }
    out: dict[str, Optional[float]] = {}
    for name, fn in specs.items():
        vals = [v for t in trades if (v := fn(t)) is not None]
        out[name] = round(statistics.mean(vals), 4) if vals else None
    return out


def _top_feature_candidates(
    d_trades: Sequence[Mapping[str, Any]],
    winners: Sequence[Mapping[str, Any]],
    *,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    d_means = _feature_means(d_trades)
    w_means = _feature_means(winners)
    rows: list[dict[str, Any]] = []
    for feat, d_val in d_means.items():
        w_val = w_means.get(feat)
        if d_val is None or w_val is None:
            continue
        delta = round(d_val - w_val, 4)
        rows.append(
            {
                "feature": feat,
                "d_group_mean": d_val,
                "winner_mean": w_val,
                "delta": delta,
                "abs_delta": abs(delta),
                "direction": "higher_in_D" if delta > 0 else "lower_in_D",
            }
        )
    rows.sort(key=lambda r: float(r["abs_delta"]), reverse=True)
    for i, row in enumerate(rows[:top_n], start=1):
        row["rank"] = i
    return rows[:top_n]


def _symbol_classifications(
    pool: Sequence[Mapping[str, Any]], symbol: str
) -> list[dict[str, Any]]:
    return [_trade_row(t) for t in pool if str(t.get("symbol") or "") == symbol]


def _verdict(
    *,
    d_loss_count: int,
    d_loss_pnl: float,
    top_candidates: Sequence[Mapping[str, Any]],
    loss_count: int,
) -> str:
    if d_loss_count == 0 or abs(d_loss_pnl) < 5000:
        return "problem_solved"
    if top_candidates and float(top_candidates[0].get("abs_delta") or 0) > 0.3:
        return "remaining_pattern_found"
    if d_loss_count / max(loss_count, 1) < 0.15:
        return "problem_solved"
    return "random_noise"


def run_phase454_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)
    pool = _board_mid_pool(enriched)

    losers = [t for t in pool if _pnl_yen(t) < 0]
    winners = [t for t in pool if _pnl_yen(t) > 0]
    losers_sorted = sorted(losers, key=_pnl_yen)

    loss_top100: list[dict[str, Any]] = []
    for i, t in enumerate(losers_sorted[:100], start=1):
        loss_top100.append(_trade_row(t, rank=i))

    class_counts = Counter(_guard_class(_high_drift_block(t), _weak_shape_block(t)) for t in losers)
    all_class_counts = Counter(_guard_class(_high_drift_block(t), _weak_shape_block(t)) for t in pool)

    d_trades = [t for t in pool if _guard_class(_high_drift_block(t), _weak_shape_block(t)) == "D"]
    d_losers = [t for t in d_trades if _pnl_yen(t) < 0]
    d_metrics = _group_metrics(d_trades)
    d_loss_metrics = _group_metrics(d_losers)

    sym_pnl_d = Counter()
    for t in d_trades:
        sym_pnl_d[str(t.get("symbol") or "")] += _pnl_yen(t)
    top20_symbols = [
        {"symbol": sym, "pnl_yen": round(pnl, 2), "count": sum(1 for t in d_trades if t.get("symbol") == sym)}
        for sym, pnl in sym_pnl_d.most_common()
    ][:20]
    top20_symbols.sort(key=lambda x: x["pnl_yen"])

    d_features = _feature_means(d_trades)
    w_features = _feature_means(winners)
    feature_compare = {
        feat: {"d_group": d_features.get(feat), "winners": w_features.get(feat)}
        for feat in d_features
    }

    target_deep: dict[str, Any] = {}
    for sym in TARGET_SYMBOLS:
        rows = _symbol_classifications(pool, sym)
        by_class = Counter(r["guard_class"] for r in rows)
        target_deep[sym] = {
            "trade_count": len(rows),
            "total_pnl_yen": round(sum(r["pnl_yen"] for r in rows), 2),
            "guard_class_counts": dict(by_class),
            "primary_class_among_losses": (
                Counter(r["guard_class"] for r in rows if r["pnl_yen"] < 0).most_common(1)[0][0]
                if any(r["pnl_yen"] < 0 for r in rows)
                else None
            ),
            "trades": rows,
        }

    candidates_rules = _top_feature_candidates(d_trades, winners, top_n=10)

    d_loss_pnl = round(sum(_pnl_yen(t) for t in d_losers), 2)
    expected_improvement = round(abs(d_loss_pnl), 2)

    guard_candidates: list[str] = []
    for c in candidates_rules[:5]:
        feat = str(c.get("feature") or "")
        direction = str(c.get("direction") or "")
        if feat == "r15" and direction == "lower_in_D":
            guard_candidates.append("mid_board_r15_floor — reject Board:mid when r15 below D-group mean (~{:.2f})".format(c.get("d_group_mean")))
        elif feat == "day_high_distance" and direction == "lower_in_D":
            guard_candidates.append("near_day_high_mid_board — tighten distance threshold for Board:mid")
        elif feat == "high_update_age" and direction == "higher_in_D":
            guard_candidates.append("stale_day_high_mid — reject when minutes_since_day_high_update elevated")
        elif feat == "vwap_dev" and direction == "lower_in_D":
            guard_candidates.append("below_vwap_mid_board — reject Board:mid deep below VWAP")
        elif feat == "entry_hour" and direction == "higher_in_D":
            guard_candidates.append("pm_board_mid_filter — afternoon Board:mid entries underperform")
    if not guard_candidates:
        guard_candidates.append("no strong single-feature guard — D losses may be idiosyncratic")

    unresolved = len(d_losers) > 10 and abs(d_loss_pnl) > 10000
    verdict = _verdict(
        d_loss_count=len(d_losers),
        d_loss_pnl=d_loss_pnl,
        top_candidates=candidates_rules,
        loss_count=len(losers),
    )

    mandatory = {
        "1_board_mid_loss_count": len(losers),
        "2_class_A_high_drift_only": class_counts.get("A", 0),
        "3_class_B_weak_shape_only": class_counts.get("B", 0),
        "4_class_C_both": class_counts.get("C", 0),
        "5_class_D_neither": class_counts.get("D", 0),
        "6_d_group_pnl_yen": d_metrics["pnl_yen"],
        "7_symbol_6920": {
            "classification": target_deep["6920.T"]["guard_class_counts"],
            "primary_loss_class": target_deep["6920.T"]["primary_class_among_losses"],
            "total_pnl": target_deep["6920.T"]["total_pnl_yen"],
        },
        "8_symbol_6976": {
            "classification": target_deep["6976.T"]["guard_class_counts"],
            "primary_loss_class": target_deep["6976.T"]["primary_class_among_losses"],
            "total_pnl": target_deep["6976.T"]["total_pnl_yen"],
        },
        "9_symbol_4062": {
            "classification": target_deep["4062.T"]["guard_class_counts"],
            "primary_loss_class": target_deep["4062.T"]["primary_class_among_losses"],
            "total_pnl": target_deep["4062.T"]["total_pnl_yen"],
        },
        "10_unresolved_pattern_exists": unresolved,
        "11_next_guard_candidates": guard_candidates,
        "12_expected_improvement_yen": expected_improvement,
        "verdict": verdict,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "population": "Momentum:low + Board:mid (Board:high excluded)",
        "pool_count": len(pool),
        "loss_count": len(losers),
        "win_count": len(winners),
        "guard_class_on_losses": dict(class_counts),
        "guard_class_on_all": dict(all_class_counts),
        "part_a_loss_top100": loss_top100,
        "part_c_d_group": {**d_metrics, "loss_subset": d_loss_metrics, "top20_symbols_by_pnl": top20_symbols},
        "part_d_feature_compare": feature_compare,
        "part_e_target_symbols": target_deep,
        "part_f_candidates": candidates_rules,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "_audit_rows": loss_top100,
        "_candidate_rows": candidates_rules,
    }


@dataclass
class Phase454Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase454_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "audit": reports / "phase454_board_mid_loss_audit.csv",
            "candidates": reports / "phase454_unsolved_pattern_candidates.csv",
            "summary": reports / "phase454_board_mid_loss_summary.json",
        }
        _write_csv(paths["audit"], LOSS_RANK_FIELDS, list(result.get("_audit_rows") or []))
        _write_csv(paths["candidates"], CANDIDATE_FIELDS, list(result.get("_candidate_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase454_board_mid_loss_audit.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        m = result.get("mandatory_answers") or {}
        pc = result.get("part_c_d_group") or {}
        fc = result.get("part_f_candidates") or []
        report.write_text(
            "\n".join(
                [
                    "# Phase454 — Board:mid Loss Pattern Audit",
                    "",
                    f"Generated: {result.get('generated_at')}",
                    f"Period: {result.get('period_start')}..{result.get('period_end')}",
                    f"Population: {result.get('population')} (n={result.get('pool_count')})",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Board:mid loss count: **{m.get('1_board_mid_loss_count')}**",
                    f"2. Class A (HD only): **{m.get('2_class_A_high_drift_only')}**",
                    f"3. Class B (WS only): **{m.get('3_class_B_weak_shape_only')}**",
                    f"4. Class C (both): **{m.get('4_class_C_both')}**",
                    f"5. Class D (neither): **{m.get('5_class_D_neither')}**",
                    f"6. D-group PnL: **{m.get('6_d_group_pnl_yen')}** yen",
                    f"7. 6920: **{m.get('7_symbol_6920')}**",
                    f"8. 6976: **{m.get('8_symbol_6976')}**",
                    f"9. 4062: **{m.get('9_symbol_4062')}**",
                    f"10. Unresolved pattern exists: **{m.get('10_unresolved_pattern_exists')}**",
                    f"11. Next guard candidates: {m.get('11_next_guard_candidates')}",
                    f"12. Expected improvement: **{m.get('12_expected_improvement_yen')}** yen (upper bound = |D losses|)",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## Part C — D group",
                    "",
                    f"- Count: {pc.get('count')} | PnL: {pc.get('pnl_yen')} | PF: {pc.get('profit_factor')} | Stop: {pc.get('stop_rate')}",
                    "",
                    "## Part F — Top feature gaps (D vs winners)",
                    "",
                    "| Rank | Feature | D mean | Win mean | Delta |",
                    "|------|---------|--------|----------|-------|",
                    *[
                        f"| {r.get('rank')} | {r.get('feature')} | {r.get('d_group_mean')} | {r.get('winner_mean')} | {r.get('delta')} |"
                        for r in fc
                    ],
                    "",
                    "Outputs: phase454_board_mid_loss_audit.csv, phase454_unsolved_pattern_candidates.csv, phase454_board_mid_loss_summary.json",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        paths["report"] = report
        return paths
