#!/usr/bin/env python3
"""
Phase 86: OOS comparison of vol_liq trial vs vol_liq + symbol_cooloff (diagnostic only).
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER = ROOT / "kabu_native" / "results" / "small_paper"
REPORTS = ROOT / "kabu_native" / "results" / "reports"
VOL_LIQ_CFG = ROOT / "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
COMBO_CFG = ROOT / "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq_symbol_cooloff.yaml"
PHASE84_REVIEW = REPORTS / "phase84_vol_liq_trial_review.json"
QUALITY_GATE = 0.70

POLICY_BASELINE = "q070_cap3_mfe_fav_vol_liq_trial"
POLICY_CANDIDATE = "q070_cap3_mfe_fav_vol_liq_symbol_cooloff_trial"


def _bootstrap() -> None:
    native = ROOT / "kabu_native"
    for p in (native / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_phase71() -> Any:
    path = ROOT / "kabu_native" / "scripts" / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine_p86"
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


def _import_phase84_helpers() -> Any:
    path = ROOT / "kabu_native" / "scripts" / "run_phase84_vol_liq_trial_review.py"
    name = "phase84_helpers_p86"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


def _summarize_kept(kept: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from small_paper.daytrade_suitability import summarize_trades

    s = summarize_trades(kept)
    s["accepted_count"] = len(kept)
    return s


def _symbol_distribution(kept: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for r in kept:
        c[str(r.get("symbol") or "")] += 1
    return dict(sorted(c.items(), key=lambda x: (-x[1], x[0])))


def _evaluate_session(
    session_id: str,
    qrows: Sequence[Mapping[str, Any]],
    *,
    vol_liq_threshold: Optional[float],
    cooloff_symbols: set[str],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if vol_liq_threshold is None:
        vol_liq_kept = list(qrows)
    else:
        th = float(vol_liq_threshold)
        vol_liq_kept = [
            dict(r)
            for r in qrows
            if (_float(r.get("volatility_liquidity_score")) or 0) >= th
        ]

    baseline_kept = vol_liq_kept
    cooloff_blocked: list[dict[str, Any]] = []
    candidate_kept: list[dict[str, Any]] = []
    for r in baseline_kept:
        sym = str(r.get("symbol") or "")
        if sym in cooloff_symbols:
            cooloff_blocked.append(dict(r))
        else:
            candidate_kept.append(dict(r))

    def row(policy_id: str, kept: list[dict[str, Any]], *, cooloff_rej: int) -> dict[str, Any]:
        pnls = [float(t["realized_pnl_pct"]) for t in kept if t.get("realized_pnl_pct") is not None]
        base_set = {(t["symbol"], t["entry_time"]) for t in baseline_kept}
        kept_set = {(t["symbol"], t["entry_time"]) for t in kept}
        dropped = base_set - kept_set
        missed = avoided = 0
        for t in baseline_kept:
            key = (t["symbol"], t["entry_time"])
            if key not in dropped:
                continue
            pnl = _float(t.get("realized_pnl_pct")) or 0.0
            if pnl > 0:
                missed += 1
            elif pnl < 0:
                avoided += 1
        s = _summarize_kept(kept)
        return {
            "session_id": session_id,
            "policy_id": policy_id,
            "vol_liq_threshold": vol_liq_threshold,
            "quality_gate_count": len(qrows),
            "rejected_count": len(qrows) - len(baseline_kept) + cooloff_rej,
            "rejected_by_vol_liq": len(qrows) - len(baseline_kept),
            "rejected_by_symbol_cooloff": cooloff_rej,
            "symbol_cooloff_count": len(cooloff_symbols),
            "missed_winners": missed if policy_id == POLICY_CANDIDATE else 0,
            "avoided_losers": avoided if policy_id == POLICY_CANDIDATE else 0,
            "symbol_distribution": _symbol_distribution(kept),
            **s,
            "_pnls": pnls,
        }

    base_row = row(POLICY_BASELINE, baseline_kept, cooloff_rej=0)
    cand_row = row(POLICY_CANDIDATE, candidate_kept, cooloff_rej=len(cooloff_blocked))
    cases = []
    for r in cooloff_blocked:
        pnl = _float(r.get("realized_pnl_pct"))
        cases.append(
            {
                "session_id": session_id,
                "symbol": r.get("symbol"),
                "entry_time": r.get("entry_time"),
                "realized_pnl_pct": pnl,
                "volatility_liquidity_score": r.get("volatility_liquidity_score"),
                "outcome": "avoided_loser"
                if pnl is not None and pnl < 0
                else ("missed_winner" if pnl and pnl > 0 else "blocked"),
            }
        )
    return base_row, cand_row, cases


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls: list[float] = []
    sym_dist: Counter[str] = Counter()
    for r in rows:
        pnls.extend(r.get("_pnls") or [])
        for sym, cnt in (r.get("symbol_distribution") or {}).items():
            sym_dist[sym] += int(cnt)
    pf = _profit_factor(pnls) if pnls else None
    n = sum(int(r.get("accepted_count") or 0) for r in rows)
    return {
        "oos_session_count": len(rows),
        "aggregate_structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "aggregate_trade_count": n,
        "aggregate_avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
        "aggregate_win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
        "aggregate_max_loss": round(min(pnls), 4) if pnls else None,
        "aggregate_rejected_by_symbol_cooloff": sum(
            int(r.get("rejected_by_symbol_cooloff") or 0) for r in rows
        ),
        "aggregate_missed_winners": sum(int(r.get("missed_winners") or 0) for r in rows),
        "aggregate_avoided_losers": sum(int(r.get("avoided_losers") or 0) for r in rows),
        "aggregate_symbol_distribution": dict(sym_dist.most_common(40)),
    }


def _recommend(
    agg_base: Mapping[str, Any],
    agg_cand: Mapping[str, Any],
    *,
    phase84_pf: Optional[float],
) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    pf_b = float(agg_base.get("aggregate_structural_pf") or 0)
    pf_c = float(agg_cand.get("aggregate_structural_pf") or 0)
    n_b = int(agg_base.get("aggregate_trade_count") or 0)
    n_c = int(agg_cand.get("aggregate_trade_count") or 0)
    avg_b = float(agg_base.get("aggregate_avg_pnl") or 0)
    avg_c = float(agg_cand.get("aggregate_avg_pnl") or 0)
    avoided = int(agg_cand.get("aggregate_avoided_losers") or 0)
    missed = int(agg_cand.get("aggregate_missed_winners") or 0)
    oos_n = int(agg_cand.get("oos_session_count") or 0)

    if oos_n < 2:
        return "collect_more_sessions", f"OOS sessions={oos_n}", notes

    trade_ratio = n_c / n_b if n_b else 0.0
    if trade_ratio < 0.35:
        notes.append(f"trade_count_ratio={trade_ratio:.2f} (<0.35 suggests overfit PF lift)")

    if pf_c > pf_b + 0.02 and avoided > missed and trade_ratio >= 0.5:
        if avg_c < avg_b * 0.85:
            notes.append(f"avg_pnl fell {avg_c} vs {avg_b}")
            return (
                "keep_vol_liq_only",
                f"PF {pf_c} vs {pf_b} but avg_pnl degraded; cooloff not adopted",
                notes,
            )
        return (
            "promote_vol_liq_symbol_cooloff_trial",
            f"OOS PF {pf_c} vs vol_liq-only {pf_b}; trades {n_c}/{n_b}; "
            f"avg_pnl {avg_c} vs {avg_b}; avoided={avoided} missed={missed}",
            notes,
        )

    if pf_c <= pf_b:
        return (
            "keep_vol_liq_only",
            f"cooloff did not improve PF ({pf_c} <= {pf_b})",
            notes,
        )

    if pf_c > pf_b and missed > avoided:
        return (
            "keep_vol_liq_only",
            f"PF {pf_c} > {pf_b} but missed_winners ({missed}) > avoided_losers ({avoided})",
            notes,
        )

    if trade_ratio < 0.5:
        return (
            "reject_overfit",
            f"PF {pf_c} vs {pf_b} with only {trade_ratio:.0%} trades kept; likely overfit",
            notes,
        )

    return (
        "inconclusive",
        f"PF {pf_c} vs {pf_b}; trades {n_c}/{n_b}; avoided={avoided} missed={missed}",
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
            if k not in fields and not str(k).startswith("_") and k != "symbol_distribution":
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
    from small_paper.symbol_cooloff import build_symbol_cooloff_state

    p84 = _import_phase84_helpers()
    p71 = _load_phase71()
    vol_pilot = load_pilot_config(VOL_LIQ_CFG)
    combo_pilot = load_pilot_config(COMBO_CFG)

    parser = argparse.ArgumentParser(description="Phase86 vol_liq + symbol_cooloff OOS")
    parser.add_argument("--output-dir", type=Path, default=REPORTS)
    args = parser.parse_args()
    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    phase84_ref: dict[str, Any] = {}
    if PHASE84_REVIEW.is_file():
        phase84_ref = json.loads(PHASE84_REVIEW.read_text(encoding="utf-8"))

    comparison: list[dict[str, Any]] = []
    sym_rows: list[dict[str, Any]] = []
    cooloff_cases: list[dict[str, Any]] = []
    base_oos: list[dict[str, Any]] = []
    cand_oos: list[dict[str, Any]] = []

    for sid in discover_sessions(SMALL_PAPER):
        sdir = SMALL_PAPER / sid
        push_dir = p84.push_dir_for_key(sid)
        if not push_dir or not push_dir.is_dir():
            continue
        trades = p84.load_trades(sdir, p71)
        if not trades:
            continue
        rows = p84.build_metric_rows(trades, push_dir)
        qrows = p84.filter_quality(rows)
        if not qrows:
            continue

        vl_state = build_vol_liq_threshold(vol_pilot, repo_root=ROOT, run_session_key=sid)
        th = vl_state.vol_liq_threshold if vl_state else None
        if th is None:
            continue

        co_state = build_symbol_cooloff_state(combo_pilot, repo_root=ROOT, run_session_key=sid)
        cooloff_syms = set(co_state.cooloff_symbols) if co_state else set()

        base_row, cand_row, cases = _evaluate_session(
            sid, qrows, vol_liq_threshold=th, cooloff_symbols=cooloff_syms
        )
        comparison.append({k: v for k, v in base_row.items() if not str(k).startswith("_")})
        comparison.append({k: v for k, v in cand_row.items() if not str(k).startswith("_")})
        base_oos.append(base_row)
        cand_oos.append(cand_row)
        cooloff_cases.extend(cases)

        for policy_id, kept_dist in (
            (POLICY_BASELINE, base_row.get("symbol_distribution") or {}),
            (POLICY_CANDIDATE, cand_row.get("symbol_distribution") or {}),
        ):
            for sym, cnt in kept_dist.items():
                sym_rows.append(
                    {
                        "session_id": sid,
                        "policy_id": policy_id,
                        "symbol": sym,
                        "trade_count": cnt,
                    }
                )

    agg_base = _aggregate(base_oos)
    agg_cand = _aggregate(cand_oos)
    comparison.append({"row_type": "oos_aggregate", "policy_id": POLICY_BASELINE, **agg_base})
    comparison.append({"row_type": "oos_aggregate", "policy_id": POLICY_CANDIDATE, **agg_cand})

    p84_pf = None
    if phase84_ref:
        p84_pf = (phase84_ref.get("aggregate_oos") or {}).get("vol_liq_trial", {}).get(
            "aggregate_structural_pf"
        )

    decision, rationale, notes = _recommend(agg_base, agg_cand, phase84_pf=p84_pf)

    review = {
        "phase": 86,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "baseline_policy": POLICY_BASELINE,
        "candidate_policy": POLICY_CANDIDATE,
        "combo_config": str(COMBO_CFG.relative_to(ROOT)),
        "vol_liq_config": str(VOL_LIQ_CFG.relative_to(ROOT)),
        "exit_policy": "combined_structural_exit_v1",
        "max_concurrent_positions": 3,
        "favorable_mode": "mfe_linked",
        "oos_rule": "vol_liq top50 from prior sessions; symbol cooloff from prior sessions (rule D)",
        "sessions_analyzed": [r["session_id"] for r in base_oos],
        "phase84_oos_pf_reference": p84_pf,
        "aggregate_oos": {
            POLICY_BASELINE: agg_base,
            POLICY_CANDIDATE: agg_cand,
        },
        "decision": decision,
        "rationale": rationale,
        "evaluation_notes": notes,
        "comparison_summary": {
            "pf_delta": round(
                float(agg_cand.get("aggregate_structural_pf") or 0)
                - float(agg_base.get("aggregate_structural_pf") or 0),
                4,
            ),
            "trade_count_delta": int(agg_cand.get("aggregate_trade_count") or 0)
            - int(agg_base.get("aggregate_trade_count") or 0),
            "avg_pnl_delta": round(
                (float(agg_cand.get("aggregate_avg_pnl") or 0) or 0)
                - (float(agg_base.get("aggregate_avg_pnl") or 0) or 0),
                4,
            ),
            "rejected_by_symbol_cooloff_total": agg_cand.get("aggregate_rejected_by_symbol_cooloff"),
        },
        "note": "Diagnostic only; no production or runtime logic changes.",
    }

    write_csv(out_dir / "phase86_policy_comparison.csv", comparison)
    write_csv(out_dir / "phase86_symbol_distribution.csv", sym_rows)
    write_csv(out_dir / "phase86_cooloff_rejected_cases.csv", cooloff_cases)
    (out_dir / "phase86_vol_liq_symbol_cooloff_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(review, ensure_ascii=False, indent=2))
    print(f"\nWrote phase86 outputs under {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
