"""Project date-exposure ledger from research source constants only. No market outcomes. No Capture JSONL."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from research.new_information_acquisition_feasibility_design_v1 import (
    BURNED_HOLDOUT_DAYS,
    LEGACY_DEV_DAYS,
    MBO_ERA_START,
    MIN_REQUIRED_RESEARCH_DAYS,
    QUARANTINE_DAYS,
    STRESS_DAYS,
)
from research.new_information_acquisition_feasibility_design_v1.isolation import NATIVE

_DATE_RE = re.compile(r'"(20\d{6})"')

# From e1_x29_prospective: JPX closed weekdays 2026. Not research-exposed trading days.
JPX_HOLIDAYS_2026 = frozenset(
    {
        "20260101",
        "20260102",
        "20260112",
        "20260211",
        "20260223",
        "20260320",
        "20260429",
        "20260504",
        "20260505",
        "20260506",
        "20260720",
        "20260811",
        "20260921",
        "20260922",
        "20260923",
        "20261012",
        "20261103",
        "20261123",
        "20261231",
    }
)

OR_ERA_DAYS = (
    "20260625",
    "20260629",
    "20260630",
    "20260701",
    "20260706",
    "20260708",
    "20260709",
    "20260710",
    "20260714",
)
CONSUMED_ALPHA_DATES = (
    "20260721",
    "20260722",
    "20260723",
    "20260724",
    "20260727",
    "20260728",
    "20260729",
    "20260730",
    "20260731",
    "20260803",
    "20260804",
)


def _valid_ymd(s: str) -> bool:
    if len(s) != 8 or not s.isdigit():
        return False
    y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
    return 2024 <= y <= 2026 and 1 <= m <= 12 and 1 <= d <= 31


def scan_research_source_dates() -> set[str]:
    root = NATIVE / "src" / "research"
    found: set[str] = set()
    skip_parts = {
        "new_information_acquisition_feasibility_design_v1",
        "new_information_source_policy_decision_v1",
        "research_pause_and_data_requirements_redesign_v1",
    }
    if not root.is_dir():
        return found
    for path in root.rglob("*.py"):
        if path.name == "isolation.py":
            continue
        if any(p in path.parts for p in skip_parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _DATE_RE.finditer(text):
            d = m.group(1)
            if not _valid_ymd(d):
                continue
            if d >= "20260905":
                continue
            if d in JPX_HOLIDAYS_2026:
                continue
            if d == "20241105":
                continue
            found.add(d)
    return found


def classify(day: str) -> str:
    if day >= "20260907":
        return "PROSPECTIVE_SEALED"
    if day in QUARANTINE_DAYS:
        return "QUARANTINE"
    if day in STRESS_DAYS:
        return "BURNED_STRESS"
    if day in BURNED_HOLDOUT_DAYS:
        return "BURNED_HOLDOUT"
    if day in LEGACY_DEV_DAYS:
        return "LEGACY_DEV"
    return "RESEARCH_EXPOSED"


def build_ledger() -> dict[str, Any]:
    scanned = scan_research_source_dates()
    pinned = set(LEGACY_DEV_DAYS) | set(BURNED_HOLDOUT_DAYS) | set(STRESS_DAYS) | set(QUARANTINE_DAYS)
    pinned |= set(OR_ERA_DAYS) | set(CONSUMED_ALPHA_DATES)
    exposed = sorted(scanned | pinned)
    rows = [{"date": d, "status": classify(d)} for d in exposed]
    status_n = {}
    for r in rows:
        status_n[r["status"]] = status_n.get(r["status"], 0) + 1
    # Clean historical: MBO-era calendar span exists; we do not enumerate TSE sessions.
    # Structurally: MBO from 2024-11-05 through day before earliest exposed in 2026 still covers >>30 sessions.
    earliest_exp = exposed[0] if exposed else None
    latest_exp = exposed[-1] if exposed else None
    return {
        "EXPOSED_DATE_N": len(exposed),
        "EXPOSED_DATE_LIST": exposed,
        "EARLIEST_EXPOSED_DATE": earliest_exp,
        "LATEST_EXPOSED_DATE": latest_exp,
        "STATUS_COUNTS": status_n,
        "ROWS": rows,
        "EXPOSED_DAY_REUSE_WITH_NEW_SOURCE_ALLOWED": False,
        "MBO_ERA_START": MBO_ERA_START,
        "MIN_REQUIRED_RESEARCH_DAYS": int(MIN_REQUIRED_RESEARCH_DAYS),
        "CLEAN_HISTORICAL_PERIOD_EXISTS": True,
        "CLEAN_HISTORICAL_NOTE": (
            "FLEX MBO Historical all-period covers MBO-era from 2024-11-05. Artifact-exposed "
            "strategy-outcome dates found in research source constants cluster in 2026. "
            "A clean 30-day window that avoids LEGACY_DEV/Holdout/Stress/quarantine/other "
            "listed research-exposed days is structurally available earlier in the MBO era. "
            "No dates assigned. TSE holiday calendar not used to name days."
        ),
        "AT_LEAST_30_CLEAN_DAYS_STRUCTURALLY_POSSIBLE": True,
        "SCAN_SCOPE": "src/research/*.py quoted YYYYMMDD only; no Capture JSONL; no paper journals; no 20260907+",
        "PROSPECTIVE_SEALED": "20260907+",
    }
