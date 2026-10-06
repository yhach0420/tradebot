"""Exact signed-volume construction. Semantics come from the builder, not from field names."""
from __future__ import annotations

from typing import Any, Optional

SOURCE_FILE = "src/research/simple_tech_entry_family/bars.py"
SOURCE_FUNCTION = "SymbolBarBuilder.on_event"
INPUT_ADAPTER = "src/research/simple_tech_entry_family/harvest.py:_board_row"


def _field(name: str, **extra: Any) -> dict[str, Any]:
    return {"field": name, "source_file": SOURCE_FILE, "source_function": SOURCE_FUNCTION, **extra}


def semantics() -> dict[str, Any]:
    """Document the executable rule. This does not create a second candidate."""
    common = {
        "source_inputs": [
            "event time et",
            "px from CurrentPrice",
            "cum_vol from TradingVolume",
            "bid from Buy1.Price, else BidPrice",
            "ask from Sell1.Price, else AskPrice",
            "continuous from the executable continuous-board gate",
        ],
        "input_adapter": INPUT_ADAPTER,
        "time_aggregation": "Sum of positive TradingVolume deltas whose event minute is the bar minute. Minute M finalizes on the first valid continuous event of minute M+1. That finalizing event is not added to minute M.",
        "causal_timestamp_semantics": "Classification uses only the event being applied: its price, its bid, its ask, and the increase in cumulative TradingVolume since the previous accepted event. No later bar and no later price are used.",
        "reset_boundary": "One SymbolBarBuilder per symbol per AM session. last_px and last_cum do not carry overnight. close_session drops the incomplete last minute.",
        "missing_data_behavior": "A non-finite or negative cumulative volume does not change last_cum. A cumulative decrease updates last_cum and adds no delta. A non-continuous event updates last_cum and last_px but adds nothing to the bar. A continuous event with volume and no finite positive price adds the delta to total bar volume only. A missing ask skips the ask bucket. A missing bid skips the bid bucket. Inside-spread volume stays in total volume and in neither bucket. Buckets initialize at 0, so 0 is not a missing field.",
    }
    return {
        "resolved": True,
        "equivalent_contract": "at-ask / at-bid snapshot classification of the volume delta",
        "exchange_aggressor_flag": False,
        "ask_vol": _field(
            "ask_vol",
            exact_meaning="Positive TradingVolume delta on a continuous, finite-price event is added when that event price is greater than or equal to that event ask. Ask is tested before bid.",
            classification_rule="dvol > 0 and ask finite and ask > 0 and price + 1e-12 >= ask",
            buyer_aggressing_exchange_flag=False,
            at_ask_or_through_ask=True,
            **common,
        ),
        "bid_vol": _field(
            "bid_vol",
            exact_meaning="The same delta is added to bid volume only when it was not added to ask volume and the event price is less than or equal to that event bid.",
            classification_rule="not ask-classified and dvol > 0 and bid finite and bid > 0 and price - 1e-12 <= bid",
            seller_aggressing_exchange_flag=False,
            at_bid_or_through_bid=True,
            **common,
        ),
        "up_vol": _field(
            "up_vol",
            exact_meaning="Positive delta on a continuous finite-price event whose price is above the builder's previous price. This is a tick test, not ask volume.",
            classification_rule="dvol > 0 and last_px finite and price > last_px + 1e-12",
            audit_only=True,
            candidate_defined=False,
            **common,
        ),
        "down_vol": _field(
            "down_vol",
            exact_meaning="Positive delta on a continuous finite-price event whose price is below the builder's previous price. This is a tick test, not bid volume.",
            classification_rule="dvol > 0 and last_px finite and price < last_px - 1e-12",
            audit_only=True,
            candidate_defined=False,
            **common,
        ),
        "unclassified_volume": "bar volume minus ask_vol minus bid_vol. It stays inside total volume. It includes inside-spread prints, prints with a missing quote, and continuous volume that arrived without a finite positive price.",
        "locked_or_crossed_book": "A print that is both at or above the ask and at or below the bid is counted only as ask_vol.",
        "multi_trade_delta": "The whole cumulative increase since the previous event is labeled by this event's price and this event's quotes.",
    }


def repair_spec() -> dict[str, Any]:
    return {
        "REPAIR_ID": "SYMBOL_SETUP_DIRECTIONAL_VOLUME_CONFIRMATION_V1",
        "STAGE": "VOLUME_PARTICIPATION",
        "ROLE": "ENTRY_PARTICIPATION_ONLY",
        "BASELINE_VOLUME": "volume[t] > 0 AND volume[t] >= 1.5 * median(prior 5 completed-bar volumes)",
        "REPAIRED_VOLUME": "volume[t] > 0 AND volume[t] >= 1.5 * median(prior 5 completed-bar volumes) AND ask_vol[t] > bid_vol[t]",
        "BUY_VOLUME_DOMINANT": "ask_vol[t] > bid_vol[t]",
        "comparison": "strict_greater",
        "ties_pass": False,
        "threshold_searched": False,
        "relative_volume_replaced": False,
        "unknown_volume_dropped": False,
        "PRICE_ACTION": "close[t] > EMA9[t] AND close[t] > high[t-1] AND close[t] <= BB_upper[t]",
        "RANGE_BREAK_COMBINED": False,
        "UP_DOWN_CANDIDATE_DEFINED": False,
        "stage_order": [
            "MA_TREND",
            "BB_LOCATION",
            "RCI_REVERSAL",
            "VOLUME_PARTICIPATION",
            "PRICE_ACTION_TRIGGER",
            "BOARD_SUPPORT_VETO",
        ],
        "primary_population": "DIRECTIONAL_VOLUME_PLUS_PRICE_ACTION_PRE_BOARD",
        "primary_horizon_sec": 180,
        "horizons_sec": [30, 60, 180, 300],
        "metrics": ["RAW_MID_MOVE", "BID_ANCHOR_TO_FUTURE_BID", "ASK_TO_BID"],
        "denominator": "ask0",
        "quote_clock": "first fresh two-sided quote at or after signal finalize; future quote at or after that quote plus the horizon",
        "ask_vol_rule": "price >= event ask",
        "bid_vol_rule": "not ask-classified and price <= event bid",
    }
