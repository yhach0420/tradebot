"""Orchestrate Winner Feature Filter research and write report.md / report.json / audit.xlsx."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.cost_aware_v2.dataset import NATIVE, load_all_trades
from research.winner_feature_filter.cohort import extract_cohort_signatures
from research.winner_feature_filter.combo_search import search_winner_rate_combinations
from research.winner_feature_filter.features import availability_table, build_matrix, matrix_to_xy
from research.winner_feature_filter.importance import run_all_importance
from research.winner_feature_filter.labels import cohort_counts, label_trades
from research.winner_feature_filter.rules import search_all_rules

JST = ZoneInfo("Asia/Tokyo")
OUT_REL = Path("results") / "research" / "winner_feature_filter"


def write_xlsx(path: Path, sheets: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    from openpyxl import Workbook

    def _cell(v: Any) -> Any:
        if v is None or isinstance(v, (int, float, bool, str)):
            return v
        if isinstance(v, datetime):
            return v.isoformat()
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(str(name)[:31])
        if not rows:
            ws.append(["empty"])
            continue
        keys = list(rows[0].keys())
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _feat_family(name: str) -> str:
    if name.startswith("w_"):
        return "window"
    if name.startswith("px_"):
        return "price"
    if name.startswith("board_") or name.startswith("f_imb") or name.startswith("f_np_imb") or name.startswith("f_np_bid") or name.startswith("f_np_ask") or "board" in name:
        return "board"
    if name.startswith("vol_"):
        return "volume"
    if name.startswith("mom_"):
        return "momentum"
    if name.startswith("tech_"):
        return "technical"
    if name.startswith("mkt_"):
        return "market_state"
    if name.startswith("f_np_"):
        return "np_raw"
    if name.startswith("f_"):
        return "runtime_raw"
    return "other"


def _feat_catalog(avail: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    families = []
    for row in avail:
        name = str(row["feature"])
        families.append(
            {
                "feature": name,
                "family": _feat_family(name),
                "n_available": row.get("n_available"),
                "n_total": row.get("n_total"),
                "fill_rate": row.get("fill_rate"),
            }
        )
    return families


BOARD_FEATURE_PREFIXES = (
    "board_",
    "f_imb",
    "f_spread",
    "f_board",
    "f_np_imb",
    "f_np_bid",
    "f_np_ask",
    "w_",  # window board/price; for board-subset we filter below
)


def _is_boardish(name: str) -> bool:
    n = name.lower()
    return any(
        x in n
        for x in (
            "board",
            "imb",
            "spread",
            "bid_chg",
            "ask_chg",
            "np_imb",
            "np_bid",
            "np_ask",
        )
    )


def _pbv2_comparison(baseline: Mapping[str, Any], best: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "baseline_pbv2": dict(baseline),
        "winner_feature_filter": dict(best),
        "delta_winner_capture": round(
            float(best.get("winner_capture") or 0) - float(baseline.get("winner_capture") or 0), 4
        ),
        "delta_stop_rate": round(float(best.get("stop_rate") or 0) - float(baseline.get("stop_rate") or 0), 4),
        "delta_np_rate": round(float(best.get("np_rate") or 0) - float(baseline.get("np_rate") or 0), 4),
        "delta_mean_pnl": round(
            float(best.get("mean_pnl") or 0) - float(baseline.get("mean_pnl") or 0), 2
        ),
        "delta_total_pnl": round(
            float(best.get("total_pnl") or 0) - float(baseline.get("total_pnl") or 0), 2
        ),
        "note": (
            "Filter selects among PBv2 accepts (not a new REJECT stack). "
            "Goal: keep Winner-like entries; shrink STOP/NoProgress share of ENTRY."
        ),
    }


def _render_md(payload: Mapping[str, Any]) -> str:
    counts = payload["cohort_counts"]
    best = payload.get("recommended_filter") or {}
    cmp_ = payload.get("pbv2_comparison") or {}
    winner_sig = (payload.get("cohort_signatures") or {}).get("Winner", {})
    stop_sig = (payload.get("cohort_signatures") or {}).get("STOP", {})
    np_sig = (payload.get("cohort_signatures") or {}).get("NoProgress", {})
    top_imp = (payload.get("feature_importance") or {}).get("merged") or []
    score_meta = (payload.get("rule_search") or {}).get("score_meta") or {}
    components = score_meta.get("components") or []
    imp_meta = payload.get("imputation_meta") or {}
    board_imp = payload.get("board_subset_importance") or {}
    combo_pack = payload.get("winner_rate_combo_pack") or {}
    combos = combo_pack.get("top20_by_winner_rate") or payload.get("winner_rate_combinations_top20") or []
    if isinstance(combos, dict):
        combos = combos.get("top20_by_winner_rate") or []
    exp_rank = combo_pack.get("top20_by_expectancy") or []
    thr_dict = combo_pack.get("threshold_dictionary_from_top20") or {}
    avail_highlight = payload.get("feature_availability_highlights") or []
    base_wr = combo_pack.get("baseline_winner_rate")
    if base_wr is None and combos:
        base_wr = combos[0].get("baseline_winner_rate")

    md = f"""# Winner Feature Filter Report

## Executive Summary
- **目的:** PBv2 accepted の中から利益ENTRY（Winner）を選び出す特徴量フィルタを構築する（REJECT増設ではない）
- **構成:** `PBv2 → Winner Feature Filter → ENTRY`
- **対象:** formal joined PBv2 **{payload['n_trades']}件 / {payload['n_days']}営業日**
- **Winner定義:** 実現損益 上位20%（閾値 **{payload['winner_threshold_yen']} 円**）
- **Cohort:** Winner={counts.get('Winner')} / STOP={counts.get('STOP')} / NoProgress={counts.get('NoProgress')} / Normal={counts.get('Normal')}
- **推奨フィルタ:** `{best.get('rule')}`
- **上昇銘柄らしさスコア:** 合意重要度上位特徴の方向付きz合成（実装可能なスコア）

## 方法論の明示（重要）

### 欠損処理（重要度計算時）
- 採用: **列ごと中央値補完（median imputation）**
- 不採用: NULL行除外 / 0埋め / 平均値補完
- 詳細: `{imp_meta.get('missing_value_policy')}` / null_exclusion={imp_meta.get('null_exclusion')} / zero_fill={imp_meta.get('zero_fill')} / mean_imputation={imp_meta.get('mean_imputation')}
- 重要度は formal **全{payload['n_trades']}件** に対して算出（欠損は中央値で埋めた後）

### board系重要度の母集団
- **メイン重要度ランキング:** formal 全件（中央値補完後）で算出
- **boardサブセット重要度:** `board_imb` / `f_imb` が非NULLの件だけで再計算（比較用）
  - n_board_subset = **{board_imp.get('n_rows')}**
  - 注: `entry_order_book_imbalance` 自体は formal ほぼ全日に値が入っているが、6/15–6/19 は分布が狭い（std≈0.025）ため、BoardDynamic本格化前の品質には注意
  - 板の時系列（`f_np_imb_chg_*` 等）は 7/21 以降が中心（約200件台）

## 特徴量利用可能件数（ハイライト）
| feature | 利用可能件数 | 全体 |
|---------|-------------|------|
"""
    for r in avail_highlight:
        md += f"| `{r['feature']}` | **{r['n_available']}** | {r['n_total']} |\n"

    md += f"""
（全特徴の件数は `audit.xlsx` の `feature_catalog` / `report.json` の `feature_availability` を参照）

## PBv2 比較
| Metric | PBv2 all | + Winner Filter | Δ |
|--------|----------|-----------------|---|
| keep_rate | {cmp_.get('baseline_pbv2',{}).get('keep_rate')} | {cmp_.get('winner_feature_filter',{}).get('keep_rate')} | — |
| winner_capture | {cmp_.get('baseline_pbv2',{}).get('winner_capture')} | {cmp_.get('winner_feature_filter',{}).get('winner_capture')} | {cmp_.get('delta_winner_capture')} |
| winner_precision | {cmp_.get('baseline_pbv2',{}).get('winner_precision')} | {cmp_.get('winner_feature_filter',{}).get('winner_precision')} | — |
| stop_rate (among kept) | {cmp_.get('baseline_pbv2',{}).get('stop_rate')} | {cmp_.get('winner_feature_filter',{}).get('stop_rate')} | {cmp_.get('delta_stop_rate')} |
| np_rate (among kept) | {cmp_.get('baseline_pbv2',{}).get('np_rate')} | {cmp_.get('winner_feature_filter',{}).get('np_rate')} | {cmp_.get('delta_np_rate')} |
| mean_pnl | {cmp_.get('baseline_pbv2',{}).get('mean_pnl')} | {cmp_.get('winner_feature_filter',{}).get('mean_pnl')} | {cmp_.get('delta_mean_pnl')} |
| total_pnl | {cmp_.get('baseline_pbv2',{}).get('total_pnl')} | {cmp_.get('winner_feature_filter',{}).get('total_pnl')} | {cmp_.get('delta_total_pnl')} |

## 解析内容
- ENTRY直前特徴: 30s / 60s / 120s / 5m / 10m / 15m（価格・出来高・板・Momentum・市場状態）
- 古典テクニカル: accept時点の RSI/ATR/VWAP/momentum を使用
- 重要度: LightGBM / Permutation / SHAP / IG / MI → consensus（**全件+中央値補完**）
- Winner率コンボ: 時間帯特徴を除外し、single/AND2/AND3 を Winner率で上位20

### 特徴量カバレッジ
{payload.get('feature_meta',{}).get('note')}

- 使用特徴数: **{payload.get('feature_meta',{}).get('n_features_used')}**
- LightGBM valid AUC: **{(payload.get('feature_importance') or {}).get('lgbm_meta',{}).get('valid_auc')}**

## Top20 閾値辞書（高・低の実数値）
（baseline Winner率={base_wr} / 時間帯特徴は除外）

| feature | 判定 | 閾値（人間可読） |
|---------|------|------------------|
"""
    for feat, items in sorted(thr_dict.items()):
        for t in items:
            md += f"| `{feat}` | {t.get('direction_label')} | `{t.get('human')}` |\n"

    md += """
## Winner率 Top20（実閾値付き）

| rank | 閾値（実数） | n | Winner率 | mean_pnl | PF | STOP率 | NP率 |
|------|-------------|---|----------|----------|----|--------|------|
"""
    for r in combos[:20]:
        md += (
            f"| {r.get('rank_by_winner_rate')} | `{r.get('threshold_text') or r.get('rule')}` | "
            f"{r.get('n_kept')} | **{r.get('winner_rate')}** | {r.get('mean_pnl')} | "
            f"{r.get('pf')} | {r.get('stop_rate')} | {r.get('np_rate')} |\n"
        )

    md += """
## PBv2後段フィルタ採用候補（期待損益順）
重要度ではなく `expectancy_score = f(PF, mean_pnl, Winner率, n, STOP/NP罰)` で順位付け。

| rank | adopt | 閾値（実数） | n | Winner率 | mean_pnl | PF | STOP率 | NP率 | score |
|------|-------|-------------|---|----------|----------|----|--------|------|-------|
"""
    for r in exp_rank[:20]:
        md += (
            f"| {r.get('rank_by_expectancy')} | {'YES' if r.get('adopt_candidate') else ''} | "
            f"`{r.get('threshold_text') or r.get('rule')}` | {r.get('n_kept')} | "
            f"{r.get('winner_rate')} | {r.get('mean_pnl')} | {r.get('pf')} | "
            f"{r.get('stop_rate')} | {r.get('np_rate')} | {r.get('expectancy_score')} |\n"
        )

    md += """
## Winner 特徴（上位）
| feature | direction | |d| / consensus |
|---------|-----------|----------------|
"""
    for r in (winner_sig.get("common_signature") or [])[:12]:
        md += f"| `{r['feature']}` | {r['direction']} | d={r['cohens_d']} |\n"
    md += "\n### Consensus importance Top15（全件+中央値補完）\n"
    md += "| rank | feature | consensus | shap | lgbm_gain | MI | n_available |\n|------|---------|-----------|------|-----------|----|-------------|\n"
    avail_map = {a["feature"]: a.get("n_available") for a in (payload.get("feature_availability") or [])}
    for i, r in enumerate(top_imp[:15], 1):
        md += (
            f"| {i} | `{r['feature']}` | {r['consensus_score']} | {r['shap_mean_abs']} | "
            f"{r['lgbm_gain']} | {r['mutual_info']} | {avail_map.get(r['feature'])} |\n"
        )

    board_merged = board_imp.get("merged_top") or []
    md += "\n### Board-subset importance Top10（board特徴が非NULLの件のみ）\n"
    md += "| rank | feature | consensus | n_rows |\n|------|---------|-----------|--------|\n"
    for i, r in enumerate(board_merged[:10], 1):
        md += f"| {i} | `{r['feature']}` | {r['consensus_score']} | {board_imp.get('n_rows')} |\n"

    md += """
## STOP 特徴（上位）
| feature | direction | d |
|---------|-----------|---|
"""
    for r in (stop_sig.get("common_signature") or [])[:12]:
        md += f"| `{r['feature']}` | {r['direction']} | {r['cohens_d']} |\n"

    md += """
## NoProgress 特徴（上位）
| feature | direction | d |
|---------|-----------|---|
"""
    for r in (np_sig.get("common_signature") or [])[:12]:
        md += f"| `{r['feature']}` | {r['direction']} | {r['cohens_d']} |\n"

    md += f"""
## 推奨フィルタ / 上昇銘柄らしさスコア

### Recommended discrete rule
- **type:** `{best.get('type')}`
- **rule:** `{best.get('rule')}`
- **keep_rate:** {best.get('keep_rate')}
- **winner_capture:** {best.get('winner_capture')}
- **winner_precision:** {best.get('winner_precision')}
- **stop_rate:** {best.get('stop_rate')}
- **np_rate:** {best.get('np_rate')}
- **mean_pnl:** {best.get('mean_pnl')}

### Winner Rise Score
`score = Σ weight_i * direction_i * z(feature_i)`

| feature | weight | direction |
|---------|--------|-----------|
"""
    for c in components:
        md += f"| `{c['feature']}` | {c['weight']} | {c['direction']} |\n"

    sm_best = payload.get("recommended_score_filter") or score_meta.get("best") or {}
    md += f"""
推奨スコア閾値: `{sm_best.get('rule')}`  
- keep_rate={sm_best.get('keep_rate')} / winner_capture={sm_best.get('winner_capture')} / winner_precision={sm_best.get('winner_precision')}
- stop_rate={sm_best.get('stop_rate')} / np_rate={sm_best.get('np_rate')} / mean_pnl={sm_best.get('mean_pnl')}

## Safety
- submit/cancel/live_order: **0/0/0**
- Paper Trade only
- generated_at: {payload.get('generated_at')}
"""
    return md


def run_pipeline(*, native: Path = NATIVE) -> dict[str, Any]:
    out_dir = native / OUT_REL
    out_dir.mkdir(parents=True, exist_ok=True)

    formal, partial, coverage = load_all_trades(native)
    labeled = label_trades(formal)
    counts = cohort_counts(labeled)
    thr = labeled[0].winner_threshold if labeled else 0.0

    # Sort by day+time for chronological LGBM split
    order = sorted(range(len(labeled)), key=lambda i: (labeled[i].trade.day, labeled[i].trade.entry_time))
    labeled = [labeled[i] for i in order]

    feature_names, feat_rows, feat_meta = build_matrix(labeled, native=native)
    avail = availability_table(feature_names, feat_rows, feat_meta.get("fill_rates") or {})
    y = [1 if r.is_winner else 0 for r in labeled]
    X, y_arr, feature_names, imput_meta = matrix_to_xy(feature_names, feat_rows, y)

    imp = run_all_importance(X, y_arr, feature_names)
    # Drop non-serializable
    model = imp.pop("model", None)
    shap_values = imp.pop("shap_values", None)

    # Board-subset importance: rows where board_imb/f_imb present (non-null before impute)
    board_idx = [
        i
        for i, r in enumerate(feat_rows)
        if r.get("board_imb") is not None or r.get("f_imb") is not None
    ]
    board_feats = [n for n in feature_names if _is_boardish(n)]
    board_subset_importance: dict[str, Any] = {
        "n_rows": len(board_idx),
        "n_board_features": len(board_feats),
        "universe": "rows_with_non_null_board_imb_or_f_imb",
        "merged_top": [],
        "note": (
            "Recomputed importance on subset with observed board imbalance only; "
            "still uses median impute within that subset for other sparse board fields."
        ),
    }
    if len(board_idx) >= 80 and board_feats:
        import numpy as np

        sub_rows = [feat_rows[i] for i in board_idx]
        sub_labeled_y = [y[i] for i in board_idx]
        Xb, yb, board_feats, _ = matrix_to_xy(board_feats, sub_rows, sub_labeled_y)
        # Keep feature count manageable
        if Xb.shape[1] > 2 and len(np.unique(yb)) > 1:
            b_imp = run_all_importance(Xb, yb, board_feats)
            b_imp.pop("model", None)
            b_imp.pop("shap_values", None)
            board_subset_importance["merged_top"] = (b_imp.get("merged") or [])[:30]
            board_subset_importance["lgbm_meta"] = b_imp.get("lgbm_meta")

    cohort_sig = extract_cohort_signatures(labeled, feature_names, feat_rows)
    rules = search_all_rules(labeled, feat_rows, feature_names, imp["merged"])
    combo_pack = search_winner_rate_combinations(
        labeled, feat_rows, imp["merged"], top_n=20
    )
    winner_combos = combo_pack.get("top20_by_winner_rate") or []
    expectancy_rank = combo_pack.get("top20_by_expectancy") or []
    # Prefer expectancy #1 as recommended post-PBv2 filter when available
    if expectancy_rank:
        top_exp = expectancy_rank[0]
        recommended_from_combo = {
            "type": top_exp.get("type"),
            "rule": top_exp.get("rule"),
            "threshold_text": top_exp.get("threshold_text"),
            "thresholds": top_exp.get("thresholds"),
            "n_kept": top_exp.get("n_kept"),
            "keep_rate": top_exp.get("keep_rate"),
            "winner_capture": None,
            "winner_precision": top_exp.get("winner_rate"),
            "winner_rate": top_exp.get("winner_rate"),
            "stop_rate": top_exp.get("stop_rate"),
            "np_rate": top_exp.get("np_rate"),
            "mean_pnl": top_exp.get("mean_pnl"),
            "total_pnl": top_exp.get("total_pnl"),
            "pf": top_exp.get("pf"),
            "expectancy_score": top_exp.get("expectancy_score"),
            "source": "top20_expectancy_rank_1",
        }
    else:
        recommended_from_combo = None

    highlight_names = [
        "board_imb",
        "f_imb",
        "board_imb_pct",
        "f_imb_pct",
        "board_spread",
        "f_spread",
        "board_age",
        "f_vwap",
        "px_vwap_dev",
        "f_atr",
        "px_atr",
        "f_tv",
        "f_near_high",
        "f_chase",
        "f_rise5",
        "f_rise10",
        "f_mom",
        "f_np_imb_chg_60",
        "w_60s_imb_chg",
        "w_60s_ret",
        "f_cap_usage",
        "mkt_minutes_from_open",
    ]
    avail_map = {a["feature"]: a for a in avail}
    avail_highlights = []
    for hn in highlight_names:
        if hn in avail_map:
            avail_highlights.append(avail_map[hn])
        else:
            # may be filtered out by min_non_null
            n_ok = sum(1 for r in feat_rows if r.get(hn) is not None)
            avail_highlights.append(
                {"feature": hn, "n_available": n_ok, "n_total": len(feat_rows), "fill_rate": round(n_ok / max(len(feat_rows), 1), 4)}
            )

    # Drop huge score list from nested duplication in json (keep summary + sample)
    rise_scores = rules.get("winner_rise_scores") or []
    rules_json = {k: v for k, v in rules.items() if k != "winner_rise_scores"}
    rules_json["winner_rise_score_summary"] = {
        "n": len(rise_scores),
        "mean": round(sum(rise_scores) / len(rise_scores), 6) if rise_scores else None,
        "p50": sorted(rise_scores)[len(rise_scores) // 2] if rise_scores else None,
    }

    recommended = recommended_from_combo or rules.get("recommended_filter") or {}
    # Build comparable baseline metrics for PBv2 comparison block
    base_all = rules.get("baseline_pbv2") or {}
    if recommended_from_combo:
        comparison = _pbv2_comparison(
            base_all,
            {
                "keep_rate": recommended.get("keep_rate"),
                "winner_capture": recommended.get("winner_rate"),  # not capture; kept for schema
                "winner_precision": recommended.get("winner_rate"),
                "stop_rate": recommended.get("stop_rate"),
                "np_rate": recommended.get("np_rate"),
                "mean_pnl": recommended.get("mean_pnl"),
                "total_pnl": recommended.get("total_pnl"),
                "rule": recommended.get("rule"),
                "type": recommended.get("type"),
            },
        )
    else:
        comparison = _pbv2_comparison(base_all, recommended)

    # Daily breakdown under best rule
    daily = []
    by_day: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(labeled):
        by_day[r.trade.day].append(i)

    # Recompute keep mask from recommended rule if possible
    keep_mask = None
    if recommended.get("type") == "score" and rise_scores:
        thr_s = float(recommended.get("threshold") or 0)
        keep_mask = [s >= thr_s for s in rise_scores]
    elif recommended.get("type") == "single" and recommended.get("feature"):
        name = recommended["feature"]
        op = recommended.get("op")
        thr_v = float(recommended.get("threshold") or 0)
        col = [feat_rows[i].get(name) for i in range(len(feat_rows))]
        if op == ">=":
            keep_mask = [v is not None and float(v) >= thr_v for v in col]
        else:
            keep_mask = [v is not None and float(v) <= thr_v for v in col]

    if keep_mask is not None:
        for day, idxs in sorted(by_day.items()):
            sub = [labeled[i] for i in idxs]
            kept_idx = [i for i in idxs if keep_mask[i]]
            n_w = sum(1 for r in sub if r.is_winner)
            daily.append(
                {
                    "day": day,
                    "n": len(sub),
                    "n_kept": len(kept_idx),
                    "winner_total": n_w,
                    "winner_kept": sum(1 for i in kept_idx if labeled[i].is_winner),
                    "stop_kept": sum(1 for i in kept_idx if labeled[i].cohort == "STOP"),
                    "np_kept": sum(1 for i in kept_idx if labeled[i].cohort == "NoProgress"),
                    "pnl_all": round(sum(r.pnl_yen for r in sub), 2),
                    "pnl_kept": round(sum(labeled[i].pnl_yen for i in kept_idx), 2),
                }
            )

    exit_dist = Counter(r.trade.exit_reason for r in labeled)

    payload: dict[str, Any] = {
        "phase": "WinnerFeatureFilter",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "WINNER_FEATURE_FILTER_READY",
        "purpose": "Select profitable ENTRY among PBv2 accepts (not add REJECT rules)",
        "pipeline": ["PBv2", "WinnerFeatureFilter", "ENTRY"],
        "n_days": len({r.trade.day for r in labeled}),
        "days": sorted({r.trade.day for r in labeled}),
        "n_trades": len(labeled),
        "n_partial_excluded": len(partial),
        "winner_threshold_yen": round(float(thr), 4),
        "winner_quantile": 0.80,
        "cohort_counts": counts,
        "exit_reason_dist": dict(exit_dist),
        "feature_meta": feat_meta,
        "feature_availability": avail,
        "feature_availability_highlights": avail_highlights,
        "imputation_meta": imput_meta,
        "board_subset_importance": board_subset_importance,
        "winner_rate_combo_pack": combo_pack,
        "winner_rate_combinations_top20": winner_combos,
        "expectancy_ranked_filters": expectancy_rank,
        "feature_importance": {
            "universe": "all_formal_rows_after_median_imputation",
            "n_rows": len(labeled),
            "lgbm_meta": imp.get("lgbm_meta"),
            "merged": imp.get("merged"),
            "lgbm_top": (imp.get("lgbm") or [])[:40],
            "shap_top": (imp.get("shap") or [])[:40],
            "permutation_top": (imp.get("permutation") or [])[:40],
            "information_gain_top": (imp.get("information_gain") or [])[:40],
            "mutual_info_top": (imp.get("mutual_info") or [])[:40],
        },
        "cohort_signatures": {
            k: {
                "n": v.get("n"),
                "common_signature": v.get("common_signature"),
                "top_discriminators": v.get("top_discriminators"),
            }
            for k, v in cohort_sig.items()
        },
        "rule_search": rules_json,
        "recommended_filter": recommended,
        "recommended_score_filter": rules.get("recommended_score_filter")
        or rules.get("best_score_filter")
        or ((rules.get("score_meta") or {}).get("best")),
        "winner_rise_score_spec": (rules.get("score_meta") or {}).get("components"),
        "pbv2_comparison": comparison,
        "daily": daily,
        "coverage": coverage,
        "safety": {"submit": 0, "cancel": 0, "live_order": 0, "paper_only": True, "observe_only": True},
    }

    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_render_md(payload), encoding="utf-8")

    # audit.xlsx sheets
    catalog = _feat_catalog(avail)
    shap_sheet = list((imp.get("shap") or [])[:100])

    winner_vs_stop = []
    w_map = {r["feature"]: r for r in (cohort_sig.get("Winner", {}).get("top_discriminators") or [])}
    s_map = {r["feature"]: r for r in (cohort_sig.get("STOP", {}).get("top_discriminators") or [])}
    for name in sorted(set(w_map) | set(s_map)):
        wr, sr = w_map.get(name), s_map.get(name)
        winner_vs_stop.append(
            {
                "feature": name,
                "winner_d": None if wr is None else wr.get("cohens_d"),
                "stop_d": None if sr is None else sr.get("cohens_d"),
                "winner_mean": None if wr is None else wr.get("cohort_mean"),
                "stop_mean": None if sr is None else sr.get("cohort_mean"),
            }
        )

    rule_rows = []
    for block in ("single_rules", "and_or_rules", "score_candidates"):
        for r in (rules.get(block) or []):
            rule_rows.append({**r, "block": block})

    imp_rank_with_n = []
    for r in imp.get("merged") or []:
        imp_rank_with_n.append({**r, "n_available": avail_map.get(r["feature"], {}).get("n_available")})

    sheets = {
        "feature_catalog": catalog,
        "feature_availability": avail,
        "imputation_meta": [imput_meta],
        "importance_ranking": imp_rank_with_n,
        "board_subset_importance": board_subset_importance.get("merged_top") or [],
        "winner_rate_combos": [
            {
                "rank_by_winner_rate": r.get("rank_by_winner_rate"),
                "type": r.get("type"),
                "threshold_text": r.get("threshold_text"),
                "rule": r.get("rule"),
                "n_kept": r.get("n_kept"),
                "winner_rate": r.get("winner_rate"),
                "mean_pnl": r.get("mean_pnl"),
                "total_pnl": r.get("total_pnl"),
                "pf": r.get("pf"),
                "stop_rate": r.get("stop_rate"),
                "np_rate": r.get("np_rate"),
                "expectancy_score": r.get("expectancy_score"),
            }
            for r in winner_combos
        ],
        "expectancy_ranked": [
            {
                "rank_by_expectancy": r.get("rank_by_expectancy"),
                "adopt_candidate": r.get("adopt_candidate"),
                "threshold_text": r.get("threshold_text"),
                "rule": r.get("rule"),
                "n_kept": r.get("n_kept"),
                "winner_rate": r.get("winner_rate"),
                "mean_pnl": r.get("mean_pnl"),
                "pf": r.get("pf"),
                "stop_rate": r.get("stop_rate"),
                "np_rate": r.get("np_rate"),
                "expectancy_score": r.get("expectancy_score"),
            }
            for r in expectancy_rank
        ],
        "threshold_dictionary": [
            {
                "feature": feat,
                "display": t.get("display"),
                "op": t.get("op"),
                "threshold": t.get("threshold"),
                "unit": t.get("unit"),
                "human": t.get("human"),
                "direction_label": t.get("direction_label"),
            }
            for feat, items in (combo_pack.get("threshold_dictionary_from_top20") or {}).items()
            for t in items
        ],
        "lgbm_importance": imp.get("lgbm") or [],
        "permutation": imp.get("permutation") or [],
        "shap": shap_sheet,
        "information_gain": imp.get("information_gain") or [],
        "mutual_info": imp.get("mutual_info") or [],
        "winner_signature": cohort_sig.get("Winner", {}).get("common_signature") or [],
        "stop_signature": cohort_sig.get("STOP", {}).get("common_signature") or [],
        "noprogress_signature": cohort_sig.get("NoProgress", {}).get("common_signature") or [],
        "winner_vs_stop": winner_vs_stop,
        "candidate_rules": rule_rows,
        "pbv2_comparison": [comparison.get("baseline_pbv2") or {}, comparison.get("winner_feature_filter") or {}],
        "daily": daily,
        "cohort_counts": [counts],
        "lgbm_meta": [imp.get("lgbm_meta") or {}],
    }
    write_xlsx(out_dir / "audit.xlsx", sheets)

    # Ensure only the 3 artifacts remain in out_dir (no extras from this run)
    allowed = {"report.md", "report.json", "audit.xlsx"}
    for p in out_dir.iterdir():
        if p.is_file() and p.name not in allowed:
            # do not delete unrelated; only skip creating extras
            pass

    _ = model  # kept for potential debug; not serialized
    return payload


if __name__ == "__main__":
    run_pipeline()
