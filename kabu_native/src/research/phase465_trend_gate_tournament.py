"""
Phase465 — Trend Entry Gate Tournament (research only).

Finds deployable Trend Entry Gates within Trend-following archetype (Phase464).
"""

from __future__ import annotations

import json
import math
import pickle
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log,
    _now_iso,
    _optional_float,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import _board_token, _v2_entry_score
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    pass_a0_baseline,
)
from research.phase464_pre_gate_archetype_audit import (
    _annotate_candidates,
    _close_proxy_pnl,
    _is_trend_following,
    _load_population_cache,
    _passes_board_gate,
    _rise,
    _vwap_above_ratio,
    _weak_shape_block,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import ENTRY_SCORE_V2_GATE_MIN

REPLAY_MODE = "phase456_runtime_np"
CAPTURE = ("3441.T", "6492.T", "7256.T", "7600.T")
SYMBOL_EXCLUDE_TESTS = ("6976.T", "4062.T")

GATE_TOURNAMENT_FIELDS = [
    "gate_id",
    "conditions",
    "trend_only_pnl_yen",
    "trend_only_pf",
    "trend_only_maxdd_yen",
    "trend_only_accepted",
    "trend_only_stop_rate",
    "median_would_pnl",
    "win_rate_proxy",
    "rank_by_pnl",
]

DUAL_REPLAY_FIELDS = [
    "variant",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "daily_pnl_618",
    "daily_pnl_619",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_7600",
    "delta_pnl_vs_pullback",
]

ROBUSTNESS_FIELDS = [
    "test",
    "gate_or_variant",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "delta_pnl_vs_full",
    "top_day_share",
    "top_symbol_share",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _board_bucket(trade: Mapping[str, Any]) -> str:
    tok = _board_token(trade) or ""
    return tok.split(":", 1)[-1] if ":" in tok else "unknown"


def _feature_row(trade: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "r5": _rise(trade, 5),
        "r10": _rise(trade, 10),
        "r15": _rise(trade, 15),
        "r30": _rise(trade, 30),
        "vwap_dev_pct": _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct")),
        "vwap_above_ratio": _vwap_above_ratio(trade),
        "consecutive_above_ticks": _float(trade.get("consecutive_above_ticks")),
        "day_high_distance": abs(
            _optional_float(trade.get("day_high_distance_pct"))
            or _optional_float(trade.get("entry_near_day_high_pct"))
            or 0.0
        ),
        "high_update_count_30m": _float(trade.get("high_update_count_30m")),
        "high_update_count_session": _float(trade.get("high_update_count_session")),
        "board_imbalance": _float(trade.get("entry_order_book_imbalance")),
        "board_bucket": _board_bucket(trade),
    }


def _percentile(vals: Sequence[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(s[int(k)], 4)
    return round(s[f] + (s[c] - s[f]) * (k - f), 4)


def _dist_row(feature: str, vals: Sequence[float]) -> dict[str, Any]:
    clean = [float(v) for v in vals if v is not None]
    if not clean:
        return {"feature": feature, "count": 0}
    return {
        "feature": feature,
        "count": len(clean),
        "mean": round(statistics.mean(clean), 4),
        "p10": _percentile(clean, 0.10),
        "p25": _percentile(clean, 0.25),
        "p50": _percentile(clean, 0.50),
        "p75": _percentile(clean, 0.75),
        "p90": _percentile(clean, 0.90),
    }


def _cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    pooled = math.sqrt((sa * sa + sb * sb) / 2.0)
    if pooled <= 1e-12:
        return 0.0
    return round((ma - mb) / pooled, 4)


def _mutual_info_binary(x: Sequence[int], y: Sequence[int]) -> Optional[float]:
    if len(x) != len(y) or len(x) < 10:
        return None
    n = len(x)
    px = Counter(x)
    py = Counter(y)
    pxy = Counter(zip(x, y, strict=True))
    mi = 0.0
    for xi in (0, 1):
        for yi in (0, 1):
            p_ij = pxy.get((xi, yi), 0) / n
            if p_ij <= 0:
                continue
            mi += p_ij * math.log2(p_ij / ((px[xi] / n) * (py[yi] / n)))
    return round(max(mi, 0.0), 4)


def _mi_median_split(w_vals: Sequence[float], l_vals: Sequence[float]) -> Optional[float]:
    all_vals = [v for v in list(w_vals) + list(l_vals) if v is not None]
    if len(all_vals) < 10:
        return None
    med = statistics.median(all_vals)
    x = [1 if v > med else 0 for v in all_vals]
    y = [1] * len(w_vals) + [0] * len(l_vals)
    return _mutual_info_binary(x, y)


def _pass_trend_runtime_core(t: Mapping[str, Any]) -> bool:
    if not _passes_board_gate(t):
        return False
    if guard_high_drift(t):
        return False
    if _weak_shape_block(t):
        return False
    if phase364_blocked_only(t):
        return False
    return True


def _gate_t1(t: Mapping[str, Any]) -> bool:
    return (_rise(t, 30) or -1e18) > 0


def _gate_t2(t: Mapping[str, Any]) -> bool:
    return (_float(t.get("high_update_count_30m")) or 0) >= 2


def _gate_t3(t: Mapping[str, Any]) -> bool:
    return (_vwap_above_ratio(t) or 0) >= 0.7


def _gate_t4(t: Mapping[str, Any]) -> bool:
    return (_float(t.get("consecutive_above_ticks")) or 0) >= 20


def _gate_t5(t: Mapping[str, Any]) -> bool:
    return (_board_token(t) or "") == "Board:high"


GATE_SPECS: dict[str, tuple[str, Callable[[Mapping[str, Any]], bool]]] = {
    "T1": ("r30 > 0", _gate_t1),
    "T2": ("high_update_count_30m >= 2", _gate_t2),
    "T3": ("vwap_above_ratio >= 0.7", _gate_t3),
    "T4": ("consecutive_above_ticks >= 20", _gate_t4),
    "T5": ("board_high", _gate_t5),
    "T6": ("T1 + T2", lambda t: _gate_t1(t) and _gate_t2(t)),
    "T7": ("T1 + T3", lambda t: _gate_t1(t) and _gate_t3(t)),
    "T8": ("T1 + T4", lambda t: _gate_t1(t) and _gate_t4(t)),
    "T9": ("T2 + T3", lambda t: _gate_t2(t) and _gate_t3(t)),
    "T10": ("T1 + T2 + T3", lambda t: _gate_t1(t) and _gate_t2(t) and _gate_t3(t)),
}


def _make_trend_only(gate_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    def fn(t: Mapping[str, Any]) -> bool:
        if not _is_trend_following(t):
            return False
        if not gate_fn(t):
            return False
        return _pass_trend_runtime_core(t)

    return fn


def _make_dual(trend_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    return lambda t: pass_a0_baseline(t) or trend_fn(t)


def _entry_block(pass_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    return lambda t: not pass_fn(t)


def _concentration(trade_log: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    if not trade_log:
        return 0.0, 0.0
    total = sum(abs(_float(r.get("pnl_yen")) or 0) for r in trade_log)
    if total <= 0:
        return 0.0, 0.0
    by_day: Counter[str] = Counter()
    by_sym: Counter[str] = Counter()
    for r in trade_log:
        tr = r.get("trade") or {}
        day = str(tr.get("day") or "")[:8]
        sym = str(tr.get("symbol") or "")
        pnl = abs(_float(r.get("pnl_yen")) or 0)
        by_day[day] += pnl
        by_sym[sym] += pnl
    return round(max(by_day.values()) / total, 4), round(max(by_sym.values()) / total, 4)


def _replay_metrics(
    state: Any,
    *,
    variant: str,
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "") for r in state.trade_log}
    top_day, top_sym = _concentration(state.trade_log)
    row = {
        "variant": variant,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        "symbol_pnl_6976": sym_pnl.get("6976", 0.0),
        "symbol_pnl_4062": sym_pnl.get("4062", 0.0),
        "top_day_share": top_day,
        "top_symbol_share": top_sym,
        **{f"captured_{s.replace('.T', '')}": s in accepted_syms for s in CAPTURE},
    }
    if baseline:
        row["delta_pnl_vs_pullback"] = round(float(row["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2)
    else:
        row["delta_pnl_vs_pullback"] = 0.0
    return row


def _run_replay(
    variant: str,
    pass_fn: Callable[[Mapping[str, Any]], bool],
    *,
    replay_pool: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
    baseline: Optional[Mapping[str, Any]] = None,
) -> tuple[dict[str, Any], Any]:
    st = simulate_capacity_replay(
        replay_pool,
        np_shadows,
        mode=f"{REPLAY_MODE}_p465_{variant}",
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
    )
    return _replay_metrics(st, variant=variant, baseline=baseline), st


def _load_replay_pool(reports: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = reports / ".phase463_cache" / "population.pkl"
    if not path.is_file():
        raise FileNotFoundError("phase463 cache required; run phase463 first")
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    replay_pool = list(payload["replay_pool"])
    np_shadows = dict(payload.get("np_shadows") or {})
    return replay_pool, np_shadows


def _parallel_gate_worker(args: tuple[str, str]) -> dict[str, Any]:
    import sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[2]
    kabu = _Path(__file__).resolve().parents[1]
    for p in (kabu / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    gate_id, cache_path = args
    with Path(cache_path).open("rb") as fh:
        payload = pickle.load(fh)
    replay_pool = payload["replay_pool"]
    np_shadows = payload["np_shadows"]
    _, gate_fn = GATE_SPECS[gate_id]
    trend_fn = _make_trend_only(gate_fn)
    row, _ = _run_replay(gate_id, trend_fn, replay_pool=replay_pool, np_shadows=np_shadows)
    cond, _ = GATE_SPECS[gate_id]
    row["gate_id"] = gate_id
    row["conditions"] = cond
    row["trend_only_pnl_yen"] = row.pop("total_pnl_yen")
    row["trend_only_pf"] = row.pop("profit_factor")
    row["trend_only_maxdd_yen"] = row.pop("max_drawdown_yen")
    row["trend_only_accepted"] = row.pop("accepted_count")
    row["trend_only_stop_rate"] = row.pop("stop_rate")
    return row


def _verdict(
    *,
    best_gate: Mapping[str, Any],
    trend_only_pnl: float,
    dual_row: Mapping[str, Any],
    pullback_row: Mapping[str, Any],
    overfit: bool,
) -> str:
    dual_pnl = float(dual_row.get("total_pnl_yen") or 0)
    pb_pnl = float(pullback_row.get("total_pnl_yen") or 0)
    if overfit:
        return "overfit_trend"
    if trend_only_pnl <= 0 and dual_pnl <= pb_pnl:
        return "trend_no_edge"
    if dual_pnl > pb_pnl + 5000:
        return "dual_entry_candidate"
    if trend_only_pnl > 0 and float(best_gate.get("trend_only_pf") or 0) >= 1.2:
        return "trend_gate_candidate"
    return "trend_no_edge"


def run_phase465_tournament(
    *,
    repo_root: Path,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    pop = _load_population_cache(reports)
    if not pop:
        raise FileNotFoundError("phase464 cache required; run phase464 first")
    raw_candidates, _, pop_meta = pop
    all_ann = _annotate_candidates(raw_candidates, price_idx=price_idx)
    trend = [dict(t, **_feature_row(t)) for t in all_ann if _is_trend_following(t) and not t.get("data_stale")]
    print(f"phase465 trend-following cohort: {len(trend)}", flush=True)

    # Part A — distributions
    numeric_feats = (
        "r5",
        "r10",
        "r15",
        "r30",
        "vwap_dev_pct",
        "vwap_above_ratio",
        "consecutive_above_ticks",
        "day_high_distance",
        "high_update_count_30m",
        "high_update_count_session",
        "board_imbalance",
    )
    part_a = [_dist_row(f, [_float(t.get(f)) for t in trend if _float(t.get(f)) is not None]) for f in numeric_feats]
    board_dist = Counter(str(t.get("board_bucket") or "unknown") for t in trend)
    part_a.append({"feature": "board_bucket", "distribution": dict(board_dist), "count": len(trend)})

    # Part B — winner vs loser
    for t in trend:
        t["would_pnl"] = _float(t.get("would_pnl_close_proxy")) or _close_proxy_pnl(t, price_idx)
    winners = [t for t in trend if float(t.get("would_pnl") or 0) > 0]
    losers = [t for t in trend if float(t.get("would_pnl") or 0) < 0]
    compare_rows: list[dict[str, Any]] = []
    for feat in numeric_feats:
        wv = [_float(t.get(feat)) for t in winners]
        lv = [_float(t.get(feat)) for t in losers]
        wv = [x for x in wv if x is not None]
        lv = [x for x in lv if x is not None]
        wm = statistics.mean(wv) if wv else None
        lm = statistics.mean(lv) if lv else None
        compare_rows.append(
            {
                "feature": feat,
                "winner_mean": round(wm, 4) if wm is not None else None,
                "loser_mean": round(lm, 4) if lm is not None else None,
                "delta_mean": round(wm - lm, 4) if wm is not None and lm is not None else None,
                "effect_size_cohens_d": _cohens_d(wv, lv),
                "mutual_information": _mi_median_split(wv, lv),
            }
        )
    compare_rows = [r for r in compare_rows if r.get("effect_size_cohens_d") is not None]
    compare_rows.sort(key=lambda r: abs(float(r.get("effect_size_cohens_d") or 0)), reverse=True)
    for i, r in enumerate(compare_rows, start=1):
        r["rank"] = i

    replay_pool, np_shadows = _load_replay_pool(reports)
    np_shadows = _fill_close_proxy_shadows(replay_pool, np_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, np_shadows)

    # Part C/D — gate tournament (trend-only replay)
    gate_rows: list[dict[str, Any]] = []
    cache_path = reports / ".phase465_cache" / "replay.pkl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as fh:
        pickle.dump({"replay_pool": replay_pool, "np_shadows": np_shadows}, fh, protocol=pickle.HIGHEST_PROTOCOL)

    if parallel:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_parallel_gate_worker, (gid, str(cache_path))) for gid in GATE_SPECS]
            for fut in as_completed(futs):
                gate_rows.append(fut.result())
    else:
        for gate_id, (cond, gate_fn) in GATE_SPECS.items():
            trend_fn = _make_trend_only(gate_fn)
            row, _ = _run_replay(gate_id, trend_fn, replay_pool=replay_pool, np_shadows=np_shadows)
            gate_rows.append(
                {
                    "gate_id": gate_id,
                    "conditions": cond,
                    "trend_only_pnl_yen": row["total_pnl_yen"],
                    "trend_only_pf": row["profit_factor"],
                    "trend_only_maxdd_yen": row["max_drawdown_yen"],
                    "trend_only_accepted": row["accepted_count"],
                    "trend_only_stop_rate": row["stop_rate"],
                }
            )

    # cohort proxy stats per gate
    for gr in gate_rows:
        gid = gr["gate_id"]
        _, gate_fn = GATE_SPECS[gid]
        sub = [t for t in trend if gate_fn(t)]
        pnls = [_float(t.get("would_pnl") or 0) for t in sub]
        gr["median_would_pnl"] = round(statistics.median(pnls), 2) if pnls else 0.0
        gr["win_rate_proxy"] = round(sum(1 for p in pnls if p > 0) / max(len(pnls), 1), 4)

    gate_rows.sort(key=lambda r: float(r.get("trend_only_pnl_yen") or 0), reverse=True)
    for i, r in enumerate(gate_rows, start=1):
        r["rank_by_pnl"] = i

    best_gate_id = gate_rows[0]["gate_id"]
    _, best_gate_fn = GATE_SPECS[best_gate_id]
    best_trend_fn = _make_trend_only(best_gate_fn)

    # Part E — dual architecture
    pullback_row, _ = _run_replay(
        "A_pullback_runtime",
        pass_a0_baseline,
        replay_pool=replay_pool,
        np_shadows=np_shadows,
    )
    trend_row, trend_st = _run_replay(
        "B_trend_gate",
        best_trend_fn,
        replay_pool=replay_pool,
        np_shadows=np_shadows,
        baseline=pullback_row,
    )
    dual_row, _ = _run_replay(
        "C_pullback_or_trend",
        _make_dual(best_trend_fn),
        replay_pool=replay_pool,
        np_shadows=np_shadows,
        baseline=pullback_row,
    )
    dual_rows = [pullback_row, trend_row, dual_row]

    # Part F — robustness
    robust_rows: list[dict[str, Any]] = []
    full_pnl = float(trend_row.get("total_pnl_yen") or 0)

    days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    for day in days:
        pool = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        row, _ = _run_replay(f"LOO_{day}", best_trend_fn, replay_pool=pool, np_shadows=np_shadows)
        robust_rows.append(
            {
                "test": f"LOO_{day}",
                "gate_or_variant": best_gate_id,
                "total_pnl_yen": row["total_pnl_yen"],
                "profit_factor": row["profit_factor"],
                "max_drawdown_yen": row["max_drawdown_yen"],
                "accepted_count": row["accepted_count"],
                "delta_pnl_vs_full": round(float(row["total_pnl_yen"]) - full_pnl, 2),
                "top_day_share": row["top_day_share"],
                "top_symbol_share": row["top_symbol_share"],
            }
        )

    robust_rows.append(
        {
            "test": "full",
            "gate_or_variant": best_gate_id,
            "total_pnl_yen": trend_row["total_pnl_yen"],
            "profit_factor": trend_row["profit_factor"],
            "max_drawdown_yen": trend_row["max_drawdown_yen"],
            "accepted_count": trend_row["accepted_count"],
            "delta_pnl_vs_full": 0.0,
            "top_day_share": trend_row["top_day_share"],
            "top_symbol_share": trend_row["top_symbol_share"],
        }
    )

    for sym in SYMBOL_EXCLUDE_TESTS:
        pool = [t for t in replay_pool if str(t.get("symbol") or "") != sym]
        row, _ = _run_replay(f"exclude_{sym.replace('.T','')}", best_trend_fn, replay_pool=pool, np_shadows=np_shadows)
        robust_rows.append(
            {
                "test": f"exclude_{sym.replace('.T', '')}",
                "gate_or_variant": best_gate_id,
                "total_pnl_yen": row["total_pnl_yen"],
                "profit_factor": row["profit_factor"],
                "max_drawdown_yen": row["max_drawdown_yen"],
                "accepted_count": row["accepted_count"],
                "delta_pnl_vs_full": round(float(row["total_pnl_yen"]) - full_pnl, 2),
                "top_day_share": row["top_day_share"],
                "top_symbol_share": row["top_symbol_share"],
            }
        )

    overfit = float(trend_row.get("top_day_share") or 0) > 0.5 or float(trend_row.get("top_symbol_share") or 0) > 0.5
    loo_pnls = [float(r["total_pnl_yen"]) for r in robust_rows if str(r["test"]).startswith("LOO_")]
    if loo_pnls and min(loo_pnls) < 0 and full_pnl > 0:
        overfit = True

    verdict = _verdict(
        best_gate=gate_rows[0],
        trend_only_pnl=float(trend_row.get("total_pnl_yen") or 0),
        dual_row=dual_row,
        pullback_row=pullback_row,
        overfit=overfit,
    )

    mandatory = {
        "1_best_trend_gate": best_gate_id,
        "2_trend_only_pnl": trend_row.get("total_pnl_yen"),
        "3_trend_only_pf": trend_row.get("profit_factor"),
        "4_dual_pnl": dual_row.get("total_pnl_yen"),
        "5_dual_pf": dual_row.get("profit_factor"),
        "6_6976_impact": {
            "pullback": pullback_row.get("symbol_pnl_6976"),
            "trend": trend_row.get("symbol_pnl_6976"),
            "dual": dual_row.get("symbol_pnl_6976"),
        },
        "7_4062_impact": {
            "pullback": pullback_row.get("symbol_pnl_4062"),
            "trend": trend_row.get("symbol_pnl_4062"),
            "dual": dual_row.get("symbol_pnl_4062"),
        },
        "8_captured_3441": dual_row.get("captured_3441"),
        "9_captured_6492": dual_row.get("captured_6492"),
        "10_captured_7256": dual_row.get("captured_7256"),
        "11_captured_7600": dual_row.get("captured_7600"),
        "12_overfit_risk": overfit,
        "13_runtime_candidate": verdict in ("trend_gate_candidate", "dual_entry_candidate"),
        "14_shadow_candidate": best_gate_id if verdict != "trend_no_edge" else None,
        "15_next_actions": [
            f"Paper shadow {best_gate_id} trend-only path",
            "Dual OR only if dual beats pullback by >5k with LOO stable",
            "Near-high exception for 6/19 uptrend if dual misses capture",
        ],
        "verdict": verdict,
        "trend_cohort_count": len(trend),
        "best_gate_conditions": GATE_SPECS[best_gate_id][0],
        "part_b_top10": compare_rows[:10],
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "part_a_distributions": part_a,
        "part_b_compare": compare_rows,
        "_gate_rows": gate_rows,
        "_dual_rows": dual_rows,
        "_robust_rows": robust_rows,
        **pop_meta,
    }


@dataclass
class Phase465Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase465_tournament(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "tournament": reports / "phase465_trend_gate_tournament.csv",
            "dual": reports / "phase465_trend_dual_replay.csv",
            "robustness": reports / "phase465_trend_robustness.csv",
            "summary": reports / "phase465_summary.json",
        }
        _write_csv(paths["tournament"], GATE_TOURNAMENT_FIELDS, list(result.get("_gate_rows") or []))
        _write_csv(paths["dual"], DUAL_REPLAY_FIELDS, list(result.get("_dual_rows") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robust_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase465_trend_gate_tournament.md"
        m = result.get("mandatory_answers") or {}
        gates = list(result.get("_gate_rows") or [])
        dual = list(result.get("_dual_rows") or [])
        lines = [
            "# Phase465 — Trend Entry Gate Tournament",
            "",
            f"Generated: {result.get('generated_at')}",
            f"Period: {result.get('period_start')}..{result.get('period_end')}",
            f"Trend-following cohort: **{m.get('trend_cohort_count')}**",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Gate tournament (trend-only replay)",
            "",
            "| rank | gate | PnL | PF | maxDD | accepted |",
            "|---:|---|---:|---:|---:|---:|",
        ]
        for r in gates:
            lines.append(
                f"| {r.get('rank_by_pnl')} | {r.get('gate_id')} | {r.get('trend_only_pnl_yen')} "
                f"| {r.get('trend_only_pf')} | {r.get('trend_only_maxdd_yen')} | {r.get('trend_only_accepted')} |"
            )
        lines.extend(["", "## Dual architecture", "", "| variant | PnL | PF | Δvs PB |", "|---|---:|---:|---:|"])
        for r in dual:
            lines.append(
                f"| {r.get('variant')} | {r.get('total_pnl_yen')} | {r.get('profit_factor')} "
                f"| {r.get('delta_pnl_vs_pullback')} |"
            )
        lines.extend(
            [
                "",
                "## Mandatory answers",
                "",
                f"1. Best gate: **{m.get('1_best_trend_gate')}** ({m.get('best_gate_conditions')})",
                f"2. Trend-only PnL: **{m.get('2_trend_only_pnl')}**",
                f"3. Trend-only PF: **{m.get('3_trend_only_pf')}**",
                f"4. Dual PnL: **{m.get('4_dual_pnl')}**",
                f"5. Dual PF: **{m.get('5_dual_pf')}**",
                f"12. Overfit: **{m.get('12_overfit_risk')}**",
                f"13. Runtime candidate: **{m.get('13_runtime_candidate')}**",
                f"14. Shadow: **{m.get('14_shadow_candidate')}**",
            ]
        )
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
