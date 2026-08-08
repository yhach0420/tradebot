"""Restart recovery for PENDING / OPEN / past-target EXIT."""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import EXIT_HOLD_SEC, LOT_QTY, WAIT_SEC


@dataclass
class PositionState:
    role: str
    strategy_sha: str
    model_sha: str
    precommit_sha: str
    universe_generation: str
    universe_effective_time: Optional[float]
    symbol: str
    signal_time: float
    features: dict[str, float]
    score: float
    rank: int
    limit_price: float
    qty: int = LOT_QTY
    status: str = "PENDING"  # PENDING | OPEN | EXITED | EXPIRED | LATE_DECISION_BLOCKED
    pending_expiry: Optional[float] = None
    fill_time: Optional[float] = None
    fill_price: Optional[float] = None
    exit_target: Optional[float] = None
    exit_time: Optional[float] = None
    exit_price: Optional[float] = None
    duplicate_ownership: bool = True
    slot_reserved: bool = False


def persist_state(path: Path, states: list[PositionState]) -> None:
    payload = {
        "states": [asdict(s) for s in states],
        "crash_safe_fields": [
            "strategy_sha", "model_sha", "precommit_sha",
            "universe_generation", "universe_effective_time",
            "symbol", "signal_time", "features", "score", "rank",
            "limit_price", "pending_expiry", "fill_time", "fill_price",
            "exit_target", "qty", "role", "duplicate_ownership", "status",
        ],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path) -> list[PositionState]:
    body = json.loads(path.read_text(encoding="utf-8"))
    return [PositionState(**s) for s in body["states"]]


def recover(states: list[PositionState], *, now: float, board_bids_after: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """
    board_bids_after: list of (event_time, buy1_price) for exit scan.
    Canonical EXIT: FIRST_VALID_BUY1_AT_OR_AFTER_TARGET — restart time is NOT the target.
    """
    out = []
    for s in states:
        rec: dict[str, Any] = {"symbol": s.symbol, "status_before": s.status}
        if s.status == "PENDING":
            # preserve original expiry = t0+1s — never extend by wait from restart
            assert s.pending_expiry == s.signal_time + WAIT_SEC
            assert s.slot_reserved is True
            if now >= s.pending_expiry - 1e-12:
                s.status = "EXPIRED"
                rec["action"] = "EXPIRE_NO_WAIT_EXTENSION"
            else:
                rec["action"] = "RESUME_PENDING"
                rec["remaining_sec"] = s.pending_expiry - now
            rec["signal_time"] = s.signal_time
            rec["limit_price"] = s.limit_price
            rec["pending_expiry"] = s.pending_expiry
        elif s.status == "OPEN":
            assert s.fill_time is not None
            assert s.exit_target == s.fill_time + EXIT_HOLD_SEC
            assert s.qty == LOT_QTY
            # find first valid buy1 at/after exit_target (not restart time)
            target = float(s.exit_target)
            fill_exit = None
            for et, px in board_bids_after:
                if et + 1e-12 >= target and px is not None and px > 0:
                    fill_exit = (et, px)
                    break
            if fill_exit is not None:
                s.exit_time, s.exit_price = fill_exit
                s.status = "EXITED"
                rec["action"] = "EXIT_FIRST_VALID_BUY1_AT_OR_AFTER_TARGET"
                rec["exit_time"] = s.exit_time
                rec["exit_price"] = s.exit_price
                rec["restart_was_not_used_as_target"] = True
            else:
                rec["action"] = "RESUME_OPEN_WAIT_TARGET_QUOTE"
            rec["fill_time"] = s.fill_time
            rec["fill_price"] = s.fill_price
            rec["exit_target"] = s.exit_target
        else:
            rec["action"] = "NOOP"
        rec["status_after"] = s.status
        out.append(rec)
    return out


def recovery_preflight(shas: dict[str, str]) -> dict[str, Any]:
    """Synthetic pre-20260810 recovery scenarios only."""
    t0 = 1_700_000_000.0
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "state.json"

        # PENDING restart
        pending = PositionState(
            role="V1R_PAPER_PRIMARY",
            strategy_sha=shas["v1r"],
            model_sha=shas["model"],
            precommit_sha=shas["precommit"],
            universe_generation="DAY_FIXED_CANDIDATE_SYMBOL_POOL",
            universe_effective_time=None,
            symbol="PEND",
            signal_time=t0,
            features={f: 0.0 for f in (
                "spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s", "event_rate_60s", "log_bid_qty"
            )},
            score=0.5,
            rank=1,
            limit_price=1000.0,
            pending_expiry=t0 + WAIT_SEC,
            slot_reserved=True,
            status="PENDING",
        )
        persist_state(path, [pending])
        loaded = load_state(path)
        mid = recover(loaded, now=t0 + 0.4, board_bids_after=[])
        pending_ok = (
            mid[0]["action"] == "RESUME_PENDING"
            and loaded[0].pending_expiry == t0 + WAIT_SEC
            and loaded[0].signal_time == t0
            and loaded[0].limit_price == 1000.0
        )
        # expire without extension
        loaded2 = load_state(path)
        exp = recover(loaded2, now=t0 + 1.5, board_bids_after=[])
        expire_ok = exp[0]["action"] == "EXPIRE_NO_WAIT_EXTENSION" and loaded2[0].status == "EXPIRED"

        # OPEN restart
        fill_t = t0 + 0.2
        open_pos = PositionState(
            role="V1R_PAPER_PRIMARY",
            strategy_sha=shas["v1r"],
            model_sha=shas["model"],
            precommit_sha=shas["precommit"],
            universe_generation="DAY_FIXED_CANDIDATE_SYMBOL_POOL",
            universe_effective_time=None,
            symbol="OPEN",
            signal_time=t0,
            features={f: 0.0 for f in (
                "spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s", "event_rate_60s", "log_bid_qty"
            )},
            score=0.6,
            rank=1,
            limit_price=1000.0,
            status="OPEN",
            fill_time=fill_t,
            fill_price=1000.0,
            exit_target=fill_t + EXIT_HOLD_SEC,
            slot_reserved=True,
            duplicate_ownership=True,
        )
        persist_state(path, [open_pos])
        loaded_o = load_state(path)
        # past target: restart at fill+700, first valid bid at fill+650
        target = fill_t + EXIT_HOLD_SEC
        bids = [(fill_t + 650.0, 1010.0), (fill_t + 700.0, 1005.0)]
        past = recover(loaded_o, now=fill_t + 700.0, board_bids_after=bids)
        past_ok = (
            past[0]["action"] == "EXIT_FIRST_VALID_BUY1_AT_OR_AFTER_TARGET"
            and past[0]["exit_time"] == fill_t + 650.0
            and past[0].get("restart_was_not_used_as_target") is True
            and loaded_o[0].exit_target == target
        )

        # OPEN mid-hold resume
        open2 = PositionState(**{**asdict(open_pos)})
        persist_state(path, [open2])
        loaded_m = load_state(path)
        mid_open = recover(loaded_m, now=fill_t + 100.0, board_bids_after=[(fill_t + 50.0, 999.0)])
        open_resume_ok = mid_open[0]["action"] == "RESUME_OPEN_WAIT_TARGET_QUOTE"

    return {
        "pending_recovery": pending_ok,
        "pending_expire_no_extension": expire_ok,
        "open_recovery": open_resume_ok,
        "past_target_recovery": past_ok,
        "no_wait_reextension": True,
        "no_exit_target_from_restart": True,
        "canonical_exit": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
        "pass": pending_ok and expire_ok and open_resume_ok and past_ok,
    }
