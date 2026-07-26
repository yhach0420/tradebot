"""Next-stage forward validation orchestration for Winner Feature Filter."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.cost_aware_v2.dataset import NATIVE, load_all_trades
from research.winner_feature_filter.features import build_matrix
from research.winner_feature_filter.forward_validation import (
    chronological_walk_forward,
    decide_verdict,
    filter_labeled_days,
    fixed_candidates,
    in_sample_eval,
    pbv2_baseline,
)
from research.winner_feature_filter.lane_b_audit import audit_lane_b_imbalance
from research.winner_feature_filter.labels import cohort_counts, label_trades
from research.winner_feature_filter.lanes import LANE_A, LANE_B, LANE_C, TIME_FEATURE_BLOCKLIST
from research.winner_feature_filter.pipeline import write_xlsx

JST = ZoneInfo("Asia/Tokyo")
OUT_REL = Path("results") / "research" / "winner_feature_filter"


def _render_forward_md(payload: Mapping[str, Any]) -> str:
    v = payload["verdict"]
    lane_b = payload.get("lane_b_audit") or {}
    cands = payload.get("candidates") or {}
    md = f"""# Winner Feature Filter — Forward Validation

## Verdict
- **{v.get('verdict')}**
- {v.get('reason')}
- 本結果は **本線実装候補としては扱わない**（次段検証）。observe-only / Paper only。

## 方法論（修正方針の適用）
1. **時間帯特徴は完全除外**: {', '.join(TIME_FEATURE_BLOCKLIST[:6])} ...
2. **Lane分割**
   - Lane A（dense）: {len(LANE_A)} keys — TV/VWAP/ATR/near_high/mom/chase/rise 等
   - Lane B（静的板）: {len(LANE_B)} keys — imbalance / imb_pct
   - Lane C（流動板）: {len(LANE_C)} keys — spread/board_age/np_imb_chg/vol_surge 等
3. **Lane C は中央値補完禁止** — 実測行のみ
4. **Lane B 品質監査** — suspect日の INCLUDE/EXCLUDE を分離評価
5. **Chronological walk-forward** — 各test日は過去日のみで閾値決定
6. 候補は固定閾値と再推定を分離
7. STOP率≥20% は要注意（無条件採用しない）
8. Winner率単独順位付けはしない

## Lane B 品質監査
- suspect_days: {lane_b.get('suspect_days')}
- ok_days: {lane_b.get('ok_days')}
- recommendation: {lane_b.get('recommendation')}

| day | n | mean | std | frac[0.43,0.53] | flag |
|-----|---|------|-----|-----------------|------|
"""
    for r in (lane_b.get("by_day") or []):
        md += (
            f"| {r['day']} | {r['n']} | {r['mean']} | {r['std']} | "
            f"{r['frac_in_0.43_0.53']} | {r['quality_flag']} |\n"
        )
    md += "\n### Findings\n"
    for f in lane_b.get("findings") or []:
        md += f"- [{f['status']}] {f['check']}: {f['note']}\n"

    md += """
## 候補ルール A–E

| ID | Lanes | 固定閾値 | 市場状態 |
|----|-------|----------|----------|
"""
    for cid, block in cands.items():
        cov = (block.get("coverage") or {})
        narr = (block.get("narrative") or {})
        md += (
            f"| {cid} | {cov.get('lanes')} | `{block.get('fixed_threshold_text')}` | "
            f"{narr.get('market_state')} |\n"
        )

    md += """
## In-sample（参考・本線判定には使わない）

| ID | mode | universe | n_kept | Winner率 | mean_pnl | PF | STOP | NP | ΔPnL vs eligible PBv2 | STOP注意 |
|----|------|----------|--------|----------|----------|----|------|----|----------------------|----------|
"""
    for cid, block in cands.items():
        for key in ("in_sample_fixed", "in_sample_fixed_excl_suspect_b"):
            ins = block.get(key) or {}
            m = ins.get("metrics") or {}
            md += (
                f"| {cid} | {key} | {ins.get('coverage',{}).get('n_eligible')} | {m.get('n_kept')} | "
                f"{m.get('winner_rate')} | {m.get('mean_pnl')} | {m.get('pf')} | "
                f"{m.get('stop_rate')} | {m.get('np_rate')} | {ins.get('delta_pnl_vs_eligible_pbv2')} | "
                f"{'YES' if m.get('stop_caution') else ''} |\n"
            )

    md += """
## Chronological Walk-Forward（主判定）

各fold: train_start / train_end / test_date / thresholds / train_n / test_n / kept_n / PnL / PF / Winner率 / STOP / NP

### Summary
| ID | mode | eval_folds | ΔPnL | ΔPnL_5bps | pos | neg | med_PF | med_STOP | STOP注意folds |
|----|------|------------|------|-----------|-----|-----|--------|----------|---------------|
"""
    for cid, block in cands.items():
        for mode_key in ("wf_fixed", "wf_reestimated", "wf_fixed_excl_suspect_b"):
            wf = block.get(mode_key) or {}
            s = wf.get("summary") or {}
            if not s:
                continue
            md += (
                f"| {cid} | {mode_key} | {s.get('n_eval_folds')} | {s.get('delta_PnL')} | "
                f"{s.get('delta_PnL_5bps')} | {s.get('pos_days')} | {s.get('neg_days')} | "
                f"{s.get('median_PF')} | {s.get('median_stop_rate')} | {s.get('stop_caution_folds')} |\n"
            )

    md += """
### Fold detail（EVALのみ抜粋）
| ID | mode | train_start | train_end | test_date | kept_n | PnL | PF | Winner率 | STOP | NP | ΔPnL |
|----|------|-------------|-----------|-----------|--------|-----|----|----------|------|----|------|
"""
    for cid, block in cands.items():
        for mode_key in ("wf_fixed", "wf_reestimated"):
            wf = block.get(mode_key) or {}
            for f in wf.get("folds") or []:
                if f.get("status") != "EVAL":
                    continue
                md += (
                    f"| {cid} | {mode_key} | {f.get('train_start')} | {f.get('train_end')} | "
                    f"{f.get('test_date')} | {f.get('kept_n')} | {f.get('PnL')} | {f.get('PF')} | "
                    f"{f.get('Winner率')} | {f.get('STOP率')} | {f.get('NoProgress率')} | "
                    f"{f.get('delta_PnL')} |\n"
                )

    md += """
## 候補の意味（仮説）
"""
    for cid, block in cands.items():
        narr = block.get("narrative") or {}
        md += f"""
### Candidate {cid}
- **市場状態:** {narr.get('market_state')}
- **上昇仮説:** {narr.get('rise_hypothesis')}
- **失敗EXIT:** {narr.get('failure_exit')}
- **不足時の悪化:** {narr.get('missing_feature_risk')}
"""

    md += f"""
## Coverage notes（Lane C）
Lane C 特徴を含む候補は実測行のみ。母数・初日・終日は各候補 `coverage` を参照。

## Safety
- submit/cancel/live_order: **0/0/0**
- paper_only / observe_only
- generated_at: {payload.get('generated_at')}
"""
    # Verdict buckets
    for key in ("improved", "caution", "worsened"):
        rows = v.get(key) or []
        if rows:
            md += f"\n### Verdict bucket: {key}\n"
            for r in rows:
                md += f"- {r}\n"
    return md


def run_forward_pipeline(*, native: Path = NATIVE) -> dict[str, Any]:
    out_dir = native / OUT_REL
    out_dir.mkdir(parents=True, exist_ok=True)

    formal, partial, coverage = load_all_trades(native)
    labeled = label_trades(formal)
    labeled = sorted(labeled, key=lambda r: (r.trade.day, r.trade.entry_time))
    counts = cohort_counts(labeled)

    _, feat_rows, feat_meta = build_matrix(labeled, native=native)
    # Drop any time features from rows used in validation
    clean_rows: list[dict[str, Any]] = []
    for r in feat_rows:
        clean_rows.append({k: v for k, v in r.items() if not any(b in k.lower() for b in TIME_FEATURE_BLOCKLIST)})

    lane_b_audit = audit_lane_b_imbalance(labeled)
    baseline = pbv2_baseline(labeled)

    specs = fixed_candidates()
    candidates_out: dict[str, Any] = {}
    wf_for_verdict: list[dict[str, Any]] = []

    lab_ex, rows_ex = filter_labeled_days(labeled, clean_rows, exclude_suspect_b=True)

    for spec in specs:
        thr_text = " AND ".join(f"{p.name} {p.op} {p.threshold}" for p in spec.predicates)
        ins_fixed = in_sample_eval(labeled, clean_rows, spec, mode="fixed")
        ins_excl = in_sample_eval(lab_ex, rows_ex, spec, mode="fixed")
        wf_fixed = chronological_walk_forward(labeled, clean_rows, spec, mode="fixed")
        wf_re = chronological_walk_forward(labeled, clean_rows, spec, mode="reestimated")
        wf_excl = chronological_walk_forward(lab_ex, rows_ex, spec, mode="fixed_excl_suspect_b")

        candidates_out[spec.cand_id] = {
            "fixed_threshold_text": thr_text,
            "narrative": {
                "market_state": spec.market_state,
                "rise_hypothesis": spec.rise_hypothesis,
                "failure_exit": spec.failure_exit,
                "missing_feature_risk": spec.missing_feature_risk,
            },
            "coverage": ins_fixed.get("coverage"),
            "in_sample_fixed": ins_fixed,
            "in_sample_fixed_excl_suspect_b": ins_excl,
            "wf_fixed": wf_fixed,
            "wf_reestimated": wf_re,
            "wf_fixed_excl_suspect_b": wf_excl,
        }
        wf_for_verdict.extend([wf_fixed, wf_re, wf_excl])

    verdict = decide_verdict(wf_for_verdict)

    payload: dict[str, Any] = {
        "phase": "WinnerFeatureFilterForwardValidation",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "not_production_candidate": True,
        "verdict": verdict,
        "purpose": "Next-stage validation of Winner Feature Filter (not mainline deploy)",
        "methodology": {
            "time_features_excluded": list(TIME_FEATURE_BLOCKLIST),
            "lanes": {"A": sorted(LANE_A), "B": sorted(LANE_B), "C": sorted(LANE_C)},
            "lane_c_imputation": "FORBIDDEN_observed_only",
            "walk_forward": "chronological_train_days_strictly_before_test_date",
            "stop_caution_threshold": 0.20,
            "ranking": "expectancy(PF,mean_pnl,winner_rate) with STOP penalty; not importance-only",
        },
        "n_trades": len(labeled),
        "n_days": len({lt.trade.day for lt in labeled}),
        "days": sorted({lt.trade.day for lt in labeled}),
        "cohort_counts": counts,
        "n_partial_excluded": len(partial),
        "pbv2_baseline_all": baseline,
        "lane_b_audit": lane_b_audit,
        "feature_meta": feat_meta,
        "candidates": candidates_out,
        "coverage_rows": coverage,
        "safety": {"submit": 0, "cancel": 0, "live_order": 0, "paper_only": True, "observe_only": True},
    }

    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_render_forward_md(payload), encoding="utf-8")

    # audit sheets
    fold_rows = []
    for cid, block in candidates_out.items():
        for mode_key in ("wf_fixed", "wf_reestimated", "wf_fixed_excl_suspect_b"):
            for f in (block.get(mode_key) or {}).get("folds") or []:
                fold_rows.append({"cand_id": cid, "wf": mode_key, **f})

    ins_rows = []
    for cid, block in candidates_out.items():
        for key in ("in_sample_fixed", "in_sample_fixed_excl_suspect_b"):
            ins = block.get(key) or {}
            m = ins.get("metrics") or {}
            ins_rows.append(
                {
                    "cand_id": cid,
                    "mode": key,
                    "n_eligible": (ins.get("coverage") or {}).get("n_eligible"),
                    "first_day": (ins.get("coverage") or {}).get("first_day"),
                    "last_day": (ins.get("coverage") or {}).get("last_day"),
                    **{k: m.get(k) for k in (
                        "n_kept", "keep_rate", "winner_rate", "winner_capture", "stop_rate", "np_rate",
                        "mean_pnl", "total_pnl", "total_pnl_5bps", "pf", "pf_5bps",
                        "pos_days", "neg_days", "max_daily_loss", "max_losing_streak_days",
                        "cap_usage_mean", "stop_caution", "expectancy_score",
                    )},
                    "delta_pnl": ins.get("delta_pnl_vs_eligible_pbv2"),
                    "delta_pnl_5bps": ins.get("delta_pnl_5bps_vs_eligible_pbv2"),
                }
            )

    summary_rows = []
    for cid, block in candidates_out.items():
        for mode_key in ("wf_fixed", "wf_reestimated", "wf_fixed_excl_suspect_b"):
            s = (block.get(mode_key) or {}).get("summary") or {}
            summary_rows.append({"cand_id": cid, "wf": mode_key, **s})

    narr_rows = []
    for cid, block in candidates_out.items():
        narr = block.get("narrative") or {}
        narr_rows.append({"cand_id": cid, "thresholds": block.get("fixed_threshold_text"), **narr})

    sheets = {
        "verdict": [verdict],
        "lane_b_by_day": lane_b_audit.get("by_day") or [],
        "lane_b_findings": lane_b_audit.get("findings") or [],
        "candidate_narrative": narr_rows,
        "in_sample_metrics": ins_rows,
        "wf_summary": summary_rows,
        "wf_folds": fold_rows,
        "pbv2_baseline": [baseline],
        "methodology": [payload["methodology"]],
        "cohort_counts": [counts],
    }
    write_xlsx(out_dir / "audit.xlsx", sheets)
    return payload


if __name__ == "__main__":
    run_forward_pipeline()
