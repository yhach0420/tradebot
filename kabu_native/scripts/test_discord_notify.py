#!/usr/bin/env python3
"""
kabu_native Discord Webhook 接続テスト（市場時間・ENTRY 条件なし）。

.env の KABU_SHADOW_DISCORD_WEBHOOK_URL に [KABU_PAPER] TEST NOTIFICATION を送る。
実発注設定（order_enabled 等）には触れない。

例::
    python kabu_native/scripts/test_discord_notify.py
    python kabu_native/scripts/test_discord_notify.py --webhook-env KABU_SHADOW_DISCORD_WEBHOOK_URL
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

_DEFAULT_ENV = "KABU_SHADOW_DISCORD_WEBHOOK_URL"
_TEST_TITLE = "[KABU_PAPER] TEST NOTIFICATION"


def _paths() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    return repo_root, native_root


def _bootstrap(repo_root: Path) -> None:
    src_root = repo_root / "kabu_native" / "src"
    if not src_root.is_dir():
        src_root = repo_root.parent / "kabu_native" / "src"
    native_src = Path(__file__).resolve().parents[1] / "src"
    for p in (native_src, src_root, repo_root):
        s = str(p.resolve())
        if s not in sys.path:
            sys.path.insert(0, s)
    try:
        from api.rest_client import load_kabu_env

        load_kabu_env(repo_root=repo_root)
    except ImportError:
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=repo_root / ".env", override=False)
        except ImportError:
            pass


def _send_test(webhook_url: str) -> tuple[bool, str]:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    payload = {
        "content": _TEST_TITLE,
        "embeds": [
            {
                "title": _TEST_TITLE,
                "description": "kabu_native Webhook connectivity test (no orders, no shadow loop).",
                "color": 0x4A5568,
                "fields": [
                    {"name": "status", "value": "test", "inline": True},
                    {"name": "sent_at", "value": now_utc, "inline": True},
                    {
                        "name": "note",
                        "value": "仮想売買テスト通知 / 発注なし",
                        "inline": False,
                    },
                ],
                "footer": {"text": "test_discord_notify.py"},
            }
        ],
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
    except requests.RequestException as e:
        return False, f"request failed: {e}"
    if resp.status_code >= 400:
        body = (resp.text or "")[:300]
        return False, f"HTTP {resp.status_code}: {body}"
    return True, f"HTTP {resp.status_code} OK"


def main() -> int:
    parser = argparse.ArgumentParser(description="Send [KABU_PAPER] Discord webhook test message")
    parser.add_argument(
        "--webhook-env",
        default=_DEFAULT_ENV,
        help=f"env var name for webhook URL (default: {_DEFAULT_ENV})",
    )
    args = parser.parse_args()

    repo_root, native_root = _paths()
    _bootstrap(repo_root)

    env_name = str(args.webhook_env).strip() or _DEFAULT_ENV
    webhook = (os.getenv(env_name) or "").strip()
    env_path = repo_root / ".env"

    print(f"repo_root: {repo_root}")
    print(f"native_root: {native_root}")
    print(f".env: {env_path} ({'exists' if env_path.is_file() else 'missing'})")
    print(f"webhook_env: {env_name}")

    if not webhook:
        print(f"FAIL: {env_name} is not set or empty in environment / .env")
        return 1

    # Do not print full URL (secret)
    print(f"webhook: set (len={len(webhook)})")
    print(f"sending: {_TEST_TITLE}")

    ok, detail = _send_test(webhook)
    if ok:
        print(f"SUCCESS: {detail}")
        print("Check Discord for the test message.")
        return 0
    print(f"FAIL: {detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
