"""V26-G2: generic activation resolver, inventory coverage, candidate fail-closed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from small_paper.v1r_activation_binding import (
    CANDIDATE_STATUS_UNCERTIFIED,
    ENV_ACTIVATION_SELECTOR,
    NATIVE,
    OUT,
    RUNTIME_CRITICAL_MUST_COVER,
    RUNTIME_DEPENDENCY_RELS,
    SELECTOR_PATH,
    UNCERTIFIED_NOT_ALLOWED,
    V25_ACTIVATION_ID,
    audit_runtime_inventory_coverage,
    collect_runtime_inventory,
    inventory_digest,
    load_active_selector,
    manifest_content_sha,
    uncertified_paper_blocked_reason,
    verify_generator_inventory_coverage,
    verify_runtime_inventory,
)
from small_paper.v1r_exit_v2_activation_gate import assert_exit_v2_primary_roles
from small_paper.runtime_clock import ENV_CERT_MODE
from small_paper.operational_validation import ENV_OPVAL_MODE

V25_MANIFEST = OUT / f"{V25_ACTIVATION_ID}.json"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
CID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_TEST"


def _v25() -> dict:
    return json.loads(V25_MANIFEST.read_text(encoding="utf-8"))


def _make_candidate_body(
    *,
    inventory: dict[str, str] | None = None,
    candidate_id: str = CID,
    drop_rel: str | None = None,
    hash_rel: str | None = None,
) -> dict:
    parent = _v25()
    body = {k: v for k, v in parent.items() if k != "sha256"}
    inv = dict(inventory if inventory is not None else collect_runtime_inventory())
    if drop_rel:
        inv.pop(drop_rel, None)
    if hash_rel and hash_rel in inv:
        inv[hash_rel] = ("0" if inv[hash_rel][0] != "0" else "1") + inv[hash_rel][1:]
    body.update(
        {
            "manifest_id": candidate_id,
            "parent_activation_id": V25_ACTIVATION_ID,
            "parent_activation_sha": parent["sha256"],
            "candidate_id": candidate_id,
            "candidate_status": CANDIDATE_STATUS_UNCERTIFIED,
            "formal_paper_allowed": False,
            "immutable": True,
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": inventory_digest(inv),
        }
    )
    body["sha256"] = manifest_content_sha(body)
    return body


def _write_bound(tmp_path: Path, body: dict) -> Path:
    man = tmp_path / f"{body['manifest_id']}.json"
    man.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sel = {
        "schema": "V1R_ACTIVE_ACTIVATION_SELECTOR_V1",
        "activation_id": body["manifest_id"],
        "activation_sha": body["sha256"],
        "manifest_relpath": str(man.resolve()).replace("\\", "/"),
        "note": "Identity-only selector; no Strategy/Precommit/trading fields.",
    }
    sp = tmp_path / "selector.json"
    sp.write_text(json.dumps(sel, indent=2) + "\n", encoding="utf-8")
    return sp


def _bind(monkeypatch: pytest.MonkeyPatch, selector: Path, *, cert: bool) -> None:
    monkeypatch.setenv(ENV_ACTIVATION_SELECTOR, str(selector))
    monkeypatch.delenv(ENV_OPVAL_MODE, raising=False)
    if cert:
        monkeypatch.setenv(ENV_CERT_MODE, "1")
    else:
        monkeypatch.delenv(ENV_CERT_MODE, raising=False)


def test_inventory_coverage_100_percent() -> None:
    cov = audit_runtime_inventory_coverage()
    assert cov["runtime_critical_uncovered_files"] == []
    assert cov["unexpected_runtime_critical_files"] == []
    assert cov["ok"] is True
    assert cov["v25_inventory_count"] == 44
    assert cov["v26_candidate_inventory_count"] == len(RUNTIME_DEPENDENCY_RELS)
    assert cov["v26_candidate_inventory_count"] != 44
    for rel in RUNTIME_CRITICAL_MUST_COVER:
        assert rel in RUNTIME_DEPENDENCY_RELS
        assert rel in cov["new_runtime_files_added"]
        assert rel in cov["new_runtime_files_covered"]


def test_v25_selector_and_manifest_immutable() -> None:
    sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    assert sel["activation_id"] == V25_ACTIVATION_ID
    assert sel["activation_sha"] == V25_SHA
    man = _v25()
    assert man["sha256"] == V25_SHA == manifest_content_sha(man)
    assert len(man["runtime_file_sha256"]) == 44


def test_no_certification_inventory_bypass() -> None:
    gate = (NATIVE / "src/small_paper/v1r_exit_v2_activation_gate.py").read_text(encoding="utf-8")
    bind = (NATIVE / "src/small_paper/v1r_activation_binding.py").read_text(encoding="utf-8")
    runner = (NATIVE / "src/small_paper/paper_trade_checked_runner.py").read_text(encoding="utf-8")
    for src in (gate, bind, runner):
        assert "skip_inventory" not in src
        assert "use_candidate_without_assertion" not in src
        assert "if CERT_MODE" not in src


def test_a_j_exact_candidate_certification_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    body = _make_candidate_body()
    sel = _write_bound(tmp_path, body)
    _bind(monkeypatch, sel, cert=True)
    r = assert_exit_v2_primary_roles()
    assert r.ok and r.ready, r.reason
    assert r.identity["activation_id"] == CID
    inv = r.identity["runtime_inventory"]
    assert inv["mismatch_n"] == 0
    assert inv["expected_n"] == len(RUNTIME_DEPENDENCY_RELS)
    assert inv["matched"] == inv["expected_n"]
    assert inv["uncovered_runtime_critical"] == []


def test_b_one_byte_hash_mismatch_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rel = "src/small_paper/paper_trade_checked_runner.py"
    body = _make_candidate_body(hash_rel=rel)
    sel = _write_bound(tmp_path, body)
    _bind(monkeypatch, sel, cert=True)
    r = assert_exit_v2_primary_roles()
    assert r.ok is False
    assert "runtime_inventory" in r.reason


def test_c_unknown_candidate_id_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sel = tmp_path / "selector.json"
    sel.write_text(
        json.dumps(
            {
                "schema": "V1R_ACTIVE_ACTIVATION_SELECTOR_V1",
                "activation_id": "NO_SUCH_CANDIDATE",
                "activation_sha": "a" * 64,
                "manifest_relpath": str((tmp_path / "NO_SUCH_CANDIDATE.json").resolve()).replace("\\", "/"),
            }
        ),
        encoding="utf-8",
    )
    _bind(monkeypatch, sel, cert=True)
    r = assert_exit_v2_primary_roles()
    assert r.ok is False
    assert "selector_or_manifest" in r.reason


def test_d_candidate_digest_mismatch_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    body = _make_candidate_body()
    sel = _write_bound(tmp_path, body)
    doc = json.loads(sel.read_text(encoding="utf-8"))
    doc["activation_sha"] = "b" * 64
    sel.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    _bind(monkeypatch, sel, cert=True)
    r = assert_exit_v2_primary_roles()
    assert r.ok is False
    assert "selector_activation_sha" in r.reason


def test_e_manifest_missing_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    body = _make_candidate_body()
    sel = _write_bound(tmp_path, body)
    (tmp_path / f"{body['manifest_id']}.json").unlink()
    _bind(monkeypatch, sel, cert=True)
    r = assert_exit_v2_primary_roles()
    assert r.ok is False
    assert "selector_or_manifest" in r.reason


def test_f_runtime_file_missing_from_manifest_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = _make_candidate_body(drop_rel="src/small_paper/ownership_classifier.py")
    sel = _write_bound(tmp_path, body)
    _bind(monkeypatch, sel, cert=True)
    r = assert_exit_v2_primary_roles()
    assert r.ok is False
    assert "runtime_inventory" in r.reason
    gen = verify_generator_inventory_coverage(body)
    assert gen["ok"] is False
    assert "src/small_paper/ownership_classifier.py" in gen["missing_from_manifest"]


def test_g_unexpected_runtime_critical_module_coverage_fail(tmp_path: Path) -> None:
    for rel in RUNTIME_CRITICAL_MUST_COVER:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# stub\n", encoding="utf-8")
    extra = tmp_path / "src/small_paper/runtime_lifecycle_extra.py"
    extra.write_text("x = 1\n", encoding="utf-8")
    v25_dir = tmp_path / "results/research/v1r_exit_v2_prospective_activation"
    v25_dir.mkdir(parents=True)
    v25_dir.joinpath(f"{V25_ACTIVATION_ID}.json").write_text(
        json.dumps({"runtime_file_sha256": {r: "0" * 64 for r in list(RUNTIME_DEPENDENCY_RELS)[:44]}}),
        encoding="utf-8",
    )
    cov = audit_runtime_inventory_coverage(native_root=tmp_path)
    assert cov["ok"] is False
    assert "src/small_paper/runtime_lifecycle_extra.py" in cov["unexpected_runtime_critical_files"]


def test_h_v25_identity_v26_source_runtime_inventory_fail() -> None:
    r = assert_exit_v2_primary_roles()
    assert r.ok is False
    assert "runtime_inventory" in r.reason
    man = _v25()
    inv = verify_runtime_inventory(man, native_root=NATIVE)
    gen = verify_generator_inventory_coverage(man)
    assert inv["ok"] is False or gen["ok"] is False
    assert gen["ok"] is False
    assert gen["generator_n"] == len(RUNTIME_DEPENDENCY_RELS)
    assert gen["manifest_n"] == 44


def test_i_normal_paper_uncertified_candidate_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = _make_candidate_body()
    sel = _write_bound(tmp_path, body)
    _bind(monkeypatch, sel, cert=False)
    r = assert_exit_v2_primary_roles()
    assert r.ok is False
    assert UNCERTIFIED_NOT_ALLOWED in r.reason
    assert uncertified_paper_blocked_reason(body, certification=False) == UNCERTIFIED_NOT_ALLOWED
    assert uncertified_paper_blocked_reason(body, certification=True) == ""


def test_env_selector_overrides_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    body = _make_candidate_body()
    sel = _write_bound(tmp_path, body)
    monkeypatch.setenv(ENV_ACTIVATION_SELECTOR, str(sel))
    loaded = load_active_selector()
    assert loaded["activation_id"] == CID
    default = load_active_selector(path=SELECTOR_PATH)
    assert default["activation_id"] == V25_ACTIVATION_ID


def test_same_resolver_no_cert_skip_in_assert_source() -> None:
    src = (NATIVE / "src/small_paper/v1r_exit_v2_activation_gate.py").read_text(encoding="utf-8")
    assert "verify_runtime_inventory(manifest" in src
    assert "uncertified_paper_blocked_reason" in src
    assert "certification_mode()" in src
    # Must still hash-check after UNCERTIFIED is allowed.
    i_block = src.find("uncertified_paper_blocked_reason")
    i_inv = src.find("verify_runtime_inventory")
    assert 0 < i_block < i_inv
