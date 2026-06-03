"""
Phase277: Session counters for Discord operator UX (notification/aggregation only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _int_score(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


@dataclass
class DiscordUxSessionStats:
    score5_candidate_count: int = 0
    score5_entry_count: int = 0
    score5_deferred_total_count: int = 0
    entry_deferred_notify_count: int = 0
    deferred_notify_log: list[dict[str, Any]] = field(default_factory=list)
    deferred_reject_by_symbol: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record_score5_candidate(self) -> int:
        """Returns 1-based ordinal for today's score>=5 evaluations."""
        self.score5_candidate_count += 1
        return self.score5_candidate_count

    def record_score5_entry(self) -> None:
        self.score5_entry_count += 1

    def record_score5_deferred_reject(self, *, symbol: str, entry_score_v2: int) -> None:
        self.score5_deferred_total_count += 1
        sym = str(symbol or "").strip()
        if not sym:
            return
        bucket = self.deferred_reject_by_symbol.setdefault(
            sym,
            {"symbol": sym, "count": 0, "max_score": 0},
        )
        bucket["count"] = int(bucket["count"]) + 1
        bucket["max_score"] = max(int(bucket["max_score"]), int(entry_score_v2))

    def record_entry_deferred_notify(self, *, symbol: str, entry_score_v2: int) -> None:
        self.entry_deferred_notify_count += 1
        self.deferred_notify_log.append(
            {
                "symbol": symbol,
                "entry_score_v2": entry_score_v2,
            }
        )

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "score5_candidate_count": self.score5_candidate_count,
            "score5_entry_count": self.score5_entry_count,
            "score5_deferred_total_count": self.score5_deferred_total_count,
            "entry_deferred_notify_count": self.entry_deferred_notify_count,
            "deferred_reject_by_symbol": dict(self.deferred_reject_by_symbol),
        }


def score5_from_mapping(data: Mapping[str, Any]) -> bool:
    v = _int_score(data.get("entry_expectancy_score_v2"))
    return v is not None and v >= 5
