"""P1 canonical ledger load, SHA reconcile, concentration stats. No path scan."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

_NATIVE = Path(__file__).resolve().parents[3]
if str(_NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(_NATIVE / "scripts"))

from research.canonical_fixed_pnl_source_p3_3 import ANCHORS, TAIL_NS
from run_p0_3_exact_runtime_replay_20260820 import _ledger_sha, _pf


def pnl(t: dict[str, Any]) -> float:
    return float(t.get("pnl_yen_100") or 0.0)


def wl(p: float) -> str:
    if p > 1e-9:
        return "WIN"
    if p < -1e-9:
        return "LOSS"
    return "DRAW"


def group_pnl(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [pnl(t) for t in rows]
    gp = sum(x for x in pnls if x > 0)
    gl = sum(-x for x in pnls if x < 0)
    w = sum(1 for x in pnls if x > 1e-9)
    l = sum(1 for x in pnls if x < -1e-9)
    d = len(pnls) - w - l
    pf = _pf(pnls) if pnls else None
    if pf == float("inf"):
        pf_out: Any = "Infinity"
    else:
        pf_out = pf
    return {
        "trades": len(rows),
        "win": w,
        "loss": l,
        "draw": d,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "pnl": round(sum(pnls), 2),
        "PF": pf_out,
        "mean": float(np.mean(pnls)) if pnls else None,
        "median": float(np.median(pnls)) if pnls else None,
        "mean_holding_sec": float(np.mean([float(t.get("holding_sec") or 0.0) for t in rows])) if rows else None,
        "median_holding_sec": float(np.median([float(t.get("holding_sec") or 0.0) for t in rows])) if rows else None,
    }


def distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [pnl(t) for t in rows]
    arr = np.asarray(pnls, dtype=float)
    g = group_pnl(rows)
    qs = {}
    for name, q in (("p10", 10), ("p25", 25), ("p75", 75), ("p90", 90), ("p95", 95), ("p99", 99)):
        qs[name] = float(np.percentile(arr, q)) if arr.size else None
    return {
        **g,
        **qs,
        "positive_n": g["win"],
        "negative_n": g["loss"],
        "zero_n": g["draw"],
    }


def _share(part: float, total: float) -> Optional[float]:
    if abs(total) < 1e-12:
        return None
    return float(part) / float(total)


def tail_blocks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(pnl(t) for t in rows)
    ranked = sorted(rows, key=lambda t: pnl(t), reverse=True)
    worst = sorted(rows, key=lambda t: pnl(t))
    out: dict[str, Any] = {}
    for n in TAIL_NS:
        top = ranked[:n]
        bot = worst[:n]
        tp = sum(pnl(t) for t in top)
        bp = sum(pnl(t) for t in bot)
        out[f"top{n}"] = {
            "n": len(top),
            "combined_pnl": round(tp, 2),
            "signed_share_of_total_pnl": _share(tp, total),
        }
        out[f"worst{n}"] = {
            "n": len(bot),
            "combined_pnl": round(bp, 2),
            "signed_share_of_total_pnl": _share(bp, total),
        }
        rem = ranked[n:]
        out[f"EX_TOP{n}"] = group_pnl(rem)
    return out


def top_winner_rows(rows: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda t: pnl(t), reverse=True)[:n]
    return [
        {
            "rank": i,
            "trade_id": t.get("trade_id"),
            "date": t.get("date"),
            "anchor_time": t.get("anchor_time"),
            "symbol": t.get("symbol"),
            "session": t.get("session"),
            "exit_reason": t.get("exit_reason"),
            "holding_sec": t.get("holding_sec"),
            "pnl": pnl(t),
        }
        for i, t in enumerate(ranked, 1)
    ]


def day_table(rows: list[dict[str, Any]], p1_daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_p1 = {str(d.get("date")): d for d in p1_daily}
    by: dict[str, list] = defaultdict(list)
    for t in rows:
        by[str(t.get("date"))].append(t)
    out = []
    for day in sorted(by):
        g = group_pnl(by[day])
        sha = _ledger_sha(by[day])
        src = by_p1.get(day) or {}
        out.append(
            {
                **g,
                "date": day,
                "ledger_sha": sha,
                "p1_trades": src.get("trades"),
                "p1_pnl": src.get("pnl"),
                "p1_ledger_sha": src.get("ledger_sha"),
                "sha_match": sha == src.get("ledger_sha"),
                "count_match": g["trades"] == src.get("trades"),
                "pnl_match": abs(float(g["pnl"]) - float(src.get("pnl") or 0.0)) < 0.51,
            }
        )
    return out


def symbol_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list] = defaultdict(list)
    for t in rows:
        by[str(t.get("symbol"))].append(t)
    out = []
    for sym, ts in by.items():
        g = group_pnl(ts)
        days: dict[str, float] = defaultdict(float)
        for t in ts:
            days[str(t.get("date"))] += pnl(t)
        out.append(
            {
                **g,
                "symbol": sym,
                "winning_days": sum(1 for v in days.values() if v > 1e-9),
                "losing_days": sum(1 for v in days.values() if v < -1e-9),
            }
        )
    out.sort(key=lambda r: float(r["pnl"]), reverse=True)
    return out


def exit_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(pnl(t) for t in rows)
    by: dict[str, list] = defaultdict(list)
    for t in rows:
        by[str(t.get("exit_reason") or "")].append(t)
    out = []
    for reason, ts in sorted(by.items()):
        g = group_pnl(ts)
        g["exit_reason"] = reason
        g["count"] = g["trades"]
        g["signed_share_of_total_pnl"] = _share(float(g["pnl"]), total)
        out.append(g)
    out.sort(key=lambda r: float(r["pnl"]), reverse=True)
    return out


def anchor_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list] = defaultdict(list)
    for t in rows:
        by[str(t.get("anchor_time") or "")].append(t)
    out = []
    for an in ANCHORS:
        g = group_pnl(by.get(an) or [])
        g["anchor_time"] = an
        out.append(g)
    return out


def am_pm_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for sess in ("AM", "PM"):
        ts = [t for t in rows if t.get("session") == sess]
        g = group_pnl(ts)
        g["session"] = sess
        g["exit_reasons"] = exit_table(ts)
        ranked = sorted(ts, key=lambda t: pnl(t), reverse=True)
        tot = sum(pnl(t) for t in rows)
        top5 = ranked[:5]
        g["top5_pnl"] = round(sum(pnl(t) for t in top5), 2)
        g["top5_share_of_primary"] = _share(sum(pnl(t) for t in top5), tot)
        out.append(g)
    return out


def symbol_share_pack(sym_rows: list[dict[str, Any]], total: float) -> dict[str, Any]:
    if not sym_rows:
        return {"top1_symbol": None, "top1_pnl": None, "top1_share": None, "top3_share": None, "top5_share": None}
    t1 = sym_rows[0]
    def sh(n):
        return _share(sum(float(r["pnl"]) for r in sym_rows[:n]), total)
    return {
        "top1_symbol": t1["symbol"],
        "top1_pnl": t1["pnl"],
        "top1_share": sh(1),
        "top3_symbols": [r["symbol"] for r in sym_rows[:3]],
        "top3_pnl": round(sum(float(r["pnl"]) for r in sym_rows[:3]), 2),
        "top3_share": sh(3),
        "top5_share": sh(5),
    }
