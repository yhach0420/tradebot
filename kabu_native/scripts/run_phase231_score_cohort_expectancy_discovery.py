#!/usr/bin/env python3
"""
Phase231: Score-cohort internal expectancy discovery (review only).

Within Phase229 Score>=5 and Score>=6 cohorts ONLY — no full 2503 re-scan.
Find feature combinations that beat cohort baseline PF/PnL with IS/OOS PF>1.
Expectancy improvement only — no loser/stop analysis.
"""

from __future__ import annotations

import csv
import importlib.util
import itertools
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
P228 = REPO / "kabu_native/results/reports/phase228_entry_expectancy_discovery.json"
P229 = REPO / "kabu_native/results/reports/phase229_entry_score_discovery.json"
OUT = REPO / "kabu_native/results/reports/phase231_score_cohort_expectancy_discovery.json"
OUT_CSV = REPO / "kabu_native/results/reports/phase231_score_cohort_adoption_candidates.csv"

LOOKBACK_SEC = 600.0
MIN_N = 200
SCORE_GE5 = 5
SCORE_GE6 = 6

TARGET_FEATURES = frozenset(
    {"TV", "RollingMAE", "HBRecent", "HBCount", "Price", "Board", "Momentum", "Duration"}
)

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


def _bin_label(val: float, q33: float, q66: float) -> str:
    if val <= q33:
        return "low"
    if val <= q66:
        return "mid"
    return "high"


def _assign_bins(rows: list[dict[str, Any]], cuts: dict[str, dict[str, Any]], key: str = "_bins") -> None:
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
        r[key] = bins


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
            "coverage_pct": round(100.0 * len(vals) / max(1, len(rows)), 2),
            "usable": True,
            "p33": round(_quantile(vals, 1.0 / 3.0), 6),
            "p66": round(_quantile(vals, 2.0 / 3.0), 6),
        }
    return cuts


def _trade_score(r: dict[str, Any], score_map: dict[str, int]) -> int:
    bins = r.get("_bins") or {}
    total = 0
    for lbl in TARGET_FEATURES:
        if lbl not in bins:
            continue
        token = f"{lbl}:{bins[lbl]}"
        total += score_map.get(token, 0)
    return total


def _expectancy_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "IS_n": 0,
            "IS_pf": None,
            "IS_total_pnl_pct": 0.0,
            "OOS_n": 0,
            "OOS_pf": None,
            "OOS_total_pnl_pct": 0.0,
        }
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    n = len(rows)
    wins = sum(1 for p in pnls if p > 0)
    is_rows = [r for r in rows if r.get("split") == "in_sample"]
    oos_rows = [r for r in rows if r.get("split") == "oos"]
    is_pnls = [float(r.get("pnl_pct") or 0) for r in is_rows]
    oos_pnls = [float(r.get("pnl_pct") or 0) for r in oos_rows]
    pf = _pf(pnls)
    return {
        "n": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(wins / n, 4),
        "IS_n": len(is_rows),
        "IS_pf": _pf(is_pnls) if is_pnls else None,
        "IS_total_pnl_pct": round(sum(is_pnls), 4) if is_pnls else 0.0,
        "OOS_n": len(oos_rows),
        "OOS_pf": _pf(oos_pnls) if oos_pnls else None,
        "OOS_total_pnl_pct": round(sum(oos_pnls), 4) if oos_pnls else 0.0,
    }


def _cluster_key(r: dict[str, Any], combo: tuple[str, ...], bin_key: str = "_cohort_bins") -> Optional[tuple[str, ...]]:
    bins = r.get(bin_key) or {}
    if not all(lbl in bins for lbl in combo):
        return None
    return tuple(f"{lbl}:{bins[lbl]}" for lbl in combo)


def _pf_gt_1(v: Any) -> bool:
    return v is not None and isinstance(v, (int, float)) and v > 1


def _passes_adoption(
    m: dict[str, Any],
    *,
    baseline_pf: float,
    baseline_pnl: float,
) -> bool:
    pf = m.get("profit_factor")
    if pf is None or pf == float("inf"):
        return False
    return (
        m["n"] >= MIN_N
        and pf > baseline_pf
        and m.get("total_pnl_pct", 0) > baseline_pnl
        and _pf_gt_1(m.get("IS_pf"))
        and _pf_gt_1(m.get("OOS_pf"))
    )


def _rank_key(m: dict[str, Any]) -> tuple[float, float, float]:
    pf = m.get("profit_factor") or 0
    if pf == float("inf"):
        pf = 999.0
    return (-float(pf), -float(m.get("total_pnl_pct") or 0), -float(m.get("n") or 0))


def _scan_cohort(
    rows: list[dict[str, Any]],
    usable_labels: list[str],
    *,
    baseline_pf: float,
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    adopted: list[dict[str, Any]] = []
    for arity in (2, 3, 4, 5):
        for combo in itertools.combinations(usable_labels, arity):
            ck = "+".join(combo)
            groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
            for r in rows:
                key = _cluster_key(r, combo)
                if key is None:
                    continue
                groups[key].append(r)
            for key, members in groups.items():
                m = _expectancy_metrics(members)
                if not _passes_adoption(m, baseline_pf=baseline_pf, baseline_pnl=baseline_pnl):
                    continue
                adopted.append(
                    {
                        "cluster": " & ".join(key),
                        "features": list(combo),
                        "combo_key": ck,
                        "arity": arity,
                        "baseline_pf": baseline_pf,
                        "baseline_total_pnl_pct": baseline_pnl,
                        "pf_lift_vs_baseline": round(float(m["profit_factor"]) - baseline_pf, 4),
                        "pnl_lift_vs_baseline": round(float(m["total_pnl_pct"]) - baseline_pnl, 4),
                        **m,
                    }
                )
    adopted.sort(key=_rank_key)
    return adopted


def _write_csv(rows: list[dict[str, Any]]) -> None:
    if not rows:
        OUT_CSV.write_text("", encoding="utf-8")
        return
    fields = [
        "cohort",
        "cluster",
        "arity",
        "n",
        "profit_factor",
        "total_pnl_pct",
        "avg_pnl_pct",
        "win_rate",
        "IS_n",
        "IS_pf",
        "IS_total_pnl_pct",
        "OOS_n",
        "OOS_pf",
        "OOS_total_pnl_pct",
        "baseline_pf",
        "baseline_total_pnl_pct",
        "pf_lift_vs_baseline",
        "pnl_lift_vs_baseline",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p228 = json.loads(P228.read_text(encoding="utf-8"))
    p229 = json.loads(P229.read_text(encoding="utf-8"))
    score_cutoffs = p228["tertile_cutoffs"]
    score_map = p229["work3_score_components"]["score_map"]

    p217 = _load_module("phase217_loader_p231", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py")
    p221 = _load_module("phase221_loader_p231", "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py")
    mod = p217._load_phase213c_module()

    print("loading trades (for score assignment only)...", flush=True)
    all_rows = p217._build_all(mod)
    print("augmenting...", flush=True)
    p221._augment_early_features(mod, all_rows)
    _augment_high_break(mod, all_rows)
    _assign_bins(all_rows, score_cutoffs, key="_bins")

    for r in all_rows:
        r["_entry_score"] = _trade_score(r, score_map)

    score5_rows = [r for r in all_rows if int(r.get("_entry_score") or 0) >= SCORE_GE5]
    score6_rows = [r for r in all_rows if int(r.get("_entry_score") or 0) >= SCORE_GE6]

    cohorts: dict[str, dict[str, Any]] = {}
    csv_rows: list[dict[str, Any]] = []

    for cohort_name, cohort_rows, min_score in (
        ("score_ge5", score5_rows, SCORE_GE5),
        ("score_ge6", score6_rows, SCORE_GE6),
    ):
        print(f"cohort {cohort_name} n={len(cohort_rows)}...", flush=True)
        baseline = _expectancy_metrics(cohort_rows)
        baseline_pf = float(baseline["profit_factor"] or 0)
        baseline_pnl = float(baseline["total_pnl_pct"] or 0)

        cohort_cuts = _build_tertiles(cohort_rows)
        for r in cohort_rows:
            r["_cohort_bins"] = {}
        _assign_bins(cohort_rows, cohort_cuts, key="_cohort_bins")

        usable = [lbl for lbl, _ in TERTILE_FEATURES if cohort_cuts.get(lbl, {}).get("usable")]
        if any((r.get("_cohort_bins") or {}).get("HBRecent") for r in cohort_rows):
            usable.append("HBRecent")

        print(f"  scanning {cohort_name} features={len(usable)}...", flush=True)
        candidates = _scan_cohort(
            cohort_rows,
            usable,
            baseline_pf=baseline_pf,
            baseline_pnl=baseline_pnl,
        )
        top20 = candidates[:20]
        top5 = candidates[:5]

        cohorts[cohort_name] = {
            "min_entry_score": min_score,
            "cohort_n": len(cohort_rows),
            "baseline": baseline,
            "within_cohort_tertile_cutoffs": cohort_cuts,
            "scan_feature_count": len(usable),
            "adopted_count": len(candidates),
            "adopted_top20": top20,
            "adopted_top5": top5,
            "all_adopted_clusters": candidates,
        }

        for c in top20:
            csv_rows.append({"cohort": cohort_name, **c})

    report = {
        "phase": 231,
        "mode": "score_cohort_internal_expectancy_discovery",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "hard_reject_forbidden": True,
            "full_population_rescan_forbidden": True,
            "loser_analysis_forbidden": True,
            "stop_analysis_forbidden": True,
            "expectancy_improvement_only": True,
        },
        "population": {
            "total_trades_loaded_for_score": len(all_rows),
            "score_ge5_cohort_n": len(score5_rows),
            "score_ge6_cohort_n": len(score6_rows),
            "note": "Interaction scan runs ONLY within score cohorts; not on full 2503.",
        },
        "method": {
            "score_source": "phase229 fixed score_map on phase228 tertiles",
            "cohort_binning": "tertile low/mid/high recomputed within each score cohort",
            "interactions": "2-way through 5-way",
            "min_n": MIN_N,
            "adoption_requires": (
                f"n>={MIN_N}, PF>cohort_baseline, total_pnl>cohort_baseline, IS_pf>1, OOS_pf>1"
            ),
            "ranking": "PF desc, total_pnl desc, n desc",
        },
        "cohorts": cohorts,
        "summary": {
            "score_ge5_adopted": cohorts["score_ge5"]["adopted_count"],
            "score_ge6_adopted": cohorts["score_ge6"]["adopted_count"],
            "score_ge5_best": cohorts["score_ge5"]["adopted_top5"][0] if cohorts["score_ge5"]["adopted_top5"] else None,
            "score_ge6_best": cohorts["score_ge6"]["adopted_top5"][0] if cohorts["score_ge6"]["adopted_top5"] else None,
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(csv_rows)
    print(
        f"wrote {OUT} ge5={cohorts['score_ge5']['adopted_count']} ge6={cohorts['score_ge6']['adopted_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
