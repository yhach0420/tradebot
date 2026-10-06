"""Keep this audit from writing over frozen V2 or capture data."""
from pathlib import Path

FORBIDDEN = (
    "event_time_impulse_complete_strategy_v2",
    "event_time_volume_confirmed_impulse",
    "capture",
)


def assert_isolated(out: Path) -> None:
    text = str(out).replace("\\", "/")
    if "v2_resistance_structure_audit_v1" not in text:
        raise RuntimeError("output_not_isolated")
    for name in FORBIDDEN:
        if name in text:
            raise RuntimeError(f"output_overlaps:{name}")
