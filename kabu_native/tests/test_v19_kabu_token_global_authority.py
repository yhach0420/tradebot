"""V19: Station-global single Kabu token authority.

S4 regression (preflight must not POST /token), cross-process second issuer
blocked before HTTP, stale-owner takeover, atomic publish.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from small_paper.kabu_token_authority import (
    BLOCKED_REASON,
    OWNER_INGRESS,
    ChildTokenIssueBlocked,
    TokenSecondIssuerBlocked,
    TokenUnavailable,
    acquire_token_for_readonly,
    evaluate_issue_permission,
    issue_station_token,
    live_kabu_auth_allowed,
    load_station_bundle,
    owner_issue_context,
    publish_owned_token,
    read_shared_token,
    station_issue_audit_summary,
    station_issue_lock,
    token_fingerprint,
)
from small_paper.market_ingress_service import MarketIngressService
from small_paper.runtime_clock import (
    ENV_KABU_AUTH_MODE,
    ENV_MARKET_INPUT_MODE,
    ENV_REPLAY_PATH,
    ENV_TOKEN_PREFLIGHT,
    KABU_AUTH_NONE,
    apply_non_issuer_env,
    kabu_auth_mode,
    market_input_mode,
)

NATIVE = Path(__file__).resolve().parents[1]


class _StubClient:
    def __init__(self, counter: Path, token: str = "tok-s3") -> None:
        self.base_url = "http://localhost:18080/kabusapi"
        self.counter = counter
        self.token = token

    def post_token_http(self, api_password: str) -> str:
        assert api_password
        self.counter.write_text(
            str(int(self.counter.read_text(encoding="utf-8") or "0") + 1),
            encoding="utf-8",
        )
        return self.token


def _iso_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_AUTH_MODE", "LIVE")
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    monkeypatch.delenv("KABU_CERTIFICATION_PROBE", raising=False)
    monkeypatch.delenv("MARKET_INPUT_MODE", raising=False)
    return tmp_path / "post_count.txt"


@pytest.fixture
def station_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _iso_env(tmp_path, monkeypatch)
    count = tmp_path / "post_count.txt"
    count.write_text("0", encoding="utf-8")
    return tmp_path


def test_s1_s2_consumers_cannot_issue(station_tmp: Path) -> None:
    client = _StubClient(station_tmp / "post_count.txt")
    with pytest.raises(TokenSecondIssuerBlocked) as exc:
        issue_station_token(client, "pw", caller="kabu_readonly_readiness")
    assert BLOCKED_REASON in str(exc.value)
    assert (station_tmp / "post_count.txt").read_text(encoding="utf-8") == "0"
    with pytest.raises(TokenUnavailable):
        acquire_token_for_readonly(
            native_root=station_tmp,
            trading_date="20260812",
            caller="verify_kabu_connection",
        )


def test_authorized_ingress_issues_once_and_consumers_reuse(station_tmp: Path) -> None:
    client = _StubClient(station_tmp / "post_count.txt", token="tok-s3")
    with owner_issue_context(
        native_root=station_tmp,
        trading_date="20260812",
        pid=os.getpid(),
        session_id="ing_a",
        caller="ingress_replay_connect",
    ):
        tok = issue_station_token(client, "pw", caller="ingress_replay_connect")
    assert tok == "tok-s3"
    assert (station_tmp / "post_count.txt").read_text(encoding="utf-8") == "1"
    bundle = load_station_bundle()
    assert bundle["generation"] == 1
    assert bundle["fingerprint"] == token_fingerprint("tok-s3")
    got = acquire_token_for_readonly(
        native_root=station_tmp,
        trading_date="20260812",
        caller="daily_safety",
    )
    assert got["reused"] is True
    assert got["issued"] is False
    assert got["token"] == "tok-s3"
    assert int(got["token_generation"]) == 1


def test_s4_synthetic_replay_does_not_post_token(
    station_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V18 hole: replay path + synthetic preflight POSTed /token. Must not."""
    monkeypatch.setenv(ENV_REPLAY_PATH, str(station_tmp / "replay.jsonl"))
    (station_tmp / "replay.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv(ENV_TOKEN_PREFLIGHT, "1")
    monkeypatch.setenv(ENV_KABU_AUTH_MODE, KABU_AUTH_NONE)
    ok, reason = live_kabu_auth_allowed(synthetic=True)
    assert ok is False
    svc = MarketIngressService(
        native_root=station_tmp,
        trading_date="20260814",
        synthetic=True,
        enable_tcp_bus=False,
    )
    out = svc._replay_try_register()
    assert out.get("token_issued") is False
    assert out.get("skipped")
    assert (station_tmp / "post_count.txt").read_text(encoding="utf-8") == "0"
    svc.writer.close()
    svc.bus.stop()


def test_s4_second_ingress_blocked_before_http(station_tmp: Path) -> None:
    count = station_tmp / "post_count.txt"
    a = _StubClient(count, token="tok-s3")
    with owner_issue_context(
        native_root=station_tmp,
        trading_date="20260812",
        pid=os.getpid(),
        session_id="ing_a",
        caller="ingress_replay_connect",
    ):
        issue_station_token(a, "pw", caller="ingress_replay_connect")
    b = _StubClient(count, token="tok-s4")
    # Simulate S4: another process with its own owner_issue_context (TLS hole).
    with owner_issue_context(
        native_root=station_tmp / "sandbox",
        trading_date="20260814",
        pid=os.getpid() + 10_000_000,
        session_id="ing_b",
        caller="ingress_replay_connect",
    ):
        with pytest.raises(TokenSecondIssuerBlocked):
            issue_station_token(b, "pw", caller="ingress_replay_connect")
    assert count.read_text(encoding="utf-8") == "1"
    assert read_shared_token(station_tmp, "20260812") == "tok-s3"
    snap = station_issue_audit_summary()
    assert snap["authorized_issue_count"] == 1
    assert snap["blocked_second_issuer_count"] >= 1


def test_atomic_publish_generation_matches_fingerprint(station_tmp: Path) -> None:
    with owner_issue_context(
        native_root=station_tmp,
        trading_date="20260812",
        pid=os.getpid(),
        session_id="ing_a",
        caller="ingress_connect",
    ):
        body = publish_owned_token(
            "tok-atomic",
            native_root=station_tmp,
            trading_date="20260812",
            caller="ingress_connect",
        )
    bundle = load_station_bundle()
    assert int(body["token_generation"]) == int(bundle["generation"])
    assert bundle["fingerprint"] == token_fingerprint(bundle["token"])
    assert bundle["token"] == "tok-atomic"
    # Second publish of same token must not bump generation (idempotent).
    body2 = publish_owned_token(
        "tok-atomic",
        native_root=station_tmp,
        trading_date="20260812",
        caller="ingress_connect",
    )
    assert int(body2["token_generation"]) == int(bundle["generation"])


def test_stale_owner_takeover_single_issue(station_tmp: Path) -> None:
    dead_pid = 2147483646
    owner_path = station_tmp / "kabu_station_owner.json"
    owner_path.write_text(
        json.dumps(
            {
                "owner": OWNER_INGRESS,
                "pid": dead_pid,
                "token_generation": 3,
                "token_issue_count": 3,
            }
        ),
        encoding="utf-8",
    )
    count = station_tmp / "post_count.txt"
    client = _StubClient(count, token="tok-takeover")
    with owner_issue_context(
        native_root=station_tmp,
        trading_date="20260812",
        pid=os.getpid(),
        session_id="ing_b",
        caller="ingress_connect",
    ):
        tok = issue_station_token(client, "pw", caller="ingress_connect")
    assert tok == "tok-takeover"
    assert count.read_text(encoding="utf-8") == "1"
    bundle = load_station_bundle()
    assert bundle["pid"] == os.getpid()
    assert int(bundle["generation"]) == 4


def test_apply_non_issuer_env_strips_cert_and_forbids_auth() -> None:
    env = {
        "TRADEBOT_INGRESS_REPLAY_PATH": r"C:\leak.jsonl",
        "TRADEBOT_SESSION_CLOCK": "1",
        "KABU_AUTH_MODE": "LIVE",
    }
    apply_non_issuer_env(env)
    assert not env.get("TRADEBOT_INGRESS_REPLAY_PATH")
    assert env.get("KABU_AUTH_MODE") == KABU_AUTH_NONE
    assert env.get("MARKET_INPUT_MODE") == "SYNTHETIC"
    assert env.get("KABU_TOKEN_PREFLIGHT") == "1"


def test_cross_process_second_issuer_blocked(station_tmp: Path) -> None:
    """TLS in each process is insufficient; OS lock + station owner must serialize."""
    helper = station_tmp / "issuer_helper.py"
    helper.write_text(
        "\n".join(
            [
                "import os, sys, time",
                "from pathlib import Path",
                "sys.path.insert(0, r'%s')" % str(NATIVE / "src"),
                "from small_paper.kabu_token_authority import owner_issue_context, issue_station_token, TokenSecondIssuerBlocked",
                "root = Path(sys.argv[1])",
                "hold = float(sys.argv[2])",
                "os.environ['KABU_STATION_AUTHORITY_DIR'] = str(root)",
                "os.environ['KABU_TOKEN_AUTHORITY_DIR'] = str(root)",
                "os.environ['KABU_AUTH_MODE'] = 'LIVE'",
                "count = root / 'post_count.txt'",
                "class C:",
                "    base_url = 'http://localhost:18080/kabusapi'",
                "    def post_token_http(self, pw):",
                "        n = int(count.read_text(encoding='utf-8') or '0') + 1",
                "        count.write_text(str(n), encoding='utf-8')",
                "        time.sleep(hold)",
                "        return 'tok-proc'",
                "try:",
                "    with owner_issue_context(native_root=root, trading_date='20260812', pid=os.getpid(), session_id='ing_'+str(os.getpid()), caller='ingress_connect'):",
                "        issue_station_token(C(), 'pw', caller='ingress_connect')",
                "    (root / ('ok_'+str(os.getpid()))).write_text('1', encoding='utf-8')",
                "except TokenSecondIssuerBlocked:",
                "    (root / ('blocked_'+str(os.getpid()))).write_text('1', encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    py = sys.executable
    p1 = subprocess.Popen([py, str(helper), str(station_tmp), "0.8"], cwd=str(NATIVE))
    time.sleep(0.15)
    p2 = subprocess.Popen([py, str(helper), str(station_tmp), "0.0"], cwd=str(NATIVE))
    rc1 = p1.wait(timeout=15)
    rc2 = p2.wait(timeout=15)
    assert rc1 == 0 and rc2 == 0
    posts = int((station_tmp / "post_count.txt").read_text(encoding="utf-8") or "0")
    blocked = list(station_tmp.glob("blocked_*"))
    ok = list(station_tmp.glob("ok_*"))
    assert posts == 1
    assert len(ok) == 1
    assert len(blocked) == 1


def test_leftover_day_dir_token_without_live_owner_is_unavailable(station_tmp: Path) -> None:
    """V19 cert FAIL: 20260812 leftover token reused pre-Ingress → Wallet 4001009."""
    (station_tmp / ".kabu_session_token").write_text("stale-leftover-token", encoding="utf-8")
    owner_path = station_tmp / "kabu_station_owner.json"
    owner_path.write_text(
        json.dumps({"owner": OWNER_INGRESS, "pid": 2147483646, "token_generation": 21}),
        encoding="utf-8",
    )
    with pytest.raises(TokenUnavailable):
        acquire_token_for_readonly(
            native_root=station_tmp,
            trading_date="20260812",
            caller="kabu_readonly_readiness",
        )
    src = (NATIVE / "scripts" / "run_paper_full_day_certification.py").read_text(encoding="utf-8")
    assert "issue_token_from_env" not in src
    pre = (NATIVE / "scripts" / "run_market_ingress_v2_preflight.py").read_text(encoding="utf-8")
    assert "apply_non_issuer_env" in pre


def test_inventory_pins_readonly_and_authority() -> None:
    from small_paper.v1r_activation_binding import RUNTIME_DEPENDENCY_RELS

    assert "src/small_paper/kabu_token_authority.py" in RUNTIME_DEPENDENCY_RELS
    assert "src/small_paper/kabu_readonly_readiness.py" in RUNTIME_DEPENDENCY_RELS
    assert "src/small_paper/market_ingress_spawn.py" in RUNTIME_DEPENDENCY_RELS


def test_preclear_skips_without_shared_token(station_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(station_tmp)
    from api.kabu_register import clear_register_before_session

    out = clear_register_before_session(station_tmp)
    assert out.get("ok") is True
    assert out.get("skipped") is True
    assert out.get("reason") == "AUTH_DEFERRED_UNTIL_INGRESS"


def test_synthetic_spawn_env_is_non_issuer() -> None:
    from small_paper.runtime_clock import apply_non_issuer_env, official_cert_child_env

    env = {
        "TRADEBOT_INGRESS_REPLAY_PATH": r"C:\replay.jsonl",
        "TRADEBOT_SESSION_CLOCK": "1",
        "KABU_AUTH_MODE": "LIVE",
    }
    apply_non_issuer_env(env)
    assert env["KABU_AUTH_MODE"] == "NONE"
    assert not env.get("TRADEBOT_INGRESS_REPLAY_PATH")
    live = official_cert_child_env(
        {
            "TRADEBOT_INGRESS_REPLAY_PATH": r"C:\replay.jsonl",
            "KABU_TOKEN_PREFLIGHT": "1",
        }
    )
    assert live["KABU_AUTH_MODE"] == "LIVE"
    assert live.get("TRADEBOT_INGRESS_REPLAY_PATH")
    assert "KABU_TOKEN_PREFLIGHT" not in live
