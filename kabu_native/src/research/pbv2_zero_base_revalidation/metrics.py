"""PnL / PF metrics with integrity checks (raw vs 5bps separated)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Optional, Sequence

from research.pbv2_zero_base_revalidation.panel import CandidateRow

KeepFn = Callable[[CandidateRow], bool]


def _pf(gp: float, gl: float) -> Optional[float]:
    if gl > 1e-12:
        return round(gp / gl, 4)
    if gp > 0:
        return 999.0
    return None


def pnl_metric_block(y_raw: Sequence[float], y5: Sequence[float]) -> dict[str, Any]:
    gp_raw = sum(y for y in y_raw if y > 0)
    gl_raw = abs(sum(y for y in y_raw if y < 0))
    gp5 = sum(y for y in y5 if y > 0)
    gl5 = abs(sum(y for y in y5 if y < 0))
    total_raw = round(sum(y_raw), 2)
    total_5 = round(sum(y5), 2)
    pf_raw = _pf(gp_raw, gl_raw)
    pf5 = _pf(gp5, gl5)
    blocked = bool(total_5 < 0 and pf5 is not None and pf5 > 1.0)
    return {
        "n": len(y5),
        "total_pnl_raw": total_raw,
        "total_pnl_5bps": total_5,
        "gross_profit_raw": round(gp_raw, 2),
        "gross_loss_raw": round(gl_raw, 2),
        "gross_profit_5bps": round(gp5, 2),
        "gross_loss_5bps": round(gl5, 2),
        "PF_raw": pf_raw,
        "PF_5bps": pf5,
        # aliases used by existing report fields
        "pnl_raw": total_raw,
        "pnl_5bps": total_5,
        "pf": pf5,
        "metric_integrity_blocked": blocked,
    }


def metrics_for(
    rows: Sequence[CandidateRow],
    keep: KeepFn,
    *,
    require_pnl: bool = True,
    universe: Optional[Sequence[CandidateRow]] = None,
) -> dict[str, Any]:
    base_rows = list(universe) if universe is not None else list(rows)
    kept: list[CandidateRow] = []
    for r in rows:
        if require_pnl and not getattr(r, "pnl_evaluable", False) and r.cf_pnl_5bps is None and r.cf_pnl is None:
            continue
        if keep(r):
            kept.append(r)
    y5 = [float(r.cf_pnl_5bps if r.cf_pnl_5bps is not None else r.cf_pnl or 0.0) for r in kept]
    y_raw = [float(r.cf_pnl if r.cf_pnl is not None else r.cf_pnl_5bps or 0.0) for r in kept]
    block = pnl_metric_block(y_raw, y5)
    by_day: dict[str, float] = defaultdict(float)
    for r, y in zip(kept, y5):
        by_day[r.day] += y
    pos_days = sum(1 for v in by_day.values() if v > 0)
    neg_days = sum(1 for v in by_day.values() if v < 0)
    n_stop = sum(1 for r in kept if r.is_stop)
    n_np = sum(1 for r in kept if r.is_np)
    n_win = sum(1 for r in kept if r.is_winner)
    n_lr = sum(1 for r in kept if r.is_large_rise and getattr(r, "large_rise_evaluable", True))
    base_lr = sum(1 for r in base_rows if r.is_large_rise and getattr(r, "large_rise_evaluable", True))
    base_win = sum(1 for r in base_rows if r.is_winner)
    kept_win_from_base = sum(1 for r in kept if r.is_winner)
    return {
        **block,
        "stop_rate": round(n_stop / len(kept), 4) if kept else None,
        "np_rate": round(n_np / len(kept), 4) if kept else None,
        "winner_rate": round(n_win / len(kept), 4) if kept else None,
        "large_rise_n": n_lr,
        "large_rise_capture": round(n_lr / base_lr, 4) if base_lr else None,
        "winner_capture": round(kept_win_from_base / base_win, 4) if base_win else None,
        "pos_days": pos_days,
        "neg_days": neg_days,
        "max_daily_loss": round(min(by_day.values()), 2) if by_day else 0.0,
        "by_day": {k: round(v, 2) for k, v in sorted(by_day.items())},
        "eligible_universe_n": len(base_rows),
    }


def aggregate_oos_daily(day_metrics: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Pool gross profit/loss across days — never average daily PF."""
    if not day_metrics:
        return {
            "n": 0,
            "total_pnl_raw": 0.0,
            "total_pnl_5bps": 0.0,
            "PF_raw": None,
            "PF_5bps": None,
            "pnl_5bps": 0.0,
            "pf": None,
            "pos_days": 0,
            "neg_days": 0,
            "metric_integrity_blocked": False,
        }
    gp5 = sum(float(m.get("gross_profit_5bps") or 0) for m in day_metrics)
    gl5 = sum(float(m.get("gross_loss_5bps") or 0) for m in day_metrics)
    gp_raw = sum(float(m.get("gross_profit_raw") or 0) for m in day_metrics)
    gl_raw = sum(float(m.get("gross_loss_raw") or 0) for m in day_metrics)
    total5 = round(sum(float(m.get("total_pnl_5bps") if m.get("total_pnl_5bps") is not None else m.get("pnl_5bps") or 0) for m in day_metrics), 2)
    total_raw = round(sum(float(m.get("total_pnl_raw") if m.get("total_pnl_raw") is not None else m.get("pnl_raw") or 0) for m in day_metrics), 2)
    pf5 = _pf(gp5, gl5)
    pf_raw = _pf(gp_raw, gl_raw)
    blocked = bool(total5 < 0 and pf5 is not None and pf5 > 1.0)
    blocked = blocked or any(bool(m.get("metric_integrity_blocked")) for m in day_metrics)
    n = sum(int(m.get("n") or 0) for m in day_metrics)
    pos = sum(1 for m in day_metrics if float(m.get("total_pnl_5bps") if m.get("total_pnl_5bps") is not None else m.get("pnl_5bps") or 0) > 0)
    neg = sum(1 for m in day_metrics if float(m.get("total_pnl_5bps") if m.get("total_pnl_5bps") is not None else m.get("pnl_5bps") or 0) < 0)
    stops = [m["stop_rate"] for m in day_metrics if m.get("stop_rate") is not None]
    nps = [m["np_rate"] for m in day_metrics if m.get("np_rate") is not None]
    lrc = [m["large_rise_capture"] for m in day_metrics if m.get("large_rise_capture") is not None]
    winc = [m["winner_capture"] for m in day_metrics if m.get("winner_capture") is not None]
    return {
        "n": n,
        "total_pnl_raw": total_raw,
        "total_pnl_5bps": total5,
        "gross_profit_raw": round(gp_raw, 2),
        "gross_loss_raw": round(gl_raw, 2),
        "gross_profit_5bps": round(gp5, 2),
        "gross_loss_5bps": round(gl5, 2),
        "PF_raw": pf_raw,
        "PF_5bps": pf5,
        "pnl_raw": total_raw,
        "pnl_5bps": total5,
        "pf": pf5,
        "metric_integrity_blocked": blocked,
        "pos_days": pos,
        "neg_days": neg,
        "stop_rate": round(sum(stops) / len(stops), 4) if stops else None,
        "np_rate": round(sum(nps) / len(nps), 4) if nps else None,
        "large_rise_capture": round(sum(lrc) / len(lrc), 4) if lrc else None,
        "winner_capture": round(sum(winc) / len(winc), 4) if winc else None,
        "max_daily_loss": round(
            min(float(m.get("total_pnl_5bps") if m.get("total_pnl_5bps") is not None else m.get("pnl_5bps") or 0) for m in day_metrics),
            2,
        ),
        "n_oos_days": len(day_metrics),
        "daily": [
            {
                "test_date": m.get("test_date"),
                "pnl_5bps": m.get("total_pnl_5bps", m.get("pnl_5bps")),
                "PF_5bps": m.get("PF_5bps"),
                "n": m.get("n"),
            }
            for m in day_metrics
        ],
    }
