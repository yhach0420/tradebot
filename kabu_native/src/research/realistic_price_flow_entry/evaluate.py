"""Chronological OOS evaluation for RPFE methods R0–R8."""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Callable, Optional, Sequence

from research.pbv2_zero_base_revalidation.cap5 import replay_cap5
from research.pbv2_zero_base_revalidation.metrics import aggregate_oos_daily, metrics_for, pnl_metric_block
from research.pbv2_zero_base_revalidation.panel import CandidateRow, PricePoint
from research.pbv2_zero_base_revalidation.walk_forward import chronological_oos
from research.realistic_price_flow_entry.constants import (
    MIN_AM_COMPLETE_DAYS,
    MIN_DYNAMIC_COMPLETE_ROWS,
    MIN_OOS_DAYS,
)
from research.realistic_price_flow_entry.features import dynamic_complete
from research.realistic_price_flow_entry.state_machine import (
    PATTERN_A_SPECS,
    PATTERN_B_SPECS,
    TriggerEvent,
    audit_state_machine_integrity,
    fit_thresholds,
    narrative,
    run_pattern_stream,
)

KeepFn = Callable[[CandidateRow], bool]


def _event_key(r: CandidateRow) -> tuple:
    return (r.day, r.symbol, r.evaluation_time.isoformat(), r.session)


def triggers_to_keep(triggers: Sequence[TriggerEvent]) -> KeepFn:
    keys = {_event_key(t.row) for t in triggers}

    def keep(r: CandidateRow) -> bool:
        return _event_key(r) in keys

    return keep


def pbv2_keep(r: CandidateRow) -> bool:
    return bool(r.pbv2_decision or r.accept)


def _is_stop_reason(reason: str) -> bool:
    r = (reason or "").lower()
    return "stop" in r


def early_stop_rate(rows: Sequence[CandidateRow], keep: KeepFn) -> Optional[float]:
    """early_stop := STOP reason AND hold_sec <= 300."""
    kept = [r for r in rows if keep(r) and r.pnl_evaluable]
    if not kept:
        return None
    n = 0
    for r in kept:
        if not _is_stop_reason(r.cf_exit_reason or r.actual_exit_reason or ""):
            continue
        hold = r.cf_hold_sec
        if hold is not None and hold <= 300:
            n += 1
    return round(n / len(kept), 4)


def stop_hold_buckets(rows: Sequence[CandidateRow], keep: KeepFn) -> dict[str, Any]:
    kept = [r for r in rows if keep(r) and r.pnl_evaluable]
    buckets = {
        "total_stop": 0,
        "stop_le_60": 0,
        "stop_le_180": 0,
        "stop_le_300": 0,
        "stop_gt_300": 0,
        "hold_time_missing": 0,
        "n_kept": len(kept),
    }
    for r in kept:
        if not _is_stop_reason(r.cf_exit_reason or r.actual_exit_reason or ""):
            continue
        buckets["total_stop"] += 1
        hold = r.cf_hold_sec
        if hold is None:
            buckets["hold_time_missing"] += 1
            continue
        if hold <= 60:
            buckets["stop_le_60"] += 1
        if hold <= 180:
            buckets["stop_le_180"] += 1
        if hold <= 300:
            buckets["stop_le_300"] += 1
        else:
            buckets["stop_gt_300"] += 1
    return buckets


def audit_early_stop_labels(methods: dict[str, Any]) -> dict[str, Any]:
    """If early_stop_rate == stop_rate for all methods → label implementation blocked."""
    equal_all = True
    compared = 0
    for mid, m in methods.items():
        o = m.get("oos") or {}
        es = o.get("early_stop_rate")
        sr = o.get("stop_rate")
        if es is None or sr is None:
            continue
        compared += 1
        if abs(float(es) - float(sr)) > 1e-9:
            equal_all = False
    blocked = compared > 0 and equal_all
    return {
        "methods_compared": compared,
        "early_stop_equals_stop_all_methods": equal_all if compared else None,
        "gate_ok": not blocked,
        "verdict": "EARLY_STOP_LABEL_BLOCKED" if blocked else "EARLY_STOP_LABEL_FIXED",
    }


def mfe_mae_summary(rows: Sequence[CandidateRow], keep: KeepFn) -> dict[str, Any]:
    mfes, maes = [], []
    for r in rows:
        if not keep(r):
            continue
        mfe = r.forward.get("forward_MFE_5m")
        mae = r.forward.get("forward_MAE_5m")
        if mfe is not None:
            mfes.append(float(mfe))
        if mae is not None:
            maes.append(float(mae))
    import statistics

    def _stat(xs):
        if not xs:
            return None
        return {
            "n": len(xs),
            "mean": round(sum(xs) / len(xs), 4),
            "median": round(statistics.median(xs), 4),
            "p25": round(sorted(xs)[max(0, int(0.25 * len(xs)) - 1)], 4),
            "p75": round(sorted(xs)[min(len(xs) - 1, int(0.75 * len(xs)))], 4),
        }

    return {"mfe_5m": _stat(mfes), "mae_5m": _stat(maes)}


def dynamic_gate(panel: Sequence[CandidateRow], oos_days: Sequence[str]) -> dict[str, Any]:
    complete = [r for r in panel if dynamic_complete(r)]
    am_days = sorted({r.day for r in complete if r.session_bucket == "AM"})
    pm_days = sorted({r.day for r in complete if r.session_bucket == "PM"})
    oos_complete_days = [d for d in oos_days if d in {r.day for r in complete}]
    ok = (
        len(oos_days) >= MIN_OOS_DAYS
        and len(complete) >= MIN_DYNAMIC_COMPLETE_ROWS
        and len(am_days) >= MIN_AM_COMPLETE_DAYS
        and len(pm_days) >= MIN_PM_COMPLETE_DAYS
    )
    return {
        "complete_rows_total": len(complete),
        "am_complete_days": am_days,
        "pm_complete_days": pm_days,
        "oos_days": list(oos_days),
        "oos_complete_days": oos_complete_days,
        "n_oos_days": len(oos_days),
        "gate_ok": ok,
        "verdict": "DYNAMIC_COVERAGE_OK" if ok else "RPFE_FLOW_INSUFFICIENT_DATA",
    }


def _metrics_ids(panel: Sequence[CandidateRow], ids: set[int]) -> dict[str, Any]:
    return metrics_for(panel, lambda r: id(r) in ids)


def day_matched_comparison(
    panel: Sequence[CandidateRow],
    keep_a: KeepFn,
    keep_b: KeepFn,
    *,
    score_a: Optional[Callable[[CandidateRow], float]] = None,
    score_b: Optional[Callable[[CandidateRow], float]] = None,
    n_random: int = 20,
    seed: int = 42,
) -> dict[str, Any]:
    """Per test-day min(n_a, n_b) matching only — no global same-n pooling."""
    days = sorted({r.day for r in panel})
    sa = score_a or (lambda r: float(r.pbv2_score or 0.0))
    sb = score_b or (lambda r: float(r.pbv2_score or 0.0))

    def _collect(mode: str) -> tuple[list[CandidateRow], list[CandidateRow], list[dict[str, Any]]]:
        a_all: list[CandidateRow] = []
        b_all: list[CandidateRow] = []
        day_rows: list[dict[str, Any]] = []
        rng = random.Random(seed)
        for day in days:
            a_d = [r for r in panel if r.day == day and keep_a(r) and r.pnl_evaluable]
            b_d = [r for r in panel if r.day == day and keep_b(r) and r.pnl_evaluable]
            n = min(len(a_d), len(b_d))
            if n <= 0:
                day_rows.append({"day": day, "matched_n_day": 0, "a_n": len(a_d), "b_n": len(b_d)})
                continue
            if mode == "same_n":
                a_d.sort(key=lambda r: (r.evaluation_time, r.symbol))
                b_d.sort(key=lambda r: (r.evaluation_time, r.symbol))
                aa, bb = a_d[:n], b_d[:n]
            elif mode == "same_cap":
                # prefer rows not cap_blocked; then time order
                a_d.sort(key=lambda r: (r.cap_blocked, r.evaluation_time, r.symbol))
                b_d.sort(key=lambda r: (r.cap_blocked, r.evaluation_time, r.symbol))
                aa, bb = a_d[:n], b_d[:n]
            elif mode == "same_window":
                # hour bucket alignment: take earliest n in overlapping hours
                def hour(r: CandidateRow) -> int:
                    return r.evaluation_time.hour

                hours = sorted(set(hour(r) for r in a_d) & set(hour(r) for r in b_d))
                aa, bb = [], []
                for h in hours:
                    ah = [r for r in a_d if hour(r) == h]
                    bh = [r for r in b_d if hour(r) == h]
                    k = min(len(ah), len(bh))
                    ah.sort(key=lambda r: (r.evaluation_time, r.symbol))
                    bh.sort(key=lambda r: (r.evaluation_time, r.symbol))
                    aa.extend(ah[:k])
                    bb.extend(bh[:k])
                n2 = min(len(aa), len(bb), n)
                aa, bb = aa[:n2], bb[:n2]
            elif mode == "rank_a":
                a_d.sort(key=lambda r: (-sa(r), r.evaluation_time, r.symbol))
                b_d.sort(key=lambda r: (r.evaluation_time, r.symbol))
                aa, bb = a_d[:n], b_d[:n]
            elif mode == "rank_b":
                a_d.sort(key=lambda r: (r.evaluation_time, r.symbol))
                b_d.sort(key=lambda r: (-sb(r), r.evaluation_time, r.symbol))
                aa, bb = a_d[:n], b_d[:n]
            else:
                aa, bb = a_d[:n], b_d[:n]
            a_all.extend(aa)
            b_all.extend(bb)
            day_rows.append(
                {
                    "day": day,
                    "matched_n_day": len(aa),
                    "a_n": len(a_d),
                    "b_n": len(b_d),
                }
            )
        return a_all, b_all, day_rows

    out: dict[str, Any] = {"mode": "day_matched", "global_pool_forbidden": True}
    for mode, key in (
        ("same_n", "same_day_same_n"),
        ("same_cap", "same_day_same_cap_usage"),
        ("same_window", "same_day_same_opportunity_window"),
        ("rank_a", "pbv2_native_ranking"),
        ("rank_b", "rpfe_ranking"),
    ):
        aa, bb, day_rows = _collect(mode)
        ka, kb = {id(r) for r in aa}, {id(r) for r in bb}
        out[key] = {
            "n_matched": len(aa),
            "days_with_match": sum(1 for d in day_rows if d["matched_n_day"] > 0),
            "per_day": day_rows,
            "method_a": _metrics_ids(panel, ka),
            "method_b": _metrics_ids(panel, kb),
        }

    # random repeated baseline: within-day shuffle, average over seeds
    pnl_a, pnl_b = [], []
    for i in range(n_random):
        rng = random.Random(seed + i)
        aa, bb = [], []
        for day in days:
            a_d = [r for r in panel if r.day == day and keep_a(r) and r.pnl_evaluable]
            b_d = [r for r in panel if r.day == day and keep_b(r) and r.pnl_evaluable]
            n = min(len(a_d), len(b_d))
            if n <= 0:
                continue
            rng.shuffle(a_d)
            rng.shuffle(b_d)
            aa.extend(a_d[:n])
            bb.extend(b_d[:n])
        ma = _metrics_ids(panel, {id(r) for r in aa})
        mb = _metrics_ids(panel, {id(r) for r in bb})
        pnl_a.append(float(ma.get("total_pnl_5bps") or 0))
        pnl_b.append(float(mb.get("total_pnl_5bps") or 0))
    out["random_repeated_baseline"] = {
        "n_repeats": n_random,
        "method_a_mean_pnl_5bps": round(sum(pnl_a) / len(pnl_a), 4) if pnl_a else None,
        "method_b_mean_pnl_5bps": round(sum(pnl_b) / len(pnl_b), 4) if pnl_b else None,
    }

    # integrity: no side may be single-day-only while the other spans many days in same_n match
    sn = out["same_day_same_n"]
    a_days = {d["day"] for d in sn["per_day"] if d["matched_n_day"] > 0}
    concentration_ok = True
    # matched sets are day-aligned by construction; flag if one method's raw eligible days diverge wildly
    raw_a_days = {r.day for r in panel if keep_a(r) and r.pnl_evaluable}
    raw_b_days = {r.day for r in panel if keep_b(r) and r.pnl_evaluable}
    if raw_a_days and raw_b_days:
        if (len(raw_a_days) == 1 and len(raw_b_days) >= 3) or (len(raw_b_days) == 1 and len(raw_a_days) >= 3):
            # comparison still day-matched, but note concentration risk on raw eligibility
            concentration_ok = False

    out["raw_eligible_days_a"] = sorted(raw_a_days)
    out["raw_eligible_days_b"] = sorted(raw_b_days)
    out["matched_days"] = sorted(a_days)
    out["gate_ok"] = True  # day-matched procedure applied
    out["concentration_warning"] = not concentration_ok
    out["verdict"] = "DAY_MATCHED_COMPARISON_READY"
    return out


def matched_comparison(
    panel: Sequence[CandidateRow],
    keep_a: KeepFn,
    keep_b: KeepFn,
    *,
    n_target: Optional[int] = None,
) -> dict[str, Any]:
    """Backward-compatible name → day-matched same-n (ignores global n_target)."""
    d = day_matched_comparison(panel, keep_a, keep_b)
    sn = d["same_day_same_n"]
    return {
        "n_matched": sn["n_matched"],
        "method_a": sn["method_a"],
        "method_b": sn["method_b"],
        "day_matched": d,
        "legacy_global_n_target_ignored": n_target,
    }


def run_oos(
    panel: Sequence[CandidateRow],
    price_paths: Optional[dict[tuple[str, str], list[PricePoint]]] = None,
) -> dict[str, Any]:
    folds = chronological_oos(panel, min_train_days=3)
    if folds and folds[0].get("leakage_blocked"):
        return {"leakage_blocked": True}

    oos_days = [f["test_date"] for f in folds]
    dyn = dynamic_gate(panel, oos_days)
    paths = price_paths or {}

    method_daily: dict[str, list[dict[str, Any]]] = defaultdict(list)
    thr_hist: list[dict[str, Any]] = []
    all_triggers: list[dict[str, Any]] = []
    all_trigger_events: list[TriggerEvent] = []
    narratives: list[dict[str, Any]] = []

    # Accumulate OOS trigger keys across folds (do not keep only last fold).
    oos_keys: dict[str, set[tuple]] = defaultdict(set)

    for fold in folds:
        train, test = fold["train_rows"], fold["test_rows"]
        thr_a = fit_thresholds(train, PATTERN_A_SPECS)
        thr_b = fit_thresholds(train, PATTERN_B_SPECS)
        thr = thr_a
        thr.values.update({k: v for k, v in thr_b.values.items() if k not in thr.values})

        thr_hist.append(
            {
                "test_date": fold["test_date"],
                "n_thresholds": len(thr.values),
                "thresholds": dict(thr.values),
            }
        )

        test_day_rows = [r for r in panel if r.day == fold["test_date"]]

        t_a_price = run_pattern_stream(
            test_day_rows, pattern="A", thr=thr, require_flow=False, price_paths=paths
        )
        t_a_flow = run_pattern_stream(
            test_day_rows, pattern="A", thr=thr, require_flow=True, price_paths=paths
        )
        t_b_price = run_pattern_stream(
            test_day_rows, pattern="B", thr=thr, require_flow=False, price_paths=paths
        )
        t_b_flow = run_pattern_stream(
            test_day_rows, pattern="B", thr=thr, require_flow=True, price_paths=paths
        )

        packs = {
            "R0_PBv2": ([], pbv2_keep),
            "R1_Pullback_PRICE": (t_a_price, triggers_to_keep(t_a_price)),
            "R2_Pullback_FLOW": (t_a_flow, triggers_to_keep(t_a_flow)),
            "R3_Compression_PRICE": (t_b_price, triggers_to_keep(t_b_price)),
            "R4_Compression_FLOW": (t_b_flow, triggers_to_keep(t_b_flow)),
            "R5_A_OR_B_PRICE": (
                t_a_price + t_b_price,
                triggers_to_keep(t_a_price + t_b_price),
            ),
            "R6_A_OR_B_FLOW": (
                t_a_flow + t_b_flow,
                triggers_to_keep(t_a_flow + t_b_flow),
            ),
        }
        k5 = packs["R5_A_OR_B_PRICE"][1]
        packs["R7_PBv2_OR_RPFE"] = (
            packs["R5_A_OR_B_PRICE"][0],
            lambda r, k5=k5: pbv2_keep(r) or k5(r),
        )
        packs["R8_PBv2_AND_RPFE"] = (
            packs["R5_A_OR_B_PRICE"][0],
            lambda r, k5=k5: pbv2_keep(r) and k5(r),
        )

        for mid, (trigs, keep) in packs.items():
            m = metrics_for(test, keep, universe=test)
            m["test_date"] = fold["test_date"]
            m["early_stop_rate"] = early_stop_rate(test, keep)
            m["stop_hold_buckets"] = stop_hold_buckets(test, keep)
            method_daily[mid].append(m)
            if mid == "R0_PBv2":
                continue
            for t in trigs or []:
                oos_keys[mid].add(_event_key(t.row))
            # R7/R8 are composites — also record keys from underlying RPFE keep
            if mid in ("R7_PBv2_OR_RPFE", "R8_PBv2_AND_RPFE"):
                for t in packs["R5_A_OR_B_PRICE"][0]:
                    oos_keys[mid].add(_event_key(t.row))

        fold_trigs = t_a_price + t_a_flow + t_b_price + t_b_flow
        all_trigger_events.extend(fold_trigs)
        for t in fold_trigs:
            all_triggers.append(
                {
                    "day": t.day,
                    "symbol": t.symbol,
                    "evaluation_time": t.entry_time,
                    "pattern": t.pattern,
                    "mode": t.mode,
                    "state_history": " | ".join(t.state_history[-12:]),
                    "context_time": t.context_time,
                    "setup_time": t.setup_time,
                    "sell_weak_time": t.sell_weak_time,
                    "flow_time": t.flow_time,
                    "price_trigger_time": t.price_trigger_time,
                    "entry_time": t.entry_time,
                    "confirmation_latency_sec": t.confirmation_latency_sec,
                    "context_to_setup_sec": t.context_to_setup_sec,
                    "setup_to_sell_weak_sec": t.setup_to_sell_weak_sec,
                    "sell_weak_to_buy_sec": t.sell_weak_to_buy_sec,
                    "buy_to_price_trigger_sec": t.buy_to_price_trigger_sec,
                    "total_confirmation_latency_sec": t.total_confirmation_latency_sec,
                    "transitions_same_timestamp": t.transitions_same_timestamp,
                    "states_advanced_per_observation": t.states_advanced_per_observation,
                    "real_micro_high_cross": t.real_micro_high_cross,
                    "real_range_high_cross": t.real_range_high_cross,
                    "episode_id": t.episode_id,
                    "setup_id": t.setup_id,
                    "flow_status": t.flow_status,
                    "price_trigger_status": t.price_trigger_status,
                    "dynamic_evaluable": t.dynamic_evaluable,
                    "test_date": fold["test_date"],
                }
            )
            if len(narratives) < 40:
                narratives.append(
                    {
                        "pattern": t.pattern,
                        "mode": t.mode,
                        "text": narrative(t),
                        "day": t.day,
                        "symbol": t.symbol,
                    }
                )

    def _keep_from_keys(mid: str) -> KeepFn:
        keys = oos_keys.get(mid, set())
        if mid == "R0_PBv2":
            return pbv2_keep
        if mid == "R7_PBv2_OR_RPFE":
            k5 = oos_keys.get("R5_A_OR_B_PRICE", set())

            def keep(r: CandidateRow, k5=k5) -> bool:
                return pbv2_keep(r) or _event_key(r) in k5

            return keep
        if mid == "R8_PBv2_AND_RPFE":
            k5 = oos_keys.get("R5_A_OR_B_PRICE", set())

            def keep(r: CandidateRow, k5=k5) -> bool:
                return pbv2_keep(r) and _event_key(r) in k5

            return keep

        def keep(r: CandidateRow, keys=keys) -> bool:
            return _event_key(r) in keys

        return keep

    last_keeps: dict[str, KeepFn] = {mid: _keep_from_keys(mid) for mid in method_daily}

    methods = {}
    for mid, daily in method_daily.items():
        oos = aggregate_oos_daily(daily)
        early = [d["early_stop_rate"] for d in daily if d.get("early_stop_rate") is not None]
        oos["early_stop_rate"] = round(sum(early) / len(early), 4) if early else None
        # merge stop buckets
        buck = {
            "total_stop": 0,
            "stop_le_60": 0,
            "stop_le_180": 0,
            "stop_le_300": 0,
            "stop_gt_300": 0,
            "hold_time_missing": 0,
            "n_kept": 0,
        }
        for d in daily:
            b = d.get("stop_hold_buckets") or {}
            for k in buck:
                buck[k] += int(b.get(k) or 0)
        oos["stop_hold_buckets"] = buck
        keep = last_keeps.get(mid, lambda r: False)
        cap = replay_cap5(panel, lambda r, keep=keep: (1.0 if keep(r) else None), method_name=mid)
        kept = [r for r in panel if keep(r) and r.pnl_evaluable]
        y5 = [float(r.cf_pnl_5bps or 0) for r in kept]
        yraw = [float(r.cf_pnl or 0) for r in kept]
        cap.update({k: v for k, v in pnl_metric_block(yraw, y5).items() if k.startswith(("total", "gross", "PF", "metric"))})
        methods[mid] = {
            "oos": oos,
            "cap5": cap,
            "mfe_mae": mfe_mae_summary(panel, keep),
            "trigger_n": sum(1 for t in all_triggers if _trigger_matches_method(t, mid)),
        }

    m0 = methods["R0_PBv2"]["oos"]

    def edge(mid: str) -> bool:
        o = methods[mid]["oos"]
        if o.get("metric_integrity_blocked"):
            return False
        keep_ratio = (o.get("n") or 0) / max(1, m0.get("n") or 1)
        lr_r = (o.get("large_rise_capture") or 0) / max(1e-9, m0.get("large_rise_capture") or 1e-9)
        w_r = (o.get("winner_capture") or 0) / max(1e-9, m0.get("winner_capture") or 1e-9)
        return (
            float(o.get("total_pnl_5bps") or 0) > float(m0.get("total_pnl_5bps") or 0)
            and (o.get("PF_5bps") or 0) > (m0.get("PF_5bps") or 0)
            and (o.get("pos_days") or 0) > (o.get("neg_days") or 0)
            and keep_ratio >= 0.25
            and lr_r >= 0.8
            and w_r >= 0.8
            and (o.get("stop_rate") is None or m0.get("stop_rate") is None or float(o["stop_rate"]) <= float(m0["stop_rate"]) + 1e-9)
            and (o.get("np_rate") is None or m0.get("np_rate") is None or float(o["np_rate"]) <= float(m0["np_rate"]) + 1e-9)
        )

    flow_inc = {
        "pullback": _incremental(methods, "R1_Pullback_PRICE", "R2_Pullback_FLOW"),
        "compression": _incremental(methods, "R3_Compression_PRICE", "R4_Compression_FLOW"),
    }

    # day-matched: only OOS days
    oos_panel = [r for r in panel if r.day in set(oos_days)]
    matched = day_matched_comparison(
        oos_panel,
        pbv2_keep,
        last_keeps.get("R5_A_OR_B_PRICE", lambda r: False),
        score_a=lambda r: float(r.pbv2_score or 0.0),
        score_b=lambda r: float(r.features.get("f_mom") or 0.0),
    )

    sm_audit = audit_state_machine_integrity(all_trigger_events)
    # price cross readiness
    proxy_rejected = False
    for t in all_trigger_events:
        if t.mode == "PRICE" and t.price_trigger_status == "NOT_EVALUABLE":
            proxy_rejected = True
            break
    # ENTRY triggers should have real cross
    entries_without_cross = sum(
        1
        for t in all_trigger_events
        if t.mode == "PRICE" and not t.real_micro_high_cross and not t.real_range_high_cross
    )
    price_cross = {
        "entries_without_real_cross": entries_without_cross,
        "real_micro_high_cross_n": sm_audit["real_micro_high_cross_n"],
        "real_range_high_cross_n": sm_audit["real_range_high_cross_n"],
        "gate_ok": entries_without_cross == 0 and not proxy_rejected,
        "verdict": (
            "PRICE_TRIGGER_PROXY_REJECTED"
            if proxy_rejected or entries_without_cross > 0
            else "REAL_PRICE_CROSS_TRIGGER_READY"
        ),
    }

    early_audit = audit_early_stop_labels(methods)

    for mid, m in methods.items():
        by = (m["oos"].get("daily") or [])
        pnls = [float(x.get("pnl_5bps") or 0) for x in by]
        tot = sum(pnls)
        max_share = max((abs(x) for x in pnls), default=0) / max(1e-9, abs(tot)) if tot else 0
        m["max_day_pnl_share"] = round(max_share, 4)

    return {
        "folds_n": len(folds),
        "oos_days": oos_days,
        "methods": methods,
        "threshold_history": thr_hist,
        "triggers": all_triggers[:8000],
        "narratives": narratives,
        "dynamic_coverage": dyn,
        "flow_incremental": flow_inc,
        "matched_comparison": matched,
        "state_machine_integrity": sm_audit,
        "price_cross_integrity": price_cross,
        "early_stop_label_audit": early_audit,
        "edges": {mid: edge(mid) for mid in methods if mid != "R0_PBv2"},
        "pattern_counts": {
            "A_PRICE": sum(1 for t in all_triggers if t["pattern"] == "A" and t["mode"] == "PRICE"),
            "A_FLOW": sum(1 for t in all_triggers if t["pattern"] == "A" and t["mode"] == "FLOW"),
            "B_PRICE": sum(1 for t in all_triggers if t["pattern"] == "B" and t["mode"] == "PRICE"),
            "B_FLOW": sum(1 for t in all_triggers if t["pattern"] == "B" and t["mode"] == "FLOW"),
        },
    }


def _incremental(methods: dict, base: str, richer: str) -> dict[str, Any]:
    b = methods[base]["oos"]
    r = methods[richer]["oos"]
    return {
        "base": base,
        "richer": richer,
        "delta_pnl_5bps": round(float(r.get("total_pnl_5bps") or 0) - float(b.get("total_pnl_5bps") or 0), 2),
        "delta_PF_5bps": None
        if r.get("PF_5bps") is None or b.get("PF_5bps") is None
        else round(float(r["PF_5bps"]) - float(b["PF_5bps"]), 4),
        "base_n": b.get("n"),
        "richer_n": r.get("n"),
        "incremental_edge": float(r.get("total_pnl_5bps") or 0) > float(b.get("total_pnl_5bps") or 0)
        and (r.get("PF_5bps") or 0) > (b.get("PF_5bps") or 0),
    }


def _trigger_matches_method(t: dict[str, Any], mid: str) -> bool:
    if mid.startswith("R1"):
        return t["pattern"] == "A" and t["mode"] == "PRICE"
    if mid.startswith("R2"):
        return t["pattern"] == "A" and t["mode"] == "FLOW"
    if mid.startswith("R3"):
        return t["pattern"] == "B" and t["mode"] == "PRICE"
    if mid.startswith("R4"):
        return t["pattern"] == "B" and t["mode"] == "FLOW"
    if mid.startswith("R5"):
        return t["mode"] == "PRICE"
    if mid.startswith("R6"):
        return t["mode"] == "FLOW"
    return False
