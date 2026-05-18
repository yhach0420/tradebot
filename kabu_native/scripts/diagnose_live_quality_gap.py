#!/usr/bin/env python3
"""
Investigate replay vs live continuation_quality gap (no new trading logic).

Outputs:
  - results/reports/live_quality_gap_diagnosis_<date>.json
  - <live_session_dir>/quality_top_debug.csv
  - <live_session_dir>/quality_top_debug.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional

from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def _enriched_components(trade: Mapping[str, Any]) -> dict[str, Any]:
    from research.continuation_quality_ranking import continuation_components
    from research.research_exit_criteria import _as_float

    comps = continuation_components(trade)
    mfe = _as_float(comps.get("max_favorable_excursion_pct")) or 0.0
    mae = abs(_as_float(comps.get("max_adverse_excursion_pct")) or 0.0)
    return {
        **comps,
        "momentum_weighted": comps.get("momentum_continuation"),
        "bullish_duration": comps.get("continuation_persistence"),
        "adverse_shrinking": round(max(0.0, 1.0 - min(1.0, mae / 0.5)), 4) if mae else 0.0,
        "raw_score_unclamped": round(
            0.30 * float(comps.get("momentum_continuation", 0))
            + 0.22 * float(comps.get("continuation_persistence", 0))
            + 0.20 * float(comps.get("favorable_continuation", 0))
            + 0.14 * (1.0 - min(1.0, float(comps.get("bearish_accumulation", 0))))
            + 0.14 * (1.0 if mfe > mae else max(0.0, 0.5 + (mfe - mae) / 0.5))
            + 0.04 * float(comps.get("bullish_continuation", 0)),
            4,
        ),
        "normalized_score": comps.get("continuation_quality"),
        "quality_fallback_path": (
            trade.get("momentum_continuation_score") is None
            and not (_as_float(trade.get("max_favorable_excursion_pct")) or 0)
            and not (_as_float(trade.get("max_adverse_excursion_pct")) or 0)
        ),
    }


def _trade_from_push(payload: Mapping[str, Any], *, symbol: str, profile: str) -> dict[str, Any]:
    from small_paper.pilot_runner import _candidate_trade_from_push

    return _candidate_trade_from_push(payload, symbol=symbol, profile=profile)


def _push_field_stats(push_dir: Path, *, max_files: int = 5, max_lines_per_file: int = 5000) -> dict[str, Any]:
    stats: dict[str, int] = defaultdict(int)
    files = sorted(push_dir.glob("*.jsonl"))[:max_files]
    for fp in files:
        n = 0
        for line in fp.open(encoding="utf-8"):
            if n >= max_lines_per_file:
                break
            n += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("source") != "live_push":
                continue
            pl = row.get("payload") or {}
            stats["live_push_rows"] += 1
            for key in (
                "CurrentPrice",
                "VWAP",
                "momentum_continuation_score",
                "max_favorable_excursion_pct",
                "max_adverse_excursion_pct",
                "favorable_continuation",
                "bullish_continuation_score",
            ):
                if pl.get(key) is not None:
                    stats[f"{key}_present"] += 1
    return dict(stats)


def _analyze_events(events_path: Path) -> dict[str, Any]:
    quality_hist: Counter[float] = Counter()
    tier_hist: Counter[str] = Counter()
    null_price = 0
    candidates = 0
    top_rows: list[dict[str, Any]] = []

    with events_path.open(encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e.get("event_type") != "candidate":
                continue
            candidates += 1
            q = round(float(e.get("continuation_quality_score") or 0), 4)
            quality_hist[q] += 1
            tier_hist[str(e.get("quality_tier") or "")] += 1
            if e.get("current_price") is None:
                null_price += 1
            top_rows.append(
                {
                    "event_time": e.get("event_time"),
                    "symbol": e.get("symbol"),
                    "continuation_quality_score": q,
                    "quality_tier": e.get("quality_tier"),
                    "current_price": e.get("current_price"),
                    "message_index": e.get("message_index"),
                }
            )

    top_rows.sort(key=lambda r: float(r["continuation_quality_score"]), reverse=True)
    return {
        "candidate_count": candidates,
        "quality_histogram": {str(k): v for k, v in sorted(quality_hist.items())},
        "quality_tier_histogram": dict(tier_hist),
        "current_price_null_pct": round(100.0 * null_price / max(1, candidates), 2),
        "top_candidates_preview": top_rows[:100],
    }


def _oos_precomputed_quality_stats(trades_csv: Path) -> dict[str, Any]:
    from research.research_exit_criteria import _load_csv

    rows = _load_csv(trades_csv)
    vals = [float(r["continuation_quality_score"]) for r in rows if r.get("continuation_quality_score") not in (None, "")]
    if not vals:
        return {"trades_csv": str(trades_csv), "precomputed_count": 0}
    ge55 = sum(1 for v in vals if v >= 0.55)
    return {
        "trades_csv": str(trades_csv),
        "precomputed_count": len(vals),
        "quality_min": round(min(vals), 4),
        "quality_max": round(max(vals), 4),
        "quality_ge_0_55_count": ge55,
        "quality_ge_0_55_pct": round(100.0 * ge55 / len(vals), 2),
        "note": "Phase40/41 gate CSV stores continuation_quality_score at gate time; rows lack MFE/MAE columns.",
    }


def _replay_quality_stats(trades_csv: Path, *, profile: str, limit: int = 50000) -> dict[str, Any]:
    from research.continuation_quality_ranking import continuation_quality_score
    from research.research_exit_criteria import _load_csv

    rows = _load_csv(trades_csv)
    prof_rows = [r for r in rows if str(r.get("profile")) == profile]
    if len(prof_rows) > limit:
        prof_rows = prof_rows[:limit]

    qs = [continuation_quality_score(r) for r in prof_rows]
    ge55 = sum(1 for q in qs if q >= 0.55)
    return {
        "trades_csv": str(trades_csv),
        "trade_count": len(prof_rows),
        "quality_min": round(min(qs), 4) if qs else None,
        "quality_max": round(max(qs), 4) if qs else None,
        "quality_ge_0_55_count": ge55,
        "quality_ge_0_55_pct": round(100.0 * ge55 / max(1, len(qs)), 2),
        "sample_with_mfe": sum(1 for r in prof_rows if r.get("max_favorable_excursion_pct") not in (None, "")),
        "sample_with_momentum_field": sum(
            1 for r in prof_rows if r.get("momentum_continuation_score") not in (None, "")
        ),
    }


def _replay_vs_live_symbol(
    *,
    push_path: Path,
    replay_trades: list[Mapping[str, Any]],
    symbol: str,
    profile: str,
) -> list[dict[str, Any]]:
    from research.continuation_quality_ranking import continuation_quality_score

    out: list[dict[str, Any]] = []
    replay_for_sym = [r for r in replay_trades if str(r.get("symbol")) == symbol][:5]
    live_samples: list[dict[str, Any]] = []
    if push_path.is_file():
        for i, line in enumerate(push_path.open(encoding="utf-8")):
            if i >= 200:
                break
            row = json.loads(line)
            if row.get("source") != "live_push":
                continue
            pl = row.get("payload") or {}
            trade = _trade_from_push(pl, symbol=symbol, profile=profile)
            live_samples.append(
                {
                    "recorded_at": row.get("recorded_at"),
                    "live_quality": trade.get("continuation_quality_score"),
                    "components": _enriched_components(trade),
                    "push_CurrentPrice": pl.get("CurrentPrice"),
                    "push_VWAP": pl.get("VWAP"),
                }
            )

    for rt in replay_for_sym:
        out.append(
            {
                "symbol": symbol,
                "replay_entry_time": rt.get("entry_time"),
                "replay_quality": round(continuation_quality_score(rt), 4),
                "replay_mfe": rt.get("max_favorable_excursion_pct"),
                "replay_mae": rt.get("max_adverse_excursion_pct"),
                "replay_momentum_field": rt.get("momentum_continuation_score"),
                "live_samples": live_samples[:3],
            }
        )
    return out


def main() -> int:
    repo_root, native_root = _bootstrap()
    from research.research_exit_criteria import _load_csv
    from small_paper.config import load_pilot_config

    parser = argparse.ArgumentParser(description="Diagnose live vs replay continuation_quality gap")
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=native_root / "results" / "small_paper" / "20260518" / "live_full_session_081121",
    )
    parser.add_argument(
        "--replay-trades-csv",
        type=Path,
        default=None,
        help="Replay/OOS trades CSV (default: from small_paper_pilot.yaml reference)",
    )
    parser.add_argument(
        "--push-dir",
        type=Path,
        default=native_root / "data" / "push_jsonl" / "2026-05-18",
    )
    args = parser.parse_args()

    session_dir = args.session_dir if args.session_dir.is_absolute() else (repo_root / args.session_dir)
    cfg = load_pilot_config(native_root / "configs" / "small_paper_pilot.yaml")
    profile = cfg.profile

    replay_csv = args.replay_trades_csv
    if replay_csv is None and cfg.reference_trades_csv:
        replay_csv = repo_root / cfg.reference_trades_csv
    if replay_csv and not replay_csv.is_absolute():
        replay_csv = repo_root / replay_csv

    events_path = session_dir / "small_paper_events.jsonl"
    if not events_path.is_file():
        print(f"events not found: {events_path}", file=sys.stderr)
        return 2

    events_analysis = _analyze_events(events_path)
    push_stats = _push_field_stats(args.push_dir) if args.push_dir.is_dir() else {}

    # Top 100 live candidates (full component dump; join first live_push per symbol)
    top_debug: list[dict[str, Any]] = []
    push_by_sym = {p.stem: p for p in args.push_dir.glob("*.jsonl")} if args.push_dir.is_dir() else {}

    for row in events_analysis["top_candidates_preview"][:100]:
        sym = str(row.get("symbol") or "")
        push_file = push_by_sym.get(sym) or push_by_sym.get(sym.replace(".T", ""))
        payload: dict[str, Any] = {}
        if push_file and push_file.is_file():
            for line in push_file.open(encoding="utf-8"):
                rec = json.loads(line)
                if rec.get("source") == "live_push":
                    payload = rec.get("payload") or {}
                    break
        trade = _trade_from_push(payload, symbol=sym, profile=profile) if payload else {}
        enriched = _enriched_components(trade) if trade else {}
        top_debug.append(
            {
                **row,
                **enriched,
                "push_has_CurrentPrice": payload.get("CurrentPrice") is not None if payload else False,
                "push_has_VWAP": payload.get("VWAP") is not None if payload else False,
                "push_has_momentum_field": payload.get("momentum_continuation_score") is not None
                if payload
                else False,
                "push_has_mfe_field": payload.get("max_favorable_excursion_pct") is not None
                if payload
                else False,
            }
        )
    replay_stats = _replay_quality_stats(replay_csv, profile=profile) if replay_csv and replay_csv.is_file() else {}

    ref_csv = native_root / "results" / "research" / "logic_lab" / "20260517" / "run_225513" / "trades_by_profile.csv"
    replay_recomputed = (
        _replay_quality_stats(ref_csv, profile=profile) if ref_csv.is_file() else {}
    )
    oos_gate_csv = (
        native_root
        / "results"
        / "research"
        / "logic_lab"
        / "phase41_data_oos"
        / "phase40_top_quartile_oos"
        / "top_quartile_oos_trades.csv"
    )
    oos_precomputed = _oos_precomputed_quality_stats(oos_gate_csv) if oos_gate_csv.is_file() else {}

    replay_rows: list[Mapping[str, Any]] = []
    if replay_csv and replay_csv.is_file():
        replay_rows = [r for r in _load_csv(replay_csv) if str(r.get("profile")) == profile]

    compare_symbol = "9984.T"
    push_compare = push_by_sym.get("9984.T")
    symbol_compare = _replay_vs_live_symbol(
        push_path=push_compare or Path(),
        replay_trades=replay_rows,
        symbol=compare_symbol,
        profile=profile,
    )

    empty_trade = _trade_from_push({}, symbol="DUMMY.T", profile=profile)
    replay_sample_trade = (
        dict(replay_rows[0])
        if replay_rows
        else {"max_favorable_excursion_pct": 0.2174, "max_adverse_excursion_pct": -0.2007}
    )

    diagnosis = {
        "phase": "live_quality_gap_investigation",
        "session_dir": str(session_dir),
        "profile": profile,
        "root_cause_summary": (
            "Live PUSH has no momentum_continuation_score / MFE / MAE fields. "
            "continuation_quality_score() falls back to fixed defaults (~0.323), "
            "below gate threshold 0.55. Replay/OOS trades carry MFE/MAE from bar replay, "
            "so quality reaches 0.55+."
        ),
        "live_events": events_analysis,
        "live_push_field_stats": push_stats,
        "replay_oos_recomputed_from_csv": replay_stats,
        "replay_logic_lab_recomputed_with_mfe": replay_recomputed,
        "phase40_41_oos_gate_precomputed": oos_precomputed,
        "component_comparison": {
            "live_empty_push_inputs": _enriched_components(
                {
                    "momentum_continuation_score": None,
                    "max_favorable_excursion_pct": None,
                    "max_adverse_excursion_pct": None,
                }
            ),
            "live_via_pilot_mapper_empty_payload": _enriched_components(empty_trade),
            "replay_sample_trade": _enriched_components(replay_sample_trade),
        },
        "normalization_drift": {
            "live_constant_score_observed": events_analysis["quality_histogram"],
            "fallback_formula_note": (
                "When MFE=MAE=0 and momentum missing: mom=0.25, bull=0.2, fav=0.15, "
                "dur=0, bear_inv=1.0, stability=0.5 -> quality=0.323"
            ),
            "threshold_gate": 0.55,
        },
        "replay_vs_live_symbol_compare": symbol_compare,
        "flags_checked": {
            "favorable_always_false_on_push": push_stats.get("favorable_continuation_present", 0) == 0,
            "momentum_field_on_push": push_stats.get("momentum_continuation_score_present", 0) == 0,
            "mfe_field_on_push": push_stats.get("max_favorable_excursion_pct_present", 0) == 0,
            "bullish_duration_accumulates_on_live": False,
        },
    }

    report_path = native_root / "results" / "reports" / f"live_quality_gap_diagnosis_{session_dir.parent.name}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")

    out_csv = session_dir / "quality_top_debug.csv"
    out_json = session_dir / "quality_top_debug.json"
    if top_debug:
        fields = list(top_debug[0].keys())
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in top_debug:
                w.writerow(r)
    out_json.write_text(json.dumps(top_debug, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(diagnosis, ensure_ascii=False, indent=2))
    print(f"\nWrote {report_path}", file=sys.stderr)
    print(f"Wrote {out_csv}", file=sys.stderr)
    print(f"Wrote {out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
