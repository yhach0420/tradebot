"""Pin C4 V1 freeze read-only. Do not write V1 OUT."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.c4_portfolio_crowding_precommit_v1.policy import policy_sha256 as v1_policy_sha256
from research.c4_portfolio_crowding_precommit_v2 import (
    EXPECTED_EVENT_N,
    EXPECTED_SEQ_DUPLICATE_N,
    EXPECTED_SEQ_MISSING_N,
    EXPECTED_SEQ_NON_MONOTONE_N,
    EXPECTED_V1_POLICY_SHA256,
    PRIOR_V1_ANALYSIS_ID,
    PRIOR_V1_VERDICT_REQUIRED,
)
from research.c4_portfolio_crowding_precommit_v2.isolation import V1_ARTIFACT_NAMES, V1_OUT
from research.c4_portfolio_crowding_precommit_v2.spec import file_sha256


def prior_artifact_paths() -> dict[str, Path]:
    return {name: V1_OUT / name for name in V1_ARTIFACT_NAMES}


def snapshot_prior_artifacts() -> dict[str, str]:
    out = {}
    for key, path in prior_artifact_paths().items():
        out[key] = file_sha256(path) if path.is_file() else ""
    return out


def artifacts_modified(before: dict[str, str], after: dict[str, str]) -> bool:
    return dict(before) != dict(after)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def load_v1() -> dict[str, Any]:
    path = V1_OUT / "report.json"
    obj = _load_json(path)
    d = dict(obj.get("decision") or {})
    a = dict(obj.get("answers") or {})
    lib = dict(d.get("library") or {})
    order = dict(d.get("order") or {})
    totals = dict(order.get("totals") or {})
    streams = dict(d.get("streams") or {})
    verdict = str(d.get("VERDICT") or a.get("51") or "")
    live_v1_sha = v1_policy_sha256()
    eco = (
        d.get("CANDIDATE_ECONOMICS_RUN") is False
        and d.get("CONTROL_ECONOMICS_RUN") is False
        and d.get("TREATMENT_ECONOMICS_RUN") is False
        and obj.get("precommit", {}).get("CANDIDATE_ECONOMICS_RUN") is False
    )

    def _inum(d: dict[str, Any], key: str) -> int | None:
        if key not in d or d[key] is None:
            return None
        return int(d[key])

    checks = {
        "ANALYSIS_ID": str(obj.get("ANALYSIS_ID") or "") == PRIOR_V1_ANALYSIS_ID,
        "VERDICT": verdict == PRIOR_V1_VERDICT_REQUIRED,
        "ECONOMICS": bool(eco),
        "ENTRY_N": int(lib.get("ENTRY_N") or 0) == 25,
        "EXIT_N": int(lib.get("EXIT_N") or 0) == 4,
        "CONTROL_ARM_N": int(lib.get("CONTROL_ARM_N") or 0) == 100,
        "TREATMENT_ARM_N": int(lib.get("TREATMENT_ARM_N") or 0) == 100,
        "TOTAL_ARM_N": int(lib.get("TOTAL_ARM_N") or 0) == 200,
        "EVENT_N": _inum(totals, "EVENT_N") == EXPECTED_EVENT_N,
        "SEQ_MISSING": _inum(totals, "SEQ_MISSING_N") == EXPECTED_SEQ_MISSING_N,
        "SEQ_DUPLICATE": _inum(totals, "SEQ_DUPLICATE_N") == EXPECTED_SEQ_DUPLICATE_N,
        "SEQ_NON_MONOTONE": _inum(totals, "SEQ_NON_MONOTONE_N") == EXPECTED_SEQ_NON_MONOTONE_N,
        "ORDER_CAUSAL": order.get("SOURCE_EVENT_ORDER_CAUSAL") is True,
        "ORDER_UNIQUE": order.get("SOURCE_EVENT_ORDER_UNIQUE") is True,
        "POLICY_SHA": str(d.get("C4_POLICY_SHA256") or "") == EXPECTED_V1_POLICY_SHA256,
        "LIVE_POLICY_SHA": live_v1_sha == EXPECTED_V1_POLICY_SHA256,
        "RAW_INVARIANCE": streams.get("RAW_STREAM_INVARIANCE_PASS") is True,
        "C4_INVARIANCE": streams.get("C4_STREAM_INVARIANCE_PASS") is True,
    }
    failed = [k for k, v in checks.items() if not v]
    raw_hashes = {
        str(r.get("ENTRY_ID")): str(r.get("RAW_ENTRY_STREAM_SHA256") or "")
        for r in list(streams.get("raw_streams") or [])
    }
    raw_n = {
        str(r.get("ENTRY_ID")): int(r.get("RAW_ENTRY_SIGNAL_N") or 0)
        for r in list(streams.get("raw_streams") or [])
    }
    v1_pass = {
        str(r.get("ENTRY_ID")): int(r.get("C4_PASS_N") or 0)
        for r in list(streams.get("same_t0") or [])
    }
    v1_reject = {
        str(r.get("ENTRY_ID")): int(r.get("C4_REJECT_LATER_SAME_T0_N") or 0)
        for r in list(streams.get("same_t0") or [])
    }
    v1_c4_hashes = {
        str(r.get("ENTRY_ID")): str(r.get("C4_PASSED_STREAM_SHA256") or "")
        for r in list(streams.get("c4_streams") or [])
    }
    return {
        "ok": not failed,
        "blocker": None if not failed else "V1_PIN:" + ",".join(failed),
        "failed": failed,
        "path": str(path),
        "ANALYSIS_ID": obj.get("ANALYSIS_ID"),
        "VERDICT": verdict,
        "C4_POLICY_ID": "C4_FIRST_ARRIVAL_PER_EXACT_T0",
        "C4_POLICY_SHA256": d.get("C4_POLICY_SHA256"),
        "LIVE_V1_POLICY_SHA256": live_v1_sha,
        "ENTRY_N": lib.get("ENTRY_N"),
        "EXIT_N": lib.get("EXIT_N"),
        "CONTROL_ARM_N": lib.get("CONTROL_ARM_N"),
        "TREATMENT_ARM_N": lib.get("TREATMENT_ARM_N"),
        "TOTAL_ARM_N": lib.get("TOTAL_ARM_N"),
        "CANDIDATE_ECONOMICS_RUN": False,
        "CONTROL_ECONOMICS_RUN": False,
        "TREATMENT_ECONOMICS_RUN": False,
        "order": order,
        "totals": totals,
        "raw_hashes": raw_hashes,
        "raw_n": raw_n,
        "v1_pass_n": v1_pass,
        "v1_reject_n": v1_reject,
        "v1_c4_hashes": v1_c4_hashes,
        "same_t0": list(streams.get("same_t0") or []),
    }
