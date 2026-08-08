"""X29 precommit V2 after successful/mixed X28D stress (no 0810 open)."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    OLD_X29_PRECOMMIT_SHA,
    OLD_X29_RUN_ID,
    PROSPECTIVE_FIRST_DAY,
    STRESS_DAYS,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
X29_DIR = NATIVE / "results" / "research" / "e1_x29_prospective"
X28D_DIR = NATIVE / "results" / "research" / "e1_x28d_additional_stress"


def build_x29_v2(*, x28d_run_id: str, x28d_verdict: str, x28d_precommit_sha: str) -> dict[str, Any]:
    old = json.loads((X29_DIR / "precommit.json").read_text(encoding="utf-8"))
    if old.get("precommit_sha") != OLD_X29_PRECOMMIT_SHA:
        raise RuntimeError("cannot build V2 — old sha mismatch")

    now = datetime.now(JST)
    run_id = f"e1x29_precommit_v2_{now.strftime('%Y%m%d_%H%M%S')}_A"
    body = deepcopy(old)
    body["precommit_id"] = "X29_PRECOMMIT_V2_FROZEN"
    body["run_id"] = run_id
    body["verdict"] = "X29_PRECOMMIT_V2_FROZEN"
    body["precommit_timestamp"] = now.isoformat()
    body["precommit_version"] = 2
    body["supersedes"] = {
        "old_run_id": OLD_X29_RUN_ID,
        "old_precommit_sha": OLD_X29_PRECOMMIT_SHA,
        "status": "SUPERSEDED_NOT_PROSPECTIVE_OPENED",
        "reason": "ADDITIONAL_HISTORICAL_STRESS_VALIDATION_20260805_20260807",
        "20260810_market_data_not_opened": True,
        "prospective_observer_not_started": True,
        "no_prospective_evidence_consumed": True,
    }
    body["source_x28d"] = {
        "run_id": x28d_run_id,
        "verdict": x28d_verdict,
        "precommit_sha": x28d_precommit_sha,
        "stress_days": list(STRESS_DAYS),
        "role": "CONSUMED_ADDITIONAL_HISTORICAL_STRESS",
    }
    # Mark stress days consumed for additional historical stress (not prospective)
    pw = body.setdefault("prospective_window", {})
    consumed = list(pw.get("consumed_alpha_dates") or [])
    # Do NOT add 0805-07 into consumed_alpha_dates as alpha — separate field
    body["consumed_additional_historical_stress"] = list(STRESS_DAYS)
    body["consumed_additional_historical_stress_role"] = "CONSUMED_ADDITIONAL_HISTORICAL_STRESS"
    body["first_eligible_prospective_day"] = PROSPECTIVE_FIRST_DAY
    # Preserve cohorts exactly
    assert body["cohorts"]["PROSPECTIVE_SPECIFIC_49"]["n"] == 49
    assert body["cohorts"]["PROSPECTIVE_FAMILY_PREFERRED_118"]["n"] == 118
    body["no_parameter_retune"] = True
    body["no_cohort_retune"] = True
    body["market_data_not_opened"] = True
    body["observer_not_started"] = True
    body["20260810_not_opened"] = True

    # Recompute SHA excluding itself
    body.pop("precommit_sha", None)
    body.pop("published_shas", None)
    sha = sha256_obj(body)
    body["precommit_sha"] = sha
    body["published_shas"] = {"precommit_sha": sha, "old_superseded_sha": OLD_X29_PRECOMMIT_SHA}

    X29_DIR.mkdir(parents=True, exist_ok=True)
    (X29_DIR / "precommit_v2.json").write_text(
        json.dumps(body, indent=2, default=str), encoding="utf-8",
    )
    # Supersede marker for old (do not overwrite/delete old precommit.json)
    marker = {
        "old_run_id": OLD_X29_RUN_ID,
        "old_precommit_sha": OLD_X29_PRECOMMIT_SHA,
        "status": "SUPERSEDED_NOT_PROSPECTIVE_OPENED",
        "also": "SUPERSEDED_BEFORE_PROSPECTIVE_MARKET_OPEN",
        "reason": "ADDITIONAL_HISTORICAL_STRESS_VALIDATION_20260805_20260807",
        "superseded_by_run_id": run_id,
        "superseded_by_sha": sha,
        "20260810_market_data_not_opened": True,
        "prospective_observer_not_started": True,
        "no_prospective_evidence_consumed": True,
        "at": now.isoformat(),
    }
    (X29_DIR / "precommit_superseded.json").write_text(
        json.dumps(marker, indent=2), encoding="utf-8",
    )
    (X28D_DIR / "x29_v2_precommit_sha.txt").write_text(sha + "\n", encoding="utf-8")
    return body
