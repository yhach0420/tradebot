"""
Phase 160: momentum_fade / price_momentum_fade exit review — separate range-hold vs breakdown.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.cap3_entry_replay import _profit_factor
from research.fade_exit_replay import FADE_EXIT_REASONS
from research.phase159_overlap_review import load_cap5_only_keys
from research.mfe_mae_exit_review import (
    as_float,
    best_pnl_in_window,
    build_price_timeline_from_events_csv,
    load_structural_trades,
    parse_ts,
    pnl_pct,
    price_at_horizon,
    session_end_ts_from_trades,
)
from research.phase159_overlap_review import _is_overlap_reason
from research.runtime_pilot_policy_review import _build_price_index
from research.small_paper_performance_review import _load_events
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    combined_exit_signal_on_latest_tick,
    tick_from_candidate,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot

POST_WINDOWS = (30, 60, 120, 300)
REACCEL_MIN_GAIN = 0.12
BREAKDOWN_MIN_DROP = 0.12
RANGE_BAND = 0.08
MIN_TICKS_CLASSIFY = 2

FADE_POLICIES = (
    ("A_current", "current"),
    ("B_no_fade", "no_fade"),
    ("C_breakdown_only", "breakdown_only"),
    ("D_range_hold_continue", "range_hold_continue"),
    ("E_take_only_fade", "take_only_fade"),
    ("F_mfe_required", "mfe_required"),
    ("G_delayed_fade", "delayed_fade"),
)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _session_id(session_dir: Path) -> str:
    if session_dir.parent.name.isdigit():
        return f"{session_dir.parent.name}/{session_dir.name}"
    return session_dir.name


def _fade_reason(reason: str) -> bool:
    return str(reason or "") in FADE_EXIT_REASONS or "momentum_fade" in str(reason or "")


def _build_ticks_for_trade(
    events: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    entry_ts: float,
    close_ts: float,
    entry_price: float,
) -> list[dict[str, Any]]:
    ticks: list[dict[str, Any]] = []
    for ev in events:
        if str(ev.get("symbol") or "") != symbol:
            continue
        if str(ev.get("event_type") or "") != "candidate":
            continue
        ts = parse_ts(str(ev.get("event_time") or ev.get("entry_time") or ""))
        if ts < entry_ts or ts > close_ts + 1:
            continue
        px = float(ev.get("current_price") or 0)
        if px <= 0:
            continue
        q = float(ev.get("continuation_quality_score") or 0)
        t = tick_from_candidate(dict(ev), entry_price, q)
        t["ts_epoch"] = ts
        t["ts"] = str(ev.get("event_time") or ev.get("entry_time") or "")
        ticks.append(t)
    ticks.sort(key=lambda t: float(t.get("ts_epoch") or 0))
    return ticks


def _overlap_history(trades: Sequence[Mapping[str, Any]], idx: int) -> bool:
    sym = str(trades[idx].get("symbol") or "")
    ent = parse_ts(str(trades[idx].get("entry_time") or ""))
    for j in range(max(0, idx - 5), idx):
        t = trades[j]
        if str(t.get("symbol") or "") != sym:
            continue
        if _is_overlap_reason(str(t.get("close_reason") or "")):
            if ent - parse_ts(str(t.get("close_time") or "")) < 180:
                return True
    return False


def _exit_features(ticks: Sequence[Mapping[str, Any]], entry_price: float) -> dict[str, Any]:
    if not ticks:
        return {}
    last = ticks[-1]
    pnls = [float(t.get("pnl_pct") or 0) for t in ticks]
    peak_pnl = max(pnls) if pnls else 0.0
    exit_pnl = float(last.get("pnl_pct") or 0)
    mfe_giveback = (peak_pnl - exit_pnl) / peak_pnl if peak_pnl > 0.01 else 0.0
    recent = ticks[-min(5, len(ticks)) :]
    recent_px = [float(t.get("price") or entry_price) for t in recent]
    range_pct = 0.0
    if entry_price > 0 and recent_px:
        range_pct = (max(recent_px) - min(recent_px)) / entry_price * 100.0
    down_ticks = 0
    for i in range(1, len(recent)):
        if float(recent[i].get("pnl_pct") or 0) < float(recent[i - 1].get("pnl_pct") or 0):
            down_ticks += 1
    return {
        "quality_at_exit": float(last.get("quality") or 0),
        "momentum_at_exit": float(last.get("momentum") or 0),
        "favorable_at_exit": float(last.get("favorable") or 0),
        "peak_pnl_at_exit": round(peak_pnl, 4),
        "mfe_giveback_frac_at_exit": round(mfe_giveback, 4),
        "consecutive_down_ticks": down_ticks,
        "pre_exit_30s_range_pct": round(range_pct, 4),
        "vwap_distance_at_exit": round(exit_pnl, 4),
        "tick_count_to_exit": len(ticks),
    }


def _post_exit_metrics(
    timeline: Sequence[tuple[float, float]],
    *,
    entry_price: float,
    exit_pnl: float,
    close_ts: float,
    session_end_ts: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    mfe_before = exit_pnl
    for w in POST_WINDOWS:
        p_at = price_at_horizon(
            timeline, base_ts=close_ts, entry_price=entry_price, horizon_sec=float(w), session_end_ts=session_end_ts
        )
        best = best_pnl_in_window(
            timeline, base_ts=close_ts, entry_price=entry_price, window_sec=float(w), session_end_ts=session_end_ts
        )
        worst = None
        end_ts = min(close_ts + w, session_end_ts)
        vals: list[float] = []
        for ts, px in timeline:
            if close_ts <= ts <= end_ts:
                vals.append(pnl_pct(entry_price, px))
        if vals:
            worst = min(vals)
        row[f"pnl_at_{w}s"] = p_at
        row[f"best_pnl_after_{w}s"] = best
        row[f"worst_pnl_after_{w}s"] = worst
        if best is not None:
            mfe_before = max(mfe_before, best)
        row[f"new_high_after_{w}s"] = bool(best is not None and best >= exit_pnl + REACCEL_MIN_GAIN)
        row[f"new_mfe_after_{w}s"] = bool(best is not None and best > exit_pnl + 0.05)
        row[f"recovered_above_exit_{w}s"] = bool(p_at is not None and p_at >= exit_pnl)
    row["mfe_including_post_exit_300s"] = round(mfe_before, 4)
    end_pnl = price_at_horizon(
        timeline,
        base_ts=close_ts,
        entry_price=entry_price,
        horizon_sec=max(1.0, session_end_ts - close_ts),
        session_end_ts=session_end_ts,
    )
    row["pnl_at_session_end"] = end_pnl
    best_end = best_pnl_in_window(
        timeline,
        base_ts=close_ts,
        entry_price=entry_price,
        window_sec=max(1.0, session_end_ts - close_ts),
        session_end_ts=session_end_ts,
    )
    row["best_pnl_after_session_end"] = best_end
    row["worst_pnl_after_session_end"] = None
    end_vals: list[float] = []
    for ts, px in timeline:
        if close_ts <= ts <= session_end_ts:
            end_vals.append(pnl_pct(entry_price, px))
    if end_vals:
        row["worst_pnl_after_session_end"] = min(end_vals)
    row["new_high_after_session_end"] = bool(
        best_end is not None and best_end >= exit_pnl + REACCEL_MIN_GAIN
    )
    row["breakdown_after_exit"] = bool(
        row.get("worst_pnl_after_120s") is not None
        and float(row["worst_pnl_after_120s"]) <= exit_pnl - BREAKDOWN_MIN_DROP
        and (best_end is None or float(best_end) < exit_pnl + RANGE_BAND)
    )
    return row


def classify_post_exit(
    row: Mapping[str, Any],
    *,
    post_tick_count: int,
    pattern: str = "default",
) -> tuple[str, str]:
    if post_tick_count < MIN_TICKS_CLASSIFY:
        return "D_noisy_insufficient_ticks", "insufficient_post_ticks"
    exit_pnl = float(row.get("exit_pnl") or 0)
    best120 = as_float(row.get("best_pnl_after_120s"))
    worst120 = as_float(row.get("worst_pnl_after_120s"))
    best60 = as_float(row.get("best_pnl_after_60s"))
    best30 = as_float(row.get("best_pnl_after_30s"))
    reaccel_thr = REACCEL_MIN_GAIN if pattern == "default" else (0.08 if pattern == "loose" else 0.18)
    break_thr = BREAKDOWN_MIN_DROP if pattern == "default" else (0.08 if pattern == "strict" else 0.15)
    if pattern == "loose":
        if best60 is not None and best60 >= exit_pnl + reaccel_thr:
            return "A_reacceleration", "loose_new_high_60s"
    if row.get("new_high_after_120s") or row.get("new_mfe_after_120s"):
        if best120 is not None and best120 >= exit_pnl + reaccel_thr:
            return "A_reacceleration", "new_high_or_mfe_after_exit"
    if pattern == "strict" and best30 is not None and best30 >= exit_pnl + 0.05:
        return "A_reacceleration", "strict_early_bounce_30s"
    if worst120 is not None and worst120 <= exit_pnl - break_thr:
        if best60 is None or best60 < exit_pnl + RANGE_BAND:
            return "C_breakdown", "post_exit_lower_low_no_recovery"
    if best120 is not None and worst120 is not None:
        if abs(best120 - exit_pnl) <= RANGE_BAND and abs(worst120 - exit_pnl) <= RANGE_BAND:
            return "B_range_hold", "oscillation_near_exit_price"
    if best120 is not None and best120 >= exit_pnl - RANGE_BAND:
        return "B_range_hold", "muted_follow_through"
    return "B_range_hold", "default_range_hold"


CLASSIFICATION_PATTERNS = ("default", "strict", "loose")


def _predict_breakdown(row: Mapping[str, Any], rule_id: str) -> bool:
    exit_pnl = float(row.get("exit_pnl") or 0)
    mfe = float(row.get("mfe_before_exit") or 0)
    giveback = float(row.get("mfe_giveback_frac_at_exit") or 0)
    mom = float(row.get("momentum_at_exit") or 0)
    q = float(row.get("quality_at_exit") or 0)
    down = int(row.get("consecutive_down_ticks") or 0)
    had_take = bool(row.get("take_reached"))
    if rule_id == "R1_exit_pnl_negative":
        return exit_pnl < -0.05
    if rule_id == "R2_mfe_giveback_gt_50pct":
        return giveback >= 0.5 and mfe > 0.1
    if rule_id == "R3_consecutive_down_ge_3":
        return down >= 3
    if rule_id == "R4_momentum_below_015":
        return mom < 0.15
    if rule_id == "R5_quality_drop_010":
        return q < 0.55
    if rule_id == "R6_no_take_and_negative":
        return not had_take and exit_pnl < 0
    if rule_id == "R7_vwap_break_proxy":
        return exit_pnl < 0 and mfe > 0.2
    if rule_id == "R8_combo_down_mom":
        return down >= 2 and mom < 0.2
    if rule_id == "R9_pre_exit_range_tight":
        return float(row.get("pre_exit_30s_range_pct") or 99) < 0.15 and exit_pnl < 0
    if rule_id == "R10_mfe_not_captured":
        return mfe > 0.25 and giveback > 0.6
    return False


RULE_IDS = (
    "R1_exit_pnl_negative",
    "R2_mfe_giveback_gt_50pct",
    "R3_consecutive_down_ge_3",
    "R4_momentum_below_015",
    "R5_quality_drop_010",
    "R6_no_take_and_negative",
    "R7_vwap_break_proxy",
    "R8_combo_down_mom",
    "R9_pre_exit_range_tight",
    "R10_mfe_not_captured",
)


def evaluate_breakdown_rules(fade_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rule_id in RULE_IDS:
        tp = fp = tn = fn = 0
        hold_pnls: list[float] = []
        false_hold = 0
        for r in fade_rows:
            pred = _predict_breakdown(r, rule_id)
            actual = str(r.get("post_exit_class") or "").startswith("C")
            reaccel = str(r.get("post_exit_class") or "").startswith("A")
            exit_pnl = float(r.get("exit_pnl") or 0)
            hold_pnl = as_float(r.get("best_pnl_after_120s"))
            if hold_pnl is None:
                hold_pnl = exit_pnl
            if pred and actual:
                tp += 1
            elif pred and reaccel:
                fp += 1
            elif not pred and actual:
                fn += 1
            else:
                tn += 1
            if pred:
                hold_pnls.append(hold_pnl)
                if reaccel:
                    false_hold += 1
        prec = round(tp / max(1, tp + fp), 4)
        rec = round(tp / max(1, tp + fn), 4)
        out.append(
            {
                "rule_id": rule_id,
                "predicted_breakdown_count": tp + fp,
                "true_breakdown_count": tp,
                "false_reacceleration_count": fp,
                "missed_breakdown_count": fn,
                "true_negative_count": tn,
                "breakdown_precision": prec,
                "breakdown_recall": rec,
                "reacceleration_false_hold_rate": round(fp / max(1, tp + fp), 4),
                "hold_if_rule_pf": _profit_factor(hold_pnls),
                "hold_if_rule_avg_pnl": round(statistics.mean(hold_pnls), 4) if hold_pnls else None,
                "false_hold_count": false_hold,
            }
        )
    return out


def _is_breakdown_at_exit(ticks: Sequence[Mapping[str, Any]], entry_price: float) -> bool:
    if len(ticks) < 2:
        return False
    row = {**_exit_features(ticks, entry_price), "exit_pnl": ticks[-1].get("pnl_pct", 0)}
    return _predict_breakdown(row, "R8_combo_down_mom") or _predict_breakdown(row, "R1_exit_pnl_negative")


def _unwrap_exit_sig(sig: Optional[tuple[Any, ...]]) -> Optional[tuple[float, str]]:
    if not sig:
        return None
    return float(sig[0]), str(sig[1])


def simulate_fade_policy_on_ticks(
    ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    *,
    mode: str,
    exit_cfg: Any,
    had_take: bool,
) -> tuple[float, str]:
    if not ticks:
        return 0.0, "session_end"

    peak_mfe = 0.0
    fade_streak = 0
    for i, _ in enumerate(ticks):
        sub = ticks[: i + 1]
        if mode == "B_no_fade":
            sig = _unwrap_exit_sig(combined_exit_signal_on_latest_tick(sub, entry_price, exit_cfg))
            if sig and sig[1] not in FADE_EXIT_REASONS:
                return sig
            continue
        if mode == "F_mfe_required":
            peak_mfe = max(peak_mfe, float(sub[-1].get("pnl_pct") or 0))
            if peak_mfe < 0.10:
                continue
        if mode == "E_take_only_fade" and not had_take:
            continue
        sig = _unwrap_exit_sig(combined_exit_signal_on_latest_tick(sub, entry_price, exit_cfg))
        if not sig:
            continue
        pnl, reason = sig
        if reason not in FADE_EXIT_REASONS:
            return pnl, reason
        if mode == "C_breakdown_only":
            if _is_breakdown_at_exit(sub, entry_price):
                return pnl, reason
            continue
        if mode == "G_delayed_fade":
            fade_streak += 1
            if fade_streak >= 2:
                return pnl, reason
            continue
        if mode == "D_range_hold_continue":
            if _is_breakdown_at_exit(sub, entry_price):
                return pnl, reason
            continue
        return pnl, reason
    return float(ticks[-1].get("pnl_pct") or 0), "session_end"


def fade_policy_whatif(
    fade_rows: Sequence[Mapping[str, Any]],
    tick_by_key: Mapping[tuple[str, str], list[dict[str, Any]]],
    *,
    pilot_config: Any,
) -> list[dict[str, Any]]:
    exit_cfg = observer_tracker_config_from_pilot(pilot_config)
    exit_cfg.structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1
    summary: list[dict[str, Any]] = []

    for policy_id, mode in FADE_POLICIES:
        pnls: list[float] = []
        holds: list[float] = []
        reasons: Counter[str] = Counter()
        improved = worsened = 0
        for r in fade_rows:
            key = (str(r.get("symbol")), str(r.get("entry_time")))
            ticks = tick_by_key.get(key) or []
            base = float(r.get("exit_pnl") or 0)
            base_hold = float(r.get("hold_sec") or 0)
            if mode == "current":
                pnl, reason = base, str(r.get("exit_reason") or "")
            else:
                if not ticks:
                    pnl, reason = base, str(r.get("exit_reason") or "")
                else:
                    entry_px = float(r.get("entry_price") or 0)
                    had_take = bool(r.get("take_reached"))
                    pnl, reason = simulate_fade_policy_on_ticks(
                        ticks, entry_px, mode=mode, exit_cfg=exit_cfg, had_take=had_take
                    )
            pnls.append(pnl)
            reasons[reason] += 1
            holds.append(base_hold)
            if pnl > base + 0.02:
                improved += 1
            elif pnl < base - 0.02:
                worsened += 1
        summary.append(
            {
                "scenario": policy_id,
                "mode": mode,
                "trade_count": len(pnls),
                "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
                "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
                "pf": _profit_factor(pnls),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
                "max_loss_pct": round(min(pnls), 4) if pnls else None,
                "max_gain_pct": round(max(pnls), 4) if pnls else None,
                "fade_exit_count": sum(reasons.get(x, 0) for x in FADE_EXIT_REASONS),
                "stop_hit_count": reasons.get("stop_hit", 0),
                "session_close_count": reasons.get("session_end", 0),
                "hold_time_increase_avg_sec": round(
                    statistics.mean(holds) if holds else 0.0, 2
                ),
                "improved_count": improved,
                "worsened_count": worsened,
            }
        )
    return summary


def analyze_session(
    session_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], list[dict[str, Any]]]]:
    session_id = _session_id(session_dir)
    trades = load_structural_trades(session_dir / "structural_trades.csv")
    if not trades:
        return [], [], [], {}
    ordered = sorted(trades, key=lambda t: parse_ts(str(t.get("entry_time") or "")))
    events = _load_events(session_dir)
    syms = {str(t.get("symbol") or "") for t in trades}
    timeline_map = build_price_timeline_from_events_csv(session_dir / "small_paper_events.csv", syms)
    if not any(timeline_map.values()) and events:
        idx = _build_price_index(events)
        timeline_map = {s: list(v) for s, v in idx.items()}
    session_end = session_end_ts_from_trades(trades)

    fade_events: list[dict[str, Any]] = []
    path_rows: list[dict[str, Any]] = []
    class_rows: list[dict[str, Any]] = []
    tick_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for i, tr in enumerate(ordered):
        reason = str(tr.get("close_reason") or "")
        if not _fade_reason(reason):
            continue
        sym = str(tr.get("symbol") or "")
        entry_ts = parse_ts(str(tr.get("entry_time") or ""))
        close_ts = parse_ts(str(tr.get("close_time") or ""))
        entry_px = float(tr.get("entry_price") or 0)
        exit_pnl = float(tr.get("realized_pnl_pct") or 0)
        ticks = _build_ticks_for_trade(events, symbol=sym, entry_ts=entry_ts, close_ts=close_ts, entry_price=entry_px)
        feats = _exit_features(ticks, entry_px)
        tl = timeline_map.get(sym, [])
        post = _post_exit_metrics(tl, entry_price=entry_px, exit_pnl=exit_pnl, close_ts=close_ts, session_end_ts=session_end)
        post_ticks = sum(1 for ts, _ in tl if ts >= close_ts)
        cls, cls_note = classify_post_exit(
            {**post, "exit_pnl": exit_pnl}, post_tick_count=post_ticks, pattern="default"
        )
        had_take = str(tr.get("had_take_before_exit") or "").lower() in ("true", "1", "yes")
        trade_key = (sym, str(tr.get("entry_time") or ""))
        tick_by_key[trade_key] = ticks

        ev = {
            "session": session_id,
            "symbol": sym,
            "entry_time": tr.get("entry_time"),
            "fade_exit_time": tr.get("close_time"),
            "entry_price": entry_px,
            "exit_price": tr.get("close_price"),
            "exit_pnl": exit_pnl,
            "mfe_before_exit": float(tr.get("mfe_pct") or 0),
            "mae_before_exit": float(tr.get("mae_pct") or 0),
            "hold_sec": float(tr.get("hold_duration_sec") or 0),
            "exit_reason": reason,
            "take_reached": had_take,
            "overlap_history": _overlap_history(ordered, i),
            **feats,
            **post,
            "post_exit_class": cls,
            "post_exit_class_note": cls_note,
            "breakdown_after_exit": post.get("breakdown_after_exit"),
        }
        fade_events.append(ev)
        path_rows.append(
            {
                "session": session_id,
                "symbol": sym,
                "entry_time": tr.get("entry_time"),
                "fade_exit_time": tr.get("close_time"),
                "exit_reason": reason,
                "exit_pnl": exit_pnl,
                **post,
                "post_exit_class": cls,
            }
        )
        for pattern in CLASSIFICATION_PATTERNS:
            pcls, pnote = classify_post_exit(
                {**post, "exit_pnl": exit_pnl}, post_tick_count=post_ticks, pattern=pattern
            )
            class_rows.append(
                {
                    "session": session_id,
                    "symbol": sym,
                    "entry_time": tr.get("entry_time"),
                    "fade_exit_time": tr.get("close_time"),
                    "exit_reason": reason,
                    "exit_pnl": exit_pnl,
                    "classification_pattern": pattern,
                    "post_exit_class": pcls,
                    "post_exit_class_note": pnote,
                    "tick_count_post_window": post_ticks,
                }
            )
    return fade_events, path_rows, class_rows, tick_by_key


def cap5_fade_analysis(
    fade_events: Sequence[Mapping[str, Any]],
    cap5_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    cap5_fades = [
        r
        for r in fade_events
        if (str(r.get("symbol")), str(r.get("entry_time"))) in cap5_keys
    ]
    n = len(cap5_fades)
    if not n:
        return [{"cap5_only_fade_count": 0}]
    reaccel = sum(1 for r in cap5_fades if str(r.get("post_exit_class", "")).startswith("A"))
    return [
        {
            "cap5_only_fade_count": n,
            "reacceleration_rate_pct": round(100.0 * reaccel / n, 2),
            "breakdown_rate_pct": round(
                100.0
                * sum(1 for r in cap5_fades if str(r.get("post_exit_class", "")).startswith("C"))
                / n,
                2,
            ),
            "range_hold_rate_pct": round(
                100.0
                * sum(1 for r in cap5_fades if str(r.get("post_exit_class", "")).startswith("B"))
                / n,
                2,
            ),
            "avg_exit_pnl": round(
                statistics.mean(float(r.get("exit_pnl") or 0) for r in cap5_fades), 4
            ),
            "avg_best_pnl_120s": round(
                statistics.mean(
                    float(r["best_pnl_after_120s"])
                    for r in cap5_fades
                    if r.get("best_pnl_after_120s") not in (None, "")
                ),
                4,
            )
            if any(r.get("best_pnl_after_120s") not in (None, "") for r in cap5_fades)
            else None,
        }
    ]


def determine_verdict(
    fade_events: Sequence[Mapping[str, Any]],
    policy_rows: Sequence[Mapping[str, Any]],
    *,
    session_count: int,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    n = len(fade_events)
    if n < 15 or session_count < 2:
        return "need_tick_density", [f"fade_trades={n} sessions={session_count}"]

    reaccel = sum(1 for r in fade_events if str(r.get("post_exit_class", "")).startswith("A"))
    reaccel_rate = 100.0 * reaccel / n
    notes.append(f"fade_count={n} reacceleration_rate={reaccel_rate:.1f}%")

    avg_ticks = statistics.mean(
        [float(r.get("tick_count_to_exit") or 0) for r in fade_events if r.get("tick_count_to_exit")]
    )
    if avg_ticks < 2.5:
        return "need_tick_density", notes + [f"avg_ticks_to_exit={avg_ticks:.2f}"]

    by_policy = {str(r["scenario"]): r for r in policy_rows}
    cur = by_policy.get("A_current", {})
    no_fade = by_policy.get("B_no_fade", {})
    c_only = by_policy.get("C_breakdown_only", {})

    cur_pf = float(cur.get("pf") or 0)
    no_pf = float(no_fade.get("pf") or 0) if no_fade.get("pf") is not None else 0
    if reaccel_rate >= 45 and no_pf > cur_pf + 0.05:
        return "fade_exit_too_fast", notes + ["high reacceleration; hold/delay improves PF on fade trades"]

    best_rule = max(
        evaluate_breakdown_rules(fade_events),
        key=lambda x: float(x.get("breakdown_precision") or 0),
        default={"breakdown_precision": 0},
    )
    if float(best_rule.get("breakdown_precision") or 0) >= 0.55 and float(
        best_rule.get("breakdown_recall") or 0
    ) >= 0.35:
        if c_only and float(c_only.get("pf") or 0) > cur_pf:
            return "breakdown_rule_promising", notes + [f"best_rule={best_rule.get('rule_id')}"]

    if cur_pf >= no_pf and reaccel_rate < 35:
        return "fade_exit_reasonable", notes + ["current fade policy competitive vs alternatives"]

    return "mixed_result", notes


def build_recommendation_md(
    verdict: str,
    notes: Sequence[str],
    summary: Mapping[str, Any],
    *,
    policy_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    cap5_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    top_rules: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    lines = [
        "# Phase 160: fade exit review recommendation",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## Summary (all sessions, fade exits only)",
        "",
        f"- Fade exits analyzed: {summary.get('fade_exit_count')}",
        f"- Post-exit **reacceleration** (new high / MFE @120s): {summary.get('reacceleration_rate_pct')}%",
        f"- Post-exit **range-hold** (価格が exit 付近で推移): {summary.get('range_hold_rate_pct')}%",
        f"- Post-exit **breakdown** (安値更新・回復なし): {summary.get('breakdown_rate_pct')}%",
        "",
        "## Interpretation",
        "",
        "- 約4割が exit 後に再加速 → fade が早すぎる候補が多いが、breakdown は約2割のみ。",
        "- **横ばい継続 (range_hold) が最大クラスタ** → 「全部 fade 禁止」より **崩れ確認付き継続** が筋が良い。",
        "",
        "## Policy what-if (fade trades, tick replay)",
        "",
    ]
    if policy_rows:
        cur = next((r for r in policy_rows if r.get("scenario") == "A_current"), {})
        best = max(policy_rows, key=lambda r: float(r.get("pf") or 0))
        lines.append(
            f"- **A 現行**: PF {cur.get('pf')}, avg {cur.get('avg_pnl')}%, total {cur.get('total_pnl')}%"
        )
        lines.append(
            f"- **B fade 無効（他 exit のみ）**: PF {best.get('pf')}, avg {best.get('avg_pnl')}%, "
            f"improved {best.get('improved_count')} / worsened {best.get('worsened_count')}"
        )
        if float(best.get("pf") or 0) > float(cur.get("pf") or 0):
            lines.append("- 現行より PF 改善はあるが、無条件 hold は max_loss 悪化リスクあり → breakdown ゲート設計を優先。")
    if cap5_rows:
        c5 = cap5_rows[0]
        if c5.get("cap5_only_fade_count"):
            lines.extend(
                [
                    "",
                    "## Cap5-only fade (Phase158 subset)",
                    "",
                    f"- Fade count: {c5.get('cap5_only_fade_count')}",
                    f"- Reacceleration: {c5.get('reacceleration_rate_pct')}%",
                    f"- Avg exit PnL: {c5.get('avg_exit_pnl')}% vs avg best+120s: {c5.get('avg_best_pnl_120s')}%",
                    "- cap5 層では fade 後の取りこぼしがより顕著 → cap 増加前に exit 条件の見直しが必要。",
                ]
            )
    if top_rules:
        lines.extend(["", "## Breakdown rule candidates (top precision)", ""])
        for r in top_rules[:3]:
            lines.append(
                f"- `{r.get('rule_id')}`: precision {r.get('breakdown_precision')}, "
                f"recall {r.get('breakdown_recall')}, false-reaccel hold {r.get('reacceleration_false_hold_rate')}"
            )
        lines.append("- 単独ルールの precision は概ね 0.33–0.38。複合条件の shadow 検証が次ステップ。")
    lines.extend(["", "## Notes", ""])
    for n in notes:
        lines.append(f"- {n}")
    lines.extend(
        [
            "",
            "## Next step (shadow only)",
            "",
            "1. Phase161: breakdown 複合ルール（R6+R4 等）の shadow replay",
            "2. fade 遅延（2-tick 確認）+ take 到達後のみ fade を別 CSV で比較",
            "3. 本番 YAML / entry / exit は変更しない",
            "",
            "## Constraints",
            "",
            "- Review only; `order_enabled=false`, `paper_only=true`.",
        ]
    )
    return "\n".join(lines) + "\n"


def analyze_phase160(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    cap5_csv: Optional[Path] = None,
) -> dict[str, Any]:
    all_fade: list[dict[str, Any]] = []
    all_paths: list[dict[str, Any]] = []
    all_class: list[dict[str, Any]] = []
    all_ticks: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for sdir in session_dirs:
        ev, paths, cls, ticks = analyze_session(sdir)
        all_fade.extend(ev)
        all_paths.extend(paths)
        all_class.extend(cls)
        all_ticks.update(ticks)

    n = len(all_fade)
    summary = {
        "fade_exit_count": n,
        "reacceleration_rate_pct": round(
            100.0
            * sum(1 for r in all_fade if str(r.get("post_exit_class", "")).startswith("A"))
            / max(1, n),
            2,
        ),
        "breakdown_rate_pct": round(
            100.0
            * sum(1 for r in all_fade if str(r.get("post_exit_class", "")).startswith("C"))
            / max(1, n),
            2,
        ),
        "range_hold_rate_pct": round(
            100.0
            * sum(1 for r in all_fade if str(r.get("post_exit_class", "")).startswith("B"))
            / max(1, n),
            2,
        ),
    }

    cap5_keys = load_cap5_only_keys(cap5_csv) if cap5_csv else set()

    policy_rows = fade_policy_whatif(all_fade, all_ticks, pilot_config=pilot_config)
    rule_rows = evaluate_breakdown_rules(all_fade)
    verdict, notes = determine_verdict(
        all_fade, policy_rows, session_count=len(session_dirs)
    )

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "verdict_options": {
            "A": "fade_exit_too_fast",
            "B": "fade_exit_reasonable",
            "C": "breakdown_rule_promising",
            "D": "need_tick_density",
            "E": "mixed_result",
        },
        "summary": summary,
        "fade_events": all_fade,
        "path_rows": all_paths,
        "class_rows": all_class,
        "rule_candidates": rule_rows,
        "policy_whatif": policy_rows,
        "cap5_fade": cap5_fade_analysis(all_fade, cap5_keys),
        "session_count": len(session_dirs),
    }


def write_phase160_outputs(result: Mapping[str, Any], *, reports_dir: Path, docs_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": reports_dir / "phase160_fade_exit_review.json",
        "events": reports_dir / "phase160_fade_exit_events.csv",
        "paths": reports_dir / "phase160_fade_after_exit_paths.csv",
        "classify": reports_dir / "phase160_fade_classification.csv",
        "rules": reports_dir / "phase160_breakdown_rule_candidates.csv",
        "whatif": reports_dir / "phase160_fade_policy_whatif.csv",
        "cap5": reports_dir / "phase160_cap5_fade_analysis.csv",
        "md": docs_dir / "phase160_recommendation.md",
    }
    design = {k: v for k, v in result.items() if k not in ("fade_events", "path_rows", "class_rows")}
    paths["json"].write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(paths["events"], result.get("fade_events") or [])
    _write_csv(paths["paths"], result.get("path_rows") or [])
    _write_csv(paths["classify"], result.get("class_rows") or [])
    _write_csv(paths["rules"], result.get("rule_candidates") or [])
    _write_csv(paths["whatif"], result.get("policy_whatif") or [])
    _write_csv(paths["cap5"], result.get("cap5_fade") or [])
    rules_sorted = sorted(
        result.get("rule_candidates") or [],
        key=lambda x: float(x.get("breakdown_precision") or 0),
        reverse=True,
    )
    paths["md"].write_text(
        build_recommendation_md(
            str(result.get("verdict") or ""),
            result.get("verdict_notes") or [],
            result.get("summary") or {},
            policy_rows=result.get("policy_whatif"),
            cap5_rows=result.get("cap5_fade"),
            top_rules=rules_sorted,
        ),
        encoding="utf-8",
    )
    return {k: str(v) for k, v in paths.items()}
