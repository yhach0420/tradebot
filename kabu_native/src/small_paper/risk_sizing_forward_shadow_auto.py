"""
Phase262: auto-run Risk-Aware Sizing Forward Shadow after paper session aggregation.

Research-only — does not change Runtime / Universe / Entry / YAML trading behavior.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LOG_PREFIX = "[risk_sizing_forward_shadow]"

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
    if status.startswith("logged_"):
        return "success"
    if "skipped" in status:
        return "skipped"
    return "skipped"


def _emit_block(block: Mapping[str, Any]) -> None:
    lines = [LOG_PREFIX, f"day={block.get('day')}", f"status={block.get('status')}"]
    if block.get("trade_overlap_days") is not None:
        lines.append(f"days={block.get('trade_overlap_days')}")
    if block.get("best_policy") is not None:
        lines.append(f"best_policy={block.get('best_policy')}")
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


def run_risk_sizing_forward_shadow_auto(
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
        "phase": "262-Risk-Aware-Sizing-Forward-Shadow-Auto",
        "day": validation_day,
        "status": "skipped",
        "trade_overlap_days": None,
        "best_policy": None,
        "adopt_not_allowed": None,
    }
    error: Optional[str] = None

    try:
        if output_dir is not None and config is not None:
            _ensure_structural_trades_csv(
                output_dir,
                config=config,
                poll_interval_sec=poll_interval_sec,
            )

        from research.risk_sizing_forward_shadow_logger import RiskSizingForwardShadowLogger

        reports = reports_dir or (repo_root / "kabu_native" / "results" / "reports")
        job = RiskSizingForwardShadowLogger(repo_root=repo_root, reports_dir=reports)
        summary_path = job.paths()["summary"]
        backfill = not summary_path.is_file()
        result = job.run(day=validation_day, backfill_phase261=backfill)
        job.write_outputs(result)

        forward_summary = result.get("forward_summary") or {}
        last_run = result.get("last_run") or {}
        block.update(
            {
                "trade_overlap_days": forward_summary.get("trade_overlap_day_count"),
                "best_policy": forward_summary.get("best_policy"),
                "adopt_not_allowed": forward_summary.get("adopt_not_allowed"),
                "summary_path": str(summary_path),
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
