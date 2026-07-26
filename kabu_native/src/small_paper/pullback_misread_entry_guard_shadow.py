"""
Phase353: Pullback misread ENTRY guard shadow (B from Phase350).

Block shadow counterfactual when:
  - entry_rise_5min_pct < 0
  - AND entry_vwap_dev_pct < 0
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional

GUARD_VARIANT = "B_pullback_misread_guard"

SPLIT_VARIANTS = (
    "A_all_symbols",
    "B_dynamic40_only",
    "C_core10_only",
    "D_am_dynamic40_only",
    "E_am_all_symbols",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: list[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _entry_key(sym: str, ent: str) -> str:
    return f"{sym}|{ent}"


def would_block_pullback_misread_guard(fields: Mapping[str, Any]) -> bool:
    rise5 = _float(fields.get("entry_rise_5min_pct"))
    vwap_dev = _float(fields.get("entry_vwap_dev_pct"))
    return rise5 is not None and rise5 < 0 and vwap_dev is not None and vwap_dev < 0


def variant_blocked(
    variant: str,
    trade: Mapping[str, Any],
    *,
    session_kind: str,
) -> bool:
    """Pullback condition + universe/session scope for Phase354 split validation."""
    if not would_block_pullback_misread_guard(trade):
        return False
    slot = str(trade.get("universe_slot") or "")
    if variant == "A_all_symbols":
        return True
    if variant == "B_dynamic40_only":
        return slot == "dynamic"
    if variant == "C_core10_only":
        return slot == "core"
    if variant == "D_am_dynamic40_only":
        return session_kind == "am" and slot == "dynamic"
    if variant == "E_am_all_symbols":
        return session_kind == "am"
    return False


def _variant_metrics(
    trades: list[dict[str, Any]],
    *,
    variant: str,
    session_kind: str,
    actual_yens: list[float],
    stops_actual: int,
) -> dict[str, Any]:
    blocked = [t for t in trades if variant_blocked(variant, t, session_kind=session_kind)]
    kept = [t for t in trades if not variant_blocked(variant, t, session_kind=session_kind)]
    yens_kept = [float(t["pnl_yen_100"]) for t in kept if t.get("pnl_yen_100") is not None]
    yens_skip = [float(t["pnl_yen_100"]) for t in blocked if t.get("pnl_yen_100") is not None]
    stops_shadow = sum(1 for t in kept if t.get("is_stop_hit"))
    dyn_actual = [
        float(t["pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "dynamic" and t.get("pnl_yen_100") is not None
    ]
    core_actual = [
        float(t["pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "core" and t.get("pnl_yen_100") is not None
    ]
    dyn_kept = [t for t in kept if t.get("universe_slot") == "dynamic"]
    core_kept = [t for t in kept if t.get("universe_slot") == "core"]
    dyn_yens = [float(t["pnl_yen_100"]) for t in dyn_kept if t.get("pnl_yen_100") is not None]
    core_yens = [float(t["pnl_yen_100"]) for t in core_kept if t.get("pnl_yen_100") is not None]
    actual_total = round(sum(actual_yens), 2) if actual_yens else 0.0
    shadow_total = round(sum(yens_kept), 2) if yens_kept else 0.0
    delta = round(shadow_total - actual_total, 2)
    dyn_shadow = round(sum(dyn_yens), 2) if dyn_yens else 0.0
    core_shadow = round(sum(core_yens), 2) if core_yens else 0.0
    dyn_actual_total = round(sum(dyn_actual), 2) if dyn_actual else 0.0
    core_actual_total = round(sum(core_actual), 2) if core_actual else 0.0
    return {
        "variant": variant,
        "actual_total_pnl_yen_100": actual_total,
        "shadow_total_pnl_yen_100": shadow_total,
        "delta_yen": delta,
        "actual_profit_factor_yen_100": _pf(actual_yens),
        "profit_factor_yen_100": _pf(yens_kept),
        "trade_count_actual": len(trades),
        "trade_count_shadow": len(kept),
        "skipped_trade_count": len(blocked),
        "skipped_trade_pnl_actual": round(sum(yens_skip), 2) if yens_skip else 0.0,
        "stop_hit_count_actual": stops_actual,
        "stop_hit_count_shadow": stops_shadow,
        "stop_hit_reduction_count": stops_actual - stops_shadow,
        "improved_vs_actual": delta > 0,
        "dynamic40_actual_pnl_yen_100": dyn_actual_total,
        "dynamic40_shadow_pnl_yen_100": dyn_shadow,
        "dynamic40_delta_yen": round(dyn_shadow - dyn_actual_total, 2),
        "core10_actual_pnl_yen_100": core_actual_total,
        "core10_shadow_pnl_yen_100": core_shadow,
        "core10_delta_yen": round(core_shadow - core_actual_total, 2),
    }


def would_block_pullback_dynamic40_shadow(fields: Mapping[str, Any]) -> bool:
    """Phase354 B scope: pullback condition on Dynamic40 only."""
    if not would_block_pullback_misread_guard(fields):
        return False
    slot = str(fields.get("universe_slot") or "")
    bucket = str(fields.get("universe_bucket") or fields.get("source_bucket") or "")
    if slot == "dynamic":
        return True
    return bucket in ("dynamic40", "vol_liq_dynamic40")


def enrich_exit_pullback_misread_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
) -> dict[str, Any]:
    blocked = would_block_pullback_dynamic40_shadow(entry_shadow)
    actual_yen = round((exit_price - entry_price) * 100.0, 2)
    shadow_yen = 0.0 if blocked else actual_yen
    return {
        "pullback_misread_guard_shadow_blocked": blocked,
        "pullback_misread_shadow_pnl_yen_100": shadow_yen,
        "pullback_misread_shadow_delta_yen": round(shadow_yen - actual_yen, 2),
        "stop_hit": exit_reason == "stop_hit",
    }


@dataclass
class PullbackMisreadEntryGuardShadowCounters:
    pullback_misread_guard_shadow_blocked_count: int = 0
    pullback_misread_guard_shadow_kept_count: int = 0
    pullback_misread_guard_shadow_reject_candidate_count: int = 0
    actual_total_pnl_yen_100: float = 0.0
    shadow_total_pnl_yen_100: float = 0.0
    skipped_trade_pnl_actual: float = 0.0
    stop_hit_count_actual: int = 0
    stop_hit_count_shadow: int = 0

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        if would_block_pullback_dynamic40_shadow(fields):
            self.pullback_misread_guard_shadow_blocked_count += 1
        else:
            self.pullback_misread_guard_shadow_kept_count += 1

    def record_reject_candidate(self, fields: Mapping[str, Any]) -> None:
        if would_block_pullback_dynamic40_shadow(fields):
            self.pullback_misread_guard_shadow_reject_candidate_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        delta = _float(row.get("pullback_misread_shadow_delta_yen")) or 0.0
        shadow_yen = _float(row.get("pullback_misread_shadow_pnl_yen_100")) or 0.0
        actual_yen = round(shadow_yen - delta, 2)
        blocked = would_block_pullback_dynamic40_shadow(row)
        self.actual_total_pnl_yen_100 = round(self.actual_total_pnl_yen_100 + actual_yen, 2)
        self.shadow_total_pnl_yen_100 = round(self.shadow_total_pnl_yen_100 + shadow_yen, 2)
        if blocked:
            self.skipped_trade_pnl_actual = round(self.skipped_trade_pnl_actual + actual_yen, 2)
        if row.get("is_stop_hit") or row.get("stop_hit"):
            self.stop_hit_count_actual += 1
            if not blocked:
                self.stop_hit_count_shadow += 1

    def summary_fields(self) -> dict[str, Any]:
        delta = round(self.shadow_total_pnl_yen_100 - self.actual_total_pnl_yen_100, 2)
        try:
            from small_paper.shadow_registry import is_shadow_runtime_enabled

            enabled = is_shadow_runtime_enabled("pullback_misread_guard_shadow")
        except Exception:
            enabled = False
        return {
            "pullback_misread_guard_shadow_enabled": enabled,
            "pullback_misread_guard_shadow_blocked_count": (
                self.pullback_misread_guard_shadow_blocked_count
            ),
            "pullback_misread_guard_shadow_kept_count": (
                self.pullback_misread_guard_shadow_kept_count
            ),
            "pullback_misread_guard_shadow_reject_candidate_count": (
                self.pullback_misread_guard_shadow_reject_candidate_count
            ),
            "pullback_misread_guard_shadow_actual_total_pnl_yen_100": (
                self.actual_total_pnl_yen_100
            ),
            "pullback_misread_guard_shadow_total_pnl_yen_100": self.shadow_total_pnl_yen_100,
            "pullback_misread_guard_shadow_delta_yen": delta,
            "pullback_misread_guard_shadow_skipped_trade_pnl_actual": (
                self.skipped_trade_pnl_actual
            ),
            "pullback_misread_guard_shadow_stop_hit_reduction_count": (
                self.stop_hit_count_actual - self.stop_hit_count_shadow
            ),
            "pullback_misread_guard_shadow_improved_vs_actual": delta > 0,
        }


def enrich_trade_features_for_review(
    acc: dict[str, str],
    ex: dict[str, str],
    universe: dict[str, dict[str, str]],
) -> dict[str, Any]:
    sym = str(ex.get("symbol") or "")
    u = universe.get(sym, {})
    rise5 = _float(acc.get("entry_rise_5min_pct") or ex.get("entry_rise_5min_pct"))
    vwap_dev = _float(acc.get("entry_vwap_dev_pct") or ex.get("entry_vwap_dev_pct"))
    blocked = would_block_pullback_misread_guard(
        {"entry_rise_5min_pct": rise5, "entry_vwap_dev_pct": vwap_dev}
    )

    ep, xp = _float(ex.get("entry_price")), _float(ex.get("exit_price"))
    yen = round((xp - ep) * 100.0, 2) if ep is not None and xp is not None else None
    reason = str(ex.get("structural_exit_reason") or ex.get("exit_reason") or "")
    shadow_yen = 0.0 if blocked else (yen or 0.0)

    return {
        "trade_key": _entry_key(sym, str(ex.get("entry_time") or "")),
        "symbol": sym,
        "entry_time": ex.get("entry_time"),
        "exit_time": ex.get("exit_time"),
        "entry_price": ep,
        "exit_price": xp,
        "pnl_yen_100": yen,
        "is_stop_hit": reason == "stop_hit",
        "exit_reason": reason,
        "entry_rise_5min_pct": rise5,
        "entry_vwap_dev_pct": vwap_dev,
        "pullback_misread_guard_shadow_blocked": blocked,
        "pullback_misread_shadow_pnl_yen_100": shadow_yen,
        "pullback_misread_shadow_delta_yen": round((shadow_yen - (yen or 0.0)), 2)
        if yen is not None
        else None,
        "universe_slot": u.get("universe_slot", ""),
        "source_bucket": u.get("source_bucket", ""),
    }


def _stream_events_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            yield row


def evaluate_session(session_meta: Mapping[str, Any], *, reports_dir: Path) -> dict[str, Any]:
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _load_session_summary,
        _load_universe,
        _session_source_label,
        _universe_path_for_session,
    )

    sess_dir = Path(str(session_meta["session_dir"]))
    summary = _load_session_summary(sess_dir)
    session_kind = str(session_meta.get("session_kind") or _infer_session_kind(sess_dir, summary))
    universe = _load_universe(
        _universe_path_for_session(
            str(session_meta["day"]),
            session_kind,
            summary,
            reports_dir,
        )
    )

    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") != "observer_exit" or row.get("pnl_pct") in (None, ""):
            continue
        key = (row.get("symbol", ""), row.get("entry_time", ""))
        acc = accepted.get(key, {})
        t = enrich_trade_features_for_review(acc, row, universe)
        t["session_id"] = session_meta["session_id"]
        t["day"] = session_meta["day"]
        t["session_kind"] = session_kind
        trades.append(t)

    actual_yens = [float(t["pnl_yen_100"]) for t in trades if t.get("pnl_yen_100") is not None]
    shadow_yens = [
        float(t["pullback_misread_shadow_pnl_yen_100"])
        for t in trades
        if t.get("pullback_misread_shadow_pnl_yen_100") is not None
    ]
    skipped = [t for t in trades if t.get("pullback_misread_guard_shadow_blocked")]
    skipped_pnl = [float(t["pnl_yen_100"]) for t in skipped if t.get("pnl_yen_100") is not None]
    stops_actual = sum(1 for t in trades if t.get("is_stop_hit"))
    stops_shadow = sum(
        1 for t in trades if t.get("is_stop_hit") and not t.get("pullback_misread_guard_shadow_blocked")
    )

    dyn_actual_yens = [
        float(t["pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "dynamic" and t.get("pnl_yen_100") is not None
    ]
    core_actual_yens = [
        float(t["pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "core" and t.get("pnl_yen_100") is not None
    ]
    dyn_shadow_yens = [
        float(t["pullback_misread_shadow_pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "dynamic" and t.get("pullback_misread_shadow_pnl_yen_100") is not None
    ]
    core_shadow_yens = [
        float(t["pullback_misread_shadow_pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "core" and t.get("pullback_misread_shadow_pnl_yen_100") is not None
    ]

    actual_total = round(sum(actual_yens), 2) if actual_yens else 0.0
    shadow_total = round(sum(shadow_yens), 2) if shadow_yens else 0.0
    delta = round(shadow_total - actual_total, 2)

    base = {
        "session_meta": dict(session_meta),
        "session_kind": session_kind,
        "session_source": str(session_meta.get("session_source") or _session_source_label(sess_dir)),
        "trades": trades,
    }
    variants = {
        v: _variant_metrics(
            trades,
            variant=v,
            session_kind=session_kind,
            actual_yens=actual_yens,
            stops_actual=stops_actual,
        )
        for v in SPLIT_VARIANTS
    }
    return {**base, "variants": variants, **variants["A_all_symbols"]}
