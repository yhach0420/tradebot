"""Core E1_X8 analyses: profiles, LOSO, random deletion, groups, verdict."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any, Optional

import numpy as np

from research.e1_x7_pfq.candidates import passes_candidate

from . import FROZEN, MIN_SYMBOL_SUPPORT, RANDOM_REPS, RANDOM_SEED, TARGET_SYMBOL
from .quantile_ops import jaccard, membership_ids, quantile_shift_sensitivity, thresholds_from_audits, tie_counts
from .signal import evaluate_update_signal, summarize_ft


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def build_episode_table(audits: list[dict[str, Any]], fg_by: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    thr = dict(FROZEN)
    rows = []
    for a in audits:
        eid = a["episode_id"]
        fg = fg_by.get(eid) or {}
        pu = a.get("price_update_count_10s")
        path_ok = bool(fg.get("evaluable"))
        update_parent = pu is not None and path_ok
        flow_parent = bool(a.get("ratio_valid")) and int(a.get("classified_trade_count_30s") or 0) >= 3 and path_ok
        joint_parent = update_parent and flow_parent
        row = {
            **{k: a.get(k) for k in (
                "episode_id", "cluster_id", "day", "session", "symbol",
                "price_update_count_10s", "uptick_volume_ratio_30s",
                "ratio_valid", "classified_trade_count_30s",
            )},
            "update_eligible_parent": update_parent,
            "flow_eligible_parent": flow_parent,
            "joint_eligible_parent": joint_parent,
            "path_evaluable": path_ok,
            "evaluable": fg.get("evaluable"),
            "best_net_pnl_bps_300s": fg.get("best_net_pnl_bps_300s"),
            "ft_plus5_vs_minus10": fg.get("ft_plus5_vs_minus10"),
            "ft_plus5_vs_minus15": fg.get("ft_plus5_vs_minus15"),
            "ft_plus10_vs_minus10": fg.get("ft_plus10_vs_minus10"),
            "ft_plus10_vs_minus15": fg.get("ft_plus10_vs_minus15"),
            "mem_UPDATE": passes_candidate(a, "PFQ_UPDATE_Q70", thr),
            "mem_FLOW": passes_candidate(a, "PFQ_FLOW_Q30", thr),
            "mem_JOINT": passes_candidate(a, "PFQ_JOINT", thr),
        }
        rows.append(row)
    return rows


def full_threshold_summary(audits: list[dict[str, Any]], thr: dict[str, Any]) -> dict[str, Any]:
    pu_vals = [float(a["price_update_count_10s"]) for a in audits if a.get("price_update_count_10s") is not None]
    flow_vals = [
        float(a["uptick_volume_ratio_30s"]) for a in audits
        if a.get("ratio_valid") and a.get("uptick_volume_ratio_30s") is not None
    ]
    return {
        "update_q70_full": thr["price_update_count_10s_q70"],
        "flow_q30_full": thr["uptick_volume_ratio_30s_q30"],
        "update_parent_n": len(pu_vals),
        "flow_parent_n": len(flow_vals),
        "joint_parent_n": sum(
            1 for a in audits
            if a.get("price_update_count_10s") is not None
            and a.get("ratio_valid")
            and int(a.get("classified_trade_count_30s") or 0) >= 3
        ),
        "update_tie_counts": tie_counts(pu_vals, thr["price_update_count_10s_q70"]),
        "flow_tie_counts": tie_counts(flow_vals, thr["uptick_volume_ratio_30s_q30"]),
        "update_thr_shift_sensitivity": quantile_shift_sensitivity(
            pu_vals, 0.70, thr["price_update_count_10s_q70"]
        ),
    }


def symbol_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = defaultdict(list)
    for r in rows:
        by[str(r["symbol"])].append(r)
    out = []
    for sym in sorted(by):
        rs = by[sym]
        pu = [_f(r["price_update_count_10s"]) for r in rs]
        pu = [x for x in pu if x is not None]
        flow = [
            _f(r["uptick_volume_ratio_30s"]) for r in rs
            if r.get("ratio_valid") and r.get("uptick_volume_ratio_30s") is not None
        ]
        flow = [x for x in flow if x is not None]
        n = len(rs)
        u_pass = sum(1 for r in rs if r["mem_UPDATE"])
        f_pass = sum(1 for r in rs if r["mem_FLOW"])
        j_pass = sum(1 for r in rs if r["mem_JOINT"])
        plus5 = [
            1.0 if (r.get("best_net_pnl_bps_300s") is not None and float(r["best_net_pnl_bps_300s"]) >= 5)
            else 0.0
            for r in rs if r.get("evaluable")
        ]
        p5m10 = [
            1.0 if r.get("ft_plus5_vs_minus10") == "PLUS_FIRST" else 0.0
            for r in rs if r.get("ft_plus5_vs_minus10") not in (None, "NOT_EVALUABLE")
        ]
        out.append({
            "symbol": sym,
            "n_all": n,
            "n_update_valid": len(pu),
            "n_flow_valid": len(flow),
            "n_joint_valid": sum(1 for r in rs if r.get("update_eligible_parent") and r.get("flow_eligible_parent")),
            "median_price_update_count_10s": float(median(pu)) if pu else None,
            "q70_price_update_count_10s": float(np.quantile(pu, 0.70)) if pu else None,
            "median_uptick_volume_ratio_30s": float(median(flow)) if flow else None,
            "q30_uptick_volume_ratio_30s": float(np.quantile(flow, 0.30)) if flow else None,
            "UPDATE_Q70_pass_n": u_pass,
            "UPDATE_Q70_pass_rate": u_pass / n if n else None,
            "FLOW_Q30_pass_n": f_pass,
            "FLOW_Q30_pass_rate": f_pass / n if n else None,
            "JOINT_pass_n": j_pass,
            "JOINT_pass_rate": j_pass / n if n else None,
            "fixed_grid_plus5_rate": float(np.mean(plus5)) if plus5 else None,
            "fixed_grid_plus5_before_minus10_rate": float(np.mean(p5m10)) if p5m10 else None,
            "is_285A": sym == TARGET_SYMBOL,
        })
    return out


def frozen_membership_concentration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def block(flag: str) -> list[dict[str, Any]]:
        cand = [r for r in rows if r[flag]]
        n = len(cand)
        c = Counter(str(r["symbol"]) for r in cand)
        overall = n / len(rows) if rows else 0
        ranked = []
        for i, (sym, cnt) in enumerate(c.most_common(), start=1):
            sym_n = sum(1 for r in rows if str(r["symbol"]) == sym)
            ranked.append({
                "symbol": sym,
                "candidate_count": cnt,
                "candidate_share": cnt / n if n else None,
                "symbol_pass_rate": cnt / sym_n if sym_n else None,
                "overall_pass_rate": overall,
                "pass_rate_diff_vs_overall": (cnt / sym_n - overall) if sym_n else None,
                "rank": i,
                "is_285A": sym == TARGET_SYMBOL,
            })
        return ranked

    return {
        "UPDATE": block("mem_UPDATE"),
        "FLOW": block("mem_FLOW"),
        "JOINT": block("mem_JOINT"),
        "UPDATE_285A": next((x for x in block("mem_UPDATE") if x["symbol"] == TARGET_SYMBOL), None),
    }


def loso_thresholds(audits: list[dict[str, Any]], full_thr: dict[str, Any]) -> list[dict[str, Any]]:
    symbols = sorted({str(a["symbol"]) for a in audits})
    out = []
    for s in symbols:
        remain = [a for a in audits if str(a["symbol"]) != s]
        thr = thresholds_from_audits(remain)
        out.append({
            "symbol": s,
            "removed_n": len(audits) - len(remain),
            "update_threshold_without": thr["price_update_count_10s_q70"],
            "update_threshold_delta": thr["price_update_count_10s_q70"] - full_thr["price_update_count_10s_q70"],
            "flow_threshold_without": thr["uptick_volume_ratio_30s_q30"],
            "flow_threshold_delta": thr["uptick_volume_ratio_30s_q30"] - full_thr["uptick_volume_ratio_30s_q30"],
            "pu_n": thr["pu_n"],
            "flow_n": thr["flow_n"],
            "is_285A": s == TARGET_SYMBOL,
        })
    return out


def membership_flips(
    audits: list[dict[str, Any]],
    full_thr: dict[str, Any],
    loso_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    full_u = membership_ids(audits, full_thr, "PFQ_UPDATE_Q70")
    full_f = membership_ids(audits, full_thr, "PFQ_FLOW_Q30")
    full_j = membership_ids(audits, full_thr, "PFQ_JOINT")
    out = []
    for lr in loso_rows:
        s = lr["symbol"]
        common = [a for a in audits if str(a["symbol"]) != s]
        thr = {
            "price_update_count_10s_q70": lr["update_threshold_without"],
            "uptick_volume_ratio_30s_q30": lr["flow_threshold_without"],
        }
        # membership on common only
        full_u_c = {e for e in full_u if not e.startswith("SKIP")}
        # filter full sets to common episode ids
        common_ids = {a["episode_id"] for a in common}
        fu = full_u & common_ids
        ff = full_f & common_ids
        fj = full_j & common_ids
        lu = membership_ids(common, thr, "PFQ_UPDATE_Q70")
        lf = membership_ids(common, thr, "PFQ_FLOW_Q30")
        lj = membership_ids(common, thr, "PFQ_JOINT")

        def flip(a: set[str], b: set[str]) -> tuple[int, float]:
            n = len(a ^ b)
            rate = n / len(common_ids) if common_ids else 0.0
            return n, rate

        u_n, u_r = flip(fu, lu)
        f_n, f_r = flip(ff, lf)
        j_n, j_r = flip(fj, lj)
        out.append({
            "symbol_removed": s,
            "update_membership_flip_n": u_n,
            "update_membership_flip_rate": u_r,
            "update_jaccard": jaccard(fu, lu),
            "flow_membership_flip_n": f_n,
            "flow_membership_flip_rate": f_r,
            "flow_jaccard": jaccard(ff, lf),
            "joint_membership_flip_n": j_n,
            "joint_membership_flip_rate": j_r,
            "joint_jaccard": jaccard(fj, lj),
            "candidate_count_full_on_common_UPDATE": len(fu),
            "candidate_count_loso_UPDATE": len(lu),
            "is_285A": s == TARGET_SYMBOL,
        })
    return out


def size_matched_random_deletion(
    audits: list[dict[str, Any]],
    full_thr: dict[str, Any],
    loso_rows: list[dict[str, Any]],
    flip_rows: list[dict[str, Any]],
    *,
    reps: int = RANDOM_REPS,
    seed: int = RANDOM_SEED,
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    by_sym = defaultdict(list)
    for a in audits:
        by_sym[str(a["symbol"])].append(a)
    # strata keys
    strata_all = defaultdict(list)
    for a in audits:
        strata_all[(a["day"], a.get("session"))].append(a)

    flip_by = {r["symbol_removed"]: r for r in flip_rows}
    out = []
    for lr in loso_rows:
        s = lr["symbol"]
        target = by_sym[s]
        # count per strata for s
        need = Counter((a["day"], a.get("session")) for a in target)
        actual_u_delta = abs(lr["update_threshold_delta"])
        actual_f_delta = abs(lr["flow_threshold_delta"])
        actual_flip = float(flip_by[s]["update_membership_flip_rate"])

        u_deltas = []
        f_deltas = []
        flip_rates = []
        invalid = 0
        for _ in range(reps):
            remove_ids = set()
            ok = True
            for key, n_need in need.items():
                pool = [a for a in strata_all[key] if str(a["symbol"]) != s]
                if len(pool) < n_need:
                    ok = False
                    break
                idx = rng.choice(len(pool), size=n_need, replace=False)
                for i in idx:
                    remove_ids.add(pool[int(i)]["episode_id"])
            if not ok:
                invalid += 1
                continue
            remain = [a for a in audits if a["episode_id"] not in remove_ids]
            thr = thresholds_from_audits(remain)
            u_deltas.append(abs(thr["price_update_count_10s_q70"] - full_thr["price_update_count_10s_q70"]))
            f_deltas.append(abs(thr["uptick_volume_ratio_30s_q30"] - full_thr["uptick_volume_ratio_30s_q30"]))
            # flip rate on common = all minus removed random set
            common_ids = {a["episode_id"] for a in remain}
            fu = membership_ids(audits, full_thr, "PFQ_UPDATE_Q70") & common_ids
            lu = membership_ids(remain, thr, "PFQ_UPDATE_Q70")
            flip_rates.append(len(fu ^ lu) / len(common_ids) if common_ids else 0.0)

        def pct(actual: float, dist: list[float]) -> Optional[float]:
            if not dist:
                return None
            return float(np.mean(np.asarray(dist) <= actual))

        out.append({
            "symbol": s,
            "removed_n": len(target),
            "actual_update_delta": lr["update_threshold_delta"],
            "actual_update_abs_delta": actual_u_delta,
            "random_update_abs_delta_median": float(np.median(u_deltas)) if u_deltas else None,
            "actual_update_delta_percentile": pct(actual_u_delta, u_deltas),
            "actual_flow_delta": lr["flow_threshold_delta"],
            "actual_flow_abs_delta": actual_f_delta,
            "random_flow_abs_delta_median": float(np.median(f_deltas)) if f_deltas else None,
            "actual_flow_delta_percentile": pct(actual_f_delta, f_deltas),
            "actual_membership_flip_rate": actual_flip,
            "random_flip_rate_median": float(np.median(flip_rates)) if flip_rates else None,
            "actual_flip_percentile": pct(actual_flip, flip_rates),
            "valid_reps": len(u_deltas),
            "invalid_reps": invalid,
            "is_285A": s == TARGET_SYMBOL,
        })
    return out


def influence_ranking(loso_rows: list[dict[str, Any]], rand_rows: list[dict[str, Any]], flip_rows: list[dict[str, Any]]) -> dict[str, Any]:
    # rank by abs update delta, then abs flow delta
    u_sorted = sorted(loso_rows, key=lambda r: abs(r["update_threshold_delta"]), reverse=True)
    f_sorted = sorted(loso_rows, key=lambda r: abs(r["flow_threshold_delta"]), reverse=True)
    rand_by = {r["symbol"]: r for r in rand_rows}
    flip_by = {r["symbol_removed"]: r for r in flip_rows}

    def rank_map(sorted_rows: list[dict], key: str) -> dict[str, int]:
        return {r["symbol"]: i for i, r in enumerate(sorted_rows, start=1)}

    u_rank = rank_map(u_sorted, "update")
    f_rank = rank_map(f_sorted, "flow")
    target = TARGET_SYMBOL
    tr = next(r for r in loso_rows if r["symbol"] == target)
    rr = rand_by[target]
    fr = flip_by[target]
    # leverage conditions
    u_pct = rr.get("actual_update_delta_percentile")
    f_pct = rr.get("actual_flow_delta_percentile")
    flip_pct_metric = max(
        fr["update_membership_flip_rate"],
        fr["flow_membership_flip_rate"],
        fr["joint_membership_flip_rate"],
    )
    cond1 = (u_pct is not None and u_pct >= 0.95) or (f_pct is not None and f_pct >= 0.95)
    cond2 = (u_rank[target] <= 3) or (f_rank[target] <= 3)
    # for cond2 with same metric as cond1
    if u_pct is not None and u_pct >= 0.95:
        cond2 = u_rank[target] <= 3
        metric_used = "update"
    elif f_pct is not None and f_pct >= 0.95:
        cond2 = f_rank[target] <= 3
        metric_used = "flow"
    else:
        metric_used = "none"
        cond2 = (u_rank[target] <= 3) or (f_rank[target] <= 3)
    cond3 = flip_pct_metric >= 0.05
    return {
        "update_influence_ranking": [
            {"rank": i, "symbol": r["symbol"], "abs_delta": abs(r["update_threshold_delta"]),
             "delta": r["update_threshold_delta"]}
            for i, r in enumerate(u_sorted[:15], start=1)
        ],
        "flow_influence_ranking": [
            {"rank": i, "symbol": r["symbol"], "abs_delta": abs(r["flow_threshold_delta"]),
             "delta": r["flow_threshold_delta"]}
            for i, r in enumerate(f_sorted[:15], start=1)
        ],
        "285A": {
            "update_rank": u_rank[target],
            "flow_rank": f_rank[target],
            "update_delta": tr["update_threshold_delta"],
            "flow_delta": tr["flow_threshold_delta"],
            "update_threshold_without": tr["update_threshold_without"],
            "flow_threshold_without": tr["flow_threshold_without"],
            "size_matched_update_percentile": u_pct,
            "size_matched_flow_percentile": f_pct,
            "update_flip_rate": fr["update_membership_flip_rate"],
            "flow_flip_rate": fr["flow_membership_flip_rate"],
            "joint_flip_rate": fr["joint_membership_flip_rate"],
            "max_flip_rate": flip_pct_metric,
            "metric_for_leverage_test": metric_used,
            "cond_size_matched_pct_ge_95": cond1,
            "cond_rank_le_3": cond2,
            "cond_flip_ge_5pct": cond3,
            "kioxia_threshold_leverage": bool(cond1 and cond2 and cond3),
        },
    }


def symbol_groups(profiles: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    heavy = [p for p in profiles if p["n_update_valid"] >= MIN_SYMBOL_SUPPORT
             and p["median_price_update_count_10s"] is not None
             and p["median_price_update_count_10s"] >= 8.0]
    low_flow = [p for p in profiles if p["n_flow_valid"] >= MIN_SYMBOL_SUPPORT
                and p["median_uptick_volume_ratio_30s"] is not None
                and p["median_uptick_volume_ratio_30s"] <= FROZEN["uptick_volume_ratio_30s_q30"]]
    heavy_set = {p["symbol"] for p in heavy}
    low_set = {p["symbol"] for p in low_flow}
    pfq_like = [p for p in profiles if p["symbol"] in heavy_set and p["symbol"] in low_set]

    def group_stats(syms: set[str], name: str) -> dict[str, Any]:
        rs = [r for r in rows if str(r["symbol"]) in syms]
        return {
            "group": name,
            "symbols": sorted(syms),
            "n_symbols": len(syms),
            "n_episodes": len(rs),
            "UPDATE_pass_rate": (sum(1 for r in rs if r["mem_UPDATE"]) / len(rs)) if rs else None,
            "first_touch_plus5_before_minus10_rate": (
                float(np.mean([1.0 if r.get("ft_plus5_vs_minus10") == "PLUS_FIRST" else 0.0
                               for r in rs if r.get("ft_plus5_vs_minus10") not in (None, "NOT_EVALUABLE")]))
                if any(r.get("ft_plus5_vs_minus10") not in (None, "NOT_EVALUABLE") for r in rs) else None
            ),
            "net_plus5_rate": (
                float(np.mean([1.0 if (r.get("best_net_pnl_bps_300s") is not None and float(r["best_net_pnl_bps_300s"]) >= 5) else 0.0
                               for r in rs if r.get("evaluable")]))
                if any(r.get("evaluable") for r in rs) else None
            ),
            "includes_285A": TARGET_SYMBOL in syms,
        }

    return {
        "UPDATE_HEAVY": group_stats(heavy_set, "UPDATE_HEAVY"),
        "LOW_UPTICK_FLOW": group_stats(low_set, "LOW_UPTICK_FLOW"),
        "PFQ_LIKE": group_stats(heavy_set & low_set, "PFQ_LIKE"),
        "definition": {
            "UPDATE_HEAVY": "median pu10 >= 8 AND n_update_valid >= 5",
            "LOW_UPTICK_FLOW": "median uptick_ratio <= frozen q30 AND n_flow_valid >= 5",
            "PFQ_LIKE": "UPDATE_HEAVY AND LOW_UPTICK_FLOW",
            "status": "DESCRIPTIVE_FIXED_DEFINITION",
        },
    }


def economic_reference(base_trades: list[dict], rev_trades: list[dict]) -> dict[str, Any]:
    def summarize(trades: list[dict], label: str) -> dict[str, Any]:
        pass_tr = [t for t in trades if t.get("integrity_status") == "PASS" or t.get("pnl_yen_100") is not None or t.get("net_pnl_yen") is not None]
        # normalize yen field
        def yen(t):
            return float(t.get("pnl_yen_100") if t.get("pnl_yen_100") is not None else t.get("net_pnl_yen") or t.get("exit_net_pnl_yen") or 0)

        def sym(t):
            return str(t.get("symbol"))

        all_pnl = sum(yen(t) for t in pass_tr)
        k_pnl = sum(yen(t) for t in pass_tr if sym(t) == TARGET_SYMBOL)
        ex_pnl = sum(yen(t) for t in pass_tr if sym(t) != TARGET_SYMBOL)
        by_trade = sorted(pass_tr, key=yen, reverse=True)
        top = by_trade[0] if by_trade else None
        ex_top1 = all_pnl - yen(top) if top else None
        day_pnl = defaultdict(float)
        for t in pass_tr:
            day_pnl[t["day"]] += yen(t)
        top_day = max(day_pnl, key=day_pnl.get) if day_pnl else None
        ex_top_day = all_pnl - day_pnl[top_day] if top_day else None
        loso = {s: sum(yen(t) for t in pass_tr if sym(t) != s) for s in sorted({sym(t) for t in pass_tr})}
        return {
            "label": label,
            "all_symbol_pnl": all_pnl,
            "285A_pnl": k_pnl,
            "ex_285A_pnl": ex_pnl,
            "top_trade_episode": top.get("episode_id") if top else None,
            "top_trade_pnl": yen(top) if top else None,
            "ex_top1_trade_pnl": ex_top1,
            "top_day": top_day,
            "ex_top1_day_pnl": ex_top_day,
            "loso_symbol_pnl": loso,
            "reference_only": True,
        }

    return {
        "baseline_PROGRESS_STRUCT": summarize(base_trades, "PFQ_UPDATE_Q70|PFQ_X_PROGRESS_STRUCT"),
        "revision_BE5_FLOOR0": summarize(rev_trades, "PFQ_UPDATE_Q70|PFQ_X_PROGRESS_BE5_FLOOR0"),
    }


def decide_verdict(
    *,
    identity_ok: bool,
    quantile_ok: bool,
    bridge_signal_ok: bool,
    influence: dict[str, Any],
    signal_full: dict[str, Any],
    signal_ex: dict[str, Any],
    loso_signal: dict[str, Any],
    groups: dict[str, Any],
) -> dict[str, Any]:
    if not identity_ok:
        return {"verdict": "E1_X8_SYMBOL_LEVERAGE_IDENTITY_MISMATCH", "next": "fix identity sources"}
    if not quantile_ok:
        return {"verdict": "E1_X8_QUANTILE_CONTRACT_UNRESOLVED", "next": "resolve quantile contract vs frozen PFQ"}
    if not bridge_signal_ok:
        return {"verdict": "E1_X8_BRIDGE_SIGNAL_IDENTITY_MISMATCH", "next": "fix Bridge V2 fixed-grid signal reproduction"}

    kiox = influence["285A"]["kioxia_threshold_leverage"]
    support_full = bool(signal_full.get("supported"))
    support_ex = bool(signal_ex.get("supported"))
    if support_full and not support_ex:
        signal_dep = "KIOXIA_DEPENDENT"
    elif support_ex:
        signal_dep = "SURVIVES_EX_KIOXIA"
    else:
        signal_dep = "NO_SUPPORT"

    spr = float(loso_signal.get("support_preserved_rate") or 0)
    symbol_rob = "BROADLY_PRESERVED" if spr >= 0.80 else "SYMBOL_FRAGILE"
    n_heavy = groups["UPDATE_HEAVY"]["n_symbols"]
    # other influencers near 285A
    top_u = influence["update_influence_ranking"][:3]
    other_strong = sum(1 for r in top_u if r["symbol"] != TARGET_SYMBOL and r["abs_delta"] > 0)

    if signal_dep == "KIOXIA_DEPENDENT":
        return {
            "verdict": "E1_X8_KIOXIA_DOMINANT_SIGNAL_DEPENDENCE",
            "signal_dependence": signal_dep,
            "symbol_robustness": symbol_rob,
            "kioxia_threshold_leverage": kiox,
            "next": "future families: prefer within-symbol normalization or universe split before cross-symbol raw quantiles",
            "pfq_revive": False,
        }
    if kiox and signal_dep == "SURVIVES_EX_KIOXIA":
        return {
            "verdict": "E1_X8_KIOXIA_THRESHOLD_LEVERAGE_SIGNAL_SURVIVES",
            "signal_dependence": signal_dep,
            "symbol_robustness": symbol_rob,
            "kioxia_threshold_leverage": kiox,
            "next": "document 285A threshold leverage; do not revive PFQ; consider regime design later",
            "pfq_revive": False,
        }
    if (not kiox or other_strong >= 1) and n_heavy >= 3 and symbol_rob == "BROADLY_PRESERVED":
        return {
            "verdict": "E1_X8_BROAD_HIGH_UPDATE_REGIME_PROXY",
            "signal_dependence": signal_dep,
            "symbol_robustness": symbol_rob,
            "kioxia_threshold_leverage": kiox,
            "next": "consider high-update vs low-update regime split in future research design",
            "pfq_revive": False,
        }
    if not kiox and abs(influence["285A"]["update_delta"]) < 1e-12 and abs(influence["285A"]["flow_delta"]) < 1e-9:
        return {
            "verdict": "E1_X8_SYMBOL_THRESHOLD_STABLE",
            "signal_dependence": signal_dep,
            "symbol_robustness": symbol_rob,
            "kioxia_threshold_leverage": kiox,
            "next": "do not attribute PFQ failure primarily to symbol threshold leverage",
            "pfq_revive": False,
        }
    return {
        "verdict": "E1_X8_SYMBOL_LEVERAGE_INSUFFICIENT_EVIDENCE",
        "signal_dependence": signal_dep,
        "symbol_robustness": symbol_rob,
        "kioxia_threshold_leverage": kiox,
        "next": "no PFQ revival; keep as descriptive audit only",
        "pfq_revive": False,
    }
