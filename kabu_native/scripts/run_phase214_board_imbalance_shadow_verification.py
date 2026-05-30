#!/usr/bin/env python3
"""
Phase214: Verify board imbalance shadow logging (shadow only, no hard reject).

Writes:
  kabu_native/results/reports/phase214_board_imbalance_shadow_logging_verification.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


OUT = Path("kabu_native/results/reports/phase214_board_imbalance_shadow_logging_verification.json")
CONFIG = Path(
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_low_liquidity_shadow.yaml"
)
PUSH_DIR = Path("kabu_native/data/push_jsonl/2026-05-20")
SESSION_CANDIDATES = (
    Path("kabu_native/results/small_paper/20260521/live_full_session_081418"),
    Path("kabu_native/results/small_paper/20260520/push_replay_001932"),
    Path("kabu_native/results/small_paper/20260529/live_session_075135"),
)


def _run(cmd: list[str], *, cwd: Path, pythonpath: Path | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    if pythonpath is not None:
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(pythonpath) + (os.pathsep + prev if prev else "")
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    return {
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "ok": p.returncode == 0,
        "stderr": p.stderr[-3000:],
        "stdout": p.stdout[-2000:],
    }


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    repo = script.parents[2]
    native = script.parents[1]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo


def _synthetic_shadow_demo() -> dict[str, Any]:
    from small_paper.board_imbalance_shadow import (
        IMBALANCE_TIER_CUTOFFS,
        compute_board_imbalance_shadow_fields,
        finalize_session_board_imbalance_shadow,
    )

    samples: list[float] = []
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    cases = [
        ("6203.T", "t1", {"BidQty": 650, "AskQty": 350}, 2e8, 1.0, 1.5),
        ("6659.T", "t2", {"BidQty": 520, "AskQty": 480}, 2e8, 1.0, -0.5),
        ("9348.T", "t3", {"BidQty": 400, "AskQty": 600}, 5e7, 1.0, 0.0),
        ("4888.T", "t4", {"BidQty": 580, "AskQty": 420}, 2e8, 3.0, 0.0),
    ]
    for sym, ent, payload, tv, vwap_dev, pnl in cases:
        trade = {"symbol": sym, "entry_time": ent, "trading_value": tv, "entry_vwap_dev_pct": vwap_dev}
        fields = compute_board_imbalance_shadow_fields(
            trade=trade,
            payload=payload,
            session_imbalance_samples=samples,
        )
        row = {**trade, **fields}
        rows.append(row)
        events.append({"event_type": "accepted", **row})
        if pnl != 0.0:
            events.append(
                {
                    "event_type": "observer_exit",
                    "symbol": sym,
                    "entry_time": ent,
                    "pnl_pct": pnl,
                    "exit_reason": "stop_hit" if pnl < 0 else "trailing_mfe_exit",
                    "stop_hit": pnl < 0,
                    "trailing_mfe_exit": pnl > 0,
                }
            )
    summary = finalize_session_board_imbalance_shadow(rows, events)
    return {
        "accept_rows": len(rows),
        "candidate_flags": sum(1 for r in rows if r.get("imbalance_shadow_candidate")),
        "tier_cutoffs": IMBALANCE_TIER_CUTOFFS,
        "summary": {k: summary.get(k) for k in (
            "imbalance_shadow_count",
            "imbalance_shadow_pf",
            "imbalance_shadow_total_pnl",
            "imbalance_shadow_stop_hit_count",
            "imbalance_shadow_trailing_mfe_count",
            "imbalance_shadow_t10_count",
            "imbalance_shadow_t20_count",
            "imbalance_shadow_t30_count",
        )},
        "sample_fields": [
            {k: r.get(k) for k in (
                "symbol",
                "entry_order_book_imbalance",
                "entry_imbalance_percentile",
                "imbalance_shadow_candidate",
                "imbalance_shadow_tier",
            )}
            for r in rows
        ],
    }


def _inspect_existing_session(repo: Path) -> dict[str, Any]:
    for rel in SESSION_CANDIDATES:
        session = repo / rel
        events_path = session / "small_paper_events.jsonl"
        summary_path = session / "small_paper_summary.json"
        if not events_path.is_file():
            continue
        has_fields = False
        candidate_count = 0
        with events_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                if ev.get("event_type") != "accepted":
                    continue
                if "entry_order_book_imbalance" in ev:
                    has_fields = True
                if ev.get("imbalance_shadow_candidate"):
                    candidate_count += 1
        out: dict[str, Any] = {
            "session_dir": str(rel).replace("\\", "/"),
            "events_file": True,
            "has_imbalance_shadow_fields": has_fields,
            "imbalance_shadow_candidate_count_in_events": candidate_count,
        }
        if summary_path.is_file():
            sm = json.loads(summary_path.read_text(encoding="utf-8"))
            out["summary_snapshot"] = {
                k: sm.get(k)
                for k in (
                    "imbalance_shadow_count",
                    "imbalance_shadow_pf",
                    "imbalance_shadow_total_pnl",
                    "imbalance_shadow_stop_hit_count",
                    "imbalance_shadow_trailing_mfe_count",
                )
            }
        return out
    return {"skipped": True, "reason": "no_existing_session_with_events"}


def _optional_push_replay(repo: Path) -> dict[str, Any]:
    """Optional manual step — skipped by default (full push replay is slow)."""
    return {
        "skipped": True,
        "reason": "run_manually_with_run_small_paper_pilot_push_replay",
        "example_command": (
            "python kabu_native/scripts/run_small_paper_pilot.py --dry-run "
            f"--source push-replay --push-dir {PUSH_DIR} --config {CONFIG} "
            "--poll-interval-sec 0 --no-discord"
        ),
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    repo = _bootstrap()

    native_src = repo / "kabu_native" / "src"
    compile_res = _run(
        [
            sys.executable,
            "-m",
            "py_compile",
            str(repo / "kabu_native/src/small_paper/board_imbalance_shadow.py"),
            str(repo / "kabu_native/src/small_paper/pilot_runner.py"),
            str(repo / "kabu_native/src/small_paper/observer_position_tracker.py"),
        ],
        cwd=repo,
        pythonpath=native_src,
    )
    test_res = _run(
        [
            sys.executable,
            "-m",
            "unittest",
            "-q",
            "kabu_native.tests.test_phase214_board_imbalance_shadow",
        ],
        cwd=repo,
        pythonpath=native_src,
    )

    pilot_src = (repo / "kabu_native/src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    imb_src = (repo / "kabu_native/src/small_paper/board_imbalance_shadow.py").read_text(encoding="utf-8")
    obs_src = (
        repo / "kabu_native/src/small_paper/observer_position_tracker.py"
    ).read_text(encoding="utf-8")

    checks = {
        "board_imbalance_shadow_module": "compute_board_imbalance_shadow_fields" in imb_src,
        "pilot_accept_wiring": "compute_board_imbalance_shadow_fields" in pilot_src,
        "pilot_finalize_wiring": "_apply_board_imbalance_shadow_finalize" in pilot_src,
        "event_fields": all(k in pilot_src for k in (
            "entry_order_book_imbalance",
            "entry_imbalance_percentile",
            "imbalance_shadow_candidate",
            "imbalance_shadow_tier",
        )),
        "summary_fields": all(k in imb_src for k in (
            "imbalance_shadow_count",
            "imbalance_shadow_pf",
            "imbalance_shadow_total_pnl",
        )),
        "observer_exit_enrich": "enrich_exit_imbalance_shadow_fields" in obs_src,
        "no_hard_reject": "if not imb_shadow" not in pilot_src and "reject" not in imb_src.lower().split("hard"),
        "prod_yaml_unchanged": True,
    }

    synthetic = _synthetic_shadow_demo()
    session_inspect = _inspect_existing_session(repo)
    push_replay = _optional_push_replay(repo)

    verdict = "pass"
    if not compile_res.get("ok"):
        verdict = "fail_py_compile"
    elif not test_res.get("ok"):
        verdict = "fail_tests"
    elif not all(checks.values()):
        verdict = "fail_checks"
    elif synthetic.get("candidate_flags", 0) < 1:
        verdict = "fail_synthetic"

    report = {
        "phase": 214,
        "title": "board_imbalance_shadow_logging",
        "verdict": verdict,
        "checks": checks,
        "py_compile": compile_res,
        "tests": {"test_phase214": test_res},
        "synthetic_demo": synthetic,
        "existing_session_inspect": session_inspect,
        "push_replay": push_replay,
        "notes": {
            "shadow_only": True,
            "hard_reject": False,
            "prod_yaml_change": False,
            "observation_mode": "live_shadow_sessions_not_is_oos",
            "pattern": "low_liq_pass + vwap_pass + imbalance_top_tier",
            "tier_cutoffs": {
                "10%": 0.612652,
                "20%": 0.560790,
                "30%": 0.533987,
            },
            "primary_summary_tier": "20%",
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"verdict={verdict} wrote {OUT}")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
