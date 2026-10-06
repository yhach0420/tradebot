"""Bind prior split SHA and rejected M1–M11. Do not rewrite prior artifacts."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.cause_first_mechanism_discovery_v1.bind import bind_foundation_v2
from research.cause_first_mechanism_discovery_v1.split import split_session_days
from research.causal_path_to_complete_strategy_v1 import EXPECTED_SPLIT_SHA256, PARENT_VERDICT, REJECTED_M1_M11
from research.causal_path_to_complete_strategy_v1.isolation import PREV_DISCOVERY_OUT


def _sha_lists(disc: list[str], conf: list[str], val: list[str]) -> str:
    payload = {"confirmation": conf, "discovery": disc, "frozen_validation": val}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def bind_prior() -> dict[str, Any]:
    foundation = bind_foundation_v2()
    prev_path = PREV_DISCOVERY_OUT / "report.json"
    if not prev_path.is_file():
        return {"ok": False, "reason": "previous_discovery_report_missing", "foundation": {k: v for k, v in foundation.items() if k != "by_symbol"}}
    prev = json.loads(prev_path.read_text(encoding="utf-8"))
    answers = dict(prev.get("answers") or {})
    split = dict(prev.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or (answers.get("exact_discovery_dates") or {}).get("dates") or [])]
    conf = [str(d) for d in list(split.get("confirmation_dates") or (answers.get("exact_confirmation_dates") or {}).get("dates") or [])]
    val = [str(d) for d in list(split.get("frozen_validation_dates") or (answers.get("exact_frozen_validation_dates") or {}).get("dates") or [])]
    recomputed = _sha_lists(disc, conf, val)
    reported = str(split.get("split_sha256") or answers.get("split_sha") or "")
    prev_verdict = str(answers.get("VERDICT") or (prev.get("decision") or {}).get("VERDICT") or "")
    surviving_n = int((prev.get("mechanisms") or {}).get("surviving_n") or answers.get("surviving_n") or 0)
    rejected_ids = [str(r.get("id")) for r in list((prev.get("mechanisms") or {}).get("rejected") or [])]
    ok = (
        bool(foundation.get("ok"))
        and prev_verdict == PARENT_VERDICT
        and surviving_n == 0
        and set(REJECTED_M1_M11) <= set(rejected_ids)
        and reported == EXPECTED_SPLIT_SHA256
        and recomputed == EXPECTED_SPLIT_SHA256
        and len(disc) == 291
        and len(conf) == 97
        and len(val) == 97
        and disc[0] == "20240917"
        and disc[-1] == "20251126"
        and conf[0] == "20251127"
        and conf[-1] == "20260421"
        and val[0] == "20260422"
        and val[-1] == "20260911"
    )
    return {
        "ok": ok,
        "foundation": {k: v for k, v in foundation.items() if k != "by_symbol"},
        "symbols": list(foundation.get("symbols") or []),
        "by_symbol": dict(foundation.get("by_symbol") or {}),
        "previous_verdict": prev_verdict,
        "m1_m11_surviving_n": surviving_n,
        "m1_m11_rejected_ids": rejected_ids,
        "m1_m11_rescued": False,
        "did_not_relax_day_stability_floor": True,
        "did_not_optimize_clock_or_horizon": True,
        "old_confirmation_used_to_design": False,
        "split": {
            "discovery_dates": disc,
            "confirmation_dates": conf,
            "frozen_validation_dates": val,
            "split_sha256": reported,
            "recomputed_sha256": recomputed,
            "sha_match": recomputed == EXPECTED_SPLIT_SHA256 and reported == EXPECTED_SPLIT_SHA256,
        },
        "reason": None if ok else "prior_split_or_rejected_m1_m11_mismatch",
    }


def verify_recompute_independent(session_days: list[str]) -> dict[str, Any]:
    got = split_session_days(session_days)
    return {
        "independent_sha": got.get("split_sha256"),
        "matches_expected": got.get("split_sha256") == EXPECTED_SPLIT_SHA256,
    }
