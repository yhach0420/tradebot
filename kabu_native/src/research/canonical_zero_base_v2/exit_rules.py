"""Strategy-specific EXIT candidates (X0 is control-only, not reused as v1 final)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.constants import COST_BPS, HARD_STOP_PCT, LOT
from research.canonical_zero_base_v2.exit_features import compute_exit_features_at, path_class
from research.canonical_zero_base_v2.loader import Tick


@dataclass
class ExitRule:
    exit_id: str
    strategy_id: str
    kind: str  # control_x0 | structural | structural_flow | structural_board | structural_volume | structural_flow_board | exhaustion | trailing
    persistence_events: int
    use_flow: bool
    use_board: bool
    use_volume: bool
    use_trailing: bool
    use_exhaustion: bool
    max_hold_sec: float
    is_control: bool = False


def strategy_exit_candidates(strategy_id: str) -> list[ExitRule]:
    """Distinct EXIT sets per strategy — not shared unqualified X0–X6 copy."""
    base = [
        ExitRule(f"{strategy_id}_XC0", strategy_id, "control_x0", 0, False, False, False, False, False, 1800, True),
    ]
    specs = {
        "Z1": [
            ("XS_struct", "structural", 2, False, False, False, False, False),
            ("XS_flow", "structural_flow", 2, True, False, False, False, False),
            ("XS_board", "structural_board", 2, False, True, False, False, False),
            ("XS_fb", "structural_flow_board", 3, True, True, False, False, False),
            ("XS_exh", "exhaustion", 2, True, False, False, False, True),
            ("XS_trail", "trailing", 1, False, False, False, True, False),
        ],
        "Z2": [
            ("XS_struct", "structural", 2, False, False, False, False, False),
            ("XS_vol", "structural_volume", 2, False, False, True, False, False),
            ("XS_flow", "structural_flow", 2, True, False, False, False, False),
            ("XS_fb", "structural_flow_board", 2, True, True, False, False, False),
            ("XS_exh", "exhaustion", 1, True, False, True, False, True),
            ("XS_trail", "trailing", 1, False, False, False, True, False),
        ],
        "Z3": [
            ("XS_struct", "structural", 2, False, False, False, False, False),
            ("XS_board", "structural_board", 3, False, True, False, False, False),
            ("XS_flow", "structural_flow", 2, True, False, False, False, False),
            ("XS_fb", "structural_flow_board", 3, True, True, False, False, False),
            ("XS_exh", "exhaustion", 2, True, True, False, False, True),
            ("XS_trail", "trailing", 1, False, True, False, True, False),
        ],
        "Z4": [
            ("XS_struct", "structural", 2, False, False, False, False, False),
            ("XS_vol", "structural_volume", 2, False, False, True, False, False),
            ("XS_flow", "structural_flow", 2, True, False, False, False, False),
            ("XS_fb", "structural_flow_board", 2, True, True, True, False, False),
            ("XS_exh", "exhaustion", 2, True, False, True, False, True),
            ("XS_trail", "trailing", 1, False, False, False, True, False),
        ],
    }
    for suf, kind, pers, uf, ub, uv, ut, ue in specs[strategy_id]:
        base.append(ExitRule(
            f"{strategy_id}_{suf}", strategy_id, kind, pers, uf, ub, uv, ut, ue, 1800, False,
        ))
    return base


def _session(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


def _pnl_yen(entry: float, exit_: float) -> float:
    raw = (exit_ - entry) * LOT
    cost = entry * LOT * COST_BPS / 10000.0 + exit_ * LOT * COST_BPS / 10000.0
    return raw - cost


def _structural_breach(strategy_id: str, bid: float, levels: dict[str, float], feats: dict) -> bool:
    if strategy_id == "Z1":
        return bool(feats.get("thesis_low_breach") == 1.0 or feats.get("reclaim_fail") == 1.0)
    if strategy_id == "Z2":
        return bool(feats.get("breakout_reentry") == 1.0)
    if strategy_id == "Z3":
        return bool(feats.get("wall_reform") == 1.0 and feats.get("bid_back") == 1.0)
    if strategy_id == "Z4":
        return bool(feats.get("range_reentry") == 1.0)
    return False


def simulate_exit(
    ticks: Sequence[Tick],
    entry_idx: int,
    entry_ask: float,
    *,
    strategy_id: str,
    exit_rule: ExitRule,
    levels: dict[str, float],
) -> dict[str, Any]:
    if entry_ask <= 0 or entry_idx >= len(ticks) - 1:
        return {"evaluable": False}
    t0 = ticks[entry_idx]
    stop_px = entry_ask * (1.0 - HARD_STOP_PCT / 100.0)
    mfe = mae = 0.0
    peak = 0.0
    warn_streak = 0
    inv_streak = 0
    state = "ENTERED"
    # strategy-specific trail params (not legacy 0.6/1.0 board tiers)
    act = {"Z1": 0.75, "Z2": 0.55, "Z3": 0.45, "Z4": 0.65}[strategy_id]
    gb = {"Z1": 0.42, "Z2": 0.48, "Z3": 0.38, "Z4": 0.50}[strategy_id]
    last_bid = entry_ask
    last_ts = t0.ts
    reason = "max_horizon"
    false_warning = 0
    true_invalidation = 0
    warning_seen = False

    for j in range(entry_idx + 1, min(len(ticks), entry_idx + 600)):
        t = ticks[j]
        bid = t.board.canonical_best_bid
        if bid is None or bid <= 0:
            continue
        hold = (t.ts - t0.ts).total_seconds()
        pnl_pct = (bid - entry_ask) / entry_ask * 100.0
        mfe = max(mfe, pnl_pct)
        mae = min(mae, pnl_pct)
        feats = compute_exit_features_at(ticks, entry_idx, j, entry_ask=entry_ask, levels=levels, strategy_id=strategy_id)

        if bid <= stop_px:
            return _pack(t.ts, bid, "hard_stop", mfe, mae, entry_ask, state, false_warning, true_invalidation)
        if _session(t.ts) != _session(t0.ts):
            return _pack(t.ts, bid, "session_close", mfe, mae, entry_ask, state, false_warning, true_invalidation)
        if hold > exit_rule.max_hold_sec:
            return _pack(t.ts, bid, "max_horizon", mfe, mae, entry_ask, state, false_warning, true_invalidation)

        if exit_rule.is_control or exit_rule.kind == "control_x0":
            last_bid, last_ts = bid, t.ts
            continue

        # WARNING: partial deterioration, structure still ok
        warn = False
        if exit_rule.use_flow and (feats.get("sell_flow_accel") or 0) > 2:
            warn = True
        if exit_rule.use_board and feats.get("bid_depletion") == 1.0:
            warn = True
        if feats.get("giveback", 0) > 0.35 and mfe > 0.4:
            warn = True
        struct = _structural_breach(strategy_id, bid, levels, feats)

        if warn and not struct:
            state = "WARNING"
            warning_seen = True
            warn_streak += 1
            # WARNING alone does not exit
            if pnl_pct > 0 and warn_streak >= 2:
                state = "RECOVERED"
                warn_streak = 0
                false_warning += 1
            last_bid, last_ts = bid, t.ts
            continue

        if struct:
            state = "INVALIDATED"
            inv_streak += 1
            confirm = True
            if exit_rule.use_flow and (feats.get("sell_flow_accel") or 0) <= 0:
                confirm = confirm if not exit_rule.use_flow else False
            if exit_rule.use_board and feats.get("ask_replenish") != 1.0 and strategy_id != "Z1":
                # board confirm preferred when requested
                if exit_rule.kind.endswith("board") or "flow_board" in exit_rule.kind:
                    confirm = confirm and (feats.get("bid_depletion") == 1.0 or feats.get("ask_replenish") == 1.0)
            if exit_rule.use_volume:
                confirm = confirm  # volume soft
            if inv_streak >= max(1, exit_rule.persistence_events) and confirm:
                true_invalidation += 1
                return _pack(t.ts, bid, f"invalidation_{exit_rule.kind}", mfe, mae, entry_ask, state, false_warning, true_invalidation)
            last_bid, last_ts = bid, t.ts
            continue
        else:
            inv_streak = 0
            if warning_seen and state == "WARNING":
                state = "RECOVERED"

        if exit_rule.use_exhaustion and mfe >= act and (feats.get("mfe_stagnation") == 1.0 or (feats.get("uptick_ratio_path") or 1) < 0.4):
            state = "EXHAUSTED"
            return _pack(t.ts, bid, "exhaustion", mfe, mae, entry_ask, state, false_warning, true_invalidation)

        if exit_rule.use_trailing:
            peak = max(peak, pnl_pct)
            if peak >= act and pnl_pct <= peak * (1 - gb):
                return _pack(t.ts, bid, "trailing", mfe, mae, entry_ask, "EXIT_DECIDED", false_warning, true_invalidation)

        state = "ACTIVE"
        last_bid, last_ts = bid, t.ts

    return _pack(last_ts, last_bid, reason, mfe, mae, entry_ask, state, false_warning, true_invalidation)


def _pack(ts, bid, reason, mfe, mae, entry, state, fw, ti) -> dict[str, Any]:
    return {
        "evaluable": True,
        "exit_time": ts,
        "exit_bid": float(bid),
        "exit_reason": reason,
        "mfe": mfe,
        "mae": mae,
        "pnl_5bps": _pnl_yen(entry, float(bid)),
        "state": state,
        "false_warning": fw,
        "true_invalidation": ti,
        "path_class": path_class(mfe, mae, (float(bid) - entry) / entry * 100.0, reason),
    }
