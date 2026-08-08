"""Per-candidate CAP5 portfolio replay on a window bundle."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from .economics import net_pnl_yen
from .evaluation_plan import cap5_tie_break_key
from .exit_eval import evaluate_from_open, structural_entry_ok
from .setups import run_setup_machine

JST = ZoneInfo("Asia/Tokyo")
CAP = 5


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=JST).isoformat()


def _opens_for_symbol(
    bundle: dict[str, Any],
    sym: str,
    *,
    setup: str,
    confirmation: str,
    regime_mode: str,
) -> list[dict[str, Any]]:
    feats = bundle["sym_feats"][sym]
    due = bundle["due"][sym]
    decision_ok = bundle["decision_ok"][sym]
    regimes = (
        bundle["regimes_strict"][sym] if regime_mode == "strict"
        else bundle["regimes_std"][sym]
    )
    cls = bundle["symbol_classes"][sym]
    decs = run_setup_machine(
        setup, feats, regimes, bundle["entry_allowed"],
        confirmation=confirmation, symbol=sym, symbol_class=cls,
        vwap_available=False,  # R3 freeze: VWAP gate inactive
        decision_ok=decision_ok, due=due,
    )
    opens = []
    for d in decs:
        if d.state != "OPEN" or not d.frozen:
            continue
        g = d.grid_idx
        ask = float(feats["ask"][g])
        if not np.isfinite(ask) or ask <= 0:
            continue
        stop = float(d.frozen["stop_reference"])
        ok, r, reason = structural_entry_ok(ask, stop)
        if not ok:
            opens.append({
                "kind": "REJECTED_ENTRY",
                "symbol": sym,
                "grid": g,
                "reason": reason,
                "entry_ask": ask,
                "stop_level": stop,
                "R": r,
                "frozen": d.frozen,
                "episode_id": d.episode_id,
            })
            continue
        trigger_g = int(d.frozen["trigger_grid"])
        opens.append({
            "kind": "OPEN_CANDIDATE",
            "symbol": sym,
            "grid": g,
            "trigger_ts": float(bundle["grid"][trigger_g]),
            "decision_grid": g,
            "trigger_grid": trigger_g,
            "entry_ask": ask,
            "stop_level": stop,
            "R": r,
            "frozen": d.frozen,
            "episode_id": d.episode_id,
            "setup": setup,
        })
    return opens


def replay_candidate_on_bundle(
    bundle: dict[str, Any],
    cand: dict[str, Any],
) -> dict[str, Any]:
    """Independent CAP5 replay for one candidate on one window."""
    if bundle.get("empty"):
        return {
            "trades": [], "cap_blocked": [], "rejected": [],
            "counters": {"completed": 0, "open": 0, "orphan": 0, "censored": 0,
                         "invalid_source": 0, "cap_blocked": 0, "rejected_entry": 0},
        }
    setup = cand["setup"]
    confirmation = cand["confirmation"]
    regime_mode = cand["regime_mode"]
    exit_id = "EXIT_B" if "EXIT_B" in cand["strategy_id"] else "EXIT_A"
    day = bundle["day"]
    am_pm = bundle["am_pm"]
    grid = bundle["grid"]

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for sym in bundle["universe"]:
        for row in _opens_for_symbol(
            bundle, sym, setup=setup, confirmation=confirmation, regime_mode=regime_mode,
        ):
            if row["kind"] == "REJECTED_ENTRY":
                rejected.append(row)
            else:
                candidates.append(row)

    # Sort all OPEN candidates by CAP5 tie-break; process in time order of decision grid
    candidates.sort(key=lambda r: (r["decision_grid"],) + cap5_tie_break_key(r))

    open_positions: list[dict[str, Any]] = []  # active slots
    trades: list[dict[str, Any]] = []
    cap_blocked: list[dict[str, Any]] = []
    counters = {
        "completed": 0, "open": 0, "orphan": 0, "censored": 0,
        "invalid_source": 0, "cap_blocked": 0, "rejected_entry": len(rejected),
    }

    # Group opens by decision_grid for same-grid CAP competition
    by_grid: dict[int, list[dict[str, Any]]] = {}
    for row in candidates:
        by_grid.setdefault(row["decision_grid"], []).append(row)

    n = bundle["n_grid"]
    for g in range(n):
        # EXIT at this grid frees CAP before same-grid ENTRY (EXIT priority).
        open_positions = [p for p in open_positions if p["exit_g"] > g]

        # Accept new opens at this grid under CAP
        rows = by_grid.get(g) or []
        rows = sorted(rows, key=cap5_tie_break_key)
        for row in rows:
            if len(open_positions) >= CAP:
                blocked = {
                    **{k: row[k] for k in (
                        "symbol", "grid", "trigger_ts", "decision_grid",
                        "entry_ask", "stop_level", "episode_id", "setup",
                    )},
                    "day": day, "am_pm": am_pm,
                    "strategy_id": cand["strategy_id"],
                    "reason": "CAP5_FULL",
                    "open_slots": len(open_positions),
                }
                cap_blocked.append(blocked)
                counters["cap_blocked"] += 1
                continue
            # Accept ENTRY and immediately evaluate EXIT path to completion
            feats = bundle["sym_feats"][row["symbol"]]
            cls = bundle["symbol_classes"][row["symbol"]]
            ex = evaluate_from_open(
                setup=setup, exit_id=exit_id,
                open_g=row["grid"], trigger_g=row["trigger_grid"],
                entry_ask=row["entry_ask"], stop_level=row["stop_level"],
                frozen=row["frozen"], feats=feats, grid=grid,
                symbol_class=cls, vwap_in_use=False,
            )
            if ex["status"] == "CENSORED":
                counters["censored"] += 1
                # Do NOT zero PnL; store as censored row (integrity will BLOCK)
                trades.append({
                    "strategy_id": cand["strategy_id"],
                    "day": day, "am_pm": am_pm, "symbol": row["symbol"],
                    "status": "CENSORED",
                    "exit_reason": ex["exit_reason"],
                    "entry_time": _iso(float(grid[row["grid"]])),
                    "exit_time": None,
                    "entry_ask": row["entry_ask"],
                    "exit_bid": None,
                    "net_pnl_yen_100": None,  # never zeroed
                    "mfe_yen": ex.get("mfe_yen"),
                    "mae_yen": ex.get("mae_yen"),
                    "episode_id": row["episode_id"],
                })
                continue
            if ex["status"] != "COMPLETED" or ex.get("exit_bid") is None:
                counters["orphan"] += 1
                trades.append({
                    "strategy_id": cand["strategy_id"],
                    "day": day, "am_pm": am_pm, "symbol": row["symbol"],
                    "status": "ORPHAN",
                    "exit_reason": ex.get("exit_reason"),
                    "entry_time": _iso(float(grid[row["grid"]])),
                    "exit_time": None,
                    "entry_ask": row["entry_ask"],
                    "exit_bid": None,
                    "net_pnl_yen_100": None,
                    "episode_id": row["episode_id"],
                })
                continue
            econ = net_pnl_yen(row["entry_ask"], float(ex["exit_bid"]))
            trade = {
                "strategy_id": cand["strategy_id"],
                "day": day, "am_pm": am_pm, "symbol": row["symbol"],
                "status": "COMPLETED",
                "exit_reason": ex["exit_reason"],
                "entry_time": _iso(float(grid[row["grid"]])),
                "exit_time": _iso(float(grid[int(ex["exit_g"])])),
                "entry_ask": row["entry_ask"],
                "exit_bid": float(ex["exit_bid"]),
                "stop_level": row["stop_level"],
                "R": row["R"],
                "gross_pnl_yen_100": econ["gross_pnl_yen_100"],
                "cost_yen_100": econ["cost_yen_100"],
                "net_pnl_yen_100": econ["net_pnl_yen_100"],
                "mfe_yen": ex.get("mfe_yen"),
                "mae_yen": ex.get("mae_yen"),
                "elapsed_sec": ex.get("elapsed_sec"),
                "trailing_armed": ex.get("trailing_armed"),
                "episode_id": row["episode_id"],
                "setup": setup,
                "exit_id": exit_id,
                "entry_grid": row["grid"],
                "exit_grid": int(ex["exit_g"]),
                "trigger_ts": row["trigger_ts"],
                "decision_grid": row["decision_grid"],
            }
            trades.append(trade)
            counters["completed"] += 1
            # CAP occupancy: hold slot from entry_grid to exit_grid inclusive
            open_positions.append({
                "symbol": row["symbol"],
                "entry_g": row["grid"],
                "exit_g": int(ex["exit_g"]),
            })

    # Any position still open at end (should be empty if exits always complete)
    for p in open_positions:
        counters["open"] += 1

    return {
        "trades": trades,
        "cap_blocked": cap_blocked,
        "rejected": rejected,
        "counters": counters,
    }


def replay_all_candidates(
    bundle: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        c["strategy_id"]: replay_candidate_on_bundle(bundle, c)
        for c in candidates if c.get("enabled", True)
    }
