"""Patch Phase 0 answers.futures_1min_autosplice to false. Do not change conclusion."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.fixed_universe_historical_foundation_v1 import CASE_PARTIAL_EXTERNAL
from research.fixed_daytrade_universe_v1.isolation import PHASE0_OUT

JST = ZoneInfo("Asia/Tokyo")


def patch_phase0_autosplice(*, path: Path | None = None) -> dict[str, Any]:
    report_path = path or (PHASE0_OUT / "report.json")
    md_path = report_path.with_name("report.md")
    if not report_path.is_file():
        return {"ok": False, "reason": "phase0_report_missing", "path": str(report_path)}
    prev = json.loads(report_path.read_text(encoding="utf-8"))
    answers = dict(prev.get("answers") or {})
    decision = dict(prev.get("decision") or {})
    old = answers.get("futures_1min_autosplice")
    answers["futures_1min_autosplice"] = False
    answers["futures_autosplice_forbidden"] = True
    prev["answers"] = answers
    prev["metadata_fix_v1"] = {
        "field": "answers.futures_1min_autosplice",
        "from": old,
        "to": False,
        "canonical": False,
        "meaning": "per-contract series only; no continuous autosplice",
        "research_conclusion_unchanged": True,
        "VERDICT_unchanged": decision.get("VERDICT") == CASE_PARTIAL_EXTERNAL,
        "patched_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }
    report_path.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
    if md_path.is_file():
        md = md_path.read_text(encoding="utf-8")
        note = (
            "\nPhase0 metadata fix: `futures_1min_autosplice=false` "
            "(per-contract; autosplice forbidden). Conclusion unchanged.\n"
        )
        if "futures_1min_autosplice=false" not in md:
            md_path.write_text(md.rstrip() + "\n" + note, encoding="utf-8")
    return {
        "ok": True,
        "from": old,
        "to": False,
        "verdict_unchanged": decision.get("VERDICT") == CASE_PARTIAL_EXTERNAL,
        "path": str(report_path),
    }


assert CASE_PARTIAL_EXTERNAL == "HISTORICAL_FOUNDATION_PARTIAL_EXTERNAL_CONTEXT_V1"
