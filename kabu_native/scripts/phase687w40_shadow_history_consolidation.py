#!/usr/bin/env python3
"""Phase687W40: Historical Shadow Performance Consolidation (research only).

MAINLINE / Shadow adoption / orders unchanged.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
SMALL = NATIVE / "results" / "small_paper"
REPORTS = NATIVE / "results" / "reports"
OUT = REPORTS / "phase687w40_shadow_history_summary"
REG652 = REPORTS / "phase652_shadow_registry" / "phase652_shadow_registry.csv"
ADOPT668 = REPORTS / "phase668_existing_shadow_adoption" / "phase668_shadow_adoption_summary.csv"
DISP_INV = REPORTS / "phase687w25c_r3_legacy_embed_reentry_visibility" / "displayed_shadow_inventory.csv"
JST = ZoneInfo("Asia/Tokyo")
MAX_CONCURRENT = 5

# Entry-block shadows with event-level flags for CAP replay
EVENT_BLOCK_SHADOWS: dict[str, str] = {
    "pbv2_rise5_shadow": "pbv2_rise5_shadow_block",
    "pbv2_flat_band_shadow": "pbv2_flat_band_shadow_block",
    "pullback_misread_guard_shadow": "pullback_misread_guard_shadow_blocked",
    "flat_weak_range_shadow": "flat_weak_range_shadow_block",
    "readiness_precision_shadow": "readiness_precision_shadow_block",
    "readiness_economics_shadow": "readiness_economics_shadow_block",
    "vwap_shadow_reject": "vwap_shadow_reject_candidate",
    "limit_up_proximity_entry_guard_shadow": "limit_up_proximity_guard_shadow_blocked",
    "microsequence_recovery_fail_shadow": "microsequence_recovery_fail_shadow_block",
}

# Summary-level extractors: shadow_id -> mapping of metric keys
SUMMARY_SPECS: dict[str, dict[str, Any]] = {
    "pbv2_rise5_shadow": {
        "enabled": "pbv2_rise5_shadow_enabled",
        "blocked": "pbv2_rise5_shadow_block_count",
        "accepted": "pbv2_rise5_shadow_kept_count",
        "target": "pbv2_rise5_shadow_target_count",
        "delta": "pbv2_rise5_shadow_delta_yen",
        "alt_delta": "pbv2_rise5_shadow_net_effect_yen",
        "bw": "pbv2_rise5_shadow_blocked_winners",
        "bl": "pbv2_rise5_shadow_blocked_losers",
        "shadow_pnl": "pbv2_rise5_shadow_total_pnl_yen_100",
        "actual_pnl": "pbv2_rise5_shadow_actual_total_pnl_yen_100",
    },
    "pbv2_flat_band_shadow": {
        "enabled": "pbv2_flat_band_shadow_enabled",
        "blocked": "pbv2_flat_band_shadow_block_count",
        "accepted": "pbv2_flat_band_shadow_kept_count",
        "target": "pbv2_flat_band_shadow_target_count",
        "delta": "pbv2_flat_band_shadow_delta_yen",
        "alt_delta": "pbv2_flat_band_shadow_net_effect_yen",
        "bw": "pbv2_flat_band_shadow_blocked_winners",
        "bl": "pbv2_flat_band_shadow_blocked_losers",
        "shadow_pnl": "pbv2_flat_band_shadow_total_pnl_yen_100",
        "actual_pnl": "pbv2_flat_band_shadow_actual_total_pnl_yen_100",
        "mainline_flag": "pbv2_flat_band_mainline_enabled",
    },
    "pullback_misread_guard_shadow": {
        "enabled": "pullback_misread_guard_shadow_enabled",
        "blocked": "pullback_misread_guard_shadow_blocked_count",
        "accepted": "pullback_misread_guard_shadow_kept_count",
        "delta": "pullback_misread_guard_shadow_delta_yen",
        "shadow_pnl": "pullback_misread_guard_shadow_total_pnl_yen_100",
        "actual_pnl": "pullback_misread_guard_shadow_actual_total_pnl_yen_100",
    },
    "flat_weak_range_shadow": {
        "enabled": "flat_weak_range_shadow_enabled",
        "blocked": "flat_weak_range_shadow_block_count",
        "accepted": "flat_weak_range_shadow_kept_count",
        "target": "flat_weak_range_shadow_target_count",
        "delta": "flat_weak_range_shadow_delta_yen",
        "bw": "flat_weak_range_shadow_blocked_winners",
        "bl": "flat_weak_range_shadow_blocked_losers",
        "shadow_pnl": "flat_weak_range_shadow_total_pnl_yen_100",
        "actual_pnl": "flat_weak_range_shadow_actual_total_pnl_yen_100",
        "stop_red": "flat_weak_range_shadow_stop_hit_reduction",
        "np_red": "flat_weak_range_shadow_no_progress_reduction",
    },
    "board_dynamic_trailing_shadow": {
        "enabled": "board_dynamic_shadow_enabled",
        "blocked": "board_dynamic_shadow_exit_count",  # overlay exits evaluated
        "delta": "board_dynamic_shadow_total_delta_yen",
        "stop": "board_dynamic_shadow_stop_hit_count",
        "trailing": "board_dynamic_shadow_trailing_mfe_count",
    },
    "imbalance_shadow": {
        "enabled": "imbalance_shadow_enabled",
        "accepted": "imbalance_shadow_count",
        "shadow_pnl": "imbalance_shadow_total_pnl",  # often pct-scale; keep as reported
        "pf": "imbalance_shadow_pf",
        "stop": "imbalance_shadow_stop_hit_count",
        "trailing": "imbalance_shadow_trailing_mfe_count",
        "note": "pnl may be pct units in older sessions",
    },
    "exit_shadow_monitor_t2": {
        "enabled": "exit_shadow_monitor_t2_enabled",
        "delta": "shadow_exit_t2_delta",
        "shadow_pnl": "shadow_exit_t2_pnl",
        "parent_enabled": "exit_shadow_monitor_enabled",
    },
    "exit_shadow_monitor_t3": {
        "enabled": "exit_shadow_monitor_t3_enabled",
        "delta": "shadow_exit_t3_delta",
        "shadow_pnl": "shadow_exit_t3_pnl",
        "parent_enabled": "exit_shadow_monitor_enabled",
    },
    "readiness_precision_shadow": {
        "enabled": "readiness_precision_shadow_enabled",
        "nested": "readiness_precision_shadow",
    },
    "readiness_economics_shadow": {
        "enabled": "readiness_economics_shadow_enabled",
        "nested": "readiness_economics_shadow",
    },
    "readiness_refined_h_shadow": {
        "enabled": "readiness_refined_h_shadow_enabled",
        "research_only": True,
    },
    "vwap_shadow_reject": {
        "enabled": "vwap_shadow_reject_enabled",
        "blocked": "vwap_shadow_reject_candidate_count",
        "shadow_pnl": "vwap_shadow_candidate_total_pnl",
        "pf": "vwap_shadow_candidate_pf",
        "stop": "vwap_shadow_candidate_stop_hit_count",
        "trailing": "vwap_shadow_candidate_trailing_mfe_count",
    },
    "limit_up_proximity_entry_guard_shadow": {
        "enabled": "limit_up_proximity_guard_shadow_enabled",
        "blocked": "limit_up_proximity_guard_shadow_blocked_count",
        "accepted": "limit_up_proximity_guard_shadow_kept_count",
        "delta": "limit_up_proximity_guard_shadow_delta_yen",
        "shadow_pnl": "limit_up_proximity_guard_shadow_total_pnl_yen_100",
        "actual_pnl": "limit_up_proximity_guard_shadow_actual_total_pnl_yen_100",
    },
    "microsequence_recovery_fail_shadow": {
        "enabled": "microsequence_recovery_fail_shadow_enabled",
        "nested": "microsequence_recovery_fail_shadow",
    },
    "extended_entry_shadow": {
        "enabled": None,
        "accepted": "extended_entry_shadow_count",
        "shadow_pnl": "extended_entry_shadow_pnl_estimate",
        "stop": "extended_entry_shadow_stop_hit_count",
        "trailing": "extended_entry_shadow_trailing_mfe_count",
    },
    "trading_value_shadow_gate": {"enabled": "trading_value_shadow_gate_enabled"},
    "volume_gate_relaxation_shadow": {"enabled": "volume_gate_relaxation_shadow_enabled"},
    "quality_formula_shadow": {"enabled": "quality_formula_shadow_enabled"},
    "entry_expectancy_score_shadow": {"enabled": "entry_expectancy_score_shadow_enabled"},
}


EXTRA_INVENTORY = [
    {
        "shadow_id": "flat_weak_range_shadow",
        "phase": "670",
        "name": "Flat weak-range entry filter",
        "category": "Filter",
        "entry_or_exit": "ENTRY",
        "purpose": "Block weak flat-range breakouts that fail to progress",
    },
    {
        "shadow_id": "imbalance_shadow",
        "phase": "230+",
        "name": "Order-book imbalance tier shadow",
        "category": "ENTRY",
        "entry_or_exit": "ENTRY",
        "purpose": "Observe imbalance percentile tiers vs outcome",
    },
    {
        "shadow_id": "readiness_precision_shadow",
        "phase": "680+",
        "name": "Readiness precision block",
        "category": "Filter",
        "entry_or_exit": "ENTRY",
        "purpose": "Block low-expectancy incomplete-live candidates",
    },
    {
        "shadow_id": "readiness_economics_shadow",
        "phase": "680+",
        "name": "Readiness economics block",
        "category": "Filter",
        "entry_or_exit": "ENTRY",
        "purpose": "Block poor bounce/economics incomplete-live candidates",
    },
    {
        "shadow_id": "readiness_refined_h_shadow",
        "phase": "680+",
        "name": "Readiness refined-H research",
        "category": "RESEARCH_ONLY",
        "entry_or_exit": "ENTRY",
        "purpose": "Research-only readiness refinement",
    },
    {
        "shadow_id": "exit_shadow_monitor_t2",
        "phase": "563",
        "name": "EXIT shadow monitor T2",
        "category": "EXIT",
        "entry_or_exit": "EXIT",
        "purpose": "Counterfactual exit timing T2",
    },
    {
        "shadow_id": "exit_shadow_monitor_t3",
        "phase": "563",
        "name": "EXIT shadow monitor T3",
        "category": "EXIT",
        "entry_or_exit": "EXIT",
        "purpose": "Counterfactual exit timing T3",
    },
    {
        "shadow_id": "vwap_shadow_reject",
        "phase": "legacy",
        "name": "VWAP reject shadow",
        "category": "Filter",
        "entry_or_exit": "ENTRY",
        "purpose": "VWAP deviation reject counterfactual",
    },
    {
        "shadow_id": "microsequence_recovery_fail_shadow",
        "phase": "682+",
        "name": "Microsequence recovery-fail",
        "category": "Filter",
        "entry_or_exit": "ENTRY",
        "purpose": "IHC microsequence recovery-fail block",
    },
    {
        "shadow_id": "extended_entry_shadow",
        "phase": "legacy",
        "name": "Extended entry shadow",
        "category": "Monitoring",
        "entry_or_exit": "ENTRY",
        "purpose": "Log extended-entry candidates",
    },
    {
        "shadow_id": "limit_up_proximity_entry_guard_shadow",
        "phase": "legacy",
        "name": "Limit-up proximity guard",
        "category": "Filter",
        "entry_or_exit": "ENTRY",
        "purpose": "Block near-limit-up chases",
    },
    {
        "shadow_id": "trading_value_shadow_gate",
        "phase": "legacy",
        "name": "Trading value shadow gate",
        "category": "Monitoring",
        "entry_or_exit": "ENTRY",
        "purpose": "Trading-value gate observability",
    },
    {
        "shadow_id": "volume_gate_relaxation_shadow",
        "phase": "590",
        "name": "Volume gate relaxation",
        "category": "Monitoring",
        "entry_or_exit": "ENTRY",
        "purpose": "V90/V80 rescue observability",
    },
    {
        "shadow_id": "quality_formula_shadow",
        "phase": "legacy",
        "name": "Quality formula shadow",
        "category": "Monitoring",
        "entry_or_exit": "ENTRY",
        "purpose": "Quality ranking observability",
    },
    {
        "shadow_id": "entry_expectancy_score_shadow",
        "phase": "230+",
        "name": "Entry expectancy score shadow",
        "category": "Monitoring",
        "entry_or_exit": "ENTRY",
        "purpose": "Expectancy score observability",
    },
]


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in ((k, r.get(k)) for k in cols)})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y")


def _pf(pnls: Sequence[float]) -> float:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    if gl <= 0:
        return 999.0 if gp > 0 else 0.0
    return round(gp / gl, 4)


def iter_sessions() -> list[tuple[str, Path]]:
    out = []
    if not SMALL.is_dir():
        return out
    for day in sorted(SMALL.iterdir()):
        if not day.is_dir() or not re.fullmatch(r"\d{8}", day.name):
            continue
        if day.name.startswith("2099"):
            continue
        for sess in sorted(day.glob("live_session_*")):
            if not sess.is_dir() or sess.name.endswith("_abort"):
                continue
            if not (sess / "small_paper_summary.json").is_file():
                continue
            out.append((day.name, sess))
    return out


def load_phase668() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not ADOPT668.is_file():
        return out
    with ADOPT668.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[str(row.get("shadow_id"))] = row
    return out


def classify_kind(entry_or_exit: str, category: str) -> str:
    e = (entry_or_exit or "").lower()
    c = (category or "").lower()
    if "research" in c or "research" in e:
        return "RESEARCH_ONLY"
    if "monitor" in c or "observ" in c:
        return "Monitoring"
    if "exit" in e or "exit" in c:
        return "EXIT"
    if "filter" in c or "guard" in c:
        return "Filter"
    if "entry" in e or "entry" in c:
        return "ENTRY"
    return category or "Monitoring"


def classify_status(shadow_id: str, reg: dict[str, str], adopt: dict[str, str], yaml_hints: dict[str, Any]) -> str:
    # Explicit promotions / removals (shadow_id-scoped — do not use global yaml flags alone)
    if shadow_id == "pbv2_flat_band_shadow":
        if adopt.get("decision") == "ADOPT" or bool(yaml_hints.get("pbv2_flat_band_mainline_enabled")):
            return "PROMOTED_TO_MAINLINE"
    if shadow_id in ("pbv2_rise5_shadow", "exit_shadow_monitor_t2", "exit_shadow_monitor_t3", "exit_shadow_monitor_t2_t3"):
        if adopt.get("decision") == "REMOVE" or str(reg.get("status")) == "disabled":
            return "REMOVED"
        if shadow_id == "pbv2_rise5_shadow" and yaml_hints.get("pbv2_rise5_shadow_enabled") is False:
            return "REMOVED"
        if shadow_id.startswith("exit_shadow_monitor") and yaml_hints.get("exit_shadow_monitor_enabled") is False:
            return "REMOVED"
    if shadow_id == "vwap_shadow_reject" and adopt.get("decision") == "REMOVE":
        return "REMOVED"
    if shadow_id == "readiness_refined_h_shadow" or bool(yaml_hints.get("readiness_refined_h_shadow_research_only")):
        if shadow_id == "readiness_refined_h_shadow":
            return "RESEARCH_ONLY"
    if "research" in str(reg.get("runtime_or_research") or "").lower() or "research_only" in str(
        reg.get("status") or ""
    ).lower():
        return "RESEARCH_ONLY"
    # Board-dynamic: production trailing adopted, but overlay remains an ACTIVE monitoring shadow
    if shadow_id == "board_dynamic_trailing_shadow":
        return "ACTIVE_SHADOW"
    if adopt.get("decision") == "ADOPT" and shadow_id == "pbv2_flat_band_shadow":
        return "PROMOTED_TO_MAINLINE"
    if str(reg.get("status")) == "disabled":
        if adopt.get("decision") == "REMOVE":
            return "REMOVED"
        # stale registry disabled but still logging → ACTIVE if recently enabled in hints
        return "DISABLED"
    if adopt.get("decision") == "KEEP":
        return "ACTIVE_SHADOW"
    if adopt.get("decision") == "REMOVE":
        return "REMOVED"
    return "ACTIVE_SHADOW"


def build_inventory(sessions_meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reg_rows: list[dict[str, str]] = []
    if REG652.is_file():
        with REG652.open(encoding="utf-8", newline="") as fh:
            reg_rows = list(csv.DictReader(fh))
    adopt = load_phase668()
    by_id: dict[str, dict[str, Any]] = {}
    for r in reg_rows:
        sid = str(r.get("shadow_id"))
        by_id[sid] = {
            "shadow_id": sid,
            "name": r.get("name"),
            "first_phase": r.get("phase"),
            "purpose": r.get("recommended_next_action") or r.get("mainline_effect") or "",
            "kind": classify_kind(str(r.get("entry_or_exit")), str(r.get("category"))),
            "category_raw": r.get("category"),
            "entry_or_exit": r.get("entry_or_exit"),
            "runtime_or_research": r.get("runtime_or_research"),
            "registry_status": r.get("status"),
            "registry_decision": r.get("decision"),
            "phase668_decision": (adopt.get(sid) or {}).get("decision", ""),
            "phase668_rationale": (adopt.get(sid) or {}).get("rationale", ""),
        }
    for ex in EXTRA_INVENTORY:
        sid = ex["shadow_id"]
        if sid not in by_id:
            by_id[sid] = {
                "shadow_id": sid,
                "name": ex["name"],
                "first_phase": ex["phase"],
                "purpose": ex["purpose"],
                "kind": ex["category"],
                "category_raw": ex["category"],
                "entry_or_exit": ex["entry_or_exit"],
                "runtime_or_research": "runtime",
                "registry_status": "",
                "registry_decision": "",
                "phase668_decision": (adopt.get(sid) or {}).get("decision", ""),
                "phase668_rationale": (adopt.get(sid) or {}).get("rationale", ""),
            }
        else:
            by_id[sid]["purpose"] = by_id[sid].get("purpose") or ex["purpose"]

    # yaml/mainline hints from latest valid session
    yaml_hints: dict[str, Any] = {}
    for m in reversed(sessions_meta):
        if m.get("session_validity") == "VALID_SESSION":
            yaml_hints = m.get("summary") or {}
            break

    rows = []
    for sid, base in sorted(by_id.items()):
        st = classify_status(
            sid,
            {
                "status": base.get("registry_status"),
                "enabled": "",
                "runtime_or_research": base.get("runtime_or_research"),
            },
            adopt.get(sid) or {},
            yaml_hints,
        )
        seen_enabled = any(
            (m.get("shadow_daily") or {}).get(sid, {}).get("enabled") for m in sessions_meta
        )
        if st == "DISABLED" and seen_enabled:
            st = "ACTIVE_SHADOW"
        # Known active ops shadows (user taxonomy)
        if sid in (
            "pullback_misread_guard_shadow",
            "flat_weak_range_shadow",
            "imbalance_shadow",
            "board_dynamic_trailing_shadow",
            "readiness_precision_shadow",
            "readiness_economics_shadow",
        ) and st not in ("REMOVED", "PROMOTED_TO_MAINLINE", "RESEARCH_ONLY"):
            st = "ACTIVE_SHADOW"
        if sid == "pbv2_flat_band_shadow" and yaml_hints.get("pbv2_flat_band_mainline_enabled"):
            st = "PROMOTED_TO_MAINLINE"
        if sid == "pbv2_rise5_shadow" and (adopt.get(sid) or {}).get("decision") == "REMOVE":
            st = "REMOVED"
        if sid.startswith("exit_shadow_monitor"):
            st = "REMOVED"
        if sid == "readiness_refined_h_shadow":
            st = "RESEARCH_ONLY"
        rows.append({**base, "status": st})
    return rows


def extract_nested(summary: Mapping[str, Any], nested_key: str) -> dict[str, Any]:
    sub = summary.get(nested_key)
    if not isinstance(sub, dict):
        return {
            "enabled": bool(summary.get(f"{nested_key}_enabled")),
            "blocked": None,
            "delta": None,
            "bw": None,
            "bl": None,
            "shadow_pnl": None,
        }
    return {
        "enabled": bool(sub.get("enabled", summary.get(f"{nested_key}_enabled"))),
        "blocked": _f(sub.get("block_count")),
        "accepted": None,
        "target": _f(sub.get("evaluable_count")),
        "delta": _f(sub.get("delta_yen")),
        "bw": _f(sub.get("blocked_winners")),
        "bl": _f(sub.get("blocked_losers")),
        "shadow_pnl": _f(sub.get("counterfactual_total_pnl_yen") or sub.get("blocked_pnl_yen_100")),
        "stop": _f(sub.get("blocked_stop_hit") or sub.get("blocked_early_stop")),
    }


def extract_shadow_day(summary: Mapping[str, Any], shadow_id: str) -> dict[str, Any]:
    spec = SUMMARY_SPECS.get(shadow_id)
    if not spec:
        return {}
    if spec.get("nested"):
        row = extract_nested(summary, str(spec["nested"]))
        row["shadow_id"] = shadow_id
        return row
    enabled_key = spec.get("enabled")
    parent = spec.get("parent_enabled")
    enabled = None
    if enabled_key:
        enabled = bool(summary.get(enabled_key))
    if parent is not None:
        enabled = bool(summary.get(parent)) and (enabled if enabled is not None else True)
    delta = _f(summary.get(spec["delta"])) if spec.get("delta") else None
    if delta is None and spec.get("alt_delta"):
        delta = _f(summary.get(spec["alt_delta"]))
    return {
        "shadow_id": shadow_id,
        "enabled": enabled,
        "blocked": _f(summary.get(spec["blocked"])) if spec.get("blocked") else None,
        "accepted": _f(summary.get(spec["accepted"])) if spec.get("accepted") else None,
        "target": _f(summary.get(spec["target"])) if spec.get("target") else None,
        "delta": delta,
        "bw": _f(summary.get(spec["bw"])) if spec.get("bw") else None,
        "bl": _f(summary.get(spec["bl"])) if spec.get("bl") else None,
        "shadow_pnl": _f(summary.get(spec["shadow_pnl"])) if spec.get("shadow_pnl") else None,
        "actual_pnl": _f(summary.get(spec["actual_pnl"])) if spec.get("actual_pnl") else None,
        "pf": _f(summary.get(spec["pf"])) if spec.get("pf") else None,
        "stop": _f(summary.get(spec["stop"])) if spec.get("stop") else None,
        "trailing": _f(summary.get(spec["trailing"])) if spec.get("trailing") else None,
        "np_red": _f(summary.get(spec["np_red"])) if spec.get("np_red") else None,
        "stop_red": _f(summary.get(spec["stop_red"])) if spec.get("stop_red") else None,
    }


@dataclass
class TradeLeg:
    day: str
    session: str
    symbol: str
    entry_time: str
    exit_time: str
    entry_epoch: float
    exit_epoch: float
    pnl_yen: float
    exit_reason: str
    hold_sec: float
    blocks: dict[str, bool] = field(default_factory=dict)
    shadow_exit_delta: Optional[float] = None


def _parse_ts(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt
    except Exception:
        return None


def load_session_trades(day: str, sess: Path) -> list[TradeLeg]:
    path = sess / "small_paper_events.csv"
    if not path.is_file():
        # fallback jsonl
        path_j = sess / "small_paper_events.jsonl"
        if not path_j.is_file():
            return []
        acc: list[dict[str, Any]] = []
        exits: list[dict[str, Any]] = []
        with path_j.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                o = json.loads(line)
                t = o.get("event_type")
                if t == "accepted":
                    acc.append(o)
                elif t == "observer_exit":
                    exits.append(o)
        return _pair_trades(day, sess.name, acc, exits)

    acc = []
    exits = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            t = row.get("event_type") or row.get("event")
            if t == "accepted":
                acc.append(row)
            elif t == "observer_exit":
                exits.append(row)
    return _pair_trades(day, sess.name, acc, exits)


def _pair_trades(day: str, session: str, acc: list[dict], exits: list[dict]) -> list[TradeLeg]:
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for a in sorted(acc, key=lambda x: str(x.get("entry_time") or x.get("event_time") or "")):
        by_sym[str(a.get("symbol"))].append(a)
    exit_q: dict[str, list[dict]] = defaultdict(list)
    for e in sorted(exits, key=lambda x: str(x.get("exit_time") or x.get("event_time") or "")):
        exit_q[str(e.get("symbol"))].append(e)
    legs: list[TradeLeg] = []
    for sym, entries in by_sym.items():
        outs = exit_q.get(sym, [])
        for i, a in enumerate(entries):
            e = outs[i] if i < len(outs) else {}
            et = str(a.get("entry_time") or a.get("event_time") or "")
            xt = str(e.get("exit_time") or e.get("event_time") or "")
            tea, txa = _parse_ts(et), _parse_ts(xt)
            pnl = _f(e.get("pnl_pct") or e.get("realized_pnl_pct")) or 0.0
            # yen_100 convention
            pnl_yen = pnl * 100.0
            hold = (txa - tea).total_seconds() if tea and txa else _f(e.get("hold_duration_sec")) or 0.0
            blocks = {}
            for sid, col in EVENT_BLOCK_SHADOWS.items():
                # block flag may be on accept or exit row
                blocks[sid] = _truthy(a.get(col)) or _truthy(e.get(col))
            # imbalance: treat candidate tier as optional block when tier high — skip hard block
            delta = _f(e.get("actual_vs_shadow_delta_yen"))
            legs.append(
                TradeLeg(
                    day=day,
                    session=session,
                    symbol=sym,
                    entry_time=et,
                    exit_time=xt,
                    entry_epoch=tea.timestamp() if tea else 0.0,
                    exit_epoch=txa.timestamp() if txa else 0.0,
                    pnl_yen=pnl_yen,
                    exit_reason=str(e.get("exit_reason") or ""),
                    hold_sec=float(hold or 0.0),
                    blocks=blocks,
                    shadow_exit_delta=delta,
                )
            )
    return legs


def portfolio_replay(legs: Sequence[TradeLeg], blocked_keys: set[tuple[str, str, str]]) -> dict[str, Any]:
    ordered = sorted(legs, key=lambda t: t.entry_epoch)
    kept: list[TradeLeg] = []
    open_pos: list[TradeLeg] = []
    for leg in ordered:
        key = (leg.day, leg.symbol, leg.entry_time)
        open_pos = [o for o in open_pos if o.exit_epoch > leg.entry_epoch]
        if key in blocked_keys:
            continue
        if len(open_pos) >= MAX_CONCURRENT:
            continue
        kept.append(leg)
        open_pos.append(leg)
    pnls = [k.pnl_yen for k in kept]
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for k in kept if k.exit_reason == "stop_hit")
    nps = sum(1 for k in kept if k.exit_reason == "no_progress_exit")
    equity = peak = mdd = 0.0
    for k in sorted(kept, key=lambda x: x.exit_epoch):
        equity += k.pnl_yen
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    return {
        "n": len(kept),
        "pnl_yen_100": round(sum(pnls), 4),
        "PF": _pf(pnls),
        "win_rate": round(wins / len(kept), 4) if kept else 0.0,
        "stop_n": stops,
        "np_n": nps,
        "max_drawdown": round(mdd, 4),
        "avg_hold_sec": round(sum(k.hold_sec for k in kept) / len(kept), 2) if kept else float("nan"),
    }


def stability_from_daily(deltas: list[tuple[str, float]]) -> dict[str, Any]:
    """deltas: sorted (date, delta_yen)."""
    if not deltas:
        return {
            "improve_days": 0,
            "worsen_days": 0,
            "improve_rate": None,
            "max_improve_streak": 0,
            "max_worsen_streak": 0,
            "rolling5_mean_delta": None,
            "rolling10_mean_delta": None,
        }
    vals = [d for _, d in deltas if d == d]
    improve = sum(1 for d in vals if d > 0)
    worsen = sum(1 for d in vals if d < 0)
    # streaks
    max_i = max_w = cur_i = cur_w = 0
    for d in vals:
        if d > 0:
            cur_i += 1
            cur_w = 0
        elif d < 0:
            cur_w += 1
            cur_i = 0
        else:
            cur_i = cur_w = 0
        max_i = max(max_i, cur_i)
        max_w = max(max_w, cur_w)

    def roll(n: int) -> Optional[float]:
        if len(vals) < n:
            return round(sum(vals) / len(vals), 4) if vals else None
        window = vals[-n:]
        return round(sum(window) / len(window), 4)

    return {
        "improve_days": improve,
        "worsen_days": worsen,
        "improve_rate": round(improve / len(vals), 4) if vals else None,
        "max_improve_streak": max_i,
        "max_worsen_streak": max_w,
        "rolling5_mean_delta": roll(5),
        "rolling10_mean_delta": roll(10),
        "n_days_with_signal": len(vals),
    }


def decision_for(shadow_id: str, status: str, total: dict[str, Any], stab: dict[str, Any], replay: dict[str, Any]) -> tuple[str, str]:
    """Return (decision, reason). No adoption this phase."""
    if status == "PROMOTED_TO_MAINLINE":
        return "PROMOTE", "Already promoted / mainline equivalent; keep monitoring only"
    if status == "REMOVED":
        return "REMOVE", "Previously retired (phase668/YAML); do not re-enable"
    if status == "RESEARCH_ONLY":
        return "RESEARCH_ONLY", "Research-only shadow; not for Discord ops adoption"
    cum_delta = _f(total.get("cumulative_delta_yen")) or 0.0
    improve_rate = _f(stab.get("improve_rate")) or 0.0
    n_days = int(stab.get("n_days_with_signal") or 0)
    single_day_dom = bool(total.get("single_day_dominant"))
    single_sym_dom = bool(total.get("single_symbol_dominant"))
    replay_delta = _f(replay.get("delta_pnl")) or 0.0

    if status == "DISABLED" and cum_delta <= 0:
        return "REMOVE", "Disabled and non-positive cumulative delta"
    if n_days >= 5 and improve_rate >= 0.6 and cum_delta > 0 and not single_day_dom and not single_sym_dom:
        if replay_delta > 0 or cum_delta > 5000:
            return "KEEP_SHADOW", "Multi-day positive tendency; continue monitoring (no promote this phase)"
        return "KEEP_SHADOW", "Mixed but enough positive days; keep shadow"
    if n_days >= 5 and (improve_rate <= 0.35 or cum_delta < -10000) and not single_day_dom:
        return "REMOVE", "Multi-day negative / low improve-rate — retirement candidate (not applied)"
    if single_day_dom or single_sym_dom:
        return "KEEP_SHADOW", "Signal concentrated in 1 day/symbol — insufficient for promote/remove"
    return "KEEP_SHADOW", "Insufficient multi-day evidence; keep observing"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    sessions = iter_sessions()
    session_metas: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    all_trades: list[TradeLeg] = []

    # Prefer one session per day: VALID_SESSION+SEALED_VALID, else largest push
    by_day: dict[str, list[Path]] = defaultdict(list)
    for day, sess in sessions:
        by_day[day].append(sess)

    selected: list[tuple[str, Path, dict[str, Any]]] = []
    for day, sess_list in sorted(by_day.items()):
        scored = []
        for sess in sess_list:
            sm = json.loads((sess / "small_paper_summary.json").read_text(encoding="utf-8"))
            seal = {}
            if (sess / "session_seal.json").is_file():
                try:
                    seal = json.loads((sess / "session_seal.json").read_text(encoding="utf-8"))
                except Exception:
                    pass
            score = 0
            if sm.get("session_validity") == "VALID_SESSION":
                score += 100
            if seal.get("session_seal_status") == "SEALED_VALID":
                score += 50
            score += min(int(sm.get("push_messages") or 0), 10_000_000) / 1e6
            scored.append((score, sess, sm, seal))
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0]
        selected.append((day, best[1], best[2]))

    for day, sess, sm in selected:
        shadow_daily = {}
        for sid in list(SUMMARY_SPECS.keys()):
            row = extract_shadow_day(sm, sid)
            if row:
                shadow_daily[sid] = row
                daily_rows.append(
                    {
                        "date": day,
                        "session": sess.name,
                        "session_validity": sm.get("session_validity"),
                        "accepted_count": sm.get("accepted_count"),
                        "canonical_pnl": (sm.get("canonical_summary") or {}).get("total_pnl_yen_100"),
                        **{f"d_{k}": v for k, v in row.items() if k != "shadow_id"},
                        "shadow_id": sid,
                        "shadow_trades": row.get("accepted") if row.get("accepted") is not None else row.get("target"),
                        "blocked": row.get("blocked"),
                        "accepted": row.get("accepted"),
                        "winners": row.get("bw"),
                        "losers": row.get("bl"),
                        "pnl_yen_100": row.get("shadow_pnl"),
                        "pnl_pct": None,
                        "PF": row.get("pf"),
                        "win_rate": None,
                        "STOP": row.get("stop"),
                        "no_progress": row.get("np_red"),
                        "trailing": row.get("trailing"),
                        "average_hold": None,
                        "delta_yen": row.get("delta"),
                        "enabled": row.get("enabled"),
                    }
                )
        session_metas.append(
            {
                "day": day,
                "session": sess.name,
                "session_validity": sm.get("session_validity"),
                "summary": sm,
                "shadow_daily": shadow_daily,
            }
        )
        # trades for CAP (skip INVALID with 0 push to save time — still load if accepted>0)
        if int(sm.get("accepted_count") or 0) > 0 or int(sm.get("push_messages") or 0) > 1000:
            all_trades.extend(load_session_trades(day, sess))

    inventory = build_inventory(session_metas)

    # Totals + stability + consistency
    total_rows = []
    consistency_rows = []
    ranking_source = []
    for inv in inventory:
        sid = inv["shadow_id"]
        days = [r for r in daily_rows if r["shadow_id"] == sid]
        # use days with any non-null metric
        active_days = [
            r
            for r in days
            if r.get("delta_yen") is not None
            or r.get("blocked")
            or r.get("accepted")
            or r.get("pnl_yen_100") is not None
            or r.get("enabled")
        ]
        deltas = [(r["date"], float(r["delta_yen"])) for r in active_days if r.get("delta_yen") is not None]
        stab = stability_from_daily(sorted(deltas, key=lambda x: x[0]))
        cum_delta = sum(d for _, d in deltas) if deltas else 0.0
        total_blocked = sum(float(r["blocked"] or 0) for r in active_days)
        total_accepted = sum(float(r["accepted"] or 0) for r in active_days if r.get("accepted") is not None)
        # single-day / single-symbol dominance for delta
        single_day_dom = False
        if deltas and abs(cum_delta) > 1e-9:
            top = max(abs(d) for _, d in deltas)
            single_day_dom = (top / abs(cum_delta)) >= 0.85 and len(deltas) >= 2
        elif deltas and len(deltas) == 1:
            single_day_dom = True

        # symbol concentration from trade blocks if available
        single_sym_dom = False
        if sid in EVENT_BLOCK_SHADOWS:
            blocked_legs = [t for t in all_trades if t.blocks.get(sid)]
            if blocked_legs:
                by_sym: dict[str, float] = defaultdict(float)
                for t in blocked_legs:
                    by_sym[t.symbol] += abs(t.pnl_yen)
                tot = sum(by_sym.values()) or 1.0
                single_sym_dom = (max(by_sym.values()) / tot) >= 0.7

        total = {
            "shadow_id": sid,
            "name": inv.get("name"),
            "status": inv.get("status"),
            "n_days_present": len(active_days),
            "total_trades": total_accepted,
            "total_blocked": total_blocked,
            "cumulative_delta_yen": round(cum_delta, 4),
            "cumulative_shadow_pnl": round(
                sum(float(r["pnl_yen_100"]) for r in active_days if r.get("pnl_yen_100") is not None), 4
            ),
            "avg_daily_delta": round(cum_delta / len(deltas), 4) if deltas else None,
            "sum_blocked_winners": sum(float(r["winners"] or 0) for r in active_days),
            "sum_blocked_losers": sum(float(r["losers"] or 0) for r in active_days),
            "single_day_dominant": single_day_dom,
            "single_symbol_dominant": single_sym_dom,
            **{f"stab_{k}": v for k, v in stab.items()},
        }
        total_rows.append(total)
        consistency_rows.append(
            {
                "shadow_id": sid,
                "n_days_with_delta": len(deltas),
                "improve_days": stab["improve_days"],
                "worsen_days": stab["worsen_days"],
                "improve_rate": stab["improve_rate"],
                "max_improve_streak": stab["max_improve_streak"],
                "max_worsen_streak": stab["max_worsen_streak"],
                "rolling5_mean_delta": stab["rolling5_mean_delta"],
                "rolling10_mean_delta": stab["rolling10_mean_delta"],
                "single_day_dominant": single_day_dom,
                "single_symbol_dominant": single_sym_dom,
                "multi_day_consistent": bool(
                    len(deltas) >= 3 and (stab["improve_rate"] or 0) >= 0.6 and not single_day_dom and not single_sym_dom
                ),
            }
        )
        ranking_source.append(total)

    # CAP portfolio replay
    baseline = portfolio_replay(all_trades, set())
    replay_rows = []
    for sid, col in EVENT_BLOCK_SHADOWS.items():
        blocked_keys = {(t.day, t.symbol, t.entry_time) for t in all_trades if t.blocks.get(sid)}
        blocked_legs = [t for t in all_trades if t.blocks.get(sid)]
        port = portfolio_replay(all_trades, blocked_keys)
        bw = sum(1 for t in blocked_legs if t.pnl_yen > 0)
        bl = sum(1 for t in blocked_legs if t.pnl_yen <= 0)
        replay_rows.append(
            {
                "shadow_id": sid,
                "method": "cap_portfolio_replay_entry_block",
                "event_flag": col,
                "blocked_n": len(blocked_keys),
                "blocked_winners": bw,
                "blocked_losers": bl,
                "baseline_n": baseline["n"],
                "baseline_pnl": baseline["pnl_yen_100"],
                "baseline_PF": baseline["PF"],
                "baseline_MDD": baseline["max_drawdown"],
                "baseline_stop": baseline["stop_n"],
                "baseline_np": baseline["np_n"],
                "shadow_n": port["n"],
                "shadow_pnl": port["pnl_yen_100"],
                "shadow_PF": port["PF"],
                "shadow_MDD": port["max_drawdown"],
                "shadow_stop": port["stop_n"],
                "shadow_np": port["np_n"],
                "delta_pnl": round(port["pnl_yen_100"] - baseline["pnl_yen_100"], 4),
                "delta_PF": round(port["PF"] - baseline["PF"], 4),
                "delta_MDD": round(port["max_drawdown"] - baseline["max_drawdown"], 4),
                "delta_STOP": port["stop_n"] - baseline["stop_n"],
                "delta_no_progress": port["np_n"] - baseline["np_n"],
            }
        )
    # board dynamic exit overlay: sum actual_vs_shadow_delta
    bd_delta = sum((t.shadow_exit_delta or 0.0) for t in all_trades if t.shadow_exit_delta is not None)
    bd_n = sum(1 for t in all_trades if t.shadow_exit_delta is not None)
    replay_rows.append(
        {
            "shadow_id": "board_dynamic_trailing_shadow",
            "method": "exit_overlay_delta_sum",
            "event_flag": "actual_vs_shadow_delta_yen",
            "blocked_n": bd_n,
            "delta_pnl": round(bd_delta, 4),
            "note": "Sum of per-trade actual_vs_shadow_delta_yen (not entry CAP removal)",
            "baseline_pnl": baseline["pnl_yen_100"],
            "shadow_pnl": round(baseline["pnl_yen_100"] + bd_delta, 4),
        }
    )
    replay_by_id = {r["shadow_id"]: r for r in replay_rows}

    # Decisions
    status_matrix = []
    decisions = []
    for inv in inventory:
        sid = inv["shadow_id"]
        tot = next((t for t in total_rows if t["shadow_id"] == sid), {})
        stab = next((c for c in consistency_rows if c["shadow_id"] == sid), {})
        rep = replay_by_id.get(sid, {})
        dec, reason = decision_for(sid, str(inv.get("status")), tot, stab, rep)
        # Force known statuses
        if inv["status"] == "PROMOTED_TO_MAINLINE":
            dec = "PROMOTE"
        if inv["status"] == "REMOVED":
            dec = "REMOVE"
        if inv["status"] == "RESEARCH_ONLY":
            dec = "RESEARCH_ONLY"
        inv["recommendation"] = dec
        inv["recommendation_reason"] = reason
        status_matrix.append(
            {
                "shadow_id": sid,
                "name": inv.get("name"),
                "first_phase": inv.get("first_phase"),
                "kind": inv.get("kind"),
                "status": inv.get("status"),
                "recommendation": dec,
                "phase668_decision": inv.get("phase668_decision"),
                "cumulative_delta_yen": tot.get("cumulative_delta_yen"),
                "cap_replay_delta_pnl": rep.get("delta_pnl"),
                "improve_rate": stab.get("improve_rate"),
                "multi_day_consistent": stab.get("multi_day_consistent"),
                "reason": reason,
            }
        )
        decisions.append({"shadow_id": sid, "decision": dec, "reason": reason, "status": inv.get("status")})

    # Rankings
    def topn(key: str, n: int = 10, reverse: bool = True) -> list[dict[str, Any]]:
        rows = [r for r in ranking_source if r.get(key) is not None]
        rows = sorted(rows, key=lambda x: float(x.get(key) or 0), reverse=reverse)[:n]
        return [{"rank": i + 1, "metric": key, **r} for i, r in enumerate(rows)]

    # Use CAP replay delta when available for improve ranking
    for t in ranking_source:
        sid = t["shadow_id"]
        if sid in replay_by_id and replay_by_id[sid].get("delta_pnl") is not None:
            t["cap_delta_pnl"] = replay_by_id[sid]["delta_pnl"]
            t["cap_delta_PF"] = replay_by_id[sid].get("delta_PF")
            t["cap_delta_STOP"] = replay_by_id[sid].get("delta_STOP")
            t["cap_delta_np"] = replay_by_id[sid].get("delta_no_progress")
        else:
            t["cap_delta_pnl"] = t.get("cumulative_delta_yen")
            t["cap_delta_PF"] = None
            t["cap_delta_STOP"] = None
            t["cap_delta_np"] = None

    rankings = []
    rankings += topn("cap_delta_pnl", 10, True)
    rankings += topn("cap_delta_pnl", 10, False)  # worst — tag later
    # mark worst
    for i, r in enumerate(rankings):
        if i >= 10:
            r["list"] = "WORST10_delta"
            r["rank"] = i - 9
        else:
            r["list"] = "TOP10_delta"
    # PF / STOP / NP from replay where present
    for metric, list_name, rev in (
        ("cap_delta_PF", "TOP10_PF", True),
        ("cap_delta_STOP", "TOP10_STOP_reduction", False),  # more negative stop_n better
        ("cap_delta_np", "TOP10_NP_reduction", False),
    ):
        rows = [r for r in ranking_source if r.get(metric) is not None]
        rows = sorted(rows, key=lambda x: float(x.get(metric) or 0), reverse=rev)[:10]
        for i, r in enumerate(rows):
            rankings.append({"rank": i + 1, "list": list_name, "metric": metric, **r})

    # Write artifacts
    _wc(OUT / "shadow_inventory.csv", inventory)
    _wc(OUT / "shadow_daily_summary.csv", daily_rows)
    _wc(OUT / "shadow_total_summary.csv", total_rows)
    _wc(OUT / "shadow_portfolio_replay.csv", replay_rows)
    _wc(OUT / "shadow_rankings.csv", rankings)
    _wc(OUT / "shadow_consistency.csv", consistency_rows)
    _wc(OUT / "shadow_status_matrix.csv", status_matrix)

    def _tot(sid: str) -> dict[str, Any]:
        return next((t for t in total_rows if t["shadow_id"] == sid), {})

    def _rep(sid: str) -> dict[str, Any]:
        return replay_by_id.get(sid, {})

    n_shadow = len(inventory)
    n_active = sum(1 for i in inventory if i["status"] == "ACTIVE_SHADOW")
    n_promoted = sum(1 for i in inventory if i["status"] == "PROMOTED_TO_MAINLINE")
    n_removed = sum(1 for i in inventory if i["status"] == "REMOVED")
    ranked_nonzero = [r for r in ranking_source if abs(float(r.get("cap_delta_pnl") or 0)) > 1e-9]
    ranked_pool = ranked_nonzero or ranking_source
    top_improve = sorted(ranked_pool, key=lambda x: float(x.get("cap_delta_pnl") or 0), reverse=True)[:10]
    top_worsen = sorted(ranked_pool, key=lambda x: float(x.get("cap_delta_pnl") or 0))[:10]
    keep = [d["shadow_id"] for d in decisions if d["decision"] == "KEEP_SHADOW"]
    remove_cand = [d["shadow_id"] for d in decisions if d["decision"] == "REMOVE" and d["status"] != "REMOVED"]
    promote_cand = [
        d["shadow_id"]
        for d in decisions
        if d["decision"] in ("PROMOTE", "KEEP_SHADOW")
        and d["status"] == "ACTIVE_SHADOW"
        and (_rep(d["shadow_id"]).get("delta_pnl") or 0) > 0
        and next((c for c in consistency_rows if c["shadow_id"] == d["shadow_id"]), {}).get("multi_day_consistent")
    ]

    # Verdict
    verdicts = ["SHADOW_HISTORY_CONSOLIDATED"]
    if promote_cand:
        verdicts.append("PROMOTION_CANDIDATES_FOUND")
    else:
        verdicts.append("NO_PROMOTION_CANDIDATE")
    if remove_cand or n_removed:
        verdicts.append("SHADOW_RETIREMENT_CANDIDATES_FOUND")
    primary = "SHADOW_HISTORY_CONSOLIDATED"

    answers = {
        "1_shadow_total": n_shadow,
        "2_active": n_active,
        "3_mainline_promoted": n_promoted,
        "4_removed": n_removed,
        "5_top10_improve": [{"shadow_id": r["shadow_id"], "delta": r.get("cap_delta_pnl")} for r in top_improve],
        "6_top10_worsen": [{"shadow_id": r["shadow_id"], "delta": r.get("cap_delta_pnl")} for r in top_worsen],
        "7_pullback_misread": {"total": _tot("pullback_misread_guard_shadow"), "cap_replay": _rep("pullback_misread_guard_shadow")},
        "8_flat_weak_range": {"total": _tot("flat_weak_range_shadow"), "cap_replay": _rep("flat_weak_range_shadow")},
        "9_imbalance": {"total": _tot("imbalance_shadow"), "cap_replay": _rep("imbalance_shadow")},
        "10_board_exit": {"total": _tot("board_dynamic_trailing_shadow"), "cap_replay": _rep("board_dynamic_trailing_shadow")},
        "11_readiness": {
            "precision": {"total": _tot("readiness_precision_shadow"), "cap_replay": _rep("readiness_precision_shadow")},
            "economics": {"total": _tot("readiness_economics_shadow"), "cap_replay": _rep("readiness_economics_shadow")},
            "refined_h": {"total": _tot("readiness_refined_h_shadow")},
        },
        "12_continue_monitor": keep,
        "13_retirement_candidates": sorted(set(remove_cand + [i["shadow_id"] for i in inventory if i["status"] == "REMOVED"])),
        "14_mainline_candidates": promote_cand,
        "15_mainline_unchanged": True,
        "16_submit_cancel": {"submit": 0, "cancel": 0},
    }

    report = {
        "phase": "687W40",
        "verdict": primary,
        "verdict_tags": verdicts,
        "n_trading_days_scanned": len(selected),
        "n_trades_loaded": len(all_trades),
        "baseline_portfolio": baseline,
        "answers": answers,
        "note": "No Shadow promotion/removal applied this phase; recommendations are research labels only.",
        "generated_at": datetime.now(JST).isoformat(),
    }
    _wj(OUT / "phase687w40_report.json", report)

    # Decision markdown
    lines = [
        "# Phase687W40 Shadow History Consolidation",
        "",
        f"## Verdict: `{primary}`",
        f"Tags: {', '.join(verdicts)}",
        "",
        "### Required answers",
        f"1. Shadow total: **{n_shadow}**",
        f"2. Active: **{n_active}**",
        f"3. Mainline promoted: **{n_promoted}**",
        f"4. Removed: **{n_removed}**",
        f"5. TOP10 improve: `{json.dumps(answers['5_top10_improve'], ensure_ascii=False)}`",
        f"6. TOP10 worsen: `{json.dumps(answers['6_top10_worsen'], ensure_ascii=False)}`",
        f"7. Pullback Misread: cum_delta={_tot('pullback_misread_guard_shadow').get('cumulative_delta_yen')} CAPΔ={_rep('pullback_misread_guard_shadow').get('delta_pnl')}",
        f"8. Flat Weak Range: cum_delta={_tot('flat_weak_range_shadow').get('cumulative_delta_yen')} CAPΔ={_rep('flat_weak_range_shadow').get('delta_pnl')}",
        f"9. Imbalance: cum_delta={_tot('imbalance_shadow').get('cumulative_delta_yen')} (summary PF path; no entry-block CAP)",
        f"10. Board Exit: cum_delta={_tot('board_dynamic_trailing_shadow').get('cumulative_delta_yen')} overlayΔ={_rep('board_dynamic_trailing_shadow').get('delta_pnl')}",
        f"11. Readiness: precision CAPΔ={_rep('readiness_precision_shadow').get('delta_pnl')} economics CAPΔ={_rep('readiness_economics_shadow').get('delta_pnl')}",
        f"12. Continue monitor: {keep}",
        f"13. Retirement candidates: {answers['13_retirement_candidates']}",
        f"14. Mainline candidates (research only, not adopted): {promote_cand}",
        "15. MAINLINE unchanged: **True**",
        "16. submit/cancel: **0/0**",
        "",
        "### Per-shadow recommendation (not applied)",
    ]
    for m in status_matrix:
        lines.append(
            f"- `{m['shadow_id']}` status={m['status']} → **{m['recommendation']}** — {m['reason']}"
        )
    lines += [
        "",
        "### Method",
        f"- Days selected (1 session/day preference VALID+SEALED): {len(selected)}",
        f"- Trades loaded for CAP replay: {len(all_trades)}",
        f"- Baseline CAP portfolio: n={baseline['n']} pnl={baseline['pnl_yen_100']} PF={baseline['PF']}",
        "- Inventory seeded from phase652 registry + extras; status reconciled with phase668 + recent summaries",
        "- This phase does **not** promote or remove any Shadow",
    ]
    _wm(OUT / "shadow_decision.md", "\n".join(lines) + "\n")

    # Also copy decision name expected
    _wm(OUT / "phase687w40_decision.md", "\n".join(lines) + "\n")

    print(json.dumps({"verdict": primary, "answers": answers}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
