"""Publish the complete-strategy precommit. Does not replay trades."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.sector_state_alpha_complete_precommit import ANALYSIS_ID, CASE_BLOCKED, CASE_READY, NEXT_READY
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.contract import contract
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.isolation import OUT, assert_write_root, write_overlap_n


def build() -> dict[str, Any]:
    frozen = contract()
    ready = bool(frozen.get("ready"))
    return {
        "verdict": CASE_READY if ready else CASE_BLOCKED,
        "next": NEXT_READY if ready else str((frozen.get("missing_contracts") or ["MISSING_CONTRACT"])[0]),
        "contract": frozen,
    }


def publish(result: dict[str, Any]) -> dict[str, str]:
    import pandas as pd

    assert_write_root()
    frozen = result["contract"]
    answers = {
        "VERDICT": result["verdict"],
        "NEXT": result["next"],
        "old_complete_strategy_sha256": frozen.get("supersedes"),
        "NEW_ALPHA_COMPLETE_STRATEGY_SHA256": frozen.get("NEW_ALPHA_COMPLETE_STRATEGY_SHA256"),
        "old_sha_superseded_before_pnl": True,
        "alpha_invalidation_family_sha256": frozen["alpha_position_invalidation"]["family_sha256"],
        "E0_ID": frozen["adapter"]["E0_ID"],
        "E0_SHA256": frozen["adapter"]["E0_SHA256"],
        "E1_ID": frozen["adapter"]["E1_ID"],
        "E1_SHA256": frozen["adapter"]["E1_SHA256"],
        "OCCUPANCY_ENGINE_ID": frozen["portfolio"]["OCCUPANCY_ENGINE_ID"],
        "OCCUPANCY_ENGINE_SHA256": frozen["portfolio"]["OCCUPANCY_ENGINE_SHA256"],
        "ECONOMIC_REPLAY_RUNNER_SHA256": frozen.get("ECONOMIC_REPLAY_RUNNER_SHA256"),
        "LAST_ENTRY_ADMISSION_CLOCK": frozen["observability"]["last_entry_admission_clock"],
        "ALPHA_POSITION_PM_CARRY": False,
        "missing_contracts": frozen.get("missing_contracts"),
        "economic_replay": "NOT_RUN",
        "PROSPECTIVE_DATA_OPENED": False,
        "prospective_rows_read": 0,
        "ALPHA_CREATED": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "cancelled_activation": "ACTIVATE_SECTOR_STATE_ALPHA_SIGNAL_SHADOW_PROSPECTIVE_V1",
    }
    doc = {"analysis_id": ANALYSIS_ID, "answers": answers, "contract": frozen, "safety": {
        "research_only": True,
        "ALPHA_CREATED": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "V4_CHANGED": False,
        "V5_CREATED": False,
    }}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(
        "\n".join([
            f"# {ANALYSIS_ID}",
            "",
            f"VERDICT: {answers['VERDICT']}",
            f"NEXT: {answers['NEXT']}",
            "",
            f"old SHA: {answers['old_complete_strategy_sha256']}",
            f"new SHA: {answers['NEW_ALPHA_COMPLETE_STRATEGY_SHA256']}",
            "",
            "The previous SHA is superseded before any economic replay. Q60_STATE_LOSS is unchanged.",
            "11:25 ends observable Alpha risk by FAIL_CLOSE, not by declaring the economic thesis false.",
            "No new entry is admitted after 11:24. Economic replay was not run.",
            "",
        ]),
        encoding="utf-8",
    )
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as writer:
        def put(name: str, rows: list[dict[str, Any]]) -> None:
            pd.DataFrame(rows).to_excel(writer, sheet_name=name[:31], index=False)
        put("Manifest", [{"field": k, "value": str(v)} for k, v in answers.items()])
        put("Alpha_Binding", [frozen["alpha"]])
        put("Invalidation_Family", frozen["alpha_position_invalidation"]["admitted"] + [{"candidate_id": x, "admitted": False} for x in frozen["alpha_position_invalidation"]["not_admitted"]])
        put("PB1_Thesis", [frozen["pb1"]])
        put("Observability", [frozen["observability"]])
        put("Timing", [{"E0_ID": frozen["adapter"]["E0_ID"], "E0_SHA256": frozen["adapter"]["E0_SHA256"], "E1_ID": frozen["adapter"]["E1_ID"], "E1_SHA256": frozen["adapter"]["E1_SHA256"]}])
        put("Execution", [frozen["execution"]])
        put("Portfolio", [frozen["portfolio"]])
        put("Event_Priority", [{"ordinal": i, "event": e} for i, e in enumerate(frozen["event_priority"], start=1)])
        put("Session_Close", [frozen["position"]])
        put("Economic_Gate", [frozen["economic_gate"]])
        put("Chronology", [{"fold": k, "window": v} for k, v in frozen["chronology"]["folds"].items()])
        put("Prospective_Firewall", [{"opened": False, "rows_read": 0, "cancelled_next": answers["cancelled_activation"]}])
        put("Shadow_Infrastructure", [{"class": "FUTURE_RUNTIME_INFRASTRUCTURE_ONLY", "activated": False}])
        put("Safety", [doc["safety"]])
    if write_overlap_n("", ""):
        raise RuntimeError("complete_precommit_write_isolation_failed")
    return {"verdict": answers["VERDICT"], "sha": str(answers["NEW_ALPHA_COMPLETE_STRATEGY_SHA256"] or "")}
