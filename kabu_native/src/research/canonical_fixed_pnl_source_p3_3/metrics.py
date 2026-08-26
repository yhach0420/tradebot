"""Descriptive stats, Spearman, PnL-source classification. No cutoff / no retune."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_fixed_pnl_source_p3_3.ledger import group_pnl, pnl, top_winner_rows


def _finite(xs: list[Any]) -> np.ndarray:
    out = []
    for x in xs:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if v == v and v not in (float("inf"), float("-inf")):
            out.append(v)
    return np.asarray(out, dtype=float)


def dist(xs: list[Any]) -> dict[str, Any]:
    a = _finite(xs)
    if a.size == 0:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None, "p10": None, "p90": None}
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "p10": float(np.percentile(a, 10)),
        "p25": float(np.percentile(a, 25)),
        "p75": float(np.percentile(a, 75)),
        "p90": float(np.percentile(a, 90)),
    }


def _avg_rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=float)
    i = 0
    while i < a.size:
        j = i + 1
        while j < a.size and a[order[j]] == a[order[i]]:
            j += 1
        avg = 0.5 * (i + 1 + j)
        ranks[order[i:j]] = avg
        i = j
    return ranks


def spearman(x: list[Any], y: list[Any]) -> dict[str, Any]:
    xa: list[float] = []
    ya: list[float] = []
    for a, b in zip(x, y):
        try:
            fa = float(a)
            fb = float(b)
        except (TypeError, ValueError):
            continue
        if fa != fa or fb != fb:
            continue
        if fa in (float("inf"), float("-inf")) or fb in (float("inf"), float("-inf")):
            continue
        xa.append(fa)
        ya.append(fb)
    n = len(xa)
    if n < 3:
        return {"n": n, "rho": None}
    try:
        from scipy.stats import spearmanr

        rho, _p = spearmanr(xa, ya)
        rho_f = None if rho != rho else float(rho)
        return {"n": n, "rho": rho_f}
    except Exception:
        rx = _avg_rank(np.asarray(xa, dtype=float))
        ry = _avg_rank(np.asarray(ya, dtype=float))
        if float(np.std(rx)) < 1e-18 or float(np.std(ry)) < 1e-18:
            return {"n": n, "rho": None}
        return {"n": n, "rho": float(np.corrcoef(rx, ry)[0, 1])}


def capture_buckets(vals: list[Any]) -> dict[str, Any]:
    a = _finite(vals)
    if a.size == 0:
        return {"n": 0, "lt0": 0, "in_0_1": 0, "gt1": 0, "mean": None, "median": None}
    return {
        "n": int(a.size),
        "lt0": int(np.sum(a < 0)),
        "in_0_1": int(np.sum((a >= 0) & (a <= 1))),
        "gt1": int(np.sum(a > 1)),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
    }


def holding_dist(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return dist([r.get("holding_sec") for r in rows])


def slice_exit_and_tail(rows: list[dict[str, Any]], total_primary: float) -> dict[str, Any]:
    from research.canonical_fixed_pnl_source_p3_3.ledger import exit_table, _share

    g = group_pnl(rows)
    tails = {}
    ranked = sorted(rows, key=lambda t: pnl(t), reverse=True)
    slice_pnl = float(g["pnl"])
    for n in (1, 3, 5):
        top = ranked[:n]
        tp = sum(pnl(t) for t in top)
        tails[f"top{n}"] = {
            "combined_pnl": round(tp, 2),
            "share_of_slice": _share(tp, slice_pnl),
            "share_of_primary": _share(tp, total_primary),
            "rows": top_winner_rows(rows, n),
        }
    return {
        **g,
        "exit_reasons": exit_table(rows),
        "holding": holding_dist(rows),
        "trade_tail": tails,
    }


def path_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys_mid = [f"mid_markout_{h}" for h in (1, 10, 60, 180, 600)]
    out: dict[str, Any] = {
        "n": len(rows),
        "pnl": round(sum(pnl(t) for t in rows), 2),
        "holding_sec": holding_dist(rows),
        "executable_mfe": dist([r.get("executable_mfe") for r in rows]),
        "executable_mae": dist([r.get("executable_mae") for r in rows]),
        "mid_mfe": dist([r.get("mid_mfe") for r in rows]),
        "mid_mae": dist([r.get("mid_mae") for r in rows]),
        "capture_ratio": capture_buckets([r.get("capture_ratio") for r in rows]),
    }
    for k in keys_mid:
        out[k] = dist([r.get(k) for r in rows])
    return out


def exit_path_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        by[str(r.get("exit_reason") or "")].append(r)
    out = []
    for reason, ts in sorted(by.items(), key=lambda kv: sum(pnl(t) for t in kv[1]), reverse=True):
        g = group_pnl(ts)
        cap = [t.get("capture_ratio") for t in ts]
        out.append(
            {
                "exit_reason": reason,
                **g,
                "median_executable_mfe": dist([t.get("executable_mfe") for t in ts])["median"],
                "mean_executable_mfe": dist([t.get("executable_mfe") for t in ts])["mean"],
                "median_executable_mae": dist([t.get("executable_mae") for t in ts])["median"],
                "mean_executable_mae": dist([t.get("executable_mae") for t in ts])["mean"],
                "median_mid_mfe": dist([t.get("mid_mfe") for t in ts])["median"],
                "mean_mid_mfe": dist([t.get("mid_mfe") for t in ts])["mean"],
                "median_mid_mae": dist([t.get("mid_mae") for t in ts])["median"],
                "mean_mid_mae": dist([t.get("mid_mae") for t in ts])["mean"],
                "capture": capture_buckets(cap),
            }
        )
    return out


def rank_quintiles(rows: list[dict[str, Any]], total: float, top20_ids: set[str]) -> list[dict[str, Any]]:
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        q = r.get("rank_quintile")
        if q:
            by[str(q)].append(r)
    out = []
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
        ts = by.get(q) or []
        g = group_pnl(ts)
        g["rank_quintile"] = q
        g["positive_tail_top20_n"] = sum(1 for t in ts if str(t.get("trade_id")) in top20_ids)
        out.append(g)
    missing = [r for r in rows if not r.get("rank_quintile")]
    if missing:
        g = group_pnl(missing)
        g["rank_quintile"] = "MISSING"
        g["positive_tail_top20_n"] = sum(1 for t in missing if str(t.get("trade_id")) in top20_ids)
        out.append(g)
    return out


def exec_state_block(rows: list[dict[str, Any]], top10: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = (
        "fill_latency_ms",
        "first_ask_minus_limit_bps",
        "min_ask_minus_limit_bps",
        "spread_bps_at_anchor",
        "spread_bps_at_fill",
        "execution_advantage_bps",
    )
    top_ids = {str(t.get("trade_id")) for t in top10}
    rest = [r for r in rows if str(r.get("trade_id")) not in top_ids]
    pnls = [pnl(r) for r in rows]
    out: dict[str, Any] = {}
    for m in metrics:
        vals = [r.get(m) for r in rows]
        out[m] = {
            **dist(vals),
            "spearman_vs_pnl": spearman(vals, pnls),
            "top10_winners": dist([r.get(m) for r in top10]),
            "remaining": dist([r.get(m) for r in rest]),
        }
    return out


def mechanism_same(top3: dict[str, Any], rest: dict[str, Any]) -> dict[str, Any]:
    def dom(blk: dict[str, Any]) -> Optional[str]:
        ers = blk.get("exit_reasons") or []
        if not ers:
            return None
        return str(ers[0].get("exit_reason"))

    d3, dr = dom(top3), dom(rest)
    s3 = ((top3.get("trade_tail") or {}).get("top5") or {}).get("share_of_slice")
    sr = ((rest.get("trade_tail") or {}).get("top5") or {}).get("share_of_slice")
    h3 = ((top3.get("holding") or {}).get("median"))
    hr = ((rest.get("holding") or {}).get("median"))
    same_exit = d3 is not None and d3 == dr
    close_tail = s3 is not None and sr is not None and abs(float(s3) - float(sr)) < 0.25
    close_hold = h3 is not None and hr is not None and abs(float(h3) - float(hr)) < 120.0

    def reason_pnl(blk: dict[str, Any], name: str) -> float:
        for e in blk.get("exit_reasons") or []:
            if str(e.get("exit_reason")) == name:
                return float(e.get("pnl") or 0.0)
        return 0.0

    opposite = []
    names = {str(e.get("exit_reason")) for e in (top3.get("exit_reasons") or [])} | {
        str(e.get("exit_reason")) for e in (rest.get("exit_reasons") or [])
    }
    for name in names:
        a, b = reason_pnl(top3, name), reason_pnl(rest, name)
        if abs(a) >= 1.0 and abs(b) >= 1.0 and a * b < 0:
            opposite.append(name)
    if opposite:
        label = "DIFFERENT_MECHANISM"
    else:
        label = "SAME_MECHANISM" if (same_exit and (close_tail or close_hold)) else "DIFFERENT_MECHANISM"
    return {
        "label": label,
        "dominant_exit_TOP3": d3,
        "dominant_exit_REST11": dr,
        "top5_share_TOP3": s3,
        "top5_share_REST11": sr,
        "median_holding_TOP3": h3,
        "median_holding_REST11": hr,
        "same_dominant_exit": same_exit,
        "opposite_sign_exit_reasons": opposite,
    }


def classify_source(ev: dict[str, Any]) -> dict[str, Any]:
    """One Primary + optional Secondaries from observed shares. Not a trading rule."""
    fired: list[tuple[str, float, str]] = []

    def add(name: str, strength: Optional[float], why: str) -> None:
        if strength is None:
            return
        fired.append((name, float(strength), why))

    add("TOP3_DAY_REGIME", ev.get("top3_days_share"), "predeclared TOP3 day signed share of PRIMARY pnl")
    add("RARE_LARGE_WINNER_TAIL", ev.get("top10_trade_share"), "top10 trades signed share of PRIMARY pnl")
    add("SYMBOL_CONCENTRATION", ev.get("top1_symbol_share"), "top1 symbol signed share")
    if ev.get("top3_symbol_share") is not None and float(ev["top3_symbol_share"]) > float(ev.get("top1_symbol_share") or 0):
        # keep top1 as the symbol flag strength; also fire if top3 is very high
        if float(ev["top3_symbol_share"]) >= 0.60:
            add("SYMBOL_CONCENTRATION", ev.get("top3_symbol_share"), "top3 symbols signed share")
    add("EXIT_PATH_TAIL", ev.get("top_exit_share"), "largest actual exit_reason signed share")
    add("ANCHOR_TIME_CONCENTRATION", ev.get("top3_anchor_share"), "top3 clock-grid anchors signed share")

    material = [(n, s, w) for n, s, w in fired if abs(s) >= 0.40]
    # BROAD if neither day nor trade tail is concentrated
    t10 = ev.get("top10_trade_share")
    d3 = ev.get("top3_days_share")
    if t10 is not None and d3 is not None and abs(float(t10)) < 0.30 and abs(float(d3)) < 0.40:
        material.append(("BROAD_SMALL_EDGE", 1.0 - abs(float(t10)), "pnl not concentrated in top10 trades or TOP3 days"))

    exec_rho = ev.get("exec_spearman_abs_max")
    exit_cap = ev.get("exit_path_with_high_capture")
    if exec_rho is not None and float(exec_rho) >= 0.25 and exit_cap:
        material.append(("EXECUTION_PLUS_EXIT_PATH", float(exec_rho), "execution-state Spearman plus EXIT path capture"))

    # unique by name, keep max strength
    best: dict[str, tuple[float, str]] = {}
    for n, s, w in material:
        if n not in best or abs(s) > abs(best[n][0]):
            best[n] = (s, w)

    ranked = sorted(best.items(), key=lambda kv: abs(kv[1][0]), reverse=True)
    if not ranked:
        return {"PRIMARY_PNL_SOURCE": "NO_CLEAR_SOURCE", "SECONDARY_PNL_SOURCES": [], "evidence": fired}

    primary_name, (ps, pw) = ranked[0]
    secondaries = []
    mixed = False
    if len(ranked) >= 2 and abs(ranked[1][1][0]) >= 0.40 and abs(abs(ranked[0][1][0]) - abs(ranked[1][1][0])) < 0.10:
        mixed = True
        primary_name = "MIXED"
        secondaries = [n for n, _ in ranked if n != "MIXED"]
    else:
        secondaries = [n for n, _ in ranked[1:]]

    return {
        "PRIMARY_PNL_SOURCE": primary_name,
        "SECONDARY_PNL_SOURCES": secondaries,
        "primary_strength": None if mixed else ps,
        "primary_why": "two material sources within 0.10" if mixed else pw,
        "material": [{"name": n, "strength": s, "why": w} for n, (s, w) in ranked],
        "all_candidates": [{"name": n, "strength": s, "why": w} for n, s, w in fired],
    }
