#!/usr/bin/env python3
"""
Phase 74: Entry churn / overlap reduction what-if (read-only diagnosis).

Target: push_replay session with v1 vs v2 structural EXIT replay + ENTRY what-if.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
SESSION = (
    ROOT / "kabu_native" / "results" / "small_paper" / "20260520" / "push_replay_231314"
)
PUSH_DIR = ROOT / "kabu_native" / "data" / "push_jsonl" / "2026-05-20"

V1_MODE = "legacy"
V1_RATIO = 0.85
V2_MODE = "price"
V2_RATIO = 0.80

COOLDOWN_SECS = (0, 30, 60, 120, 180)
POST_EXIT_HORIZONS_SEC = (300, 900)  # 5m, 15m


def _load_phase71():
    path = Path(__file__).resolve().parent / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class EntryMeta:
    gate_accept_events: int = 0
    entries_opened: int = 0
    overlap_replacements: int = 0
    skipped_duplicate: int = 0
    skipped_no_overlap: int = 0
    skipped_cooldown: int = 0


def replay_with_entry_policy(
    p71: Any,
    events: Sequence[Mapping[str, Any]],
    *,
    momentum_mode: str,
    ratio: float,
    session_end: str,
    entry_mode: str = "overlap",
    cooldown_sec: float = 0.0,
) -> tuple[list[Any], EntryMeta, list[dict[str, Any]]]:
    """Replay structural trades; entry_mode: overlap | no_overlap_skip | reject_duplicate | cooldown."""
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[Any] = []
    last_exit_ts: dict[str, float] = {}
    overlap_rows: list[dict[str, Any]] = []
    meta = EntryMeta()

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        act.trade.close_time = close_time
        act.trade.close_price = close_price
        act.trade.close_reason = reason
        act.trade.realized_pnl_pct = p71._pnl_pct(act.trade.entry_price, close_price)
        act.trade.hold_duration_sec = round(max(0.0, p71._parse_ts(close_time) - act.entry_ts), 1)
        completed.append(act.trade)
        last_exit_ts[act.trade.symbol] = p71._parse_ts(close_time)

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        ent_raw = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(ent_raw)
        price = p71._as_float(ev.get("current_price"))

        if et == "accepted" and price and price > 0:
            meta.gate_accept_events += 1

            if sym in active:
                old = active[sym]
                old_pnl = p71._pnl_pct(old.trade.entry_price, float(price))
                old_hold = round(max(0.0, ts - old.entry_ts), 1)

                if entry_mode == "no_overlap_skip":
                    meta.skipped_no_overlap += 1
                    continue
                if entry_mode == "reject_duplicate":
                    meta.skipped_duplicate += 1
                    continue
                if entry_mode == "cooldown" and cooldown_sec > 0:
                    le = last_exit_ts.get(sym, 0.0)
                    if le > 0 and (ts - le) < cooldown_sec:
                        meta.skipped_cooldown += 1
                        continue
                # overlap (default): close prior
                active.pop(sym)
                close_act(old, close_time=ent_raw, close_price=float(price), reason="overlap_replaced_review")
                meta.overlap_replacements += 1
                overlap_rows.append(
                    {
                        "symbol": sym,
                        "previous_entry_time": old.trade.entry_time,
                        "next_entry_time": ent_raw,
                        "interval_sec": round(old_hold, 1),
                        "previous_exit_reason": "overlap_replaced_review",
                        "pnl_pct_at_replace": old_pnl,
                        "previous_hold_sec": old_hold,
                        "entry_quality_new": p71._as_float(ev.get("continuation_quality_score")),
                        "exit_policy_mode": momentum_mode,
                    }
                )

            if entry_mode == "cooldown" and cooldown_sec > 0 and sym not in active:
                le = last_exit_ts.get(sym, 0.0)
                if le > 0 and (ts - le) < cooldown_sec:
                    meta.skipped_cooldown += 1
                    continue

            st = sym_states.setdefault(sym, p71.SymState())
            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
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
            tr = p71.StructuralTrade(
                symbol=sym,
                entry_time=ent_raw,
                entry_price=float(price),
                entry_quality=float(ev.get("continuation_quality_score") or comps["quality"]),
            )
            active[sym] = p71.ActiveTrade(trade=tr, entry_ts=ts, rich_ticks=[rich])
            meta.entries_opened += 1

        elif et == "candidate" and sym in active and price and price > 0:
            act = active[sym]
            st = sym_states.setdefault(sym, p71.SymState())
            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
            rich = {
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
            act.rich_ticks.append(rich)
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

    for sym, act in list(active.items()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return completed, meta, overlap_rows


def _summarize_extended(
    p71: Any,
    trades: Sequence[Any],
    meta: EntryMeta,
    *,
    policy_id: str,
    exit_policy: str,
    entry_policy: str,
    cooldown_sec: float,
) -> dict[str, Any]:
    base = p71._summarize(trades)
    holds = [t.hold_duration_sec for t in trades] if trades else []
    med_hold = statistics.median(holds) if holds else None
    return {
        "policy_id": policy_id,
        "exit_policy": exit_policy,
        "entry_policy": entry_policy,
        "cooldown_sec": cooldown_sec,
        "gate_accept_events": meta.gate_accept_events,
        "accepted_count": meta.entries_opened,
        "entries_opened": meta.entries_opened,
        "overlap_count": base.get("overlap_count", 0),
        "overlap_replacements": meta.overlap_replacements,
        "skipped_duplicate": meta.skipped_duplicate,
        "skipped_no_overlap": meta.skipped_no_overlap,
        "skipped_cooldown": meta.skipped_cooldown,
        "structural_pf": base.get("structural_pf"),
        "avg_pnl": base.get("avg_pnl"),
        "win_rate": base.get("win_rate"),
        "max_loss": base.get("max_loss"),
        "trade_count": base.get("trade_count"),
        "avg_hold_sec": base.get("avg_hold_sec"),
        "median_hold_sec": round(med_hold, 1) if med_hold is not None else None,
        "momentum_fade_exit_count": base.get("momentum_fade_exit_count", 0),
        "price_momentum_fade_exit_count": base.get("price_momentum_fade_exit_count", 0),
        "quality_decay_exit_count": base.get("quality_decay_exit_count", 0),
        "favorable_fade_exit_count": base.get("favorable_fade_exit_count", 0),
        "session_end_count": base.get("session_end_count", 0),
    }


def _build_reentry_intervals(trades: Sequence[Any], p71: Any) -> list[dict[str, Any]]:
    by_sym: dict[str, list[Any]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    rows: list[dict[str, Any]] = []
    for sym, ts_list in by_sym.items():
        ts_list.sort(key=lambda x: p71._parse_ts(x.entry_time))
        for i in range(1, len(ts_list)):
            prev, nxt = ts_list[i - 1], ts_list[i]
            prev_close = p71._parse_ts(prev.close_time)
            nxt_entry = p71._parse_ts(nxt.entry_time)
            rows.append(
                {
                    "symbol": sym,
                    "previous_entry_time": prev.entry_time,
                    "previous_exit_time": prev.close_time,
                    "next_entry_time": nxt.entry_time,
                    "interval_sec": round(max(0.0, nxt_entry - prev_close), 1),
                    "previous_exit_reason": prev.close_reason,
                    "pnl_pct": prev.realized_pnl_pct,
                    "next_entry_quality": nxt.entry_quality,
                }
            )
    return rows


def _build_price_timeline(events: Sequence[Mapping[str, Any]], p71: Any) -> dict[str, list[tuple[float, float]]]:
    tl: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        if et not in ("candidate", "accepted"):
            continue
        px = p71._as_float(ev.get("current_price"))
        if not px or px <= 0:
            continue
        raw = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(raw)
        if ts > 0:
            tl[sym].append((ts, float(px)))
    for sym in tl:
        tl[sym].sort(key=lambda x: x[0])
    return tl


def _pnl_pct(entry_price: float, px: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((px - entry_price) / entry_price * 100.0, 4)


def _price_at_horizon(
    timeline: Sequence[tuple[float, float]],
    *,
    base_ts: float,
    entry_price: float,
    horizon_sec: float,
    session_end_ts: float,
) -> Optional[float]:
    target = session_end_ts if horizon_sec > 1e6 else base_ts + horizon_sec
    chosen: Optional[float] = None
    for ts, px in timeline:
        if ts >= target and ts <= session_end_ts + 1.0:
            chosen = px
            break
    if chosen is None:
        for ts, px in reversed(timeline):
            if ts <= session_end_ts + 1.0:
                chosen = px
                break
    return _pnl_pct(entry_price, chosen) if chosen is not None else None


def _classify_overlap(row: Mapping[str, Any]) -> str:
    pnl = float(row.get("pnl_pct_at_replace") or 0)
    hold = float(row.get("previous_hold_sec") or 0)
    if hold < 60 and abs(pnl) < 0.05:
        return "noise_churn"
    if pnl < -0.10:
        return "protective_cut"
    if pnl > 0.05:
        return "premature_replace"
    return "neutral"


def _classify_immediate_exit(
    trade: Any,
    *,
    exit_pnl: float,
    peak_pnl_in_hold: float,
    hold_sec: float,
) -> str:
    if hold_sec <= 30 and exit_pnl <= 0 and peak_pnl_in_hold < 0.08:
        return "bad_entry_early_exit"
    if hold_sec <= 60 and exit_pnl <= 0 and peak_pnl_in_hold >= 0.12:
        return "early_exit_cut_winner"
    if hold_sec <= 60:
        return "exit_too_fast"
    return "normal_hold"


def _load_official_session_metrics(session: Path) -> dict[str, Any]:
    path = session / "structural_observer_review.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    reasons = (data.get("exit_reason_distribution") or data.get("structural_metrics", {}).get(
        "exit_reason_distribution"
    ) or {})
    v1m = data.get("combined_structural_exit_v1_metrics") or {}
    v2m = data.get("combined_structural_exit_v2_price_mom_metrics") or v1m
    b_reasons = v1m.get("exit_reason_distribution") or {}
    v2_reasons = v2m.get("exit_reason_distribution") or reasons
    return {
        "v1_official": {
            "structural_pf": v1m.get("structural_pf"),
            "structural_avg_pnl": v1m.get("structural_avg_pnl"),
            "overlap_count": b_reasons.get("overlap_replaced_review"),
            "momentum_fade_exit_count": b_reasons.get("momentum_fade_exit"),
            "median_hold_sec": v1m.get("median_hold_duration_structural"),
        },
        "v2_official": {
            "structural_pf": v2m.get("structural_pf") or data.get("structural_pf"),
            "structural_avg_pnl": v2m.get("structural_avg_pnl") or data.get("structural_avg_pnl"),
            "overlap_count": v2_reasons.get("overlap_replaced_review") or reasons.get(
                "overlap_replaced_review"
            ),
            "price_momentum_fade_exit_count": v2_reasons.get("price_momentum_fade_exit")
            or reasons.get("price_momentum_fade_exit"),
            "median_hold_sec": v2m.get("median_hold_duration_structural")
            or data.get("median_hold_duration_structural"),
        },
    }


def _recommend(
    grid: Sequence[Mapping[str, Any]],
    *,
    v1_overlap: Mapping[str, Any],
    v2_overlap: Mapping[str, Any],
    official: Mapping[str, Any],
    overlap_enriched: Sequence[Mapping[str, Any]],
    immediate: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    off_v1 = official.get("v1_official") or {}
    off_v2 = official.get("v2_official") or {}
    pf_v1_off = float(off_v1.get("structural_pf") or v1_overlap.get("structural_pf") or 0)
    pf_v2_off = float(off_v2.get("structural_pf") or v2_overlap.get("structural_pf") or 0)
    pf_v1 = float(v1_overlap.get("structural_pf") or 0)
    pf_v2 = float(v2_overlap.get("structural_pf") or 0)

    exit_rec = "revert_to_v1_exit" if pf_v2_off < pf_v1_off - 0.02 else "keep_v2_exit"

    cooldown_rows = [r for r in grid if r.get("entry_policy") == "cooldown"]
    best_cd_v1 = max(
        [r for r in cooldown_rows if r.get("exit_policy") == "combined_structural_exit_v1"],
        key=lambda r: float(r.get("structural_pf") or 0),
        default=None,
    )
    no_ov_v1 = next((r for r in grid if r.get("policy_id") == "v1_no_overlap_skip"), None)
    rej_v1 = next((r for r in grid if r.get("policy_id") == "v1_reject_duplicate"), None)

    noise = sum(1 for o in overlap_enriched if o.get("overlap_class") == "noise_churn")
    noise_pct = noise / max(1, len(overlap_enriched))
    early_bad = sum(1 for c in immediate if c.get("exit_class") == "bad_entry_early_exit")
    imm_60 = sum(1 for c in immediate if c.get("exit_within_60s"))

    parts: list[str] = [
        f"official PF v1={pf_v1_off} v2={pf_v2_off}",
        f"replay PF v1={pf_v1} v2={pf_v2}",
    ]
    entry_rec = "inconclusive"

    if best_cd_v1 and float(best_cd_v1.get("structural_pf") or 0) >= pf_v1 + 0.05:
        entry_rec = "add_same_symbol_cooldown"
        parts.append(f"replay-only uplift at cooldown {best_cd_v1.get('cooldown_sec')}s")
    if best_cd_v1 and float(best_cd_v1.get("structural_pf") or 0) < pf_v1 - 0.2:
        parts.append("cooldown what-if degrades PF vs overlap on both v1/v2")
        if entry_rec == "add_same_symbol_cooldown":
            entry_rec = "inconclusive"

    if no_ov_v1 and float(no_ov_v1.get("structural_pf") or 0) > pf_v1 + 0.05:
        entry_rec = "reject_duplicate_entry"
        parts.append(f"v1 no_overlap_skip replay PF={no_ov_v1.get('structural_pf')}")
    elif rej_v1 and float(rej_v1.get("structural_pf") or 0) > pf_v1 + 0.05:
        entry_rec = "reject_duplicate_entry"
        parts.append(f"v1 reject_duplicate replay PF={rej_v1.get('structural_pf')}")
    elif noise_pct >= 0.5:
        entry_rec = "keep_overlap"
        parts.append(f"overlap noise_churn {noise}/{len(overlap_enriched)} ({noise_pct:.0%})")

    if imm_60 >= 50 and early_bad >= 20:
        parts.append(f"v2 price_mom immediate<=60s={imm_60} bad_entry={early_bad}")
        if exit_rec == "revert_to_v1_exit" and entry_rec in ("inconclusive", "keep_overlap"):
            entry_rec = "revert_to_v1_exit"

    if exit_rec == "revert_to_v1_exit" and entry_rec == "inconclusive":
        entry_rec = "revert_to_v1_exit"

    detail = "; ".join(parts)
    if exit_rec == "revert_to_v1_exit":
        return "revert_to_v1_exit", f"{exit_rec}; ENTRY: {entry_rec} — {detail}"
    return entry_rec, detail


def main() -> int:
    p71 = _load_phase71()
    events_path = SESSION / "small_paper_events.jsonl"
    if not events_path.is_file():
        print(f"missing events: {events_path}", file=sys.stderr)
        return 2

    events = p71._load_events(events_path)
    session_end = p71._session_end(events)
    session_end_ts = p71._parse_ts(session_end)

    # --- v1 / v2 baseline overlap ---
    v1_trades, v1_meta, v1_overlaps = replay_with_entry_policy(
        p71, events, momentum_mode=V1_MODE, ratio=V1_RATIO, session_end=session_end, entry_mode="overlap"
    )
    v2_trades, v2_meta, v2_overlaps = replay_with_entry_policy(
        p71, events, momentum_mode=V2_MODE, ratio=V2_RATIO, session_end=session_end, entry_mode="overlap"
    )
    v1_sum = _summarize_extended(
        p71, v1_trades, v1_meta,
        policy_id="v1_overlap",
        exit_policy="combined_structural_exit_v1",
        entry_policy="overlap",
        cooldown_sec=0,
    )
    v2_sum = _summarize_extended(
        p71, v2_trades, v2_meta,
        policy_id="v2_overlap",
        exit_policy="combined_structural_exit_v2_price_mom",
        entry_policy="overlap",
        cooldown_sec=0,
    )

    reentry_v2 = _build_reentry_intervals(v2_trades, p71)

    # --- overlap enrichment (v2) ---
    price_tl = _build_price_timeline(events, p71)
    overlap_cases: list[dict[str, Any]] = []
    for row in v2_overlaps:
        sym = row["symbol"]
        tl = price_tl.get(sym, [])
        rep_ts = p71._parse_ts(row["next_entry_time"])
        old_entry_px = None
        for t in v2_trades:
            if t.symbol == sym and t.entry_time == row["previous_entry_time"]:
                old_entry_px = t.entry_price
                break
        if old_entry_px and tl:
            cf_5m = _price_at_horizon(
                tl,
                base_ts=rep_ts,
                entry_price=old_entry_px,
                horizon_sec=300,
                session_end_ts=session_end_ts,
            )
            cf_15m = _price_at_horizon(
                tl,
                base_ts=rep_ts,
                entry_price=old_entry_px,
                horizon_sec=900,
                session_end_ts=session_end_ts,
            )
        else:
            cf_5m = cf_15m = None
        enriched = {
            **row,
            "overlap_class": _classify_overlap(row),
            "counterfactual_hold_5m_pnl_pct": cf_5m,
            "counterfactual_hold_15m_pnl_pct": cf_15m,
        }
        overlap_cases.append(enriched)

    # --- immediate exit (v2 price_momentum_fade) ---
    immediate_cases: list[dict[str, Any]] = []
    for t in v2_trades:
        if t.close_reason != "price_momentum_fade_exit":
            continue
        tl = price_tl.get(t.symbol, [])
        close_ts = p71._parse_ts(t.close_time)
        entry_ts = p71._parse_ts(t.entry_time)
        peak_pnl = 0.0
        for ts, px in tl:
            if entry_ts <= ts <= close_ts:
                peak_pnl = max(peak_pnl, _pnl_pct(t.entry_price, px))
        p5 = _price_at_horizon(
            tl, base_ts=close_ts, entry_price=t.entry_price, horizon_sec=300, session_end_ts=session_end_ts
        )
        p15 = _price_at_horizon(
            tl, base_ts=close_ts, entry_price=t.entry_price, horizon_sec=900, session_end_ts=session_end_ts
        )
        p_eod = _price_at_horizon(
            tl, base_ts=close_ts, entry_price=t.entry_price, horizon_sec=1e9, session_end_ts=session_end_ts
        )
        hold = t.hold_duration_sec
        immediate_cases.append(
            {
                "symbol": t.symbol,
                "entry_time": t.entry_time,
                "close_time": t.close_time,
                "close_reason": t.close_reason,
                "entry_quality": t.entry_quality,
                "hold_duration_sec": hold,
                "realized_pnl_pct": t.realized_pnl_pct,
                "peak_pnl_in_hold_pct": round(peak_pnl, 4),
                "exit_within_30s": hold <= 30,
                "exit_within_60s": hold <= 60,
                "post_exit_5m_pnl_pct": p5,
                "post_exit_15m_pnl_pct": p15,
                "post_exit_session_pnl_pct": p_eod,
                "exit_class": _classify_immediate_exit(
                    t, exit_pnl=t.realized_pnl_pct, peak_pnl_in_hold=peak_pnl, hold_sec=hold
                ),
            }
        )

    # --- policy grid ---
    grid: list[dict[str, Any]] = [v1_sum, v2_sum]
    for cd in COOLDOWN_SECS:
        if cd == 0:
            continue
        for mode, ratio, exit_id in (
            (V1_MODE, V1_RATIO, "combined_structural_exit_v1"),
            (V2_MODE, V2_RATIO, "combined_structural_exit_v2_price_mom"),
        ):
            trades, meta, _ = replay_with_entry_policy(
                p71,
                events,
                momentum_mode=mode,
                ratio=ratio,
                session_end=session_end,
                entry_mode="cooldown",
                cooldown_sec=float(cd),
            )
            tag = "v1" if mode == V1_MODE else "v2"
            grid.append(
                _summarize_extended(
                    p71,
                    trades,
                    meta,
                    policy_id=f"{tag}_cooldown_{cd}s",
                    exit_policy=exit_id,
                    entry_policy="cooldown",
                    cooldown_sec=float(cd),
                )
            )

    for mode, ratio, exit_id, tag in (
        (V1_MODE, V1_RATIO, "combined_structural_exit_v1", "v1"),
        (V2_MODE, V2_RATIO, "combined_structural_exit_v2_price_mom", "v2"),
    ):
        for entry_mode, pid_suffix in (
            ("no_overlap_skip", "no_overlap_skip"),
            ("reject_duplicate", "reject_duplicate"),
        ):
            trades, meta, _ = replay_with_entry_policy(
                p71,
                events,
                momentum_mode=mode,
                ratio=ratio,
                session_end=session_end,
                entry_mode=entry_mode,
            )
            grid.append(
                _summarize_extended(
                    p71,
                    trades,
                    meta,
                    policy_id=f"{tag}_{pid_suffix}",
                    exit_policy=exit_id,
                    entry_policy=entry_mode,
                    cooldown_sec=0,
                )
            )

    # overlap stats
    ov_classes = Counter(c.get("overlap_class") for c in overlap_cases)
    imm_30 = sum(1 for c in immediate_cases if c.get("exit_within_30s"))
    imm_60 = sum(1 for c in immediate_cases if c.get("exit_within_60s"))
    reentry_short = sum(1 for r in reentry_v2 if float(r.get("interval_sec") or 999) < 120)
    overlap_reentries = [
        r for r in reentry_v2 if r.get("previous_exit_reason") == "overlap_replaced_review"
    ]
    overlap_intervals = [float(r["interval_sec"]) for r in overlap_reentries]

    official = _load_official_session_metrics(SESSION)
    off_v1 = official.get("v1_official") or {}
    off_v2 = official.get("v2_official") or {}
    pf_v1_off = float(off_v1.get("structural_pf") or 0)
    pf_v2_off = float(off_v2.get("structural_pf") or 0)

    recommendation, rec_detail = _recommend(
        grid,
        v1_overlap=v1_sum,
        v2_overlap=v2_sum,
        official=official,
        overlap_enriched=overlap_cases,
        immediate=immediate_cases,
    )

    churn_hypothesis = (
        "short_reentry_and_immediate_exit_likely_pf_driver"
        if (
            pf_v2_off < pf_v1_off
            and (imm_60 >= 40 or len(overlap_reentries) >= 40)
            and int(off_v2.get("overlap_count") or v2_sum.get("overlap_count") or 0) >= 40
        )
        else "inconclusive_or_mixed"
    )

    review = {
        "phase": 74,
        "mode": "entry_churn_overlap_whatif",
        "session_dir": str(SESSION),
        "push_dir": str(PUSH_DIR),
        "constraints": {
            "no_production_code_change": True,
            "no_threshold_change": True,
            "no_config_change": True,
            "diagnosis_only": True,
        },
        "comparison": {
            "v1": v1_sum,
            "v2": v2_sum,
        "v2_worse_than_v1_replay": float(v2_sum.get("structural_pf") or 0)
        < float(v1_sum.get("structural_pf") or 0),
        "pf_delta_v2_minus_v1_replay": round(
            float(v2_sum.get("structural_pf") or 0) - float(v1_sum.get("structural_pf") or 0),
            4,
        ),
        "official_session_metrics": official,
        "v2_worse_than_v1_official": pf_v2_off < pf_v1_off if pf_v1_off and pf_v2_off else None,
        "pf_delta_v2_minus_v1_official": round(pf_v2_off - pf_v1_off, 4)
        if pf_v1_off and pf_v2_off
        else None,
    },
        "gate_accept_events": v2_meta.gate_accept_events,
        "reentry_interval_summary": {
            "pair_count": len(reentry_v2),
            "median_interval_sec": round(statistics.median([float(r["interval_sec"]) for r in reentry_v2]), 1)
            if reentry_v2
            else None,
            "pct_interval_under_120s": round(
                100.0 * reentry_short / max(1, len(reentry_v2)),
                1,
            ),
            "pct_previous_exit_overlap": round(
                100.0
                * sum(1 for r in reentry_v2 if r.get("previous_exit_reason") == "overlap_replaced_review")
                / max(1, len(reentry_v2)),
                1,
            ),
            "overlap_reentry_pair_count": len(overlap_reentries),
            "overlap_reentry_median_interval_sec": round(statistics.median(overlap_intervals), 1)
            if overlap_intervals
            else None,
            "overlap_reentry_pct_interval_zero": round(
                100.0 * sum(1 for x in overlap_intervals if x <= 1.0) / max(1, len(overlap_intervals)),
                1,
            ),
        },
        "overlap_analysis": {
            "overlap_count_v2": v2_sum.get("overlap_count"),
            "overlap_count_v1": v1_sum.get("overlap_count"),
            "overlap_class_counts": dict(ov_classes),
            "noise_churn_pct": round(100.0 * ov_classes.get("noise_churn", 0) / max(1, len(overlap_cases)), 1),
            "note": "overlap replaces prior virtual position at new accept price; counterfactual uses old entry vs post-replace prices",
        },
        "immediate_exit_analysis": {
            "price_momentum_fade_exit_count": v2_sum.get("price_momentum_fade_exit_count"),
            "exit_within_30s_count": imm_30,
            "exit_within_60s_count": imm_60,
            "exit_class_counts": dict(Counter(c.get("exit_class") for c in immediate_cases)),
            "median_hold_sec": round(
                statistics.median([float(c["hold_duration_sec"]) for c in immediate_cases]),
                1,
            )
            if immediate_cases
            else None,
        },
        "cooldown_grid_preview": sorted(
            grid,
            key=lambda r: (-(float(r.get("structural_pf") or 0)), str(r.get("policy_id"))),
        )[:12],
        "entry_churn_hypothesis": churn_hypothesis,
        "recommendation": recommendation,
        "recommendation_detail": rec_detail,
        "completion": {
            "v1_v2_compared": True,
            "reentry_intervals_built": True,
            "overlap_cases_built": True,
            "cooldown_grid_built": True,
            "immediate_exit_cases_built": True,
        },
    }

    out_json = SESSION / "phase74_entry_churn_overlap_review.json"
    out_grid = SESSION / "phase74_cooldown_policy_grid.csv"
    out_overlap = SESSION / "phase74_overlap_cases.csv"
    out_immediate = SESSION / "phase74_immediate_exit_cases.csv"
    out_reentry = SESSION / "phase74_reentry_intervals.csv"

    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    grid_fields = [
        "policy_id",
        "exit_policy",
        "entry_policy",
        "cooldown_sec",
        "gate_accept_events",
        "accepted_count",
        "structural_pf",
        "avg_pnl",
        "win_rate",
        "max_loss",
        "trade_count",
        "overlap_count",
        "price_momentum_fade_exit_count",
        "momentum_fade_exit_count",
        "avg_hold_sec",
        "median_hold_sec",
        "skipped_cooldown",
        "skipped_duplicate",
        "skipped_no_overlap",
    ]
    with out_grid.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=grid_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(grid)

    if overlap_cases:
        with out_overlap.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(overlap_cases[0].keys()))
            w.writeheader()
            w.writerows(overlap_cases)
    if immediate_cases:
        with out_immediate.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(immediate_cases[0].keys()))
            w.writeheader()
            w.writerows(immediate_cases)
    if reentry_v2:
        with out_reentry.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(reentry_v2[0].keys()))
            w.writeheader()
            w.writerows(reentry_v2)

    print("recommendation:", recommendation)
    print("v1 PF", v1_sum.get("structural_pf"), "v2 PF", v2_sum.get("structural_pf"))
    print("overlap v1/v2", v1_sum.get("overlap_count"), v2_sum.get("overlap_count"))
    print("immediate 30s/60s", imm_30, imm_60)
    print("wrote", out_json.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
