"""Regime path outcomes, UPDATE sensitivity, verdict."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any, Optional

import numpy as np

from research.e1_x7_pfq.bridge_v2.stats import bootstrap_difference, rate_plus_first
from research.e1_x8_symbol_leverage.signal import evaluate_update_signal, summarize_ft

from . import BOOTSTRAP_REPS, BOOTSTRAP_SEED, FROZEN_UPDATE_THR, MIN_DAYS, MIN_EPISODES, MIN_SYMBOLS, TARGET_SYMBOL


FT_KEYS = (
    "plus5_vs_minus10",
    "plus5_vs_minus15",
    "plus10_vs_minus10",
    "plus10_vs_minus15",
)


def support_ok(n_symbols: int, n_episodes: int, n_days: int) -> bool:
    return n_symbols >= MIN_SYMBOLS and n_episodes >= MIN_EPISODES and n_days >= MIN_DAYS


def _rows_for_signal(eps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in eps:
        out.append({
            "episode_id": e["episode_id"],
            "day": e["day"],
            "symbol": e["symbol"],
            "session": e.get("session"),
            "evaluable": e.get("evaluable", True),
            "best_net_pnl_bps_300s": e.get("best_net_pnl_bps_300s"),
            "ft_plus5_vs_minus10": e.get("ft_plus5_vs_minus10"),
            "ft_plus5_vs_minus15": e.get("ft_plus5_vs_minus15"),
            "ft_plus10_vs_minus10": e.get("ft_plus10_vs_minus10"),
            "ft_plus10_vs_minus15": e.get("ft_plus10_vs_minus15"),
            "update_eligible_parent": e.get("update_eligible_parent", True),
            "price_update_count_10s": e.get("price_update_count_10s"),
            "mem_UPDATE": e.get("mem_UPDATE"),
        })
    return out


def regime_first_touch(eps: list[dict[str, Any]], *, regime_name: str, regime_value: str) -> dict[str, Any]:
    n_sym = len({e["symbol"] for e in eps})
    n_ep = len(eps)
    n_days = len({e["day"] for e in eps})
    if not support_ok(n_sym, n_ep, n_days):
        return {
            "regime_name": regime_name,
            "regime_value": regime_value,
            "status": "NOT_EVALUABLE_SUPPORT",
            "n_symbols": n_sym,
            "n_episodes": n_ep,
            "n_days": n_days,
        }
    rows = _rows_for_signal(eps)
    metrics = {}
    for k in FT_KEYS:
        # PLUS_FIRST rate with bootstrap vs nothing — store rate + CI of rate via day×symbol resample
        def rate_fn(rs, kk=k):
            return rate_plus_first(rs, kk, "fixed_grid")

        # wrap rows with fixed_grid_ft
        wrapped = []
        for r in rows:
            wrapped.append({
                **r,
                "fixed_grid_ft": {
                    "plus5_vs_minus10": r.get("ft_plus5_vs_minus10"),
                    "plus5_vs_minus15": r.get("ft_plus5_vs_minus15"),
                    "plus10_vs_minus10": r.get("ft_plus10_vs_minus10"),
                    "plus10_vs_minus15": r.get("ft_plus10_vs_minus15"),
                },
            })
        # bootstrap mean rate
        units = sorted({(r["day"], r["symbol"]) for r in wrapped})
        by_u = defaultdict(list)
        for r in wrapped:
            by_u[(r["day"], r["symbol"])].append(r)
        rng = np.random.default_rng(BOOTSTRAP_SEED)
        vals = []
        for _ in range(BOOTSTRAP_REPS):
            draw = [units[i] for i in rng.integers(0, len(units), size=len(units))]
            sample = [x for u in draw for x in by_u[u]]
            rv = rate_fn(sample)
            if rv is not None:
                vals.append(rv)
        point = rate_fn(wrapped)
        if vals:
            lo, hi = np.quantile(vals, [0.025, 0.975])
            ci = [float(lo), float(hi)]
        else:
            ci = [None, None]
        # day rates
        day_rates = {}
        for d in sorted({r["day"] for r in wrapped}):
            day_rates[d] = rate_fn([r for r in wrapped if r["day"] == d])
        metrics[k] = {
            "plus_first_rate": point,
            "ci95": ci,
            "day_rates": day_rates,
            "positive_days": sum(1 for v in day_rates.values() if v is not None and v > 0),
        }
    return {
        "regime_name": regime_name,
        "regime_value": regime_value,
        "status": "OK",
        "n_symbols": n_sym,
        "n_episodes": n_ep,
        "n_days": n_days,
        "metrics": metrics,
    }


def update_signal_by_regime(
    eps: list[dict[str, Any]],
    parent_pool: list[dict[str, Any]],
    *,
    regime_name: str,
    regime_value: str,
) -> dict[str, Any]:
    n_sym = len({e["symbol"] for e in eps})
    n_ep = len(eps)
    n_days = len({e["day"] for e in eps})
    cand = [e for e in eps if e.get("mem_UPDATE")]
    # parent = update eligible in same regime
    parent = [e for e in parent_pool if e.get("update_eligible_parent")]
    if not support_ok(n_sym, len(cand) if cand else n_ep, n_days) or len(cand) < 5:
        return {
            "regime_name": regime_name,
            "regime_value": regime_value,
            "status": "NOT_EVALUABLE_SUPPORT",
            "candidate_n": len(cand),
            "parent_n": len(parent),
            "n_symbols": n_sym,
            "n_episodes": n_ep,
            "n_days": n_days,
            "threshold": FROZEN_UPDATE_THR,
        }
    sig = evaluate_update_signal(
        _rows_for_signal(cand),
        _rows_for_signal(parent),
        ft_keys=("plus5_vs_minus10", "plus10_vs_minus10"),
    )
    return {
        "regime_name": regime_name,
        "regime_value": regime_value,
        "status": "OK",
        "candidate_n": len(cand),
        "parent_n": len(parent),
        "n_symbols": n_sym,
        "n_episodes": n_ep,
        "n_days": n_days,
        "threshold": FROZEN_UPDATE_THR,
        "supported": sig.get("supported"),
        "plus5_vs_minus10": summarize_ft(sig, "plus5_vs_minus10"),
        "plus10_vs_minus10": summarize_ft(sig, "plus10_vs_minus10"),
    }


def compare_regime_pair(low: dict[str, Any], high: dict[str, Any], key: str = "plus5_vs_minus10") -> dict[str, Any]:
    """Difference of PLUS_FIRST rates with informal CI using low/high bootstrap CIs if present."""
    if low.get("status") != "OK" or high.get("status") != "OK":
        return {"status": "NOT_EVALUABLE", "reason": "support"}
    lm = ((low.get("metrics") or {}).get(key) or {})
    hm = ((high.get("metrics") or {}).get(key) or {})
    if lm.get("plus_first_rate") is None or hm.get("plus_first_rate") is None:
        return {"status": "NOT_EVALUABLE"}
    diff = float(lm["plus_first_rate"]) - float(hm["plus_first_rate"])
    # conservative lower bound: low.ci_lo - high.ci_hi
    lci = lm.get("ci95") or [None, None]
    hci = hm.get("ci95") or [None, None]
    lo = None if lci[0] is None or hci[1] is None else float(lci[0]) - float(hci[1])
    hi = None if lci[1] is None or hci[0] is None else float(lci[1]) - float(hci[0])
    # day agreement: days where low rate > high rate
    days = sorted(set(lm.get("day_rates") or {}) | set(hm.get("day_rates") or {}))
    pos = 0
    scored = 0
    for d in days:
        a = (lm.get("day_rates") or {}).get(d)
        b = (hm.get("day_rates") or {}).get(d)
        if a is None or b is None:
            continue
        scored += 1
        if a > b:
            pos += 1
    return {
        "status": "OK",
        "key": key,
        "difference": diff,
        "ci95_approx": [lo, hi],
        "positive_days": pos,
        "n_days_scored": scored,
        "supported_hypothesis": (
            lo is not None and lo > 0 and scored >= 7 and pos >= 7
        ),
    }


def within_symbol_normalization(eps: list[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive only: raw >=8 vs within-symbol high-update events."""
    by_sym = defaultdict(list)
    for e in eps:
        if e.get("price_update_count_10s") is None:
            continue
        by_sym[str(e["symbol"])].append(e)
    rows = []
    raw_plus = []
    within_plus = []
    for sym, rs in by_sym.items():
        if len(rs) < 5:
            continue
        vals = [float(r["price_update_count_10s"]) for r in rs]
        med = float(median(vals))
        # percentile within symbol
        order = sorted(vals)
        for r in rs:
            v = float(r["price_update_count_10s"])
            pct = sum(1 for x in order if x <= v) / len(order)
            ft = r.get("ft_plus5_vs_minus10") == "PLUS_FIRST"
            raw_ge8 = v >= FROZEN_UPDATE_THR - 1e-12
            within_high = pct >= 0.70
            if raw_ge8 and ft is not None:
                raw_plus.append(1.0 if ft else 0.0)
            if within_high and r.get("ft_plus5_vs_minus10") not in (None, "NOT_EVALUABLE"):
                within_plus.append(1.0 if ft else 0.0)
            rows.append({
                "symbol": sym,
                "episode_id": r["episode_id"],
                "raw_update": v,
                "within_symbol_percentile": pct,
                "within_symbol_median_dev": v - med,
                "raw_ge_8": raw_ge8,
                "within_high_p70": within_high,
                "plus5_before_minus10": ft,
            })
    return {
        "status": "DESCRIPTIVE_REFERENCE_ONLY",
        "n_rows": len(rows),
        "symbols_with_support": len({r["symbol"] for r in rows}),
        "raw_ge8_plus5_before_minus10_rate": float(np.mean(raw_plus)) if raw_plus else None,
        "within_high_p70_plus5_before_minus10_rate": float(np.mean(within_plus)) if within_plus else None,
        "note": "no new threshold selected; feasibility only",
        "sample_rows": rows[:50],
    }


def high_update_regime_split(eps: list[dict[str, Any]]) -> dict[str, Any]:
    """Symbol median pu10 >= 8 vs < 8 — from E1_X8 UPDATE_HEAVY idea."""
    by_sym = defaultdict(list)
    for e in eps:
        if e.get("price_update_count_10s") is not None:
            by_sym[str(e["symbol"])].append(float(e["price_update_count_10s"]))
    heavy = {s for s, vs in by_sym.items() if len(vs) >= 5 and median(vs) >= 8.0}
    low = {s for s, vs in by_sym.items() if len(vs) >= 5 and median(vs) < 8.0}
    return {"UPDATE_HEAVY": heavy, "UPDATE_LIGHT": low}


def decide_verdict(
    *,
    coverage: dict[str, Any],
    direct_status: dict[str, Any],
    proxy_comparisons: list[dict[str, Any]],
    update_heavy_vs_light: Optional[dict[str, Any]],
    core_proxy_evaluable: bool,
) -> dict[str, Any]:
    if not core_proxy_evaluable and direct_status.get("status") == "DIRECT_INSTITUTIONAL_DATA_NOT_EVALUABLE":
        # still may have some proxy axes
        if coverage.get("any_proxy_axis_evaluable"):
            pass
        else:
            return {
                "verdict": "E1_X9_ASOF_METADATA_INSUFFICIENT",
                "next": "decide whether to build historical as-of metadata acquisition",
                "pfq_revive": False,
            }

    # Direct path
    if direct_status.get("evaluable"):
        # not implemented with data
        return {
            "verdict": "E1_X9_DIRECT_INSTITUTIONAL_DATA_EVALUABLE_NO_STABLE_RELATION",
            "next": "do not adopt low-institutional universe from current evidence",
            "pfq_revive": False,
        }

    # Update intensity explains better than participation proxies?
    uh = update_heavy_vs_light or {}
    uh_supported = bool(uh.get("supported_hypothesis"))
    proxy_supported = any(c.get("supported_hypothesis") for c in proxy_comparisons)

    if uh_supported:
        return {
            "verdict": "E1_X9_HIGH_UPDATE_REGIME_EXPLAINS_SIGNAL",
            "next": (
                "prefer update-intensity regime framing over unverified ownership narrative"
                if proxy_supported
                else "consider high-update vs low-update regime split in future research design; do not claim institutional ownership causation"
            ),
            "pfq_revive": False,
            "note": "update intensity separates more consistently than available participation proxies",
            "proxy_also_supported": proxy_supported,
        }
    if proxy_supported:
        return {
            "verdict": "E1_X9_PROXY_LOW_PARTICIPATION_REGIME_SUPPORTED",
            "next": "independent Universe family planning document only — no ENTRY/EXIT implementation",
            "pfq_revive": False,
            "note": "proxy regime only; do not assert low institutional ownership as proven cause",
        }
    if not coverage.get("any_proxy_axis_evaluable"):
        return {
            "verdict": "E1_X9_ASOF_METADATA_INSUFFICIENT",
            "next": "historical as-of metadata acquisition method needed",
            "pfq_revive": False,
        }
    return {
        "verdict": "E1_X9_NO_STABLE_UNIVERSE_REGIME_SEPARATION",
        "next": "do not adopt low-institutional / low-participation Universe hypothesis on current data",
        "pfq_revive": False,
    }
