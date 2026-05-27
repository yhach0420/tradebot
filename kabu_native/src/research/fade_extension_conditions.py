"""
Phase 122: Classify fade exits where +60s hold helps vs hurts; rule exploration (review only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_exit_replay import FADE_EXIT_REASONS, is_fade_trade, replay_trade_scenarios
from research.mfe_mae_exit_review import (
    as_float,
    build_price_timeline_from_events_csv,
    discover_sessions,
    load_structural_trades,
    parse_ts,
    session_end_ts_from_trades,
)

HOLD60_SCENARIO = "C_hold_60s"
IMPROVE_EPS = 0.01

CLUSTER_LABELS = {
    "A_improved_60s": "+60s improved",
    "B_no_improvement": "no improvement",
    "C_loss_expanded": "loss expanded",
}


def classify_hold60_outcome(baseline_pnl: float, hold60_pnl: float) -> str:
    delta = hold60_pnl - baseline_pnl
    loss_expanded = hold60_pnl < baseline_pnl and hold60_pnl < 0
    if loss_expanded:
        return "C_loss_expanded"
    if delta > IMPROVE_EPS:
        return "A_improved_60s"
    return "B_no_improvement"


def _build_sym_timelines(events_csv: Path) -> dict[str, list[tuple[float, dict[str, Any]]]]:
    by_sym: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    if not events_csv.is_file():
        return by_sym
    with events_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            et = str(row.get("event_type") or "")
            if et not in ("accepted", "candidate"):
                continue
            sym = str(row.get("symbol") or "").strip()
            ts = parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
            if sym and ts > 0:
                by_sym[sym].append((ts, dict(row)))
    for sym in by_sym:
        by_sym[sym].sort(key=lambda x: x[0])
    return by_sym


def _nearest_snapshot(
    by_sym: dict[str, list[tuple[float, dict[str, Any]]]],
    symbol: str,
    entry_ts: float,
    *,
    max_delta_sec: float = 15.0,
) -> Optional[dict[str, Any]]:
    items = by_sym.get(symbol)
    if not items:
        return None
    best: Optional[dict[str, Any]] = None
    best_d = 1e18
    for ts, row in items:
        d = abs(ts - entry_ts)
        if d <= max_delta_sec and d < best_d:
            best_d = d
            best = row
    return best


def _load_candidate_events(events_csv: Path) -> list[tuple[float, str, float]]:
    cands: list[tuple[float, str, float]] = []
    if not events_csv.is_file():
        return cands
    with events_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("event_type") or "") != "candidate":
                continue
            ts = parse_ts(str(row.get("event_time") or ""))
            q = as_float(row.get("continuation_quality_score"))
            if ts <= 0 or q is None:
                continue
            cands.append((ts, str(row.get("symbol") or ""), q))
    return cands


def _candidate_rank_at_entry(
    cands: Sequence[tuple[float, str, float]],
    symbol: str,
    entry_ts: float,
    *,
    window_sec: float = 120.0,
) -> Optional[int]:
    latest: dict[str, tuple[float, float]] = {}
    for ts, sym, q in cands:
        if abs(ts - entry_ts) > window_sec:
            continue
        if sym not in latest or ts >= latest[sym][0]:
            latest[sym] = (ts, q)
    if not latest:
        return None
    ranked = sorted(latest.items(), key=lambda x: x[1][1], reverse=True)
    for i, (sym, _) in enumerate(ranked, start=1):
        if sym == symbol:
            return i
    return len(ranked) + 1


def _overlap_replaced_before(
    trades: Sequence[Mapping[str, Any]],
    trade: Mapping[str, Any],
) -> bool:
    sym = str(trade.get("symbol") or "")
    entry_ts = parse_ts(str(trade.get("entry_time") or ""))
    for t in trades:
        if str(t.get("symbol") or "") != sym:
            continue
        close_ts = parse_ts(str(t.get("close_time") or ""))
        if close_ts >= entry_ts or entry_ts - close_ts > 120:
            continue
        if str(t.get("close_reason") or "") == "overlap_replaced_review":
            return True
    return False


def _mean_feature(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    vals = [as_float(r.get(key)) for r in rows]
    nums = [v for v in vals if v is not None]
    return round(statistics.mean(nums), 4) if nums else None


def _rate_bool(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    if not rows:
        return None
    trues = sum(1 for r in rows if r.get(key) in (True, "True", "true", 1, "1"))
    return round(trues / len(rows), 4)


def build_fade_cluster_rows(session_dirs: Sequence[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for sdir in session_dirs:
        sdir = Path(sdir)
        trades_raw = load_structural_trades(sdir / "structural_trades.csv")
        fade_trades = [t for t in trades_raw if is_fade_trade(t)]
        if not fade_trades:
            continue
        session_id = (
            str(sdir.relative_to(sdir.parent.parent))
            if sdir.parent.parent
            else sdir.name
        )
        events_csv = sdir / "small_paper_events.csv"
        sym_events = _build_sym_timelines(events_csv)
        cand_events = _load_candidate_events(events_csv)
        symbols = {str(t.get("symbol") or "") for t in fade_trades}
        tl_map = build_price_timeline_from_events_csv(events_csv, symbols)
        end_ts = session_end_ts_from_trades(trades_raw)

        for t in fade_trades:
            replay = replay_trade_scenarios(
                t,
                tl_map.get(str(t.get("symbol") or ""), []),
                session_end_ts=end_ts,
                session_id=session_id,
            )
            baseline = float(replay["A_current_pnl"])
            hold60 = float(replay[f"{HOLD60_SCENARIO}_pnl"])
            delta = round(hold60 - baseline, 4)
            cluster = classify_hold60_outcome(baseline, hold60)

            entry_ts = parse_ts(str(t.get("entry_time") or ""))
            sym = str(t.get("symbol") or "")
            snap = _nearest_snapshot(sym_events, sym, entry_ts)
            quality = as_float(t.get("continuation_quality_score"))
            vol_liq = None
            vwap_dist = None
            msg_idx = None
            if snap:
                quality = quality if quality is not None else as_float(snap.get("continuation_quality_score"))
                vol_liq = as_float(snap.get("daytrade_suitability_score"))
                msg_idx = as_float(snap.get("message_index"))
                qc_raw = snap.get("quality_components_json") or ""
                if qc_raw:
                    try:
                        qc = json.loads(qc_raw)
                        vwap_dist = as_float(qc.get("vwap_distance_pct") or qc.get("vwap_distance"))
                    except json.JSONDecodeError:
                        pass

            rank = _candidate_rank_at_entry(cand_events, sym, entry_ts)
            take_reached = replay.get("had_take_before_exit") in (True, "True", "true", 1, "1")
            overlap = _overlap_replaced_before(trades_raw, t)

            rows.append(
                {
                    "session_id": session_id,
                    "symbol": sym,
                    "entry_time": t.get("entry_time"),
                    "close_time": t.get("close_time"),
                    "exit_reason": t.get("close_reason"),
                    "cluster": cluster,
                    "cluster_label": CLUSTER_LABELS.get(cluster, cluster),
                    "pnl_at_exit": baseline,
                    "hold60_pnl": hold60,
                    "hold60_delta": delta,
                    "mfe_pct": as_float(t.get("mfe_pct")),
                    "mae_pct": as_float(t.get("mae_pct")),
                    "hold_sec": as_float(t.get("hold_duration_sec")),
                    "quality_score": quality,
                    "vol_liq_score": vol_liq,
                    "vwap_distance": vwap_dist,
                    "candidate_rank": rank,
                    "message_index": msg_idx,
                    "take_reached": take_reached,
                    "overlap_replaced": overlap,
                    "quality_tier": t.get("quality_tier"),
                    "session_bucket": t.get("session_bucket"),
                }
            )

    return rows


def compare_groups(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    improved = [r for r in rows if r.get("cluster") == "A_improved_60s"]
    worsened = [r for r in rows if float(r.get("hold60_delta") or 0) < -IMPROVE_EPS]
    loss_exp = [r for r in rows if r.get("cluster") == "C_loss_expanded"]

    def profile(name: str, grp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "group": name,
            "count": len(grp),
            "avg_mfe_pct": _mean_feature(grp, "mfe_pct"),
            "avg_mae_pct": _mean_feature(grp, "mae_pct"),
            "avg_quality_score": _mean_feature(grp, "quality_score"),
            "avg_vol_liq_score": _mean_feature(grp, "vol_liq_score"),
            "avg_hold_sec": _mean_feature(grp, "hold_sec"),
            "avg_pnl_at_exit": _mean_feature(grp, "pnl_at_exit"),
            "take_reached_rate": _rate_bool(grp, "take_reached"),
            "overlap_replaced_rate": _rate_bool(grp, "overlap_replaced"),
            "avg_hold60_delta": _mean_feature(grp, "hold60_delta"),
            "total_hold60_delta": round(sum(float(r.get("hold60_delta") or 0) for r in grp), 4),
        }

    return {
        "improved_A": profile("improved_A", improved),
        "worsened": profile("worsened", worsened),
        "loss_expanded_C": profile("loss_expanded_C", loss_exp),
        "no_improvement_B": profile("no_improvement_B", [r for r in rows if r.get("cluster") == "B_no_improvement"]),
    }


def _rule_mask(row: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    if rule.get("take_reached") is not None and bool(row.get("take_reached")) != rule["take_reached"]:
        return False
    if rule.get("overlap_replaced") is not None and bool(row.get("overlap_replaced")) != rule["overlap_replaced"]:
        return False
    for key, op, thr in (
        ("mfe_pct", "gt", rule.get("mfe_pct_gt")),
        ("quality_score", "gt", rule.get("quality_gt")),
        ("vol_liq_score", "gt", rule.get("vol_liq_gt")),
        ("hold_sec", "lt", rule.get("hold_sec_lt")),
        ("pnl_at_exit", "gt", rule.get("pnl_at_exit_gt")),
    ):
        if thr is None:
            continue
        val = as_float(row.get(key))
        if val is None:
            return False
        if op == "gt" and not (val > thr):
            return False
        if op == "lt" and not (val < thr):
            return False
    return True


def explore_rules(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    improved = {i for i, r in enumerate(rows) if r.get("cluster") == "A_improved_60s"}
    n_imp = len(improved)
    n = len(rows)
    if n == 0:
        return []

    candidates: list[dict[str, Any]] = []

    def add_rule(rule_id: str, desc: str, rule: dict[str, Any]) -> None:
        matched_idx = [i for i, r in enumerate(rows) if _rule_mask(r, rule)]
        if not matched_idx:
            return
        matched = [rows[i] for i in matched_idx]
        imp_in = sum(1 for i in matched_idx if i in improved)
        wors_in = sum(1 for r in matched if float(r.get("hold60_delta") or 0) < -IMPROVE_EPS)
        loss_in = sum(1 for r in matched if r.get("cluster") == "C_loss_expanded")
        total_delta = round(sum(float(r.get("hold60_delta") or 0) for r in matched), 4)
        baseline_delta = round(sum(float(rows[i].get("hold60_delta") or 0) for i in range(n)), 4)
        candidates.append(
            {
                "rule_id": rule_id,
                "description": desc,
                "rule": rule,
                "matched_count": len(matched),
                "matched_rate": round(len(matched) / n, 4),
                "improved_in_match": imp_in,
                "precision_improved": round(imp_in / len(matched), 4) if matched else None,
                "recall_improved": round(imp_in / n_imp, 4) if n_imp else None,
                "worsened_in_match": wors_in,
                "worsened_rate": round(wors_in / len(matched), 4) if matched else None,
                "loss_expanded_in_match": loss_in,
                "loss_expanded_rate": round(loss_in / len(matched), 4) if matched else None,
                "total_hold60_delta": total_delta,
                "avg_hold60_delta": round(total_delta / len(matched), 4),
                "net_vs_extend_all": round(total_delta - baseline_delta, 4),
            }
        )

    mfe_thresholds = [0.0, 0.1, 0.15, 0.2, 0.25, 0.3]
    quality_thresholds = [0.65, 0.68, 0.7, 0.72, 0.75, 0.78]
    vol_thresholds = [15, 20, 25, 30, 35, 40, 45]

    for mfe in mfe_thresholds:
        add_rule(f"mfe_gt_{mfe}", f"mfe_pct > {mfe}", {"mfe_pct_gt": mfe})

    for q in quality_thresholds:
        add_rule(f"quality_gt_{q}", f"quality_score > {q}", {"quality_gt": q})

    for vl in vol_thresholds:
        add_rule(f"vol_liq_gt_{vl}", f"vol_liq_score > {vl}", {"vol_liq_gt": vl})

    add_rule("take_reached", "take_reached = true", {"take_reached": True})
    add_rule("take_not_reached", "take_reached = false", {"take_reached": False})
    add_rule("overlap_true", "overlap_replaced = true", {"overlap_replaced": True})
    add_rule("overlap_false", "overlap_replaced = false", {"overlap_replaced": False})

    for mfe in (0.1, 0.15, 0.2, 0.25):
        add_rule(
            f"take_and_mfe_{mfe}",
            f"take_reached & mfe_pct > {mfe}",
            {"take_reached": True, "mfe_pct_gt": mfe},
        )
        add_rule(
            f"take_and_quality_0.72_mfe_{mfe}",
            f"take_reached & quality > 0.72 & mfe > {mfe}",
            {"take_reached": True, "quality_gt": 0.72, "mfe_pct_gt": mfe},
        )

    for q in (0.7, 0.72, 0.75):
        for vl in (25, 30, 35):
            add_rule(
                f"quality_{q}_vol_{vl}",
                f"quality > {q} & vol_liq > {vl}",
                {"quality_gt": q, "vol_liq_gt": vl},
            )

    for mfe in (0.15, 0.2):
        for q in (0.7, 0.72):
            add_rule(
                f"mfe_{mfe}_quality_{q}_no_overlap",
                f"mfe > {mfe} & quality > {q} & not overlap",
                {"mfe_pct_gt": mfe, "quality_gt": q, "overlap_replaced": False},
            )

    candidates.sort(
        key=lambda r: (
            float(r.get("total_hold60_delta") or -1e9),
            float(r.get("precision_improved") or 0),
        ),
        reverse=True,
    )
    return candidates


def determine_verdict(
    rows: Sequence[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    rules: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    n = len(rows)
    if n == 0:
        return "fade_extension_not_predictable", ["no fade trades"]

    imp = comparison.get("improved_A") or {}
    wor = comparison.get("worsened") or {}
    imp_take = float(imp.get("take_reached_rate") or 0)
    wor_take = float(wor.get("take_reached_rate") or 0)
    imp_q = float(imp.get("avg_quality_score") or 0)
    wor_q = float(wor.get("avg_quality_score") or 0)
    imp_mfe = float(imp.get("avg_mfe_pct") or 0)
    wor_mfe = float(wor.get("avg_mfe_pct") or 0)

    notes.append(
        f"n={n} improved={imp.get('count')} worsened={wor.get('count')} "
        f"take_rate imp={imp_take:.2f} wor={wor_take:.2f} mfe imp={imp_mfe:.3f} wor={wor_mfe:.3f}"
    )

    baseline_prec = (int(imp.get("count") or 0) / n) if n else 0.0
    notes.append(f"baseline_improved_rate={baseline_prec:.3f}")

    discriminative = [
        r
        for r in rules
        if float(r.get("matched_rate") or 0) <= 0.75
        and int(r.get("matched_count") or 0) >= 15
    ]
    pool = discriminative if discriminative else list(rules)
    top = pool[0] if pool else rules[0]
    take_rule = next((r for r in rules if r.get("rule_id") == "take_reached"), None)
    quality_rules = [
        r
        for r in rules
        if "quality" in str(r.get("rule_id", ""))
        and float(r.get("matched_rate") or 0) <= 0.75
        and int(r.get("matched_count") or 0) >= 15
    ]
    mfe_rules = [
        r
        for r in rules
        if str(r.get("rule_id", "")).startswith("mfe_gt")
        and float(r.get("matched_rate") or 0) <= 0.75
        and int(r.get("matched_count") or 0) >= 15
    ]

    take_signal = (
        take_rule is not None
        and float(take_rule.get("precision_improved") or 0) >= baseline_prec + 0.03
        and float(take_rule.get("worsened_rate") or 1) < 0.42
        and imp_take - wor_take >= 0.08
    )

    quality_vol_signal = False
    if quality_rules:
        qr = quality_rules[0]
        quality_vol_signal = (
            float(qr.get("precision_improved") or 0) >= baseline_prec + 0.03
            and float(qr.get("total_hold60_delta") or 0) > 0
        )

    promising = (
        float(top.get("precision_improved") or 0) >= baseline_prec + 0.05
        and float(top.get("worsened_rate") or 1) <= 0.42
        and float(top.get("total_hold60_delta") or 0) > 3.0
    )

    if not promising and not quality_vol_signal and not take_signal:
        best_mfe = mfe_rules[0] if mfe_rules else None
        if best_mfe and float(best_mfe.get("precision_improved") or 0) >= baseline_prec + 0.07:
            return "conditional_fade_extension_promising", notes + [
                f"best_discriminator={best_mfe.get('rule_id')} prec={best_mfe.get('precision_improved')} cov={best_mfe.get('matched_rate')}"
            ]
        return "fade_extension_not_predictable", notes + [
            f"best_rule={top.get('rule_id')} lacks separation vs baseline={baseline_prec:.3f}"
        ]

    if take_signal and take_rule:
        return "take_reached_is_key_signal", notes + [
            f"take_rule precision={take_rule.get('precision_improved')} worsened={take_rule.get('worsened_rate')}"
        ]

    if quality_vol_signal:
        return "quality_or_vol_liq_required", notes + [f"top_quality_rule={quality_rules[0].get('rule_id')}"]

    return "conditional_fade_extension_promising", notes + [
        f"best_rule={top.get('rule_id')} prec={top.get('precision_improved')} cov={top.get('matched_rate')}"
    ]


def analyze_fade_extension_conditions(session_dirs: Sequence[Path]) -> dict[str, Any]:
    rows = build_fade_cluster_rows(session_dirs)
    comparison = compare_groups(rows)
    rules = explore_rules(rows)
    verdict, notes = determine_verdict(rows, comparison, rules)

    cluster_counts = defaultdict(int)
    for r in rows:
        cluster_counts[str(r.get("cluster") or "")] += 1

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "fade_trade_count": len(rows),
        "cluster_counts": dict(cluster_counts),
        "group_comparison": comparison,
        "rule_candidates": rules[:40],
        "cluster_rows": rows,
    }
