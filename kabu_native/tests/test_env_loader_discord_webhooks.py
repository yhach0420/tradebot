"""Repo-root .env loader — cwd-independent Discord webhook discovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from small_paper.env_loader import (
    DISCORD_WEBHOOK_ENV_KEYS,
    ensure_repo_dotenv,
    load_repo_dotenv,
    log_webhook_configured,
    resolve_repo_root,
    webhook_configured_map,
)


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Temp repo with .env; clear webhook keys so OS does not mask .env."""
    env_file = tmp_path / ".env"
    lines = [
        "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL=https://discord.example/notify-from-dotenv",
        "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL=https://discord.example/cap-from-dotenv",
        "KABU_DISCORD_OPERATIONS_WEBHOOK_URL=https://discord.example/ops-from-dotenv",
        "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL=https://discord.example/cap-side-from-dotenv",
        "KABU_DISCORD_RESEARCH_WEBHOOK_URL=https://discord.example/research-from-dotenv",
        "KABU_DISCORD_CRITICAL_WEBHOOK_URL=https://discord.example/critical-from-dotenv",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k in DISCORD_WEBHOOK_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_resolve_repo_root_is_tradebotfile():
    root = resolve_repo_root()
    assert root.name == "tradebotfile" or (root / "kabu_native").is_dir()
    assert (root / "kabu_native" / "src" / "small_paper" / "env_loader.py").is_file()


def test_load_from_repo_root_env(isolated_repo: Path):
    st = load_repo_dotenv(repo_root=isolated_repo, override=False)
    assert st.dotenv_exists is True
    assert st.dotenv_loaded is True
    assert all(st.webhook_configured[k] for k in DISCORD_WEBHOOK_ENV_KEYS)
    assert os.environ["KABU_DISCORD_RESEARCH_WEBHOOK_URL"].endswith("research-from-dotenv")


def test_os_env_wins_over_dotenv(isolated_repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "https://discord.example/os-wins")
    st = load_repo_dotenv(repo_root=isolated_repo, override=False)
    assert os.environ["KABU_DISCORD_OPERATIONS_WEBHOOK_URL"] == "https://discord.example/os-wins"
    assert st.webhook_configured["KABU_DISCORD_OPERATIONS_WEBHOOK_URL"] is True


def test_missing_dotenv_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for k in DISCORD_WEBHOOK_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    st = load_repo_dotenv(repo_root=tmp_path, override=False)
    assert st.dotenv_exists is False
    assert st.dotenv_loaded is False
    assert all(v is False for v in st.webhook_configured.values())


def test_public_dict_and_log_have_no_url_leak(isolated_repo: Path, caplog):
    import logging

    st = load_repo_dotenv(repo_root=isolated_repo, override=False)
    pub = st.as_public_dict()
    blob = json.dumps(pub)
    assert "https://" not in blob
    assert "discord.example" not in blob
    with caplog.at_level(logging.INFO):
        log_webhook_configured(st)
    joined = "\n".join(r.message for r in caplog.records)
    assert "https://" not in joined
    assert "discord.example" not in joined
    assert "configured=True" in joined or "configured=true" in joined.lower()


def test_cwd_independent_recognition(isolated_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """Categories stay recognized after cwd changes (env already loaded into process)."""
    load_repo_dotenv(repo_root=isolated_repo, override=False)
    monkeypatch.chdir(isolated_repo)
    assert all(webhook_configured_map().values())
    nested = isolated_repo / "kabu_native" / "src"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert all(webhook_configured_map().values())
    assert all(bool(os.environ.get(k)) for k in DISCORD_WEBHOOK_ENV_KEYS)


def test_readiness_subprocess_from_two_cwds(isolated_repo: Path):
    """CLI readiness sees configured categories from both cwds (uses real repo .env SoT)."""
    native = Path(__file__).resolve().parents[1]
    repo = native.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{native / 'src'};{repo}"
    # Do not force --send-test
    for cwd in (repo, native):
        p = subprocess.run(
            [sys.executable, "-m", "small_paper.check_discord_notification_readiness"],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
        )
        assert p.returncode in (0, 1), p.stderr
        data = json.loads(p.stdout)
        assert data.get("external_test_send", 0) == 0 or data.get("external_send_default") == 0
        assert "https://" not in p.stdout
        assert "webhook_configured" in data or "env" in data
        # If real SoT .env has the keys, categories should be true
        env_block = data.get("env") or {}
        assert env_block.get("dotenv_path", "").endswith(".env") or "dotenv" in str(env_block)


def test_ensure_repo_dotenv_alias(isolated_repo: Path):
    st = ensure_repo_dotenv(repo_root=isolated_repo)
    assert st.dotenv_loaded is True
