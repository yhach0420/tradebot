"""
Phase455 — D Group Win/Loss Feature Audit (research only).

Compare D_win vs D_loss within Phase454 D population to find actionable guard rules.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase450_momentum_redesign_shadow import _passes_baseline_entry
from research.phase451_entry_shape_tournament import (
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

JST = ZoneInfo("Asia/Tokyo")
TARGET_SYMBOLS = ("6920.T", "4062.T", "6976.T")

FEATURE_CSV_FIELDS = [
    "feature",
    "d_win_mean",
    "d_win_median",
    "d_win_p25",
    "d_win_p75",
    "d_loss_mean",
    "d_loss_median",
    "d_loss_p25",
    "d_loss_p75",
    "delta_mean",
    "effect_size_cohens_d",
    "rank_by_abs_effect",
]

RULE_CSV_FIELDS = [
    "rule_type",
    "rule_id",
    "rule_description",
    "blocked_count",
    "blocked_loss_count",
    "blocked_win_count",
    "blocked_pnl_yen",
    "remaining_pnl_yen",
    "baseline_pnl_yen",
    "pnl_improvement_yen",
    "remaining_pf",
    "baseline_pf",
    "remaining_maxdd_yen",
    "baseline_maxdd_yen",
    "remaining_stop_rate",
    "symbol_6976_remaining_pnl",
    "symbol_6920_remaining_pnl",
    "symbol_4062_remaining_pnl",
    "day_619_blocked_share",
    "single_symbol_blocked_share",
    "adopt_score",
]

SYMBOL_CSV_FIELDS = [
    "symbol",
    "d_win_count",
    "d_loss_count",
    "d_win_pnl",
    "d_loss_pnl",
    "net_pnl",
    "best_single_rule",
    "single_rule_blocked_losses",
    "single_rule_blocked_wins",
    "single_rule_pnl_delta",
    "separable_by_features",
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


def _rise(trade: Mapping[str, Any], mins: int) -> Optional[float]:
    return _float(trade.get(f"return_{mins}min_pct")) or _float(trade.get(f"entry_rise_{mins}min_pct"))


def _day_high_dist(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("day_high_distance_pct")) or _float(trade.get("entry_near_day_high_pct"))


def _entry_hour(trade: Mapping[str, Any]) -> Optional[float]:
    et = _parse_ts(str(trade.get("entry_time") or ""))
    if et is None:
        return None
    dt = et.astimezone(JST)
    return round(dt.hour + dt.minute / 60.0, 4)


def _feature_specs() -> dict[str, Callable[[Mapping[str, Any]], Optional[float]]]:
    return {
        "r5": lambda t: _rise(t, 5),
        "r10": lambda t: _rise(t, 10),
        "r15": lambda t: _rise(t, 15),
        "r30": lambda t: _rise(t, 30),
        "day_high_distance": _day_high_dist,
        "high_update_age": lambda t: _float(t.get("minutes_since_day_high_update")),
        "entry_vwap_dev_pct": lambda t: _float(t.get("entry_vwap_dev_pct")),
        "entry_order_book_imbalance": lambda t: _float(t.get("entry_order_book_imbalance")),
        "entry_hour": _entry_hour,
        "price": lambda t: _float(t.get("entry_price")) or _float(t.get("current_price")),
        "notional_100shares": lambda t: (_float(t.get("entry_price")) or _float(t.get("current_price")) or 0)
        * 100,
        "trading_value": lambda t: _float(t.get("trading_value")),
    }


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


def _is_d_group(trade: Mapping[str, Any]) -> bool:
    if not _passes_baseline_entry(trade):
        return False
    mapped = _map_runtime_fields(trade)
    return not guard_high_drift(mapped) and not would_block_weak_shape_reject(mapped)


def _is_stop(trade: Mapping[str, Any]) -> bool:
    return normalize_exit_reason(str(trade.get("exit_reason") or "")) == "stop_hit"


def _day_key(trade: Mapping[str, Any]) -> str:
    return str(trade.get("day") or "")[:8]


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        trades,
        key=lambda t: (
            _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(t.get("symbol") or ""),
        ),
    )
    return [_pnl_yen(t) for t in ordered]


def _percentile(vals: Sequence[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(s[int(k)], 4)
    return round(s[f] + (s[c] - s[f]) * (k - f), 4)


def _feature_stats(vals: Sequence[float]) -> dict[str, Optional[float]]:
    if not vals:
        return {"mean": None, "median": None, "p25": None, "p75": None}
    return {
        "mean": round(statistics.mean(vals), 4),
        "median": round(statistics.median(vals), 4),
        "p25": _percentile(vals, 0.25),
        "p75": _percentile(vals, 0.75),
    }


def _cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    va = statistics.pvariance(a)
    vb = statistics.pvariance(b)
    pooled = math.sqrt((va + vb) / 2.0)
    if pooled <= 0:
        return None
    return round((ma - mb) / pooled, 4)


def _cohort_summary(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_pnl_yen(t) for t in trades]
    syms = {str(t.get("symbol") or "") for t in trades}
    return {
        "count": len(trades),
        "pnl_yen": round(sum(pnls), 2),
        "avg_pnl_yen": round(statistics.mean(pnls), 2) if pnls else 0.0,
        "profit_factor": _pf(pnls),
        "stop_rate": round(sum(1 for t in trades if _is_stop(t)) / len(trades), 4) if trades else None,
        "symbol_count": len(syms),
    }


def _eval_block_rule(
    trades: Sequence[Mapping[str, Any]],
    *,
    rule_id: str,
    rule_type: str,
    description: str,
    block_fn: Callable[[Mapping[str, Any]], bool],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    blocked = [t for t in trades if block_fn(t)]
    remaining = [t for t in trades if not block_fn(t)]
    b_pnls = [_pnl_yen(t) for t in blocked]
    r_pnls = _chron_pnls(remaining)
    base_pnl = float(baseline.get("pnl_yen") or 0)
    base_pf = baseline.get("profit_factor")
    base_dd = float(baseline.get("max_drawdown_yen") or 0)
    rem_pnl = round(sum(r_pnls), 2)
    sym_rem = {
        sym: round(sum(_pnl_yen(t) for t in remaining if str(t.get("symbol") or "") == sym), 2)
        for sym in TARGET_SYMBOLS
    }
    blocked_days = Counter(_day_key(t) for t in blocked)
    blocked_syms = Counter(str(t.get("symbol") or "") for t in blocked)
    top_sym_share = (
        max(blocked_syms.values()) / len(blocked) if blocked and blocked_syms else 0.0
    )
    d619_share = blocked_days.get(DAY_619, 0) / len(blocked) if blocked else 0.0
    b_loss = sum(1 for t in blocked if _pnl_yen(t) < 0)
    b_win = sum(1 for t in blocked if _pnl_yen(t) > 0)
    rem_pf = _pf(r_pnls)
    rem_dd = _max_drawdown_yen(r_pnls) if r_pnls else 0.0
    pnl_imp = round(rem_pnl - base_pnl, 2)
    pf_ok = rem_pf is not None and base_pf is not None and rem_pf >= float(base_pf)
    dd_ok = rem_dd <= base_dd + 1.0
    sym6976_ok = sym_rem.get("6976.T", 0) >= float(baseline.get("symbol_6976_pnl") or 0) * 0.85
    not_single = top_sym_share <= 0.6
    not_619_only = d619_share <= 0.7
    adopt = pnl_imp > 0 and pf_ok and dd_ok and sym6976_ok and not_single and not_619_only
    adopt_score = (
        (1 if pnl_imp > 0 else 0)
        + (1 if pf_ok else 0)
        + (1 if dd_ok else 0)
        + (1 if sym6976_ok else 0)
        + (1 if not_single else 0)
        + (1 if not_619_only else 0)
        + min(b_loss / max(len(blocked), 1), 1.0)
    )
    return {
        "rule_type": rule_type,
        "rule_id": rule_id,
        "rule_description": description,
        "blocked_count": len(blocked),
        "blocked_loss_count": b_loss,
        "blocked_win_count": b_win,
        "blocked_pnl_yen": round(sum(b_pnls), 2),
        "remaining_pnl_yen": rem_pnl,
        "baseline_pnl_yen": base_pnl,
        "pnl_improvement_yen": pnl_imp,
        "remaining_pf": rem_pf,
        "baseline_pf": base_pf,
        "remaining_maxdd_yen": rem_dd,
        "baseline_maxdd_yen": base_dd,
        "remaining_stop_rate": round(sum(1 for t in remaining if _is_stop(t)) / len(remaining), 4)
        if remaining
        else None,
        "symbol_6976_remaining_pnl": sym_rem.get("6976.T", 0),
        "symbol_6920_remaining_pnl": sym_rem.get("6920.T", 0),
        "symbol_4062_remaining_pnl": sym_rem.get("4062.T", 0),
        "day_619_blocked_share": round(d619_share, 4),
        "single_symbol_blocked_share": round(top_sym_share, 4),
        "adopt_score": round(adopt_score, 4),
        "adopt_pass": adopt,
    }


def _single_rule_candidates(
    d_win: Sequence[Mapping[str, Any]],
    d_loss: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]]:
    rules: list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]] = []
    specs = _feature_specs()
    for feat, fn in specs.items():
        w_vals = [v for t in d_win if (v := fn(t)) is not None]
        l_vals = [v for t in d_loss if (v := fn(t)) is not None]
        if len(w_vals) < 5 or len(l_vals) < 5:
            continue
        w_med = statistics.median(w_vals)
        l_med = statistics.median(l_vals)
        mid = (w_med + l_med) / 2.0
        l_p75 = _percentile(l_vals, 0.75) or l_med
        l_p25 = _percentile(l_vals, 0.25) or l_med
        w_p25 = _percentile(w_vals, 0.25) or w_med
        w_p75 = _percentile(w_vals, 0.75) or w_med

        if l_med > w_med:
            for thr, op in ((mid, ">"), (l_p75, ">"), ((w_p75 + l_p75) / 2, ">")):
                rules.append(
                    (
                        f"{feat}_gt_{thr:.4f}",
                        f"{feat} > {thr:.4f}",
                        lambda t, f=fn, th=thr: (f(t) or -1e18) > th,
                    )
                )
        else:
            for thr, _op in ((mid, "<"), (l_p25, "<"), ((w_p25 + l_p25) / 2, "<")):
                rules.append(
                    (
                        f"{feat}_lt_{thr:.4f}",
                        f"{feat} < {thr:.4f}",
                        lambda t, f=fn, th=thr: (f(t) or 1e18) < th,
                    )
                )
    return rules


def _dedupe_rules(
    rules: list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]],
) -> list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]]:
    seen: set[str] = set()
    out: list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]] = []
    for rid, desc, fn in rules:
        if rid in seen:
            continue
        seen.add(rid)
        out.append((rid, desc, fn))
    return out


def _rule_fn(single_defs, rule_id: str) -> Callable[[Mapping[str, Any]], bool]:
    return next(f for rid, _, f in single_defs if rid == rule_id)


def run_phase455_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)
    d_group = [dict(t) for t in enriched if _is_d_group(t)]

    d_win = [t for t in d_group if _pnl_yen(t) > 0]
    d_loss = [t for t in d_group if _pnl_yen(t) < 0]
    d_flat = [t for t in d_group if _pnl_yen(t) == 0]

    part_a = {
        "d_win": _cohort_summary(d_win),
        "d_loss": _cohort_summary(d_loss),
        "d_flat_count": len(d_flat),
        "d_net_pnl": round(sum(_pnl_yen(t) for t in d_group), 2),
    }

    baseline_pnls = _chron_pnls(d_group)
    baseline = {
        "pnl_yen": round(sum(baseline_pnls), 2),
        "profit_factor": _pf(baseline_pnls),
        "max_drawdown_yen": _max_drawdown_yen(baseline_pnls),
        "symbol_6976_pnl": round(sum(_pnl_yen(t) for t in d_group if t.get("symbol") == "6976.T"), 2),
        "symbol_6920_pnl": round(sum(_pnl_yen(t) for t in d_group if t.get("symbol") == "6920.T"), 2),
        "symbol_4062_pnl": round(sum(_pnl_yen(t) for t in d_group if t.get("symbol") == "4062.T"), 2),
    }

    feature_rows: list[dict[str, Any]] = []
    specs = _feature_specs()
    for feat, fn in specs.items():
        w_vals = [v for t in d_win if (v := fn(t)) is not None]
        l_vals = [v for t in d_loss if (v := fn(t)) is not None]
        ws, ls = _feature_stats(w_vals), _feature_stats(l_vals)
        d_mean = (ws["mean"], ls["mean"])
        delta = None
        if d_mean[0] is not None and d_mean[1] is not None:
            delta = round(d_mean[1] - d_mean[0], 4)
        effect = _cohens_d(l_vals, w_vals)
        feature_rows.append(
            {
                "feature": feat,
                "d_win_mean": ws["mean"],
                "d_win_median": ws["median"],
                "d_win_p25": ws["p25"],
                "d_win_p75": ws["p75"],
                "d_loss_mean": ls["mean"],
                "d_loss_median": ls["median"],
                "d_loss_p25": ls["p25"],
                "d_loss_p75": ls["p75"],
                "delta_mean": delta,
                "effect_size_cohens_d": effect,
            }
        )
    feature_rows.sort(
        key=lambda r: abs(float(r.get("effect_size_cohens_d") or 0)), reverse=True
    )
    for i, row in enumerate(feature_rows, start=1):
        row["rank_by_abs_effect"] = i
    top5_features = [r["feature"] for r in feature_rows[:5]]

    single_defs = _dedupe_rules(_single_rule_candidates(d_win, d_loss))
    rule_rows: list[dict[str, Any]] = []
    for rid, desc, fn in single_defs:
        rule_rows.append(
            _eval_block_rule(
                d_group, rule_id=rid, rule_type="single", description=desc, block_fn=fn, baseline=baseline
            )
        )
    rule_rows.sort(key=lambda r: float(r.get("pnl_improvement_yen") or 0), reverse=True)

    top_singles = rule_rows[:8]
    combo_rows: list[dict[str, Any]] = []
    for i, a in enumerate(top_singles[:6]):
        fn_a = next(f for rid, _, f in single_defs if rid == a["rule_id"])
        for b in top_singles[i + 1 : i + 4]:
            fn_b = next(f for rid, _, f in single_defs if rid == b["rule_id"])
            cid = f"combo_{a['rule_id']}__AND__{b['rule_id']}"
            cdesc = f"({a['rule_description']}) AND ({b['rule_description']})"

            def _combo(t: Mapping[str, Any], fa=fn_a, fb=fn_b) -> bool:
                return fa(t) and fb(t)

            combo_rows.append(
                _eval_block_rule(
                    d_group,
                    rule_id=cid,
                    rule_type="combo",
                    description=cdesc,
                    block_fn=_combo,
                    baseline=baseline,
                )
            )
    combo_rows.sort(key=lambda r: float(r.get("pnl_improvement_yen") or 0), reverse=True)
    all_rules = rule_rows + combo_rows

    best_single = rule_rows[0] if rule_rows else None
    best_combo = combo_rows[0] if combo_rows else None
    best_overall = max(all_rules, key=lambda r: float(r.get("pnl_improvement_yen") or 0)) if all_rules else None

    symbol_rows: list[dict[str, Any]] = []
    for sym in TARGET_SYMBOLS:
        sym_trades = [t for t in d_group if str(t.get("symbol") or "") == sym]
        sw = [t for t in sym_trades if _pnl_yen(t) > 0]
        sl = [t for t in sym_trades if _pnl_yen(t) < 0]
        sym_best = None
        sym_best_delta = None
        if best_single:
            fn = next(f for rid, _, f in single_defs if rid == best_single["rule_id"])
            blocked = [t for t in sym_trades if fn(t)]
            sym_best_delta = round(
                sum(_pnl_yen(t) for t in sym_trades if not fn(t))
                - sum(_pnl_yen(t) for t in sym_trades),
                2,
            )
            sym_best = best_single["rule_id"]
        separable = False
        if sym_trades and best_single:
            fn = next(f for rid, _, f in single_defs if rid == best_single["rule_id"])
            bl = sum(1 for t in sl if fn(t))
            bw = sum(1 for t in sw if fn(t))
            separable = bl >= max(1, len(sl) // 2) and bw <= max(1, len(sw) // 2)
        symbol_rows.append(
            {
                "symbol": sym,
                "d_win_count": len(sw),
                "d_loss_count": len(sl),
                "d_win_pnl": round(sum(_pnl_yen(t) for t in sw), 2),
                "d_loss_pnl": round(sum(_pnl_yen(t) for t in sl), 2),
                "net_pnl": round(sum(_pnl_yen(t) for t in sym_trades), 2),
                "best_single_rule": sym_best,
                "single_rule_blocked_losses": sum(
                    1 for t in sl if best_single and next(f for rid, _, f in single_defs if rid == best_single["rule_id"])(t)
                )
                if best_single
                else 0,
                "single_rule_blocked_wins": sum(
                    1 for t in sw if best_single and next(f for rid, _, f in single_defs if rid == best_single["rule_id"])(t)
                )
                if best_single
                else 0,
                "single_rule_pnl_delta": sym_best_delta,
                "separable_by_features": separable,
            }
        )

    sym6920_sep = next(r["separable_by_features"] for r in symbol_rows if r["symbol"] == "6920.T")
    sym4062_sep = next(r["separable_by_features"] for r in symbol_rows if r["symbol"] == "4062.T")
    sym6976_rem = best_overall.get("symbol_6976_remaining_pnl") if best_overall else baseline["symbol_6976_pnl"]
    sym6976_ok = float(sym6976_rem or 0) >= float(baseline["symbol_6976_pnl"]) * 0.85

    adoptable = [r for r in all_rules if r.get("adopt_pass")]
    if adoptable:
        verdict = "actionable_pattern_found"
    elif sym6920_sep or sym4062_sep:
        verdict = "symbol_specific_only"
    else:
        verdict = "no_actionable_pattern"

    overfit_risk = "high" if best_overall and float(best_overall.get("day_619_blocked_share") or 0) > 0.5 else (
        "medium" if best_overall and float(best_overall.get("single_symbol_blocked_share") or 0) > 0.4 else "low"
    )

    runtime_candidate = bool(adoptable) or (
        best_overall is not None and float(best_overall.get("pnl_improvement_yen") or 0) > 20000
    )

    expected_improvement = float(best_overall.get("pnl_improvement_yen") or 0) if best_overall else 0.0

    next_actions: list[str] = []
    if verdict == "actionable_pattern_found" and best_overall:
        next_actions.append(f"Shadow-eval rule: {best_overall.get('rule_description')}")
    elif verdict == "symbol_specific_only":
        next_actions.append("6920/4062 symbol-specific guard shadow — not global Board:mid rule")
    else:
        next_actions.append("No global D-group guard — keep Phase439+452; monitor D leakage")
    next_actions.append("Phase455B: walk-forward on rule if shadow positive")

    mandatory = {
        "1_d_win_count": len(d_win),
        "1_d_loss_count": len(d_loss),
        "2_d_net_pnl_yen": part_a["d_net_pnl"],
        "3_top5_features_by_effect": top5_features,
        "3_feature_detail_top5": feature_rows[:5],
        "4_6920_separable": sym6920_sep,
        "5_4062_separable": sym4062_sep,
        "6_6976_profit_preserved": sym6976_ok,
        "7_best_single_rule": best_single,
        "8_best_combo_rule": best_combo,
        "9_expected_improvement_yen": expected_improvement,
        "10_runtime_candidate": runtime_candidate,
        "11_overfit_risk": overfit_risk,
        "12_next_actions": next_actions,
        "verdict": verdict,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "population": "Phase454 D-group (518 trades)",
        "part_a": part_a,
        "baseline": baseline,
        "part_b_features": feature_rows,
        "part_c_d_single_rules": rule_rows[:30],
        "part_d_combo_rules": combo_rows[:20],
        "part_e_symbols": symbol_rows,
        "part_f_adoptable_rules": adoptable[:10],
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "_feature_rows": feature_rows,
        "_rule_rows": all_rules[:50],
        "_symbol_rows": symbol_rows,
    }


@dataclass
class Phase455Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase455_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "features": reports / "phase455_d_group_win_loss_features.csv",
            "rules": reports / "phase455_d_group_rule_candidates.csv",
            "symbols": reports / "phase455_d_group_symbol_breakdown.csv",
            "summary": reports / "phase455_d_group_summary.json",
        }
        _write_csv(paths["features"], FEATURE_CSV_FIELDS, list(result.get("_feature_rows") or []))
        _write_csv(paths["rules"], RULE_CSV_FIELDS, list(result.get("_rule_rows") or []))
        _write_csv(paths["symbols"], SYMBOL_CSV_FIELDS, list(result.get("_symbol_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase455_d_group_win_loss_feature_audit.md"
        m = result.get("mandatory_answers") or {}
        pa = result.get("part_a") or {}
        report.write_text(
            _render_report(result, m, pa),
            encoding="utf-8",
        )
        paths["report"] = report
        return paths


def _render_report(result: Mapping[str, Any], m: Mapping[str, Any], pa: Mapping[str, Any]) -> str:
    win = pa.get("d_win") or {}
    loss = pa.get("d_loss") or {}
    bs = m.get("7_best_single_rule") or {}
    bc = m.get("8_best_combo_rule") or {}
    lines = [
        "# Phase455 — D Group Win/Loss Feature Audit",
        "",
        f"Generated: {result.get('generated_at')}",
        f"Period: {result.get('period_start')}..{result.get('period_end')}",
        f"Population: {result.get('population')}",
        "",
        "## Mandatory answers",
        "",
        f"1. D_win / D_loss: **{m.get('1_d_win_count')} / {m.get('1_d_loss_count')}**",
        f"2. D net PnL: **{m.get('2_d_net_pnl_yen')}** yen",
        f"3. TOP5 features: **{m.get('3_top5_features_by_effect')}**",
        f"4. 6920 separable: **{m.get('4_6920_separable')}**",
        f"5. 4062 separable: **{m.get('5_4062_separable')}**",
        f"6. 6976 preserved: **{m.get('6_6976_profit_preserved')}**",
        f"7. Best single: `{bs.get('rule_description')}` (ΔPnL {bs.get('pnl_improvement_yen')})",
        f"8. Best combo: `{bc.get('rule_description')}` (ΔPnL {bc.get('pnl_improvement_yen')})",
        f"9. Expected improvement: **{m.get('9_expected_improvement_yen')}** yen",
        f"10. Runtime candidate: **{m.get('10_runtime_candidate')}**",
        f"11. Overfit risk: **{m.get('11_overfit_risk')}**",
        f"12. Next: {m.get('12_next_actions')}",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "",
        "## Part A",
        "",
        f"| | D_win | D_loss |",
        f"|--|-------|--------|",
        f"| count | {win.get('count')} | {loss.get('count')} |",
        f"| PnL | {win.get('pnl_yen')} | {loss.get('pnl_yen')} |",
        f"| avg | {win.get('avg_pnl_yen')} | {loss.get('avg_pnl_yen')} |",
        f"| PF | {win.get('profit_factor')} | {loss.get('profit_factor')} |",
        f"| stop | {win.get('stop_rate')} | {loss.get('stop_rate')} |",
        f"| symbols | {win.get('symbol_count')} | {loss.get('symbol_count')} |",
        "",
        "See CSV/JSON for Parts B–F.",
        "",
    ]
    return "\n".join(lines)
