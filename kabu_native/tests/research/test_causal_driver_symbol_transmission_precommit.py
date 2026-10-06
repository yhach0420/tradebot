"""Transmission precommit structure tests. No symbol future-return beta."""
from __future__ import annotations

from math import ceil
from pathlib import Path

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.sector_state_transmission_precommit import (
    FAMILY_N,
    GLOBAL_TARGET_N,
    PARENT_PRECOMMIT_SHA256,
    PARENT_VERDICT,
    SECTOR3650_TARGET_N,
)
from research.causal_driver_pb1.sector_state_transmission_precommit.family import build_family
from research.causal_driver_pb1.sector_state_transmission_precommit.isolation import DISC_V2_OUT, OUT, PARENT_OUT, V13_OUT
from research.causal_driver_pb1.sector_state_transmission_precommit.parent import MECHANISMS, parent_mechanism_set_sha256
from research.causal_driver_pb1.sector_state_transmission_precommit.publish import SHEETS


def test_isolation_and_parent_binding():
    assert OUT.resolve() not in {PARENT_OUT.resolve(), V13_OUT.resolve(), DISC_V2_OUT.resolve()}
    assert PARENT_VERDICT.endswith("FOUND_V1")
    assert PARENT_PRECOMMIT_SHA256 == "83c2b92056941f5cae72d1123499c549e9e34ea3280d3ec4588e63a261509a80"
    assert len(MECHANISMS) == 4
    assert parent_mechanism_set_sha256()


def test_family_270_leave_target_out():
    symbols = [f"S{i:04d}" for i in range(105)]
    sector_of = {s: "3650" if i < 30 else "3200" for i, s in enumerate(symbols)}
    s3650 = symbols[:30]
    fam = build_family(symbols=symbols, sector_of=sector_of, sector3650_symbols=s3650)
    assert fam["family_n"] == FAMILY_N == 210 + 60
    assert fam["pass"]
    assert all(r["TARGET_SELF_IN_DRIVER"] is False for r in fam["rows"])
    assert sum(1 for r in fam["rows"] if r["global"]) == 2 * GLOBAL_TARGET_N
    assert sum(1 for r in fam["rows"] if not r["global"]) == 2 * SECTOR3650_TARGET_N
    assert {r["driver_n"] for r in fam["rows"] if r["global"]} == {104}
    assert {r["driver_n"] for r in fam["rows"] if not r["global"]} == {29}


def test_coverage_thresholds():
    assert max(5, ceil(0.10 * 105)) == 11
    assert max(5, ceil(0.10 * 30)) == 5


def test_sheets_and_no_beta_fit():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "causal_driver_pb1" / "sector_state_transmission_precommit"
    for name in ("feasibility.py", "family.py", "presence.py", "bootstrap.py", "contract.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "np.log" not in text
        assert "fit_frozen" not in text
        assert "point_b_fx" not in text
    for sheet in (
        "Manifest",
        "Parent_Mechanisms",
        "Leave_Target_Out",
        "Input_Feasibility",
        "Family270",
        "Bootstrap_Discovery",
        "Bootstrap_FV",
        "Coverage_Gates",
        "Safety",
    ):
        assert sheet in SHEETS
