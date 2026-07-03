"""Phase616B: ExtensionBus session_end TypeError fix and AM→PM regression."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from runner.am_pm_daily_runner import (  # noqa: E402
    DailyRunnerOptions,
    make_state,
    _run_daily_runner_body,
)
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.core_runtime_mode import CoreRuntimeMode  # noqa: E402
from small_paper.extension_bus import ExtensionBus  # noqa: E402
from small_paper.exit_shadow_monitor import finalize_session_exit_shadow_monitor_safe  # noqa: E402

PROD_YAML = NATIVE / "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"


def _mock_state() -> SimpleNamespace:
    return SimpleNamespace(accepted_rows=[], events=[])


class TestPhase616bExtensionBusSessionEnd(unittest.TestCase):
    def test_finalize_signature_has_no_state_kwarg(self) -> None:
        """Regression guard: wrong ExtensionBus call used state=/summary=/config= kwargs."""
        import inspect

        sig = inspect.signature(finalize_session_exit_shadow_monitor_safe)
        self.assertIn("events", sig.parameters)
        self.assertIn("monitor", sig.parameters)
        self.assertNotIn("state", sig.parameters)
        self.assertNotIn("summary", sig.parameters)
        self.assertNotIn("config", sig.parameters)

    def test_on_session_end_with_exit_shadow_monitor_enabled(self) -> None:
        config = load_pilot_config(PROD_YAML)
        self.assertTrue(config.exit_shadow_monitor_enabled)
        bus = ExtensionBus(
            mode=CoreRuntimeMode.FULL_EXTENSION,
            config=config,
            state=_mock_state(),
            writer=object(),
            output_dir=None,
        )
        summary = {"exit_shadow_monitor_enabled": True}
        out = bus.on_session_end(
            _mock_state(),
            summary,
            config=config,
            output_dir=None,
        )
        self.assertIsInstance(out, dict)
        self.assertNotIn("extension_errors", out)

    def test_on_session_end_captures_extension_errors(self) -> None:
        config = load_pilot_config(PROD_YAML)
        bus = ExtensionBus(
            mode=CoreRuntimeMode.FULL_EXTENSION,
            config=config,
            state=_mock_state(),
            writer=object(),
            output_dir=None,
        )
        with mock.patch(
            "small_paper.quality_formula_shadow.finalize_session_quality_shadow",
            side_effect=RuntimeError("quality_shadow_boom"),
        ):
            out = bus.on_session_end(
                _mock_state(),
                {},
                config=config,
                output_dir=None,
            )
        self.assertIn("extension_errors", out)
        self.assertTrue(any("quality_shadow" in e for e in out["extension_errors"]))

    def test_on_session_end_preserves_prior_extension_errors(self) -> None:
        config = load_pilot_config(PROD_YAML)
        bus = ExtensionBus(
            mode=CoreRuntimeMode.FULL_EXTENSION,
            config=config,
            state=_mock_state(),
            writer=object(),
            output_dir=None,
        )
        with mock.patch(
            "small_paper.trading_value_shadow_gate.finalize_session_trading_value_shadow",
            side_effect=ValueError("tv_fail"),
        ):
            out = bus.on_session_end(
                _mock_state(),
                {"extension_errors": ["prior: err"]},
                config=config,
                output_dir=None,
            )
        errors = out["extension_errors"]
        self.assertIn("prior: err", errors)
        self.assertTrue(any("trading_value_shadow" in e for e in errors))

    def test_am_session_end_does_not_block_pm_transition(self) -> None:
        """AM pilot ok (incl. extension bus session_end) must still reach PM prep."""
        tmp_path = Path(self.id().replace(".", "_"))
        try:
            tmp_path.mkdir(parents=True, exist_ok=True)
            state = make_state(
                tmp_path,
                tmp_path / "kabu_native",
                DailyRunnerOptions(
                    day_stamp="20260521",
                    skip_safety=True,
                    skip_kabu=True,
                    dry_run_only=False,
                    skip_pm=False,
                ),
            )
            am_live = {
                "exit_code": 0,
                "pilot_ok": True,
                "session_detection_ok": True,
                "ok": True,
                "summary": {"am_pm_session": {"kind": "am"}},
            }
            with mock.patch("runner.am_pm_daily_runner.preflight", return_value=True):
                with mock.patch(
                    "runner.am_pm_daily_runner.build_am_universe",
                    return_value={
                        "ok": True,
                        "am_csv": "kabu_native/results/reports/universe_core10_dynamic40_am_20260521.csv",
                    },
                ):
                    with mock.patch(
                        "runner.am_pm_daily_runner.notify_screening_universe_discord",
                        return_value={"skipped": True},
                    ):
                        with mock.patch(
                            "runner.am_pm_daily_runner.run_pilot_session",
                            return_value=am_live,
                        ):
                            with mock.patch(
                                "runner.am_pm_daily_runner.kabu_clear_stale_registrations",
                                return_value={"skipped": True},
                            ):
                                with mock.patch(
                                    "runner.am_pm_daily_runner.wait_until_hhmm",
                                    return_value={"skipped": True},
                                ):
                                    with mock.patch(
                                        "runner.am_pm_daily_runner.build_pm_universe"
                                    ) as pm_build:
                                        pm_build.return_value = {"ok": False, "error": "stop_here"}
                                        with mock.patch("runner.am_pm_daily_runner.write_outputs"):
                                            rc = _run_daily_runner_body(state)
            self.assertEqual(rc, 2)
            self.assertEqual(state.stopped_reason, "pm_universe")
            pm_build.assert_called_once()
        finally:
            if tmp_path.exists():
                for p in sorted(tmp_path.rglob("*"), reverse=True):
                    if p.is_file():
                        p.unlink()
                    elif p.is_dir():
                        p.rmdir()


if __name__ == "__main__":
    unittest.main()
