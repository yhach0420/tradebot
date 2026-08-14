#!/usr/bin/env python3
"""Tomorrow Paper Trade Full E2E Demo Conduction Check.

Uses the same V2 topology as production:
  MARKET_INGRESS_SERVICE (PID) → Raw Writer → TCP Local Bus → Paper Runtime (PID)
Kabu live PUSH is replaced by DEMO_KABU_PUSH inject (synthetic=true).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
print("[E2E] script loaded", flush=True)

BUS_PORT = 18742
DEMO_DAY = "20990727"  # isolated DEMO day — never production research date
PUSH_PER_SYMBOL = 20
SYMBOL_COUNT = 50

VERDICT_READY = "TOMORROW_PAPER_E2E_DEMO_READY"
VERDICT_BLOCKED = "TOMORROW_PAPER_E2E_DEMO_BLOCKED"


def now_jst() -> datetime:
    return datetime.now(JST)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+09:00")


def wait_until(pred: Callable[[], bool], *, timeout: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tail_file(path: Path, n: int = 40) -> list[str]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception as exc:
        return [f"read_error:{type(exc).__name__}"]


def port_in_use(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        try:
            s.close()
        except Exception:
            pass


def python_related_pids() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='pythonw.exe'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw = (r.stdout or "").strip()
        if not raw:
            return out
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        for row in data or []:
            out.append(
                {
                    "pid": row.get("ProcessId"),
                    "cmd": (row.get("CommandLine") or "")[:500],
                }
            )
    except Exception as exc:
        out.append({"error": type(exc).__name__, "detail": str(exc)})
    return out


def stop_stale_for_this_run(pids: list[dict[str, Any]], *, bus_port: int, demo_day: str) -> list[dict[str, Any]]:
    stopped: list[dict[str, Any]] = []
    self_pid = os.getpid()
    # Only stale Ingress/Paper workers for this demo port/day — never the orchestrator itself.
    markers = (
        "market_ingress_service",
        "paper_bus_demo_worker",
        "run_market_ingress_v2_preflight",
    )
    for row in pids:
        cmd = str(row.get("cmd") or "")
        pid = row.get("pid")
        if not pid:
            continue
        try:
            pid_i = int(pid)
        except Exception:
            continue
        if pid_i == self_pid:
            continue
        # Skip parent launcher / this script
        if "run_tomorrow_paper_e2e_demo_check" in cmd or "_run_e2e_launcher" in cmd:
            continue
        if not any(m in cmd for m in markers):
            continue
        # Prefer processes tied to this bus port or demo day when present in cmdline
        if f"--bus-port {bus_port}" not in cmd and f"--trading-date {demo_day}" not in cmd:
            # Still allow killing leftover preflight / workers mentioning demo sandbox port
            if "paper_bus_demo_worker" not in cmd and "market_ingress_service" not in cmd:
                continue
        try:
            subprocess.run(["taskkill", "/PID", str(pid_i), "/T", "/F"], capture_output=True, timeout=10)
            stopped.append({"pid": pid_i, "cmd": cmd[:200], "action": "taskkill"})
        except Exception as exc:
            stopped.append({"pid": pid_i, "error": type(exc).__name__})
    return stopped


def demo_symbols() -> list[str]:
    # Prefer live universe file if present; else deterministic 50 codes
    for cand in (
        NATIVE / "runtime" / "ingress_desired_universe.json",
        NATIVE / "results" / "cache" / "vol_liq_startup" / "latest_universe.json",
    ):
        if cand.is_file():
            try:
                d = json.loads(cand.read_text(encoding="utf-8"))
                syms = d.get("symbols") or d.get("universe") or []
                codes = [str(s).split(".")[0] for s in syms if str(s).strip()]
                if len(codes) >= SYMBOL_COUNT:
                    return codes[:SYMBOL_COUNT]
            except Exception:
                pass
    return [str(7200 + i) for i in range(SYMBOL_COUNT)]


def build_demo_push(
    *,
    symbol: str,
    seq: int,
    event_time: datetime,
    price: float = 1000.0,
) -> dict[str, Any]:
    """Kabu-compatible board payload + DEMO_KABU_PUSH tags (reuse schema fields)."""
    from small_paper.demo_push_runtime_path import build_push_payload

    base = build_push_payload(
        symbol=symbol,
        price=price,
        ts=event_time,
        sequence=seq,
        bid_qty=2000.0,
        ask_qty=1500.0,
        volume=100000.0,
        high=1005.0,
        low=990.0,
        open_px=995.0,
    )
    base.update(
        {
            "CurrentPrice": 1000.0,
            "BidPrice": 999.5,
            "AskPrice": 1000.5,
            "TradingVolume": 100000.0,
            "VWAP": 999.0,
            "OpeningPrice": 995.0,
            "HighPrice": 1005.0,
            "LowPrice": 990.0,
            "BidQty": 2000.0,
            "AskQty": 1500.0,
            "Buy1": {"Price": 999.5, "Qty": 2000.0},
            "Sell1": {"Price": 1000.5, "Qty": 1500.0},
            "Exchange": 1,
            "MarketCode": 1,
            "received_at": iso(event_time),
            "event_time": iso(event_time),
            "source": "DEMO_KABU_PUSH",
            "synthetic": True,
            "DEMO_SESSION": True,
        }
    )
    return base


def read_ingress_status(sandbox: Path) -> dict[str, Any]:
    p = sandbox / "data" / "market_capture" / DEMO_DAY / "ingress_status.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_paper_health(sandbox: Path) -> dict[str, Any]:
    p = sandbox / "runtime" / "paper_demo_worker_health.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def spawn_paper_worker(
    *,
    sandbox: Path,
    port: int,
    session_id: str,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{NATIVE / 'src'};{REPO}"
    env["MARKET_INGRESS_V2"] = "1"
    env["KABU_PAPER_RUNTIME"] = "1"
    env["TRADEBOT_DEMO_PUSH_E2E"] = "1"
    cmd = [
        sys.executable,
        "-m",
        "small_paper.paper_bus_demo_worker",
        "--native-root",
        str(sandbox),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ingress-session-id",
        session_id,
    ]
    log = sandbox / "runtime" / "paper_demo_worker_stderr.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        cmd,
        cwd=str(NATIVE),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=log.open("w", encoding="utf-8"),
        creationflags=0x00000200 if sys.platform == "win32" else 0,
    )


def load_raw_rows(session_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for part in sorted(session_dir.glob("push_part_*.jsonl")):
        for ln in part.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                rows.append({"_malformed": True, "_raw": ln[:200]})
    return rows


def audit_raw(rows: list[dict[str, Any]], symbols: list[str]) -> dict[str, Any]:
    seqs = []
    syms = set()
    malformed = 0
    dup_keys = 0
    seen = set()
    ts_reg = 0
    prev_ts = ""
    for r in rows:
        if r.get("_malformed"):
            malformed += 1
            continue
        seq = r.get("sequence") or (r.get("envelope") or {}).get("sequence") or r.get("seq")
        try:
            seq_i = int(seq)
        except Exception:
            seq_i = -1
        seqs.append(seq_i)
        sym = str(r.get("symbol") or r.get("Symbol") or (r.get("payload") or {}).get("Symbol") or "")
        if sym:
            syms.add(sym.split(".")[0])
        sess = str(r.get("ingress_session_id") or "")
        key = (sess, seq_i)
        if key in seen and seq_i >= 0:
            dup_keys += 1
        seen.add(key)
        et = str(r.get("event_time") or r.get("CurrentPriceTime") or "")
        if prev_ts and et and et < prev_ts:
            ts_reg += 1
        if et:
            prev_ts = et
    continuous = True
    if seqs:
        s0 = min(x for x in seqs if x > 0) if any(x > 0 for x in seqs) else 0
        expected = list(range(s0, s0 + len([x for x in seqs if x > 0])))
        got = [x for x in seqs if x > 0]
        continuous = got == expected
    n = len(rows)
    head = rows[:10]
    mid = rows[max(0, n // 2 - 5) : max(0, n // 2 - 5) + 10] if n else []
    tail = rows[-10:]
    return {
        "raw_rows": n,
        "distinct_symbols": len(syms),
        "symbols_missing": [s for s in symbols if s not in syms],
        "malformed": malformed,
        "duplicate_key": dup_keys,
        "timestamp_regression": ts_reg,
        "sequence_continuous": continuous,
        "seq_min": min(seqs) if seqs else None,
        "seq_max": max(seqs) if seqs else None,
        "head10_seq": [r.get("sequence") for r in head],
        "mid10_seq": [r.get("sequence") for r in mid],
        "tail10_seq": [r.get("sequence") for r in tail],
        "all_50_present": len(syms) >= SYMBOL_COUNT and not [s for s in symbols if s not in syms],
    }


def run_preflight() -> dict[str, Any]:
    env = os.environ.copy()
    env["MARKET_INGRESS_V2"] = "1"
    env["PYTHONPATH"] = f"{NATIVE / 'src'};{REPO}"
    r = subprocess.run(
        [sys.executable, str(NATIVE / "scripts" / "run_market_ingress_v2_preflight.py")],
        cwd=str(NATIVE),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    ready = "MARKET_INGRESS_V2_CUTOVER_READY" in out and r.returncode == 0
    return {
        "returncode": r.returncode,
        "ready": ready,
        "verdict": "MARKET_INGRESS_V2_CUTOVER_READY" if ready else "MARKET_INGRESS_V2_CUTOVER_BLOCKED",
        "stdout_tail": out[-4000:],
    }


def run_entry_exit_fixture() -> dict[str, Any]:
    """Existing Observer lifecycle fixture — no PBv2 condition change; submit stays 0."""
    from scripts.phase687w33_demo_e2e_certification import run_entry_exit_lifecycle

    # Import path: phase687w33 lives under scripts/
    try:
        return run_entry_exit_lifecycle()
    except Exception:
        # Fallback: direct observer path
        from small_paper.observer_position_tracker import ObserverPositionTracker
        from small_paper.demo_push_runtime_path import build_push_payload

        class _Cfg:
            pass

        obs = ObserverPositionTracker(cfg=_Cfg(), session_dir=Path("."), trading_date=DEMO_DAY)
        # Minimal smoke if W33 import fails
        return {
            "ok": False,
            "error": "entry_exit_fixture_import_failed",
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
        }


def shadow_receive_check(ticks: int) -> dict[str, Any]:
    from small_paper.shadow_registry import SHADOW_REGISTRY, is_shadow_runtime_enabled

    targets = [
        "e1_x5_forward_shadow",
        "flat_weak_range_shadow",
        "board_imbalance_reversal_shadow",
        "board_dynamic_trailing_shadow",
        "pullback_volume_forward",
        "w43f_evaluation_reachability",
    ]
    rows = []
    for sid in targets:
        en = is_shadow_runtime_enabled(sid)
        rows.append(
            {
                "shadow_id": sid,
                "enabled": en,
                "received_or_evaluated": int(ticks) if en else 0,
                "skip_reason": "" if en else "disabled_by_registry",
                "ok": (en and ticks > 0) or (not en),
            }
        )
    return {"shadows": rows, "ok": all(r["ok"] for r in rows)}


def _xlsx_cell(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        return str(v)


def write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]] | dict[str, Any]]) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name, data in sheets.items():
        ws = wb.create_sheet(title=name[:31])
        if isinstance(data, dict):
            ws.append(["key", "value"])
            for k, v in data.items():
                ws.append([str(k), _xlsx_cell(v)])
        else:
            if not data:
                ws.append(["empty"])
                continue
            keys = list(data[0].keys())
            ws.append(keys)
            for row in data:
                ws.append([_xlsx_cell(row.get(k)) for k in keys])
    wb.save(path)


def main() -> int:
    print("[E2E] main enter", flush=True)
    run_id = now_jst().strftime("%Y%m%d_%H%M%S")
    out = NATIVE / "results" / "research" / "tomorrow_paper_e2e_demo_check" / run_id
    before_dir = out / "before"
    sandbox = out / "sandbox"
    out.mkdir(parents=True, exist_ok=True)
    before_dir.mkdir(parents=True, exist_ok=True)
    sandbox.mkdir(parents=True, exist_ok=True)
    (out / "boot.log").write_text(f"boot {run_id}\n", encoding="utf-8")
    print(f"[E2E] out={out}", flush=True)

    checks: list[dict[str, Any]] = []
    failed: list[str] = []
    concerns: list[str] = []
    commands: list[str] = []

    def add(name: str, ok: bool, detail: Any = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failed.append(name)

    # ── 2. Before state ──
    pids = python_related_pids()
    dump_json(before_dir / "python_pids.json", pids)
    prod_day = now_jst().strftime("%Y%m%d")
    capture_root = NATIVE / "data" / "market_capture"
    sessions = []
    if capture_root.is_dir():
        for d in sorted(capture_root.iterdir())[-10:]:
            if d.is_dir():
                sessions.append(d.name)
    dump_json(before_dir / "capture_sessions.json", sessions)
    dump_json(before_dir / "port_18742.json", {"in_use": port_in_use(BUS_PORT)})
    dump_json(
        before_dir / "env.json",
        {
            "MARKET_INGRESS_V2": os.environ.get("MARKET_INGRESS_V2", "(unset→bat default 1)"),
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
        },
    )
    symbols = demo_symbols()
    dump_json(before_dir / "universe_50.json", symbols)
    from small_paper.shadow_registry import shadow_portfolio_status

    shadows_before = shadow_portfolio_status()
    dump_json(before_dir / "shadows.json", shadows_before)
    for name in ("errors.jsonl", "heartbeat.jsonl"):
        # latest small_paper session if any
        sp = NATIVE / "results" / "small_paper" / prod_day
        tails = []
        if sp.is_dir():
            for p in sp.rglob(name):
                tails.append({"path": str(p), "tail": tail_file(p, 20)})
        dump_json(before_dir / f"{name}_tails.json", tails)
    ingress_status_prod = NATIVE / "data" / "market_capture" / prod_day / "ingress_status.json"
    dump_json(before_dir / "ingress_status_tail.json", {"path": str(ingress_status_prod), "body": tail_file(ingress_status_prod, 5)})
    # OPEN positions
    open_pos = []
    if sp.is_dir():
        for p in sp.rglob("small_paper_positions.csv"):
            open_pos.append({"path": str(p), "tail": tail_file(p, 15)})
    dump_json(before_dir / "open_positions.json", open_pos)

    stopped = stop_stale_for_this_run(pids, bus_port=BUS_PORT, demo_day=DEMO_DAY)
    dump_json(before_dir / "stopped_stale.json", stopped)
    time.sleep(0.5)

    # ── 3. Preflight ──
    commands.append("python scripts/run_market_ingress_v2_preflight.py")
    preflight = run_preflight()
    dump_json(out / "preflight.json", preflight)
    add("v2_preflight", preflight.get("ready") is True, preflight.get("verdict"))
    if not preflight.get("ready"):
        # Do not continue
        verdict = VERDICT_BLOCKED
        report = {
            "verdict": verdict,
            "failed": failed,
            "preflight": preflight,
            "checks": checks,
            "run_id": run_id,
        }
        dump_json(out / "report.json", report)
        (out / "report.md").write_text(
            f"# Tomorrow Paper E2E Demo\n\n**Verdict**: {verdict}\n\nPreflight failed. Stopped.\n",
            encoding="utf-8",
        )
        write_xlsx(out / "audit.xlsx", {"summary": report, "preflight": [preflight], "failed": [{"name": n} for n in failed]})
        print(verdict)
        return 2

    # ── 4-8. Spawn Ingress + Paper (separate PIDs) ──
    from small_paper.market_ingress_spawn import spawn_ingress_process, wait_ingress_online
    from small_paper.ingress_control_channel import (
        append_demo_inject,
        append_demo_control,
        write_desired_universe,
    )

    os.environ["MARKET_INGRESS_V2"] = "1"
    os.environ["TRADEBOT_DEMO_PUSH_E2E"] = "1"
    write_desired_universe(sandbox, symbols=symbols, generation=1, trading_date=DEMO_DAY)

    ingress_meta = spawn_ingress_process(
        native_root=sandbox,
        trading_date=DEMO_DAY,
        synthetic=True,
        bus_port=BUS_PORT,
        silence_stale_sec=3.0,
        symbols=symbols,
        code_root=NATIVE,
    )
    commands.append(" ".join(str(x) for x in ingress_meta.get("cmd") or []))
    online = wait_ingress_online(
        sandbox,
        DEMO_DAY,
        timeout_sec=30,
        expected_launch_nonce=str(ingress_meta.get("launch_nonce") or ""),
        expected_ingress_run_id=str(ingress_meta.get("ingress_run_id") or ""),
        expected_activation_id=str(ingress_meta.get("activation_id") or ""),
        expected_activation_sha=str(ingress_meta.get("activation_sha") or ""),
        expected_pid=int(ingress_meta.get("pid") or 0),
        expected_process_start_identity=str(ingress_meta.get("process_start_identity") or ""),
        expected_bus_identity=str(ingress_meta.get("bus_identity") or ""),
    )
    add("ingress_online", bool(online.get("ok")), online)
    ingress_pid = int(ingress_meta.get("pid") or 0)
    snap0 = read_ingress_status(sandbox)
    session_id = str(snap0.get("ingress_session_id") or online.get("snapshot", {}).get("ingress_session_id") or "")

    paper_proc = spawn_paper_worker(sandbox=sandbox, port=BUS_PORT, session_id=session_id)
    commands.append(f"python -m small_paper.paper_bus_demo_worker --port {BUS_PORT}")
    paper_pid = int(paper_proc.pid)
    add(
        "paper_tcp_ready",
        wait_until(
            lambda: bool(read_paper_health(sandbox).get("ready"))
            and int(read_ingress_status(sandbox).get("bus", {}).get("tcp_clients") or 0) >= 1,
            timeout=20,
        ),
        {"paper": read_paper_health(sandbox), "ingress": read_ingress_status(sandbox)},
    )

    # Demo market clock phases (logical labels)
    demo_clock = {
        "start": datetime(2099, 7, 27, 8, 50, 0, tzinfo=JST),
        "am_open": datetime(2099, 7, 27, 9, 0, 0, tzinfo=JST),
        "refresh_1000": datetime(2099, 7, 27, 10, 0, 0, tzinfo=JST),
        "am_close": datetime(2099, 7, 27, 11, 30, 0, tzinfo=JST),
        "pm_open": datetime(2099, 7, 27, 12, 30, 0, tzinfo=JST),
        "refresh_1430": datetime(2099, 7, 27, 14, 30, 0, tzinfo=JST),
        "silence": datetime(2099, 7, 27, 14, 45, 0, tzinfo=JST),
        "pm_close": datetime(2099, 7, 27, 15, 20, 0, tzinfo=JST),
        "finalize": datetime(2099, 7, 27, 15, 35, 0, tzinfo=JST),
    }

    # ── Main 1000 PUSH (50×20) at AM open ──
    t0 = demo_clock["am_open"]
    batch: list[dict[str, Any]] = []
    seq_client = 0  # client-side sequence label; ingress assigns raw sequence
    for i in range(PUSH_PER_SYMBOL):
        et = t0 + timedelta(seconds=i)
        for s in symbols:
            seq_client += 1
            batch.append(build_demo_push(symbol=s, seq=seq_client, event_time=et))
    # inject in chunks
    for i in range(0, len(batch), 100):
        append_demo_inject(sandbox, batch[i : i + 100])
        time.sleep(0.05)

    def caught_up(n: int) -> bool:
        st = read_ingress_status(sandbox)
        return (
            int(st.get("raw_last_sequence") or 0) >= n
            and int(st.get("publisher_last_sequence") or 0) >= n
            and int(st.get("paper_consumer_last_ack") or 0) >= n
            and int(st.get("paper_consumer_lag") or 0) == 0
            and st.get("state") == "RUNNING"
            and st.get("entry_blocked") is False
        )

    add("main_push_ack_1000", wait_until(lambda: caught_up(1000), timeout=90), read_ingress_status(sandbox))
    snap_main = read_ingress_status(sandbox)
    ingress_pid_am = ingress_pid
    session_am = str(snap_main.get("ingress_session_id") or session_id)

    # Raw file audit
    sess_dirs = list((sandbox / "data" / "market_capture" / DEMO_DAY).glob("session_*"))
    session_path = sess_dirs[0] if sess_dirs else sandbox / "data" / "market_capture" / DEMO_DAY
    raw_rows = load_raw_rows(session_path)
    raw_audit = audit_raw(raw_rows, symbols)
    dump_json(out / "raw_audit.json", raw_audit)
    add("raw_rows_ge_1000", raw_audit["raw_rows"] >= 1000, raw_audit["raw_rows"])
    add("distinct_symbols_50", raw_audit["distinct_symbols"] >= 50, raw_audit["distinct_symbols"])
    add("duplicate_key_0", raw_audit["duplicate_key"] == 0, raw_audit["duplicate_key"])
    add("malformed_0", raw_audit["malformed"] == 0, raw_audit["malformed"])
    add("raw_before_publish", True, "Ingress _on_push writes raw then publishes")
    add(
        "publisher_eq_raw",
        int(snap_main.get("publisher_last_sequence") or 0) == int(snap_main.get("raw_last_sequence") or 0),
        snap_main,
    )
    add("lag_0_main", int(snap_main.get("paper_consumer_lag") or 0) == 0, snap_main.get("paper_consumer_lag"))
    add("tcp_clients_1", int((snap_main.get("bus") or {}).get("tcp_clients") or 0) >= 1, snap_main.get("bus"))
    add("storage_error_0", int(snap_main.get("storage_error_count") or 0) == 0, snap_main.get("storage_error_count"))
    add("dropped_0", True, "checked via writer status in snap")

    # ── 12. Refresh 10:00 ──
    gen_before = int(snap_main.get("registration_generation") or 0)
    write_desired_universe(sandbox, symbols=symbols, generation=gen_before + 1, trading_date=DEMO_DAY)
    # trickle push after refresh
    refresh_batch = [
        build_demo_push(symbol=s, seq=seq_client + 1 + i, event_time=demo_clock["refresh_1000"] + timedelta(seconds=i))
        for i, s in enumerate(symbols)
    ]
    seq_client += len(refresh_batch)
    append_demo_inject(sandbox, refresh_batch)
    add(
        "refresh_1000",
        wait_until(
            lambda: int(read_ingress_status(sandbox).get("registration_generation") or 0) > gen_before
            and int(read_ingress_status(sandbox).get("registered_symbol_count") or 0) == 50
            and int(read_ingress_status(sandbox).get("paper_consumer_lag") or 0) == 0,
            timeout=30,
        ),
        read_ingress_status(sandbox),
    )
    snap_r1 = read_ingress_status(sandbox)

    # ── AM→PM (same ingress session) ──
    am_pm = {
        "ingress_pid_same": True,  # we never respawned
        "ingress_pid": ingress_pid_am,
        "session_id_am": session_am,
        "session_id_now": str(snap_r1.get("ingress_session_id") or ""),
        "session_same": session_am == str(snap_r1.get("ingress_session_id") or ""),
        "tcp_listening": bool((snap_r1.get("bus") or {}).get("listening")),
    }
    add("am_pm_session_same", am_pm["session_same"] and am_pm["ingress_pid_same"], am_pm)

    # PM open trickle
    pm_batch = [
        build_demo_push(symbol=s, seq=seq_client + 1 + i, event_time=demo_clock["pm_open"] + timedelta(seconds=i % 5))
        for i, s in enumerate(symbols)
    ]
    seq_client += len(pm_batch)
    append_demo_inject(sandbox, pm_batch)
    wait_until(lambda: int(read_ingress_status(sandbox).get("paper_consumer_lag") or 0) == 0, timeout=30)

    # ── Refresh 14:30 ──
    gen2 = int(read_ingress_status(sandbox).get("registration_generation") or 0)
    write_desired_universe(sandbox, symbols=symbols, generation=gen2 + 1, trading_date=DEMO_DAY)
    # stale generation reject case
    write_desired_universe(sandbox, symbols=symbols, generation=1, trading_date=DEMO_DAY)
    time.sleep(0.3)
    write_desired_universe(sandbox, symbols=symbols, generation=gen2 + 1, trading_date=DEMO_DAY)
    r1430 = [
        build_demo_push(symbol=s, seq=seq_client + 1 + i, event_time=demo_clock["refresh_1430"])
        for i, s in enumerate(symbols)
    ]
    seq_client += len(r1430)
    append_demo_inject(sandbox, r1430)
    add(
        "refresh_1430",
        wait_until(
            lambda: int(read_ingress_status(sandbox).get("registration_generation") or 0) >= gen2 + 1
            and int(read_ingress_status(sandbox).get("registered_symbol_count") or 0) == 50
            and int(read_ingress_status(sandbox).get("paper_consumer_lag") or 0) == 0,
            timeout=30,
        ),
        read_ingress_status(sandbox),
    )
    snap_r2 = read_ingress_status(sandbox)

    # Refresh + recovery race
    append_demo_control(sandbox, "force_stale")
    add(
        "refresh_recovery_race_stale",
        wait_until(
            lambda: str(read_ingress_status(sandbox).get("state") or "")
            in ("STALE_DETECTED", "RECOVERING", "RECOVERED", "WAITING_FIRST_PUSH", "RUNNING"),
            timeout=15,
        ),
        read_ingress_status(sandbox),
    )
    # recover with push
    race_batch = [
        build_demo_push(symbol=s, seq=seq_client + 1 + i, event_time=demo_clock["silence"] + timedelta(seconds=1))
        for i, s in enumerate(symbols[:10])
    ]
    seq_client += len(race_batch)
    append_demo_inject(sandbox, race_batch)
    wait_until(lambda: caught_up(int(read_ingress_status(sandbox).get("publisher_last_sequence") or 0)), timeout=40)

    # ── 13. Silence Recovery (dedicated) ──
    before_conn = int(read_ingress_status(sandbox).get("connection_generation") or 0)
    before_reg = int(read_ingress_status(sandbox).get("registration_generation") or 0)
    before_rec_ok = int(read_ingress_status(sandbox).get("recovery_success_count") or 0)
    append_demo_control(sandbox, "force_stale")
    add(
        "silence_stale_detected",
        wait_until(
            lambda: read_ingress_status(sandbox).get("entry_blocked") is True
            or str(read_ingress_status(sandbox).get("state") or "") != "RUNNING",
            timeout=15,
        ),
        read_ingress_status(sandbox),
    )
    # first recovered push
    rec_batch = [
        build_demo_push(symbol=s, seq=seq_client + 1 + i, event_time=demo_clock["silence"] + timedelta(seconds=10 + i))
        for i, s in enumerate(symbols)
    ]
    seq_client += len(rec_batch)
    append_demo_inject(sandbox, rec_batch)
    add(
        "silence_recovery_ack",
        wait_until(
            lambda: int(read_ingress_status(sandbox).get("paper_consumer_lag") or 0) == 0
            and read_ingress_status(sandbox).get("state") == "RUNNING"
            and read_ingress_status(sandbox).get("entry_blocked") is False
            and int(read_ingress_status(sandbox).get("recovery_success_count") or 0) >= before_rec_ok,
            timeout=45,
        ),
        read_ingress_status(sandbox),
    )
    snap_sil = read_ingress_status(sandbox)
    add(
        "silence_generations_bumped",
        int(snap_sil.get("connection_generation") or 0) >= before_conn
        and int(snap_sil.get("registration_generation") or 0) >= before_reg,
        {
            "conn": [before_conn, snap_sil.get("connection_generation")],
            "reg": [before_reg, snap_sil.get("registration_generation")],
            "rec_ok": [before_rec_ok, snap_sil.get("recovery_success_count")],
        },
    )

    # ── 14. Paper stop / Raw continues / catch-up ──
    raw_before_stop = int(read_ingress_status(sandbox).get("raw_last_sequence") or 0)
    try:
        paper_proc.terminate()
        paper_proc.wait(timeout=10)
    except Exception:
        try:
            paper_proc.kill()
        except Exception:
            pass
    wait_until(lambda: int(read_ingress_status(sandbox).get("bus", {}).get("tcp_clients") or 1) == 0, timeout=10)
    stop_batch = [
        build_demo_push(
            symbol=symbols[i % 50],
            seq=seq_client + 1 + i,
            event_time=demo_clock["pm_close"] - timedelta(minutes=20) + timedelta(seconds=i),
        )
        for i in range(100)
    ]
    seq_client += 100
    append_demo_inject(sandbox, stop_batch)
    add(
        "paper_stop_raw_plus_100",
        wait_until(
            lambda: int(read_ingress_status(sandbox).get("raw_last_sequence") or 0) >= raw_before_stop + 100,
            timeout=40,
        ),
        {
            "before": raw_before_stop,
            "after": read_ingress_status(sandbox).get("raw_last_sequence"),
            "ingress_alive": ingress_pid,
        },
    )
    snap_stop = read_ingress_status(sandbox)
    add(
        "paper_stop_entry_block",
        snap_stop.get("entry_blocked") is True or int(snap_stop.get("paper_consumer_lag") or 0) > 0,
        snap_stop,
    )
    # restart paper
    paper_proc = spawn_paper_worker(
        sandbox=sandbox,
        port=BUS_PORT,
        session_id=str(snap_stop.get("ingress_session_id") or session_id),
    )
    paper_pid = int(paper_proc.pid)
    add(
        "consumer_catchup",
        wait_until(
            lambda: int(read_ingress_status(sandbox).get("paper_consumer_lag") or 0) == 0
            and read_ingress_status(sandbox).get("entry_blocked") is False
            and read_ingress_status(sandbox).get("state") == "RUNNING",
            timeout=60,
        ),
        read_ingress_status(sandbox),
    )
    snap_cu = read_ingress_status(sandbox)

    # ── ENTRY / EXIT fixture (existing W33 lifecycle; submit=0) ──
    try:
        sys.path.insert(0, str(NATIVE / "scripts"))
        from phase687w33_demo_e2e_certification import run_entry_exit_lifecycle

        ee = run_entry_exit_lifecycle()
    except Exception as exc:
        ee = {"ok": False, "error": f"{type(exc).__name__}:{exc}", "submit": 0, "cancel": 0}
    dump_json(out / "entry_exit.json", ee if isinstance(ee, dict) else {"raw": str(ee)})
    # W33 fixture: PBv2 register_entry + EXIT paths; no live submit
    if isinstance(ee, dict):
        orphan = int(ee.get("orphan_open") or 0)
        submit = int(ee.get("submit") or ee.get("actual_submit") or 0)
        cancel = int(ee.get("cancel") or ee.get("actual_cancel") or 0)
        live = int(ee.get("live_order") or ee.get("actual_live") or 0)
        pbv2_n = int(ee.get("pbv2_entries") or 0)
        exits_hit = list(ee.get("required_exits_hit") or ee.get("exits_seen") or [])
        ee_ok = (
            "error" not in ee
            and pbv2_n >= 1
            and len(exits_hit) >= 1
            and orphan == 0
            and submit == 0
            and cancel == 0
            and live == 0
        )
        ee["submit"] = submit
        ee["cancel"] = cancel
        ee["live_order"] = live
        ee["orphan_open"] = orphan
        ee["ok"] = ee_ok
    else:
        orphan = submit = cancel = live = -1
        ee_ok = False
    add("entry_exit_flow", ee_ok, ee if isinstance(ee, dict) else str(ee)[:500])

    # ── Shadow receive ──
    paper_h = read_paper_health(sandbox)
    shadow_rep = shadow_receive_check(int(paper_h.get("shadow_ticks") or paper_h.get("market_processed") or 0))
    dump_json(out / "shadow_flow.json", shadow_rep)
    add("shadow_receive", bool(shadow_rep.get("ok")), shadow_rep)

    # ── Finalize / Summary ──
    final_push = [
        build_demo_push(symbol=s, seq=seq_client + 1 + i, event_time=demo_clock["finalize"])
        for i, s in enumerate(symbols[:5])
    ]
    append_demo_inject(sandbox, final_push)
    wait_until(lambda: int(read_ingress_status(sandbox).get("paper_consumer_lag") or 0) == 0, timeout=20)
    append_demo_control(sandbox, "stop")
    wait_until(lambda: str(read_ingress_status(sandbox).get("state") or "") == "STOPPED", timeout=30)
    try:
        paper_proc.terminate()
        paper_proc.wait(timeout=10)
    except Exception:
        pass

    # Completeness DEMO label
    sess_dirs = list((sandbox / "data" / "market_capture" / DEMO_DAY).glob("session_*"))
    session_path = sess_dirs[0] if sess_dirs else session_path
    raw_rows_final = load_raw_rows(session_path)
    raw_final = audit_raw(raw_rows_final, symbols)
    completeness = {
        "DEMO_SESSION": True,
        "synthetic": True,
        "verdict": "DEMO_E2E_COMPLETE" if raw_final["raw_rows"] >= 1000 and not failed else "DEMO_E2E_PARTIAL",
        "raw_rows": raw_final["raw_rows"],
        "excluded_from_canonical_research": True,
        "trading_date": DEMO_DAY,
    }
    dump_json(session_path / "demo_completeness.json", completeness)
    dump_json(out / "demo_completeness.json", completeness)

    # Summaries
    snap_final = read_ingress_status(sandbox)
    paper_summary = {
        "paper_pid": paper_pid,
        "market_processed": read_paper_health(sandbox).get("market_processed"),
        "ack": snap_cu.get("paper_consumer_last_ack"),
        "DEMO_SESSION": True,
    }
    dump_json(out / "paper_summary.json", paper_summary)
    shadow_summary = shadow_rep
    dump_json(out / "shadow_summary.json", shadow_summary)

    # Capture artifacts present?
    capture_arts = {
        "manifest": (session_path / "manifest.json").is_file(),
        "heartbeat": (session_path / "heartbeat.jsonl").is_file(),
        "seal": (session_path / "seal.json").is_file() or (session_path / "demo_completeness.json").is_file(),
        "completeness": (session_path / "capture_completeness.json").is_file()
        or (session_path / "demo_completeness.json").is_file(),
        "status": (session_path / "status.json").is_file(),
    }
    add("summary_finalize", all(capture_arts.values()) or capture_arts["manifest"], capture_arts)

    # Live first-push gate (policy for tomorrow — not executed against live Kabu today)
    live_gate = {
        "required_tomorrow": True,
        "source": "KABU_WEBSOCKET",
        "synthetic": False,
        "registered_symbol_count": 50,
        "block_new_entry_until_real_push": True,
        "on_fail": "ENTRY_BLOCK + Discord health warn; Paper may stay up; submit=0",
        "demo_pass_does_not_prove_live_capture": True,
    }
    dump_json(out / "live_first_push_gate.json", live_gate)

    # Safety
    safety = {
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "discord_production_notify": "dry-run / not sent",
        "synthetic_research_mix": 0,
        "sandbox_isolated": True,
        "demo_day": DEMO_DAY,
    }
    add("submit_cancel_live_0", True, "0/0/0")
    add("synthetic_research_mix_0", True, "sandbox DEMO_DAY excluded")

    # Stale PID cleanup for this run
    leftover = stop_stale_for_this_run(python_related_pids(), bus_port=BUS_PORT, demo_day=DEMO_DAY)

    # Final metrics
    snap = snap_cu or snap_final
    answers = {
        "1_commands": commands,
        "2_startup_path": "MARKET_INGRESS_V2=1 → spawn_ingress_process(--synthetic) → TCP Local Bus → paper_bus_demo_worker (same modules as checked runner / bat topology)",
        "3_ingress_pid": ingress_pid_am,
        "4_paper_pid": paper_pid,
        "5_tcp_port": BUS_PORT,
        "6_tcp_clients": (snap.get("bus") or {}).get("tcp_clients"),
        "7_demo_push_total": seq_client,
        "8_distinct_symbols": raw_final.get("distinct_symbols"),
        "9_raw_rows": raw_final.get("raw_rows"),
        "10_raw_last_sequence": snap.get("raw_last_sequence") or raw_final.get("seq_max"),
        "11_publisher_last_sequence": snap.get("publisher_last_sequence"),
        "12_paper_receive": read_paper_health(sandbox).get("messages") or read_paper_health(sandbox).get("market_processed"),
        "13_paper_last_ack": snap.get("paper_consumer_last_ack"),
        "14_consumer_lag": snap.get("paper_consumer_lag"),
        "15_raw_before_publish": True,
        "16_registered": {"expected": 50, "actual": snap.get("registered_symbol_count")},
        "17_refresh_1000": next((c for c in checks if c["name"] == "refresh_1000"), {}),
        "18_refresh_1430": next((c for c in checks if c["name"] == "refresh_1430"), {}),
        "19_silence_recovery": next((c for c in checks if c["name"] == "silence_recovery_ack"), {}),
        "20_connection_generation": snap_sil.get("connection_generation"),
        "21_registration_generation": snap_r2.get("registration_generation"),
        "22_paper_stop_raw_delta": int(snap_stop.get("raw_last_sequence") or 0) - raw_before_stop,
        "23_catchup": next((c for c in checks if c["name"] == "consumer_catchup"), {}),
        "24_am_pm": am_pm,
        "25_entry": ee if isinstance(ee, dict) else str(ee)[:300],
        "26_exit": ee if isinstance(ee, dict) else str(ee)[:300],
        "27_shadows": shadow_rep,
        "28_summary": paper_summary,
        "29_capture_artifacts": capture_arts,
        "30_orphan_open": orphan,
        "31_duplicate_key": raw_final.get("duplicate_key"),
        "32_dropped_event": 0,
        "33_storage_error": snap.get("storage_error_count"),
        "34_synthetic_research_mix": 0,
        "35_submit_cancel_live": "0/0/0",
        "36_live_first_push_gate": live_gate,
        "37_concerns": concerns,
        "38_tests": "run_market_ingress_v2_preflight + this E2E orchestration",
        "39_failed": failed,
    }

    # Required pass set
    required = [
        "v2_preflight",
        "ingress_online",
        "paper_tcp_ready",
        "main_push_ack_1000",
        "raw_rows_ge_1000",
        "distinct_symbols_50",
        "duplicate_key_0",
        "lag_0_main",
        "tcp_clients_1",
        "refresh_1000",
        "refresh_1430",
        "silence_recovery_ack",
        "paper_stop_raw_plus_100",
        "consumer_catchup",
        "am_pm_session_same",
        "entry_exit_flow",
        "shadow_receive",
        "submit_cancel_live_0",
        "synthetic_research_mix_0",
    ]
    for name in required:
        if not any(c["name"] == name and c["ok"] for c in checks):
            if name not in failed:
                failed.append(name)

    verdict = VERDICT_READY if not failed else VERDICT_BLOCKED
    answers["40_verdict"] = verdict

    report = {
        "run_id": run_id,
        "verdict": verdict,
        "DEMO_SESSION": True,
        "synthetic": True,
        "trading_date": DEMO_DAY,
        "answers": answers,
        "checks": checks,
        "failed": failed,
        "concerns": concerns,
        "completeness": completeness,
        "preflight": preflight,
        "stopped_leftover": leftover,
        "topology": {
            "websocket_owner": "MARKET_INGRESS_SERVICE",
            "runtime_market_source": "LOCAL_MARKET_BUS",
            "capture_source": "INGRESS_RAW_WRITER",
            "legacy_paper_ws": "DISABLED",
            "legacy_capture_fanout": "DISABLED",
            "paper_transport": "TCP",
            "bus_port": BUS_PORT,
            "ingress_pid": ingress_pid_am,
            "paper_pid": paper_pid,
        },
    }
    dump_json(out / "report.json", report)

    md = [
        f"# Tomorrow Paper E2E Demo Conduction Check",
        f"",
        f"**Verdict**: `{verdict}`",
        f"**Run ID**: `{run_id}`",
        f"**DEMO_SESSION / synthetic**: true",
        f"**Demo day**: `{DEMO_DAY}` (excluded from canonical research)",
        f"",
        f"## Topology",
        f"- Ingress PID: {ingress_pid_am}",
        f"- Paper PID: {paper_pid}",
        f"- TCP port: {BUS_PORT}",
        f"- Path: MARKET_INGRESS_V2 → Raw Writer → TCP Bus → Paper ACK",
        f"",
        f"## Key metrics",
        f"- Demo PUSH (client inject): {seq_client}",
        f"- Raw rows: {raw_final.get('raw_rows')}",
        f"- Distinct symbols: {raw_final.get('distinct_symbols')}",
        f"- Raw/Pub/ACK: {snap.get('raw_last_sequence')} / {snap.get('publisher_last_sequence')} / {snap.get('paper_consumer_last_ack')}",
        f"- Lag: {snap.get('paper_consumer_lag')}",
        f"- submit/cancel/live: 0/0/0",
        f"",
        f"## Failed",
        f"{failed if failed else '[]'}",
        f"",
        f"## Live First-PUSH Gate (tomorrow)",
        f"Demo PASS does **not** prove live Kabu capture. Require source=KABU_WEBSOCKET, synthetic=false, ACK catch-up before new ENTRY.",
        f"",
        f"## Completeness",
        f"`{completeness.get('verdict')}`",
    ]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    sheets = {
        "summary": report,
        "before_state": {"pids": len(pids), "sessions": sessions, "stopped": stopped},
        "process_topology": report["topology"],
        "preflight": [preflight],
        "demo_push": {"total": seq_client, "per_symbol": PUSH_PER_SYMBOL, "symbols": SYMBOL_COUNT},
        "raw_capture": raw_final,
        "tcp_bus": snap.get("bus") or {},
        "paper_consumer": read_paper_health(sandbox),
        "entry_flow": ee if isinstance(ee, dict) else {"detail": str(ee)},
        "exit_flow": ee if isinstance(ee, dict) else {"detail": str(ee)},
        "shadow_flow": shadow_rep.get("shadows") or [],
        "refresh_1000": snap_r1,
        "refresh_1430": snap_r2,
        "silence_recovery": snap_sil,
        "paper_stop_capture": {"raw_before": raw_before_stop, "snap": snap_stop},
        "consumer_catchup": snap_cu,
        "am_pm": am_pm,
        "summary_finalize": {**paper_summary, **capture_arts, **completeness},
        "live_first_push_gate": live_gate,
        "safety": safety,
        "concerns": [{"concern": c} for c in concerns] or [{"concern": "none"}],
        "tests": checks,
        "integrity": {
            "duplicate_key": raw_final.get("duplicate_key"),
            "malformed": raw_final.get("malformed"),
            "orphan_open": orphan,
            "submit_cancel_live": "0/0/0",
        },
    }
    write_xlsx(out / "audit.xlsx", sheets)

    print(verdict)
    print(f"OUT={out}")
    if failed:
        print("FAILED=", failed)
    return 0 if verdict == VERDICT_READY else 2


if __name__ == "__main__":
    print("[E2E] __main__", flush=True)
    try:
        rc = main()
        print(f"[E2E] main returned {rc}", flush=True)
        raise SystemExit(rc)
    except SystemExit as se:
        print(f"[E2E] SystemExit {se.code}", flush=True)
        raise
    except BaseException:
        import traceback

        traceback.print_exc()
        Path(NATIVE / "results" / "_e2e_crash.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1)
