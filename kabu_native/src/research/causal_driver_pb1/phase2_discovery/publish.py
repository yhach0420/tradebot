"""Write the three discovery artifacts. No bulk CSV."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.phase2_discovery import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.phase2_discovery.isolation import OUT

SHEETS = (
    "Manifest",
    "Identity",
    "Eligible_Days",
    "Data_Coverage",
    "DEV_All_288",
    "DEV_Gates",
    "DEV_Candidates",
    "Candidate_Freeze",
    "C1_Confirmation",
    "Offset_Map",
    "Day_Shuffle",
    "Concentration",
    "Missingness",
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


def _df(rows: list[dict[str, Any]]) -> pd.DataFrame:
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
    finals = a.get("final_candidates") or []
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        f"- precommit_sha256 `{a.get('precommit_sha256')}`",
        f"- DEV_candidate_n `{a.get('DEV_candidate_n')}`",
        f"- candidate_list_sha256 `{a.get('candidate_list_sha256')}`",
        f"- C1_rows_read_before_candidate_freeze `{a.get('C1_rows_read_before_candidate_freeze')}`",
        f"- C1_confirmed_n `{a.get('C1_confirmed_n')}`",
        f"- lead_gate_pass_n `{a.get('lead_gate_pass_n')}`",
        f"- day_shuffle_pass_n `{a.get('day_shuffle_pass_n')}`",
        f"- concentration_pass_n `{a.get('concentration_pass_n')}`",
        f"- final_pass_n `{a.get('final_pass_n')}`",
        f"- DEV_all_288_n `{a.get('DEV_all_288_n')}`",
        "",
        "## Final candidates",
        "",
    ]
    if not finals:
        lines.append("None.")
    else:
        for r in finals:
            lines.append(
                f"- `{r.get('target_scope')}` lookback={r.get('fx_lookback')}h={r.get('response_horizon')} "
                f"dir={r.get('direction')} DEV_b={r.get('DEV_b_fx')} C1_b={r.get('C1_b_fx')}"
            )
    lines += [
        "",
        f"OLD_RESULT_OVERLAP `{json.dumps(a.get('OLD_RESULT_OVERLAP'), ensure_ascii=False)}`",
        "",
        f"FROZEN_VALIDATION_OPENED `{a.get('FROZEN_VALIDATION_OPENED')}`",
        f"PROSPECTIVE_DATA_OPENED `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        f"ALPHA_CREATED `{a.get('ALPHA_CREATED')}` PB1_BOUND `{a.get('PB1_BOUND')}`",
        "",
        "Phase 3 is not started.",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "reason": evaluation.get("reason"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "DEV_candidate_n": evaluation.get("DEV_candidate_n"),
        "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
        "C1_rows_read_before_candidate_freeze": evaluation.get("C1_rows_read_before_candidate_freeze"),
        "C1_confirmed_n": evaluation.get("C1_confirmed_n"),
        "lead_gate_pass_n": evaluation.get("lead_gate_pass_n"),
        "day_shuffle_pass_n": evaluation.get("day_shuffle_pass_n"),
        "concentration_pass_n": evaluation.get("concentration_pass_n"),
        "final_pass_n": evaluation.get("final_pass_n"),
        "final_candidates": evaluation.get("final_candidates"),
        "top_failed_candidates": evaluation.get("top_failed_candidates"),
        "failure_stage_counts": evaluation.get("failure_stage_counts"),
        "DEV_all_288_n": evaluation.get("DEV_all_288_n"),
        "eligible_dev_n": evaluation.get("eligible_dev_n"),
        "eligible_c1_n": evaluation.get("eligible_c1_n"),
        "missingness_status": evaluation.get("missingness_status"),
        "FROZEN_VALIDATION_OPENED": False,
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
        "OLD_RESULT_OVERLAP": evaluation.get("OLD_RESULT_OVERLAP"),
        "blockers": evaluation.get("blockers"),
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
        "evaluation": slim,
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    family = list(evaluation.get("family") or [])
    payload = list(evaluation.get("candidate_payload") or [])
    c1 = list(evaluation.get("c1") or [])
    access = evaluation.get("access") or {}
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
                    "DEV_candidate_n": evaluation.get("DEV_candidate_n"),
                    "final_pass_n": evaluation.get("final_pass_n"),
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df(
            [
                {
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
                    "C1_rows_read_before_candidate_freeze": evaluation.get("C1_rows_read_before_candidate_freeze"),
                    "access": access,
                }
            ]
        ).to_excel(xw, sheet_name="Identity", index=False)
        _df(
            [
                {
                    "eligible_dev_n": evaluation.get("eligible_dev_n"),
                    "eligible_c1_n": evaluation.get("eligible_c1_n"),
                }
            ]
        ).to_excel(xw, sheet_name="Eligible_Days", index=False)
        _df([evaluation.get("missingness") or {"coverage": True}]).to_excel(xw, sheet_name="Data_Coverage", index=False)
        _df(family).to_excel(xw, sheet_name="DEV_All_288", index=False)
        _df(
            [
                {
                    "target_scope": r.get("target_scope"),
                    "fx_lookback": r.get("fx_lookback"),
                    "response_horizon": r.get("response_horizon"),
                    "D1": r.get("D1"),
                    "D2": r.get("D2"),
                    "D3": r.get("D3"),
                    "D4": r.get("D4"),
                    "D5": r.get("D5"),
                    "D6": r.get("D6"),
                    "candidate": r.get("candidate"),
                }
                for r in family
            ]
            or [{"empty": True}]
        ).to_excel(xw, sheet_name="DEV_Gates", index=False)
        _df(payload or [{"none": True}]).to_excel(xw, sheet_name="DEV_Candidates", index=False)
        _df(
            [
                {
                    "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
                    "n": len(payload),
                    "STAGE_A_COMPLETE": access.get("STAGE_A_COMPLETE"),
                    "C1_ROWS_READ_BEFORE_CANDIDATE_FREEZE": access.get("C1_ROWS_READ_BEFORE_CANDIDATE_FREEZE"),
                }
            ]
        ).to_excel(xw, sheet_name="Candidate_Freeze", index=False)
        _df(c1 or [{"c1_not_opened": not evaluation.get("C1_opened")}]).to_excel(xw, sheet_name="C1_Confirmation", index=False)
        _df([{"lead": r.get("lead"), **{k: r.get(k) for k in ("target_scope", "fx_lookback", "response_horizon")}} for r in c1] or [{"empty": True}]).to_excel(
            xw, sheet_name="Offset_Map", index=False
        )
        _df([{"shuffle": r.get("shuffle"), **{k: r.get(k) for k in ("target_scope", "fx_lookback", "response_horizon")}} for r in c1] or [{"empty": True}]).to_excel(
            xw, sheet_name="Day_Shuffle", index=False
        )
        _df(
            [{"concentration": r.get("concentration"), **{k: r.get(k) for k in ("target_scope", "fx_lookback", "response_horizon")}} for r in c1]
            or [{"empty": True}]
        ).to_excel(xw, sheet_name="Concentration", index=False)
        _df([evaluation.get("missingness") or {}]).to_excel(xw, sheet_name="Missingness", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([{"a": evaluation.get("firewall_a"), "b": evaluation.get("firewall_b")}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
