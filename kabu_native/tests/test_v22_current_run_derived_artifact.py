"""V22: stale derived artifacts are not SoT; cert dest is run-scoped."""
from __future__ import annotations

import json
from pathlib import Path

from small_paper.derived_artifact_contract import (
    ARTIFACT_SCHEMA_VERSION,
    CURRENT_DESIGN_CONSISTENCY_NOT_PROVEN,
    STALE_DERIVED_ARTIFACT_REJECTED,
    cert_stage_dest,
    current_derived_scope,
    dump_runtime_artifact_inventory,
    evaluate_or_recompute_design_consistency,
    stamp_derived_artifact,
    validate_derived_artifact,
)
from small_paper.operational_recovery import evaluate_design_consistency_artifact, probe_workspace_recovery
from small_paper.paper_full_day_certification import (
    copy_scoped_run_snapshot,
    count_stale_dest_artifacts,
    evaluate_current_stage_lifecycle,
    failed_tests_from_current_stage,
    source_regression_gates,
)
from small_paper.v1r_activation_binding import RUNTIME_DEPENDENCY_RELS

DAY = "20260812"
NATIVE = Path(__file__).resolve().parents[1]
V20_LEFTOVER = {
    "pass": False,
    "mismatch_count": 1,
    "mismatches": [
        {
            "field": "token_probe_statuses",
            "schema": ["ok", "missing"],
            "code": ["ok", "missing", "STATION_REACHABLE_AUTH_DEFERRED"],
        }
    ],
}


def _v20_write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(V20_LEFTOVER, indent=2), encoding="utf-8")


def test_stale_v20_design_consistency_rejected_and_recomputed_pass(tmp_path: Path) -> None:
    art = tmp_path / "phase687w3_design_consistency.json"
    _v20_write(art)
    out = evaluate_or_recompute_design_consistency(
        tmp_path,
        trading_date=DAY,
        path=art,
        recompute_fn=lambda: {"pass": True, "mismatch_count": 0, "mismatches": []},
    )
    assert out["stale_derived_artifact_rejected"] is True
    assert out["status"] != "fail" or out["recomputed"] is True
    assert out["recomputed"] is True
    assert out["pass"] is True
    written = json.loads(art.read_text(encoding="utf-8"))
    assert written["pass"] is True
    assert written["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert written["artifact_type"] == "design_consistency"
    assert written["input_manifest_sha"]
    assert written["activation_sha"]
    assert written["runtime_commit"]
    assert written["config_sha"]
    assert "DESIGN_CONFIG_MISMATCH" not in str(out)


def test_real_schema_recompute_pass_despite_v20_leftover(tmp_path: Path) -> None:
    art = tmp_path / "phase687w3_design_consistency.json"
    _v20_write(art)
    out = evaluate_or_recompute_design_consistency(
        NATIVE,
        trading_date=DAY,
        path=art,
        write=True,
    )
    assert out["stale_derived_artifact_rejected"] is True
    assert out["recomputed"] is True
    assert out["pass"] is True, out
    assert json.loads(art.read_text(encoding="utf-8"))["pass"] is True


def test_current_design_consistency_fail_still_blocks(tmp_path: Path) -> None:
    art = tmp_path / "phase687w3_design_consistency.json"
    _v20_write(art)
    out = evaluate_or_recompute_design_consistency(
        tmp_path,
        trading_date=DAY,
        path=art,
        recompute_fn=lambda: {
            "pass": False,
            "mismatch_count": 1,
            "mismatches": [{"field": "current_inputs"}],
        },
    )
    assert out["recomputed"] is True
    assert out["pass"] is False
    assert out["status"] == "fail"


def test_current_identity_fail_not_ignored_as_stale(tmp_path: Path) -> None:
    art = tmp_path / "dc.json"
    fresh = {"pass": False, "mismatch_count": 1, "mismatches": [{"field": "now"}]}
    stamp_derived_artifact(
        fresh,
        artifact_type="design_consistency",
        native_root=tmp_path,
        trading_date=DAY,
        producer="test",
    )
    art.write_text(json.dumps(fresh), encoding="utf-8")
    out = evaluate_or_recompute_design_consistency(
        tmp_path,
        trading_date=DAY,
        path=art,
        recompute_fn=lambda: {"pass": True, "mismatch_count": 0},
    )
    assert out["recomputed"] is False
    assert out["pass"] is False
    assert out["stale_derived_artifact_rejected"] is False


def test_recompute_error_is_not_proven(tmp_path: Path) -> None:
    art = tmp_path / "dc.json"
    _v20_write(art)

    def _boom() -> dict:
        raise RuntimeError("cannot_recompute")

    out = evaluate_or_recompute_design_consistency(
        tmp_path, trading_date=DAY, path=art, recompute_fn=_boom
    )
    assert out["pass"] is False
    assert out["status"] == CURRENT_DESIGN_CONSISTENCY_NOT_PROVEN


def test_recovery_does_not_promote_v20_leftover_fail(tmp_path: Path) -> None:
    reports = (
        tmp_path
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    _v20_write(reports)
    cfg = tmp_path / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("live_trading_enabled: false\norder_enabled: false\n", encoding="utf-8")
    from small_paper.operational_recovery import config_sha256

    (cfg.parent / "production_config_sha256.pin").write_text(config_sha256(cfg) + "\n", encoding="utf-8")
    result = probe_workspace_recovery(tmp_path, trading_date=DAY, config_path=cfg)
    design = (result.get("artifact_trace") or {}).get("design") or {}
    assert design.get("stale_derived_artifact_rejected") is True
    assert design.get("recomputed") is True
    assert design.get("pass") is True, design
    codes = {b["code"] for b in result["blockers"]}
    assert "DESIGN_CONFIG_MISMATCH" not in codes


def test_cert_dest_contamination_excluded_from_failed_tests(tmp_path: Path) -> None:
    cert_dir = tmp_path / "paper_runtime_full_day_certification"
    leftovers = [
        (
            cert_dir / "run_snapshots" / "full_day" / f"phase148_am_pm_daily_runner_{DAY}.json",
            {
                "verdict": "preflight_blocked",
                "stopped_reason": "preflight_blocked",
                "preflight": {"safety": {"failed_check_ids": ["kabu_station_connection"]}},
                "certification_run_id": "v18",
                "stage_run_id": "v18_full",
                "activation_sha": "old",
            },
        ),
        (
            cert_dir / "run_snapshots" / "full_day" / f"small_paper_safety_{DAY}.json",
            {
                "failed_check_ids": ["kabu_station_connection"],
                "certification_run_id": "v18",
                "stage_run_id": "v18_full",
                "activation_sha": "old",
            },
        ),
        (
            cert_dir / "run_snapshots" / "pm_direct_start" / f"phase148_am_pm_daily_runner_{DAY}.json",
            {
                "verdict": "fail",
                "am_live": {"reason": "STARTED", "ok": True, "am_token_mutation": 0},
                "certification_run_id": "v20",
                "stage_run_id": "v20_pm",
                "activation_sha": "old",
            },
        ),
        (
            cert_dir / "run_snapshots" / "window_A" / f"phase148_am_pm_daily_runner_{DAY}.json",
            {
                "verdict": "fail",
                "certification_run_id": "v21",
                "stage_run_id": "v21_win",
                "activation_sha": "old",
            },
        ),
    ]
    for path, body in leftovers:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")

    cert_id = "cert_v22_now"
    stage_id = "full_day_current"
    dest = cert_stage_dest(cert_dir, cert_id, stage_id)
    reports = tmp_path / "reports"
    reports.mkdir()
    snap = copy_scoped_run_snapshot(
        dest=dest,
        reports_dir=reports,
        day=DAY,
        expected_scope={
            "certification_run_id": cert_id,
            "stage_run_id": stage_id,
            "activation_sha": "current-sha",
        },
    )
    assert snap["copied"] == {}
    lifecycle = evaluate_current_stage_lifecycle(
        copied=snap["copied"],
        expected_scope={
            "certification_run_id": cert_id,
            "stage_run_id": stage_id,
            "activation_sha": "current-sha",
        },
        snap_dir=dest,
    )
    assert lifecycle["safety_failed"] == []
    failed = failed_tests_from_current_stage(
        stage="full_day",
        invoke_ok=False,
        lifecycle=lifecycle,
        copied=snap["copied"],
    )
    blob = " ".join(failed)
    assert failed == ["FULL_DAY_CHECKED_BAT"]
    assert "kabu_station_connection" not in blob
    assert "FULL_DAY_SAFETY" not in blob
    pm_failed = failed_tests_from_current_stage(
        stage="pm_direct",
        invoke_ok=True,
        lifecycle=lifecycle,
        copied={},
    )
    assert "PM_DIRECT_START_AM_NOT_SKIPPED" not in pm_failed
    excluded = count_stale_dest_artifacts(cert_dir, cert_id)
    assert excluded >= 4


def test_wrong_stage_and_activation_and_runtime_rejected(tmp_path: Path) -> None:
    expected = current_derived_scope(native_root=tmp_path, trading_date=DAY)
    expected = dict(expected)
    expected["certification_run_id"] = "cert_now"
    expected["stage_run_id"] = "stage_now"
    base = {
        "pass": True,
        "mismatch_count": 0,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_type": "design_consistency",
        "activation_id": expected["activation_id"],
        "activation_sha": expected["activation_sha"],
        "runtime_commit": expected["runtime_commit"],
        "config_sha": expected["config_sha"],
        "trading_date": DAY,
        "generated_at": "2026-08-15T00:00:00+09:00",
        "input_manifest_sha": expected["input_manifest_sha"],
        "certification_run_id": "cert_now",
        "stage_run_id": "stage_now",
    }
    wrong_stage = dict(base)
    wrong_stage["stage_run_id"] = "other_stage"
    st = validate_derived_artifact(wrong_stage, expected, require_cert_ids=True)
    assert st["ok"] is False
    assert st["reject_code"] == "stage_run_id_mismatch"
    assert st["reason"] == STALE_DERIVED_ARTIFACT_REJECTED

    wrong_act = dict(base)
    wrong_act["activation_sha"] = "other_activation"
    ac = validate_derived_artifact(wrong_act, expected, require_cert_ids=True)
    assert ac["ok"] is False
    assert ac["reject_code"] == "activation_sha_mismatch"

    wrong_rt = dict(base)
    wrong_rt["runtime_commit"] = "deadbeef"
    rt = validate_derived_artifact(wrong_rt, expected, require_cert_ids=True)
    assert rt["ok"] is False
    assert rt["reject_code"] == "runtime_commit_mismatch"

    wrong_cfg = dict(base)
    wrong_cfg["config_sha"] = "ffff"
    cfg = validate_derived_artifact(wrong_cfg, expected, require_cert_ids=True)
    assert cfg["ok"] is False
    assert cfg["reject_code"] == "config_sha_mismatch"

    ok = validate_derived_artifact(base, expected, require_cert_ids=True)
    assert ok["ok"] is True


def test_source_gates_include_v22_contracts() -> None:
    gates = source_regression_gates()
    assert gates["ok"], gates.get("failed")
    assert gates["checks"]["STALE_DERIVED_RUNTIME_ARTIFACT"]["ok"]
    assert gates["checks"]["CERT_DEST_RUN_SCOPE"]["ok"]
    assert gates["checks"]["INGRESS_CURRENT_RUN_IDENTITY"]["ok"]
    assert "src/small_paper/derived_artifact_contract.py" in RUNTIME_DEPENDENCY_RELS


def test_inventory_dump_classifies_design_as_derived(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    body = dump_runtime_artifact_inventory(path)
    classes = {row["path"]: row["class"] for row in body["items"]}
    assert classes["results/reports/phase687w3_e2e_readonly_reconciliation/phase687w3_design_consistency.json"] == "C"
    assert any(row["class"] == "D" and "run_snapshots" in row["path"] for row in body["items"])
    assert path.is_file()


def test_evaluate_design_consistency_artifact_wrapper(tmp_path: Path) -> None:
    art = (
        tmp_path
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    _v20_write(art)
    out = evaluate_design_consistency_artifact(tmp_path, trading_date=DAY)
    assert out["recomputed"] is True
    assert out["pass"] is True, out
