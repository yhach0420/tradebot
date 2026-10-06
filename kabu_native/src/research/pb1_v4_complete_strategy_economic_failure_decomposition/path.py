"""Causal path diagnostics after ENTRY fill. Not a new fixed-hold strategy."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, in_lunch
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import hhmm_to_min
from research.pb1_v4_complete_strategy_economic_failure_decomposition import HORIZONS_MIN

SHARES = 100


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def dir_bps(*, side: str, entry: float, px: float) -> float | None:
    if not (_finite(entry) and float(entry) > 0 and _finite(px)):
        return None
    long = str(side).lower() in {"bull", "long", "1"}
    signed = (float(px) - float(entry)) if long else (float(entry) - float(px))
    return 10_000.0 * signed / float(entry)


def parse_identity(execution_id: str) -> dict[str, str | None]:
    parts = str(execution_id or "").split("|")
    seed = parts[3] if len(parts) > 3 else None
    loc = parts[6] if len(parts) > 6 else None
    sub = parts[7] if len(parts) > 7 else None
    return {"seed_family": seed, "location_family": loc, "location_subtype": sub}


def _plus_tradable(start: str, minutes: int) -> str | None:
    cur = str(start)[:5]
    for _ in range(int(minutes)):
        nxt = hhmm_add(cur, 1)
        if nxt is None:
            return None
        if in_lunch(nxt) or nxt == "11:30":
            cur = "12:29"
            nxt = hhmm_add(cur, 1)
        cur = str(nxt)
        if cur > "15:20":
            return None
    return cur


def _idx(rec: dict[str, Any], t: str) -> int | None:
    try:
        return list(rec["t"]).index(str(t)[:5])
    except ValueError:
        return None


def _bar_after(rec: dict[str, Any], t: str) -> int | None:
    tm = hhmm_to_min(str(t)[:5])
    if tm is None:
        return None
    for i, bt in enumerate(rec.get("t") or []):
        bm = hhmm_to_min(str(bt)[:5])
        if bm is None or in_lunch(str(bt)):
            continue
        if bm > tm:
            return i
    return None


def path_for_trade(trade: dict[str, Any], rec: dict[str, Any] | None) -> dict[str, Any]:
    ident = parse_identity(str(trade.get("execution_id") or ""))
    entry = float(trade.get("entry_px") or 0)
    side = str(trade.get("side") or "")
    entry_t = str(trade.get("entry_t") or "")[:5]
    exit_t = str(trade.get("exit_t") or "")[:5]
    lost_t = str(trade.get("THESIS_LOST_AT") or "")[:5] or None
    gross = float(trade.get("gross_pnl_yen") or 0.0)
    realized = dir_bps(side=side, entry=entry, px=float(trade.get("exit_px") or 0))
    out = {
        **{k: trade.get(k) for k in (
            "symbol", "date", "entry_type", "side", "DIR", "entry_t", "entry_px", "exit_t", "exit_px",
            "exit_reason", "THESIS_LOST_AT", "THESIS_LOST_REASON", "gross_pnl_yen", "execution_cost_yen",
            "net_pnl_yen", "holding_min", "reentry_n", "execution_id", "ops_flatten", "thesis_death",
        )},
        **ident,
        "entry_notional_yen": float(entry) * float(SHARES),
        "realized_gross_bps": realized,
        "gross_bps": realized,
        "net_bps": dir_bps(side=side, entry=entry, px=float(trade.get("exit_px") or 0)) if realized is not None else None,
        "return_on_entry_notional": (float(trade.get("net_pnl_yen") or 0.0) / (entry * SHARES) if entry else None),
        "path_ok": False,
    }
    # net_bps should include tax in yen / notional
    notional = entry * SHARES
    if notional:
        out["net_bps"] = 10_000.0 * float(trade.get("net_pnl_yen") or 0.0) / notional
        out["gross_bps"] = 10_000.0 * gross / notional
    if rec is None:
        return out
    start_i = _idx(rec, entry_t)
    if start_i is None:
        return out
    long = str(side).lower() in {"bull", "long", "1"}
    mfe = 0.0
    mae = 0.0
    mfe_t = entry_t
    mae_t = entry_t
    mfe_pre = 0.0
    mae_pre = 0.0
    first_fav_t = None
    first_adv_t = None
    times = list(rec["t"])
    for i in range(start_i, len(times)):
        t = str(times[i])[:5]
        if in_lunch(t):
            continue
        if t > exit_t:
            break
        hi, lo, cl = rec["h"][i], rec["l"][i], rec["c"][i]
        if t == entry_t:
            fav_px = hi if long else lo
            adv_px = lo if long else hi
        elif t == exit_t:
            fav_px = rec["o"][i]
            adv_px = rec["o"][i]
        else:
            fav_px = hi if long else lo
            adv_px = lo if long else hi
        fb = dir_bps(side=side, entry=entry, px=float(fav_px) if _finite(fav_px) else entry)
        ab = dir_bps(side=side, entry=entry, px=float(adv_px) if _finite(adv_px) else entry)
        if fb is not None and fb > mfe:
            mfe = float(fb)
            mfe_t = t
        if ab is not None and ab < mae:
            mae = float(ab)
            mae_t = t
        if first_fav_t is None and fb is not None and fb > 0:
            first_fav_t = t
        if first_adv_t is None and ab is not None and ab < 0:
            first_adv_t = t
        if lost_t and t <= lost_t:
            mfe_pre = mfe
            mae_pre = mae
        elif not lost_t:
            mfe_pre = mfe
            mae_pre = mae
        if t >= exit_t:
            break
        _ = cl
    horizons = {}
    for n in HORIZONS_MIN:
        ht = _plus_tradable(entry_t, n)
        if not ht:
            horizons[f"ret_bps_{n}m"] = None
            continue
        j = _idx(rec, ht)
        if j is None:
            j = _bar_after(rec, ht)
            if j is None:
                horizons[f"ret_bps_{n}m"] = None
                continue
            ht = str(rec["t"][j])[:5]
        if ht > exit_t:
            horizons[f"ret_bps_{n}m"] = realized
            continue
        px = rec["c"][j]
        horizons[f"ret_bps_{n}m"] = dir_bps(side=side, entry=entry, px=float(px) if _finite(px) else entry)
    mfe_to_exit = (float(realized) - float(mfe)) if realized is not None else None
    capture = (float(realized) / float(mfe)) if realized is not None and mfe > 0 else None
    em = hhmm_to_min(entry_t) or 0
    out.update(
        {
            "path_ok": True,
            "MFE_bps": float(mfe),
            "MAE_bps": float(mae),
            "MFE_t": mfe_t,
            "MAE_t": mae_t,
            "time_to_MFE": (hhmm_to_min(mfe_t) or 0) - em,
            "time_to_MAE": (hhmm_to_min(mae_t) or 0) - em,
            "MFE_before_THESIS_LOST": float(mfe_pre),
            "MAE_before_THESIS_LOST": float(mae_pre),
            "first_favorable_t": first_fav_t,
            "first_adverse_t": first_adv_t,
            "MFE_to_exit_giveback_bps": mfe_to_exit,
            "MFE_capture_ratio": capture,
            "minutes_MFE_to_THESIS_LOST": ((hhmm_to_min(lost_t) or 0) - (hhmm_to_min(mfe_t) or 0)) if lost_t else None,
            "minutes_THESIS_LOST_to_fill": ((hhmm_to_min(exit_t) or 0) - (hhmm_to_min(lost_t) or 0)) if lost_t else None,
            "profit_available_before_THESIS_LOST": bool(mfe_pre >= 8.0),
            "loss_already_present_before_THESIS_LOST": bool(mae_pre <= -8.0),
            **horizons,
        }
    )
    return out


def attach_paths(trades: list[dict[str, Any]], recs: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for t in trades:
        rec = recs.get((str(t.get("date") or ""), str(t.get("symbol") or "")))
        rows.append(path_for_trade(t, rec))
    return rows
