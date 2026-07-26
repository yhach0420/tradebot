"""Zero-base candidate generators (4 series) + comparison baselines."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from research.pbv2_zero_base_revalidation.constants import (
    LANE_A_FEATURES,
    LANE_B_FEATURES,
    LANE_C_REQUIRED,
    MIN_DYNAMIC_OOS_DAYS,
    SUSPECT_BOARD_DAYS,
)
from research.pbv2_zero_base_revalidation.metrics import metrics_for as metrics_for  # noqa: F401
from research.pbv2_zero_base_revalidation.panel import CandidateRow

KeepFn = Callable[[CandidateRow], bool]


@dataclass
class RuleSpec:
    rule_id: str
    series: str  # pbv2 | dense | static | dynamic | combined | baseline
    description: str
    feature_keys: tuple[str, ...]
    ops: tuple[str, ...]  # >= or <=
    # thresholds filled at train fit
    thresholds: tuple[float, ...] = ()
    score_mode: str = "and"  # and | linear

    def ready(self, row: CandidateRow) -> bool:
        return all(row.features.get(k) is not None for k in self.feature_keys)

    def score(self, row: CandidateRow) -> Optional[float]:
        if not self.ready(row) or not self.thresholds:
            return None
        vals = []
        for k, op, thr in zip(self.feature_keys, self.ops, self.thresholds):
            v = float(row.features[k])  # type: ignore[index]
            if op == ">=":
                vals.append(1.0 if v >= thr else 0.0)
            else:
                vals.append(1.0 if v <= thr else 0.0)
        if self.score_mode == "and":
            return float(all(x >= 1.0 for x in vals))
        return float(sum(vals))

    def keep(self, row: CandidateRow) -> bool:
        s = self.score(row)
        if s is None:
            return False
        if self.score_mode == "and":
            return s >= 1.0
        return s >= max(1.0, len(self.feature_keys) - 0.5)


def fit_rule_thresholds(train: Sequence[CandidateRow], spec: RuleSpec) -> RuleSpec:
    qs = [0.25, 0.4, 0.5, 0.6, 0.75]
    train_fit = list(train)
    if len(train_fit) > 2500:
        step = max(1, len(train_fit) // 2500)
        train_fit = train_fit[::step][:2500]
    grids: list[list[float]] = []
    for k, op in zip(spec.feature_keys, spec.ops):
        vals = np.array(
            [float(r.features[k]) for r in train_fit if r.features.get(k) is not None],
            dtype=float,
        )
        if len(vals) < 30:
            return RuleSpec(
                rule_id=spec.rule_id,
                series=spec.series,
                description=spec.description,
                feature_keys=spec.feature_keys,
                ops=spec.ops,
                thresholds=tuple(float(np.median(vals)) for _ in spec.feature_keys) if len(vals) else (),
                score_mode=spec.score_mode,
            )
        grids.append(sorted({float(np.quantile(vals, q)) for q in qs}))

    best_thr: Optional[tuple[float, ...]] = None
    best_score = -1e18
    trimmed = [g[:4] if len(g) > 4 else g for g in grids]

    for combo in product(*trimmed):
        trial = RuleSpec(
            rule_id=spec.rule_id,
            series=spec.series,
            description=spec.description,
            feature_keys=spec.feature_keys,
            ops=spec.ops,
            thresholds=tuple(combo),
            score_mode=spec.score_mode,
        )
        m = metrics_for(train_fit, trial.keep)
        if m["n"] < max(15, int(0.02 * len(train_fit))):
            continue
        score = float(m["pnl_5bps"]) + 10.0 * float(m["pf"] or 0.0) - 50.0 * float(m["stop_rate"] or 0.0)
        if score > best_score:
            best_score = score
            best_thr = tuple(combo)
    if best_thr is None:
        meds = []
        for k in spec.feature_keys:
            vals = [float(r.features[k]) for r in train_fit if r.features.get(k) is not None]
            meds.append(float(np.median(vals)) if vals else 0.0)
        best_thr = tuple(meds)
    return RuleSpec(
        rule_id=spec.rule_id,
        series=spec.series,
        description=spec.description,
        feature_keys=spec.feature_keys,
        ops=spec.ops,
        thresholds=best_thr,
        score_mode=spec.score_mode,
    )


def pbv2_baseline_keep(row: CandidateRow) -> bool:
    return bool(row.pbv2_decision or row.accept)


def winner_filter_specs() -> list[RuleSpec]:
    """Reproduce WinnerFilter A–E shapes; thresholds re-fit on train."""
    return [
        RuleSpec("WinnerFilter_A", "baseline", "TV high + chase low + rise5 pullback", ("f_tv", "f_chase", "f_rise5"), (">=", "<=", "<=")),
        RuleSpec("WinnerFilter_B", "baseline", "imb high + mom not overheated", ("f_imb", "f_mom"), (">=", "<=")),
        RuleSpec("WinnerFilter_C", "baseline", "vwap below + mom", ("f_vwap", "f_mom"), ("<=", ">=")),
        RuleSpec("WinnerFilter_D", "baseline", "imb + vwap + atr", ("f_imb", "f_vwap", "f_atr"), (">=", "<=", "<=")),
        RuleSpec("WinnerFilter_E", "baseline", "imb + spread + tv", ("f_imb", "f_spread", "f_tv"), (">=", "<=", ">=")),
    ]


def dense_rule_candidates() -> list[RuleSpec]:
    return [
        RuleSpec("dense_1_rise5", "dense", "single rise5 pullback", ("f_rise5",), ("<=",)),
        RuleSpec("dense_1_mom", "dense", "single mom", ("f_mom",), (">=",)),
        RuleSpec("dense_1_near", "dense", "single near_high", ("f_near_high",), ("<=",)),
        RuleSpec("dense_1_vwap", "dense", "single vwap", ("f_vwap",), ("<=",)),
        RuleSpec("dense_2_rise_mom", "dense", "rise5+mom", ("f_rise5", "f_mom"), ("<=", ">=")),
        RuleSpec("dense_2_vwap_mom", "dense", "vwap+mom", ("f_vwap", "f_mom"), ("<=", ">=")),
        RuleSpec("dense_2_near_mom", "dense", "near+mom", ("f_near_high", "f_mom"), ("<=", ">=")),
        RuleSpec(
            "dense_3_pullback_mom_vwap",
            "dense",
            "rise5 pullback + mom + vwap",
            ("f_rise5", "f_mom", "f_vwap"),
            ("<=", ">=", "<="),
        ),
        RuleSpec(
            "dense_3_near_mom_bounce",
            "dense",
            "near_high + mom + bounce",
            ("f_near_high", "f_mom", "f_bounce"),
            ("<=", ">=", ">="),
        ),
    ]


def static_rule_candidates() -> list[RuleSpec]:
    return [
        RuleSpec("top_only_imb_mom", "static", "TOP_ONLY imb + mom", ("f_imb", "f_mom"), (">=", ">=")),
        RuleSpec("top_only_imb_vwap_rise", "static", "TOP_ONLY imb + vwap + rise5", ("f_imb", "f_vwap", "f_rise5"), (">=", "<=", "<=")),
        RuleSpec("top_only_imb_near_tv", "static", "TOP_ONLY imb + near_high + tv", ("f_imb", "f_near_high", "f_tv"), (">=", "<=", ">=")),
        RuleSpec("top_only_imb_only", "static", "TOP_ONLY imb alone (control)", ("f_imb",), (">=",)),
    ]


def dynamic_rule_candidates() -> list[RuleSpec]:
    return [
        RuleSpec(
            "dyn_imbchg_ret",
            "dynamic",
            "imb_chg_60 + ret_60",
            ("f_np_imb_chg_60", "f_np_ret_60"),
            (">=", ">="),
        ),
        RuleSpec(
            "dyn_board_improve",
            "dynamic",
            "imb_chg + bid_chg + ask not rising",
            ("f_np_imb_chg_60", "f_np_bid_chg_60", "f_np_ask_chg_60"),
            (">=", ">=", "<="),
        ),
        RuleSpec(
            "dyn_vol_sync",
            "dynamic",
            "tv_chg + vol_price_sync + imb_persist",
            ("f_np_tv_chg_pct_60", "f_np_vol_price_sync_60", "f_np_imb_persist_60"),
            (">=", ">=", ">="),
        ),
        RuleSpec(
            "dyn_reject_board_worsen",
            "dynamic",
            "reject when imb_chg weak (used as keep when NOT weak)",
            ("f_np_imb_chg_60",),
            (">=",),
        ),
    ]


def combined_rule_candidates() -> list[RuleSpec]:
    return [
        RuleSpec(
            "comb_dense_then_dyn",
            "combined",
            "mom+rise5 then imb_chg",
            ("f_mom", "f_rise5", "f_np_imb_chg_60"),
            (">=", "<=", ">="),
        ),
        RuleSpec(
            "comb_static_dyn",
            "combined",
            "imb + imb_chg",
            ("f_imb", "f_np_imb_chg_60"),
            (">=", ">="),
        ),
    ]


def h_board_ts_keep_factory(thr: Optional[float]) -> KeepFn:
    def keep(row: CandidateRow) -> bool:
        # baseline filter on top of PBv2 decision universe
        if not pbv2_baseline_keep(row):
            return False
        v = row.features.get("f_np_imb_chg_60")
        if v is None:
            return True  # fail-open as historical H
        if thr is None:
            return True
        return float(v) > thr

    return keep


def i_price_board_keep_factory(thr_imb: Optional[float], thr_chase: Optional[float], thr_near: Optional[float]) -> KeepFn:
    def keep(row: CandidateRow) -> bool:
        if not pbv2_baseline_keep(row):
            return False
        chase = row.features.get("f_chase")
        near = row.features.get("f_near_high")
        if thr_chase is not None and chase is not None and float(chase) >= thr_chase:
            return False
        if thr_near is not None and near is not None and float(near) >= thr_near:
            return False
        v = row.features.get("f_np_imb_chg_60")
        if v is None:
            return True
        if thr_imb is None:
            return True
        return float(v) > thr_imb

    return keep


def fit_quantile(train: Sequence[CandidateRow], key: str, q: float, side: str = "low") -> Optional[float]:
    vals = np.array([float(r.features[key]) for r in train if r.features.get(key) is not None], dtype=float)
    if len(vals) < 20:
        return None
    qq = q if side == "low" else q
    return float(np.quantile(vals, qq))


def board_universe(rows: Sequence[CandidateRow], mode: str) -> list[CandidateRow]:
    if mode == "INCLUDE_ALL":
        return list(rows)
    if mode == "FULL_L2_ONLY":
        return [r for r in rows if r.board_quality == "FULL_L2"]
    if mode == "EXCLUDE_SUSPECT_DAYS":
        return [r for r in rows if r.day not in SUSPECT_BOARD_DAYS]
    if mode == "RECENT_CLEAN_BOARD_ONLY":
        return [r for r in rows if r.day not in SUSPECT_BOARD_DAYS and r.board_quality in ("FULL_L2", "TOP_ONLY")]
    return list(rows)


def dynamic_status(days_with_complete: Sequence[str], test_day: str) -> str:
    prior = [d for d in days_with_complete if d < test_day]
    if not days_with_complete:
        return "FEATURE_MISSING"
    first = min(days_with_complete)
    if test_day == first:
        return "WARMUP"
    if test_day < first:
        return "FEATURE_MISSING"
    if len(prior) < 1:
        return "INSUFFICIENT_DYNAMIC_TRAIN"
    if test_day in days_with_complete:
        return "OOS_EVALUABLE"
    return "COVERAGE_ONLY"


def dynamic_gate(oos_days: int, m: Mapping[str, Any], m_base: Mapping[str, Any]) -> str:
    if oos_days < MIN_DYNAMIC_OOS_DAYS:
        return "DYNAMIC_BOARD_INSUFFICIENT_OOS"
    if float(m.get("pnl_5bps") or 0) <= float(m_base.get("pnl_5bps") or 0):
        return "DYNAMIC_BOARD_NO_EDGE"
    if (m.get("pf") or 0) <= (m_base.get("pf") or 0):
        return "DYNAMIC_BOARD_NO_EDGE"
    if (m.get("pos_days") or 0) <= (m.get("neg_days") or 0):
        return "DYNAMIC_BOARD_NO_EDGE"
    return "DYNAMIC_BOARD_EDGE_CONFIRMED"
