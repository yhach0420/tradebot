"""P4-1 local diagnostic + portfolio compare + one-shot status. No retune."""
from __future__ import annotations

from typing import Any, Optional

from research.canonical_fixed_pnl_source_p3_3.ledger import pnl, wl
from research.fixed_winner_cluster_extension_p3_4 import EXIT600_REASON, EXTEND_REASON
from research.mid_hold_gate_p4_1 import (
    CHECKPOINTS_SEC,
    EXIT_REASON,
    PREDECLARED_TOP3,
    REST11,
    STATUS_DESTRUCTIVE,
    STATUS_HARM,
    STATUS_INTEGRITY,
    STATUS_MIXED,
    STATUS_NO_COVERAGE,
    STATUS_NOT_FALSIFIED,
)
from run_p0_3_exact_runtime_replay_20260820 import _maxdd, _pf, _sess_stats
from small_paper.v1r_live_dual_lane import canonical_symbol_key


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


def index_canonical(trades: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    out = {}
    for t in trades:
        out[fill_key(t)] = t
    return out


def match_canonical(idx: dict[tuple, dict[str, Any]], row: dict[str, Any]) -> Optional[dict[str, Any]]:
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


def reached_600(tr: dict[str, Any]) -> bool:
    r = str(tr.get("exit_reason") or "")
    return r in {EXIT600_REASON, EXTEND_REASON, "CONT_EXIT_600", "CONT_EXTEND_750"}


def summary_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [pnl(t) for t in trades]
    w = sum(1 for p in pnls if p > 1e-9)
    l = sum(1 for p in pnls if p < -1e-9)
    d = len(pnls) - w - l
    gp = round(sum(p for p in pnls if p > 0), 2)
    gl = round(sum(-p for p in pnls if p < 0), 2)
    pf = _pf(pnls)
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
    }


def slice_days(trades: list[dict[str, Any]], days) -> list[dict[str, Any]]:
    want = set(days)
    return [t for t in trades if str(t.get("date")) in want]


def first_triggers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[tuple, dict[str, Any]] = {}
    for r in records:
        if not r.get("first_trigger") and not r.get("gate_true"):
            continue
        if not r.get("gate_true"):
            continue
        k = fill_key(r)
        prev = by.get(k)
        if prev is None or int(r.get("checkpoint") or 10**9) < int(prev.get("checkpoint") or 10**9):
            by[k] = r
    return list(by.values())


def local_diagnostic(
    *,
    records: list[dict[str, Any]],
    canonical: list[dict[str, Any]],
    top10_ids: set[str],
    top20_ids: set[str],
) -> dict[str, Any]:
    idx = index_canonical(canonical)
    trigs = first_triggers(records)
    by_cp = {int(h): 0 for h in CHECKPOINTS_SEC}
    classes = {"WIN": 0, "LOSS": 0, "DRAW": 0, "UNMATCHED": 0}
    rows = []
    for r in trigs:
        tr = match_canonical(idx, r)
        h = int(r.get("checkpoint") or 0)
        if h in by_cp:
            by_cp[h] += 1
        label = "UNMATCHED"
        p = None
        tid = None
        ext = False
        if tr is not None:
            p = pnl(tr)
            label = wl(p)
            tid = str(tr.get("trade_id") or "")
            ext = str(tr.get("exit_reason") or "") == EXTEND_REASON
        classes[label] = classes.get(label, 0) + 1
        rows.append(
            {
                **{k: r.get(k) for k in (
                    "date", "symbol", "fill_time", "fill_price", "checkpoint",
                    "current_bid_return", "executable_mfe", "off",
                )},
                "canonical_class": label,
                "canonical_pnl": p,
                "trade_id": tid,
                "TOP10": bool(tid and tid in top10_ids),
                "TOP20": bool(tid and tid in top20_ids),
                "EXTEND35": ext,
            }
        )

    def _cut(flag: str) -> dict[str, Any]:
        sel = [x for x in rows if x.get(flag)]
        return {
            "cut_n": len(sel),
            "cut_rate": (len(sel) / n_flag) if (n_flag := {"TOP10": 10, "TOP20": 20, "EXTEND35": 35}[flag]) else None,
            "canonical_future_pnl": round(sum(float(x.get("canonical_pnl") or 0.0) for x in sel), 2),
            "trade_ids": [x.get("trade_id") for x in sel],
        }

    loss48 = [t for t in canonical if reached_600(t) and wl(pnl(t)) == "LOSS"]
    trig_idx = {fill_key(r): r for r in trigs}
    triggered_loss = sum(1 for t in loss48 if match_canonical(trig_idx, t) is not None)

    return {
        "n": len(trigs),
        "by_checkpoint": by_cp,
        "canonical_WIN": classes["WIN"],
        "canonical_LOSS": classes["LOSS"],
        "canonical_DRAW": classes["DRAW"],
        "canonical_UNMATCHED": classes["UNMATCHED"],
        "rows": rows,
        "loss48_n": len(loss48),
        "loss_triggered_n": triggered_loss,
        "loss_not_triggered_n": len(loss48) - triggered_loss,
        "loss_first_trigger_dist": {
            str(h): sum(
                1
                for x in rows
                if int(x.get("checkpoint") or 0) == h and x.get("canonical_class") == "LOSS" and reached_600(
                    match_canonical(idx, x) or {}
                )
            )
            for h in CHECKPOINTS_SEC
        },
        "TOP10": _cut("TOP10"),
        "TOP20": _cut("TOP20"),
        "EXTEND35": _cut("EXTEND35"),
    }


def portfolio_effects(
    *,
    base_trades: list[dict[str, Any]],
    gate_trades: list[dict[str, Any]],
    base_admits: list[dict[str, Any]],
    gate_admits: list[dict[str, Any]],
    base_fills: list[dict[str, Any]],
    gate_fills: list[dict[str, Any]],
    base_cap: int,
    gate_cap: int,
    base_same: int,
    gate_same: int,
) -> dict[str, Any]:
    bt = {fill_key(t) for t in base_trades}
    gt = {fill_key(t) for t in gate_trades}
    bf = {fill_key(t) for t in base_fills}
    gf = {fill_key(t) for t in gate_fills}
    ba = {admit_key(t) for t in base_admits}
    ga = {admit_key(t) for t in gate_admits}
    early = [
        t
        for t in gate_trades
        if str(t.get("exit_reason") or "") == EXIT_REASON and fill_key(t) in bt
    ]
    return {
        "early_exited": len(early),
        "early_exited_pnl_gate": round(sum(pnl(t) for t in early), 2),
        "newly_admitted": len(ga - ba),
        "lost_admission": len(ba - ga),
        "newly_filled": len(gf - bf),
        "lost_fill": len(bf - gf),
        "trades_only_gate": len(gt - bt),
        "trades_only_baseline": len(bt - gt),
        "same_symbol_blocked_baseline": int(base_same),
        "same_symbol_blocked_gate": int(gate_same),
        "same_symbol_changed": int(gate_same) - int(base_same),
        "cap_blocked_baseline": int(base_cap),
        "cap_blocked_gate": int(gate_cap),
        "capacity_changed": int(gate_cap) - int(base_cap),
    }


def classify_status(
    *,
    integrity_flags: list[str],
    local: dict[str, Any],
    base: dict[str, Any],
    gate: dict[str, Any],
    base_rest: dict[str, Any],
    gate_rest: dict[str, Any],
) -> dict[str, Any]:
    top10_cut = int((local.get("TOP10") or {}).get("cut_n") or 0)
    loss_trig = int(local.get("loss_triggered_n") or 0)
    b_pnl = float(base.get("pnl") or 0)
    g_pnl = float(gate.get("pnl") or 0)
    br_pnl = float(base_rest.get("pnl") or 0)
    gr_pnl = float(gate_rest.get("pnl") or 0)

    def _pfv(block):
        v = block.get("PF")
        if v in (None, "Infinity"):
            return None
        return float(v)

    if integrity_flags:
        st = STATUS_INTEGRITY
        why = ";".join(integrity_flags)
    elif top10_cut > 0:
        st = STATUS_DESTRUCTIVE
        why = f"TOP10_cut={top10_cut}"
    elif loss_trig == 0:
        st = STATUS_NO_COVERAGE
        why = "canonical LOSS48 triggered = 0"
    elif g_pnl < b_pnl - 0.51 or gr_pnl < br_pnl - 0.51:
        st = STATUS_HARM
        why = f"FULL14 {g_pnl} vs {b_pnl}; REST11 {gr_pnl} vs {br_pnl}"
    else:
        mixed = []
        bpf, gpf = _pfv(base), _pfv(gate)
        brpf, grpf = _pfv(base_rest), _pfv(gate_rest)
        if bpf is not None and gpf is not None and gpf + 1e-12 < bpf:
            mixed.append("FULL14_PF")
        if brpf is not None and grpf is not None and grpf + 1e-12 < brpf:
            mixed.append("REST11_PF")
        if float(gate.get("maxDD") or 0) < float(base.get("maxDD") or 0) - 0.51:
            mixed.append("FULL14_maxDD")
        if float(gate_rest.get("maxDD") or 0) < float(base_rest.get("maxDD") or 0) - 0.51:
            mixed.append("REST11_maxDD")
        if int((local.get("TOP20") or {}).get("cut_n") or 0) > 0:
            mixed.append("TOP20_cut")
        if int((local.get("EXTEND35") or {}).get("cut_n") or 0) > 0:
            mixed.append("EXTEND35_cut")
        if mixed:
            st = STATUS_MIXED
            why = ",".join(mixed)
        else:
            st = STATUS_NOT_FALSIFIED
            why = "no obvious winner destruction / coverage-zero / portfolio harm on reused FULL14"
    return {
        "STATUS": st,
        "why": why,
        "TOP10_cut": top10_cut,
        "loss_triggered_n": loss_trig,
        "note": (
            "NOT_FALSIFIED is not validated/approved/robust. "
            "It only permits a frozen Shadow prospective on new Capture."
        ),
    }
