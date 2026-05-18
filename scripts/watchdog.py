"""
tradebotfile プロセス監視（discord_issue_bot / paper_trade の自動復帰）。

* 5 分間隔で psutil により対象プロセスの有無を確認する。
* 欠落時のみ scripts/start_*.bat を起動する（bat 側でも二重起動を抑止）。
* 可能なら環境変数 PAPER_LOG_CHANNEL_ID へ Discord Bot API で短文通知する。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, time as time_of_day
from pathlib import Path
from typing import Optional

try:
    import psutil
except ImportError as e:  # pragma: no cover
    print("watchdog requires psutil. Install: pip install psutil", file=sys.stderr)
    raise SystemExit(1) from e

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

try:
    import requests
except ImportError:
    requests = None  # type: ignore[misc, assignment]

JST_NAME = "Asia/Tokyo"
INTERVAL_SEC = 300
PAPER_TRADE_START = time_of_day(8, 45)
PAPER_TRADE_END = time_of_day(15, 40)

_JST_TZ: object | None = None


def _jst_timezone():
    """Windows 等で tzdata 未導入でも落ちないよう JST を解決する。"""
    global _JST_TZ
    if _JST_TZ is not None:
        return _JST_TZ
    try:
        from zoneinfo import ZoneInfo

        _JST_TZ = ZoneInfo(JST_NAME)
    except Exception:
        from datetime import timedelta, timezone

        _JST_TZ = timezone(timedelta(hours=9), name="JST")
    return _JST_TZ


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_env(root: Path, log: logging.Logger) -> Path:
    env_path = (root / ".env").resolve()
    if load_dotenv is None:
        log.info("dotenv: not installed; skip load; path=%s", env_path)
        return env_path
    if not env_path.is_file():
        log.info("dotenv: path=%s exists=False (skip load)", env_path)
        return env_path
    load_dotenv(dotenv_path=env_path, override=False)
    log.info("dotenv: loaded path=%s exists=True", env_path)
    return env_path


def _now_jst() -> datetime:
    return datetime.now(_jst_timezone())


def _weekday_jst(now: datetime) -> int:
    return int(now.weekday())


def _in_paper_trade_window(now: datetime) -> bool:
    if _weekday_jst(now) >= 5:
        return False
    t = now.time()
    return PAPER_TRADE_START <= t <= PAPER_TRADE_END


def _proc_cmdline(p: psutil.Process) -> str:
    try:
        parts = p.cmdline() or []
    except (psutil.Error, OSError, PermissionError):
        return ""
    return " ".join(parts).replace("\\", "/")


def _is_python_proc(p: psutil.Process) -> bool:
    try:
        name = (p.name() or "").lower()
    except (psutil.Error, OSError):
        return False
    return "python" in name


def issue_bot_running() -> bool:
    needle = "discord_issue_bot.py"
    for p in psutil.process_iter():
        try:
            if not _is_python_proc(p):
                continue
            if needle in _proc_cmdline(p):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def paper_trade_running() -> bool:
    for p in psutil.process_iter():
        try:
            if not _is_python_proc(p):
                continue
            cl = _proc_cmdline(p)
            if (
                ("yahoo_kabu_watch.py" in cl or "market.yahoo.watch" in cl)
                and "--paper-trade" in cl
            ):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _discord_bot_token() -> str:
    return (
        (os.getenv("DISCORD_TOKEN") or "").strip()
        or (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    )


def notify_paper_log(message: str, log: logging.Logger) -> None:
    if requests is None:
        return
    ch = (os.getenv("PAPER_LOG_CHANNEL_ID") or "").strip()
    tok = _discord_bot_token()
    if not ch or not tok:
        return
    url = f"https://discord.com/api/v10/channels/{ch}/messages"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bot {tok}"},
            json={"content": message[:2000]},
            timeout=15,
        )
        if r.status_code >= 400:
            log.warning("Discord notify failed: %s %s", r.status_code, r.text[:200])
    except OSError as e:
        log.warning("Discord notify error: %s", e)


def _run_bat(root: Path, name: str, log: logging.Logger) -> Optional[subprocess.Popen]:
    bat = (root / "scripts" / name).resolve()
    if not bat.is_file():
        log.error("missing script: %s", bat)
        return None
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            ["cmd", "/c", str(bat)],
            cwd=str(root),
            creationflags=creationflags,
        )
        log.info(
            "spawned bat=%s abs=%s cwd=%s child_pid=%s",
            bat.name,
            bat,
            root,
            proc.pid,
        )
        return proc
    except OSError as e:
        log.error("failed to spawn %s: %s", bat, e)
        return None


def _setup_logging() -> logging.Logger:
    log = logging.getLogger("tradebot_watchdog")
    log.setLevel(logging.INFO)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"),
        )
        log.addHandler(h)
    return log


def main() -> int:
    root = _project_root()
    log = _setup_logging()

    env_path = _load_env(root, log)
    runtime = (root / "logs" / "runtime").resolve()
    runtime.mkdir(parents=True, exist_ok=True)

    issue_bat = (root / "scripts" / "start_issue_bot.bat").resolve()
    paper_bat = (root / "scripts" / "start_paper_trade.bat").resolve()

    log.info("watchdog bootstrap: os.getcwd()=%s", os.getcwd())
    log.info("watchdog bootstrap: sys.executable=%s", sys.executable)
    log.info("watchdog bootstrap: __file__=%s", Path(__file__).resolve())
    log.info("watchdog bootstrap: ROOT=%s", root)
    log.info("watchdog bootstrap: dotenv_path=%s", env_path)
    log.info("watchdog bootstrap: issue_bot_bat=%s", issue_bat)
    log.info("watchdog bootstrap: paper_trade_bat=%s", paper_bat)
    tz = _jst_timezone()
    log.info(
        "watchdog bootstrap: jst_tz=%s (install tzdata package for IANA names on Windows)",
        tz,
    )
    log.info(
        "watchdog started (interval=%ss, root=%s)",
        INTERVAL_SEC,
        root,
    )

    try:
        while True:
            now = _now_jst()
            in_win = _in_paper_trade_window(now)

            issue_restart = False
            ib_up = issue_bot_running()
            if not ib_up:
                log.warning(
                    "issue_bot: restart attempt (running=False bat=%s cwd=%s)",
                    issue_bat,
                    root,
                )
                proc = _run_bat(root, "start_issue_bot.bat", log)
                if proc is None:
                    log.error(
                        "issue_bot: start_issue_bot.bat spawn FAILED (no child process)"
                    )
                else:
                    log.info(
                        "issue_bot: start_issue_bot.bat spawn OK child_pid=%s",
                        proc.pid,
                    )
                    time.sleep(4)
                    seen = issue_bot_running()
                    log.info(
                        "issue_bot: post-restart alive check after 4s running=%s",
                        seen,
                    )
                    log.info("[WATCHDOG] restarted issue_bot")
                    notify_paper_log("[WATCHDOG] restarted issue_bot", log)
                issue_restart = proc is not None

            paper_restart = False
            if in_win:
                pt_up = paper_trade_running()
                if not pt_up:
                    log.warning(
                        "paper_trade: restart attempt (running=False bat=%s cwd=%s)",
                        paper_bat,
                        root,
                    )
                    proc_pt = _run_bat(root, "start_paper_trade.bat", log)
                    if proc_pt is None:
                        log.error(
                            "paper_trade: start_paper_trade.bat spawn FAILED "
                            "(no child process)"
                        )
                    else:
                        log.info(
                            "paper_trade: start_paper_trade.bat spawn OK child_pid=%s",
                            proc_pt.pid,
                        )
                        time.sleep(4)
                        seen_pt = paper_trade_running()
                        log.info(
                            "paper_trade: post-restart alive check after 4s "
                            "running=%s",
                            seen_pt,
                        )
                        log.info("[WATCHDOG] restarted paper_trade")
                        notify_paper_log("[WATCHDOG] restarted paper_trade", log)
                    paper_restart = proc_pt is not None

            log.info(
                "tick jst=%s paper_window=%s issue_bot=%s paper_trade=%s",
                now.strftime("%Y-%m-%d %H:%M"),
                "yes" if in_win else "no",
                "restarted" if issue_restart else "ok",
                "restarted" if paper_restart else ("ok" if in_win else "idle"),
            )

            time.sleep(INTERVAL_SEC)
    except KeyboardInterrupt:
        log.info("watchdog shutdown (KeyboardInterrupt)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
