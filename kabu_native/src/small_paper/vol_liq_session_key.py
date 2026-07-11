"""
Phase687W1 helpers: normalize vol_liq run_session_key for safety/pilot cache alignment.
"""

from __future__ import annotations

import re
from typing import Optional

_BARE_STAMP = re.compile(r"^\d{6}$")


def normalize_vol_liq_run_session_key(run_session_key: str) -> str:
    """Canonical key: YYYYMMDD/live_session_HHMMSS (when stamp-only was passed).

    Safety historically used ``{day}/{HHMMSS}`` while pilot uses
    ``{day}/live_session_{HHMMSS}`` from the output directory name. Mismatched
    keys caused cache_missing → ~1000s baseline_fallback every AM/PM start.
    """
    key = str(run_session_key or "").replace("\\", "/").strip()
    if not key or "/" not in key:
        return key
    day, sess = key.split("/", 1)
    day = day.strip()
    sess = sess.strip()
    if not day or not sess:
        return key
    if sess.startswith(("live_session_", "live_full_session_", "push_replay_", "phase")):
        return f"{day}/{sess}"
    if _BARE_STAMP.match(sess):
        return f"{day}/live_session_{sess}"
    return f"{day}/{sess}"


def vol_liq_cache_key_aliases(run_session_key: str) -> list[str]:
    """Return lookup order: canonical first, then historical bare-stamp alias."""
    canon = normalize_vol_liq_run_session_key(run_session_key)
    aliases = [canon]
    if "/" in canon:
        day, sess = canon.split("/", 1)
        if sess.startswith("live_session_"):
            bare = sess[len("live_session_") :]
            if _BARE_STAMP.match(bare):
                alt = f"{day}/{bare}"
                if alt not in aliases:
                    aliases.append(alt)
        elif _BARE_STAMP.match(sess):
            alt = f"{day}/live_session_{sess}"
            if alt not in aliases:
                aliases.insert(0, alt)
    raw = str(run_session_key or "").replace("\\", "/").strip()
    if raw and raw not in aliases:
        aliases.append(raw)
    return aliases


def am_pm_cache_reuse_allowed(*, am_key: str, pm_key: str) -> bool:
    """AM→PM reuse is forbidden: PM prior set can include same-day AM session."""
    a = normalize_vol_liq_run_session_key(am_key)
    p = normalize_vol_liq_run_session_key(pm_key)
    if not a or not p or a == p:
        return False
    return False  # explicit policy


def day_stamp_from_key(run_session_key: str) -> Optional[str]:
    key = normalize_vol_liq_run_session_key(run_session_key)
    if "/" not in key:
        return None
    return key.split("/", 1)[0]
