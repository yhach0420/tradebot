"""M3 breadth episodes on the already-exposed calendar. No forward returns."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1 import PROSPECTIVE_FROM
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, min_to_hhmm
from research.causal_driver_pb1.sector_state_alpha_shadow.driver import DriverObservation, panel_driver
from research.causal_driver_pb1.sector_state_alpha_shadow.machine import start_session, step
from research.causal_driver_pb1.sector_state_alpha_shadow.parity import exposed_dates
from research.causal_driver_pb1.sector_state_transmission.panel import load_prices


def calendar() -> list[str]:
    raw = exposed_dates()
    dates = sorted(set(raw["discovery"]) | set(raw["fv"]))
    if any(d >= PROSPECTIVE_FROM for d in dates):
        raise RuntimeError("prospective_date_in_calendar")
    return dates


def build_alpha(symbols: list[str]) -> dict[str, Any]:
    raw = exposed_dates()
    clocks: dict[str, dict[str, dict[str, Any]]] = {}
    episodes: list[dict[str, str]] = []
    prospective_rows_read = 0
    for stage, key in (("DISCOVERY", "discovery"), ("FV", "fv")):
        dates = list(raw[key])
        if not dates:
            raise RuntimeError(f"missing_{key}_dates")
        panel = load_prices(symbols=symbols, dates=dates, stage=stage)
        if any(str(d) >= PROSPECTIVE_FROM for d in panel["dates"]):
            raise RuntimeError("prospective_row_materialized")
        shadow = panel_driver(px=panel["px"], age=panel["age"], listed=panel["listed"])
        for di, day in enumerate(dates):
            session = start_session(day)
            day_clocks: dict[str, dict[str, Any]] = {}
            for ci, minute in enumerate(CLOCK_MINS):
                hhmm = min_to_hhmm(int(minute))
                available = bool(shadow["ok"][di, ci])
                value = float(shadow["breadth"][di, ci]) if available else None
                obs = DriverObservation(
                    available,
                    value,
                    int(shadow["n_valid"][di, ci]),
                    int(shadow["n_up"][di, ci]),
                    int(shadow["n_down"][di, ci]),
                    available,
                    int(shadow["n_listed"][di]),
                    24,
                )
                session, events = step(session, parent_id="M3", hhmm=hhmm, driver=obs)
                for event in events:
                    if event["event_type"] == "ALPHA_ON":
                        episodes.append({
                            "date": day,
                            "episode_id": event["episode_id"],
                            "start": event["episode_start"],
                        })
                day_clocks[hhmm] = {
                    "available": available,
                    "value": value,
                    "state": session.state,
                    "episode_id": session.episode_id or "",
                    "episode_start": session.episode_start or "",
                }
            clocks[day] = day_clocks
        print(f"ALPHA_TIMELINE {stage} days={len(dates)} episodes_so_far={len(episodes)}", flush=True)
    return {
        "clocks": clocks,
        "episodes": episodes,
        "episode_n": len(episodes),
        "prospective_rows_read": prospective_rows_read,
    }
