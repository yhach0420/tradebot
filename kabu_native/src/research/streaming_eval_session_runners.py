"""
Top-level session replay runners for Phase337+ streaming evaluations.

Worker-safe: no lambdas; importable from child processes on Windows.
"""

from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class SessionEvalResult:
    session_meta: dict[str, Any]
    session_index: int = 0
    trade_rows: list[dict[str, Any]] = field(default_factory=list)
    push_rows: int = 0
    runtime_sec: float = 0.0
    vwap_coverage_pct: Optional[float] = None
    error: str = ""
    peak_memory_mb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionEvalResult:
        return cls(
            session_meta=dict(data.get("session_meta") or {}),
            session_index=int(data.get("session_index") or 0),
            trade_rows=list(data.get("trade_rows") or []),
            push_rows=int(data.get("push_rows") or 0),
            runtime_sec=float(data.get("runtime_sec") or 0.0),
            vwap_coverage_pct=data.get("vwap_coverage_pct"),
            error=str(data.get("error") or ""),
            peak_memory_mb=float(data.get("peak_memory_mb") or 0.0),
        )


def bootstrap_sys_path(repo_root: Path) -> None:
    for p in (repo_root / "kabu_native" / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _note_peak_memory_mb() -> float:
    from research.phase337_exit_candidate_evaluation import _memory_mb

    return _memory_mb()


def _silence_discord_posts() -> Any:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    orig = SmallPaperDiscordNotifier._post

    def _silent(self, **kwargs: Any) -> bool:
        return False

    SmallPaperDiscordNotifier._post = _silent  # type: ignore[method-assign]
    return orig


def _restore_discord_posts(orig: Any) -> None:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    SmallPaperDiscordNotifier._post = orig  # type: ignore[method-assign]


def _vwap_coverage_from_pack(pack: Any) -> Optional[float]:
    ticks = int(getattr(pack, "vwap_eval_ticks", 0) or 0)
    missing = int(getattr(pack, "vwap_missing_ticks", 0) or 0)
    if ticks <= 0:
        return None
    return round(100.0 * (ticks - missing) / ticks, 2)


def run_streaming_eval_session(
    *,
    mode: str,
    session_meta: Mapping[str, Any],
    repo_root: Path,
    config_path: Path,
    max_push_rows: Optional[int],
    streaming: bool,
    extra: Optional[Mapping[str, Any]] = None,
    session_index: int = 0,
) -> SessionEvalResult:
    """Run one push-replay session for the given research mode."""
    from dataclasses import replace

    from small_paper.config import load_pilot_config, resolve_output_dir
    from small_paper.pilot_runner import run_push_replay_dry_run

    extra = dict(extra or {})
    meta = dict(session_meta)
    push_dir = Path(str(meta.get("push_dir") or ""))
    if not push_dir.is_dir():
        return SessionEvalResult(
            session_meta=meta,
            session_index=session_index,
            error=f"push_dir_missing:{push_dir}",
        )

    day_key = str(meta.get("day_key") or push_dir.name.replace("-", ""))
    cfg = load_pilot_config(config_path)
    cfg = replace(cfg, discord_enabled=False, discord_observer_only=True)
    stamp = datetime.now(JST).strftime("%H%M%S")
    phase_tag = mode.split("_", 1)[0] if "_" in mode else mode
    out_dir = resolve_output_dir(cfg, repo_root=repo_root, day_key=day_key) / f"{phase_tag}_{stamp}"

    replay_kwargs: dict[str, Any] = {
        "config": cfg,
        "push_dir": push_dir,
        "output_dir": out_dir,
        "repo_root": repo_root,
        "poll_interval_sec": 0.0,
        "replay_speed_sec": 0.0,
        "max_push_rows": max_push_rows,
        "enable_discord": False,
        "write_board_shadow_reports": False,
        "streaming_push_replay": streaming,
    }

    if mode == "phase337_exit_candidate":
        replay_kwargs["enable_exit_candidate_shadow"] = True
    elif mode == "phase338_exit_candidate":
        from small_paper.exit_candidate_shadow import PHASE338_CANDIDATE_IDS

        replay_kwargs["enable_exit_candidate_shadow"] = True
        replay_kwargs["exit_candidate_ids"] = list(
            extra.get("exit_candidate_ids") or PHASE338_CANDIDATE_IDS
        )
    elif mode == "phase339_vwap_tuning":
        from small_paper.vwap_assisted_loss_tuning import default_phase339_variants

        replay_kwargs["enable_vwap_tuning_shadow"] = True
        replay_kwargs["vwap_tuning_variants"] = list(default_phase339_variants())
    elif mode == "phase340_vwap_finetune":
        from small_paper.vwap_assisted_loss_tuning import default_phase340_variants

        replay_kwargs["enable_vwap_tuning_shadow"] = True
        replay_kwargs["vwap_tuning_variants"] = list(default_phase340_variants())
    elif mode == "phase341_vwap_robustness":
        from small_paper.vwap_assisted_loss_tuning import phase341_vwap_dev_0p4pct_variant

        replay_kwargs["enable_vwap_tuning_shadow"] = True
        replay_kwargs["vwap_tuning_variants"] = [phase341_vwap_dev_0p4pct_variant()]
    elif mode == "phase342_board_failure":
        replay_kwargs["enable_board_failure_shadow"] = True
    elif mode == "phase343_board_failure_mfe":
        replay_kwargs["enable_board_failure_tuning_shadow"] = True
    elif mode == "phase344_board_failure_mfe0p2_confirm5":
        from small_paper.board_failure_exit_tuning import phase344_mfe_lt_0p2_confirm5_variant

        replay_kwargs["enable_board_failure_tuning_shadow"] = True
        replay_kwargs["board_failure_tuning_variants"] = [phase344_mfe_lt_0p2_confirm5_variant()]
    elif mode == "phase345_board_failure_forensic":
        replay_kwargs["enable_board_failure_forensic_shadow"] = True
    elif mode == "phase346_board_failure_false_positive_guard":
        from small_paper.board_failure_false_positive_guard import default_phase346_variants

        replay_kwargs["enable_board_failure_guard_shadow"] = True
        replay_kwargs["board_failure_guard_variants"] = list(default_phase346_variants())
    elif mode == "phase347_board_failure_cooldown_finetune":
        from small_paper.board_failure_false_positive_guard import default_phase347_variants

        replay_kwargs["enable_board_failure_guard_shadow"] = True
        replay_kwargs["board_failure_guard_variants"] = list(default_phase347_variants())
    elif mode == "phase356_exit_rebaseline":
        replay_kwargs["enable_phase356_exit_rebaseline_shadow"] = True
        replay_kwargs["poll_interval_sec"] = float(extra.get("poll_interval_sec") or 0.0)
    else:
        return SessionEvalResult(
            session_meta=meta,
            session_index=session_index,
            error=f"unsupported_mode:{mode}",
        )

    orig_post = _silence_discord_posts()
    peak_mb = 0.0
    t0 = time.monotonic()
    try:
        result = run_push_replay_dry_run(**replay_kwargs)
        peak_mb = max(peak_mb, _note_peak_memory_mb())
    except Exception as exc:
        return SessionEvalResult(
            session_meta=meta,
            session_index=session_index,
            runtime_sec=time.monotonic() - t0,
            error=str(exc),
            peak_memory_mb=peak_mb,
        )
    finally:
        _restore_discord_posts(orig_post)

    runtime = time.monotonic() - t0
    pack = result.exit_candidate_shadow
    if pack is None:
        return SessionEvalResult(
            session_meta=meta,
            session_index=session_index,
            runtime_sec=runtime,
            error=f"no_shadow_pack:{mode}",
            peak_memory_mb=peak_mb,
        )

    trades = _export_trade_rows(mode, pack, session_meta=meta)
    push_rows = int(result.summary.get("push_rows") or result.summary.get("push_messages") or 0)
    vwap_cov = _vwap_coverage_from_pack(pack) if mode.startswith("phase339") or mode.startswith("phase340") or mode.startswith("phase341") else None
    peak_mb = max(peak_mb, _note_peak_memory_mb())
    return SessionEvalResult(
        session_meta=meta,
        session_index=session_index,
        trade_rows=trades,
        push_rows=push_rows,
        runtime_sec=runtime,
        vwap_coverage_pct=vwap_cov,
        peak_memory_mb=peak_mb,
    )


def _export_trade_rows(
    mode: str,
    pack: Any,
    *,
    session_meta: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    if mode == "phase337_exit_candidate":
        from small_paper.exit_candidate_shadow import export_exit_candidate_trade_rows

        return export_exit_candidate_trade_rows(pack)
    if mode == "phase338_exit_candidate":
        from small_paper.exit_candidate_shadow import export_exit_candidate_trade_rows

        return export_exit_candidate_trade_rows(pack)
    if mode in ("phase339_vwap_tuning", "phase340_vwap_finetune", "phase341_vwap_robustness"):
        from small_paper.vwap_assisted_loss_tuning import export_vwap_tuning_trade_rows

        return export_vwap_tuning_trade_rows(pack)
    if mode == "phase342_board_failure":
        from small_paper.board_failure_exit_shadow import export_board_failure_trade_rows

        return export_board_failure_trade_rows(pack)
    if mode == "phase345_board_failure_forensic":
        from small_paper.board_failure_forensic_pack import BoardFailureForensicPack

        meta = dict(session_meta or {})
        if isinstance(pack, BoardFailureForensicPack):
            return pack.export_forensic_rows(
                session_id=str(meta.get("session_id") or ""),
                day_key=str(meta.get("day_key") or ""),
            )
        return []
    if mode in ("phase343_board_failure_mfe", "phase344_board_failure_mfe0p2_confirm5"):
        from small_paper.board_failure_exit_tuning import export_board_failure_tuning_trade_rows

        return export_board_failure_tuning_trade_rows(pack)
    if mode in (
        "phase346_board_failure_false_positive_guard",
        "phase347_board_failure_cooldown_finetune",
    ):
        from small_paper.board_failure_false_positive_guard import export_board_failure_guard_trade_rows

        return export_board_failure_guard_trade_rows(pack)
    if mode == "phase356_exit_rebaseline":
        from small_paper.phase356_exit_rebaseline_pack import export_phase356_exit_rebaseline_trade_rows

        return export_phase356_exit_rebaseline_trade_rows(pack)
    return []


def execute_streaming_eval_worker_job(job: Mapping[str, Any]) -> dict[str, Any]:
    """
    ProcessPoolExecutor entry point.

    Writes full SessionEvalResult JSON to job['output_path'].
    Returns a small status dict (picklable).
    """
    import json

    repo_root = Path(str(job.get("repo_root") or ""))
    bootstrap_sys_path(repo_root)
    output_path = Path(str(job.get("output_path") or ""))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = run_streaming_eval_session(
            mode=str(job.get("mode") or ""),
            session_meta=dict(job.get("session_meta") or {}),
            repo_root=repo_root,
            config_path=Path(str(job.get("config_path") or "")),
            max_push_rows=job.get("max_push_rows"),
            streaming=bool(job.get("streaming", True)),
            extra=dict(job.get("extra") or {}),
            session_index=int(job.get("session_index") or 0),
        )
    except Exception as exc:
        result = SessionEvalResult(
            session_meta=dict(job.get("session_meta") or {}),
            session_index=int(job.get("session_index") or 0),
            error=f"worker_exception:{exc}",
        )

    output_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "session_index": result.session_index,
        "output_path": str(output_path),
        "error": result.error,
        "runtime_sec": result.runtime_sec,
        "peak_memory_mb": result.peak_memory_mb,
        "trade_row_count": len(result.trade_rows),
    }
