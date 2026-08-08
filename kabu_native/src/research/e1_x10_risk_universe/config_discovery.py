"""Discover existing Paper/runtime risk config — never invent missing limits."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from research.e1_x6_provisional.util import sha256_file

from . import FRESHNESS_MAX_SEC

NATIVE = Path(__file__).resolve().parents[3]
PROD_YAML = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
PIN_FILE = NATIVE / "configs" / "production_config_sha256.pin"


def _get(cfg: dict[str, Any], key: str) -> Any:
    return cfg.get(key)


def discover_risk_config() -> dict[str, Any]:
    """Load canonical production Paper YAML and classify configured vs missing."""
    if not PROD_YAML.exists():
        return {
            "status": "RISK_BUDGET_NOT_CONFIGURED",
            "reason": "production paper YAML missing",
            "config_file": str(PROD_YAML),
            "rows": [],
        }
    raw = yaml.safe_load(PROD_YAML.read_text(encoding="utf-8")) or {}
    sha = sha256_file(PROD_YAML)
    pin = PIN_FILE.read_text(encoding="utf-8").strip() if PIN_FILE.exists() else None

    rows = []

    def add(key: str, value: Any, *, configured: bool, note: str = ""):
        rows.append({
            "config_file": str(PROD_YAML),
            "config_key": key,
            "value": value,
            "sha256": sha,
            "configured": configured,
            "note": note,
        })

    # Found keys
    add("max_concurrent_positions", _get(raw, "max_concurrent_positions"), configured=True,
        note="canonical concurrent position limit (alias of position_cap in runtime)")
    add("position_cap_mode", _get(raw, "position_cap_mode"), configured=True)
    add("position_cap_release", _get(raw, "position_cap_release"), configured=True)
    add("daily_loss_guard_pct", _get(raw, "daily_loss_guard_pct"), configured=True)
    add("daily_loss_guard_enabled", _get(raw, "daily_loss_guard_enabled"), configured=True)
    add("live_capital_check_enabled", _get(raw, "live_capital_check_enabled"), configured=True,
        note="capital from live wallet API at runtime — no fixed yen amount in YAML")
    add("entry_max_price_age_sec", _get(raw, "entry_max_price_age_sec"), configured=True)
    add("entry_max_board_age_sec", _get(raw, "entry_max_board_age_sec"), configured=True)
    add("entry_freshness_guard_enabled", _get(raw, "entry_freshness_guard_enabled"), configured=True)
    add("entry_price_risk_guard_min_entry_price", _get(raw, "entry_price_risk_guard_min_entry_price"),
        configured=True, note="price floor, not yen risk budget")
    add("entry_price_risk_guard_max_tick_ratio_pct", _get(raw, "entry_price_risk_guard_max_tick_ratio_pct"),
        configured=True, note="tick/price ratio %, not yen risk budget")

    # Explicitly missing — do not invent
    missing_keys = [
        ("per_trade_risk_limit_yen", "no yen per-trade risk limit in Paper YAML"),
        ("available_trading_capital_yen", "no fixed trading capital yen in YAML; live API only"),
        ("per_symbol_notional_limit_yen", "not configured"),
        ("buying_power_reserve_yen", "not configured"),
        ("position_cap", "key absent; use max_concurrent_positions"),
    ]
    for key, note in missing_keys:
        add(key, None, configured=False, note=note)

    per_trade = None  # not configured
    capital = None
    pos_cap = _get(raw, "max_concurrent_positions")

    freshness_price = float(_get(raw, "entry_max_price_age_sec") or FRESHNESS_MAX_SEC)
    freshness_board = float(_get(raw, "entry_max_board_age_sec") or FRESHNESS_MAX_SEC)

    budget_configured = per_trade is not None and capital is not None
    return {
        "status": "CONFIGURED" if budget_configured else "RISK_BUDGET_NOT_CONFIGURED",
        "config_file": str(PROD_YAML),
        "config_sha256": sha,
        "pin_sha256": pin,
        "pin_match": pin == sha if pin else None,
        "max_concurrent_positions": pos_cap,
        "position_cap_alias": pos_cap,
        "per_trade_risk_limit_yen": per_trade,
        "available_trading_capital_yen": capital,
        "per_symbol_notional_limit_yen": None,
        "buying_power_reserve_yen": None,
        "daily_loss_guard_pct": _get(raw, "daily_loss_guard_pct"),
        "live_capital_check_enabled": _get(raw, "live_capital_check_enabled"),
        "freshness_max_price_age_sec": freshness_price,
        "freshness_max_board_age_sec": freshness_board,
        "rows": rows,
        "note": "per_trade_risk_limit_yen and available_trading_capital_yen absent — do not invent",
    }
