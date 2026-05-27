#!/usr/bin/env python3
"""
Phase 82: Daytrade suitability selection what-if (read-only diagnosis).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]

SESSIONS_DEFAULT = [
    "20260519/live_full_session_081047",
    "20260520/push_replay_231314",
]

QUALITY_GATE = 0.70


def _bootstrap() -> None:
    native = ROOT / "kabu_native"
    for p in (native / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _parse_ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def load_structural_trades(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_events_jsonl(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "small_paper_events.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def push_dir_for_session(session_dir: Path) -> Optional[Path]:
    day = session_dir.parent.name
    if len(day) == 8 and day.isdigit():
        return ROOT / "kabu_native/data/push_jsonl" / f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    return None


def load_enriched_push_series(
    push_dir: Path,
    symbols: set[str],
) -> dict[str, list[tuple[float, dict[str, Optional[float]]]]]:
    from small_paper.accepted_liquidity_metrics import metrics_from_payload
    from small_paper.daytrade_suitability import enrich_daytrade_metrics

    out: dict[str, list[tuple[float, dict[str, Optional[float]]]]] = {}
    for sym in symbols:
        path = push_dir / f"{sym}.jsonl"
        if not path.is_file():
            continue
        series: list[tuple[float, dict[str, Optional[float]]]] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = rec.get("payload") or {}
                ts = _parse_ts(rec.get("recorded_at") or "")
                px = _float(payload.get("CurrentPrice")) or _float(payload.get("CalcPrice")) or 0.0
                base = metrics_from_payload(payload, entry_price=px)
                series.append((ts, enrich_daytrade_metrics(base, payload)))
        series.sort(key=lambda x: x[0])
        out[sym] = series
    return out


def build_trade_metric_rows(
    trades: Sequence[Mapping[str, Any]],
    series_map: Mapping[str, Sequence[tuple[float, dict[str, Optional[float]]]]],
) -> list[dict[str, Any]]:
    from small_paper.accepted_liquidity_metrics import lookup_metrics_at_entry
    from small_paper.daytrade_suitability import attach_composite_scores, tier_label, tier_ja

    rows: list[dict[str, Any]] = []
    for t in trades:
        sym = str(t.get("symbol") or "")
        ent = str(t.get("entry_time") or "")
        ent_ts = _parse_ts(ent)
        m = lookup_metrics_at_entry(series_map.get(sym, []), ent_ts)
        pnl = _float(t.get("realized_pnl_pct"))
        q = _float(t.get("continuation_quality_score"))
        mc = m.get("market_cap_jpy")
        rows.append(
            {
                "symbol": sym,
                "entry_time": ent,
                "exit_time": str(t.get("close_time") or t.get("exit_time") or ""),
                "realized_pnl_pct": pnl,
                "trade_outcome": "win" if (pnl or 0) > 0 else ("loss" if (pnl or 0) < 0 else "flat"),
                "continuation_quality_score": q,
                "close_reason": t.get("close_reason"),
                "market_cap_tier": tier_label(mc),
                "tier_ja": tier_ja(tier_label(mc)),
                **{k: v for k, v in m.items()},
            }
        )
    attach_composite_scores(rows)
    return rows


def correlation_rows(trade_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    from small_paper.daytrade_suitability import pearson

    indicators = (
        "intraday_range_pct",
        "atr_pct",
        "trading_value_jpy",
        "trading_volume",
        "turnover_proxy",
        "liquidity_score",
        "volatility_liquidity_score",
        "daytrade_suitability_score",
        "continuation_quality_score",
    )
    pnls = [float(r["realized_pnl_pct"]) for r in trade_rows if r.get("realized_pnl_pct") is not None]
    wins = [1.0 if float(r["realized_pnl_pct"]) > 0 else 0.0 for r in trade_rows if r.get("realized_pnl_pct") is not None]
    out: list[dict[str, Any]] = []
    for ind in indicators:
        pairs = [
            (r, float(r[ind]))
            for r in trade_rows
            if r.get(ind) is not None and r.get("realized_pnl_pct") is not None
        ]
        if len(pairs) < 5:
            continue
        xs = [p[1] for p in pairs]
        ys = [float(p[0]["realized_pnl_pct"]) for p in pairs]
        ws = [1.0 if y > 0 else 0.0 for y in ys]
        out.append(
            {
                "indicator": ind,
                "n": len(pairs),
                "corr_pnl_pct": pearson(xs, ys),
                "corr_win": pearson(xs, ws),
                "mean_indicator": round(statistics.mean(xs), 4),
            }
        )
    return out


def quartile_pf_rows(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> list[dict[str, Any]]:
    from small_paper.daytrade_suitability import profit_factor, summarize_trades

    specs = (
        ("atr_pct", "ATR_top25"),
        ("intraday_range_pct", "range_top25"),
        ("trading_value_jpy", "trading_value_top25"),
        ("volatility_liquidity_score", "vol_liq_top25"),
        ("daytrade_suitability_score", "suitability_top25"),
    )
    rows: list[dict[str, Any]] = []
    for field, label in specs:
        vals = [float(r[field]) for r in trade_rows if r.get(field) is not None]
        if not vals:
            continue
        cutoff = statistics.quantiles(vals, n=4)[-1] if len(vals) >= 4 else max(vals)
        top = [r for r in trade_rows if r.get(field) is not None and float(r[field]) >= cutoff]
        bot = [r for r in trade_rows if r.get(field) is not None and float(r[field]) < cutoff]
        for subset, q in ((top, "top25"), (bot, "bottom75")):
            s = summarize_trades(subset)
            rows.append(
                {
                    "session_id": session_id,
                    "quartile_rule": label,
                    "subset": q,
                    "cutoff": round(cutoff, 4),
                    **s,
                }
            )
    return rows


def threshold_catalog(trade_rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    from small_paper.daytrade_suitability import percentile_value

    def vals(field: str) -> list[float]:
        return [float(r[field]) for r in trade_rows if r.get(field) is not None]

    atr_v = vals("atr_pct")
    range_v = vals("intraday_range_pct")
    tv_v = vals("trading_value_jpy")
    suit_v = vals("daytrade_suitability_score")
    vol_v = vals("volatility_liquidity_score")
    return {
        "atr_pct": {
            "top25": percentile_value(atr_v, 0.75),
            "top50": percentile_value(atr_v, 0.50),
            "ge_3": 3.0,
            "ge_5": 5.0,
        },
        "intraday_range_pct": {
            "top25": percentile_value(range_v, 0.75),
            "top50": percentile_value(range_v, 0.50),
            "ge_3": 3.0,
            "ge_5": 5.0,
        },
        "trading_value_jpy": {
            "top25": percentile_value(tv_v, 0.75),
            "top50": percentile_value(tv_v, 0.50),
        },
        "volatility_liquidity_score": {
            "top25": percentile_value(vol_v, 0.75),
            "top50": percentile_value(vol_v, 0.50),
        },
        "daytrade_suitability_score": {
            "top25": percentile_value(suit_v, 0.75),
            "top50": percentile_value(suit_v, 0.50),
        },
    }


def filter_quality(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in rows
        if (_float(r.get("continuation_quality_score")) or 0) >= QUALITY_GATE
    ]


def simulate_cap3(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(trades, key=lambda t: _parse_ts(str(t.get("entry_time") or "")))
    open_slots: list[tuple[float, str]] = []
    kept: list[dict[str, Any]] = []
    for t in ordered:
        sym = str(t.get("symbol") or "")
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or "")) or ent + 3600
        open_slots = [(a, b, s) for a, b, s in open_slots if b > ent]
        if len(open_slots) >= 3:
            continue
        open_slots.append((ent, ex, sym))
        kept.append(dict(t))
    return kept


def simulate_cap3_suitability_rank(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Chronological entries; when cap full skip, else prefer higher suitability among passing."""
    ordered = sorted(trades, key=lambda t: _parse_ts(str(t.get("entry_time") or "")))
    open_slots: list[tuple[float, float, str, float]] = []
    kept: list[dict[str, Any]] = []
    for t in ordered:
        sym = str(t.get("symbol") or "")
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or "")) or ent + 3600
        suit = _float(t.get("daytrade_suitability_score")) or 0.0
        open_slots = [x for x in open_slots if x[1] > ent]
        if len(open_slots) < 3:
            open_slots.append((ent, ex, sym, suit))
            kept.append(dict(t))
            continue
        worst = min(open_slots, key=lambda x: x[3])
        if suit > worst[3]:
            open_slots.remove(worst)
            open_slots.append((ent, ex, sym, suit))
            kept.append(dict(t))
    return kept


def policy_impact(
    baseline: Sequence[Mapping[str, Any]],
    kept: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_set = {(t["symbol"], t["entry_time"]) for t in baseline}
    kept_set = {(t["symbol"], t["entry_time"]) for t in kept}
    dropped = base_set - kept_set
    missed_winners = 0
    avoided_losers = 0
    for t in baseline:
        key = (t["symbol"], t["entry_time"])
        if key not in dropped:
            continue
        pnl = _float(t.get("realized_pnl_pct")) or 0.0
        if pnl > 0:
            missed_winners += 1
        elif pnl < 0:
            avoided_losers += 1
    return {
        "rejected_by_suitability": len(dropped),
        "missed_winners": missed_winners,
        "avoided_losers": avoided_losers,
    }


def eval_policy(
    policy_id: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
    baseline: Sequence[Mapping[str, Any]],
    threshold_note: str = "",
) -> dict[str, Any]:
    from small_paper.daytrade_suitability import summarize_trades

    summary = summarize_trades(trades)
    impact = policy_impact(baseline, trades)
    return {
        "session_id": session_id,
        "policy_id": policy_id,
        "threshold_note": threshold_note,
        **summary,
        **impact,
    }


def build_policy_grid(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> list[dict[str, Any]]:
    from small_paper.daytrade_suitability import summarize_trades

    qrows = filter_quality(trade_rows)
    baseline = qrows
    catalog = threshold_catalog(qrows)
    grid: list[dict[str, Any]] = []

    grid.append(
        eval_policy("A_current_quality_only", baseline, session_id=session_id, baseline=baseline)
    )

    for th_key, cutoff in catalog["atr_pct"].items():
        kept = [r for r in qrows if (r.get("atr_pct") or 0) >= cutoff]
        grid.append(
            eval_policy(
                "B_quality_and_atr_pct",
                kept,
                session_id=session_id,
                baseline=baseline,
                threshold_note=f"atr_{th_key}>={cutoff:.4f}",
            )
        )

    for th_key, cutoff in catalog["intraday_range_pct"].items():
        kept = [r for r in qrows if (r.get("intraday_range_pct") or 0) >= cutoff]
        grid.append(
            eval_policy(
                "C_quality_and_intraday_range_pct",
                kept,
                session_id=session_id,
                baseline=baseline,
                threshold_note=f"range_{th_key}>={cutoff:.4f}",
            )
        )

    for th_key, cutoff in catalog["trading_value_jpy"].items():
        kept = [r for r in qrows if (r.get("trading_value_jpy") or 0) >= cutoff]
        grid.append(
            eval_policy(
                "D_quality_and_trading_value",
                kept,
                session_id=session_id,
                baseline=baseline,
                threshold_note=f"tv_{th_key}>={cutoff:.0f}",
            )
        )

    cutoff = catalog["volatility_liquidity_score"]["top50"]
    kept = [r for r in qrows if (r.get("volatility_liquidity_score") or 0) >= cutoff]
    grid.append(
        eval_policy(
            "E_quality_and_vol_liq_top50",
            kept,
            session_id=session_id,
            baseline=baseline,
            threshold_note=f"vol_liq>={cutoff:.4f}",
        )
    )

    cutoff = catalog["daytrade_suitability_score"]["top50"]
    kept = [r for r in qrows if (r.get("daytrade_suitability_score") or 0) >= cutoff]
    grid.append(
        eval_policy(
            "F_quality_and_suitability_top50",
            kept,
            session_id=session_id,
            baseline=baseline,
            threshold_note=f"suitability>={cutoff:.4f}",
        )
    )

    kept_g = simulate_cap3_suitability_rank(
        [r for r in qrows if (r.get("daytrade_suitability_score") or 0) >= cutoff]
    )
    grid.append(
        eval_policy(
            "G_suitability_rank_cap3",
            kept_g,
            session_id=session_id,
            baseline=baseline,
            threshold_note="top50_suitability+cap3_rank",
        )
    )

    return grid


def markcap_suitability_comparison(trade_rows: Sequence[Mapping[str, Any]], *, session_id: str) -> list[dict[str, Any]]:
    by_tier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in trade_rows:
        by_tier[str(r.get("market_cap_tier") or "unknown")].append(dict(r))
    rows: list[dict[str, Any]] = []
    for tier in ("large", "mid", "small", "unknown"):
        if tier not in by_tier:
            continue
        sub = by_tier[tier]
        def mean_f(f: str) -> Optional[float]:
            v = [float(x[f]) for x in sub if x.get(f) is not None]
            return round(statistics.mean(v), 4) if v else None

        rows.append(
            {
                "session_id": session_id,
                "market_cap_tier": tier,
                "tier_ja": sub[0].get("tier_ja"),
                "trade_count": len(sub),
                "mean_intraday_range_pct": mean_f("intraday_range_pct"),
                "mean_atr_pct": mean_f("atr_pct"),
                "mean_trading_value_jpy": mean_f("trading_value_jpy"),
                "mean_turnover_proxy": mean_f("turnover_proxy"),
                "mean_volatility_liquidity_score": mean_f("volatility_liquidity_score"),
                "mean_daytrade_suitability_score": mean_f("daytrade_suitability_score"),
                "mean_quality_score": mean_f("continuation_quality_score"),
            }
        )
    return rows


def large_mover_classification(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> dict[str, Any]:
    large = [r for r in trade_rows if r.get("market_cap_tier") == "large"]
    if not large:
        return {}
    atr_vals = [float(r["atr_pct"]) for r in large if r.get("atr_pct") is not None]
    med = statistics.median(atr_vals) if atr_vals else 0.0
    movers = [r for r in large if (r.get("atr_pct") or 0) >= med]
    quiet = [r for r in large if (r.get("atr_pct") or 0) < med]
    from small_paper.daytrade_suitability import summarize_trades

    return {
        "session_id": session_id,
        "large_atr_median": round(med, 4),
        "large_mover_count": len(movers),
        "large_quiet_count": len(quiet),
        "large_mover_summary": summarize_trades(movers),
        "large_quiet_summary": summarize_trades(quiet),
    }


def mid_reject_diagnosis(events: Sequence[Mapping[str, Any]], *, session_id: str) -> list[dict[str, Any]]:
    """Why mid/growth names fail to reach accepted (quality>=0.70 candidates)."""
    from small_paper.daytrade_suitability import tier_label, tier_ja

    by_sym: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "candidates_q70": 0,
            "accepted": 0,
            "rejected": 0,
            "quality_scores": [],
            "atr_scores": [],
        }
    )
    for ev in events:
        et = str(ev.get("event_type") or "")
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        q = _float(ev.get("continuation_quality_score"))
        if et == "candidate" and (q or 0) >= QUALITY_GATE:
            by_sym[sym]["candidates_q70"] += 1
            if q is not None:
                by_sym[sym]["quality_scores"].append(q)
        elif et == "accepted":
            by_sym[sym]["accepted"] += 1
        elif et == "rejected" and (q or 0) >= QUALITY_GATE:
            by_sym[sym]["rejected"] += 1

    rows: list[dict[str, Any]] = []
    for sym, st in sorted(by_sym.items()):
        if st["candidates_q70"] <= 0:
            continue
        rows.append(
            {
                "session_id": session_id,
                "symbol": sym,
                "market_cap_tier": "unknown",
                "tier_ja": "",
                "candidates_q70": st["candidates_q70"],
                "accepted_events": st["accepted"],
                "rejected_q70_events": st["rejected"],
                "accepted_rate": round(st["accepted"] / st["candidates_q70"], 4)
                if st["candidates_q70"]
                else None,
                "mean_quality_q70": round(statistics.mean(st["quality_scores"]), 4)
                if st["quality_scores"]
                else None,
            }
        )
    return rows


def attach_tier_to_mid_rows(
    rows: list[dict[str, Any]],
    trade_rows: Sequence[Mapping[str, Any]],
) -> None:
    sym_tier = {r["symbol"]: r.get("market_cap_tier") for r in trade_rows}
    sym_tier_ja = {r["symbol"]: r.get("tier_ja") for r in trade_rows}
    for r in rows:
        sym = r["symbol"]
        r["market_cap_tier"] = sym_tier.get(sym, "unknown")
        r["tier_ja"] = sym_tier_ja.get(sym, "")


def rejected_suitability_cases(
    baseline: Sequence[Mapping[str, Any]],
    policy_trades: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
    policy_id: str,
) -> list[dict[str, Any]]:
    kept = {(t["symbol"], t["entry_time"]) for t in policy_trades}
    rows: list[dict[str, Any]] = []
    for t in baseline:
        key = (t["symbol"], t["entry_time"])
        if key in kept:
            continue
        pnl = _float(t.get("realized_pnl_pct")) or 0.0
        rows.append(
            {
                "session_id": session_id,
                "policy_id": policy_id,
                "symbol": t["symbol"],
                "entry_time": t["entry_time"],
                "realized_pnl_pct": pnl,
                "outcome": "missed_winner" if pnl > 0 else ("avoided_loser" if pnl < 0 else "flat"),
                "market_cap_tier": t.get("market_cap_tier"),
                "atr_pct": t.get("atr_pct"),
                "intraday_range_pct": t.get("intraday_range_pct"),
                "daytrade_suitability_score": t.get("daytrade_suitability_score"),
                "continuation_quality_score": t.get("continuation_quality_score"),
            }
        )
    return rows


def _rows_for_policy_note(
    qrows: Sequence[Mapping[str, Any]],
    policy_id: str,
    threshold_note: str,
    catalog: Mapping[str, dict[str, float]],
) -> list[dict[str, Any]]:
    if policy_id == "A_current_quality_only":
        return [dict(r) for r in qrows]
    if policy_id == "B_quality_and_atr_pct":
        for key, cutoff in catalog["atr_pct"].items():
            if key in threshold_note:
                return [dict(r) for r in qrows if (r.get("atr_pct") or 0) >= cutoff]
    if policy_id == "C_quality_and_intraday_range_pct":
        for key, cutoff in catalog["intraday_range_pct"].items():
            if key in threshold_note:
                return [dict(r) for r in qrows if (r.get("intraday_range_pct") or 0) >= cutoff]
    if policy_id == "D_quality_and_trading_value":
        for key, cutoff in catalog["trading_value_jpy"].items():
            if key in threshold_note:
                return [dict(r) for r in qrows if (r.get("trading_value_jpy") or 0) >= cutoff]
    if policy_id == "E_quality_and_vol_liq_top50":
        c = catalog["volatility_liquidity_score"]["top50"]
        return [dict(r) for r in qrows if (r.get("volatility_liquidity_score") or 0) >= c]
    if policy_id == "F_quality_and_suitability_top50":
        c = catalog["daytrade_suitability_score"]["top50"]
        return [dict(r) for r in qrows if (r.get("daytrade_suitability_score") or 0) >= c]
    if policy_id == "G_suitability_rank_cap3":
        c = catalog["daytrade_suitability_score"]["top50"]
        return simulate_cap3_suitability_rank(
            [r for r in qrows if (r.get("daytrade_suitability_score") or 0) >= c]
        )
    return [dict(r) for r in qrows]


def recommend(
    grids: Sequence[Mapping[str, Any]],
    correlations: Sequence[Mapping[str, Any]],
    *,
    primary_session: str = SESSIONS_DEFAULT[0],
) -> tuple[str, str]:
    baselines = [
        g
        for g in grids
        if g.get("policy_id") == "A_current_quality_only"
        and g.get("session_id") == primary_session
    ]
    if not baselines:
        baselines = [g for g in grids if g.get("policy_id") == "A_current_quality_only"]
    if not baselines:
        return "collect_more_sessions", "no baseline policy row"
    base_pf = float(baselines[0].get("structural_pf") or 0)
    session_grids = [g for g in grids if g.get("session_id") == primary_session]
    best = max(
        (g for g in session_grids if g.get("policy_id") != "A_current_quality_only"),
        key=lambda g: float(g.get("structural_pf") or 0),
        default=None,
    )
    if best is None:
        return "inconclusive", "no alternative policies"
    bpf = float(best.get("structural_pf") or 0)
    if bpf > base_pf + 0.08 and int(best.get("accepted_count") or 0) >= 20:
        pid = str(best.get("policy_id"))
        if "suitability" in pid or pid.startswith("F") or pid.startswith("G"):
            return (
                "add_daytrade_suitability_filter",
                f"{pid} PF {bpf} vs baseline {base_pf}; {best.get('threshold_note')}",
            )
        if "vol_liq" in pid or pid.startswith("E"):
            return (
                "add_volatility_liquidity_score",
                f"{pid} PF {bpf} vs baseline {base_pf}",
            )
        if pid.startswith("B") or pid.startswith("C"):
            return (
                "add_daytrade_suitability_filter",
                f"{pid} PF {bpf} vs baseline {base_pf}; threshold {best.get('threshold_note')}",
            )
    strong_corr = [
        c for c in correlations if c.get("corr_pnl_pct") is not None and abs(float(c["corr_pnl_pct"])) >= 0.15
    ]
    if bpf > base_pf + 0.03:
        return (
            "rank_by_suitability_before_quality",
            f"modest PF lift {bpf} vs {base_pf}; correlates: {[c['indicator'] for c in strong_corr[:3]]}",
        )
    if bpf <= base_pf:
        return (
            "keep_current_selection",
            f"no policy beat baseline PF {base_pf} (best {bpf})",
        )
    return "inconclusive", f"best PF {bpf} vs baseline {base_pf} insufficient lift"


def recommend_marketcap_aware(
    aware_grids: Sequence[Mapping[str, Any]],
    tier_slots: Sequence[Mapping[str, Any]],
    *,
    primary_session: str = SESSIONS_DEFAULT[0],
    baseline_pf: float = 0.0,
) -> tuple[str, str]:
    session_rows = [g for g in aware_grids if g.get("session_id") == primary_session]
    if not session_rows:
        return "inconclusive", "no markcap-aware policies"
    base = next((g for g in session_rows if g.get("policy_id") == "A_current_quality_only"), None)
    base_pf_local = float(base.get("structural_pf") or 0) if base else baseline_pf
    j_row = next((g for g in session_rows if g.get("policy_id") == "J_tier_specific_vol_liquidity_rules"), None)
    h_row = next(
        (g for g in session_rows if g.get("policy_id") == "H_marketcap_aware_suitability_top50"),
        None,
    )
    k_reserve = next(
        (g for g in session_rows if g.get("policy_id") == "K_cap3_reserve_mid_small_slot"),
        None,
    )

    candidates = [g for g in session_rows if g.get("policy_id", "").startswith(("H_", "I_", "J_", "K_"))]
    best = max(candidates, key=lambda g: float(g.get("structural_pf") or 0), default=None)
    if best is None:
        return "inconclusive", "no H/I/J/K policies"

    bpf = float(best.get("structural_pf") or 0)
    mid_share = float(best.get("mid_share") or 0)
    base_mid = float(base.get("mid_share") or 0) if base else 0.061
    missed = int(best.get("missed_winners") or 0)
    avoided = int(best.get("avoided_losers") or 0)
    n = int(best.get("accepted_count") or 0)

    slot_rows = [r for r in tier_slots if r.get("session_id") == primary_session]
    reserve_mid_delta = 0.0
    rsv = next((r for r in slot_rows if r.get("allocation_policy") == "K_reserve_mid_slot"), None)
    base_cap = next((r for r in slot_rows if r.get("allocation_policy") == "baseline_cap3"), None)
    if rsv and base_cap:
        reserve_mid_delta = float(rsv.get("mid_share") or 0) - float(base_cap.get("mid_share") or 0)

    if bpf > base_pf_local + 0.05 and mid_share >= base_mid and avoided >= missed and n >= 15:
        if str(best.get("policy_id", "")).startswith("J"):
            return (
                "add_marketcap_aware_suitability_filter",
                f"J tier rules PF {bpf} vs {base_pf_local}; mid_share {mid_share:.1%} "
                f"avoided={avoided} missed={missed}",
            )
        if "midcap_bonus" in str(best.get("policy_id", "")) or mid_share > base_mid + 0.05:
            return (
                "add_midcap_suitability_bonus",
                f"{best.get('policy_id')} PF {bpf}; mid_share {mid_share:.1%} vs baseline {base_mid:.1%}",
            )

    if reserve_mid_delta > 0.03 and rsv and float(rsv.get("structural_pf") or 0) >= base_pf_local * 0.95:
        return (
            "reserve_midcap_slot",
            f"K_reserve mid_share +{reserve_mid_delta:.1%}; PF {rsv.get('structural_pf')} vs cap3 {base_cap.get('structural_pf') if base_cap else ''}",
        )

    if j_row and float(j_row.get("mid_share") or 0) <= base_mid and float(j_row.get("structural_pf") or 0) < base_pf_local:
        return (
            "keep_large_cap_bias",
            f"J did not lift mid_share ({j_row.get('mid_share')}) or PF ({j_row.get('structural_pf')})",
        )

    if h_row and float(h_row.get("large_share") or 0) >= 0.9:
        return (
            "keep_large_cap_bias",
            f"H still {float(h_row.get('large_share') or 0):.0%} large; PF {h_row.get('structural_pf')}",
        )

    if bpf > base_pf_local + 0.03:
        return (
            "add_marketcap_aware_suitability_filter",
            f"modest PF {bpf} vs {base_pf_local} via {best.get('policy_id')}",
        )

    return "inconclusive", f"best aware PF {bpf} vs baseline {base_pf_local}; mid_share {mid_share:.1%}"


def process_session(session_rel: str) -> dict[str, Any]:
    session_dir = ROOT / "kabu_native/results/small_paper" / session_rel
    push_dir = push_dir_for_session(session_dir)
    trades = load_structural_trades(session_dir)
    if not trades or not push_dir or not push_dir.is_dir():
        return {"session_id": session_rel, "skipped": True, "reason": "missing trades or push_dir"}

    symbols = {str(t.get("symbol") or "") for t in trades}
    series_map = load_enriched_push_series(push_dir, symbols)
    trade_rows = build_trade_metric_rows(trades, series_map)

    events = load_events_jsonl(session_dir)
    corr = correlation_rows(trade_rows)
    quartiles = quartile_pf_rows(trade_rows, session_id=session_rel)
    grid = build_policy_grid(trade_rows, session_id=session_rel)
    mcap_cmp = markcap_suitability_comparison(trade_rows, session_id=session_rel)
    large_cls = large_mover_classification(trade_rows, session_id=session_rel)
    mid_rows = mid_reject_diagnosis(events, session_id=session_rel)
    attach_tier_to_mid_rows(mid_rows, trade_rows)

    baseline_q = filter_quality(trade_rows)
    cat = threshold_catalog(baseline_q)
    best_alt = max(
        (g for g in grid if g["policy_id"] != "A_current_quality_only"),
        key=lambda g: float(g.get("structural_pf") or 0),
        default=None,
    )
    reject_cases: list[dict[str, Any]] = []
    if best_alt:
        kept_rows = _rows_for_policy_note(
            baseline_q,
            str(best_alt["policy_id"]),
            str(best_alt.get("threshold_note") or ""),
            cat,
        )
        reject_cases = rejected_suitability_cases(
            baseline_q,
            kept_rows,
            session_id=session_rel,
            policy_id=str(best_alt.get("policy_id")),
        )

    from small_paper.marketcap_aware_suitability import (
        build_marketcap_aware_policy_grid,
        midcap_candidate_review_rows,
        tier_slot_allocation_summary,
    )

    aware_grid, aware_qrows, tier_th = build_marketcap_aware_policy_grid(
        trade_rows,
        session_id=session_rel,
        baseline=baseline_q,
        eval_policy_fn=eval_policy,
    )
    midcap_review = midcap_candidate_review_rows(
        events, trade_rows, aware_qrows, tier_th, session_id=session_rel
    )
    tier_slots = tier_slot_allocation_summary(
        trade_rows,
        aware_qrows,
        tier_th,
        session_id=session_rel,
        baseline=baseline_q,
        eval_policy_fn=eval_policy,
    )

    return {
        "session_id": session_rel,
        "skipped": False,
        "trade_count": len(trade_rows),
        "correlations": corr,
        "quartile_pf": quartiles,
        "policy_grid": grid,
        "marketcap_aware_policy_grid": aware_grid,
        "tier_thresholds": {
            "large_atr_p50": tier_th.large_atr_p50,
            "large_range_p50": tier_th.large_range_p50,
            "large_tv_p25": tier_th.large_tv_p25,
            "mid_atr_p50": tier_th.mid_atr_p50,
            "mid_range_p50": tier_th.mid_range_p50,
            "mid_tv_p25": tier_th.mid_tv_p25,
            "markcap_aware_top50": tier_th.markcap_aware_top50,
        },
        "marketcap_comparison": mcap_cmp,
        "large_mover_classification": large_cls,
        "mid_symbol_diagnosis": mid_rows,
        "midcap_candidate_review": midcap_review,
        "tier_slot_allocation": tier_slots,
        "rejected_cases": reject_cases,
        "trade_rows": trade_rows,
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase82 daytrade suitability what-if")
    parser.add_argument(
        "--output-session",
        type=Path,
        default=ROOT / "kabu_native/results/small_paper/20260519/live_full_session_081047",
    )
    parser.add_argument(
        "--sessions",
        nargs="*",
        default=SESSIONS_DEFAULT,
    )
    args = parser.parse_args()

    out_dir = args.output_session if args.output_session.is_absolute() else ROOT / args.output_session
    out_dir.mkdir(parents=True, exist_ok=True)

    all_corr: list[dict[str, Any]] = []
    all_quartile: list[dict[str, Any]] = []
    all_grid: list[dict[str, Any]] = []
    all_aware_grid: list[dict[str, Any]] = []
    all_mcap: list[dict[str, Any]] = []
    all_reject: list[dict[str, Any]] = []
    all_midcap_review: list[dict[str, Any]] = []
    all_tier_slots: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []

    for rel in args.sessions:
        result = process_session(rel)
        if result.get("skipped"):
            session_summaries.append(result)
            continue
        for c in result["correlations"]:
            c["session_id"] = rel
        all_corr.extend(result["correlations"])
        all_quartile.extend(result["quartile_pf"])
        all_grid.extend(result["policy_grid"])
        all_aware_grid.extend(result.get("marketcap_aware_policy_grid") or [])
        all_mcap.extend(result["marketcap_comparison"])
        all_reject.extend(result["rejected_cases"])
        all_midcap_review.extend(result.get("midcap_candidate_review") or [])
        all_tier_slots.extend(result.get("tier_slot_allocation") or [])
        session_summaries.append(
            {
                "session_id": rel,
                "trade_count": result["trade_count"],
                "baseline": next(
                    (g for g in result["policy_grid"] if g["policy_id"] == "A_current_quality_only"),
                    {},
                ),
                "marketcap_aware_baseline": next(
                    (
                        g
                        for g in result.get("marketcap_aware_policy_grid") or []
                        if g.get("policy_id") == "J_tier_specific_vol_liquidity_rules"
                    ),
                    {},
                ),
                "tier_thresholds": result.get("tier_thresholds"),
                "large_mover_classification": result["large_mover_classification"],
                "mid_tier_symbols": [
                    r for r in result["mid_symbol_diagnosis"] if r.get("market_cap_tier") == "mid"
                ],
            }
        )

    primary = args.sessions[0]
    recommendation, rationale = recommend(all_grid, all_corr, primary_session=primary)
    base_pf = float(
        next(
            (
                g.get("structural_pf")
                for g in all_grid
                if g.get("policy_id") == "A_current_quality_only" and g.get("session_id") == primary
            ),
            0,
        )
        or 0
    )
    aware_rec, aware_rat = recommend_marketcap_aware(
        all_aware_grid, all_tier_slots, primary_session=primary, baseline_pf=base_pf
    )

    review = {
        "phase": 82,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "sessions": args.sessions,
        "quality_gate": QUALITY_GATE,
        "entry_policy": "q070_cap3_mfe_fav_trial",
        "exit_policy": "combined_structural_exit_v1",
        "daytrade_suitability_formula": {
            "daytrade_suitability_score": "0.40*norm(atr)+0.30*norm(range)+0.20*norm(tv)+0.10*norm(turnover)",
            "volatility_liquidity_score": "atr_pct * log10(TradingValue)",
            "liquidity_score": "mean(log10(tv), log10(volume), 1/spread_pct)",
        },
        "marketcap_aware_formula": {
            "large": "0.35*ATR+0.30*range+0.30*tv+0.05*turnover; exclude low-vol large",
            "mid": "0.40*ATR+0.30*range+0.20*tv+0.10*turnover +0.10 bonus if ATR&range>=median",
            "small": "0.35*ATR+0.25*range+0.25*tv+0.15*turnover; reject if tv too low",
        },
        "session_summaries": session_summaries,
        "recommendation": recommendation,
        "rationale": rationale,
        "marketcap_aware_recommendation": aware_rec,
        "marketcap_aware_rationale": aware_rat,
        "note": "Diagnostic only; no config or production code changes.",
    }

    write_csv(out_dir / "phase82_suitability_indicator_correlation.csv", all_corr)
    write_csv(out_dir / "phase82_suitability_quartile_pf.csv", all_quartile)
    write_csv(out_dir / "phase82_suitability_policy_grid.csv", all_grid)
    write_csv(out_dir / "phase82_marketcap_aware_policy_grid.csv", all_aware_grid)
    write_csv(out_dir / "phase82_marketcap_suitability_comparison.csv", all_mcap)
    write_csv(out_dir / "phase82_midcap_candidate_review.csv", all_midcap_review)
    write_csv(out_dir / "phase82_tier_slot_allocation_whatif.csv", all_tier_slots)
    write_csv(out_dir / "phase82_rejected_by_suitability_cases.csv", all_reject)
    (out_dir / "phase82_daytrade_suitability_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(review, ensure_ascii=False, indent=2))
    print(f"\nWrote phase82 outputs under {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
