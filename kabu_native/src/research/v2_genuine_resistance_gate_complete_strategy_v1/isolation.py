from pathlib import Path

def assert_isolated(out: Path) -> None:
    text = str(out).replace("\\", "/")
    if "v2_genuine_resistance_gate_complete_strategy_v1" not in text:
        raise RuntimeError("output_not_isolated")
    for name in ("event_time_impulse_complete_strategy_v2", "v2_structural_resistance_detector_validation_v1", "capture"):
        if name in text:
            raise RuntimeError(f"output_overlaps:{name}")
