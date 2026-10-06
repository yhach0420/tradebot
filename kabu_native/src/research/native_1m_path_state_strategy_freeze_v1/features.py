"""Frozen formulas for dist_vwap, mins_from_open, vwap_reclaim. Do not redefine reclaim."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.native_path_state_discrimination_v1.features import mins_from_open as mins_from_open_impl


def feature_spec() -> dict[str, Any]:
    return {
        "bar_semantics": "BAR_START. Label T is the bar covering [T, T+1m). Features at T use that completed bar and are available at T+1m.",
        "dist_vwap": {
            "formula": "close[T] / session_vwap[T] - 1.0",
            "price_field": "close of feature_bar T",
            "vwap_field": "session_vwap[T] from prep_symbol",
            "vwap_formula": (
                "Per symbol-day cumulative. For each observed bar i: "
                "if trading_value finite and volume>0, add trading_value to numerator and volume to denominator; "
                "else typical_price=(high+low+close)/3, and if typical and volume>0 add typical*volume / volume. "
                "If den>0, vwap=num/den; else nan. Zero-volume bars do not update num/den; prior vwap carries if den>0."
            ),
            "session_reset": "num=den=0 at the start of each symbol-day prep_symbol.",
            "zero_volume": "Bar skipped in the accumulator if volume is not > 0 (and trading_value path also requires volume>0).",
            "missing_bar": "Only observed parquet minutes exist. No interpolation. dist_vwap nan if close or vwap non-finite or vwap==0.",
            "corporate_action": "None. Native parquet prices used as stored. No split/dividend restatement in this feature.",
            "BAR_START": True,
        },
        "mins_from_open": {
            "formula": "HH*60+MM - 9*60 of feature_bar, not event_time",
            "implementation": "research.native_path_state_discrimination_v1.features.mins_from_open",
            "threshold_145_5_means": "feature_bar 11:25 = 145 minutes from 09:00, allowed; feature_bar 11:26 = 146, rejected. Entry is the next bar (~11:26).",
        },
        "vwap_reclaim": {
            "previous_state_required": "close[T-1] <= session_vwap[T-1] (non-strict previous not-above)",
            "current_completed_bar": "close[T] > session_vwap[T] (strict above on the feature bar)",
            "combined": "above_vwap AND prev_below",
            "strict": {
                "current_above": "strict close > vwap",
                "previous_below_or_equal": "non-strict close <= vwap",
            },
            "price_field": "close",
            "previous_bar": "previous OBSERVED bar in the symbol-day array, not a filled missing clock minute",
            "available_at": "T+1m (hhmm_add(feature_bar, 1))",
            "encoded_numeric": "1.0 if true else 0.0; predicate is vwap_reclaim > 0.5",
            "i_eq_0": "No previous bar → vwap_reclaim false",
            "do_not_redefine": True,
        },
        "nan_imputation": (
            "match_rule: non-finite feature replaced by D1 contrast median for that name, else 0.0. "
            "This median is a frozen Discovery D1 constant, not a live label."
        ),
    }


def feature_spec_sha256() -> str:
    payload = feature_spec()
    return hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")).hexdigest()


def mins_from_open(hhmm: str) -> float:
    return mins_from_open_impl(hhmm)
