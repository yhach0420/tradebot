"""Phase687W9 — Capture process supervisor (max 1 auto-restart).

Owns restart policy outside Paper. Never imports trading paths.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[2]
MAX_AUTO_RESTARTS = 1


def _day_dir(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "data" / "market_capture" / trading_date


def _scheduled_end_passed(trading_date: str) -> bool:
    y, m, d = int(trading_date[:4]), int(trading_date[4:6]), int(trading_date[6:8])
    end = datetime(y, m, d, 15, 35, tzinfo=JST)
    return datetime.now(JST) >= end


def _operator_stop(day: Path) -> bool:
    return (day / "operator_stop.flag").is_file()


def _seal_exists(day: Path) -> bool:
    return (day / "capture_seal.json").is_file()


def run_supervised(
    *,
    native_root: Path,
    trading_date: str,
    synthetic: bool = False,
    synthetic_events: int = 100,
    topology: str = "PASSIVE_DUAL_WEBSOCKET",
    python_exe: Optional[str] = None,
) -> int:
    """Run sidecar; on abnormal exit before seal/15:35, restart once with new part."""
    exe = python_exe or sys.executable
    code_root = NATIVE_ROOT if (NATIVE_ROOT / "src" / "small_paper").is_dir() else native_root
    day = _day_dir(native_root, trading_date)
    day.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    src = str(code_root / "src")
    repo = str(code_root.parent)
    env["PYTHONPATH"] = f"{src};{repo}" if sys.platform == "win32" else f"{src}:{repo}"
    env["PYTHONIOENCODING"] = "utf-8"

    attempt = 0
    last_code = 1
    while attempt <= MAX_AUTO_RESTARTS:
        cmd = [
            exe,
            "-m",
            "small_paper.market_capture_sidecar",
            "--native-root",
            str(native_root),
            "--trading-date",
            trading_date,
            "--topology",
            topology,
            "--restart-count",
            str(attempt),
        ]
        if synthetic:
            cmd.extend(["--synthetic", "--synthetic-events", str(synthetic_events)])

        hist = {
            "at": datetime.now(JST).isoformat(timespec="seconds"),
            "attempt": attempt,
            "action": "start",
            "cmd_tail": cmd[-6:],
        }
        with (day / "restart_history.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(hist, ensure_ascii=False, separators=(",", ":")) + "\n")

        creationflags = 0x00000200 if sys.platform == "win32" else 0  # NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            cmd,
            cwd=str(code_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=(day / f"sidecar_stderr_attempt{attempt}.log").open("w", encoding="utf-8"),
            creationflags=creationflags,
            start_new_session=(sys.platform != "win32"),
        )
        last_code = proc.wait()

        if _seal_exists(day) or _operator_stop(day) or _scheduled_end_passed(trading_date):
            return int(last_code or 0)
        if last_code == 0:
            return 0
        # Abnormal exit — restart at most once
        if attempt >= MAX_AUTO_RESTARTS:
            with (day / "restart_history.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(
                    json.dumps(
                        {
                            "at": datetime.now(JST).isoformat(timespec="seconds"),
                            "attempt": attempt,
                            "action": "give_up",
                            "exit_code": last_code,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            return int(last_code or 1)
        with (day / "restart_history.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(
                json.dumps(
                    {
                        "at": datetime.now(JST).isoformat(timespec="seconds"),
                        "attempt": attempt,
                        "action": "auto_restart",
                        "exit_code": last_code,
                        "policy": "new_part_no_append",
                        "next_attempt": attempt + 1,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
        attempt += 1
        time.sleep(0.5)
    return int(last_code or 1)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Market capture supervisor (max 1 restart)")
    p.add_argument("--native-root", type=str, default=str(NATIVE_ROOT))
    p.add_argument("--trading-date", type=str, required=True)
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--synthetic-events", type=int, default=100)
    p.add_argument("--topology", type=str, default="PASSIVE_DUAL_WEBSOCKET")
    args = p.parse_args(list(argv) if argv is not None else None)
    return run_supervised(
        native_root=Path(args.native_root),
        trading_date=args.trading_date,
        synthetic=bool(args.synthetic),
        synthetic_events=int(args.synthetic_events),
        topology=args.topology,
    )


if __name__ == "__main__":
    raise SystemExit(main())
