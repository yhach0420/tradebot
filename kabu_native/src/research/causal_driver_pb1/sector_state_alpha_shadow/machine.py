"""Q80/Q60 hysteresis. Q60 is an episode boundary, not a position exit."""
from __future__ import annotations

from dataclasses import dataclass, replace

from research.causal_driver_pb1.phase2_precommit import CLOCK_FIRST, CLOCK_LAST
from research.causal_driver_pb1.sector_state_alpha_shadow import (
    CANDIDATE_LIST_SHA256,
    FAMILY_ID,
    M3_TARGET_SHA256,
    M3_TARGETS,
    PRECOMMIT_SHA256,
    Q60_OFF,
    Q80_ON,
    VALIDATED_TRANSMISSION_SHA256,
)
from research.causal_driver_pb1.sector_state_alpha_shadow.driver import DriverObservation

RUNTIME_EMITS = {"M1": False, "M2": False, "M3": True}
CLOCK_END = CLOCK_LAST


def clock_valid(hhmm: str) -> bool:
    text = str(hhmm)
    if len(text) != 5 or text[2] != ":":
        return False
    return CLOCK_FIRST <= text <= CLOCK_LAST


@dataclass(frozen=True, slots=True)
class Session:
    trading_date: str
    state: str = "INACTIVE"
    episode_counter: int = 0
    episode_id: str | None = None
    episode_start: str | None = None
    unavailable_open: bool = False


def start_session(trading_date: str) -> Session:
    return Session(trading_date=str(trading_date))


def step(
    session: Session,
    *,
    parent_id: str,
    hhmm: str,
    driver: DriverObservation,
) -> tuple[Session, list[dict[str, str]]]:
    if parent_id not in RUNTIME_EMITS:
        raise RuntimeError("unknown_parent")
    if not RUNTIME_EMITS[parent_id]:
        return session, []
    if parent_id != "M3":
        return session, []
    if not clock_valid(hhmm):
        return session, []
    events: list[dict[str, str]] = []
    state = session
    if not driver.available:
        if state.state == "ACTIVE":
            state = replace(state, state="ACTIVE_DATA_GAP", unavailable_open=True)
            events.append(_event(state, hhmm, "DRIVER_UNAVAILABLE", "FRESHNESS_OR_COVERAGE"))
        elif state.state == "INACTIVE" and not state.unavailable_open:
            state = replace(state, unavailable_open=True)
            events.append(_event(state, hhmm, "DRIVER_UNAVAILABLE", "FRESHNESS_OR_COVERAGE"))
        elif state.state == "ACTIVE_DATA_GAP":
            pass
    else:
        value = float(driver.value)
        if state.state == "ACTIVE_DATA_GAP":
            state = replace(state, unavailable_open=False)
            events.append(_event(state, hhmm, "DRIVER_RECOVERED", "DRIVER_AVAILABLE"))
            if value <= Q60_OFF:
                events.append(_event(state, hhmm, "ALPHA_OFF", "Q60_HYSTERESIS_OFF"))
                state = replace(state, state="INACTIVE", episode_id=None, episode_start=None)
            else:
                state = replace(state, state="ACTIVE")
        elif state.state == "INACTIVE":
            state = replace(state, unavailable_open=False)
            if value >= Q80_ON:
                counter = state.episode_counter + 1
                episode_id = f"M3-{state.trading_date}-{counter:03d}"
                state = replace(state, state="ACTIVE", episode_counter=counter, episode_id=episode_id, episode_start=hhmm)
                events.append(_event(state, hhmm, "ALPHA_ON", "Q80_ON"))
        elif state.state == "ACTIVE":
            state = replace(state, unavailable_open=False)
            if value <= Q60_OFF:
                events.append(_event(state, hhmm, "ALPHA_OFF", "Q60_HYSTERESIS_OFF"))
                state = replace(state, state="INACTIVE", episode_id=None, episode_start=None)
    if hhmm == CLOCK_END and state.state in {"ACTIVE", "ACTIVE_DATA_GAP"}:
        events.append(_event(state, hhmm, "ALPHA_WINDOW_END", "RESEARCH_CLOCK_END_1125"))
        state = replace(state, state="INACTIVE", episode_id=None, episode_start=None, unavailable_open=False)
    return state, events


def _event(session: Session, hhmm: str, event_type: str, reason: str) -> dict[str, str]:
    return {
        "alpha_family_id": FAMILY_ID,
        "trading_date": session.trading_date,
        "decision_time": hhmm,
        "signal_time": hhmm,
        "episode_id": session.episode_id or "",
        "episode_start": session.episode_start or "",
        "episode_state": "INACTIVE" if event_type in {"ALPHA_OFF", "ALPHA_WINDOW_END"} else session.state,
        "event_type": event_type,
        "event_reason": reason,
        "direction": "LONG",
        "supporting_parent_ids": "M3",
    }


SCHEMA_FIELDS = (
    "alpha_signal_id", "alpha_family_id", "target_symbol", "direction", "trading_date", "decision_time",
    "signal_time", "available_at", "driver_scope", "driver_metric", "driver_lookback", "driver_value",
    "driver_valid_n", "driver_up_n", "driver_down_n", "q80_on", "q60_off", "episode_id", "episode_state",
    "event_type", "event_reason", "supporting_parent_ids", "validated_transmission_sha256", "target_set_sha256",
    "driver_identity_sha256", "precommit_sha256", "runtime_binding_status", "research_clock_valid",
    "freshness_valid", "shadow_only", "order_eligible",
)


def target_signals(
    event: dict[str, str],
    driver: DriverObservation,
    *,
    available_at: str,
    driver_identity_sha256: str,
) -> list[dict[str, str]]:
    if event.get("event_type") != "ALPHA_ON":
        return []
    rows = []
    for symbol in M3_TARGETS:
        row = {
            **event,
            "alpha_signal_id": f"{event['episode_id']}-{symbol}-ALPHA_ON",
            "target_symbol": symbol,
            "available_at": available_at,
            "driver_scope": "SECTOR_3650",
            "driver_metric": "BREADTH",
            "driver_lookback": "1",
            "driver_value": "" if driver.value is None else repr(float(driver.value)),
            "driver_valid_n": str(driver.valid_n),
            "driver_up_n": str(driver.up_n),
            "driver_down_n": str(driver.down_n),
            "q80_on": repr(Q80_ON),
            "q60_off": repr(Q60_OFF),
            "validated_transmission_sha256": VALIDATED_TRANSMISSION_SHA256,
            "transmission_candidate_list_sha256": CANDIDATE_LIST_SHA256,
            "target_set_sha256": M3_TARGET_SHA256,
            "driver_identity_sha256": driver_identity_sha256,
            "precommit_sha256": PRECOMMIT_SHA256,
            "runtime_binding_status": "EXACT_RUNTIME_OBSERVABILITY_PROVEN",
            "research_clock_valid": "true",
            "freshness_valid": "true" if driver.freshness_valid else "false",
            "shadow_only": "true",
            "order_eligible": "false",
        }
        if tuple(SCHEMA_FIELDS) != tuple(k for k in SCHEMA_FIELDS if k in row):
            raise RuntimeError("schema_field_missing")
        if row["order_eligible"] != "false" or row["shadow_only"] != "true":
            raise RuntimeError("shadow_order_flag")
        rows.append(row)
    if [r["target_symbol"] for r in rows] != list(M3_TARGETS):
        raise RuntimeError("m3_target_emission_n")
    return rows
