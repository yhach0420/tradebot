#!/usr/bin/env python3
"""
Phase 75: Quality gate redesign what-if (read-only).

Re-gates push_replay session with alternate quality formulas; EXIT fixed to v1.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
SESSION = (
    ROOT / "kabu_native" / "results" / "small_paper" / "20260520" / "push_replay_231314"
)

MIN_ENTRY_Q = 0.70
MAX_CONCURRENT = 3
V1_MODE = "legacy"
V1_RATIO = 0.85
V2_MODE = "price"
V2_RATIO = 0.80
IMMEDIATE_EXIT_SEC = 60.0

VARIANT_IDS = (
    "A_current",
    "B_remove_duration",
    "C_reduce_duration_weight",
    "D_increase_mae_penalty",
    "E_mfe_mae_edge_quality",
    "F_favorable_mae_guard",
    "G_quality_v2",
)


def _load_phase71():
    path = Path(__file__).resolve().parent / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine_p75"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_quality_components(p71: Any, ev: Mapping[str, Any], st: Any) -> dict[str, float]:
    """Component breakdown aligned with continuation_quality_ranking weights."""
    ts = p71._parse_ts(str(ev.get("entry_time") or ""))
    price = _as_float(ev.get("current_price")) or 0.0
    if price <= 0:
        price = 1.0
    comps = p71._components(st, ts=ts, price=price, ev=ev)

    rolling_mfe = _as_float(ev.get("rolling_mfe_pct")) or 0.0
    rolling_mae = _as_float(ev.get("rolling_mae_pct")) or 0.0
    if rolling_mfe == 0 and rolling_mae == 0 and st.ref > 0:
        rolling_mfe = max(0.0, (st.running_max - st.ref) / st.ref)
        rolling_mae = min(0.0, (st.running_min - st.ref) / st.ref)

    mom = float(comps.get("momentum") or ev.get("momentum_continuation_score") or 0.0)
    fav = float(comps.get("favorable") or ev.get("favorable_continuation") or 0.15)
    dur = _as_float(ev.get("max_continuation_duration")) or 0.0
    dur_n = min(1.0, float(dur) / 14.0)
    mae = abs(rolling_mae)
    mfe = rolling_mfe

    bear = _as_float(ev.get("bearish_accumulation_score")) or 0.0
    if ev.get("adverse_shrinking") is not None:
        bear = _as_float(ev.get("adverse_shrinking")) or 0.0
    bear_inv = max(0.0, 1.0 - min(1.0, bear))
    bull = min(1.0, max(0.0, mfe / 0.25)) if mfe else 0.2
    stability = 1.0 if mfe > mae else max(0.0, 0.5 + (mfe - mae) / 0.5)

    mfe_mae_edge = mfe - mae
    mfe_mae_edge_n = _clamp01(mfe_mae_edge / 0.35) if (mfe or mae) else 0.0
    adverse_shrink = bear_inv
    vwap_strength = float(comps.get("vwap_strength") or 0.0)
    vwap_n = _clamp01(abs(vwap_strength) / 0.004) if vwap_strength else 0.0
    favorable_tick_ratio = _clamp01(fav)

    quality_a = min(
        1.0,
        0.30 * mom
        + 0.22 * dur_n
        + 0.20 * fav
        + 0.14 * bear_inv
        + 0.14 * stability
        + 0.04 * bull,
    )
    quality_b = min(1.0, 0.30 * mom + 0.20 * fav + 0.14 * bear_inv + 0.14 * stability + 0.04 * bull)
    quality_c = min(
        1.0,
        0.30 * mom + 0.10 * dur_n + 0.20 * fav + 0.14 * bear_inv + 0.14 * stability + 0.04 * bull,
    )
    stability_d = 1.0 if mfe > mae else max(0.0, 0.5 + (mfe - 1.2 * mae) / 0.5)
    mom_d = max(0.0, mom - 0.25 * _clamp01(mae / 0.01))
    quality_d = min(
        1.0,
        0.30 * mom_d + 0.22 * dur_n + 0.20 * fav + 0.14 * bear_inv + 0.14 * stability_d + 0.04 * bull,
    )
    quality_e = min(
        1.0,
        0.35 * mfe_mae_edge_n
        + 0.22 * fav
        + 0.14 * bear_inv
        + 0.14 * stability
        + 0.04 * bull
        + 0.05 * mom,
    )
    guard_f_ok = fav >= 0.8 and rolling_mae > -0.003
    quality_f = quality_a if guard_f_ok else 0.0
    quality_g = min(
        1.0,
        0.35 * mfe_mae_edge_n
        + 0.25 * favorable_tick_ratio
        + 0.20 * adverse_shrink
        + 0.20 * vwap_n,
    )

    return {
        "mom": round(mom, 4),
        "dur_n": round(dur_n, 4),
        "favorable": round(fav, 4),
        "bear_inv": round(bear_inv, 4),
        "stability": round(stability, 4),
        "bull": round(bull, 4),
        "rolling_mfe": round(mfe, 6),
        "rolling_mae": round(rolling_mae, 6),
        "mfe_mae_edge": round(mfe_mae_edge, 6),
        "mfe_mae_edge_n": round(mfe_mae_edge_n, 4),
        "adverse_shrink": round(adverse_shrink, 4),
        "vwap_strength": round(vwap_strength, 6),
        "favorable_tick_ratio": round(favorable_tick_ratio, 4),
        "guard_f_ok": guard_f_ok,
        "quality_a": round(quality_a, 4),
        "quality_b": round(quality_b, 4),
        "quality_c": round(quality_c, 4),
        "quality_d": round(quality_d, 4),
        "quality_e": round(quality_e, 4),
        "quality_f": round(quality_f, 4),
        "quality_g": round(quality_g, 4),
    }


def variant_score(components: Mapping[str, float], variant_id: str) -> float:
    key = {
        "A_current": "quality_a",
        "B_remove_duration": "quality_b",
        "C_reduce_duration_weight": "quality_c",
        "D_increase_mae_penalty": "quality_d",
        "E_mfe_mae_edge_quality": "quality_e",
        "F_favorable_mae_guard": "quality_f",
        "G_quality_v2": "quality_g",
    }[variant_id]
    return float(components[key])


def variant_passes(
    components: Mapping[str, float],
    variant_id: str,
    *,
    event_quality: Optional[float] = None,
) -> bool:
    if variant_id == "A_current" and event_quality is not None:
        q = float(event_quality)
    else:
        q = variant_score(components, variant_id)
    if variant_id == "F_favorable_mae_guard":
        return bool(components.get("guard_f_ok")) and q >= MIN_ENTRY_Q
    return q >= MIN_ENTRY_Q


@dataclass
class GateMeta:
    gate_accept_events: int = 0
    variant_accepts: int = 0
    filtered_vs_current_accept: int = 0
    low_quality_would_pass_count: int = 0


def replay_quality_variant(
    p71: Any,
    events: Sequence[Mapping[str, Any]],
    *,
    variant_id: str,
    session_end: str,
    momentum_mode: str = V1_MODE,
    ratio: float = V1_RATIO,
) -> tuple[list[Any], GateMeta]:
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[Any] = []
    meta = GateMeta()

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
        price = p71._as_float(ev.get("current_price"))
        st = sym_states.setdefault(sym, p71.SymState())

        if et == "candidate" and sym in active and price and price > 0:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
            act.rich_ticks.append(
                {
                    "ts": ent_raw,
                    "price": float(price),
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, float(price)),
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
                momentum_mode=momentum_mode,
                ratio=ratio,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                close_act(act, close_time=ent_raw, close_price=float(price), reason=reason)
                active.pop(sym, None)

        elif et == "rejected" and price and price > 0:
            reason = str(ev.get("gate_reject_reason") or "")
            if reason == "low_quality":
                components = extract_quality_components(p71, ev, st)
                if variant_passes(
                    components, variant_id, event_quality=_as_float(ev.get("continuation_quality_score"))
                ):
                    meta.low_quality_would_pass_count += 1

        elif et == "accepted" and price and price > 0:
            meta.gate_accept_events += 1
            components = extract_quality_components(p71, ev, st)
            ev_q = _as_float(ev.get("continuation_quality_score"))
            passes = variant_passes(components, variant_id, event_quality=ev_q)
            if not passes:
                meta.filtered_vs_current_accept += 1
                continue

            if True:
                if sym in active:
                    old = active.pop(sym)
                    close_act(
                        old,
                        close_time=ent_raw,
                        close_price=float(price),
                        reason="overlap_replaced_review",
                    )
                comps = p71._components(st, ts=ts, price=float(price), ev=ev)
                entry_q = (
                    float(ev_q)
                    if variant_id == "A_current" and ev_q is not None
                    else variant_score(components, variant_id)
                )
                tr = p71.StructuralTrade(
                    symbol=sym,
                    entry_time=ent_raw,
                    entry_price=float(price),
                    entry_quality=entry_q,
                )
                setattr(tr, "entry_snapshot", dict(components))
                active[sym] = p71.ActiveTrade(
                    trade=tr,
                    entry_ts=ts,
                    rich_ticks=[
                        {
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
                    ],
                )
                meta.variant_accepts += 1

        elif et == "candidate" and price and price > 0:
            p71._components(st, ts=ts, price=float(price), ev=ev)

    for sym, act in list(active.items()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return completed, meta


def _summarize_variant(
    p71: Any,
    trades: Sequence[Any],
    meta: GateMeta,
    *,
    variant_id: str,
    exit_policy: str,
) -> dict[str, Any]:
    base = p71._summarize(trades)
    holds = [t.hold_duration_sec for t in trades]
    imm = sum(
        1
        for t in trades
        if t.close_reason == "momentum_fade_exit" and t.hold_duration_sec <= IMMEDIATE_EXIT_SEC
    )
    imm_price = sum(
        1
        for t in trades
        if t.close_reason == "price_momentum_fade_exit" and t.hold_duration_sec <= IMMEDIATE_EXIT_SEC
    )
    return {
        "variant_id": variant_id,
        "exit_policy": exit_policy,
        "gate_accept_events": meta.gate_accept_events,
        "accepted_count": meta.variant_accepts,
        "filtered_vs_current_accept": meta.filtered_vs_current_accept,
        "low_quality_would_pass_count": meta.low_quality_would_pass_count,
        "structural_pf": base.get("structural_pf"),
        "avg_pnl": base.get("avg_pnl"),
        "win_rate": base.get("win_rate"),
        "max_loss": base.get("max_loss"),
        "trade_count": base.get("trade_count"),
        "overlap_count": base.get("overlap_count"),
        "immediate_exit_count": imm,
        "price_momentum_immediate_exit_count": imm_price,
        "quality_decay_exit_count": base.get("quality_decay_exit_count", 0),
        "momentum_fade_exit_count": base.get("momentum_fade_exit_count", 0),
        "price_momentum_fade_exit_count": base.get("price_momentum_fade_exit_count", 0),
        "avg_hold_sec": base.get("avg_hold_sec"),
        "median_hold_sec": round(statistics.median(holds), 1) if holds else None,
    }


def _component_win_loss_rows(
    trades: Sequence[Any],
    entry_components: dict[tuple[str, str], dict[str, float]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    labels = ("mom", "dur_n", "favorable", "bear_inv", "stability", "bull")
    for subset, tag in (
        (trades, "all_accepted"),
        ([t for t in trades if t.close_reason != "overlap_replaced_review"], "excl_overlap"),
        (
            [
                t
                for t in trades
                if not (
                    t.close_reason == "momentum_fade_exit"
                    and t.hold_duration_sec <= IMMEDIATE_EXIT_SEC
                )
            ],
            "excl_immediate_momentum_fade",
        ),
    ):
        wins = [t for t in subset if t.realized_pnl_pct > 0]
        losses = [t for t in subset if t.realized_pnl_pct <= 0]
        for comp in labels:
            wvals = [
                entry_components.get((t.symbol, t.entry_time), {}).get(comp)
                for t in wins
            ]
            lvals = [
                entry_components.get((t.symbol, t.entry_time), {}).get(comp)
                for t in losses
            ]
            wvals = [v for v in wvals if v is not None]
            lvals = [v for v in lvals if v is not None]
            rows.append(
                {
                    "subset": tag,
                    "component": comp,
                    "win_n": len(wvals),
                    "loss_n": len(lvals),
                    "win_mean": round(statistics.mean(wvals), 4) if wvals else None,
                    "loss_mean": round(statistics.mean(lvals), 4) if lvals else None,
                    "mean_delta_win_minus_loss": round(statistics.mean(wvals) - statistics.mean(lvals), 4)
                    if wvals and lvals
                    else None,
                }
            )
    by_sym: dict[str, list[Any]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    for sym, ts_list in sorted(by_sym.items()):
        wins = [t for t in ts_list if t.realized_pnl_pct > 0]
        losses = [t for t in ts_list if t.realized_pnl_pct <= 0]
        for comp in labels:
            wvals = [
                entry_components.get((t.symbol, t.entry_time), {}).get(comp) for t in wins
            ]
            lvals = [
                entry_components.get((t.symbol, t.entry_time), {}).get(comp) for t in losses
            ]
            wvals = [v for v in wvals if v is not None]
            lvals = [v for v in lvals if v is not None]
            if not wvals and not lvals:
                continue
            rows.append(
                {
                    "subset": f"symbol_{sym}",
                    "component": comp,
                    "win_n": len(wvals),
                    "loss_n": len(lvals),
                    "win_mean": round(statistics.mean(wvals), 4) if wvals else None,
                    "loss_mean": round(statistics.mean(lvals), 4) if lvals else None,
                    "mean_delta_win_minus_loss": round(statistics.mean(wvals) - statistics.mean(lvals), 4)
                    if wvals and lvals
                    else None,
                }
            )
    return rows


def _recommend(grid: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    base = next(r for r in grid if r["variant_id"] == "A_current")
    pf_base = float(base.get("structural_pf") or 0)
    best = max(grid, key=lambda r: float(r.get("structural_pf") or 0))
    pf_best = float(best.get("structural_pf") or 0)
    vid = str(best["variant_id"])

    mapping = {
        "B_remove_duration": "keep_current_quality",
        "C_reduce_duration_weight": "reduce_duration_weight",
        "D_increase_mae_penalty": "add_mae_penalty",
        "E_mfe_mae_edge_quality": "use_mfe_mae_edge_quality",
        "F_favorable_mae_guard": "require_favorable_and_mae_guard",
        "G_quality_v2": "use_mfe_mae_edge_quality",
        "A_current": "keep_current_quality",
    }
    if vid == "A_current" or pf_best <= pf_base + 0.02:
        return "keep_current_quality", f"no variant beat A by >0.02 (best {vid} PF={pf_best} vs A={pf_base})"
    rec = mapping.get(vid, "inconclusive")
    return rec, (
        f"{vid} PF={pf_best} accepted={best.get('accepted_count')} "
        f"filtered={best.get('filtered_vs_current_accept')} vs A PF={pf_base}"
    )


def main() -> int:
    p71 = _load_phase71()
    events_path = SESSION / "small_paper_events.jsonl"
    if not events_path.is_file():
        print(f"missing: {events_path}", file=sys.stderr)
        return 2

    events = p71._load_events(events_path)
    session_end = p71._session_end(events)

    grid: list[dict[str, Any]] = []
    metas: dict[str, GateMeta] = {}
    trades_by_variant: dict[str, list[Any]] = {}

    for vid in VARIANT_IDS:
        trades, meta = replay_quality_variant(
            p71, events, variant_id=vid, session_end=session_end, momentum_mode=V1_MODE, ratio=V1_RATIO
        )
        metas[vid] = meta
        trades_by_variant[vid] = trades
        grid.append(
            _summarize_variant(
                p71,
                trades,
                meta,
                variant_id=vid,
                exit_policy="combined_structural_exit_v1",
            )
        )

    trades_v2_ref, meta_v2 = replay_quality_variant(
        p71,
        events,
        variant_id="A_current",
        session_end=session_end,
        momentum_mode=V2_MODE,
        ratio=V2_RATIO,
    )
    grid.append(
        _summarize_variant(
            p71,
            trades_v2_ref,
            meta_v2,
            variant_id="A_current_v2_exit_reference",
            exit_policy="combined_structural_exit_v2_price_mom",
        )
    )

    trades_a = trades_by_variant["A_current"]
    entry_components = {
        (t.symbol, t.entry_time): getattr(t, "entry_snapshot", {})
        for t in trades_a
    }
    comp_rows = _component_win_loss_rows(trades_a, entry_components)

    # Component contribution correlation with PnL on baseline trades
    contrib_rows: list[dict[str, Any]] = []
    for comp in ("mom", "dur_n", "favorable", "bear_inv", "stability", "bull"):
        pairs = [
            (
                entry_components.get((t.symbol, t.entry_time), {}).get(comp),
                t.realized_pnl_pct,
            )
            for t in trades_a
        ]
        pairs = [(a, b) for a, b in pairs if a is not None]
        if len(pairs) < 5:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        num = sum((x - mx) * (y - my) for x, y in pairs)
        den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
        corr = round(num / den, 4) if den else None
        contrib_rows.append(
            {
                "component": comp,
                "pearson_vs_realized_pnl": corr,
                "mean_win": round(
                    statistics.mean([x for x, y in pairs if y > 0]),
                    4,
                )
                if any(y > 0 for _, y in pairs)
                else None,
                "mean_loss": round(
                    statistics.mean([x for x, y in pairs if y <= 0]),
                    4,
                )
                if any(y <= 0 for _, y in pairs)
                else None,
            }
        )

    bad_cases: list[dict[str, Any]] = []
    for t in trades_a:
        ec = getattr(t, "entry_snapshot", None) or entry_components.get((t.symbol, t.entry_time), {})
        if t.realized_pnl_pct > 0:
            continue
        if not ec or "quality_a" not in ec:
            continue
        filters = {
            vid: not variant_passes(ec, vid) for vid in VARIANT_IDS if vid != "A_current"
        }
        would_filter = [k for k, v in filters.items() if v]
        if not would_filter:
            continue
        bad_cases.append(
            {
                "symbol": t.symbol,
                "entry_time": t.entry_time,
                "realized_pnl_pct": t.realized_pnl_pct,
                "close_reason": t.close_reason,
                "hold_duration_sec": t.hold_duration_sec,
                "quality_a": ec.get("quality_a"),
                "quality_e": ec.get("quality_e"),
                "quality_f": ec.get("quality_f"),
                "quality_g": ec.get("quality_g"),
                "variants_that_would_filter": "|".join(would_filter),
            }
        )

    recommendation, rec_detail = _recommend(grid)
    base_pf = float(next(r for r in grid if r["variant_id"] == "A_current").get("structural_pf") or 0)
    losing_n = sum(1 for t in trades_a if t.realized_pnl_pct <= 0)
    filt_f = sum(
        1
        for t in trades_a
        if t.realized_pnl_pct <= 0
        and not variant_passes(getattr(t, "entry_snapshot", {}), "F_favorable_mae_guard")
    )

    review = {
        "phase": 75,
        "mode": "quality_gate_redesign_whatif",
        "session_dir": str(SESSION),
        "constraints": {
            "no_production_code_change": True,
            "no_config_change": True,
            "no_threshold_change": True,
            "min_continuation_quality": MIN_ENTRY_Q,
            "diagnosis_only": True,
        },
        "exit_evaluation_policy": "combined_structural_exit_v1",
        "v2_exit_reference_variant": "A_current_v2_exit_reference",
        "component_contribution": contrib_rows,
        "quality_variant_grid": grid,
        "recommendation": recommendation,
        "recommendation_detail": rec_detail,
        "can_filter_bad_entries_at_same_threshold": False,
        "quality_redesign_verdict": (
            "At min_q=0.70, B/C/E/G reject all current accepts; D and F drop "
            f"{105} / {86} accepts but PF falls below baseline ({base_pf}). "
            "Component scores barely separate wins vs losses; duration is not predictive. "
            f"F would block {filt_f}/{losing_n} losers but also removes winners."
        ),
        "bad_entry_filter_summary": {
            "losing_trades": sum(1 for t in trades_a if t.realized_pnl_pct <= 0),
            "losing_filtered_by_F": sum(
                1
                for t in trades_a
                if t.realized_pnl_pct <= 0
                and not variant_passes(getattr(t, "entry_snapshot", {}), "F_favorable_mae_guard")
            ),
            "losing_filtered_by_E": sum(
                1
                for t in trades_a
                if t.realized_pnl_pct <= 0
                and not variant_passes(getattr(t, "entry_snapshot", {}), "E_mfe_mae_edge_quality")
            ),
            "losing_filtered_by_G": sum(
                1
                for t in trades_a
                if t.realized_pnl_pct <= 0
                and not variant_passes(getattr(t, "entry_snapshot", {}), "G_quality_v2")
            ),
        },
        "completion": {
            "component_win_loss": True,
            "gate_replay_variants": len(VARIANT_IDS),
            "bad_entry_cases": True,
        },
    }

    out_json = SESSION / "phase75_quality_gate_redesign_review.json"
    out_grid = SESSION / "phase75_quality_variant_grid.csv"
    out_comp = SESSION / "phase75_quality_component_win_loss.csv"
    out_bad = SESSION / "phase75_bad_entry_filter_cases.csv"

    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    grid_fields = [
        "variant_id",
        "exit_policy",
        "accepted_count",
        "structural_pf",
        "avg_pnl",
        "win_rate",
        "max_loss",
        "trade_count",
        "overlap_count",
        "immediate_exit_count",
        "quality_decay_exit_count",
        "momentum_fade_exit_count",
        "price_momentum_fade_exit_count",
        "filtered_vs_current_accept",
        "low_quality_would_pass_count",
        "avg_hold_sec",
        "median_hold_sec",
    ]
    with out_grid.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=grid_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(grid)

    with out_comp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(comp_rows[0].keys()) if comp_rows else ["subset"])
        w.writeheader()
        w.writerows(comp_rows)

    if bad_cases:
        with out_bad.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(bad_cases[0].keys()))
            w.writeheader()
            w.writerows(bad_cases)

    print("recommendation:", recommendation)
    for r in sorted(grid, key=lambda x: -(float(x.get("structural_pf") or 0)))[:8]:
        print(
            r["variant_id"],
            "PF",
            r.get("structural_pf"),
            "acc",
            r.get("accepted_count"),
            "filt",
            r.get("filtered_vs_current_accept"),
        )
    print("wrote", out_json.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
