"""Block composition, standardization, clocks. Diagnostic only. No selection."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from research.pb1_opening_range_causal_path_test_v1.analyze import _finite, pct_summary


def _age_bucket(m: Any) -> str:
    if not _finite(m):
        return "na"
    if float(m) < 5:
        return "0_5"
    if float(m) < 15:
        return "5_15"
    return "15_30"


def _break_clock(t: Any) -> str:
    s = str(t or "")
    if "09:15" <= s <= "09:24":
        return "09:15-09:24"
    if "09:25" <= s <= "09:39":
        return "09:25-09:39"
    if "09:40" <= s <= "10:00":
        return "09:40-10:00"
    return "other"


def _trig_clock(t: Any) -> str:
    s = str(t or "")
    if "09:15" <= s <= "09:29":
        return "09:15-09:29"
    if "09:30" <= s <= "09:59":
        return "09:30-09:59"
    if s >= "10:00":
        return "10:00+"
    return "other"


def _share(xs: list[str]) -> dict[str, float]:
    n = len(xs)
    c = Counter(xs)
    return {k: float(v / n) if n else 0.0 for k, v in c.items()}


def _smd_cont(a: list[Any], b: list[Any]) -> float | None:
    xa = np.asarray([float(x) for x in a if _finite(x)], dtype=float)
    xb = np.asarray([float(x) for x in b if _finite(x)], dtype=float)
    if xa.size == 0 or xb.size == 0:
        return None
    den = float(np.sqrt(0.5 * (np.var(xa) + np.var(xb))))
    if den <= 0:
        return 0.0
    return float((np.mean(xa) - np.mean(xb)) / den)


def annotate(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in events:
        row = dict(e)
        row["age_bucket"] = _age_bucket(e.get("break_to_retest_minutes"))
        row["break_clock"] = _break_clock(e.get("break_t"))
        row["trigger_clock"] = _trig_clock(e.get("trigger_t"))
        out.append(row)
    return out


def composition(events: list[dict[str, Any]]) -> dict[str, Any]:
    by = {b: [e for e in events if e.get("block") == b] for b in ("D1", "D2", "D3", "D4")}
    cat_keys = ("trigger_primary", "direction", "in_play_reason", "age_bucket", "break_clock", "trigger_clock", "daily_bias")
    cont_keys = (
        "break_to_retest_minutes",
        "away_n",
        "break_beyond_over_or",
        "or_range_over_atr",
        "impulse_mag",
        "abs_gap_atr",
        "tv_0915_pctl",
        "target_distance_R",
        "opening_efficiency",
        "cons_break_to_entry_bps",
        "cons_max_ext_before_retest_bps",
    )
    out: dict[str, Any] = {"n": {b: len(vs) for b, vs in by.items()}}
    pooled = events
    for k in cat_keys:
        out[k] = {b: _share([str(e.get(k) or "na") for e in vs]) for b, vs in by.items()}
        out[k]["all"] = _share([str(e.get(k) or "na") for e in pooled])
    for k in cont_keys:
        out[k] = {b: pct_summary([e.get(k) for e in vs]) for b, vs in by.items()}
        out[k]["smd_D2_vs_D1"] = _smd_cont([e.get(k) for e in by["D2"]], [e.get(k) for e in by["D1"]])
        out[k]["smd_D3_vs_D1"] = _smd_cont([e.get(k) for e in by["D3"]], [e.get(k) for e in by["D1"]])
        out[k]["smd_D4_vs_D1"] = _smd_cont([e.get(k) for e in by["D4"]], [e.get(k) for e in by["D1"]])
    out["risk_valid_share"] = {b: float(sum(1 for e in vs if e.get("risk_state") == "RISK_DEFINED") / len(vs)) if vs else None for b, vs in by.items()}
    return out


def reweight_r10(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Reweight D2/D3/D4 to pooled composition of trigger × side × age × in-play. Not a success claim."""
    cells_all = Counter((str(e.get("trigger_primary")), str(e.get("direction")), str(e.get("age_bucket")), str(e.get("in_play_reason"))) for e in events)
    n = len(events)
    target = {k: v / n for k, v in cells_all.items()} if n else {}
    out: dict[str, Any] = {}
    for b in ("D1", "D2", "D3", "D4"):
        vs = [e for e in events if e.get("block") == b]
        cell_n = Counter((str(e.get("trigger_primary")), str(e.get("direction")), str(e.get("age_bucket")), str(e.get("in_play_reason"))) for e in vs)
        wmean = []
        wmed = []
        used = 0
        for e in vs:
            key = (str(e.get("trigger_primary")), str(e.get("direction")), str(e.get("age_bucket")), str(e.get("in_play_reason")))
            cn = cell_n.get(key) or 0
            if cn <= 0 or key not in target:
                continue
            w = target[key] / (cn / len(vs) if vs else 1)
            x = e.get("r10_bps")
            if not _finite(x):
                continue
            wmean.append((w, float(x)))
            wmed.append((w, float(x)))
            used += 1
        if not wmean:
            out[b] = {"n": len(vs), "used": 0, "raw_p50": pct_summary([e.get("r10_bps") for e in vs]).get("p50"), "reweighted_mean": None}
            continue
        tw = sum(w for w, _ in wmean)
        mean = sum(w * x for w, x in wmean) / tw if tw else None
        rows = sorted(wmed, key=lambda z: z[1])
        acc = 0.0
        p50 = rows[-1][1]
        for w, x in rows:
            acc += w
            if acc >= 0.5 * tw:
                p50 = x
                break
        out[b] = {
            "n": len(vs),
            "used": used,
            "raw_p50": pct_summary([e.get("r10_bps") for e in vs]).get("p50"),
            "reweighted_mean": mean,
            "reweighted_p50": p50,
        }
    signs = [_sign(out[b].get("reweighted_p50")) for b in ("D2", "D3", "D4")]
    out["instability_remains"] = len({s for s in signs if s != 0}) > 1 or any((out[b].get("reweighted_p50") or 0) <= 0 for b in ("D2", "D3", "D4"))
    out["not_a_strategy_success_claim"] = True
    return out


def _sign(x: Any) -> int:
    if not _finite(x) or float(x) == 0:
        return 0
    return 1 if float(x) > 0 else -1


def clock_paths(events: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("break_clock", "trigger_clock"):
        groups: dict[str, list] = defaultdict(list)
        for e in events:
            groups[str(e.get(key) or "na")].append(e)
        out[key] = {
            k: {
                "n": len(vs),
                "r10_p50": pct_summary([e.get("r10_bps") for e in vs]).get("p50"),
                "or_fail": float(sum(1 for e in vs if e.get("or_accept_fail")) / len(vs)) if vs else None,
                "cons_break_to_entry_p50": pct_summary([e.get("cons_break_to_entry_bps") for e in vs]).get("p50"),
            }
            for k, vs in groups.items()
        }
    return out
