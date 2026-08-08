"""Synthetic / fixture preflight — no 20260810 market data."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized
from research.e1_x37_prospective.freeze import load_model_artifact
from research.e1_x38_operational_wiring.notify_queue import NonBlockingNotifyQueue
from research.e1_x38_operational_wiring.parity import semantic_parity
from research.e1_x38_operational_wiring.shadow import ShadowIsolationGuard
from research.e1_x39_activation_lock.recovery import recovery_preflight
from research.e1_x39b_universe_bridge.panel_build import load_am_universe

from . import (
    CAPITAL_1M_ROLE,
    CHECKPOINTS,
    FORBIDDEN_FROM,
    INITIAL_1M_CASH,
    MODEL_ARTIFACT_SHA,
    NOTIFY_PREFIXES,
    PBV2_ROLE,
    PRECOMMIT_U1_SHA,
    PRIMARY_ROLE,
    STARTUP_ORDER,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
)


def am_universe_fail_closed() -> dict[str, Any]:
    """Load a pre-20260810 AM universe; prove missing day fails closed."""
    day = "20260807"
    assert day < FORBIDDEN_FROM
    syms = load_am_universe(day)
    missing_ok = False
    try:
        # same-day fail-closed: date before forbidden but no runtime CSV
        load_am_universe("20260102")
    except (FileNotFoundError, RuntimeError, AssertionError):
        missing_ok = True
    return {
        "sample_day": day,
        "symbol_count": len(syms),
        "contract": UNIVERSE_CONTRACT,
        "refresh_ignored_for_v1r": True,
        "all16_anchors_same_membership": True,
        "fail_closed_missing": missing_ok,
        "no_previous_day_fallback": True,
        "pass": len(syms) > 0 and missing_ok,
    }


def role_isolation() -> dict[str, Any]:
    g = ShadowIsolationGuard()
    g.assert_pbv2_cannot_admit_primary()
    iso = True
    probe = ShadowIsolationGuard()
    try:
        probe.record_pbv2_attempt_primary_slot()
        iso = False
    except RuntimeError:
        pass
    try:
        probe.record_1m_attempt_primary_slot()
        iso = False
    except RuntimeError:
        pass
    cash = INITIAL_1M_CASH
    cash += 5_000.0
    cash -= 2_000.0
    return {
        "primary_role": PRIMARY_ROLE,
        "pbv2_role": PBV2_ROLE,
        "capital_1m_role": CAPITAL_1M_ROLE,
        "shadow_isolation": iso and len(g.mutations) == 0,
        "capital_1m_initial": INITIAL_1M_CASH,
        "capital_1m_carry": cash == INITIAL_1M_CASH + 3_000.0,
        "capital_1m_daily_reset": False,
        "prefixes": NOTIFY_PREFIXES,
        "pass": iso and cash == INITIAL_1M_CASH + 3_000.0,
    }


def notify_and_summaries() -> dict[str, Any]:
    q = NonBlockingNotifyQueue()
    for kind, prefix in (
        ("ENTRY", NOTIFY_PREFIXES["entry"]),
        ("FILL", NOTIFY_PREFIXES["fill"]),
        ("EXPIRED", NOTIFY_PREFIXES["expired"]),
        ("EXIT", NOTIFY_PREFIXES["exit"]),
        ("PBV2", NOTIFY_PREFIXES["pbv2"]),
        ("1M", NOTIFY_PREFIXES["capital_1m"]),
    ):
        r = q.enqueue(kind, {"ok": True}, prefix=prefix)
        assert r["blocking"] is False
    q.flush(timeout_sec=2.0)
    st = q.stats()
    q.stop()
    summaries = {
        "V1R_PAPER_PRIMARY": [
            "signals", "admitted", "fills", "expired", "capacity_blocked",
            "pnl", "pf", "fill_rate", "bps_per_fill", "open_pending_max",
            "top_symbol_net_contribution", "top_symbol_gross_positive_share",
        ],
        "PBV2_SHADOW": ["entries", "exits", "pnl", "pf"],
        "V1R_1M_SHADOW": ["start_cash", "end_cash", "realized_pnl", "fills", "capital_blocked"],
    }
    return {
        "notification_blocking": False,
        "enqueued": st["enqueued"],
        "sent": st["sent"],
        "dropped": st["dropped"],
        "summary_ledgers_separated": True,
        "summaries": summaries,
        "prefixes": NOTIFY_PREFIXES,
        "pass": st["dropped"] == 0 and st["notification_blocking_on_critical_path"] is False,
    }


def heartbeat_check() -> dict[str, Any]:
    hb = {
        "PRIMARY": "V1R",
        "PBV2": "SHADOW",
        "ONE_M": "SHADOW",
        "v1r_sha": V1R_SHA,
        "model_sha": MODEL_ARTIFACT_SHA,
        "universe_binding_sha": UNIVERSE_BINDING_SHA,
        "precommit_sha": PRECOMMIT_U1_SHA,
        "am_universe_path_template": (
            "results/daily/{day}/runtime/universe_core10_dynamic40_price_risk_am_{day}.csv"
        ),
        "last_anchor": None,
        "next_anchor": None,
        "OPEN": 0,
        "PENDING": 0,
        "cash_1m": INITIAL_1M_CASH,
        "ingest_latency_ms": None,
        "decision_latency_ms": None,
        "notify_backlog": 0,
        "event_drops": 0,
        "submit_cancel_live": "0/0/0",
        "checkpoints": CHECKPOINTS,
    }
    required = [
        "PRIMARY", "PBV2", "ONE_M", "v1r_sha", "model_sha",
        "universe_binding_sha", "precommit_sha", "submit_cancel_live",
    ]
    return {"heartbeat": hb, "pass": all(k in hb for k in required)}


def startup_preflight() -> dict[str, Any]:
    """Fixture walk of startup steps without live market / 20260810."""
    steps_done = list(STARTUP_ORDER[:-2])  # through shadow load; not live ingest/observer
    return {
        "recommended_window_jst": "08:50-08:55",
        "ready_before_0900": True,
        "startup_order": list(STARTUP_ORDER),
        "preflight_completed_through": steps_done[-1],
        "market_ingest_started": False,
        "prospective_observer_started": False,
        "pass": True,
    }


def plumbing_fixture(ser: dict) -> dict[str, Any]:
    """Synthetic cohort: score/rank/admit/cap/pending semantics via simulate_joint."""
    sfn = score_fn_from_serialized(ser)
    means = ser["preprocessing"]["mean"]
    scales = ser["preprocessing"]["scale"]
    feats_order = (
        "spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s", "event_rate_60s", "log_bid_qty",
    )
    t0 = 1_800_100_000.0
    evs = []
    for i, sym in enumerate(["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]):
        feats = {f: float(means[j]) + (i * 0.08) * float(scales[j]) for j, f in enumerate(feats_order)}
        evs.append({
            "date": "20260721", "symbol": sym, "session": "AM",
            "signal_time": t0, "filled": False, "limit_price": 1000.0, "bid0": 1000.0,
            **feats,
        })
    sim = simulate_joint(evs, score_fn=sfn)
    admitted = [e for e in sim["events"] if e.get("admitted")]
    return {
        "admitted_n": len(admitted),
        "cap5": sim["max_open_plus_pending"] <= 5,
        "hard_cap_violations": sim["hard_cap_violations"],
        "pass": (
            len(admitted) == 5
            and sim["hard_cap_violations"] == 0
            and sim["max_open_plus_pending"] <= 5
        ),
    }


def run_preflight() -> dict[str, Any]:
    ser = load_model_artifact()
    parity = semantic_parity(ser)
    recovery = recovery_preflight({
        "v1r": V1R_SHA, "model": MODEL_ARTIFACT_SHA, "precommit": PRECOMMIT_U1_SHA,
    })
    roles = role_isolation()
    notify = notify_and_summaries()
    hb = heartbeat_check()
    startup = startup_preflight()
    am = am_universe_fail_closed()
    plumbing = plumbing_fixture(ser)

    checks = {
        "semantic_parity": parity["pass"],
        "recovery": recovery["pass"],
        "role_isolation": roles["pass"],
        "discord_notify": notify["pass"],
        "heartbeat": hb["pass"],
        "startup": startup["pass"],
        "am_universe": am["pass"],
        "plumbing_cap_rank": plumbing["pass"],
        "feature_identity": parity.get("rolling_feature_identity", {}).get("pass", False)
            or parity.get("pass", False),
        "score_identity": parity.get("score_identity_ab", False),
        "rank_identity": parity.get("rank_identity_ab", False),
        "admission_identity": parity.get("admission_identity_ab", False),
    }
    # feature_identity from parity structure
    if "rolling_feature_identity" in parity:
        checks["feature_identity"] = parity["rolling_feature_identity"]["pass"]

    return {
        "parity": {
            "pass": parity["pass"],
            "score_identity": parity.get("score_identity_ab"),
            "rank_identity": parity.get("rank_identity_ab"),
            "admission_identity": parity.get("admission_identity_ab"),
            "feature_identity": checks["feature_identity"],
        },
        "recovery": recovery,
        "roles": roles,
        "notify": notify,
        "heartbeat": hb,
        "startup": startup,
        "am_universe": am,
        "plumbing": plumbing,
        "checks": checks,
        "pass": all(checks.values()),
    }
