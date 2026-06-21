"""kabu_native research tools (logic validation before paper_trade)."""

from typing import Any

__all__ = ["LogicLabConfig", "PROFILE_NAMES", "run_logic_lab"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from research.logic_lab import LogicLabConfig, PROFILE_NAMES, run_logic_lab

        return {
            "LogicLabConfig": LogicLabConfig,
            "PROFILE_NAMES": PROFILE_NAMES,
            "run_logic_lab": run_logic_lab,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
