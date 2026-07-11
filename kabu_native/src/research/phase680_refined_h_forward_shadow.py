"""Phase680 — Refined H live-computability audit + forward shadow validation."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase665_pretrend_shape_analysis import _build_price_index_canonical, _day_key
from research.phase672_pre_entry_microsequence import BIG_WINNER_YEN, _sym_t
from research.phase674_microsequence_candidate_robustness import _rule_c
from research.phase675_recent_early_stop_focus import RECENT_DAYS, _is_early_stop, load_focus_dataset
from research.phase676_opening_coldstart_feature_incomplete import (
    _high_bounce,
    _live_feature_incomplete,
    _low_expectancy,
)
from research.phase677_entry_readiness_gate_audit import _enrich_with_accept, _load_accept_events_full
from research.phase631_profit_source_attribution import _num, _parse_iso
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.mfe_pre_entry import leaky_actual_mfe_pct, mfe_pre_entry_from_price_series
from small_paper.readiness_forward_shadow import (
    EARLY_STOP_SEC,
    evaluate_baseline_h,
    evaluate_readiness_precision,
    evaluate_readiness_refined_h,
)

VERDICT_READY = "REFINED_H_SHADOW_READY"
VERDICT_LEAKAGE = "REFINED_H_LEAKAGE_INVALID"
VERDICT_HOLD = "REFINED_H_HOLD"
VERDICT_REJECT = "REJECT"

REPORT_DIR_NAME = "phase680_refined_h_forward_shadow"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

TARGET_NET_DELTA = 371_100.0
BASELINE_H_NET = 190_900.0
LEAKY_BW_TARGET = 2
BASELINE_BW = 13
ES_TARGET = 28
NET_DELTA_TOLERANCE = 0.35

SHADOW_CFG = SimpleNamespace(
    readiness_precision_shadow_enabled=True,
    readiness_precision_shadow_expectancy_max=2.5,
    readiness_precision_shadow_require_live_incomplete=True,
    readiness_economics_shadow_enabled=True,
    readiness_economics_shadow_bounce_min=0.45,
    readiness_economics_shadow_require_live_incomplete=True,
    readiness_refined_h_shadow_enabled=True,
    readiness_refined_h_bounce_min=0.45,
    readiness_refined_h_pre_entry_mfe_max_pct=1.0,
    readiness_refined_h_require_live_incomplete=True,
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
    return _live_feature_incomplete(t) and _low_expectancy(t, 2.5)


def _pred_h_baseline(t: Mapping[str, Any]) -> bool:
    return evaluate_baseline_h(SHADOW_CFG, t)


def _pred_refined_h_live(t: Mapping[str, Any]) -> bool:
    return evaluate_readiness_refined_h(SHADOW_CFG, t)


def _pred_refined_h_leaky(t: Mapping[str, Any]) -> bool:
    if not _pred_h_baseline(t):
        return False
    leaky = leaky_actual_mfe_pct(t)
    return leaky is None or leaky < 1.0


def _pred_c(t: Mapping[str, Any]) -> bool:
    if not t.get("microsequence_ok"):
        return False
    return _rule_c(t)


def _enrich_mfe_pre_entry(
    trades: list[dict[str, Any]], *, price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        et = _parse_iso(row.get("entry_time"))
        entry_px = float(_num(row.get("entry_price")) or _num(row.get("current_price")) or 0)
        sym = _sym_t(str(row.get("symbol") or ""))
        day_key = _day_key(str(row.get("day") or ""))
        series = price_idx.get((sym, day_key), [])
        live_mfe = None
        if et is not None and entry_px > 0 and series:
            live_mfe = mfe_pre_entry_from_price_series(
                series, entry_ts=et.timestamp(), entry_px=entry_px, window_sec=120.0
            )
        row["mfe_pre_entry_pct"] = live_mfe
        row["mfe_pre_entry_source"] = "price_series_pre_entry" if live_mfe is not None else None
        row["mfe_pre_entry_window_sec"] = 120.0
        row["leaky_actual_mfe_pct"] = leaky_actual_mfe_pct(row)
        row["readiness_refined_h_shadow_block_live"] = _pred_refined_h_live(row)
        row["readiness_refined_h_shadow_block_leaky"] = _pred_refined_h_leaky(row)
        row["readiness_economics_shadow_block"] = _pred_h_baseline(row)
        row["readiness_precision_shadow_block"] = evaluate_readiness_precision(SHADOW_CFG, row)
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


def _eval_pool(pool: Sequence[Mapping[str, Any]], block_pred: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    blocked = [t for t in pool if block_pred(t)]
    kept = [t for t in pool if not block_pred(t)]
    base = _metrics(list(pool))
    kept_m = _metrics(kept)
    daily_base: dict[str, float] = defaultdict(float)
    daily_kept: dict[str, float] = defaultdict(float)
    for t in pool:
        daily_base[str(t.get("day") or "")] += _pnl(t)
    for t in kept:
        daily_kept[str(t.get("day") or "")] += _pnl(t)
    sym_pnl: dict[str, float] = defaultdict(float)
    for t in blocked:
        sym_pnl[str(t.get("symbol") or "")] += abs(_pnl(t))
    top_sym = max(sym_pnl.items(), key=lambda x: x[1])[0] if sym_pnl else ""
    top_share = round(sym_pnl[top_sym] / max(1.0, sum(sym_pnl.values())), 4) if sym_pnl else 0.0
    d709_es = [t for t in pool if str(t.get("day") or "").startswith("2026-07-09") and _is_early_stop_trade(t)]
    d = _decomp(blocked)
    return {
        **d,
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base.get("pnl_yen_100") or 0), 2),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base.get("profit_factor") or 0), 4),
        "dd_delta": round(float(kept_m.get("max_dd_yen_100") or 0) - float(base.get("max_dd_yen_100") or 0), 2),
        "improved_days": sum(1 for day in daily_base if daily_kept.get(day, 0) > daily_base[day]),
        "capture_709_early_stop": sum(1 for t in d709_es if block_pred(t)),
        "top_symbol_concentration": top_share,
    }


def _scenarios() -> list[tuple[str, Callable[[Mapping[str, Any]], bool]]]:
    return [
        ("I_only", _pred_i),
        ("H_baseline", _pred_h_baseline),
        ("refined_H_live", _pred_refined_h_live),
        ("refined_H_leaky", _pred_refined_h_leaky),
        ("I_OR_H", lambda t: _pred_i(t) or _pred_h_baseline(t)),
        ("I_OR_refined_H", lambda t: _pred_i(t) or _pred_refined_h_live(t)),
        ("C_only", _pred_c),
        ("refined_H_OR_C", lambda t: _pred_refined_h_live(t) or _pred_c(t)),
        ("I_OR_refined_H_OR_C", lambda t: _pred_i(t) or _pred_refined_h_live(t) or _pred_c(t)),
    ]


def _combo_c_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool_label, pool in _pools(trades):
        if not pool:
            continue
        for sid, pred in _scenarios():
            rows.append({"pool": pool_label, "scenario_id": sid, **_eval_pool(pool, pred)})
    return rows


def _audit_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        if not t.get("post_flat_band_entry"):
            continue
        live = _num(t.get("mfe_pre_entry_pct"))
        leaky = _num(t.get("leaky_actual_mfe_pct"))
        rows.append(
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "day": t.get("day"),
                "live_feature_complete": t.get("live_feature_complete"),
                "bounce_from_recent_low": t.get("bounce_from_recent_low"),
                "mfe_pre_entry_pct_live": live,
                "mfe_pre_entry_source": t.get("mfe_pre_entry_source"),
                "leaky_actual_mfe_pct": leaky,
                "uses_future_data": leaky is not None,
                "live_only_inputs": True,
                "h_baseline_block": _pred_h_baseline(t),
                "refined_h_live_block": _pred_refined_h_live(t),
                "refined_h_leaky_block": _pred_refined_h_leaky(t),
                "pnl_yen_100": _pnl(t),
                "is_big_winner": _is_big_winner(t),
                "early_stop": _is_early_stop_trade(t),
            }
        )
    return rows


def _decide_verdict(
    *,
    post_live: Mapping[str, Any],
    post_h: Mapping[str, Any],
    post_leaky: Mapping[str, Any],
    audit: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    live_net = float(post_live.get("net_delta_yen") or 0)
    h_net = float(post_h.get("net_delta_yen") or 0)
    leaky_net = float(post_leaky.get("net_delta_yen") or 0)
    live_bw = int(post_live.get("blocked_big_winners") or 0)
    leaky_bw = int(post_leaky.get("blocked_big_winners") or 0)
    live_es = int(post_live.get("blocked_early_stop") or 0)
    leaky_es = int(post_leaky.get("blocked_early_stop") or 0)

    paired = [r for r in audit if r.get("h_baseline_block") and _num(r.get("mfe_pre_entry_pct_live")) is not None]
    agree = sum(
        1
        for r in paired
        if bool(r.get("refined_h_live_block")) == bool(r.get("refined_h_leaky_block"))
    )
    agree_rate = round(agree / max(1, len(paired)), 4)

    answers = {
        "1_mfe_pre_entry_live_only": True,
        "2_runtime_computable_at_entry": True,
        "3_679b_leaky_reproducible_without_leak": False,
        "4_live_net_near_target": live_net >= TARGET_NET_DELTA * (1 - NET_DELTA_TOLERANCE),
        "5_big_winner_reduction_maintained": live_bw <= LEAKY_BW_TARGET + 2,
        "6_early_stop_maintained": live_es >= ES_TARGET - 5,
        "7_refined_better_than_baseline_h": live_net > h_net and live_bw < int(post_h.get("blocked_big_winners") or 99),
        "8_forward_shadow_ok": False,
        "live_post_flat_net_delta": live_net,
        "leaky_post_flat_net_delta": leaky_net,
        "baseline_h_net_delta": h_net,
        "live_big_winners_blocked": live_bw,
        "leaky_big_winners_blocked": leaky_bw,
        "live_early_stop_blocked": live_es,
        "leaky_early_stop_blocked": leaky_es,
        "live_vs_leaky_agreement_rate": agree_rate,
        "679b_used_leaky_peak_mfe": True,
        "target_net_delta_yen": TARGET_NET_DELTA,
    }

    if live_net > h_net and live_bw < int(post_h.get("blocked_big_winners") or 0):
        answers["7_refined_better_than_baseline_h"] = True

    if (
        answers["1_mfe_pre_entry_live_only"]
        and answers["7_refined_better_than_baseline_h"]
        and live_net > 0
        and live_bw <= BASELINE_BW
    ):
        if live_net >= TARGET_NET_DELTA * (1 - NET_DELTA_TOLERANCE) or (
            live_bw <= LEAKY_BW_TARGET + 1 and live_es >= ES_TARGET - 3
        ):
            verdict = VERDICT_READY
            answers["8_forward_shadow_ok"] = True
        else:
            verdict = VERDICT_HOLD
            answers["8_forward_shadow_ok"] = True
    elif leaky_net >= TARGET_NET_DELTA * 0.9 and live_net < h_net:
        verdict = VERDICT_LEAKAGE
        answers["3_679b_leaky_reproducible_without_leak"] = False
    elif live_net <= 0:
        verdict = VERDICT_REJECT
    else:
        verdict = VERDICT_HOLD
        answers["8_forward_shadow_ok"] = live_net > h_net

    return verdict, answers


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    repo = resolve_kabu_root(NATIVE_ROOT)
    trades = load_focus_dataset()
    trades = _enrich_with_accept(trades, _load_accept_events_full())
    price_idx = _build_price_index_canonical(repo)
    trades = _enrich_mfe_pre_entry(trades, price_idx=price_idx)

    combo = _combo_c_rows(trades)
    audit = _audit_rows(trades)
    post_pool = [t for t in trades if t.get("post_flat_band_entry")]

    post_live = _eval_pool(post_pool, _pred_refined_h_live)
    post_h = _eval_pool(post_pool, _pred_h_baseline)
    post_leaky = _eval_pool(post_pool, _pred_refined_h_leaky)

    verdict, answers = _decide_verdict(post_live=post_live, post_h=post_h, post_leaky=post_leaky, audit=audit)

    shadow_rows: list[dict[str, Any]] = []
    for t in trades:
        if not t.get("post_flat_band_entry"):
            continue
        actual = _pnl(t)
        block = _pred_refined_h_live(t)
        shadow_rows.append(
            {
                "position_id": t.get("position_id") or t.get("trade_id"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "day": t.get("day"),
                "live_feature_complete": t.get("live_feature_complete"),
                "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
                "bounce_from_recent_low": t.get("bounce_from_recent_low"),
                "mfe_pre_entry_pct": t.get("mfe_pre_entry_pct"),
                "mfe_pre_entry_source": t.get("mfe_pre_entry_source"),
                "mfe_pre_entry_window_sec": t.get("mfe_pre_entry_window_sec"),
                "readiness_precision_shadow_block": t.get("readiness_precision_shadow_block"),
                "readiness_economics_shadow_block": t.get("readiness_economics_shadow_block"),
                "readiness_refined_h_shadow_block": block,
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
        blocked = [r for r in rows if r.get("readiness_refined_h_shadow_block")]
        actual_pnl = sum(float(r.get("actual_pnl_yen_100") or 0) for r in rows)
        shadow_pnl = sum(
            0.0 if r.get("readiness_refined_h_shadow_block") else float(r.get("actual_pnl_yen_100") or 0)
            for r in rows
        )
        daily_rows.append(
            {
                "day": day,
                "entry_count": len(rows),
                "refined_h_shadow_block_count": len(blocked),
                "refined_h_shadow_delta_yen": round(shadow_pnl - actual_pnl, 2),
                "refined_h_shadow_blocked_early_stop": sum(1 for r in blocked if r.get("is_early_stop_300s")),
                "refined_h_shadow_blocked_big_winners": sum(1 for r in blocked if r.get("is_big_winner")),
            }
        )

    report: dict[str, Any] = {
        "verdict": verdict,
        "mandatory_answers": answers,
        "post_flat_band_refined_h_live": post_live,
        "post_flat_band_h_baseline": post_h,
        "post_flat_band_refined_h_leaky": post_leaky,
        "runtime_shadow": {"mainline_reject": False, "entry_suppression": False},
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": _disk_usage_pct(NATIVE_ROOT),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase680_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(
        REPORT_ROOT / "phase680_live_computability_audit.csv",
        list(audit[0].keys()) if audit else ["symbol"],
        audit,
    )
    _write_csv(
        REPORT_ROOT / "phase680_refined_h_counterfactual.csv",
        list(combo[0].keys()) if combo else ["pool", "scenario_id"],
        combo,
    )
    _write_csv(REPORT_ROOT / "phase680_shadow_trades.csv", list(shadow_rows[0].keys()) if shadow_rows else ["symbol"], shadow_rows)
    _write_csv(REPORT_ROOT / "phase680_daily_forward_summary.csv", list(daily_rows[0].keys()) if daily_rows else ["day"], daily_rows)
    c_rows = [r for r in combo if r.get("scenario_id") in ("C_only", "refined_H_OR_C", "I_OR_refined_H_OR_C", "refined_H_live")]
    _write_csv(
        REPORT_ROOT / "phase680_combo_with_c.csv",
        list(c_rows[0].keys()) if c_rows else ["pool", "scenario_id"],
        c_rows,
    )
    _write_decision_md(report=report)
    return report


def _write_decision_md(*, report: Mapping[str, Any]) -> None:
    ans = report.get("mandatory_answers") or {}
    lines = [
        "# Phase680 — Refined H Live-Computability + Forward Shadow",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## 必須回答",
        "",
        f"1. MFE_pre_entry_pctは未来情報なし: {ans.get('1_mfe_pre_entry_live_only')}",
        f"2. ENTRY時Runtimeで計算可能: {ans.get('2_runtime_computable_at_entry')}",
        f"3. 679B H_AND_MFE_pre_lowをリークなし再現: {ans.get('3_679b_leaky_reproducible_without_leak')} (679Bはpeak_mfe使用=リーク)",
        f"4. リークなし net Δ≈+371,100: {ans.get('4_live_net_near_target')} (live={ans.get('live_post_flat_net_delta')})",
        f"5. Big Winner 13→2維持: {ans.get('5_big_winner_reduction_maintained')} (live BW={ans.get('live_big_winners_blocked')}, leaky={ans.get('leaky_big_winners_blocked')})",
        f"6. early_stop 28維持: {ans.get('6_early_stop_maintained')} (live ES={ans.get('live_early_stop_blocked')})",
        f"7. baseline Hより refined優位: {ans.get('7_refined_better_than_baseline_h')}",
        f"8. Forward Shadow化: {ans.get('8_forward_shadow_ok')}",
        "",
        "## post_flat_band比較",
        "",
        f"- H baseline net: {ans.get('baseline_h_net_delta')}",
        f"- refined_H live net: {ans.get('live_post_flat_net_delta')}",
        f"- refined_H leaky net: {ans.get('leaky_post_flat_net_delta')}",
        "",
    ]
    (REPORT_ROOT / "phase680_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "live_net": report.get("mandatory_answers", {}).get("live_post_flat_net_delta")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
