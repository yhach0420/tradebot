#!/usr/bin/env python3
"""
Phase 88: Pathology review for mfe_giveback_exit and favorable_fade_exit (diagnostic only).
No symbol/time/session-specific filters or coefficients.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER = ROOT / "kabu_native" / "results" / "small_paper"
REPORTS = ROOT / "kabu_native" / "results" / "reports"
VOL_LIQ_CFG = ROOT / "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
POLICY_ID = "q070_cap3_mfe_fav_vol_liq_trial"
TARGET_REASONS = ("mfe_giveback_exit", "favorable_fade_exit")
PRE_EXIT_TAIL = 5
TRAILING_GIVEBACK_PCT = 0.18
FAVORABLE_FADE_RATIO = 0.85
TAKE_QUALITY_DROP = 0.08
MOMENTUM_WEAKEN_RATIO = 0.85


def _bootstrap() -> None:
    native = ROOT / "kabu_native" / "src"
    for p in (str(ROOT), str(native)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _import_phase84() -> Any:
    path = ROOT / "kabu_native/scripts/run_phase84_vol_liq_trial_review.py"
    name = "phase84_helpers_p88"
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


def _dist(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    vals = sorted(values)
    n = len(vals)

    def pct(p: float) -> float:
        if n == 1:
            return vals[0]
        idx = min(n - 1, max(0, int(p * (n - 1))))
        return round(vals[idx], 6)

    return {
        "n": n,
        "min": round(vals[0], 6),
        "max": round(vals[-1], 6),
        "mean": round(statistics.mean(vals), 6),
        "median": round(statistics.median(vals), 6),
        "p95": pct(0.95),
    }


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


def _replay_hold_path(
    trade: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sym = str(trade.get("symbol") or "")
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    ex = _parse_ts(str(trade.get("close_time") or ""))
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
        if ent <= _parse_ts(str(e.get("entry_time") or e.get("timestamp") or "")) <= ex
    ]
    cands.sort(key=lambda e: _parse_ts(str(e.get("entry_time") or e.get("timestamp") or "")))

    rich: list[dict[str, Any]] = []
    peak_q = peak_mom = peak_fav = peak_pnl = 0.0
    exit_trigger: Optional[str] = None

    for ev in cands:
        tick = _tick_from_candidate(ev, entry_price, entry_q)
        q = float(tick["quality"])
        mom = float(tick["momentum"])
        fav = float(tick["favorable"])
        pnl = float(tick["pnl_pct"])
        peak_q = max(peak_q, q)
        peak_mom = max(peak_mom, mom)
        peak_fav = max(peak_fav, fav)
        peak_pnl = max(peak_pnl, pnl)
        rich.append(tick)

        if q <= peak_q - TAKE_QUALITY_DROP:
            exit_trigger = "quality_decay_exit"
        elif peak_mom > 0 and mom < peak_mom * MOMENTUM_WEAKEN_RATIO:
            exit_trigger = "momentum_fade_exit"
        elif peak_fav > 0 and fav < peak_fav * FAVORABLE_FADE_RATIO:
            exit_trigger = "favorable_fade_exit"
        elif peak_pnl > 0.10 and pnl < 0:
            exit_trigger = "vwap_break_exit"
        elif peak_pnl > 0 and pnl <= peak_pnl - TRAILING_GIVEBACK_PCT:
            exit_trigger = "mfe_giveback_exit"

    last = rich[-1] if rich else {}
    tail = rich[-PRE_EXIT_TAIL:] if rich else []
    pre_exit = [
        {
            "ts": t.get("ts"),
            "pnl_pct": round(float(t.get("pnl_pct") or 0), 4),
            "quality": round(float(t.get("quality") or 0), 4),
            "momentum": round(float(t.get("momentum") or 0), 4),
            "favorable": round(float(t.get("favorable") or 0), 4),
        }
        for t in tail
    ]
    pnl_slope = None
    fav_slope = None
    if len(pre_exit) >= 2:
        pnl_slope = round(pre_exit[-1]["pnl_pct"] - pre_exit[0]["pnl_pct"], 4)
        fav_slope = round(pre_exit[-1]["favorable"] - pre_exit[0]["favorable"], 4)

    return {
        "tick_count_replay": len(rich),
        "replay_available": bool(rich),
        "replayed_trigger": exit_trigger,
        "trigger_matches_csv": exit_trigger == trade.get("close_reason"),
        "peak_pnl_pct_replay": round(peak_pnl, 4),
        "peak_favorable_replay": round(peak_fav, 4),
        "peak_quality_replay": round(peak_q, 4),
        "peak_momentum_replay": round(peak_mom, 4),
        "at_exit_pnl_pct": round(float(last.get("pnl_pct") or 0), 4),
        "at_exit_favorable": round(float(last.get("favorable") or 0), 4),
        "at_exit_quality": round(float(last.get("quality") or 0), 4),
        "at_exit_momentum": round(float(last.get("momentum") or 0), 4),
        "giveback_from_peak_pct": round(peak_pnl - float(last.get("pnl_pct") or 0), 4)
        if rich
        else None,
        "favorable_vs_peak_ratio": round(float(last.get("favorable") or 0) / peak_fav, 4)
        if peak_fav > 0
        else None,
        "pre_exit_tail": pre_exit,
        "pre_exit_pnl_slope": pnl_slope,
        "pre_exit_favorable_slope": fav_slope,
    }


def _case_row(
    trade: Mapping[str, Any],
    *,
    session_id: str,
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    mfe = _float(trade.get("mfe_pct"))
    mae = _float(trade.get("mae_pct"))
    pnl = _float(trade.get("realized_pnl_pct"))
    hold = _float(trade.get("hold_duration_sec"))
    q = _float(trade.get("continuation_quality_score"))
    had_take = str(trade.get("had_take_before_exit") or "").lower() in ("true", "1", "yes")
    take_pnl = _float(trade.get("take_pnl_pct"))
    return {
        "session_id": session_id,
        "symbol": trade.get("symbol"),
        "entry_time": trade.get("entry_time"),
        "close_time": trade.get("close_time"),
        "exit_reason": trade.get("close_reason"),
        "realized_pnl_pct": pnl,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "mfe_to_exit_giveback_pct": round((mfe or 0) - (pnl or 0), 4) if mfe is not None and pnl is not None else None,
        "hold_duration_sec": hold,
        "continuation_quality_score": q,
        "quality_tier": trade.get("quality_tier"),
        "quality_band": trade.get("quality_band"),
        "had_take_before_exit": had_take,
        "take_pnl_pct": take_pnl,
        "take_to_exit_pnl_delta": _float(trade.get("take_to_exit_pnl_delta")),
        "tick_count_csv": _float(trade.get("tick_count")),
        **replay,
    }


def _pattern_summary(cases: Sequence[Mapping[str, Any]], exit_reason: str) -> dict[str, Any]:
    if not cases:
        return {"exit_reason": exit_reason, "case_count": 0}

    pnls = [float(c["realized_pnl_pct"]) for c in cases if c.get("realized_pnl_pct") is not None]
    mfes = [float(c["mfe_pct"]) for c in cases if c.get("mfe_pct") is not None]
    maes = [float(c["mae_pct"]) for c in cases if c.get("mae_pct") is not None]
    holds = [float(c["hold_duration_sec"]) for c in cases if c.get("hold_duration_sec") is not None]
    qs = [float(c["continuation_quality_score"]) for c in cases if c.get("continuation_quality_score") is not None]
    givebacks = [
        float(c["giveback_from_peak_pct"])
        for c in cases
        if c.get("giveback_from_peak_pct") is not None
    ]
    peak_pnls = [
        float(c["peak_pnl_pct_replay"])
        for c in cases
        if c.get("peak_pnl_pct_replay") is not None
    ]
    at_fav = [float(c["at_exit_favorable"]) for c in cases if c.get("at_exit_favorable") is not None]
    peak_fav = [float(c["peak_favorable_replay"]) for c in cases if c.get("peak_favorable_replay") is not None]
    fav_ratios = [
        float(c["favorable_vs_peak_ratio"])
        for c in cases
        if c.get("favorable_vs_peak_ratio") is not None
    ]
    had_take_n = sum(1 for c in cases if c.get("had_take_before_exit"))
    match_n = sum(1 for c in cases if c.get("trigger_matches_csv"))
    replay_n = sum(1 for c in cases if c.get("replay_available"))

    flags: Counter[str] = Counter()
    for c in cases:
        pp = _float(c.get("peak_pnl_pct_replay"))
        gb = _float(c.get("giveback_from_peak_pct"))
        h = _float(c.get("hold_duration_sec"))
        if pp is not None and pp < 0.12:
            flags["small_peak_pnl_lt_0.12"] += 1
        if gb is not None and gb >= TRAILING_GIVEBACK_PCT * 0.9:
            flags["giveback_near_full_trailing"] += 1
        if h is not None and h <= 30:
            flags["short_hold_le_30s"] += 1
        if c.get("had_take_before_exit"):
            flags["had_take_before_exit"] += 1
        if exit_reason == "favorable_fade_exit":
            pf = _float(c.get("peak_favorable_replay"))
            if pf is not None and pf < 0.25:
                flags["low_peak_favorable_lt_0.25"] += 1
        slopes = c.get("pre_exit_pnl_slope")
        if slopes is not None and slopes < -0.05:
            flags["sharp_pre_exit_pnl_drop"] += 1

    n = len(cases)
    flag_rates = {k: round(v / n, 4) for k, v in flags.items()}

    return {
        "exit_reason": exit_reason,
        "case_count": n,
        "total_pnl": round(sum(pnls), 4) if pnls else None,
        "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
        "replay_coverage": round(replay_n / n, 4) if n else 0,
        "trigger_match_rate": round(match_n / replay_n, 4) if replay_n else None,
        "had_take_rate": round(had_take_n / n, 4) if n else 0,
        "distributions": {
            "realized_pnl_pct": _dist(pnls),
            "mfe_pct": _dist(mfes),
            "mae_pct": _dist(maes),
            "hold_duration_sec": _dist(holds),
            "continuation_quality_score": _dist(qs),
            "giveback_from_peak_pct": _dist(givebacks),
            "peak_pnl_pct_replay": _dist(peak_pnls),
            "at_exit_favorable": _dist(at_fav),
            "peak_favorable_replay": _dist(peak_fav),
            "favorable_vs_peak_ratio": _dist(fav_ratios),
        },
        "pattern_flags": dict(flags),
        "pattern_flag_rates": flag_rates,
    }


def _recommend(
    mfe_cases: Sequence[Mapping[str, Any]],
    fav_cases: Sequence[Mapping[str, Any]],
    mfe_pat: Mapping[str, Any],
    fav_pat: Mapping[str, Any],
) -> tuple[str, str, list[str], list[dict[str, Any]]]:
    notes: list[str] = []
    candidates: list[dict[str, Any]] = []

    mfe_n = int(mfe_pat.get("case_count") or 0)
    fav_n = int(fav_pat.get("case_count") or 0)
    if mfe_n + fav_n < 5:
        return (
            "keep_current_exit",
            f"Too few pathology cases after vol_liq filter (mfe={mfe_n}, favorable_fade={fav_n})",
            notes,
            candidates,
        )

    mfe_loss = float(mfe_pat.get("total_pnl") or 0)
    fav_loss = float(fav_pat.get("total_pnl") or 0)
    notes.append(f"mfe_giveback aggregate pnl {mfe_loss}; favorable_fade {fav_loss}")

    mfe_flags = mfe_pat.get("pattern_flag_rates") or {}
    fav_flags = fav_pat.get("pattern_flag_rates") or {}

    # Global-only hypotheses (not adopted automatically)
    if mfe_n >= 3 and mfe_flags.get("small_peak_pnl_lt_0.12", 0) >= 0.6:
        candidates.append(
            {
                "hypothesis": "min_peak_pnl_before_mfe_giveback",
                "scope": "global_exit_parameter",
                "evidence": f"{mfe_flags.get('small_peak_pnl_lt_0.12', 0):.0%} of mfe_giveback cases peak_pnl < 0.12%",
                "risk": "May delay exits on legitimate pullbacks; needs OOS replay",
            }
        )
    if mfe_n >= 3 and mfe_flags.get("giveback_near_full_trailing", 0) >= 0.7:
        notes.append("mfe_giveback fires at full TRAILING_GIVEBACK_PCT (0.18) — rule working as designed")

    if fav_n >= 3 and fav_flags.get("low_peak_favorable_lt_0.25", 0) >= 0.5:
        candidates.append(
            {
                "hypothesis": "min_peak_favorable_before_fade_exit",
                "scope": "global_exit_parameter",
                "evidence": f"{fav_flags.get('low_peak_favorable_lt_0.25', 0):.0%} favorable_fade with peak_favorable < 0.25",
                "risk": "May hold losers longer; no symbol/time tuning",
            }
        )

    total_drag = mfe_loss + fav_loss
    notes.append(f"combined pathology pnl {round(total_drag, 4)} on vol_liq population")

    if not candidates:
        return (
            "keep_current_exit",
            "No robust global EXIT tweak pattern; losses are few and heterogeneous",
            notes,
            candidates,
        )

    notes.append(
        "Global hypotheses listed under improvement_hypotheses for optional OOS replay only"
    )
    return (
        "keep_current_exit",
        f"Documented {len(candidates)} research-only global idea(s) but n={mfe_n + fav_n} "
        f"and drag {total_drag:.2f}% do not justify changing combined_structural_exit_v1 now",
        notes,
        candidates,
    )


def main() -> int:
    _bootstrap()
    from small_paper.config import load_pilot_config
    from small_paper.daytrade_suitability_gate import build_vol_liq_threshold

    p84 = _import_phase84()
    p71 = p84._load_phase71()
    pilot = load_pilot_config(VOL_LIQ_CFG)

    parser = argparse.ArgumentParser(description="Phase88 exit pathology review")
    parser.add_argument("--output-dir", type=Path, default=REPORTS)
    args = parser.parse_args()
    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    mfe_cases: list[dict[str, Any]] = []
    fav_cases: list[dict[str, Any]] = []
    sessions_used: list[str] = []

    for sid in p84.discover_sessions(SMALL_PAPER):
        sdir = SMALL_PAPER / sid
        if not (sdir / "structural_trades.csv").is_file():
            continue
        events_path = sdir / "small_paper_events.jsonl"
        events = _load_events(events_path) if events_path.is_file() else []

        push_dir = p84.push_dir_for_key(sid)
        if not push_dir or not push_dir.is_dir():
            continue
        raw = p84.load_trades(sdir, p71)
        if not raw:
            continue
        rows = p84.build_metric_rows(raw, push_dir)
        qrows = p84.filter_quality(rows)
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

        raw_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for t in raw:
            key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            raw_by_key[key].append(dict(t))

        for key in kept_keys:
            queue = raw_by_key.get(key) or []
            if not queue:
                continue
            tr = queue.pop(0)
            reason = str(tr.get("close_reason") or "")
            if reason not in TARGET_REASONS:
                continue
            replay = _replay_hold_path(tr, events) if events else {"replay_available": False}
            case = _case_row(tr, session_id=sid, replay=replay)
            if reason == "mfe_giveback_exit":
                mfe_cases.append(case)
            else:
                fav_cases.append(case)

    mfe_pat = _pattern_summary(mfe_cases, "mfe_giveback_exit")
    fav_pat = _pattern_summary(fav_cases, "favorable_fade_exit")
    decision, rationale, notes, hypotheses = _recommend(mfe_cases, fav_cases, mfe_pat, fav_pat)

    review = {
        "phase": 88,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "policy_id": POLICY_ID,
        "exit_policy": "combined_structural_exit_v1",
        "phase87_reference": "maintain_vol_liq_trial; mfe_giveback/favorable_fade main loss paths",
        "constraints": {
            "forbidden": [
                "symbol_filters",
                "symbol_coefficients",
                "symbol_blacklist_whitelist",
                "time_of_day_filters",
                "time_coefficients",
                "day_of_week_adjustments",
                "session_adjustments",
                "symbol_x_time_adjustments",
            ],
            "unchanged": [
                "entry_logic",
                "quality_calculation",
                "daytrade_suitability",
                "max_concurrent_positions",
                "vol_liq_gate",
            ],
        },
        "population_filter": "quality>=0.70 and volatility_liquidity_top50; structural_trades.csv sessions only",
        "sessions_analyzed": sessions_used,
        "exit_parameters_reference": {
            "TRAILING_GIVEBACK_PCT": TRAILING_GIVEBACK_PCT,
            "FAVORABLE_FADE_RATIO": FAVORABLE_FADE_RATIO,
            "TAKE_QUALITY_DROP": TAKE_QUALITY_DROP,
            "MOMENTUM_WEAKEN_RATIO": MOMENTUM_WEAKEN_RATIO,
        },
        "mfe_giveback_exit": {
            "cases": mfe_cases,
            "pattern_summary": mfe_pat,
        },
        "favorable_fade_exit": {
            "cases": fav_cases,
            "pattern_summary": fav_pat,
        },
        "common_patterns": {
            "mfe_giveback_dominant_flags": mfe_pat.get("pattern_flag_rates"),
            "favorable_fade_dominant_flags": fav_pat.get("pattern_flag_rates"),
            "cross_cutting_note": (
                "Losses concentrated in trailing giveback and favorable decay rules; "
                "no symbol-specific concentration required to explain aggregate drag."
            ),
        },
        "decision": decision,
        "rationale": rationale,
        "evaluation_notes": notes,
        "improvement_hypotheses": hypotheses,
        "overfit_guard": (
            "Any EXIT change must be a single global rule/threshold tested OOS; "
            "never symbol/time/session-specific tuning from this review."
        ),
        "note": "Diagnostic only; no runtime or config changes.",
    }

    out_path = out_dir / "phase88_exit_pathology_review.json"
    out_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": review["decision"],
                "rationale": review["rationale"],
                "mfe_case_count": mfe_pat.get("case_count"),
                "favorable_fade_case_count": fav_pat.get("case_count"),
                "output": str(out_path),
            },
            ensure_ascii=True,
        )
    )
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
