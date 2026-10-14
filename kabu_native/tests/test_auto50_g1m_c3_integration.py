"""AUTO50_G1M universe switch plus a C3 paper session. No live orders."""
from __future__ import annotations

import csv
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from runner.am_pm_daily_runner import DailyRunnerOptions, DailyRunnerState, build_am_universe, build_pm_universe
from small_paper.auto50_g1m_selector import G_YEN, RESTORE_SELECTOR, SELECTOR_ID, read_selector_mode, select_auto50_g1m

NATIVE = Path(__file__).resolve().parents[1]
DAY = "20261014"


def _features(path: Path, *, cheap: int, expensive: int, trade_date: str = "2026-10-14") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["symbol", "exchange", "market", "close", "volume", "trading_value", "atr_pct", "trade_date"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in range(cheap):
            writer.writerow(
                {
                    "symbol": f"{1000 + index}.T",
                    "exchange": "1",
                    "market": "prime",
                    "close": "1000",
                    "volume": "100000",
                    "trading_value": "100000000",
                    "atr_pct": "2.5",
                    "trade_date": trade_date,
                }
            )
        for index in range(expensive):
            writer.writerow(
                {
                    "symbol": f"{8000 + index}.T",
                    "exchange": "1",
                    "market": "prime",
                    "close": "20000",
                    "volume": "100000",
                    "trading_value": "2000000000",
                    "atr_pct": "3.0",
                    "trade_date": trade_date,
                }
            )


def _state(tmp_path: Path) -> DailyRunnerState:
    reports = tmp_path / "results" / "reports"
    reports.mkdir(parents=True)
    return DailyRunnerState(
        options=DailyRunnerOptions(day_stamp=DAY, generate_features=False, dry_run_only=True, skip_kabu=True),
        repo_root=tmp_path,
        native_root=tmp_path,
        reports_dir=reports,
        push_root=tmp_path / "push",
        trade_date=date(2026, 10, 14),
    )


def _enable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mode = tmp_path / "selector.json"
    mode.write_text(
        json.dumps({"selected": SELECTOR_ID, "enabled": True, "restore": RESTORE_SELECTOR}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADEBOT_UNIVERSE_SELECTOR_MODE_PATH", str(mode))


def test_production_mode_is_auto50_and_disabled_stays_fail_closed(tmp_path: Path) -> None:
    assert read_selector_mode(NATIVE / "src" / "small_paper" / "universe_selector_mode.json") == SELECTOR_ID
    disabled = tmp_path / "selector.json"
    disabled.write_text(
        json.dumps({"selected": SELECTOR_ID, "enabled": False, "restore": RESTORE_SELECTOR}),
        encoding="utf-8",
    )
    assert read_selector_mode(disabled) == RESTORE_SELECTOR


def test_g1m_fail_closed_under_50_and_drops_over_g(tmp_path: Path) -> None:
    path = tmp_path / "features.csv"
    _features(path, cheap=10, expensive=5)
    import csv as csvmod

    rows = list(csvmod.DictReader(path.open(encoding="utf-8")))
    denied = select_auto50_g1m(rows)
    assert denied["status"] == "AUTO50_G1M_FAIL_CLOSED"
    assert denied["reason"] == "UNDER_50"
    assert denied["symbols"] == []
    _features(path, cheap=60, expensive=10)
    rows = list(csvmod.DictReader(path.open(encoding="utf-8")))
    chosen = select_auto50_g1m(rows)
    assert chosen["status"] == "TOP50"
    assert len(chosen["symbols"]) == 50
    assert chosen["rejected_budget_n"] == 10
    assert all(not symbol.startswith("8000") for symbol in chosen["symbols"])
    assert all(float(row["one_lot_notional"]) <= G_YEN for row in chosen["rows"])


def test_runner_am_pm_same_50_and_c3_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mode_before = (NATIVE / "src" / "small_paper" / "c3_paper_mode.json").read_bytes()
    selector_before = (NATIVE / "src" / "small_paper" / "universe_selector_mode.json").read_bytes()
    residual_before = (NATIVE / "results" / "paper" / "c3_residual_ledger.json").read_bytes()
    _enable(tmp_path, monkeypatch)
    state = _state(tmp_path)
    _features(state.reports_dir / f"features_{DAY}.csv", cheap=60, expensive=8)
    am = build_am_universe(state)
    assert am["ok"] is True, am
    assert am["exact50"] is True
    assert am["selector_version"] == "AUTO50_G1M_CAUSAL_BASELINE_READY_V1"
    assert am["gross_budget_yen"] == 1_000_000
    assert am["qty"] == 100
    assert len(am["symbols"]) == 50
    pm = build_pm_universe(state)
    assert pm["ok"] is True
    assert pm["same_as_am"] is True
    assert pm["symbols"] == [row["symbol"] for row in am["am_rows"]]

    from small_paper.c3_paper_session import C3PaperSessionExecutor
    monkeypatch.setenv("TRADEBOT_C3_RESIDUAL_PATH", str(tmp_path / "residual.json"))
    from notify.x1_discord_gate import force_x1_discord_routing, reset_x1_discord_routing_for_tests
    from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

    reset_x1_discord_routing_for_tests()
    sends: list[str] = []

    class _Router:
        def publish(self, envelope):
            sends.append(str(envelope.event_type))
            return {"status": "QUEUED", "queued": True}

        class worker:
            @staticmethod
            def enqueue(*_a, **_k):
                raise AssertionError("direct enqueue is not an X1 send")

    monkeypatch.setattr("notify.discord_notification_router.get_router", lambda *_a, **_k: _Router())
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://example.invalid/auto50-c3")
    book = C3PaperSessionExecutor()
    book.notifier = SmallPaperDiscordNotifier(
        SmallPaperDiscordConfig(enabled=True, observer_only=True),
        profile="paper",
        entry_profile="paper",
    )
    membership = [row["symbol"] for row in am["am_rows"]]

    def admit(symbol: str, _t: float):
        return (symbol in membership, "AUTO50_G1M_EXACT50" if symbol in membership else "NOT_REGISTERED")

    book.admission = admit
    book.admission_membership = membership
    from small_paper.paper_primary_activation import ACTIVATION_V9_ID

    book.bind_production_schedule(day=DAY, kind="am")
    book.activation_id = ACTIVATION_V9_ID
    force_x1_discord_routing(True, activation_id=ACTIVATION_V9_ID)
    book.ledger_path = tmp_path / "ledger.jsonl"
    assert len(book.admission_membership) == 50
    assert book.gross_cap == 1_000_000
    from research.anchor_timing_robustness.grid import hm_epoch

    t0 = float(hm_epoch(DAY, 9, 0))
    symbol = membership[0]
    spec = importlib.util.spec_from_file_location(
        "c3_paper_runtime_integration",
        NATIVE / "tests" / "test_c3_paper_runtime_integration.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    from types import SimpleNamespace

    ctx = SimpleNamespace(state=SimpleNamespace(paper_session_executor=book))
    code = symbol[:-2] if symbol.endswith(".T") else symbol
    cum = module._impulse(ctx, code, t0)
    module._push(ctx, code, t0 + 202.0, 90.0, cum + 1.0)
    module._push(ctx, code, t0 + 203.0, 90.0, cum + 2.0)
    assert book.live_order_enabled is False
    snap = book.snapshot_status()
    perf = book.x1_performance()
    assert snap["submit"] == 0 and snap["cancel"] == 0 and snap["live"] == 0
    assert book.session_executor == "FixedSupportX1SessionExecutor"
    assert perf["entry_n"] == 1, perf
    assert perf["exit_n"] == 1, perf
    assert perf["open_n"] == 0, perf
    assert book.live_order_enabled is False
    assert sends == ["X1_PAPER_ENTRY", "X1_PAPER_EXIT"], {
        "sends": sends,
        "discord_failures": book.discord_failures,
        "activation": book.activation_id,
    }
    mode_file = NATIVE / "src" / "small_paper" / "c3_paper_mode.json"
    selector_file = NATIVE / "src" / "small_paper" / "universe_selector_mode.json"
    residual_file = NATIVE / "results" / "paper" / "c3_residual_ledger.json"
    assert mode_before == mode_file.read_bytes()
    assert selector_before == selector_file.read_bytes()
    assert residual_before == residual_file.read_bytes()
    reset_x1_discord_routing_for_tests()


def test_future_feature_bar_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(tmp_path, monkeypatch)
    state = _state(tmp_path)
    _features(state.reports_dir / f"features_{DAY}.csv", cheap=60, expensive=0, trade_date="2026-10-15")
    built = build_am_universe(state)
    assert built["ok"] is False
    assert built.get("reason") == "FUTURE_FEATURE_BAR", built


def test_cap_state_zero_is_flat_not_missing() -> None:
    path = NATIVE / "results" / "research" / "v19_actual_paper_e2e_parity_20261013" / "audit_actual.py"
    spec = importlib.util.spec_from_file_location("audit_actual", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.cap_state_value({"cap_state": 0}) == 0
    assert module.cap_state_value({"cap_state": 5}) == 5
    assert module.cap_state_value({}) is None
