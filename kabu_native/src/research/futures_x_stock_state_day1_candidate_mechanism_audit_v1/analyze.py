"""Audit the frozen Day1 candidate only. No new search. No ENTRY/EXIT."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1.engine import load_futures_series, ret_asof
from research.futures_x_stock_state_day1_candidate_mechanism_audit_v1 import (
    ACTIVITY_FAMILY,
    ANALYSIS_ID,
    CLASS_A,
    CLASS_B,
    CLASS_C,
    CLASS_D,
    CLASS_E,
    DAY1_SECONDARY_ROWS,
    DAY2_ANALYSIS_ID,
    FROZEN_CLOCKS,
    FROZEN_CONTEXT,
    FROZEN_CONTEXT_BUCKET,
    FROZEN_DIRECTION,
    FROZEN_HORIZON,
    FROZEN_SELECTOR,
    KIND,
    NEXT_DAY2,
    PARENT_ID,
    TRADING_DATE,
    VERDICT_REVERSAL,
    VERDICT_SUPPORTED,
    VERDICT_WINNER,
)
from research.futures_x_stock_state_day1_candidate_mechanism_audit_v1.isolation import NATIVE
from research.futures_x_stock_state_day1_candidate_mechanism_audit_v1.stats import (
    dist,
    finite,
    mean,
    median,
    monotonic_mid,
    pos_neg,
    spearman,
    tercile_three,
)
from research.futures_x_stock_state_interaction_day1_v1.engine import (
    agreement_180,
    build_interaction_table,
)
from research.new_causal_information_acquisition_v1.launcher import live_order_counts
from research.new_causal_information_acquisition_v1.writer import day_layout

JST = ZoneInfo("Asia/Tokyo")
HORIZON_ORDER = ("1m", "3m", "5m", "10m")
DROP_CLOCKS_1 = ("10:20",)
DROP_CLOCKS_2 = ("10:20", "09:20")


def _metric(row: dict[str, Any], horizon: str, name: str) -> Optional[float]:
    pack = row.get(horizon) or {}
    v = pack.get(name)
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _basket(rows: list[dict[str, Any]], horizon: str, name: str) -> dict[str, Any]:
    xs = [_metric(r, horizon, name) for r in rows]
    ys = finite(xs)
    p, n = pos_neg(ys)
    return {
        "n": len(ys),
        "mean": mean(ys),
        "median": median(ys),
        "positive_n": p,
        "negative_n": n,
        "values": ys,
        "by_symbol": [
            {"symbol": r.get("symbol"), "value": _metric(r, horizon, name)}
            for r in rows
            if _metric(r, horizon, name) is not None
        ],
    }


def _split_clock(clock: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    members = list(clock.get("members") or [])
    ranked = []
    for m in members:
        sel = (m.get("selectors") or {}).get(FROZEN_SELECTOR)
        ranked.append({**m, "value": sel})
    split = tercile_three(ranked)
    if split is None:
        raise RuntimeError(f"tercile split failed at {clock.get('clock')}")
    return split


def _path_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mid = {h: mean([_metric(r, h, "MID_RETURN_BPS") for r in rows]) for h in HORIZON_ORDER}
    inc = {
        "0_to_1m": mid["1m"],
        "1_to_3m": (None if mid["3m"] is None or mid["1m"] is None else float(mid["3m"]) - float(mid["1m"])),
        "3_to_5m": (None if mid["5m"] is None or mid["3m"] is None else float(mid["5m"]) - float(mid["3m"])),
        "5_to_10m": (None if mid["10m"] is None or mid["5m"] is None else float(mid["10m"]) - float(mid["5m"])),
    }
    return {"cumulative_mid": mid, "incremental_mid": inc}


def _fwd_ret(series: Any, t0: float, sec: int) -> Optional[float]:
    px0, _ = series.at(t0)
    px1, _ = series.at(t0 + float(sec))
    if px0 is None or px1 is None or float(px0) == 0:
        return None
    return 10000.0 * (float(px1) - float(px0)) / float(px0)


def _agreement_at(nk: Any, tx: Any, t: float) -> tuple[Optional[str], Optional[float], Optional[float]]:
    nk_r, _ = ret_asof(nk, t, 180)
    tx_r, _ = ret_asof(tx, t, 180)
    return agreement_180(nk_r, tx_r), nk_r, tx_r


def _spearman_matrix(members: list[dict[str, Any]]) -> dict[str, dict[str, Optional[float]]]:
    vals: dict[str, list[Optional[float]]] = {s: [] for s in ACTIVITY_FAMILY}
    for m in members:
        sel = m.get("selectors") or {}
        for s in ACTIVITY_FAMILY:
            vals[s].append(sel.get(s))
    out: dict[str, dict[str, Optional[float]]] = {}
    for a in ACTIVITY_FAMILY:
        out[a] = {}
        for b in ACTIVITY_FAMILY:
            if a == b:
                out[a][b] = 1.0
            else:
                out[a][b] = spearman(vals[a], vals[b])
    return out


def _mean_matrix(mats: list[dict[str, dict[str, Optional[float]]]]) -> dict[str, dict[str, Optional[float]]]:
    out: dict[str, dict[str, Optional[float]]] = {a: {} for a in ACTIVITY_FAMILY}
    for a in ACTIVITY_FAMILY:
        for b in ACTIVITY_FAMILY:
            out[a][b] = mean([m[a][b] for m in mats])
    return out


def _high_corr(mat: dict[str, dict[str, Optional[float]]], thresh: float = 0.70) -> bool:
    pairs = []
    names = list(ACTIVITY_FAMILY)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            v = mat[a][b]
            if v is not None:
                pairs.append(abs(float(v)))
    if not pairs:
        return False
    return mean(pairs) is not None and float(mean(pairs)) >= thresh


def run_mechanism_audit(*, native_root: Optional[Path] = None, trading_date: str = TRADING_DATE) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    table = build_interaction_table(native_root=root, day=day)
    by_label = {c["clock"]: c for c in table["clocks"]}
    missing = [lab for lab in FROZEN_CLOCKS if lab not in by_label]
    if missing:
        raise RuntimeError(f"missing frozen clocks: {missing}")
    clocks = [by_label[lab] for lab in FROZEN_CLOCKS]
    bad = [c["clock"] for c in clocks if c.get("agreement_180") != FROZEN_CONTEXT_BUCKET]
    if bad:
        raise RuntimeError(f"frozen clocks not BOTH_DOWN: {bad}")

    layout = day_layout(day, native_root=root)
    nk = load_futures_series(layout["nk225mini_jsonl"])
    tx = load_futures_series(layout["topix_jsonl"])

    per_clock: list[dict[str, Any]] = []
    top_long_obs: list[dict[str, Any]] = []
    top_mid_obs: list[float] = []
    bot_mid_obs: list[float] = []
    mid_mid_obs: list[float] = []
    top_pre: list[float] = []
    mid_pre: list[float] = []
    bot_pre: list[float] = []
    spearman_mid: dict[str, Optional[float]] = {}
    spearman_long: dict[str, Optional[float]] = {}
    corr_mats: list[dict[str, dict[str, Optional[float]]]] = []
    path_top: list[dict[str, Any]] = []
    path_mid: list[dict[str, Any]] = []
    path_bot: list[dict[str, Any]] = []

    for c in clocks:
        split = _split_clock(c)
        top, middle, bottom = split["TOP"], split["MIDDLE"], split["BOTTOM"]
        t_mid = _basket(top, FROZEN_HORIZON, "MID_RETURN_BPS")
        t_long = _basket(top, FROZEN_HORIZON, "LONG_EXEC_MARKOUT_BPS")
        b_mid = _basket(bottom, FROZEN_HORIZON, "MID_RETURN_BPS")
        b_long = _basket(bottom, FROZEN_HORIZON, "LONG_EXEC_MARKOUT_BPS")
        m_mid = _basket(middle, FROZEN_HORIZON, "MID_RETURN_BPS")
        m_long = _basket(middle, FROZEN_HORIZON, "LONG_EXEC_MARKOUT_BPS")
        tb_mid = None if t_mid["mean"] is None or b_mid["mean"] is None else float(t_mid["mean"]) - float(b_mid["mean"])
        tb_long = None if t_long["mean"] is None or b_long["mean"] is None else float(t_long["mean"]) - float(b_long["mean"])
        te = float(c["t_epoch"])
        agr1, nk1, tx1 = _agreement_at(nk, tx, te + 60.0)
        agr3, nk3, tx3 = _agreement_at(nk, tx, te + 180.0)
        nk_f1 = _fwd_ret(nk, te, 60)
        tx_f1 = _fwd_ret(tx, te, 60)
        nk_f3 = _fwd_ret(nk, te, 180)
        tx_f3 = _fwd_ret(tx, te, 180)
        rev1 = agr1 == "BOTH_UP" or (nk_f1 is not None and tx_f1 is not None and nk_f1 > 0 and tx_f1 > 0)
        rev3 = agr3 == "BOTH_UP" or (nk_f3 is not None and tx_f3 is not None and nk_f3 > 0 and tx_f3 > 0)
        cont1 = agr1 == "BOTH_DOWN"
        cont3 = agr3 == "BOTH_DOWN"
        members = list(c.get("members") or [])
        sel_v = [(m.get("selectors") or {}).get(FROZEN_SELECTOR) for m in members]
        mid10 = [_metric(m, "10m", "MID_RETURN_BPS") for m in members]
        long10 = [_metric(m, "10m", "LONG_EXEC_MARKOUT_BPS") for m in members]
        spearman_mid[c["clock"]] = spearman(sel_v, mid10)
        spearman_long[c["clock"]] = spearman(sel_v, long10)
        corr_mats.append(_spearman_matrix(members))
        p_top = _path_block(top)
        p_mid = _path_block(middle)
        p_bot = _path_block(bottom)
        path_top.append({"clock": c["clock"], **p_top})
        path_mid.append({"clock": c["clock"], **p_mid})
        path_bot.append({"clock": c["clock"], **p_bot})
        for row in t_long["by_symbol"]:
            top_long_obs.append({"clock": c["clock"], "symbol": row["symbol"], "long": row["value"]})
        top_mid_obs.extend(t_mid["values"])
        bot_mid_obs.extend(b_mid["values"])
        mid_mid_obs.extend(m_mid["values"])
        for r in top:
            if r.get("past_ret_180") is not None:
                top_pre.append(10000.0 * float(r["past_ret_180"]))
        for r in middle:
            if r.get("past_ret_180") is not None:
                mid_pre.append(10000.0 * float(r["past_ret_180"]))
        for r in bottom:
            if r.get("past_ret_180") is not None:
                bot_pre.append(10000.0 * float(r["past_ret_180"]))
        per_clock.append(
            {
                "clock": c["clock"],
                "block": c.get("block"),
                "nk_ret_180": c.get("nk_ret_180"),
                "tx_ret_180": c.get("tx_ret_180"),
                "TOP": {"MID": t_mid, "LONG": t_long},
                "MIDDLE": {"MID": m_mid, "LONG": m_long},
                "BOTTOM": {"MID": b_mid, "LONG": b_long},
                "TOP_BOTTOM_MID": tb_mid,
                "TOP_BOTTOM_LONG": tb_long,
                "monotonic_mid": monotonic_mid(b_mid["mean"], m_mid["mean"], t_mid["mean"]),
                "monotonic_long": monotonic_mid(b_long["mean"], m_long["mean"], t_long["mean"]),
                "spearman_mid": spearman_mid[c["clock"]],
                "spearman_long": spearman_long[c["clock"]],
                "noncausal": {
                    "agreement_t_plus_1m": agr1,
                    "agreement_t_plus_3m": agr3,
                    "nk_ret_180_t_plus_1m": nk1,
                    "tx_ret_180_t_plus_1m": tx1,
                    "nk_ret_180_t_plus_3m": nk3,
                    "tx_ret_180_t_plus_3m": tx3,
                    "nk_fwd_1m_bps": nk_f1,
                    "tx_fwd_1m_bps": tx_f1,
                    "nk_fwd_3m_bps": nk_f3,
                    "tx_fwd_3m_bps": tx_f3,
                    "both_down_continued_1m": cont1,
                    "both_down_continued_3m": cont3,
                    "reversal_1m": rev1,
                    "reversal_3m": rev3,
                    "label_1m": "CONTINUED" if cont1 else ("REVERSED" if rev1 else "RELEASED"),
                    "label_3m": "CONTINUED" if cont3 else ("REVERSED" if rev3 else "RELEASED"),
                },
                "path": {"TOP": p_top, "MIDDLE": p_mid, "BOTTOM": p_bot},
                "pre_t_mid_bps": {
                    "TOP": mean([10000.0 * float(r["past_ret_180"]) for r in top if r.get("past_ret_180") is not None]),
                    "MIDDLE": mean([10000.0 * float(r["past_ret_180"]) for r in middle if r.get("past_ret_180") is not None]),
                    "BOTTOM": mean([10000.0 * float(r["past_ret_180"]) for r in bottom if r.get("past_ret_180") is not None]),
                },
            }
        )

    pooled_top_long = [o["long"] for o in top_long_obs]
    long_dist = dist(pooled_top_long)
    by_sym: dict[str, list[float]] = {}
    for o in top_long_obs:
        by_sym.setdefault(str(o["symbol"]), []).append(float(o["long"]))
    n_pool = max(1, len(pooled_top_long))
    contrib = []
    for sym, vs in by_sym.items():
        s = sum(vs)
        contrib.append(
            {
                "symbol": sym,
                "n": len(vs),
                "sum": s,
                "mean": mean(vs),
                "contrib_to_pooled_mean": s / float(n_pool),
            }
        )
    contrib.sort(key=lambda r: -float(r["sum"]))
    winners = [r for r in contrib if float(r["sum"]) > 0]
    top1 = winners[:1]
    top2 = winners[:2]
    top3 = winners[:3]
    drop_set = {
        1: {r["symbol"] for r in top1},
        2: {r["symbol"] for r in top2},
        3: {r["symbol"] for r in top3},
    }
    drop_long = {}
    for k, drop in drop_set.items():
        kept = [o["long"] for o in top_long_obs if o["symbol"] not in drop]
        drop_long[k] = {"mean": mean(kept), "median": median(kept), "n": len(kept), "dropped": sorted(drop)}

    clock_long_means = [c["TOP"]["LONG"]["mean"] for c in per_clock]
    clock_long_medians = [c["TOP"]["LONG"]["median"] for c in per_clock]

    def _clock_abs(drop: tuple[str, ...]) -> dict[str, Any]:
        remain = [c for c in per_clock if c["clock"] not in drop]
        longs = [c["TOP"]["LONG"]["mean"] for c in remain]
        pooled = [o["long"] for o in top_long_obs if o["clock"] not in drop]
        mids = [c["TOP_BOTTOM_MID"] for c in remain]
        return {
            "dropped": list(drop),
            "remain_n": len(remain),
            "top_long_clock_mean": mean(longs),
            "top_long_clock_median": median(longs),
            "top_long_pooled_mean": mean(pooled),
            "top_long_pooled_median": median(pooled),
            "top_bottom_mid": mean(mids),
        }

    drop_c1 = _clock_abs(DROP_CLOCKS_1)
    drop_c2 = _clock_abs(DROP_CLOCKS_2)

    pooled_tercile = {
        "BOTTOM": {"MID": mean(bot_mid_obs), "LONG": mean([c["BOTTOM"]["LONG"]["mean"] for c in per_clock])},
        "MIDDLE": {"MID": mean(mid_mid_obs), "LONG": mean([c["MIDDLE"]["LONG"]["mean"] for c in per_clock])},
        "TOP": {"MID": mean(top_mid_obs), "LONG": mean(clock_long_means)},
    }
    pooled_mono_mid = monotonic_mid(pooled_tercile["BOTTOM"]["MID"], pooled_tercile["MIDDLE"]["MID"], pooled_tercile["TOP"]["MID"])
    pooled_mono_long = monotonic_mid(pooled_tercile["BOTTOM"]["LONG"], pooled_tercile["MIDDLE"]["LONG"], pooled_tercile["TOP"]["LONG"])
    clock_mono_ok = sum(1 for c in per_clock if c["monotonic_mid"] in ("BOTTOM < MIDDLE < TOP", "TOP > MIDDLE > BOTTOM"))

    mean_corr = _mean_matrix(corr_mats)
    family_high = _high_corr(mean_corr, 0.70)
    fam_vs_primary = [mean_corr[FROZEN_SELECTOR][s] for s in ACTIVITY_FAMILY if s != FROZEN_SELECTOR]
    family_coherent = bool(fam_vs_primary) and all(v is not None and float(v) > 0 for v in fam_vs_primary)

    path_pooled = {
        "TOP": {
            "cumulative_mid": {h: mean([p["cumulative_mid"][h] for p in path_top]) for h in HORIZON_ORDER},
            "incremental_mid": {
                k: mean([p["incremental_mid"][k] for p in path_top])
                for k in ("0_to_1m", "1_to_3m", "3_to_5m", "5_to_10m")
            },
        },
        "MIDDLE": {
            "cumulative_mid": {h: mean([p["cumulative_mid"][h] for p in path_mid]) for h in HORIZON_ORDER},
            "incremental_mid": {
                k: mean([p["incremental_mid"][k] for p in path_mid])
                for k in ("0_to_1m", "1_to_3m", "3_to_5m", "5_to_10m")
            },
        },
        "BOTTOM": {
            "cumulative_mid": {h: mean([p["cumulative_mid"][h] for p in path_bot]) for h in HORIZON_ORDER},
            "incremental_mid": {
                k: mean([p["incremental_mid"][k] for p in path_bot])
                for k in ("0_to_1m", "1_to_3m", "3_to_5m", "5_to_10m")
            },
        },
    }

    pos_clocks = [c for c in per_clock if c["TOP_BOTTOM_MID"] is not None and float(c["TOP_BOTTOM_MID"]) > 0]
    continued_clocks = [c for c in per_clock if c["noncausal"]["both_down_continued_1m"] and c["noncausal"]["both_down_continued_3m"]]
    continued_spread = mean([c["TOP_BOTTOM_MID"] for c in continued_clocks])
    reversed_clocks = [c for c in per_clock if c["noncausal"]["reversal_1m"] or c["noncausal"]["reversal_3m"]]
    reversed_spread = mean([c["TOP_BOTTOM_MID"] for c in reversed_clocks])
    continued_long = mean([c["TOP"]["LONG"]["mean"] for c in continued_clocks])
    reversed_long = mean([c["TOP"]["LONG"]["mean"] for c in reversed_clocks])
    long_pos_clocks = [
        c for c in per_clock if c["TOP"]["LONG"]["mean"] is not None and float(c["TOP"]["LONG"]["mean"]) > 0
    ]
    long_pos_without_rev = [
        c for c in long_pos_clocks if not (c["noncausal"]["reversal_1m"] or c["noncausal"]["reversal_3m"])
    ]
    reversal_dependent = bool(long_pos_clocks) and len(long_pos_without_rev) == 0

    drop1_mean = drop_long[1]["mean"]
    drop3_mean = drop_long[3]["mean"]
    winner_driven = bool(
        (long_dist["mean"] is not None and float(long_dist["mean"]) > 0)
        and (
            (drop1_mean is not None and float(drop1_mean) <= 0)
            or (drop3_mean is not None and float(drop3_mean) <= 0)
        )
    )

    top_pre_m = mean(top_pre)
    bot_pre_m = mean(bot_pre)
    mid_pre_m = mean(mid_pre)
    if top_pre_m is not None and bot_pre_m is not None and top_pre_m > 0 and top_pre_m > bot_pre_m + 2.0:
        pre_class = "continuation"
    elif top_pre_m is not None and top_pre_m < 0:
        pre_class = "resilience/reversal"
    elif top_pre_m is not None and bot_pre_m is not None and abs(float(top_pre_m) - float(bot_pre_m)) < 2.0:
        pre_class = "participation"
    else:
        pre_class = "mixed"

    if reversal_dependent:
        mech = CLASS_C
        verdict = VERDICT_REVERSAL
    elif winner_driven:
        mech = CLASS_D
        verdict = VERDICT_WINNER
    elif pre_class == "continuation" and pooled_mono_mid in ("BOTTOM < MIDDLE < TOP", "TOP > MIDDLE > BOTTOM"):
        mech = CLASS_B
        verdict = VERDICT_SUPPORTED
    elif pre_class in ("resilience/reversal", "participation", "mixed") and pooled_mono_mid in (
        "BOTTOM < MIDDLE < TOP",
        "TOP > MIDDLE > BOTTOM",
    ):
        mech = CLASS_A if pre_class != "continuation" else CLASS_B
        verdict = VERDICT_SUPPORTED
    else:
        mech = CLASS_E
        verdict = VERDICT_SUPPORTED if pooled_mono_mid in ("BOTTOM < MIDDLE < TOP", "TOP > MIDDLE > BOTTOM") else VERDICT_WINNER
        if mech == CLASS_E and winner_driven:
            verdict = VERDICT_WINNER

    # If relative monotone exists but absolute is winner/clock-tail driven, prefer D.
    if mech in (CLASS_A, CLASS_B) and winner_driven:
        mech = CLASS_D
        verdict = VERDICT_WINNER
    if reversal_dependent:
        mech = CLASS_C
        verdict = VERDICT_REVERSAL

    sentence = _mechanism_sentence(
        mech,
        per_clock=per_clock,
        path=path_pooled,
        pre_class=pre_class,
        top_pre=top_pre_m,
        bot_pre=bot_pre_m,
        family_high=family_high,
        family_coherent=family_coherent,
        long_dist=long_dist,
        drop_long=drop_long,
        reversal_dependent=reversal_dependent,
        continued_spread=continued_spread,
        reversed_spread=reversed_spread,
    )

    orders = live_order_counts()
    latent = "MULTIPLE_ROWS_NOT_INDEPENDENT" if family_high else "SELECTORS_PARTIALLY_DISTINCT"
    if family_coherent:
        latent = latent + "+ACTIVITY_FAMILY_COHERENCE"

    day2_manifest = {
        "analysis_id": DAY2_ANALYSIS_ID,
        "parent_audit": ANALYSIS_ID,
        "trading_date_day1": TRADING_DATE,
        "primary": {
            "context": FROZEN_CONTEXT,
            "context_family": "AGREEMENT_180S",
            "context_bucket": "BOTH_DOWN",
            "context_definition": "NK_RET_180S < 0 AND TOPIX_RET_180S < 0",
            "selector": FROZEN_SELECTOR,
            "rank": "TOP16 / BOTTOM16",
            "middle": "excluded from selection; diagnostic only",
            "horizon": FROZEN_HORIZON,
            "direction": FROZEN_DIRECTION,
        },
        "substitutions_allowed": False,
        "secondary_rows": [
            {"selector": a, "family": b, "bucket": c, "horizon": d, "use": "ACTIVITY_FAMILY_COHERENCE_ONLY"}
            for a, b, c, d in DAY1_SECONDARY_ROWS
        ],
        "secondary_may_not_rescue_primary_fail": True,
        "gates": {
            "C1_BOTH_DOWN_clock_n_ge_3": True,
            "C2_TOP_BOTTOM_MID_gt_5bps": True,
            "C3_incremental_lift_vs_BASE_A_gt_5bps": True,
            "C4_TOP_BOTTOM_LONG_spread_gt_0": True,
            "C5_TOP_LONG_absolute_mean_gt_0": True,
            "C6_drop_top_clock_TOP_BOTTOM_MID_gt_0": True,
            "C7_drop_top_symbol_TOP_BOTTOM_MID_gt_0": True,
        },
        "strong_confirmation": {"TOP_LONG_median_gt_0": True},
        "entry_built": False,
        "exit_built": False,
        "ten_m_is_diagnostic_not_exit": True,
        "mechanism_class_day1": mech,
        "day1_verdict": verdict,
    }

    answers = {
        "1_frozen_context_clocks": list(FROZEN_CLOCKS),
        "2_TOP_MIDDLE_BOTTOM_per_clock_path": {
            "TOP": path_top,
            "MIDDLE": path_mid,
            "BOTTOM": path_bot,
            "pooled": path_pooled,
        },
        "3_tercile_monotonicity": {
            "per_clock": [{k: c[k] for k in ("clock", "monotonic_mid", "monotonic_long", "TOP_BOTTOM_MID")} | {
                "BOTTOM_MID": c["BOTTOM"]["MID"]["mean"],
                "MIDDLE_MID": c["MIDDLE"]["MID"]["mean"],
                "TOP_MID": c["TOP"]["MID"]["mean"],
                "BOTTOM_LONG": c["BOTTOM"]["LONG"]["mean"],
                "MIDDLE_LONG": c["MIDDLE"]["LONG"]["mean"],
                "TOP_LONG": c["TOP"]["LONG"]["mean"],
            } for c in per_clock],
            "pooled": pooled_tercile,
            "pooled_monotonic_mid": pooled_mono_mid,
            "pooled_monotonic_long": pooled_mono_long,
            "clocks_monotone_mid_n": clock_mono_ok,
        },
        "4_per_clock_spearman_MID": spearman_mid,
        "5_per_clock_spearman_LONG": spearman_long,
        "6_pooled_TOP_LONG_N": long_dist["n"],
        "7_pooled_LONG_mean": long_dist["mean"],
        "8_median": long_dist["median"],
        "9_trimmed_mean": long_dist["trimmed_mean_10"],
        "10_win_rate": long_dist["win_rate"],
        "11_top1_winner_contribution": top1,
        "12_top2_contribution": top2,
        "13_top3_contribution": top3,
        "14_drop_top1_LONG_mean_median": drop_long[1],
        "15_drop_top2_LONG_mean_median": drop_long[2],
        "16_drop_top3_LONG_mean_median": drop_long[3],
        "17_drop_top_clock_absolute_LONG": drop_c1,
        "18_drop_top2_clocks_absolute_LONG": drop_c2,
        "19_activity_selector_correlations": {
            "per_clock": {c["clock"]: corr_mats[i] for i, c in enumerate(per_clock)},
            "mean": mean_corr,
        },
        "20_one_latent_family": latent,
        "21_TOP_pre_T_MID_state": top_pre_m,
        "22_BOTTOM_pre_T_MID_state": bot_pre_m,
        "23_continuation_or_reversal": pre_class,
        "24_future_futures_reversal_dependence": {
            "reversal_dependent": reversal_dependent,
            "CURRENT_CONTEXT_NOT_SUFFICIENT": bool(reversal_dependent),
            "continued_clock_n": len(continued_clocks),
            "continued_clocks": [c["clock"] for c in continued_clocks],
            "continued_top_bottom_mid": continued_spread,
            "continued_top_long_mean": continued_long,
            "reversed_clock_n": len(reversed_clocks),
            "reversed_clocks": [c["clock"] for c in reversed_clocks],
            "reversed_top_bottom_mid": reversed_spread,
            "reversed_top_long_mean": reversed_long,
            "absolute_long_positive_clocks": [c["clock"] for c in long_pos_clocks],
            "absolute_long_positive_without_reversal": [c["clock"] for c in long_pos_without_rev],
            "note": "NONCAUSAL_MECHANISM_DIAGNOSTIC",
        },
        "25_mechanism_classification": mech,
        "26_exact_mechanism_sentence": sentence,
        "27_Day2_primary_manifest_frozen": True,
        "28_Day2_substitutions_allowed": False,
        "29_ENTRY_built": False,
        "30_EXIT_built": False,
        "31_Runtime_changed": False,
        "32_Paper_changed": False,
        "33_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "34_VERDICT": verdict,
        "35_NEXT": NEXT_DAY2,
    }

    decision = {
        "VERDICT": verdict,
        "NEXT": NEXT_DAY2,
        "mechanism_class": mech,
        "ENTRY_built": False,
        "EXIT_built": False,
        "Runtime_changed": False,
        "Paper_changed": False,
        "submit_cancel_live": answers["33_submit_cancel_live"],
        "Day2_substitutions_allowed": False,
        "CURRENT_CONTEXT_NOT_SUFFICIENT": bool(reversal_dependent),
        "MULTIPLE_ROWS_NOT_INDEPENDENT": bool(family_high),
        "ACTIVITY_FAMILY_COHERENCE": bool(family_coherent),
        "NONCAUSAL_MECHANISM_DIAGNOSTIC": True,
    }

    return {
        "analysis_id": ANALYSIS_ID,
        "parent_id": PARENT_ID,
        "kind": KIND,
        "trading_date": day,
        "built_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "frozen": {
            "context": FROZEN_CONTEXT,
            "selector": FROZEN_SELECTOR,
            "horizon": FROZEN_HORIZON,
            "direction": FROZEN_DIRECTION,
            "clocks": list(FROZEN_CLOCKS),
        },
        "future_leakage_n": table["future_leakage_n"],
        "per_clock": per_clock,
        "pooled_path": path_pooled,
        "long_distribution": long_dist,
        "winner_contrib": contrib,
        "drop_winners": drop_long,
        "drop_clocks": {"top1": drop_c1, "top2": drop_c2},
        "activity_corr_mean": mean_corr,
        "day2_manifest": day2_manifest,
        "answers": answers,
        "decision": decision,
        "clock_long_means": clock_long_means,
        "clock_long_medians": clock_long_medians,
        "middle_pre_t": mid_pre_m,
        "symbols": table["symbols"],
    }


def _mechanism_sentence(
    mech: str,
    *,
    per_clock: list[dict[str, Any]],
    path: dict[str, Any],
    pre_class: str,
    top_pre: Optional[float],
    bot_pre: Optional[float],
    family_high: bool,
    family_coherent: bool,
    long_dist: dict[str, Any],
    drop_long: dict[int, dict[str, Any]],
    reversal_dependent: bool,
    continued_spread: Optional[float],
    reversed_spread: Optional[float],
) -> str:
    inc = path["TOP"]["incremental_mid"]
    fam = "Activity proxies are one latent family." if family_high else "Activity proxies are positively related but not a single collapsed factor."
    if family_coherent:
        fam += " Same-direction ACTIVITY_FAMILY_COHERENCE holds."
    pre = f"Pre-T 180s MID TOP={top_pre} BOTTOM={bot_pre} ({pre_class})."
    path_s = (
        f"TOP incremental MID 0→1m={inc['0_to_1m']}, 1→3m={inc['1_to_3m']}, "
        f"3→5m={inc['3_to_5m']}, 5→10m={inc['5_to_10m']}."
    )
    win = (
        f"Pooled TOP LONG mean={long_dist['mean']} median={long_dist['median']} "
        f"win_rate={long_dist['win_rate']}; drop-top3 mean={drop_long[3]['mean']}."
    )
    rev = (
        f"NONCAUSAL: continued BOTH_DOWN TOP-BOTTOM={continued_spread}; "
        f"post-T reversal clocks TOP-BOTTOM={reversed_spread}."
    )
    if mech == CLASS_C:
        return (
            f"The Day1 absolute LONG edge is FUTURE_REVERSAL_DEPENDENT. "
            f"TOP LONG mean is positive only on clocks where NK/TOPIX AGREEMENT_180S "
            f"has already flipped by T+3m (NONCAUSAL_MECHANISM_DIAGNOSTIC). "
            f"T-time BOTH_DOWN is therefore CURRENT_CONTEXT_NOT_SUFFICIENT for ENTRY. "
            f"{rev} {pre} {path_s} {fam} {win}"
        )
    if mech == CLASS_D:
        return (
            f"Cross-sectional activity rank still splits TOP vs BOTTOM, but absolute LONG "
            f"is RIGHT_TAIL_WINNER_DRIVEN. {win} {pre} {path_s} {rev} {fam}"
        )
    if mech == CLASS_B:
        return (
            f"RISK_OFF_ACTIVITY_CONTINUATION: high-activity names were already relatively "
            f"strong before T and that gap continues after BOTH_DOWN. {pre} {path_s} {fam} {win}"
        )
    if mech == CLASS_A:
        return (
            f"RISK_OFF_ACTIVITY_RESILIENCE: under frozen BOTH_DOWN, high-activity names "
            f"are not simply riding a prior up-move; they hold up versus quiet names. "
            f"{pre} {path_s} {fam} {win} {rev}"
        )
    return (
        f"MECHANISM_UNRESOLVED on Day1. {pre} {path_s} {rev} {fam} {win}"
    )
