"""Chronological walk-forward validation for Winner Feature Filter candidates."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from research.winner_feature_filter.labels import LabeledTrade
from research.winner_feature_filter.lanes import LANE_B_SUSPECT_DAYS, lane_of, rule_lanes

COST_BPS = 0.05


def _f(row: Mapping[str, Optional[float]], key: str) -> Optional[float]:
    v = row.get(key)
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if np.isnan(x) or np.isinf(x):
        return None
    return x


def _first(row: Mapping[str, Optional[float]], keys: Sequence[str]) -> Optional[float]:
    for k in keys:
        v = _f(row, k)
        if v is not None:
            return v
    return None


@dataclass(frozen=True)
class Predicate:
    feature_keys: tuple[str, ...]  # aliases, first available used
    op: str  # ">=" or "<="
    threshold: float
    name: str

    def value(self, row: Mapping[str, Optional[float]]) -> Optional[float]:
        return _first(row, self.feature_keys)

    def holds(self, row: Mapping[str, Optional[float]]) -> Optional[bool]:
        v = self.value(row)
        if v is None:
            return None  # missing — not True
        if self.op == ">=":
            return v >= self.threshold
        return v <= self.threshold


@dataclass
class CandidateSpec:
    cand_id: str
    predicates: list[Predicate]
    market_state: str
    rise_hypothesis: str
    failure_exit: str
    missing_feature_risk: str

    @property
    def feature_names(self) -> list[str]:
        return [p.feature_keys[0] for p in self.predicates]

    @property
    def lanes(self) -> set[str]:
        return rule_lanes(self.feature_names)

    def requires_lane_c(self) -> bool:
        return "C" in self.lanes

    def requires_lane_b(self) -> bool:
        return "B" in self.lanes


def fixed_candidates() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            cand_id="A",
            predicates=[
                Predicate(("f_tv", "vol_tv"), ">=", 3.53727e9, "TV"),
                Predicate(("f_chase", "tech_chase"), "<=", 1.0919, "chase"),
                Predicate(("f_rise5", "px_ma_proxy_5m"), "<=", -0.3989, "rise5"),
            ],
            market_state="高TVかつchase弱く、直近5分が押し目（負リターン）の状態",
            rise_hypothesis="流動性のある銘柄で過熱chaseを避け、押し目からの再開を狙う",
            failure_exit="押し目継続ならSTOP、再開失敗ならNoProgress",
            missing_feature_risk="rise5欠損時は条件を満たせず除外（補完しない）",
        ),
        CandidateSpec(
            cand_id="B",
            predicates=[
                Predicate(("board_imb", "f_imb"), ">=", 0.545768, "imbalance"),
                Predicate(("w_60s_ret", "f_r60", "f_np_ret_60"), "<=", 0.10836, "return60"),
                Predicate(("vol_surge_60s", "w_60s_tv_chg", "f_np_tv_chg_pct_60"), ">=", 0.26711, "volume60_chg_pct"),
            ],
            market_state="買い板優勢 + 60s価格は伸びすぎず + 60s出来高急増",
            rise_hypothesis="板が残ったまま出来高が乗り、価格はまだ伸び切っていない初動",
            failure_exit="板剥落→STOP、出来高一過性→NoProgress",
            missing_feature_risk="Lane C (volume60/return60-NP) 欠損行は評価母集団から除外",
        ),
        CandidateSpec(
            cand_id="C",
            predicates=[
                Predicate(("f_vwap", "px_vwap_dev"), "<=", -0.43878, "VWAP乖離"),
                Predicate(("vol_surge_60s", "w_60s_tv_chg", "f_np_tv_chg_pct_60"), ">=", 0.26711, "volume60_chg_pct"),
            ],
            market_state="VWAP下（相対割安）かつ出来高急増",
            rise_hypothesis="VWAP回帰 + 出来高を伴う反発初動",
            failure_exit="VWAP割れ継続→STOP、出来高消失→NoProgress",
            missing_feature_risk="volume60欠損はLane C除外（補完禁止）",
        ),
        CandidateSpec(
            cand_id="D",
            predicates=[
                Predicate(("board_imb", "f_imb"), ">=", 0.545768, "imbalance"),
                Predicate(("f_vwap", "px_vwap_dev"), "<=", -0.43878, "VWAP乖離"),
                Predicate(("f_atr", "px_atr"), "<=", 6.49486, "ATR"),
            ],
            market_state="買い板優勢 + VWAP下 + 低ATR（過熱しすぎない）",
            rise_hypothesis="落ち着いた波動の中で板が買い優勢かつVWAP下の押し目買い",
            failure_exit="板崩れ→STOP、停滞→NoProgress",
            missing_feature_risk="Lane B品質不良日を含むと閾値の意味が歪む",
        ),
        CandidateSpec(
            cand_id="E",
            predicates=[
                Predicate(("board_imb", "f_imb"), ">=", 0.545768, "imbalance"),
                Predicate(("board_spread", "f_spread"), "<=", 5.96604, "spread_bps"),
                Predicate(("f_tv", "vol_tv"), ">=", 3.53727e9, "TV"),
            ],
            market_state="高TV + 買い板優勢 + スプレッド狭い（流動性良好）",
            rise_hypothesis="流動性・板・スプレッドが揃った「取引しやすい上昇銘柄」状態",
            failure_exit="スプレッド拡大・板剥落→STOP",
            missing_feature_risk="spreadはLane C。実測がある行のみ。母数は後半日に偏る",
        ),
    ]


def eligible_mask(
    rows: Sequence[Mapping[str, Optional[float]]],
    spec: CandidateSpec,
    *,
    require_all_observed: bool = True,
) -> np.ndarray:
    """True where all predicate features are observed (Lane C / sparse safe)."""
    out = np.ones(len(rows), dtype=bool)
    if not require_all_observed:
        return out
    for i, row in enumerate(rows):
        for p in spec.predicates:
            if p.value(row) is None:
                out[i] = False
                break
    return out


def apply_rule(
    rows: Sequence[Mapping[str, Optional[float]]],
    spec: CandidateSpec,
    *,
    thresholds: Optional[Mapping[str, float]] = None,
) -> np.ndarray:
    """Keep mask: all predicates True. Missing feature → not kept."""
    kept = np.ones(len(rows), dtype=bool)
    for p in spec.predicates:
        thr = float(thresholds[p.name]) if thresholds and p.name in thresholds else p.threshold
        col_ok = np.zeros(len(rows), dtype=bool)
        for i, row in enumerate(rows):
            v = p.value(row)
            if v is None:
                col_ok[i] = False
            elif p.op == ">=":
                col_ok[i] = v >= thr
            else:
                col_ok[i] = v <= thr
        kept &= col_ok
    return kept


def fit_thresholds_on_train(
    train_rows: Sequence[Mapping[str, Optional[float]]],
    train_labeled: Sequence[LabeledTrade],
    spec: CandidateSpec,
    *,
    min_n: int = 40,
) -> dict[str, Any]:
    """Re-estimate thresholds on train only, preserving predicate directions.

    Uses quantile search on train Winner-rate / expectancy (no test leakage).
    """
    # Simple: for each feature, pick quantile on train observed values matching direction
    # among {0.2,0.3,0.4,0.5,0.6,0.7,0.8}, choose combo maximizing train expectancy score
    qs = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    cand_thrs: list[list[float]] = []
    names: list[str] = []
    for p in spec.predicates:
        names.append(p.name)
        vals = [p.value(r) for r in train_rows]
        arr = np.array([v for v in vals if v is not None], dtype=float)
        if len(arr) < min_n:
            return {
                "ok": False,
                "reason": f"insufficient_train_observed:{p.name}",
                "thresholds": {pp.name: pp.threshold for pp in spec.predicates},
            }
        cand_thrs.append(sorted({float(np.quantile(arr, q)) for q in qs}))

    base_wr = float(np.mean([1 if x.is_winner else 0 for x in train_labeled]))
    best = None
    best_score = -1e18
    # Cap search: product of up to 3x3x3 if large — take every other if needed
    from itertools import product

    grids = []
    for opts in cand_thrs:
        if len(opts) > 5:
            opts = opts[::2]
        grids.append(opts)
    for combo in product(*grids):
        thr_map = {n: t for n, t in zip(names, combo)}
        kept = apply_rule(train_rows, spec, thresholds=thr_map)
        m = _metrics(kept, train_labeled)
        if m["n_kept"] < max(25, int(0.05 * len(train_labeled))):
            continue
        score = _expectancy(m, base_wr=base_wr)
        if score > best_score:
            best_score = score
            best = thr_map
    if best is None:
        return {
            "ok": False,
            "reason": "no_viable_train_combo",
            "thresholds": {pp.name: pp.threshold for pp in spec.predicates},
        }
    return {"ok": True, "reason": "ok", "thresholds": best, "train_score": best_score}


def _profit_factor(pnls: np.ndarray) -> Optional[float]:
    gains = float(pnls[pnls > 0].sum()) if (pnls > 0).any() else 0.0
    losses = float(-pnls[pnls < 0].sum()) if (pnls < 0).any() else 0.0
    if losses <= 1e-12:
        return None if gains <= 0 else 99.0
    return round(gains / losses, 4)


def _metrics(kept: np.ndarray, labeled: Sequence[LabeledTrade]) -> dict[str, Any]:
    n = len(labeled)
    k = int(kept.sum())
    if k == 0:
        return {
            "n_total": n,
            "n_kept": 0,
            "keep_rate": 0.0,
            "winner_rate": 0.0,
            "winner_capture": 0.0,
            "stop_rate": 0.0,
            "np_rate": 0.0,
            "mean_pnl": None,
            "total_pnl": 0.0,
            "total_pnl_5bps": 0.0,
            "mean_pnl_5bps": None,
            "pf": None,
            "pf_5bps": None,
        }
    winners = np.array([r.is_winner for r in labeled])
    stops = np.array([r.cohort == "STOP" for r in labeled])
    nps = np.array([r.cohort == "NoProgress" for r in labeled])
    pnls = np.array([r.pnl_yen for r in labeled], dtype=float)
    pnl5 = np.array([r.trade.pnl_5bps for r in labeled], dtype=float)
    n_w = max(int(winners.sum()), 1)
    sub = pnls[kept]
    sub5 = pnl5[kept]
    return {
        "n_total": n,
        "n_kept": k,
        "keep_rate": round(k / n, 4),
        "winner_rate": round(float((kept & winners).sum()) / k, 4),
        "winner_capture": round(float((kept & winners).sum()) / n_w, 4),
        "stop_rate": round(float((kept & stops).sum()) / k, 4),
        "np_rate": round(float((kept & nps).sum()) / k, 4),
        "mean_pnl": round(float(sub.mean()), 2),
        "total_pnl": round(float(sub.sum()), 2),
        "total_pnl_5bps": round(float(sub5.sum()), 2),
        "mean_pnl_5bps": round(float(sub5.mean()), 2),
        "pf": _profit_factor(sub),
        "pf_5bps": _profit_factor(sub5),
    }


def _expectancy(m: Mapping[str, Any], *, base_wr: float) -> float:
    pf = float(m.get("pf") or 0.0)
    pf_term = min(max(pf, 0.0), 5.0) / 5.0
    mean_pnl = float(m.get("mean_pnl") or 0.0)
    mean_term = float(np.tanh(mean_pnl / 1500.0))
    wr = float(m.get("winner_rate") or 0.0)
    wr_term = (wr - base_wr) / max(1.0 - base_wr, 1e-6)
    stop_pen = float(m.get("stop_rate") or 0.0)
    np_pen = float(m.get("np_rate") or 0.0)
    score = 0.40 * pf_term + 0.35 * mean_term + 0.25 * wr_term - 0.25 * stop_pen - 0.15 * np_pen
    if mean_pnl <= 0:
        score -= 0.40
    if pf < 1.0:
        score -= 0.30
    if stop_pen >= 0.20:
        score -= 0.35  # STOP>=20% caution
    return float(score)


def _cap_usage_stats(labeled: Sequence[LabeledTrade], kept: np.ndarray) -> dict[str, Any]:
    caps = []
    for i, lt in enumerate(labeled):
        if not kept[i]:
            continue
        v = lt.trade.features.get("f_cap_usage")
        if v is not None:
            caps.append(float(v))
    if not caps:
        return {"cap_usage_n": 0, "cap_usage_mean": None}
    return {
        "cap_usage_n": len(caps),
        "cap_usage_mean": round(float(np.mean(caps)), 4),
        "cap_usage_p50": round(float(np.median(caps)), 4),
    }


def _daily_stability(labeled: Sequence[LabeledTrade], kept: np.ndarray) -> dict[str, Any]:
    by_day: dict[str, list[float]] = {}
    for i, lt in enumerate(labeled):
        if not kept[i]:
            continue
        by_day.setdefault(lt.trade.day, []).append(float(lt.pnl_yen))
    if not by_day:
        return {
            "n_days_kept": 0,
            "pos_days": 0,
            "neg_days": 0,
            "max_daily_loss": None,
            "max_losing_streak_days": 0,
            "daily_pnl": [],
        }
    daily = []
    for d in sorted(by_day):
        s = sum(by_day[d])
        daily.append({"day": d, "pnl": round(s, 2), "n": len(by_day[d])})
    signs = [1 if x["pnl"] > 0 else (-1 if x["pnl"] < 0 else 0) for x in daily]
    streak = max_streak = 0
    for s in signs:
        if s < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    losses = [x["pnl"] for x in daily if x["pnl"] < 0]
    return {
        "n_days_kept": len(daily),
        "pos_days": sum(1 for x in daily if x["pnl"] > 0),
        "neg_days": sum(1 for x in daily if x["pnl"] < 0),
        "max_daily_loss": round(min(losses), 2) if losses else 0.0,
        "max_losing_streak_days": max_streak,
        "daily_pnl": daily,
    }


def evaluate_universe(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    kept: np.ndarray,
    *,
    universe_n: Optional[int] = None,
) -> dict[str, Any]:
    m = _metrics(kept, labeled)
    m.update(_cap_usage_stats(labeled, kept))
    m.update(_daily_stability(labeled, kept))
    m["universe_n"] = int(universe_n if universe_n is not None else len(labeled))
    m["stop_caution"] = bool((m.get("stop_rate") or 0) >= 0.20)
    base_wr = float(np.mean([1 if r.is_winner else 0 for r in labeled])) if labeled else 0.2
    m["expectancy_score"] = round(_expectancy(m, base_wr=base_wr), 6)
    return m


def pbv2_baseline(labeled: Sequence[LabeledTrade]) -> dict[str, Any]:
    kept = np.ones(len(labeled), dtype=bool)
    return evaluate_universe(labeled, [{} for _ in labeled], kept)


def filter_labeled_days(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    *,
    exclude_suspect_b: bool = False,
) -> tuple[list[LabeledTrade], list[Mapping[str, Optional[float]]]]:
    out_l, out_r = [], []
    for lt, r in zip(labeled, rows):
        if exclude_suspect_b and lt.trade.day in LANE_B_SUSPECT_DAYS:
            continue
        out_l.append(lt)
        out_r.append(r)
    return out_l, out_r


def coverage_for_spec(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    spec: CandidateSpec,
) -> dict[str, Any]:
    elig = eligible_mask(rows, spec, require_all_observed=True)
    days = sorted({labeled[i].trade.day for i in range(len(labeled)) if elig[i]})
    return {
        "cand_id": spec.cand_id,
        "lanes": sorted(spec.lanes),
        "n_eligible": int(elig.sum()),
        "n_total": len(labeled),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "days": days,
        "n_days": len(days),
        "imputation": "NONE_observed_only" if spec.requires_lane_c() else "NONE_required_features_must_exist",
    }


def chronological_walk_forward(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    spec: CandidateSpec,
    *,
    mode: str,  # "fixed" | "reestimated" | "fixed_excl_suspect_b"
    min_train_n: int = 80,
    min_train_eligible: int = 40,
) -> dict[str, Any]:
    fit_mode = "reestimated" if mode == "reestimated" else "fixed"
    days = sorted({lt.trade.day for lt in labeled})
    folds = []
    for d in days:
        train_idx = [i for i, lt in enumerate(labeled) if lt.trade.day < d]
        test_idx = [i for i, lt in enumerate(labeled) if lt.trade.day == d]
        if len(test_idx) < 1 or len(train_idx) < min_train_n:
            folds.append(
                {
                    "train_start": days[0] if days else None,
                    "train_end": None,
                    "test_date": d,
                    "status": "SKIP_INSUFFICIENT_TRAIN",
                    "train_n": len(train_idx),
                    "test_n": len(test_idx),
                }
            )
            continue
        train_days = sorted({labeled[i].trade.day for i in train_idx})
        train_rows = [rows[i] for i in train_idx]
        train_lab = [labeled[i] for i in train_idx]
        test_rows = [rows[i] for i in test_idx]
        test_lab = [labeled[i] for i in test_idx]

        # Lane C / observed-only: train eligible
        train_elig = eligible_mask(train_rows, spec, require_all_observed=True)
        if int(train_elig.sum()) < min_train_eligible:
            folds.append(
                {
                    "train_start": train_days[0],
                    "train_end": train_days[-1],
                    "test_date": d,
                    "status": "SKIP_INSUFFICIENT_ELIGIBLE_TRAIN",
                    "train_n": len(train_idx),
                    "train_eligible_n": int(train_elig.sum()),
                    "test_n": len(test_idx),
                    "thresholds": {p.name: p.threshold for p in spec.predicates},
                }
            )
            continue

        if fit_mode == "fixed":
            thr = {p.name: p.threshold for p in spec.predicates}
            thr_meta = {"ok": True, "reason": "fixed", "thresholds": thr}
        else:
            # fit only on eligible train rows
            tr_rows_e = [train_rows[i] for i in range(len(train_rows)) if train_elig[i]]
            tr_lab_e = [train_lab[i] for i in range(len(train_lab)) if train_elig[i]]
            thr_meta = fit_thresholds_on_train(tr_rows_e, tr_lab_e, spec, min_n=min(30, min_train_eligible))
            thr = thr_meta["thresholds"]

        test_elig = eligible_mask(test_rows, spec, require_all_observed=True)
        kept = apply_rule(test_rows, spec, thresholds=thr) & test_elig
        m = evaluate_universe(test_lab, test_rows, kept, universe_n=int(test_elig.sum()))
        # PBv2 baseline on same eligible test universe
        base_kept = test_elig.copy()
        base = evaluate_universe(test_lab, test_rows, base_kept, universe_n=int(test_elig.sum()))

        folds.append(
            {
                "train_start": train_days[0],
                "train_end": train_days[-1],
                "test_date": d,
                "status": "EVAL",
                "mode": mode,
                "thresholds": thr,
                "threshold_fit": thr_meta.get("reason"),
                "train_n": len(train_idx),
                "train_eligible_n": int(train_elig.sum()),
                "test_n": len(test_idx),
                "test_eligible_n": int(test_elig.sum()),
                "kept_n": m["n_kept"],
                "PnL": m["total_pnl"],
                "PnL_5bps": m["total_pnl_5bps"],
                "PF": m["pf"],
                "PF_5bps": m["pf_5bps"],
                "Winner率": m["winner_rate"],
                "Winner捕捉率": m["winner_capture"],
                "STOP率": m["stop_rate"],
                "NoProgress率": m["np_rate"],
                "keep率": m["keep_rate"],
                "base_PnL": base["total_pnl"],
                "base_PnL_5bps": base["total_pnl_5bps"],
                "delta_PnL": round(float(m["total_pnl"]) - float(base["total_pnl"]), 2),
                "delta_PnL_5bps": round(float(m["total_pnl_5bps"]) - float(base["total_pnl_5bps"]), 2),
                "stop_caution": m["stop_caution"],
            }
        )

    eval_folds = [f for f in folds if f.get("status") == "EVAL"]
    if eval_folds:
        total_pnl = sum(float(f["PnL"] or 0) for f in eval_folds)
        base_pnl = sum(float(f["base_PnL"] or 0) for f in eval_folds)
        total_5 = sum(float(f["PnL_5bps"] or 0) for f in eval_folds)
        base_5 = sum(float(f["base_PnL_5bps"] or 0) for f in eval_folds)
        pos = sum(1 for f in eval_folds if float(f.get("delta_PnL") or 0) > 0)
        neg = sum(1 for f in eval_folds if float(f.get("delta_PnL") or 0) < 0)
        stop_hi = sum(1 for f in eval_folds if f.get("stop_caution"))
        # Aggregate kept trades for PF
        # approximate from fold means — better recompute if needed
        summary = {
            "n_eval_folds": len(eval_folds),
            "n_skip_folds": sum(1 for f in folds if f.get("status") != "EVAL"),
            "total_PnL": round(total_pnl, 2),
            "base_total_PnL": round(base_pnl, 2),
            "delta_PnL": round(total_pnl - base_pnl, 2),
            "total_PnL_5bps": round(total_5, 2),
            "base_total_PnL_5bps": round(base_5, 2),
            "delta_PnL_5bps": round(total_5 - base_5, 2),
            "pos_days": pos,
            "neg_days": neg,
            "stop_caution_folds": stop_hi,
            "median_winner_rate": float(np.median([f["Winner率"] for f in eval_folds])),
            "median_stop_rate": float(np.median([f["STOP率"] for f in eval_folds])),
            "median_np_rate": float(np.median([f["NoProgress率"] for f in eval_folds])),
            "median_PF": float(np.median([f["PF"] for f in eval_folds if f.get("PF") is not None] or [0])),
        }
    else:
        summary = {"n_eval_folds": 0, "n_skip_folds": len(folds), "delta_PnL": None}

    return {
        "cand_id": spec.cand_id,
        "mode": mode,
        "lanes": sorted(spec.lanes),
        "folds": folds,
        "summary": summary,
        "narrative": {
            "market_state": spec.market_state,
            "rise_hypothesis": spec.rise_hypothesis,
            "failure_exit": spec.failure_exit,
            "missing_feature_risk": spec.missing_feature_risk,
        },
    }


def in_sample_eval(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    spec: CandidateSpec,
    *,
    mode: str = "fixed",
) -> dict[str, Any]:
    elig = eligible_mask(rows, spec, require_all_observed=True)
    if mode == "reestimated":
        # fit on all eligible (in-sample; for reference only)
        e_rows = [rows[i] for i in range(len(rows)) if elig[i]]
        e_lab = [labeled[i] for i in range(len(labeled)) if elig[i]]
        fit = fit_thresholds_on_train(e_rows, e_lab, spec)
        thr = fit["thresholds"]
    else:
        thr = {p.name: p.threshold for p in spec.predicates}
        fit = {"ok": True, "reason": "fixed"}
    kept = apply_rule(rows, spec, thresholds=thr) & elig
    # Restrict metrics population to eligible universe for fair keep_rate
    # Evaluate on full labeled but keep only eligible&rule; also report eligible baseline
    m = evaluate_universe(labeled, rows, kept, universe_n=int(elig.sum()))
    base = evaluate_universe(labeled, rows, elig, universe_n=int(elig.sum()))
    cov = coverage_for_spec(labeled, rows, spec)
    return {
        "cand_id": spec.cand_id,
        "mode": mode,
        "thresholds": thr,
        "fit": fit,
        "coverage": cov,
        "metrics": m,
        "baseline_eligible": {
            "total_pnl": base["total_pnl"],
            "total_pnl_5bps": base["total_pnl_5bps"],
            "pf": base["pf"],
            "mean_pnl": base["mean_pnl"],
            "n_kept": base["n_kept"],
        },
        "delta_pnl_vs_eligible_pbv2": round(float(m["total_pnl"]) - float(base["total_pnl"]), 2),
        "delta_pnl_5bps_vs_eligible_pbv2": round(float(m["total_pnl_5bps"]) - float(base["total_pnl_5bps"]), 2),
        "narrative": {
            "market_state": spec.market_state,
            "rise_hypothesis": spec.rise_hypothesis,
            "failure_exit": spec.failure_exit,
            "missing_feature_risk": spec.missing_feature_risk,
        },
        "stop_caution": m["stop_caution"],
    }


def decide_verdict(wf_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate OOS results across candidates → final verdict.

    FORWARD_READY requires a candidate that is strong on **both** fixed and reestimated
    chronological OOS, with enough folds and controlled STOP caution.
    """
    by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for r in wf_results:
        # key: (cand_id, mode) — prefer non-excl for pairing; also track excl separately
        mode = str(r.get("mode") or "")
        cid = str(r.get("cand_id") or "")
        # wf_fixed_excl_suspect_b has mode still "fixed" — disambiguate via summary presence in pipeline
        by_key[(cid, mode)] = r

    # Group all results by cand_id (all modes)
    by_cand: dict[str, list[Mapping[str, Any]]] = {}
    for r in wf_results:
        by_cand.setdefault(str(r.get("cand_id")), []).append(r)

    improved = []
    caution = []
    worsened = []
    forward_ready_hits = []

    for cid, runs in by_cand.items():
        for r in runs:
            s = r.get("summary") or {}
            n_eval = int(s.get("n_eval_folds") or 0)
            if n_eval < 3 or s.get("delta_PnL") is None:
                caution.append(
                    {
                        "cand_id": cid,
                        "mode": r.get("mode"),
                        "note": "insufficient_oos_folds",
                        "n_eval_folds": n_eval,
                    }
                )
                continue
            delta = float(s.get("delta_PnL") or 0)
            delta5 = float(s.get("delta_PnL_5bps") or 0)
            pos, neg = int(s.get("pos_days") or 0), int(s.get("neg_days") or 0)
            med_pf = float(s.get("median_PF") or 0)
            med_stop = float(s.get("median_stop_rate") or 0)
            stop_folds = int(s.get("stop_caution_folds") or 0)
            stop_frac = stop_folds / max(n_eval, 1)
            row = {
                "cand_id": cid,
                "mode": r.get("mode"),
                "n_eval_folds": n_eval,
                "delta_PnL": delta,
                "delta_PnL_5bps": delta5,
                "pos_days": pos,
                "neg_days": neg,
                "median_PF": med_pf,
                "median_stop_rate": med_stop,
                "stop_caution_folds": stop_folds,
                "stop_caution_frac": round(stop_frac, 4),
            }
            strong = (
                n_eval >= 5
                and delta > 0
                and delta5 > 0
                and med_pf >= 1.0
                and pos >= neg + 2
                and med_stop < 0.20
                and stop_frac <= 0.35
            )
            if strong:
                improved.append(row)
            elif delta < 0 and delta5 < 0:
                worsened.append(row)
            else:
                caution.append(row)

        # Pair fixed + reestimated for same cand (modes exactly "fixed" and "reestimated")
        fixed = next((r for r in runs if r.get("mode") == "fixed" and int((r.get("summary") or {}).get("n_eval_folds") or 0) >= 5), None)
        reest = next((r for r in runs if r.get("mode") == "reestimated" and int((r.get("summary") or {}).get("n_eval_folds") or 0) >= 5), None)
        if fixed and reest:
            sf, sr = fixed["summary"], reest["summary"]
            ok_fixed = (
                float(sf.get("delta_PnL") or 0) > 0
                and float(sf.get("delta_PnL_5bps") or 0) > 0
                and float(sf.get("median_PF") or 0) >= 1.0
                and int(sf.get("pos_days") or 0) >= int(sf.get("neg_days") or 0) + 2
                and float(sf.get("median_stop_rate") or 0) < 0.20
                and int(sf.get("stop_caution_folds") or 0) / max(int(sf.get("n_eval_folds") or 1), 1) <= 0.35
            )
            ok_re = (
                float(sr.get("delta_PnL") or 0) > 0
                and float(sr.get("delta_PnL_5bps") or 0) > 0
                and float(sr.get("median_PF") or 0) >= 1.0
                and int(sr.get("pos_days") or 0) >= int(sr.get("neg_days") or 0)
                and float(sr.get("median_stop_rate") or 0) < 0.20
            )
            if ok_fixed and ok_re:
                forward_ready_hits.append({"cand_id": cid, "fixed": sf, "reestimated": sr})

    if forward_ready_hits:
        return {
            "verdict": "WINNER_FILTER_FORWARD_READY",
            "reason": (
                "Candidate(s) improve chronological OOS on BOTH fixed and reestimated thresholds "
                "with >=5 folds, PF>=1, STOP controlled, pos_days dominance"
            ),
            "forward_ready_hits": forward_ready_hits,
            "improved": improved,
            "caution": caution,
            "worsened": worsened,
            "not_production": True,
        }
    if worsened and not improved and not any(
        isinstance(c, dict) and c.get("delta_PnL", 0) > 0 for c in caution if "delta_PnL" in c
    ):
        return {
            "verdict": "WINNER_FILTER_REJECTED",
            "reason": "Evaluable chronological OOS candidates worsen vs PBv2 eligible baseline",
            "worsened": worsened,
            "caution": caution,
            "not_production": True,
        }
    return {
        "verdict": "WINNER_FILTER_OFFLINE_ONLY",
        "reason": (
            "In-sample / fixed-threshold OOS shows pockets of improvement, but reestimated OOS, "
            "STOP caution, or fold coverage is insufficient for FORWARD_READY. "
            "Not a mainline implementation candidate."
        ),
        "improved": improved,
        "caution": caution,
        "worsened": worsened,
        "forward_ready_hits": forward_ready_hits,
        "not_production": True,
    }
