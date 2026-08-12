"""20260812 must not count as Valid Prospective Day."""
from __future__ import annotations

from small_paper.v1r_prospective_day_gate import (
    assert_not_counted_as_valid,
    is_valid_prospective_day,
    load_prospective_day_status,
)


def test_20260812_not_valid_prospective_day():
    assert is_valid_prospective_day("20260812") is False
    st = load_prospective_day_status("20260812")
    assert st is not None
    assert st["count_as_valid_prospective_day"] is False
    assert st["count_as_valid_day"] is False
    assert st["prospective_day_number"] is None or st.get("prospective_day_number") is None
    reasons = st.get("reasons") or [
        r.get("verdict") for r in (st.get("invalidating_verdicts") or [])
    ]
    joined = " ".join(str(x) for x in reasons)
    assert "PRIMARY_CONTAMINATION" in joined or "V1R_PBV2_PRIMARY_CONTAMINATION" in joined
    out = assert_not_counted_as_valid("20260812")
    assert out["assert_not_counted"] is True
