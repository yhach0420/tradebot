"""Parent M3 breadth parity on already-exposed dates. No forward returns."""
from __future__ import annotations

import json
from typing import Any

import numpy as np

from research.causal_driver_pb1 import FV_FIRST, FV_LAST, PROSPECTIVE_FROM
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, min_to_hhmm
from research.causal_driver_pb1.sector_breadth_discovery.features import constituent_ret_clock, driver_from_ret
from research.causal_driver_pb1.sector_state_alpha_shadow import Q60_OFF, Q80_ON
from research.causal_driver_pb1.sector_state_alpha_shadow.driver import DriverObservation, panel_driver
from research.causal_driver_pb1.sector_state_alpha_shadow.machine import start_session, step, target_signals
from research.causal_driver_pb1.sector_state_transmission.panel import load_prices
from research.causal_driver_pb1.sector_state_transmission_precommit.isolation import DISC_V2_OUT
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.isolation import OUT as PRECOMMIT_V11_OUT

ATOL = 1e-12


def exposed_dates() -> dict[str, list[str]]:
    v2 = json.loads((DISC_V2_OUT / "report.json").read_text(encoding="utf-8"))
    discovery = [str(d) for d in (((v2.get("evaluation") or {}).get("bound") or {}).get("eligible_dates") or [])]
    pre = json.loads((PRECOMMIT_V11_OUT / "report.json").read_text(encoding="utf-8"))
    folds = ((pre.get("evaluation") or {}).get("fv_folds") or {})
    fv = [str(d) for d in list(folds.get("FV_EARLY") or []) + list(folds.get("FV_MIDDLE") or []) + list(folds.get("FV_LATE") or [])]
    if any(d >= PROSPECTIVE_FROM for d in discovery + fv):
        raise RuntimeError("prospective_date_in_replay_calendar")
    if any(d > FV_LAST for d in discovery + fv):
        raise RuntimeError("replay_date_after_exposed_window")
    if any(d >= FV_FIRST for d in discovery):
        raise RuntimeError("discovery_calendar_enters_fv")
    return {"discovery": discovery, "fv": fv}


def _parent_reference(*, px: np.ndarray, age: np.ndarray, listed: np.ndarray) -> dict[str, np.ndarray]:
    ret = constituent_ret_clock(px=px, age=age, w=1)
    mem = np.ones(px.shape[0], dtype=np.bool_)
    st = driver_from_ret(ret=ret, mem=mem, listed=listed, global_scope=False)
    valid = mem[:, None, None] & listed[:, :, None] & np.isfinite(ret)
    return {
        "breadth": st["BREADTH"],
        "n_valid": st["n_valid"],
        "n_up": ((ret > 0) & valid).sum(axis=0),
        "n_down": ((ret < 0) & valid).sum(axis=0),
        "ok": st["ok"],
    }


def _compare(parent: dict[str, np.ndarray], shadow: dict[str, np.ndarray]) -> dict[str, int]:
    breadth_bad = ~np.isclose(parent["breadth"], shadow["breadth"], atol=ATOL, rtol=0.0, equal_nan=True)
    valid_bad = parent["n_valid"].astype(np.int32) != shadow["n_valid"]
    up_bad = parent["n_up"].astype(np.int32) != shadow["n_up"]
    down_bad = parent["n_down"].astype(np.int32) != shadow["n_down"]
    ok_bad = parent["ok"] != shadow["ok"]
    bad = breadth_bad | valid_bad | up_bad | down_bad | ok_bad
    return {
        "eligible_clock_n": int(bad.size),
        "parent_reference_driver_n": int(np.isfinite(parent["breadth"]).sum()),
        "implementation_driver_n": int(np.isfinite(shadow["breadth"]).sum()),
        "driver_value_mismatch_n": int(bad.sum()),
    }


def _episodes(dates: list[str], shadow: dict[str, np.ndarray]) -> dict[str, Any]:
    on_n = 0
    off_n = 0
    window_n = 0
    target_n = 0
    duplicate_n = 0
    ended_active = 0
    target_sha_mismatch = 0
    for di, day in enumerate(dates):
        session = start_session(day)
        seen: set[tuple[str, str]] = set()
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
                    on_n += 1
                    rows = target_signals(event, obs, available_at=hhmm, driver_identity_sha256="")
                    target_n += len(rows)
                    symbols = [r["target_symbol"] for r in rows]
                    if symbols != ["6590", "6787", "6861", "6941", "6961"] or any(r["order_eligible"] != "false" for r in rows):
                        target_sha_mismatch += 1
                    for symbol in symbols:
                        key = (event["episode_id"], symbol)
                        if key in seen:
                            duplicate_n += 1
                        seen.add(key)
                elif event["event_type"] == "ALPHA_OFF":
                    off_n += 1
                elif event["event_type"] == "ALPHA_WINDOW_END":
                    window_n += 1
            if hhmm == "11:25" and session.state != "INACTIVE":
                ended_active += 1
        if session.state != "INACTIVE":
            ended_active += 1
    return {
        "alpha_on_n": on_n,
        "alpha_off_n": off_n,
        "window_end_n": window_n,
        "target_shadow_record_n": target_n,
        "duplicate_target_n": duplicate_n,
        "sessions_ending_active": ended_active,
        "target_set_mismatch_n": target_sha_mismatch,
        "forward_returns_computed": False,
        "q80_on": Q80_ON,
        "q60_off": Q60_OFF,
    }


def run_parity(symbols: list[str]) -> dict[str, Any]:
    calendars = exposed_dates()
    total = {"eligible_clock_n": 0, "parent_reference_driver_n": 0, "implementation_driver_n": 0, "driver_value_mismatch_n": 0}
    episodes = {"alpha_on_n": 0, "alpha_off_n": 0, "window_end_n": 0, "target_shadow_record_n": 0, "duplicate_target_n": 0, "sessions_ending_active": 0, "target_set_mismatch_n": 0}
    prospective_rows_read = 0
    for stage, key in (("DISCOVERY", "discovery"), ("FV", "fv")):
        dates = calendars[key]
        if not dates:
            raise RuntimeError(f"missing_{key}_dates")
        panel = load_prices(symbols=symbols, dates=dates, stage=stage)
        if any(d >= PROSPECTIVE_FROM for d in panel["dates"]):
            raise RuntimeError("prospective_row_materialized")
        parent = _parent_reference(px=panel["px"], age=panel["age"], listed=panel["listed"])
        shadow = panel_driver(px=panel["px"], age=panel["age"], listed=panel["listed"])
        stats = _compare(parent, shadow)
        for name in total:
            total[name] += stats[name]
        ep = _episodes(dates, shadow)
        for name in episodes:
            episodes[name] += ep[name]
        print(f"SHADOW_PARITY {stage} clocks={stats['eligible_clock_n']} mismatch={stats['driver_value_mismatch_n']}", flush=True)
    episodes["forward_returns_computed"] = False
    return {**total, "episodes": episodes, "prospective_rows_read": prospective_rows_read, "tolerance_abs": ATOL, "pass": total["driver_value_mismatch_n"] == 0 and episodes["duplicate_target_n"] == 0 and episodes["sessions_ending_active"] == 0 and episodes["target_set_mismatch_n"] == 0 and episodes["alpha_on_n"] > 0 and episodes["target_shadow_record_n"] == 5 * episodes["alpha_on_n"]}
