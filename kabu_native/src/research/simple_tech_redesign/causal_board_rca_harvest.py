"""Causal board snapshots at t0 / Branch U trigger. Sealed days only. No 20260903. No board_ok gate."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Optional

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    find_capture_dir,
    iter_push,
    record_event_stamp,
)
from research.simple_tech_entry_family.harvest import load_day_cache, sealed_day_caps
from research.simple_tech_redesign.branch_u_bb_harvest import BRANCH_U_CACHE
from research.simple_tech_redesign.branch_u_bb_spec import EXIT_REASON
from research.simple_tech_redesign.branch_u_causal_harvest import replay_causal_day
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_holdout_harvest import HOLDOUT_CACHE
from research.simple_tech_redesign.causal_board_rca_spec import (
    AUDIT_EXAMPLES,
    DEPTH_LEVELS,
    EVOLUTION_WINDOW_SEC,
    FORBIDDEN_DAYS,
    FEATURE_KEYS,
)
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from small_paper.v1r_live_dual_lane import session_end_for_position

BOARD_CACHE = RESEARCH_CACHE / "causal_board_information_rca"
AUDIT_SET = {(str(d), str(s)) for d, s in AUDIT_EXAMPLES}


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _finite(v: Any) -> bool:
    return _f(v) is not None


def _payload(rec: dict[str, Any]) -> dict[str, Any]:
    pay = rec.get("payload") or rec.get("original_payload") or {}
    return dict(pay) if isinstance(pay, dict) else {}


def _level(pay: dict[str, Any], side: str, i: int) -> tuple[Optional[float], Optional[float], bool]:
    lv = pay.get(f"{side}{i}")
    if not isinstance(lv, dict):
        return None, None, False
    return _f(lv.get("Price")), _f(lv.get("Qty")), True


def empty_inventory() -> dict[str, Any]:
    levels = {}
    for i in range(1, DEPTH_LEVELS + 1):
        for side in ("Buy", "Sell"):
            levels[f"{side}{i}"] = {"present_n": 0, "price_n": 0, "qty_n": 0, "time_n": 0, "sign_n": 0}
    return {
        "events_n": 0,
        "levels": levels,
        "scalars": {
            "BidPrice": 0,
            "AskPrice": 0,
            "BidQty": 0,
            "AskQty": 0,
            "BidTime": 0,
            "AskTime": 0,
            "CurrentPriceTime": 0,
            "received_at_or_event_t": 0,
        },
        "days": [],
    }


def inventory_event(acc: dict[str, Any], pay: dict[str, Any], *, has_event_t: bool) -> None:
    acc["events_n"] = int(acc.get("events_n") or 0) + 1
    levels = acc["levels"]
    for i in range(1, DEPTH_LEVELS + 1):
        for side in ("Buy", "Sell"):
            k = f"{side}{i}"
            rec = levels[k]
            lv = pay.get(k)
            if not isinstance(lv, dict):
                continue
            rec["present_n"] += 1
            if lv.get("Price") not in (None, ""):
                rec["price_n"] += 1
            if lv.get("Qty") not in (None, ""):
                rec["qty_n"] += 1
            if lv.get("Time") not in (None, ""):
                rec["time_n"] += 1
            if lv.get("Sign") not in (None, ""):
                rec["sign_n"] += 1
    sc = acc["scalars"]
    for k in sc:
        if k == "received_at_or_event_t":
            continue
        if pay.get(k) not in (None, ""):
            sc[k] += 1
    if has_event_t:
        sc["received_at_or_event_t"] += 1


def merge_inventory(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = empty_inventory()
    out["events_n"] = int(a.get("events_n") or 0) + int(b.get("events_n") or 0)
    out["days"] = list(a.get("days") or []) + list(b.get("days") or [])
    for k, rec in out["levels"].items():
        ar = dict((a.get("levels") or {}).get(k) or {})
        br = dict((b.get("levels") or {}).get(k) or {})
        for f in rec:
            rec[f] = int(ar.get(f) or 0) + int(br.get(f) or 0)
    for k in out["scalars"]:
        out["scalars"][k] = int((a.get("scalars") or {}).get(k) or 0) + int((b.get("scalars") or {}).get(k) or 0)
    return out


def board_snap(pay: dict[str, Any], et: float) -> dict[str, Any]:
    bid, bq, b1 = _level(pay, "Buy", 1)
    ask, aq, s1 = _level(pay, "Sell", 1)
    depth_b = 0.0
    depth_a = 0.0
    bid_levels = 0
    ask_levels = 0
    for i in range(1, DEPTH_LEVELS + 1):
        _p, q, pres = _level(pay, "Buy", i)
        if pres:
            bid_levels += 1
            if q is not None:
                depth_b += q
        _p, q, pres = _level(pay, "Sell", i)
        if pres:
            ask_levels += 1
            if q is not None:
                depth_a += q
    spread = None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = 0.5 * (bid + ask)
        if mid > 0:
            spread = (ask - bid) / mid * 10000.0
    l1 = None
    if bq is not None and aq is not None and (bq + aq) > 0:
        l1 = (bq - aq) / (bq + aq)
    dimb = None
    if (depth_b + depth_a) > 0:
        dimb = (depth_b - depth_a) / (depth_b + depth_a)
    ratio = None
    if bq is not None and aq is not None and aq > 0:
        ratio = bq / aq
    return {
        "t": float(et),
        "ok": bool(b1 and s1 and bid is not None and ask is not None and bid > 0 and ask > 0 and bq is not None and aq is not None),
        "buy1_present": bool(b1),
        "sell1_present": bool(s1),
        "bid": bid,
        "ask": ask,
        "bid1_qty": bq,
        "ask1_qty": aq,
        "spread_bps": spread,
        "l1_imbalance": l1,
        "bid1_ask1_qty_ratio": ratio,
        "bid_depth": depth_b if bid_levels else None,
        "ask_depth": depth_a if ask_levels else None,
        "depth_imbalance": dimb,
        "bid_depth_levels": bid_levels,
        "ask_depth_levels": ask_levels,
        "raw_BidPrice": _f(pay.get("BidPrice")),
        "raw_AskPrice": _f(pay.get("AskPrice")),
        "raw_BidQty": _f(pay.get("BidQty")),
        "raw_AskQty": _f(pay.get("AskQty")),
    }


def _delta(end: Optional[float], start: Optional[float]) -> Optional[float]:
    if end is None or start is None:
        return None
    return float(end) - float(start)


def evolve(snaps: list[dict[str, Any]], t0: float, window: float = EVOLUTION_WINDOW_SEC) -> dict[str, Any]:
    at = [s for s in snaps if float(s["t"]) <= float(t0) + 1e-12]
    in_win = [s for s in at if float(s["t"]) + 1e-12 >= float(t0) - float(window)]
    start_c = [s for s in at if float(s["t"]) <= float(t0) - float(window) + 1e-12]
    start = start_c[-1] if start_c else (in_win[0] if in_win else None)
    end = at[-1] if at else None
    bid_down = bid_dep = bid_ref = ask_dep = ask_add = spr_exp = 0
    prev = None
    for s in in_win:
        if prev is not None:
            pb, sb = _f(prev.get("bid")), _f(s.get("bid"))
            pa, sa = _f(prev.get("ask")), _f(s.get("ask"))
            pbq, sbq = _f(prev.get("bid1_qty")), _f(s.get("bid1_qty"))
            paq, saq = _f(prev.get("ask1_qty")), _f(s.get("ask1_qty"))
            ps, ss = _f(prev.get("spread_bps")), _f(s.get("spread_bps"))
            if pb is not None and sb is not None and sb < pb - 1e-12:
                bid_down += 1
            if pb is not None and sb is not None and abs(sb - pb) <= 1e-12 and pbq is not None and sbq is not None:
                if sbq < pbq - 1e-12:
                    bid_dep += 1
                elif sbq > pbq + 1e-12:
                    bid_ref += 1
            if pa is not None and sa is not None and abs(sa - pa) <= 1e-12 and paq is not None and saq is not None:
                if saq < paq - 1e-12:
                    ask_dep += 1
                elif saq > paq + 1e-12:
                    ask_add += 1
            if ps is not None and ss is not None and ss > ps + 1e-12:
                spr_exp += 1
        prev = s
    pack = {
        "window_sec": float(window),
        "window_event_n": len(in_win),
        "start_t": None if start is None else start.get("t"),
        "end_t": None if end is None else end.get("t"),
        "bid_px_change": _delta(None if end is None else end.get("bid"), None if start is None else start.get("bid")),
        "ask_px_change": _delta(None if end is None else end.get("ask"), None if start is None else start.get("ask")),
        "bid_qty_change": _delta(None if end is None else end.get("bid1_qty"), None if start is None else start.get("bid1_qty")),
        "ask_qty_change": _delta(None if end is None else end.get("ask1_qty"), None if start is None else start.get("ask1_qty")),
        "spread_bps_change": _delta(None if end is None else end.get("spread_bps"), None if start is None else start.get("spread_bps")),
        "l1_imbalance_change": _delta(None if end is None else end.get("l1_imbalance"), None if start is None else start.get("l1_imbalance")),
        "bid1_downshift_n": int(bid_down),
        "bid_depletion_event_n": int(bid_dep),
        "bid_refill_event_n": int(bid_ref),
        "ask_depletion_event_n": int(ask_dep),
        "ask_add_event_n": int(ask_add),
        "spread_expand_event_n": int(spr_exp),
        "causal_ok": bool(end is not None and end.get("ok")),
    }
    return pack


def feature_pack(snap: dict[str, Any], evo: dict[str, Any]) -> dict[str, Any]:
    out = {k: None for k in FEATURE_KEYS}
    for k in (
        "spread_bps",
        "bid1_qty",
        "ask1_qty",
        "bid1_ask1_qty_ratio",
        "l1_imbalance",
        "bid_depth",
        "ask_depth",
        "depth_imbalance",
    ):
        out[k] = snap.get(k)
    for k in (
        "bid_px_change",
        "ask_px_change",
        "bid_qty_change",
        "ask_qty_change",
        "spread_bps_change",
        "l1_imbalance_change",
        "bid1_downshift_n",
        "bid_depletion_event_n",
        "bid_refill_event_n",
        "ask_depletion_event_n",
        "ask_add_event_n",
        "spread_expand_event_n",
    ):
        out[k] = evo.get(k)
    out["snapshot_ok"] = bool(snap.get("ok"))
    out["board_event_t"] = snap.get("t")
    out["window_event_n"] = evo.get("window_event_n")
    out["buy1_present"] = snap.get("buy1_present")
    out["sell1_present"] = snap.get("sell1_present")
    out["bid_depth_levels"] = snap.get("bid_depth_levels")
    out["ask_depth_levels"] = snap.get("ask_depth_levels")
    return out


def load_u_day(day: str, *, cohort: str) -> dict[str, Any]:
    if cohort == "DEVELOPMENT":
        path = BRANCH_U_CACHE / f"day_{day}.json"
    else:
        path = HOLDOUT_CACHE / f"branch_u_day_{day}.json"
    body = load_day_cache(path, BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED)
    return body if body else {}


def _u_triggered(row: dict[str, Any]) -> bool:
    bu = dict(row.get("branch_u") or {})
    tx = dict(row.get("treatment_exit") or {})
    return bool(bu.get("u_bb_lower_break") or bu.get("branch_u_exit_triggered") or str(tx.get("reason") or "") == EXIT_REASON)


def _trigger_t(row: dict[str, Any]) -> Optional[float]:
    bu = dict(row.get("branch_u") or {})
    tx = dict(row.get("treatment_exit") or {})
    return _f(bu.get("trigger_time")) or _f(tx.get("trigger_time")) or _f(tx.get("exit_t"))


def _cache_path(day: str, cohort: str) -> Path:
    return BOARD_CACHE / f"day_{cohort}_{day}.json"


def harvest_day(
    cap: dict[str, Any],
    *,
    cohort: str,
    spec_sha: str,
    today: str = TODAY,
) -> dict[str, Any]:
    day = str(cap["date"])
    if day == str(today) or day in FORBIDDEN_DAYS:
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    path = _cache_path(day, cohort)
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    ubody = load_u_day(day, cohort=cohort)
    rows = list(ubody.get("rows") or [])
    if not ubody.get("ok") or not rows:
        return {"ok": False, "blocker": f"missing_branch_u_cache:{day}", "date": day}
    capture = find_capture_dir(day)
    if capture is None:
        return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
    if str(capture).replace("\\", "/").find(str(today)) >= 0 and day == str(today):
        return {"ok": False, "blocker": "ACTIVE_CAPTURE", "date": day}

    occ = replay_causal_day(rows)
    cmap = {}
    for c in list((occ.get("control") or {}).get("candidates") or []):
        t0 = _f(c.get("signal_time") or c.get("t0"))
        if t0 is None:
            continue
        cmap[(str(c.get("date") or day), _bare(c.get("symbol")), float(t0))] = c
    ctrl_occ = set()
    treat_occ = set()
    treat_u = set()
    for t in list((occ.get("control") or {}).get("trades") or []):
        t0 = _f(t.get("t0"))
        if t0 is None:
            continue
        ctrl_occ.add((str(t.get("date") or day), _bare(t.get("symbol")), float(t0)))
    for t in list((occ.get("treatment") or {}).get("trades") or []):
        t0 = _f(t.get("t0"))
        if t0 is None:
            continue
        key = (str(t.get("date") or day), _bare(t.get("symbol")), float(t0))
        treat_occ.add(key)
        if str(t.get("exit_reason") or "") == EXIT_REASON:
            treat_u.add(key)

    decisions: list[dict[str, Any]] = []
    needed: set[str] = set()
    for i, r in enumerate(rows):
        sym = _bare(r.get("symbol"))
        t0 = _f(r.get("t0"))
        if not sym or t0 is None:
            continue
        needed.add(sym)
        decisions.append({"kind": "t0", "i": i, "symbol": sym, "t": float(t0)})
        if _u_triggered(r):
            tt = _trigger_t(r)
            if tt is not None:
                decisions.append({"kind": "trigger", "i": i, "symbol": sym, "t": float(tt)})
    decisions.sort(key=lambda e: (float(e["t"]), str(e["kind"]), str(e["symbol"])))
    pending = {s: [d for d in decisions if d["symbol"] == s] for s in needed}
    bufs: dict[str, deque] = {s: deque() for s in needed}
    frozen: dict[tuple, dict[str, Any]] = {}
    audit_seq: dict[str, list[dict[str, Any]]] = {sym: [] for d, sym in AUDIT_SET if d == day}
    inv = empty_inventory()
    inv_budget = 5000
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    events_n = 0
    kept_n = 0

    def _trim(sym: str, tnow: float) -> None:
        q = bufs[sym]
        pend = pending.get(sym) or []
        tmin = float(pend[0]["t"]) if pend else tnow
        cut = min(tmin, tnow) - float(EVOLUTION_WINDOW_SEC) - 1.0
        while q and float(q[0]["t"]) < cut:
            q.popleft()

    def _freeze_due(sym: str, et: float) -> None:
        pend = pending.get(sym) or []
        while pend and float(pend[0]["t"]) < float(et) - 1e-15:
            d = pend.pop(0)
            snaps = [s for s in bufs[sym] if float(s["t"]) <= float(d["t"]) + 1e-12]
            snap = dict(snaps[-1]) if snaps else {"t": None, "ok": False}
            evo = evolve(list(snaps), float(d["t"]))
            frozen[(d["kind"], d["i"])] = {"snap": snap, "evo": evo}

    for rec in iter_push(capture):
        pay = _payload(rec)
        et = capture_event_epoch(rec, pay)
        has_et = et is not None
        if inv["events_n"] < inv_budget:
            inventory_event(inv, pay, has_event_t=has_et)
        if et is None:
            continue
        et = float(et)
        if et < am_start - 120.0 or et > am_end + 2.0:
            continue
        events_n += 1
        recv = record_event_stamp(rec)
        if recv:
            pay["received_at"] = recv
        sym = _bare(rec.get("symbol") or pay.get("Symbol"))
        if not sym or sym not in needed:
            continue
        _freeze_due(sym, et)
        snap = board_snap(pay, et)
        bufs[sym].append(snap)
        _trim(sym, et)
        kept_n += 1
        if (day, sym) in AUDIT_SET:
            audit_seq.setdefault(sym, []).append(
                {
                    "t": et,
                    "bid": snap.get("bid"),
                    "ask": snap.get("ask"),
                    "bid1_qty": snap.get("bid1_qty"),
                    "ask1_qty": snap.get("ask1_qty"),
                    "spread_bps": snap.get("spread_bps"),
                    "l1_imbalance": snap.get("l1_imbalance"),
                }
            )
        if kept_n % 200000 == 0:
            print(f"{day} board stream kept={kept_n} events={events_n}", flush=True)

    for sym in needed:
        _freeze_due(sym, am_end + 10.0)
        for d in list(pending.get(sym) or []):
            snaps = [s for s in bufs[sym] if float(s["t"]) <= float(d["t"]) + 1e-12]
            snap = dict(snaps[-1]) if snaps else {"t": None, "ok": False}
            evo = evolve(list(snaps), float(d["t"]))
            frozen[(d["kind"], d["i"])] = {"snap": snap, "evo": evo}
        pending[sym] = []

    out_rows = []
    for i, r in enumerate(rows):
        t0 = _f(r.get("t0"))
        sym = _bare(r.get("symbol"))
        key = (day, sym, float(t0)) if t0 is not None else None
        pre = frozen.get(("t0", i)) or {"snap": {"ok": False}, "evo": evolve([], float(t0 or 0.0))}
        trig = frozen.get(("trigger", i))
        cx = dict(r.get("control_exit") or {})
        tx = dict(r.get("treatment_exit") or {})
        c_pnl = _f(cx.get("pnl_yen_100"))
        t_pnl = _f(tx.get("pnl_yen_100"))
        filled = bool(r.get("actual_filled"))
        u_tr = _u_triggered(r)
        recov = None
        if c_pnl is not None and t_pnl is not None:
            recov = float(c_pnl) - float(t_pnl)
        cand = cmap.get(key) if key else {}
        block = str((cand or {}).get("block_reason") or "")
        pack = {
            "date": day,
            "cohort": cohort,
            "symbol": sym,
            "t0": t0,
            "fill_role": r.get("fill_role"),
            "path_type": r.get("path_type"),
            "actual_filled": filled,
            "executable_signal": r.get("executable_signal"),
            "fill_t": r.get("fill_t"),
            "fill_price": r.get("fill_price"),
            "control_pnl": c_pnl,
            "treatment_pnl": t_pnl,
            "control_exit_reason": cx.get("reason"),
            "treatment_exit_reason": tx.get("reason"),
            "control_exit_bid": cx.get("exit_bid"),
            "treatment_exit_bid": tx.get("exit_bid"),
            "control_exit_t": cx.get("exit_t"),
            "treatment_exit_t": tx.get("exit_t"),
            "u_triggered": u_tr,
            "trigger_t": _trigger_t(r),
            "recovery_yen": recov,
            "recovered_after_trigger": bool(u_tr and recov is not None and recov > 1e-12),
            "terminal_after_trigger": bool(u_tr and recov is not None and recov <= 1e-12),
            "positive": bool(filled and c_pnl is not None and c_pnl > 1e-12),
            "negative": bool(filled and c_pnl is not None and c_pnl < -1e-12),
            "control_block_reason": block,
            "control_cap_rejected": block == "CAP",
            "control_admitted": block == "",
            "control_occupied": bool(key in ctrl_occ) if key else False,
            "treatment_occupied": bool(key in treat_occ) if key else False,
            "treatment_u_occupied": bool(key in treat_u) if key else False,
            "pre_cap": feature_pack(dict(pre.get("snap") or {}), dict(pre.get("evo") or {})),
            "exit_board": None if trig is None else feature_pack(dict(trig.get("snap") or {}), dict(trig.get("evo") or {})),
        }
        out_rows.append(pack)

    audit = []
    for i, r in enumerate(rows):
        sym = _bare(r.get("symbol"))
        if (day, sym) not in AUDIT_SET:
            continue
        seq = list(audit_seq.get(sym) or [])
        tt = _trigger_t(r)
        ft = _f(r.get("fill_t"))
        pre_seq = []
        post_seq = []
        if tt is not None:
            pre_seq = [e for e in seq if float(tt) - float(EVOLUTION_WINDOW_SEC) - 1e-12 <= float(e["t"]) <= float(tt) + 1e-12]
            post_seq = [e for e in seq if float(tt) < float(e["t"]) <= float(tt) + 60.0][:80]
        fill_seq = []
        if ft is not None:
            fill_seq = [e for e in seq if abs(float(e["t"]) - float(ft)) <= 1.0][:5]
        audit.append(
            {
                "date": day,
                "symbol": sym,
                "t0": r.get("t0"),
                "fill_t": r.get("fill_t"),
                "fill_price": r.get("fill_price"),
                "fill_role": r.get("fill_role"),
                "path_type": r.get("path_type"),
                "u_triggered": _u_triggered(r),
                "trigger_t": tt,
                "control_exit": r.get("control_exit"),
                "treatment_exit": r.get("treatment_exit"),
                "branch_u": r.get("branch_u"),
                "fill_near_events": fill_seq,
                "pre_trigger_events": pre_seq[-40:],
                "post_trigger_events": post_seq,
            }
        )

    inv["days"] = [day]
    slim = json_sanitize(
        {
            "ok": True,
            "date": day,
            "cohort": cohort,
            "spec_sha": spec_sha,
            "events_n": events_n,
            "kept_n": kept_n,
            "signal_n": len(out_rows),
            "occupancy_sot_ok": bool(occ.get("control_sot_ok")) and bool(occ.get("treatment_sot_ok")),
            "rows": out_rows,
            "audit": audit,
            "inventory": inv,
            "blocker": None,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")
    return slim


def harvest_days(days: list[str], *, cohort: str, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    if any(d == str(today) or d in FORBIDDEN_DAYS for d in days):
        return {"ok": False, "blocker": "TODAY_OR_FORBIDDEN_IN_DAYS"}
    caps = sealed_day_caps(list(days), str(today))
    bodies = []
    inv = empty_inventory()
    for cap in caps:
        if not cap.get("ok"):
            return {"ok": False, "blocker": f"universe_or_capture:{cap.get('date')}"}
        print(f"{cohort} {cap['date']} board harvest start", flush=True)
        body = harvest_day(cap, cohort=cohort, spec_sha=spec_sha, today=today)
        if not body.get("ok"):
            return body
        bodies.append(body)
        inv = merge_inventory(inv, dict(body.get("inventory") or {}))
        print(f"{cohort} {cap['date']} signals={body.get('signal_n')} kept={body.get('kept_n')}", flush=True)
    rows = []
    audit = []
    for b in bodies:
        rows.extend(list(b.get("rows") or []))
        audit.extend(list(b.get("audit") or []))
    return {
        "ok": True,
        "cohort": cohort,
        "days": list(days),
        "rows": rows,
        "audit": audit,
        "inventory": inv,
        "day_n": len(bodies),
        "occupancy_sot_ok": all(bool(b.get("occupancy_sot_ok")) for b in bodies),
    }
