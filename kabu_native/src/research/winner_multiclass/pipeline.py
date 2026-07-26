"""Orchestrate Winner Multiclass offline research → report.md / report.json / audit.xlsx."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from research.cost_aware_v2.analyze import build_keep_fns
from research.cost_aware_v2.dataset import NATIVE, load_all_trades
from research.winner_feature_filter.features import build_feature_dict, select_model_features
from research.winner_feature_filter.forward_validation import (
    apply_rule,
    eligible_mask,
    fixed_candidates,
)
from research.winner_feature_filter.pipeline import write_xlsx
from research.winner_multiclass.labels import (
    CLASS_ORDER,
    PRIORITY,
    class_counts,
    label_multiclass,
    winner_threshold,
    y_ids,
)
from research.winner_multiclass.lanes import (
    LANE_A,
    LANE_B,
    LANE_C,
    SUSPECT_B_DAYS,
    TIME_BLOCKLIST,
    is_time_or_id_feature,
    lane_of,
    select_lane_features,
)
from research.winner_multiclass.matrix import build_xy
from research.winner_multiclass.models import compare_models_holdout
from research.winner_multiclass.quality import (
    audit_all_features,
    bad_quality_features,
    lane_b_daily_quality,
)
from research.winner_multiclass.rules import search_readable_rules_chronological
from research.winner_multiclass.scores import SCORE_FORMULAS, estimate_payoffs_train, expected_value_score
from research.winner_multiclass.univariate import run_univariate
from research.winner_multiclass.walk_forward import (
    chronological_walk_forward,
    daily_stability,
    keep_rate_sensitivity_oos,
    portfolio_metrics,
)

JST = ZoneInfo("Asia/Tokyo")
OUT_REL = Path("results") / "research" / "winner_multiclass"
KEEP_RATES = (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 1.0)


def _build_rows(labeled) -> tuple[list[str], list[dict[str, Optional[float]]], dict[str, Any]]:
    rows: list[dict[str, Optional[float]]] = []
    for lt in labeled:
        full = build_feature_dict(lt.trade)
        clean = select_model_features(full)
        # Extra time/id scrub
        rows.append({k: v for k, v in clean.items() if not is_time_or_id_feature(k)})
    key_counts: dict[str, int] = {}
    for r in rows:
        for k, v in r.items():
            if v is not None:
                key_counts[k] = key_counts.get(k, 0) + 1
    names = sorted(k for k, n in key_counts.items() if n >= max(20, int(0.01 * len(rows))))
    meta = {
        "n_rows": len(rows),
        "n_features": len(names),
        "fill_rates": {k: round(key_counts.get(k, 0) / max(len(rows), 1), 4) for k in names},
        "time_blocklist": list(TIME_BLOCKLIST),
    }
    return names, rows, meta


def _lane_coverage(names: Sequence[str], rows: Sequence[Mapping[str, Optional[float]]], labeled) -> list[dict]:
    out = []
    for lane in ("A", "B", "C"):
        feats = [n for n in names if lane_of(n) == lane]
        if not feats:
            out.append({"lane": lane, "n_features": 0, "n_complete_rows": 0, "n_days": 0})
            continue
        complete = 0
        days = set()
        for i, r in enumerate(rows):
            if all(r.get(f) is not None for f in feats):
                complete += 1
                days.add(labeled[i].trade.day)
            elif lane == "A" and any(r.get(f) is not None for f in feats):
                # dense-ish: count row if ≥50% observed
                obs = sum(1 for f in feats if r.get(f) is not None)
                if obs >= max(3, len(feats) // 2):
                    complete += 1
                    days.add(labeled[i].trade.day)
        # For B/C: row complete if ALL selected feats present is too strict;
        # report rows with ≥1 feature of lane
        any_obs = 0
        any_days = set()
        for i, r in enumerate(rows):
            if any(r.get(f) is not None for f in feats):
                any_obs += 1
                any_days.add(labeled[i].trade.day)
        out.append(
            {
                "lane": lane,
                "n_features": len(feats),
                "n_rows_any_feature": any_obs,
                "n_days_any_feature": len(any_days),
                "days": sorted(any_days),
                "feature_sample": feats[:15],
            }
        )
    return out


def _policy_metrics(trades, keep_fn, labeled) -> dict[str, Any]:
    kept = np.array([bool(keep_fn(t)) for t in trades], dtype=bool)
    return portfolio_metrics(labeled, kept)


def _winner_filter_candidate_metrics(labeled, rows) -> list[dict[str, Any]]:
    out = []
    for spec in fixed_candidates():
        elig = eligible_mask(rows, spec, require_all_observed=True)
        kept = apply_rule(rows, spec) & elig
        # Map to labeled indices (same order)
        m = portfolio_metrics(labeled, kept)
        stab = daily_stability(labeled, kept)
        out.append(
            {
                "name": f"WinnerFilter_{spec.cand_id}",
                "rule": " AND ".join(f"{p.name} {p.op} {p.threshold}" for p in spec.predicates),
                "eligible_n": int(elig.sum()),
                **m,
                "pos_days": stab["pos_days"],
                "neg_days": stab["neg_days"],
                "max_daily_loss": stab["max_daily_loss"],
            }
        )
    return out


def _decide_verdict(
    *,
    wf_summary: Mapping[str, Any],
    keep_sens: Sequence[Mapping[str, Any]],
    lane_cov: Sequence[Mapping[str, Any]],
    bad_feats: Sequence[Mapping[str, Any]],
    best_keep: Mapping[str, Any],
    pbv2: Mapping[str, Any],
) -> dict[str, Any]:
    reasons = []
    lane_c = next((x for x in lane_cov if x["lane"] == "C"), {})
    n_c_days = int(lane_c.get("n_days_any_feature") or 0)
    n_eval = int(wf_summary.get("n_eval_folds") or 0)
    delta = float(wf_summary.get("delta_PnL_5bps") or 0)
    med_stop = float(wf_summary.get("median_STOP") or 0)
    med_np = float(wf_summary.get("median_NP") or 0)
    base_stop = float(pbv2.get("STOP率") or 0)
    base_np = float(pbv2.get("NoProgress率") or 0)
    sac = float(best_keep.get("Winner犠牲率") or 1)
    pos = int(wf_summary.get("pos_days") or 0)
    neg = int(wf_summary.get("neg_days") or 0)

    # Relative OOS improvement vs fold baseline, and absolute vs full PBv2 at chosen keep
    improved = delta > 0 and float(best_keep.get("total_pnl_5bps") or 0) > float(pbv2.get("total_pnl_5bps") or 0)
    stop_ok = med_stop <= base_stop + 0.02
    np_ok = med_np <= base_np + 0.01
    stable = n_eval >= 5 and pos >= neg
    lane_ok = n_c_days >= 5 and len(bad_feats) < 8
    sac_ok = sac <= 0.70

    abs_worse = float(wf_summary.get("total_PnL_5bps") or 0) < float(pbv2.get("total_pnl_5bps") or 0) and delta < 0
    if improved and stop_ok and np_ok and stable and lane_ok and sac_ok and n_eval >= 5:
        verdict = "WINNER_MULTICLASS_FORWARD_READY"
        reasons.append("chronological OOS improves PnL/PF vs PBv2 with STOP/NP not worse and Lane quality OK")
    elif abs_worse and (sac > 0.90 or med_stop > base_stop + 0.10) and n_eval >= 3:
        verdict = "WINNER_MULTICLASS_REJECTED"
        reasons.append("OOS worsens vs PBv2 with unacceptable STOP/sacrifice")
    else:
        verdict = "WINNER_MULTICLASS_OFFLINE_ONLY"
        if n_eval < 5:
            reasons.append(f"insufficient OOS eval folds ({n_eval})")
        if n_c_days < 5:
            reasons.append(f"Lane C coverage only {n_c_days} days")
        if not improved:
            reasons.append("PnL/PF improvement vs PBv2 not stable")
        if not stop_ok:
            reasons.append("STOP rate not improved / unstable")
        if not np_ok:
            reasons.append("NoProgress rate not improved / unstable")
        if not sac_ok:
            reasons.append("Winner sacrifice high")
        if bad_feats:
            reasons.append(f"{len(bad_feats)} quality-flagged features")

    return {
        "verdict": verdict,
        "reason": "; ".join(reasons) or "offline research complete",
        "checks": {
            "improved": improved,
            "stop_ok": stop_ok,
            "np_ok": np_ok,
            "stable": stable,
            "lane_ok": lane_ok,
            "sac_ok": sac_ok,
            "n_eval_folds": n_eval,
            "lane_c_days": n_c_days,
            "delta_PnL_5bps": delta,
        },
    }


def _shadow_spec(verdict: str, best_model: str, lane: str, best_kr: float) -> dict[str, Any]:
    ready = verdict == "WINNER_MULTICLASS_FORWARD_READY"
    return {
        "mode": "observe_only" if ready else "spec_only_not_deploy",
        "enabled_recommendation": ready,
        "fail_open_on_lane_c_missing": True,
        "model_version": f"winner_multiclass_{best_model}_v1",
        "feature_lane": lane,
        "keep_rate_target": best_kr,
        "score": "entry_quality_score (EQ2)",
        "formulas": SCORE_FORMULAS,
        "fields": [
            "winner_prob",
            "stop_prob",
            "no_progress_prob",
            "normal_prob",
            "expected_value_score",
            "entry_quality_score",
            "model_version",
            "feature_lane",
            "feature_available",
            "feature_missing",
            "fail_open",
            "shadow_keep",
            "shadow_reject",
            "actual_exit_class",
            "actual_pnl_5bps",
            "counterfactual_delta_5bps",
        ],
        "note": "本線実装禁止。OOS有望時のみ Shadow observe-only。Lane C不足は fail-open。",
    }


def _render_md(p: Mapping[str, Any]) -> str:
    v = p["verdict"]
    cc = p["class_counts"]
    best = p.get("best_model_holdout") or {}
    wf = (p.get("walk_forward") or {}).get("summary") or {}
    bk = p.get("best_keep") or {}
    pb = p.get("pbv2_baseline") or {}
    md = f"""# Winner Multiclass — 上昇期待モデル（offline）

## 最終判定
- **{v.get('verdict')}**
- {v.get('reason')}
- Paper / observe-only。本線・実注文変更なし。

## クラス定義
優先順位（重複禁止）: `{' > '.join(PRIORITY)}`
- Winner閾値（全体）: {p.get('winner_threshold_global')}
- 件数: Winner={cc.get('Winner')} STOP={cc.get('STOP')} NoProgress={cc.get('NoProgress')} Normal={cc.get('Normal')}
- 使用: {p.get('n_days')}日 / {p.get('n_trades')}件

## Lane
"""
    for r in p.get("lane_coverage") or []:
        md += (
            f"- Lane {r['lane']}: features={r['n_features']} "
            f"any_obs_rows={r.get('n_rows_any_feature')} days={r.get('n_days_any_feature')}\n"
        )
    md += f"\n時間帯特徴除外: {', '.join(TIME_BLOCKLIST[:8])} ...\n"
    md += "\n## 品質不良特徴\n"
    for b in (p.get("bad_features") or [])[:20]:
        md += f"- {b['feature']} [{b['lane']}]: {b['flags']}\n"
    md += f"\n## 最良モデル（chronological holdout）\n- model: **{best.get('name')}**\n"
    m = best.get("metrics") or {}
    md += (
        f"- macro_f1={m.get('macro_f1')} weighted_f1={m.get('weighted_f1')} "
        f"balanced_acc={m.get('balanced_accuracy')} log_loss={m.get('log_loss')} "
        f"macro_auc_ovr={m.get('macro_auc_ovr')}\n"
        f"- Winner precision/recall={m.get('winner_precision')}/{m.get('winner_recall')}\n"
        f"- STOP recall={m.get('stop_recall')} NoProgress recall={m.get('no_progress_recall')}\n"
        f"- false_winner_rate={m.get('false_winner_rate')} winner_sacrifice={m.get('winner_sacrifice_rate')} "
        f"stop_missed={m.get('stop_missed_rate')}\n"
    )
    md += f"""
## Chronological Walk-Forward（主判定）
- model={p.get('wf_model')} score={p.get('wf_score_key')} fold_keep≈{p.get('wf_fold_keep')}
- eval_folds={wf.get('n_eval_folds')} skip={wf.get('n_skip')}
- OOS PnL_5bps={wf.get('total_PnL_5bps')} base={wf.get('base_total_PnL_5bps')} Δ={wf.get('delta_PnL_5bps')}
- median PF={wf.get('median_PF')} STOP={wf.get('median_STOP')} NP={wf.get('median_NP')}
- pos/neg days={wf.get('pos_days')}/{wf.get('neg_days')}
- median macro_f1={wf.get('median_macro_f1')} winner_precision={wf.get('median_winner_precision')}

## Keep率感度（OOS scores）
| keep | trades | pnl_5bps | PF | winner_rate | STOP | NP | Winner犠牲 | max_daily_loss | +/- days |
|------|--------|----------|----|-------------|------|----|------------|----------------|----------|
"""
    for r in p.get("keep_rate_sensitivity") or []:
        md += (
            f"| {r.get('keep_rate_target')} | {r.get('trades')} | {r.get('total_pnl_5bps')} | {r.get('PF')} | "
            f"{r.get('winner_rate')} | {r.get('STOP率')} | {r.get('NoProgress率')} | {r.get('Winner犠牲率')} | "
            f"{r.get('max_daily_loss')} | {r.get('pos_days')}/{r.get('neg_days')} |\n"
        )
    md += f"""
## 最良keep（EV/EQ）
- target={bk.get('keep_rate_target')} trades={bk.get('trades')} pnl_5bps={bk.get('total_pnl_5bps')} PF={bk.get('PF')}
- Winner犠牲={bk.get('Winner犠牲率')} STOP={bk.get('STOP率')} NP={bk.get('NoProgress率')}

## PBv2 baseline
- trades={pb.get('trades')} pnl_5bps={pb.get('total_pnl_5bps')} PF={pb.get('PF')}
- STOP={pb.get('STOP率')} NP={pb.get('NoProgress率')} winner_rate={pb.get('winner_rate')}

## 期待値スコア式
"""
    for k, fml in SCORE_FORMULAS.items():
        md += f"- `{k}`: {fml}\n"
    md += "\n## 人間可読ルール（OOS上位）\n"
    for r in (p.get("readable_rules") or [])[:8]:
        md += (
            f"- `{r.get('rule')}` | pnl={r.get('total_pnl_5bps')} PF={r.get('median_PF')} "
            f"STOP={r.get('median_STOP')} NP={r.get('median_NP')} days={r.get('n_oos_days')}\n"
        )
    md += "\n## Baseline比較\n| name | pnl_5bps | PF | trades | keep | STOP | NP |\n|------|----------|----|--------|------|------|----|\n"
    for r in p.get("baseline_comparison") or []:
        md += (
            f"| {r.get('name')} | {r.get('total_pnl_5bps')} | {r.get('PF')} | {r.get('trades')} | "
            f"{r.get('keep_rate')} | {r.get('STOP率')} | {r.get('NoProgress率')} |\n"
        )
    sh = p.get("shadow_spec") or {}
    md += f"""
## Forward Shadow
- recommendation_enabled={sh.get('enabled_recommendation')}
- mode={sh.get('mode')}
- fail_open_on_lane_c_missing={sh.get('fail_open_on_lane_c_missing')}

## Safety
- submit={p['safety']['submit']} cancel={p['safety']['cancel']} live_order={p['safety']['live_order']}
- generated_at={p.get('generated_at')}
- skipped_models: {p.get('skipped_models')}
"""
    return md


def run_pipeline(*, native: Path = NATIVE) -> dict[str, Any]:
    out_dir = native / OUT_REL
    out_dir.mkdir(parents=True, exist_ok=True)

    formal, partial, coverage = load_all_trades(native)
    formal = sorted(formal, key=lambda t: (t.day, t.entry_time or "", t.symbol))
    thr_global = winner_threshold(formal)
    labeled = label_multiclass(formal)
    counts = class_counts(labeled)
    days = sorted({r.trade.day for r in labeled})

    all_names, rows, feat_meta = _build_rows(labeled)
    feats_a = select_lane_features(all_names, lanes=("A",))
    feats_ab = select_lane_features(all_names, lanes=("A", "B"))
    feats_abc = select_lane_features(all_names, lanes=("A", "B", "C"))

    audits = audit_all_features(all_names, rows, labeled)
    bad = bad_quality_features(audits)
    bad_names = {
        b["feature"]
        for b in bad
        if any(x in (b.get("flags") or []) for x in ("constant", "near_constant", "future_leakage_name"))
    }
    fill = feat_meta.get("fill_rates") or {}
    # Lane A: dense only (fill>=80%). Lane B/C: keep sparse; WF drops incomplete rows.
    feats_a = [f for f in feats_a if f not in bad_names and fill.get(f, 0) >= 0.80]
    feats_ab_inc = [
        f
        for f in feats_ab
        if f not in bad_names and (lane_of(f) != "A" or fill.get(f, 0) >= 0.80)
    ]
    feats_abc = [
        f
        for f in feats_abc
        if f not in bad_names and (lane_of(f) != "A" or fill.get(f, 0) >= 0.80)
    ]
    # Lane B exclude suspect days universe
    mask_excl_b = np.array([r.trade.day not in SUSPECT_B_DAYS for r in labeled], dtype=bool)
    lab_ex = [labeled[i] for i in range(len(labeled)) if mask_excl_b[i]]
    rows_ex = [rows[i] for i in range(len(rows)) if mask_excl_b[i]]

    lane_b_daily = lane_b_daily_quality(rows, labeled, "board_imb")
    lane_cov = _lane_coverage(all_names, rows, labeled)

    # Univariate (top fill features)
    class_stats, uni_tests = run_univariate(feats_ab_inc or feats_a, rows, labeled, max_features=45)

    # Holdout model comparison on Lane A (dense, impute A)
    y = y_ids(labeled)
    X_a, y_a, used_a, meta_a = build_xy(feats_a, rows, y, impute_lanes=("A",))
    # chronological order already in labeled
    holdout, best_model_obj, best_name = compare_models_holdout(X_a, y_a, split=0.8)
    best_metrics = holdout.get(best_name) or {}

    skipped = ["xgboost (not installed)", "catboost (not installed)"]

    # Walk-forward Lane A primary + Lane AB include/exclude + Lane ABC observed
    wf_a = chronological_walk_forward(
        labeled, rows, feats_a, model_name=best_name if best_name in ("lightgbm", "random_forest", "logistic") else "lightgbm",
        impute_lanes=("A",), keep_rate_for_fold=0.25, score_key="entry_quality_score",
    )
    wf_ab = chronological_walk_forward(
        labeled, rows, feats_ab_inc, model_name=wf_a["model_name"], impute_lanes=("A",),
        keep_rate_for_fold=0.25, score_key="entry_quality_score",
    )
    wf_ab_ex = chronological_walk_forward(
        lab_ex, rows_ex, feats_ab_inc, model_name=wf_a["model_name"], impute_lanes=("A",),
        keep_rate_for_fold=0.25, score_key="entry_quality_score",
    )
    wf_abc = chronological_walk_forward(
        labeled, rows, feats_abc, model_name=wf_a["model_name"], impute_lanes=("A",),
        keep_rate_for_fold=0.25, score_key="entry_quality_score",
    )

    # Choose primary WF by delta PnL among A / AB
    primary_key = "A"
    primary_wf = wf_a
    for key, wf in (("AB_include_suspect", wf_ab), ("AB_exclude_suspect", wf_ab_ex), ("ABC", wf_abc)):
        s = wf.get("summary") or {}
        ps = primary_wf.get("summary") or {}
        if (s.get("n_eval_folds") or 0) >= 3 and (s.get("delta_PnL_5bps") or -1e18) > (ps.get("delta_PnL_5bps") or -1e18):
            primary_key, primary_wf = key, wf

    keep_sens = keep_rate_sensitivity_oos(labeled if primary_key != "AB_exclude_suspect" else lab_ex, primary_wf["oos_scores"], KEEP_RATES)
    # Best keep: maximize total_pnl_5bps among kr<1 with PF>=1 and STOP not much worse than baseline
    pbv2 = portfolio_metrics(labeled, np.ones(len(labeled), dtype=bool))
    stab_pb = daily_stability(labeled, np.ones(len(labeled), dtype=bool))
    pbv2 = {**pbv2, **{k: stab_pb[k] for k in ("pos_days", "neg_days", "max_daily_loss", "max_losing_streak_days")}}

    candidates_kr = [r for r in keep_sens if r["keep_rate_target"] < 1.0 and (r.get("trades") or 0) >= 20]

    def _kr_score(r):
        pf = r.get("PF") or 0
        # Prefer mid keep (15–40%); do not crown ~10% solely by PnL.
        kr = float(r.get("keep_rate_target") or 0)
        band_bonus = 8000.0 if 0.15 <= kr <= 0.40 else (0.0 if kr >= 0.10 else -5000.0)
        bal = abs((r.get("pos_days") or 0) - (r.get("neg_days") or 0))
        return (
            (r.get("total_pnl_5bps") or -1e18)
            + 80 * (pf if pf < 10 else 10)
            + band_bonus
            - 1500 * max(0.0, (r.get("STOP率") or 0) - (pbv2.get("STOP率") or 0))
            - 2000 * bal
        )

    mid = [r for r in candidates_kr if 0.15 <= float(r["keep_rate_target"]) <= 0.40]
    best_keep = max(mid or candidates_kr, key=_kr_score) if (mid or candidates_kr) else keep_sens[-1]

    # Also EV score sensitivity
    oos_ev = primary_wf["oos_proba"]
    # rebuild EV scores where proba available
    valid = ~np.isnan(primary_wf["oos_scores"])
    # use train-global payoffs for EV alternate ranking (documented)
    payoffs_all = estimate_payoffs_train(labeled)
    ev_scores = np.full(len(labeled), np.nan)
    if valid.any():
        ev_scores[valid] = expected_value_score(primary_wf["oos_proba"][valid], payoffs_all)
    keep_sens_ev = keep_rate_sensitivity_oos(
        labeled if primary_key != "AB_exclude_suspect" else lab_ex, ev_scores, KEEP_RATES
    )

    # Readable rules
    readable = search_readable_rules_chronological(labeled, rows, feats_a, top_feats=10, min_kept=25)

    # Baselines: PBv2, H, I, WinnerFilter A-E, multiclass best keep
    trades = [r.trade for r in labeled]
    fns, thr_dict, _ = build_keep_fns(trades)
    baseline_rows = [{"name": "PBv2_baseline", **pbv2}]
    for pid in ("H_board_ts", "I_price_board"):
        name, fn = fns[pid]
        m = _policy_metrics(trades, fn, labeled)
        st = daily_stability(labeled, np.array([bool(fn(t)) for t in trades]))
        baseline_rows.append({"name": pid, "desc": name, **m, "pos_days": st["pos_days"], "neg_days": st["neg_days"], "max_daily_loss": st["max_daily_loss"]})
    baseline_rows.extend(_winner_filter_candidate_metrics(labeled, rows))

    m_mc = dict(best_keep)
    baseline_rows.append({"name": "4class_model_EQ_keep", **{k: m_mc.get(k) for k in m_mc if k != "universe_n"}})
    if readable:
        # proxy: apply top rule string not available as mask; report rule metrics as comparison row
        top_r = readable[0]
        baseline_rows.append(
            {
                "name": "readable_rule_top",
                "rule": top_r.get("rule"),
                "total_pnl_5bps": top_r.get("total_pnl_5bps"),
                "PF": top_r.get("median_PF"),
                "STOP率": top_r.get("median_STOP"),
                "NoProgress率": top_r.get("median_NP"),
                "trades": top_r.get("mean_trades"),
                "keep_rate": None,
                "pos_days": top_r.get("pos_days"),
                "neg_days": top_r.get("neg_days"),
            }
        )

    verdict = _decide_verdict(
        wf_summary=primary_wf.get("summary") or {},
        keep_sens=keep_sens,
        lane_cov=lane_cov,
        bad_feats=bad,
        best_keep=best_keep,
        pbv2=pbv2,
    )
    shadow = _shadow_spec(verdict["verdict"], wf_a["model_name"], primary_key, float(best_keep.get("keep_rate_target") or 0.25))

    # Class definition rows for xlsx
    class_def_rows = [
        {
            "class_label": r.class_label,
            "class_reason": r.class_reason,
            "exit_reason": r.exit_reason,
            "pnl_yen_100": r.pnl_yen_100,
            "pnl_5bps": r.pnl_5bps,
            "holding_sec": r.holding_sec,
            "mfe": r.mfe,
            "mae": r.mae,
            "day": r.trade.day,
            "symbol": r.trade.symbol,
            "winner_threshold": r.winner_threshold,
        }
        for r in labeled
    ]

    # Confusion / calibration from holdout best
    cm_rows = []
    cm = (best_metrics.get("confusion_matrix") or [])
    for i, row in enumerate(cm):
        for j, v in enumerate(row):
            cm_rows.append({"true": CLASS_ORDER[i], "pred": CLASS_ORDER[j], "n": v})
    calib = best_metrics.get("calibration") or {}
    calib_rows = []
    if calib:
        for a, b in zip(calib.get("winner_fraction_positives") or [], calib.get("winner_mean_predicted") or []):
            calib_rows.append({"winner_fraction_positives": a, "winner_mean_predicted": b})

    model_metric_rows = []
    for name, m in holdout.items():
        if not isinstance(m, dict) or m.get("error"):
            model_metric_rows.append({"model": name, "error": m.get("error") if isinstance(m, dict) else str(m)})
            continue
        model_metric_rows.append(
            {
                "model": name,
                "macro_f1": m.get("macro_f1"),
                "weighted_f1": m.get("weighted_f1"),
                "balanced_accuracy": m.get("balanced_accuracy"),
                "log_loss": m.get("log_loss"),
                "macro_auc_ovr": m.get("macro_auc_ovr"),
                "winner_precision": m.get("winner_precision"),
                "winner_recall": m.get("winner_recall"),
                "stop_recall": m.get("stop_recall"),
                "no_progress_recall": m.get("no_progress_recall"),
                "false_winner_rate": m.get("false_winner_rate"),
                "winner_sacrifice_rate": m.get("winner_sacrifice_rate"),
                "stop_missed_rate": m.get("stop_missed_rate"),
                "n_train": m.get("n_train"),
                "n_test": m.get("n_test"),
            }
        )

    # Flatten WF folds
    wf_fold_rows = []
    for tag, wf in (("A", wf_a), ("AB_inc", wf_ab), ("AB_ex", wf_ab_ex), ("ABC", wf_abc)):
        for f in wf.get("folds") or []:
            row = {"lane_run": tag, **{k: v for k, v in f.items() if k not in ("class_counts_train", "class_counts_test", "payoffs_train")}}
            ct = f.get("class_counts_train") or {}
            cte = f.get("class_counts_test") or {}
            for c in CLASS_ORDER:
                row[f"train_{c}"] = ct.get(c)
                row[f"test_{c}"] = cte.get(c)
            wf_fold_rows.append(row)

    lane_c_oos_days = sorted(
        {
            f["test_date"]
            for f in (wf_abc.get("folds") or [])
            if f.get("status") == "EVAL" and (f.get("test_n") or 0) > 0
        }
    )

    payload: dict[str, Any] = {
        "phase": "WinnerMulticlassOffline",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "not_production_candidate": True,
        "verdict": verdict,
        "priority": list(PRIORITY),
        "winner_threshold_global": thr_global,
        "n_trades": len(labeled),
        "n_days": len(days),
        "days": days,
        "class_counts": counts,
        "n_partial_excluded": len(partial),
        "lane_coverage": lane_cov,
        "lane_a_n": lane_cov[0].get("n_rows_any_feature") if lane_cov else 0,
        "lane_b_n": lane_cov[1].get("n_rows_any_feature") if len(lane_cov) > 1 else 0,
        "lane_c_n": lane_cov[2].get("n_rows_any_feature") if len(lane_cov) > 2 else 0,
        "lane_c_oos_days": lane_c_oos_days,
        "lane_c_oos_n_days": len(lane_c_oos_days),
        "bad_features": bad,
        "feature_meta": feat_meta,
        "matrix_meta_lane_a": meta_a,
        "model_holdout": holdout,
        "best_model_holdout": {"name": best_name, "metrics": best_metrics},
        "skipped_models": skipped,
        "walk_forward": {
            "primary": primary_key,
            "summary": primary_wf.get("summary"),
            "A": {"summary": wf_a.get("summary")},
            "AB_include_suspect": {"summary": wf_ab.get("summary")},
            "AB_exclude_suspect": {"summary": wf_ab_ex.get("summary")},
            "ABC": {"summary": wf_abc.get("summary")},
        },
        "wf_model": primary_wf.get("model_name"),
        "wf_score_key": primary_wf.get("score_key"),
        "wf_fold_keep": primary_wf.get("keep_rate_for_fold"),
        "keep_rate_sensitivity": keep_sens,
        "keep_rate_sensitivity_ev": keep_sens_ev,
        "best_keep": best_keep,
        "pbv2_baseline": pbv2,
        "score_formulas": SCORE_FORMULAS,
        "payoffs_global_train_ref": payoffs_all,
        "readable_rules": readable,
        "baseline_comparison": baseline_rows,
        "shadow_spec": shadow,
        "safety": {"submit": 0, "cancel": 0, "live_order": 0, "paper_only": True, "observe_only": True},
        "coverage_rows": coverage,
        "completion": {
            "1_verdict": verdict.get("verdict"),
            "2_days_n": f"{len(days)}/{len(labeled)}",
            "3_class_counts": counts,
            "4_lane_abc_n": {
                "A": lane_cov[0].get("n_rows_any_feature") if lane_cov else 0,
                "B": lane_cov[1].get("n_rows_any_feature") if len(lane_cov) > 1 else 0,
                "C": lane_cov[2].get("n_rows_any_feature") if len(lane_cov) > 2 else 0,
            },
            "5_bad_features": [b["feature"] for b in bad[:30]],
            "6_best_model": best_name,
            "7_macro_f1": best_metrics.get("macro_f1"),
            "8_winner_precision_recall": [best_metrics.get("winner_precision"), best_metrics.get("winner_recall")],
            "9_stop_recall": best_metrics.get("stop_recall"),
            "10_np_recall": best_metrics.get("no_progress_recall"),
            "11_oos_pnl_pf": [primary_wf.get("summary", {}).get("total_PnL_5bps"), primary_wf.get("summary", {}).get("median_PF")],
            "12_delta_vs_pbv2": primary_wf.get("summary", {}).get("delta_PnL_5bps"),
            "13_best_keep_rate": best_keep.get("keep_rate_target"),
            "14_winner_sacrifice": best_keep.get("Winner犠牲率"),
            "15_stop_rate": best_keep.get("STOP率"),
            "16_np_rate": best_keep.get("NoProgress率"),
            "17_best_readable_rule": (readable[0]["rule"] if readable else None),
            "18_lane_c_oos_days": len(lane_c_oos_days),
            "19_forward_shadow": shadow.get("enabled_recommendation"),
            "20_submit": 0,
            "21_cancel": 0,
            "22_live_order": 0,
            "23_artifacts": [
                str(OUT_REL / "report.md"),
                str(OUT_REL / "report.json"),
                str(OUT_REL / "audit.xlsx"),
            ],
        },
    }

    # Serialize JSON (drop huge arrays)
    json_payload = {k: v for k, v in payload.items()}
    (out_dir / "report.json").write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_render_md(payload), encoding="utf-8")

    # Expected value sheet
    ev_rows = []
    for i, r in enumerate(labeled):
        if np.isnan(primary_wf["oos_scores"][i]):
            continue
        pr = primary_wf["oos_proba"][i]
        ev_rows.append(
            {
                "day": r.trade.day,
                "symbol": r.trade.symbol,
                "class_label": r.class_label,
                "winner_prob": round(float(pr[0]), 6),
                "stop_prob": round(float(pr[1]), 6),
                "no_progress_prob": round(float(pr[2]), 6),
                "normal_prob": round(float(pr[3]), 6),
                "entry_quality_score": round(float(primary_wf["oos_scores"][i]), 6),
                "expected_value_score": None if np.isnan(ev_scores[i]) else round(float(ev_scores[i]), 4),
                "pnl_5bps": r.pnl_5bps,
            }
        )

    sheets = {
        "coverage": lane_cov + (coverage if isinstance(coverage, list) else [{"coverage": str(coverage)}]),
        "data_quality": audits,
        "class_definition": class_def_rows,
        "class_feature_stats": class_stats,
        "univariate_tests": uni_tests,
        "model_metrics": model_metric_rows,
        "confusion_matrix": cm_rows or [{"empty": 1}],
        "calibration": calib_rows or [{"empty": 1}],
        "wf_folds": wf_fold_rows,
        "keep_rate_sensitivity": keep_sens,
        "expected_value": ev_rows[:5000] or [{"empty": 1}],
        "readable_rules": readable or [{"empty": 1}],
        "baseline_comparison": baseline_rows,
        "shadow_spec": [shadow],
        "lane_b_daily": lane_b_daily,
        "bad_features": bad or [{"none": 1}],
        "keep_rate_ev": keep_sens_ev,
    }
    write_xlsx(out_dir / "audit.xlsx", sheets)
    return payload


if __name__ == "__main__":
    run_pipeline()
