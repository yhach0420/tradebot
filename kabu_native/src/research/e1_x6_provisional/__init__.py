"""E1_X6 research-only builders (Runtime / MAINLINE untouched)."""

from __future__ import annotations

from research.e1_x6_provisional.constants import FINAL_BANNER, PROVISIONAL_BANNER

__all__ = [
    "PROVISIONAL_BANNER",
    "FINAL_BANNER",
    "run_provisional_pipeline",
    "run_final_9day_pipeline",
]


def run_provisional_pipeline(**kwargs):
    from research.e1_x6_provisional.pipeline import run_provisional_pipeline as _run

    return _run(**kwargs)


def run_final_9day_pipeline(**kwargs):
    from research.e1_x6_provisional.pipeline import run_final_9day_pipeline as _run

    return _run(**kwargs)
