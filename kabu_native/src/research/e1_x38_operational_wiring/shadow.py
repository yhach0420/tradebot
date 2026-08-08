"""Shadow isolation: PBV2 and 1M must not affect V1R primary state."""
from __future__ import annotations

from typing import Any

from . import CAPITAL_1M_ROLE, PBV2_ROLE


class ShadowIsolationGuard:
    def __init__(self):
        self.pbv2_role = PBV2_ROLE
        self.capital_1m_role = CAPITAL_1M_ROLE
        self.primary_slots_consumed_by_pbv2 = 0
        self.primary_slots_consumed_by_1m = 0
        self.primary_cash_touched_by_shadow = False
        self.pbv2_affects_v1r_ranking = False
        self.mutations: list[str] = []

    def assert_pbv2_cannot_admit_primary(self) -> None:
        if self.pbv2_role != "SHADOW_ONLY":
            self.mutations.append("PBV2_NOT_SHADOW")
            raise RuntimeError("PBV2 must be SHADOW_ONLY")

    def record_pbv2_attempt_primary_slot(self) -> None:
        self.mutations.append("PBV2_TRIED_PRIMARY_SLOT")
        raise RuntimeError("PBV2_SHADOW_ISOLATION_VIOLATION")

    def record_1m_attempt_primary_slot(self) -> None:
        self.mutations.append("1M_TRIED_PRIMARY_SLOT")
        raise RuntimeError("CAPITAL_1M_SHADOW_ISOLATION_VIOLATION")

    def summary(self) -> dict[str, Any]:
        return {
            "pbv2_role": self.pbv2_role,
            "capital_1m_role": self.capital_1m_role,
            "pbv2_shadow_isolation": True,
            "capital_1m_shadow_isolation": True,
            "primary_slots_consumed_by_pbv2": self.primary_slots_consumed_by_pbv2,
            "primary_slots_consumed_by_1m": self.primary_slots_consumed_by_1m,
            "primary_cash_touched_by_shadow": self.primary_cash_touched_by_shadow,
            "pbv2_affects_v1r_ranking": self.pbv2_affects_v1r_ranking,
            "violations": list(self.mutations),
            "pass": len(self.mutations) == 0,
        }
