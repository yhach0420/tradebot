"""Research engine: production CLOCK_GRID or shifted grid. Invalid shifts do not fire."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch, hm_label, session_of_epoch
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine
from small_paper.v1r_native_entry_live import JST
from small_paper.v1r_primary_runtime import CLOCK_GRID


class RobustEngine(P3Engine):
    """CollectorEngine trading path unchanged. Shifted wake skips session-boundary slots."""

    def maybe_fire_anchor(self, *, now_t: Optional[float] = None) -> list[dict[str, Any]]:
        if self.fire_mode == "production" or now_t is None:
            return super().maybe_fire_anchor(now_t=now_t)

        now_f = float(now_t)
        dt = datetime.fromtimestamp(now_f, JST)
        day = dt.strftime("%Y%m%d")
        hm_now = (dt.hour, dt.minute)
        grid = self.allowed_hm if self.allowed_hm is not None else CLOCK_GRID
        hit: Optional[tuple[float, int, int]] = None
        for h, m in grid:
            t0 = hm_epoch(day, h, m) + float(self.offset_sec)
            dt0 = datetime.fromtimestamp(t0, JST)
            if (dt0.hour, dt0.minute) == hm_now:
                hit = (t0, h, m)
                break
        if hit is None:
            return []
        t0, h, m = hit
        if session_of_epoch(day, t0) is None:
            return []
        key = f"{day}|{hm_label(h, m)}"
        if key in self.fired_anchors:
            return []
        if now_f <= float(t0) + 1e-12:
            return []
        sess = session_of_epoch(day, t0)
        if sess is None:
            return []
        return self.fire_anchor_at(
            anchor=hm_label(h, m),
            t0=float(t0),
            day=day,
            session=sess,
        )
