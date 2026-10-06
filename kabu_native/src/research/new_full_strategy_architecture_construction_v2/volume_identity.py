"""Pin exact existing S_VOL_CONFIRM_1M. No threshold search."""
from __future__ import annotations

import inspect
from typing import Any

from research.simple_tech_entry_family import VOLUME_MEDIAN_BARS, VOLUME_MULT
from research.simple_tech_entry_family.stages import volume_confirm
from research.systematic_state_transition_full_strategy_v1.states import STATE_FNS


def volume_identity() -> dict[str, Any]:
    src = inspect.getsource(volume_confirm)
    window = "ind[\"volume\"][i - w : i]" in src or "ind['volume'][i - w : i]" in src
    ok = (
        abs(float(VOLUME_MULT) - 1.5) < 1e-12
        and int(VOLUME_MEDIAN_BARS) == 5
        and STATE_FNS.get("S_VOL_CONFIRM_1M") is volume_confirm
        and window
        and "np.median" in src
    )
    return {
        "STATE_ID": "S_VOL_CONFIRM_1M",
        "SOURCE_PATH": "src/research/simple_tech_entry_family/stages.py",
        "SOURCE_FUNCTION": "volume_confirm",
        "VOLUME_MEDIAN_BARS": int(VOLUME_MEDIAN_BARS),
        "VOLUME_MULT": float(VOLUME_MULT),
        "CURRENT_BAR_EXCLUDED": True,
        "THRESHOLD_SEARCH": False,
        "ROLE": "BREAK_PARTICIPATION_CONFIRMATION",
        "ST_BINDING": STATE_FNS.get("S_VOL_CONFIRM_1M") is volume_confirm,
        "IDENTITY_PASS": bool(ok),
    }
