"""Canonical E1_X5 feature / score-identity hash — shared by Runtime and Replay.

Does not affect ENTRY/EXIT/score decisions. Logging / parity evidence only.
Frozen 20260727 Oracle is not rewritten; next Paper logs schema+version.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Optional, Sequence

FEATURE_HASH_SCHEMA = "e1_x5_canonical_feature_hash"
FEATURE_HASH_VERSION = 1

# Legacy recipes observed on 20260727 Source-of-Truth logs (do not rewrite).
LEGACY_ORACLE_FEATURE_HASH_SCHEMA = "e1_x5_oracle_freeze_packet_proxy_v0"
LEGACY_RUNTIME_FEATURE_HASH_SCHEMA = "e1_x5_runtime_packet_hash_v0"

NOT_COMPARABLE_RECIPE_DIFFERENCE = "NOT_COMPARABLE_RECIPE_DIFFERENCE"
COMPARABLE = "COMPARABLE"


def normalize_feature_value(v: Any) -> Any:
    """Fixed None / float / int / bool / str normalization for stable hashing."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and not isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(float(v), 12)
    if isinstance(v, str):
        return v
    # Nested / unexpected → stable JSON string
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def canonicalize_features(
    features: Mapping[str, Any],
    *,
    feature_order: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Order features deterministically; normalize values."""
    if feature_order is not None:
        keys = list(feature_order)
        # Append any extras sorted for forward-compat
        extras = sorted(k for k in features.keys() if k not in keys)
        keys = keys + extras
    else:
        keys = sorted(features.keys())
    return {k: normalize_feature_value(features.get(k)) for k in keys}


def stable_serialize(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_feature_hash(
    features: Mapping[str, Any],
    *,
    feature_order: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Hash a feature dict. Returns hash + schema/version metadata."""
    payload = {
        "schema": FEATURE_HASH_SCHEMA,
        "version": FEATURE_HASH_VERSION,
        "features": canonicalize_features(features, feature_order=feature_order),
    }
    digest = _sha256_text(stable_serialize(payload))
    return {
        "feature_hash": digest,
        "feature_hash_schema": FEATURE_HASH_SCHEMA,
        "feature_hash_version": FEATURE_HASH_VERSION,
    }


def canonical_score_identity_hash(
    *,
    sample_id: str = "",
    score: Optional[float] = None,
    spread_bps: Any = None,
    bid: Any = None,
    ask: Any = None,
    event_sequence: Any = None,
) -> dict[str, Any]:
    """Shared packet-identity hash when raw feature vectors are unavailable.

    Used by Runtime event log for next Paper so Replay can match the same recipe.
    """
    payload = {
        "schema": FEATURE_HASH_SCHEMA,
        "version": FEATURE_HASH_VERSION,
        "kind": "score_identity",
        "sample_id": str(sample_id or ""),
        "score": normalize_feature_value(float(score) if score is not None else None),
        "spread_bps": normalize_feature_value(spread_bps),
        "bid": normalize_feature_value(bid),
        "ask": normalize_feature_value(ask),
        "event_sequence": normalize_feature_value(
            int(event_sequence) if event_sequence is not None and str(event_sequence) != "" else None
        ),
    }
    digest = _sha256_text(stable_serialize(payload))
    return {
        "feature_hash": digest,
        "feature_hash_schema": FEATURE_HASH_SCHEMA,
        "feature_hash_version": FEATURE_HASH_VERSION,
    }


def infer_legacy_feature_hash_schema(side: str) -> str:
    side_l = str(side or "").lower()
    if side_l in ("oracle", "oracle_freeze", "offline"):
        return LEGACY_ORACLE_FEATURE_HASH_SCHEMA
    return LEGACY_RUNTIME_FEATURE_HASH_SCHEMA


def compare_feature_hashes(
    *,
    oracle_schema: Optional[str],
    runtime_schema: Optional[str],
    pairs: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """Compare hashes only when schemas match; else NOT_COMPARABLE_RECIPE_DIFFERENCE.

    pairs: list of (oracle_hash, runtime_hash) for SCORE rows with a hash present.
    """
    o_s = str(oracle_schema or "")
    r_s = str(runtime_schema or "")
    n = len(pairs)
    if not o_s or not r_s or o_s != r_s:
        return {
            "feature_hash_comparison_status": NOT_COMPARABLE_RECIPE_DIFFERENCE,
            "feature_hash_comparable_count": 0,
            "feature_hash_not_comparable_count": int(n),
            "feature_hash_mismatch_count": None,
            "feature_hash_mismatch_display": "N/A",
            "oracle_schema": o_s or None,
            "runtime_schema": r_s or None,
            "note": (
                "Oracle and Runtime hash recipes/schemas differ; "
                "do not treat hash inequality as a decision mismatch. "
                "Next Paper uses e1_x5_canonical_feature_hash v1 on both sides."
            ),
        }

    mismatch = 0
    for oh, rh in pairs:
        if (oh or "") != (rh or ""):
            mismatch += 1
    return {
        "feature_hash_comparison_status": COMPARABLE,
        "feature_hash_comparable_count": int(n),
        "feature_hash_not_comparable_count": 0,
        "feature_hash_mismatch_count": int(mismatch),
        "feature_hash_mismatch_display": str(mismatch),
        "oracle_schema": o_s,
        "runtime_schema": r_s,
        "note": None,
    }
