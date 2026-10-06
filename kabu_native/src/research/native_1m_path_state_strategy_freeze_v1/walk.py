"""Rebuild the frozen atlas episode population via the discrimination walk. Labels are post-start only."""
from __future__ import annotations

from typing import Any

from research.native_path_state_discrimination_v1.walk import walk_episodes


def walk_frozen_population(bind: dict[str, Any]) -> dict[str, Any]:
    walked = walk_episodes(bind)
    walked["labels_attached_after_episode_start"] = True
    walked["labels_used_for_runtime_candidate_generation"] = False
    walked["future_in_boundary"] = False
    return walked
