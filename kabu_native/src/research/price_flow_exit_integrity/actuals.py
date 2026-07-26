"""Load actual observer_exit trades from small_paper sessions."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from research.pbv2_zero_base_revalidation.util import fnum, parse_ts, yen100
from research.price_flow_exit_integrity.constants import CAPTURE_DAYS, NATIVE, SMALL_PAPER


@dataclass
class ActualTrade:
    day: str
    session: str
    position_id: str
    symbol: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    exit_reason: str
    hold_sec: Optional[float]
    pnl_yen_100: Optional[float]
    entry_imbalance_percentile: Optional[float]
    trail_activate_pct: Optional[float]
    trail_giveback_frac: Optional[float]
    raw: dict[str, Any]


def load_actual_exits(*, native: Path = NATIVE, days: tuple[str, ...] = CAPTURE_DAYS) -> list[ActualTrade]:
    root = native / "results" / "small_paper" if (native / "results" / "small_paper").is_dir() else SMALL_PAPER
    out: list[ActualTrade] = []
    seen: set[str] = set()
    for day in days:
        day_dir = root / day
        if not day_dir.is_dir():
            continue
        for sess in sorted(day_dir.iterdir()):
            ev = sess / "small_paper_events.csv"
            if not ev.is_file() or ev.stat().st_size < 1000:
                continue
            with ev.open(encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh):
                    if str(row.get("event_type") or "") != "observer_exit":
                        continue
                    pid = str(row.get("position_id") or row.get("observer_position_id") or "")
                    if not pid:
                        pid = f"{row.get('symbol')}_{row.get('entry_time')}"
                    if pid in seen:
                        continue
                    seen.add(pid)
                    et = parse_ts(row.get("entry_time"))
                    xt = parse_ts(row.get("exit_time") or row.get("event_time"))
                    ep = fnum(row.get("entry_price"))
                    xp = fnum(row.get("exit_price") or row.get("current_price"))
                    if et is None or ep is None or ep <= 0:
                        continue
                    pnl = fnum(row.get("actual_pnl_yen_100"))
                    if pnl is None and xp is not None:
                        pnl = yen100(ep, xp)
                    out.append(
                        ActualTrade(
                            day=day,
                            session=sess.name,
                            position_id=pid,
                            symbol=str(row.get("symbol") or ""),
                            entry_time=et,
                            exit_time=xt,
                            entry_price=ep,
                            exit_price=xp,
                            exit_reason=str(row.get("exit_reason") or ""),
                            hold_sec=fnum(row.get("hold_sec")),
                            pnl_yen_100=pnl,
                            entry_imbalance_percentile=fnum(row.get("entry_imbalance_percentile")),
                            trail_activate_pct=fnum(row.get("board_dynamic_trailing_activate_pct")),
                            trail_giveback_frac=fnum(row.get("board_dynamic_trailing_giveback_frac")),
                            raw=dict(row),
                        )
                    )
    out.sort(key=lambda t: (t.day, t.entry_time, t.symbol))
    return out
