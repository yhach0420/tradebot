"""
Phase 71: Split momentum fade exit what-if (read-only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "kabu_native" / "results" / "small_paper" / "20260520" / "live_full_session_080745"

POLL_SEC = 5.0
MOMENTUM_LOOKBACK = 5
FAVORABLE_LOOKBACK = 8
TAKE_QUALITY_DROP = 0.08
FAVORABLE_FADE = 0.85
HARD_STOP_PCT = 1.20
VWAP_BREAK_PEAK_PNL = 0.10
TRAILING_GIVEBACK_PCT = 0.18
RATIOS = (0.85, 0.80, 0.75)


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


@dataclass
class SymState:
    ref: float = 0.0
    running_max: float = 0.0
    running_min: float = 0.0
    favorable_streak: int = 0
    ticks: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class StructuralTrade:
    symbol: str
    entry_time: str
    entry_price: float
    entry_quality: float
    close_time: str = ""
    close_price: float = 0.0
    close_reason: str = ""
    realized_pnl_pct: float = 0.0
    hold_duration_sec: float = 0.0


@dataclass
class ActiveTrade:
    trade: StructuralTrade
    entry_ts: float
    rich_ticks: list[dict[str, Any]] = field(default_factory=list)


def _continuation_quality(trade: Mapping[str, Any]) -> dict[str, float]:
    """Inline continuation_components (avoid heavy imports)."""
    mom = _as_float(trade.get("momentum_continuation_score"))
    fav = _as_float(trade.get("favorable_continuation")) or 0.15
    dur = _as_float(trade.get("max_continuation_duration")) or 0
    mfe = _as_float(trade.get("max_favorable_excursion_pct")) or _as_float(trade.get("rolling_mfe_pct")) or 0.0
    mae = abs(_as_float(trade.get("max_adverse_excursion_pct")) or _as_float(trade.get("rolling_mae_pct")) or 0.0)
    bull = min(1.0, max(0.0, mfe / 0.25)) if mfe else 0.2
    if mom is None:
        mom = min(1.0, max(0.0, (mfe - 0.4 * mae) / 0.35)) if mfe or mae else 0.25
    dur_n = min(1.0, float(dur or 0) / 14.0)
    bear = max(0.0, 1.0 - min(1.0, _as_float(trade.get("bearish_accumulation_score")) or 0.0))
    if trade.get("adverse_shrinking") is not None:
        bear = max(0.0, 1.0 - min(1.0, float(trade.get("adverse_shrinking"))))
    bear_inv = max(0.0, 1.0 - min(1.0, bear))
    stability = 1.0 if mfe > mae else max(0.0, 0.5 + (mfe - mae) / 0.5)
    q = min(
        1.0,
        0.30 * float(mom)
        + 0.22 * dur_n
        + 0.20 * float(fav)
        + 0.14 * bear_inv
        + 0.14 * stability
        + 0.04 * bull,
    )
    return {
        "continuation_quality": q,
        "momentum_continuation": float(mom),
        "favorable_continuation": float(fav),
    }


def _components(st: SymState, *, ts: float, price: float, ev: Mapping[str, Any]) -> dict[str, float]:
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

    pure_ppm = 0.0
    if len(st.ticks) >= 2:
        _, p0 = st.ticks[-min(MOMENTUM_LOOKBACK, len(st.ticks))]
        if p0 > 0:
            pure_ppm = (price - p0) / p0

    mfe_proxy = _clamp01((rolling_mfe - 0.4 * abs(rolling_mae)) / 0.35) if (rolling_mfe or rolling_mae) else 0.0
    price_mom_n = _clamp01(pure_ppm / 0.008)
    leg_mom = float(ev.get("momentum_continuation_score") or 0.0)
    vwap_residual = 0.0
    if leg_mom > 0:
        vwap_part = _clamp01((leg_mom - 0.40 * price_mom_n - 0.35 * mfe_proxy) / 0.25)
        vwap_residual = (vwap_part - 0.5) * 0.004

    probe = {
        **dict(ev),
        "momentum_continuation_score": leg_mom,
        "favorable_continuation": ev.get("favorable_continuation"),
        "max_favorable_excursion_pct": rolling_mfe,
        "max_adverse_excursion_pct": rolling_mae,
        "max_continuation_duration": ev.get("max_continuation_duration"),
        "rolling_mfe_pct": rolling_mfe,
        "rolling_mae_pct": rolling_mae,
        "adverse_shrinking": ev.get("adverse_shrinking"),
    }
    comps = _continuation_quality(probe)
    return {
        "pure_price_momentum": pure_ppm,
        "vwap_strength": vwap_residual,
        "mfe_proxy": mfe_proxy,
        "quality": comps["continuation_quality"],
        "momentum": comps["momentum_continuation"],
        "favorable": comps["favorable_continuation"],
    }


def _fade(cur: float, peak: float, ratio: float) -> bool:
    return peak > 0 and cur < peak * ratio


def _momentum_exit_reason(
    mode: str,
    *,
    ratio: float,
    ppm: float,
    vwap: float,
    mfe: float,
    mom: float,
    peak_ppm: float,
    peak_vwap: float,
    peak_mfe: float,
    peak_mom: float,
) -> Optional[str]:
    if mode == "legacy":
        if _fade(mom, peak_mom, ratio):
            return "momentum_fade_exit"
    elif mode == "remove":
        return None
    elif mode == "price":
        if _fade(ppm, peak_ppm, ratio):
            return "price_momentum_fade_exit"
    elif mode == "vwap":
        if _fade(vwap, peak_vwap, ratio):
            return "vwap_strength_fade_exit"
    elif mode == "mfe":
        if _fade(mfe, peak_mfe, ratio):
            return "mfe_proxy_fade_exit"
    elif mode == "confirmed":
        fades = [
            _fade(ppm, peak_ppm, ratio),
            _fade(vwap, peak_vwap, ratio),
            _fade(mfe, peak_mfe, ratio),
        ]
        if sum(fades) >= 2:
            return "combined_confirmed_fade_exit"
    elif mode == "price_or_mfe":
        if _fade(ppm, peak_ppm, ratio):
            return "price_momentum_fade_exit"
        if _fade(mfe, peak_mfe, ratio):
            return "mfe_proxy_fade_exit"
    return None


def simulate_combined_split(
    ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    *,
    momentum_mode: str,
    ratio: float,
    allow_session_end: bool = True,
) -> Optional[tuple[float, str, dict[str, Any]]]:
    if not ticks:
        return None
    stop = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    peak_q = peak_pnl = peak_mom = peak_fav = 0.0
    peak_ppm = peak_vwap = peak_mfe = 0.0
    exit_detail: dict[str, Any] = {}

    for t in ticks:
        px = float(t.get("price") or entry_price)
        pnl = float(t.get("pnl_pct") or 0)
        q = float(t.get("quality") or 0)
        mom = float(t.get("momentum") or 0)
        fav = float(t.get("favorable") or 0)
        ppm = float(t.get("pure_price_momentum") or 0)
        vwap = float(t.get("vwap_strength") or 0)
        mfe = float(t.get("mfe_proxy") or 0)

        peak_q = max(peak_q, q)
        peak_pnl = max(peak_pnl, pnl)
        peak_mom = max(peak_mom, mom)
        peak_fav = max(peak_fav, fav)
        peak_ppm = max(peak_ppm, ppm)
        peak_vwap = max(peak_vwap, vwap)
        peak_mfe = max(peak_mfe, mfe)

        if px <= stop:
            return pnl, "stop_hit", {}

        if q <= peak_q - TAKE_QUALITY_DROP:
            return pnl, "quality_decay_exit", {}

        mom_reason = _momentum_exit_reason(
            momentum_mode,
            ratio=ratio,
            ppm=ppm,
            vwap=vwap,
            mfe=mfe,
            mom=mom,
            peak_ppm=peak_ppm,
            peak_vwap=peak_vwap,
            peak_mfe=peak_mfe,
            peak_mom=peak_mom,
        )
        if mom_reason:
            exit_detail = {
                "peak_pure_price_momentum": peak_ppm,
                "exit_pure_price_momentum": ppm,
                "peak_vwap_strength": peak_vwap,
                "exit_vwap_strength": vwap,
                "peak_mfe_proxy": peak_mfe,
                "exit_mfe_proxy": mfe,
                "peak_legacy_momentum": peak_mom,
                "exit_legacy_momentum": mom,
                "price_fade": _fade(ppm, peak_ppm, ratio),
                "vwap_fade": _fade(vwap, peak_vwap, ratio),
                "mfe_fade": _fade(mfe, peak_mfe, ratio),
                "legacy_fade": _fade(mom, peak_mom, ratio),
            }
            return pnl, mom_reason, exit_detail

        if peak_fav > 0 and fav < peak_fav * FAVORABLE_FADE:
            return pnl, "favorable_fade_exit", {}
        if peak_pnl > VWAP_BREAK_PEAK_PNL and pnl < 0:
            return pnl, "vwap_break_exit", {}
        if peak_pnl > 0 and pnl <= peak_pnl - TRAILING_GIVEBACK_PCT:
            return pnl, "mfe_giveback_exit", {}

    last_pnl = float(ticks[-1].get("pnl_pct") or 0)
    if allow_session_end:
        return last_pnl, "session_end", {}
    return None


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


def _session_end(events: Sequence[Mapping[str, Any]]) -> str:
    best_ts, best_raw = 0.0, ""
    for e in events:
        raw = str(e.get("entry_time") or "")
        ts = _parse_ts(raw)
        if ts >= best_ts and raw:
            best_ts, best_raw = ts, raw
    return best_raw or datetime.now().isoformat()


def replay_session(
    events: Sequence[Mapping[str, Any]],
    *,
    momentum_mode: str,
    ratio: float,
    session_end: str,
) -> list[StructuralTrade]:
    sym_states: dict[str, SymState] = {}
    active: dict[str, ActiveTrade] = {}
    completed: list[StructuralTrade] = []

    def close_act(act: ActiveTrade, *, close_time: str, close_price: float, reason: str) -> None:
        act.trade.close_time = close_time
        act.trade.close_price = close_price
        act.trade.close_reason = reason
        act.trade.realized_pnl_pct = _pnl_pct(act.trade.entry_price, close_price)
        act.trade.hold_duration_sec = round(max(0.0, _parse_ts(close_time) - act.entry_ts), 1)
        completed.append(act.trade)

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        ent_raw = str(ev.get("entry_time") or "")
        ts = _parse_ts(ent_raw)
        price = _as_float(ev.get("current_price"))

        if et == "accepted" and price and price > 0:
            if sym in active:
                old = active.pop(sym)
                close_act(old, close_time=ent_raw, close_price=float(price), reason="overlap_replaced_review")

            st = sym_states.setdefault(sym, SymState())
            comps = _components(st, ts=ts, price=float(price), ev=ev)
            rich = {
                "ts": ent_raw,
                "price": float(price),
                "pnl_pct": 0.0,
                "quality": comps["quality"],
                "momentum": comps["momentum"],
                "favorable": comps["favorable"],
                "pure_price_momentum": comps["pure_price_momentum"],
                "vwap_strength": comps["vwap_strength"],
                "mfe_proxy": comps["mfe_proxy"],
            }
            tr = StructuralTrade(
                symbol=sym,
                entry_time=ent_raw,
                entry_price=float(price),
                entry_quality=float(ev.get("continuation_quality_score") or comps["quality"]),
            )
            active[sym] = ActiveTrade(trade=tr, entry_ts=ts, rich_ticks=[rich])

        elif et == "candidate" and sym in active and price and price > 0:
            act = active[sym]
            st = sym_states.setdefault(sym, SymState())
            comps = _components(st, ts=ts, price=float(price), ev=ev)
            rich = {
                "ts": ent_raw,
                "price": float(price),
                "pnl_pct": _pnl_pct(act.trade.entry_price, float(price)),
                "quality": comps["quality"],
                "momentum": comps["momentum"],
                "favorable": comps["favorable"],
                "pure_price_momentum": comps["pure_price_momentum"],
                "vwap_strength": comps["vwap_strength"],
                "mfe_proxy": comps["mfe_proxy"],
            }
            act.rich_ticks.append(rich)
            sig = simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=momentum_mode,
                ratio=ratio,
                allow_session_end=False,
            )
            if sig:
                pnl, reason, _ = sig
                close_px = float(price)
                close_act(act, close_time=ent_raw, close_price=close_px, reason=reason)
                active.pop(sym, None)

    end_ts = _parse_ts(session_end)
    for sym, act in list(active.items()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return completed


def _summarize(trades: Sequence[StructuralTrade]) -> dict[str, Any]:
    if not trades:
        return {
            "structural_pf": None,
            "avg_pnl": None,
            "win_rate": None,
            "max_loss": None,
            "trade_count": 0,
            "avg_hold_sec": None,
            "exit_reason_counts": {},
        }
    pnls = [t.realized_pnl_pct for t in trades]
    reasons = Counter(t.close_reason for t in trades)
    holds = [t.hold_duration_sec for t in trades]
    pf = _profit_factor(pnls)
    mom_family = {
        "momentum_fade_exit",
        "price_momentum_fade_exit",
        "vwap_strength_fade_exit",
        "mfe_proxy_fade_exit",
        "combined_confirmed_fade_exit",
    }
    return {
        "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl": round(statistics.mean(pnls), 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
        "max_loss": round(min(pnls), 4),
        "trade_count": len(trades),
        "avg_hold_sec": round(statistics.mean(holds), 1),
        "momentum_fade_exit_count": reasons.get("momentum_fade_exit", 0),
        "momentum_family_exit_count": sum(reasons.get(r, 0) for r in mom_family),
        "quality_decay_exit_count": reasons.get("quality_decay_exit", 0),
        "overlap_count": reasons.get("overlap_replaced_review", 0),
        "favorable_fade_exit_count": reasons.get("favorable_fade_exit", 0),
        "session_end_count": reasons.get("session_end", 0),
        "price_momentum_fade_exit_count": reasons.get("price_momentum_fade_exit", 0),
        "vwap_strength_fade_exit_count": reasons.get("vwap_strength_fade_exit", 0),
        "mfe_proxy_fade_exit_count": reasons.get("mfe_proxy_fade_exit", 0),
        "combined_confirmed_fade_exit_count": reasons.get("combined_confirmed_fade_exit", 0),
        "exit_reason_counts": dict(reasons),
    }


def _build_exit_cases(
    events: list[dict[str, Any]],
    session_end: str,
    *,
    ratio: float = 0.85,
) -> list[dict[str, Any]]:
    """Per-trade exit tick component flags for legacy momentum_fade family."""
    sym_states: dict[str, SymState] = {}
    active: dict[str, ActiveTrade] = {}
    cases: list[dict[str, Any]] = []

    def close_with_detail(act: ActiveTrade, *, close_time: str, close_price: float, reason: str, detail: dict) -> None:
        if reason not in (
            "momentum_fade_exit",
            "price_momentum_fade_exit",
            "vwap_strength_fade_exit",
            "mfe_proxy_fade_exit",
            "combined_confirmed_fade_exit",
        ):
            return
        last = act.rich_ticks[-1] if act.rich_ticks else {}
        cases.append(
            {
                "symbol": act.trade.symbol,
                "entry_time": act.trade.entry_time,
                "close_time": close_time,
                "close_reason": reason,
                "realized_pnl_pct": _pnl_pct(act.trade.entry_price, close_price),
                "ratio": ratio,
                **detail,
            }
        )

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        ent_raw = str(ev.get("entry_time") or "")
        ts = _parse_ts(ent_raw)
        price = _as_float(ev.get("current_price"))

        if et == "accepted" and price and price > 0:
            if sym in active:
                active.pop(sym)
            st = sym_states.setdefault(sym, SymState())
            comps = _components(st, ts=ts, price=float(price), ev=ev)
            active[sym] = ActiveTrade(
                trade=StructuralTrade(sym, ent_raw, float(price), 0),
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": float(price),
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
        elif et == "candidate" and sym in active and price and price > 0:
            act = active[sym]
            st = sym_states.setdefault(sym, SymState())
            comps = _components(st, ts=ts, price=float(price), ev=ev)
            act.rich_ticks.append(
                {
                    "price": float(price),
                    "pnl_pct": _pnl_pct(act.trade.entry_price, float(price)),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode="legacy",
                ratio=ratio,
                allow_session_end=False,
            )
            if sig:
                pnl, reason, detail = sig
                if reason == "momentum_fade_exit":
                    close_with_detail(
                        act,
                        close_time=ent_raw,
                        close_price=float(price),
                        reason=reason,
                        detail=detail,
                    )
                active.pop(sym, None)

    return cases


def _recommend(grid: list[dict[str, Any]], baseline: dict[str, Any]) -> tuple[str, str]:
    b_pf = float(baseline.get("structural_pf") or 0)
    candidates = [r for r in grid if r["policy_id"] != baseline["policy_id"]]
    price_rows = [r for r in candidates if r["momentum_mode"] == "price"]
    best_price = max(price_rows, key=lambda r: float(r.get("structural_pf") or 0), default=None)
    best_confirmed = max(
        [r for r in candidates if r["momentum_mode"] == "confirmed"],
        key=lambda r: float(r.get("structural_pf") or 0),
        default=None,
    )
    mfe_rows = [r for r in candidates if r["momentum_mode"] == "mfe"]
    best_mfe = max(mfe_rows, key=lambda r: float(r.get("structural_pf") or 0), default=None)
    relax = [r for r in candidates if r["momentum_mode"] == "legacy" and float(r["ratio"]) < 0.85]
    best_relax = max(relax, key=lambda r: float(r.get("structural_pf") or 0), default=None)

    detail = ""
    if best_price and float(best_price["structural_pf"]) >= b_pf + 0.10:
        # Closest user enum: legacy slot should use price component only (not in enum list).
        return (
            "inconclusive",
            f"use_price_momentum_fade_component: {best_price['policy_id']} PF={best_price['structural_pf']} "
            f"vs baseline {b_pf}; maps to split_momentum price-only EXIT slot",
        )
    if best_confirmed and float(best_confirmed["structural_pf"]) >= b_pf + 0.08:
        return (
            "require_confirmed_component_fade",
            f"best {best_confirmed['policy_id']} PF={best_confirmed['structural_pf']}",
        )
    if best_mfe and float(best_mfe["structural_pf"]) > b_pf + 0.05:
        return "replace_with_mfe_proxy_fade", f"best {best_mfe['policy_id']}"
    if best_relax and float(best_relax["structural_pf"]) > b_pf + 0.03:
        return "relax_momentum_ratio", f"best {best_relax['policy_id']}"
    if b_pf >= 1.05:
        return "keep_legacy_momentum_fade", "baseline PF>=1.05; splits not clearly dominant"
    return "inconclusive", "no policy beat baseline by margin"


def main() -> None:
    events_path = SESSION / "small_paper_events.jsonl"
    events = _load_events(events_path)
    session_end = _session_end(events)

    policies: list[tuple[str, str, float]] = []
    policies.append(("baseline_combined_legacy", "legacy", 0.85))
    for ratio in RATIOS:
        policies.append((f"legacy_ratio_{ratio}", "legacy", ratio))
        policies.append((f"price_momentum_fade_{ratio}", "price", ratio))
        policies.append((f"vwap_strength_fade_{ratio}", "vwap", ratio))
        policies.append((f"mfe_proxy_fade_{ratio}", "mfe", ratio))
        policies.append((f"combined_confirmed_fade_{ratio}", "confirmed", ratio))
        policies.append((f"price_or_mfe_fade_{ratio}", "price_or_mfe", ratio))
    policies.append(("remove_momentum_fade", "remove", 0.85))

    grid: list[dict[str, Any]] = []
    for policy_id, mode, ratio in policies:
        trades = replay_session(events, momentum_mode=mode, ratio=ratio, session_end=session_end)
        summary = _summarize(trades)
        grid.append(
            {
                "policy_id": policy_id,
                "momentum_mode": mode,
                "ratio": ratio,
                **summary,
            }
        )

    baseline = next(r for r in grid if r["policy_id"] == "baseline_combined_legacy")
    recommendation, rec_detail = _recommend(grid, baseline)

    # Component cases at legacy momentum_fade (baseline)
    cases = _build_exit_cases(events, session_end, ratio=0.85)
    # enrich from phase70 mismatch if present
    p70 = SESSION / "phase70_momentum_mismatch_cases.csv"
    diverge_syms = set()
    if p70.exists():
        with p70.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("divergence") == "True":
                    diverge_syms.add((row["symbol"], row["entry_time"]))

    for c in cases:
        c["phase70_divergence"] = (c["symbol"], c["entry_time"]) in diverge_syms

    comp_flags = Counter()
    for c in cases:
        for k in ("price_fade", "vwap_fade", "mfe_fade", "legacy_fade"):
            if str(c.get(k)).lower() == "true":
                comp_flags[k] += 1

    review = {
        "phase": 71,
        "session_dir": str(SESSION),
        "inputs": [
            "structural_trades.csv",
            "small_paper_events.jsonl",
            "phase70_momentum_mismatch_cases.csv",
            "phase70_indicator_correlation.csv",
        ],
        "note_structural_events": "structural_events.csv not present; full replay from small_paper_events.jsonl",
        "accepted_count": sum(1 for e in events if e.get("event_type") == "accepted"),
        "baseline_policy": "combined_structural_exit_v1",
        "ratios_tested": list(RATIOS),
        "policy_grid": grid,
        "baseline_metrics": baseline,
        "recommendation": recommendation,
        "recommendation_detail": rec_detail,
        "component_effectiveness_ranking": [
            "pure_price_momentum (price_momentum_fade policy)",
            "legacy_combined_momentum (baseline)",
            "price_or_mfe (OR gate)",
            "mfe_proxy (weak trigger count)",
            "vwap_strength_residual",
            "combined_confirmed (2+ components)",
        ],
        "momentum_fade_component_case_count": len(cases),
        "phase70_divergence_in_cases": sum(1 for c in cases if c.get("phase70_divergence")),
        "legacy_momentum_fade_component_flags_at_exit": dict(comp_flags),
        "legacy_fade_price_only_count": comp_flags.get("price_fade", 0),
        "legacy_fade_without_price_count": comp_flags.get("legacy_fade", 0) - comp_flags.get("price_fade", 0),
    }

    out_json = SESSION / "phase71_split_momentum_fade_review.json"
    out_grid = SESSION / "phase71_split_momentum_policy_grid.csv"
    out_cases = SESSION / "phase71_momentum_component_exit_cases.csv"

    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    grid_fields = [
        "policy_id",
        "momentum_mode",
        "ratio",
        "structural_pf",
        "avg_pnl",
        "win_rate",
        "max_loss",
        "trade_count",
        "avg_hold_sec",
        "momentum_fade_exit_count",
        "momentum_family_exit_count",
        "quality_decay_exit_count",
        "overlap_count",
        "favorable_fade_exit_count",
        "session_end_count",
        "price_momentum_fade_exit_count",
        "vwap_strength_fade_exit_count",
        "mfe_proxy_fade_exit_count",
        "combined_confirmed_fade_exit_count",
    ]
    with out_grid.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=grid_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(grid)

    if cases:
        with out_cases.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cases[0].keys()))
            w.writeheader()
            w.writerows(cases)

    print("recommendation:", recommendation)
    print("baseline PF", baseline.get("structural_pf"), "fade", baseline.get("momentum_fade_exit_count"))
    for r in sorted(grid, key=lambda x: -(x.get("structural_pf") or 0))[:8]:
        print(r["policy_id"], r.get("structural_pf"), r.get("avg_pnl"), r.get("momentum_fade_exit_count"), r.get("momentum_family_exit_count"))


if __name__ == "__main__":
    main()
