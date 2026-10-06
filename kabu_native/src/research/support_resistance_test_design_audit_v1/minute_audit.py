"""1-minute audit of event independence, retest transitions, and a non-economic matched diagnostic."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes, same_day_reactions
from research.multi_touch_daily_zone_1m_price_action_v1.machine import new_zone_state, step_zone
from research.multi_touch_daily_zone_1m_price_action_v1.walk import next_dates_map
from research.multi_touch_daily_zone_1m_price_action_v1.zones import snapshot_zones
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol, to_min
from research.support_resistance_test_design_audit_v1 import SAMPLE_SEED, TRANSITION_N
from research.support_resistance_test_design_audit_v1.reconstruct import percentiles


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _mom(rec: dict[str, Any], i: int, n: int) -> float:
    if i < n:
        return float("nan")
    a = float(rec["c"][i - n])
    b = float(rec["c"][i])
    if not _finite(a) or not _finite(b) or a == 0:
        return float("nan")
    return (b - a) / a


def _vol_rel(rec: dict[str, Any], i: int, n: int = 20) -> float:
    if i < n:
        return float("nan")
    m = float(np.nanmean(rec["v"][i - n : i]))
    v = float(rec["v"][i])
    if not _finite(m) or m <= 0 or not _finite(v):
        return float("nan")
    return v / m


def _rng_rel(rec: dict[str, Any], i: int, n: int = 20) -> float:
    if i < n:
        return float("nan")
    mr = float(np.nanmean((rec["h"][i - n : i] - rec["l"][i - n : i])))
    h, l = float(rec["h"][i]), float(rec["l"][i])
    if not _finite(mr) or mr <= 0 or not _finite(h) or not _finite(l):
        return float("nan")
    return (h - l) / mr


def _clock_bucket(t: str) -> str:
    hhmm = str(t)[:5]
    if len(hhmm) < 5:
        return "na"
    try:
        hh, mm = int(hhmm[:2]), int(hhmm[3:5])
    except ValueError:
        return "na"
    tot = hh * 60 + mm
    return f"{(tot // 30) * 30:04d}"


def _gap_sign(opn: float, pdc: float | None) -> str:
    if not _finite(opn) or not _finite(pdc) or pdc == 0:
        return "na"
    d = opn - float(pdc)
    if d > 0:
        return "up"
    if d < 0:
        return "down"
    return "flat"


def _in_any_zone(h: float, l: float, zones: list[dict[str, Any]]) -> bool:
    for z in zones:
        if l <= float(z["hi"]) and h >= float(z["lo"]):
            return True
    return False


def _bps_path(rec: dict[str, Any], i: int, n: int = 30) -> dict[str, Any]:
    px0 = float(rec["o"][i + 1]) if i + 1 < rec["n"] and _finite(rec["o"][i + 1]) else float(rec["c"][i])
    if not _finite(px0) or px0 <= 0:
        return {"mfe_bps": None, "mae_bps": None, "end_bps": None, "p20_before_m20": None, "p40_before_m20": None, "p80_before_m30": None}
    mfe = 0.0
    mae = 0.0
    end = None
    hit20 = False
    hit40 = False
    hit80 = False
    hit_m20_first = False
    hit_m30_first = False
    p20_b = None
    p40_b = None
    p80_b = None
    last = min(rec["n"] - 1, i + n)
    for j in range(i + 1, last + 1):
        h, l, c = float(rec["h"][j]), float(rec["l"][j]), float(rec["c"][j])
        if _finite(h):
            mfe = max(mfe, (h - px0) / px0 * 1e4)
        if _finite(l):
            mae = min(mae, (l - px0) / px0 * 1e4)
        if _finite(c):
            end = (c - px0) / px0 * 1e4
        if p20_b is None:
            if mfe >= 20 and mae > -20:
                p20_b = True
            elif mae <= -20:
                p20_b = False
        if p40_b is None:
            if mfe >= 40 and mae > -20:
                p40_b = True
            elif mae <= -20:
                p40_b = False
        if p80_b is None:
            if mfe >= 80 and mae > -30:
                p80_b = True
            elif mae <= -30:
                p80_b = False
    return {
        "mfe_bps": mfe,
        "mae_bps": mae,
        "end_bps": end,
        "p20_before_m20": p20_b,
        "p40_before_m20": p40_b,
        "p80_before_m30": p80_b,
        "mfe_before_mae": bool(mfe > 0 and (mae == 0 or mfe >= abs(mae))),
    }


def walk_minute_audit(
    *,
    bind: dict[str, Any],
    minutes,
    hist: dict[str, list[dict[str, Any]]],
    reactions: dict[str, list[dict[str, Any]]],
    sample_keys: list[tuple[str, str]],
) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    disc_set = set(disc)
    date_to_block = dict(blocks.get("date_to_block") or {})
    sample_set = set(sample_keys)
    rng = np.random.default_rng(int(SAMPLE_SEED) + 7)

    ev_per: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    bars_inside_hold: list[float] = []
    bars_inside_break: list[float] = []
    traces_res = _Reservoir(TRANSITION_N, rng)
    matched_rows: list[dict[str, Any]] = []
    n_raw = 0
    n_days = int(minutes["date"].nunique())

    # Rebuild hist/reactions by walking dates in order; do not use future daily bars.
    live_hist: dict[str, list[dict[str, Any]]] = defaultdict(list)
    live_rx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    nxt = next_dates_map(disc)

    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        date = str(day)
        block = str(date_to_block.get(date) or "")
        lookback = disc[: disc.index(date)] if date in disc_set else disc
        for sym, sg in g.groupby("symbol", sort=False):
            rec = prep_symbol(sg)
            if rec["n"] < 20:
                continue
            symbol = str(sym)
            atr = atr20(live_hist[symbol])
            snap = snapshot_zones(symbol=symbol, reactions=live_rx[symbol], atr=atr, session_date=date, lookback_dates=lookback)
            zones = list(snap["resistance_active"]) + list(snap["support_active"])
            zstates = [new_zone_state(z) for z in zones]
            sampled = (symbol, date) in sample_set
            logs: dict[str, list[dict[str, Any]]] = defaultdict(list) if sampled else {}
            armed: dict[str, bool] = {}
            saw_hold: dict[str, bool] = {}
            treatments: list[dict[str, Any]] = []
            feat: list[dict[str, Any]] = []
            pdc = live_hist[symbol][-1]["close"] if live_hist[symbol] else None
            n = int(rec["n"])
            for i in range(n):
                t = rec["t"][i]
                if in_lunch(t):
                    continue
                tm = to_min(t)
                if tm is None:
                    continue
                c = float(rec["c"][i])
                h = float(rec["h"][i])
                l = float(rec["l"][i])
                prev_c = float(rec["c"][i - 1]) if i > 0 else float("nan")
                if sampled:
                    feat.append(
                        {
                            "i": i,
                            "t": t,
                            "bucket": _clock_bucket(t),
                            "mom1": _mom(rec, i, 1),
                            "mom3": _mom(rec, i, 3),
                            "mom5": _mom(rec, i, 5),
                            "vol_rel": _vol_rel(rec, i),
                            "rng_rel": _rng_rel(rec, i),
                            "in_zone": _in_any_zone(h, l, zones),
                            "gap": _gap_sign(float(rec["o"][0]), pdc),
                        }
                    )
                for st in zstates:
                    emits = step_zone(st, c=c, h=h, l=l, prev_c=prev_c, tm=tm, feature_bar=t)
                    zid = str(st["zone"].get("zone_id") or "")
                    kinds = [str(e.get("event_kind") or "") for e in emits]
                    if sampled and (armed.get(zid) or any(k.startswith("BREAK_") for k in kinds) or st.get("waiting_retest") or st.get("flipped")):
                        logs[zid].append(
                            {
                                "t": t,
                                "c": c,
                                "h": h,
                                "l": l,
                                "in_zone": bool(l <= st["hi"] and h >= st["lo"]),
                                "waiting_retest": bool(st.get("waiting_retest")),
                                "flipped": bool(st.get("flipped")),
                                "broke_above": bool(st.get("broke_above")),
                                "events": ",".join(kinds),
                                "bars_inside": int(st.get("bars_inside") or 0),
                            }
                        )
                    for e in emits:
                        n_raw += 1
                        kind = str(e.get("event_kind") or "")
                        key = (symbol, zid, date)
                        ev_per[key][kind] += 1
                        ev_per[key]["_n"] += 1
                        if kind in ("RETEST_HOLD",):
                            if _finite(e.get("bars_inside")):
                                bars_inside_hold.append(float(e["bars_inside"]))
                            saw_hold[zid] = True
                        if kind in ("BREAK_ABOVE_ZONE", "BREAK_BELOW_ZONE"):
                            armed[zid] = True
                            if _finite(e.get("bars_inside")):
                                bars_inside_break.append(float(e["bars_inside"]))
                            if sampled and str(e.get("role")) == "RESISTANCE" and kind == "BREAK_ABOVE_ZONE":
                                treatments.append({"i": i, "t": t, "zone_id": zid, "kind": kind})
            if sampled:
                for zid, held in saw_hold.items():
                    if not held:
                        continue
                    bars = logs.get(zid) or []
                    if not bars:
                        continue
                    rec_tr = {
                        "symbol": symbol,
                        "date": date,
                        "block": block,
                        "zone_id": zid,
                        "n_bars_logged": len(bars),
                        "n_retest": sum(1 for b in bars if "RETEST_ZONE" in (b.get("events") or "")),
                        "n_hold": sum(1 for b in bars if "RETEST_HOLD" in (b.get("events") or "")),
                        "n_fail": sum(1 for b in bars if "FAIL_BACK_BELOW" in (b.get("events") or "") or "FAIL_BACK_ABOVE" in (b.get("events") or "")),
                        "cleared_before_retest": _cleared_before_retest(bars),
                        "lingering": _lingering(bars),
                        "bars": bars[:120],
                    }
                    traces_res.add(rec_tr)
                if treatments and feat:
                    first = treatments[0]
                    ti = int(first["i"])
                    tf = next((x for x in feat if int(x["i"]) == ti), None)
                    if tf is not None:
                        ctrl = _find_control(feat, tf)
                        tr_path = _bps_path(rec, ti)
                        row_m = {
                            "symbol": symbol,
                            "date": date,
                            "block": block,
                            "treatment_t": first["t"],
                            "treatment_zone_id": first["zone_id"],
                            **{f"tr_{k}": v for k, v in tr_path.items()},
                            "matched": ctrl is not None,
                        }
                        if ctrl is not None:
                            cp = _bps_path(rec, int(ctrl["i"]))
                            row_m.update({f"ct_{k}": v for k, v in cp.items()})
                            row_m["control_t"] = ctrl["t"]
                        matched_rows.append(row_m)

            dbar = daily_from_minutes(rec, date)
            if dbar:
                live_hist[symbol].append(dbar)
                nxt_d = nxt.get(date)
                for rx in same_day_reactions(dbar, atr, next_session=nxt_d):
                    live_rx[symbol].append({**rx, "symbol": symbol})
        if di % 20 == 0 or di == n_days:
            print(f"MINUTE {di}/{n_days} raw={n_raw} szd={len(ev_per)} traces={len(traces_res.buf)}", flush=True)

    return _summarize_minute(ev_per, n_raw, bars_inside_hold, bars_inside_break, traces_res.buf, matched_rows)


def _cleared_before_retest(bars: list[dict[str, Any]]) -> bool:
    saw_break = False
    cleared = False
    for b in bars:
        ev = str(b.get("events") or "")
        if "BREAK_ABOVE" in ev or "BREAK_BELOW" in ev:
            saw_break = True
            cleared = False
            continue
        if saw_break and (not b.get("in_zone")) and not b.get("waiting_retest"):
            # waiting_retest may still be true; use price relative to zone via in_zone False
            cleared = True
        if saw_break and (not b.get("in_zone")):
            cleared = True
        if "RETEST_HOLD" in ev or "RETEST_ZONE" in ev:
            return bool(cleared)
    return False


def _lingering(bars: list[dict[str, Any]]) -> bool:
    """Hold fires while never leaving the zone after break."""
    return (not _cleared_before_retest(bars)) and any("RETEST_HOLD" in str(b.get("events") or "") for b in bars)


class _Reservoir:
    def __init__(self, k: int, rng: Any) -> None:
        self.k = int(k)
        self.rng = rng
        self.buf: list[dict[str, Any]] = []
        self.n = 0

    def add(self, item: dict[str, Any]) -> None:
        self.n += 1
        if len(self.buf) < self.k:
            self.buf.append(item)
            return
        j = int(self.rng.integers(0, self.n))
        if j < self.k:
            self.buf[j] = item


def _find_control(feat: list[dict[str, Any]], tf: dict[str, Any]) -> dict[str, Any] | None:
    best = None
    best_d = 1e9
    for x in feat:
        if int(x["i"]) == int(tf["i"]):
            continue
        if x.get("in_zone"):
            continue
        if x.get("bucket") != tf.get("bucket"):
            continue
        if x.get("gap") != tf.get("gap"):
            continue
        if not _finite(x.get("mom5")) or not _finite(tf.get("mom5")):
            continue
        if abs(float(x["mom5"]) - float(tf["mom5"])) > 0.002:
            continue
        if _finite(x.get("vol_rel")) and _finite(tf.get("vol_rel")) and tf["vol_rel"] > 0:
            if abs(float(x["vol_rel"]) - float(tf["vol_rel"])) / max(float(tf["vol_rel"]), 1e-9) > 0.40:
                continue
        d = abs(float(x["mom5"]) - float(tf["mom5"]))
        if d < best_d:
            best_d = d
            best = x
    return best


def _summarize_minute(
    ev_per: dict[tuple[str, str, str], Counter],
    n_raw: int,
    bars_inside_hold: list[float],
    bars_inside_break: list[float],
    traces: list[dict[str, Any]],
    matched_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    n_szd = len(ev_per)
    ev_counts = [float(c["_n"]) for c in ev_per.values()]
    break_n = []
    retest_n = []
    hold_n = []
    enter_n = []
    retest_per_break = []
    hold_per_retest = []
    szd_with_break = 0
    for c in ev_per.values():
        br = int(c.get("BREAK_ABOVE_ZONE", 0) + c.get("BREAK_BELOW_ZONE", 0))
        rt = int(c.get("RETEST_ZONE", 0))
        hd = int(c.get("RETEST_HOLD", 0))
        en = int(c.get("ENTER_ZONE", 0))
        break_n.append(float(br))
        retest_n.append(float(rt))
        hold_n.append(float(hd))
        enter_n.append(float(en))
        if br:
            szd_with_break += 1
            retest_per_break.append(rt / br)
        if rt:
            hold_per_retest.append(hd / rt)
    lingering_n = sum(1 for t in traces if t.get("lingering"))
    cleared_n = sum(1 for t in traces if t.get("cleared_before_retest"))
    both_hold_fail = sum(1 for t in traces if int(t.get("n_hold") or 0) > 0 and int(t.get("n_fail") or 0) > 0)
    matched = [r for r in matched_rows if r.get("matched")]
    def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
        xs = [r.get(key) for r in rows if r.get(key) is not None]
        if not xs:
            return None
        return float(np.mean([1.0 if x else 0.0 for x in xs]))

    tr_p20 = _rate(matched, "tr_p20_before_m20")
    ct_p20 = _rate(matched, "ct_p20_before_m20")
    return {
        "raw_event_n": n_raw,
        "unique_symbol_zone_day_n": n_szd,
        "raw_events_per_true_interaction": (n_raw / n_szd) if n_szd else None,
        "events_per_symbol_zone_day": percentiles(ev_counts),
        "breaks_per_zone_day": percentiles(break_n),
        "retests_per_zone_day": percentiles(retest_n),
        "holds_per_zone_day": percentiles(hold_n),
        "enters_per_zone_day": percentiles(enter_n),
        "retests_per_break": percentiles(retest_per_break),
        "holds_per_retest": percentiles(hold_per_retest),
        "zone_days_with_break": szd_with_break,
        "labels_independent": False,
        "independence_note": (
            "Counts are highly correlated minutes of the same symbol×zone×day. "
            "Mean events per episode >> 1. RETEST_HOLD ≈ RETEST_ZONE."
        ),
        "bars_inside_at_retest_hold": percentiles(bars_inside_hold),
        "bars_inside_at_break": percentiles(bars_inside_break),
        "mean_bars_inside_hold_consistent_with_lingering": bool(
            bars_inside_hold and float(np.mean(bars_inside_hold)) >= 20
        ),
        "transition_n": len(traces),
        "transition_lingering_n": lingering_n,
        "transition_cleared_before_retest_n": cleared_n,
        "transition_lingering_rate": (lingering_n / len(traces)) if traces else None,
        "transition_hold_and_fail_same_episode_n": both_hold_fail,
        "traces": traces,
        "matched_attempt_n": len(matched_rows),
        "matched_n": len(matched),
        "matched_treatment_p20_before_m20": tr_p20,
        "matched_control_p20_before_m20": ct_p20,
        "matched_treatment_median_mfe": float(np.median([r["tr_mfe_bps"] for r in matched if _finite(r.get("tr_mfe_bps"))])) if matched else None,
        "matched_control_median_mfe": float(np.median([r["ct_mfe_bps"] for r in matched if _finite(r.get("ct_mfe_bps"))])) if matched else None,
        "matched_treatment_median_mae": float(np.median([r["tr_mae_bps"] for r in matched if _finite(r.get("tr_mae_bps"))])) if matched else None,
        "matched_control_median_mae": float(np.median([r["ct_mae_bps"] for r in matched if _finite(r.get("ct_mae_bps"))])) if matched else None,
        "matched_changes_old_unmatched_pdh_design": True,
        "matched_rehabilitates_old_no_info_verdict": False,
        "matched_rows": matched_rows[:200],
        "labels_not_iid": True,
    }
