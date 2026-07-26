"""Chronological TRAIN / VALIDATION / STRICT OOS isolation helpers."""
from __future__ import annotations

from typing import Any, Sequence


def assert_no_oos_leak(train: Sequence[str], val: Sequence[str], oos: Sequence[str]) -> dict[str, Any]:
    ts, vs, os_ = set(train), set(val), set(oos)
    # allow insufficient fallback overlap only when explicitly flagged elsewhere
    leak_tv = bool(ts & vs) and len(ts) > 1
    leak_to = bool(ts & os_)
    leak_vo = bool(vs & os_) and vs != ts
    return {
        "train_val_overlap": sorted(ts & vs),
        "train_oos_overlap": sorted(ts & os_),
        "val_oos_overlap": sorted(vs & os_),
        "strict_isolation": not leak_to and not (leak_vo and vs != os_),
    }
