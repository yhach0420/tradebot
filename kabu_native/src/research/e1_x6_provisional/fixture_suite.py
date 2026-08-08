"""Fixture contract suite for E1_X6 research builder (embeddable in report.tests).

report.tests count MUST equal pytest pass count for
tests/test_e1_x6_research_builder_contracts.py when both are green.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Optional

from research.e1_x6_provisional.util import sha256_file


def _row(
    name: str,
    *,
    expected: Any,
    actual: Any,
    ok: bool,
    code_sha: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "test_name": name,
        "name": name,
        "expected": expected,
        "actual": actual,
        "result": "PASS" if ok else "FAIL",
        "PASS_FAIL": "PASS" if ok else "FAIL",
        "code_sha": code_sha,
    }


def _module_sha(rel: str) -> Optional[str]:
    from research.e1_x6_provisional.util import repo_root

    p = repo_root() / rel
    return sha256_file(p) if p.is_file() else None


def _run_check(name: str, fn: Callable[[], tuple[Any, Any]]) -> dict[str, Any]:
    try:
        expected, actual = fn()
        ok = expected == actual if not isinstance(expected, float) else abs(float(expected) - float(actual)) < 1e-9
        # allow callables that return (expected, actual, ok)
        if isinstance(expected, tuple) and len(expected) == 0:
            pass
        return _row(name, expected=expected, actual=actual, ok=bool(ok))
    except AssertionError as e:
        return _row(name, expected="PASS", actual=f"FAIL:{e}", ok=False)
    except Exception as e:
        return _row(name, expected="PASS", actual=f"ERROR:{type(e).__name__}:{e}", ok=False)


def fixture_contract_suite() -> list[dict[str, Any]]:
    """Execute all fixture contract checks; return one row per test.

    Prefer invoking pytest collection/run so count matches pytest pass count.
    Falls back to inline checks if pytest is unavailable.
    Recursion-safe: nested calls use inline suite (avoids pytest re-entry).
    """
    global _SUITE_RUNNING
    if _SUITE_RUNNING:
        return _run_inline_suite()
    _SUITE_RUNNING = True
    try:
        rows = _run_via_pytest()
        if rows is not None:
            return rows
        return _run_inline_suite()
    finally:
        _SUITE_RUNNING = False


_SUITE_RUNNING = False


def _run_via_pytest() -> Optional[list[dict[str, Any]]]:
    try:
        import pytest
    except ImportError:
        return None

    from research.e1_x6_provisional.util import native_root

    test_path = native_root() / "tests" / "test_e1_x6_research_builder_contracts.py"
    if not test_path.is_file():
        return None

    results: list[dict[str, Any]] = []

    class _Collector:
        def pytest_runtest_logreport(self, report):  # noqa: N802
            if report.when != "call":
                return
            name = report.nodeid.split("::")[-1]
            ok = report.passed
            results.append(
                _row(
                    name,
                    expected="PASS",
                    actual="PASS" if ok else (str(report.longrepr)[:500] if report.failed else "SKIP"),
                    ok=ok,
                    code_sha=_module_sha(
                        "kabu_native/src/research/e1_x6_provisional/canonical_partition_replay.py"
                    ),
                )
            )

    # -q --tb=no to keep quiet; disable cacheprovider noise
    rc = pytest.main(
        ["-q", "--tb=line", str(test_path), "-p", "no:cacheprovider"],
        plugins=[_Collector()],
    )
    if not results:
        return None
    # Annotate suite meta
    for r in results:
        r["pytest_rc"] = int(rc)
    return results


def _run_inline_suite() -> list[dict[str, Any]]:
    """Fallback inline checks (subset) when pytest cannot run."""
    from research.e1_x6_provisional.analysis_mask import mask_contract_fixture_rows
    from research.e1_x6_provisional.canonical_partition_replay import (
        fixed_spec_day_deletion_from_ledger,
    )
    from research.e1_x6_provisional.constants import (
        SUPERSEDED_FINAL_AUDIT_RUN,
        SUPERSEDED_REPLAY_BOUNDARY_RUN,
    )
    from research.e1_x6_provisional.cost_contract import ROUNDTRIP_COST_BPS
    from research.e1_x6_provisional.pipeline import SUPERSEDED_RUNS
    from research.e1_x6_provisional.replay_lifecycle_contract import (
        EVALUATION_MODE_REQUIRED,
        REPLAY_LIFECYCLE_CONTRACT_TEXT,
    )

    rows: list[dict[str, Any]] = []
    for m in mask_contract_fixture_rows():
        rows.append(
            _row(
                m["test_name"],
                expected="PASS",
                actual=m.get("result"),
                ok=m.get("result") == "PASS",
            )
        )

    rows.append(
        _row(
            "cost_bps_once_5",
            expected=5.0,
            actual=ROUNDTRIP_COST_BPS,
            ok=ROUNDTRIP_COST_BPS == 5.0,
        )
    )
    rows.append(
        _row(
            "evaluation_mode_full_canonical",
            expected="FULL_CANONICAL_EVENT_REPLAY",
            actual=EVALUATION_MODE_REQUIRED,
            ok=EVALUATION_MODE_REQUIRED == "FULL_CANONICAL_EVENT_REPLAY",
        )
    )
    rows.append(
        _row(
            "lifecycle_contract_nonempty",
            expected=True,
            actual=bool(REPLAY_LIFECYCLE_CONTRACT_TEXT.strip()),
            ok=bool(REPLAY_LIFECYCLE_CONTRACT_TEXT.strip()),
        )
    )
    rows.append(
        _row(
            "superseded_replay_boundary_recorded",
            expected="e1x6_final_20260801_024352_97202b28",
            actual=SUPERSEDED_REPLAY_BOUNDARY_RUN["run_id"],
            ok=SUPERSEDED_REPLAY_BOUNDARY_RUN in SUPERSEDED_RUNS,
        )
    )
    rows.append(
        _row(
            "superseded_final_audit_recorded",
            expected="e1x6_final_20260801_071154_331521a3",
            actual=SUPERSEDED_FINAL_AUDIT_RUN["run_id"],
            ok=SUPERSEDED_FINAL_AUDIT_RUN in SUPERSEDED_RUNS,
        )
    )

    trades = [
        {"day": "20260721", "net_pnl_yen_100": 100.0},
        {"day": "20260722", "net_pnl_yen_100": -40.0},
        {"day": "20260721", "net_pnl_yen_100": 10.0},
    ]
    fs = fixed_spec_day_deletion_from_ledger(trades, held_out_day="20260722")
    rows.append(
        _row(
            "fixed_spec_additivity",
            expected=True,
            actual=fs["additivity_ok"],
            ok=fs["additivity_ok"] and fs["n_all"] == fs["n_day"] + fs["n_without"],
        )
    )
    return rows


def pytest_pass_count_matches_suite(suite_rows: list[dict[str, Any]], pytest_passed: int) -> bool:
    return len(suite_rows) == int(pytest_passed) and all(
        r.get("result") == "PASS" or r.get("PASS_FAIL") == "PASS" for r in suite_rows
    )
