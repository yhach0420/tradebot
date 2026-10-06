"""Shadow activation is explicit. Defaults cannot emit or order."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShadowConfig:
    enabled: bool = False
    shadow_only: bool = True
    orders_enabled: bool = False

    def allows_shadow_evaluation(self) -> bool:
        return bool(self.enabled) and bool(self.shadow_only) and not bool(self.orders_enabled)


def activation(*, enabled: bool, shadow_only: bool = True, orders_enabled: bool = False) -> ShadowConfig:
    if orders_enabled or not shadow_only:
        raise RuntimeError("shadow_activation_refused")
    return ShadowConfig(enabled=bool(enabled), shadow_only=True, orders_enabled=False)
