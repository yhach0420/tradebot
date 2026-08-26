"""Predefined flags, 16-cell overlap, intersections, residuals. No new flags."""
from __future__ import annotations

from itertools import product
from typing import Any

from research.canonical_fixed_pnl_source_p3_3.ledger import _share, group_pnl, pnl, tail_blocks
from research.fixed_winner_cluster_extension_p3_4 import (
    ANCHOR_0905,
    EXTEND_REASON,
    PREDECLARED_TOP3,
    REST11,
    TOP3_SYMBOLS,
)


def attach_flags(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda t: pnl(t), reverse=True)
    top10_ids = {str(t.get("trade_id")) for t in ranked[:10]}
    out = []
    for t in rows:
        rec = dict(t)
        rec["IS_0905"] = str(t.get("anchor_time") or "") == ANCHOR_0905
        rec["IS_TOP3_DAY"] = str(t.get("date") or "") in set(PREDECLARED_TOP3)
        rec["IS_TOP3_SYMBOL"] = str(t.get("symbol") or "") in set(TOP3_SYMBOLS)
        rec["IS_CONT_EXTEND_750"] = str(t.get("exit_reason") or "") == EXTEND_REASON
        rec["IS_TOP10_WINNER"] = str(t.get("trade_id") or "") in top10_ids
        rec["n_mechanism_flags"] = int(
            rec["IS_0905"] + rec["IS_TOP3_DAY"] + rec["IS_TOP3_SYMBOL"] + rec["IS_CONT_EXTEND_750"]
        )
        rec["is_rest11"] = str(t.get("date") or "") in set(REST11)
        out.append(rec)
    return out


def bits_of(t: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        int(bool(t.get("IS_0905"))),
        int(bool(t.get("IS_TOP3_DAY"))),
        int(bool(t.get("IS_TOP3_SYMBOL"))),
        int(bool(t.get("IS_CONT_EXTEND_750"))),
    )


def overlap_16(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(pnl(t) for t in rows)
    out = []
    for bits in product((0, 1), repeat=4):
        sel = [t for t in rows if bits_of(t) == bits]
        g = group_pnl(sel)
        g.update(
            {
                "0905": bits[0],
                "top3_day": bits[1],
                "top3_symbol": bits[2],
                "extend750": bits[3],
                "cell": f"{bits[0]}{bits[1]}{bits[2]}{bits[3]}",
                "n": g["trades"],
                "share_of_total": _share(float(g["pnl"]), total),
            }
        )
        out.append(g)
    return out


def _filt(rows: list[dict[str, Any]], pred) -> list[dict[str, Any]]:
    return [t for t in rows if pred(t)]


def _block(name: str, rows: list[dict[str, Any]], total: float) -> dict[str, Any]:
    g = group_pnl(rows)
    g["name"] = name
    g["n"] = g["trades"]
    g["share_of_total"] = _share(float(g["pnl"]), total)
    return g


def intersections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(pnl(t) for t in rows)
    specs = [
        ("09:05 ∩ TOP3_DAY", lambda t: t["IS_0905"] and t["IS_TOP3_DAY"]),
        ("09:05 ∩ TOP3_SYMBOL", lambda t: t["IS_0905"] and t["IS_TOP3_SYMBOL"]),
        ("09:05 ∩ EXTEND750", lambda t: t["IS_0905"] and t["IS_CONT_EXTEND_750"]),
        ("TOP3_DAY ∩ TOP3_SYMBOL", lambda t: t["IS_TOP3_DAY"] and t["IS_TOP3_SYMBOL"]),
        ("TOP3_DAY ∩ EXTEND750", lambda t: t["IS_TOP3_DAY"] and t["IS_CONT_EXTEND_750"]),
        ("TOP3_SYMBOL ∩ EXTEND750", lambda t: t["IS_TOP3_SYMBOL"] and t["IS_CONT_EXTEND_750"]),
        ("09:05 ∩ TOP3_DAY ∩ TOP3_SYMBOL", lambda t: t["IS_0905"] and t["IS_TOP3_DAY"] and t["IS_TOP3_SYMBOL"]),
        ("09:05 ∩ TOP3_DAY ∩ EXTEND750", lambda t: t["IS_0905"] and t["IS_TOP3_DAY"] and t["IS_CONT_EXTEND_750"]),
        ("09:05 ∩ TOP3_SYMBOL ∩ EXTEND750", lambda t: t["IS_0905"] and t["IS_TOP3_SYMBOL"] and t["IS_CONT_EXTEND_750"]),
        ("TOP3_DAY ∩ TOP3_SYMBOL ∩ EXTEND750", lambda t: t["IS_TOP3_DAY"] and t["IS_TOP3_SYMBOL"] and t["IS_CONT_EXTEND_750"]),
        (
            "FULL: 09:05 ∩ TOP3_DAY ∩ TOP3_SYMBOL ∩ EXTEND750",
            lambda t: t["IS_0905"] and t["IS_TOP3_DAY"] and t["IS_TOP3_SYMBOL"] and t["IS_CONT_EXTEND_750"],
        ),
    ]
    return [_block(name, _filt(rows, pred), total) for name, pred in specs]


def residuals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(pnl(t) for t in rows)
    specs = [
        ("09:05 AND NOT TOP3_DAY", lambda t: t["IS_0905"] and not t["IS_TOP3_DAY"]),
        ("TOP3_DAY AND NOT 09:05", lambda t: t["IS_TOP3_DAY"] and not t["IS_0905"]),
        ("09:05 AND NOT TOP3_SYMBOL", lambda t: t["IS_0905"] and not t["IS_TOP3_SYMBOL"]),
        ("TOP3_SYMBOL AND NOT 09:05", lambda t: t["IS_TOP3_SYMBOL"] and not t["IS_0905"]),
        ("EXTEND750 AND NOT TOP3_DAY", lambda t: t["IS_CONT_EXTEND_750"] and not t["IS_TOP3_DAY"]),
        ("EXTEND750 AND NOT 09:05", lambda t: t["IS_CONT_EXTEND_750"] and not t["IS_0905"]),
        ("EXTEND750 AND NOT TOP3_SYMBOL", lambda t: t["IS_CONT_EXTEND_750"] and not t["IS_TOP3_SYMBOL"]),
        ("TOP3_SYMBOL AND NOT TOP3_DAY", lambda t: t["IS_TOP3_SYMBOL"] and not t["IS_TOP3_DAY"]),
        ("NONE_OF_4_FLAGS", lambda t: t["n_mechanism_flags"] == 0),
    ]
    return [_block(name, _filt(rows, pred), total) for name, pred in specs]


def top10_membership(rows: list[dict[str, Any]]) -> dict[str, Any]:
    top = [t for t in rows if t.get("IS_TOP10_WINNER")]
    by_k: dict[int, list] = {k: [] for k in range(5)}
    for t in top:
        by_k[int(t.get("n_mechanism_flags") or 0)].append(t)
    counts = []
    for k in (4, 3, 2, 1, 0):
        g = group_pnl(by_k[k])
        counts.append({"k": k, "label": f"{k}/4", "n": g["trades"], "pnl": g["pnl"]})
    return {
        "n": len(top),
        "note": "Top10 is outcome-defined. Descriptive only. Not a mechanism condition.",
        "counts": counts,
        "rows": [
            {
                "rank": i,
                "trade_id": t.get("trade_id"),
                "date": t.get("date"),
                "symbol": t.get("symbol"),
                "anchor_time": t.get("anchor_time"),
                "exit_reason": t.get("exit_reason"),
                "pnl": pnl(t),
                "IS_0905": t.get("IS_0905"),
                "IS_TOP3_DAY": t.get("IS_TOP3_DAY"),
                "IS_TOP3_SYMBOL": t.get("IS_TOP3_SYMBOL"),
                "IS_CONT_EXTEND_750": t.get("IS_CONT_EXTEND_750"),
                "n_mechanism_flags": t.get("n_mechanism_flags"),
            }
            for i, t in enumerate(sorted(top, key=lambda x: pnl(x), reverse=True), 1)
        ],
    }


def _split_block(rows: list[dict[str, Any]], name_a: str, pred_a, name_b: str) -> list[dict[str, Any]]:
    a = [t for t in rows if pred_a(t)]
    b = [t for t in rows if not pred_a(t)]
    return [
        {**group_pnl(a), "split": name_a, "n": len(a)},
        {**group_pnl(b), "split": name_b, "n": len(b)},
    ]


def decompose_0905(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s = [t for t in rows if t.get("IS_0905")]
    g = group_pnl(s)
    return {
        "universe": {**g, "n": g["trades"]},
        "TOP3_vs_REST11": _split_block(s, "TOP3_DAY", lambda t: t["IS_TOP3_DAY"], "REST11"),
        "TOP3_SYMBOL_vs_other": _split_block(s, "TOP3_SYMBOL", lambda t: t["IS_TOP3_SYMBOL"], "other"),
        "285A_vs_non285A": _split_block(s, "285A", lambda t: str(t.get("symbol")) == "285A", "non-285A"),
        "EXTEND750_vs_non": _split_block(s, "EXTEND750", lambda t: t["IS_CONT_EXTEND_750"], "non-EXTEND"),
        "TOP10_vs_non": _split_block(s, "TOP10", lambda t: t["IS_TOP10_WINNER"], "non-TOP10"),
    }


def decompose_symbol(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    s = [t for t in rows if str(t.get("symbol")) == symbol]
    g = group_pnl(s)
    return {
        "symbol": symbol,
        "universe": {**g, "n": g["trades"]},
        "0905_vs_non": _split_block(s, "09:05", lambda t: t["IS_0905"], "non-09:05"),
        "TOP3_vs_REST11": _split_block(s, "TOP3_DAY", lambda t: t["IS_TOP3_DAY"], "REST11"),
        "EXTEND750_vs_non": _split_block(s, "EXTEND750", lambda t: t["IS_CONT_EXTEND_750"], "non-EXTEND"),
    }


def decompose_extend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s = [t for t in rows if t.get("IS_CONT_EXTEND_750")]
    g = group_pnl(s)
    tails = tail_blocks(s)
    return {
        "universe": {**g, "n": g["trades"]},
        "TOP3_vs_REST11": _split_block(s, "TOP3_DAY", lambda t: t["IS_TOP3_DAY"], "REST11"),
        "0905_vs_non": _split_block(s, "09:05", lambda t: t["IS_0905"], "non-09:05"),
        "TOP3_SYMBOL_vs_other": _split_block(s, "TOP3_SYMBOL", lambda t: t["IS_TOP3_SYMBOL"], "other"),
        "285A_vs_non285A": _split_block(s, "285A", lambda t: str(t.get("symbol")) == "285A", "non-285A"),
        "pnl_tail": {
            "top1": tails.get("top1"),
            "top3": tails.get("top3"),
            "top5": tails.get("top5"),
            "top10": tails.get("top10"),
            "EX_TOP1": tails.get("EX_TOP1"),
            "EX_TOP3": tails.get("EX_TOP3"),
            "EX_TOP5": tails.get("EX_TOP5"),
            "EX_TOP10": tails.get("EX_TOP10"),
            "note": "Descriptive only. Not an exclusion rule.",
        },
        "rows": [
            {
                "trade_id": t.get("trade_id"),
                "date": t.get("date"),
                "session": t.get("session"),
                "anchor_time": t.get("anchor_time"),
                "symbol": t.get("symbol"),
                "fill_time": t.get("fill_time"),
                "t600": None if t.get("fill_time") is None else float(t["fill_time"]) + 600.0,
                "exit_time": t.get("exit_time"),
                "pnl": pnl(t),
                "IS_0905": t.get("IS_0905"),
                "IS_TOP3_DAY": t.get("IS_TOP3_DAY"),
                "IS_TOP3_SYMBOL": t.get("IS_TOP3_SYMBOL"),
                "IS_TOP10_WINNER": t.get("IS_TOP10_WINNER"),
            }
            for t in s
        ],
    }


def classify_cluster(inter: list[dict[str, Any]], resid: list[dict[str, Any]], total: float) -> dict[str, Any]:
    full = next((r for r in inter if str(r.get("name", "")).startswith("FULL")), {})
    none = next((r for r in resid if r.get("name") == "NONE_OF_4_FLAGS"), {})
    residual_material = [
        r for r in resid if r.get("name") != "NONE_OF_4_FLAGS" and abs(float(r.get("pnl") or 0)) >= 200000
    ]
    full_share = abs(float(full.get("share_of_total") or 0))
    none_pnl = float(none.get("pnl") or 0)
    if full_share >= 0.60 and len(residual_material) <= 1:
        label = "SINGLE_OVERLAPPING_CLUSTER"
        why = "FULL 4-flag cell dominates total PnL and residuals are small"
    elif none_pnl > 0 and float(none.get("PF") or 0) > 1.2 and full_share < 0.40:
        label = "BROAD_WITH_CONCENTRATED_TAIL"
        why = "NONE_OF_4_FLAGS remains profitable; concentration is a tail on a broader base"
    elif len(residual_material) >= 2 and full_share < 0.50:
        label = "MULTIPLE_PARTLY_INDEPENDENT_CLUSTERS"
        why = "FULL intersection is not majority; several flag-residuals each carry material PnL"
    else:
        label = "NO_CLEAR_CLUSTER"
        why = "overlap / residual pattern does not meet a cluster rule"
    return {
        "WINNER_CONCENTRATION": label,
        "why": why,
        "full_n": full.get("n"),
        "full_pnl": full.get("pnl"),
        "full_share": full.get("share_of_total"),
        "none_n": none.get("n"),
        "none_pnl": none.get("pnl"),
        "none_PF": none.get("PF"),
        "material_residuals": [{"name": r["name"], "n": r["n"], "pnl": r["pnl"]} for r in residual_material],
        "note": "Shares of overlapping sets are not added. Cluster is from cells and residuals.",
    }
