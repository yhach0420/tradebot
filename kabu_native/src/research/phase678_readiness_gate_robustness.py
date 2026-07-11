"""Phase678 — Minimal readiness gate robustness review (research only)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase672_pre_entry_microsequence import BIG_WINNER_YEN, _sym_t
from research.phase674_microsequence_candidate_robustness import _rule_c
from research.phase675_recent_early_stop_focus import RECENT_DAYS, _is_early_stop, load_focus_dataset
from research.phase676_opening_coldstart_feature_incomplete import (
    COLD_START_SEC,
    _board_not_following,
    _classify_gap_reason,
    _high_bounce,
    _live_feature_incomplete,
    _low_expectancy,
)
from research.phase677_entry_readiness_gate_audit import _enrich_with_accept, _load_accept_events_full
from research.phase631_profit_source_attribution import _num

PHASE678_VERDICT_SHADOW_CANDIDATE = "READINESS_SHADOW_CANDIDATE"
PHASE678_VERDICT_HOLD = "HOLD"
PHASE678_VERDICT_REJECT = "REJECT"

REPORT_DIR_NAME = "phase678_readiness_gate_robustness"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

SYM_6525 = "6525.T"


def _is_winner(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) > 0


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return float(_num(t.get("pnl_yen_100")) or 0) >= BIG_WINNER_YEN


def _is_stop_hit(t: Mapping[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == "stop_hit"


def _cold_start(t: Mapping[str, Any]) -> bool:
    sec = _num(t.get("push_pre_entry_sec")) or _num(t.get("price_history_sec"))
    return sec is not None and sec < COLD_START_SEC


def _price_history_lt_120(t: Mapping[str, Any]) -> bool:
    sec = _num(t.get("push_pre_entry_sec")) or _num(t.get("price_history_sec"))
    return sec is not None and sec < COLD_START_SEC


def _microsequence_gap(t: Mapping[str, Any]) -> bool:
    return not bool(t.get("microsequence_ok"))


def _phase674_microsequence_c(t: Mapping[str, Any]) -> bool:
    return bool(t.get("microsequence_ok")) and _rule_c(t)


def _phase676_high_bounce_incomplete(t: Mapping[str, Any]) -> bool:
    return _live_feature_incomplete(t) and _high_bounce(t, 0.45)


def _candidate_predicates() -> list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]]:
    return [
        ("I_precision", "primary", lambda t: _live_feature_incomplete(t) and _low_expectancy(t, 2.5)),
        ("H_economics", "primary", lambda t: _live_feature_incomplete(t) and _high_bounce(t, 0.45)),
        ("M_board_not_following", "auxiliary", lambda t: _live_feature_incomplete(t) and _high_bounce(t, 0.45) and _board_not_following(t)),
        ("K_cold_start_high_bounce", "auxiliary", lambda t: _cold_start(t) and _high_bounce(t, 0.45)),
        ("L_cold_start_low_expectancy", "auxiliary", lambda t: _cold_start(t) and _low_expectancy(t, 2.5)),
        ("baseline_lfc_incomplete_solo", "baseline_forbidden", _live_feature_incomplete),
        ("baseline_microsequence_gap", "baseline", _microsequence_gap),
        ("baseline_price_history_lt_120", "baseline", _price_history_lt_120),
        ("baseline_phase674_microsequence_C", "baseline", _phase674_microsequence_c),
        ("baseline_phase676_high_bounce_incomplete", "baseline", _phase676_high_bounce_incomplete),
    ]


def _pools(trades: Sequence[Mapping[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        ("canonical_22", [t for t in trades if t.get("dataset") == "canonical_22"]),
        ("post_flat_band", [t for t in trades if t.get("post_flat_band_entry")]),
        ("20260707", [t for t in trades if str(t.get("day") or "").startswith("2026-07-07")]),
        ("20260708", [t for t in trades if str(t.get("day") or "").startswith("2026-07-08")]),
        ("20260709", [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]),
        ("recent_707_709", [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]),
    ]


def _eval_candidate(
    *,
    candidate_id: str,
    candidate_kind: str,
    pool_label: str,
    pool: Sequence[Mapping[str, Any]],
    block_pred: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    base = _metrics(list(pool))
    blocked = [t for t in pool if block_pred(t)]
    kept = [t for t in pool if not block_pred(t)]
    kept_m = _metrics(kept)
    daily_base: dict[str, float] = defaultdict(float)
    daily_kept: dict[str, float] = defaultdict(float)
    for t in pool:
        daily_base[str(t.get("day") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    for t in kept:
        daily_kept[str(t.get("day") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    improved_days = sum(1 for d in daily_base if daily_kept.get(d, 0) > daily_base[d])
    sym_pnl: dict[str, float] = defaultdict(float)
    for t in blocked:
        sym_pnl[str(t.get("symbol") or "")] += float(_num(t.get("pnl_yen_100")) or 0)
    top_sym = max(sym_pnl.items(), key=lambda x: abs(x[1]))[0] if sym_pnl else ""
    top_sym_share = (
        round(abs(sym_pnl[top_sym]) / max(1.0, abs(sum(sym_pnl.values()))), 4) if sym_pnl else 0.0
    )
    d709_es = [t for t in pool if str(t.get("day") or "").startswith("2026-07-09") and _is_early_stop(t)]
    cold_es = [
        t
        for t in blocked
        if _is_early_stop(t) and _classify_gap_reason(t) == "price_history_insufficient"
    ]
    sym6525 = [t for t in blocked if _sym_t(str(t.get("symbol") or "")) == SYM_6525]
    return {
        "candidate_id": candidate_id,
        "candidate_kind": candidate_kind,
        "pool": pool_label,
        "blocked_count": len(blocked),
        "blocked_early_stop": sum(1 for t in blocked if _is_early_stop(t)),
        "blocked_stop_hit": sum(1 for t in blocked if _is_stop_hit(t)),
        "blocked_winners": sum(1 for t in blocked if _is_winner(t)),
        "blocked_big_winners": sum(1 for t in blocked if _is_big_winner(t)),
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base.get("pnl_yen_100") or 0), 2),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base.get("profit_factor") or 0), 4),
        "dd_delta": round(float(kept_m.get("max_dd_yen_100") or 0) - float(base.get("max_dd_yen_100") or 0), 2),
        "improved_days": improved_days,
        "capture_709_early_stop": sum(1 for t in d709_es if block_pred(t)),
        "blocked_709_winners": sum(
            1 for t in blocked if str(t.get("day") or "").startswith("2026-07-09") and _is_winner(t)
        ),
        "opening_cold_start_es_blocked": len(cold_es),
        "blocked_6525_count": len(sym6525),
        "top_blocked_symbol": top_sym,
        "top_symbol_pnl_share": top_sym_share,
    }


def _build_comparison(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cid, kind, pred in _candidate_predicates():
        for pool_label, pool in _pools(trades):
            if not pool:
                continue
            rows.append(
                _eval_candidate(
                    candidate_id=cid,
                    candidate_kind=kind,
                    pool_label=pool_label,
                    pool=pool,
                    block_pred=pred,
                )
            )
    return rows


def _pool_audit_rows(comparison: Sequence[Mapping[str, Any]], pool: str) -> list[dict[str, Any]]:
    return [dict(r) for r in comparison if r.get("pool") == pool]


def _6525_effect(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seq = [
        t
        for t in trades
        if str(t.get("day") or "").startswith("2026-07-09") and _sym_t(str(t.get("symbol") or "")) == SYM_6525
    ]
    seq.sort(key=lambda r: str(r.get("entry_time") or ""))
    rows: list[dict[str, Any]] = []
    preds = {cid: pred for cid, _, pred in _candidate_predicates() if cid in ("I_precision", "H_economics", "M_board_not_following", "K_cold_start_high_bounce", "L_cold_start_low_expectancy")}
    for i, t in enumerate(seq, start=1):
        row: dict[str, Any] = {
            "entry_index": i,
            "entry_time": t.get("entry_time"),
            "pnl_yen_100": t.get("pnl_yen_100"),
            "early_stop": t.get("early_stop"),
            "live_feature_incomplete": _live_feature_incomplete(t),
            "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
            "bounce_from_recent_low": t.get("bounce_from_recent_low"),
            "gap_reason": _classify_gap_reason(t),
        }
        for cid, pred in preds.items():
            row[f"blocked_by_{cid}"] = pred(t)
        rows.append(row)
    return rows


def _row(comparison: Sequence[Mapping[str, Any]], cid: str, pool: str) -> dict[str, Any]:
    return next((r for r in comparison if r.get("candidate_id") == cid and r.get("pool") == pool), {})


def _decide_verdict(comparison: Sequence[Mapping[str, Any]], sym6525: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, Any]]:
    i_canon = _row(comparison, "I_precision", "canonical_22")
    i_post = _row(comparison, "I_precision", "post_flat_band")
    i_709 = _row(comparison, "I_precision", "20260709")
    i_recent = _row(comparison, "I_precision", "recent_707_709")
    h_canon = _row(comparison, "H_economics", "canonical_22")
    h_709 = _row(comparison, "H_economics", "20260709")
    h_recent = _row(comparison, "H_economics", "recent_707_709")
    base_709 = _row(comparison, "baseline_lfc_incomplete_solo", "20260709")
    m_709 = _row(comparison, "M_board_not_following", "20260709")
    k_709 = _row(comparison, "K_cold_start_high_bounce", "20260709")
    gap_709 = _row(comparison, "baseline_microsequence_gap", "20260709")

    i_safe_canon = float(i_canon.get("delta_pnl_yen") or 0) >= 0 and int(i_canon.get("blocked_big_winners") or 0) <= 15
    i_safe_post = float(i_post.get("delta_pnl_yen") or 0) >= 0
    i_better_than_lfc = (
        int(i_709.get("blocked_winners") or 99) < int(base_709.get("blocked_winners") or 0)
        and int(i_709.get("capture_709_early_stop") or 0) >= 3
    )
    h_economics_ok = float(h_709.get("delta_pnl_yen") or 0) > 50000 and int(h_709.get("capture_709_early_stop") or 0) >= 5
    h_not_accident = float(h_recent.get("delta_pnl_yen") or 0) > 0 and float(h_canon.get("delta_pnl_yen") or 0) >= 0
    sym6525_i = sum(1 for r in sym6525 if r.get("blocked_by_I_precision") and r.get("early_stop"))
    sym6525_h = sum(1 for r in sym6525 if r.get("blocked_by_H_economics") and r.get("early_stop"))
    cold_gap = int(gap_709.get("opening_cold_start_es_blocked") or 0)

    shadow_pick = "I_precision"
    if h_economics_ok and not i_safe_canon:
        shadow_pick = "H_economics"
    elif int(m_709.get("capture_709_early_stop") or 0) >= int(i_709.get("capture_709_early_stop") or 0) and int(m_709.get("blocked_winners") or 99) <= int(i_709.get("blocked_winners") or 0):
        shadow_pick = "M_board_not_following"

    answers = {
        "1_I_safe_all_periods": i_safe_canon and i_safe_post,
        "2_H_worth_winner_sacrifice": h_economics_ok and int(h_709.get("blocked_winners") or 0) <= 10,
        "3_not_709_only_accident": float(i_recent.get("delta_pnl_yen") or 0) > 0 and h_not_accident,
        "4_post_flat_band_incremental": float(i_post.get("delta_pnl_yen") or 0) >= 0,
        "5_6525_stoppable": sym6525_i >= 2 or sym6525_h >= 2,
        "6525_es_blocked_I": sym6525_i,
        "6525_es_blocked_H": sym6525_h,
        "6_opening_cold_start_stoppable": cold_gap >= 2 or int(k_709.get("opening_cold_start_es_blocked") or 0) >= 2,
        "7_better_than_lfc_solo": i_better_than_lfc,
        "8_forward_shadow_pick": shadow_pick if i_safe_canon or h_economics_ok else "none",
        "9_forward_days_needed": 5 if shadow_pick == "I_precision" else 8,
        "I_canonical": i_canon,
        "I_709": i_709,
        "H_709": h_709,
        "baseline_lfc_709": base_709,
    }

    if (i_safe_canon and i_better_than_lfc) or (h_economics_ok and h_not_accident):
        verdict = PHASE678_VERDICT_SHADOW_CANDIDATE
    elif float(i_709.get("delta_pnl_yen") or 0) > 0 or float(h_709.get("delta_pnl_yen") or 0) > 0:
        verdict = PHASE678_VERDICT_HOLD
    else:
        verdict = PHASE678_VERDICT_REJECT
    return verdict, answers


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_focus_dataset()
    trades = _enrich_with_accept(trades, _load_accept_events_full())
    comparison = _build_comparison(trades)
    sym6525 = _6525_effect(trades)
    verdict, answers = _decide_verdict(comparison, sym6525)

    report: dict[str, Any] = {
        "verdict": verdict,
        "mandatory_answers": answers,
        "primary_candidates": [r for r in comparison if r.get("candidate_kind") == "primary"],
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": _disk_usage_pct(NATIVE_ROOT),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase678_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(
        REPORT_ROOT / "phase678_candidate_comparison.csv",
        [
            "candidate_id",
            "candidate_kind",
            "pool",
            "blocked_count",
            "blocked_early_stop",
            "blocked_stop_hit",
            "blocked_winners",
            "blocked_big_winners",
            "delta_pnl_yen",
            "pf_delta",
            "dd_delta",
            "improved_days",
            "capture_709_early_stop",
            "blocked_709_winners",
            "opening_cold_start_es_blocked",
            "blocked_6525_count",
            "top_blocked_symbol",
            "top_symbol_pnl_share",
        ],
        comparison,
    )
    _write_csv(REPORT_ROOT / "phase678_recent_707_709_audit.csv", list(comparison[0].keys()) if comparison else ["pool"], _pool_audit_rows(comparison, "recent_707_709"))
    _write_csv(REPORT_ROOT / "phase678_post_flat_band_audit.csv", list(comparison[0].keys()) if comparison else ["pool"], _pool_audit_rows(comparison, "post_flat_band"))
    _write_csv(REPORT_ROOT / "phase678_6525_readiness_effect.csv", list(sym6525[0].keys()) if sym6525 else ["entry_index"], sym6525)
    _write_decision_md(report=report)
    return report


def _write_decision_md(*, report: Mapping[str, Any]) -> None:
    ans = report.get("mandatory_answers") or {}
    lines = [
        "# Phase678 — Minimal Readiness Gate Robustness Review",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## 必須回答",
        "",
        f"1. I_precisionは全期間でも安全か: {ans.get('1_I_safe_all_periods')}",
        f"2. H_economicsはwinner犠牲に見合うか: {ans.get('2_H_worth_winner_sacrifice')}",
        f"3. 7/9だけの偶然ではないか: {ans.get('3_not_709_only_accident')}",
        f"4. post-flat-band純追加効果: {ans.get('4_post_flat_band_incremental')}",
        f"5. 6525型を止められるか: {ans.get('5_6525_stoppable')} (I ES={ans.get('6525_es_blocked_I')}, H ES={ans.get('6525_es_blocked_H')})",
        f"6. opening cold-start ESを止められるか: {ans.get('6_opening_cold_start_stoppable')}",
        f"7. live_feature_complete単独より優れるか: {ans.get('7_better_than_lfc_solo')}",
        f"8. Forward Shadow候補: {ans.get('8_forward_shadow_pick')}",
        f"9. Forward確認日数: {ans.get('9_forward_days_needed')}日",
        "",
        "## 参照メトリクス",
        "",
        f"- I canonical: {ans.get('I_canonical')}",
        f"- I 7/9: {ans.get('I_709')}",
        f"- H 7/9: {ans.get('H_709')}",
        "",
    ]
    (REPORT_ROOT / "phase678_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "shadow_pick": report.get("mandatory_answers", {}).get("8_forward_shadow_pick")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
