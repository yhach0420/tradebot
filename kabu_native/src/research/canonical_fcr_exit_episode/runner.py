"""Canonical FCR EXIT episode runner — ENTRY fixed, evaluate ENTRY+EXIT."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.canonical_fcr_exit_episode.arms import (
    ExitTrade,
    increment_exit,
    materialize_arm,
    summarize,
)
from research.canonical_fcr_exit_episode.constants import (
    CANCEL,
    ENTRY_SOT,
    EXIT_ARMS,
    FROZEN_ENTRY,
    HOLDOUT,
    INTEGRITY_SOT,
    LIVE_ORDER,
    OUT_ROOT,
    SEED,
    SUBMIT,
    TRAIN,
    VALIDATION,
    WARMUP,
)
from research.canonical_fcr_exit_episode.entry_fixed import collect_frozen_entries, load_for_exit
from research.canonical_fcr_exit_episode.exit_states import build_exit_episode, class_counts
from research.canonical_fcr_exit_episode.reporting import emit

JST = ZoneInfo("Asia/Tokyo")


def _cap5(trades: list[ExitTrade]) -> dict[str, Any]:
    try:
        from research.canonical_zero_base_v2.cap5 import CapTrade, replay_cap5
    except Exception:
        return {"trades": 0, "note": "cap5_unavailable", "pnl_5bps": 0.0}
    caps = []
    for t in trades:
        caps.append(CapTrade(
            day=t.day, symbol=t.symbol, episode_id=t.episode_id,
            entry_time=t.entry_time, exit_time=t.exit_time,
            entry_price=t.entry_ask, exit_price=t.exit_bid,
            pnl_5bps=t.pnl_yen, exit_reason=t.exit_reason,
            strategy_id="FCR_EXIT", setup_id=t.impulse_id,
            session="AM" if t.entry_time.hour < 12 else "PM",
            mfe=t.mfe_pct, mae=t.mae_pct, winner=t.winner,
        ))
    return replay_cap5(caps, portfolio_id="FCR_ENTRY_EXIT_CAP5")


def _train_exit_gate(x5: dict[str, Any], x0: dict[str, Any]) -> tuple[bool, str]:
    if (x5.get("n") or 0) < 15:
        return False, "n<15"
    if (x5.get("pnl") or 0) <= 0 or (x5.get("mean") or 0) <= 0:
        return False, "pnl"
    pf = x5.get("pf")
    if pf is None or (isinstance(pf, float) and pf <= 1):
        return False, "pf"
    # must improve vs X0 on PF or mean
    if isinstance(x0.get("pf"), (int, float)) and isinstance(pf, (int, float)):
        if pf <= (x0["pf"] or 0) and (x5.get("mean") or 0) <= (x0.get("mean") or 0):
            return False, "no_improve_vs_x0"
    return True, "TRAIN_EXIT_PASS"


def run_exit(
    *,
    run_id: Optional[str] = None,
    out_root: Optional[Path] = None,
    test_results: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = (out_root or OUT_ROOT) / run_id

    # Warmup day not required for post-entry EXIT (ENTRY SM is causal within-day)
    days = [TRAIN, VALIDATION, HOLDOUT]
    print(f"[fcr-exit] load days={days} stride=1...", flush=True)
    streams = load_for_exit(days)
    print(f"[fcr-exit] streams={len(streams)}", flush=True)

    print("[fcr-exit] frozen ENTRY…", flush=True)
    entries_train = collect_frozen_entries(streams, [TRAIN])
    entries_val = collect_frozen_entries(streams, [VALIDATION])
    entries_hold = collect_frozen_entries(streams, [HOLDOUT])
    print(f"[fcr-exit] ENTRY n train/val/hold={len(entries_train)}/{len(entries_val)}/{len(entries_hold)}", flush=True)

    def build_eps(entries):
        eps = []
        for e in entries:
            eps.append(build_exit_episode(e, streams[e.stream_key]))
        return eps

    print("[fcr-exit] post-entry episodes TRAIN…", flush=True)
    eps_train = build_eps(entries_train)
    classes = class_counts(eps_train)

    train_arms = {}
    train_trades = {}
    for arm in EXIT_ARMS:
        tr = materialize_arm(eps_train, streams, arm)
        train_trades[arm] = tr
        train_arms[arm] = summarize(tr)

    inc = {
        "X0_to_X1": increment_exit(train_arms["X0"], train_arms["X1"]),
        "X1_to_X2": increment_exit(train_arms["X1"], train_arms["X2"]),
        "X2_to_X3": increment_exit(train_arms["X2"], train_arms["X3"]),
        "X3_to_X4": increment_exit(train_arms["X3"], train_arms["X4"]),
        "X4_to_X5": increment_exit(train_arms["X4"], train_arms["X5"]),
    }

    tg_ok, tg_reason = _train_exit_gate(train_arms["X5"], train_arms["X0"])

    val_arms = {a: {"n": 0, "note": "not_run"} for a in EXIT_ARMS}
    cap5: dict[str, Any] = {"trades": 0, "note": "no_validated_strategy", "pnl_5bps": 0.0}
    vg_ok = False
    vg_reason = "SKIPPED_NO_TRAIN"

    if tg_ok:
        print("[fcr-exit] VALIDATION…", flush=True)
        eps_val = build_eps(entries_val)
        for arm in EXIT_ARMS:
            val_arms[arm] = summarize(materialize_arm(eps_val, streams, arm))
        v5 = val_arms["X5"]
        if (v5.get("n") or 0) >= 5 and (v5.get("pnl") or 0) > 0 and isinstance(v5.get("pf"), (int, float)) and v5["pf"] > 1:
            vg_ok, vg_reason = True, "VALIDATION_PASS"
            # CAP5 on holdout with X5
            eps_h = build_eps(entries_hold)
            cap5 = _cap5(materialize_arm(eps_h, streams, "X5"))
        else:
            vg_ok, vg_reason = False, "VALIDATION_FAIL"
    else:
        print("[fcr-exit] TRAIN fail - skip VAL/CAP5 formal", flush=True)

    # strategy codes
    codes = ["FCR_EXIT_EPISODE_READY"]
    if tg_ok:
        codes.append("FCR_EXIT_CANDIDATE")
    else:
        codes += ["FCR_EXIT_NO_EDGE", "FCR_STRATEGY_REJECTED"]
    if vg_ok:
        codes.append("FCR_ENTRY_EXIT_STRATEGY_READY")
    else:
        codes.append("NO_VALIDATED_FCR_STRATEGY")
    codes += [
        "ENTRY_FROZEN", "NO_ENTRY_RETUNE",
        "CAPTURE_ONLY_CONTINUE", "NO_PAPER_ENTRY", "NO_PRODUCTION_CHANGE", "LIVE_TRADING_BLOCKED",
    ]

    x0, x5 = train_arms["X0"], train_arms["X5"]
    stop_improve = None
    if x0.get("stop_rate") is not None and x5.get("stop_rate") is not None:
        stop_improve = x0["stop_rate"] - x5["stop_rate"]
    np_improve = None
    if x0.get("noprogress_exit_rate") is not None and x5.get("noprogress_exit_rate") is not None:
        # for no-progress: higher intentional exit rate can be good; compare stop vs X0
        np_improve = (x0.get("stop_rate") or 0) - (x5.get("stop_rate") or 0)

    final_verdict = (
        "FCR_ENTRY_EXIT_STRATEGY_READY" if vg_ok
        else ("FCR_EXIT_CANDIDATE" if tg_ok else "FCR_STRATEGY_REJECTED")
    )

    payload: dict[str, Any] = {
        "run_id": run_id,
        "phase": "canonical_fcr_exit_episode",
        "seed": SEED,
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False, "paper_auto_start": False, "live_trading_enabled": False,
        "source_audit": {
            "entry_sot": str(ENTRY_SOT),
            "integrity_sot": str(INTEGRITY_SOT),
            "entry_frozen": True,
            "method": "post_entry_A_E_states_X0_X5",
        },
        "frozen_entry": FROZEN_ENTRY,
        "state_note": {
            "machine": "ENTRY→ADVANCE→{HEALTHY|NOISE|FALSE_RECLAIM|NO_PROGRESS|WINNER_GIVEBACK}",
            "policy": "A/B hold · C/D exit · E take profit",
        },
        "entry_counts": {"train": len(entries_train), "val": len(entries_val), "hold": len(entries_hold)},
        "class_counts": classes,
        "post_entry_n": len(eps_train),
        "train_arms": train_arms,
        "val_arms": val_arms,
        "incremental": inc,
        "train_gate": {"ok": tg_ok, "reason": tg_reason},
        "val_gate": {"ok": vg_ok, "reason": vg_reason},
        "cap5": {k: cap5.get(k) for k in ("trades", "pnl_5bps", "PF_5bps", "trades_per_day", "pos_days", "neg_days", "note")},
        "strategy": {
            "entry": "FCR_FULL_F5_FROZEN",
            "exit": "X5_FULL_FCR_EXIT" if tg_ok else None,
            "evaluated_as": "ENTRY_PLUS_EXIT",
        },
        "tests": test_results or {"all_passed": False, "rows": [{"name": "deferred", "status": "pending"}]},
        "verdict": {
            "final_verdict": final_verdict,
            "codes": codes,
            "FCR_EXIT_EPISODE_READY": True,
            "FCR_EXIT_CANDIDATE": tg_ok,
            "FCR_EXIT_NO_EDGE": not tg_ok,
            "FCR_ENTRY_EXIT_STRATEGY_READY": vg_ok,
            "NO_VALIDATED_FCR_STRATEGY": not vg_ok,
            "FCR_STRATEGY_REJECTED": not tg_ok,
        },
    }

    payload["completion"] = {
        "1_exit_direction": {
            "A_HEALTHY_ADVANCE": "HOLD",
            "B_TEMPORARY_NOISE": "HOLD",
            "C_FALSE_RECLAIM": "EXIT",
            "D_NO_PROGRESS": "EXIT",
            "E_WINNER_GIVEBACK": "TAKE_PROFIT",
        },
        "2_post_entry_states_n": len(eps_train),
        "3_healthy_n": classes.get("HEALTHY_ADVANCE", 0),
        "4_temporary_n": classes.get("TEMPORARY_NOISE", 0),
        "5_false_reclaim_n": classes.get("FALSE_RECLAIM", 0),
        "6_noprogress_n": classes.get("NO_PROGRESS", 0),
        "7_winner_giveback_n": classes.get("WINNER_GIVEBACK", 0),
        "8_X0_X5_n": {a: train_arms[a].get("n") for a in EXIT_ARMS},
        "9_X0_X5_pf": {a: train_arms[a].get("pf") for a in EXIT_ARMS},
        "10_X0_X5_mean": {a: train_arms[a].get("mean") for a in EXIT_ARMS},
        "11_winner_keep": {a: train_arms[a].get("winner_rate") for a in EXIT_ARMS},
        "12_stop_improve_X5_vs_X0": stop_improve,
        "13_noprogress_improve_proxy": np_improve,
        "14_mfe_capture": {a: train_arms[a].get("mfe_capture") for a in EXIT_ARMS},
        "15_mae": {a: train_arms[a].get("avg_mae") for a in EXIT_ARMS},
        "16_final_pf": x5.get("pf"),
        "17_final_pnl": x5.get("pnl"),
        "18_cap5": payload["cap5"],
        "19_validation": {"ok": vg_ok, "reason": vg_reason, "X5": val_arms.get("X5")},
        "20_final_verdict": final_verdict,
        "incremental": inc,
        "entry_frozen": True,
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "artifacts": str(out_dir),
    }

    print("[fcr-exit] emit…", flush=True)
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
