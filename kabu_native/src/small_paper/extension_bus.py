"""
Phase616: ExtensionBus — optional Extension hooks (never changes Core gate decisions).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.core_runtime_mode import (
    CoreRuntimeMode,
    audit_enabled_for_mode,
    extension_bus_enabled,
    full_extension_active,
)


@dataclass
class ExtensionBus:
    """Extension layer hooks; absent under CORE_ONLY."""

    mode: CoreRuntimeMode
    config: Any
    state: Any
    writer: Any
    output_dir: Optional[Path] = None
    latency_trace: Any = None
    stage_profiler: Any = None

    @classmethod
    def maybe_create(
        cls,
        *,
        mode: CoreRuntimeMode,
        config: Any,
        state: Any,
        writer: Any,
        output_dir: Optional[Path] = None,
        stage_profiler: Any = None,
    ) -> Optional["ExtensionBus"]:
        if not extension_bus_enabled(mode):
            return None
        bus = cls(
            mode=mode,
            config=config,
            state=state,
            writer=writer,
            output_dir=output_dir,
            stage_profiler=stage_profiler,
        )
        bus._init_latency_trace()
        return bus

    def _record_extension(self, name: str, duration_ms: float) -> None:
        prof = self.stage_profiler
        if prof is not None:
            prof.record_extension(name, duration_ms)

    def _init_latency_trace(self) -> None:
        if not full_extension_active(self.mode):
            return
        from small_paper.entry_latency_trace import EntryLatencyTraceSession, entry_latency_trace_enabled

        if not entry_latency_trace_enabled(self.config) or self.output_dir is None:
            return
        self.latency_trace = EntryLatencyTraceSession(
            self.output_dir,
            max_price_age_sec=float(getattr(self.config, "entry_max_price_age_sec", 3.0) or 3.0),
        )

    def on_push_tick(
        self,
        *,
        symbol: str,
        payload: Mapping[str, Any],
        price_ring: list,
        t0_push_received_at: Optional[str] = None,
        t0_mono: Optional[float] = None,
    ) -> None:
        import time

        t0 = time.monotonic()
        if self.latency_trace is not None:
            t_trace = time.monotonic()
            self.latency_trace.begin_push(
                symbol=symbol,
                payload=payload,
                t0_push_received_at=t0_push_received_at,
                t0_mono=t0_mono,
            )
            self.latency_trace.mark_scan_enqueue()
            self._record_extension("Trace", (time.monotonic() - t_trace) * 1000.0)
        if not full_extension_active(self.mode):
            self._record_extension("ExtensionPushTick", (time.monotonic() - t0) * 1000.0)
            return
        t_shadow = time.monotonic()
        from small_paper.shadow_registry import is_shadow_runtime_enabled

        board_shadow = getattr(self.state, "realtime_board_exit_shadow", None)
        if board_shadow is not None and is_shadow_runtime_enabled("realtime_board_exit_shadow"):
            board_shadow.record_push_board_tick(symbol=symbol, payload=payload)
        from small_paper.extended_entry_shadow import tick_ts_from_payload

        try:
            px_tick = float(payload.get("CurrentPrice") or 0)
        except (TypeError, ValueError):
            px_tick = 0.0
        if px_tick > 0 and is_shadow_runtime_enabled("classic_momentum_forward_shadow"):
            cm_shadow = getattr(self.state, "classic_momentum_forward_shadow", None)
            if cm_shadow is not None:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                cm_shadow.on_price_tick(
                    symbol=symbol,
                    price_ring=price_ring,
                    ts=tick_ts_from_payload(payload),
                    px=px_tick,
                    day=datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d"),
                )
        # E1_X5 independent shadow (Paper default ON; Live forced OFF; E1_X5_FORWARD_SHADOW=0 to disable)
        e1x5 = getattr(self.state, "e1_x5_forward_shadow", None)
        if e1x5 is not None and getattr(e1x5, "enabled", False):
            try:
                from small_paper.canonical_board import best_bid_ask_for_mode
                from datetime import datetime
                from zoneinfo import ZoneInfo

                bid, ask = best_bid_ask_for_mode(payload, mode="canonical")
                ts = tick_ts_from_payload(payload)
                if ts is None:
                    ts = datetime.now(ZoneInfo("Asia/Tokyo"))
                e1x5.on_quote(
                    symbol=symbol, ts=ts, bid=bid, ask=ask,
                    day=ts.strftime("%Y%m%d") if hasattr(ts, "strftime") else "",
                )
            except Exception:
                pass
        self._record_extension("Shadow", (time.monotonic() - t_shadow) * 1000.0)
        self._record_extension("ExtensionPushTick", (time.monotonic() - t0) * 1000.0)

    def mark_payload_parsed(self) -> None:
        if self.latency_trace is not None:
            self.latency_trace.mark_payload_parsed()

    def mark_freshness_check(self) -> None:
        if self.latency_trace is not None:
            self.latency_trace.mark_freshness_check()

    def mark_pbv2_start(self) -> None:
        if self.latency_trace is not None:
            self.latency_trace.mark_pbv2_start()

    def mark_pbv2_end(self) -> None:
        if self.latency_trace is not None:
            self.latency_trace.mark_pbv2_end()

    def finish_latency_trace(
        self,
        *,
        stale_reason: Optional[str],
        gate_reason: str,
        entry_score_v2: Any = None,
    ) -> None:
        if self.latency_trace is not None:
            self.latency_trace.finish(
                stale_reason=stale_reason,
                gate_reason=gate_reason,
                entry_score_v2=entry_score_v2,
            )

    def on_post_eval(
        self,
        ctx: Any,
        *,
        sym: str,
        trade: Mapping[str, Any],
        decision: Any,
        timestamp: str,
    ) -> None:
        import time

        if not full_extension_active(self.mode):
            return
        t0 = time.monotonic()
        from small_paper.volume_gate_relaxation_shadow import (
            record_volume_gate_shadow_eval,
            shadow_enabled,
        )

        if not shadow_enabled(self.config):
            return
        suit = getattr(ctx.gate, "daytrade_suitability", None)
        if suit is None:
            return
        chk = suit.check(trade)
        row = record_volume_gate_shadow_eval(
            ctx.state.volume_gate_shadow,
            trade=trade,
            threshold=chk.threshold,
            symbol=sym,
            timestamp=timestamp,
            reject_reason="" if decision.accept else str(decision.reason or ""),
        )
        if row is not None:
            ctx.writer.append_volume_shadow_eval(row)
        self._record_extension("VolumeShadow", (time.monotonic() - t0) * 1000.0)

    def should_record_entry_shadows(self) -> bool:
        return full_extension_active(self.mode)

    def should_enrich_accept_audit(self) -> bool:
        return audit_enabled_for_mode(self.mode)

    def on_session_end(self, state: Any, summary: dict[str, Any], *, config: Any, output_dir: Optional[Path]) -> dict[str, Any]:
        if not full_extension_active(self.mode):
            return summary
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from small_paper.board_imbalance_shadow import finalize_session_board_imbalance_shadow
        from small_paper.entry_expectancy_score_shadow import finalize_session_entry_expectancy_score
        from small_paper.quality_formula_shadow import finalize_session_quality_shadow
        from small_paper.trading_value_shadow_gate import finalize_session_trading_value_shadow

        out = dict(summary)
        extension_errors: list[str] = summary.get("extension_errors") or []
        if not isinstance(extension_errors, list):
            extension_errors = []

        def _run_step(step: str, fn) -> None:
            try:
                result = fn()
            except Exception as exc:
                extension_errors.append(f"{step}: {exc}")
                return
            if isinstance(result, dict):
                out.update(result)

        # Exit shadow monitor is finalized in _build_live_summary / _build_push_replay_summary.
        from small_paper.shadow_registry import is_shadow_runtime_enabled

        if is_shadow_runtime_enabled("quality_formula_shadow"):
            _run_step(
                "quality_shadow",
                lambda: finalize_session_quality_shadow(state.accepted_rows, state.events),
            )
        if is_shadow_runtime_enabled("trading_value_shadow_gate"):
            _run_step(
                "trading_value_shadow",
                lambda: finalize_session_trading_value_shadow(state.accepted_rows, state.events),
            )
        if is_shadow_runtime_enabled("board_imbalance_shadow"):
            _run_step(
                "board_imbalance_shadow",
                lambda: finalize_session_board_imbalance_shadow(state.accepted_rows, state.events),
            )
        # Mainline component finalize retained (not a Forward Shadow count)
        _run_step(
            "entry_expectancy_score_shadow",
            lambda: finalize_session_entry_expectancy_score(state.accepted_rows, state.events),
        )
        if output_dir is not None:
            from small_paper.ihc_shadow_counterfactual import finalize_session_ihc_shadow_summary

            _run_step(
                "ihc_shadow_counterfactual",
                lambda: finalize_session_ihc_shadow_summary(
                    state.accepted_rows,
                    state.events,
                    session_dir=output_dir,
                    config=config,
                ),
            )

        jst = ZoneInfo("Asia/Tokyo")
        day = datetime.now(jst).strftime("%Y%m%d")
        pe = getattr(state, "post_entry_forward_shadow", None)
        if (
            pe is not None
            and output_dir is not None
            and is_shadow_runtime_enabled("post_entry_forward_shadow")
        ):
            def _finalize_post_entry() -> dict[str, Any]:
                pe.finalize_session_end(ts=datetime.now(jst).timestamp(), day=day)
                pe.write_session_csv(output_dir)
                return pe.summary_fields()

            _run_step("post_entry_forward_shadow", _finalize_post_entry)
        cm = getattr(state, "classic_momentum_forward_shadow", None)
        if (
            cm is not None
            and output_dir is not None
            and is_shadow_runtime_enabled("classic_momentum_forward_shadow")
        ):
            def _finalize_classic_momentum() -> dict[str, Any]:
                cm.finalize_session_end(ts=datetime.now(jst).timestamp(), day=day)
                cm.write_session_csv(output_dir)
                return cm.summary_fields()

            _run_step("classic_momentum_forward_shadow", _finalize_classic_momentum)

        if extension_errors:
            out["extension_errors"] = extension_errors
        return out
