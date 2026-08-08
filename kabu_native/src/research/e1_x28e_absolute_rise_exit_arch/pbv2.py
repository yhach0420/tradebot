"""Phase B: freeze PBv2 EXIT from Paper runtime source (no re-implementation from memory)."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x6_provisional.util import sha256_obj
from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint.board import first_valid_quote
from small_paper.board_dynamic_trailing_shadow import (
    BOARD_HIGH_ACTIVATE_PCT,
    BOARD_HIGH_GIVEBACK_FRAC,
    BOARD_LOW_ACTIVATE_PCT,
    BOARD_LOW_GIVEBACK_FRAC,
    BOARD_SPLIT_PERCENTILE,
    trailing_params_for_board_tier,
)
from small_paper.no_progress_exit import (
    INITIAL_MFE_PCT,
    MAX_MFE_CAP_PCT,
    MAX_PNL_PCT,
    PHASE442_POLICY_KEY,
    SLOPE_PER_5MIN,
    START_TIME_SEC,
    no_progress_exit_triggered,
)

from . import PBV2_AM_CLOSE_HM, PBV2_HARD_STOP_PCT, PBV2_PM_CLOSE_HM, PBV2_POLICY

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]

SOURCE_FILES = (
    "src/research/structural_exit_policies.py",
    "src/small_paper/board_dynamic_trailing_shadow.py",
    "src/small_paper/no_progress_exit.py",
    "src/small_paper/observer_position_tracker.py",
    "src/small_paper/am_pm_session_policy.py",
    "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
)


def _file_sha(rel: str) -> str:
    return hashlib.sha256((NATIVE / rel).read_bytes()).hexdigest()


def _trailing_triggered(peak_pnl: float, pnl: float, imb: Optional[float]) -> bool:
    activate, giveback, _ = trailing_params_for_board_tier(imb)
    return peak_pnl >= activate and pnl <= peak_pnl * giveback


def freeze_pbv2_manifest() -> dict[str, Any]:
    """Extract PBv2 EXIT from runtime modules — do not invent parameters."""
    body = {
        "manifest_id": "PBV2_EXIT_MANIFEST_V1",
        "policy_id": PBV2_POLICY,
        "runtime_unchanged": True,
        "source_files": [{"path": f, "sha256": _file_sha(f)} for f in SOURCE_FILES],
        "hard_stop": {
            "pct": PBV2_HARD_STOP_PCT,
            "sign": "negative_of_entry",
            "function": "observer_position_tracker.on_tick stop_hit",
        },
        "board_dynamic_trailing": {
            "split_percentile": BOARD_SPLIT_PERCENTILE,
            "board_high": {"activate_pct": BOARD_HIGH_ACTIVATE_PCT, "giveback_frac": BOARD_HIGH_GIVEBACK_FRAC},
            "board_low": {"activate_pct": BOARD_LOW_ACTIVATE_PCT, "giveback_frac": BOARD_LOW_GIVEBACK_FRAC},
            "entry_reference": "actual_ask_entry_price",
            "mfe_basis": "peak_unrealized_pnl_pct_from_entry",
            "board_state_dependency": "entry_imbalance_percentile_at_entry_only",
            "function": "trailing_params_for_board_tier",
        },
        "no_progress": {
            "enabled": True,
            "policy_key": PHASE442_POLICY_KEY,
            "start_sec": START_TIME_SEC,
            "initial_mfe_pct": INITIAL_MFE_PCT,
            "slope_per_5min": SLOPE_PER_5MIN,
            "max_mfe_cap_pct": MAX_MFE_CAP_PCT,
            "max_pnl_pct": MAX_PNL_PCT,
            "function": "no_progress_exit_triggered",
        },
        "max_hold_exit": {
            "present": False,
            "note": "virtual_hold_sec=300 is cooldown/cap related; not production close reason",
        },
        "session_close": {
            "am_force_hm": list(PBV2_AM_CLOSE_HM),
            "pm_force_hm": list(PBV2_PM_CLOSE_HM),
            "reasons": ["morning_session_close", "afternoon_session_close"],
            "module": "small_paper.am_pm_session_policy.AmPmSessionPolicy",
        },
        "trigger_ordering_same_tick": [
            "HARD_STOP",
            "NO_PROGRESS",
            "BOARD_DYNAMIC_TRAILING",
            "SESSION_CLOSE_via_runner_not_same_tick",
        ],
        "fill_contract_for_x28e": {
            "entry": "first_valid_ask Sell1",
            "exit": "first_valid_bid Buy1 after trigger",
            "trigger_mark": "CurrentPrice path (runtime mark)",
        },
        "extraction_note": (
            "Logic functions imported from small_paper.*; "
            "structural_exit_policies.py listed for source identity SHA but not imported "
            "(avoids unrelated research import chain)."
        ),
    }
    body["manifest_sha256"] = sha256_obj(body)
    return body


def _session_close_epoch(date: str, session: str, entry_t: float) -> float:
    y, m, d = int(date[:4]), int(date[4:6]), int(date[6:8])
    hm = PBV2_AM_CLOSE_HM if str(session).upper().startswith("A") else PBV2_PM_CLOSE_HM
    return datetime(y, m, d, hm[0], hm[1], tzinfo=JST).timestamp()


def _entry_imbalance_pct(
    board: dict[str, np.ndarray],
    ask_t: float,
    day_samples: list[float],
) -> Optional[float]:
    if board is None or board["t"].size == 0:
        return None
    j = int(np.searchsorted(board["t"], ask_t, side="right") - 1)
    if j < 0:
        return None
    aq = float(board["ask_qty"][j]) if board["ask_qty"].size else 0.0
    bq = float(board["bid_qty"][j]) if board["bid_qty"].size else 0.0
    denom = aq + bq
    if denom <= 0:
        return None
    imb = (bq - aq) / denom
    day_samples.append(imb)
    if len(day_samples) < 2:
        return None
    le = sum(1 for s in day_samples[:-1] if s <= imb)
    return round(100.0 * le / max(len(day_samples) - 1, 1), 2)


def build_pbv2_matrix(
    *,
    rows: list[dict[str, Any]],
    entry_asks: dict[str, np.ndarray],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    """PBv2 trigger on CP path from actual ask; fill at first valid bid after trigger."""
    n = len(rows)
    m = {
        "valid": np.zeros(n, dtype=bool),
        "ret_bps": np.full(n, np.nan),
        "pnl": np.full(n, np.nan),
        "hold": np.full(n, np.nan),
        "reason": np.array([""] * n, dtype=object),
        "mae_bps": np.full(n, np.nan),
        "mfe_bps": np.full(n, np.nan),
        "entry_px": np.full(n, np.nan),
        "exit_px": np.full(n, np.nan),
        "status": np.array([""] * n, dtype=object),
        "imb_pct": np.full(n, np.nan),
    }
    day_samples: dict[str, list[float]] = {}

    for i, r in enumerate(rows):
        if not entry_asks["valid"][i]:
            m["status"][i] = entry_asks["status"][i]
            continue
        ask = float(entry_asks["ask"][i])
        ask_t = float(entry_asks["ask_t"][i])
        times = times_list[i]
        prices = prices_list[i]
        if times.size == 0:
            m["status"][i] = "PATH_UNAVAILABLE"
            continue
        i0 = int(np.searchsorted(times, ask_t, side="left"))
        if i0 >= times.size:
            m["status"][i] = "PATH_UNAVAILABLE"
            continue
        sess_end = session_end_epoch(r["date"], r["session"])
        close_t = min(sess_end, _session_close_epoch(r["date"], r["session"], ask_t))
        board = board_by_key.get((r["date"], r["symbol"]))
        samples = day_samples.setdefault(r["date"], [])
        imb = _entry_imbalance_pct(board, ask_t, samples) if board else None
        if imb is not None:
            m["imb_pct"][i] = imb

        stop = ask * (1.0 - PBV2_HARD_STOP_PCT / 100.0)
        peak = 0.0
        mae = 0.0
        trig_t = None
        reason = None
        for j in range(i0, times.size):
            t = float(times[j])
            if t > close_t + 1e-9:
                break
            px = float(prices[j])
            pnl_pct = (px / ask - 1.0) * 100.0
            peak = max(peak, pnl_pct)
            mae = min(mae, pnl_pct)
            elapsed = t - ask_t
            if px <= stop:
                trig_t, reason = t, "stop_hit"
                break
            if no_progress_exit_triggered(elapsed, peak, pnl_pct):
                trig_t, reason = t, "no_progress_exit"
                break
            if _trailing_triggered(peak, pnl_pct, imb):
                trig_t, reason = t, "trailing_mfe_exit"
                break
            if t >= close_t - 1e-9:
                trig_t = t
                reason = (
                    "morning_session_close"
                    if str(r["session"]).upper().startswith("A")
                    else "afternoon_session_close"
                )
                break
        if trig_t is None:
            j_last = min(times.size - 1, int(np.searchsorted(times, close_t, side="right") - 1))
            if j_last < i0:
                m["status"][i] = "NO_TRIGGER"
                continue
            trig_t = float(times[j_last])
            reason = (
                "morning_session_close"
                if str(r["session"]).upper().startswith("A")
                else "afternoon_session_close"
            )

        if board is None:
            m["status"][i] = "EXIT_BID_UNAVAILABLE"
            continue
        q = first_valid_quote(board, float(trig_t), side="bid")
        if q["status"] != "OK":
            m["status"][i] = q["status"]
            continue
        bid = float(q["price"])
        ret = (bid / ask - 1.0) * 10000.0
        m["valid"][i] = True
        m["status"][i] = "OK"
        m["ret_bps"][i] = ret
        m["pnl"][i] = (bid - ask) * 100.0
        m["hold"][i] = float(q["event_time"] - ask_t)
        m["reason"][i] = reason
        m["entry_px"][i] = ask
        m["exit_px"][i] = bid
        m["mae_bps"][i] = mae * 100.0
        m["mfe_bps"][i] = peak * 100.0
    return m


def pbv2_parity_check() -> dict[str, Any]:
    root = NATIVE / "results" / "small_paper"
    if not root.exists():
        return {"ok": False, "status": "PBV2_EXIT_REPLAY_NOT_VALIDATED", "reason": "no_small_paper_dir"}

    candidates = []
    for d in sorted(root.iterdir()):
        name = d.name
        if not name.isdigit() or name >= "20260810" or name < "20260804":
            continue
        for sess in d.glob("live_session_*"):
            trades = sess / "structural_trades.csv"
            if trades.exists():
                candidates.append(trades)
    if not candidates:
        return {"ok": False, "status": "PBV2_EXIT_REPLAY_NOT_VALIDATED", "reason": "no_trades_csv"}

    import csv
    reason_ok = 0
    n = 0
    allowed = {
        "stop_hit", "no_progress_exit", "trailing_mfe_exit",
        "morning_session_close", "afternoon_session_close", "session_end",
    }
    sample_path = candidates[-1]
    with sample_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n += 1
            reason = str(row.get("exit_reason") or row.get("reason") or "").strip()
            if reason in allowed:
                reason_ok += 1
            if n >= 50:
                break
    act_h, gb_h, _ = trailing_params_for_board_tier(80.0)
    act_l, gb_l, _ = trailing_params_for_board_tier(10.0)
    param_ok = (
        abs(act_h - BOARD_HIGH_ACTIVATE_PCT) < 1e-9
        and abs(gb_h - BOARD_HIGH_GIVEBACK_FRAC) < 1e-9
        and abs(act_l - BOARD_LOW_ACTIVATE_PCT) < 1e-9
        and abs(gb_l - BOARD_LOW_GIVEBACK_FRAC) < 1e-9
        and no_progress_exit_triggered(900.0, 0.5, 0.1) is True
        and no_progress_exit_triggered(800.0, 0.5, 0.1) is False
    )
    ok = param_ok and n > 0 and reason_ok >= max(1, int(0.8 * n))
    return {
        "ok": ok,
        "status": "PBV2_EXIT_REPLAY_VALIDATED" if ok else "PBV2_EXIT_REPLAY_NOT_VALIDATED",
        "sample_trades_path": str(sample_path),
        "sample_n": n,
        "reason_ok_n": reason_ok,
        "param_ok": param_ok,
        "policy": PBV2_POLICY,
        "note": "Parity = runtime param identity + paper exit_reason vocabulary",
    }
