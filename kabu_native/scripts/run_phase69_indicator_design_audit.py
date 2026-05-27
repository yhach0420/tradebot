"""
Phase 69: Indicator design alignment audit (read-only).
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "kabu_native" / "results" / "small_paper" / "20260520" / "live_full_session_080745"

INDICATORS = [
    "continuation_quality_score",
    "momentum_continuation_score",
    "favorable_continuation",
    "max_continuation_duration",
    "adverse_shrinking",
    "rolling_mfe_pct",
    "rolling_mae_pct",
]

INDICATOR_FIELDS = INDICATORS  # event payload keys match


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)


def _win_rate_by_quartile(
    values: Sequence[float], wins: Sequence[bool]
) -> list[dict[str, Any]]:
    if len(values) != len(wins) or len(values) < 4:
        return []
    paired = sorted(zip(values, wins), key=lambda x: x[0])
    n = len(paired)
    q = n // 4
    buckets: list[dict[str, Any]] = []
    labels = ["Q1_low", "Q2", "Q3", "Q4_high"]
    for i, label in enumerate(labels):
        start = i * q
        end = (i + 1) * q if i < 3 else n
        chunk = paired[start:end]
        if not chunk:
            continue
        w = [1 if w else 0 for _, w in chunk]
        buckets.append(
            {
                "quartile": label,
                "n": len(chunk),
                "value_min": round(chunk[0][0], 6),
                "value_max": round(chunk[-1][0], 6),
                "win_rate": round(sum(w) / len(w), 4),
            }
        )
    return buckets


def _load_accepted_index(events_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    idx: dict[tuple[str, str], dict[str, Any]] = {}
    with events_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            p = ev.get("payload") or ev
            if (ev.get("event_type") or p.get("event_type")) != "accepted":
                continue
            sym = str(p.get("symbol") or "")
            ent = str(p.get("entry_time") or "")
            if sym and ent:
                idx[(sym, ent)] = p
    return idx


def _design_catalog() -> list[dict[str, Any]]:
    return [
        {
            "variable": "continuation_quality_score",
            "intended_phenomenon": "エントリー時点の総合的な継続上昇の強さ（ランキング用）",
            "current_formula": (
                "q=min(1,0.30*mom+0.22*dur_n+0.20*fav+0.14*bear_inv+0.14*stability+0.04*bull); "
                "dur_n=min(1,max_continuation_duration/14); bear_inv=1-bear; "
                "bear=max(0,1-adverse_shrinking); stability=mfe>|mae| branch"
            ),
            "actually_measures": (
                "複数現象の加重合成（短期価格/VWAP/MFEレベル、MFE連動fav、"
                "favorable_streak、adverse回復、MFE/MAE安定性）"
            ),
            "alignment": "partial_mismatch",
            "alignment_ja": "不一致（部分）",
            "mismatch_detail": "単一の「継続品質」ではなく6成分の合成スコア",
            "improvement_candidates": [
                "continuation_quality_decomposed_v2 (成分を正規化してから合成)",
                "entry_edge_score (PnL期待とデコレレートした純粋ランキング)",
            ],
            "design_priority": "medium",
        },
        {
            "variable": "momentum_continuation_score",
            "intended_phenomenon": "価格継続のモメンタム（上昇速度・勢い）",
            "current_formula": (
                "0.40*clamp01((price-p0)/p0/0.008)+0.25*clamp01(0.5+(price-vwap)/vwap/0.004)"
                "+0.35*clamp01((mfe-0.4*mae)/0.35); p0=5tick前"
            ),
            "actually_measures": (
                "短期価格差分レベル + VWAP上位置 + セッション内累積MFE/MAEレベル"
                "（速度ではなく水準・ミックス）"
            ),
            "alignment": "mismatch",
            "alignment_ja": "不一致",
            "mismatch_detail": "名称はmomentumだが35%がMFE水準、25%がVWAP水準",
            "improvement_candidates": [
                "pure_price_momentum: (price-p0)/p0 のみ正規化",
                "price_velocity_score: (price-p0)/p0/dt または log return / tick",
                "pure_vwap_strength: (price-vwap)/vwap のみ",
                "momentum_acceleration: price_mom[t]-price_mom[t-1]",
            ],
            "design_priority": "high",
        },
        {
            "variable": "favorable_continuation",
            "intended_phenomenon": "有利方向への継続（押し目なし・順行）",
            "current_formula": "mfe_linked: min(1, rolling_mfe_pct/0.003) [本セッション設定]",
            "actually_measures": "ref以降の最大有利幅（MFE水準）のみ。tick_hit比率は未使用",
            "alignment": "mismatch",
            "alignment_ja": "不一致",
            "mismatch_detail": "continuationではなく累積MFEの線形スケール",
            "improvement_candidates": [
                "pure_favorable_tick_ratio: recent tick_hit_favorable (lookback=8)",
                "favorable_streak_score: favorable_streak / scale",
                "forward_return_consistency: 直近N tickの正リターン比率",
            ],
            "design_priority": "high",
        },
        {
            "variable": "max_continuation_duration",
            "intended_phenomenon": "モメンタム/継続が持続した時間（tick数）",
            "current_formula": (
                "price>ref OR price>recent_low*1.0001 の連続tick数の最大値 "
                "(favorable_streak)"
            ),
            "actually_measures": "有利方向と判定したtickの連続カウント（モメンタム持続ではない）",
            "alignment": "mismatch",
            "alignment_ja": "不一致",
            "mismatch_detail": "Logic Labのmax_momentum_continuation_durationと別定義",
            "improvement_candidates": [
                "momentum_persistence_ticks: mom>threshold の連続長",
                "pure_uptrend_duration: 連続 higher-close tick数",
                "time_above_vwap_sec: VWAP上滞在時間",
            ],
            "design_priority": "medium",
        },
        {
            "variable": "adverse_shrinking",
            "intended_phenomenon": "不利方向の動きが縮小しているか",
            "current_formula": (
                "mae<=0→1; else 0.5*recovery_from_running_min+0.5*mae_improving; "
                "mae_improving=(last_mae>=peak_mae*0.98)"
            ),
            "actually_measures": (
                "安値からの回復度 + MAEが深くなっていないかの二値混合"
                "（縮小率そのものではない）"
            ),
            "alignment": "partial_mismatch",
            "alignment_ja": "不一致（部分）",
            "mismatch_detail": "adverse幅の変化率|Δmae|を直接測っていない",
            "improvement_candidates": [
                "adverse_shrink_rate: (peak_mae-last_mae)/|peak_mae|",
                "drawdown_recovery_ratio: (price-running_min)/(ref-running_min)",
                "mae_slope_score: Δmae per tick",
            ],
            "design_priority": "low",
        },
        {
            "variable": "rolling_mfe_pct",
            "intended_phenomenon": "参照価格からの最大有利変動率（MFE）",
            "current_formula": "max(0, (running_max-ref)/ref); refは銘柄window先頭 or 300s reset",
            "actually_measures": "ブリッジwindow内の累積MFE水準",
            "alignment": "match",
            "alignment_ja": "一致",
            "mismatch_detail": "",
            "improvement_candidates": [
                "mfe_since_entry_only (ポジション開始後に限定—要別パイプ)",
                "mfe_velocity: Δmfe per tick",
            ],
            "design_priority": "low",
        },
        {
            "variable": "rolling_mae_pct",
            "intended_phenomenon": "参照価格からの最大不利変動率（MAE、負値）",
            "current_formula": "min(0, (running_min-ref)/ref)",
            "actually_measures": "ブリッジwindow内の累積MAE水準",
            "alignment": "match",
            "alignment_ja": "一致",
            "mismatch_detail": "",
            "improvement_candidates": [
                "mae_since_entry_only",
                "mae_depth_score: |mae|/ref 単独",
            ],
            "design_priority": "low",
        },
    ]


def main() -> None:
    trades_path = SESSION / "structural_trades.csv"
    events_path = SESSION / "small_paper_events.jsonl"
    accepted_idx = _load_accepted_index(events_path)

    trade_rows: list[dict[str, Any]] = []
    with trades_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sym = row["symbol"]
            ent = row["entry_time"]
            acc = accepted_idx.get((sym, ent), {})
            pnl = float(row["realized_pnl_pct"])
            win = pnl > 0
            rec = {
                "symbol": sym,
                "entry_time": ent,
                "close_reason": row["close_reason"],
                "realized_pnl_pct": pnl,
                "win": win,
                "continuation_quality_score_entry": float(
                    row.get("continuation_quality_score") or acc.get("continuation_quality_score") or 0
                ),
            }
            for field in INDICATOR_FIELDS:
                v = acc.get(field)
                if v is None and field == "continuation_quality_score":
                    v = rec["continuation_quality_score_entry"]
                rec[f"{field}_at_entry"] = float(v) if v is not None else None
            trade_rows.append(rec)

    all_trades = trade_rows
    structural_only = [
        t
        for t in trade_rows
        if t["close_reason"] not in ("overlap_replaced_review",)
    ]

    correlations: list[dict[str, Any]] = []
    for field in INDICATOR_FIELDS:
        key = f"{field}_at_entry"
        for label, subset in (
            ("all_structural_trades", all_trades),
            ("excl_overlap", structural_only),
        ):
            xs: list[float] = []
            ys: list[float] = []
            wins: list[bool] = []
            for t in subset:
                v = t.get(key)
                if v is None:
                    continue
                xs.append(float(v))
                ys.append(float(t["realized_pnl_pct"]))
                wins.append(bool(t["win"]))
            corr_pnl = _pearson(xs, ys)
            corr_win = _pearson(xs, [1.0 if w else 0.0 for w in wins])
            correlations.append(
                {
                    "variable": field,
                    "subset": label,
                    "n": len(xs),
                    "corr_with_pnl_pct": corr_pnl,
                    "corr_with_win": corr_win,
                    "mean_pnl_when_var_high_q4": None,
                    "win_rate_quartiles": _win_rate_by_quartile(xs, wins),
                }
            )

    mismatch_vars = [d for d in _design_catalog() if d["alignment"] in ("mismatch", "partial_mismatch")]

    priority = {
        "high": [d["variable"] for d in _design_catalog() if d["design_priority"] == "high"],
        "medium": [d["variable"] for d in _design_catalog() if d["design_priority"] == "medium"],
        "low": [d["variable"] for d in _design_catalog() if d["design_priority"] == "low"],
    }

    audit = {
        "phase": 69,
        "session_dir": str(SESSION),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "indicators": _design_catalog(),
        "mismatch_variables": mismatch_vars,
        "all_improvement_candidates": sorted(
            {
                c
                for d in _design_catalog()
                for c in d.get("improvement_candidates", [])
            }
        ),
        "design_priority": priority,
        "correlation_notes": (
            "Entry-time indicator from accepted event joined to structural_trades. "
            "Pearson vs realized_pnl_pct and win(1/0). overlap_replaced_review included in all_structural_trades."
        ),
        "structural_trade_count": len(all_trades),
        "structural_trade_count_excl_overlap": len(structural_only),
    }

    out_json = SESSION / "phase69_indicator_design_audit.json"
    out_csv = SESSION / "phase69_indicator_design_audit.csv"
    out_corr = SESSION / "phase69_indicator_correlation.csv"

    out_json.write_text(
        json.dumps({**audit, "correlations": correlations}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "variable",
                "intended_phenomenon",
                "current_formula",
                "actually_measures",
                "alignment",
                "alignment_ja",
                "mismatch_detail",
                "improvement_candidates",
                "design_priority",
            ],
        )
        w.writeheader()
        for d in _design_catalog():
            w.writerow(
                {
                    "variable": d["variable"],
                    "intended_phenomenon": d["intended_phenomenon"],
                    "current_formula": d["current_formula"],
                    "actually_measures": d["actually_measures"],
                    "alignment": d["alignment"],
                    "alignment_ja": d["alignment_ja"],
                    "mismatch_detail": d["mismatch_detail"],
                    "improvement_candidates": "|".join(d.get("improvement_candidates", [])),
                    "design_priority": d["design_priority"],
                }
            )

    with out_corr.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "variable",
                "subset",
                "n",
                "corr_with_pnl_pct",
                "corr_with_win",
            ],
        )
        w.writeheader()
        for c in correlations:
            w.writerow(
                {
                    "variable": c["variable"],
                    "subset": c["subset"],
                    "n": c["n"],
                    "corr_with_pnl_pct": c["corr_with_pnl_pct"],
                    "corr_with_win": c["corr_with_win"],
                }
            )

    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_corr}")
    for c in correlations:
        if c["subset"] == "excl_overlap":
            print(
                c["variable"],
                "pnl",
                c["corr_with_pnl_pct"],
                "win",
                c["corr_with_win"],
                "n",
                c["n"],
            )


if __name__ == "__main__":
    main()
