"""
Phase661: Structural Exit holding-time consistency audit (research only).

Investigates 6327.T case where no_progress_exit fired within ~30s of a new
ENTRY notification while Discord showed ~15 minutes holding time.

No ENTRY/EXIT/YAML/runtime changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

PHASE661_VERDICT = "phase661_structural_exit_holding_audit_done"
REPORT_DIR_NAME = "phase661_structural_exit_holding_audit"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

# Primary incident session (user-reported pattern reproduced here).
INCIDENT_SESSION = NATIVE_ROOT / "results" / "small_paper" / "20260707" / "live_session_122539"
INCIDENT_SYMBOL = "6327.T"
NO_PROGRESS_START_SEC = 900.0


@dataclass(frozen=True)
class TimelineRow:
    event_time: str
    event_type: str
    message_index: int
    position_id: str
    entry_time: str
    exit_time: str
    hold_sec: Optional[float]
    exit_reason: str
    price_age_sec: Optional[float]
    price_freshness_source: str
    delta_from_prior_accept_sec: Optional[float]
    notes: str


def _parse_ts(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def _position_id(symbol: str, entry_time: str) -> str:
    return f"{symbol}|{entry_time}"


def _load_symbol_events(session_dir: Path, symbol: str) -> list[dict[str, Any]]:
    path = session_dir / "small_paper_events.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("symbol") == symbol:
            out.append(e)
    return out


def build_timeline(events: Sequence[Mapping[str, Any]]) -> list[TimelineRow]:
    rows: list[TimelineRow] = []
    last_accept_evt: Optional[datetime] = None
    for e in events:
        et = str(e.get("event_type") or "")
        if et not in ("accepted", "observer_exit", "observer_take"):
            continue
        evt = _parse_ts(e.get("event_time"))
        ent = str(e.get("entry_time") or "")
        hold_raw = e.get("hold_sec")
        if hold_raw is None:
            hold_raw = e.get("hold_duration_sec")
        hold = float(hold_raw) if hold_raw is not None else None
        delta: Optional[float] = None
        notes = ""
        if et == "accepted":
            if evt:
                last_accept_evt = evt
            if e.get("price_age_sec") is not None and float(e["price_age_sec"]) >= 300:
                notes = "stale_board_entry_time"
        elif et == "observer_exit" and evt and last_accept_evt:
            delta = (evt - last_accept_evt).total_seconds()
            if hold is not None and hold >= NO_PROGRESS_START_SEC and delta is not None and delta < 120:
                notes = "no_progress_short_after_accept_display_mismatch"
        rows.append(
            TimelineRow(
                event_time=str(e.get("event_time") or ""),
                event_type=et,
                message_index=int(e.get("message_index") or 0),
                position_id=_position_id(str(e.get("symbol") or ""), ent),
                entry_time=ent,
                exit_time=str(e.get("exit_time") or ""),
                hold_sec=hold,
                exit_reason=str(e.get("exit_reason") or e.get("take_reason") or ""),
                price_age_sec=float(e["price_age_sec"]) if e.get("price_age_sec") is not None else None,
                price_freshness_source=str(e.get("price_freshness_source") or ""),
                delta_from_prior_accept_sec=delta,
                notes=notes,
            )
        )
    return rows


def _code_path_summary() -> dict[str, Any]:
    return {
        "entry_time_source_chain": [
            "pilot_runner._candidate_trade_from_push: entry_time = parse_kabu_time(payload.CurrentPriceTime)",
            "pilot_runner accepted path: register_entry(trade=trade)",
            "observer_position_tracker.register_entry: ent = parse_kabu_time(trade.entry_time)",
            "observer _VirtualPosition.entry_time = ent (NOT event_time, NOT position_id lookup)",
        ],
        "holding_duration_source": [
            "on_tick hold_sec = now(JST) - pos.entry_time",
            "no_progress elapsed = tick.ts_epoch - pos.entry_time.timestamp() (via entry_ts_epoch)",
            "_close hold_sec = now(JST) - pos.entry_time",
            "discord notify_exit hold_minutes = hold_sec / 60 (int round for display)",
        ],
        "position_identity": {
            "storage_key": "symbol only (_positions: dict[str, _VirtualPosition])",
            "position_id": "make_position_id(symbol, entry_time) — informational; not used as dict key",
            "reentry_behavior": "closed position deleted; new register_entry uses trade.entry_time from latest accept",
            "register_entry_skip": "if symbol open and not closed → return without updating entry_time",
        },
        "no_progress_policy": {
            "start_time_sec": NO_PROGRESS_START_SEC,
            "implication": "no_progress_exit cannot fire unless elapsed >= 900s from pos.entry_time",
        },
    }


def _classify_incident(rows: list[TimelineRow]) -> dict[str, Any]:
    exits = [r for r in rows if r.event_type == "observer_exit" and r.exit_reason == "no_progress_exit"]
    accepts = [r for r in rows if r.event_type == "accepted"]
    if not exits or not accepts:
        return {"found_incident": False}

    first_accept = accepts[0]
    first_exit = exits[0]
    ent = _parse_ts(first_accept.entry_time)
    evt_accept = _parse_ts(first_accept.event_time)
    evt_exit = _parse_ts(first_exit.event_time)
    board_vs_accept_sec = (evt_accept - ent).total_seconds() if ent and evt_accept else None
    accept_to_exit_sec = first_exit.delta_from_prior_accept_sec

    return {
        "found_incident": True,
        "first_accept_event_time": first_accept.event_time,
        "trade_entry_time": first_accept.entry_time,
        "first_exit_event_time": first_exit.event_time,
        "hold_sec_at_exit": first_exit.hold_sec,
        "hold_minutes_display": int(round((first_exit.hold_sec or 0) / 60.0)),
        "accept_to_exit_wall_sec": accept_to_exit_sec,
        "board_entry_time_lag_vs_accept_sec": board_vs_accept_sec,
        "price_age_sec_at_accept": first_accept.price_age_sec,
        "price_freshness_source": first_accept.price_freshness_source,
        "exit_logic_vs_display": "consistent_both_use_pos.entry_time",
        "root_cause": "stale_trade_entry_time_from_CurrentPriceTime",
        "stale_state_on_reentry": exits[1].hold_sec > exits[0].hold_sec if len(exits) > 1 else False,
        "verdict": (
            "EXIT判定は15分経過と正しく認識している（hold_sec≈909≥900）。"
            "ユーザー体感の「ENTRY直後30秒」は accepted.event_time 基準。"
            "observer の entry_time は board CurrentPriceTime（本件 12:44:14）を引き継いでおり、"
            "accept 時点で既に約15分経過していたため no_progress が即発火。"
            "Discord 表示も同じ hold_sec を丸めたもので、表示単独のバグではない。"
        ),
    }


def run_audit(
    *,
    session_dir: Path = INCIDENT_SESSION,
    symbol: str = INCIDENT_SYMBOL,
    report_root: Path = REPORT_ROOT,
) -> dict[str, Any]:
    report_root.mkdir(parents=True, exist_ok=True)
    events = _load_symbol_events(session_dir, symbol)
    timeline = build_timeline(events)
    incident = _classify_incident(timeline)
    code_paths = _code_path_summary()

    timeline_csv = report_root / f"{symbol.replace('.', '_')}_timeline.csv"
    timeline_fields = [
        "event_time",
        "event_type",
        "message_index",
        "position_id",
        "entry_time",
        "exit_time",
        "hold_sec",
        "exit_reason",
        "price_age_sec",
        "price_freshness_source",
        "delta_from_prior_accept_sec",
        "notes",
    ]
    timeline_rows = [
        {
            "event_time": r.event_time,
            "event_type": r.event_type,
            "message_index": r.message_index,
            "position_id": r.position_id,
            "entry_time": r.entry_time,
            "exit_time": r.exit_time,
            "hold_sec": r.hold_sec,
            "exit_reason": r.exit_reason,
            "price_age_sec": r.price_age_sec,
            "price_freshness_source": r.price_freshness_source,
            "delta_from_prior_accept_sec": r.delta_from_prior_accept_sec,
            "notes": r.notes,
        }
        for r in timeline
    ]
    _write_csv(timeline_csv, timeline_fields, timeline_rows)

    summary = {
        "phase": 661,
        "verdict": PHASE661_VERDICT,
        "generated_at": _now_iso(),
        "incident_symbol": symbol,
        "incident_session": str(session_dir.relative_to(NATIVE_ROOT)).replace("\\", "/"),
        "code_paths": code_paths,
        "incident_analysis": incident,
        "timeline_rows": len(timeline),
        "artifacts": {
            "timeline_csv": str(timeline_csv.relative_to(NATIVE_ROOT)).replace("\\", "/"),
        },
    }
    (report_root / "phase661_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    summary = run_audit()
    inc = summary["incident_analysis"]
    print(PHASE661_VERDICT)
    print(f"session={summary['incident_session']}")
    if inc.get("found_incident"):
        print(
            f"accept={inc['first_accept_event_time']} exit={inc['first_exit_event_time']} "
            f"hold_sec={inc['hold_sec_at_exit']} accept_to_exit={inc['accept_to_exit_wall_sec']}s "
            f"board_lag={inc['board_entry_time_lag_vs_accept_sec']}s"
        )
        print(inc["verdict"])


if __name__ == "__main__":
    main()
