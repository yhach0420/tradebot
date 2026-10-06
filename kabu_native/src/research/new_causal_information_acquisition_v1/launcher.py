"""Isolated NEW_INFO capture launcher. No sendorder. No Paper/Ingress default change."""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1 import WINDOW_END_HM, WINDOW_START_HM

JST = ZoneInfo("Asia/Tokyo")
LAUNCHER_ID = "FUTURES_MARKET_CONTEXT_CAPTURE_LAUNCHER_V1"
SENDORDER_CALL_N = 0
SUBMIT_N = 0
CANCEL_N = 0
LIVE_ORDER_N = 0


def window_bounds() -> tuple[time, time]:
    return time(*WINDOW_START_HM), time(*WINDOW_END_HM)


def in_acquisition_window(now: Optional[datetime] = None) -> bool:
    dt = now or datetime.now(JST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    t = dt.astimezone(JST).time()
    start, end = window_bounds()
    return start <= t <= end


def launcher_has_order_api() -> bool:
    src = Path(__file__).read_text(encoding="utf-8")
    needles = ("send" + "order(", "send" + "_order(", "/" + "sendorder")
    return any(n in src for n in needles)


def live_order_counts() -> dict[str, int]:
    return {
        "submit": int(SUBMIT_N),
        "cancel": int(CANCEL_N),
        "live": int(LIVE_ORDER_N),
        "sendorder_call_n": int(SENDORDER_CALL_N),
    }


def dry_preflight_live_blockers(*, native_root: Any, trading_date: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """Read-only. Does not PUT /register, POST /token, or unregister."""
    from research.new_causal_information_acquisition_v1.exclusive import probe_exclusive
    from research.new_causal_information_acquisition_v1.spec import refuse_legacy_futures_backfill, standard_config_unchanged
    from research.new_causal_information_acquisition_v1.universe import build_new_info_universe, same_day_am_csv

    refuse_legacy_futures_backfill(trading_date)
    exclusive = probe_exclusive(native_root=native_root, trading_date=trading_date)
    csv_path = same_day_am_csv(native_root, trading_date)
    universe = None
    universe_error = ""
    try:
        universe = build_new_info_universe(csv_path)
    except Exception as exc:
        universe_error = f"{type(exc).__name__}:{exc}"
    window_ok = in_acquisition_window(now)
    std = standard_config_unchanged()
    blockers = []
    if not exclusive.get("ok"):
        blockers.append("EXCLUSIVE_COMPETITOR")
    if universe is None:
        blockers.append("SAME_DAY_AM_CSV")
    if not window_ok:
        blockers.append("OUTSIDE_0755_1130_JST")
    if not std.get("ok"):
        blockers.append("STANDARD_CONFIG_DRIFT")
    if launcher_has_order_api():
        blockers.append("SENDORDER_IMPORT")
    return {
        "ok": not blockers,
        "blockers": blockers,
        "window_ok": window_ok,
        "exclusive": exclusive,
        "universe": universe,
        "universe_error": universe_error,
        "standard_config": std,
        "orders": live_order_counts(),
        "would_register": False,
        "would_unregister_competitor": False,
        "launcher_id": LAUNCHER_ID,
    }


def run_live_readonly(*, native_root: Any, trading_date: str, token: str, rest: Any, push: Any) -> dict[str, Any]:
    """Live read-only acquisition. Requires exclusive + window. No orders.

    Token must already be issued by the sole Station client. This function does not POST /token.
    """
    from research.new_causal_information_acquisition_v1.exclusive import require_exclusive
    from research.new_causal_information_acquisition_v1.futures_resolve import resolve_both
    from research.new_causal_information_acquisition_v1.register_plan import apply_registration_if_exclusive, build_plan
    from research.new_causal_information_acquisition_v1.spec import refuse_legacy_futures_backfill
    from research.new_causal_information_acquisition_v1.universe import build_new_info_universe, same_day_am_csv
    from research.new_causal_information_acquisition_v1.writer import ContextCaptureSession, day_layout

    if not in_acquisition_window():
        raise RuntimeError("NEW_INFO live capture is 07:55-11:30 JST only")
    refuse_legacy_futures_backfill(trading_date)
    require_exclusive(native_root=native_root, trading_date=trading_date)
    uni = build_new_info_universe(same_day_am_csv(native_root, trading_date))
    resolved = resolve_both(rest, token=token)
    plan = build_plan(stock_symbols=uni["stock_symbols"], contracts=resolved["contracts"])
    layout = day_layout(trading_date, native_root=native_root)
    applied = apply_registration_if_exclusive(
        push,
        plan,
        native_root=native_root,
        trading_date=trading_date,
        isolated_state_root=layout["station_state"],
    )
    session = ContextCaptureSession(
        native_root=native_root,
        trading_date=trading_date,
        session_id=f"new_info_{trading_date}",
    )
    session.bind_contracts(resolved["by_code"])
    session.write_manifest(
        {
            "mode": "NEW_INFO_DEV_CONSTRUCTION_ONLY",
            "trading_date": trading_date,
            "universe": {k: uni[k] for k in ("core_n", "dynamic_n", "stock_n", "core_symbols", "dynamic38_symbols", "dropped_dynamic_tail")},
            "futures": resolved["contracts"],
            "registration": {
                "total_n": plan["total_n"],
                "specs": plan["specs"],
                "applied": applied.get("ok"),
            },
            "window": "07:55-11:30 JST",
            "orders": live_order_counts(),
        }
    )
    return {
        "ok": bool(applied.get("ok")),
        "session": session,
        "plan": plan,
        "resolved": resolved,
        "universe": uni,
        "registration": applied,
        "orders": live_order_counts(),
    }


assert SENDORDER_CALL_N == 0
assert SUBMIT_N == CANCEL_N == LIVE_ORDER_N == 0
assert not launcher_has_order_api()
