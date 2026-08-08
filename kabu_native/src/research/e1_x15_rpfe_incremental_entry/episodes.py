"""Build RPFE-conditioned episodes from small_paper candidates."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import DAYS, EPISODE_GAP_SEC

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]


def _norm_sym(s: str) -> str:
    s = str(s or "")
    return s[:-2] if s.endswith(".T") else s


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _session(ts: datetime) -> str:
    return "AM" if (ts.hour * 60 + ts.minute) < 12 * 60 else "PM"


def load_day_candidates(day: str) -> list[dict[str, Any]]:
    root = NATIVE / "results" / "small_paper" / day
    if not root.exists():
        return []
    events = list(root.glob("live_session_*/small_paper_events.csv"))
    if not events:
        return []
    # use largest session file(s) — merge all to avoid missing
    out = []
    seen = set()
    for ev in events:
        with ev.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("event_type") != "candidate":
                    continue
                ts = _parse_ts(row.get("event_time") or row.get("entry_time"))
                if ts is None:
                    continue
                sym = _norm_sym(row.get("symbol") or "")
                if not sym:
                    continue
                key = (sym, ts.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "date": day,
                    "symbol": sym,
                    "session": _session(ts),
                    "event_time": ts,
                    "event_epoch": ts.timestamp(),
                    "current_price": _f(row.get("current_price") or row.get("entry_price")),
                    "gate_accept": str(row.get("gate_accept") or "").lower() in ("true", "1", "yes"),
                })
    out.sort(key=lambda r: (r["symbol"], r["session"], r["event_epoch"]))
    return out


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def build_episodes(days: tuple[str, ...] = DAYS) -> list[dict[str, Any]]:
    """Gap-based episodes: same symbol+session; gap > 300s starts new episode.

    C0 canonical anchor = first candidate in episode.
    Episode search window = [t0, min(session_end, max(t_last, t0+300))].
    """
    episodes = []
    for day in days:
        cands = load_day_candidates(day)
        # group
        groups: dict[tuple[str, str], list] = {}
        for c in cands:
            groups.setdefault((c["symbol"], c["session"]), []).append(c)
        for (sym, sess), rows in groups.items():
            rows = sorted(rows, key=lambda r: r["event_epoch"])
            i = 0
            while i < len(rows):
                start = rows[i]
                members = [start]
                j = i + 1
                while j < len(rows) and rows[j]["event_epoch"] - members[-1]["event_epoch"] <= EPISODE_GAP_SEC:
                    members.append(rows[j])
                    j += 1
                t0 = start["event_epoch"]
                t_last = members[-1]["event_epoch"]
                # session end
                from datetime import datetime as dt
                y, m, d = int(day[:4]), int(day[4:6]), int(day[6:])
                if sess == "AM":
                    sess_end = dt(y, m, d, 11, 30, tzinfo=JST).timestamp()
                else:
                    sess_end = dt(y, m, d, 15, 0, tzinfo=JST).timestamp()
                win_end = min(sess_end, max(t_last, t0 + EPISODE_GAP_SEC))
                ep_id = f"{day}|{sym}|{sess}|{int(t0)}"
                episodes.append({
                    "rpfe_episode_id": ep_id,
                    "date": day,
                    "symbol": sym,
                    "session": sess,
                    "c0_time": start["event_time"].isoformat(),
                    "c0_epoch": t0,
                    "c0_price": start.get("current_price"),
                    "window_start": t0,
                    "window_end": win_end,
                    "n_candidates": len(members),
                    "gate_accept_any": any(m.get("gate_accept") for m in members),
                })
                i = j
    return episodes
