"""Focused tests for 20260819 OPVAL permanent repair (A/B/C/D).

Does not start Formal certification, G6, or 48x parity.
Does not rewrite original 20260819 session artifacts.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from api.rest_client import KabuNativeApiError
from small_paper.canonical_lifecycle_integrity import (
    attach_lifecycle_integrity,
    reconcile_canonical_lifecycle,
)
from small_paper.day_fixed_am_registration import (
    freeze_same_day_am_universe,
    load_frozen_am_universe,
)
from small_paper.ingress_control_channel import write_desired_universe
from small_paper.kabu_registration_authority import verify_exact50_membership
from small_paper.market_ingress_service import MarketIngressService
from small_paper.pre_freeze_kabu_validation import (
    AUTH_NOT_READY,
    INVALID_SYMBOL,
    RATE_LIMIT,
    REGISTER_CAPACITY,
    TRANSPORT_FAILURE,
    VALID_SYMBOL,
    classify_board_probe_error,
    freeze_valid50_after_kabu_validation,
    load_ranked_candidate_pool,
    ranker_exclude_set,
    select_valid50_from_ranked,
)
from small_paper.registration_attempt import (
    PARTIAL_UNCONFIRMED,
    TEMPORARY_FAILED,
    desired_universe_sha,
    should_attempt_register,
)
from small_paper.session_validity import VALID_SESSION, classify_session_validity

NATIVE = Path(__file__).resolve().parents[1]
AM_SUMMARY = NATIVE / "results" / "small_paper" / "20260819" / "live_session_093054" / "small_paper_summary.json"
PM_SUMMARY = NATIVE / "results" / "small_paper" / "20260819" / "live_session_122513" / "small_paper_summary.json"
AM_SHA = "d6fba16df14b721c367c2432b55e140725ca24a8694d6a37e0ff8b3041fce214"
PM_SHA = "0eb0c965f940c0e623a0c0ac00f741b48ea747378b3d44caf06402ffd23098c5"


def _valid_ops(**overrides: Any) -> dict[str, Any]:
    body = {
        "stop_reason": "morning_session_close",
        "push_messages": 10,
        "gate_evaluations": 10,
        "heartbeat_count": 3,
        "runtime_sec": 120.0,
        "session_seal_status": "SEALED",
    }
    body.update(overrides)
    return body


def _ranked(n: int = 70) -> list[str]:
    return [f"{1000 + i:04d}" for i in range(n)]


class _FakePush:
    def __init__(self, *, fail_code: str = "", fail_temp: bool = False) -> None:
        self.calls: list[list[tuple[str, int]]] = []
        self.fail_code = fail_code
        self.fail_temp = fail_temp
        self.regist: list[tuple[str, int]] = []

    def register(self, symbols_spec: list[tuple[str, int]]) -> dict[str, Any]:
        self.calls.append(list(symbols_spec))
        if self.fail_code:
            raise KabuNativeApiError(f"register HTTP 400: {self.fail_code}", kabu_code=self.fail_code)
        if self.fail_temp:
            raise KabuNativeApiError("register HTTP 500: boom")
        have = {s for s, _ in self.regist}
        for s, ex in symbols_spec:
            if s not in have:
                self.regist.append((s, int(ex)))
        return {
            "RegistNum": len(symbols_spec),
            "Symbols": [{"Symbol": s, "Exchange": int(ex)} for s, ex in symbols_spec],
            "RegistList": [{"Symbol": s, "Exchange": int(ex)} for s, ex in symbols_spec],
        }

    def unregister_all(self) -> dict[str, Any]:
        self.regist = []
        return {"RegistNum": 0, "Symbols": [], "RegistList": []}

    def fetch_regist_list(self) -> dict[str, Any]:
        return {
            "ok": True,
            "readonly": True,
            "reason": "push_fetch_regist_list",
            "symbols": [s for s, _ in self.regist],
            "http_status": 200,
        }


def test_a1_opval_valid_session_excludes_strategy_metrics() -> None:
    summary = _valid_ops(
        activation_id="V1R_EXIT_V2_PAPER_PRIMARY_OPVAL_CURRENT_TRADING_DAY",
        paper_mode="OPERATIONAL_VALIDATION_ONLY",
        INVALID_FOR_STRATEGY_EVALUATION=True,
        strategy_evaluation_allowed=False,
    )
    v = classify_session_validity(
        summary,
        environ={"TRADEBOT_OPERATIONAL_VALIDATION_MODE": "1"},
    )
    assert v["session_validity"] == VALID_SESSION
    assert v["operational_validity"] == VALID_SESSION
    assert v["strategy_evaluation_eligible"] is False
    assert v["include_in_strategy_metrics"] is False
    assert v["include_in_cumulative_pnl"] is False
    assert v["include_in_forward_day_count"] is False
    assert v["include_in_live_readiness_streak"] is False


def test_a2_normal_prospective_paper_inclusion_true() -> None:
    v = classify_session_validity(_valid_ops(), environ={})
    assert v["session_validity"] == VALID_SESSION
    assert v["run_classification"] == "NORMAL_PROSPECTIVE_PAPER"
    assert v["strategy_evaluation_eligible"] is True
    assert v["include_in_strategy_metrics"] is True
    assert v["include_in_cumulative_pnl"] is True
    assert v["include_in_forward_day_count"] is True
    assert v["include_in_live_readiness_streak"] is True


def test_a3_degraded_universe_strategy_inclusion_false() -> None:
    summary = _valid_ops(
        paper_mode="OPERATIONAL_VALIDATION_ONLY",
        degraded_universe="DEGRADED_UNIVERSE_49_OF_50",
        watch_symbols_count=49,
    )
    v = classify_session_validity(
        summary,
        environ={
            "TRADEBOT_OPERATIONAL_VALIDATION_MODE": "1",
            "TRADEBOT_OPVAL_DEGRADED_UNIVERSE_ONLY": "1",
        },
    )
    assert v["session_validity"] == VALID_SESSION
    assert v["run_classification"] == "DEGRADED_UNIVERSE"
    assert v["include_in_strategy_metrics"] is False
    assert v["include_in_cumulative_pnl"] is False


def test_a4_historical_20260819_artifact_immutable() -> None:
    assert AM_SUMMARY.is_file()
    assert PM_SUMMARY.is_file()
    assert hashlib.sha256(AM_SUMMARY.read_bytes()).hexdigest() == AM_SHA
    assert hashlib.sha256(PM_SUMMARY.read_bytes()).hexdigest() == PM_SHA
    am = json.loads(AM_SUMMARY.read_text(encoding="utf-8"))
    pm = json.loads(PM_SUMMARY.read_text(encoding="utf-8"))
    assert am.get("include_in_strategy_metrics") is True
    assert pm.get("include_in_strategy_metrics") is True
    assert am.get("canonical_trade_count") == 3
    assert pm.get("canonical_trade_count") == 6


def test_b1_one_terminal_invalid_refills_rank51_then_freeze(tmp_path: Path) -> None:
    ranked = _ranked(60)
    invalid = {ranked[0]}

    def probe(sym: str) -> dict[str, Any]:
        if str(sym) in invalid:
            return {"ok": False, "verdict": INVALID_SYMBOL, "kabu_code": "4002001"}
        return {"ok": True, "verdict": VALID_SYMBOL}

    selected = select_valid50_from_ranked(ranked, probe_fn=probe)
    assert selected["ok"] is True
    assert ranked[0] not in selected["valid_symbols"]
    assert ranked[50] in selected["valid_symbols"]
    assert selected["valid_symbols"] == ranked[1:51]
    assert selected["refill_attempt_count"] == 1
    assert selected["refill_success_count"] == 1
    assert selected["final_valid_count"] == 50
    frozen = freeze_valid50_after_kabu_validation(
        tmp_path,
        "20260820",
        ranked=ranked,
        probe_fn=probe,
        skip_if_frozen=False,
    )
    assert frozen["ok"] is True
    assert frozen.get("reused") is not True
    got = load_frozen_am_universe(tmp_path, "20260820")
    assert got["ok"] is True
    assert got["canonical_symbols"] == ranked[1:51]


def test_b2_multiple_terminal_invalids_preserve_rank() -> None:
    ranked = _ranked(60)
    invalid = {ranked[2], ranked[7], ranked[11]}

    def probe(sym: str) -> dict[str, Any]:
        if str(sym) in invalid:
            return {"ok": False, "verdict": INVALID_SYMBOL, "kabu_code": "4002001"}
        return {"ok": True, "verdict": VALID_SYMBOL}

    selected = select_valid50_from_ranked(ranked, probe_fn=probe)
    assert selected["ok"] is True
    for bad in invalid:
        assert bad not in selected["valid_symbols"]
    expected = [s for s in ranked if s not in invalid][:50]
    assert selected["valid_symbols"] == expected
    assert selected["refill_attempt_count"] == 3
    assert selected["refill_success_count"] == 3
    assert selected["final_valid_count"] == 50


def test_b3_temporary_failure_no_substitution_fail_closed() -> None:
    ranked = _ranked(60)

    def probe_auth(sym: str) -> dict[str, Any]:
        if str(sym) == ranked[0]:
            return {"ok": False, "verdict": AUTH_NOT_READY}
        return {"ok": True, "verdict": VALID_SYMBOL}

    auth = select_valid50_from_ranked(ranked, probe_fn=probe_auth)
    assert auth["ok"] is False
    assert auth["fail_closed"] is True
    assert auth["substituted"] is False
    assert auth["reason"] == AUTH_NOT_READY
    assert ranked[1] not in auth["valid_symbols"]

    def probe_rl(sym: str) -> dict[str, Any]:
        if str(sym) == ranked[0]:
            return {"ok": False, "verdict": RATE_LIMIT}
        return {"ok": True, "verdict": VALID_SYMBOL}

    rl = select_valid50_from_ranked(ranked, probe_fn=probe_rl)
    assert rl["ok"] is False
    assert rl["reason"] == RATE_LIMIT
    assert rl["substituted"] is False

    def probe_tr(sym: str) -> dict[str, Any]:
        if str(sym) == ranked[0]:
            return {"ok": False, "verdict": TRANSPORT_FAILURE}
        return {"ok": True, "verdict": VALID_SYMBOL}

    tr = select_valid50_from_ranked(ranked, probe_fn=probe_tr)
    assert tr["ok"] is False
    assert tr["reason"] == TRANSPORT_FAILURE
    assert tr["substituted"] is False
    assert ranked[1] not in tr["valid_symbols"]


def test_board_4002006_register_capacity_is_not_fail_closed() -> None:
    assert classify_board_probe_error(kabu_code="4002006", error='{"Code":4002006}') == REGISTER_CAPACITY
    ranked = _ranked(60)

    def probe(sym: str) -> dict[str, Any]:
        if str(sym) == ranked[49]:
            return {"ok": False, "verdict": REGISTER_CAPACITY, "kabu_code": "4002006"}
        return {"ok": True, "verdict": VALID_SYMBOL}

    got = select_valid50_from_ranked(ranked, probe_fn=probe)
    assert got["ok"] is True
    assert got["fail_closed"] is False
    assert ranked[49] in got["valid_symbols"]
    assert got["valid_symbols"] == ranked[:50]
    assert got["final_valid_count"] == 50
    assert got["refill_attempt_count"] == 0


def test_b0_cut_ranked_pool_at_50_cannot_refill() -> None:
    ranked = _ranked(50)

    def probe(sym: str) -> dict[str, Any]:
        if str(sym) == ranked[0]:
            return {"ok": False, "verdict": INVALID_SYMBOL, "kabu_code": "4002001"}
        return {"ok": True, "verdict": VALID_SYMBOL}

    selected = select_valid50_from_ranked(ranked, probe_fn=probe)
    assert selected["ok"] is False
    assert selected["reason"] == "INSUFFICIENT_VALID_CANDIDATES"
    assert selected["valid_count"] == 49
    assert selected["refill_success_count"] == 0
    assert selected["first_failure_symbol"] == ranked[0]
    assert selected["first_failure_code"] == "4002001"


def test_b4_freeze_exactly_once(tmp_path: Path) -> None:
    ranked = _ranked(60)
    calls = {"n": 0}

    def probe(sym: str) -> dict[str, Any]:
        calls["n"] += 1
        return {"ok": True, "verdict": VALID_SYMBOL}

    first = freeze_valid50_after_kabu_validation(
        tmp_path,
        "20260820",
        ranked=ranked,
        probe_fn=probe,
        skip_if_frozen=False,
    )
    assert first["ok"] is True
    assert first.get("freeze_created") is True
    assert first.get("reused") is not True
    assert first["freeze_symbol_count"] == 50
    probed = calls["n"]
    second = freeze_valid50_after_kabu_validation(
        tmp_path,
        "20260820",
        ranked=ranked,
        probe_fn=probe,
        skip_if_frozen=True,
    )
    assert second["ok"] is True
    assert second.get("reused") is True
    assert second.get("freeze_created") is False
    assert calls["n"] == probed
    got = load_frozen_am_universe(tmp_path, "20260820")
    assert got["canonical_symbols"] == ranked[:50]


def test_b5_dot_t_exclude_keeps_refill_pool(tmp_path: Path) -> None:
    from universe.am_pm_universe import _norm

    ex = ranker_exclude_set(["3054", "285A", "3054.T"])
    assert "3054" in ex
    assert "3054.T" in ex
    assert "285A" in ex
    assert "285A.T" in ex
    assert _norm("3054") in ex
    assert _norm("3054.T") in ex

    reports = tmp_path / "results" / "reports"
    reports.mkdir(parents=True)
    am_syms = [f"{1000 + i:04d}" for i in range(50)]
    extra_syms = [f"{2000 + i:04d}" for i in range(20)]
    with (reports / "universe_core10_dynamic40_price_risk_am_20260820.csv").open(
        "w", encoding="utf-8", newline=""
    ) as fh:
        writer = csv.DictWriter(fh, fieldnames=["symbol"])
        writer.writeheader()
        for sym in am_syms:
            writer.writerow({"symbol": f"{sym}.T"})
    with (reports / "features_20260820.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["symbol", "close", "volatility_liquidity_score"])
        writer.writeheader()
        for i, sym in enumerate(am_syms):
            writer.writerow({"symbol": f"{sym}.T", "close": "1000", "volatility_liquidity_score": str(300 - i)})
        for i, sym in enumerate(extra_syms):
            writer.writerow({"symbol": f"{sym}.T", "close": "1000", "volatility_liquidity_score": str(100 - i)})
    pool = load_ranked_candidate_pool(tmp_path, "20260820", extra_slots=20)
    assert pool["ok"] is True
    assert pool["primary_count"] == 50
    assert pool["extras_count"] == 20
    assert pool["ranked_count"] == 70
    assert pool["extras"] == extra_syms
    assert pool["ranked"][:50] == am_syms
    assert pool["ranked"][50] == extra_syms[0]
    overlap = set(pool["extras"]) & set(pool["primary"])
    assert not overlap


def test_c1_4001019_same_universe_not_retried_on_push(tmp_path: Path) -> None:
    svc = MarketIngressService(native_root=tmp_path, trading_date="20260820", synthetic=False, enable_tcp_bus=False)
    push = _FakePush(fail_code="4001019")
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=_ranked(50), generation=1, trading_date="20260820")
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    retry_after = int(svc._register_retry_count)
    svc._poll_desired_universe()
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    assert int(svc._register_retry_count) == retry_after
    assert svc._reg_attempt_state == PARTIAL_UNCONFIRMED
    svc.writer.close()
    svc.bus.stop()


def test_c2_temporary_failure_bounded_retry(tmp_path: Path) -> None:
    svc = MarketIngressService(native_root=tmp_path, trading_date="20260820", synthetic=False, enable_tcp_bus=False)
    push = _FakePush(fail_temp=True)
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=_ranked(10), generation=1, trading_date="20260820")
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    assert svc._reg_attempt_state == TEMPORARY_FAILED
    before = int(svc._register_retry_count)
    svc._poll_desired_universe()
    assert len(push.calls) == 2
    assert int(svc._register_retry_count) > before
    svc.writer.close()
    svc.bus.stop()


def test_c3_desired_sha_change_allows_new_generation() -> None:
    sha_a = desired_universe_sha(_ranked(50))
    sha_b = desired_universe_sha(_ranked(50)[1:] + ["9999"])
    blocked = should_attempt_register(
        trading_date="20260820",
        desired_sha=sha_a,
        registration_generation=1,
        last_attempt_date="20260820",
        last_attempt_sha=sha_a,
        last_attempt_state=PARTIAL_UNCONFIRMED,
        last_attempt_generation=1,
    )
    assert blocked["allow"] is False
    allowed = should_attempt_register(
        trading_date="20260820",
        desired_sha=sha_b,
        registration_generation=1,
        last_attempt_date="20260820",
        last_attempt_sha=sha_a,
        last_attempt_state=PARTIAL_UNCONFIRMED,
        last_attempt_generation=1,
    )
    assert allowed["allow"] is True
    assert allowed["reason"] == "new_desired_universe"
    reset = should_attempt_register(
        trading_date="20260820",
        desired_sha=sha_a,
        registration_generation=2,
        last_attempt_date="20260820",
        last_attempt_sha=sha_a,
        last_attempt_state=PARTIAL_UNCONFIRMED,
        last_attempt_generation=1,
    )
    assert reset["allow"] is True
    assert reset["reason"] == "operator_reset_generation"


def test_c4_c5_49_of_50_not_paper_ready_50_of_50_may_proceed(tmp_path: Path) -> None:
    day = "20260820"
    am = _ranked(50)
    freeze_same_day_am_universe(tmp_path, day, symbols=am, write_from_symbols=True)
    write_desired_universe(tmp_path, symbols=am, generation=1, trading_date=day)
    from small_paper.market_capture_registration import write_registration_manifest

    write_registration_manifest(
        tmp_path,
        trading_date=day,
        symbols=am,
        generation_id="gen_test",
        verified=True,
        extra={"actual_symbols": am, "actual_count": 50, "registered_count": 50},
    )
    forty_nine = verify_exact50_membership(
        tmp_path,
        day,
        actual_symbols=am[:49],
        require_actual_kabu=True,
    )
    assert forty_nine["ok"] is False
    fifty = verify_exact50_membership(
        tmp_path,
        day,
        actual_symbols=am,
        require_actual_kabu=True,
    )
    assert fifty["ok"] is True


def _admit(symbol: str, fill: float, ts: str) -> dict[str, Any]:
    return {
        "event": "ADMIT",
        "lane": "primary",
        "symbol": symbol,
        "fill_price": fill,
        "fill_time": ts,
        "ts": ts,
        "position_id": f"{symbol}-{fill}",
    }


def _exit(symbol: str, fill: float, exit_p: float, ts: str, **extra: Any) -> dict[str, Any]:
    body = {
        "event": "EXIT_EXECUTED",
        "lane": "primary",
        "symbol": symbol,
        "fill_price": fill,
        "exit_price": exit_p,
        "exit_time": ts,
        "ts": ts,
        "reason": "IMBALANCE",
        "position_id": f"{symbol}-{fill}",
    }
    body.update(extra)
    return body


def test_d1_full_lifecycle_canonical_accepted() -> None:
    traces = [
        _admit("285A", 100.0, "t0"),
        _exit("285A", 100.0, 101.0, "t1"),
    ]
    body = reconcile_canonical_lifecycle(traces, official_entry_count=0)
    assert body["ok"] is True
    assert body["class_A"] == 1
    assert body["class_C"] == 0
    assert body["official_entry_count_explainable_class_A"] is True
    summary: dict[str, Any] = {"official_entry_count": 0, "canonical_trade_count": 1}
    attach_lifecycle_integrity(summary, traces=traces)
    v = classify_session_validity(_valid_ops(**summary), environ={})
    assert v["session_validity"] == VALID_SESSION
    assert v["include_in_strategy_metrics"] is True


def test_d2_orphan_exit_excludes_strategy_metrics() -> None:
    traces = [_exit("285A", 100.0, 101.0, "t1")]
    body = reconcile_canonical_lifecycle(traces, official_entry_count=0)
    assert body["class_C"] == 1
    assert body["orphan_exit_count"] == 1
    assert body["ok"] is False
    summary: dict[str, Any] = {"official_entry_count": 0}
    attach_lifecycle_integrity(summary, traces=traces)
    v = classify_session_validity(_valid_ops(**summary), environ={})
    assert v["session_validity"] == VALID_SESSION
    assert v["strategy_evaluation_eligible"] is False
    assert v["include_in_strategy_metrics"] is False


def test_d3_pre_attach_recovered_ownership() -> None:
    traces = [_exit("285A", 100.0, 101.0, "t1", recovered=True, ownership="pre_attach")]
    body = reconcile_canonical_lifecycle(traces)
    assert body["class_B"] == 1
    assert body["class_C"] == 0
    assert body["trades"][0]["ownership"] == "PRE_ATTACH_OR_RECOVERED"


def test_d4_canonical_without_explainable_ownership_integrity_failure() -> None:
    traces = [_exit("285A", 100.0, 101.0, "t1")]
    summary: dict[str, Any] = {"official_entry_count": 0, "canonical_trade_count": 1}
    attach_lifecycle_integrity(summary, traces=traces)
    assert summary["lifecycle_integrity"]["pass"] is False
    reasons = " ".join(summary["lifecycle_integrity"].get("integrity_reasons") or [])
    assert "orphan_exit_count" in reasons or "canonical_trades_without_explainable_ownership" in reasons
    v = classify_session_validity(_valid_ops(**summary), environ={})
    assert v["session_validity"] == VALID_SESSION
    assert v["include_in_strategy_metrics"] is False


def test_pre_freeze_probe_throttle_paces_live_board_gets(monkeypatch: Any) -> None:
    """Live /board pacing must not change fail-closed RATE_LIMIT semantics."""
    import small_paper.pre_freeze_kabu_validation as mod

    sleeps: list[float] = []
    monkeypatch.setenv("KABU_PRE_FREEZE_PROBE_INTERVAL_SEC", "0.2")
    monkeypatch.setattr(mod, "_last_live_probe_monotonic", 0.0)
    clock = {"t": 100.0}

    def fake_mono() -> float:
        return float(clock["t"])

    def fake_sleep(sec: float) -> None:
        sleeps.append(float(sec))
        clock["t"] += float(sec)

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_board(self, key: str, token: str = "") -> dict[str, Any]:
            return {"Symbol": key}

    monkeypatch.setattr(mod.time, "monotonic", fake_mono)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    monkeypatch.setattr("api.rest_client.KabuNativeRestClient", _Client)
    monkeypatch.setattr("api.rest_client.default_base_url", lambda: "http://127.0.0.1:18080")

    a = mod.live_board_probe("7203", token="tok")
    clock["t"] += 0.05  # still inside interval
    b = mod.live_board_probe("6758", token="tok")
    assert a["ok"] is True and b["ok"] is True
    assert sleeps and sleeps[0] >= 0.14

    ranked = _ranked(60)

    def probe_rl(sym: str) -> dict[str, Any]:
        if str(sym) == ranked[10]:
            return {"ok": False, "verdict": RATE_LIMIT, "kabu_code": "4001006"}
        return {"ok": True, "verdict": VALID_SYMBOL}

    rl = select_valid50_from_ranked(ranked, probe_fn=probe_rl)
    assert rl["ok"] is False
    assert rl["fail_closed"] is True
    assert rl["reason"] == RATE_LIMIT
    assert rl["valid_count"] == 10
    assert rl["substituted"] is False
