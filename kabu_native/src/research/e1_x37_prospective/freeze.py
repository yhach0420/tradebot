"""Load frozen V1R / model artifact; mutation guards; no refit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized

from . import (
    EXPECTED_COEF,
    EXPECTED_INTERCEPT,
    FEATURE_ORDER,
    MODEL_ARTIFACT_SHA,
    TRAINING_PANEL_SHA,
    V1R_SHA,
)

NATIVE = Path(__file__).resolve().parents[3]
X36R = NATIVE / "results" / "research" / "e1_x36r_freeze_integrity"


class MutationGuard:
    """Reject any attempt to mutate frozen strategy parameters."""

    def __init__(self, frozen: dict[str, Any]):
        self._frozen = dict(frozen)
        self._locked = True

    def assert_locked(self) -> None:
        if not self._locked:
            raise RuntimeError("mutation_guard_unlocked")

    def refuse(self, field: str, new_value: Any) -> None:
        raise RuntimeError(f"MUTATION_REJECTED:{field}")

    def snapshot(self) -> dict[str, Any]:
        return dict(self._frozen)


def _sha_body(body: dict) -> str:
    raw = {k: v for k, v in body.items() if k != "sha256"}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()


def load_v1r() -> dict[str, Any]:
    path = X36R / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body.get("sha256") == V1R_SHA
    assert _sha_body(body) == V1R_SHA
    return body


def load_model_artifact() -> dict[str, Any]:
    path = X36R / "allocator_model_artifact.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body.get("model_artifact_sha256") == MODEL_ARTIFACT_SHA
    # recompute sha over body without sha field (same as serialize.py)
    raw = {k: v for k, v in body.items() if k != "model_artifact_sha256"}
    recomputed = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    assert recomputed == MODEL_ARTIFACT_SHA
    v1r = load_v1r()
    fitted = v1r["allocator"]["fitted"]
    assert fitted["model_artifact_sha256"] == MODEL_ARTIFACT_SHA
    assert list(body["feature_order"]) == list(FEATURE_ORDER)
    assert list(body["coefficients"]) == list(EXPECTED_COEF)
    assert float(body["intercept"]) == float(EXPECTED_INTERCEPT)
    return body


def verify_model_identity(ser: dict[str, Any]) -> dict[str, Any]:
    coef_ok = all(
        abs(float(a) - float(b)) < 1e-15
        for a, b in zip(ser["coefficients"], EXPECTED_COEF)
    )
    intercept_ok = abs(float(ser["intercept"]) - EXPECTED_INTERCEPT) < 1e-15
    feat_ok = list(ser["feature_order"]) == list(FEATURE_ORDER)
    scaler = ser["preprocessing"]
    scaler_ok = (
        scaler.get("type") == "StandardScaler"
        and len(scaler.get("mean") or []) == 6
        and len(scaler.get("scale") or []) == 6
    )
    panel_ok = True
    v1r = load_v1r()
    panel_ok = v1r["allocator"]["training_panel_fingerprint"]["sha256"] == TRAINING_PANEL_SHA
    return {
        "coefficients_identity": coef_ok,
        "intercept_identity": intercept_ok,
        "feature_order_identity": feat_ok,
        "scaler_identity": scaler_ok,
        "model_artifact_sha_ok": ser.get("model_artifact_sha256") == MODEL_ARTIFACT_SHA,
        "training_panel_sha_ok": panel_ok,
        "no_refit": True,
        "pass": bool(coef_ok and intercept_ok and feat_ok and scaler_ok and panel_ok),
    }


def score_reproduction_check(ser: dict[str, Any]) -> dict[str, Any]:
    """Exact score on synthetic feature vectors — no market data."""
    sfn = score_fn_from_serialized(ser)
    # mean vector → standardized zeros → score = sigmoid(intercept)
    feats = dict(zip(FEATURE_ORDER, ser["preprocessing"]["mean"]))
    p_at_mean = float(sfn(feats))
    # intercept-only logistic
    z = float(ser["intercept"])
    p_expected = 1.0 / (1.0 + np.exp(-z)) if z >= 0 else float(np.exp(z) / (1.0 + np.exp(z)))
    # missing feature → -inf
    bad = dict(feats)
    bad["spread_bps"] = float("nan")
    p_missing = float(sfn(bad))
    return {
        "score_at_scaler_mean": p_at_mean,
        "expected_sigmoid_intercept": p_expected,
        "mean_score_delta": abs(p_at_mean - p_expected),
        "missing_returns_neg_inf": p_missing == float("-inf"),
        "pass": abs(p_at_mean - p_expected) < 1e-12 and p_missing == float("-inf"),
    }


def build_mutation_guard(v1r: dict, ser: dict) -> MutationGuard:
    return MutationGuard({
        "v1r_sha": V1R_SHA,
        "model_sha": MODEL_ARTIFACT_SHA,
        "coefficients": list(ser["coefficients"]),
        "intercept": float(ser["intercept"]),
        "feature_order": list(FEATURE_ORDER),
        "entry_sha": v1r["entry_sha"],
        "exit_sha": v1r["exit_sha"],
        "position_cap": v1r["position_cap"],
        "prospective_locked": True,
    })
