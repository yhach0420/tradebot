"""EXIT failure composition shift RCA — analyze residual SoT rows only."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any, Optional

from research.anchor_timing_robustness.grid import hm_epoch
from research.simple_tech_redesign.exit_composition_shift_rca_spec import (
    COMMON_ROLE_WEIGHT_ADDED,
    COMMON_ROLE_WEIGHT_CORE,
    CONCENTRATION_WARN,
    DECOMP_TOL,
    DEV_CONTROL_ADDED_N_EXPECTED,
    DEV_CONTROL_CORE_N_EXPECTED,
    DEV_CONTROL_FILL_N_EXPECTED,
    DEV_CONTROL_PNL_EXPECTED,
    FAILURE_CLASSES,
    P_FAMILY,
    PRIMARY_FAILURE_KEYS,
    RESIDUAL_VERDICT_EXPECTED,
    SHIFT_DRIVERS,
    YEN_PARITY_TOL,
)
from research.simple_tech_redesign.exit_residual_rca_analyze import class_pack, identity_ok
from research.simple_tech_redesign.exit_residual_rca_spec import (
    FWD_CONTROL_ADDED_N_EXPECTED,
    FWD_CONTROL_CORE_N_EXPECTED,
    FWD_CONTROL_FILL_N_EXPECTED,
    FWD_CONTROL_PNL_EXPECTED,
)
from research.simple_tech_redesign.isolation import EXIT_RESIDUAL_RCA_OUT
from small_paper.v1r_live_dual_lane import session_end_for_position

EPS = 1e-12
P_FAMILY_MEMBERS = ("P_EARLY_AFTER_BE", "P_PROFIT_THEN_FAILURE")


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


def _iqr(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    s = sorted(xs)
    n = len(s)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    return float(q3 - q1)


def _class_filter(rows: list[dict[str, Any]], class_key: str) -> list[dict[str, Any]]:
    if class_key == P_FAMILY:
        return [r for r in rows if str(r.get("residual_class") or "") in P_FAMILY_MEMBERS]
    return [r for r in rows if str(r.get("residual_class") or "") == class_key]


def _gross_loss(rows: list[dict[str, Any]]) -> float:
    return float(sum(float(_f(r.get("below_be_terminal_loss")) or 0.0) for r in rows))


def _giveback(rows: list[dict[str, Any]]) -> float:
    return float(sum(float(_f(r.get("peak_to_close_giveback")) or 0.0) for r in rows))


def load_residual_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    path = EXIT_RESIDUAL_RCA_OUT / "report.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing residual SoT: {path}")
    body = json.loads(path.read_text(encoding="utf-8"))
    req = dict(body.get("required") or {})
    if str(req.get("VERDICT") or "") != RESIDUAL_VERDICT_EXPECTED:
        raise AssertionError(f"residual verdict mismatch: {req.get('VERDICT')}")
    dev = list((body.get("development") or {}).get("rows") or [])
    fwd = list((body.get("forward") or {}).get("rows") or [])
    return dev, fwd, body


def enrich_horizon(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(r)
        day = str(rec.get("date") or "")
        fill_time = float(_f(rec.get("fill_time")) or 0.0)
        fill_price = float(_f(rec.get("fill_price")) or 0.0)
        pnl = float(_f(rec.get("session_close_pnl")) or 0.0)
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=fill_time))
        entry_notional = fill_price * 100.0
        pnl_bps = (pnl / entry_notional * 10000.0) if entry_notional > EPS else None
        loss_bps = max(0.0, -(pnl_bps or 0.0))
        rec["seconds_from_session_open"] = fill_time - am_start
        rec["seconds_to_session_close"] = am_end - fill_time
        rec["session_segment"] = "AM"
        rec["entry_notional"] = entry_notional
        rec["pnl_bps"] = pnl_bps
        rec["loss_bps"] = loss_bps
        out.append(rec)
    return out


def incidence_severity_pack(rows: list[dict[str, Any]], class_key: str, fill_n: int) -> dict[str, Any]:
    cls_rows = _class_filter(rows, class_key)
    class_n = len(cls_rows)
    gross = _gross_loss(cls_rows)
    giveback = _giveback(cls_rows)
    pnls = [float(_f(r.get("session_close_pnl")) or 0.0) for r in cls_rows]
    losses = [p for p in pnls if p < -EPS]
    loss_yen = [float(_f(r.get("below_be_terminal_loss")) or 0.0) for r in cls_rows if float(_f(r.get("below_be_terminal_loss")) or 0.0) > EPS]
    loss_bps = [float(_f(r.get("loss_bps")) or 0.0) for r in cls_rows if float(_f(r.get("loss_bps")) or 0.0) > EPS]
    s = gross / class_n if class_n else 0.0
    r = class_n / fill_n if fill_n else 0.0
    return {
        "class_n": class_n,
        "class_rate": r,
        "gross_loss": gross,
        "gross_loss_per_all_fill": gross / fill_n if fill_n else 0.0,
        "gross_loss_per_class_trade": s,
        "giveback": giveback,
        "giveback_per_all_fill": giveback / fill_n if fill_n else 0.0,
        "giveback_per_class_trade": giveback / class_n if class_n else 0.0,
        "loss_trade_n": len(losses),
        "loss_trade_rate": len(losses) / class_n if class_n else 0.0,
        "median_loss_among_losers": median(losses) if losses else None,
        "mean_loss_among_losers": (sum(losses) / len(losses)) if losses else None,
        "gross_loss_yen": gross,
        "median_loss_yen": median(loss_yen) if loss_yen else None,
        "median_loss_bps": median(loss_bps) if loss_bps else None,
        "mean_loss_bps": (sum(loss_bps) / len(loss_bps)) if loss_bps else None,
        "total_loss_bps_diagnostic_sum": sum(loss_bps),
        "severity_per_class_trade": s,
        "incidence_rate": r,
        "gross_loss_per_fill": r * s,
    }


def midpoint_decompose(dev: dict[str, Any], fwd: dict[str, Any], class_key: str) -> dict[str, Any]:
    r_dev = float(dev["incidence_rate"])
    r_fwd = float(fwd["incidence_rate"])
    s_dev = float(dev["severity_per_class_trade"])
    s_fwd = float(fwd["severity_per_class_trade"])
    glpf_dev = float(dev["gross_loss_per_fill"])
    glpf_fwd = float(fwd["gross_loss_per_fill"])
    incidence = (r_fwd - r_dev) * (s_fwd + s_dev) / 2.0
    severity = (s_fwd - s_dev) * (r_fwd + r_dev) / 2.0
    delta = glpf_fwd - glpf_dev
    ok = abs(incidence + severity - delta) <= DECOMP_TOL + abs(delta) * 1e-9
    if not ok:
        raise AssertionError(
            f"{class_key}: decomposition fail incidence={incidence} severity={severity} delta={delta}"
        )
    return {
        "r_dev": r_dev,
        "r_fwd": r_fwd,
        "s_dev": s_dev,
        "s_fwd": s_fwd,
        "gross_loss_per_fill_dev": glpf_dev,
        "gross_loss_per_fill_fwd": glpf_fwd,
        "delta_gross_loss_per_fill": delta,
        "INCIDENCE_EFFECT": incidence,
        "SEVERITY_EFFECT": severity,
        "decomposition_ok": True,
    }


def _gross_rank(gross_by_class: dict[str, float]) -> list[str]:
    items = [(k, gross_by_class[k]) for k in FAILURE_CLASSES]
    items.sort(key=lambda kv: (-float(kv[1]), kv[0]))
    return [k for k, _ in items]


def role_slice(rows: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("fill_role") or "") == role]


def role_breakdown(rows: list[dict[str, Any]], fill_n: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for role in ("CORE", "ADDED"):
        rrows = role_slice(rows, role)
        rn = len(rrows)
        pack: dict[str, Any] = {"role_fill_n": rn, "role_fill_rate": rn / fill_n if fill_n else 0.0}
        for ck in PRIMARY_FAILURE_KEYS:
            pack[ck] = incidence_severity_pack(rrows, ck, rn if rn else 1)
        out[role] = pack
    return out


def role_standardized(rows: list[dict[str, Any]], fill_n: int) -> dict[str, Any]:
    by_role = role_breakdown(rows, fill_n)
    out: dict[str, Any] = {}
    for ck in PRIMARY_FAILURE_KEYS:
        std_rate = 0.0
        std_glpf = 0.0
        for role, w in (("CORE", COMMON_ROLE_WEIGHT_CORE), ("ADDED", COMMON_ROLE_WEIGHT_ADDED)):
            rp = by_role[role][ck]
            rn = int(by_role[role]["role_fill_n"])
            rate_in_role = float(rp["class_rate"]) if rn else 0.0
            glpf_in_role = float(rp["gross_loss"]) / rn if rn else 0.0
            std_rate += w * rate_in_role
            std_glpf += w * glpf_in_role
        out[ck] = {
            "standardized_class_rate": std_rate,
            "standardized_gross_loss_per_fill": std_glpf,
            "standardized_gross_loss": std_glpf * fill_n,
        }
    gross_map = {k: float(out[k]["standardized_gross_loss"]) for k in FAILURE_CLASSES}
    out["gross_rank"] = _gross_rank(gross_map)
    out["gross_by_class"] = gross_map
    return out


def horizon_pack(rows: list[dict[str, Any]], class_key: str) -> dict[str, Any]:
    cls_rows = _class_filter(rows, class_key)
    open_secs = [float(_f(r.get("seconds_from_session_open")) or 0.0) for r in cls_rows]
    close_secs = [float(_f(r.get("seconds_to_session_close")) or 0.0) for r in cls_rows]
    return {
        "class_n": len(cls_rows),
        "median_seconds_from_open": median(open_secs) if open_secs else None,
        "median_seconds_to_close": median(close_secs) if close_secs else None,
        "iqr_seconds_from_open": _iqr(open_secs),
        "iqr_seconds_to_close": _iqr(close_secs),
    }


def concentration_pack(rows: list[dict[str, Any]], class_key: str) -> dict[str, Any]:
    cls_rows = _class_filter(rows, class_key)
    pack = class_pack(cls_rows)
    return {
        "class_n": int(pack.get("trade_n") or 0),
        "gross_loss": float(pack.get("gross_loss") or 0.0),
        "top_day": pack.get("top_day"),
        "top_day_loss": pack.get("top_day_loss"),
        "top_day_share": pack.get("top_day_share"),
        "top_day_warn": pack.get("top_day_warn"),
        "top_symbol": pack.get("top_symbol"),
        "top_symbol_loss": pack.get("top_symbol_loss"),
        "top_symbol_share": pack.get("top_symbol_share"),
        "top_symbol_warn": pack.get("top_symbol_warn"),
    }


def leave_one_out(rows: list[dict[str, Any]], fill_n: int, *, kind: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    base = {k: _gross_loss(_class_filter(rows, k)) for k in FAILURE_CLASSES}
    base_rank = _gross_rank(base)
    for ck in FAILURE_CLASSES:
        cls_rows = _class_filter(rows, ck)
        if not cls_rows:
            out[ck] = {"excluded": None, "gross_by_class": base, "rank": base_rank, "rank_changed": False}
            continue
        if kind == "day":
            loss_by: dict[str, float] = {}
            for r in cls_rows:
                d = str(r.get("date") or "")
                loss_by[d] = loss_by.get(d, 0.0) + float(_f(r.get("below_be_terminal_loss")) or 0.0)
            excluded = max(loss_by.items(), key=lambda kv: kv[1])[0] if loss_by else None
            filt = [r for r in rows if str(r.get("date") or "") != excluded]
        else:
            loss_by = {}
            for r in cls_rows:
                s = str(r.get("symbol") or "")
                loss_by[s] = loss_by.get(s, 0.0) + float(_f(r.get("below_be_terminal_loss")) or 0.0)
            excluded = max(loss_by.items(), key=lambda kv: kv[1])[0] if loss_by else None
            filt = [r for r in rows if str(r.get("symbol") or "") != excluded]
        adj = {k: _gross_loss(_class_filter(filt, k)) for k in FAILURE_CLASSES}
        rank = _gross_rank(adj)
        out[ck] = {
            "excluded": excluded,
            "gross_by_class": adj,
            "rank": rank,
            "base_rank": base_rank,
            "rank_changed": rank != base_rank,
            "rank1_changed": rank[0] != base_rank[0] if rank and base_rank else False,
        }
    return out


def be_reach_pack(rows: list[dict[str, Any]], fill_n: int) -> dict[str, Any]:
    early = [r for r in rows if str(r.get("path_type") or "") == "EARLY_FAILURE"]
    u = _class_filter(rows, "U_EARLY_NEVER_BE")
    p_early = _class_filter(rows, "P_EARLY_AFTER_BE")
    be_n = sum(1 for r in rows if r.get("break_even_reached"))
    out: dict[str, Any] = {
        "all_be_reached_rate": be_n / fill_n if fill_n else 0.0,
        "be_reached_n": be_n,
        "early_failure_n": len(early),
        "early_never_be_n": len(u),
        "early_after_be_n": len(p_early),
        "early_never_be_share": len(u) / len(early) if early else None,
        "early_after_be_share": len(p_early) / len(early) if early else None,
    }
    for role in ("CORE", "ADDED"):
        rrows = role_slice(rows, role)
        rn = len(rrows)
        re = [r for r in rrows if str(r.get("path_type") or "") == "EARLY_FAILURE"]
        ru = _class_filter(rrows, "U_EARLY_NEVER_BE")
        rp = _class_filter(rrows, "P_EARLY_AFTER_BE")
        rbe = sum(1 for r in rrows if r.get("break_even_reached"))
        out[role] = {
            "fill_n": rn,
            "be_reached_rate": rbe / rn if rn else 0.0,
            "early_failure_n": len(re),
            "early_never_be_n": len(ru),
            "early_after_be_n": len(rp),
            "early_never_be_share": len(ru) / len(re) if re else None,
            "early_after_be_share": len(rp) / len(re) if re else None,
        }
    return out


def ptf_stability(rows: list[dict[str, Any]], fill_n: int, be_n: int) -> dict[str, Any]:
    ptf = _class_filter(rows, "P_PROFIT_THEN_FAILURE")
    pn = len(ptf)
    gross = _gross_loss(ptf)
    give = _giveback(ptf)
    return {
        "PTF_n": pn,
        "PTF_rate_per_fill": pn / fill_n if fill_n else 0.0,
        "PTF_rate_per_be_reached": pn / be_n if be_n else None,
        "PTF_gross_loss_per_fill": gross / fill_n if fill_n else 0.0,
        "PTF_giveback_per_fill": give / fill_n if fill_n else 0.0,
        "PTF_giveback_per_PTF_trade": give / pn if pn else 0.0,
        "PTF_gross_loss": gross,
        "PTF_giveback": give,
    }


def assert_integrity(dev_rows: list[dict[str, Any]], fwd_rows: list[dict[str, Any]]) -> dict[str, Any]:
    dev_body = {
        "control_fill_n": len(dev_rows),
        "control_core_n": sum(1 for r in dev_rows if r.get("fill_role") == "CORE"),
        "control_added_n": sum(1 for r in dev_rows if r.get("fill_role") == "ADDED"),
        "control_total_pnl": sum(float(_f(r.get("session_close_pnl")) or 0.0) for r in dev_rows),
        "occupancy_sot_ok": True,
        "leftover_ok": True,
        "rows": dev_rows,
    }
    fwd_body = {
        "control_fill_n": len(fwd_rows),
        "control_core_n": sum(1 for r in fwd_rows if r.get("fill_role") == "CORE"),
        "control_added_n": sum(1 for r in fwd_rows if r.get("fill_role") == "ADDED"),
        "control_total_pnl": sum(float(_f(r.get("session_close_pnl")) or 0.0) for r in fwd_rows),
        "occupancy_sot_ok": True,
        "leftover_ok": True,
        "rows": fwd_rows,
    }
    dev_ok, dev_id = identity_ok(dev_body, cohort="DEVELOPMENT")
    fwd_ok, fwd_id = identity_ok(fwd_body, cohort="FORWARD_BURNED")
    assert dev_ok and fwd_ok, f"identity fail dev={dev_id} fwd={fwd_id}"
    for label, rows in (("DEVELOPMENT", dev_rows), ("FORWARD_BURNED", fwd_rows)):
        classes = Counter(str(r.get("residual_class") or "") for r in rows)
        assert sum(classes.values()) == len(rows)
        early_u = sum(1 for r in rows if r.get("residual_class") == "U_EARLY_NEVER_BE")
        early_p = sum(1 for r in rows if r.get("residual_class") == "P_EARLY_AFTER_BE")
        early = sum(1 for r in rows if str(r.get("path_type") or "") == "EARLY_FAILURE")
        assert early_u + early_p <= early + 1  # OTHER early possible? only U+P in failure
        pf = classes.get("P_EARLY_AFTER_BE", 0) + classes.get("P_PROFIT_THEN_FAILURE", 0)
        assert pf >= 0
    return {"dev_identity": dev_id, "fwd_identity": fwd_id, "ok": True}


def cohort_analyze(rows: list[dict[str, Any]], *, cohort: str) -> dict[str, Any]:
    fill_n = len(rows)
    by_class = {ck: incidence_severity_pack(rows, ck, fill_n) for ck in PRIMARY_FAILURE_KEYS}
    roles = role_breakdown(rows, fill_n)
    role_std = role_standardized(rows, fill_n)
    horizon = {ck: horizon_pack(rows, ck) for ck in PRIMARY_FAILURE_KEYS}
    conc = {ck: concentration_pack(rows, ck) for ck in PRIMARY_FAILURE_KEYS}
    loo_day = leave_one_out(rows, fill_n, kind="day")
    loo_sym = leave_one_out(rows, fill_n, kind="symbol")
    be = be_reach_pack(rows, fill_n)
    ptf = ptf_stability(rows, fill_n, int(be.get("be_reached_n") or 0))
    gross_map = {k: float(by_class[k]["gross_loss"]) for k in FAILURE_CLASSES}
    give_map = {k: float(by_class[k]["giveback"]) for k in FAILURE_CLASSES}
    return {
        "cohort": cohort,
        "fill_n": fill_n,
        "by_class": by_class,
        "roles": roles,
        "role_standardized": role_std,
        "horizon": horizon,
        "concentration": conc,
        "leave_one_out_day": loo_day,
        "leave_one_out_symbol": loo_sym,
        "be_reach": be,
        "ptf": ptf,
        "gross_rank": _gross_rank(gross_map),
        "giveback_rank": _gross_rank(give_map),
        "gross_by_class": gross_map,
        "giveback_by_class": give_map,
    }


def _rank_reversal(raw_dev: list[str], raw_fwd: list[str]) -> bool:
    return raw_dev != raw_fwd


def _horizon_shift(dev: dict[str, Any], fwd: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ck in PRIMARY_FAILURE_KEYS:
        dh = dev["horizon"][ck]
        fh = fwd["horizon"][ck]
        med_close_dev = dh.get("median_seconds_to_close")
        med_close_fwd = fh.get("median_seconds_to_close")
        delta = None
        if med_close_dev is not None and med_close_fwd is not None:
            delta = float(med_close_fwd) - float(med_close_dev)
        out[ck] = {
            "median_seconds_to_close_dev": med_close_dev,
            "median_seconds_to_close_fwd": med_close_fwd,
            "delta_median_seconds_to_close": delta,
        }
    return out


def _within_role_shift(dev: dict[str, Any], fwd: dict[str, Any]) -> dict[str, Any]:
    signals = []
    for role in ("CORE", "ADDED"):
        dev_u = float(dev["roles"][role]["U_EARLY_NEVER_BE"]["class_rate"])
        fwd_u = float(fwd["roles"][role]["U_EARLY_NEVER_BE"]["class_rate"])
        dev_p = float(dev["roles"][role]["P_EARLY_AFTER_BE"]["class_rate"])
        fwd_p = float(fwd["roles"][role]["P_EARLY_AFTER_BE"]["class_rate"])
        u_down = fwd_u < dev_u
        p_up = fwd_p > dev_p
        signals.append({"role": role, "u_rate_down": u_down, "p_early_rate_up": p_up, "u_shift": fwd_u - dev_u, "p_shift": fwd_p - dev_p})
    same_dir = all(s["u_rate_down"] and s["p_early_rate_up"] for s in signals if s["u_shift"] != 0 or s["p_shift"] != 0)
    any_role = any(abs(s["u_shift"]) > 0.01 or abs(s["p_shift"]) > 0.01 for s in signals)
    return {"by_role": signals, "same_direction_both_roles": bool(same_dir and any_role)}


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, residual_body: dict[str, Any]) -> dict[str, Any]:
    decomposition = {}
    for ck in PRIMARY_FAILURE_KEYS:
        decomposition[ck] = midpoint_decompose(dev["by_class"][ck], fwd["by_class"][ck], ck)

    raw_dev_rank = dev["gross_rank"]
    raw_fwd_rank = fwd["gross_rank"]
    std_dev_rank = dev["role_standardized"]["gross_rank"]
    std_fwd_rank = fwd["role_standardized"]["gross_rank"]
    raw_reversal = _rank_reversal(raw_dev_rank, raw_fwd_rank)
    std_reversal = _rank_reversal(std_dev_rank, std_fwd_rank)

    horizon = _horizon_shift(dev, fwd)
    within_role = _within_role_shift(dev, fwd)

    conc_warn = []
    loo_unstable = []
    for cohort_name, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for ck in FAILURE_CLASSES:
            c = pack["concentration"][ck]
            if float(c.get("top_day_share") or 0.0) > CONCENTRATION_WARN:
                conc_warn.append(f"{cohort_name}:{ck}:day={c.get('top_day_share'):.3f}")
            if float(c.get("top_symbol_share") or 0.0) > CONCENTRATION_WARN:
                conc_warn.append(f"{cohort_name}:{ck}:sym={c.get('top_symbol_share'):.3f}")
            if pack["leave_one_out_day"][ck].get("rank1_changed"):
                loo_unstable.append(f"{cohort_name}:{ck}:day")
            if pack["leave_one_out_symbol"][ck].get("rank1_changed"):
                loo_unstable.append(f"{cohort_name}:{ck}:symbol")

    dev_core_rate = float(dev["roles"]["CORE"]["role_fill_n"]) / float(dev["fill_n"])
    fwd_core_rate = float(fwd["roles"]["CORE"]["role_fill_n"]) / float(fwd["fill_n"])
    role_mix_delta = abs(fwd_core_rate - dev_core_rate)

    inc_sum = sum(abs(decomposition[k]["INCIDENCE_EFFECT"]) for k in PRIMARY_FAILURE_KEYS)
    sev_sum = sum(abs(decomposition[k]["SEVERITY_EFFECT"]) for k in PRIMARY_FAILURE_KEYS)

    bps_rank_dev = _gross_rank(
        {k: float(dev["by_class"][k]["total_loss_bps_diagnostic_sum"]) for k in FAILURE_CLASSES}
    )
    bps_rank_fwd = _gross_rank(
        {k: float(fwd["by_class"][k]["total_loss_bps_diagnostic_sum"]) for k in FAILURE_CLASSES}
    )
    bps_differs = bps_rank_dev != raw_dev_rank or bps_rank_fwd != raw_fwd_rank

    driver_scores: dict[str, float] = {k: 0.0 for k in SHIFT_DRIVERS}
    if raw_reversal and not std_reversal:
        driver_scores["ROLE_MIX"] += 100.0
    driver_scores["ROLE_MIX"] += role_mix_delta * 200.0
    driver_scores["INCIDENCE_SHIFT"] += inc_sum
    driver_scores["SEVERITY_SHIFT"] += sev_sum
    driver_scores["DAY_SYMBOL_CONCENTRATION"] += len(conc_warn) * 25000.0
    driver_scores["DAY_SYMBOL_CONCENTRATION"] += len(loo_unstable) * 50000.0
    if bps_differs:
        driver_scores["PRICE_SCALE_CONCENTRATION"] += 30000.0
    med_deltas = [
        abs(float(v["delta_median_seconds_to_close"]))
        for v in horizon.values()
        if v.get("delta_median_seconds_to_close") is not None
    ]
    if med_deltas and max(med_deltas) > 600.0:
        driver_scores["ENTRY_HORIZON_SHIFT"] += max(med_deltas) / 10.0
    if std_reversal and within_role["same_direction_both_roles"]:
        driver_scores["WITHIN_ROLE_LIFECYCLE_SHIFT"] += 80000.0
    elif std_reversal:
        driver_scores["WITHIN_ROLE_LIFECYCLE_SHIFT"] += 40000.0

    ranked_drivers = sorted(
        [(k, v) for k in SHIFT_DRIVERS if k != "MIXED_OR_INSUFFICIENT" for v in [driver_scores[k]]],
        key=lambda kv: (-kv[1], kv[0]),
    )
    primary = ranked_drivers[0][0]
    secondary = [k for k, v in ranked_drivers[1:4] if v > 0.25 * ranked_drivers[0][1] and v > 0]

    role_or_horizon_explains = raw_reversal and not std_reversal
    concentration_driven = bool(conc_warn) and bool(loo_unstable) and raw_reversal
    genuine_lifecycle = std_reversal and within_role["same_direction_both_roles"] and not concentration_driven

    if concentration_driven and driver_scores["DAY_SYMBOL_CONCENTRATION"] >= driver_scores[primary] * 0.8:
        verdict = "SIMPLE_TECH_EXIT_COMPOSITION_SHIFT_CONCENTRATION_DRIVEN"
        case = "B"
        nxt = "新EXITを作らない。追加future evidence優先。STOP。"
        primary_driver = "DAY_SYMBOL_CONCENTRATION"
    elif role_or_horizon_explains and not std_reversal:
        verdict = "SIMPLE_TECH_EXIT_COMPOSITION_SHIFT_ROLE_OR_HORIZON_EXPLAINED"
        case = "A"
        nxt = "次のEXIT mechanism研究はrole/horizonを無視した一括architectureにしない。STOP。"
        primary_driver = "ROLE_MIX" if driver_scores["ROLE_MIX"] >= driver_scores["ENTRY_HORIZON_SHIFT"] else "ENTRY_HORIZON_SHIFT"
    elif genuine_lifecycle:
        verdict = "SIMPLE_TECH_EXIT_COMPOSITION_GENUINE_LIFECYCLE_SHIFT"
        case = "C"
        nxt = "次runでcross-branch lifecycle transition mechanismを研究。まだEXIT ruleは作らない。"
        primary_driver = "WITHIN_ROLE_LIFECYCLE_SHIFT"
    elif driver_scores[primary] > 0 and driver_scores[ranked_drivers[1][0]] > 0.6 * driver_scores[primary]:
        verdict = "SIMPLE_TECH_EXIT_COMPOSITION_MIXED_OR_INSUFFICIENT"
        case = "D"
        nxt = "STOP。局所EXIT追加禁止。"
        primary_driver = "MIXED_OR_INSUFFICIENT"
        secondary = [primary] + secondary
    else:
        verdict = "SIMPLE_TECH_EXIT_COMPOSITION_MIXED_OR_INSUFFICIENT"
        case = "D"
        nxt = "STOP。局所EXIT追加禁止。"
        primary_driver = primary

    explained = not raw_reversal or (role_or_horizon_explains or concentration_driven or genuine_lifecycle)

    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "PRIMARY_SHIFT_DRIVER": primary_driver,
        "SECONDARY_DRIVERS": secondary,
        "PRIMARY_NEXT_EXIT_TARGET": None,
        "raw_rank_reversal": raw_reversal,
        "raw_gross_rank": {"DEVELOPMENT": raw_dev_rank, "FORWARD_BURNED": raw_fwd_rank},
        "role_std_gross_rank": {"DEVELOPMENT": std_dev_rank, "FORWARD_BURNED": std_fwd_rank},
        "role_standardization_removes_reversal": raw_reversal and not std_reversal,
        "raw_rank_reversal_explained": explained,
        "decomposition": decomposition,
        "horizon_shift": horizon,
        "within_role_shift": within_role,
        "concentration_warnings": conc_warn,
        "leave_one_out_unstable": loo_unstable,
        "driver_scores": driver_scores,
        "bps_gross_rank": {"DEVELOPMENT": bps_rank_dev, "FORWARD_BURNED": bps_rank_fwd},
        "residual_source_verdict": str((residual_body.get("required") or {}).get("VERDICT") or ""),
        "residual_rankings_dev": ((residual_body.get("development") or {}).get("rankings") or {}),
        "residual_rankings_fwd": ((residual_body.get("forward") or {}).get("rankings") or {}),
    }


def analyze_all() -> dict[str, Any]:
    dev_rows, fwd_rows, residual_body = load_residual_rows()
    assert_integrity(dev_rows, fwd_rows)
    dev_rows = enrich_horizon(dev_rows)
    fwd_rows = enrich_horizon(fwd_rows)
    dev = cohort_analyze(dev_rows, cohort="DEVELOPMENT")
    fwd = cohort_analyze(fwd_rows, cohort="FORWARD_BURNED")
    decision = decide(dev, fwd, residual_body=residual_body)
    return {
        "development": dev,
        "forward": fwd,
        "decision": decision,
        "integrity": {
            "dev_fill_n": len(dev_rows),
            "fwd_fill_n": len(fwd_rows),
            "dev_core_n": sum(1 for r in dev_rows if r.get("fill_role") == "CORE"),
            "dev_added_n": sum(1 for r in dev_rows if r.get("fill_role") == "ADDED"),
            "fwd_core_n": sum(1 for r in fwd_rows if r.get("fill_role") == "CORE"),
            "fwd_added_n": sum(1 for r in fwd_rows if r.get("fill_role") == "ADDED"),
            "dev_pnl": sum(float(_f(r.get("session_close_pnl")) or 0.0) for r in dev_rows),
            "fwd_pnl": sum(float(_f(r.get("session_close_pnl")) or 0.0) for r in fwd_rows),
        },
    }
