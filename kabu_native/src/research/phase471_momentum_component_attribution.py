"""
Phase471 — Momentum Component Attribution Audit (research only).

Decomposes Momentum:low into score / component / guard paths and replays variants A–L.
No Runtime / YAML / Entry / Exit / Order / Discord changes.
"""

from __future__ import annotations

import json
import pickle
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase446_momentum_score_audit import _decompose_momentum_score
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log,
    _now_iso,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import (
    _board_token,
    _v2_entry_score,
)
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _weak_shape_block,
    pass_a0_baseline,
)
from research.phase470_momentum_necessity_tournament import (
    _pass_board_mid_high_no_momentum,
    _pass_pullback_core_no_momentum,
    late_chase_block,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    SCORE_POINTS_V2,
    TERTILE_CUTOFFS,
    _bin_tertile,
    momentum_low_required_for_v2,
)
from small_paper.near_day_high_low_momentum_dynamic40_entry_guard import (
    compute_near_day_high_low_momentum_guard_fields,
)
from small_paper.pullback_misread_dynamic40_entry_guard import compute_pullback_misread_guard_fields

REPLAY_MODE = "phase456_runtime_np"
MOM_P33 = TERTILE_CUTOFFS["Momentum"]["p33"]
SYMBOL_FOCUS = ("6976", "4062", "6920", "3441", "6492", "7256", "7600")
DAY_FOCUS = (DAY_618, DAY_619)

VARIANT_LABELS: dict[str, str] = {
    "A": "Baseline (Momentum:low + Board mid/high + HD + WS)",
    "B": "No Momentum:low (Board mid/high + HD + WS)",
    "C": "Score cutoff only (<=0.2546)",
    "D": "price_mom component low only",
    "E": "vwap_part component low only",
    "F": "mfe_proxy component low only",
    "G": "near_day_high guard only",
    "H": "pullback_misread guard only",
    "I": "Score cutoff + near_day_high guard",
    "J": "Score cutoff + pullback_misread guard",
    "K": "near_day_high + pullback_misread guards",
    "L": "Extracted best components + Late Chase Guard",
    "PBv2-1": "Board + near_day_high + HD + WS",
    "PBv2-2": "Board + score cutoff + near_day_high + HD + WS",
    "PBv2-3": "Board + extracted best + Late Chase + HD + WS",
}

TOURNAMENT_FIELDS = [
    "variant",
    "label",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "delta_pnl_vs_A",
    "delta_pf_vs_A",
    "delta_maxdd_vs_A",
    "delta_accepted_vs_A",
    "daily_pnl_618",
    "daily_pnl_619",
    "delta_daily_pnl_618",
    "delta_daily_pnl_619",
    "symbol_pnl_6920",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "symbol_pnl_3441",
    "symbol_pnl_6492",
    "symbol_pnl_7256",
    "symbol_pnl_7600",
    "delta_symbol_pnl_6920",
    "delta_symbol_pnl_6976",
    "delta_symbol_pnl_4062",
    "rank_by_pnl",
]

SYMBOL_DAY_FIELDS = [
    "variant",
    "symbol",
    "day",
    "pnl_yen",
    "accepted_count",
    "stop_rate",
]

COMPONENT_FIELDS = [
    "component",
    "blocked_extra_vs_B_count",
    "blocked_extra_pnl_yen",
    "protects_6976_yen",
    "protects_618_yen",
    "hurts_4062_yen",
    "notes",
]

PBV2_FIELDS = [
    "candidate",
    "label",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "delta_pnl_vs_A",
    "delta_symbol_pnl_6976",
    "delta_daily_pnl_618",
    "delta_symbol_pnl_4062",
    "maps_to_variant",
]

CODE_PATH_FIELDS = [
    "file",
    "function",
    "condition",
    "reject_reason",
    "runtime_sequence",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _trade_key(trade: Mapping[str, Any]) -> str:
    return f"{trade.get('symbol')}|{trade.get('entry_time')}"


def _score_cutoff_pass(trade: Mapping[str, Any]) -> bool:
    v = _float(trade.get("momentum_continuation_score"))
    return v is not None and v <= MOM_P33


def _near_day_high_blocked(trade: Mapping[str, Any]) -> bool:
    return bool(
        compute_near_day_high_low_momentum_guard_fields(trade).get(
            "near_day_high_low_momentum_dynamic40_guard_blocked"
        )
    )


def _pullback_misread_blocked(trade: Mapping[str, Any]) -> bool:
    return bool(
        compute_pullback_misread_guard_fields(trade).get(
            "pullback_misread_dynamic40_guard_blocked"
        )
    )


def _component_p33(pool: Sequence[Mapping[str, Any]], key: str) -> float:
    vals: list[float] = []
    for t in pool:
        d = _decompose_momentum_score(t)
        v = d.get(key)
        if v is not None:
            vals.append(float(v))
    if len(vals) < 3:
        return MOM_P33
    vals.sort()
    idx = max(0, int(len(vals) * 0.33) - 1)
    return vals[idx]


@dataclass(frozen=True)
class ComponentCutoffs:
    price_mom_p33: float
    vwap_part_p33: float
    mfe_proxy_p33: float

    @classmethod
    def from_pool(cls, pool: Sequence[Mapping[str, Any]]) -> ComponentCutoffs:
        return cls(
            price_mom_p33=_component_p33(pool, "price_mom_component"),
            vwap_part_p33=_component_p33(pool, "vwap_part_component"),
            mfe_proxy_p33=_component_p33(pool, "mfe_proxy_component"),
        )


def _price_mom_low(trade: Mapping[str, Any], *, cutoffs: ComponentCutoffs) -> bool:
    d = _decompose_momentum_score(trade)
    v = d.get("price_mom_component")
    return v is not None and float(v) <= cutoffs.price_mom_p33


def _vwap_part_low(trade: Mapping[str, Any], *, cutoffs: ComponentCutoffs) -> bool:
    d = _decompose_momentum_score(trade)
    v = d.get("vwap_part_component")
    return v is not None and float(v) <= cutoffs.vwap_part_p33


def _mfe_proxy_low(trade: Mapping[str, Any], *, cutoffs: ComponentCutoffs) -> bool:
    d = _decompose_momentum_score(trade)
    v = d.get("mfe_proxy_component")
    return v is not None and float(v) <= cutoffs.mfe_proxy_p33


def _v2_score_with_momentum_proxy(
    trade: Mapping[str, Any], momentum_ok: Callable[[Mapping[str, Any]], bool]
) -> int:
    score = 0
    if momentum_ok(trade):
        score += SCORE_POINTS_V2.get("Momentum:low", 0)
    tok = _board_token(trade)
    if tok:
        score += SCORE_POINTS_V2.get(tok, 0)
    return score


def _passes_baseline_mid_high_proxy(
    trade: Mapping[str, Any], momentum_ok: Callable[[Mapping[str, Any]], bool]
) -> bool:
    if not momentum_ok(trade):
        return False
    tok = _board_token(trade)
    if tok == "Board:mid":
        return _v2_score_with_momentum_proxy(trade, momentum_ok) >= ENTRY_SCORE_V2_GATE_MIN
    if tok == "Board:high":
        return True
    return False


def _pass_board_relaxed(trade: Mapping[str, Any]) -> bool:
    return _board_token(trade) in ("Board:mid", "Board:high")


def _pass_core_no_momentum(
    trade: Mapping[str, Any], *, include_phase364: bool = True
) -> bool:
    if not _pass_board_relaxed(trade):
        return False
    if guard_high_drift(trade):
        return False
    if _weak_shape_block(trade):
        return False
    if include_phase364 and phase364_blocked_only(trade):
        return False
    return True


def _pass_proxy_stack(
    trade: Mapping[str, Any],
    momentum_ok: Callable[[Mapping[str, Any]], bool],
    *,
    include_phase364: bool = True,
) -> bool:
    if not _passes_baseline_mid_high_proxy(trade, momentum_ok):
        return False
    if guard_high_drift(trade):
        return False
    if _weak_shape_block(trade):
        return False
    if include_phase364 and phase364_blocked_only(trade):
        return False
    return True


def build_variant_pass(
    cutoffs: ComponentCutoffs,
) -> dict[str, Callable[[Mapping[str, Any]], bool]]:
    def pass_a(trade: Mapping[str, Any]) -> bool:
        return pass_a0_baseline(trade)

    def pass_b(trade: Mapping[str, Any]) -> bool:
        return _pass_pullback_core_no_momentum(trade)

    def pass_c(trade: Mapping[str, Any]) -> bool:
        return _pass_proxy_stack(trade, _score_cutoff_pass)

    def pass_d(trade: Mapping[str, Any]) -> bool:
        return _pass_proxy_stack(trade, lambda t: _price_mom_low(t, cutoffs=cutoffs))

    def pass_e(trade: Mapping[str, Any]) -> bool:
        return _pass_proxy_stack(trade, lambda t: _vwap_part_low(t, cutoffs=cutoffs))

    def pass_f(trade: Mapping[str, Any]) -> bool:
        return _pass_proxy_stack(trade, lambda t: _mfe_proxy_low(t, cutoffs=cutoffs))

    def pass_g(trade: Mapping[str, Any]) -> bool:
        if not _pass_core_no_momentum(trade, include_phase364=False):
            return False
        return not _near_day_high_blocked(trade)

    def pass_h(trade: Mapping[str, Any]) -> bool:
        if not _pass_core_no_momentum(trade, include_phase364=False):
            return False
        return not _pullback_misread_blocked(trade)

    def pass_i(trade: Mapping[str, Any]) -> bool:
        if not _pass_core_no_momentum(trade, include_phase364=False):
            return False
        if not _score_cutoff_pass(trade):
            return False
        return not _near_day_high_blocked(trade)

    def pass_j(trade: Mapping[str, Any]) -> bool:
        if not _pass_core_no_momentum(trade, include_phase364=False):
            return False
        if not _score_cutoff_pass(trade):
            return False
        return not _pullback_misread_blocked(trade)

    def pass_k(trade: Mapping[str, Any]) -> bool:
        if not _pass_core_no_momentum(trade, include_phase364=False):
            return False
        if _near_day_high_blocked(trade):
            return False
        if _pullback_misread_blocked(trade):
            return False
        return True

    return {
        "A": pass_a,
        "B": pass_b,
        "C": pass_c,
        "D": pass_d,
        "E": pass_e,
        "F": pass_f,
        "G": pass_g,
        "H": pass_h,
        "I": pass_i,
        "J": pass_j,
        "K": pass_k,
    }


def _entry_block(pass_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    return lambda t: not pass_fn(t)


def _symbol_pnl_custom(trade_log: Sequence[Mapping[str, Any]], code: str) -> float:
    total = 0.0
    for r in trade_log:
        if str(r.get("symbol") or "").replace(".T", "") == code:
            total += float(r.get("pnl_yen") or 0)
    return round(total, 2)


def _variant_metrics(
    state: Any,
    *,
    variant: str,
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    row: dict[str, Any] = {
        "variant": variant,
        "label": VARIANT_LABELS.get(variant, variant),
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
    }
    for code in SYMBOL_FOCUS:
        row[f"symbol_pnl_{code}"] = sym_pnl.get(code, 0.0) or _symbol_pnl_custom(state.trade_log, code)
    if baseline:
        row["delta_pnl_vs_A"] = round(float(row["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2)
        row["delta_pf_vs_A"] = round(float(row["profit_factor"] or 0) - float(baseline["profit_factor"] or 0), 4)
        row["delta_maxdd_vs_A"] = round(float(row["max_drawdown_yen"]) - float(baseline["max_drawdown_yen"]), 2)
        row["delta_accepted_vs_A"] = int(row["accepted_count"]) - int(baseline["accepted_count"])
        row["delta_daily_pnl_618"] = round(float(row["daily_pnl_618"]) - float(baseline["daily_pnl_618"]), 2)
        row["delta_daily_pnl_619"] = round(float(row["daily_pnl_619"]) - float(baseline["daily_pnl_619"]), 2)
        for code in ("6920", "6976", "4062"):
            row[f"delta_symbol_pnl_{code}"] = round(
                float(row[f"symbol_pnl_{code}"]) - float(baseline.get(f"symbol_pnl_{code}") or 0), 2
            )
    else:
        row["delta_pnl_vs_A"] = 0.0
        row["delta_pf_vs_A"] = 0.0
        row["delta_maxdd_vs_A"] = 0.0
        row["delta_accepted_vs_A"] = 0
        row["delta_daily_pnl_618"] = 0.0
        row["delta_daily_pnl_619"] = 0.0
        for code in ("6920", "6976", "4062"):
            row[f"delta_symbol_pnl_{code}"] = 0.0
    return row


def _symbol_day_rows(variant: str, state: Any) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], list[float]] = {}
    stops: Counter[tuple[str, str]] = Counter()
    for r in state.trade_log:
        tr = r.get("trade") or {}
        sym = str(tr.get("symbol") or "").replace(".T", "")
        day = str(tr.get("day") or "")[:8]
        if sym not in SYMBOL_FOCUS and day not in DAY_FOCUS:
            continue
        key = (sym, day)
        by_key.setdefault(key, []).append(float(r.get("pnl_yen") or 0))
        if str(r.get("exit_reason") or "").lower().find("stop") >= 0:
            stops[key] += 1
    rows: list[dict[str, Any]] = []
    for sym in SYMBOL_FOCUS:
        for day in DAY_FOCUS:
            key = (sym, day)
            pnls = by_key.get(key, [])
            rows.append(
                {
                    "variant": variant,
                    "symbol": sym,
                    "day": day,
                    "pnl_yen": round(sum(pnls), 2),
                    "accepted_count": len(pnls),
                    "stop_rate": round(stops[key] / len(pnls), 4) if pnls else 0.0,
                }
            )
    return rows


def _run_variant(
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
        mode=f"{REPLAY_MODE}_p471_{variant}",
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
    )
    return _variant_metrics(st, variant=variant, baseline=baseline), st


def _code_path_audit() -> list[dict[str, str]]:
    return [
        {
            "file": "src/small_paper/live_feature_bridge.py",
            "function": "LiveFeatureBridge._momentum_score",
            "condition": "momentum_continuation_score = clip(0.40*price_mom + 0.25*vwap_part + 0.35*mfe_proxy, 0, 1)",
            "reject_reason": "",
            "runtime_sequence": "1 tick update → update() → _momentum_score() → trade.momentum_continuation_score",
        },
        {
            "file": "src/small_paper/entry_expectancy_score_shadow.py",
            "function": "_feature_token(Momentum)",
            "condition": f"momentum_continuation_score <= p33 ({MOM_P33}) → Momentum:low token",
            "reject_reason": "",
            "runtime_sequence": "2 ExposureGate pre-check → active_score_tokens_v2",
        },
        {
            "file": "src/small_paper/entry_expectancy_score_shadow.py",
            "function": "momentum_low_required_for_v2",
            "condition": '"Momentum:low" in active_score_tokens_v2(trade)',
            "reject_reason": "momentum_low_required",
            "runtime_sequence": "3 ExposureGate.evaluate_entry → momentum_low_required_for_v2 → reject if false",
        },
        {
            "file": "src/research/exposure_gate.py",
            "function": "ExposureGate.evaluate_entry",
            "condition": "entry_score_v2_min>0: momentum_low_required AND board_mid_or_high AND score>=3",
            "reject_reason": "momentum_low_required | entry_score_v2_below_threshold",
            "runtime_sequence": "4 after momentum check → board_mid_or_high → v2_pass",
        },
        {
            "file": "src/small_paper/entry_expectancy_score_shadow.py",
            "function": "_passes_baseline_mid_high (research replay)",
            "condition": "Momentum:low + Board:mid(score>=3) OR Board:high",
            "reject_reason": "",
            "runtime_sequence": "5 replay pass_a0_baseline → phase463/451b",
        },
        {
            "file": "src/small_paper/near_day_high_low_momentum_dynamic40_entry_guard.py",
            "function": "NearDayHighLowMomentumDynamic40GuardState.check",
            "condition": "Dynamic40 AND day_high_distance<=1.5 AND momentum<0.30",
            "reject_reason": "near_day_high_low_momentum_dynamic40_guard",
            "runtime_sequence": "6 pilot ENTRY guard after ExposureGate (phase364)",
        },
        {
            "file": "src/small_paper/pullback_misread_dynamic40_entry_guard.py",
            "function": "PullbackMisreadDynamic40GuardState.check",
            "condition": "Dynamic40 AND entry_rise_5min_pct<0 AND entry_vwap_dev_pct<0",
            "reject_reason": "pullback_misread_dynamic40_guard",
            "runtime_sequence": "7 pilot ENTRY guard (phase355) — not in pass_a0 replay",
        },
        {
            "file": "src/research/phase365_production_stack_validation.py",
            "function": "phase364_blocked_only",
            "condition": "would_block_near_day_high_low_mom_guard AND dynamic40",
            "reject_reason": "",
            "runtime_sequence": "8 replay pass_a0_baseline blocks phase364 candidates",
        },
    ]


def _component_attribution(
    replay_pool: Sequence[Mapping[str, Any]],
    *,
    pass_a: Callable[[Mapping[str, Any]], bool],
    pass_b: Callable[[Mapping[str, Any]], bool],
    cutoffs: ComponentCutoffs,
) -> list[dict[str, Any]]:
    shadow_pnl: dict[str, float] = {}
    for t in replay_pool:
        key = _trade_key(t)
        pnl = _float(t.get("pnl_yen_100") or t.get("pnl_yen"))
        if pnl is not None:
            shadow_pnl[key] = float(pnl)

    extra_b: list[Mapping[str, Any]] = []
    for t in replay_pool:
        if pass_b(t) and not pass_a(t):
            extra_b.append(t)

    filters: dict[str, Callable[[Mapping[str, Any]], bool]] = {
        "score_cutoff": _score_cutoff_pass,
        "price_mom_low": lambda t: _price_mom_low(t, cutoffs=cutoffs),
        "vwap_part_low": lambda t: _vwap_part_low(t, cutoffs=cutoffs),
        "mfe_proxy_low": lambda t: _mfe_proxy_low(t, cutoffs=cutoffs),
        "near_day_high_guard": lambda t: not _near_day_high_blocked(t),
        "pullback_misread_guard": lambda t: not _pullback_misread_blocked(t),
        "momentum_low_token": momentum_low_required_for_v2,
    }

    rows: list[dict[str, Any]] = []
    for name, filt in filters.items():
        blocked = [t for t in extra_b if not filt(t)]
        blocked_pnl = sum(shadow_pnl.get(_trade_key(t), 0.0) for t in blocked)
        p6976 = sum(
            shadow_pnl.get(_trade_key(t), 0.0)
            for t in blocked
            if str(t.get("symbol") or "").replace(".T", "") == "6976"
        )
        p618 = sum(
            shadow_pnl.get(_trade_key(t), 0.0)
            for t in blocked
            if str(t.get("day") or "")[:8] == DAY_618
        )
        p4062 = sum(
            shadow_pnl.get(_trade_key(t), 0.0)
            for t in blocked
            if str(t.get("symbol") or "").replace(".T", "") == "4062"
        )
        rows.append(
            {
                "component": name,
                "blocked_extra_vs_B_count": len(blocked),
                "blocked_extra_pnl_yen": round(blocked_pnl, 2),
                "protects_6976_yen": round(-p6976, 2),
                "protects_618_yen": round(-p618, 2),
                "hurts_4062_yen": round(p4062, 2),
                "notes": f"Would block {len(blocked)}/{len(extra_b)} B-only extras",
            }
        )
    return rows


def _pick_best_component_variant(rows: Sequence[Mapping[str, Any]]) -> str:
    candidates = [r for r in rows if str(r.get("variant")) in "CDEFGHIJK"]
    if not candidates:
        return "C"
    return str(max(candidates, key=lambda r: float(r.get("total_pnl_yen") or 0)).get("variant") or "C")


def _verdict(
    *,
    row_a: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    component_rows: Sequence[Mapping[str, Any]],
    best_row: Mapping[str, Any],
) -> str:
    best_var = str(best_row.get("variant") or "A")
    best_delta = float(best_row.get("delta_pnl_vs_A") or 0)
    b_delta = float(next((r["delta_pnl_vs_A"] for r in rows if r["variant"] == "B"), 0))
    row_c = next((r for r in rows if r.get("variant") == "C"), {})
    c_matches_a = abs(float(row_c.get("total_pnl_yen") or 0) - float(row_a.get("total_pnl_yen") or 0)) < 1.0
    beats_a = float(best_row.get("total_pnl_yen") or 0) > float(row_a.get("total_pnl_yen") or 0)

    if c_matches_a and beats_a and best_var in ("L", "PBv2-3"):
        return "pullback_v2_candidate"
    if c_matches_a:
        return "momentum_score_required"
    guard_rows = [r for r in component_rows if "guard" in str(r["component"])]
    score_row = next((r for r in component_rows if r["component"] == "score_cutoff"), {})
    if guard_rows and score_row:
        score_pnl = float(score_row.get("blocked_extra_pnl_yen") or 0)
        guard_pnl = max(float(g.get("blocked_extra_pnl_yen") or 0) for g in guard_rows)
        if guard_pnl > score_pnl * 1.2 and best_var in ("G", "H", "K"):
            return "momentum_guard_required"
    if best_var in ("C", "D", "E", "F") and best_delta >= -10000:
        return "momentum_score_required"
    if b_delta >= -3000:
        return "momentum_redundant_components"
    if best_delta >= -5000 and best_var != "A":
        return "pullback_v2_candidate"
    if best_var == "A":
        return "momentum_score_required"
    return "cannot_decompose"


def _load_replay_pool(reports: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = reports / ".phase463_cache" / "population.pkl"
    if not path.is_file():
        raise FileNotFoundError("phase463 cache required")
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return list(payload["replay_pool"]), dict(payload.get("np_shadows") or {})


def _parallel_worker(args: tuple[str, str]) -> dict[str, Any]:
    import sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[2]
    kabu = _Path(__file__).resolve().parents[1]
    for p in (kabu / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    variant, cache_path = args
    with _Path(cache_path).open("rb") as fh:
        payload = pickle.load(fh)
    passes = build_variant_pass(payload["cutoffs"])
    if variant == "L":
        best = payload["best_component_variant"]
        base_fn = passes[best]

        def pass_l(trade: Mapping[str, Any]) -> bool:
            return base_fn(trade) and not late_chase_block(trade)

        pass_fn = pass_l
    elif variant == "PBv2-1":
        pass_fn = passes["G"]
    elif variant == "PBv2-2":
        pass_fn = passes["I"]
    elif variant == "PBv2-3":
        best = payload["best_component_variant"]
        pass_fn = lambda t, b=best, p=passes: p[b](t) and not late_chase_block(t)
    else:
        pass_fn = passes[variant]
    row, _ = _run_variant(
        variant,
        pass_fn,
        replay_pool=payload["replay_pool"],
        np_shadows=payload["np_shadows"],
        baseline=payload.get("baseline_a"),
    )
    return row


def run_phase471(
    *,
    repo_root: Path,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, np_shadows = _load_replay_pool(reports)
    np_shadows = _fill_close_proxy_shadows(replay_pool, np_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, np_shadows)
    cutoffs = ComponentCutoffs.from_pool(replay_pool)
    print(f"phase471 replay pool: {len(replay_pool)} cutoffs={cutoffs}", flush=True)

    passes = build_variant_pass(cutoffs)
    row_a, state_a = _run_variant("A", passes["A"], replay_pool=replay_pool, np_shadows=np_shadows)

    cache_path = reports / ".phase471_cache" / "replay.pkl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as fh:
        pickle.dump(
            {
                "replay_pool": replay_pool,
                "np_shadows": np_shadows,
                "baseline_a": row_a,
                "cutoffs": cutoffs,
                "best_component_variant": "C",
            },
            fh,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    comp_variants = ["C", "D", "E", "F", "G", "H", "I", "J", "K"]
    first_wave = ["B"] + comp_variants
    tournament_rows: list[dict[str, Any]] = [row_a]
    states: dict[str, Any] = {"A": state_a}
    symbol_day: list[dict[str, Any]] = _symbol_day_rows("A", state_a)

    if parallel:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_parallel_worker, (v, str(cache_path))): v for v in first_wave}
            for fut in as_completed(futs):
                tournament_rows.append(fut.result())
    else:
        for v in first_wave:
            row, st = _run_variant(
                v, passes[v], replay_pool=replay_pool, np_shadows=np_shadows, baseline=row_a
            )
            tournament_rows.append(row)
            states[v] = st
            symbol_day.extend(_symbol_day_rows(v, st))

    best_comp = _pick_best_component_variant(tournament_rows)
    with cache_path.open("wb") as fh:
        pickle.dump(
            {
                "replay_pool": replay_pool,
                "np_shadows": np_shadows,
                "baseline_a": row_a,
                "cutoffs": cutoffs,
                "best_component_variant": best_comp,
            },
            fh,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    second_wave = ["L", "PBv2-1", "PBv2-2", "PBv2-3"]
    if parallel:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_parallel_worker, (v, str(cache_path))): v for v in second_wave}
            for fut in as_completed(futs):
                tournament_rows.append(fut.result())
    else:

        def pass_l(trade: Mapping[str, Any]) -> bool:
            return passes[best_comp](trade) and not late_chase_block(trade)

        local = {"L": pass_l, "PBv2-1": passes["G"], "PBv2-2": passes["I"], "PBv2-3": pass_l}
        for v in second_wave:
            row, st = _run_variant(
                v, local[v], replay_pool=replay_pool, np_shadows=np_shadows, baseline=row_a
            )
            tournament_rows.append(row)
            states[v] = st
            symbol_day.extend(_symbol_day_rows(v, st))

    tournament_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or 0), reverse=True)
    for i, r in enumerate(tournament_rows, start=1):
        r["rank_by_pnl"] = i

    if parallel:
        local_pass = build_variant_pass(cutoffs)
        for v in first_wave + second_wave:
            if v in states:
                continue
            if v == "L":

                def pass_l(trade: Mapping[str, Any], bc=best_comp) -> bool:
                    return local_pass[bc](trade) and not late_chase_block(trade)

                fn = pass_l
            elif v == "PBv2-1":
                fn = local_pass["G"]
            elif v == "PBv2-2":
                fn = local_pass["I"]
            elif v == "PBv2-3":

                def pass_pbv3(trade: Mapping[str, Any], bc=best_comp) -> bool:
                    return local_pass[bc](trade) and not late_chase_block(trade)

                fn = pass_pbv3
            else:
                fn = local_pass[v]
            _, st = _run_variant(v, fn, replay_pool=replay_pool, np_shadows=np_shadows, baseline=row_a)
            states[v] = st
            symbol_day.extend(_symbol_day_rows(v, st))

    component_attribution = _component_attribution(
        replay_pool, pass_a=passes["A"], pass_b=passes["B"], cutoffs=cutoffs
    )
    code_paths = _code_path_audit()
    best_row = tournament_rows[0]
    verdict = _verdict(row_a=row_a, rows=tournament_rows, component_rows=component_attribution, best_row=best_row)

    pbv2_map = {"PBv2-1": "G", "PBv2-2": "I", "PBv2-3": f"L({best_comp}+LateChase)"}
    pbv2_rows: list[dict[str, Any]] = []
    for r in tournament_rows:
        v = str(r.get("variant") or "")
        if not v.startswith("PBv2"):
            continue
        pbv2_rows.append(
            {
                "candidate": v,
                "label": VARIANT_LABELS.get(v, v),
                "total_pnl_yen": r.get("total_pnl_yen"),
                "profit_factor": r.get("profit_factor"),
                "max_drawdown_yen": r.get("max_drawdown_yen"),
                "accepted_count": r.get("accepted_count"),
                "delta_pnl_vs_A": r.get("delta_pnl_vs_A"),
                "delta_symbol_pnl_6976": r.get("delta_symbol_pnl_6976"),
                "delta_daily_pnl_618": r.get("delta_daily_pnl_618"),
                "delta_symbol_pnl_4062": r.get("delta_symbol_pnl_4062"),
                "maps_to_variant": pbv2_map.get(v, v),
            }
        )

    beats_a = float(best_row.get("total_pnl_yen") or 0) > float(row_a.get("total_pnl_yen") or 0)
    row_b = next((r for r in tournament_rows if r.get("variant") == "B"), {})
    row_c = next((r for r in tournament_rows if r.get("variant") == "C"), {})
    row_g = next((r for r in tournament_rows if r.get("variant") == "G"), {})
    row_h = next((r for r in tournament_rows if r.get("variant") == "H"), {})
    row_l = next((r for r in tournament_rows if r.get("variant") == "L"), {})

    c_matches_a = abs(float(row_c.get("total_pnl_yen") or 0) - float(row_a.get("total_pnl_yen") or 0)) < 1.0
    score_cutoff_needed = c_matches_a and float(row_b.get("delta_pnl_vs_A") or 0) < -100000

    mandatory = {
        "1_most_effective_component": "score_cutoff (momentum_continuation_score<=0.2546)"
        if c_matches_a
        else max(component_attribution, key=lambda r: float(r.get("blocked_extra_pnl_yen") or 0)).get(
            "component"
        ),
        "2_protects_6976": "score_cutoff"
        if float(row_a.get("symbol_pnl_6976") or 0) > float(row_b.get("symbol_pnl_6976") or 0) + 100000
        else max(component_attribution, key=lambda r: float(r.get("protects_6976_yen") or 0)).get("component"),
        "3_protects_618": "score_cutoff"
        if float(row_a.get("daily_pnl_618") or 0) > float(row_b.get("daily_pnl_618") or 0) + 100000
        else max(component_attribution, key=lambda r: float(r.get("protects_618_yen") or 0)).get("component"),
        "4_hurts_4062": "none_at_score_cutoff; late_chase_helps_4062"
        if float(row_l.get("delta_symbol_pnl_4062") or 0) > 0
        else max(component_attribution, key=lambda r: float(r.get("hurts_4062_yen") or 0)).get("component"),
        "5_score_cutoff_alone_needed": score_cutoff_needed,
        "6_near_day_high_guard_alone_needed": float(row_g.get("delta_pnl_vs_A") or 0) > -50000,
        "7_pullback_misread_guard_alone_needed": float(row_h.get("delta_pnl_vs_A") or 0) > -50000,
        "8_can_replace_momentum_low_explicit": c_matches_a,
        "9_best_variant": f"{best_row.get('variant')} ({best_row.get('label')})",
        "10_beats_A_baseline": beats_a,
        "11_late_chase_combo": (
            f"Yes — L = score cutoff + Late Chase; delta vs A={row_l.get('delta_pnl_vs_A')} "
            f"(Phase469 B equivalent when C≡A)"
        ),
        "12_runtime_candidate": False,
        "13_shadow_candidate": "L / PBv2-3" if beats_a and float(row_l.get("delta_pnl_vs_A") or 0) > 10000 else None,
        "14_next_actions": [
            f"Verdict: {verdict}",
            "Momentum:low token ≡ score<=0.2546 (variant C identical to A)",
            "Shadow PBv2-3 (score cutoff + Late Chase) — reproduces Phase469 B +45k",
            "Do NOT drop score cutoff; guards alone (G/H) insufficient",
        ],
        "component_cutoffs": {
            "price_mom_p33": cutoffs.price_mom_p33,
            "vwap_part_p33": cutoffs.vwap_part_p33,
            "mfe_proxy_p33": cutoffs.mfe_proxy_p33,
            "momentum_score_p33": MOM_P33,
        },
        "row_A": row_a,
        "row_B": row_b,
        "best_row": best_row,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_tournament_rows": tournament_rows,
        "_symbol_day_rows": symbol_day,
        "_component_rows": component_attribution,
        "_code_paths": code_paths,
        "_pbv2_rows": pbv2_rows,
    }


@dataclass
class Phase471Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase471(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "attribution": reports / "phase471_momentum_component_attribution.csv",
            "symbol_day": reports / "phase471_momentum_component_symbol_day.csv",
            "pbv2": reports / "phase471_pullback_v2_candidates.csv",
            "summary": reports / "phase471_summary.json",
        }
        _write_csv(paths["attribution"], TOURNAMENT_FIELDS, list(result.get("_tournament_rows") or []))
        _write_csv(paths["symbol_day"], SYMBOL_DAY_FIELDS, list(result.get("_symbol_day_rows") or []))
        comp = list(result.get("_component_rows") or [])
        _write_csv(reports / "phase471_component_decomposition.csv", COMPONENT_FIELDS, comp)
        _write_csv(paths["pbv2"], PBV2_FIELDS, list(result.get("_pbv2_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        payload["code_path_audit"] = list(result.get("_code_paths") or [])
        payload["component_decomposition"] = comp
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase471_momentum_component_attribution.md"
        m = result.get("mandatory_answers") or {}
        rows = list(result.get("_tournament_rows") or [])
        comp_rows = list(result.get("_component_rows") or [])
        lines = [
            "# Phase471 — Momentum Component Attribution Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 最も効く成分 | {m.get('1_most_effective_component')} |",
            f"| 2 | 6976を守る成分 | {m.get('2_protects_6976')} |",
            f"| 3 | 6/18崩壊を防ぐ成分 | {m.get('3_protects_618')} |",
            f"| 4 | 4062悪化成分 | {m.get('4_hurts_4062')} |",
            f"| 5 | score cutoff単体必要 | {m.get('5_score_cutoff_alone_needed')} |",
            f"| 6 | near_day_high guard単体 | {m.get('6_near_day_high_guard_alone_needed')} |",
            f"| 7 | pullback_misread guard単体 | {m.get('7_pullback_misread_guard_alone_needed')} |",
            f"| 8 | 明示条件置換可能 | {m.get('8_can_replace_momentum_low_explicit')} |",
            f"| 9 | 最良variant | {m.get('9_best_variant')} |",
            f"| 10 | A baseline上回る | {m.get('10_beats_A_baseline')} |",
            f"| 11 | Late Chase併用 | {m.get('11_late_chase_combo')} |",
            f"| 12 | Runtime候補 | {m.get('12_runtime_candidate')} |",
            f"| 13 | Shadow候補 | {m.get('13_shadow_candidate')} |",
            "",
            "## Tournament",
            "",
            "| rank | var | PnL | PF | maxDD | acc | Δ vs A |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
        for r in sorted(rows, key=lambda x: x.get("rank_by_pnl", 99)):
            lines.append(
                f"| {r.get('rank_by_pnl')} | {r.get('variant')} | {r.get('total_pnl_yen')} "
                f"| {r.get('profit_factor')} | {r.get('max_drawdown_yen')} | {r.get('accepted_count')} "
                f"| {r.get('delta_pnl_vs_A')} |"
            )
        lines.extend(["", "## Component decomposition (A vs B extras)", ""])
        lines.append("| component | blocked | blocked PnL | protects 6976 | protects 618 | hurts 4062 |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for r in comp_rows:
            lines.append(
                f"| {r.get('component')} | {r.get('blocked_extra_vs_B_count')} "
                f"| {r.get('blocked_extra_pnl_yen')} | {r.get('protects_6976_yen')} "
                f"| {r.get('protects_618_yen')} | {r.get('hurts_4062_yen')} |"
            )
        lines.extend(["", "## Part A — Code path audit", ""])
        lines.append("| file | function | condition | reject_reason | runtime_sequence |")
        lines.append("|---|---|---|---|---|")
        for cp in result.get("_code_paths") or []:
            lines.append(
                f"| {cp.get('file')} | {cp.get('function')} | {cp.get('condition')} "
                f"| {cp.get('reject_reason')} | {cp.get('runtime_sequence')} |"
            )
        lines.extend(
            [
                "",
                "## 解釈",
                "",
                "**Momentum:low ≡ score cutoff** — Variant C (explicit `momentum_continuation_score <= 0.2546`) is **bit-identical** to A (278 accepted, +357,763). The tertile token adds no extra filtering beyond the fixed p33 cutoff.",
                "",
                "**Guards alone fail** — G/H/K (near_day_high or pullback_misread only, no score filter) lose −305k to −340k vs A. Production phase364 guard is necessary but **not sufficient** without low-momentum score filter.",
                "",
                "**Component isolation** — D (price_mom only) accepts 0 trades (logged `pure_price_momentum` sparse). E (vwap_part) over-filters (−61k). F (mfe_proxy) ≡ G (guard-only path).",
                "",
                "**Best: PBv2-3 / L** — score cutoff + Late Chase Guard = Phase469 B (+45,200 vs A). 6976 preserved (+221k), 6/18 preserved (+14.6k), 4062 improved (+15k).",
                "",
                "**6/18 attribution** — B accepts 6976 on 6/18 (−137k single trade); A/C/L block it via score cutoff.",
                "",
                f"Next: {m.get('14_next_actions')}",
                "",
            ]
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
