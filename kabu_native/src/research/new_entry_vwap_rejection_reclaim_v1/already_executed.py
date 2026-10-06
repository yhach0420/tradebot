"""Compare exact architecture vs prior VWAP/reclaim work. Word 'VWAP' alone is not a duplicate."""
from __future__ import annotations

from typing import Any

from research.new_entry_vwap_rejection_reclaim_v1.rule import EXACT_RULE_TEXT

PRIMARY_IDENTITY = (
    "same completed 1m bar: Low[i] < VWAP[i] AND Close[i] > VWAP[i] AND Close[i] > Open[i]; "
    "first-cross of that 3-condition RECLAIM_STATE; SIGNAL_T0 = bar i finalize; "
    "ENTRY reference = fresh executable Ask1; markout = (Bid_h / Ask_t0 - 1) * 10000"
)


def _cmp(
    *,
    name: str,
    predicate: str,
    state_machine: str,
    event_timestamp: str,
    entry_reference: str,
    markout: str,
    exact_same: bool,
    why_not: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "predicate": predicate,
        "state_machine": state_machine,
        "event_timestamp": event_timestamp,
        "entry_reference": entry_reference,
        "markout": markout,
        "exact_same_architecture": bool(exact_same),
        "why_not_duplicate": why_not,
    }


def already_executed_check() -> dict[str, Any]:
    comparisons = [
        _cmp(
            name="E1_X18 VWAP late-chase reject",
            predicate="C0 overlay: reject when distance_from_vwap_bps exceeds frozen VWAP_UPPER_LIMIT_BPS (late-chase above VWAP).",
            state_machine="Filter on existing C0 ENTRY population. Not a 1m OHLC reclaim event.",
            event_timestamp="C0 signal time, not completed 1m bar finalize of Low-undercut/Close-reclaim.",
            entry_reference="C0 fill / C0 Ask path, not this family's Ask1-at-1m-finalize.",
            markout="X16/X17/X18 VWAP-reject audit cohorts, not Ask1→Bid 60/180/300 on this event.",
            exact_same=False,
            why_not="Rejects chasing extended above VWAP. Opposite of same-bar VWAP undercut then reclaim.",
        ),
        _cmp(
            name="X6 / X6R3 pullback reclaim",
            predicate="Price-flow EXIT / pullback-entry EXIT composite (X6), not 1m Low<VWAP Close>VWAP Close>Open ENTRY.",
            state_machine="EXIT overlay on pullback entries.",
            event_timestamp="Post-ENTRY exit clock.",
            entry_reference="Prior pullback ENTRY, not this event's Ask1.",
            markout="EXIT PnL, not entry-neutral Ask1→Bid markout of a VWAP reclaim bar.",
            exact_same=False,
            why_not="Exit family on a different ENTRY population.",
        ),
        _cmp(
            name="EC2 pullback reclaim",
            predicate="Trend + pullback depth band + micro-high true-cross + hold_sec + uptick volume. VWAP approximated as mid of range high/low.",
            state_machine="Multi-bar pullback then reclaim of a micro-high, with hold confirmation.",
            event_timestamp="Tick/push event after hold, not completed 1m bar i finalize.",
            entry_reference="EC2 contract current_price at hold confirmation.",
            markout="EC2 contract M0/M1/M2 pairing, not this Ask1→Bid 60/180/300 protocol.",
            exact_same=False,
            why_not="Pullback-to-micro-high reclaim with extra thresholds. Not same-bar VWAP rejection candle.",
        ),
        _cmp(
            name="Simple-Tech VWAP-related rules",
            predicate="T3 pullback + RCI. VWAP is diagnostic vwap_rel, not ENTRY of Low<VWAP AND Close>VWAP AND Close>Open.",
            state_machine="RCI reversal / pullback stages.",
            event_timestamp="Simple-Tech signal time on 1m stack with RCI/EMA/BB.",
            entry_reference="Simple-Tech E4/Ask path.",
            markout="Simple-Tech family economics; family CLOSED.",
            exact_same=False,
            why_not="Different ENTRY stack. VWAP word overlap only.",
        ),
        _cmp(
            name="Branch P VWAP EXIT",
            predicate="POST_BE_VWAP_CLOSE_LOSS_1M: after ENTRY, 1m Close loss vs VWAP as EXIT.",
            state_machine="EXIT on Simple-Tech T3 population after BE.",
            event_timestamp="Post-fill 1m close vs VWAP.",
            entry_reference="Existing Simple-Tech fill, not a new ENTRY event.",
            markout="EXIT yen / Full Causal candidate stream.",
            exact_same=False,
            why_not="EXIT, not ENTRY. Close below VWAP to leave, not Low-undercut Close-reclaim to enter.",
        ),
        _cmp(
            name="BREAKOUT_CONTINUATION_V1",
            predicate="Close[i] > max(prior 5 High) AND Volume[i] > median(prior 10 Volume) AND Close[i] > VWAP[i]. First-cross of P1 only.",
            state_machine="Momentum chase of a local high with volume expansion.",
            event_timestamp="Completed 1m finalize (shared bar clock only).",
            entry_reference="fresh executable Ask1 (shared execution validity only).",
            markout="Ask1→Bid 60/180/300 (shared evaluation protocol only).",
            exact_same=False,
            why_not="Chase family. No Low<VWAP. No Close>Open. Volume and prior-high required. CLOSED CASE B. Not a retune.",
        ),
    ]
    duplicate = any(bool(c["exact_same_architecture"]) for c in comparisons)
    vwap_structurally_unavailable = False
    fallback_allowed = bool(duplicate) or bool(vwap_structurally_unavailable)
    return {
        "PRIMARY_IDENTITY": PRIMARY_IDENTITY,
        "EXACT_RULE_TEXT": EXACT_RULE_TEXT,
        "comparisons": comparisons,
        "DUPLICATE_ARCHITECTURE": bool(duplicate),
        "PRIMARY_DUPLICATE_ARCHITECTURE": bool(duplicate),
        "PRIMARY_REQUIRED_VWAP_DATA_STRUCTURALLY_UNAVAILABLE": bool(vwap_structurally_unavailable),
        "FALLBACK_ALLOWED": bool(fallback_allowed),
        "FALLBACK_USED": False,
        "BREAKOUT_USED_ONLY_AS": "local-high chase family is closed. No Breakout numeric retune.",
    }
