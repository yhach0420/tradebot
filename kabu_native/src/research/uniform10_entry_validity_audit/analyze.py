"""Validity metrics. Frozen C model. No retune."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_10min_opportunity.grids import tod_family
from research.anchor_timing_robustness.metrics import maxdd, trade_stats
from research.edge_decay_rca.analyze import tag_entry_kind
from research.passive_fill_corrected_rebase import DEVELOPMENT_DAYS, LATE_DAY, POST_DAYS
from research.passive_fill_corrected_rebase.analyze import period_pack
from research.passive_fill_itayose_reconciliation.analyze import concentration, headline
from research.anchor_timing_robustness.grid import hm_label
from research.uniform10_entry_rebuild import UNIFORM10
from research.uniform10_entry_rebuild.analyze import attach_rebuild_scores, ranking_block
from research.uniform10_entry_rebuild.model import TARGET
from research.uniform10_entry_validity_audit import GROUP_A, GROUP_B, GROUP_C
from research.uniform10_entry_validity_audit.classify import ITAYOSE_SOURCES

_U10 = {hm_label(h, m) for h, m in UNIFORM10}

TOPK = (1, 3, 5, 10)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _pnl(t: dict[str, Any]) -> float:
    return float(t.get("pnl_yen_100") or 0.0)


def key_row(r: dict[str, Any]) -> tuple[str, str, str]:
    return (str(r.get("date") or ""), str(r.get("symbol") or "").replace(".T", ""), str(r.get("anchor") or r.get("anchor_time") or ""))


def trade_panel_key(t: dict[str, Any]) -> tuple[str, str, str]:
    """Map a fill onto the UNIFORM10 panel key using fill clock, not occupancy label."""
    day = str(t.get("date") or "")
    sym = str(t.get("symbol") or "").replace(".T", "")
    iso = str(t.get("fill_time_iso") or "")
    hm = None
    if "T" in iso:
        hm = iso.split("T", 1)[1][:5]
    if hm in _U10:
        return (day, sym, hm)
    return (day, sym, str(t.get("anchor_time") or t.get("anchor") or ""))


def daily_pnl_block(trades: list[dict[str, Any]], all_days: list[str] | None = None) -> dict[str, Any]:
    by: dict[str, list] = defaultdict(list)
    for t in trades:
        by[str(t.get("date"))].append(t)
    keys = list(all_days) if all_days else sorted(by)
    rows = []
    for d in keys:
        xs = by.get(d) or []
        st = headline(xs) if xs else {"trades": 0, "pnl": 0.0, "PF": None, "maxDD": 0.0}
        rows.append(
            {
                "date": d,
                "trade_count": st.get("trades") if xs else 0,
                "PnL": st.get("pnl") if xs else 0.0,
                "PF": st.get("PF") if xs else None,
                "maxDD": st.get("maxDD") if xs else 0.0,
            }
        )
    pnls = [float(r["PnL"] or 0.0) for r in rows]
    pos = sum(1 for p in pnls if p > 1e-9)
    neg = sum(1 for p in pnls if p < -1e-9)
    flat = len(pnls) - pos - neg
    return {
        "days": rows,
        "positive_days": pos,
        "negative_days": neg,
        "flat_days": flat,
        "positive_day_rate": (pos / len(pnls)) if pnls else None,
        "median_daily_pnl": float(np.median(pnls)) if pnls else None,
        "mean_daily_pnl": float(np.mean(pnls)) if pnls else None,
    }


def exclude_pack(trades: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(_pnl(t) for t in trades)
    ordered = sorted(trades, key=_pnl, reverse=True)
    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[str(t.get("date"))] += _pnl(t)
        by_sym[str(t.get("symbol"))] += _pnl(t)
    day_ord = sorted(by_day.items(), key=lambda kv: kv[1], reverse=True)
    sym_ord = sorted(by_sym.items(), key=lambda kv: kv[1], reverse=True)

    def drop_n(n: int) -> list[dict[str, Any]]:
        drop = {id(t) for t in ordered[:n]}
        return [t for t in trades if id(t) not in drop]

    def drop_days(n: int) -> list[dict[str, Any]]:
        bad = {d for d, _p in day_ord[:n]}
        return [t for t in trades if str(t.get("date")) not in bad]

    def drop_syms(n: int) -> list[dict[str, Any]]:
        bad = {s for s, _p in sym_ord[:n]}
        return [t for t in trades if str(t.get("symbol")) not in bad]

    def pack(xs: list[dict[str, Any]], label: str) -> dict[str, Any]:
        st = headline(xs)
        return {
            "label": label,
            "trades": st.get("trades"),
            "PnL": st.get("pnl"),
            "PF": st.get("PF"),
            "maxDD": st.get("maxDD"),
        }

    ex285 = [t for t in trades if str(t.get("symbol")) != "285A"]
    top1_share = (sum(_pnl(t) for t in ordered[:1]) / total) if abs(total) > 1e-12 else None
    winners = sum(_pnl(t) for t in trades if _pnl(t) > 0)
    return {
        "net_pnl": total,
        "winner_contribution": winners,
        "NET_PROFIT_DEPENDS_ON_FEW_WINNERS": bool(winners > abs(total) + 1e-9 and total > 0),
        "top1_trade_share": top1_share,
        "concentration": concentration(trades),
        "ex_top1_trade": pack(drop_n(1), "ex_top1_trade"),
        "ex_top3_trades": pack(drop_n(3), "ex_top3_trades"),
        "ex_top5_trades": pack(drop_n(5), "ex_top5_trades"),
        "ex_top10_trades": pack(drop_n(10), "ex_top10_trades"),
        "ex_best_day": pack(drop_days(1), "ex_best_day"),
        "ex_top3_days": pack(drop_days(3), "ex_top3_days"),
        "ex_top_symbol": pack(drop_syms(1), "ex_top_symbol"),
        "ex_top3_symbols": pack(drop_syms(3), "ex_top3_symbols"),
        "ex_285A": pack(ex285, "ex_285A"),
    }


def session_pack(trades: list[dict[str, Any]]) -> dict[str, Any]:
    tagged = tag_entry_kind(trades)
    am = [t for t in tagged if str(t.get("session")) == "AM"]
    pm = [t for t in tagged if str(t.get("session")) == "PM"]
    tod = {}
    for fam in ("OPEN_EARLY", "NORMAL_SESSION", "SESSION_TAIL"):
        tod[fam] = headline([t for t in tagged if tod_family(str(t.get("anchor_time") or "")) == fam])
    first = [t for t in tagged if t.get("entry_kind") == "FIRST_ENTRY"]
    reent = [t for t in tagged if t.get("entry_kind") == "REENTRY"]
    return {
        "headline": headline(tagged),
        "AM": headline(am),
        "PM": headline(pm),
        "tod": tod,
        "first_entry": headline(first),
        "re_entry": headline(reent),
        "n_first": len(first),
        "n_reentry": len(reent),
    }


def join_panel(
    panel: list[dict[str, Any]],
    klass: list[dict[str, Any]],
    b_trades: list[dict[str, Any]],
    c_trades: list[dict[str, Any]],
    model: dict[str, Any],
) -> list[dict[str, Any]]:
    kmap = {key_row(r): r for r in klass}
    b_pnl: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    c_pnl: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in b_trades:
        b_pnl[trade_panel_key(t)].append(t)
    for t in c_trades:
        c_pnl[trade_panel_key(t)].append(t)
    raw_panel = []
    for r in panel:
        rec = dict(r)
        rec["executable_flag_raw"] = rec.get("executable_flag")
        raw_panel.append(rec)
    scored = attach_rebuild_scores(raw_panel, model)
    by: dict[tuple[str, str], list] = defaultdict(list)
    for r in scored:
        by[(str(r.get("date")), str(r.get("anchor")))].append(r)
    out = []
    for grp in by.values():
        ranked = sorted(
            [r for r in grp if _f(r.get("rebuild_score")) is not None],
            key=lambda r: (-float(r["rebuild_score"]), str(r.get("symbol") or "")),
        )
        n = len(ranked)
        for i, r in enumerate(ranked):
            rec = dict(r)
            rec["rebuild_rank0"] = i
            rec["in_top1"] = i < 1
            rec["in_top3"] = i < 3
            rec["in_top5"] = i < 5
            rec["in_top10"] = i < 10
            rec["cohort_n"] = n
            k = key_row(r)
            extra = kmap.get(k) or {}
            rec["group"] = extra.get("group") or "UNCLASSIFIED"
            for fld in (
                "executable_at_t0",
                "becomes_executable_within_1s",
                "opening_transition_within_1s",
                "opened_at_t0",
                "t0_price_source",
                "itayose_like_t0_mid",
                "t0_mid",
                "t0_bid",
                "t0_ask",
                "t0_CurrentPrice",
                "t0_OpeningPrice",
                "t0_CalcPrice",
                "t0_board_state",
                "t0_AskSign",
                "t0_locked_or_crossed",
                "t0_special",
                "mid_src_executable",
                "mid_src_state",
                "mid_src_locked",
            ):
                rec[fld] = extra.get(fld)
            bt = b_pnl.get(k) or []
            ct = c_pnl.get(k) or []
            rec["b_fill"] = bool(bt)
            rec["c_fill"] = bool(ct)
            rec["b_pnl"] = sum(_pnl(t) for t in bt)
            rec["c_pnl"] = sum(_pnl(t) for t in ct)
            rec["b_trade_n"] = len(bt)
            rec["c_trade_n"] = len(ct)
            out.append(rec)
        for r in grp:
            if _f(r.get("rebuild_score")) is not None:
                continue
            rec = dict(r)
            rec["rebuild_rank0"] = None
            rec["in_top1"] = False
            rec["in_top3"] = False
            rec["in_top5"] = False
            rec["in_top10"] = False
            k = key_row(r)
            extra = kmap.get(k) or {}
            rec["group"] = extra.get("group") or "UNCLASSIFIED"
            rec["t0_price_source"] = extra.get("t0_price_source")
            rec["itayose_like_t0_mid"] = extra.get("itayose_like_t0_mid")
            rec["b_fill"] = bool(b_pnl.get(k))
            rec["c_fill"] = bool(c_pnl.get(k))
            rec["b_pnl"] = sum(_pnl(t) for t in (b_pnl.get(k) or []))
            rec["c_pnl"] = sum(_pnl(t) for t in (c_pnl.get(k) or []))
            rec["b_trade_n"] = len(b_pnl.get(k) or [])
            rec["c_trade_n"] = len(c_pnl.get(k) or [])
            out.append(rec)
    return out


def _target_stats(xs: list[dict[str, Any]]) -> dict[str, Any]:
    ys = [_f(r.get(TARGET)) for r in xs]
    ys = [y for y in ys if y is not None]
    if not ys:
        return {"n_target": 0, "mean": None, "median": None, "positive_rate": None}
    return {
        "n_target": len(ys),
        "mean": float(np.mean(ys)),
        "median": float(np.median(ys)),
        "positive_rate": float(sum(1 for y in ys if y > 0) / len(ys)),
    }


def fills_by_group(klass: list[dict[str, Any]], trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    kmap = {key_row(r): str(r.get("group") or "UNCLASSIFIED") for r in klass}
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        g = kmap.get(trade_panel_key(t)) or "UNCLASSIFIED"
        out[g].append(t)
    return out


def tradability_table(
    joined: list[dict[str, Any]],
    b_by_g: dict[str, list] | None = None,
    c_by_g: dict[str, list] | None = None,
) -> list[dict[str, Any]]:
    n_all = len(joined) or 1
    rows = []
    for g in (GROUP_A, GROUP_B, GROUP_C, "UNCLASSIFIED"):
        xs = [r for r in joined if r.get("group") == g]
        tgt = _target_stats(xs)
        n = len(xs)
        if b_by_g is not None:
            bt = b_by_g.get(g) or []
            b_fill_n, b_trade_n, b_pnl_s = len(bt), len(bt), sum(_pnl(t) for t in bt)
        else:
            b_fills = [r for r in xs if r.get("b_fill")]
            b_fill_n = len(b_fills)
            b_trade_n = sum(int(r.get("b_trade_n") or 0) for r in xs)
            b_pnl_s = sum(float(r.get("b_pnl") or 0.0) for r in xs)
        if c_by_g is not None:
            ct = c_by_g.get(g) or []
            c_fill_n, c_trade_n, c_pnl_s = len(ct), len(ct), sum(_pnl(t) for t in ct)
        else:
            c_fills = [r for r in xs if r.get("c_fill")]
            c_fill_n = len(c_fills)
            c_trade_n = sum(int(r.get("c_trade_n") or 0) for r in xs)
            c_pnl_s = sum(float(r.get("c_pnl") or 0.0) for r in xs)
        rows.append(
            {
                "group": g,
                "row_count": n,
                "share_of_all_rows": n / n_all,
                "Top10_inclusion": sum(1 for r in xs if r.get("in_top10")),
                "Top5_inclusion": sum(1 for r in xs if r.get("in_top5")),
                "Top3_inclusion": sum(1 for r in xs if r.get("in_top3")),
                "Top1_inclusion": sum(1 for r in xs if r.get("in_top1")),
                "PRIMARY_TARGET_mean": tgt["mean"],
                "PRIMARY_TARGET_median": tgt["median"],
                "positive_target_rate": tgt["positive_rate"],
                "B_Passive_Fill_count": b_fill_n,
                "B_Fill_rate": (b_fill_n / n) if n else None,
                "B_portfolio_trade_count": b_trade_n,
                "B_PnL_contribution": b_pnl_s,
                "C_Passive_Fill_count": c_fill_n,
                "C_Fill_rate": (c_fill_n / n) if n else None,
                "C_portfolio_trade_count": c_trade_n,
                "C_PnL_contribution": c_pnl_s,
            }
        )
    return rows


def ranking_contribution(joined: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in joined if _f(r.get("rebuild_score")) is not None and _f(r.get(TARGET)) is not None]
    n = len(scored) or 1
    pop = {g: sum(1 for r in scored if r.get("group") == g) / n for g in (GROUP_A, GROUP_B, GROUP_C)}
    out: dict[str, Any] = {"population_share": pop}
    for k in (1, 3, 5, 10):
        key = f"in_top{k}"
        top = [r for r in scored if r.get(key)]
        nt = len(top) or 1
        block = {}
        for g in (GROUP_A, GROUP_B, GROUP_C):
            xs = [r for r in top if r.get("group") == g]
            ys = [_f(r.get(TARGET)) for r in xs]
            ys = [y for y in ys if y is not None]
            block[g] = {
                "n": len(xs),
                "share_of_topk": len(xs) / nt,
                "mean_target": float(np.mean(ys)) if ys else None,
                "population_share": pop.get(g),
                "overrep": (len(xs) / nt) / pop[g] if pop.get(g) else None,
            }
        out[f"Top{k}"] = block
    top1_c = (out["Top1"].get(GROUP_C) or {}).get("share_of_topk") or 0.0
    top3_c = (out["Top3"].get(GROUP_C) or {}).get("share_of_topk") or 0.0
    pop_c = pop.get(GROUP_C) or 0.0
    contaminated = bool(
        (top1_c >= 0.25 and top1_c > pop_c * 1.5)
        or (top3_c >= 0.20 and top3_c > pop_c * 1.5)
        or (top1_c >= 0.40)
    )
    out["NON_EXECUTABLE_RANKING_CONTAMINATION"] = contaminated
    return out


def ranking_set(rows: list[dict[str, Any]], model: dict[str, Any]) -> dict[str, Any]:
    scored = attach_rebuild_scores(rows, model)
    blk = ranking_block(scored, "rebuild_score")
    # pooled extras
    extra = {}
    by: dict[tuple[str, str], list] = defaultdict(list)
    for r in scored:
        if _f(r.get(TARGET)) is None or _f(r.get("rebuild_score")) is None:
            continue
        by[(str(r.get("date")), str(r.get("anchor")))].append(r)
    for name, k in (("ALL", None), ("Top10", 10), ("Top5", 5), ("Top3", 3), ("Top1", 1)):
        ys = []
        for grp in by.values():
            ordered = sorted(grp, key=lambda x: (-float(x["rebuild_score"]), str(x.get("symbol"))))
            pick = ordered if k is None else ordered[:k]
            ys.extend(_f(x.get(TARGET)) for x in pick)
        ys = [y for y in ys if y is not None]
        extra[name] = {
            **(blk.get(name) or {}),
            "pooled_mean": float(np.mean(ys)) if ys else None,
            "pooled_median": float(np.median(ys)) if ys else None,
            "pooled_positive_rate": (sum(1 for y in ys if y > 0) / len(ys)) if ys else None,
            "pooled_n": len(ys),
        }
    all_m = extra["ALL"].get("mean_target")
    for name in ("Top10", "Top5", "Top3", "Top1"):
        m = extra[name].get("mean_target")
        extra[name]["uplift_vs_ALL"] = (m - all_m) if (m is not None and all_m is not None) else None
    extra["UP_MOVER_RANKING_SUPPORTED"] = blk.get("UP_MOVER_RANKING_SUPPORTED")
    extra["n_rows"] = len(rows)
    extra["n_scored"] = sum(1 for r in scored if _f(r.get("rebuild_score")) is not None)
    return extra


def target_base_audit(joined: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    itayose_target_n = 0
    finite_n = 0
    for g in (GROUP_A, GROUP_B, GROUP_C):
        xs = [r for r in joined if r.get("group") == g]
        by_src: dict[str, list] = defaultdict(list)
        for r in xs:
            by_src[str(r.get("t0_price_source") or "UNKNOWN")].append(r)
        for src, grp in sorted(by_src.items(), key=lambda kv: -len(kv[1])):
            tgt = _target_stats(grp)
            exe_share = sum(1 for r in grp if r.get("executable_at_t0")) / len(grp) if grp else None
            locked = sum(1 for r in grp if r.get("t0_locked_or_crossed") or r.get("mid_src_locked")) / len(grp) if grp else None
            rows.append(
                {
                    "group": g,
                    "t0_price_source": src,
                    "n": len(grp),
                    "PRIMARY_TARGET_mean": tgt["mean"],
                    "PRIMARY_TARGET_median": tgt["median"],
                    "positive_target_rate": tgt["positive_rate"],
                    "executable_at_t0_share": exe_share,
                    "locked_or_crossed_share": locked,
                    "itayose_like": src in ITAYOSE_SOURCES,
                }
            )
        for r in xs:
            y = _f(r.get(TARGET))
            if y is None:
                continue
            finite_n += 1
            if r.get("itayose_like_t0_mid") and g in {GROUP_B, GROUP_C}:
                itayose_target_n += 1
    contaminated = bool(itayose_target_n > 0)
    return {
        "rows": rows,
        "TARGET_ITAYOSE_PRICE_CONTAMINATION": contaminated,
        "itayose_like_finite_target_rows_BC": itayose_target_n,
        "finite_target_rows": finite_n,
        "note": (
            "_mid_at skips SpecialQuote/qty-special only. Itayose Buy1=Sell1 with AskSign=0102 "
            "and SpecialQuote=null is a valid mid base. FORWARD_MID_RETURN_600S can then be "
            "indicative_t0 → continuous mid_t0+600."
        ),
    }


def _flag_raw(r: dict[str, Any]) -> Optional[float]:
    v = _f(r.get("executable_flag_raw"))
    if v is not None:
        return 1.0 if v >= 0.5 else 0.0
    if r.get("executable_at_t0") is True:
        return 1.0
    if r.get("executable_at_t0") is False:
        return 0.0
    return None


def executable_flag_audit(joined: list[dict[str, Any]]) -> dict[str, Any]:
    def corr_flag(xs: list[dict[str, Any]]) -> Optional[float]:
        a = np.asarray([_flag_raw(r) for r in xs], dtype=float)
        b = np.asarray([_f(r.get(TARGET)) for r in xs], dtype=float)
        ok = np.isfinite(a) & np.isfinite(b)
        if int(ok.sum()) < 30 or float(np.std(a[ok])) <= 1e-12 or float(np.std(b[ok])) <= 1e-12:
            return None
        return float(np.corrcoef(a[ok], b[ok])[0, 1])

    by_flag = {}
    for flag in (0.0, 1.0):
        xs = [r for r in joined if _flag_raw(r) == flag]
        by_flag[str(int(flag))] = {
            "n": len(xs),
            **_target_stats(xs),
            "Top1": sum(1 for r in xs if r.get("in_top1")),
            "Top3": sum(1 for r in xs if r.get("in_top3")),
            "GROUP_C_share": (sum(1 for r in xs if r.get("group") == GROUP_C) / len(xs)) if xs else None,
            "itayose_like_share": (sum(1 for r in xs if r.get("itayose_like_t0_mid")) / len(xs)) if xs else None,
            "OPEN_EARLY_share": (
                sum(1 for r in xs if tod_family(str(r.get("anchor") or "")) == "OPEN_EARLY") / len(xs)
            )
            if xs
            else None,
            "missing_mid_ret_180s": (sum(1 for r in xs if _f(r.get("mid_ret_180s")) is None) / len(xs)) if xs else None,
        }
    by_group = {}
    for g in (GROUP_A, GROUP_B, GROUP_C):
        xs = [r for r in joined if r.get("group") == g]
        by_group[g] = {"n": len(xs), "corr_flag_vs_target": corr_flag(xs), **_target_stats(xs)}
    xs_a = [r for r in joined if r.get("group") == GROUP_A]
    raw_a = [_flag_raw(r) for r in xs_a]
    raw_a = [v for v in raw_a if v is not None]
    flag_std_a = float(np.std(raw_a)) if raw_a else 0.0
    overall = corr_flag(joined)
    top1_c = [r for r in joined if r.get("in_top1") and r.get("group") == GROUP_C]
    top1 = [r for r in joined if r.get("in_top1")]
    share_c = (len(top1_c) / len(top1)) if top1 else 0.0
    early0 = by_flag["0"].get("OPEN_EARLY_share") or 0.0
    miss0 = by_flag["0"].get("missing_mid_ret_180s") or 0.0
    itay0 = by_flag["0"].get("itayose_like_share") or 0.0
    role = "F_OTHER"
    if itay0 >= 0.5 or share_c >= 0.25:
        role = "B_ITAYOSE_PRICE_ARTIFACT"
    elif flag_std_a <= 1e-9 and (overall is not None and overall < 0):
        role = "C_OPENING_DELAY_PROXY"
    elif early0 >= 0.4:
        role = "D_TIME_OF_DAY_PROXY"
    elif miss0 >= 0.3:
        role = "E_MISSING_DATA_PROXY"
    genuine = bool(
        flag_std_a > 1e-6
        and by_group[GROUP_A]["corr_flag_vs_target"] is not None
        and abs(float(by_group[GROUP_A]["corr_flag_vs_target"])) >= 0.02
        and share_c < 0.20
    )
    invalid = bool(share_c >= 0.20 or (itay0 >= 0.3 and overall is not None and overall < 0))
    return {
        "sign_locked": -1,
        "corr_flag_vs_target_all": overall,
        "corr_flag_vs_target_GROUP_A": by_group[GROUP_A]["corr_flag_vs_target"],
        "executable_flag_std_GROUP_A": flag_std_a,
        "Top1_GROUP_C_share": share_c,
        "by_flag": by_flag,
        "by_group": by_group,
        "role": role,
        "GENUINE_FUTURE_PRICE_PREDICTOR": genuine,
        "MODEL_DESIGN_INVALID_TRADABILITY_FEATURE": invalid,
        "note": (
            "sign=-1 ranks non-executable names higher after CS-z. GROUP C over-representation "
            "in Top1 with itayose-like t0 mids means the flag is a tradability/itayose artifact, "
            "not a within-executable price predictor. Feature left unchanged (audit only)."
        ),
    }


def mfe_audit(c_trades: list[dict[str, Any]]) -> dict[str, Any]:
    raw = [t for t in c_trades if t.get("mfe_yen_100") is not None]
    null_n = sum(1 for t in c_trades if t.get("mfe_yen_100") is None)
    raw_vals = [float(t["mfe_yen_100"]) for t in raw]
    neg_n = sum(1 for v in raw_vals if v < 0)
    # standard MFE floors at 0 (entry is an attainable excursion of 0)
    corrected = [max(0.0, v) for v in raw_vals]
    st = trade_stats(c_trades)
    return {
        "formula_mfe": (
            "path_metrics.mfe = max_i (bid_i / fill_price - 1) * 10000 over executable bids "
            "in [fill_time, exit_time]; yen = mfe_bps * fill_price / 10000 * LOT_QTY"
        ),
        "formula_mae": "min_i of the same bid return path (most adverse, typically negative)",
        "entry_price_baseline": "fill_price (limit_price under corrected Passive Fill)",
        "price_side": "executable bid (not mid) for MFE/MAE path; mid stored only as diagnostic",
        "event_window": "[fill_time, exit_time] (IMBALANCE exits can be <30s; not a 600s window)",
        "missing_handling": "path ok=False or no valid bid → mfe_yen_100=null and excluded from mean",
        "SESSION_CLOSE_handling": "if exit_time is session end, path stops there; last ret is exec_session_close",
        "aggregation": "mean of mfe_yen_100 among trades where value is not None (trade_stats)",
        "includes_zero_at_entry": False,
        "floors_at_zero": False,
        "headline_MFE_from_trade_stats": st.get("MFE"),
        "n_trades": len(c_trades),
        "n_mfe_non_null": len(raw),
        "n_mfe_null": null_n,
        "n_mfe_negative": neg_n,
        "mean_raw_mfe_yen": float(np.mean(raw_vals)) if raw_vals else None,
        "mean_standard_mfe_yen_floor0": float(np.mean(corrected)) if corrected else None,
        "MFE_METRIC_VALID": False,
        "defect": (
            "Standard MFE is max(0, max_t (price_t - entry)). Implementation uses max of bid "
            "returns after fill without inserting 0 at t=fill. Paths that never trade above "
            "the fill print a negative MFE. Headline C MFE is therefore a metric defect, "
            "not a strategy property. Ledger/PnL/Fill unchanged."
        ),
        "strategy_impact": "NONE — metric-only; do not retune",
    }


def avsb(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
    n_days: int,
    n_anchors_a: int,
    n_anchors_b: int,
    all_days: list[str] | None = None,
) -> dict[str, Any]:
    ha, hb = headline(a), headline(b)
    da, db = daily_pnl_block(a, all_days), daily_pnl_block(b, all_days)
    ca, cb = concentration(a), concentration(b)
    n_a, n_b = len(a), len(b)
    pnl_a = float(ha.get("pnl") or 0.0)
    pnl_b = float(hb.get("pnl") or 0.0)

    def _pf(st: dict[str, Any]) -> Optional[float]:
        v = st.get("PF")
        if v is None or v == "Infinity":
            return None if v is None else float("inf")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "same_eligible_days": True,
        "same_corrected_fill": True,
        "same_ENTRY": True,
        "same_EXIT": True,
        "same_CAP": True,
        "same_reentry": True,
        "same_symbol_universe": True,
        "sole_diff": "CLOCK_GRID irregular vs UNIFORM10",
        "A_trades": n_a,
        "B_trades": n_b,
        "trades_delta": n_b - n_a,
        "A_PnL": pnl_a,
        "B_PnL": pnl_b,
        "PnL_delta": pnl_b - pnl_a,
        "A_PF": ha.get("PF"),
        "B_PF": hb.get("PF"),
        "PF_delta": ((_pf(hb) or 0) - (_pf(ha) or 0)) if (_pf(ha) is not None and _pf(hb) is not None) else None,
        "A_maxDD": ha.get("maxDD"),
        "B_maxDD": hb.get("maxDD"),
        "maxDD_delta": float(hb.get("maxDD") or 0.0) - float(ha.get("maxDD") or 0.0),
        "A_avg_trade": ha.get("avg_trade"),
        "B_avg_trade": hb.get("avg_trade"),
        "A_median_trade": ha.get("median_trade"),
        "B_median_trade": hb.get("median_trade"),
        "A_PnL_per_fill": (pnl_a / n_a) if n_a else None,
        "B_PnL_per_fill": (pnl_b / n_b) if n_b else None,
        "A_PnL_per_anchor": pnl_a / (n_days * n_anchors_a) if n_days and n_anchors_a else None,
        "B_PnL_per_anchor": pnl_b / (n_days * n_anchors_b) if n_days and n_anchors_b else None,
        "A_positive_day_rate": da.get("positive_day_rate"),
        "B_positive_day_rate": db.get("positive_day_rate"),
        "A_median_daily_pnl": da.get("median_daily_pnl"),
        "B_median_daily_pnl": db.get("median_daily_pnl"),
        "A_top1_trade_share": ca.get("top1_trade_share"),
        "B_top1_trade_share": cb.get("top1_trade_share"),
        "A_top1_symbol_share": ca.get("top1_symbol_share"),
        "B_top1_symbol_share": cb.get("top1_symbol_share"),
        "A_top1_day_share": ca.get("top1_day_share"),
        "B_top1_day_share": cb.get("top1_day_share"),
        "risk_adjusted_improved": bool(
            pnl_b > pnl_a
            and (_pf(hb) or 0) > (_pf(ha) or 0)
            and float(hb.get("maxDD") or -1e18) > float(ha.get("maxDD") or -1e18)
        ),
        "note": "maxDD_delta > 0 means shallower drawdown (maxDD is negative).",
    }


def b_period(b_trades: list[dict[str, Any]]) -> dict[str, Any]:
    dev = period_pack(b_trades, DEVELOPMENT_DAYS, include_0827=False)
    post7 = period_pack(b_trades, POST_DAYS, include_0827=False)
    post_plus = period_pack(b_trades, POST_DAYS + (LATE_DAY,), include_0827=True)
    d_pnl = float(dev.get("pnl") or 0.0)
    p_pnl = float(post7.get("pnl") or 0.0)
    sign_flip = bool((d_pnl > 0) != (p_pnl > 0) and abs(d_pnl) > 1e-9 and abs(p_pnl) > 1e-9)
    return {
        "DEV10": dev,
        "POST7": post7,
        "POST7_PLUS_20260827": post_plus,
        "sign_completely_reversed": sign_flip,
    }
