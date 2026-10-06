"""Pin closed V4 identity. Do not RCA coverage. Do not copy V4 PnL."""
from __future__ import annotations

import json
from typing import Any

from research.new_full_strategy_architecture_inventory_and_freeze_v3 import (
    PINNED_V4_FILL_DAY_N,
    PINNED_V4_INTEGRITY_PASS_N,
    PINNED_V4_INTEGRITY_TOTAL_N,
    PINNED_V4_TRADE_N,
    PINNED_V4_TRADES_PER_DAY,
    REQUIRED_V4_VERDICT,
)
from research.new_full_strategy_architecture_inventory_and_freeze_v3.isolation import RESEARCH_ROOT

V4_REPORT = RESEARCH_ROOT / "new_full_strategy_v2_implementation_and_dev_eval_v1" / "report.json"


def load_v4_report() -> dict[str, Any]:
    if not V4_REPORT.is_file():
        return {}
    return json.loads(V4_REPORT.read_text(encoding="utf-8"))


def pin_v4() -> dict[str, Any]:
    prev = load_v4_report()
    d = dict(prev.get("decision") or {})
    cov = dict(prev.get("coverage") or {})
    integ = dict(prev.get("integrity") or {})
    verdict = str(d.get("VERDICT") or prev.get("VERDICT") or "")
    trade_n = int(cov.get("TRADE_N") or -1)
    fill_days = int(cov.get("fill_day_n") or cov.get("TRADING_DAY_WITH_FILL_N") or -1)
    tpd = float(cov.get("trades_per_day") or -1.0)
    pass_n = int(integ.get("pass_n") or d.get("INTEGRITY_PASS_N") or -1)
    total_n = int(integ.get("total_n") or d.get("INTEGRITY_TOTAL_N") or -1)
    ok = (
        verdict == REQUIRED_V4_VERDICT
        and trade_n == int(PINNED_V4_TRADE_N)
        and fill_days == int(PINNED_V4_FILL_DAY_N)
        and abs(tpd - float(PINNED_V4_TRADES_PER_DAY)) < 1e-9
        and pass_n == int(PINNED_V4_INTEGRITY_PASS_N)
        and total_n == int(PINNED_V4_INTEGRITY_TOTAL_N)
    )
    return {
        "ok": bool(ok),
        "V4_CLOSED": True,
        "V4_RCA_RUN": False,
        "V4_RETUNE": False,
        "V4_VERDICT": verdict,
        "REQUIRED_V4_VERDICT": REQUIRED_V4_VERDICT,
        "TRADE_N": int(PINNED_V4_TRADE_N),
        "FILL_DAY_N": int(PINNED_V4_FILL_DAY_N),
        "TRADES_PER_DAY": float(PINNED_V4_TRADES_PER_DAY),
        "INTEGRITY": f"{int(PINNED_V4_INTEGRITY_PASS_N)}/{int(PINNED_V4_INTEGRITY_TOTAL_N)} PASS",
        "G1_G6": "NOT_DECIDED",
        "COVERAGE_RCA_RUN": False,
        "observed_verdict": verdict,
        "observed_trade_n": trade_n,
        "observed_fill_day_n": fill_days,
        "observed_trades_per_day": tpd,
        "observed_integrity": f"{pass_n}/{total_n}",
        "ARCHITECTURE_ID": "HTF5_MA_RESISTANCE_EPISODE_VOL_X1_Z_SUPPORT_FAIL",
        "FULL_STRATEGY_SPEC_SHA256_V4": "65682e47ed10d6861c2d22a379f4fd11149b22881d961634ae27d0e61e00f72b",
    }
