"""Phase269: daily runner default universe is price-risk."""

import unittest

from runner.am_pm_daily_runner import (
    ENTRY_GUARD_SHADOW_YAML,
    SHADOW_PILOT_YAML,
    UNIVERSE_MODE_DEFAULT,
    UNIVERSE_MODE_LEGACY,
    UNIVERSE_MODE_PRICE_RISK,
    build_commands_json,
    make_state,
    DailyRunnerOptions,
)


class TestPhase269DailyRunnerDefaultUniverse(unittest.TestCase):
    def test_default_constants(self) -> None:
        self.assertEqual(UNIVERSE_MODE_DEFAULT, UNIVERSE_MODE_PRICE_RISK)
        self.assertNotEqual(UNIVERSE_MODE_LEGACY, UNIVERSE_MODE_DEFAULT)

    def test_build_commands_config_flag(self) -> None:
        repo = __import__("pathlib").Path(__file__).resolve().parents[2]
        native = repo / "kabu_native"
        day = "20260602"
        st_pr = make_state(
            repo,
            native,
            DailyRunnerOptions(
                day_stamp=day,
                universe_mode=UNIVERSE_MODE_DEFAULT,
                config_rel=ENTRY_GUARD_SHADOW_YAML,
            ),
        )
        st_leg = make_state(
            repo,
            native,
            DailyRunnerOptions(
                day_stamp=day,
                universe_mode=UNIVERSE_MODE_LEGACY,
                config_rel=SHADOW_PILOT_YAML,
            ),
        )
        cmd_pr = build_commands_json(st_pr)["daily_runner"]["phase148_script"]
        cmd_leg = build_commands_json(st_leg)["daily_runner"]["phase148_script"]
        self.assertIn(UNIVERSE_MODE_PRICE_RISK, cmd_pr)
        self.assertNotIn("--config", cmd_pr)
        self.assertIn(UNIVERSE_MODE_LEGACY, cmd_leg)
        self.assertIn("--config", cmd_leg)


if __name__ == "__main__":
    unittest.main()
