#!/usr/bin/env python3
"""
Phase283: Replay today's live_session timeline at 12x (1 min = 5 sec) and send Discord
notifications to KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL (notification validation only).

Output: kabu_native/results/reports/phase283_fast_realtime_replay_discord_flow.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
_REPO = Path(__file__).resolve().parents[2]
_OUT = _REPO / "kabu_native/results/reports/phase283_fast_realtime_replay_discord_flow.json"
_NOTIFY_ENV = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
_LEGACY_ENV = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
_REAL_SEC_PER_REPLAY_SEC = 12.0  # 60 wall-clock sec -> 5 replay sec


def _bootstrap() -> None:
    native = _REPO / "kabu_native"
    for p in (native / "src", _REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_env() -> None:
    try:
        from api.rest_client import load_kabu_env

        load_kabu_env(repo_root=_REPO)
    except Exception:
        env = _REPO / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _parse_ts(s: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(JST)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _day_key_from_date(d: Optional[datetime] = None) -> str:
    dt = d or datetime.now(JST)
    return dt.strftime("%Y%m%d")


def _discover_sessions(day_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    for d in sorted(day_dir.glob("live_session_*")):
        cfg_path = d / "live_session_config.json"
        if cfg_path.is_file():
            out.append((d, json.loads(cfg_path.read_text(encoding="utf-8"))))
    order = {"am": 0, "pm": 1}

    def _sort_key(item: tuple[Path, dict[str, Any]]) -> tuple[int, str]:
        _d, cfg = item
        kind = str((cfg.get("am_pm_session") or {}).get("kind") or "am").lower()
        return (order.get(kind, 2), str(cfg.get("generated_at") or ""))

    return sorted(out, key=_sort_key)


def _load_universe_symbols(csv_path: Path) -> list[str]:
    if not csv_path.is_file():
        return []
    syms: list[str] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            if sym:
                syms.append(sym if sym.endswith(".T") else f"{sym}.T")
    return syms


def _screening_ts(day: str, window: str, *, fallback_hhmm: str) -> datetime:
    """Use screening window end (e.g. 09:00-09:03 -> 09:03)."""
    d = datetime.strptime(day, "%Y%m%d").replace(tzinfo=JST)
    end_hhmm = fallback_hhmm
    if window and "-" in window:
        end_hhmm = window.split("-", 1)[1].strip()
    hh, mm = end_hhmm.split(":")
    return d.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)


@dataclass(order=True)
class TimelineItem:
    sort_key: datetime
    kind: str
    session_id: str = field(compare=False)
    payload: dict[str, Any] = field(default_factory=dict, compare=False)


def _iter_jsonl_events(path: Path, *, kinds: set[str]) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("event_type") or "") in kinds:
                yield row


def _iter_refresh_completed(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("error_type") == "intraday_refresh" and row.get("event") == "completed":
            yield row


def _v2_from_row(row: Mapping[str, Any]) -> int:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    raw = row.get("entry_expectancy_score_v2")
    if raw not in (None, ""):
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            pass
    sf = compute_entry_expectancy_score_fields(trade=row)
    try:
        return int(sf.get("entry_expectancy_score_v2") or 0)
    except (TypeError, ValueError):
        return 0


def build_timeline(
    sessions: list[tuple[Path, dict[str, Any]]],
    *,
    day_key: str,
) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    day_iso = f"{day_key[:4]}-{day_key[4:6]}-{day_key[6:8]}"

    for session_dir, cfg in sessions:
        sid = session_dir.name
        am_pm = cfg.get("am_pm_session") or {}
        kind = str(am_pm.get("kind") or "am").lower()
        uni_path = Path(str(cfg.get("universe_csv_path") or ""))
        watch = _load_universe_symbols(uni_path)
        screening_window = str(am_pm.get("screening_window") or "")
        fallback = "09:03" if kind == "am" else "12:32"
        screen_ts = _screening_ts(day_key, screening_window, fallback_hhmm=fallback)
        items.append(
            TimelineItem(
                sort_key=screen_ts,
                kind="screening_universe",
                session_id=sid,
                payload={
                    "session_label": "AM" if kind == "am" else "PM",
                    "refresh_time": screening_window or fallback,
                    "watch_symbols": watch,
                    "title_note": "朝スクリーニング" if kind == "am" else "後場前スクリーニング",
                    "universe_csv": str(uni_path),
                },
            )
        )

        for ref in _iter_refresh_completed(session_dir / "errors.jsonl"):
            ts = _parse_ts(str(ref.get("event_time") or ""))
            sk = str(ref.get("session_kind") or kind).lower()
            label = "PM" if sk == "pm" else "AM"
            rt = str(ref.get("refresh_time") or ("14:30" if sk == "pm" else "10:00"))
            watch_syms = list(ref.get("after_symbols") or [])
            if not watch_syms and watch:
                watch_syms = watch
            items.append(
                TimelineItem(
                    sort_key=ts,
                    kind="intraday_refresh",
                    session_id=sid,
                    payload={
                        "session_label": label,
                        "refresh_time": rt,
                        "added_symbols": list(ref.get("added_symbols") or []),
                        "removed_symbols": list(ref.get("removed_symbols") or []),
                        "watch_symbols": watch_syms,
                    },
                )
            )

        kinds = {"accepted", "rejected", "observer_exit"}
        jsonl = session_dir / "small_paper_events.jsonl"
        deferred_cap = 40
        deferred_n = 0
        for row in _iter_jsonl_events(jsonl, kinds=kinds):
            et = str(row.get("event_type") or "")
            ts = _parse_ts(str(row.get("event_time") or ""))
            if et == "accepted":
                items.append(
                    TimelineItem(sort_key=ts, kind="entry", session_id=sid, payload=dict(row))
                )
            elif et == "rejected":
                if str(row.get("gate_reject_reason") or "") != "max_concurrent":
                    continue
                if deferred_n >= deferred_cap:
                    continue
                v2 = _v2_from_row(row)
                try:
                    quality = float(row.get("continuation_quality_score") or 0)
                except (TypeError, ValueError):
                    quality = 0.0
                if v2 < 5 and quality < 0.55:
                    continue
                payload = dict(row)
                if v2 < 5:
                    payload["entry_expectancy_score_v2"] = 5
                items.append(
                    TimelineItem(
                        sort_key=ts,
                        kind="entry_deferred",
                        session_id=sid,
                        payload=payload,
                    )
                )
                deferred_n += 1
            elif et == "observer_exit":
                items.append(
                    TimelineItem(
                        sort_key=ts,
                        kind="exit",
                        session_id=sid,
                        payload=dict(row),
                    )
                )

    end_ts = _parse_ts(f"{day_iso}T15:35:00+09:00")
    items.append(
        TimelineItem(
            sort_key=end_ts,
            kind="daily_summary",
            session_id="day_end",
            payload={},
        )
    )
    items.sort(key=lambda x: x.sort_key)
    return items


@dataclass
class NotifyReplayState:
    open_slot_count: int = 0
    max_slots: int = 3
    holdings: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    reject_rows: list[dict[str, Any]] = field(default_factory=list)
    accepted_rows: list[dict[str, Any]] = field(default_factory=list)


def _install_audit() -> dict[str, Any]:
    from collections import Counter

    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    audit: dict[str, Any] = {
        "timeline_log": [],
        "counts_sent": Counter(),
        "counts_blocked": Counter(),
        "duplicate_keys": [],
        "seen_dedupe": set(),
    }
    orig = SmallPaperDiscordNotifier._post

    def _wrap(self, **kwargs: Any) -> bool:
        tag = str(kwargs.get("event_tag") or "")
        dedupe = kwargs.get("dedupe_key")
        trade = bool(kwargs.get("trade_notify"))
        ok = orig(self, **kwargs)
        entry = {
            "at": datetime.now(JST).isoformat(timespec="seconds"),
            "event_tag": tag,
            "title_line": kwargs.get("title_line"),
            "sent": ok,
            "blocked": not ok and dedupe is not None,
            "dedupe_key": dedupe,
            "trade_notify": trade,
            "webhook_source": self.trade_webhook_source() if trade else "legacy",
        }
        audit["timeline_log"].append(entry)
        if ok:
            audit["counts_sent"][tag] += 1
        elif dedupe:
            audit["counts_blocked"][tag] += 1
        if dedupe:
            if dedupe in audit["seen_dedupe"]:
                audit["duplicate_keys"].append(dedupe)
            audit["seen_dedupe"].add(dedupe)
        return ok

    SmallPaperDiscordNotifier._post = _wrap  # type: ignore[method-assign]
    audit["_restore"] = orig
    return audit


def _restore_audit(audit: dict[str, Any]) -> None:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    SmallPaperDiscordNotifier._post = audit["_restore"]  # type: ignore[assignment]


def _holdings_snapshot(state: NotifyReplayState) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for h in state.holdings:
        sym = str(h.get("symbol") or "")
        code = sym.replace(".T", "")
        out.append(
            {
                "symbol": sym,
                "symbol_short": code,
                "unrealized_pnl_pct": h.get("unrealized_pnl_pct", 0.0),
                "entry_score_v2": h.get("entry_score_v2", 5),
                "hold_minutes": h.get("hold_minutes", 0),
            }
        )
    return out


def _tag_audit(audit: dict[str, Any], item: TimelineItem, *, extra: Optional[dict] = None) -> None:
    if not audit.get("timeline_log"):
        return
    audit["timeline_log"][-1]["logical_kind"] = item.kind
    audit["timeline_log"][-1]["replay_ts"] = item.sort_key.isoformat()
    if extra:
        audit["timeline_log"][-1].update(extra)


def _replay_item(
    item: TimelineItem,
    *,
    notifier: Any,
    ux: Any,
    state: NotifyReplayState,
    audit: dict[str, Any],
) -> None:
    p = item.payload
    if item.kind == "screening_universe":
        label = str(p.get("session_label") or "AM")
        rt = str(p.get("refresh_time") or "screening")
        note = str(p.get("title_note") or "")
        notifier.notify_universe_refresh(
            session_label=label,
            refresh_time=rt,
            added_symbols=[],
            removed_symbols=[],
            watch_symbols=list(p.get("watch_symbols") or []),
            status="completed",
        )
        _tag_audit(audit, item, extra={"note": note, "session_label": label})
        return

    if item.kind == "intraday_refresh":
        notifier.notify_universe_refresh(
            session_label=str(p.get("session_label") or "AM"),
            refresh_time=str(p.get("refresh_time") or "10:00"),
            added_symbols=list(p.get("added_symbols") or []),
            removed_symbols=list(p.get("removed_symbols") or []),
            watch_symbols=list(p.get("watch_symbols") or []),
            status="completed",
        )
        _tag_audit(
            audit,
            item,
            extra={
                "session_label": p.get("session_label"),
                "refresh_time": p.get("refresh_time"),
            },
        )
        return

    if item.kind == "entry":
        row = p
        merged = {**row, **dict(row)}
        slots_after = int(row.get("open_slots_after") or state.open_slot_count + 1)
        state.open_slot_count = min(state.max_slots, slots_after)
        ok = notifier.notify_entry(
            event=merged,
            payload={"CurrentPrice": row.get("current_price"), **merged},
            open_slots=state.open_slot_count,
            session_bucket=str(row.get("session_bucket") or "morning"),
            score5_candidate_ordinal=ux.record_score5_candidate(),
            ux_stats=ux,
        )
        if ok:
            state.accepted_rows.append(row)
            state.holdings.append(
                {
                    "symbol": row.get("symbol"),
                    "entry_score_v2": _v2_from_row(row),
                    "unrealized_pnl_pct": 0.0,
                    "hold_minutes": 0,
                }
            )
            state.events.append(row)
        _tag_audit(audit, item, extra={"symbol": row.get("symbol")})
        return

    if item.kind == "entry_deferred":
        row = p
        merged = {**row}
        notifier.notify_entry_deferred_max_concurrent(
            event=merged,
            payload={"CurrentPrice": row.get("current_price"), **merged},
            trade_data=merged,
            open_slots=state.max_slots,
            open_positions=_holdings_snapshot(state),
            score5_candidate_ordinal=ux.record_score5_candidate(),
            ux_stats=ux,
        )
        state.reject_rows.append(row)
        _tag_audit(audit, item, extra={"symbol": row.get("symbol")})
        return

    if item.kind == "exit":
        row = p
        sym = str(row.get("symbol") or "")
        try:
            pnl = float(row.get("pnl_pct") or row.get("realized_pnl_pct") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        ctx = {
            "symbol": sym,
            "is_structural_exit": True,
            "exit_reason": str(row.get("exit_reason") or "observer_exit"),
            "current_price": row.get("current_price") or row.get("exit_price"),
            "entry_price": row.get("entry_price"),
            "realized_pnl_pct": pnl,
            "mfe_pct": row.get("mfe_pct") or row.get("peak_mfe_pct"),
            "mae_pct": row.get("mae_pct"),
            "hold_sec": row.get("hold_sec") or row.get("hold_duration_sec"),
            "exit_time": row.get("event_time"),
        }
        notifier.notify_exit(context=ctx)
        state.holdings = [h for h in state.holdings if str(h.get("symbol")) != sym]
        state.open_slot_count = max(0, state.open_slot_count - 1)
        state.events.append(row)
        _tag_audit(audit, item, extra={"symbol": sym})
        return

    if item.kind == "daily_summary":
        from small_paper.discord_notifier import notify_discord_session_end

        summary = {
            "peak_open_slots": state.open_slot_count,
            "observer_entry_count": len(state.accepted_rows),
            "observer_exit_count": sum(
                1 for e in state.events if e.get("event_type") == "observer_exit"
            ),
            "accepted_count": len(state.accepted_rows),
        }
        notify_discord_session_end(
            notifier,
            events=state.events,
            summary=summary,
            monitored_symbol_count=40,
            reject_rows=state.reject_rows,
            ux_stats=ux,
        )
        _tag_audit(audit, item)


def _verify_sequence(audit: dict[str, Any]) -> dict[str, Any]:
    log = audit.get("timeline_log") or []
    sent_tags = [e for e in log if e.get("sent")]

    def _first_idx(pred: Callable[[dict], bool]) -> Optional[int]:
        for i, e in enumerate(sent_tags):
            if pred(e):
                return i
        return None

    checks = {
        "uses_notify_webhook": all(
            e.get("webhook_source") == "notify" for e in sent_tags if e.get("trade_notify")
        ),
        "has_am_screening": any(
            e.get("logical_kind") == "screening_universe"
            and str(e.get("session_label") or "") == "AM"
            for e in sent_tags
        ),
        "has_pm_screening": any(
            e.get("logical_kind") == "screening_universe"
            and str(e.get("session_label") or "") == "PM"
            for e in sent_tags
        ),
        "has_1000_refresh": any(
            e.get("logical_kind") == "intraday_refresh"
            and str(e.get("refresh_time") or "") == "10:00"
            for e in sent_tags
        ),
        "has_1430_refresh": any(
            e.get("logical_kind") == "intraday_refresh"
            and str(e.get("refresh_time") or "") == "14:30"
            for e in sent_tags
        ),
        "has_entry": audit["counts_sent"].get("ENTRY", 0) > 0,
        "has_entry_deferred": audit["counts_sent"].get("ENTRY見送り", 0) > 0,
        "entry_deferred_cap_note": "max 40 deferred events replayed (rate-limit guard)",
        "has_exit": audit["counts_sent"].get("EXIT", 0) > 0,
        "has_daily_summary": audit["counts_sent"].get("Daily Summary", 0) >= 1,
        "daily_summary_last": (
            sent_tags[-1].get("event_tag") == "Daily Summary" if sent_tags else False
        ),
        "deferred_cooldown_observed": audit["counts_blocked"].get("ENTRY見送り", 0) > 0,
    }

    idx_entry = _first_idx(lambda e: e.get("event_tag") == "ENTRY")
    idx_exit = _first_idx(lambda e: e.get("event_tag") == "EXIT")
    idx_summary = _first_idx(lambda e: e.get("event_tag") == "Daily Summary")
    checks["entry_before_summary"] = (
        idx_entry is not None and idx_summary is not None and idx_entry < idx_summary
    )
    checks["exit_before_summary"] = (
        idx_exit is not None and idx_summary is not None and idx_exit < idx_summary
    )

    phase284: list[str] = []
    if not checks["deferred_cooldown_observed"]:
        phase284.append(
            "ENTRY見送り: 同一銘柄の2回目以降がクールダウンで抑止されること（replayでは先頭40件で検証）"
        )
    if not checks["has_1000_refresh"]:
        phase284.append("10:00 refresh: AMセッション errors.jsonl に completed があるか確認")
    if audit["counts_sent"].get("ENTRY見送り", 0) > 15:
        phase284.append("ENTRY見送り通知量: 日次上限・集約表示の検討")

    return {"checks": checks, "phase284_candidates": phase284, "sent_sequence": sent_tags}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase283 fast realtime Discord replay")
    parser.add_argument("--day-key", default=None, help="YYYYMMDD (default: today JST)")
    parser.add_argument("--day-dir", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--no-sleep", action="store_true", help="Skip 12x sleeps (debug)")
    parser.add_argument("--max-items", type=int, default=None, help="Cap timeline items")
    parser.add_argument(
        "--post-interval-sec",
        type=float,
        default=1.0,
        help="Min seconds between Discord POSTs (rate-limit guard)",
    )
    args = parser.parse_args()

    _bootstrap()
    _load_env()

    from dataclasses import replace

    from small_paper.config import load_pilot_config
    from small_paper.discord_notifier import SmallPaperDiscordNotifier, discord_config_from_pilot
    from small_paper.discord_ux_session import DiscordUxSessionStats

    day_key = args.day_key or _day_key_from_date()
    day_dir = args.day_dir or (_REPO / "kabu_native/results/small_paper" / day_key)
    if not day_dir.is_dir():
        print(f"day_dir missing: {day_dir}", file=sys.stderr)
        return 2

    sessions = _discover_sessions(day_dir)
    if not sessions:
        print("no live_session_* with config", file=sys.stderr)
        return 2

    timeline = build_timeline(sessions, day_key=day_key)
    if args.max_items:
        timeline = timeline[: int(args.max_items)]

    cfg_path = args.config or (_REPO / "kabu_native/configs/small_paper_pilot_q070_cap3.yaml")
    cfg = load_pilot_config(cfg_path if cfg_path.is_absolute() else _REPO / cfg_path)
    cfg = replace(
        cfg,
        discord_enabled=True,
        discord_observer_only=True,
        discord_send_entry_deferred_max_concurrent=True,
        discord_send_universe_refresh=True,
        discord_send_daily_summary=True,
    )
    dcfg = replace(discord_config_from_pilot(cfg), enabled=True)
    notifier = SmallPaperDiscordNotifier(
        dcfg,
        profile=cfg.profile,
        entry_profile=cfg.entry_profile,
        policy_label=str(cfg.policy_label),
    )
    ux = DiscordUxSessionStats()
    state = NotifyReplayState(max_slots=int(cfg.max_concurrent_positions))
    audit = _install_audit()

    t0 = time.monotonic()
    prev_ts: Optional[datetime] = None
    last_post_mono = 0.0
    post_gap = max(0.0, float(args.post_interval_sec))
    for item in timeline:
        if prev_ts is not None and not args.no_sleep:
            gap = (item.sort_key - prev_ts).total_seconds()
            if gap > 0:
                time.sleep(gap / _REAL_SEC_PER_REPLAY_SEC)
        _replay_item(item, notifier=notifier, ux=ux, state=state, audit=audit)
        if post_gap > 0:
            now_m = time.monotonic()
            wait = post_gap - (now_m - last_post_mono) if last_post_mono else 0.0
            if wait > 0:
                time.sleep(wait)
            last_post_mono = time.monotonic()
        prev_ts = item.sort_key

    _restore_audit(audit)
    runtime_sec = round(time.monotonic() - t0, 2)

    review = _verify_sequence(audit)
    checks = review["checks"]
    verdict = (
        "validation_ok"
        if all(
            checks.get(k)
            for k in (
                "uses_notify_webhook",
                "has_am_screening",
                "has_pm_screening",
                "has_1000_refresh",
                "has_1430_refresh",
                "has_entry",
                "has_exit",
                "has_daily_summary",
                "daily_summary_last",
            )
        )
        else "needs_attention"
    )

    report = {
        "phase": 283,
        "title": "Fast realtime live_session Discord flow replay (12x)",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "replay_speed": "1 min = 5 sec (12x)",
        "day_key": day_key,
        "day_dir": str(day_dir),
        "sessions_used": [
            {
                "id": d.name,
                "kind": (c.get("am_pm_session") or {}).get("kind"),
                "generated_at": c.get("generated_at"),
                "universe_csv": c.get("universe_csv_path"),
            }
            for d, c in sessions
        ],
        "timeline_item_count": len(timeline),
        "runtime_sec": runtime_sec,
        "post_interval_sec": post_gap,
        "no_sleep": bool(args.no_sleep),
        "discord_env": {
            _NOTIFY_ENV: bool(os.getenv(_NOTIFY_ENV, "").strip()),
            _LEGACY_ENV: bool(os.getenv(_LEGACY_ENV, "").strip()),
        },
        "notification_counts_sent": dict(audit["counts_sent"]),
        "notification_counts_blocked": dict(audit["counts_blocked"]),
        "sequence_review": review,
        "constraints": {
            "trading_logic_changed": False,
            "notification_validation_only": True,
        },
    }

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {_OUT}")
    print(f"verdict={verdict} runtime_sec={runtime_sec}")
    print(f"sent={dict(audit['counts_sent'])} blocked={dict(audit['counts_blocked'])}")
    return 0 if verdict == "validation_ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
