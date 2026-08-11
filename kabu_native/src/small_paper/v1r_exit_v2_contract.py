"""Frozen V1R EXIT V2 Arch E contract — production path.

Research SoT (do not re-derive semantics):
  research.v1r_exit_v2_asymmetric.guards.detect_guard_trigger
  research.v1r_exit_v2_asymmetric.continuation.continuation_supported
  research.v1r_exit_v2_asymmetric.policy.apply_architecture
  research.v1r_exit_v2_asymmetric.states.build_trade_bundle

EXIT SHA candidate:
  V1R_EXIT_V2_ASYMMETRIC_CANDIDATE_V1
  sha256=6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from research.v1r_exit_v2_asymmetric.continuation import continuation_supported
from research.v1r_exit_v2_asymmetric.guards import detect_guard_trigger
from research.v1r_exit_v2_asymmetric.policy import apply_architecture
from research.v1r_exit_v2_asymmetric.states import build_trade_bundle, exit_at_horizon

NATIVE = Path(__file__).resolve().parents[2]
EXIT_V2_CANDIDATE_PATH = (
    NATIVE / "results/research/v1r_exit_v2_asymmetric/V1R_EXIT_V2_ASYMMETRIC_CANDIDATE_V1.json"
)
EXIT_V2_CANDIDATE_SHA = "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"
EXIT_V2_MANIFEST_ID = "V1R_EXIT_V2_ASYMMETRIC_CANDIDATE_V1"

FROZEN_GUARD = {
    "family": "IMBALANCE",
    "id": "IMB_p5_t-10",
    "kind": "imbalance",
    "persist_sec": 5.0,
    "imb_threshold": -0.1,
    "monitor_to": 120.0,
}
FROZEN_CONTINUATION = {
    "id": "MFE60_IMB10",
    "kind": "mfe_and_imb",
    "mfe_min": 60.0,
    "imb_min": 0.1,
}

# Human Discord reasons (ledger keeps raw reason ids)
DISCORD_REASON_JA = {
    "IMBALANCE": "早期撤退: 売り優勢状態が5秒継続",
    "CONT_EXIT_600": "600秒決済: 上昇継続条件なし",
    "CONT_EXTEND_750": "750秒延長決済: 600秒時点で上昇継続条件成立",
    "FIXED600": "600秒決済: 上昇継続条件なし",
    "TIME750": "750秒延長決済: 600秒時点で上昇継続条件成立",
    "FIXED_HOLD": "600秒決済: 上昇継続条件なし",
    "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET": "600秒決済: 上昇継続条件なし",
}


def load_exit_v2_candidate() -> dict[str, Any]:
    body = json.loads(EXIT_V2_CANDIDATE_PATH.read_text(encoding="utf-8"))
    if body.get("sha256") != EXIT_V2_CANDIDATE_SHA:
        raise RuntimeError(
            f"exit_v2_sha_mismatch: disk={body.get('sha256')} expected={EXIT_V2_CANDIDATE_SHA}"
        )
    return body


def frozen_guard() -> dict[str, Any]:
    return dict(FROZEN_GUARD)


def frozen_continuation() -> dict[str, Any]:
    return dict(FROZEN_CONTINUATION)


def apply_arch_e_to_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Apply Frozen Arch E via research policy SoT."""
    return apply_architecture(
        bundle,
        arch="E",
        guard=frozen_guard(),
        cont_rule=frozen_continuation(),
    )


def apply_fixed600_to_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    return apply_architecture(bundle, arch="A")


def discord_exit_reason_ja(raw_reason: Any) -> str:
    s = str(raw_reason or "").strip()
    return DISCORD_REASON_JA.get(s, s or "EXIT")


def patch_panel_exits(
    panel: list[dict],
    bundles_by_key: dict[tuple, dict],
    *,
    mode: str,
) -> list[dict]:
    """
    mode: arch_e | fixed600
    Mutates copies of panel events with canonical exit fields for simulate_joint.
    """
    evs = [dict(e) for e in panel]
    for e in evs:
        if not (e.get("filled") and e.get("fill_time") is not None):
            continue
        key = (e["date"], e["symbol"], float(e["fill_time"]))
        b = bundles_by_key.get(key)
        if not b:
            continue
        if mode == "arch_e":
            pol = apply_arch_e_to_bundle(b)
        elif mode == "fixed600":
            pol = apply_fixed600_to_bundle(b)
        else:
            raise ValueError(mode)
        if not pol.get("ok"):
            continue
        e["canonical_exit_time"] = pol["exit_time"]
        e["canonical_exit_ret_bps"] = pol["exit_ret_bps"]
        e["canonical_hold_sec"] = pol["exit_off"]
        e["canonical_exit_reason"] = pol.get("reason")
        e["FIXED600_NET_BPS"] = pol["exit_ret_bps"]  # joint allocator occupancy field
        e["exit_v2_triggered_guard"] = bool(pol.get("triggered_guard"))
        e["exit_v2_extended"] = bool(pol.get("extended"))
        e["exit_v2_arch"] = pol.get("arch")
        e["exit_reason_ja"] = discord_exit_reason_ja(pol.get("reason"))
    return evs
