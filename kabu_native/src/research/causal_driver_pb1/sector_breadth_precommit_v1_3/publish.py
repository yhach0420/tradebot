"""Write V1.3 precommit artifacts. Does not overwrite V2 or prior precommits. No corrected betas."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.sector_breadth_precommit_v1_3 import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.isolation import DISC_V2_OUT, OUT, V12_OUT

SHEETS = (
    "Manifest",
    "V2_Invalidation",
    "Candidate_Binding",
    "C1_Confirmed_Set",
    "Old_Offset_Overlap",
    "New_Offset_Definition",
    "Window_Overlap_Proof",
    "Session_Feasibility",
    "Common_Offset_Sample",
    "Contamination",
    "Firewall",
    "Safety",
)


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900")


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if hasattr(x, "item") and type(x).__module__.startswith("numpy"):
        return _clean(x.item())
    if isinstance(x, float) and x != x:
        return None
    return x


def _df(rows: list[dict[str, Any]]):
    import pandas as pd

    if not rows:
        return pd.DataFrame([{"empty": True}])
    flat = []
    for r in rows:
        row = {}
        for k, v in r.items():
            if isinstance(v, (dict, list, tuple)):
                row[k] = json.dumps(_clean(v), ensure_ascii=False)
            else:
                row[k] = v
        flat.append(row)
    return pd.DataFrame(flat)


def _markdown(report: dict[str, Any]) -> str:
    a = report["answers"]
    inv = a.get("invalidation") or {}
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        f"- V2 classification `{inv.get('old_discovery_classification')}`",
        f"- invalidation `{inv.get('INVALIDATION_VERDICT')}`",
        f"- reason `{inv.get('reason')}`",
        f"- reason_raw `{a.get('reason_raw')}`",
        f"- decision_status `{a.get('decision_status')}`",
        f"- candidate_list_sha256 `{a.get('candidate_list_sha256')}`",
        f"- C1_confirmed_n `{a.get('C1_confirmed_n')}`",
        f"- c1_confirmed_set_sha256 `{a.get('c1_confirmed_set_sha256')}`",
        f"- all future windows non-overlap `{a.get('overlap_ok')}`",
        f"- common sample valid `{a.get('common_sample_valid')}`",
        f"- DEV/C1 reused unchanged `{a.get('DEV_C1_reused_unchanged')}`",
        f"- FV / Prospective `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}` / `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        f"- parent precommit SHA `{a.get('parent_precommit_sha256')}`",
        f"- new V1.3 SHA `{a.get('precommit_sha256')}`",
        f"- corrected offset beta opened `{a.get('corrected_offset_beta_opened')}`",
        "",
        "Corrected OFFSET betas are not computed. V2 artifacts are not overwritten. New driver acquisition is not started.",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    inv = evaluation.get("invalidation") or {}
    samples = (evaluation.get("common_sample") or {}).get("rows") or []
    cands = list(evaluation.get("candidates") or [])
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "reason": evaluation.get("reason"),
        "blockers": evaluation.get("blockers"),
        "precommit_id": evaluation.get("precommit_id"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "parent_precommit_sha256": evaluation.get("parent_precommit_sha256"),
        "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
        "c1_confirmed_set_sha256": evaluation.get("c1_confirmed_set_sha256"),
        "C1_confirmed_n": evaluation.get("C1_confirmed_n"),
        "DEV_candidate_n": evaluation.get("DEV_candidate_n"),
        "reason_raw": evaluation.get("reason_raw"),
        "decision_status": evaluation.get("decision_status"),
        "invalidation": {
            "INVALIDATION_VERDICT": inv.get("INVALIDATION_VERDICT"),
            "reason": inv.get("reason"),
            "old_discovery_classification": inv.get("old_discovery_classification"),
            "old_published_verdict": inv.get("old_published_verdict"),
            "old_published_next_suspended": inv.get("old_published_next_suspended"),
        },
        "overlap_ok": evaluation.get("overlap_ok"),
        "session_ok": evaluation.get("session_ok"),
        "common_sample_valid": bool((evaluation.get("common_sample") or {}).get("pass")),
        "DEV_C1_reused_unchanged": True,
        "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
        "C1_OUTCOMES_PREVIOUSLY_OPENED": True,
        "fresh_blind_first_look": False,
        "corrected_offset_beta_opened": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "v2_path": str(DISC_V2_OUT),
        "v1_2_path": str(V12_OUT),
    }
    slim = dict(evaluation)
    if isinstance(slim.get("identity"), dict):
        ident = dict(slim["identity"])
        ident["source_inventory"] = "omitted"
        slim["identity"] = ident
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": _now(),
        "answers": answers,
        "evaluation": _clean(slim),
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    proofs = []
    sessions = []
    defs = []
    for r in cands:
        od = r.get("offset_def") or {}
        defs.append(
            {
                "test_id": r.get("test_id"),
                "lookback": r.get("lookback"),
                "horizon": r.get("horizon"),
                "old_future": [1, 3, 5],
                "K1": od.get("K1"),
                "K2": od.get("K2"),
                "K3": od.get("K3"),
            }
        )
        for p in od.get("future_proofs") or []:
            proofs.append({"test_id": r.get("test_id"), **p})
        sessions.append(
            {
                "test_id": r.get("test_id"),
                "session_feasible": od.get("session_feasible"),
                "session_feasible_clock_n": od.get("session_feasible_clock_n"),
                "session_first": od.get("session_first"),
                "session_last": od.get("session_last"),
            }
        )
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "c1_confirmed_set_sha256": evaluation.get("c1_confirmed_set_sha256"),
                    "C1_confirmed_n": evaluation.get("C1_confirmed_n"),
                    "corrected_offset_beta_opened": False,
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df([inv or {"empty": True}]).to_excel(xw, sheet_name="V2_Invalidation", index=False)
        _df(
            [
                {
                    "parent_precommit_sha256": evaluation.get("parent_precommit_sha256"),
                    "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
                    "c1_confirmed_set_sha256": evaluation.get("c1_confirmed_set_sha256"),
                    "DEV_candidate_n": evaluation.get("DEV_candidate_n"),
                    "C1_confirmed_n": evaluation.get("C1_confirmed_n"),
                }
            ]
        ).to_excel(xw, sheet_name="Candidate_Binding", index=False)
        _df(cands or [{"empty": True}]).to_excel(xw, sheet_name="C1_Confirmed_Set", index=False)
        _df(list(inv.get("old_future_offset_rows") or [{"empty": True}])).to_excel(xw, sheet_name="Old_Offset_Overlap", index=False)
        _df(defs or [{"empty": True}]).to_excel(xw, sheet_name="New_Offset_Definition", index=False)
        _df(proofs or [{"empty": True}]).to_excel(xw, sheet_name="Window_Overlap_Proof", index=False)
        _df(sessions or [{"empty": True}]).to_excel(xw, sheet_name="Session_Feasibility", index=False)
        _df(samples or [{"empty": True}]).to_excel(xw, sheet_name="Common_Offset_Sample", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
