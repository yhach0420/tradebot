"""Recover the V1 per-session universe. Reads manifests only, not push events."""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from typing import Any

from research.anchor_vs_event_driven.run_comparison import find_capture_dir
from research.symbol_setup_baseline_complete_strategy_precommit.isolation import NATIVE

if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from _p1_inventory import resolve_universe  # noqa: E402


def policy_sha256() -> str:
    return hashlib.sha256(inspect.getsource(resolve_universe).encode("utf-8")).hexdigest()


def recover(dates: list[str]) -> dict[str, Any]:
    rows = []
    for day in dates:
        if str(day) > "20260911":
            raise RuntimeError("prospective_surface")
        cap = find_capture_dir(str(day))
        resolved = resolve_universe(str(day), cap)
        symbols = [str(s) for s in list(resolved.get("symbols") or [])]
        rows.append(
            {
                "date": str(day),
                "resolved": bool(resolved.get("resolved")),
                "reason": str(resolved.get("reason") or ""),
                "source": str(resolved.get("source") or ""),
                "universe_n": int(resolved.get("universe_n") or 0),
                "symbols": symbols,
                "notes": list(resolved.get("notes") or []),
                "capture_present": cap is not None,
            }
        )
    ok = all(r["resolved"] and r["universe_n"] > 0 for r in rows)
    membership = [{"date": r["date"], "source": r["source"], "symbols": r["symbols"]} for r in rows]
    blob = json.dumps(membership, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return {
        "UNIVERSE_POLICY_ID": "SIMPLE_TECH_V1_RESOLVE_UNIVERSE",
        "UNIVERSE_POLICY_SHA256": policy_sha256(),
        "source_file": "scripts/_p1_inventory.py",
        "function": "resolve_universe",
        "priority": "frozen AM universe, else one same-day registration or AM csv, unresolved when those sources disagree or are absent",
        "not_used": ["fixed 105 universe", "M3 targets", "current runtime 50 as a substitute"],
        "resolved": ok,
        "membership_sha256": hashlib.sha256(blob).hexdigest(),
        "sessions": rows,
    }
