#!/usr/bin/env python
"""V26-G4 targeted RCA proofs. Not 8h Full Certification. Not Formal Paper.

Stages:
  am_pm     11:20 AM close → 12:30 PM start → first PM PUSH (full-day BAT)
  window_a  08:50-09:20 fill-capable / clock watermark
  pm_direct 12:30 start, short STOP
  window_c  15:10-15:35 regression

Uses working-tree selector (not Candidate-5). submit/cancel/live must stay 0/0/0.
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
from small_paper.runtime_clock import ENV_CERT_MODE, ENV_REPLAY_EPS, ENV_REPLAY_PATH, bind_session_clock, official_cert_child_env
from small_paper.v1r_activation_binding import ENV_ACTIVATION_SELECTOR

JST = ZoneInfo("Asia/Tokyo")
G4_DIR = NATIVE / "results" / "research" / "v26g4_targeted_runtime_fix"
SELECTOR = G4_DIR / "active_v1r_working_v26g4.json"
CERT_SCRIPT = NATIVE / "scripts" / "run_paper_full_day_certification.py"
DAY = "20260812"


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
                            pnl_yen_100 += int(row.get("pnl_yen_100") or 0)
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
        can_pnl = int(can.get("pnl_yen_100") or can.get("sum_pnl_yen_100") or 0)
    except (TypeError, ValueError):
        can_pnl = 0
    primary_exits = strategy_exits + session_close_exits
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
    }


def _eval_sessions(scope: dict[str, str]) -> dict:
    from small_paper.ingress_run_identity import artifact_matches_scope

    root = NATIVE / "results" / "small_paper" / DAY
    out: dict = {
        "matched": [],
        "push_messages": 0,
        "gate_evaluations": 0,
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
        }
        parity = _lane_parity(cand)
        row.update(parity)
        out["matched"].append(row)
        out["v1r_fill"] += int(parity.get("v1r_fill") or 0)
        out["strategy_exit_executed"] += int(parity.get("strategy_exit_executed") or 0)
        out["session_close_exit_executed"] += int(parity.get("session_close_exit_executed") or 0)
        out["slot_release"] += int(parity.get("slot_release") or 0)
        out["shadow_pbv2_exit_contamination"] += int(parity.get("shadow_pbv2_exit_contamination") or 0)
        out["push_messages"] += push
        out["gate_evaluations"] += gates
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        required=True,
        choices=("am_pm", "window_a", "pm_direct", "window_c", "fill_am"),
    )
    args = parser.parse_args()
    if not SELECTOR.is_file():
        print("MISSING_WORKING_SELECTOR", SELECTOR)
        return 2
    cert = _cert()
    G4_DIR.mkdir(parents=True, exist_ok=True)
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
    env[ENV_ACTIVATION_SELECTOR] = str(SELECTOR)
    env["TRADEBOT_TRADING_DATE"] = DAY
    env["PAPER_EXTERNAL_BACKUP_ROOT"] = r"Z:\cert_kabudata_missing"
    env["KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"] = webhook
    env["KABU_V1R_ENTRY_WEBHOOK_URL"] = webhook
    env["KABU_DISCORD_RESEARCH_WEBHOOK_URL"] = webhook
    env["KABU_SHADOW_DISCORD_WEBHOOK_URL"] = webhook
    env["KABU_DISCORD_OPERATIONS_WEBHOOK_URL"] = webhook
    env["KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL"] = webhook
    env[ENV_REPLAY_PATH] = str(fixture)
    env[ENV_REPLAY_EPS] = "2500"
    env["TRADEBOT_INGRESS_REPLAY_MAX_LAG"] = "128"
    arm_file = NATIVE / "data" / "market_capture" / DAY / "session_clock_arm.json"
    specs = {
        "am_pm": (
            datetime(2026, 8, 12, 11, 20, 0, tzinfo=JST),
            datetime(2026, 8, 12, 12, 35, 0, tzinfo=JST),
            "11:20",
            True,
            2400,
        ),
        "window_a": (
            datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST),
            datetime(2026, 8, 12, 9, 20, 0, tzinfo=JST),
            "08:50",
            False,
            2400,
        ),
        "pm_direct": (
            datetime(2026, 8, 12, 12, 30, 0, tzinfo=JST),
            datetime(2026, 8, 12, 12, 50, 0, tzinfo=JST),
            "12:30",
            False,
            1800,
        ),
        "window_c": (
            datetime(2026, 8, 12, 15, 10, 0, tzinfo=JST),
            datetime(2026, 8, 12, 15, 35, 0, tzinfo=JST),
            "15:10",
            False,
            1800,
        ),
        "fill_am": (
            datetime(2026, 8, 12, 9, 3, 0, tzinfo=JST),
            datetime(2026, 8, 12, 9, 25, 0, tzinfo=JST),
            "09:03",
            False,
            3600,
        ),
    }
    start, stop, not_before, full_day, timeout = specs[args.stage]
    bind_session_clock(
        virtual_start=start,
        speed_mult=48.0,
        stop=stop,
        environ=env,
        arm_now=False,
        arm_file=arm_file,
    )
    env["TRADEBOT_INGRESS_REPLAY_NOT_BEFORE"] = not_before
    env = official_cert_child_env(env)
    certification_run_id = "cert_" + generate_launch_nonce()
    env[ENV_CERTIFICATION_RUN_ID] = certification_run_id
    env["TRADEBOT_DAILY_RUN_ID"] = "daily_" + certification_run_id[:16]
    nonce = generate_launch_nonce()[:12]
    if full_day:
        stage_run_id = f"full_day_g4_{args.stage}_{nonce}"
    else:
        stage_run_id = f"g4_{args.stage}_{nonce}"
    env[ENV_STAGE_RUN_ID] = stage_run_id
    sel = json.loads(SELECTOR.read_text(encoding="utf-8"))
    scope = {
        "certification_run_id": certification_run_id,
        "stage_run_id": stage_run_id,
        "activation_sha": str(sel.get("activation_sha") or ""),
    }
    cert._stop_cert_children(DAY)
    print(f"G4_TARGETED stage={args.stage} cert={certification_run_id} stage_run={stage_run_id}", flush=True)
    from small_paper.kabu_token_authority import station_issue_audit_summary

    token0 = station_issue_audit_summary()
    ingress_before = _ingress_identity()
    t0 = time.time()
    try:
        result = cert._invoke_checked_bat(
            env=env,
            timeout_sec=timeout,
            log_name=f"g4_{args.stage}",
            certification_run_id=certification_run_id,
            stage_run_id=stage_run_id,
        )
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}:{exc}", "exit_code": 124}
    result["duration_sec"] = round(time.time() - t0, 3)
    result["token_delta"] = cert._token_delta(token0, station_issue_audit_summary())
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
    result["full_day_child"] = bool(full_day)
    result["scope"] = scope
    mets = result.get("metrics") or {}
    stdout = str(result.get("stdout_tail") or "")
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
    matched = list(mets.get("matched") or [])
    pm_rows = [r for r in matched if str(r.get("session_kind") or "").lower() == "pm"]
    stop_reasons = [str(x) for x in (mets.get("stop_reasons") or [])]
    clock_stop = any(x == "session_clock_stop" for x in stop_reasons)
    owner_dead = "CURRENT_STAGE_OWNER_DEAD" in stdout
    raw_exit = result.get("exit_code")
    try:
        exit_i = int(raw_exit) if raw_exit is not None else 1
    except (TypeError, ValueError):
        exit_i = 1
    fail: list[str] = []
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
    if mets.get("invalid_no_gate") is True:
        fail.append("INVALID_NO_GATE")
    if mets.get("waiting_market") is True:
        fail.append("WAITING_MARKET")
    if "SEALED_VALID" not in (mets.get("session_seal") or []):
        fail.append("not_SEALED_VALID")
    if int(sessions_collected or 0) < 1:
        fail.append("sessions_collected=0")
    if post_result == "SESSION_ARTIFACT_INCOMPLETE":
        fail.append("SESSION_ARTIFACT_INCOMPLETE")
    if clock_stop and not bool(mets.get("session_clock_stop_valid")):
        fail.append("session_clock_stop_invalid")
    if owner_dead:
        fail.append("CURRENT_STAGE_OWNER_DEAD")
    if int((result.get("token_delta") or {}).get("overlap_count") or 0) != 0:
        fail.append("issuer_overlap")
    if args.stage == "am_pm":
        if len(matched) < 2 or int(sessions_collected or 0) < 2:
            fail.append("am_pm_need_2_sessions")
        if not pm_rows:
            fail.append("pm_child_missing")
        elif int((pm_rows[0] or {}).get("push_messages") or 0) <= 0:
            fail.append("pm_push=0")
        elif int((pm_rows[0] or {}).get("gate_evaluations") or 0) <= 0:
            fail.append("pm_gate=0")
        elif str((pm_rows[0] or {}).get("session_validity") or "") != "VALID_SESSION":
            fail.append("pm_not_VALID_SESSION")
    if args.stage == "fill_am":
        if int(mets.get("v1r_fill") or 0) < 1:
            fail.append("V1R_FILL=0")
        if int(mets.get("strategy_exit_executed") or 0) < 1:
            fail.append("strategy_EXIT_EXECUTED=0")
        if int(mets.get("slot_release") or 0) < 1:
            fail.append("SLOT_RELEASE=0")
        if int(mets.get("shadow_pbv2_exit_contamination") or 0) != 0:
            fail.append("shadow_pbv2_contamination")
        if not any(bool(r.get("canonical_count_matches_primary_exit")) for r in matched):
            fail.append("canonical_summary_parity")
    proof_ok = not fail
    result["proof_ok"] = bool(proof_ok)
    result["proof_fail_reasons"] = fail
    out = G4_DIR / f"targeted_{args.stage}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "stage": args.stage,
        "exit_code": result.get("exit_code"),
        "ok": result.get("ok"),
        "proof_ok": proof_ok,
        "proof_fail_reasons": fail,
        "post_session_collected": sessions_collected,
        "post_session_result": post_result,
        "metrics": result.get("metrics"),
        "watermarks": result.get("watermarks"),
        "residuals_before_kill": result.get("residuals_before_orchestrator_kill"),
        "out": str(out),
    }, default=str), flush=True)
    try:
        sink.shutdown()
    except BaseException:
        pass
    try:
        sink.server_close()
    except BaseException:
        pass
    return 0 if proof_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
