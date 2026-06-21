"""
Phase464 — Pre-Gate Archetype Audit (research only).

Classifies Dynamic40 candidates BEFORE Momentum/Board/Drift/Shape gates.
"""

from __future__ import annotations

import json
import pickle
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv, read_jpx_sector_map
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before, guard_high_drift
from research.phase440_boundary_capacity_audit import ShadowExitInfo
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
    _optional_float,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import (
    _board_token,
    _passes_baseline_mid_high,
    _v2_entry_score,
)
from research.phase456_entry_features import enrich_trade_phase456_features
from research.phase456c_vwap_structure_features import enrich_trade_phase456c_features
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase460_entry_gate_failure_audit import _load_dynamic40_records
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _is_actionable_record,
    _should_inject_for_replay,
    pass_a0_baseline,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    board_mid_or_high_required_for_v2,
    momentum_low_required_for_v2,
)
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

JST = ZoneInfo("Asia/Tokyo")
REPLAY_MODE = "phase456_runtime_np"
MISSED_619 = ("3441.T", "6492.T", "7256.T", "6466.T", "7600.T")
CAPTURE_SYMBOLS = ("6976.T", "4062.T", "3441.T", "6492.T", "7256.T", "7600.T")

ARCHETYPE_ORDER = (
    "Trend-following",
    "Near-high continuation",
    "Pullback-reversal",
    "VWAP-stable",
    "Range/Other",
)

AUDIT_ROW_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "primary_label",
    "all_labels",
    "would_pnl_close_proxy",
    "outcome",
    "momentum_pass",
    "board_pass",
    "runtime_pass",
    "gate_reject_reason",
]

GATE_PASS_FIELDS = [
    "primary_label",
    "candidate_count",
    "momentum_gate_pass_rate",
    "board_gate_pass_rate",
    "near_day_high_guard_reject_rate",
    "high_drift_reject_rate",
    "weak_shape_reject_rate",
    "accepted_rate",
]

REPLAY_FIELDS = [
    "variant",
    "accepted_count",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "stop_rate",
    "daily_pnl_618",
    "daily_pnl_619",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_7600",
    "delta_pnl_vs_a",
]

MISSED_FIELDS = [
    "symbol",
    "day",
    "was_candidate",
    "primary_label",
    "all_labels",
    "blocked_gate",
    "rescue_captures",
    "would_pnl_close_proxy",
    "replay_pnl_baseline",
]

_CACHE_VERSION = 1


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


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


def _rise(trade: Mapping[str, Any], mins: int) -> Optional[float]:
    return _float(trade.get(f"return_{mins}min_pct")) or _float(trade.get(f"entry_rise_{mins}min_pct"))


def _vwap_above_ratio(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("vwap_above_ratio")) or _float(trade.get("vwap_above_ratio_20tick"))


def _vwap_dev(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct"))


def _day_high_distance(trade: Mapping[str, Any]) -> Optional[float]:
    return abs(
        _optional_float(trade.get("day_high_distance_pct"))
        or _optional_float(trade.get("entry_near_day_high_pct"))
        or 0.0
    )


def _weak_shape_block(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def _passes_board_gate(trade: Mapping[str, Any]) -> bool:
    tok = _board_token(trade) or ""
    if tok == "Board:high":
        return True
    if tok == "Board:mid":
        return _v2_entry_score(trade) >= ENTRY_SCORE_V2_GATE_MIN
    return False


def _pass_runtime_core_no_momentum(trade: Mapping[str, Any]) -> bool:
    if not _passes_board_gate(trade):
        return False
    if guard_high_drift(trade):
        return False
    if _weak_shape_block(trade):
        return False
    return True


def _is_trend_following(trade: Mapping[str, Any]) -> bool:
    r15 = _rise(trade, 15)
    r30 = _rise(trade, 30)
    vwap = _vwap_above_ratio(trade) or 0
    hu = _float(trade.get("high_update_count_30m")) or 0
    if r15 is not None and r30 is not None and r15 > 0 and r30 > 0 and vwap >= 0.7:
        return True
    return hu >= 2


def _is_pullback_reversal(trade: Mapping[str, Any]) -> bool:
    r30 = _rise(trade, 30)
    r5 = _rise(trade, 5)
    if r30 is None or r5 is None or r30 >= 0 or r5 <= 0:
        return False
    if trade.get("vwap_failed_reclaim_flag"):
        return False
    vwap_dev = _vwap_dev(trade)
    vwap_above = _vwap_above_ratio(trade)
    recovering = (vwap_dev is not None and vwap_dev < 0) or (vwap_above is not None and vwap_above < 0.5)
    return recovering


def _is_near_high_continuation(trade: Mapping[str, Any]) -> bool:
    dist = _day_high_distance(trade)
    r5 = _rise(trade, 5)
    return dist is not None and dist <= 1.5 and r5 is not None and r5 > 0


def _is_vwap_stable(trade: Mapping[str, Any]) -> bool:
    return (_vwap_above_ratio(trade) or 0) >= 0.7 and (_float(trade.get("consecutive_above_ticks")) or 0) >= 20


def _archetype_labels(trade: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    if _is_trend_following(trade):
        labels.append("Trend-following")
    if _is_near_high_continuation(trade):
        labels.append("Near-high continuation")
    if _is_pullback_reversal(trade):
        labels.append("Pullback-reversal")
    if _is_vwap_stable(trade):
        labels.append("VWAP-stable")
    if not labels:
        labels.append("Range/Other")
    return labels


def _primary_label(labels: Sequence[str]) -> str:
    for name in ARCHETYPE_ORDER:
        if name in labels:
            return name
    return "Range/Other"


def _close_proxy_pnl(
    trade: Mapping[str, Any],
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> float:
    sym = str(trade.get("symbol") or "")
    day = str(trade.get("day") or "")[:8]
    ep = _float(trade.get("entry_price"))
    if not sym or not day or ep is None or ep <= 0:
        return 0.0
    series = price_idx.get((sym, day), [])
    if not series:
        return 0.0
    close_dt = datetime.strptime(f"{day} 15:30:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)
    close_px = _price_at_or_before(series, close_dt)
    if close_px is None or close_px <= 0:
        return 0.0
    return round((close_px - ep) * 100.0, 2)


def _enrich_full(
    rows: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list],
    sector_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    total = len(rows)
    for i, raw in enumerate(rows, start=1):
        if total >= 5000 and i % 5000 == 0:
            print(f"phase464 enrich {i}/{total}", flush=True)
        t = _map_runtime_fields(dict(raw))
        t.setdefault("day", raw.get("day"))
        px = _float(t.get("entry_price")) or _float(t.get("current_price"))
        if px:
            t["entry_price"] = px
        t.update(enrich_trade_phase456_features(t, price_idx=price_idx, sector_map=sector_map))
        t.update(enrich_trade_phase456c_features(t, price_idx=price_idx))
        out.append(t)
    return out


def _cache_dir(reports: Path) -> Path:
    d = reports / ".phase464_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_population_cache(reports: Path) -> Optional[tuple[list[dict], list[dict], dict]]:
    path = _cache_dir(reports) / "population.pkl"
    if not path.is_file():
        return None
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    if payload.get("version") != _CACHE_VERSION:
        return None
    return payload["candidates"], payload["replay_pool"], payload.get("meta") or {}


def _save_population_cache(
    reports: Path,
    *,
    candidates: list[dict],
    replay_pool: list[dict],
    meta: dict[str, Any],
) -> None:
    path = _cache_dir(reports) / "population.pkl"
    with path.open("wb") as fh:
        pickle.dump(
            {"version": _CACHE_VERSION, "candidates": candidates, "replay_pool": replay_pool, "meta": meta},
            fh,
            protocol=pickle.HIGHEST_PROTOCOL,
        )


def _load_phase463_enriched_lookup(reports: Path) -> dict[str, dict[str, Any]]:
    path = reports / ".phase463_cache" / "population.pkl"
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            payload = pickle.load(fh)
    except (OSError, pickle.UnpicklingError):
        return {}
    if payload.get("version") not in (3, 4):
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for key in ("replay_pool", "candidates"):
        for t in payload.get(key) or []:
            lookup[_position_key(t)] = dict(t)
    return lookup


def _build_population(
    *,
    repo_root: Path,
    kabu: Path,
    price_idx: Mapping[tuple[str, str], list],
    sector_map: Mapping[str, str],
    reports: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    all_d40 = _load_dynamic40_records(kabu)
    enriched_canon = _enrich_candidates(_load_candidate_stream(repo_root), kabu=kabu)
    canon_by_key = {_position_key(t): dict(t) for t in enriched_canon}
    canon_keys = set(canon_by_key)
    prior_enriched = _load_phase463_enriched_lookup(reports)

    actionable_raw = [r for r in all_d40 if _is_actionable_record(r)]
    stale_raw = [r for r in all_d40 if not _is_actionable_record(r)]

    to_enrich: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for rec in actionable_raw:
        key = _position_key(rec)
        if key in prior_enriched:
            t = dict(prior_enriched[key])
            t.update({k: v for k, v in rec.items() if v not in (None, "")})
            candidates.append(t)
        elif key in canon_by_key:
            t = dict(canon_by_key[key])
            t.update({k: v for k, v in rec.items() if v not in (None, "")})
            candidates.append(t)
        else:
            to_enrich.append(dict(rec))

    print(
        f"phase464 load: dynamic40={len(all_d40)} actionable={len(actionable_raw)} "
        f"prior_hit={len(prior_enriched)} to_enrich={len(to_enrich)} stale={len(stale_raw)}",
        flush=True,
    )
    candidates.extend(_enrich_full(to_enrich, price_idx=price_idx, sector_map=sector_map))

    for rec in stale_raw:
        key = _position_key(rec)
        t = dict(canon_by_key.get(key, rec))
        t.update({k: v for k, v in rec.items() if v not in (None, "")})
        t["primary_label"] = "Range/Other"
        t["all_labels"] = ["Range/Other"]
        t["data_stale"] = True
        candidates.append(t)

    inject_raw = [
        r for r in actionable_raw if _position_key(r) not in canon_keys and _should_inject_for_replay(r)
    ]
    inject_keys = {_position_key(r) for r in inject_raw}
    replay_pool = [t for t in candidates if _position_key(t) in canon_keys or _position_key(t) in inject_keys]
    replay_pool.sort(key=lambda r: (str(r.get("day") or ""), str(r.get("entry_time") or ""), str(r.get("symbol") or "")))

    meta = {
        "dynamic40_total": len(all_d40),
        "dynamic40_actionable": len(actionable_raw),
        "dynamic40_stale": len(stale_raw),
        "enriched_actionable": len(actionable_raw),
        "replay_pool_count": len(replay_pool),
    }
    return candidates, replay_pool, meta


def _annotate_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in candidates:
        t = dict(raw)
        if t.get("data_stale"):
            out.append(t)
            continue
        labels = _archetype_labels(t)
        t["all_labels"] = labels
        t["primary_label"] = _primary_label(labels)
        t["would_pnl_close_proxy"] = _close_proxy_pnl(t, price_idx)
        t["momentum_pass"] = momentum_low_required_for_v2(t)
        t["board_pass"] = _passes_board_gate(t)
        t["near_day_high_reject"] = phase364_blocked_only(t)
        t["high_drift_reject"] = guard_high_drift(t)
        t["weak_shape_reject"] = _weak_shape_block(t)
        t["runtime_pass"] = pass_a0_baseline(t)
        out.append(t)
    return out


def _pass_trend_rescue(trade: Mapping[str, Any]) -> bool:
    if pass_a0_baseline(trade):
        return True
    if not _is_trend_following(trade):
        return False
    if not _pass_runtime_core_no_momentum(trade):
        return False
    return not phase364_blocked_only(trade)


def _pass_pullback_rescue(trade: Mapping[str, Any]) -> bool:
    if pass_a0_baseline(trade):
        return True
    if not _is_pullback_reversal(trade):
        return False
    if not _pass_runtime_core_no_momentum(trade):
        return False
    return not phase364_blocked_only(trade)


def _pass_near_high_rescue(trade: Mapping[str, Any]) -> bool:
    if pass_a0_baseline(trade):
        return True
    if not _is_near_high_continuation(trade):
        return False
    if not _pass_runtime_core_no_momentum(trade):
        return False
    if phase364_blocked_only(trade) and (_rise(trade, 5) or -1e18) <= 0:
        return False
    return True


def _pass_vwap_stable_rescue(trade: Mapping[str, Any]) -> bool:
    if pass_a0_baseline(trade):
        return True
    if not _is_vwap_stable(trade):
        return False
    if not _pass_runtime_core_no_momentum(trade):
        return False
    return not phase364_blocked_only(trade)


def _make_best_two_rescue(labels: set[str]) -> Callable[[Mapping[str, Any]], bool]:
    def fn(trade: Mapping[str, Any]) -> bool:
        if pass_a0_baseline(trade):
            return True
        primary = _primary_label(_archetype_labels(trade))
        if primary not in labels:
            return False
        if not _pass_runtime_core_no_momentum(trade):
            return False
        if phase364_blocked_only(trade) and primary == "Near-high continuation":
            return (_rise(trade, 5) or -1e18) > 0
        return not phase364_blocked_only(trade)

    return fn


def _entry_block(pass_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    return lambda t: not pass_fn(t)


def _replay_metrics(state: Any, *, variant: str, baseline: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "") for r in state.trade_log}
    row = {
        "variant": variant,
        "accepted_count": state.accepted_trade_count,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        "symbol_pnl_6976": sym_pnl.get("6976", 0.0),
        "symbol_pnl_4062": sym_pnl.get("4062", 0.0),
        **{f"captured_{s.replace('.T', '')}": s in accepted_syms for s in ("3441.T", "6492.T", "7256.T", "7600.T")},
    }
    if baseline:
        row["delta_pnl_vs_a"] = round(float(row["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2)
    else:
        row["delta_pnl_vs_a"] = 0.0
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
        mode=f"{REPLAY_MODE}_{variant}",
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
    )
    return _replay_metrics(st, variant=variant, baseline=baseline), st


REPLAY_PASS_FNS: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "A_baseline_current_runtime": pass_a0_baseline,
    "B_trend_following_rescue": _pass_trend_rescue,
    "C_pullback_reversal_rescue": _pass_pullback_rescue,
    "D_near_high_continuation_rescue": _pass_near_high_rescue,
    "E_vwap_stable_rescue": _pass_vwap_stable_rescue,
}


def _parallel_replay_worker(args: tuple[str, str, str, tuple[str, ...]]) -> dict[str, Any]:
    import sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[2]
    kabu = _Path(__file__).resolve().parents[1]
    for p in (kabu / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    variant, cache_path, baseline_variant, best_two_labels = args
    with Path(cache_path).open("rb") as fh:
        payload = pickle.load(fh)
    replay_pool = payload["replay_pool"]
    np_shadows = payload["np_shadows"]
    baseline = payload.get("baselines", {}).get(baseline_variant)
    if variant == "F_best_two_archetype_rescue":
        pass_fn = _make_best_two_rescue(set(best_two_labels))
    else:
        pass_fn = REPLAY_PASS_FNS[variant]
    return _run_replay(variant, pass_fn, replay_pool=replay_pool, np_shadows=np_shadows, baseline=baseline)[0]


def _part_b_rows(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in candidates:
        if t.get("data_stale"):
            continue
        by_label[str(t.get("primary_label") or "Range/Other")].append(dict(t))

    rows: list[dict[str, Any]] = []
    for label in ARCHETYPE_ORDER:
        grp = by_label.get(label, [])
        if not grp:
            continue
        pnls = [_float(t.get("would_pnl_close_proxy")) or 0.0 for t in grp]
        wins = [p for p in pnls if p > 0]
        accepted = [t for t in grp if str(t.get("outcome") or "") == "accepted"]
        acc_pnls = [_float(t.get("pnl_yen")) or _float(t.get("would_pnl_close_proxy")) or 0.0 for t in accepted]
        rejects = [t for t in grp if str(t.get("outcome") or "") != "accepted"]
        reason_ctr = Counter(
            str(t.get("gate_reject_reason") or t.get("reject_reason") or "")[:80]
            for t in rejects
            if str(t.get("gate_reject_reason") or t.get("reject_reason") or "")
        )
        top5 = [r for r, _ in reason_ctr.most_common(5)]
        sym_days = {(str(t.get("symbol") or ""), str(t.get("day") or "")[:8]) for t in grp}
        rows.append(
            {
                "primary_label": label,
                "candidate_count": len(grp),
                "symbol_day_count": len(sym_days),
                "would_pnl_close_proxy": round(sum(pnls), 2),
                "median_would_pnl": round(statistics.median(pnls), 2) if pnls else 0.0,
                "win_rate": round(len(wins) / max(len(pnls), 1), 4),
                "pf_proxy": _pf(pnls),
                "actual_accepted_count": len(accepted),
                "actual_accepted_pnl": round(sum(acc_pnls), 2),
                "gate_reject_count": len(rejects),
                **{f"gate_reject_top{i}": top5[i - 1] if len(top5) >= i else "" for i in range(1, 6)},
            }
        )
    return rows


def _part_c_rows(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in candidates:
        if t.get("data_stale"):
            continue
        by_label[str(t.get("primary_label") or "Range/Other")].append(dict(t))

    rows: list[dict[str, Any]] = []
    for label in ARCHETYPE_ORDER:
        grp = by_label.get(label, [])
        if not grp:
            continue
        n = len(grp)
        rows.append(
            {
                "primary_label": label,
                "candidate_count": n,
                "momentum_gate_pass_rate": round(sum(1 for t in grp if t.get("momentum_pass")) / n, 4),
                "board_gate_pass_rate": round(sum(1 for t in grp if t.get("board_pass")) / n, 4),
                "near_day_high_guard_reject_rate": round(
                    sum(1 for t in grp if t.get("near_day_high_reject")) / n, 4
                ),
                "high_drift_reject_rate": round(sum(1 for t in grp if t.get("high_drift_reject")) / n, 4),
                "weak_shape_reject_rate": round(sum(1 for t in grp if t.get("weak_shape_reject")) / n, 4),
                "accepted_rate": round(sum(1 for t in grp if str(t.get("outcome") or "") == "accepted") / n, 4),
            }
        )
    return rows


def _verdict(
    *,
    part_b: Sequence[Mapping[str, Any]],
    part_c: Sequence[Mapping[str, Any]],
    replay_rows: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
) -> str:
    if not part_b:
        return "no_pre_gate_edge"
    best = max(part_b, key=lambda r: float(r.get("would_pnl_close_proxy") or 0))
    worst = min(part_b, key=lambda r: float(r.get("would_pnl_close_proxy") or 0))
    best_label = str(best.get("primary_label") or "")
    worst_pnl = float(worst.get("would_pnl_close_proxy") or 0)

    trend_row = next((r for r in part_b if r.get("primary_label") == "Trend-following"), None)
    trend_pnl = float(trend_row.get("would_pnl_close_proxy") or 0) if trend_row else 0.0
    trend_mom = float(
        next((r for r in part_c if r.get("primary_label") == "Trend-following"), {}).get("momentum_gate_pass_rate") or 0
    )

    rescue_delta = max(float(r.get("delta_pnl_vs_a") or 0) for r in replay_rows if r.get("variant") != "A_baseline")

    if trend_pnl > 0 and trend_mom < 0.3 and rescue_delta > 5000:
        return "gate_bias_problem"
    mapping = {
        "Trend-following": "trend_pre_gate_edge",
        "Pullback-reversal": "pullback_pre_gate_edge",
        "Near-high continuation": "near_high_continuation_edge",
        "VWAP-stable": "vwap_stable_edge",
    }
    if float(best.get("would_pnl_close_proxy") or 0) > 0 and rescue_delta > 5000:
        return mapping.get(best_label, "gate_bias_problem")
    if worst_pnl < -1_000_000 and trend_mom < 0.25:
        return "gate_bias_problem"
    if float(best.get("would_pnl_close_proxy") or 0) <= 0:
        return "no_pre_gate_edge"
    return mapping.get(best_label, "no_pre_gate_edge")


def run_phase464_audit(
    *,
    repo_root: Path,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    sector_map = read_jpx_sector_map(kabu)

    cached = _load_population_cache(reports)
    if cached:
        raw_candidates, replay_pool, pop_meta = cached
        print(f"phase464 cache hit candidates={len(raw_candidates)} replay={len(replay_pool)}", flush=True)
    else:
        raw_candidates, replay_pool, pop_meta = _build_population(
            repo_root=repo_root, kabu=kabu, price_idx=price_idx, sector_map=sector_map, reports=reports
        )
        _save_population_cache(reports, candidates=raw_candidates, replay_pool=replay_pool, meta=pop_meta)

    candidates = _annotate_candidates(raw_candidates, price_idx=price_idx)

    np_shadows = _precompute_np_shadows(replay_pool, kabu=kabu, np_policy=BEST_NP_POLICY)
    np_shadows = _fill_close_proxy_shadows(replay_pool, np_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, np_shadows)

    part_b = _part_b_rows(candidates)
    part_c = _part_c_rows(candidates)

    best_two = sorted(part_b, key=lambda r: float(r.get("would_pnl_close_proxy") or 0), reverse=True)[:2]
    best_two_labels = {str(r.get("primary_label") or "") for r in best_two}
    replay_variants = {
        **REPLAY_PASS_FNS,
        "F_best_two_archetype_rescue": _make_best_two_rescue(best_two_labels),
    }

    replay_rows: list[dict[str, Any]] = []
    baseline_row, baseline_state = _run_replay(
        "A_baseline_current_runtime",
        pass_a0_baseline,
        replay_pool=replay_pool,
        np_shadows=np_shadows,
        baseline=None,
    )
    replay_rows.append(baseline_row)

    rest = [v for v in replay_variants if v != "A_baseline_current_runtime"]
    if parallel and len(rest) > 1:
        cache_path = _cache_dir(reports) / "replay.pkl"
        with cache_path.open("wb") as fh:
            pickle.dump(
                {
                    "replay_pool": replay_pool,
                    "np_shadows": np_shadows,
                    "baselines": {"A_baseline_current_runtime": baseline_row},
                },
                fh,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        args = [
            (v, str(cache_path), "A_baseline_current_runtime", tuple(best_two_labels)) for v in rest
        ]
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            for fut in as_completed(ex.submit(_parallel_replay_worker, a) for a in args):
                replay_rows.append(fut.result())
    else:
        for vid in rest:
            row, _ = _run_replay(
                vid,
                replay_variants[vid],
                replay_pool=replay_pool,
                np_shadows=np_shadows,
                baseline=baseline_row,
            )
            replay_rows.append(row)

    replay_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or 0), reverse=True)

    replay_pnl_by_key = {
        _position_key(dict(r.get("trade") or {})): _float(r.get("pnl_yen")) or 0.0
        for r in baseline_state.trade_log
    }

    missed_rows: list[dict[str, Any]] = []
    rescue_checks = {
        "B_trend_following_rescue": _pass_trend_rescue,
        "C_pullback_reversal_rescue": _pass_pullback_rescue,
        "D_near_high_continuation_rescue": _pass_near_high_rescue,
        "E_vwap_stable_rescue": _pass_vwap_stable_rescue,
        "F_best_two_archetype_rescue": _make_best_two_rescue(best_two_labels),
    }
    for sym in MISSED_619:
        day_cands = [
            t
            for t in candidates
            if str(t.get("symbol") or "") == sym and str(t.get("day") or "")[:8] == DAY_619 and not t.get("data_stale")
        ]
        if not day_cands:
            missed_rows.append(
                {
                    "symbol": sym,
                    "day": DAY_619,
                    "was_candidate": False,
                    "primary_label": "",
                    "all_labels": "",
                    "blocked_gate": "no_candidate",
                    "rescue_captures": "",
                    "would_pnl_close_proxy": 0.0,
                    "replay_pnl_baseline": 0.0,
                }
            )
            continue
        best = max(day_cands, key=lambda t: _float(t.get("would_pnl_close_proxy")) or 0.0)
        blocked = str(best.get("gate_reject_reason") or best.get("reject_reason") or "")
        if best.get("runtime_pass"):
            blocked = "accepted" if str(best.get("outcome") or "") == "accepted" else "runtime_pass_not_accepted"
        elif best.get("near_day_high_reject"):
            blocked = blocked or "near_day_high_guard"
        elif not best.get("momentum_pass"):
            blocked = blocked or "momentum_low_required"
        elif not best.get("board_pass"):
            blocked = blocked or "board_gate"
        rescues = [name for name, fn in rescue_checks.items() if fn(best)]
        missed_rows.append(
            {
                "symbol": sym,
                "day": DAY_619,
                "was_candidate": True,
                "primary_label": best.get("primary_label"),
                "all_labels": "|".join(best.get("all_labels") or []),
                "blocked_gate": blocked[:120],
                "rescue_captures": "|".join(rescues),
                "would_pnl_close_proxy": best.get("would_pnl_close_proxy"),
                "replay_pnl_baseline": replay_pnl_by_key.get(_position_key(best), 0.0),
            }
        )

    runtime_accepted = [t for t in candidates if t.get("runtime_pass")]
    runtime_by_arch = Counter(str(t.get("primary_label") or "Range/Other") for t in runtime_accepted)
    trend_blocked_mom = [
        t
        for t in candidates
        if t.get("primary_label") == "Trend-following" and not t.get("momentum_pass") and not t.get("data_stale")
    ]

    verdict = _verdict(part_b=part_b, part_c=part_c, replay_rows=replay_rows, baseline=baseline_row)
    best_arch = max(part_b, key=lambda r: float(r.get("would_pnl_close_proxy") or 0)) if part_b else {}
    worst_arch = min(part_b, key=lambda r: float(r.get("would_pnl_close_proxy") or 0)) if part_b else {}
    best_rescue = max(
        (r for r in replay_rows if r.get("variant") != "A_baseline_current_runtime"),
        key=lambda r: float(r.get("delta_pnl_vs_a") or 0),
        default={},
    )

    def _arch_pnl(name: str) -> float:
        row = next((r for r in part_b if r.get("primary_label") == name), None)
        return float(row.get("would_pnl_close_proxy") or 0) if row else 0.0

    mandatory = {
        "1_most_profitable_archetype": best_arch.get("primary_label"),
        "2_most_loss_archetype": worst_arch.get("primary_label"),
        "3_trend_following_profit_source": _arch_pnl("Trend-following") > 0,
        "4_pullback_reversal_profit_source": _arch_pnl("Pullback-reversal") > 0,
        "5_near_high_continuation_profit_source": _arch_pnl("Near-high continuation") > 0,
        "6_vwap_stable_profit_source": _arch_pnl("VWAP-stable") > 0,
        "7_momentum_gate_drops_archetype": "Trend-following"
        if len(trend_blocked_mom) > len(candidates) * 0.05
        else runtime_by_arch.most_common(1)[0][0] if runtime_by_arch else "unknown",
        "8_runtime_picks_archetype": runtime_by_arch.most_common(3),
        "9_619_missed_archetypes": {r["symbol"]: r.get("primary_label") for r in missed_rows},
        "10_rescue_improved_archetype": best_rescue.get("variant"),
        "11_runtime_candidate": best_rescue.get("variant") if float(best_rescue.get("delta_pnl_vs_a") or 0) > 5000 else None,
        "12_next_actions": [
            f"Shadow {best_rescue.get('variant')} if delta vs A > 5k",
            "Review momentum gate vs Trend-following pre-gate edge",
            "6/19 missed: near-high rescue path for uptrend symbols",
        ],
        "verdict": verdict,
        **pop_meta,
    }

    audit_rows = [
        {
            "symbol": t.get("symbol"),
            "day": t.get("day"),
            "entry_time": t.get("entry_time"),
            "primary_label": t.get("primary_label"),
            "all_labels": "|".join(t.get("all_labels") or []),
            "would_pnl_close_proxy": t.get("would_pnl_close_proxy"),
            "outcome": t.get("outcome"),
            "momentum_pass": t.get("momentum_pass"),
            "board_pass": t.get("board_pass"),
            "runtime_pass": t.get("runtime_pass"),
            "gate_reject_reason": t.get("gate_reject_reason") or t.get("reject_reason"),
        }
        for t in candidates
        if not t.get("data_stale")
    ]

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_audit_rows": audit_rows,
        "_part_b": part_b,
        "_part_c": part_c,
        "_replay_rows": replay_rows,
        "_missed_rows": missed_rows,
    }


@dataclass
class Phase464Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase464_audit(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "audit": reports / "phase464_pre_gate_archetype_audit.csv",
            "gate_rates": reports / "phase464_archetype_gate_pass_rates.csv",
            "replay": reports / "phase464_archetype_rescue_replay.csv",
            "missed": reports / "phase464_619_missed_archetypes.csv",
            "summary": reports / "phase464_summary.json",
        }
        _write_csv(paths["audit"], AUDIT_ROW_FIELDS, list(result.get("_audit_rows") or []))
        _write_csv(paths["gate_rates"], GATE_PASS_FIELDS, list(result.get("_part_c") or []))
        _write_csv(paths["replay"], REPLAY_FIELDS, list(result.get("_replay_rows") or []))
        _write_csv(paths["missed"], MISSED_FIELDS, list(result.get("_missed_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        payload["part_b_archetype_pnl"] = list(result.get("_part_b") or [])
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase464_pre_gate_archetype_audit.md"
        m = result.get("mandatory_answers") or {}
        part_b = list(result.get("_part_b") or [])
        replay = list(result.get("_replay_rows") or [])
        lines = [
            "# Phase464 — Pre-Gate Archetype Audit",
            "",
            f"Generated: {result.get('generated_at')}",
            f"Period: {result.get('period_start')}..{result.get('period_end')}",
            f"Dynamic40 total: **{m.get('dynamic40_total')}** | actionable: **{m.get('dynamic40_actionable')}**",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Part B — Archetype would PnL (close proxy)",
            "",
            "| label | count | would_pnl | median | win_rate | PF | accepted |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for r in part_b:
            lines.append(
                f"| {r.get('primary_label')} | {r.get('candidate_count')} | {r.get('would_pnl_close_proxy')} "
                f"| {r.get('median_would_pnl')} | {r.get('win_rate')} | {r.get('pf_proxy')} "
                f"| {r.get('actual_accepted_count')} |"
            )
        lines.extend(["", "## Part D — Rescue replay", "", "| variant | PnL | Δvs A | accepted |", "|---|---:|---:|---:|"])
        for r in sorted(replay, key=lambda x: float(x.get("total_pnl_yen") or 0), reverse=True):
            lines.append(
                f"| {r.get('variant')} | {r.get('total_pnl_yen')} | {r.get('delta_pnl_vs_a')} | {r.get('accepted_count')} |"
            )
        lines.extend(
            [
                "",
                "## Mandatory answers",
                "",
                f"1. Most profitable: **{m.get('1_most_profitable_archetype')}**",
                f"2. Most loss: **{m.get('2_most_loss_archetype')}**",
                f"3. Trend profit source: **{m.get('3_trend_following_profit_source')}**",
                f"4. Pullback profit source: **{m.get('4_pullback_reversal_profit_source')}**",
                f"5. Near-high profit source: **{m.get('5_near_high_continuation_profit_source')}**",
                f"6. VWAP-stable profit source: **{m.get('6_vwap_stable_profit_source')}**",
                f"7. Momentum drops: **{m.get('7_momentum_gate_drops_archetype')}**",
                f"8. Runtime picks: **{m.get('8_runtime_picks_archetype')}**",
                f"9. 6/19 missed: **{m.get('9_619_missed_archetypes')}**",
                f"10. Rescue improved: **{m.get('10_rescue_improved_archetype')}**",
                f"11. Runtime candidate: **{m.get('11_runtime_candidate')}**",
                f"12. Next: {m.get('12_next_actions')}",
                "",
            ]
        )
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
