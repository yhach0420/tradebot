"""Publish the non-overlap offset correction. Does not overwrite V2 or V1.3."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.isolation import DISC_V2_OUT, OUT, V13_OUT

SHEETS = (
    "Manifest",
    "Identity",
    "Candidate_Binding",
    "Common_Sample",
    "NonOverlap_Proof",
    "Offset_Map",
    "Offset_Gates",
    "Offset_Passers",
    "Day_Shuffle",
    "Sector_Identity",
    "Driver_Concentration",
    "Target_Concentration",
    "Common_Factor",
    "Final_Decision",
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
        return pd.DataFrame([{"status": "NOT_RUN"}])
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
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        f"- reason `{a.get('reason')}`",
        f"- precommit_sha256 `{a.get('precommit_sha256')}`",
        f"- C1_confirmed_n `{a.get('C1_confirmed_n')}`",
        f"- offset_evaluated_n `{a.get('offset_evaluated_n')}`",
        f"- offset_pass_n `{a.get('offset_pass_n')}`",
        f"- shuffle_pass_n `{a.get('shuffle_pass_n')}`",
        f"- sector_identity_pass_n `{a.get('sector_identity_pass_n')}`",
        f"- driver_concentration_pass_n `{a.get('driver_concentration_pass_n')}`",
        f"- target_concentration_pass_n `{a.get('target_concentration_pass_n')}`",
        f"- common_factor_pass_n `{a.get('common_factor_pass_n')}`",
        f"- final_pass_n `{a.get('final_pass_n')}`",
        f"- FV `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}`",
        f"- Prospective `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        "",
    ]
    for r in a.get("offset_results") or []:
        lines.append(
            "- `{test_id}` b=[{m5}, {m3}, {m1}, {b0}, {k1}, {k2}, {k3}] O={o1}/{o2}/{o3}/{o4} pass={p} failed_at={f}".format(
                test_id=r.get("test_id"),
                m5=r.get("beta_-5"),
                m3=r.get("beta_-3"),
                m1=r.get("beta_-1"),
                b0=r.get("beta_0"),
                k1=r.get("beta_K1"),
                k2=r.get("beta_K2"),
                k3=r.get("beta_K3"),
                o1=r.get("O1"),
                o2=r.get("O2"),
                o3=r.get("O3"),
                o4=r.get("O4"),
                p=r.get("offset_pass"),
                f=r.get("failed_at"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

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
        "offset_evaluated_n": evaluation.get("offset_evaluated_n"),
        "offset_pass_n": evaluation.get("offset_pass_n"),
        "offset_results": evaluation.get("offset_results"),
        "shuffle_pass_n": evaluation.get("shuffle_pass_n"),
        "sector_identity_pass_n": evaluation.get("sector_identity_pass_n"),
        "driver_concentration_pass_n": evaluation.get("driver_concentration_pass_n"),
        "target_concentration_pass_n": evaluation.get("target_concentration_pass_n"),
        "common_factor_pass_n": evaluation.get("common_factor_pass_n"),
        "final_pass_n": evaluation.get("final_pass_n"),
        "final_candidates": evaluation.get("final_candidates"),
        "DEV_C1_reused_unchanged": True,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": _now(),
        "answers": answers,
        "evaluation": _clean(evaluation),
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    offsets = list(evaluation.get("offset_results") or [])
    passers = [r for r in offsets if r.get("offset_pass")] or [{"status": "NONE"}]
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df([{"verdict": evaluation.get("VERDICT"), "next": evaluation.get("NEXT"), "reason": evaluation.get("reason")}]).to_excel(
            xw, sheet_name="Manifest", index=False
        )
        _df(
            [
                {
                    "precommit_id": evaluation.get("precommit_id"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "parent_precommit_sha256": evaluation.get("parent_precommit_sha256"),
                    "candidate_list_sha256": evaluation.get("candidate_list_sha256"),
                    "c1_confirmed_set_sha256": evaluation.get("c1_confirmed_set_sha256"),
                    "permutation_sha256": evaluation.get("permutation_sha256"),
                    "v13_path": str(V13_OUT),
                    "v2_path": str(DISC_V2_OUT),
                }
            ]
        ).to_excel(xw, sheet_name="Identity", index=False)
        _df(offsets or [{"status": "NOT_RUN"}]).to_excel(xw, sheet_name="Candidate_Binding", index=False)
        _df(list(evaluation.get("sample_verification") or [{"status": "NOT_RUN"}])).to_excel(xw, sheet_name="Common_Sample", index=False)
        _df(list(evaluation.get("overlap_rows") or [{"status": "NOT_RUN"}])).to_excel(xw, sheet_name="NonOverlap_Proof", index=False)
        _df(offsets or [{"status": "NOT_RUN"}]).to_excel(xw, sheet_name="Offset_Map", index=False)
        _df(offsets or [{"status": "NOT_RUN"}]).to_excel(xw, sheet_name="Offset_Gates", index=False)
        _df(passers).to_excel(xw, sheet_name="Offset_Passers", index=False)
        _df(list(evaluation.get("day_shuffle") or [{"status": "NOT_RUN"}])).to_excel(xw, sheet_name="Day_Shuffle", index=False)
        _df(list(evaluation.get("sector_identity") or [{"status": "NOT_RUN"}])).to_excel(xw, sheet_name="Sector_Identity", index=False)
        _df(list(evaluation.get("driver_concentration") or [{"status": "NOT_RUN"}])).to_excel(xw, sheet_name="Driver_Concentration", index=False)
        _df(list(evaluation.get("target_concentration") or [{"status": "NOT_RUN"}])).to_excel(xw, sheet_name="Target_Concentration", index=False)
        _df(list(evaluation.get("common_factor") or [{"status": "NOT_RUN"}])).to_excel(xw, sheet_name="Common_Factor", index=False)
        _df(
            [
                {
                    "VERDICT": evaluation.get("VERDICT"),
                    "NEXT": evaluation.get("NEXT"),
                    "reason": evaluation.get("reason"),
                    "offset_pass_n": evaluation.get("offset_pass_n"),
                    "final_pass_n": evaluation.get("final_pass_n"),
                    "final_candidates": evaluation.get("final_candidates"),
                }
            ]
        ).to_excel(xw, sheet_name="Final_Decision", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
