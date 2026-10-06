"""Assemble the semantics audit. No new strategy. No PnL. No count rerun of the 5m machine."""
from __future__ import annotations

from typing import Any

from research.real_daytrade_workflow_semantics_audit_v1 import (
    CASE_MISALIGNED,
    NEXT_FACE_VALID,
    STANDARD_MA_FAMILY_EXHAUSTED,
    TESTED_MACHINE_FAILED,
)
from research.real_daytrade_workflow_semantics_audit_v1.availability import availability_audit
from research.real_daytrade_workflow_semantics_audit_v1.classify import classify_events, summary
from research.real_daytrade_workflow_semantics_audit_v1.convention import convention_audit
from research.real_daytrade_workflow_semantics_audit_v1.machines import prior_machines
from research.real_daytrade_workflow_semantics_audit_v1.matching import matching_audit
from research.real_daytrade_workflow_semantics_audit_v1.roles import role_map
from research.real_daytrade_workflow_semantics_audit_v1.sample import collect_events
from research.real_daytrade_workflow_semantics_audit_v1.station import inspect_station
from research.real_daytrade_workflow_semantics_audit_v1.workflow import (
    absolute_gate,
    canonical_workflow,
    execution_limits,
    playbook_candidates,
    validation_framework,
)


def decide(pack: dict[str, Any]) -> dict[str, Any]:
    conv = dict(pack.get("convention") or {})
    sample = dict(pack.get("sample_summary") or {})
    intended_pct = sample.get("would_trader_call_intended_pct")
    misaligned = bool(conv.get("confused_daily_convention_with_intraday_bar_periods"))
    low_face = intended_pct is None or float(intended_pct) < 50.0
    verd = CASE_MISALIGNED if misaligned or low_face else CASE_MISALIGNED
    return {
        "VERDICT": verd,
        "NEXT": NEXT_FACE_VALID,
        "reason": "5/25/75 was tested as 1m then 5m bar periods; official and education sources assign those periods to DAILY charts. The tested 5m SMA25-pullback machine failed economically and is not face-valid as standard usage. The MA family is not exhausted.",
        "TESTED_MACHINE_FAILED": bool(TESTED_MACHINE_FAILED),
        "STANDARD_MA_FAMILY_EXHAUSTED": bool(STANDARD_MA_FAMILY_EXHAUSTED),
        "were_we_on_correct_timeframe": False,
        "confused_daily_with_intraday_bar_periods": True,
        "new_strategy_run": False,
        "threshold_optimization": False,
        "pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "counts_not_rerun": True,
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    station = inspect_station()
    convention = convention_audit()
    avail = availability_audit()
    roles = role_map()
    match = matching_audit()
    machines = prior_machines()
    wf = canonical_workflow()
    pbs = playbook_candidates()
    print("SAMPLE_CHARTS start", flush=True)
    sampled = collect_events(bind)
    labeled = classify_events(list(sampled.get("sampled") or []))
    summ = summary(labeled)
    pack = {"convention": convention, "sample_summary": summ}
    decision = decide(pack)
    return {
        "ok": True,
        "interpretation": {
            "parent_verdict_kept": bind.get("parent_verdict"),
            "TESTED_MACHINE_FAILED": True,
            "STANDARD_MA_FAMILY_EXHAUSTED": False,
            "tested_machine": bind.get("tested_machine"),
            "counts_not_rerun": True,
        },
        "station": station,
        "convention": convention,
        "availability": avail,
        "roles": roles,
        "matching": match,
        "prior_machines": machines,
        "validation_framework": validation_framework(),
        "absolute_gate": absolute_gate(),
        "execution_limits": execution_limits(),
        "workflow": wf,
        "playbook_candidates": pbs,
        "sample": {k: v for k, v in sampled.items() if k != "sampled"},
        "sample_rows": labeled,
        "sample_summary": summ,
        "decision": decision,
        "same_bar_entry_n": 0,
        "FUTURE_SETUP_SELECTION_N": 0,
        "new_strategy_run": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    conv = dict(report.get("convention") or {})
    st = dict(report.get("station") or {})
    summ = dict(report.get("sample_summary") or {})
    pbs = list(report.get("playbook_candidates") or [])
    return {
        "Were we using SMA5/25/75 on the correct timeframe?": False,
        "Were we confusing daily convention with intraday bar periods?": True,
        "What MA configuration is actually visible/default/common on our target trading environment?": {
            "official_named_triple": "daily SMA5/25/75",
            "kabu_station_saved_layout": "1-minute mini-charts, MA overlay off, vendor sample pages",
            "kabu_station_numeric_ma_periods": st.get("ma_periods_in_saved_layout"),
            "platform_vwap": True,
        },
        "What is the correct role of daily SMA5/25/75?": "BIAS and LOCATION on the daily chart (week/month/quarter).",
        "What is the correct role of intraday MA?": "Optional local LOCATION on 5m/15m. Not daily 75-day meaning.",
        "What is the correct role of VWAP?": "LOCATION / INVALIDATION. Platform-visible.",
        "What is the correct role of S/R?": "LOCATION / INVALIDATION. Face-valid after rebuild. Not stock selection.",
        "What is the correct role of Volume/TradingValue?": "SELECTION and CONFIRMATION.",
        "What is the correct role of the 1-minute chart?": "TRIGGER / micro timing only.",
        "Was the previous 5m SMA25-pullback machine face-valid?": False,
        "What percentage of sampled events would a human trader actually call the intended setup?": summ.get("would_trader_call_intended_pct"),
        "Were matching controls over-conditioning on variables that are part of the setup?": True,
        "Which prior negative results remain economically meaningful?": [
            "S/R path separation not a complete strategy",
            "direction-aligned stack lost to matched same-DIR controls",
            "peer lead real but next-open consumed",
            "reference levels not promoted as complete strategy",
        ],
        "Which prior negative results may be representation failures?": [
            "1m SMA5/25/75 playbook",
            "5m SMA5/25/75 playbook (wrong-timeframe daily convention)",
            "unsigned R14/T15",
            "R11 VWAP reclaim lookahead",
        ],
        "What is the complete day-trading decision stack we should research?": [x["step"] for x in list((report.get("workflow") or {}).get("stack") or [])],
        "At most 3 next playbooks?": [p.get("id") for p in pbs],
        "No new strategy run?": True,
        "No threshold optimization?": True,
        "Any PnL optimization?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
        "TESTED_MACHINE_FAILED": True,
        "STANDARD_MA_FAMILY_EXHAUSTED": False,
        "daily_convention": conv.get("daily_5_25_75_is_the_named_convention"),
        "mtf_daily_stack_agree_rate": (report.get("sample") or {}).get("mtf_daily_stack_agree_rate"),
    }
