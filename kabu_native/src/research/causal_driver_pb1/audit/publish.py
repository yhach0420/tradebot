"""Write report.json / report.md / audit.xlsx only under phase0 OUT."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from research.causal_driver_pb1 import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.datasets.roles import role_catalog
from research.causal_driver_pb1.isolation import OUT


SHEETS = (
    "Manifest",
    "Contracts",
    "Dataset_Roles",
    "Firewall",
    "Universe105",
    "Source_Identity",
    "Event_Time_Tests",
    "Determinism",
    "Runtime_NonImpact",
    "Safety",
)


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900")


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
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
    lines = [
        f"# {PROGRAM_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        "Phase 0 is foundation only. No USDJPY alpha, no sector PnL, no PB1 bind, no V5.",
        "",
        "## Identities",
        "",
        f"- PB1 V4 machine SHA `{a.get('V4_MACHINE_SHA256')}` unchanged=`{a.get('PB1_identity_unchanged')}`",
        f"- Complete Strategy SHA `{a.get('COMPLETE_STRATEGY_SHA256')}` unchanged=`{a.get('complete_strategy_identity_unchanged')}`",
        f"- Universe 105 count=`{a.get('universe105_count')}` sha_match=`{a.get('universe105_sha_match')}`",
        "",
        "## Gates",
        "",
        f"- contracts_created `{a.get('contracts_created')}`",
        f"- dataset_firewall_pass `{a.get('dataset_firewall_pass')}`",
        f"- economic_payload_firewall_pass `{a.get('economic_payload_firewall_pass')}`",
        f"- timestamp_semantics_pass `{a.get('timestamp_semantics_pass')}`",
        f"- timezone_pass `{a.get('timezone_pass')}`",
        f"- historical_runtime_transport_interface_ready `{a.get('historical_runtime_transport_interface_ready')}`",
        f"- determinism_pass `{a.get('determinism_pass')}`",
        f"- runtime_nonimpact_pass `{a.get('runtime_nonimpact_pass')}`",
        "",
        "## Safety",
        "",
        f"- FROZEN_VALIDATION_ECONOMIC_OPENED `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}`",
        f"- PROSPECTIVE_DATA_OPENED `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        f"- submit/cancel/live `{a.get('orders_submit')}/{a.get('orders_cancel')}/{a.get('orders_live')}`",
        "",
        "Phase 1 (USDJPY historical adapter only) is not started.",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    uni = evaluation.get("universe") or {}
    fw = evaluation.get("firewall") or {}
    time_r = evaluation.get("timestamp") or {}
    tr = evaluation.get("transport") or {}
    identity = evaluation.get("identity") or {}
    ni = evaluation.get("runtime_nonimpact") or {}
    tz_pass = any(r.get("name") == "timezone_naive_forbidden" and r.get("pass") for r in (time_r.get("rows") or [])) and any(
        r.get("name") == "timezone_jst_plus9" and r.get("pass") for r in (time_r.get("rows") or [])
    )
    econ_pass = any(r.get("name") == "fv_economic_payload" and r.get("pass") for r in (fw.get("rows") or []))
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "contracts_created": [
            "DriverObservation",
            "AlphaSignal",
            "SourceIdentity",
            "EventEnvelope",
            "MechanismRegistry",
            "ResearchObservationUniverse",
            "RuntimeTradeCandidateSet",
            "DriverTransport",
        ],
        "PB1_identity_unchanged": bool(identity.get("PB1_identity_unchanged")),
        "complete_strategy_identity_unchanged": bool(identity.get("complete_strategy_identity_unchanged")),
        "universe105_count": uni.get("universe105_count"),
        "universe105_sha_match": bool(uni.get("pass")),
        "universe105_sha": uni.get("universe105_sha"),
        "dataset_firewall_pass": bool(fw.get("pass")),
        "economic_payload_firewall_pass": bool(econ_pass),
        "timestamp_semantics_pass": bool(time_r.get("pass")),
        "timezone_pass": bool(tz_pass),
        "historical_runtime_transport_interface_ready": bool(tr.get("pass")),
        "determinism_pass": bool(any(r.get("name") == "determinism_ids" and r.get("pass") for r in (tr.get("rows") or []))),
        "runtime_nonimpact_pass": bool(ni.get("pass")),
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "orders_submit": 0,
        "orders_cancel": 0,
        "orders_live": 0,
        "V4_MACHINE_SHA256": identity.get("V4_MACHINE_SHA256"),
        "COMPLETE_STRATEGY_SHA256": identity.get("COMPLETE_STRATEGY_SHA256"),
        "dataset_role_config_sha256": identity.get("dataset_role_config_sha256"),
        "contract_schema_sha256": identity.get("contract_schema_sha256"),
        "firewall_config_sha256": identity.get("firewall_config_sha256"),
        "blockers": evaluation.get("blockers"),
        "run_mode": evaluation.get("run_mode"),
    }
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": _now(),
        "answers": answers,
        "evaluation": {
            k: v
            for k, v in evaluation.items()
            if k != "identity" or True
        },
        "identity_bind": {k: v for k, v in identity.items() if k != "source_inventory"},
        "source_inventory_keys": sorted((identity.get("source_inventory") or {}).keys()),
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    # Drop bulky file-hash maps from json
    if isinstance(report["evaluation"].get("identity"), dict):
        inv = dict(report["evaluation"]["identity"])
        inv["source_inventory"] = "omitted_see_audit_Source_Identity"
        report["evaluation"]["identity"] = inv
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    roles = [
        {
            "role": s.role.value,
            "first": s.first,
            "last": s.last,
            "semantic_exposed": s.semantic_exposed,
            "economic_opened": s.economic_opened,
            "economic_payload_access": s.economic_payload_access,
            "not_fully_blind": s.not_fully_blind,
            "description": s.description,
        }
        for s in role_catalog()
    ]
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "program": PROGRAM_ID,
                    "analysis": ANALYSIS_ID,
                    "verdict": answers["VERDICT"],
                    "next": answers["NEXT"],
                    "created_at": _now(),
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df([{"contract": n} for n in answers["contracts_created"]]).to_excel(xw, sheet_name="Contracts", index=False)
        _df(roles).to_excel(xw, sheet_name="Dataset_Roles", index=False)
        _df(list(fw.get("rows") or [])).to_excel(xw, sheet_name="Firewall", index=False)
        _df(
            [
                {
                    "count": uni.get("universe105_count"),
                    "sha": uni.get("universe105_sha"),
                    "symbols_order_sha": uni.get("universe105_symbols_order_sha"),
                    "path": uni.get("source_path"),
                    "pass": uni.get("pass"),
                }
            ]
        ).to_excel(xw, sheet_name="Universe105", index=False)
        _df(
            [
                {
                    "V4_MACHINE_SHA256": identity.get("V4_MACHINE_SHA256"),
                    "COMPLETE_STRATEGY_SHA256": identity.get("COMPLETE_STRATEGY_SHA256"),
                    "contract_schema_sha256": identity.get("contract_schema_sha256"),
                    "dataset_role_config_sha256": identity.get("dataset_role_config_sha256"),
                    "firewall_config_sha256": identity.get("firewall_config_sha256"),
                }
            ]
        ).to_excel(xw, sheet_name="Source_Identity", index=False)
        _df(list(time_r.get("rows") or [])).to_excel(xw, sheet_name="Event_Time_Tests", index=False)
        _df(list(tr.get("rows") or [])).to_excel(xw, sheet_name="Determinism", index=False)
        _df(list(ni.get("rows") or [])).to_excel(xw, sheet_name="Runtime_NonImpact", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
