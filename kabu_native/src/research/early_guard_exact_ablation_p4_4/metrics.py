"""P4-4 compare Guard ON vs OFF. No retune."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from research.canonical_fixed_pnl_source_p3_3.ledger import pnl, wl
from research.early_guard_exact_ablation_p4_4 import (
    CANONICAL_GUARD_N,
    CLASS_DATA,
    CLASS_HARMFUL,
    CLASS_MIXED,
    CLASS_SUPPORTED,
    FULL14,
    GUARD_EXIT_REASON,
    PREDECLARED_TOP3,
    REST11,
)
from research.fixed_winner_cluster_extension_p3_4 import EXIT600_REASON, EXTEND_REASON
from run_p0_3_exact_runtime_replay_20260820 import _maxdd, _pf, _sess_stats
from small_paper.v1r_live_dual_lane import canonical_symbol_key

SESSION_CLOSE = "SESSION_CLOSE"


def _f(x: Any, nd: int = 3) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return round(v, nd)


def fill_key(row: dict[str, Any], *, nd: int = 3) -> tuple[str, str, Optional[float]]:
    return (
        str(row.get("date") or ""),
        canonical_symbol_key(row.get("symbol")),
        _f(row.get("fill_time"), nd),
    )


def admit_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("date") or ""),
        canonical_symbol_key(row.get("symbol")),
        str(row.get("anchor") or row.get("anchor_time") or ""),
    )


def index_by_fill(rows: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    out = {}
    for t in rows:
        out[fill_key(t)] = t
    return out


def match_fill(idx: dict[tuple, dict[str, Any]], row: dict[str, Any]) -> Optional[dict[str, Any]]:
    k = fill_key(row)
    hit = idx.get(k)
    if hit is not None:
        return hit
    date, sym, ft = k
    if ft is None:
        return None
    for (d, s, t), tr in idx.items():
        if d == date and s == sym and t is not None and abs(float(t) - float(ft)) < 0.05:
            return tr
    return None


def slice_days(trades: list[dict[str, Any]], days) -> list[dict[str, Any]]:
    want = set(str(d) for d in days)
    return [t for t in trades if str(t.get("date")) in want]


def summary_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [pnl(t) for t in trades]
    w = sum(1 for p in pnls if p > 1e-9)
    l = sum(1 for p in pnls if p < -1e-9)
    d = len(pnls) - w - l
    gp = round(sum(p for p in pnls if p > 0), 2)
    gl = round(sum(-p for p in pnls if p < 0), 2)
    pf = _pf(pnls)
    reasons = Counter(str(t.get("exit_reason") or "") for t in trades)
    return {
        "trades": len(trades),
        "W": w,
        "L": l,
        "D": d,
        "GP": gp,
        "GL": gl,
        "pnl": round(sum(pnls), 2),
        "PF": "Infinity" if pf == float("inf") else pf,
        "maxDD": _maxdd(trades) if trades else 0.0,
        "AM": _sess_stats(trades, "AM"),
        "PM": _sess_stats(trades, "PM"),
        "exit_reason_counts": dict(reasons),
    }


def is_guard_exit(t: dict[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == GUARD_EXIT_REASON


def reconcile_89(
    *,
    canonical: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
) -> dict[str, Any]:
    can = [t for t in canonical if is_guard_exit(t)]
    base = [t for t in baseline if is_guard_exit(t)]
    cidx = index_by_fill(can)
    bidx = index_by_fill(base)
    matched = []
    missing_in_baseline = []
    extra_in_baseline = []
    for t in can:
        hit = match_fill(bidx, t)
        if hit is None:
            missing_in_baseline.append(str(t.get("trade_id")))
        else:
            matched.append(
                {
                    "canonical_trade_id": t.get("trade_id"),
                    "baseline_trade_id": hit.get("trade_id"),
                    "date": t.get("date"),
                    "symbol": canonical_symbol_key(t.get("symbol")),
                    "fill_time": t.get("fill_time"),
                    "fill_price": t.get("fill_price"),
                    "guard_exit_time": hit.get("exit_time") or t.get("exit_time"),
                    "guard_exit_reason": hit.get("exit_reason") or t.get("exit_reason"),
                    "holding_sec": hit.get("holding_sec") if hit.get("holding_sec") is not None else t.get("holding_sec"),
                    "canonical_pnl": pnl(t),
                    "baseline_pnl": pnl(hit),
                    "triggered_guard": True,
                    "TOP10": bool(t.get("TOP10")),
                    "TOP20": bool(t.get("TOP20")),
                    "EXTEND_canonical": str(t.get("exit_reason") or "") == EXTEND_REASON,
                }
            )
    for t in base:
        if match_fill(cidx, t) is None:
            extra_in_baseline.append(str(t.get("trade_id")))
    n_match = len(matched)
    ok = (
        len(can) == CANONICAL_GUARD_N
        and len(base) == CANONICAL_GUARD_N
        and n_match == CANONICAL_GUARD_N
        and not missing_in_baseline
        and not extra_in_baseline
    )
    return {
        "ok": ok,
        "canonical_n": len(can),
        "baseline_n": len(base),
        "matched_n": n_match,
        "missing_in_baseline": missing_in_baseline,
        "extra_in_baseline": extra_in_baseline,
        "rows": matched,
    }


def destination(reason: str) -> str:
    r = str(reason or "")
    if r in {EXIT600_REASON, "CONT_EXIT_600"}:
        return "exit600"
    if r in {EXTEND_REASON, "CONT_EXTEND_750"}:
        return "extend750"
    if r == SESSION_CLOSE:
        return "session_close"
    return "other"


def guard89_off_paths(
    *,
    guard89: list[dict[str, Any]],
    off_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    oidx = index_by_fill(off_trades)
    dest = {"exit600": 0, "extend750": 0, "session_close": 0, "other": 0, "lost_fill": 0}
    dest_pnl = {k: 0.0 for k in dest}
    rows = []
    saved = 0.0
    foregone = 0.0
    saved_n = 0
    foregone_n = 0
    matched_n = 0
    for g in guard89:
        syn = {
            "date": g.get("date"),
            "symbol": g.get("symbol"),
            "fill_time": g.get("fill_time"),
        }
        off = match_fill(oidx, syn)
        on_pnl = float(g.get("baseline_pnl") if g.get("baseline_pnl") is not None else g.get("canonical_pnl") or 0.0)
        row = {
            **{k: g.get(k) for k in (
                "canonical_trade_id", "date", "symbol", "fill_time", "holding_sec",
                "canonical_pnl", "baseline_pnl", "TOP10", "TOP20",
            )},
            "guard_on_pnl": on_pnl,
        }
        if off is None:
            dest["lost_fill"] += 1
            row["off_found"] = False
            row["off_exit_reason"] = None
            row["off_pnl"] = None
            row["destination"] = "lost_fill"
            row["pnl_delta_off_minus_on"] = None
        else:
            matched_n += 1
            off_pnl = pnl(off)
            dlab = destination(str(off.get("exit_reason") or ""))
            dest[dlab] = dest.get(dlab, 0) + 1
            dest_pnl[dlab] = dest_pnl.get(dlab, 0.0) + (off_pnl - on_pnl)
            row["off_found"] = True
            row["off_trade_id"] = off.get("trade_id")
            row["off_exit_reason"] = off.get("exit_reason")
            row["off_holding_sec"] = off.get("holding_sec")
            row["off_pnl"] = off_pnl
            row["destination"] = dlab
            row["pnl_delta_off_minus_on"] = off_pnl - on_pnl
            delta_guard_minus_wait = on_pnl - off_pnl
            if off_pnl < -1e-9 and delta_guard_minus_wait > 1e-9:
                saved += delta_guard_minus_wait
                saved_n += 1
                row["saved_loss"] = delta_guard_minus_wait
            else:
                row["saved_loss"] = 0.0
            if off_pnl > 1e-9 and delta_guard_minus_wait < -1e-9:
                foregone += -delta_guard_minus_wait
                foregone_n += 1
                row["foregone_winner"] = -delta_guard_minus_wait
            else:
                row["foregone_winner"] = 0.0
        rows.append(row)
    net = saved - foregone
    ratio = (saved / foregone) if foregone > 1e-9 else None
    dest_out = {
        k: {"n": dest.get(k, 0), "pnl_delta_off_minus_on": round(dest_pnl.get(k, 0.0), 2)}
        for k in ("exit600", "extend750", "session_close", "other", "lost_fill")
    }
    return {
        "rows": rows,
        "matched_n": matched_n,
        "destination": dest_out,
        "saved_loss": round(saved, 2),
        "foregone_winner": round(foregone, 2),
        "net_guard_value": round(net, 2),
        "ratio": ratio,
        "saved_n": saved_n,
        "foregone_n": foregone_n,
    }


def portfolio_effects(
    *,
    base_trades: list[dict[str, Any]],
    off_trades: list[dict[str, Any]],
    base_admits: list[dict[str, Any]],
    off_admits: list[dict[str, Any]],
    base_fills: list[dict[str, Any]],
    off_fills: list[dict[str, Any]],
    base_cap: int,
    off_cap: int,
    base_same: int,
    off_same: int,
) -> dict[str, Any]:
    bt = {fill_key(t) for t in base_trades}
    ot = {fill_key(t) for t in off_trades}
    bf = {fill_key(t) for t in base_fills}
    of = {fill_key(t) for t in off_fills}
    ba = {admit_key(t) for t in base_admits}
    oa = {admit_key(t) for t in off_admits}
    return {
        "newly_admitted": len(oa - ba),
        "lost_admission": len(ba - oa),
        "newly_filled": len(of - bf),
        "lost_fill": len(bf - of),
        "trades_only_off": len(ot - bt),
        "trades_only_baseline": len(bt - ot),
        "same_symbol_blocked_baseline": int(base_same),
        "same_symbol_blocked_off": int(off_same),
        "same_symbol_changed": int(off_same) - int(base_same),
        "cap_blocked_baseline": int(base_cap),
        "cap_blocked_off": int(off_cap),
        "capacity_changed": int(off_cap) - int(base_cap),
    }


def tail_block(
    *,
    canonical: list[dict[str, Any]],
    guard89: list[dict[str, Any]],
    base_trades: list[dict[str, Any]],
    off_trades: list[dict[str, Any]],
    top10_ids: set[str],
    top20_ids: set[str],
) -> dict[str, Any]:
    gkeys = {fill_key({"date": r.get("date"), "symbol": r.get("symbol"), "fill_time": r.get("fill_time")}) for r in guard89}

    def _cut(ids: set[str]) -> dict[str, Any]:
        rows = [t for t in canonical if str(t.get("trade_id")) in ids]
        cut = [t for t in rows if is_guard_exit(t)]
        return {
            "n": len(ids),
            "guard_cut_n": len(cut),
            "guard_cut_ids": [str(t.get("trade_id")) for t in cut],
            "guard_cut_pnl": round(sum(pnl(t) for t in cut), 2),
        }

    ext_can = [t for t in canonical if str(t.get("exit_reason") or "") == EXTEND_REASON]
    ext_base = [t for t in base_trades if str(t.get("exit_reason") or "") == EXTEND_REASON]
    ext_off = [t for t in off_trades if str(t.get("exit_reason") or "") == EXTEND_REASON]
    unused = gkeys, PREDECLARED_TOP3
    del unused
    return {
        "TOP10": _cut(top10_ids),
        "TOP20": _cut(top20_ids),
        "CONT_EXTEND_750": {
            "canonical_n": len(ext_can),
            "baseline_n": len(ext_base),
            "guard_off_n": len(ext_off),
            "canonical_pnl": round(sum(pnl(t) for t in ext_can), 2),
            "baseline_pnl": round(sum(pnl(t) for t in ext_base), 2),
            "guard_off_pnl": round(sum(pnl(t) for t in ext_off), 2),
        },
    }


def daily_rows(base_trades: list[dict[str, Any]], off_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for day in FULL14:
        b = slice_days(base_trades, [day])
        o = slice_days(off_trades, [day])
        bp = round(sum(pnl(t) for t in b), 2)
        op = round(sum(pnl(t) for t in o), 2)
        dlt = round(op - bp, 2)
        if dlt > 0.51:
            who = "GUARD_OFF_BETTER"
        elif dlt < -0.51:
            who = "GUARD_ON_BETTER"
        else:
            who = "equal"
        out.append(
            {
                "date": day,
                "baseline_trades": len(b),
                "guard_off_trades": len(o),
                "baseline_pnl": bp,
                "guard_off_pnl": op,
                "delta_off_minus_on": dlt,
                "winner": who,
            }
        )
    return out


def classify(
    *,
    integrity: list[str],
    base: dict[str, Any],
    off: dict[str, Any],
    base_rest: dict[str, Any],
    off_rest: dict[str, Any],
    net_guard_value: Optional[float],
) -> dict[str, Any]:
    if integrity:
        return {"CLASSIFICATION": CLASS_DATA, "why": ";".join(integrity)}
    b = float(base.get("pnl") or 0)
    o = float(off.get("pnl") or 0)
    br = float(base_rest.get("pnl") or 0)
    orr = float(off_rest.get("pnl") or 0)
    net = float(net_guard_value or 0.0)
    on_full = b > o + 0.51
    off_full = o > b + 0.51
    on_rest = br > orr + 0.51
    off_rest_b = orr > br + 0.51
    supported = on_full and on_rest and net > 0
    harmful = off_full and off_rest_b and net < 0
    if supported:
        klass = CLASS_SUPPORTED
    elif harmful:
        klass = CLASS_HARMFUL
    else:
        klass = CLASS_MIXED
    return {
        "CLASSIFICATION": klass,
        "FULL14_guard_on_better": on_full,
        "REST11_guard_on_better": on_rest,
        "net_guard_value": net,
        "why": (
            f"FULL14 on={b} off={o} on_better={on_full}; "
            f"REST11 on={br} off={orr} on_better={on_rest}; net={net}"
        ),
    }
