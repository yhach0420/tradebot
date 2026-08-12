"""Startup path: AM bind → Ingress → PUT/synthetic register → bus → consumer → native READY."""
from __future__ import annotations

import csv
import threading
import time
from pathlib import Path

import pytest

from small_paper.day_fixed_am_registration import bind_same_day_am_desired_universe
from small_paper.market_ingress_service import MarketIngressService
from small_paper.paper_market_bus_consumer import PaperMarketBusBridge
from small_paper.v1r_live_dual_lane import ensure_dual_lane, reset_dual_lane_for_tests
from small_paper.v1r_native_entry_live import (
    boot_v1r_native_entry,
    reset_native_entry_for_tests,
    resolve_day_fixed_am_runtime_universe,
    set_native_entry,
)
from small_paper.v1r_pbv2_duplicate_runtime import list_live_ingress


def _am_syms(n: int = 50) -> list[str]:
    return [f"{1000 + i}" for i in range(n)]


def _write_am_csv(root: Path, day: str, symbols: list[str]) -> Path:
    path = root / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, bare in enumerate(symbols):
        slot = "core" if i < 10 else "dynamic"
        rows.append(
            {
                "symbol": f"{bare}.T",
                "symbol_key": f"{bare}@1",
                "exchange": "1",
                "passed": "True",
                "source_bucket": "core10_discord" if slot == "core" else "vol_liq_dynamic40",
                "selected_reason": slot,
                "universe_slot": slot,
                "rank": str(i + 1),
                "am_pm_session": "am",
            }
        )
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def _board(sym: str, px: float = 100.0) -> dict:
    return {
        "Symbol": sym,
        "CurrentPrice": px,
        "CurrentPriceTime": "2026-08-13T09:05:00+09:00",
        "TradingVolume": 1000,
        "Buy1": {"Price": px - 1, "Qty": 100},
        "Sell1": {"Price": px + 1, "Qty": 100},
    }


def _wait(pred, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_startup_am_bind_ingress_bus_consumer_native_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V1R_EXIT_V2_LIVE_PRIMARY", "1")
    day = "20260813"
    am = _am_syms()
    _write_am_csv(tmp_path, day, am)
    # Prior-day contamination present before bind.
    from small_paper.ingress_control_channel import write_desired_universe

    write_desired_universe(tmp_path, symbols=[f"{2000 + i}" for i in range(50)], trading_date="20260812")

    bind = bind_same_day_am_desired_universe(tmp_path, day)
    assert bind["ok"] is True
    assert bind["symbol_count"] == 50

    port = 18973
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date=day,
        synthetic=True,
        enable_tcp_bus=True,
        bus_port_override=port,
        silence_stale_sec=60.0,
    )
    svc.start()
    assert _wait(lambda: svc.bus.listening)
    assert len(svc.registered_symbols) == 50
    assert svc.registered_symbols == am
    assert svc._desired_source_trading_date == day

    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=port,
        ingress_session_id=svc.session_id,
        native_root=tmp_path,
        trading_date=day,
    )
    assert bridge.start()
    assert _wait(lambda: svc.bus.publisher_health().get("tcp_clients", 0) >= 1)

    stop = threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                item = bridge.q.get(timeout=0.05)
            except Exception:
                continue
            bridge.process_queue_item(item, handler=lambda _p: None)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    for i, sym in enumerate(am[:8]):
        svc.inject_payload(_board(sym, px=100 + i))
    assert _wait(lambda: int(svc.health_snapshot().get("paper_consumer_last_ack") or 0) >= 8)

    snap = svc.health_snapshot()
    assert snap.get("paper_consumer_connected") is True or svc.bus.publisher_health().get("tcp_clients", 0) >= 1
    assert int(snap.get("registered_symbol_count") or 0) == 50
    assert int(snap.get("desired_symbol_count") or 0) == 50
    hb1 = str(snap.get("at") or "")
    time.sleep(0.3)
    svc._write_status()
    hb2 = str(svc.health_snapshot().get("at") or "")
    assert hb2 >= hb1

    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    # Synthetic register writes manifest via poll; if not, membership may be AM-only.
    if resolved.get("ingress_count"):
        assert resolved["ok"] is True
        assert resolved["ingress_match"] is True
    else:
        assert resolved["am_count"] == 50

    reset_native_entry_for_tests()
    reset_dual_lane_for_tests()
    native = boot_v1r_native_entry(
        universe=list(resolved.get("symbols") or am),
        universe_source=str(resolved.get("source") or "am_csv"),
    )
    set_native_entry(native)
    snap_n = native.snapshot()
    assert snap_n.get("submit_cancel_live") == "0/0/0"
    assert native.ready is True

    live = list_live_ingress(trading_date=day, native_root=tmp_path)
    # In-process service is not a separate python -m process.
    assert isinstance(live, list)

    stop.set()
    bridge.stop()
    svc.stop()
    assert _wait(lambda: not svc.bus.listening, timeout=3.0)
    assert list_live_ingress(trading_date=day, native_root=tmp_path) == []
