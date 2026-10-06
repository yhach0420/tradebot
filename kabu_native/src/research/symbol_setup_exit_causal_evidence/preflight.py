"""Confirm the 20260924 collector path before that session opens.

Does not read prospective push rows and does not rewrite the evidence report.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import find_capture_dir, iter_push
from research.entry_execution_feasibility.fill import standalone_fill
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _board_row
from research.simple_tech_entry_family.stages import attach_indicators, board_support, price_action
from research.symbol_setup_baseline_complete_development import EXIT_CONTRACT_SHA256
from research.symbol_setup_baseline_complete_development.replay import thesis_exit
from research.symbol_setup_baseline_complete_development.verify import identity
from research.symbol_setup_exit_causal_evidence import (
    CLASS_NOT_OBSERVED,
    CLASS_NOT_YET,
    CLASS_OBSERVATION_FAILED,
    COMPLETE_STRATEGY_SHA256,
    EXIT_ID,
    EXIT_SHA256,
    NOT_OBSERVED_DATES,
    TARGET_SESSION,
)
from research.symbol_setup_exit_causal_evidence.collect import classify_day, collect_day, push_capture_present
from research.symbol_setup_exit_causal_evidence.isolation import NATIVE, OUT
from research.symbol_setup_exit_noise_rca.scan import _day as frozen_day
from research.symbol_setup_exit_noise_rca.scan import _episode
from research.symbol_setup_exit_noise_rca.state import bar_state, classify_slope

JST = ZoneInfo("Asia/Tokyo")
REPORT_FILES = ("report.json", "report.md", "audit.xlsx")


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report_hashes() -> dict[str, str]:
    return {name: _sha(OUT / name) for name in REPORT_FILES}


def _synthetic_bars() -> dict[str, bool]:
    start = float(hm_epoch("20260910", 9, 0))
    end = float(hm_epoch("20260910", 11, 30))
    builder = SymbolBarBuilder(am_start=start, am_end=end)
    cum = 0.0
    for minute in range(30):
        cum += 1000.0
        builder.on_event(
            et=start + minute * 60.0 + 5.0,
            px=1000.0 + minute,
            cum_vol=cum,
            bid=999.0 + minute,
            ask=1001.0 + minute,
            continuous=True,
        )
    builder.close_session()
    raw = builder.as_arrays()
    integrity = bar_integrity(raw, am_start=start, am_end=end)
    ind = attach_indicators(raw)
    state = bar_state(ind, int(ind["close"].size) - 1)
    labels = {
        classify_slope(
            [
                {"thesis_live": True, "fast_slow_relation_live": True, "bid": 2.0},
                None,
                None,
            ],
            1.0,
        )["label"],
        classify_slope(
            [
                {"thesis_live": True, "fast_slow_relation_live": True, "bid": 0.5},
                None,
                None,
            ],
            1.0,
        )["label"],
        classify_slope(
            [
                {"thesis_live": False, "fast_slow_relation_live": False, "bid": 0.5},
                {"thesis_live": False, "fast_slow_relation_live": False, "bid": 0.5},
                {"thesis_live": False, "fast_slow_relation_live": False, "bid": 0.5},
            ],
            1.0,
        )["label"],
        classify_slope([{"thesis_live": False, "fast_slow_relation_live": True, "bid": 1.0}, None, None], 1.0)["label"],
    }
    payload = {
        "Symbol": "6501",
        "CurrentPrice": 1000,
        "TradingVolume": 1000,
        "Buy1": {"Price": 999, "Qty": 1000},
        "Sell1": {"Price": 1001, "Qty": 500},
        "CurrentPriceTime": "2026-09-10T09:00:05+09:00",
    }
    row = _board_row(payload, start + 5.0)
    required = {"bid", "ask", "bid_qty", "ask_qty", "fresh_sec", "executable", "px"}
    return {
        "bar_n": int(raw["minute_epoch"].size) >= 24,
        "finalize_t_present": "finalize_t" in raw and bool(np.isfinite(raw["finalize_t"]).all()),
        "integrity_ok": bool(integrity["ok"]),
        "ema9": "ema9" in ind and bool(np.isfinite(ind["ema9"][-1])),
        "ema21": "ema21" in ind and bool(np.isfinite(ind["ema21"][-1])),
        "ema21_lag3": state is not None and state.get("ema21_lag3") is not None,
        "board_fields": required <= set(row),
        "board_support_callable": callable(board_support),
        "price_action_is_baseline": "high" in inspect.getsource(price_action) and "local_resistance" not in inspect.getsource(price_action),
        "four_labels": labels
        == {
            "TEMPORARY_AND_PRICE_RECOVERED",
            "TEMPORARY_SLOPE_INTERRUPTION",
            "TERMINAL_SLOPE_FAILURE",
            "AMBIGUOUS_SLOPE_FAILURE",
        },
    }


def run() -> dict:
    before = _report_hashes()
    ident = identity()
    historical_cap = find_capture_dir("20260910")
    historical_bytes = 0
    if historical_cap is not None:
        historical_bytes = sum(p.stat().st_size for p in historical_cap.glob("push_part_*.jsonl"))
    now = datetime.now(JST)
    target = classify_day(TARGET_SESSION, now=now)
    unobserved = [classify_day(day, now=now) for day in NOT_OBSERVED_DATES]
    failed_example = classify_day(TARGET_SESSION, now=datetime(2026, 9, 24, 16, 0, tzinfo=JST))
    day_source = inspect.getsource(frozen_day)
    collect_source = inspect.getsource(collect_day)
    bars = _synthetic_bars()
    checks = {
        "market_capture_input_available": (NATIVE / "data" / "market_capture").is_dir(),
        "push_capture_true_path": historical_cap is not None and historical_bytes > 0 and push_capture_present("20260910"),
        "iter_push_available": callable(iter_push),
        "baseline_signal_builder_available": "trend_up" in day_source and "price_action" in day_source and "volume_confirm" in day_source,
        "board_snapshot_fields_available": bars["board_fields"] and "_snap_at" in day_source and "board_support" in day_source,
        "w5_shadow_fill_available": float(DEV_WAIT_SEC) == 5.0 and "standalone_fill" in day_source and callable(standalone_fill),
        "bar_finalization_available": bars["bar_n"] and bars["finalize_t_present"] and bars["integrity_ok"],
        "ema_available": bars["ema9"] and bars["ema21"] and bars["ema21_lag3"],
        "post_k1_logger_available": callable(_episode) and "_episode" in day_source and callable(thesis_exit) and "thesis_exit" in day_source and bars["four_labels"],
        "baseline_price_action": bars["price_action_is_baseline"],
        "frozen_day_is_scanner": "frozen_day" in collect_source,
        "no_portfolio_replay": "portfolio_replay" not in day_source and "portfolio_replay" not in collect_source,
        "no_range_break": "range_break" not in day_source and "range_break" not in collect_source,
        "no_directional_volume_repair": "ask_vol" not in day_source,
        "no_live_route": not any(token in collect_source for token in ("submit_order", "live_order", "cancel_order")),
        "identity_ok": bool(ident["ok"]),
        "strategy_sha": ident["checks"]["complete_strategy"] and COMPLETE_STRATEGY_SHA256 == "25d8ef6944c4d82a15d57a3bb078e5503f733184ecb92ee5ad6ba95fe4f95f6d",
        "exit_sha": ident["checks"]["exit_contract"] and EXIT_CONTRACT_SHA256 == EXIT_SHA256,
        "unobserved_not_zero_event": all(
            row["classification"] == CLASS_NOT_OBSERVED and "technical_signals" not in row and row["admitted"] is False for row in unobserved
        ),
        "target_not_yet": target["classification"] == CLASS_NOT_YET and target["admitted"] is False and "technical_signals" not in target,
        "missing_push_after_close_is_observation_failed": (
            failed_example["classification"] == CLASS_OBSERVATION_FAILED
            and failed_example["admitted"] is False
            and "technical_signals" not in failed_example
        ),
        "report_unchanged": _report_hashes() == before,
    }
    return {
        "task": "PREPARE_20260924_EXIT_CAUSAL_EVIDENCE_COLLECTION_V1",
        "as_of": now.strftime("%Y%m%d"),
        "target_session": TARGET_SESSION,
        "session_started": False,
        "EXIT_ID": EXIT_ID,
        "EXIT_SHA256": EXIT_SHA256,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "K_CHANGED": False,
        "TIMEFRAME_CHANGED": False,
        "K": 1,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "historical_push_capture_present": True if checks["push_capture_true_path"] else False,
        "target_push_capture_present": bool(target["push_capture_present"]),
        "target_classification_now": target["classification"],
        "target_classification_if_close_without_push": failed_example["classification"],
        "not_observed": [row["date"] for row in unobserved],
        "checks": checks,
        "ready": all(checks.values()),
        "report_rewritten": False,
    }
