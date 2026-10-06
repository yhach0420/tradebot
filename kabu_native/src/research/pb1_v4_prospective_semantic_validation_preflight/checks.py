"""Run the 16 preflight checks. Synthetic / development-exposed only. No 20260924 open."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.inventory import file_inventory, source_inventory_sha
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.ledger import build_ledger
from research.pb1_v4_prospective_semantic_validation_preflight import (
    CALENDAR_CORRECTED_PRECOMMIT_SHA256,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    FIRST_ELIGIBLE_JP_CASH_SESSION,
    NON_CASH_DATES,
)
from research.pb1_v4_prospective_semantic_validation_preflight.completeness import (
    check_1m_complete,
    check_5m_complete,
    check_atr20,
    quality_gate,
    synthetic_1m,
    synthetic_prior_dailies,
)
from research.pb1_v4_prospective_semantic_validation_preflight.eligibility import resolve_jp_cash_session
from research.pb1_v4_prospective_semantic_validation_preflight.erratum import eligibility_unchanged
from research.pb1_v4_prospective_semantic_validation_preflight.guards import hidden_1m_parallel, same_bar_guard, thesis_lost_absorbing
from research.pb1_v4_prospective_semantic_validation_preflight.isolation import CAL_OUT, V4_OUT
from research.pb1_v4_prospective_semantic_validation_preflight.logging import CANDIDATE_DAY_FIELDS, CAUSAL_FIELDS, candidate_day_log
from research.pb1_v4_prospective_semantic_validation_preflight.scans import scan_v4_and_harness
from research.pb1_v4_prospective_semantic_validation_preflight.startgate import start_day_gate
from research.pb1_v4_clarified_machine_correction_v4.machine import mint_active, mint_seed, mint_why, new_side


def _row(cid: int, name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"id": cid, "name": name, "pass": bool(ok), "detail": detail}


def run_checks(*, corrected_precommit: dict[str, Any], parent_precommit: dict[str, Any], harness_src) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    live_machine = machine_sha256()
    rows.append(
        _row(
            1,
            "frozen machine hash verification",
            live_machine == EXPECTED_MACHINE_SHA256,
            {"live": live_machine, "expected": EXPECTED_MACHINE_SHA256},
        )
    )
    inv = source_inventory_sha(file_inventory())
    rows.append(
        _row(
            2,
            "frozen source inventory verification",
            inv == EXPECTED_SOURCE_INVENTORY_SHA256,
            {"live": inv, "expected": EXPECTED_SOURCE_INVENTORY_SHA256, "v4_source": v4_source_sha256()},
        )
    )
    pre_sha = str(corrected_precommit.get("PRECOMMIT_SHA256") or "")
    parent_sha = str(parent_precommit.get("PRECOMMIT_SHA256") or "")
    pre_ok = bool(pre_sha) and pre_sha != parent_sha and pre_sha != CALENDAR_CORRECTED_PRECOMMIT_SHA256
    rows.append(
        _row(
            3,
            "current precommit hash verification",
            pre_ok,
            {
                "new": pre_sha,
                "parent_calendar_corrected": parent_sha,
                "expected_parent": CALENDAR_CORRECTED_PRECOMMIT_SHA256,
            },
        )
    )
    ledger = build_ledger()
    rows.append(
        _row(
            4,
            "contamination ledger load",
            bool(ledger.get("ok")) and int(ledger.get("CONTAMINATED_SYMBOL_DATE_N") or 0) >= 352,
            {
                "ok": ledger.get("ok"),
                "n": ledger.get("CONTAMINATED_SYMBOL_DATE_N"),
                "can_be_prospective_n": ledger.get("can_be_prospective_n"),
            },
        )
    )
    keys = {(str(r.get("symbol")), str(r.get("date"))) for r in list(ledger.get("rows") or [])}
    r21 = resolve_jp_cash_session("20260921")
    r22 = resolve_jp_cash_session("20260922")
    r23 = resolve_jp_cash_session("20260923")
    r24 = resolve_jp_cash_session("20260924", completeness=None, allow_open_prospective=False)
    r_plus = resolve_jp_cash_session("20260921")
    r_cont = resolve_jp_cash_session("20241004", symbol="3382", ledger_keys=keys, completeness={"ok": True}, allow_open_prospective=False)
    elig_ok = (
        r21["counts_as_session"] is False
        and r22["counts_as_session"] is False
        and r23["counts_as_session"] is False
        and r21["is_tse_cash_session"] is False
        and r22["is_tse_cash_session"] is False
        and r23["is_tse_cash_session"] is False
        and r24["is_tse_cash_session"] is True
        and r24["eligible"] is False
        and "PROSPECTIVE_DATA_NOT_OPENED_THIS_TASK" in r24["reasons"]
        and r_plus["calendar_plus_one_rejected"] is True
        and r_cont["in_contamination_ledger"] is True
        and str((corrected_precommit.get("data_eligibility") or {}).get("first_eligible_session")) == FIRST_ELIGIBLE_JP_CASH_SESSION
    )
    rows.append(
        _row(
            5,
            "JP cash session eligibility resolver",
            elig_ok,
            {
                "20260921": r21,
                "20260922": r22,
                "20260923": r23,
                "20260924_this_task": r24,
                "FIRST_ELIGIBLE_JP_CASH_SESSION": FIRST_ELIGIBLE_JP_CASH_SESSION,
                "NON_CASH_DATES": list(NON_CASH_DATES),
            },
        )
    )
    rec_ok = synthetic_1m()
    rec_bad = synthetic_1m(drop=("09:03", "11:19"))
    c1 = check_1m_complete(rec_ok)
    c1b = check_1m_complete(rec_bad)
    rows.append(_row(6, "native 1m completeness check", bool(c1.get("ok")) and not c1b.get("ok"), {"complete": c1, "incomplete_rejected": c1b}))
    c5 = check_5m_complete(rec_ok)
    c5b = check_5m_complete(rec_bad)
    rows.append(_row(7, "native 5m completeness check", bool(c5.get("ok")) and not c5b.get("ok"), {"complete": c5, "incomplete_rejected": c5b}))
    atr = check_atr20(synthetic_prior_dailies(20))
    rows.append(_row(8, "ATR20 availability check", bool(atr.get("ok")), atr))
    q = quality_gate(rec_ok, atr=atr)
    rows.append(_row(9, "BAR_START timestamp convention", bool(q["bar_start"].get("ok")), q["bar_start"]))
    st = new_side(-1)
    mint_why(st, symbol="SYN", date="19990101")
    mint_seed(st, seed="FAILED_OPEN_SEED")
    mint_active(st)
    clog = candidate_day_log(st, symbol="SYN", date="19990101", opening_state="FAILED_OPEN_THEN_REAL_DRIVE")
    rows.append(
        _row(
            10,
            "candidate-day logging",
            bool(clog.get("_complete_schema")) and bool(clog.get("candidate_day_id")),
            {"fields": list(CANDIDATE_DAY_FIELDS), "missing": clog.get("_missing_fields")},
        )
    )
    hid = hidden_1m_parallel()
    causal_ok = all(k in (hid.get("causal_fields_attachable") or []) for k in CAUSAL_FIELDS)
    rows.append(_row(11, "causal timestamp logging", causal_ok, {"fields": list(CAUSAL_FIELDS), "attachable": hid.get("causal_fields_attachable")}))
    v4_rep = json.loads((V4_OUT / "report.json").read_text(encoding="utf-8")) if (V4_OUT / "report.json").is_file() else {}
    stored_hidden = bool(v4_rep.get("HIDDEN_1M_THESIS_PARITY"))
    rows.append(
        _row(
            12,
            "Hidden-1m parallel path",
            bool(hid.get("ok")) and stored_hidden,
            {"synthetic": hid, "stored_v4_HIDDEN_1M_THESIS_PARITY": stored_hidden},
        )
    )
    sb = same_bar_guard()
    stored_sb = int(v4_rep.get("same_bar_entry_n") or 0) == 0
    rows.append(_row(13, "SAME_BAR guard", bool(sb.get("ok")) and stored_sb, {"synthetic": sb, "stored_v4_same_bar_entry_n": v4_rep.get("same_bar_entry_n")}))
    absb = thesis_lost_absorbing()
    rows.append(_row(14, "THESIS_LOST absorbing guard", bool(absb.get("ok")), absb))
    scans = scan_v4_and_harness(harness_src)
    rows.append(_row(15, "no PnL/MFE/MAE path", bool(scans.get("pnl_ok")), {"hits": scans.get("pnl_hits")}))
    rows.append(_row(16, "no real-order path", bool(scans.get("order_ok")), {"hits": scans.get("order_hits")}))
    gate = start_day_gate(precommit_sha=pre_sha, expected_precommit_sha=pre_sha)
    elig_par = eligibility_unchanged(parent_precommit, corrected_precommit)
    all_pass = all(r.get("pass") for r in rows) and bool(gate.get("ok")) and bool(elig_par.get("ok"))
    cal_rep = json.loads((CAL_OUT / "report.json").read_text(encoding="utf-8")) if (CAL_OUT / "report.json").is_file() else {}
    return {
        "ok": all_pass,
        "pass_n": sum(1 for r in rows if r.get("pass")),
        "fail_n": sum(1 for r in rows if not r.get("pass")),
        "rows": rows,
        "start_day_gate": gate,
        "eligibility_unchanged": elig_par,
        "ledger_n": ledger.get("CONTAMINATED_SYMBOL_DATE_N"),
        "quality_gate_synthetic": q,
        "parent_calendar_verdict": (cal_rep.get("decision") or {}).get("VERDICT"),
        "PROSPECTIVE_DATA_OPENED": False,
        "20260924_bars_loaded": False,
    }
