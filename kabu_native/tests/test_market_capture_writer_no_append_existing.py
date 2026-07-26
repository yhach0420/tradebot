"""Existing non-empty parts must open exclusive max+1 (no cross-session append)."""
from __future__ import annotations

from pathlib import Path

from small_paper.market_capture_writer import MarketCaptureWriter, list_push_part_indexes


def test_fresh_writer_skips_nonempty_parts(tmp_path: Path) -> None:
    old = tmp_path / "push_part_0002.jsonl"
    old.write_text('{"sequence":1}\n', encoding="utf-8")
    w = MarketCaptureWriter(output_dir=tmp_path, capture_session_id="mcs_test")
    try:
        idxs = list_push_part_indexes(tmp_path)
        assert max(idxs) >= 3
        assert w._part_idx >= 3
        # prior part untouched
        assert old.read_text(encoding="utf-8") == '{"sequence":1}\n'
        assert w._part_path is not None
        assert w._part_path.name == f"push_part_{w._part_idx:04d}.jsonl"
        assert w._part_path.stat().st_size == 0
    finally:
        if w._part_fh:
            w._part_fh.close()
