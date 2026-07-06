"""Phase652 shadow registry tests."""

from __future__ import annotations

import csv
import json
import sys
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase652_shadow_registry import (  # noqa: E402
    PHASE652_VERDICT,
    REGISTRY_COLUMNS,
    _registry_definitions,
    _yaml_enabled,
    run,
)


class Phase652RegistryTests(unittest.TestCase):
    def test_registry_has_required_shadows(self) -> None:
        ids = {d.shadow_id for d in _registry_definitions()}
        required = {
            "pbv2_rise5_shadow",
            "pbv2_flat_band_shadow",
            "board_dynamic_trailing_shadow",
            "pullback_misread_guard_shadow",
            "exit_shadow_monitor_t2_t3",
            "sector_heat_forward_shadow",
            "phase632_pbv2_profit_filter",
            "phase649_flat_band_guard",
            "phase643_position_sizing_shadow",
            "classic_momentum_forward_shadow",
        }
        self.assertTrue(required.issubset(ids))

    def test_yaml_enabled_rise5(self) -> None:
        sd = next(d for d in _registry_definitions() if d.shadow_id == "pbv2_rise5_shadow")
        self.assertTrue(_yaml_enabled({"pbv2_rise5_shadow_enabled": True}, sd))

    def test_run_produces_artifacts(self) -> None:
        report = run()
        self.assertEqual(report["verdict"], PHASE652_VERDICT)
        reg = Path(report["artifacts"]["registry_csv"])
        self.assertTrue(reg.is_file())
        with reg.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertGreaterEqual(len(rows), 20)
        self.assertEqual(rows[0].keys(), set(REGISTRY_COLUMNS))
        dash = json.loads(Path(report["artifacts"]["dashboard_json"]).read_text(encoding="utf-8"))
        self.assertIn("shadows", dash)
        self.assertIn("pbv2_rise5_shadow", dash["shadows"])


if __name__ == "__main__":
    unittest.main()
