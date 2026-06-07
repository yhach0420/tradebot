#!/usr/bin/env python3
"""Phase315: 100-share yen expectancy metrics report."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase315_100share_yen_expectancy_report.json"
SRC = REPO / "kabu_native/src"


def _run_py_compile() -> dict[str, Any]:
    targets = [
        SRC / "replay/pnl_yen.py",
        SRC / "replay/metrics.py",
        REPO / "kabu_native/tests/test_phase315_pnl_yen_100.py",
    ]
    ok = True
    errors: list[str] = []
    for t in targets:
        r = subprocess.run([sys.executable, "-m", "py_compile", str(t)], capture_output=True, text=True)
        if r.returncode != 0:
            ok = False
            errors.append(f"{t}: {r.stderr.strip()}")
    return {"success": ok, "errors": errors}


def _run_unit_tests() -> dict[str, Any]:
    tests = ["kabu_native/tests/test_phase315_pnl_yen_100.py"]
    r = subprocess.run(
        [sys.executable, "-m", "unittest"] + tests,
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    return {
        "success": r.returncode == 0,
        "returncode": r.returncode,
        "tests": tests,
        "stdout_tail": "\n".join(r.stdout.splitlines()[-12:]),
        "stderr_tail": "\n".join(r.stderr.splitlines()[-8:]) if r.stderr else "",
    }


def main() -> int:
    for p in (SRC, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    from replay.metrics import _summary_block, trades_to_rows
    from replay.pnl_yen import compute_pnl_yen_100, summarize_pnl_yen_100

    sample_trades = [
        {
            "symbol": "9984.T",
            "trade_date": "2026-05-21",
            "entry_price": 8500.0,
            "exit_price": 8570.0,
            "pnl_pct": 0.823529,
            "side": "long",
        },
        {
            "symbol": "7203.T",
            "trade_date": "2026-05-21",
            "entry_price": 2800.0,
            "exit_price": 2772.0,
            "pnl_pct": -1.0,
            "side": "long",
        },
        {
            "symbol": "6758.T",
            "trade_date": "2026-05-22",
            "entry_price": 3200.0,
            "exit_price": 3184.0,
            "pnl_pct": 0.5,
            "side": "short",
        },
    ]

    rows = trades_to_rows(sample_trades)
    yen_summary = summarize_pnl_yen_100(sample_trades)
    aggregate = _summary_block(sample_trades)

    py_compile = _run_py_compile()
    unit_tests = _run_unit_tests()

    report = {
        "phase": 315,
        "title": "100share_yen_expectancy",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "change_summary": {
            "per_trade_field": "pnl_yen_100",
            "formula_long": "(exit_price - entry_price) * 100",
            "formula_short": "(entry_price - exit_price) * 100",
            "fees_tax_included": False,
            "entry_exit_logic_changed": False,
        },
        "expectancy_policy": {
            "primary_metric": "avg_pnl_yen_100",
            "secondary_metrics": ["total_pnl_yen_100", "profit_factor_yen_100"],
            "legacy_pct_metrics_retained": True,
        },
        "sample_trades": rows,
        "sample_yen_summary": yen_summary,
        "sample_aggregate_block": {
            k: aggregate[k]
            for k in (
                "trades",
                "total_pnl_pct",
                "avg_pnl_pct",
                "total_pnl_yen_100",
                "avg_pnl_yen_100",
                "gross_profit_yen_100",
                "gross_loss_yen_100",
                "profit_factor_yen_100",
                "max_win_yen_100",
                "max_loss_yen_100",
            )
        },
        "formula_checks": {
            "long_9984": compute_pnl_yen_100(8500.0, 8570.0) == 7000.0,
            "long_7203_loss": compute_pnl_yen_100(2800.0, 2772.0) == -2800.0,
            "short_6758": compute_pnl_yen_100(3200.0, 3184.0, side="short") == 1600.0,
        },
        "checks": {"py_compile": py_compile, "unit_tests": unit_tests},
        "verdict": {
            "pnl_yen_100_on_all_sample_trades": all("pnl_yen_100" in r for r in rows),
            "aggregate_has_yen_metrics": all(
                k in aggregate
                for k in (
                    "total_pnl_yen_100",
                    "avg_pnl_yen_100",
                    "gross_profit_yen_100",
                    "gross_loss_yen_100",
                    "profit_factor_yen_100",
                    "max_win_yen_100",
                    "max_loss_yen_100",
                )
            ),
            "primary_metric_is_avg_pnl_yen_100": True,
            "tests_passed": py_compile["success"] and unit_tests["success"],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"avg_pnl_yen_100={yen_summary['avg_pnl_yen_100']} "
        f"tests_ok={report['verdict']['tests_passed']}"
    )
    return 0 if report["verdict"]["tests_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
