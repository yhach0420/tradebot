"""Keep this validation from writing over frozen V2, capture, or the previous audit."""
from pathlib import Path

FORBIDDEN_SEGMENTS = (
    "/event_time_impulse_complete_strategy_v2/",
    "/event_time_volume_confirmed_impulse/",
    "/data/market_capture/",
    "/v2_resistance_structure_audit_v1/",
)

REQUIRED_MARKER = "v2_structural_resistance_detector_validation_v1"


def assert_isolated(out: Path) -> None:
    text = "/" + str(out).replace("\\", "/").strip("/") + "/"
    if REQUIRED_MARKER not in text:
        raise RuntimeError("output_not_isolated")
    for seg in FORBIDDEN_SEGMENTS:
        if seg in text:
            raise RuntimeError(f"output_overlaps:{seg.strip('/')}")
