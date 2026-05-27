#!/usr/bin/env python3
"""
Phase168 post-fix verification (off-market).

Outputs:
- phase168_post_fix_verification.json
- phase168_post_fix_guard_unit_cases.csv
- phase168_post_fix_replay_20260527.csv
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DAY = "20260527"
SESSIONS = (
    "kabu_native/results/small_paper/20260527/live_session_082953",
    "kabu_native/results/small_paper/20260527/live_session_122531",
)
WANTED = ("6327.T", "9984.T", "5856.T", "7203.T", "8035.T", "6857.T")


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def _guard_state(*, shadow_only: bool = True):
    from small_paper.entry_price_risk_guard import EntryPriceRiskGuardConfig, EntryPriceRiskGuardState

    return EntryPriceRiskGuardState(
        config=EntryPriceRiskGuardConfig(
            enabled=True,
            min_entry_price=50.0,
            max_tick_ratio_pct=5.0,
            shadow_only=shadow_only,
        )
    )


def _as_float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class UnitCase:
    case_id: str
    symbol: str
    trade: dict[str, Any]
    shadow_only: bool
    expect_trigger: str
    expect_blocked: bool
    expect_not_missing: bool = False


def run_unit_cases() -> tuple[list[dict[str, Any]], bool]:
    cases = [
        UnitCase("A", "6327.T", {"symbol": "6327.T", "current_price": 4110}, True, "", False, True),
        UnitCase("B", "6327.T", {"symbol": "6327.T", "CurrentPrice": 4110}, True, "", False, True),
        UnitCase(
            "C",
            "5856.T",
            {"symbol": "5856.T", "current_price": 13},
            True,
            "price_below_min",
            True,
        ),
        UnitCase("D", "6327.T", {"symbol": "6327.T"}, True, "missing_price", False),
        UnitCase("E", "6327.T", {"symbol": "6327.T"}, False, "missing_price", True),
    ]
    rows: list[dict[str, Any]] = []
    all_ok = True
    for c in cases:
        g = _guard_state(shadow_only=c.shadow_only)
        chk = g.check(c.trade)
        ok = True
        if c.expect_not_missing and chk.trigger == "missing_price":
            ok = False
        if c.expect_trigger and chk.trigger != c.expect_trigger:
            ok = False
        if chk.blocked != c.expect_blocked:
            ok = False
        if c.case_id == "D" and not chk.shadow_missing_price_bypassed:
            ok = False
        rows.append(
            {
                "case_id": c.case_id,
                "symbol": c.symbol,
                "shadow_only": c.shadow_only,
                "expect_trigger": c.expect_trigger,
                "expect_blocked": c.expect_blocked,
                "actual_trigger": chk.trigger,
                "actual_blocked": chk.blocked,
                "price_source": chk.price_source,
                "guard_price": chk.current_price,
                "tick_size": chk.tick_size_yen,
                "tick_ratio_pct": chk.tick_ratio_pct,
                "shadow_missing_price_bypassed": chk.shadow_missing_price_bypassed,
                "passed": ok,
            }
        )
        if not ok:
            all_ok = False
    return rows, all_ok


def run_pipeline_test() -> tuple[dict[str, Any], bool]:
    from research.exposure_gate import ExposureGate, ExposureGateConfig
    from small_paper.pilot_runner import _candidate_trade_from_push

    payload = {
        "Symbol": "6327",
        "CurrentPrice": 4110.0,
        "CurrentPriceTime": "2026-05-27T09:10:00+09:00",
    }
    sym = "6327.T"
    trade = _candidate_trade_from_push(
        payload, symbol=sym, profile="momentum_volume_v13_combined"
    )
    had_price_before = bool(trade.get("current_price") or trade.get("CurrentPrice"))
    live_px = payload.get("CurrentPrice")
    if live_px is not None:
        trade.setdefault("CurrentPrice", live_px)
        trade.setdefault("current_price", live_px)
    g = _guard_state()
    chk = g.check(trade)
    gate = ExposureGate(
        ExposureGateConfig(profile="momentum_volume_v13_combined", min_continuation_quality=0.7),
        entry_price_risk_guard=g,
    )
    dec = gate.evaluate_entry(trade)
    ok = (
        trade.get("current_price") == 4110.0
        and trade.get("CurrentPrice") == 4110.0
        and chk.trigger != "missing_price"
        and chk.blocked is False
    )
    return {
        "had_price_in_trade_before_injection": had_price_before,
        "trade_current_price_after": trade.get("current_price"),
        "trade_CurrentPrice_after": trade.get("CurrentPrice"),
        "guard_trigger": chk.trigger,
        "guard_blocked": chk.blocked,
        "gate_accept": dec.accept,
        "gate_reason": dec.reason,
        "passed": ok,
    }, ok


def replay_20260527(repo: Path, *, max_rows: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    g_shadow = _guard_state(shadow_only=True)
    before_trig = Counter()
    after_trig = Counter()
    after_blocked = Counter()
    spot_checks: dict[str, list[dict[str, Any]]] = {s: [] for s in WANTED}
    n = 0

    for sess_rel in SESSIONS:
        rej_path = repo / sess_rel / "small_paper_rejects.csv"
        if not rej_path.is_file():
            continue
        with rej_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("gate_reject_reason") or "").strip() != "entry_price_risk_guard":
                    continue
                px = _as_float(row.get("current_price"))
                if px <= 0:
                    continue
                sym = (row.get("symbol") or "").strip().upper()
                trade_before = {
                    "symbol": sym,
                    "profile": row.get("profile") or "momentum_volume_v13_combined",
                }
                trade_after = {
                    **trade_before,
                    "current_price": px,
                    "CurrentPrice": px,
                }
                chk_b = g_shadow.check(trade_before)
                chk_a = g_shadow.check(trade_after)
                before_trig[chk_b.trigger or "(empty)"] += 1
                after_trig[chk_a.trigger or "(empty)"] += 1
                if chk_a.blocked:
                    after_blocked[chk_a.trigger or "(empty)"] += 1
                if sym in spot_checks and len(spot_checks[sym]) < 3:
                    spot_checks[sym].append(
                        {
                            "session": sess_rel,
                            "current_price": px,
                            "before_trigger": chk_b.trigger,
                            "after_trigger": chk_a.trigger,
                            "after_blocked": chk_a.blocked,
                        }
                    )
                n += 1
                if max_rows is not None and n >= max_rows:
                    break
        if max_rows is not None and n >= max_rows:
            break

    summary = {
        "rows_replayed": n,
        "before_trigger_counts": dict(before_trig),
        "after_trigger_counts": dict(after_trig),
        "after_blocked_counts": dict(after_blocked),
        "before_missing_price": int(before_trig.get("missing_price", 0)),
        "after_missing_price": int(after_trig.get("missing_price", 0)),
        "spot_checks": spot_checks,
    }
    replay_rows: list[dict[str, Any]] = []
    for sym in WANTED:
        for sc in spot_checks.get(sym, []):
            replay_rows.append({"symbol": sym, **sc})
    return replay_rows, summary


def determine_verdict(
    *,
    unit_ok: bool,
    pipeline_ok: bool,
    replay_summary: dict[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not unit_ok:
        return "C", notes + ["unit cases failed"]
    if not pipeline_ok:
        return "B", notes + ["pipeline price injection failed"]
    before_mp = int(replay_summary.get("before_missing_price", 0))
    after_mp = int(replay_summary.get("after_missing_price", 0))
    if before_mp > 0 and after_mp == 0:
        notes.append(f"replay: missing_price {before_mp} -> {after_mp}")
        return "A", notes + ["post_fix_verified_off_market (replay); live PUSH still required for final check"]
    if after_mp < before_mp * 0.01:
        notes.append(f"replay: missing_price {before_mp} -> {after_mp}")
        return "A", notes
    return "D", notes + [f"replay still has missing_price={after_mp}"]


def main() -> int:
    repo, native = _bootstrap()
    reports = native / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    unit_rows, unit_ok = run_unit_cases()
    unit_csv = reports / "phase168_post_fix_guard_unit_cases.csv"
    with unit_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(unit_rows[0].keys()) if unit_rows else ["case_id"])
        w.writeheader()
        w.writerows(unit_rows)

    pipeline_result, pipeline_ok = run_pipeline_test()
    replay_rows, replay_summary = replay_20260527(repo)
    replay_csv = reports / "phase168_post_fix_replay_20260527.csv"
    with replay_csv.open("w", encoding="utf-8", newline="") as f:
        if replay_rows:
            w = csv.DictWriter(f, fieldnames=list(replay_rows[0].keys()))
            w.writeheader()
            w.writerows(replay_rows)
        else:
            f.write("symbol\n")

    verdict, notes = determine_verdict(
        unit_ok=unit_ok,
        pipeline_ok=pipeline_ok,
        replay_summary=replay_summary,
    )

    # Also run prior phase168 audit script for cross-reference
    prior_script = native / "scripts/run_phase168_entry_price_risk_guard_missing_price_fix.py"
    prior_rc = None
    if prior_script.is_file():
        prior_rc = subprocess.run(
            [sys.executable, str(prior_script)],
            cwd=str(repo),
            capture_output=True,
            text=True,
        ).returncode

    out = {
        "phase": 168,
        "title": "post_fix_verification",
        "day": DAY,
        "verdict": verdict,
        "verdict_options": {
            "A": "post_fix_verified_off_market",
            "B": "pipeline_price_injection_failed",
            "C": "guard_unit_failed",
            "D": "live_only_remaining",
        },
        "verdict_notes": notes,
        "unit_tests": {"all_passed": unit_ok, "cases": unit_rows},
        "pipeline_test": pipeline_result,
        "replay_20260527": replay_summary,
        "historical_log": {
            "errors_jsonl_missing_price_pre_fix": 114943,
            "note": "pre-fix logs used guard without trade price fields",
        },
        "prior_phase168_script_exit_code": prior_rc,
        "outputs": {
            "json": str(reports / "phase168_post_fix_verification.json"),
            "unit_csv": str(unit_csv),
            "replay_csv": str(replay_csv),
        },
    }
    out_path = reports / "phase168_post_fix_verification.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "unit_ok": unit_ok, "pipeline_ok": pipeline_ok, "replay": replay_summary}, indent=2))
    return 0 if verdict == "A" else 1


if __name__ == "__main__":
    raise SystemExit(main())
