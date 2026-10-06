"""Write the three precommit artifacts. No future-return outcome sheets."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.cross_sectional_precommit import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.cross_sectional_precommit.isolation import OUT

SHEETS = (
    "Manifest",
    "USDJPY_Final_Closeout",
    "Driver_Finalization",
    "Universe105",
    "Sector_Mapping",
    "Leader_Eligibility",
    "Sector_Liquidity",
    "Leader_Liquidity",
    "Frozen_Leader_Set",
    "Frozen_Target_Set",
    "Eligible_Days",
    "Folds",
    "Timestamp_Semantics",
    "Freshness",
    "Research_Family",
    "Models",
    "DEV_Gates",
    "C1_Gates",
    "Placebos",
    "Concentration",
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
    parent = a.get("parent_usdjpy") or {}
    lines = [
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        f"- FINAL_NEXT_DRIVER_VERDICT `{a.get('FINAL_NEXT_DRIVER_VERDICT')}`",
        f"- PRIMARY_NEXT_DRIVER `{a.get('PRIMARY_NEXT_DRIVER')}`",
        f"- DRIVER_FAMILY_ID `{a.get('DRIVER_FAMILY_ID')}`",
        f"- BACKUP `{a.get('BACKUP_DRIVER')}`",
        f"- futures `{a.get('FUTURES_DRIVER_DATA_NOT_READY')}`",
        f"- USDJPY parent `{parent.get('VERDICT')}` status `{parent.get('USDJPY_DRIVER_FAMILY_STATUS')}`",
        f"- USDJPY_REOPENED `{a.get('USDJPY_REOPENED')}`",
        f"- leader_n `{a.get('leader_n')}`",
        f"- leader_set_sha256 `{a.get('leader_set_sha256')}`",
        f"- target_set_sha256 `{a.get('target_set_sha256')}`",
        f"- disjoint `{a.get('disjoint')}`",
        f"- LEADER_SET_FROZEN_BEFORE_OUTCOME `{a.get('LEADER_SET_FROZEN_BEFORE_OUTCOME')}`",
        f"- eligible_day_sha256 `{a.get('eligible_day_sha256')}`",
        f"- fold_boundary_sha256 `{a.get('fold_boundary_sha256')}`",
        f"- permutation_sha256 `{a.get('permutation_sha256')}`",
        f"- precommit_sha256 `{a.get('precommit_sha256')}`",
        f"- family_n `{a.get('family_n')}`",
        f"- LEADER_LAGGARD_OUTCOMES_OPENED `{a.get('LEADER_LAGGARD_OUTCOMES_OPENED')}`",
        f"- C1 outcomes opened `{a.get('C1_outcomes_opened')}`",
        f"- FV / prospective `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}` / `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        "",
        "Discovery is not run.",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    parent = evaluation.get("parent_usdjpy") or {}
    leaders = evaluation.get("leaders") or {}
    days = evaluation.get("days") or {}
    folds = days.get("folds") or {}
    contract = evaluation.get("contract") or {}
    sectors = evaluation.get("sectors") or {}
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "FINAL_NEXT_DRIVER_VERDICT": evaluation.get("FINAL_NEXT_DRIVER_VERDICT"),
        "reason": evaluation.get("reason"),
        "blockers": evaluation.get("blockers"),
        "precommit_id": evaluation.get("precommit_id"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "PRIMARY_NEXT_DRIVER": evaluation.get("PRIMARY_NEXT_DRIVER"),
        "DRIVER_FAMILY_ID": evaluation.get("DRIVER_FAMILY_ID"),
        "BACKUP_DRIVER": evaluation.get("BACKUP_DRIVER"),
        "FUTURES_DRIVER_DATA_NOT_READY": evaluation.get("FUTURES_DRIVER_DATA_NOT_READY"),
        "parent_usdjpy": parent,
        "USDJPY_REOPENED": False,
        "LEADER_LAGGARD_OUTCOMES_OPENED": False,
        "C1_outcomes_opened": False,
        "leader_n": evaluation.get("leader_n"),
        "leader_set_sha256": evaluation.get("leader_set_sha256"),
        "target_n": evaluation.get("target_n"),
        "target_set_sha256": evaluation.get("target_set_sha256"),
        "disjoint": evaluation.get("disjoint"),
        "LEADER_SET_FROZEN_BEFORE_OUTCOME": True,
        "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
        "eligible_dev_n": evaluation.get("eligible_dev_n"),
        "eligible_c1_n": evaluation.get("eligible_c1_n"),
        "fold_boundary_sha256": evaluation.get("fold_boundary_sha256"),
        "permutation_sha256": evaluation.get("permutation_sha256"),
        "family_n": evaluation.get("family_n"),
        "universe105_sha256": sectors.get("universe105_sha256"),
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
        "discovery_not_run": True,
    }
    slim = dict(evaluation)
    if isinstance(slim.get("identity"), dict):
        ident = dict(slim["identity"])
        ident["source_inventory"] = "omitted"
        slim["identity"] = ident
    if isinstance(slim.get("days"), dict):
        slim["days"] = {k: v for k, v in slim["days"].items() if k not in {"rows"}}
    slim.pop("day_rows", None)
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
    frozen = list(leaders.get("frozen_leaders") or [])
    elig = list(leaders.get("eligibility_rows") or [])
    sec_liq = list(leaders.get("sector_liquidity") or [])
    targets = [{"symbol": s} for s in (leaders.get("target_symbols") or [])]
    mapping = list(sectors.get("rows") or [])
    day_rows = list(evaluation.get("day_rows") or [])
    fold_dates = []
    for name in ("DEV_EARLY", "DEV_LATE", "C1_EARLY", "C1_MIDDLE", "C1_LATE"):
        for day in folds.get(name) or []:
            fold_dates.append({"fold": name, "date": day})
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "verdict": evaluation.get("VERDICT"),
                    "next": evaluation.get("NEXT"),
                    "final_next_driver": evaluation.get("FINAL_NEXT_DRIVER_VERDICT"),
                    "precommit_sha256": evaluation.get("precommit_sha256"),
                    "leader_set_sha256": evaluation.get("leader_set_sha256"),
                    "target_set_sha256": evaluation.get("target_set_sha256"),
                    "eligible_day_sha256": evaluation.get("eligible_day_sha256"),
                    "family_n": evaluation.get("family_n"),
                    "discovery_not_run": True,
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df([parent or {"empty": True}]).to_excel(xw, sheet_name="USDJPY_Final_Closeout", index=False)
        _df(
            [
                {
                    "PRIMARY_NEXT_DRIVER": evaluation.get("PRIMARY_NEXT_DRIVER"),
                    "DRIVER_FAMILY_ID": evaluation.get("DRIVER_FAMILY_ID"),
                    "BACKUP_DRIVER": evaluation.get("BACKUP_DRIVER"),
                    "FUTURES_DRIVER_DATA_NOT_READY": evaluation.get("FUTURES_DRIVER_DATA_NOT_READY"),
                    "phase0_enum_mutated": evaluation.get("phase0_DriverFamily_enum_mutated"),
                    "USDJPY_REOPENED": False,
                }
            ]
        ).to_excel(xw, sheet_name="Driver_Finalization", index=False)
        _df(
            [
                {
                    "n": 105,
                    "sha256": sectors.get("universe105_sha256"),
                    "sector_mapping_sha256": sectors.get("sector_mapping_sha256"),
                }
            ]
        ).to_excel(xw, sheet_name="Universe105", index=False)
        _df(mapping).to_excel(xw, sheet_name="Sector_Mapping", index=False)
        _df(elig).to_excel(xw, sheet_name="Leader_Eligibility", index=False)
        _df(sec_liq).to_excel(xw, sheet_name="Sector_Liquidity", index=False)
        _df(elig).to_excel(xw, sheet_name="Leader_Liquidity", index=False)
        _df(frozen or [{"empty": True}]).to_excel(xw, sheet_name="Frozen_Leader_Set", index=False)
        _df(targets or [{"empty": True}]).to_excel(xw, sheet_name="Frozen_Target_Set", index=False)
        _df(day_rows or [{"empty": True}]).to_excel(xw, sheet_name="Eligible_Days", index=False)
        _df(
            fold_dates
            or [
                {
                    "fold_boundary_sha256": folds.get("fold_boundary_sha256"),
                    "eligible_dev_n": evaluation.get("eligible_dev_n"),
                    "eligible_c1_n": evaluation.get("eligible_c1_n"),
                }
            ]
        ).to_excel(xw, sheet_name="Folds", index=False)
        _df([evaluation.get("stock") or {}]).to_excel(xw, sheet_name="Timestamp_Semantics", index=False)
        _df([(contract.get("freshness") or {"empty": True})]).to_excel(xw, sheet_name="Freshness", index=False)
        _df(
            [
                {
                    "lookbacks": (contract.get("family") or {}).get("lookbacks"),
                    "horizons": (contract.get("family") or {}).get("horizons"),
                    "n": (contract.get("family") or {}).get("n"),
                    "scopes": (contract.get("scopes") or []),
                }
            ]
        ).to_excel(xw, sheet_name="Research_Family", index=False)
        _df([(contract.get("models") or {"empty": True})]).to_excel(xw, sheet_name="Models", index=False)
        _df([(contract.get("dev_gates") or {"empty": True})]).to_excel(xw, sheet_name="DEV_Gates", index=False)
        _df([(contract.get("c1_gates") or {"empty": True})]).to_excel(xw, sheet_name="C1_Gates", index=False)
        _df([(contract.get("placebos") or {"empty": True})]).to_excel(xw, sheet_name="Placebos", index=False)
        _df([(contract.get("concentration") or {"empty": True})]).to_excel(xw, sheet_name="Concentration", index=False)
        _df([evaluation.get("contamination") or {}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df([evaluation.get("firewall") or {}]).to_excel(xw, sheet_name="Firewall", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
