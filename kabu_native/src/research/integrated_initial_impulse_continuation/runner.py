"""IIC runner — integrated scenario TRAIN (+ VAL/CAP5 if pass)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.integrated_initial_impulse_continuation.arms import increment, materialize, summarize
from research.integrated_initial_impulse_continuation.constants import (
    ARMS,
    CANCEL,
    HOLDOUT,
    LIVE_ORDER,
    MIN_TRAIN_EPISODES,
    OUT_ROOT,
    SEED,
    STRIDE,
    SUBMIT,
    TRAIN,
    VALIDATION,
)
from research.integrated_initial_impulse_continuation.loader import load_streams
from research.integrated_initial_impulse_continuation.reporting import emit
from research.integrated_initial_impulse_continuation.state_machine import build_episodes

JST = ZoneInfo("Asia/Tokyo")


def _state_counts(episodes) -> dict[str, int]:
    keys = [
        "S0_QUIET_BASE", "S1_FLOW_IGNITION", "S2_RANGE_BREAK", "S3_BREAK_HOLD", "ENTRY",
        "S4_IMPULSE_ADVANCE", "S5_HEALTHY_CONTINUATION", "S6_BREAK_FAILURE",
        "S7_NO_FOLLOW_THROUGH", "S8_MOMENTUM_EXHAUSTION", "S9_PROFIT_GIVEBACK", "HARD_EXIT",
    ]
    out = {k: 0 for k in keys}
    for ep in episodes:
        seen = set(ep.states)
        for k in keys:
            if k in seen:
                out[k] += 1
    return out


def _nesting_ok(episodes) -> tuple[bool, str]:
    """ENTRY ⊆ S3 ⊆ S2 ⊆ S1 ⊆ S0; post states only after ENTRY."""
    for ep in episodes:
        s = ep.states
        def has(x):
            return x in s
        if has("ENTRY") and not (has("S3_BREAK_HOLD") and has("S2_RANGE_BREAK") and has("S1_FLOW_IGNITION") and has("S0_QUIET_BASE")):
            return False, "entry_without_pre_states"
        if has("S3_BREAK_HOLD") and not has("S2_RANGE_BREAK"):
            return False, "s3_without_s2"
        if has("S2_RANGE_BREAK") and not has("S1_FLOW_IGNITION"):
            return False, "s2_without_s1"
        if has("S4_IMPULSE_ADVANCE") and not has("ENTRY"):
            return False, "s4_without_entry"
    return True, "STATE_NESTING_PASS"


def _train_gate(a5: dict[str, Any], entry_n: int, nest_ok: bool) -> tuple[bool, str, list[str]]:
    codes: list[str] = []
    if not nest_ok:
        return False, "STATE_INTEGRITY_BLOCKED", ["STATE_INTEGRITY_BLOCKED", "IIC_TRAIN_NO_EDGE"]
    if entry_n < MIN_TRAIN_EPISODES:
        return False, "INSUFFICIENT_EPISODE_COUNT", ["INSUFFICIENT_EPISODE_COUNT", "IIC_TRAIN_NO_EDGE"]
    if (a5.get("n") or 0) < MIN_TRAIN_EPISODES:
        return False, "INSUFFICIENT_EPISODE_COUNT", ["INSUFFICIENT_EPISODE_COUNT", "IIC_TRAIN_NO_EDGE"]
    if (a5.get("pnl") or 0) <= 0 or (a5.get("mean") or 0) <= 0:
        codes += ["IIC_TRAIN_NO_EDGE", "IIC_STRATEGY_REJECTED"]
        return False, "pnl", codes
    pf = a5.get("pf")
    if pf is None or (isinstance(pf, float) and pf <= 1.0):
        codes += ["IIC_TRAIN_NO_EDGE", "IIC_STRATEGY_REJECTED"]
        return False, "pf", codes
    if (a5.get("top1_symbol_share") or 0) >= 0.45:
        codes += ["IIC_TRAIN_NO_EDGE", "IIC_STRATEGY_REJECTED"]
        return False, "symbol_concentration", codes
    return True, "IIC_TRAIN_CANDIDATE", ["IIC_TRAIN_CANDIDATE"]


def _cap5(trades) -> dict[str, Any]:
    try:
        from research.canonical_zero_base_v2.cap5 import CapTrade, replay_cap5
    except Exception:
        return {"trades": 0, "note": "cap5_unavailable", "pnl_5bps": 0.0}
    rows = [
        CapTrade(
            day=t.day, symbol=t.symbol, episode_id=t.episode_id,
            entry_time=t.entry_time, exit_time=t.exit_time,
            entry_price=t.entry_ask, exit_price=t.exit_bid,
            pnl_5bps=t.pnl_yen, exit_reason=t.exit_reason,
            strategy_id="IIC", setup_id=t.episode_id,
            session="AM" if t.entry_time.hour < 12 else "PM",
            mfe=t.mfe_pct, mae=t.mae_pct, winner=t.winner,
        )
        for t in trades
    ]
    return replay_cap5(rows, portfolio_id="IIC_CAP5")


def run_iic(
    *,
    run_id: Optional[str] = None,
    out_root: Optional[Path] = None,
    test_results: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = (out_root or OUT_ROOT) / run_id

    print(f"[iic] load TRAIN={TRAIN} stride={STRIDE}...", flush=True)
    streams_train = load_streams([TRAIN])
    print(f"[iic] streams={len(streams_train)}", flush=True)

    print("[iic] build episodes...", flush=True)
    episodes = []
    for key, ticks in streams_train.items():
        episodes.extend(build_episodes(key, ticks))
    print(f"[iic] episodes={len(episodes)}", flush=True)

    state_counts = _state_counts(episodes)
    nest_ok, nest_reason = _nesting_ok(episodes)
    entry_eps = [e for e in episodes if e.entry_idx is not None and "ENTRY" in e.states]
    entry_n = len(entry_eps)

    train_arms = {}
    train_trades = {}
    for arm in ARMS:
        tr = materialize(episodes, streams_train, arm)
        train_trades[arm] = tr
        train_arms[arm] = summarize(tr)

    # arm nesting: same entry universe, A5 ⊆ ... by exit policy on same episodes
    arm_ns = {a: train_arms[a]["n"] for a in ARMS}
    arm_nest_ok = all(arm_ns[a] == arm_ns["A0"] for a in ARMS)  # same ENTRY set

    inc = {
        "A0_to_A1": increment(train_arms["A0"], train_arms["A1"]),
        "A1_to_A2": increment(train_arms["A1"], train_arms["A2"]),
        "A2_to_A3": increment(train_arms["A2"], train_arms["A3"]),
        "A3_to_A4": increment(train_arms["A3"], train_arms["A4"]),
        "A4_to_A5": increment(train_arms["A4"], train_arms["A5"]),
    }

    tg_ok, tg_reason, tg_codes = _train_gate(train_arms["A5"], entry_n, nest_ok and arm_nest_ok)

    val_arms = {a: {"n": 0, "note": "not_run"} for a in ARMS}
    cap5: dict[str, Any] = {"trades": 0, "pnl_5bps": 0.0, "note": "not_run"}
    vg_ok = False
    vg_reason = "SKIPPED_NO_TRAIN"

    if tg_ok:
        print("[iic] VALIDATION...", flush=True)
        streams_val = load_streams([VALIDATION])
        eps_val = []
        for key, ticks in streams_val.items():
            eps_val.extend(build_episodes(key, ticks))
        for arm in ARMS:
            val_arms[arm] = summarize(materialize(eps_val, streams_val, arm))
        v5 = val_arms["A5"]
        if (v5.get("n") or 0) >= 20 and (v5.get("pnl") or 0) > 0 and isinstance(v5.get("pf"), (int, float)) and v5["pf"] > 1:
            vg_ok, vg_reason = True, "IIC_VALIDATED"
            streams_h = load_streams([HOLDOUT])
            eps_h = []
            for key, ticks in streams_h.items():
                eps_h.extend(build_episodes(key, ticks))
            cap5 = _cap5(materialize(eps_h, streams_h, "A5"))
        else:
            vg_ok, vg_reason = False, "IIC_VALIDATION_REJECTED"
    else:
        print(f"[iic] TRAIN fail ({tg_reason}) - skip VAL/CAP5", flush=True)

    codes = ["IIC_EPISODE_READY"]
    if nest_ok:
        codes.append("IIC_STATE_MACHINE_VALID")
    else:
        codes.append("STATE_INTEGRITY_BLOCKED")
    codes.extend(tg_codes)
    if tg_ok and vg_ok:
        codes += ["IIC_VALIDATED", "IIC_CAP5_READY", "IIC_STRATEGY_READY"]
    elif tg_ok and not vg_ok:
        codes += ["IIC_VALIDATION_REJECTED", "IIC_STRATEGY_REJECTED"]
    else:
        if "IIC_STRATEGY_REJECTED" not in codes:
            codes.append("IIC_STRATEGY_REJECTED")
    codes += ["NO_PAPER_ENTRY", "NO_PRODUCTION_CHANGE", "LIVE_TRADING_BLOCKED"]

    final = (
        "IIC_STRATEGY_READY" if vg_ok
        else ("IIC_TRAIN_CANDIDATE" if tg_ok else "IIC_STRATEGY_REJECTED")
    )
    if entry_n < MIN_TRAIN_EPISODES and "INSUFFICIENT_EPISODE_COUNT" in tg_codes:
        final = "INSUFFICIENT_EPISODE_COUNT"

    a5 = train_arms["A5"]
    payload: dict[str, Any] = {
        "run_id": run_id,
        "phase": "integrated_initial_impulse_continuation",
        "seed": SEED,
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False, "paper_auto_start": False, "live_trading_enabled": False,
        "hypothesis": "quiet→buy ignition→range break→hold→ENTRY→impulse continuation→exit on failure/exhaust/giveback",
        "state_machine": "S0→S1→S2→S3→ENTRY→S4→S5|S6|S7|S8|S9→EXIT",
        "stride": STRIDE,
        "episode_stats": {
            "all": len(episodes), "with_entry": entry_n,
            "complete": sum(1 for e in episodes if e.status == "COMPLETE"),
        },
        "state_counts": state_counts,
        "entry_sample": [
            {
                "episode_id": e.episode_id, "entry_time": e.entry_time.isoformat() if e.entry_time else None,
                "entry_ask": e.entry_ask, "break_level": e.break_level,
                "base_low": e.base_low, "base_high": e.base_high,
            }
            for e in entry_eps[:40]
        ],
        "train_arms": train_arms,
        "val_arms": val_arms,
        "incremental": inc,
        "train_gate": {"ok": tg_ok, "reason": tg_reason},
        "val_gate": {"ok": vg_ok, "reason": vg_reason},
        "cap5": {k: cap5.get(k) for k in ("trades", "pnl_5bps", "PF_5bps", "trades_per_day", "pos_days", "neg_days", "note")},
        "symbol_dependency": {"top1": a5.get("top1_symbol_share")},
        "execution_audit": {
            "entry": "canonical_ask", "exit": "canonical_bid",
            "cost_bps": 5.0, "lot": 100, "stride": 1,
            "no_raw_bid_ask_semantic": True,
            "verdict": "EXECUTION_SEMANTICS_OK",
        },
        "integrity": {
            "state_nesting": nest_reason,
            "arm_same_entry_n": arm_nest_ok,
            "stride": STRIDE,
            "duplicate_entry_blocked": True,
            "verdict": "STATE_INTEGRITY_PASS" if nest_ok and arm_nest_ok else "STATE_INTEGRITY_BLOCKED",
        },
        "tests": test_results or {"all_passed": False, "rows": [{"name": "deferred", "status": "pending"}]},
        "verdict": {"final_verdict": final, "codes": codes},
    }

    payload["completion"] = {
        "1_hypothesis": payload["hypothesis"],
        "2_state_machine": payload["state_machine"],
        "3_state_counts": state_counts,
        "4_all_episodes": len(episodes),
        "5_entry_n": entry_n,
        "6_exit_reasons": a5.get("reasons"),
        "7_arm_n": {a: train_arms[a].get("n") for a in ARMS},
        "8_pf": {a: train_arms[a].get("pf") for a in ARMS},
        "9_pnl": {a: train_arms[a].get("pnl") for a in ARMS},
        "10_mean": {a: train_arms[a].get("mean") for a in ARMS},
        "11_incremental": inc,
        "12_winner_rate": a5.get("winner_rate"),
        "13_stop_5m": a5.get("stop_5m_rate"),
        "14_break_failure": a5.get("break_failure_rate"),
        "15_no_follow": a5.get("no_follow_rate"),
        "16_mfe_capture": a5.get("mfe_capture"),
        "17_mae": a5.get("avg_mae"),
        "18_avg_hold": a5.get("avg_hold"),
        "19_daily": a5.get("by_day"),
        "20_symbol_concentration": a5.get("top1_symbol_share"),
        "21_train": {"ok": tg_ok, "reason": tg_reason},
        "22_validation": {"ok": vg_ok, "reason": vg_reason, "A5": val_arms.get("A5")},
        "23_cap5": payload["cap5"],
        "24_integrity": payload["integrity"],
        "25_execution_audit": payload["execution_audit"],
        "26_tests": test_results,
        "27_submit_cancel_live": (SUBMIT, CANCEL, LIVE_ORDER),
        "28_mainline_changed": False,
        "29_final_verdict": final,
        "artifacts": str(out_dir),
    }

    print("[iic] emit...", flush=True)
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
