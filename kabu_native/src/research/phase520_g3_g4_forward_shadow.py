"""
Phase520 — G3_G4 forward shadow (research replay + live shadow types).

G3_G4 overlay shadow:
  day_high + updates<=8 + rolling_volume_percentile>=80 + spread<=phase519 median
  Exit: PBv2 Exit (precomputed replay). Shadow only — no adoption.

Forward evaluation period: 20260616+ (out-of-sample vs Phase519 calibration).
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase488_current_runtime_replay import _filter_period
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    _day_rows,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976, _dependency_metrics
from research.phase515c_day_high_breakout_refinement import _timing_ratios
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _prepare_runtime_env,
    _scan_overlay_day,
)
from research.phase518_day_high_winner_loser_separation import (
    _build_micro_lookup,
    _extract_entry_features,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE520_VERDICT = "phase520_g3_g4_forward_shadow_done"
STRATEGY_ID = "G3_G4_SHADOW"
FORWARD_PERIOD_START = "20260616"
MIN_TRADING_DAYS = 5
MIN_OVERLAY_TRADES = 50
SPREAD_MEDIAN_PHASE519 = 63.78

REF_OR_LATE_BREAKOUT = 0.3372
REF_OR_HIGH_CHASE = 0.1279
REF_OR_TOP10_SHARE = 50.45
REF_OR_6976_SHARE = 71.92

TRADE_FIELDS = [
    "strategy_id",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "exit_reason",
    "rolling_volume_percentile",
    "spread",
    "update_count_before_entry",
    "minutes_from_open",
]

DAILY_FIELDS = [
    "day",
    "strategy_id",
    "total_pnl_yen_100",
    "profit_factor",
    "trade_count",
    "win_rate",
    "avg_pnl_yen_100",
    "max_drawdown_yen_100",
    "baseline_pnl_yen_100",
    "beats_baseline",
    "top1_symbol_profit_share_pct",
    "top3_symbol_profit_share_pct",
    "top1_day_profit_share_pct",
    "top10_trade_profit_share_pct",
]


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _passes_g3_g4(feats: Mapping[str, Any]) -> bool:
    vol = feats.get("rolling_volume_percentile")
    spread = feats.get("spread")
    if vol is None or _float(vol) < 80.0:
        return False
    if spread is None or _float(spread) > SPREAD_MEDIAN_PHASE519:
        return False
    return True


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    from datetime import datetime

    from research.phase451_entry_shape_tournament import JST
    from research.phase382_capital_constrained_backtest import _parse_ts

    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("exit_time") or t.get("entry_time") or ""))
        or datetime.min.replace(tzinfo=JST),
    )
    return [_float(t.get("pnl_yen_100")) for t in ordered]


def _metrics_from_trades(trades: Sequence[Mapping[str, Any]], *, strategy_id: str) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    chron = _chron_pnls(trades)
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        daily[str(t.get("day") or "")[:8]] += _float(t.get("pnl_yen_100"))
    pos_days = sum(1 for v in daily.values() if v > 0)
    neg_days = sum(1 for v in daily.values() if v < 0)
    total = round(sum(pnls), 2)
    return {
        "strategy_id": strategy_id,
        "total_pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(chron) if chron else 0.0, 2),
        "trades": len(pnls),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
        "avg_pnl_yen_100": round(total / len(pnls), 2) if pnls else 0.0,
        "positive_day_count": pos_days,
        "negative_day_count": neg_days,
        "daily_stability_score": round(pos_days / max(1, pos_days + neg_days), 4),
    }


def _dependency_row(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dep = _dependency_metrics(trades)
    total = _float(dep.get("total_pnl_yen_100"))
    sym6976 = round(
        sum(_float(t.get("pnl_yen_100")) for t in trades if str(t.get("symbol") or "") == SYMBOL_6976) / total * 100.0,
        2,
    ) if total else 0.0
    return {
        "top1_symbol_profit_share_pct": dep.get("top1_symbol_profit_share_pct"),
        "top3_symbol_profit_share_pct": dep.get("top3_symbol_profit_share_pct"),
        "top1_day_profit_share_pct": dep.get("top1_day_profit_share_pct"),
        "top3_day_profit_share_pct": dep.get("top3_day_profit_share_pct"),
        "top10_trade_profit_share_pct": dep.get("top10_trade_profit_share_pct"),
        "symbol_6976_share_pct": sym6976,
        "fragile_flag": bool(
            dep.get("single_symbol_dependency")
            or dep.get("single_day_dependency")
            or dep.get("trade_concentration_dependency")
        ),
    }


def _overlay_quality(trades: Sequence[Mapping[str, Any]], bar_cache: Mapping[tuple[str, tuple], Any]) -> dict[str, Any]:
    if not trades:
        return {
            "overlay_only_trades": 0,
            "overlay_only_pnl": 0.0,
            "overlay_only_pf": 0.0,
            "true_breakout_ratio": 0.0,
            "late_breakout_ratio": 0.0,
            "high_chase_ratio": 0.0,
            "high_update_continues_after_entry_ratio": 0.0,
            "avg_mfe": 0.0,
            "avg_mae": 0.0,
            "avg_mfe_mae_ratio": 0.0,
        }
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    timing = _timing_ratios(trades, bar_cache)
    from research.phase515b_day_high_breakout_dependency_audit import _bar_index_at, _high_update_stats
    from research.phase382_capital_constrained_backtest import _parse_ts

    mfes: list[float] = []
    maes: list[float] = []
    for t in trades:
        sym = str(t.get("symbol") or "").replace(".T", "")
        sym_t = f"{sym}.T"
        day = str(t.get("day") or "")[:8]
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or ""))
        cached = bar_cache.get((sym_t, day))
        if not cached or ent is None:
            continue
        bars, _ = cached
        ei = _bar_index_at(bars, ent)
        xi = _bar_index_at(bars, ex) if ex else ei
        if ei is None:
            continue
        stats = _high_update_stats(bars, ei, xi or ei)
        mfes.append(_float(stats.get("mfe_pct")))
        maes.append(_float(stats.get("mae_pct")))
    avg_mfe = round(statistics.mean(mfes), 4) if mfes else 0.0
    avg_mae = round(statistics.mean(maes), 4) if maes else 0.0
    ratio = round(avg_mfe / abs(avg_mae), 4) if avg_mae < -1e-9 else 0.0
    return {
        "overlay_only_trades": len(trades),
        "overlay_only_pnl": round(sum(pnls), 2),
        "overlay_only_pf": _pf(pnls),
        "true_breakout_ratio": timing.get("true_breakout_ratio", 0.0),
        "late_breakout_ratio": timing.get("late_breakout_ratio", 0.0),
        "high_chase_ratio": timing.get("high_chase_ratio", 0.0),
        "high_update_continues_after_entry_ratio": timing.get("high_update_continues_after_entry_ratio", 0.0),
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "avg_mfe_mae_ratio": ratio,
    }


def _mandatory_answers(
    *,
    shadow_metrics: Mapping[str, Any],
    baseline_fwd: Mapping[str, Any],
    dependency: Mapping[str, Any],
    overlay_q: Mapping[str, Any],
    completion_met: bool,
) -> dict[str, Any]:
    s_pnl = _float(shadow_metrics.get("total_pnl_yen_100"))
    b_pnl = _float(baseline_fwd.get("total_pnl_yen_100"))
    s_pf = _float(shadow_metrics.get("profit_factor"))
    b_pf = _float(baseline_fwd.get("profit_factor"))
    s_dd = _float(shadow_metrics.get("max_drawdown_yen_100"))
    b_dd = _float(baseline_fwd.get("max_drawdown_yen_100"))
    better = s_pnl > b_pnl and s_pf > b_pf and s_dd <= b_dd
    late_ok = _float(overlay_q.get("late_breakout_ratio")) < REF_OR_LATE_BREAKOUT
    chase_ok = _float(overlay_q.get("high_chase_ratio")) <= REF_OR_HIGH_CHASE
    dep6976_ok = _float(dependency.get("symbol_6976_share_pct")) < REF_OR_6976_SHARE
    top10_ok = _float(dependency.get("top10_trade_profit_share_pct")) < REF_OR_TOP10_SHARE
    adopt_promote = bool(
        completion_met
        and better
        and late_ok
        and chase_ok
        and dep6976_ok
        and top10_ok
    )
    return {
        "1_g3_g4_better_than_pbv2": better,
        "1_shadow_pnl": s_pnl,
        "1_baseline_pnl": b_pnl,
        "2_pnl_maintained": s_pnl >= b_pnl,
        "3_pf_maintained": s_pf >= b_pf,
        "4_dd_maintained": s_dd <= b_dd,
        "5_6976_dependency_recurred": _float(dependency.get("symbol_6976_share_pct")) >= REF_OR_6976_SHARE,
        "5_symbol_6976_share_pct": dependency.get("symbol_6976_share_pct"),
        "6_top10_dependency_recurred": _float(dependency.get("top10_trade_profit_share_pct")) >= REF_OR_TOP10_SHARE,
        "6_top10_trade_share_pct": dependency.get("top10_trade_profit_share_pct"),
        "7_late_breakout_suppressed": late_ok,
        "7_late_breakout_ratio": overlay_q.get("late_breakout_ratio"),
        "7_ref_or_late_ratio": REF_OR_LATE_BREAKOUT,
        "8_high_chase_suppressed": chase_ok,
        "8_high_chase_ratio": overlay_q.get("high_chase_ratio"),
        "8_ref_or_chase_ratio": REF_OR_HIGH_CHASE,
        "9_shadow_continue_value": bool(
            completion_met and not adopt_promote and (better or (late_ok and chase_ok and s_pnl > 0))
        ),
        "10_adopt_promotion": adopt_promote,
        "10_adopt_not_allowed": True,
        "completion_met": completion_met,
    }


@dataclass
class Phase520Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), 4)
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        bar_cache, all_days = _build_bar_cache(self.repo_root)
        forward_days = [d for d in all_days if d >= FORWARD_PERIOD_START]
        overlay = OVERLAY_DEFS["O_R003"]
        replay_pool, _runtime_shadows, _guard_c_block = _prepare_runtime_env(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
        universe = _universe_symbols(replay_pool)
        micro_lookup = _build_micro_lookup(replay_pool)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        baseline_trades_fwd: list[dict[str, Any]] = []
        for log in baseline_state.trade_log:
            if not log.get("exit_time"):
                continue
            day = str(log.get("day") or "")[:8]
            if day < FORWARD_PERIOD_START:
                continue
            tr = log.get("trade") or log
            baseline_trades_fwd.append(
                {
                    "strategy_id": BASELINE_STRATEGY_ID,
                    "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                    "day": day,
                    "entry_time": tr.get("entry_time"),
                    "exit_time": log.get("exit_time"),
                    "pnl_yen_100": _float(log.get("pnl_yen")),
                    "exit_reason": log.get("exit_reason"),
                }
            )
        baseline_fwd = _metrics_from_trades(baseline_trades_fwd, strategy_id=BASELINE_STRATEGY_ID)

        shadow_candidates: list[dict[str, Any]] = []

        def _scan_day(day: str) -> list[dict[str, Any]]:
            if day < FORWARD_PERIOD_START:
                return []
            raw = _scan_overlay_day(
                overlay,
                day=day,
                universe=universe,
                bar_cache=bar_cache,
                price_idx=price_idx,
            )
            kept: list[dict[str, Any]] = []
            for trade in raw:
                feats = _extract_entry_features(trade, bar_cache=bar_cache, micro_lookup=micro_lookup)
                if not _passes_g3_g4(feats):
                    continue
                kept.append(
                    {
                        **dict(trade),
                        "strategy_id": STRATEGY_ID,
                        "rolling_volume_percentile": feats.get("rolling_volume_percentile"),
                        "spread": feats.get("spread"),
                        "update_count_before_entry": feats.get("update_count_before_entry"),
                        "minutes_from_open": feats.get("minutes_from_open"),
                    }
                )
            return kept

        if self.parallel and forward_days:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_scan_day, day): day for day in forward_days}
                for fut in as_completed(futs):
                    shadow_candidates.extend(fut.result())
        else:
            for day in forward_days:
                shadow_candidates.extend(_scan_day(day))

        shadow_state = _simulate_precomputed_cap(shadow_candidates, mode="phase520_g3_g4_shadow")
        shadow_metrics = _strategy_metrics_safe(
            shadow_state,
            strategy_id=STRATEGY_ID,
            entry_rule_id="G3_G4_SHADOW",
            exit_rule_id="PBv2_EXIT",
            baseline=baseline_met,
        )

        trade_rows: list[dict[str, Any]] = []
        for log in shadow_state.trade_log:
            if not log.get("exit_time"):
                continue
            tr = log.get("trade") or log
            trade_rows.append(
                {
                    "strategy_id": STRATEGY_ID,
                    "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                    "day": str(log.get("day") or tr.get("day") or "")[:8],
                    "entry_time": tr.get("entry_time"),
                    "exit_time": log.get("exit_time"),
                    "pnl_yen_100": _float(log.get("pnl_yen")),
                    "exit_reason": log.get("exit_reason"),
                    "rolling_volume_percentile": tr.get("rolling_volume_percentile"),
                    "spread": tr.get("spread"),
                    "update_count_before_entry": tr.get("update_count_before_entry"),
                    "minutes_from_open": tr.get("minutes_from_open"),
                }
            )

        baseline_by_day_list: dict[str, list[float]] = defaultdict(list)
        for t in baseline_trades_fwd:
            baseline_by_day_list[str(t["day"])[:8]].append(_float(t["pnl_yen_100"]))

        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for tr in trade_rows:
            by_day[str(tr["day"])[:8]].append(tr)

        daily_rows: list[dict[str, Any]] = []
        for day in sorted(set(forward_days) | set(baseline_by_day_list)):
            dtrades = by_day.get(day, [])
            pnls = [_float(t["pnl_yen_100"]) for t in dtrades]
            wins = sum(1 for p in pnls if p > 0)
            dep = _dependency_metrics(dtrades) if dtrades else {}
            b_pnls = baseline_by_day_list.get(day, [])
            b_pnl = round(sum(b_pnls), 2)
            b_wins = sum(1 for p in b_pnls if p > 0)
            chron = _chron_pnls(dtrades)
            daily_rows.append(
                {
                    "day": day,
                    "strategy_id": STRATEGY_ID,
                    "total_pnl_yen_100": round(sum(pnls), 2),
                    "profit_factor": _pf(pnls),
                    "trade_count": len(pnls),
                    "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
                    "avg_pnl_yen_100": round(statistics.mean(pnls), 2) if pnls else 0.0,
                    "max_drawdown_yen_100": round(_max_drawdown_yen(chron) if chron else 0.0, 2),
                    "baseline_pnl_yen_100": b_pnl,
                    "beats_baseline": sum(pnls) > b_pnl if pnls else False,
                    "top1_symbol_profit_share_pct": dep.get("top1_symbol_profit_share_pct", 0),
                    "top3_symbol_profit_share_pct": dep.get("top3_symbol_profit_share_pct", 0),
                    "top1_day_profit_share_pct": dep.get("top1_day_profit_share_pct", 0),
                    "top10_trade_profit_share_pct": dep.get("top10_trade_profit_share_pct", 0),
                }
            )
            b_chron = b_pnls
            daily_rows.append(
                {
                    "day": day,
                    "strategy_id": BASELINE_STRATEGY_ID,
                    "total_pnl_yen_100": b_pnl,
                    "profit_factor": _pf(b_pnls),
                    "trade_count": len(b_pnls),
                    "win_rate": round(b_wins / len(b_pnls), 4) if b_pnls else 0.0,
                    "avg_pnl_yen_100": round(statistics.mean(b_pnls), 2) if b_pnls else 0.0,
                    "max_drawdown_yen_100": round(_max_drawdown_yen(b_chron) if b_chron else 0.0, 2),
                    "baseline_pnl_yen_100": b_pnl,
                    "beats_baseline": False,
                    "top1_symbol_profit_share_pct": 0,
                    "top3_symbol_profit_share_pct": 0,
                    "top1_day_profit_share_pct": 0,
                    "top10_trade_profit_share_pct": 0,
                }
            )

        dependency = _dependency_row(trade_rows)
        overlay_q = _overlay_quality(trade_rows, bar_cache)
        trading_days = len(forward_days)
        trade_count = len(trade_rows)
        last_forward_day = max(forward_days) if forward_days else ""
        forward_data_ceiling = bool(forward_days) and last_forward_day < PERIOD_END
        completion_met = (
            trading_days >= MIN_TRADING_DAYS
            or trade_count >= MIN_OVERLAY_TRADES
            or (forward_data_ceiling and trade_count > 0)
        )
        if trading_days >= MIN_TRADING_DAYS:
            completion_reason = "min_trading_days"
        elif trade_count >= MIN_OVERLAY_TRADES:
            completion_reason = "min_overlay_trades"
        elif forward_data_ceiling and trade_count > 0:
            completion_reason = "forward_data_ceiling"
        else:
            completion_reason = "collecting"

        mandatory = _mandatory_answers(
            shadow_metrics=shadow_metrics,
            baseline_fwd=baseline_fwd,
            dependency=dependency,
            overlay_q=overlay_q,
            completion_met=completion_met,
        )

        return {
            "verdict": PHASE520_VERDICT if completion_met else "phase520_g3_g4_forward_shadow_collecting",
            "generated_at": _now_iso(),
            "forward_period_start": FORWARD_PERIOD_START,
            "forward_period_end": PERIOD_END,
            "calibration_period_end": "20260615",
            "spread_median_frozen": SPREAD_MEDIAN_PHASE519,
            "parallel_workers": workers,
            "forward_trading_days": trading_days,
            "forward_data_last_day": last_forward_day,
            "forward_data_ceiling": forward_data_ceiling,
            "completion_reason": completion_reason,
            "shadow_candidate_count": len(shadow_candidates),
            "shadow_trade_count": trade_count,
            "completion_met": completion_met,
            "baseline_forward": baseline_fwd,
            "baseline_full_period": baseline_met,
            "shadow_metrics": shadow_metrics,
            "dependency": dependency,
            "overlay_quality": overlay_q,
            "reference_or_base": {
                "late_breakout_ratio": REF_OR_LATE_BREAKOUT,
                "high_chase_ratio": REF_OR_HIGH_CHASE,
                "top10_trade_share_pct": REF_OR_TOP10_SHARE,
                "symbol_6976_share_pct": REF_OR_6976_SHARE,
            },
            "trade_rows": trade_rows,
            "daily_rows": daily_rows,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "trades": reports / "phase520_shadow_trades.csv",
            "daily": reports / "phase520_shadow_daily.csv",
            "report": reports / "phase520_shadow_report.json",
            "docs": kabu / "docs" / "operations" / "phase520_g3_g4_forward_shadow.md",
        }
        _write_csv(paths["trades"], TRADE_FIELDS, list(result.get("trade_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("daily_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    sm = result.get("shadow_metrics") or {}
    bf = result.get("baseline_forward") or {}
    oq = result.get("overlay_quality") or {}
    dep = result.get("dependency") or {}
    lines = [
        "# Phase520 — G3_G4 Forward Shadow",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Forward period:** {result.get('forward_period_start')} – {result.get('forward_period_end')}",
        f"**Frozen spread median (Phase519):** {result.get('spread_median_frozen')}",
        f"**Completion met:** {result.get('completion_met')} "
        f"(days={result.get('forward_trading_days')}, trades={result.get('shadow_trade_count')}, "
        f"reason={result.get('completion_reason')})",
        f"**Forward data ceiling:** {result.get('forward_data_ceiling')} (last_day={result.get('forward_data_last_day')})",
        "",
        "## G3_G4 rules (overlay shadow only)",
        "",
        "- day_high + updates<=8",
        "- rolling_volume_percentile >= 80",
        f"- spread <= {result.get('spread_median_frozen')} (Phase519 median)",
        "- Exit: PBv2 Exit replay",
        "",
        "## Forward vs PBv2",
        "",
        "| | G3_G4 Shadow | PBv2 (forward) |",
        "|--|--------------|----------------|",
        f"| PnL | {sm.get('total_pnl_yen_100')} | {bf.get('total_pnl_yen_100')} |",
        f"| PF | {sm.get('profit_factor')} | {bf.get('profit_factor')} |",
        f"| maxDD | {sm.get('max_drawdown_yen_100')} | {bf.get('max_drawdown_yen_100')} |",
        f"| Trades | {sm.get('trades')} | {bf.get('trades')} |",
        "",
        "## Overlay quality",
        "",
        f"- true_breakout: {oq.get('true_breakout_ratio')} (ref OR late={ma.get('7_ref_or_late_ratio')})",
        f"- late_breakout: {oq.get('late_breakout_ratio')}",
        f"- high_chase: {oq.get('high_chase_ratio')}",
        "",
        "## Dependency",
        "",
        f"- 6976 share: {dep.get('symbol_6976_share_pct')}% (ref OR {REF_OR_6976_SHARE}%)",
        f"- top10 trade share: {dep.get('top10_trade_profit_share_pct')}% (ref OR {REF_OR_TOP10_SHARE}%)",
        "",
        "## Mandatory answers",
        "",
        f"1. Better than PBv2: **{ma.get('1_g3_g4_better_than_pbv2')}**",
        f"2. PnL maintained: **{ma.get('2_pnl_maintained')}**",
        f"3. PF maintained: **{ma.get('3_pf_maintained')}**",
        f"4. DD maintained: **{ma.get('4_dd_maintained')}**",
        f"5. 6976 recurred: **{ma.get('5_6976_dependency_recurred')}**",
        f"6. top10 recurred: **{ma.get('6_top10_dependency_recurred')}**",
        f"7. late suppressed: **{ma.get('7_late_breakout_suppressed')}**",
        f"8. high_chase suppressed: **{ma.get('8_high_chase_suppressed')}**",
        f"9. shadow continue value: **{ma.get('9_shadow_continue_value')}**",
        f"10. adopt promotion: **{ma.get('10_adopt_promotion')}** (not allowed)",
    ]
    return "\n".join(lines) + "\n"
