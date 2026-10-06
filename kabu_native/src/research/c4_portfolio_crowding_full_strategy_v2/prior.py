"""Pin C4 V2 library freeze read-only. Do not write V1/V2 OUT."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.c4_portfolio_crowding_full_strategy_v2 import (
    EXPECTED_V2_POLICY_SHA256,
    FROZEN_ELIGIBLE_IDS,
    PRECOMMIT_ANALYSIS_ID,
    PRECOMMIT_NEXT_REQUIRED,
    PRECOMMIT_VERDICT_REQUIRED,
    TREATMENT_ARM_N,
    TOTAL_ARM_N,
)
from research.c4_portfolio_crowding_full_strategy_v2.isolation import (
    V1_ARTIFACT_NAMES,
    V1_OUT,
    V2_ARTIFACT_NAMES,
    V2_OUT,
)
from research.c4_portfolio_crowding_full_strategy_v2.spec import file_sha256
from research.c4_portfolio_crowding_precommit_v2.policy import policy_sha256 as live_v2_policy_sha256


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def snapshot_prior_artifacts() -> dict[str, str]:
    out = {}
    for name in V2_ARTIFACT_NAMES:
        path = V2_OUT / name
        out[f"v2_{name}"] = file_sha256(path) if path.is_file() else ""
    for name in V1_ARTIFACT_NAMES:
        path = V1_OUT / name
        out[f"v1_{name}"] = file_sha256(path) if path.is_file() else ""
    return out


def artifacts_modified(before: dict[str, str], after: dict[str, str]) -> bool:
    return dict(before) != dict(after)


def load_v2() -> dict[str, Any]:
    path = V2_OUT / "report.json"
    obj = _load_json(path)
    d = dict(obj.get("decision") or {})
    a = dict(obj.get("answers") or {})
    lib = dict(d.get("library") or {})
    inter = dict(d.get("intervention") or {})
    verdict = str(d.get("VERDICT") or a.get("47") or "")
    nxt = str(d.get("NEXT") or a.get("48") or "")
    eligible = tuple(str(x) for x in list(inter.get("eligible_ids") or lib.get("eligible_ids") or []))
    if not eligible:
        eligible = tuple(str(x) for x in list(a.get("21") or []))
    live_sha = live_v2_policy_sha256()
    eco = (
        d.get("CANDIDATE_ECONOMICS_RUN") is False
        and d.get("CONTROL_ECONOMICS_RUN") is False
        and d.get("TREATMENT_ECONOMICS_RUN") is False
    )
    checks = {
        "ANALYSIS_ID": str(obj.get("ANALYSIS_ID") or d.get("ANALYSIS_ID") or "") == PRECOMMIT_ANALYSIS_ID,
        "VERDICT": verdict == PRECOMMIT_VERDICT_REQUIRED,
        "NEXT": nxt == PRECOMMIT_NEXT_REQUIRED,
        "ECONOMICS": bool(eco),
        "ELIGIBLE_ENTRY_N": int(inter.get("C4_ATTRIBUTION_ELIGIBLE_ENTRY_N") or lib.get("ELIGIBLE_ENTRY_N") or 0) == 5,
        "ELIGIBLE_TOTAL_ARM_N": int(lib.get("ELIGIBLE_TOTAL_ARM_N") or 0) == int(TOTAL_ARM_N),
        "ELIGIBLE_TREATMENT_ARM_N": int(lib.get("ELIGIBLE_TREATMENT_ARM_N") or 0) == int(TREATMENT_ARM_N),
        "ELIGIBLE_IDS": eligible == FROZEN_ELIGIBLE_IDS,
        "POLICY_SHA": str(d.get("C4_V2_POLICY_SHA256") or "") == EXPECTED_V2_POLICY_SHA256,
        "LIVE_POLICY_SHA": live_sha == EXPECTED_V2_POLICY_SHA256,
        "CONTROL_CANNOT_WIN": d.get("CONTROL_ELIGIBLE_AS_WINNER") is False,
        "EVERY_MATCHED": bool(lib.get("EVERY_TREATMENT_HAS_MATCHED_CONTROL")),
    }
    failed = [k for k, v in checks.items() if not v]
    return {
        "ok": not failed,
        "blocker": None if not failed else "V2_PIN:" + ",".join(failed),
        "failed": failed,
        "path": str(path),
        "ANALYSIS_ID": obj.get("ANALYSIS_ID") or PRECOMMIT_ANALYSIS_ID,
        "VERDICT": verdict,
        "NEXT": nxt,
        "C4_V2_POLICY_SHA256": d.get("C4_V2_POLICY_SHA256"),
        "LIVE_V2_POLICY_SHA256": live_sha,
        "eligible_ids": list(eligible),
        "block_intervention": list(d.get("block_intervention") or []),
        "library": lib,
        "intervention": inter,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CONTROL_ELIGIBLE_AS_WINNER": False,
        "checks": checks,
    }
