"""Confirmed-entry evaluation. No precursor inputs. No Day2 primary rewrite."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1.engine import _epoch, clock_dt, mean, median
from research.futures_reversal_confirmed_entry_day1_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    DAY2_PRIMARY,
    DAY2_PRIMARY_ID,
    KIND,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    NEXT_D,
    ORIGINAL_FIVE,
    PARENT_ID,
    TRADING_DATE,
    VERDICT_A,
    VERDICT_B,
    VERDICT_C,
    VERDICT_D,
)
from research.futures_reversal_confirmed_entry_day1_v1.engine import run_state_machine
from research.futures_reversal_confirmed_entry_day1_v1.isolation import AUDIT_OUT, NATIVE, PRECURSOR_OUT
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")


def _long(pack: dict[str, Any], hz: str, stat: str) -> Optional[float]:
    v = ((pack or {}).get(hz) or {}).get(stat)
    return None if v is None else float(v)


def _event_stats(events: list[dict[str, Any]], side: str, hz: str, metric: str) -> dict[str, Any]:
    xs = []
    for e in events:
        v = _long(e.get(side) or {}, hz, metric)
        if v is not None:
            xs.append(v)
    pos = sum(1 for v in xs if v > 0)
    neg = sum(1 for v in xs if v < 0)
    return {
        "n": len(xs),
        "mean": mean(xs),
        "median": median(xs),
        "pos_n": pos,
        "neg_n": neg,
    }


def _parent_consistency() -> dict[str, Any]:
    prec_path = PRECURSOR_OUT / "report.json"
    audit_path = AUDIT_OUT / "report.json"
    prec = {}
    audit = {}
    if prec_path.is_file():
        try:
            prec = json.loads(prec_path.read_text(encoding="utf-8"))
        except Exception:
            prec = {}
    if audit_path.is_file():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except Exception:
            audit = {}
    written = (prec.get("decision") or {}).get("POST_HOC_REVERSAL_DEPENDENT")
    audit_dec = dict(audit.get("decision") or {})
    audit_verdict = audit_dec.get("VERDICT") or (audit.get("answers") or {}).get("34_VERDICT")
    return {
        "precursor_report": str(prec_path),
        "precursor_decision_POST_HOC_REVERSAL_DEPENDENT": written,
        "inconsistent_with_mechanism_audit": written is False,
        "mechanism_audit_verdict": audit_verdict,
        "mechanism_audit_CURRENT_CONTEXT_NOT_SUFFICIENT": audit_dec.get("CURRENT_CONTEXT_NOT_SUFFICIENT"),
        "mechanism_class_day1": "FUTURE_REVERSAL_DEPENDENT",
        "corrected_metadata": {
            "T_time_BOTH_DOWN_insufficient": True,
            "absolute_LONG_future_reversal_dependent": True,
            "POST_HOC_REVERSAL_DEPENDENT": True,
            "CURRENT_CONTEXT_NOT_SUFFICIENT": True,
        },
        "artifact_overwritten": False,
        "note": (
            "Precursor report.json left decision.POST_HOC_REVERSAL_DEPENDENT=false "
            "because CASE B did not close the family. Mechanism audit already found "
            "absolute LONG only on clocks that later flipped to BOTH_UP. This file "
            "records the correction without destructive overwrite."
        ),
    }


def _clock_epoch(day: str, label: str) -> float:
    hh, mm = label.split(":")
    return _epoch(clock_dt(day, (int(hh), int(mm))))


def _trace_original(day: str, episodes: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    by_ep = {e["episode_id"]: e for e in events}
    out = {}
    for lab in ORIGINAL_FIVE:
        te = _clock_epoch(day, lab)
        hit = None
        for ep in episodes:
            mins = [float(x) for x in ep.get("both_down_minutes") or []]
            if any(abs(x - te) < 1e-6 for x in mins):
                hit = ep
                break
        if hit is None:
            out[lab] = {"clock": lab, "in_episode": False}
            continue
        ev = by_ep.get(hit["episode_id"])
        row = {
            "clock": lab,
            "in_episode": True,
            "episode_id": hit["episode_id"],
            "episode_start": hit.get("start"),
            "episode_end": hit.get("end"),
            "status": hit.get("status"),
            "arm_time": (None if ev is None else ev.get("arm_hm")) or hit.get("arm_hm"),
            "select_time": None if ev is None else ev.get("select_hm"),
            "trigger_time": None if ev is None else ev.get("trigger_hm"),
            "latency_sec": None if ev is None else ev.get("latency_sec"),
            "entry_ask_mean": None if ev is None else (ev.get("TOP") or {}).get("entry_ask_mean"),
            "entry_asks": None if ev is None else (ev.get("TOP") or {}).get("asks"),
            "top16": (None if ev is None else ev.get("top16")) or hit.get("top16"),
            "expire_hm": hit.get("expire_hm"),
        }
        if ev is not None:
            for hz in ("1m", "3m", "5m", "10m"):
                row[f"TOP_LONG_{hz}"] = _long(ev.get("TOP") or {}, hz, "LONG_mean")
                row[f"TOP_LONG_{hz}_median"] = _long(ev.get("TOP") or {}, hz, "LONG_median")
                row[f"TOP_MID_{hz}"] = _long(ev.get("TOP") or {}, hz, "MID_mean")
            row["BOTTOM_LONG_10m"] = _long(ev.get("BOTTOM") or {}, "10m", "LONG_mean")
            row["ALL48_LONG_10m"] = _long(ev.get("ALL48") or {}, "10m", "LONG_mean")
            row["TOP_BOTTOM_LONG_10m"] = ev.get("TOP_BOTTOM_LONG_10m")
            row["confirmation_received_at_le_entry"] = ev.get("confirmation_received_at_le_entry")
        out[lab] = row
    return out


def _classify(events: list[dict[str, Any]], traces: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    top10 = _event_stats(events, "TOP", "10m", "LONG_mean")
    bot10 = _event_stats(events, "BOTTOM", "10m", "LONG_mean")
    tb = None
    if top10["mean"] is not None and bot10["mean"] is not None:
        tb = float(top10["mean"]) - float(bot10["mean"])
    med = top10["median"]
    mn = top10["mean"]

    def _trig_long(row: dict[str, Any]) -> Optional[float]:
        if row.get("status") != "TRIGGER":
            return None
        v = row.get("TOP_LONG_10m")
        return None if v is None else float(v)

    t0920 = traces.get("09:20") or {}
    t1020 = traces.get("10:20") or {}
    t1030 = traces.get("10:30") or {}
    v0920 = _trig_long(t0920)
    v1020 = _trig_long(t1020)
    v1030 = _trig_long(t1030)
    survives = v0920 is not None and v0920 > 0 and v1020 is not None and v1020 > 0
    orig_triggered = [v for v in (v0920, v1020) if v is not None]
    priced = bool(orig_triggered) and all(v <= 0 for v in orig_triggered)
    t1030_neg = v1030 is not None and v1030 < 0
    abs_ok = mn is not None and med is not None and mn > 0 and med > 0
    rel_ok = tb is not None and tb > 0
    flags = {
        "top_long_mean": mn,
        "top_long_median": med,
        "top_bottom_long": tb,
        "original_0920_1020_survive": survives,
        "rebound_already_priced": priced,
        "ten30_triggered_negative": t1030_neg,
        "reversal_confirmation_sufficient": bool(survives and (not t1030_neg) and abs_ok and rel_ok),
    }
    if t1030_neg:
        return CASE_D, VERDICT_D, NEXT_D, flags
    if priced or not events:
        return CASE_C, VERDICT_C, NEXT_C, flags
    if abs_ok and rel_ok:
        return CASE_A, VERDICT_A, NEXT_A, flags
    if rel_ok:
        return CASE_B, VERDICT_B, NEXT_B, flags
    return CASE_C, VERDICT_C, NEXT_C, flags


def run_confirmed_entry(*, native_root: Optional[Path] = None, trading_date: str = TRADING_DATE) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    machine = run_state_machine(native_root=root, day=day)
    events = list(machine["events"])
    traces = _trace_original(day, list(machine["episodes"]), events)
    case, verdict, nxt, flags = _classify(events, traces)
    top1 = _event_stats(events, "TOP", "1m", "LONG_mean")
    top3 = _event_stats(events, "TOP", "3m", "LONG_mean")
    top5 = _event_stats(events, "TOP", "5m", "LONG_mean")
    top10 = _event_stats(events, "TOP", "10m", "LONG_mean")
    bot10 = _event_stats(events, "BOTTOM", "10m", "LONG_mean")
    all10 = _event_stats(events, "ALL48", "10m", "LONG_mean")
    tb = None
    if top10["mean"] is not None and bot10["mean"] is not None:
        tb = float(top10["mean"]) - float(bot10["mean"])
    vs_all = None
    if top10["mean"] is not None and all10["mean"] is not None:
        vs_all = float(top10["mean"]) - float(all10["mean"])
    pooled_top = []
    for e in events:
        pooled_top.extend([float(v) for v in (((e.get("TOP") or {}).get("10m") or {}).get("LONG_values") or [])])
    pooled10 = {"n": len(pooled_top), "mean": mean(pooled_top), "median": median(pooled_top)}
    top_mid10 = _event_stats(events, "TOP", "10m", "MID_mean")
    bot_mid10 = _event_stats(events, "BOTTOM", "10m", "MID_mean")
    all_mid10 = _event_stats(events, "ALL48", "10m", "MID_mean")
    one_trig = len({e.get("episode_id") for e in events}) == len(events)
    sel_causal = all(bool(e.get("select_before_trigger")) for e in events) if events else True
    conf_le = all(bool(e.get("confirmation_received_at_le_entry")) for e in events) if events else True
    orders = live_order_counts()
    thesis = bool(case == CASE_A)
    sentence = (
        f"On 20260911, {machine['trigger_n']} BOTH_DOWN episodes produced a causal "
        f"BOTH_UP confirmation within 180s. Selection used last pre-trigger "
        f"OBSERVED_TRADE_N_180S tercile; LONG markout used Ask at confirmation. "
        f"TOP LONG 10m mean={top10['mean']} median={top10['median']}; "
        f"BOTTOM={bot10['mean']}; ALL48={all10['mean']}; TOP-BOTTOM={tb}. "
        f"09:20 confirmed={traces.get('09:20', {}).get('TOP_LONG_10m')} "
        f"10:20={traces.get('10:20', {}).get('TOP_LONG_10m')} "
        f"10:30={traces.get('10:30', {}).get('TOP_LONG_10m')}."
    )
    answers = {
        "1_BOTH_DOWN_episode_N": machine["episode_n"],
        "2_armed_N": machine["armed_n"],
        "3_expired_N": machine["expired_n"],
        "4_confirmed_BOTH_UP_trigger_N": machine["trigger_n"],
        "5_trigger_timestamps": [e.get("trigger_hm") for e in events],
        "6_arm_to_trigger_latencies_sec": [e.get("latency_sec") for e in events],
        "7_future_leakage_N": machine["future_leakage_n"],
        "8_TOP16_selection_time_causal": bool(sel_causal and conf_le),
        "9_TOP_LONG_1m_mean_median": {"mean": top1["mean"], "median": top1["median"]},
        "10_TOP_LONG_3m": {"mean": top3["mean"], "median": top3["median"]},
        "11_TOP_LONG_5m": {"mean": top5["mean"], "median": top5["median"]},
        "12_TOP_LONG_10m": {"mean": top10["mean"], "median": top10["median"]},
        "13_BOTTOM_LONG_10m": {"mean": bot10["mean"], "median": bot10["median"]},
        "14_TOP_BOTTOM_LONG_10m": tb,
        "15_ALL48_LONG_10m": {"mean": all10["mean"], "median": all10["median"]},
        "16_TOP_incremental_vs_ALL48": vs_all,
        "17_TOP_positive_event_N": top10["pos_n"],
        "18_TOP_negative_event_N": top10["neg_n"],
        "19_original_09:20_episode": traces.get("09:20"),
        "20_original_10:20_episode": traces.get("10:20"),
        "21_09:40": traces.get("09:40"),
        "22_10:30": traces.get("10:30"),
        "23_10:40": traces.get("10:40"),
        "24_edge_survives_confirmation_delay": flags["original_0920_1020_survive"],
        "25_rebound_already_priced": flags["rebound_already_priced"],
        "26_reversal_confirmation_sufficient": flags["reversal_confirmation_sufficient"],
        "27_exact_causal_mechanism_sentence": sentence,
        "28_ENTRY_thesis_plausible": thesis,
        "29_ENTRY_built": False,
        "30_EXIT_built": False,
        "31_existing_Day2_primary_changed": False,
        "32_Runtime_changed": False,
        "33_Paper_changed": False,
        "34_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "35_CASE": case,
        "36_VERDICT": verdict,
        "37_NEXT": nxt,
    }
    decision = {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "ENTRY_built": False,
        "EXIT_built": False,
        "Day2_primary_changed": False,
        "Day2_substitutions_allowed": False,
        "usable_ENTRY_thesis": thesis,
        "REBOUND_ALREADY_PRICED_BEFORE_CONFIRMATION": flags["rebound_already_priced"],
        "REVERSAL_NOT_SUFFICIENT": flags["ten30_triggered_negative"],
        "submit_cancel_live": answers["34_submit_cancel_live"],
        "day2_primary": dict(DAY2_PRIMARY),
        "day2_primary_id": DAY2_PRIMARY_ID,
    }
    slim_events = []
    for e in events:
        slim_events.append(
            {
                "episode_id": e["episode_id"],
                "arm_hm": e["arm_hm"],
                "select_hm": e["select_hm"],
                "trigger_hm": e["trigger_hm"],
                "latency_sec": e["latency_sec"],
                "confirmation_received_at_le_entry": e.get("confirmation_received_at_le_entry"),
                "select_before_trigger": e.get("select_before_trigger"),
                "TOP_MID_1m": _long(e["TOP"], "1m", "MID_mean"),
                "TOP_MID_3m": _long(e["TOP"], "3m", "MID_mean"),
                "TOP_MID_5m": _long(e["TOP"], "5m", "MID_mean"),
                "TOP_MID_10m": _long(e["TOP"], "10m", "MID_mean"),
                "TOP_LONG_1m": _long(e["TOP"], "1m", "LONG_mean"),
                "TOP_LONG_3m": _long(e["TOP"], "3m", "LONG_mean"),
                "TOP_LONG_5m": _long(e["TOP"], "5m", "LONG_mean"),
                "TOP_LONG_10m": _long(e["TOP"], "10m", "LONG_mean"),
                "TOP_LONG_10m_median": _long(e["TOP"], "10m", "LONG_median"),
                "MIDDLE_LONG_10m": _long(e["MIDDLE"], "10m", "LONG_mean"),
                "BOTTOM_LONG_10m": _long(e["BOTTOM"], "10m", "LONG_mean"),
                "ALL48_LONG_10m": _long(e["ALL48"], "10m", "LONG_mean"),
                "TOP_BOTTOM_LONG_10m": e.get("TOP_BOTTOM_LONG_10m"),
                "entry_ask_mean": (e.get("TOP") or {}).get("entry_ask_mean"),
                "top16": e.get("top16"),
            }
        )
    return {
        "analysis_id": ANALYSIS_ID,
        "parent_id": PARENT_ID,
        "kind": KIND,
        "trading_date": day,
        "built_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "future_leakage_n": machine["future_leakage_n"],
        "episode_n": machine["episode_n"],
        "armed_n": machine["armed_n"],
        "expired_n": machine["expired_n"],
        "trigger_n": machine["trigger_n"],
        "events": slim_events,
        "episodes": [
            {
                "episode_id": e["episode_id"],
                "start": e.get("start"),
                "end": e.get("end"),
                "n_minutes": e.get("n_minutes"),
                "status": e.get("status"),
                "arm_hm": e.get("arm_hm"),
                "expire_hm": e.get("expire_hm"),
                "latency_sec": e.get("latency_sec"),
                "top16": e.get("top16"),
            }
            for e in machine["episodes"]
        ],
        "original_five": traces,
        "horizon_stats": {
            "1m": top1,
            "3m": top3,
            "5m": top5,
            "10m": top10,
            "bottom10": bot10,
            "all10": all10,
            "top_mid10": top_mid10,
            "bot_mid10": bot_mid10,
            "all_mid10": all_mid10,
            "pooled_top_long_10m": pooled10,
        },
        "one_trigger_per_episode": one_trig,
        "confirmation_received_at_le_entry": conf_le,
        "parent_consistency_audit": _parent_consistency(),
        "answers": answers,
        "decision": decision,
        "selection_causal": answers["8_TOP16_selection_time_causal"],
    }
