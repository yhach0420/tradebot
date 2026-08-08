"""Date registry — classify before raw open."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ALPHA_RESERVED_DAYS,
    DESIGN_DAYS,
    STATUS_ALREADY_USED,
    STATUS_ALPHA_RESERVED,
    STATUS_RISK_ONLY,
    STATUS_UNCLASSIFIED,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]


def _undash(d: str) -> str:
    return d.replace("-", "")


def discover_capture_dates() -> list[str]:
    """YYYYMMDD for all push_jsonl and market_capture dirs from 20260721 onward."""
    out = set()
    push = NATIVE / "data" / "push_jsonl"
    if push.exists():
        for d in push.iterdir():
            if d.is_dir() and d.name.startswith("2026-"):
                u = _undash(d.name)
                if u >= "20260721":
                    out.add(u)
    mc = NATIVE / "data" / "market_capture"
    if mc.exists():
        for d in mc.iterdir():
            if d.is_dir() and d.name.isdigit() and len(d.name) == 8 and d.name >= "20260721":
                out.add(d.name)
    return sorted(out)


def build_date_registry(
    *,
    newly_risk_only: list[str] | None = None,
    assigned_by: str = "E1_X12",
) -> dict[str, Any]:
    """Classify every known capture date before any raw open."""
    newly_risk_only = newly_risk_only or []
    now = datetime.now(JST).isoformat()
    rows = []
    for day in discover_capture_dates():
        if day in DESIGN_DAYS:
            status = STATUS_ALREADY_USED
            reason = "design-period risk/execution panel already used in E1_X7–X11"
            raw_ok, alpha_ok, risk_ok = True, False, True
        elif day in ALPHA_RESERVED_DAYS:
            status = STATUS_ALPHA_RESERVED
            reason = "existing alpha Prospective reservation preserved; do not open"
            raw_ok, alpha_ok, risk_ok = False, True, False
        elif day in newly_risk_only:
            status = STATUS_RISK_ONLY
            reason = "precommitted RISK_INFRASTRUCTURE_ONLY before market open; alpha_use forbidden"
            raw_ok, alpha_ok, risk_ok = True, False, True
        else:
            status = STATUS_UNCLASSIFIED
            reason = "no explicit classification; do not open until precommitted"
            raw_ok, alpha_ok, risk_ok = False, False, False
        rows.append({
            "date": day,
            "status": status,
            "classified_at_jst": now,
            "classification_reason": reason,
            "raw_open_allowed": raw_ok,
            "alpha_use_allowed": alpha_ok,
            "risk_use_allowed": risk_ok,
            "assigned_by": assigned_by,
        })
    # integrity: RISK_ONLY never alpha; ALPHA_RESERVED never risk/raw
    for r in rows:
        if r["status"] == STATUS_RISK_ONLY:
            assert r["alpha_use_allowed"] is False
            assert r["risk_use_allowed"] is True
        if r["status"] == STATUS_ALPHA_RESERVED:
            assert r["risk_use_allowed"] is False
            assert r["raw_open_allowed"] is False
    body_rows_for_sha = [
        {k: r[k] for k in (
            "date", "status", "classification_reason",
            "raw_open_allowed", "alpha_use_allowed", "risk_use_allowed", "assigned_by",
        )}
        for r in rows
    ]
    registry_sha = sha256_obj(body_rows_for_sha)
    for r in rows:
        r["registry_sha256"] = registry_sha
    return {
        "registry_sha256": registry_sha,
        "classified_at_jst": now,
        "rows": rows,
        "n": len(rows),
        "by_date": {r["date"]: r for r in rows},
    }


def assert_raw_open_allowed(registry: dict[str, Any], day: str) -> None:
    row = registry["by_date"].get(day)
    if row is None or not row.get("raw_open_allowed"):
        raise PermissionError(f"raw open forbidden for {day}: {row}")
