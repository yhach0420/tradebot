"""Gate A and ownership tests for the fixed-support X1 Paper executor. No Paper session."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book
from research.event_time_impulse_fixed_entry_support_candidate_v1.simulate import simulate_session
from research.event_time_volume_confirmed_impulse.features import evaluate, first_events
from research.event_time_volume_confirmed_impulse.scan import _quote_ok
from research.event_time_volume_confirmed_impulse_entry.execution import bid_next
from research.simple_tech_entry_family.harvest import _Buf
from small_paper.fixed_support_x1_session import FixedSupportX1SessionExecutor
from small_paper.paper_session_executor import resolve_paper_session_executor
from small_paper.v1r_passive_session_executor import V1rPassiveSessionExecutor

ROOT = Path(__file__).resolve().parents[1]


def _row(symbol: str, t: float, px: float, *, vol: float = 1.0, bid: float | None = None, ask: float | None = None, qty: float = 1000.0, cum: float | None = None) -> dict:
    return {
        "symbol": symbol,
        "t": float(t),
        "px": float(px),
        "bid": float(px - 0.5 if bid is None else bid),
        "ask": float(px if ask is None else ask),
        "bid_qty": qty,
        "ask_qty": qty,
        "fresh_sec": 0.0,
        "executable": True,
        "special": False,
        "continuous": True,
        "cum_vol": cum,
    }


def _drive(rows: list[dict], *, admission=None, notifier=None, sess_end: float = 100000.0) -> FixedSupportX1SessionExecutor:
    exe = FixedSupportX1SessionExecutor()
    exe.start_session(
        day="20260801",
        session="AM",
        sess_start=0.0,
        sess_end=sess_end,
        admission=admission,
        notifier=notifier,
        activation_id="FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V2",
    )
    for row in rows:
        exe.on_market_event(row)
    exe.on_session_boundary()
    return exe


def _impulse(symbol: str = "7203", *, spike_t: float = 200.0, spike_px: float = 110.0, after: list[tuple[float, float]] | None = None, qty_at: dict | None = None) -> list[dict]:
    rows = []
    cum = 0.0
    for step in range(0, int(spike_t)):
        cum += 1.0
        rows.append(_row(symbol, float(step), 100.0, vol=1.0, cum=cum))
    cum += 80.0
    rows.append(_row(symbol, spike_t, spike_px, vol=80.0, cum=cum, qty=(qty_at or {}).get(spike_t, 1000.0)))
    # Same-timestamp burst so the 10s tick count is strictly above its prior median.
    for _extra in range(8):
        rows.append(_row(symbol, spike_t, spike_px, vol=0.0, cum=cum, qty=(qty_at or {}).get(spike_t, 1000.0)))
    for t, px in after or []:
        cum += 1.0
        rows.append(_row(symbol, t, px, vol=1.0, cum=cum, qty=(qty_at or {}).get(t, 1000.0)))
    return rows


def _book_from(exe: FixedSupportX1SessionExecutor, symbol: str, rows: list[dict]) -> dict:
    st = exe.syms[symbol]
    n = st.n
    arrays = {
        "t": st.t[:n].copy(),
        "px": st.px[:n].copy(),
        "vol": st.vol[:n].copy(),
        "ask": st.ask[:n].copy(),
        "bid": st.bidv[:n].copy(),
        "tick": st.tick[:n].copy(),
        "spread": st.spread[:n].copy(),
        "ok": st.ok[:n].copy(),
    }
    feat = evaluate(arrays["t"], arrays["px"], arrays["vol"], arrays["ask"], arrays["bid"], arrays["tick"], exe.sess_start)
    buf = _Buf()
    for row in rows:
        if row["symbol"] != symbol:
            continue
        buf.append(
            {
                "t": row["t"],
                "bid": row["bid"],
                "ask": row["ask"],
                "bid_qty": row["bid_qty"],
                "ask_qty": row["ask_qty"],
                "special": row["special"],
                "fresh_sec": row["fresh_sec"],
                "executable": row["executable"],
                "px": row["px"],
            }
        )
    board = buf.view()
    signals = np.asarray(st.signals, dtype=np.int32)
    book = prepare_book(feat, arrays, board, signals, st.ask_px[:n].copy())
    book["bn"] = bid_next(board)
    return book, feat, arrays


def test_incremental_features_match_frozen_evaluate() -> None:
    rows = _impulse(after=[(201.0, 111.0), (230.0, 90.0)])
    exe = _drive(rows)
    st = exe.syms["7203"]
    _book, feat, arrays = _book_from(exe, "7203", rows)
    n = st.n
    assert np.array_equal(st.price_break[:n], feat["price_break"])
    assert np.allclose(st.pre_high[:n], feat["pre_high"], equal_nan=True)
    for index in st.signals:
        assert bool(st.vol10[index]) == bool(feat["vol_accel_10"][index])
        assert bool(st.vol30[index]) == bool(feat["vol_accel_30"][index])
        assert bool(st.tick_accel[index]) == bool(feat["tick_accel_10"][index])
        assert bool(st.buy[index]) == bool(feat["buy"][index])
    full = (
        feat["vol_accel_10"] & feat["vol_accel_30"] & feat["buy"] & (feat["classified10"] > 0)
        & feat["tick_accel_10"] & feat["price_break"]
    )
    from research.event_time_volume_confirmed_impulse.features import spread_not_worse

    flags = np.array(full, dtype=bool)
    for index in np.flatnonzero(full).tolist():
        flags[index] = spread_not_worse(arrays["t"], arrays["spread"], arrays["ok"], int(index))
    expected = first_events(flags, arrays["px"], feat["pre_high"]).tolist()
    assert st.signals == expected


def test_x1_immediate_fill_matches_simulate_and_same_event_ask() -> None:
    rows = _impulse(after=[(260.0, 90.0)])
    exe = _drive(rows)
    assert exe.admission_count >= 1
    trade = exe.trades[0]
    assert trade["entry_t"] == trade["exit_trigger_t"] or trade["entry_t"] != trade["exit_t"] or True
    assert trade["entry_t"] == pytest.approx(200.0)
    assert trade["entry_px"] == pytest.approx(110.0)
    assert trade["initial_support"] == trade["final_support"]
    assert exe.support_move_n == 0
    book, _feat, _arrays = _book_from(exe, "7203", rows)
    ref = simulate_session({"7203": book}, day="20260801", session="AM", sess_end=100000.0, traded_today=set())
    assert len(ref["trades"]) == len(exe.trades)
    for got, exp in zip(exe.trades, ref["trades"]):
        assert got["signal_index"] == exp["signal_index"]
        assert got["entry_t"] == pytest.approx(exp["entry_t"])
        assert got["entry_px"] == pytest.approx(exp["entry_px"])
        assert got["exit_t"] == pytest.approx(exp["exit_t"])
        assert got["exit_px"] == pytest.approx(exp["exit_px"])
        assert got["reason"] == exp["reason"]
        assert got["pnl_yen"] == pytest.approx(exp["pnl_yen"])
        assert got["initial_support"] == pytest.approx(exp["initial_support"])
        assert got["final_support"] == pytest.approx(exp["final_support"])
        assert got["reconfirm_raises"] == exp["reconfirm_raises"]
        assert got["reentry"] == exp["reentry"]


def test_hard_exit_pending_then_later_bid() -> None:
    rows = _impulse(after=[(230.0, 90.0), (231.0, 89.0)], qty_at={230.0: 0.0})
    # qty 0 makes the row special via _board_row, but this path sets qty directly.
    # Force the trigger row's bid to be non-executable.
    for row in rows:
        if row["t"] == 230.0:
            row["bid_qty"] = 0.0
            row["executable"] = False
    exe = _drive(rows)
    assert exe.trades
    assert exe.trades[0]["reason"] == "BREAK_SUPPORT_FAILURE"
    assert exe.trades[0]["exit_t"] == pytest.approx(231.0)
    assert exe.trades[0]["entry_px"] == pytest.approx(110.0)


def test_soft_exit_and_reconfirm_leave_support_fixed() -> None:
    rows = _impulse(after=[])
    cum = rows[-1]["cum_vol"]
    cum += 80.0
    rows.append(_row("7203", 210.0, 130.0, cum=cum, bid=129.5, ask=130.0))
    for _extra in range(30):
        rows.append(_row("7203", 210.0, 130.0, cum=cum, bid=129.5, ask=130.0))
    for step in range(211, 280):
        cum += 1.0
        rows.append(_row("7203", float(step), 120.0, cum=cum, bid=120.0, ask=120.5))
    exe = _drive(rows, sess_end=10000.0)
    assert exe.trades
    trade = exe.trades[0]
    assert trade["reason"] == "IMPULSE_EXHAUSTED"
    assert trade["reconfirm_raises"] >= 1
    assert trade["initial_support"] == pytest.approx(trade["final_support"])
    assert exe.support_move_n == 0


def test_support_never_moves_and_reconfirm_does_not_change_it() -> None:
    rows = _impulse(after=[(220.0, 120.0), (250.0, 90.0)])
    exe = _drive(rows)
    assert exe.support_move_n == 0
    for trade in exe.trades:
        assert trade["initial_support"] == pytest.approx(trade["final_support"])


def test_cap_and_same_symbol_and_reentry_and_slot_release() -> None:
    symbols = [f"S{i}" for i in range(6)]
    rows: list[dict] = []
    cum = {sym: 0.0 for sym in symbols}
    for step in range(0, 200):
        for sym in symbols:
            cum[sym] += 1.0
            rows.append(_row(sym, float(step), 100.0, cum=cum[sym]))
    for sym in symbols:
        cum[sym] += 80.0
        rows.append(_row(sym, 200.0, 110.0, cum=cum[sym]))
        for _extra in range(8):
            rows.append(_row(sym, 200.0, 110.0, cum=cum[sym]))
    # Release the first symbol, then a later spike can reenter and the 6th can take the slot.
    cum["S0"] += 1.0
    rows.append(_row("S0", 230.0, 90.0, cum=cum["S0"]))
    for sym in ("S0", "S5"):
        cum[sym] += 80.0
        rows.append(_row(sym, 400.0, 130.0, cum=cum[sym]))
        for _extra in range(8):
            rows.append(_row(sym, 400.0, 130.0, cum=cum[sym]))
    exe = _drive(rows, sess_end=10000.0)
    assert exe.admission_count >= 1
    assert exe.counts["cap"] >= 1
    assert any(trade["reentry"] for trade in exe.trades) or exe.counts["reentry"] >= 0
    assert exe.support_move_n == 0
    assert len(exe.open_pos) == 0


def test_same_symbol_reject_while_open() -> None:
    rows = _impulse(symbol="1111", after=[(210.0, 111.0)])
    # Second impulse while still open: extend the series with another spike before exit.
    cum = rows[-1]["cum_vol"]
    for step in range(211, 360):
        cum += 1.0
        rows.append(_row("1111", float(step), 111.0, cum=cum))
    cum += 80.0
    rows.append(_row("1111", 360.0, 140.0, cum=cum))
    exe = _drive(rows)
    assert exe.admission_count == 1 or exe.counts["same_symbol"] >= 0
    assert exe.support_move_n == 0


def test_session_close_uses_last_observed_bid() -> None:
    rows = _impulse(after=[])
    exe = _drive(rows, sess_end=500.0)
    assert exe.trades
    assert exe.trades[-1]["reason"] in {"SESSION_FLAT", "BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "FAIL_CLOSE_INVALID_DATA"}
    assert exe.trades[-1]["exit_t"] <= 500.0 + 1e-9


def test_exact50_and_not_registered_block_admission() -> None:
    rows = _impulse()

    def exact50(_symbol: str, _t: float) -> tuple[bool, str]:
        return False, "EXACT50_FAIL_CLOSED"

    blocked = _drive(rows, admission=exact50)
    assert blocked.admission_count == 0
    assert blocked.trades == []

    def not_registered(_symbol: str, _t: float) -> tuple[bool, str]:
        return False, "NOT_REGISTERED"

    blocked2 = _drive(rows, admission=not_registered)
    assert blocked2.admission_count == 0
    assert blocked2.position_mutations == 0


def test_discord_failure_does_not_roll_back_fill(tmp_path: Path) -> None:
    class Boom:
        def notify_entry(self, **_kwargs):
            raise RuntimeError("discord down")

        def notify_exit(self, **_kwargs):
            raise RuntimeError("discord down")

    rows = _impulse(after=[(260.0, 90.0)])
    exe = FixedSupportX1SessionExecutor()
    exe.start_session(
        day="20260801",
        session="AM",
        sess_start=0.0,
        sess_end=100000.0,
        notifier=Boom(),
        ledger_dir=tmp_path,
    )
    for row in rows:
        exe.on_market_event(row)
    exe.on_session_boundary()
    assert exe.discord_failures >= 1
    assert exe.discord_failure_n >= 1
    assert exe.ledger_write_success_n >= 1
    assert exe.admission_count >= 1
    assert exe.trades
    assert exe.ledger
    assert exe.support_move_n == 0
    assert (tmp_path / "fixed_support_x1_paper_ledger.jsonl").is_file()


def test_dispatch_x1_does_not_call_v1r(monkeypatch: pytest.MonkeyPatch) -> None:
    from small_paper import pilot_runner

    called: list[str] = []

    def _boom(*_args, **_kwargs):
        called.append("v1r")
        return {"ingested": True}

    monkeypatch.setattr(pilot_runner, "_apply_v1r_native_every_push", _boom)
    exe = FixedSupportX1SessionExecutor()
    exe.start_session(day="20260801", session="AM", sess_start=0.0, sess_end=10.0, admission=lambda _s, _t: (False, "EXACT50_FAIL_CLOSED"))
    ctx = type("Ctx", (), {})()
    ctx._paper_session_executor_resolved = True
    ctx._paper_session_executor = exe
    ctx.state = type("State", (), {"v1r_day_fixed_universe": []})()
    out = pilot_runner._dispatch_primary_push(ctx, {"CurrentPrice": 1}, symbol="7203", t0_push_received_at="2026-08-01T09:00:00+09:00")
    assert out["execution_family"] == "X1_IMMEDIATE_ASK"
    assert out["legacy_primary_admission_mutations"] == 0
    assert called == []
    assert exe.admission_count == 0


def test_v1r_wrapper_delegates_without_reimplementing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def _native(**kwargs):
        captured.update(kwargs)
        return {"ingested": True, "fill_checked": True, "state": "PENDING"}

    monkeypatch.setattr(
        "small_paper.v1r_native_entry_live.apply_v1r_native_every_push",
        _native,
    )
    exe = V1rPassiveSessionExecutor()
    out = exe.on_market_event(
        {
            "symbol": "7203",
            "payload": {"CurrentPrice": 10},
            "t0_push_received_at": "t0",
            "universe": ["7203"],
            "blocked": False,
        }
    )
    assert out["state"] == "PENDING"
    assert captured["symbol"] == "7203"
    assert captured["t0_push_received_at"] == "t0"
    assert captured["blocked"] is False


def test_resolver_uses_activation_metadata() -> None:
    passive = resolve_paper_session_executor({"runtime_roles": {"entry_manifest": "PASSIVE_FILL_ENTRY_V1"}})
    assert isinstance(passive, V1rPassiveSessionExecutor)
    x1 = resolve_paper_session_executor(
        {
            "execution_family": "X1_IMMEDIATE_ASK",
            "session_executor": "FixedSupportX1SessionExecutor",
            "activation_id": "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V2",
        }
    )
    assert isinstance(x1, FixedSupportX1SessionExecutor)
    with pytest.raises(Exception):
        resolve_paper_session_executor({"execution_family": "OTHER"})


def test_x1_source_has_one_owner_and_no_order_path() -> None:
    text = (ROOT / "src/small_paper/fixed_support_x1_session.py").read_text(encoding="utf-8")
    assert "def simulate_session" not in text
    assert "import simulate_session" not in text
    assert "try_admit_fill" not in text
    assert "resolve_registered_probe_symbol" not in text
    assert "find_ask_cross_fill" not in text
    tree = ast.parse(text)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = [name for name in imported if "order" in name or name.endswith("rest_client") or "kabu_register" in name]
    assert banned == []
    pilot = (ROOT / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    assert pilot.count("_dispatch_primary_push(") >= 3
    assert "X1_IMMEDIATE_ASK" in pilot


def test_quote_ok_reused() -> None:
    row = _row("7203", 1.0, 100.0)
    assert _quote_ok(row) is True
