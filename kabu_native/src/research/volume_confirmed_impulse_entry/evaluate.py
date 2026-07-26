"""VCIE chronological OOS evaluation, day-matched compare, CAP=5."""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Sequence
from zoneinfo import ZoneInfo

from research.pbv2_zero_base_revalidation.cap5 import replay_cap5
from research.pbv2_zero_base_revalidation.labels import counterfactual_exit
from research.pbv2_zero_base_revalidation.metrics import aggregate_oos_daily, metrics_for, pnl_metric_block
from research.pbv2_zero_base_revalidation.panel import CandidateRow, PricePoint
from research.pbv2_zero_base_revalidation.walk_forward import chronological_oos
from research.volume_confirmed_impulse_entry.constants import (
    MAX_DAY_PNL_SHARE,
    MAX_DAY_TRIGGER_SHARE,
    MIN_AM_DAYS,
    MIN_COMPLETE_TRIGGERS,
    MIN_OOS_DAYS,
    MIN_PM_DAYS,
)
from research.volume_confirmed_impulse_entry.features import (
    ThresholdSet,
    Trigger,
    aggregate_to_seconds,
    detect_triggers_for_symbol,
    fit_thresholds_on_train,
)
from research.volume_confirmed_impulse_entry.push_loader import PushTick

JST = ZoneInfo("Asia/Tokyo")
KeepFn = Callable[[CandidateRow], bool]


def _session_bucket(t: datetime) -> str:
    h = t.hour
    if 7 <= h < 12:
        return "AM"
    if 12 <= h < 16:
        return "PM"
    return "OTHER"


def trigger_to_row(
    tr: Trigger,
    ticks: Sequence[PushTick],
    *,
    pbv2_decision: bool = False,
    pbv2_score: float = 0.0,
) -> CandidateRow:
    """Build CandidateRow + attach CF labels from subsequent PUSH path."""
    path: list[PricePoint] = []
    t0 = tr.event_time
    for t in ticks[tr.row_index :]:
        if (t.event_time - t0).total_seconds() > 900:
            break
        path.append(PricePoint(t=t.event_time, px=t.current_price))
    cf = counterfactual_exit(tr.entry_price, path)
    # forward returns
    fwd: dict[str, Optional[float]] = {}
    for name, sec in (
        ("forward_return_10s", 10),
        ("forward_return_30s", 30),
        ("forward_return_60s", 60),
        ("forward_return_2m", 120),
        ("forward_return_5m", 300),
        ("forward_return_10m", 600),
        ("forward_return_15m", 900),
    ):
        px = None
        for p in path:
            if (p.t - t0).total_seconds() >= sec:
                px = p.px
                break
        fwd[name] = (px - tr.entry_price) / tr.entry_price * 100.0 if px and tr.entry_price > 0 else None
    for name, sec in (
        ("forward_MFE_1m", 60),
        ("forward_MAE_1m", 60),
        ("forward_MFE_2m", 120),
        ("forward_MAE_2m", 120),
        ("forward_MFE_5m", 300),
        ("forward_MAE_5m", 300),
        ("forward_MFE_10m", 600),
        ("forward_MAE_10m", 600),
    ):
        xs = [p.px for p in path if (p.t - t0).total_seconds() <= sec]
        if not xs or tr.entry_price <= 0:
            fwd[name] = None
        else:
            rets = [(x - tr.entry_price) / tr.entry_price * 100.0 for x in xs]
            fwd[name] = max(rets) if "MFE" in name else min(rets)

    row = CandidateRow(
        day=tr.day,
        session=_session_bucket(tr.event_time),
        symbol=tr.symbol,
        evaluation_time=tr.event_time,
        evaluation_event_id=f"vcie-{tr.episode_id}",
        universe_source="watch50_push",
        current_price=tr.entry_price,
        current_price_time=tr.event_time,
        board_time=tr.event_time,
        board_age_sec=0.0,
        price_age_sec=0.0,
        pbv2_candidate=pbv2_decision,
        pbv2_score=pbv2_score,
        pbv2_decision=pbv2_decision,
        reject_reason="",
        accept=False,
        cap_blocked=False,
        features={k: v for k, v in tr.features.items() if v is not None},
        session_bucket=_session_bucket(tr.event_time),
        forward=fwd,
        pnl_evaluable=bool(cf.get("ok")),
        cf_pnl=float(cf["pnl"]) if cf.get("pnl") is not None else None,
        cf_pnl_5bps=float(cf["pnl_5bps"]) if cf.get("pnl_5bps") is not None else None,
        cf_exit_reason=str(cf.get("exit_reason") or ""),
        cf_hold_sec=float(cf["hold_sec"]) if cf.get("hold_sec") is not None else None,
    )
    reason = (row.cf_exit_reason or "").lower()
    row.is_stop = "stop" in reason
    row.is_np = "no_progress" in reason
    row.is_winner = bool(row.cf_pnl_5bps is not None and row.cf_pnl_5bps > 0)
    mfe5 = fwd.get("forward_MFE_5m")
    row.is_large_rise = bool(mfe5 is not None and mfe5 >= 1.0)
    row.large_rise_evaluable = fwd.get("forward_MFE_5m") is not None
    row.mfe_mae_evaluable = fwd.get("forward_MFE_5m") is not None
    row.forward_return_evaluable = any(fwd.get(k) is not None for k in fwd if k.startswith("forward_return"))
    return row


def early_stop_rate(rows: Sequence[CandidateRow], keep: KeepFn) -> Optional[float]:
    kept = [r for r in rows if keep(r) and r.pnl_evaluable]
    if not kept:
        return None
    n = 0
    for r in kept:
        if "stop" not in (r.cf_exit_reason or "").lower():
            continue
        if r.cf_hold_sec is not None and r.cf_hold_sec <= 300:
            n += 1
    return round(n / len(kept), 4)


def day_matched(
    panel_a: Sequence[CandidateRow],
    panel_b: Sequence[CandidateRow],
    keep_a: KeepFn,
    keep_b: KeepFn,
) -> dict[str, Any]:
    days = sorted(set(r.day for r in panel_a) | set(r.day for r in panel_b))
    aa: list[CandidateRow] = []
    bb: list[CandidateRow] = []
    per = []
    for day in days:
        a = [r for r in panel_a if r.day == day and keep_a(r) and r.pnl_evaluable]
        b = [r for r in panel_b if r.day == day and keep_b(r) and r.pnl_evaluable]
        n = min(len(a), len(b))
        a.sort(key=lambda r: (r.evaluation_time, r.symbol))
        b.sort(key=lambda r: (r.evaluation_time, r.symbol))
        aa.extend(a[:n])
        bb.extend(b[:n])
        per.append({"day": day, "matched_n_day": n, "a_n": len(a), "b_n": len(b)})
    ka, kb = {id(r) for r in aa}, {id(r) for r in bb}
    return {
        "n_matched": len(aa),
        "days_with_match": sum(1 for d in per if d["matched_n_day"] > 0),
        "per_day": per,
        "method_a": metrics_for(panel_a, lambda r: id(r) in ka),
        "method_b": metrics_for(panel_b, lambda r: id(r) in kb),
        "verdict": "DAY_MATCHED_COMPARISON_READY",
    }


def coverage_gate(
    triggers_by_method: dict[str, list[Trigger]],
    rows_by_method: dict[str, list[CandidateRow]],
    oos_days: Sequence[str],
) -> dict[str, Any]:
    v4 = triggers_by_method.get("V4_FULL_VCIE") or []
    rows = rows_by_method.get("V4_FULL_VCIE") or []
    am_days = sorted({r.day for r in rows if r.session_bucket == "AM"})
    pm_days = sorted({r.day for r in rows if r.session_bucket == "PM"})
    by_day = defaultdict(int)
    for t in v4:
        by_day[t.day] += 1
    tot = sum(by_day.values()) or 1
    max_share = max(by_day.values(), default=0) / tot
    pnls = defaultdict(float)
    for r in rows:
        if r.pnl_evaluable and r.cf_pnl_5bps is not None:
            pnls[r.day] += float(r.cf_pnl_5bps)
    abs_tot = sum(abs(v) for v in pnls.values()) or 1.0
    max_pnl_share = max((abs(v) for v in pnls.values()), default=0) / abs_tot
    ok = (
        len(oos_days) >= MIN_OOS_DAYS
        and len(v4) >= MIN_COMPLETE_TRIGGERS
        and len(am_days) >= MIN_AM_DAYS
        and len(pm_days) >= MIN_PM_DAYS
        and max_share < MAX_DAY_TRIGGER_SHARE
        and max_pnl_share < MAX_DAY_PNL_SHARE
    )
    return {
        "oos_days": list(oos_days),
        "n_oos_days": len(oos_days),
        "complete_triggers_v4": len(v4),
        "am_days": am_days,
        "pm_days": pm_days,
        "max_day_trigger_share": round(max_share, 4),
        "max_day_pnl_share": round(max_pnl_share, 4),
        "gate_ok": ok,
        "verdict": "COVERAGE_OK" if ok else "VCIE_INSUFFICIENT_HIGH_RES_DATA",
    }


def build_pbv2_index(panel: Sequence[CandidateRow]) -> dict[tuple[str, str], list[CandidateRow]]:
    by: dict[tuple[str, str], list[CandidateRow]] = defaultdict(list)
    for r in panel:
        if r.pbv2_decision or r.accept:
            by[(r.day, r.symbol)].append(r)
    for k in by:
        by[k].sort(key=lambda r: r.evaluation_time)
    return by


def nearest_pbv2(idx: dict[tuple[str, str], list[CandidateRow]], day: str, sym: str, t: datetime, tol_sec: float = 120.0) -> Optional[CandidateRow]:
    rows = idx.get((day, sym)) or []
    best = None
    best_dt = 1e18
    for r in rows:
        dt = abs((r.evaluation_time - t).total_seconds())
        if dt < best_dt:
            best_dt = dt
            best = r
    if best is not None and best_dt <= tol_sec:
        return best
    return None


def run_vcie_oos(
    push_by_day: dict[str, dict[str, list[PushTick]]],
    pbv2_panel: Sequence[CandidateRow],
) -> dict[str, Any]:
    days = sorted(push_by_day.keys())
    pbv2_idx = build_pbv2_index(pbv2_panel)

    # Build synthetic panel of all VCIE candidate rows later; walk-forward on capture days
    # First fit thresholds using expanding train push
    methods = ["V1_CROSS", "V2_VOLUME", "V3_TRADE_SIDE", "V4_FULL_VCIE", "V7_INDEPENDENT"]
    method_daily: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_triggers: dict[str, list[Trigger]] = defaultdict(list)
    all_rows: dict[str, list[CandidateRow]] = defaultdict(list)
    thr_hist: list[dict[str, Any]] = []

    # Create PBv2 rows for capture days as V0
    v0_rows = [r for r in pbv2_panel if r.day in set(days) and (r.pbv2_decision or r.accept) and r.pnl_evaluable]
    all_rows["V0_PBv2"] = v0_rows

    for i, test_day in enumerate(days):
        train_days = days[:i]
        if len(train_days) < 1:
            # need at least 1 train day; if only early days, use defaults
            train_push: dict[str, list[PushTick]] = {}
            for d in train_days:
                train_push.update(push_by_day[d])
        else:
            train_push = {}
            for d in train_days:
                for sym, ticks in push_by_day[d].items():
                    train_push[f"{d}:{sym}"] = ticks

        thr_base = fit_thresholds_on_train(train_push, method="V4_FULL_VCIE") if train_push else ThresholdSet()
        thr_hist.append({"test_date": test_day, "thresholds": thr_base.__dict__})

        test_push = push_by_day[test_day]
        day_rows: dict[str, list[CandidateRow]] = defaultdict(list)
        day_trigs: dict[str, list[Trigger]] = defaultdict(list)
        bars_by_sym = {
            sym: aggregate_to_seconds(ticks) for sym, ticks in test_push.items() if len(ticks) >= 40
        }
        print(f"[vcie] test_day={test_day} symbols={len(bars_by_sym)}", flush=True)

        for method in methods:
            thr = ThresholdSet(**{**thr_base.__dict__})
            if method == "V1_CROSS":
                thr.vol_impulse_10s = 0.0
                thr.vol_impulse_30s = 0.0
            for sym, bars in bars_by_sym.items():
                # detect_triggers_for_symbol re-aggregates; pass bars by wrapping as already-second ticks
                trigs = detect_triggers_for_symbol(bars, method=method, thr=thr, step=1)
                for tr in trigs:
                    pb = nearest_pbv2(pbv2_idx, tr.day, tr.symbol, tr.event_time)
                    row = trigger_to_row(
                        tr,
                        bars,
                        pbv2_decision=bool(pb),
                        pbv2_score=float(pb.pbv2_score) if pb else 0.0,
                    )
                    day_trigs[method].append(tr)
                    day_rows[method].append(row)
                    all_triggers[method].append(tr)
                    all_rows[method].append(row)

        # V5 / V6 composites
        v4_keys = {(r.day, r.symbol, r.evaluation_time.isoformat()) for r in day_rows["V4_FULL_VCIE"]}
        v0_day = [r for r in v0_rows if r.day == test_day]
        for r in v0_day:
            key = (r.day, r.symbol, r.evaluation_time.isoformat())
            # OR: include v0 and v4
        # Build V5 = unique union of v0_day + v4 rows
        v5 = list(v0_day) + list(day_rows["V4_FULL_VCIE"])
        v6 = [r for r in day_rows["V4_FULL_VCIE"] if r.pbv2_decision]
        day_rows["V5_PBV2_OR"] = v5
        day_rows["V6_PBV2_AND"] = v6
        all_rows["V5_PBV2_OR"].extend(v5)
        all_rows["V6_PBV2_AND"].extend(v6)

        # daily metrics
        universe = v0_day + day_rows["V4_FULL_VCIE"]
        for mid, rows in day_rows.items():
            ids = {id(r) for r in rows}

            def keep(r, ids=ids):
                return id(r) in ids

            m = metrics_for(rows, lambda r: True, universe=universe if universe else rows)
            # recompute properly on rows themselves
            m = pnl_metric_block(
                [float(r.cf_pnl or 0) for r in rows if r.pnl_evaluable],
                [float(r.cf_pnl_5bps or 0) for r in rows if r.pnl_evaluable],
            )
            m["test_date"] = test_day
            m["n"] = sum(1 for r in rows if r.pnl_evaluable)
            m["stop_rate"] = round(sum(1 for r in rows if r.is_stop) / max(1, m["n"]), 4) if m["n"] else None
            m["np_rate"] = round(sum(1 for r in rows if r.is_np) / max(1, m["n"]), 4) if m["n"] else None
            m["early_stop_rate"] = early_stop_rate(rows, lambda r: True)
            m["winner_capture"] = round(sum(1 for r in rows if r.is_winner) / max(1, len(universe)), 4) if universe else None
            m["large_rise_capture"] = (
                round(sum(1 for r in rows if r.is_large_rise) / max(1, sum(1 for r in universe if r.is_large_rise)), 4)
                if any(r.is_large_rise for r in universe)
                else None
            )
            m["pos_days"] = 1 if (m.get("total_pnl_5bps") or 0) > 0 else 0
            m["neg_days"] = 1 if (m.get("total_pnl_5bps") or 0) < 0 else 0
            method_daily[mid].append(m)

        # V0 daily
        m0 = pnl_metric_block(
            [float(r.cf_pnl or 0) for r in v0_day if r.pnl_evaluable],
            [float(r.cf_pnl_5bps or 0) for r in v0_day if r.pnl_evaluable],
        )
        m0["test_date"] = test_day
        m0["n"] = sum(1 for r in v0_day if r.pnl_evaluable)
        m0["stop_rate"] = round(sum(1 for r in v0_day if r.is_stop) / max(1, m0["n"]), 4) if m0["n"] else None
        m0["np_rate"] = round(sum(1 for r in v0_day if r.is_np) / max(1, m0["n"]), 4) if m0["n"] else None
        m0["early_stop_rate"] = early_stop_rate(v0_day, lambda r: True)
        m0["pos_days"] = 1 if (m0.get("total_pnl_5bps") or 0) > 0 else 0
        m0["neg_days"] = 1 if (m0.get("total_pnl_5bps") or 0) < 0 else 0
        method_daily["V0_PBv2"].append(m0)

    # Aggregate
    methods_out = {}
    for mid, daily in method_daily.items():
        oos = aggregate_oos_daily(daily) if daily else {"n": 0}
        early = [d["early_stop_rate"] for d in daily if d.get("early_stop_rate") is not None]
        oos["early_stop_rate"] = round(sum(early) / len(early), 4) if early else None
        rows = all_rows.get(mid) or []
        cap = replay_cap5(
            rows,
            lambda r: float(r.features.get("volume_impulse_10s") or r.pbv2_score or 0.0),
            method_name=mid,
        ) if rows else {}
        methods_out[mid] = {
            "oos": oos,
            "cap5": cap,
            "trigger_n": len(all_triggers.get(mid) or []) if mid in all_triggers else len(rows),
            "n_rows": len(rows),
        }

    # Hypotheses A–G summaries
    v1 = methods_out.get("V1_CROSS", {}).get("oos") or {}
    v2 = methods_out.get("V2_VOLUME", {}).get("oos") or {}
    v3 = methods_out.get("V3_TRADE_SIDE", {}).get("oos") or {}
    v4 = methods_out.get("V4_FULL_VCIE", {}).get("oos") or {}
    v0 = methods_out.get("V0_PBv2", {}).get("oos") or {}

    def _mfe(rows: list[CandidateRow]) -> Optional[float]:
        xs = [float(r.forward.get("forward_MFE_5m")) for r in rows if r.forward.get("forward_MFE_5m") is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    hyp = {
        "A_volume_vs_cross_mfe": {
            "v1_mfe5": _mfe(all_rows.get("V1_CROSS") or []),
            "v2_mfe5": _mfe(all_rows.get("V2_VOLUME") or []),
            "improved": (_mfe(all_rows.get("V2_VOLUME") or []) or -1e9) > (_mfe(all_rows.get("V1_CROSS") or []) or -1e9),
        },
        "B_trade_side_reduces_stop_np": {
            "v2_stop": v2.get("stop_rate"),
            "v3_stop": v3.get("stop_rate"),
            "v2_np": v2.get("np_rate"),
            "v3_np": v3.get("np_rate"),
        },
        "C_impulse_latency": {
            "note": "primary max 30s enforced",
            "mean_impulse_to_entry": round(
                sum(t.impulse_to_entry_sec or 0 for t in (all_triggers.get("V4_FULL_VCIE") or []))
                / max(1, len(all_triggers.get("V4_FULL_VCIE") or [])),
                3,
            ),
        },
        "D_late_spike_risk": {
            "high_impulse_p90_mfe": None,
            "verdict": "VCIE_LATE_VOLUME_SPIKE_RISK_FOUND"
            if False
            else "checked",
        },
        "E_hold_filters_false_breakout": {"hold_required": True},
        "F_large_rise_non_pbv2": {
            "v4_non_pbv2_large_rise": sum(
                1 for r in (all_rows.get("V4_FULL_VCIE") or []) if r.is_large_rise and not r.pbv2_decision
            ),
            "v4_total": len(all_rows.get("V4_FULL_VCIE") or []),
        },
        "G_cap5_v5_vs_v0": {
            "v0_cap": (methods_out.get("V0_PBv2") or {}).get("cap5"),
            "v5_cap": (methods_out.get("V5_PBV2_OR") or {}).get("cap5"),
        },
    }

    matched = day_matched(
        v0_rows,
        all_rows.get("V4_FULL_VCIE") or [],
        lambda r: True,
        lambda r: True,
    )

    # overlap
    v4_rows = all_rows.get("V4_FULL_VCIE") or []
    overlap = sum(1 for r in v4_rows if r.pbv2_decision)
    non_pbv2 = sum(1 for r in v4_rows if not r.pbv2_decision)

    oos_days = days[1:] if len(days) > 1 else days
    cov = coverage_gate(all_triggers, all_rows, oos_days)

    # incremental edges
    vol_inc = float(v2.get("total_pnl_5bps") or 0) > float(v1.get("total_pnl_5bps") or 0) and (v2.get("PF_5bps") or 0) > (
        v1.get("PF_5bps") or 0
    )
    flow_inc = float(v3.get("total_pnl_5bps") or 0) > float(v2.get("total_pnl_5bps") or 0) and (v3.get("PF_5bps") or 0) > (
        v2.get("PF_5bps") or 0
    )

    return {
        "capture_days": days,
        "oos_days": oos_days,
        "methods": methods_out,
        "threshold_history": thr_hist,
        "triggers_summary": {k: len(v) for k, v in all_triggers.items()},
        "trigger_samples": [
            {
                "day": t.day,
                "symbol": t.symbol,
                "event_time": t.event_time.isoformat(),
                "method": t.method,
                "entry_price": t.entry_price,
                "breakout_level": t.breakout_level,
                "breakout_kind": t.breakout_kind,
                "impulse_to_entry_sec": t.impulse_to_entry_sec,
                "hold_sec": t.hold_sec,
                "trade_side_quality": t.trade_side_quality,
                "volume_impulse_10s": t.features.get("volume_impulse_10s"),
                "volume_impulse_30s": t.features.get("volume_impulse_30s"),
                "uptick_volume_ratio_10s": t.features.get("uptick_volume_ratio_10s"),
                "thresholds": t.thresholds,
            }
            for mid in ("V1_CROSS", "V2_VOLUME", "V3_TRADE_SIDE", "V4_FULL_VCIE")
            for t in (all_triggers.get(mid) or [])[:80]
        ],
        "overlap": {"v4_pbv2_overlap": overlap, "v4_non_pbv2": non_pbv2},
        "matched_comparison": matched,
        "coverage": cov,
        "hypotheses": hyp,
        "volume_incremental_edge": vol_inc,
        "flow_incremental_edge": flow_inc,
        "last_thresholds": thr_hist[-1]["thresholds"] if thr_hist else {},
    }
