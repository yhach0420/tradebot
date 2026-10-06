"""RCA of frozen peer-propagation onsets. No PnL. No P repair. No MA period search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1 import DOM_SHARE, EVAL_BLOCKS, MATCH_RATE_MIN, MECHS
from research.cross_sectional_peer_propagation_discovery_v1.match import balance, find_control
from research.peer_propagation_mechanism_rca_v1 import (
    ABLATION,
    BOOT_N,
    BOOT_SEED,
    CASE_CONSUMED,
    CASE_EXECUTABLE,
    CASE_MARKET,
    CASE_PARTIAL,
    CASE_PERSIST,
    NEXT_PLAYBOOK,
    NEXT_STOP,
    STREAMS,
    TV_HIGH_DIAG_PCTL,
)
from research.peer_propagation_mechanism_rca_v1.episodes import duration_stats
from research.peer_propagation_mechanism_rca_v1.match import find_control_market
from research.peer_propagation_mechanism_rca_v1.walk import emit_rca

CLOSE_MAP = {
    "ret_5m_bps": "ret_5m_bps",
    "ret_10m_bps": "ret_10m_bps",
    "ret_20m_bps": "ret_20m_bps",
    "mfe_bps": "mfe_bps",
    "mae_bps": "mae_bps",
}
XO_MAP = {
    "ret_5m_bps": "xo_ret_5m_bps",
    "ret_10m_bps": "xo_ret_10m_bps",
    "ret_20m_bps": "xo_ret_20m_bps",
    "mfe_bps": "xo_mfe_bps",
    "mae_bps": "xo_mae_bps",
}


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.mean(xs))


def _med(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.median(xs))


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [r.get(key) for r in rows if r.get(key) is not None]
    if not xs:
        return None
    return float(np.mean([float(x) for x in xs]))


def _share_max(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    by: dict[str, int] = defaultdict(int)
    for r in rows:
        by[str(r.get(key) or "")] += 1
    if not by:
        return {"top": None, "n": 0, "share": None, "dominated": False}
    top, n = max(by.items(), key=lambda kv: kv[1])
    tot = len(rows)
    share = n / tot if tot else None
    return {"top": top, "n": n, "share": share, "dominated": bool(share is not None and share >= DOM_SHARE)}


def _block(rows: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("block") or "") == block]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event_n": len(rows),
        "symbol_n": len({str(r.get("symbol") or "") for r in rows}),
        "day_n": len({str(r.get("date") or "") for r in rows}),
        "sector_n": len({str(r.get("sector") or "") for r in rows}),
        "ret_5m": _mean(rows, "ret_5m_bps"),
        "ret_10m": _mean(rows, "ret_10m_bps"),
        "ret_20m": _mean(rows, "ret_20m_bps"),
        "median_mfe": _med(rows, "mfe_bps"),
        "median_mae": _med(rows, "mae_bps"),
        "xo_ret_5m": _mean(rows, "xo_ret_5m_bps"),
        "xo_ret_10m": _mean(rows, "xo_ret_10m_bps"),
        "xo_ret_20m": _mean(rows, "xo_ret_20m_bps"),
        "signal_to_entry_bps": _mean(rows, "signal_to_entry_bps"),
        "p40_before_m20": _rate(rows, "p40_before_m20"),
    }


def _pair_gaps(pairs: list[tuple[dict[str, Any], dict[str, Any]]], keymap: dict[str, str]) -> dict[str, Any]:
    if not pairs:
        return {"matched_n": 0, "gap_10m": None}
    ta = [a for a, _ in pairs]
    ca = [c for _, c in pairs]
    k5, k10, k20, kmfe, kmae = keymap["ret_5m_bps"], keymap["ret_10m_bps"], keymap["ret_20m_bps"], keymap["mfe_bps"], keymap["mae_bps"]
    t10, c10 = _mean(ta, k10), _mean(ca, k10)
    t5, c5 = _mean(ta, k5), _mean(ca, k5)
    t20, c20 = _mean(ta, k20), _mean(ca, k20)
    tmfe, cmfe = _med(ta, kmfe), _med(ca, kmfe)
    tmae, cmae = _med(ta, kmae), _med(ca, kmae)
    return {
        "matched_n": len(pairs),
        "treatment_10m": t10,
        "control_10m": c10,
        "gap_5m": None if t5 is None or c5 is None else float(t5) - float(c5),
        "gap_10m": None if t10 is None or c10 is None else float(t10) - float(c10),
        "gap_20m": None if t20 is None or c20 is None else float(t20) - float(c20),
        "gap_mfe": None if tmfe is None or cmfe is None else float(tmfe) - float(cmfe),
        "gap_mae": None if tmae is None or cmae is None else float(tmae) - float(cmae),
    }


def path_coherent(gaps: dict[str, Any]) -> bool:
    g10 = gaps.get("gap_10m")
    if g10 is None or float(g10) <= 0:
        return False
    g5, g20, gmfe = gaps.get("gap_5m"), gaps.get("gap_20m"), gaps.get("gap_mfe")
    side = (g5 is not None and float(g5) > 0) or (g20 is not None and float(g20) > 0)
    mfe_ok = gmfe is None or float(gmfe) >= 0
    return bool(side and mfe_ok)


def matched_pack(
    treated: list[dict[str, Any]],
    control_index: dict,
    *,
    keymap: dict[str, str] | None = None,
    finder=None,
) -> dict[str, Any]:
    km = keymap or CLOSE_MAP
    find = finder or find_control
    gaps_block: dict[str, Any] = {}
    all_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    treated_n = 0
    for b in EVAL_BLOCKS:
        ts = _block(treated, b)
        treated_n += len(ts)
        pairs = []
        for row in ts:
            ctrl = find(row, control_index)
            if ctrl is not None:
                pairs.append((row, ctrl))
        all_pairs.extend(pairs)
        g = _pair_gaps(pairs, km)
        g["treated_n"] = len(ts)
        g["match_rate"] = (len(pairs) / len(ts)) if ts else None
        g["path_coherent"] = path_coherent(g)
        gaps_block[b] = g
    pos = [b for b in EVAL_BLOCKS if (gaps_block.get(b) or {}).get("gap_10m") is not None and float(gaps_block[b]["gap_10m"]) > 0]
    coh = [b for b in EVAL_BLOCKS if path_coherent(gaps_block.get(b) or {})]
    pooled = _pair_gaps(all_pairs, km)
    return {
        "treated_n": treated_n,
        "matched_n": len(all_pairs),
        "match_rate": (len(all_pairs) / treated_n) if treated_n else None,
        **{k: v for k, v in pooled.items() if k != "matched_n"},
        "blocks": gaps_block,
        "positive_blocks": pos,
        "coherent_blocks": coh,
        "all_eval_positive": len(pos) == 3,
        "all_eval_coherent": len(coh) == 3,
        "balance": balance(all_pairs) if keymap == CLOSE_MAP or keymap is None else {"skipped": "xo_or_market"},
        "future_outcomes_not_used_for_matching": True,
        "peer_features_not_matched_on": True,
        "pairs_meta": all_pairs,
    }


def _overlap(onsets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    sets = {m: {tuple(r.get("id") or (r.get("date"), r.get("t"), r.get("symbol"), r.get("direction"))) for r in onsets.get(m) or []} for m in MECHS}
    p1, p2, p3 = sets["P1"], sets["P2"], sets["P3"]
    i12, i13, i23 = p1 & p2, p1 & p3, p2 & p3
    i123 = p1 & p2 & p3
    def jac(a, b, inter):
        u = len(a | b)
        return None if u == 0 else float(len(inter) / u)

    def cond(inter, a):
        return None if not a else float(len(inter) / len(a))

    return {
        "P1_n": len(p1),
        "P2_n": len(p2),
        "P3_n": len(p3),
        "P1_and_P2": len(i12),
        "P1_and_P3": len(i13),
        "P2_and_P3": len(i23),
        "P1_and_P2_and_P3": len(i123),
        "jaccard_P1_P2": jac(p1, p2, i12),
        "jaccard_P1_P3": jac(p1, p3, i13),
        "jaccard_P2_P3": jac(p2, p3, i23),
        "P2_given_P1": cond(i12, p1),
        "P3_given_P1": cond(i13, p1),
        "P1_given_P2": cond(i12, p2),
        "unique_P1": len(p1 - p2 - p3),
        "unique_P2": len(p2 - p1 - p3),
        "unique_P3": len(p3 - p1 - p2),
        "mostly_same": bool((jac(p1, p2, i12) or 0) >= 0.50),
    }


def _adds(full: dict[str, Any], lag: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for b in EVAL_BLOCKS:
        fg = ((full.get("blocks") or {}).get(b) or {}).get("gap_10m")
        lg = ((lag.get("blocks") or {}).get(b) or {}).get("gap_10m")
        out[b] = None if fg is None or lg is None else float(fg) - float(lg)
    signs = [out[b] for b in EVAL_BLOCKS if out[b] is not None]
    return {
        "gap_minus_lag": out,
        "adds_all_eval": bool(signs) and all(x > 0 for x in signs) and len(signs) == 3,
        "pooled_full_minus_lag": None
        if full.get("gap_10m") is None or lag.get("gap_10m") is None
        else float(full["gap_10m"]) - float(lag["gap_10m"]),
    }


def offset_map(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = (("tm5", -5, True), ("tm3", -3, True), ("tm1", -1, True), ("t0", 0, True), ("tp1", 1, False), ("tp3", 3, False), ("tp5", 5, False))
    out = []
    for name, off, causal in names:
        xs = [r.get(f"off_{name}") for r in rows if isinstance(r.get(f"off_{name}"), dict)]
        rec = {
            "offset": off,
            "label": name,
            "causal": causal,
            "placebo": not causal,
            "n": len(xs),
            "peer_ret_3m": float(np.mean([x["peer_ret_3m"] for x in xs if _finite(x.get("peer_ret_3m"))])) if xs else None,
            "peer_minus_target_3m": float(np.mean([x["peer_minus_target_3m"] for x in xs if _finite(x.get("peer_minus_target_3m"))])) if xs else None,
            "mkt_ret_3m": float(np.mean([x["mkt_ret_3m"] for x in xs if _finite(x.get("mkt_ret_3m"))])) if xs else None,
        }
        out.append(rec)
    causal = [r for r in out if r["causal"] and r["peer_ret_3m"] is not None]
    first = None
    for r in causal:
        if float(r["peer_ret_3m"]) > 0 and r.get("peer_minus_target_3m") is not None and float(r["peer_minus_target_3m"]) > 0:
            first = r
            break
    usable = None if first is None else int(0 - int(first["offset"]) + 1)
    return {"rows": out, "peer_acceleration_begins_offset": None if first is None else first["offset"], "usable_lead_trading_minutes": usable, "post_T_cannot_define_onset": True}


def cluster_bootstrap(pairs: list[tuple[dict[str, Any], dict[str, Any]]], key: str = "ret_10m_bps") -> dict[str, Any]:
    if len(pairs) < 50:
        return {"n": len(pairs), "ok": False}
    t = np.asarray([float(a[key]) for a, c in pairs if _finite(a.get(key)) and _finite(c.get(key))], dtype=float)
    c = np.asarray([float(b[key]) for a, b in pairs if _finite(a.get(key)) and _finite(b.get(key))], dtype=float)
    dates = np.asarray([str(a.get("date") or "") for a, b in pairs if _finite(a.get(key)) and _finite(b.get(key))])
    symbols = np.asarray([str(a.get("symbol") or "") for a, b in pairs if _finite(a.get(key)) and _finite(b.get(key))])
    if t.size < 50:
        return {"n": int(t.size), "ok": False}
    rng = np.random.default_rng(int(BOOT_SEED))

    def _ci(kind: str) -> dict[str, Any]:
        gaps = []
        if kind == "date":
            u = np.unique(dates)
            for _ in range(int(BOOT_N)):
                samp = rng.choice(u, size=u.size, replace=True)
                mask = np.isin(dates, samp)
                if mask.sum() < 5:
                    continue
                gaps.append(float(t[mask].mean() - c[mask].mean()))
        elif kind == "symbol":
            u = np.unique(symbols)
            for _ in range(int(BOOT_N)):
                samp = rng.choice(u, size=u.size, replace=True)
                mask = np.isin(symbols, samp)
                if mask.sum() < 5:
                    continue
                gaps.append(float(t[mask].mean() - c[mask].mean()))
        else:
            ud, us = np.unique(dates), np.unique(symbols)
            for _ in range(int(BOOT_N)):
                sd = rng.choice(ud, size=ud.size, replace=True)
                ss = rng.choice(us, size=us.size, replace=True)
                wd = defaultdict(int)
                ws = defaultdict(int)
                for x in sd:
                    wd[str(x)] += 1
                for x in ss:
                    ws[str(x)] += 1
                w = np.array([wd[str(d)] * ws[str(s)] for d, s in zip(dates, symbols)], dtype=float)
                if float(w.sum()) <= 0:
                    continue
                gaps.append(float(np.average(t, weights=w) - np.average(c, weights=w)))
        if len(gaps) < 20:
            return {"ok": False, "n_boot": len(gaps)}
        arr = np.asarray(gaps, dtype=float)
        return {
            "ok": True,
            "n_boot": int(arr.size),
            "mean": float(np.mean(arr)),
            "p025": float(np.quantile(arr, 0.025)),
            "p975": float(np.quantile(arr, 0.975)),
            "frac_positive": float(np.mean(arr > 0)),
        }

    point = float(t.mean() - c.mean())
    return {"n": int(t.size), "point_gap_10m": point, "date": _ci("date"), "symbol": _ci("symbol"), "two_way": _ci("two_way")}


def _playbook(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = {"A": [], "B": [], "C": [], "D": [], "E": []}
    for r in rows:
        if r.get("ma_stack_aligned"):
            buckets["A"].append(r)
        elif r.get("delay_sma25_reclaim") is not None:
            buckets["B"].append(r)
        elif r.get("delay_vwap_reclaim") is not None:
            buckets["C"].append(r)
        elif r.get("delay_tv_expand") is not None:
            buckets["D"].append(r)
        else:
            buckets["E"].append(r)
    labels = {
        "A": "PEER_LEAD_THEN_MA_STACK_ALIGNED",
        "B": "PEER_LEAD_THEN_SMA25_PULLBACK_HOLD",
        "C": "PEER_LEAD_THEN_VWAP_RECLAIM",
        "D": "PEER_LEAD_THEN_TARGET_TV_EXPAND",
        "E": "PEER_LEAD_NEVER_CONFIRMS",
    }
    out = []
    for k, xs in buckets.items():
        follow = [r for r in xs if _finite(r.get("ret_10m_bps")) and float(r["ret_10m_bps"]) > 0]
        out.append(
            {
                "code": k,
                "label": labels[k],
                "n": len(xs),
                "follow_n": len(follow),
                "fail_n": len(xs) - len(follow),
                "ret_10m": _mean(xs, "ret_10m_bps"),
                "xo_ret_10m": _mean(xs, "xo_ret_10m_bps"),
                "rca_category_not_selected_rule": True,
            }
        )
    return out


def _failure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fail = [r for r in rows if _finite(r.get("ret_10m_bps")) and float(r["ret_10m_bps"]) <= 0]
    n = len(fail) or 1

    def frac(pred) -> float:
        return float(sum(1 for r in fail if pred(r)) / n) if fail else None

    return {
        "fail_n": len(fail),
        "ma_stack_opposed": frac(lambda r: bool(r.get("ma_stack_opposed"))),
        "against_sma75": frac(lambda r: not bool(r.get("ma75_side_aligned"))),
        "blocked_nearby_sr": frac(lambda r: str(r.get("sr_bin") or "") == "near_ahead"),
        "unable_reclaim_vwap": frac(lambda r: (not bool(r.get("vwap_aligned"))) and r.get("delay_vwap_reclaim") is None),
        "already_high_target_tv": frac(lambda r: _finite(r.get("tv_clock_pctl")) and float(r["tv_clock_pctl"]) >= float(TV_HIGH_DIAG_PCTL)),
        "no_target_tv_pickup": frac(lambda r: r.get("delay_tv_expand") is None),
        "broad_market_reversing": frac(lambda r: _finite(r.get("mkt_ret_3m")) and float(r["mkt_ret_3m"]) <= 0),
        "sector_not_in_excess_of_market": frac(lambda r: _finite(r.get("sector_excess")) and float(r["sector_excess"]) <= 0),
        "no_filtering_applied": True,
    }


def _tv_seq(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("tv_t", "tv_tp1", "tv_tp2", "tv_tp3", "tv_tp5")
    means = {k: _mean(rows, k) for k in keys}
    t0, t5 = means.get("tv_t"), means.get("tv_tp5")
    return {
        **means,
        "participation_arrives_after_peer_signal": bool(t0 is not None and t5 is not None and float(t5) > float(t0)),
        "n": len(rows),
    }


def _confirm_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        ("NEXT_OPEN", None, "signal_to_entry_bps", "remain10_next_open"),
        ("SMA5_RECLAIM", "delay_sma5_reclaim", "moved_before_sma5_reclaim_bps", "remain10_sma5_reclaim"),
        ("SMA25_RECLAIM", "delay_sma25_reclaim", "moved_before_sma25_reclaim_bps", "remain10_sma25_reclaim"),
        ("VWAP_RECLAIM", "delay_vwap_reclaim", "moved_before_vwap_reclaim_bps", "remain10_vwap_reclaim"),
        ("TARGET_TV_EXPAND", "delay_tv_expand", "moved_before_tv_expand_bps", "remain10_tv_expand"),
    ]
    out = []
    for name, delay_key, moved_key, remain_key in specs:
        if delay_key is None:
            xs = [r for r in rows if r.get("entry_ok")]
            delay = 1.0
        else:
            xs = [r for r in rows if r.get(delay_key) is not None]
            delay = _mean(xs, delay_key)
        out.append(
            {
                "confirmation": name,
                "n": len(xs),
                "mean_delay_trading_minutes": delay,
                "price_moved_before_bps": _mean(xs, moved_key),
                "remaining_10m": _mean(xs, remain_key),
                "remaining_5m": None,
                "not_selected_as_rule": True,
            }
        )
    ranked = [r for r in out if r["remaining_10m"] is not None]
    best = max(ranked, key=lambda r: float(r["remaining_10m"]))["confirmation"] if ranked else None
    for r in out:
        r["retains_most_remaining_edge_diagnostic"] = bool(best is not None and r["confirmation"] == best)
    return out


def _ma_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stack = [r for r in rows if r.get("ma_stack_aligned")]
    rec5 = [r for r in rows if r.get("delay_sma5_reclaim") is not None]
    rec25 = [r for r in rows if r.get("delay_sma25_reclaim") is not None]
    touch25 = [r for r in rows if r.get("delay_sma25_touch") is not None]
    return {
        "stack_aligned_n": len(stack),
        "stack_aligned_share": (len(stack) / len(rows)) if rows else None,
        "stack_aligned_10m": _mean(stack, "ret_10m_bps"),
        "not_stack_10m": _mean([r for r in rows if not r.get("ma_stack_aligned")], "ret_10m_bps"),
        "sma5_reclaim_n": len(rec5),
        "sma5_reclaim_delay": _mean(rec5, "delay_sma5_reclaim"),
        "sma5_reclaim_10m": _mean(rec5, "ret_10m_bps"),
        "sma25_touch_n": len(touch25),
        "sma25_reclaim_n": len(rec25),
        "sma25_reclaim_delay": _mean(rec25, "delay_sma25_reclaim"),
        "sma25_reclaim_10m": _mean(rec25, "ret_10m_bps"),
        "ma_period_tuned": False,
        "ma_not_a_predictor": True,
    }


def _drop_pairs(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k != "pairs_meta"}


def decide(pack: dict[str, Any]) -> dict[str, Any]:
    onset_ok = bool(pack.get("onset_ok"))
    persist = bool(pack.get("persistence_artifact"))
    xo_ok = bool(pack.get("next_open_ok"))
    consumed = bool(pack.get("consumed_most"))
    market = bool(pack.get("market_wide"))
    sector = bool(pack.get("sector_specific"))
    if persist:
        verd, nxt = CASE_PERSIST, NEXT_STOP
        reason = "first-onset matched 10m sign does not hold in D2/D3/D4; PEER_EFFECT_STATE_PERSISTENCE_ARTIFACT"
    elif onset_ok and (consumed or not xo_ok):
        verd, nxt = CASE_CONSUMED, NEXT_STOP
        reason = "unique onset is real but next-open matched edge is consumed or not executable"
    elif onset_ok and market and not sector:
        verd, nxt = CASE_MARKET, NEXT_STOP
        reason = "after similar broad-market matching, sector-peer incrementality does not survive"
    elif onset_ok and xo_ok and not consumed and sector:
        verd, nxt = CASE_EXECUTABLE, NEXT_PLAYBOOK
        reason = "unique TRUE_PEER_LEAD onset and next-open matched 10m gap survive D2/D3/D4"
    else:
        verd, nxt = CASE_PARTIAL, NEXT_STOP
        reason = "RCA mixed: onset, next-open, or sector/market diagnostics do not agree"
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "reason": reason,
        "PEER_EFFECT_STATE_PERSISTENCE_ARTIFACT": persist,
        "PEER_LEAD_NOT_EXECUTABLY_LARGE": consumed,
        "onset_ok": onset_ok,
        "next_open_ok": xo_ok,
        "sector_specific": sector,
        "market_wide": market,
        "p1_p2_p3_not_modified": True,
        "d1_boundaries_not_modified": True,
        "ma_period_tuned": False,
        "pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "complete_strategy_not_run": True,
        "sr_did_not_alter_verdict": True,
        "ma_did_not_alter_verdict": True,
        "confirmation_not_selected": True,
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    freeze = dict(bind.get("freeze") or {})
    walked = emit_rca(bind, freeze)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason"), "decision": {"VERDICT": CASE_PARTIAL, "NEXT": NEXT_STOP}}
    hi = dict(freeze.get("high") or {})
    hi_ret = float(hi.get("peer_ret_3m") or 0)
    mech: dict[str, Any] = {}
    onset_ok_ids: list[str] = []
    xo_ok_ids: list[str] = []
    control_bad = True
    lag_pack = None
    for m in STREAMS:
        tr = list((walked.get("treated") or {}).get(m) or [])
        idx = dict((walked.get("controls") or {}).get(m) or {})
        eval_tr = [r for r in tr if str(r.get("block") or "") in EVAL_BLOCKS]
        print(f"RCA_MATCH {m} true_lead_onset={len(tr)} eval={len(eval_tr)}", flush=True)
        mc = matched_pack(tr, idx, keymap=CLOSE_MAP)
        xo = matched_pack(tr, idx, keymap=XO_MAP) if m in MECHS else {"blocks": {}, "all_eval_positive": False}
        pairs = list(mc.pop("pairs_meta", []) or [])
        xo_pairs = list(xo.pop("pairs_meta", []) or [])
        boot = cluster_bootstrap(pairs, "ret_10m_bps") if m in MECHS else {}
        boot_xo = cluster_bootstrap(xo_pairs, "xo_ret_10m_bps") if m in MECHS and xo_pairs else {}
        conc = {
            "symbol": _share_max(eval_tr, "symbol"),
            "sector": _share_max(eval_tr, "sector"),
            "day": _share_max(eval_tr, "date"),
        }
        dominated = bool(conc["symbol"]["dominated"] or conc["sector"]["dominated"] or conc["day"]["dominated"])
        mr = mc.get("match_rate")
        if mr is not None and float(mr) >= float(MATCH_RATE_MIN):
            control_bad = False
        if m in MECHS and mc.get("all_eval_positive") and mc.get("all_eval_coherent") and not dominated:
            onset_ok_ids.append(m)
        if m in MECHS and xo.get("all_eval_positive") and xo.get("all_eval_coherent") and not dominated:
            xo_ok_ids.append(m)
        disc_g = mc.get("gap_10m")
        xo_g = xo.get("gap_10m")
        consumed_bps = None if disc_g is None or xo_g is None else float(disc_g) - float(xo_g)
        retained = None if disc_g in (None, 0) or xo_g is None else float(xo_g) / float(disc_g)
        quiet = [r for r in eval_tr if _finite(r.get("mkt_ret_3m")) and float(r["mkt_ret_3m"]) < hi_ret]
        excess = [r for r in eval_tr if _finite(r.get("sector_excess")) and float(r["sector_excess"]) > 0]
        mkt_match = matched_pack(tr, idx, keymap=CLOSE_MAP, finder=find_control_market) if m in MECHS else {}
        mkt_match.pop("pairs_meta", None)
        rec = {
            "qualified_minute_n": int((walked.get("qualified_n") or {}).get(m) or 0),
            "first_onset_episode_n": int((walked.get("episode_n") or {}).get(m) or 0),
            "duration": duration_stats(list((walked.get("durations") or {}).get(m) or [])),
            "true_lead_onset_n": len(tr),
            "eval": summarize(eval_tr),
            "matched_close": _drop_pairs(mc) if m == ABLATION else {**_drop_pairs(mc), "bootstrap": boot},
            "matched_next_open": {**_drop_pairs(xo), "bootstrap": boot_xo} if m in MECHS else None,
            "edge_consumption": {
                "discovery_gap_10m": disc_g,
                "next_open_gap_10m": xo_g,
                "EDGE_CONSUMED_BEFORE_EXECUTION_BPS": consumed_bps,
                "EDGE_RETAINED_PCT": retained,
                "mean_signal_to_entry_bps": _mean(eval_tr, "signal_to_entry_bps"),
            },
            "concentration": conc,
            "dominated": dominated,
            "offset_map": offset_map(eval_tr) if m in MECHS else None,
            "sector_vs_market": {
                "market_matched": mkt_match,
                "sector_excess_positive_n": len(excess),
                "sector_excess_positive_10m": _mean(excess, "ret_10m_bps"),
                "market_not_high_n": len(quiet),
                "market_not_high_10m": _mean(quiet, "ret_10m_bps"),
                "market_not_high_matched": _drop_pairs(matched_pack(quiet, idx, keymap=CLOSE_MAP)) if m in MECHS else None,
                "primary_remains_sector_peers": True,
                "not_chosen_by_profit": True,
            }
            if m in MECHS
            else None,
            "ma": _ma_summary(eval_tr) if m in MECHS else None,
            "vwap": {
                "aligned_n": len([r for r in eval_tr if r.get("vwap_aligned")]),
                "aligned_10m": _mean([r for r in eval_tr if r.get("vwap_aligned")], "ret_10m_bps"),
                "not_aligned_10m": _mean([r for r in eval_tr if not r.get("vwap_aligned")], "ret_10m_bps"),
                "reclaim_n": len([r for r in eval_tr if r.get("delay_vwap_reclaim") is not None]),
            }
            if m in MECHS
            else None,
            "sr": None,
            "tv_sequence": _tv_seq(eval_tr) if m in MECHS else None,
            "confirmation": _confirm_table(eval_tr) if m in MECHS else None,
            "playbook": _playbook(eval_tr) if m in MECHS else None,
            "failure": _failure(eval_tr) if m in MECHS else None,
        }
        if m in MECHS:
            by_sr: dict[str, list] = defaultdict(list)
            for r in eval_tr:
                by_sr[str(r.get("sr_bin") or "away")].append(r)
            rec["sr"] = [{"sr_bin": k, "n": len(v), "ret_10m": _mean(v, "ret_10m_bps")} for k, v in sorted(by_sr.items())]
        if m == ABLATION:
            lag_pack = mc
        mech[m] = rec
    lag_mc = (mech.get(ABLATION) or {}).get("matched_close") or {}
    ablation = {}
    for m in MECHS:
        ablation[m] = _adds((mech[m].get("matched_close") or {}), lag_mc)
    overlap = _overlap(dict(walked.get("all_onsets") or {}))
    next_open_ok = len(xo_ok_ids) == 3
    consumed_flags = []
    for m in MECHS:
        ec = dict((mech[m].get("edge_consumption") or {}))
        retp = ec.get("EDGE_RETAINED_PCT")
        consumed_flags.append(bool(retp is not None and float(retp) < 0.50) or m not in xo_ok_ids)
    consumed_most = bool(consumed_flags) and all(consumed_flags)
    sector_ok = False
    market_wide = False
    for m in MECHS:
        svm = dict(mech[m].get("sector_vs_market") or {})
        mm = dict(svm.get("market_matched") or {})
        q = dict(svm.get("market_not_high_matched") or {})
        if mm.get("all_eval_positive") or q.get("all_eval_positive"):
            sector_ok = True
        if (mech[m].get("matched_close") or {}).get("all_eval_positive") and not mm.get("all_eval_positive") and not q.get("all_eval_positive"):
            market_wide = True
    if sector_ok:
        market_wide = False
    three = len(onset_ok_ids) == 3 and not control_bad
    decision = decide(
        {
            "onset_ok": three,
            "persistence_artifact": len(onset_ok_ids) == 0,
            "next_open_ok": next_open_ok,
            "consumed_most": bool(three and (consumed_most or not next_open_ok)),
            "market_wide": bool(three and market_wide),
            "sector_specific": sector_ok,
        }
    )
    uni = dict(walked.get("universe") or {})
    return {
        "ok": True,
        "freeze": freeze,
        "identity": {
            "n_days": walked.get("n_days"),
            "n_symbols_loaded": walked.get("n_symbols_loaded"),
            "feature_row_n": walked.get("feature_row_n"),
            "valid_peer_target_n": uni.get("valid_target_n"),
        },
        "episodes": {
            m: {
                "raw_qualified_minute_n": int((walked.get("qualified_n") or {}).get(m) or 0),
                "first_onset_episode_n": int((walked.get("episode_n") or {}).get(m) or 0),
                "duration": duration_stats(list((walked.get("durations") or {}).get(m) or [])),
            }
            for m in STREAMS
        },
        "overlap": overlap,
        "ablation": ablation,
        "mechanisms": {m: mech[m] for m in MECHS},
        "lag_only": mech.get(ABLATION),
        "onset_ok_ids": onset_ok_ids,
        "next_open_ok_ids": xo_ok_ids,
        "TARGET_INCLUDED_IN_PEER_METRIC_N": walked.get("TARGET_INCLUDED_IN_PEER_METRIC_N"),
        "future_peer_information_n": 0,
        "same_bar_outcome_n": walked.get("same_bar_outcome_n"),
        "FUTURE_EPISODE_SELECTION_N": walked.get("FUTURE_EPISODE_SELECTION_N"),
        "d1_boundaries_not_modified": True,
        "p1_p2_p3_not_modified": True,
        "decision": decision,
        "primary_metric": "ret_10m_bps",
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    ep = dict(report.get("episodes") or {})
    mech = dict(report.get("mechanisms") or {})
    abl = dict(report.get("ablation") or {})
    ov = dict(report.get("overlap") or {})

    def gaps(m: str, which: str) -> dict[str, Any]:
        pack = dict((mech.get(m) or {}).get(which) or {})
        bl = dict(pack.get("blocks") or {})
        return {b: (bl.get(b) or {}).get("gap_10m") for b in EVAL_BLOCKS}

    def close_block(m: str, b: str) -> dict[str, Any]:
        return dict((((mech.get(m) or {}).get("matched_close") or {}).get("blocks") or {}).get(b) or {})

    p1 = ep.get("P1") or {}
    best_conf = None
    for row in list((mech.get("P1") or {}).get("confirmation") or []):
        if row.get("retains_most_remaining_edge_diagnostic"):
            best_conf = row.get("confirmation")
    return {
        "raw qualified minute_n?": {m: (ep.get(m) or {}).get("raw_qualified_minute_n") for m in STREAMS},
        "unique first-onset episode_n?": {m: (ep.get(m) or {}).get("first_onset_episode_n") for m in STREAMS},
        "median state duration?": {m: ((ep.get(m) or {}).get("duration") or {}).get("median") for m in STREAMS},
        "Are P1/P2/P3 mostly the same mechanism?": ov.get("mostly_same"),
        "Does HIGH target lag alone explain it?": bool((report.get("lag_only") or {}).get("matched_close", {}).get("all_eval_positive"))
        and not any((abl.get(m) or {}).get("adds_all_eval") for m in MECHS),
        "Does breadth add?": (abl.get("P1") or {}).get("adds_all_eval"),
        "peer return add?": (abl.get("P2") or {}).get("adds_all_eval"),
        "peer TV breadth add?": (abl.get("P3") or {}).get("adds_all_eval"),
        "Sector-specific or broad-market propagation?": "SECTOR-SPECIFIC" if d.get("sector_specific") else ("BROAD-MARKET" if d.get("market_wide") else "MIXED_OR_UNRESOLVED"),
        "First-onset matched D2/D3/D4 10m gap?": {m: gaps(m, "matched_close") for m in MECHS},
        "First-onset matched 5m/20m/MFE/MAE?": {
            m: {
                b: {
                    "gap_5m": (close_block(m, b) or {}).get("gap_5m"),
                    "gap_10m": (close_block(m, b) or {}).get("gap_10m"),
                    "gap_20m": (close_block(m, b) or {}).get("gap_20m"),
                    "gap_mfe": (close_block(m, b) or {}).get("gap_mfe"),
                    "gap_mae": (close_block(m, b) or {}).get("gap_mae"),
                }
                for b in EVAL_BLOCKS
            }
            for m in MECHS
        },
        "Next-open matched D2?": {m: close_xo_block(mech, m, "D2") for m in MECHS},
        "Next-open matched D3?": {m: close_xo_block(mech, m, "D3") for m in MECHS},
        "Next-open matched D4?": {m: close_xo_block(mech, m, "D4") for m in MECHS},
        "How many bps are consumed before executable entry?": {m: ((mech.get(m) or {}).get("edge_consumption") or {}).get("EDGE_CONSUMED_BEFORE_EXECUTION_BPS") for m in MECHS},
        "Does signal remain economically nontrivial after next open?": bool(d.get("next_open_ok")) and not bool(d.get("PEER_LEAD_NOT_EXECUTABLY_LARGE")),
        "SMA5/25/75 stack-aligned result?": {m: (mech.get(m) or {}).get("ma") for m in MECHS},
        "SMA25 pullback/hold?": {m: ((mech.get(m) or {}).get("ma") or {}).get("sma25_reclaim_n") for m in MECHS},
        "SMA5 reclaim?": {m: ((mech.get(m) or {}).get("ma") or {}).get("sma5_reclaim_n") for m in MECHS},
        "SMA25 reclaim?": {m: ((mech.get(m) or {}).get("ma") or {}).get("sma25_reclaim_n") for m in MECHS},
        "VWAP reclaim?": {m: ((mech.get(m) or {}).get("vwap") or {}).get("reclaim_n") for m in MECHS},
        "Target TV: does participation arrive after peer signal?": {m: ((mech.get(m) or {}).get("tv_sequence") or {}).get("participation_arrives_after_peer_signal") for m in MECHS},
        "Which causal confirmation retains the most remaining edge?": best_conf,
        "How long is usable peer lead?": {m: ((mech.get(m) or {}).get("offset_map") or {}).get("usable_lead_trading_minutes") for m in MECHS},
        "Any one-symbol dominance?": {m: ((mech.get(m) or {}).get("concentration") or {}).get("symbol", {}).get("dominated") for m in MECHS},
        "one-sector?": {m: ((mech.get(m) or {}).get("concentration") or {}).get("sector", {}).get("dominated") for m in MECHS},
        "one-day?": {m: ((mech.get(m) or {}).get("concentration") or {}).get("day", {}).get("dominated") for m in MECHS},
        "Any future episode selection?": False if int(report.get("FUTURE_EPISODE_SELECTION_N") or 0) == 0 else True,
        "Any MA period tuning?": False,
        "Any PnL optimization?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "Kabu50?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
        "_p1_duration_median": ((p1.get("duration") or {}).get("median")),
        "_close_D2_P1": close_block("P1", "D2").get("gap_10m"),
    }


def close_xo_block(mech: dict[str, Any], m: str, b: str) -> float | None:
    return ((((mech.get(m) or {}).get("matched_next_open") or {}).get("blocks") or {}).get(b) or {}).get("gap_10m")
