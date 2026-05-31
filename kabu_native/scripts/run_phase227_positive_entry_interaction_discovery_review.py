#!/usr/bin/env python3
"""
Phase227: Positive entry interaction discovery review (review only).

Discover feature combinations associated with winning entry timing (not stop avoidance).
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase227_positive_entry_interaction_discovery_review.json"

LOOKBACK_SEC = 600.0
MIN_CANDIDATE_N = 30
MIN_STORE_N = 20
TV_MIN = 1e8
TURNOVER_MIN = 0.002

TERTILE_FEATURES: tuple[tuple[str, str], ...] = (
    ("VWAP", "entry_vwap_dev_pct"),
    ("Board", "entry_order_book_imbalance"),
    ("TV", "trading_value"),
    ("TickRatio", "tick_ratio_pct"),
    ("Quality", "continuation_quality_score"),
    ("Momentum", "momentum_continuation_score"),
    ("Duration", "max_continuation_duration"),
    ("RollingMFE", "rolling_mfe_pct"),
    ("RollingMAE", "rolling_mae_pct"),
    ("Rise5m", "entry_rise_5min_pct"),
    ("Rise10m", "entry_rise_10min_pct"),
    ("VolumeAccel", "volume_accel_30s_vs_prev30s"),
    ("HBCount", "high_break_count_full_jsonl"),
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
        entry_time = str(r.get("entry_time") or "")
        entry_ts = mod._parse_ts(entry_time)
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


def _label_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    by_pnl = sorted(rows, key=lambda r: float(r.get("pnl_pct") or 0))
    by_mfe = sorted(
        [r for r in rows if _float(r.get("mfe_pct")) is not None],
        key=lambda r: float(r.get("mfe_pct") or 0),
    )
    k20 = max(1, int(math.ceil(n * 0.20)))
    kmfe = max(1, int(math.ceil(len(by_mfe) * 0.20)))

    top_pnl = set(id(r) for r in by_pnl[-k20:])
    bot_pnl = set(id(r) for r in by_pnl[:k20])
    top_mfe = set(id(r) for r in by_mfe[-kmfe:])

    total_winners = 0
    total_strong = 0
    total_losers = 0
    for r in rows:
        pnl = float(r.get("pnl_pct") or 0)
        rid = id(r)
        r["_profitable"] = pnl > 0
        r["_loser"] = pnl < 0
        r["_strong_winner"] = rid in top_pnl
        r["_high_mfe_winner"] = rid in top_mfe
        r["_bottom20_pnl"] = rid in bot_pnl
        if r["_profitable"]:
            total_winners += 1
        if r["_strong_winner"]:
            total_strong += 1
        if r["_loser"]:
            total_losers += 1

    return {
        "total_winners": total_winners,
        "total_strong_winners": total_strong,
        "total_losers": total_losers,
        "top20_pnl_count": k20,
        "top20_mfe_count": kmfe,
    }


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
        if r.get("entry_time_bucket"):
            bins["TimeBucket"] = str(r.get("entry_time_bucket"))
        r["_bins"] = bins


def _cluster_key(r: dict[str, Any], feat_labels: tuple[str, ...]) -> Optional[tuple[str, ...]]:
    bins = r.get("_bins") or {}
    if not all(lbl in bins for lbl in feat_labels):
        return None
    return tuple(f"{lbl}:{bins[lbl]}" for lbl in feat_labels)


def _cluster_metrics(
    members: list[dict[str, Any]],
    label_stats: dict[str, Any],
    baseline_stop: float,
) -> dict[str, Any]:
    if not members:
        return {"n": 0}
    pnls = [float(r.get("pnl_pct") or 0) for r in members]
    mfes = [_float(r.get("mfe_pct")) for r in members]
    mfes_ok = [m for m in mfes if m is not None]
    n = len(members)
    stops = sum(1 for r in members if r.get("stop_hit"))
    wins = sum(1 for p in pnls if p > 0)
    win_pnls = [p for p in pnls if p > 0]
    lose_pnls = [p for p in pnls if p < 0]
    prof = sum(1 for r in members if r.get("_profitable"))
    strong = sum(1 for r in members if r.get("_strong_winner"))
    losers = sum(1 for r in members if r.get("_loser"))
    pf = _pf(pnls)
    is_rows = [r for r in members if r.get("split") == "in_sample"]
    oos_rows = [r for r in members if r.get("split") == "oos"]
    is_pnls = [float(r.get("pnl_pct") or 0) for r in is_rows]
    oos_pnls = [float(r.get("pnl_pct") or 0) for r in oos_rows]
    is_pf = _pf(is_pnls) if is_pnls else None
    oos_pf = _pf(oos_pnls) if oos_pnls else None
    rr = None
    if win_pnls and lose_pnls:
        aw = sum(win_pnls) / len(win_pnls)
        al = abs(sum(lose_pnls) / len(lose_pnls))
        rr = round(aw / al, 4) if al > 0 else None

    tw = max(1, label_stats["total_winners"])
    ts = max(1, label_stats["total_strong_winners"])
    return {
        "n": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(wins / n, 4),
        "avg_mfe_pct": round(statistics.mean(mfes_ok), 4) if mfes_ok else None,
        "stop_hit_rate": round(stops / n, 4),
        "stop_below_baseline": (stops / n) < baseline_stop,
        "winner_capture_count": prof,
        "strong_winner_capture_count": strong,
        "loser_inclusion_count": losers,
        "precision_winner": round(prof / n, 4),
        "recall_winner": round(prof / tw, 4),
        "precision_strong_winner": round(strong / n, 4),
        "recall_strong_winner": round(strong / ts, 4),
        "loser_inclusion_rate": round(losers / n, 4),
        "risk_reward_avg": rr,
        "IS": {"n": len(is_rows), "profit_factor": is_pf, "total_pnl_pct": round(sum(is_pnls), 4) if is_pnls else 0.0},
        "OOS": {"n": len(oos_rows), "profit_factor": oos_pf, "total_pnl_pct": round(sum(oos_pnls), 4) if oos_pnls else 0.0},
        "is_oos_both_pf_gt_1": (
            is_pf is not None
            and oos_pf is not None
            and is_pf > 1
            and oos_pf > 1
            and len(is_rows) >= 10
            and len(oos_rows) >= 10
        ),
    }


def _slim_cluster(cluster: str, features: list[str], m: dict[str, Any]) -> dict[str, Any]:
    return {
        "cluster": cluster,
        "features": features,
        **{k: m[k] for k in m if k not in ("IS", "OOS")},
        "IS_pf": m.get("IS", {}).get("profit_factor"),
        "IS_n": m.get("IS", {}).get("n"),
        "OOS_pf": m.get("OOS", {}).get("profit_factor"),
        "OOS_n": m.get("OOS", {}).get("n"),
        "is_oos_both_pf_gt_1": m.get("is_oos_both_pf_gt_1"),
    }


def _is_candidate(m: dict[str, Any], baseline_stop: float) -> bool:
    if m.get("n", 0) < MIN_CANDIDATE_N:
        return False
    pf = m.get("profit_factor")
    if pf is None or pf <= 1:
        return False
    if m.get("total_pnl_pct", 0) <= 0:
        return False
    if (m.get("stop_hit_rate") or 0) >= baseline_stop:
        return False
    return True


def _is_overfit(m: dict[str, Any]) -> bool:
    n = m.get("n", 0)
    pf = m.get("profit_factor")
    is_pf = m.get("IS", {}).get("profit_factor")
    oos_pf = m.get("OOS", {}).get("profit_factor")
    if n < MIN_CANDIDATE_N and pf is not None and pf > 1.5:
        return True
    if is_pf is not None and is_pf > 1 and oos_pf is not None and oos_pf < 0.9:
        return True
    if is_pf is not None and is_pf > 1.3 and oos_pf is not None and oos_pf <= 1:
        return True
    return False


def _scan_interactions(
    rows: list[dict[str, Any]],
    feat_labels: list[str],
    label_stats: dict[str, Any],
    baseline_stop: float,
    arity: int,
) -> list[dict[str, Any]]:
    usable = [f for f in feat_labels if f != "TimeBucket" and f != "HBRecent"]
    usable = [f for f in usable if f in {t[0] for t in TERTILE_FEATURES}]
    results: list[dict[str, Any]] = []
    for combo in itertools.combinations(usable, arity):
        eligible = [r for r in rows if _cluster_key(r, combo) is not None]
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for r in eligible:
            key = _cluster_key(r, combo)
            assert key is not None
            groups[key].append(r)
        for key, members in groups.items():
            m = _cluster_metrics(members, label_stats, baseline_stop)
            if m["n"] < MIN_STORE_N:
                continue
            results.append(
                {
                    "cluster": " & ".join(key),
                    "features": list(combo),
                    "combo_key": "+".join(combo),
                    **m,
                }
            )
    return results


def _eval_hypothesis(
    rows: list[dict[str, Any]],
    name: str,
    pred: Callable[[dict[str, Any]], bool],
    label_stats: dict[str, Any],
    baseline_stop: float,
    p217: Any,
) -> dict[str, Any]:
    members = [r for r in rows if pred(r)]
    m = _cluster_metrics(members, label_stats, baseline_stop)
    return {
        "hypothesis": name,
        "cluster": name,
        **_slim_cluster(name, [name], m),
        "is_candidate": _is_candidate(m, baseline_stop),
        "is_overfit": _is_overfit(m),
    }


def _rank_score(m: dict[str, Any]) -> float:
    pf = m.get("profit_factor") or 0
    return (
        (pf - 1) * 3
        + m.get("total_pnl_pct", 0) / 20
        + m.get("recall_winner", 0) * 5
        + m.get("recall_strong_winner", 0) * 3
        - m.get("loser_inclusion_rate", 0) * 2
        + (1 if m.get("is_oos_both_pf_gt_1") else 0) * 2
    )


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_module("phase217_loader_p227", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py")
    p221 = _load_module("phase221_loader_p227", "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py")
    mod = p217._load_phase213c_module()

    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    print("augmenting features...", flush=True)
    p221._augment_early_features(mod, rows)
    print("augmenting high_break (full jsonl)...", flush=True)
    _augment_high_break(mod, rows)

    label_stats = _label_rows(rows)
    baseline_stop = sum(1 for r in rows if r.get("stop_hit")) / len(rows)
    cuts = _build_tertiles(rows)
    _assign_bins(rows, cuts)

    def b(r: dict[str, Any], lbl: str) -> Optional[str]:
        return (r.get("_bins") or {}).get(lbl)

    def low_liq_pass(row: dict[str, Any]) -> bool:
        return not p217._low_liq_reject(row)

    hypotheses = [
        (
            "low_vwap_board_high_momentum_high",
            lambda r: b(r, "VWAP") == "low" and b(r, "Board") == "high" and b(r, "Momentum") == "high",
        ),
        (
            "low_vwap_short_duration_hb_recent_yes",
            lambda r: b(r, "VWAP") == "low"
            and b(r, "Duration") == "low"
            and b(r, "HBRecent") == "yes",
        ),
        (
            "tv_midplus_board_high_tickratio_low",
            lambda r: b(r, "TV") in ("mid", "high")
            and b(r, "Board") == "high"
            and b(r, "TickRatio") == "low",
        ),
        (
            "rollingmfe_lowmid_momentum_high_board_high",
            lambda r: b(r, "RollingMFE") in ("low", "mid")
            and b(r, "Momentum") == "high"
            and b(r, "Board") == "high",
        ),
        (
            "rise5m_lowmid_hb_recent_board_high",
            lambda r: b(r, "Rise5m") in ("low", "mid")
            and b(r, "HBRecent") == "yes"
            and b(r, "Board") == "high",
        ),
        (
            "low_liq_pass_vwap_low_board_high_momentum_high",
            lambda r: low_liq_pass(r)
            and b(r, "VWAP") == "low"
            and b(r, "Board") == "high"
            and b(r, "Momentum") == "high",
        ),
    ]

    print("scanning 2-way...", flush=True)
    all_2 = _scan_interactions(rows, [f[0] for f in TERTILE_FEATURES], label_stats, baseline_stop, 2)
    print("scanning 3-way...", flush=True)
    all_3 = _scan_interactions(rows, [f[0] for f in TERTILE_FEATURES], label_stats, baseline_stop, 3)
    print("scanning 4-way...", flush=True)
    all_4 = _scan_interactions(rows, [f[0] for f in TERTILE_FEATURES], label_stats, baseline_stop, 4)

    all_clusters = all_2 + all_3 + all_4
    hyp_results = [_eval_hypothesis(rows, name, pred, label_stats, baseline_stop, p217) for name, pred in hypotheses]

    candidates = [c for c in all_clusters if _is_candidate(c, baseline_stop)]
    candidates.sort(key=_rank_score, reverse=True)

    is_oos_stable = [c for c in all_clusters if c.get("is_oos_both_pf_gt_1")]
    is_oos_stable.sort(key=lambda c: c.get("total_pnl_pct", 0), reverse=True)

    overfit = [c for c in all_clusters if _is_overfit(c)]
    overfit.sort(key=lambda c: (c.get("profit_factor") or 0), reverse=True)

    top10 = candidates[:10]
    if len(top10) < 10:
        extra = [c for c in all_clusters if c.get("profit_factor") and c["profit_factor"] > 1 and c["n"] >= MIN_CANDIDATE_N]
        extra.sort(key=_rank_score, reverse=True)
        seen = {c["cluster"] for c in top10}
        for c in extra:
            if c["cluster"] not in seen:
                top10.append(_slim_cluster(c["cluster"], c["features"], c))
                seen.add(c["cluster"])
            if len(top10) >= 10:
                break
    else:
        top10 = [_slim_cluster(c["cluster"], c["features"], c) for c in top10]

    practical = [
        _slim_cluster(c["cluster"], c["features"], c)
        for c in candidates
        if c.get("recall_winner", 0) >= 0.02 or c.get("strong_winner_capture_count", 0) >= 3
    ][:25]

    shadow_features = [
        {
            "feature": "high_break_recent_recomputed",
            "reason": "Appears in positive hypotheses; full-jsonl coverage 100% vs logged 8.7%.",
            "priority": "high",
        },
        {
            "feature": "high_break_count_full_jsonl",
            "reason": "Entry-aligned recompute; Phase223 showed Phase221 ring under-counts.",
            "priority": "high",
        },
        {
            "feature": "entry_rise_5min_pct",
            "reason": "Phase221/224 signal for early momentum; log coverage sparse without push ring fix.",
            "priority": "medium",
        },
        {
            "feature": "tick_ratio_pct",
            "reason": "Only 15% coverage; high lift in Phase225 stop clusters — log consistently for positive discovery.",
            "priority": "medium",
        },
        {
            "feature": "momentum_continuation_score_x_board_imbalance",
            "reason": "Repeated in top PF>1 hypothesis combos (board+momentum interactions).",
            "priority": "medium",
        },
        {
            "feature": "max_continuation_duration",
            "reason": "100% coverage; short-duration leg appears in positive early-momentum hypotheses.",
            "priority": "low",
        },
    ]

    # Loser vs winner comparison on tertile features
    winners = [r for r in rows if r.get("_profitable")]
    losers = [r for r in rows if r.get("_loser")]
    bottom20 = [r for r in rows if r.get("_bottom20_pnl")]
    strong = [r for r in rows if r.get("_strong_winner")]

    def _feat_compare(field: str) -> dict[str, Any]:
        def med(rs: list[dict]) -> Optional[float]:
            xs = [_float(r.get(field)) for r in rs]
            xs = [x for x in xs if x is not None]
            return round(statistics.median(xs), 4) if xs else None

        return {
            "field": field,
            "strong_winner_median": med(strong),
            "profitable_median": med(winners),
            "loser_median": med(losers),
            "bottom20_median": med(bottom20),
        }

    report = {
        "phase": 227,
        "mode": "positive_entry_interaction_discovery_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
            "no_per_day_tuning": True,
            "fixed_scenario_comparison_only": True,
        },
        "labels": {
            "A_profitable": "pnl_pct > 0",
            "B_strong_winner": "pnl top 20%",
            "C_high_mfe_winner": "mfe_pct top 20%",
            "compare_loser": "pnl_pct < 0",
            "compare_bottom20_pnl": "pnl bottom 20%",
            **label_stats,
        },
        "population": {
            "total_trades": len(rows),
            "baseline_stop_hit_rate": round(baseline_stop, 4),
            "baseline_pf": _pf([float(r.get("pnl_pct") or 0) for r in rows]),
            "baseline_total_pnl_pct": round(sum(float(r.get("pnl_pct") or 0) for r in rows), 4),
        },
        "tertile_cutoffs": cuts,
        "method": {
            "binning": "tertile low/mid/high; HBRecent yes/no; TimeBucket discrete",
            "interactions": "2-way, 3-way, 4-way on tertile features",
            "min_candidate_n": MIN_CANDIDATE_N,
            "candidate_criteria": "n>=30, PF>1, total_pnl>0, stop_rate<baseline",
            "is_oos_stable_criteria": "IS and OOS each n>=10 and PF>1",
        },
        "winner_vs_loser_feature_medians": [
            _feat_compare(f) for _, f in TERTILE_FEATURES
        ],
        "hypothesis_verification": hyp_results,
        "interaction_scan_stats": {
            "two_way_clusters_n_ge_20": len(all_2),
            "three_way_clusters_n_ge_20": len(all_3),
            "four_way_clusters_n_ge_20": len(all_4),
            "candidate_clusters": len(candidates),
            "is_oos_stable_clusters": len(is_oos_stable),
        },
        "1_top_10_positive_entry_clusters": top10,
        "2_is_oos_both_pf_gt_1_clusters": [_slim_cluster(c["cluster"], c["features"], c) for c in is_oos_stable[:30]],
        "3_practical_candidate_clusters": practical,
        "4_overfit_or_unusable_clusters": [_slim_cluster(c["cluster"], c["features"], c) for c in overfit[:30]],
        "5_shadow_logging_recommendations": shadow_features,
        "notes": [
            "Positive entry discovery — not stop-cluster exclusion (contrast Phase225/226).",
            "HBCount/HBRecent from entry-aligned full push jsonl (Phase223 method).",
            "Rise5m/TickRatio tertiles limited by field coverage.",
            "IS/OOS split from Phase213c session lists on row split field.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"wrote {OUT} candidates={len(candidates)} is_oos_stable={len(is_oos_stable)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
