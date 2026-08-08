"""Verdict + PASSIVE_FIXED600_EXIT_BASELINE_V1 freeze."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import (
    CANONICAL_HORIZON_SEC,
    CANONICAL_LOOKUP,
    ENTRY_SHA,
    NEXT_PASS,
    NEXT_STOP,
    VERDICT_BASELINE_CHANGED,
    VERDICT_NOT_SUPPORTED,
    VERDICT_RECONCILED,
)
from .recompute import best_robust_fixed, passes_robustness


def decide_verdict(recomputed: dict[str, Any]) -> dict[str, Any]:
    f600 = recomputed["FIXED600"]
    rob600 = passes_robustness(f600)
    ranking = best_robust_fixed(recomputed)
    best_H = ranking.get("best_H")

    if not rob600["pass"]:
        return {
            "verdict": VERDICT_NOT_SUPPORTED,
            "freeze": False,
            "next": NEXT_STOP,
            "fixed600_gates": rob600,
            "ranking": ranking,
            "reason": f"FIXED600 lost robustness: {rob600['failed']}",
            "x35_verdict_changed": True,
        }

    if best_H is not None and best_H != 600:
        # another fixed horizon robustly preferable — stop for review
        return {
            "verdict": VERDICT_BASELINE_CHANGED,
            "freeze": False,
            "next": NEXT_STOP,
            "fixed600_gates": rob600,
            "ranking": ranking,
            "reason": f"FIXED{best_H} robustly preferable to FIXED600 after contract repair",
            "x35_verdict_changed": True,
        }

    return {
        "verdict": VERDICT_RECONCILED,
        "freeze": True,
        "next": NEXT_PASS,
        "fixed600_gates": rob600,
        "ranking": ranking,
        "reason": "FIXED600 passes robustness and remains best robust fixed baseline under canonical contract",
        "x35_verdict_changed": False,  # X35 FIXED600 baseline direction preserved
    }


def freeze_manifest(*, decision: dict, f600: dict) -> dict[str, Any]:
    body = {
        "manifest_id": "PASSIVE_FIXED600_EXIT_BASELINE_V1",
        "entry_sha": ENTRY_SHA,
        "horizon_sec": CANONICAL_HORIZON_SEC,
        "entry_timestamp_rule": "conservative_passive_fill_time",
        "entry_price_rule": "passive_limit_fill_price",
        "target_timestamp_rule": "fill_time + horizon_sec",
        "bid_lookup_rule": CANONICAL_LOOKUP,
        "bid_lookup_detail": (
            "Walk valid Buy1 path after fill; EXIT at first tick with "
            "offset >= horizon_sec (same as X35 E0_FIXED_600 / simulate_exit)."
        ),
        "qty_min": 100,
        "freshness_max_sec": 5.0,
        "special_quote": False,
        "same_session": True,
        "session_close_rule": (
            "If fill_time+horizon exceeds session end or no tick reaches horizon, "
            "force SESSION_CLOSE at last valid Buy1 in session. No session cross."
        ),
        "missing_quote_rule": (
            "No synthetic/mid/CurrentPrice. If no valid bid after fill, episode invalid; "
            "if path exists but never reaches horizon, SESSION_CLOSE at last valid bid."
        ),
        "return_denominator": "fill_price",
        "price_side": "Buy1.Price",
        "no_mid_exit": True,
        "no_synthetic_price": True,
        "research_paper_only": True,
        "runtime_reflect": False,
        "mean_ret_bps": f600.get("mean_ret_bps"),
        "pf": f600.get("pf"),
        "positive_days": f600.get("positive_days"),
        "hold_median_sec": (f600.get("hold_sec") or {}).get("median"),
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
