"""
Discord -> GitHub Issue 作成 Bot（初心者向け・コメント多め）
====================================================

やりたいこと
------------
Discordで `!issue <内容>` を受け取ったら…

1) GitHub REST API で Issue を作成
2) Issue URL を Discord に返信
3) 同じ内容（結果）を Discord Webhook にも通知

必要な環境変数
--------------
- DISCORD_TOKEN: Discord Bot トークン
- GITHUB_TOKEN:  GitHub Personal Access Token（repo への Issue 作成権限が必要）
- DISCORD_WEBHOOK_URL: 通知先 Webhook URL（任意ではなく「仕様で必須」）

注意
----
このファイルは `discord.py` を使います。
同期HTTPライブラリの `requests` は、そのまま async 関数内で呼ぶと Bot が固まりやすいので、
`asyncio.to_thread()` で別スレッドに逃がして「イベントループをブロックしない」ようにしています。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Optional

import discord
from discord.ext import commands

# requests は標準ライブラリではないので、無い場合は分かりやすく案内します。
try:
    import requests
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "このBotは 'requests' が必要です。次を実行して入れてください:\n"
        "  pip install -r requirements.txt\n"
    ) from e


# =========================
# 設定（このBotの固定値）
# =========================
GITHUB_OWNER = "yhach0420"
GITHUB_REPO = "tradebot"
GITHUB_API_BASE = "https://api.github.com"


# =========================
# 環境変数の読み込み
# =========================
def require_env(name: str) -> str:
    """
    必須の環境変数を読むヘルパー。
    無ければ即エラーにします（起動時に気付ける方が初心者に優しい）。
    """
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"環境変数 {name} が設定されていません。")
    return v


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")  # 起動後に validate します
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# =========================
# GitHub Issue 作成
# =========================
@dataclass(frozen=True)
class CreatedIssue:
    number: int
    url: str  # html_url（ブラウザで開けるURL）
    title: str


def _github_create_issue_sync(
    *,
    github_token: str,
    title: str,
    body: str,
) -> CreatedIssue:
    """
    GitHub REST API（同期版）で Issue を作成します。
    ここは requests を使うため同期関数にしておき、呼び出し側で別スレッドに逃がします。
    """
    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/issues"
    headers = {
        # GitHub Token は Authorization ヘッダで渡すのが基本です。
        "Authorization": f"Bearer {github_token}",
        # 2020年代以降はこのヘッダが推奨です（GitHubの仕様変更で必要になる場合があります）。
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/vnd.github+json",
    }
    payload = {
        "title": title,
        "body": body,
    }

    r = requests.post(api_url, headers=headers, json=payload, timeout=20)
    # 200台以外はここで例外にして、上位でまとめてエラーメッセージにします。
    r.raise_for_status()
    data: dict[str, Any] = r.json()

    number = int(data["number"])
    html_url = str(data["html_url"])
    returned_title = str(data.get("title") or title)

    return CreatedIssue(number=number, url=html_url, title=returned_title)


async def github_create_issue(
    *,
    github_token: str,
    title: str,
    body: str,
) -> CreatedIssue:
    """
    async から呼べる Issue 作成関数。
    requests は同期なので、`asyncio.to_thread()` で別スレッド実行します。
    """
    return await asyncio.to_thread(
        _github_create_issue_sync,
        github_token=github_token,
        title=title,
        body=body,
    )


# =========================
# Discord Webhook 通知
# =========================
def _webhook_post_sync(webhook_url: str, content: str) -> None:
    """
    Discord Webhook へ通知（同期版）。
    """
    # Webhook は POST {content: "..."} が一番シンプルです。
    r = requests.post(webhook_url, json={"content": content}, timeout=20)
    r.raise_for_status()


async def webhook_post(webhook_url: str, content: str) -> None:
    """
    async から呼べる Webhook 通知。
    """
    await asyncio.to_thread(_webhook_post_sync, webhook_url, content)


# =========================
# Discord Bot 本体
# =========================
intents = discord.Intents.default()
intents.message_content = True  # これを True にしないと prefix コマンドが受け取れません（要: Portal設定）

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    # 起動確認用ログ（Botがオンラインになったら表示される）
    if bot.user:
        print(f"Logged in as: {bot.user} (id={bot.user.id})")
    else:
        print("Logged in (bot.user is None)")


def _compact_title(text: str, max_len: int = 80) -> str:
    """
    GitHub Issue の title は短い方が見やすいので、
    先頭から max_len 文字だけ切って使います。
    """
    s = " ".join(text.strip().split())  # 改行/連続空白をつぶして見た目を安定させる
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _build_issue_body(
    *,
    content: str,
    author: discord.abc.User,
    channel: discord.abc.GuildChannel | discord.abc.PrivateChannel,
    message_url: Optional[str],
) -> str:
    """
    Issue の本文を組み立てます。
    「Discordで誰が/どこで書いた指示か」が後から追えるように残します。
    """
    lines: list[str] = []
    lines.append("## Discord からの指示")
    lines.append("")
    lines.append(content.strip())
    lines.append("")
    lines.append("## 送信元")
    lines.append(f"- user: {author} (id={getattr(author, 'id', 'N/A')})")

    # channel は DM と guild channel で型が分かれます。安全に情報を取り出します。
    ch_name = getattr(channel, "name", None)
    guild = getattr(channel, "guild", None)
    guild_name = getattr(guild, "name", None) if guild else None
    if guild_name and ch_name:
        lines.append(f"- channel: {guild_name} / #{ch_name}")
    elif ch_name:
        lines.append(f"- channel: {ch_name}")
    else:
        lines.append("- channel: (unknown)")

    if message_url:
        lines.append(f"- message: {message_url}")

    return "\n".join(lines).strip() + "\n"


@bot.command(name="issue")
async def issue_command(ctx: commands.Context, *, content: str = "") -> None:
    """
    Discord コマンド: !issue <内容>

    - content: ユーザーが書いた Issue 内容（自由文）
    """
    # 1) 入力チェック（空だと困るので、使い方を返します）
    if not content.strip():
        await ctx.reply("使い方: `!issue <内容>` 例: `!issue エラー修正：ログインできない`")
        return

    # 2) 起動に必要な環境変数があるか確認
    #    ここは「起動前に fail」でも良いのですが、Discord上でも原因が分かる方が親切なので両方でケアします。
    try:
        discord_token = DISCORD_TOKEN or require_env("DISCORD_TOKEN")
        github_token = GITHUB_TOKEN or require_env("GITHUB_TOKEN")
        webhook_url = DISCORD_WEBHOOK_URL or require_env("DISCORD_WEBHOOK_URL")
    except Exception as e:
        await ctx.reply(f"環境変数の設定が不足しています: {e}")
        return

    # discord_token はここでは使いませんが、「環境変数が揃っている」ことを保証するため読みます
    _ = discord_token

    # 3) GitHub Issue の title/body を準備
    title = _compact_title(content)
    message_url = None
    try:
        # discord.py の Message には jump_url があり、Discord内でメッセージを開けます（DM等で無い場合もあります）
        if ctx.message:
            message_url = getattr(ctx.message, "jump_url", None)
    except Exception:
        message_url = None

    body = _build_issue_body(
        content=content,
        author=ctx.author,
        channel=ctx.channel,
        message_url=message_url,
    )

    # 4) Issue を作成（失敗したら理由を返す）
    try:
        created = await github_create_issue(
            github_token=github_token,
            title=title,
            body=body,
        )
    except requests.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        # GitHub からのエラーメッセージも見えるとデバッグが楽
        detail = ""
        try:
            detail = f" / detail={e.response.text}"
        except Exception:
            pass
        await ctx.reply(f"GitHub Issue 作成に失敗しました (HTTP {status}){detail}")
        return
    except Exception as e:
        await ctx.reply(f"GitHub Issue 作成に失敗しました: {e}")
        return

    # 5) まずは Discord へ Issue URL を返信（ユーザーがすぐ見られるのが大事）
    await ctx.reply(f"Issue を作成しました: {created.url}")

    # 6) Webhook にも通知（失敗しても Issue 作成は成功しているので、ここは「失敗した」とだけ返す）
    webhook_message = (
        "GitHub Issue を作成しました\n"
        f"- repo: {GITHUB_OWNER}/{GITHUB_REPO}\n"
        f"- title: {created.title}\n"
        f"- url: {created.url}\n"
    )
    try:
        await webhook_post(webhook_url, webhook_message)
    except Exception as e:
        await ctx.reply(f"Webhook 通知に失敗しました: {e}")


def main() -> int:
    """
    エントリーポイント。
    起動時に必須環境変数が無ければ、ここで止めます。
    """
    # 早期に原因が分かるように、起動時にも必須チェックします。
    _ = require_env("DISCORD_TOKEN")
    _ = require_env("GITHUB_TOKEN")
    _ = require_env("DISCORD_WEBHOOK_URL")

    bot.run(require_env("DISCORD_TOKEN"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

