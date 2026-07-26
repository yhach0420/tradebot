"""Offline recompute of entry-block Shadow PnL including recovery_forced_close."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y")


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(y for y in yens if y > 0)
    gl = abs(sum(y for y in yens if y < 0))
    if gl > 0:
        return round(gp / gl, 4)
    if gp > 0:
        return float("inf")
    return None


def load_events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def index_accept_exit(events: Sequence[Mapping[str, Any]]) -> tuple[dict[str, dict], dict[str, dict]]:
    accepts: dict[str, dict] = {}
    exits: dict[str, dict] = {}
    for e in events:
        pid = str(e.get("position_id") or "")
        if not pid:
            continue
        if e.get("event_type") == "accepted":
            accepts[pid] = dict(e)
        elif e.get("event_type") == "observer_exit":
            exits[pid] = dict(e)
    return accepts, exits


def recompute_entry_block_shadow(
    events: Sequence[Mapping[str, Any]],
    *,
    candidate_keys: Sequence[str],
    block_keys: Sequence[str],
    shadow_id: str,
) -> dict[str, Any]:
    """Join accept→exit by position_id; recovery exits included."""
    accepts, exits = index_accept_exit(events)
    target = 0
    block_n = 0
    kept = 0
    joined = 0
    miss = 0
    recovery_joined = 0
    runtime_pnls: list[float] = []
    shadow_pnls: list[float] = []
    open_pids: list[str] = []
    blocked_winners = blocked_losers = 0

    for pid, acc in accepts.items():
        is_cand = any(_bool(acc.get(k)) for k in candidate_keys)
        # if no candidate key, treat block key alone as candidate for pullback-style
        if not is_cand and candidate_keys:
            # pullback: blocked flag implies candidate
            if any(k for k in block_keys if k in acc):
                is_cand = True
            else:
                continue
        if not is_cand and not any(_bool(acc.get(k)) for k in block_keys):
            # still skip non-candidates when candidate_keys empty means all accepts?
            if candidate_keys:
                continue
        blocked = any(_bool(acc.get(k)) for k in block_keys)
        # also read from exit if accept missing
        ex = exits.get(pid)
        if ex is not None:
            if not is_cand:
                is_cand = any(_bool(ex.get(k)) for k in candidate_keys) or any(
                    _bool(ex.get(k)) for k in block_keys
                )
            if not blocked:
                blocked = any(_bool(ex.get(k)) for k in block_keys)
        if not is_cand:
            continue
        target += 1
        if blocked:
            block_n += 1
        else:
            kept += 1
        if ex is None:
            miss += 1
            open_pids.append(pid)
            continue
        joined += 1
        if str(ex.get("exit_reason") or "") == "recovery_forced_close":
            recovery_joined += 1
        actual = _f(ex.get("actual_pnl_yen_100"))
        if actual is None:
            actual = _f(ex.get("pnl_yen_100"))
        if actual is None:
            ep = _f(ex.get("entry_price") or acc.get("entry_price"))
            xp = _f(ex.get("exit_price") or ex.get("current_price"))
            if ep is not None and xp is not None:
                from replay.pnl_yen import compute_pnl_yen_100

                actual = round(compute_pnl_yen_100(ep, xp), 2)
        if actual is None:
            miss += 1
            continue
        shadow = 0.0 if blocked else float(actual)
        runtime_pnls.append(float(actual))
        shadow_pnls.append(float(shadow))
        if blocked and actual > 0:
            blocked_winners += 1
        if blocked and actual < 0:
            blocked_losers += 1

    deltas = [s - r for s, r in zip(shadow_pnls, runtime_pnls)]
    return {
        "shadow_id": shadow_id,
        "target_count": target,
        "block_count": block_n,
        "kept_count": kept,
        "completed": joined,
        "open": len(open_pids),
        "open_position_ids": open_pids,
        "exit_join_count": joined,
        "exit_join_miss_count": miss,
        "recovery_join_count": recovery_joined,
        "runtime_pnl": round(sum(runtime_pnls), 2),
        "shadow_pnl": round(sum(shadow_pnls), 2),
        "delta_pnl": round(sum(deltas), 2),
        "runtime_pf": _pf(runtime_pnls),
        "shadow_pf": _pf(shadow_pnls),
        "win_count": sum(1 for y in shadow_pnls if y > 0),
        "loss_count": sum(1 for y in shadow_pnls if y < 0),
        "flat_count": sum(1 for y in shadow_pnls if y == 0),
        "blocked_winners": blocked_winners,
        "blocked_losers": blocked_losers,
        "join_success_rate": round(joined / target, 4) if target else None,
        "pnl_applicable": True,
        "status": "RUNNING_PNL_COMPLETE" if miss == 0 and target > 0 else (
            "ENABLED_NO_EVENTS" if target == 0 else "RUNNING_PNL_INCOMPLETE"
        ),
    }


def recompute_flat_weak(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return recompute_entry_block_shadow(
        events,
        candidate_keys=("flat_weak_range_shadow_candidate",),
        block_keys=("flat_weak_range_shadow_block",),
        shadow_id="flat_weak_range_shadow",
    )


def recompute_pullback_misread(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """All accepted trades are pullback-misread evaluation targets (matches runtime summary)."""
    accepts, exits = index_accept_exit(events)
    target = 0
    block_n = 0
    kept = 0
    joined = 0
    miss = 0
    recovery_joined = 0
    runtime_pnls: list[float] = []
    shadow_pnls: list[float] = []
    open_pids: list[str] = []
    blocked_winners = blocked_losers = 0
    block_keys = (
        "pullback_misread_guard_shadow_blocked",
        "pullback_misread_dynamic40_guard_blocked",
    )
    for pid, acc in accepts.items():
        target += 1
        blocked = any(_bool(acc.get(k)) for k in block_keys)
        ex = exits.get(pid)
        if ex is not None and not blocked:
            blocked = any(_bool(ex.get(k)) for k in block_keys)
        if blocked:
            block_n += 1
        else:
            kept += 1
        if ex is None:
            miss += 1
            open_pids.append(pid)
            continue
        joined += 1
        if str(ex.get("exit_reason") or "") == "recovery_forced_close":
            recovery_joined += 1
        actual = _f(ex.get("actual_pnl_yen_100"))
        if actual is None:
            actual = _f(ex.get("pnl_yen_100"))
        if actual is None:
            ep = _f(ex.get("entry_price") or acc.get("entry_price"))
            xp = _f(ex.get("exit_price") or ex.get("current_price"))
            if ep is not None and xp is not None:
                from replay.pnl_yen import compute_pnl_yen_100

                actual = round(compute_pnl_yen_100(ep, xp), 2)
        if actual is None:
            miss += 1
            continue
        shadow = 0.0 if blocked else float(actual)
        runtime_pnls.append(float(actual))
        shadow_pnls.append(float(shadow))
        if blocked and actual > 0:
            blocked_winners += 1
        if blocked and actual < 0:
            blocked_losers += 1
    deltas = [s - r for s, r in zip(shadow_pnls, runtime_pnls)]
    return {
        "shadow_id": "pullback_misread_guard_shadow",
        "target_count": target,
        "block_count": block_n,
        "kept_count": kept,
        "completed": joined,
        "open": len(open_pids),
        "open_position_ids": open_pids,
        "exit_join_count": joined,
        "exit_join_miss_count": miss,
        "recovery_join_count": recovery_joined,
        "runtime_pnl": round(sum(runtime_pnls), 2),
        "shadow_pnl": round(sum(shadow_pnls), 2),
        "delta_pnl": round(sum(deltas), 2),
        "runtime_pf": _pf(runtime_pnls),
        "shadow_pf": _pf(shadow_pnls),
        "win_count": sum(1 for y in shadow_pnls if y > 0),
        "loss_count": sum(1 for y in shadow_pnls if y < 0),
        "flat_count": sum(1 for y in shadow_pnls if y == 0),
        "blocked_winners": blocked_winners,
        "blocked_losers": blocked_losers,
        "join_success_rate": round(joined / target, 4) if target else None,
        "pnl_applicable": True,
        "status": "RUNNING_PNL_COMPLETE" if miss == 0 and target > 0 else "RUNNING_PNL_INCOMPLETE",
    }


def recompute_board_dynamic(
    events: Sequence[Mapping[str, Any]],
    *,
    hard_stop_pct: float = 1.20,
    events_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Full Board Dynamic Shadow PnL including Recovery fallback."""
    from replay.pnl_yen import compute_pnl_yen_100
    from small_paper.board_dynamic_trailing_shadow import simulate_legacy_fixed_trailing_exit
    from small_paper.cost_aware_price_path import parse_ts

    accepts, exits = index_accept_exit(events)
    trade_keys: set[tuple[str, str]] = set()
    for pid, acc in accepts.items():
        sym = str(acc.get("symbol") or "")
        ent = str(acc.get("entry_time") or "")
        if sym and ent:
            trade_keys.add((sym, ent))

    tick_paths: dict[tuple[str, str], list] = {}
    if events_path is not None and Path(events_path).is_file():
        csv_path = Path(events_path)
        if csv_path.suffix == ".jsonl":
            alt = csv_path.with_suffix(".csv")
            if alt.is_file():
                csv_path = alt
        try:
            from small_paper.high_mfe_stophit_exit_recovery_shadow import _build_tick_paths

            tick_paths = _build_tick_paths(
                csv_path if csv_path.suffix == ".csv" else events_path, trade_keys
            )
        except Exception:
            tick_paths = {}

    if not tick_paths and events:
        active: dict[str, tuple[str, str]] = {}
        entry_px: dict[tuple[str, str], float] = {}
        paths: dict[tuple[str, str], list[dict]] = {k: [] for k in trade_keys}
        for e in events:
            sym = str(e.get("symbol") or "")
            et = e.get("event_type")
            if et == "accepted":
                key = (sym, str(e.get("entry_time") or ""))
                if key in trade_keys:
                    ep = _f(e.get("entry_price")) or 0.0
                    entry_px[key] = ep
                    active[sym] = key
                    ts = parse_ts(e.get("entry_time") or e.get("event_time"))
                    paths[key].append(
                        {"ts_epoch": ts.timestamp() if ts else 0.0, "price": ep, "pnl_pct": 0.0}
                    )
            elif et in ("candidate", "observer_hold", "observer_take") and sym in active:
                key = active[sym]
                ep = entry_px.get(key, 0.0)
                px = _f(e.get("current_price"))
                if ep > 0 and px and px > 0:
                    ts = parse_ts(e.get("event_time") or e.get("timestamp"))
                    paths[key].append(
                        {
                            "ts_epoch": ts.timestamp() if ts else 0.0,
                            "price": px,
                            "pnl_pct": (px - ep) / ep * 100.0,
                        }
                    )
            elif et == "observer_exit" and sym in active:
                active.pop(sym, None)
        tick_paths = paths  # type: ignore[assignment]

    runtime_pnls: list[float] = []
    shadow_pnls: list[float] = []
    deltas: list[float] = []
    exit_n = 0
    recovery_n = 0
    recovery_missing = 0
    recovery_fallback_n = 0
    shadow_triggered_n = 0

    for pid, ex in exits.items():
        exit_n += 1
        acc = accepts.get(pid) or {}
        is_rec = str(ex.get("exit_reason") or "") == "recovery_forced_close"
        if is_rec:
            recovery_n += 1

        entry = _f(ex.get("entry_price") or acc.get("entry_price")) or 0.0
        actual_exit = _f(ex.get("exit_price") or ex.get("current_price")) or 0.0
        actual_yen = _f(ex.get("actual_pnl_yen_100"))
        if actual_yen is None:
            actual_yen = _f(ex.get("pnl_yen_100"))
        if actual_yen is None and entry > 0 and actual_exit > 0:
            actual_yen = round(compute_pnl_yen_100(entry, actual_exit), 2)
        if actual_yen is None:
            actual_yen = 0.0

        existing_sx = _f(ex.get("shadow_exit_price"))
        existing_reason = str(ex.get("shadow_exit_reason") or "").strip()
        shadow_triggered = bool(
            existing_sx and existing_sx > 0 and existing_reason not in ("", "no_ticks")
        )

        sym = str(ex.get("symbol") or acc.get("symbol") or "")
        ent = str(acc.get("entry_time") or ex.get("entry_time") or "")
        key = (sym, ent)
        ticks_raw = tick_paths.get(key) or []
        rich = []
        for t in ticks_raw:
            if hasattr(t, "ts_epoch"):
                rich.append({"ts_epoch": t.ts_epoch, "price": t.price, "pnl_pct": t.pnl_pct})
            else:
                rich.append(dict(t))

        exit_ts = parse_ts(ex.get("exit_time") or ex.get("event_time"))
        cutoff = exit_ts.timestamp() if exit_ts else None

        shadow_exit_price = existing_sx
        shadow_exit_time = ex.get("shadow_exit_time") or ""
        shadow_exit_reason = existing_reason
        recovery_fallback_used = False

        if not shadow_triggered and rich and entry > 0:
            sim = simulate_legacy_fixed_trailing_exit(
                rich,
                entry_price=entry,
                hard_stop_pct=hard_stop_pct,
                cutoff_ts=cutoff,
            )
            sim_reason = str(sim.get("shadow_exit_reason") or "")
            sim_px = _f(sim.get("shadow_exit_price"))
            sim_time = str(sim.get("shadow_exit_time") or "")
            if sim_reason in ("stop_hit", "trailing_mfe_exit") and sim_px and sim_px > 0:
                shadow_triggered = True
                shadow_exit_price = sim_px
                shadow_exit_time = sim_time
                shadow_exit_reason = sim_reason
            elif is_rec:
                shadow_exit_price = actual_exit
                shadow_exit_time = exit_ts.isoformat() if exit_ts else ""
                shadow_exit_reason = "recovery_fallback"
                recovery_fallback_used = True
            else:
                shadow_exit_price = sim_px or actual_exit
                shadow_exit_time = sim_time or (exit_ts.isoformat() if exit_ts else "")
                shadow_exit_reason = sim_reason or "session_close"
                shadow_triggered = True
        elif not shadow_triggered and is_rec:
            shadow_exit_price = actual_exit
            shadow_exit_time = exit_ts.isoformat() if exit_ts else ""
            shadow_exit_reason = "recovery_fallback"
            recovery_fallback_used = True
        elif not shadow_triggered:
            shadow_exit_price = actual_exit
            shadow_exit_time = exit_ts.isoformat() if exit_ts else ""
            shadow_exit_reason = "actual_exit_fallback"
            shadow_triggered = True

        if shadow_exit_price is None or shadow_exit_price <= 0:
            if is_rec:
                recovery_missing += 1
            shadow_exit_price = actual_exit or entry
            recovery_fallback_used = True
            shadow_exit_reason = shadow_exit_reason or "recovery_fallback"

        if recovery_fallback_used:
            recovery_fallback_n += 1
            shadow_yen = float(actual_yen)
            delta = 0.0
        else:
            shadow_yen = (
                round(compute_pnl_yen_100(entry, float(shadow_exit_price)), 2)
                if entry > 0
                else float(actual_yen)
            )
            delta = round(shadow_yen - float(actual_yen), 2)
            shadow_triggered_n += 1

        runtime_pnls.append(float(actual_yen))
        shadow_pnls.append(float(shadow_yen))
        deltas.append(delta)

        ex["shadow_exit_triggered"] = bool(shadow_triggered and not recovery_fallback_used)
        ex["shadow_exit_time"] = shadow_exit_time
        ex["shadow_exit_price"] = shadow_exit_price
        ex["shadow_exit_reason"] = shadow_exit_reason
        ex["recovery_fallback_used"] = recovery_fallback_used
        ex["shadow_pnl_yen_100"] = shadow_yen
        ex["actual_vs_shadow_delta_yen"] = delta

    open_n = max(0, len(accepts) - len(exits))
    complete = recovery_missing == 0 and open_n == 0 and exit_n > 0
    return {
        "shadow_id": "board_dynamic_trailing_shadow",
        "exit_count": exit_n,
        "completed": exit_n,
        "open": open_n,
        "recovery_join_count": recovery_n,
        "recovery_missing_shadow_exit": recovery_missing,
        "recovery_fallback_count": recovery_fallback_n,
        "shadow_exit_triggered_count": shadow_triggered_n,
        "runtime_pnl": round(sum(runtime_pnls), 2),
        "shadow_pnl": round(sum(shadow_pnls), 2),
        "delta_pnl": round(sum(deltas), 2),
        "runtime_pf": _pf(runtime_pnls),
        "shadow_pf": _pf(shadow_pnls),
        "win_count": sum(1 for y in shadow_pnls if y > 0),
        "loss_count": sum(1 for y in shadow_pnls if y < 0),
        "flat_count": sum(1 for y in shadow_pnls if y == 0),
        "pnl_applicable": True,
        "status": "RUNNING_PNL_COMPLETE" if complete else "PARTIAL_PIPELINE",
        "reason_if_incomplete": "RECOVERY_EXIT_SHADOW_EXIT_PRICE_MISSING" if recovery_missing else "",
        "join_success_rate": 1.0 if exits else None,
    }


def recompute_imbalance_yen(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Board Imbalance candidates joined to yen_100."""
    accepts, exits = index_accept_exit(events)
    yens: list[float] = []
    n = 0
    for pid, acc in accepts.items():
        if not (_bool(acc.get("imbalance_shadow_candidate")) or acc.get("imbalance_shadow_tier")):
            continue
        ex = exits.get(pid)
        if ex is None:
            continue
        n += 1
        y = _f(ex.get("actual_pnl_yen_100")) or _f(ex.get("pnl_yen_100"))
        if y is None:
            ep = _f(ex.get("entry_price") or acc.get("entry_price"))
            xp = _f(ex.get("exit_price"))
            if ep and xp:
                from replay.pnl_yen import compute_pnl_yen_100

                y = round(compute_pnl_yen_100(ep, xp), 2)
        if y is not None:
            yens.append(float(y))
    return {
        "shadow_id": "board_imbalance_shadow",
        "candidates": n,
        "completed": len(yens),
        "open": 0,
        "runtime_pnl": round(sum(yens), 2),
        "shadow_pnl": round(sum(yens), 2),
        "delta_pnl": 0.0,
        "runtime_pf": _pf(yens),
        "shadow_pf": _pf(yens),
        "win_count": sum(1 for y in yens if y > 0),
        "loss_count": sum(1 for y in yens if y < 0),
        "flat_count": sum(1 for y in yens if y == 0),
        "pnl_applicable": True,
        "status": "RUNNING_PNL_COMPLETE" if yens else "ENABLED_NO_EVENTS",
    }


def recompute_readiness_precision(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return recompute_entry_block_shadow(
        events,
        candidate_keys=("readiness_precision_shadow_block",),
        block_keys=("readiness_precision_shadow_block",),
        shadow_id="readiness_precision_shadow",
    )


def recompute_readiness_economics(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return recompute_entry_block_shadow(
        events,
        candidate_keys=("readiness_economics_shadow_block",),
        block_keys=("readiness_economics_shadow_block",),
        shadow_id="readiness_economics_shadow",
    )


def recompute_microsequence_c(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return recompute_entry_block_shadow(
        events,
        candidate_keys=("microsequence_recovery_fail_shadow_block",),
        block_keys=("microsequence_recovery_fail_shadow_block",),
        shadow_id="microsequence_recovery_fail_shadow",
    )


def apply_fwr_summary_fields(summary: dict[str, Any], recomputed: Mapping[str, Any]) -> None:
    summary["flat_weak_range_shadow_target_count"] = recomputed["target_count"]
    summary["flat_weak_range_shadow_block_count"] = recomputed["block_count"]
    summary["flat_weak_range_shadow_kept_count"] = recomputed["kept_count"]
    summary["flat_weak_range_shadow_completed"] = recomputed["completed"]
    summary["flat_weak_range_shadow_exit_join_count"] = recomputed["exit_join_count"]
    summary["flat_weak_range_shadow_exit_join_miss_count"] = recomputed["exit_join_miss_count"]
    summary["flat_weak_range_shadow_actual_total_pnl_yen_100"] = recomputed["runtime_pnl"]
    summary["flat_weak_range_shadow_total_pnl_yen_100"] = recomputed["shadow_pnl"]
    summary["flat_weak_range_shadow_delta_yen"] = recomputed["delta_pnl"]
    summary["flat_weak_range_shadow_actual_pf"] = recomputed["runtime_pf"]
    summary["flat_weak_range_shadow_shadow_pf"] = recomputed["shadow_pf"]
    summary["flat_weak_range_shadow_blocked_winners"] = recomputed["blocked_winners"]
    summary["flat_weak_range_shadow_blocked_losers"] = recomputed["blocked_losers"]
    summary["flat_weak_range_shadow_recovery_join_count"] = recomputed["recovery_join_count"]
    summary["flat_weak_range_shadow_phase677_recomputed"] = True
