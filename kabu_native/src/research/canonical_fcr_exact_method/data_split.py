"""Chronological split — 20260724 = REUSED_FORENSIC_HOLDOUT."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.canonical_fcr_exact_method.constants import CAPTURE_ROOT


def list_capture_days(root: Path = CAPTURE_ROOT) -> list[str]:
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.glob("push_part_*.jsonl")))


def discover_and_split(root: Path = CAPTURE_ROOT) -> dict[str, Any]:
    days = list_capture_days(root)
    eligible = list(days)
    if len(eligible) >= 4:
        warmup, train, val, holdout = [eligible[0]], [eligible[1]], [eligible[2]], [eligible[3]]
        split_mode = "INSUFFICIENT_FOUR_DAY_FALLBACK"
    elif len(eligible) == 3:
        warmup, train, val, holdout = [eligible[0]], [eligible[1]], [eligible[1]], [eligible[2]]
        split_mode = "INSUFFICIENT_FALLBACK"
    else:
        warmup = train = val = holdout = list(eligible)
        split_mode = "INSUFFICIENT"
    return {
        "all_days": days,
        "eligible_days": eligible,
        "warmup": warmup,
        "train": train,
        "validation": val,
        "forensic_holdout": holdout,
        "strict_oos_label": "REUSED_FORENSIC_HOLDOUT",
        "insufficient_fresh_oos": True,
        "split_mode": split_mode,
    }
