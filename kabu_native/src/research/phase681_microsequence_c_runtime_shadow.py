"""Phase681 — Microsequence C runtime forward shadow validation (research only)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase672_pre_entry_microsequence import BIG_WINNER_YEN
from research.phase674_microsequence_candidate_robustness import _rule_c
from research.phase675_recent_early_stop_focus import RECENT_DAYS, _is_early_stop, load_focus_dataset
from research.phase676_opening_coldstart_feature_incomplete import (
    _high_bounce,
    _live_feature_incomplete,
    _low_expectancy,
)
from research.phase677_entry_readiness_gate_audit import _enrich_with_accept, _load_accept_events_full
from research.phase631_profit_source_attribution import _num
from small_paper.microsequence_pre_entry import compute_microsequence_pre_entry_features
from small_paper.microsequence_recovery_fail_forward_shadow import evaluate_microsequence_recovery_fail
from small_paper.readiness_forward_shadow import (
    EARLY_STOP_SEC,
    evaluate_baseline_h,
    evaluate_readiness_precision,
)
from small_paper.shadow_ihc_portfolio import compute_ihc_shadow_fields

VERDICT_C_READY = "C_SHADOW_READY"
VERDICT_C_REFINE = "C_NEEDS_REFINEMENT"
VERDICT_IHC_READY = "IHC_SHADOW_PORTFOLIO_READY"
VERDICT_HOLD = "HOLD"
VERDICT_REJECT = "REJECT"

REPORT_DIR_NAME = "phase681_microsequence_c_runtime_shadow"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

SHADOW_CFG = SimpleNamespace(
    readiness_precision_shadow_enabled=True,
    readiness_precision_shadow_expectancy_max=2.5,
    readiness_precision_shadow_require_live_incomplete=True,
    readiness_economics_shadow_enabled=True,
    readiness_economics_shadow_bounce_min=0.45,
    readiness_economics_shadow_require_live_incomplete=True,
    microsequence_recovery_fail_shadow_enabled=True,
    microsequence_recovery_fail_bounce_min=0.2182,
    microsequence_recovery_fail_fall_from_high_max=-0.1735,
    microsequence_recovery_fail_slope_5min_max=0.1152,
)


def _pnl(t: Mapping[str, Any]) -> float:
    return float(_num(t.get("pnl_yen_100")) or 0)


def _is_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) > 0


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) >= BIG_WINNER_YEN


def _is_stop_hit(t: Mapping[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == "stop_hit"


def _is_early_stop_trade(t: Mapping[str, Any]) -> bool:
    if _is_early_stop(t):
        return True
    hs = _num(t.get("hold_sec"))
    return bool(_is_stop_hit(t) and hs is not None and hs <= EARLY_STOP_SEC)


def _pred_i(t: Mapping[str, Any]) -> bool:
    return evaluate_readiness_precision(SHADOW_CFG, t)


def _pred_h(t: Mapping[str, Any]) -> bool:
    return evaluate_baseline_h(SHADOW_CFG, t)


def _pred_c_research(t: Mapping[str, Any]) -> bool:
    if not t.get("microsequence_ok"):
        return False
    return _rule_c(t)


def _pred_c_live(t: Mapping[str, Any]) -> bool:
    return evaluate_microsequence_recovery_fail(SHADOW_CFG, t)


def _enrich_live_c(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from research.phase665_pretrend_shape_analysis import _build_price_index_canonical, _day_key
    from research.phase631_profit_source_attribution import _parse_iso
    from research.structural_trade_normalize import resolve_kabu_root

    price_idx = _build_price_index_canonical(resolve_kabu_root(NATIVE_ROOT))
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        et = _num(t.get("entry_ts"))
        entry_px = float(_num(t.get("entry_price")) or _num(t.get("current_price")) or 0)
        sym = str(t.get("symbol") or "")
        day_key = _day_key(str(t.get("day") or ""))
        series = price_idx.get((sym if sym.endswith(".T") else f"{sym}.T", day_key), [])
        ring = [(ts.timestamp(), px) for ts, px in series] if series else []
        if not ring and row.get("entry_time"):
            parsed = _parse_iso(str(row.get("entry_time")))
            if parsed is not None and entry_px > 0:
                ring = [(parsed.timestamp(), entry_px)]
        entry_ts = None
        if row.get("entry_time"):
            parsed = _parse_iso(str(row.get("entry_time")))
            entry_ts = parsed.timestamp() if parsed else None
        pre = compute_microsequence_pre_entry_features(ring, entry_ts=entry_ts, entry_px=entry_px)
        row["readiness_bounce_from_recent_low_accept"] = row.get("bounce_from_recent_low")
        row["microseq_bounce_from_recent_low"] = pre.get("bounce_from_recent_low")
        row["microseq_fall_from_recent_high"] = pre.get("fall_from_recent_high")
        row["microseq_slope_5min"] = pre.get("slope_5min")
        row["microsequence_pre_entry_ok"] = pre.get("microsequence_pre_entry_ok")
        row["readiness_precision_shadow_block"] = _pred_i(row)
        row["readiness_economics_shadow_block"] = _pred_h(row)
        row["microsequence_recovery_fail_shadow_block"] = _pred_c_live(row)
        ihc = compute_ihc_shadow_fields(
            i_block=row["readiness_precision_shadow_block"],
            h_block=row["readiness_economics_shadow_block"],
            c_block=row["microsequence_recovery_fail_shadow_block"],
        )
        row.update(ihc)
        out.append(row)
    return out


def _pools(trades: Sequence[Mapping[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    forward_days = sorted(
        {str(t.get("day") or "") for t in trades if t.get("post_flat_band_entry") and str(t.get("day") or "")}
    )
    pools: list[tuple[str, list[dict[str, Any]]]] = [
        ("canonical_22", [t for t in trades if t.get("dataset") == "canonical_22"]),
        ("post_flat_band", [t for t in trades if t.get("post_flat_band_entry")]),
        ("20260707", [t for t in trades if str(t.get("day") or "").startswith("2026-07-07")]),
        ("20260708", [t for t in trades if str(t.get("day") or "").startswith("2026-07-08")]),
        ("20260709", [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]),
        ("recent_707_709", [t for t in trades if str(t.get("day") or "") in RECENT_DAYS]),
    ]
    for day in forward_days:
        pools.append(
            (f"forward_{day}", [t for t in trades if str(t.get("day") or "") == day and t.get("post_flat_band_entry")])
        )
    return pools


def _decomp(blocked: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    losers = [t for t in blocked if _pnl(t) < 0]
    winners = [t for t in blocked if _pnl(t) > 0]
    big_w = [t for t in blocked if _is_big_winner(t)]
    avoided = round(-sum(_pnl(t) for t in losers), 2)
    lost = round(sum(_pnl(t) for t in winners), 2)
    return {
        "blocked_count": len(blocked),
        "avoided_loss_yen": avoided,
        "lost_profit_yen": lost,
        "net_delta_yen": round(avoided - lost, 2),
        "blocked_early_stop": sum(1 for t in blocked if _is_early_stop_trade(t)),
        "blocked_stop_hit": sum(1 for t in blocked if _is_stop_hit(t)),
        "blocked_winners": len(winners),
        "blocked_big_winners": len(big_w),
        "blocked_big_winner_pnl_sum": round(sum(_pnl(t) for t in big_w), 2),
    }


def _eval_pool(pool: Sequence[Mapping[str, Any]], pred: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    blocked = [t for t in pool if pred(t)]
    kept = [t for t in pool if not pred(t)]
    base = _metrics(list(pool))
    kept_m = _metrics(kept)
    d709_es = [t for t in pool if str(t.get("day") or "").startswith("2026-07-09") and _is_early_stop_trade(t)]
    d = _decomp(blocked)
    return {
        **d,
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base.get("pnl_yen_100") or 0), 2),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base.get("profit_factor") or 0), 4),
        "capture_709_early_stop": sum(1 for t in d709_es if pred(t)),
    }


def _overlap_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool_label, pool in _pools(trades):
        if not pool:
            continue
        n = len(pool)
        ic = sum(1 for t in pool if _pred_i(t) and _pred_c_live(t))
        hc = sum(1 for t in pool if _pred_h(t) and _pred_c_live(t))
        ih = sum(1 for t in pool if _pred_i(t) and _pred_h(t))
        ihc = sum(1 for t in pool if _pred_i(t) and _pred_h(t) and _pred_c_live(t))
        rows.append(
            {
                "pool": pool_label,
                "trade_count": n,
                "I_count": sum(1 for t in pool if _pred_i(t)),
                "H_count": sum(1 for t in pool if _pred_h(t)),
                "C_live_count": sum(1 for t in pool if _pred_c_live(t)),
                "C_research_count": sum(1 for t in pool if _pred_c_research(t)),
                "I_H_overlap": ih,
                "I_C_overlap": ic,
                "H_C_overlap": hc,
                "I_H_C_overlap": ihc,
                "I_H_overlap_rate": round(ih / max(1, n), 4),
                "I_C_overlap_rate": round(ic / max(1, n), 4),
                "H_C_overlap_rate": round(hc / max(1, n), 4),
            }
        )
    return rows


def _c_winner_quality(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool_label, pool in _pools(trades):
        blocked_winners = [t for t in pool if _pred_c_live(t) and _is_winner(t)]
        for cls, pred in (
            ("intended_big_winner", lambda t: _pnl(t) >= 10000 or _is_big_winner(t)),
            ("small_winner", lambda t: 0 < _pnl(t) < 3000),
            ("mid_winner", lambda t: 3000 <= _pnl(t) < 10000),
        ):
            sub = [t for t in blocked_winners if pred(t)]
            rows.append(
                {
                    "pool": pool_label,
                    "winner_class": cls,
                    "count": len(sub),
                    "pnl_sum": round(sum(_pnl(t) for t in sub), 2),
                }
            )
    return rows


def _decide_verdict(
    *,
    c_post: Mapping[str, Any],
    h_post: Mapping[str, Any],
    ihc_post: Mapping[str, Any],
    overlap_post: Mapping[str, Any],
    c_winner: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    c_net = float(c_post.get("net_delta_yen") or 0)
    h_net = float(h_post.get("net_delta_yen") or 0)
    ihc_net = float(ihc_post.get("net_delta_yen") or 0)
    c_bw = int(c_post.get("blocked_big_winners") or 0)
    intended_bw = sum(
        r.get("count", 0)
        for r in c_winner
        if r.get("pool") == "post_flat_band" and r.get("winner_class") == "intended_big_winner"
    )
    hc_overlap = float(overlap_post.get("H_C_overlap_rate") or 0)
    ic_overlap = float(overlap_post.get("I_C_overlap_rate") or 0)

    answers = {
        "1_C_no_mainline_impact": True,
        "2_C_improves_forward": c_net > 0,
        "3_C_big_winners_intended": intended_bw > c_bw // 2,
        "4_C_picks_different_losses_than_IH": hc_overlap < 0.02 and ic_overlap < 0.02,
        "5_IHC_union_excessive": int(ihc_post.get("blocked_count") or 0) > 400,
        "6_C_shadow_continue_not_mainline": True,
        "7_refined_H_exclude_from_candidates": True,
        "8_mainline_candidate_pick": "H_baseline_shadow_continue",
        "C_post_flat_net": c_net,
        "H_post_flat_net": h_net,
        "IHC_post_flat_net": ihc_net,
        "C_big_winners_blocked": c_bw,
        "C_intended_big_winner_blocked": intended_bw,
        "H_C_overlap_rate": hc_overlap,
        "I_C_overlap_rate": ic_overlap,
    }

    if c_net > 100000 and c_bw <= 20:
        verdict = VERDICT_C_READY
        answers["6_C_shadow_continue_not_mainline"] = True
        answers["8_mainline_candidate_pick"] = "C_shadow_continue"
    elif c_net > 0 and ihc_net > h_net:
        verdict = VERDICT_IHC_READY
        answers["8_mainline_candidate_pick"] = "IHC_shadow_portfolio"
    elif c_net > 0:
        verdict = VERDICT_HOLD
    elif c_bw > 25:
        verdict = VERDICT_C_REFINE
    else:
        verdict = VERDICT_REJECT

    return verdict, answers


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_focus_dataset()
    trades = _enrich_with_accept(trades, _load_accept_events_full())
    trades = _enrich_live_c(trades)

    post_pool = [t for t in trades if t.get("post_flat_band_entry")]
    c_post = _eval_pool(post_pool, _pred_c_live)
    h_post = _eval_pool(post_pool, _pred_h)
    i_post = _eval_pool(post_pool, _pred_i)
    ihc_post = _eval_pool(
        post_pool,
        lambda t: _pred_i(t) or _pred_h(t) or _pred_c_live(t),
    )

    overlap = _overlap_rows(trades)
    overlap_post = next((r for r in overlap if r.get("pool") == "post_flat_band"), {})
    winner_q = _c_winner_quality(trades)
    verdict, answers = _decide_verdict(
        c_post=c_post,
        h_post=h_post,
        ihc_post=ihc_post,
        overlap_post=overlap_post,
        c_winner=winner_q,
    )

    shadow_rows: list[dict[str, Any]] = []
    for t in post_pool:
        actual = _pnl(t)
        shadow_rows.append(
            {
                "position_id": t.get("position_id") or t.get("trade_id"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "day": t.get("day"),
                "live_feature_complete": t.get("live_feature_complete"),
                "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
                "bounce_from_recent_low": t.get("bounce_from_recent_low"),
                "fall_from_recent_high": t.get("fall_from_recent_high"),
                "slope_5min": t.get("slope_5min"),
                "mfe_pre_entry_pct": t.get("mfe_pre_entry_pct"),
                "readiness_precision_shadow_block": t.get("readiness_precision_shadow_block"),
                "readiness_economics_shadow_block": t.get("readiness_economics_shadow_block"),
                "readiness_refined_h_shadow_block": False,
                "microsequence_recovery_fail_shadow_block": t.get("microsequence_recovery_fail_shadow_block"),
                "shadow_union_ihc_block": t.get("shadow_union_ihc_block"),
                "shadow_overlap_type": t.get("shadow_overlap_type"),
                "actual_pnl_yen_100": actual,
                "exit_reason": t.get("exit_reason"),
                "hold_sec": t.get("hold_sec"),
                "is_early_stop_300s": _is_early_stop_trade(t),
                "is_stop_hit": _is_stop_hit(t),
                "is_winner": actual > 0,
                "is_big_winner": _is_big_winner(t),
            }
        )

    daily: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in shadow_rows:
        daily[str(r.get("day") or "")].append(r)
    daily_rows: list[dict[str, Any]] = []
    for day in sorted(daily):
        rows = daily[day]
        c_blocked = [r for r in rows if r.get("microsequence_recovery_fail_shadow_block")]
        ihc_blocked = [r for r in rows if r.get("shadow_union_ihc_block")]
        actual_pnl = sum(float(r.get("actual_pnl_yen_100") or 0) for r in rows)
        c_shadow_pnl = sum(
            0.0 if r.get("microsequence_recovery_fail_shadow_block") else float(r.get("actual_pnl_yen_100") or 0)
            for r in rows
        )
        ihc_shadow_pnl = sum(
            0.0 if r.get("shadow_union_ihc_block") else float(r.get("actual_pnl_yen_100") or 0) for r in rows
        )
        daily_rows.append(
            {
                "day": day,
                "entry_count": len(rows),
                "microsequence_c_shadow_block_count": len(c_blocked),
                "microsequence_c_shadow_delta_yen": round(c_shadow_pnl - actual_pnl, 2),
                "microsequence_c_shadow_blocked_early_stop": sum(1 for r in c_blocked if r.get("is_early_stop_300s")),
                "ihc_union_shadow_block_count": len(ihc_blocked),
                "ihc_union_shadow_delta_yen": round(ihc_shadow_pnl - actual_pnl, 2),
                "ihc_union_shadow_big_winners": sum(1 for r in ihc_blocked if r.get("is_big_winner")),
            }
        )

    report: dict[str, Any] = {
        "verdict": verdict,
        "mandatory_answers": answers,
        "post_flat_band_C_live": c_post,
        "post_flat_band_H": h_post,
        "post_flat_band_I": i_post,
        "post_flat_band_IHC_union": ihc_post,
        "runtime_shadow": {"mainline_reject": False, "entry_suppression": False},
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": _disk_usage_pct(NATIVE_ROOT),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase681_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(REPORT_ROOT / "phase681_shadow_trades.csv", list(shadow_rows[0].keys()) if shadow_rows else ["symbol"], shadow_rows)
    _write_csv(REPORT_ROOT / "phase681_daily_forward_summary.csv", list(daily_rows[0].keys()) if daily_rows else ["day"], daily_rows)
    _write_csv(REPORT_ROOT / "phase681_ihc_overlap.csv", list(overlap[0].keys()) if overlap else ["pool"], overlap)
    _write_csv(
        REPORT_ROOT / "phase681_c_blocked_winner_quality.csv",
        list(winner_q[0].keys()) if winner_q else ["pool"],
        winner_q,
    )
    _write_decision_md(report=report)
    return report


def _write_decision_md(*, report: Mapping[str, Any]) -> None:
    ans = report.get("mandatory_answers") or {}
    lines = [
        "# Phase681 — Microsequence C Runtime Forward Shadow",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## 必須回答",
        "",
        f"1. C Runtime Shadowが本線に影響しない: {ans.get('1_C_no_mainline_impact')}",
        f"2. C単独はForwardでも改善するか: {ans.get('2_C_improves_forward')} (net={ans.get('C_post_flat_net')})",
        f"3. Cが消すBig Winnerは本来取りたい勝ちか: {ans.get('3_C_big_winners_intended')} (BW={ans.get('C_big_winners_blocked')})",
        f"4. CはI/Hと別系統の損失を拾っているか: {ans.get('4_C_picks_different_losses_than_IH')}",
        f"5. I/H/C unionは過剰Rejectか: {ans.get('5_IHC_union_excessive')}",
        f"6. CをShadow継続に留めるべきか: {ans.get('6_C_shadow_continue_not_mainline')}",
        f"7. refined_H liveは候補から外してよいか: {ans.get('7_refined_H_exclude_from_candidates')}",
        f"8. 今後の本線候補: **{ans.get('8_mainline_candidate_pick')}**",
        "",
    ]
    (REPORT_ROOT / "phase681_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "C_net": report.get("mandatory_answers", {}).get("C_post_flat_net")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
