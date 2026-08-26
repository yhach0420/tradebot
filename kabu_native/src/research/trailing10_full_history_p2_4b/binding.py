"""P2-4B spec freeze + Current ENTRY binding. No production substitution."""
from __future__ import annotations

from typing import Any

from research.dynamic_anchor_p2_2.binding import verify_entry_binding
from research.trailing10_dynamic_anchor_p2_4a.publish import implementation_sha, spec_sha
from research.trailing10_full_history_p2_4b import (
    CANDIDATE_ID,
    FROZEN_IMPLEMENTATION_SHA,
    FROZEN_SPEC_SHA,
    FREEZE_TIMESTAMP_JST,
)


def verify_frozen_spec() -> dict[str, Any]:
    spec = spec_sha()
    impl = implementation_sha()
    spec_ok = spec == FROZEN_SPEC_SHA
    impl_ok = impl == FROZEN_IMPLEMENTATION_SHA
    return {
        "CANDIDATE_ID": CANDIDATE_ID,
        "SPEC_SHA_MATCH": "PASS" if spec_ok else "FAIL",
        "IMPLEMENTATION_SHA_MATCH": "PASS" if impl_ok else "FAIL",
        "observed_spec_sha": spec,
        "observed_implementation_sha": impl,
        "expected_spec_sha": FROZEN_SPEC_SHA,
        "expected_implementation_sha": FROZEN_IMPLEMENTATION_SHA,
        "FREEZE_TIMESTAMP_JST": FREEZE_TIMESTAMP_JST,
        "pass": bool(spec_ok and impl_ok),
    }


def verify_bindings() -> dict[str, Any]:
    spec = verify_frozen_spec()
    entry = verify_entry_binding()
    return {
        **spec,
        "CURRENT_ENTRY_BINDING": entry.get("CURRENT_ENTRY_BINDING"),
        "entry_missing": entry.get("missing") or [],
        "entry_path": entry.get("path"),
        "V1RNativeEntryLive_sha": entry.get("V1RNativeEntryLive_sha"),
        "V1RLiveDualLane_sha": entry.get("V1RLiveDualLane_sha"),
        "pass": bool(spec.get("pass") and entry.get("CURRENT_ENTRY_BINDING") == "PASS"),
    }
