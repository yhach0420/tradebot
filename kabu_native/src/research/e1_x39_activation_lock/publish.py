"""Publish X39 artifacts. Activation manifest only on full PASS."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def write_md(path: Path, report: dict[str, Any]) -> None:
    uni = report.get("universe") or {}
    warm = report.get("warmup") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- V1R: `{report.get('v1r_sha')}`",
        f"- model: `{report.get('model_artifact_sha')}`",
        f"- precommit: `{report.get('precommit_sha')}`",
        f"- universe binding pass: `{uni.get('pass')}`",
        f"- warmup pass: `{warm.get('pass')}`",
        f"- 09:05 parity: `{(warm.get('parity_0905') or {}).get('pass')}`",
        f"- 12:40 parity: `{(warm.get('parity_1240') or {}).get('pass')}`",
        f"- recovery pass: `{(report.get('recovery') or {}).get('pass')}`",
        f"- activation manifest: `{report.get('activation_manifest')}`",
        f"- prospective observer: `{report.get('prospective_observer')}`",
        f"- 20260810: `{report.get('opened_20260810')}`",
        f"- strategy mutation: `{report.get('strategy_mutation')}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet(name[:31])
        if first:
            ws.title = name[:31]
            first = False
        if not rows:
            ws.append(["empty"])
            continue
        hdr = list(rows[0].keys())
        ws.append(hdr)
        for r in rows:
            ws.append([
                json.dumps(r.get(h), default=str)[:32000]
                if isinstance(r.get(h), (dict, list)) else r.get(h)
                for h in hdr
            ])
    wb.save(path)


def maybe_write_activation_manifest(out: Path, report: dict[str, Any]) -> dict[str, Any] | None:
    if report.get("verdict") != "E1_X39_PAPER_PRIMARY_ACTIVATION_READY":
        return None
    body = {
        "manifest_id": "V1R_PAPER_PRIMARY_ACTIVATION_V1",
        "kind": "operational_activation_manifest_not_strategy",
        "v1r_sha": report["v1r_sha"],
        "model_artifact_sha": report["model_artifact_sha"],
        "precommit_sha": report["precommit_sha"],
        "universe_mapping_contract": report.get("universe", {}).get("prospective_mapping"),
        "universe_effective_time_semantics": report.get("universe", {}).get("rule_1000"),
        "warmup_session_semantics": (report.get("warmup") or {}).get("warmup_semantics"),
        "recovery_semantics": report.get("recovery"),
        "roles": {
            "primary": "PASSIVE_FIXED600_FULL_STRATEGY_V1R",
            "pbv2": "SHADOW_ONLY",
            "capital_1m": "SHADOW_ONLY_DIAGNOSTIC",
        },
        "notification_prefixes": (report.get("notification") or {}).get("prefixes"),
        "startup_order": report.get("startup_sequence"),
        "paper_only": True,
        "submit_cancel_live": "0/0/0",
        "does_not_overwrite_v1r_or_precommit": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    (out / "V1R_PAPER_PRIMARY_ACTIVATION_V1.json").write_text(
        json.dumps(body, indent=2, default=str), encoding="utf-8"
    )
    return {"written": True, "sha256": body["sha256"]}


def publish(out: Path, report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_md(out / "report.md", report)
    write_xlsx(out / "audit.xlsx", sheets)
