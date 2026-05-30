#!/usr/bin/env python3
"""
Phase183: Verify extended_entry_shadow logging (shadow only, no hard reject).

Writes:
  kabu_native/results/reports/phase183_extended_entry_shadow_logging_verification.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


OUT = Path("kabu_native/results/reports/phase183_extended_entry_shadow_logging_verification.json")
SESSION = Path("kabu_native/results/small_paper/20260529/live_session_075135")
PUSH_DIR = Path("kabu_native/data/push_jsonl/2026-05-29")
PHASE182 = Path("kabu_native/results/reports/phase182_extended_entry_analysis.json")
CONFIG = Path(
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_low_liquidity_shadow.yaml"
)


def _run(cmd: list[str], *, cwd: Path) -> dict[str, Any]:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return {
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "ok": p.returncode == 0,
        "stderr": p.stderr[-3000:],
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


def _offline_replay_flags(repo: Path) -> dict[str, Any]:
    """Recompute shadow flags from 5/29 push + accepted events (Phase182 parity check)."""
    from research.phase181_entry_expectancy_review import _load_events, _pair_trades, _parse_ts
    from small_paper.extended_entry_shadow import (
        append_price_tick,
        compute_entry_shadow_fields,
        tick_ts_from_payload,
    )
    import json as _json

    events = _load_events(repo / SESSION)
    pairs = _pair_trades(events)
    cache: dict[str, list[tuple[float, float]]] = {}
    push_dir = repo / PUSH_DIR
    samples: list[float] = []
    flags = 0
    hq = 0
    for acc, _ex in pairs:
        sym = str(acc.get("symbol") or "")
        path = push_dir / f"{sym}.jsonl"
        if sym not in cache and path.is_file():
            ring: list[tuple[float, float]] = []
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = _json.loads(line)
                    payload = rec.get("payload") or {}
                    try:
                        px = float(payload.get("CurrentPrice") or 0)
                    except (TypeError, ValueError):
                        px = 0.0
                    if px > 0:
                        append_price_tick(ring, ts=_parse_ts(str(rec.get("recorded_at") or "")), px=px)
            cache[sym] = ring
        ring = cache.get(sym, [])
        ent = str(acc.get("entry_time") or "")
        ent_ts = _parse_ts(ent)
        payload = {"CurrentPrice": acc.get("current_price"), "VWAP": None, "HighPrice": None}
        if path.is_file():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = _json.loads(line)
                    ts = _parse_ts(str(rec.get("recorded_at") or ""))
                    if ts > ent_ts:
                        break
                    pld = rec.get("payload") or {}
                    payload = {
                        "CurrentPrice": pld.get("CurrentPrice") or payload.get("CurrentPrice"),
                        "VWAP": pld.get("VWAP"),
                        "HighPrice": pld.get("HighPrice"),
                    }
        mom = acc.get("momentum_continuation_score")
        if mom is not None:
            try:
                samples.append(float(mom))
            except (TypeError, ValueError):
                pass
        shadow = compute_entry_shadow_fields(
            trade=acc,
            payload=payload,
            price_ring=[t for t in ring if t[0] <= ent_ts],
            entry_ts=ent_ts,
            session_momentum_samples=samples,
        )
        if shadow.get("extended_entry_shadow_flag"):
            flags += 1
        if shadow.get("high_quality_low_momentum_shadow_flag"):
            hq += 1
    total = len(pairs)
    return {
        "paired_trades": total,
        "extended_entry_shadow_flag_count": flags,
        "extended_entry_shadow_rate": round(flags / max(1, total), 4),
        "high_quality_low_momentum_count": hq,
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    repo = _bootstrap()

    compile_res = _run(
        [
            sys.executable,
            "-m",
            "py_compile",
            str(repo / "kabu_native/src/small_paper/extended_entry_shadow.py"),
            str(repo / "kabu_native/src/small_paper/pilot_runner.py"),
            str(repo / "kabu_native/src/small_paper/observer_position_tracker.py"),
        ],
        cwd=repo,
    )
    t183 = _run(
        [sys.executable, "-m", "unittest", "-q", "kabu_native.tests.test_phase183_extended_entry_shadow"],
        cwd=repo,
    )
    t180 = _run(
        [sys.executable, "-m", "unittest", "-q", "kabu_native.tests.test_phase180_logging"],
        cwd=repo,
    )

    src = (repo / "kabu_native/src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    checks = {
        "extended_entry_shadow_module_import": "from small_paper.extended_entry_shadow import compute_entry_shadow_fields"
        in src,
        "event_fields_extended_shadow": "extended_entry_shadow_flag" in src
        and "entry_rise_5min_pct" in src,
        "summary_counters": "extended_entry_shadow_count" in (
            repo / "kabu_native/src/small_paper/extended_entry_shadow.py"
        ).read_text(encoding="utf-8")
        and "_extended_shadow_summary_fields" in src,
        "observer_exit_enrich": "enrich_exit_shadow_fields" in (
            repo / "kabu_native/src/small_paper/observer_position_tracker.py"
        ).read_text(encoding="utf-8"),
    }

    offline = _offline_replay_flags(repo)
    phase182_ref: dict[str, Any] = {}
    if PHASE182.is_file():
        p182 = json.loads(PHASE182.read_text(encoding="utf-8"))
        band = (p182.get("quality_band_expectancy") or {}).get("0.75_0.80") or {}
        phase182_ref = {
            "quality_0.75_0.80_extended_rate_phase182": band.get("extended_entry_rate"),
            "quality_0.75_0.80_pf_phase182": band.get("profit_factor"),
        }

    push_replay: dict[str, Any] = {
        "skipped": True,
        "reason": "use_offline_recompute_for_20260529_am; full push_replay is optional manual step",
    }
    replay_summary: dict[str, Any] = {}

    parity_ok = offline.get("extended_entry_shadow_rate", 0) >= 0.45

    verdict = "pass"
    if not compile_res.get("ok"):
        verdict = "fail_py_compile"
    elif not t183.get("ok"):
        verdict = "fail_tests"
    elif not all(checks.values()):
        verdict = "fail_checks"
    elif not parity_ok:
        verdict = "fail_phase182_parity"

    report = {
        "phase": 183,
        "verdict": verdict,
        "checks": checks,
        "py_compile": compile_res,
        "tests": {"test_phase183": t183, "test_phase180": t180},
        "offline_20260529_am": offline,
        "phase182_reference": phase182_ref,
        "push_replay": push_replay,
        "push_replay_summary": {
            k: replay_summary.get(k)
            for k in (
                "extended_entry_shadow_count",
                "high_quality_low_momentum_shadow_count",
                "extended_plus_early_adverse_shadow_count",
                "extended_entry_shadow_pnl_estimate",
                "extended_entry_shadow_stop_hit_count",
                "extended_entry_shadow_trailing_mfe_count",
            )
            if replay_summary
        },
        "notes": {
            "shadow_only": True,
            "hard_reject": False,
            "fixed_thresholds": {
                "rise_5min_pct": 1.5,
                "vwap_dev_pct": 2.5,
                "rolling_mfe_pct": 1.5,
                "momentum_low": 0.3,
                "quality_high": 0.75,
            },
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"verdict={verdict} wrote {OUT}")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
