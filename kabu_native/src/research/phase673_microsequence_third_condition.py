"""Phase673 — Third-condition search on Phase672 best 2-condition combo (research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.phase671_early_stop_feature_discovery import _is_leaky_feature
from research.phase672_pre_entry_microsequence import (
    BIG_WINNER_YEN,
    MICROSEQ_FEATURES,
    _attach_microsequence,
    _build_price_index_canonical,
    _enrich_trade_labels,
    _is_winner,
    _load_canonical_trades_with_session,
    _load_signal_index,
)
from research.phase631_profit_source_attribution import _num
from research.structural_trade_normalize import resolve_kabu_root

PHASE673_VERDICT_FOUND_CANDIDATE = "FOUND_CANDIDATE"
PHASE673_VERDICT_HOLD = "HOLD"
PHASE673_VERDICT_REJECT = "REJECT"
PHASE673_VERDICT_DATA_GAP = "DATA_GAP"
REPORT_DIR_NAME = "phase673_microsequence_third_condition"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
DISK_USAGE_MAX_PCT = 75.0

BASE_BOUNCE_THR = 0.2182
BASE_FALL_THR = -0.1735
BASE_RULE_ID = (
    f"base_bounce_ge_{BASE_BOUNCE_THR}_AND_fall_le_{abs(BASE_FALL_THR)}"
)

SHADOW_CAUTIOUS = frozenset(
    {
        "flat_weak_range_shadow_block",
        "flat_band_mainline_would_block",
        "num_flat_weak_range_shadow_block",
        "num_flat_band_mainline_would_block",
    }
)

FORBIDDEN_THIRD_FEATURES = frozenset(
    {
        "bounce_from_recent_low",
        "fall_from_recent_high",
        "symbol",
        "day",
        "session",
        "entry_time",
        "exit_time",
        "pnl_yen_100",
        "pnl_pct",
        "hold_sec",
        "microsequence_ok",
        "board_microsequence_ok",
        "winner",
        "early_stop",
        "normal_stop",
        "no_progress_exit",
        "post_flat_band_entry",
        "candidate_signal_time",
        "accept_time",
    }
)

EXTRA_FEATURE_HINTS = (
    "pretrend",
    "flat",
    "vwap",
    "volume",
    "trading_value",
    "turnover",
    "board",
    "imbalance",
    "spread",
    "momentum",
    "entry_score",
    "expectancy",
    "rise",
    "return_",
    "price_age",
    "board_age",
    "token",
    "liquidity",
    "quality",
    "breakout",
    "shape",
    "subclass",
)


def _base_combo(t: Mapping[str, Any]) -> bool:
    bounce = _num(t.get("bounce_from_recent_low"))
    fall = _num(t.get("fall_from_recent_high"))
    if bounce is None or fall is None:
        return False
    return float(bounce) >= BASE_BOUNCE_THR and float(fall) <= BASE_FALL_THR


def _is_loser(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) < 0


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) >= BIG_WINNER_YEN


def _pool_tree_rules(pool: Sequence[Mapping[str, Any]], features: Sequence[str], *, max_depth: int = 3) -> list[dict[str, Any]]:
    try:
        import numpy as np
        from sklearn.tree import DecisionTreeClassifier, export_text
    except ImportError:
        return []

    y = [1 if _is_loser(t) else 0 for t in pool]
    if sum(y) < 5 or len(y) - sum(y) < 5:
        return []

    X_cols: list[str] = []
    matrix: list[list[float]] = []
    for f in features:
        vals = [_num(t.get(f)) for t in pool]
        if sum(1 for v in vals if v is not None) < 10:
            continue
        med = statistics.median([float(v) for v in vals if v is not None])
        X_cols.append(f)
        matrix.append([float(v if v is not None else med) for v in vals])
    if not X_cols:
        return []

    X = np.array(matrix, dtype=float).T
    clf = DecisionTreeClassifier(max_depth=max_depth, min_samples_leaf=max(8, len(y) // 40), random_state=42)
    clf.fit(X, y)
    text = export_text(clf, feature_names=X_cols, max_depth=max_depth)
    rows: list[dict[str, Any]] = []
    for i, ln in enumerate([ln.strip() for ln in text.splitlines() if ln.strip()][:40], start=1):
        rows.append({"rule_line": i, "tree_export": ln})
    return rows


def _is_allowed_third_feature(key: str) -> bool:
    k = str(key)
    if k in FORBIDDEN_THIRD_FEATURES:
        return False
    if _is_leaky_feature(k):
        return False
    if k.startswith("hour_") or k.startswith("minute_") or "symbol" in k.lower():
        return False
    return True


def _collect_third_feature_candidates(trades: Sequence[Mapping[str, Any]]) -> list[str]:
    keys: set[str] = set()
    for feat in MICROSEQ_FEATURES:
        if _is_allowed_third_feature(feat):
            keys.add(feat)
    for t in trades:
        for k, v in t.items():
            if not _is_allowed_third_feature(k):
                continue
            if isinstance(v, bool) or k.startswith("num_"):
                keys.add(k)
                continue
            if _num(v) is not None:
                keys.add(k)
            if any(h in k.lower() for h in EXTRA_FEATURE_HINTS) and _num(v) is not None:
                keys.add(k)
    return sorted(keys)


def load_microsequence_trades(*, skip_enrich: bool = False) -> list[dict[str, Any]]:
    repo_root = resolve_kabu_root(NATIVE_ROOT)
    push_root = repo_root / "data" / "push_jsonl"
    trades = _load_canonical_trades_with_session(repo_root)
    price_idx = _build_price_index_canonical(repo_root)
    if not skip_enrich:
        trades = _enrich_trade_labels(trades, repo_root=repo_root, price_idx=price_idx)
    signal_index = _load_signal_index()
    return _attach_microsequence(
        trades,
        push_root=push_root,
        signal_index=signal_index,
        price_idx=price_idx,
    )


def _feature_ranking(
    pool: Sequence[Mapping[str, Any]],
    *,
    features: Sequence[str],
    pos_label: str,
    neg_label: str,
    pos_pred: Callable[[Mapping[str, Any]], bool],
    neg_pred: Callable[[Mapping[str, Any]], bool],
) -> list[dict[str, Any]]:
    pos = [t for t in pool if pos_pred(t)]
    neg = [t for t in pool if neg_pred(t)]
    rows: list[dict[str, Any]] = []
    for feat in features:
        pv = [float(_num(t.get(feat)) or 0) for t in pos if _num(t.get(feat)) is not None]
        nv = [float(_num(t.get(feat)) or 0) for t in neg if _num(t.get(feat)) is not None]
        if len(pv) < 5 or len(nv) < 5:
            continue
        d = _cohens_d(pv, nv)
        mi = _mi_median_split(pv, nv)
        rows.append(
            {
                "comparison": f"{pos_label}_vs_{neg_label}",
                "feature": feat,
                f"{pos_label}_mean": round(statistics.mean(pv), 4),
                f"{neg_label}_mean": round(statistics.mean(nv), 4),
                "cohens_d": round(d, 4) if d is not None else None,
                "mutual_information": round(mi, 6) if mi is not None else None,
                f"{pos_label}_n": len(pv),
                f"{neg_label}_n": len(nv),
                "shadow_cautious": feat in SHADOW_CAUTIOUS,
            }
        )
    rows.sort(
        key=lambda r: (abs(float(r.get("cohens_d") or 0)), float(r.get("mutual_information") or 0)),
        reverse=True,
    )
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def _permutation_importance(
    pool: Sequence[Mapping[str, Any]],
    features: Sequence[str],
    *,
    top_n: int = 15,
) -> list[dict[str, Any]]:
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.inspection import permutation_importance
    except ImportError:
        return []

    y = [1 if _is_loser(t) else 0 for t in pool]
    if sum(y) < 10 or len(y) - sum(y) < 10:
        return []

    cols: list[str] = []
    matrix: list[list[float]] = []
    for f in features:
        vals = [_num(t.get(f)) for t in pool]
        if sum(1 for v in vals if v is not None) < 20:
            continue
        med = statistics.median([float(v) for v in vals if v is not None])
        cols.append(f)
        matrix.append([float(v if v is not None else med) for v in vals])
    if not cols:
        return []

    X = np.array(matrix, dtype=float).T
    clf = RandomForestClassifier(n_estimators=80, max_depth=4, random_state=42, min_samples_leaf=5)
    clf.fit(X, y)
    pi = permutation_importance(clf, X, y, n_repeats=8, random_state=42, n_jobs=1)
    rows = [
        {
            "feature": cols[i],
            "permutation_importance_mean": round(float(pi.importances_mean[i]), 6),
            "permutation_importance_std": round(float(pi.importances_std[i]), 6),
        }
        for i in range(len(cols))
    ]
    rows.sort(key=lambda r: float(r["permutation_importance_mean"]), reverse=True)
    for i, r in enumerate(rows[:top_n], start=1):
        r["rank"] = i
    return rows[:top_n]


def _day_pnl(trades: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for t in trades:
        out[str(t.get("day") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    return dict(out)


def _max_drawdown(yens: Sequence[float]) -> float:
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for y in yens:
        eq += y
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return round(max_dd, 2)


def _eval_third_rule(
    all_trades: Sequence[Mapping[str, Any]],
    *,
    rule_id: str,
    third_pred: Callable[[Mapping[str, Any]], bool],
    baseline_early_stop: int,
) -> dict[str, Any]:
    chron = sorted(all_trades, key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    base_m = _metrics(list(chron))

    def _block(t: Mapping[str, Any]) -> bool:
        return _base_combo(t) and third_pred(t)

    blocked = [t for t in chron if _block(t)]
    kept = [t for t in chron if not _block(t)]
    kept_m = _metrics(kept)

    bw = sum(1 for t in blocked if _is_winner(t))
    bl = sum(1 for t in blocked if _is_loser(t))
    bbw = sum(1 for t in blocked if _is_big_winner(t))
    bes = sum(1 for t in blocked if t.get("early_stop"))

    base_day = _day_pnl(chron)
    kept_day = _day_pnl(kept)
    improved = sum(1 for d in base_day if kept_day.get(d, 0) > base_day[d])
    day_total = len(base_day) or 1

    by_sym: dict[str, int] = defaultdict(int)
    for t in blocked:
        by_sym[str(t.get("symbol") or "")] += 1
    top_sym = max(by_sym.items(), key=lambda x: x[1]) if by_sym else ("", 0)
    concentration = round(top_sym[1] / len(blocked), 4) if blocked else 0.0

    flat_kept = [t for t in kept if t.get("post_flat_band_entry")]
    flat_base = [t for t in chron if t.get("post_flat_band_entry")]
    post_flat_delta = round(
        float(_metrics(flat_kept).get("pnl_yen_100") or 0) - float(_metrics(flat_base).get("pnl_yen_100") or 0),
        2,
    )

    base_dd = _max_drawdown([float(t.get("pnl_yen_100") or 0) for t in chron])
    kept_dd = _max_drawdown([float(t.get("pnl_yen_100") or 0) for t in kept])

    return {
        "rule_id": rule_id,
        "blocked_count": len(blocked),
        "blocked_winners": bw,
        "blocked_losers": bl,
        "blocked_big_winners": bbw,
        "blocked_early_stop": bes,
        "early_stop_reduction": round(bes / baseline_early_stop, 4) if baseline_early_stop else 0.0,
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base_m.get("pnl_yen_100") or 0), 2),
        "baseline_pf": base_m.get("profit_factor"),
        "scenario_pf": kept_m.get("profit_factor"),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base_m.get("profit_factor") or 0), 4),
        "baseline_dd_yen": base_dd,
        "scenario_dd_yen": kept_dd,
        "dd_delta_yen": round(kept_dd - base_dd, 2),
        "improved_days": improved,
        "improved_days_rate": round(improved / day_total, 4),
        "top_symbol": top_sym[0],
        "top_symbol_blocked": top_sym[1],
        "top_symbol_concentration": concentration,
        "post_flat_band_delta_pnl_yen": post_flat_delta,
        "winner_loser_gap": bl - bw,
    }


def _median(vals: Sequence[float]) -> float:
    return statistics.median(list(vals)) if vals else 0.0


def _pick_threshold(vals: Sequence[float], q: float) -> float:
    ordered = sorted(vals)
    if not ordered:
        return 0.0
    idx = min(max(int(len(ordered) * q), 0), len(ordered) - 1)
    return ordered[idx]


def _sweep_third_conditions(
    all_trades: Sequence[Mapping[str, Any]],
    pool: Sequence[Mapping[str, Any]],
    features: Sequence[str],
    *,
    baseline_early_stop: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    losers = [t for t in pool if _is_loser(t)]
    winners = [t for t in pool if _is_winner(t)]

    for feat in features:
        vals_l = [float(_num(t.get(feat)) or 0) for t in losers if _num(t.get(feat)) is not None]
        vals_w = [float(_num(t.get(feat)) or 0) for t in winners if _num(t.get(feat)) is not None]
        if len(vals_l) < 5:
            continue

        unique = sorted(set(vals_l + vals_w))
        is_flag = unique == [0.0, 1.0] or unique == [0.0] or unique == [1.0]
        thresholds: list[tuple[str, float, Callable[[Mapping[str, Any]], bool]]] = []

        if is_flag or feat in SHADOW_CAUTIOUS:
            thresholds.append(
                (
                    f"{feat}==1",
                    1.0,
                    lambda t, f=feat: bool(_num(t.get(f)) or 0) >= 0.5,
                )
            )
            thresholds.append(
                (
                    f"{feat}==0",
                    0.0,
                    lambda t, f=feat: bool((_num(t.get(f)) or 0) < 0.5),
                )
            )
        else:
            qs = (0.25, 0.33, 0.5, 0.67, 0.75)
            for q in qs:
                thr_l = _pick_threshold(vals_l, q)
                thr_w = _pick_threshold(vals_w, q) if vals_w else thr_l
                for thr in {thr_l, thr_w}:
                    thresholds.append(
                        (
                            f"{feat}>={thr:.6g}",
                            thr,
                            lambda t, f=feat, th=thr: (_num(t.get(f)) or -1e18) >= th,
                        )
                    )
                    thresholds.append(
                        (
                            f"{feat}<={thr:.6g}",
                            thr,
                            lambda t, f=feat, th=thr: (_num(t.get(f)) or 1e18) <= th,
                        )
                    )

        seen_ids: set[str] = set()
        for label, thr, pred in thresholds:
            rid = f"{BASE_RULE_ID}_AND_{label}"
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            row = _eval_third_rule(all_trades, rule_id=rid, third_pred=pred, baseline_early_stop=baseline_early_stop)
            row["third_feature"] = feat
            row["third_operator"] = label
            row["third_threshold"] = thr
            row["shadow_cautious"] = feat in SHADOW_CAUTIOUS
            rows.append(row)

    return rows


def _score_rule(row: Mapping[str, Any], *, baseline: Mapping[str, Any]) -> float:
    delta = float(row.get("delta_pnl_yen") or 0)
    pf_d = float(row.get("pf_delta") or 0)
    bw = int(row.get("blocked_winners") or 0)
    bl = int(row.get("blocked_losers") or 0)
    bbw = int(row.get("blocked_big_winners") or 0)
    base_bbw = int(baseline.get("blocked_big_winners") or 0)
    early_red = float(row.get("early_stop_reduction") or 0)
    base_early = float(baseline.get("early_stop_reduction") or 0)
    imp = float(row.get("improved_days_rate") or 0)
    conc = float(row.get("top_symbol_concentration") or 1)

    score = 0.0
    if delta > 0:
        score += min(delta / 100000.0, 5.0)
    if pf_d >= 0.03:
        score += 2.0
    elif pf_d > 0:
        score += pf_d * 10
    if bl >= bw:
        score += 1.5
    else:
        score -= (bw - bl) * 0.01
    if bbw < base_bbw:
        score += (base_bbw - bbw) * 0.15
    else:
        score -= (bbw - base_bbw) * 0.1
    if early_red >= base_early * 0.7:
        score += 1.0
    else:
        score -= (base_early - early_red) * 2
    if imp >= 0.6:
        score += 1.5
    if conc <= 0.25:
        score += 0.5
    return round(score, 4)


def _mandatory_comparisons(
    all_trades: Sequence[Mapping[str, Any]],
    *,
    baseline_early_stop: int,
) -> list[dict[str, Any]]:
    board_thr = _median(
        [float(_num(t.get("board_imbalance_drop")) or 0) for t in all_trades if _base_combo(t) and _num(t.get("board_imbalance_drop")) is not None]
    )
    sig_thr = _median(
        [float(_num(t.get("signal_to_accept_return")) or 0) for t in all_trades if _base_combo(t) and _num(t.get("signal_to_accept_return")) is not None]
    )
    spread_thr = _median(
        [float(_num(t.get("spread_bps_change")) or 0) for t in all_trades if _base_combo(t) and _num(t.get("spread_bps_change")) is not None]
    )
    down_thr = _median(
        [float(_num(t.get("down_tick_ratio")) or 0) for t in all_trades if _base_combo(t) and _num(t.get("down_tick_ratio")) is not None]
    )

    scenarios: list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]] = [
        ("A_base_combo_only", "Phase672 best combo only", lambda t: True),
        ("B_plus_board_deterioration", "board_imbalance_drop", lambda t, th=board_thr: (_num(t.get("board_imbalance_drop")) or 0) >= th),
        ("C_plus_signal_to_accept_worse", "signal_to_accept_return", lambda t, th=sig_thr: (_num(t.get("signal_to_accept_return")) or 0) <= th),
        ("D_plus_fake_breakout", "fake_breakout_signature", lambda t: bool(_num(t.get("fake_breakout_signature")))),
        ("E_plus_spread_expansion", "spread_bps_change", lambda t, th=spread_thr: (_num(t.get("spread_bps_change")) or 0) >= th),
        (
            "F_plus_bid_disappear_or_ask_growth",
            "bid_disappear_or_ask_growth",
            lambda t: (_num(t.get("bid_disappear_count")) or 0) >= 1 or (_num(t.get("best_ask_size_increase")) or 0) > 0,
        ),
        (
            "G_plus_tick_down_reversal",
            "down_tick_or_consecutive_down",
            lambda t, th=down_thr: (_num(t.get("down_tick_ratio")) or 0) >= th or (_num(t.get("consecutive_down_ticks")) or 0) >= 2,
        ),
        (
            "H_plus_liquidity_drop",
            "liquidity_pressure",
            lambda t: (_num(t.get("quote_update_rate")) or 999) <= 0.5 or (_num(t.get("sell_pressure_proxy")) or 0) > (_num(t.get("buy_pressure_proxy")) or 0),
        ),
        (
            "I_plus_flat_weak",
            "flat_weak_or_pretrend_D",
            lambda t: bool(t.get("flat_weak_range_shadow_block")) or str(t.get("pretrend_shape") or "") in ("D", "C"),
        ),
    ]

    rows: list[dict[str, Any]] = []
    for sid, _desc, third in scenarios:
        if sid == "A_base_combo_only":
            row = _eval_third_rule(
                all_trades,
                rule_id=sid,
                third_pred=lambda t: True,
                baseline_early_stop=baseline_early_stop,
            )
        else:
            row = _eval_third_rule(
                all_trades,
                rule_id=sid,
                third_pred=third,
                baseline_early_stop=baseline_early_stop,
            )
        row["scenario_id"] = sid
        rows.append(row)
    return rows


def _decide_verdict(
    *,
    pool_count: int,
    baseline: Mapping[str, Any],
    best_auto: Optional[Mapping[str, Any]],
    coverage: float,
) -> tuple[str, dict[str, Any]]:
    if coverage < 0.5 or pool_count < 50:
        return PHASE673_VERDICT_DATA_GAP, {"reason": "insufficient pool or feature coverage"}

    if not best_auto:
        return PHASE673_VERDICT_REJECT, {"reason": "no auto third condition evaluated"}

    base_bbw = int(baseline.get("blocked_big_winners") or 0)
    auto_bbw = int(best_auto.get("blocked_big_winners") or 0)
    bw_reduced = auto_bbw < base_bbw - 5
    delta = float(best_auto.get("delta_pnl_yen") or 0)
    pf_d = float(best_auto.get("pf_delta") or 0)
    wl_gap = int(best_auto.get("winner_loser_gap") or 0)
    imp = float(best_auto.get("improved_days_rate") or 0)

    answers = {
        "pool_count": pool_count,
        "baseline_blocked_winners": baseline.get("blocked_winners"),
        "baseline_blocked_big_winners": base_bbw,
        "best_auto_rule": best_auto.get("rule_id"),
        "best_auto_blocked_winners": best_auto.get("blocked_winners"),
        "best_auto_blocked_big_winners": auto_bbw,
        "big_winner_reduction": base_bbw - auto_bbw,
        "best_auto_delta_pnl": delta,
        "best_auto_pf_delta": pf_d,
        "best_auto_winner_loser_gap": wl_gap,
        "best_auto_improved_days_rate": imp,
    }

    if (
        bw_reduced
        and delta > 0
        and pf_d >= 0.03
        and wl_gap >= -10
        and imp >= 0.55
        and float(best_auto.get("early_stop_reduction") or 0) >= float(baseline.get("early_stop_reduction") or 0) * 0.65
    ):
        return PHASE673_VERDICT_FOUND_CANDIDATE, answers
    if delta > 0 and (bw_reduced or wl_gap > int(baseline.get("winner_loser_gap") or 0)):
        return PHASE673_VERDICT_HOLD, answers
    return PHASE673_VERDICT_REJECT, answers


def run_audit(*, skip_enrich: bool = False) -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_microsequence_trades(skip_enrich=skip_enrich)
    ok = [t for t in trades if t.get("microsequence_ok")]
    coverage = len(ok) / len(trades) if trades else 0.0

    pool = [t for t in ok if _base_combo(t)]
    baseline_early_stop = sum(1 for t in ok if t.get("early_stop"))

    features = _collect_third_feature_candidates(ok)
    if len(pool) < 50 or not features:
        report = {
            "verdict": PHASE673_VERDICT_DATA_GAP,
            "entry_count": len(trades),
            "pool_count": len(pool),
            "feature_count": len(features),
            "microsequence_coverage": round(coverage, 4),
        }
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        (REPORT_ROOT / "phase673_third_condition_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_decision_md(report=report)
        return report

    rank_wl = _feature_ranking(
        pool,
        features=features,
        pos_label="loser",
        neg_label="winner",
        pos_pred=_is_loser,
        neg_pred=_is_winner,
    )
    rank_big = _feature_ranking(
        pool,
        features=features,
        pos_label="loser",
        neg_label="big_winner",
        pos_pred=_is_loser,
        neg_pred=_is_big_winner,
    )
    feature_rank = rank_wl + rank_big
    perm_imp = _permutation_importance(pool, features)

    sweep_rows = _sweep_third_conditions(ok, pool, features, baseline_early_stop=baseline_early_stop)
    mandatory = _mandatory_comparisons(ok, baseline_early_stop=baseline_early_stop)
    baseline_a = next(r for r in mandatory if r.get("scenario_id") == "A_base_combo_only")

    for row in sweep_rows:
        row["score"] = _score_rule(row, baseline=baseline_a)
    sweep_rows.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    for i, r in enumerate(sweep_rows, start=1):
        r["rank"] = i

    non_shadow = [r for r in sweep_rows if not r.get("shadow_cautious")]
    best_auto = non_shadow[0] if non_shadow else (sweep_rows[0] if sweep_rows else None)

    cf_rows = list(mandatory)
    if best_auto:
        j_row = dict(best_auto)
        j_row["scenario_id"] = "J_auto_best_third"
        cf_rows.append(j_row)
    for i, r in enumerate(cf_rows, start=1):
        r["rank"] = i

    top_feats = [str(r["feature"]) for r in rank_wl[:12]]
    tree_rows = _pool_tree_rules(pool, top_feats, max_depth=3)

    verdict, answers = _decide_verdict(
        pool_count=len(pool),
        baseline=baseline_a,
        best_auto=best_auto,
        coverage=coverage,
    )

    disk_after = _disk_usage_pct(NATIVE_ROOT)
    report: dict[str, Any] = {
        "verdict": verdict,
        "entry_count": len(trades),
        "microsequence_ok_count": len(ok),
        "microsequence_coverage": round(coverage, 4),
        "pool_count": len(pool),
        "pool_winners": sum(1 for t in pool if _is_winner(t)),
        "pool_losers": sum(1 for t in pool if _is_loser(t)),
        "pool_early_stop": sum(1 for t in pool if t.get("early_stop")),
        "pool_big_winners": sum(1 for t in pool if _is_big_winner(t)),
        "base_combo_thresholds": {"bounce_from_recent_low_ge": BASE_BOUNCE_THR, "fall_from_recent_high_le": BASE_FALL_THR},
        "mandatory_answers": answers,
        "baseline_A": baseline_a,
        "best_auto_third": best_auto,
        "top_sweep_rules": sweep_rows[:15],
        "permutation_importance_top": perm_imp,
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": disk_after,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase673_third_condition_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        REPORT_ROOT / "phase673_third_condition_feature_rank.csv",
        [
            "rank",
            "comparison",
            "feature",
            "loser_mean",
            "winner_mean",
            "cohens_d",
            "mutual_information",
            "shadow_cautious",
        ],
        feature_rank,
    )
    _write_csv(
        REPORT_ROOT / "phase673_third_condition_sweep.csv",
        [
            "rank",
            "rule_id",
            "third_feature",
            "third_operator",
            "score",
            "blocked_count",
            "blocked_winners",
            "blocked_losers",
            "blocked_big_winners",
            "blocked_early_stop",
            "early_stop_reduction",
            "delta_pnl_yen",
            "pf_delta",
            "dd_delta_yen",
            "improved_days_rate",
            "top_symbol_concentration",
            "post_flat_band_delta_pnl_yen",
            "shadow_cautious",
        ],
        sweep_rows[:200],
    )
    _write_csv(
        REPORT_ROOT / "phase673_third_condition_counterfactual.csv",
        [
            "rank",
            "scenario_id",
            "rule_id",
            "blocked_count",
            "blocked_winners",
            "blocked_losers",
            "blocked_big_winners",
            "blocked_early_stop",
            "early_stop_reduction",
            "delta_pnl_yen",
            "pf_delta",
            "improved_days_rate",
            "top_symbol",
            "top_symbol_blocked",
            "post_flat_band_delta_pnl_yen",
        ],
        cf_rows,
    )
    _write_csv(REPORT_ROOT / "phase673_tree_rules.csv", ["rule_line", "tree_export"], tree_rows)
    _write_candidate_rules_md(report=report, sweep_rows=sweep_rows[:20], mandatory=mandatory)
    _write_decision_md(report=report)
    return report


def _write_candidate_rules_md(
    *,
    report: Mapping[str, Any],
    sweep_rows: Sequence[Mapping[str, Any]],
    mandatory: Sequence[Mapping[str, Any]],
) -> None:
    base = report.get("baseline_A") or {}
    lines = [
        "# Phase673 — Third Condition Candidate Rules",
        "",
        f"Pool size (Phase672 2-condition): {report.get('pool_count')}",
        "",
        "## Baseline A (Phase672 combo)",
        "",
        f"- blocked: {base.get('blocked_count')} (W/L/BW: {base.get('blocked_winners')}/{base.get('blocked_losers')}/{base.get('blocked_big_winners')})",
        f"- ΔPnL: {float(base.get('delta_pnl_yen') or 0):+,.0f}",
        f"- early_stop_reduction: {float(base.get('early_stop_reduction') or 0) * 100:.1f}%",
        "",
        "## Mandatory comparisons",
        "",
    ]
    for row in mandatory:
        lines.append(
            f"- **{row.get('scenario_id')}**: blocked={row.get('blocked_count')} "
            f"W/L/BW={row.get('blocked_winners')}/{row.get('blocked_losers')}/{row.get('blocked_big_winners')} "
            f"ΔPnL={float(row.get('delta_pnl_yen') or 0):+,.0f}"
        )
    lines.extend(["", "## Top auto-swept 3-condition rules", ""])
    for row in sweep_rows[:10]:
        lines.append(
            f"- `{row.get('rule_id')}` score={row.get('score')} "
            f"BW={row.get('blocked_big_winners')} W/L={row.get('blocked_winners')}/{row.get('blocked_losers')} "
            f"ΔPnL={float(row.get('delta_pnl_yen') or 0):+,.0f}"
        )
    (REPORT_ROOT / "phase673_candidate_rules.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_decision_md(*, report: Mapping[str, Any]) -> None:
    ans = report.get("mandatory_answers") or {}
    base = report.get("baseline_A") or {}
    best = report.get("best_auto_third") or {}
    lines = [
        "# Phase673 — Microsequence 3rd Condition Search",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        f"- Pool (2-condition match): {report.get('pool_count')} / {report.get('entry_count')} entries",
        f"- Baseline A blocked big winners: {base.get('blocked_big_winners')}",
        f"- Best auto 3rd blocked big winners: {best.get('blocked_big_winners')}",
        f"- Big-winner reduction: {ans.get('big_winner_reduction')}",
        "",
        "## Best auto 3rd condition",
        "",
        f"- Rule: `{best.get('rule_id', 'n/a')}`",
        f"- ΔPnL: {float(best.get('delta_pnl_yen') or 0):+,.0f} yen (baseline A: {float(base.get('delta_pnl_yen') or 0):+,.0f})",
        f"- Blocked W/L: {best.get('blocked_winners')}/{best.get('blocked_losers')} (baseline: {base.get('blocked_winners')}/{base.get('blocked_losers')})",
        f"- Improved days rate: {float(best.get('improved_days_rate') or ans.get('best_auto_improved_days_rate') or 0) * 100:.1f}%",
        "",
        "## Constraints",
        "",
        "- Runtime / YAML / Shadow 変更なし",
        "- 時間帯・銘柄ブラックリスト禁止",
        "",
    ]
    (REPORT_ROOT / "phase673_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(
        json.dumps(
            {
                "verdict": report.get("verdict"),
                "pool_count": report.get("pool_count"),
                "best_auto": (report.get("best_auto_third") or {}).get("rule_id"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
