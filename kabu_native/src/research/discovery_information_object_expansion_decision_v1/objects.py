"""Frozen information-object catalog, eligibility, and selection. No PnL. No thresholds."""
from __future__ import annotations

from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.discovery_information_object_expansion_decision_v1 import (
    CASE_A,
    CASE_B,
    CURRENT_42_RETUNE,
    IOAR_EXACT_MECHANISM,
    NEXT_A,
    NEXT_B,
    PREOPEN_EXECUTION_VALID,
    STATUSES,
)

RAW_FIELD_LEVELS = tuple(
    [f"Buy{i}" for i in range(1, 11)] + [f"Sell{i}" for i in range(1, 11)]
)
FULL_DEPTH_RAW_FIELDS = (
    "Buy1.Price",
    "Buy1.Qty",
    "Buy1.Sign",
    "Buy1.Time",
    "Sell1.Price",
    "Sell1.Qty",
    "Sell1.Sign",
    "Sell1.Time",
    *tuple(f"Buy{i}.Price" for i in range(2, 11)),
    *tuple(f"Buy{i}.Qty" for i in range(2, 11)),
    *tuple(f"Sell{i}.Price" for i in range(2, 11)),
    *tuple(f"Sell{i}.Qty" for i in range(2, 11)),
    "received_at",
    "AskTime",
    "BidTime",
)

FORBIDDEN_DEPTH_TRANSFORMS = (
    "canonical_depth_imbalance",
    "depth_imb_d3",
    "depth_imb_d5",
    "depth_imb_d10",
    "depth_ratio_3",
    "depth_ratio_5",
    "depth_ratio_10",
    "weighted_depth_imbalance",
    "PBv2_p33_p66_board_token_from_depth",
    "L1_only_bid_depth_simple_tech_filter_revival",
    "IOAR_exact_absorption_replenishment",
    "numeric_weighting_search",
    "k_level_search_3_vs_5_vs_10",
    "threshold_optimization",
    "top1_imbalance_as_this_object",
    "net_bid_pressure_formula",
    "net_ask_pressure_formula",
    "imbalance_change_formula",
)

FULL_DEPTH_BEGIN = (
    "After a gapped or incomplete displayed ladder on the chosen side, "
    "all 10 stored levels become present (Qty>0) and strictly price-ordered "
    "(Buy1>Buy2>...>Buy10 for bids; Sell1<Sell2<...<Sell10 for asks) on a "
    "causal as-of payload."
)
FULL_DEPTH_INVALIDATE = (
    "Any stored level Qty hits 0 (a hole opens), displayed price order breaks, "
    "or successive as-of snapshots show the ladder migrating off a contiguous "
    "displayed stack. Pre-open Ask/Bid cannot begin or invalidate an executable state."
)
FULL_DEPTH_STATE = (
    "Displayed 10-level bid or ask ladder is a contiguous resting structure "
    "versus a gapped or migrating structure. The whole stored 10-level book is "
    "one object, not a search over 3 vs 5 vs 10."
)


def _base(
    object_id: str,
    *,
    raw_fields: tuple[str, ...],
    economic_meaning: str,
    timestamp_source: str,
    asof_rule: str,
    prior_diagnostic: str,
    prior_primary: str,
    prior_full: str,
    status: str,
    i5: bool,
    i6: bool,
    i9: bool,
    i10: bool,
    state: str = "",
    begin: str = "",
    invalidate: str = "",
    notes: str = "",
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"bad status {status}")
    return {
        "OBJECT_ID": object_id,
        "RAW_FIELDS": list(raw_fields),
        "ECONOMIC_MEANING": economic_meaning,
        "TIMESTAMP_SOURCE": timestamp_source,
        "CAUSAL_ASOF_RULE": asof_rule,
        "PRIOR_DIAGNOSTIC_USE": prior_diagnostic,
        "PRIOR_PRIMARY_ALPHA_USE": prior_primary,
        "PRIOR_FULL_STRATEGY_USE": prior_full,
        "STATUS": status,
        "I5_NOT_EXACT_CLOSED_PRIMARY": bool(i5),
        "I6_NOT_NEW_TRANSFORM_OF_OLD_OBJECT": bool(i6),
        "I9_DIFFERENT_DECISION_MECHANISM": bool(i9),
        "I10_COMPLETE_STRATEGY_WITHOUT_THRESHOLD_SEARCH": bool(i10),
        "WHAT_DECISION_STATE_IT_COULD_REPRESENT": state,
        "HOW_STATE_CAN_BEGIN": begin,
        "HOW_STATE_CAN_INVALIDATE": invalidate,
        "NOTES": notes,
        "CURRENT_42_RETUNE": CURRENT_42_RETUNE,
        "PREOPEN_EXECUTION_VALID": PREOPEN_EXECUTION_VALID,
    }


def catalog() -> list[dict[str, Any]]:
    asof = (
        "Latest payload with capture_event_epoch<=decision t. "
        "Board freshness AskTime/BidTime then ingress. Never CurrentPriceTime."
    )
    return [
        _base(
            "PRICE_PATH",
            raw_fields=("CurrentPrice", "CurrentPriceTime", "OHLC_1m", "HTF_bars"),
            economic_meaning="Traded/quoted price trajectory and completed-bar path.",
            timestamp_source="completed-bar finalize; CurrentPrice is print not board clock",
            asof_rule=asof,
            prior_diagnostic="PMMD labels; V4 resistance episode RCA",
            prior_primary="SIMPLE_FULL, SYSTEMATIC_STATE_TRANSITION, C1 MTF, SIMPLE_TECH_PULLBACK, V4, FCMD O1/O2/O3 predicates",
            prior_full="Current 42 Full Causal line; Recovery Sequence; ST",
            status="CLOSED_PRIMARY_ALPHA_OBJECT",
            i5=False,
            i6=False,
            i9=False,
            i10=False,
            notes="Do not reopen O1/O2/O3 or bar-path identities.",
        ),
        _base(
            "TRADED_ACTIVITY",
            raw_fields=("TradingVolume", "volume_1m", "participation"),
            economic_meaning="Cumulative and incremental traded size.",
            timestamp_source="payload TradingVolume at ingress; completed 1m volume",
            asof_rule=asof,
            prior_diagnostic="E1_X14 activity overlays",
            prior_primary="PARTICIPATION_ONSET, PFQ, E1_X6 FCRR volume confirm",
            prior_full="Participation Onset Full Strategy; PFQ",
            status="CLOSED_PRIMARY_ALPHA_OBJECT",
            i5=False,
            i6=False,
            i9=False,
            i10=False,
        ),
        _base(
            "TOP_OF_BOOK_STATE",
            raw_fields=("Buy1.Price", "Buy1.Qty", "Sell1.Price", "Sell1.Qty", "AskPrice", "BidPrice"),
            economic_meaning="True L1 bid=Buy1, true L1 ask=Sell1 (kabu BidPrice≈Sell1 inverted).",
            timestamp_source="Buy1.Time / Sell1.Time plus AskTime/BidTime / ingress",
            asof_rule=asof + " Pre-open L1 is not executable X1.",
            prior_diagnostic="causal board L1 qty/imbalance; PBv2 top imbalance",
            prior_primary="IOAR L1 trade-vs-quote; board_support L1 qty ratio",
            prior_full="X1 Ask1 execution; board_support filter on Full Strategies",
            status="CLOSED_PRIMARY_ALPHA_OBJECT",
            i5=False,
            i6=False,
            i9=False,
            i10=False,
            notes="Also USED_AS_EXECUTION_ONLY for X1. Do not retune board_support.",
        ),
        _base(
            "FULL_DEPTH_GEOMETRY",
            raw_fields=FULL_DEPTH_RAW_FIELDS,
            economic_meaning=(
                "Displayed 10-level resting book geometry: presence/absence and "
                "price-order of stored Buy1..Buy10 / Sell1..Sell10, including holes "
                "and contiguous stacks. Not a weighted imbalance scalar."
            ),
            timestamp_source=(
                "Quote-update event clock=ingress received_at; freshness=AskTime/BidTime. "
                "Buy2..Buy10 and Sell2..Sell10 have Price/Qty only and inherit the "
                "carrying quote-update event. Never CurrentPriceTime."
            ),
            asof_rule=asof + " Pre-open depth is context only; PREOPEN_EXECUTION_VALID=false.",
            prior_diagnostic=(
                "SIMPLE_TECH causal board RCA: bid_depth/ask_depth/depth_imbalance "
                "as pre-cap quality diagnostics (sum of displayed qty, then imbalance)."
            ),
            prior_primary=(
                "CZB2/canonical_depth_imbalance and depth_imb_d3/d5/d10 are "
                "weighted-imbalance FEATURES of the same raw fields, not a Full "
                "Strategy identity of discrete ladder structure/migration. "
                "PBv2 board tokens are p33/p66 transforms of imbalance."
            ),
            prior_full=(
                "Never a frozen Complete Full Causal ENTRY/EXIT identity. "
                "Full Causal harvest board_row uses L1 only."
            ),
            status="UNDEREXPLORED_CAUSAL_OBJECT",
            i5=True,
            i6=True,
            i9=True,
            i10=True,
            state=FULL_DEPTH_STATE,
            begin=FULL_DEPTH_BEGIN,
            invalidate=FULL_DEPTH_INVALIDATE,
            notes=(
                "I6 holds only if ALLOWED_INFORMATION is discrete structure/migration "
                "and FORBIDDEN_TRANSFORMATIONS block all prior imbalance formulas. "
                "depth_ratio_3/5/10 are not separate objects."
            ),
        ),
        _base(
            "DEPTH_MIGRATION",
            raw_fields=FULL_DEPTH_RAW_FIELDS,
            economic_meaning="Change of displayed depth across successive as-of books.",
            timestamp_source="Same as FULL_DEPTH_GEOMETRY; state is pairwise snapshot delta.",
            asof_rule=asof,
            prior_diagnostic="causal board L1 bid_depletion_event_n / ask_add_event_n in 5s window",
            prior_primary="None as multi-level ladder migration",
            prior_full="Not a separate Full Strategy object; invalidate path of FULL_DEPTH_GEOMETRY",
            status="DIAGNOSTIC_ONLY",
            i5=True,
            i6=False,
            i9=False,
            i10=False,
            notes="L1 depletion already diagnostic. Multi-level migration is not a second family.",
        ),
        _base(
            "QUOTE_UPDATE_DYNAMICS",
            raw_fields=("received_at", "AskTime", "BidTime", "quote inter-arrival"),
            economic_meaning="Quote update rate, survival, burst of quote events.",
            timestamp_source="ingress received_at sequence",
            asof_rule=asof,
            prior_diagnostic="OR update_count overlay",
            prior_primary="UEIA G4 persistence (survival/replenish)",
            prior_full="UEIA (no validated edge). Not a new object via different windows.",
            status="CLOSED_PRIMARY_ALPHA_OBJECT",
            i5=False,
            i6=False,
            i9=False,
            i10=False,
            notes="event_rate_30s/60s/120s are not separate objects.",
        ),
        _base(
            "TRADE_FLOW_DYNAMICS",
            raw_fields=("TradingVolume", "CurrentPrice", "Buy1", "Sell1", "derived trade_side"),
            economic_meaning="Aggressor-side flow inferred from prints vs L1 and volume delta.",
            timestamp_source="print clock vs L1; derived in IOAR/UEIA loaders (no native TradeSide field)",
            asof_rule=asof,
            prior_diagnostic="ENTRY_EDGE_MASTER activity",
            prior_primary="IOAR trade-side; UEIA G2 AGGRESSIVE_FLOW / G3 FLOW_EFFICIENCY; PFQ",
            prior_full="IOAR strategy (rejected); UEIA (no validated edge)",
            status="CLOSED_PRIMARY_ALPHA_OBJECT",
            i5=False,
            i6=False,
            i9=False,
            i10=False,
        ),
        _base(
            "ABSORPTION_REPLENISHMENT",
            raw_fields=("derived trade_side", "Buy1.Qty", "Sell1.Qty", "CurrentPrice"),
            economic_meaning=IOAR_EXACT_MECHANISM,
            timestamp_source="IOAR episode clock on L1 + prints",
            asof_rule=asof,
            prior_diagnostic="IOAR state machine S1–S5",
            prior_primary="IOAR exact hypothesis",
            prior_full="IOAR_STRATEGY_REJECTED / IOAR_HYPOTHESIS_NO_EDGE",
            status="EXACT_MECHANISM_CLOSED_OBJECT_BROADER",
            i5=False,
            i6=False,
            i9=False,
            i10=False,
            notes="Exact mechanism closed. Broader order-book family is not automatically closed.",
        ),
        _base(
            "OPENING_AUCTION_CONTEXT",
            raw_fields=(
                "MarketOrderBuyQty",
                "MarketOrderSellQty",
                "OverSellQty",
                "UnderBuyQty",
                "pre-open Buy1..Buy10",
                "pre-open Sell1..Sell10",
                "OpeningPrice",
            ),
            economic_meaning="Frozen pre-09:00 auction imbalance and indicative depth. Not executable.",
            timestamp_source="pre-open payload received_at < 09:00; OpeningPrice fills after open",
            asof_rule="Context frozen at/before 09:00 for later AM. Never treat pre-open Ask/Bid as X1.",
            prior_diagnostic="UEIA pre-open contamination (PREOPEN_EDGE_CONTAMINATION)",
            prior_primary="UEIA market-context / remaining-upside sampling used pre-open tape",
            prior_full="No Complete Full Strategy on auction context. PREOPEN_EXECUTION_VALID=false",
            status="DIAGNOSTIC_ONLY",
            i5=True,
            i6=True,
            i9=False,
            i10=False,
            notes="Static 09:00 freeze can only be a cosmetic ENTRY filter, not begin/invalidate during AM.",
        ),
        _base(
            "PRIOR_SESSION_CONTEXT",
            raw_fields=("PreviousClose", "OpeningPrice", "open_vs_previous_close", "opening_range"),
            economic_meaning="Prior close and open reference for the current session.",
            timestamp_source="PreviousClose on payload; OpeningPrice after 09:00; opening range from AM path",
            asof_rule=asof,
            prior_diagnostic="gap diagnostics",
            prior_primary="Recovery Sequence (gap-fill/reclaim semantics); OR / Open Strength",
            prior_full="Recovery Full Strategy; OR overlay",
            status="CLOSED_PRIMARY_ALPHA_OBJECT",
            i5=False,
            i6=False,
            i9=False,
            i10=False,
            notes="Do not propose gap-fill reclaim; it is Recovery. No gap-size threshold search.",
        ),
        _base(
            "CROSS_SECTIONAL_FLOW_STATE",
            raw_fields=("N_up", "rank", "first_arrival", "universe"),
            economic_meaning="Cross-symbol breadth / crowding / rank at a time.",
            timestamp_source="same AM event clock across universe",
            asof_rule=asof,
            prior_diagnostic="OR rank",
            prior_primary="CSB_MA_ONSET; C4 portfolio crowding; UEIA G5 MARKET_CONTEXT XS",
            prior_full="CSB; C4 Full Strategy",
            status="CLOSED_PRIMARY_ALPHA_OBJECT",
            i5=False,
            i6=False,
            i9=False,
            i10=False,
        ),
        _base(
            "PORTFOLIO_STATE",
            raw_fields=("occupancy", "CAP", "same_symbol", "slot_release"),
            economic_meaning="Internal book of open research slots. Not market information.",
            timestamp_source="fill/exit fill times of the strategy under test",
            asof_rule="Occupancy at fill time; slot release after EXIT fill.",
            prior_diagnostic="CAP RCA",
            prior_primary="None as ENTRY thesis",
            prior_full="Inherited CAP=5 / same-symbol / occupancy on every Full Strategy. Filter/constraint only.",
            status="USED_AS_FILTER_ONLY",
            i5=True,
            i6=False,
            i9=False,
            i10=False,
        ),
        _base(
            "EVENT_FLOW_DYNAMICS",
            raw_fields=(
                "event inter-arrival",
                "quote survival",
                "quote withdrawal",
                "quote replenishment",
                "trade-side flow",
                "buy_trade_ratio",
                "event/update burst",
                "price response per event",
            ),
            economic_meaning="Microstructure event clock and quote/trade burst geometry.",
            timestamp_source="ingress sequence; quote Times; derived trade_side",
            asof_rule=asof,
            prior_diagnostic="ENTRY_EDGE_MASTER; Board Dynamic",
            prior_primary="UEIA G2–G4; IOAR replenishment/survival",
            prior_full="UEIA; IOAR; Board Dynamic. Semantics equivalent → NOT NEW.",
            status="CLOSED_PRIMARY_ALPHA_OBJECT",
            i5=False,
            i6=False,
            i9=False,
            i10=False,
            notes="Different event_rate windows are not new objects.",
        ),
    ]


def prior_use_rows() -> list[dict[str, Any]]:
    return [
        {"PROGRAM": "SIMPLE_FULL", "OBJECT_FAMILY": "PRICE_PATH", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "SYSTEMATIC_STATE_TRANSITION", "OBJECT_FAMILY": "PRICE_PATH", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "RECOVERY_SEQUENCE", "OBJECT_FAMILY": "PRIOR_SESSION_CONTEXT+PRICE_PATH", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "PARTICIPATION_ONSET", "OBJECT_FAMILY": "TRADED_ACTIVITY", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "C1_MULTI_TIMEFRAME", "OBJECT_FAMILY": "PRICE_PATH", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "C4_PORTFOLIO_CROWDING", "OBJECT_FAMILY": "CROSS_SECTIONAL_FLOW_STATE+PORTFOLIO_STATE", "ROLE": "PRIMARY_ALPHA+FILTER", "NOVELTY": "CLOSED"},
        {"PROGRAM": "CSB", "OBJECT_FAMILY": "CROSS_SECTIONAL_FLOW_STATE", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "V4_RESISTANCE_EPISODE", "OBJECT_FAMILY": "PRICE_PATH", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "SIMPLE_TECH_PULLBACK", "OBJECT_FAMILY": "PRICE_PATH+TOP_OF_BOOK_STATE", "ROLE": "PRIMARY_ALPHA+FILTER", "NOVELTY": "CLOSED"},
        {"PROGRAM": "E1_X6_FCRR", "OBJECT_FAMILY": "TRADED_ACTIVITY+PRICE_PATH", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "E1_X14_ACTIVITY", "OBJECT_FAMILY": "TRADED_ACTIVITY+EVENT_FLOW_DYNAMICS", "ROLE": "OVERLAY", "NOVELTY": "CLOSED"},
        {"PROGRAM": "RPFE", "OBJECT_FAMILY": "PRICE_PATH", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "PFQ", "OBJECT_FAMILY": "TRADE_FLOW_DYNAMICS+TRADED_ACTIVITY", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "OR_OPEN_STRENGTH", "OBJECT_FAMILY": "PRIOR_SESSION_CONTEXT+PRICE_PATH", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "PBv2", "OBJECT_FAMILY": "TOP_OF_BOOK_STATE+FULL_DEPTH_GEOMETRY_AS_IMBALANCE_FORMULA", "ROLE": "FEATURE/TOKEN", "NOVELTY": "FORMULA_NOT_STRUCTURE_OBJECT"},
        {"PROGRAM": "BOARD_DYNAMIC", "OBJECT_FAMILY": "EVENT_FLOW_DYNAMICS+TOP_OF_BOOK_STATE", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "ENTRY_EDGE_MASTER", "OBJECT_FAMILY": "EVENT_FLOW_DYNAMICS+TRADE_FLOW_DYNAMICS", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "CLOSED"},
        {"PROGRAM": "UEIA", "OBJECT_FAMILY": "TRADE_FLOW_DYNAMICS+QUOTE_UPDATE_DYNAMICS+OPENING_AUCTION_CONTEXT", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "UEIA_NO_VALIDATED_EDGE_DO_NOT_REOPEN"},
        {"PROGRAM": "IOAR", "OBJECT_FAMILY": "ABSORPTION_REPLENISHMENT", "ROLE": "PRIMARY_ALPHA", "NOVELTY": "EXACT_MECHANISM_CLOSED"},
        {"PROGRAM": "CZB2_CANONICAL_DEPTH_IMBALANCE", "OBJECT_FAMILY": "FULL_DEPTH_GEOMETRY", "ROLE": "FEATURE_FORMULA", "NOVELTY": "NOT_STRUCTURE_OBJECT"},
        {"PROGRAM": "CAUSAL_BOARD_RCA", "OBJECT_FAMILY": "FULL_DEPTH_GEOMETRY", "ROLE": "DIAGNOSTIC_FILTER", "NOVELTY": "SUM_QTY_NOT_LADDER_IDENTITY"},
        {"PROGRAM": "FCMD_O1_O2_O3", "OBJECT_FAMILY": "PRICE_PATH+TOP_OF_BOOK_STATE", "ROLE": "FULL_STRATEGY", "NOVELTY": "LINE_CLOSED_NO_RETUNE"},
        {"PROGRAM": "FREEZE_V3", "OBJECT_FAMILY": "COMPLETED_BARS+BOARD_SUPPORT", "ROLE": "INVENTORY", "NOVELTY": "NO_JUSTIFIED_NEW_ARCHITECTURE_V3"},
    ]


def apply_schema(rows: list[dict[str, Any]], schema: dict[str, Any]) -> list[dict[str, Any]]:
    cov = dict(schema.get("coverage_by_object") or {})
    out = []
    for row in rows:
        oid = str(row["OBJECT_ID"])
        c = dict(cov.get(oid) or schema.get("default_coverage") or {})
        rec = dict(row)
        rec["DEV_DAY_N"] = int(c.get("DEV_DAY_N") or 0)
        rec["SYMBOL_COVERAGE"] = int(c.get("SYMBOL_COVERAGE") or 0)
        rec["SESSION_COVERAGE"] = str(c.get("SESSION_COVERAGE") or "")
        rec["STORED_IN_SEALED_DEV"] = bool(c.get("STORED") or False)
        rec["CAUSALLY_TIMESTAMPED"] = bool(c.get("CAUSALLY_TIMESTAMPED") or False)
        rec["I1_STORED"] = bool(rec["STORED_IN_SEALED_DEV"])
        rec["I2_CAUSAL_TIMESTAMP"] = bool(rec["CAUSALLY_TIMESTAMPED"])
        rec["I3_DEV_DAY_N_GE_8"] = int(rec["DEV_DAY_N"]) >= 8
        rec["I4_BROAD_COVERAGE"] = bool(c.get("BROAD") or False)
        rec["I7_NO_EXTERNAL"] = True
        rec["I8_NO_FUTURE_LABEL"] = True
        rec["ELIGIBLE"] = bool(
            rec["I1_STORED"]
            and rec["I2_CAUSAL_TIMESTAMP"]
            and rec["I3_DEV_DAY_N_GE_8"]
            and rec["I4_BROAD_COVERAGE"]
            and rec["I5_NOT_EXACT_CLOSED_PRIMARY"]
            and rec["I6_NOT_NEW_TRANSFORM_OF_OLD_OBJECT"]
            and rec["I7_NO_EXTERNAL"]
            and rec["I8_NO_FUTURE_LABEL"]
            and rec["I9_DIFFERENT_DECISION_MECHANISM"]
            and rec["I10_COMPLETE_STRATEGY_WITHOUT_THRESHOLD_SEARCH"]
            and rec["STATUS"] == "UNDEREXPLORED_CAUSAL_OBJECT"
        )
        out.append(rec)
    return out


def eligible_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    got = [r for r in rows if r.get("ELIGIBLE")]
    got.sort(key=lambda r: str(r["OBJECT_ID"]))
    if len(got) > 3:
        got = got[:3]
    return got


def _rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    oid = str(row["OBJECT_ID"])
    distinction = 0 if oid == "FULL_DEPTH_GEOMETRY" else 1
    ts = 0 if row.get("I2_CAUSAL_TIMESTAMP") else 1
    cov = -int(row.get("DEV_DAY_N") or 0)
    econ = 0 if row.get("ECONOMIC_MEANING") else 1
    complete = 0 if row.get("HOW_STATE_CAN_BEGIN") and row.get("HOW_STATE_CAN_INVALIDATE") else 1
    params = 0 if oid == "FULL_DEPTH_GEOMETRY" else 1
    return (distinction, ts, cov, econ, complete, params, oid)


def select_one(eligible: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not eligible:
        return None
    ordered = sorted(eligible, key=_rank_key)
    return ordered[0]


def freeze_spec(selected: dict[str, Any] | None, *, eligible_n: int) -> dict[str, Any]:
    if selected is None:
        body = {
            "ANALYSIS_ID": "DISCOVERY_INFORMATION_OBJECT_EXPANSION_DECISION_V1",
            "SELECTED": False,
            "OBJECT_ID": None,
            "ELIGIBLE_INFORMATION_OBJECT_N": int(eligible_n),
            "CURRENT_42_RETUNE": False,
            "ABSORPTION_REVERSAL_EXACT_MECHANISM_SELECTED": False,
            "UEIA_REPRODUCTION": False,
        }
        return {**body, "INFORMATION_OBJECT_SPEC_SHA256": dumps_sha256(body)}
    body = {
        "ANALYSIS_ID": "DISCOVERY_INFORMATION_OBJECT_EXPANSION_DECISION_V1",
        "SELECTED": True,
        "OBJECT_ID": selected["OBJECT_ID"],
        "RAW_FIELD_SET": list(FULL_DEPTH_RAW_FIELDS) if selected["OBJECT_ID"] == "FULL_DEPTH_GEOMETRY" else list(selected.get("RAW_FIELDS") or []),
        "CAUSAL_TIMESTAMP_SEMANTICS": selected.get("TIMESTAMP_SOURCE"),
        "ASOF_RULE": selected.get("CAUSAL_ASOF_RULE"),
        "PRIOR_USE_BOUNDARY": (
            "Prior use of Buy1..Buy10/Sell1..Sell10 is imbalance formulas "
            "(canonical_depth_imbalance, depth_imb_d3/d5/d10, PBv2 tokens) and "
            "diagnostic qty sums (causal board bid_depth). Those transforms are "
            "outside this object. IOAR exact absorption-replenishment is closed. "
            "L1 board_support remains a closed execution/filter object."
        ),
        "ALLOWED_INFORMATION": (
            "Discrete displayed 10-level structure and its migration: Qty>0 vs Qty==0 "
            "at stored levels; strict price-order of Buy1..Buy10 / Sell1..Sell10; "
            "hole vs contiguous stack; successive as-of snapshot change of those "
            "discrete states. Whole 10-level book as one object."
        ),
        "FORBIDDEN_TRANSFORMATIONS": list(FORBIDDEN_DEPTH_TRANSFORMS),
        "PREOPEN_EXECUTION_VALID": False,
        "CURRENT_42_RETUNE": False,
        "ABSORPTION_REVERSAL_EXACT_MECHANISM_SELECTED": False,
        "UEIA_REPRODUCTION": False,
        "THRESHOLD_VARIATION_ONLY": False,
        "ENTRY_RULE": None,
        "EXIT_RULE": None,
        "ELIGIBLE_INFORMATION_OBJECT_N": int(eligible_n),
        "WHAT_DECISION_STATE_IT_COULD_REPRESENT": selected.get("WHAT_DECISION_STATE_IT_COULD_REPRESENT"),
        "HOW_STATE_CAN_BEGIN": selected.get("HOW_STATE_CAN_BEGIN"),
        "HOW_STATE_CAN_INVALIDATE": selected.get("HOW_STATE_CAN_INVALIDATE"),
    }
    return {**body, "INFORMATION_OBJECT_SPEC_SHA256": dumps_sha256(body)}


def decide(eligible_n: int, selected: dict[str, Any] | None, spec: dict[str, Any]) -> dict[str, Any]:
    if int(eligible_n) >= 1 and selected is not None:
        return {
            "CASE": "A",
            "VERDICT": CASE_A,
            "NEXT": NEXT_A,
            "ELIGIBLE_INFORMATION_OBJECT_N": int(eligible_n),
            "SELECTED_OBJECT_ID": selected["OBJECT_ID"],
            "INFORMATION_OBJECT_SPEC_SHA256": spec["INFORMATION_OBJECT_SPEC_SHA256"],
            "CURRENT_42_RETUNE": False,
            "THRESHOLD_VARIATION_ONLY": False,
            "EXACT_IOAR": False,
            "EXACT_UEIA_REPRODUCTION": False,
            "EXACT_CLOSED_BOARD_MECHANISM": False,
            "ECONOMICS_RUN": False,
            "OUTCOME_READ_N": 0,
        }
    return {
        "CASE": "B",
        "VERDICT": CASE_B,
        "NEXT": NEXT_B,
        "ELIGIBLE_INFORMATION_OBJECT_N": 0,
        "SELECTED_OBJECT_ID": None,
        "INFORMATION_OBJECT_SPEC_SHA256": spec["INFORMATION_OBJECT_SPEC_SHA256"],
        "CURRENT_42_RETUNE": False,
        "THRESHOLD_VARIATION_ONLY": False,
        "EXACT_IOAR": False,
        "EXACT_UEIA_REPRODUCTION": False,
        "EXACT_CLOSED_BOARD_MECHANISM": False,
        "ECONOMICS_RUN": False,
        "OUTCOME_READ_N": 0,
    }


assert CURRENT_42_RETUNE is False
assert PREOPEN_EXECUTION_VALID is False
assert "UNEXPLORED" not in STATUSES
