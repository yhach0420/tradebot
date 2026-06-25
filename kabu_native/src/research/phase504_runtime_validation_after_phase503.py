"""
Phase504 — Runtime Validation After Phase503 (research only).

Re-validates full-period replay + asset simulation with Phase503
classic_late_chase_rsi_over80 guard enabled on current PBv2 runtime stack.
No Runtime / Exit / Entry / Order changes.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _position_key
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _equity_row,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
    _summary_metrics,
    _symbol_contribution,
    _trade_summary_rows,
)
from research.phase493_global_entry_failure_audit import (
    DAY_622,
    PERIOD_END,
    PERIOD_START,
    _enrich_trade_row,
    _is_loser,
    _medians_from_losers,
    _replay_with_extra_block,
)
from research.phase495_new_feature_guard_replay import _counterfactual_row, _rows_from_state
from research.phase502_classic_indicator_guard_replay import _build_feature_environment
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE504_MODE = "phase504_post503_runtime"
EQUITY_LEVELS = (1_000_000, 1_500_000, 2_000_000)
PHASE488_BASELINE_PNL = 244_962.83
PHASE488_BASELINE_PF = 1.6171
PHASE488_BASELINE_MAXDD = 53_899.13
PHASE488_BASELINE_TRADES = 286
PHASE502_C_DELTA = 15_599.96
PHASE502_C_BLOCKED_WL = (1, 6)

VALIDATION_FIELDS = [
    "metric",
    "baseline_phase488",
    "phase502_guard_c_expected",
    "phase504_actual",
    "delta_vs_phase488",
    "notes",
]

ASSET_FIELDS = [
    "initial_equity_yen",
    "final_equity_yen",
    "total_pnl_yen",
    "return_pct",
    "max_drawdown_yen",
    "max_drawdown_pct",
    "cagr_pct",
    "accepted_count",
    "profit_factor",
]


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_pilot_flags(repo_root: Path) -> dict[str, Any]:
    try:
        from small_paper.config import load_pilot_config

        cfg_path = (
            resolve_kabu_root(repo_root)
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        if not cfg_path.exists():
            cfg_path = repo_root / "kabu_native" / "configs" / (
                "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
            )
        cfg = load_pilot_config(cfg_path)
        return {
            "config_path": str(cfg_path),
            "order_enabled": bool(cfg.order_enabled),
            "paper_only": bool(cfg.paper_only),
            "classic_late_chase_rsi_guard_enabled": bool(cfg.classic_late_chase_rsi_guard_enabled),
            "classic_late_chase_rsi_threshold": float(cfg.classic_late_chase_rsi_threshold),
            "late_chase_guard_enabled": bool(cfg.late_chase_guard_enabled),
            "high_drift_guard_enabled": bool(cfg.high_drift_guard_enabled),
            "weak_shape_reject_enabled": bool(cfg.weak_shape_reject_enabled),
            "no_progress_exit_enabled": bool(cfg.no_progress_exit_enabled),
            "max_concurrent_positions": int(cfg.max_concurrent_positions),
        }
    except Exception as exc:  # noqa: BLE001
        return {"config_load_error": str(exc)}


def _guard_blocked_from_baseline(
    baseline_state: Any,
    baseline_rows: Sequence[Mapping[str, Any]],
    *,
    guard_block: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    row_by_pk = {str(r.get("position_key") or ""): r for r in baseline_rows}
    blocked_pnls: list[float] = []
    blocked_rows: list[dict[str, Any]] = []
    for log in baseline_state.trade_log:
        tr = log.get("trade") or log
        if not guard_block(tr):
            continue
        pk = str(_position_key(tr))
        row = row_by_pk.get(pk, {})
        pnl = float(row.get("pnl_yen") if row.get("pnl_yen") is not None else log.get("pnl_yen") or 0)
        blocked_pnls.append(pnl)
        blocked_rows.append(
            {
                "symbol": str(tr.get("symbol") or row.get("symbol") or "").replace(".T", ""),
                "day": str(row.get("day") or log.get("day") or tr.get("day") or "")[:8],
                "pnl_yen": round(pnl, 2),
                "exit_reason": row.get("exit_reason") or log.get("exit_reason"),
            }
        )
    bw = sum(1 for p in blocked_pnls if p > 0)
    bl = sum(1 for p in blocked_pnls if p < 0)
    return {
        "blocked_total": len(blocked_pnls),
        "blocked_winners": bw,
        "blocked_losers": bl,
        "blocked_pnl_yen_100": round(sum(blocked_pnls), 2),
        "blocked_rows": blocked_rows,
        "impact_6976": round(
            sum(r["pnl_yen"] for r in blocked_rows if r["symbol"] == "6976"), 2
        ),
        "impact_4062": round(
            sum(r["pnl_yen"] for r in blocked_rows if r["symbol"] == "4062"), 2
        ),
        "impact_20260622": round(
            sum(r["pnl_yen"] for r in blocked_rows if r["day"] == DAY_622), 2
        ),
    }


def _verdict(
    *,
    summary: Mapping[str, Any],
    guard_cf: Mapping[str, Any],
    sym6976: Mapping[str, Any],
    pilot_flags: Mapping[str, Any],
) -> str:
    pnl = float(summary.get("total_pnl_yen") or 0)
    pf = float(summary.get("profit_factor") or 0)
    max_dd = float(summary.get("max_drawdown_yen") or 0)
    delta_vs488 = pnl - PHASE488_BASELINE_PNL
    blocked_w = int(guard_cf.get("blocked_winners") or 0)
    blocked_l = int(guard_cf.get("blocked_losers") or 0)
    blocked_total = int(guard_cf.get("blocked_total") or 0)
    share6976 = abs(float(sym6976.get("share_of_total_pnl") or 0))

    if pilot_flags.get("order_enabled") is not False or not pilot_flags.get("paper_only"):
        return "needs_rollback"
    if not pilot_flags.get("classic_late_chase_rsi_guard_enabled"):
        return "needs_rollback"
    if pnl < PHASE488_BASELINE_PNL - 5000 or pf < 1.0:
        return "runtime_regression_detected"
    if max_dd > PHASE488_BASELINE_MAXDD + 5000:
        return "runtime_regression_detected"
    if delta_vs488 < PHASE502_C_DELTA * 0.85:
        return "runtime_regression_detected"
    if blocked_total < 5 or blocked_total > 15:
        return "runtime_regression_detected"
    if blocked_w > 3 or blocked_l < 4:
        return "runtime_regression_detected"
    if share6976 > 0.68:
        return "runtime_regression_detected"
    if (
        pnl >= PHASE488_BASELINE_PNL
        and pf >= 1.65
        and delta_vs488 >= PHASE502_C_DELTA * 0.95
        and max_dd <= PHASE488_BASELINE_MAXDD
    ):
        return "runtime_ready"
    return "runtime_regression_detected"


def run_phase504(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    actual_days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    start_day = actual_days[0] if actual_days else PERIOD_START
    end_day = actual_days[-1] if actual_days else PERIOD_END

    pre_rows = [
        _enrich_trade_row({"trade": t, "day": str(t.get("day") or "")[:8], "pnl_yen": 0, "exit_reason": ""})
        for t in replay_pool
        if pass_pbv2(t)
    ]
    medians = _medians_from_losers([r for r in pre_rows if _is_loser(r)])
    feature_row, _thresholds = _build_feature_environment(
        replay_pool, price_idx=price_idx, medians=medians
    )

    def guard_c_block(trade: Mapping[str, Any]) -> bool:
        row = feature_row(trade)
        return bool(row.get("late_chase_cluster")) and _float(row.get("rsi_over80")) == 1.0

    def pass_pbv2_phase503(trade: Mapping[str, Any]) -> bool:
        return pass_pbv2(trade) and not guard_c_block(trade)

    baseline_state = _simulate_runtime_replay(
        replay_pool,
        runtime_shadows,
        mode=f"{REPLAY_MODE}_phase504_baseline",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )
    current_state = _replay_with_extra_block(
        replay_pool,
        runtime_shadows,
        extra_block=guard_c_block,
        mode_suffix="phase504_guard_c",
    )

    baseline_met = _summary_metrics(baseline_state, initial_equity=1_500_000.0)
    summary = _summary_metrics(current_state, initial_equity=1_500_000.0)
    baseline_rows = _rows_from_state(baseline_state)
    guard_entry = _guard_blocked_from_baseline(
        baseline_state, baseline_rows, guard_block=guard_c_block
    )
    guard_cf = _counterfactual_row(
        current_state,
        baseline_state,
        scenario="classic_late_chase_rsi_over80",
        baseline_pnl=float(baseline_met["total_pnl_yen"]),
        baseline_pf=baseline_met["profit_factor"],
        baseline_max_dd=float(baseline_met["max_drawdown_yen"]),
        baseline_rows=baseline_rows,
        medians=medians,
    )

    sym6976 = _symbol_contribution(current_state.trade_log, "6976")
    sym4062 = _symbol_contribution(current_state.trade_log, "4062")
    day622_pnl = round(
        sum(
            float(r.get("pnl_yen") or 0)
            for r in _rows_from_state(current_state)
            if str(r.get("day") or "")[:8] == DAY_622
        ),
        2,
    )
    pilot_flags = _load_pilot_flags(repo_root)

    equity_rows: list[dict[str, Any]] = []
    for eq in EQUITY_LEVELS:
        eq_state = _simulate_runtime_replay(
            replay_pool,
            runtime_shadows,
            mode=f"{PHASE504_MODE}_eq{eq}",
            entry_block_fn=_entry_block(pass_pbv2_phase503),
            initial_equity=float(eq),
        )
        equity_rows.append(
            _equity_row(eq_state, initial_equity=float(eq), start_day=start_day, end_day=end_day)
        )

    trade_rows = _trade_summary_rows(current_state)
    validation_rows = [
        {
            "metric": "total_pnl_yen",
            "baseline_phase488": PHASE488_BASELINE_PNL,
            "phase502_guard_c_expected": round(PHASE488_BASELINE_PNL + PHASE502_C_DELTA, 2),
            "phase504_actual": summary["total_pnl_yen"],
            "delta_vs_phase488": round(float(summary["total_pnl_yen"]) - PHASE488_BASELINE_PNL, 2),
            "notes": "Phase503 guard C adoption target",
        },
        {
            "metric": "profit_factor",
            "baseline_phase488": PHASE488_BASELINE_PF,
            "phase502_guard_c_expected": 1.6904,
            "phase504_actual": summary["profit_factor"],
            "delta_vs_phase488": round(float(summary["profit_factor"] or 0) - PHASE488_BASELINE_PF, 4),
            "notes": "",
        },
        {
            "metric": "max_drawdown_yen",
            "baseline_phase488": PHASE488_BASELINE_MAXDD,
            "phase502_guard_c_expected": round(PHASE488_BASELINE_MAXDD - 10_899.12, 2),
            "phase504_actual": summary["max_drawdown_yen"],
            "delta_vs_phase488": round(float(summary["max_drawdown_yen"]) - PHASE488_BASELINE_MAXDD, 2),
            "notes": "Phase502 C improved maxDD",
        },
        {
            "metric": "trade_count",
            "baseline_phase488": PHASE488_BASELINE_TRADES,
            "phase502_guard_c_expected": PHASE488_BASELINE_TRADES - 7,
            "phase504_actual": summary["trade_count"],
            "delta_vs_phase488": int(summary["trade_count"]) - PHASE488_BASELINE_TRADES,
            "notes": "7 fewer trades expected (1W+6L)",
        },
        {
            "metric": "rejected_classic_late_chase_rsi_over80",
            "baseline_phase488": 0,
            "phase502_guard_c_expected": 7,
            "phase504_actual": guard_entry["blocked_total"],
            "delta_vs_phase488": guard_entry["blocked_total"],
            "notes": f"blocked W/L={guard_entry['blocked_winners']}/{guard_entry['blocked_losers']} (Phase502 C: 1/6)",
        },
        {
            "metric": "rejected_guard_pnl_yen",
            "baseline_phase488": 0,
            "phase502_guard_c_expected": round(-PHASE502_C_DELTA, 2),
            "phase504_actual": guard_entry["blocked_pnl_yen_100"],
            "delta_vs_phase488": guard_entry["blocked_pnl_yen_100"],
            "notes": "Negative blocked PnL = guard value",
        },
        {
            "metric": "6976_share",
            "baseline_phase488": 0.6225,
            "phase502_guard_c_expected": "",
            "phase504_actual": sym6976.get("share_of_total_pnl"),
            "delta_vs_phase488": "",
            "notes": f"6976 pnl={sym6976.get('total_pnl_yen')}",
        },
        {
            "metric": "4062_share",
            "baseline_phase488": 0.0367,
            "phase502_guard_c_expected": "",
            "phase504_actual": sym4062.get("share_of_total_pnl"),
            "delta_vs_phase488": "",
            "notes": f"4062 pnl={sym4062.get('total_pnl_yen')}",
        },
        {
            "metric": "day_622_pnl",
            "baseline_phase488": "",
            "phase502_guard_c_expected": "",
            "phase504_actual": day622_pnl,
            "delta_vs_phase488": "",
            "notes": f"guard blocked on 6/22={guard_entry.get('impact_20260622')}",
        },
    ]

    verdict = _verdict(summary=summary, guard_cf=guard_entry, sym6976=sym6976, pilot_flags=pilot_flags)
    eq_map = {int(r["initial_equity_yen"]): r for r in equity_rows}

    mandatory = {
        "total_pnl": summary["total_pnl_yen"],
        "profit_factor": summary["profit_factor"],
        "max_drawdown_yen": summary["max_drawdown_yen"],
        "trade_count": summary["trade_count"],
        "rejected_by_classic_late_chase_rsi_over80": guard_entry["blocked_total"],
        "rejected_guard_blocked_winners": guard_entry["blocked_winners"],
        "rejected_guard_blocked_losers": guard_entry["blocked_losers"],
        "rejected_guard_pnl_yen": guard_entry["blocked_pnl_yen_100"],
        "6976_contribution": sym6976,
        "4062_contribution": sym4062,
        "6976_guard_blocked_pnl": guard_entry.get("impact_6976"),
        "4062_guard_blocked_pnl": guard_entry.get("impact_4062"),
        "day_622_pnl": day622_pnl,
        "day_622_guard_blocked_pnl": guard_entry.get("impact_20260622"),
        "equity_1m": eq_map.get(1_000_000),
        "equity_1p5m": eq_map.get(1_500_000),
        "equity_2m": eq_map.get(2_000_000),
        "phase488_baseline_pnl": PHASE488_BASELINE_PNL,
        "phase502_guard_c_delta_expected": PHASE502_C_DELTA,
        "delta_vs_phase488": round(float(summary["total_pnl_yen"]) - PHASE488_BASELINE_PNL, 2),
        "delta_vs_phase502_expected": round(
            float(summary["total_pnl_yen"]) - (PHASE488_BASELINE_PNL + PHASE502_C_DELTA), 2
        ),
        "pilot_config": pilot_flags,
        "reject_funnel_note": (
            "Daily Summary / Discord Reject Funnel uses reject_reason_counts; "
            "classic_late_chase_rsi_over80 emitted when Phase503 guard fires at runtime"
        ),
        "runtime_ok": verdict == "runtime_ready",
        "start_tomorrow_ok": verdict == "runtime_ready",
        "verdict": verdict,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": start_day,
        "period_end": end_day,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_validation_rows": validation_rows,
        "_asset_rows": equity_rows,
        "_trade_rows": trade_rows,
        "_guard_cf": guard_cf,
        "_guard_entry": guard_entry,
        "_summary": summary,
        "_baseline_summary": baseline_met,
    }


@dataclass
class Phase504Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase504(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "validation": reports / "phase504_runtime_validation_after_phase503.csv",
            "asset": reports / "phase504_asset_simulation.csv",
            "summary": reports / "phase504_summary.json",
        }
        _write_csv(paths["validation"], VALIDATION_FIELDS, list(result.get("_validation_rows") or []))
        _write_csv(paths["asset"], ASSET_FIELDS, list(result.get("_asset_rows") or []))
        payload = {
            k: v
            for k, v in result.items()
            if not str(k).startswith("_")
        }
        paths["summary"].write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return paths
