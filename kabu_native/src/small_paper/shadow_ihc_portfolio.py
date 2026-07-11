"""I/H/C shadow portfolio union tracking (observation only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from research.phase631_profit_source_attribution import _num

BIG_WINNER_YEN = 5000.0


def _bool(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def compute_ihc_shadow_fields(
    *,
    i_block: bool,
    h_block: bool,
    c_block: bool,
) -> dict[str, Any]:
    parts: list[str] = []
    if i_block:
        parts.append("I")
    if h_block:
        parts.append("H")
    if c_block:
        parts.append("C")
    overlap_n = sum((i_block, h_block, c_block))
    return {
        "shadow_union_ihc_block": i_block or h_block or c_block,
        "shadow_overlap_type": "+".join(parts) if parts else "none",
        "ihc_overlap_count": max(0, overlap_n - 1) if overlap_n > 1 else 0,
        "ihc_i_feature_source": "entry_expectancy_score_v2+live_feature_incomplete",
        "ihc_h_feature_source": "readiness_bounce_from_recent_low_accept",
        "ihc_c_feature_source": "microseq_ring",
        "ihc_union_feature_sources": (
            "|".join(
                src
                for flag, src in (
                    (i_block, "I:expectancy"),
                    (h_block, "H:accept_bounce"),
                    (c_block, "C:microseq_ring"),
                )
                if flag
            )
            or "none"
        ),
    }


@dataclass
class IhcShadowPortfolioCounters:
    ihc_shadow_target_count: int = 0
    ihc_union_block_count: int = 0
    ihc_union_delta_yen: float = 0.0
    ihc_union_blocked_early_stop: int = 0
    ihc_union_blocked_stop_hit: int = 0
    ihc_union_blocked_winners: int = 0
    ihc_union_blocked_big_winners: int = 0
    ihc_overlap_count: int = 0

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        if not any(
            _bool(fields.get(k))
            for k in (
                "readiness_precision_shadow_candidate",
                "readiness_economics_shadow_candidate",
                "microsequence_recovery_fail_shadow_candidate",
            )
        ):
            return
        self.ihc_shadow_target_count += 1
        self.ihc_overlap_count += int(fields.get("ihc_overlap_count") or 0)

    def record_exit(self, row: Mapping[str, Any]) -> None:
        union = _bool(row.get("shadow_union_ihc_block"))
        if not union and not (
            _bool(row.get("readiness_precision_shadow_candidate"))
            or _bool(row.get("readiness_economics_shadow_candidate"))
            or _bool(row.get("microsequence_recovery_fail_shadow_candidate"))
        ):
            return
        actual = _float(row.get("actual_pnl_yen_100")) or 0.0
        if union:
            self.ihc_union_block_count += 1
            self.ihc_union_delta_yen = round(self.ihc_union_delta_yen - actual, 2)
            if _bool(row.get("is_early_stop_300s")):
                self.ihc_union_blocked_early_stop += 1
            if _bool(row.get("is_stop_hit")):
                self.ihc_union_blocked_stop_hit += 1
            if actual > 0:
                self.ihc_union_blocked_winners += 1
            if actual >= BIG_WINNER_YEN:
                self.ihc_union_blocked_big_winners += 1

    def summary_fields(self) -> dict[str, Any]:
        return {
            "ihc_union_shadow_block_count": self.ihc_union_block_count,
            "ihc_union_shadow_delta_yen": self.ihc_union_delta_yen,
            "ihc_union_shadow_blocked_early_stop": self.ihc_union_blocked_early_stop,
            "ihc_union_shadow_blocked_stop_hit": self.ihc_union_blocked_stop_hit,
            "ihc_union_shadow_blocked_winners": self.ihc_union_blocked_winners,
            "ihc_union_shadow_big_winners": self.ihc_union_blocked_big_winners,
            "ihc_overlap_count": self.ihc_overlap_count,
            "ihc_h_feature_source": "readiness_bounce_from_recent_low_accept",
            "ihc_c_feature_source": "microseq_ring",
            "ihc_union_feature_sources": "I:expectancy|H:accept_bounce|C:microseq_ring",
        }


def build_ihc_shadow_portfolio_counters(config: Any) -> Optional[IhcShadowPortfolioCounters]:
    from small_paper.microsequence_recovery_fail_forward_shadow import microsequence_recovery_fail_shadow_enabled
    from small_paper.readiness_forward_shadow import (
        readiness_economics_shadow_enabled,
        readiness_precision_shadow_enabled,
    )

    if not (
        readiness_precision_shadow_enabled(config)
        or readiness_economics_shadow_enabled(config)
        or microsequence_recovery_fail_shadow_enabled(config)
    ):
        return None
    return IhcShadowPortfolioCounters()


def format_ihc_shadow_discord_lines(summary: Mapping[str, Any]) -> list[str]:
    if summary.get("ihc_union_shadow_block_count") is None and summary.get("microsequence_c_shadow_block_count") is None:
        return []
    lines = ["IHC Shadow Portfolio:"]
    if summary.get("microsequence_c_shadow_block_count") is not None:
        lines.append(
            f"C block={summary.get('microsequence_c_shadow_block_count', 0)} "
            f"Δ={summary.get('microsequence_c_shadow_delta_yen', 0)} "
            f"ES={summary.get('microsequence_c_shadow_blocked_early_stop', 0)}"
        )
    lines.append(
        f"I∨H∨C union={summary.get('ihc_union_shadow_block_count', 0)} "
        f"Δ={summary.get('ihc_union_shadow_delta_yen', 0)} "
        f"BW={summary.get('ihc_union_shadow_big_winners', 0)} "
        f"overlap={summary.get('ihc_overlap_count', 0)} "
        f"sources=I:expectancy|H:accept_bounce|C:microseq_ring"
    )
    return lines
