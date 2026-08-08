"""Latency drag diagnostics — not strategy search; no interpolation."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from .aggregate import dist_stats, episode_mean, tod_bucket
from .quotes import evaluate_episode


def _paired_drag(
    base_rows: list[dict],
    delayed_map: dict[tuple, dict],
    *,
    H: int,
    field: str = "exec",
) -> list[float]:
    """delayed_return - zero_delay_return for matched keys."""
    key_fn = lambda r: (r["date"], r["symbol"], r["session"], float(r["signal_t"]))
    out = []
    k = f"{field}_{H}"
    for r in base_rows:
        if not r.get(f"{field}_valid_{H}" if field != "exec" else f"exec_valid_{H}"):
            # base uses exec_valid / mid_valid
            pass
        bval = r.get(k)
        d = delayed_map.get(key_fn(r))
        if d is None or bval is None or not np.isfinite(bval):
            continue
        dval = d.get(k)
        if dval is None or not np.isfinite(dval):
            continue
        # also require valid flags when present
        bv = r.get(f"{field}_valid_{H}", True)
        dv = d.get(f"{field}_valid_{H}", True)
        if field == "exec":
            bv = r.get(f"exec_valid_{H}")
            dv = d.get(f"exec_valid_{H}")
        elif field == "mid":
            bv = r.get(f"mid_valid_{H}")
            dv = d.get(f"mid_valid_{H}")
        if not bv or not dv:
            continue
        out.append(float(dval) - float(bval))
    return out


def run_latency_scenarios(
    planned: list[dict],
    board_by_key: dict,
    base_rows: list[dict],
    delays: list[float],
) -> dict[str, Any]:
    """
    For each delay d in delays (d>0):
      ENTRY_LATENCY: entry_delay=d, exit_delay=0
      EXIT_LATENCY:  entry_delay=0, exit_delay=d
      BOTH_LATENCY:  entry_delay=d, exit_delay=d
    Drag vs zero-delay baseline on matched episodes.
    """
    base_idx = {
        (r["date"], r["symbol"], r["session"], float(r["signal_t"])): r
        for r in base_rows
    }

    results: dict[str, Any] = {"delays_sec": delays, "by_delay": {}}

    for d in delays:
        if d <= 0:
            continue
        entry_map: dict[tuple, dict] = {}
        exit_map: dict[tuple, dict] = {}
        both_map: dict[tuple, dict] = {}

        for a in planned:
            key = (a["date"], a["symbol"], a["session"], float(a["grid_epoch"]))
            if key not in base_idx:
                continue
            board = board_by_key.get((a["date"], a["symbol"]))
            if board is None or board["t"].size == 0:
                continue

            ep_e = evaluate_episode(
                board, date=a["date"], session=a["session"],
                signal_t=float(a["grid_epoch"]), entry_delay=d, exit_delay=0.0,
            )
            if ep_e.get("ok"):
                entry_map[key] = ep_e

            ep_x = evaluate_episode(
                board, date=a["date"], session=a["session"],
                signal_t=float(a["grid_epoch"]), entry_delay=0.0, exit_delay=d,
            )
            if ep_x.get("ok"):
                exit_map[key] = ep_x

            ep_b = evaluate_episode(
                board, date=a["date"], session=a["session"],
                signal_t=float(a["grid_epoch"]), entry_delay=d, exit_delay=d,
            )
            if ep_b.get("ok"):
                both_map[key] = ep_b

        block: dict[str, Any] = {"delay_sec": d}
        for H in (300, 600):
            e_drag = _paired_drag(base_rows, entry_map, H=H, field="exec")
            x_drag = _paired_drag(base_rows, exit_map, H=H, field="exec")
            b_drag = _paired_drag(base_rows, both_map, H=H, field="exec")
            block[f"entry_latency_drag_{H}"] = dist_stats(e_drag)
            block[f"exit_latency_drag_{H}"] = dist_stats(x_drag)
            block[f"both_latency_drag_{H}"] = dist_stats(b_drag)
            # estimated gain from removing d sec = -mean(drag) if drag is delayed-minus-zero
            # negative drag means delayed is worse; removing latency gains -mean(drag)
            me = block[f"entry_latency_drag_{H}"]["mean"]
            block[f"estimated_gain_remove_entry_latency_{H}"] = (
                None if me is None else float(-me)
            )

        # TOD breakdown for entry latency 300/600
        tod_stats: dict[str, Any] = {}
        for bucket, _, _ in __import__(
            "research.e1_x33c_baseline_economics", fromlist=["TOD_BUCKETS"]
        ).TOD_BUCKETS:
            tod_stats[bucket] = {}
        # rebuild per-bucket entry drag
        for H in (300, 600):
            by_tod: dict[str, list[float]] = defaultdict(list)
            key_fn = lambda r: (r["date"], r["symbol"], r["session"], float(r["signal_t"]))
            for r in base_rows:
                drow = entry_map.get(key_fn(r))
                if drow is None:
                    continue
                if not r.get(f"exec_valid_{H}") or not drow.get(f"exec_valid_{H}"):
                    continue
                bval, dval = r.get(f"exec_{H}"), drow.get(f"exec_{H}")
                if bval is None or dval is None:
                    continue
                b = tod_bucket(float(r["signal_t"])) or "UNK"
                by_tod[b].append(float(dval) - float(bval))
            for b, xs in by_tod.items():
                tod_stats.setdefault(b, {})[f"entry_drag_{H}"] = dist_stats(xs)
        block["tod"] = tod_stats

        # market state bins
        state: dict[str, Any] = {}
        for dim in ("spread_bin", "vol_bin", "activity_bin"):
            state[dim] = {}
            for H in (300, 600):
                by_bin: dict[str, list[float]] = defaultdict(list)
                key_fn = lambda r: (r["date"], r["symbol"], r["session"], float(r["signal_t"]))
                for r in base_rows:
                    drow = entry_map.get(key_fn(r))
                    if drow is None or not r.get(f"exec_valid_{H}") or not drow.get(f"exec_valid_{H}"):
                        continue
                    bval, dval = r.get(f"exec_{H}"), drow.get(f"exec_{H}")
                    if bval is None or dval is None:
                        continue
                    by_bin[str(r.get(dim) or "UNK")].append(float(dval) - float(bval))
                state[dim][str(H)] = {k: dist_stats(v) for k, v in by_bin.items()}
        block["by_market_state"] = state

        # day-level entry drag means
        day_lat: dict[str, list[float]] = defaultdict(list)
        for r in base_rows:
            key = (r["date"], r["symbol"], r["session"], float(r["signal_t"]))
            drow = entry_map.get(key)
            if drow is None or not r.get("exec_valid_600") or not drow.get("exec_valid_600"):
                continue
            bval, dval = r.get("exec_600"), drow.get("exec_600")
            if bval is None or dval is None:
                continue
            day_lat[r["date"]].append(float(dval) - float(bval))
        block["entry_drag600_by_day"] = {
            d: float(np.mean(v)) for d, v in sorted(day_lat.items()) if v
        }

        results["by_delay"][str(d)] = block

    return results


def waterfall_bps(base_summary: dict[str, Any], latency: dict[str, Any]) -> dict[str, Any]:
    """
    Explicit non-double-counting waterfall for 300/600.

    Identity:
      EXEC = MID + EXECUTION_DRAG
      EXECUTION_DRAG ≈ ENTRY_HALF_SPREAD + EXIT_HALF_SPREAD + RESIDUAL
      (latency is a sensitivity of EXEC under delayed entry/exit — not added into
       baseline EXEC; shown as incremental diagnostic)
    """
    out = {}
    lat1 = (latency.get("by_delay") or {}).get("1.0") or {}
    lat2 = (latency.get("by_delay") or {}).get("2.0") or {}
    lat5 = (latency.get("by_delay") or {}).get("5.0") or {}

    for H in (300, 600):
        mid = base_summary.get(f"mid_{H}_episode")
        exe = base_summary.get(f"exec_{H}_episode")
        drag = base_summary.get(f"drag_{H}_episode")
        spr = base_summary.get(f"spread_only_drag_{H}_episode")
        res = base_summary.get(f"residual_drag_{H}_episode")
        e_half = base_summary.get("entry_half_spread_mean")
        x_half = base_summary.get(f"exit_half_spread_{H}_mean")

        e1 = (lat1.get(f"entry_latency_drag_{H}") or {}).get("mean")
        e2 = (lat2.get(f"entry_latency_drag_{H}") or {}).get("mean")
        e5 = (lat5.get(f"entry_latency_drag_{H}") or {}).get("mean")

        out[str(H)] = {
            "formula": (
                f"EXEC_{H} = MID_{H} + DRAG_{H}; "
                f"DRAG_{H} ≈ ENTRY_HALF_SPREAD + EXIT_HALF_SPREAD_{H} + RESIDUAL_{H}; "
                "LATENCY_DRAG is sensitivity (delayed−zero), not a baseline addend"
            ),
            "steps_bps": [
                {"name": "MID_market_direction", "bps": mid},
                {
                    "name": "entry_crossing_half_spread",
                    "bps": None if e_half is None else -float(e_half),
                    "magnitude_bps": e_half,
                    "note": "subtracted from mid path (ask entry)",
                },
                {
                    "name": "exit_crossing_half_spread",
                    "bps": None if x_half is None else -float(x_half),
                    "magnitude_bps": x_half,
                    "note": "subtracted from mid path (bid exit)",
                },
                {"name": "quote_execution_residual", "bps": res},
                {
                    "name": "sum_mid_minus_spreads_plus_residual",
                    "bps": (
                        None
                        if None in (mid, e_half, x_half, res)
                        else float(mid) - float(e_half) - float(x_half) + float(res)
                    ),
                },
                {"name": "sum_to_exec_check_mid_plus_drag", "bps": None if mid is None or drag is None else mid + drag},
                {"name": "actual_executable_return", "bps": exe},
                {"name": "latency_entry_0to1s_drag", "bps": e1, "diagnostic_only": True},
                {"name": "latency_entry_0to2s_drag", "bps": e2, "diagnostic_only": True},
                {"name": "latency_entry_0to5s_drag", "bps": e5, "diagnostic_only": True},
            ],
            "execution_drag": drag,
            "spread_only_drag_magnitude": spr,
            "residual_drag": res,
            "identity": "EXEC = MID + DRAG; DRAG = -(ENTRY_HALF+EXIT_HALF) + RESIDUAL",
            "identity_check_exec_minus_mid": (
                None if exe is None or mid is None else float(exe - mid)
            ),
        }
    return out


def loss_share(base_summary: dict[str, Any], latency: dict[str, Any]) -> dict[str, Any]:
    """
    Attribute baseline EXEC loss (negative) into shares.
    Uses episode means at 600 primary; also reports 300.
    """
    out = {}
    lat1 = (latency.get("by_delay") or {}).get("1.0") or {}
    for H in (300, 600):
        exe = base_summary.get(f"exec_{H}_episode")
        mid = base_summary.get(f"mid_{H}_episode")
        spr = base_summary.get(f"spread_only_drag_{H}_episode")
        res = base_summary.get(f"residual_drag_{H}_episode")
        # latency not in baseline; share of |loss| that code speedup could reclaim
        # if we assume current code has ~1s latency vs ideal 0 — use |entry drag 1s|
        e1 = (lat1.get(f"entry_latency_drag_{H}") or {}).get("mean")
        loss = abs(exe) if exe is not None and exe < 0 else None
        # price direction contribution to loss: max(0, -mid) if mid negative
        # mid>0 ⇒ price direction not contributing to loss
        price = max(0.0, -float(mid)) if mid is not None else None
        # spread_only stored as positive magnitude; contribution to EXEC is -spread
        spread_cost = abs(float(spr)) if spr is not None else None
        # residual can help or hurt; adverse share uses max(0,-res) if res defined as DRAG+spread
        residual_adverse = max(0.0, -float(res)) if res is not None else None
        residual_abs = abs(float(res)) if res is not None else None
        lat_cost = abs(float(e1)) if e1 is not None else None
        # vs full ask→bid drag magnitude
        drag = None if mid is None or exe is None else float(exe) - float(mid)
        drag_mag = abs(drag) if drag is not None else None

        def pct(part, denom=None):
            d = loss if denom is None else denom
            if d is None or d < 1e-12 or part is None:
                return None
            return float(100.0 * part / d)

        out[str(H)] = {
            "baseline_exec": exe,
            "loss_abs_bps": loss,
            "execution_drag_bps": drag,
            "price_direction_adverse_bps": price,
            "spread_cost_bps": spread_cost,
            "residual_exec_bps": residual_abs,
            "residual_adverse_bps": residual_adverse,
            "latency_1s_abs_bps": lat_cost,
            "share_pct_of_baseline_loss": {
                "price_direction": pct(price),
                "spread_vs_loss": pct(spread_cost),
                "latency_1s": pct(lat_cost),
                "residual_adverse": pct(residual_adverse),
            },
            "share_pct_of_execution_drag_mag": {
                "spread": pct(spread_cost, drag_mag),
                "residual_abs": pct(residual_abs, drag_mag),
            },
            "code_speedup_reclaimable_pct_of_loss_1s": pct(lat_cost),
            "note": (
                "spread explains most of EXEC-MID; mid often positive so baseline loss "
                "is residual after large opposing mid vs drag. latency share vs |EXEC|."
            ),
        }
    return out
