"""Bind the designated Simple Tech V1 contract from source. No economic replay."""
from __future__ import annotations

import hashlib
from typing import Any

from research.am_entry_profit_improvement import C14_ID, DEV_WAIT_SEC
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family import (
    ARCHITECTURE_ID,
    BB_PERIOD,
    BB_SIGMA,
    BOARD_ASK_BID_QTY_MAX_RATIO,
    EMA_LONG,
    EMA_SHORT,
    EMA_SLOPE_BARS,
    PULLBACK_LOOKBACK,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
    STRATEGY_ID,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
    WARMUP_BARS,
)
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_entry_family.v2_spec import PARENT_SPEC_SHA256_EXPECTED
from research.symbol_setup_baseline_reconstruction import BASELINE_ANALYSIS_ID, BASELINE_ID, EXPECTED_V1_SPEC_SHA256
from research.symbol_setup_baseline_reconstruction.isolation import NATIVE
from small_paper.v1r_exit_v2_contract import EXIT_V2_CANDIDATE_SHA, FROZEN_CONTINUATION, FROZEN_GUARD
from small_paper.v1r_primary_runtime import POSITION_CAP

ROOT = NATIVE / "src" / "research" / "simple_tech_entry_family"


def _file_sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def versions() -> list[dict[str, str]]:
    return [
        {"version_id": "SIMPLE_TECH_PULLBACK_V1", "phase": "SIMPLE_TECH_ENTRY_FAMILY_V1", "entry": "MA+BB pullback+RCI cross+price action+relative volume+board veto", "exit": "C14 Arch E", "status": "CANONICAL_BASELINE_CANDIDATE", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_PULLBACK_V2_EVENT_TRIGGER", "phase": "V2", "entry": "V1 parent; price trigger changed only", "exit": "C14 unchanged", "status": "RESEARCH_ONLY", "superseded_by": "later RCAs"},
        {"version_id": "SIMPLE_TECH_PULLBACK_V3_EXIT_NEUTRAL", "phase": "V3", "entry": "V1 parent", "exit": "exit-neutral diagnostic", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V4_VOLUME_QUALITY_RCA", "phase": "V4", "entry": "volume quality diagnostic", "exit": "not a new baseline exit", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_PULLBACK_V4_PERSISTENCE_RULE", "phase": "V4 persistence", "entry": "added persistence rule", "exit": "not V1", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V5_REVERSAL_QUALITY_RCA", "phase": "V5", "entry": "reversal diagnostic", "exit": "not a new baseline exit", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V6_TREND_PULLBACK_STAGE_RCA", "phase": "V6", "entry": "stage RCA", "exit": "not a new baseline exit", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_PULLBACK_V6_DEPTH_RULE", "phase": "V6 depth", "entry": "pullback depth rule", "exit": "not V1", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V7_TIMEFRAME_ROLE_RCA", "phase": "V7", "entry": "timeframe diagnostic of V1", "exit": "not a new baseline exit", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V8_ARCHITECTURE_ROLE_RCA", "phase": "V8", "entry": "role diagnostic of V1", "exit": "not a new baseline exit", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V9_TREND_CONTEXT_RCA", "phase": "V9", "entry": "trend context diagnostic", "exit": "not a new baseline exit", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V10_RCI_BOARD_ROLE_RCA", "phase": "V10", "entry": "RCI/board role diagnostic", "exit": "not a new baseline exit", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V11_SIGNAL_VS_EXECUTION_COST_RCA", "phase": "V11", "entry": "cost attribution", "exit": "not a new baseline exit", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V12_ENTRY_EXECUTION_ARCHITECTURE", "phase": "V12", "entry": "execution architecture test", "exit": "not V1", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V13_ENTRY_EXECUTION_STRUCTURE_VERIFICATION", "phase": "V13", "entry": "later development entry freeze, not V1", "exit": "EXIT_IMPLEMENTED false", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V18_FIXED180_EXIT_POLICY", "phase": "V18", "entry": "later E4 stack", "exit": "FIXED180", "status": "RESEARCH_ONLY", "superseded_by": "V19"},
        {"version_id": "SIMPLE_TECH_V19_EXIT_STRUCTURE_VERIFICATION", "phase": "V19", "entry": "T3_PULLBACK_RCI plus E4", "exit": "FIXED180_FIRST_CAUSAL_BID", "status": "RESEARCH_ONLY", "superseded_by": ""},
        {"version_id": "SIMPLE_TECH_V25_CORRECTED_EXECUTION_BASELINE_RECONCILIATION", "phase": "V25", "entry": "later execution reconciliation", "exit": "not the V1 C14 contract", "status": "RESEARCH_ONLY", "superseded_by": ""},
    ]


def stages(files: dict[str, str]) -> list[dict[str, Any]]:
    src = "src/research/simple_tech_entry_family"
    art = "results/research/simple_tech_entry_family/v1/report.json"
    common = {"source_artifact": art, "spec_sha256": EXPECTED_V1_SPEC_SHA256}
    return [
        _stage(stage_id="MA_TREND", role="trend", timeframe="1min_completed", indicator="EMA", formula="SMA seed of first N closes, then EMA alpha=2/(N+1). Pass if EMA9[t] > EMA21[t] and EMA21[t] > EMA21[t-3].", parameters="EMA9, EMA21, slope_bars=3", threshold="strict greater-than", lookback="21 bars to define EMA21; slope uses t-3; first evaluable index 23", causal_availability="completed bar only; value at t uses closes through t", source_file=f"{src}/indicators.py; {src}/stages.py", source_sha256=files["indicators.py"], **common),
        _stage(stage_id="BB_LOCATION", role="pullback location", timeframe="1min_completed", indicator="Bollinger", formula="mid = rolling mean of close, period 20; sd = population std ddof=0; lower = mid - 2*sd. Over the last 3 completed bars including t: at least one low <= EMA9, and every close >= lower band.", parameters="period=20, sigma=2, pullback_lookback=3", threshold="low touch EMA9; close not below lower band", lookback="20 closes for the band; 3 bars for the touch", causal_availability="band at t uses closes through t", source_file=f"{src}/indicators.py; {src}/stages.py", source_sha256=files["indicators.py"], **common),
        _stage(stage_id="RCI_REVERSAL", role="oversold reversal cross", timeframe="1min_completed", indicator="RCI", formula="RCI = (1 - 6*sum((time_rank-price_rank)^2) / (n*(n*n-1))) * 100. Price ties use average rank. Pass if RCI9[t-1] <= -80 and RCI9[t] > -80.", parameters="period=9, level=-80", threshold="cross up through -80", lookback="9 closes", causal_availability="RCI[t] uses closes through t; the cross compares t-1 and t", source_file=f"{src}/indicators.py; {src}/stages.py", source_sha256=files["indicators.py"], **common),
        _stage(stage_id="VOLUME_PARTICIPATION", role="relative volume", timeframe="1min_completed", indicator="1m volume delta", formula="bar volume is the positive cumulative TradingVolume delta inside the completed minute. Pass if volume[t] > 0 and volume[t] >= 1.5 * median(volume[t-5:t-1]).", parameters="median_bars=5, mult=1.5", threshold="1.5 times prior median", lookback="prior 5 completed bars, current bar excluded from the median", causal_availability="current bar volume is known only after the bar finalizes", source_file=f"{src}/bars.py; {src}/stages.py", source_sha256=files["bars.py"], **common),
        _stage(stage_id="PRICE_ACTION_TRIGGER", role="trigger", timeframe="1min_completed", indicator="close, prior high, EMA9, BB upper", formula="close[t] > EMA9[t] AND close[t] > high[t-1] AND close[t] <= BB_upper[t]", parameters="none beyond MA and BB", threshold="prior-high break and still inside the upper band", lookback="1 prior bar", causal_availability="same completed bar as the setup", source_file=f"{src}/stages.py", source_sha256=files["stages.py"], **common),
        _stage(stage_id="BOARD_SUPPORT_VETO", role="support veto", timeframe="quote at bar finalize", indicator="best bid/ask and quantities", formula="executable, not special, fresh_sec <= 5, bid>0, ask>0, both quantities > 0, ask_qty <= 2 * bid_qty. Failure vetoes. Board does not create the signal.", parameters="fresh_sec_max=5, ask/bid qty ratio=2", threshold="all support conditions", lookback="snapshot at finalize_t", causal_availability="board state at the completed bar's finalize time", source_file=f"{src}/stages.py", source_sha256=files["stages.py"], **common),
        _stage(stage_id="ENTRY", role="conjunction", timeframe="1min_completed then quote", indicator="all prior stages", formula="TREND and PULLBACK and RCI and PRICE_ACTION and VOLUME and BOARD and execution_eligible. Harvest applies them in that order. execution_eligible also requires both quantities >= 100 and ask >= bid.", parameters="MIN_QTY=100", threshold="all true", lookback="warmup 24 bars", causal_availability="decision time is finalize_t of the completed minute", source_file=f"{src}/harvest.py", source_sha256=files["harvest.py"], **common),
        _stage(stage_id="EXECUTION", role="historical passive fill", timeframe="event clock", indicator="bid limit", formula="limit = bid at finalize_t. Fill if ask crosses that limit within 5 seconds, no price improvement. Not a next-bar-open fill.", parameters="DEV_WAIT_SEC=5", threshold="ask cross", lookback="5 seconds", causal_availability="fill cannot precede t0", source_file=f"{src}/harvest.py", source_sha256=files["harvest.py"], **common),
        _stage(stage_id="EXIT", role="legacy C14", timeframe="event clock after fill", indicator="Arch E imbalance and continuation", formula="Frozen C14 Arch E: imbalance guard IMB_p5_t-10 for 5 seconds inside 120 seconds, else continuation MFE60_IMB10 at 600 seconds with a 750 second extension, else session-close bid. Not an invalidation of EMA, BB, RCI, volume, or the price-action trigger.", parameters="guard persist 5s, threshold -0.1, monitor 120s; continuation mfe 60 bps and imb 0.1", threshold="Arch E policy", lookback="post-fill path", causal_availability="exit uses the fill and later board path", source_file="src/small_paper/v1r_exit_v2_contract.py", source_sha256=files["v1r_exit_v2_contract.py"], **common),
    ]


def contract() -> dict[str, Any]:
    live = spec_sha256()
    resolved = (
        STRATEGY_ID == BASELINE_ID
        and live == EXPECTED_V1_SPEC_SHA256
        and PARENT_SPEC_SHA256_EXPECTED == EXPECTED_V1_SPEC_SHA256
    )
    files = {
        name: _file_sha(ROOT / name)
        for name in ("__init__.py", "spec.py", "stages.py", "indicators.py", "bars.py", "harvest.py", "portfolio.py")
    }
    files["v1r_exit_v2_contract.py"] = _file_sha(NATIVE / "src" / "small_paper" / "v1r_exit_v2_contract.py")
    body = {
        "baseline_id": BASELINE_ID,
        "analysis_id": BASELINE_ANALYSIS_ID,
        "architecture_id": ARCHITECTURE_ID,
        "baseline_spec_sha256": live,
        "identity_resolved": resolved,
        "published_verdict": "SIMPLE_TECH_V1_DEFICIENCY_IDENTIFIED",
        "published_report": "results/research/simple_tech_entry_family/v1/report.json",
        "selection_basis": "project identity SIMPLE_TECH_PULLBACK_V1, parent SHA pinned by later versions, not PnL",
        "symbol_first": True,
        "context_independent": True,
        "setup_timeframe": "1min_completed",
        "trigger_timeframe": "1min_completed",
        "execution_timeframe": "quote_event_clock_5s_passive_bid",
        "one_minute_role": "mixed_setup_and_trigger",
        "board_role": "SUPPORT_VETO",
        "structure_layer_present": False,
        "price_action_contract_missing": False,
        "price_action_rule": "close[t] > EMA9[t] and close[t] > high[t-1] and close[t] <= BB_upper[t]",
        "volume_kind": "relative_volume_ratio",
        "volume_buy_sell_distinguished_in_gate": False,
        "volume_signed_fields_recorded_not_gated": ["up_vol", "down_vol", "ask_vol", "bid_vol"],
        "rci_use": "oversold_reversal_cross",
        "ma_use": "short_above_long_plus_long_ma_higher_than_three_bars_ago",
        "bb_use": "pullback_location_not_below_lower_band",
        "entry_contract_complete": True,
        "exit_contract_complete": True,
        "exit_thesis_aligned": False,
        "baseline_exit_architecture_incompatible": True,
        "exit_id": C14_ID,
        "exit_candidate_sha256": EXIT_V2_CANDIDATE_SHA,
        "exit_guard": FROZEN_GUARD,
        "exit_continuation": FROZEN_CONTINUATION,
        "parameters": {
            "ema_short": int(EMA_SHORT),
            "ema_long": int(EMA_LONG),
            "ema_slope_bars": int(EMA_SLOPE_BARS),
            "bb_period": int(BB_PERIOD),
            "bb_sigma": float(BB_SIGMA),
            "rci_period": int(RCI_PERIOD),
            "rci_cross_level": float(RCI_CROSS_LEVEL),
            "volume_median_bars": int(VOLUME_MEDIAN_BARS),
            "volume_mult": float(VOLUME_MULT),
            "pullback_lookback": int(PULLBACK_LOOKBACK),
            "board_ask_bid_qty_max_ratio": float(BOARD_ASK_BID_QTY_MAX_RATIO),
            "board_fresh_sec": float(BOARD_FRESHNESS_SEC),
            "min_qty": float(MIN_QTY),
            "dev_wait_sec": float(DEV_WAIT_SEC),
            "position_cap": int(POSITION_CAP),
            "warmup_bars": int(WARMUP_BARS),
        },
        "source_file_sha256": files,
        "pb1_required": False,
        "market_context_required": False,
        "sector_context_required": False,
        "new_pnl_run": False,
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
    }
    return body
