"""Bind corrected SHA + leaked SHA + frozen spec. Read-only."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.bind import bind_prior as bind_correction
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction.isolation import OUT as CORRECTED_OUT
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    parent = bind_correction()
    corr_rep = _load_json(CORRECTED_OUT / "report.json")
    live_c = corrected_machine_sha256()
    live_l = leaked_machine_sha256()
    report_c = str((corr_rep.get("hashes") or {}).get("V4_CORRECTED_MACHINE_SHA256") or corr_rep.get("V4_CORRECTED_MACHINE_SHA256") or "")
    ok = (
        bool(parent.get("ok"))
        and live_c == V4_CORRECTED_MACHINE_SHA256
        and report_c == V4_CORRECTED_MACHINE_SHA256
        and live_l == V4_LEGACY_LEAKED_IMPLEMENTATION
        and corr_rep.get("spec_changed") is False
        and corr_rep.get("future_economic_outcome_used") is False
    )
    return {
        **{k: v for k, v in parent.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(parent.get("symbols") or []),
        "by_symbol": dict(parent.get("by_symbol") or {}),
        "live_corrected_sha": live_c,
        "report_corrected_sha": report_c,
        "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
        "live_leaked_sha": live_l,
        "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
        "corrected_sha_unchanged": live_c == V4_CORRECTED_MACHINE_SHA256 == report_c,
        "leaked_preserved": live_l == V4_LEGACY_LEAKED_IMPLEMENTATION,
        "reason": None if ok else "corrected_or_leaked_sha_mismatch",
        "any_rule_changed": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
    }
