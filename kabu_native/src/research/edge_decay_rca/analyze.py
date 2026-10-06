"""Economic bridge, tails, selection, features, fill, exit, TOD, cohort, classify."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_economic_sensitivity.decompose import daily_pnl, exit_family, portfolio_reentry
from research.anchor_timing_robustness.grid import tod_bucket
from research.anchor_timing_robustness.metrics import maxdd, mean_finite, spearman, trade_stats
from research.edge_decay_rca import (
    BEST_DAY_SHARE_HIGH,
    COMPONENT_MATERIAL,
    DEVELOPMENT_DAYS,
    EXIT_PNL_RATIO_DECAY,
    FILL_RATE_DROP,
    KS_HIGH,
    POST_DAYS,
    PSI_HIGH,
    RANK_GAP_RATIO_DECAY,
    SMD_HIGH,
    SPEARMAN_DROP,
    TAIL_SHARE_HIGH,
    TAIL_SHARE_MED,
    VERDICT_ID,
    VERDICT_INCONCLUSIVE,
    VERDICT_MULTI,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _pnl(t: dict[str, Any]) -> float:
    return float(t.get("pnl_yen_100") or 0.0)


def _period(t: dict[str, Any]) -> str:
    d = str(t.get("date") or "")
    if t.get("period") == "DEVELOPMENT" or d in DEVELOPMENT_DAYS:
        return "DEV"
    if t.get("period") == "POST_FREEZE_HOLDOUT" or d in POST_DAYS:
        return "POST"
    return "OTHER"


def split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return [r for r in rows if _period(r) == "DEV"], [r for r in rows if _period(r) == "POST"]


def economics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    st = trade_stats(trades)
    wins = [t for t in trades if _pnl(t) > 1e-9]
    losses = [t for t in trades if _pnl(t) < -1e-9]
    draws = [t for t in trades if abs(_pnl(t)) <= 1e-9]
    n = len(trades)
    days = sorted({str(t.get("date")) for t in trades})
    st.update(
        {
            "n": n,
            "days": len(days),
            "trades_per_day": (n / len(days)) if days else None,
            "win_rate": (len(wins) / n) if n else None,
            "loss_rate": (len(losses) / n) if n else None,
            "draw_rate": (len(draws) / n) if n else None,
            "avg_winner": mean_finite([_pnl(t) for t in wins]),
            "median_winner": float(np.median([_pnl(t) for t in wins])) if wins else None,
            "avg_loser": mean_finite([_pnl(t) for t in losses]),
            "median_loser": float(np.median([_pnl(t) for t in losses])) if losses else None,
            "n_win": len(wins),
            "n_loss": len(losses),
            "n_draw": len(draws),
            "holding_sec": mean_finite([_f(t.get("holding_sec")) for t in trades]),
            "MFE": mean_finite([_f(t.get("mfe_yen_100")) for t in trades]),
            "MAE": mean_finite([_f(t.get("mae_yen_100")) for t in trades]),
            "maxDD": maxdd(trades),
        }
    )
    return st


def exact_bridge(dev: list[dict[str, Any]], post: list[dict[str, Any]]) -> dict[str, Any]:
    def parts(xs: list[dict[str, Any]]) -> dict[str, float]:
        n = float(len(xs))
        w = [t for t in xs if _pnl(t) > 1e-9]
        l = [t for t in xs if _pnl(t) < -1e-9]
        pnl = sum(_pnl(t) for t in xs)
        g = mean_finite([_pnl(t) for t in w]) or 0.0
        lo = mean_finite([_pnl(t) for t in l]) or 0.0
        wr = (len(w) / n) if n else 0.0
        lr = (len(l) / n) if n else 0.0
        avg = (pnl / n) if n else 0.0
        return {"n": n, "pnl": pnl, "g": g, "lo": lo, "wr": wr, "lr": lr, "avg": avg}

    a, b = parts(dev), parts(post)
    after_count = b["n"] * a["avg"]
    count = after_count - a["pnl"]
    hyp_wr = b["n"] * (b["wr"] * a["g"] + b["lr"] * a["lo"])
    wr_eff = hyp_wr - after_count
    win_sz = b["n"] * b["wr"] * (b["g"] - a["g"])
    loss_sz = b["n"] * b["lr"] * (b["lo"] - a["lo"])
    recon = count + wr_eff + win_sz + loss_sz
    observed = b["pnl"] - a["pnl"]
    return {
        "TRADE_COUNT_COMPONENT": round(count, 2),
        "WIN_RATE_COMPONENT": round(wr_eff, 2),
        "WIN_SIZE_COMPONENT": round(win_sz, 2),
        "LOSS_SIZE_COMPONENT": round(loss_sz, 2),
        "residual": round(observed - recon, 2),
        "recon": round(recon, 2),
        "observed": round(observed, 2),
        "DEV_PNL": round(a["pnl"], 2),
        "POST_PNL": round(b["pnl"], 2),
    }


def tail_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(trades, key=_pnl, reverse=True)
    tot = sum(_pnl(t) for t in trades)

    def share(k: int) -> Optional[float]:
        if not ordered or abs(tot) < 1e-9:
            return None
        return sum(_pnl(t) for t in ordered[:k]) / tot

    daily = daily_pnl(trades)
    best = max(daily, key=lambda d: float(d["pnl"])) if daily else None
    top3d = sorted(daily, key=lambda d: float(d["pnl"]), reverse=True)[:3]
    rest_best = [t for t in trades if str(t.get("date")) != (best or {}).get("date")]
    drop1 = ordered[1:]
    drop3 = ordered[3:]

    def pf_pnl(xs):
        st = trade_stats(xs)
        return {"pnl": st.get("pnl"), "PF": st.get("PF"), "n": len(xs)}

    best_share = None
    if best and abs(tot) > 1e-9:
        best_share = abs(float(best["pnl"])) / abs(tot)
    top3_share = share(3)
    if top3_share is not None and top3_share >= TAIL_SHARE_HIGH:
        kind = "RIGHT_TAIL_DEPENDENT"
    elif best_share is not None and best_share >= BEST_DAY_SHARE_HIGH:
        kind = "FEW_DAY_DEPENDENT"
    elif top3_share is not None and top3_share >= TAIL_SHARE_MED:
        kind = "RIGHT_TAIL_DEPENDENT"
    else:
        kind = "BROAD_EDGE"
    return {
        "n": len(trades),
        "pnl": round(tot, 2),
        "top1_share": share(1),
        "top3_share": share(3),
        "top5_share": share(5),
        "top10_share": share(10),
        "best_day": (best or {}).get("date"),
        "best_day_pnl": (best or {}).get("pnl"),
        "best_day_share": best_share,
        "top3_day_pnl": round(sum(float(d["pnl"]) for d in top3d), 2),
        "remove_best_trade": pf_pnl(drop1),
        "remove_top3_trades": pf_pnl(drop3),
        "remove_best_day": pf_pnl(rest_best),
        "kind": kind,
        "top_trades": [
            {"date": t.get("date"), "symbol": t.get("symbol"), "anchor": t.get("anchor_time"), "pnl": _pnl(t)}
            for t in ordered[:10]
        ],
    }


def symbol_table(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list] = defaultdict(list)
    for t in trades:
        by[str(t.get("symbol"))].append(t)
    rows = []
    for sym, xs in by.items():
        st = trade_stats(xs)
        rows.append(
            {
                "symbol": sym,
                "n": len(xs),
                "pnl": st.get("pnl"),
                "PF": st.get("PF"),
                "win": st.get("win"),
                "loss": st.get("loss"),
                "MFE": st.get("MFE"),
                "MAE": st.get("MAE"),
            }
        )
    rows.sort(key=lambda r: float(r.get("pnl") or 0.0), reverse=True)
    return rows


def selection_by_rank(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[int, list] = defaultdict(list)
    for t in trades:
        rk = t.get("candidate_rank")
        if rk is None:
            continue
        by[int(rk)].append(t)
    out = {}
    avgs: list[Optional[float]] = []
    for rk in range(0, 5):
        xs = by.get(rk) or []
        st = trade_stats(xs)
        out[f"rank{rk + 1}"] = {
            "n": len(xs),
            "pnl": st.get("pnl"),
            "avg": st.get("avg_trade"),
            "PF": st.get("PF"),
            "win_rate": st.get("win_rate"),
        }
        avgs.append(float(st.get("avg_trade") or 0.0) if xs else None)
    gap = None
    if avgs[0] is not None and avgs[4] is not None:
        gap = avgs[0] - avgs[4]
    scores = [_f(t.get("score")) for t in trades]
    sc = [x for x in scores if x is not None]
    return {
        "by_rank": out,
        "rank1_minus_rank5_avg": gap,
        "selected_score_mean": mean_finite(sc),
        "selected_score_median": float(np.median(sc)) if sc else None,
    }


def cutoff_margins(rank_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    for r in rank_rows:
        sc = _f(r.get("score"))
        rk = r.get("rank")
        if sc is None or rk is None:
            continue
        by[(str(r.get("date")), str(r.get("anchor")))][int(rk)] = sc
    m12, m45, cut = [], [], []
    for scores in by.values():
        if 0 in scores and 1 in scores:
            m12.append(scores[0] - scores[1])
        if 3 in scores and 4 in scores:
            m45.append(scores[3] - scores[4])
        if 4 in scores and 5 in scores:
            cut.append(scores[4] - scores[5])
    return {
        "n_anchors": len(by),
        "top1_top2_margin": mean_finite(m12),
        "rank4_rank5_margin": mean_finite(m45),
        "TOP5_CUTOFF_MARGIN": mean_finite(cut),
    }


def _quantiles(xs: list[float]) -> dict[str, Any]:
    a = np.asarray(xs, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0}
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "std": float(np.std(a, ddof=1)) if a.size > 1 else 0.0,
        "q10": float(np.percentile(a, 10)),
        "q25": float(np.percentile(a, 25)),
        "q50": float(np.percentile(a, 50)),
        "q75": float(np.percentile(a, 75)),
        "q90": float(np.percentile(a, 90)),
    }


def psi_ks_smd(a: list[float], b: list[float]) -> dict[str, Any]:
    aa = np.asarray([x for x in a if x is not None and x == x], dtype=float)
    bb = np.asarray([x for x in b if x is not None and x == x], dtype=float)
    if aa.size < 5 or bb.size < 5:
        return {"n_dev": int(aa.size), "n_post": int(bb.size), "psi": None, "ks": None, "smd": None}
    edges = np.unique(np.quantile(aa, np.linspace(0, 1, 11)))
    if edges.size < 3:
        edges = np.linspace(float(np.min(aa)), float(np.max(aa)) + 1e-12, 5)
    p, _ = np.histogram(aa, bins=edges)
    q, _ = np.histogram(bb, bins=edges)
    p = p.astype(float) + 1e-6
    q = q.astype(float) + 1e-6
    p /= p.sum()
    q /= q.sum()
    psi = float(np.sum((p - q) * np.log(p / q)))
    sa, sb = np.sort(aa), np.sort(bb)
    allv = np.sort(np.concatenate([sa, sb]))
    cdf_a = np.searchsorted(sa, allv, side="right") / sa.size
    cdf_b = np.searchsorted(sb, allv, side="right") / sb.size
    ks = float(np.max(np.abs(cdf_a - cdf_b)))
    sd = float(np.sqrt(0.5 * (np.var(aa, ddof=1) + np.var(bb, ddof=1))))
    smd = float((np.mean(bb) - np.mean(aa)) / sd) if sd > 1e-12 else 0.0
    return {"n_dev": int(aa.size), "n_post": int(bb.size), "psi": psi, "ks": ks, "smd": smd}


def feature_shift(feat_rows: list[dict[str, Any]], *, selected_only: bool = False) -> dict[str, Any]:
    rows = [r for r in feat_rows if (not selected_only or r.get("selected"))]
    pop = "selected" if selected_only else "all_evaluable"
    if not rows:
        return {
            "FEATURE_DISTRIBUTION_SHIFT": "LOW",
            "features": {},
            "shifted_features": [],
            "population": pop,
            "note": "unavailable",
        }
    dev, post = split(rows)
    out = {}
    flags = []
    names = ("alloc_score", "score") + tuple(FEATURE_ORDER)
    for f in names:
        da = [x for x in (_f(r.get(f)) for r in dev) if x is not None]
        pb = [x for x in (_f(r.get(f)) for r in post) if x is not None]
        cmp_ = psi_ks_smd(da, pb)
        out[f] = {"dev": _quantiles(da), "post": _quantiles(pb), **cmp_}
        psi, ks, smd = cmp_.get("psi"), cmp_.get("ks"), cmp_.get("smd")
        if (psi is not None and psi >= PSI_HIGH) or (ks is not None and ks >= KS_HIGH) or (
            smd is not None and abs(smd) >= SMD_HIGH
        ):
            flags.append(f)
    level = "HIGH" if len(flags) >= 3 else ("MEDIUM" if flags else "LOW")
    return {
        "features": out,
        "shifted_features": flags,
        "FEATURE_DISTRIBUTION_SHIFT": level,
        "population": pop,
        "n_dev": len(dev),
        "n_post": len(post),
    }


def feature_outcome(feat_rows: list[dict[str, Any]], trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not feat_rows:
        return {"FEATURE_OUTCOME_RELATIONSHIP_SHIFT": "LOW", "by_feature": {}, "decayed_features": []}
    by = {(str(t.get("date")), str(t.get("anchor_time")), str(t.get("symbol"))): t for t in trades}
    joined = []
    for r in feat_rows:
        if not r.get("selected"):
            continue
        t = by.get((str(r.get("date")), str(r.get("anchor")), str(r.get("symbol"))))
        rec = dict(r)
        rec["pnl"] = _pnl(t) if t else None
        rec["filled"] = t is not None
        rec["mfe"] = _f((t or {}).get("mfe_yen_100"))
        rec["mae"] = _f((t or {}).get("mae_yen_100"))
        joined.append(rec)
    dev, post = split(joined)
    out = {}
    rel_flags = []

    def bucket(xs: list[dict[str, Any]], key: str) -> dict[str, Any]:
        filled = [r for r in xs if r.get("pnl") is not None and _f(r.get(key)) is not None]
        if len(filled) < 10:
            return {"n": len(filled), "spearman": None, "quintiles": []}
        vals = np.asarray([float(_f(r.get(key))) for r in filled], dtype=float)
        qs = np.quantile(vals, [0.2, 0.4, 0.6, 0.8])
        qrows = []
        for i in range(5):
            if i == 0:
                sub = [r for r in filled if float(_f(r.get(key))) <= qs[0]]
            elif i == 4:
                sub = [r for r in filled if float(_f(r.get(key))) > qs[3]]
            else:
                sub = [r for r in filled if qs[i - 1] < float(_f(r.get(key))) <= qs[i]]
            pnls = [float(r["pnl"]) for r in sub]
            wr = (sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else None
            qrows.append(
                {
                    "q": i + 1,
                    "n": len(sub),
                    "pnl": round(sum(pnls), 2),
                    "avg": mean_finite(pnls),
                    "win_rate": wr,
                    "MFE": mean_finite([r.get("mfe") for r in sub]),
                    "MAE": mean_finite([r.get("mae") for r in sub]),
                }
            )
        sp = spearman([float(_f(r.get(key))) for r in filled], [float(r["pnl"]) for r in filled])
        return {"n": len(filled), "spearman": sp, "quintiles": qrows}

    for f in ("alloc_score",) + FEATURE_ORDER:
        bd, bp = bucket(dev, f), bucket(post, f)
        drop = None
        if bd.get("spearman") is not None and bp.get("spearman") is not None:
            drop = float(bd["spearman"]) - float(bp["spearman"])
            if drop >= SPEARMAN_DROP:
                rel_flags.append(f)
        out[f] = {"DEV": bd, "POST": bp, "spearman_drop": drop}
    level = "HIGH" if len(rel_flags) >= 2 else ("MEDIUM" if rel_flags else "LOW")
    return {"by_feature": out, "decayed_features": rel_flags, "FEATURE_OUTCOME_RELATIONSHIP_SHIFT": level}


def fill_block(iso: list[dict[str, Any]], admits: list[dict[str, Any]], trades: list[dict[str, Any]]) -> dict[str, Any]:
    def one(xs_iso, xs_ad, xs_tr):
        filled = [r for r in xs_iso if r.get("independent_filled") in (True, "True", "true", 1)]
        lat, slip = [], []
        for r in filled:
            t0, ft = _f(r.get("t0")), _f(r.get("fill_time"))
            if t0 is not None and ft is not None:
                lat.append(ft - t0)
            lim, px = _f(r.get("limit")), _f(r.get("fill_price"))
            if lim and px and lim > 0:
                slip.append((px - lim) / lim * 10000.0)
        n_ad, n_tr = len(xs_ad), len(xs_tr)
        return {
            "isolated_selected": len(xs_iso),
            "isolated_fills": len(filled),
            "isolated_fill_rate": (len(filled) / len(xs_iso)) if xs_iso else None,
            "admit_n": n_ad,
            "portfolio_fills": n_tr,
            "portfolio_fill_rate": (n_tr / n_ad) if n_ad else None,
            "spread_bps": mean_finite([_f(r.get("spread_bps")) for r in xs_iso]),
            "imbalance": mean_finite([_f(r.get("imbalance")) for r in xs_iso]),
            "bid": mean_finite([_f(r.get("bid_at_anchor")) for r in xs_iso]),
            "ask": mean_finite([_f(r.get("ask_at_anchor")) for r in xs_iso]),
            "fill_latency_sec": mean_finite(lat),
            "fill_slippage_bps": mean_finite(slip),
        }

    d_iso, p_iso = split(iso)
    d_ad, p_ad = split(admits)
    d_tr, p_tr = split(trades)
    dev, post = one(d_iso, d_ad, d_tr), one(p_iso, p_ad, p_tr)
    drop = None
    if (
        dev.get("isolated_fill_rate") is not None
        and post.get("isolated_fill_rate") is not None
        and float(dev["isolated_fill_rate"]) > 1e-9
    ):
        drop = (float(dev["isolated_fill_rate"]) - float(post["isolated_fill_rate"])) / float(dev["isolated_fill_rate"])
    level = "LOW"
    if drop is not None and drop >= 0.10:
        level = "MEDIUM"
    if drop is not None and drop >= FILL_RATE_DROP:
        level = "HIGH"
    spr = psi_ks_smd(
        [x for x in (_f(r.get("spread_bps")) for r in d_iso) if x is not None],
        [x for x in (_f(r.get("spread_bps")) for r in p_iso) if x is not None],
    )
    if spr.get("psi") is not None and spr["psi"] >= PSI_HIGH:
        level = "HIGH"
    return {"DEV": dev, "POST": post, "fill_rate_relative_drop": drop, "spread_shift": spr, "FILL_EXECUTION_SHIFT": level}


def tag_entry_kind(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by[(str(t.get("date")), str(t.get("symbol")))].append(t)
    out: list[dict[str, Any]] = []
    for grp in by.values():
        grp = sorted(grp, key=lambda x: float(_f(x.get("fill_time")) or 0.0))
        for i, t in enumerate(grp):
            rec = dict(t)
            rec["entry_kind"] = "FIRST_ENTRY" if i == 0 else "REENTRY"
            out.append(rec)
    return out


def market_regime(
    feat_rows: list[dict[str, Any]],
    iso: list[dict[str, Any]],
    universes: dict[str, list[str]],
    daily_pack: list[dict[str, Any]],
) -> dict[str, Any]:
    """Stored strategy/universe state only. No external series."""
    d_feat, p_feat = split(feat_rows)
    d_iso, p_iso = split(iso)

    def feat_mean(xs: list[dict[str, Any]], key: str) -> Optional[float]:
        return mean_finite([_f(r.get(key)) for r in xs])

    def uni_days(days: tuple[str, ...]) -> dict[str, Any]:
        ns = [len(universes.get(d) or []) for d in days]
        jacc = []
        ordered = [d for d in days if d in universes]
        for a, b in zip(ordered, ordered[1:]):
            A, B = set(universes[a]), set(universes[b])
            u = A | B
            jacc.append((len(A & B) / len(u)) if u else None)
        return {
            "mean_universe_n": mean_finite(ns),
            "median_universe_n": float(np.median(ns)) if ns else None,
            "consecutive_jaccard": mean_finite(jacc),
            "unique_symbols": len({s for d in days for s in (universes.get(d) or [])}),
        }

    body = {
        "price_level_bid_DEV": mean_finite([_f(r.get("bid_at_anchor")) for r in d_iso]),
        "price_level_bid_POST": mean_finite([_f(r.get("bid_at_anchor")) for r in p_iso]),
        "spread_bps_DEV": feat_mean(d_feat, "spread_bps") or mean_finite([_f(r.get("spread_bps")) for r in d_iso]),
        "spread_bps_POST": feat_mean(p_feat, "spread_bps") or mean_finite([_f(r.get("spread_bps")) for r in p_iso]),
        "log_bid_qty_DEV": feat_mean(d_feat, "log_bid_qty"),
        "log_bid_qty_POST": feat_mean(p_feat, "log_bid_qty"),
        "event_rate_60s_DEV": feat_mean(d_feat, "event_rate_60s"),
        "event_rate_60s_POST": feat_mean(p_feat, "event_rate_60s"),
        "imbalance_DEV": feat_mean(d_feat, "imbalance"),
        "imbalance_POST": feat_mean(p_feat, "imbalance"),
        "mid_ret_60s_DEV": feat_mean(d_feat, "mid_ret_60s"),
        "mid_ret_60s_POST": feat_mean(p_feat, "mid_ret_60s"),
        "universe_DEV": uni_days(DEVELOPMENT_DAYS),
        "universe_POST": uni_days(POST_DAYS),
        "feature_evaluable_n_DEV": len(d_feat),
        "feature_evaluable_n_POST": len(p_feat),
        "daily_pack_n": len(daily_pack),
    }
    return body


def attach_activity(eco: dict[str, Any], trades: list[dict[str, Any]], admits: list[dict[str, Any]], daily_pack: list[dict[str, Any]], period: str) -> None:
    days = DEVELOPMENT_DAYS if period == "DEV" else POST_DAYS
    pack = [r for r in daily_pack if str(r.get("date")) in days]
    fires = sum(float(r.get("ref_fires") or 0.0) for r in pack)
    fills = float(len(trades))
    ad = [a for a in admits if _period(a) == period]
    eco["fills"] = int(fills)
    eco["admits"] = len(ad)
    eco["anchor_fires"] = fires if fires else None
    eco["fills_per_anchor"] = (fills / fires) if fires else None
    eco["fill_rate"] = (fills / len(ad)) if ad else None
    eco["trades_per_day"] = (fills / len(days)) if days else None


def exit_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    def one(xs):
        by: dict[str, list] = defaultdict(list)
        for t in xs:
            by[exit_family(t.get("exit_reason"))].append(t)
        out = {}
        for k, vs in sorted(by.items()):
            st = trade_stats(vs)
            out[k] = {
                "n": len(vs),
                "pnl": st.get("pnl"),
                "PF": st.get("PF"),
                "avg": st.get("avg_trade"),
                "MFE": st.get("MFE"),
                "MAE": st.get("MAE"),
            }
        return out

    d, p = split(trades)
    dev, post = one(d), one(p)

    def cont(block):
        n = pnl = 0.0
        for k in ("EXIT600", "EXTEND750"):
            b = block.get(k) or {}
            n += float(b.get("n") or 0)
            pnl += float(b.get("pnl") or 0.0)
        return (pnl / n) if n else None

    cd, cp = cont(dev), cont(post)
    level = "LOW"
    if cd and cd > 0 and (cp is None or cp <= cd * EXIT_PNL_RATIO_DECAY):
        level = "HIGH" if (cp is None or cp <= 0) else "MEDIUM"
    tagged_d = tag_entry_kind(d)
    tagged_p = tag_entry_kind(p)
    first_vs_re = {
        "DEV": {
            "FIRST_ENTRY": one([t for t in tagged_d if t.get("entry_kind") == "FIRST_ENTRY"]),
            "REENTRY": one([t for t in tagged_d if t.get("entry_kind") == "REENTRY"]),
        },
        "POST": {
            "FIRST_ENTRY": one([t for t in tagged_p if t.get("entry_kind") == "FIRST_ENTRY"]),
            "REENTRY": one([t for t in tagged_p if t.get("entry_kind") == "REENTRY"]),
        },
    }
    n_cont_p = 0.0
    for k in ("EXIT600", "EXTEND750"):
        n_cont_p += float((post.get(k) or {}).get("n") or 0)
    cont_delta = None
    if cd is not None:
        cont_delta = n_cont_p * ((cp or 0.0) - cd)
    return {
        "DEV": dev,
        "POST": post,
        "cont_avg_DEV": cd,
        "cont_avg_POST": cp,
        "cont_n_POST": n_cont_p,
        "EXIT_COMPONENT": round(cont_delta, 2) if cont_delta is not None else None,
        "first_vs_reentry_exit": first_vs_re,
        "EXIT_EDGE_SHIFT": level,
    }


def tod_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    def one(xs):
        by: dict[str, list] = defaultdict(list)
        for t in xs:
            by[tod_bucket(str(t.get("anchor_time") or ""))].append(t)
        out = {}
        for k, vs in by.items():
            st = trade_stats(vs)
            out[k] = {"n": len(vs), "pnl": st.get("pnl"), "PF": st.get("PF")}
        am = [t for t in xs if str(t.get("session")) == "AM"]
        pm = [t for t in xs if str(t.get("session")) == "PM"]
        out["_AM"] = {"n": len(am), "pnl": trade_stats(am).get("pnl")}
        out["_PM"] = {"n": len(pm), "pnl": trade_stats(pm).get("pnl")}
        return out

    d, p = split(trades)
    dev, post = one(d), one(p)
    am_decay = float((post.get("_AM") or {}).get("pnl") or 0.0) - float((dev.get("_AM") or {}).get("pnl") or 0.0)
    pm_decay = float((post.get("_PM") or {}).get("pnl") or 0.0) - float((dev.get("_PM") or {}).get("pnl") or 0.0)
    return {"DEV": dev, "POST": post, "AM_EDGE_DECAY": round(am_decay, 2), "PM_EDGE_DECAY": round(pm_decay, 2)}


def reentry_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    d, p = split(trades)
    out = {}
    for lab, xs in (("DEV", d), ("POST", p)):
        rows = []
        for t in xs:
            rec = dict(t)
            rec["shift_key"] = lab
            rec["pnl_yen_100"] = _pnl(t)
            rows.append(rec)
        st = portfolio_reentry(rows, lab)
        st.pop("lineage", None)
        out[lab] = st
    return out


def cohort_block(trades: list[dict[str, Any]], universes: dict[str, list[str]]) -> dict[str, Any]:
    d, p = split(trades)
    sd = {str(t.get("symbol")) for t in d}
    sp = {str(t.get("symbol")) for t in p}
    common = sd & sp
    only_d, only_p = sd - sp, sp - sd

    def pnl_set(xs, syms):
        return trade_stats([t for t in xs if str(t.get("symbol")) in set(syms)])

    u_dev, u_post = set(), set()
    for day, syms in universes.items():
        if day in DEVELOPMENT_DAYS:
            u_dev.update(syms)
        if day in POST_DAYS:
            u_post.update(syms)
    union = u_dev | u_post
    u_jacc = (len(u_dev & u_post) / len(union)) if union else None
    common_dev = pnl_set(d, common)
    common_post = pnl_set(p, common)
    return {
        "COMMON_SYMBOL_N": len(common),
        "DEV_ONLY_SYMBOL_N": len(only_d),
        "POST_ONLY_SYMBOL_N": len(only_p),
        "COMMON_SYMBOL_PNL_DEV": common_dev.get("pnl"),
        "COMMON_SYMBOL_PNL_POST": common_post.get("pnl"),
        "COMMON_SYMBOL_EDGE": round(float(common_post.get("pnl") or 0.0) - float(common_dev.get("pnl") or 0.0), 2),
        "DEV_ONLY_PNL": pnl_set(d, only_d).get("pnl"),
        "POST_ONLY_PNL": pnl_set(p, only_p).get("pnl"),
        "universe_jaccard": u_jacc,
        "universe_dev_n": len(u_dev),
        "universe_post_n": len(u_post),
        "universe_common_n": len(u_dev & u_post),
        "UNIVERSE_COMPOSITION_EFFECT": round(
            float(pnl_set(d, only_d).get("pnl") or 0.0) - float(pnl_set(p, only_p).get("pnl") or 0.0), 2
        ),
    }


def overlay_components(
    *,
    bridge: dict[str, Any],
    reent: dict[str, Any],
    exits: dict[str, Any],
    sel_dev: dict[str, Any],
    sel_post: dict[str, Any],
    tail_dev: dict[str, Any],
    tail_post: dict[str, Any],
    fill: dict[str, Any],
    eco_dev: dict[str, Any],
    eco_post: dict[str, Any],
) -> dict[str, Any]:
    first_d = float((reent.get("DEV") or {}).get("FIRST_ENTRY_PNL") or 0.0)
    first_p = float((reent.get("POST") or {}).get("FIRST_ENTRY_PNL") or 0.0)
    re_d = float((reent.get("DEV") or {}).get("REENTRY_PNL") or 0.0)
    re_p = float((reent.get("POST") or {}).get("REENTRY_PNL") or 0.0)

    def topk_pnl(block: dict[str, Any], k: int) -> float:
        return float(sum(float(t.get("pnl") or 0.0) for t in (block.get("top_trades") or [])[:k]))

    g_d = sel_dev.get("rank1_minus_rank5_avg")
    g_p = sel_post.get("rank1_minus_rank5_avg")
    n_p = float(eco_post.get("n") or 0.0)
    sel_eff = (float(g_p) - float(g_d)) * n_p if (g_d is not None and g_p is not None) else None
    r1d = ((sel_dev.get("by_rank") or {}).get("rank1") or {}).get("avg")
    r1p = ((sel_post.get("by_rank") or {}).get("rank1") or {}).get("avg")
    n1p = float(((sel_post.get("by_rank") or {}).get("rank1") or {}).get("n") or 0.0)
    rank1_eff = (float(r1p) - float(r1d)) * n1p if (r1d is not None and r1p is not None) else None
    return {
        "FILL_COMPONENT": fill.get("FILL_COMPONENT"),
        "SELECTION_COMPONENT": round(sel_eff, 2) if sel_eff is not None else None,
        "SELECTION_RANK1_COMPONENT": round(rank1_eff, 2) if rank1_eff is not None else None,
        "EXIT_COMPONENT": exits.get("EXIT_COMPONENT"),
        "REENTRY_EFFECT": round(re_p - re_d, 2),
        "FIRST_ENTRY_DECAY": round(first_p - first_d, 2),
        "TAIL_COMPONENT": round(topk_pnl(tail_post, 3) - topk_pnl(tail_dev, 3), 2),
        "note": (
            "TRADE_COUNT/WIN_RATE/WIN_SIZE/LOSS_SIZE sum to observed (draws=0). "
            "FILL/SELECTION/EXIT/REENTRY/TAIL are overlays and are not required to sum to decay."
        ),
    }


def classify(ctx: dict[str, Any]) -> dict[str, Any]:
    decay = abs(float(ctx["bridge"].get("observed") or 0.0)) or 1.0
    drivers: list[str] = []
    mags: dict[str, float] = {}
    tail_d = ctx["tail_dev"]["kind"]
    top3_d = ctx["tail_dev"].get("top3_share") or 0.0
    top3_p = ctx["tail_post"].get("top3_share") or 0.0
    tail_yen = abs(float(ctx["overlay"].get("TAIL_COMPONENT") or 0.0))
    # POST top3_share is not comparable when POST PnL is near 0 (share can exceed 1).
    # Use DEV concentration + yen change in top-3 contribution.
    if tail_d in {"RIGHT_TAIL_DEPENDENT", "FEW_DAY_DEPENDENT"} and tail_yen >= COMPONENT_MATERIAL * decay:
        drivers.append("RIGHT_TAIL_DISAPPEARED")
        mags["RIGHT_TAIL_DISAPPEARED"] = tail_yen
    g_d = ctx["sel_dev"].get("rank1_minus_rank5_avg")
    g_p = ctx["sel_post"].get("rank1_minus_rank5_avg")
    if g_d is not None and g_d > 0 and (g_p is None or g_p < RANK_GAP_RATIO_DECAY * g_d):
        drivers.append("SELECTION_EDGE_DECAY")
        mags["SELECTION_EDGE_DECAY"] = abs(
            float(ctx["overlay"].get("SELECTION_RANK1_COMPONENT") or ctx["overlay"].get("SELECTION_COMPONENT") or 0.0)
        )
    if ctx["feat_rel"].get("FEATURE_OUTCOME_RELATIONSHIP_SHIFT") in {"MEDIUM", "HIGH"}:
        drivers.append("FEATURE_RELATIONSHIP_DECAY")
        mags["FEATURE_RELATIONSHIP_DECAY"] = 0.20 * decay
    if ctx["feat_dist"].get("FEATURE_DISTRIBUTION_SHIFT") in {"MEDIUM", "HIGH"}:
        drivers.append("MARKET_REGIME_SHIFT")
        mags["MARKET_REGIME_SHIFT"] = 0.25 * decay
    common_edge = ctx["cohort"].get("COMMON_SYMBOL_EDGE") or 0.0
    uni_eff = ctx["cohort"].get("UNIVERSE_COMPOSITION_EFFECT") or 0.0
    if abs(common_edge) >= COMPONENT_MATERIAL * decay:
        drivers.append("SAME_SYMBOL_EDGE_DECAY")
        mags["SAME_SYMBOL_EDGE_DECAY"] = abs(float(common_edge))
    uj = ctx["cohort"].get("universe_jaccard")
    only_d = abs(float(ctx["cohort"].get("DEV_ONLY_PNL") or 0.0))
    if abs(uni_eff) >= COMPONENT_MATERIAL * decay or (
        only_d >= COMPONENT_MATERIAL * abs(float(ctx["bridge"].get("DEV_PNL") or 0.0) or 1.0)
        and (uj is None or uj < 0.85)
    ):
        drivers.append("UNIVERSE_COMPOSITION_SHIFT")
        mags["UNIVERSE_COMPOSITION_SHIFT"] = max(abs(float(uni_eff)), only_d)
    if ctx["fill"].get("FILL_EXECUTION_SHIFT") in {"MEDIUM", "HIGH"}:
        drivers.append("FILL_EXECUTION_DECAY")
        mags["FILL_EXECUTION_DECAY"] = abs(float(ctx["fill"].get("FILL_COMPONENT") or 0.0))
    if ctx["exit"].get("EXIT_EDGE_SHIFT") in {"MEDIUM", "HIGH"}:
        drivers.append("EXIT_EDGE_DECAY")
        mags["EXIT_EDGE_DECAY"] = abs(float(ctx["exit"].get("EXIT_COMPONENT") or 0.0))
    re_d = float((ctx["reent"].get("DEV") or {}).get("REENTRY_PNL") or 0.0)
    re_p = float((ctx["reent"].get("POST") or {}).get("REENTRY_PNL") or 0.0)
    first_d = float((ctx["reent"].get("DEV") or {}).get("FIRST_ENTRY_PNL") or 0.0)
    first_p = float((ctx["reent"].get("POST") or {}).get("FIRST_ENTRY_PNL") or 0.0)
    if abs(re_p - re_d) >= COMPONENT_MATERIAL * decay and abs(first_p - first_d) < abs(re_p - re_d):
        drivers.append("REENTRY_EDGE_DECAY")
        mags["REENTRY_EDGE_DECAY"] = abs(re_p - re_d)
    am_n_d = float(((ctx["tod"].get("DEV") or {}).get("_AM") or {}).get("n") or 0.0)
    am_n_p = float(((ctx["tod"].get("POST") or {}).get("_AM") or {}).get("n") or 0.0)
    n_d = float(ctx["eco_dev"].get("n") or 0.0)
    n_p = float(ctx["eco_post"].get("n") or 0.0)
    share_d = (am_n_d / n_d) if n_d else None
    share_p = (am_n_p / n_p) if n_p else None
    if share_d is not None and share_p is not None and abs(share_p - share_d) >= 0.15:
        drivers.append("TIME_OF_DAY_MIX_SHIFT")
        mags["TIME_OF_DAY_MIX_SHIFT"] = abs(share_p - share_d) * decay
    seen: list[str] = []
    for d in drivers:
        if d not in seen:
            seen.append(d)
    ranked = sorted(seen, key=lambda k: mags.get(k, 0.0), reverse=True)
    material = [k for k in ranked if mags.get(k, 0.0) >= COMPONENT_MATERIAL * decay]
    if not ranked:
        primary, secondary, family = "NO_CLEAR_PRIMARY_DRIVER", None, VERDICT_INCONCLUSIVE
    elif len(material) >= 3:
        primary, secondary, family = "MULTIPLE_EDGE_DECAY_DRIVERS", ranked[0], VERDICT_MULTI
    elif len(ranked) == 1:
        primary, secondary, family = ranked[0], None, VERDICT_ID
    else:
        primary, secondary, family = ranked[0], ranked[1], VERDICT_MULTI
        if len(material) <= 1 and mags.get(ranked[0], 0.0) >= 0.5 * decay:
            family = VERDICT_ID
            secondary = ranked[1]
    conf = "MEDIUM"
    residual_ok = abs(float(ctx["bridge"].get("residual") or 0.0)) < 1.0
    if family == VERDICT_ID and residual_ok:
        conf = "HIGH"
    if family == VERDICT_INCONCLUSIVE:
        conf = "LOW"
    if family == VERDICT_MULTI and len(material) >= 3:
        conf = "MEDIUM"
    return {
        "PRIMARY_ROOT_CAUSE": primary,
        "SECONDARY_ROOT_CAUSE": secondary,
        "drivers": ranked,
        "driver_magnitudes": {k: round(mags.get(k, 0.0), 2) for k in ranked},
        "verdict": family,
        "EDGE_DECAY_CONFIDENCE": conf,
        "am_share_DEV": share_d,
        "am_share_POST": share_p,
    }


def run_all(
    *,
    ref: list[dict[str, Any]],
    uni: list[dict[str, Any]],
    admits: list[dict[str, Any]],
    iso: list[dict[str, Any]],
    rank_rows: list[dict[str, Any]],
    feat_rows: list[dict[str, Any]],
    universes: dict[str, list[str]],
    daily_pack: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    daily_pack = daily_pack or []
    d, p = split(ref)
    ud, up = split(uni)
    eco_d, eco_p = economics(d), economics(p)
    attach_activity(eco_d, d, admits, daily_pack, "DEV")
    attach_activity(eco_p, p, admits, daily_pack, "POST")
    bridge = exact_bridge(d, p)
    tail_d, tail_p = tail_block(d), tail_block(p)
    sym_d, sym_p = symbol_table(d), symbol_table(p)
    tot_d = float(eco_d.get("pnl") or 0.0)
    tot_p = float(eco_p.get("pnl") or 0.0)
    top_share_d = (float(sym_d[0]["pnl"]) / tot_d) if sym_d and abs(tot_d) > 1e-9 else None
    top_share_p = (float(sym_p[0]["pnl"]) / tot_p) if sym_p and abs(tot_p) > 1e-9 else None
    sel_d = selection_by_rank(d)
    sel_p = selection_by_rank(p)
    sel_d["cutoff"] = cutoff_margins([r for r in rank_rows if _period(r) == "DEV"])
    sel_p["cutoff"] = cutoff_margins([r for r in rank_rows if _period(r) == "POST"])
    feat_dist = feature_shift(feat_rows, selected_only=False)
    feat_dist_sel = feature_shift(feat_rows, selected_only=True)
    feat_rel = feature_outcome(feat_rows, ref)
    fill = fill_block(iso, admits, ref)
    fr_d = fill["DEV"].get("portfolio_fill_rate")
    fr_p = fill["POST"].get("portfolio_fill_rate")
    if fr_d is not None and fr_p is not None:
        n_ad_p = float(fill["POST"].get("admit_n") or 0.0)
        avg_d = float(eco_d.get("avg_trade") or 0.0)
        fill["FILL_COMPONENT"] = round((float(fr_p) - float(fr_d)) * n_ad_p * avg_d, 2)
    else:
        fill["FILL_COMPONENT"] = None
    exits = exit_block(ref)
    tod = tod_block(ref)
    reent = reentry_block(ref)
    cohort = cohort_block(ref, universes)
    market = market_regime(feat_rows, iso, universes, daily_pack)
    ov = overlay_components(
        bridge=bridge,
        reent=reent,
        exits=exits,
        sel_dev=sel_d,
        sel_post=sel_p,
        tail_dev=tail_d,
        tail_post=tail_p,
        fill=fill,
        eco_dev=eco_d,
        eco_post=eco_p,
    )
    clf = classify(
        {
            "bridge": bridge,
            "overlay": ov,
            "tail_dev": tail_d,
            "tail_post": tail_p,
            "sel_dev": sel_d,
            "sel_post": sel_p,
            "feat_rel": feat_rel,
            "feat_dist": feat_dist,
            "cohort": cohort,
            "fill": fill,
            "exit": exits,
            "reent": reent,
            "tod": tod,
            "eco_dev": eco_d,
            "eco_post": eco_p,
        }
    )
    u_bridge = exact_bridge(ud, up)
    u_tail_d, u_tail_p = tail_block(ud), tail_block(up)
    u_sel_d, u_sel_p = selection_by_rank(ud), selection_by_rank(up)
    u_tod = tod_block(uni)
    u_cohort = cohort_block(uni, universes)
    u_reent = reentry_block(uni)
    dev_pnl = float(bridge.get("DEV_PNL") or 0.0)
    post_pnl = float(bridge.get("POST_PNL") or 0.0)
    u_dev_pnl = float(u_bridge.get("DEV_PNL") or 0.0)
    u_post_pnl = float(u_bridge.get("POST_PNL") or 0.0)
    ref_ratio = (post_pnl / dev_pnl) if abs(dev_pnl) > 1e-9 else None
    u_ratio = (u_post_pnl / u_dev_pnl) if abs(u_dev_pnl) > 1e-9 else None
    same_decay = bool(
        ref_ratio is not None and u_ratio is not None and ref_ratio < 0.20 and u_ratio < 0.20
    )
    same_tail = bool(
        u_tail_d.get("kind") == tail_d.get("kind")
        and u_tail_d.get("kind") in {"RIGHT_TAIL_DEPENDENT", "FEW_DAY_DEPENDENT"}
    )
    u_g_d = u_sel_d.get("rank1_minus_rank5_avg")
    u_g_p = u_sel_p.get("rank1_minus_rank5_avg")
    same_sel = bool(
        u_g_d is not None and u_g_d > 0 and (u_g_p is None or u_g_p < RANK_GAP_RATIO_DECAY * u_g_d)
    )
    grid_not_primary = bool(same_decay)
    return {
        "eco_dev": eco_d,
        "eco_post": eco_p,
        "bridge": bridge,
        "overlay": ov,
        "tail_dev": tail_d,
        "tail_post": tail_p,
        "symbols_dev": sym_d,
        "symbols_post": sym_p,
        "TOP_SYMBOL_PNL_SHARE_DEV": top_share_d,
        "TOP_SYMBOL_PNL_SHARE_POST": top_share_p,
        "sel_dev": sel_d,
        "sel_post": sel_p,
        "feat_dist": feat_dist,
        "feat_dist_selected": feat_dist_sel,
        "feat_rel": feat_rel,
        "fill": fill,
        "exit": exits,
        "tod": tod,
        "reentry": reent,
        "cohort": cohort,
        "market": market,
        "daily": {
            "DEV": daily_pnl(d),
            "POST": daily_pnl(p),
            "UNIFORM10_DEV": daily_pnl(ud),
            "UNIFORM10_POST": daily_pnl(up),
        },
        "uniform10": {
            "bridge": u_bridge,
            "tail_dev": u_tail_d,
            "tail_post": u_tail_p,
            "eco_dev": economics(ud),
            "eco_post": economics(up),
            "tod": u_tod,
            "sel_dev": u_sel_d,
            "sel_post": u_sel_p,
            "cohort": u_cohort,
            "reentry": u_reent,
            "same_tail_kind": u_tail_d.get("kind") == tail_d.get("kind"),
            "same_tail_disappeared": same_tail and (u_tail_d.get("top3_share") or 0) - (u_tail_p.get("top3_share") or 0) >= 0.15,
            "same_selection_decay": same_sel,
            "POST_OVER_DEV_RATIO_REF": ref_ratio,
            "POST_OVER_DEV_RATIO_U10": u_ratio,
            "ANCHOR_GRID_NOT_PRIMARY_DRIVER": grid_not_primary,
        },
        "classify": clf,
    }

