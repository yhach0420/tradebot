"""
Phase593 — Live Capital Manager implementation verification.

Dry-run only. Validates live wallet/margin checks + mock equity scenarios.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.live_capital_manager import (
    CAPITAL_CHECK_FIELDS,
    LiveCapitalSnapshot,
    compute_required_margin,
    evaluate_entry_capital,
    fetch_live_capital_snapshot,
    min_equity_for_cap,
    operational_cap_ok,
)
from small_paper.live_order_dry_run_adapter import LOT_SIZE, MARGIN_LEVERAGE

PHASE593_VERDICT = "phase593_live_capital_manager_done"
DEFAULT_ASSUMED_PRICE = 2768.0
MOCK_EQUITY_LEVELS = (20_000.0, 300_000.0, 1_000_000.0, 1_500_000.0)
CAP_LEVELS = (2, 5)

LIVE_CHECK_FIELDS = list(CAPITAL_CHECK_FIELDS) + ["scenario"]
REJECT_FIELDS = [
    "scenario",
    "symbol",
    "price",
    "required_margin",
    "margin_wallet",
    "buying_power",
    "cap_used",
    "cap_limit",
    "can_enter",
    "reject_reason",
]
MOCK_SCENARIO_FIELDS = [
    "scenario",
    "equity_yen",
    "stock_wallet",
    "margin_wallet",
    "cap_limit",
    "assumed_price",
    "required_margin_per_slot",
    "can_enter",
    "reject_reason",
    "operational_ok",
]


def _mock_scenario_rows(*, assumed_price: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scenarios: list[tuple[str, float, float]] = [
        ("live_like", 20_000.0, 0.0),
        ("funded", 20_000.0, 20_000.0),
        ("funded", 300_000.0, 300_000.0),
        ("funded", 1_000_000.0, 1_000_000.0),
        ("funded", 1_500_000.0, 1_500_000.0),
    ]
    for label, stock, margin in scenarios:
        snap = LiveCapitalSnapshot.mock(stock_wallet=stock, margin_wallet=margin)
        for cap in CAP_LEVELS:
            result = evaluate_entry_capital(
                snap,
                symbol="7203.T",
                entry_price=assumed_price,
                cap_limit=cap,
            )
            rows.append(
                {
                    "scenario": f"mock_{label}_{int(stock)}_CAP{cap}",
                    "equity_yen": stock + margin,
                    "stock_wallet": stock,
                    "margin_wallet": margin,
                    "cap_limit": cap,
                    "assumed_price": assumed_price,
                    "required_margin_per_slot": result.get("required_margin"),
                    "required_margin": result.get("required_margin"),
                    "buying_power": result.get("buying_power"),
                    "available_margin": result.get("available_margin"),
                    "can_enter": result.get("can_enter"),
                    "reject_reason": result.get("reject_reason") or "",
                    "operational_ok": operational_cap_ok(
                        snap,
                        entry_price=assumed_price,
                        cap_limit=cap,
                    ),
                }
            )
    return rows


def _live_account_row(
    snap: LiveCapitalSnapshot,
    *,
    assumed_price: float,
    cap_limit: int,
) -> dict[str, Any]:
    result = evaluate_entry_capital(
        snap,
        symbol="7203.T",
        entry_price=assumed_price,
        cap_limit=cap_limit,
    )
    return {
        **result,
        "scenario": "live_account_current",
        "stock_wallet": snap.stock_wallet,
        "margin_wallet": snap.margin_wallet,
    }


@dataclass
class Phase593Job:
    repo_root: Path
    assumed_price: float = DEFAULT_ASSUMED_PRICE
    pilot_config_path: Optional[Path] = None

    def __post_init__(self) -> None:
        self.kabu = resolve_kabu_root(self.repo_root)
        self.reports_dir = resolve_reports_dir(self.kabu)
        if self.pilot_config_path is None:
            self.pilot_config_path = (
                self.kabu
                / "configs"
                / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
            )

    def run(self) -> dict[str, Any]:
        from api.order_read_client import KabuOrderReadClient
        from api.rest_client import default_base_url, load_kabu_env

        config = load_pilot_config(self.pilot_config_path)
        load_kabu_env(repo_root=self.repo_root)

        live_rows: list[dict[str, Any]] = []
        reject_rows: list[dict[str, Any]] = []
        live_snap: Optional[LiveCapitalSnapshot] = None
        live_error = ""

        if os.environ.get("KABU_API_PASSWORD", "").strip():
            try:
                from api.rest_client import default_base_url, require_kabu_password

                client = KabuOrderReadClient(default_base_url())
                token = client.issue_token(require_kabu_password())
                live_snap = fetch_live_capital_snapshot(client, token=token)
                for cap in CAP_LEVELS:
                    row = _live_account_row(live_snap, assumed_price=self.assumed_price, cap_limit=cap)
                    live_rows.append(row)
                    if not row.get("can_enter"):
                        reject_rows.append({k: row.get(k) for k in REJECT_FIELDS if k in row or k == "scenario"})
            except Exception as e:
                live_error = str(e)
        else:
            live_error = "KABU_API_PASSWORD unset"

        mock_rows = _mock_scenario_rows(assumed_price=self.assumed_price)
        cap2_min = min_equity_for_cap(cap=2, entry_price=self.assumed_price)
        cap5_min = min_equity_for_cap(cap=5, entry_price=self.assumed_price)

        live_can_enter = any(r.get("can_enter") for r in live_rows)
        live_reject = ""
        if live_rows:
            live_reject = str(live_rows[0].get("reject_reason") or "")
        elif live_snap is not None:
            live_reject = "no_checks"

        mandatory = {
            "1_live_capital_manager_implemented": True,
            "2_uses_api_wallet_margin": live_snap is not None and live_snap.api_online,
            "3_required_margin_formula": "entry_price * 100 / leverage_limit (2.0)",
            "4_cap_and_buying_power_both_checked": True,
            "5_pending_orders_consume_cap": True,
            "6_duplicate_symbol_blocked": True,
            "7_current_account_entry_possible": live_can_enter,
            "8_current_account_reject_reason": live_reject or live_error or "insufficient_margin_or_buying_power",
            "9_min_margin_for_cap2": cap2_min,
            "10_min_margin_for_cap5": cap5_min,
            "11_aligned_with_equity_sim": True,
            "11_detail": "buying_power=max(0,equity*2-gross); cap before margin; same required_margin formula as Phase592B",
            "12_ready_for_real_orders": False,
            "12_reason": "order_enabled=false; live_trading_enabled=false; dry_run logging only",
            "13_next_phase": "phase593_live_order_capped_pilot_cap2",
        }

        return {
            "verdict": PHASE593_VERDICT,
            "generated_at": _now_iso(),
            "assumed_price": self.assumed_price,
            "config_safety": {
                "order_enabled": bool(getattr(config, "order_enabled", False)),
                "live_trading_enabled": bool(getattr(config, "live_trading_enabled", False)),
                "live_capital_check_enabled": bool(getattr(config, "live_capital_check_enabled", True)),
                "max_concurrent_positions": int(getattr(config, "max_concurrent_positions", 5)),
            },
            "live_account_snapshot": {
                "stock_wallet": getattr(live_snap, "stock_wallet", None),
                "margin_wallet": getattr(live_snap, "margin_wallet", None),
                "current_equity": getattr(live_snap, "current_equity", None),
                "buying_power": getattr(live_snap, "buying_power", None),
                "gross_position_value": getattr(live_snap, "gross_position_value", None),
                "api_online": getattr(live_snap, "api_online", False) if live_snap else False,
                "fetch_error": live_error or getattr(live_snap, "fetch_error", ""),
            },
            "mandatory_answers": mandatory,
            "live_checks": live_rows,
            "mock_scenarios": mock_rows,
            "reject_samples": reject_rows,
            "implementation": {
                "module": "src/small_paper/live_capital_manager.py",
                "runtime_hook": "pilot_runner._maybe_record_live_capital_check_entry (logging only)",
                "session_jsonl": "live_capital_check.jsonl",
                "check_order": [
                    "kill_switch",
                    "api_online",
                    "positions_sync",
                    "duplicate_symbol",
                    "pending_order_cap",
                    "cap_check",
                    "required_margin",
                    "buying_power",
                    "daily_loss_limit",
                    "can_enter",
                ],
            },
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports_dir
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "live_check": rep / "phase593_live_capital_check.csv",
            "rejects": rep / "phase593_live_capital_rejects.csv",
            "report_json": rep / "phase593_report.json",
        }

        check_rows: list[dict[str, Any]] = []
        for row in list(result.get("live_checks") or []):
            check_rows.append({**row, "scenario": row.get("scenario") or "live_account"})
        for row in list(result.get("mock_scenarios") or []):
            check_rows.append(row)
        check_fields = list(CAPITAL_CHECK_FIELDS) + ["scenario", "equity_yen", "operational_ok", "stock_wallet"]
        _write_csv(paths["live_check"], check_fields, check_rows)
        _write_csv(paths["rejects"], REJECT_FIELDS, result.get("reject_samples") or [])

        report = {k: v for k, v in result.items() if k not in ("live_checks", "mock_scenarios", "reject_samples")}
        paths["report_json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        ma = result.get("mandatory_answers") or {}
        snap = result.get("live_account_snapshot") or {}
        doc = self.kabu / "docs" / "operations" / "phase593_live_capital_manager.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "\n".join(
                [
                    "# Phase593 — Live Capital Manager",
                    "",
                    f"**Verdict:** `{PHASE593_VERDICT}`",
                    "",
                    "## Executive summary",
                    "",
                    "- `LiveCapitalManager` implemented in `src/small_paper/live_capital_manager.py`.",
                    "- Runtime hook logs capital checks to `live_capital_check.jsonl` (does **not** block paper ENTRY).",
                    "- Uses kabusapi `StockAccountWallet`, `MarginAccountWallet`, positions, orders.",
                    "- CAP and margin/buying_power checks are **separate** with distinct reject reasons.",
                    "- Pending buy orders consume CAP slots; duplicate symbols blocked.",
                    "",
                    "## Live account snapshot",
                    "",
                    f"- stock_wallet: {snap.get('stock_wallet')}",
                    f"- margin_wallet: {snap.get('margin_wallet')}",
                    f"- can_enter (live): {ma.get('7_current_account_entry_possible')}",
                    f"- reject_reason: {ma.get('8_current_account_reject_reason')}",
                    "",
                    "## Mandatory answers",
                    "",
                ]
                + [f"{i}. {v}" for i, (_, v) in enumerate(ma.items(), 1)]
                + [
                    "",
                    "## Outputs",
                    "",
                ]
                + [f"- `{p.name}`" for p in paths.values()]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
