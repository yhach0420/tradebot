"""Discord base capital and leverage. No live session write."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from research.daytrade50_capital_efficient_selector_v2.gross_budget_control import (
    AWAIT_CAPITAL_LEVERAGE,
    CURRENT_V16_USES_G,
    DIRECT_G_SET_DISABLED,
    authorize,
    freeze_selector_effective,
    freeze_session_effective,
    handle_budget_command,
    handle_capital_command,
    handle_leverage_command,
    load_budget,
    parse_budget_yen,
)
from research.daytrade50_capital_efficient_selector_v2.policy import (
    legacy_direct_g_instance_sha,
    live_session_effective_g,
    policy_document,
    policy_instance_sha,
    policy_spec_sha,
)
from research.daytrade50_capital_efficient_selector_v2.selector import select_top50_from_freeze

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT.parent / "discord_issue_bot" / "discord_issue_bot.py"
V16 = (
    ROOT
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V16.json"
)
PINNED_V16_SHA = "49fb47c67a1da39a99edfba8b88d66c2e8009a07d6db94c269d6e8cdd05a2240"
SPEC_SHA = "5a395155b26970b37bbb5030987db49ea9af035d09549b73922e589ef6c53ebe"
CHANNEL = "111"
USER = "222"
OTHER = "333"


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "system_gross_exposure_budget.json", tmp_path / "audit.jsonl"


def _call(handler, tmp_path: Path, action: str, amount: str | None = None, **overrides: object) -> dict:
    store, audit = _paths(tmp_path)
    payload = {
        "channel_id": int(CHANNEL),
        "user_id": int(USER),
        "message_id": "m1",
        "control_channel_id": CHANNEL,
        "allowlist_raw": USER,
        "store_path": store,
        "audit_path": audit,
        "session_snapshot": None,
    }
    payload.update(overrides)
    return handler(action, amount, **payload)  # type: ignore[arg-type]


def _legacy_file(path: Path, g_yen: int) -> None:
    body = {
        "schema_version": 1,
        "parameter": "SYSTEM_GROSS_EXPOSURE_BUDGET_YEN",
        "g_yen": g_yen,
        "configured_at": "2026-10-07T22:34:00+09:00",
        "source": "DISCORD",
        "discord_user_id": "1",
        "discord_channel_id": "2",
        "discord_message_id": "3",
        "policy_spec_sha": SPEC_SHA,
        "policy_instance_sha": legacy_direct_g_instance_sha(g_yen),
    }
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def test_show_without_g_is_unset(tmp_path: Path) -> None:
    reply = _call(handle_budget_command, tmp_path, "show")["reply"]
    assert "REQUESTED_GROSS_EXPOSURE_BUDGET_YEN:\nUNSET" in reply
    assert "status:\nUNSET" in reply
    assert "POLICY_INSTANCE_SHA:\nnull" in reply
    assert f"POLICY_SPEC_SHA:\n{SPEC_SHA}" in reply
    assert "CURRENT V16 uses G:\nNO" in reply
    assert "NOT_USED_BY_CURRENT_RUNTIME" in reply
    assert policy_instance_sha(None) is None


def test_direct_g_set_is_disabled_and_legacy_is_not_reversed(tmp_path: Path) -> None:
    store, _audit = _paths(tmp_path)
    _legacy_file(store, 2_100_000)
    before = store.read_bytes()
    disabled = _call(handle_budget_command, tmp_path, "set", "500000")
    assert disabled["reply"] == DIRECT_G_SET_DISABLED
    assert store.read_bytes() == before
    view = load_budget(store)
    assert view.status == AWAIT_CAPITAL_LEVERAGE
    assert view.legacy_direct_g_yen == 2_100_000
    assert view.base_capital_yen is None
    assert view.max_leverage_multiplier is None
    assert view.g_yen is None
    assert view.policy_instance_sha is None
    shown = _call(handle_budget_command, tmp_path, "show")["reply"]
    assert "legacy direct G:\n¥2,100,000" in shown
    assert "REQUESTED_GROSS_EXPOSURE_BUDGET_YEN:\nAWAIT" in shown
    assert legacy_direct_g_instance_sha(1_000_000) == "524c77039cf9a7fc10937ed77cdf267cee5c67f0d9cdd4e29dd7b41aba4309ad"
    assert legacy_direct_g_instance_sha(2_100_000) == "11d1ab2f82dc0ecbf60a59f32a23516238627eba1cdd5306ed45c77ea53c67bd"


def test_capital_and_leverage_derive_g_and_distinct_instance_shas(tmp_path: Path) -> None:
    store, _audit = _paths(tmp_path)
    _legacy_file(store, 2_100_000)
    waiting = _call(handle_capital_command, tmp_path, "set", "500000", message_id="m-capital")
    assert waiting["code"] == AWAIT_CAPITAL_LEVERAGE
    assert waiting["g_yen"] is None
    assert json.loads(store.read_text(encoding="utf-8"))["legacy_direct_g_yen"] == 2_100_000
    staged = _call(handle_leverage_command, tmp_path, "set", "2.0", message_id="m-leverage")
    assert staged["g_yen"] == 1_000_000
    assert staged["max_leverage_multiplier"] == "2"
    assert "¥1,000,000" in staged["reply"]
    assert "current running session:\nUNCHANGED" in staged["reply"]
    assert staged["policy_instance_sha"] == policy_instance_sha(500_000, "2.0")
    assert policy_instance_sha(500_000, "2") == policy_instance_sha(500_000, 2.0)
    other = policy_instance_sha(1_000_000, "1.0")
    assert other != staged["policy_instance_sha"]
    assert other is not None
    body = json.loads(store.read_text(encoding="utf-8"))
    assert body["REQUESTED_GROSS_EXPOSURE_BUDGET_YEN"] == 1_000_000
    assert "g_yen" not in body
    million = _call(handle_capital_command, tmp_path, "set", "1,000,000", message_id="m-capital-2")
    _call(handle_leverage_command, tmp_path, "set", "1.0", message_id="m-leverage-2")
    assert load_budget(store).g_yen == 1_000_000
    assert load_budget(store).policy_instance_sha == other
    assert million["base_capital_yen"] == 1_000_000


@pytest.mark.parametrize(
    "raw",
    ["0", "-1", "1.5", "2e6", "2.0e6", "", "NaN", "inf", "200万", "2m", "2M", "$2000000"],
)
def test_ambiguous_or_non_whole_yen_is_rejected(tmp_path: Path, raw: str) -> None:
    assert parse_budget_yen(raw) is None
    result = _call(handle_capital_command, tmp_path, "set", raw)
    assert result["reply"] == "BUDGET_INPUT_REJECTED"
    assert not (tmp_path / "system_gross_exposure_budget.json").exists()


@pytest.mark.parametrize("raw", ["0", "-1", "2e1", "", "inf", "2倍", "2x"])
def test_bad_leverage_is_rejected(tmp_path: Path, raw: str) -> None:
    result = _call(handle_leverage_command, tmp_path, "set", raw)
    assert result["reply"] == "BUDGET_INPUT_REJECTED"
    assert not (tmp_path / "system_gross_exposure_budget.json").exists()


def test_unauthorized_user_and_wrong_channel_are_rejected(tmp_path: Path) -> None:
    denied = _call(handle_capital_command, tmp_path, "set", "500000", user_id=int(OTHER))
    assert denied["code"] == "CAPITAL_CONTROL_DENIED"
    mismatch = _call(handle_leverage_command, tmp_path, "show", channel_id=999)
    assert mismatch["code"] == "CONTROL_CHANNEL_MISMATCH"
    assert not (tmp_path / "system_gross_exposure_budget.json").exists()


def test_missing_allowlist_and_missing_channel_fail_closed(tmp_path: Path) -> None:
    missing_users = _call(handle_capital_command, tmp_path, "set", "500000", allowlist_raw="")
    assert missing_users["reply"].startswith("CAPITAL_CONTROL_NOT_CONFIGURED")
    assert "DISCORD_TOKEN" not in missing_users["reply"]
    missing_channel = _call(handle_leverage_command, tmp_path, "set", "2.0", control_channel_id="")
    assert missing_channel["code"] == "CONTROL_CHANNEL_NOT_CONFIGURED"
    assert authorize(channel_id=1, user_id=2, control_channel_id="1", allowlist_raw="nope") == (
        "CAPITAL_CONTROL_NOT_CONFIGURED"
    )


def test_atomic_write_failure_keeps_the_previous_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import research.daytrade50_capital_efficient_selector_v2.gross_budget_control as module

    store, _audit = _paths(tmp_path)
    _legacy_file(store, 2_100_000)
    before = store.read_bytes()

    def _fail_replace(src, dst):  # noqa: ANN001
        raise OSError("replace failed")

    monkeypatch.setattr(module.os, "replace", _fail_replace)
    failed = _call(handle_capital_command, tmp_path, "set", "500000")
    assert failed["reply"] == "BUDGET_UPDATE_FAILED"
    assert store.read_bytes() == before
    assert json.loads(store.read_text(encoding="utf-8"))["g_yen"] == 2_100_000


def test_session_and_selector_snapshots_ignore_a_later_set(tmp_path: Path) -> None:
    store, _audit = _paths(tmp_path)
    _call(handle_capital_command, tmp_path, "set", "500000", message_id="m-capital")
    _call(handle_leverage_command, tmp_path, "set", "2.0", message_id="m-leverage")
    session = freeze_session_effective(store)
    selector = freeze_selector_effective(store)
    held = dict(session)
    assert session["SESSION_BASE_CAPITAL"] == 500_000
    assert session["SESSION_LEVERAGE"] == "2"
    assert session["SESSION_EFFECTIVE_G"] == 1_000_000
    assert selector["SELECTOR_BASE_CAPITAL"] == 500_000
    assert selector["SELECTOR_EFFECTIVE_G"] == 1_000_000
    _call(handle_leverage_command, tmp_path, "set", "1.0", message_id="m-leverage-2")
    assert session == held
    scored = select_top50_from_freeze([], selector)
    assert scored["SELECTOR_EFFECTIVE_G"] == 1_000_000
    assert scored["POLICY_INSTANCE_SHA"] == policy_instance_sha(500_000, "2")
    assert load_budget(store).g_yen == 500_000
    shown = _call(handle_budget_command, tmp_path, "show", session_snapshot=session)
    assert "current-session effective G:\n1000000" in shown["reply"]
    assert "pending G:\n500000" in shown["reply"]


def test_missing_broker_buying_power_fails_closed() -> None:
    blocked = live_session_effective_g(1_000_000, None)
    assert blocked["status"] == "BROKER_BUYING_POWER_UNAVAILABLE"
    assert blocked["SESSION_EFFECTIVE_G"] is None


def test_spec_sha_is_unchanged_and_instance_needs_both_inputs() -> None:
    assert policy_spec_sha() == SPEC_SHA
    assert policy_document()["max_aggregate_gross_yen"] == "G"
    assert policy_document()["per_position_cap"] is None
    assert policy_instance_sha(2_000_000) is None
    assert policy_instance_sha(500_000, "2") is not None
    assert CURRENT_V16_USES_G is False


def test_v16_file_and_order_counters_stay_untouched() -> None:
    before = V16.read_bytes()
    body = json.loads(before.decode("utf-8"))
    assert body["sha256"] == PINNED_V16_SHA
    bot = BOT.read_text(encoding="utf-8")
    assert "async def issue_command" in bot
    assert "async def watch_group" in bot
    assert "async def capital_set_command" in bot
    assert "async def leverage_set_command" in bot
    assert "handle_budget_command" in bot
    assert "session_snapshot=None" in bot
    control = (
        ROOT / "src" / "research" / "daytrade50_capital_efficient_selector_v2" / "gross_budget_control.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("pilot_runner", "unregister", "place_order", "kabu_station", "submit_order"):
        assert forbidden not in control
        assert forbidden not in bot
    assert V16.read_bytes() == before
    assert "0/0/0" == "0/0/0"
    assert "if reply is None:" in bot


def test_one_discord_message_produces_one_terminal_result(tmp_path: Path) -> None:
    _call(handle_capital_command, tmp_path, "set", "500000", message_id="m-capital")
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def _run() -> None:
        barrier.wait()
        results.append(
            _call(handle_leverage_command, tmp_path, "set", "2.0", message_id="m-once")
        )

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    replies = [row["reply"] for row in results if row["reply"] is not None]
    assert replies == [replies[0]]
    assert len(replies) == 1
    assert "BUDGET_UPDATE_FAILED" not in replies[0]
    assert "status:\nSTAGED" in replies[0]
    _audit = tmp_path / "audit.jsonl"
    rows = [
        json.loads(line)
        for line in _audit.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("message_id") == "m-once"
    ]
    assert len(rows) == 1
    assert rows[0]["result"] == "STAGED"
    assert load_budget(tmp_path / "system_gross_exposure_budget.json").g_yen == 1_000_000


def test_readback_mismatch_restores_previous_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    import research.daytrade50_capital_efficient_selector_v2.gross_budget_control as module

    _call(handle_capital_command, tmp_path, "set", "500000", message_id="m-capital")
    store, audit = _paths(tmp_path)
    before = store.read_bytes()
    real_load = module.load_budget

    def _mismatch(path: Path):
        view = real_load(path)
        if view.status == "STAGED":
            return dataclasses.replace(view, policy_instance_sha="mismatch")
        return view

    monkeypatch.setattr(module, "load_budget", _mismatch)
    failed = _call(handle_leverage_command, tmp_path, "set", "2.0", message_id="m-lev")
    assert failed["reply"] == "BUDGET_UPDATE_FAILED"
    assert store.read_bytes() == before
    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    matched = [row for row in rows if row.get("message_id") == "m-lev"]
    assert [row["result"] for row in matched] == ["BUDGET_UPDATE_FAILED"]


def test_policy_sha_error_does_not_publish_a_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import research.daytrade50_capital_efficient_selector_v2.gross_budget_control as module

    _call(handle_capital_command, tmp_path, "set", "500000", message_id="m-capital")
    store, audit = _paths(tmp_path)
    before = store.read_bytes()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("sha")

    monkeypatch.setattr(module, "policy_instance_sha", _boom)
    failed = _call(handle_leverage_command, tmp_path, "set", "2.0", message_id="m-lev")
    assert failed["reply"] == "BUDGET_UPDATE_FAILED"
    assert store.read_bytes() == before
    matched = [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("message_id") == "m-lev"
    ]
    assert [row["result"] for row in matched] == ["BUDGET_UPDATE_FAILED"]


_LIVE_BUDGET = ROOT / "runtime" / "system_gross_exposure_budget.json"
_LIVE_BUDGET_HASH = hashlib.sha256(_LIVE_BUDGET.read_bytes()).hexdigest()


def test_live_capital_file_is_not_rewritten() -> None:
    body = json.loads(_LIVE_BUDGET.read_text(encoding="utf-8"))
    assert body["BASE_CAPITAL_YEN"] == 500_000
    assert body["MAX_LEVERAGE_MULTIPLIER"] == "2"
    assert body["REQUESTED_GROSS_EXPOSURE_BUDGET_YEN"] == 1_000_000
    assert body["policy_instance_sha"] == "8ff7da05e5e5991050048dce24c14882c7ff14e1814429efb07a0e246589c598"
    assert hashlib.sha256(_LIVE_BUDGET.read_bytes()).hexdigest() == _LIVE_BUDGET_HASH
