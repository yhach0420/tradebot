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
import json
import os
from dataclasses import dataclass
from pathlib import Path
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

# 旧互換:
# - 以前の設定名 `DISCORD_BOT_TOKEN` が残っている場合でも、初心者が詰まらないように
#   警告を出しつつ動作は継続します（最終的には DISCORD_TOKEN に統一してください）。
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")


def get_discord_token_required() -> str:
    """
    DiscordのBotトークンを必須取得します（環境変数名を DISCORD_TOKEN に統一）。

    仕様:
    - 正: DISCORD_TOKEN
    - 旧: DISCORD_BOT_TOKEN（廃止予定）
      - 残っていたら警告を表示
      - DISCORD_TOKEN が未設定なら旧値でフォールバックして動作継続
    """
    tok = (DISCORD_TOKEN or "").strip()
    old = (DISCORD_BOT_TOKEN or "").strip()
    if old:
        print("警告: 環境変数 DISCORD_BOT_TOKEN は廃止予定です。DISCORD_TOKEN に移行してください。")
        if not tok:
            tok = old
    if not tok:
        # ここまで来たら両方とも無い
        raise RuntimeError("環境変数 DISCORD_TOKEN が設定されていません。")
    return tok

# =========================
# チャンネル役割の分離（Issue #? 拡張）
# =========================
# 目的:
# - 「命令ログ（コマンド）」と「通知ログ（アラート）」を別チャンネルに分けたい。
#
# 仕様:
# - CONTROL_CHANNEL_ID: Botコマンド受付専用チャンネル
#   - !watch add / !watch remove / !watch list / !issue /（将来 !set など）
# - ALERT_CHANNEL_ID: 通知送信用チャンネル
#   - 条件一致通知 / 条件外れ通知 / 候補価格変更通知（trade監視側）
#   - このBotでは「Issue作成結果の通知」をここに集約できます
#
# 設定方法:
# - どちらも .env（=環境変数）で設定できます。
# - 互換性のため、未設定なら「制限なし / 従来挙動（Webhook通知）」で動きます。
#
# 例（.env）:
#   CONTROL_CHANNEL_ID=123456789012345678
#   ALERT_CHANNEL_ID=987654321098765432
CONTROL_CHANNEL_ID = os.getenv("CONTROL_CHANNEL_ID", "").strip()
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID", "").strip()


def _parse_channel_id(raw: str) -> Optional[int]:
    """
    環境変数で渡されたチャンネルID（文字列）を int に変換します。
    - 未設定（空文字）の場合は None
    - 数字以外が混ざっていたら None（起動時に分かるように main() でもチェックします）
    """
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


CONTROL_CHANNEL_ID_INT: Optional[int] = _parse_channel_id(CONTROL_CHANNEL_ID)
ALERT_CHANNEL_ID_INT: Optional[int] = _parse_channel_id(ALERT_CHANNEL_ID)


def _is_control_channel(ctx: commands.Context) -> bool:
    """
    「このコマンドが CONTROL チャンネルで実行されたか？」を判定します。

    仕様:
    - CONTROL_CHANNEL_ID が未設定なら、どのチャンネルでもコマンドを受け付けます（互換性）。
    - 設定されている場合は、そのチャンネルID以外ではコマンドを拒否します。
    """
    if CONTROL_CHANNEL_ID_INT is None:
        return True
    ch = getattr(ctx, "channel", None)
    ch_id = getattr(ch, "id", None)
    return int(ch_id) == int(CONTROL_CHANNEL_ID_INT) if ch_id is not None else False


async def _reply_control_only(ctx: commands.Context) -> None:
    """
    コマンドが別チャンネルで実行された場合の案内メッセージ。
    """
    if CONTROL_CHANNEL_ID_INT is None:
        # 未設定なら基本的にここに来ない想定ですが、念のため
        await ctx.reply("このコマンドは実行できません。")
        return
    await ctx.reply("コマンドは control チャンネルで実行してください。")


async def _send_alert_message(content: str) -> None:
    """
    ALERT チャンネルに「通知だけ」を送るためのヘルパー。

    注意:
    - ALERT_CHANNEL_ID が未設定の場合は、何もしません（互換性）。
    - 送信に失敗しても例外は投げます（呼び出し側で握りつぶす/ログする）
    """
    if ALERT_CHANNEL_ID_INT is None:
        return

    # 1) キャッシュから探す（bot が見たことのあるチャンネルなら速い）
    ch = bot.get_channel(ALERT_CHANNEL_ID_INT)

    # 2) キャッシュに無ければ fetch する（権限があれば取れる）
    if ch is None:
        ch = await bot.fetch_channel(ALERT_CHANNEL_ID_INT)

    # チャンネル型は色々ありますが、とにかく send できるものだけ送ります
    if hasattr(ch, "send"):
        await ch.send(content)
    else:
        raise RuntimeError("ALERT_CHANNEL_ID のチャンネルに send できません（権限/種別を確認してください）。")

# =========================
# watchlist.json（Issue #1 拡張）
# =========================
# yahoo_kabu_watch.py 側が監視銘柄を読み込むためのファイルです。
# - 形式: JSON 配列（例: ["7203.T", "9984.T"]）
# - 保存先: この discord_issue_bot.py と同じフォルダ
BASE_DIR = Path(__file__).resolve().parent
WATCHLIST_JSON_PATH = BASE_DIR / "watchlist.json"


def _load_watchlist() -> list[str]:
    """
    watchlist.json を読み込みます。
    - ファイルが無い場合は空リスト
    - 想定外の形なら空リスト（初心者でも壊れにくいように）
    """
    if not WATCHLIST_JSON_PATH.exists():
        return []
    try:
        raw = json.loads(WATCHLIST_JSON_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [str(s).strip() for s in raw if str(s).strip()]
        if isinstance(raw, dict):
            # 念のため: {"symbols": [...]} のような形にも対応
            maybe = raw.get("symbols") or raw.get("watchlist") or []
            if isinstance(maybe, list):
                return [str(s).strip() for s in maybe if str(s).strip()]
    except Exception:
        pass
    return []


def _save_watchlist(symbols: list[str]) -> None:
    """
    watchlist.json を保存します（重複なし・ソートして保存）。
    """
    uniq = sorted({s.strip() for s in symbols if s and s.strip()})
    WATCHLIST_JSON_PATH.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")

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
    # このコマンドは CONTROL チャンネルでのみ受け付けます。
    # （別チャンネルで実行されたら、正しいチャンネルに誘導します）
    if not _is_control_channel(ctx):
        await _reply_control_only(ctx)
        return

    # 1) 入力チェック（空だと困るので、使い方を返します）
    if not content.strip():
        await ctx.reply("使い方: `!issue <内容>` 例: `!issue エラー修正：ログインできない`")
        return

    # 2) 起動に必要な環境変数があるか確認
    #    ここは「起動前に fail」でも良いのですが、Discord上でも原因が分かる方が親切なので両方でケアします。
    try:
        discord_token = get_discord_token_required()
        github_token = GITHUB_TOKEN or require_env("GITHUB_TOKEN")
        # 通知先は 2 通り:
        # - ALERT_CHANNEL_ID が設定されていれば「アラートチャンネルへ送る」
        # - 無ければ従来どおり DISCORD_WEBHOOK_URL に送る
        webhook_url = DISCORD_WEBHOOK_URL or ""
        if ALERT_CHANNEL_ID_INT is None and not webhook_url:
            # どちらも無いと通知できないのでエラー
            _ = require_env("DISCORD_WEBHOOK_URL")
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
        # まずは ALERT チャンネルが設定されていれば、そこへ通知します（通知ログの分離）。
        if ALERT_CHANNEL_ID_INT is not None:
            await _send_alert_message(webhook_message)
        else:
            await webhook_post(webhook_url, webhook_message)
    except Exception as e:
        await ctx.reply(f"Webhook 通知に失敗しました: {e}")


# =========================
# watch コマンド（監視銘柄の管理）
# =========================
# 仕様:
# - !watch add 7203.T
# - !watch remove 7203.T
# - !watch list
#
# 保存先:
# - watchlist.json（このファイルと同じフォルダ）
@bot.group(name="watch", invoke_without_command=True)
async def watch_group(ctx: commands.Context) -> None:
    # watch 系コマンドは CONTROL チャンネルでのみ受け付けます。
    if not _is_control_channel(ctx):
        await _reply_control_only(ctx)
        return
    # サブコマンドが無い場合の案内
    await ctx.reply("使い方: `!watch add <symbol>` / `!watch remove <symbol>` / `!watch list`")


@watch_group.command(name="add")
async def watch_add_command(ctx: commands.Context, symbol: str) -> None:
    if not _is_control_channel(ctx):
        await _reply_control_only(ctx)
        return
    symbol = (symbol or "").strip()
    if not symbol:
        await ctx.reply("symbol が空です。例: `!watch add 7203.T`")
        return

    symbols = _load_watchlist()
    if symbol in symbols:
        await ctx.reply(f"すでに監視中です: {symbol}")
        return

    symbols.append(symbol)
    _save_watchlist(symbols)
    await ctx.reply(f"監視に追加しました: {symbol}")


@watch_group.command(name="remove")
async def watch_remove_command(ctx: commands.Context, symbol: str) -> None:
    if not _is_control_channel(ctx):
        await _reply_control_only(ctx)
        return
    symbol = (symbol or "").strip()
    if not symbol:
        await ctx.reply("symbol が空です。例: `!watch remove 7203.T`")
        return

    symbols = _load_watchlist()
    if symbol not in symbols:
        await ctx.reply(f"監視中ではありません: {symbol}")
        return

    symbols = [s for s in symbols if s != symbol]
    _save_watchlist(symbols)
    await ctx.reply(f"監視から削除しました: {symbol}")


@watch_group.command(name="list")
async def watch_list_command(ctx: commands.Context) -> None:
    if not _is_control_channel(ctx):
        await _reply_control_only(ctx)
        return
    symbols = _load_watchlist()
    if not symbols:
        await ctx.reply("watchlist.json は空です。`!watch add <symbol>` で追加してください。")
        return
    await ctx.reply("監視中銘柄:\n" + "\n".join(f"- {s}" for s in symbols))


def main() -> int:
    """
    エントリーポイント。
    起動時に必須環境変数が無ければ、ここで止めます。
    """
    # 早期に原因が分かるように、起動時にも必須チェックします。
    _ = get_discord_token_required()
    _ = require_env("GITHUB_TOKEN")

    # 通知先はどちらかが必要です:
    # - ALERT_CHANNEL_ID（通知専用チャンネル）
    # - または従来の DISCORD_WEBHOOK_URL（Webhookで特定チャンネルへ送る）
    if ALERT_CHANNEL_ID_INT is None:
        _ = require_env("DISCORD_WEBHOOK_URL")

    # チャンネルIDが設定されているのに数値に変換できない場合は、ここで止めます。
    if CONTROL_CHANNEL_ID and CONTROL_CHANNEL_ID_INT is None:
        raise RuntimeError("CONTROL_CHANNEL_ID が数値ではありません。例: 123456789012345678")
    if ALERT_CHANNEL_ID and ALERT_CHANNEL_ID_INT is None:
        raise RuntimeError("ALERT_CHANNEL_ID が数値ではありません。例: 123456789012345678")

    bot.run(get_discord_token_required())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

