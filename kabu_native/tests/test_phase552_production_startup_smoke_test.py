"""Phase552 production startup smoke test regression tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
for p in (REPO, KABU / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.config import SmallPaperPilotConfig, load_pilot_config  # noqa: E402
from small_paper.entry_cluster_guard import (  # noqa: E402
    PHASE552_SMOKE_VERDICT,
    validate_entry_cluster_guard_model,
)
from small_paper.production_startup_smoke_test import (  # noqa: E402
    DEFAULT_PRODUCTION_CONFIG_REL,
    run_production_startup_smoke_test,
)


class TestProductionStartupSmokeTest(unittest.TestCase):
    def test_smoke_test_ok_production_path(self) -> None:
        report = run_production_startup_smoke_test(
            repo_root=REPO,
            config_rel=DEFAULT_PRODUCTION_CONFIG_REL,
        )
        self.assertTrue(report.ready, report.errors)
        self.assertEqual(report.verdict, PHASE552_SMOKE_VERDICT)
        self.assertTrue(report.checks.get("make_exposure_gate"))
        self.assertTrue(report.checks.get("gate_entry_cluster_guard"))
        self.assertTrue(report.checks.get("classifier_inference"))

    def test_model_missing_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "kabu_native" / "configs").mkdir(parents=True)
            cfg = SmallPaperPilotConfig(
                entry_cluster_guard_enabled=True,
                raw={
                    "entry_cluster_guard_model_path": "kabu_native/configs/missing_model.json",
                    "entry_cluster_guard_reject_clusters": [5],
                    "entry_cluster_guard_reject_csubs": [0, 2, 3, 5],
                },
            )
            yaml_path = root / "kabu_native" / "configs" / "test.yaml"
            yaml_path.write_text(
                "entry_cluster_guard_enabled: true\n"
                "entry_cluster_guard_model_path: kabu_native/configs/missing_model.json\n",
                encoding="utf-8",
            )
            config = load_pilot_config(yaml_path)
            _, errors = validate_entry_cluster_guard_model(config, repo_root=root)
            self.assertTrue(errors)
            report = run_production_startup_smoke_test(
                repo_root=root,
                config_rel="kabu_native/configs/test.yaml",
            )
            self.assertFalse(report.ready)
            self.assertTrue(any("not found" in e.lower() for e in report.errors))

    def test_invalid_json_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_dir = root / "kabu_native" / "configs"
            cfg_dir.mkdir(parents=True)
            model = cfg_dir / "entry_cluster_guard_model.json"
            model.write_text("{not-json", encoding="utf-8")
            yaml_path = cfg_dir / "test.yaml"
            yaml_path.write_text(
                "entry_cluster_guard_enabled: true\n"
                "entry_cluster_guard_model_path: kabu_native/configs/entry_cluster_guard_model.json\n",
                encoding="utf-8",
            )
            config = load_pilot_config(yaml_path)
            _, errors = validate_entry_cluster_guard_model(config, repo_root=root)
            self.assertTrue(any("JSON" in e for e in errors))

    def test_wrong_yaml_path_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_dir = root / "kabu_native" / "configs"
            cfg_dir.mkdir(parents=True)
            good = KABU / "configs" / "entry_cluster_guard_model.json"
            self.assertTrue(good.is_file())
            yaml_path = cfg_dir / "test.yaml"
            yaml_path.write_text(
                "entry_cluster_guard_enabled: true\n"
                "entry_cluster_guard_model_path: configs/wrong_model.json\n",
                encoding="utf-8",
            )
            config = load_pilot_config(yaml_path)
            _, errors = validate_entry_cluster_guard_model(config, repo_root=root)
            self.assertTrue(errors)

    def test_correct_yaml_path_ok(self) -> None:
        cfg_path = KABU / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        config = load_pilot_config(cfg_path)
        self.assertEqual(
            config.raw.get("entry_cluster_guard_model_path"),
            "kabu_native/configs/entry_cluster_guard_model.json",
        )
        _, errors = validate_entry_cluster_guard_model(config, repo_root=REPO)
        self.assertEqual(errors, [])

    def test_smoke_script_entrypoint_fails_on_missing_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_dir = root / "kabu_native" / "configs"
            cfg_dir.mkdir(parents=True)
            yaml_path = cfg_dir / "test.yaml"
            yaml_path.write_text("entry_cluster_guard_enabled: true\n", encoding="utf-8")
            report = run_production_startup_smoke_test(
                repo_root=root,
                config_rel="kabu_native/configs/test.yaml",
            )
            self.assertFalse(report.ready)


if __name__ == "__main__":
    unittest.main()
