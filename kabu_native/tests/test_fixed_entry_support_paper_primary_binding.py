"""Wiring tests for the fixed-entry-support Paper Primary activation. No Paper session."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from small_paper.paper_primary_activation import (
    ACTIVATION_ID,
    ACTIVATION_V2_ID,
    ACTIVATION_V14_ID,
    ACTIVATION_V15_ID,
    CANDIDATE_ID,
    CANDIDATE_MANIFEST_SHA,
    COMPLETE_STRATEGY_SHA,
    ENTRY_SHA,
    EXIT_SHA,
    PREVIOUS_PAPER_ACTIVATION_ID,
    PREVIOUS_SELECTOR_PATH,
    assert_selected_paper_primary,
    load_previous_paper_activation,
    resolve_paper_primary,
)
from small_paper.v1r_activation_binding import (
    OUT,
    SELECTOR_PATH,
    file_sha256,
    load_activation_manifest,
    manifest_content_sha,
    verify_generator_inventory_coverage,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)
from small_paper.v1r_exit_v2_activation_gate import PRIMARY_STRATEGY

V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
OLD_STRATEGY = "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY"
FROZEN_V2 = "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_V2"
UNCHANGED_VS_V25 = (
    "src/small_paper/kabu_registration_authority.py",
    "src/universe/core10_dynamic40_price_risk.py",
)


def _active() -> tuple[dict, dict]:
    selector = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    manifest = load_activation_manifest(selector=selector)
    return selector, manifest


def _rewrite(tmp_path: Path, manifest: dict, *, selector_sha: str | None = None) -> Path:
    body = dict(manifest)
    body["sha256"] = manifest_content_sha(body)
    man = tmp_path / f"{body['manifest_id']}.json"
    man.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    sel = {
        "schema": "V1R_ACTIVE_ACTIVATION_SELECTOR_V1",
        "activation_id": body["manifest_id"],
        "activation_sha": selector_sha if selector_sha is not None else body["sha256"],
        "manifest_relpath": str(man.resolve()).replace("\\", "/"),
        "note": "Identity-only selector; no Strategy/Precommit/trading fields.",
    }
    sp = tmp_path / "selector.json"
    sp.write_text(json.dumps(sel, indent=2) + "\n", encoding="utf-8")
    return sp


def test_selector_resolves_new_activation() -> None:
    selector, manifest = _active()
    assert selector["activation_id"] == ACTIVATION_V15_ID
    assert selector["activation_sha"] == manifest["sha256"]
    result = assert_selected_paper_primary()
    assert result.ok, result.reason
    assert result.identity["activation_id"] == ACTIVATION_V15_ID
    assert result.identity["activation_sha"] == manifest["sha256"]
    assert result.identity["execution_family"] == "X1_IMMEDIATE_ASK"
    v1 = OUT / f"{ACTIVATION_ID}.json"
    assert file_sha256(v1) == "8e2524fb6dd9d0693c8d1abc599a69c7b10ee83c5c59e057bb59826bb1daf91a"


def test_identity_hashes_match_accepted_pins() -> None:
    result = assert_selected_paper_primary()
    assert result.ok, result.reason
    ident = result.identity
    assert ident["entry_sha"] == ENTRY_SHA
    assert ident["exit_sha"] == EXIT_SHA
    assert ident["complete_strategy_sha"] == COMPLETE_STRATEGY_SHA
    assert ident["manifest_sha"] == CANDIDATE_MANIFEST_SHA
    assert "ENTRY_SHA=" + ENTRY_SHA in result.startup_block
    assert "activation_id=" + ACTIVATION_V15_ID in result.startup_block
    assert ident["activation_sha"] in result.startup_block


def test_factory_resolves_candidate_engine() -> None:
    resolved = resolve_paper_primary()
    assert resolved["candidate_name"] == CANDIDATE_ID
    assert resolved["primary_role"] == "PAPER_PRIMARY"
    assert resolved["session_executor"].__name__ == "FixedSupportX1SessionExecutor"
    assert resolved["simulate_session"] is None
    assert resolved["paper_executed"] is False
    assert resolved["submit"] == 0 and resolved["cancel"] == 0 and resolved["live"] == 0


def test_single_paper_primary() -> None:
    result = assert_selected_paper_primary()
    assert result.ok, result.reason
    assert result.identity["primary_role"] == "PAPER_PRIMARY"
    assert result.identity["primary_strategy"] == CANDIDATE_ID
    assert result.identity["primary_strategy"] != OLD_STRATEGY
    assert result.identity["primary_strategy"] != PRIMARY_STRATEGY
    assert result.identity["primary_strategy"] != FROZEN_V2
    previous = load_previous_paper_activation()
    assert previous["runtime_roles"]["strategy"] == OLD_STRATEGY
    assert previous["manifest_id"] != ACTIVATION_ID


def test_v25_preserved_and_explicitly_resolvable(tmp_path: Path) -> None:
    path = OUT / f"{PREVIOUS_PAPER_ACTIVATION_ID}.json"
    previous = load_previous_paper_activation()
    ok, got, calc = verify_manifest_self_sha(previous)
    assert ok and got == calc == V25_SHA
    assert json.loads(path.read_text(encoding="utf-8"))["sha256"] == V25_SHA
    hist = json.loads(PREVIOUS_SELECTOR_PATH.read_text(encoding="utf-8"))
    assert hist["activation_id"] == PREVIOUS_PAPER_ACTIVATION_ID
    assert hist["activation_sha"] == V25_SHA
    sel = _rewrite(tmp_path, previous)
    refused = assert_selected_paper_primary(path=sel)
    assert refused.ok is False
    assert refused.checks.get("previous_activation_resolved") is True
    assert refused.identity["primary_strategy"] == OLD_STRATEGY
    assert refused.identity["primary_role"] == "PREVIOUS_PAPER_ACTIVATION"


def test_corrupt_activation_sha_fails(tmp_path: Path) -> None:
    _selector, manifest = _active()
    sel = _rewrite(tmp_path, manifest, selector_sha="a" * 64)
    result = assert_selected_paper_primary(path=sel)
    assert result.ok is False
    assert "activation_sha_mismatch" in result.reason


def test_corrupt_entry_sha_fails(tmp_path: Path) -> None:
    _selector, manifest = _active()
    body = dict(manifest)
    body["entry_sha"] = "b" * 64
    sel = _rewrite(tmp_path, body)
    result = assert_selected_paper_primary(path=sel)
    assert result.ok is False
    assert "entry_sha_mismatch" in result.reason


def test_corrupt_candidate_name_fails(tmp_path: Path) -> None:
    _selector, manifest = _active()
    body = dict(manifest)
    body["candidate_name"] = "OTHER_CANDIDATE"
    body["primary_strategy"] = "OTHER_CANDIDATE"
    sel = _rewrite(tmp_path, body)
    result = assert_selected_paper_primary(path=sel)
    assert result.ok is False
    assert "candidate_name_mismatch" in result.reason


def test_missing_activation_fails(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    result = assert_selected_paper_primary(path=missing)
    assert result.ok is False
    assert "activation_missing" in result.reason


def test_safety_counters_and_inventory() -> None:
    result = assert_selected_paper_primary()
    assert result.ok, result.reason
    ident = result.identity
    assert ident["paper_only"] is True
    assert ident["live"] is False
    assert ident["submit"] == 0
    assert ident["cancel"] == 0
    assert ident["submit_cancel_live"] == "0/0/0"
    assert "submit/cancel/live=0/0/0" in result.startup_block
    _selector, manifest = _active()
    inv = verify_runtime_inventory(manifest)
    gen = verify_generator_inventory_coverage(manifest)
    assert inv["ok"] is True
    assert gen["ok"] is True
    previous = load_previous_paper_activation()
    stored = previous["runtime_file_sha256"]
    native = OUT.parents[2]
    for rel in UNCHANGED_VS_V25:
        if rel in stored:
            assert file_sha256(native / rel) == stored[rel]
    sim = "src/research/event_time_impulse_fixed_entry_support_candidate_v1/simulate.py"
    assert manifest["runtime_file_sha256"][sim] == file_sha256(native / sim)


def test_assert_only_does_not_start_and_live_delegates_to_existing_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from small_paper import v1r_paper_primary_launcher as launcher

    calls: list[str] = []
    monkeypatch.setattr(launcher, "_run_daily_live", lambda *a, **k: calls.append("daily") or 0)
    monkeypatch.setattr(launcher, "_run_residency_live_loop", lambda *a, **k: calls.append("residency") or 0)
    monkeypatch.setattr(launcher, "_acquire_single_primary_lock", lambda: True)
    monkeypatch.setattr(launcher, "_release_lock", lambda: None)
    assert launcher.assert_only() == 0
    assert calls == []
    code = launcher.launch_primary(mode="live", session_dir=tmp_path)
    assert code == 0
    assert calls == ["daily"]
