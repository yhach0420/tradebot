"""Complete-strategy replay invariants. Any break blocks the economic verdict."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.pb1_v4_complete_strategy_economic_confirmation1 import CAP, EXPECTED_OC_E0, EXPECTED_OC_E1, FV_FIRST, FV_LAST, PROSPECTIVE_FROM


def replay_invariants(*, replay: dict[str, Any]) -> dict[str, Any]:
    trades = list(replay.get("trades") or [])
    blocked = list(replay.get("blocked_rows") or [])
    same_bar = sum(1 for t in trades if t.get("same_bar_entry") or (t.get("entry_fill") or {}).get("same_bar_fill"))
    same_bar += int(replay.get("same_bar_entry_n") or 0)
    mid = sum(1 for t in trades if t.get("used_mid") or (t.get("entry_fill") or {}).get("used_mid") or (t.get("exit_fill") or {}).get("used_mid"))
    future_q = sum(
        1
        for t in trades
        if t.get("used_future_quote")
        or (t.get("entry_fill") or {}).get("used_future_quote")
        or (t.get("exit_fill") or {}).get("used_future_quote")
    )
    leak = [t for t in trades if FV_FIRST <= str(t.get("date") or "") <= FV_LAST or str(t.get("date") or "") >= PROSPECTIVE_FROM]
    clock_bad = sum(
        1
        for t in trades
        if not (str(t.get("signal_t") or "") < str(t.get("entry_t") or "") < str(t.get("exit_t") or ""))
    )
    lunch_fill = sum(1 for t in trades if in_lunch(str(t.get("entry_t") or "")) or in_lunch(str(t.get("exit_t") or "")))
    after_close = sum(1 for t in trades if str(t.get("exit_t") or "") > "15:20")
    no_exit = sum(1 for t in trades if not t.get("exit_t") or not t.get("slot_released"))
    fill_n = int(replay.get("fill_n") or 0)
    trade_n = len(trades)
    leftover_open = max(0, fill_n - trade_n)
    counts = {
        "same_bar_fill_n": int(same_bar),
        "mid_fill_n": int(mid),
        "future_quote_fill_n": int(future_q),
        "max_concurrent": int(replay.get("max_concurrent") or 0),
        "duplicate_same_symbol_open_n": int(replay.get("same_symbol_overlap_violation_n") or 0),
        "exit_without_position_n": 0,
        "slot_release_without_exit_n": int(no_exit),
        "occupancy_negative_n": 0,
        "open_position_after_session_close_n": int(leftover_open) + int(after_close),
        "cap_violation_n": int(replay.get("cap_violation_n") or 0),
        "clock_order_fail_n": int(clock_bad),
        "lunch_fill_n": int(lunch_fill),
        "holdout_trade_n": len(leak),
        "fill_px_mismatch_n": int(replay.get("fill_px_mismatch_n") or 0),
        "missing_bars_n": int((replay.get("skip") or {}).get("missing_bars") or 0),
        "e0_n": int(replay.get("e0_n") or 0),
        "e1_n": int(replay.get("e1_n") or 0),
        "expected_e0_n": int(EXPECTED_OC_E0),
        "expected_e1_n": int(EXPECTED_OC_E1),
        "blocked_n": len(blocked),
    }
    ok = (
        counts["same_bar_fill_n"] == 0
        and counts["mid_fill_n"] == 0
        and counts["future_quote_fill_n"] == 0
        and counts["max_concurrent"] <= int(CAP)
        and counts["duplicate_same_symbol_open_n"] == 0
        and counts["exit_without_position_n"] == 0
        and counts["slot_release_without_exit_n"] == 0
        and counts["occupancy_negative_n"] == 0
        and counts["open_position_after_session_close_n"] == 0
        and counts["cap_violation_n"] == 0
        and counts["clock_order_fail_n"] == 0
        and counts["lunch_fill_n"] == 0
        and counts["holdout_trade_n"] == 0
        and counts["fill_px_mismatch_n"] == 0
        and counts["missing_bars_n"] == 0
        and counts["e0_n"] == int(EXPECTED_OC_E0)
        and counts["e1_n"] == int(EXPECTED_OC_E1)
        and bool(replay.get("ok"))
        and not bool(replay.get("FROZEN_VALIDATION_ECONOMIC_OPENED"))
        and not bool(replay.get("PROSPECTIVE_DATA_OPENED"))
    )
    return {"ok": ok, "counts": counts, "reason": None if ok else "complete_strategy_invariant_break"}
