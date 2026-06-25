"""
Phase500: auto-run post-entry forward shadow review after paper session.

Research-only — does not change Runtime / Entry / Exit / Order / YAML / Discord production.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LOG_PREFIX = "[post_entry_forward_shadow]"

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


def _emit_block(block: Mapping[str, Any]) -> None:
    lines = [LOG_PREFIX, f"day={block.get('day')}", f"status={block.get('status')}"]
    if block.get("forward_days_collected") is not None:
        lines.append(f"days={block.get('forward_days_collected')}")
    if block.get("verdict") is not None:
        lines.append(f"verdict={block.get('verdict')}")
    if block.get("warning"):
        lines.append(f"warning={block.get('warning')}")
    text = "\n".join(lines)
    print(text, flush=True)
    if block.get("status") == "warning":
        log.warning(text)
    else:
        log.info(text)


def run_post_entry_forward_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Optional[Path] = None,
    day: Optional[str] = None,
    reports_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Upsert session CSV into cumulative forward log and refresh Phase500 summary."""
    validation_day = infer_validation_day(day=day, output_dir=output_dir)
    block: dict[str, Any] = {
        "phase": "500-Post-Entry-Forward-Shadow-Auto",
        "day": validation_day,
        "status": "skipped",
        "forward_days_collected": None,
        "verdict": "forward_shadow_started",
        "score_ge3_count": None,
        "score_ge3_pnl": None,
        "score_ge4_count": None,
        "score_ge4_pnl": None,
        "adopt_not_allowed": True,
    }

    try:
        from research.phase500_post_entry_shadow_review import PostEntryShadowReview
        from research.structural_trade_normalize import resolve_reports_dir

        session_csv = None
        if output_dir is not None:
            candidate = output_dir / "small_paper_shadow_post_entry.csv"
            if candidate.is_file():
                session_csv = candidate

        reports = reports_dir or resolve_reports_dir(repo_root)
        job = PostEntryShadowReview(repo_root=repo_root, reports_dir=reports)
        result = job.run(day=validation_day, session_csv=session_csv)
        paths = job.write_outputs(result)

        mandatory = result.get("mandatory_answers") or {}
        block.update(
            {
                "status": str(result.get("status") or "success"),
                "forward_days_collected": result.get("forward_days_collected"),
                "verdict": result.get("verdict"),
                "score_ge3_count": mandatory.get("1_score_ge3_count"),
                "score_ge3_pnl": mandatory.get("2_score_ge3_pnl"),
                "score_ge4_count": mandatory.get("3_score_ge4_count"),
                "score_ge4_pnl": mandatory.get("4_score_ge4_pnl"),
                "data_source": result.get("data_source"),
                "summary_path": str(paths.get("summary")),
            }
        )
    except Exception as exc:
        block["status"] = "warning"
        block["warning"] = f"{type(exc).__name__}: {exc}"
        log.warning("%s run_failed day=%s error=%s", LOG_PREFIX, validation_day, exc)

    _emit_block(block)
    return block
