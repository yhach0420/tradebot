"""Audit Exact engine: occupancy snapshot only. Fill/score path is C3LookupEngine."""
from __future__ import annotations

from typing import Any

from research.entry_objective_redesign_c3.engine import C3LookupEngine
from small_paper.v1r_primary_runtime import POSITION_CAP


class AuditEngine(C3LookupEngine):
    """Same live Ridge Exact path as C3 FINAL / OOF-stitched. Occupancy at CLOCK fire."""

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
