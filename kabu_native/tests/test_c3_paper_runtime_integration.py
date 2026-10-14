"""C3 through the Paper launcher, PUSH dispatch, and AM/PM session close.

The production mode file and residual ledger are not modified.
Event time drives the book. There is no wall-clock sleep and no order API.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from research.anchor_timing_robustness.grid import hm_epoch
from small_paper.paper_session_executor import (
    UnresolvedPaperExecutor,
    X1_OWNER_ENV,
    bind_selected_live_executor,
)

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
JST = ZoneInfo("Asia/Tokyo")
DAY = "20261002"
SAVED_PUSH = (
    NATIVE
    / "data"
    / "market_capture"
    / "20260728"
    / "session_ing_20260728_33164_1785194889_f13718ce"
    / "push_part_0001.jsonl"
)
PROD_MODE = NATIVE / "src" / "small_paper" / "c3_paper_mode.json"
PROD_RESIDUAL = NATIVE / "results" / "paper" / "c3_residual_ledger.json"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), JST).isoformat()


def _freeze() -> list[str]:
    names = ["2351", "7376", "5246", "9999", "8001", "8002", "8003", "8004", "8005", "8006"]
    names.extend(f"{8100 + i:04d}" for i in range(40))
    return names


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import small_paper.v1r_native_entry_live as native

    monkeypatch.setattr(
        native,
        "resolve_day_fixed_am_runtime_universe",
        lambda *_a, **_k: {
            "ok": True,
            "symbols": _freeze(),
            "source": "TEST_FREEZE",
            "authority": "SAME_DAY_AM_FROZEN_UNIVERSE",
        },
    )
    monkeypatch.setattr(
        "small_paper.kabu_registration_authority.verify_exact50_membership",
        lambda *_a, **_k: {"ok": True, "actual_n": 50},
    )
    monkeypatch.setattr(
        "small_paper.kabu_registration_authority.resolve_registered_probe_symbol",
        lambda *_a, **_k: {"ok": True, "kabu_probe_symbol_registered": True},
    )


def _board(
    t: float,
    px: float,
    cum: float,
    *,
    bid_qty: float = 1000.0,
    ask_qty: float = 1000.0,
    current_age: float = 0.0,
    buy1_age: float = 0.0,
    special: bool = False,
) -> dict:
    bid = float(px) - 0.5
    return {
        "CurrentPrice": float(px),
        "CurrentPriceTime": _iso(float(t) - float(current_age)),
        "CurrentPriceStatus": 1,
        "OpeningPrice": 100.0,
        "OpeningPriceTime": _iso(float(t) - 3600.0),
        "TradingVolume": float(cum),
        "Buy1": {"Price": bid, "Qty": float(bid_qty), "Time": _iso(float(t) - float(buy1_age))},
        "Sell1": {"Price": float(px), "Qty": float(ask_qty), "Time": _iso(float(t))},
        "SpecialQuote": "1" if special else "0",
    }


def _push(ctx, symbol: str, t: float, px: float, cum: float, **flags) -> None:
    from small_paper.pilot_runner import _dispatch_primary_push

    _dispatch_primary_push(
        ctx,
        _board(t, px, cum, **flags),
        symbol=f"{symbol}.T" if not str(symbol).endswith(".T") else str(symbol),
        t0_push_received_at=_iso(t),
    )


def _impulse(ctx, symbol: str, t0: float, *, spike_px: float = 110.0, base_px: float = 100.0) -> float:
    cum = 0.0
    for step in range(200):
        cum += 1.0
        _push(ctx, symbol, t0 + step, base_px, cum)
    cum += 80.0
    spike = t0 + 200.0
    _push(ctx, symbol, spike, spike_px, cum)
    for _extra in range(8):
        _push(ctx, symbol, spike, spike_px, cum)
    return cum


def _boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from small_paper.pilot_runner import _init_v1r_native_entry_for_live

    _patch_runtime(monkeypatch)
    state = SimpleNamespace(
        trading_date=DAY,
        v1r_day_fixed_universe=[],
        v1r_native_entry_blocked=False,
        v1r_native_block_reason="",
        paper_session_executor=None,
        session_owner_class="",
    )
    writer = SimpleNamespace(output_dir=tmp_path)
    wiring = _init_v1r_native_entry_for_live(
        state=state,
        writer=writer,
        native_root=NATIVE,
        trading_date=DAY,
        session_symbols=_freeze(),
    )
    return state, wiring, SimpleNamespace(state=state, native_root=NATIVE)


def _saved_push_record() -> tuple[str, str, dict]:
    line = SAVED_PUSH.open(encoding="utf-8").readline()
    record = json.loads(line)
    payload = dict(record["payload"])
    return str(record["symbol"]), str(record["received_at"]), payload


def test_launcher_selects_c3_and_disabled_mode_stays_on_x1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import small_paper.v1r_native_entry_live as native
    import small_paper.v1r_paper_primary_launcher as launcher

    before_mode = PROD_MODE.read_bytes()
    before_residual = PROD_RESIDUAL.read_bytes() if PROD_RESIDUAL.is_file() else None
    bat = (REPO / "run_paper_trade.bat").read_text(encoding="utf-8")
    ps1 = (NATIVE / "scripts" / "run_paper_trade_checked.ps1").read_text(encoding="utf-8")
    assert "python -m small_paper.v1r_paper_primary_launcher --mode live" in bat
    assert "c3_paper_mode" not in bat
    assert "paper_trade_checked_runner" in ps1
    assert "TRADEBOT_PAPER_STRATEGY_MODE_PATH" not in ps1

    mode = tmp_path / "c3_on.json"
    mode.write_text(
        json.dumps({"selected": "C3", "enabled": True, "restore": "FIXED_SUPPORT_X1"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADEBOT_PAPER_STRATEGY_MODE_PATH", str(mode))
    monkeypatch.setenv("TRADEBOT_C3_RESIDUAL_PATH", str(tmp_path / "residual.json"))
    monkeypatch.delenv(X1_OWNER_ENV, raising=False)
    _patch_runtime(monkeypatch)
    calls = {"boot": 0, "popen": 0}

    def boom(*_a, **_k):
        calls["boot"] += 1
        raise AssertionError("boot_v1r_native_entry")

    class _Proc:
        def poll(self):
            return 0

    def _popen(*_a, **_k):
        calls["popen"] += 1
        return _Proc()

    monkeypatch.setattr(native, "boot_v1r_native_entry", boom)
    monkeypatch.setattr(launcher.subprocess, "Popen", _popen)
    out = tmp_path / "c3_launch"
    out.mkdir()
    code = launcher._run_daily_live(out, out / "heartbeat.jsonl", SimpleNamespace())
    owner = json.loads((out / "live_session_owner.json").read_text(encoding="utf-8"))
    cmd = json.loads((out / "daily_runner_cmd.json").read_text(encoding="utf-8"))
    assert owner["session_owner_class"] == "C3PaperSessionExecutor"
    assert owner["execution_family"] == "X1_IMMEDIATE_ASK"
    assert owner["boot_v1r_native_entry"] == 0
    assert calls["boot"] == 0
    assert calls["popen"] == 1
    assert "run_core10_dynamic40_am_pm_daily_runner.py" in " ".join(cmd["cmd"])
    assert (out / "heartbeat.jsonl").is_file()
    assert code == 4

    disabled = tmp_path / "c3_off.json"
    disabled.write_text(
        json.dumps({"selected": "C3", "enabled": False, "restore": "FIXED_SUPPORT_X1"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADEBOT_PAPER_STRATEGY_MODE_PATH", str(disabled))
    monkeypatch.delenv(X1_OWNER_ENV, raising=False)
    out_x1 = tmp_path / "x1_launch"
    out_x1.mkdir()
    launcher._run_daily_live(out_x1, out_x1 / "heartbeat.jsonl", SimpleNamespace())
    owner_x1 = json.loads((out_x1 / "live_session_owner.json").read_text(encoding="utf-8"))
    assert owner_x1["session_owner_class"] == "FixedSupportX1SessionExecutor"
    assert PROD_MODE.read_bytes() == before_mode
    after_residual = PROD_RESIDUAL.read_bytes() if PROD_RESIDUAL.is_file() else None
    assert after_residual == before_residual


def test_am_pm_runner_path_entries_exits_cap_and_residual(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import small_paper.v1r_native_entry_live as native
    from notify.x1_discord_gate import stamp_x1_summary_identity
    from small_paper.live_writer import LiveSessionWriter

    before_mode = PROD_MODE.read_bytes()
    before_residual = PROD_RESIDUAL.read_bytes() if PROD_RESIDUAL.is_file() else None
    mode = tmp_path / "c3_on.json"
    residual = tmp_path / "residual.json"
    mode.write_text(
        json.dumps({"selected": "C3", "enabled": True, "restore": "FIXED_SUPPORT_X1"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADEBOT_PAPER_STRATEGY_MODE_PATH", str(mode))
    monkeypatch.setenv("TRADEBOT_C3_RESIDUAL_PATH", str(residual))
    from small_paper.paper_session_executor import arm_x1_live_owner

    arm_x1_live_owner()
    calls = {"boot": 0}

    def boom(*_a, **_k):
        calls["boot"] += 1
        raise AssertionError("boot_v1r_native_entry")

    monkeypatch.setattr(native, "boot_v1r_native_entry", boom)
    state, wiring, ctx = _boot(tmp_path / "am", monkeypatch)
    exe = state.paper_session_executor
    assert wiring["session_owner_class"] == "C3PaperSessionExecutor"
    assert wiring["boot_v1r_native_entry"] == 0
    assert wiring["passive_fill_boot"] == 0
    assert type(exe).__name__ == "C3PaperSessionExecutor"
    assert any(cls.__name__ == "BookC3" for cls in type(exe).__mro__)
    assert exe.buy1_fresh is True and exe.residual_mode is True
    assert state.v1r_native_entry_blocked is True
    assert calls["boot"] == 0

    am0 = float(hm_epoch(DAY, 9, 0))
    exe.bind_production_schedule(day=DAY, kind="am")
    assert exe.session == "AM"
    assert exe.sess_start == pytest.approx(am0)
    _impulse(ctx, "9999", am0, base_px=19000.0, spike_px=20000.0)
    _push(ctx, "9999", am0 + 210.0, 20000.0, 9000.0)
    assert exe.gross_cap_denied_n >= 1
    assert "9999.T" not in exe.open_pos
    cap_names = ["2351", "8001", "8002", "8003", "8004", "8005"]
    cum = {name: 0.0 for name in cap_names}
    origin = am0 + 220.0
    for step in range(200):
        for name in cap_names:
            cum[name] += 1.0
            _push(ctx, name, origin + step, 100.0, cum[name])
    spike = origin + 200.0
    for name in cap_names:
        cum[name] += 80.0
        _push(ctx, name, spike, 110.0, cum[name])
        for _extra in range(8):
            _push(ctx, name, spike, 110.0, cum[name])
    _push(ctx, cap_names[0], spike + 1.0, 110.0, cum[cap_names[0]] + 1.0)
    assert exe.counts["cap"] >= 1
    assert len(exe.open_pos) == 5
    same_before = int(exe.counts["same_symbol"])
    _impulse(ctx, "2351", spike + 10.0)
    _push(ctx, "2351", spike + 220.0, 110.0, 5000.0)
    assert int(exe.counts["same_symbol"]) > same_before
    crash = spike + 230.0
    _push(ctx, "2351", crash, 90.0, 10000.0, bid_qty=0.0)
    _push(ctx, "2351", crash + 1.0, 90.0, 10001.0, current_age=1800.0, buy1_age=0.0, bid_qty=100.0)
    _push(ctx, "2351", crash + 2.0, 90.0, 10002.0, bid_qty=100.0)
    closed = [trade for trade in exe.trades if trade["symbol"] == "2351.T"]
    assert closed
    assert closed[-1]["qty"] == 100.0
    assert closed[-1]["entry_px"] == pytest.approx(110.0)
    assert closed[-1]["exit_px"] == pytest.approx(89.5)
    assert "2351.T" not in exe.open_pos
    re_t = am0 + 720.0
    re_cum = 20000.0
    for step in range(200):
        re_cum += 1.0
        _push(ctx, "2351", re_t + step, 100.0, re_cum)
    re_cum += 80.0
    _push(ctx, "2351", re_t + 200.0, 130.0, re_cum)
    for _extra in range(8):
        _push(ctx, "2351", re_t + 200.0, 130.0, re_cum)
    _push(ctx, "2351", re_t + 210.0, 130.0, re_cum + 1.0)
    assert exe.counts["reentry"] >= 1
    perf = exe.close_at_runtime_boundary(day=DAY, kind="am")
    assert perf["submit_cancel_live"] == "0/0/0"
    assert exe.snapshot_status()["live_order_enabled"] is False

    pm_state, _pm_wiring, pm_ctx = _boot(tmp_path / "pm", monkeypatch)
    pm = pm_state.paper_session_executor
    assert type(pm).__name__ == "C3PaperSessionExecutor"
    assert state.paper_session_executor is not pm
    pm.bind_safe_pm_flatten(day=DAY)
    assert pm.flatten_start == pytest.approx(float(hm_epoch(DAY, 15, 20)))
    assert pm.flatten_deadline == pytest.approx(float(hm_epoch(DAY, 15, 25)))
    pm0 = float(hm_epoch(DAY, 12, 30))
    held_cum = {"7376": 0.0, "5246": 0.0}
    for step in range(200):
        for name in ("7376", "5246"):
            held_cum[name] += 1.0
            _push(pm_ctx, name, pm0 + step, 100.0, held_cum[name])
    pm_spike = pm0 + 200.0
    for name in ("7376", "5246"):
        held_cum[name] += 80.0
        _push(pm_ctx, name, pm_spike, 110.0, held_cum[name])
        for _extra in range(8):
            _push(pm_ctx, name, pm_spike, 110.0, held_cum[name])
    flat_at = float(hm_epoch(DAY, 15, 20)) + 1.0
    _push(pm_ctx, "7376", flat_at, 110.0, 5000.0, bid_qty=100.0)
    _push(pm_ctx, "5246", flat_at, 110.0, 5000.0, special=True, bid_qty=100.0)
    pm_perf = pm.close_at_runtime_boundary(day=DAY, kind="pm")
    assert pm_perf["submit_cancel_live"] == "0/0/0"
    flat = [trade for trade in pm.trades if trade["symbol"] == "7376.T" and trade["reason"] == "SESSION_FLAT"]
    assert flat
    assert flat[0]["qty"] == 100.0
    assert "5246.T" not in pm.open_pos
    saved = json.loads(residual.read_text(encoding="utf-8"))
    held = [row for row in saved["residuals"] if row["symbol"] == "5246.T"]
    assert held
    assert held[0]["remaining_qty"] == 100.0
    assert held[0]["capital_occupied"] == pytest.approx(held[0]["entry_px"] * 100.0)
    assert held[0]["pnl_yen"] is None
    assert held[0]["slot_release_economic"] is False

    writer = LiveSessionWriter(tmp_path / "summary", incremental=True, event_fields=["symbol"], async_io=False)
    summary = {"source": "c3_runtime_integration", "submit_cancel_live": "0/0/0"}
    stamp_x1_summary_identity(summary, pm)
    writer.append_heartbeat({"heartbeat_index": 1, "open_n": 0, **summary})
    writer.close()
    heartbeat = (tmp_path / "summary" / "heartbeat.jsonl").read_text(encoding="utf-8")
    assert "0/0/0" in heartbeat
    assert summary["x1_executor"]["x1_entry_n"] >= 1

    restarted, _wiring, restarted_ctx = _boot(tmp_path / "restart", monkeypatch)
    again = restarted.paper_session_executor
    again.bind_production_schedule(day="20261005", kind="am")
    assert again.open_pos["5246.T"]["pending_reason"] == "CARRIED_UNFILLED"
    assert again.open_pos["5246.T"]["entry_px"] == pytest.approx(held[0]["entry_px"])
    occupied = again.open_pos["5246.T"]["entry_px"] * 100.0
    assert occupied == pytest.approx(held[0]["capital_occupied"])
    with pytest.raises(UnresolvedPaperExecutor, match="C3_RESIDUAL_BLOCKS_X1_RESTORE"):
        bind_selected_live_executor(
            {"execution_family": "X1_IMMEDIATE_ASK", "session_executor": "FixedSupportX1SessionExecutor"},
            strategy_mode="FIXED_SUPPORT_X1",
        )
    trades_before = len(again.trades)
    _push(restarted_ctx, "5246", float(hm_epoch("20261005", 9, 5)), 100.0, 10.0)
    assert "5246.T" in again.open_pos
    assert len(again.trades) == trades_before

    residual.write_text("{", encoding="utf-8")
    broken, _wiring, broken_ctx = _boot(tmp_path / "broken", monkeypatch)
    bad = broken.paper_session_executor
    bad.bind_production_schedule(day="20261005", kind="am")
    assert bad.residual_load_error
    allowed, reason = bad.admission("8001", 0.0)
    assert allowed is False and reason == "RESIDUAL_LEDGER_FAIL_CLOSED"
    _push(broken_ctx, "8001", float(hm_epoch("20261005", 9, 6)), 110.0, 80.0)
    assert bad.open_pos == {}
    assert residual.read_text(encoding="utf-8") == "{"

    missing_before = exe._seen_events
    from small_paper.pilot_runner import _dispatch_primary_push

    _dispatch_primary_push(ctx, {"CurrentPrice": 100.0}, symbol="2351.T", t0_push_received_at=None)
    assert exe._seen_events == missing_before

    symbol, received_at, payload = _saved_push_record()
    saved_state, _wiring, saved_ctx = _boot(tmp_path / "saved", monkeypatch)
    saved_exe = saved_state.paper_session_executor
    stamp = datetime.fromisoformat(received_at).timestamp()
    saved_exe.start_session(
        day="20260728",
        session="AM",
        sess_start=stamp - 5.0,
        sess_end=stamp + 5.0,
        admission=lambda _s, _t: (True, "PASS"),
    )
    out = _dispatch_primary_push(
        saved_ctx,
        payload,
        symbol=symbol,
        t0_push_received_at=received_at,
    )
    assert out["ingested"] is True
    assert out["execution_family"] == "X1_IMMEDIATE_ASK"
    assert saved_exe._seen_events >= 1
    assert type(saved_exe).__name__ == "C3PaperSessionExecutor"
    assert calls["boot"] == 0
    assert PROD_MODE.read_bytes() == before_mode
    after_residual = PROD_RESIDUAL.read_bytes() if PROD_RESIDUAL.is_file() else None
    assert after_residual == before_residual


def test_dual_start_lock_uses_a_temp_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import small_paper.v1r_paper_primary_launcher as launcher

    real = launcher.LOCK_PATH
    before = real.read_bytes() if real.is_file() else None
    monkeypatch.setattr(launcher, "LOCK_PATH", tmp_path / "primary.lock")
    assert launcher._acquire_single_primary_lock() is True
    assert launcher._acquire_single_primary_lock() is False
    launcher._release_lock()
    after = real.read_bytes() if real.is_file() else None
    assert after == before
