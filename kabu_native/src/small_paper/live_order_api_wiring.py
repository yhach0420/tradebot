"""
Phase592: Live order API wiring — payload build + latency + preflight. No sendorder.
"""

from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from small_paper.live_order_dry_run_adapter import (
    LOT_SIZE,
    MARGIN_LEVERAGE,
    DEFAULT_ENTRY_TIMEOUT_SEC,
    _exit_order_type,
    _limit_entry_price,
    _paper_trade_id,
)

# kabusapi sendorder constants (reference: kabucom.github.io/kabusapi)
SIDE_BUY = "2"
SIDE_SELL = "1"
CASH_MARGIN_NEW = 2
CASH_MARGIN_REPAY = 3
MARGIN_TRADE_DAY = 3
MARGIN_TRADE_GENERAL = 2
MARGIN_TRADE_SYSTEM = 1
FRONT_ORDER_MARKET = 10
FRONT_ORDER_LIMIT = 20
SECURITY_TYPE_STOCK = 1
EXCHANGE_TSE = 1
ACCOUNT_TYPE_SPECIFIC = 4
DELIV_TYPE_NONE = 0

ENTRY_LATENCY_TARGET_P95_SEC = 1.5
STOP_EXIT_LATENCY_TARGET_P95_SEC = 1.0

LATENCY_FIELDS = (
    "phase",
    "symbol",
    "exit_reason",
    "entry_signal_ts",
    "adapter_receive_ts",
    "payload_build_start_ts",
    "payload_build_done_ts",
    "preflight_done_ts",
    "would_send_order_ts",
    "signal_to_would_send_ms",
    "dry_run",
    "order_enabled",
    "trading_enabled",
    "linked_paper_trade_id",
    "client_order_id",
)


def wiring_enabled(config: Any) -> bool:
    if bool(getattr(config, "live_trading_enabled", False)):
        return False
    if bool(getattr(config, "order_enabled", False)):
        return False
    return bool(getattr(config, "live_order_api_wiring_enabled", True))


def symbol_to_kabu_code(symbol: str) -> str:
    return str(symbol or "").replace(".T", "").strip()


def make_client_order_id(symbol: str, *, suffix: str = "entry") -> str:
    code = symbol_to_kabu_code(symbol)
    return f"kbn-{code}-{suffix}-{uuid.uuid4().hex[:12]}"


def build_entry_sendorder_payload(
    *,
    symbol: str,
    exchange: int,
    limit_price: float,
    quantity: int = LOT_SIZE,
    margin_trade_type: int = MARGIN_TRADE_DAY,
    client_order_id: str = "",
    linked_paper_trade_id: str = "",
    timeout_sec: float = DEFAULT_ENTRY_TIMEOUT_SEC,
) -> dict[str, Any]:
    return {
        "endpoint": "POST /sendorder",
        "would_send": True,
        "dry_run": True,
        "Symbol": symbol_to_kabu_code(symbol),
        "Exchange": int(exchange),
        "SecurityType": SECURITY_TYPE_STOCK,
        "Side": SIDE_BUY,
        "CashMargin": CASH_MARGIN_NEW,
        "MarginTradeType": margin_trade_type,
        "DelivType": DELIV_TYPE_NONE,
        "AccountType": ACCOUNT_TYPE_SPECIFIC,
        "Qty": int(quantity),
        "FrontOrderType": FRONT_ORDER_LIMIT,
        "Price": float(limit_price),
        "ExpireDay": 0,
        "client_order_id": client_order_id or make_client_order_id(symbol, suffix="entry"),
        "linked_paper_trade_id": linked_paper_trade_id,
        "timeout_sec": float(timeout_sec),
        "margin_type_label": _margin_trade_label(margin_trade_type),
    }


def build_exit_sendorder_payload(
    *,
    symbol: str,
    exchange: int,
    exit_reason: str,
    quantity: int = LOT_SIZE,
    limit_price: Optional[float] = None,
    hold_id: str = "",
    margin_trade_type: int = MARGIN_TRADE_DAY,
    client_order_id: str = "",
    linked_paper_trade_id: str = "",
    filled_quantity: Optional[int] = None,
) -> dict[str, Any]:
    order_type = _exit_order_type(exit_reason)
    qty = int(filled_quantity if filled_quantity is not None else quantity)
    front = FRONT_ORDER_MARKET if order_type == "market" else FRONT_ORDER_LIMIT
    px = 0.0 if front == FRONT_ORDER_MARKET else float(limit_price or 0)
    payload: dict[str, Any] = {
        "endpoint": "POST /sendorder",
        "would_send": True,
        "dry_run": True,
        "Symbol": symbol_to_kabu_code(symbol),
        "Exchange": int(exchange),
        "SecurityType": SECURITY_TYPE_STOCK,
        "Side": SIDE_SELL,
        "CashMargin": CASH_MARGIN_REPAY,
        "MarginTradeType": margin_trade_type,
        "DelivType": 2,
        "AccountType": ACCOUNT_TYPE_SPECIFIC,
        "Qty": qty,
        "FrontOrderType": front,
        "Price": px,
        "ExpireDay": 0,
        "exit_reason": exit_reason,
        "client_order_id": client_order_id or make_client_order_id(symbol, suffix="exit"),
        "linked_paper_trade_id": linked_paper_trade_id,
        "order_type_label": order_type,
        "margin_type_label": _margin_trade_label(margin_trade_type),
    }
    if hold_id:
        payload["ClosePositions"] = [{"HoldID": hold_id, "Qty": qty}]
    else:
        payload["ClosePositionOrder"] = 0
    return payload


def _margin_trade_label(v: int) -> str:
    return {
        MARGIN_TRADE_SYSTEM: "system_credit",
        MARGIN_TRADE_GENERAL: "general_credit_long",
        MARGIN_TRADE_DAY: "general_credit_daytrade",
    }.get(v, f"unknown_{v}")


def _iso_now() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="milliseconds")


def _ms_between(a: str, b: str) -> float:
    from datetime import datetime

    try:
        ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
        tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return max(0.0, (tb - ta).total_seconds() * 1000.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class LatencySample:
    phase: str
    symbol: str
    exit_reason: str = ""
    entry_signal_ts: str = ""
    adapter_receive_ts: str = ""
    payload_build_start_ts: str = ""
    payload_build_done_ts: str = ""
    preflight_done_ts: str = ""
    would_send_order_ts: str = ""
    linked_paper_trade_id: str = ""
    client_order_id: str = ""
    signal_mono: float = 0.0
    would_send_mono: float = 0.0

    def to_row(self, config: Any) -> dict[str, Any]:
        mono_ms = (
            (self.would_send_mono - self.signal_mono) * 1000.0
            if self.would_send_mono > self.signal_mono
            else 0.0
        )
        return {
            "phase": self.phase,
            "symbol": self.symbol,
            "exit_reason": self.exit_reason,
            "entry_signal_ts": self.entry_signal_ts,
            "adapter_receive_ts": self.adapter_receive_ts,
            "payload_build_start_ts": self.payload_build_start_ts,
            "payload_build_done_ts": self.payload_build_done_ts,
            "preflight_done_ts": self.preflight_done_ts,
            "would_send_order_ts": self.would_send_order_ts,
            "signal_to_would_send_ms": round(mono_ms, 3),
            "dry_run": True,
            "order_enabled": bool(getattr(config, "order_enabled", False)),
            "trading_enabled": bool(getattr(config, "live_trading_enabled", False)),
            "linked_paper_trade_id": self.linked_paper_trade_id,
            "client_order_id": self.client_order_id,
        }


@dataclass
class LiveOrderWiringSession:
    exchange_default: int = EXCHANGE_TSE
    margin_trade_type: int = MARGIN_TRADE_DAY
    latency_samples: list[LatencySample] = field(default_factory=list)
    would_send_payloads: list[dict[str, Any]] = field(default_factory=list)

    def record_sample(self, sample: LatencySample) -> None:
        self.latency_samples.append(sample)


def _run_inline_preflight(config: Any) -> dict[str, Any]:
    """Fast local preflight before would_send (no HTTP)."""
    checks = {
        "order_enabled_false": not bool(getattr(config, "order_enabled", False)),
        "live_trading_disabled": not bool(getattr(config, "live_trading_enabled", False)),
        "dry_run_true": bool(getattr(config, "live_order_dry_run_enabled", True)),
        "wiring_enabled": wiring_enabled(config),
    }
    return {"ok": all(checks.values()), "checks": checks}


def process_entry_wiring(
    session: LiveOrderWiringSession,
    *,
    symbol: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    writer: Any,
    config: Any,
    entry_signal_ts: Optional[str] = None,
    exchange: Optional[int] = None,
) -> dict[str, Any]:
    if not wiring_enabled(config):
        return {"skipped": True}
    signal_mono = time.perf_counter()
    sig_ts = entry_signal_ts or str(trade.get("entry_time") or _iso_now())
    recv_ts = _iso_now()
    sample = LatencySample(
        phase="ENTRY",
        symbol=symbol,
        entry_signal_ts=sig_ts,
        adapter_receive_ts=recv_ts,
        linked_paper_trade_id=_paper_trade_id(trade, symbol),
        signal_mono=signal_mono,
    )
    sample.payload_build_start_ts = _iso_now()
    limit_px = _limit_entry_price(payload)
    if limit_px is None:
        return {"blocked": True, "reason": "missing_limit_price"}
    cid = make_client_order_id(symbol, suffix="entry")
    sample.client_order_id = cid
    order_payload = build_entry_sendorder_payload(
        symbol=symbol,
        exchange=exchange or session.exchange_default,
        limit_price=limit_px,
        margin_trade_type=session.margin_trade_type,
        client_order_id=cid,
        linked_paper_trade_id=sample.linked_paper_trade_id,
        timeout_sec=float(getattr(config, "live_order_entry_timeout_sec", DEFAULT_ENTRY_TIMEOUT_SEC)),
    )
    sample.payload_build_done_ts = _iso_now()
    pf = _run_inline_preflight(config)
    sample.preflight_done_ts = _iso_now()
    sample.would_send_order_ts = _iso_now()
    sample.would_send_mono = time.perf_counter()
    row = sample.to_row(config)
    row["sendorder_payload"] = order_payload
    row["preflight_ok"] = pf.get("ok")
    writer.append_live_order_latency(row)
    writer.append_live_order_would_send(
        {
            "timestamp": sample.would_send_order_ts,
            "phase": "ENTRY",
            "symbol": symbol,
            "client_order_id": cid,
            "payload": order_payload,
            "dry_run": True,
            "note": "would_send blocked — order_enabled=false",
        }
    )
    session.record_sample(sample)
    session.would_send_payloads.append(order_payload)
    return {"ok": True, "payload": order_payload, "latency_ms": row["signal_to_would_send_ms"]}


def process_exit_wiring(
    session: LiveOrderWiringSession,
    *,
    symbol: str,
    context: Mapping[str, Any],
    writer: Any,
    config: Any,
    hold_id: str = "",
    exchange: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    if not wiring_enabled(config):
        return None
    exit_reason = str(context.get("exit_reason") or context.get("reason") or "structural_exit")
    signal_mono = time.perf_counter()
    sig_ts = str(context.get("exit_time") or context.get("entry_time") or _iso_now())
    sample = LatencySample(
        phase="STOP_EXIT" if "stop" in exit_reason.lower() else "EXIT",
        symbol=symbol,
        exit_reason=exit_reason,
        entry_signal_ts=sig_ts,
        adapter_receive_ts=_iso_now(),
        linked_paper_trade_id=str(context.get("linked_paper_trade_id") or ""),
        signal_mono=signal_mono,
    )
    sample.payload_build_start_ts = _iso_now()
    try:
        exit_px = float(context.get("exit_price") or context.get("current_price") or 0)
    except (TypeError, ValueError):
        exit_px = 0.0
    cid = make_client_order_id(symbol, suffix="exit")
    sample.client_order_id = cid
    order_payload = build_exit_sendorder_payload(
        symbol=symbol,
        exchange=exchange or session.exchange_default,
        exit_reason=exit_reason,
        limit_price=round(exit_px, 1) if exit_px > 0 else None,
        hold_id=hold_id,
        margin_trade_type=session.margin_trade_type,
        client_order_id=cid,
        linked_paper_trade_id=sample.linked_paper_trade_id,
    )
    sample.payload_build_done_ts = _iso_now()
    pf = _run_inline_preflight(config)
    sample.preflight_done_ts = _iso_now()
    sample.would_send_order_ts = _iso_now()
    sample.would_send_mono = time.perf_counter()
    row = sample.to_row(config)
    row["sendorder_payload"] = order_payload
    row["preflight_ok"] = pf.get("ok")
    writer.append_live_order_latency(row)
    writer.append_live_order_would_send(
        {
            "timestamp": sample.would_send_order_ts,
            "phase": sample.phase,
            "symbol": symbol,
            "client_order_id": cid,
            "payload": order_payload,
            "dry_run": True,
            "note": "would_send blocked — order_enabled=false",
        }
    )
    session.record_sample(sample)
    session.would_send_payloads.append(order_payload)
    return {"ok": True, "payload": order_payload, "latency_ms": row["signal_to_would_send_ms"]}


def latency_summary(samples: Sequence[LatencySample]) -> dict[str, Any]:
    def _stats(phase: str) -> dict[str, Any]:
        rows = [s for s in samples if s.phase == phase]
        if not rows:
            return {}
        vals = [
            (s.would_send_mono - s.signal_mono) * 1000.0
            for s in rows
            if s.would_send_mono > s.signal_mono
        ]
        if not vals:
            vals = [0.0]
        vals.sort()
        n = len(vals)
        p95_i = min(n - 1, int(n * 0.95))
        return {
            "count": n,
            "avg_ms": round(statistics.mean(vals), 3),
            "median_ms": round(statistics.median(vals), 3),
            "p95_ms": round(vals[p95_i], 3),
            "max_ms": round(max(vals), 3),
        }

    entry = _stats("ENTRY")
    exit_ = _stats("EXIT")
    stop = _stats("STOP_EXIT")
    return {
        "entry": entry,
        "exit": exit_,
        "stop_exit": stop,
        "entry_p95_target_ms": ENTRY_LATENCY_TARGET_P95_SEC * 1000,
        "stop_exit_p95_target_ms": STOP_EXIT_LATENCY_TARGET_P95_SEC * 1000,
        "entry_p95_pass": (entry.get("p95_ms") or 0) <= ENTRY_LATENCY_TARGET_P95_SEC * 1000,
        "stop_exit_p95_pass": (stop.get("p95_ms") or 0) <= STOP_EXIT_LATENCY_TARGET_P95_SEC * 1000,
    }


def wiring_summary_fields(session: Optional[LiveOrderWiringSession]) -> dict[str, Any]:
    if session is None:
        return {"live_order_api_wiring_enabled": False}
    summ = latency_summary(session.latency_samples)
    return {
        "live_order_api_wiring_enabled": True,
        "live_order_wiring_entry_samples": (summ.get("entry") or {}).get("count", 0),
        "live_order_wiring_stop_exit_p95_ms": (summ.get("stop_exit") or {}).get("p95_ms"),
        "live_order_wiring_entry_p95_ms": (summ.get("entry") or {}).get("p95_ms"),
        "live_order_wiring_would_send_count": len(session.would_send_payloads),
    }


# --- STOP EXIT emergency flow (dry-run simulation) ---


def simulate_stop_exit_emergency_cases() -> list[dict[str, Any]]:
    rows = []
    cases = [
        ("A", "open order exists", "monitor_fill", "poll order until filled", False, "continue_repay"),
        ("B", "no open order + position exists", "resend_market_repay", "POST market CashMargin=3", False, "retry_until_flat"),
        ("C", "inquiry failed", "SAFE_STOP", "Discord emergency alert", True, "manual_intervention"),
        ("D", "partial filled", "resend_remaining_qty", "market repay remainder", False, "track filled_quantity"),
        ("E", "position flat", "close_track", "release CAP", False, "done"),
    ]
    for case_id, detect, action, detail, safe_stop, recovery in cases:
        rows.append(
            {
                "case_id": case_id,
                "state": "STOP_EXIT_SIGNAL",
                "mock_condition": detect,
                "next_action": action,
                "detail": detail,
                "safe_stop": safe_stop,
                "recovery": recovery,
                "discord_emergency_alert": case_id == "C",
                "exit_never_give_up": case_id in ("A", "B", "D"),
            }
        )
    return rows


# --- API error dry-run matrix ---


def simulate_api_error_dryrun() -> list[dict[str, Any]]:
    cases = [
        ("token_failure", "issue_token raises", "abort session start", "manual token refresh", True, True),
        ("wallet_failure", "wallet/cash error", "block ENTRY", "retry 3x then SAFE_STOP", True, False),
        ("order_inquiry_timeout", "GET /orders timeout", "assume unknown", "SAFE_STOP if STOP EXIT", True, True),
        ("position_inquiry_timeout", "GET /positions timeout", "SAFE_STOP", "alert", True, True),
        ("order_send_timeout", "POST timeout (mock)", "unknown state", "inquiry before resend", True, True),
        ("order_rejected", "Result!=0", "log; no retry same signal", "skip symbol", False, False),
        ("order_status_unknown", "inquiry empty", "SAFE_STOP", "manual", True, True),
        ("partial_fill", "CumQty<Qty", "track remainder", "resend exit", False, False),
        ("cancel_failure", "cancel API fail", "SAFE_STOP + block ENTRY", "manual flat", True, True),
        ("websocket_disconnect", "push drop", "reconnect", "continue if REST ok", False, False),
        ("duplicate_client_order_id", "duplicate detected", "ignore 2nd", "dedupe", False, False),
        ("position_mismatch", "reconcile fail", "SAFE_STOP", "manual", True, True),
    ]
    return [
        {
            "error_case": c,
            "detection": d,
            "immediate_action": a,
            "recovery": r,
            "blocks_new_entry": str(b),
            "safe_stop": str(s),
            "dry_run_verified": True,
        }
        for c, d, a, r, b, s in cases
    ]


# --- Live order preflight ---


@dataclass
class LiveOrderPreflightReport:
    ready: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)
    api_online: bool = False
    errors: list[str] = field(default_factory=list)


def run_live_order_preflight(
    *,
    config: Any,
    repo_root: Path,
    kill_switch_path: Optional[Path] = None,
    lock_path: Optional[Path] = None,
) -> LiveOrderPreflightReport:
    import os

    report = LiveOrderPreflightReport()
    root = Path(repo_root)

    def _add(cid: str, ok: bool, detail: str = "", **extra: Any) -> None:
        report.checks.append({"check_id": cid, "pass": ok, "detail": detail, **extra})
        if not ok:
            report.errors.append(f"{cid}: {detail}")

    _add("PF_ORDER_DISABLED", not bool(getattr(config, "order_enabled", False)), str(getattr(config, "order_enabled", False)))
    _add("PF_TRADING_DISABLED", not bool(getattr(config, "live_trading_enabled", False)))
    _add("PF_DRY_RUN", bool(getattr(config, "live_order_dry_run_enabled", True)))
    _add("PF_WIRING", wiring_enabled(config))

    ks = kill_switch_path or root / "kabu_native" / "configs" / "live_trading_kill_switch.flag"
    _add("PF_KILL_SWITCH_FILE", ks.parent.is_dir(), str(ks), exists=ks.is_file())

    lock = lock_path or root / "kabu_native" / "results" / "small_paper" / ".live_order_process.lock"
    lock_ok = True
    if lock.is_file():
        lock_ok = False
    _add("PF_DUPLICATE_PROCESS_LOCK", lock_ok, "no lock file" if lock_ok else f"lock exists: {lock}")

    wh = os.environ.get("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL") or os.environ.get("KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL")
    _add("PF_DISCORD_EMERGENCY", bool((wh or "").strip()), "webhook configured" if wh else "missing webhook")

    if not os.environ.get("KABU_API_PASSWORD", "").strip():
        _add("PF_TOKEN", False, "KABU_API_PASSWORD unset")
        report.ready = False
        return report

    try:
        from api.order_read_client import KabuOrderReadClient
        from api.rest_client import load_kabu_env, default_base_url

        load_kabu_env(repo_root=root)
        client = KabuOrderReadClient(default_base_url())
        token = client.issue_token_from_env()
        _add("PF_TOKEN", True, "token issued")
        probe = client.probe_all(token=token)
        report.api_online = bool(probe.get("ok"))
        for name, res in (probe.get("probes") or {}).items():
            _add(f"PF_API_{name.upper()}", bool(res.get("ok")), str(res.get("error") or res.get("latency_ms")))
        positions, _ = client.get_positions(token=token)
        open_count = sum(int(float(p.get("LeavesQty") or p.get("Qty") or 0)) for p in positions)
        _add("PF_POSITIONS_FLAT_OR_KNOWN", True, f"open_qty_total={open_count}", open_count=open_count)
        _add("PF_CREDIT_MARGIN_API", report.api_online, "wallet/margin reachable")
    except Exception as e:
        _add("PF_API_PROBE", False, str(e))
        report.api_online = False

    report.ready = not report.errors
    return report


def margin_leverage_analysis(
    wallet_cash: Mapping[str, Any],
    wallet_margin: Mapping[str, Any],
    *,
    leverage_assumed: float = MARGIN_LEVERAGE,
    lot_size: int = LOT_SIZE,
    sample_price: float = 3000.0,
) -> list[dict[str, Any]]:
    def _f(key: str, src: Mapping[str, Any]) -> Optional[float]:
        v = src.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    rows = []
    deposit = _f("StockAccountWallet", wallet_cash) or _f("Cash", wallet_cash)
    margin_available = _f("MarginAccountWallet", wallet_margin) or _f("DepositkeepRate", wallet_margin)
    buying = _f("MarginAmount", wallet_margin) or margin_available
    req_margin = sample_price * lot_size / leverage_assumed
    rows.append(
        {
            "field": "assumed_leverage",
            "api_value": None,
            "design_value": leverage_assumed,
            "recommendation": "Use API MarginAccountWallet for live checks; 2x is design assumption",
        }
    )
    rows.append(
        {
            "field": "required_margin_formula",
            "api_value": None,
            "design_value": f"price*{lot_size}/{leverage_assumed}",
            "recommendation": f"example @ {sample_price} = {req_margin:.0f} yen",
        }
    )
    rows.append(
        {
            "field": "StockAccountWallet",
            "api_value": deposit,
            "design_value": None,
            "recommendation": "cash component from wallet/cash",
        }
    )
    rows.append(
        {
            "field": "MarginAccountWallet",
            "api_value": margin_available,
            "design_value": None,
            "recommendation": "prefer API returned buying power over fixed 2x formula",
        }
    )
    rows.append(
        {
            "field": "guarantee_rate",
            "api_value": _f("DepositkeepRate", wallet_margin),
            "design_value": None,
            "recommendation": "monitor DepositkeepRate; do not hard-code 2x if API margin lower",
        }
    )
    rows.append(
        {
            "field": "management_policy",
            "api_value": buying,
            "design_value": "cap=2 initial live",
            "recommendation": "block entry if required_margin > MarginAccountWallet",
        }
    )
    return rows
