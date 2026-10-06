"""Scan this package for unsupported clock / mixed-state semantics."""
from __future__ import annotations

from pathlib import Path
from typing import Any

FORBIDDEN_TOKENS = (
    "FAIL_EXTEND_MAX_BARS",
    "LAST_BREAK",
)
# SETUP_ELIGIBLE is forbidden as a mixed state name in this machine.
MIXED_STATE = "SETUP_ELIGIBLE"
SEMANTIC_CLOCKS = ("09:30", "09:45")
ELIGIBILITY_FILES = {
    "walk.py",
    "machine.py",
    "seed.py",
    "active.py",
    "location.py",
    "execution.py",
    "continuation.py",
    "s0.py",
    "baselines.py",
    "thesis.py",
}


def scan_package() -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    hits: list[dict[str, Any]] = []
    for p in sorted(root.glob("*.py")):
        if p.name not in ELIGIBILITY_FILES:
            continue
        text = p.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                hits.append({"file": p.name, "token": token, "count": text.count(token)})
        if MIXED_STATE in text:
            hits.append({"file": p.name, "token": MIXED_STATE, "count": text.count(MIXED_STATE)})
        for token in SEMANTIC_CLOCKS:
            if token in text:
                hits.append({"file": p.name, "token": token, "count": text.count(token)})
    return {
        "unsupported_clock_semantic_hits": hits,
        "unsupported_clock_semantic_count": sum(int(h.get("count") or 0) for h in hits),
        "FAIL_EXTEND_present": any(h.get("token") == "FAIL_EXTEND_MAX_BARS" for h in hits),
        "LAST_BREAK_present": any(h.get("token") == "LAST_BREAK" for h in hits),
        "SETUP_ELIGIBLE_in_eligibility": any(h.get("token") == MIXED_STATE for h in hits),
    }
