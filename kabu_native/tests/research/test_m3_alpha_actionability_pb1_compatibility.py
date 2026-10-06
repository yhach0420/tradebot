from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility.pb1_census import bucket
from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility.response import summarize


def test_clock_buckets_and_actionability_gate():
    assert bucket("09:09") == "09:00-09:10"
    assert bucket("09:10") == "09:10-10:00"
    assert bucket("11:25") == "11:00-11:25"
    assert bucket("11:26") == "after_11:25"
    rows = []
    for symbol, gross in (("6590", 20.0), ("6787", 20.0), ("6861", 4.0), ("6941", 4.0), ("6961", 20.0)):
        rows.append({
            "date": "20241001",
            "symbol": symbol,
            "gross_bps": gross,
            "adjusted_bps": gross - 8.0,
            "fold": "EARLY",
        })
    for symbol in ("6590", "6787", "6861", "6941", "6961"):
        rows.append({
            "date": "20260106",
            "symbol": symbol,
            "gross_bps": 12.0,
            "adjusted_bps": 4.0,
            "fold": "MIDDLE",
        })
    out = summarize(rows)
    assert out["gates"]["A1_adjusted_mean_gt_0"] is True
    assert out["gates"]["A3_two_folds_positive_mean"] is True
    assert out["gates"]["A4_no_single_target_supplies_all_positive_edge"] is True
    concentrated = [
        {"date": "20241001", "symbol": "6590", "gross_bps": 30.0, "adjusted_bps": 22.0, "fold": "EARLY"},
        {"date": "20241001", "symbol": "6787", "gross_bps": -10.0, "adjusted_bps": -18.0, "fold": "EARLY"},
    ]
    assert summarize(concentrated)["single_target_supplies_all_positive_edge"] is True
