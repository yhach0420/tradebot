#!/usr/bin/env python3
"""
Phase 89: OOS what-if for min_peak_pnl_before_mfe_giveback (diagnostic only).
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER = ROOT / "kabu_native" / "results" / "small_paper"
REPORTS = ROOT / "kabu_native" / "results" / "reports"
VOL_LIQ_CFG = ROOT / "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
POLICY_BASELINE = "q070_cap3_mfe_fav_vol_liq_trial"
EXIT_POLICY = "combined_structural_exit_v1"
MIN_PEAK_CANDIDATES = (0.10, 0.12, 0.15, 0.20)
TRAILING_GIVEBACK_PCT = 0.18
VWAP_BREAK_PEAK_PNL = 0.10
TAKE_QUALITY_DROP = 0.08
MOMENTUM_WEAKEN_RATIO = 0.85
FAVORABLE_FADE_RATIO = 0.85
MIN_AFFECTED_FOR_ADOPT = 5
MIN_SESSIONS_WITH_AFFECTED = 2
MAX_SESSION_SHARE_OF_BENEFIT = 0.70


def _bootstrap() -> None:
    native = ROOT / "kabu_native" / "src"
    for p in (str(ROOT), str(native)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _import_phase84() -> Any:
    path = ROOT / "kabu_native/scripts/run_phase84_vol_liq_trial_review.py"
    name = "phase84_helpers_p89"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_phase71() -> Any:
    path = ROOT / "kabu_native/scripts/run_phase71_split_momentum_fade_review.py"
    name = "phase71_helpers_p89"
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


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    from small_paper.daytrade_suitability import profit_factor

    return profit_factor(pnls)


def _load_events(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            p = ev.get("payload") or ev
            out.append({**p, "event_type": ev.get("event_type") or p.get("event_type")})
    return out


def _tick_from_candidate(ev: Mapping[str, Any], entry_price: float, entry_q: float) -> dict[str, Any]:
    from research.structural_exit_policies import tick_from_candidate

    return tick_from_candidate(ev, entry_price, entry_q)


def simulate_combined_v1(
    ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    *,
    hard_stop_pct: float,
    min_peak_pnl_before_mfe_giveback: Optional[float] = None,
) -> tuple[float, str, float]:
    """Returns (exit_pnl_pct, exit_reason, peak_pnl_pct)."""
    if not ticks:
        return 0.0, "no_ticks", 0.0
    stop = entry_price * (1.0 - hard_stop_pct / 100.0)
    peak_q = peak_pnl = peak_mom = peak_fav = 0.0

    for t in ticks:
        px = float(t.get("price") or entry_price)
        pnl = float(t.get("pnl_pct") or 0)
        q = float(t.get("quality") or 0)
        mom = float(t.get("momentum") or 0)
        fav = float(t.get("favorable") or 0)
        peak_q = max(peak_q, q)
        peak_pnl = max(peak_pnl, pnl)
        peak_mom = max(peak_mom, mom)
        peak_fav = max(peak_fav, fav)

        if px <= stop:
            return pnl, "stop_hit", peak_pnl
        if q <= peak_q - TAKE_QUALITY_DROP:
            return pnl, "quality_decay_exit", peak_pnl
        if peak_mom > 0 and mom < peak_mom * MOMENTUM_WEAKEN_RATIO:
            return pnl, "momentum_fade_exit", peak_pnl
        if peak_fav > 0 and fav < peak_fav * FAVORABLE_FADE_RATIO:
            return pnl, "favorable_fade_exit", peak_pnl
        if peak_pnl > VWAP_BREAK_PEAK_PNL and pnl < 0:
            return pnl, "vwap_break_exit", peak_pnl
        if peak_pnl > 0 and pnl <= peak_pnl - TRAILING_GIVEBACK_PCT:
            allow = min_peak_pnl_before_mfe_giveback is None or peak_pnl >= min_peak_pnl_before_mfe_giveback
            if allow:
                return pnl, "mfe_giveback_exit", peak_pnl

    last_pnl = float(ticks[-1].get("pnl_pct") or 0)
    return last_pnl, "session_end", peak_pnl


def build_extended_ticks(
    trade: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    session_end: str,
) -> list[dict[str, Any]]:
    sym = str(trade.get("symbol") or "")
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    end_bound = _parse_ts(session_end)
    entry_price = _float(trade.get("entry_price")) or 0.0
    entry_q = _float(trade.get("continuation_quality_score")) or 0.0

    cands = [
        e
        for e in events
        if str(e.get("event_type") or "") == "candidate" and str(e.get("symbol") or "") == sym
    ]
    cands = [
        e
        for e in cands
        if ent <= _parse_ts(str(e.get("entry_time") or e.get("timestamp") or "")) <= end_bound
    ]
    cands.sort(key=lambda e: _parse_ts(str(e.get("entry_time") or e.get("timestamp") or "")))
    return [_tick_from_candidate(e, entry_price, entry_q) for e in cands]


def _summarize_pnls(pnls: Sequence[float]) -> dict[str, Any]:
    pf = _profit_factor(pnls)
    n = len(pnls)
    return {
        "trade_count": n,
        "aggregate_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else None,
        "total_pnl": round(sum(pnls), 4) if pnls else None,
        "worst_loss": round(min(pnls), 4) if pnls else None,
    }


def _recommend(
    baseline: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    affected_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, str, Optional[float], list[str]]:
    notes: list[str] = []
    b_pf = float(baseline.get("aggregate_pf") or 0)
    b_avg = float(baseline.get("avg_pnl") or 0)
    b_total = float(baseline.get("total_pnl") or 0)
    b_worst = float(baseline.get("worst_loss") or 0)

    best: Optional[dict[str, Any]] = None
    for c in candidates:
        thresh = c.get("min_peak_pnl_before_mfe_giveback")
        aff = int(c.get("affected_trade_count") or 0)
        if aff < MIN_AFFECTED_FOR_ADOPT:
            notes.append(f"min_peak={thresh}: affected={aff} < {MIN_AFFECTED_FOR_ADOPT}")
            continue
        pf = float(c.get("aggregate_pf") or 0)
        avg = float(c.get("avg_pnl") or 0)
        total = float(c.get("total_pnl") or 0)
        worst = float(c.get("worst_loss") or 0)
        if not (pf > b_pf and avg > b_avg and total > b_total):
            continue
        if worst < b_worst - 1e-6:
            notes.append(f"min_peak={thresh}: worst_loss worsened {worst} vs {b_worst}")
            continue
        sess = c.get("session_net_pnl_delta") or {}
        pos = sum(float(v) for v in sess.values() if float(v) > 0)
        if pos > 0:
            max_share = max(float(v) for v in sess.values() if float(v) > 0) / pos
            if max_share > MAX_SESSION_SHARE_OF_BENEFIT:
                notes.append(
                    f"min_peak={thresh}: session benefit concentration {max_share:.0%}"
                )
                continue
        sessions_pos = sum(1 for v in sess.values() if float(v) > 0)
        if sessions_pos < MIN_SESSIONS_WITH_AFFECTED and aff >= MIN_AFFECTED_FOR_ADOPT:
            notes.append(f"min_peak={thresh}: benefit in only {sessions_pos} session(s)")
            continue
        if best is None or float(c.get("net_pnl_delta") or 0) > float(best.get("net_pnl_delta") or 0):
            best = c

    if best is None:
        if max(int(c.get("affected_trade_count") or 0) for c in candidates) < MIN_AFFECTED_FOR_ADOPT:
            return (
                "research_only",
                "Too few mfe_giveback trades affected; keep combined_structural_exit_v1",
                None,
                notes,
            )
        return (
            "keep_current_exit",
            "No min_peak threshold met strict adopt criteria (PF, avg_pnl, total_pnl, worst_loss, session balance)",
            None,
            notes,
        )

    return (
        "adopt_candidate",
        f"min_peak_pnl_before_mfe_giveback={best['min_peak_pnl_before_mfe_giveback']}% improves OOS replay metrics",
        float(best["min_peak_pnl_before_mfe_giveback"]),
        notes,
    )


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
    p71 = _import_phase71()
    pilot = load_pilot_config(VOL_LIQ_CFG)
    hard_stop = float(getattr(pilot, "hard_stop_pct", None) or 1.20)

    parser = argparse.ArgumentParser(description="Phase89 mfe giveback min_peak what-if")
    parser.add_argument("--output-dir", type=Path, default=REPORTS)
    args = parser.parse_args()
    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    trade_rows: list[dict[str, Any]] = []
    sessions_used: list[str] = []

    for sid in p84.discover_sessions(SMALL_PAPER):
        sdir = SMALL_PAPER / sid
        events_path = sdir / "small_paper_events.jsonl"
        if not (sdir / "structural_trades.csv").is_file() or not events_path.is_file():
            continue
        events = _load_events(events_path)
        if not events:
            continue
        session_end = p71._session_end(events)

        push_dir = p84.push_dir_for_key(sid)
        if not push_dir or not push_dir.is_dir():
            continue
        raw = p84.load_trades(sdir, p71)
        if not raw:
            continue
        metric = p84.build_metric_rows(raw, push_dir)
        qrows = p84.filter_quality(metric)
        state = build_vol_liq_threshold(pilot, repo_root=ROOT, run_session_key=sid)
        th = state.vol_liq_threshold if state else None
        if th is None:
            continue

        kept_keys = {
            (str(r["symbol"]), str(r["entry_time"]))
            for r in qrows
            if (_float(r.get("volatility_liquidity_score")) or 0) >= float(th)
        }
        if not kept_keys:
            continue
        sessions_used.append(sid)

        raw_buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for t in raw:
            key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            raw_buckets[key].append(dict(t))

        for key in kept_keys:
            queue = raw_buckets.get(key) or []
            if not queue:
                continue
            tr = queue.pop(0)
            ticks = build_extended_ticks(tr, events, session_end)
            if not ticks:
                continue
            entry_px = _float(tr.get("entry_price")) or 0.0
            csv_pnl = _float(tr.get("realized_pnl_pct")) or 0.0
            csv_reason = str(tr.get("close_reason") or "")

            base_pnl, base_reason, peak_pnl = simulate_combined_v1(
                ticks, entry_px, hard_stop_pct=hard_stop, min_peak_pnl_before_mfe_giveback=None
            )
            sims: dict[str, tuple[float, str]] = {
                "baseline_replay": (base_pnl, base_reason),
            }
            for mp in MIN_PEAK_CANDIDATES:
                pnl, reason, _ = simulate_combined_v1(
                    ticks,
                    entry_px,
                    hard_stop_pct=hard_stop,
                    min_peak_pnl_before_mfe_giveback=mp,
                )
                sims[f"min_peak_{mp:.2f}"] = (pnl, reason)

            trade_rows.append(
                {
                    "session_id": sid,
                    "symbol": tr.get("symbol"),
                    "entry_time": tr.get("entry_time"),
                    "csv_close_reason": csv_reason,
                    "csv_realized_pnl_pct": csv_pnl,
                    "peak_pnl_pct_replay": round(peak_pnl, 4),
                    "baseline_replay_pnl": round(base_pnl, 4),
                    "baseline_replay_exit": base_reason,
                    **{
                        f"{k}_pnl": round(v[0], 4)
                        for k, v in sims.items()
                        if k != "baseline_replay"
                    },
                    **{
                        f"{k}_exit": v[1]
                        for k, v in sims.items()
                        if k != "baseline_replay"
                    },
                }
            )

    if not trade_rows:
        print("No replayable vol_liq trades", file=sys.stderr)
        return 1

    baseline_pnls = [float(r["baseline_replay_pnl"]) for r in trade_rows]
    baseline_agg = _summarize_pnls(baseline_pnls)
    baseline_agg["policy_id"] = POLICY_BASELINE
    baseline_agg["exit_policy"] = EXIT_POLICY
    baseline_agg["min_peak_pnl_before_mfe_giveback"] = None

    comparison: list[dict[str, Any]] = [baseline_agg]
    candidate_aggs: list[dict[str, Any]] = []
    affected_all: list[dict[str, Any]] = []

    for mp in MIN_PEAK_CANDIDATES:
        key = f"min_peak_{mp:.2f}"
        pnls = [float(r[f"{key}_pnl"]) for r in trade_rows]
        agg = _summarize_pnls(pnls)
        agg["policy_id"] = f"{POLICY_BASELINE}+min_peak_{mp:.2f}"
        agg["min_peak_pnl_before_mfe_giveback"] = mp

        improved = worsened = affected = 0
        net_delta = 0.0
        sess_delta: dict[str, float] = defaultdict(float)
        worst_before = min(baseline_pnls)
        worst_after = min(pnls)

        for r in trade_rows:
            b = float(r["baseline_replay_pnl"])
            c = float(r[f"{key}_pnl"])
            if abs(c - b) < 1e-6 and r[f"{key}_exit"] == r["baseline_replay_exit"]:
                continue
            affected += 1
            d = round(c - b, 4)
            net_delta += d
            sess_delta[str(r["session_id"] or "")] += d
            if c > b:
                improved += 1
            elif c < b:
                worsened += 1
            if r["baseline_replay_exit"] == "mfe_giveback_exit" or r["csv_close_reason"] == "mfe_giveback_exit":
                affected_all.append(
                    {
                        "session_id": r["session_id"],
                        "symbol": r["symbol"],
                        "entry_time": r["entry_time"],
                        "min_peak_pnl_before_mfe_giveback": mp,
                        "peak_pnl_pct_replay": r["peak_pnl_pct_replay"],
                        "csv_close_reason": r["csv_close_reason"],
                        "csv_realized_pnl_pct": r["csv_realized_pnl_pct"],
                        "baseline_replay_pnl": r["baseline_replay_pnl"],
                        "baseline_replay_exit": r["baseline_replay_exit"],
                        "whatif_pnl": round(c, 4),
                        "whatif_exit": r[f"{key}_exit"],
                        "pnl_delta": d,
                        "outcome": "improved" if c > b else ("worsened" if c < b else "unchanged"),
                    }
                )

        agg["affected_trade_count"] = affected
        agg["improved_count"] = improved
        agg["worsened_count"] = worsened
        agg["net_pnl_delta"] = round(net_delta, 4)
        agg["worst_loss_change"] = round(worst_after - worst_before, 4)
        agg["pf_delta"] = round(float(agg["aggregate_pf"] or 0) - float(baseline_agg["aggregate_pf"] or 0), 4)
        agg["avg_pnl_delta"] = round(float(agg["avg_pnl"] or 0) - float(baseline_agg["avg_pnl"] or 0), 4)
        agg["total_pnl_delta"] = round(float(agg["total_pnl"] or 0) - float(baseline_agg["total_pnl"] or 0), 4)
        agg["session_net_pnl_delta"] = {k: round(v, 4) for k, v in sess_delta.items()}
        comparison.append(agg)
        candidate_aggs.append(agg)

    decision, rationale, adopted_peak, notes = _recommend(baseline_agg, candidate_aggs, affected_all)

    review = {
        "phase": 89,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "baseline_policy": POLICY_BASELINE,
        "exit_policy": EXIT_POLICY,
        "whatif_parameter": "min_peak_pnl_before_mfe_giveback",
        "candidates_pct": list(MIN_PEAK_CANDIDATES),
        "phase88_reference": "research memo; keep_current_exit on pathology sample n=6",
        "constraints": {
            "no_production_yaml_change": True,
            "no_runtime_change": True,
            "no_symbol_or_time_tuning": True,
        },
        "replay_method": (
            "vol_liq-filtered structural trades; candidate ticks through session_end; "
            "combined_structural_exit_v1 with optional min peak before mfe_giveback"
        ),
        "sessions_analyzed": sessions_used,
        "trade_count_replayed": len(trade_rows),
        "baseline": baseline_agg,
        "candidates": candidate_aggs,
        "decision": decision,
        "rationale": rationale,
        "adopted_min_peak_pct": adopted_peak,
        "evaluation_notes": notes,
        "adopt_criteria": {
            "min_affected_trades": MIN_AFFECTED_FOR_ADOPT,
            "requires_pf_avg_total_improvement": True,
            "worst_loss_must_not_worsen": True,
            "max_session_share_of_benefit": MAX_SESSION_SHARE_OF_BENEFIT,
        },
        "note": "Diagnostic only; no production or runtime changes.",
    }

    write_csv(out_dir / "phase89_mfe_giveback_min_peak_comparison.csv", comparison)
    write_csv(out_dir / "phase89_affected_trades.csv", affected_all)
    out_path = out_dir / "phase89_mfe_giveback_min_peak_whatif_review.json"
    out_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "decision": decision,
                "rationale": rationale,
                "adopted_min_peak_pct": adopted_peak,
                "baseline_pf": baseline_agg.get("aggregate_pf"),
                "trade_count": len(trade_rows),
                "output": str(out_path),
            },
            ensure_ascii=True,
        )
    )
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
