#!/usr/bin/env python3
"""
Phase179c: Verify low_liquidity_shadow YAML is non-prod and logging-only.

Writes:
- kabu_native/results/reports/phase179c_low_liquidity_shadow_yaml_verification.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


CFG_REL = "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_low_liquidity_shadow.yaml"
OUT = Path("kabu_native/results/reports/phase179c_low_liquidity_shadow_yaml_verification.json")


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def main() -> int:
    repo_root, _native_root = _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    from small_paper.config import load_pilot_config

    cfg_path = repo_root / CFG_REL
    cfg = load_pilot_config(cfg_path)

    checks: dict[str, Any] = {
        "config_path": CFG_REL,
        "order_enabled_false": (not bool(getattr(cfg, "order_enabled", True))),
        "paper_only_true": bool(getattr(cfg, "paper_only", False)),
        "shadow_only_true": bool(getattr(cfg, "shadow_only", False)),
        "structural_exit_policy_trailing_mfe": str(getattr(cfg, "structural_exit_policy", "")) == "combined_structural_exit_v1_trailing_mfe_shadow",
        "low_liquidity_shadow_enabled": bool(getattr(cfg, "low_liquidity_shadow_enabled", False)),
        "low_liquidity_shadow_trading_value_min": float(getattr(cfg, "low_liquidity_shadow_trading_value_min", 0.0) or 0.0),
        "low_liquidity_shadow_turnover_proxy_min": float(getattr(cfg, "low_liquidity_shadow_turnover_proxy_min", 0.0) or 0.0),
    }
    # fixed expected values
    checks["low_liquidity_shadow_trading_value_min_ok"] = (
        abs(checks["low_liquidity_shadow_trading_value_min"] - 100000000.0) < 1e-6
    )
    checks["low_liquidity_shadow_turnover_proxy_min_ok"] = (
        abs(checks["low_liquidity_shadow_turnover_proxy_min"] - 0.002) < 1e-9
    )

    verdict = "ok"
    required_true = [
        "order_enabled_false",
        "paper_only_true",
        "shadow_only_true",
        "structural_exit_policy_trailing_mfe",
        "low_liquidity_shadow_enabled",
        "low_liquidity_shadow_trading_value_min_ok",
        "low_liquidity_shadow_turnover_proxy_min_ok",
    ]
    if not all(bool(checks.get(k)) for k in required_true):
        verdict = "fail"

    report = {
        "phase": "179c",
        "verdict": verdict,
        "checks": checks,
        "notes": [
            "This YAML enables low_liquidity_shadow logging only. It does not change entry gate behavior (no hard reject).",
            "To observe results, run a live shadow session and then run Phase179b observation script.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

