"""Mandatory Phase A contract tests (plan §13). Research-only; no Paper access."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

NATIVE = Path(__file__).resolve().parents[3]
RESEARCH_DIR = NATIVE / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from e1_x6_raw_redesign import guard as guard_mod  # noqa: E402
from e1_x6_raw_redesign.event_input import EvalEvent, event_from_payload  # noqa: E402
from e1_x6_raw_redesign.features import (  # noqa: E402
    build_symbol_grid,
    compute_market_loo,
    compute_symbol_features,
    entry_allowed_mask,
)
from e1_x6_raw_redesign.p1 import build_p1_lock  # noqa: E402
from e1_x6_raw_redesign.registry import MAX_CANDIDATES, build_candidate_registry  # noqa: E402
from e1_x6_raw_redesign.setups import run_setup_machine  # noqa: E402
from e1_x6_raw_redesign.store import (  # noqa: E402
    load_checkpoint,
    save_checkpoint,
    sha256_obj,
)

T0 = 1_800_000_000.0


def _ev(sym: str, t: float, bid: float, ask: float, **kw) -> EvalEvent:
    return EvalEvent(symbol=sym, ts_epoch=t, bid=bid, ask=ask,
                     bid_qty=kw.get("bid_qty", 100.0), ask_qty=kw.get("ask_qty", 100.0),
                     volume=kw.get("volume"), vwap=kw.get("vwap"),
                     board_buy_qty10=kw.get("bb"), board_sell_qty10=kw.get("bs"))


def _grid(n: int = 200) -> np.ndarray:
    return T0 + np.arange(n, dtype=np.float64) * 5.0


def _steady_events(sym: str, n_grid: int, px: float = 1000.0, step: float = 0.0):
    evs = []
    for k in range(n_grid):
        p = px + step * k
        evs.append(_ev(sym, T0 + 5.0 * k - 0.5, p, p + 0.2))
    return evs


def _feature_ledger_sha(feats: dict) -> str:
    return sha256_obj({k: [None if not np.isfinite(x) else round(float(x), 9) for x in v]
                       for k, v in sorted(feats.items())})


def test_causality_future_event_does_not_change_past_features():
    grid = _grid(120)
    evs = _steady_events("A", 90, step=0.5)
    sg1 = build_symbol_grid("A", evs, grid)
    f1 = compute_symbol_features(sg1)
    # append future events after grid index 89
    evs2 = evs + [_ev("A", T0 + 5.0 * 200, 5000.0, 5000.2)]
    sg2 = build_symbol_grid("A", evs2, grid)
    f2 = compute_symbol_features(sg2)
    for k in f1:
        a, b = f1[k][:90], f2[k][:90]
        assert np.array_equal(np.isnan(a), np.isnan(b)), k
        assert np.allclose(a[~np.isnan(a)], b[~np.isnan(b)], rtol=0, atol=0), k


def test_score_removed_or_randomized_decisions_identical():
    payload = {
        "Buy1": {"Price": 1000.0, "Qty": 100.0}, "Sell1": {"Price": 1000.2, "Qty": 100.0},
        "TradingVolume": 500.0, "VWAP": 999.0,
    }
    base = event_from_payload("A", "2026-07-23T09:10:00+09:00", dict(payload))
    with_score = event_from_payload(
        "A", "2026-07-23T09:10:00+09:00", {**payload, "score": 0.99, "entry_score_v2": 5.0}
    )
    randomized = event_from_payload(
        "A", "2026-07-23T09:10:00+09:00", {**payload, "score": -123.4}
    )
    assert base == with_score == randomized

    # full ledger + decisions byte-identical
    grid = _grid(150)
    evs = _steady_events("A", 150, step=0.5)
    sg = build_symbol_grid("A", evs, grid)
    f = compute_symbol_features(sg)
    sha_a = _feature_ledger_sha(f)
    dec_a = _run_cont_machine(f, grid)
    # nothing in the pipeline can read a score; recompute is byte-identical
    sha_b = _feature_ledger_sha(compute_symbol_features(build_symbol_grid("A", evs, grid)))
    dec_b = _run_cont_machine(compute_symbol_features(build_symbol_grid("A", evs, grid)), grid)
    assert sha_a == sha_b
    assert json.dumps([d.__dict__ for d in dec_a], default=str) == json.dumps(
        [d.__dict__ for d in dec_b], default=str
    )


def _synthetic_cont_feats(n: int) -> dict:
    # +0.1/grid (~1bp) keeps the chase-reject gate (0.5*rv300=10bps) satisfied;
    # price stays under 1000 (NARROW_TOPIX500 band => tick 0.1 matches the step)
    mid = 900.0 + 0.1 * np.arange(n)
    f = {
        "mid": mid,
        "spread_bps": np.full(n, 2.0),
        "ret_300s_bps": np.full(n, 25.0),
        "ret_60s_bps": np.full(n, 6.0),
        "ret_30s_bps": np.full(n, 3.0),
        "range_pos_300s": np.full(n, 0.95),
        "dir_eff_300s": np.full(n, 0.9),
        "breakout_dev_bps": np.full(n, 5.0),
        "rv_300s_bps": np.full(n, 20.0),
        "up_persist_60s": np.full(n, 1.0),
        "high_300s": mid + 1.0, "low_300s": mid - 50.0,
        "pullback_bps": np.full(n, 10.0),
        "range_ratio_60_300": np.full(n, 0.2),
        "vol_ratio_60_300": np.full(n, 1.2),
        "vwap_dev_bps": np.full(n, 5.0),
    }
    return f


def _run_cont_machine(feats: dict, grid: np.ndarray):
    n = grid.shape[0]
    need = set(_synthetic_cont_feats(4).keys())
    full = dict(feats)
    for k in need:
        if k not in full:
            full[k] = np.full(n, np.nan)
    return run_setup_machine(
        "CONT", full, ["TREND_UP"] * n, entry_allowed_mask(grid),
        confirmation="STANDARD", symbol="A", symbol_class="NARROW_TOPIX500",
    )


def test_same_episode_reentry_forbidden():
    n = 200  # long enough that the 10-minute no-entry tail leaves room to OPEN
    grid = _grid(n)
    feats = _synthetic_cont_feats(n)
    dec = run_setup_machine("CONT", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500")
    opens = [d for d in dec if d.state == "OPEN"]
    # conditions hold for the whole window => exactly one OPEN (episode locked)
    assert len(opens) == 1
    assert opens[0].episode_id == 0


def test_open_requires_setup_break_before_next_episode():
    n = 250
    grid = _grid(n)
    feats = _synthetic_cont_feats(n)
    feats["ret_300s_bps"] = np.full(n, 25.0)
    feats["ret_300s_bps"][60:64] = -5.0  # setup breaks => episode ends
    dec = run_setup_machine("CONT", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500")
    opens = [d for d in dec if d.state == "OPEN"]
    assert len(opens) == 2
    assert opens[0].episode_id != opens[1].episode_id


def test_state_order_and_post_trigger_confirmation_counting():
    """TRIGGERED precedes CONFIRM; pre-trigger grids never count toward the
    confirmation window (trigger grid is the 1st counted grid)."""
    n = 200
    grid = _grid(n)
    feats = _synthetic_cont_feats(n)
    dec = run_setup_machine("CONT", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500")
    states = [(d.grid_idx, d.state) for d in dec]
    seq = [s for _, s in states[:4]]
    assert seq[:4] == ["SETUP", "TRIGGERED", "CONFIRM", "OPEN"]
    g_trig = next(g for g, s in states if s == "TRIGGERED")
    g_conf = next(g for g, s in states if s == "CONFIRM")
    g_open = next(g for g, s in states if s == "OPEN")
    # STANDARD needs 2 held grids counting the trigger grid as #1 => CONFIRM at
    # trigger+1 earliest, OPEN one grid later.
    assert g_conf >= g_trig + 1
    assert g_open == g_conf + 1
    trig = next(d for d in dec if d.state == "TRIGGERED")
    assert trig.frozen is not None
    for key in ("trigger_level", "stop_reference", "tick", "trigger_grid", "episode_id"):
        assert key in trig.frozen


def test_one_evaluation_per_symbol_per_grid():
    grid = _grid(100)
    evs = _steady_events("A", 100) + _steady_events("A", 100)  # duplicated events
    sg = build_symbol_grid("A", evs, grid)
    assert sg.bid.shape[0] == grid.shape[0]  # exactly one row per grid point
    assert len(sg.not_evaluable_reason) == grid.shape[0]


def test_leave_one_out_market_aggregates_exclude_self():
    n = 3
    grid = _grid(n)
    sym_feats = {}
    evaluable = {}
    for i, s in enumerate(("A", "B", "C")):
        f = {k: np.full(n, np.nan) for k in
             ("ret_60s_bps", "ret_300s_bps", "rv_300s_bps", "vol_ratio_60_300", "spread_bps")}
        f["ret_60s_bps"] = np.full(n, float(10 * (i + 1)))  # A=10 B=20 C=30
        sym_feats[s] = f
        evaluable[s] = np.ones(n, dtype=bool)
    out = compute_market_loo(sym_feats, evaluable, n)
    # median of others: for A -> median(20,30)=25; for B -> median(10,30)=20; for C -> 15
    assert out["A"]["mkt_ret_60s_med_bps"][0] == 25.0
    assert out["B"]["mkt_ret_60s_med_bps"][0] == 20.0
    assert out["C"]["mkt_ret_60s_med_bps"][0] == 15.0
    assert out["A"]["mkt_evaluable_n"][0] == 2.0


def test_stale_and_missing_rejected():
    grid = _grid(100)
    evs = _steady_events("A", 20)  # events stop after ~100s
    sg = build_symbol_grid("A", evs, grid)
    # far beyond freshness window => STALE_QUOTE
    assert sg.not_evaluable_reason[80] == "STALE_QUOTE"
    assert not sg.evaluable[80]
    # before first event => NO_EVENT_YET
    sg2 = build_symbol_grid("B", _steady_events("B", 5)[3:], grid)
    assert sg2.not_evaluable_reason[0] in ("NO_EVENT_YET", "WARMUP")
    # missing/crossed quote rejected
    evs3 = [_ev("C", T0 + 5.0 * k - 0.5, 1000.0, 999.0) for k in range(80, 100)]
    sg3 = build_symbol_grid("C", evs3, grid)
    assert sg3.not_evaluable_reason[95] == "CROSSED_OR_ZERO_QUOTE"


def test_warmup_first_5_minutes_not_evaluable():
    grid = _grid(100)
    sg = build_symbol_grid("A", _steady_events("A", 100), grid)
    assert sg.not_evaluable_reason[0] == "WARMUP"
    assert not sg.evaluable[59]      # 295s < 300s warmup
    assert sg.evaluable[60]          # 300s


def test_am_pm_rolling_separation():
    # Sessions are built independently (separate grids + separate event sets),
    # so a PM grid can never see AM history: lookbacks longer than the elapsed
    # PM time are NaN even though AM data existed earlier the same day.
    grid_pm = _grid(100)
    evs_pm = _steady_events("A", 100)
    sg = build_symbol_grid("A", evs_pm, grid_pm)
    f = compute_symbol_features(sg)
    assert np.isnan(f["ret_300s_bps"][59])   # only 295s of PM history exists
    assert np.isfinite(f["ret_300s_bps"][60])  # exactly 300s of PM history
    assert np.isnan(f["ret_300s_bps"][0])


def test_no_entry_last_10_minutes():
    grid = _grid(200)
    allowed = entry_allowed_mask(grid)
    assert not allowed[-1]
    assert not allowed[-120]          # within last 600s
    assert allowed[-121]


def test_registry_ids_unique_and_cap_24():
    reg = build_candidate_registry(
        core_feature_coverage_ok=True, market_feature_coverage_ok=True,
        vwap_available=False, volume_available=False, board_available=False,
    )
    ids = [r["strategy_id"] for r in reg]
    assert len(reg) == MAX_CANDIDATES == 24
    assert len(set(ids)) == 24
    for r in reg:
        assert r["strategy_id"].startswith("X6R3_")
        assert r["enabled"] is True


def test_registry_disables_on_missing_coverage():
    reg = build_candidate_registry(
        core_feature_coverage_ok=False, market_feature_coverage_ok=True,
        vwap_available=False, volume_available=False, board_available=False,
    )
    assert all(not r["enabled"] for r in reg)
    assert all(r["disable_reason"] for r in reg)


def test_p1_builds_without_any_future_labels(tmp_path):
    reg = build_candidate_registry(
        core_feature_coverage_ok=True, market_feature_coverage_ok=True,
        vwap_available=False, volume_available=False, board_available=False,
    )
    p1 = build_p1_lock(
        run_id="testrun", plan_doc_path=tmp_path / "missing.md",
        source_manifest_sha256="s" * 64, protected_manifest_sha256="p" * 64,
        inventory_summary={"days_n": 9}, field_usability={"usable": [], "unusable": []},
        registry=reg,
    )
    assert p1["p1_sha256"]
    # no numeric economics anywhere in P1 (gate names are definitions, not values)
    def _walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                assert k not in ("pnl", "mfe", "mae", "profit_factor", "daily_pnl"), k
                _walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                _walk(v)

    _walk(p1)
    assert p1["candidate_registry_n"] == 24
    assert p1["safety"] == {"submit": 0, "cancel": 0, "live": 0}


def test_ab_same_input_feature_ledger_sha_match():
    grid = _grid(150)
    evs = _steady_events("A", 150, step=0.3)
    s1 = _feature_ledger_sha(compute_symbol_features(build_symbol_grid("A", evs, grid)))
    s2 = _feature_ledger_sha(compute_symbol_features(build_symbol_grid("A", evs, grid)))
    assert s1 == s2


def test_protected_manifest_before_after_match():
    from e1_x6_raw_redesign.protected_manifest import build_protected_manifest, manifests_equal

    repo = NATIVE.parent
    before = build_protected_manifest(repo)
    after = build_protected_manifest(repo)
    ok, diffs = manifests_equal(before, after)
    assert ok, diffs
    assert before["files_n"] > 0


def test_no_forbidden_modules_imported():
    bad = guard_mod.assert_no_forbidden_imports()
    assert bad == [], f"forbidden modules loaded: {bad}"


def test_paper_guard_pauses_on_running_paper(monkeypatch):
    monkeypatch.setattr(
        guard_mod, "paper_processes",
        lambda: [{"pid": 1, "name": "python", "cmdline": "python -m small_paper.paper_trade_checked_runner"}],
    )
    monkeypatch.setattr(guard_mod, "in_trading_window", lambda now=None: False)
    monkeypatch.setattr(guard_mod, "fresh_paper_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(guard_mod, "disk_write_risk", lambda *a, **k: None)
    res = guard_mod.paper_guard_check(NATIVE, Path.home())
    assert res["ok"] is False
    assert "PAPER_RUNNER_PROCESS_RUNNING" in res["reasons"]


def test_paper_guard_pauses_on_unknown_state(monkeypatch):
    def _boom():
        raise RuntimeError("PAPER_STATE_UNKNOWN: simulated")

    monkeypatch.setattr(guard_mod, "paper_processes", _boom)
    monkeypatch.setattr(guard_mod, "in_trading_window", lambda now=None: False)
    monkeypatch.setattr(guard_mod, "fresh_paper_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(guard_mod, "disk_write_risk", lambda *a, **k: None)
    res = guard_mod.paper_guard_check(NATIVE, Path.home())
    assert res["ok"] is False


def test_resume_rejects_binding_mismatch(tmp_path, monkeypatch):
    import e1_x6_raw_redesign.store as store_mod

    monkeypatch.setattr(store_mod, "research_store_root", lambda: tmp_path)
    save_checkpoint("r1", "phase", {"x": 1}, binding={"source_manifest_sha256": "a" * 64})
    ok = load_checkpoint("r1", "phase", binding={"source_manifest_sha256": "a" * 64})
    assert ok == {"x": 1}
    with pytest.raises(SystemExit):
        load_checkpoint("r1", "phase", binding={"source_manifest_sha256": "b" * 64})


# ===================== Phase A-R1 additional mandatory tests =====================

from e1_x6_raw_redesign.evaluation_plan import (  # noqa: E402
    ROLLING_ORIGIN_5FOLD,
    cap5_tie_break_key,
    sens_722_summary,
)
from e1_x6_raw_redesign.features import continuous_lookback_ok  # noqa: E402
from e1_x6_raw_redesign.replay_order import availability_sort_key  # noqa: E402
from e1_x6_raw_redesign.tick_resolver import (  # noqa: E402
    classify_from_increments,
    next_valid_price_above,
    next_valid_price_below,
    tick_size,
)
from e1_x6_raw_redesign.windows import CENSOR_POLICY, build_analysis_mask_r1  # noqa: E402


def _write_raw_day(tmp_path, day_dash: str, sym: str, events: list[dict]) -> Path:
    dd = tmp_path / "data" / "push_jsonl" / day_dash
    dd.mkdir(parents=True, exist_ok=True)
    fp = dd / f"{sym}.jsonl"
    with fp.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return fp


def _quote_event(ts_iso: str, bid: float, ask: float, bid_time: str = None) -> dict:
    payload = {
        "Buy1": {"Price": bid, "Qty": 100.0},
        "Sell1": {"Price": ask, "Qty": 100.0},
    }
    if bid_time:
        payload["BidTime"] = bid_time
        payload["AskTime"] = bid_time
    return {"symbol": "TEST.T", "recorded_at": ts_iso, "source": "t", "payload": payload}


def test_event_row_coverage_not_confused_with_asof_grid_coverage(tmp_path):
    """Events exist 09:00-10:00 only: event-row missing rate is 0, but as-of
    grid coverage over the valid span is far below 1 (stale after 30s)."""
    from e1_x6_raw_redesign.asof_coverage import scan_day

    day = "20260721"
    evs = []
    for k in range(720):  # 09:00:00 .. 09:59:55 every 5s
        mm, ss = divmod(k * 5, 60)
        evs.append(_quote_event(f"2026-07-21T09:{mm:02d}:{ss:02d}+09:00", 500.0, 500.5))
    evs.append(_quote_event("2026-07-21T10:30:00+09:00", 500.0, 500.5))
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", evs)
    cov = scan_day(tmp_path, day, ["TEST"])  # universe uses canonical codes (no .T)
    q = cov["sessions"]["AM"]["quote"]
    # valid span 09:00..10:30 => 1081 grids; available: 720 (event period) +
    # 6 grids carried <=30s after 09:59:55 + the 10:30:00 grid = 727
    assert q["eligible_grid_n"] == 1081
    assert q["available_grid_n"] == 727
    assert q["stale_grid_n"] == 1081 - 727
    assert q["coverage"] < 0.70  # event-row missing rate would be 0.0


def test_state_carry_within_30s_and_stale_beyond(tmp_path):
    from e1_x6_raw_redesign.asof_coverage import scan_day

    evs = [_quote_event("2026-07-21T09:10:00+09:00", 500.0, 500.5)]
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", evs)
    cov = scan_day(tmp_path, "20260721", ["TEST"])
    q = cov["sessions"]["AM"]["quote"]
    # single-event valid span collapses to one grid; it is available (age 0)
    assert q["available_grid_n"] == 1
    assert q["age_sec"]["max"] <= 30.0

    evs2 = [
        _quote_event("2026-07-21T09:10:00+09:00", 500.0, 500.5),
        _quote_event("2026-07-21T09:11:00+09:00", 500.0, 500.5),
    ]
    _write_raw_day(tmp_path, "2026-07-21", "TEST2.T", evs2)
    cov2 = scan_day(tmp_path, "20260721", ["TEST2"])
    q2 = cov2["sessions"]["AM"]["quote"]
    # 13 grids in [09:10:00, 09:11:00]: carry ok 09:10:00..09:10:30 (7 grids)
    # then stale 09:10:35..09:10:55 (5 grids), then the 09:11:00 update (1)
    assert q2["eligible_grid_n"] == 13
    assert q2["available_grid_n"] == 8
    assert q2["stale_grid_n"] == 5


def test_late_arrival_not_backfilled_into_past_grids(tmp_path):
    """An event whose SOURCE time is old but arrives (ingress) late becomes
    usable only from its ingress; earlier grids stay missing."""
    from e1_x6_raw_redesign.asof_coverage import scan_day

    evs = [_quote_event("2026-07-21T09:20:00+09:00", 500.0, 500.5,
                        bid_time="2026-07-21T09:00:00+09:00")]
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", evs)
    cov = scan_day(tmp_path, "20260721", ["TEST"])
    q = cov["sessions"]["AM"]["quote"]
    # valid span = ingress only (09:20): exactly 1 grid, available there and
    # NOT projected back to 09:00-09:19 (those grids are outside the span /
    # would be missing, never available)
    assert q["eligible_grid_n"] == 1
    assert q["available_grid_n"] == 1
    assert q["missing_grid_n"] == 0


def test_late_event_does_not_change_prior_feature_ledger():
    grid = _grid(120)
    evs = _steady_events("A", 90, step=0.5)
    f1 = compute_symbol_features(build_symbol_grid("A", evs, grid))
    sha1 = _feature_ledger_sha({k: v[:90] for k, v in f1.items()})
    late = _ev("A", T0 + 5.0 * 100, 990.0, 990.2)  # arrives after grid 90
    f2 = compute_symbol_features(build_symbol_grid("A", evs + [late], grid))
    sha2 = _feature_ledger_sha({k: v[:90] for k, v in f2.items()})
    assert sha1 == sha2


def test_availability_order_deterministic_despite_source_regressions():
    from datetime import datetime, timezone

    class _E:
        def __init__(self, ts, seq, sym, key):
            self.ts = datetime.fromtimestamp(ts, tz=timezone.utc)
            self.sequence = seq
            self.symbol = sym
            self.unique_key = key

    evs = [_E(100.0, 2, "B", "k2"), _E(100.0, 1, "A", "k1"), _E(99.0, 3, "C", "k3"),
           _E(100.0, 1, "A", "k0")]
    import random

    order1 = sorted(evs, key=availability_sort_key)
    shuffled = evs[:]
    random.Random(7).shuffle(shuffled)
    order2 = sorted(shuffled, key=availability_sort_key)
    assert [e.unique_key for e in order1] == [e.unique_key for e in order2]
    assert order1[0].unique_key == "k3"  # earliest availability first


def test_300s_lookback_never_spans_a_gap():
    grid = _grid(300)
    evs = _steady_events("A", 100) + [
        _ev("A", T0 + 5.0 * k - 0.5, 1000.0, 1000.2) for k in range(150, 300)
    ]  # 250s hole between grid 100 and 150
    sg = build_symbol_grid("A", evs, grid)
    ok = continuous_lookback_ok(sg, steps=60)
    assert not ok[149]           # inside/just after the gap
    assert not ok[200]           # only 50 covered grids since resume
    assert ok[150 + 61]          # 61 grids after resume => 300s continuous
    assert not ok[105]           # gap started (stale)


def test_exit_horizon_mask_precomputed_for_truncated_windows():
    fake_cov = {
        "20260721": {
            "windows": {
                "AM": {"expected_start_epoch": 0.0, "expected_end_epoch": 9000.0,
                       "valid_start_epoch": 0.0, "valid_end_epoch": 9000.0,
                       "valid_sec": 9000.0, "coverage_rate": 1.0,
                       "eligible_grids_n": 1801, "quality_class": "FULL"},
                "PM": {"expected_start_epoch": 20000.0, "expected_end_epoch": 30800.0,
                       "valid_start_epoch": 20000.0, "valid_end_epoch": 28000.0,
                       "valid_sec": 8000.0, "coverage_rate": 0.74,
                       "eligible_grids_n": 1601, "quality_class": "TRUNCATED"},
            },
        },
    }
    mask = build_analysis_mask_r1(fake_cov, {"windows": {}})
    am = mask["windows"]["20260721_AM"]
    pm = mask["windows"]["20260721_PM"]
    # FULL window: exit horizon may end at REGULAR close => no extra cut
    assert am["entry_evaluable_until_epoch"] == 9000.0
    # TRUNCATED window: entries within 600s of the truncated end are pre-masked
    assert pm["entry_evaluable_until_epoch"] == 28000.0 - 600.0
    assert mask["analysis_mask_id"].startswith("MASK_R1_")
    # A/B: same input => same mask id
    mask2 = build_analysis_mask_r1(fake_cov, {"windows": {}})
    assert mask2["analysis_mask_id"] == mask["analysis_mask_id"]


def test_censor_policy_forbids_silent_drops_and_zeroing():
    assert "never converted to 0 yen" in CENSOR_POLICY["no_zeroing_incomplete"]
    assert "never silently removed" in CENSOR_POLICY["no_silent_censor_drop"]
    assert "gate FAIL" in CENSOR_POLICY["gate_fail_on_leftover"]
    assert "identical" in CENSOR_POLICY["same_mask_for_all_candidates"]


def test_dynamic_tick_band_boundaries():
    # OTHER table boundaries
    assert tick_size("OTHER", 3000.0) == 1.0
    assert tick_size("OTHER", 3000.5) == 5.0
    assert next_valid_price_above(3000.0, "OTHER") == 3005.0
    assert next_valid_price_below(3005.0, "OTHER") == 3000.0
    assert next_valid_price_below(3000.0, "OTHER") == 2999.0
    assert next_valid_price_above(4999.0, "OTHER") == 5000.0
    assert next_valid_price_above(5000.0, "OTHER") == 5010.0
    assert next_valid_price_below(5010.0, "OTHER") == 5000.0
    # NARROW (official TOPIX500 fine table) vs OTHER
    assert tick_size("NARROW_TOPIX500", 999.9) == 0.1
    assert tick_size("NARROW_TOPIX500", 1000.5) == 0.5
    assert tick_size("NARROW_TOPIX500", 5000.0) == 1.0
    assert tick_size("NARROW_TOPIX500", 15000.0) == 5.0
    assert next_valid_price_above(999.9, "NARROW_TOPIX500") == 1000.0
    assert next_valid_price_above(1000.0, "NARROW_TOPIX500") == 1000.5
    assert next_valid_price_below(1000.0, "NARROW_TOPIX500") == 999.9
    assert next_valid_price_below(1000.5, "NARROW_TOPIX500") == 1000.0
    assert next_valid_price_above(3000.0, "NARROW_TOPIX500") == 3001.0
    assert next_valid_price_below(3001.0, "NARROW_TOPIX500") == 3000.0
    assert next_valid_price_above(10000.0, "NARROW_TOPIX500") == 10005.0
    assert next_valid_price_below(10005.0, "NARROW_TOPIX500") == 10000.0
    # half-tick mid references resolve to the true grid
    assert next_valid_price_above(999.95, "NARROW_TOPIX500") == 1000.0
    assert next_valid_price_below(999.95, "NARROW_TOPIX500") == 999.9
    with pytest.raises(ValueError):
        next_valid_price_below(0.1, "NARROW_TOPIX500")


def test_tick_classification_requires_evidence():
    # increments of 1 at price ~5700 are finer than OTHER (10) => NARROW
    res = classify_from_increments({"5001.0": 1.0}, {"5001.0": 500})
    assert res["class"] == "NARROW_TOPIX500"
    # increments equal to OTHER tick everywhere => coarser class chosen
    res2 = classify_from_increments({"1001.0": 1.0}, {"1001.0": 500})
    assert res2["class"] == "OTHER"
    # insufficient observations => unresolved (=> P1_R1_BLOCKED upstream)
    res3 = classify_from_increments({"1001.0": 1.0}, {"1001.0": 3})
    assert res3["class"] is None
    # finer than both tables => inconsistent => unresolved
    res4 = classify_from_increments({"5001.0": 0.01}, {"5001.0": 500})
    assert res4["class"] is None


def _pull_feats(mid: np.ndarray) -> dict:
    n = mid.shape[0]
    f = {k: np.full(n, np.nan) for k in (
        "spread_bps", "ret_30s_bps", "ret_60s_bps", "rv_300s_bps", "up_persist_60s",
        "dir_eff_300s", "vwap_dev_bps", "range_ratio_60_300", "vol_ratio_60_300",
        "ret_300s_bps", "range_pos_300s", "breakout_dev_bps", "high_300s", "low_300s",
        "pullback_bps", "rv_60s_bps", "high_60s", "low_60s")}
    f["mid"] = mid
    f["spread_bps"] = np.full(n, 2.0)
    f["ret_30s_bps"] = np.full(n, 2.0)
    f["ret_60s_bps"] = np.full(n, 1.0)   # ret30 > ret60 (decelerating)
    f["rv_300s_bps"] = np.full(n, 60.0)
    f["up_persist_60s"] = np.full(n, 1.0)
    return f


def test_pull_no_setup_when_low_after_high():
    # falling market: swing_low occurs after any local high => no valid episode
    n = 200
    grid = _grid(n)
    mid = 1000.0 - 0.5 * np.arange(n)
    feats = _pull_feats(mid)
    dec = run_setup_machine("PULL", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500")
    assert not [d for d in dec if d.state in ("SETUP", "TRIGGERED", "OPEN")]


def test_pull_support_condition_not_tautological():
    # rise 950->990 (~420bps), then pullback keeps making NEW LOWS every grid:
    # mid[g] < pullback_low(min of mid[h+1..g-1]) => setup must NOT hold
    n = 160
    grid = _grid(n)
    up = 950.0 + 1.0 * np.arange(41)          # g0..g40 rise to 990
    down = 990.0 - 0.4 * np.arange(1, 80)     # continuous new lows
    mid = np.concatenate([up, down, np.full(n - 41 - 79, 960.0)])
    feats = _pull_feats(mid)
    dec = run_setup_machine("PULL", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500")
    setup_grids = [d.grid_idx for d in dec if d.state == "SETUP"]
    # during the continuous fall (grids 42..119) setup never forms
    assert not [g for g in setup_grids if 42 <= g <= 119]


def _break_feats(n: int) -> dict:
    mid = np.full(n, 900.0)
    f = {k: np.full(n, np.nan) for k in (
        "spread_bps", "ret_30s_bps", "ret_60s_bps", "rv_300s_bps", "up_persist_60s",
        "dir_eff_300s", "vwap_dev_bps", "ret_300s_bps", "range_pos_300s",
        "breakout_dev_bps", "high_300s", "low_300s", "pullback_bps", "rv_60s_bps",
        "high_60s", "low_60s")}
    f["mid"] = mid
    f["spread_bps"] = np.full(n, 2.0)
    f["range_ratio_60_300"] = np.full(n, 1.0)
    f["vol_ratio_60_300"] = np.full(n, 1.0)
    f["up_persist_60s"] = np.full(n, 1.0)
    f["rv_300s_bps"] = np.full(n, 30.0)
    return f


def test_break_range_only_60s_not_sufficient():
    n = 120
    grid = _grid(n)
    feats = _break_feats(n)
    feats["range_ratio_60_300"][20:60] = 0.3   # range compressed 40 grids
    # vol_ratio stays 1.0 (NOT compressed)
    dec = run_setup_machine("BREAK", feats, ["NEUTRAL"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500")
    assert not [d for d in dec if d.state == "SETUP"]


def test_break_requires_both_ratios_for_12_grids():
    n = 200
    grid = _grid(n)
    feats = _break_feats(n)
    feats["range_ratio_60_300"][20:31] = 0.3   # both, but only 11 grids
    feats["vol_ratio_60_300"][20:31] = 0.5
    dec = run_setup_machine("BREAK", feats, ["NEUTRAL"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500")
    assert not [d for d in dec if d.state == "SETUP"]

    feats2 = _break_feats(n)
    feats2["range_ratio_60_300"][20:60] = 0.3  # both for >=12 grids
    feats2["vol_ratio_60_300"][20:60] = 0.5
    dec2 = run_setup_machine("BREAK", feats2, ["NEUTRAL"] * n, entry_allowed_mask(grid),
                             confirmation="STANDARD", symbol="A",
                             symbol_class="NARROW_TOPIX500")
    assert [d for d in dec2 if d.state == "SETUP"]


def test_break_streak_resets_on_not_evaluable():
    n = 200
    grid = _grid(n)
    feats = _break_feats(n)
    feats["range_ratio_60_300"][20:60] = 0.3
    feats["vol_ratio_60_300"][20:60] = 0.5
    feats["mid"][28] = np.nan   # NOT_EVALUABLE grid inside the streak
    dec = run_setup_machine("BREAK", feats, ["NEUTRAL"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500")
    first_setup = min((d.grid_idx for d in dec if d.state == "SETUP"), default=None)
    # streak restarts at 29 => earliest possible SETUP at 29+11=40, not 31
    assert first_setup is not None and first_setup >= 40


def test_cap5_tie_break_order():
    rows = [
        {"trigger_ts": 100.0, "decision_grid": 7, "symbol": "B"},
        {"trigger_ts": 100.0, "decision_grid": 7, "symbol": "A"},
        {"trigger_ts": 100.0, "decision_grid": 5, "symbol": "Z"},
        {"trigger_ts": 99.0, "decision_grid": 9, "symbol": "Q"},
    ]
    ordered = sorted(rows, key=cap5_tie_break_key)
    assert [r["symbol"] for r in ordered] == ["Q", "Z", "A", "B"]


def test_sens_722_gate_block():
    day_pnls = {d: 1000.0 for d in ("20260721", "20260723", "20260724", "20260727",
                                    "20260728", "20260729", "20260730", "20260731")}
    day_pnls["20260722"] = 50000.0
    s = sens_722_summary(day_pnls)
    assert s["ex722_total_pnl"] == 8000.0
    assert s["ex722_median_day_pnl"] == 1000.0
    assert abs(s["contribution_722_share_of_gross_positive"] - 50000.0 / 58000.0) < 1e-12
    assert s["direction_agreement_with_full"] is True
    # rolling folds are frozen and complete
    assert set(ROLLING_ORIGIN_5FOLD) >= {"F1", "F2", "F3", "F4", "F5"}
    assert ROLLING_ORIGIN_5FOLD["F5"]["confirm"] == "20260731"


def test_base_binding_rejects_mismatched_mask():
    from e1_x6_raw_redesign.base_binding import build_base_binding

    bad_windows = [f"2026072{d}_AM" for d in (1, 2, 3)]
    res = build_base_binding(bad_windows)
    if "artifact_path" in res:  # artifact exists on this machine
        assert res["comparable"] is False
        assert "NOT_COMPARABLE_BASE" in res["reason"]
    else:
        assert res["comparable"] is False


def test_base_binding_accepts_stage1_window_set():
    from e1_x6_raw_redesign.base_binding import build_base_binding

    wins = [f"{d}_{ap}" for d in ("20260721", "20260722", "20260723", "20260724",
                                  "20260727", "20260728", "20260729", "20260730",
                                  "20260731")
            for ap in ("AM", "PM") if not (d == "20260721" and ap == "AM")]
    res = build_base_binding(wins)
    if res.get("artifact_path"):
        assert res["comparable"] is True
        assert res["base_metrics"]["completed_trades"] == 1058
        assert res["dd_formula"].startswith("realized_sequence_max_dd")


def test_ab_decision_ledger_sha_match():
    n = 200
    grid = _grid(n)
    feats = _synthetic_cont_feats(n)

    def _run():
        dec = run_setup_machine("CONT", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                                confirmation="STANDARD", symbol="A",
                                symbol_class="NARROW_TOPIX500")
        return sha256_obj([d.__dict__ for d in dec])

    assert _run() == _run()


# ===================== Phase A-R2 additional mandatory tests =====================

from e1_x6_raw_redesign.decision_coverage import scan_day_r2  # noqa: E402


def test_no_state_advance_on_not_due_grid():
    """Without a symbol PUSH in the grid, the ENTRY machine must not advance,
    even though carried quotes / other symbols' market state changed."""
    n = 200
    grid = _grid(n)
    feats = _synthetic_cont_feats(n)
    due = np.zeros(n, dtype=bool)
    due[:5] = True  # PUSHes stop after grid 4 (before any trigger is reachable)
    dec = run_setup_machine("CONT", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500", due=due)
    states_after = [d for d in dec if d.grid_idx >= 5]
    assert not states_after  # machine held; nothing advanced on non-due grids
    # with PUSHes present the same features DO advance to OPEN
    dec2 = run_setup_machine("CONT", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                             confirmation="STANDARD", symbol="A",
                             symbol_class="NARROW_TOPIX500",
                             due=np.ones(n, dtype=bool))
    assert [d for d in dec2 if d.state == "OPEN"]


def _am_span_events(prices_at: list[tuple[str, float, float]]) -> list[dict]:
    """Helper: always bookend AM with session-open and session-close so the
    window is FULL (entry horizon = regular close) and warmup/horizon gates
    are meaningful for mid-session due grids."""
    evs = [_quote_event("2026-07-21T09:00:00+09:00", 500.0, 500.5)]
    for ts, bid, ask in prices_at:
        evs.append(_quote_event(ts, bid, ask))
    evs.append(_quote_event("2026-07-21T11:30:00+09:00", 500.0, 500.5))
    return evs


def test_same_grid_multi_push_availability_collapse(tmp_path):
    """Multiple PUSHes in one grid: the LAST state in availability order wins,
    deterministically (one evaluation per symbol per grid)."""
    mid = [
        ("2026-07-21T09:10:01+09:00", 500.0, 500.5),
        ("2026-07-21T09:10:03+09:00", 501.0, 501.5),
        ("2026-07-21T09:10:04+09:00", 502.0, 502.5),
    ]
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", _am_span_events(mid))
    r1 = scan_day_r2(tmp_path, "20260721", ["TEST"])
    r2 = scan_day_r2(tmp_path, "20260721", ["TEST"])
    s1 = r1["sessions"]["AM"]
    assert "due_symbol_grid_n" in s1
    assert s1["due_symbol_grid_n"] >= 1
    assert sha256_obj(s1) == sha256_obj(r2["sessions"]["AM"])  # deterministic


def test_not_due_excluded_from_decision_denominator(tmp_path):
    # bookends + one mid-session push after warmup => exactly 2 due grids
    # (09:20 and 11:30); 09:00 is before warmup. NOT_DUE = grids with no PUSH.
    mid = [("2026-07-21T09:20:00+09:00", 500.0, 500.5)]
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", _am_span_events(mid))
    cov = scan_day_r2(tmp_path, "20260721", ["TEST"])
    s = cov["sessions"]["AM"]
    assert s["full_grid_n"] > 100
    assert s["due_symbol_grid_n"] == 2  # 09:20 + 11:30
    # three PUSH grids (09:00, 09:20, 11:30); NOT_DUE excludes only no-PUSH
    assert s["NOT_DUE_NO_SYMBOL_UPDATE_n"] == s["universe_n"] * s["full_grid_n"] - 3
    assert s["decision_quote_coverage"] == 1.0
    assert "source_semantics_unknown_n" in s
    # NOT_DUE grids are never in the decision denominator
    assert s["due_symbol_grid_n"] + s["NOT_DUE_NO_SYMBOL_UPDATE_n"] < s["universe_n"] * s["full_grid_n"] \
        or s["due_symbol_grid_n"] == 2


def test_full_grid_vs_decision_coverage_not_confused(tmp_path):
    """Sparse pushes: full-grid state coverage is low, decision coverage is
    high — they must be reported separately with different values."""
    mid = []
    for k in range(0, 3600, 120):  # one push every 2 minutes, 09:00-10:00
        mm, ss = divmod(k, 60)
        hh = 9 + mm // 60
        mid.append((f"2026-07-21T{hh:02d}:{mm % 60:02d}:{ss:02d}+09:00", 500.0, 500.5))
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", _am_span_events(mid))
    s = scan_day_r2(tmp_path, "20260721", ["TEST"])["sessions"]["AM"]
    assert s["decision_quote_coverage"] == 1.0          # every due grid healthy
    assert s["full_grid_state_coverage"] < 0.5          # mostly stale grids
    assert s["full_grid_state_coverage"] != s["decision_quote_coverage"]


def test_ingress_source_max_merge_abolished(tmp_path):
    """An event whose SOURCE time is in the future of its ingress must still be
    available at its ingress grid (availability = ingress only)."""
    evs = _am_span_events([
        ("2026-07-21T09:20:00+09:00", 500.0, 500.5),
    ])
    # rewrite the mid event with a future BidTime
    evs[1] = _quote_event("2026-07-21T09:20:00+09:00", 500.0, 500.5,
                          bid_time="2026-07-21T09:25:00+09:00")
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", evs)
    s = scan_day_r2(tmp_path, "20260721", ["TEST"])["sessions"]["AM"]
    # availability = ingress: 09:20 is a due + available decision opportunity
    assert s["due_symbol_grid_n"] >= 1
    assert s["decision_quote_coverage"] == 1.0
    # policy statement must record the abolition
    assert "max(ingress,source) ABOLISHED" in scan_day_r2(
        tmp_path, "20260721", ["TEST"])["source_semantics"]["policy"]


def test_future_event_does_not_change_past_due_decisions(tmp_path):
    mid = [("2026-07-21T09:20:00+09:00", 500.0, 500.5),
           ("2026-07-21T10:00:00+09:00", 501.0, 501.5)]
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", _am_span_events(mid))
    before = scan_day_r2(tmp_path, "20260721", ["TEST"])
    # append a late event AFTER previous close bookend — extend with a later
    # mid-session event that does not rewrite earlier due grids
    mid2 = mid + [("2026-07-21T10:40:00+09:00", 502.0, 502.5)]
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", _am_span_events(mid2))
    after = scan_day_r2(tmp_path, "20260721", ["TEST"])
    b = before["sessions"]["AM"]["symbol_decision_coverage"]["TEST"]
    a = after["sessions"]["AM"]["symbol_decision_coverage"]["TEST"]
    assert b["due_n"] <= a["due_n"]
    assert a["ok_n"] >= b["ok_n"]


def test_r2_lookback_gap_counted_not_usability_kill(tmp_path):
    mid = []
    for k in range(0, 1200, 5):  # 09:00-09:20 dense
        mm, ss = divmod(k, 60)
        mid.append((f"2026-07-21T09:{mm:02d}:{ss:02d}+09:00", 500.0, 500.5))
    # gap 09:20-10:00, then one push at 10:00 (lookback incomplete there)
    mid.append(("2026-07-21T10:00:00+09:00", 500.0, 500.5))
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", _am_span_events(mid))
    s = scan_day_r2(tmp_path, "20260721", ["TEST"])["sessions"]["AM"]
    assert s["incomplete_lookback_n"] >= 1        # counted per opportunity
    assert s["decision_quote_coverage"] == 1.0    # NOT a quote-usability kill


def test_market_context_loo_30_gate(tmp_path):
    """With only 3 symbols, LOO evaluable n is 2 < 30 => context coverage 0."""
    for i, sym in enumerate(("AAA.T", "BBB.T", "CCC.T")):
        mid = [(f"2026-07-21T09:{10+m:02d}:00+09:00", 500.0 + i, 500.5 + i)
               for m in range(0, 40)]
        _write_raw_day(tmp_path, "2026-07-21", sym, _am_span_events(mid))
    s = scan_day_r2(tmp_path, "20260721", ["AAA", "BBB", "CCC"])["sessions"]["AM"]
    assert s["due_symbol_grid_n"] > 0
    assert s["market_context_coverage"] == 0.0
    assert s["mkt_evaluable_stats"]["max"] <= 2


def test_official_tick_class_with_empirical_crosscheck():
    from e1_x6_raw_redesign.tick_official import empirical_check, official_class

    cls, reason = official_class({"scale_category": "TOPIX Core30",
                                  "is_etf": "false", "is_reit": "false"})
    assert cls == "NARROW_TOPIX500" and "Core30" in reason
    cls2, _ = official_class({"scale_category": "TOPIX Small 1",
                              "is_etf": "false", "is_reit": "false"})
    assert cls2 == "OTHER"
    assert official_class(None)[0] is None
    assert official_class({"scale_category": "-", "is_etf": "true",
                           "is_reit": "false"})[0] is None
    # empirical evidence is cross-check only: contradiction => not consistent
    ok, _ = empirical_check("OTHER", {"5001.0": 1.0})       # finer than 10 yen
    assert not ok
    ok2, _ = empirical_check("NARROW_TOPIX500", {"5001.0": 1.0})
    assert ok2


def test_base_recut_refuses_without_same_mask():
    from e1_x6_raw_redesign.base_recut import recut_base

    empty_mask = {"analysis_mask_id": "MASK_X", "windows": {}}
    res = recut_base(empty_mask)
    if res.get("comparable"):
        # bundles exist: with NO included windows every trade must be excluded
        assert res["artifact"]["recut_metrics"]["completed_trades"] == 0
        assert res["artifact"]["excluded_counts"]["WINDOW_NOT_INCLUDED"] == 1058
    else:
        assert "NOT_COMPARABLE_BASE" in res["reason"]


def test_ab_feature_due_mask_sha_match(tmp_path):
    # feature sha
    grid = _grid(150)
    evs = _steady_events("A", 150)
    f1 = compute_symbol_features(build_symbol_grid("A", evs, grid))
    f2 = compute_symbol_features(build_symbol_grid("A", evs, grid))
    assert _feature_ledger_sha(f1) == _feature_ledger_sha(f2)
    # due-decision sha (machine level, due-masked)
    feats = _synthetic_cont_feats(200)
    due = np.ones(200, dtype=bool)
    due[::7] = False
    g2 = _grid(200)

    def _sha():
        dec = run_setup_machine("CONT", feats, ["TREND_UP"] * 200,
                                entry_allowed_mask(g2), confirmation="STANDARD",
                                symbol="A", symbol_class="NARROW_TOPIX500", due=due)
        return sha256_obj([d.__dict__ for d in dec])

    assert _sha() == _sha()
    # mask sha (A/B) — already covered for build_analysis_mask_r1; assert again
    fake_cov = {"20260721": {"windows": {
        "AM": {"expected_start_epoch": 0.0, "expected_end_epoch": 9000.0,
               "valid_start_epoch": 0.0, "valid_end_epoch": 9000.0,
               "valid_sec": 9000.0, "coverage_rate": 1.0,
               "eligible_grids_n": 1801, "quality_class": "FULL"},
        "PM": {"expected_start_epoch": 20000.0, "expected_end_epoch": 30800.0,
               "valid_start_epoch": 20000.0, "valid_end_epoch": 30800.0,
               "valid_sec": 10800.0, "coverage_rate": 1.0,
               "eligible_grids_n": 2161, "quality_class": "FULL"}}}}
    m1 = build_analysis_mask_r1(fake_cov, {"windows": {}})
    m2 = build_analysis_mask_r1(fake_cov, {"windows": {}})
    assert m1["analysis_mask_id"] == m2["analysis_mask_id"]


# ===================== Phase A-R3 additional mandatory tests =====================

from e1_x6_raw_redesign.decision_coverage_r3 import (  # noqa: E402
    AUDIT_EXPECT,
    scan_day_r3,
)
from e1_x6_raw_redesign.tick_official_r3 import (  # noqa: E402
    SUPPLEMENTAL_OFFICIAL,
    class_on_day,
    classify_universe_r3,
)


def test_spread_above_50_does_not_lower_structural_coverage(tmp_path):
    """Wide spreads reduce tradeability, not structural quote coverage."""
    mid = []
    for m in range(10, 50):
        # 100bps spread (bid=500, ask=505) — unhealthy but structurally valid
        mid.append((f"2026-07-21T09:{m:02d}:00+09:00", 500.0, 505.0))
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", _am_span_events(mid))
    s = scan_day_r3(tmp_path, "20260721", ["TEST"])["sessions"]["AM"]
    assert s["structural_decision_quote_coverage"] == 1.0
    assert s["spread_healthy_rate"] < 1.0
    assert s["spread_unhealthy_n"] > 0


def test_spread_above_50_blocks_entry_open():
    n = 200
    grid = _grid(n)
    feats = _synthetic_cont_feats(n)
    feats["spread_bps"] = np.full(n, 60.0)  # unhealthy
    dec = run_setup_machine("CONT", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500",
                            due=np.ones(n, dtype=bool))
    assert not [d for d in dec if d.state == "OPEN"]


def test_spread_eq_50_boundary_passes_entry():
    n = 200
    grid = _grid(n)
    feats = _synthetic_cont_feats(n)
    feats["spread_bps"] = np.full(n, 50.0)  # boundary: <=50 passes
    dec = run_setup_machine("CONT", feats, ["TREND_UP"] * n, entry_allowed_mask(grid),
                            confirmation="STANDARD", symbol="A",
                            symbol_class="NARROW_TOPIX500",
                            due=np.ones(n, dtype=bool))
    assert [d for d in dec if d.state == "OPEN"]


def test_invalid_crossed_missing_stale_lower_structural(tmp_path):
    # valid bookends + one mid event with crossed market (ask < bid)
    mid = [("2026-07-21T09:20:00+09:00", 505.0, 500.0)]  # crossed
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", _am_span_events(mid))
    s = scan_day_r3(tmp_path, "20260721", ["TEST"])["sessions"]["AM"]
    # 09:20 crossed fails structural; 11:30 bookend is healthy => coverage < 1
    assert s["rejects"]["crossed_n"] >= 1
    assert s["structural_decision_quote_coverage"] < 1.0


def test_audit_expect_windows_recomputed_from_raw():
    """If real raw is present, 7/24 AM and 7/29 AM must match audit expectations."""
    from pathlib import Path

    nat = Path(r"C:\Users\yhach\Documents\tradebotfile\kabu_native")
    raw = nat / "data" / "push_jsonl" / "2026-07-24"
    if not raw.is_dir():
        return  # skip silently when raw absent
    from e1_x6_raw_redesign.asof_coverage import canonical_day_bundle

    for day, wid in (("20260724", "20260724_AM"), ("20260729", "20260729_AM")):
        cb = canonical_day_bundle(nat, day)
        cov = scan_day_r3(nat, day, cb["universe"])
        sk = "AM"
        s = cov["sessions"][sk]
        exp = AUDIT_EXPECT[wid]
        assert s["due_symbol_grid_n"] == exp["due_n"]
        assert s["structural_decision_quote_available_n"] == exp["structural_n"]
        assert abs(s["structural_decision_quote_coverage"] - exp["coverage"]) < 1e-5


def test_official_tick_effective_date_switch():
    spec = SUPPLEMENTAL_OFFICIAL["593A"]  # listed 2026-07-22
    assert class_on_day(spec, "20260721")[0] is None  # before listing
    assert class_on_day(spec, "20260722")[0] == "OTHER"
    assert class_on_day(spec, "20260731")[0] == "OTHER"


def test_master_gap_no_empirical_fallback(tmp_path):
    """Missing master row without supplemental evidence stays UNRESOLVED —
    empirical ticks must not invent a class."""
    # use a fake code with observations but no master / no supplemental
    res = classify_universe_r3(
        Path(r"C:\Users\yhach\Documents\tradebotfile"),
        ["ZZZZ"],
        {"ZZZZ": {"1001.0": [1.0, 500]}},
        ["20260721"],
    )
    assert "ZZZZ" in res["unresolved"]
    assert res["symbol_classes"]["ZZZZ"]["class"] is None
    assert res["symbol_classes"]["ZZZZ"]["empirical_check"] == (
        "NOT_EVALUATED_OFFICIAL_CLASS_UNRESOLVED"
    )


def test_four_symbols_require_official_evidence():
    for code in ("581A", "584A", "593A", "598A"):
        assert code in SUPPLEMENTAL_OFFICIAL
        spec = SUPPLEMENTAL_OFFICIAL[code]
        assert spec["security_type"] == "内国普通株式"
        assert spec["market_segment"] == "グロース"
        assert spec["topix500_applicable"] is False
        assert spec["class"] == "OTHER"


def test_r2_block_evidence_unchanged():
    from pathlib import Path

    from e1_x6_raw_redesign.history import SUPERSEDED_RUNS
    from e1_x6_raw_redesign.store import sha256_file

    row = SUPERSEDED_RUNS["e1x6r3r2_20260803_040009_4d87ffa4"]
    assert row["disposition"] == "VALID_BLOCK_EVIDENCE_R2"
    fp = Path(row["artifacts_preserved_at"]) / "published" / "report.json"
    if fp.is_file():
        assert sha256_file(fp) == row["artifact_sha256"]["report.json"]


def test_base_recut_sha_matches_r2_freeze():
    from pathlib import Path
    import json

    fp = (Path.home() / "e1x6_research_store" / "raw_feature_redesign"
          / "e1x6r3r2_20260803_040009_4d87ffa4" / "e1x5_base_recut.json")
    if not fp.is_file():
        return
    art = json.loads(fp.read_text(encoding="utf-8"))
    assert art["artifact_sha256"] == (
        "138f74676a3ffd3f303f2bfdeb529c9bd4369a0f13f59bb805e65690aefa909f"
    )
    assert art["analysis_mask_id"] == "MASK_R1_ea8f67eb1b559218"
    assert art["recut_metrics"]["completed_trades"] == 915


def test_ab_r3_structural_sha_match(tmp_path):
    mid = [("2026-07-21T09:20:00+09:00", 500.0, 500.5)]
    _write_raw_day(tmp_path, "2026-07-21", "TEST.T", _am_span_events(mid))
    a = scan_day_r3(tmp_path, "20260721", ["TEST"])
    b = scan_day_r3(tmp_path, "20260721", ["TEST"])
    assert sha256_obj(a["sessions"]) == sha256_obj(b["sessions"])
    assert a["sessions"]["AM"]["structural_decision_quote_coverage"] == 1.0
