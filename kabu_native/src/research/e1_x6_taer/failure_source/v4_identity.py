"""FSA V4 identity — annotate V3 scope; lock frozen SHAs."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

V3_RUN = "e1x6_taer_fsa_v3_20260804_043255"
V3_STORE = Path.home() / "e1x6_research_store" / "taer" / V3_RUN

LOCKED_EPISODE_SHA = "11f7204d983112bec85dcaabb930913c4e690f62a78aa5a17ebcbe9ec1091695"
LOCKED_CLUSTER_SHA = "97916191cf6c12e5c6d121c8f95425286a6338866adb27803eb99818810e271e"
LOCKED_OPPORTUNITY_SHA = "7bea8a84d2eb53dfa4f4bd98bbc40f4fb11443c670a219607a50ec11831e521d"
LOCKED_TARGET_VALIDITY_SHA = "48cef4fe47d1f0af22ebd2186d8582966372b356d337ff39cc53ef79a79e33d4"

ANALYSIS_ID_V4 = "E1_X6_TAER_FAILURE_SOURCE_ANALYSIS_V4"
PURPOSE_V4 = "STABILITY_GATE_CONTRACT_REPAIR"


def annotate_v3_scope() -> dict:
    body = {
        "run_id": V3_RUN,
        "reported_verdict": "TAER_TRIGGER_ANCHORED_FAMILY_NO_STABLE_ENTRY_SIGNAL",
        "meaning_limited_to": "FSA_V3_STOPPED_BY_INVALID_DAY_CLASS_SUPPORT_GATE",
        "reason": (
            "non_opportunity_days used sign of daily median best_net_pnl_bps_300s; "
            "all 9 day medians were positive so non_opportunity_days=0 and all "
            "stable_candidate=false despite per-day negative net_plus_5bps clusters"
        ),
        "not_evidence_for": [
            "TAERに安定ENTRY情報がない",
            "新family仮説を作る根拠がない",
            "TAER trigger-anchored familyを分析上終了すべき",
        ],
        "taer_v1_closeout_unchanged": True,
        "annotated_at_jst": datetime.now(JST).isoformat(),
        "overwrite_v3_forbidden": True,
    }
    fp = V3_STORE / "VERDICT_SCOPE.json"
    if not fp.exists():
        fp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return body
