#!/usr/bin/env python3
"""
Phase 83: Multi-session daytrade suitability OOS review (diagnostic only).
Threshold percentiles use prior sessions only (strictly before session t).
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER = ROOT / "kabu_native" / "results" / "small_paper"
REPORTS = ROOT / "kabu_native" / "results" / "reports"
COOLOFF_CFG = ROOT / "kabu_native" / "configs" / "small_paper_pilot_q070_cap3_mfe_fav_symbol_cooloff.yaml"

QUALITY_GATE = 0.70
FIXED_RANGE_3 = 3.0
FIXED_RANGE_5 = 5.0
FIXED_ATR_5 = 5.0

MINIMUM_SESSIONS = [
    "20260518/push_replay_220451",
    "20260519/live_full_session_081047",
    "20260520/push_replay_001932",
    "20260520/live_full_session_080745",
    "20260520/push_replay_231314",
]

POLICY_IDS = (
    "A_baseline_no_suitability",
    "B_intraday_range_pct_top25_oos",
    "C_intraday_range_pct_ge_3pct",
    "D_intraday_range_pct_ge_5pct",
    "E_atr_pct_top25_oos",
    "F_atr_pct_ge_5pct",
    "G_trading_value_top50_oos",
    "H_volatility_liquidity_score_top50_oos",
    "I_range_top25_or_atr_top25_oos",
    "J_range_ge_3pct_and_tv_top50_oos",
    "K_atr_ge_5pct_and_tv_top50_oos",
)


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


def _load_phase71() -> Any:
    path = ROOT / "kabu_native" / "scripts" / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine_p83"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def discover_sessions(base: Path) -> list[str]:
    found: list[str] = []
    if not base.is_dir():
        return found
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir() or len(day_dir.name) != 8 or not day_dir.name.isdigit():
            continue
        for sub in sorted(day_dir.iterdir()):
            if not sub.is_dir():
                continue
            key = f"{day_dir.name}/{sub.name}"
            has_trades = (sub / "structural_trades.csv").is_file()
            has_events = (sub / "small_paper_events.jsonl").is_file()
            if not has_trades and not has_events:
                continue
            if (
                has_trades
                or sub.name.startswith("push_replay_")
                or sub.name.startswith("live_full_session_")
            ):
                found.append(key)
    return found


def load_structural_trades_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def replay_trades_from_events(p71: Any, session_dir: Path) -> list[dict[str, Any]]:
    """Replay combined_structural_exit_v1 trades from events (Phase78-compatible)."""
    V1_MODE = "legacy"
    V1_RATIO = 0.85
    events_path = session_dir / "small_paper_events.jsonl"
    if not events_path.is_file():
        return []
    events = p71._load_events(events_path)
    session_end = p71._session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[Any] = []

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        act.trade.close_time = close_time
        act.trade.close_price = close_price
        act.trade.close_reason = reason
        act.trade.realized_pnl_pct = p71._pnl_pct(act.trade.entry_price, close_price)
        act.trade.hold_duration_sec = round(max(0.0, p71._parse_ts(close_time) - act.entry_ts), 1)
        completed.append(act.trade)

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        ent_raw = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(ent_raw)
        price = float(ev.get("current_price") or 0)
        if price <= 0:
            continue
        st = sym_states.setdefault(sym, p71.SymState())

        if et == "accepted":
            if sym in active:
                old = active.pop(sym)
                close_act(old, close_time=ent_raw, close_price=price, reason="overlap_replaced_review")
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            tr = p71.StructuralTrade(sym, ent_raw, price, float(ev.get("continuation_quality_score") or 0))
            active[sym] = p71.ActiveTrade(
                trade=tr,
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": price,
                        "pnl_pct": 0.0,
                        "quality": comps["quality"],
                        "momentum": comps["momentum"],
                        "favorable": comps["favorable"],
                        "pure_price_momentum": comps["pure_price_momentum"],
                        "vwap_strength": comps["vwap_strength"],
                        "mfe_proxy": comps["mfe_proxy"],
                    }
                ],
            )
        elif et == "candidate" and sym in active:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            act.rich_ticks.append(
                {
                    "price": price,
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, price),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=V1_MODE,
                ratio=V1_RATIO,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                close_act(act, close_time=ent_raw, close_price=price, reason=reason)
                active.pop(sym, None)

    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "close_time": t.close_time,
            "exit_time": t.close_time,
            "close_reason": t.close_reason,
            "realized_pnl_pct": t.realized_pnl_pct,
            "continuation_quality_score": t.entry_quality,
        }
        for t in completed
    ]


def push_dir_for_session_key(session_key: str) -> Optional[Path]:
    day = session_key.split("/")[0]
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
                "continuation_quality_score": q,
                "market_cap_tier": tier_label(mc),
                "tier_ja": tier_ja(tier_label(mc)),
                **{k: v for k, v in m.items()},
            }
        )
    attach_composite_scores(rows)
    return rows


def filter_quality(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in rows
        if (_float(r.get("continuation_quality_score")) or 0) >= QUALITY_GATE
    ]


def build_oos_thresholds(prior_qrows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from small_paper.daytrade_suitability import percentile_value

    def vals(field: str) -> list[float]:
        return [float(r[field]) for r in prior_qrows if r.get(field) is not None]

    atr_v = vals("atr_pct")
    range_v = vals("intraday_range_pct")
    tv_v = vals("trading_value_jpy")
    vol_v = vals("volatility_liquidity_score")
    large = [r for r in prior_qrows if r.get("market_cap_tier") == "large"]
    large_atr = [float(r["atr_pct"]) for r in large if r.get("atr_pct") is not None]
    large_rng = [float(r["intraday_range_pct"]) for r in large if r.get("intraday_range_pct") is not None]

    return {
        "prior_quality_trade_count": len(prior_qrows),
        "prior_session_count": len({r.get("_session_id") for r in prior_qrows if r.get("_session_id")}),
        "range_top25": percentile_value(range_v, 0.75) if range_v else None,
        "atr_top25": percentile_value(atr_v, 0.75) if atr_v else None,
        "tv_top50": percentile_value(tv_v, 0.50) if tv_v else None,
        "vol_liq_top50": percentile_value(vol_v, 0.50) if vol_v else None,
        "large_atr_p25": percentile_value(large_atr, 0.25) if large_atr else None,
        "large_range_p25": percentile_value(large_rng, 0.25) if large_rng else None,
    }


def is_large_low_vol(row: Mapping[str, Any], th: Mapping[str, Any]) -> bool:
    if row.get("market_cap_tier") != "large":
        return False
    atr = _float(row.get("atr_pct"))
    rng = _float(row.get("intraday_range_pct"))
    la = th.get("large_atr_p25")
    lr = th.get("large_range_p25")
    if la is None or lr is None or atr is None or rng is None:
        return False
    return atr < float(la) and rng < float(lr)


def apply_policy(
    policy_id: str,
    qrows: Sequence[Mapping[str, Any]],
    th: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], bool, str]:
    """Return (kept, oos_applicable, threshold_note)."""
    note_parts: list[str] = []

    def ge(r: Mapping[str, Any], field: str, cutoff: Optional[float]) -> bool:
        if cutoff is None:
            return False
        return (_float(r.get(field)) or 0) >= float(cutoff)

    if policy_id == "A_baseline_no_suitability":
        return list(qrows), True, "quality>=0.70"

    if policy_id == "B_intraday_range_pct_top25_oos":
        c = th.get("range_top25")
        if c is None:
            return [], False, "no_prior_range_top25"
        note_parts.append(f"range>={c:.4f}")
        return [dict(r) for r in qrows if ge(r, "intraday_range_pct", c)], True, ";".join(note_parts)

    if policy_id == "C_intraday_range_pct_ge_3pct":
        note_parts.append(f"range>={FIXED_RANGE_3}")
        return [
            dict(r) for r in qrows if (_float(r.get("intraday_range_pct")) or 0) >= FIXED_RANGE_3
        ], True, ";".join(note_parts)

    if policy_id == "D_intraday_range_pct_ge_5pct":
        note_parts.append(f"range>={FIXED_RANGE_5}")
        return [
            dict(r) for r in qrows if (_float(r.get("intraday_range_pct")) or 0) >= FIXED_RANGE_5
        ], True, ";".join(note_parts)

    if policy_id == "E_atr_pct_top25_oos":
        c = th.get("atr_top25")
        if c is None:
            return [], False, "no_prior_atr_top25"
        note_parts.append(f"atr>={c:.4f}")
        return [dict(r) for r in qrows if ge(r, "atr_pct", c)], True, ";".join(note_parts)

    if policy_id == "F_atr_pct_ge_5pct":
        note_parts.append(f"atr>={FIXED_ATR_5}")
        return [dict(r) for r in qrows if (_float(r.get("atr_pct")) or 0) >= FIXED_ATR_5], True, ";".join(
            note_parts
        )

    if policy_id == "G_trading_value_top50_oos":
        c = th.get("tv_top50")
        if c is None:
            return [], False, "no_prior_tv_top50"
        note_parts.append(f"tv>={c:.0f}")
        return [dict(r) for r in qrows if ge(r, "trading_value_jpy", c)], True, ";".join(note_parts)

    if policy_id == "H_volatility_liquidity_score_top50_oos":
        c = th.get("vol_liq_top50")
        if c is None:
            return [], False, "no_prior_vol_liq_top50"
        note_parts.append(f"vol_liq>={c:.4f}")
        return [dict(r) for r in qrows if ge(r, "volatility_liquidity_score", c)], True, ";".join(note_parts)

    if policy_id == "I_range_top25_or_atr_top25_oos":
        cr, ca = th.get("range_top25"), th.get("atr_top25")
        if cr is None or ca is None:
            return [], False, "no_prior_range_or_atr_top25"
        note_parts.append(f"range>={cr:.4f}|atr>={ca:.4f}")
        kept = [
            dict(r)
            for r in qrows
            if ge(r, "intraday_range_pct", cr) or ge(r, "atr_pct", ca)
        ]
        return kept, True, ";".join(note_parts)

    if policy_id == "J_range_ge_3pct_and_tv_top50_oos":
        c = th.get("tv_top50")
        if c is None:
            return [], False, "no_prior_tv_top50"
        note_parts.append(f"range>={FIXED_RANGE_3};tv>={c:.0f}")
        return [
            dict(r)
            for r in qrows
            if (_float(r.get("intraday_range_pct")) or 0) >= FIXED_RANGE_3
            and ge(r, "trading_value_jpy", c)
        ], True, ";".join(note_parts)

    if policy_id == "K_atr_ge_5pct_and_tv_top50_oos":
        c = th.get("tv_top50")
        if c is None:
            return [], False, "no_prior_tv_top50"
        note_parts.append(f"atr>={FIXED_ATR_5};tv>={c:.0f}")
        return [
            dict(r)
            for r in qrows
            if (_float(r.get("atr_pct")) or 0) >= FIXED_ATR_5 and ge(r, "trading_value_jpy", c)
        ], True, ";".join(note_parts)

    return list(qrows), True, "unknown"


def policy_impact(
    baseline: Sequence[Mapping[str, Any]],
    kept: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    from small_paper.daytrade_suitability import policy_impact as _pi

    return _pi(baseline, kept)


def eval_session_policy(
    policy_id: str,
    kept: Sequence[Mapping[str, Any]],
    baseline: Sequence[Mapping[str, Any]],
    th: Mapping[str, Any],
    *,
    session_id: str,
    threshold_note: str,
    oos_applicable: bool,
) -> dict[str, Any]:
    from small_paper.daytrade_suitability import summarize_trades

    summary = summarize_trades(kept)
    impact = policy_impact(baseline, kept)
    mid_n = sum(1 for t in kept if t.get("market_cap_tier") == "mid")
    base_set = {(t["symbol"], t["entry_time"]) for t in baseline}
    kept_set = {(t["symbol"], t["entry_time"]) for t in kept}
    large_low_rej = sum(
        1
        for t in baseline
        if (t["symbol"], t["entry_time"]) in base_set - kept_set and is_large_low_vol(t, th)
    )

    n = len(kept) or 1
    return {
        "session_id": session_id,
        "policy_id": policy_id,
        "threshold_note": threshold_note,
        "oos_applicable": oos_applicable,
        "prior_session_count": th.get("prior_session_count"),
        "prior_quality_trade_count": th.get("prior_quality_trade_count"),
        **summary,
        **impact,
        "mid_cap_accepted_ratio": round(mid_n / n, 4) if kept else None,
        "large_low_vol_rejected_count": large_low_rej,
        "_pnls": [float(t["realized_pnl_pct"]) for t in kept if t.get("realized_pnl_pct") is not None],
    }


def aggregate_oos_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from small_paper.daytrade_suitability import profit_factor

    pnls: list[float] = []
    for r in rows:
        pnls.extend(r.get("_pnls") or [])
    if not pnls:
        return {"oos_session_count": 0, "aggregate_structural_pf": None}
    pf = profit_factor(pnls)
    n_all = sum(int(r.get("accepted_count") or 0) for r in rows)
    mid_all = sum(
        round((r.get("mid_cap_accepted_ratio") or 0) * int(r.get("accepted_count") or 0))
        for r in rows
    )
    return {
        "oos_session_count": len(rows),
        "aggregate_structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "aggregate_trade_count": n_all,
        "aggregate_avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
        "aggregate_max_loss": round(min(pnls), 4) if pnls else None,
        "aggregate_mid_cap_ratio": round(mid_all / n_all, 4) if n_all else None,
        "aggregate_rejected_by_suitability": sum(int(r.get("rejected_by_suitability") or 0) for r in rows),
        "aggregate_missed_winners": sum(int(r.get("missed_winners") or 0) for r in rows),
        "aggregate_avoided_losers": sum(int(r.get("avoided_losers") or 0) for r in rows),
        "aggregate_large_low_vol_rejected": sum(
            int(r.get("large_low_vol_rejected_count") or 0) for r in rows
        ),
    }


def apply_cooloff(
    rows: Sequence[Mapping[str, Any]],
    cooloff_symbols: set[str],
) -> list[dict[str, Any]]:
    return [dict(r) for r in rows if str(r.get("symbol") or "") not in cooloff_symbols]


def recommend(
    grid: Sequence[Mapping[str, Any]],
    cooloff_rows: Sequence[Mapping[str, Any]],
    *,
    session_count: int,
) -> tuple[str, str]:
    agg = [g for g in grid if g.get("row_type") == "oos_aggregate"]
    if not agg:
        return "inconclusive", "no aggregate rows"
    base = next((g for g in agg if g["policy_id"] == "A_baseline_no_suitability"), None)
    if session_count < 3:
        return "collect_more_sessions", f"only {session_count} sessions with trades"
    pf_a = float(base.get("aggregate_structural_pf") or 0) if base else 0.0
    oos_n = int(base.get("oos_session_count") or 0) if base else 0
    if oos_n < 2:
        return "collect_more_sessions", f"OOS-evaluable sessions={oos_n}"

    def pick(candidates: list[str]) -> Optional[Mapping[str, Any]]:
        rows = [g for g in agg if g["policy_id"] in candidates]
        return max(rows, key=lambda g: float(g.get("aggregate_structural_pf") or 0), default=None)

    range_policies = [
        "B_intraday_range_pct_top25_oos",
        "C_intraday_range_pct_ge_3pct",
        "D_intraday_range_pct_ge_5pct",
        "I_range_top25_or_atr_top25_oos",
        "J_range_ge_3pct_and_tv_top50_oos",
    ]
    atr_policies = ["E_atr_pct_top25_oos", "F_atr_pct_ge_5pct", "K_atr_ge_5pct_and_tv_top50_oos"]
    vol_policies = ["H_volatility_liquidity_score_top50_oos"]

    best_range = pick(range_policies)
    best_atr = pick(atr_policies)
    best_vol = pick(vol_policies)
    best_all = max(
        [g for g in agg if g["policy_id"] != "A_baseline_no_suitability"],
        key=lambda g: float(g.get("aggregate_structural_pf") or 0),
        default=base,
    )

    def ok(g: Optional[Mapping[str, Any]]) -> bool:
        if not g:
            return False
        bpf = float(g.get("aggregate_structural_pf") or 0)
        missed = int(g.get("aggregate_missed_winners") or 0)
        avoided = int(g.get("aggregate_avoided_losers") or 0)
        return bpf > pf_a + 0.03 and avoided >= missed and int(g.get("oos_session_count") or 0) >= 2

    cooloff_agg = [r for r in cooloff_rows if r.get("row_type") == "oos_aggregate"]
    combo_better = False
    if cooloff_agg and base:
        for cr in cooloff_agg:
            if (
                float(cr.get("aggregate_structural_pf") or 0)
                > float(base.get("aggregate_structural_pf") or 0) + 0.02
            ):
                combo_better = True
                break

    cooloff_note = "; cooloff combo reference also improves PF" if combo_better else ""

    if ok(best_vol):
        return (
            "add_volatility_liquidity_filter",
            f"{best_vol['policy_id']} OOS aggregate PF {best_vol.get('aggregate_structural_pf')} "
            f"vs A {pf_a}; avoided={best_vol.get('aggregate_avoided_losers')} "
            f"missed={best_vol.get('aggregate_missed_winners')}{cooloff_note}",
        )

    if ok(best_atr):
        return (
            "add_atr_filter",
            f"{best_atr['policy_id']} OOS aggregate PF {best_atr.get('aggregate_structural_pf')} "
            f"vs A {pf_a}; avoided={best_atr.get('aggregate_avoided_losers')} "
            f"missed={best_atr.get('aggregate_missed_winners')}{cooloff_note}",
        )

    if ok(best_range) and best_range:
        return (
            "add_range_filter",
            f"{best_range['policy_id']} OOS aggregate PF {best_range.get('aggregate_structural_pf')} "
            f"vs A {pf_a}; avoided={best_range.get('aggregate_avoided_losers')} "
            f"missed={best_range.get('aggregate_missed_winners')}{cooloff_note}",
        )

    if combo_better and not ok(best_all):
        return (
            "combine_with_symbol_cooloff",
            "suitability filters alone inconclusive; symbol cooloff combo reference improves PF",
        )

    if ok(best_all):
        pid = str(best_all.get("policy_id", ""))
        if pid.startswith("H_"):
            rec = "add_volatility_liquidity_filter"
        elif pid.startswith(("E_", "F_", "K_")):
            rec = "add_atr_filter"
        else:
            rec = "add_range_filter"
        return (
            rec,
            f"best OOS {pid} PF {best_all.get('aggregate_structural_pf')} vs A {pf_a}{cooloff_note}",
        )

    if pf_a >= float(best_all.get("aggregate_structural_pf") or 0) if best_all else True:
        return (
            "keep_current_selection",
            f"no suitability policy beat A OOS PF {pf_a} with avoided>=missed",
        )

    return "inconclusive", f"A PF {pf_a}; best {best_all.get('policy_id') if best_all else 'n/a'}"


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields and not str(k).startswith("_"):
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase83 daytrade suitability OOS review")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPORTS,
    )
    parser.add_argument(
        "--sessions",
        nargs="*",
        default=None,
        help="Session keys; default = all discoverable",
    )
    args = parser.parse_args()
    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    p71 = _load_phase71()
    discovered = discover_sessions(SMALL_PAPER)
    session_keys = args.sessions if args.sessions else discovered
    session_keys = sorted(set(session_keys))

    from small_paper.config import load_pilot_config
    from small_paper.symbol_cooloff import build_symbol_cooloff_state

    cooloff_pilot = load_pilot_config(COOLOFF_CFG)

    loaded: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for key in session_keys:
        sdir = SMALL_PAPER / key
        if not sdir.is_dir():
            skipped.append({"session_id": key, "reason": "directory_missing"})
            continue
        trades: list[dict[str, Any]] = []
        source = ""
        if (sdir / "structural_trades.csv").is_file():
            trades = load_structural_trades_csv(sdir / "structural_trades.csv")
            source = "structural_trades.csv"
        if not trades:
            trades = replay_trades_from_events(p71, sdir)
            source = "replayed_v1_from_events" if trades else source
        if not trades:
            skipped.append({"session_id": key, "reason": "no_trades"})
            continue
        push_dir = push_dir_for_session_key(key)
        series_map: dict[str, Any] = {}
        if push_dir and push_dir.is_dir():
            series_map = load_enriched_push_series(push_dir, {str(t.get("symbol") or "") for t in trades})
        rows = build_trade_metric_rows(trades, series_map)
        for r in rows:
            r["_session_id"] = key
        loaded.append(
            {
                "session_id": key,
                "trade_rows": rows,
                "trades_source": source,
                "push_dir": str(push_dir) if push_dir else "",
            }
        )

    prior_pool: list[dict[str, Any]] = []
    grid_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    reject_cases: list[dict[str, Any]] = []
    cooloff_combo_rows: list[dict[str, Any]] = []
    per_policy_oos: dict[str, list[dict[str, Any]]] = {p: [] for p in POLICY_IDS}
    per_policy_cooloff_oos: dict[str, list[dict[str, Any]]] = {}

    for item in loaded:
        sid = item["session_id"]
        qrows = filter_quality(item["trade_rows"])
        th = build_oos_thresholds(prior_pool)
        th["prior_session_count"] = len({r.get("_session_id") for r in prior_pool})

        cooloff_state = build_symbol_cooloff_state(
            cooloff_pilot, repo_root=ROOT, run_session_key=sid
        )
        cooloff_syms = set(cooloff_state.cooloff_symbols) if cooloff_state else set()

        for pid in POLICY_IDS:
            kept, applicable, note = apply_policy(pid, qrows, th)
            if pid != "A_baseline_no_suitability" and not applicable:
                detail_rows.append(
                    {
                        "session_id": sid,
                        "policy_id": pid,
                        "skipped": True,
                        "skip_reason": note,
                        "prior_session_count": th.get("prior_session_count"),
                    }
                )
                continue
            row = eval_session_policy(
                pid,
                kept,
                qrows,
                th,
                session_id=sid,
                threshold_note=note,
                oos_applicable=applicable or pid == "A_baseline_no_suitability",
            )
            detail_rows.append({k: v for k, v in row.items() if not str(k).startswith("_")})
            include_oos = pid in (
                "A_baseline_no_suitability",
                "C_intraday_range_pct_ge_3pct",
                "D_intraday_range_pct_ge_5pct",
                "F_atr_pct_ge_5pct",
            ) or int(th.get("prior_session_count") or 0) > 0
            if include_oos and (applicable or pid == "A_baseline_no_suitability"):
                per_policy_oos[pid].append(row)

            if pid != "A_baseline_no_suitability":
                base_set = {(t["symbol"], t["entry_time"]) for t in qrows}
                kept_set = {(t["symbol"], t["entry_time"]) for t in kept}
                for t in qrows:
                    key = (t["symbol"], t["entry_time"])
                    if key not in kept_set:
                        reject_cases.append(
                            {
                                "session_id": sid,
                                "policy_id": pid,
                                "symbol": t.get("symbol"),
                                "entry_time": t.get("entry_time"),
                                "realized_pnl_pct": t.get("realized_pnl_pct"),
                                "trade_outcome": "win"
                                if (_float(t.get("realized_pnl_pct")) or 0) > 0
                                else "loss",
                                "market_cap_tier": t.get("market_cap_tier"),
                                "intraday_range_pct": t.get("intraday_range_pct"),
                                "atr_pct": t.get("atr_pct"),
                                "trading_value_jpy": t.get("trading_value_jpy"),
                                "volatility_liquidity_score": t.get("volatility_liquidity_score"),
                                "large_low_vol": is_large_low_vol(t, th),
                                "threshold_note": note,
                            }
                        )

            if cooloff_syms and (applicable or pid == "A_baseline_no_suitability"):
                kept_co = apply_cooloff(kept, cooloff_syms)
                co_row = eval_session_policy(
                    f"{pid}+symbol_cooloff",
                    kept_co,
                    qrows,
                    th,
                    session_id=sid,
                    threshold_note=f"{note};cooloff_symbols={len(cooloff_syms)}",
                    oos_applicable=applicable,
                )
                cooloff_combo_rows.append(
                    {k: v for k, v in co_row.items() if not str(k).startswith("_")}
                )
                if int(th.get("prior_session_count") or 0) > 0:
                    per_policy_cooloff_oos.setdefault(pid, []).append(co_row)

        for r in qrows:
            prior_pool.append(dict(r))

    for pid in POLICY_IDS:
        agg = aggregate_oos_rows(per_policy_oos[pid])
        if agg.get("oos_session_count"):
            grid_rows.append(
                {
                    "row_type": "oos_aggregate",
                    "policy_id": pid,
                    **{k: v for k, v in agg.items()},
                }
            )
    for pid, rows in per_policy_cooloff_oos.items():
        agg = aggregate_oos_rows(rows)
        if agg.get("oos_session_count"):
            cooloff_combo_rows.append(
                {
                    "row_type": "oos_aggregate",
                    "policy_id": f"{pid}+symbol_cooloff",
                    **{k: v for k, v in agg.items()},
                }
            )

    recommendation, rationale = recommend(
        grid_rows, cooloff_combo_rows, session_count=len(loaded)
    )

    minimum_status = []
    for m in MINIMUM_SESSIONS:
        minimum_status.append(
            {
                "session_id": m,
                "included": m in {x["session_id"] for x in loaded},
                "required": True,
            }
        )

    agg_sorted = sorted(
        [g for g in grid_rows if g.get("row_type") == "oos_aggregate"],
        key=lambda g: float(g.get("aggregate_structural_pf") or 0),
        reverse=True,
    )

    review = {
        "phase": 83,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "oos_aggregate_ranked": [
            {
                "policy_id": g["policy_id"],
                "aggregate_structural_pf": g.get("aggregate_structural_pf"),
                "aggregate_trade_count": g.get("aggregate_trade_count"),
                "aggregate_mid_cap_ratio": g.get("aggregate_mid_cap_ratio"),
                "aggregate_avoided_losers": g.get("aggregate_avoided_losers"),
                "aggregate_missed_winners": g.get("aggregate_missed_winners"),
            }
            for g in agg_sorted
        ],
        "quality_gate": QUALITY_GATE,
        "entry_policy": "q070_cap3_mfe_fav_trial",
        "exit_policy": "combined_structural_exit_v1",
        "symbol_cooloff": "reference_only via symbol_cooloff trial config",
        "oos_rule": "Percentile thresholds from quality>=0.70 trades in sessions strictly before t; fixed 3%/5% global",
        "sessions_discovered": discovered,
        "sessions_analyzed": [x["session_id"] for x in loaded],
        "sessions_skipped": skipped,
        "minimum_sessions_status": minimum_status,
        "recommendation": recommendation,
        "rationale": rationale,
        "note": "Diagnostic only; no production or config changes.",
    }

    write_csv(out_dir / "phase83_suitability_oos_grid.csv", grid_rows)
    write_csv(out_dir / "phase83_suitability_session_detail.csv", detail_rows)
    write_csv(out_dir / "phase83_rejected_by_suitability_cases.csv", reject_cases)
    write_csv(out_dir / "phase83_suitability_cooloff_combo.csv", cooloff_combo_rows)
    (out_dir / "phase83_daytrade_suitability_oos_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(review, ensure_ascii=False, indent=2))
    print(f"\nWrote phase83 outputs under {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
