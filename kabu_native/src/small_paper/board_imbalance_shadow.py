"""
Phase214: Board imbalance shadow logging (shadow only; no hard reject).

Flags accepts matching Phase213b D pattern: low_liq pass + vwap pass + top-tier imbalance.
Fixed tier cutoffs from Phase213b review — not tuned per session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from screening.morning_screen import calc_board_imbalance
from small_paper.vwap_shadow_reject import VWAP_SHADOW_REJECT_MIN

TV_MIN = 1e8
VWAP_DEV_MAX = VWAP_SHADOW_REJECT_MIN  # 2.5 — exclude vwap_shadow_reject candidates

# Phase213b fixed imbalance cutoffs (top N% of cohort)
IMBALANCE_TIER_CUTOFFS: dict[str, float] = {
    "10%": 0.612652,
    "20%": 0.560790,
    "30%": 0.533987,
}
PRIMARY_TIER = "20%"

SHADOW_FIELD_KEYS = (
    "entry_order_book_imbalance",
    "entry_imbalance_percentile",
    "imbalance_shadow_candidate",
    "imbalance_shadow_tier",
)

SUMMARY_FIELD_KEYS = (
    "imbalance_shadow_count",
    "imbalance_shadow_pf",
    "imbalance_shadow_total_pnl",
    "imbalance_shadow_stop_hit_count",
    "imbalance_shadow_trailing_mfe_count",
)

TIER_SUMMARY_PREFIX = "imbalance_shadow_t"


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def session_imbalance_percentile(
    prior_samples: Sequence[float],
    value: float,
) -> Optional[float]:
    """Session-local rank among accepts seen before this one (0–100)."""
    if not prior_samples:
        return None
    le = sum(1 for s in prior_samples if s <= value)
    return round(100.0 * le / len(prior_samples), 2)


def _passes_entry_guards(trade: Mapping[str, Any]) -> bool:
    tv = _float(trade.get("trading_value"))
    if tv is None or tv < TV_MIN:
        return False
    dev = _float(trade.get("entry_vwap_dev_pct"))
    if dev is not None and dev >= VWAP_DEV_MAX:
        return False
    return True


def highest_imbalance_tier(imbalance: Optional[float]) -> str:
    if imbalance is None:
        return ""
    for tier in ("10%", "20%", "30%"):
        if imbalance >= IMBALANCE_TIER_CUTOFFS[tier]:
            return tier
    return ""


def is_imbalance_shadow_candidate(
    trade: Mapping[str, Any],
    *,
    tier: str = PRIMARY_TIER,
) -> bool:
    if not _passes_entry_guards(trade):
        return False
    imb = _float(trade.get("entry_order_book_imbalance"))
    if imb is None:
        return False
    cutoff = IMBALANCE_TIER_CUTOFFS.get(tier, IMBALANCE_TIER_CUTOFFS[PRIMARY_TIER])
    return imb >= cutoff


def compute_board_imbalance_shadow_fields(
    *,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    session_imbalance_samples: list[float],
) -> dict[str, Any]:
    """Shadow-only board imbalance fields at accept (does not block entry)."""
    imbalance = calc_board_imbalance(payload)
    percentile: Optional[float] = None
    if imbalance is not None:
        percentile = session_imbalance_percentile(session_imbalance_samples, float(imbalance))
        session_imbalance_samples.append(float(imbalance))

    tier = ""
    candidate = False
    if imbalance is not None and _passes_entry_guards(trade):
        tier = highest_imbalance_tier(imbalance)
        candidate = tier in ("10%", "20%")  # primary 20% band includes top-10%

    return {
        "entry_order_book_imbalance": round(float(imbalance), 6) if imbalance is not None else None,
        "entry_imbalance_percentile": percentile,
        "imbalance_shadow_candidate": candidate,
        "imbalance_shadow_tier": tier,
    }


def enrich_exit_imbalance_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    pnl_pct: float,
    exit_reason: str,
) -> dict[str, Any]:
    """Merge entry imbalance shadow flags with exit outcome (logging only)."""
    return {
        "entry_order_book_imbalance": entry_shadow.get("entry_order_book_imbalance"),
        "entry_imbalance_percentile": entry_shadow.get("entry_imbalance_percentile"),
        "imbalance_shadow_candidate": bool(entry_shadow.get("imbalance_shadow_candidate")),
        "imbalance_shadow_tier": entry_shadow.get("imbalance_shadow_tier", ""),
        "pnl_pct": round(float(pnl_pct), 4),
        "stop_hit": exit_reason == "stop_hit",
        "trailing_mfe_exit": exit_reason == "trailing_mfe_exit",
    }


def _pf(pnls: Sequence[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return wins / gl


def _tier_metrics(
    rows: Sequence[Mapping[str, Any]],
    pnl_by_key: Mapping[tuple[str, str], float],
    *,
    tier: str,
) -> dict[str, Any]:
    cutoff = IMBALANCE_TIER_CUTOFFS[tier]
    pnls: list[float] = []
    stop_hits = 0
    trailing_mfe = 0
    for row in rows:
        if not _passes_entry_guards(row):
            continue
        imb = _float(row.get("entry_order_book_imbalance"))
        if imb is None or imb < cutoff:
            continue
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        pnl = pnl_by_key.get(key)
        if pnl is None:
            continue
        pnls.append(float(pnl))
        reason = str(row.get("exit_reason") or "")
        if bool(row.get("stop_hit")) or reason == "stop_hit":
            stop_hits += 1
        if bool(row.get("trailing_mfe_exit")) or reason == "trailing_mfe_exit":
            trailing_mfe += 1
    pf = _pf(pnls)
    return {
        "count": len(pnls),
        "pf": round(pf, 4) if pf is not None and pf != float("inf") else pf,
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
        "stop_hit_count": stop_hits,
        "trailing_mfe_count": trailing_mfe,
    }


def pnl_map_from_events(events: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if ev.get("event_type") != "observer_exit":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if not key[1]:
            continue
        out[key] = {
            "pnl_pct": _float(ev.get("pnl_pct")),
            "exit_reason": str(ev.get("exit_reason") or ""),
            "stop_hit": bool(ev.get("stop_hit")),
            "trailing_mfe_exit": bool(ev.get("trailing_mfe_exit")),
        }
    return out


@dataclass
class BoardImbalanceShadowCounters:
    imbalance_shadow_count: int = 0
    imbalance_shadow_total_pnl: float = 0.0
    _candidate_win_pnl: float = 0.0
    _candidate_loss_pnl: float = 0.0
    imbalance_shadow_stop_hit_count: int = 0
    imbalance_shadow_trailing_mfe_count: int = 0
    tier_counts: dict[str, int] = field(default_factory=lambda: {t: 0 for t in IMBALANCE_TIER_CUTOFFS})

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        if fields.get("imbalance_shadow_candidate"):
            self.imbalance_shadow_count += 1
        tier = str(fields.get("imbalance_shadow_tier") or "")
        if tier in self.tier_counts:
            self.tier_counts[tier] += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        if not row.get("imbalance_shadow_candidate"):
            return
        pnl = _float(row.get("pnl_pct")) or 0.0
        self.imbalance_shadow_total_pnl = round(self.imbalance_shadow_total_pnl + pnl, 4)
        if pnl > 0:
            self._candidate_win_pnl = round(self._candidate_win_pnl + pnl, 4)
        elif pnl < 0:
            self._candidate_loss_pnl = round(self._candidate_loss_pnl + pnl, 4)
        reason = str(row.get("exit_reason") or "")
        if bool(row.get("stop_hit")) or reason == "stop_hit":
            self.imbalance_shadow_stop_hit_count += 1
        if bool(row.get("trailing_mfe_exit")) or reason == "trailing_mfe_exit":
            self.imbalance_shadow_trailing_mfe_count += 1

    def summary_fields(self) -> dict[str, Any]:
        gl = abs(self._candidate_loss_pnl)
        if gl <= 0:
            pf: Optional[float] = None if self._candidate_win_pnl <= 0 else float("inf")
        else:
            pf = round(self._candidate_win_pnl / gl, 4)
        out = {
            "imbalance_shadow_enabled": True,
            "imbalance_shadow_count": self.imbalance_shadow_count,
            "imbalance_shadow_total_pnl": self.imbalance_shadow_total_pnl,
            "imbalance_shadow_pf": pf,
            "imbalance_shadow_stop_hit_count": self.imbalance_shadow_stop_hit_count,
            "imbalance_shadow_trailing_mfe_count": self.imbalance_shadow_trailing_mfe_count,
        }
        for tier in ("10%", "20%", "30%"):
            label = tier.replace("%", "")
            out[f"{TIER_SUMMARY_PREFIX}{label}_accept_count"] = self.tier_counts.get(tier, 0)
        return out


def finalize_session_board_imbalance_shadow(
    accepted_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reconcile exit outcomes and return tier summary metrics for small_paper_summary.json."""
    exit_by_key = pnl_map_from_events(events)
    for row in accepted_rows:
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        ex = exit_by_key.get(key)
        if ex:
            if ex.get("pnl_pct") is not None:
                row["pnl_pct"] = ex["pnl_pct"]
            row["exit_reason"] = ex.get("exit_reason", "")
            row["stop_hit"] = ex.get("stop_hit", False)
            row["trailing_mfe_exit"] = ex.get("trailing_mfe_exit", False)

    pnl_by_key = {k: v["pnl_pct"] for k, v in exit_by_key.items() if v.get("pnl_pct") is not None}

    primary = _tier_metrics(accepted_rows, pnl_by_key, tier=PRIMARY_TIER)
    out: dict[str, Any] = {
        "imbalance_shadow_enabled": True,
        "imbalance_shadow_count": primary["count"],
        "imbalance_shadow_pf": primary["pf"],
        "imbalance_shadow_total_pnl": primary["total_pnl"],
        "imbalance_shadow_stop_hit_count": primary["stop_hit_count"],
        "imbalance_shadow_trailing_mfe_count": primary["trailing_mfe_count"],
        "imbalance_shadow_tier_cutoffs": dict(IMBALANCE_TIER_CUTOFFS),
    }
    for tier in ("10%", "20%", "30%"):
        label = tier.replace("%", "")
        m = _tier_metrics(accepted_rows, pnl_by_key, tier=tier)
        out[f"{TIER_SUMMARY_PREFIX}{label}_count"] = m["count"]
        out[f"{TIER_SUMMARY_PREFIX}{label}_pf"] = m["pf"]
        out[f"{TIER_SUMMARY_PREFIX}{label}_total_pnl"] = m["total_pnl"]
        out[f"{TIER_SUMMARY_PREFIX}{label}_stop_hit_count"] = m["stop_hit_count"]
        out[f"{TIER_SUMMARY_PREFIX}{label}_trailing_mfe_count"] = m["trailing_mfe_count"]
    return out
