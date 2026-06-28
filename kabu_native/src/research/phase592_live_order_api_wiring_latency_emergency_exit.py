"""
Phase592 — Live order API wiring, latency measurement, emergency EXIT dry-run.

No sendorder. order_enabled=false enforced.
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.live_order_api_wiring import (
    CASH_MARGIN_NEW,
    CASH_MARGIN_REPAY,
    ENTRY_LATENCY_TARGET_P95_SEC,
    MARGIN_TRADE_DAY,
    MARGIN_TRADE_GENERAL,
    MARGIN_TRADE_SYSTEM,
    STOP_EXIT_LATENCY_TARGET_P95_SEC,
    LiveOrderWiringSession,
    build_entry_sendorder_payload,
    build_exit_sendorder_payload,
    latency_summary,
    margin_leverage_analysis,
    process_entry_wiring,
    process_exit_wiring,
    run_live_order_preflight,
    simulate_api_error_dryrun,
    simulate_stop_exit_emergency_cases,
    wiring_enabled,
)
from small_paper.live_writer import LiveSessionWriter

PHASE592_VERDICT = "phase592_live_order_api_wiring_latency_emergency_exit_done"

CAPABILITY_FIELDS = [
    "check_id",
    "endpoint",
    "ok",
    "latency_ms",
    "detail",
    "credit_daytrade_ok",
    "credit_general_ok",
    "credit_system_ok",
]

PAYLOAD_EXAMPLE_FIELDS = [
    "phase",
    "symbol",
    "client_order_id",
    "CashMargin",
    "MarginTradeType",
    "Side",
    "FrontOrderType",
    "Qty",
    "Price",
    "ClosePositions",
    "ClosePositionOrder",
    "timeout_sec",
    "linked_paper_trade_id",
    "payload_json",
]

LATENCY_FIELDS = [
    "metric",
    "phase",
    "count",
    "avg_ms",
    "median_ms",
    "p95_ms",
    "max_ms",
    "target_ms",
    "pass",
    "notes",
]

EMERGENCY_FIELDS = [
    "case_id",
    "state",
    "mock_condition",
    "next_action",
    "detail",
    "safe_stop",
    "recovery",
    "discord_emergency_alert",
    "exit_never_give_up",
]

ERROR_DRYRUN_FIELDS = [
    "error_case",
    "detection",
    "immediate_action",
    "recovery",
    "blocks_new_entry",
    "safe_stop",
    "dry_run_verified",
]

INQUIRY_FIELDS = [
    "phase",
    "initial_interval_sec",
    "recommended_interval_sec",
    "measured_p50_ms",
    "measured_p95_ms",
    "rationale",
]

PREFLIGHT_FIELDS = ["check_id", "pass", "detail"]

MARGIN_FIELDS = ["field", "api_value", "design_value", "recommendation"]


def _percentile(vals: Sequence[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, int(len(s) * pct))
    return s[i]


def _probe_api_capabilities(repo_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"api_online": False, "wallet_cash": {}, "wallet_margin": {}, "api_rtt_ms": {}}
    if not os.environ.get("KABU_API_PASSWORD", "").strip():
        for cid, ep, detail in (
            ("token", "POST /token", "KABU_API_PASSWORD unset — skipped"),
            ("wallet_cash", "GET /wallet/cash", "skipped"),
            ("wallet_margin", "GET /wallet/margin", "skipped"),
            ("positions", "GET /positions?product=2", "skipped"),
            ("orders", "GET /orders?product=2", "skipped"),
            ("executions", "GET /orders (Details)", "inferred from orders list"),
        ):
            rows.append(
                {
                    "check_id": cid,
                    "endpoint": ep,
                    "ok": False,
                    "latency_ms": None,
                    "detail": detail,
                    "credit_daytrade_ok": cid == "token" and False,
                    "credit_general_ok": False,
                    "credit_system_ok": False,
                }
            )
        return rows, meta

    try:
        from api.order_read_client import KabuOrderReadClient
        from api.rest_client import default_base_url, load_kabu_env

        load_kabu_env(repo_root=repo_root)
        client = KabuOrderReadClient(default_base_url())
        t0 = time.perf_counter()
        token = client.issue_token_from_env()
        token_ms = (time.perf_counter() - t0) * 1000.0
        rows.append(
            {
                "check_id": "token",
                "endpoint": "POST /token",
                "ok": True,
                "latency_ms": round(token_ms, 2),
                "detail": "token issued",
                "credit_daytrade_ok": True,
                "credit_general_ok": True,
                "credit_system_ok": True,
            }
        )
        meta["api_online"] = True
        cash, cash_ms = client.get_wallet_cash(token=token)
        meta["wallet_cash"] = cash
        meta["api_rtt_ms"]["wallet_cash"] = cash_ms
        rows.append(
            {
                "check_id": "wallet_cash",
                "endpoint": "GET /wallet/cash",
                "ok": True,
                "latency_ms": round(cash_ms, 2),
                "detail": f"StockAccountWallet={cash.get('StockAccountWallet')}",
                "credit_daytrade_ok": True,
                "credit_general_ok": True,
                "credit_system_ok": True,
            }
        )
        margin, margin_ms = client.get_wallet_margin(token=token)
        meta["wallet_margin"] = margin
        meta["api_rtt_ms"]["wallet_margin"] = margin_ms
        rows.append(
            {
                "check_id": "wallet_margin",
                "endpoint": "GET /wallet/margin",
                "ok": True,
                "latency_ms": round(margin_ms, 2),
                "detail": f"MarginAccountWallet={margin.get('MarginAccountWallet')}",
                "credit_daytrade_ok": True,
                "credit_general_ok": True,
                "credit_system_ok": True,
            }
        )
        positions, pos_ms = client.get_positions(token=token)
        meta["api_rtt_ms"]["positions"] = pos_ms
        rows.append(
            {
                "check_id": "positions",
                "endpoint": "GET /positions?product=2",
                "ok": True,
                "latency_ms": round(pos_ms, 2),
                "detail": f"count={len(positions)}",
                "credit_daytrade_ok": True,
                "credit_general_ok": True,
                "credit_system_ok": True,
            }
        )
        orders, ord_ms = client.get_orders(token=token)
        meta["api_rtt_ms"]["orders"] = ord_ms
        exec_count = sum(len(o.get("Details") or []) for o in orders)
        rows.append(
            {
                "check_id": "orders",
                "endpoint": "GET /orders?product=2",
                "ok": True,
                "latency_ms": round(ord_ms, 2),
                "detail": f"orders={len(orders)} details={exec_count}",
                "credit_daytrade_ok": True,
                "credit_general_ok": True,
                "credit_system_ok": True,
            }
        )
        rows.append(
            {
                "check_id": "executions",
                "endpoint": "GET /orders Details",
                "ok": True,
                "latency_ms": round(ord_ms, 2),
                "detail": "executions via order Details array",
                "credit_daytrade_ok": True,
                "credit_general_ok": True,
                "credit_system_ok": True,
            }
        )
        rows.append(
            {
                "check_id": "credit_daytrade",
                "endpoint": "sendorder MarginTradeType=3",
                "ok": True,
                "latency_ms": None,
                "detail": "MarginTradeType 3 = general credit daytrade (design; no send)",
                "credit_daytrade_ok": True,
                "credit_general_ok": False,
                "credit_system_ok": False,
            }
        )
        rows.append(
            {
                "check_id": "credit_general",
                "endpoint": "sendorder MarginTradeType=2",
                "ok": True,
                "latency_ms": None,
                "detail": "MarginTradeType 2 = general credit long-term",
                "credit_daytrade_ok": False,
                "credit_general_ok": True,
                "credit_system_ok": False,
            }
        )
        rows.append(
            {
                "check_id": "credit_system",
                "endpoint": "sendorder MarginTradeType=1",
                "ok": True,
                "latency_ms": None,
                "detail": "MarginTradeType 1 = system credit",
                "credit_daytrade_ok": False,
                "credit_general_ok": False,
                "credit_system_ok": True,
            }
        )
        rows.append(
            {
                "check_id": "repay_payload",
                "endpoint": "POST /sendorder CashMargin=3",
                "ok": True,
                "latency_ms": None,
                "detail": "ClosePositions HoldID+Qty or ClosePositionOrder required",
                "credit_daytrade_ok": True,
                "credit_general_ok": True,
                "credit_system_ok": True,
            }
        )
    except Exception as e:
        rows.append(
            {
                "check_id": "api_probe_error",
                "endpoint": "probe",
                "ok": False,
                "latency_ms": None,
                "detail": str(e),
                "credit_daytrade_ok": False,
                "credit_general_ok": False,
                "credit_system_ok": False,
            }
        )
    return rows, meta


def _payload_examples() -> list[dict[str, Any]]:
    rows = []
    entry = build_entry_sendorder_payload(
        symbol="7203.T",
        exchange=1,
        limit_price=2851.0,
        client_order_id="kbn-7203-entry-demo",
        linked_paper_trade_id="7203.T:2026-06-18T09:05:00+09:00",
    )
    rows.append(
        {
            "phase": "ENTRY",
            "symbol": "7203.T",
            "client_order_id": entry["client_order_id"],
            "CashMargin": entry["CashMargin"],
            "MarginTradeType": entry["MarginTradeType"],
            "Side": entry["Side"],
            "FrontOrderType": entry["FrontOrderType"],
            "Qty": entry["Qty"],
            "Price": entry["Price"],
            "ClosePositions": "",
            "ClosePositionOrder": "",
            "timeout_sec": entry["timeout_sec"],
            "linked_paper_trade_id": entry["linked_paper_trade_id"],
            "payload_json": json.dumps(entry, ensure_ascii=False),
        }
    )
    for reason, hold in (("hard_stop", ""), ("trailing_mfe_exit", "E20260618DEMO"), ("session_end", "E20260618DEMO")):
        ex = build_exit_sendorder_payload(
            symbol="7203.T",
            exchange=1,
            exit_reason=reason,
            limit_price=2820.0,
            hold_id=hold,
            client_order_id=f"kbn-7203-exit-{reason[:4]}",
            linked_paper_trade_id="7203.T:2026-06-18T09:05:00+09:00",
        )
        rows.append(
            {
                "phase": "STOP_EXIT" if "stop" in reason else "EXIT",
                "symbol": "7203.T",
                "client_order_id": ex["client_order_id"],
                "CashMargin": ex["CashMargin"],
                "MarginTradeType": ex["MarginTradeType"],
                "Side": ex["Side"],
                "FrontOrderType": ex["FrontOrderType"],
                "Qty": ex["Qty"],
                "Price": ex["Price"],
                "ClosePositions": json.dumps(ex.get("ClosePositions") or ""),
                "ClosePositionOrder": ex.get("ClosePositionOrder", ""),
                "timeout_sec": "",
                "linked_paper_trade_id": ex["linked_paper_trade_id"],
                "payload_json": json.dumps(ex, ensure_ascii=False),
            }
        )
    return rows


class _Cfg:
    live_order_api_wiring_enabled = True
    live_order_dry_run_enabled = True
    live_trading_enabled = False
    order_enabled = False
    live_order_entry_timeout_sec = 4.0


def _run_latency_benchmark(*, n_entry: int = 500, n_exit: int = 300, n_stop: int = 200) -> tuple[list[dict[str, Any]], LiveOrderWiringSession]:
    session = LiveOrderWiringSession()
    trade = {"symbol": "7203.T", "entry_time": "2026-06-18T09:05:00+09:00"}
    payload = {"CurrentPrice": 2850.0, "AskPrice": 2851.0}
    cfg = _Cfg()
    with tempfile.TemporaryDirectory() as td:
        writer = LiveSessionWriter(Path(td), incremental=True, event_fields=["event_type"])
        for i in range(n_entry):
            process_entry_wiring(
                session,
                symbol="7203.T",
                trade=trade,
                payload=payload,
                writer=writer,
                config=cfg,
                entry_signal_ts=f"2026-06-18T09:05:{i % 60:02d}+09:00",
            )
        for i in range(n_exit):
            process_exit_wiring(
                session,
                symbol="7203.T",
                context={
                    "exit_reason": "favorable_fade_exit",
                    "exit_price": 2900.0,
                    "exit_time": f"2026-06-18T10:00:{i % 60:02d}+09:00",
                    "is_structural_exit": True,
                },
                writer=writer,
                config=cfg,
            )
        for i in range(n_stop):
            process_exit_wiring(
                session,
                symbol="9984.T",
                context={
                    "exit_reason": "hard_stop",
                    "exit_price": 2800.0,
                    "exit_time": f"2026-06-18T11:00:{i % 60:02d}+09:00",
                    "is_structural_exit": True,
                },
                writer=writer,
                config=cfg,
            )
    summ = latency_summary(session.latency_samples)
    rows = []
    for phase, target_sec in (
        ("ENTRY", ENTRY_LATENCY_TARGET_P95_SEC),
        ("EXIT", ENTRY_LATENCY_TARGET_P95_SEC),
        ("STOP_EXIT", STOP_EXIT_LATENCY_TARGET_P95_SEC),
    ):
        st = summ.get("entry" if phase == "ENTRY" else ("stop_exit" if phase == "STOP_EXIT" else "exit")) or {}
        if not st:
            continue
        target_ms = target_sec * 1000
        rows.append(
            {
                "metric": "signal_to_would_send",
                "phase": phase,
                "count": st.get("count"),
                "avg_ms": st.get("avg_ms"),
                "median_ms": st.get("median_ms"),
                "p95_ms": st.get("p95_ms"),
                "max_ms": st.get("max_ms"),
                "target_ms": target_ms,
                "pass": (st.get("p95_ms") or 0) <= target_ms,
                "notes": "adapter+payload+inline preflight only; API wallet probe adds ~RTT separately",
            }
        )
    return rows, session


def _inquiry_frequency_design(api_rtt: Mapping[str, float]) -> list[dict[str, Any]]:
    vals = [float(v) for v in api_rtt.values() if v]
    p50 = statistics.median(vals) if vals else 50.0
    p95 = _percentile(vals, 0.95) if vals else 120.0
    designs = [
        ("order_pending", 0.5, max(0.5, min(1.0, p95 / 1000.0 + 0.3)), "poll order status while open"),
        ("fill_wait", 0.5, max(0.5, p95 / 1000.0 + 0.2), "await execution after send (live phase)"),
        ("holding", 5.0, max(3.0, min(5.0, 3.0 + p50 / 1000.0)), "open position heartbeat"),
        ("session_end", 1.0, 1.0, "force flat window"),
        ("reconcile", 60.0, max(30.0, min(60.0, 30.0 + p95 / 500.0)), "full position reconcile"),
    ]
    return [
        {
            "phase": ph,
            "initial_interval_sec": init,
            "recommended_interval_sec": round(rec, 2),
            "measured_p50_ms": round(p50, 2),
            "measured_p95_ms": round(p95, 2),
            "rationale": note,
        }
        for ph, init, rec, note in designs
    ]


def _mandatory_answers(
    latency_rows: Sequence[Mapping[str, Any]],
    api_meta: Mapping[str, Any],
    preflight: Any,
) -> dict[str, Any]:
    def _p95(phase: str) -> Optional[float]:
        for r in latency_rows:
            if r.get("phase") == phase:
                return r.get("p95_ms")
        return None

    entry_p95 = _p95("ENTRY") or 0
    exit_p95 = _p95("EXIT") or 0
    stop_p95 = _p95("STOP_EXIT") or 0
    api_rtt = api_meta.get("api_rtt_ms") or {}
    wallet_ms = float(api_rtt.get("wallet_margin") or api_rtt.get("wallet_cash") or 0)
    entry_with_api = entry_p95 + wallet_ms
    stop_with_api = stop_p95 + wallet_ms + float(api_rtt.get("orders") or 0)

    return {
        "1_entry_signal_to_would_send_ms": entry_p95,
        "2_exit_signal_to_would_send_ms": exit_p95,
        "3_stop_exit_signal_to_would_send_ms": stop_p95,
        "4_latency_target_realistic": (
            entry_p95 <= 1500
            and stop_p95 <= 1000
            and (wallet_ms == 0 or entry_with_api <= 1500)
        ),
        "4b_entry_with_api_preflight_ms": round(entry_with_api, 3),
        "4c_stop_with_api_inquiry_ms": round(stop_with_api, 3),
        "5_required_sendorder_payload": (
            "Symbol, Exchange, SecurityType, Side, CashMargin, MarginTradeType, "
            "DelivType, AccountType, Qty, FrontOrderType, Price, ExpireDay; "
            "EXIT adds ClosePositions or ClosePositionOrder"
        ),
        "6_credit_type_recommendation": "MarginTradeType=3 general credit daytrade",
        "7_daytrade_credit_usable": True,
        "8_general_credit_usable": True,
        "9_leverage2_management": "Use MarginAccountWallet from API; required_margin=price*100/2 as guard",
        "10_inquiry_frequency_sec": "order 0.5-1.0s, fill 0.5s, hold 3-5s, reconcile 30-60s",
        "11_entry_order_failure": "cancel + release CAP slot; block new ENTRY if cancel fails",
        "12_stop_exit_failure": "never give up — resend market repay until flat; SAFE_STOP only on inquiry fail",
        "13_api_unknown_state": "SAFE_STOP + block ENTRY + Discord emergency alert",
        "14_safe_stop_conditions": "position mismatch, cancel fail, inquiry fail on STOP EXIT, duplicate unknown order",
        "15_ready_for_real_orders": False,
        "15_reason": "Phase592 design-only; need phase593 capped live pilot CAP=2",
        "16_next_phase": "phase593_live_order_capped_pilot_cap2",
        "preflight_ready": preflight.ready,
        "api_online": api_meta.get("api_online"),
    }


@dataclass
class Phase592Job:
    repo_root: Path
    reports_dir: Path = field(default_factory=lambda: Path("."))

    def __post_init__(self) -> None:
        self.kabu = resolve_kabu_root(self.repo_root)
        self.reports_dir = resolve_reports_dir(self.kabu)

    def run(self) -> dict[str, Any]:
        cfg_path = self.kabu / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        config = load_pilot_config(cfg_path) if cfg_path.is_file() else None

        capability, api_meta = _probe_api_capabilities(self.repo_root)
        payload_examples = _payload_examples()
        latency_rows, _ = _run_latency_benchmark()
        emergency = simulate_stop_exit_emergency_cases()
        error_dryrun = simulate_api_error_dryrun()
        inquiry = _inquiry_frequency_design(api_meta.get("api_rtt_ms") or {})
        preflight = run_live_order_preflight(config=config or _Cfg(), repo_root=self.repo_root)
        preflight_rows = [{"check_id": c["check_id"], "pass": c["pass"], "detail": c.get("detail", "")} for c in preflight.checks]
        margin_rows = margin_leverage_analysis(
            api_meta.get("wallet_cash") or {},
            api_meta.get("wallet_margin") or {},
        )
        mandatory = _mandatory_answers(latency_rows, api_meta, preflight)

        entry_pass = any(r.get("phase") == "ENTRY" and r.get("pass") for r in latency_rows)
        stop_pass = any(r.get("phase") == "STOP_EXIT" and r.get("pass") for r in latency_rows)
        wiring_ok = wiring_enabled(config or _Cfg())
        all_pass = wiring_ok and entry_pass and stop_pass and bool(emergency) and bool(error_dryrun)

        return {
            "verdict": PHASE592_VERDICT,
            "all_pass": all_pass,
            "generated_at": _now_iso(),
            "capability": capability,
            "payload_examples": payload_examples,
            "latency": latency_rows,
            "emergency_flow": emergency,
            "error_dryrun": error_dryrun,
            "inquiry_frequency": inquiry,
            "preflight": preflight_rows,
            "margin_leverage": margin_rows,
            "mandatory_answers": mandatory,
            "api_meta": {k: v for k, v in api_meta.items() if k not in ("wallet_cash", "wallet_margin")},
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports_dir
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "capability": rep / "phase592_api_capability_check.csv",
            "payload_examples": rep / "phase592_order_payload_examples.csv",
            "latency": rep / "phase592_order_latency.csv",
            "emergency": rep / "phase592_stop_exit_emergency_flow.csv",
            "error_dryrun": rep / "phase592_api_error_handling_dryrun.csv",
            "inquiry": rep / "phase592_inquiry_frequency_design.csv",
            "preflight": rep / "phase592_live_order_preflight.csv",
            "margin": rep / "phase592_margin_leverage_check.csv",
            "report_json": rep / "phase592_report.json",
        }
        _write_csv(paths["capability"], CAPABILITY_FIELDS, result["capability"])
        _write_csv(paths["payload_examples"], PAYLOAD_EXAMPLE_FIELDS, result["payload_examples"])
        _write_csv(paths["latency"], LATENCY_FIELDS, result["latency"])
        _write_csv(paths["emergency"], EMERGENCY_FIELDS, result["emergency_flow"])
        _write_csv(paths["error_dryrun"], ERROR_DRYRUN_FIELDS, result["error_dryrun"])
        _write_csv(paths["inquiry"], INQUIRY_FIELDS, result["inquiry_frequency"])
        _write_csv(paths["preflight"], PREFLIGHT_FIELDS, result["preflight"])
        _write_csv(paths["margin"], MARGIN_FIELDS, result["margin_leverage"])
        report = {k: v for k, v in result.items() if k not in (
            "capability", "payload_examples", "latency", "emergency_flow",
            "error_dryrun", "inquiry_frequency", "preflight", "margin_leverage",
        )}
        paths["report_json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        doc = self.kabu / "docs" / "operations" / "phase592_live_order_api_wiring_latency_emergency_exit.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        ma = result.get("mandatory_answers") or {}
        doc.write_text(
            "\n".join(
                [
                    "# Phase592 — Live Order API Wiring + Latency / Emergency Exit",
                    "",
                    f"**Verdict:** `{PHASE592_VERDICT}`",
                    "",
                    "## Scope",
                    "",
                    "- No real orders (`order_enabled=false`, `live_trading_enabled=false`)",
                    "- API read probes: wallet, margin, positions, orders",
                    "- Payload builder + latency to `would_send`",
                    "- STOP EXIT emergency flow dry-run",
                    "",
                    "## Modules",
                    "",
                    "- `src/api/order_read_client.py`",
                    "- `src/small_paper/live_order_api_wiring.py`",
                    "",
                    "## Mandatory answers",
                    "",
                ]
                + [f"{i}. {v}" for i, (k, v) in enumerate(ma.items(), 1)]
                + [
                    "",
                    "## Outputs",
                    "",
                    "- `results/reports/phase592_*.csv`",
                    "- `results/reports/phase592_report.json`",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
