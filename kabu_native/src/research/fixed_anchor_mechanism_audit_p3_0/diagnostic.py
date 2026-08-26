"""Cross-section scoring + independent diagnostic outcome (not a strategy)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.e1_x35_passive_exit.paths import build_path
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.fixed_anchor_mechanism_audit_p3_0.grid import hm_epoch, hm_label, session_of_epoch
from research.v1r_exit_v2_asymmetric.states import build_trade_bundle
from small_paper.v1r_exit_v2_contract import apply_arch_e_to_bundle
from small_paper.v1r_live_dual_lane import (
    BOARD_FRESH_SEC,
    MIN_BUY1_QTY,
    canonical_symbol_key,
    session_end_for_position,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import CLOCK_GRID, WAIT_SEC

OFFSETS_MARKET = (0, -300, -180, -60, 60, 180, 300)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _with_mid(board: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    out = dict(board)
    if "mid" not in out:
        bid = np.asarray(out["bid"], dtype=float)
        ask = np.asarray(out["ask"], dtype=float)
        mid = np.full(bid.shape, np.nan, dtype=float)
        ok = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > 0)
        mid[ok] = (bid[ok] + ask[ok]) / 2.0
        out["mid"] = mid
    return out


def last_valid_bid_at_or_before(
    board: dict[str, np.ndarray], *, until_t: float, after_t: Optional[float] = None
) -> Optional[tuple[float, float]]:
    t = board["t"]
    if t.size == 0:
        return None
    i_end = int(np.searchsorted(t, float(until_t), side="right") - 1)
    for i in range(i_end, -1, -1):
        ti = float(t[i])
        if after_t is not None and ti + 1e-12 < float(after_t):
            break
        if board["special"][i]:
            continue
        fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else 0.0
        if fresh > BOARD_FRESH_SEC + 1e-12:
            continue
        bq = board["bid_qty"][i]
        bid = board["bid"][i]
        if not np.isfinite(bq) or bq < MIN_BUY1_QTY - 1e-12:
            continue
        if not np.isfinite(bid) or bid <= 0:
            continue
        return ti, float(bid)
    return None


def independent_diagnostic_outcome(
    board: dict[str, np.ndarray],
    *,
    date: str,
    symbol: str,
    session: str,
    t0: float,
    limit_price: float,
) -> dict[str, Any]:
    """Same t0 / limit bid / WAIT_SEC=1 / 100 shares / Arch E if filled.

    Ignores OPEN/PENDING/POSITION_CAP. Not a portfolio PnL. Not a strategy return.
    """
    out: dict[str, Any] = {
        "label": "INDEPENDENT_DIAGNOSTIC_OUTCOME",
        "independent_filled": False,
        "independent_fill_time": None,
        "independent_fill_price": None,
        "independent_exit_time": None,
        "independent_exit_price": None,
        "independent_exit_reason": None,
        "independent_pnl": None,
        "fill_reason": None,
    }
    lim = float(limit_price)
    if not np.isfinite(lim) or lim <= 0:
        out["fill_reason"] = "INVALID_LIMIT"
        return out
    fill = find_ask_cross_fill(
        board,
        t0=float(t0),
        wait_sec=float(WAIT_SEC),
        limit_price=lim,
        sess_end=float(t0) + 3 * 3600.0,
    )
    if not fill.get("filled"):
        out["fill_reason"] = str(fill.get("reason") or "NO_FILL")
        return out
    fill_t = float(fill["fill_t"])
    fill_px = float(fill.get("fill_price") or lim)
    out["independent_filled"] = True
    out["independent_fill_time"] = fill_t
    out["independent_fill_price"] = fill_px

    sess_end = session_end_for_position(date=date, session=session, fill_time=fill_t)
    board2 = _with_mid(board)
    path = build_path(board2, entry_price=fill_px, entry_t=fill_t, sess_end=float(sess_end))
    reason = "SESSION_CLOSE"
    exit_t: Optional[float] = None
    exit_px: Optional[float] = None
    if path.get("ok"):
        bundle = build_trade_bundle(
            {
                "date": date,
                "symbol": symbol,
                "session": session,
                "fill_time": fill_t,
                "fill_price": fill_px,
                "anchor_id": "INDEPENDENT_DIAGNOSTIC",
            },
            path,
            board2,
        )
        pol = apply_arch_e_to_bundle(bundle)
        if pol.get("ok"):
            reason = str(pol.get("reason") or "ARCH_E")
            exit_t = float(pol["exit_time"])
            ret = float(pol.get("exit_ret_bps") or 0.0)
            exit_px = fill_px * (1.0 + ret / 10000.0)
            if exit_t > float(sess_end) + 1e-12:
                reason = "SESSION_CLOSE"
                exit_t = None
                exit_px = None
    if exit_t is None or exit_px is None:
        found = last_valid_bid_at_or_before(board, until_t=float(sess_end), after_t=fill_t)
        if found is None:
            found = last_valid_bid_at_or_before(board, until_t=float(sess_end), after_t=None)
        if found is None:
            exit_t = fill_t
            exit_px = fill_px
            reason = "SESSION_CLOSE"
        else:
            exit_t, exit_px = found
            reason = "SESSION_CLOSE"
    out["independent_exit_time"] = float(exit_t)
    out["independent_exit_price"] = float(exit_px)
    out["independent_exit_reason"] = reason
    out["independent_pnl"] = round((float(exit_px) - fill_px) * 100.0, 4)
    return out


def score_universe_at(
    eng: Any,
    *,
    t0: float,
    day: str,
    session: str,
) -> dict[str, Any]:
    """Rebuild preentry/score/simulate_joint metadata with state <= t0 only."""
    events: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    leak = False
    max_snap_minus_t0 = 0.0
    for sym in list(eng.universe):
        s = canonical_symbol_key(sym)
        board = eng._board_arrays(s)
        rec: dict[str, Any] = {
            "symbol": s,
            "feature_evaluable": False,
            "score": None,
            "alloc_score": None,
            "limit": None,
            "snapshot_t": None,
            "snapshot_minus_t0": None,
        }
        if board["t"].size == 0:
            rows.append(rec)
            continue
        i = int(np.searchsorted(board["t"], t0, side="right") - 1)
        if i < 0:
            rows.append(rec)
            continue
        snap_t = float(board["t"][i])
        rec["snapshot_t"] = snap_t
        rec["snapshot_minus_t0"] = snap_t - float(t0)
        max_snap_minus_t0 = max(max_snap_minus_t0, rec["snapshot_minus_t0"])
        if snap_t > float(t0) + 1e-9:
            leak = True
        feats = preentry_from_board(board, t0)
        if any(feats.get(f) is None or not np.isfinite(feats.get(f)) for f in FEATURE_ORDER):
            rows.append(rec)
            continue
        score = float(eng.score_fn(feats))
        if not np.isfinite(score):
            rows.append(rec)
            continue
        limit = float(board["bid"][i])
        if not np.isfinite(limit) or limit <= 0:
            rows.append(rec)
            continue
        rec["feature_evaluable"] = True
        rec["score"] = score
        rec["limit"] = limit
        events.append(
            {
                "date": day,
                "symbol": s,
                "session": session,
                "signal_time": float(t0),
                "filled": False,
                "limit_price": limit,
                "bid0": limit,
                **{f: feats.get(f) for f in FEATURE_ORDER},
                "score_preview": score,
            }
        )
        rows.append(rec)
    selected_n = 0
    if events:
        sim = simulate_joint([dict(e) for e in events], score_fn=eng.score_fn)
        ranked = sorted(
            [e for e in sim["events"] if e.get("alloc_score") is not None],
            key=lambda e: (-float(e.get("alloc_score") or 0.0), str(e.get("symbol") or "")),
        )
        rank_by = {str(e["symbol"]): i for i, e in enumerate(ranked)}
        by = {str(e["symbol"]): e for e in sim["events"]}
        for rec in rows:
            e = by.get(str(rec["symbol"]))
            if e is None:
                continue
            rec["alloc_score"] = e.get("alloc_score")
            rec["rank"] = rank_by.get(str(rec["symbol"]))
            rec["selected"] = bool(e.get("admitted"))
            if rec["selected"]:
                selected_n += 1
    scores = [float(r["score"]) for r in rows if r.get("score") is not None]
    arr = np.asarray(scores, dtype=float) if scores else np.asarray([], dtype=float)
    gap = None
    if arr.size >= 2:
        ordered = np.sort(arr)[::-1]
        gap = float(ordered[0] - ordered[1])
    elif arr.size == 1:
        gap = None
    p90p10 = None
    if arr.size:
        p90p10 = float(np.percentile(arr, 90) - np.percentile(arr, 10))
    return {
        "rows": rows,
        "eligible_symbol_n": int(sum(1 for r in rows if r.get("feature_evaluable"))),
        "selected_n": int(selected_n),
        "score_median": None if arr.size == 0 else float(np.median(arr)),
        "score_std": None if arr.size < 2 else float(np.std(arr, ddof=1)),
        "score_p90_p10": p90p10,
        "top1_top2_score_gap": gap,
        "snapshot_future_leak": bool(leak),
        "max_snapshot_minus_t0": float(max_snap_minus_t0),
    }


def market_state_grid(eng: Any, *, day: str) -> tuple[list[dict[str, Any]], bool]:
    out: list[dict[str, Any]] = []
    leak = False
    for h, m in CLOCK_GRID:
        t_orig = hm_epoch(day, h, m)
        for off in OFFSETS_MARKET:
            t0 = t_orig + float(off)
            sess = session_of_epoch(day, t0)
            if sess is None:
                continue
            scored = score_universe_at(eng, t0=t0, day=day, session=sess)
            if scored.get("snapshot_future_leak"):
                leak = True
            out.append(
                {
                    "date": day,
                    "session": sess,
                    "anchor_time": hm_label(h, m),
                    "offset_sec": int(off),
                    "t0": float(t0),
                    "eligible_symbol_n": scored["eligible_symbol_n"],
                    "score_median": scored["score_median"],
                    "score_std": scored["score_std"],
                    "score_p90_p10": scored["score_p90_p10"],
                    "top1_top2_score_gap": scored["top1_top2_score_gap"],
                    "selected_n": scored["selected_n"],
                }
            )
    return out, leak


def assemble_cross_section(
    eng: Any,
    *,
    day: str,
    trades: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Original Fixed fires only. Attach occupancy + independent diagnostic."""
    occ_by = {}
    for o in eng.anchor_occ:
        occ_by[str(o["anchor"])] = o
    admits = {(str(a.get("anchor")), str(a.get("symbol"))) for a in eng.a_admits}
    fills = {(str(f.get("anchor")), str(f.get("symbol"))) for f in eng.a_fills}
    trade_keys = {(str(t.get("anchor_time")), str(t.get("symbol"))) for t in trades}

    xs: list[dict[str, Any]] = []
    leak = False
    seen_anchors = sorted({an for an, _sym in eng.snapshots.keys()})
    for an in seen_anchors:
        occ = occ_by.get(an) or {}
        t0 = occ.get("t0")
        session = occ.get("session") or ("AM" if str(an).split(":")[0] < "12" else "PM")
        open_set = set(occ.get("open") or [])
        pending_set = set(occ.get("pending") or [])
        exposure = int(occ.get("exposure") or 0)
        cap = int(occ.get("position_cap") or 5)
        if t0 is None:
            for (_an, _s), snap in eng.snapshots.items():
                if _an == an and snap.get("t0") is not None:
                    t0 = float(snap["t0"])
                    break
        if t0 is None:
            h, m = [int(x) for x in str(an).split(":")]
            t0 = hm_epoch(day, h, m)

        scored = score_universe_at(eng, t0=float(t0), day=day, session=str(session))
        if scored.get("snapshot_future_leak"):
            leak = True
        by_scored = {str(r["symbol"]): r for r in scored["rows"]}
        symbols = sorted({sym for a, sym in eng.snapshots.keys() if a == an} | set(by_scored))
        for s in symbols:
            snap = eng.snapshots.get((an, s), {})
            sc = by_scored.get(s, {})
            feature_evaluable = bool(sc.get("feature_evaluable"))
            score = sc.get("score") if sc.get("score") is not None else snap.get("score")
            alloc = sc.get("alloc_score") if sc.get("alloc_score") is not None else snap.get("score")
            rank = sc.get("rank") if sc.get("rank") is not None else snap.get("rank")
            selected = bool(sc.get("selected")) if "selected" in sc else bool(snap.get("admitted"))
            in_open = s in open_set
            in_pending = s in pending_set
            live_eligible = bool(feature_evaluable and not in_open and not in_pending)
            limit = sc.get("limit") if sc.get("limit") is not None else snap.get("bid")
            row: dict[str, Any] = {
                "date": day,
                "session": session,
                "anchor_time": an,
                "t0": float(t0),
                "symbol": s,
                "feature_evaluable": feature_evaluable,
                "score": None if score is None else float(score),
                "alloc_score": None if alloc is None else float(alloc),
                "rank": None if rank is None else int(rank),
                "selected": bool(selected) if feature_evaluable else False,
                "live_eligible": live_eligible,
                "in_open": in_open,
                "in_pending": in_pending,
                "exposure": exposure,
                "cap_full": exposure >= cap,
                "actual_admitted": (an, s) in admits,
                "actual_filled": (an, s) in fills,
                "actual_trade": (an, s) in trade_keys,
                "independent_filled": False,
                "independent_pnl": None,
                "independent_exit_reason": None,
                "fill_reason": None,
                "label": "INDEPENDENT_DIAGNOSTIC_OUTCOME",
            }
            if feature_evaluable and limit is not None:
                board = eng._board_arrays(s)
                diag = independent_diagnostic_outcome(
                    board,
                    date=day,
                    symbol=s,
                    session=str(session),
                    t0=float(t0),
                    limit_price=float(limit),
                )
                row["independent_filled"] = bool(diag.get("independent_filled"))
                row["independent_pnl"] = diag.get("independent_pnl")
                row["independent_exit_reason"] = diag.get("independent_exit_reason")
                row["fill_reason"] = diag.get("fill_reason")
            xs.append(row)
    return xs, leak
