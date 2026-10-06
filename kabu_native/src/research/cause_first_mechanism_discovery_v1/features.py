"""Feature catalog with DEPLOYABILITY_CLASS. Research-only series are allowed but not Paper-ready."""
from __future__ import annotations

from typing import Any


def feature_catalog() -> list[dict[str, Any]]:
    return [
        {"feature": "stock_minute_ohlcv", "source": "jquants_v2_equities_bars_minute_v2_panel", "history_range": "20240917-20260911_per_symbol_actual", "timestamp_semantics": "BAR_START", "availability_time": "bar_end_T_plus_1m", "DEPLOYABILITY_CLASS": "RUNTIME_PROXYABLE", "note": "Live subset may later approximate the 105-name panel."},
        {"feature": "breadth_1m_3m_5m", "source": "cross_section_of_minute_panel", "history_range": "panel", "timestamp_semantics": "BAR_START", "availability_time": "bar_end", "DEPLOYABILITY_CLASS": "RUNTIME_PROXYABLE", "note": "Denominator = symbols with both lookback and clock bars present. No OHLC fill."},
        {"feature": "median_return_dispersion_volume_va_activity", "source": "cross_section_of_minute_panel", "history_range": "panel", "timestamp_semantics": "BAR_START", "availability_time": "bar_end", "DEPLOYABILITY_CLASS": "RUNTIME_PROXYABLE"},
        {"feature": "vwap_above_ratio", "source": "session_vwap_then_cross_section", "history_range": "panel", "timestamp_semantics": "BAR_START", "availability_time": "bar_end", "DEPLOYABILITY_CLASS": "RUNTIME_PROXYABLE"},
        {"feature": "ema9_gt_ema21_breadth", "source": "ema_on_session_minute_close", "history_range": "panel", "timestamp_semantics": "BAR_START", "availability_time": "bar_end", "DEPLOYABILITY_CLASS": "RUNTIME_PROXYABLE"},
        {"feature": "sector_breadth_activity_rs", "source": "tse33_from_listed_master_join_panel", "history_range": "panel", "timestamp_semantics": "BAR_START", "availability_time": "bar_end", "DEPLOYABILITY_CLASS": "RUNTIME_PROXYABLE"},
        {"feature": "leadership_hhi_top10_share", "source": "cross_section_5m_return_mass", "history_range": "panel", "timestamp_semantics": "BAR_START", "availability_time": "bar_end", "DEPLOYABILITY_CLASS": "RUNTIME_PROXYABLE"},
        {"feature": "stock_rs_pullback_reclaim_breakout_relvol", "source": "stock_minute_plus_session_vwap_ema", "history_range": "panel", "timestamp_semantics": "BAR_START", "availability_time": "bar_end", "DEPLOYABILITY_CLASS": "RUNTIME_NATIVE", "note": "Computable from live bars/PUSH for a registered name."},
        {"feature": "forward_x0_markout", "source": "next_bar_open_then_later_close", "history_range": "discovery_confirmation_only", "timestamp_semantics": "BAR_START", "availability_time": "outcome_only_not_a_feature", "DEPLOYABILITY_CLASS": "RESEARCH_ONLY", "note": "Used as supervised diagnostic inside Discovery/Confirmation. Never a decision-time feature."},
        {"feature": "execution_tax_x1_8bps", "source": "explicit_scenario_not_spread", "history_range": "n/a", "timestamp_semantics": "n/a", "availability_time": "n/a", "DEPLOYABILITY_CLASS": "RESEARCH_ONLY", "note": "Not Bid/Ask proof."},
        {"feature": "historical_bid_ask", "source": "unavailable_official_trades_have_no_bid_ask", "history_range": None, "timestamp_semantics": None, "availability_time": None, "DEPLOYABILITY_CLASS": "RESEARCH_ONLY", "note": "Not inferred."},
        {"feature": "topix_daily_60d", "source": "jquants_indices_topix_cache_20260618_20260911", "history_range": "20260618-20260911_daily_only", "timestamp_semantics": "daily_bar_not_joined", "availability_time": "not_used_this_run", "DEPLOYABILITY_CLASS": "RESEARCH_ONLY", "note": "Present as daily cache but not joined; too coarse vs 1m clocks and shorter than the minute panel."},
        {"feature": "nk225mini_intraday", "source": "not_in_v2_minute_panel", "history_range": None, "timestamp_semantics": None, "availability_time": None, "DEPLOYABILITY_CLASS": "RESEARCH_ONLY", "note": "Allowed later. Missing does not block this run."},
        {"feature": "usdjpy_es_nq_wti", "source": "not_acquired", "history_range": None, "timestamp_semantics": None, "availability_time": None, "DEPLOYABILITY_CLASS": "RESEARCH_ONLY", "note": "Not purchased. Does not block."},
        {"feature": "v27_ema_structure_loss_persistence", "source": "prior_evidence_not_frozen_rule", "history_range": "prior_study", "timestamp_semantics": "BAR_START_if_replicated", "availability_time": "bar_end", "DEPLOYABILITY_CLASS": "RUNTIME_NATIVE", "note": "Prior EXIT evidence. Not a production freeze this run."},
    ]


def deployability_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {"RUNTIME_NATIVE": 0, "RUNTIME_PROXYABLE": 0, "RESEARCH_ONLY": 0}
    for r in rows:
        k = str(r.get("DEPLOYABILITY_CLASS") or "")
        if k in out:
            out[k] += 1
    return out
