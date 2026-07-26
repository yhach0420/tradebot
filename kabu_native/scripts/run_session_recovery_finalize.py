#!/usr/bin/env python3
"""Phase675 — Offline session Recovery Finalize (Paper only).

Idempotent orphan recovery_forced_close + Summary + seal + C/D backup.
Does not place orders. submit/cancel remain 0.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))


def _now() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    from small_paper.ws_freeze_recovery import load_jsonl

    return load_jsonl(path)


def _count_submit_cancel(session_dir: Path) -> tuple[int, int]:
    submit = cancel = 0
    for name in ("live_order_event.jsonl", "live_order_would_send.jsonl"):
        p = session_dir / name
        if not p.is_file():
            continue
        for e in _load_jsonl(p):
            et = str(e.get("event") or e.get("event_type") or e.get("kind") or "").upper()
            if "SUBMIT" in et and "WOULD" not in et and "DRY" not in et:
                submit += 1
            if "CANCEL" in et and "WOULD" not in et:
                cancel += 1
    # Safety: dry-run would_send must not count as submit
    return submit, cancel


def finalize_session(
    session_dir: Path,
    *,
    reason: str,
    symbols: Optional[list[str]] = None,
    am_pm: str = "pm",
    push_freeze_at: str = "",
    do_archive: bool = True,
    do_external: bool = True,
) -> dict[str, Any]:
    from small_paper.ws_freeze_recovery import (
        append_jsonl,
        apply_orphan_recovery_to_events,
        find_orphan_accepted,
        load_jsonl,
    )

    session_dir = Path(session_dir)
    events_path = session_dir / "small_paper_events.jsonl"
    summary_path = session_dir / "small_paper_summary.json"
    closed_at = _now()
    backup_dir = session_dir / f"recovery_backup_{datetime.now(JST).strftime('%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "small_paper_events.jsonl",
        "small_paper_events.csv",
        "small_paper_positions.csv",
        "small_paper_summary.json",
        "heartbeat.jsonl",
        "errors.jsonl",
    ):
        src = session_dir / name
        if src.is_file():
            shutil.copy2(src, backup_dir / name)

    events = load_jsonl(events_path)
    before_orphans = find_orphan_accepted(events)
    result = apply_orphan_recovery_to_events(
        events,
        recovery_note=reason,
        closed_at=closed_at,
        only_symbols=symbols,
        force_close_at=push_freeze_at or closed_at,
        events_path=events_path,
    )
    if result.exits_appended:
        append_jsonl(events_path, result.exits_appended)
        # append CSV rows if csv exists
        csv_path = session_dir / "small_paper_events.csv"
        if csv_path.is_file() and result.exits_appended:
            with csv_path.open(encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                fieldnames = list(reader.fieldnames or [])
            if fieldnames:
                with csv_path.open("a", encoding="utf-8", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
                    for ev in result.exits_appended:
                        row = {k: ev.get(k, "") for k in fieldnames}
                        w.writerow(row)

    # Rebuild summary from previous + recovery flags
    prev: dict[str, Any] = {}
    if summary_path.is_file():
        try:
            prev = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    accepted = [e for e in events if e.get("event_type") == "accepted"]
    exits = [e for e in events if e.get("event_type") == "observer_exit"]
    recovery_exits = [e for e in exits if e.get("exit_reason") == "recovery_forced_close"]
    normal_exits = [e for e in exits if e.get("exit_reason") != "recovery_forced_close"]
    still = find_orphan_accepted(events)
    submit_n, cancel_n = _count_submit_cancel(session_dir)

    recovery_block = {
        "status": "INCOMPLETE_DATA_FORCE_FINALIZED",
        "reason": reason,
        "closed_at": closed_at,
        "orphan_forced_close_count": result.orphan_forced_close_count,
        "orphan_position_ids": result.orphan_position_ids,
        "skipped_already_closed": result.skipped_already_closed,
        "exit_reason": "recovery_forced_close",
        "push_freeze_at": push_freeze_at or None,
        "note": f"{am_pm.upper()} Summary incomplete due to push freeze; orphans closed without live orders.",
        "active_positions_after": len(still),
        "submit_count": submit_n,
        "cancel_count": cancel_n,
        "before_orphan_count": len(before_orphans),
    }
    summary = dict(prev)
    summary.update(
        {
            "ended_at": closed_at,
            "accepted_count": len(accepted),
            "observer_exit_count": len(exits),
            "normal_exit_count": len(normal_exits),
            "recovery_forced_close_count": len(recovery_exits),
            "active_positions": len(still),
            "session_end_reason": "recovery_forced_close",
            f"{am_pm}_recovery": recovery_block,
            f"{am_pm}_summary_incomplete": True,
            f"{am_pm}_finalize_forced": True,
            "order_enabled": False,
            "paper_only": True,
            "submit_count": submit_n,
            "cancel_count": cancel_n,
            "phase675_recovery_finalize": True,
        }
    )
    # Shadow summary stub from existing shadow keys if present
    shadow_keys = [k for k in summary if "shadow" in k.lower()]
    summary["shadow_summary_available"] = bool(shadow_keys)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_path = session_dir / f"{am_pm}_summary_incomplete_recovery.md"
    md_path.write_text(
        "\n".join(
            [
                f"# {am_pm.upper()} Summary (INCOMPLETE — recovery finalize)",
                "",
                f"- closed_at: `{closed_at}`",
                f"- reason: `{reason}`",
                f"- orphan_forced_close_count: **{result.orphan_forced_close_count}**",
                f"- active_positions_after: **{len(still)}**",
                f"- accepted: {len(accepted)} / normal_exit: {len(normal_exits)} / recovery_exit: {len(recovery_exits)}",
                f"- submit/cancel: {submit_n}/{cancel_n}",
                f"- data completeness: INCOMPLETE (push froze; offline recovery finalize)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    seal = {
        "session_id": session_dir.name,
        "sealed_at": closed_at,
        "session_seal_status": "SEALED_INCOMPLETE_RECOVERY",
        "reason": reason,
        "orphan_forced_close_count": result.orphan_forced_close_count,
        "orphan_position_ids": result.orphan_position_ids,
        "active_positions": len(still),
        "submit_count": submit_n,
        "cancel_count": cancel_n,
        "note": "Forced recovery after WebSocket freeze; do not treat as normal SEALED_VALID soak session.",
    }
    (session_dir / "session_seal.json").write_text(
        json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    archive_res: dict[str, Any] = {"skipped": not do_archive}
    external_res: dict[str, Any] = {"skipped": not do_external}
    if do_archive:
        try:
            from small_paper.data_retention_guard import archive_session_copy

            archive_res = archive_session_copy(session_dir, root=NATIVE)
            summary["session_archive_backup"] = archive_res
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            archive_res = {"ok": False, "error": str(exc)}
    if do_external:
        try:
            from small_paper.external_backup import after_session_archive

            external_res = after_session_archive(session_dir, native=NATIVE)
            summary["session_external_backup"] = external_res
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            external_res = {
                "ok": False,
                "pending": True,
                "code": "EXTERNAL_BACKUP_PENDING",
                "error": str(exc),
            }
            summary["session_external_backup"] = external_res
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out = {
        "ok": result.ok and len(still) == 0 and submit_n == 0 and cancel_n == 0,
        "action": f"{am_pm}_recovery_finalize",
        "closed_at": closed_at,
        "orphan_forced_close_count": result.orphan_forced_close_count,
        "orphan_position_ids": result.orphan_position_ids,
        "skipped_already_closed": result.skipped_already_closed,
        "still_open_after": [str(o.get("position_id") or o.get("symbol")) for o in still],
        "active_positions": len(still),
        "accepted_count": len(accepted),
        "normal_exit_count": len(normal_exits),
        "submit_count": submit_n,
        "cancel_count": cancel_n,
        "seal_path": str(session_dir / "session_seal.json"),
        "summary_path": str(summary_path),
        "backup_dir": str(backup_dir),
        "session_archive_backup": archive_res,
        "session_external_backup": external_res,
        "shadow_summary_available": bool(shadow_keys),
    }
    (session_dir / f"{am_pm}_recovery_finalize.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out


def build_daily_summary(day: str = "20260721") -> dict[str, Any]:
    root = NATIVE / "results" / "small_paper" / day
    am = root / "live_session_080044" / "small_paper_summary.json"
    pm = root / "live_session_124342" / "small_paper_summary.json"
    out_dir = NATIVE / "results" / "daily" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    am_s = json.loads(am.read_text(encoding="utf-8")) if am.is_file() else {}
    pm_s = json.loads(pm.read_text(encoding="utf-8")) if pm.is_file() else {}
    daily = {
        "trading_date": day,
        "generated_at": _now(),
        "am_session": "live_session_080044",
        "pm_session": "live_session_124342",
        "am_accepted": am_s.get("accepted_count"),
        "pm_accepted": pm_s.get("accepted_count"),
        "am_recovery": am_s.get("am_recovery"),
        "pm_recovery": pm_s.get("pm_recovery"),
        "am_active_positions": am_s.get("active_positions"),
        "pm_active_positions": pm_s.get("active_positions"),
        "submit_count": int(am_s.get("submit_count") or 0) + int(pm_s.get("submit_count") or 0),
        "cancel_count": int(am_s.get("cancel_count") or 0) + int(pm_s.get("cancel_count") or 0),
        "daily_summary_status": "RECOVERY_COMBINED",
        "note": "Combined from AM+PM recovery-finalized session summaries (Phase675).",
    }
    path = out_dir / f"daily_summary_recovery_{day}.json"
    path.write_text(json.dumps(daily, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # shadow rollup if keys exist
    shadow = {
        "trading_date": day,
        "generated_at": _now(),
        "am_shadow_keys": [k for k in am_s if "shadow" in k.lower()][:40],
        "pm_shadow_keys": [k for k in pm_s if "shadow" in k.lower()][:40],
        "shadow_summary_status": "RECOVERY_AVAILABLE"
        if (am_s or pm_s)
        else "MISSING",
    }
    sp = out_dir / f"shadow_summary_recovery_{day}.json"
    sp.write_text(json.dumps(shadow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"daily": str(path), "shadow": str(sp), "daily_obj": daily, "shadow_obj": shadow}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--am-pm", default="pm", choices=("am", "pm"))
    ap.add_argument("--symbols", default="", help="comma symbols e.g. 6058,5016,5985,3449")
    ap.add_argument("--push-freeze-at", default="")
    ap.add_argument("--skip-archive", action="store_true")
    ap.add_argument("--skip-external", action="store_true")
    ap.add_argument("--also-daily", action="store_true")
    args = ap.parse_args(argv)
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    out = finalize_session(
        Path(args.session_dir),
        reason=args.reason,
        symbols=syms,
        am_pm=args.am_pm,
        push_freeze_at=args.push_freeze_at,
        do_archive=not args.skip_archive,
        do_external=not args.skip_external,
    )
    if args.also_daily:
        out["daily"] = build_daily_summary()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
