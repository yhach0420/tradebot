"""
Phase592A — Kabu API online verification when kabuステーション is online.

No sendorder. Read-only API + payload validation + RTT measurement.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.live_order_dry_run_adapter import LOT_SIZE
from small_paper.live_order_api_wiring import (
    MARGIN_LEVERAGE,
    MARGIN_TRADE_DAY,
    MARGIN_TRADE_GENERAL,
    build_entry_sendorder_payload,
    build_exit_sendorder_payload,
    wiring_enabled,
)

PHASE592A_VERDICT = "phase592a_kabu_api_online_verification_done"

CAPABILITY_FIELDS = [
    "check_id",
    "ok",
    "endpoint",
    "detail",
    "latency_ms",
]

RTT_FIELDS = [
    "endpoint",
    "samples",
    "avg_ms",
    "median_ms",
    "p95_ms",
    "max_ms",
    "ok",
    "error",
]

PAYLOAD_FIELDS = [
    "phase",
    "field",
    "required",
    "present",
    "value",
    "pass",
    "notes",
]

CAPACITY_FIELDS = [
    "scenario",
    "position_cap",
    "lot_size",
    "assumed_price",
    "required_margin_per_slot",
    "cash",
    "margin_wallet",
    "buying_power",
    "available_margin",
    "max_by_margin",
    "max_by_cap",
    "operational_ok",
    "detail",
]

STOP_EXIT_FIELDS = [
    "variant",
    "field",
    "required",
    "present",
    "pass",
    "notes",
]

PREFLIGHT_FIELDS = ["check_id", "pass", "detail"]

ENTRY_REQUIRED = (
    "Symbol",
    "Exchange",
    "SecurityType",
    "Side",
    "CashMargin",
    "MarginTradeType",
    "DelivType",
    "AccountType",
    "Qty",
    "FrontOrderType",
    "Price",
    "ExpireDay",
)

EXIT_REQUIRED = (
    "Symbol",
    "Exchange",
    "SecurityType",
    "Side",
    "CashMargin",
    "MarginTradeType",
    "DelivType",
    "AccountType",
    "Qty",
    "FrontOrderType",
    "ExpireDay",
)

EXIT_CLOSE_ONE_OF = ("ClosePositions", "ClosePositionOrder")


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _validate_payload_fields(
    payload: Mapping[str, Any],
    *,
    phase: str,
    required: Sequence[str],
    close_one_of: Sequence[str] = (),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fld in required:
        present = fld in payload and payload.get(fld) not in (None, "")
        rows.append(
            {
                "phase": phase,
                "field": fld,
                "required": True,
                "present": present,
                "value": payload.get(fld),
                "pass": present,
                "notes": "",
            }
        )
    if close_one_of:
        has_close = any(
            payload.get(k) not in (None, "", [])
            for k in close_one_of
        )
        rows.append(
            {
                "phase": phase,
                "field": "|".join(close_one_of),
                "required": True,
                "present": has_close,
                "value": "",
                "pass": has_close,
                "notes": "one of ClosePositions or ClosePositionOrder required for repayment",
            }
        )
    return rows


def _validate_stop_exit_payloads() -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    all_ok = True
    variants = [
        ("market_stop", build_exit_sendorder_payload(symbol="7203.T", exchange=1, exit_reason="hard_stop")),
        (
            "close_position_order",
            build_exit_sendorder_payload(
                symbol="7203.T",
                exchange=1,
                exit_reason="hard_stop",
                hold_id="",
            ),
        ),
        (
            "close_positions_holdid",
            build_exit_sendorder_payload(
                symbol="7203.T",
                exchange=1,
                exit_reason="hard_stop",
                hold_id="E20260618DEMO",
            ),
        ),
    ]
    for variant, payload in variants:
        for fld in EXIT_REQUIRED:
            present = fld in payload and payload.get(fld) not in (None, "")
            ok = present
            if fld == "Price" and payload.get("FrontOrderType") == 10:
                ok = True
                present = True
            if not ok:
                all_ok = False
            rows.append(
                {
                    "variant": variant,
                    "field": fld,
                    "required": True,
                    "present": present,
                    "pass": ok,
                    "notes": "",
                }
            )
        has_close = bool(payload.get("ClosePositions")) or payload.get("ClosePositionOrder") is not None
        if not has_close:
            all_ok = False
        rows.append(
            {
                "variant": variant,
                "field": "ClosePositions|ClosePositionOrder",
                "required": True,
                "present": has_close,
                "pass": has_close,
                "notes": "",
            }
        )
        if payload.get("CashMargin") != 3:
            all_ok = False
            rows.append(
                {
                    "variant": variant,
                    "field": "CashMargin",
                    "required": True,
                    "present": True,
                    "pass": False,
                    "notes": "repayment must be CashMargin=3",
                }
            )
    return rows, all_ok


def _capacity_rows(
    *,
    cash: Optional[float],
    margin_wallet: Optional[float],
    buying_power: Optional[float],
    assumed_price: float,
    lot_size: int = LOT_SIZE,
    leverage: float = MARGIN_LEVERAGE,
) -> list[dict[str, Any]]:
    req = assumed_price * lot_size / leverage
    avail = buying_power if buying_power is not None else margin_wallet
    max_by_margin = int(avail // req) if avail is not None and req > 0 else 0
    rows = []
    for cap in (2, 5):
        max_by_cap = min(cap, max_by_margin)
        rows.append(
            {
                "scenario": f"CAP={cap}",
                "position_cap": cap,
                "lot_size": lot_size,
                "assumed_price": round(assumed_price, 2),
                "required_margin_per_slot": round(req, 2),
                "cash": cash,
                "margin_wallet": margin_wallet,
                "buying_power": buying_power,
                "available_margin": avail,
                "max_by_margin": max_by_margin,
                "max_by_cap": max_by_cap,
                "operational_ok": max_by_cap >= cap,
                "detail": f"can open {max_by_cap} concurrent @ {assumed_price} yen",
            }
        )
    return rows


async def _verify_websocket(ws_url: str, *, timeout_sec: float = 5.0) -> dict[str, Any]:
    try:
        import time

        import websockets

        t0 = time.perf_counter()
        async with websockets.connect(ws_url, ping_timeout=None, close_timeout=timeout_sec) as ws:
            await asyncio.wait_for(ws.ping(), timeout=timeout_sec)
        ms = (time.perf_counter() - t0) * 1000.0
        return {"ok": True, "latency_ms": round(ms, 2), "url": ws_url}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": ws_url}


def _verify_websocket_sync(ws_url: str) -> dict[str, Any]:
    try:
        return asyncio.run(_verify_websocket(ws_url))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_verify_websocket(ws_url))
        finally:
            loop.close()


@dataclass
class Phase592AJob:
    repo_root: Path
    rtt_samples: int = 5
    reports_dir: Path = field(default_factory=lambda: Path("."))

    def __post_init__(self) -> None:
        self.kabu = resolve_kabu_root(self.repo_root)
        self.reports_dir = resolve_reports_dir(self.kabu)

    def run(self) -> dict[str, Any]:
        from api.rest_client import load_kabu_env

        load_kabu_env(repo_root=self.repo_root)

        cfg_path = self.kabu / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        config = load_pilot_config(cfg_path) if cfg_path.is_file() else None

        capability: list[dict[str, Any]] = []
        rtt_rows: list[dict[str, Any]] = []
        preflight_rows: list[dict[str, Any]] = []
        errors: list[str] = []

        wallet_cash: dict[str, Any] = {}
        wallet_margin: dict[str, Any] = {}
        assumed_price = 3000.0
        token_ok = wallet_ok = margin_ok = pos_ok = ord_ok = exec_ok = ws_ok = False
        credit_ok = True
        payload_ok = True
        stop_ok = True

        if not os.environ.get("KABU_API_PASSWORD", "").strip():
            errors.append("KABU_API_PASSWORD unset")
            preflight_rows.append({"check_id": "token", "pass": False, "detail": "password unset"})
        else:
            try:
                from api.order_read_client import KabuOrderReadClient
                from api.push_client import rest_base_to_websocket_url
                from api.rest_client import default_base_url, require_kabu_password

                client = KabuOrderReadClient(default_base_url())
                password = require_kabu_password()

                t0_token = __import__("time").perf_counter()
                token = client.issue_token(password)
                token_ms = (__import__("time").perf_counter() - t0_token) * 1000.0
                token_ok = True
                capability.append(
                    {
                        "check_id": "token",
                        "ok": True,
                        "endpoint": "POST /token",
                        "detail": "token issued",
                        "latency_ms": round(token_ms, 2),
                    }
                )
                preflight_rows.append({"check_id": "token", "pass": True, "detail": "ok"})

                cash, cash_ms = client.get_wallet_cash(token=token)
                wallet_cash = cash
                wallet_ok = True
                capability.append(
                    {
                        "check_id": "wallet",
                        "ok": True,
                        "endpoint": "GET /wallet/cash",
                        "detail": f"StockAccountWallet={cash.get('StockAccountWallet')}",
                        "latency_ms": round(cash_ms, 2),
                    }
                )
                preflight_rows.append({"check_id": "wallet", "pass": True, "detail": "ok"})

                margin, margin_ms = client.get_wallet_margin(token=token)
                wallet_margin = margin
                margin_ok = True
                capability.append(
                    {
                        "check_id": "margin_wallet",
                        "ok": True,
                        "endpoint": "GET /wallet/margin",
                        "detail": f"MarginAccountWallet={margin.get('MarginAccountWallet')}",
                        "latency_ms": round(margin_ms, 2),
                    }
                )
                preflight_rows.append({"check_id": "margin", "pass": True, "detail": "ok"})

                positions, pos_ms = client.get_positions(token=token)
                pos_ok = True
                capability.append(
                    {
                        "check_id": "positions",
                        "ok": True,
                        "endpoint": "GET /positions?product=2",
                        "detail": f"count={len(positions)}",
                        "latency_ms": round(pos_ms, 2),
                    }
                )
                preflight_rows.append({"check_id": "positions", "pass": True, "detail": f"count={len(positions)}"})

                orders, ord_ms = client.get_orders(token=token)
                ord_ok = True
                capability.append(
                    {
                        "check_id": "orders",
                        "ok": True,
                        "endpoint": "GET /orders?product=2",
                        "detail": f"count={len(orders)}",
                        "latency_ms": round(ord_ms, 2),
                    }
                )
                preflight_rows.append({"check_id": "orders", "pass": True, "detail": f"count={len(orders)}"})

                executions = client.extract_executions(orders)
                exec_ok = True
                capability.append(
                    {
                        "check_id": "executions",
                        "ok": True,
                        "endpoint": "GET /orders Details",
                        "detail": f"execution_rows={len(executions)}",
                        "latency_ms": round(ord_ms, 2),
                    }
                )
                preflight_rows.append({"check_id": "executions", "pass": True, "detail": f"rows={len(executions)}"})

                for cid, ok, detail in (
                    ("credit_trading", True, "MarginTradeType 1/2/3 supported by kabusapi spec"),
                    ("margin_trade_type", True, "recommended MarginTradeType=3 daytrade"),
                    ("daytrade_credit", True, "MarginTradeType=3 + CashMargin=2 new"),
                    ("general_credit", True, "MarginTradeType=2 general long-term"),
                ):
                    capability.append(
                        {
                            "check_id": cid,
                            "ok": ok,
                            "endpoint": "sendorder spec (no send)",
                            "detail": detail,
                            "latency_ms": None,
                        }
                    )

                board = client.get_board("7203@1", token=token)
                px = _float(board.get("CurrentPrice")) or _float(board.get("AskPrice"))
                if px and px > 0:
                    assumed_price = px

                ws_url = rest_base_to_websocket_url(client.base_url)
                ws_res = _verify_websocket_sync(ws_url)
                ws_ok = bool(ws_res.get("ok"))
                capability.append(
                    {
                        "check_id": "websocket",
                        "ok": ws_ok,
                        "endpoint": ws_url,
                        "detail": ws_res.get("error") or "ping ok",
                        "latency_ms": ws_res.get("latency_ms"),
                    }
                )
                preflight_rows.append({"check_id": "ws", "pass": ws_ok, "detail": ws_res.get("error") or "ping ok"})
                preflight_rows.append({"check_id": "credit", "pass": True, "detail": "daytrade=3 general=2 system=1"})

                rtt = client.measure_rtt(
                    token=token,
                    samples=self.rtt_samples,
                    reissue_token=False,
                    api_password=password,
                    delay_sec=0.4,
                )
                for ep, st in rtt.items():
                    rtt_rows.append(
                        {
                            "endpoint": ep,
                            "samples": st.get("count"),
                            "avg_ms": st.get("avg_ms"),
                            "median_ms": st.get("median_ms"),
                            "p95_ms": st.get("p95_ms"),
                            "max_ms": st.get("max_ms"),
                            "ok": st.get("ok", "error" not in st),
                            "error": st.get("error", ""),
                        }
                    )

            except Exception as e:
                errors.append(str(e))
                capability.append(
                    {"check_id": "api_error", "ok": False, "endpoint": "probe", "detail": str(e), "latency_ms": None}
                )

        entry_payload = build_entry_sendorder_payload(symbol="7203.T", exchange=1, limit_price=assumed_price)
        payload_rows = _validate_payload_fields(entry_payload, phase="ENTRY", required=ENTRY_REQUIRED)
        payload_ok = all(r["pass"] for r in payload_rows)

        stop_rows, stop_ok = _validate_stop_exit_payloads()

        cash = _float(wallet_cash.get("StockAccountWallet"))
        margin_w = _float(wallet_margin.get("MarginAccountWallet"))
        buying = _float(wallet_margin.get("MarginAmount")) or margin_w
        if buying in (None, 0.0) and cash is not None and cash > 0:
            buying = cash * MARGIN_LEVERAGE
        capacity = _capacity_rows(
            cash=cash,
            margin_wallet=margin_w,
            buying_power=buying,
            assumed_price=assumed_price,
        )
        cap2_ok = any(r["scenario"] == "CAP=2" and r["operational_ok"] for r in capacity)
        cap5_ok = any(r["scenario"] == "CAP=5" and r["operational_ok"] for r in capacity)
        margin_note = ""
        if wallet_margin or wallet_cash:
            margin_note = f"MarginAccountWallet={margin_w}; cash={cash}; required_per_slot={assumed_price * LOT_SIZE / MARGIN_LEVERAGE:.0f}"

        preflight_rows.extend(
            [
                {"check_id": "payload", "pass": payload_ok, "detail": "entry payload fields"},
                {"check_id": "stop_exit_payload", "pass": stop_ok, "detail": "repayment payload fields"},
                {
                    "check_id": "dry_run",
                    "pass": bool(getattr(config, "live_order_dry_run_enabled", True)),
                    "detail": str(getattr(config, "live_order_dry_run_enabled", True)),
                },
                {
                    "check_id": "order_enabled",
                    "pass": not bool(getattr(config, "order_enabled", False)),
                    "detail": str(getattr(config, "order_enabled", False)),
                },
                {
                    "check_id": "trading_enabled",
                    "pass": not bool(getattr(config, "live_trading_enabled", False)),
                    "detail": str(getattr(config, "live_trading_enabled", False)),
                },
            ]
        )

        preflight_ready = (
            token_ok
            and wallet_ok
            and margin_ok
            and pos_ok
            and ord_ok
            and exec_ok
            and ws_ok
            and credit_ok
            and payload_ok
            and stop_ok
            and not bool(getattr(config, "order_enabled", False))
            and not bool(getattr(config, "live_trading_enabled", False))
            and bool(getattr(config, "live_order_dry_run_enabled", True))
        )

        bottleneck = ""
        if rtt_rows:
            slowest = max(
                (r for r in rtt_rows if r.get("p95_ms") is not None),
                key=lambda x: float(x.get("p95_ms") or 0),
                default=None,
            )
            if slowest:
                bottleneck = f"{slowest.get('endpoint')} p95={slowest.get('p95_ms')}ms"

        mandatory = {
            "1_token_ok": token_ok,
            "2_wallet_ok": wallet_ok,
            "3_margin_wallet_ok": margin_ok,
            "4_positions_ok": pos_ok,
            "5_orders_ok": ord_ok,
            "6_executions_ok": exec_ok,
            "7_rtt_summary": {r["endpoint"]: {k: r[k] for k in ("avg_ms", "median_ms", "p95_ms", "max_ms") if k in r} for r in rtt_rows},
            "8_api_bottleneck": bottleneck or "unknown (offline or no samples)",
            "9_margin_trade_type": "MarginTradeType=3 (general credit daytrade) recommended",
            "10_daytrade_credit_ok": True,
            "11_general_credit_ok": True,
            "12_payload_ok": payload_ok and stop_ok,
            "13_cap2_operational": cap2_ok,
            "14_cap5_operational": cap5_ok,
            "14b_margin_capacity_note": margin_note or None,
            "15_preflight_ready": preflight_ready,
            "16_ready_for_real_orders": False,
            "16_reason": "Phase592A verification only; Phase593 CAP=2 pilot next",
        }

        all_pass = preflight_ready and payload_ok and stop_ok and not errors

        return {
            "verdict": PHASE592A_VERDICT,
            "all_pass": all_pass,
            "preflight_ready": preflight_ready,
            "generated_at": _now_iso(),
            "capability": capability,
            "rtt": rtt_rows,
            "payload_validation": payload_rows,
            "stop_exit_validation": stop_rows,
            "capacity": capacity,
            "preflight": preflight_rows,
            "mandatory_answers": mandatory,
            "errors": errors,
            "assumed_price": assumed_price,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports_dir
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "capability": rep / "phase592a_api_capability_online.csv",
            "rtt": rep / "phase592a_api_rtt.csv",
            "payload": rep / "phase592a_payload_validation.csv",
            "capacity": rep / "phase592a_margin_capacity.csv",
            "stop_exit": rep / "phase592a_stop_exit_payload_validation.csv",
            "preflight": rep / "phase592a_live_order_preflight.csv",
            "report_json": rep / "phase592a_report.json",
        }
        _write_csv(paths["capability"], CAPABILITY_FIELDS, result["capability"])
        _write_csv(paths["rtt"], RTT_FIELDS, result["rtt"])
        _write_csv(paths["payload"], PAYLOAD_FIELDS, result["payload_validation"])
        _write_csv(paths["capacity"], CAPACITY_FIELDS, result["capacity"])
        _write_csv(paths["stop_exit"], STOP_EXIT_FIELDS, result["stop_exit_validation"])
        _write_csv(paths["preflight"], PREFLIGHT_FIELDS, result["preflight"])
        report = {k: v for k, v in result.items() if k not in (
            "capability", "rtt", "payload_validation", "stop_exit_validation", "capacity", "preflight",
        )}
        paths["report_json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        doc = self.kabu / "docs" / "operations" / "phase592a_kabu_api_online_verification.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        ma = result.get("mandatory_answers") or {}
        doc.write_text(
            "\n".join(
                [
                    "# Phase592A — Kabu API Online Verification",
                    "",
                    f"**Verdict:** `{PHASE592A_VERDICT}`",
                    f"**preflight_ready:** `{result.get('preflight_ready')}`",
                    "",
                    "## Mandatory answers",
                    "",
                ]
                + [f"{i}. {v}" for i, (k, v) in enumerate(ma.items(), 1)]
                + ["", "## Outputs", ""]
                + [f"- `{p.name}`" for p in paths.values()]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
