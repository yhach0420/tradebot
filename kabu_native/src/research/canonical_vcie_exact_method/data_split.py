"""Chronological split — 4-day fallback when insufficient OOS."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.canonical_vcie_exact_method.constants import CAPTURE_ROOT


def list_capture_days(root: Path = CAPTURE_ROOT) -> list[str]:
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.glob("push_part_*.jsonl")))


def discover_and_split(root: Path = CAPTURE_ROOT) -> dict[str, Any]:
    days = list_capture_days(root)
    eligible = list(days)
    need_train, need_val, need_oos = 10, 3, 5
    insufficient = len(eligible) < (need_train + need_val + need_oos)
    if insufficient and len(eligible) >= 4:
        warmup, train, val, oos = [eligible[0]], [eligible[1]], [eligible[2]], [eligible[3]]
        split_mode = "INSUFFICIENT_FOUR_DAY_FALLBACK"
    elif insufficient and len(eligible) == 3:
        warmup, train, val, oos = [eligible[0]], [eligible[1]], [eligible[1]], [eligible[2]]
        split_mode = "INSUFFICIENT_FALLBACK"
    else:
        warmup = []
        train = eligible[:need_train]
        val = eligible[need_train : need_train + need_val]
        oos = eligible[need_train + need_val : need_train + need_val + need_oos]
        split_mode = "FULL"
    return {
        "all_days": days,
        "eligible_days": eligible,
        "warmup": warmup,
        "train": train,
        "validation": val,
        "strict_oos": oos,
        "insufficient_oos": insufficient or len(oos) < need_oos,
        "split_mode": split_mode,
    }
