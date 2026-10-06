"""Contemporaneous correlation, TOPIX beta, sector-relative similarity. Not signal ranking."""
from __future__ import annotations

from typing import Any

from research.fixed_daytrade_universe_composition_audit_v1 import CORR_REDUNDANT
from research.fixed_daytrade_universe_composition_audit_v1.stats import mean, ols_alpha_beta, pearson


def aligned_returns(by_symbol: dict[str, list[tuple[str, float]]]) -> tuple[list[str], dict[str, list[float]]]:
    date_sets = [set(d for d, _r in series) for series in by_symbol.values() if series]
    if not date_sets:
        return [], {}
    common = sorted(set.intersection(*date_sets))
    out: dict[str, list[float]] = {}
    for sym, series in by_symbol.items():
        mp = dict(series)
        out[sym] = [mp[d] for d in common]
    return common, out


def pair_correlations(returns: dict[str, list[float]], *, sector_of: dict[str, str]) -> list[dict[str, Any]]:
    syms = sorted(returns)
    rows: list[dict[str, Any]] = []
    for i, a in enumerate(syms):
        for b in syms[i + 1 :]:
            r = pearson(returns[a], returns[b])
            same = sector_of.get(a) == sector_of.get(b) and bool(sector_of.get(a))
            rows.append(
                {
                    "a": a,
                    "b": b,
                    "corr": r,
                    "same_tse33": same,
                    "tse33": sector_of.get(a) if same else None,
                    "redundant_flag": bool(same and r is not None and r >= CORR_REDUNDANT),
                }
            )
    rows.sort(key=lambda rec: (-(rec["corr"] if rec["corr"] is not None else -9), rec["a"], rec["b"]))
    return rows


def sector_relative_returns(returns: dict[str, list[float]], *, sector_of: dict[str, str]) -> dict[str, list[float]]:
    n = len(next(iter(returns.values()))) if returns else 0
    groups: dict[str, list[str]] = {}
    for sym, sec in sector_of.items():
        if sym not in returns:
            continue
        groups.setdefault(sec or "UNKNOWN", []).append(sym)
    out: dict[str, list[float]] = {}
    for _sec, members in groups.items():
        if len(members) < 2:
            for sym in members:
                out[sym] = list(returns[sym])
            continue
        for t in range(n):
            pass
        for sym in members:
            rel = []
            for t in range(n):
                others = [returns[m][t] for m in members if m != sym]
                mu = mean(others)
                rel.append(returns[sym][t] - (mu if mu is not None else 0.0))
            out[sym] = rel
    return out


def topix_betas(
    returns: dict[str, list[float]],
    dates: list[str],
    topix_closes: dict[str, float],
) -> dict[str, Any]:
    if not topix_closes or not dates:
        return {"ok": False, "reason": "topix_unavailable", "rows": []}
    mkt: list[float] = []
    keep: list[int] = []
    for i, d in enumerate(dates):
        # need previous topix close; dates are return dates (already skip first bar)
        # find previous session in topix keys
        prev_candidates = sorted(k for k in topix_closes if k < d)
        if not prev_candidates:
            continue
        prev = prev_candidates[-1]
        pc = topix_closes.get(prev)
        cc = topix_closes.get(d)
        if pc in {None, 0.0} or cc is None:
            continue
        mkt.append((cc - pc) / pc)
        keep.append(i)
    if len(keep) < 8:
        return {"ok": False, "reason": "topix_returns_too_short", "rows": []}
    rows = []
    for sym, series in returns.items():
        y = [series[i] for i in keep]
        fit = ols_alpha_beta(y, mkt)
        rows.append({"symbol": sym, "topix_beta": fit.get("beta"), "topix_r": fit.get("r"), "n": fit.get("n")})
    rows.sort(key=lambda r: str(r["symbol"]))
    return {"ok": True, "n_mkt": len(mkt), "rows": rows}


def redundancy_clusters(pair_rows: list[dict[str, Any]], *, median_va: dict[str, float | None]) -> list[dict[str, Any]]:
    clusters: dict[str, set[str]] = {}
    for rec in pair_rows:
        if not rec.get("redundant_flag"):
            continue
        sec = str(rec.get("tse33") or "UNKNOWN")
        clusters.setdefault(sec, set()).update([rec["a"], rec["b"]])
    out = []
    for sec, names in sorted(clusters.items()):
        ranked = sorted(names, key=lambda s: (-(median_va.get(s) or 0.0), s))
        out.append(
            {
                "tse33": sec,
                "symbols": ranked,
                "n": len(ranked),
                "keep_liquidity_anchor": ranked[0] if ranked else None,
                "redundant_candidates_not_removed": ranked[1:],
                "note": "V1 membership unchanged. These names are redundancy candidates for a later V1.1 review only.",
            }
        )
    return out


assert CORR_REDUNDANT == 0.85
