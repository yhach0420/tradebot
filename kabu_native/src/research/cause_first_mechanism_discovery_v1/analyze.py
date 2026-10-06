"""Mechanism-discovery decision. No complete-strategy freeze. No Kabu 50. Frozen Validation closed."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1 import (
    ADDITIONAL_RESEARCH_DATA_ALLOWED,
    ANALYSIS_ID,
    CASE_BIND,
    CASE_NONE,
    CASE_READY,
    CASE_SPLIT,
    COMPLETE_STRATEGY_FROZEN,
    ENTRY_ONLY_SUCCESS_ALLOWED,
    EXTERNAL_ALLOWED,
    FROZEN_VALIDATION_ACCESSED,
    HISTORICAL_BID_ASK_INFERRED,
    KABU_50_APPLIED,
    KABU_REGISTRATION_CHANGED,
    LIVE_20260914_CHANGED,
    NEXT_BIND,
    NEXT_ENTRY,
    NEXT_REASSESS,
    PAPER_CHANGED,
    PNL_ALLOWED_IN_RESEARCH,
    PROGRAM_ID,
    RUNTIME_CHANGED,
    SAME_BAR_CLOSE_ENTRY,
    STRATEGY_SEARCH_STARTED,
    UNIVERSE_105_MANDATORY_FINAL,
)
from research.cause_first_mechanism_discovery_v1.bind import bind_foundation_v2
from research.cause_first_mechanism_discovery_v1.features import deployability_summary, feature_catalog
from research.cause_first_mechanism_discovery_v1.mechanisms import decide_hypotheses
from research.cause_first_mechanism_discovery_v1.panel import build_events, collect_session_days, load_minutes
from research.cause_first_mechanism_discovery_v1.split import partition_set, split_session_days
from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED


def decide(*, bind_ok: bool, split_ok: bool, surviving_n: int) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Foundation V2 artifacts did not bind. Mechanism discovery did not start.",
        }
    if not split_ok:
        return {
            "CASE": "SPLIT",
            "VERDICT": CASE_SPLIT,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Chronological split could not be frozen. Outcomes were not examined.",
        }
    if int(surviving_n) <= 0:
        return {
            "CASE": "NONE",
            "VERDICT": CASE_NONE,
            "NEXT": NEXT_REASSESS,
            "INTERPRETATION": "No precommitted mechanism survived Discovery sign, day-stability, and Confirmation sign-check. Frozen Validation remains closed. This is not a complete-strategy ranking.",
        }
    return {
        "CASE": "READY",
        "VERDICT": CASE_READY,
        "NEXT": NEXT_ENTRY,
        "INTERPRETATION": "Cause-first mechanisms survived Discovery and a one-shot Confirmation sign-check. Next is ENTRY research with aligned EXIT, still without Kabu 50 compression. Frozen Validation remains closed. Complete strategy is not frozen. Paper-ready is false.",
    }


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or "")
    return out


def _market_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    keys = (
        "date",
        "clock",
        "active_n",
        "up_1m",
        "up_3m",
        "up_5m",
        "median_ret_5m",
        "dispersion_5m",
        "volume_active_ratio",
        "va_active_ratio",
        "vwap_above_ratio",
        "ema_breadth",
        "leadership_hhi",
        "leadership_top10_share",
        "market_strong",
        "market_weak",
        "market_neutral",
    )
    for e in events:
        if e.get("clock") != "09:30":
            continue
        seen[str(e["date"])] = {k: e.get(k) for k in keys}
    return [seen[k] for k in sorted(seen)]


def _sector_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for e in events:
        if e.get("clock") != "09:30":
            continue
        key = (str(e["date"]), str(e.get("sector") or ""))
        rec = acc.setdefault(key, {"date": key[0], "sector": key[1], "n": 0, "sector_up_5m": e.get("sector_up_5m"), "up_5m_market": e.get("up_5m")})
        rec["n"] += 1
    return [acc[k] for k in sorted(acc)]


def _stock_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import numpy as np

    xs = [e for e in events if e.get("clock") == "09:30"]
    if not xs:
        return [{"empty": True}]

    def q(field: str) -> dict[str, Any]:
        arr = np.asarray([float(e[field]) for e in xs if e.get(field) is not None and e.get(field) == e.get(field)], dtype=float)
        if arr.size == 0:
            return {"field": field, "n": 0}
        return {
            "field": field,
            "n": int(arr.size),
            "p20": float(np.percentile(arr, 20)),
            "median": float(np.median(arr)),
            "p80": float(np.percentile(arr, 80)),
        }

    return [q("ret_5m"), q("rs_5m"), q("vol_rel20"), q("x0_h15_bps")]


def _next_experiments(surviving: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {"experiment": "CAUSE_FIRST_CONTEXT_PLUS_TECHNICAL_ENTRY_V1", "why": "Build ENTRY from surviving layers with causal availability time.", "uses_frozen_validation": False, "kabu_50": False},
        {"experiment": "ENTRY_ALIGNED_TECHNICAL_EXIT_V1", "why": "Each surviving ENTRY thesis needs a matching invalidation EXIT. V27 EMA persistence is prior evidence only.", "uses_frozen_validation": False, "kabu_50": False},
        {"experiment": "COMPLETE_CAUSAL_STRATEGY_REPLAY_V1", "why": "Only after ENTRY+EXIT+CAP+same-symbol+occupancy+reentry+session-close are specified.", "uses_frozen_validation": False, "kabu_50": False},
        {"experiment": "PAPER_DEPLOYABILITY_COMPRESSION_V1", "why": "Only after a frozen strategy's actual context dependencies are known. Not a generic 105->48 map.", "uses_frozen_validation": False, "kabu_50": True, "now": False},
    ]
    for s in surviving:
        rows.append({"experiment": f"ENTRY_FROM_{s.get('id')}", "why": s.get("layer"), "status": "queued_after_this_run", "uses_frozen_validation": False})
    return rows


def build_report_body() -> dict[str, Any]:
    catalog = feature_catalog()
    bind = bind_foundation_v2()
    print(f"BIND ok={bind.get('ok')} n={bind.get('union_n')} semantics={bind.get('semantics')}", flush=True)
    availability = {"ok": False, "session_days": []}
    split = {"ok": False, "dates_assigned": False}
    mech = {
        "hypotheses": [],
        "surviving": [],
        "rejected": [],
        "surviving_n": 0,
        "rejected_n": 0,
        "frozen_validation_accessed": False,
    }
    disc_events: list[dict[str, Any]] = []
    conf_events: list[dict[str, Any]] = []
    if bind.get("ok"):
        availability = collect_session_days(symbols=list(bind.get("symbols") or []))
        print(f"SESSIONS {availability.get('n_sessions')}", flush=True)
        split = split_session_days(list(availability.get("session_days") or []))
        print(f"SPLIT_FROZEN sha={split.get('split_sha256')} disc={((split.get('discovery') or {}).get('n'))} conf={((split.get('confirmation') or {}).get('n'))} val={((split.get('frozen_validation') or {}).get('n'))}", flush=True)
        if split.get("ok"):
            val = partition_set(split, "frozen_validation")
            disc_dates = partition_set(split, "discovery")
            conf_dates = partition_set(split, "confirmation")
            allowed = disc_dates | conf_dates
            print(f"LOAD_MINUTES allowed={len(allowed)} forbidden_val={len(val)}", flush=True)
            minutes = load_minutes(symbols=list(bind.get("symbols") or []), allowed_dates=allowed, forbidden_dates=val)
            print(f"MINUTES_ROWS {len(minutes)}", flush=True)
            events = build_events(minutes=minutes, sector_of=_sector_of(bind), forbidden_dates=val)
            disc_events = [e for e in events if str(e.get("date")) in disc_dates]
            conf_events = [e for e in events if str(e.get("date")) in conf_dates]
            mech = decide_hypotheses(discovery_events=disc_events, confirmation_events=conf_events, validation_dates=val)
            print(f"MECH surviving={mech.get('surviving_n')} rejected={mech.get('rejected_n')}", flush=True)
    decision = decide(bind_ok=bool(bind.get("ok")), split_ok=bool(split.get("ok")), surviving_n=int(mech.get("surviving_n") or 0))
    return {
        "program_id": PROGRAM_ID,
        "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
        "availability": {k: v for k, v in availability.items() if k not in {"session_days", "active_n_by_day"}},
        "availability_active_n_by_day": dict(availability.get("active_n_by_day") or {}),
        "split": {
            **{k: v for k, v in split.items() if k not in {"discovery", "confirmation", "frozen_validation"}},
            "discovery": {k: v for k, v in dict(split.get("discovery") or {}).items() if k != "dates"},
            "confirmation": {k: v for k, v in dict(split.get("confirmation") or {}).items() if k != "dates"},
            "frozen_validation": {k: v for k, v in dict(split.get("frozen_validation") or {}).items() if k != "dates"},
            "discovery_dates": list(dict(split.get("discovery") or {}).get("dates") or []),
            "confirmation_dates": list(dict(split.get("confirmation") or {}).get("dates") or []),
            "frozen_validation_dates": list(dict(split.get("frozen_validation") or {}).get("dates") or []),
        },
        "feature_catalog": catalog,
        "deployability": deployability_summary(catalog),
        "mechanisms": mech,
        "market_state_discovery_0930": _market_rows(disc_events),
        "sector_state_discovery_0930": _sector_rows(disc_events),
        "stock_state_summary_discovery_0930": _stock_summary(disc_events),
        "next_experiments": _next_experiments(list(mech.get("surviving") or [])),
        "event_n_discovery": len(disc_events),
        "event_n_confirmation": len(conf_events),
        "event_n_frozen_validation": 0,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    split = dict(report.get("split") or {})
    disc = dict(split.get("discovery") or {})
    conf = dict(split.get("confirmation") or {})
    val = dict(split.get("frozen_validation") or {})
    mech = dict(report.get("mechanisms") or {})
    d = dict(report.get("decision") or {})
    bind = dict(report.get("bind") or {})
    return {
        "exact_discovery_dates": {"first": disc.get("first"), "last": disc.get("last"), "n": disc.get("n"), "dates": split.get("discovery_dates")},
        "exact_confirmation_dates": {"first": conf.get("first"), "last": conf.get("last"), "n": conf.get("n"), "dates": split.get("confirmation_dates")},
        "exact_frozen_validation_dates": {"first": val.get("first"), "last": val.get("last"), "n": val.get("n"), "dates": split.get("frozen_validation_dates")},
        "split_sha": split.get("split_sha256"),
        "frozen_validation_accessed_during_discovery": bool(FROZEN_VALIDATION_ACCESSED) or bool(mech.get("frozen_validation_accessed")),
        "pool_105_used_as_mandatory_final_universe": bool(UNIVERSE_105_MANDATORY_FINAL),
        "kabu_50_slot_constraint_applied_to_research": bool(KABU_50_APPLIED),
        "additional_research_data_allowed": bool(ADDITIONAL_RESEARCH_DATA_ALLOWED),
        "external_historical_context_allowed": bool(EXTERNAL_ALLOWED),
        "pnl_allowed_in_strategy_research": bool(PNL_ALLOWED_IN_RESEARCH),
        "entry_only_success_allowed": bool(ENTRY_ONLY_SUCCESS_ALLOWED),
        "historical_bid_ask_inferred": bool(HISTORICAL_BID_ASK_INFERRED),
        "same_bar_leakage": bool(SAME_BAR_CLOSE_ENTRY),
        "panel_conditioned_label_retained": True,
        "strategy_search_started_in_this_run": STRATEGY_SEARCH_STARTED,
        "runtime_modified": bool(RUNTIME_CHANGED),
        "paper_modified": bool(PAPER_CHANGED),
        "kabu_registration_modified": bool(KABU_REGISTRATION_CHANGED),
        "live_20260914_modified": bool(LIVE_20260914_CHANGED),
        "submit_cancel_live": "0/0/0",
        "FEATURE_MINING_CLOSED": bool(FEATURE_MINING_CLOSED),
        "complete_strategy_frozen": bool(COMPLETE_STRATEGY_FROZEN),
        "surviving_n": mech.get("surviving_n"),
        "rejected_n": mech.get("rejected_n"),
        "bind_ok": bind.get("ok"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "ANALYSIS_ID": ANALYSIS_ID,
    }
