"""Mainline + research impact tables (semantic correct / inverted / unaffected)."""
from __future__ import annotations

from typing import Any


def pbv2_impact() -> list[dict[str, Any]]:
    return [
        {
            "feature": "Board mid/high判定",
            "semantic": "inverted",
            "direction_flips": True,
            "threshold_meaning_changes": True,
            "entry_count_impact": "HIGH — Board:mid/high gate uses inverted imb tertiles",
            "exit_time_impact": "indirect via different accepts",
            "pnl_impact": "HIGH",
            "safety_impact": "HIGH — accepts different cohort than English-book intent",
            "detail": "entry_order_book_imbalance from calc_board_imbalance mixes BidQty(ask) into bid bucket",
        },
        {
            "feature": "entry_score_v2 Board点",
            "semantic": "inverted",
            "direction_flips": True,
            "threshold_meaning_changes": True,
            "entry_count_impact": "HIGH",
            "exit_time_impact": "indirect",
            "pnl_impact": "HIGH",
            "safety_impact": "HIGH",
            "detail": "Board:mid/high tokens score +1 from inverted imbalance",
        },
        {
            "feature": "entry_order_book_imbalance",
            "semantic": "inverted",
            "direction_flips": True,
            "threshold_meaning_changes": True,
            "entry_count_impact": "HIGH",
            "exit_time_impact": "n/a",
            "pnl_impact": "HIGH",
            "safety_impact": "HIGH",
            "detail": "Core feature inverted/mixed; thresholds (0.43/0.52 tertile) calibrated on inverted values",
        },
        {
            "feature": "board improvement / bid_pressure / ask_pressure",
            "semantic": "mixed",
            "direction_flips": "partial",
            "threshold_meaning_changes": True,
            "entry_count_impact": "MEDIUM",
            "exit_time_impact": "n/a",
            "pnl_impact": "MEDIUM",
            "safety_impact": "MEDIUM",
            "detail": "board_entry_features research path CORRECT; runtime imb path INVERTED",
        },
        {
            "feature": "spread / spread_bps",
            "semantic": "unaffected_magnitude",
            "direction_flips": False,
            "threshold_meaning_changes": False,
            "entry_count_impact": "LOW",
            "exit_time_impact": "none",
            "pnl_impact": "LOW",
            "safety_impact": "LOW",
            "detail": "calc_spread_bps uses abs(); width OK. Signed (ask-bid) would flip.",
        },
        {
            "feature": "flat-band / flat-weak-range / pullback-misread",
            "semantic": "partial",
            "direction_flips": "if board-gated",
            "threshold_meaning_changes": "partial",
            "entry_count_impact": "MEDIUM",
            "exit_time_impact": "indirect",
            "pnl_impact": "MEDIUM",
            "safety_impact": "MEDIUM",
            "detail": "Price-path parts OK; board-conditioned filters inherit inversion",
        },
        {
            "feature": "entry expectancy / price risk guard",
            "semantic": "partial",
            "direction_flips": "Board component only",
            "threshold_meaning_changes": True,
            "entry_count_impact": "MEDIUM",
            "exit_time_impact": "n/a",
            "pnl_impact": "MEDIUM",
            "safety_impact": "MEDIUM",
            "detail": "Board token in expectancy uses inverted imb",
        },
    ]


def guard_impact() -> list[dict[str, Any]]:
    return [
        {
            "feature": "EntryQualityGuard spread",
            "semantic": "unaffected_magnitude",
            "direction_flips": False,
            "threshold_meaning_changes": False,
            "entry_count_impact": "LOW",
            "pnl_impact": "LOW",
            "safety_impact": "LOW",
            "detail": "abs spread_bps",
        },
        {
            "feature": "ExposureGate entry_quality",
            "semantic": "unaffected_magnitude",
            "direction_flips": False,
            "threshold_meaning_changes": False,
            "entry_count_impact": "LOW",
            "pnl_impact": "LOW",
            "safety_impact": "LOW",
            "detail": "Depends on abs spread",
        },
        {
            "feature": "PBv2 board_mid_required gate",
            "semantic": "inverted",
            "direction_flips": True,
            "threshold_meaning_changes": True,
            "entry_count_impact": "HIGH",
            "pnl_impact": "HIGH",
            "safety_impact": "HIGH",
            "detail": "Primary semantic integrity break on accept path",
        },
        {
            "feature": "scan ranking imbalance",
            "semantic": "inverted",
            "direction_flips": True,
            "threshold_meaning_changes": True,
            "entry_count_impact": "HIGH",
            "pnl_impact": "HIGH",
            "safety_impact": "MEDIUM",
            "detail": "entry_scan uses entry_order_book_imbalance",
        },
    ]


def exit_impact() -> list[dict[str, Any]]:
    return [
        {
            "feature": "Board Dynamic Trailing",
            "semantic": "inverted",
            "direction_flips": True,
            "threshold_meaning_changes": True,
            "exit_time_impact": "HIGH — tier from entry_imbalance_percentile",
            "pnl_impact": "HIGH",
            "safety_impact": "HIGH",
            "detail": "Trailing params keyed by board tier from inverted entry imb",
        },
        {
            "feature": "entry_imbalance_percentile",
            "semantic": "inverted",
            "direction_flips": True,
            "threshold_meaning_changes": True,
            "exit_time_impact": "HIGH",
            "pnl_impact": "HIGH",
            "safety_impact": "HIGH",
            "detail": "Rank among inverted values; relative rank may partially self-calibrate",
        },
        {
            "feature": "board_high / board_low分岐",
            "semantic": "inverted",
            "direction_flips": True,
            "threshold_meaning_changes": True,
            "exit_time_impact": "MEDIUM",
            "pnl_impact": "MEDIUM",
            "safety_impact": "MEDIUM",
            "detail": "Token labels swapped relative to English book",
        },
        {
            "feature": "realtime board EXIT / board collapse / profit protect",
            "semantic": "inverted",
            "direction_flips": True,
            "threshold_meaning_changes": True,
            "exit_time_impact": "HIGH",
            "pnl_impact": "HIGH",
            "safety_impact": "HIGH",
            "detail": "calc_bid_ask_imbalance = BidQty/(Bid+Ask) = true ask share",
        },
        {
            "feature": "no-progress / hard stop / observer EXIT price",
            "semantic": "unaffected",
            "direction_flips": False,
            "threshold_meaning_changes": False,
            "exit_time_impact": "none (price-path)",
            "pnl_impact": "LOW (unless board-conditioned)",
            "safety_impact": "LOW",
            "detail": "Primarily CurrentPrice; not quote-side dependent",
        },
    ]


def execution_impact() -> list[dict[str, Any]]:
    return [
        {
            "feature": "buy execution price (should use ask)",
            "semantic": "inverted_if_AskPrice_used",
            "direction_flips": True,
            "detail": "dry-run uses AskPrice as ask but AskPrice=true bid → understates buy cost",
            "live_reachable": False,
            "safety_impact": "HIGH if ever wired live without remap",
        },
        {
            "feature": "sell execution price (should use bid)",
            "semantic": "inverted_if_BidPrice_used",
            "direction_flips": True,
            "detail": "BidPrice=true ask → overstates sell proceeds if used as bid",
            "live_reachable": False,
            "safety_impact": "HIGH if ever wired live without remap",
        },
        {
            "feature": "Paper observer mark / CurrentPrice",
            "semantic": "unaffected",
            "direction_flips": False,
            "detail": "Marks use CurrentPrice",
            "live_reachable": True,
            "safety_impact": "LOW",
        },
        {
            "feature": "submit/cancel/live_order counters",
            "semantic": "unaffected",
            "direction_flips": False,
            "detail": "Audit enforces 0/0/0; no order path change",
            "live_reachable": False,
            "safety_impact": "NONE",
        },
    ]


def research_impact() -> list[dict[str, Any]]:
    return [
        {
            "study": "PBv2 baseline replay",
            "classification": "INVALID_INVERTED_BOARD",
            "reason": "entry_order_book_imbalance / Board tokens from inverted lineage",
            "replay_priority": 1,
        },
        {
            "study": "flat-band",
            "classification": "PARTIAL_BOARD_IMPACT",
            "reason": "price-path OK; board-conditioned variants impacted",
            "replay_priority": 3,
        },
        {
            "study": "flat-weak-range",
            "classification": "PARTIAL_BOARD_IMPACT",
            "reason": "same as flat-band",
            "replay_priority": 3,
        },
        {
            "study": "pullback-misread",
            "classification": "PARTIAL_BOARD_IMPACT",
            "reason": "board filters inherit inversion",
            "replay_priority": 3,
        },
        {
            "study": "cost-aware entry",
            "classification": "PARTIAL_BOARD_IMPACT",
            "reason": "spread_bps abs OK; board/score components may use imb",
            "replay_priority": 4,
        },
        {
            "study": "Winner / STOP / NoProgress研究",
            "classification": "VALID_UNAFFECTED",
            "reason": "primarily price/MFE path; board not causal core",
            "replay_priority": 9,
        },
        {
            "study": "W43 series",
            "classification": "PARTIAL_BOARD_IMPACT",
            "reason": "mixed board usage across scripts",
            "replay_priority": 5,
        },
        {
            "study": "W54 series",
            "classification": "PARTIAL_BOARD_IMPACT",
            "reason": "bid_pressure from board_entry_features may be CORRECT",
            "replay_priority": 5,
        },
        {
            "study": "VCIE / volume_confirmed_impulse_entry",
            "classification": "INVALID_INVERTED_BOARD",
            "reason": "push_loader BidPrice→bid",
            "replay_priority": 1,
        },
        {
            "study": "Price-Flow EXIT",
            "classification": "INVALID_INVERTED_BOARD",
            "reason": "PushTick quotes + board trailing proxy",
            "replay_priority": 2,
        },
        {
            "study": "Entry–Exit Contract / EEC v2",
            "classification": "INVALID_INVERTED_BOARD",
            "reason": "bid_qty/spread from inverted PushTick",
            "replay_priority": 1,
        },
        {
            "study": "EEC v3 (eec_noise_hysteresis) A1 PF2.40",
            "classification": "INVALID_INVERTED_BOARD",
            "reason": "Confirmed dependency on inverted ask; formally invalidated by EGC",
            "replay_priority": 99,
            "action": "DO_NOT_RERUN_AS_IS",
        },
        {
            "study": "Confirmation Integrity",
            "classification": "INVALID_INVERTED_BOARD",
            "reason": "Pre-remap; superseded by EGC",
            "replay_priority": 99,
            "action": "CLOSED",
        },
        {
            "study": "Execution-Grade Confirmation",
            "classification": "VALID_CANONICAL_BOARD",
            "reason": "Buy1/Sell1 AtomicQuote; E1_X1 PF=0.3552 formal",
            "replay_priority": 0,
            "action": "SOURCE_OF_TRUTH_FOR_MAPPING",
        },
        {
            "study": "EC2 confirmation research",
            "classification": "INVALID_INVERTED_BOARD",
            "reason": "ENTRY_CONFIRMATION_NO_EDGE under remapped book; closed",
            "replay_priority": 99,
            "action": "ENDED",
        },
        {
            "study": "board_entry_features",
            "classification": "VALID_CANONICAL_BOARD",
            "reason": "Buy=bid Sell=ask ladder",
            "replay_priority": 0,
        },
        {
            "study": "Board Dynamic Trailing research / shadows",
            "classification": "INVALID_INVERTED_BOARD",
            "reason": "percentile from inverted entry imb",
            "replay_priority": 2,
        },
        {
            "study": "realtime_board_exit / board_failure shadows",
            "classification": "INVALID_INVERTED_BOARD",
            "reason": "top-of-book imb exactly inverted",
            "replay_priority": 2,
        },
    ]


def lineage_rows() -> dict[str, list[dict[str, Any]]]:
    runtime = [
        {"step": 1, "node": "raw PUSH original_payload", "bid_ask": "kabu BidPrice/AskPrice + Buy1/Sell1", "status": "RAW_CORRECT"},
        {"step": 2, "node": "push_client / market_capture", "bid_ask": "preserves raw names", "status": "RAW_CORRECT"},
        {"step": 3, "node": "Stage0 normalize", "bid_ask": "no remap", "status": "PASSTHROUGH"},
        {"step": 4, "node": "LiveFeatureBridge", "bid_ask": "price-only", "status": "UNAFFECTED"},
        {"step": 5, "node": "calc_board_imbalance", "bid_ask": "BidQty as bid", "status": "INVERTED"},
        {"step": 6, "node": "entry_order_book_imbalance / Board tokens", "bid_ask": "inverted imb", "status": "INVERTED"},
        {"step": 7, "node": "PBv2 score + board_mid gate", "bid_ask": "accepts on inverted tokens", "status": "INVERTED"},
        {"step": 8, "node": "ExposureGate / spread", "bid_ask": "abs spread", "status": "MAGNITUDE_OK"},
        {"step": 9, "node": "accept → position", "bid_ask": "cohort biased by inverted board", "status": "AFFECTED"},
        {"step": 10, "node": "Board Dynamic Trailing EXIT", "bid_ask": "tier from inverted percentile", "status": "INVERTED"},
        {"step": 11, "node": "summary / research replay", "bid_ask": "inherits accept+exit bias", "status": "AFFECTED"},
    ]
    paper = list(runtime) + [
        {"step": 12, "node": "realtime_board_exit_shadow", "bid_ask": "BidPrice as best_bid", "status": "INVERTED"},
        {"step": 13, "node": "market_capture_writer extract_board_fields", "bid_ask": "bid←BidPrice", "status": "INVERTED"},
    ]
    research = [
        {"step": 1, "node": "capture JSONL original_payload", "status": "RAW_CORRECT"},
        {"step": 2, "node": "push_loader PushTick", "status": "INVERTED"},
        {"step": 3, "node": "VCIE/EEC/PriceFlow features", "status": "INVERTED"},
        {"step": 4, "node": "execution_grade_confirmation.board", "status": "CANONICAL"},
        {"step": 5, "node": "board_entry_features", "status": "CANONICAL"},
    ]
    return {"runtime": runtime, "paper": paper, "research": research}
