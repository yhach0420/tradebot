"""Separate-PID Paper Market Bus consumer for DEMO E2E (TCP + ACK).

Mirrors pilot V2 receive → process → ACK path without live order / Discord.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from small_paper.market_ingress_protocol import now_iso
from small_paper.paper_market_bus_consumer import PaperMarketBusBridge


def _health_path(native_root: Path) -> Path:
    return Path(native_root) / "runtime" / "paper_demo_worker_health.json"


def _write_health(native_root: Path, payload: dict[str, Any]) -> None:
    path = _health_path(native_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_worker(
    *,
    native_root: Path,
    host: str,
    port: int,
    ingress_session_id: str = "",
) -> int:
    bridge = PaperMarketBusBridge(
        host=host,
        port=int(port),
        consumer_id="paper_runtime",
        ingress_session_id=ingress_session_id,
    )
    ok = bridge.start()
    stop = threading.Event()
    stats = {
        "market_processed": 0,
        "control_seen": 0,
        "process_errors": 0,
        "shadow_ticks": 0,
        "started_at": now_iso(),
        "pid": os.getpid(),
        "connected": bool(ok),
        "transport": bridge.consumer.transport,
    }

    def _handler(payload: dict[str, Any]) -> None:
        # Count shadow-eligible market ticks (observe-only; no strategy change)
        stats["shadow_ticks"] += 1
        try:
            from small_paper.shadow_registry import shadow_portfolio_status

            _ = shadow_portfolio_status()
        except Exception:
            pass

    def _loop() -> None:
        while not stop.is_set():
            try:
                item = bridge.q.get(timeout=0.1)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            kind = str(item.get("__kind__") or item.get("kind") or "")
            if kind and kind not in ("market", "MARKET", ""):
                # control envelopes still must ACK if processed as bus messages
                stats["control_seen"] += 1
            ok_p = bridge.process_queue_item(item, handler=_handler)
            if ok_p:
                stats["market_processed"] += 1
            else:
                stats["process_errors"] += 1

    t = threading.Thread(target=_loop, name="paper-demo-worker", daemon=True)
    t.start()

    def _sig(*_a: Any) -> None:
        stop.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _sig)
        except Exception:
            pass

    try:
        while not stop.is_set():
            h = bridge.health()
            _write_health(
                native_root,
                {
                    **stats,
                    "at": now_iso(),
                    "health": h,
                    "ready": bool(h.get("paper_consumer_ready") or h.get("ready")),
                    "last_ack_sequence": h.get("last_ack_sequence"),
                    "messages": h.get("messages"),
                    "queue_depth": h.get("queue_depth"),
                },
            )
            time.sleep(0.25)
    finally:
        stop.set()
        bridge.stop()
        _write_health(
            native_root,
            {**stats, "at": now_iso(), "stopped": True, "health": bridge.health()},
        )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Paper demo TCP bus worker")
    p.add_argument("--native-root", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--ingress-session-id", default="")
    args = p.parse_args(argv)
    return run_worker(
        native_root=Path(args.native_root),
        host=str(args.host),
        port=int(args.port),
        ingress_session_id=str(args.ingress_session_id or ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
