"""Publish the persistence map."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.event_time_reconfirm_persistence_causal_map_v1.isolation import OUT, write_overlap_n


def _put(wb: Workbook, name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(name)
    if not rows:
        ws.append(["none"])
        return
    keys: list[str] = []
    flat = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            item[str(key)] = value
            if str(key) not in keys:
                keys.append(str(key))
        flat.append(item)
    ws.append(keys)
    for item in flat:
        ws.append([item.get(key) for key in keys])


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    decision = report["decision"]
    lines = [
        f"# {report['analysis_id']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "R2, R3, and R4 are separate causal entry states. Entry uses only the ordinal already observed.",
        f"Earliest executable ordinal: {decision['earliest_executable_ordinal']}",
        f"Ordered persistence improvement: {decision['ordered']['ordered_persistence_improvement']}",
        "ENTRY_FROZEN: false",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Funnel": [{"map": key, **counts} for key, counts in decision["counts"].items()],
        "Economics": [{"map": key, **block["all"], "support_failure_rate": block["support_failure_rate"], "exhausted_rate": block["exhausted_rate"]} for key, block in decision["blocks"].items()],
        "Original18": [{"map": key, **block["ORIGINAL18"]} for key, block in decision["blocks"].items()],
        "Extension17": [{"map": key, **block["EXTENSION17"]} for key, block in decision["blocks"].items()],
        "Folds": [{"map": key, **fold} for key, block in decision["blocks"].items() for fold in block["folds"]],
        "Exit_Reasons": [{"map": key, "reason": reason, **pack} for key, block in decision["blocks"].items() for reason, pack in block["reasons"].items()],
        "Path": [{"map": key, "mfe_mean": block["mfe_mean"], "mfe_median": block["mfe_median"], "mae_mean": block["mae_mean"], "mae_median": block["mae_median"], "support_failure_rate": block["support_failure_rate"], "exhausted_rate": block["exhausted_rate"]} for key, block in decision["blocks"].items()],
        "Frequency": [{"map": key, **row} for key, row in decision["frequency"].items()],
        "Persistence_Order": [decision["ordered"]],
        "Executable": [{"ordinal": key, "passes": value} for key, value in decision["passed"].items()],
        "Leakage": [decision["leakage"]],
        "Verdict": [{"verdict": report["verdict"], "next": report["next"], "earliest": decision["earliest_executable_ordinal"]}],
        "Safety": [report["safety"]],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
