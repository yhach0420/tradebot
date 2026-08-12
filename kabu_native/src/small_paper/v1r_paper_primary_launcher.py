"""V1R Paper Primary production launcher - fail-closed, no PBv2 Primary fallback.

live mode starts the ACTUAL Paper runtime loop (Market Ingress V2 → LOCAL_MARKET_BUS
→ pilot_runner.run_live_dry_run / AM-PM daily runner). Stub heartbeat-only exit is forbidden.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from small_paper.v1r_exit_v2_activation_gate import (
    ASSERTION_FAIL,
    assert_exit_v2_primary_roles,
    format_startup_contract,
)
from small_paper.v1r_live_dual_lane import ENV_FLAG, live_primary_enabled
from small_paper.v1r_primary_activation_gate import heartbeat_identity_fields
from small_paper.v1r_dual_strategy_replay import run_dual_day

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[2]
REPO = NATIVE.parent
DAILY_RUNNER = NATIVE / "scripts" / "run_core10_dynamic40_am_pm_daily_runner.py"
LOCK_PATH = NATIVE / "runtime" / "v1r_primary_live.lock"


def _write_hb(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _acquire_single_primary_lock() -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.is_file():
        try:
            body = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            pid = int(body.get("pid") or 0)
            if pid > 0:
                # Windows: OpenProcess-style check via os.kill(pid, 0) may fail; use tasklist
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True,
                    text=True,
                )
                if str(pid) in (r.stdout or ""):
                    return False
        except Exception:
            pass
    LOCK_PATH.write_text(
        json.dumps({"pid": os.getpid(), "ts": datetime.now(JST).isoformat()}, indent=2),
        encoding="utf-8",
    )
    return True


def _release_lock() -> None:
    try:
        if LOCK_PATH.is_file():
            body = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            if int(body.get("pid") or 0) == os.getpid():
                LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def assert_only() -> int:
    """Preflight: role assertion only - must NOT start long-running runtime."""
    assertion = assert_exit_v2_primary_roles()
    print(assertion.startup_block, flush=True)
    if not assertion.ok:
        print(f"[V1R EXIT V2 PRIMARY] {ASSERTION_FAIL}: {assertion.reason}", flush=True)
        print("[V1R EXIT V2 PRIMARY] NO PAPER PRIMARY - FIXED600/PBv2 Primary fallback FORBIDDEN", flush=True)
        return 2
    print("[V1R EXIT V2] preflight assert-only PASS (no live runtime started)", flush=True)
    return 0


def _start_session_dir() -> Path:
    out = (
        NATIVE / "results" / "small_paper" / datetime.now(JST).strftime("%Y%m%d")
        / f"v1r_primary_{datetime.now(JST).strftime('%H%M%S')}"
    )
    out.mkdir(parents=True, exist_ok=True)
    return out


def _run_residency_live_loop(
    *,
    out: Path,
    residency_sec: float,
    hb_path: Path,
    assertion,
) -> int:
    """Healthcheck: stay resident on LOCAL_MARKET_BUS for residency_sec (actual consumer)."""
    import asyncio

    from small_paper.paper_market_bus_consumer import PaperMarketBusBridge
    from small_paper.v1r_live_dual_lane import get_dual_lane, reset_dual_lane_for_tests

    os.environ[ENV_FLAG] = "1"
    os.environ.setdefault("MARKET_INGRESS_V2", "1")
    session_out = out / "live_residency_session"
    session_out.mkdir(parents=True, exist_ok=True)
    reset_dual_lane_for_tests()
    dual = get_dual_lane(trace_dir=session_out)
    assert dual is not None

    print(
        f"[V1R EXIT V2] starting ACTUAL LOCAL_MARKET_BUS consumer residency_sec={residency_sec}",
        flush=True,
    )
    print(
        "Market Data Architecture:\nINGRESS_V2\nRuntime Market Source:\nLOCAL_MARKET_BUS\n"
        "submit/cancel/live:\n0/0/0",
        flush=True,
    )

    hb_count = 0
    push_n = 0
    state = {"stop": False}

    def _hb(note: str = "") -> None:
        nonlocal hb_count
        hb_count += 1
        fields = dual.heartbeat_fields()
        fields.update({
            "mode": "live_residency_test",
            "hb_seq": hb_count,
            "push_messages": push_n,
            "note": note,
            "runtime_pid": os.getpid(),
        })
        _write_hb(hb_path, heartbeat_identity_fields(
            current_anchor=None,
            next_anchor="09:05",
            open_n=int(fields.get("primary_open") or 0),
            pending_n=0,
            extra=fields,
        ))
        (session_out / "runtime_heartbeat.json").write_text(
            json.dumps(fields, indent=2, default=str), encoding="utf-8"
        )

    async def _loop() -> None:
        nonlocal push_n
        bridge = PaperMarketBusBridge(consumer_id="v1r_primary_residency", native_root=NATIVE)
        dual.stats.state = "WAITING_MARKET"
        _hb("connect")
        try:
            bridge.start()
            dual.stats.state = "RUNNING"
            _hb("bus_connected")
            print(f"[INGRESS_V2] PaperMarketBusBridge started health={bridge.health()}", flush=True)
        except Exception as exc:
            # Bus may be temporarily unavailable - still residency-wait while retrying
            print(f"[V1R EXIT V2] bus start deferred: {type(exc).__name__}:{exc}", flush=True)
            bridge = None

        start = time.monotonic()
        last_hb = start
        while (time.monotonic() - start) < float(residency_sec) and not state["stop"]:
            if time.monotonic() - last_hb >= 5.0:
                _hb("tick")
                last_hb = time.monotonic()
            if bridge is None:
                await asyncio.sleep(1.0)
                try:
                    bridge = PaperMarketBusBridge(consumer_id="v1r_primary_residency", native_root=NATIVE)
                    bridge.start()
                    dual.stats.state = "RUNNING"
                    _hb("bus_connected_retry")
                except Exception:
                    bridge = None
                continue
            try:
                # Non-blocking-ish: wait briefly for one message
                got = False
                async for payload in bridge.iter_messages(recv_poll_sec=1.0):
                    if payload.get("tick_kind") == "recv_timeout" or payload.get("_recv_timeout"):
                        if (time.monotonic() - start) >= float(residency_sec):
                            break
                        if time.monotonic() - last_hb >= 5.0:
                            _hb("waiting_market")
                            last_hb = time.monotonic()
                        continue
                    got = True
                    push_n += 1
                    seq = int(payload.get("sequence") or payload.get("Sequence") or push_n)
                    dual.on_push_meta(
                        sequence=seq,
                        push_at=str(payload.get("CurrentPriceTime") or datetime.now(JST).isoformat()),
                    )
                    sym = str(payload.get("Symbol") or "").replace(".T", "")
                    if sym:
                        dual.on_tick(symbol=sym, payload=payload, event_t=time.time())
                    try:
                        bridge.ack_processed(payload)
                    except Exception:
                        pass
                    if (time.monotonic() - start) >= float(residency_sec):
                        break
                    if time.monotonic() - last_hb >= 5.0:
                        _hb("push")
                        last_hb = time.monotonic()
                if not got:
                    await asyncio.sleep(0.2)
            except Exception as exc:
                print(f"[V1R EXIT V2] bus iter error: {type(exc).__name__}:{exc}", flush=True)
                await asyncio.sleep(1.0)
        dual.stats.state = "STOPPING"
        _hb("stopping")
        try:
            if bridge is not None:
                bridge.stop()
        except Exception:
            pass
        dual.stats.state = "STOPPED"
        _hb("stopped")

    t0 = time.time()
    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        state["stop"] = True
        print("[V1R EXIT V2] KeyboardInterrupt - graceful stop", flush=True)
    elapsed = time.time() - t0
    snap = dual.snapshot()
    summary = {
        "mode": "live_residency_test",
        "elapsed_sec": elapsed,
        "requested_sec": residency_sec,
        "heartbeat_count": hb_count,
        "push_messages": push_n,
        "dual_lane": snap,
        "submit_cancel_live": "0/0/0",
        "bus": "PaperMarketBusBridge",
    }
    (out / "live_residency_result.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"[V1R EXIT V2] residency finished elapsed={elapsed:.1f}s pushes={push_n} hb={hb_count}", flush=True)
    if elapsed < max(20.0, float(residency_sec) * 0.7) or hb_count < 2:
        print("[V1R EXIT V2] V1R_PRIMARY_PREMATURE_EXIT during residency test", flush=True)
        return 4
    return 0


def _run_daily_live(out: Path, hb_path: Path, assertion) -> int:
    """Production live: AM→lunch→PM daily runner on LOCAL_MARKET_BUS."""
    os.environ[ENV_FLAG] = "1"
    os.environ.setdefault("MARKET_INGRESS_V2", "1")
    os.environ.setdefault("KABU_PAPER_RUNTIME", "1")
    if not DAILY_RUNNER.is_file():
        print(f"[V1R EXIT V2] NO PAPER PRIMARY - missing daily runner {DAILY_RUNNER}", flush=True)
        return 2

    # Fail-closed: V1R-native ENTRY SoT must boot (no PBv2 Primary fallback)
    try:
        from small_paper.v1r_native_entry_live import (
            boot_v1r_native_entry,
            resolve_day_fixed_am_runtime_universe,
            set_native_entry,
        )

        day = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d")
        resolved = resolve_day_fixed_am_runtime_universe(native_root=NATIVE, trading_date=day)
        (out / "native_universe_resolve.json").write_text(
            json.dumps(resolved, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        if not resolved.get("ok"):
            print(
                f"[V1R EXIT V2] NO PAPER PRIMARY - day-fixed universe unresolved: "
                f"{resolved.get('reason')}",
                flush=True,
            )
            print("[V1R EXIT V2] PBv2/classic Primary fallback FORBIDDEN", flush=True)
            return 2
        native = boot_v1r_native_entry(
            universe=list(resolved.get("symbols") or []),
            trace_dir=out / "native_entry",
            universe_source=str(resolved.get("source") or ""),
        )
        set_native_entry(native)
        (out / "native_entry_boot.json").write_text(
            json.dumps(native.snapshot(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        if not native.ready:
            print(
                f"[V1R EXIT V2] NO PAPER PRIMARY - native ENTRY boot failed: {native.fail_reason}",
                flush=True,
            )
            print("[V1R EXIT V2] PBv2/classic Primary fallback FORBIDDEN", flush=True)
            return 2
        print(
            "[V1R EXIT V2] Native ENTRY SoT READY - "
            f"universe={len(native.universe)} "
            f"entry={native.identity()['entry_sha'][:12]}… "
            f"anchor={native.identity()['anchor_sha'][:12]}… "
            f"model={native.identity()['model_sha'][:12]}…",
            flush=True,
        )
    except Exception as exc:
        print(f"[V1R EXIT V2] NO PAPER PRIMARY - native ENTRY exception: {exc}", flush=True)
        print("[V1R EXIT V2] PBv2/classic Primary fallback FORBIDDEN", flush=True)
        return 2

    cmd = [
        sys.executable,
        str(DAILY_RUNNER),
        "--universe-mode",
        "core10-dynamic40-price-risk-filter-shadow",
        "--enable-intraday-refresh",
        "--exit-policy-shadow",
        "trailing-mfe",
    ]
    (out / "daily_runner_cmd.json").write_text(
        json.dumps({"cmd": cmd, "env_flag": ENV_FLAG, "cwd": str(REPO)}, indent=2),
        encoding="utf-8",
    )
    print("[V1R EXIT V2] LIVE gate PASS - starting ACTUAL AM/PM Paper runtime loop", flush=True)
    print("[V1R EXIT V2] Market data owner=MARKET_INGRESS_V2; Paper source=LOCAL_MARKET_BUS", flush=True)
    print("[V1R EXIT V2] Primary=Arch E; Control=FIXED600 SHADOW; waiting market / operator stop", flush=True)
    _write_hb(hb_path, heartbeat_identity_fields(
        current_anchor=None,
        next_anchor="09:05",
        open_n=0,
        pending_n=0,
        extra={
            "mode": "live",
            "runtime": "am_pm_daily_runner",
            "state": "WAITING_MARKET",
            "primary_exit": "ARCH_E_V2",
            "env": ENV_FLAG,
        },
    ))
    env = os.environ.copy()
    env[ENV_FLAG] = "1"
    env["PYTHONPATH"] = f"{NATIVE / 'src'};{REPO}" + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), env=env)
        code = int(proc.returncode)
    except KeyboardInterrupt:
        print("[V1R EXIT V2] KeyboardInterrupt - graceful stop", flush=True)
        code = 0
    elapsed = time.time() - t0
    _write_hb(hb_path, heartbeat_identity_fields(
        current_anchor="15:00",
        next_anchor=None,
        open_n=0,
        pending_n=0,
        extra={"mode": "live", "elapsed_sec": elapsed, "daily_exit_code": code, "state": "STOPPED"},
    ))
    (out / "daily_runner_result.json").write_text(
        json.dumps({"exit_code": code, "elapsed_sec": elapsed}, indent=2), encoding="utf-8"
    )
    # Premature: daily runner returned in under 30s on a normal live day start
    if code == 0 and elapsed < 30.0:
        print(
            f"[V1R EXIT V2] V1R_PRIMARY_PREMATURE_EXIT elapsed={elapsed:.1f}s "
            "(daily runner returned too fast)",
            flush=True,
        )
        return 4
    return code


def launch_primary(
    *,
    mode: str = "live",
    replay_day: Optional[str] = None,
    session_dir: Optional[Path] = None,
    residency_sec: Optional[float] = None,
) -> int:
    assertion = assert_exit_v2_primary_roles()
    print(assertion.startup_block, flush=True)
    if not assertion.ok:
        print(f"[V1R EXIT V2 PRIMARY] {ASSERTION_FAIL}: {assertion.reason}", flush=True)
        print("[V1R EXIT V2 PRIMARY] NO PAPER PRIMARY - FIXED600/PBv2 Primary fallback FORBIDDEN", flush=True)
        return 2

    out = session_dir or _start_session_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "startup_contract.txt").write_text(assertion.startup_block, encoding="utf-8")
    (out / "role_assertion.json").write_text(
        json.dumps(assertion.to_dict(), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    hb_path = out / "heartbeat.jsonl"
    _write_hb(hb_path, heartbeat_identity_fields(
        current_anchor=None,
        next_anchor="09:05",
        open_n=0,
        pending_n=0,
        extra={
            "mode": mode,
            "ready": True,
            "primary_exit": "ARCH_E_V2",
            "control": "FIXED600_SHADOW_CONTROL",
            "guard_id": assertion.identity.get("guard_id"),
            "continuation_id": assertion.identity.get("continuation_id"),
            "strategy_sha": assertion.identity.get("strategy_sha"),
        },
    ))

    if mode == "offline_replay":
        day = replay_day or "20260810"
        print(f"[V1R EXIT V2] offline dual replay day={day} (no broker / no Discord)", flush=True)
        res = run_dual_day(day, label="production_offline_dual")
        slim = {k: v for k, v in res.items() if k not in ("primary", "control")}
        if res.get("ok"):
            slim["primary_summary"] = res["primary"]["summary"]
            slim["control_summary"] = res["control"]["summary"]
            slim["comparison"] = res["comparison"]
        (out / "offline_dual_replay_result.json").write_text(
            json.dumps(slim, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        return 0 if res.get("ok") else 3

    # live
    if not _acquire_single_primary_lock():
        print("[V1R EXIT V2] NO PAPER PRIMARY - dual Primary lock held (another live process)", flush=True)
        return 2
    try:
        (out / "primary_role_bound.json").write_text(json.dumps({
            "primary": "V1R_EXIT_V2_ARCH_E_PAPER_PRIMARY",
            "control": "FIXED600_SHADOW_CONTROL",
            "pbv2_primary_fallback": False,
            "fixed600_primary_fallback": False,
            "classic_trailing_mfe_as_primary": False,
            "submit_cancel_live": "0/0/0",
            "session_dir": str(out),
            "runtime": "pilot_runner_LOCAL_MARKET_BUS" if residency_sec else "am_pm_daily_runner",
            "identity": assertion.identity,
        }, indent=2, default=str), encoding="utf-8")

        if residency_sec is not None and float(residency_sec) > 0:
            return _run_residency_live_loop(
                out=out, residency_sec=float(residency_sec), hb_path=hb_path, assertion=assertion
            )
        return _run_daily_live(out, hb_path, assertion)
    finally:
        _release_lock()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="V1R Paper Primary launcher (fail-closed)")
    p.add_argument("--mode", choices=["live", "offline_replay"], default="live")
    p.add_argument("--assert-only", action="store_true", help="Preflight role assert only (no live loop)")
    p.add_argument("--replay-day", default=None)
    p.add_argument("--session-dir", default=None)
    p.add_argument(
        "--residency-sec",
        type=float,
        default=None,
        help="Healthcheck: run actual live bus loop for N seconds then stop",
    )
    args = p.parse_args(argv)
    if args.assert_only:
        return assert_only()
    return launch_primary(
        mode=args.mode,
        replay_day=args.replay_day,
        session_dir=Path(args.session_dir) if args.session_dir else None,
        residency_sec=args.residency_sec,
    )


if __name__ == "__main__":
    sys.exit(main())
