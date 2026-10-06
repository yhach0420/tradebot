"""Causal 1-minute lag / catch-up and allowed-pair onsets. Data at T only. No atlas episode classifier."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from research.behavior_group_sequence_mechanism_v1 import (
    ENTRY_CUTOFF,
    EPISODE_GAP_MIN,
    FAVOR_BPS,
    LAG_RANK_FRAC,
    LEAD_STREAK_MIN,
    LOOKBACK_BARS,
    MIN_SECTOR_N,
    PATH_BARS,
)
from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, interval_crosses_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.one_minute_native_playbook_discovery_v1.states import entry_ok, features_at, prep_symbol, to_min


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _med(xs: list[float]) -> float:
    arr = np.asarray([x for x in xs if _finite(x)], dtype=float)
    if arr.size == 0:
        return float("nan")
    return float(np.median(arr))


def _bps(exit_px: float, entry_px: float) -> float | None:
    if not np.isfinite(exit_px) or not np.isfinite(entry_px) or entry_px == 0:
        return None
    return float((exit_px / entry_px - 1.0) * 10_000.0)


def _rank_desc(vals: list[tuple[str, float]]) -> dict[str, int]:
    ordered = sorted(vals, key=lambda kv: (-kv[1], kv[0]))
    return {sym: i + 1 for i, (sym, _) in enumerate(ordered)}


def _impulse_day_stats(rec: dict[str, Any], impulse_idx: list[int]) -> dict[str, int]:
    cont = rev = fav = 0
    for i in impulse_idx:
        px = rec["c"][i]
        if not np.isfinite(px) or px <= 0:
            continue
        last = min(i + 5, rec["n"] - 1)
        fav_i = adv_i = None
        end_c = rec["c"][last]
        for k in range(i, last + 1):
            hb = _bps(rec["h"][k], px)
            lb = _bps(rec["l"][k], px)
            if fav_i is None and hb is not None and hb >= FAVOR_BPS:
                fav_i = k
            if adv_i is None and lb is not None and lb <= -FAVOR_BPS:
                adv_i = k
        if fav_i is not None and (adv_i is None or fav_i <= adv_i):
            fav += 1
        if np.isfinite(end_c) and end_c > px:
            cont += 1
        elif np.isfinite(end_c) and end_c < px:
            rev += 1
    return {"impulse_n": len(impulse_idx), "continuation_n": cont, "reversal_n": rev, "favor_first_n": fav}


def attach_path(ep: dict[str, Any], rec: dict[str, Any], sec_at: dict[str, dict[str, dict[str, Any]]]) -> None:
    entry_t = str(ep.get("event_time") or "")
    ie = rec["idx"].get(entry_t)
    px = rec["o"][ie] if ie is not None else float("nan")
    ep["x0_entry_open"] = float(px) if ie is not None and np.isfinite(px) else None
    fwd = []
    mfe = mae = None
    mfe_i = mae_i = None
    sec = str(ep.get("sector") or "")
    feat_t = str(ep.get("feature_bar") or "")
    if ie is not None and np.isfinite(px) and px > 0:
        last = min(ie + PATH_BARS, len(rec["t"]) - 1)
        for k in range(ie, last + 1):
            hh = rec["t"][k]
            if interval_crosses_lunch(entry_t, hh):
                break
            st = (sec_at.get(hh) or {}).get(sec) or {}
            cl = rec["c"][k]
            vw = rec["vw"][k]
            above_vw = bool(np.isfinite(vw) and np.isfinite(cl) and cl > vw)
            r1 = rec["r1"][k]
            sec_r1 = st.get("r1")
            rs1 = (float(r1) - float(sec_r1)) if _finite(r1) and _finite(sec_r1) else float("nan")
            sess = rec["sess"][k]
            sec_sess = st.get("sess")
            lag_dist = (float(sess) - float(sec_sess)) if _finite(sess) and _finite(sec_sess) else float("nan")
            fwd.append(
                (
                    hh,
                    float(rec["o"][k]),
                    float(rec["h"][k]),
                    float(rec["l"][k]),
                    float(cl) if np.isfinite(cl) else float("nan"),
                    float(vw) if np.isfinite(vw) else None,
                    above_vw,
                    float(ep.get("hi20") or np.nan),
                    float(sec_r1) if _finite(sec_r1) else float("nan"),
                    float(st.get("leader_r1")) if _finite(st.get("leader_r1")) else float("nan"),
                    rs1,
                    lag_dist,
                    str(st.get("leader") or ""),
                )
            )
            hbps = _bps(rec["h"][k], px)
            lbps = _bps(rec["l"][k], px)
            step = k - ie
            if hbps is not None and (mfe is None or hbps > mfe):
                mfe, mfe_i = hbps, step
            if lbps is not None and (mae is None or lbps < mae):
                mae, mae_i = lbps, step
    ep["fwd_bars"] = fwd
    ep["mfe_bps"] = mfe
    ep["mae_bps"] = mae
    ep["time_to_mfe_min"] = mfe_i
    ep["time_to_mae_min"] = mae_i
    fav_i = adv_i = None
    if ie is not None and np.isfinite(px) and px > 0:
        for step, row in enumerate(fwd):
            hbps = _bps(row[2], px)
            lbps = _bps(row[3], px)
            if fav_i is None and hbps is not None and hbps >= FAVOR_BPS:
                fav_i = step
            if adv_i is None and lbps is not None and lbps <= -FAVOR_BPS:
                adv_i = step
    ep["time_to_favorable"] = fav_i
    ep["time_to_failure"] = adv_i
    ep["favor_first"] = bool(fav_i is not None and (adv_i is None or fav_i <= adv_i))
    ep["adverse_first"] = bool(adv_i is not None and (fav_i is None or adv_i < fav_i))
    ep["outcomes_attached"] = True


def process_day(
    *,
    date: str,
    g: pd.DataFrame,
    sector_of: dict[str, str],
    date_to_block: dict[str, str],
    prev_close: dict[str, float],
) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {}
    for sym, sg in g.groupby("symbol", sort=False):
        rec = prep_symbol(sg)
        if rec["n"] < LOOKBACK_BARS:
            continue
        rec["symbol"] = str(sym)
        rec["sector"] = sector_of.get(str(sym)) or ""
        per[str(sym)] = rec
    if len(per) < 20:
        return {"events": [], "daily": [], "leader_rows": [], "lag_rows": [], "last_close": {}}

    times = sorted({t for rec in per.values() for t in rec["t"]}, key=lambda x: to_min(x) or 0)
    present_at: dict[str, list[str]] = defaultdict(list)
    for rec in per.values():
        for t in rec["t"]:
            present_at[t].append(rec["symbol"])

    block = date_to_block.get(date) or ""
    last_imp: dict[str, int] = {}
    lead_streak: dict[str, int] = {}
    prev_rel: dict[str, float] = {}
    prev_r1: dict[str, float] = {}
    prev_pull: dict[str, bool] = {}
    prev_vol: dict[str, bool] = {}
    prev_flag_break: dict[str, bool] = {}
    last_emit: dict[tuple[str, str], int] = {}
    last_leader: dict[str, str] = {}
    lead_acc: dict[str, dict[str, Any]] = {}
    impulse_idx: dict[str, list[int]] = defaultdict(list)
    sec_at: dict[str, dict[str, dict[str, Any]]] = {}
    leader_rows: list[dict[str, Any]] = []
    lag_rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    opening: dict[str, dict[str, Any]] = {}
    catchup_n = defaultdict(int)
    lag_min_n = 0
    lead_change_n = 0

    def maybe_emit(seq: str, *, feat: dict[str, Any], rec: dict[str, Any], t: str, tm: int, extra: dict[str, Any]) -> None:
        entry = feat.get("entry_bar")
        if not entry_ok(entry) or str(entry) > ENTRY_CUTOFF:
            return
        key = (str(feat["symbol"]), seq)
        prev = last_emit.get(key)
        if prev is not None and (tm - prev) <= EPISODE_GAP_MIN:
            return
        last_emit[key] = tm
        events.append(
            {
                "date": date,
                "block": block,
                "symbol": feat["symbol"],
                "sector": feat.get("sector"),
                "sequence": seq,
                "feature_bar": t,
                "available_at": feat.get("available_at"),
                "event_time": entry,
                "leading": extra.get("leading"),
                "lagging": extra.get("lagging"),
                "sector_rank": extra.get("rank"),
                "sector_n": extra.get("nsec"),
                "leader": extra.get("leader"),
                "lag_dist": extra.get("lag_dist"),
                "sector_r1": extra.get("sec_r1"),
                "rel_1m": extra.get("rel"),
                "hi20": feat.get("hi20"),
                "lo20": feat.get("lo20"),
                "ret_1m": feat.get("ret_1m"),
                "dist_vwap": feat.get("dist_vwap"),
                "vol_rel20": feat.get("vol_rel20"),
                "sess_ret": feat.get("sess_ret"),
                "rec": rec,
            }
        )

    for t in times:
        tm = to_min(t)
        if tm is None:
            continue
        rows = []
        for sym in present_at.get(t) or []:
            rec = per[sym]
            i = rec["idx"].get(t)
            if i is None:
                continue
            feat = features_at(rec, i)
            feat["symbol"] = sym
            feat["sector"] = rec["sector"]
            rows.append(feat)
        if len(rows) < 20:
            continue
        by_sec: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            if r.get("sector"):
                by_sec[str(r["sector"])].append(r)
        ranks: dict[str, int] = {}
        sec_n: dict[str, int] = {}
        sec_pack: dict[str, dict[str, Any]] = {}
        for sec, xs in by_sec.items():
            finite = [(str(x["symbol"]), float(x["sess_ret"])) for x in xs if _finite(x.get("sess_ret"))]
            sec_n[sec] = len(finite)
            if len(finite) < MIN_SECTOR_N:
                continue
            rk = _rank_desc(finite)
            ranks.update(rk)
            leader = sorted(finite, key=lambda kv: (-kv[1], kv[0]))[0][0]
            r1s = [float(x["ret_1m"]) for x in xs if _finite(x.get("ret_1m"))]
            r3s = [float(x["ret_3m"]) for x in xs if _finite(x.get("ret_3m"))]
            sesss = [float(x["sess_ret"]) for x in xs if _finite(x.get("sess_ret"))]
            breadth = float(np.mean(np.asarray(r1s) > 0)) if r1s else float("nan")
            lead_row = next((x for x in xs if x["symbol"] == leader), None)
            pack = {
                "r1": _med(r1s),
                "r3": _med(r3s),
                "sess": _med(sesss),
                "breadth": breadth,
                "n": len(finite),
                "leader": leader,
                "leader_r1": (lead_row or {}).get("ret_1m"),
                "leader_sess": (lead_row or {}).get("sess_ret"),
            }
            sec_pack[sec] = pack
            if last_leader.get(sec) and last_leader[sec] != leader:
                lead_change_n += 1
            last_leader[sec] = leader
        sec_at[t] = sec_pack
        if t in {"10:00", "11:00", "13:00"}:
            for sec, pack in sec_pack.items():
                leader_rows.append(
                    {
                        "date": date,
                        "block": block,
                        "time": t,
                        "sector": sec,
                        "leader": pack.get("leader"),
                        "leader_r1": pack.get("leader_r1"),
                        "sector_r1": pack.get("r1"),
                        "sector_r3": pack.get("r3"),
                        "breadth": pack.get("breadth"),
                        "intraday_hardcoded": False,
                    }
                )

        for r in rows:
            rec = per[r["symbol"]]
            i = rec["idx"][t]
            sec = str(r.get("sector") or "")
            nsec = sec_n.get(sec) or 0
            rk = ranks.get(r["symbol"])
            pack = sec_pack.get(sec) or {}
            leading = bool(rk == 1 and nsec >= MIN_SECTOR_N)
            lagging = bool(rk is not None and nsec >= MIN_SECTOR_N and rk > int(np.ceil(LAG_RANK_FRAC * nsec)))
            if nsec >= MIN_SECTOR_N and rk is not None:
                acc = lead_acc.setdefault(
                    r["symbol"],
                    {"date": date, "block": block, "symbol": r["symbol"], "sector": sec, "present_n": 0, "leading_n": 0, "lagging_n": 0},
                )
                acc["present_n"] += 1
                if leading:
                    acc["leading_n"] += 1
                if lagging:
                    acc["lagging_n"] += 1
            if leading:
                lead_streak[r["symbol"]] = int(lead_streak.get(r["symbol"]) or 0) + 1
            else:
                lead_streak[r["symbol"]] = 0
            if r.get("impulse_up"):
                last_imp[r["symbol"]] = tm
                impulse_idx[r["symbol"]].append(i)

            sec_r1 = pack.get("r1")
            sec_r3 = pack.get("r3")
            sec_sess = pack.get("sess")
            sector_strong = bool((_finite(sec_r1) and float(sec_r1) > 0) or (_finite(sec_r3) and float(sec_r3) > 0))
            sess = r.get("sess_ret")
            lag_dist = (float(sess) - float(sec_sess)) if _finite(sess) and _finite(sec_sess) else float("nan")
            if lagging and _finite(lag_dist):
                lagging = bool(lag_dist < 0)
            r1 = r.get("ret_1m")
            rel = (float(r1) - float(sec_r1)) if _finite(r1) and _finite(sec_r1) else float("nan")
            extra = {
                "leading": leading,
                "lagging": lagging,
                "rank": rk,
                "nsec": nsec,
                "leader": pack.get("leader"),
                "lag_dist": lag_dist,
                "sec_r1": sec_r1,
                "rel": rel,
            }
            if lagging:
                lag_min_n += 1

            vol_on = bool(r.get("vol_expand") and not prev_vol.get(r["symbol"]))
            prev_vol[r["symbol"]] = bool(r.get("vol_expand"))
            brk_on = bool(r.get("breakout20") and not prev_flag_break.get(r["symbol"]))
            prev_flag_break[r["symbol"]] = bool(r.get("breakout20"))

            # CATCH-UP: sector already moved; target has not fully followed; catch-up BEGINNING (not future).
            if lagging and sector_strong:
                p_rel = prev_rel.get(r["symbol"])
                p_r1 = prev_r1.get(r["symbol"])
                rs_turn = bool(_finite(p_rel) and p_rel <= 0 and _finite(rel) and rel > 0 and _finite(r1) and float(r1) > 0)
                stop_det = bool(_finite(p_r1) and p_r1 < 0 and _finite(r1) and float(r1) >= 0)
                if rs_turn:
                    maybe_emit("CATCHUP_RS_TURN", feat=r, rec=rec, t=t, tm=tm, extra=extra)
                    catchup_n["RS_TURN"] += 1
                if r.get("vwap_reclaim"):
                    maybe_emit("CATCHUP_VWAP", feat=r, rec=rec, t=t, tm=tm, extra=extra)
                    catchup_n["VWAP"] += 1
                if vol_on and _finite(r1) and float(r1) >= 0:
                    maybe_emit("CATCHUP_VOL", feat=r, rec=rec, t=t, tm=tm, extra=extra)
                    catchup_n["VOL"] += 1
                if stop_det:
                    maybe_emit("CATCHUP_STOP_DETERIORATE", feat=r, rec=rec, t=t, tm=tm, extra=extra)
                    catchup_n["STOP_DETERIORATE"] += 1

            if r.get("vwap_reclaim"):
                maybe_emit("VWAP_RECLAIM", feat=r, rec=rec, t=t, tm=tm, extra=extra)
            recent_imp = bool(r["symbol"] in last_imp and 0 < tm - last_imp[r["symbol"]] <= 10)
            if recent_imp and r.get("pause"):
                maybe_emit("IMPULSE_THEN_PAUSE", feat=r, rec=rec, t=t, tm=tm, extra=extra)
            if leading and int(lead_streak.get(r["symbol"]) or 0) >= LEAD_STREAK_MIN and prev_pull.get(r["symbol"]) and (
                (_finite(r1) and float(r1) >= 0) or bool(r.get("vwap_reclaim"))
            ):
                maybe_emit("LEADER_PULLBACK_CONT", feat=r, rec=rec, t=t, tm=tm, extra=extra)
            if brk_on:
                catchup_n["BREAKOUT20_ONSET"] += 1

            prev_rel[r["symbol"]] = rel if _finite(rel) else float("nan")
            prev_r1[r["symbol"]] = float(r1) if _finite(r1) else float("nan")
            prev_pull[r["symbol"]] = bool(r.get("pullback"))

        if t == "09:00":
            for r in rows:
                rec = per[r["symbol"]]
                pc = prev_close.get(r["symbol"])
                o0 = rec["o"][0] if rec["t"][0] == "09:00" else float("nan")
                gap = float(o0 / pc - 1.0) if pc and np.isfinite(pc) and pc != 0 and np.isfinite(o0) else float("nan")
                opening[r["symbol"]] = {
                    "date": date,
                    "block": block,
                    "symbol": r["symbol"],
                    "sector": r["sector"],
                    "gap": gap,
                    "gap_up": bool(np.isfinite(gap) and gap > 0),
                    "gap_down": bool(np.isfinite(gap) and gap < 0),
                }

    for rec in per.values():
        row = opening.get(rec["symbol"])
        if not row:
            continue
        i14 = rec["idx"].get("09:14")
        i15 = rec["idx"].get("09:15")
        pc = prev_close.get(rec["symbol"])
        hold = None
        if i14 is not None and pc and np.isfinite(pc) and pc != 0 and np.isfinite(row.get("gap")):
            cl = rec["c"][i14]
            if np.isfinite(cl):
                if row["gap"] > 0:
                    hold = bool(cl >= pc)
                elif row["gap"] < 0:
                    hold = bool(cl <= pc)
        row["hold_0914"] = hold
        if hold is True and bool(row.get("gap_up")) and i15 is not None:
            feat = features_at(rec, rec["idx"]["09:14"])
            feat["symbol"] = rec["symbol"]
            feat["sector"] = rec["sector"]
            feat["entry_bar"] = "09:15"
            feat["available_at"] = "09:15"
            maybe_emit(
                "OPENING_GAP_HOLD",
                feat=feat,
                rec=rec,
                t="09:14",
                tm=to_min("09:14") or 0,
                extra={"leading": False, "lagging": False, "rank": None, "nsec": None, "leader": None},
            )

    tagged = []
    for e in events:
        rec = e.pop("rec", None)
        if rec is None:
            continue
        attach_path(e, rec, sec_at)
        tagged.append(e)

    daily = []
    for rec in per.values():
        acc = lead_acc.get(rec["symbol"]) or {
            "date": date,
            "block": block,
            "symbol": rec["symbol"],
            "sector": rec["sector"],
            "present_n": 0,
            "leading_n": 0,
            "lagging_n": 0,
        }
        stats = _impulse_day_stats(rec, impulse_idx.get(rec["symbol"]) or [])
        op = opening.get(rec["symbol"]) or {}
        daily.append(
            {
                **acc,
                **stats,
                "opening_n": 1 if op else 0,
                "opening_hold_n": 1 if op.get("hold_0914") is True else 0,
                "gap_up_n": 1 if op.get("gap_up") else 0,
                "gap_up_hold_n": 1 if op.get("gap_up") and op.get("hold_0914") is True else 0,
            }
        )

    lag_rows.append(
        {
            "date": date,
            "block": block,
            "lag_minutes": lag_min_n,
            "catchup_rs_turn": int(catchup_n.get("RS_TURN") or 0),
            "catchup_vwap": int(catchup_n.get("VWAP") or 0),
            "catchup_vol": int(catchup_n.get("VOL") or 0),
            "catchup_stop_deteriorate": int(catchup_n.get("STOP_DETERIORATE") or 0),
            "leader_changes": lead_change_n,
        }
    )
    last_close = {}
    for rec in per.values():
        if rec["n"] and np.isfinite(rec["c"][-1]) and rec["c"][-1] > 0:
            last_close[rec["symbol"]] = float(rec["c"][-1])
    return {
        "events": tagged,
        "daily": daily,
        "leader_rows": leader_rows,
        "lag_rows": lag_rows,
        "last_close": last_close,
    }


def walk_discovery(
    *,
    symbols: list[str],
    sector_of: dict[str, str],
    allowed: set[str],
    forbidden: set[str],
    date_to_block: dict[str, str],
) -> dict[str, Any]:
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(allowed)}", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=allowed, forbidden_dates=forbidden)
    if minutes.empty:
        return {"ok": False, "events": [], "daily": []}
    if minutes["date"].isin(list(forbidden)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    events: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    leader_rows: list[dict[str, Any]] = []
    lag_rows: list[dict[str, Any]] = []
    prev_close: dict[str, float] = {}
    n_days = int(minutes["date"].nunique())
    for i, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        pack = process_day(date=str(day), g=g, sector_of=sector_of, date_to_block=date_to_block, prev_close=prev_close)
        events.extend(pack["events"])
        daily.extend(pack["daily"])
        leader_rows.extend(pack["leader_rows"])
        lag_rows.extend(pack["lag_rows"])
        prev_close.update(pack["last_close"])
        if i % 20 == 0 or i == n_days:
            print(f"DAY {i}/{n_days} events={len(events)}", flush=True)
    by_seq: dict[str, int] = defaultdict(int)
    for e in events:
        by_seq[str(e.get("sequence"))] += 1
    return {
        "ok": True,
        "day_n": n_days,
        "event_n": len(events),
        "events": events,
        "daily": daily,
        "leader_rows": leader_rows,
        "lag_rows": lag_rows,
        "by_sequence": dict(by_seq),
        "used_5m_grid": False,
        "bar_start": True,
        "future_catchup_not_used_for_entry": True,
        "leader_hardcoded_from_full_history": False,
        "atlas_classifier_used": False,
    }
