"""DAYTRADE50 selector V2. Fixture G is not the user's capital."""
from __future__ import annotations

import inspect
from pathlib import Path

from research.daytrade50_capital_efficient_selector_v2.baselines import auto50_v1_score, load_current50_symbols
from research.daytrade50_capital_efficient_selector_v2.features import (
    SessionObservation,
    build_symbol_features,
    window_for_target,
)
from research.daytrade50_capital_efficient_selector_v2.policy import (
    AWAIT_USER_G,
    admit_candidate,
    admit_in_given_order,
    one_lot_notional,
    policy_document,
    policy_instance_sha,
    policy_spec_sha,
    release_position,
)
from research.daytrade50_capital_efficient_selector_v2.selector import select_top50
from research.daytrade50_capital_efficient_selector_v2.validate import (
    capital_efficiency_audit,
    combination_budget_fit_rate,
    evaluate_book,
)

FIXTURE_G = 1_000_000.0


def _obs(symbol: str, day: str, close: float, volume: float, span: float, *, market: str = "prime", invalid: str = "") -> SessionObservation:
    return SessionObservation(
        symbol=symbol,
        available_on=day,
        close=close,
        volume=volume,
        trading_value=close * volume,
        range_pct=span,
        exchange="1",
        market=market,
        invalid_reason=invalid,
    )


def _history(symbol: str, close: float, volume: float, span: float, n: int = 20, start: int = 1) -> list[SessionObservation]:
    return [_obs(symbol, f"2026{(start + i):04d}", close, volume, span) for i in range(n)]


def test_policy_has_one_capital_parameter_and_no_slot_split() -> None:
    document = policy_document()
    assert document["user_capital_parameter"] == "SYSTEM_GROSS_EXPOSURE_BUDGET_YEN"
    assert document["per_position_cap"] is None
    assert document["max_concurrent_meaning"] == "occupancy_count_cap"
    assert document["qty"] == 100
    assert document["runtime_applied"] is False
    source = inspect.getsource(admit_candidate)
    assert "/ 5" not in source and "/5" not in source


def test_one_name_near_g_and_several_names_share_g() -> None:
    first = admit_candidate(symbol="A", ask=8500, open_positions=[], gross_budget_yen=FIXTURE_G)
    assert first["admit"] is True
    assert first["qty"] == 100
    book = []
    for symbol, ask in (("A", 3000), ("B", 2500), ("C", 4000)):
        decision = admit_candidate(symbol=symbol, ask=ask, open_positions=book, gross_budget_yen=FIXTURE_G)
        assert decision["admit"] is True
        book = decision["open_positions"]
    assert sum(row["entry_notional"] for row in book) == 950_000
    second = admit_candidate(
        symbol="B",
        ask=5000,
        open_positions=[{"symbol": "A", "entry_notional": 600_000, "qty": 100}],
        gross_budget_yen=FIXTURE_G,
    )
    assert second["admit"] is False
    assert second["reason"] == "NOTIONAL_AGGREGATE_CAP"


def test_occupancy_is_separate_from_budget_and_release_restores_room() -> None:
    book = [{"symbol": str(i), "entry_notional": 10_000, "qty": 100} for i in range(5)]
    blocked = admit_candidate(symbol="Z", ask=100, open_positions=book, gross_budget_yen=FIXTURE_G)
    assert blocked["reason"] == "MAX_CONCURRENT"
    released = release_position(book, "0")
    opened = admit_candidate(symbol="Z", ask=100, open_positions=released, gross_budget_yen=FIXTURE_G)
    assert opened["admit"] is True
    assert opened["qty"] == 100


def test_gap_up_does_not_replace_membership() -> None:
    membership = ["7203", "6758"]
    decision = admit_candidate(symbol="7203", ask=20000, open_positions=[], gross_budget_yen=FIXTURE_G)
    assert decision["reason"] == "BUDGET_INELIGIBLE_AT_ENTRY"
    assert membership == ["7203", "6758"]


def test_given_order_is_kept_and_future_budget_is_not_reserved() -> None:
    decisions = admit_in_given_order(
        [
            {"symbol": "A", "ask": 6000},
            {"symbol": "B", "ask": 5000},
            {"event": "EXIT", "symbol": "A"},
            {"symbol": "C", "ask": 4000},
        ],
        gross_budget_yen=FIXTURE_G,
    )
    assert [row["reason"] for row in decisions if row["event"] == "ENTRY"] == [
        "ADMIT",
        "NOTIONAL_AGGREGATE_CAP",
        "ADMIT",
    ]
    assert decisions[1]["symbol"] == "B"


def test_unset_g_does_not_select_or_audit() -> None:
    rows = []
    for index in range(60):
        symbol = f"{1000 + index}"
        built = build_symbol_features(_history(symbol, 1000 + index, 1000, 2.0), selection_day="20260020")
        rows.append(built)
    result = select_top50(rows, gross_budget_yen=None)
    assert result["status"] == AWAIT_USER_G
    assert result["symbols"] == []
    assert capital_efficiency_audit([100.0], gross_budget_yen=None)["status"] == AWAIT_USER_G
    assert combination_budget_fit_rate([100.0, 100.0], gross_budget_yen=None)["status"] == AWAIT_USER_G


def test_quality_ignores_budget_share_and_does_not_prefer_cheap_names() -> None:
    cheap = build_symbol_features(_history("CHEAP", 500, 10_000, 3.0), selection_day="20260020")
    rich = build_symbol_features(_history("RICH", 2500, 2_000, 3.0), selection_day="20260020")
    assert cheap["liquidity_feature"] == rich["liquidity_feature"]
    assert cheap["range_pct_5d_median"] == rich["range_pct_5d_median"]
    low_g = select_top50([cheap, rich] + _fill(48, close=1000), gross_budget_yen=10_000_000)
    high_g = select_top50([cheap, rich] + _fill(48, close=1000), gross_budget_yen=50_000_000)
    assert low_g["status"] == "TOP50"
    cheap_low = next(row for row in low_g["rows"] if row["symbol"] == "CHEAP")
    rich_low = next(row for row in low_g["rows"] if row["symbol"] == "RICH")
    cheap_high = next(row for row in high_g["rows"] if row["symbol"] == "CHEAP")
    assert cheap_low["PRIMARY_QUALITY_SCORE"] == cheap_high["PRIMARY_QUALITY_SCORE"]
    assert cheap_low["PRIMARY_QUALITY_SCORE"] <= rich_low["PRIMARY_QUALITY_SCORE"]
    assert cheap_low["BUDGET_SHARE"] < rich_low["BUDGET_SHARE"]


def test_one_day_spike_does_not_outrank_persistent_names() -> None:
    spike = _history("SPIKE", 1000, 1_000, 1.0)
    spike[-1] = _obs("SPIKE", spike[-1].available_on, 1000, 1_000_000, 20.0)
    steady = _history("STEADY", 1000, 20_000, 4.0)
    rows = [build_symbol_features(spike, selection_day="20260020"), build_symbol_features(steady, selection_day="20260020")]
    rows.extend(_fill(48, close=1000, volume=5_000, span=2.0))
    selected = select_top50(rows, gross_budget_yen=10_000_000)
    ranks = {row["symbol"]: row["rank"] for row in selected["rows"]}
    assert ranks["STEADY"] < ranks["SPIKE"]


def test_future_session_and_missing_target_do_not_change_membership() -> None:
    history = _history("KEEP", 1000, 10_000, 3.0)
    leaked = history + [_obs("KEEP", "20260021", 1000, 10_000, 50.0)]
    clean = build_symbol_features(history, selection_day="20260020")
    ignored = build_symbol_features(leaked, selection_day="20260020")
    assert clean["range_pct_5d_median"] == ignored["range_pct_5d_median"]
    assert ignored["future_rows_ignored"] == 1
    book = [f"{2000 + i}" for i in range(50)]
    evaluated = evaluate_book(book, {book[0]: {"range_pct": 1.0, "trading_value": 1.0}})
    assert evaluated["symbols_unchanged"] == book
    assert evaluated["target_missing_n"] == 49
    assert evaluated["target_missing_label"] == "TARGET_MISSING"


def test_under_50_fails_closed_and_core_bucket_is_absent() -> None:
    rows = _fill(49, close=1000)
    result = select_top50(rows, gross_budget_yen=10_000_000)
    assert result["status"] == "FAIL_CLOSED_UNDER_50"
    assert result["symbols"] == []
    assert result["core10_used"] is False


def test_tie_uses_lower_budget_share_then_symbol() -> None:
    left = build_symbol_features(_history("2000", 1000, 10_000, 3.0), selection_day="20260020")
    right = build_symbol_features(_history("1000", 2000, 5_000, 3.0), selection_day="20260020")
    right["suitability_feature"] = left["suitability_feature"]
    right["tick_ratio"] = left["tick_ratio"]
    filler = _fill(48, close=1000, volume=1_000, span=1.0)
    selected = select_top50([left, right, *filler], gross_budget_yen=10_000_000)
    pair = [row for row in selected["rows"] if row["symbol"] in {"1000", "2000"}]
    assert pair[0]["score_rounded"] == pair[1]["score_rounded"]
    assert [row["symbol"] for row in pair] == ["2000", "1000"]


def test_budget_above_prior_close_is_not_an_alpha_reject() -> None:
    row = build_symbol_features(_history("HIGH", 20000, 1000, 3.0), selection_day="20260020")
    result = select_top50([row, *_fill(60, close=1000)], gross_budget_yen=FIXTURE_G)
    assert "HIGH" not in result["symbols"]
    assert result["rejected"]["budget"] == 1


def test_known_invalid_and_non_jpx_are_excluded_without_a_board_history() -> None:
    halted = build_symbol_features(_history("HALT", 1000, 10_000, 3.0), selection_day="20260020")
    halted_rows = _history("HALT", 1000, 10_000, 3.0)
    halted_rows[-1] = _obs("HALT", halted_rows[-1].available_on, 1000, 10_000, 3.0, invalid="halted")
    assert build_symbol_features(halted_rows, selection_day="20260020")["reject"] == "KNOWN_INVALID"
    growth = _history("GROW", 1000, 10_000, 3.0)
    assert build_symbol_features(growth, selection_day="20260020")["eligible"] is True
    assert halted["eligible"] is True


def test_preopen_second_last_bar_is_stale_and_missing_session_is_not_backfilled() -> None:
    from research.daytrade50_capital_efficient_selector_v2.lineage import (
        corrected_symbol_window,
        iloc_minus_two,
        lag_sessions,
        last_completed_session,
    )

    sessions = ["20261002", "20261005", "20261006"]
    assert iloc_minus_two(sessions) == "20261005"
    assert last_completed_session(sessions, "20261007") == "20261006"
    assert lag_sessions(sessions, "20261007", "20261005") == 1
    assert lag_sessions(sessions, "20261007", "20261006") == 0
    stale = [{"source_bar_date": "20261005", "close": 10, "high": 11, "low": 9, "volume": 1000}]
    blocked = corrected_symbol_window(stale, symbol="1301", target="20261007", sessions=sessions + ["20261006"])
    assert blocked["status"] == "LATEST_SESSION_DATA_MISSING"


def test_target_dated_artifact_is_excluded_and_spec_sha_has_no_instance() -> None:
    days = ["20261001", "20261002", "20261005", "20261007"]
    window = window_for_target(days, "20261007", lookback=20)
    assert "20261007" not in window
    assert window[-1] < "20261007"
    assert policy_spec_sha() == "5a395155b26970b37bbb5030987db49ea9af035d09549b73922e589ef6c53ebe"
    assert policy_instance_sha(None) is None
    assert "G" == policy_document()["max_aggregate_gross_yen"]


def test_baselines_stay_separate(tmp_path: Path) -> None:
    score = auto50_v1_score(2.0, 1_000_000)
    assert score is not None
    path = tmp_path / "frozen.json"
    path.write_text('{"canonical_symbols":["7203.T","6758.T"]}', encoding="utf-8")
    assert load_current50_symbols(path) == ["7203", "6758"]
    assert one_lot_notional(8500) == 850_000


def _fill(count: int, *, close: float = 1000, volume: float = 10_000, span: float = 2.0) -> list[dict]:
    rows = []
    for index in range(count):
        symbol = f"{3000 + index}"
        rows.append(build_symbol_features(_history(symbol, close, volume, span), selection_day="20260020"))
    return rows
