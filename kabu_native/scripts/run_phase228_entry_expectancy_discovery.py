#!/usr/bin/env python3
"""
Phase228: Entry expectancy discovery (review only).

Find ENTRY condition combinations with PF>1 and positive total PnL (IS+OOS).
No stop/winner/cause analysis — expectancy only.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase228_entry_expectancy_discovery.json"

LOOKBACK_SEC = 600.0
MIN_N = 300

TERTILE_FEATURES: tuple[tuple[str, str], ...] = (
    ("Board", "entry_order_book_imbalance"),
    ("VWAP", "entry_vwap_dev_pct"),
    ("TV", "trading_value"),
    ("TickRatio", "tick_ratio_pct"),
    ("Momentum", "momentum_continuation_score"),
    ("Duration", "max_continuation_duration"),
    ("RollingMFE", "rolling_mfe_pct"),
    ("RollingMAE", "rolling_mae_pct"),
    ("Quality", "continuation_quality_score"),
    ("Rise5m", "entry_rise_5min_pct"),
    ("Rise10m", "entry_rise_10min_pct"),
    ("HBCount", "high_break_count_full_jsonl"),
    ("Price", "current_price"),
    ("Turnover", "turnover_proxy"),
)


def _load_module(name: str, rel_path: str) -> Any:
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _quantile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def _pf(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _find_push_jsonl(push_dir: Path, symbol: str) -> Optional[Path]:
    sym = symbol.replace(".T", "")
    for name in (f"{symbol}.jsonl", f"{sym}.jsonl"):
        p = push_dir / name
        if p.is_file():
            return p
    return None


def _load_ticks(path: Path, mod: Any) -> list[tuple[float, float]]:
    ticks: list[tuple[float, float]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = mod._parse_ts(str(rec.get("recorded_at") or ""))
            px = _float((rec.get("payload") or {}).get("CurrentPrice"))
            if px and px > 0:
                ticks.append((ts, float(px)))
    return ticks


def _high_break_count(window: list[tuple[float, float]]) -> int:
    if len(window) < 2:
        return 0
    running = window[0][1]
    count = 0
    for _, px in window[1:]:
        if px > running * 1.0001:
            count += 1
            running = px
    return count


def _high_break_recent(ring: list[tuple[float, float]], entry_ts: float, entry_px: float) -> bool:
    cur = [(t, px) for t, px in ring if entry_ts - 300 <= t <= entry_ts]
    prev = [(t, px) for t, px in ring if entry_ts - 600 <= t < entry_ts - 300]
    if len(cur) < 2 or len(prev) < 2 or entry_px <= 0:
        return False
    m5 = max(px for _, px in cur)
    m5_prev = max(px for _, px in prev)
    if m5 <= m5_prev * 1.0001:
        return False
    if entry_px < m5 * 0.998:
        return False
    last_high_ts = max(t for t, px in cur if px >= m5 * 0.998)
    return (entry_ts - last_high_ts) <= 60.0


def _augment_high_break(mod: Any, rows: list[dict[str, Any]]) -> None:
    tick_cache: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        sym = str(r.get("symbol") or "")
        entry_ts = mod._parse_ts(str(r.get("entry_time") or ""))
        entry_day = str(r.get("day_stamp") or "")
        session_rel = str(r.get("session_id") or "")
        entry_px = _float(r.get("current_price"))
        push_dir = mod._push_dir_for_day(entry_day) or mod._push_dir(session_rel)
        if not push_dir or not entry_px:
            continue
        jpath = _find_push_jsonl(push_dir, sym)
        if not jpath:
            continue
        key = str(jpath)
        if key not in tick_cache:
            tick_cache[key] = _load_ticks(jpath, mod)
        ticks = tick_cache[key]
        before = [(t, px) for t, px in ticks if t <= entry_ts]
        window = [(t, px) for t, px in before if entry_ts - LOOKBACK_SEC <= t <= entry_ts]
        if len(window) >= 2:
            r["high_break_count_full_jsonl"] = _high_break_count(window)
            r["high_break_recent_recomputed"] = _high_break_recent(before, entry_ts, float(entry_px))


def _build_tertiles(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cuts: dict[str, dict[str, Any]] = {}
    for label, field in TERTILE_FEATURES:
        vals = [_float(r.get(field)) for r in rows]
        vals = [v for v in vals if v is not None]
        if len(vals) < 9:
            cuts[label] = {"field": field, "coverage_n": len(vals), "usable": False}
            continue
        cuts[label] = {
            "field": field,
            "coverage_n": len(vals),
            "coverage_pct": round(100.0 * len(vals) / len(rows), 2),
            "usable": True,
            "p33": round(_quantile(vals, 1.0 / 3.0), 6),
            "p66": round(_quantile(vals, 2.0 / 3.0), 6),
        }
    return cuts


def _bin_label(val: float, q33: float, q66: float) -> str:
    if val <= q33:
        return "low"
    if val <= q66:
        return "mid"
    return "high"


def _assign_bins(rows: list[dict[str, Any]], cuts: dict[str, dict[str, Any]]) -> None:
    for r in rows:
        bins: dict[str, str] = {}
        for label, info in cuts.items():
            if not info.get("usable"):
                continue
            v = _float(r.get(info["field"]))
            if v is None:
                continue
            bins[label] = _bin_label(v, info["p33"], info["p66"])
        if r.get("high_break_recent_recomputed") is not None:
            bins["HBRecent"] = "yes" if r.get("high_break_recent_recomputed") else "no"
        r["_bins"] = bins


def _cluster_key(r: dict[str, Any], combo: tuple[str, ...]) -> Optional[tuple[str, ...]]:
    bins = r.get("_bins") or {}
    if not all(lbl in bins for lbl in combo):
        return None
    return tuple(f"{lbl}:{bins[lbl]}" for lbl in combo)


def _metrics(members: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(r.get("pnl_pct") or 0) for r in members]
    n = len(members)
    stops = sum(1 for r in members if r.get("stop_hit"))
    wins = sum(1 for p in pnls if p > 0)
    pf = _pf(pnls)
    is_rows = [r for r in members if r.get("split") == "in_sample"]
    oos_rows = [r for r in members if r.get("split") == "oos"]
    is_pnls = [float(r.get("pnl_pct") or 0) for r in is_rows]
    oos_pnls = [float(r.get("pnl_pct") or 0) for r in oos_rows]
    is_pf = _pf(is_pnls) if is_pnls else None
    oos_pf = _pf(oos_pnls) if oos_pnls else None
    return {
        "n": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(wins / n, 4),
        "stop_hit_rate": round(stops / n, 4),
        "IS_n": len(is_rows),
        "IS_pf": is_pf,
        "IS_total_pnl_pct": round(sum(is_pnls), 4) if is_pnls else 0.0,
        "OOS_n": len(oos_rows),
        "OOS_pf": oos_pf,
        "OOS_total_pnl_pct": round(sum(oos_pnls), 4) if oos_pnls else 0.0,
    }


def _slim(m: dict[str, Any], cluster: str, features: list[str], combo_key: str, arity: int) -> dict[str, Any]:
    return {
        "cluster": cluster,
        "features": features,
        "combo_key": combo_key,
        "arity": arity,
        **m,
    }


def _pf_gt_1(v: Any) -> bool:
    return v is not None and isinstance(v, (int, float)) and v > 1


def _passes_pnl_positive(m: dict[str, Any]) -> bool:
    return m["n"] >= MIN_N and _pf_gt_1(m.get("profit_factor")) and m.get("total_pnl_pct", 0) > 0


def _passes_is_oos(m: dict[str, Any]) -> bool:
    return _passes_pnl_positive(m) and _pf_gt_1(m.get("IS_pf")) and _pf_gt_1(m.get("OOS_pf"))


def _rank_key(m: dict[str, Any]) -> tuple[float, float, float]:
    pf = m.get("profit_factor") or 0
    if pf == float("inf"):
        pf = 999.0
    return (-float(pf), -float(m.get("total_pnl_pct") or 0), -float(m.get("n") or 0))


def _scan(
    rows: list[dict[str, Any]],
    usable_labels: list[str],
    arity: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    combo_key_base = "+".join
    for combo in itertools.combinations(usable_labels, arity):
        ck = combo_key_base(combo)
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            key = _cluster_key(r, combo)
            if key is None:
                continue
            groups[key].append(r)
        for key, members in groups.items():
            m = _metrics(members)
            if m["n"] < MIN_N:
                continue
            if not _pf_gt_1(m.get("profit_factor")) or m.get("total_pnl_pct", 0) <= 0:
                continue
            out.append(_slim(m, " & ".join(key), list(combo), ck, arity))
    return out


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_module("phase217_loader_p228", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py")
    p221 = _load_module("phase221_loader_p228", "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py")
    mod = p217._load_phase213c_module()

    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    print("augmenting...", flush=True)
    p221._augment_early_features(mod, rows)
    _augment_high_break(mod, rows)

    cuts = _build_tertiles(rows)
    _assign_bins(rows, cuts)

    usable = [lbl for lbl, _ in TERTILE_FEATURES if cuts.get(lbl, {}).get("usable")]
    if "HBRecent" in {r.get("_bins", {}).get("HBRecent") for r in rows if r.get("_bins")} or any(
        "HBRecent" in (r.get("_bins") or {}) for r in rows
    ):
        usable_hb = usable + ["HBRecent"]
    else:
        usable_hb = usable + (["HBRecent"] if any((r.get("_bins") or {}).get("HBRecent") for r in rows) else [])

    all_positive: list[dict[str, Any]] = []
    for arity in (2, 3, 4, 5):
        print(f"scanning {arity}-way...", flush=True)
        all_positive.extend(_scan(rows, usable_hb, arity))

    is_oos_both = [c for c in all_positive if _passes_is_oos(c)]
    is_oos_both.sort(key=_rank_key)

    top20 = is_oos_both[:20]
    top5 = is_oos_both[:5]

    coverage_gaps = [
        {"field": info["field"], "coverage_pct": info.get("coverage_pct"), "usable": info.get("usable")}
        for info in cuts.values()
        if not info.get("usable") or (info.get("coverage_pct") or 0) < 50
    ]
    shadow = [
        {"field": "high_break_recent_recomputed", "coverage": "full jsonl"},
        {"field": "high_break_count_full_jsonl", "coverage": "full jsonl"},
        {"field": "tick_ratio_pct", "coverage_pct": cuts.get("TickRatio", {}).get("coverage_pct")},
        {"field": "entry_rise_5min_pct", "coverage_pct": cuts.get("Rise5m", {}).get("coverage_pct")},
        {"field": "entry_rise_10min_pct", "coverage_pct": cuts.get("Rise10m", {}).get("coverage_pct")},
    ]

    report = {
        "phase": 228,
        "mode": "entry_expectancy_discovery",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "hard_reject_forbidden": True,
            "expectancy_only": True,
        },
        "population": {"total_trades": len(rows)},
        "method": {
            "binning": "tertile low/mid/high; HBRecent yes/no",
            "interactions": "2-way through 5-way",
            "min_n": MIN_N,
            "adoption_requires": "n>=300, PF>1, total_pnl>0, IS_pf>1, OOS_pf>1",
            "ranking": "PF desc, total_pnl desc, n desc",
        },
        "tertile_cutoffs": cuts,
        "scan_counts": {
            "pf_gt_1_pnl_gt_0_n_ge_300": len(all_positive),
            "is_oos_both_pf_gt_1": len(is_oos_both),
        },
        "1_adoption_candidates_top20": top20,
        "2_pf_gt_1_pnl_gt_0_clusters": all_positive,
        "3_is_oos_both_pf_gt_1_clusters": is_oos_both,
        "4_main_candidates_top5": top5,
        "5_shadow_logging_candidates": shadow + coverage_gaps,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} is_oos_ok={len(is_oos_both)} all_pos={len(all_positive)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
