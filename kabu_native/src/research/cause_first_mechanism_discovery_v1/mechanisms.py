"""Precommitted causal-chain hypotheses. Discovery evaluates; Confirmation is a one-shot sign check."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

import numpy as np

from research.cause_first_mechanism_discovery_v1 import MIN_DAY_N, MIN_DAY_STABILITY, PRIMARY_CLOCK, PRIMARY_HORIZON_MIN

BPS_KEY = "x0_h15_bps"


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _rows(events: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("clock") == PRIMARY_CLOCK and _finite(e.get(BPS_KEY)) and pred(e)]


def _day_means(rows: list[dict[str, Any]]) -> dict[str, float]:
    bucket: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        bucket[str(r["date"])].append(float(r[BPS_KEY]))
    return {d: float(np.mean(vs)) for d, vs in bucket.items() if vs}


def _share(rows: list[dict[str, Any]], key: str, out_name: str) -> dict[str, Any]:
    by: dict[str, float] = defaultdict(float)
    tot = 0.0
    for r in rows:
        v = float(r[BPS_KEY])
        if v > 0:
            by[str(r.get(key) or "")] += v
            tot += v
    if tot <= 0 or not by:
        return {f"top_{out_name}": None, f"top_{out_name}_share_of_positive_bps": None}
    top = max(by.items(), key=lambda kv: kv[1])
    return {f"top_{out_name}": top[0], f"top_{out_name}_share_of_positive_bps": float(top[1] / tot)}


def contrast(*, treat: list[dict[str, Any]], ctrl: list[dict[str, Any]], hypothesized_diff_sign: int) -> dict[str, Any]:
    tb = [float(r[BPS_KEY]) for r in treat]
    cb = [float(r[BPS_KEY]) for r in ctrl]
    t_days = _day_means(treat)
    c_days = _day_means(ctrl)
    both = sorted(set(t_days) & set(c_days))
    if both:
        wins = sum(1 for d in both if t_days[d] > c_days[d])
        day_n = len(both)
        stability = (wins / day_n) if day_n else None
        mean_diff_days = float(np.mean([t_days[d] - c_days[d] for d in both]))
    else:
        t_pos = sum(1 for v in t_days.values() if v > 0)
        stability = (t_pos / len(t_days)) if t_days else None
        day_n = len(t_days)
        mean_diff_days = (float(np.mean(list(t_days.values()))) - float(np.mean(list(c_days.values())))) if t_days and c_days else None
    mean_t = float(np.mean(tb)) if tb else None
    mean_c = float(np.mean(cb)) if cb else None
    diff = (mean_t - mean_c) if mean_t is not None and mean_c is not None else None
    sign = 0 if diff is None else (1 if diff > 0 else (-1 if diff < 0 else 0))
    x1_t = [float(r["x1_h15_bps"]) for r in treat if _finite(r.get("x1_h15_bps"))]
    return {
        "treat_n": len(treat),
        "ctrl_n": len(ctrl),
        "treat_day_n": len(t_days),
        "ctrl_day_n": len(c_days),
        "paired_day_n": len(both),
        "day_n_for_gate": int(day_n or 0),
        "mean_treat_x0_h15_bps": mean_t,
        "mean_ctrl_x0_h15_bps": mean_c,
        "mean_diff_x0_h15_bps": diff,
        "mean_diff_day_bps": mean_diff_days,
        "hit_rate_treat": float(np.mean([x > 0 for x in tb])) if tb else None,
        "hit_rate_ctrl": float(np.mean([x > 0 for x in cb])) if cb else None,
        "day_stability": stability,
        "mean_treat_x1_h15_bps": float(np.mean(x1_t)) if x1_t else None,
        "observed_diff_sign": sign,
        "hypothesized_diff_sign": int(hypothesized_diff_sign),
        "sign_matches_hypothesis": sign == int(hypothesized_diff_sign) and sign != 0,
        **_share(treat, "symbol", "symbol"),
        **_share(treat, "sector", "sector"),
        "execution_claim": "signal_mechanism_evidence_not_spread_proof",
        "entry_only_success_declared": False,
    }


def _gate(disc: dict[str, Any], conf: dict[str, Any] | None) -> tuple[str, list[str]]:
    reasons = []
    if not disc.get("sign_matches_hypothesis"):
        reasons.append("discovery_sign_mismatch")
    if int(disc.get("day_n_for_gate") or 0) < int(MIN_DAY_N):
        reasons.append("discovery_day_n_below_floor")
    stab = disc.get("day_stability")
    if stab is None or float(stab) < float(MIN_DAY_STABILITY):
        reasons.append("discovery_day_stability_below_floor")
    if disc.get("mean_diff_x0_h15_bps") is None:
        reasons.append("discovery_diff_undefined")
    if conf is not None and not conf.get("sign_matches_hypothesis"):
        reasons.append("confirmation_sign_mismatch")
    if reasons:
        return "REJECTED", reasons
    return "SURVIVING", ["discovery_sign_stable_and_confirmation_same_sign"]


def _robust(events: list[dict[str, Any]], pred_t, pred_c, sign: int) -> list[dict[str, Any]]:
    out = []
    for clock in ("09:15", "09:30", "10:00", "13:00"):
        treat = [e for e in events if e.get("clock") == clock and _finite(e.get(BPS_KEY)) and pred_t(e)]
        ctrl = [e for e in events if e.get("clock") == clock and _finite(e.get(BPS_KEY)) and pred_c(e)]
        got = contrast(treat=treat, ctrl=ctrl, hypothesized_diff_sign=sign)
        got["clock"] = clock
        out.append(got)
    return out


def _specs() -> list[dict[str, Any]]:
    return [
        {"id": "M1_market_5m_breadth_continuation", "sign": 1, "treat": lambda e: bool(e.get("market_strong")), "ctrl": lambda e: bool(e.get("market_weak")), "meta": {"layer": "market", "hypothesis": "Strong 5m up-ratio (>=0.60) is followed by higher equal-weight X0 15m continuation than weak tape (<=0.40).", "chain": "market_state -> subsequent_cross_section_drift"}},
        {"id": "M2_sector_adds_given_strong_market", "sign": 1, "treat": lambda e: bool(e.get("market_strong")) and bool(e.get("sector_strong")), "ctrl": lambda e: bool(e.get("market_strong")) and bool(e.get("sector_weak")), "meta": {"layer": "sector", "hypothesis": "Given a strong market, stocks in a strong sector outperform stocks in a weak sector over the next 15m.", "chain": "market_strong -> sector_strong -> stock_continuation"}},
        {"id": "M3_stock_rs_continuation_in_strong_market", "sign": 1, "treat": lambda e: bool(e.get("market_strong")) and bool(e.get("rs_high")), "ctrl": lambda e: bool(e.get("market_strong")) and (not bool(e.get("rs_high"))) and _finite(e.get("rs_5m")), "meta": {"layer": "stock_relative", "hypothesis": "In a strong market, positive 5m relative strength continues over the next 15m versus negative RS.", "chain": "market_strong -> stock_rs -> continuation"}},
        {"id": "M4_pullback_vs_chase_in_strong_market", "sign": 1, "treat": lambda e: bool(e.get("market_strong")) and bool(e.get("pullback")), "ctrl": lambda e: bool(e.get("market_strong")) and bool(e.get("chase")), "meta": {"layer": "local_technical", "hypothesis": "In a strong market, 15m-up / 5m-down pullbacks beat 15m-up / 5m-up chase over the next 15m.", "chain": "market_strong -> pullback -> continuation"}},
        {"id": "M5_vwap_reclaim_not_weak_market", "sign": 1, "treat": lambda e: (not bool(e.get("market_weak"))) and bool(e.get("vwap_reclaim")), "ctrl": lambda e: (not bool(e.get("market_weak"))) and (not bool(e.get("above_vwap"))), "meta": {"layer": "local_technical", "hypothesis": "VWAP reclaim in a non-weak tape beats names still below VWAP over the next 15m.", "chain": "market_not_weak -> vwap_reclaim -> continuation"}},
        {"id": "M6_breakout_in_neutral_tape", "sign": 1, "treat": lambda e: bool(e.get("market_neutral")) and bool(e.get("breakout20")), "ctrl": lambda e: bool(e.get("market_neutral")) and (not bool(e.get("breakout20"))), "meta": {"layer": "local_technical", "hypothesis": "In a neutral tape, 20-bar high breakout outperforms non-breakouts over the next 15m.", "chain": "market_neutral -> breakout -> continuation"}},
        {"id": "M7_volume_expansion_breakout_vs_quiet_breakout", "sign": 1, "treat": lambda e: (not bool(e.get("market_weak"))) and bool(e.get("breakout20")) and bool(e.get("vol_expand")), "ctrl": lambda e: (not bool(e.get("market_weak"))) and bool(e.get("breakout20")) and (not bool(e.get("vol_expand"))), "meta": {"layer": "local_technical", "hypothesis": "Breakouts with relative volume >= 1.5 beat quiet breakouts in a non-weak tape.", "chain": "market_not_weak -> vol_expansion -> breakout_quality"}},
        {"id": "M8_1m_loser_bounce_strong_vs_weak_market", "sign": 1, "treat": lambda e: bool(e.get("market_strong")) and _finite(e.get("ret_1m")) and float(e["ret_1m"]) < 0, "ctrl": lambda e: bool(e.get("market_weak")) and _finite(e.get("ret_1m")) and float(e["ret_1m"]) < 0, "meta": {"layer": "market_x_reversal", "hypothesis": "Negative 1m prints bounce more in a strong tape than in a weak tape.", "chain": "market_regime -> short_term_reversal_quality"}},
        {"id": "M9_ema_breadth_adds_given_strong_5m_tape", "sign": 1, "treat": lambda e: bool(e.get("market_strong")) and e.get("ema_breadth") is not None and float(e["ema_breadth"]) >= 0.60, "ctrl": lambda e: bool(e.get("market_strong")) and e.get("ema_breadth") is not None and float(e["ema_breadth"]) <= 0.40, "meta": {"layer": "market", "hypothesis": "When 5m breadth is already strong, EMA9>EMA21 breadth still adds continuation information.", "chain": "5m_breadth -> ema_trend_breadth -> continuation"}},
        {"id": "M10_high_leadership_concentration_not_ew_alpha", "sign": -1, "treat": lambda e: e.get("leadership_hhi") is not None and float(e["leadership_hhi"]) >= 0.08, "ctrl": lambda e: e.get("leadership_hhi") is not None and float(e["leadership_hhi"]) <= 0.04, "meta": {"layer": "leadership", "hypothesis": "High leadership concentration does not produce higher equal-weight continuation than low concentration.", "chain": "leadership_concentration -> ew_return_not_improved"}},
        {"id": "M11_ema_structure_loss_negative_continuation", "sign": -1, "treat": lambda e: bool(e.get("ema_ready")) and (not bool(e.get("ema9_gt_ema21"))) and bool(e.get("market_weak")), "ctrl": lambda e: bool(e.get("ema_ready")) and bool(e.get("ema9_gt_ema21")) and bool(e.get("market_strong")), "meta": {"layer": "invalidation", "hypothesis": "Weak tape plus EMA9<EMA21 continues worse than strong tape plus EMA9>EMA21. Prior V27 evidence, not a frozen EXIT.", "chain": "market_weak -> ema_structure_loss -> negative_continuation"}},
    ]


def evaluate_partition(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for spec in _specs():
        treat = _rows(events, spec["treat"])
        ctrl = _rows(events, spec["ctrl"])
        primary = contrast(treat=treat, ctrl=ctrl, hypothesized_diff_sign=spec["sign"])
        out[spec["id"]] = {**spec["meta"], "primary_clock": PRIMARY_CLOCK, "primary_horizon_min": PRIMARY_HORIZON_MIN, "primary": primary, "robustness_clocks": _robust(events, spec["treat"], spec["ctrl"], spec["sign"])}
    return out


def decide_hypotheses(*, discovery_events: list[dict[str, Any]], confirmation_events: list[dict[str, Any]], validation_dates: set[str]) -> dict[str, Any]:
    if any(str(e.get("date")) in validation_dates for e in discovery_events + confirmation_events):
        raise RuntimeError("frozen_validation_accessed_during_discovery")
    disc = evaluate_partition(discovery_events)
    conf = evaluate_partition(confirmation_events)
    surviving = []
    rejected = []
    rows = []
    for spec in _specs():
        hid = spec["id"]
        d_primary = dict(disc[hid]["primary"])
        c_primary = dict(conf[hid]["primary"])
        status, reasons = _gate(d_primary, c_primary)
        rec = {
            "id": hid,
            **{k: v for k, v in disc[hid].items() if k != "primary"},
            "discovery": d_primary,
            "confirmation": c_primary,
            "status": status,
            "reasons": reasons,
            "complete_strategy": False,
            "paper_ready": False,
            "kabu_50_used": False,
            "frozen_validation_used": False,
        }
        rows.append(rec)
        slim = {"id": hid, "layer": rec.get("layer"), "status": status, "reasons": reasons, "discovery_diff_bps": d_primary.get("mean_diff_x0_h15_bps"), "confirmation_diff_bps": c_primary.get("mean_diff_x0_h15_bps"), "discovery_stability": d_primary.get("day_stability")}
        (surviving if status == "SURVIVING" else rejected).append(slim)
    return {
        "hypotheses": rows,
        "surviving": surviving,
        "rejected": rejected,
        "surviving_n": len(surviving),
        "rejected_n": len(rejected),
        "frozen_validation_accessed": False,
        "entry_only_success_declared": False,
        "complete_strategy_frozen": False,
        "families_forced": False,
        "kabu_50_applied": False,
    }


assert PRIMARY_CLOCK == "09:30"
assert BPS_KEY == "x0_h15_bps"
