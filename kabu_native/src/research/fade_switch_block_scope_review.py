"""
Phase 142: Classify Phase141 over-broad fade switch blocks; scoped block what-if (review only).
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_switch_policy_review import (
    _fade_pairs,
    _pnl_current,
    _pnl_keep_old,
)
from research.hybrid_fade_switch_policy_review import (
    enrich_new_features_from_candidates,
    load_candidate_events,
)
from research.mfe_mae_exit_review import as_float, load_structural_trades, parse_ts
from research.replay_fidelity_review import _norm_session_id
from research.switch_old_vs_new_review import MAX_PAIR_SEC, PNL_EPS

# Phase141 structural replay baseline (avoid re-running replay in Phase142).
PHASE141_A_TOTAL_PNL = 6.3568
PHASE141_A_TRADE_COUNT = 945

PHASE139_BLOCK_DELTA = 72.7341
IMMEDIATE_SWITCH_SEC = MAX_PAIR_SEC
NEW_STRONG_QUALITY = 0.72
NEW_STRONG_MOMENTUM = 0.40


def _load_blocked_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("event_kind") or "") != "fade_switch_blocked":
                continue
            rows.append(dict(r))
    return rows


def _dedupe_blocked(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = (
            str(r.get("session_id") or ""),
            str(r.get("blocked_new_symbol") or r.get("symbol") or ""),
            str(r.get("entry_time") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(r))
    return out


def _pair_key(p: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        _norm_session_id(str(p.get("session_id") or "")),
        str(p.get("old_symbol") or ""),
        str(p.get("new_symbol") or ""),
        str(p.get("new_entry_time") or ""),
    )


def _index_pairs(pairs: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    return {_pair_key(p): dict(p) for p in pairs}


def _match_pair(
    block: Mapping[str, Any],
    pairs_by_session: dict[str, list[dict[str, Any]]],
    *,
    time_tol_sec: float = 30.0,
) -> Optional[dict[str, Any]]:
    sid = _norm_session_id(str(block.get("session_id") or ""))
    new_sym = str(block.get("blocked_new_symbol") or "")
    ent_ts = parse_ts(str(block.get("entry_time") or ""))
    old_sym = str(block.get("old_symbol") or "")
    for p in pairs_by_session.get(sid, []):
        if str(p.get("new_symbol") or "") != new_sym:
            continue
        if str(p.get("old_symbol") or "") != old_sym:
            continue
        pts = parse_ts(str(p.get("new_entry_time") or ""))
        if abs(pts - ent_ts) <= time_tol_sec:
            return p
    return None


def _load_trades_a_from_sessions(
    session_dirs: Sequence[Path],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Map (session, symbol, entry_time) -> trade row from live structural_trades.csv."""
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for sdir in session_dirs:
        sdir = Path(sdir)
        sid = _norm_session_id(
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        for row in load_structural_trades(sdir / "structural_trades.csv"):
            key = (sid, str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
            out[key] = row
    return out


class _TradeShim:
    """Minimal trade-like object for pnl lookup."""

    def __init__(self, row: Mapping[str, Any]) -> None:
        self.realized_pnl_pct = float(row.get("realized_pnl_pct") or 0)
        self.symbol = str(row.get("symbol") or "")
        self.entry_time = str(row.get("entry_time") or "")


def _load_trades_a(
    session_dirs: Sequence[Path],
    pilot_config: Any,
) -> dict[tuple[str, str, str], _TradeShim]:
    del pilot_config
    rows = _load_trades_a_from_sessions(session_dirs)
    return {k: _TradeShim(v) for k, v in rows.items()}


def _enrich_block_row(
    block: dict[str, Any],
    *,
    pair: Optional[Mapping[str, Any]],
    trade: Optional[_TradeShim],
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    fade_ts = parse_ts(str(block.get("fade_exit_time") or ""))
    ent_ts = parse_ts(str(block.get("entry_time") or ""))
    gap = round(ent_ts - fade_ts, 1) if fade_ts and ent_ts else None
    would_pnl = float(trade.realized_pnl_pct) if trade else None
    truth = str(pair.get("switch_classification") or "") if pair else ""
    row: dict[str, Any] = {
        **block,
        "blocked_new_symbol": block.get("blocked_new_symbol") or block.get("symbol"),
        "time_since_old_exit_sec": gap,
        "in_phase139_pair": pair is not None,
        "switch_classification": truth,
        "switch_gap_sec": pair.get("switch_gap_sec") if pair else None,
        "is_immediate_fade_switch": gap is not None and gap <= IMMEDIATE_SWITCH_SEC,
        "is_delayed_unrelated": gap is not None and gap > IMMEDIATE_SWITCH_SEC,
        "same_symbol_entry": str(block.get("blocked_new_symbol") or "")
        == str(block.get("old_symbol") or ""),
        "old_breakdown_before_exit": pair.get("old_breakdown_before_exit") if pair else None,
        "old_reaccelerated_after_exit": pair.get("old_reaccelerated_after_exit") if pair else None,
        "new_quality": pair.get("new_quality") if pair else (extra or {}).get("new_quality"),
        "new_momentum": pair.get("new_momentum") if pair else (extra or {}).get("new_momentum"),
        "new_favorable": pair.get("new_favorable") if pair else (extra or {}).get("new_favorable"),
        "would_have_pnl_proxy": would_pnl,
        "pair_current_pnl_proxy": _pnl_current(pair) if pair else None,
        "pair_keep_old_pnl_proxy": _pnl_keep_old(pair) if pair else None,
    }
    if would_pnl is not None:
        row["blocked_entry_was_good"] = would_pnl > PNL_EPS or truth == "switch_correct"
        row["blocked_entry_was_bad"] = would_pnl < -PNL_EPS or truth == "switch_wrong"
    else:
        row["blocked_entry_was_good"] = truth == "switch_correct"
        row["blocked_entry_was_bad"] = truth == "switch_wrong"
    return row


def _first_cross_keys(blocks: Sequence[Mapping[str, Any]]) -> set[tuple[str, str, str]]:
    """First cross-symbol block per (session, fade_exit_time, old_symbol)."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for b in blocks:
        sid = _norm_session_id(str(b.get("session_id") or ""))
        old = str(b.get("old_symbol") or "")
        fade_t = str(b.get("fade_exit_time") or "")
        new = str(b.get("blocked_new_symbol") or "")
        if not sid or not old or not fade_t or new == old:
            continue
        groups[(sid, fade_t, old)].append(dict(b))
    keys: set[tuple[str, str, str]] = set()
    for _, items in groups.items():
        items.sort(key=lambda x: parse_ts(str(x.get("entry_time") or "")))
        b0 = items[0]
        keys.add(
            (
                _norm_session_id(str(b0.get("session_id") or "")),
                str(b0.get("blocked_new_symbol") or ""),
                str(b0.get("entry_time") or ""),
            )
        )
    return keys


def _block_row_key(b: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _norm_session_id(str(b.get("session_id") or "")),
        str(b.get("blocked_new_symbol") or ""),
        str(b.get("entry_time") or ""),
    )


def _scenario_should_block_row(
    scenario_id: str,
    row: Mapping[str, Any],
    *,
    first_cross_keys: set[tuple[str, str, str]],
) -> bool:
    if scenario_id in ("A_current",):
        return False
    if scenario_id == "B_phase141_full_block":
        return True
    if scenario_id == "C_first_cross_symbol_only":
        return _block_row_key(row) in first_cross_keys
    if scenario_id == "D_old_not_breakdown_only":
        ob = row.get("old_breakdown_before_exit")
        if ob is None:
            return False
        return str(ob).lower() not in ("true", "1", "yes")
    if scenario_id == "E_new_not_strong_only":
        nq = as_float(row.get("new_quality"))
        nm = as_float(row.get("new_momentum"))
        if nq is None and nm is None:
            return False
        weak_q = nq is not None and nq < NEW_STRONG_QUALITY
        weak_m = nm is not None and nm < NEW_STRONG_MOMENTUM
        return weak_q or weak_m
    if scenario_id == "F_combined_safe_block":
        if _block_row_key(row) not in first_cross_keys:
            return False
        ob = str(row.get("old_breakdown_before_exit") or "").lower() in ("true", "1", "yes")
        nq = as_float(row.get("new_quality")) or 0.0
        nm = as_float(row.get("new_momentum")) or 0.0
        return (not ob) or (nq < NEW_STRONG_QUALITY and nm < NEW_STRONG_MOMENTUM)
    return False


def _pair_scenario_should_block(
    scenario_id: str,
    pair: Mapping[str, Any],
    *,
    first_pair_keys: set[tuple[str, str, str]],
    all_pairs: Sequence[Mapping[str, Any]],
) -> bool:
    pk = (
        _norm_session_id(str(pair.get("session_id") or "")),
        str(pair.get("old_symbol") or ""),
        str(pair.get("old_close_time") or ""),
    )
    if scenario_id in ("A_current",):
        return False
    if scenario_id == "B_phase141_full_block":
        return True
    if scenario_id == "C_first_cross_symbol_only":
        return _pair_is_first_cross(pair, all_pairs)
    if scenario_id == "D_old_not_breakdown_only":
        return not bool(pair.get("old_breakdown_before_exit"))
    if scenario_id == "E_new_not_strong_only":
        nq = float(pair.get("new_quality") or 0)
        nm = float(pair.get("new_momentum") or 0)
        return nq < NEW_STRONG_QUALITY or nm < NEW_STRONG_MOMENTUM
    if scenario_id == "F_combined_safe_block":
        if not _pair_is_first_cross(pair, all_pairs):
            return False
        ob = bool(pair.get("old_breakdown_before_exit"))
        nq = float(pair.get("new_quality") or 0)
        nm = float(pair.get("new_momentum") or 0)
        return (not ob) or (nq < NEW_STRONG_QUALITY and nm < NEW_STRONG_MOMENTUM)
    return False


def _pair_is_first_cross(
    pair: Mapping[str, Any],
    pairs: Sequence[Mapping[str, Any]],
) -> bool:
    sid = _norm_session_id(str(pair.get("session_id") or ""))
    old = str(pair.get("old_symbol") or "")
    close_t = str(pair.get("old_close_time") or "")
    ent = parse_ts(str(pair.get("new_entry_time") or ""))
    same = [
        p
        for p in pairs
        if _norm_session_id(str(p.get("session_id") or "")) == sid
        and str(p.get("old_symbol") or "") == old
        and str(p.get("old_close_time") or "") == close_t
    ]
    if not same:
        return False
    first_ts = min(parse_ts(str(p.get("new_entry_time") or "")) for p in same)
    return abs(ent - first_ts) < 1.0


def _first_pair_keys(pairs: Sequence[Mapping[str, Any]]) -> set[tuple[str, str, str]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        sid = _norm_session_id(str(p.get("session_id") or ""))
        old = str(p.get("old_symbol") or "")
        close_t = str(p.get("old_close_time") or "")
        groups[(sid, old, close_t)].append(dict(p))
    out: set[tuple[str, str, str]] = set()
    for key, ps in groups.items():
        ps.sort(key=lambda x: parse_ts(str(x.get("new_entry_time") or "")))
        out.add(key)
    return out


def _classification_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    c = Counter()
    for r in rows:
        if r.get("in_phase139_pair"):
            c["in_phase139_pair"] += 1
        else:
            c["not_in_phase139_pair"] += 1
        if r.get("is_immediate_fade_switch"):
            c["immediate_fade_switch"] += 1
        if r.get("is_delayed_unrelated"):
            c["delayed_unrelated"] += 1
        if r.get("blocked_entry_was_bad"):
            c["blocked_was_bad"] += 1
        if r.get("blocked_entry_was_good"):
            c["blocked_was_good"] += 1
    by_session = Counter(_norm_session_id(str(r.get("session_id") or "")) for r in rows)
    by_reason = Counter(str(r.get("old_exit_reason") or "") for r in rows)
    return {
        "blocked_unique_count": len(rows),
        "counts": dict(c),
        "by_session": dict(by_session),
        "by_old_exit_reason": dict(by_reason),
    }


def _scenario_metrics(
    scenario_id: str,
    classified: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
    trades_a: Mapping[tuple[str, str, str], _TradeShim],
    *,
    first_cross_keys: set[tuple[str, str, str]],
    first_pair_keys: set[tuple[str, str, str]],
    baseline_a_total: float,
    baseline_a_trades: int,
) -> dict[str, Any]:
    block_keys: set[tuple[str, str, str]] = set()
    for r in classified:
        if _scenario_should_block_row(scenario_id, r, first_cross_keys=first_cross_keys):
            block_keys.add(_block_row_key(r))

    avoided_bad = missed_good = 0
    removed_pnl = 0.0
    for key in block_keys:
        t = trades_a.get(key)
        if not t:
            continue
        pnl = float(t.realized_pnl_pct)
        removed_pnl += pnl
        if pnl < -PNL_EPS:
            avoided_bad += 1
        elif pnl > PNL_EPS:
            missed_good += 1

    pair_pnls: list[float] = []
    pair_deltas: list[float] = []
    for p in pairs:
        cur = _pnl_current(p)
        keep = _pnl_keep_old(p)
        if _pair_scenario_should_block(
            scenario_id, p, first_pair_keys=first_pair_keys, all_pairs=pairs
        ):
            pair_pnls.append(keep)
            pair_deltas.append(keep - cur)
        else:
            pair_pnls.append(cur)
            pair_deltas.append(0.0)

    tp = sum(
        1
        for k in block_keys
        if (t := trades_a.get(k)) and float(t.realized_pnl_pct) < -PNL_EPS
    )
    fp = sum(
        1
        for k in block_keys
        if (t := trades_a.get(k)) and float(t.realized_pnl_pct) > PNL_EPS
    )
    decided = tp + fp
    return {
        "scenario_id": scenario_id,
        "blocked_count": len(block_keys),
        "trade_count_proxy": baseline_a_trades - len(block_keys),
        "full_replay_total_pnl_proxy": round(baseline_a_total - removed_pnl, 4),
        "full_replay_delta_vs_A": round(-removed_pnl, 4),
        "pair_proxy_total_pnl": round(sum(pair_pnls), 4),
        "pair_proxy_delta_vs_A": round(sum(pair_deltas), 4),
        "avoided_bad_new": avoided_bad,
        "missed_good_new": missed_good,
        "precision": round(tp / decided, 4) if decided else None,
        "true_positive_blocks": tp,
        "false_positive_blocks": fp,
    }


def determine_verdict(
    scenarios: Sequence[Mapping[str, Any]],
    classification: Mapping[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    by_id = {s["scenario_id"]: s for s in scenarios}
    full = by_id.get("B_phase141_full_block") or {}
    combined = by_id.get("F_combined_safe_block") or {}
    first = by_id.get("C_first_cross_symbol_only") or {}

    not_in_pair = int(classification.get("counts", {}).get("not_in_phase139_pair", 0))
    unique = int(classification.get("blocked_unique_count", 0))
    comb_delta = float(combined.get("pair_proxy_delta_vs_A") or 0)
    full_delta = float(full.get("pair_proxy_delta_vs_A") or 0)
    comb_blocks = int(combined.get("blocked_count") or 0)
    full_blocks = int(full.get("blocked_count") or 0)
    notes.append(f"unique_blocks={unique} not_in_phase139_pair={not_in_pair}")

    if unique == 0:
        return "runner_support_missing", notes

    first_delta = float(first.get("pair_proxy_delta_vs_A") or 0)
    first_blocks = int(first.get("blocked_count") or 0)

    if first_delta >= PHASE139_BLOCK_DELTA * 0.95 and first_blocks < full_blocks * 0.1:
        notes.append(
            f"first_cross_only: {first_blocks} blocks, pair_delta={first_delta:.2f} "
            f"matches Phase139 full block"
        )
        return "scoped_block_promising", notes

    if not_in_pair > unique * 0.5:
        notes.append("majority of blocks are outside Phase139 fade-switch pairs")
        if comb_delta >= PHASE139_BLOCK_DELTA * 0.5:
            return "scoped_block_promising", notes + ["use scoped rule; full block over-reaches"]
        return "full_block_too_broad", notes

    if comb_delta >= full_delta * 0.85 and comb_blocks < full_blocks * 0.25:
        return "scoped_block_promising", notes + [
            f"F retains {comb_delta:.2f} pair delta with {comb_blocks} blocks vs {full_blocks}"
        ]

    if float(first.get("pair_proxy_delta_vs_A") or 0) >= PHASE139_BLOCK_DELTA * 0.9:
        return "scoped_block_promising", notes + ["first_cross_symbol_only near Phase139 gain"]

    if full_delta > 50 and comb_delta < 20:
        return "full_block_too_broad", notes

    if comb_delta < 5 and full_delta < 5:
        return "block_not_useful", notes

    if not_in_pair > 100 and comb_blocks < 10:
        return "need_entry_relation_features", notes + [
            "scoped rules miss relation; need tighter fade-switch linkage"
        ]

    return "scoped_block_promising", notes


def analyze_fade_switch_block_scope(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    phase141_events_path: Path,
    phase139_pairs_path: Path,
    phase141_review_path: Optional[Path] = None,
) -> dict[str, Any]:
    session_dirs = [Path(s) for s in session_dirs]
    raw_blocked = _load_blocked_events(phase141_events_path)
    classified_base = _dedupe_blocked(raw_blocked)

    pairs: list[dict[str, Any]] = []
    if phase139_pairs_path.is_file():
        with phase139_pairs_path.open(encoding="utf-8", newline="") as f:
            pairs = list(csv.DictReader(f))
    else:
        pairs = _fade_pairs(session_dirs)

    pairs_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        pairs_by_session[_norm_session_id(str(p.get("session_id") or ""))].append(dict(p))

    trades_a = _load_trades_a(session_dirs, pilot_config)
    baseline_a_total = PHASE141_A_TOTAL_PNL
    baseline_a_trades = PHASE141_A_TRADE_COUNT

    session_by_id = {
        _norm_session_id(
            str(s.relative_to(s.parent.parent)) if s.parent.parent else s.name
        ): s
        for s in session_dirs
    }

    candidate_cache: dict[str, list[dict[str, Any]]] = {}
    classified: list[dict[str, Any]] = []
    for b in classified_base:
        pair = _match_pair(b, pairs_by_session)
        sid = _norm_session_id(str(b.get("session_id") or ""))
        sdir = session_by_id.get(sid)
        extra: dict[str, Any] = {}
        if sdir and not pair:
            if sid not in candidate_cache:
                candidate_cache[sid] = load_candidate_events(sdir)
            snap = enrich_new_features_from_candidates(
                {
                    "new_symbol": b.get("blocked_new_symbol"),
                    "new_entry_time": b.get("entry_time"),
                    "switch_gap_sec": 0,
                },
                candidate_cache[sid],
            )
            extra = snap
        trade = trades_a.get(
            (
                sid,
                str(b.get("blocked_new_symbol") or ""),
                str(b.get("entry_time") or ""),
            )
        )
        classified.append(
            _enrich_block_row(b, pair=pair, trade=trade, extra=extra)
        )

    first_cross_keys = _first_cross_keys(classified)
    first_pair_keys = _first_pair_keys(pairs)
    classification = _classification_summary(classified)

    scenario_ids = (
        "A_current",
        "B_phase141_full_block",
        "C_first_cross_symbol_only",
        "D_old_not_breakdown_only",
        "E_new_not_strong_only",
        "F_combined_safe_block",
    )
    scenarios = [
        _scenario_metrics(
            sid,
            classified,
            pairs,
            trades_a,
            first_cross_keys=first_cross_keys,
            first_pair_keys=first_pair_keys,
            baseline_a_total=baseline_a_total,
            baseline_a_trades=baseline_a_trades,
        )
        for sid in scenario_ids
    ]

    overblocked = sorted(
        [
            r
            for r in classified
            if r.get("is_delayed_unrelated") and not r.get("in_phase139_pair")
        ],
        key=lambda x: float(x.get("time_since_old_exit_sec") or 0),
        reverse=True,
    )[:50]

    verdict, notes = determine_verdict(scenarios, classification)

    phase141_ref: dict[str, Any] = {}
    if phase141_review_path and phase141_review_path.is_file():
        phase141_ref = json.loads(phase141_review_path.read_text(encoding="utf-8")).get(
            "comparison", {}
        )

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "classification_summary": classification,
        "classified_blocked_entries": classified,
        "scenarios": scenarios,
        "overblocked_examples": overblocked,
        "phase141_reference": phase141_ref,
        "phase139_pair_count": len(pairs),
        "first_cross_block_count": len(first_cross_keys),
    }
