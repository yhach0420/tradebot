"""
Phase274: auto-run Live Config Auto Transition Shadow after Phase273.

Research-only — does not change Runtime / Universe / Entry / Exit / YAML trading behavior.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import PERIOD_START

JST = ZoneInfo("Asia/Tokyo")
LOG_PREFIX = "[live_config_transition_shadow]"

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
    repo_root: Path,
    config: Any,
    poll_interval_sec: Optional[float],
) -> bool:
    trades_path = session_dir / "structural_trades.csv"
    if trades_path.is_file():
        return True
    try:
        from research.structural_trades_backfill import backfill_session

        row = backfill_session(session_dir, repo_root=repo_root)
        if str(row.get("status") or "") == "generated":
            return trades_path.is_file()
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
    if block.get("current_equity") is not None:
        lines.append(f"equity={block.get('current_equity')}")
    if block.get("active_policy_band") is not None:
        lines.append(f"band={block.get('active_policy_band')}")
    if block.get("transition_to_2000k") is not None:
        lines.append(f"transition_to_2000k={block.get('transition_to_2000k')}")
    if block.get("warning"):
        lines.append(f"warning={block.get('warning')}")
    text = "\n".join(lines)
    print(text, flush=True)
    if block.get("status") == "warning":
        log.warning(text)
    else:
        log.info(text)


def run_live_config_transition_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Optional[Path] = None,
    day: Optional[str] = None,
    config: Any = None,
    poll_interval_sec: Optional[float] = None,
    reports_dir: Optional[Path] = None,
) -> dict[str, Any]:
    validation_day = infer_validation_day(day=day, output_dir=output_dir)
    block: dict[str, Any] = {
        "phase": "274-Live-Config-Auto-Transition-Shadow-Auto",
        "day": validation_day,
        "status": "skipped",
        "current_equity": None,
        "active_policy_band": None,
        "cap_used": None,
        "stop_policy_used": None,
        "transition_to_2000k": None,
    }

    if validation_day < PERIOD_START:
        block["status"] = "skipped_before_period"
        _emit_block(block)
        return block

    error: Optional[str] = None
    try:
        if output_dir is not None and config is not None:
            _ensure_structural_trades_csv(
                output_dir,
                repo_root=repo_root,
                config=config,
                poll_interval_sec=poll_interval_sec,
            )

        from research.phase274_live_config_auto_transition_shadow import LiveConfigAutoTransitionShadow

        reports = reports_dir or (repo_root / "kabu_native" / "results" / "reports")
        job = LiveConfigAutoTransitionShadow(repo_root=repo_root, reports_dir=reports)
        result = job.run(day=validation_day)
        paths = job.write_outputs(result)

        ts = result.get("transition_summary") or {}
        adoption = ts.get("adoption_verdict") or {}
        block.update(
            {
                "current_equity": ts.get("current_equity"),
                "active_policy_band": ts.get("active_policy_band"),
                "cap_used": ts.get("cap_used"),
                "stop_policy_used": ts.get("stop_policy_used"),
                "transition_day_to_2000k": ts.get("transition_day_to_2000k"),
                "transition_to_2000k": ts.get("transition_to_2000k"),
                "adoption_verdict": adoption.get("adoption_verdict"),
                "summary_path": str(paths.get("summary")),
                "last_status": (result.get("last_run") or {}).get("status"),
            }
        )
        block["status"] = _resolve_status(result.get("last_run") or {}, error=None)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        block["status"] = "warning"
        block["warning"] = error
        log.warning("%s run_failed day=%s error=%s", LOG_PREFIX, validation_day, error)

    _emit_block(block)
    return block
