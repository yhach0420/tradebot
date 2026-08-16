#!/usr/bin/env python
"""20260817 live OPVAL start gate. Capture must already be healthy.

Does not start Paper. Does not mutate Formal selector. Does not enable certification.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

from small_paper.operational_validation import (
    ENV_OPVAL_MODE,
    OPVAL_ACTIVATION_ID,
    OPVAL_TRADING_DATE,
    operational_validation_mode,
    opval_startup_blocked_reason,
)
from small_paper.runtime_clock import (
    ENV_ARM_FILE,
    ENV_CERT_MODE,
    ENV_ENABLED,
    ENV_REPLAY_PATH,
    certification_mode,
    ingress_replay_path,
    session_clock_enabled,
)
from small_paper.v1r_activation_binding import (
    ENV_ACTIVATION_SELECTOR,
    SELECTOR_PATH,
    load_activation_manifest,
    load_active_selector,
)
from small_paper.v1r_exit_v2_activation_gate import assert_exit_v2_primary_roles

JST = ZoneInfo("Asia/Tokyo")
DAY = OPVAL_TRADING_DATE
OPVAL_SELECTOR = NATIVE / "results" / "research" / "v26g6_targeted_rca" / "active_v1r_opval_20260817.json"
CAPTURE_DIR = NATIVE / "data" / "market_capture" / DAY
INGRESS_STATUS = CAPTURE_DIR / "ingress_status.json"
EVENTS = CAPTURE_DIR / "events.jsonl"


def _fail(fail: list[str], **extra) -> dict:
    return {
        "ok": False,
        "paper_start_allowed": False,
        "fail": fail,
        "paper_mode": "OPERATIONAL_VALIDATION_ONLY",
        "INVALID_FOR_STRATEGY_EVALUATION": True,
        "NOT_PROSPECTIVE_DAY1": True,
        **extra,
    }


def check_capture() -> dict:
    fail: list[str] = []
    if certification_mode():
        fail.append("CAPTURE_CERT_MODE")
    if session_clock_enabled():
        fail.append("CAPTURE_SESSION_CLOCK")
    if ingress_replay_path():
        fail.append("CAPTURE_REPLAY_PATH")
    if str(os.environ.get(ENV_ARM_FILE) or "").strip():
        fail.append("CAPTURE_ARM_FILE")
    status: dict = {}
    if INGRESS_STATUS.is_file():
        try:
            status = json.loads(INGRESS_STATUS.read_text(encoding="utf-8"))
        except Exception as exc:
            fail.append(f"INGRESS_STATUS:{type(exc).__name__}")
    else:
        fail.append("INGRESS_STATUS_MISSING")
    day = str(status.get("trading_date") or os.environ.get("TRADEBOT_TRADING_DATE") or "").strip()
    if day and day != DAY:
        fail.append(f"CAPTURE_TRADING_DATE={day}")
    state = str(status.get("state") or "").upper()
    if state not in {"RUNNING", "READY", "WRITING", "CAPTURE_WRITING"} and "RUN" not in state:
        if state:
            fail.append(f"CAPTURE_STATE={state}")
    events = 0
    symbols = 0
    size = 0
    if EVENTS.is_file():
        size = EVENTS.stat().st_size
        try:
            seen: set[str] = set()
            for line in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
                if not line.strip():
                    continue
                events += 1
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                sym = str(row.get("symbol") or row.get("Symbol") or "")
                if sym:
                    seen.add(sym)
            symbols = len(seen)
        except Exception:
            pass
    if events <= 0 and int(status.get("event_count") or status.get("push_messages") or 0) <= 0:
        fail.append("CAPTURE_EVENTS_NOT_INCREASING")
    if symbols < 2 and int(status.get("symbol_count") or 0) < 2:
        fail.append("CAPTURE_SYMBOLS_LT_2")
    if size <= 0:
        fail.append("CAPTURE_OUTPUT_SIZE_0")
    return {
        "ok": not fail,
        "fail": fail,
        "trading_date": day or DAY,
        "state": state,
        "events_sample": events,
        "symbols_sample": symbols,
        "events_file_bytes": size,
        "ingress_status": {k: status.get(k) for k in ("state", "pid", "trading_date", "event_count", "symbol_count") if k in status},
    }


def check_kabu() -> dict:
    fail: list[str] = []
    from api.rest_client import default_base_url

    base = default_base_url()
    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 18080)
    try:
        sock = socket.create_connection((host, port), timeout=3.0)
        sock.close()
        reachable = True
    except Exception as exc:
        reachable = False
        fail.append(f"KABUS_UNREACHABLE:{type(exc).__name__}")
    return {"ok": not fail, "fail": fail, "reachable": reachable, "base": base}


def check() -> dict:
    now = datetime.now(JST).strftime("%Y%m%d")
    if now != DAY:
        return _fail([f"WALL_DATE={now}"])
    cap = check_capture()
    kabu = check_kabu()
    fail: list[str] = []
    if not cap.get("ok"):
        fail.extend(["CAPTURE:" + x for x in cap.get("fail") or []])
    if not kabu.get("ok"):
        fail.extend(kabu.get("fail") or [])
    if not operational_validation_mode():
        fail.append("OPVAL_MODE_REQUIRED")
    if certification_mode():
        fail.append("OPVAL_CERTIFICATION_MODE_FORBIDDEN")
    if not OPVAL_SELECTOR.is_file():
        fail.append("OPVAL_SELECTOR_MISSING")
    os.environ[ENV_ACTIVATION_SELECTOR] = str(OPVAL_SELECTOR)
    os.environ[ENV_OPVAL_MODE] = "1"
    os.environ["TRADEBOT_TRADING_DATE"] = DAY
    os.environ.pop(ENV_CERT_MODE, None)
    os.environ.pop(ENV_REPLAY_PATH, None)
    os.environ.pop(ENV_ENABLED, None)
    os.environ.pop(ENV_ARM_FILE, None)
    try:
        sel = load_active_selector()
        man = load_activation_manifest(selector=sel)
        blocked = opval_startup_blocked_reason(sel, man)
        if blocked:
            fail.append(blocked)
        if str(sel.get("activation_id") or "") != OPVAL_ACTIVATION_ID:
            fail.append("OPVAL_IDENTITY_MISMATCH")
        formal = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
        if formal.get("activation_id") == OPVAL_ACTIVATION_ID:
            fail.append("FORMAL_SELECTOR_MUTATED")
        assertion = assert_exit_v2_primary_roles()
        if not assertion.ok or not assertion.ready:
            fail.append(assertion.reason or "PAPER_PRIMARY_NOT_READY")
        identity = assertion.identity
    except Exception as exc:
        fail.append(f"GATE:{type(exc).__name__}:{exc}")
        identity = {}
        assertion = None
    scl = "0/0/0"
    if identity.get("submit") or identity.get("cancel") or identity.get("live"):
        fail.append("SUBMIT_CANCEL_LIVE")
        scl = f"{identity.get('submit')}/{identity.get('cancel')}/{identity.get('live')}"
    return {
        "ok": not fail,
        "paper_start_allowed": not fail,
        "fail": fail,
        "paper_mode": "OPERATIONAL_VALIDATION_ONLY",
        "INVALID_FOR_STRATEGY_EVALUATION": True,
        "NOT_PROSPECTIVE_DAY1": True,
        "formal_paper_allowed": False,
        "prospective_allowed": False,
        "strategy_evaluation_allowed": False,
        "submit_cancel_live": scl,
        "capture": cap,
        "kabu": kabu,
        "activation_id": OPVAL_ACTIVATION_ID,
        "ready": bool(assertion.ready) if assertion is not None else False,
        "reason": "" if not fail else ",".join(fail[:12]),
    }


def main() -> int:
    body = check()
    print(json.dumps(body, indent=2, default=str))
    return 0 if body.get("paper_start_allowed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
