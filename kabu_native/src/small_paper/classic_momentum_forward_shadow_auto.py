"""
Phase513: auto-run classic momentum forward shadow after paper session.

Research-only — no Runtime adoption, no notifications.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LOG_PREFIX = "[classic_momentum_forward_shadow]"

log = logging.getLogger(__name__)


def infer_validation_day(
    *,
    day: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> str:
    if day:
        return str(day)
    if output_dir is not None:
        for part in reversed(output_dir.resolve().parts):
            if len(part) == 8 and part.isdigit():
                return part
    return datetime.now(JST).strftime("%Y%m%d")


def run_classic_momentum_forward_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Optional[Path] = None,
    day: Optional[str] = None,
) -> dict[str, Any]:
    validation_day = infer_validation_day(day=day, output_dir=output_dir)
    block: dict[str, Any] = {
        "phase": "513-Classic-Momentum-Forward-Shadow-Auto",
        "day": validation_day,
        "status": "skipped",
        "adopt_not_allowed": True,
    }
    session_csv = None
    if output_dir is not None:
        candidate = output_dir / "small_paper_shadow_classic_momentum.csv"
        if candidate.is_file():
            session_csv = candidate
    if session_csv is None:
        block["warning"] = "no_session_csv"
        log.info("%s day=%s status=skipped (no session csv)", LOG_PREFIX, validation_day)
        return block
    block["status"] = "ok"
    block["session_csv"] = str(session_csv)
    log.info("%s day=%s status=ok path=%s", LOG_PREFIX, validation_day, session_csv)
    return block
