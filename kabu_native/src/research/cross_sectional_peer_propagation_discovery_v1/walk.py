"""Walk synchronized 1-minute panel. Peer metrics exclude target. No displacement event."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.cross_sectional_peer_propagation_discovery_v1 import (
    CONTROL_RESERVOIR,
    MECHS,
    NEAR_SR_ATR,
    PEER_MIN_N,
    SESSION_FLAT,
    TRAIN_BLOCK,
)
from research.cross_sectional_peer_propagation_discovery_v1.features import lead_class, p_flags, peer_stats, sess_ret
from research.cross_sectional_peer_propagation_discovery_v1.match import key_of
from research.cross_sectional_peer_propagation_discovery_v1.outcomes import trading_path
from research.cross_sectional_peer_propagation_discovery_v1.peers import peer_universe, sector_of_map
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.native_direction_aligned_context_stack_v1.align import aligned_vwap_bps, sr_aligned
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol, to_min
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _sign_gap(open_px: Any, pdc: Any) -> tuple[str, float]:
    if not (_finite(open_px) and _finite(pdc)):
        return "na", 0.0
    d = float(open_px) - float(pdc)
    if d > 0:
        return "up", 1.0
    if d < 0:
        return "down", -1.0
    return "flat", 0.0


def _bucket(t: str) -> str:
    tm = to_min(t)
    if tm is None:
        return "na"
    return f"{(int(tm) // 30) * 30:04d}"


def _rng_rel(rec: dict[str, Any], session_idx: list[int], pos: int, n: int = 20) -> float:
    if pos < n:
        return float("nan")
    xs = [float(rec["rng"][j]) for j in session_idx[pos - n : pos] if _finite(rec["rng"][j])]
    if len(xs) < 5:
        return float("nan")
    return float(np.mean(xs))


def _sr_bin(sr: dict[str, Any]) -> str:
    if float(sr.get("breaking_ahead_sr") or 0) >= 1:
        return "breaking"
    ahead, behind = sr.get("ahead_sr_distance_atr"), sr.get("behind_sr_distance_atr")
    if _finite(ahead) and float(ahead) <= float(NEAR_SR_ATR):
        return "near_ahead"
    if _finite(behind) and float(behind) <= float(NEAR_SR_ATR):
        return "near_behind"
    if float(sr.get("ahead_sr_missing") or 0) >= 1 and float(sr.get("behind_sr_missing") or 0) >= 1:
        return "away"
    return "away"


def _load_panel(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    symbols = list(bind.get("symbols") or [])
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} peer_propagation", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=set(disc), forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "reason": "empty_minutes"}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    return {"ok": True, "minutes": minutes, "disc": disc, "symbols": symbols, "n_days": int(minutes["date"].nunique())}


def _groups_for_day(g, sector_of: dict[str, str], hist: dict[str, list], date: str, lookback: list[str]):
    _ = lookback
    per: dict[str, dict[str, Any]] = {}
    for sym, sg in g.groupby("symbol", sort=False):
        rec = prep_symbol(sg)
        if rec["n"] < 20:
            continue
        symbol = str(sym)
        session_idx = [i for i in range(int(rec["n"])) if not in_lunch(rec["t"][i])]
        if len(session_idx) < 8:
            continue
        atr = atr20(hist[symbol])
        dbar = daily_from_minutes(rec, date)
        opn = float(dbar["open"]) if dbar and _finite(dbar.get("open")) else float("nan")
        pdc = hist[symbol][-1]["close"] if hist[symbol] else None
        gap, gap_num = _sign_gap(opn, pdc)
        per[symbol] = {
            "rec": rec,
            "session_idx": session_idx,
            "pos_of": {rec["t"][i]: p for p, i in enumerate(session_idx)},
            "atr": atr,
            "sector": sector_of.get(symbol, "") or "",
            "gap": gap,
            "gap_num": gap_num,
            "dbar": dbar,
        }
    return per


def _timestamp_features(per: dict[str, dict[str, Any]], t: str, clock: ClockHistory) -> tuple[list[str], dict[str, Any], int]:
    names: list[str] = []
    r1 = []
    r3 = []
    r5 = []
    tv = []
    sec = []
    leak_n = 0
    for symbol, pack in per.items():
        pos = pack["pos_of"].get(t)
        if pos is None or pos < 5:
            continue
        rec = pack["rec"]
        i = pack["session_idx"][pos]
        if str(rec["t"][i]) >= SESSION_FLAT:
            continue
        c = rec["c"][i]
        if not _finite(c) or float(c) <= 0:
            continue
        a1 = sess_ret(rec["c"], pack["session_idx"], pos, 1)
        a3 = sess_ret(rec["c"], pack["session_idx"], pos, 3)
        a5 = sess_ret(rec["c"], pack["session_idx"], pos, 5)
        if not (_finite(a1) and _finite(a3)):
            continue
        pctl = clock.tv_pctl(symbol, t, float(rec["va"][i]) if _finite(rec["va"][i]) else float("nan"))
        names.append(symbol)
        r1.append(a1)
        r3.append(a3)
        r5.append(a5)
        tv.append(float(pctl) if pctl is not None else float("nan"))
        sec.append(pack["sector"])
    if not names:
        return [], {}, 0
    return names, {"r1": np.asarray(r1, float), "r3": np.asarray(r3, float), "r5": np.asarray(r5, float), "tv": np.asarray(tv, float), "sec": sec}, leak_n


def _rows_at_t(
    names: list[str],
    arr: dict[str, Any],
    per: dict[str, dict[str, Any]],
    *,
    t: str,
    date: str,
    block: str,
    direction: str,
    dsgn: int,
) -> tuple[list[dict[str, Any]], int]:
    n = len(names)
    aligned1 = dsgn * arr["r1"]
    aligned3 = dsgn * arr["r3"]
    aligned5 = dsgn * arr["r5"]
    sec = arr["sec"]
    leak = 0
    rows: list[dict[str, Any]] = []
    by_sec: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(sec):
        by_sec[s].append(i)
    peer_med1 = np.full(n, np.nan)
    peer_med3 = np.full(n, np.nan)
    peer_br1 = np.full(n, np.nan)
    peer_br3 = np.full(n, np.nan)
    peer_tv = np.full(n, np.nan)
    peer_n = np.zeros(n, dtype=int)
    for _sec, idxs in by_sec.items():
        mask = np.zeros(n, dtype=bool)
        mask[idxs] = True
        st1 = peer_stats(aligned1, arr["tv"], include_mask=mask)
        st3 = peer_stats(aligned3, arr["tv"], include_mask=mask)
        peer_med1[mask] = st1["median"][mask]
        peer_med3[mask] = st3["median"][mask]
        peer_br1[mask] = st1["breadth"][mask]
        peer_br3[mask] = st3["breadth"][mask]
        peer_tv[mask] = st3["tv_breadth"][mask]
        peer_n[mask] = st3["peer_n"][mask]
        leak += int(st3["target_in_peer_n"].sum())
    mkt_mask = np.ones(n, dtype=bool)
    mk3 = peer_stats(aligned3, arr["tv"], include_mask=mkt_mask)
    for i, symbol in enumerate(names):
        if int(peer_n[i]) < int(PEER_MIN_N):
            continue
        pack = per[symbol]
        pos = pack["pos_of"][t]
        rec = pack["rec"]
        tgt1, tgt3, tgt5 = float(aligned1[i]), float(aligned3[i]), float(aligned5[i])
        pr1, pr3 = float(peer_med1[i]), float(peer_med3[i])
        rows.append(
            {
                "symbol": symbol,
                "date": date,
                "block": block,
                "t": t,
                "pos": pos,
                "i": pack["session_idx"][pos],
                "direction": direction,
                "DIR": dsgn,
                "sector": pack["sector"],
                "bucket": _bucket(t),
                "tod_min": float((to_min(t) or 0) - 9 * 60),
                "gap": pack["gap"],
                "gap_num": pack["gap_num"],
                "rng_rel": _rng_rel(rec, pack["session_idx"], pos),
                "peer_n": int(peer_n[i]),
                "peer_ret_1m": pr1,
                "peer_ret_3m": pr3,
                "peer_breadth_1m": float(peer_br1[i]),
                "peer_breadth_3m": float(peer_br3[i]),
                "peer_tv_breadth": float(peer_tv[i]),
                "target_ret_1m": tgt1,
                "target_ret_3m": tgt3,
                "target_ret_5m": tgt5,
                "peer_minus_target_1m": pr1 - tgt1 if _finite(pr1) else float("nan"),
                "peer_minus_target_3m": pr3 - tgt3 if _finite(pr3) else float("nan"),
                "mkt_ret_3m": float(mk3["median"][i]),
                "mkt_breadth_3m": float(mk3["breadth"][i]),
                "tv_clock_pctl": float(arr["tv"][i]),
                "lead": lead_class(pr1, tgt1, pr3, tgt3),
            }
        )
    return rows, leak


def harvest_d1_metrics(bind: dict[str, Any], loaded: dict[str, Any]) -> dict[str, list[float]]:
    minutes = loaded["minutes"]
    disc = loaded["disc"]
    blocks = dict(bind.get("blocks") or {})
    date_to_block = dict(blocks.get("date_to_block") or {})
    sector_of = sector_of_map(bind)
    clock = ClockHistory()
    hist: dict[str, list] = defaultdict(list)
    out: dict[str, list[float]] = {"peer_ret_3m": [], "peer_breadth_3m": [], "peer_minus_target_3m": [], "peer_tv_breadth": []}
    d1_dates = [d for d in disc if str(date_to_block.get(d) or "") == TRAIN_BLOCK]
    n_days = len(d1_dates)
    print(f"D1_METRIC_PASS days={n_days}", flush=True)
    by_date = minutes[minutes["date"].isin(d1_dates)]
    for di, (day, g) in enumerate(by_date.groupby("date", sort=True), start=1):
        date = str(day)
        lookback = disc[: disc.index(date)] if date in set(disc) else disc
        per = _groups_for_day(g, sector_of, hist, date, lookback)
        times = sorted({t for p in per.values() for t in p["pos_of"] if t < SESSION_FLAT})
        for t in times:
            names, arr, _ = _timestamp_features(per, t, clock)
            if not names:
                continue
            for direction, dsgn in (("BULLISH", 1), ("BEARISH", -1)):
                rows, _ = _rows_at_t(names, arr, per, t=t, date=date, block=TRAIN_BLOCK, direction=direction, dsgn=dsgn)
                for r in rows:
                    for k in out:
                        v = r.get(k)
                        if _finite(v):
                            out[k].append(float(v))
        for symbol, pack in per.items():
            rec = pack["rec"]
            clock.commit_day(symbol, rec, pack["session_idx"])
            dbar = pack.get("dbar")
            if dbar:
                hist[symbol].append(dbar)
        if di % 20 == 0 or di == n_days:
            print(f"D1_METRICS {di}/{n_days} n={len(out['peer_ret_3m'])}", flush=True)
    return out


def emit_events(bind: dict[str, Any], loaded: dict[str, Any], freeze: dict[str, Any]) -> dict[str, Any]:
    minutes = loaded["minutes"]
    disc = loaded["disc"]
    symbols = loaded["symbols"]
    blocks = dict(bind.get("blocks") or {})
    date_to_block = dict(blocks.get("date_to_block") or {})
    nxt = next_dates_map(disc)
    sector_of = sector_of_map(bind)
    universe = peer_universe(symbols, sector_of)
    clock = ClockHistory()
    hist: dict[str, list] = defaultdict(list)
    reactions: dict[str, list] = defaultdict(list)
    swing_st: dict[str, dict[str, Any]] = defaultdict(new_swing_state)
    rng = np.random.default_rng(20260917)
    treated: dict[str, list] = {m: [] for m in MECHS}
    sim: dict[str, list] = {m: [] for m in MECHS}
    already: dict[str, list] = {m: [] for m in MECHS}
    controls: dict[str, dict] = {m: defaultdict(list) for m in MECHS}
    counts: dict[str, dict] = {m: {} for m in MECHS}
    leak_n = 0
    unavailable_n = 0
    row_n = 0
    n_days = loaded["n_days"]
    nonlocal_same: list[int] = []

    def attach_diag(row: dict[str, Any], pack: dict[str, Any], rec: dict[str, Any], i: int) -> None:
        c = float(rec["c"][i])
        sr = sr_aligned(pack["snap"], close=c, atr=float(pack["atr"]) if _finite(pack.get("atr")) else float("nan"), direction=row["direction"])
        row["aligned_vwap_bps"] = aligned_vwap_bps(c, rec["vw"][i], row["direction"])
        row.update(sr)
        row["sr_bin"] = _sr_bin(sr)

    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        date = str(day)
        block = str(date_to_block.get(date) or "")
        lookback = disc[: disc.index(date)] if date in set(disc) else disc
        per = _groups_for_day(g, sector_of, hist, date, lookback)
        for symbol, pack in per.items():
            pack["snap"] = snapshot_zones(
                symbol=symbol,
                reactions=reactions[symbol],
                atr=pack["atr"],
                session_date=date,
                lookback_dates=lookback,
                hist=hist[symbol],
            )
        times = sorted({t for p in per.values() for t in p["pos_of"] if t < SESSION_FLAT})
        prev_flag: dict[tuple[str, str], dict[str, bool]] = {}
        day_state: dict[tuple[str, str, str], dict[str, float]] = {}
        day_onsets: list[dict[str, Any]] = []
        for t in times:
            names, arr, _ = _timestamp_features(per, t, clock)
            if not names:
                continue
            for direction, dsgn in (("BULLISH", 1), ("BEARISH", -1)):
                rows, leak = _rows_at_t(names, arr, per, t=t, date=date, block=block, direction=direction, dsgn=dsgn)
                leak_n += leak
                for r in rows:
                    row_n += 1
                    flags = p_flags(r, freeze)
                    r["P1"], r["P2"], r["P3"] = flags["P1"], flags["P2"], flags["P3"]
                    day_state[(t, r["symbol"], direction)] = {
                        "peer_ret_3m": r["peer_ret_3m"],
                        "peer_breadth_3m": r["peer_breadth_3m"],
                        "peer_minus_target_3m": r["peer_minus_target_3m"],
                    }
                    key = (r["symbol"], direction)
                    prev = prev_flag.get(key) or {m: False for m in MECHS}
                    pack = per[r["symbol"]]
                    rec = pack["rec"]
                    path_cache: dict[str, Any] | None = None

                    def get_path() -> dict[str, Any]:
                        nonlocal path_cache
                        if path_cache is None:
                            path_cache = trading_path(rec, pack["session_idx"], int(r["pos"]), int(r["DIR"]))
                        return path_cache

                    for m in MECHS:
                        now = bool(flags[m])
                        onset = now and not prev[m]
                        if onset:
                            attach_diag(r, pack, rec, int(r["i"]))
                            path = get_path()
                            r.update(path)
                            if path.get("same_bar_outcome"):
                                nonlocal_same.append(1)
                            if not r.get("primary_complete"):
                                continue
                            item = dict(r)
                            item["mech"] = m
                            item["onset"] = True
                            day_onsets.append(item)
                            if r["lead"] == "TRUE_PEER_LEAD":
                                treated[m].append(item)
                            elif r["lead"] == "SIMULTANEOUS_MOVE":
                                sim[m].append(item)
                            elif r["lead"] == "TARGET_ALREADY_LED":
                                already[m].append(item)
                        if not now:
                            ck = key_of(r)
                            xs = controls[m][ck]
                            counts[m][ck] = int(counts[m].get(ck) or 0) + 1
                            n_seen = int(counts[m][ck])
                            cap = int(CONTROL_RESERVOIR)
                            take = len(xs) < cap or int(rng.integers(0, n_seen)) < cap
                            if take:
                                path = get_path()
                                if path.get("primary_complete"):
                                    cr = dict(r)
                                    cr.update(path)
                                    if len(xs) < cap:
                                        xs.append(cr)
                                    else:
                                        xs[int(rng.integers(0, cap))] = cr
                    prev_flag[key] = {m: bool(flags[m]) for m in MECHS}
        t_index = {tt: k for k, tt in enumerate(times)}
        for item in day_onsets:
            t0 = item["t"]
            k0 = t_index.get(t0)
            for off, name in ((-5, "tm5"), (-3, "tm3"), (-1, "tm1"), (0, "t0"), (1, "tp1"), (3, "tp3"), (5, "tp5")):
                keyn = f"off_{name}"
                item[keyn] = None
                if k0 is None:
                    continue
                kk = k0 + off
                if kk < 0 or kk >= len(times):
                    continue
                st = day_state.get((times[kk], item["symbol"], item["direction"]))
                if st:
                    item[keyn] = st
        for symbol, pack in per.items():
            rec = pack["rec"]
            clock.commit_day(symbol, rec, pack["session_idx"])
            dbar = pack.get("dbar")
            if dbar:
                hist[symbol].append(dbar)
                atr_c = atr20(hist[symbol])
                nxt_d = nxt.get(date)
                for rx in step_swings(swing_st[symbol], dbar, atr=atr_c, next_session=nxt_d, symbol=symbol):
                    reactions[symbol].append(rx)
        if di % 20 == 0 or di == n_days:
            print(
                f"WALK {di}/{n_days} rows={row_n} P1={len(treated['P1'])} P2={len(treated['P2'])} P3={len(treated['P3'])}",
                flush=True,
            )
    return {
        "ok": True,
        "universe": universe,
        "treated": treated,
        "simultaneous": sim,
        "already": already,
        "controls": controls,
        "TARGET_INCLUDED_IN_PEER_METRIC_N": int(leak_n),
        "unavailable_n": unavailable_n,
        "feature_row_n": row_n,
        "same_bar_outcome_n": len(nonlocal_same),
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "future_peer_information_n": 0,
        "displacement_used": False,
    }
