"""At most three complete Full Strategy proposals. No placeholders. No PnL."""
from __future__ import annotations

from typing import Any

from research.new_full_strategy_architecture_precommit_v1 import AM_END_HM, AM_START_HM, SESSION
from research.simple_full_strategy_discovery_v1 import BOARD_FRESHNESS_SEC, MIN_QTY, POSITION_CAP, SHARES
from research.simple_tech_entry_family import EMA_LONG, EMA_SHORT, EMA_SLOPE_BARS, WARMUP_BARS
from research.systematic_state_transition_library_precommit_v1.spec import execution_contract
from research.systematic_state_transition_library_precommit_v1.state_registry import EVENT_TS

TREND_UP_DEF = (
    "simple_tech_entry_family.stages.trend_up: "
    f"ema{int(EMA_SHORT)}[i] > ema{int(EMA_LONG)}[i] AND "
    f"ema{int(EMA_LONG)}[i] > ema{int(EMA_LONG)}[i-{int(EMA_SLOPE_BARS)}]; "
    f"WARMUP_BARS={int(WARMUP_BARS)}. Completed 1m bars only."
)

X1 = execution_contract()
PORTFOLIO_COMMON = {
    "SHARES": int(SHARES),
    "CAP": int(POSITION_CAP),
    "CAP_ROLE": "occupancy constraint, not a quality filter",
    "same_symbol": True,
    "SAME_SYMBOL_BEHAVIOR": "reject SIGNAL if this symbol already occupies a slot",
    "OCCUPANCY_BEHAVIOR": "slot count increments at fill_t; decrements at actual EXIT fill_t; SIGNAL rejected while count>=CAP",
    "SLOT_RELEASE": "held until actual EXIT fill, not at technical fire",
    "REENTRY": True,
    "REENTRY_RULE": "allowed after slot release for that symbol; no extra cooldown",
    "SESSION": SESSION,
    "SESSION_WINDOW": {"start_hm": list(AM_START_HM), "end_hm": list(AM_END_HM), "tz": "Asia/Tokyo"},
        "SESSION_CLOSE": (
            "operational flatten: simple_full_strategy_discovery_v1.exits.last_session_bid; "
            "last fresh valid Bid1 at/before AM session end after fill_t; 100-share SELL; "
            "fill=Bid1. Not a technical timeout screen."
        ),
    "event_time_causal": True,
    "SIZING": False,
}


def _common_execution() -> dict[str, Any]:
    return {
        **X1,
        "BOARD_USE": "execution freshness and marketability only",
        "BOARD_PRIMARY_ALPHA": False,
        "BOARD_FRESHNESS_SEC": float(BOARD_FRESHNESS_SEC),
        "MIN_QTY": float(MIN_QTY),
        "SEARCHED_THIS_RUN": False,
    }


def proposals() -> list[dict[str, Any]]:
    p1 = {
        "ARCHITECTURE_ID": "CSB_MA_ONSET_X1_Z_MA_LOSS",
        "CORE_MECHANISM": (
            "Cross-sectional breadth of the canonical MA-trend-up state expanding "
            "(N_up[i] > N_up[i-1] on completed 1m) is a market latch. A name becomes "
            "eligible only as a pulse: that name's trend_up turns FALSE→TRUE on the same bar."
        ),
        "CAUSAL_EVENT_SEQUENCE": [
            "Completed 1m bar i finalizes at first valid continuous-market event of minute M+1.",
            "Compute trend_up(s,i) for every warmed-up AM 1m universe name using bars[<=i] only.",
            "N_up[i] = count of True. Breadth expanding iff N_up[i] > N_up[i-1] (false if i-1 missing).",
            "Name onset iff trend_up(s,i) and not trend_up(s,i-1).",
            "SIGNAL iff breadth expanding AND name onset.",
            "X1 first fresh Ask1 at/after finalize_t.",
            "EXIT fire on later completed bar where trend_up(s) is false; then first causal Bid1.",
            "Else SESSION_CLOSE.",
        ],
        "WHAT_INFORMATION_IS_USED": "Canonical 1m MA trend_up per name, plus the count of names for which it is true.",
        "WHEN_IT_BECOMES_KNOWN": EVENT_TS,
        "WHAT_EVENT_CREATES_ELIGIBILITY": "Joint pulse: market breadth of trend_up expands AND this name's trend_up onset.",
        "WHAT_STATE_IS_MAINTAINED": "Per-name trend_up bit; market N_up; portfolio occupancy/same-symbol slots.",
        "WHAT_CAUSES_ENTRY": "SIGNAL as defined; not standing trend_up and not persist-one-extra-bar.",
        "WHAT_CAUSES_EXIT": "This name's trend_up becomes false after fill, or AM session close.",
        "HOW_PORTFOLIO_STATE_CHANGES_FUTURE_DECISIONS": "CAP occupancy and same-symbol occupancy block new SIGNALS until slot release; reentry allowed after release. Occupancy is not a ranking filter.",
        "ENTRY_STATE_MACHINE": {
            "STATES": ["IDLE", "SIGNAL"],
            "CLOCK": "completed 1m bar index i",
            "UNIVERSE": "DEV-day AM 1m completed-bar universe (same harvest universe class as ST/C1 1m builders); no extra symbol filter",
            "PREDICATE_NAME": "trend_up",
            "PREDICATE_SOURCE": TREND_UP_DEF,
            "BREADTH": "N_up[i] = #{s: trend_up(s,i)}; expanding iff N_up[i] > N_up[i-1]; no level threshold",
            "ONSET": "trend_up(s,i) AND NOT trend_up(s,i-1)",
            "SIGNAL": "expanding AND onset",
            "NOT": ["PERSIST_NEXT extra bar", "HANDOFF_NEXT A→B", "HTF confirm", "VWAP", "TopK", "strongest-symbol"],
        },
        "EXECUTION_RULE": _common_execution(),
        "EXIT_STATE_MACHINE": {
            "EXIT_ID": "Z_MA_TREND_LOSS",
            "CLOCK": "completed 1m bars with finalize_t > fill_t",
            "TECHNICAL_FIRE": "first i where trend_up(symbol, i) is false",
            "AFTER_TRIGGER": "EXIT_PENDING; first fresh valid Bid1; 100-share SELL; fill=Bid1",
            "SESSION_CLOSE": "operational",
            "VWAP_EXIT_USED": False,
            "JOINT_WITH_ENTRY": True,
            "SOURCE_PREDICATE": TREND_UP_DEF,
            "SOURCE_FUNCTION": "invert of simple_tech_entry_family.stages.trend_up after fill",
        },
        "CAP_BEHAVIOR": PORTFOLIO_COMMON["CAP_ROLE"],
        "SAME_SYMBOL_BEHAVIOR": PORTFOLIO_COMMON["SAME_SYMBOL_BEHAVIOR"],
        "OCCUPANCY_BEHAVIOR": PORTFOLIO_COMMON["OCCUPANCY_BEHAVIOR"],
        "SLOT_RELEASE": PORTFOLIO_COMMON["SLOT_RELEASE"],
        "REENTRY": PORTFOLIO_COMMON["REENTRY_RULE"],
        "SESSION_CLOSE": PORTFOLIO_COMMON["SESSION_CLOSE"],
        "PORTFOLIO": dict(PORTFOLIO_COMMON),
        "VWAP_ENTRY_USED": False,
        "VWAP_EXIT_USED": False,
        "VWAP_FILTER_USED": False,
        "BOARD_PRIMARY_ALPHA": False,
        "EXPECTED_COVERAGE_REASON": (
            "AM 1m clock over a multi-name universe produces repeated onset pulses on days "
            "when breadth expands; CAP=5 occupancy can fill several slots per morning. "
            "Coverage gates are not measured in this run."
        ),
        "STRUCTURAL_NOVELTY_REASON": (
            "Decision object is market-wide count dynamics of one canonical MA state gating "
            "a per-name onset pulse. Not ST persist/handoff, not same-name HTF confirm, "
            "not C4 first-arrival uniqueness, not participation volume-onset, not VWAP reclaim."
        ),
        "CLOSED_LINEAGE_COMPARE": {
            "SIMPLE_FULL": "E1 is Close>prev AND Close>VWAP first-cross, not MA trend_up breadth.",
            "ST": "ST persist requires the state still true one extra bar; ST handoff is A then B. No cross-sectional N_up.",
            "C1": "C1 confirms the SAME name on completed 3m/5m. This uses OTHER names' 1m trend_up count.",
            "C4": "C4 admits by unique candidate timestamp. This admits by expanding count of trend_up.",
            "RECOVERY": "Not a reclaim of a broken local level.",
            "PARTICIPATION": "Not T1 volume-percentile onset.",
            "PFQ": "Not PFQ overlay.",
            "X9": "X9 audited PFQ universe regime; this is not a PFQ split and not rank<=10/high-nearness.",
            "C4_PORTFOLIO_CROWDING": "Different extra-name statistic and different trigger (onset vs inherited ST signal).",
            "E4": "E4 is VWAP reclaim. This uses no VWAP.",
            "OR": "Not production open-strength overlay.",
            "DYNAMIC_ANCHOR": "EXIT is this-name trend_up loss, not a fill-time trailing anchor.",
        },
        "CLOSED_LINEAGE_MATCH": False,
        "DISCRETIONARY_DOF": 0,
        "SIGNAL_PRIMITIVE_N": 1,
        "STATE_MACHINE_SIZE": 2,
        "COVERAGE_PLAUSIBILITY_RANK": 0,
        "IMPLEMENTATION_AMBIGUITY_RANK": 1,
        "PLACEHOLDER": False,
    }
    p2 = {
        "ARCHITECTURE_ID": "BB_WIDTH_RELEASE_X1_Z3",
        "CORE_MECHANISM": (
            "Per-name Bollinger width contracts then expands on consecutive completed 1m bars; "
            "ENTRY pulse on first width increase after a width decrease. EXIT is Z3 two-bar weakness."
        ),
        "CAUSAL_EVENT_SEQUENCE": [
            "Width[i] = (bb_upper[i]-bb_lower[i]) / bb_mid[i] on completed 1m, existing BB 20,2.",
            "Compress bar: Width[i] < Width[i-1].",
            "SIGNAL if Width[i] > Width[i-1] AND Width[i-1] < Width[i-2].",
            "X1 Ask1. EXIT Z3 then Bid1 or SESSION_CLOSE.",
        ],
        "WHAT_INFORMATION_IS_USED": "Existing 1m Bollinger bands (period 20, sigma 2).",
        "WHEN_IT_BECOMES_KNOWN": EVENT_TS,
        "WHAT_EVENT_CREATES_ELIGIBILITY": "Onset of width expansion after one-bar contraction.",
        "WHAT_STATE_IS_MAINTAINED": "Last two width comparisons; occupancy.",
        "WHAT_CAUSES_ENTRY": "Width-release pulse.",
        "WHAT_CAUSES_EXIT": "Z3 two consecutive weak 1m bars, or session close.",
        "HOW_PORTFOLIO_STATE_CHANGES_FUTURE_DECISIONS": PORTFOLIO_COMMON["OCCUPANCY_BEHAVIOR"],
        "ENTRY_STATE_MACHINE": {
            "STATES": ["IDLE", "SIGNAL"],
            "SIGNAL": "Width[i] > Width[i-1] AND Width[i-1] < Width[i-2]",
        },
        "EXECUTION_RULE": _common_execution(),
        "EXIT_STATE_MACHINE": {
            "EXIT_ID": "Z3_TWO_BAR_WEAKNESS",
            "EXACT_DEFINITION": (
                "first-fire of two consecutive completed 1m bars k where "
                "Close[k] < Open[k] AND Close[k] < Close[k-1]"
            ),
            "AFTER_TRIGGER": "EXIT_PENDING; first fresh valid Bid1; 100-share SELL; fill=Bid1",
            "SESSION_CLOSE": "operational",
            "JOINT_WITH_ENTRY": False,
            "SOURCE_PATH": "src/research/simple_full_strategy_discovery_v1/exits.py",
            "SOURCE_FUNCTION": "technical_fire_i exit_id==Z3",
        },
        "CAP_BEHAVIOR": PORTFOLIO_COMMON["CAP_ROLE"],
        "SAME_SYMBOL_BEHAVIOR": PORTFOLIO_COMMON["SAME_SYMBOL_BEHAVIOR"],
        "OCCUPANCY_BEHAVIOR": PORTFOLIO_COMMON["OCCUPANCY_BEHAVIOR"],
        "SLOT_RELEASE": PORTFOLIO_COMMON["SLOT_RELEASE"],
        "REENTRY": PORTFOLIO_COMMON["REENTRY_RULE"],
        "SESSION_CLOSE": PORTFOLIO_COMMON["SESSION_CLOSE"],
        "PORTFOLIO": dict(PORTFOLIO_COMMON),
        "VWAP_ENTRY_USED": False,
        "VWAP_EXIT_USED": False,
        "VWAP_FILTER_USED": False,
        "BOARD_PRIMARY_ALPHA": False,
        "EXPECTED_COVERAGE_REASON": "1m width pulses can be frequent; coverage unmeasured.",
        "STRUCTURAL_NOVELTY_REASON": "New 1m boolean onset on BB width; still a per-name temporal onset on a 1m technical bit.",
        "CLOSED_LINEAGE_COMPARE": {
            "ST": "Onset of a new 1m technical boolean is the TEMPORAL_STATE_TRANSITION / C2 class (new STATE_ID + pulse).",
            "C1": "No HTF.",
            "SIMPLE_FULL": "Not an E1-E5 first-cross, but still per-name 1m technical first-cross.",
        },
        "CLOSED_LINEAGE_MATCH": True,
        "CLOSED_LINEAGE_IDS": ["SYSTEMATIC_STATE_TRANSITION"],
        "DISCRETIONARY_DOF": 0,
        "SIGNAL_PRIMITIVE_N": 1,
        "STATE_MACHINE_SIZE": 2,
        "COVERAGE_PLAUSIBILITY_RANK": 1,
        "IMPLEMENTATION_AMBIGUITY_RANK": 0,
        "PLACEHOLDER": False,
    }
    p3 = {
        "ARCHITECTURE_ID": "HTF5_MA_ONLY_X1_Z3",
        "CORE_MECHANISM": (
            "Drop 1m. Signal on first-cross of trend_up computed on completed 5m session-anchored bars only."
        ),
        "CAUSAL_EVENT_SEQUENCE": [
            "Aggregate completed 1m to 5m with origin 09:00 JST; drop partial buckets.",
            "SIGNAL on first-cross of trend_up on the HTF series at HTF finalize_t.",
            "X1 Ask1. Z3 on 1m clock after fill, or SESSION_CLOSE.",
        ],
        "WHAT_INFORMATION_IS_USED": "Completed 5m MA trend_up.",
        "WHEN_IT_BECOMES_KNOWN": "Last 1m of the 5m bucket finalizes; asof uses finalize_t<=t0.",
        "WHAT_EVENT_CREATES_ELIGIBILITY": "5m trend_up first-cross.",
        "WHAT_STATE_IS_MAINTAINED": "HTF trend_up; occupancy.",
        "WHAT_CAUSES_ENTRY": "HTF first-cross.",
        "WHAT_CAUSES_EXIT": "Z3 on 1m, or session close.",
        "HOW_PORTFOLIO_STATE_CHANGES_FUTURE_DECISIONS": PORTFOLIO_COMMON["OCCUPANCY_BEHAVIOR"],
        "ENTRY_STATE_MACHINE": {
            "STATES": ["IDLE", "SIGNAL"],
            "HTF": "HTF_5M width 300s origin 09:00",
            "SIGNAL": "trend_up first-cross on completed 5m",
        },
        "EXECUTION_RULE": _common_execution(),
        "EXIT_STATE_MACHINE": {
            "EXIT_ID": "Z3_TWO_BAR_WEAKNESS",
            "EXACT_DEFINITION": (
                "first-fire of two consecutive completed 1m bars k where "
                "Close[k] < Open[k] AND Close[k] < Close[k-1]"
            ),
            "AFTER_TRIGGER": "EXIT_PENDING; first fresh valid Bid1; 100-share SELL; fill=Bid1",
            "SESSION_CLOSE": "operational",
            "JOINT_WITH_ENTRY": False,
            "SOURCE_PATH": "src/research/simple_full_strategy_discovery_v1/exits.py",
            "SOURCE_FUNCTION": "technical_fire_i exit_id==Z3",
        },
        "CAP_BEHAVIOR": PORTFOLIO_COMMON["CAP_ROLE"],
        "SAME_SYMBOL_BEHAVIOR": PORTFOLIO_COMMON["SAME_SYMBOL_BEHAVIOR"],
        "OCCUPANCY_BEHAVIOR": PORTFOLIO_COMMON["OCCUPANCY_BEHAVIOR"],
        "SLOT_RELEASE": PORTFOLIO_COMMON["SLOT_RELEASE"],
        "REENTRY": PORTFOLIO_COMMON["REENTRY_RULE"],
        "SESSION_CLOSE": PORTFOLIO_COMMON["SESSION_CLOSE"],
        "PORTFOLIO": dict(PORTFOLIO_COMMON),
        "VWAP_ENTRY_USED": False,
        "VWAP_EXIT_USED": False,
        "VWAP_FILTER_USED": False,
        "BOARD_PRIMARY_ALPHA": False,
        "EXPECTED_COVERAGE_REASON": (
            "5m-only first-cross is sparse versus TRADE_N>=40 and 4 trades/day on a 2.5h AM session."
        ),
        "STRUCTURAL_NOVELTY_REASON": "C1 without the 1m leg is a timeframe deletion, not a new decision mechanism.",
        "CLOSED_LINEAGE_COMPARE": {
            "C1": "C1 is 1m + completed HTF of the same predicate. This is the HTF predicate alone.",
            "ST": "Different clock, same MA predicate family.",
        },
        "CLOSED_LINEAGE_MATCH": True,
        "CLOSED_LINEAGE_IDS": ["C1_MULTI_TIMEFRAME"],
        "DISCRETIONARY_DOF": 0,
        "SIGNAL_PRIMITIVE_N": 1,
        "STATE_MACHINE_SIZE": 2,
        "COVERAGE_PLAUSIBILITY_RANK": 2,
        "IMPLEMENTATION_AMBIGUITY_RANK": 0,
        "PLACEHOLDER": False,
    }
    rows = [p1, p2, p3]
    ids = [r["ARCHITECTURE_ID"] for r in rows]
    if len(rows) > 3 or len(set(ids)) != len(ids):
        raise RuntimeError("PROPOSAL_N")
    for r in rows:
        if r.get("PLACEHOLDER"):
            raise RuntimeError("PLACEHOLDER")
        for key in (
            "ARCHITECTURE_ID",
            "CORE_MECHANISM",
            "CAUSAL_EVENT_SEQUENCE",
            "ENTRY_STATE_MACHINE",
            "EXECUTION_RULE",
            "EXIT_STATE_MACHINE",
            "CAP_BEHAVIOR",
            "SAME_SYMBOL_BEHAVIOR",
            "OCCUPANCY_BEHAVIOR",
            "SLOT_RELEASE",
            "REENTRY",
            "SESSION_CLOSE",
            "EXPECTED_COVERAGE_REASON",
            "STRUCTURAL_NOVELTY_REASON",
        ):
            if r.get(key) in (None, "", [], {}):
                raise RuntimeError(f"INCOMPLETE {r.get('ARCHITECTURE_ID')} {key}")
    return rows
