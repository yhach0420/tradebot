#!/usr/bin/env python3
"""
Phase 87: Profit-source analysis for q070_cap3_mfe_fav_vol_liq_trial (diagnostic only).
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER = ROOT / "kabu_native" / "results" / "small_paper"
REPORTS = ROOT / "kabu_native" / "results" / "reports"
VOL_LIQ_CFG = ROOT / "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
POLICY_ID = "q070_cap3_mfe_fav_vol_liq_trial"
QUALITY_GATE = 0.70
MIN_SYMBOL_TRADES_FOR_RANK = 8

# Semantic windows for conclusion (JST, inclusive start, exclusive end unless noted).
PERIOD_WINDOWS: list[tuple[str, str, str]] = [
    ("post_open_30m", "09:05", "09:35"),  # 09:05直後
    ("late_morning", "10:30", "11:24"),  # 前場後半 (window ends 11:23)
    ("afternoon_open", "12:33", "13:03"),  # 後場寄り
    ("pre_close", "14:30", "15:21"),  # 引け前 (window ends 15:20)
]


def _bootstrap() -> None:
    native = ROOT / "kabu_native"
    for p in (native / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _import_phase84() -> Any:
    path = ROOT / "kabu_native" / "scripts" / "run_phase84_vol_liq_trial_review.py"
    name = "phase84_helpers_p87"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    from small_paper.daytrade_suitability import profit_factor

    return profit_factor(pnls)


def _parse_entry_dt(entry_time: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(entry_time).replace("Z", "+00:00"))
        return dt.astimezone(JST)
    except (TypeError, ValueError):
        return None


def time_bucket_30m(entry_time: str) -> str:
    dt = _parse_entry_dt(entry_time)
    if dt is None:
        return "unknown"
    minute_slot = 0 if dt.minute < 30 else 30
    start = dt.replace(minute=minute_slot, second=0, microsecond=0)
    end = start + timedelta(minutes=30)
    return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"


def _in_period(dt: datetime, start_hm: str, end_hm: str) -> bool:
    sh, sm = map(int, start_hm.split(":"))
    eh, em = map(int, end_hm.split(":"))
    t = dt.hour * 60 + dt.minute
    return sh * 60 + sm <= t < eh * 60 + em


def period_tags(entry_time: str) -> list[str]:
    dt = _parse_entry_dt(entry_time)
    if dt is None:
        return []
    return [label for label, start, end in PERIOD_WINDOWS if _in_period(dt, start, end)]


def enrich_trade_rows(
    trades: Sequence[Mapping[str, Any]],
    metric_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    # FIFO per (symbol, entry_time): overlap replacements reuse the same key.
    buckets: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for t in trades:
        key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
        buckets[key].append(t)
    out: list[dict[str, Any]] = []
    for m in metric_rows:
        key = (str(m.get("symbol") or ""), str(m.get("entry_time") or ""))
        queue = buckets.get(key) or []
        raw = queue.pop(0) if queue else {}
        row = dict(m)
        row["exit_reason"] = str(raw.get("close_reason") or raw.get("exit_reason") or "unknown")
        row["mfe_pct"] = _float(raw.get("mfe_pct"))
        row["mae_pct"] = _float(raw.get("mae_pct"))
        row["time_bucket_30m"] = time_bucket_30m(str(row.get("entry_time") or ""))
        row["period_tags"] = "|".join(period_tags(str(row.get("entry_time") or "")))
        out.append(row)
    return out


def summarize_group(
    trades: Sequence[Mapping[str, Any]],
    *,
    dim_key: str,
    dim_value: str,
) -> dict[str, Any]:
    pnls = [float(t["realized_pnl_pct"]) for t in trades if t.get("realized_pnl_pct") is not None]
    mfes = [_float(t.get("mfe_pct")) for t in trades]
    maes = [_float(t.get("mae_pct")) for t in trades]
    mfes_ok = [x for x in mfes if x is not None]
    maes_ok = [x for x in maes if x is not None]
    pf = _profit_factor(pnls) if pnls else None
    n = len(pnls)
    return {
        dim_key: dim_value,
        "trade_count": n,
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else None,
        "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
        "total_pnl": round(sum(pnls), 4) if pnls else None,
        "pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_mfe": round(statistics.mean(mfes_ok), 4) if mfes_ok else None,
        "avg_mae": round(statistics.mean(maes_ok), 4) if maes_ok else None,
    }


def aggregate_by(
    trades: Sequence[Mapping[str, Any]],
    key_fn: Callable[[Mapping[str, Any]], str],
    dim_key: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        groups[key_fn(t)].append(dict(t))
    rows = [summarize_group(g, dim_key=dim_key, dim_value=k) for k, g in sorted(groups.items())]
    rows.sort(key=lambda r: (-(r.get("trade_count") or 0), str(r.get(dim_key) or "")))
    return rows


def matrix_symbol_dim(
    trades: Sequence[Mapping[str, Any]],
    dim_key: str,
    dim_fn: Callable[[Mapping[str, Any]], str],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        sym = str(t.get("symbol") or "")
        dim = dim_fn(t)
        groups[(sym, dim)].append(dict(t))
    rows: list[dict[str, Any]] = []
    for (sym, dim), g in sorted(groups.items()):
        s = summarize_group(g, dim_key=dim_key, dim_value=dim)
        rows.append({"symbol": sym, dim_key: dim, **s})
    rows.sort(key=lambda r: (-abs(r.get("total_pnl") or 0), str(r.get("symbol") or "")))
    return rows


def aggregate_periods(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    period_trades: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        for tag in period_tags(str(t.get("entry_time") or "")):
            period_trades[tag].append(dict(t))
    order = [p[0] for p in PERIOD_WINDOWS]
    rows = []
    for label in order:
        g = period_trades.get(label, [])
        if not g:
            continue
        rows.append(summarize_group(g, dim_key="period_label", dim_value=label))
    return rows


def _top_bottom(
    rows: Sequence[Mapping[str, Any]],
    *,
    dim_key: str,
    min_trades: int,
    top_n: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    eligible = [r for r in rows if (r.get("trade_count") or 0) >= min_trades and r.get("pf") is not None]
    by_pf = sorted(eligible, key=lambda r: (-(r.get("pf") or 0), -(r.get("trade_count") or 0)))
    by_loss = sorted(eligible, key=lambda r: (r.get("total_pnl") or 0))
    high_vol_bad = sorted(
        [r for r in rows if (r.get("trade_count") or 0) >= min_trades],
        key=lambda r: (-(r.get("trade_count") or 0), r.get("avg_pnl") or 0),
    )
    bad_pnl_high_vol = [r for r in high_vol_bad if (r.get("avg_pnl") or 0) < 0][:top_n]
    return {
        "pf_leaders": [
            {dim_key: r[dim_key], "pf": r["pf"], "trade_count": r["trade_count"], "total_pnl": r["total_pnl"]}
            for r in by_pf[:top_n]
        ],
        "pf_laggards": [
            {dim_key: r[dim_key], "pf": r["pf"], "trade_count": r["trade_count"], "total_pnl": r["total_pnl"]}
            for r in by_pf[-top_n:]
        ],
        "total_pnl_leaders": [
            {dim_key: r[dim_key], "total_pnl": r["total_pnl"], "pf": r["pf"], "trade_count": r["trade_count"]}
            for r in sorted(eligible, key=lambda r: -(r.get("total_pnl") or 0))[:top_n]
        ],
        "high_volume_negative_avg_pnl": [
            {
                dim_key: r[dim_key],
                "trade_count": r["trade_count"],
                "avg_pnl": r["avg_pnl"],
                "total_pnl": r["total_pnl"],
                "pf": r["pf"],
            }
            for r in bad_pnl_high_vol
        ],
    }


def recommend_conclusion(
    agg: Mapping[str, Any],
    symbol_rows: Sequence[Mapping[str, Any]],
    exit_rows: Sequence[Mapping[str, Any]],
    time_rows: Sequence[Mapping[str, Any]],
    period_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, str, dict[str, Any]]:
    pf = float(agg.get("aggregate_pf") or 0)
    n = int(agg.get("trade_count") or 0)
    sym_rank = _top_bottom(symbol_rows, dim_key="symbol", min_trades=MIN_SYMBOL_TRADES_FOR_RANK)
    exit_rank = _top_bottom(exit_rows, dim_key="exit_reason", min_trades=5)
    time_rank = _top_bottom(time_rows, dim_key="time_bucket_30m", min_trades=10)

    exit_pf_spread = 0.0
    ex_eligible = [r for r in exit_rows if (r.get("trade_count") or 0) >= 5 and r.get("pf")]
    if len(ex_eligible) >= 2:
        pfs = [float(r["pf"]) for r in ex_eligible]
        exit_pf_spread = max(pfs) - min(pfs)

    sym_concentration = 0.0
    if symbol_rows and n:
        top = max(int(r.get("trade_count") or 0) for r in symbol_rows)
        sym_concentration = top / n

    period_note = {r["period_label"]: {"pf": r["pf"], "trade_count": r["trade_count"], "avg_pnl": r["avg_pnl"]} for r in period_rows}

    # Heuristic: where is improvement headroom without new filters?
    exit_signal = exit_pf_spread >= 0.4 and len(ex_eligible) >= 3
    symbol_signal = len(sym_rank["high_volume_negative_avg_pnl"]) >= 2
    time_spread = 0.0
    t_eligible = [r for r in time_rows if (r.get("trade_count") or 0) >= 10 and r.get("pf")]
    if len(t_eligible) >= 2:
        pfs = [float(r["pf"]) for r in t_eligible]
        time_spread = max(pfs) - min(pfs)
    time_signal = time_spread >= 0.35

    if exit_signal and not symbol_signal:
        next_focus = "exit_policy_tuning"
        focus_rationale = (
            f"exit_reason PF spread {exit_pf_spread:.2f} exceeds symbol/time dispersion; "
            "losses cluster on specific close paths"
        )
    elif symbol_signal and sym_concentration >= 0.12:
        next_focus = "symbol_selection_refinement"
        focus_rationale = (
            f"{len(sym_rank['high_volume_negative_avg_pnl'])} symbols show high volume with negative avg_pnl; "
            f"top symbol share {sym_concentration:.1%} of trades"
        )
    elif time_signal:
        next_focus = "time_window_refinement"
        focus_rationale = f"30m bucket PF spread {time_spread:.2f}; period breakdown {period_note}"
    else:
        next_focus = "maintain_and_observe"
        focus_rationale = "No single dimension dominates; continue vol_liq trial without new gates"

    maintain = pf >= 1.15 and n >= 100
    decision = "maintain_vol_liq_trial" if maintain else "maintain_vol_liq_trial_observe_more_data"
    rationale = (
        f"Aggregate PF {pf} on {n} vol_liq-filtered structural trades across OOS/live sessions. "
        f"Phase86 rejected symbol_cooloff; profit source is descriptive only."
    )

    findings = {
        "pf_leader_symbols": sym_rank["pf_leaders"],
        "pf_laggard_symbols": sym_rank["pf_laggards"],
        "total_pnl_leader_symbols": sym_rank["total_pnl_leaders"],
        "high_volume_negative_avg_pnl_symbols": sym_rank["high_volume_negative_avg_pnl"],
        "exit_pf_leaders": exit_rank["pf_leaders"],
        "exit_pf_laggards": exit_rank["pf_laggards"],
        "time_bucket_pf_leaders": time_rank["pf_leaders"],
        "time_bucket_pf_laggards": time_rank["pf_laggards"],
        "semantic_periods": period_note,
        "exit_reason_pf_spread": round(exit_pf_spread, 4),
        "time_bucket_pf_spread": round(time_spread, 4),
        "top_symbol_trade_share": round(sym_concentration, 4),
    }

    overfit_guard = (
        "Do not add per-symbol or per-slot hard filters from this review alone; "
        "prefer exit-path review and continued OOS before any gate."
    )

    return decision, rationale, {
        "maintain_current_config": maintain,
        "next_improvement_focus": next_focus,
        "focus_rationale": focus_rationale,
        "key_findings": findings,
        "overfit_guard": overfit_guard,
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def main() -> int:
    _bootstrap()
    from small_paper.config import load_pilot_config
    from small_paper.daytrade_suitability_gate import build_vol_liq_threshold

    p84 = _import_phase84()
    p71 = p84._load_phase71()
    pilot = load_pilot_config(VOL_LIQ_CFG)

    parser = argparse.ArgumentParser(description="Phase87 vol_liq profit source review")
    parser.add_argument("--output-dir", type=Path, default=REPORTS)
    args = parser.parse_args()
    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    all_trades: list[dict[str, Any]] = []
    sessions_used: list[str] = []

    for sid in p84.discover_sessions(SMALL_PAPER):
        sdir = SMALL_PAPER / sid
        if not (sdir / "structural_trades.csv").is_file():
            continue
        push_dir = p84.push_dir_for_key(sid)
        if not push_dir or not push_dir.is_dir():
            continue
        raw = p84.load_trades(sdir, p71)
        if not raw:
            continue
        rows = p84.build_metric_rows(raw, push_dir)
        qrows = p84.filter_quality(rows)
        if not qrows:
            continue
        state = build_vol_liq_threshold(pilot, repo_root=ROOT, run_session_key=sid)
        th = state.vol_liq_threshold if state else None
        if th is None:
            continue
        kept = [
            r
            for r in qrows
            if (_float(r.get("volatility_liquidity_score")) or 0) >= float(th)
        ]
        enriched = enrich_trade_rows(raw, kept)
        for r in enriched:
            r["session_id"] = sid
        all_trades.extend(enriched)
        sessions_used.append(sid)

    if not all_trades:
        print("No vol_liq trial trades found", file=sys.stderr)
        return 1

    pnls = [float(t["realized_pnl_pct"]) for t in all_trades]
    agg_pf = _profit_factor(pnls)
    aggregate = {
        "trade_count": len(all_trades),
        "aggregate_pf": round(agg_pf, 4) if agg_pf not in (None, float("inf")) else agg_pf,
        "avg_pnl": round(statistics.mean(pnls), 4),
        "total_pnl": round(sum(pnls), 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
        "session_count": len(sessions_used),
    }

    symbol_rows = aggregate_by(all_trades, lambda t: str(t.get("symbol") or ""), "symbol")
    exit_rows = aggregate_by(all_trades, lambda t: str(t.get("exit_reason") or ""), "exit_reason")
    time_rows = aggregate_by(all_trades, lambda t: str(t.get("time_bucket_30m") or ""), "time_bucket_30m")
    period_rows = aggregate_periods(all_trades)
    sym_exit = matrix_symbol_dim(all_trades, "exit_reason", lambda t: str(t.get("exit_reason") or ""))
    sym_time = matrix_symbol_dim(all_trades, "time_bucket_30m", lambda t: str(t.get("time_bucket_30m") or ""))

    decision, rationale, conclusion_detail = recommend_conclusion(
        aggregate, symbol_rows, exit_rows, time_rows, period_rows
    )

    review = {
        "phase": 87,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "policy_id": POLICY_ID,
        "vol_liq_config": str(VOL_LIQ_CFG.relative_to(ROOT)),
        "exit_policy": "combined_structural_exit_v1",
        "max_concurrent_positions": 3,
        "favorable_mode": "mfe_linked",
        "filter_rule": "quality>=0.70 and volatility_liquidity_top50 (prior-only threshold per session)",
        "data_source": "structural_trades.csv only (sessions without CSV skipped)",
        "phase86_decision_reference": "keep_vol_liq_only",
        "sessions_analyzed": sessions_used,
        "aggregate": aggregate,
        "semantic_period_summary": period_rows,
        "decision": decision,
        "rationale": rationale,
        **conclusion_detail,
        "note": "Diagnostic only; identifies profit sources without new entry filters.",
    }

    write_csv(out_dir / "phase87_symbol_profit.csv", symbol_rows)
    write_csv(out_dir / "phase87_exit_reason_profit.csv", exit_rows)
    write_csv(out_dir / "phase87_time_bucket_profit.csv", time_rows)
    write_csv(out_dir / "phase87_symbol_exit_matrix.csv", sym_exit)
    write_csv(out_dir / "phase87_symbol_time_matrix.csv", sym_time)
    (out_dir / "phase87_profit_source_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(review, ensure_ascii=False, indent=2))
    print(f"\nWrote phase87 outputs under {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
