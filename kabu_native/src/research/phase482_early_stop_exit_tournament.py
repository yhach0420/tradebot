"""
Phase482 — PBv2 Early Stop / No Progress Exit Tournament (research only).

Memory/disk constrained: PBv2 accepted trades only (256), stream one trade at a time.
No full-pool enrich, no tick CSV, no intermediate cache.
"""

from __future__ import annotations

import csv
import json
import statistics
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import HARD_STOP_PCT, _max_drawdown_yen
from research.phase404_no_progress_exit_shadow import _exit_result, build_tick_states
from research.phase428_no_progress_tightening_sweep import TighteningPolicySpec, tightening_matches
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _now_iso,
)
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows, _filter_replay_pool
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase480_pbv2_loss_cluster_audit import _assign_cluster
from research.phase481_stop_low_mfe_reduction_tournament import FOCUS_SYMBOLS
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier

JST = ZoneInfo("Asia/Tokyo")
EARLY_STOP_PCT = 0.8
NORMAL_STOP_PCT = HARD_STOP_PCT
TICK_CANDIDATES = (5, 10, 20)
TIME_CANDIDATES = (30, 60, 120)
RSS_LIMIT_MB = 2500.0
DISK_LIMIT_MB = 100.0
MAX_WORKERS_CAP = 2

TOURNAMENT_FIELDS = [
    "variant_id",
    "variant_family",
    "label",
    "param_summary",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "avg_hold_sec",
    "median_hold_sec",
    "stop_low_mfe_count",
    "stop_low_mfe_pnl_yen",
    "early_exit_count",
    "early_exit_pnl_yen",
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
    "delta_maxdd_vs_baseline",
    "delta_stop_low_mfe_count",
    "delta_stop_low_mfe_pnl",
    "cut_winners",
    "saved_losers",
    "rank_by_pnl",
]

SYMBOL_DAY_FIELDS = [
    "variant_id",
    "symbol",
    "day",
    "accepted_count",
    "total_pnl_yen",
    "stop_low_mfe_count",
    "stop_low_mfe_pnl_yen",
    "early_exit_count",
    "delta_pnl_vs_baseline",
]

ROBUSTNESS_FIELDS = [
    "test",
    "variant_id",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_low_mfe_count",
    "delta_pnl_vs_full",
    "top_day_share",
    "top_symbol_share",
]


@dataclass(frozen=True)
class ExitVariantSpec:
    variant_id: str
    family: str
    label: str
    param_summary: str
    n_ticks: Optional[int] = None
    time_sec: Optional[float] = None
    mfe_thr: Optional[float] = None
    mae_thr: Optional[float] = None
    conditional_mfe: Optional[float] = None
    hybrid_rules: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class TradeOutcome:
    position_key: str
    symbol: str
    day: str
    entry_time: str
    exit_time: str
    exit_reason: str
    pnl_yen: float
    mfe_pct: float
    mae_pct: float
    hold_sec: float
    is_stop_low_mfe: bool
    is_early_exit: bool


@dataclass
class VariantAgg:
    outcomes: list[TradeOutcome] = field(default_factory=list)

    def add(self, o: TradeOutcome) -> None:
        self.outcomes.append(o)


def _rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def _check_rss(tag: str, *, limit_mb: float = RSS_LIMIT_MB) -> None:
    rss = _rss_mb()
    print(f"phase482 rss [{tag}] {rss:.1f} MB", flush=True)
    if rss > limit_mb:
        raise MemoryError(f"RSS {rss:.1f} MB exceeds limit {limit_mb:.1f} MB at {tag}")


def _dir_size_mb(path: Path) -> float:
    if not path.is_dir():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def _check_disk_output(paths: Mapping[str, Path]) -> float:
    size = sum(p.stat().st_size for p in paths.values() if p.is_file()) / (1024 * 1024)
    if size > DISK_LIMIT_MB:
        raise OSError(f"phase482 output {size:.1f} MB exceeds {DISK_LIMIT_MB:.1f} MB limit")
    return size


def _is_early_exit_reason(reason: str) -> bool:
    r = str(reason or "").lower()
    return r.startswith("early_") or r == "early_no_progress_exit"


def _is_stop_low_mfe(exit_reason: str, mfe_pct: float) -> bool:
    cid, _ = _assign_cluster({"exit_reason": exit_reason, "mfe_pct": mfe_pct, "pnl_yen": 0})
    return cid == "A"


def _build_variant_specs() -> list[ExitVariantSpec]:
    specs = [ExitVariantSpec("A", "A", "Baseline runtime exit", "Hard Stop → No Progress → Board Dynamic Trailing")]
    for n in TICK_CANDIDATES:
        specs.append(ExitVariantSpec(f"B_N{n}", "B", f"Early NP1 N={n}", f"N={n} MFE<0.2%", n_ticks=n))
        specs.append(ExitVariantSpec(f"C_N{n}", "C", f"Early NP2 N={n}", f"N={n} MFE<0.3%", n_ticks=n, mfe_thr=0.3))
        specs.append(
            ExitVariantSpec(
                f"D_N{n}", "D", f"Early NP+MAE N={n}",
                f"N={n} MFE<0.3% MAE<-0.5%", n_ticks=n, mfe_thr=0.3, mae_thr=-0.5,
            )
        )
        specs.append(ExitVariantSpec(f"E_N{n}", "E", f"Stop tighten N={n}", f"N={n} stop -0.8%/-1.2%", n_ticks=n))
        specs.append(
            ExitVariantSpec(
                f"F_N{n}", "F", f"Cond. tighten N={n}",
                f"N={n} MFE<0.2% → stop -0.8%", n_ticks=n, conditional_mfe=0.2,
            )
        )
    for t in TIME_CANDIDATES:
        specs.append(ExitVariantSpec(f"G_T{t}", "G", f"No Progress Time T={t}s", f"T={t}s MFE<0.3%", time_sec=float(t), mfe_thr=0.3))
    return specs


def _build_hybrid_spec(f1: Mapping[str, Any], f2: Mapping[str, Any]) -> ExitVariantSpec:
    fam1 = str(f1.get("variant_family") or "")
    fam2 = str(f2.get("variant_family") or "")
    id1 = str(f1.get("variant_id") or "")
    id2 = str(f2.get("variant_id") or "")
    n_ticks = None
    time_sec = None
    for row in (f1, f2):
        vid = str(row.get("variant_id") or "")
        if vid.startswith("B_") or vid.startswith("C_") or vid.startswith("D_") or vid.startswith("E_") or vid.startswith("F_"):
            try:
                n_ticks = int(vid.split("N")[-1])
            except ValueError:
                pass
        if vid.startswith("G_"):
            try:
                time_sec = float(vid.split("T")[-1])
            except ValueError:
                pass
    return ExitVariantSpec(
        f"H_{fam1}{id1.split('_')[-1]}_{fam2}{id2.split('_')[-1]}",
        "H",
        f"Hybrid {fam1}+{fam2}",
        f"{f1.get('param_summary')} OR {f2.get('param_summary')}",
        n_ticks=n_ticks,
        time_sec=time_sec,
        mfe_thr=0.3,
        hybrid_rules=tuple(sorted({fam1, fam2})),
    )


def _early_np_trigger(
    *,
    tick_idx: int,
    elapsed: float,
    peak_mfe: float,
    pnl: float,
    spec: ExitVariantSpec,
) -> bool:
    if spec.family == "B" or "B" in spec.hybrid_rules:
        n, thr = spec.n_ticks, 0.2
        if n and tick_idx <= n and peak_mfe < thr:
            return True
    if spec.family == "C" or "C" in spec.hybrid_rules:
        n, thr = spec.n_ticks, spec.mfe_thr if spec.mfe_thr is not None else 0.3
        if n and tick_idx <= n and peak_mfe < thr:
            return True
    if spec.family == "D":
        n = spec.n_ticks
        thr = spec.mfe_thr if spec.mfe_thr is not None else 0.3
        mae = spec.mae_thr if spec.mae_thr is not None else -0.5
        if n and tick_idx <= n and peak_mfe < thr and pnl < mae:
            return True
    if spec.family == "G" or "G" in spec.hybrid_rules:
        tsec = spec.time_sec
        thr = spec.mfe_thr if spec.mfe_thr is not None else 0.3
        if tsec is not None and elapsed <= tsec and peak_mfe < thr:
            return True
    return False


def _stop_pct_for_tick(*, tick_idx: int, peak_mfe: float, spec: ExitVariantSpec) -> float:
    if spec.family == "E" and spec.n_ticks and tick_idx <= spec.n_ticks:
        return EARLY_STOP_PCT
    if spec.family == "F" and spec.n_ticks and tick_idx <= spec.n_ticks:
        cond = spec.conditional_mfe if spec.conditional_mfe is not None else 0.2
        if peak_mfe < cond:
            return EARLY_STOP_PCT
    if "F" in spec.hybrid_rules and spec.n_ticks and tick_idx <= spec.n_ticks and peak_mfe < 0.2:
        return EARLY_STOP_PCT
    return NORMAL_STOP_PCT


def simulate_exit_on_states(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    imb_pct: Optional[float],
    spec: ExitVariantSpec,
) -> dict[str, Any]:
    if spec.family == "A":
        from research.phase428_no_progress_tightening_sweep import simulate_tightening_no_progress_exit

        return simulate_tightening_no_progress_exit(
            states,
            entry_price=entry_price,
            entry_ts=entry_ts,
            imb_pct=imb_pct,
            policy=BEST_NP_POLICY,
        )

    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")

    for tick_idx, state in enumerate(states, start=1):
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        peak_mfe = float(state["peak_mfe"])
        elapsed = float(state["elapsed"])

        if spec.family in ("B", "C", "D", "G", "H") and _early_np_trigger(
            tick_idx=tick_idx, elapsed=elapsed, peak_mfe=peak_mfe, pnl=pnl, spec=spec,
        ):
            return _exit_result(entry_price, px, ts, pnl, "early_no_progress_exit")

        stop_pct = _stop_pct_for_tick(tick_idx=tick_idx, peak_mfe=peak_mfe, spec=spec)
        hard_px = entry_price * (1.0 - stop_pct / 100.0)
        if px <= hard_px:
            reason = "early_stop_hit" if stop_pct < NORMAL_STOP_PCT else "stop_hit"
            return _exit_result(entry_price, px, ts, pnl, reason)

        if tightening_matches(state, BEST_NP_POLICY):
            return _exit_result(entry_price, px, ts, pnl, "no_progress_exit")

        activate_base, giveback_frac, _ = trailing_params_for_board_tier(imb_pct)
        if peak_mfe >= activate_base and pnl <= peak_mfe * giveback_frac:
            return _exit_result(entry_price, px, ts, pnl, "trailing_mfe_exit")

    last = states[-1]
    return _exit_result(entry_price, float(last["px"]), float(last["ts"]), float(last["pnl"]), "session_close")


def _load_day_all_series(kabu_root: Path, day: str) -> dict[str, list[tuple[float, float]]]:
    """Load one day CSV once; return symbol -> [(ts, px), ...]."""
    base = kabu_root / "results" / "small_paper" / day
    out: dict[str, list[tuple[float, float]]] = defaultdict(list)
    if not base.is_dir():
        return out
    for sess in sorted(base.iterdir()):
        if not sess.is_dir() or not sess.name.startswith("live_session"):
            continue
        path = sess / "small_paper_events.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                sym = str(row.get("symbol") or "")
                px = _float(row.get("current_price"))
                if not sym or px is None or px <= 0:
                    continue
                ts = _parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
                if ts is None:
                    continue
                out[sym].append((ts.timestamp(), px))
    for sym in out:
        out[sym].sort(key=lambda x: x[0])
    return dict(out)


def _stream_tick_states(
    trade: Mapping[str, Any],
    series: Sequence[tuple[float, float]],
) -> Optional[tuple[list[dict[str, Any]], float, float, Optional[float]]]:
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    entry_px = _float(trade.get("entry_price"))
    if ent is None or not entry_px or entry_px <= 0 or len(series) < 3:
        return None
    ent_ts = ent.timestamp()
    forward = [(ts, px) for ts, px in series if ts >= ent_ts - 1.0 and px > 0]
    if len(forward) < 3:
        return None
    session_end = float(forward[-1][0])
    vwap_dev = _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct"))
    states = build_tick_states(
        forward,
        entry_ts=ent_ts,
        entry_price=entry_px,
        session_end_ts=session_end,
        entry_vwap_dev_pct=vwap_dev,
    )
    if not states:
        return None
    imb = _float(trade.get("entry_imbalance_percentile"))
    return states, entry_px, ent_ts, imb


def _outcome_from_sim(
    trade: Mapping[str, Any],
    sim: Mapping[str, Any],
    states: Sequence[Mapping[str, Any]],
) -> TradeOutcome:
    from replay.pnl_yen import compute_pnl_yen_100

    entry_px = float(trade.get("entry_price") or 0)
    exit_px = float(sim.get("shadow_exit_price") or entry_px)
    pnl_yen = float(sim.get("shadow_pnl_yen_100") or compute_pnl_yen_100(entry_px, exit_px))
    exit_ts = float(sim.get("shadow_exit_ts") or 0)
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    hold = (exit_ts - ent.timestamp()) if ent else 0.0
    exit_iso = datetime.fromtimestamp(exit_ts, tz=JST).isoformat() if exit_ts > 0 else ""
    reason = normalize_exit_reason(str(sim.get("shadow_exit_reason") or ""))
    peak_mfe = max((float(s["peak_mfe"]) for s in states), default=0.0)
    mae = min((float(s["pnl"]) for s in states), default=0.0)
    return TradeOutcome(
        position_key=_position_key(trade),
        symbol=str(trade.get("symbol") or "").replace(".T", ""),
        day=str(trade.get("day") or "")[:8],
        entry_time=str(trade.get("entry_time") or ""),
        exit_time=exit_iso,
        exit_reason=reason,
        pnl_yen=round(pnl_yen, 2),
        mfe_pct=round(peak_mfe, 4),
        mae_pct=round(mae, 4),
        hold_sec=round(hold, 2),
        is_stop_low_mfe=_is_stop_low_mfe(reason, peak_mfe),
        is_early_exit=_is_early_exit_reason(reason),
    )


def _outcome_from_log(trade: Mapping[str, Any], log_row: Mapping[str, Any], *, mfe: float, mae: float) -> TradeOutcome:
    reason = normalize_exit_reason(str(log_row.get("exit_reason") or ""))
    return TradeOutcome(
        position_key=_position_key(trade),
        symbol=str(trade.get("symbol") or "").replace(".T", ""),
        day=str(trade.get("day") or "")[:8],
        entry_time=str(trade.get("entry_time") or ""),
        exit_time=str(log_row.get("exit_time") or ""),
        exit_reason=reason,
        pnl_yen=round(float(log_row.get("pnl_yen") or 0), 2),
        mfe_pct=round(mfe, 4),
        mae_pct=round(mae, 4),
        hold_sec=round(float(log_row.get("hold_sec") or 0), 2),
        is_stop_low_mfe=_is_stop_low_mfe(reason, mfe),
        is_early_exit=_is_early_exit_reason(reason),
    )


def _simulate_trade_all_variants(
    trade: Mapping[str, Any],
    series: Sequence[tuple[float, float]],
    specs: Sequence[ExitVariantSpec],
    baseline_outcome: Optional[TradeOutcome],
) -> dict[str, TradeOutcome]:
    streamed = _stream_tick_states(trade, series)
    out: dict[str, TradeOutcome] = {}
    if baseline_outcome is not None:
        out["A"] = baseline_outcome
    if streamed is None:
        if baseline_outcome is not None:
            for spec in specs:
                if spec.variant_id != "A":
                    out[spec.variant_id] = baseline_outcome
        return out
    states, entry_px, entry_ts, imb = streamed
    for spec in specs:
        if spec.variant_id == "A" and "A" in out:
            continue
        sim = simulate_exit_on_states(states, entry_price=entry_px, entry_ts=entry_ts, imb_pct=imb, spec=spec)
        out[spec.variant_id] = _outcome_from_sim(trade, sim, states)
    return out


def _chron_pnls(outcomes: Sequence[TradeOutcome]) -> list[float]:
    rows = sorted(outcomes, key=lambda o: (o.exit_time or o.entry_time))
    return [float(o.pnl_yen) for o in rows]


def _metrics_from_outcomes(
    outcomes: Sequence[TradeOutcome],
    *,
    baseline_outcomes: Sequence[TradeOutcome],
) -> dict[str, Any]:
    chron = _chron_pnls(outcomes)
    base_map = {o.position_key: o for o in baseline_outcomes}
    cut_winners = saved_losers = 0
    for o in outcomes:
        b = base_map.get(o.position_key)
        if b is None:
            continue
        if b.pnl_yen > 0 and o.pnl_yen < b.pnl_yen - 1e-6:
            cut_winners += 1
        if b.pnl_yen < 0 and o.pnl_yen > b.pnl_yen + 1e-6:
            saved_losers += 1
    slm = [o for o in outcomes if o.is_stop_low_mfe]
    early = [o for o in outcomes if o.is_early_exit]
    holds = [o.hold_sec for o in outcomes]
    stops = sum(1 for o in outcomes if "stop" in o.exit_reason.lower())
    return {
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": len(outcomes),
        "stop_rate": round(stops / len(outcomes), 4) if outcomes else 0.0,
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
        "median_hold_sec": round(statistics.median(holds), 2) if holds else 0.0,
        "stop_low_mfe_count": len(slm),
        "stop_low_mfe_pnl_yen": round(sum(o.pnl_yen for o in slm), 2),
        "early_exit_count": len(early),
        "early_exit_pnl_yen": round(sum(o.pnl_yen for o in early), 2),
        "cut_winners": cut_winners,
        "saved_losers": saved_losers,
    }


def _symbol_day_rows(
    variant_id: str,
    outcomes: Sequence[TradeOutcome],
    baseline: Sequence[TradeOutcome],
) -> list[dict[str, Any]]:
    base_by: dict[tuple[str, str], float] = defaultdict(float)
    for o in baseline:
        base_by[(o.symbol, o.day)] += o.pnl_yen
    rows: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[TradeOutcome]] = defaultdict(list)
    for o in outcomes:
        grouped[(o.symbol, o.day)].append(o)
    for sym in FOCUS_SYMBOLS:
        for day in (DAY_618, DAY_619):
            bucket = grouped.get((sym, day), [])
            pnl = sum(x.pnl_yen for x in bucket)
            slm = [x for x in bucket if x.is_stop_low_mfe]
            early = [x for x in bucket if x.is_early_exit]
            rows.append(
                {
                    "variant_id": variant_id,
                    "symbol": sym,
                    "day": day,
                    "accepted_count": len(bucket),
                    "total_pnl_yen": round(pnl, 2),
                    "stop_low_mfe_count": len(slm),
                    "stop_low_mfe_pnl_yen": round(sum(x.pnl_yen for x in slm), 2),
                    "early_exit_count": len(early),
                    "delta_pnl_vs_baseline": round(pnl - base_by.get((sym, day), 0.0), 2),
                }
            )
    for sym in FOCUS_SYMBOLS:
        sym_rows = [o for o in outcomes if o.symbol == sym]
        pnl = sum(o.pnl_yen for o in sym_rows)
        base_pnl = sum(o.pnl_yen for o in baseline if o.symbol == sym)
        slm = [x for x in sym_rows if x.is_stop_low_mfe]
        early = [x for x in sym_rows if x.is_early_exit]
        rows.append(
            {
                "variant_id": variant_id,
                "symbol": sym,
                "day": "ALL",
                "accepted_count": len(sym_rows),
                "total_pnl_yen": round(pnl, 2),
                "stop_low_mfe_count": len(slm),
                "stop_low_mfe_pnl_yen": round(sum(x.pnl_yen for x in slm), 2),
                "early_exit_count": len(early),
                "delta_pnl_vs_baseline": round(pnl - base_pnl, 2),
            }
        )
    return rows


def _concentration_from_outcomes(outcomes: Sequence[TradeOutcome]) -> tuple[float, float]:
    if not outcomes:
        return 0.0, 0.0
    total = sum(abs(o.pnl_yen) for o in outcomes)
    if total <= 0:
        return 0.0, 0.0
    day_pnls: Counter[str] = Counter()
    sym_pnls: Counter[str] = Counter()
    for o in outcomes:
        day_pnls[o.day] += o.pnl_yen
        sym_pnls[o.symbol] += o.pnl_yen
    top_day = max((abs(v) for v in day_pnls.values()), default=0.0)
    top_sym = max((abs(v) for v in sym_pnls.values()), default=0.0)
    return round(top_day / total, 4), round(top_sym / total, 4)


def _verdict(*, best: Mapping[str, Any], resource_hit: bool) -> str:
    if resource_hit:
        return "resource_limit_hit"
    if str(best.get("variant_id")) == "A" or float(best.get("delta_pnl_vs_baseline") or 0) <= 0:
        if int(best.get("delta_stop_low_mfe_count") or 0) >= 0:
            return "entry_problem_confirmed" if float(best.get("delta_pnl_vs_baseline") or 0) <= 0 else "no_exit_edge"
        return "no_exit_edge"
    if int(best.get("cut_winners") or 0) > 25:
        return "early_exit_overfit"
    if float(best.get("delta_pnl_vs_baseline") or 0) >= 10000 and int(best.get("delta_stop_low_mfe_count") or 0) <= -5:
        return "early_exit_candidate"
    if float(best.get("delta_pnl_vs_baseline") or 0) > 0:
        return "early_exit_candidate"
    return "no_exit_edge"


def _load_accepted_trades(replay_pool: Sequence[Mapping[str, Any]], runtime_shadows: Mapping[str, Any]) -> list[dict[str, Any]]:
    st = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase482_accepted_bootstrap",
        entry_block_fn=_entry_block(pass_pbv2),
        baseline_accepted_keys=set(),
    )
    accepted: list[dict[str, Any]] = []
    for log_row in st.trade_log:
        tr = dict(log_row.get("trade") or log_row)
        accepted.append({"trade": tr, "log": log_row})
    return accepted


def run_phase482(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    max_workers = min(max(1, max_workers), MAX_WORKERS_CAP)
    resource_hit = False
    peak_rss = _rss_mb()
    _check_rss("start")

    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx={})
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)

    accepted = _load_accepted_trades(replay_pool, runtime_shadows)
    print(f"phase482 accepted trades {len(accepted)}", flush=True)
    _check_rss("after_bootstrap")

    needed_days = {str(a["trade"].get("day") or "")[:8] for a in accepted}
    day_cache: dict[str, dict[str, list[tuple[float, float]]]] = {}
    cache_lock = threading.Lock()

    def _series(sym: str, day: str) -> list[tuple[float, float]]:
        with cache_lock:
            if day not in day_cache:
                day_cache[day] = _load_day_all_series(kabu, day)
                if len(day_cache) > 3:
                    oldest = min(day_cache)
                    del day_cache[oldest]
            return list(day_cache.get(day, {}).get(sym, []))

    overlay_specs = [s for s in _build_variant_specs() if s.family != "A"]
    variant_aggs: dict[str, VariantAgg] = {s.variant_id: VariantAgg() for s in _build_variant_specs()}
    variant_aggs["A"] = VariantAgg()

    def _process_one(item: Mapping[str, Any]) -> dict[str, TradeOutcome]:
        tr = item["trade"]
        log = item["log"]
        sym = str(tr.get("symbol") or "")
        day = str(tr.get("day") or "")[:8]
        series = _series(sym, day)
        streamed = _stream_tick_states(tr, series)
        if streamed is not None:
            states, entry_px, entry_ts, imb = streamed
            mfe = max((float(s["peak_mfe"]) for s in states), default=0.0)
            mae = min((float(s["pnl"]) for s in states), default=0.0)
        else:
            mfe, mae = 0.0, 0.0
        base = _outcome_from_log(tr, log, mfe=mfe, mae=mae)
        results = _simulate_trade_all_variants(tr, series, overlay_specs, base)
        results["A"] = base
        return results

    all_results: dict[str, dict[str, TradeOutcome]] = {}
    if parallel and len(accepted) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_process_one, item): item for item in accepted}
            for fut in as_completed(futs):
                item = futs[fut]
                all_results[_position_key(item["trade"])] = fut.result()
                peak_rss = max(peak_rss, _rss_mb())
                if len(all_results) % 64 == 0:
                    _check_rss("parallel_trade")
    else:
        for i, item in enumerate(accepted, start=1):
            key = _position_key(item["trade"])
            all_results[key] = _process_one(item)
            if i % 50 == 0:
                peak_rss = max(peak_rss, _rss_mb())
                _check_rss(f"trade_{i}")

    baseline_outcomes = [all_results[k]["A"] for k in sorted(all_results)]
    for vid, agg in variant_aggs.items():
        for key in sorted(all_results):
            if vid in all_results[key]:
                agg.add(all_results[key][vid])

    tournament_rows: list[dict[str, Any]] = []
    baseline_metrics = _metrics_from_outcomes(baseline_outcomes, baseline_outcomes=baseline_outcomes)
    tournament_rows.append(
        {
            "variant_id": "A",
            "variant_family": "A",
            "label": "Baseline runtime exit",
            "param_summary": "Hard Stop → No Progress → Board Dynamic Trailing",
            **baseline_metrics,
            "delta_pnl_vs_baseline": 0.0,
            "delta_pf_vs_baseline": 0.0,
            "delta_maxdd_vs_baseline": 0.0,
            "delta_stop_low_mfe_count": 0,
            "delta_stop_low_mfe_pnl": 0.0,
            "rank_by_pnl": 0,
        }
    )

    for spec in overlay_specs:
        outs = variant_aggs[spec.variant_id].outcomes
        met = _metrics_from_outcomes(outs, baseline_outcomes=baseline_outcomes)
        tournament_rows.append(
            {
                "variant_id": spec.variant_id,
                "variant_family": spec.family,
                "label": spec.label,
                "param_summary": spec.param_summary,
                **met,
                "delta_pnl_vs_baseline": round(met["total_pnl_yen"] - baseline_metrics["total_pnl_yen"], 2),
                "delta_pf_vs_baseline": round((met["profit_factor"] or 0) - (baseline_metrics["profit_factor"] or 0), 4),
                "delta_maxdd_vs_baseline": round(met["max_drawdown_yen"] - baseline_metrics["max_drawdown_yen"], 2),
                "delta_stop_low_mfe_count": met["stop_low_mfe_count"] - baseline_metrics["stop_low_mfe_count"],
                "delta_stop_low_mfe_pnl": round(met["stop_low_mfe_pnl_yen"] - baseline_metrics["stop_low_mfe_pnl_yen"], 2),
            }
        )

    non_a = [r for r in tournament_rows if r["variant_id"] != "A"]
    non_a.sort(key=lambda r: float(r.get("delta_pnl_vs_baseline") or -1e18), reverse=True)
    h_specs: list[ExitVariantSpec] = []
    if len(non_a) >= 2:
        fams: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for row in non_a:
            fam = str(row.get("variant_family") or "")
            if fam in ("B", "C", "F", "G") and fam not in seen:
                seen.add(fam)
                fams.append(row)
            if len(fams) >= 2:
                break
        if len(fams) == 2:
            h_spec = _build_hybrid_spec(fams[0], fams[1])
            h_specs.append(h_spec)
            h_agg = VariantAgg()
            for item in accepted:
                tr = item["trade"]
                sym = str(tr.get("symbol") or "")
                day = str(tr.get("day") or "")[:8]
                key = _position_key(tr)
                base = all_results[key]["A"]
                series = _series(sym, day)
                res = _simulate_trade_all_variants(tr, series, [h_spec], base)
                if h_spec.variant_id in res:
                    h_agg.add(res[h_spec.variant_id])
            variant_aggs[h_spec.variant_id] = h_agg
            h_outs = h_agg.outcomes
            if h_outs:
                met = _metrics_from_outcomes(h_outs, baseline_outcomes=baseline_outcomes)
                tournament_rows.append(
                    {
                        "variant_id": h_spec.variant_id,
                        "variant_family": "H",
                        "label": h_spec.label,
                        "param_summary": h_spec.param_summary,
                        **met,
                        "delta_pnl_vs_baseline": round(met["total_pnl_yen"] - baseline_metrics["total_pnl_yen"], 2),
                        "delta_pf_vs_baseline": round((met["profit_factor"] or 0) - (baseline_metrics["profit_factor"] or 0), 4),
                        "delta_maxdd_vs_baseline": round(met["max_drawdown_yen"] - baseline_metrics["max_drawdown_yen"], 2),
                        "delta_stop_low_mfe_count": met["stop_low_mfe_count"] - baseline_metrics["stop_low_mfe_count"],
                        "delta_stop_low_mfe_pnl": round(met["stop_low_mfe_pnl_yen"] - baseline_metrics["stop_low_mfe_pnl_yen"], 2),
                    }
                )

    tournament_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or -1e18), reverse=True)
    for i, r in enumerate(tournament_rows, start=1):
        r["rank_by_pnl"] = i

    best_non_a = max(
        (r for r in tournament_rows if r["variant_id"] != "A"),
        key=lambda r: (float(r.get("delta_pnl_vs_baseline") or -1e18), int(r.get("delta_stop_low_mfe_count") or 0)),
        default=tournament_rows[0],
    )
    best = best_non_a if float(best_non_a.get("delta_pnl_vs_baseline") or 0) > 0 else next(r for r in tournament_rows if r["variant_id"] == "A")
    best_id = str(best["variant_id"])

    symbol_day_rows: list[dict[str, Any]] = []
    for row in tournament_rows:
        vid = str(row["variant_id"])
        outs = variant_aggs[vid].outcomes if vid in variant_aggs and variant_aggs[vid].outcomes else baseline_outcomes
        symbol_day_rows.extend(_symbol_day_rows(vid, outs, baseline_outcomes))

    robust_rows: list[dict[str, Any]] = []
    best_outcomes = variant_aggs[best_id].outcomes if best_id in variant_aggs else baseline_outcomes
    full_pnl = float(best.get("total_pnl_yen") or 0)

    def _robust(test: str, filt: Callable[[TradeOutcome], bool]) -> None:
        sub = [o for o in best_outcomes if filt(o)]
        met = _metrics_from_outcomes(sub, baseline_outcomes=baseline_outcomes)
        td, ts = _concentration_from_outcomes(sub)
        robust_rows.append(
            {
                "test": test,
                "variant_id": best_id,
                "total_pnl_yen": met["total_pnl_yen"],
                "profit_factor": met["profit_factor"],
                "max_drawdown_yen": met["max_drawdown_yen"],
                "accepted_count": met["accepted_count"],
                "stop_low_mfe_count": met["stop_low_mfe_count"],
                "delta_pnl_vs_full": round(met["total_pnl_yen"] - full_pnl, 2),
                "top_day_share": td,
                "top_symbol_share": ts,
            }
        )

    days = sorted({o.day for o in best_outcomes if o.day})
    for day in days:
        _robust(f"LOO_{day}", lambda o, d=day: o.day != d)
    _robust("full", lambda o: True)
    _robust("exclude_6976", lambda o: o.symbol != "6976")
    _robust("exclude_4062", lambda o: o.symbol != "4062")
    sym_counts = Counter(o.symbol for o in best_outcomes)
    if sym_counts:
        top_sym = sym_counts.most_common(1)[0][0]
        _robust("exclude_top_symbol", lambda o, s=top_sym: o.symbol != s)

    loo_deltas = [float(r.get("delta_pnl_vs_full") or 0) for r in robust_rows if str(r.get("test", "")).startswith("LOO_")]
    overfit_risk = "high" if loo_deltas and min(loo_deltas) < -40000 else "moderate" if loo_deltas and statistics.pstdev(loo_deltas) > 25000 else "low"

    verdict = _verdict(best=best, resource_hit=resource_hit)
    sym6976 = next((r for r in symbol_day_rows if r["variant_id"] == best_id and r["symbol"] == "6976" and r["day"] == "ALL"), {})
    sym4062 = next((r for r in symbol_day_rows if r["variant_id"] == best_id and r["symbol"] == "4062" and r["day"] == "ALL"), {})
    day618 = {
        "variant_id": best_id,
        "day": DAY_618,
        "accepted_count": sum(r["accepted_count"] for r in symbol_day_rows if r["variant_id"] == best_id and r["day"] == DAY_618),
        "total_pnl_yen": round(sum(r["total_pnl_yen"] for r in symbol_day_rows if r["variant_id"] == best_id and r["day"] == DAY_618), 2),
        "delta_pnl_vs_baseline": round(sum(r["delta_pnl_vs_baseline"] for r in symbol_day_rows if r["variant_id"] == best_id and r["day"] == DAY_618), 2),
    }
    day619 = {
        "variant_id": best_id,
        "day": DAY_619,
        "accepted_count": sum(r["accepted_count"] for r in symbol_day_rows if r["variant_id"] == best_id and r["day"] == DAY_619),
        "total_pnl_yen": round(sum(r["total_pnl_yen"] for r in symbol_day_rows if r["variant_id"] == best_id and r["day"] == DAY_619), 2),
        "delta_pnl_vs_baseline": round(sum(r["delta_pnl_vs_baseline"] for r in symbol_day_rows if r["variant_id"] == best_id and r["day"] == DAY_619), 2),
    }

    peak_rss = max(peak_rss, _rss_mb())
    mandatory = {
        "1_best_exit_variant": f"{best.get('variant_id')} ({best.get('label')})",
        "2_pnl_improvement": best.get("delta_pnl_vs_baseline"),
        "3_pf_improvement": best.get("delta_pf_vs_baseline"),
        "4_maxdd_change": best.get("delta_maxdd_vs_baseline"),
        "5_stop_low_mfe_reduction_count": best.get("delta_stop_low_mfe_count"),
        "6_stop_low_mfe_reduction_pnl": best.get("delta_stop_low_mfe_pnl"),
        "7_early_exit_count": best.get("early_exit_count"),
        "8_early_exit_pnl": best.get("early_exit_pnl_yen"),
        "9_winner_early_cut": best.get("cut_winners"),
        "10_6976_impact": sym6976,
        "11_4062_impact": sym4062,
        "12_day_618_impact": day618,
        "13_day_619_impact": day619,
        "14_overfit_risk": overfit_risk,
        "15_runtime_candidate": verdict == "early_exit_candidate" and float(best.get("delta_pnl_vs_baseline") or 0) >= 15000,
        "16_shadow_candidate": best_id if verdict in ("early_exit_candidate", "early_exit_overfit") else None,
        "17_next_actions": _next_actions(verdict, best),
        "18_peak_rss_mb": round(peak_rss, 1),
        "19_output_size_mb": None,
        "verdict": verdict,
        "baseline_pnl": baseline_metrics["total_pnl_yen"],
        "baseline_stop_low_mfe_count": baseline_metrics["stop_low_mfe_count"],
        "accepted_count": len(accepted),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_tournament": tournament_rows,
        "_symbol_day": symbol_day_rows,
        "_robustness": robust_rows,
        "_peak_rss_mb": peak_rss,
    }


def _next_actions(verdict: str, best: Mapping[str, Any]) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "early_exit_candidate":
        actions.append(f"Shadow early exit {best.get('variant_id')}: {best.get('param_summary')}")
    elif verdict == "early_exit_overfit":
        actions.append("Early exit in-sample gain but winner cuts / LOO unstable")
    elif verdict == "entry_problem_confirmed":
        actions.append("Exit overlays cannot fix stop_low_mfe on accepted set")
    elif verdict == "resource_limit_hit":
        actions.append("Stopped by RSS/disk guard — rerun with narrower scope")
    else:
        actions.append("Keep runtime exit stack")
    actions.append(f"Best ΔPnL: {best.get('delta_pnl_vs_baseline')}")
    return actions


@dataclass
class Phase482Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        try:
            return run_phase482(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)
        except (MemoryError, OSError) as exc:
            return {
                "generated_at": _now_iso(),
                "period_start": PERIOD_START,
                "period_end": PERIOD_END,
                "verdict": "resource_limit_hit",
                "mandatory_answers": {
                    "verdict": "resource_limit_hit",
                    "17_next_actions": [f"Resource limit: {exc}"],
                    "18_peak_rss_mb": round(_rss_mb(), 1),
                },
                "_tournament": [],
                "_symbol_day": [],
                "_robustness": [],
            }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "tournament": reports / "phase482_early_stop_exit_tournament.csv",
            "symbol_day": reports / "phase482_early_stop_symbol_day.csv",
            "robustness": reports / "phase482_early_stop_robustness.csv",
            "summary": reports / "phase482_summary.json",
        }
        _write_csv(paths["tournament"], TOURNAMENT_FIELDS, list(result.get("_tournament") or []))
        _write_csv(paths["symbol_day"], SYMBOL_DAY_FIELDS, list(result.get("_symbol_day") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        out_mb = _check_disk_output(paths)
        if isinstance(payload.get("mandatory_answers"), dict):
            payload["mandatory_answers"]["19_output_size_mb"] = round(out_mb, 3)
            paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase482_early_stop_exit_tournament.md"
        self._write_report(report, result, out_mb)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any], out_mb: float) -> None:
        m = result.get("mandatory_answers") or {}
        rows = list(result.get("_tournament") or [])
        lines = [
            "# Phase482 — PBv2 Early Stop / No Progress Exit Tournament",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            f"**Peak RSS:** {m.get('18_peak_rss_mb')} MB | **Output:** {out_mb:.3f} MB",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 最良Exit variant | **{m.get('1_best_exit_variant')}** |",
            f"| 2 | PnL改善 | **{m.get('2_pnl_improvement')}** |",
            f"| 3 | PF改善 | **{m.get('3_pf_improvement')}** |",
            f"| 4 | maxDD変化 | **{m.get('4_maxdd_change')}** |",
            f"| 5 | stop_low_mfe削減 | **{m.get('5_stop_low_mfe_reduction_count')}** |",
            f"| 6 | stop_low_mfe PnL | **{m.get('6_stop_low_mfe_reduction_pnl')}** |",
            f"| 7 | early_exit件数 | **{m.get('7_early_exit_count')}** |",
            f"| 8 | early_exit PnL | **{m.get('8_early_exit_pnl')}** |",
            f"| 9 | winner早切り | **{m.get('9_winner_early_cut')}** |",
            f"| 14 | 過学習リスク | **{m.get('14_overfit_risk')}** |",
            f"| 15 | Runtime候補 | **{m.get('15_runtime_candidate')}** |",
            f"| 16 | Shadow候補 | **{m.get('16_shadow_candidate')}** |",
            f"| 18 | 最大RSS | **{m.get('18_peak_rss_mb')} MB** |",
            f"| 19 | 出力サイズ | **{m.get('19_output_size_mb')} MB** |",
            "",
            "## Top variants",
            "",
        ]
        for r in rows[:8]:
            lines.append(
                f"- **{r.get('variant_id')}**: PnL {r.get('total_pnl_yen')} Δ{r.get('delta_pnl_vs_baseline')} "
                f"slm Δ{r.get('delta_stop_low_mfe_count')} early {r.get('early_exit_count')}"
            )
        lines.append("")
        lines.append(f"**判定:** `{result.get('verdict')}`")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
