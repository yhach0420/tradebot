"""Start PB1 V4 prospective semantic validation. Runtime 0/0/0. Frozen V4."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import TODAY, advanced
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.pb1_v4_clarified_machine_correction_v4.bind import bind_prior
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.ledger import build_ledger
from research.pb1_v4_prospective_semantic_validation import (
    ANALYSIS_ID,
    CASE_BIND,
    CASE_HASH,
    CASE_INELIGIBLE,
    CASE_NOT_YET,
    CASE_STARTED,
    CASE_WAITING,
    EXPECTED_PRECOMMIT_SHA256,
    NEXT_CONTINUE,
    NEXT_RETRY_AFTER_AM,
    PROGRAM_ID,
    SESSION0,
    SESSION_STOP,
    WHY_STOP,
)
from research.pb1_v4_prospective_semantic_validation.collect import collect_session
from research.pb1_v4_prospective_semantic_validation.isolation import (
    CACHE,
    CLARIFIED_SPEC_SRC,
    OUT,
    PREFLIGHT_OUT,
    V4_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_prospective_semantic_validation.logs import (
    append_jsonl,
    load_batch_state,
    save_batch_state,
)
from research.pb1_v4_prospective_semantic_validation.publish import SHEET_ORDER, build_answers, build_sheets, write_artifacts
from research.pb1_v4_prospective_semantic_validation.session import session_gate
from research.pb1_v4_prospective_semantic_validation.spec import source_sha256
from research.pb1_v4_prospective_semantic_validation.states import (
    STATE_ELIGIBLE,
    STATE_NOT_YET,
    STATE_WAITING,
    apply_quality,
    classify_clock,
)
from research.pb1_v4_prospective_semantic_validation.walk_session import walk_prospective
from research.pb1_v4_prospective_semantic_validation_preflight.startgate import start_day_gate
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint(root: Path, *, py_only: bool = False) -> str:
    h = hashlib.sha256()
    if not root.exists():
        return ""
    files = sorted(p for p in (root.glob("*.py") if py_only else root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts)
    if py_only:
        files = sorted(p for p in root.glob("*.py") if p.is_file())
    for p in files:
        h.update(str(p.relative_to(root)).encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _load_precommit() -> dict:
    path = PREFLIGHT_OUT / "report.json"
    if not path.is_file():
        return {}
    rep = json.loads(path.read_text(encoding="utf-8"))
    return dict(rep.get("corrected_precommit") or {})


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_PROSPECTIVE_SEMANTIC_VALIDATION RESEARCH_ONLY", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    print(f"TODAY_JST {TODAY} SESSION0 {SESSION0}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = {"v4_src": _fingerprint(V4_SRC, py_only=True), "spec": _fingerprint(CLARIFIED_SPEC_SRC, py_only=True)}
    pre = _load_precommit()
    live_pre = str(pre.get("PRECOMMIT_SHA256") or "")
    gate = start_day_gate(precommit_sha=live_pre, expected_precommit_sha=EXPECTED_PRECOMMIT_SHA256)
    append_jsonl(CACHE, "hash_verification_log", {"gate": gate, "today": TODAY, "session": SESSION0})
    bind = bind_prior()
    symbols = list(bind.get("symbols") or [])
    ledger = build_ledger()
    ledger_keys = {(str(r.get("symbol")), str(r.get("date"))) for r in list(ledger.get("rows") or [])}
    batch = load_batch_state(CACHE)
    safety = {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "SPEC_CHANGED": False,
        "V4_CHANGED": False,
        "THRESHOLD_RETUNED": False,
        "PNL_USED": False,
        "MFE_MAE_USED": False,
        "FUTURE_OUTCOME_USED": False,
        "submit/cancel/live": "0/0/0",
        "OLD_CONFIRMATION_OPENED": False,
        "FROZEN_VALIDATION_OPENED": False,
        "PRECOMMIT_CHANGED": False,
        "PROSPECTIVE_DATA_OPENED": False,
    }
    walked = None
    collect: dict = {"session": SESSION0, "native_1m_ready": False, "reason": "not_searched"}
    now = datetime.now(JST)
    clock = classify_clock(session=SESSION0, today_jst=TODAY, now=now)
    op = apply_quality(clock=clock, data_complete=False)
    sess_gate: dict = {"data_complete": False, "quality": {}, "calendar": {}}
    if not gate.get("ok") or not bind.get("ok"):
        decision = {
            "VERDICT": CASE_HASH if not gate.get("ok") else CASE_BIND,
            "NEXT": CASE_HASH if not gate.get("ok") else "STOP",
            "reasons": ["hash_or_bind_failed"],
        }
    elif clock.get("SESSION_STATE") == STATE_NOT_YET:
        decision = {
            "VERDICT": CASE_NOT_YET,
            "NEXT": NEXT_RETRY_AFTER_AM,
            "reasons": ["session_date_after_today_jst", f"today={TODAY}", f"target={SESSION0}"],
        }
    elif clock.get("SESSION_STATE") == STATE_WAITING:
        decision = {
            "VERDICT": CASE_WAITING,
            "NEXT": NEXT_RETRY_AFTER_AM,
            "reasons": ["am_window_not_complete"],
        }
    else:
        collect = collect_session(SESSION0, symbols=symbols)
        minutes = collect.get("minutes")
        sess_gate = session_gate(
            session=SESSION0,
            symbols=symbols,
            ledger_keys=ledger_keys,
            minutes=minutes,
            allow_open=True,
        )
        op = apply_quality(clock=clock, data_complete=bool(sess_gate.get("data_complete")))
        append_jsonl(
            CACHE,
            "quality_gate_log",
            {
                "session": SESSION0,
                "SESSION_STATE": op.get("SESSION_STATE"),
                "native_1m_ready": collect.get("native_1m_ready"),
                "reason": collect.get("reason"),
                "locations": collect.get("locations"),
                "quality": sess_gate.get("quality"),
            },
        )
        if op.get("SESSION_STATE") == STATE_ELIGIBLE:
            walked = walk_prospective(
                bind=bind,
                minutes=minutes,
                session=SESSION0,
                lookback_dates=[],
                ledger_keys=ledger_keys,
            )
            why_n = int(walked.get("candidate_day_n") or 0)
            batch["eligible_session_n"] = int(batch.get("eligible_session_n") or 0) + 1
            batch["cumulative_candidate_day_n"] = int(batch.get("cumulative_candidate_day_n") or 0) + why_n
            batch.setdefault("sessions", []).append(SESSION0)
            decision = {"VERDICT": CASE_STARTED, "NEXT": NEXT_CONTINUE, "reasons": []}
            for row in list(walked.get("funnel_days") or []):
                if row.get("WHY_THIS_STOCK"):
                    append_jsonl(CACHE, "prospective_state_log", {"role": "candidate_day", **{k: row.get(k) for k in (
                        "symbol", "date", "WHY_THIS_STOCK", "OPENING_DRIVE_SEED", "opening_state",
                        "POST_DOMINANT_PATH_STATE", "OPENING_DRIVE_REACHED", "OPENING_DRIVE_LIVE",
                        "LOCATION_IDENTIFIED", "THESIS_REACHED", "THESIS_LIVE", "THESIS_LOST",
                        "E0", "E1", "execution_id", "information_available_at", "event_completed_at",
                        "state_changed_at", "entry_allowed_at",
                    )}})
        else:
            decision = {
                "VERDICT": CASE_INELIGIBLE,
                "NEXT": NEXT_RETRY_AFTER_AM,
                "reasons": ["completeness_gate_fail_after_am_window", str(collect.get("reason") or "")],
            }
        if int(batch.get("cumulative_candidate_day_n") or 0) >= WHY_STOP or int(batch.get("eligible_session_n") or 0) >= SESSION_STOP:
            if int(batch.get("cumulative_candidate_day_n") or 0) < WHY_STOP:
                batch["UNDERPOWERED_NOT_A_PASS"] = True
                batch["closed"] = True
            else:
                batch["closed"] = True
                decision["NEXT"] = "OPEN_SEMANTIC_ADJUDICATION_AFTER_BATCH_CLOSE"
    save_batch_state(CACHE, batch)
    session_result = {
        "SESSION_STATE": op.get("SESSION_STATE"),
        "SESSION_ACCEPTED_FOR_PROSPECTIVE": bool(op.get("SESSION_ACCEPTED_FOR_PROSPECTIVE")),
        "INELIGIBLE_SESSION": bool(op.get("INELIGIBLE_SESSION")),
        "counts_as_session": bool(op.get("counts_as_session")),
        "semantic_fail": False,
        "PROSPECTIVE_DATA_OPENED": bool(op.get("PROSPECTIVE_DATA_OPENED")),
        "session_date": SESSION0,
        "today_jst": TODAY,
        "machine_sha_verified": bool(gate.get("machine_sha_ok")),
        "source_inventory_sha_verified": bool(gate.get("source_inventory_sha_ok")),
        "precommit_sha_verified": bool(gate.get("precommit_sha_ok")),
        "data_complete": bool(op.get("data_complete")),
        "candidate_day_n": 0 if walked is None else int(walked.get("candidate_day_n") or 0),
        "cumulative_candidate_day_n": int(batch.get("cumulative_candidate_day_n") or 0),
        "eligible_session_n": int(batch.get("eligible_session_n") or 0),
        "hidden1m_mismatch_n": 0 if walked is None else int(walked.get("hidden1m_mismatch_n") or 0),
        "invariant_violation_n": 0 if walked is None else int(walked.get("invariant_violation_n") or 0),
        "same_bar_entry_n": 0 if walked is None else int(walked.get("same_bar_entry_n") or 0),
        "ledger_n": ledger.get("CONTAMINATED_SYMBOL_DATE_N"),
        "pool_n": len(symbols),
        "clock_reason": clock.get("reason"),
        "bars_searched": bool(clock.get("may_search_bars")),
    }
    safety["PROSPECTIVE_DATA_OPENED"] = bool(session_result.get("PROSPECTIVE_DATA_OPENED"))
    append_jsonl(CACHE, "session_manifest", session_result)
    fps_after = {"v4_src": _fingerprint(V4_SRC, py_only=True), "spec": _fingerprint(CLARIFIED_SPEC_SRC, py_only=True)}
    if fps != fps_after:
        raise RuntimeError("PRIOR_MUTATED")
    after = snapshot(phase="POST")
    quality = {
        "native_1m_ready": collect.get("native_1m_ready"),
        "reason": collect.get("reason"),
        "locations": collect.get("locations"),
        "parquet": collect.get("parquet"),
        "session_gate": {k: v for k, v in sess_gate.items() if k != "calendar"} | {
            "calendar_is_tse_cash": (sess_gate.get("calendar") or {}).get("is_tse_cash_session"),
            "calendar_label": (sess_gate.get("calendar") or {}).get("calendar_label"),
        },
    }
    stopping = {
        "close_when": f"WHY_THIS_STOCK candidate-days >= {WHY_STOP} OR {SESSION_STOP} actual eligible JP cash sessions, whichever first",
        "eligible_session_n": session_result["eligible_session_n"],
        "cumulative_candidate_day_n": session_result["cumulative_candidate_day_n"],
        "extend_after_seeing_results": False,
        "holidays_weekends_partials_count": False,
        "UNDERPOWERED_NOT_A_PASS": bool(batch.get("UNDERPOWERED_NOT_A_PASS")),
        "batch_closed": bool(batch.get("closed")),
        "adjudication_open": False,
    }
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "FROZEN_IDENTITY": "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_FROZEN_V1",
        "hashes": {
            "HARNESS_SOURCE_SHA256": source_sha256(),
            "SPEC_SHA256": spec_sha256(),
            "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_SHA256": v4_machine_sha256(),
            "PB1_V4_SOURCE_SHA256": v4_source_sha256(),
            "PRECOMMIT_SHA256": live_pre,
            "EXPECTED_PRECOMMIT_SHA256": EXPECTED_PRECOMMIT_SHA256,
        },
        "hash_gate": gate,
        "session_result": session_result,
        "quality": quality,
        "stopping": stopping,
        "state_log_rows": [],
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
        "contamination_ledger_n": ledger.get("CONTAMINATED_SYMBOL_DATE_N"),
        "walked_ok": None if walked is None else bool(walked.get("ok")),
    }
    report["answers"] = build_answers(report)
    CACHE.mkdir(parents=True, exist_ok=True)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    if tuple(sheets.keys()) != SHEET_ORDER:
        raise RuntimeError("SHEET_ORDER_MISMATCH")
    write_artifacts(report, sheets)
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print(f"NEXT {decision.get('NEXT')}", flush=True)
    ans = dict(report.get("answers") or {})
    for k in (
        "SESSION_STATE",
        "SESSION_ACCEPTED_FOR_PROSPECTIVE",
        "INELIGIBLE_SESSION",
        "session_date",
        "machine_sha_verified",
        "precommit_sha_verified",
        "data_complete",
        "candidate_day_n",
        "cumulative_candidate_day_n",
        "eligible_session_n",
        "hidden1m_mismatch_n",
        "invariant_violation_n",
        "V4_CHANGED",
        "SPEC_CHANGED",
        "THRESHOLD_RETUNED",
        "PRECOMMIT_CHANGED",
        "PNL_USED",
        "MFE_MAE_USED",
        "FUTURE_OUTCOME_USED",
        "PROSPECTIVE_DATA_OPENED",
        "submit/cancel/live",
    ):
        print(f"{k} = {ans.get(k)}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
