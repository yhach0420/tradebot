"""
Phase 70: Decomposed indicator what-if (read-only diagnosis).
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "kabu_native" / "results" / "small_paper" / "20260520" / "live_full_session_080745"

POLL_SEC = 5.0
MOMENTUM_LOOKBACK = 5
FAVORABLE_LOOKBACK = 8
DURATION_SCALE = 14.0
MOMENTUM_WEAKEN = 0.85

DECOMPOSED = [
    "pure_price_momentum",
    "price_velocity_score",
    "pure_vwap_strength_residual",
    "momentum_acceleration",
    "pure_favorable_tick_ratio",
    "favorable_streak_score",
    "mfe_mae_edge",
    "adverse_pressure",
    "quality_decomposed_v2",
    "momentum_continuation_score_legacy",
    "continuation_quality_score_legacy",
    "favorable_continuation_legacy",
    "max_continuation_duration_legacy",
    "rolling_mfe_pct_entry",
    "rolling_mae_pct_entry",
]


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


@dataclass
class SymState:
    ref: float = 0.0
    running_max: float = 0.0
    running_min: float = 0.0
    favorable_streak: int = 0
    max_favorable_streak: int = 0
    ticks: list[tuple[float, float]] = field(default_factory=list)  # ts, price
    prev_pure_price_mom: Optional[float] = None


def _compute_tick(
    st: SymState, *, ts: float, price: float, legacy: dict[str, Any]
) -> dict[str, float]:
    if st.ref <= 0:
        st.ref = price
        st.running_max = price
        st.running_min = price

    st.running_max = max(st.running_max, price)
    st.running_min = min(st.running_min, price)
    st.ticks.append((ts, price))
    if len(st.ticks) > 120:
        st.ticks.pop(0)

    ref = st.ref
    rolling_mfe = max(0.0, (st.running_max - ref) / ref) if ref > 0 else 0.0
    rolling_mae = min(0.0, (st.running_min - ref) / ref) if ref > 0 else 0.0

    recent = st.ticks[-FAVORABLE_LOOKBACK:]
    recent_low = min(p for _, p in recent) if recent else price
    fav_hits = sum(1 for _, p in recent if p > ref or p > recent_low * 1.0001)
    pure_fav_tick = fav_hits / len(recent) if recent else 0.0

    if price > ref or price > recent_low:
        st.favorable_streak += 1
    else:
        st.favorable_streak = 0
    st.max_favorable_streak = max(st.max_favorable_streak, st.favorable_streak)

    pure_ppm = 0.0
    p0_ts = 0.0
    if len(st.ticks) >= 2:
        idx = -min(MOMENTUM_LOOKBACK, len(st.ticks))
        p0_ts, p0 = st.ticks[idx]
        if p0 > 0:
            pure_ppm = (price - p0) / p0

    dt = max(ts - p0_ts, POLL_SEC) if len(st.ticks) >= 2 else POLL_SEC
    price_velocity = pure_ppm / dt if dt > 0 else 0.0

    mfe_proxy = _clamp01((rolling_mfe - 0.4 * abs(rolling_mae)) / 0.35) if (rolling_mfe or rolling_mae) else 0.0
    price_mom_n = _clamp01(pure_ppm / 0.008)

    leg_mom = float(legacy.get("momentum_continuation_score") or 0.0)
    vwap_residual = 0.0
    if leg_mom > 0 and 0.25 > 0:
        vwap_part = _clamp01((leg_mom - 0.40 * price_mom_n - 0.35 * mfe_proxy) / 0.25)
        vwap_residual = (vwap_part - 0.5) * 0.004

    mom_accel = 0.0
    if st.prev_pure_price_mom is not None:
        mom_accel = pure_ppm - st.prev_pure_price_mom
    st.prev_pure_price_mom = pure_ppm

    fav_streak_score = min(1.0, st.favorable_streak / DURATION_SCALE)
    mfe_mae_edge = rolling_mfe - abs(rolling_mae)
    adverse_pressure = abs(rolling_mae)

    q2 = _clamp01(
        0.25 * price_mom_n
        + 0.15 * _clamp01(abs(price_velocity) / 0.0016)
        + 0.10 * _clamp01(0.5 + vwap_residual / 0.004)
        + 0.10 * _clamp01(abs(mom_accel) / 0.004)
        + 0.15 * pure_fav_tick
        + 0.10 * fav_streak_score
        + 0.10 * _clamp01(mfe_mae_edge / 0.003)
        + 0.05 * _clamp01(1.0 - adverse_pressure / 0.01)
    )

    return {
        "pure_price_momentum": round(pure_ppm, 6),
        "price_velocity_score": round(price_velocity, 8),
        "pure_vwap_strength_residual": round(vwap_residual, 6),
        "momentum_acceleration": round(mom_accel, 6),
        "pure_favorable_tick_ratio": round(pure_fav_tick, 4),
        "favorable_streak_score": round(fav_streak_score, 4),
        "mfe_mae_edge": round(mfe_mae_edge, 6),
        "adverse_pressure": round(adverse_pressure, 6),
        "quality_decomposed_v2": round(q2, 4),
        "momentum_continuation_score_legacy": leg_mom,
        "continuation_quality_score_legacy": float(legacy.get("continuation_quality_score") or 0),
        "favorable_continuation_legacy": float(legacy.get("favorable_continuation") or 0),
        "max_continuation_duration_legacy": float(legacy.get("max_continuation_duration") or 0),
        "rolling_mfe_pct": round(rolling_mfe, 6),
        "rolling_mae_pct": round(rolling_mae, 6),
    }


def _load_events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            ev = json.loads(line)
            p = ev.get("payload") or ev
            rows.append(
                {
                    **p,
                    "event_type": ev.get("event_type") or p.get("event_type"),
                    "message_index": int(ev.get("message_index") or p.get("message_index") or 0),
                }
            )
    rows.sort(key=lambda r: r.get("message_index", 0))
    return rows


def _tick_index(events: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, float]]:
    """Map (symbol, entry_time_iso) -> decomposed metrics for each candidate/accepted tick."""
    states: dict[str, SymState] = {}
    index: dict[tuple[str, str], dict[str, float]] = {}
    for ev in events:
        et = str(ev.get("event_type") or "")
        if et not in ("candidate", "accepted"):
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        price = ev.get("current_price")
        if not sym or not ent or price is None:
            continue
        price_f = float(price)
        if price_f <= 0:
            continue
        ts = _parse_ts(ent)
        st = states.setdefault(sym, SymState())
        metrics = _compute_tick(st, ts=ts, price=price_f, legacy=ev)
        index[(sym, ent)] = metrics
    return index


def _trades(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _trade_ticks(
    sym: str, ent: float, ex: float, tick_index: dict[tuple[str, str], dict[str, float]]
) -> list[tuple[str, dict[str, float]]]:
    ticks = [
        (k[1], v)
        for k, v in tick_index.items()
        if k[0] == sym and ent <= _parse_ts(k[1]) <= ex
    ]
    ticks.sort(key=lambda x: _parse_ts(x[0]))
    return ticks


def _adoption_verdict(corr_pnl: Optional[float], corr_win: Optional[float], excl: dict) -> str:
    cp = corr_pnl if corr_pnl is not None else excl.get("corr_pnl")
    cw = corr_win if corr_win is not None else excl.get("corr_win")
    if cp is None:
        return "inconclusive"
    if cp >= 0.15:
        return "adopt_candidate"
    if cp <= -0.10:
        return "reject_proxy"
    return "monitor"


def main() -> None:
    events_path = SESSION / "small_paper_events.jsonl"
    trades_path = SESSION / "structural_trades.csv"

    events = _load_events(events_path)
    tick_index = _tick_index(events)
    trades = _trades(trades_path)

    trade_records: list[dict[str, Any]] = []
    for tr in trades:
        sym = tr["symbol"]
        ent_s, ex_s = tr["entry_time"], tr["close_time"]
        ent, ex = _parse_ts(ent_s), _parse_ts(ex_s)
        pnl = float(tr["realized_pnl_pct"])
        ticks = _trade_ticks(sym, ent, ex, tick_index)
        entry_m = dict(ticks[0][1] if ticks else tick_index.get((sym, ent_s), {}))
        exit_m = dict(ticks[-1][1] if ticks else entry_m)
        entry_m["rolling_mfe_pct_entry"] = entry_m.get("rolling_mfe_pct", 0)
        entry_m["rolling_mae_pct_entry"] = entry_m.get("rolling_mae_pct", 0)
        trade_records.append(
            {
                "symbol": sym,
                "entry_time": ent_s,
                "close_time": ex_s,
                "close_reason": tr["close_reason"],
                "realized_pnl_pct": pnl,
                "win": pnl > 0,
                "tick_count": len(ticks),
                "entry": entry_m,
                "exit": exit_m,
            }
        )

    all_tr = trade_records
    excl = [t for t in trade_records if t["close_reason"] != "overlap_replaced_review"]

    # A/B correlations
    corr_rows: list[dict[str, Any]] = []
    for ind in DECOMPOSED:
        for label, subset in (("all_structural_trades", all_tr), ("excl_overlap", excl)):
            xs, ys, wins = [], [], []
            for t in subset:
                v = t["entry"].get(ind)
                if v is None:
                    continue
                xs.append(float(v))
                ys.append(float(t["realized_pnl_pct"]))
                wins.append(t["win"])
            corr_rows.append(
                {
                    "indicator": ind,
                    "subset": label,
                    "n": len(xs),
                    "corr_with_pnl_pct": _pearson(xs, ys),
                    "corr_with_win": _pearson(xs, [1.0 if w else 0.0 for w in wins]),
                }
            )

    # C exit reason breakdown
    by_reason: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for t in all_tr:
        reason = t["close_reason"]
        for ind in DECOMPOSED:
            v = t["entry"].get(ind)
            if v is not None:
                by_reason[reason][ind].append(float(v))

    exit_breakdown: list[dict[str, Any]] = []
    for reason in sorted(by_reason.keys()):
        counts = [len(v) for v in by_reason[reason].values()]
        row: dict[str, Any] = {"close_reason": reason, "n": max(counts) if counts else 0}
        for ind in DECOMPOSED:
            vals = by_reason[reason].get(ind, [])
            row[f"{ind}_mean"] = round(statistics.mean(vals), 6) if vals else None
            row[f"{ind}_median"] = round(statistics.median(vals), 6) if vals else None
        exit_breakdown.append(row)

    # D momentum_fade mismatch
    fade_cases: list[dict[str, Any]] = []
    for t in all_tr:
        if t["close_reason"] != "momentum_fade_exit":
            continue
        ent_m, ex_m = t["entry"], t["exit"]
        sym = t["symbol"]
        ticks = _trade_ticks(sym, _parse_ts(t["entry_time"]), _parse_ts(t["close_time"]), tick_index)
        peak_legacy = max(float(x[1].get("momentum_continuation_score_legacy") or 0) for x in ticks) if ticks else 0
        peak_ppm = max(float(x[1].get("pure_price_momentum") or 0) for x in ticks) if ticks else 0
        leg_exit = float(ex_m.get("momentum_continuation_score_legacy") or 0)
        ppm_exit = float(ex_m.get("pure_price_momentum") or 0)
        legacy_fade = peak_legacy > 0 and leg_exit < peak_legacy * MOMENTUM_WEAKEN
        ppm_fade = peak_ppm > 0 and ppm_exit < peak_ppm * MOMENTUM_WEAKEN
        fade_cases.append(
            {
                "symbol": sym,
                "entry_time": t["entry_time"],
                "close_time": t["close_time"],
                "realized_pnl_pct": t["realized_pnl_pct"],
                "peak_legacy_momentum": round(peak_legacy, 4),
                "exit_legacy_momentum": round(leg_exit, 4),
                "legacy_fade_threshold": round(peak_legacy * MOMENTUM_WEAKEN, 4),
                "legacy_fade_fires": legacy_fade,
                "peak_pure_price_momentum": round(peak_ppm, 6),
                "exit_pure_price_momentum": round(ppm_exit, 6),
                "ppm_fade_threshold": round(peak_ppm * MOMENTUM_WEAKEN, 6),
                "pure_price_fade_would_fire": ppm_fade,
                "divergence": legacy_fade and not ppm_fade,
                "exit_mfe_mae_edge": ex_m.get("mfe_mae_edge"),
                "exit_price_velocity": ex_m.get("price_velocity_score"),
            }
        )

    diverge_count = sum(1 for c in fade_cases if c["divergence"])

    # F adoption from excl_overlap correlations
    excl_corr = {r["indicator"]: r for r in corr_rows if r["subset"] == "excl_overlap"}
    adoption: dict[str, str] = {}
    for ind in DECOMPOSED:
        if ind.endswith("_legacy"):
            continue
        r = excl_corr.get(ind, {})
        adoption[ind] = _adoption_verdict(r.get("corr_with_pnl_pct"), r.get("corr_with_win"), {})

    # Recommendation logic (excl_overlap entry-time correlations)
    ppm_corr = excl_corr.get("pure_price_momentum", {}).get("corr_with_pnl_pct")
    leg_corr = excl_corr.get("momentum_continuation_score_legacy", {}).get("corr_with_pnl_pct")
    edge_corr = excl_corr.get("mfe_mae_edge", {}).get("corr_with_pnl_pct")
    vel_corr = excl_corr.get("price_velocity_score", {}).get("corr_with_pnl_pct")
    q2_corr = excl_corr.get("quality_decomposed_v2", {}).get("corr_with_pnl_pct")
    qleg_corr = excl_corr.get("continuation_quality_score_legacy", {}).get("corr_with_pnl_pct")
    mfe_legacy = excl_corr.get("rolling_mfe_pct_entry", {}).get("corr_with_pnl_pct")

    rec = "inconclusive"
    if ppm_corr is not None and leg_corr is not None:
        if ppm_corr > leg_corr + 0.05 and ppm_corr > 0.05:
            rec = "replace_momentum_with_pure_price_momentum"
        elif ppm_corr >= leg_corr - 0.02 and ppm_corr <= leg_corr + 0.02:
            rec = "keep_current_momentum"
        elif diverge_count >= 5 or (edge_corr is not None and edge_corr > ppm_corr):
            rec = "split_momentum_into_price_vwap_mfe"
        elif leg_corr > ppm_corr:
            rec = "keep_current_momentum"
        else:
            rec = "split_momentum_into_price_vwap_mfe"

    rec_quality = "inconclusive"
    if q2_corr is not None and qleg_corr is not None:
        if q2_corr > qleg_corr + 0.05 or (qleg_corr < -0.08 and q2_corr > qleg_corr):
            rec_quality = "redesign_quality_score"
        else:
            rec_quality = "inconclusive"
    if mfe_legacy is not None and mfe_legacy > (q2_corr or -1) + 0.15:
        rec_quality = "redesign_quality_score"

    review = {
        "phase": 70,
        "session_dir": str(SESSION),
        "inputs": [
            "structural_trades.csv",
            "small_paper_events.jsonl",
            "phase68_variable_audit.csv",
            "phase69_indicator_correlation.csv",
        ],
        "notes": {
            "pure_vwap_strength": (
                "VWAP not in events; pure_vwap_strength_residual back-solved from "
                "legacy momentum minus price_mom and mfe_proxy components"
            ),
            "poll_interval_sec": POLL_SEC,
            "quality_decomposed_v2": "diagnostic weighted blend of single-phenomenon proxies only",
        },
        "structural_trade_count": len(all_tr),
        "excl_overlap_count": len(excl),
        "momentum_fade_exit_count": len(fade_cases),
        "momentum_fade_divergence_count": diverge_count,
        "divergence_definition": "legacy momentum_fade fires but pure_price_momentum fade would not",
        "correlations": corr_rows,
        "exit_reason_indicator_means": exit_breakdown,
        "indicator_adoption_verdict_excl_overlap": adoption,
        "recommendation": rec,
        "recommendation_quality": rec_quality,
        "recommendation_momentum_fade": (
            "split_momentum_into_price_vwap_mfe"
            if diverge_count >= 5
            else "keep_current_momentum"
        ),
        "recommendation_detail": {
            "pure_price_momentum_vs_legacy_momentum_corr_pnl_excl": {
                "pure_price_momentum": ppm_corr,
                "momentum_continuation_score_legacy": leg_corr,
            },
            "mfe_mae_edge_corr_pnl_excl": edge_corr,
            "quality_decomposed_v2_corr_pnl_excl": q2_corr,
            "continuation_quality_legacy_corr_pnl_excl": qleg_corr,
            "rolling_mfe_pct_entry_corr_pnl_excl": mfe_legacy,
        },
        "per_indicator_adoption_summary": {
            ind: {
                "corr_pnl_excl": excl_corr.get(ind, {}).get("corr_with_pnl_pct"),
                "corr_win_excl": excl_corr.get(ind, {}).get("corr_with_win"),
                "verdict": adoption.get(ind),
            }
            for ind in DECOMPOSED
            if not ind.endswith("_legacy") or ind in (
                "momentum_continuation_score_legacy",
                "continuation_quality_score_legacy",
            )
        },
    }

    out_json = SESSION / "phase70_decomposed_indicator_review.json"
    out_corr = SESSION / "phase70_indicator_correlation.csv"
    out_exit = SESSION / "phase70_exit_reason_indicator_breakdown.csv"
    out_mom = SESSION / "phase70_momentum_mismatch_cases.csv"

    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    with out_corr.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(corr_rows[0].keys()))
        w.writeheader()
        w.writerows(corr_rows)

    if exit_breakdown:
        fields = list(exit_breakdown[0].keys())
        with out_exit.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(exit_breakdown)

    if fade_cases:
        with out_mom.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fade_cases[0].keys()))
            w.writeheader()
            w.writerows(fade_cases)

    print("recommendation:", rec)
    print("recommendation_quality:", rec_quality)
    print("fade divergences:", diverge_count, "/", len(fade_cases))
    for r in corr_rows:
        if r["subset"] == "excl_overlap" and not r["indicator"].endswith("_legacy"):
            print(r["indicator"], r["corr_with_pnl_pct"], r["corr_with_win"])


if __name__ == "__main__":
    main()
