"""State, target, safety, and non-emission checks. No price history."""
from __future__ import annotations

from pathlib import Path

from research.causal_driver_pb1.sector_state_alpha_shadow import M3_TARGET_SHA256, M3_TARGETS, Q60_OFF, Q80_ON
from research.causal_driver_pb1.sector_state_alpha_shadow.config import ShadowConfig, activation
from research.causal_driver_pb1.sector_state_alpha_shadow.driver import CONSTITUENT_N, breadth_from_endpoints, required_valid_n
from research.causal_driver_pb1.sector_state_alpha_shadow.machine import RUNTIME_EMITS, start_session, step, target_signals
from research.causal_driver_pb1.sector_state_alpha_shadow.safety import counters, forbid_submit
from research.causal_driver_pb1.sector_state_alpha_shadow.driver import DriverObservation


def _obs(value: float | None, *, available: bool = True, valid_n: int = 30) -> DriverObservation:
    return DriverObservation(available, value, valid_n, 10, 10, available, 30, 24)


def _on(events: list[dict[str, str]]) -> list[dict[str, str]]:
    return [e for e in events if e["event_type"] == "ALPHA_ON"]


def check_state_machine() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        rows.append({"test": name, "pass": ok, "detail": detail})

    st = start_session("20260422")
    st, ev = step(st, parent_id="M3", hhmm="10:00", driver=_obs(Q80_ON - 1e-6))
    record("S1", st.state == "INACTIVE" and not _on(ev))

    st, ev = step(start_session("20260422"), parent_id="M3", hhmm="10:00", driver=_obs(Q80_ON))
    record("S2", st.state == "ACTIVE" and len(_on(ev)) == 1 and ev[0]["event_reason"] == "Q80_ON")

    st, ev = step(st, parent_id="M3", hhmm="10:01", driver=_obs((Q60_OFF + Q80_ON) / 2))
    record("S3", st.state == "ACTIVE" and ev == [])

    st, ev = step(st, parent_id="M3", hhmm="10:02", driver=_obs(Q60_OFF))
    record("S4", st.state == "INACTIVE" and any(e["event_type"] == "ALPHA_OFF" and e["event_reason"] == "Q60_HYSTERESIS_OFF" for e in ev))

    st, ev = step(st, parent_id="M3", hhmm="10:03", driver=_obs((Q60_OFF + Q80_ON) / 2))
    quiet = st.state == "INACTIVE" and not _on(ev)
    st, ev = step(st, parent_id="M3", hhmm="10:04", driver=_obs(Q80_ON))
    record("S5", quiet and st.episode_counter == 2 and len(_on(ev)) == 1)

    st, ev = step(start_session("20260422"), parent_id="M3", hhmm="09:10", driver=_obs(Q80_ON))
    record("S6", len(_on(ev)) == 1 and ev[0]["decision_time"] == "09:10")

    st, ev = step(start_session("20260422"), parent_id="M3", hhmm="09:09", driver=_obs(Q80_ON))
    record("S7", ev == [] and st.state == "INACTIVE")

    st, ev = step(start_session("20260422"), parent_id="M3", hhmm="11:26", driver=_obs(Q80_ON))
    record("S8", ev == [] and st.state == "INACTIVE")

    st, _ = step(start_session("20260422"), parent_id="M3", hhmm="10:00", driver=_obs(Q80_ON))
    st, ev = step(st, parent_id="M3", hhmm="11:25", driver=_obs(Q80_ON))
    record("S9", st.state == "INACTIVE" and any(e["event_type"] == "ALPHA_WINDOW_END" and e["event_reason"] == "RESEARCH_CLOCK_END_1125" for e in ev) and not any(e["event_reason"] == "Q60_HYSTERESIS_OFF" for e in ev))

    need = required_valid_n(30)
    low = breadth_from_endpoints(
        price_now=[100.0] * 30,
        age_now=[0.0] * 23 + [999.0] * 7,
        price_prev=[99.0] * 30,
        age_prev=[0.0] * 30,
        listed=[True] * 30,
    )
    high = breadth_from_endpoints(
        price_now=[100.0] * 30,
        age_now=[0.0] * 24 + [999.0] * 6,
        price_prev=[99.0] * 30,
        age_prev=[0.0] * 30,
        listed=[True] * 30,
    )
    record("S10", need == 24 and low.valid_n == 23 and not low.available and high.valid_n == 24 and high.available)

    st, ev_on = step(start_session("20260422"), parent_id="M3", hhmm="10:00", driver=_obs(Q80_ON))
    episode = st.episode_id
    st, ev_gap = step(st, parent_id="M3", hhmm="10:01", driver=_obs(None, available=False, valid_n=23))
    gap_ok = st.state == "ACTIVE_DATA_GAP" and st.episode_id == episode and not any(e["event_type"] == "ALPHA_OFF" for e in ev_gap)
    st, ev_back = step(st, parent_id="M3", hhmm="10:02", driver=_obs((Q60_OFF + Q80_ON) / 2))
    record("S11", gap_ok and st.state == "ACTIVE" and st.episode_id == episode and st.episode_counter == 1 and any(e["event_type"] == "DRIVER_RECOVERED" for e in ev_back) and not _on(ev_back))

    nxt = start_session("20260423")
    record("S12", nxt.state == "INACTIVE" and nxt.episode_counter == 0 and nxt.trading_date != st.trading_date)
    return rows


def check_targets() -> dict[str, object]:
    st, ev = step(start_session("20260422"), parent_id="M3", hhmm="10:00", driver=_obs(Q80_ON))
    rows = target_signals(ev[0], _obs(Q80_ON), available_at="10:00", driver_identity_sha256="pending")
    symbols = [r["target_symbol"] for r in rows]
    return {
        "pass": symbols == list(M3_TARGETS) and len(rows) == 5 and all(r["order_eligible"] == "false" and r["shadow_only"] == "true" and r["target_set_sha256"] == M3_TARGET_SHA256 for r in rows),
        "n": len(rows),
        "symbols": symbols,
        "episode_open": st.state == "ACTIVE",
    }


def check_m1_m2() -> dict[str, object]:
    st0 = start_session("20260422")
    st1, ev1 = step(st0, parent_id="M1", hhmm="10:00", driver=_obs(1.0))
    st2, ev2 = step(st0, parent_id="M2", hhmm="10:00", driver=_obs(1.0))
    return {
        "pass": RUNTIME_EMITS["M1"] is False and RUNTIME_EMITS["M2"] is False and RUNTIME_EMITS["M3"] is True and ev1 == [] and ev2 == [] and st1 == st0 and st2 == st0,
        "m1_events": len(ev1),
        "m2_events": len(ev2),
    }


def check_order_path() -> dict[str, object]:
    root = Path(__file__).resolve().parent
    banned = ("register_symbols_cleared(", "submit_order", "cancel_order", "small_paper", "live_order")
    hits = []
    for path in sorted(root.glob("*.py")):
        if path.name == "checks.py":
            continue
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                hits.append(f"{path.name}:{token}")
    cfg = ShadowConfig()
    raised = False
    try:
        forbid_submit()
    except RuntimeError:
        raised = True
    orders_refused = False
    try:
        activation(enabled=True, orders_enabled=True)
    except RuntimeError:
        orders_refused = True
    return {
        "pass": not hits and counters() == {"PB1_INVOCATION_N": 0, "ENTRY_CREATED_N": 0, "submit": 0, "cancel": 0, "live": 0} and raised and orders_refused and not cfg.allows_shadow_evaluation() and cfg.shadow_only and not cfg.orders_enabled,
        "banned_hits": hits,
        "counters": counters(),
        "default_enabled": cfg.enabled,
    }


def run_checks() -> dict[str, object]:
    state = check_state_machine()
    targets = check_targets()
    parents = check_m1_m2()
    safety = check_order_path()
    state_pass = all(bool(r["pass"]) for r in state) and len(state) == 12
    return {
        "pass": state_pass and bool(targets["pass"]) and bool(parents["pass"]) and bool(safety["pass"]) and CONSTITUENT_N == 30,
        "state": state,
        "targets": targets,
        "parents": parents,
        "safety": safety,
    }
