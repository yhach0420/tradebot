"""Walk-forward lock on DEV_EARLY. Occupancy Complete Strategy on kept candidates."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.pb1_entry_architecture_reassessment import MIN_OOF_TRADES, WINNER_CONC_MAX
from research.pb1_v4_complete_strategy_build_and_economic_validation.analyze import development_metrics
from research.pb1_v4_complete_strategy_build_and_economic_validation.portfolio import replay_occupancy


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    i = min(len(ys) - 1, max(0, int(round((len(ys) - 1) * float(q)))))
    return float(ys[i])


def lock_threshold(train_rows: list[dict[str, Any]], *, feat: str, direction: int) -> dict[str, Any]:
    xs = [float(r[feat]) for r in train_rows if _finite(r.get(feat))]
    if direction > 0:
        thr = percentile(xs, 0.80)
        rule = "keep_if_feature_ge_DEV_EARLY_Q80"
    elif direction < 0:
        thr = percentile(xs, 0.20)
        rule = "keep_if_feature_le_DEV_EARLY_Q20"
    else:
        return {"ok": False, "reason": "no_direction"}
    return {"ok": True, "feature": feat, "direction": direction, "threshold": thr, "rule": rule, "train_n": len(xs)}


def keep_row(row: dict[str, Any], *, feat: str, direction: int, threshold: float) -> bool:
    v = row.get(feat)
    if not _finite(v):
        return False
    if direction > 0:
        return float(v) >= float(threshold)
    return float(v) <= float(threshold)


def session_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    bag: dict[str, float] = defaultdict(float)
    for t in trades:
        bag[str(t.get("date") or "")] += float(t.get("net_pnl_yen") or 0)
    vals = list(bag.values())
    return {
        "session_n": len(vals),
        "positive_session_n": sum(1 for v in vals if v > 0),
        "negative_session_n": sum(1 for v in vals if v < 0),
    }


def concentration(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_sym: dict[str, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    tot = 0.0
    for t in trades:
        n = float(t.get("net_pnl_yen") or 0)
        tot += n
        by_sym[str(t.get("symbol") or "")] += n
        by_month[str(t.get("date") or "")[:6]] += n
    abs_tot = sum(abs(v) for v in by_sym.values()) or 1.0
    top_sym = max(by_sym.items(), key=lambda kv: abs(kv[1])) if by_sym else ("", 0.0)
    return {
        "top_symbol": top_sym[0],
        "top_symbol_net": top_sym[1],
        "top_symbol_abs_share": abs(top_sym[1]) / abs_tot,
        "net_sum": tot,
        "month_nets": dict(by_month),
    }


def evaluate_kept(v1_rows: list[dict[str, Any]], kept: list[dict[str, Any]]) -> dict[str, Any]:
    """Filter already-filled V1 trades. Occupancy not re-solved; labeled as such."""
    occ = replay_occupancy(kept)
    trades = list(occ.get("trades") or kept)
    m = development_metrics(trades)
    conc = concentration(trades)
    sess = session_stats(trades)
    oof = [t for t in trades if str(t.get("sample") or "") == "C1"]
    oof_m = development_metrics(oof) if oof else {}
    oof_conc = concentration(oof) if oof else {}
    month_nets = dict(oof_conc.get("month_nets") or {})
    c1_net = float(oof_m.get("net_pnl_yen") or 0)
    month_abs = sum(abs(v) for v in month_nets.values()) or 1.0
    top_month_share = (max(abs(v) for v in month_nets.values()) / month_abs) if month_nets else 1.0
    c1_ok = (
        len(oof) >= int(MIN_OOF_TRADES)
        and c1_net > 0
        and (oof_m.get("profit_factor") is not None and float(oof_m.get("profit_factor") or 0) > 1)
        and float(oof_m.get("mean_net_pnl_per_trade") or 0) > 0
        and float(oof_conc.get("top_symbol_abs_share") or 1) <= float(WINNER_CONC_MAX)
        and float(top_month_share) <= float(WINNER_CONC_MAX)
    )
    return {
        "occupancy_mode": "FILTER_ON_V1_FILLS_THEN_REPLAY_OCCUPANCY",
        "v1_trade_n": len(v1_rows),
        "kept_n": len(kept),
        "metrics": m,
        "oof_c1": oof_m,
        "oof_c1_trade_n": len(oof),
        "oof_c1_concentration": oof_conc,
        "oof_c1_top_month_abs_share": top_month_share,
        "sessions": sess,
        "concentration": conc,
        "cap_blocked_n": occ.get("cap_blocked_n"),
        "same_symbol_blocked_n": occ.get("same_symbol_blocked_n"),
        "c1_positive_edge": bool(c1_ok),
        "c1_reject": [] if c1_ok else _c1_reject(len(oof), oof_m, oof_conc, top_month_share),
    }


def _c1_reject(n: int, oof_m: dict[str, Any], oof_conc: dict[str, Any], top_month_share: float) -> list[str]:
    out = []
    if n < int(MIN_OOF_TRADES):
        out.append("OOF_N")
    if float(oof_m.get("net_pnl_yen") or 0) <= 0:
        out.append("OOF_NET")
    pf = oof_m.get("profit_factor")
    if pf is None or float(pf) <= 1:
        out.append("OOF_PF")
    if float(oof_m.get("mean_net_pnl_per_trade") or 0) <= 0:
        out.append("OOF_MEAN")
    if float(oof_conc.get("top_symbol_abs_share") or 1) > float(WINNER_CONC_MAX):
        out.append("OOF_SYMBOL")
    if float(top_month_share) > float(WINNER_CONC_MAX):
        out.append("OOF_MONTH")
    return out
