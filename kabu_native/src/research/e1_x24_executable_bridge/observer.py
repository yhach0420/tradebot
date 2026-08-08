"""Observer-only pure evaluation module (no runtime connection)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from research.e1_x21_entry_factory_exit_benchmark.factory import evaluate_entry_candidate
from research.e1_x22_actual_exit_factory.exits import simulate_exit_on_path

from . import ACTUAL_EXITS, EXEC_WINDOW_SEC


@dataclass
class PairDecision:
    pair_id: str
    candidate_id: str
    actual_exit_id: str
    entry_decision: str
    entry_reason: str
    exit_reason: Optional[str] = None
    exit_state: Optional[str] = None
    reference_entry_price: Optional[float] = None
    reference_exit_price: Optional[float] = None
    executable_price_status: str = "NOT_EVALUATED"
    entry_ask: Optional[float] = None
    exit_bid: Optional[float] = None


@dataclass
class PairDecisions:
    decisions: list[PairDecision] = field(default_factory=list)
    unique_masks_evaluated: int = 0
    runtime_connected: bool = False


def evaluate_precommitted_pair_bundle(
    bundle: dict[str, Any],
    market_snapshot: dict[str, Any],
    price_path: dict[str, Any],
) -> PairDecisions:
    """
    Pure evaluation of precommitted pairs.

    - Computes each unique ENTRY mask once from market_snapshot feature fields
    - Distributes ENTRY_ALLOWED to each EXIT attached to that mask
    - Simulates EXIT on price_path arrays (times/prices)
    - Does not connect to production runtime
    """
    pair_list = bundle.get("pair_list") or []
    # group exits by candidate
    by_cand: dict[str, list[dict[str, Any]]] = {}
    for p in pair_list:
        by_cand.setdefault(p["candidate_id"], []).append(p)

    decisions: list[PairDecision] = []
    masks_evaluated = 0
    for cid, pairs in by_cand.items():
        spec = pairs[0].get("candidate_spec") or {}
        # synthesize candidate for evaluate_entry_candidate
        cand = {
            "candidate_id": cid,
            "feature_name": spec.get("feature_name"),
            "threshold": spec.get("threshold"),
            "op": spec.get("op") or ">=",
            "n_features": spec.get("n_features") or 1,
            "parents": spec.get("parents"),
        }
        # two-feature: require both parent features in snapshot via precomputed decision
        if cand.get("n_features") == 2 and cand.get("parents"):
            # market_snapshot may include precomputed parent decisions
            parent_ok = all(
                market_snapshot.get(f"decision::{pid}") == "ENTRY_ALLOWED"
                for pid in cand["parents"]
            )
            if parent_ok:
                entry = {
                    "decision": "ENTRY_ALLOWED",
                    "reason": "AND_parents",
                    "anchor_price": market_snapshot.get("CurrentPrice"),
                }
            else:
                missing = any(
                    market_snapshot.get(f"decision::{pid}") == "FEATURE_MISSING"
                    for pid in cand["parents"]
                )
                entry = {
                    "decision": "FEATURE_MISSING" if missing else "ENTRY_REJECTED",
                    "reason": "AND_parents_not_allowed",
                    "anchor_price": market_snapshot.get("CurrentPrice"),
                }
        else:
            # single feature: if feature_name has '+', skip to FEATURE_MISSING unless value provided
            fn = cand.get("feature_name") or ""
            if "+" in fn:
                entry = {
                    "decision": "FEATURE_MISSING",
                    "reason": "composite_requires_parents",
                    "anchor_price": market_snapshot.get("CurrentPrice"),
                }
            else:
                entry = evaluate_entry_candidate(cand, market_snapshot)
        masks_evaluated += 1

        for p in pairs:
            eid = p["actual_exit_id"]
            pd = PairDecision(
                pair_id=p["pair_id"],
                candidate_id=cid,
                actual_exit_id=eid,
                entry_decision=entry["decision"],
                entry_reason=entry.get("reason") or "",
                reference_entry_price=entry.get("anchor_price") or market_snapshot.get("CurrentPrice"),
            )
            if entry["decision"] != "ENTRY_ALLOWED":
                pd.exit_state = "NO_ENTRY"
                pd.executable_price_status = "NOT_APPLICABLE"
                decisions.append(pd)
                continue
            times = np.asarray(price_path.get("times", []), dtype=float)
            prices = np.asarray(price_path.get("prices", []), dtype=float)
            if times.size == 0:
                pd.exit_state = "NO_PATH"
                pd.executable_price_status = "EXECUTION_PRICE_UNAVAILABLE"
                decisions.append(pd)
                continue
            tr = simulate_exit_on_path(
                exit_id=eid,
                entry_epoch=float(market_snapshot.get("grid_epoch") or price_path.get("entry_epoch") or times[0]),
                entry_price=float(pd.reference_entry_price),
                date=str(market_snapshot.get("date") or "20260804"),
                session=str(market_snapshot.get("session") or "AM"),
                times=times,
                prices=prices,
            )
            if tr is None:
                pd.exit_state = "UNEVALUABLE"
            else:
                pd.exit_reason = tr["exit_reason"]
                pd.exit_state = "EXITED"
                pd.reference_exit_price = tr["exit_price"]
            # executable status from optional board path
            board = price_path.get("board")
            if board is None:
                pd.executable_price_status = "BOARD_NOT_PROVIDED"
            else:
                from .execution import first_valid_after
                ent = first_valid_after(board, float(market_snapshot.get("grid_epoch") or times[0]), side="ask")
                if ent["status"] != "OK":
                    pd.executable_price_status = ent["status"]
                else:
                    pd.entry_ask = ent["price"]
                    if tr is not None:
                        exi = first_valid_after(board, float(tr["exit_time_epoch"]), side="bid")
                        pd.executable_price_status = exi["status"]
                        if exi["status"] == "OK":
                            pd.exit_bid = exi["price"]
                    else:
                        pd.executable_price_status = "EXIT_UNEVALUABLE"
            decisions.append(pd)

    return PairDecisions(
        decisions=decisions,
        unique_masks_evaluated=masks_evaluated,
        runtime_connected=False,
    )
