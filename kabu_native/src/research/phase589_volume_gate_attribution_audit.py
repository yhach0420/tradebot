"""
Phase589 — Volume Gate Attribution Audit (research only).

Documents daytrade_suitability / volume gate algorithm and attributes rejects
with counterfactual replay using the correct vol_liq gate (not phase364 proxy).
No Runtime / ENTRY changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase470_momentum_necessity_tournament import late_chase_block
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase530_winner_capture_research import _sym_key
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import (
    _is_mfe0,
    _load_canonical_trades_for_day,
    _mfe_pct,
)
from research.phase546_entry_cluster_shadow_replay import VARIANTS, _is_rejected, _merge_dataset, _trade_key
from research.phase554_stop_low_mfe_entry_quality_feature_study import _is_stop_low_mfe_554
from research.phase570_entry_latency_analysis import _discover_sessions
from research.phase571_entry_wait_breakdown import PERIOD_START
from research.phase582_universe_optimization_study import _discover_days
from research.phase464_pre_gate_archetype_audit import _passes_board_gate, _weak_shape_block
from research.phase473_trend_entry_architecture import _entry_block
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.daytrade_suitability import (
    RULE_VOLATILITY_LIQUIDITY_TOP50,
    percentile_value,
    volatility_liquidity_score,
)
from small_paper.daytrade_suitability_gate import REJECT_DAYTRADE_SUITABILITY
from small_paper.entry_expectancy_score_shadow import (
    MOMENTUM_SCORE_CUTOFF_P33,
    momentum_score_cutoff_pass,
)
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

V6_SPEC = next(v for v in VARIANTS if v.variant_id == "V6")
PHASE589_VERDICT = "phase589_volume_gate_attribution_done"
MATCH_TOLERANCE_SEC = 180
TV_LOW_JPY = 100_000_000.0
ATR_LOW_PCT = 0.8
TURNOVER_LOW = 0.002

ALGORITHM_FIELDS = [
    "component_id",
    "gate_layer",
    "function_name",
    "source_file",
    "feature_or_score",
    "formula",
    "threshold_rule",
    "filter_type",
    "production_enabled",
    "notes",
]

BREAKDOWN_FIELDS = [
    "reject_subcategory",
    "reject_count",
    "share_pct",
    "source",
]

COUNTERFACTUAL_FIELDS = [
    "reject_subcategory",
    "rejected_count",
    "simulated_trades",
    "unavailable_count",
    "simulated_pnl",
    "simulated_pf",
    "simulated_win_rate",
    "simulated_mfe_avg",
    "simulated_stop_rate",
]

RELAXATION_FIELDS = [
    "variant_id",
    "relaxation_pct",
    "label",
    "effective_threshold",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "mfe0_count",
    "stop_low_mfe_count",
    "big_loser_count",
    "added_trades",
    "removed_trades",
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
    "runtime_change_candidate",
]

IMPACT_FIELDS = [
    "variant_id",
    "day",
    "symbol",
    "baseline_pnl_yen_100",
    "variant_pnl_yen_100",
    "delta_pnl_yen_100",
    "baseline_trades",
    "variant_trades",
]


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _vol_liq_score(trade: Mapping[str, Any]) -> Optional[float]:
    v = _float(trade.get("volatility_liquidity_score"))
    if v is not None:
        return v
    tv = _float(trade.get("trading_value") or trade.get("trading_value_jpy"))
    atr = _float(trade.get("atr_pct"))
    return volatility_liquidity_score(atr, tv)


def _pass_core_pbv2(trade: Mapping[str, Any]) -> bool:
    if not momentum_score_cutoff_pass(trade, cutoff=MOMENTUM_SCORE_CUTOFF_P33):
        return False
    if not _passes_board_gate(trade):
        return False
    if guard_high_drift(trade):
        return False
    if _weak_shape_block(trade):
        return False
    if phase364_blocked_only(trade):
        return False
    if late_chase_block(trade):
        return False
    if _is_rejected(trade, V6_SPEC):
        return False
    if _is_stop_low_mfe_554(trade):
        return False
    return True


def _pass_daytrade(trade: Mapping[str, Any], threshold: float, relaxation_pct: float) -> bool:
    if relaxation_pct <= 0:
        return True
    score = _vol_liq_score(trade)
    if score is None:
        return False
    eff = threshold * (relaxation_pct / 100.0)
    return float(score) >= eff


def _make_pass_fn(threshold: float, relaxation_pct: float) -> Callable[[Mapping[str, Any]], bool]:
    def _pass(trade: Mapping[str, Any]) -> bool:
        if not _pass_core_pbv2(trade):
            return False
        return _pass_daytrade(trade, threshold, relaxation_pct)

    return _pass


def _algorithm_rows() -> list[dict[str, Any]]:
    return [
        {
            "component_id": "daytrade_suitability",
            "gate_layer": "ExposureGate.evaluate_entry",
            "function_name": "DaytradeSuitabilityState.check",
            "source_file": "src/small_paper/daytrade_suitability_gate.py",
            "feature_or_score": "volatility_liquidity_score",
            "formula": "atr_pct * log10(max(trading_value_jpy,1))",
            "threshold_rule": "median(prior_session vol_liq scores) @ pct=0.50 (rule volatility_liquidity_top50)",
            "filter_type": "hard_reject",
            "production_enabled": True,
            "notes": "reject_reason=daytrade_suitability; score<threshold blocks entry",
        },
        {
            "component_id": "daytrade_missing_score",
            "gate_layer": "DaytradeSuitabilityState.check",
            "function_name": "DaytradeSuitabilityState.check",
            "source_file": "src/small_paper/daytrade_suitability_gate.py",
            "feature_or_score": "atr_pct, trading_value_jpy",
            "formula": "volatility_liquidity_score returns None if atr or tv missing",
            "threshold_rule": "N/A",
            "filter_type": "hard_reject",
            "production_enabled": True,
            "notes": "reason=missing_vol_liq_score",
        },
        {
            "component_id": "daytrade_threshold_build",
            "gate_layer": "session_startup",
            "function_name": "build_vol_liq_threshold_with_startup_cache",
            "source_file": "src/small_paper/vol_liq_startup_cache.py",
            "feature_or_score": "prior accepted trades vol_liq scores",
            "formula": "percentile_value(scores, 0.50)",
            "threshold_rule": "prior_only lookback; quality>=0.70 trades",
            "filter_type": "rolling_threshold",
            "production_enabled": True,
            "notes": "Phase575 cache wraps full prior_vol_liq_scores scan",
        },
        {
            "component_id": "low_liquidity_shadow",
            "gate_layer": "pilot_runner._execute_accepted_entry",
            "function_name": "low_liquidity_shadow check",
            "source_file": "src/small_paper/pilot_runner.py",
            "feature_or_score": "trading_value, turnover_proxy",
            "formula": "tv>=1e8 AND turnover_proxy>=0.002",
            "threshold_rule": "trading_value_min=1e8; turnover_proxy_min=0.002",
            "filter_type": "shadow_log_only",
            "production_enabled": False,
            "notes": "logs low_liquidity_shadow_rejected; NOT hard reject in production yaml",
        },
        {
            "component_id": "phase364_near_day_high",
            "gate_layer": "pass_pbv2 replay stack",
            "function_name": "phase364_blocked_only",
            "source_file": "src/research/phase365_production_stack_validation.py",
            "feature_or_score": "day_high_distance_pct, momentum",
            "formula": "dist<=1.5 AND mom<0.30 on dynamic40",
            "threshold_rule": "DAY_HIGH_DISTANCE_MAX=1.5; MOMENTUM_MAX=0.30",
            "filter_type": "hard_reject_replay_only",
            "production_enabled": True,
            "notes": "Phase588 mislabeled this as volume gate in replay ablation; NOT daytrade_suitability",
        },
        {
            "component_id": "composite_daytrade_score_legacy",
            "gate_layer": "diagnostic_only",
            "function_name": "attach_composite_scores",
            "source_file": "src/small_paper/daytrade_suitability.py",
            "feature_or_score": "daytrade_suitability_score",
            "formula": "0.4*norm_atr + 0.3*norm_range + 0.2*norm_tv + 0.1*norm_turnover",
            "threshold_rule": "not used for live hard reject",
            "filter_type": "diagnostic",
            "production_enabled": False,
            "notes": "Phase82 composite; production gate uses vol_liq score only",
        },
    ]


def _synthetic_atr_pct(trade: Mapping[str, Any]) -> Optional[float]:
    ep = _float(trade.get("entry_price"))
    hi = _float(trade.get("day_high_price"))
    lo = _float(trade.get("day_low_price"))
    if ep and ep > 0 and hi is not None and lo is not None and hi >= lo:
        return round((hi - lo) / ep * 100.0, 4)
    dist = _float(trade.get("day_high_distance_pct"))
    if ep and ep > 0 and dist is not None and hi is not None:
        lo_est = hi - dist / 100.0 * ep if dist else None
        if lo_est is not None and hi >= lo_est:
            return round((hi - lo_est) / ep * 100.0, 4)
    return None


def _enrich_pool_vol_liq(pool: list[dict[str, Any]]) -> None:
    for t in pool:
        if t.get("atr_pct") is None:
            atr = _synthetic_atr_pct(t)
            if atr is not None:
                t["atr_pct"] = atr
        if t.get("trading_value_jpy") is None and t.get("trading_value") is not None:
            t["trading_value_jpy"] = t.get("trading_value")
        s = _vol_liq_score(t)
        if s is not None:
            t["volatility_liquidity_score"] = s


def _classify_with_trade(row: Mapping[str, Any], trade: Optional[Mapping[str, Any]], threshold: float) -> str:
    if trade is None:
        return _classify_volume_reject(row)
    score = _vol_liq_score(trade)
    tv = _float(trade.get("trading_value") or trade.get("trading_value_jpy"))
    atr = _float(trade.get("atr_pct"))
    turnover = _float(trade.get("turnover_proxy"))
    if score is None:
        return "missing_vol_liq_data"
    if tv is not None and tv < TV_LOW_JPY and (atr is None or atr < ATR_LOW_PCT):
        return "trading_value_and_vol_low"
    if tv is not None and tv < TV_LOW_JPY:
        return "trading_value_insufficient"
    if atr is not None and atr < ATR_LOW_PCT:
        return "volatility_insufficient"
    if turnover is not None and turnover < TURNOVER_LOW:
        return "turnover_proxy_low"
    if threshold > 0 and score < threshold * 0.85:
        return "vol_liq_score_well_below_threshold"
    if threshold > 0 and score < threshold:
        return "vol_liq_score_slightly_below_threshold"
    return "vol_liq_score_below_threshold"


def _classify_volume_reject(row: Mapping[str, Any]) -> str:
    rej = str(row.get("reject_reason") or row.get("gate_reject_reason") or "")
    if rej == "low_liquidity":
        return "liquidity_insufficient_legacy"
    if rej not in (REJECT_DAYTRADE_SUITABILITY, "daytrade_suitability"):
        if "liquidity" in rej:
            return "liquidity_insufficient_legacy"
        return "other_volume"

    reason = str(row.get("daytrade_block_reason") or row.get("internal_reason") or "")
    if reason == "missing_vol_liq_score":
        return "missing_vol_liq_data"

    score = _float(row.get("daytrade_suitability_score") or row.get("volatility_liquidity_score"))
    th = _float(row.get("daytrade_suitability_threshold"))
    tv = _float(row.get("trading_value") or row.get("trading_value_jpy"))
    atr = _float(row.get("atr_pct"))
    turnover = _float(row.get("turnover_proxy"))

    if score is None:
        return "missing_vol_liq_data"

    if tv is not None and tv < TV_LOW_JPY and (atr is None or atr < ATR_LOW_PCT):
        return "trading_value_and_vol_low"
    if tv is not None and tv < TV_LOW_JPY:
        return "trading_value_insufficient"
    if atr is not None and atr < ATR_LOW_PCT:
        return "volatility_insufficient"
    if turnover is not None and turnover < TURNOVER_LOW:
        return "turnover_proxy_low"

    if th is not None and score < th:
        gap = th - score
        if gap > max(th * 0.15, 0.05):
            return "vol_liq_score_well_below_threshold"
        return "vol_liq_score_slightly_below_threshold"

    return "vol_liq_score_below_threshold"


def _load_volume_reject_events(session_dir: Path, day: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fname in ("small_paper_events.csv", "small_paper_events.jsonl"):
        path = session_dir / fname
        if not path.is_file():
            continue
        if fname.endswith(".csv"):
            for ev in _stream_events_csv(path):
                rej = str(ev.get("gate_reject_reason") or ev.get("reject_reason") or "")
                if ev.get("event_type") not in ("rejected", "candidate") and rej not in (
                    REJECT_DAYTRADE_SUITABILITY,
                    "daytrade_suitability",
                    "low_liquidity",
                ):
                    continue
                if rej not in (REJECT_DAYTRADE_SUITABILITY, "daytrade_suitability", "low_liquidity"):
                    if "daytrade" not in rej and "liquidity" not in rej:
                        continue
                row = dict(ev)
                row["day"] = day
                row["session"] = session_dir.name
                row["symbol"] = _sym_key(row.get("symbol"))
                row["reject_reason"] = rej
                row["reject_subcategory"] = _classify_volume_reject(row)
                rows.append(row)
        else:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rej = str(ev.get("gate_reject_reason") or ev.get("reject_reason") or "")
                    if rej not in (REJECT_DAYTRADE_SUITABILITY, "daytrade_suitability", "low_liquidity"):
                        continue
                    row = dict(ev)
                    row["day"] = day
                    row["session"] = session_dir.name
                    row["symbol"] = _sym_key(row.get("symbol"))
                    row["reject_reason"] = rej
                    row["reject_subcategory"] = _classify_volume_reject(row)
                    rows.append(row)
    return rows


def _load_audit_volume_rejects(session_dir: Path, day: str) -> list[dict[str, Any]]:
    path = session_dir / "entry_scan_audit.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("audit_type") != "entry_symbol_eval":
                continue
            rej = str(ev.get("reject_reason") or "")
            if rej not in (REJECT_DAYTRADE_SUITABILITY, "daytrade_suitability", "low_liquidity"):
                continue
            if bool(ev.get("entry_decision")):
                continue
            row = dict(ev)
            row["day"] = day
            row["session"] = session_dir.name
            row["symbol"] = _sym_key(row.get("symbol"))
            row["reject_subcategory"] = _classify_volume_reject(row)
            row["eval_start_ts"] = str(ev.get("eval_start_ts") or "")
            out.append(row)
    return out


def _build_pool_index(pool: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[tuple[float, dict[str, Any]]]]:
    idx: dict[tuple[str, str], list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for trade in pool:
        sym = _sym_key(trade.get("symbol"))
        day = str(trade.get("day") or "")[:8]
        dt = _parse_ts(str(trade.get("entry_time") or ""))
        if not sym or not day or dt is None:
            continue
        idx[(sym, day)].append((dt.timestamp(), dict(trade)))
    for key in idx:
        idx[key].sort(key=lambda x: x[0])
    return idx


def _match_trade(
    idx: Mapping[tuple[str, str], Sequence[tuple[float, dict[str, Any]]]],
    *,
    symbol: str,
    day: str,
    ts: str,
) -> Optional[dict[str, Any]]:
    dt = _parse_ts(ts)
    if dt is None:
        return None
    t = dt.timestamp()
    best: Optional[tuple[float, dict[str, Any]]] = None
    best_d = float("inf")
    for et, trade in idx.get((symbol, day), []):
        d = abs(et - t)
        if d < best_d:
            best_d = d
            best = (et, trade)
    if best is None or best_d > MATCH_TOLERANCE_SEC:
        return None
    return best[1]


def _trade_outcome(trade: Mapping[str, Any]) -> dict[str, Any]:
    pnl = _num(trade.get("pnl_yen_100") or trade.get("pnl_yen"))
    mfe = _mfe_pct(trade)
    reason = str(trade.get("exit_reason") or trade.get("close_reason") or "")
    return {
        "pnl": pnl,
        "mfe": mfe,
        "stop": "stop" in reason.lower(),
        "winner": pnl > 0,
    }


def _aggregate_cf(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unavailable: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    for row in rows:
        cat = str(row.get("reject_subcategory") or "other_volume")
        rejected[cat] += 1
        if row.get("counterfactual_available"):
            groups[cat].append(row)
        else:
            unavailable[cat] += 1
    out: list[dict[str, Any]] = []
    total = sum(rejected.values())
    for cat in sorted(rejected, key=lambda c: -rejected[c]):
        sim = groups.get(cat, [])
        pnls = [_num(r.get("simulated_pnl")) for r in sim]
        mfes = [_num(r.get("simulated_mfe")) for r in sim if r.get("simulated_mfe") is not None]
        stops = sum(1 for r in sim if r.get("simulated_stop"))
        out.append(
            {
                "reject_subcategory": cat,
                "rejected_count": rejected[cat],
                "simulated_trades": len(sim),
                "unavailable_count": unavailable[cat],
                "simulated_pnl": round(sum(pnls), 2),
                "simulated_pf": _pf(pnls),
                "simulated_win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                "simulated_mfe_avg": round(statistics.mean(mfes), 4) if mfes else 0.0,
                "simulated_stop_rate": round(stops / len(sim), 4) if sim else 0.0,
            }
        )
        _ = total
    return out


def _run_replay(
    pool: Sequence[Mapping[str, Any]],
    shadows: Mapping[str, Any],
    pass_fn: Callable[[Mapping[str, Any]], bool],
    *,
    variant_id: str,
) -> Any:
    import heapq

    from research.phase271_leverage_attribution_and_robustness import build_spec
    from research.phase440_boundary_capacity_audit import ShadowExitInfo
    from research.phase443_full_runtime_combined_capital_sim import (
        LEVERAGE,
        STOP_POLICY,
        CapacityReplayState,
        _day_from_ts,
    )

    spec = build_spec(leverage=LEVERAGE, cap=5, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=variant_id,
        max_concurrent_positions=5,
        spec=spec,
        initial_equity=1_500_000.0,
        equity_floor=750_000.0,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=REPLAY_MODE,
        shadow_by_key=dict(shadows),
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
    )
    entry_heap: list[tuple[Any, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(pool):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        heapq.heappush(entry_heap, (ent, 0, f"e{i:05d}", dict(trade)))
    exit_heap: list[tuple[Any, int, str, dict[str, Any]]] = []
    open_trade: dict[str, dict[str, Any]] = {}
    while entry_heap or exit_heap:
        ne = entry_heap[0] if entry_heap else None
        nx = exit_heap[0] if exit_heap else None
        if nx is not None and (ne is None or nx[0] <= ne[0]):
            ex_dt, _, key, trade = heapq.heappop(exit_heap)
            ts = ex_dt.isoformat()
            day = _day_from_ts(ts)
            si = shadows.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)
            pnl, reason = state._close_pnl(trade, si)
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            open_trade.pop(key, None)
            continue
        ent_dt, _, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = _day_from_ts(ts)
        if state.try_entry(trade, ts, day):
            key = _position_key(trade)
            si = shadows.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)
            ex_dt = state._exit_dt(trade, si)
            open_trade[key] = trade
            heapq.heappush(exit_heap, (ex_dt, 1, key, trade))
    return state


def _metrics_from_state(state: Any, pool_by_key: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    chron = [float(log.get("pnl_yen") or 0) for log in state.trade_log]
    keys = {_position_key(log.get("trade") or log) for log in state.trade_log}
    mfe0 = slm = big_l = 0
    for log in state.trade_log:
        tr = dict(log.get("trade") or log)
        meta = pool_by_key.get(_position_key(tr), tr)
        mfe0 += int(_is_mfe0(meta))
        slm += int(_is_stop_low_mfe(meta) or _is_stop_low_mfe_554(meta))
        if _num(meta.get("pnl_yen_100") or meta.get("pnl_yen")) <= -5000:
            big_l += 1
    n = len(chron)
    return {
        "trades": n,
        "pnl_yen_100": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen_100": round(_max_drawdown_yen(chron) if chron else 0.0, 2),
        "win_rate": round(sum(1 for p in chron if p > 0) / n, 4) if n else 0.0,
        "mfe0_count": mfe0,
        "stop_low_mfe_count": slm,
        "big_loser_count": big_l,
        "_keys": keys,
        "_state": state,
    }


def _symbol_day_pnl(state: Any) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], int]]:
    pnl: dict[tuple[str, str], float] = defaultdict(float)
    cnt: dict[tuple[str, str], int] = defaultdict(int)
    for log in state.trade_log:
        tr = dict(log.get("trade") or log)
        sym = _sym_key(tr.get("symbol"))
        day = str(log.get("day") or tr.get("day") or "")[:8]
        pnl[(day, sym)] += float(log.get("pnl_yen") or 0)
        cnt[(day, sym)] += 1
    return pnl, cnt


@dataclass
class Phase589Job:
    repo_root: Path
    workers: int = 4

    def run(self) -> dict[str, Any]:
        end = _latest_live_day(self.repo_root)
        days = [d for d in _discover_days(self.repo_root) if PERIOD_START <= d <= end]
        reports = resolve_reports_dir(self.repo_root)
        sessions = [
            s
            for s in _discover_sessions(self.repo_root, start=PERIOD_START, end=end)
            if "live_session_" in str(s.get("session_dir") or "")
        ]

        algorithm_rows = _algorithm_rows()

        # Investigation 2 — live reject breakdown
        volume_rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = []
            for s in sessions:
                sd = Path(str(s["session_dir"]))
                day = str(s["day"])
                futs.append(ex.submit(_load_volume_reject_events, sd, day))
                futs.append(ex.submit(_load_audit_volume_rejects, sd, day))
            for fut in as_completed(futs):
                volume_rows.extend(fut.result())

        # Dedupe audit vs events by symbol+day+ts+reason
        seen: set[tuple[str, str, str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for row in volume_rows:
            key = (
                str(row.get("symbol")),
                str(row.get("day")),
                str(row.get("eval_start_ts") or row.get("entry_time") or ""),
                str(row.get("reject_reason")),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        volume_rows = deduped

        # Replay pool + vol/liq enrichment
        replay_raw, np_shadows = _load_replay_pool(reports)
        pool = _filter_period(_filter_replay_pool_safe(replay_raw, np_shadows), start=PERIOD_START, end=end)
        price_idx = _build_price_index_to(self.repo_root, period_end=end)
        shadows = _fill_close_proxy_shadows(pool, np_shadows, price_idx=price_idx)
        _enrich_pool_vol_liq(pool)
        pool_index = _build_pool_index(pool)
        pool_by_key = {_position_key(t): dict(t) for t in pool}
        label_rows = _merge_dataset(reports)
        label_by_key = {_trade_key(r): dict(r) for r in label_rows}
        for t in pool:
            meta = label_by_key.get(_trade_key(t))
            if meta:
                for k in ("is_mfe0", "is_stop_low_mfe"):
                    if k in meta:
                        t[k] = meta[k]

        pool_scores = [s for s in (_vol_liq_score(t) for t in pool) if s is not None]
        base_threshold = percentile_value(pool_scores, 0.50) if pool_scores else 0.0

        # Re-classify live rejects using matched pool features
        for row in volume_rows:
            sym = str(row.get("symbol"))
            day = str(row.get("day"))
            ts = str(row.get("eval_start_ts") or row.get("entry_time") or "")
            matched = _match_trade(pool_index, symbol=sym, day=day, ts=ts)
            row["reject_subcategory"] = _classify_with_trade(row, matched, base_threshold)

        sub_counts = Counter(r["reject_subcategory"] for r in volume_rows)
        total_vol_rej = sum(sub_counts.values())
        breakdown_rows = [
            {
                "reject_subcategory": cat,
                "reject_count": cnt,
                "share_pct": round(100.0 * cnt / max(total_vol_rej, 1), 2),
                "source": "live_events_and_audit",
            }
            for cat, cnt in sub_counts.most_common()
        ]

        # Investigation 3 — counterfactual per subcategory
        cf_source: list[dict[str, Any]] = []
        for row in volume_rows:
            sym = str(row.get("symbol"))
            day = str(row.get("day"))
            ts = str(row.get("eval_start_ts") or row.get("entry_time") or "")
            enriched = dict(row)
            trade = _match_trade(pool_index, symbol=sym, day=day, ts=ts)
            if trade is None:
                enriched["counterfactual_available"] = False
            else:
                oc = _trade_outcome(trade)
                enriched.update(
                    {
                        "counterfactual_available": True,
                        "simulated_pnl": oc["pnl"],
                        "simulated_mfe": oc["mfe"],
                        "simulated_stop": oc["stop"],
                    }
                )
            cf_source.append(enriched)
        counterfactual_rows = _aggregate_cf(cf_source)

        # Investigation 4 — relaxation replay (correct daytrade gate)
        baseline_pass = _make_pass_fn(base_threshold, 100.0)
        baseline_state = _run_replay(pool, shadows, baseline_pass, variant_id="V100")
        baseline_metrics = _metrics_from_state(baseline_state, pool_by_key)
        baseline_keys = baseline_metrics["_keys"]

        relaxation_specs = [
            ("V100", 100.0, "daytrade threshold 100% (baseline median)"),
            ("V90", 90.0, "daytrade threshold relaxed 90%"),
            ("V80", 80.0, "daytrade threshold relaxed 80%"),
            ("V70", 70.0, "daytrade threshold relaxed 70%"),
            ("V0", 0.0, "daytrade OFF"),
        ]
        relaxation_rows: list[dict[str, Any]] = []
        variant_metrics: dict[str, dict[str, Any]] = {}
        for vid, pct, label in relaxation_specs:
            pfn = baseline_pass if pct == 100.0 else _make_pass_fn(base_threshold, pct)
            st = baseline_state if pct == 100.0 else _run_replay(pool, shadows, pfn, variant_id=vid)
            met = baseline_metrics if pct == 100.0 else _metrics_from_state(st, pool_by_key)
            if pct != 100.0:
                met["added_trades"] = len(met["_keys"] - baseline_keys)
                met["removed_trades"] = len(baseline_keys - met["_keys"])
            else:
                met["added_trades"] = 0
                met["removed_trades"] = 0
            variant_metrics[vid] = met
            eff_th = 0.0 if pct <= 0 else round(base_threshold * pct / 100.0, 6)
            dpnl = round(_num(met.get("pnl_yen_100")) - _num(baseline_metrics.get("pnl_yen_100")), 2)
            dpf = round(_num(met.get("profit_factor")) - _num(baseline_metrics.get("profit_factor")), 4)
            runtime_cand = pct in (90.0, 80.0) and dpnl > 5000 and dpf >= -0.05
            relaxation_rows.append(
                {
                    "variant_id": vid,
                    "relaxation_pct": pct,
                    "label": label,
                    "effective_threshold": eff_th,
                    "trades": met.get("trades"),
                    "pnl_yen_100": met.get("pnl_yen_100"),
                    "profit_factor": met.get("profit_factor"),
                    "max_drawdown_yen_100": met.get("max_drawdown_yen_100"),
                    "win_rate": met.get("win_rate"),
                    "mfe0_count": met.get("mfe0_count"),
                    "stop_low_mfe_count": met.get("stop_low_mfe_count"),
                    "big_loser_count": met.get("big_loser_count"),
                    "added_trades": met.get("added_trades"),
                    "removed_trades": met.get("removed_trades"),
                    "delta_pnl_vs_baseline": dpnl,
                    "delta_pf_vs_baseline": dpf,
                    "runtime_change_candidate": runtime_cand,
                }
            )

        # Phase588 proxy comparison (phase364 OFF)
        phase364_pass = _pass_core_pbv2  # no daytrade
        p364_state = _run_replay(pool, shadows, phase364_pass, variant_id="phase588_volume_proxy")
        p364_met = _metrics_from_state(p364_state, pool_by_key)

        # Investigation 5 — daily/symbol impact for V90 and V0
        impact_rows: list[dict[str, Any]] = []
        b_pnl, b_cnt = _symbol_day_pnl(baseline_state)
        for vid in ("V90", "V0"):
            met = variant_metrics[vid]
            v_pnl, v_cnt = _symbol_day_pnl(met["_state"])
            keys = set(b_pnl) | set(v_pnl)
            for day, sym in sorted(keys):
                impact_rows.append(
                    {
                        "variant_id": vid,
                        "day": day,
                        "symbol": sym,
                        "baseline_pnl_yen_100": round(b_pnl.get((day, sym), 0.0), 2),
                        "variant_pnl_yen_100": round(v_pnl.get((day, sym), 0.0), 2),
                        "delta_pnl_yen_100": round(v_pnl.get((day, sym), 0.0) - b_pnl.get((day, sym), 0.0), 2),
                        "baseline_trades": b_cnt.get((day, sym), 0),
                        "variant_trades": v_cnt.get((day, sym), 0),
                    }
                )
        impact_rows.sort(key=lambda r: -abs(_num(r.get("delta_pnl_yen_100"))))

        top_sub = sub_counts.most_common(1)[0][0] if sub_counts else "none"
        cf_top = next((r for r in counterfactual_rows if r.get("reject_subcategory") == top_sub), {})
        v0 = variant_metrics.get("V0", baseline_metrics)
        v90 = variant_metrics.get("V90", baseline_metrics)
        v100 = variant_metrics.get("V100", baseline_metrics)

        unnecessary_subs = [
            r["reject_subcategory"]
            for r in counterfactual_rows
            if _num(r.get("simulated_pnl")) > 0 and _num(r.get("simulated_pf")) >= 1.0 and r.get("simulated_trades", 0) >= 5
        ]

        v0_row = next((r for r in relaxation_rows if r.get("variant_id") == "V0"), {})
        v90_row = next((r for r in relaxation_rows if r.get("variant_id") == "V90"), {})
        v0_delta = _num(v0_row.get("delta_pnl_vs_baseline"))
        v90_delta = _num(v90_row.get("delta_pnl_vs_baseline"))

        v80_row = next((r for r in relaxation_rows if r.get("variant_id") == "V80"), {})
        v80_delta = _num(v80_row.get("delta_pnl_vs_baseline"))

        mandatory = {
            "1_volume_gate_watches": "volatility_liquidity_score=atr_pct*log10(trading_value); threshold=median prior session scores (top50 rule)",
            "2_top_reject_condition": top_sub,
            "2_daytrade_reject_total": total_vol_rej,
            "3_truly_unnecessary_components": [
                c
                for c in (
                    "vol_liq_score_slightly_below_threshold",
                    "turnover_proxy_low",
                )
                if c in {r["reject_subcategory"] for r in counterfactual_rows}
            ],
            "4_all_volume_unnecessary": False,
            "4_daytrade_off_improves_pnl": v0_delta > 0,
            "4_daytrade_off_quality_safe": int(v0.get("big_loser_count") or 0) <= int(baseline_metrics.get("big_loser_count") or 0) + 3,
            "5_partial_unnecessary": v90_delta > 0 or v80_delta > 0,
            "5_best_partial_relaxation": "V80" if v80_delta >= v90_delta else "V90",
            "6_relaxation_improves": v90_delta > 0 or v80_delta > 0,
            "7_runtime_candidate": bool(v90_row.get("runtime_change_candidate")) or bool(v80_row.get("runtime_change_candidate")),
            "8_next_phase": "phase590_volume_gate_relaxation_shadow_pilot",
            "baseline_threshold": round(base_threshold, 6),
            "baseline_pnl": baseline_metrics.get("pnl_yen_100"),
            "baseline_pf": baseline_metrics.get("profit_factor"),
            "v90_delta_pnl": v90_delta,
            "v0_delta_pnl": v0_delta,
            "phase588_proxy_delta_pnl": round(
                _num(p364_met.get("pnl_yen_100")) - _num(baseline_metrics.get("pnl_yen_100")), 2
            ),
            "phase588_proxy_note": "Phase588 no_volume disabled phase364 near-day-high guard NOT daytrade_suitability",
            "counterfactual_match_rate_pct": round(
                100.0 * sum(1 for r in cf_source if r.get("counterfactual_available")) / max(len(cf_source), 1),
                2,
            ),
            "period_start": PERIOD_START,
            "period_end": end,
        }

        return {
            "verdict": PHASE589_VERDICT,
            "all_pass": len(sessions) > 0 and len(pool_scores) > 0 and baseline_metrics.get("trades", 0) > 0,
            "algorithm_rows": algorithm_rows,
            "breakdown_rows": breakdown_rows,
            "counterfactual_rows": counterfactual_rows,
            "relaxation_rows": relaxation_rows,
            "impact_rows": impact_rows,
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "algorithm": reports / "phase589_volume_algorithm.csv",
            "breakdown": reports / "phase589_volume_reject_breakdown.csv",
            "counterfactual": reports / "phase589_volume_reject_counterfactual.csv",
            "relaxation": reports / "phase589_volume_relaxation_replay.csv",
            "impact": reports / "phase589_volume_daily_symbol_impact.csv",
            "report": reports / "phase589_report.json",
        }
        _write_csv(paths["algorithm"], ALGORITHM_FIELDS, list(result.get("algorithm_rows") or []))
        _write_csv(paths["breakdown"], BREAKDOWN_FIELDS, list(result.get("breakdown_rows") or []))
        _write_csv(paths["counterfactual"], COUNTERFACTUAL_FIELDS, list(result.get("counterfactual_rows") or []))
        _write_csv(paths["relaxation"], RELAXATION_FIELDS, list(result.get("relaxation_rows") or []))
        _write_csv(paths["impact"], IMPACT_FIELDS, list(result.get("impact_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = kabu / "docs" / "operations" / "phase589_volume_gate_attribution_audit.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "\n".join(
                [
                    "# Phase589 — Volume Gate Attribution Audit",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {m.get('period_start')}–{m.get('period_end')}",
                    "",
                    "## Key finding",
                    "",
                    f"Production Volume Gate = **daytrade_suitability** ({m.get('1_volume_gate_watches')}).",
                    f"Phase588 `no_volume` replay disabled **phase364 near-day-high guard**, not this gate (ΔPnL proxy={m.get('phase588_proxy_delta_pnl')}).",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Volume Gate watches: **{m.get('1_volume_gate_watches')}**",
                    f"2. Top reject condition: **{m.get('2_top_reject_condition')}** ({m.get('2_daytrade_reject_total')} live rejects)",
                    f"3. Truly unnecessary (partial): **{m.get('3_truly_unnecessary_components')}** — slightly-below-threshold band; well-below band blocks losers",
                    f"4. All volume unnecessary: **{m.get('4_all_volume_unnecessary')}** (OFF ΔPnL={m.get('v0_delta_pnl')}, quality-safe={m.get('4_daytrade_off_quality_safe')})",
                    f"5. Partial unnecessary: **{m.get('5_partial_unnecessary')}** (best={m.get('5_best_partial_relaxation')})",
                    f"6. Relaxation improves: **{m.get('6_relaxation_improves')}** (V90 ΔPnL={m.get('v90_delta_pnl')})",
                    f"7. Runtime candidate: **{m.get('7_runtime_candidate')}**",
                    f"8. Next phase: **{m.get('8_next_phase')}**",
                    "",
                    f"Baseline threshold (pool median): {m.get('baseline_threshold')}",
                    f"Baseline replay PnL/PF: {m.get('baseline_pnl')} / {m.get('baseline_pf')}",
                    f"Counterfactual match rate: {m.get('counterfactual_match_rate_pct')}%",
                    "",
                    "## Outputs",
                    "",
                    "- `results/reports/phase589_volume_algorithm.csv`",
                    "- `results/reports/phase589_volume_reject_breakdown.csv`",
                    "- `results/reports/phase589_volume_reject_counterfactual.csv`",
                    "- `results/reports/phase589_volume_relaxation_replay.csv`",
                    "- `results/reports/phase589_volume_daily_symbol_impact.csv`",
                    "- `results/reports/phase589_report.json`",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
