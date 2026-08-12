"""V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from small_paper.market_ingress_spawn import spawn_ingress_process
from small_paper.v1r_pbv2_duplicate_runtime import (
    VERDICT,
    audit_duplicate_runtime,
    ingress_meta_consistency,
)


def test_spawn_rejects_when_live_ingress_exists(tmp_path: Path):
    live = [{"pid": 111, "kind": "ingress", "cmdline": "market_ingress_service --trading-date 20260812"}]
    with patch("small_paper.v1r_pbv2_duplicate_runtime.list_live_ingress", return_value=live):
        meta = spawn_ingress_process(
            native_root=tmp_path,
            trading_date="20260812",
            synthetic=False,
        )
    assert meta.get("rejected") is True
    assert meta.get("reason") == VERDICT
    assert meta.get("pid") == 0
    assert not (tmp_path / "data" / "market_capture" / "20260812" / "ingress.pid").exists()


def test_spawn_allows_synthetic_even_if_live(tmp_path: Path):
    live = [{"pid": 111, "kind": "ingress", "cmdline": "x"}]
    with patch("small_paper.v1r_pbv2_duplicate_runtime.list_live_ingress", return_value=live), patch(
        "subprocess.Popen"
    ) as popen:
        popen.return_value.pid = 999
        meta = spawn_ingress_process(
            native_root=tmp_path,
            trading_date="20990101",
            synthetic=True,
        )
    assert meta.get("rejected") is not True
    assert meta.get("pid") == 999


def test_ingress_meta_mismatch(tmp_path: Path):
    day = tmp_path / "data" / "market_capture" / "20260812"
    day.mkdir(parents=True)
    (day / "ingress.pid").write_text("13912\n", encoding="utf-8")
    (day / "ingress_spawn.json").write_text('{"pid": 13912}\n', encoding="utf-8")
    (day / "ingress_status.json").write_text(
        '{"pid": 28400, "state": "RUNNING"}\n', encoding="utf-8"
    )
    (day / "session_ing_20260812_13912_x").mkdir()
    (day / "session_ing_20260812_28400_y").mkdir()
    with patch("small_paper.v1r_pbv2_duplicate_runtime.query_process", return_value={"exists": False}):
        meta = ingress_meta_consistency(tmp_path, "20260812")
    assert meta["pid_file_status_mismatch"] is True
    assert meta["session_dir_count"] == 2


def test_audit_flags_contamination_on_meta(tmp_path: Path):
    day = tmp_path / "data" / "market_capture" / "20260812"
    day.mkdir(parents=True)
    (day / "ingress.pid").write_text("1\n", encoding="utf-8")
    (day / "ingress_status.json").write_text('{"pid": 2, "state": "RUNNING"}\n', encoding="utf-8")
    (day / "session_a").mkdir()
    # name must match session_ing_*
    (day / "session_ing_a").mkdir()
    (day / "session_ing_b").mkdir()
    with patch("small_paper.v1r_pbv2_duplicate_runtime.list_live_ingress", return_value=[]), patch(
        "small_paper.v1r_pbv2_duplicate_runtime.list_live_pilots", return_value=[]
    ), patch(
        "small_paper.v1r_pbv2_duplicate_runtime.query_process", return_value={"exists": False}
    ):
        audit = audit_duplicate_runtime(native_root=tmp_path, trading_date="20260812")
    assert audit["contaminated"] is True
    assert audit["verdict"] == VERDICT
    assert audit["classes"]["ingress_pid_meta_mismatch"] is True
