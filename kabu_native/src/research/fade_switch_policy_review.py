"""
Phase 134: Fade-exit switch policy what-if (block / priority / cooldown vs current).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.mfe_mae_exit_review import (
    as_float,
    build_price_timeline_from_events_csv,
    discover_sessions,
    load_structural_trades,
    parse_ts,
    pnl_pct,
    session_end_ts_from_trades,
)
from research.switch_old_vs_new_review import (
    MAX_PAIR_SEC,
    PNL_EPS,
    extract_switch_pairs,
)

FADE_EXIT_REASONS = frozenset({"momentum_fade_exit", "price_momentum_fade_exit"})
COOLDOWN_MIN_TICKS = 2
COOLDOWN_REACCEL_PNL_EPS = 0.03


def _fade_pairs(session_dirs: Sequence[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sdir in session_dirs:
        # `extract_switch_pairs()` (Phase133) builds price timelines for all symbols in the session,
        # which is slow for long sessions. Phase134 only needs fade-exit switches, so we rebuild
        # pairs with a minimal symbol set.
        sdir = Path(sdir)
        trades = load_structural_trades(sdir / "structural_trades.csv")
        if not trades:
            continue

        from research.switch_old_vs_new_review import (
            HORIZONS_SEC,
            _classify_switch,
            _find_next_cross_symbol_entry,
            _old_pre_exit_flags,
            _path_after_switch,
        )

        end_ts = session_end_ts_from_trades(trades)
        session_id = (
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )

        fade_trades: list[dict[str, Any]] = []
        symbols_needed: set[str] = set()
        for old in trades:
            reason = str(old.get("close_reason") or "")
            if reason not in FADE_EXIT_REASONS:
                continue
            old_sym = str(old.get("symbol") or "")
            old_close_ts = parse_ts(str(old.get("close_time") or ""))
            if not old_sym or old_close_ts <= 0:
                continue
            new = _find_next_cross_symbol_entry(
                trades, old_symbol=old_sym, old_close_ts=old_close_ts
            )
            if not new:
                continue
            new_entry_ts = parse_ts(str(new.get("entry_time") or ""))
            if new_entry_ts <= 0 or (new_entry_ts - old_close_ts) > MAX_PAIR_SEC:
                continue
            fade_trades.append({"old": old, "new": new, "reason": reason})
            symbols_needed.add(old_sym)
            symbols_needed.add(str(new.get("symbol") or ""))

        if not fade_trades or not symbols_needed:
            continue

        tl_map = build_price_timeline_from_events_csv(
            sdir / "small_paper_events.csv", symbols_needed
        )

        for item in fade_trades:
            old = item["old"]
            new = item["new"]
            reason = str(item["reason"])

            old_sym = str(old.get("symbol") or "")
            old_close_ts = parse_ts(str(old.get("close_time") or ""))
            old_entry_ts = parse_ts(str(old.get("entry_time") or ""))
            old_entry_px = as_float(old.get("entry_price")) or 0.0
            old_close_px = as_float(old.get("close_price")) or old_entry_px
            if old_entry_px <= 0 or old_close_ts <= 0:
                continue

            new_sym = str(new.get("symbol") or "")
            new_entry_ts = parse_ts(str(new.get("entry_time") or ""))
            new_entry_px = as_float(new.get("entry_price")) or 0.0
            if new_entry_px <= 0 or new_entry_ts <= 0:
                continue

            switch_ts = max(old_close_ts, new_entry_ts)
            gap_sec = round(new_entry_ts - old_close_ts, 1)

            old_tl = tl_map.get(old_sym, [])
            new_tl = tl_map.get(new_sym, [])
            if len(old_tl) < 3 or len(new_tl) < 3:
                continue

            pre_flags = _old_pre_exit_flags(
                old,
                old_tl,
                entry_ts=old_entry_ts,
                close_ts=old_close_ts,
                entry_price=old_entry_px,
                close_price=old_close_px,
            )
            old_path = _path_after_switch(
                old_tl,
                entry_price=old_entry_px,
                switch_ts=switch_ts,
                session_end_ts=end_ts,
            )
            new_path = _path_after_switch(
                new_tl,
                entry_price=new_entry_px,
                switch_ts=switch_ts,
                session_end_ts=end_ts,
            )

            old_pnl_se = old_path.get("pnl_session_end")
            new_pnl_se = new_path.get("pnl_session_end")
            old_best_se = old_path.get("best_session_end")
            new_best_se = new_path.get("best_session_end")
            delta_se = (
                round(float(new_pnl_se) - float(old_pnl_se), 4)
                if old_pnl_se is not None and new_pnl_se is not None
                else None
            )

            switch_class = _classify_switch(old_pnl_se, new_pnl_se)
            old_reaccel = (
                old_best_se is not None
                and old_pnl_se is not None
                and float(old_best_se) > float(old_pnl_se) + PNL_EPS
                and float(old_best_se) > PNL_EPS
            )

            row: dict[str, Any] = {
                "session_id": session_id,
                "old_symbol": old_sym,
                "new_symbol": new_sym,
                "old_exit_reason": reason,
                "old_entry_time": old.get("entry_time"),
                "old_entry_price": old_entry_px,
                "old_close_time": old.get("close_time"),
                "new_entry_time": new.get("entry_time"),
                "new_entry_price": new_entry_px,
                "switch_gap_sec": gap_sec,
                "switch_time": old.get("close_time"),
                "old_pnl_at_exit": as_float(old.get("realized_pnl_pct")),
                "old_mfe_pct": as_float(old.get("mfe_pct")),
                "old_mae_pct": as_float(old.get("mae_pct")),
                "new_quality": as_float(new.get("continuation_quality_score")),
                "switch_classification": switch_class,
                "old_pnl_after_switch": old_pnl_se,
                "new_pnl_after_switch": new_pnl_se,
                "old_best_pnl": old_best_se,
                "new_best_pnl": new_best_se,
                "delta_new_minus_old": delta_se,
                "old_reaccelerated_after_exit": old_reaccel,
                **pre_flags,
            }
            for h in HORIZONS_SEC:
                row[f"old_pnl_{h}s"] = old_path.get(f"pnl_{h}s")
                row[f"new_pnl_{h}s"] = new_path.get(f"pnl_{h}s")
                row[f"old_best_{h}s"] = old_path.get(f"best_{h}s")
                row[f"new_best_{h}s"] = new_path.get(f"best_{h}s")
            rows.append(row)
    return rows


def _enrich_new_features(
    session_dir: Path,
    pair: Mapping[str, Any],
) -> dict[str, Any]:
    new_sym = str(pair.get("new_symbol") or "")
    new_ts = parse_ts(str(pair.get("new_entry_time") or ""))
    snap: Optional[dict[str, Any]] = None  # closest candidate snap for new symbol
    best_d = 1e18

    # Avoid loading all events into memory. For Phase134 we only need:
    # - new symbol candidate snapshot within +/-15s (features)
    # - candidate quality ranks around new entry (window +/-120s)
    window = 120
    snap_window = 15
    jsonl = session_dir / "small_paper_events.jsonl"
    csvp = session_dir / "small_paper_events.csv"
    candidate_rows: list[dict[str, Any]] = []

    def consider_candidate(e: Mapping[str, Any]) -> None:
        nonlocal snap, best_d
        ts = parse_ts(str(e.get("event_time") or e.get("entry_time") or ""))
        d = abs(ts - new_ts)
        if d <= window:
            candidate_rows.append(dict(e))
        if str(e.get("symbol") or "") == new_sym and d <= snap_window and d < best_d:
            best_d = d
            snap = dict(e)

    if jsonl.is_file():
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                if str(e.get("event_type") or "") != "candidate":
                    continue
                consider_candidate(e)
    elif csvp.is_file():
        with csvp.open(encoding="utf-8", newline="") as f:
            for e in csv.DictReader(f):
                if str(e.get("event_type") or "") != "candidate":
                    continue
                consider_candidate(e)

    out: dict[str, Any] = {
        "new_favorable": None,
        "new_momentum": None,
        "new_vol_liq": None,
        "new_candidate_rank": None,
        "new_entry_gap_proxy": float(pair.get("switch_gap_sec") or 0),
    }
    if snap:
        out["new_favorable"] = as_float(snap.get("favorable_continuation"))
        out["new_momentum"] = as_float(snap.get("momentum_continuation_score"))
        out["new_vol_liq"] = as_float(snap.get("daytrade_suitability_score"))
        qc_raw = snap.get("quality_components_json") or ""
        if qc_raw:
            try:
                qc = json.loads(qc_raw)
                out["new_favorable"] = out["new_favorable"] or as_float(qc.get("favorable_continuation"))
            except json.JSONDecodeError:
                pass

    cands: list[tuple[float, str, float]] = []
    for e in candidate_rows:
        ts = parse_ts(str(e.get("event_time") or ""))
        q = as_float(e.get("continuation_quality_score"))
        if q is not None:
            cands.append((ts, str(e.get("symbol") or ""), q))
    latest: dict[str, tuple[float, float]] = {}
    for ts, sym, q in cands:
        if sym not in latest or ts >= latest[sym][0]:
            latest[sym] = (ts, q)
    ranked = sorted(latest.items(), key=lambda x: x[1][1], reverse=True)
    for i, (sym, _) in enumerate(ranked, start=1):
        if sym == new_sym:
            out["new_candidate_rank"] = i
            break
    if out["new_candidate_rank"] is None and new_sym in latest:
        out["new_candidate_rank"] = len(ranked) + 1
    return out


def _old_reaccel_before_new_entry(
    pair: Mapping[str, Any],
    *,
    old_timeline: Sequence[tuple[float, float]],
) -> tuple[bool, int]:
    """Event-based: ticks on old symbol between fade close and new entry."""
    old_sym = str(pair.get("old_symbol") or "")
    old_close_ts = parse_ts(str(pair.get("old_close_time") or ""))
    new_entry_ts = parse_ts(str(pair.get("new_entry_time") or ""))
    old_entry_px = as_float(pair.get("old_entry_price")) or 0.0
    if old_entry_px <= 0:
        return False, 0

    exit_pnl = as_float(pair.get("old_pnl_at_exit")) or 0.0
    ticks = 0
    reaccel = False
    peak = exit_pnl
    for ts, px in old_timeline:
        if ts <= old_close_ts:
            continue
        if ts > new_entry_ts:
            break
        ticks += 1
        p = pnl_pct(old_entry_px, px)
        if p > peak + COOLDOWN_REACCEL_PNL_EPS:
            peak = p
            reaccel = True
    return reaccel, ticks


def _pnl_current(pair: Mapping[str, Any]) -> float:
    old_exit = float(pair.get("old_pnl_at_exit") or 0)
    new_se = float(pair.get("new_pnl_after_switch") or 0)
    return round(old_exit + new_se, 4)


def _pnl_keep_old(pair: Mapping[str, Any]) -> float:
    return round(float(pair.get("old_pnl_after_switch") or 0), 4)


def _priority_allow(pair: Mapping[str, Any], rule_id: str) -> bool:
    oq = float(pair.get("old_pnl_at_exit") or 0)
    omfe = float(pair.get("old_mfe_pct") or 0)
    o_range = bool(pair.get("old_range_hold_before_exit"))
    o_break = bool(pair.get("old_breakdown_before_exit"))
    nq = float(pair.get("new_quality") or 0)
    nm = float(pair.get("new_momentum") or 0) or 0.0
    nf = float(pair.get("new_favorable") or 0) or 0.0
    nr = int(pair.get("new_candidate_rank") or 99)
    o_reaccel = bool(pair.get("old_reaccelerated_after_exit"))

    if rule_id == "strict_quality_momentum":
        return nq >= 0.75 and nm >= 0.45 and not o_range and not o_break
    if rule_id == "quality_gap_and_rank":
        return nq >= 0.72 and nm >= 0.40 and nr <= 5 and not o_range
    if rule_id == "score_margin":
        score_new = nq * 0.45 + nm * 0.35 + nf * 0.2
        score_old = max(0.0, omfe) * 0.4 + max(0.0, oq) * 0.3 + (0.15 if o_range else 0.0)
        return score_new > score_old + 0.12
    if rule_id == "not_old_reaccel_hold":
        return nq >= 0.70 and nm >= 0.38 and not o_reaccel and oq < 0.05
    return False


def _evaluate_pair(
    pair: Mapping[str, Any],
    *,
    session_dir: Path,
    old_timeline: Sequence[tuple[float, float]],
    priority_rule: str = "quality_gap_and_rank",
) -> dict[str, Any]:
    cur = _pnl_current(pair)
    keep = _pnl_keep_old(pair)
    old_se = float(pair.get("old_pnl_after_switch") or 0)
    new_se = float(pair.get("new_pnl_after_switch") or 0)

    reaccel_before, ticks_before = _old_reaccel_before_new_entry(
        pair, old_timeline=old_timeline
    )
    cooldown_allow = reaccel_before and ticks_before >= COOLDOWN_MIN_TICKS

    priority_allow = _priority_allow(pair, priority_rule)

    scenarios = {
        "A_current": cur,
        "B_fade_switch_block": keep,
        "C_fade_switch_priority": cur if priority_allow else keep,
        "D_fade_switch_cooldown": cur if cooldown_allow else keep,
    }

    truth = str(pair.get("switch_classification") or "")
    correct = truth == "switch_correct"
    wrong = truth == "switch_wrong"

    row = {
        **pair,
        "current_pnl_proxy": cur,
        "keep_old_pnl_proxy": keep,
        "delta_keep_vs_current": round(keep - cur, 4),
        "priority_allow_switch": priority_allow,
        "cooldown_allow_switch": cooldown_allow,
        "cooldown_ticks_before_new": ticks_before,
        "old_reaccel_before_new_entry": reaccel_before,
        **{f"pnl_{k}": v for k, v in scenarios.items()},
    }
    row["both_bad_avoided"] = (
        wrong
        and cur < -PNL_EPS
        and new_se < -PNL_EPS
        and keep > cur + PNL_EPS
    )
    return row


def _scenario_aggregate(rows: Sequence[Mapping[str, Any]], scenario_key: str) -> dict[str, Any]:
    pnl_key = f"pnl_{scenario_key}"
    pnls = [float(r[pnl_key]) for r in rows]
    cur_pnls = [float(r["current_pnl_proxy"]) for r in rows]
    deltas = [float(r[pnl_key]) - float(r["current_pnl_proxy"]) for r in rows]
    truths = [str(r.get("switch_classification") or "") for r in rows]

    blocked = sum(
        1 for r in rows if float(r[pnl_key]) == float(r["keep_old_pnl_proxy"])
    )
    kept = blocked
    accepted = len(rows) - blocked

    improved_vs_current = sum(1 for d in deltas if d > PNL_EPS)
    worsened_vs_current = sum(1 for d in deltas if d < -PNL_EPS)

    correct_kept = 0
    wrong_avoided = 0
    for r, d in zip(rows, deltas):
        if str(r.get("switch_classification")) == "switch_correct" and d < -PNL_EPS:
            correct_kept += 1
        if str(r.get("switch_classification")) == "switch_wrong" and d > PNL_EPS:
            wrong_avoided += 1

    return {
        "scenario_id": scenario_key,
        "fade_switch_count": len(rows),
        "total_pnl_proxy": round(sum(pnls), 4),
        "avg_pnl_proxy": round(statistics.mean(pnls), 4) if pnls else None,
        "delta_total_vs_A_current": round(sum(deltas), 4),
        "avg_delta_vs_A": round(statistics.mean(deltas), 4) if deltas else None,
        "old_kept_count": kept,
        "new_accepted_count": accepted,
        "skipped_entry_count": kept,
        "improved_vs_A_count": improved_vs_current,
        "worsened_vs_A_count": worsened_vs_current,
        "wrong_avoided_count": wrong_avoided,
        "correct_sacrificed_count": correct_kept,
        "both_bad_avoided_count": sum(1 for r in rows if r.get("both_bad_avoided")),
        "correct_rate_vs_truth": round(
            sum(1 for r in rows if r.get("switch_classification") == "switch_correct") / len(rows), 4
        )
        if rows
        else None,
        "wrong_rate_vs_truth": round(
            sum(1 for r in rows if r.get("switch_classification") == "switch_wrong") / len(rows), 4
        )
        if rows
        else None,
    }


def _rule_search(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rules = (
        "strict_quality_momentum",
        "quality_gap_and_rank",
        "score_margin",
        "not_old_reaccel_hold",
    )
    out: list[dict[str, Any]] = []
    for rule_id in rules:
        pnls = []
        deltas = []
        allow_n = 0
        for r in rows:
            allow = _priority_allow(r, rule_id)
            if allow:
                allow_n += 1
            pnl = _pnl_current(r) if allow else _pnl_keep_old(r)
            pnls.append(pnl)
            deltas.append(pnl - _pnl_current(r))
        out.append(
            {
                "rule_id": rule_id,
                "allow_switch_count": allow_n,
                "allow_rate": round(allow_n / len(rows), 4) if rows else None,
                "total_pnl_proxy": round(sum(pnls), 4),
                "delta_total_vs_A": round(sum(deltas), 4),
                "wrong_avoided": sum(
                    1
                    for r, d in zip(rows, deltas)
                    if r.get("switch_classification") == "switch_wrong" and d > PNL_EPS
                ),
            }
        )
    out.sort(key=lambda x: float(x.get("delta_total_vs_A") or -1e9), reverse=True)
    return out


def determine_verdict(scenarios: Sequence[Mapping[str, Any]]) -> tuple[str, list[str]]:
    by_id = {s["scenario_id"]: s for s in scenarios}
    a = by_id.get("A_current") or {}
    b = by_id.get("B_fade_switch_block") or {}
    c = by_id.get("C_fade_switch_priority") or {}
    d = by_id.get("D_fade_switch_cooldown") or {}

    notes: list[str] = []
    a_total = float(a.get("total_pnl_proxy") or 0)
    b_delta = float(b.get("delta_total_vs_A_current") or 0)
    c_delta = float(c.get("delta_total_vs_A_current") or 0)
    d_delta = float(d.get("delta_total_vs_A_current") or 0)
    notes.append(
        f"A={a_total:.4f} B_delta={b_delta:.4f} C_delta={c_delta:.4f} D_delta={d_delta:.4f}"
    )

    best = max(
        [b, c, d],
        key=lambda s: float(s.get("delta_total_vs_A_current") or -1e9),
    )
    best_id = best.get("scenario_id", "")

    if float(b.get("wrong_avoided_count") or 0) >= 40 and b_delta > 5:
        if best_id == "B_fade_switch_block":
            return "fade_switch_block_promising", notes + [f"best={best_id}"]
        return "fade_switch_block_promising", notes + [f"block helps; best={best_id}"]

    if c_delta > b_delta and c_delta > 3:
        return "fade_switch_priority_promising", notes + [f"priority beats block delta={c_delta:.4f}"]

    if max(b_delta, c_delta, d_delta) <= 1:
        return "current_switch_best", notes

    if best_id == "C_fade_switch_priority":
        return "fade_switch_priority_promising", notes

    return "need_priority_features", notes + [f"ambiguous best={best_id}"]


def analyze_fade_switch_policies(
    session_dirs: Sequence[Path],
    *,
    priority_rule: str = "quality_gap_and_rank",
) -> dict[str, Any]:
    session_dirs = [Path(s) for s in session_dirs]
    pairs = _fade_pairs(session_dirs)

    session_by_id = {}
    for sdir in session_dirs:
        sid = (
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        session_by_id[sid] = sdir

    # Preload per-session data to avoid O(pairs * event_file) scans.
    pairs_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        sid = str(p.get("session_id") or "")
        if sid:
            pairs_by_session[sid].append(dict(p))

    def load_candidate_events(sdir: Path) -> list[dict[str, Any]]:
        jsonl = sdir / "small_paper_events.jsonl"
        csvp = sdir / "small_paper_events.csv"
        out: list[dict[str, Any]] = []
        if jsonl.is_file():
            with jsonl.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    e = json.loads(line)
                    if str(e.get("event_type") or "") != "candidate":
                        continue
                    out.append(e)
        elif csvp.is_file():
            with csvp.open(encoding="utf-8", newline="") as f:
                for e in csv.DictReader(f):
                    if str(e.get("event_type") or "") != "candidate":
                        continue
                    out.append(e)
        out.sort(key=lambda r: parse_ts(str(r.get("event_time") or r.get("entry_time") or "")))
        return out

    def enrich_new_features_from_candidates(
        pair: Mapping[str, Any],
        candidate_events: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        new_sym = str(pair.get("new_symbol") or "")
        new_ts = parse_ts(str(pair.get("new_entry_time") or ""))
        snap: Optional[dict[str, Any]] = None
        best_d = 1e18
        window = 120
        snap_window = 15

        cands: list[tuple[float, str, float]] = []
        for e in candidate_events:
            ts = parse_ts(str(e.get("event_time") or e.get("entry_time") or ""))
            d = abs(ts - new_ts)
            if d > window:
                continue
            sym = str(e.get("symbol") or "")
            q = as_float(e.get("continuation_quality_score"))
            if q is not None and sym:
                cands.append((ts, sym, q))
            if sym == new_sym and d <= snap_window and d < best_d:
                best_d = d
                snap = dict(e)

        out: dict[str, Any] = {
            "new_favorable": None,
            "new_momentum": None,
            "new_vol_liq": None,
            "new_candidate_rank": None,
            "new_entry_gap_proxy": float(pair.get("switch_gap_sec") or 0),
        }
        if snap:
            out["new_favorable"] = as_float(snap.get("favorable_continuation"))
            out["new_momentum"] = as_float(snap.get("momentum_continuation_score"))
            out["new_vol_liq"] = as_float(snap.get("daytrade_suitability_score"))
            qc_raw = snap.get("quality_components_json") or ""
            if qc_raw:
                try:
                    qc = json.loads(qc_raw)
                    out["new_favorable"] = out["new_favorable"] or as_float(
                        qc.get("favorable_continuation")
                    )
                except json.JSONDecodeError:
                    pass

        latest: dict[str, tuple[float, float]] = {}
        for ts, sym, q in cands:
            if sym not in latest or ts >= latest[sym][0]:
                latest[sym] = (ts, q)
        ranked = sorted(latest.items(), key=lambda x: x[1][1], reverse=True)
        for i, (sym, _) in enumerate(ranked, start=1):
            if sym == new_sym:
                out["new_candidate_rank"] = i
                break
        if out["new_candidate_rank"] is None and new_sym in latest:
            out["new_candidate_rank"] = len(ranked) + 1
        return out

    enriched: list[dict[str, Any]] = []
    for sid, ps in pairs_by_session.items():
        sdir = session_by_id.get(sid)
        if not sdir:
            continue

        candidate_events = load_candidate_events(sdir)
        old_symbols = {str(p.get("old_symbol") or "") for p in ps if str(p.get("old_symbol") or "")}
        old_tl_map = build_price_timeline_from_events_csv(
            sdir / "small_paper_events.csv", old_symbols
        )

        for p in ps:
            extra = enrich_new_features_from_candidates(p, candidate_events)
            merged = {**p, **extra}
            old_sym = str(merged.get("old_symbol") or "")
            old_tl = old_tl_map.get(old_sym, [])
            enriched.append(
                _evaluate_pair(
                    merged,
                    session_dir=sdir,
                    old_timeline=old_tl,
                    priority_rule=priority_rule,
                )
            )

    scenario_keys = (
        "A_current",
        "B_fade_switch_block",
        "C_fade_switch_priority",
        "D_fade_switch_cooldown",
    )
    scenarios = [_scenario_aggregate(enriched, k) for k in scenario_keys]
    rules = _rule_search(enriched)
    verdict, notes = determine_verdict(scenarios)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "fade_switch_count": len(enriched),
        "scenarios": scenarios,
        "rule_candidates": rules,
        "pairs": enriched,
    }
