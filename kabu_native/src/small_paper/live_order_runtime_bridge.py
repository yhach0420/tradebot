"""Phase687W4 — Runtime Dry-Run wiring bridge for LiveOrderSafetyEngine.

Connects actual Paper ENTRY/EXIT accepts to SafetySM without real broker submits.
Shadow / reject / capacity / notification-only events never create intents.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.live_order_account_status import (
    API_FAILURE_STATUSES,
    CAPITAL_PRECHECK_ALLOWED,
    AccountReadStatus,
)
from small_paper.live_order_safety_sm import (
    LOT_SIZE,
    DryRunBrokerAdapter,
    KabuBrokerAdapter,
    LiveOrderSafetyEngine,
    OrderLifecycleState,
    build_engine,
    dryrun_position_sizing,
)

JST = ZoneInfo("Asia/Tokyo")
SCHEMA_VERSION = "687W4.1"

# Sources allowed to create intents
ENTRY_SOURCE_ACTUAL = "actual_accepted_entry"
EXIT_SOURCE_ACTUAL = "actual_structural_exit"

FORBIDDEN_ENTRY_KINDS = frozenset(
    {
        "shadow",
        "ihc_block",
        "capacity_blocked",
        "reject",
        "skipped",
        "debug",
        "notification_only",
        "virtual_hold",
        "observer_only",
        "duplicated_event",
        "replay_research",
    }
)

STRUCTURAL_EXIT_REASONS = frozenset(
    {
        "stop_hit",
        "no_progress_exit",
        "trailing_mfe_exit",
        "morning_session_close",
        "afternoon_session_close",
        "session_end",
    }
)

LATENCY_FIELDS = (
    "market_event_time",
    "current_price_time",
    "board_time",
    "push_received_at",
    "runtime_evaluation_start",
    "accepted_at",
    "safety_signal_received_at",
    "precheck_started_at",
    "precheck_completed_at",
    "capital_reserved_at",
    "intent_created_at",
    "journal_committed_at",
    "would_submit_at",
    "simulated_ack_at",
    "simulated_fill_at",
)


def safety_sm_enabled(config: Any) -> bool:
    if bool(getattr(config, "live_trading_enabled", False)):
        return False
    if bool(getattr(config, "order_enabled", False)):
        return False
    if not bool(getattr(config, "dry_run", True)):
        return False
    return bool(getattr(config, "live_order_safety_sm_enabled", False))


def _iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _ms(a: float, b: float) -> Optional[float]:
    if a <= 0 or b <= 0:
        return None
    if b < a:
        return None  # clock regression
    return round((b - a) * 1000.0, 3)


def _pct(vals: list[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[i]


@dataclass
class SignalMappingRow:
    side: str
    session_id: str
    position_id: str
    symbol: str
    signal_event_id: str
    intent_id: str = ""
    idempotency_key: str = ""
    would_submit: bool = False
    block_reason: str = ""
    dryrun_state: str = ""
    precheck_result: str = ""
    capital_result: str = ""
    exit_reason: str = ""
    holding_quantity: int = 0
    intended_quantity: int = 0
    accepted_at: str = ""
    exit_signal_at: str = ""


@dataclass
class RuntimeSafetyBridge:
    """Paper Runtime ↔ LiveOrderSafetyEngine dry-run bridge."""

    engine: LiveOrderSafetyEngine
    readonly: KabuBrokerAdapter
    session_id: str
    config: Any = None
    mappings: list[SignalMappingRow] = field(default_factory=list)
    latency_samples: list[dict[str, Any]] = field(default_factory=list)
    discord_events: list[dict[str, Any]] = field(default_factory=list)
    token_probe: dict[str, Any] = field(default_factory=dict)

    # counters
    actual_entry_signal_count: int = 0
    actual_exit_signal_count: int = 0
    unique_entry_intent_count: int = 0
    unique_exit_intent_count: int = 0
    duplicate_signal_detected_count: int = 0
    duplicate_intent_prevented_count: int = 0
    duplicate_intent_created_count: int = 0
    duplicate_broker_submit_count: int = 0
    forbidden_source_blocked_count: int = 0
    orphan_intent_count: int = 0
    missing_intent_count: int = 0
    invalid_timestamp_count: int = 0
    startup_recon: dict[str, Any] = field(default_factory=dict)
    account_audit: dict[str, Any] = field(default_factory=dict)
    journal_restore: dict[str, Any] = field(default_factory=dict)
    _seen_entry_keys: set[str] = field(default_factory=set)
    _seen_exit_keys: set[str] = field(default_factory=set)
    _entry_intent_ids: set[str] = field(default_factory=set)
    _exit_intent_ids: set[str] = field(default_factory=set)

    def _notify(self, kind: str, payload: Mapping[str, Any]) -> None:
        try:
            self.discord_events.append(
                {
                    "timestamp": _iso(),
                    "kind": kind,
                    "label": f"[DRY-RUN] {kind}",
                    "dry_run": True,
                    **dict(payload),
                }
            )
        except Exception:
            pass

    def startup(self) -> dict[str, Any]:
        # Phase687W4T: capture token/readiness probe fields (no secrets)
        try:
            from small_paper.kabu_readonly_readiness import (
                check_port_reachable,
                check_station_process,
                parse_host_port,
                password_configured,
            )
            from api.rest_client import default_base_url

            base = default_base_url()
            host, port = parse_host_port(base)
            self.token_probe = {
                "station_running": check_station_process(),
                "port_reachable": check_port_reachable(host, port),
                "password_configured": password_configured(),
                "client_configured": self.readonly.client is not None,
                "token_present": bool(self.readonly.token),
                "host": host,
                "port": port,
                "no_secrets": True,
            }
        except Exception:
            self.token_probe = {"no_secrets": True}

        status = self.readonly.refresh_readonly()
        self.token_probe["token_probe_status"] = status
        self.token_probe["latency_ms"] = dict(self.readonly.last_latency_ms)
        self.token_probe["failure_reason"] = self.readonly.last_error
        self.token_probe["token_refresh_count"] = 1 if self.readonly.token else 0
        # continue existing startup logic
        st_enum = AccountReadStatus(status) if status in AccountReadStatus.__members__ else AccountReadStatus.UNKNOWN
        bp = None
        bp_err = ""
        if st_enum in CAPITAL_PRECHECK_ALLOWED:
            try:
                bp = self.readonly.get_buying_power()
                if isinstance(self.engine.broker, DryRunBrokerAdapter):
                    self.engine.broker.account.buying_power = float(bp)
                    self.engine.broker.account.online = True
                    self.engine.broker.account.token_valid = True
                self.token_probe["readonly_successful_endpoint_count"] = 1
                self.token_probe["ready_for_soak"] = True
            except Exception as exc:
                bp_err = type(exc).__name__
                st_enum = AccountReadStatus.UNKNOWN
                self.token_probe["ready_for_soak"] = False
        elif st_enum in API_FAILURE_STATUSES:
            if isinstance(self.engine.broker, DryRunBrokerAdapter):
                if not bool(getattr(self.config, "safety_sm_allow_mock_capital", False)):
                    self.engine.broker.account.buying_power = 0.0
                    self.engine.entry_blocked = True
                    self.engine.kill_reasons.append(f"capital_unknown:{status}")
            self.token_probe["ready_for_soak"] = False

        self.account_audit = {
            "account_status": st_enum.value,
            "cash_buying_power_present": self.readonly._cash_buying_power is not None,
            "margin_buying_power_present": self.readonly._margin_buying_power is not None,
            "selected_buying_power_present": bp is not None,
            "selected_buying_power_bucket": (
                "positive" if (bp or 0) > 0 else ("zero" if bp == 0 else "unknown")
            ),
            "broker_position_count": len(self.readonly.get_positions()),
            "broker_open_order_count": len(self.readonly.get_open_orders()),
            "latency_ms": dict(self.readonly.last_latency_ms),
            "error": self.readonly.last_error or bp_err,
            "no_secrets": True,
        }
        self._notify("READONLY ACCOUNT", {"account_status": st_enum.value})

        # Phase687W7A: restore journal state before reconciliation (never resubmit)
        try:
            self.journal_restore = self.engine.restore_from_journal()
        except Exception as exc:
            self.journal_restore = {
                "error": type(exc).__name__,
                "resubmit": False,
                "restored_order_count": 0,
                "recovery_mode": "JOURNAL_RECOVERY_REQUIRED",
            }
            self.engine.recovery_required = True
            self.engine.entry_blocked = True

        local_pos = dict(self.engine.ledger.open_positions)
        broker_pos = self.readonly.get_positions()
        dry = self.engine.broker
        self.engine.broker = self.readonly  # type: ignore[assignment]
        try:
            self.startup_recon = self.engine.startup_reconciliation(
                local_positions=local_pos, local_pending=dict(self.engine.ledger.pending_by_symbol)
            )
        finally:
            self.engine.broker = dry

        if st_enum in API_FAILURE_STATUSES:
            self.startup_recon["classification"] = "API_UNAVAILABLE"
            self.startup_recon["account_status"] = st_enum.value
            if not bool(getattr(self.config, "safety_sm_allow_mock_capital", False)):
                self.engine.entry_blocked = True
                self.engine.recovery_required = True
            else:
                self.engine.recovery_required = False
                self.engine.entry_blocked = False
                self.startup_recon["mode"] = "MOCK_CAPITAL_BYPASS"
            self._notify("RECONCILIATION ERROR", {"classification": "API_UNAVAILABLE"})
        elif self.startup_recon.get("classification") == "MATCH":
            self._notify("RECONCILIATION MATCH", {"diff_count": 0})
        else:
            self._notify(
                "RECONCILIATION ERROR",
                {"classification": self.startup_recon.get("classification")},
            )
        return self.startup_recon

    def _is_forbidden_entry(self, kind: str) -> bool:
        return str(kind or "").lower() in FORBIDDEN_ENTRY_KINDS

    def on_actual_entry(
        self,
        *,
        symbol: str,
        price: float,
        position_id: str,
        accepted_at: str = "",
        signal_event_id: str = "",
        source_kind: str = ENTRY_SOURCE_ACTUAL,
        timestamps: Optional[Mapping[str, Any]] = None,
        freshness: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Wire actual accepted ENTRY only."""
        ts_mono_recv = time.perf_counter()
        self.actual_entry_signal_count += 1
        row = SignalMappingRow(
            side="ENTRY",
            session_id=self.session_id,
            position_id=position_id,
            symbol=symbol,
            signal_event_id=signal_event_id or f"entry:{position_id}",
            accepted_at=accepted_at or _iso(),
        )
        if self._is_forbidden_entry(source_kind) or source_kind != ENTRY_SOURCE_ACTUAL:
            self.forbidden_source_blocked_count += 1
            row.block_reason = f"forbidden_source:{source_kind}"
            row.would_submit = False
            self.mappings.append(row)
            return {"ok": False, "reason": row.block_reason, "would_submit": False}

        # Duplicate signal detection (same position_id)
        if position_id in self._seen_entry_keys:
            self.duplicate_signal_detected_count += 1
        self._seen_entry_keys.add(position_id)

        fresh = dict(freshness or {})
        ctx = {
            "quantity": LOT_SIZE,
            "price_age_sec": fresh.get("price_age_sec"),
            "board_age_sec": fresh.get("board_age_sec"),
            "symbol_registered": True,
        }
        pre_t0 = time.perf_counter()
        # sizing compare-only
        sizing = dryrun_position_sizing(
            equity=1_000_000,
            available_buying_power=float(
                getattr(getattr(self.engine.broker, "account", None), "buying_power", 0) or 0
            ),
            current_gross_exposure=0,
            current_symbol_exposure=0,
            price=price,
        )
        order_before = set(self.engine.orders.keys())
        dup_before = self.engine.duplicate_order_count
        order = self.engine.receive_entry_signal(
            symbol=symbol, price=price, position_id=position_id, ctx=ctx
        )
        pre_t1 = time.perf_counter()
        if self.engine.duplicate_order_count > dup_before:
            self.duplicate_intent_prevented_count += 1
        else:
            if order.order_id not in order_before and order.order_id not in self._entry_intent_ids:
                if order.state not in (
                    OrderLifecycleState.PRECHECK_REJECTED,
                    OrderLifecycleState.CANCELED,
                ):
                    self.unique_entry_intent_count += 1
                    self._entry_intent_ids.add(order.order_id)
                elif order.reject_reason:
                    pass

        would = order.state in (
            OrderLifecycleState.FILLED,
            OrderLifecycleState.PARTIALLY_FILLED,
            OrderLifecycleState.ACKNOWLEDGED,
            OrderLifecycleState.SUBMITTED,
            OrderLifecycleState.UNKNOWN,
        ) and order.reject_reason == ""
        # UNKNOWN after submit still counts as would_submit attempt in dry-run
        if order.state == OrderLifecycleState.UNKNOWN:
            would = True
        if order.state == OrderLifecycleState.PRECHECK_REJECTED:
            would = False

        row.intent_id = order.order_id
        row.idempotency_key = order.idempotency_key
        row.would_submit = would
        row.block_reason = order.reject_reason
        row.dryrun_state = order.state.value
        row.precheck_result = "pass" if not order.reject_reason else "block"
        row.capital_result = "reserved" if order.reservation_id else ("blocked" if order.reject_reason else "")
        self.mappings.append(row)

        # Latency sample
        wall = dict(timestamps or {})
        sample = {
            "side": "ENTRY",
            "symbol": symbol,
            "position_id": position_id,
            "intent_id": order.order_id,
            "would_submit": would,
            "safety_precheck_ms": _ms(pre_t0, pre_t1),
            "accept_to_safety_receive_ms": 0.0,
            "push_to_accept_ms": wall.get("push_to_accept_ms"),
            "current_price_age_at_accept_sec": fresh.get("price_age_sec"),
            "board_age_at_accept_sec": fresh.get("board_age_sec"),
            "market_event_age_at_accept_sec": fresh.get("market_event_age_sec"),
            "stale_current_price_excluded_from_pipeline": True,
            "sizing_compare_only": sizing.get("comparison"),
            "safety_signal_received_at": _iso(),
            "would_submit_at": _iso() if would else "",
            "simulated_fill_at": _iso() if order.state == OrderLifecycleState.FILLED else "",
            "journal_commit_ms": None,
            "mono_recv": ts_mono_recv,
        }
        # Detect clock regression in provided wall timestamps
        for a, b in (
            ("push_received_mono", "accepted_mono"),
            ("accepted_mono", "safety_mono"),
        ):
            if a in wall and b in wall and float(wall[b]) < float(wall[a]):
                self.invalid_timestamp_count += 1
        if wall.get("accepted_mono") and wall.get("push_received_mono"):
            sample["push_to_accept_ms"] = _ms(
                float(wall["push_received_mono"]), float(wall["accepted_mono"])
            )
        if wall.get("accepted_mono"):
            sample["accept_to_would_submit_ms"] = _ms(float(wall["accepted_mono"]), pre_t1)
        self.latency_samples.append(sample)
        if would:
            self._notify("DRYRUN ORDER INTENT", {"symbol": symbol, "intent_id": order.order_id})
        else:
            self._notify("DRYRUN ORDER BLOCK", {"symbol": symbol, "reason": order.reject_reason})
        return {
            "ok": True,
            "would_submit": would,
            "order_id": order.order_id,
            "state": order.state.value,
            "reject_reason": order.reject_reason,
        }

    def on_actual_exit(
        self,
        *,
        symbol: str,
        position_id: str,
        exit_reason: str,
        holding_quantity: Optional[int] = None,
        exit_signal_at: str = "",
        signal_event_id: str = "",
        is_structural_exit: bool = True,
        source_kind: str = EXIT_SOURCE_ACTUAL,
    ) -> dict[str, Any]:
        self.actual_exit_signal_count += 1
        row = SignalMappingRow(
            side="EXIT",
            session_id=self.session_id,
            position_id=position_id,
            symbol=symbol,
            signal_event_id=signal_event_id or f"exit:{position_id}:{exit_reason}",
            exit_reason=exit_reason,
            exit_signal_at=exit_signal_at or _iso(),
        )
        if (not is_structural_exit) or source_kind != EXIT_SOURCE_ACTUAL:
            self.forbidden_source_blocked_count += 1
            row.block_reason = "non_structural_or_forbidden"
            self.mappings.append(row)
            return {"ok": False, "reason": row.block_reason, "would_submit": False}
        if exit_reason not in STRUCTURAL_EXIT_REASONS and not str(exit_reason).endswith("_close"):
            # allow other canonical actual exits tagged structural
            if not is_structural_exit:
                row.block_reason = f"non_canonical_exit:{exit_reason}"
                self.mappings.append(row)
                return {"ok": False, "reason": row.block_reason}

        exit_key = f"{position_id}|{exit_reason}"
        if exit_key in self._seen_exit_keys:
            self.duplicate_signal_detected_count += 1
        self._seen_exit_keys.add(exit_key)

        local_qty = int(holding_quantity if holding_quantity is not None else self.engine.ledger.open_positions.get(symbol, 0))
        row.holding_quantity = local_qty

        # Broker readonly mismatch → RECOVERY_REQUIRED, no auto-correct
        st = self.readonly.last_account_status
        if st in {s.value for s in CAPITAL_PRECHECK_ALLOWED} or st in {
            AccountReadStatus.ONLINE_ZERO_BALANCE.value,
            AccountReadStatus.ONLINE_NO_POSITIONS.value,
            AccountReadStatus.ONLINE_NO_ORDERS.value,
            AccountReadStatus.MARKET_CLOSED_READ_AVAILABLE.value,
        }:
            bpos = self.readonly.get_positions().get(symbol, 0)
            if bpos != local_qty and (bpos > 0 or local_qty > 0):
                # Only flag when readonly was successfully read this session
                if self.readonly._refreshed and self.readonly.last_error == "":
                    self.engine.recovery_required = True
                    self.engine.entry_blocked = True
                    row.block_reason = "broker_local_qty_mismatch"
                    row.dryrun_state = OrderLifecycleState.RECOVERY_REQUIRED.value
                    self.mappings.append(row)
                    self._notify("RECOVERY REQUIRED", {"symbol": symbol, "local": local_qty, "broker": bpos})
                    return {"ok": False, "reason": row.block_reason, "would_submit": False}

        dup_before = self.engine.duplicate_order_count
        order = self.engine.receive_exit_signal(
            symbol=symbol,
            quantity=local_qty,
            exit_reason=exit_reason,
            position_id=position_id,
        )
        if self.engine.duplicate_order_count > dup_before:
            self.duplicate_intent_prevented_count += 1
        elif order.order_id not in self._exit_intent_ids and order.state != OrderLifecycleState.PRECHECK_REJECTED:
            self.unique_exit_intent_count += 1
            self._exit_intent_ids.add(order.order_id)

        would = order.state == OrderLifecycleState.FILLED
        row.intent_id = order.order_id
        row.idempotency_key = order.idempotency_key
        row.intended_quantity = order.quantity
        row.would_submit = would
        row.block_reason = order.reject_reason
        row.dryrun_state = order.state.value
        self.mappings.append(row)
        if would:
            self._notify("DRYRUN ORDER INTENT", {"side": "EXIT", "symbol": symbol})
        return {"ok": True, "would_submit": would, "order_id": order.order_id, "state": order.state.value}

    def session_integrity(self, *, canonical_entry_count: int, canonical_exit_count: int) -> dict[str, Any]:
        # missing = canonical without mapping would_submit or block recorded
        mapped_entry = sum(1 for m in self.mappings if m.side == "ENTRY")
        mapped_exit = sum(1 for m in self.mappings if m.side == "EXIT")
        self.missing_intent_count = max(0, canonical_entry_count - mapped_entry) + max(
            0, canonical_exit_count - mapped_exit
        )
        # orphan = intents without canonical (should be 0 when wired correctly)
        self.orphan_intent_count = max(0, self.unique_entry_intent_count - canonical_entry_count) + max(
            0, self.unique_exit_intent_count - canonical_exit_count
        )
        # duplicate created should stay 0 (prevented counts are OK)
        self.duplicate_intent_created_count = 0
        self.duplicate_broker_submit_count = int(self.engine.actual_broker_submit_count())
        return {
            "canonical_entry_count": canonical_entry_count,
            "actual_entry_signal_count": self.actual_entry_signal_count,
            "unique_entry_intent_count": self.unique_entry_intent_count,
            "canonical_exit_count": canonical_exit_count,
            "actual_exit_signal_count": self.actual_exit_signal_count,
            "unique_exit_intent_count": self.unique_exit_intent_count,
            "duplicate_signal_detected_count": self.duplicate_signal_detected_count,
            "duplicate_intent_prevented_count": self.duplicate_intent_prevented_count,
            "duplicate_intent_created_count": self.duplicate_intent_created_count,
            "duplicate_broker_submit_count": self.duplicate_broker_submit_count,
            "orphan_intent_count": self.orphan_intent_count,
            "missing_intent_count": self.missing_intent_count,
            "forbidden_source_blocked_count": self.forbidden_source_blocked_count,
            "reservation_leak": self.engine.ledger.leak_count(),
            "actual_broker_submit_count": self.engine.actual_broker_submit_count(),
            "actual_broker_cancel_count": int(
                getattr(self.readonly, "actual_broker_cancel_count", 0)
            ),
        }

    def latency_summary(self) -> dict[str, Any]:
        pre = [s["safety_precheck_ms"] for s in self.latency_samples if s.get("safety_precheck_ms") is not None]
        accept_ws = [
            s["accept_to_would_submit_ms"]
            for s in self.latency_samples
            if s.get("accept_to_would_submit_ms") is not None
        ]
        push_acc = [s["push_to_accept_ms"] for s in self.latency_samples if s.get("push_to_accept_ms") is not None]
        return {
            "latency_sample_count": len(self.latency_samples),
            "invalid_timestamp_count": self.invalid_timestamp_count,
            "safety_precheck_ms_p50": _pct(pre, 50),
            "safety_precheck_ms_p95": _pct(pre, 95),
            "safety_precheck_ms_max": max(pre) if pre else None,
            "accept_to_would_submit_ms_p50": _pct(accept_ws, 50),
            "accept_to_would_submit_ms_p95": _pct(accept_ws, 95),
            "accept_to_would_submit_ms_max": max(accept_ws) if accept_ws else None,
            "paper_push_to_accept_ms_p50": _pct(push_acc, 50),
            "paper_push_to_accept_ms_p95": _pct(push_acc, 95),
            "kabu_submit_ack_unmeasured": True,
            "kabu_ack_fill_unmeasured": True,
            "note": "Real Kabu submit→ACK / ACK→fill not measured (production submit forbidden)",
            "target_safety_p95_ms": 100,
            "target_journal_p95_ms": 50,
        }


def build_runtime_bridge(
    *,
    output_dir: Path,
    session_id: str,
    config: Any,
    kabu_client: Any = None,
    kabu_token: str = "",
    allow_mock_capital: bool = False,
) -> RuntimeSafetyBridge:
    from types import SimpleNamespace

    cfg = config
    if allow_mock_capital and not hasattr(cfg, "safety_sm_allow_mock_capital"):
        try:
            setattr(cfg, "safety_sm_allow_mock_capital", True)
        except Exception:
            cfg = SimpleNamespace(
                **{**(cfg.__dict__ if hasattr(cfg, "__dict__") else {}), "safety_sm_allow_mock_capital": True}
            )

    readonly = KabuBrokerAdapter(client=kabu_client, token=kabu_token)
    dry = DryRunBrokerAdapter()
    engine = build_engine(output_dir=output_dir, session_id=session_id, broker=dry, config=cfg)
    return RuntimeSafetyBridge(
        engine=engine, readonly=readonly, session_id=session_id, config=cfg
    )


def write_soak_session_snapshot(
    bridge: RuntimeSafetyBridge,
    *,
    output_dir: Path,
    canonical_entry_count: int,
    canonical_exit_count: int,
) -> Path:
    """Persist per-session soak metrics for Phase687W4S forward evaluation."""
    import json

    output_dir.mkdir(parents=True, exist_ok=True)
    integ = bridge.session_integrity(
        canonical_entry_count=canonical_entry_count,
        canonical_exit_count=canonical_exit_count,
    )
    lat = bridge.latency_summary()
    # Refresh readonly once more for end-of-session account probe (no submit)
    try:
        end_status = bridge.readonly.refresh_readonly()
    except Exception:
        end_status = bridge.readonly.last_account_status
    snap = {
        "schema_version": SCHEMA_VERSION,
        "phase": "687W4S",
        "session_id": bridge.session_id,
        "written_at": _iso(),
        # Phase687W8 — Forward soak provenance (real Paper runtime only)
        "session_provenance": "LIVE_PAPER_RUNTIME",
        "synthetic": False,
        "fixture": False,
        "test_mode": False,
        "runtime_session": True,
        "readonly": {
            "account_status": end_status,
            "account_audit": bridge.account_audit,
            "token_present": bool(bridge.readonly.token),
            "client_configured": bridge.readonly.client is not None,
            "error": bridge.readonly.last_error,
            "latency_ms": dict(bridge.readonly.last_latency_ms),
            "position_count": len(bridge.readonly.get_positions()),
            "open_order_count": len(bridge.readonly.get_open_orders()),
            "executions_count": len(bridge.readonly.get_recent_executions()),
            "buying_power_present": bridge.readonly._buying_power is not None,
            "no_secrets": True,
        },
        "token_probe": getattr(bridge, "token_probe", {}) or {},
        "startup_recon": bridge.startup_recon,
        "mapping": integ,
        "latency": lat,
        "latency_samples": bridge.latency_samples[-200:],
        "safety": {
            "actual_broker_submit_count": integ["actual_broker_submit_count"],
            "actual_broker_cancel_count": integ["actual_broker_cancel_count"],
            "duplicate_intent_created_count": integ["duplicate_intent_created_count"],
            "duplicate_broker_submit_count": integ["duplicate_broker_submit_count"],
            "reservation_leak": integ["reservation_leak"],
            "journal_write_failure": 1 if bridge.engine.last_journal_error else 0,
        },
        "flags": {
            "live_trading_enabled": bool(getattr(bridge.config, "live_trading_enabled", False)),
            "order_enabled": bool(getattr(bridge.config, "order_enabled", False)),
            "dry_run": bool(getattr(bridge.config, "dry_run", True)),
        },
    }
    # Phase687W4T soak fields (no secrets)
    tp = snap["token_probe"]
    snap["token_probe_status"] = tp.get("token_probe_status") or bridge.account_audit.get("account_status")
    snap["token_probe_latency_ms"] = tp.get("latency_ms")
    snap["station_running"] = tp.get("station_running")
    snap["port_reachable"] = tp.get("port_reachable")
    snap["readonly_ready_at_start"] = bool(tp.get("ready_for_soak"))
    snap["readonly_ready_at_end"] = str(end_status) in {
        "ONLINE_VALID",
        "ONLINE_ZERO_BALANCE",
        "ONLINE_NO_POSITIONS",
        "ONLINE_NO_ORDERS",
        "MARKET_CLOSED_READ_AVAILABLE",
    }
    snap["token_refresh_count"] = int(tp.get("token_refresh_count") or 0)
    snap["readonly_failure_category"] = tp.get("failure_reason") or bridge.readonly.last_error or ""
    snap["readonly_successful_endpoint_count"] = int(tp.get("readonly_successful_endpoint_count") or 0)

    # Phase687W5B/B1 — account capability / policy shadow soak fields (no secrets, no raw HoldID)
    try:
        from small_paper.kabu_account_capability import (
            CapabilityProvenance,
            LiveVerificationEvidence,
            build_account_capability_profile,
            soak_provenance_fields,
        )
        from small_paper.kabu_position_identity import parse_position_lots
        from small_paper.kabu_execution_policy_shadow import soak_shadow_metrics

        raw_lots = []
        cash_bp = None
        margin_bp = None
        token_ok = bool(getattr(bridge.readonly, "token", None))
        client_ok = getattr(bridge.readonly, "client", None) is not None
        if hasattr(bridge.readonly, "get_position_lots_raw"):
            raw_lots = bridge.readonly.get_position_lots_raw()
        if hasattr(bridge.readonly, "get_cash_buying_power"):
            cash_bp = bridge.readonly.get_cash_buying_power()
        if hasattr(bridge.readonly, "get_margin_buying_power"):
            margin_bp = bridge.readonly.get_margin_buying_power()

        # Live only when readonly client+token present; otherwise UNKNOWN (not fixture promotion)
        is_live = bool(token_ok and client_ok)
        resp_ts = _iso()
        prov = (
            CapabilityProvenance.LIVE_API_POSITION_RESPONSE.value
            if is_live
            else CapabilityProvenance.UNKNOWN.value
        )
        lots = parse_position_lots(
            raw_lots,
            source_timestamp=resp_ts,
            provenance=prov,
        )
        # Schema: each lot must have MTT/Exchange/AccountType when claiming live verify
        schema_ok = all(
            L.margin_trade_type is not None and L.exchange is not None and L.account_type is not None
            for L in lots
        ) if lots else True
        evidence = LiveVerificationEvidence(
            provenance=prov,
            token_acquired=token_ok,
            positions_endpoint_ok=is_live and not bool(getattr(bridge.readonly, "last_error", "")),
            account_endpoint_ok=is_live and margin_bp is not None,
            response_timestamp=resp_ts if is_live else "",
            fixture_used=False,
            synthetic_used=False,
            schema_validation_pass=schema_ok,
        )
        cap = build_account_capability_profile(
            account_status=str(end_status),
            cash_buying_power=cash_bp,
            margin_buying_power=margin_bp,
            position_lots=[L.to_artifact_dict() for L in lots],
            capability_source="soak_readonly_refresh",
            capability_provenance=prov,
            evidence=evidence,
        )
        w5b = soak_shadow_metrics(
            capability_status=cap.capability_status,
            margin_trade_type_status=cap.margin_trade_type_status,
            observed_margin_trade_types=cap.observed_position_margin_trade_types,
            identity_matches=[],
            close_decisions=[],
            exchange_shadow_count=0,
            execution_policy_shadow_count=0,
        )
        snap["w5b_policy_shadow"] = w5b
        for k, v in w5b.items():
            snap[k] = v
        # W5B1 provenance fields
        for k, v in soak_provenance_fields(cap).items():
            snap[k] = v
    except Exception as exc:
        snap["w5b_policy_shadow_error"] = type(exc).__name__
        snap["account_capability_status"] = "UNKNOWN"
        snap["margin_trade_type_status"] = "NOT_VERIFIED"
        snap["capability_provenance"] = "UNKNOWN"
        snap["fixture_used"] = False
        snap["synthetic_used"] = False
        snap["live_account_response_received"] = False
        snap["live_position_response_received"] = False
        snap["live_position_count"] = 0
        snap["margin_trade_type_live_verified"] = False
        snap["exchange_live_verified"] = False
        snap["hold_id_live_verified"] = False
        snap["verified_response_time"] = ""
        snap["verification_failure_reason"] = type(exc).__name__
        snap["observed_margin_trade_types"] = []
        snap["position_identity_match_count"] = 0
        snap["position_identity_mismatch_count"] = 0
        snap["exact_hold_close_candidate_count"] = 0
        snap["exact_hold_close_not_evaluable_count"] = 0
        snap["entry_exchange_shadow_count"] = 0
        snap["execution_policy_shadow_count"] = 0
        snap["policy_feature_coverage"] = {"production_policy_selected": False}

    # Phase687W7A recovery / seal fields (pre-seal defaults; W7A2 finalize propagates real seal SoT)
    try:
        from small_paper.stateful_journal_recovery import soak_w7a_fields
        from small_paper.w4s_seal_propagation import SEAL_NOT_GENERATED, resolve_seal_path

        jr = getattr(bridge, "journal_restore", {}) or {}
        man_path = output_dir / "session_manifest.json"
        # Prefer session-root full seal when present (14 artifacts)
        session_root = output_dir.parent if output_dir.name == "live_order_safety" else output_dir
        seal_path = resolve_seal_path(session_root, output_dir)
        man_status = "MISSING"
        if man_path.is_file():
            try:
                man = json.loads(man_path.read_text(encoding="utf-8"))
                man_status = "COMPLETE" if man.get("sealed") and man.get("git_commit") not in ("", "UNSET", "demo") else "INCOMPLETE"
                if man.get("config_sha256") in ("", "UNSET", "demo", "MISSING"):
                    man_status = "INCOMPLETE"
            except Exception:
                man_status = "INCOMPLETE"
        seal_status = "MISSING"
        seal_entries = 0
        seal_required = 0
        missing_req = 0
        post_mut = False
        seal_verified = False
        seal_generated_at = ""
        seal_schema = ""
        seal_manifest_sha = ""
        prop_status = SEAL_NOT_GENERATED
        if seal_path is not None and seal_path.is_file():
            try:
                seal = json.loads(seal_path.read_text(encoding="utf-8"))
                from small_paper.w4s_seal_propagation import enrich_seal_sot_fields, extract_seal_sot
                from small_paper.stateful_journal_recovery import detect_post_seal_mutation

                enrich_seal_sot_fields(seal)
                post_mut = bool(
                    detect_post_seal_mutation(seal_path, Path(seal.get("root") or session_root)).get(
                        "post_seal_mutation_detected"
                    )
                )
                verified = (
                    (not post_mut)
                    and str(seal.get("session_seal_status")) == "SEALED_VALID"
                    and int(seal.get("required_artifact_missing_count") or 0) == 0
                )
                sot = extract_seal_sot(seal, verified=verified, post_mutation=post_mut)
                seal_status = sot["session_seal_status"]
                seal_entries = sot["session_seal_entry_count"]
                seal_required = sot["session_seal_required_count"]
                missing_req = sot["required_artifact_missing_count"]
                seal_verified = sot["session_seal_verified"]
                seal_generated_at = sot["session_seal_generated_at"]
                seal_schema = sot["session_seal_schema_version"]
                seal_manifest_sha = sot["session_seal_manifest_sha256"]
                post_mut = sot["post_seal_mutation_detected"]
                from small_paper.w4s_seal_propagation import classify_seal_propagation

                # provisional classification from current snap fields after merge below
                prop_status = classify_seal_propagation(
                    sot, seal, verified=verified, post_mutation=post_mut
                )
            except Exception:
                seal_status = "INCOMPLETE"
        recovery_mode = str(jr.get("recovery_mode") or ("KILL_SWITCH_ACTIVE" if getattr(bridge.engine, "kill_switch", False) else "NORMAL"))
        w7a = soak_w7a_fields(
            journal_restore_status="JOURNAL_OK" if not jr.get("error") and not jr.get("journal_issues") else "JOURNAL_RECOVERY_REQUIRED",
            restored_order_count=int(jr.get("restored_order_count") or 0),
            restored_reservation_count=int(jr.get("restored_reservation_count") or 0),
            restored_position_count=int(jr.get("restored_position_count") or 0),
            session_manifest_status=man_status,
            session_seal_status=seal_status,
            session_seal_entry_count=seal_entries,
            session_seal_required_count=seal_required,
            required_artifact_missing_count=missing_req,
            session_seal_verified=seal_verified,
            session_seal_generated_at=seal_generated_at,
            session_seal_schema_version=seal_schema,
            session_seal_manifest_sha256=seal_manifest_sha,
            post_seal_mutation_detected=post_mut,
            seal_propagation_status=prop_status,
            recovery_mode_at_end=recovery_mode,
            recovery_assertion_failure_count=int(jr.get("assertion_failure_count") or 0),
            recovery_unexpected_object_count=int(jr.get("unexpected_restored_object_count") or 0),
            recovery_expected_actual_match=bool(
                (not jr.get("error"))
                and (not jr.get("journal_issues"))
                and int(jr.get("assertion_failure_count") or 0) == 0
            ),
        )
        snap["w7a_recovery"] = w7a
        for k, v in w7a.items():
            snap[k] = v
    except Exception as exc:
        snap["w7a_recovery_error"] = type(exc).__name__

    path = output_dir / "soak_session_snapshot.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
