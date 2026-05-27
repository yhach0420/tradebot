#!/usr/bin/env python3
"""
Phase 84: OOS replay of volatility_liquidity top50 trial gate vs no_filter baseline.
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
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER = ROOT / "kabu_native" / "results" / "small_paper"
REPORTS = ROOT / "kabu_native" / "results" / "reports"
VOL_LIQ_CFG = ROOT / "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
QUALITY_GATE = 0.70


def _bootstrap() -> None:
    native = ROOT / "kabu_native"
    for p in (native / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_phase71() -> Any:
    path = ROOT / "kabu_native" / "scripts" / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine_p84"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def discover_sessions(base: Path) -> list[str]:
    found: list[str] = []
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir() or len(day_dir.name) != 8:
            continue
        for sub in sorted(day_dir.iterdir()):
            if not sub.is_dir():
                continue
            has_trades = (sub / "structural_trades.csv").is_file()
            has_events = (sub / "small_paper_events.jsonl").is_file()
            if not has_trades and not has_events:
                continue
            if (
                has_trades
                or sub.name.startswith("push_replay_")
                or sub.name.startswith("live_full_session_")
            ):
                found.append(f"{day_dir.name}/{sub.name}")
    return found


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def replay_trades_from_events(p71: Any, session_dir: Path) -> list[dict[str, Any]]:
    """Phase78-compatible structural replay from events."""
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
                rich_ticks=[{"price": price, "quality": comps["quality"]}],
            )
        elif et == "candidate" and sym in active:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            act.rich_ticks.append({"price": price})
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
        px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(px), reason="session_end")
    return [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "realized_pnl_pct": t.realized_pnl_pct,
            "continuation_quality_score": t.entry_quality,
        }
        for t in completed
    ]


def load_trades(session_dir: Path, p71: Any) -> list[dict[str, Any]]:
    if (session_dir / "structural_trades.csv").is_file():
        with (session_dir / "structural_trades.csv").open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    return replay_trades_from_events(p71, session_dir)


def push_dir_for_key(key: str) -> Optional[Path]:
    day = key.split("/")[0]
    if len(day) == 8:
        return ROOT / "kabu_native/data/push_jsonl" / f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    return None


def build_metric_rows(trades: Sequence[Mapping[str, Any]], push_dir: Path) -> list[dict[str, Any]]:
    from small_paper.accepted_liquidity_metrics import lookup_metrics_at_entry
    from small_paper.daytrade_suitability import tier_label, volatility_liquidity_score
    from small_paper.daytrade_suitability_gate import load_push_tick_series

    symbols = {str(t.get("symbol") or "") for t in trades}
    series = load_push_tick_series(push_dir, symbols)

    def _parse_ts(iso: str) -> float:
        try:
            return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 0.0

    rows: list[dict[str, Any]] = []
    for t in trades:
        sym = str(t.get("symbol") or "")
        ent_ts = _parse_ts(str(t.get("entry_time") or ""))
        m = lookup_metrics_at_entry(series.get(sym, []), ent_ts)
        tv = _float(m.get("trading_value_jpy"))
        atr = _float(m.get("atr_pct"))
        vol = volatility_liquidity_score(atr, tv)
        mc = m.get("market_cap_jpy")
        rows.append(
            {
                "symbol": sym,
                "entry_time": t.get("entry_time"),
                "realized_pnl_pct": _float(t.get("realized_pnl_pct")),
                "continuation_quality_score": _float(t.get("continuation_quality_score")),
                "market_cap_tier": tier_label(_float(mc)),
                "atr_pct": atr,
                "intraday_range_pct": _float(m.get("intraday_range_pct")),
                "trading_value": tv,
                "turnover_proxy": (tv / mc if tv and mc and mc > 0 else None),
                "volatility_liquidity_score": vol,
            }
        )
    return rows


def filter_quality(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows if (_float(r.get("continuation_quality_score")) or 0) >= QUALITY_GATE]


def policy_impact(baseline: Sequence[Mapping[str, Any]], kept: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from small_paper.daytrade_suitability import policy_impact as _pi

    return _pi(baseline, kept)


def summarize(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from small_paper.daytrade_suitability import summarize_trades

    s = summarize_trades(trades)
    mid_n = sum(1 for t in trades if t.get("market_cap_tier") == "mid")
    n = len(trades) or 1
    s["mid_cap_accepted_ratio"] = round(mid_n / n, 4) if trades else None
    return s


def recommend(
    agg_nf: Mapping[str, Any],
    agg_vl: Mapping[str, Any],
    *,
    session_count: int,
) -> tuple[str, str]:
    if session_count < 3:
        return "collect_more_sessions", f"only {session_count} OOS sessions"
    pf_a = float(agg_nf.get("aggregate_structural_pf") or 0)
    pf_v = float(agg_vl.get("aggregate_structural_pf") or 0)
    avoided = int(agg_vl.get("aggregate_avoided_losers") or 0)
    missed = int(agg_vl.get("aggregate_missed_winners") or 0)
    oos_n = int(agg_vl.get("oos_session_count") or 0)
    if oos_n < 2:
        return "collect_more_sessions", f"OOS sessions={oos_n}"
    if pf_v > pf_a + 0.03 and avoided > missed:
        return (
            "promote_vol_liq_trial",
            f"vol_liq_trial OOS PF {pf_v} vs no_filter {pf_a}; avoided={avoided} missed={missed}",
        )
    if pf_v <= pf_a:
        return "keep_current_selection", f"vol_liq PF {pf_v} <= no_filter {pf_a}"
    return (
        "inconclusive",
        f"PF gain {pf_v} vs {pf_a} but avoided={avoided} missed={missed}",
    )


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
    from small_paper.config import load_pilot_config
    from small_paper.allowed_trading_windows import windows_summary
    from small_paper.daytrade_suitability import profit_factor
    from small_paper.daytrade_suitability_gate import build_vol_liq_threshold

    parser = argparse.ArgumentParser(description="Phase84 vol_liq trial OOS review")
    parser.add_argument("--output-dir", type=Path, default=REPORTS)
    args = parser.parse_args()
    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir

    pilot = load_pilot_config(VOL_LIQ_CFG)
    p71 = _load_phase71()
    session_keys = discover_sessions(SMALL_PAPER)

    comparison_rows: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []
    reject_cases: list[dict[str, Any]] = []
    per_nf: list[dict[str, Any]] = []
    per_vl: list[dict[str, Any]] = []

    for sid in session_keys:
        sdir = SMALL_PAPER / sid
        push_dir = push_dir_for_key(sid)
        if not push_dir or not push_dir.is_dir():
            continue
        trades = load_trades(sdir, p71)
        if not trades:
            continue
        rows = build_metric_rows(trades, push_dir)
        qrows = filter_quality(rows)
        if not qrows:
            continue

        state = build_vol_liq_threshold(pilot, repo_root=ROOT, run_session_key=sid)
        th = state.vol_liq_threshold if state else None
        prior_n = state.prior_quality_trade_count if state else 0
        sources = list(state.source_sessions) if state else []

        threshold_rows.append(
            {
                "session_id": sid,
                "prior_session_count": len(sources),
                "daytrade_suitability_source_sessions": "|".join(sources),
                "prior_quality_trade_count": prior_n,
                "daytrade_suitability_threshold": th,
                "oos_applicable": th is not None,
            }
        )

        kept_nf = qrows
        if th is not None:
            kept_vl = [
                r
                for r in qrows
                if (_float(r.get("volatility_liquidity_score")) or 0) >= float(th)
            ]
        else:
            kept_vl = list(qrows)

        nf_sum = {**summarize(kept_nf), **policy_impact(qrows, kept_nf)}
        vl_sum = {**summarize(kept_vl), **policy_impact(qrows, kept_vl)}
        nf_pnls = [float(t["realized_pnl_pct"]) for t in kept_nf if t.get("realized_pnl_pct") is not None]
        vl_pnls = [float(t["realized_pnl_pct"]) for t in kept_vl if t.get("realized_pnl_pct") is not None]

        for policy_id, s, pnls in (
            ("no_filter", nf_sum, nf_pnls),
            ("vol_liq_trial", vl_sum, vl_pnls),
        ):
            row = {
                "session_id": sid,
                "policy_id": policy_id,
                "daytrade_suitability_threshold": th,
                "threshold_note": f"vol_liq>={th:.4f}" if th and policy_id == "vol_liq_trial" else "quality_only",
                **{k: v for k, v in s.items()},
                "_pnls": pnls,
            }
            comparison_rows.append(row)
            if th is not None:
                (per_nf if policy_id == "no_filter" else per_vl).append(row)

        base_set = {(r["symbol"], r["entry_time"]) for r in qrows}
        kept_set = {(r["symbol"], r["entry_time"]) for r in kept_vl}
        for r in qrows:
            key = (r["symbol"], r["entry_time"])
            if key in kept_set or th is None:
                continue
            reject_cases.append(
                {
                    "session_id": sid,
                    "symbol": r.get("symbol"),
                    "entry_time": r.get("entry_time"),
                    "realized_pnl_pct": r.get("realized_pnl_pct"),
                    "market_cap_tier": r.get("market_cap_tier"),
                    "volatility_liquidity_score": r.get("volatility_liquidity_score"),
                    "daytrade_suitability_threshold": th,
                    "atr_pct": r.get("atr_pct"),
                    "intraday_range_pct": r.get("intraday_range_pct"),
                    "trading_value": r.get("trading_value"),
                    "turnover_proxy": r.get("turnover_proxy"),
                }
            )

    def aggregate(per_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        all_pnls: list[float] = []
        for r in per_rows:
            all_pnls.extend(r.get("_pnls") or [])
        pf = profit_factor(all_pnls) if all_pnls else None
        return {
            "oos_session_count": len(per_rows),
            "aggregate_structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
            "aggregate_trade_count": sum(int(r.get("accepted_count") or 0) for r in per_rows),
            "aggregate_rejected_by_suitability": sum(
                int(r.get("rejected_by_suitability") or 0) for r in per_rows
            ),
            "aggregate_missed_winners": sum(int(r.get("missed_winners") or 0) for r in per_rows),
            "aggregate_avoided_losers": sum(int(r.get("avoided_losers") or 0) for r in per_rows),
            "aggregate_mid_cap_ratio": round(
                statistics.mean([float(r.get("mid_cap_accepted_ratio") or 0) for r in per_rows]),
                4,
            )
            if per_rows
            else None,
        }

    nf_rows = [
        r
        for r in comparison_rows
        if r.get("policy_id") == "no_filter" and r.get("daytrade_suitability_threshold") is not None
    ]
    vl_rows = [
        r
        for r in comparison_rows
        if r.get("policy_id") == "vol_liq_trial" and r.get("daytrade_suitability_threshold") is not None
    ]
    agg_nf = aggregate(nf_rows)
    agg_vl = aggregate(vl_rows)

    for pid, agg in (("no_filter", agg_nf), ("vol_liq_trial", agg_vl)):
        comparison_rows.append({"row_type": "oos_aggregate", "policy_id": pid, **agg})

    rec, rat = recommend(agg_nf, agg_vl, session_count=len({r["session_id"] for r in threshold_rows}))

    windows = pilot.allowed_trading_windows
    review = {
        "phase": 84,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "trial_config": str(VOL_LIQ_CFG.relative_to(ROOT)),
        "policy_label": pilot.policy_label,
        "baseline_policy": pilot.baseline_policy,
        "sessions_analyzed": [r["session_id"] for r in threshold_rows],
        "allowed_trading_windows": windows_summary(windows),
        "aggregate_oos": {"no_filter": agg_nf, "vol_liq_trial": agg_vl},
        "phase83_reference_pf": {"A_baseline": 1.0198, "H_vol_liq_top50": 1.1572},
        "recommendation": rec,
        "rationale": rat,
        "note": "Diagnostic only; production yaml not modified.",
    }

    write_csv(out_dir / "phase84_vol_liq_policy_comparison.csv", comparison_rows)
    write_csv(out_dir / "phase84_vol_liq_thresholds_by_session.csv", threshold_rows)
    write_csv(out_dir / "phase84_vol_liq_rejected_cases.csv", reject_cases)
    (out_dir / "phase84_vol_liq_trial_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(review, ensure_ascii=False, indent=2))
    print(f"\nWrote phase84 outputs under {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
