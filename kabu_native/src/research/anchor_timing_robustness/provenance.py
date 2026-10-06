"""CLOCK_GRID source of truth + freeze provenance. No guessed times."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from research.anchor_timing_robustness.grid import canonical_grid, hm_label, split_am_pm
from small_paper.v1r_primary_runtime import ANCHOR_SHA, CLOCK_GRID

NATIVE = Path(__file__).resolve().parents[3]
RUNTIME_REL = "src/small_paper/v1r_primary_runtime.py"
X32_REL = "src/research/e1_x32_upstream_attribution/__init__.py"
FREEZE_REL = "results/research/e1_x33b_neutral_anchor/NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json"


def file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(args: list[str], cwd: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _git_root() -> Path:
    for cand in (NATIVE, NATIVE.parent):
        if (cand / ".git").exists() or _git(["rev-parse", "--show-toplevel"], cand):
            return cand
    return NATIVE


def collect_provenance() -> dict[str, Any]:
    grid = canonical_grid()
    am, pm = split_am_pm(grid)
    src = NATIVE / RUNTIME_REL
    freeze = NATIVE / FREEZE_REL
    x32 = NATIVE / X32_REL
    freeze_body: dict[str, Any] = {}
    if freeze.is_file():
        freeze_body = json.loads(freeze.read_text(encoding="utf-8"))
    freeze_grid = freeze_body.get("clock_grid") or []
    freeze_tuples = tuple((int(a), int(b)) for a, b in freeze_grid) if freeze_grid else ()
    x32_grid = None
    x32_comment = ""
    try:
        from research.e1_x32_upstream_attribution import CLOCK_POINTS_HM, FORBIDDEN_FROM, HISTORICAL_DAYS

        x32_grid = tuple((int(h), int(m)) for h, m in CLOCK_POINTS_HM)
        x32_comment = "Precommitted common clock (JST HM) — fixed, not derived from outcomes"
        x32_forbidden = FORBIDDEN_FROM
        x32_hist = list(HISTORICAL_DAYS)
    except Exception as exc:
        CLOCK_POINTS_HM = ()  # noqa: N806
        x32_forbidden = "20260810"
        x32_hist = []
        x32_comment = f"import_failed:{exc}"

    root = _git_root()
    log_runtime = _git(
        ["log", "-n", "8", "--format=%h %ad %s", "--date=short", "--", RUNTIME_REL],
        NATIVE if (NATIVE / ".git").exists() else root,
    )
    log_x32 = _git(
        ["log", "-n", "8", "--format=%h %ad %s", "--date=short", "--", X32_REL],
        NATIVE if (NATIVE / ".git").exists() else root,
    )
    first_clock = _git(
        [
            "log",
            "-n",
            "3",
            "--reverse",
            "-S",
            "CLOCK_POINTS_HM",
            "--format=%h %ad %s",
            "--date=short",
            "--",
            X32_REL,
        ],
        NATIVE if (NATIVE / ".git").exists() else root,
    )
    pnl_search_hits = _git(
        [
            "log",
            "-n",
            "20",
            "--format=%h %s",
            "-S",
            "best offset",
            "--",
            "src/research/fixed_anchor_mechanism_audit_p3_0",
        ],
        NATIVE if (NATIVE / ".git").exists() else root,
    )

    matches_runtime = tuple(CLOCK_GRID) == grid
    matches_freeze = freeze_tuples == grid if freeze_tuples else False
    matches_x32 = x32_grid == grid if x32_grid else False
    holdout_ok = bool(matches_x32 and matches_freeze and x32_forbidden)

    return {
        "CANONICAL_AM_ANCHORS": am,
        "CANONICAL_PM_ANCHORS": pm,
        "CANONICAL_CLOCK_GRID": [hm_label(h, m) for h, m in grid],
        "SOURCE_FILE": RUNTIME_REL.replace("\\", "/"),
        "SOURCE_SHA": file_sha256(src),
        "ANCHOR_SHA": ANCHOR_SHA,
        "runtime_clock_equals_import": matches_runtime,
        "freeze_manifest": FREEZE_REL.replace("\\", "/"),
        "freeze_sha256": freeze_body.get("sha256"),
        "freeze_matches_runtime": matches_freeze,
        "x32_source": X32_REL.replace("\\", "/"),
        "x32_matches_runtime": matches_x32,
        "x32_precommitted_not_outcome_derived": x32_comment,
        "x32_historical_days": x32_hist,
        "x32_forbidden_from": x32_forbidden,
        "no_performance_search_at_freeze": True,
        "introduced_as": "E1_X32 CLOCK_POINTS_HM → E1_X33B NEUTRAL_FIXED_CLOCK_ANCHOR_V1 freeze → v1r_primary_runtime.CLOCK_GRID",
        "pnl_selected_clock_evidence": (
            "None in freeze contract. X32 documents the grid as precommitted, not derived from outcomes. "
            "P3-0 later observed that original common-support PnL beat ±1/3/5 minute shifts on FULL14; "
            "that study explicitly forbade adopting a best offset and CLOCK_GRID was not changed."
        ),
        "TRUE_HOLDOUT_AVAILABLE": bool(holdout_ok),
        "DEVELOPMENT_PERIOD": f"{x32_hist[0]}-{x32_hist[-1]}" if x32_hist else "",
        "POST_FREEZE_FROM": x32_forbidden,
        "git_log_runtime": log_runtime,
        "git_log_x32": log_x32,
        "git_first_CLOCK_POINTS_HM": first_clock,
        "git_p3_0_note": pnl_search_hits,
        "x32_file_sha": file_sha256(x32),
        "freeze_file_sha": file_sha256(freeze),
    }
