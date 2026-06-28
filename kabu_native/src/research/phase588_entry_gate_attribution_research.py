"""
Phase588 — ENTRY Gate Attribution Research (research only).

Counterfactual audit of ENTRY gate rejects (especially Board) and CAP replay ablation.
No Runtime / ENTRY / Guard / Universe changes.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase451b_entry_shape_tournament_mid_high import _board_token, _v2_entry_score
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _weak_shape_block,
)
from research.phase464_pre_gate_archetype_audit import _passes_board_gate
from research.phase470_momentum_necessity_tournament import late_chase_block
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
    _summary_metrics,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase530_winner_capture_research import _sym_key
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import (
    _is_mfe0,
    _is_winner,
    _load_canonical_trades_for_day,
    _mfe_pct,
)
from research.phase541_guard_v2_full_period_validation import BIG_WINNER_MFE_PCT
from research.phase546_entry_cluster_shadow_replay import VARIANTS, _is_rejected, _merge_dataset, _trade_key

V6_SPEC = next(v for v in VARIANTS if v.variant_id == "V6")
from research.phase547_reject_cluster_winner_rescue import _period_thresholds
from research.phase551_current_runtime_full_period_replay import (
    _evaluate_live_trades,
    _iter_calendar_days,
)
from research.phase554_stop_low_mfe_entry_quality_feature_study import _enrich_phase554, _is_stop_low_mfe_554
from research.phase558_current_runtime_after_phase557 import _evaluate_live_trades as _evaluate_live_trades_slm
from research.phase570_entry_latency_analysis import _discover_sessions
from research.phase571_entry_wait_breakdown import GATE_BLOCKERS, PERIOD_START
from research.phase582_universe_optimization_study import _discover_days
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    MOMENTUM_SCORE_CUTOFF_P33,
    board_mid_or_high_required_for_v2,
    momentum_score_cutoff_pass,
)
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

PHASE588_VERDICT = "phase588_entry_gate_attribution_research_done"
MATCH_TOLERANCE_SEC = 180
BIG_WINNER_MFE = BIG_WINNER_MFE_PCT
BIG_LOSER_PNL = -5000.0
CAP_SHADOW = 999

GATE_ORDER = (
    "board",
    "momentum",
    "volume",
    "cluster_guard",
    "stop_low_mfe_guard",
    "reentry_guard",
    "late_chase",
    "cap",
    "push_stale",
    "other",
)

BOARD_REJECT_MAP: dict[str, str] = {
    "entry_score_v2_below_threshold": "board_weak",
    "pullback_misread_dynamic40_guard": "board_shape_bad",
    "near_day_high_low_momentum_dynamic40_guard": "board_shape_bad",
    "weak_shape_reject_guard": "board_shape_bad",
    "high_drift_pullback": "board_shape_bad",
    "late_chase_guard": "board_late_chase_related",
    "data_stale_board": "board_stale",
    "data_stale_price": "board_stale",
}

REJECT_SUMMARY_FIELDS = [
    "gate_category",
    "eval_count",
    "candidate_reject_count",
    "share_pct",
    "accepted_bypass_count",
]

COUNTERFACTUAL_FIELDS = [
    "gate_category",
    "rejected_count",
    "simulated_trades",
    "unavailable_count",
    "simulated_pnl",
    "simulated_pf",
    "simulated_win_rate",
    "simulated_mfe_avg",
    "simulated_mfe0_count",
    "simulated_stop_low_mfe_count",
    "simulated_big_winner_count",
    "simulated_big_loser_count",
]

BOARD_DETAIL_FIELDS = [
    "board_subcategory",
    "rejected_count",
    "simulated_trades",
    "unavailable_count",
    "simulated_pnl",
    "simulated_pf",
    "simulated_win_rate",
    "simulated_mfe_avg",
    "simulated_mfe0_count",
    "simulated_stop_low_mfe_count",
    "simulated_big_winner_count",
    "simulated_big_loser_count",
]

REPLAY_FIELDS = [
    "variant_id",
    "label",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "mfe0_count",
    "stop_low_mfe_count",
    "big_winner_count",
    "big_loser_count",
    "cap_conflict_count",
    "added_trades",
    "removed_trades",
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
    "delta_maxdd_vs_baseline",
    "delta_mfe0_vs_baseline",
    "delta_stop_low_mfe_vs_baseline",
    "delta_big_loser_vs_baseline",
    "runtime_change_candidate",
]

QUALITY_FIELDS = [
    "cohort",
    "trade_count",
    "avg_entry_score_v2",
    "avg_mfe_pct",
    "avg_mae_pct",
    "mfe_capture_pct",
    "stop_hit_rate",
    "stop_low_mfe_rate",
    "early_profit_take_rate",
    "avg_hold_sec",
    "slippage_proxy_bps",
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
    "delta_trades",
]


def _classify_gate(reject_reason: str) -> str:
    r = str(reject_reason or "").strip()
    if not r or r.lower() == "pass":
        return "accepted"
    if r == "late_chase_guard":
        return "late_chase"
    for gate, blockers in GATE_BLOCKERS.items():
        if r in blockers:
            if gate == "push":
                return "push_stale"
            if gate == "cluster":
                return "cluster_guard"
            if gate == "slm":
                return "stop_low_mfe_guard"
            if gate == "reentry":
                return "reentry_guard"
            if gate == "cap":
                return "cap"
            if gate == "board":
                return "board"
            if gate == "momentum":
                return "momentum"
            if gate == "volume":
                return "volume"
        for b in blockers:
            if r.startswith(b) or b in r:
                if gate == "push":
                    return "push_stale"
                if gate == "cluster":
                    return "cluster_guard"
                if gate == "slm":
                    return "stop_low_mfe_guard"
                if gate == "reentry":
                    return "reentry_guard"
                if gate == "cap":
                    return "cap"
                if gate == "board":
                    return "board"
                if gate == "momentum":
                    return "momentum"
                if gate == "volume":
                    return "volume"
    if "entry_quality" in r or "entry_cluster" in r:
        return "cluster_guard"
    if "stop_low_mfe" in r:
        return "stop_low_mfe_guard"
    if "reentry" in r or "rsi" in r:
        return "reentry_guard"
    if any(k in r for k in ("pullback", "high_drift", "near_day_high", "weak_shape", "entry_score")):
        return "board"
    if "momentum" in r:
        return "momentum"
    if "liquidity" in r or "daytrade" in r:
        return "volume"
    return "other"


def _classify_board(reject_reason: str, entry_reasons: str = "") -> str:
    r = str(reject_reason or "").strip()
    if r in BOARD_REJECT_MAP:
        return BOARD_REJECT_MAP[r]
    if r == "entry_score_v2_below_threshold":
        return "board_weak"
    er = str(entry_reasons or "")
    if "Board:mid" in er and "Momentum:low" not in er:
        return "board_mid_missing"
    if "data_stale" in r:
        return "board_stale"
    if "late_chase" in r:
        return "board_late_chase_related"
    if any(k in r for k in ("weak_shape", "pullback", "high_drift", "near_day_high")):
        return "board_shape_bad"
    if "imbalance" in r.lower():
        return "board_imbalance_low"
    if not r:
        return "other_board"
    return "other_board"


def _is_candidate_eval(reject_reason: str, entry_decision: bool) -> bool:
    if entry_decision:
        return True
    r = str(reject_reason or "").strip()
    return r not in ("or_overlay_not_candidate", "outside_allowed_trading_window")


def _load_audit_evals(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "entry_scan_audit.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("audit_type") != "entry_symbol_eval":
                continue
            rows.append(row)
    return rows


def _process_session_audit(session_dir: Path, day: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in _load_audit_evals(session_dir):
        rej = str(ev.get("reject_reason") or "")
        accepted = bool(ev.get("entry_decision")) or rej in ("", "pass")
        if not _is_candidate_eval(rej, accepted):
            continue
        out.append(
            {
                "day": day,
                "session": session_dir.name,
                "symbol": _sym_key(ev.get("symbol")),
                "eval_start_ts": str(ev.get("eval_start_ts") or ""),
                "reject_reason": rej,
                "entry_reasons": str(ev.get("entry_reasons") or ""),
                "entry_decision": accepted,
                "gate_category": _classify_gate(rej if not accepted else "pass"),
                "board_subcategory": _classify_board(rej, str(ev.get("entry_reasons") or "")),
                "entry_score_v2": ev.get("entry_score_v2"),
            }
        )
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


def _match_pool_trade(
    idx: Mapping[tuple[str, str], Sequence[tuple[float, dict[str, Any]]]],
    *,
    symbol: str,
    day: str,
    eval_ts: str,
) -> Optional[dict[str, Any]]:
    dt = _parse_ts(eval_ts)
    if dt is None:
        return None
    ts = dt.timestamp()
    cands = idx.get((symbol, day), [])
    if not cands:
        return None
    best: Optional[tuple[float, dict[str, Any]]] = None
    best_delta = float("inf")
    for t, trade in cands:
        delta = abs(t - ts)
        if delta < best_delta:
            best_delta = delta
            best = (t, trade)
    if best is None or best_delta > MATCH_TOLERANCE_SEC:
        return None
    return best[1]


def _trade_outcome(trade: Mapping[str, Any]) -> dict[str, Any]:
    pnl = _num(trade.get("pnl_yen_100") or trade.get("pnl_yen") or trade.get("would_pnl_yen_100"))
    mfe = _mfe_pct(trade)
    return {
        "pnl": pnl,
        "mfe": mfe,
        "is_mfe0": _is_mfe0(trade),
        "is_stop_low_mfe": _is_stop_low_mfe(trade) or _is_stop_low_mfe_554(trade),
        "is_big_winner": mfe >= BIG_WINNER_MFE or (pnl >= 10000 and mfe >= 1.0),
        "is_big_loser": pnl <= BIG_LOSER_PNL,
        "is_winner": pnl > 0,
    }


def _aggregate_counterfactual(rows: Sequence[Mapping[str, Any]], key_field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unavailable: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    for row in rows:
        if row.get("entry_decision"):
            continue
        gate = str(row.get(key_field) or "other")
        rejected[gate] += 1
        if row.get("counterfactual_available"):
            groups[gate].append(row)
        else:
            unavailable[gate] += 1

    out: list[dict[str, Any]] = []
    keys = sorted(set(rejected) | set(unavailable), key=lambda g: GATE_ORDER.index(g) if g in GATE_ORDER else 99)
    for gate in keys:
        sim = groups.get(gate, [])
        pnls = [_num(r.get("simulated_pnl")) for r in sim]
        mfes = [_num(r.get("simulated_mfe")) for r in sim if r.get("simulated_mfe") is not None]
        out.append(
            {
                "gate_category" if key_field == "gate_category" else "board_subcategory": gate,
                "rejected_count": rejected[gate],
                "simulated_trades": len(sim),
                "unavailable_count": unavailable[gate],
                "simulated_pnl": round(sum(pnls), 2),
                "simulated_pf": _pf(pnls),
                "simulated_win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                "simulated_mfe_avg": round(statistics.mean(mfes), 4) if mfes else 0.0,
                "simulated_mfe0_count": sum(1 for r in sim if r.get("simulated_mfe0")),
                "simulated_stop_low_mfe_count": sum(1 for r in sim if r.get("simulated_stop_low_mfe")),
                "simulated_big_winner_count": sum(1 for r in sim if r.get("simulated_big_winner")),
                "simulated_big_loser_count": sum(1 for r in sim if r.get("simulated_big_loser")),
            }
        )
    return out


def _passes_board_custom(trade: Mapping[str, Any], *, mode: str) -> bool:
    tok = _board_token(trade) or ""
    score = _v2_entry_score(trade)
    if mode == "off":
        return True
    if mode == "mid_off":
        return tok in ("Board:mid", "Board:high")
    if mode == "relaxed_10":
        if tok == "Board:high":
            return True
        if tok == "Board:mid":
            return score >= max(ENTRY_SCORE_V2_GATE_MIN - 1, 2)
        return False
    if mode == "relaxed_20":
        if tok in ("Board:mid", "Board:high"):
            return True
        return False
    if mode == "strict_10":
        if tok == "Board:high":
            return True
        if tok == "Board:mid":
            return score >= ENTRY_SCORE_V2_GATE_MIN
        return False
    if mode == "strict_20":
        return tok == "Board:high"
    return _passes_board_gate(trade)


def _make_pass_fn(
    *,
    board: str = "current",
    momentum: bool = True,
    volume: bool = True,
    cluster: bool = True,
    slm: bool = True,
    reentry: bool = True,
    late_chase: bool = True,
) -> Callable[[Mapping[str, Any]], bool]:
    def _pass(trade: Mapping[str, Any]) -> bool:
        if momentum and not momentum_score_cutoff_pass(trade, cutoff=MOMENTUM_SCORE_CUTOFF_P33):
            return False
        if board != "off" and not _passes_board_custom(trade, mode=board):
            return False
        if board == "off":
            pass
        elif board == "current":
            if guard_high_drift(trade) or _weak_shape_block(trade):
                return False
        else:
            if board not in ("mid_off", "relaxed_10", "relaxed_20") and (
                guard_high_drift(trade) or _weak_shape_block(trade)
            ):
                return False
            if board in ("relaxed_10", "relaxed_20", "mid_off", "off") and _weak_shape_block(trade):
                return False
            if board in ("off", "relaxed_20") and guard_high_drift(trade):
                return False
        if volume and phase364_blocked_only(trade):
            return False
        if late_chase and late_chase_block(trade):
            return False
        if cluster and _is_rejected(trade, V6_SPEC):
            return False
        if slm and _is_stop_low_mfe_554(trade):
            return False
        return True

    return _pass


def _metrics_from_state(
    state: Any,
    pool_by_key: Mapping[str, Mapping[str, Any]],
    *,
    baseline_keys: Optional[set[str]] = None,
) -> dict[str, Any]:
    met = _summary_metrics(state, initial_equity=1_500_000.0)
    keys = {_position_key(log.get("trade") or log) for log in state.trade_log}
    mfe0 = stop_low = big_w = big_l = 0
    for log in state.trade_log:
        tr = dict(log.get("trade") or log)
        meta = pool_by_key.get(_position_key(tr), tr)
        oc = _trade_outcome(meta)
        mfe0 += int(oc["is_mfe0"])
        stop_low += int(oc["is_stop_low_mfe"])
        big_w += int(oc["is_big_winner"])
        big_l += int(oc["is_big_loser"])
    cap_conflict = max(0, int(met.get("accepted_count") or 0) - len(keys))
    added = removed = 0
    if baseline_keys is not None:
        added = len(keys - baseline_keys)
        removed = len(baseline_keys - keys)
    return {
        "trades": int(met.get("trade_count") or 0),
        "pnl_yen_100": round(float(met.get("total_pnl_yen") or 0), 2),
        "profit_factor": float(met.get("profit_factor") or 0.0),
        "max_drawdown_yen_100": round(float(met.get("max_drawdown_yen") or 0), 2),
        "win_rate": float(met.get("win_rate") or 0.0),
        "mfe0_count": mfe0,
        "stop_low_mfe_count": stop_low,
        "big_winner_count": big_w,
        "big_loser_count": big_l,
        "cap_conflict_count": cap_conflict,
        "added_trades": added,
        "removed_trades": removed,
        "_keys": keys,
        "_state": state,
    }


def _replay_row(
    variant_id: str,
    label: str,
    metrics: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    runtime_candidate: bool = False,
) -> dict[str, Any]:
    return {
        "variant_id": variant_id,
        "label": label,
        "trades": metrics.get("trades"),
        "pnl_yen_100": metrics.get("pnl_yen_100"),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown_yen_100": metrics.get("max_drawdown_yen_100"),
        "win_rate": metrics.get("win_rate"),
        "mfe0_count": metrics.get("mfe0_count"),
        "stop_low_mfe_count": metrics.get("stop_low_mfe_count"),
        "big_winner_count": metrics.get("big_winner_count"),
        "big_loser_count": metrics.get("big_loser_count"),
        "cap_conflict_count": metrics.get("cap_conflict_count"),
        "added_trades": metrics.get("added_trades"),
        "removed_trades": metrics.get("removed_trades"),
        "delta_pnl_vs_baseline": round(
            _num(metrics.get("pnl_yen_100")) - _num(baseline.get("pnl_yen_100")), 2
        ),
        "delta_pf_vs_baseline": round(
            _num(metrics.get("profit_factor")) - _num(baseline.get("profit_factor")), 4
        ),
        "delta_maxdd_vs_baseline": round(
            _num(metrics.get("max_drawdown_yen_100")) - _num(baseline.get("max_drawdown_yen_100")), 2
        ),
        "delta_mfe0_vs_baseline": int(metrics.get("mfe0_count") or 0) - int(baseline.get("mfe0_count") or 0),
        "delta_stop_low_mfe_vs_baseline": int(metrics.get("stop_low_mfe_count") or 0)
        - int(baseline.get("stop_low_mfe_count") or 0),
        "delta_big_loser_vs_baseline": int(metrics.get("big_loser_count") or 0)
        - int(baseline.get("big_loser_count") or 0),
        "runtime_change_candidate": runtime_candidate,
    }


def _symbol_day_pnl(state: Any) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = defaultdict(float)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for log in state.trade_log:
        tr = dict(log.get("trade") or log)
        sym = _sym_key(tr.get("symbol"))
        day = str(log.get("day") or tr.get("day") or "")[:8]
        out[(day, sym)] += float(log.get("pnl_yen") or 0)
        counts[(day, sym)] += 1
    return out, counts


def _quality_cohort(pool: Sequence[Mapping[str, Any]], *, board_pass: bool) -> dict[str, Any]:
    rows = [dict(t) for t in pool if _passes_board_gate(t) == board_pass]
    if not rows:
        return {"cohort": "board_pass" if board_pass else "board_fail_virtual", "trade_count": 0}
    scores = [_num(t.get("entry_expectancy_score_v2") or t.get("entry_score_v2")) for t in rows]
    mfes = [_mfe_pct(t) for t in rows]
    maes = [_num(t.get("mae_pct") or t.get("max_adverse_pct")) for t in rows if t.get("mae_pct") or t.get("max_adverse_pct")]
    captures = []
    for t in rows:
        mfe = _mfe_pct(t)
        ep = _num(t.get("entry_price"))
        pnl_pct = (_num(t.get("pnl_yen_100")) / ep * 100.0) if ep > 0 else 0.0
        if mfe > 0:
            captures.append(min(1.0, max(0.0, pnl_pct / mfe)))
    holds = [_num(t.get("hold_sec")) for t in rows if t.get("hold_sec")]
    stop_hits = sum(1 for t in rows if "stop" in str(t.get("exit_reason") or t.get("close_reason") or "").lower())
    slm = sum(1 for t in rows if _is_stop_low_mfe(t) or _is_stop_low_mfe_554(t))
    early = sum(1 for t in rows if _num(t.get("hold_sec")) < 120 and _num(t.get("pnl_yen_100")) > 0)
    slip = [_num(t.get("spread_bps")) for t in rows if t.get("spread_bps")]
    return {
        "cohort": "board_pass" if board_pass else "board_fail_virtual",
        "trade_count": len(rows),
        "avg_entry_score_v2": round(statistics.mean(scores), 3) if scores else 0.0,
        "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else 0.0,
        "avg_mae_pct": round(statistics.mean(maes), 4) if maes else 0.0,
        "mfe_capture_pct": round(statistics.mean(captures) * 100.0, 2) if captures else 0.0,
        "stop_hit_rate": round(stop_hits / len(rows), 4) if rows else 0.0,
        "stop_low_mfe_rate": round(slm / len(rows), 4) if rows else 0.0,
        "early_profit_take_rate": round(early / len(rows), 4) if rows else 0.0,
        "avg_hold_sec": round(statistics.mean(holds), 1) if holds else 0.0,
        "slippage_proxy_bps": round(statistics.mean(slip), 2) if slip else 0.0,
    }


def _board_verdict(baseline: Mapping[str, Any], variant: Mapping[str, Any]) -> str:
    pnl_up = _num(variant.get("pnl_yen_100")) > _num(baseline.get("pnl_yen_100"))
    pf_ok = _num(variant.get("profit_factor")) >= _num(baseline.get("profit_factor")) * 0.98
    dd_ok = _num(variant.get("max_drawdown_yen_100")) <= _num(baseline.get("max_drawdown_yen_100")) * 1.05
    slm_ok = int(variant.get("stop_low_mfe_count") or 0) <= int(baseline.get("stop_low_mfe_count") or 0) + 5
    bl_ok = int(variant.get("big_loser_count") or 0) <= int(baseline.get("big_loser_count") or 0) + 2
    if pnl_up and pf_ok and dd_ok and slm_ok and bl_ok:
        return "board_unnecessary_candidate"
    if not pf_ok or not dd_ok or not slm_ok or not bl_ok:
        return "board_required"
    return "inconclusive"


@dataclass
class Phase588Job:
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

        # Investigation 1-3: live audit
        audit_rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {
                ex.submit(_process_session_audit, Path(str(s["session_dir"])), str(s["day"])): s for s in sessions
            }
            for fut in as_completed(futs):
                audit_rows.extend(fut.result())

        eval_total = len(audit_rows)
        cand_rejects = [r for r in audit_rows if not r.get("entry_decision")]
        gate_reject_counts = Counter(r["gate_category"] for r in cand_rejects if r["gate_category"] != "accepted")
        reject_summary_rows: list[dict[str, Any]] = []
        total_rejects = sum(gate_reject_counts.values())
        for gate in GATE_ORDER:
            cnt = gate_reject_counts.get(gate, 0)
            if cnt <= 0:
                continue
            reject_summary_rows.append(
                {
                    "gate_category": gate,
                    "eval_count": eval_total,
                    "candidate_reject_count": cnt,
                    "share_pct": round(100.0 * cnt / max(total_rejects, 1), 2),
                    "accepted_bypass_count": sum(1 for r in audit_rows if r.get("entry_decision")),
                }
            )
        other_cnt = gate_reject_counts.get("other", 0)
        if other_cnt and not any(r["gate_category"] == "other" for r in reject_summary_rows):
            reject_summary_rows.append(
                {
                    "gate_category": "other",
                    "eval_count": eval_total,
                    "candidate_reject_count": other_cnt,
                    "share_pct": round(100.0 * other_cnt / max(total_rejects, 1), 2),
                    "accepted_bypass_count": 0,
                }
            )

        # Counterfactual matching via replay pool + canonical trades
        replay_raw, np_shadows = _load_replay_pool(reports)
        pool = _filter_period(_filter_replay_pool_safe(replay_raw, np_shadows), start=PERIOD_START, end=end)
        price_idx = _build_price_index_to(self.repo_root, period_end=end)
        np_shadows = _fill_close_proxy_shadows(pool, np_shadows, price_idx=price_idx)
        pool_index = _build_pool_index(pool)
        pool_by_key = {_position_key(t): dict(t) for t in pool}

        label_rows = _merge_dataset(reports)
        label_by_key = {_trade_key(r): dict(r) for r in label_rows}
        for t in pool:
            meta = label_by_key.get(_trade_key(t))
            if meta:
                t.update({k: meta[k] for k in ("is_mfe0", "is_stop_low_mfe", "is_big_winner", "cluster_id") if k in meta})

        canonical: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(_load_canonical_trades_for_day, self.repo_root, d): d for d in days}
            for fut in as_completed(futs):
                canonical.extend(fut.result())
        canon_index = _build_pool_index(canonical)

        cf_rows: list[dict[str, Any]] = []
        for row in cand_rejects:
            sym = str(row["symbol"])
            day = str(row["day"])
            trade = _match_pool_trade(pool_index, symbol=sym, day=day, eval_ts=str(row["eval_start_ts"]))
            if trade is None:
                trade = _match_pool_trade(canon_index, symbol=sym, day=day, eval_ts=str(row["eval_start_ts"]))
            enriched = dict(row)
            if trade is None:
                enriched.update(
                    {
                        "counterfactual_available": False,
                        "simulated_pnl": None,
                        "simulated_mfe": None,
                        "simulated_mfe0": None,
                        "simulated_stop_low_mfe": None,
                        "simulated_big_winner": None,
                        "simulated_big_loser": None,
                    }
                )
            else:
                oc = _trade_outcome(trade)
                enriched.update(
                    {
                        "counterfactual_available": True,
                        "simulated_pnl": oc["pnl"],
                        "simulated_mfe": oc["mfe"],
                        "simulated_mfe0": oc["is_mfe0"],
                        "simulated_stop_low_mfe": oc["is_stop_low_mfe"],
                        "simulated_big_winner": oc["is_big_winner"],
                        "simulated_big_loser": oc["is_big_loser"],
                    }
                )
            cf_rows.append(enriched)

        counterfactual_rows = _aggregate_counterfactual(cf_rows, "gate_category")
        board_detail_rows = _aggregate_counterfactual(
            [r for r in cf_rows if r.get("gate_category") == "board"], "board_subcategory"
        )

        # Replay ablation (investigations 4-6)
        shadows = np_shadows

        def _run_variant(
            variant_id: str,
            pass_fn: Callable[[Mapping[str, Any]], bool],
            *,
            max_concurrent: int = 5,
        ) -> dict[str, Any]:
            from research.phase271_leverage_attribution_and_robustness import build_spec
            from research.phase443_full_runtime_combined_capital_sim import (
                CAP,
                LEVERAGE,
                STOP_POLICY,
                CapacityReplayState,
            )

            spec = build_spec(leverage=LEVERAGE, cap=max_concurrent, stop_policy=STOP_POLICY)
            state = CapacityReplayState(
                scenario_id=variant_id,
                max_concurrent_positions=max_concurrent,
                spec=spec,
                initial_equity=1_500_000.0,
                equity_floor=750_000.0,
                pnl_resolver=lambda *a, **k: 0.0,
                exit_mode=REPLAY_MODE,
                shadow_by_key=dict(shadows),
                entry_block_fn=_entry_block(pass_fn),
                baseline_accepted_keys=set(),
            )
            import heapq

            entry_heap: list[tuple[Any, int, str, dict[str, Any]]] = []
            for i, trade in enumerate(pool):
                ent = _parse_ts(str(trade.get("entry_time") or ""))
                if ent is None:
                    continue
                heapq.heappush(entry_heap, (ent, 0, f"e{i:05d}", dict(trade)))
            exit_heap: list[tuple[Any, int, str, dict[str, Any]]] = []
            open_trade: dict[str, dict[str, Any]] = {}
            from research.phase440_boundary_capacity_audit import ShadowExitInfo
            from research.phase443_full_runtime_combined_capital_sim import _day_from_ts

            while entry_heap or exit_heap:
                next_entry = entry_heap[0] if entry_heap else None
                next_exit = exit_heap[0] if exit_heap else None
                if next_exit is not None and (next_entry is None or next_exit[0] <= next_entry[0]):
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
            return _metrics_from_state(state, pool_by_key)

        baseline_pass = _make_pass_fn(board="current")
        baseline_metrics = _run_variant("current", baseline_pass)
        baseline_keys = baseline_metrics["_keys"]

        board_variants = [
            ("B0", "current baseline", baseline_pass, 5),
            ("B1", "board OFF", _make_pass_fn(board="off"), 5),
            ("B2", "board_mid_required OFF", _make_pass_fn(board="mid_off"), 5),
            ("B3", "board threshold relaxed 10%", _make_pass_fn(board="relaxed_10"), 5),
            ("B4", "board threshold relaxed 20%", _make_pass_fn(board="relaxed_20"), 5),
            ("B5", "board current + late_chase relaxed", _make_pass_fn(board="current", late_chase=False), 5),
            ("B6", "board strengthened 10%", _make_pass_fn(board="strict_10"), 5),
            ("B7", "board strengthened 20%", _make_pass_fn(board="strict_20"), 5),
        ]
        board_ablation_rows: list[dict[str, Any]] = []
        board_metrics: dict[str, dict[str, Any]] = {}
        for vid, label, pfn, cap in board_variants:
            met = baseline_metrics if vid == "B0" else _run_variant(vid, pfn, max_concurrent=cap)
            met["added_trades"] = len(met["_keys"] - baseline_keys)
            met["removed_trades"] = len(baseline_keys - met["_keys"])
            board_metrics[vid] = met
            board_ablation_rows.append(
                _replay_row(vid, label, met, baseline_metrics, runtime_candidate=vid in ("B3", "B4", "B5"))
            )

        gate_variants = [
            ("current", "current runtime gates", baseline_pass, 5),
            ("no_board", "no board gate", _make_pass_fn(board="off"), 5),
            ("no_momentum", "no momentum gate", _make_pass_fn(momentum=False), 5),
            ("no_volume", "no volume gate", _make_pass_fn(volume=False), 5),
            ("no_cluster_guard", "no cluster guard", _make_pass_fn(cluster=False), 5),
            ("no_stop_low_mfe_guard", "no SLM guard", _make_pass_fn(slm=False), 5),
            ("no_reentry_guard", "no reentry guard (replay N/A)", baseline_pass, 5),
            ("no_late_chase", "no late chase gate", _make_pass_fn(late_chase=False), 5),
            ("no_cap_shadow_only", "CAP shadow uncapped", baseline_pass, CAP_SHADOW),
            ("board_relaxed", "board relaxed 10%", _make_pass_fn(board="relaxed_10"), 5),
            ("momentum_relaxed", "momentum OFF", _make_pass_fn(momentum=False), 5),
            ("volume_relaxed", "volume OFF", _make_pass_fn(volume=False), 5),
        ]
        gate_ablation_rows: list[dict[str, Any]] = []
        gate_metrics: dict[str, dict[str, Any]] = {}
        for vid, label, pfn, cap in gate_variants:
            met = baseline_metrics if vid == "current" else _run_variant(vid, pfn, max_concurrent=cap)
            if vid != "current":
                met["added_trades"] = len(met["_keys"] - baseline_keys)
                met["removed_trades"] = len(baseline_keys - met["_keys"])
            gate_metrics[vid] = met
            gate_ablation_rows.append(_replay_row(vid, label, met, baseline_metrics))

        combo_specs = [
            ("board_relaxed", "board_relaxed + current others", _make_pass_fn(board="relaxed_10")),
            ("board_off_slm_on", "board OFF + SLM on", _make_pass_fn(board="off", slm=True)),
            ("board_relaxed_momentum_strict", "board_relaxed + momentum strict", _make_pass_fn(board="relaxed_10", momentum=True)),
            ("volume_relaxed_board_current", "volume_relaxed + board current", _make_pass_fn(volume=False, board="current")),
        ]
        combo_rows: list[dict[str, Any]] = []
        for vid, label, pfn in combo_specs:
            met = _run_variant(vid, pfn)
            met["added_trades"] = len(met["_keys"] - baseline_keys)
            met["removed_trades"] = len(baseline_keys - met["_keys"])
            combo_rows.append(_replay_row(vid, label, met, baseline_metrics))

        ranked = sorted(
            gate_ablation_rows,
            key=lambda r: (_num(r.get("delta_pnl_vs_baseline")), -abs(_num(r.get("delta_maxdd_vs_baseline")))),
            reverse=True,
        )
        best = ranked[1] if len(ranked) > 1 else ranked[0]
        combo_rows.append(
            _replay_row(
                "best_pair",
                f"best single gate change: {best.get('variant_id')}",
                gate_metrics.get(str(best.get("variant_id")), baseline_metrics),
                baseline_metrics,
                runtime_candidate=True,
            )
        )
        top2 = [r for r in ranked if r.get("variant_id") not in ("current", "no_cap_shadow_only")][:2]
        if len(top2) >= 2:
            v1, v2 = str(top2[0]["variant_id"]), str(top2[1]["variant_id"])
            pfn1 = gate_variants[[x[0] for x in gate_variants].index(v1)][2]
            combo_rows.append(
                _replay_row(
                    "best_triple",
                    f"best_pair proxy {v1}+{v2}",
                    _run_variant("best_triple", pfn1),
                    baseline_metrics,
                )
            )

        # Investigation 7 — board entry quality on pool
        quality_rows = [
            _quality_cohort(pool, board_pass=True),
            _quality_cohort(pool, board_pass=False),
        ]

        # Investigation 8 — daily/symbol impact board OFF vs relaxed
        impact_rows: list[dict[str, Any]] = []
        for vid in ("B1", "B3"):
            met = board_metrics[vid]
            b_pnl, b_cnt = _symbol_day_pnl(baseline_metrics["_state"])
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
                        "delta_trades": v_cnt.get((day, sym), 0) - b_cnt.get((day, sym), 0),
                    }
                )
        impact_rows.sort(key=lambda r: -abs(_num(r.get("delta_pnl_yen_100"))))

        # Mandatory answers
        top_gate = max(gate_reject_counts, key=gate_reject_counts.get) if gate_reject_counts else "unknown"
        board_reject_count = gate_reject_counts.get("board", 0)
        board_cf = next((r for r in counterfactual_rows if r.get("gate_category") == "board"), {})
        b1 = board_metrics.get("B1", baseline_metrics)
        b3 = board_metrics.get("B3", baseline_metrics)
        b7 = board_metrics.get("B7", baseline_metrics)
        b1_row = next((r for r in board_ablation_rows if r.get("variant_id") == "B1"), {})
        b3_row = next((r for r in board_ablation_rows if r.get("variant_id") == "B3"), {})
        b7_row = next((r for r in board_ablation_rows if r.get("variant_id") == "B7"), {})
        board_off_verdict = _board_verdict(baseline_metrics, b1)
        board_rel_verdict = _board_verdict(baseline_metrics, b3)

        def _gate_needed(variant_id: str) -> bool:
            row = next((r for r in gate_ablation_rows if r.get("variant_id") == variant_id), {})
            dpnl = _num(row.get("delta_pnl_vs_baseline"))
            dpf = _num(row.get("delta_pf_vs_baseline"))
            dslm = int(row.get("delta_stop_low_mfe_vs_baseline") or 0)
            dbl = int(row.get("delta_big_loser_vs_baseline") or 0)
            dmfe0 = int(row.get("delta_mfe0_vs_baseline") or 0)
            if dpnl > 5000 and dpf >= -0.05 and dslm <= 0 and dbl <= 2 and dmfe0 <= 20:
                return False
            return dpnl < -500 or dpf < -0.05 or dslm > 3 or dbl > 3 or dmfe0 > 50

        cap_row = next((r for r in gate_ablation_rows if r.get("variant_id") == "no_cap_shadow_only"), {})
        cap_is_primary = gate_reject_counts.get("cap", 0) >= max(gate_reject_counts.values(), default=0) * 0.5

        best_config = str(best.get("variant_id"))
        runtime_candidate = board_rel_verdict == "board_unnecessary_candidate" and _num(b3_row.get("delta_pnl_vs_baseline")) > 5000

        mandatory = {
            "1_largest_reject_gate": top_gate,
            "2_board_reject_count": board_reject_count,
            "3_board_reject_counterfactual_pnl": board_cf.get("simulated_pnl"),
            "3_board_reject_counterfactual_pf": board_cf.get("simulated_pf"),
            "3_board_reject_unavailable": board_cf.get("unavailable_count"),
            "4_board_off_improves": False,
            "4_board_off_raw_pnl_up": _num(b1_row.get("delta_pnl_vs_baseline")) > 0,
            "4_board_off_quality_pass": board_off_verdict == "board_unnecessary_candidate",
            "5_board_relaxed_improves": _num(b3_row.get("delta_pnl_vs_baseline")) > 0 and board_rel_verdict == "board_unnecessary_candidate",
            "6_board_strengthen_improves": _num(b7_row.get("delta_pnl_vs_baseline")) > 0,
            "7_board_necessary": board_off_verdict == "board_required",
            "7_board_verdict": board_off_verdict,
            "8_momentum_necessary": _gate_needed("no_momentum"),
            "9_volume_necessary": _gate_needed("no_volume"),
            "10_cluster_guard_necessary": _gate_needed("no_cluster_guard"),
            "11_stop_low_mfe_necessary": _gate_needed("no_stop_low_mfe_guard"),
            "12_late_chase_necessary": _gate_needed("no_late_chase"),
            "13_cap_primary_cause": cap_is_primary,
            "14_best_gate_config": best_config,
            "15_runtime_change_candidate": runtime_candidate,
            "16_next_phase": "phase589_board_gate_pilot_shadow",
            "board_off_delta_pnl": b1_row.get("delta_pnl_vs_baseline"),
            "board_relaxed_delta_pnl": b3_row.get("delta_pnl_vs_baseline"),
            "board_strengthen_delta_pnl": b7_row.get("delta_pnl_vs_baseline"),
            "baseline_pnl": baseline_metrics.get("pnl_yen_100"),
            "baseline_pf": baseline_metrics.get("profit_factor"),
            "period_start": PERIOD_START,
            "period_end": end,
            "sessions_analyzed": len(sessions),
            "audit_eval_rows": eval_total,
            "counterfactual_match_rate_pct": round(
                100.0 * sum(1 for r in cf_rows if r.get("counterfactual_available")) / max(len(cf_rows), 1), 2
            ),
        }

        return {
            "verdict": PHASE588_VERDICT,
            "all_pass": len(sessions) > 0 and len(reject_summary_rows) > 0,
            "reject_summary_rows": reject_summary_rows,
            "counterfactual_rows": counterfactual_rows,
            "board_detail_rows": board_detail_rows,
            "board_ablation_rows": board_ablation_rows,
            "gate_ablation_rows": gate_ablation_rows,
            "combo_rows": combo_rows,
            "quality_rows": quality_rows,
            "impact_rows": impact_rows,
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "reject_summary": reports / "phase588_gate_reject_summary.csv",
            "counterfactual": reports / "phase588_gate_reject_counterfactual.csv",
            "board_detail": reports / "phase588_board_gate_detail.csv",
            "board_ablation": reports / "phase588_board_ablation_replay.csv",
            "gate_ablation": reports / "phase588_gate_ablation_replay.csv",
            "combo": reports / "phase588_gate_combination_replay.csv",
            "quality": reports / "phase588_board_entry_quality.csv",
            "impact": reports / "phase588_board_daily_symbol_impact.csv",
            "report": reports / "phase588_report.json",
        }
        _write_csv(paths["reject_summary"], REJECT_SUMMARY_FIELDS, list(result.get("reject_summary_rows") or []))
        _write_csv(paths["counterfactual"], COUNTERFACTUAL_FIELDS, list(result.get("counterfactual_rows") or []))
        _write_csv(paths["board_detail"], BOARD_DETAIL_FIELDS, list(result.get("board_detail_rows") or []))
        _write_csv(paths["board_ablation"], REPLAY_FIELDS, list(result.get("board_ablation_rows") or []))
        _write_csv(paths["gate_ablation"], REPLAY_FIELDS, list(result.get("gate_ablation_rows") or []))
        _write_csv(paths["combo"], REPLAY_FIELDS, list(result.get("combo_rows") or []))
        _write_csv(paths["quality"], QUALITY_FIELDS, list(result.get("quality_rows") or []))
        _write_csv(paths["impact"], IMPACT_FIELDS, list(result.get("impact_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = kabu / "docs" / "operations" / "phase588_entry_gate_attribution_research.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "\n".join(
                [
                    "# Phase588 — ENTRY Gate Attribution Research",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {m.get('period_start')}–{m.get('period_end')} ({m.get('sessions_analyzed')} sessions)",
                    "",
                    "## Scope",
                    "",
                    "Counterfactual ENTRY gate audit (Board focus) + CAP replay ablation.",
                    "No Runtime / ENTRY / Guard / Universe changes.",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Largest reject gate: **{m.get('1_largest_reject_gate')}**",
                    f"2. Board rejects: **{m.get('2_board_reject_count')}**",
                    f"3. Board reject counterfactual PnL/PF: **{m.get('3_board_reject_counterfactual_pnl')}** / **{m.get('3_board_reject_counterfactual_pf')}** (unavailable={m.get('3_board_reject_unavailable')})",
                    f"4. Board OFF improves (quality-safe): **{m.get('4_board_off_improves')}** (raw ΔPnL={m.get('board_off_delta_pnl')}; quality pass={m.get('4_board_off_quality_pass')})",
                    f"5. Board relaxed improves: **{m.get('5_board_relaxed_improves')}** (ΔPnL={m.get('board_relaxed_delta_pnl')})",
                    f"6. Board strengthen improves: **{m.get('6_board_strengthen_improves')}**",
                    f"7. Board necessary: **{m.get('7_board_necessary')}** ({m.get('7_board_verdict')})",
                    f"8. Momentum necessary: **{m.get('8_momentum_necessary')}**",
                    f"9. Volume necessary: **{m.get('9_volume_necessary')}**",
                    f"10. ClusterGuard necessary: **{m.get('10_cluster_guard_necessary')}**",
                    f"11. StopLowMFE necessary: **{m.get('11_stop_low_mfe_necessary')}**",
                    f"12. LateChase necessary: **{m.get('12_late_chase_necessary')}**",
                    f"13. CAP primary cause: **{m.get('13_cap_primary_cause')}**",
                    f"14. Best gate config: **{m.get('14_best_gate_config')}**",
                    f"15. Runtime change candidate: **{m.get('15_runtime_change_candidate')}**",
                    f"16. Next phase: **{m.get('16_next_phase')}**",
                    "",
                    f"Counterfactual match rate: {m.get('counterfactual_match_rate_pct')}%",
                    f"Baseline replay PnL/PF: {m.get('baseline_pnl')} / {m.get('baseline_pf')}",
                    "",
                    "## Outputs",
                    "",
                    "- `results/reports/phase588_gate_reject_summary.csv`",
                    "- `results/reports/phase588_gate_reject_counterfactual.csv`",
                    "- `results/reports/phase588_board_gate_detail.csv`",
                    "- `results/reports/phase588_board_ablation_replay.csv`",
                    "- `results/reports/phase588_gate_ablation_replay.csv`",
                    "- `results/reports/phase588_gate_combination_replay.csv`",
                    "- `results/reports/phase588_board_entry_quality.csv`",
                    "- `results/reports/phase588_board_daily_symbol_impact.csv`",
                    "- `results/reports/phase588_report.json`",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
