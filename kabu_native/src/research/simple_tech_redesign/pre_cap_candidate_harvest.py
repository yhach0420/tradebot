"""Apply frozen 2-of-3 Pre-CAP rule on RCA board features. Occupancy replay. No capture restream. No 20260903."""
from __future__ import annotations

from typing import Any, Optional

from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_redesign.branch_u_causal_harvest import occupancy_replay, sot_parity
from research.simple_tech_redesign.causal_board_rca_harvest import BOARD_CACHE, load_u_day
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, spec_sha256_board_rca
from research.simple_tech_redesign.isolation import TODAY
from research.simple_tech_redesign.pre_cap_candidate_spec import (
    ADVERSE_COMPONENT_MIN,
    COMPONENT_ACTIVE_IF_EVENT_N_GE,
    COMPONENT_KEYS,
    COMPONENT_NAMES,
    REJECT_REASON,
)

BOARD_RCA_SHA = spec_sha256_board_rca()


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


def _key(row: dict[str, Any]) -> tuple[str, str, float] | None:
    t0 = _f(row.get("t0"))
    if t0 is None:
        return None
    return (str(row.get("date") or ""), str(row.get("symbol") or "").replace(".T", ""), float(t0))


def components(pre: dict[str, Any]) -> dict[str, Any]:
    bits = []
    for name, key in zip(COMPONENT_NAMES, COMPONENT_KEYS):
        n = int(_f(pre.get(key)) or 0)
        active = n >= int(COMPONENT_ACTIVE_IF_EVENT_N_GE)
        bits.append({"name": name, "key": key, "event_n": n, "active": bool(active)})
    count = sum(1 for b in bits if b["active"])
    return {
        "components": bits,
        "adverse_component_count": int(count),
        "pre_cap_reject": bool(count >= int(ADVERSE_COMPONENT_MIN)),
        "reject_reason": REJECT_REASON if count >= int(ADVERSE_COMPONENT_MIN) else "",
        "bid_depth": pre.get("bid_depth"),
    }


def excursion(fill_px: Any, exit_bid: Any) -> tuple[Optional[float], Optional[float], Optional[float]]:
    fp = _f(fill_px)
    ex = _f(exit_bid)
    if fp is None or ex is None or fp <= 0:
        return None, None, None
    bps = (ex - fp) / fp * 10000.0
    return bps, max(0.0, bps), min(0.0, bps)


def attach_flags(u_rows: list[dict[str, Any]], rca_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = {}
    for r in rca_rows:
        k = _key(r)
        if k:
            by[k] = r
    out = []
    for r in u_rows:
        rec = dict(r)
        k = _key(rec)
        rca = dict(by.get(k) or {})
        pre = dict(rca.get("pre_cap") or {})
        pack = components(pre)
        rec["pre_cap"] = pre
        rec["adverse_component_count"] = pack["adverse_component_count"]
        rec["components"] = pack["components"]
        rec["pre_cap_reject"] = pack["pre_cap_reject"]
        rec["reject_reason"] = pack["reject_reason"]
        rec["bid_depth_diagnostic"] = pack["bid_depth"]
        rec["board_snapshot_ok"] = bool(pre.get("snapshot_ok"))
        be = dict(rec.get("branch_u") or {}).get("break_even_reached")
        rec["break_even_reached"] = bool(be)
        rec["never_break_even"] = bool(rec.get("actual_filled")) and not bool(be)
        cx = dict(rec.get("control_exit") or {})
        bps, mfe, mae = excursion(rec.get("fill_price"), cx.get("exit_bid"))
        rec["session_close_bps"] = bps
        rec["mfe_bps"] = mfe
        rec["mae_bps"] = mae
        rec["control_pnl"] = _f(cx.get("pnl_yen_100"))
        out.append(rec)
    return out


def replay_day(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ctrl = occupancy_replay(rows, exit_key="control_exit")
    retained = [r for r in rows if not r.get("pre_cap_reject")]
    treat = occupancy_replay(retained, exit_key="control_exit")
    flagged_n = sum(1 for r in rows if r.get("pre_cap_reject"))
    ctrl_sot = bool(sot_parity(rows, exit_key="control_exit", ours=ctrl))
    treat_sot = bool(sot_parity(retained, exit_key="control_exit", ours=treat))
    leftover = (
        int(ctrl.get("open_leftover_n") or 0)
        + int(ctrl.get("pending_leftover_n") or 0)
        + int(treat.get("open_leftover_n") or 0)
        + int(treat.get("pending_leftover_n") or 0)
    )
    ctrl["pre_cap_reject_n"] = 0
    treat["pre_cap_reject_n"] = int(flagged_n)
    treat["signal_n"] = len(rows)
    ctrl["signal_n"] = len(rows)
    treat["candidate_n"] = len(retained)
    return {
        "control": ctrl,
        "treatment": treat,
        "control_sot_ok": ctrl_sot,
        "treatment_sot_ok": treat_sot,
        "occupancy_leftover_n": int(leftover),
        "pre_cap_reject_n": int(flagged_n),
        "retained_n": len(retained),
        "rows": rows,
    }


def load_day(day: str, *, cohort: str, today: str = TODAY) -> dict[str, Any]:
    if day == str(today) or day in FORBIDDEN_DAYS:
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    rca_path = BOARD_CACHE / f"day_{cohort}_{day}.json"
    rca = load_day_cache(rca_path, BOARD_RCA_SHA)
    if not rca or not rca.get("ok"):
        return {"ok": False, "blocker": f"rca_cache_missing:{day}", "date": day}
    ubody = load_u_day(day, cohort=cohort)
    if not ubody.get("ok"):
        return {"ok": False, "blocker": f"u_cache_missing:{day}", "date": day}
    rows = attach_flags(list(ubody.get("rows") or []), list(rca.get("rows") or []))
    body = replay_day(rows)
    body["ok"] = True
    body["date"] = day
    body["cohort"] = cohort
    body["blocker"] = None
    return body


def load_cohort(days: list[str], *, cohort: str, today: str = TODAY) -> dict[str, Any]:
    if any(d == str(today) or d in FORBIDDEN_DAYS for d in days):
        return {"ok": False, "blocker": "TODAY_OR_FORBIDDEN"}
    bodies = []
    rows = []
    for day in days:
        print(f"{cohort} {day} precap occupancy", flush=True)
        body = load_day(day, cohort=cohort, today=today)
        if not body.get("ok"):
            return body
        bodies.append(body)
        rows.extend(list(body.get("rows") or []))
    return {
        "ok": True,
        "cohort": cohort,
        "days": list(days),
        "day_bodies": bodies,
        "rows": rows,
        "occupancy_sot_ok": all(bool(b.get("control_sot_ok")) and bool(b.get("treatment_sot_ok")) for b in bodies),
        "occupancy_leftover_n": sum(int(b.get("occupancy_leftover_n") or 0) for b in bodies),
    }
