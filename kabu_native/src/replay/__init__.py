"""kabu_native replay orchestration."""

from typing import Any

__all__ = [
    "ReplayRunConfig",
    "ReplayRunResult",
    "aggregate_summary",
    "daily_summaries",
    "run_replay_batch",
    "symbol_summaries",
    "trades_to_rows",
]


def __getattr__(name: str) -> Any:
    if name in ("ReplayRunConfig", "ReplayRunResult", "run_replay_batch"):
        from replay.runner import ReplayRunConfig, ReplayRunResult, run_replay_batch

        return {
            "ReplayRunConfig": ReplayRunConfig,
            "ReplayRunResult": ReplayRunResult,
            "run_replay_batch": run_replay_batch,
        }[name]
    if name in ("aggregate_summary", "daily_summaries", "symbol_summaries", "trades_to_rows"):
        from replay.metrics import aggregate_summary, daily_summaries, symbol_summaries, trades_to_rows

        return {
            "aggregate_summary": aggregate_summary,
            "daily_summaries": daily_summaries,
            "symbol_summaries": symbol_summaries,
            "trades_to_rows": trades_to_rows,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
