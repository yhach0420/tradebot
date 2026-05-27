"""
Phase 144: Classify Phase143 first-switch blocks vs Phase142; refined block what-if (review only).
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_switch_block_scope_review import (
    PHASE141_A_TOTAL_PNL,
    PHASE141_A_TRADE_COUNT,
    _TradeShim,
    _block_row_key,
    _dedupe_blocked,
    _enrich_block_row,
    _first_cross_keys,
    _load_blocked_events,
    _load_trades_a,
    _match_pair,
    _pair_is_first_cross,
)
from research.fade_switch_policy_review import _pnl_current, _pnl_keep_old
from research.hybrid_fade_switch_policy_review import (
    enrich_new_features_from_candidates,
    load_candidate_events,
)
from research.hybrid_live_replay import MAX_CONCURRENT, build_hybrid_session
from research.mfe_mae_exit_review import parse_ts
from research.replay_fidelity_review import _norm_session_id
from research.switch_old_vs_new_review import MAX_PAIR_SEC, PNL_EPS

PHASE139_BLOCK_DELTA = 72.7341
PHASE142_BLOCK_COUNT = 23
PHASE142_PAIR_DELTA = 72.7341
PHASE142_FULL_REPLAY_DELTA = 0.8051
PHASE143_BLOCK_COUNT = 224
PHASE143_FULL_REPLAY_DELTA = -1.9118
IMMEDIATE_SWITCH_SEC = MAX_PAIR_SEC


def _load_phase143_blocked(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("event_kind") or "") != "fade_first_switch_blocked":
                continue
            row = dict(r)
            row["entry_time"] = row.get("blocked_new_entry_time") or row.get("entry_time")
            row["blocked_new_symbol"] = row.get("blocked_new_symbol") or row.get("symbol")
            rows.append(row)
    return rows


def _dedupe_phase143(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = (
            str(r.get("session_id") or ""),
            str(r.get("blocked_new_symbol") or ""),
            str(r.get("blocked_new_entry_time") or r.get("entry_time") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(r))
    return out


def _load_pairs_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _open_at_ts(
    positions: Sequence[Any],
    ts: float,
    *,
    exclude_symbol: Optional[str] = None,
) -> list[str]:
    open_syms: list[str] = []
    for p in positions:
        sym = p.symbol
        if exclude_symbol and sym == exclude_symbol:
            continue
        if p.entry_ts <= ts < p.close_ts:
            open_syms.append(sym)
    return open_syms


def _find_structural_old_exit(
    positions: Sequence[Any],
    old_symbol: str,
    fade_exit_time: str,
    *,
    tol_sec: float = 5.0,
) -> Optional[Any]:
    target = parse_ts(fade_exit_time)
    if target <= 0:
        return None
    best: Optional[Any] = None
    best_d = 1e18
    for p in positions:
        if p.symbol != old_symbol:
            continue
        d = abs(p.close_ts - target)
        if d < best_d:
            best_d = d
            best = p
    if best is not None and best_d <= tol_sec:
        return best
    return None


def _immediate_next_cross_entry(
    positions: Sequence[Any],
    old_pos: Any,
) -> Optional[tuple[str, str, float]]:
    """First cross-symbol entry after old_pos.close_ts within MAX_PAIR_SEC."""
    candidates: list[tuple[str, str, float]] = []
    for p in positions:
        if p.symbol == old_pos.symbol:
            continue
        gap = p.entry_ts - old_pos.close_ts
        if gap < 0 or gap > MAX_PAIR_SEC:
            continue
        candidates.append((p.symbol, p.entry_time, gap))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[2])
    return candidates[0]


def _phase142_first_cross_keys_from_phase141(phase141_events_path: Path) -> set[tuple[str, str, str]]:
    raw = _load_blocked_events(phase141_events_path)
    deduped = _dedupe_blocked(raw)
    normalized: list[dict[str, Any]] = []
    for r in deduped:
        r = dict(r)
        r["blocked_new_symbol"] = r.get("blocked_new_symbol") or r.get("symbol")
        normalized.append(r)
    return _first_cross_keys(normalized)


def _match_phase134_pair(
    block: Mapping[str, Any],
    phase134: Sequence[Mapping[str, Any]],
    *,
    time_tol_sec: float = 30.0,
) -> Optional[dict[str, Any]]:
    sid = _norm_session_id(str(block.get("session_id") or ""))
    new_sym = str(block.get("blocked_new_symbol") or "")
    old_sym = str(block.get("old_symbol") or "")
    ent_ts = parse_ts(str(block.get("entry_time") or block.get("blocked_new_entry_time") or ""))
    for p in phase134:
        if _norm_session_id(str(p.get("session_id") or "")) != sid:
            continue
        if str(p.get("new_symbol") or "") != new_sym:
            continue
        if str(p.get("old_symbol") or "") != old_sym:
            continue
        pts = parse_ts(str(p.get("new_entry_time") or ""))
        if abs(pts - ent_ts) <= time_tol_sec:
            return dict(p)
    return None


def _classify_overblock_bucket(row: Mapping[str, Any]) -> str:
    """Single primary overblock label (priority order)."""
    if not row.get("in_phase139_pair"):
        if row.get("is_unrelated_first_cross"):
            return "unrelated_entry"
        return "missing_pair_link"
    if row.get("matched_to_phase142_first_cross"):
        return "phase142_target_switch"
    if not row.get("within_300s_switch"):
        return "non_switch_candidate"
    if row.get("old_position_already_closed_long_before"):
        return "old_position_already_closed_long_before"
    if not row.get("cap_full_before_old_exit"):
        return "not_capacity_replacement"
    if not row.get("new_entry_fills_freed_slot"):
        return "not_capacity_replacement"
    if not row.get("is_immediate_next_accepted"):
        return "same_session_noise"
    if not row.get("pair_is_first_cross"):
        return "same_session_noise"
    return "phase143_overblock_other"


def _build_session_context(session_dirs: Sequence[Path]) -> dict[str, dict[str, Any]]:
    ctx: dict[str, dict[str, Any]] = {}
    for sdir in session_dirs:
        sdir = Path(sdir)
        sid = _norm_session_id(
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        hybrid = build_hybrid_session(sdir)
        positions = hybrid.positions
        by_old_close: dict[tuple[str, str], Any] = {}
        for p in positions:
            by_old_close[(p.symbol, p.close_time)] = p

        immediate_map: dict[tuple[str, str], tuple[str, str]] = {}
        for old in positions:
            nxt = _immediate_next_cross_entry(positions, old)
            if nxt:
                immediate_map[(old.symbol, old.close_time)] = (nxt[0], nxt[1])

        ctx[sid] = {
            "session_dir": sdir,
            "positions": positions,
            "by_old_close": by_old_close,
            "immediate_map": immediate_map,
            "fade_switches": hybrid.fade_switches,
        }
    return ctx


def _enrich_phase143_block(
    block: dict[str, Any],
    *,
    pair: Optional[Mapping[str, Any]],
    phase134_pair: Optional[Mapping[str, Any]],
    trade: Optional[_TradeShim],
    extra: Optional[Mapping[str, Any]],
    session_ctx: Mapping[str, Any],
    phase142_keys: set[tuple[str, str, str]],
) -> dict[str, Any]:
    row = _enrich_block_row(block, pair=pair, trade=trade, extra=extra)
    sid = _norm_session_id(str(block.get("session_id") or ""))
    positions = session_ctx.get("positions") or []
    fade_ts = parse_ts(str(block.get("fade_exit_time") or ""))
    ent_ts = parse_ts(str(row.get("entry_time") or ""))
    old_sym = str(block.get("old_symbol") or "")
    new_sym = str(row.get("blocked_new_symbol") or "")

    struct_old = _find_structural_old_exit(positions, old_sym, str(block.get("fade_exit_time") or ""))
    structural_close_ts = struct_old.close_ts if struct_old else None
    structural_close_time = struct_old.close_time if struct_old else None
    fade_vs_structural_sec: Optional[float] = None
    if structural_close_ts and fade_ts:
        fade_vs_structural_sec = round(fade_ts - structural_close_ts, 1)

    open_at_fade = _open_at_ts(positions, fade_ts) if fade_ts else []
    old_open_at_fade = old_sym in open_at_fade

    cap_full_before = False
    new_fills_slot = False
    if struct_old:
        open_before_exit = _open_at_ts(positions, struct_old.close_ts - 0.001)
        cap_full_before = len(open_before_exit) >= MAX_CONCURRENT
        open_at_new = _open_at_ts(positions, ent_ts) if ent_ts else []
        new_fills_slot = cap_full_before and old_sym not in open_at_new and len(open_at_new) >= MAX_CONCURRENT - 1

    immediate_map: dict[tuple[str, str], tuple[str, str]] = session_ctx.get("immediate_map") or {}
    imm: Optional[tuple[str, str]] = None
    if struct_old:
        imm = immediate_map.get((struct_old.symbol, struct_old.close_time))
    is_immediate_next = False
    if imm:
        is_immediate_next = imm[0] == new_sym and abs(parse_ts(imm[1]) - ent_ts) < 2.0

    pair_first = _pair_is_first_cross(pair, [pair]) if pair else False
    if pair and not pair_first:
        # need full pair list — filled by caller after loop
        pass

    in_p134 = phase134_pair is not None
    bkey = _block_row_key(row)
    matched_p142 = bkey in phase142_keys

    gap_from_structural: Optional[float] = None
    if structural_close_ts and ent_ts:
        gap_from_structural = round(ent_ts - structural_close_ts, 1)

    row.update(
        {
            "in_phase134_pair": in_p134,
            "within_300s_switch": bool(
                gap_from_structural is not None and gap_from_structural <= IMMEDIATE_SWITCH_SEC
            ),
            "old_position_open_at_fade_exit": old_open_at_fade,
            "old_position_already_closed_long_before": bool(
                fade_vs_structural_sec is not None and fade_vs_structural_sec > 30.0
            ),
            "structural_old_close_time": structural_close_time,
            "fade_vs_structural_close_sec": fade_vs_structural_sec,
            "gap_from_structural_old_exit_sec": gap_from_structural,
            "cap_full_before_old_exit": cap_full_before,
            "new_entry_fills_freed_slot": new_fills_slot,
            "is_immediate_next_accepted": is_immediate_next,
            "is_unrelated_first_cross": bool(
                not pair and (gap_from_structural or 999) > IMMEDIATE_SWITCH_SEC
            ),
            "matched_to_phase142_first_cross": matched_p142,
            "unmatched_reason": (
                ""
                if matched_p142
                else (
                    "not_in_phase142_first_cross_set"
                    if row.get("in_phase139_pair")
                    else "outside_phase139_pair"
                )
            ),
            "overblock_bucket": "",
            "pair_is_first_cross": False,
        }
    )
    return row


def _refined_should_block_row(scenario_id: str, row: Mapping[str, Any]) -> bool:
    if scenario_id in ("current_v1", "A_current"):
        return False
    if scenario_id in ("phase143_first_switch", "B_phase143_all"):
        return True
    if scenario_id in ("pair_linked_block", "C_pair_linked"):
        return bool(row.get("in_phase139_pair"))
    if scenario_id in ("cap_slot_freed_block", "D_cap_slot_freed"):
        return bool(row.get("new_entry_fills_freed_slot") and row.get("cap_full_before_old_exit"))
    if scenario_id in ("immediate_next_accepted_block", "E_immediate_next"):
        return bool(row.get("is_immediate_next_accepted"))
    if scenario_id in ("phase142_first_cross_match", "F_phase142_match"):
        return bool(row.get("matched_to_phase142_first_cross"))
    if scenario_id in ("pair_and_first_cross", "G_pair_first_cross"):
        return bool(row.get("in_phase139_pair") and row.get("pair_is_first_cross"))
    if scenario_id in ("refined_combined", "H_refined_combined"):
        return bool(
            row.get("in_phase139_pair")
            and row.get("pair_is_first_cross")
            and row.get("is_immediate_next_accepted")
            and row.get("new_entry_fills_freed_slot")
        )
    return False


def _refined_pair_should_block(
    scenario_id: str,
    pair: Mapping[str, Any],
    *,
    all_pairs: Sequence[Mapping[str, Any]],
    phase143_block_keys: set[tuple[str, str, str]],
) -> bool:
    sid = _norm_session_id(str(pair.get("session_id") or ""))
    nk = (sid, str(pair.get("new_symbol") or ""), str(pair.get("new_entry_time") or ""))
    if scenario_id in ("current_v1", "A_current"):
        return False
    if scenario_id in ("phase143_first_switch", "B_phase143_all"):
        return _pair_is_first_cross(pair, all_pairs)
    if scenario_id in ("pair_linked_block", "C_pair_linked"):
        return nk in phase143_block_keys and _pair_is_first_cross(pair, all_pairs)
    if scenario_id in ("phase142_first_cross_match", "F_phase142_match"):
        return _pair_is_first_cross(pair, all_pairs)
    if scenario_id in ("pair_and_first_cross", "G_pair_first_cross"):
        return _pair_is_first_cross(pair, all_pairs)
  # immediate/cap need row context — approximate via first_cross for pair proxy
    if scenario_id in ("immediate_next_accepted_block", "E_immediate_next"):
        return _pair_is_first_cross(pair, all_pairs)
    if scenario_id in ("cap_slot_freed_block", "D_cap_slot_freed"):
        return _pair_is_first_cross(pair, all_pairs)
    if scenario_id in ("refined_combined", "H_refined_combined"):
        return _pair_is_first_cross(pair, all_pairs)
    return False


def _scenario_metrics_refined(
    scenario_id: str,
    classified: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
    trades_a: Mapping[tuple[str, str, str], _TradeShim],
    *,
    phase143_keys: set[tuple[str, str, str]],
    baseline_a_total: float,
    baseline_a_trades: int,
) -> dict[str, Any]:
    block_keys: set[tuple[str, str, str]] = set()
    for r in classified:
        if _refined_should_block_row(scenario_id, r):
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
        if _refined_pair_should_block(
            scenario_id, p, all_pairs=pairs, phase143_block_keys=phase143_keys
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
    *,
    matched_p142: int,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    by_id = {s["scenario_id"]: s for s in scenarios}
    p143 = by_id.get("phase143_first_switch") or by_id.get("B_phase143_all") or {}
    refined = by_id.get("refined_combined") or by_id.get("H_refined_combined") or {}
    pair_link = by_id.get("pair_linked_block") or by_id.get("C_pair_linked") or {}
    p142_match = by_id.get("phase142_first_cross_match") or by_id.get("F_phase142_match") or {}

    p143_blocks = int(p143.get("blocked_count") or 0)
    p143_delta = float(p143.get("full_replay_delta_vs_A") or 0)
    p143_pair = float(p143.get("pair_proxy_delta_vs_A") or 0)

    notes.append(f"phase143_blocks={p143_blocks} matched_phase142={matched_p142}")
    notes.append(f"phase143_full_delta={p143_delta:.2f} pair_delta={p143_pair:.2f}")

    ref_blocks = int(refined.get("blocked_count") or 0)
    ref_delta = float(refined.get("full_replay_delta_vs_A") or 0)
    ref_pair = float(refined.get("pair_proxy_delta_vs_A") or 0)
    pl_blocks = int(pair_link.get("blocked_count") or 0)
    pl_delta = float(pair_link.get("full_replay_delta_vs_A") or 0)
    p42_blocks = int(p142_match.get("blocked_count") or 0)
    p42_delta = float(p142_match.get("full_replay_delta_vs_A") or 0)

    not_in_pair = int(classification.get("counts", {}).get("not_in_phase139_pair", 0))
    unique = int(classification.get("blocked_unique_count", 0))

    if ref_delta >= PHASE142_FULL_REPLAY_DELTA * 0.8 and ref_blocks <= PHASE142_BLOCK_COUNT * 2:
        notes.append(f"refined_combined: {ref_blocks} blocks delta={ref_delta:.2f}")
        return "refined_block_promising", notes

    if p42_blocks <= PHASE142_BLOCK_COUNT + 5 and p42_delta >= PHASE142_FULL_REPLAY_DELTA * 0.7:
        notes.append(f"phase142_match scenario: {p42_blocks} blocks delta={p42_delta:.2f}")
        return "refined_block_promising", notes

    if p143_blocks > PHASE142_BLOCK_COUNT * 3 and matched_p142 < PHASE142_BLOCK_COUNT:
        notes.append(
            f"only {matched_p142}/{p143_blocks} blocks match Phase142 first-cross keys"
        )
        return "first_switch_still_too_broad", notes

    if not_in_pair > unique * 0.15 and pl_delta < p143_delta:
        notes.append("pair_link trims out-of-pair blocks with better full-replay proxy")
        return "pair_link_required", notes

    if p143_pair >= PHASE139_BLOCK_DELTA * 0.95 and p143_delta < 0:
        notes.append("pair proxy matches Phase139 but full replay diverges — policy not operational at replay fidelity")
        return "block_policy_not_operational", notes

    if ref_pair < PHASE139_BLOCK_DELTA * 0.5 and pl_blocks < p143_blocks * 0.5:
        return "block_policy_not_operational", notes

    return "first_switch_still_too_broad", notes


def analyze_fade_first_switch_block_refinement(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    phase143_events_path: Path,
    phase141_events_path: Path,
    phase139_pairs_path: Path,
    phase134_pairs_path: Path,
    phase143_review_path: Optional[Path] = None,
) -> dict[str, Any]:
    session_dirs = [Path(s) for s in session_dirs]
    raw = _load_phase143_blocked(phase143_events_path)
    classified_base = _dedupe_phase143(raw)

    pairs = _load_pairs_csv(phase139_pairs_path)
    phase134 = _load_pairs_csv(phase134_pairs_path)

    pairs_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        pairs_by_session[_norm_session_id(str(p.get("session_id") or ""))].append(dict(p))

    trades_a = _load_trades_a(session_dirs, pilot_config)
    session_ctx = _build_session_context(session_dirs)
    phase142_keys = _phase142_first_cross_keys_from_phase141(phase141_events_path)

    session_by_id = {
        _norm_session_id(
            str(s.relative_to(s.parent.parent)) if s.parent.parent else s.name
        ): s
        for s in session_dirs
    }

    candidate_cache: dict[str, list[dict[str, Any]]] = {}
    classified: list[dict[str, Any]] = []
    for b in classified_base:
        sid = _norm_session_id(str(b.get("session_id") or ""))
        pair = _match_pair(b, pairs_by_session)
        p134 = _match_phase134_pair(b, phase134)
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
            (sid, str(b.get("blocked_new_symbol") or ""), str(b.get("entry_time") or ""))
        )
        ctx = session_ctx.get(sid, {})
        row = _enrich_phase143_block(
            b,
            pair=pair,
            phase134_pair=p134,
            trade=trade,
            extra=extra,
            session_ctx=ctx,
            phase142_keys=phase142_keys,
        )
        row["pair_is_first_cross"] = _pair_is_first_cross(pair, pairs) if pair else False
        row["overblock_bucket"] = _classify_overblock_bucket(row)
        classified.append(row)

    phase143_keys = {_block_row_key(r) for r in classified}

    c = Counter()
    overblock = Counter()
    for r in classified:
        if r.get("in_phase139_pair"):
            c["in_phase139_pair"] += 1
        else:
            c["not_in_phase139_pair"] += 1
        if r.get("in_phase134_pair"):
            c["in_phase134_pair"] += 1
        if r.get("within_300s_switch"):
            c["within_300s"] += 1
        if r.get("matched_to_phase142_first_cross"):
            c["matched_phase142"] += 1
        if r.get("is_immediate_next_accepted"):
            c["immediate_next"] += 1
        if r.get("new_entry_fills_freed_slot"):
            c["cap_slot_freed"] += 1
        overblock[str(r.get("overblock_bucket") or "")] += 1

    classification_summary = {
        "blocked_unique_count": len(classified),
        "counts": dict(c),
        "overblock_buckets": dict(overblock),
        "phase142_first_cross_key_count": len(phase142_keys),
        "matched_to_phase142_count": int(c.get("matched_phase142", 0)),
    }

    scenario_ids = (
        "current_v1",
        "phase143_first_switch",
        "pair_linked_block",
        "cap_slot_freed_block",
        "immediate_next_accepted_block",
        "phase142_first_cross_match",
        "pair_and_first_cross",
        "refined_combined",
    )
    scenarios = [
        _scenario_metrics_refined(
            sid,
            classified,
            pairs,
            trades_a,
            phase143_keys=phase143_keys,
            baseline_a_total=PHASE141_A_TOTAL_PNL,
            baseline_a_trades=PHASE141_A_TRADE_COUNT,
        )
        for sid in scenario_ids
    ]

    unmatched = [
        r
        for r in classified
        if not r.get("matched_to_phase142_first_cross")
    ]

    matched_p142 = int(c.get("matched_phase142", 0))
    verdict, notes = determine_verdict(
        scenarios, classification_summary, matched_p142=matched_p142
    )

    phase143_ref: dict[str, Any] = {}
    if phase143_review_path and phase143_review_path.is_file():
        phase143_ref = json.loads(phase143_review_path.read_text(encoding="utf-8"))

    root_cause = {
        "phase142_first_cross_definition": (
            "First blocked entry per (session, fade_exit_time, old_symbol) among Phase141 "
            "full-block log (843 deduped -> 23 keys)"
        ),
        "phase143_first_switch_definition": (
            "First cross-symbol block per fade-exit state in structural replay "
            f"({len(classified)} fade exits with a block within {MAX_PAIR_SEC}s)"
        ),
        "count_gap_explanation": (
            f"Phase143 blocks {len(classified)} fade-exit states vs Phase142's 23 "
            f"first-cross keys from Phase141; only {matched_p142} entry keys overlap"
        ),
    }

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "classification_summary": classification_summary,
        "classified_blocks": classified,
        "scenarios": scenarios,
        "unmatched_blocks": unmatched,
        "phase143_reference": phase143_ref.get("comparison", {}),
        "phase142_reference": {
            "blocked_count": PHASE142_BLOCK_COUNT,
            "pair_proxy_delta": PHASE142_PAIR_DELTA,
            "full_replay_delta": PHASE142_FULL_REPLAY_DELTA,
        },
        "root_cause": root_cause,
        "phase139_pair_count": len(pairs),
    }
