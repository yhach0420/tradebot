"""V1R Paper Primary production launcher — fail-closed, no PBv2 Primary fallback.

Replaces classic trailing-MFE observer as Paper Primary on the production bat path.
PBv2 may only run as SHADOW_ONLY (not started here as primary).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from small_paper.v1r_exit_v2_activation_gate import (
    ASSERTION_FAIL,
    assert_exit_v2_primary_roles,
)
from small_paper.v1r_primary_activation_gate import heartbeat_identity_fields
from small_paper.v1r_day_engine import run_frozen_day
from small_paper.v1r_dual_strategy_replay import run_dual_day

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[2]


def _write_hb(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def launch_primary(
    *,
    mode: str = "live",
    replay_day: Optional[str] = None,
    session_dir: Optional[Path] = None,
) -> int:
    """
    mode:
      live  — assert EXIT V2 roles; write startup contract + heartbeats.
              No FIXED600 Primary fallback. No PBv2 Primary fallback.
      offline_replay — dual Arch E + FIXED600 Control (no Discord, no broker).
    """
    assertion = assert_exit_v2_primary_roles()
    print(assertion.startup_block, flush=True)
    if not assertion.ok:
        print(f"[V1R EXIT V2 PRIMARY] {ASSERTION_FAIL}: {assertion.reason}", flush=True)
        print("[V1R EXIT V2 PRIMARY] NO PAPER PRIMARY — FIXED600/PBv2 Primary fallback FORBIDDEN", flush=True)
        return 2

    out = session_dir or (
        NATIVE / "results" / "small_paper" / datetime.now(JST).strftime("%Y%m%d")
        / f"v1r_primary_{datetime.now(JST).strftime('%H%M%S')}"
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "startup_contract.txt").write_text(assertion.startup_block, encoding="utf-8")
    (out / "role_assertion.json").write_text(
        json.dumps(assertion.to_dict(), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    hb_path = out / "heartbeat.jsonl"
    _write_hb(hb_path, heartbeat_identity_fields(
        current_anchor=None,
        next_anchor="09:05",
        open_n=0,
        pending_n=0,
        extra={
            "mode": mode,
            "ready": True,
            "primary_exit": "ARCH_E_V2",
            "control": "FIXED600_SHADOW_CONTROL",
            "guard_id": assertion.identity.get("guard_id"),
            "continuation_id": assertion.identity.get("continuation_id"),
            "strategy_sha": assertion.identity.get("strategy_sha"),
        },
    ))

    if mode == "offline_replay":
        day = replay_day or "20260810"
        print(f"[V1R EXIT V2] offline dual replay day={day} (no broker / no Discord)", flush=True)
        res = run_dual_day(day, label="production_offline_dual")
        # strip bulky events for disk summary
        slim = {
            k: v for k, v in res.items() if k not in ("primary", "control")
        }
        if res.get("ok"):
            slim["primary_summary"] = res["primary"]["summary"]
            slim["control_summary"] = res["control"]["summary"]
            slim["comparison"] = res["comparison"]
        (out / "offline_dual_replay_result.json").write_text(
            json.dumps(slim, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        _write_hb(hb_path, heartbeat_identity_fields(
            current_anchor="15:00",
            next_anchor=None,
            open_n=0,
            pending_n=0,
            extra={
                "mode": mode,
                "replay_ok": bool(res.get("ok")),
                "primary_fills": (res.get("primary") or {}).get("summary", {}).get("n"),
                "control_fills": (res.get("control") or {}).get("summary", {}).get("n"),
                "primary_pnl": (res.get("primary") or {}).get("summary", {}).get("total"),
                "control_pnl": (res.get("control") or {}).get("summary", {}).get("total"),
            },
        ))
        if not res.get("ok"):
            print(f"[V1R EXIT V2] offline dual replay blocked: {res.get('blocked')}", flush=True)
            return 3
        print(
            f"[V1R EXIT V2] dual COMPLETE primary_n={(res.get('primary') or {}).get('summary', {}).get('n')} "
            f"control_n={(res.get('control') or {}).get('summary', {}).get('n')}",
            flush=True,
        )
        return 0

    print("[V1R EXIT V2] LIVE gate PASS — PAPER_PRIMARY=Arch E; Control=FIXED600 SHADOW; PBv2 fallback disabled", flush=True)
    print("[V1R EXIT V2] Waiting for market / operator stop. Heartbeat identity active.", flush=True)
    for i in range(3):
        _write_hb(hb_path, heartbeat_identity_fields(
            current_anchor=None,
            next_anchor="09:05",
            open_n=0,
            pending_n=0,
            extra={"mode": "live", "hb_seq": i, "primary_exit": "ARCH_E_V2"},
        ))
        time.sleep(0.2)
    (out / "primary_role_bound.json").write_text(json.dumps({
        "primary": "V1R_EXIT_V2_ARCH_E_PAPER_PRIMARY",
        "control": "FIXED600_SHADOW_CONTROL",
        "pbv2_primary_fallback": False,
        "fixed600_primary_fallback": False,
        "classic_trailing_mfe_as_primary": False,
        "submit_cancel_live": "0/0/0",
        "session_dir": str(out),
        "identity": assertion.identity,
    }, indent=2, default=str), encoding="utf-8")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="V1R Paper Primary launcher (fail-closed)")
    p.add_argument("--mode", choices=["live", "offline_replay"], default="live")
    p.add_argument("--replay-day", default=None)
    p.add_argument("--session-dir", default=None)
    args = p.parse_args(argv)
    return launch_primary(
        mode=args.mode,
        replay_day=args.replay_day,
        session_dir=Path(args.session_dir) if args.session_dir else None,
    )


if __name__ == "__main__":
    sys.exit(main())
