"""Causal RCA walk. Frozen P flags. First-onset episodes. Next-open execution. MA/VWAP/TV diagnostics."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1 import CONTROL_RESERVOIR, MECHS, SESSION_FLAT
from research.cross_sectional_peer_propagation_discovery_v1.features import p_flags
from research.cross_sectional_peer_propagation_discovery_v1.match import key_of
from research.cross_sectional_peer_propagation_discovery_v1.outcomes import trading_path
from research.cross_sectional_peer_propagation_discovery_v1.peers import peer_universe, sector_of_map
from research.cross_sectional_peer_propagation_discovery_v1.walk import (
    _groups_for_day,
    _load_panel,
    _rows_at_t,
    _sr_bin,
    _timestamp_features,
)
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.native_direction_aligned_context_stack_v1.align import aligned_vwap_bps, sr_aligned
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory
from research.peer_propagation_mechanism_rca_v1 import ABLATION, CONFIRM_LOOKAHEAD_M, STREAMS
from research.peer_propagation_mechanism_rca_v1.execute import execution_path, remaining_from_close
from research.peer_propagation_mechanism_rca_v1.ma import (
    aligned_cross,
    aligned_reclaim,
    aligned_vwap_reclaim,
    bar_touches,
    ma_states,
    sma_pack,
)
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones

TV_OFF = (0, 1, 2, 3, 5)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _stream_flags(flags: dict[str, bool]) -> dict[str, bool]:
    return {"P1": bool(flags["P1"]), "P2": bool(flags["P2"]), "P3": bool(flags["P3"]), ABLATION: bool(flags["high_c"])}


def _attach_ma(row: dict[str, Any], sma: dict[str, np.ndarray], pos: int, close: float) -> None:
    s5, s25, s75 = sma["sma5"], sma["sma25"], sma["sma75"]
    prev25 = s25[pos - 1] if pos >= 1 else float("nan")
    prev75 = s75[pos - 1] if pos >= 1 else float("nan")
    row.update(ma_states(close, s5[pos], s25[pos], s75[pos], prev25, prev75, int(row["DIR"])))


def _attach_confirm(
    row: dict[str, Any],
    rec: dict[str, Any],
    session_idx: list[int],
    sma: dict[str, np.ndarray],
    pos: int,
    clock: ClockHistory,
    symbol: str,
) -> None:
    sign = int(row["DIR"])
    n_sess = len(session_idx)
    i0 = session_idx[pos]
    row["vwap_aligned"] = bool(_finite(row.get("aligned_vwap_bps")) and float(row["aligned_vwap_bps"]) > 0)
    for name in (
        "delay_sma5_reclaim",
        "delay_sma25_touch",
        "delay_sma25_reclaim",
        "delay_sma75_touch",
        "delay_sma5_sma25_cross",
        "delay_vwap_reclaim",
        "delay_tv_expand",
        "moved_before_sma5_reclaim_bps",
        "moved_before_sma25_reclaim_bps",
        "moved_before_vwap_reclaim_bps",
        "moved_before_tv_expand_bps",
        "remain10_sma5_reclaim",
        "remain10_sma25_reclaim",
        "remain10_vwap_reclaim",
        "remain10_tv_expand",
        "remain10_next_open",
    ):
        row[name] = None
    tv0 = row.get("tv_clock_pctl")
    c0 = rec["c"][i0]
    for k, lab in ((0, "tv_t"), (1, "tv_tp1"), (2, "tv_tp2"), (3, "tv_tp3"), (5, "tv_tp5")):
        row[lab] = None
        if pos + k >= n_sess:
            continue
        j = session_idx[pos + k]
        if str(rec["t"][j]) >= SESSION_FLAT:
            continue
        pctl = clock.tv_pctl(symbol, rec["t"][j], float(rec["va"][j]) if _finite(rec["va"][j]) else float("nan"))
        row[lab] = float(pctl) if pctl is not None else None
        if k == 0 and row[lab] is None and _finite(tv0):
            row[lab] = float(tv0)
    if row.get("xo_ret_10m_bps") is not None:
        row["remain10_next_open"] = row.get("xo_ret_10m_bps")
    last = min(n_sess - 1, pos + int(CONFIRM_LOOKAHEAD_M))
    s5, s25, s75 = sma["sma5"], sma["sma25"], sma["sma75"]
    for k in range(1, last - pos + 1):
        p = pos + k
        j = session_idx[p]
        jp = session_idx[p - 1]
        if str(rec["t"][j]) >= SESSION_FLAT:
            break
        cj, cprev = rec["c"][j], rec["c"][jp]
        moved = None
        if _finite(cj) and _finite(c0) and float(c0) != 0:
            moved = float(sign) * (float(cj) / float(c0) - 1.0) * 1e4
        if row["delay_sma5_reclaim"] is None and aligned_reclaim(cprev, s5[p - 1], cj, s5[p], sign):
            row["delay_sma5_reclaim"] = k
            row["moved_before_sma5_reclaim_bps"] = moved
            row["remain10_sma5_reclaim"] = remaining_from_close(rec, session_idx, p, sign).get("ret_10m_bps")
        if row["delay_sma25_reclaim"] is None and aligned_reclaim(cprev, s25[p - 1], cj, s25[p], sign):
            row["delay_sma25_reclaim"] = k
            row["moved_before_sma25_reclaim_bps"] = moved
            row["remain10_sma25_reclaim"] = remaining_from_close(rec, session_idx, p, sign).get("ret_10m_bps")
        if row["delay_sma25_touch"] is None and bar_touches(rec["h"][j], rec["l"][j], s25[p]):
            row["delay_sma25_touch"] = k
        if row["delay_sma75_touch"] is None and bar_touches(rec["h"][j], rec["l"][j], s75[p]):
            row["delay_sma75_touch"] = k
        if row["delay_sma5_sma25_cross"] is None and aligned_cross(s5[p - 1], s25[p - 1], s5[p], s25[p], sign):
            row["delay_sma5_sma25_cross"] = k
        if row["delay_vwap_reclaim"] is None and aligned_vwap_reclaim(cprev, rec["vw"][jp], cj, rec["vw"][j], sign):
            row["delay_vwap_reclaim"] = k
            row["moved_before_vwap_reclaim_bps"] = moved
            row["remain10_vwap_reclaim"] = remaining_from_close(rec, session_idx, p, sign).get("ret_10m_bps")
        if row["delay_tv_expand"] is None and k in TV_OFF[1:]:
            pctl = row.get({1: "tv_tp1", 2: "tv_tp2", 3: "tv_tp3", 5: "tv_tp5"}.get(k) or "")
            if pctl is None:
                pctl = clock.tv_pctl(symbol, rec["t"][j], float(rec["va"][j]) if _finite(rec["va"][j]) else float("nan"))
            if _finite(pctl) and _finite(tv0) and float(pctl) > float(tv0):
                row["delay_tv_expand"] = k
                row["moved_before_tv_expand_bps"] = moved
                row["remain10_tv_expand"] = remaining_from_close(rec, session_idx, p, sign).get("ret_10m_bps")
        elif row["delay_tv_expand"] is None and k not in TV_OFF:
            pctl = clock.tv_pctl(symbol, rec["t"][j], float(rec["va"][j]) if _finite(rec["va"][j]) else float("nan"))
            if _finite(pctl) and _finite(tv0) and float(pctl) > float(tv0):
                row["delay_tv_expand"] = k
                row["moved_before_tv_expand_bps"] = moved
                row["remain10_tv_expand"] = remaining_from_close(rec, session_idx, p, sign).get("ret_10m_bps")


def emit_rca(bind: dict[str, Any], freeze: dict[str, Any]) -> dict[str, Any]:
    loaded = _load_panel(bind)
    if not loaded.get("ok"):
        return {"ok": False, "reason": loaded.get("reason")}
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
    treated: dict[str, list] = {m: [] for m in STREAMS}
    sim: dict[str, list] = {m: [] for m in MECHS}
    already: dict[str, list] = {m: [] for m in MECHS}
    all_onsets: dict[str, list] = {m: [] for m in STREAMS}
    controls: dict[str, dict] = {m: defaultdict(list) for m in STREAMS}
    counts: dict[str, dict] = {m: {} for m in STREAMS}
    qualified_n = {m: 0 for m in STREAMS}
    episode_n = {m: 0 for m in STREAMS}
    durations = {m: [] for m in STREAMS}
    leak_n = 0
    row_n = 0
    same_bar_n = 0
    future_sel_n = 0
    n_days = loaded["n_days"]

    def attach_diag(row: dict[str, Any], pack: dict[str, Any], rec: dict[str, Any], i: int) -> None:
        c = float(rec["c"][i])
        sr = sr_aligned(
            pack["snap"],
            close=c,
            atr=float(pack["atr"]) if _finite(pack.get("atr")) else float("nan"),
            direction=row["direction"],
        )
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
            sc = np.asarray([pack["rec"]["c"][i] for i in pack["session_idx"]], dtype=float)
            pack["sma"] = sma_pack(sc)
        times = sorted({t for p in per.values() for t in p["pos_of"] if t < SESSION_FLAT})
        prev_flag: dict[tuple[str, str], dict[str, bool]] = {}
        open_ep: dict[tuple[str, str, str], dict[str, Any]] = {}
        open_dur: dict[tuple[str, str, str], int] = {}
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
                    r["LAG"] = bool(flags["high_c"])
                    r["sector_excess"] = (
                        float(r["peer_ret_3m"]) - float(r["mkt_ret_3m"])
                        if _finite(r.get("peer_ret_3m")) and _finite(r.get("mkt_ret_3m"))
                        else float("nan")
                    )
                    day_state[(t, r["symbol"], direction)] = {
                        "peer_ret_3m": r["peer_ret_3m"],
                        "peer_breadth_3m": r["peer_breadth_3m"],
                        "peer_minus_target_3m": r["peer_minus_target_3m"],
                        "mkt_ret_3m": r["mkt_ret_3m"],
                        "sector_excess": r["sector_excess"],
                    }
                    key = (r["symbol"], direction)
                    nows = _stream_flags(flags)
                    prev = prev_flag.get(key) or {m: False for m in STREAMS}
                    pack = per[r["symbol"]]
                    rec = pack["rec"]
                    close_cache: dict[str, Any] | None = None
                    exec_cache: dict[str, Any] | None = None

                    def get_close() -> dict[str, Any]:
                        nonlocal close_cache
                        if close_cache is None:
                            close_cache = trading_path(rec, pack["session_idx"], int(r["pos"]), int(r["DIR"]))
                        return close_cache

                    def get_exec() -> dict[str, Any]:
                        nonlocal exec_cache
                        if exec_cache is None:
                            exec_cache = execution_path(rec, pack["session_idx"], int(r["pos"]), int(r["DIR"]))
                        return exec_cache

                    for m in STREAMS:
                        now = bool(nows[m])
                        if now:
                            qualified_n[m] += 1
                        ek = (r["symbol"], direction, m)
                        if now and not prev[m]:
                            episode_n[m] += 1
                            open_dur[ek] = 1
                            if not get_close().get("primary_complete"):
                                open_ep[ek] = {"incomplete": True}
                                continue
                            item = dict(r)
                            item["mech"] = m
                            item["onset"] = True
                            item.update(get_close())
                            item.update(get_exec())
                            if item.get("same_bar_outcome"):
                                same_bar_n += 1
                            if m in MECHS:
                                attach_diag(item, pack, rec, int(r["i"]))
                                _attach_ma(item, pack["sma"], int(r["pos"]), float(rec["c"][int(r["i"])]))
                                _attach_confirm(item, rec, pack["session_idx"], pack["sma"], int(r["pos"]), clock, r["symbol"])
                            item["id"] = (item["date"], item["t"], item["symbol"], item["direction"])
                            open_ep[ek] = item
                            day_onsets.append(item)
                            all_onsets[m].append(item)
                            if m in MECHS:
                                if r["lead"] == "TRUE_PEER_LEAD":
                                    treated[m].append(item)
                                elif r["lead"] == "SIMULTANEOUS_MOVE":
                                    sim[m].append(item)
                                elif r["lead"] == "TARGET_ALREADY_LED":
                                    already[m].append(item)
                            elif r["lead"] == "TRUE_PEER_LEAD":
                                treated[m].append(item)
                        elif now and prev[m]:
                            open_dur[ek] = int(open_dur.get(ek) or 0) + 1
                        elif (not now) and prev[m]:
                            dur = int(open_dur.pop(ek, 0) or 0)
                            durations[m].append(dur)
                            ep = open_ep.pop(ek, None)
                            if isinstance(ep, dict) and not ep.get("incomplete"):
                                ep["episode_duration"] = dur
                        if not now:
                            ck = key_of(r)
                            xs = controls[m][ck]
                            counts[m][ck] = int(counts[m].get(ck) or 0) + 1
                            n_seen = int(counts[m][ck])
                            cap = int(CONTROL_RESERVOIR)
                            take = len(xs) < cap or int(rng.integers(0, n_seen)) < cap
                            if take:
                                path = get_close()
                                if path.get("primary_complete"):
                                    cr = dict(r)
                                    cr.update(path)
                                    if m in MECHS:
                                        cr.update(get_exec())
                                    if len(xs) < cap:
                                        xs.append(cr)
                                    else:
                                        xs[int(rng.integers(0, cap))] = cr
                    prev_flag[key] = dict(nows)
        for ek, dur in list(open_dur.items()):
            durations[ek[2]].append(int(dur))
            ep = open_ep.get(ek)
            if isinstance(ep, dict) and not ep.get("incomplete"):
                ep["episode_duration"] = int(dur)
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
                f"RCA_WALK {di}/{n_days} rows={row_n} P1_ep={episode_n['P1']} P1_true={len(treated['P1'])}",
                flush=True,
            )
    return {
        "ok": True,
        "universe": universe,
        "treated": treated,
        "simultaneous": sim,
        "already": already,
        "all_onsets": all_onsets,
        "controls": controls,
        "qualified_n": qualified_n,
        "episode_n": episode_n,
        "durations": durations,
        "TARGET_INCLUDED_IN_PEER_METRIC_N": int(leak_n),
        "feature_row_n": row_n,
        "same_bar_outcome_n": same_bar_n,
        "FUTURE_EPISODE_SELECTION_N": int(future_sel_n),
        "n_days": n_days,
        "n_symbols_loaded": int(minutes["symbol"].nunique()),
        "future_peer_information_n": 0,
        "displacement_used": False,
        "ma_used_as_peer_signal": False,
    }
