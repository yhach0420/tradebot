#!/usr/bin/env python
"""V26-G6 targeted 48x RCA proofs. Not Formal freeze. Not Formal Paper.

Uses working-tree G6 selector. Never Candidate-5. Never WORKING_V26G4.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

from small_paper.ingress_run_identity import ENV_CERTIFICATION_RUN_ID, ENV_STAGE_RUN_ID, generate_launch_nonce
from small_paper.runtime_clock import (
    ENV_ARM_FILE,
    ENV_CERT_MODE,
    ENV_ENABLED,
    ENV_REPLAY_EPS,
    ENV_REPLAY_PATH,
    ENV_SPEED,
    ENV_STOP,
    ENV_T0,
    ENV_V0,
    bind_session_clock,
    official_cert_child_env,
)
from small_paper.operational_validation import OPVAL_ACTIVATION_ID
from small_paper.v1r_activation_binding import ENV_ACTIVATION_SELECTOR, OUT

JST = ZoneInfo("Asia/Tokyo")
G6_DIR = NATIVE / "results" / "research" / "v26g6_targeted_rca"
WORKING_SELECTOR = G6_DIR / "active_v1r_working_v26g6.json"
OPVAL_SELECTOR = G6_DIR / "active_v1r_opval_20260817.json"
C6_SELECTOR = OUT / "active_v1r_candidate_v26g6_6.json"
WORKING_ID = "V1R_EXIT_V2_PAPER_PRIMARY_WORKING_V26G6"
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
OPVAL_LABELS = {
    "paper_mode": "OPERATIONAL_VALIDATION_ONLY",
    "INVALID_FOR_STRATEGY_EVALUATION": True,
    "NOT_PROSPECTIVE_DAY1": True,
    "formal_paper_allowed": False,
    "prospective_allowed": False,
    "strategy_evaluation_allowed": False,
}
CERT_SCRIPT = NATIVE / "scripts" / "run_paper_full_day_certification.py"
DAY = "20260812"
ARM_FILE = NATIVE / "data" / "market_capture" / DAY / "session_clock_arm.json"


def _cert():
    spec = importlib.util.spec_from_file_location("full_day_cert", CERT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _lane_parity(session_dir: Path) -> dict:
    """Primary fill→strategy EXIT→slot counts. SESSION_CLOSE is not strategy parity."""
    fills = 0
    strategy_exits = 0
    session_close_exits = 0
    slot_release = 0
    pnl_yen_100 = 0
    shadow_pbv2 = 0
    entry_path = session_dir / "v1r_native_entry_trace.jsonl"
    if entry_path.is_file():
        try:
            for line in entry_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if str(row.get("kind") or "") == "V1R_FILL":
                    fills += 1
        except Exception:
            pass
    lane_path = session_dir / "v1r_dual_lane_trace.jsonl"
    if lane_path.is_file():
        try:
            for line in lane_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ev = str(row.get("event") or "")
                lane = str(row.get("lane") or "")
                reason = str(row.get("reason") or "")
                if lane == "primary" and ev == "EXIT_EXECUTED":
                    if reason == "SESSION_CLOSE":
                        session_close_exits += 1
                    else:
                        strategy_exits += 1
                        try:
                            fill_px = float(row.get("fill_price") or 0)
                            exit_px = float(row.get("exit_price") or 0)
                            pnl_yen_100 += int(round((exit_px - fill_px) * 100.0))
                        except (TypeError, ValueError):
                            pass
                if lane == "primary" and ev == "SLOT_RELEASE":
                    slot_release += 1
                if ev == "EXIT_EXECUTED" and lane not in {"primary", "control", ""}:
                    shadow_pbv2 += 1
        except Exception:
            pass
    can: dict = {}
    summary_path = session_dir / "small_paper_summary.json"
    if summary_path.is_file():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            raw = loaded.get("canonical_summary") or {}
            can = raw if isinstance(raw, dict) else {}
        except Exception:
            can = {}
    can_count = int(can.get("trade_count") or 0)
    try:
        can_pnl = int(round(float(can.get("total_pnl_yen_100") or can.get("pnl_yen_100") or can.get("sum_pnl_yen_100") or 0)))
    except (TypeError, ValueError):
        can_pnl = 0
    primary_exits = strategy_exits + session_close_exits
    discord = _discord_builds(session_dir)
    return {
        "v1r_fill": fills,
        "strategy_exit_executed": strategy_exits,
        "session_close_exit_executed": session_close_exits,
        "primary_exit_executed": primary_exits,
        "slot_release": slot_release,
        "canonical_trade_count": can_count,
        "canonical_pnl_yen_100": can_pnl,
        "strategy_exit_pnl_yen_100": pnl_yen_100,
        "shadow_pbv2_exit_contamination": shadow_pbv2,
        "canonical_count_matches_primary_exit": can_count == primary_exits,
        "canonical_count_matches_strategy_exit": can_count == strategy_exits,
        "canonical_pnl_matches_strategy_exit": can_pnl == pnl_yen_100,
        **discord,
    }


def _discord_builds(session_dir: Path) -> dict:
    counts = {"ENTRY": 0, "FILL": 0, "EXIT": 0, "SUMMARY": 0}
    path = session_dir / "v1r_discord_delivery.jsonl"
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                kind = str(row.get("notify_kind") or row.get("kind") or "").upper()
                if kind in counts:
                    counts[kind] += 1
        except Exception:
            pass
    summary_build = 0
    for p in session_dir.rglob("discord_done.json"):
        try:
            body = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        per = ((body.get("delivery") or {}).get("per_key") or {}).get("paper") or {}
        if per.get("status") in {"SENT", "QUEUED"} or bool(per.get("ok")):
            summary_build += 1
        elif body.get("summary_path"):
            summary_build += 1
    if summary_build == 0:
        can_path = session_dir / "small_paper_summary.json"
        if can_path.is_file():
            try:
                loaded = json.loads(can_path.read_text(encoding="utf-8"))
                if isinstance(loaded.get("canonical_summary"), dict):
                    summary_build = 1
            except Exception:
                pass
    return {
        "discord_entry_build": counts["ENTRY"],
        "discord_fill_build": counts["FILL"],
        "discord_exit_build": counts["EXIT"],
        "discord_summary_build": summary_build,
    }


def _eval_sessions(scope: dict[str, str]) -> dict:
    from small_paper.ingress_run_identity import artifact_matches_scope

    root = NATIVE / "results" / "small_paper" / DAY
    out: dict = {
        "matched": [],
        "push_messages": 0,
        "gate_evaluations": 0,
        "processed_event": 0,
        "canonical_trade_count": 0,
        "stop_reasons": [],
        "session_seal": [],
        "kabu_station_connection": 0,
        "invalid_no_gate": False,
        "waiting_market": False,
        "session_clock_stop_valid": False,
        "v1r_fill": 0,
        "strategy_exit_executed": 0,
        "session_close_exit_executed": 0,
        "slot_release": 0,
        "shadow_pbv2_exit_contamination": 0,
        "discord_entry_build": 0,
        "discord_fill_build": 0,
        "discord_exit_build": 0,
        "discord_summary_build": 0,
    }
    if not root.is_dir():
        return out
    for cand in sorted(root.glob("live_session_*")):
        summary_path = cand / "small_paper_summary.json"
        ident_path = cand / "session_identity.json"
        ident: dict = {}
        if ident_path.is_file():
            try:
                ident = json.loads(ident_path.read_text(encoding="utf-8"))
            except Exception:
                ident = {}
        summary: dict = {}
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                summary = {}
        doc = {**ident, **summary}
        if not artifact_matches_scope(doc, scope):
            continue
        seal_status = ""
        seal_path = cand / "session_seal.json"
        if seal_path.is_file():
            try:
                seal_status = str(json.loads(seal_path.read_text(encoding="utf-8")).get("session_seal_status") or "")
            except Exception:
                seal_status = ""
        push = int(summary.get("push_messages") or 0)
        gates = int(summary.get("gate_evaluations") or 0)
        stop = str(summary.get("stop_reason") or "")
        can = summary.get("canonical_summary") or {}
        trades = int((can.get("trade_count") if isinstance(can, dict) else 0) or summary.get("canonical_trade_count") or 0)
        stdout = ""
        sp = cand / "pilot_stdout.log"
        if sp.is_file():
            stdout = sp.read_text(encoding="utf-8", errors="replace")
        kabu_fail = 1 if "kabu_station_connection" in stdout or "CURRENT_STAGE_OWNER_DEAD" in stdout else 0
        processed_at = str(
            summary.get("paper_last_processed_event_time")
            or ident.get("paper_last_processed_event_time")
            or ""
        )
        row = {
            "session": cand.name,
            "session_kind": ident.get("session_kind"),
            "push_messages": push,
            "gate_evaluations": gates,
            "stop_reason": stop,
            "canonical_trade_count": trades,
            "session_seal_status": seal_status,
            "session_validity": summary.get("session_validity"),
            "session_clock_stop_valid": bool(summary.get("session_clock_stop_valid")),
            "kabu_station_connection": kabu_fail,
            "paper_last_processed_event_time": processed_at,
        }
        parity = _lane_parity(cand)
        row.update(parity)
        out["matched"].append(row)
        out["v1r_fill"] += int(parity.get("v1r_fill") or 0)
        out["strategy_exit_executed"] += int(parity.get("strategy_exit_executed") or 0)
        out["session_close_exit_executed"] += int(parity.get("session_close_exit_executed") or 0)
        out["slot_release"] += int(parity.get("slot_release") or 0)
        out["shadow_pbv2_exit_contamination"] += int(parity.get("shadow_pbv2_exit_contamination") or 0)
        out["discord_entry_build"] += int(parity.get("discord_entry_build") or 0)
        out["discord_fill_build"] += int(parity.get("discord_fill_build") or 0)
        out["discord_exit_build"] += int(parity.get("discord_exit_build") or 0)
        out["discord_summary_build"] += int(parity.get("discord_summary_build") or 0)
        out["push_messages"] += push
        out["gate_evaluations"] += gates
        if processed_at or push > 0:
            out["processed_event"] += 1
        out["canonical_trade_count"] += trades
        out["stop_reasons"].append(stop)
        out["session_seal"].append(seal_status)
        out["kabu_station_connection"] += kabu_fail
        if bool(summary.get("session_clock_stop_valid")):
            out["session_clock_stop_valid"] = True
        if stop == "WAITING_MARKET" or str(summary.get("session_validity") or "") == "WAITING_MARKET":
            out["waiting_market"] = True
        if str(summary.get("session_validity") or "") == "INVALID_NO_GATE" or stop == "INVALID_NO_GATE":
            out["invalid_no_gate"] = True
    return out


def _load_watermarks() -> dict:
    from small_paper.runtime_clock import load_replay_watermarks, now_jst

    wm = load_replay_watermarks()
    try:
        wm["session_now"] = now_jst().isoformat(timespec="milliseconds")
    except Exception:
        pass
    return wm


def _residuals_before_kill() -> dict:
    from small_paper.runtime_lifecycle import evaluate_teardown_residuals
    from small_paper.v1r_pbv2_duplicate_runtime import list_live_ingress, list_live_pilots

    residuals = evaluate_teardown_residuals(native_root=NATIVE, trading_date=DAY)
    ingress = list(list_live_ingress(trading_date=DAY, native_root=NATIVE) or [])
    pilots = list(list_live_pilots(trading_date=DAY) or [])
    return {
        "evaluate_teardown_residuals": residuals,
        "live_ingress": ingress,
        "live_pilots": pilots,
        "managed_process_residual": int(bool(ingress or pilots)),
        "killed_to_zero": False,
    }


def _ingress_identity() -> dict:
    status_path = NATIVE / "data" / "market_capture" / DAY / "ingress_status.json"
    if not status_path.is_file():
        return {}
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _scan_auth(text: str) -> dict:
    return {
        "token_stage_match": "TOKEN_STAGE_MATCH" in text,
        "auth_ready": "AUTH_READY" in text or "REAL_KABUS_AUTH_READY" in text,
        "paper_primary_ready": "PAPER_PRIMARY" in text and ("READY" in text or "Paper Primary" in text or "role=PAPER_PRIMARY" in text),
        "paper_child": "run_small_paper_pilot" in text or "pilot started" in text.lower() or "child started" in text.lower() or "[8/17]" in text or "Pilot" in text,
        "code_4001007": (
            "ENVIRONMENT_AUTH_BLOCKED" in text
            or "kabu_code=4001007" in text
            or '"kabu_code": "4001007"' in text
            or "HTTP 4001007" in text
        ),
        "unscoped_reuse": "PREVIOUS_STAGE" in text or "UNSCOPED" in text and "reuse" in text.lower(),
        "winerror5_loop_kill": ("WinError 5" in text or "WinError5" in text) and ("replay" in text.lower() and ("kill" in text.lower() or "abort" in text.lower() or "fatal" in text.lower())),
        "http_200": "HTTP 200" in text or "status=200" in text or " 200 " in text,
        "continuing_until_1535": "CONTINUING_UNTIL_1535" in text,
        "fail_closed_multiple_current": "FAIL_CLOSED_MULTIPLE_CURRENT" in text,
        "kill_used": '"kill_used": true' in text or "kill_used=True" in text,
    }


def _clock_snapshot() -> dict:
    arm: dict = {}
    if ARM_FILE.is_file():
        try:
            loaded = json.loads(ARM_FILE.read_text(encoding="utf-8"))
            arm = loaded if isinstance(loaded, dict) else {"_raw": loaded}
        except Exception as exc:
            arm = {"_error": f"{type(exc).__name__}:{exc}"}
    ident = _ingress_identity()
    return {
        "arm": {
            "t0": arm.get("t0"),
            "v0": arm.get("v0"),
            "speed": arm.get("speed"),
            "stop": arm.get("stop"),
            "replay_eof": arm.get("replay_eof"),
            "certification_run_id": arm.get("certification_run_id"),
            "stage_run_id": arm.get("stage_run_id"),
            "replay_read_watermark": arm.get("replay_read_watermark"),
            "ingress_publish_watermark": arm.get("ingress_publish_watermark"),
            "consumer_ack_watermark": arm.get("consumer_ack_watermark"),
            "paper_last_processed_event_time": arm.get("paper_last_processed_event_time"),
        },
        "watermarks": _load_watermarks(),
        "ingress": {
            "pid": ident.get("pid"),
            "state": ident.get("state"),
            "generation": ident.get("generation"),
            "ingress_run_id": ident.get("ingress_run_id") or ident.get("run_id"),
            "certification_run_id": ident.get("certification_run_id"),
            "stage_run_id": ident.get("stage_run_id"),
            "last_error": ident.get("last_error"),
        },
        "env": {
            ENV_ENABLED: os.environ.get(ENV_ENABLED),
            ENV_V0: os.environ.get(ENV_V0),
            ENV_T0: os.environ.get(ENV_T0),
            ENV_SPEED: os.environ.get(ENV_SPEED),
            ENV_STOP: os.environ.get(ENV_STOP),
            ENV_ARM_FILE: os.environ.get(ENV_ARM_FILE),
            ENV_REPLAY_PATH: os.environ.get(ENV_REPLAY_PATH),
            ENV_CERT_MODE: os.environ.get(ENV_CERT_MODE),
            ENV_CERTIFICATION_RUN_ID: os.environ.get(ENV_CERTIFICATION_RUN_ID),
            ENV_STAGE_RUN_ID: os.environ.get(ENV_STAGE_RUN_ID),
        },
    }


STAGE_SPECS = {
    "window_b": {
        "start": datetime(2026, 8, 12, 11, 20, 0, tzinfo=JST),
        "stop": datetime(2026, 8, 12, 12, 45, 0, tzinfo=JST),
        "not_before": "11:20",
        "timeout": 2400,
        "target": "B",
    },
    "pm_direct": {
        "start": datetime(2026, 8, 12, 12, 30, 0, tzinfo=JST),
        "stop": datetime(2026, 8, 12, 15, 35, 0, tzinfo=JST),
        "not_before": "12:30",
        "timeout": 14400,
        "target": "A",
    },
    "pm_direct_short": {
        "start": datetime(2026, 8, 12, 12, 30, 0, tzinfo=JST),
        "stop": datetime(2026, 8, 12, 12, 50, 0, tzinfo=JST),
        "not_before": "12:30",
        "timeout": 1800,
        "target": "C",
    },
    "full_day_boundary": {
        "start": datetime(2026, 8, 12, 11, 20, 0, tzinfo=JST),
        "stop": datetime(2026, 8, 12, 12, 45, 0, tzinfo=JST),
        "not_before": "11:20",
        "timeout": 2400,
        "target": "A",
    },
    "full_day": {
        "start": datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST),
        "stop": datetime(2026, 8, 12, 15, 35, 0, tzinfo=JST),
        "not_before": "08:50",
        "timeout": 14400,
        "target": "A",
    },
    "fill_am": {
        "start": datetime(2026, 8, 12, 9, 3, 0, tzinfo=JST),
        "stop": datetime(2026, 8, 12, 9, 25, 0, tzinfo=JST),
        "not_before": "09:03",
        "timeout": 7200,
        "target": "SENTINEL",
    },
    "restart": {
        "start": datetime(2026, 8, 12, 12, 30, 0, tzinfo=JST),
        "stop": datetime(2026, 8, 12, 12, 40, 0, tzinfo=JST),
        "not_before": "12:30",
        "timeout": 1800,
        "target": "C",
    },
}


def _proof_fail(stage: str, result: dict, speed: float) -> list[str]:
    mets = result.get("metrics") or {}
    auth = result.get("auth_scan") or {}
    fail: list[str] = []
    raw_exit = result.get("exit_code")
    try:
        exit_i = int(raw_exit) if raw_exit is not None else 1
    except (TypeError, ValueError):
        exit_i = 1
    if exit_i == 124:
        fail.append("timeout124")
    if exit_i != 0:
        fail.append(f"bat_exit={exit_i}")
    if int(mets.get("kabu_station_connection") or 0) != 0:
        fail.append("kabu_station_connection")
    if int(mets.get("push_messages") or 0) <= 0:
        fail.append("push=0")
    if int(mets.get("gate_evaluations") or 0) <= 0:
        fail.append("gate=0")
    processed = 0
    for row in mets.get("matched") or []:
        if row.get("paper_last_processed_event_time") or int(row.get("push_messages") or 0) > 0:
            processed += 1
    if processed <= 0 and int(mets.get("push_messages") or 0) <= 0:
        fail.append("processed_event=0")
    if mets.get("invalid_no_gate") is True:
        fail.append("INVALID_NO_GATE")
    if mets.get("waiting_market") is True:
        fail.append("WAITING_MARKET")
    seals = list(mets.get("session_seal") or [])
    if "SEALED_VALID" not in seals:
        fail.append("not_SEALED_VALID")
    matched = list(mets.get("matched") or [])
    if not any(str(r.get("session_validity") or "") == "VALID_SESSION" for r in matched):
        fail.append("not_VALID_SESSION")
    sessions_collected = result.get("post_session_collected")
    post_result = str(result.get("post_session_result") or "")
    if stage in ("window_b", "full_day_boundary"):
        if int(sessions_collected or 0) != 2:
            fail.append(f"sessions_collected={sessions_collected}")
        am_ok = [
            r
            for r in matched
            if str(r.get("session_kind") or "").lower() == "am"
            and str(r.get("session_validity") or "") == "VALID_SESSION"
            and str(r.get("session_seal_status") or "") == "SEALED_VALID"
        ]
        pm_ok = [
            r
            for r in matched
            if str(r.get("session_kind") or "").lower() == "pm"
            and str(r.get("session_validity") or "") == "VALID_SESSION"
            and str(r.get("session_seal_status") or "") == "SEALED_VALID"
        ]
        if len(am_ok) != 1:
            fail.append(f"am_valid_sealed={len(am_ok)}")
        if len(pm_ok) != 1:
            fail.append(f"pm_valid_sealed={len(pm_ok)}")
        if post_result == "FAIL_CLOSED_MULTIPLE_CURRENT":
            fail.append("FAIL_CLOSED_MULTIPLE_CURRENT")
        kinds = sorted(
            {
                str(r.get("session_kind") or "").lower()
                for r in matched
                if str(r.get("session_kind") or "").lower() in {"am", "pm"}
            }
        )
        if kinds != ["am", "pm"]:
            fail.append(f"topology={kinds}")
    elif int(sessions_collected or 0) < 1:
        fail.append("sessions_collected=0")
    if post_result == "SESSION_ARTIFACT_INCOMPLETE":
        fail.append("SESSION_ARTIFACT_INCOMPLETE")
    stop_reasons = [str(x) for x in (mets.get("stop_reasons") or [])]
    clock_stop = any(x == "session_clock_stop" for x in stop_reasons)
    if clock_stop and not bool(mets.get("session_clock_stop_valid")):
        fail.append("session_clock_stop_invalid")
    stdout = str(result.get("stdout_text") or result.get("stdout_tail") or "")
    if "CURRENT_STAGE_OWNER_DEAD" in stdout:
        fail.append("CURRENT_STAGE_OWNER_DEAD")
    if int((result.get("token_delta") or {}).get("overlap_count") or 0) != 0:
        fail.append("issuer_overlap")
    if auth.get("code_4001007"):
        fail.append("4001007")
    if not auth.get("token_stage_match"):
        fail.append("TOKEN_STAGE_MATCH_missing")
    if auth.get("winerror5_loop_kill"):
        fail.append("WinError5_loop_killer")
    if auth.get("continuing_until_1535"):
        fail.append("CONTINUING_UNTIL_1535")
    residuals_wrap = result.get("residuals_before_orchestrator_kill") or {}
    ev_res = residuals_wrap.get("evaluate_teardown_residuals") or {}
    res = ev_res.get("residuals") or {}
    live_ingress = list(residuals_wrap.get("live_ingress") or [])
    live_pilots = list(residuals_wrap.get("live_pilots") or [])
    if live_ingress:
        fail.append(f"live_owned_ingress={len(live_ingress)}")
        fail.append(f"force_terminate_count={len(live_ingress)}")
    if live_pilots:
        fail.append(f"live_pilots={len(live_pilots)}")
    if int(residuals_wrap.get("managed_process_residual") or 0) != 0:
        fail.append("managed_process_residual")
    if int(ev_res.get("wrong_process_kill") or 0) != 0:
        fail.append("wrong_process_kill")
    if int(res.get("current_station_owner_residual") or 0) != 0:
        fail.append("station_owner_residual")
    if int(res.get("current_issuer_residual") or 0) != 0:
        fail.append("issuer_residual")
    if int(res.get("active_current_token_authority_residual") or 0) != 0:
        fail.append("active_token_authority_residual")
    if int(res.get("mutex_or_lease_residual") or 0) != 0:
        fail.append("mutex_or_lease_residual")
    if int(res.get("canonical_status_writer_residual") or 0) != 0:
        fail.append("status_writer_residual")
    if int(res.get("current_registration_residual") or 0) != 0:
        fail.append("registration_residual")
    if auth.get("kill_used"):
        fail.append("cleanup_kill_used")
    if stage in ("fill_am",):
        if int(mets.get("v1r_fill") or 0) < 1:
            fail.append("V1R_FILL=0")
        if int(mets.get("strategy_exit_executed") or 0) < 1:
            fail.append("strategy_EXIT_EXECUTED=0")
        if int(mets.get("slot_release") or 0) < 1:
            fail.append("SLOT_RELEASE=0")
        if int(mets.get("session_close_exit_executed") or 0) != 0:
            fail.append("SESSION_CLOSE_exit_not_strategy_parity")
        if int(mets.get("shadow_pbv2_exit_contamination") or 0) != 0:
            fail.append("shadow_pbv2_contamination")
        if not any(bool(r.get("canonical_count_matches_primary_exit")) for r in matched):
            fail.append("canonical_summary_parity")
        if not any(bool(r.get("canonical_pnl_matches_strategy_exit")) for r in matched):
            fail.append("canonical_pnl_parity")
    if stage in ("restart", "pm_direct_short", "pm_direct"):
        if not auth.get("token_stage_match"):
            fail.append("next_stage_TOKEN_STAGE_MATCH")
        if int(mets.get("push_messages") or 0) <= 0:
            fail.append("next_stage_push=0")
        if int(mets.get("gate_evaluations") or 0) <= 0:
            fail.append("next_stage_gate=0")
    _ = speed
    return fail


def _state_leak(prev_clock: dict, bind_clock: dict, after_clock: dict, prev_scope: dict, new_scope: dict) -> dict:
    """Previous-stage mutable clock/replay/identity must not be reused."""
    prev_arm = (prev_clock or {}).get("arm") or {}
    bind_arm = (bind_clock or {}).get("arm") or {}
    after_ing = (after_clock or {}).get("ingress") or {}
    prev_ing = (prev_clock or {}).get("ingress") or {}
    fail: list[str] = []
    if str(bind_arm.get("v0") or "") == str(prev_arm.get("v0") or "") and prev_arm.get("v0"):
        fail.append("bind_v0_reused_previous")
    if bool(bind_arm.get("replay_eof")):
        fail.append("bind_replay_eof_true")
    if bind_arm.get("replay_read_watermark") not in (None, ""):
        fail.append("bind_stale_read_watermark")
    if bind_arm.get("ingress_publish_watermark") not in (None, ""):
        fail.append("bind_stale_publish_watermark")
    if bind_arm.get("consumer_ack_watermark") not in (None, ""):
        fail.append("bind_stale_ack_watermark")
    if bind_arm.get("paper_last_processed_event_time") not in (None, ""):
        fail.append("bind_stale_paper_processed_watermark")
    if bind_arm.get("t0") not in (None, "") and str(bind_arm.get("t0") or "") == str(prev_arm.get("t0") or ""):
        fail.append("bind_t0_reused_previous")
    if str(new_scope.get("stage_run_id") or "") and str(new_scope.get("stage_run_id")) == str(prev_scope.get("stage_run_id") or ""):
        fail.append("stage_run_id_reused")
    prev_ing_run = str(prev_ing.get("ingress_run_id") or "")
    after_ing_run = str(after_ing.get("ingress_run_id") or "")
    if prev_ing_run and after_ing_run and prev_ing_run == after_ing_run:
        fail.append("ingress_run_id_reused")
    return {
        "ok": not fail,
        "fail": fail,
        "previous_arm": prev_arm,
        "bind_arm": bind_arm,
        "previous_ingress_run_id": prev_ing_run,
        "after_ingress_run_id": after_ing_run,
        "previous_stage_run_id": prev_scope.get("stage_run_id"),
        "new_stage_run_id": new_scope.get("stage_run_id"),
        "same_certification_run_id": str(new_scope.get("certification_run_id") or "")
        == str(prev_scope.get("certification_run_id") or ""),
    }


def run_one_stage(
    *,
    cert,
    base_env: dict[str, str],
    stage: str,
    speed: float,
    prekill: bool,
    ident: dict,
    kabu: dict,
    certification_run_id: str,
    selector_path: Path,
    activation_id: str,
    previous_clock: dict | None = None,
    previous_scope: dict | None = None,
    opval_offline: bool = False,
) -> dict:
    spec = STAGE_SPECS[stage]
    env = dict(base_env)
    env[ENV_REPLAY_EPS] = "150" if float(speed) <= 1.01 else "2500"
    env[ENV_CERTIFICATION_RUN_ID] = certification_run_id
    env["TRADEBOT_DAILY_RUN_ID"] = "daily_" + certification_run_id[:16]
    nonce = generate_launch_nonce()[:12]
    if stage in ("full_day", "full_day_boundary"):
        stage_run_id = f"full_day_g6_{stage}_{nonce}"
    else:
        stage_run_id = f"g6_{stage}_{nonce}"
    env[ENV_STAGE_RUN_ID] = stage_run_id
    bind_session_clock(
        virtual_start=spec["start"],
        speed_mult=float(speed),
        stop=spec["stop"],
        environ=env,
        arm_now=False,
        arm_file=ARM_FILE,
    )
    env["TRADEBOT_INGRESS_REPLAY_NOT_BEFORE"] = spec["not_before"]
    env = official_cert_child_env(env)
    env[ENV_CERTIFICATION_RUN_ID] = certification_run_id
    env[ENV_STAGE_RUN_ID] = stage_run_id
    sel = json.loads(selector_path.read_text(encoding="utf-8"))
    activation_sha = str(sel.get("activation_sha") or "")
    scope = {
        "certification_run_id": certification_run_id,
        "stage_run_id": stage_run_id,
        "activation_sha": activation_sha,
        "activation_id": activation_id,
    }
    if prekill:
        cert._stop_cert_children(DAY)
    print(
        f"G6 stage={stage} speed={speed}x cert={certification_run_id} "
        f"stage_run={stage_run_id} activation={activation_id} prekill={prekill}",
        flush=True,
    )
    from small_paper.kabu_token_authority import station_issue_audit_summary

    clock_after_bind = _clock_snapshot()
    token0 = station_issue_audit_summary()
    ingress_before = _ingress_identity()
    t0 = time.time()
    try:
        result = cert._invoke_checked_bat(
            env=env,
            timeout_sec=float(spec["timeout"]),
            log_name=f"g6_{stage}_{speed}x".replace(".", "p"),
            certification_run_id=certification_run_id,
            stage_run_id=stage_run_id,
        )
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}:{exc}", "exit_code": 124}
    result["duration_sec"] = round(time.time() - t0, 3)
    result["token_delta"] = cert._token_delta(token0, station_issue_audit_summary())
    result["clock_after_bind"] = clock_after_bind
    result["clock_after_stage_before_orchestrator_kill"] = _clock_snapshot()
    result["ingress_before"] = {
        "pid": ingress_before.get("pid"),
        "state": ingress_before.get("state"),
        "generation": ingress_before.get("generation"),
    }
    result["ingress_after_before_kill"] = {
        "pid": _ingress_identity().get("pid"),
        "state": _ingress_identity().get("state"),
        "generation": _ingress_identity().get("generation"),
        "last_error": _ingress_identity().get("last_error"),
    }
    result["residuals_before_orchestrator_kill"] = _residuals_before_kill()
    result["cleanup"] = cert._stop_cert_children(DAY)
    result["metrics"] = _eval_sessions(scope)
    result["watermarks"] = _load_watermarks()
    result["kabu_precheck"] = kabu
    result["scope"] = scope
    result["identity"] = ident
    result["activation_id"] = activation_id
    result["activation_sha"] = activation_sha
    if opval_offline:
        result.update(OPVAL_LABELS)
    result["speed_mult"] = float(speed)
    result["stage"] = stage
    result["target"] = spec["target"]
    stdout = str(result.get("stdout_tail") or "")
    stdout_path = Path(str(result.get("stdout_path") or ""))
    if stdout_path.is_file():
        try:
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    result["stdout_text"] = stdout[-200000:]
    result["auth_scan"] = _scan_auth(stdout)
    sessions_collected = None
    post_result = ""
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith("sessions_collected:"):
            try:
                sessions_collected = int(line.split(":", 1)[1].strip() or 0)
            except ValueError:
                sessions_collected = 0
        if line.startswith("result:"):
            post_result = line.split(":", 1)[1].strip()
    result["post_session_collected"] = sessions_collected
    result["post_session_result"] = post_result
    fail = _proof_fail(stage, result, speed)
    if previous_clock:
        leak = _state_leak(
            previous_clock,
            clock_after_bind,
            result.get("clock_after_stage_before_orchestrator_kill") or {},
            previous_scope or {},
            scope,
        )
        result["state_leak"] = leak
        fail.extend([f"state_leak:{x}" for x in (leak.get("fail") or [])])
    result["proof_ok"] = not fail
    result["proof_fail_reasons"] = fail
    out = G6_DIR / f"g6_{stage}_{str(speed).replace('.', 'p')}x.json"
    slim = dict(result)
    slim.pop("stdout_text", None)
    out.write_text(json.dumps(slim, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    result["out"] = str(out)
    print(
        json.dumps(
            {
                "stage": stage,
                "speed": speed,
                "exit_code": result.get("exit_code"),
                "ok": result.get("ok"),
                "proof_ok": result["proof_ok"],
                "proof_fail_reasons": fail,
                "post_session_collected": sessions_collected,
                "post_session_result": post_result,
                "push": (result.get("metrics") or {}).get("push_messages"),
                "gates": (result.get("metrics") or {}).get("gate_evaluations"),
                "live_ingress_before_kill": (result.get("residuals_before_orchestrator_kill") or {}).get("live_ingress"),
                "clock_after_stage": result.get("clock_after_stage_before_orchestrator_kill"),
                "out": str(out),
            },
            default=str,
        ),
        flush=True,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=tuple(STAGE_SPECS) + ("chain_a",),
    )
    parser.add_argument("--seq", default="", help="comma-separated stages, e.g. window_b,restart")
    parser.add_argument("--speed", type=float, default=48.0)
    parser.add_argument("--no-prekill", action="store_true")
    parser.add_argument(
        "--identity",
        choices=("working", "opval", "candidate6"),
        default="working",
        help="working=G6 RCA selector; opval=20260817 OPVAL; candidate6=UNCERTIFIED C6 selector",
    )
    args = parser.parse_args()
    opval_offline = args.identity == "opval"
    if args.identity == "candidate6":
        selector_path = C6_SELECTOR
        activation_id = C6_ID
        ident_script = "assert_v26g6_candidate6_identity.py"
    elif opval_offline:
        selector_path = OPVAL_SELECTOR
        activation_id = OPVAL_ACTIVATION_ID
        ident_script = "assert_v26g6_opval_identity.py"
    else:
        selector_path = WORKING_SELECTOR
        activation_id = WORKING_ID
        ident_script = "assert_v26g6_working_identity.py"
    if args.seq:
        stages = [x.strip() for x in args.seq.split(",") if x.strip()]
    elif args.stage == "chain_a":
        stages = ["full_day_boundary", "pm_direct_short"]
    elif args.stage:
        stages = [args.stage]
    else:
        print("need --stage or --seq")
        return 2
    for st in stages:
        if st not in STAGE_SPECS:
            print("unknown stage", st)
            return 2
    ident_spec = importlib.util.spec_from_file_location(
        ident_script.replace(".py", ""),
        NATIVE / "scripts" / ident_script,
    )
    ident_mod = importlib.util.module_from_spec(ident_spec)
    assert ident_spec.loader is not None
    ident_spec.loader.exec_module(ident_mod)
    ident = ident_mod.check()
    if not ident.get("ok"):
        if args.identity == "candidate6":
            verdict = "V1R_V26G6_CANDIDATE6_IDENTITY_FAIL"
        elif opval_offline:
            verdict = "V1R_OPVAL_IDENTITY_FAIL"
        else:
            verdict = "V1R_V26G6_WORKING_IDENTITY_FAIL"
        print(json.dumps({"verdict": verdict, **ident}, default=str))
        return 2
    if not selector_path.is_file():
        print("MISSING_SELECTOR", selector_path)
        return 2
    sel_body = json.loads(selector_path.read_text(encoding="utf-8"))
    if sel_body.get("activation_id") != activation_id:
        print(json.dumps({"verdict": "V1R_SELECTOR_ID_MISMATCH", "selector": sel_body}, default=str))
        return 2
    cert = _cert()
    G6_DIR.mkdir(parents=True, exist_ok=True)
    fixture = cert.CERT_DIR / "ingress_replay_20260812_full_day_certification.jsonl"
    if not fixture.is_file():
        print("MISSING_FIXTURE", fixture)
        return 2
    kabu = cert._kabu_precheck()
    if not kabu.get("ok"):
        print("KABU_PRECHECK_FAIL", json.dumps(kabu, default=str))
        return 2
    sink, webhook, _thr = cert._start_sink()
    env = os.environ.copy()
    env[ENV_CERT_MODE] = "1"
    env.pop("TRADEBOT_OPERATIONAL_VALIDATION_MODE", None)
    env[ENV_ACTIVATION_SELECTOR] = str(selector_path)
    env["TRADEBOT_TRADING_DATE"] = DAY
    env["PAPER_EXTERNAL_BACKUP_ROOT"] = r"Z:\cert_kabudata_missing"
    env["KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"] = webhook
    env["KABU_V1R_ENTRY_WEBHOOK_URL"] = webhook
    env["KABU_DISCORD_RESEARCH_WEBHOOK_URL"] = webhook
    env["KABU_SHADOW_DISCORD_WEBHOOK_URL"] = webhook
    env["KABU_DISCORD_OPERATIONS_WEBHOOK_URL"] = webhook
    env["KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL"] = webhook
    env[ENV_REPLAY_PATH] = str(fixture)
    env["TRADEBOT_INGRESS_REPLAY_MAX_LAG"] = "128"
    certification_run_id = "cert_" + generate_launch_nonce()
    results: list[dict] = []
    rc = 0
    try:
        for i, st in enumerate(stages):
            prekill = (i == 0) and (not args.no_prekill)
            one = run_one_stage(
                cert=cert,
                base_env=env,
                stage=st,
                speed=float(args.speed),
                prekill=prekill,
                ident=ident,
                kabu=kabu,
                certification_run_id=certification_run_id,
                selector_path=selector_path,
                activation_id=activation_id,
                previous_clock=(results[-1].get("clock_after_stage_before_orchestrator_kill") if results else None),
                previous_scope=(results[-1].get("scope") if results else None),
                opval_offline=opval_offline,
            )
            results.append(one)
            if not one.get("proof_ok"):
                rc = 2
                break
    finally:
        try:
            sink.shutdown()
        except BaseException:
            pass
        try:
            sink.server_close()
        except BaseException:
            pass
    summary = {
        "certification_run_id": certification_run_id,
        "speed": args.speed,
        "identity": args.identity,
        "activation_id": activation_id,
        "stages": [r.get("stage") for r in results],
        "proof_ok": [bool(r.get("proof_ok")) for r in results],
        "all_pass": bool(results) and all(bool(r.get("proof_ok")) for r in results) and len(results) == len(stages),
        "outs": [r.get("out") for r in results],
    }
    if opval_offline:
        summary.update(OPVAL_LABELS)
    (G6_DIR / "last_seq.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, default=str), flush=True)
    return rc if rc else (0 if summary["all_pass"] else 2)


if __name__ == "__main__":
    raise SystemExit(main())
