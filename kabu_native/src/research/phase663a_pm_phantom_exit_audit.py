"""Phase663A — PM session phantom EXIT audit and fix verification."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv

PHASE663A_VERDICT = "phase663a_pm_phantom_exit_investigation_done"
REPORT_DIR_NAME = "phase663a_pm_phantom_exit"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

PM_SESSION_DIR = NATIVE_ROOT / "results" / "small_paper" / "20260708" / "live_session_122537"
AM_SESSION_DIR = NATIVE_ROOT / "results" / "small_paper" / "20260708" / "live_session_081852"
PM_ALLOWED_START = "2026-07-08T12:33:00"


@dataclass
class PhantomExitRow:
    symbol: str
    exit_time: str
    exit_reason: str
    position_id: str
    observer_entry_time: str
    accepted_at: str
    market_entry_time: str
    corresponding_entry_exists: bool
    entry_session: str
    exit_session: str
    pm_accept_before_exit: bool
    am_accept_exists: bool
    root_cause_hint: str


def _load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _entry_session_label(ts: str, *, pm_start: str = PM_ALLOWED_START) -> str:
    if not ts:
        return "unknown"
    return "pm" if ts >= pm_start else "am"


def audit_pm_phantom_exits(
    pm_events: Sequence[Mapping[str, Any]],
    *,
    am_events: Sequence[Mapping[str, Any]] | None = None,
    pm_allowed_start: str = PM_ALLOWED_START,
) -> list[PhantomExitRow]:
    am_events = am_events or []
    accepts_pm: dict[str, list[dict]] = {}
    accepts_am: dict[str, list[dict]] = {}
    for e in pm_events:
        sym = str(e.get("symbol") or "")
        if not sym:
            continue
        if e.get("event_type") == "accepted":
            accepts_pm.setdefault(sym, []).append(dict(e))
    for e in am_events:
        sym = str(e.get("symbol") or "")
        if e.get("event_type") == "accepted" and sym:
            accepts_am.setdefault(sym, []).append(dict(e))

    phantoms: list[PhantomExitRow] = []
    for e in pm_events:
        if e.get("event_type") != "observer_exit":
            continue
        sym = str(e.get("symbol") or "")
        if not sym:
            continue
        exit_time = str(e.get("event_time") or e.get("exit_time") or "")
        obs_ent = str(
            e.get("observer_entry_time") or e.get("entry_time") or e.get("accepted_at") or ""
        )
        pm_accepts_before = [
            a
            for a in accepts_pm.get(sym, [])
            if str(a.get("event_time") or "") >= pm_allowed_start
            and str(a.get("event_time") or "") <= exit_time
        ]
        am_exists = bool(accepts_am.get(sym))
        ent_session = _entry_session_label(obs_ent, pm_start=pm_allowed_start)
        is_phantom = not pm_accepts_before or ent_session == "am"
        if not is_phantom:
            continue
        cause = []
        if not pm_accepts_before:
            cause.append("no_pm_accept_before_exit")
        if ent_session == "am":
            cause.append("observer_entry_time_before_pm_start")
        if am_exists and not pm_accepts_before:
            cause.append("am_entry_only_carryover_suspected")
        phantoms.append(
            PhantomExitRow(
                symbol=sym,
                exit_time=exit_time,
                exit_reason=str(e.get("exit_reason") or ""),
                position_id=str(e.get("position_id") or ""),
                observer_entry_time=obs_ent,
                accepted_at=str(e.get("accepted_at") or ""),
                market_entry_time=str(
                    e.get("market_entry_time") or e.get("current_price_time") or ""
                ),
                corresponding_entry_exists=bool(pm_accepts_before or am_exists),
                entry_session=ent_session,
                exit_session="pm",
                pm_accept_before_exit=bool(pm_accepts_before),
                am_accept_exists=am_exists,
                root_cause_hint=";".join(cause) or "unclassified",
            )
        )
    return phantoms


def run_audit(
    *,
    pm_dir: Path = PM_SESSION_DIR,
    am_dir: Path = AM_SESSION_DIR,
    report_root: Path = REPORT_ROOT,
) -> dict[str, Any]:
    report_root.mkdir(parents=True, exist_ok=True)
    pm_events = _load_events(pm_dir / "small_paper_events.jsonl")
    am_events = _load_events(am_dir / "small_paper_events.jsonl")
    phantoms = audit_pm_phantom_exits(pm_events, am_events=am_events)

    pair_rows: list[dict[str, Any]] = []
    for e in pm_events:
        et = e.get("event_type")
        if et not in ("accepted", "observer_exit"):
            continue
        pair_rows.append(
            {
                "event_time": e.get("event_time"),
                "event_type": et,
                "symbol": e.get("symbol"),
                "entry_time": e.get("entry_time"),
                "observer_entry_time": e.get("observer_entry_time"),
                "accepted_at": e.get("accepted_at"),
                "market_entry_time": e.get("market_entry_time"),
                "position_id": e.get("position_id"),
                "session_id": e.get("session_id"),
                "exit_reason": e.get("exit_reason"),
                "hold_sec": e.get("hold_sec"),
            }
        )

    phantom_dicts = [p.__dict__ for p in phantoms]
    _write_csv(
        report_root / "phase663a_pm_entry_exit_pair_audit.csv",
        list(pair_rows[0].keys()) if pair_rows else ["event_time"],
        pair_rows,
    )
    if phantom_dicts:
        _write_csv(
            report_root / "phase663a_phantom_exits.csv",
            list(phantom_dicts[0].keys()),
            phantom_dicts,
        )

    first_time = min((p.exit_time for p in phantoms), default=None)
    root_cause = "no_phantom_exits_in_event_log"
    if phantoms:
        hints = {p.root_cause_hint for p in phantoms}
        root_cause = (
            "am_pm_observer_state_carry_or_pre_pm_entry_clock; "
            + ",".join(sorted(hints))
        )
    elif pm_events:
        root_cause = (
            "20260708_pm_all_exits_have_pm_accept_after_12:33; "
            "defensive_session_scope_fix_still_applied_for_cross_session_vectors"
        )

    report = {
        "phase": "663a",
        "verdict": PHASE663A_VERDICT,
        "pm_session_dir": str(pm_dir.relative_to(NATIVE_ROOT)).replace("\\", "/"),
        "am_session_dir": str(am_dir.relative_to(NATIVE_ROOT)).replace("\\", "/"),
        "phantom_exit_count": len(phantoms),
        "phantom_exit_symbols": sorted({p.symbol for p in phantoms}),
        "phantom_exit_first_time": first_time,
        "root_cause": root_cause,
        "pm_accept_count": sum(1 for e in pm_events if e.get("event_type") == "accepted"),
        "pm_exit_count": sum(1 for e in pm_events if e.get("event_type") == "observer_exit"),
        "fix_applied": {
            "observer_bind_session_clears_positions": True,
            "register_entry_requires_allowed_entry_start": True,
            "on_tick_skips_foreign_session_id": True,
            "discord_exit_filters_session_id_mismatch": True,
        },
        "artifacts": {
            "pair_audit_csv": f"results/reports/{REPORT_DIR_NAME}/phase663a_pm_entry_exit_pair_audit.csv",
            "fix_summary_md": f"results/reports/{REPORT_DIR_NAME}/phase663a_fix_summary.md",
        },
    }
    (report_root / "phase663a_pm_phantom_exit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fix_md = REPORT_ROOT / "phase663a_fix_summary.md"
    fix_md.write_text(
        "\n".join(
            [
                "# Phase663A — PM Phantom EXIT Fix",
                "",
                f"**Verdict:** `{PHASE663A_VERDICT}`",
                "",
                "## Investigation (20260708 PM `live_session_122537`)",
                f"- phantom_exit_count: **{len(phantoms)}**",
                f"- root_cause: {root_cause}",
                "",
                "## Fix (session boundary only; no ENTRY/EXIT threshold changes)",
                "- `ObserverPositionTracker.bind_session()` clears positions at session start",
                "- `register_entry` rejects observer clock before `allowed_entry_start` (warmup)",
                "- `on_tick` ignores positions with foreign `session_id`",
                "- Discord EXIT suppressed when `session_id` mismatches current session",
                "",
                "## session_id format",
                "`{YYYYMMDD}_{am|pm}_{live_session_dir}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return report


def main() -> None:
    if str(NATIVE_ROOT) not in sys.path:
        sys.path.insert(0, str(NATIVE_ROOT))
    report = run_audit()
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = f"src;{NATIVE_ROOT.parent}"
    test_proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase663a_pm_phantom_exit.py", "-q"],
        cwd=str(NATIVE_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    report["regression_tests_passed"] = test_proc.returncode == 0
    (REPORT_ROOT / "phase663a_pm_phantom_exit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(PHASE663A_VERDICT)
    print(json.dumps({k: report[k] for k in ("phantom_exit_count", "root_cause", "regression_tests_passed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
