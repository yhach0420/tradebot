"""Research-only engine: Current Fixed path with optional clock shift / common-support mask."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.fixed_anchor_mechanism_audit_p3_0.grid import (  # noqa: E402
    hm_epoch,
    hm_label,
    session_of_epoch,
)
from run_p0_4_exact_vs_fast_parity import CollectorEngine  # noqa: E402
from small_paper.v1r_native_entry_live import JST  # noqa: E402
from small_paper.v1r_primary_runtime import CLOCK_GRID, POSITION_CAP  # noqa: E402


class P3Engine(CollectorEngine):
    """CollectorEngine trading path unchanged. Only maybe_fire_anchor wake/t0 may shift."""

    def __init__(
        self,
        *a: Any,
        offset_sec: int = 0,
        allowed_hm: Optional[tuple[tuple[int, int], ...]] = None,
        fire_mode: str = "production",
        **k: Any,
    ) -> None:
        super().__init__(*a, **k)
        self.offset_sec = int(offset_sec)
        self.allowed_hm = (
            tuple((int(h), int(m)) for h, m in allowed_hm) if allowed_hm is not None else None
        )
        self.fire_mode = str(fire_mode)
        self.anchor_occ: list[dict[str, Any]] = []

    def maybe_fire_anchor(self, *, now_t: Optional[float] = None) -> list[dict[str, Any]]:
        """Production wake, optionally on a time-shifted CLOCK_GRID.

        Shifted slots keep the original label (e.g. 09:05) even when wall t0 moved.
        Same-minute gate is preserved. No clamp / remap of out-of-session slots.
        """
        if self.fire_mode == "production" or now_t is None:
            dt = datetime.fromtimestamp(float(now_t), JST) if now_t is not None else None
            if dt is not None and self.allowed_hm is not None:
                if (dt.hour, dt.minute) not in self.allowed_hm:
                    return []
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
        key = f"{day}|{hm_label(h, m)}"
        if key in self.fired_anchors:
            return []
        if now_f <= float(t0) + 1e-12:
            return []
        sess = session_of_epoch(day, t0) or ("AM" if h < 12 else "PM")
        return self.fire_anchor_at(
            anchor=hm_label(h, m),
            t0=float(t0),
            day=day,
            session=sess,
        )

    def _run_anchor(self, *, anchor: str, t0: float, day: str, session: str) -> list[dict[str, Any]]:
        self.anchor_occ.append(
            {
                "date": day,
                "session": session,
                "anchor": anchor,
                "t0": float(t0),
                "open": sorted(str(s) for s in self.open_symbols),
                "pending": sorted(str(s) for s in self.pending),
                "exposure": int(self.exposure()),
                "open_n": int(self.open_n),
                "pending_n": int(self.pending_n),
                "position_cap": int(POSITION_CAP),
            }
        )
        return super()._run_anchor(anchor=anchor, t0=t0, day=day, session=session)
