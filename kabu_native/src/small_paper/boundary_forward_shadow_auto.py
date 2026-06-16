"""
Phase409: auto-run Phase405 corrected boundary forward shadow after paper session.

Research-only — does not change Runtime Exit / Entry / Universe / YAML / Discord production.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LOG_PREFIX = "[boundary_forward_shadow]"

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


def _ensure_structural_trades_csv(
    session_dir: Path,
    *,
    config: Any,
    poll_interval_sec: Optional[float],
) -> bool:
    trades_path = session_dir / "structural_trades.csv"
    if trades_path.is_file():
        return True
    try:
        from research.structural_observer_review import build_and_write_structural_observer_review

        build_and_write_structural_observer_review(
            session_dir,
            pilot_config=config,
            poll_interval_sec=poll_interval_sec,
            structural_exit_policy=str(getattr(config, "structural_exit_policy", "") or ""),
        )
    except Exception as exc:
        log.warning("%s structural_trades_prepare_failed: %s", LOG_PREFIX, exc)
        return trades_path.is_file()
    return trades_path.is_file()


def _resolve_status(last_run: Mapping[str, Any], error: Optional[str]) -> str:
    if error:
        return "warning"
    status = str(last_run.get("status") or "")
    if status == "logged_forward_shadow":
        return "success"
    if "skipped" in status:
        return "skipped"
    return "skipped"


def _emit_block(block: Mapping[str, Any]) -> None:
    lines = [LOG_PREFIX, f"day={block.get('day')}", f"status={block.get('status')}"]
    if block.get("day_count") is not None:
        lines.append(f"days={block.get('day_count')}")
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


def run_boundary_forward_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Optional[Path] = None,
    day: Optional[str] = None,
    config: Any = None,
    poll_interval_sec: Optional[float] = None,
    reports_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Run Phase409 boundary forward shadow for ``day``.

    Never raises — failures return status=warning and do not fail the paper session.
    """
    validation_day = infer_validation_day(day=day, output_dir=output_dir)
    block: dict[str, Any] = {
        "phase": "409-Boundary-Forward-Shadow-Auto",
        "day": validation_day,
        "status": "skipped",
        "day_count": None,
        "verdict": None,
        "baseline_total_pnl_yen_100": None,
        "shadow_total_pnl_yen_100": None,
        "delta_pnl_yen_100": None,
        "baseline_pf": None,
        "shadow_pf": None,
        "baseline_maxdd_yen_100": None,
        "shadow_maxdd_yen_100": None,
        "boundary_exit_count": None,
        "adopt_not_allowed": True,
    }
    error: Optional[str] = None

    try:
        if output_dir is not None and config is not None:
            _ensure_structural_trades_csv(
                output_dir,
                config=config,
                poll_interval_sec=poll_interval_sec,
            )

        from research.phase409_boundary_forward_shadow import BoundaryForwardShadowLogger
        from research.structural_trade_normalize import resolve_reports_dir

        reports = reports_dir or resolve_reports_dir(repo_root)
        job = BoundaryForwardShadowLogger(repo_root=repo_root, reports_dir=reports)
        result = job.run(day=validation_day)
        paths = job.write_outputs(result)

        forward_summary = result.get("forward_summary") or {}
        last_run = result.get("last_run") or {}
        block.update(
            {
                "day_count": forward_summary.get("day_count"),
                "session_count": forward_summary.get("session_count"),
                "verdict": forward_summary.get("verdict"),
                "baseline_total_pnl_yen_100": forward_summary.get("baseline_total_pnl_yen_100"),
                "shadow_total_pnl_yen_100": forward_summary.get("shadow_total_pnl_yen_100"),
                "delta_pnl_yen_100": forward_summary.get("delta_pnl_yen_100"),
                "baseline_pf": forward_summary.get("baseline_pf"),
                "shadow_pf": forward_summary.get("shadow_pf"),
                "baseline_maxdd_yen_100": forward_summary.get("baseline_maxdd_yen_100"),
                "shadow_maxdd_yen_100": forward_summary.get("shadow_maxdd_yen_100"),
                "boundary_exit_count": forward_summary.get("boundary_exit_count"),
                "post_baseline_usage_count": forward_summary.get("post_baseline_usage_count"),
                "replay_audit_pass": forward_summary.get("replay_audit_pass"),
                "adoption_review_allowed": forward_summary.get("adoption_review_allowed"),
                "adopt_not_allowed": not forward_summary.get("adoption_review_allowed"),
                "summary_path": str(paths.get("summary")),
                "last_status": last_run.get("status"),
            }
        )
        block["status"] = _resolve_status(last_run, error=None)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        block["status"] = "warning"
        block["warning"] = error
        log.warning("%s run_failed day=%s error=%s", LOG_PREFIX, validation_day, error)

    _emit_block(block)
    return block
