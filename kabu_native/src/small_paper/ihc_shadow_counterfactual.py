"""I/H/C shadow counterfactual reconstruction and daily summary persistence (Phase684)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

from research.phase631_profit_source_attribution import _num, _parse_iso
from small_paper.canonical_summary import collect_canonical_trades, is_canonical_trade
from small_paper.microsequence_pre_entry import compute_microsequence_pre_entry_features
from small_paper.readiness_forward_shadow import (
    BIG_WINNER_YEN,
    EARLY_STOP_SEC,
    _bounce_from_price_ring,
    evaluate_readiness_economics,
    evaluate_readiness_precision,
)
from small_paper.microsequence_recovery_fail_forward_shadow import evaluate_microsequence_recovery_fail

DEFAULT_SHADOW_CFG = SimpleNamespace(
    readiness_precision_shadow_enabled=True,
    readiness_precision_shadow_expectancy_max=2.5,
    readiness_precision_shadow_require_live_incomplete=True,
    readiness_economics_shadow_enabled=True,
    readiness_economics_shadow_bounce_min=0.45,
    readiness_economics_shadow_require_live_incomplete=True,
    microsequence_recovery_fail_shadow_enabled=True,
    microsequence_recovery_fail_bounce_min=0.2182,
    microsequence_recovery_fail_fall_from_high_max=-0.1735,
    microsequence_recovery_fail_slope_5min_max=0.1152,
)

SCENARIOS: tuple[tuple[str, Callable[[Mapping[str, Any]], bool]], ...] = (
    ("actual", lambda _t: False),
    ("I_only", lambda t: bool(t.get("I_block"))),
    ("H_only", lambda t: bool(t.get("H_block"))),
    ("C_only", lambda t: bool(t.get("C_block"))),
    ("I_OR_H", lambda t: bool(t.get("I_block") or t.get("H_block"))),
    ("I_OR_C", lambda t: bool(t.get("I_block") or t.get("C_block"))),
    ("H_OR_C", lambda t: bool(t.get("H_block") or t.get("C_block"))),
    ("I_OR_H_OR_C", lambda t: bool(t.get("IHC_union_block"))),
    ("I_AND_H", lambda t: bool(t.get("I_block") and t.get("H_block"))),
    ("I_AND_C", lambda t: bool(t.get("I_block") and t.get("C_block"))),
    ("H_AND_C", lambda t: bool(t.get("H_block") and t.get("C_block"))),
    ("I_AND_H_AND_C", lambda t: bool(t.get("I_block") and t.get("H_block") and t.get("C_block"))),
)

LANE_SUMMARY_KEYS = (
    "enabled",
    "evaluable_count",
    "block_count",
    "blocked_pnl_yen_100",
    "delta_yen",
    "avoided_loss_yen",
    "lost_profit_yen",
    "blocked_winners",
    "blocked_losers",
    "blocked_big_winners",
    "blocked_early_stop",
    "blocked_stop_hit",
    "counterfactual_total_pnl_yen",
    "counterfactual_pf",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _pnl(t: Mapping[str, Any]) -> float:
    return float(_num(t.get("pnl_yen_100")) or 0)


def _trade_id(t: Mapping[str, Any]) -> str:
    pid = str(t.get("position_id") or t.get("trade_id") or "").strip()
    if pid:
        return pid
    return "|".join((str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or "")))


def _is_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) > 0


def _is_loser(t: Mapping[str, Any]) -> bool:
    return _pnl(t) < 0


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) >= BIG_WINNER_YEN


def _is_stop_hit(t: Mapping[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == "stop_hit" or _bool(t.get("stop_hit"))


def _is_early_stop(t: Mapping[str, Any]) -> bool:
    hs = _float(t.get("hold_sec"))
    return bool(_is_stop_hit(t) and hs is not None and hs <= EARLY_STOP_SEC)


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    gp = sum(max(p, 0) for p in pnls)
    gl = abs(sum(min(p, 0) for p in pnls))
    if gl <= 0:
        return None if gp <= 0 else 999.0
    return round(gp / gl, 4)


def build_session_price_index(session_dir: Path) -> dict[str, list[tuple[float, float]]]:
    """Compact pre-entry price index from session event stream (no full PUSH copy)."""
    idx: dict[str, list[tuple[float, float]]] = defaultdict(list)
    jsonl = session_dir / "small_paper_events.jsonl"
    if not jsonl.is_file():
        return idx
    with jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            sym = str(e.get("symbol") or "")
            px = float(_num(e.get("current_price")) or _num(e.get("entry_price")) or 0)
            ts = _parse_iso(str(e.get("event_time") or e.get("entry_time") or ""))
            if not sym or px <= 0 or ts is None:
                continue
            idx[sym].append((ts.timestamp(), px))
    for sym in idx:
        pts = sorted(set(idx[sym]), key=lambda x: x[0])
        idx[sym] = pts
    return idx


def _ring_for_entry(
    price_idx: Mapping[str, list[tuple[float, float]]],
    *,
    symbol: str,
    entry_time: str,
    entry_px: float,
) -> list[tuple[float, float]]:
    et = _parse_iso(entry_time)
    if et is None:
        return []
    entry_ts = et.timestamp()
    ring = list(price_idx.get(symbol, []))
    if entry_px > 0:
        ring.append((entry_ts, entry_px))
    ring.sort(key=lambda x: x[0])
    return ring


def evaluate_trade_shadow_fields(
    trade: Mapping[str, Any],
    *,
    config: Any = DEFAULT_SHADOW_CFG,
    price_idx: Optional[Mapping[str, list[tuple[float, float]]]] = None,
    saved_flags: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    saved = dict(saved_flags or {})
    row = dict(trade)
    entry_px = float(_num(row.get("entry_price")) or _num(row.get("current_price")) or 0)
    sym = str(row.get("symbol") or "")
    ring = (
        _ring_for_entry(price_idx or {}, symbol=sym, entry_time=str(row.get("entry_time") or ""), entry_px=entry_px)
        if price_idx is not None
        else []
    )
    et = _parse_iso(str(row.get("entry_time") or ""))
    entry_ts = et.timestamp() if et is not None else None

    accept_bounce_saved = saved.get("readiness_bounce_from_recent_low_accept")
    accept_bounce = _float(accept_bounce_saved)
    accept_bounce_source = "saved" if accept_bounce is not None else "missing"
    if accept_bounce is None and ring and entry_ts is not None and entry_px > 0:
        accept_bounce = _bounce_from_price_ring(ring, entry_ts=entry_ts, entry_px=entry_px)
        accept_bounce_source = "recomputed_accept_ring_ratio"

    microseq = (
        compute_microsequence_pre_entry_features(ring, entry_ts=entry_ts, entry_px=entry_px)
        if ring and entry_ts is not None and entry_px > 0
        else {
            "microsequence_pre_entry_ok": False,
            "bounce_from_recent_low": None,
            "fall_from_recent_high": None,
            "slope_5min": None,
        }
    )
    microseq_bounce = saved.get("microseq_bounce_from_recent_low", microseq.get("bounce_from_recent_low"))
    microseq_fall = saved.get("microseq_fall_from_recent_high", microseq.get("fall_from_recent_high"))
    microseq_slope = saved.get("microseq_slope_5min", microseq.get("slope_5min"))
    microseq_source = "saved" if saved.get("microseq_bounce_from_recent_low") is not None else "recomputed_ring"

    microseq_pre_ok = bool(
        microseq.get("microsequence_pre_entry_ok")
        or row.get("microsequence_pre_entry_ok")
        or (
            microseq_bounce is not None
            and microseq_fall is not None
            and microseq_slope is not None
            and microseq_source == "saved"
        )
    )

    eval_row = {
        **row,
        "readiness_bounce_from_recent_low_accept": accept_bounce,
        "microseq_bounce_from_recent_low": microseq_bounce,
        "microseq_fall_from_recent_high": microseq_fall,
        "microseq_slope_5min": microseq_slope,
        "microsequence_pre_entry_ok": microseq_pre_ok,
    }

    i_evaluable = row.get("entry_expectancy_score_v2") is not None
    h_evaluable = accept_bounce is not None
    c_evaluable = (
        microseq_bounce is not None and microseq_fall is not None and microseq_slope is not None and microseq_pre_ok
    )

    i_block_saved = saved.get("readiness_precision_shadow_block")
    h_block_saved = saved.get("readiness_economics_shadow_block")
    c_block_saved = saved.get("microsequence_recovery_fail_shadow_block")

    i_block = (
        bool(i_block_saved)
        if i_block_saved is not None
        else (evaluate_readiness_precision(config, eval_row) if i_evaluable else False)
    )
    h_block = (
        bool(h_block_saved)
        if h_block_saved is not None
        else (evaluate_readiness_economics(config, eval_row) if h_evaluable else False)
    )
    c_block = (
        bool(c_block_saved)
        if c_block_saved is not None
        else (evaluate_microsequence_recovery_fail(config, eval_row) if c_evaluable else False)
    )

    parts: list[str] = []
    if i_block:
        parts.append("I")
    if h_block:
        parts.append("H")
    if c_block:
        parts.append("C")

    return {
        "readiness_bounce_from_recent_low_accept": accept_bounce,
        "readiness_bounce_source": accept_bounce_source,
        "microseq_bounce_from_recent_low": microseq_bounce,
        "microseq_fall_from_recent_high": microseq_fall,
        "microseq_slope_5min": microseq_slope,
        "microseq_source": microseq_source,
        "I_evaluable": i_evaluable,
        "H_evaluable": h_evaluable,
        "C_evaluable": c_evaluable,
        "I_block": i_block,
        "H_block": h_block,
        "C_block": c_block,
        "IH_union_block": i_block or h_block,
        "IC_union_block": i_block or c_block,
        "HC_union_block": h_block or c_block,
        "IHC_union_block": i_block or h_block or c_block,
        "overlap_type": "+".join(parts) if parts else "none",
        "I_block_saved": i_block_saved is not None,
        "H_block_saved": h_block_saved is not None,
        "C_block_saved": c_block_saved is not None,
    }


def load_session_canonical_trades(
    session_dir: Path,
    *,
    session_label: str,
    expected_count: Optional[int] = None,
    expected_pnl: Optional[float] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with (session_dir / "small_paper_events.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                events.append(json.loads(line))
    canonical = collect_canonical_trades(events)
    accepted: dict[tuple[str, str], dict[str, Any]] = {}
    for e in events:
        if e.get("event_type") != "accepted":
            continue
        accepted[(str(e.get("symbol") or ""), str(e.get("entry_time") or ""))] = dict(e)

    trades: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ex in canonical:
        tid = _trade_id(ex)
        if tid in seen:
            continue
        seen.add(tid)
        acc = accepted.get((str(ex.get("symbol") or ""), str(ex.get("entry_time") or "")), {})
        row = {**acc, **ex}
        row["session"] = session_label
        row["session_dir"] = str(session_dir)
        row["position_id"] = row.get("position_id") or tid
        row["is_winner"] = _is_winner(row)
        row["is_loser"] = _is_loser(row)
        row["is_big_winner"] = _is_big_winner(row)
        row["is_stop_hit"] = _is_stop_hit(row)
        row["is_early_stop_300s"] = _is_early_stop(row)
        trades.append(row)

    total_pnl = round(sum(_pnl(t) for t in trades), 2)
    meta = {
        "session": session_label,
        "trade_count": len(trades),
        "total_pnl_yen_100": total_pnl,
        "expected_count": expected_count,
        "expected_pnl": expected_pnl,
        "count_ok": expected_count is None or len(trades) == expected_count,
        "pnl_ok": expected_pnl is None or total_pnl == expected_pnl,
    }
    if expected_count is not None and len(trades) != expected_count:
        raise ValueError(f"{session_label} trade_count mismatch: got {len(trades)} expected {expected_count}")
    if expected_pnl is not None and total_pnl != expected_pnl:
        raise ValueError(f"{session_label} pnl mismatch: got {total_pnl} expected {expected_pnl}")
    return trades, meta


def enrich_trades_with_shadow(
    trades: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[str, list[tuple[float, float]]],
    config: Any = DEFAULT_SHADOW_CFG,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        saved = {k: t.get(k) for k in (
            "readiness_precision_shadow_block",
            "readiness_economics_shadow_block",
            "microsequence_recovery_fail_shadow_block",
            "readiness_bounce_from_recent_low_accept",
            "microseq_bounce_from_recent_low",
            "microseq_fall_from_recent_high",
            "microseq_slope_5min",
        ) if t.get(k) not in (None, "")}
        shadow = evaluate_trade_shadow_fields(t, config=config, price_idx=price_idx, saved_flags=saved)
        out.append({**dict(t), **shadow})
    return out


def audit_namespace_presence(session_dir: Path) -> dict[str, Any]:
    fields = (
        "readiness_bounce_from_recent_low_accept",
        "microseq_bounce_from_recent_low",
        "microseq_fall_from_recent_high",
        "microseq_slope_5min",
        "readiness_precision_shadow_block",
        "readiness_economics_shadow_block",
        "microsequence_recovery_fail_shadow_block",
        "shadow_union_ihc_block",
    )
    counts = {f: 0 for f in fields}
    accepted = 0
    with (session_dir / "small_paper_events.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            e = json.loads(line)
            if e.get("event_type") not in ("accepted", "observer_exit"):
                continue
            if e.get("event_type") == "accepted":
                accepted += 1
            for f in fields:
                if e.get(f) not in (None, ""):
                    counts[f] += 1
    phase683_post = counts["readiness_bounce_from_recent_low_accept"] > 0 or counts["microseq_bounce_from_recent_low"] > 0
    return {
        "accepted_or_exit_rows": accepted,
        "field_non_null_counts": counts,
        "phase683_namespace_saved": phase683_post,
        "saved_flags_usable": any(counts[f] > 0 for f in fields[4:]),
    }


def missing_feature_audit(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lane, eval_key, missing_fields in (
        ("I_precision", "I_evaluable", ("entry_expectancy_score_v2",)),
        ("H_economics", "H_evaluable", ("readiness_bounce_from_recent_low_accept",)),
        ("microsequence_C", "C_evaluable", ("microseq_bounce_from_recent_low", "microseq_fall_from_recent_high", "microseq_slope_5min")),
    ):
        not_eval = [t for t in trades if not t.get(eval_key)]
        rows.append(
            {
                "lane": lane,
                "evaluable_count": sum(1 for t in trades if t.get(eval_key)),
                "not_evaluable_count": len(not_eval),
                "missing_feature_counts": {
                    f: sum(1 for t in not_eval if t.get(f) in (None, "")) for f in missing_fields
                },
                "missing_trade_ids": ";".join(_trade_id(t) for t in not_eval[:50]),
            }
        )
    return rows


def scenario_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    block_pred: Callable[[Mapping[str, Any]], bool],
    actual_total_pnl: float,
) -> dict[str, Any]:
    blocked = [t for t in trades if block_pred(t)]
    kept = [t for t in trades if not block_pred(t)]
    blocked_pnls = [_pnl(t) for t in blocked]
    kept_pnls = [_pnl(t) for t in kept]
    avoided = round(-sum(_pnl(t) for t in blocked if _is_loser(t)), 2)
    lost = round(sum(_pnl(t) for t in blocked if _is_winner(t)), 2)
    blocked_actual = round(sum(blocked_pnls), 2)
    delta = round(-blocked_actual, 2)
    cf_total = round(actual_total_pnl + delta, 2)
    sym_pnl: dict[str, float] = defaultdict(float)
    win_sym: dict[str, float] = defaultdict(float)
    for t in blocked:
        sym = str(t.get("symbol") or "")
        sym_pnl[sym] += _pnl(t)
        if _is_winner(t):
            win_sym[sym] += _pnl(t)
    top_improve = sorted(sym_pnl.items(), key=lambda x: x[1])[:3]
    top_lost_win = sorted(win_sym.items(), key=lambda x: -x[1])[:3]
    return {
        "actual_trade_count": len(trades),
        "blocked_count": len(blocked),
        "kept_count": len(kept),
        "blocked_winner_count": sum(1 for t in blocked if _is_winner(t)),
        "blocked_loser_count": sum(1 for t in blocked if _is_loser(t)),
        "blocked_big_winner_count": sum(1 for t in blocked if _is_big_winner(t)),
        "blocked_early_stop_count": sum(1 for t in blocked if _is_early_stop(t)),
        "blocked_stop_hit_count": sum(1 for t in blocked if _is_stop_hit(t)),
        "avoided_loss_yen": avoided,
        "lost_profit_yen": lost,
        "blocked_actual_pnl_yen": blocked_actual,
        "delta_pnl_yen": delta,
        "counterfactual_total_pnl_yen": cf_total,
        "counterfactual_win_count": sum(1 for p in kept_pnls if p > 0),
        "counterfactual_loss_count": sum(1 for p in kept_pnls if p < 0),
        "counterfactual_win_rate": round(sum(1 for p in kept_pnls if p > 0) / max(1, len(kept_pnls)), 4),
        "counterfactual_gross_profit": round(sum(max(p, 0) for p in kept_pnls), 2),
        "counterfactual_gross_loss": round(abs(sum(min(p, 0) for p in kept_pnls)), 2),
        "counterfactual_profit_factor": _profit_factor(kept_pnls),
        "top_improvement_symbols": ",".join(f"{s}:{v}" for s, v in top_improve if v < 0),
        "top_lost_winner_symbols": ",".join(f"{s}:{v}" for s, v in top_lost_win),
    }


def build_lane_summary(
    trades: Sequence[Mapping[str, Any]],
    *,
    lane: str,
    block_key: str,
    eval_key: str,
    enabled: bool = True,
) -> dict[str, Any]:
    evaluable = [t for t in trades if t.get(eval_key)]
    blocked = [t for t in evaluable if t.get(block_key)]
    actual_total = round(sum(_pnl(t) for t in trades), 2)
    blocked_pnl = round(sum(_pnl(t) for t in blocked), 2)
    delta = round(-blocked_pnl, 2)
    kept_pnls = [_pnl(t) for t in trades if t not in blocked]
    return {
        "enabled": enabled,
        "evaluable_count": len(evaluable),
        "block_count": len(blocked),
        "blocked_pnl_yen_100": blocked_pnl,
        "delta_yen": delta,
        "avoided_loss_yen": round(-sum(_pnl(t) for t in blocked if _is_loser(t)), 2),
        "lost_profit_yen": round(sum(_pnl(t) for t in blocked if _is_winner(t)), 2),
        "blocked_winners": sum(1 for t in blocked if _is_winner(t)),
        "blocked_losers": sum(1 for t in blocked if _is_loser(t)),
        "blocked_big_winners": sum(1 for t in blocked if _is_big_winner(t)),
        "blocked_early_stop": sum(1 for t in blocked if _is_early_stop(t)),
        "blocked_stop_hit": sum(1 for t in blocked if _is_stop_hit(t)),
        "counterfactual_total_pnl_yen": round(actual_total + delta, 2),
        "counterfactual_pf": _profit_factor(kept_pnls),
    }


def build_shadow_ihc_portfolio_summary(trades: Sequence[Mapping[str, Any]], *, actual_total_pnl: float) -> dict[str, Any]:
    i_or_h = scenario_metrics(trades, block_pred=lambda t: bool(t.get("I_block") or t.get("H_block")), actual_total_pnl=actual_total_pnl)
    ihc = scenario_metrics(trades, block_pred=lambda t: bool(t.get("IHC_union_block")), actual_total_pnl=actual_total_pnl)
    ih_overlap = sum(1 for t in trades if t.get("I_block") and t.get("H_block"))
    ic_overlap = sum(1 for t in trades if t.get("I_block") and t.get("C_block"))
    hc_overlap = sum(1 for t in trades if t.get("H_block") and t.get("C_block"))
    ihc_overlap = sum(1 for t in trades if t.get("I_block") and t.get("H_block") and t.get("C_block"))
    return {
        "I_OR_H_block_count": i_or_h["blocked_count"],
        "I_OR_H_delta_yen": i_or_h["delta_pnl_yen"],
        "I_OR_H_counterfactual_total_pnl_yen": i_or_h["counterfactual_total_pnl_yen"],
        "I_OR_H_OR_C_block_count": ihc["blocked_count"],
        "I_OR_H_OR_C_delta_yen": ihc["delta_pnl_yen"],
        "I_OR_H_OR_C_counterfactual_total_pnl_yen": ihc["counterfactual_total_pnl_yen"],
        "I_H_overlap_count": ih_overlap,
        "I_C_overlap_count": ic_overlap,
        "H_C_overlap_count": hc_overlap,
        "I_H_C_overlap_count": ihc_overlap,
        "feature_sources": {
            "I": "expectancy",
            "H": "accept_bounce",
            "C": "microseq_ring",
        },
    }


def build_daily_shadow_summary(trades: Sequence[Mapping[str, Any]], *, actual_total_pnl: float) -> dict[str, Any]:
    return {
        "readiness_precision_shadow": build_lane_summary(
            trades, lane="I", block_key="I_block", eval_key="I_evaluable"
        ),
        "readiness_economics_shadow": build_lane_summary(
            trades, lane="H", block_key="H_block", eval_key="H_evaluable"
        ),
        "microsequence_recovery_fail_shadow": build_lane_summary(
            trades, lane="C", block_key="C_block", eval_key="C_evaluable"
        ),
        "shadow_ihc_portfolio": build_shadow_ihc_portfolio_summary(trades, actual_total_pnl=actual_total_pnl),
    }


def format_entry_shadow_discord_lines(summary: Mapping[str, Any]) -> list[str]:
    lines = ["[ENTRY SHADOW]"]
    mapping = (
        ("readiness_precision_shadow", "I"),
        ("readiness_economics_shadow", "H"),
        ("microsequence_recovery_fail_shadow", "C"),
    )
    for key, label in mapping:
        lane = summary.get(key) or {}
        if not lane.get("enabled", True):
            continue
        lines.append(
            f"{label}: block {lane.get('block_count', 0)} / "
            f"Δ {lane.get('delta_yen', 0):+,}円 / "
            f"仮想損益 {lane.get('counterfactual_total_pnl_yen', 0):+,}円"
        )
    ihc = summary.get("shadow_ihc_portfolio") or {}
    if ihc:
        lines.append(
            f"I∨H∨C: block {ihc.get('I_OR_H_OR_C_block_count', 0)} / "
            f"Δ {ihc.get('I_OR_H_OR_C_delta_yen', 0):+,}円 / "
            f"仮想損益 {ihc.get('I_OR_H_OR_C_counterfactual_total_pnl_yen', 0):+,}円"
        )
    return lines


def finalize_session_ihc_shadow_summary(
    accepted_rows: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    *,
    session_dir: Path,
    config: Any = DEFAULT_SHADOW_CFG,
) -> dict[str, Any]:
    """Finalize EXIT-completed canonical trades only (no open positions)."""
    canonical = collect_canonical_trades(events)
    if not canonical:
        return {}
    price_idx = build_session_price_index(session_dir)
    accepted_idx = {
        (str(r.get("symbol") or ""), str(r.get("entry_time") or "")): dict(r) for r in accepted_rows
    }
    trades: list[dict[str, Any]] = []
    for ex in canonical:
        acc = accepted_idx.get((str(ex.get("symbol") or ""), str(ex.get("entry_time") or "")), {})
        trades.append({**acc, **ex})
    enriched = enrich_trades_with_shadow(trades, price_idx=price_idx, config=config)
    actual_total = round(sum(_pnl(t) for t in enriched), 2)
    return build_daily_shadow_summary(enriched, actual_total_pnl=actual_total)
