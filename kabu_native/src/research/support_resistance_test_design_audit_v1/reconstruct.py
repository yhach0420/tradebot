"""Recompute frozen daily reactions and zones. Density, overlap, salience. No retune."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes, same_day_reactions
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.multi_touch_daily_zone_1m_price_action_v1.zones import snapshot_zones
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.support_resistance_test_design_audit_v1 import (
    MATERIAL_OVERLAP_FRAC,
    NEAR_ATR,
    SAMPLE_N,
    SAMPLE_SEED,
    STALE_ATR,
    WEAK_MAG_ATR,
    WEAK_REJECTION_FRAC,
    ORDINARY_REJECTION_FRAC,
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def percentiles(xs: list[float] | np.ndarray) -> dict[str, float | int | None]:
    arr = np.asarray([x for x in xs if _finite(x)], dtype=float)
    if arr.size == 0:
        return {"n": 0, "p10": None, "p25": None, "p50": None, "p75": None, "p90": None, "max": None, "mean": None}
    return {
        "n": int(arr.size),
        "p10": float(np.percentile(arr, 10)),
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
    }


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or row.get("sector17_name") or "")
    return out


def _vol_regime(atr: float, hist_atr: list[float]) -> str:
    xs = [x for x in hist_atr if _finite(x) and x > 0]
    if not xs or not _finite(atr) or atr <= 0:
        return "unknown"
    lo, hi = float(np.percentile(xs, 33)), float(np.percentile(xs, 67))
    if atr < lo:
        return "low"
    if atr > hi:
        return "high"
    return "mid"


def _overlap_stats(zones: list[dict[str, Any]], atr: float) -> dict[str, Any]:
    n = len(zones)
    material = 0
    nested = 0
    center_d: list[float] = []
    shared_members = 0
    pairs = 0
    for i in range(n):
        a = zones[i]
        ids_a = {id(m) for m in list(a.get("members") or [])}
        for j in range(i + 1, n):
            b = zones[j]
            pairs += 1
            overlap = max(0.0, min(float(a["hi"]), float(b["hi"])) - max(float(a["lo"]), float(b["lo"])))
            wa = float(a["hi"]) - float(a["lo"])
            wb = float(b["hi"]) - float(b["lo"])
            mw = min(wa, wb)
            if mw > 0 and overlap >= MATERIAL_OVERLAP_FRAC * mw:
                material += 1
            if float(a["lo"]) <= float(b["lo"]) and float(b["hi"]) <= float(a["hi"]):
                nested += 1
            elif float(b["lo"]) <= float(a["lo"]) and float(a["hi"]) <= float(b["hi"]):
                nested += 1
            if _finite(atr) and atr > 0:
                center_d.append(abs(float(a["center"]) - float(b["center"])) / float(atr))
            ids_b = {id(m) for m in list(b.get("members") or [])}
            shared_members += len(ids_a & ids_b)
    return {
        "n_zones": n,
        "n_pairs": pairs,
        "n_material_overlap_pairs": material,
        "n_nested_pairs": nested,
        "shared_reaction_members": shared_members,
        "median_center_dist_atr": float(np.median(center_d)) if center_d else None,
        "min_center_dist_atr": float(np.min(center_d)) if center_d else None,
    }


def _nearest(zones: list[dict[str, Any]], px: float, *, side: str, atr: float) -> dict[str, Any]:
    if not _finite(px):
        return {"dist_atr": None, "center": None, "lo": None, "hi": None, "inside": False}
    inside = any(float(z["lo"]) <= px <= float(z["hi"]) for z in zones)
    cands = []
    for z in zones:
        c = float(z["center"])
        if side == "above" and c >= px:
            cands.append((c - px, z))
        if side == "below" and c <= px:
            cands.append((px - c, z))
    if not cands:
        return {"dist_atr": None, "center": None, "lo": None, "hi": None, "inside": inside}
    dist, z = min(cands, key=lambda t: t[0])
    return {
        "dist_atr": (float(dist) / float(atr)) if _finite(atr) and atr > 0 else None,
        "center": float(z["center"]),
        "lo": float(z["lo"]),
        "hi": float(z["hi"]),
        "inside": inside,
        "touch_count": z.get("touch_count"),
        "distinct_touch_days": z.get("distinct_touch_days"),
        "last_reaction_date": z.get("last_reaction_date"),
        "ZONE_ACTIVATED_AT": z.get("ZONE_ACTIVATED_AT"),
    }


def _compactness(z: dict[str, Any], atr: float) -> float | None:
    mem = list(z.get("members") or [])
    if len(mem) < 2 or not _finite(atr) or atr <= 0:
        return None
    xs = [float(m["price"]) for m in mem if _finite(m.get("price"))]
    if len(xs) < 2:
        return None
    return float((max(xs) - min(xs)) / atr)


def _spacing_days(z: dict[str, Any]) -> float | None:
    days = sorted({str(m.get("reaction_date") or "") for m in list(z.get("members") or []) if m.get("reaction_date")})
    if len(days) < 2:
        return None
    gaps = []
    for i in range(1, len(days)):
        # YYYYMMDD-style or ISO; Discovery dates are YYYY-MM-DD
        try:
            from datetime import date

            a = date.fromisoformat(days[i - 1])
            b = date.fromisoformat(days[i])
            gaps.append((b - a).days)
        except ValueError:
            continue
    if not gaps:
        return None
    return float(np.median(gaps))


def _already_broken(z: dict[str, Any], hist: list[dict[str, Any]], session_date: str) -> bool:
    role = str(z.get("role") or "")
    hi, lo = float(z["hi"]), float(z["lo"])
    act = str(z.get("ZONE_ACTIVATED_AT") or "")
    for d in hist:
        dt = str(d.get("date") or "")
        if dt >= session_date:
            continue
        if act and dt < act:
            continue
        c = float(d.get("close") or 0)
        if role == "RESISTANCE" and c > hi:
            return True
        if role == "SUPPORT" and c < lo:
            return True
    return False


def zone_audit_row(
    *,
    date: str,
    block: str,
    symbol: str,
    sector: str,
    atr: float,
    dbar: dict[str, Any] | None,
    pdc: float | None,
    snap: dict[str, Any],
    hist: list[dict[str, Any]],
    hist_atr: list[float],
) -> dict[str, Any]:
    res = list(snap["resistance_active"])
    sup = list(snap["support_active"])
    opn = float(dbar["open"]) if dbar and _finite(dbar.get("open")) else None
    px = float(pdc) if _finite(pdc) else opn
    nr_o = _nearest(res, opn if _finite(opn) else float("nan"), side="above", atr=atr)
    ns_o = _nearest(sup, opn if _finite(opn) else float("nan"), side="below", atr=atr)
    ov_r = _overlap_stats(res, atr)
    ov_s = _overlap_stats(sup, atr)
    stale_res = 0
    near_res = 0
    broken_res = 0
    compact_res: list[float] = []
    for z in res:
        if _finite(px) and _finite(atr) and atr > 0 and abs(float(z["center"]) - float(px)) > STALE_ATR * atr:
            stale_res += 1
        if _finite(px) and _finite(atr) and atr > 0 and abs(float(z["center"]) - float(px)) <= NEAR_ATR * atr:
            near_res += 1
        if _already_broken(z, hist, date):
            broken_res += 1
        cp = _compactness(z, atr)
        if _finite(cp):
            compact_res.append(float(cp))
    stale_sup = 0
    near_sup = 0
    broken_sup = 0
    for z in sup:
        if _finite(px) and _finite(atr) and atr > 0 and abs(float(z["center"]) - float(px)) > STALE_ATR * atr:
            stale_sup += 1
        if _finite(px) and _finite(atr) and atr > 0 and abs(float(z["center"]) - float(px)) <= NEAR_ATR * atr:
            near_sup += 1
        if _already_broken(z, hist, date):
            broken_sup += 1
    return {
        "date": date,
        "block": block,
        "symbol": symbol,
        "sector": sector,
        "atr": atr if _finite(atr) else None,
        "vol_regime": _vol_regime(atr, hist_atr),
        "open": opn,
        "prior_close": float(pdc) if _finite(pdc) else None,
        "n_res_active": len(res),
        "n_sup_active": len(sup),
        "n_res_2": snap["n_res_2"],
        "n_res_3": snap["n_res_3"],
        "n_res_4": snap["n_res_4"],
        "n_sup_2": snap["n_sup_2"],
        "n_sup_3": snap["n_sup_3"],
        "n_sup_4": snap["n_sup_4"],
        "n_res_single": len(snap["resistance_single"]),
        "n_sup_single": len(snap["support_single"]),
        "n_res_near_2atr": near_res,
        "n_sup_near_2atr": near_sup,
        "n_res_stale_3atr": stale_res,
        "n_sup_stale_3atr": stale_sup,
        "n_res_already_broken": broken_res,
        "n_sup_already_broken": broken_sup,
        "nearest_res_above_open_atr": nr_o.get("dist_atr"),
        "nearest_sup_below_open_atr": ns_o.get("dist_atr"),
        "open_inside_res": bool(nr_o.get("inside")),
        "open_inside_sup": bool(ns_o.get("inside")),
        "res_material_overlap_pairs": ov_r["n_material_overlap_pairs"],
        "sup_material_overlap_pairs": ov_s["n_material_overlap_pairs"],
        "res_nested_pairs": ov_r["n_nested_pairs"],
        "sup_nested_pairs": ov_s["n_nested_pairs"],
        "res_shared_members": ov_r["shared_reaction_members"],
        "sup_shared_members": ov_s["shared_reaction_members"],
        "res_min_center_dist_atr": ov_r["min_center_dist_atr"],
        "sup_min_center_dist_atr": ov_s["min_center_dist_atr"],
        "median_res_compactness_atr": float(np.median(compact_res)) if compact_res else None,
        "clutter": bool(len(res) + len(sup) > 6 or near_res + near_sup > 4),
    }


def reaction_salience_rows(reactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in reactions:
        atr = float(r.get("atr_at_confirm") or 0)
        rng = float(r.get("range") or 0)
        rf = float(r.get("rejection_frac") or 0)
        mag = (rng / atr) if atr > 0 else None
        move_away = (rf * rng / atr) if atr > 0 else None
        ordinary = bool(rf >= ORDINARY_REJECTION_FRAC and rf < WEAK_REJECTION_FRAC)
        pin = bool(rf >= WEAK_REJECTION_FRAC)
        weak = bool((move_away is not None and move_away < WEAK_MAG_ATR) or ordinary)
        out.append(
            {
                "symbol": r.get("symbol"),
                "reaction_date": r.get("reaction_date"),
                "role": r.get("role"),
                "price": r.get("price"),
                "rejection_frac": rf,
                "range": rng,
                "atr_at_confirm": atr if atr > 0 else None,
                "range_atr": mag,
                "move_away_atr": move_away,
                "ordinary_fluctuation": ordinary,
                "pin_like": pin,
                "structurally_weak": weak,
                "available_from": r.get("available_from"),
            }
        )
    return out


def reconstruct_daily(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    disc_set = set(disc)
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    nxt = next_dates_map(disc)
    symbols = list(bind.get("symbols") or [])
    sector_of = _sector_of(bind)
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} sr_design_audit", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=disc_set, forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "reason": "empty_minutes"}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])

    hist: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hist_atr: dict[str, list[float]] = defaultdict(list)
    reactions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    density_rows: list[dict[str, Any]] = []
    all_rx: list[dict[str, Any]] = []
    n_days = int(minutes["date"].nunique())
    snaps_keep: dict[tuple[str, str], dict[str, Any]] = {}

    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        date = str(day)
        block = str(date_to_block.get(date) or "")
        lookback = disc[: disc.index(date)] if date in disc_set else disc
        for sym, sg in g.groupby("symbol", sort=False):
            rec = prep_symbol(sg)
            if rec["n"] < 20:
                continue
            symbol = str(sym)
            atr = atr20(hist[symbol])
            snap = snapshot_zones(symbol=symbol, reactions=reactions[symbol], atr=atr, session_date=date, lookback_dates=lookback)
            dbar = daily_from_minutes(rec, date)
            pdc = hist[symbol][-1]["close"] if hist[symbol] else None
            row = zone_audit_row(
                date=date,
                block=block,
                symbol=symbol,
                sector=sector_of.get(symbol, "") or "",
                atr=atr,
                dbar=dbar,
                pdc=pdc,
                snap=snap,
                hist=hist[symbol],
                hist_atr=hist_atr[symbol],
            )
            density_rows.append(row)
            if dbar:
                hist[symbol].append(dbar)
                if _finite(atr) and atr > 0:
                    hist_atr[symbol].append(float(atr))
                nxt_d = nxt.get(date)
                for rx in same_day_reactions(dbar, atr, next_session=nxt_d):
                    tagged = {**rx, "symbol": symbol}
                    reactions[symbol].append(tagged)
                    all_rx.append(tagged)
        if di % 40 == 0 or di == n_days:
            print(f"DAILY {di}/{n_days} symbol_days={len(density_rows)} rx={len(all_rx)}", flush=True)

    salience = reaction_salience_rows(all_rx)
    sample_keys = choose_sample(density_rows, n=SAMPLE_N, seed=SAMPLE_SEED)
    sample_set = set(sample_keys)
    sample_snaps: dict[tuple[str, str], dict[str, Any]] = {}
    # Re-snapshot sampled days from stored hist/reactions (causal: zones use reactions available that morning).
    for row in density_rows:
        key = (str(row["symbol"]), str(row["date"]))
        if key not in sample_set:
            continue
        date = str(row["date"])
        symbol = str(row["symbol"])
        lookback = disc[: disc.index(date)] if date in disc_set else disc
        atr = atr20([d for d in hist[symbol] if str(d["date"]) < date])
        # reactions available: those with available_from <= date already filtered in snapshot
        rx = [r for r in reactions[symbol] if str(r.get("reaction_date") or "") < date]
        snap = snapshot_zones(symbol=symbol, reactions=rx, atr=atr, session_date=date, lookback_dates=lookback)
        sample_snaps[key] = {
            "snap": snap,
            "hist": [d for d in hist[symbol] if str(d["date"]) < date][-60:],
            "row": row,
            "atr": atr,
        }
        snaps_keep[key] = sample_snaps[key]

    return {
        "ok": True,
        "minutes": minutes,
        "hist": dict(hist),
        "reactions": dict(reactions),
        "density_rows": density_rows,
        "salience_rows": salience,
        "sample_keys": sample_keys,
        "sample_snaps": sample_snaps,
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "forbidden_loaded": False,
        "conf_n": 0,
        "val_n": 0,
    }


def choose_sample(rows: list[dict[str, Any]], *, n: int, seed: int) -> list[tuple[str, str]]:
    rng = np.random.default_rng(int(seed))
    eligible = [r for r in rows if r.get("block") in {"D1", "D2", "D3", "D4"} and _finite(r.get("atr"))]
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in eligible:
        by_block[str(r["block"])].append(r)
    per = max(1, n // 4)
    picked: list[tuple[str, str]] = []
    used: set[tuple[str, str]] = set()
    for b in ("D1", "D2", "D3", "D4"):
        pool = list(by_block.get(b) or [])
        if not pool:
            continue
        by_cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for r in pool:
            by_cell[(str(r.get("vol_regime") or "unknown"), str(r.get("sector") or "")[:24])].append(r)
        cells = list(by_cell.values())
        rng.shuffle(cells)
        take: list[dict[str, Any]] = []
        # round-robin cells for sector/vol spread
        while len(take) < min(per, len(pool)):
            progressed = False
            for cell in cells:
                if not cell or len(take) >= min(per, len(pool)):
                    continue
                i = int(rng.integers(0, len(cell)))
                take.append(cell.pop(i))
                progressed = True
            if not progressed:
                break
        for r in take:
            key = (str(r["symbol"]), str(r["date"]))
            if key not in used:
                used.add(key)
                picked.append(key)
    if len(picked) < n:
        rest = [r for r in eligible if (str(r["symbol"]), str(r["date"])) not in used]
        rng.shuffle(rest)
        for r in rest:
            if len(picked) >= n:
                break
            key = (str(r["symbol"]), str(r["date"]))
            used.add(key)
            picked.append(key)
    return picked[:n]


def summarize_density(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n_sd = len(rows)
    res_days = int(sum(int(r["n_res_active"] or 0) for r in rows))
    sup_days = int(sum(int(r["n_sup_active"] or 0) for r in rows))
    sd_with_overlap = sum(1 for r in rows if int(r.get("res_material_overlap_pairs") or 0) + int(r.get("sup_material_overlap_pairs") or 0) > 0)
    material_pairs = int(sum(int(r.get("res_material_overlap_pairs") or 0) + int(r.get("sup_material_overlap_pairs") or 0) for r in rows))
    nested_pairs = int(sum(int(r.get("res_nested_pairs") or 0) + int(r.get("sup_nested_pairs") or 0) for r in rows))
    shared = int(sum(int(r.get("res_shared_members") or 0) + int(r.get("sup_shared_members") or 0) for r in rows))
    clutter_n = sum(1 for r in rows if r.get("clutter"))
    already = int(sum(int(r.get("n_res_already_broken") or 0) + int(r.get("n_sup_already_broken") or 0) for r in rows))
    return {
        "symbol_days": n_sd,
        "resistance_zone_days": res_days,
        "support_zone_days": sup_days,
        "mean_res_per_symbol_day": (res_days / n_sd) if n_sd else None,
        "mean_sup_per_symbol_day": (sup_days / n_sd) if n_sd else None,
        "n_res_active": percentiles([float(r["n_res_active"]) for r in rows]),
        "n_sup_active": percentiles([float(r["n_sup_active"]) for r in rows]),
        "n_res_near_2atr": percentiles([float(r["n_res_near_2atr"]) for r in rows]),
        "n_sup_near_2atr": percentiles([float(r["n_sup_near_2atr"]) for r in rows]),
        "n_res_stale_3atr": percentiles([float(r["n_res_stale_3atr"]) for r in rows]),
        "n_sup_stale_3atr": percentiles([float(r["n_sup_stale_3atr"]) for r in rows]),
        "nearest_res_above_open_atr": percentiles([float(r["nearest_res_above_open_atr"]) for r in rows if _finite(r.get("nearest_res_above_open_atr"))]),
        "nearest_sup_below_open_atr": percentiles([float(r["nearest_sup_below_open_atr"]) for r in rows if _finite(r.get("nearest_sup_below_open_atr"))]),
        "res_material_overlap_pairs": percentiles([float(r["res_material_overlap_pairs"]) for r in rows]),
        "sup_material_overlap_pairs": percentiles([float(r["sup_material_overlap_pairs"]) for r in rows]),
        "res_min_center_dist_atr": percentiles([float(r["res_min_center_dist_atr"]) for r in rows if _finite(r.get("res_min_center_dist_atr"))]),
        "symbol_days_with_material_overlap": sd_with_overlap,
        "material_overlap_pair_total": material_pairs,
        "nested_pair_total": nested_pairs,
        "shared_reaction_members_total": shared,
        "clutter_symbol_days": clutter_n,
        "clutter_rate": (clutter_n / n_sd) if n_sd else None,
        "already_broken_zone_total": already,
        "open_inside_res_rate": (sum(1 for r in rows if r.get("open_inside_res")) / n_sd) if n_sd else None,
    }


def summarize_salience(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    weak = sum(1 for r in rows if r.get("structurally_weak"))
    ordinary = sum(1 for r in rows if r.get("ordinary_fluctuation"))
    pin = sum(1 for r in rows if r.get("pin_like"))
    return {
        "reaction_n": n,
        "structurally_weak_n": weak,
        "structurally_weak_rate": (weak / n) if n else None,
        "ordinary_fluctuation_n": ordinary,
        "ordinary_fluctuation_rate": (ordinary / n) if n else None,
        "pin_like_n": pin,
        "pin_like_rate": (pin / n) if n else None,
        "rejection_frac": percentiles([float(r["rejection_frac"]) for r in rows]),
        "move_away_atr": percentiles([float(r["move_away_atr"]) for r in rows if _finite(r.get("move_away_atr"))]),
        "range_atr": percentiles([float(r["range_atr"]) for r in rows if _finite(r.get("range_atr"))]),
        "note": "A reaction is a completed daily bar with range>=0.30 ATR and close in the outer 40% of the range. That includes ordinary down/up days, not only visual pins.",
    }
