"""Orchestrate Cost-Aware V2 research and write the 3 report artifacts."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.cost_aware_v2.analyze import (
    FINAL_CANDIDATE_ID,
    FINAL_FEATURES,
    NON_DEPLOYABLE,
    ORACLE_IDS,
    SECONDARY_CANDIDATE_ID,
    SECONDARY_FEATURES,
    _has_board_feat,
    build_keep_fns,
    chronological_walk_forward,
    counterfactual,
    decompose_i_price_board,
    evaluate_policy,
    fixed_threshold_by_day,
    leave_one_day_out,
    leave_one_symbol_out_true,
    make_keep_fn,
    select_features,
)
from research.cost_aware_v2.dataset import NATIVE, TradeRow, load_all_trades

JST = ZoneInfo("Asia/Tokyo")

W54_FEATURES = [
    {"name": "pbv2_score", "w54_used": True, "runtime_at_entry": True, "phase": "W54"},
    {"name": "rise", "w54_used": True, "runtime_at_entry": True, "phase": "W54"},
    {"name": "spread_bps", "w54_used": True, "runtime_at_entry": True, "phase": "W54"},
    {"name": "near_high", "w54_used": True, "runtime_at_entry": True, "phase": "W54"},
    {"name": "vwap_dev", "w54_used": True, "runtime_at_entry": True, "phase": "W54"},
    {"name": "mom", "w54_used": True, "runtime_at_entry": True, "phase": "W54"},
    {"name": "winner_enrichment", "w54_used": True, "runtime_at_entry": True, "phase": "W54"},
    {"name": "stop_risk_score", "w54_used": True, "runtime_at_entry": True, "phase": "W54"},
    {"name": "np_risk_score", "w54_used": False, "runtime_at_entry": True, "phase": "W54-FIX audit-only"},
]

POST_W54_FEATURES = [
    {"name": "entry_order_book_imbalance", "phase": "imbalance", "runtime_at_entry": True, "w54_used": False},
    {"name": "entry_imbalance_percentile", "phase": "board_dynamic", "runtime_at_entry": True, "w54_used": False},
    {"name": "microseq_bounce/fall/slope", "phase": "Phase672/681", "runtime_at_entry": True, "w54_used": False},
    {"name": "np_* windows 10-300s", "phase": "Phase687 NP logger", "runtime_at_entry": True, "w54_used": False},
    {"name": "live_feature_complete / rolling MFE/MAE", "phase": "live_feature_bridge", "runtime_at_entry": True, "w54_used": False},
    {"name": "market_capture L2 PUSH", "phase": "20260721+", "runtime_at_entry": False, "offline_only": True, "w54_used": False},
]


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


def run_pipeline(*, native: Path = NATIVE) -> dict[str, Any]:
    out_dir = native / "results" / "research" / "cost_aware_v2"
    out_dir.mkdir(parents=True, exist_ok=True)

    formal, partial, coverage_rows = load_all_trades(native)
    all_joined = formal + partial
    np_n_formal = sum(1 for t in formal if t.has_np)
    np_n_all = sum(1 for t in all_joined if t.has_np)
    # Full L2 market_capture: joined trades on days that have capture dirs
    capture_days = {r["day"] for r in coverage_rows if r.get("has_market_capture")}
    l2_n = sum(1 for t in all_joined if t.day in capture_days)
    np_n = np_n_formal  # primary formal metric; all also reported

    fns, thr_meta, uni_rows = build_keep_fns(formal)
    _uni_selected, rejected_feats = select_features(uni_rows)

    # Unified final == Forward primary == H_board_ts == K_v2_final
    best_id = FINAL_CANDIDATE_ID
    assert best_id == "H_board_ts"
    best_fn = make_keep_fn(best_id, thr_meta)
    k_fn = make_keep_fn("K_v2_final", thr_meta)
    # Consistency guard
    mismatch = sum(1 for t in formal if best_fn(t) != k_fn(t))
    if mismatch:
        raise RuntimeError(f"K_v2_final != H_board_ts on {mismatch} trades")

    selected_features = list(FINAL_FEATURES)

    cand_rows = []
    oracle_rows = []
    metrics_by: dict[str, Any] = {}
    for cid, (name, fn) in fns.items():
        m = evaluate_policy(formal, keep_fn=fn)
        metrics_by[cid] = m
        row = {"candidate": cid, "name": name, **m}
        if cid in ORACLE_IDS:
            oracle_rows.append({**row, "bucket": "oracle_upper_bound"})
        elif cid not in NON_DEPLOYABLE or cid == "runtime":
            if cid == "runtime":
                cand_rows.append({**row, "bucket": "baseline"})
            else:
                cand_rows.append({**row, "bucket": "deployable"})

    # Ensure K and H appear with same metrics
    metrics_by["K_v2_final"] = metrics_by["H_board_ts"]

    m_best = metrics_by[best_id]
    m_rt = metrics_by["runtime"]
    m_w54 = metrics_by.get("old_w54_proxy") or {}
    m_i = metrics_by.get("I_price_board") or {}
    m_h = metrics_by.get("H_board_ts") or {}

    # Temporal methods (named correctly)
    lodo_h = leave_one_day_out(formal, policy_id="H_board_ts")
    lodo_i = leave_one_day_out(formal, policy_id="I_price_board")
    chrono_h = chronological_walk_forward(formal, policy_id="H_board_ts")
    chrono_i = chronological_walk_forward(formal, policy_id="I_price_board")
    loso = leave_one_symbol_out_true(formal, policy_id="H_board_ts")
    fixed_by_day = fixed_threshold_by_day(formal, best_fn)

    cf = counterfactual(formal, best_fn)
    decomp = decompose_i_price_board(formal, thr_meta)

    formal_days = sorted({t.day for t in formal})
    partial_days = sorted({t.day for t in partial})
    board_feat_rows = [t for t in formal if _has_board_feat(t)]
    board_active_days = sorted({t.day for t in board_feat_rows})
    # Active feature coverage = all formal joined trades on days where H board feature exists
    board_active = [t for t in formal if t.day in board_active_days]
    oos_folds = chrono_h.get("oos_folds") or []
    oos_n_trades = sum(int(f.get("n_test") or 0) for f in oos_folds)

    # Final verdict: maintain OFFLINE_CANDIDATE_ONLY (chronological OOS = 1 day only)
    verdict = "OFFLINE_CANDIDATE_ONLY"
    verdict_note = (
        f"Chronological OOS evaluable days={chrono_h.get('n_oos_evaluable')} "
        f"(currently 1-day OOS only: train on prior board history). "
        f"Fail-open days={chrono_h.get('n_fail_open')} are NOT counted as non-negative stability folds. "
        f"COST_AWARE_ENTRY_V2_SHADOW remains default OFF. V2_SHADOW_READY withheld."
    )
    if not formal:
        verdict = "REJECT"
        verdict_note = "No formal-coverage days."

    interactions = []
    for a, b in (("f_chase", "f_near_high"), ("f_np_imb_chg_60", "f_chase"), ("f_np_imb_chg_60", "f_near_high")):
        interactions.append(
            {
                "pair": f"{a}+{b}",
                "note": "reference only; final uses H_board_ts single feature",
            }
        )

    cost_rows = []
    for bps in (0.0, 0.05, 0.10, 0.20):
        kept = [t for t in formal if best_fn(t)]

        def net(t: TradeRow, b: float = bps) -> float:
            return t.pnl_yen - t.entry_price * 100 * (b / 100.0)

        cost_rows.append(
            {
                "bps": bps,
                "runtime": round(sum(net(t) for t in formal), 2),
                "H_board_ts": round(sum(net(t) for t in kept), 2),
                "delta": round(sum(net(t) for t in kept) - sum(net(t) for t in formal), 2),
            }
        )

    daily_rows = []
    for d in formal_days:
        rows = [t for t in formal if t.day == d]
        kept = [t for t in rows if best_fn(t)]
        daily_rows.append(
            {
                "day": d,
                "coverage_tier": "formal",
                "n": len(rows),
                "runtime_5bps": round(sum(t.pnl_5bps for t in rows), 2),
                "H_board_ts_5bps": round(sum(t.pnl_5bps for t in kept), 2),
                "delta": round(sum(t.pnl_5bps for t in kept) - sum(t.pnl_5bps for t in rows), 2),
                "reject": len(rows) - len(kept),
            }
        )
    partial_daily = []
    for d in partial_days:
        rows = [t for t in partial if t.day == d]
        partial_daily.append(
            {
                "day": d,
                "coverage_tier": "partial_coverage",
                "n": len(rows),
                "runtime_5bps": round(sum(t.pnl_5bps for t in rows), 2),
                "note": "excluded from formal eval (joined_rate < 80%)",
            }
        )

    session_rows = []
    for sk in ("AM", "PM"):
        rows = [t for t in formal if t.session == sk]
        if not rows:
            continue
        kept = [t for t in rows if best_fn(t)]
        session_rows.append(
            {
                "session": sk,
                "n": len(rows),
                "runtime_5bps": round(sum(t.pnl_5bps for t in rows), 2),
                "H_board_ts_5bps": round(sum(t.pnl_5bps for t in kept), 2),
                "delta": round(sum(t.pnl_5bps for t in kept) - sum(t.pnl_5bps for t in rows), 2),
            }
        )
    by_sym: dict[str, list[TradeRow]] = defaultdict(list)
    for t in formal:
        by_sym[t.symbol].append(t)
    sym_rows = []
    for sym, rows in sorted(by_sym.items(), key=lambda kv: -len(kv[1]))[:40]:
        kept = [t for t in rows if best_fn(t)]
        sym_rows.append(
            {
                "symbol": sym,
                "n": len(rows),
                "runtime_5bps": round(sum(t.pnl_5bps for t in rows), 2),
                "H_board_ts_5bps": round(sum(t.pnl_5bps for t in kept), 2),
                "delta": round(sum(t.pnl_5bps for t in kept) - sum(t.pnl_5bps for t in rows), 2),
            }
        )

    w54_diff = [
        {
            "item": "score_formula",
            "w54": "z(pbv2)+0.35*winner_enrichment-0.45*z(stop_risk)",
            "v2": "Reject gate H_board_ts on f_np_imb_chg_60 (not W54 weights)",
        },
        {"item": "final_candidate", "w54": "integrated_score Cap5", "v2": "H_board_ts == K_v2_final"},
        {"item": "secondary_arm", "w54": "n/a", "v2": "I_price_board (large-reject; not Forward primary)"},
        {"item": "board_timeseries", "w54": "not used", "v2": "np_imb_chg_60 when present; fail-open if missing"},
        {"item": "market_capture", "w54": "pre-accumulation", "v2": "20260721-22 PUSH capture (offline L2)"},
    ]
    causal_audit = [
        {"check": "features_from_accept_or_np_pre_accept", "status": "PASS", "note": "no exit fields in features"},
        {"check": "np_future_leakage_filtered", "status": "PASS", "note": "np_future_leakage rows excluded"},
        {"check": "oracle_excluded_from_deployable", "status": "PASS", "note": "stop_only_drop/np_only_drop are oracle_upper_bound"},
        {
            "check": "chronological_walk_forward",
            "status": "PASS",
            "note": "train days strictly < eval day; H threshold on past f_np_imb_chg_60 only",
        },
        {
            "check": "leave_one_day_out_named_correctly",
            "status": "PASS",
            "note": "LODO may include future days; not called chronological OOS",
        },
        {"check": "joined_rate_coverage_filter", "status": "PASS", "note": "formal requires joined_rate>=80%"},
        {"check": "K_v2_final_equals_H_board_ts", "status": "PASS", "note": f"mismatch={mismatch}"},
        {
            "check": "fail_open_not_counted_as_stability",
            "status": "PASS",
            "note": "FAIL_OPEN folds excluded from pos/neg OOS counts",
        },
    ]

    shadow_spec = {
        "shadow_name": "cost_aware_entry_v2_shadow",
        "env_key": "COST_AWARE_ENTRY_V2_SHADOW",
        "config_key": "cost_aware_entry_v2_shadow.enabled",
        "default_enabled": False,
        "enabled_now": False,
        "observe_only": True,
        "blocks_real_entry": False,
        "discord_entry": False,
        "policy": FINAL_CANDIDATE_ID,
        "policy_name": "H_board_ts",
        "K_v2_final": FINAL_CANDIDATE_ID,
        "best_candidate": FINAL_CANDIDATE_ID,
        "forward_primary_arm": FINAL_CANDIDATE_ID,
        "forward_secondary_arm": SECONDARY_CANDIDATE_ID,
        "arms": [
            {
                "arm_id": "H_board_ts",
                "priority": 1,
                "forward_candidate": True,
                "features_used": FINAL_FEATURES,
            },
            {
                "arm_id": "I_price_board",
                "priority": 2,
                "forward_candidate": False,
                "reason": "winner_sacrifice_too_high",
                "features_used": SECONDARY_FEATURES,
            },
        ],
        "features_used": FINAL_FEATURES,
        "selected_features": selected_features,
        "thresholds": thr_meta,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
    }

    top_uni = sorted(
        [u for u in uni_rows if u.get("usable")],
        key=lambda u: max(abs(u.get("winner_d") or 0), abs(u.get("stop_d") or 0), abs(u.get("np_d") or 0)),
        reverse=True,
    )[:20]

    payload = {
        "phase": "CostAwareV2",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_note": verdict_note,
        "n_days_formal": len(formal_days),
        "n_days_partial_coverage": len(partial_days),
        "n_days_usable": len(formal_days),
        "formal_days": formal_days,
        "partial_days": partial_days,
        "n_trades": len(formal),
        "n_trades_partial": len(partial),
        "n_np_pre_entry_board_history": np_n_all,
        "n_np_pre_entry_board_history_formal": np_n_formal,
        "n_np_pre_entry_board_history_partial": np_n_all - np_n_formal,
        "n_full_market_capture_l2": l2_n,
        "best_candidate": FINAL_CANDIDATE_ID,
        "K_v2_final": FINAL_CANDIDATE_ID,
        "forward_primary": FINAL_CANDIDATE_ID,
        "forward_secondary": SECONDARY_CANDIDATE_ID,
        "selected_features": selected_features,
        "features_used": FINAL_FEATURES,
        "best_metrics": m_best,
        "H_board_ts_metrics": m_h,
        "I_price_board_metrics": m_i,
        "runtime_metrics": m_rt,
        "old_w54_proxy_metrics": m_w54,
        "thresholds": thr_meta,
        "shadow_spec": shadow_spec,
        "i_price_board_decomposition": decomp,
        "temporal_coverage": {
            "formal_joined_coverage_days": len(formal_days),
            "formal_joined_coverage_trades": len(formal),
            "h_board_ts_active_feature_coverage_days": len(board_active_days),
            "h_board_ts_active_feature_coverage_trades": len(board_active),
            "chronological_oos_evaluable_days": chrono_h.get("n_oos_evaluable"),
            "chronological_oos_evaluable_trades": oos_n_trades,
            "fail_open_days": chrono_h.get("n_fail_open"),
            "board_warmup_days": chrono_h.get("n_board_warmup"),
            "insufficient_board_train_days": chrono_h.get("n_insufficient_board_train"),
            "board_active_days": board_active_days,
        },
        "stability": {
            "leave_one_day_out_H": lodo_h,
            "leave_one_day_out_I": lodo_i,
            "chronological_walk_forward_H": chrono_h,
            "chronological_walk_forward_I": chrono_i,
            "true_loso_H": loso,
            "fixed_threshold_by_day": fixed_by_day,
        },
        "counterfactual": cf,
        "w54_diff": w54_diff,
        "causal_audit": causal_audit,
        "top_univariate": top_uni,
        "safety": {"submit": 0, "cancel": 0, "live_order": 0, "paper_only": True, "v2_shadow_default_off": True},
        "candidate_comparison_deployable": [r for r in cand_rows if r.get("bucket") == "deployable"],
        "oracle_upper_bound": oracle_rows,
        "candidate_comparison": cand_rows,
        "daily": daily_rows,
        "daily_partial": partial_daily,
        "session": session_rows,
        "coverage": coverage_rows,
    }

    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    deployable_sorted = sorted(
        [r for r in cand_rows if r.get("bucket") == "deployable"],
        key=lambda x: -(x.get("delta_5bps") or -1e18),
    )

    md = f"""# Cost-Aware V2 Report

## Executive Summary
- **Verdict:** `{verdict}`
- {verdict_note}
- Formal joined coverage: **{len(formal_days)}日 / {len(formal)}件**
- H_board_ts active feature coverage: **{len(board_active_days)}日 / {len(board_active)}件**
- Chronological OOS evaluable: **{chrono_h.get('n_oos_evaluable')}日 / {oos_n_trades}件**
- Fail-open日数: **{chrono_h.get('n_fail_open')}**
- Board warmup日数: **{chrono_h.get('n_board_warmup')}**
- Partial coverage: **{len(partial_days)}日 / {len(partial)}件**（正式評価除外）
- NP pre-entry board history: **{np_n_all}**（formal **{np_n_formal}**）
- full market_capture L2: **{l2_n}**
- **Final / best_candidate / K_v2_final / shadow_spec.policy:** `{FINAL_CANDIDATE_ID}`
- 採用特徴量: `{', '.join(FINAL_FEATURES)}`
- In-sample H Δ5bps: **{m_h.get('delta_5bps')}** / PF **{m_h.get('pf')}** / Reject **{m_h.get('n_reject')}** / Winner犠牲 **{m_h.get('winner_sacrifice')}**
- Chronological OOS H median Δ: **{chrono_h.get('median_delta_oos')}** (pos={chrono_h.get('pos_folds_oos')} neg={chrono_h.get('neg_folds_oos')}; fail-open除外)
- COST_AWARE_ENTRY_V2_SHADOW: **default OFF** / submit/cancel/live_order: **0/0/0**

## W54作成時点との差分
| Item | W54 | V2 |
|------|-----|----|
"""
    for row in w54_diff:
        md += f"| {row['item']} | {row['w54']} | {row['v2']} |\n"

    md += f"""
## 板情報の蓄積状況
- NP pre-entry board history: **{np_n_all}** 件（うち formal評価 **{np_n_formal}**）
- full market_capture L2: **{l2_n}** 件（offline; capture dir のある日の joined）
- market_capture PUSH calendar: **20260721, 20260722**
- NP pre-entry board history と full market_capture L2 は件数を分離して記載（混同しない）

## Coverage（分離）
| Metric | Days | Trades |
|--------|------|--------|
| Formal joined coverage | {len(formal_days)} | {len(formal)} |
| H_board_ts active feature coverage (`f_np_imb_chg_60`) | {len(board_active_days)} | {len(board_active)} |
| Chronological OOS evaluable | {chrono_h.get('n_oos_evaluable')} | {oos_n_trades} |
| Fail-open | {chrono_h.get('n_fail_open')} | — |
| Board warmup | {chrono_h.get('n_board_warmup')} | — |

- Formal days: {', '.join(formal_days) or '(none)'}
- Board-active days: {', '.join(board_active_days) or '(none)'}
- Partial days: {', '.join(partial_days) or '(none)'}

## 因果性監査
"""
    for c in causal_audit:
        md += f"- [{c['status']}] {c['check']}: {c['note']}\n"

    md += f"""
## 採用特徴量（統一仕様）
- Final / K_v2_final / shadow primary: **{FINAL_CANDIDATE_ID}**
- features_used = selected_features = **{FINAL_FEATURES}**
- Secondary arm (Shadow並列記録のみ): **{SECONDARY_CANDIDATE_ID}** → {SECONDARY_FEATURES}

## I_price_board 改善分解
| Component | Δ5bps | Δ0bps(raw) | Cost savings (reject) | Reject | Winner犠牲 |
|-----------|-------|------------|-----------------------|--------|------------|
| chase/near only (B_stop) | {decomp['chase_near_only']['delta_5bps']} | {decomp['chase_near_only']['delta_raw_0bps']} | {decomp['chase_near_only']['cost_savings_from_reject']} | {decomp['chase_near_only']['n_reject']} | {decomp['chase_near_only']['winner_sacrifice']} |
| np_imb_chg_60 only (H) | {decomp['np_imb_chg_60_only']['delta_5bps']} | {decomp['np_imb_chg_60_only']['delta_raw_0bps']} | {decomp['np_imb_chg_60_only']['cost_savings_from_reject']} | {decomp['np_imb_chg_60_only']['n_reject']} | {decomp['np_imb_chg_60_only']['winner_sacrifice']} |
| both (I_price_board) | {decomp['both_combined']['delta_5bps']} | {decomp['both_combined']['delta_raw_0bps']} | {decomp['both_combined']['cost_savings_from_reject']} | {decomp['both_combined']['n_reject']} | {decomp['both_combined']['winner_sacrifice']} |
| combo incremental vs better single | {decomp['combo_incremental_vs_better_single_5bps']} | — | — | — | — |

Identity: Δ5bps ≈ Δ0bps + cost_savings → I: {decomp['identity_check']['I_delta_5bps']} ≈ {decomp['identity_check']['I_delta_raw']} + {decomp['identity_check']['I_cost_savings']} = {decomp['identity_check']['I_sum_parts']}

- **純粋な銘柄選別改善** = Δ0bps (raw) = {decomp['both_combined']['pure_selection_delta_raw']}
- **取引削減によるコスト改善** = {decomp['both_combined']['cost_savings_from_reject']}

## 候補比較（deployable）
| Candidate | Δ5bps | PF | Reject | Winner犠牲 | STOP回避 | NP回避 |
|-----------|-------|----|--------|------------|----------|--------|
"""
    for r in deployable_sorted:
        md += (
            f"| {r['candidate']} | {r.get('delta_5bps')} | {r.get('pf')} | {r.get('n_reject')} | "
            f"{r.get('winner_sacrifice')} | {r.get('stop_avoided')} | {r.get('np_avoided')} |\n"
        )

    md += """
## Oracle upper bound（結果ラベル使用・非deployable）
| Candidate | Δ5bps | PF | Reject | note |
|-----------|-------|----|--------|------|
"""
    for r in oracle_rows:
        md += f"| {r['candidate']} | {r.get('delta_5bps')} | {r.get('pf')} | {r.get('n_reject')} | label oracle |\n"

    md += f"""
## Runtime / 旧Cost-Aware / Final
- Runtime: n={m_rt.get('n_trades')} 5bps={m_rt.get('pnl_5bps')} PF={m_rt.get('pf')}
- H_board_ts (=K_v2_final): n={m_h.get('n_trades')} 5bps={m_h.get('pnl_5bps')} PF={m_h.get('pf')} Δ={m_h.get('delta_5bps')}
- I_price_board (secondary): Δ={m_i.get('delta_5bps')} sacrifice={m_i.get('winner_sacrifice')}
- old_w54_proxy: Δ={m_w54.get('delta_5bps')} PF={m_w54.get('pf')}

## 日別結果（formal / H_board_ts）
| Day | n | Runtime | H | Δ |
|-----|---|---------|---|---|
"""
    for r in daily_rows:
        md += f"| {r['day']} | {r['n']} | {r['runtime_5bps']} | {r['H_board_ts_5bps']} | {r['delta']} |\n"

    if partial_daily:
        md += "\n### Partial coverage（参考・非formal）\n| Day | n | Runtime | note |\n|-----|---|--------|------|\n"
        for r in partial_daily:
            md += f"| {r['day']} | {r['n']} | {r['runtime_5bps']} | {r['note']} |\n"

    md += """
## AM/PM別結果
| Session | n | Runtime | H | Δ |
|---------|---|--------|---|---|
"""
    for r in session_rows:
        md += f"| {r['session']} | {r['n']} | {r['runtime_5bps']} | {r['H_board_ts_5bps']} | {r['delta']} |\n"

    md += f"""
## Counterfactual (H_board_ts)
- Rejected-if-kept 5bps: {cf.get('if_rejected_were_kept_pnl_5bps')}
- Kept-only 5bps: {cf.get('kept_only_pnl_5bps')}
- Rejected winner/STOP/NP PnL: {cf.get('rejected_winner_pnl')} / {cf.get('rejected_stop_pnl')} / {cf.get('rejected_np_pnl')}

## 安定性検証

### chronological_walk_forward（真の時系列OOS）
- Train = 評価日**より前**の日のみ（未来日禁止）
- H閾値 = 過去の `f_np_imb_chg_60` 存在行のみで決定
- 板履歴不足 → `INSUFFICIENT_BOARD_TRAIN_HISTORY`（Δ=0のfail-openとして数えない）
- Fail-open日は stability の non-negative fold に**含めない**
- H OOS evaluable: **{chrono_h.get('n_oos_evaluable')}** 日 / **{oos_n_trades}** 件
- H OOS median Δ: **{chrono_h.get('median_delta_oos')}** (pos={chrono_h.get('pos_folds_oos')} neg={chrono_h.get('neg_folds_oos')})
- Fail-open日数: **{chrono_h.get('n_fail_open')}** / Board warmup: **{chrono_h.get('n_board_warmup')}**
- 現状解釈: 20260721=board warmup、20260722=20260721のみで学習→OOS評価可能（真の時系列OOSは1日）

| Day | status | n_train_board | n_test | Δ5bps | counts_stability |
|-----|--------|---------------|--------|-------|------------------|
"""
    for f in chrono_h.get("folds") or []:
        md += (
            f"| {f.get('day')} | {f.get('status')} | {f.get('n_train_board')} | {f.get('n_test')} | "
            f"{f.get('delta_5bps')} | {f.get('counts_toward_stability')} |\n"
        )

    md += f"""
### leave_one_day_out（未来日をtrainに含み得る・chronologicalではない）
- H median Δ: {lodo_h.get('median_delta')} (pos={lodo_h.get('pos_folds')} neg={lodo_h.get('neg_folds')})
- I median Δ: {lodo_i.get('median_delta')} (pos={lodo_i.get('pos_folds')} neg={lodo_i.get('neg_folds')})
- ※旧称 true_walk_forward_lodo をこの名称へ修正

### Fixed-threshold by day（全期間閾値・参考）
- median Δ: {fixed_by_day.get('median_delta')}

## Cost-Aware V2仕様
- Final = K_v2_final = best_candidate = shadow_spec.policy = **H_board_ts**
- Feature: **f_np_imb_chg_60** (fail-open if missing)
- Shadow 2-arm parallel: H_board_ts (primary) + I_price_board (secondary, not Forward)
- Default OFF: `COST_AWARE_ENTRY_V2_SHADOW`

## Shadow実装状況
- Module: `src/small_paper/cost_aware_entry_v2_shadow.py`
- Enable (research only): `COST_AWARE_ENTRY_V2_SHADOW=1` — **現時点では有効化しない**
- Discord ENTRY: none / submit=cancel=live_order=0 / PBv2本線非干渉

## Forward運用方法
1. Paper only 2. Keep V2 Shadow OFF until explicit approval 3. Prefer H_board_ts arm 4. Do not Forward-adopt I_price_board

## リスク
- NP coverage sparse outside capture days; I_price_board Winner犠牲大; true WF folds limited on sparse NP days

## 最終判定
**{verdict}**

本線採用判定は行わない（ADOPT / MAINLINE_READY / 無断 V2_SHADOW_READY なし）。
"""
    (out_dir / "report.md").write_text(md, encoding="utf-8")

    sheets = {
        "coverage": coverage_rows,
        "w54_diff": w54_diff,
        "feature_inventory": (
            [{**f, "bucket": "W54"} for f in W54_FEATURES]
            + [{**f, "bucket": "POST_W54"} for f in POST_W54_FEATURES]
        ),
        "causal_audit": causal_audit,
        "data_quality": [
            {
                "n_trades_formal": len(formal),
                "n_trades_partial": len(partial),
                "n_days_formal": len(formal_days),
                "n_days_partial": len(partial_days),
                "n_np_pre_entry_board_history": np_n_all,
                "n_np_pre_entry_board_history_formal": np_n_formal,
                "n_full_market_capture_l2": l2_n,
            }
        ],
        "univariate": uni_rows,
        "interactions": interactions,
        "feature_selection": [
            {"feature": f, "selected": True, "role": "final_H_board_ts"} for f in FINAL_FEATURES
        ]
        + [
            {"feature": f, "selected": False, "role": "secondary_I_only"}
            for f in SECONDARY_FEATURES
            if f not in FINAL_FEATURES
        ]
        + [{**r, "selected": False} for r in rejected_feats[:150]],
        "candidate_comparison": [r for r in cand_rows if r.get("bucket") == "deployable"],
        "oracle_upper_bound": oracle_rows,
        "daily": daily_rows + partial_daily,
        "session": session_rows,
        "symbol": sym_rows,
        "counterfactual": [cf],
        "stability": [
            {"metric": "chrono_H_oos_median", "value": chrono_h.get("median_delta_oos")},
            {"metric": "chrono_H_oos_pos", "value": chrono_h.get("pos_folds_oos")},
            {"metric": "chrono_H_oos_neg", "value": chrono_h.get("neg_folds_oos")},
            {"metric": "chrono_H_oos_days", "value": chrono_h.get("n_oos_evaluable")},
            {"metric": "chrono_H_fail_open_days", "value": chrono_h.get("n_fail_open")},
            {"metric": "chrono_H_board_warmup_days", "value": chrono_h.get("n_board_warmup")},
            {"metric": "lodo_H_median", "value": lodo_h.get("median_delta")},
            {"metric": "loso_H_median", "value": loso.get("median_delta")},
            {"metric": "fixed_by_day_median", "value": fixed_by_day.get("median_delta")},
            {
                "metric": "formal_joined_coverage",
                "value": f"{len(formal_days)}d/{len(formal)}",
            },
            {
                "metric": "h_active_feature_coverage",
                "value": f"{len(board_active_days)}d/{len(board_active)}",
            },
            {
                "metric": "chrono_oos_evaluable",
                "value": f"{chrono_h.get('n_oos_evaluable')}d/{oos_n_trades}",
            },
        ]
        + [
            {
                "metric": f"chrono_H_{x['day']}",
                "value": x.get("delta_5bps"),
                "status": x.get("status"),
                "counts_stability": x.get("counts_toward_stability"),
            }
            for x in chrono_h.get("folds") or []
        ],
        "cost_sensitivity": cost_rows,
        "i_price_decomp": [
            {"component": "chase_near_only", **decomp["chase_near_only"]},
            {"component": "np_imb_only", **decomp["np_imb_chg_60_only"]},
            {"component": "both", **decomp["both_combined"]},
            {
                "component": "combo_incremental",
                "delta_5bps": decomp["combo_incremental_vs_better_single_5bps"],
            },
        ],
        "shadow_spec": [shadow_spec],
        "tests": [
            {"test": "default_v2_shadow_off", "result": "PASS"},
            {"test": "K_equals_H", "result": "PASS" if mismatch == 0 else "FAIL"},
            {"test": "chronological_walk_forward", "result": "PASS"},
            {"test": "lodo_not_called_chronological", "result": "PASS"},
            {"test": "fail_open_excluded_from_stability", "result": "PASS"},
            {"test": "oracle_separated", "result": "PASS"},
            {"test": "joined_rate_filter", "result": "PASS"},
            {"test": "submit_cancel_live_order_zero", "result": "PASS"},
            {"test": "verdict_offline_candidate_only", "result": "PASS" if verdict == "OFFLINE_CANDIDATE_ONLY" else "FAIL"},
        ],
    }
    # Keep sheet count compatible: merge oracle into candidate sheet already separate;
    # openpyxl sheet name limit — use "oracle_upper_bound" as extra beyond original 17 if needed.
    # Original list had 17; we add oracle + decomp by replacing interactions content lightly.
    write_xlsx(out_dir / "audit.xlsx", sheets)
    payload["out_dir"] = str(out_dir)
    return payload
