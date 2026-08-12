"""Activation binding hash SoT: freeze bytes == startup verification bytes (LF and CRLF)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from small_paper.v1r_activation_binding import (
    FORBIDDEN_INVENTORY_NAMES,
    SELECTOR_NAME,
    collect_runtime_inventory,
    file_sha256,
    load_active_selector,
    manifest_content_sha,
    verify_runtime_inventory,
)


def test_file_sha256_lf_and_crlf_roundtrip(tmp_path: Path) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    body_lf = b"print('x')\nprint('y')\n"
    body_crlf = b"print('x')\r\nprint('y')\r\n"
    lf.write_bytes(body_lf)
    crlf.write_bytes(body_crlf)
    # Different EOLs => different SoT hashes (no silent normalize)
    assert file_sha256(lf) != file_sha256(crlf)
    # Freeze == startup: re-read same path yields same hash
    assert file_sha256(lf) == file_sha256(lf)
    assert file_sha256(crlf) == file_sha256(crlf)
    # Simulates freeze then verify on unchanged bytes
    frozen_lf = file_sha256(lf)
    frozen_crlf = file_sha256(crlf)
    assert file_sha256(lf) == frozen_lf
    assert file_sha256(crlf) == frozen_crlf


def test_selector_rejects_economic_fields(tmp_path: Path) -> None:
    p = tmp_path / SELECTOR_NAME
    p.write_text(
        json.dumps(
            {
                "activation_id": "X",
                "activation_sha": "a" * 64,
                "strategy_sha": "nope",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="economic"):
        load_active_selector(path=p)


def test_inventory_forbids_selector_name() -> None:
    assert SELECTOR_NAME in FORBIDDEN_INVENTORY_NAMES
    with pytest.raises(RuntimeError, match="selector"):
        collect_runtime_inventory(rels=(f"results/research/x/{SELECTOR_NAME}",))


def test_manifest_content_sha_excludes_sha256_field() -> None:
    a = {"manifest_id": "M", "x": 1, "sha256": "dead"}
    b = {"manifest_id": "M", "x": 1, "sha256": "beef"}
    assert manifest_content_sha(a) == manifest_content_sha(b)


def test_verify_inventory_mismatch(tmp_path: Path) -> None:
    f = tmp_path / "src" / "a.py"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"abc\n")
    man = {"runtime_file_sha256": {"src/a.py": "0" * 64}}
    r = verify_runtime_inventory(man, native_root=tmp_path)
    assert r["ok"] is False
    assert r["mismatches"]
