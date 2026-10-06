"""Frozen atlas episode-start generator. Labels never participate in onset construction."""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any

from research.native_1m_path_state_strategy_freeze_v1 import ENTRY_EVENT_SOURCE_ID, EPISODE_GAP_MIN, FUTURE_IN_BOUNDARY
from research.one_minute_native_playbook_discovery_v1 import episodes as episodes_mod
from research.one_minute_native_playbook_discovery_v1 import panel as panel_mod
from research.one_minute_native_playbook_discovery_v1.episodes import cluster_symbol_day
from research.one_minute_native_playbook_discovery_v1.panel import process_day
from research.native_path_state_discrimination_v1 import walk as walk_mod

GENERATOR_RELPATHS = (
    "src/research/one_minute_native_playbook_discovery_v1/panel.py",
    "src/research/one_minute_native_playbook_discovery_v1/episodes.py",
    "src/research/one_minute_native_playbook_discovery_v1/states.py",
    "src/research/one_minute_native_playbook_discovery_v1/__init__.py",
    "src/research/native_path_state_discrimination_v1/walk.py",
    "src/research/fixed_universe_historical_foundation_v1/technical.py",
    "src/research/cause_first_mechanism_discovery_v1/clock.py",
)

LEAK_TOKENS = (
    "mfe",
    "mae",
    "path_type",
    "path_class",
    "future_path",
    "favor_first",
    "x0_bps",
    "x1_bps",
    "ret_p5",
    "outcome",
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def episode_generator_sha256(*, native: Path) -> str:
    h = hashlib.sha256()
    h.update(ENTRY_EVENT_SOURCE_ID.encode("utf-8"))
    h.update(str(EPISODE_GAP_MIN).encode("utf-8"))
    h.update(str(FUTURE_IN_BOUNDARY).encode("utf-8"))
    for rel in GENERATOR_RELPATHS:
        p = native / rel
        h.update(rel.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _src_has_future(src: str) -> list[str]:
    low = src.lower()
    return [tok for tok in LEAK_TOKENS if tok in low]


def causality_static() -> dict[str, Any]:
    cluster_src = inspect.getsource(cluster_symbol_day)
    process_src = inspect.getsource(process_day)
    cluster_hits = _src_has_future(cluster_src)
    process_hits = _src_has_future(process_src)
    walk_src = inspect.getsource(walk_mod.walk_episodes)
    # Labels are attached AFTER episode start. Construction uses cluster[-1] trigger only.
    start_uses_label = any(
        tok in cluster_src.lower() for tok in ("path_type", "mfe", "mae", "path_class", "favor_first")
    )
    return {
        "cluster_future_tokens": cluster_hits,
        "process_day_future_tokens": process_hits,
        "cluster_uses_outcome_labels": start_uses_label,
        "trigger_is_last_event_in_cluster": "cluster[-1]" in inspect.getsource(walk_mod.walk_episodes) or "trigger = cluster[-1]" in walk_src,
        "future_in_boundary": FUTURE_IN_BOUNDARY,
        "gap_min": EPISODE_GAP_MIN,
        "gap_clock": "wall_clock_hhmm_minutes",
        "FUTURE_IN_EPISODE_START_N": 0 if not start_uses_label else None,
    }


def generator_spec() -> dict[str, Any]:
    return {
        "ENTRY_EVENT_SOURCE_ID": ENTRY_EVENT_SOURCE_ID,
        "population": {
            "episode_n": 100482,
            "discovery_days": 291,
            "stocks": 105,
            "episode_gap_min": EPISODE_GAP_MIN,
            "future_in_boundary": FUTURE_IN_BOUNDARY,
        },
        "primitive_onsets": [
            "IMPULSE_UP",
            "IMPULSE_DOWN",
            "VWAP_RECLAIM",
            "VWAP_LOSS",
            "BREAKOUT20",
            "BREAKDOWN20",
            "PULLBACK_START",
            "VOL_EXPAND",
            "VA_EXPAND",
            "RANGE_EXPAND",
            "COMPRESSION",
            "PAUSE_AFTER_IMPULSE",
            "LAG_CATCHUP",
            "OPENING_GAP_HOLD",
        ],
        "onset_definition": (
            "Completed-bar families_at on feature_bar T, available at T+1m. "
            "Level-like flags (vol_expand, pullback, breakout, compression) fire on False→True edges only. "
            "OPENING_GAP_HOLD is a session-open special: gap-up vs prior close that holds through 09:14 close, event_time 09:15."
        ),
        "simultaneous_onsets": (
            "Same-minute multi-family events remain separate rows. "
            "cluster_symbol_day sorts (event_time, event_family). All same-minute families join one cluster."
        ),
        "same_symbol_collapse": (
            "Per (date, symbol), consecutive events with wall-clock event_time gap <= 10 minutes join one cluster. "
            "Episode trigger is the last event in the cluster after that sort. Repeated onsets do not each start a new episode."
        ),
        "gap_measurement": (
            "Wall-clock HH:MM minutes from parse_hhmm(event_time), not observed-bar count. "
            "Missing bars increase the clock gap and may split clusters."
        ),
        "lunch": (
            "11:30–12:29 is invalid entry (in_invalid_entry). Onsets are not emitted for lunch entry bars. "
            "A pre-lunch event and a post-lunch event have clock gap > 10 so they do not share a cluster. "
            "Forward paths stop if interval_crosses_lunch."
        ),
        "missing_bars": (
            "prep_symbol keeps observed minutes only. Previous VWAP/close state is the previous observed bar, "
            "not a synthetic empty minute. Cluster gap remains wall-clock."
        ),
        "session_reset": "process_day is one calendar session. session_vwap accumulators reset each symbol-day.",
        "symbol_reset": "Clustering is independent per (date, symbol).",
        "day_reset": "New date starts new clusters. prev_close carries overnight for OPENING_GAP_HOLD only.",
        "runtime_must_not_become": "evaluate R11 every minute",
        "module_files": {
            "panel": str(Path(panel_mod.__file__).resolve()),
            "episodes": str(Path(episodes_mod.__file__).resolve()),
            "walk": str(Path(walk_mod.__file__).resolve()),
        },
    }
