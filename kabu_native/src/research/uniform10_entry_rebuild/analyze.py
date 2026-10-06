"""A/B/C economics + UP-mover ranking under corrected fill."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_10min_opportunity.grids import tod_family
from research.passive_fill_corrected_rebase.analyze import (
    baseline_bundle,
    entry_kind_block,
    headline,
)
from research.uniform10_entry_rebuild.model import score_cohort

PRIMARY = "FORWARD_MID_RETURN_600S"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def ranking_block(panel: list[dict[str, Any]], score_key: str) -> dict[str, Any]:
    by: dict[tuple[str, str], list] = defaultdict(list)
    for r in panel:
        y = _f(r.get(PRIMARY))
        s = _f(r.get(score_key))
        if y is None or s is None:
            continue
        by[(str(r.get("date")), str(r.get("anchor")))].append(r)

    def agg(ks: list[int] | None) -> dict[str, Any]:
        vals = []
        day_means: dict[str, list[float]] = defaultdict(list)
        for (d, _a), grp in by.items():
            ordered = sorted(grp, key=lambda x: (-float(_f(x.get(score_key)) or -1e18), str(x.get("symbol"))))
            pick = ordered if ks is None else ordered[: ks[0]]
            if not pick:
                continue
            m = float(np.mean([_f(x.get(PRIMARY)) for x in pick]))
            vals.append(m)
            day_means[d].append(m)
        day_pos = 0
        day_n = 0
        for d, xs in day_means.items():
            day_n += 1
            if float(np.mean(xs)) > 0:
                day_pos += 1
        return {
            "n_cohorts": len(vals),
            "mean_target": float(np.mean(vals)) if vals else None,
            "median_target": float(np.median(vals)) if vals else None,
            "pos_day_share": (day_pos / day_n) if day_n else None,
        }

    out = {
        "ALL": agg(None),
        "Top10": agg([10]),
        "Top5": agg([5]),
        "Top3": agg([3]),
        "Top1": agg([1]),
    }
    all_m = out["ALL"].get("mean_target")
    t1 = out["Top1"].get("mean_target")
    t3 = out["Top3"].get("mean_target")
    out["UP_MOVER_RANKING_SUPPORTED"] = bool(
        all_m is not None
        and t1 is not None
        and t3 is not None
        and t1 > all_m
        and t3 > all_m
        and (out["Top1"].get("pos_day_share") or 0) >= 0.5
    )
    return out


def attach_rebuild_scores(panel: list[dict[str, Any]], model: dict[str, Any]) -> list[dict[str, Any]]:
    by: dict[tuple[str, str], list] = defaultdict(list)
    for r in panel:
        by[(str(r.get("date")), str(r.get("anchor")))].append(r)
    out = []
    for grp in by.values():
        out.extend(score_cohort(grp, model))
    return out


def abc_pack(a: list[dict], b: list[dict], c: list[dict]) -> dict[str, Any]:
    A = headline(a)
    B = headline(b)
    C = headline(c)
    return {
        "A": A,
        "B": B,
        "C": C,
        "A_to_B_grid_effect": {
            "pnl": (B.get("pnl") or 0) - (A.get("pnl") or 0),
            "PF_A": A.get("PF"),
            "PF_B": B.get("PF"),
            "trades_delta": (B.get("trades") or 0) - (A.get("trades") or 0),
        },
        "B_to_C_entry_effect": {
            "pnl": (C.get("pnl") or 0) - (B.get("pnl") or 0),
            "PF_B": B.get("PF"),
            "PF_C": C.get("PF"),
            "trades_delta": (C.get("trades") or 0) - (B.get("trades") or 0),
        },
    }


def stability(trades: list[dict[str, Any]]) -> dict[str, Any]:
    b = baseline_bundle(trades)
    ek = entry_kind_block(trades)
    by_day = b.get("by_day") or []
    pos = sum(1 for r in by_day if float(r.get("pnl") or 0) > 0)
    am = [t for t in trades if str(t.get("session")) == "AM"]
    pm = [t for t in trades if str(t.get("session")) == "PM"]
    tod = {}
    for fam in ("OPEN_EARLY", "NORMAL_SESSION", "SESSION_TAIL"):
        xs = [t for t in trades if tod_family(str(t.get("anchor_time") or "")) == fam]
        tod[fam] = headline(xs)
    fills = len(trades)
    return {
        "headline": b.get("headline"),
        "concentration": b.get("concentration"),
        "day_pos_share": (pos / len(by_day)) if by_day else None,
        "AM": headline(am),
        "PM": headline(pm),
        "tod": tod,
        "first_entry": ek.get("first_entry"),
        "re_entry": ek.get("re_entry"),
        "fill_n": fills,
        "pnl_per_fill": ((b.get("headline") or {}).get("pnl") / fills) if fills else None,
    }


def robustness(*, abc: dict[str, Any], ranking_ok: bool, conc: dict[str, Any]) -> str:
    def pnl(k: str) -> float:
        return float(((abc.get(k) or {}).get("pnl")) or 0.0)

    def pf(k: str) -> Optional[float]:
        v = (abc.get(k) or {}).get("PF")
        if v is None or v == "Infinity":
            return None if v is None else 9.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    top1 = conc.get("top1_trade_share")
    if not ranking_ok or pnl("C") < 0:
        return "WEAK"
    pfs = [pf("A"), pf("B"), pf("C")]
    if all(p is not None and p >= 1.0 for p in pfs) and all(pnl(k) > 0 for k in "ABC") and (top1 is None or top1 < 0.40):
        return "STRONG"
    return "MODERATE"
