"""
Phase 56: TAKE signal decomposition + quality inflation review (analysis only).

No changes to ENTRY/EXIT/cap/quality threshold/time bands.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from unittest.mock import patch
from zoneinfo import ZoneInfo

from research.continuation_quality_ranking import continuation_components
from research.research_exit_criteria import _as_float
from research.runtime_exit_review import (
    TradeRuntimePath,
    _parse_dt,
    _parse_ts,
    _pnl_pct,
    _replay_trade_paths,
    _trade_key,
)
from research.runtime_pilot_policy_review import _build_price_index
from research.small_paper_performance_review import (
    _build_trade_lifecycles,
    _load_events,
    _load_json,
    _profit_factor,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot

JST = ZoneInfo("Asia/Tokyo")

QUALITY_INFLATION_BANDS = (
    ("0.70_0.75", 0.70, 0.75),
    ("0.75_0.80", 0.75, 0.80),
    ("0.80_0.85", 0.80, 0.85),
    ("0.85_plus", 0.85, 1.01),
)

EXTENDED_THRESHOLD_PCT = 0.05
VERDICT_CONTINUE = "continue_current_trial"
VERDICT_NEED_QUALITY = "need_quality_review"


@dataclass
class TakeDecompositionRow:
    symbol: str
    entry_time: str
    take_time: str
    take_reason_primary: str
    take_quality: float
    take_pnl_pct: float
    momentum_decay: bool
    favorable_fade: bool
    quality_deterioration: bool
    display_take_target: bool
    unrealized_pnl_near_take: bool
    vwap_deterioration_proxy: bool
    continuation_weakening_labeled: bool
    momentum_at_take: float
    peak_momentum: float
    momentum_ratio: float
    favorable_at_take: float
    peak_favorable: float
    favorable_ratio: float
    quality_at_take: float
    peak_quality: float
    quality_drop: float
    contributing_factors: str
    extended_30s: bool
    extended_60s: bool
    extended_120s: bool
    extended_300s: bool
    max_upside_30s_pct: Optional[float]
    max_upside_60s_pct: Optional[float]
    max_upside_120s_pct: Optional[float]
    max_upside_300s_pct: Optional[float]


def _vwap_deterioration_proxy(
    entry_px: float,
    price: float,
    ticks_before: Sequence[Mapping[str, Any]],
) -> bool:
    """Price below entry after prior MFE (VWAP field often absent on PUSH)."""
    peak_pnl = 0.0
    for t in ticks_before:
        pnl = float(t.get("pnl_pct") or 0)
        peak_pnl = max(peak_pnl, pnl)
    cur = _pnl_pct(entry_px, price)
    return peak_pnl > 0.1 and cur < 0


def _contributing_factors(flags: Mapping[str, bool]) -> list[str]:
    order = (
        "display_take_target",
        "quality_deterioration",
        "favorable_fade",
        "momentum_decay",
        "unrealized_pnl_near_take",
        "vwap_deterioration_proxy",
    )
    return [k for k in order if flags.get(k)]


def _decompose_take_at_signal(
    path: TradeRuntimePath,
    *,
    ctx: Mapping[str, Any],
    trade: Mapping[str, Any],
    cfg: Any,
    price: float,
) -> TakeDecompositionRow:
    comps = continuation_components(trade)
    q = float(comps["continuation_quality"])
    mom = float(comps["momentum_continuation"])
    fav = float(comps["favorable_continuation"])
    pnl = float(ctx.get("unrealized_pnl_pct") or 0)

    peak_q = max(
        [path.entry_quality]
        + [float(t.get("quality") or 0) for t in path.ticks]
        + [q],
    )
    peak_pnl = float(ctx.get("peak_pnl_pct") or 0)
    peak_mom = max([mom] + [float(t.get("momentum") or 0) for t in path.ticks])
    peak_fav = max([fav] + [float(t.get("favorable") or 0) for t in path.ticks])

    take_price = path.entry_price * (1.0 + cfg.display_take_pct / 100.0)
    flags = {
        "display_take_target": price >= take_price,
        "quality_deterioration": q <= peak_q - cfg.take_quality_drop,
        "favorable_fade": fav < peak_fav * cfg.favorable_fade_ratio,
        "momentum_decay": mom < peak_mom * cfg.momentum_weaken_ratio,
        "unrealized_pnl_near_take": pnl >= cfg.display_take_pct * 0.9,
        "vwap_deterioration_proxy": _vwap_deterioration_proxy(
            path.entry_price, price, path.ticks
        ),
    }
    primary = str(ctx.get("take_reason") or path.take.get("take_reason") if path.take else "")
    if not primary:
        for name, active in (
            ("display_take_target", flags["display_take_target"]),
            ("quality_deterioration", flags["quality_deterioration"]),
            ("favorable_fade", flags["favorable_fade"]),
            ("momentum_decay", flags["momentum_decay"]),
            ("unrealized_pnl_near_take", flags["unrealized_pnl_near_take"]),
        ):
            if active:
                primary = name if name != "momentum_decay" else "continuation_weakening"
                break

    return TakeDecompositionRow(
        symbol=path.symbol,
        entry_time=path.entry_time,
        take_time=str(ctx.get("timestamp") or (path.take or {}).get("take_time", "")),
        take_reason_primary=primary,
        take_quality=q,
        take_pnl_pct=pnl,
        momentum_decay=flags["momentum_decay"],
        favorable_fade=flags["favorable_fade"],
        quality_deterioration=flags["quality_deterioration"],
        display_take_target=flags["display_take_target"],
        unrealized_pnl_near_take=flags["unrealized_pnl_near_take"],
        vwap_deterioration_proxy=flags["vwap_deterioration_proxy"],
        continuation_weakening_labeled=primary == "continuation_weakening",
        momentum_at_take=round(mom, 4),
        peak_momentum=round(peak_mom, 4),
        momentum_ratio=round(mom / peak_mom, 4) if peak_mom > 0 else 0.0,
        favorable_at_take=round(fav, 4),
        peak_favorable=round(peak_fav, 4),
        favorable_ratio=round(fav / peak_fav, 4) if peak_fav > 0 else 0.0,
        quality_at_take=round(q, 4),
        peak_quality=round(peak_q, 4),
        quality_drop=round(peak_q - q, 4),
        contributing_factors="|".join(_contributing_factors(flags)),
        extended_30s=False,
        extended_60s=False,
        extended_120s=False,
        extended_300s=False,
        max_upside_30s_pct=None,
        max_upside_60s_pct=None,
        max_upside_120s_pct=None,
        max_upside_300s_pct=None,
    )


def _replay_take_decomposition(
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    poll_interval_sec: float,
) -> list[TakeDecompositionRow]:
    import small_paper.observer_position_tracker as ot

    from small_paper.observer_position_tracker import (
        OBSERVER_EXIT,
        OBSERVER_TAKE,
        ObserverPositionTracker,
    )

    tracker = ObserverPositionTracker(observer_tracker_config_from_pilot(pilot_config))
    cfg = tracker.cfg
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    mono = [0.0]
    active: dict[tuple[str, str], TradeRuntimePath] = {}
    take_rows: list[TakeDecompositionRow] = []

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        as_of = _parse_dt(ent_raw) if ent_raw else datetime.now(JST)
        mono[0] += max(poll_interval_sec, 0.001)
        trade = dict(ev)
        price = _as_float(ev.get("current_price"))

        with patch.object(ot.time, "monotonic", lambda: mono[0]):
            with patch.object(ot, "datetime") as mdt:
                mdt.now.return_value = as_of
                mdt.combine = datetime.combine
                mdt.fromisoformat = datetime.fromisoformat

                if ev.get("event_type") == "accepted" and price and price > 0:
                    if tracker.has_open(sym):
                        tracker._positions.pop(sym, None)
                    key = _trade_key(sym, ent_raw)
                    active[key] = TradeRuntimePath(
                        symbol=sym,
                        entry_time=ent_raw,
                        exit_time=str(ev.get("exit_time") or ""),
                        entry_price=float(price),
                        entry_quality=float(ev.get("continuation_quality_score") or 0),
                        quality_tier=str(ev.get("quality_tier") or ""),
                    )
                    tracker.register_entry(
                        trade=trade,
                        payload=trade,
                        quality_tier=str(ev.get("quality_tier") or ""),
                        entry_price=float(price),
                    )
                elif ev.get("event_type") == "candidate" and tracker.has_open(sym):
                    path = next((p for p in active.values() if p.symbol == sym), None)
                    if path and price and price > 0:
                        comps = continuation_components(trade)
                        path.ticks.append(
                            {
                                "ts": ent_raw,
                                "ts_epoch": _parse_ts(ent_raw),
                                "price": float(price),
                                "pnl_pct": _pnl_pct(path.entry_price, float(price)),
                                "quality": comps["continuation_quality"],
                                "momentum": comps["momentum_continuation"],
                                "favorable": comps["favorable_continuation"],
                            }
                        )
                    for oe in tracker.on_tick(
                        symbol=sym,
                        trade=trade,
                        payload=trade,
                        current_price=price,
                        session_bucket="",
                    ):
                        if oe.kind == OBSERVER_TAKE and path and not path.take:
                            path.take = {"take_time": ent_raw, "take_ts": _parse_ts(ent_raw)}
                            row = _decompose_take_at_signal(
                                path, ctx=oe.context, trade=trade, cfg=cfg, price=float(price)
                            )
                            take_rows.append(row)
                        elif oe.kind == OBSERVER_EXIT and path:
                            active.pop(_trade_key(sym, path.entry_time), None)

    return take_rows


def _apply_horizons(
    rows: list[TakeDecompositionRow],
    price_index: Mapping[str, list[tuple[float, float]]],
) -> None:
    from research.runtime_exit_review import _max_upside_horizons

    for row in rows:
        take_ts = _parse_ts(row.take_time)
        entry_px = 0.0
        paths_px: dict[tuple[str, str], float] = {}
        # entry price from first matching - stored in row via replay paths
        series = price_index.get(row.symbol, [])
        if not series:
            continue
        # find entry from take_time walkback - use take_pnl to infer entry if needed
        for ts, px in series:
            if abs(ts - take_ts) < 1:
                if row.take_pnl_pct and row.take_pnl_pct != 0:
                    entry_px = px / (1.0 + row.take_pnl_pct / 100.0)
                break
        if entry_px <= 0 and series:
            entry_px = series[0][1]

        horizons = _max_upside_horizons(entry_px, take_ts, series)
        row.max_upside_30s_pct = horizons.get("max_upside_30s_pct")
        row.max_upside_60s_pct = horizons.get("max_upside_60s_pct")
        row.max_upside_120s_pct = horizons.get("max_upside_120s_pct")
        row.max_upside_300s_pct = horizons.get("max_upside_300s_pct")
        tp = row.take_pnl_pct
        for h, attr in (
            (30, "extended_30s"),
            (60, "extended_60s"),
            (120, "extended_120s"),
            (300, "extended_300s"),
        ):
            up = horizons.get(f"max_upside_{h}s_pct")
            val = bool(up is not None and up > tp + EXTENDED_THRESHOLD_PCT)
            setattr(row, attr, val)


def _row_to_dict(row: TakeDecompositionRow) -> dict[str, Any]:
    return {
        "symbol": row.symbol,
        "entry_time": row.entry_time,
        "take_time": row.take_time,
        "take_reason_primary": row.take_reason_primary,
        "take_quality": row.take_quality,
        "take_pnl_pct": row.take_pnl_pct,
        "momentum_decay": row.momentum_decay,
        "favorable_fade": row.favorable_fade,
        "quality_deterioration": row.quality_deterioration,
        "display_take_target": row.display_take_target,
        "unrealized_pnl_near_take": row.unrealized_pnl_near_take,
        "vwap_deterioration_proxy": row.vwap_deterioration_proxy,
        "continuation_weakening_labeled": row.continuation_weakening_labeled,
        "momentum_at_take": row.momentum_at_take,
        "peak_momentum": row.peak_momentum,
        "momentum_ratio": row.momentum_ratio,
        "favorable_at_take": row.favorable_at_take,
        "peak_favorable": row.peak_favorable,
        "favorable_ratio": row.favorable_ratio,
        "quality_at_take": row.quality_at_take,
        "peak_quality": row.peak_quality,
        "quality_drop": row.quality_drop,
        "contributing_factors": row.contributing_factors,
        "extended_30s": row.extended_30s,
        "extended_60s": row.extended_60s,
        "extended_120s": row.extended_120s,
        "extended_300s": row.extended_300s,
        "max_upside_30s_pct": row.max_upside_30s_pct,
        "max_upside_60s_pct": row.max_upside_60s_pct,
        "max_upside_120s_pct": row.max_upside_120s_pct,
        "max_upside_300s_pct": row.max_upside_300s_pct,
    }


def _summarize_take_decomposition(rows: Sequence[TakeDecompositionRow]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"take_count": 0}

    by_primary = Counter(r.take_reason_primary for r in rows)
    cw = [r for r in rows if r.continuation_weakening_labeled]

    def _rate(attr: str) -> float:
        return round(100.0 * sum(1 for r in rows if getattr(r, attr)) / n, 2)

    factor_counts: Counter[str] = Counter()
    for r in rows:
        for f in r.contributing_factors.split("|"):
            if f:
                factor_counts[f] += 1

    cw_sub = Counter()
    for r in cw:
        parts = []
        if r.momentum_decay:
            parts.append("momentum_decay")
        if r.favorable_fade:
            parts.append("favorable_fade")
        if r.quality_deterioration:
            parts.append("quality_deterioration")
        if r.vwap_deterioration_proxy:
            parts.append("vwap_proxy")
        cw_sub["+".join(parts) if parts else "label_only"] += 1

    return {
        "take_count": n,
        "take_reason_primary_distribution": dict(by_primary),
        "contributing_factor_counts": dict(factor_counts),
        "continuation_weakening_count": len(cw),
        "continuation_weakening_sub_breakdown": dict(cw_sub),
        "avg_momentum_ratio_at_take": round(statistics.mean(r.momentum_ratio for r in cw), 4) if cw else None,
        "avg_quality_drop_at_take": round(statistics.mean(r.quality_drop for r in rows), 4),
        "upside_continuation_rate_pct": {
            "30s": _rate("extended_30s"),
            "60s": _rate("extended_60s"),
            "120s": _rate("extended_120s"),
            "300s": _rate("extended_300s"),
        },
        "take_too_early_proxy_pct": _rate("extended_300s"),
        "note": "TAKE is observer notification only; decomposition is diagnostic.",
    }


def _quality_band_for(score: float) -> str:
    for name, lo, hi in QUALITY_INFLATION_BANDS:
        if lo <= score < hi:
            return name
    return "below_0.70"


def _quality_inflation_review(
    events: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lifecycles = _build_trade_lifecycles(events)
    rows: list[dict[str, Any]] = []
    for t in lifecycles:
        q = t.continuation_quality_score
        band = _quality_band_for(q)
        rows.append(
            {
                "symbol": t.symbol,
                "entry_time": t.entry_time,
                "continuation_quality_score": q,
                "quality_band": band,
                "realized_pnl_pct": t.realized_pnl_pct,
            }
        )

    summary: dict[str, Any] = {"bands": {}, "accepted_trade_count": len(rows)}
    for name, lo, hi in QUALITY_INFLATION_BANDS:
        grp = [r for r in rows if r["quality_band"] == name]
        pnls = [float(r["realized_pnl_pct"]) for r in grp]
        pf = _profit_factor(pnls)
        summary["bands"][name] = {
            "quality_range": f"{lo:.2f}-{hi:.2f}" if hi < 1.0 else f">={lo:.2f}",
            "trade_count": len(grp),
            "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
            "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
            "max_loss_pct": round(min(pnls), 4) if pnls else None,
            "share_of_accepted_pct": round(100.0 * len(grp) / max(1, len(rows)), 2),
        }

    near_floor = [r for r in rows if 0.70 <= float(r["continuation_quality_score"]) < 0.75]
    high_band = summary["bands"].get("0.85_plus", {})
    low_band = summary["bands"].get("0.70_0.75", {})
    summary["inflation_signals"] = {
        "pct_accepted_in_0.70_0.75": round(100.0 * len(near_floor) / max(1, len(rows)), 2),
        "low_band_pf": low_band.get("profit_factor"),
        "high_band_pf": high_band.get("profit_factor"),
        "high_band_trade_share_pct": high_band.get("share_of_accepted_pct"),
    }
    return rows, summary


def _phase56_verdict(
    take_summary: Mapping[str, Any],
    quality_summary: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    scores = {VERDICT_CONTINUE: 0.0, VERDICT_NEED_QUALITY: 0.0}
    take_notes: list[str] = []

    early_pct = float((take_summary.get("upside_continuation_rate_pct") or {}).get("300s") or 0)
    if early_pct >= 55:
        take_notes.append(f"take_extended_300s_rate_{early_pct}%_observer_tune_only")

    bands = quality_summary.get("bands") or {}
    b7075 = bands.get("0.70_0.75", {})
    b7580 = bands.get("0.75_0.80", {})
    b8085 = bands.get("0.80_0.85", {})
    b85 = bands.get("0.85_plus", {})

    low_share = float(b7075.get("share_of_accepted_pct") or 0)
    low_pf = float(b7075.get("profit_factor") or 0)
    mid_pf = float(b7580.get("profit_factor") or 0) if b7580.get("profit_factor") is not None else 0.0
    high_pf = float(b8085.get("profit_factor") or 0) if b8085.get("profit_factor") is not None else 0.0
    n85 = int(b85.get("trade_count") or 0)

    if low_share >= 50:
        reasons.append(f"{low_share}%_accepted_in_0.70_0.75_band")
    if n85 < 10:
        scores[VERDICT_NEED_QUALITY] += 1.5
        reasons.append("no_accepted_trades_at_0.85_plus")
    if low_pf >= 1.15 and mid_pf > 0 and mid_pf < 1.0:
        scores[VERDICT_NEED_QUALITY] += 2.5
        reasons.append("0.75_0.80_band_PF_below_1.0_while_floor_band_strong")
    if low_pf >= 1.2 and mid_pf >= 1.1 and high_pf >= 1.2:
        scores[VERDICT_CONTINUE] += 2.0
        reasons.append("monotonic_quality_band_edge")
    if low_pf >= 1.15 and mid_pf >= 1.0:
        scores[VERDICT_CONTINUE] += 1.0
    if low_share >= 40 and low_pf < 1.0:
        scores[VERDICT_NEED_QUALITY] += 2.0
        reasons.append("floor_band_cluster_with_weak_pf")

    verdict = (
        VERDICT_NEED_QUALITY
        if scores[VERDICT_NEED_QUALITY] > scores[VERDICT_CONTINUE]
        else VERDICT_CONTINUE
    )
    return {
        "phase56_verdict": verdict,
        "rationale": reasons + take_notes,
        "scores": scores,
        "take_observer_note": (
            "TAKE early rate high — adjust observer notification only, not EXIT v13 or gate threshold."
            if early_pct >= 55
            else None
        ),
        "note": "Analysis only; no ENTRY/EXIT/cap/threshold changes.",
    }


def run_phase56_diagnosis(
    session_dir: Path,
    *,
    pilot_config: Any,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    summary = _load_json(session_dir / "small_paper_summary.json")
    events = _load_events(session_dir)
    interval = poll_interval_sec if poll_interval_sec is not None else float(
        summary.get("poll_interval_sec") or 5.0
    )

    take_decomp_rows = _replay_take_decomposition(
        events, pilot_config=pilot_config, poll_interval_sec=interval
    )
    price_index = _build_price_index(events)
    paths, _ = _replay_trade_paths(events, pilot_config=pilot_config, poll_interval_sec=interval)
    entry_px_map = {(p.symbol, p.entry_time): p.entry_price for p in paths}
    for row in take_decomp_rows:
        ep = entry_px_map.get((row.symbol, row.entry_time))
        if ep:
            take_ts = _parse_ts(row.take_time)
            from research.runtime_exit_review import _max_upside_horizons

            horizons = _max_upside_horizons(ep, take_ts, price_index.get(row.symbol, []))
            row.max_upside_30s_pct = horizons.get("max_upside_30s_pct")
            row.max_upside_60s_pct = horizons.get("max_upside_60s_pct")
            row.max_upside_120s_pct = horizons.get("max_upside_120s_pct")
            row.max_upside_300s_pct = horizons.get("max_upside_300s_pct")
            tp = row.take_pnl_pct
            row.extended_30s = bool(
                row.max_upside_30s_pct is not None and row.max_upside_30s_pct > tp + EXTENDED_THRESHOLD_PCT
            )
            row.extended_60s = bool(
                row.max_upside_60s_pct is not None and row.max_upside_60s_pct > tp + EXTENDED_THRESHOLD_PCT
            )
            row.extended_120s = bool(
                row.max_upside_120s_pct is not None and row.max_upside_120s_pct > tp + EXTENDED_THRESHOLD_PCT
            )
            row.extended_300s = bool(
                row.max_upside_300s_pct is not None and row.max_upside_300s_pct > tp + EXTENDED_THRESHOLD_PCT
            )

    take_summary = _summarize_take_decomposition(take_decomp_rows)
    quality_rows, quality_summary = _quality_inflation_review(events)
    verdict = _phase56_verdict(take_summary, quality_summary)

    return {
        "phase": 56,
        "mode": "phase56_diagnosis",
        "analysis_only": True,
        "session_dir": str(session_dir),
        "constraints": {
            "no_entry_exit_changes": True,
            "no_cap_change": True,
            "min_quality_0.70_fixed": True,
            "no_time_band_optimization": True,
        },
        "take_signal_decomposition": take_summary,
        "quality_inflation_review": quality_summary,
        "verdict": verdict,
        "_take_breakdown_rows": [_row_to_dict(r) for r in take_decomp_rows],
        "_quality_band_rows": quality_rows,
    }


def write_phase56_outputs(session_dir: Path, report: Mapping[str, Any]) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    take_public = {
        "phase": report.get("phase"),
        "mode": "take_signal_decomposition",
        "session_dir": report.get("session_dir"),
        "analysis_only": True,
        **dict(report.get("take_signal_decomposition") or {}),
        "verdict": report.get("verdict"),
    }
    p = session_dir / "take_signal_decomposition.json"
    p.write_text(json.dumps(take_public, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["take_json"] = p

    q_public = {
        "phase": report.get("phase"),
        "mode": "quality_inflation_review",
        "session_dir": report.get("session_dir"),
        "analysis_only": True,
        **dict(report.get("quality_inflation_review") or {}),
        "verdict": report.get("verdict"),
    }
    qp = session_dir / "quality_band_review.json"
    qp.write_text(json.dumps(q_public, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["quality_json"] = qp

    take_rows = report.get("_take_breakdown_rows") or []
    if take_rows:
        tp = session_dir / "take_signal_breakdown.csv"
        with tp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(take_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(take_rows)
        paths["take_csv"] = tp

    q_rows = report.get("_quality_band_rows") or []
    if q_rows:
        qc = session_dir / "quality_band_review.csv"
        with qc.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(q_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(q_rows)
        paths["quality_csv"] = qc

    return paths


def build_and_write_phase56_diagnosis(
    session_dir: Path,
    *,
    pilot_config: Any,
    poll_interval_sec: Optional[float] = None,
) -> dict[str, Any]:
    report = run_phase56_diagnosis(
        session_dir, pilot_config=pilot_config, poll_interval_sec=poll_interval_sec
    )
    paths = write_phase56_outputs(session_dir, report)
    public = {
        k: v
        for k, v in report.items()
        if not k.startswith("_")
    }
    public["output_files"] = {k: str(v) for k, v in paths.items()}
    return public
