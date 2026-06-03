#!/usr/bin/env python3
"""
Phase229: Entry score discovery (review only).

Build market-wide ENTRY score from Phase228 IS/OOS-positive adoption candidates.
Expectancy maximization only — no cluster/stop/winner hunting.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
P228 = REPO / "kabu_native/results/reports/phase228_entry_expectancy_discovery.json"
OUT = REPO / "kabu_native/results/reports/phase229_entry_score_discovery.json"

LOOKBACK_SEC = 600.0
MIN_THRESHOLD_N = 200

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


def _parse_cluster_tokens(cluster: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in cluster.split(" & "):
        part = part.strip()
        if ":" not in part:
            continue
        lbl, level = part.split(":", 1)
        out.append((lbl, level))
    return out


def _fmt_num(val: float, label: str) -> str:
    if label == "TV":
        oku = val / 1e8
        return f"{oku:.1f}億"
    if label in ("RollingMAE", "RollingMFE", "VWAP", "Rise5m", "Rise10m", "TickRatio"):
        return f"{val * 100:.4f}%"
    if label in ("Board", "Momentum", "Quality"):
        return f"{val:.4f}"
    if label == "Duration":
        return f"{val:.0f}s"
    if label == "HBCount":
        return f"{val:.0f}"
    if label == "Price":
        return f"{val:.0f}"
    return f"{val:.6g}"


def _range_for_bin(label: str, level: str, cuts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if label == "HBRecent":
        return {"token": f"{label}:{level}", "range_display": level, "range_type": "boolean"}
    info = cuts.get(label)
    if not info or not info.get("usable"):
        return {"token": f"{label}:{level}", "range_display": "n/a", "range_type": "missing"}
    p33, p66 = info["p33"], info["p66"]
    field = info["field"]
    if level == "low":
        if label == "HBCount":
            lo_s, hi_s = "0", _fmt_num(p33, label)
        else:
            lo_s, hi_s = "min", _fmt_num(p33, label)
        lo_v, hi_v = None, p33
    elif level == "mid":
        lo_s, hi_s = _fmt_num(p33, label), _fmt_num(p66, label)
        lo_v, hi_v = p33, p66
    else:
        lo_s, hi_s = _fmt_num(p66, label), "max"
        lo_v, hi_v = p66, None
    return {
        "token": f"{label}:{level}",
        "field": field,
        "bin": level,
        "range_lo": lo_v,
        "range_hi": hi_v,
        "range_display": f"{lo_s}～{hi_s}",
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_hit_rate": None,
            "IS_pf": None,
            "OOS_pf": None,
            "qualified_n_ge_200": False,
        }
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    n = len(rows)
    stops = sum(1 for r in rows if r.get("stop_hit"))
    wins = sum(1 for p in pnls if p > 0)
    is_pnls = [float(r.get("pnl_pct") or 0) for r in rows if r.get("split") == "in_sample"]
    oos_pnls = [float(r.get("pnl_pct") or 0) for r in rows if r.get("split") == "oos"]
    return {
        "n": n,
        "profit_factor": _pf(pnls),
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(wins / n, 4),
        "stop_hit_rate": round(stops / n, 4),
        "IS_n": len(is_pnls),
        "IS_pf": _pf(is_pnls) if is_pnls else None,
        "OOS_n": len(oos_pnls),
        "OOS_pf": _pf(oos_pnls) if oos_pnls else None,
        "qualified_n_ge_200": n >= MIN_THRESHOLD_N,
    }


def _trade_score(r: dict[str, Any], components: dict[str, int]) -> int:
    bins = r.get("_bins") or {}
    total = 0
    for lbl in TARGET_FEATURES:
        if lbl not in bins:
            continue
        token = f"{lbl}:{bins[lbl]}"
        total += components.get(token, 0)
    return total


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p228 = json.loads(P228.read_text(encoding="utf-8"))
    cuts = p228["tertile_cutoffs"]
    adoption = p228["3_is_oos_both_pf_gt_1_clusters"]

    p217 = _load_module("phase217_loader_p229", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py")
    p221 = _load_module("phase221_loader_p229", "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py")
    mod = p217._load_phase213c_module()

    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    print("augmenting...", flush=True)
    p221._augment_early_features(mod, rows)
    _augment_high_break(mod, rows)
    _assign_bins(rows, cuts)

    # Work 1: numeric ranges per adoption candidate
    candidate_ranges: list[dict[str, Any]] = []
    token_counter: Counter[str] = Counter()

    for cand in adoption:
        tokens = _parse_cluster_tokens(cand["cluster"])
        feature_ranges: list[dict[str, Any]] = []
        for lbl, level in tokens:
            token = f"{lbl}:{level}"
            if lbl in TARGET_FEATURES:
                token_counter[token] += 1
            feature_ranges.append(_range_for_bin(lbl, level, cuts))
        candidate_ranges.append(
            {
                "cluster": cand["cluster"],
                "n": cand["n"],
                "profit_factor": cand["profit_factor"],
                "total_pnl_pct": cand["total_pnl_pct"],
                "IS_pf": cand.get("IS_pf"),
                "OOS_pf": cand.get("OOS_pf"),
                "feature_ranges": feature_ranges,
            }
        )

    # Work 2: frequency table (target features only)
    feature_frequency_table = [
        {
            "token": tok,
            "count": cnt,
            "share_of_adoption_appearances": round(
                cnt / max(1, sum(token_counter.values())), 4
            ),
        }
        for tok, cnt in token_counter.most_common()
    ]

    # Work 3: score components from frequency ranks
    ranked_tokens = sorted(token_counter.items(), key=lambda x: (-x[1], x[0]))
    n_tok = len(ranked_tokens)
    top20_n = max(1, math.ceil(n_tok * 0.2))
    top50_n = max(1, math.ceil(n_tok * 0.5))

    score_components: dict[str, int] = {}
    score_component_detail: list[dict[str, Any]] = []
    for i, (tok, cnt) in enumerate(ranked_tokens):
        if i < top20_n:
            pts = 2
            tier = "top_20pct_frequency"
        elif i < top50_n:
            pts = 1
            tier = "top_50pct_frequency"
        else:
            pts = 0
            tier = "below_top_50pct"
        score_components[tok] = pts
        if pts > 0:
            score_component_detail.append(
                {
                    "token": tok,
                    "points": pts,
                    "frequency_count": cnt,
                    "frequency_rank": i + 1,
                    "tier": tier,
                }
            )

    for r in rows:
        r["_entry_score"] = _trade_score(r, score_components)

    # Work 4: threshold sweep
    threshold_results: list[dict[str, Any]] = []
    for min_score in range(1, 7):
        subset = [r for r in rows if int(r.get("_entry_score") or 0) >= min_score]
        m = _metrics(subset)
        threshold_results.append(
            {
                "min_score": min_score,
                **m,
                "disqualified": not m["qualified_n_ge_200"],
            }
        )

    qualified = [t for t in threshold_results if t["qualified_n_ge_200"]]
    best_pf = max(
        (t for t in qualified if t.get("profit_factor") is not None),
        key=lambda t: (t.get("profit_factor") or 0, t.get("total_pnl_pct") or 0),
        default=None,
    )
    best_pnl = max(
        qualified,
        key=lambda t: (t.get("total_pnl_pct") or 0, t.get("profit_factor") or 0),
        default=None,
    )

    report = {
        "phase": 229,
        "mode": "entry_score_discovery",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "expectancy_only": True,
            "min_threshold_n": MIN_THRESHOLD_N,
        },
        "input": {
            "phase228_json": str(P228),
            "adoption_candidate_count": len(adoption),
            "source": "3_is_oos_both_pf_gt_1_clusters",
        },
        "target_features": sorted(TARGET_FEATURES),
        "population": {"total_trades": len(rows)},
        "work1_candidate_numeric_ranges": candidate_ranges,
        "work2_feature_frequency_table": feature_frequency_table,
        "work3_score_components": {
            "method": "+2 top 20% frequency among target tokens; +1 top 21-50%; else 0",
            "unique_target_tokens": n_tok,
            "components": score_component_detail,
            "score_map": {k: v for k, v in score_components.items() if v > 0},
        },
        "work4_threshold_evaluation": threshold_results,
        "summary": {
            "best_qualified_by_pf": best_pf,
            "best_qualified_by_total_pnl": best_pnl,
            "qualified_thresholds": [t["min_score"] for t in qualified if (t.get("profit_factor") or 0) > 1],
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} tokens={n_tok} qualified={len(qualified)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
