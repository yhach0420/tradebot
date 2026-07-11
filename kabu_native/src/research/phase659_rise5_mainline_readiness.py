"""
Phase659: Rise5 Mainline Promotion Readiness (research only).

Final readiness review for promoting pbv2_rise5_shadow to mainline ENTRY guard.
Uses Phase634 full-period PBv2 trades. No ENTRY/EXIT/YAML/runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase631_profit_source_attribution import _num
from research.phase632_pbv2_profit_filter_counterfactual import _metrics, _profit_factor
from research.phase634_pbv2_only_rise5_full_period import (
    PRE625_CUTOFF,
    _session_bucket,
    load_all_full_period_trades,
)
from research.phase649_flat_band_guard_counterfactual import (
    block_flat_plus_overheat,
    block_phase635_rise5_shadow,
    filter_pbv2_trades,
)
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_misread_guard

PHASE659_VERDICT = "phase659_rise5_mainline_readiness_done"
REPORT_DIR_NAME = "phase659_rise5_mainline_readiness"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
RISE5_THRESHOLD = 1.84
RECENT_TRADING_DAYS = 5
TRIAL_MIN_FORWARD_SESSIONS = 5
TRIAL_TARGET_FORWARD_SESSIONS = 10
BIG_WINNER_YEN = 5000.0

WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _weekday(day_iso: str) -> str:
    try:
        return WEEKDAY_LABELS[datetime.strptime(day_iso, "%Y-%m-%d").weekday()]
    except ValueError:
        return "unknown"


def _pnl(t: Mapping[str, Any]) -> float:
    return float(t.get("pnl_yen_100") or 0.0)


def rise5_blocks(t: Mapping[str, Any], *, threshold: float = RISE5_THRESHOLD) -> bool:
    return block_phase635_rise5_shadow(t, threshold)


def rise5_keeps(t: Mapping[str, Any], *, threshold: float = RISE5_THRESHOLD) -> bool:
    return not rise5_blocks(t, threshold=threshold)


def _blocked_winners(blocked: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(t) for t in blocked if _pnl(t) > 0]


def _rescued_losers(blocked: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(t) for t in blocked if _pnl(t) < 0]


def _delta_metrics(base: Sequence[Mapping[str, Any]], kept: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    mb = _metrics(list(base))
    mk = _metrics(list(kept))
    bpf = mb.get("profit_factor")
    kpf = mk.get("profit_factor")
    delta_pf = None
    if isinstance(bpf, (int, float)) and isinstance(kpf, (int, float)) and bpf != 999.0 and kpf != 999.0:
        delta_pf = round(float(kpf) - float(bpf), 4)
    return {
        "baseline_entries": mb["entry_count"],
        "kept_entries": mk["entry_count"],
        "blocked_entries": mb["entry_count"] - mk["entry_count"],
        "entry_reduction_pct": round(100.0 * (mb["entry_count"] - mk["entry_count"]) / max(1, mb["entry_count"]), 2),
        "baseline_pnl_yen": mb["pnl_yen_100"],
        "kept_pnl_yen": mk["pnl_yen_100"],
        "delta_pnl_yen": round(float(mk["pnl_yen_100"]) - float(mb["pnl_yen_100"]), 2),
        "baseline_pf": bpf,
        "kept_pf": kpf,
        "delta_pf": delta_pf,
        "baseline_dd_yen": mb["max_dd_yen_100"],
        "kept_dd_yen": mk["max_dd_yen_100"],
        "delta_dd_yen": round(float(mk["max_dd_yen_100"]) - float(mb["max_dd_yen_100"]), 2),
        "baseline_win_rate": mb.get("win_rate"),
        "kept_win_rate": mk.get("win_rate"),
    }


def _apply_rise5(base: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept = [dict(t) for t in base if rise5_keeps(t)]
    blocked = [dict(t) for t in base if rise5_blocks(t)]
    return kept, blocked


def daily_breakdown(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day") or "")].append(dict(t))

    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        dt = by_day[day]
        kept, blocked = _apply_rise5(dt)
        m = _delta_metrics(dt, kept)
        bw = _blocked_winners(blocked)
        rl = _rescued_losers(blocked)
        rows.append(
            {
                "day": day,
                "weekday": _weekday(day),
                "period": "post625" if day >= PRE625_CUTOFF else "pre625",
                "session_AM_baseline_pnl": round(sum(_pnl(t) for t in dt if _session_bucket(t) == "AM"), 2),
                "session_AM_delta_pnl": round(
                    sum(_pnl(t) for t in kept if _session_bucket(t) == "AM")
                    - sum(_pnl(t) for t in dt if _session_bucket(t) == "AM"),
                    2,
                ),
                "session_PM_baseline_pnl": round(sum(_pnl(t) for t in dt if _session_bucket(t) == "PM"), 2),
                "session_PM_delta_pnl": round(
                    sum(_pnl(t) for t in kept if _session_bucket(t) == "PM")
                    - sum(_pnl(t) for t in dt if _session_bucket(t) == "PM"),
                    2,
                ),
                "baseline_entries": m["baseline_entries"],
                "kept_entries": m["kept_entries"],
                "blocked_entries": m["blocked_entries"],
                "entry_reduction_pct": m["entry_reduction_pct"],
                "baseline_pnl_yen": m["baseline_pnl_yen"],
                "kept_pnl_yen": m["kept_pnl_yen"],
                "delta_pnl_yen": m["delta_pnl_yen"],
                "delta_pf": m["delta_pf"],
                "blocked_winners": len(bw),
                "blocked_winners_pnl": round(sum(_pnl(t) for t in bw), 2),
                "rescued_losers": len(rl),
                "rescued_losers_pnl": round(sum(_pnl(t) for t in rl), 2),
            }
        )
    return rows


def weekday_summary(daily_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_wd: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        by_wd[str(row.get("weekday") or "unknown")].append(dict(row))
    out: list[dict[str, Any]] = []
    for wd in WEEKDAY_LABELS:
        rows = by_wd.get(wd) or []
        if not rows:
            continue
        out.append(
            {
                "weekday": wd,
                "day_count": len(rows),
                "baseline_pnl_yen": round(sum(float(r.get("baseline_pnl_yen") or 0) for r in rows), 2),
                "delta_pnl_yen": round(sum(float(r.get("delta_pnl_yen") or 0) for r in rows), 2),
                "positive_delta_days": sum(1 for r in rows if float(r.get("delta_pnl_yen") or 0) > 0),
                "negative_delta_days": sum(1 for r in rows if float(r.get("delta_pnl_yen") or 0) < 0),
            }
        )
    return out


def am_pm_summary(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in ("AM", "PM", "lunch"):
        sub = [dict(t) for t in trades if _session_bucket(t) == bucket]
        if not sub:
            continue
        kept, blocked = _apply_rise5(sub)
        m = _delta_metrics(sub, kept)
        rows.append({"session_bucket": bucket, **m, "blocked_winners": len(_blocked_winners(blocked))})
    return rows


def leave_one_day_out(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day") or "")].append(dict(t))
    full_kept, _ = _apply_rise5(trades)
    full_delta = float(_delta_metrics(trades, full_kept)["delta_pnl_yen"])
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        rest = [t for d, seq in by_day.items() if d != day for t in seq]
        kept, _ = _apply_rise5(rest)
        d = float(_delta_metrics(rest, kept)["delta_pnl_yen"])
        rows.append(
            {
                "excluded_day": day,
                "weekday": _weekday(day),
                "excluded_day_delta_pnl": round(float(by_day[day] and _delta_metrics(by_day[day], [t for t in by_day[day] if rise5_keeps(t)])["delta_pnl_yen"]), 2),
                "delta_pnl_without_day": round(d, 2),
                "full_period_delta_pnl": full_delta,
                "still_positive": d > 0,
                "share_of_total_delta": round(abs(d) / abs(full_delta), 4) if abs(full_delta) > 1e-6 else None,
            }
        )
    return rows


def leave_one_symbol_out(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))
    full_kept, _ = _apply_rise5(trades)
    full_delta = float(_delta_metrics(trades, full_kept)["delta_pnl_yen"])
    sym_deltas: list[tuple[str, float]] = []
    for sym, seq in by_sym.items():
        kept, _ = _apply_rise5(seq)
        sym_deltas.append((sym, float(_delta_metrics(seq, kept)["delta_pnl_yen"])))
    sym_deltas.sort(key=lambda x: x[1], reverse=True)
    rows: list[dict[str, Any]] = []
    for sym, sym_delta in sym_deltas[:25]:
        rest = [t for s, seq in by_sym.items() if s != sym for t in seq]
        kept, _ = _apply_rise5(rest)
        d_wo = float(_delta_metrics(rest, kept)["delta_pnl_yen"])
        rows.append(
            {
                "symbol": sym,
                "symbol_delta_pnl_yen": round(sym_delta, 2),
                "delta_pnl_without_symbol": round(d_wo, 2),
                "full_period_delta_pnl": full_delta,
                "still_positive": d_wo > 0,
                "share_of_total_delta": round(abs(sym_delta) / abs(full_delta), 4) if abs(full_delta) > 1e-6 else None,
                "symbol_trade_count": len(by_sym[sym]),
            }
        )
    return rows


def flat_band_overlap(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rise5_blk = [t for t in trades if rise5_blocks(t)]
    flat_blk = [t for t in trades if block_flat_plus_overheat(t)]
    rise5_keys = {(t.get("day"), t.get("symbol"), t.get("entry_time")) for t in rise5_blk}
    flat_keys = {(t.get("day"), t.get("symbol"), t.get("entry_time")) for t in flat_blk}
    overlap_keys = rise5_keys & flat_keys
    rise5_only = rise5_keys - flat_keys
    flat_only = flat_keys - rise5_keys
    overlap_trades = [t for t in trades if (t.get("day"), t.get("symbol"), t.get("entry_time")) in overlap_keys]
    return {
        "rise5_blocked_count": len(rise5_blk),
        "flat_band_blocked_count": len(flat_blk),
        "overlap_count": len(overlap_keys),
        "rise5_only_count": len(rise5_only),
        "flat_only_count": len(flat_only),
        "overlap_pct_of_rise5": round(100.0 * len(overlap_keys) / max(1, len(rise5_keys)), 2),
        "overlap_pct_of_flat": round(100.0 * len(overlap_keys) / max(1, len(flat_keys)), 2),
        "overlap_blocked_pnl_yen": round(sum(_pnl(t) for t in overlap_trades), 2),
        "rise5_only_blocked_pnl_yen": round(
            sum(_pnl(t) for t in rise5_blk if (t.get("day"), t.get("symbol"), t.get("entry_time")) in rise5_only), 2
        ),
    }


def keep_shadow_competition(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    shadow_fns: list[tuple[str, Any]] = [
        ("pullback_misread_guard_shadow", would_block_pullback_misread_guard),
        ("pbv2_flat_band_shadow", block_flat_plus_overheat),
        ("vwap_shadow_reject", lambda t: _num(t.get("entry_vwap_dev_pct")) is not None and float(_num(t.get("entry_vwap_dev_pct")) or 0) >= 2.5),
    ]
    rise5_keys = {(t.get("day"), t.get("symbol"), t.get("entry_time")) for t in trades if rise5_blocks(t)}
    rows: list[dict[str, Any]] = []
    for sid, fn in shadow_fns:
        other_keys = {(t.get("day"), t.get("symbol"), t.get("entry_time")) for t in trades if fn(t)}
        overlap = rise5_keys & other_keys
        only_rise5 = rise5_keys - other_keys
        only_other = other_keys - rise5_keys
        rows.append(
            {
                "competing_shadow_id": sid,
                "rise5_blocked_count": len(rise5_keys),
                "other_blocked_count": len(other_keys),
                "overlap_count": len(overlap),
                "rise5_only_count": len(only_rise5),
                "other_only_count": len(only_other),
                "overlap_pct_of_rise5": round(100.0 * len(overlap) / max(1, len(rise5_keys)), 2),
                "verdict": "low_conflict" if len(overlap) / max(1, len(rise5_keys)) < 0.35 else "moderate_conflict",
            }
        )
    return rows


def recent_5day_attribution(
    trades: Sequence[Mapping[str, Any]], daily_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    days = sorted({str(t.get("day") or "") for t in trades})
    recent = days[-RECENT_TRADING_DAYS:] if days else []
    recent_trades = [dict(t) for t in trades if str(t.get("day") or "") in recent]
    kept, blocked = _apply_rise5(recent_trades)
    m = _delta_metrics(recent_trades, kept)
    daily_recent = [r for r in daily_rows if str(r.get("day") or "") in recent]

    sym_pnl: dict[str, float] = defaultdict(float)
    sym_delta: dict[str, float] = defaultdict(float)
    for t in recent_trades:
        sym = str(t.get("symbol") or "")
        sym_pnl[sym] += _pnl(t)
        if rise5_blocks(t):
            sym_delta[sym] -= _pnl(t)

    top_hurt_symbols = sorted(sym_delta.items(), key=lambda x: x[1])[:5]
    top_help_symbols = sorted(sym_delta.items(), key=lambda x: x[1], reverse=True)[:5]

    am_base = sum(_pnl(t) for t in recent_trades if _session_bucket(t) == "AM")
    pm_base = sum(_pnl(t) for t in recent_trades if _session_bucket(t) == "PM")
    am_delta = sum(_pnl(t) for t in kept if _session_bucket(t) == "AM") - am_base
    pm_delta = sum(_pnl(t) for t in kept if _session_bucket(t) == "PM") - pm_base

    negative_days = [r for r in daily_recent if float(r.get("delta_pnl_yen") or 0) < 0]
    baseline_daily = [float(r.get("baseline_pnl_yen") or 0) for r in daily_recent]
    avg_base = statistics.fmean(baseline_daily) if baseline_daily else 0.0

    causes: list[str] = []
    if m["delta_pnl_yen"] < 0:
        if avg_base < 0:
            causes.append("market_regime_loss_days")
        if any(abs(v) > 20000 for _, v in top_hurt_symbols):
            causes.append("symbol_concentration")
        if abs(am_delta) > abs(pm_delta) and am_delta < 0:
            causes.append("AM_session_weakness")
        elif pm_delta < 0:
            causes.append("PM_session_weakness")
        if m["entry_reduction_pct"] > 8:
            causes.append("entry_count_drop")
        if len(negative_days) >= 3 and not causes:
            causes.append("variance_noise")
        if not causes:
            causes.append("mixed_small_effects")

    return {
        "recent_trading_days": recent,
        "recent_delta_pnl_yen": m["delta_pnl_yen"],
        "recent_entry_reduction_pct": m["entry_reduction_pct"],
        "recent_blocked_winners": len(_blocked_winners(blocked)),
        "recent_rescued_losers": len(_rescued_losers(blocked)),
        "am_delta_yen": round(am_delta, 2),
        "pm_delta_yen": round(pm_delta, 2),
        "avg_baseline_daily_pnl": round(avg_base, 2),
        "negative_delta_day_count": len(negative_days),
        "top_hurt_symbols": [{"symbol": s, "delta_yen": round(v, 2)} for s, v in top_hurt_symbols],
        "top_help_symbols": [{"symbol": s, "delta_yen": round(v, 2)} for s, v in top_help_symbols],
        "attributed_causes": causes,
    }


def consecutive_negative_streak(daily_rows: Sequence[Mapping[str, Any]]) -> int:
    best = cur = 0
    for row in sorted(daily_rows, key=lambda r: str(r.get("day") or "")):
        if float(row.get("delta_pnl_yen") or 0) < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def rollback_rules(daily_rows: Sequence[Mapping[str, Any]], overall: Mapping[str, Any]) -> dict[str, Any]:
    daily_deltas = [float(r.get("delta_pnl_yen") or 0) for r in daily_rows]
    bw_daily = [int(r.get("blocked_winners") or 0) for r in daily_rows]
    p95_bw = sorted(bw_daily)[int(0.95 * max(0, len(bw_daily) - 1))] if bw_daily else 0
    max_streak = consecutive_negative_streak(daily_rows)
    return {
        "rules": [
            {
                "id": "five_day_negative_streak",
                "condition": "5 consecutive trading days with rise5 delta_pnl < 0",
                "threshold": 5,
                "historical_max_streak": max_streak,
                "would_have_triggered_full_period": max_streak >= 5,
            },
            {
                "id": "trial_cumulative_negative",
                "condition": "Trial cumulative rise5 net_effect_yen < 0 over rolling 5 sessions",
                "threshold": 0,
            },
            {
                "id": "trial_pf_drop",
                "condition": "Trial kept PF drops > 0.05 vs baseline PF",
                "threshold": 0.05,
            },
            {
                "id": "blocked_winners_spike",
                "condition": f"Single-day blocked_winners > p95 historical ({p95_bw})",
                "threshold": p95_bw,
                "historical_p95": p95_bw,
            },
            {
                "id": "big_winner_block_spike",
                "condition": f"Single-day blocked winner PnL > {BIG_WINNER_YEN * 3} yen",
                "threshold": BIG_WINNER_YEN * 3,
            },
        ],
        "recommended_off_actions": [
            "Set pbv2_rise5_shadow_apply_mode back to logging_only (no ENTRY block)",
            "Preserve summary + Discord counters for post-mortem",
            "Do not auto-enable flat_band in same session",
        ],
    }


def mainline_trial_plan() -> dict[str, Any]:
    return {
        "on_conditions": [
            "pbv2_rise5_shadow_enabled=true with apply_mode=block_entry (trial YAML branch only after ops sign-off)",
            "Prior session summary shows rise5_shadow_monitor_status=ok",
            "No active rollback rule triggered in prior 3 sessions",
            "Flat-band remains logging_only during rise5 trial",
        ],
        "off_conditions": [
            "Any rollback rule triggered",
            "2 consecutive sessions with negative rise5 net_effect_yen",
            "blocked_winners > 8 in a single session",
        ],
        "discord_monitor_items": [
            "pbv2_rise5_shadow_block_count / target_count",
            "pbv2_rise5_shadow_net_effect_yen",
            "blocked_winners / blocked_losers",
            "overlap_with_flat_band_shadow count",
            "AM/PM bucket net_effect",
        ],
        "summary_extra_fields": [
            "pbv2_rise5_shadow_trial_active",
            "pbv2_rise5_shadow_trial_session_index",
            "pbv2_rise5_shadow_rolling_5d_net_effect_yen",
            "pbv2_rise5_shadow_rollback_status",
        ],
        "min_forward_sessions": TRIAL_MIN_FORWARD_SESSIONS,
        "target_forward_sessions": TRIAL_TARGET_FORWARD_SESSIONS,
    }


def _final_verdict(
    overall: Mapping[str, Any],
    loo_days: Sequence[Mapping[str, Any]],
    loo_symbols: Sequence[Mapping[str, Any]],
    recent: Mapping[str, Any],
    overlap: Mapping[str, Any],
) -> tuple[str, str]:
    delta = float(overall.get("delta_pnl_yen") or 0)
    reduction = float(overall.get("entry_reduction_pct") or 0)
    bw = int(overall.get("blocked_winners") or 0)
    loo_day_ok = all(bool(r.get("still_positive")) for r in loo_days)
    top_sym_share = 0.0
    if loo_symbols and delta > 0:
        top = max(float(r.get("share_of_total_delta") or 0) for r in loo_symbols[:3])
        top_sym_share = top
    recent_delta = float(recent.get("recent_delta_pnl_yen") or 0)

    if delta < 0 or not loo_day_ok:
        return "REJECT", "Full-period or leave-one-day-out delta not consistently positive"
    if reduction > 15.0 or bw > 100:
        return "HOLD", "Entry reduction or blocked-winner count too high for immediate block promotion"
    if recent_delta < -10000:
        return "HOLD", "Recent 5 trading days show material negative counterfactual delta"
    if top_sym_share > 0.45:
        return "HOLD", "Top symbol concentration > 45% of total rise5 delta"
    rise5_only = int(overlap.get("rise5_only_count") or 0)
    if rise5_only < 10 and float(overlap.get("overlap_pct_of_rise5") or 0) > 70:
        return "HOLD", "Rise5 blocks almost entirely overlap flat-band; no unique guard value"
    if recent_delta < 0:
        return "HOLD", "Trial-first: recent 5 trading days show negative counterfactual delta; full-period strong"
    return "ADOPT", "Stable positive delta across LOO day; acceptable entry reduction and winner blocks"


@dataclass
class Phase659Job:
    repo_root: Path = field(default_factory=lambda: NATIVE_ROOT)

    def run(self) -> dict[str, Any]:
        trades, sessions = load_all_full_period_trades(self.repo_root / "results" / "small_paper")
        pbv2 = filter_pbv2_trades(trades)
        kept, blocked = _apply_rise5(pbv2)
        overall = _delta_metrics(pbv2, kept)
        overall["blocked_winners"] = len(_blocked_winners(blocked))
        overall["blocked_winners_pnl_yen"] = round(sum(_pnl(t) for t in _blocked_winners(blocked)), 2)
        overall["rescued_losers"] = len(_rescued_losers(blocked))
        overall["rescued_losers_pnl_yen"] = round(sum(_pnl(t) for t in _rescued_losers(blocked)), 2)
        overall["big_winners_blocked"] = sum(1 for t in blocked if _pnl(t) >= BIG_WINNER_YEN)

        daily = daily_breakdown(pbv2)
        weekday = weekday_summary(daily)
        am_pm = am_pm_summary(pbv2)
        loo_day = leave_one_day_out(pbv2)
        loo_sym = leave_one_symbol_out(pbv2)
        overlap = flat_band_overlap(pbv2)
        competition = keep_shadow_competition(pbv2)
        recent = recent_5day_attribution(pbv2, daily)
        rollback = rollback_rules(daily, overall)
        trial = mainline_trial_plan()
        verdict_label, verdict_reason = _final_verdict(overall, loo_day, loo_sym, recent, overlap)

        pre625 = [r for r in daily if str(r.get("period")) == "pre625"]
        post625 = [r for r in daily if str(r.get("period")) == "post625"]

        mandatory = {
            "1_mainline_adoptable": verdict_label in ("ADOPT", "HOLD"),
            "1_note": "HOLD = trial-first promotion; ADOPT = ready for guarded block trial",
            "2_adoption_risks": [
                f"blocked_winners={overall['blocked_winners']} (incl big_winner>={BIG_WINNER_YEN}: {overall.get('big_winners_blocked')})",
                f"recent_5d_delta_yen={recent['recent_delta_pnl_yen']}",
                f"flat_band_overlap_pct={overlap['overlap_pct_of_rise5']}%",
                "post625 period weaker than pre625 in historical replay",
            ],
            "3_rollback_conditions": rollback["rules"],
            "4_trial_period_sessions": f"{TRIAL_MIN_FORWARD_SESSIONS}-{TRIAL_TARGET_FORWARD_SESSIONS} forward sessions",
            "5_required_sample_size": f">={TRIAL_TARGET_FORWARD_SESSIONS} sessions AND >=22 trading days replay validated",
            "6_adopt_before_flat_band": True,
            "6_reason": "Lower overlap, lower entry reduction, fewer blocked winners than flat_band",
            "7_before_late_july_live_orders": verdict_label != "REJECT",
            "7_recommendation": "Enable block trial shadow before live orders if HOLD/ADOPT; not silent mainline",
            "8_final_verdict": verdict_label,
            "8_verdict_reason": verdict_reason,
        }

        risk_rows = [
            {
                "risk_id": "blocked_winners",
                "severity": "medium" if overall["blocked_winners"] < 80 else "high",
                "value": overall["blocked_winners"],
                "mitigation": "Rollback on blocked_winners spike; monitor big_winner blocks",
            },
            {
                "risk_id": "recent_5d_negative",
                "severity": "medium" if float(recent["recent_delta_pnl_yen"]) < 0 else "low",
                "value": recent["recent_delta_pnl_yen"],
                "mitigation": "Trial-only; compare AM/PM buckets daily",
            },
            {
                "risk_id": "symbol_concentration",
                "severity": "medium" if loo_sym and float(loo_sym[0].get("share_of_total_delta") or 0) > 0.25 else "low",
                "value": loo_sym[0].get("share_of_total_delta") if loo_sym else None,
                "mitigation": "Leave-one-symbol-out review each week",
            },
            {
                "risk_id": "flat_band_overlap",
                "severity": "low" if float(overlap["overlap_pct_of_rise5"]) < 35 else "medium",
                "value": overlap["overlap_pct_of_rise5"],
                "mitigation": "Promote rise5 before flat_band block mode",
            },
        ]

        return {
            "phase": "phase659_rise5_mainline_readiness",
            "verdict": PHASE659_VERDICT,
            "generated_at": _now_iso(),
            "rise5_threshold_pct": RISE5_THRESHOLD,
            "dataset": {
                "session_count": len(sessions),
                "trading_day_count": len({s["day"] for s in sessions}),
                "pbv2_trade_count": len(pbv2),
                "pre625_cutoff": PRE625_CUTOFF,
            },
            "overall": overall,
            "period_split": {
                "pre625_delta_yen": round(sum(float(r.get("delta_pnl_yen") or 0) for r in pre625), 2),
                "post625_delta_yen": round(sum(float(r.get("delta_pnl_yen") or 0) for r in post625), 2),
            },
            "weekday_summary": weekday,
            "am_pm_summary": am_pm,
            "flat_band_overlap": overlap,
            "keep_shadow_competition": competition,
            "recent_5day_attribution": recent,
            "rollback": rollback,
            "mainline_trial": trial,
            "mandatory_answers": mandatory,
            "daily_breakdown": daily,
            "leave_one_day_out": loo_day,
            "leave_one_symbol_out": loo_sym,
            "risk_review": risk_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.repo_root / "results" / "reports" / REPORT_DIR_NAME
        out.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        report_fp = out / "phase659_report.json"
        payload = {k: v for k, v in result.items() if k not in ("daily_breakdown", "leave_one_day_out", "leave_one_symbol_out", "risk_review")}
        report_fp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        paths["report"] = report_fp

        daily_cols = [
            "day", "weekday", "period", "session_AM_baseline_pnl", "session_AM_delta_pnl",
            "session_PM_baseline_pnl", "session_PM_delta_pnl", "baseline_entries", "kept_entries",
            "blocked_entries", "entry_reduction_pct", "baseline_pnl_yen", "kept_pnl_yen",
            "delta_pnl_yen", "delta_pf", "blocked_winners", "blocked_winners_pnl",
            "rescued_losers", "rescued_losers_pnl",
        ]
        _write_csv(out / "phase659_daily_breakdown.csv", daily_cols, result.get("daily_breakdown") or [])
        paths["daily"] = out / "phase659_daily_breakdown.csv"

        loo_d_cols = [
            "excluded_day", "weekday", "excluded_day_delta_pnl", "delta_pnl_without_day",
            "full_period_delta_pnl", "still_positive", "share_of_total_delta",
        ]
        _write_csv(out / "phase659_leave_one_day_out.csv", loo_d_cols, result.get("leave_one_day_out") or [])
        paths["loo_day"] = out / "phase659_leave_one_day_out.csv"

        loo_s_cols = [
            "symbol", "symbol_delta_pnl_yen", "delta_pnl_without_symbol", "full_period_delta_pnl",
            "still_positive", "share_of_total_delta", "symbol_trade_count",
        ]
        _write_csv(out / "phase659_leave_one_symbol_out.csv", loo_s_cols, result.get("leave_one_symbol_out") or [])
        paths["loo_symbol"] = out / "phase659_leave_one_symbol_out.csv"

        risk_cols = ["risk_id", "severity", "value", "mitigation"]
        _write_csv(out / "phase659_risk_review.csv", risk_cols, result.get("risk_review") or [])
        paths["risk"] = out / "phase659_risk_review.csv"
        return paths


def run_phase659(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    job = Phase659Job(repo_root=repo_root or NATIVE_ROOT)
    result = job.run()
    paths = job.write_outputs(result)
    result["output_paths"] = {k: str(v) for k, v in paths.items()}
    return result
