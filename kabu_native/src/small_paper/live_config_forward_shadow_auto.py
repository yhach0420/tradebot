"""
Phase273: auto-run Live Configuration Forward Shadow after paper session aggregation.

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
LOG_PREFIX = "[live_config_forward_shadow]"

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
    if block.get("day_count") is not None:
        lines.append(f"days={block.get('day_count')}")
    if block.get("current_recommendation") is not None:
        lines.append(f"current={block.get('current_recommendation')}")
    if block.get("adopt_not_allowed") is not None:
        lines.append(f"adopt_not_allowed={block.get('adopt_not_allowed')}")
    if block.get("warning"):
        lines.append(f"warning={block.get('warning')}")
    text = "\n".join(lines)
    print(text, flush=True)
    if block.get("status") == "warning":
        log.warning(text)
    else:
        log.info(text)


def run_live_config_forward_shadow_auto(
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
        "phase": "273-Forward-Live-Configuration-Shadow-Auto",
        "day": validation_day,
        "status": "skipped",
        "day_count": None,
        "current_recommendation": None,
        "adopt_not_allowed": None,
        "candidate_1500k": None,
        "candidate_2000k": None,
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

        from research.phase273_live_config_forward_shadow_logger import LiveConfigForwardShadowLogger

        reports = reports_dir or (repo_root / "kabu_native" / "results" / "reports")
        job = LiveConfigForwardShadowLogger(repo_root=repo_root, reports_dir=reports)
        result = job.run(day=validation_day)
        paths = job.write_outputs(result)

        forward_summary = result.get("forward_summary") or {}
        last_run = result.get("last_run") or {}
        candidates = {
            str(c.get("candidate_key") or ""): c for c in (forward_summary.get("candidates") or [])
        }
        c1500 = candidates.get("live_start_candidate_1500k") or {}
        c2000 = candidates.get("scale_candidate_2000k_plus") or {}

        block.update(
            {
                "day_count": forward_summary.get("day_count"),
                "current_recommendation": forward_summary.get("current_recommendation"),
                "adopt_not_allowed": forward_summary.get("adopt_not_allowed"),
                "candidate_1500k": {
                    "final_equity": c1500.get("final_equity"),
                    "max_drawdown_pct": c1500.get("max_drawdown_pct"),
                    "verdict": c1500.get("verdict"),
                },
                "candidate_2000k": {
                    "final_equity": c2000.get("final_equity"),
                    "max_drawdown_pct": c2000.get("max_drawdown_pct"),
                    "verdict": c2000.get("verdict"),
                },
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
