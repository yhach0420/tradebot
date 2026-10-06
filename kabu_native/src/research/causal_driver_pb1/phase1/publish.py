"""Write report.json / report.md / audit.xlsx only under phase1 OUT."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.phase1 import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.phase1.isolation import OUT

SHEETS = (
    "Manifest",
    "Source_Identity",
    "Raw_Inventory",
    "Schema",
    "Timestamp_Semantics",
    "BidAsk_Alignment",
    "Duplicates",
    "Gaps",
    "Japan_Session_Coverage",
    "Transport",
    "Determinism",
    "Firewall",
    "Contamination",
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
        f"# {PROGRAM_ID} / {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{a.get('VERDICT')}`",
        "",
        f"**NEXT:** `{a.get('NEXT')}`",
        "",
        "Phase 1 maps historical USDJPY 1m Bid/Ask onto causally correct DriverObservation.",
        "No USDJPY alpha, no sector/symbol response, no PB1 bind, no Complete Strategy.",
        "",
        "## Source / coverage",
        "",
        f"- provider `{a.get('source_provider')}` id `{a.get('source_id')}`",
        f"- instrument `{a.get('instrument')}` resolution `{a.get('resolution')}`",
        f"- JST `{a.get('source_start')}`–`{a.get('source_end')}`",
        f"- raw Bid `{a.get('raw_bid_n')}` raw Ask `{a.get('raw_ask_n')}` paired `{a.get('paired_bar_n')}`",
        f"- duplicates `{a.get('duplicate_n')}` conflicting `{a.get('conflicting_duplicate_n')}`",
        f"- unexpected gaps `{a.get('unexpected_gap_n')}`",
        f"- Japan-session days `{a.get('coverage_days')}` min `{a.get('coverage_min')}` median `{a.get('coverage_median')}` p05 `{a.get('coverage_p05')}` below99 `{a.get('days_below_99')}`",
        "",
        "## Timestamp",
        "",
        f"- native `{a.get('native_timezone')}` canonical `{a.get('canonical_timezone')}` semantics `{a.get('timestamp_semantics')}`",
        f"- timestamp_pass `{a.get('timestamp_pass')}`",
        "",
        "## Bid / Ask",
        "",
        f"- both sides preserved; MID_IS_RAW_SOURCE `{a.get('MID_IS_RAW_SOURCE')}`",
        f"- value_semantics `{a.get('value_semantics')}`",
        f"- bidask_pass `{a.get('bidask_pass')}`",
        "",
        "## DriverObservation",
        "",
        f"- n `{a.get('driver_observation_n')}` family `{a.get('driver_family_id')}` direction NEUTRAL",
        "",
        "## Determinism / firewall / runtime",
        "",
        f"- determinism_pass `{a.get('determinism_pass')}`",
        f"- firewall_pass `{a.get('firewall_pass')}`",
        f"- runtime_nonimpact_pass `{a.get('runtime_nonimpact_pass')}`",
        f"- economic_data_read_n `{a.get('economic_data_read_n')}`",
        "",
        "## Safety",
        "",
        f"- FROZEN_VALIDATION_OPENED `{a.get('FROZEN_VALIDATION_OPENED')}`",
        f"- PROSPECTIVE_DATA_OPENED `{a.get('PROSPECTIVE_DATA_OPENED')}`",
        f"- V4_CHANGED `{a.get('V4_CHANGED')}` V5_CREATED `{a.get('V5_CREATED')}`",
        f"- submit/cancel/live `{a.get('submit')}/{a.get('cancel')}/{a.get('live')}`",
        "",
        f"- USDJPY_ALPHA_TESTED `{a.get('USDJPY_ALPHA_TESTED')}`",
        f"- SECTOR_RESPONSE_TESTED `{a.get('SECTOR_RESPONSE_TESTED')}`",
        f"- SYMBOL_RESPONSE_TESTED `{a.get('SYMBOL_RESPONSE_TESTED')}`",
        f"- PB1_BOUND `{a.get('PB1_BOUND')}`",
        f"- COMPLETE_STRATEGY_RUN `{a.get('COMPLETE_STRATEGY_RUN')}`",
        "",
        "Phase 2 is not started. NEXT is a precommit, not an implementation task.",
        "",
    ]
    return "\n".join(lines)


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "source_provider": evaluation.get("source_provider"),
        "source_id": evaluation.get("source_id"),
        "instrument": evaluation.get("instrument"),
        "resolution": evaluation.get("resolution"),
        "source_start": evaluation.get("source_start"),
        "source_end": evaluation.get("source_end"),
        "native_timezone": evaluation.get("native_timezone"),
        "canonical_timezone": evaluation.get("canonical_timezone"),
        "timestamp_semantics": evaluation.get("timestamp_semantics"),
        "raw_bid_n": evaluation.get("raw_bid_n"),
        "raw_ask_n": evaluation.get("raw_ask_n"),
        "paired_bar_n": evaluation.get("paired_bar_n"),
        "duplicate_n": evaluation.get("duplicate_n"),
        "conflicting_duplicate_n": evaluation.get("conflicting_duplicate_n"),
        "unexpected_gap_n": evaluation.get("unexpected_gap_n"),
        "coverage_days": evaluation.get("coverage_days"),
        "coverage_p05": evaluation.get("coverage_p05"),
        "coverage_median": evaluation.get("coverage_median"),
        "coverage_min": evaluation.get("coverage_min"),
        "days_below_99": evaluation.get("days_below_99"),
        "driver_observation_n": evaluation.get("driver_observation_n"),
        "determinism_pass": evaluation.get("determinism_pass"),
        "timestamp_pass": evaluation.get("timestamp_pass"),
        "bidask_pass": evaluation.get("bidask_pass"),
        "firewall_pass": evaluation.get("firewall_pass"),
        "runtime_nonimpact_pass": evaluation.get("runtime_nonimpact_pass"),
        "economic_data_read_n": evaluation.get("economic_data_read_n"),
        "FROZEN_VALIDATION_OPENED": evaluation.get("FROZEN_VALIDATION_OPENED"),
        "PROSPECTIVE_DATA_OPENED": evaluation.get("PROSPECTIVE_DATA_OPENED"),
        "V4_CHANGED": evaluation.get("V4_CHANGED"),
        "V5_CREATED": evaluation.get("V5_CREATED"),
        "submit": evaluation.get("submit"),
        "cancel": evaluation.get("cancel"),
        "live": evaluation.get("live"),
        "MID_IS_RAW_SOURCE": evaluation.get("MID_IS_RAW_SOURCE"),
        "value_semantics": evaluation.get("value_semantics"),
        "driver_family_id": evaluation.get("driver_family_id"),
        "USDJPY_ALPHA_TESTED": False,
        "SECTOR_RESPONSE_TESTED": False,
        "SYMBOL_RESPONSE_TESTED": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "blockers": evaluation.get("blockers"),
        "run_mode": evaluation.get("run_mode"),
        "phase1_firewall_overlay_sha256": evaluation.get("phase1_firewall_overlay_sha256"),
        "inventory_sha256": evaluation.get("inventory_sha256"),
        "normalized_schema_sha256": evaluation.get("normalized_schema_sha256"),
        "V4_MACHINE_SHA256": (evaluation.get("identity") or {}).get("V4_MACHINE_SHA256"),
        "COMPLETE_STRATEGY_SHA256": (evaluation.get("identity") or {}).get("COMPLETE_STRATEGY_SHA256"),
    }
    slim_eval = dict(evaluation)
    slim_eval.pop("raw_inventory", None)
    slim_eval.pop("coverage", None)
    identity = slim_eval.get("identity")
    if isinstance(identity, dict):
        ident = dict(identity)
        ident["source_inventory"] = "omitted_see_audit_Source_Identity"
        slim_eval["identity"] = ident
    cov = evaluation.get("coverage") or {}
    gaps = evaluation.get("gaps") or {}
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": _now(),
        "answers": answers,
        "evaluation": slim_eval,
        "coverage_summary": {k: cov.get(k) for k in ("coverage_days", "coverage_min", "coverage_median", "coverage_p05", "days_below_99", "lunch_dropped", "session")},
        "gap_summary": {
            "unexpected_gap_n": gaps.get("unexpected_gap_n"),
            "expected_closed_gap_n": gaps.get("expected_closed_gap_n"),
            "unexpected_missing_minute_n": gaps.get("unexpected_missing_minute_n"),
            "expected_closed_minute_n": gaps.get("expected_closed_minute_n"),
            "unexpected_examples": gaps.get("unexpected_examples"),
        },
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    inv = list(evaluation.get("raw_inventory") or [])
    cov_rows = list(cov.get("rows") or [])
    ident = evaluation.get("identity") or {}
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "program": PROGRAM_ID,
                    "analysis": ANALYSIS_ID,
                    "verdict": answers["VERDICT"],
                    "next": answers["NEXT"],
                    "run_mode": answers["run_mode"],
                    "created_at": _now(),
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df(
            [
                {
                    "source_id": evaluation.get("source_id"),
                    "provider": evaluation.get("source_provider"),
                    "instrument": evaluation.get("instrument"),
                    "resolution": evaluation.get("resolution"),
                    "native_timezone": evaluation.get("native_timezone"),
                    "canonical_timezone": evaluation.get("canonical_timezone"),
                    "timestamp_semantics": evaluation.get("timestamp_semantics"),
                    "bid_ask": True,
                    "date_start": evaluation.get("source_start"),
                    "date_end": evaluation.get("source_end"),
                    "raw_inventory_sha256": evaluation.get("inventory_sha256"),
                    "normalized_schema_sha256": evaluation.get("normalized_schema_sha256"),
                    "source_fingerprint": evaluation.get("source_fingerprint"),
                    "endpoint": (evaluation.get("ingest") or {}).get("endpoint_family"),
                    "V4_MACHINE_SHA256": ident.get("V4_MACHINE_SHA256"),
                    "COMPLETE_STRATEGY_SHA256": ident.get("COMPLETE_STRATEGY_SHA256"),
                }
            ]
        ).to_excel(xw, sheet_name="Source_Identity", index=False)
        _df(inv).to_excel(xw, sheet_name="Raw_Inventory", index=False)
        _df([evaluation.get("schema") or {"empty": True}]).to_excel(xw, sheet_name="Schema", index=False)
        _df(list((evaluation.get("timestamp") or {}).get("rows") or [])).to_excel(xw, sheet_name="Timestamp_Semantics", index=False)
        _df(list((evaluation.get("bidask") or {}).get("rows") or [])).to_excel(xw, sheet_name="BidAsk_Alignment", index=False)
        _df(
            [
                {
                    "duplicate_n": evaluation.get("duplicate_n"),
                    "identical_duplicate_n": evaluation.get("identical_duplicate_n"),
                    "conflicting_duplicate_n": evaluation.get("conflicting_duplicate_n"),
                    "silent_drop": False,
                }
            ]
        ).to_excel(xw, sheet_name="Duplicates", index=False)
        _df(
            list(gaps.get("unexpected_examples") or [])
            + list(gaps.get("expected_examples") or [])
            or [{"unexpected_gap_n": gaps.get("unexpected_gap_n"), "expected_closed_gap_n": gaps.get("expected_closed_gap_n")}]
        ).to_excel(xw, sheet_name="Gaps", index=False)
        _df(cov_rows).to_excel(xw, sheet_name="Japan_Session_Coverage", index=False)
        _df([evaluation.get("transport") or {"empty": True}]).to_excel(xw, sheet_name="Transport", index=False)
        _df([evaluation.get("determinism") or {"empty": True}]).to_excel(xw, sheet_name="Determinism", index=False)
        _df(list(evaluation.get("ledger_events") or []) + list((evaluation.get("firewall") or {}).get("restricted_date_rows") or [])).to_excel(
            xw, sheet_name="Firewall", index=False
        )
        _df([evaluation.get("contamination") or {"empty": True}]).to_excel(xw, sheet_name="Contamination", index=False)
        _df(list((evaluation.get("runtime_nonimpact") or {}).get("rows") or [])).to_excel(xw, sheet_name="Runtime_NonImpact", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)
    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
