"""Standalone mechanism scoring. No joint EXIT×sizing search."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from research.pb1_complete_strategy_causal_repair_mechanism_discovery import (
    MIN_CONF_FOLDS_POSITIVE,
    MIN_FOLD_TRADES,
    MIN_TOTAL_TRADES,
    REQUIRE_DEV_POSITIVE,
    WINNER_DAMAGE_FLOOR_BPS,
)
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.precommit import EXIT_CANDIDATES, FOLDS, SIZING_CANDIDATES
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.sizing import apply_sizing
from research.pb1_v4_complete_strategy_economic_failure_decomposition.classify import classify_entry_path
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import hhmm_to_min


def _mean(xs: list[float]) -> float | None:
    return float(sum(xs) / len(xs)) if xs else None


def _mins(a: str | None, b: str | None) -> int:
    ma = hhmm_to_min(str(a or "")[:5])
    mb = hhmm_to_min(str(b or "")[:5])
    if ma is None or mb is None:
        return 10**9
    d = int(mb) - int(ma)
    if str(a) < "11:30" and str(b) >= "12:30":
        d -= 60
    return d


def annotate_class(trade: dict[str, Any], path: dict[str, Any]) -> str:
    t_mae = path.get("time_to_MAE")
    row = {
        "MFE_bps": path.get("MFE_bps") or 0.0,
        "MAE_bps": path.get("MAE_bps") or 0.0,
        "realized_gross_bps": path.get("realized_gross_bps"),
        "time_to_MAE": _mins(str(trade.get("entry_t")), str(t_mae) if t_mae else None),
        "MFE_capture_ratio": path.get("MFE_capture_ratio"),
    }
    return classify_entry_path(row)


def summarize_exit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    d_net = [float(r.get("delta_net_bps") or 0) for r in rows]
    d_gross = [float(r.get("delta_gross_bps") or 0) for r in rows]
    winners = [r for r in rows if float(r.get("v1_net_yen") or 0) > 0]
    losers = [r for r in rows if float(r.get("v1_net_yen") or 0) <= 0]
    fired = [r for r in rows if r.get("used_candidate")]
    w_delta = [float(r.get("delta_net_bps") or 0) for r in winners]
    l_delta = [float(r.get("delta_net_bps") or 0) for r in losers]
    by_fold: dict[str, dict[str, Any]] = {}
    for fold in FOLDS:
        sub = [r for r in rows if r.get("fold") == fold]
        by_fold[fold] = {
            "n": len(sub),
            "delta_net_bps": _mean([float(x.get("delta_net_bps") or 0) for x in sub]),
            "delta_gross_bps": _mean([float(x.get("delta_gross_bps") or 0) for x in sub]),
            "fired_n": sum(1 for x in sub if x.get("used_candidate")),
            "winner_damage_bps": _mean([float(x.get("delta_net_bps") or 0) for x in sub if float(x.get("v1_net_yen") or 0) > 0]),
            "loss_reduction_bps": _mean([float(x.get("delta_net_bps") or 0) for x in sub if float(x.get("v1_net_yen") or 0) <= 0]),
        }
    pos_folds = [f for f, s in by_fold.items() if int(s["n"]) >= int(MIN_FOLD_TRADES) and (s["delta_net_bps"] or 0) > 0]
    conf_pos = [f for f in ("C1_EARLY", "C1_MIDDLE", "C1_LATE") if f in pos_folds]
    top_sym = _drop_top(rows, key="symbol")
    top_day = _drop_top(rows, key="date")
    pooled = _mean(d_net)
    winner_dmg = _mean(w_delta)
    support_ok = n >= int(MIN_TOTAL_TRADES) and len(fired) >= 10
    accepted = (
        bool(REQUIRE_DEV_POSITIVE)
        and "DEV" in pos_folds
        and len(conf_pos) >= int(MIN_CONF_FOLDS_POSITIVE)
        and bool(support_ok)
        and (pooled or 0) > 0
        and (winner_dmg is not None and winner_dmg >= float(WINNER_DAMAGE_FLOOR_BPS))
        and bool(top_sym["still_positive"])
        and bool(top_day["still_positive"])
        and ( _mean(d_gross) or 0) > 0
    )
    return {
        "n": n,
        "fired_n": len(fired),
        "delta_net_bps": pooled,
        "delta_gross_bps": _mean(d_gross),
        "winner_n": len(winners),
        "winner_damage_bps": winner_dmg,
        "loser_n": len(losers),
        "loss_reduction_bps": _mean(l_delta),
        "folds": by_fold,
        "positive_folds": pos_folds,
        "dev_positive": "DEV" in pos_folds,
        "conf_positive_n": len(conf_pos),
        "top1_symbol": top_sym,
        "top1_day": top_day,
        "survives_tax": (pooled or 0) > 0,
        "accepted": bool(accepted),
        "reject_reasons": _reject_reasons(
            accepted=accepted,
            pos_folds=pos_folds,
            conf_pos=conf_pos,
            support_ok=support_ok,
            pooled=pooled,
            winner_dmg=winner_dmg,
            top_sym=top_sym,
            top_day=top_day,
            d_gross=_mean(d_gross),
        ),
    }


def _drop_top(rows: list[dict[str, Any]], *, key: str) -> dict[str, Any]:
    bag: dict[str, float] = defaultdict(float)
    for r in rows:
        bag[str(r.get(key) or "")] += float(r.get("delta_net_bps") or 0)
    if not bag:
        return {"id": None, "delta_net_bps": None, "still_positive": False}
    top = max(bag.items(), key=lambda kv: abs(kv[1]))
    rest = [r for r in rows if str(r.get(key) or "") != top[0]]
    rest_mean = _mean([float(r.get("delta_net_bps") or 0) for r in rest])
    return {"id": top[0], "contrib_sum_bps": top[1], "still_positive": (rest_mean or 0) > 0, "rest_mean_bps": rest_mean, "rest_n": len(rest)}


def _reject_reasons(**kw: Any) -> list[str]:
    if kw["accepted"]:
        return []
    out = []
    if "DEV" not in kw["pos_folds"]:
        out.append("DEV_FOLD_NOT_POSITIVE")
    if len(kw["conf_pos"]) < int(MIN_CONF_FOLDS_POSITIVE):
        out.append("CONF_FOLDS_INSUFFICIENT")
    if not kw["support_ok"]:
        out.append("SUPPORT_INSUFFICIENT")
    if (kw["pooled"] or 0) <= 0:
        out.append("POOLED_DELTA_NET_NOT_POSITIVE")
    if kw["winner_dmg"] is None or kw["winner_dmg"] < float(WINNER_DAMAGE_FLOOR_BPS):
        out.append("WINNER_DAMAGE")
    if not kw["top_sym"]["still_positive"]:
        out.append("TOP1_SYMBOL")
    if not kw["top_day"]["still_positive"]:
        out.append("TOP1_DAY")
    if (kw["d_gross"] or 0) <= 0:
        out.append("GROSS_NOT_POSITIVE")
    return out


def summarize_sizing(rows: list[dict[str, Any]], *, family: str) -> dict[str, Any]:
    n = len(rows)
    nets = [float(r.get("net_yen") or 0) for r in rows]
    notionals = [float(r.get("notional_yen") or 0) for r in rows]
    notion_sum = sum(notionals)
    port_bps = (10000.0 * sum(nets) / notion_sum) if notion_sum > 0 else None
    bpss = [float(r.get("net_bps") or 0) for r in rows if r.get("net_bps") is not None]
    abs_net = [abs(x) for x in nets]
    tot_abs = sum(abs_net) or 1.0
    by_sym: dict[str, float] = defaultdict(float)
    for r in rows:
        by_sym[str(r.get("symbol") or "")] += abs(float(r.get("net_yen") or 0))
    top_share = (max(by_sym.values()) / tot_abs) if by_sym else None
    hhi = sum((v / tot_abs) ** 2 for v in by_sym.values()) if by_sym else None
    by_fold = {}
    for fold in FOLDS:
        sub = [r for r in rows if r.get("fold") == fold]
        by_fold[fold] = {
            "n": len(sub),
            "mean_net_yen": _mean([float(x.get("net_yen") or 0) for x in sub]),
            "mean_net_bps": _mean([float(x.get("net_bps") or 0) for x in sub if x.get("net_bps") is not None]),
        }
    return {
        "family": family,
        "n": n,
        "mean_net_yen": _mean(nets),
        "mean_net_bps": _mean(bpss),
        "portfolio_net_bps": port_bps,
        "median_notional": float(median(notionals)) if notionals else None,
        "max_notional": max(notionals) if notionals else None,
        "top1_symbol_abs_yen_share": top_share,
        "hhi_abs_yen": hhi,
        "folds": by_fold,
    }


def sizing_accept(s0: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    """Sizing cannot mint bps edge. Supported only as concentration repair vs S0."""
    s0_share = s0.get("top1_symbol_abs_yen_share")
    c_share = cand.get("top1_symbol_abs_yen_share")
    bps0 = s0.get("portfolio_net_bps")
    bpsc = cand.get("portfolio_net_bps")
    reduced = s0_share is not None and c_share is not None and float(c_share) <= 0.75 * float(s0_share)
    bps_not_worse = bps0 is None or bpsc is None or float(bpsc) >= float(bps0) - 1e-9
    # Sizing-only is never sufficient for FOUND; it is a supported family if concentration falls.
    accepted_family = bool(reduced and bps_not_worse and cand.get("family") != "S0_FIXED_100")
    return {
        "accepted_as_concentration_repair": accepted_family,
        "concentration_reduced_25pct": reduced,
        "bps_not_worse_vs_s0": bps_not_worse,
        "sufficient_for_found_verdict": False,
        "reject_reasons": [] if accepted_family else (["NOT_VS_S0"] if cand.get("family") == "S0_FIXED_100" else ["CONCENTRATION_OR_BPS"]),
    }


def decide(*, exit_summ: dict[str, dict[str, Any]], sizing_summ: dict[str, dict[str, Any]], asf_identifiable_frac: float) -> dict[str, Any]:
    supported_exit = [k for k, v in exit_summ.items() if v.get("accepted")]
    rejected_exit = {k: v.get("reject_reasons") for k, v in exit_summ.items() if not v.get("accepted")}
    s0 = sizing_summ.get("S0_FIXED_100") or {}
    supported_sizing = []
    rejected_sizing = {}
    for fam in SIZING_CANDIDATES:
        if fam == "S0_FIXED_100":
            continue
        gate = sizing_accept(s0, sizing_summ.get(fam) or {})
        if gate["accepted_as_concentration_repair"]:
            supported_sizing.append(fam)
        else:
            rejected_sizing[fam] = gate["reject_reasons"]
    found = bool(supported_exit)
    asf_status = "ASF_EARLY_REPAIR_NOT_IDENTIFIABLE" if asf_identifiable_frac < 0.5 else "ASF_LEVEL_RESTORED"
    if "ASF_FIRST_STRUCTURAL_KILL" not in supported_exit and asf_identifiable_frac < 0.5:
        rejected_exit.setdefault("ASF_FIRST_STRUCTURAL_KILL", [])
        if "ASF_EARLY_REPAIR_NOT_IDENTIFIABLE" not in rejected_exit["ASF_FIRST_STRUCTURAL_KILL"]:
            rejected_exit["ASF_FIRST_STRUCTURAL_KILL"] = list(rejected_exit["ASF_FIRST_STRUCTURAL_KILL"]) + ["ASF_EARLY_REPAIR_NOT_IDENTIFIABLE"]
    return {
        "found": found,
        "supported_repair_families": {
            "A_EXIT_PROFIT_RETENTION": [k for k in supported_exit],
            "B_EARLY_FAILURE_ABORT": [k for k in supported_exit],
            "C_POSITION_SIZING": supported_sizing,
        },
        "unsupported_repair_families": {
            "A_EXIT_PROFIT_RETENTION": rejected_exit,
            "C_POSITION_SIZING": rejected_sizing,
            "ASF": asf_status,
        },
        "EXIT_CANDIDATES": list(EXIT_CANDIDATES),
        "note": "B uses the same post-entry EXIT/ABORT clocks as A; not reverse-imported to ENTRY.",
        "joint_search": False,
        "V5_CREATED": False,
    }
