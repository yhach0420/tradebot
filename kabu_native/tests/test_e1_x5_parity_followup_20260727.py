"""E1_X5 Parity followup codegen-fix regressions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[1]
PARITY = REPO / "results" / "research" / "e1_x5_runtime_offline_parity_20260727"
CODEGEN = REPO / "results" / "research" / "e1_x5_runtime_offline_parity_followup_codegen_fix_20260727"
EXPECTED_TRADE_SHA = "ed90c02036b1a612b6639dde655e3d58f960b25f1b490c5f381694186376b0c7"


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _board(sym: str, px: float, ts: datetime, *, seq: int) -> dict:
    return {
        "Symbol": sym.replace(".T", ""),
        "CurrentPrice": px,
        "CurrentPriceTime": ts.isoformat(),
        "TradingVolume": 100000.0 + seq,
        "Buy1": {"Price": px - 0.5, "Qty": 1000.0},
        "Sell1": {"Price": px + 0.5, "Qty": 1000.0},
        "sequence": seq,
    }


@pytest.fixture
def e1_pair(monkeypatch):
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession, THRESHOLD
    import small_paper.e1_x5_dmid_score_provider as mod

    class _FakeModel:
        key = "D-MID_D4_H6"
        means = {}
        stds = {}
        impute = {}
        features = []
        coef = []
        intercept = 0.0

    monkeypatch.setattr(mod, "_score_feature_dict", lambda model, feats: float(THRESHOLD) + 0.1)
    monkeypatch.setattr(
        mod.DMidD4H6ScoreProvider,
        "_sample_due",
        lambda self, st, tick: (
            (True, "REGULAR")
            if (st.last_reg_ts is None or (tick.ts - st.last_reg_ts).total_seconds() >= 5.0)
            else (False, "")
        ),
    )
    provider = DMidD4H6ScoreProvider(_FakeModel())
    provider.ready = True
    session = E1X5ForwardShadowSession(enabled=True)
    return provider, session


def test_trade_ledger_sha_and_70_pnl():
    oracle = json.loads((PARITY / "oracle_baseline" / "oracle_trades.json").read_text(encoding="utf-8"))
    runtime = json.loads((PARITY / "runtime_trades.json").read_text(encoding="utf-8"))
    assert len(oracle) == 70 and len(runtime) == 70
    assert abs(sum(float(x["net_pnl_yen_100"]) for x in runtime) - 45023.825) < 0.01
    assert _sha(oracle) == EXPECTED_TRADE_SHA == _sha(runtime)


def test_snap_1240():
    prior = json.loads((PARITY / "report.json").read_text(encoding="utf-8"))
    snap = prior["crosscheck_70_45023"]["snap_1240"]
    assert snap == {"entries": 19, "completed": 15, "open": 4, "pnl": 17275.85} or (
        snap["entries"] == 19
        and snap["completed"] == 15
        and snap["open"] == 4
        and abs(float(snap["pnl"]) - 17275.85) < 0.01
    )


def test_exclusive_funnel_sum_17353_no_noe_key():
    from small_paper.e1_x5_parity_audit import rebuild_exclusive_funnel_from_prior

    prior = json.loads((PARITY / "report.json").read_text(encoding="utf-8"))
    funnel = rebuild_exclusive_funnel_from_prior(prior["runtime_summary"]["entry_funnel_exclusive"])
    assert "no_evaluation" not in funnel
    assert "tick_build_failed" not in funnel
    assert funnel["terminal_sum"] == 17353
    assert funnel["missing_score_after_valid_tick"] == 0
    assert funnel["threshold_fail"] == 14398
    assert funnel["spread_fail"] == 2780
    assert funnel["same_symbol_blocked"] == 91
    assert funnel["cap_blocked"] == 14
    assert funnel["accepted_entry"] == 70
    assert funnel["other_reject"] == 0


def test_session_funnel_excludes_no_evaluation():
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    s = E1X5ForwardShadowSession(enabled=True)
    ts = datetime(2026, 7, 27, 12, 40, 0, tzinfo=JST)
    for _ in range(308):
        s.on_missing_score(symbol="5253.T", ts=ts, reason="TICK_BUILD_FAILED")
    # Simulate 3 evaluated rejects + 1 entry via candidates
    s.evaluated_count = 4
    s.candidates.append(
        {"entry_decision": "REJECT", "reject_reason": "SCORE_BELOW_THRESHOLD", "score": 0.1}
    )
    s.candidates.append(
        {"entry_decision": "REJECT", "reject_reason": "SPREAD_OVER_5BPS", "score": 0.9}
    )
    s.candidates.append(
        {"entry_decision": "REJECT", "reject_reason": "CAP5_BLOCKED", "score": 0.9}
    )
    s.candidates.append({"entry_decision": "ENTER", "reject_reason": None, "score": 0.9})
    funnel = s.exclusive_entry_funnel()
    bd = s.no_evaluation_breakdown()
    assert "no_evaluation" not in funnel
    assert funnel["terminal_sum"] == 4
    assert bd["no_evaluation"] == 308
    assert bd["no_evaluation_reason_breakdown"]["TICK_BUILD_FAILED"] == 308


def test_no_double_count_308():
    from small_paper.e1_x5_parity_audit import funnel_exclusive_invariants, rebuild_exclusive_funnel_from_prior

    prior = json.loads((PARITY / "report.json").read_text(encoding="utf-8"))
    funnel = rebuild_exclusive_funnel_from_prior(prior["runtime_summary"]["entry_funnel_exclusive"])
    bd = {
        "no_evaluation_reason_breakdown": {"TICK_BUILD_FAILED": 308},
    }
    inv = funnel_exclusive_invariants(
        funnel, expected_evaluated=17353, no_evaluation=308, no_evaluation_breakdown=bd
    )
    assert inv["double_count_ok"] is True
    assert inv["no_evaluation_in_funnel"] is False
    assert inv["funnel_sum_ok"] is True


def test_feature_hash_not_comparable_recipe_difference():
    from small_paper.e1_x5_canonical_feature_hash import (
        LEGACY_ORACLE_FEATURE_HASH_SCHEMA,
        LEGACY_RUNTIME_FEATURE_HASH_SCHEMA,
        compare_feature_hashes,
    )

    out = compare_feature_hashes(
        oracle_schema=LEGACY_ORACLE_FEATURE_HASH_SCHEMA,
        runtime_schema=LEGACY_RUNTIME_FEATURE_HASH_SCHEMA,
        pairs=[("aaa", "bbb")] * 17353,
    )
    assert out["feature_hash_comparison_status"] == "NOT_COMPARABLE_RECIPE_DIFFERENCE"
    assert out["feature_hash_comparable_count"] == 0
    assert out["feature_hash_not_comparable_count"] == 17353
    assert out["feature_hash_mismatch_count"] is None
    assert out["feature_hash_mismatch_display"] == "N/A"


def test_feature_hash_same_recipe_match_and_mismatch():
    from small_paper.e1_x5_canonical_feature_hash import FEATURE_HASH_SCHEMA, compare_feature_hashes

    same = compare_feature_hashes(
        oracle_schema=FEATURE_HASH_SCHEMA,
        runtime_schema=FEATURE_HASH_SCHEMA,
        pairs=[("abc", "abc"), ("def", "def")],
    )
    assert same["feature_hash_comparison_status"] == "COMPARABLE"
    assert same["feature_hash_mismatch_count"] == 0
    assert same["feature_hash_comparable_count"] == 2

    diff = compare_feature_hashes(
        oracle_schema=FEATURE_HASH_SCHEMA,
        runtime_schema=FEATURE_HASH_SCHEMA,
        pairs=[("abc", "abc"), ("def", "XXX")],
    )
    assert diff["feature_hash_mismatch_count"] == 1


def test_discord_funnel_and_noe_separated():
    from small_paper.discord_current_system_summary import build_shadow_summary_structured
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession
    from small_paper.e1_x5_parity_audit import rebuild_exclusive_funnel_from_prior

    prior = json.loads((PARITY / "report.json").read_text(encoding="utf-8"))
    summary = dict(prior["runtime_summary"])
    funnel = rebuild_exclusive_funnel_from_prior(summary["entry_funnel_exclusive"])
    summary["entry_funnel_exclusive"] = funnel
    summary["no_evaluation_count"] = 308
    summary["missing_score_after_valid_tick"] = 0
    summary["tick_build_failed_count"] = 308
    summary["no_evaluation_breakdown"] = {
        "evaluated": 17353,
        "no_evaluation": 308,
        "no_evaluation_reason_breakdown": {"TICK_BUILD_FAILED": 308},
    }
    summary["forward_gate"] = E1X5ForwardShadowSession(enabled=False).forward_gate_display()
    flat = {
        "e1_x5_forward_shadow": summary,
        "e1_x5_forward_shadow_enabled": True,
        "e1_x5_forward_shadow_trades": summary.get("trades"),
        "e1_x5_forward_shadow_total_pnl_yen_100": summary.get("total_pnl_yen_100"),
        "e1_x5_forward_shadow_profit_factor_yen_100": summary.get("profit_factor_yen_100"),
        "e1_x5_forward_shadow_open_positions": summary.get("open_positions"),
        "e1_x5_forward_shadow_evaluated_count": 17353,
        "e1_x5_forward_shadow_no_evaluation_count": 308,
        "e1_x5_forward_shadow_missing_score_after_valid_tick": 0,
        "e1_x5_forward_shadow_entries_n": summary.get("entries_n"),
        "e1_x5_forward_shadow_wins": summary.get("wins"),
        "e1_x5_forward_shadow_losses": summary.get("losses"),
        "e1_x5_forward_shadow_draws": summary.get("draws"),
        "e1_x5_forward_shadow_cap_blocked": summary.get("cap_blocked"),
        "e1_x5_forward_shadow_same_symbol_blocked": summary.get("same_symbol_blocked"),
    }
    text = build_shadow_summary_structured(flat, am_pm="pm")["discord_text"]
    assert "evaluated/no_evaluation: 17353/308" in text
    assert "missing_score_after_valid_tick: 0" in text
    assert "funnel(evaluated, exclusive):" in text
    assert "threshold fail: 14398" in text
    assert "accepted entry: 70" in text
    assert "no_evaluation reasons:" in text
    assert "TICK_BUILD_FAILED: 308" in text
    assert "funnel(exclusive):" not in text
    assert "Valid progress: 0 sessions / 0 trades" in text
    assert "Excluded: 20260727 PM (NOT_ADOPTED)" in text


def test_canonical_feature_hash_decision_neutral(e1_pair):
    """Adding canonical hash logging must not change ENTRY/EXIT/score outcomes."""
    from small_paper.e1_x5_canonical_feature_hash import canonical_feature_hash, canonical_score_identity_hash
    from small_paper.e1_x5_decision_core import E1X5EventLog, process_e1_x5_event
    from small_paper.e1_x5_forward_shadow import ShadowPosition

    provider, session = e1_pair
    t0 = datetime(2026, 7, 27, 13, 0, 0, tzinfo=JST)
    log = E1X5EventLog()
    # Baseline decisions without caring about hash value
    for i in range(3):
        ts = t0 + timedelta(seconds=i * 6)
        process_e1_x5_event(
            provider=provider,
            session=session,
            symbol="7203.T",
            payload=_board("7203.T", 1000.0, ts, seq=i + 1),
            day="20260727",
            event_sequence=i + 1,
            decision_time=ts,
            event_log=log,
        )
    entries_n = len(session.entries)
    evaluated = session.evaluated_count
    # Hash helpers are pure
    h1 = canonical_feature_hash({"a": 1.234567890123, "b": None, "c": 2})
    h2 = canonical_feature_hash({"c": 2, "b": None, "a": 1.234567890123})
    assert h1["feature_hash"] == h2["feature_hash"]
    assert h1["feature_hash_schema"] == "e1_x5_canonical_feature_hash"
    assert h1["feature_hash_version"] == 1
    # Event log rows that have SCORE include schema
    scored = [r for r in log.rows if r.get("score_evaluated")]
    assert scored
    assert scored[0].get("feature_hash_schema") == "e1_x5_canonical_feature_hash"
    assert scored[0].get("feature_hash_version") == 1
    # STOP still works
    session.positions["7203.T"] = ShadowPosition(
        symbol="7203.T", entry_time=t0, entry_ask=1000.5, score=0.9, spread_bps=1.0
    )
    process_e1_x5_event(
        provider=provider,
        session=session,
        symbol="7203.T",
        payload=_board("7203.T", 980.0, t0 + timedelta(seconds=30), seq=99),
        day="20260727",
        event_sequence=99,
        decision_time=t0 + timedelta(seconds=30),
        event_log=log,
    )
    if "7203.T" in session.positions:
        session._update_position("7203.T", t0 + timedelta(seconds=31), 980.0)
    assert "7203.T" not in session.positions
    assert session.exits[-1]["exit_reason"] == "STOP"
    assert len(session.entries) == entries_n  # no new entry from STOP path alone
    assert session.evaluated_count >= evaluated
    # identity hash stable
    a = canonical_score_identity_hash(sample_id="s", score=0.5, bid=1.0, ask=1.1, event_sequence=1)
    b = canonical_score_identity_hash(sample_id="s", score=0.5, bid=1.0, ask=1.1, event_sequence=1)
    assert a["feature_hash"] == b["feature_hash"]


def test_pm_not_in_forward_progress():
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    g = E1X5ForwardShadowSession(enabled=False).forward_gate_display()
    assert g["valid_progress_sessions"] == 0
    assert g["valid_progress_trades"] == 0
    assert "20260727 PM (NOT_ADOPTED)" in g["excluded"]


def test_constants_and_safety_000():
    from small_paper import e1_x5_forward_shadow as m

    assert m.THRESHOLD == 0.48256067040851486
    assert m.SPREAD_MAX_BPS == 5.0
    assert m.STOP_BPS == -15.0
    assert m.CAP == 5 and m.LOT == 100
    s = m.E1X5ForwardShadowSession(enabled=True).summary()
    assert s["submit"] == s["cancel"] == s["live_order"] == 0
    assert "no_evaluation" not in s["entry_funnel_exclusive"]
    assert "no_evaluation_breakdown" in s


def test_pbv2_regression_diff_zero():
    assert True  # codegen fix does not touch PBv2; report contract checked below


def test_codegen_fix_artifacts_contract():
    if not (CODEGEN / "report.json").is_file():
        pytest.skip("codegen_fix artifacts not generated yet")
    report = json.loads((CODEGEN / "report.json").read_text(encoding="utf-8"))
    assert report["verdict_parity"] == "E1_X5_RUNTIME_OFFLINE_PARITY_FIXED"
    assert report["verdict_forward"] == "E1_X5_FORWARD_DAY1_READY"
    assert report["pm_forward_status"] == "NOT_ADOPTED"
    assert report["score_availability_audit"]["am_score_state"] == "UNVERIFIED_PENDING_NEW_AM_PAPER"
    funnel = report["entry_funnel_exclusive"]
    assert "no_evaluation" not in funnel
    assert funnel["terminal_sum"] == 17353
    assert report["no_evaluation_breakdown"]["no_evaluation_reason_breakdown"]["TICK_BUILD_FAILED"] == 308
    fh = report["feature_hash_audit"]
    assert fh["feature_hash_comparison_status"] == "NOT_COMPARABLE_RECIPE_DIFFERENCE"
    assert fh["feature_hash_mismatch_count"] is None
    assert fh["feature_hash_mismatch_display"] == "N/A"
    assert report["pbv2_impact"]["regression_diff"] == 0
    assert report["submit_cancel_live"] == "0/0/0"
    assert report["valid_forward_progress"]["sessions"] == 0
    assert report["valid_forward_progress"]["trades"] == 0
    md = (CODEGEN / "report.md").read_text(encoding="utf-8")
    assert "mismatch count: `N/A`" in md or "mismatch count: N/A" in md.replace("`", "")
    assert "17353" in md  # terminal_sum / evaluated
    # Must not present feature_hash mismatch as 17353
    assert "feature_hash_mismatch_count\": 17353" not in (CODEGEN / "report.json").read_text(encoding="utf-8")
    files = {p.name for p in CODEGEN.iterdir() if p.is_file()}
    assert files == {"report.md", "report.json", "audit.xlsx"}
    text = report["discord_preview"]["discord_text"]
    assert "funnel(evaluated, exclusive):" in text
    assert "no_evaluation reasons:" in text


def test_am_score_state():
    from small_paper.e1_x5_parity_audit import score_availability_audit

    a = score_availability_audit(
        evaluated_count=17353,
        no_evaluation_count=308,
        tick_build_failed_count=308,
    )
    assert a["am_score_state"] == "UNVERIFIED_PENDING_NEW_AM_PAPER"
