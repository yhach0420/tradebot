"""Phase500 post-entry forward shadow tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase500_post_entry_shadow_review import (  # noqa: E402
    PostEntryShadowReview,
    _bootstrap_rows_from_phase499,
    compute_mandatory_answers,
)
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines  # noqa: E402
from small_paper.post_entry_forward_shadow import (  # noqa: E402
    compute_early_failure_shadow_score,
    compute_post_entry_checkpoints,
    enrich_exit_post_entry_shadow_fields,
)
from small_paper.post_entry_forward_shadow_auto import run_post_entry_forward_shadow_auto  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


def _ticks(entry_ts: float, entry_px: float, moves: list[tuple[float, float]]) -> list[dict]:
    out: list[dict] = []
    for offset, px in moves:
        out.append({"ts_epoch": entry_ts + offset, "price": px})
    return out


class TestPhase500PostEntryForwardShadow(unittest.TestCase):
    def test_checkpoint_flags_and_score(self) -> None:
        entry_ts = datetime(2026, 6, 16, 9, 0, 0, tzinfo=JST).timestamp()
        entry_px = 1000.0
        rich = _ticks(
            entry_ts,
            entry_px,
            [
                (10, 999.0),
                (30, 998.0),
                (60, 999.5),
                (120, 998.5),
                (180, 999.0),
            ],
        )
        cp = compute_post_entry_checkpoints(rich, entry_price=entry_px, entry_ts=entry_ts)
        self.assertTrue(cp["flag_e2_no_progress"])
        self.assertTrue(cp["flag_e3_stall"])
        self.assertTrue(cp["flag_e4_no_reclaim"])
        self.assertEqual(cp["early_failure_shadow_score"], 3)

    def test_enrich_exit_fields(self) -> None:
        entry_ts = datetime(2026, 6, 16, 9, 0, 0, tzinfo=JST).timestamp()
        rich = _ticks(entry_ts, 1000.0, [(30, 1005.0), (60, 1010.0), (120, 1008.0), (180, 1012.0)])
        out = enrich_exit_post_entry_shadow_fields(
            rich_ticks=rich,
            entry_price=1000.0,
            entry_ts=entry_ts,
        )
        self.assertIn("mfe_pct_60s", out)
        self.assertGreater(float(out["mfe_pct_60s"]), 0.5)
        self.assertEqual(compute_early_failure_shadow_score(flag_e2=False, flag_e3=False, flag_e4=False, high_update_count_180s=2), 0)

    def test_discord_research_shadow_lines(self) -> None:
        lines = format_research_shadow_daily_summary_lines(
            {
                "post_entry_forward_shadow": {
                    "score_ge3_count": 12,
                    "score_ge3_pnl": -3400.0,
                    "score_ge4_count": 4,
                    "score_ge4_pnl": -2100.0,
                    "forward_days_collected": 3,
                    "status": "success",
                }
            }
        )
        text = "\n".join(lines)
        self.assertIn("PostEntry Shadow:", text)
        self.assertIn("score>=3", text)

    def test_review_bootstrap_from_phase499(self) -> None:
        behavior = REPO / "results" / "reports" / "phase499_post_entry_behavior.csv"
        if not behavior.is_file():
            self.skipTest("phase499 behavior csv missing")
        rows = _bootstrap_rows_from_phase499(behavior)
        self.assertGreater(len(rows), 200)
        mandatory = compute_mandatory_answers(rows, forward_days=0, data_source="phase499_replay_bootstrap")
        self.assertGreater(int(mandatory["1_score_ge3_count"]), 0)
        self.assertFalse(mandatory["eval_ready"])

    def test_review_writes_summary(self) -> None:
        behavior = REPO / "results" / "reports" / "phase499_post_entry_behavior.csv"
        if not behavior.is_file():
            self.skipTest("phase499 behavior csv missing")
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            job = PostEntryShadowReview(repo_root=PARENT, reports_dir=reports)
            result = job.run()
            paths = job.write_outputs(result)
            self.assertTrue(paths["summary"].is_file())
            payload = json.loads(paths["summary"].read_text(encoding="utf-8"))
            self.assertEqual(payload["verdict"], "forward_shadow_started")

    def test_auto_never_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            block = run_post_entry_forward_shadow_auto(
                repo_root=PARENT,
                output_dir=Path(tmp),
            )
            self.assertIn(block["status"], ("success", "skipped", "warning"))


if __name__ == "__main__":
    unittest.main()
