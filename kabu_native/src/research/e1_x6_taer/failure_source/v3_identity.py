"""Annotate V2 verdict scope without overwriting V2 economics/report."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
V2_RUN = "e1x6_taer_fsa_20260804_011807"
V2_STORE = Path.home() / "e1x6_research_store" / "taer" / V2_RUN

# Locked V2 identities (must match)
LOCKED_EPISODE_SHA = "11f7204d983112bec85dcaabb930913c4e690f62a78aa5a17ebcbe9ec1091695"
LOCKED_CLUSTER_SHA = "97916191cf6c12e5c6d121c8f95425286a6338866adb27803eb99818810e271e"
LOCKED_OPPORTUNITY_SHA = "7bea8a84d2eb53dfa4f4bd98bbc40f4fb11443c670a219607a50ec11831e521d"

ANALYSIS_ID_V3 = "E1_X6_TAER_FAILURE_SOURCE_ANALYSIS_V3"
PURPOSE_V3 = "OPPORTUNITY_LABEL_CONTRACT_REPAIR_AND_FEATURE_STABILITY"


def annotate_v2_scope() -> dict:
    body = {
        "run_id": V2_RUN,
        "verdict": "TAER_FAILURE_ANALYSIS_INSUFFICIENT_LABEL_QUALITY",
        "meaning_limited_to": "FSA_V2_STOPPED_BY_SCENARIO_BASED_LABEL_QUALITY_GATE",
        "not_evidence_for": [
            "TAERに利益機会がない",
            "ENTRY時点に安定情報がない",
            "TAER familyを終了すべき",
        ],
        "taer_v1_closeout_unchanged": True,
        "annotated_at_jst": datetime.now(JST).isoformat(),
        "overwrite_v2_forbidden": True,
    }
    fp = V2_STORE / "VERDICT_SCOPE.json"
    if not fp.exists():
        fp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return body
