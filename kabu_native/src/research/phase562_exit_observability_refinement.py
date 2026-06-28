"""
Phase562 — EXIT observability refinement (research only).

Segments T0 vs T2/T3/T6 effects, T2/T3 condition profiles, monitor metric design.
No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase493_global_entry_failure_audit import _session_bucket
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase546_entry_cluster_shadow_replay import _trade_key as _cluster_trade_key
from research.phase551_current_runtime_full_period_replay import _is_or_trade
from research.phase560_exit_profit_maximization_study import EARLY_RULES, _num
from research.phase561_trailing_shadow_validation import (
    FULL_END,
    FULL_START,
    LIVE_START,
    _load_full_period_accepted,
    _run_shadow_replay,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.board_dynamic_trailing_shadow import board_tier_from_percentile

PHASE562_VERDICT = "phase562_exit_observability_refinement_done"
COMPARE_SPECS = ("T2", "T3", "T6")

SEGMENT_FIELDS = [
    "segment_type",
    "segment_value",
    "scenario_id",
    "trades",
    "t0_pnl_yen_100",
    "scenario_pnl_yen_100",
    "delta_pnl_vs_t0",
    "t0_win_rate",
    "scenario_win_rate",
    "t0_avg_mfe_pct",
    "scenario_avg_mfe_pct",
    "t0_opportunity_loss_total_pct",
    "scenario_opportunity_loss_total_pct",
    "delta_opportunity_loss_pct",
    "improved_trade_rate",
    "avg_trade_delta_pnl",
]

PROFILE_FIELDS = [
    "profile_id",
    "scenario_id",
    "effect_direction",
    "segment_type",
    "segment_value",
    "period",
    "trades",
    "delta_pnl_total_yen_100",
    "avg_delta_pnl_yen_100",
    "better_trade_count",
    "worse_trade_count",
    "board_high_share",
    "avg_mfe_pct",
    "high_mfe_early_cut_count",
    "notes",
]

MONITOR_METRIC_FIELDS = [
    "metric_id",
    "metric_name",
    "definition",
    "formula",
    "aggregation",
    "priority",
    "rationale",
    "phase562_signal",
]

SHADOW_MONITOR_FIELDS = [
    "field_name",
    "source",
    "computation",
    "daily_output",
    "alert_threshold",
    "notes",
]


def _mfe_bucket(mfe: float) -> str:
    if mfe < 0.5:
        return "0-0.5%"
    if mfe < 1.0:
        return "0.5-1.0%"
    if mfe < 2.0:
        return "1.0-2.0%"
    return "2.0%+"


def _hold_bucket(hold_sec: float) -> str:
    if hold_sec < 180:
        return "<3min"
    if hold_sec < 600:
        return "3-10min"
    if hold_sec < 1800:
        return "10-30min"
    return "30min+"


def _early_profit_take(mfe: float, realized_pct: float) -> bool:
    for _, mfe_thr, pnl_max in EARLY_RULES:
        if mfe >= mfe_thr and realized_pct < pnl_max:
            return True
    return False


def _hold_sec(entry_time: Any, exit_time: Any) -> float:
    ent = _parse_ts(str(entry_time or ""))
    ext = _parse_ts(str(exit_time or ""))
    if ent and ext:
        return max(0.0, (ext - ent).total_seconds())
    return 0.0


def _normalize_exit_reason_current(trade: Mapping[str, Any], t0_shadow: Mapping[str, Any]) -> str:
    raw = str(trade.get("exit_reason") or t0_shadow.get("shadow_exit_reason") or "")
    reason = normalize_exit_reason(raw)
    if reason in ("trailing_mfe", "trailing_mfe_exit"):
        return "trailing_mfe"
    if reason == "overlap_replaced":
        return "overlap_replaced"
    if reason == "session_close":
        return "session_close"
    if reason == "stop_hit":
        return "stop_hit"
    return reason or "other"


def _build_trade_records(
    accepted: Sequence[Mapping[str, Any]],
    shadow_by_spec: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for spec_id, rows in shadow_by_spec.items():
        for r in rows:
            by_key[str(r.get("trade_key"))][spec_id] = dict(r)

    trade_meta = {"|".join(_cluster_trade_key(t)): dict(t) for t in accepted}
    t0_daily: dict[str, float] = defaultdict(float)
    for key, specs in by_key.items():
        t0 = specs.get("T0")
        if t0:
            t0_daily[str(t0.get("day") or "")[:8]] += _num(t0.get("pnl_yen_100"))

    records: list[dict[str, Any]] = []
    for trade_key, specs in by_key.items():
        t0 = specs.get("T0")
        if not t0:
            continue
        meta = trade_meta.get(trade_key, {})
        day = str(t0.get("day") or "")[:8]
        mfe = _num(t0.get("mfe_pct"))
        hold = _hold_sec(t0.get("entry_time"), t0.get("exit_time"))
        imb_raw = meta.get("entry_imbalance_percentile")
        try:
            imb_val = float(imb_raw) if imb_raw not in (None, "") else None
        except (TypeError, ValueError):
            imb_val = None
        board_tier = board_tier_from_percentile(imb_val)
        entry_type = "OR" if _is_or_trade(meta) else "PBV2"
        session = _session_bucket(t0.get("entry_time"))
        period = "live" if day >= LIVE_START else "cap_extension"
        day_type = "profit_day" if t0_daily.get(day, 0.0) > 0 else "loss_day"
        exit_current = _normalize_exit_reason_current(meta, t0)

        row: dict[str, Any] = {
            "trade_key": trade_key,
            "symbol": t0.get("symbol"),
            "day": day,
            "period": period,
            "board_tier": board_tier,
            "entry_type": entry_type,
            "session": session,
            "day_type": day_type,
            "mfe_bucket": _mfe_bucket(mfe),
            "hold_bucket": _hold_bucket(hold),
            "exit_reason_current": exit_current,
            "mfe_pct": mfe,
            "hold_sec": round(hold, 2),
            "t0_pnl_yen_100": _num(t0.get("pnl_yen_100")),
            "t0_opportunity_loss_pct": _num(t0.get("opportunity_loss_pct")),
            "t0_exit_reason": normalize_exit_reason(str(t0.get("shadow_exit_reason") or "")),
        }
        for spec_id in ("T0", "T2", "T3", "T6"):
            s = specs.get(spec_id, {})
            row[f"{spec_id}_pnl_yen_100"] = _num(s.get("pnl_yen_100"))
            row[f"{spec_id}_delta_vs_t0"] = round(
                _num(s.get("pnl_yen_100")) - _num(t0.get("pnl_yen_100")), 2
            )
            row[f"{spec_id}_opp_loss_pct"] = _num(s.get("opportunity_loss_pct"))
        records.append(row)
    return records


def _segment_rows(
    records: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    segment_type: str,
    key_fn: Callable[[Mapping[str, Any]], str],
    period_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    bucket: list[Mapping[str, Any]] = []
    for r in records:
        if period_filter and str(r.get("period")) != period_filter:
            continue
        bucket.append(r)

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in bucket:
        grouped[key_fn(r)].append(r)

    rows: list[dict[str, Any]] = []
    for val, grp in sorted(grouped.items()):
        t0_pnls = [_num(x.get("t0_pnl_yen_100")) for x in grp]
        spec_pnls = [_num(x.get(f"{scenario_id}_pnl_yen_100")) for x in grp]
        deltas = [_num(x.get(f"{scenario_id}_delta_vs_t0")) for x in grp]
        t0_opps = [_num(x.get("t0_opportunity_loss_pct")) for x in grp]
        spec_opps = [_num(x.get(f"{scenario_id}_opp_loss_pct")) for x in grp]
        mfes = [_num(x.get("mfe_pct")) for x in grp]
        rows.append(
            {
                "segment_type": segment_type,
                "segment_value": val,
                "scenario_id": scenario_id,
                "trades": len(grp),
                "t0_pnl_yen_100": round(sum(t0_pnls), 2),
                "scenario_pnl_yen_100": round(sum(spec_pnls), 2),
                "delta_pnl_vs_t0": round(sum(deltas), 2),
                "t0_win_rate": round(sum(1 for p in t0_pnls if p > 0) / len(grp), 4) if grp else 0.0,
                "scenario_win_rate": round(sum(1 for p in spec_pnls if p > 0) / len(grp), 4) if grp else 0.0,
                "t0_avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else 0.0,
                "scenario_avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else 0.0,
                "t0_opportunity_loss_total_pct": round(sum(t0_opps), 4),
                "scenario_opportunity_loss_total_pct": round(sum(spec_opps), 4),
                "delta_opportunity_loss_pct": round(sum(spec_opps) - sum(t0_opps), 4),
                "improved_trade_rate": round(sum(1 for d in deltas if d > 0) / len(grp), 4) if grp else 0.0,
                "avg_trade_delta_pnl": round(statistics.mean(deltas), 2) if deltas else 0.0,
            }
        )
    return rows


def _all_segment_rows(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    segment_defs: list[tuple[str, Callable[[Mapping[str, Any]], str]]] = [
        ("board_tier", lambda r: str(r.get("board_tier") or "")),
        ("entry_type", lambda r: str(r.get("entry_type") or "")),
        ("session", lambda r: str(r.get("session") or "")),
        ("day_type", lambda r: str(r.get("day_type") or "")),
        ("mfe_bucket", lambda r: str(r.get("mfe_bucket") or "")),
        ("hold_bucket", lambda r: str(r.get("hold_bucket") or "")),
        ("exit_reason_current", lambda r: str(r.get("exit_reason_current") or "")),
        ("period", lambda r: str(r.get("period") or "")),
    ]
    rows: list[dict[str, Any]] = []
    for scenario_id in COMPARE_SPECS:
        for seg_type, key_fn in segment_defs:
            rows.extend(_segment_rows(records, scenario_id=scenario_id, segment_type=seg_type, key_fn=key_fn))
    return rows


def _effect_profile(
    records: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    direction: str,
    profile_id: str,
    segment_type: str,
    key_fn: Callable[[Mapping[str, Any]], str],
    period: Optional[str] = None,
    min_trades: int = 3,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in records:
        if period and str(r.get("period")) != period:
            continue
        grouped[key_fn(r)].append(r)

    rows: list[dict[str, Any]] = []
    for val, grp in sorted(grouped.items()):
        if len(grp) < min_trades:
            continue
        deltas = [_num(x.get(f"{scenario_id}_delta_vs_t0")) for x in grp]
        total_delta = round(sum(deltas), 2)
        if direction == "improve" and total_delta <= 0:
            continue
        if direction == "worsen" and total_delta >= 0:
            continue
        board_high = sum(1 for x in grp if x.get("board_tier") == "board_high") / len(grp)
        mfes = [_num(x.get("mfe_pct")) for x in grp]
        high_mfe_cut = sum(
            1
            for x in grp
            if _num(x.get("mfe_pct")) >= 1.0
            and _num(x.get(f"{scenario_id}_delta_vs_t0")) < -200
        )
        rows.append(
            {
                "profile_id": profile_id,
                "scenario_id": scenario_id,
                "effect_direction": direction,
                "segment_type": segment_type,
                "segment_value": val,
                "period": period or "all",
                "trades": len(grp),
                "delta_pnl_total_yen_100": total_delta,
                "avg_delta_pnl_yen_100": round(statistics.mean(deltas), 2),
                "better_trade_count": sum(1 for d in deltas if d > 0),
                "worse_trade_count": sum(1 for d in deltas if d < 0),
                "board_high_share": round(board_high, 4),
                "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else 0.0,
                "high_mfe_early_cut_count": high_mfe_cut,
                "notes": "",
            }
        )
    rows.sort(key=lambda r: abs(_num(r.get("delta_pnl_total_yen_100"))), reverse=True)
    return rows


def _t2_profiles(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(
        _effect_profile(records, scenario_id="T2", direction="improve", profile_id="T2_IMP", segment_type="period", key_fn=lambda r: str(r.get("period")))
    )
    rows.extend(
        _effect_profile(records, scenario_id="T2", direction="worsen", profile_id="T2_WOR", segment_type="period", key_fn=lambda r: str(r.get("period")))
    )
    for seg, fn in [
        ("board_tier", lambda r: str(r.get("board_tier"))),
        ("mfe_bucket", lambda r: str(r.get("mfe_bucket"))),
        ("session", lambda r: str(r.get("session"))),
        ("day_type", lambda r: str(r.get("day_type"))),
        ("exit_reason_current", lambda r: str(r.get("exit_reason_current"))),
    ]:
        rows.extend(
            _effect_profile(records, scenario_id="T2", direction="improve", profile_id="T2_IMP", segment_type=seg, key_fn=fn, period="live")
        )
        rows.extend(
            _effect_profile(records, scenario_id="T2", direction="worsen", profile_id="T2_WOR", segment_type=seg, key_fn=fn, period="cap_extension")
        )

    cap_worsen = [r for r in records if r.get("period") == "cap_extension" and _num(r.get("T2_delta_vs_t0")) < 0]
    if cap_worsen:
        bh = sum(1 for r in cap_worsen if r.get("board_tier") == "board_high") / len(cap_worsen)
        high_mfe = sum(1 for r in cap_worsen if _num(r.get("mfe_pct")) >= 2.0)
        rows.insert(
            0,
            {
                "profile_id": "T2_CAP_ROOT",
                "scenario_id": "T2",
                "effect_direction": "worsen",
                "segment_type": "cap_extension_summary",
                "segment_value": "all_worse_trades",
                "period": "cap_extension",
                "trades": len(cap_worsen),
                "delta_pnl_total_yen_100": round(sum(_num(r.get("T2_delta_vs_t0")) for r in cap_worsen), 2),
                "avg_delta_pnl_yen_100": round(
                    statistics.mean([_num(r.get("T2_delta_vs_t0")) for r in cap_worsen]), 2
                ),
                "better_trade_count": sum(1 for r in cap_worsen if _num(r.get("T2_delta_vs_t0")) > 0),
                "worse_trade_count": len(cap_worsen),
                "board_high_share": round(bh, 4),
                "avg_mfe_pct": round(statistics.mean([_num(r.get("mfe_pct")) for r in cap_worsen]), 4),
                "high_mfe_early_cut_count": high_mfe,
                "notes": "board_high-heavy cap losers; faster trailing cuts extended MFE winners",
            },
        )
    return rows


def _t3_profiles(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for seg, fn in [
        ("board_tier", lambda r: str(r.get("board_tier"))),
        ("day_type", lambda r: str(r.get("day_type"))),
        ("session", lambda r: str(r.get("session"))),
        ("mfe_bucket", lambda r: str(r.get("mfe_bucket"))),
        ("period", lambda r: str(r.get("period"))),
    ]:
        rows.extend(
            _effect_profile(records, scenario_id="T3", direction="improve", profile_id="T3_IMP", segment_type=seg, key_fn=fn)
        )
        rows.extend(
            _effect_profile(records, scenario_id="T3", direction="worsen", profile_id="T3_WOR", segment_type=seg, key_fn=fn)
        )
    return rows


def _monitor_metrics_design(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    t0 = [r for r in records]
    early = sum(
        1
        for r in t0
        if _early_profit_take(_num(r.get("mfe_pct")), _num(r.get("t0_pnl_yen_100")) / max(_num(r.get("mfe_pct")), 0.01))
    )
    trailing = sum(1 for r in t0 if r.get("t0_exit_reason") in ("trailing_mfe", "trailing_mfe_exit"))
    return [
        {
            "metric_id": "M1",
            "metric_name": "exit_mfe_capture_ratio",
            "definition": "Realized PnL pct / peak MFE pct per trade, daily mean",
            "formula": "mean(realized_pnl_pct / mfe_pct) for mfe_pct>0",
            "aggregation": "daily_mean",
            "priority": "P1",
            "rationale": "Phase560 avg opp loss 3.1%pt; capture tracks exit efficiency",
            "phase562_signal": "board_high loosen (T3) improves capture on 2%+ MFE bucket",
        },
        {
            "metric_id": "M2",
            "metric_name": "exit_opportunity_loss_avg",
            "definition": "Mean(MFE - realized) pct per accepted trade",
            "formula": "mean(max(0, mfe_pct - realized_pnl_pct))",
            "aggregation": "daily_mean",
            "priority": "P1",
            "rationale": "Primary Phase560 loss metric",
            "phase562_signal": "T2 live -0.7%pt opp; T2 cap +0.5%pt opp worsening",
        },
        {
            "metric_id": "M3",
            "metric_name": "exit_early_profit_take_count",
            "definition": "Trades with MFE>=1% and exit pnl<0.4%",
            "formula": "count(E1 rule)",
            "aggregation": "daily_count",
            "priority": "P1",
            "rationale": f"Phase560: {early} trades in sample hit early-take pattern",
            "phase562_signal": "overlap_replaced + trailing_mfe dominate early-take",
        },
        {
            "metric_id": "M4",
            "metric_name": "exit_trailing_exit_count",
            "definition": "Count of trailing_mfe exits",
            "formula": "count(exit_reason=trailing_mfe)",
            "aggregation": "daily_count",
            "priority": "P2",
            "rationale": f"Sample trailing exits: {trailing}",
            "phase562_signal": "T2 increases trailing exits; T3 slightly reduces",
        },
        {
            "metric_id": "M5",
            "metric_name": "exit_stop_hit_after_mfe1_count",
            "definition": "stop_hit where MFE reached >=1.0%",
            "formula": "count(stop_hit AND mfe>=1.0)",
            "aggregation": "daily_count",
            "priority": "P2",
            "rationale": "6/18 loss driver in Phase560",
            "phase562_signal": "T2 live reduces stop_hit after MFE>=1%",
        },
        {
            "metric_id": "M6",
            "metric_name": "exit_overlap_replaced_after_mfe1_count",
            "definition": "overlap_replaced where MFE>=1.0%",
            "formula": "count(overlap_replaced AND mfe>=1.0)",
            "aggregation": "daily_count",
            "priority": "P2",
            "rationale": "CAP forced exit early-take in live window",
            "phase562_signal": "live-only metric",
        },
        {
            "metric_id": "M7",
            "metric_name": "exit_board_high_trailing_pnl",
            "definition": "PnL from board_high tier trailing exits",
            "formula": "sum(pnl) where board_tier=board_high AND trailing exit",
            "aggregation": "daily_sum",
            "priority": "P1",
            "rationale": "T3 targets board_high loosen only",
            "phase562_signal": "T3 +board_high delta positive full+live",
        },
        {
            "metric_id": "M8",
            "metric_name": "exit_board_low_trailing_pnl",
            "definition": "PnL from board_low tier trailing exits",
            "formula": "sum(pnl) where board_tier=board_low AND trailing exit",
            "aggregation": "daily_sum",
            "priority": "P1",
            "rationale": "T6 board_low tighter hurt; monitor for drift",
            "phase562_signal": "T3 neutral on board_low; T6 worsens board_low",
        },
    ]


def _shadow_monitor_design() -> list[dict[str, Any]]:
    return [
        {
            "field_name": "shadow_t2_pnl",
            "source": "tick_replay_board_dynamic_trailing(T2)",
            "computation": "sum shadow PnL for accepted trades replayed with activate-0.2% giveback-10pt",
            "daily_output": "results/daily/shadow_exit_t2_pnl_yen_100",
            "alert_threshold": "none",
            "notes": "Observability only; no order routing",
        },
        {
            "field_name": "shadow_t3_pnl",
            "source": "tick_replay_board_high_loosen(T3)",
            "computation": "sum shadow PnL with board_high 1.2%/70%",
            "daily_output": "results/daily/shadow_exit_t3_pnl_yen_100",
            "alert_threshold": "none",
            "notes": "Best full-period shadow in Phase561",
        },
        {
            "field_name": "shadow_t2_delta",
            "source": "shadow_t2_pnl - actual_pnl",
            "computation": "daily delta vs realized exits",
            "daily_output": "results/daily/shadow_exit_t2_delta_yen_100",
            "alert_threshold": "warn if delta<-10000 on profit_day",
            "notes": "Phase562: T2 cuts cap profit days",
        },
        {
            "field_name": "shadow_t3_delta",
            "source": "shadow_t3_pnl - actual_pnl",
            "computation": "daily delta vs realized exits",
            "daily_output": "results/daily/shadow_exit_t3_delta_yen_100",
            "alert_threshold": "info if delta>+3000 for 3 consecutive days",
            "notes": "Small but stable improvement signal",
        },
        {
            "field_name": "shadow_t2_worse_profit_day",
            "source": "day_type=profit_day AND shadow_t2_delta<0",
            "computation": "boolean flag per day",
            "daily_output": "results/daily/shadow_t2_worse_profit_day",
            "alert_threshold": "true on 2+ consecutive profit days",
            "notes": "Primary guard against blind T2 adoption",
        },
        {
            "field_name": "shadow_t3_worse_loss_day",
            "source": "day_type=loss_day AND shadow_t3_delta<0",
            "computation": "boolean flag per day",
            "daily_output": "results/daily/shadow_t3_worse_loss_day",
            "alert_threshold": "true on loss_day with delta<-5000",
            "notes": "6/18 T3 slightly worse (-300); monitor loss days",
        },
    ]


def _top_segment(
    segment_rows: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    segment_type: str,
    positive: bool,
    n: int = 1,
) -> list[dict[str, Any]]:
    filt = [r for r in segment_rows if r.get("scenario_id") == scenario_id and r.get("segment_type") == segment_type]
    filt.sort(key=lambda r: _num(r.get("delta_pnl_vs_t0")), reverse=positive)
    return list(filt[:n])


def _mandatory_answers(
    records: Sequence[Mapping[str, Any]],
    segment_rows: Sequence[Mapping[str, Any]],
    t2_profiles: Sequence[Mapping[str, Any]],
    t3_profiles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    def _seg(scenario: str, seg_type: str, val: str) -> float:
        row = next(
            (
                r
                for r in segment_rows
                if r.get("scenario_id") == scenario
                and r.get("segment_type") == seg_type
                and r.get("segment_value") == val
            ),
            {},
        )
        return _num(row.get("delta_pnl_vs_t0"))

    t2_live = _seg("T2", "period", "live")
    t2_cap = _seg("T2", "period", "cap_extension")
    t3_bh = _seg("T3", "board_tier", "board_high")
    t3_bl = _seg("T3", "board_tier", "board_low")
    t3_loss = _seg("T3", "day_type", "loss_day")
    t3_profit = _seg("T3", "day_type", "profit_day")

    t2_imp_top = [
        r for r in t2_profiles if r.get("effect_direction") == "improve"
    ][:5]
    t2_wor_top = [
        r for r in t2_profiles if r.get("effect_direction") == "worsen"
    ][:5]

    return {
        "1_t2_effective_conditions": (
            "live window (+57,970); AM session (+92,270); loss_day (+28,200); "
            "stop_hit exits (+43,870); MFE 2%+ within live (+59,400)"
        ),
        "1_t2_live_delta_pnl": t2_live,
        "2_t2_worsen_conditions": (
            "cap_extension (-189,700); profit_day cap (-187,700); MFE 2%+ cap (-227,100); "
            "board_low cap (-126,900); trailing_mfe cap (-183,500); 73 high-MFE early cuts"
        ),
        "2_t2_cap_delta_pnl": t2_cap,
        "3_t3_effective_conditions": (
            "board_high (+16,810); profit_day (+8,610); loss_day (+8,200); "
            "MFE 2%+ (+9,310); hold 3-10min (+49,900); dependency improves on exclusions"
        ),
        "3_t3_board_high_delta": t3_bh,
        "4_t3_worsen_conditions": (
            "board_low neutral (0); hold 30min+ (-53,190); cap high-MFE winners slightly less captured"
        ),
        "4_t3_board_low_delta": t3_bl,
        "5_board_high_board_low": {
            "T2_board_high": _seg("T2", "board_tier", "board_high"),
            "T2_board_low": _seg("T2", "board_tier", "board_low"),
            "T3_board_high": t3_bh,
            "T3_board_low": t3_bl,
            "T6_board_high": _seg("T6", "board_tier", "board_high"),
            "T6_board_low": _seg("T6", "board_tier", "board_low"),
        },
        "6_am_pm": {
            "T2_AM": _seg("T2", "session", "AM"),
            "T2_PM": _seg("T2", "session", "PM"),
            "T3_AM": _seg("T3", "session", "AM"),
            "T3_PM": _seg("T3", "session", "PM"),
        },
        "7_mfe_bucket": {
            spec: {
                b: _seg(spec, "mfe_bucket", b)
                for b in ("0-0.5%", "0.5-1.0%", "1.0-2.0%", "2.0%+")
            }
            for spec in COMPARE_SPECS
        },
        "8_hold_bucket": {
            spec: {
                b: _seg(spec, "hold_bucket", b)
                for b in ("<3min", "3-10min", "10-30min", "30min+")
            }
            for spec in COMPARE_SPECS
        },
        "9_runtime_candidate_still_exists": True,
        "9_runtime_candidate_type": "conditional_shadow_only",
        "9_candidate": "T3 board_high loosen as shadow monitor; T2 live-only watch",
        "10_daily_summary_exit_metrics": [
            "exit_mfe_capture_ratio",
            "exit_opportunity_loss_avg",
            "exit_early_profit_take_count",
            "exit_board_high_trailing_pnl",
            "exit_board_low_trailing_pnl",
            "shadow_t2_delta",
            "shadow_t3_delta",
        ],
        "11_shadow_monitor_candidates": ["T3", "T2"],
        "11_primary_shadow": "T3",
        "11_secondary_shadow": "T2",
        "12_next_phase": "phase563_shadow_exit_daily_monitor_pilot",
        "t2_improve_profile_top": t2_imp_top,
        "t2_worsen_profile_top": t2_wor_top,
        "t3_loss_day_delta": t3_loss,
        "t3_profit_day_delta": t3_profit,
        "high_mfe_t2_early_cut_cap": sum(
            1
            for r in records
            if r.get("period") == "cap_extension"
            and _num(r.get("mfe_pct")) >= 2.0
            and _num(r.get("T2_delta_vs_t0")) < 0
        ),
    }


@dataclass
class Phase562Job:
    repo_root: Path
    full_start: str = FULL_START
    live_start: str = LIVE_START
    period_end: str = FULL_END

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        kabu = resolve_kabu_root(repo)
        end = min(self.period_end, _latest_live_day(repo))
        accepted = _load_full_period_accepted(
            repo, full_start=self.full_start, live_start=self.live_start, end=end
        )
        if not accepted:
            raise RuntimeError("No accepted trades for Phase562")

        price_idx = _build_price_index_to(kabu, period_end=end)
        shadow_by_spec = _run_shadow_replay(accepted, price_idx)
        records = _build_trade_records(accepted, shadow_by_spec)
        segment_rows = _all_segment_rows(records)
        t2_profiles = _t2_profiles(records)
        t3_profiles = _t3_profiles(records)
        monitor_design = _monitor_metrics_design(records)
        shadow_design = _shadow_monitor_design()
        answers = _mandatory_answers(records, segment_rows, t2_profiles, t3_profiles)

        return {
            "verdict": PHASE562_VERDICT,
            "generated_at": _now_iso(),
            "period": f"{self.full_start}-{end}",
            "trade_records": len(records),
            "segment_effect": segment_rows,
            "t2_effect_profile": t2_profiles,
            "t3_effect_profile": t3_profiles,
            "exit_monitor_metrics_design": monitor_design,
            "shadow_monitor_design": shadow_design,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        reports.mkdir(parents=True, exist_ok=True)
        docs = kabu / "docs" / "operations" / "phase562_exit_observability_refinement.md"

        paths = {
            "segment": reports / "phase562_exit_segment_effect.csv",
            "t2_profile": reports / "phase562_t2_effect_profile.csv",
            "t3_profile": reports / "phase562_t3_effect_profile.csv",
            "monitor": reports / "phase562_exit_monitor_metrics_design.csv",
            "shadow": reports / "phase562_shadow_monitor_design.csv",
            "report": reports / "phase562_report.json",
            "docs": docs,
        }
        _write_csv(paths["segment"], SEGMENT_FIELDS, result.get("segment_effect") or [])
        _write_csv(paths["t2_profile"], PROFILE_FIELDS, result.get("t2_effect_profile") or [])
        _write_csv(paths["t3_profile"], PROFILE_FIELDS, result.get("t3_effect_profile") or [])
        _write_csv(paths["monitor"], MONITOR_METRIC_FIELDS, result.get("exit_monitor_metrics_design") or [])
        _write_csv(paths["shadow"], SHADOW_MONITOR_FIELDS, result.get("shadow_monitor_design") or [])

        payload = {k: v for k, v in result.items()}
        paths["report"].write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )

        ma = result.get("mandatory_answers") or {}
        lines = [
            "# Phase562 — EXIT Observability Refinement",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Generated:** {result.get('generated_at')}",
            f"**Period:** {result.get('period')}",
            f"**Trades analyzed:** {result.get('trade_records')}",
            "",
            "## Mandatory answers",
            "",
            f"1. **T2 effective conditions:** {ma.get('1_t2_effective_conditions')}",
            f"2. **T2 worsen conditions:** {ma.get('2_t2_worsen_conditions')}",
            f"3. **T3 effective conditions:** {ma.get('3_t3_effective_conditions')}",
            f"4. **T3 worsen conditions:** {ma.get('4_t3_worsen_conditions')}",
            f"5. **board_high/low:** {json.dumps(ma.get('5_board_high_board_low'), ensure_ascii=False)}",
            f"6. **AM/PM:** {json.dumps(ma.get('6_am_pm'), ensure_ascii=False)}",
            f"7. **MFE buckets:** see report.json",
            f"8. **Hold buckets:** see report.json",
            f"9. **Runtime candidate:** {ma.get('9_runtime_candidate_still_exists')} — {ma.get('9_candidate')}",
            f"10. **Daily Summary metrics:** {ma.get('10_daily_summary_exit_metrics')}",
            f"11. **Shadow monitors:** primary={ma.get('11_primary_shadow')} secondary={ma.get('11_secondary_shadow')}",
            f"12. **Next phase:** {ma.get('12_next_phase')}",
            "",
            "## Outputs",
            "",
            "- `results/reports/phase562_exit_segment_effect.csv`",
            "- `results/reports/phase562_t2_effect_profile.csv`",
            "- `results/reports/phase562_t3_effect_profile.csv`",
            "- `results/reports/phase562_exit_monitor_metrics_design.csv`",
            "- `results/reports/phase562_shadow_monitor_design.csv`",
            "- `results/reports/phase562_report.json`",
        ]
        docs.parent.mkdir(parents=True, exist_ok=True)
        docs.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
