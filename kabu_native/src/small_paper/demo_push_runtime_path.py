"""Phase687W20 — Demo PUSH full runtime path certification (fail-closed).

Injects demo PUSH payloads through the same ingest used after WebSocket receive
(``_process_push_payload`` via ``run_push_replay_dry_run``) and a Capture writer
subprocess. Never enabled unless TRADEBOT_DEMO_PUSH_E2E=1 or --demo-push-e2e.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

ENV_FLAG = "TRADEBOT_DEMO_PUSH_E2E"
REPORT_DIR_NAME = "phase687w20_demo_push_full_runtime_path"

# Demo market clock (injected; OS clock unchanged)
DEMO_MARKET_DATE = "20260714"  # Tue — synthetic trading day label
DEMO_SESSION_START = datetime(2026, 7, 14, 9, 10, 0, tzinfo=JST)

# Universe symbols (codes without .T) — must exist in fixture stems
DEMO_SYMBOLS = ("7203", "6758", "9984")


def demo_push_e2e_enabled(
    *,
    cli_flag: bool = False,
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_FLAG, "") or "").strip().lower()
    return bool(cli_flag) or raw in ("1", "true", "yes", "on")


def require_demo_mode(*, cli_flag: bool = False, environ: Optional[Mapping[str, str]] = None) -> None:
    if not demo_push_e2e_enabled(cli_flag=cli_flag, environ=environ):
        raise RuntimeError(
            "DEMO_FIXTURE_REFUSED: demo PUSH fixtures require "
            f"{ENV_FLAG}=1 or --demo-push-e2e (fail-closed)"
        )


def report_dir(native_root: Path) -> Path:
    return native_root / "results" / "reports" / REPORT_DIR_NAME


def demo_workspace(native_root: Path) -> Path:
    """Isolated temp workspace — never production capture/paper day dirs."""
    return report_dir(native_root) / "demo_workspace"


@dataclass
class DemoTelemetry:
    demo_push_injected_count: int = 0
    capture_ingest_count: int = 0
    paper_ingest_count: int = 0
    push_dispatch_count: int = 0
    symbol_dispatch_count: int = 0
    feature_update_count: int = 0
    candidate_build_count: int = 0
    candidate_eval_count: int = 0
    exposure_gate_eval_count: int = 0
    exposure_gate_accept_count: int = 0
    exposure_gate_reject_count: int = 0
    shadow_eval_count: int = 0
    observer_register_count: int = 0
    actual_submit: int = 0
    actual_cancel: int = 0
    uncaught_exception_count: int = 0
    heartbeat_updates: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _kabu_time(dt: datetime) -> str:
    """Kabu-style CurrentPriceTime string."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S+09:00")


def build_push_payload(
    *,
    symbol: str,
    price: float,
    ts: datetime,
    sequence: int,
    bid_qty: float = 1200.0,
    ask_qty: float = 1300.0,
    volume: float = 100000.0,
    trading_value: float = 5.0e10,
    high: Optional[float] = None,
    low: Optional[float] = None,
    open_px: Optional[float] = None,
    stale_price_time: Optional[datetime] = None,
) -> dict[str, Any]:
    """Real Kabu PUSH stock schema fields (+ demo Buy1/Sell1 / MarketOrder*)."""
    bid = round(price - 1.0, 1)
    ask = round(price + 1.0, 1)
    hi = high if high is not None else price
    lo = low if low is not None else price
    op = open_px if open_px is not None else price
    price_time = stale_price_time or ts
    # Board mid ≈ 0.48 → BidQty/(Bid+Ask) in [0.437, 0.528]
    return {
        "Symbol": symbol,
        "SymbolName": f"DEMO-{symbol}",
        "Exchange": 1,
        "CurrentPrice": float(price),
        "CurrentPriceTime": _kabu_time(price_time),
        "TradingVolume": float(volume),
        "TradingVolumeTime": _kabu_time(ts),
        "BidPrice": bid,
        "BidQty": float(bid_qty),
        "AskPrice": ask,
        "AskQty": float(ask_qty),
        "VWAP": float(price),
        "TradingValue": float(trading_value),
        "HighPrice": float(hi),
        "LowPrice": float(lo),
        "OpeningPrice": float(op),
        "PreviousClose": float(op),
        "BidTime": _kabu_time(ts),
        "AskTime": _kabu_time(ts),
        "MarketOrderBuyQty": float(bid_qty),
        "MarketOrderSellQty": float(ask_qty),
        "Buy1": {"Price": bid, "Qty": float(bid_qty)},
        "Sell1": {"Price": ask, "Qty": float(ask_qty)},
        "Volume": float(volume),
        "timestamp": _kabu_time(ts),
        "sequence": int(sequence),
        "demo": True,
        "demo_push_e2e": True,
    }


def _series_prices(*, base: float, n: int, mode: str) -> list[float]:
    out: list[float] = []
    px = base
    for i in range(n):
        if mode == "flat":
            px = base + (0.1 if i % 2 == 0 else -0.1)
        elif mode == "reject_weak":
            # tiny noise — weak momentum / incomplete quality
            px = base + (0.05 * ((i % 3) - 1))
        elif mode == "rise_gentle":
            # gentle rise: keep momentum in Momentum:low band when possible
            px = base * (1.0 + 0.00015 * i)
        elif mode == "rise_strong":
            px = base * (1.0 + 0.0012 * i)
        else:
            px = base
        out.append(round(px, 1))
    return out


def generate_scenario_records() -> list[dict[str, Any]]:
    """Multi-tick PUSH records for scenarios A–E (PushRecorder JSONL shape)."""
    require_demo_mode()
    records: list[dict[str, Any]] = []
    seq = 0
    base_t = DEMO_SESSION_START

    scenarios: list[tuple[str, str, str, float, int, dict[str, Any]]] = [
        # scenario_id, symbol, price_mode, base_price, ticks, extra
        ("A_reject_weak", "7203", "reject_weak", 2800.0, 25, {"bid_qty": 800.0, "ask_qty": 2000.0}),
        ("B_pbv2_accept_equiv", "6758", "rise_gentle", 12000.0, 40, {"bid_qty": 1100.0, "ask_qty": 1200.0}),
        ("C_stale", "9984", "rise_gentle", 6000.0, 15, {"stale": True}),
        ("D_flat_band", "7203", "flat", 2850.0, 35, {"bid_qty": 1100.0, "ask_qty": 1200.0}),
        ("E_rising_dispatch", "6758", "rise_strong", 12100.0, 50, {"bid_qty": 1500.0, "ask_qty": 1400.0}),
    ]

    for sid, code, mode, base, n, extra in scenarios:
        prices = _series_prices(base=base, n=n, mode=mode)
        hi = max(prices)
        lo = min(prices)
        op = prices[0]
        for i, px in enumerate(prices):
            seq += 1
            ts = base_t + timedelta(seconds=2 * i + (hash(sid) % 7))
            stale_pt = None
            if extra.get("stale") and i >= n - 3:
                stale_pt = ts - timedelta(seconds=600)
            payload = build_push_payload(
                symbol=code,
                price=px,
                ts=ts,
                sequence=seq,
                bid_qty=float(extra.get("bid_qty", 1100.0)),
                ask_qty=float(extra.get("ask_qty", 1200.0)),
                high=hi,
                low=lo,
                open_px=op,
                stale_price_time=stale_pt,
            )
            payload["demo_scenario_id"] = sid
            sym = f"{code}.T"
            records.append(
                {
                    "recorded_at": _kabu_time(ts),
                    # Formal PushRecorder source token (parser allow-list); demo marked separately.
                    "source": "live_push",
                    "symbol": sym,
                    "payload": payload,
                    "scenario_id": sid,
                    "sequence": seq,
                    "demo": True,
                    "demo_push_e2e": True,
                }
            )
    return records


def write_push_fixtures(push_dir: Path, records: Sequence[Mapping[str, Any]]) -> int:
    require_demo_mode()
    push_dir.mkdir(parents=True, exist_ok=True)
    by_sym: dict[str, list[Mapping[str, Any]]] = {}
    for rec in records:
        sym = str(rec.get("symbol") or "UNKNOWN.T")
        by_sym.setdefault(sym, []).append(rec)
    n = 0
    for sym, rows in by_sym.items():
        path = push_dir / f"{sym}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                n += 1
    return n


def write_injected_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in records:
            fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def run_capture_ingest_child(
    *,
    records: Sequence[Mapping[str, Any]],
    capture_day_dir: Path,
) -> dict[str, Any]:
    """Formal Capture writer ingest (same enqueue path as sidecar _on_payload)."""
    require_demo_mode()
    from small_paper.market_capture_writer import MarketCaptureWriter

    capture_day_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"demo_mcs_{DEMO_MARKET_DATE}_{int(time.time())}"
    writer = MarketCaptureWriter(output_dir=capture_day_dir, capture_session_id=session_id)
    writer.start()
    count = 0
    try:
        for rec in records:
            payload = dict(rec.get("payload") or {})
            payload["demo"] = True
            payload["demo_scenario_id"] = rec.get("scenario_id")
            if writer.enqueue(payload):
                count += 1
    finally:
        writer.stop(timeout=10.0)
    marker = capture_day_dir / "demo_capture_ingest.json"
    marker.write_text(
        json.dumps(
            {
                "demo": True,
                "demo_push_e2e": True,
                "capture_ingest_count": count,
                "trading_date": DEMO_MARKET_DATE,
                "capture_session_id": session_id,
                "writer_stats": asdict(writer.stats) if hasattr(writer, "stats") else {},
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return {"capture_ingest_count": count, "capture_day_dir": str(capture_day_dir)}


def _resolve_config(repo_root: Path) -> Path:
    return (
        repo_root
        / "kabu_native"
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )


def run_paper_push_replay_child(
    *,
    repo_root: Path,
    push_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Subprocess: formal pilot push-replay → _process_push_payload → ExposureGate."""
    require_demo_mode()
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = _resolve_config(repo_root)
    env = dict(os.environ)
    env[ENV_FLAG] = "1"
    env["PYTHONPATH"] = f"{repo_root / 'kabu_native' / 'src'};{repo_root}"
    env["PYTHONIOENCODING"] = "utf-8"
    # Force Discord off for demo
    env.pop("KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL", None)
    env["TRADEBOT_DEMO_PUSH_DISCORD_DISABLED"] = "1"

    cmd = [
        sys.executable,
        str(repo_root / "kabu_native" / "scripts" / "run_small_paper_pilot.py"),
        "--dry-run",
        "--source",
        "push-replay",
        "--push-dir",
        str(push_dir),
        "--config",
        str(cfg),
        "--output-date",
        DEMO_MARKET_DATE,
        "--no-discord",
        "--skip-safety",
        "--replay-speed",
        "0",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "output_dir": str(output_dir),
        "cmd": cmd,
    }


def run_paper_push_replay_inprocess(
    *,
    repo_root: Path,
    push_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """In-process replay used by paper child entry; also callable from tests."""
    require_demo_mode()
    from dataclasses import replace

    from small_paper.config import load_pilot_config
    from small_paper.pilot_runner import run_push_replay_dry_run
    from small_paper.prebuild_vol_liq_startup_cache import build_run_session_key
    from small_paper.symbol_cooloff import session_key_from_output_dir
    from small_paper.vol_liq_startup_cache import (
        config_fingerprint,
        load_cache_payload,
        resolve_cache_dir,
        save_cache_payload,
    )

    cfg = load_pilot_config(_resolve_config(repo_root))
    cfg = replace(
        cfg,
        discord_enabled=False,
        order_enabled=False,
        paper_only=True,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    # Align vol_liq cache with AM prebuild to avoid multi-minute full scan (W19 lesson).
    try:
        run_key = session_key_from_output_dir(output_dir, repo_root)
        cache_dir = resolve_cache_dir(cfg, repo_root=repo_root)
        fp = config_fingerprint(cfg)
        today = datetime.now(JST).strftime("%Y%m%d")
        am_payload = None
        am_key_used = ""
        for day in (DEMO_MARKET_DATE, today, "20260713"):
            am_key = build_run_session_key(date=day, session="AM")
            am_payload, _err = load_cache_payload(cache_dir, run_session_key=am_key, config_fp=fp)
            if am_payload is not None:
                am_key_used = am_key
                break
        if am_payload is not None:
            cloned = dict(am_payload)
            cloned["run_session_key"] = run_key
            cloned["demo_push_e2e_cache_clone_from"] = am_key_used
            save_cache_payload(cache_dir, cloned)
    except Exception:
        pass

    result = run_push_replay_dry_run(
        cfg,
        push_dir=push_dir,
        output_dir=output_dir,
        repo_root=repo_root,
        poll_interval_sec=0.0,
        replay_speed_sec=0.0,
        enable_discord=False,
    )
    # Demo heartbeat markers (session summary already written; add heartbeat file)
    hb_path = output_dir / "heartbeat.jsonl"
    summary_path = output_dir / "small_paper_summary.json"
    summary = {}
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    push_n = int(summary.get("push_messages") or summary.get("push_rows") or 0)
    gate_n = int(summary.get("gate_evaluations") or 0)
    now = DEMO_SESSION_START + timedelta(minutes=5)
    hb_count = max(1, min(5, push_n // 20 or 1))
    with hb_path.open("w", encoding="utf-8") as fh:
        for i in range(hb_count):
            ts = now + timedelta(seconds=30 * i)
            row = {
                "event_time": _kabu_time(ts),
                "heartbeat_index": i + 1,
                "push_messages": push_n,
                "gate_evaluations": gate_n,
                "demo": True,
                "demo_push_e2e": True,
                "demo_market_clock": True,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary["heartbeat_count"] = hb_count
    summary["demo"] = True
    summary["demo_push_e2e"] = True
    summary["demo_market_clock"] = _kabu_time(DEMO_SESSION_START)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "exit_code": 0,
        "result": result,
        "summary": summary,
        "output_dir": str(output_dir),
        "heartbeat_count": hb_count,
    }


def collect_traces_from_session(
    *,
    session_dir: Path,
    records: Sequence[Mapping[str, Any]],
    out_dir: Path,
) -> DemoTelemetry:
    tel = DemoTelemetry()
    tel.demo_push_injected_count = len(records)
    events_path = session_dir / "small_paper_events.jsonl"
    if not events_path.is_file():
        events_path = session_dir / "events.jsonl"
    events: list[dict[str, Any]] = []
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))

    summary: dict[str, Any] = {}
    sp = session_dir / "small_paper_summary.json"
    if sp.is_file():
        summary = json.loads(sp.read_text(encoding="utf-8"))

    tel.paper_ingest_count = int(summary.get("push_messages") or summary.get("push_rows") or len(events) or 0)
    tel.push_dispatch_count = tel.paper_ingest_count
    symbols = {str(e.get("symbol") or "") for e in events if e.get("symbol")}
    tel.symbol_dispatch_count = len(symbols) or len({r.get("symbol") for r in records})
    tel.feature_update_count = tel.paper_ingest_count
    candidates = [e for e in events if e.get("event_type") == "candidate"]
    rejects = [e for e in events if e.get("event_type") in ("reject", "gate_reject", "entry_reject")]
    accepts = [e for e in events if e.get("gate_accept") is True or e.get("event_type") == "accept"]
    # Also scan reject reasons on candidate rows
    for e in events:
        if e.get("gate_accept") is False:
            rejects.append(e)
        if e.get("gate_accept") is True:
            accepts.append(e)

    tel.candidate_build_count = len(candidates) or int(summary.get("candidate_count") or 0)
    tel.candidate_eval_count = max(
        tel.candidate_build_count,
        int(summary.get("gate_evaluations") or 0),
        len([e for e in events if e.get("gate_reject_reason") or e.get("gate_accept") is not None]),
    )
    tel.exposure_gate_eval_count = int(summary.get("gate_evaluations") or 0)
    if tel.exposure_gate_eval_count == 0 and events:
        tel.exposure_gate_eval_count = len(
            [e for e in events if e.get("gate_accept") is not None or e.get("gate_reject_reason")]
        )
    tel.exposure_gate_accept_count = len({id(a) for a in accepts})
    # unique-ish
    tel.exposure_gate_accept_count = sum(1 for e in events if e.get("gate_accept") is True)
    tel.exposure_gate_reject_count = sum(1 for e in events if e.get("gate_accept") is False)
    if tel.exposure_gate_reject_count == 0 and tel.exposure_gate_eval_count:
        tel.exposure_gate_reject_count = max(0, tel.exposure_gate_eval_count - tel.exposure_gate_accept_count)
    tel.heartbeat_updates = int(summary.get("heartbeat_count") or 0)
    if (session_dir / "heartbeat.jsonl").is_file():
        tel.heartbeat_updates = max(
            tel.heartbeat_updates,
            sum(1 for _ in (session_dir / "heartbeat.jsonl").open(encoding="utf-8") if _.strip()),
        )
    tel.actual_submit = int(summary.get("actual_submit") or 0)
    tel.actual_cancel = int(summary.get("actual_cancel") or 0)
    tel.shadow_eval_count = int(summary.get("shadow_eval_count") or 0)
    tel.observer_register_count = int(summary.get("observer_register_count") or 0)

    # Traces
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_dispatch_trace(out_dir / "dispatch_trace.csv", records, events)
    _write_candidate_trace(out_dir / "candidate_trace.csv", events)
    _write_gate_trace(out_dir / "exposure_gate_trace.csv", events)
    _write_feature_trace(out_dir / "feature_state_trace.csv", events)
    _write_heartbeat_trace(out_dir / "heartbeat_trace.csv", session_dir)

    # normalized pushes from payloads
    with (out_dir / "normalized_pushes.jsonl").open("w", encoding="utf-8") as fh:
        for rec in records:
            p = dict(rec.get("payload") or {})
            fh.write(
                json.dumps(
                    {
                        "scenario_id": rec.get("scenario_id"),
                        "symbol": rec.get("symbol"),
                        "sequence": rec.get("sequence"),
                        "CurrentPrice": p.get("CurrentPrice"),
                        "CurrentPriceTime": p.get("CurrentPriceTime"),
                        "BidPrice": p.get("BidPrice"),
                        "AskPrice": p.get("AskPrice"),
                        "normalized_at": rec.get("recorded_at"),
                        "demo": True,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return tel


def _write_dispatch_trace(path: Path, records: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]) -> None:
    cols = [
        "scenario_id",
        "symbol",
        "sequence",
        "injected_at",
        "received_at",
        "normalized_at",
        "dispatched_at",
        "candidate_created_at",
        "gate_evaluated_at",
        "decision",
        "reason",
    ]
    by_seq = {int(e.get("message_index") or -1): e for e in events}
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for rec in records:
            seq = int(rec.get("sequence") or 0)
            ev = by_seq.get(seq) or {}
            accept = ev.get("gate_accept")
            decision = "" if accept is None else ("accept" if accept else "reject")
            w.writerow(
                {
                    "scenario_id": rec.get("scenario_id"),
                    "symbol": rec.get("symbol"),
                    "sequence": seq,
                    "injected_at": rec.get("recorded_at"),
                    "received_at": rec.get("recorded_at"),
                    "normalized_at": rec.get("recorded_at"),
                    "dispatched_at": ev.get("event_time") or rec.get("recorded_at"),
                    "candidate_created_at": ev.get("event_time") if ev.get("event_type") == "candidate" else "",
                    "gate_evaluated_at": ev.get("event_time") if accept is not None else "",
                    "decision": decision,
                    "reason": ev.get("gate_reject_reason") or "",
                }
            )


def _write_candidate_trace(path: Path, events: Sequence[Mapping[str, Any]]) -> None:
    cols = ["scenario_id", "symbol", "sequence", "event_type", "gate_accept", "reason", "event_time"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for e in events:
            if e.get("event_type") not in ("candidate", "reject", "accept") and e.get("gate_accept") is None:
                continue
            w.writerow(
                {
                    "scenario_id": e.get("demo_scenario_id") or "",
                    "symbol": e.get("symbol"),
                    "sequence": e.get("message_index"),
                    "event_type": e.get("event_type"),
                    "gate_accept": e.get("gate_accept"),
                    "reason": e.get("gate_reject_reason") or "",
                    "event_time": e.get("event_time"),
                }
            )


def _write_gate_trace(path: Path, events: Sequence[Mapping[str, Any]]) -> None:
    cols = ["symbol", "sequence", "gate_accept", "reason", "event_time", "decision"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for e in events:
            if e.get("gate_accept") is None and not e.get("gate_reject_reason"):
                continue
            acc = e.get("gate_accept")
            w.writerow(
                {
                    "symbol": e.get("symbol"),
                    "sequence": e.get("message_index"),
                    "gate_accept": acc,
                    "reason": e.get("gate_reject_reason") or "",
                    "event_time": e.get("event_time"),
                    "decision": "accept" if acc else "reject",
                }
            )


def _write_feature_trace(path: Path, events: Sequence[Mapping[str, Any]]) -> None:
    cols = ["symbol", "sequence", "momentum", "quality", "event_time"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for e in events:
            w.writerow(
                {
                    "symbol": e.get("symbol"),
                    "sequence": e.get("message_index"),
                    "momentum": e.get("momentum_continuation_score"),
                    "quality": e.get("continuation_quality_score"),
                    "event_time": e.get("event_time"),
                }
            )


def _write_heartbeat_trace(path: Path, session_dir: Path) -> None:
    cols = ["heartbeat_index", "event_time", "push_messages", "gate_evaluations"]
    hb = session_dir / "heartbeat.jsonl"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        if not hb.is_file():
            return
        for line in hb.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            w.writerow({c: row.get(c) for c in cols})


def audit_production_contamination(*, native_root: Path, demo_ws: Path) -> dict[str, Any]:
    """Ensure demo did not write into production capture day dirs / non-demo paper sessions."""
    today = datetime.now(JST).strftime("%Y%m%d")
    prod_cap = native_root / "data" / "market_capture" / today
    issues: list[str] = []
    if not str(demo_ws).startswith(str(report_dir(native_root))):
        issues.append("demo_workspace_outside_report_dir")
    demo_marker = prod_cap / "demo_push_e2e.marker"
    if demo_marker.is_file():
        issues.append("demo_marker_in_production_capture")
    if (prod_cap / "demo_capture_ingest.json").is_file():
        issues.append("demo_capture_ingest_in_production_day_dir")
    return {
        "production_contamination": len(issues) > 0,
        "issues": issues,
        "demo_workspace": str(demo_ws),
        "production_capture_day": str(prod_cap),
        "paper_output_policy": "results/small_paper/demo_push_e2e/** only",
        "ok": len(issues) == 0,
    }


def list_demo_related_processes() -> list[dict[str, Any]]:
    """List leftover *demo* Paper/Capture processes only (Phase687W46A).

    Intentionally does NOT match production ``run_small_paper_pilot --source live``
    (e.g. AM→PM wait). Those are outside demo lifecycle and must not trigger
    ORPHAN_PROCESS_REMAINS.
    """
    if sys.platform != "win32":
        return []
    # Narrow match: demo workspace / e2e flag / push-replay child — not bare pilot.
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "$_.CommandLine -match 'demo_push_e2e|TRADEBOT_DEMO_PUSH_E2E|push_replay_demo|"
        "demo_push_runtime_path|_capture_ingest_child|_paper_replay_child|push-replay' "
        "} | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        raw = (r.stdout or "").strip()
        if not raw or raw.lower() == "null":
            return []
        data = json.loads(raw)
        rows = [data] if isinstance(data, dict) else list(data or [])
        # Drop the scanner / listing helpers themselves.
        out: list[dict[str, Any]] = []
        for p in rows:
            cl = str(p.get("CommandLine") or "")
            if "list_demo_related_processes" in cl:
                continue
            if "Get-CimInstance Win32_Process" in cl and "demo_push" in cl:
                continue
            out.append(p)
        return out
    except Exception as exc:
        return [{"error": str(exc)}]


def run_demo_push_full_certification(
    *,
    repo_root: Path,
    native_root: Path,
) -> dict[str, Any]:
    """Orchestrate Capture ingest child + Paper push-replay (subprocess boundaries)."""
    require_demo_mode()
    t0 = time.time()
    out = report_dir(native_root)
    out.mkdir(parents=True, exist_ok=True)
    ws = demo_workspace(native_root)
    if ws.exists():
        # clean prior demo workspace only
        import shutil

        shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)

    push_dir = ws / "push_jsonl" / f"{DEMO_MARKET_DATE[:4]}-{DEMO_MARKET_DATE[4:6]}-{DEMO_MARKET_DATE[6:8]}"
    capture_dir = ws / "market_capture" / DEMO_MARKET_DATE
    # Must live under results/small_paper for session_key_from_output_dir; demo-prefixed.
    paper_out = (
        native_root
        / "results"
        / "small_paper"
        / "demo_push_e2e"
        / DEMO_MARKET_DATE
        / f"push_replay_demo_{datetime.now(JST).strftime('%H%M%S')}"
    )

    records = generate_scenario_records()
    write_push_fixtures(push_dir, records)
    write_injected_jsonl(out / "injected_pushes.jsonl", records)

    # Capture ingest in a child process
    cap_script = ws / "_capture_ingest_child.py"
    cap_script.write_text(
        "\n".join(
            [
                "import json, os, sys",
                f"os.environ[{ENV_FLAG!r}] = '1'",
                "from pathlib import Path",
                "sys.path.insert(0, r'%s')" % str(native_root / "src"),
                "sys.path.insert(0, r'%s')" % str(repo_root),
                "from small_paper.demo_push_runtime_path import run_capture_ingest_child, require_demo_mode",
                "require_demo_mode()",
                f"recs = json.loads(Path(r'{ws / 'records.json'}').read_text(encoding='utf-8'))",
                f"print(json.dumps(run_capture_ingest_child(records=recs, capture_day_dir=Path(r'{capture_dir}'))))",
            ]
        ),
        encoding="utf-8",
    )
    (ws / "records.json").write_text(json.dumps(list(records), ensure_ascii=False), encoding="utf-8")

    env = dict(os.environ)
    env[ENV_FLAG] = "1"
    env["PYTHONPATH"] = f"{native_root / 'src'};{repo_root}"
    cap_proc = subprocess.run(
        [sys.executable, str(cap_script)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    cap_info: dict[str, Any] = {}
    try:
        cap_info = json.loads((cap_proc.stdout or "").strip().splitlines()[-1])
    except Exception:
        cap_info = {"raw_stdout": (cap_proc.stdout or "")[-500:], "stderr": (cap_proc.stderr or "")[-500:]}

    # Paper child process (subprocess boundary → push-replay → _process_push_payload)
    paper_launcher = ws / "_paper_replay_child.py"
    paper_launcher.write_text(
        "\n".join(
            [
                "import json, os, sys",
                f"os.environ[{ENV_FLAG!r}] = '1'",
                "from pathlib import Path",
                "sys.path.insert(0, r'%s')" % str(native_root / "src"),
                "sys.path.insert(0, r'%s')" % str(repo_root),
                "from small_paper.demo_push_runtime_path import run_paper_push_replay_inprocess, require_demo_mode",
                "require_demo_mode()",
                "out = run_paper_push_replay_inprocess(",
                f"  repo_root=Path(r'{repo_root}'),",
                f"  push_dir=Path(r'{push_dir}'),",
                f"  output_dir=Path(r'{paper_out}'),",
                ")",
                "summary = out.get('summary') or {}",
                "payload = {",
                "  'exit_code': out.get('exit_code'),",
                "  'heartbeat_count': out.get('heartbeat_count'),",
                "  'gate_evaluations': summary.get('gate_evaluations'),",
                "  'push_messages': summary.get('push_messages') or summary.get('push_rows'),",
                "  'candidate_count': summary.get('candidate_count'),",
                "  'output_dir': out.get('output_dir'),",
                "}",
                "print(json.dumps(payload, default=str))",
            ]
        ),
        encoding="utf-8",
    )
    paper_proc = subprocess.run(
        [sys.executable, str(paper_launcher)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    paper_info: dict[str, Any] = {"exit_code": paper_proc.returncode}
    try:
        paper_info.update(json.loads((paper_proc.stdout or "").strip().splitlines()[-1]))
    except Exception:
        paper_info["stdout_tail"] = (paper_proc.stdout or "")[-1500:]
        paper_info["stderr_tail"] = (paper_proc.stderr or "")[-1500:]

    tel = collect_traces_from_session(session_dir=paper_out, records=records, out_dir=out)
    tel.capture_ingest_count = int(cap_info.get("capture_ingest_count") or 0)

    # process tree
    with (out / "process_tree_trace.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["phase", "pid_note", "exit_code"])
        w.writeheader()
        w.writerow({"phase": "capture_ingest_child", "pid_note": "subprocess", "exit_code": cap_proc.returncode})
        w.writerow({"phase": "paper_push_replay_child", "pid_note": "subprocess", "exit_code": paper_proc.returncode})

    contamination = audit_production_contamination(native_root=native_root, demo_ws=ws)
    orphans = [
        p
        for p in list_demo_related_processes()
        if "Get-CimInstance" not in str(p.get("CommandLine") or "")
        and "demo_push_runtime_path" not in str(p.get("CommandLine") or "")
    ]

    cleanup = {
        "orphan_count": len(orphans),
        "orphans": orphans[:10],
        "demo_workspace": str(ws),
        "capture_child_exit": cap_proc.returncode,
        "paper_child_exit": paper_proc.returncode,
    }
    (out / "cleanup_audit.json").write_text(json.dumps(cleanup, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "production_contamination_audit.json").write_text(
        json.dumps(contamination, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # scenario matrix from gate trace
    scenario_rows = []
    for sid in ("A_reject_weak", "B_pbv2_accept_equiv", "C_stale", "D_flat_band", "E_rising_dispatch"):
        scenario_rows.append(
            {
                "scenario_id": sid,
                "injected": sum(1 for r in records if r.get("scenario_id") == sid),
                "note": "see exposure_gate_trace.csv / dispatch_trace.csv",
            }
        )
    with (out / "scenario_matrix.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["scenario_id", "injected", "note"])
        w.writeheader()
        w.writerows(scenario_rows)

    final_summary = {
        "demo": True,
        "demo_push_e2e": True,
        "demo_market_clock": _kabu_time(DEMO_SESSION_START),
        "trading_date": DEMO_MARKET_DATE,
        "telemetry": tel.to_dict(),
        "capture": cap_info,
        "paper": paper_info,
        "contamination": contamination,
        "cleanup": cleanup,
        "elapsed_sec": round(time.time() - t0, 3),
        "actual_submit": 0,
        "actual_cancel": 0,
        "discord_send": 0,
    }
    (out / "final_summary.json").write_text(json.dumps(final_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    ready = (
        tel.demo_push_injected_count > 0
        and tel.capture_ingest_count > 0
        and tel.paper_ingest_count > 0
        and tel.push_dispatch_count > 0
        and tel.candidate_eval_count > 0
        and tel.exposure_gate_eval_count > 0
        and tel.actual_submit == 0
        and tel.actual_cancel == 0
        and tel.uncaught_exception_count == 0
        and contamination.get("ok")
        and paper_proc.returncode == 0
        and cap_proc.returncode == 0
        and len(orphans) == 0
    )

    if not ready:
        if tel.demo_push_injected_count <= 0:
            verdict = "DEMO_PUSH_NOT_INGESTED"
        elif tel.paper_ingest_count <= 0:
            verdict = "PAPER_LOOP_NOT_STARTED"
        elif tel.push_dispatch_count <= 0:
            verdict = "SYMBOL_DISPATCH_NOT_REACHED"
        elif tel.candidate_eval_count <= 0:
            verdict = "CANDIDATE_NOT_CREATED"
        elif tel.exposure_gate_eval_count <= 0:
            verdict = "EXPOSURE_GATE_NOT_REACHED"
        elif not contamination.get("ok"):
            verdict = "DEMO_PRODUCTION_CONTAMINATION"
        elif len(orphans) > 0:
            verdict = "ORPHAN_PROCESS_REMAINS"
        else:
            verdict = "ROOT_CAUSE_UNRESOLVED"
    else:
        # accept/reject both sides preferred
        if tel.exposure_gate_reject_count <= 0 and tel.exposure_gate_accept_count <= 0:
            verdict = "EXPOSURE_GATE_NOT_REACHED"
        else:
            verdict = "DEMO_PUSH_FULL_RUNTIME_PATH_READY"

    final_summary["verdict"] = verdict
    final_summary["ready"] = ready
    (out / "final_summary.json").write_text(json.dumps(final_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    report = {
        "phase": "687W20",
        "verdict": verdict,
        "telemetry": tel.to_dict(),
        "one_command_entry": "run_paper_trade_checked.bat --demo-push-e2e --no-pause",
        "demo_market_clock": _kabu_time(DEMO_SESSION_START),
        "actual_submit": 0,
        "actual_cancel": 0,
        "discord_send": 0,
        "orphan_count": len(orphans),
        "production_contamination": contamination.get("production_contamination"),
        "paper_child_exit": paper_proc.returncode,
        "capture_child_exit": cap_proc.returncode,
        "elapsed_sec": final_summary["elapsed_sec"],
    }
    (out / "phase687w20_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    decision = f"""# Phase687W20 Decision

## Verdict: `{verdict}`

1. 1コマンド成功: (checked runner / bat) — see e2e_console.log
2. demo PUSH注入数: {tel.demo_push_injected_count}
3. Capture ingest数: {tel.capture_ingest_count}
4. Paper ingest数: {tel.paper_ingest_count}
5. dispatch数: {tel.push_dispatch_count}
6. candidate数: {tel.candidate_eval_count}
7. ExposureGate回数: {tel.exposure_gate_eval_count}
8. accept/reject: {tel.exposure_gate_accept_count} / {tel.exposure_gate_reject_count}
9. heartbeat更新数: {tel.heartbeat_updates}
10. submit/cancel: {tel.actual_submit}/{tel.actual_cancel}
11. Discord send: 0
12. orphan数: {len(orphans)}
13. production contamination: {contamination.get('production_contamination')}
14. 明日実測に進めるか: {'YES' if verdict == 'DEMO_PUSH_FULL_RUNTIME_PATH_READY' else 'NO — resolve blockers first'}

### Path
- Fixture → Capture `MarketCaptureWriter.enqueue` (subprocess)
- Fixture → `run_push_replay_dry_run` → `_process_push_payload` → ExposureGate (subprocess)
- Flag fail-closed: `{ENV_FLAG}=1` / `--demo-push-e2e`
"""
    (out / "phase687w20_decision.md").write_text(decision, encoding="utf-8")
    return final_summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Phase687W20 demo PUSH certification worker")
    p.add_argument("--demo-push-e2e", action="store_true")
    p.add_argument("--repo-root", type=Path, default=None)
    p.add_argument("--native-root", type=Path, default=None)
    args = p.parse_args(list(argv) if argv is not None else None)
    if not demo_push_e2e_enabled(cli_flag=bool(args.demo_push_e2e)):
        print("BLOCKED: set TRADEBOT_DEMO_PUSH_E2E=1 or pass --demo-push-e2e", file=sys.stderr)
        return 2
    os.environ[ENV_FLAG] = "1"
    native = args.native_root or Path(__file__).resolve().parents[2]
    repo = args.repo_root or native.parent
    summary = run_demo_push_full_certification(repo_root=repo, native_root=native)
    print(json.dumps({"verdict": summary.get("verdict"), "telemetry": summary.get("telemetry")}, indent=2))
    return 0 if summary.get("verdict") == "DEMO_PUSH_FULL_RUNTIME_PATH_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
