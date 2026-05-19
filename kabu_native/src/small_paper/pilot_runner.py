"""
Phase 44: Small paper pilot dry-run runner (no order placement).
"""

from __future__ import annotations

import csv
import json
import signal
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.exposure_gate import ExposureGate, ExposureGateConfig, run_exposure_gate_simulation
from research.research_exit_criteria import _load_csv
from small_paper.config import SmallPaperPilotConfig
from small_paper.discord_notifier import (
    SmallPaperDiscordNotifier,
    build_session_summary_extras,
    discord_notifier_from_pilot,
    observer_tracker_config_from_pilot,
)
from small_paper.observer_position_tracker import (
    OBSERVER_EXIT,
    OBSERVER_HOLD,
    OBSERVER_TAKE,
    ObserverJudgmentEvent,
    ObserverPositionTracker,
)
from small_paper.live_writer import LiveSessionWriter
from small_paper.session_schedule import (
    SessionSchedule,
    empty_bucket_summary,
    session_bucket,
    wait_until,
)

JST = ZoneInfo("Asia/Tokyo")

EVENT_FIELDS = (
    "event_time",
    "event_type",
    "symbol",
    "profile",
    "entry_time",
    "exit_time",
    "continuation_quality_score",
    "quality_tier",
    "gate_accept",
    "gate_reject_reason",
    "pnl_pct",
    "exit_reason",
    "dry_run",
    "source",
    "message_index",
    "current_price",
    "quality_fallback_path",
    "live_feature_complete",
    "rolling_mfe_pct",
    "rolling_mae_pct",
    "momentum_continuation_score",
    "favorable_continuation",
    "max_continuation_duration",
    "adverse_shrinking",
    "quality_components_json",
)


@dataclass
class PilotRunResult:
    output_dir: Path
    summary: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejects: list[dict[str, Any]] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def run_replay_dry_run(
    config: SmallPaperPilotConfig,
    *,
    trades_csv: Path,
    output_dir: Path,
) -> PilotRunResult:
    """Replay candidate trades through exposure gate; no orders."""
    trades = _load_csv(trades_csv)
    gate = config.make_exposure_gate()
    gate_cfg = config.exposure_gate_config()
    accepted, rejects = run_exposure_gate_simulation(
        trades, gate_cfg, allowed_windows=config.allowed_windows()
    )

    events: list[dict[str, Any]] = []
    for t in trades:
        if str(t.get("profile")) != config.profile:
            continue
        events.append(
            {
                "event_time": _now_iso(),
                "event_type": "candidate",
                "symbol": t.get("symbol"),
                "profile": t.get("profile"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "continuation_quality_score": t.get("continuation_quality_score"),
                "quality_tier": t.get("quality_tier"),
                "gate_accept": None,
                "gate_reject_reason": "",
                "pnl_pct": t.get("pnl_pct"),
                "exit_reason": t.get("exit_reason"),
                "dry_run": True,
                "source": "replay",
            }
        )

    for row in accepted:
        events.append(
            {
                "event_time": _now_iso(),
                "event_type": "accepted",
                "symbol": row.get("symbol"),
                "profile": row.get("profile"),
                "entry_time": row.get("entry_time"),
                "exit_time": row.get("exit_time"),
                "continuation_quality_score": row.get("continuation_quality_score"),
                "quality_tier": row.get("quality_tier"),
                "gate_accept": True,
                "gate_reject_reason": "",
                "pnl_pct": row.get("pnl_pct"),
                "exit_reason": row.get("exit_reason"),
                "dry_run": True,
                "source": "replay",
            }
        )
    for row in rejects:
        events.append(
            {
                "event_time": _now_iso(),
                "event_type": "rejected",
                "symbol": row.get("symbol"),
                "profile": row.get("profile"),
                "entry_time": row.get("entry_time"),
                "exit_time": row.get("exit_time"),
                "continuation_quality_score": row.get("continuation_quality_score"),
                "quality_tier": row.get("quality_tier"),
                "gate_accept": False,
                "gate_reject_reason": row.get("gate_reject_reason"),
                "pnl_pct": row.get("pnl_pct"),
                "exit_reason": row.get("exit_reason"),
                "dry_run": True,
                "source": "replay",
            }
        )

    positions = _build_positions_snapshot(accepted, gate)
    summary = {
        "phase": 44,
        "mode": "small_paper_pilot_dry_run",
        "generated_at": _now_iso(),
        "order_enabled": False,
        "paper_only": True,
        "profile": config.profile,
        "entry_profile": config.entry_profile,
        "source": "replay",
        "trades_csv": str(trades_csv),
        "candidate_count": len([e for e in events if e["event_type"] == "candidate"]),
        "accepted_count": len(accepted),
        "rejected_count": len(rejects),
        "reject_reason_counts": _count_reasons(rejects),
        "config": {
            "min_continuation_quality": config.min_continuation_quality,
            "max_concurrent_positions": config.max_concurrent_positions,
        },
    }

    _write_outputs(output_dir, events=events, accepted=accepted, rejects=rejects, positions=positions, summary=summary)
    return PilotRunResult(output_dir=output_dir, summary=summary, events=events, accepted=accepted, rejects=rejects)


def _symbol_from_push(payload: Mapping[str, Any], code_to_symbol: Mapping[str, str]) -> str:
    code = str(payload.get("Symbol") or "").strip()
    if code in code_to_symbol:
        return code_to_symbol[code]
    if code.endswith(".T"):
        return code.upper()
    return f"{code}.T" if code else ""


def _candidate_trade_from_push(
    payload: Mapping[str, Any],
    *,
    symbol: str,
    profile: str,
    virtual_hold_sec: float = 300.0,
    feature_snapshot: Optional[Any] = None,
) -> dict[str, Any]:
    """Map PUSH/board payload to gate input (virtual hold for concurrent cap only)."""
    from research.continuation_quality_ranking import continuation_quality_score
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from storage.intraday_recorder import parse_kabu_time

    now = datetime.now(JST)
    ent = parse_kabu_time(payload.get("CurrentPriceTime"), fallback=now)
    ex_ts = ent.timestamp() + virtual_hold_sec
    ex = datetime.fromtimestamp(ex_ts, tz=JST)
    mfe = payload.get("max_favorable_excursion_pct")
    if mfe is None:
        mfe = payload.get("rolling_mfe_pct")
    mae = payload.get("max_adverse_excursion_pct")
    if mae is None:
        mae = payload.get("rolling_mae_pct")
    trade = {
        "profile": profile,
        "symbol": symbol,
        "entry_time": ent.isoformat(),
        "exit_time": ex.isoformat(),
        "trade_date": ent.date().isoformat(),
        "pnl_pct": 0.0,
        "exit_reason": "live_virtual_hold",
        "momentum_continuation_score": payload.get("momentum_continuation_score"),
        "bullish_continuation_score": payload.get("bullish_continuation_score")
        or payload.get("bullish_weighted_score"),
        "favorable_continuation": payload.get("favorable_continuation")
        or payload.get("favorable_weighted"),
        "bearish_accumulation_score": payload.get("bearish_accumulation_score")
        or payload.get("bearish_weighted_score"),
        "max_favorable_excursion_pct": mfe,
        "max_adverse_excursion_pct": mae,
        "max_continuation_duration": payload.get("max_continuation_duration"),
    }
    q = continuation_quality_score(trade)
    trade["continuation_quality_score"] = round(q, 4)
    if feature_snapshot is not None:
        trade.update(LiveFeatureBridge.trade_quality_extras(trade, feature_snapshot))
    return trade


def _event_from_gate(
    *,
    event_type: str,
    trade: Mapping[str, Any],
    decision: Any,
    source: str,
    message_index: int,
    current_price: Any = None,
) -> dict[str, Any]:
    base = {
        "event_time": _now_iso(),
        "event_type": event_type,
        "symbol": trade.get("symbol"),
        "profile": trade.get("profile"),
        "entry_time": trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),
        "continuation_quality_score": trade.get("continuation_quality_score"),
        "quality_tier": getattr(decision, "quality_tier", ""),
        "gate_accept": decision.accept if event_type != "candidate" else None,
        "gate_reject_reason": "" if decision.accept else getattr(decision, "reason", ""),
        "pnl_pct": trade.get("pnl_pct"),
        "exit_reason": trade.get("exit_reason"),
        "dry_run": True,
        "source": source,
        "message_index": message_index,
        "current_price": current_price,
    }
    for key in EVENT_FIELDS:
        if key in (
            "event_time",
            "event_type",
            "gate_accept",
            "gate_reject_reason",
            "quality_tier",
            "current_price",
        ):
            continue
        if key in trade:
            base[key] = trade.get(key)
    return base


def verify_kabu_connection(repo_root: Path, *, symbol_key: str = "9984@1") -> dict[str, Any]:
    """Token + board probe; no orders."""
    import os

    from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, load_kabu_env

    if not os.environ.get("KABU_API_PASSWORD", "").strip():
        raise KabuNativeApiError("KABU_API_PASSWORD is not set")
    load_kabu_env(repo_root=repo_root)
    client = KabuNativeRestClient(default_base_url())
    token = client.issue_token_from_env()
    board = client.get_board(symbol_key, token=token)
    return {
        "ok": True,
        "symbol_key": symbol_key,
        "current_price": board.get("CurrentPrice"),
        "current_price_time": board.get("CurrentPriceTime"),
    }


@dataclass
class _LiveRunState:
    started_mono: float
    events: list[dict[str, Any]] = field(default_factory=list)
    accepted_rows: list[dict[str, Any]] = field(default_factory=list)
    reject_rows: list[dict[str, Any]] = field(default_factory=list)
    push_messages: int = 0
    gate_evaluations: int = 0
    heartbeat_count: int = 0
    api_error_count: int = 0
    consecutive_api_errors: int = 0
    reconnect_count: int = 0
    stale_tick_count: int = 0
    data_gap_count: int = 0
    peak_open_slots: int = 0
    quality_scores: list[float] = field(default_factory=list)
    quality_fallback_count: int = 0
    live_feature_complete_count: int = 0
    bucket_summary: dict[str, dict[str, int]] = field(default_factory=empty_bucket_summary)
    stop_reason: str = ""
    stop_requested: bool = False


def _quality_distribution(scores: Sequence[float]) -> dict[str, int]:
    dist = {"lt_0.55": 0, "0.55_0.65": 0, "0.65_0.75": 0, "ge_0.75": 0}
    for q in scores:
        if q < 0.55:
            dist["lt_0.55"] += 1
        elif q < 0.65:
            dist["0.55_0.65"] += 1
        elif q < 0.75:
            dist["0.65_0.75"] += 1
        else:
            dist["ge_0.75"] += 1
    return dist


def _dispatch_observer_events(
    events: Sequence[ObserverJudgmentEvent],
    *,
    discord: Optional[SmallPaperDiscordNotifier],
) -> None:
    if not discord or not discord.active:
        return
    for ev in events:
        try:
            if ev.kind == OBSERVER_HOLD:
                discord.notify_hold(context=ev.context)
            elif ev.kind == OBSERVER_TAKE:
                discord.notify_take(context=ev.context)
            elif ev.kind == OBSERVER_EXIT:
                if not ev.context.get("is_structural_exit"):
                    continue
                discord.notify_exit(context=ev.context)
        except Exception:
            pass


def _record_bucket(state: _LiveRunState, event_type: str) -> None:
    bucket = session_bucket()
    if bucket in state.bucket_summary and event_type in state.bucket_summary[bucket]:
        state.bucket_summary[bucket][event_type] += 1


def _tick_age_sec(payload: Mapping[str, Any]) -> Optional[float]:
    from storage.intraday_recorder import parse_kabu_time

    raw = payload.get("CurrentPriceTime")
    if raw is None or raw == "":
        return None
    tick = parse_kabu_time(raw, fallback=datetime.now(JST))
    return max(0.0, (datetime.now(JST) - tick).total_seconds())


@dataclass
class _PushPipelineContext:
    config: SmallPaperPilotConfig
    gate: ExposureGate
    feature_bridge: Any
    state: _LiveRunState
    writer: LiveSessionWriter
    code_to_symbol: dict[str, str]
    source: str
    pos_fields: Sequence[str]
    observer: Optional[ObserverPositionTracker] = None
    discord: Optional[SmallPaperDiscordNotifier] = None
    stale_tick_sec: float = 120.0
    gap_threshold_sec: float = 15.0
    last_symbol_tick: dict[str, float] = field(default_factory=dict)


def _process_push_payload(
    ctx: _PushPipelineContext,
    payload: Mapping[str, Any],
    msg_i: int,
    *,
    symbol: Optional[str] = None,
) -> None:
    sym = symbol or _symbol_from_push(payload, ctx.code_to_symbol)
    if not sym:
        return
    ctx.state.push_messages = msg_i
    age = _tick_age_sec(payload)
    if age is not None and age > ctx.stale_tick_sec:
        ctx.state.stale_tick_count += 1
    import time

    now_m = time.monotonic()
    prev = ctx.last_symbol_tick.get(sym)
    if prev is not None and (now_m - prev) > ctx.gap_threshold_sec:
        ctx.state.data_gap_count += 1
    ctx.last_symbol_tick[sym] = now_m

    snapshot = ctx.feature_bridge.update(sym, payload)
    enriched = ctx.feature_bridge.enrich_payload(payload, snapshot)
    trade = _candidate_trade_from_push(
        enriched,
        symbol=sym,
        profile=ctx.config.profile,
        feature_snapshot=snapshot,
    )
    if snapshot.quality_fallback_path:
        ctx.state.quality_fallback_count += 1
    if snapshot.live_feature_complete:
        ctx.state.live_feature_complete_count += 1
    q = trade.get("continuation_quality_score")
    if isinstance(q, (int, float)):
        ctx.state.quality_scores.append(float(q))
    bucket = session_bucket()
    if ctx.observer and ctx.observer.has_open(sym):
        price = payload.get("CurrentPrice")
        obs_events = ctx.observer.on_tick(
            symbol=sym,
            trade=trade,
            payload=enriched,
            current_price=float(price) if price is not None else None,
            session_bucket=bucket,
        )
        _dispatch_observer_events(obs_events, discord=ctx.discord)
    decision = ctx.gate.evaluate_entry(trade)
    ctx.state.gate_evaluations += 1

    cand = _event_from_gate(
        event_type="candidate",
        trade=trade,
        decision=decision,
        source=ctx.source,
        message_index=msg_i,
        current_price=payload.get("CurrentPrice"),
    )
    ctx.state.events.append(cand)
    ctx.writer.append_event(cand)
    _record_bucket(ctx.state, "candidate")

    if decision.accept:
        ctx.gate.record_accepted(trade)
        ctx.state.peak_open_slots = max(ctx.state.peak_open_slots, len(ctx.gate.state.open_slots))
        acc = _event_from_gate(
            event_type="accepted",
            trade=trade,
            decision=decision,
            source=ctx.source,
            message_index=msg_i,
            current_price=payload.get("CurrentPrice"),
        )
        ctx.state.events.append(acc)
        ctx.writer.append_event(acc)
        ctx.state.accepted_rows.append(dict(trade))
        _record_bucket(ctx.state, "accepted")
        ctx.writer.append_position_row(
            {
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "open_slots_after": len(ctx.gate.state.open_slots),
            },
            fields=ctx.pos_fields,
        )
        if ctx.observer:
            try:
                entry_px = float(payload.get("CurrentPrice") or 0)
            except (TypeError, ValueError):
                entry_px = 0.0
            if entry_px > 0 and ctx.observer.has_open(sym):
                overlap_events = ctx.observer.close_for_overlap(
                    symbol=sym,
                    trade=trade,
                    payload=enriched,
                    current_price=entry_px,
                    session_bucket=bucket,
                )
                _dispatch_observer_events(overlap_events, discord=ctx.discord)
            if entry_px > 0 and not ctx.observer.has_open(sym):
                ctx.observer.register_entry(
                    trade=trade,
                    payload=enriched,
                    quality_tier=str(decision.quality_tier or ""),
                    entry_price=entry_px,
                )
        if ctx.discord and ctx.discord.active:
            ctx.discord.notify_entry(
                event=acc,
                payload=enriched,
                open_slots=len(ctx.gate.state.open_slots),
                session_bucket=bucket,
            )
    else:
        rej_row = dict(trade)
        rej_row["gate_reject_reason"] = decision.reason
        ctx.state.reject_rows.append(rej_row)
        rej = _event_from_gate(
            event_type="rejected",
            trade=trade,
            decision=decision,
            source=ctx.source,
            message_index=msg_i,
            current_price=payload.get("CurrentPrice"),
        )
        ctx.state.events.append(rej)
        ctx.writer.append_event(rej)
        _record_bucket(ctx.state, "rejected")
        if ctx.discord and ctx.discord.active:
            ctx.discord.notify_rejected(
                event=rej,
                payload=enriched,
                open_slots=len(ctx.gate.state.open_slots),
                session_bucket=session_bucket(),
            )


def _quality_ge_0_55_count(scores: Sequence[float]) -> int:
    return sum(1 for q in scores if q >= 0.55)


def _policy_summary_extras(config: SmallPaperPilotConfig) -> dict[str, Any]:
    out = dict(config.policy_summary_fields())
    if config.policy_trial and config.policy_label in (
        "q070_cap3_trial",
        "q070_cap3_mfe_fav_trial",
    ):
        from small_paper.live_observer_readiness import (
            DEFAULT_PHASE60_STRUCTURAL_SESSION_REL,
            DEFAULT_PHASE54_SESSION_REL,
            live_observer_retrial_summary_fields,
        )

        repo_root = Path(__file__).resolve().parents[3]
        ref = repo_root / DEFAULT_PHASE54_SESSION_REL
        struct_ref = repo_root / DEFAULT_PHASE60_STRUCTURAL_SESSION_REL
        out.update(
            live_observer_retrial_summary_fields(
                config,
                reference_session_dir=ref,
                structural_session_dir=struct_ref,
            )
        )
    return out


def _write_quality_top_debug(output_dir: Path, events: Sequence[Mapping[str, Any]], *, top_n: int = 100) -> None:
    candidates = [e for e in events if e.get("event_type") == "candidate"]
    top = sorted(
        candidates,
        key=lambda e: float(e.get("continuation_quality_score") or 0),
        reverse=True,
    )[:top_n]
    if not top:
        return
    out_json = output_dir / "quality_top_debug.json"
    out_json.write_text(json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output_dir / "quality_top_debug.csv", list(top[0].keys()), top)


def _parse_recorded_at_ts(recorded_at: str) -> float:
    try:
        return datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _load_push_replay_records(
    push_dir: Path,
    *,
    max_rows: Optional[int] = None,
) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for fp in sorted(push_dir.glob("*.jsonl")):
        file_sym = fp.stem
        with fp.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                src = str(rec.get("source") or "")
                if src and src not in ("live_push", "push", "dry_run"):
                    continue
                payload = rec.get("payload")
                if not isinstance(payload, dict):
                    continue
                sym = str(rec.get("symbol") or file_sym).strip().upper()
                if not sym.endswith(".T"):
                    sym = f"{sym}.T"
                recorded_at = str(rec.get("recorded_at") or "")
                rows.append((recorded_at, sym, payload))
                if max_rows is not None and len(rows) >= max_rows:
                    return sorted(rows, key=lambda r: r[0])
    return sorted(rows, key=lambda r: r[0])


_C_GROUP_FAVORABLE_FADE_KEYS = frozenset(
    {
        ("7013.T", "2026-05-19T10:11:57+09:00"),
        ("9412.T", "2026-05-19T10:37:03+09:00"),
        ("9434.T", "2026-05-19T10:59:28+09:00"),
        ("8306.T", "2026-05-19T12:54:03+09:00"),
        ("7974.T", "2026-05-19T13:15:04+09:00"),
        ("8058.T", "2026-05-19T14:55:45+09:00"),
    }
)


def _structural_metrics_for_push_replay(
    events: Sequence[Mapping[str, Any]],
    *,
    config: SmallPaperPilotConfig,
    poll_interval_sec: float,
) -> dict[str, Any]:
    from research.structural_observer_review import (
        _session_end_time,
        _summarize_structural_trades,
        replay_combined_structural_exit_v1,
    )

    if not config.structural_exit_policy:
        return {}
    session_end = _session_end_time(events)
    trades, _ = replay_combined_structural_exit_v1(
        events,
        pilot_config=config,
        poll_interval_sec=poll_interval_sec,
        session_end=session_end,
    )
    met = _summarize_structural_trades(trades)
    accepted_keys = {
        (str(e.get("symbol") or ""), str(e.get("entry_time") or ""))
        for e in events
        if e.get("event_type") == "accepted"
    }
    exit_dist = met.get("exit_reason_distribution") or {}
    return {
        "structural_exit_policy": config.structural_exit_policy,
        "structural_pf": met.get("structural_pf"),
        "structural_avg_pnl_pct": met.get("structural_avg_pnl"),
        "structural_trade_count": met.get("structural_trade_count"),
        "favorable_fade_exit_count": int(exit_dist.get("favorable_fade_exit", 0)),
        "c_group_pass_count": sum(1 for k in _C_GROUP_FAVORABLE_FADE_KEYS if k in accepted_keys),
        "favorable_mode": config.favorable_mode,
        "favorable_mfe_scale": config.favorable_mfe_scale,
        "use_market_time_window": config.use_market_time_window,
    }


def _build_push_replay_summary(
    *,
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    gate: ExposureGate,
    push_dir: Path,
    push_rows: int,
    runtime_sec: float,
    poll_interval_sec: float,
    replay_speed_sec: float,
) -> dict[str, Any]:
    reject_events = [e for e in state.events if e.get("event_type") == "rejected"]
    ge55 = _quality_ge_0_55_count(state.quality_scores)
    base = {
        "phase": 51 if config.policy_trial else 48,
        "mode": "small_paper_pilot_push_replay_dry_run",
        "source": "push-replay",
        "live_feature_bridge": True,
        "push_dir": str(push_dir),
        "push_rows": push_rows,
        "push_messages": state.push_messages,
        "gate_evaluations": state.gate_evaluations,
        "generated_at": _now_iso(),
        "ended_at": _now_iso(),
        "runtime_sec": round(runtime_sec, 1),
        "poll_interval_sec": poll_interval_sec,
        "replay_speed_sec": replay_speed_sec,
        "order_enabled": False,
        "paper_only": True,
        "profile": config.profile,
        "candidate_count": len([e for e in state.events if e.get("event_type") == "candidate"]),
        "accepted_count": len(state.accepted_rows),
        "rejected_count": len(reject_events),
        "reject_reason_counts": _count_reasons(state.reject_rows),
        "max_concurrent_positions": config.max_concurrent_positions,
        "peak_open_slots": state.peak_open_slots,
        "quality_ge_0_55_count": ge55,
        "quality_ge_0_55_pct": round(100.0 * ge55 / max(1, state.gate_evaluations), 2),
        "quality_fallback_count": state.quality_fallback_count,
        "quality_fallback_rate_pct": round(
            100.0 * state.quality_fallback_count / max(1, state.gate_evaluations),
            2,
        ),
        "live_feature_complete_count": state.live_feature_complete_count,
        "live_feature_complete_rate_pct": round(
            100.0 * state.live_feature_complete_count / max(1, state.gate_evaluations),
            2,
        ),
        "quality_distribution": _quality_distribution(state.quality_scores),
        "open_slots_end": len(gate.state.open_slots),
        "discord_enabled": config.discord_enabled,
        "note": "Offline push_jsonl replay; same bridge+gate as live; no orders placed.",
    }
    base.update(_policy_summary_extras(config))
    return base


def run_push_replay_dry_run(
    config: SmallPaperPilotConfig,
    *,
    push_dir: Path,
    output_dir: Path,
    poll_interval_sec: float = 0.0,
    replay_speed_sec: float = 0.0,
    max_push_rows: Optional[int] = None,
    enable_discord: bool = False,
) -> PilotRunResult:
    """Replay saved push_jsonl through live feature bridge + exposure gate (no kabu connection)."""
    import time

    from small_paper.live_feature_bridge import LiveFeatureBridge

    if not push_dir.is_dir():
        raise FileNotFoundError(f"push_dir not found: {push_dir}")

    replay_config = config
    if not enable_discord:
        from dataclasses import replace

        replay_config = replace(config, discord_enabled=False)
    # else: respect config.discord_enabled (trial q070 keeps observer on for push-replay)

    records = _load_push_replay_records(push_dir, max_rows=max_push_rows)
    push_rows = len(records)

    code_to_symbol: dict[str, str] = {}
    for _, sym, _ in records:
        code = sym.replace(".T", "")
        code_to_symbol[code] = sym

    gate = replay_config.make_exposure_gate()
    gate_cfg = replay_config.exposure_gate_config()
    feature_bridge = LiveFeatureBridge(replay_config.feature_bridge_config())
    output_dir.mkdir(parents=True, exist_ok=True)
    writer = LiveSessionWriter(output_dir, incremental=True, event_fields=EVENT_FIELDS)
    state = _LiveRunState(started_mono=time.monotonic())
    pos_fields = ["symbol", "entry_time", "exit_time", "open_slots_after"]

    observer: Optional[ObserverPositionTracker] = None
    discord: Optional[SmallPaperDiscordNotifier] = None
    if replay_config.discord_enabled and replay_config.discord_observer_only:

        def _discord_error_logger(op: str, msg: str, extra: Mapping[str, Any]) -> None:
            writer.append_error(
                {
                    "event_time": _now_iso(),
                    "error_type": "discord_error",
                    "operation": op,
                    "message": msg,
                    **dict(extra),
                }
            )

        discord = discord_notifier_from_pilot(
            replay_config,
            error_logger=_discord_error_logger,
        )
        observer = ObserverPositionTracker(observer_tracker_config_from_pilot(replay_config))

    gap_threshold_sec = max(replay_config.live_stale_tick_sec * 2, max(poll_interval_sec, 0.001) * 3)
    ctx = _PushPipelineContext(
        config=replay_config,
        gate=gate,
        feature_bridge=feature_bridge,
        state=state,
        writer=writer,
        code_to_symbol=code_to_symbol,
        source="push-replay",
        pos_fields=pos_fields,
        observer=observer,
        discord=discord,
        stale_tick_sec=replay_config.live_stale_tick_sec,
        gap_threshold_sec=gap_threshold_sec,
    )

    last_eval_ts: dict[str, float] = {}
    msg_i = 0
    for recorded_at, sym, payload in records:
        msg_i += 1
        if poll_interval_sec > 0:
            ts = _parse_recorded_at_ts(recorded_at)
            prev = last_eval_ts.get(sym)
            if prev is not None and (ts - prev) < poll_interval_sec:
                continue
            last_eval_ts[sym] = ts
        _process_push_payload(ctx, payload, msg_i, symbol=sym)
        if replay_speed_sec > 0:
            time.sleep(replay_speed_sec)

    if observer:
        exit_events = observer.close_all(reason="push_replay_end")
        _dispatch_observer_events(exit_events, discord=discord)

    runtime_sec = time.monotonic() - state.started_mono
    positions = _build_positions_snapshot(state.accepted_rows, gate)
    summary = _build_push_replay_summary(
        config=replay_config,
        state=state,
        gate=gate,
        push_dir=push_dir,
        push_rows=push_rows,
        runtime_sec=runtime_sec,
        poll_interval_sec=poll_interval_sec,
        replay_speed_sec=replay_speed_sec,
    )
    summary.update(
        _structural_metrics_for_push_replay(
            state.events,
            config=replay_config,
            poll_interval_sec=poll_interval_sec,
        )
    )
    if discord and discord.active:
        summary.update(
            build_session_summary_extras(
                accepted_rows=state.accepted_rows,
                bucket_summary=state.bucket_summary,
                observer_stats=_observer_stats_dict(observer),
            )
        )
        discord.notify_session_summary(summary=summary)

    writer.finalize_batch(
        events=state.events,
        positions=positions,
        summary=summary,
        pos_fields=pos_fields,
    )
    _write_quality_top_debug(output_dir, state.events)
    return PilotRunResult(
        output_dir=output_dir,
        summary=summary,
        events=state.events,
        accepted=state.accepted_rows,
        rejects=state.reject_rows,
    )


def _build_live_summary(
    *,
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    session_cfg: Mapping[str, Any],
    gate: ExposureGate,
    full_session: bool,
    runtime_sec: float,
) -> dict[str, Any]:
    reject_events = [e for e in state.events if e.get("event_type") == "rejected"]
    is_retrial = config.policy_trial and config.policy_label == "q070_cap3_trial"
    summary = {
        "phase": 55 if is_retrial else (51 if config.policy_trial else 47),
        "mode": "small_paper_pilot_live_observer_retrial"
        if is_retrial and full_session
        else (
            "small_paper_pilot_live_full_dry_run"
            if full_session
            else "small_paper_pilot_live_dry_run"
        ),
        "live_feature_bridge": True,
        "quality_fallback_count": state.quality_fallback_count,
        "live_feature_complete_count": state.live_feature_complete_count,
        "quality_fallback_rate_pct": round(
            100.0 * state.quality_fallback_count / max(1, state.gate_evaluations),
            2,
        ),
        "live_feature_complete_rate_pct": round(
            100.0 * state.live_feature_complete_count / max(1, state.gate_evaluations),
            2,
        ),
        "generated_at": session_cfg.get("generated_at"),
        "ended_at": _now_iso(),
        "order_enabled": False,
        "paper_only": True,
        "profile": config.profile,
        "source": "live",
        "full_session": full_session,
        "runtime_sec": round(runtime_sec, 1),
        "heartbeat_count": state.heartbeat_count,
        "api_error_count": state.api_error_count,
        "reconnect_count": state.reconnect_count,
        "push_messages": state.push_messages,
        "gate_evaluations": state.gate_evaluations,
        "candidate_count": len([e for e in state.events if e.get("event_type") == "candidate"]),
        "accepted_count": len(state.accepted_rows),
        "rejected_count": len(reject_events),
        "reject_reason_counts": _count_reasons(state.reject_rows),
        "max_concurrent_positions": config.max_concurrent_positions,
        "peak_open_slots": state.peak_open_slots,
        "quality_distribution": _quality_distribution(state.quality_scores),
        "session_bucket_summary": state.bucket_summary,
        "data_gap_count": state.data_gap_count,
        "stale_tick_count": state.stale_tick_count,
        "open_slots_end": len(gate.state.open_slots),
        "config_sha256": session_cfg.get("config_sha256"),
        "stop_reason": state.stop_reason or "completed",
        "note": "Virtual hold on PUSH for concurrent cap; no orders placed.",
        "pilot_continue_review": {
            "data_received_ok": state.push_messages > 0,
            "gate_active_ok": state.gate_evaluations > 0,
            "suggest_continue_next_session": (
                state.push_messages > 0
                and state.gate_evaluations > 0
                and state.api_error_count < 50
            ),
            "human_decision_required": True,
        },
        **{k: session_cfg[k] for k in ("duration_sec", "poll_interval_sec", "session_start", "session_end") if k in session_cfg},
    }
    summary.update(_policy_summary_extras(config))
    return summary


def _early_session_summary(
    *,
    config: SmallPaperPilotConfig,
    session_cfg: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "phase": 45,
        "mode": "small_paper_pilot_live_full_dry_run",
        "generated_at": _now_iso(),
        "ended_at": _now_iso(),
        "order_enabled": False,
        "paper_only": True,
        "full_session": True,
        "stop_reason": reason,
        "runtime_sec": 0,
        "heartbeat_count": 0,
        "api_error_count": 0,
        "reconnect_count": 0,
        "candidate_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "reject_reason_counts": {},
        "max_concurrent_positions": config.max_concurrent_positions,
        "peak_open_slots": 0,
        "quality_distribution": _quality_distribution([]),
        "session_bucket_summary": empty_bucket_summary(),
        "data_gap_count": 0,
        "stale_tick_count": 0,
        "config_sha256": session_cfg.get("config_sha256"),
        "note": reason,
    }


def run_live_dry_run(
    config: SmallPaperPilotConfig,
    *,
    symbols: Sequence[tuple[str, str, int]],
    output_dir: Path,
    repo_root: Path,
    native_root: Path,
    config_path: Path,
    duration_sec: float,
    poll_interval_sec: float,
    max_polls: Optional[int] = None,
    safety_report: Optional[Mapping[str, Any]] = None,
    full_session: bool = False,
    session_start: str = "09:00",
    session_end: str = "15:30",
    auto_stop: bool = True,
    heartbeat_sec: float = 300.0,
    wait_until_session: bool = False,
    stale_tick_sec: float = 120.0,
    max_consecutive_api_errors: int = 10,
) -> PilotRunResult:
    """Live PUSH observation with exposure gate (no orders)."""
    import asyncio
    import time

    from api.push_client import KabuNativePushClient
    from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, load_kabu_env
    from research.exposure_gate import ExposureGate
    from small_paper.config import config_file_sha256
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from storage.push_recorder import PushRecorder

    load_kabu_env(repo_root=repo_root)
    now = datetime.now(JST)
    sched = SessionSchedule(session_start, session_end, now.date())

    if full_session and sched.is_after_session(now):
        conn: dict[str, Any] = {"ok": False, "skipped": "after_session_end"}
        session_cfg = _live_session_cfg(
            config_path=config_path,
            config=config,
            conn=conn,
            full_session=True,
            duration_sec=0,
            poll_interval_sec=poll_interval_sec,
            max_polls=max_polls,
            symbol_count=len(symbols),
            session_start=session_start,
            session_end=session_end,
            auto_stop=auto_stop,
            heartbeat_sec=heartbeat_sec,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_live_session_meta(output_dir, session_cfg=session_cfg, safety_report=safety_report)
        summary = _early_session_summary(
            config=config, session_cfg=session_cfg, reason="after_session_end"
        )
        LiveSessionWriter(output_dir, incremental=True, event_fields=EVENT_FIELDS).write_summary(
            summary
        )
        return PilotRunResult(output_dir=output_dir, summary=summary)

    if full_session and sched.is_before_session(now) and not wait_until_session:
        conn = {"ok": False, "skipped": "before_session_start"}
        session_cfg = _live_session_cfg(
            config_path=config_path,
            config=config,
            conn=conn,
            full_session=True,
            duration_sec=0,
            poll_interval_sec=poll_interval_sec,
            max_polls=max_polls,
            symbol_count=len(symbols),
            session_start=session_start,
            session_end=session_end,
            auto_stop=auto_stop,
            heartbeat_sec=heartbeat_sec,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_live_session_meta(output_dir, session_cfg=session_cfg, safety_report=safety_report)
        summary = _early_session_summary(
            config=config, session_cfg=session_cfg, reason="before_session_start"
        )
        LiveSessionWriter(output_dir, incremental=True, event_fields=EVENT_FIELDS).write_summary(
            summary
        )
        return PilotRunResult(output_dir=output_dir, summary=summary)

    if full_session and wait_until_session and sched.is_before_session(now):
        wait_until(sched.start_dt)

    if full_session and auto_stop:
        duration_sec = sched.seconds_until_end()

    conn = verify_kabu_connection(repo_root)
    rest = KabuNativeRestClient(default_base_url())
    token = rest.issue_token_from_env()
    push = KabuNativePushClient(rest, token)

    gate = config.make_exposure_gate()
    gate_cfg = config.exposure_gate_config()
    feature_bridge = LiveFeatureBridge(config.feature_bridge_config())
    code_to_symbol: dict[str, str] = {}
    sym_specs: list[tuple[str, int]] = []
    for sym, sym_key, ex in symbols:
        code = sym_key.split("@")[0]
        code_to_symbol[code] = sym
        sym_specs.append((code, ex))

    session_cfg = _live_session_cfg(
        config_path=config_path,
        config=config,
        conn=conn,
        full_session=full_session,
        duration_sec=duration_sec,
        poll_interval_sec=poll_interval_sec,
        max_polls=max_polls,
        symbol_count=len(symbols),
        session_start=session_start,
        session_end=session_end,
        auto_stop=auto_stop,
        heartbeat_sec=heartbeat_sec,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_live_session_meta(output_dir, session_cfg=session_cfg, safety_report=safety_report)

    trade_date = datetime.now(JST).date().isoformat()
    incremental = full_session
    writer = LiveSessionWriter(output_dir, incremental=incremental, event_fields=EVENT_FIELDS)
    state = _LiveRunState(started_mono=time.monotonic())
    pos_fields = ["symbol", "entry_time", "exit_time", "open_slots_after"]
    gap_threshold_sec = max(stale_tick_sec * 2, poll_interval_sec * 3)
    pipeline_ctx: Optional[_PushPipelineContext] = None

    discord: Optional[SmallPaperDiscordNotifier] = None
    observer: Optional[ObserverPositionTracker] = None
    if config.discord_enabled and not config.order_enabled and config.discord_observer_only:

        def _discord_error_logger(op: str, msg: str, extra: Mapping[str, Any]) -> None:
            writer.append_error(
                {
                    "event_time": _now_iso(),
                    "error_type": "discord_error",
                    "operation": op,
                    "message": msg,
                    **dict(extra),
                }
            )

        discord = discord_notifier_from_pilot(config, error_logger=_discord_error_logger)
        observer = ObserverPositionTracker(observer_tracker_config_from_pilot(config))

    pipeline_ctx = _PushPipelineContext(
        config=config,
        gate=gate,
        feature_bridge=feature_bridge,
        state=state,
        writer=writer,
        code_to_symbol=code_to_symbol,
        source="live",
        pos_fields=pos_fields,
        observer=observer,
        discord=discord,
        stale_tick_sec=stale_tick_sec,
        gap_threshold_sec=gap_threshold_sec,
    )

    def _request_stop(reason: str) -> None:
        state.stop_requested = True
        state.stop_reason = reason

    def _on_signal(_sig: int, _frame: Any) -> None:
        _request_stop("signal_interrupt")

    try:
        signal.signal(signal.SIGINT, _on_signal)
    except (ValueError, OSError):
        pass

    def _log_api_error(op: str, exc: Exception) -> None:
        state.api_error_count += 1
        state.consecutive_api_errors += 1
        writer.append_error(
            {
                "event_time": _now_iso(),
                "error_type": "api_error",
                "operation": op,
                "message": str(exc),
                "consecutive": state.consecutive_api_errors,
            }
        )
        if state.consecutive_api_errors >= max_consecutive_api_errors:
            _request_stop("max_consecutive_api_errors")
        if discord and discord.active and state.consecutive_api_errors in (1, 5, 10):
            discord.notify_error(operation=op, message=str(exc), extra={"consecutive": state.consecutive_api_errors})

    def _emit_heartbeat(note: str = "") -> None:
        state.heartbeat_count += 1
        runtime = time.monotonic() - state.started_mono
        hb = {
            "event_time": _now_iso(),
            "heartbeat_index": state.heartbeat_count,
            "runtime_sec": round(runtime, 1),
            "push_messages": state.push_messages,
            "gate_evaluations": state.gate_evaluations,
            "api_error_count": state.api_error_count,
            "open_slots": len(gate.state.open_slots),
            "note": note,
        }
        writer.append_heartbeat(hb)
        summary_partial = _build_live_summary(
            config=config,
            state=state,
            session_cfg=session_cfg,
            gate=gate,
            full_session=full_session,
            runtime_sec=runtime,
        )
        writer.write_summary(summary_partial)
        if discord and discord.should_send_heartbeat():
            obs_stats = _observer_stats_dict(observer)
            extras = build_session_summary_extras(
                accepted_rows=state.accepted_rows,
                bucket_summary=state.bucket_summary,
                observer_stats=obs_stats,
            )
            discord.notify_heartbeat(summary={**summary_partial, **extras})

    def _process_payload(payload: Mapping[str, Any], msg_i: int) -> None:
        assert pipeline_ctx is not None
        state.consecutive_api_errors = 0
        _process_push_payload(pipeline_ctx, payload, msg_i)

    async def _loop() -> None:
        nonlocal push, token

        push_rec_local = (
            PushRecorder(native_root, trade_date) if config.live_record_push_jsonl else None
        )
        try:
            push.register(sym_specs)
        except Exception as e:
            _log_api_error("register", e)
            return

        start = time.monotonic()
        last_hb = start
        msg_i = 0
        last_eval: dict[str, float] = {}

        def _should_stop() -> bool:
            if state.stop_requested:
                return True
            if full_session and auto_stop and sched.is_after_session():
                _request_stop("session_end")
                return True
            if duration_sec > 0 and (time.monotonic() - start) >= duration_sec:
                _request_stop("duration_elapsed")
                return True
            if max_polls is not None and msg_i >= max_polls:
                _request_stop("max_polls")
                return True
            return False

        async def _reconnect_push() -> bool:
            nonlocal push, token
            state.reconnect_count += 1
            writer.append_error(
                {
                    "event_time": _now_iso(),
                    "error_type": "reconnect",
                    "reconnect_count": state.reconnect_count,
                }
            )
            try:
                push.unregister_all()
            except Exception as e:
                _log_api_error("unregister_all", e)
            await asyncio.sleep(min(30.0, poll_interval_sec * 2))
            if state.stop_requested:
                return False
            try:
                token = rest.issue_token_from_env()
                push = KabuNativePushClient(rest, token)
                push.register(sym_specs)
                state.consecutive_api_errors = 0
                return True
            except Exception as e:
                _log_api_error("reconnect_register", e)
                return False

        try:
            while not _should_stop():
                if (time.monotonic() - last_hb) >= heartbeat_sec:
                    _emit_heartbeat()
                    last_hb = time.monotonic()
                try:
                    async for payload in push.iter_messages(recv_poll_sec=poll_interval_sec):
                        if _should_stop():
                            break
                        if (time.monotonic() - last_hb) >= heartbeat_sec:
                            _emit_heartbeat()
                            last_hb = time.monotonic()
                        if not isinstance(payload, dict):
                            continue
                        sym = _symbol_from_push(payload, code_to_symbol)
                        if not sym:
                            continue
                        msg_i += 1
                        if push_rec_local:
                            try:
                                push_rec_local.append(sym, payload, source="live_push")
                            except Exception as e:
                                _log_api_error("push_recorder", e)
                        ev_now = time.monotonic()
                        if sym in last_eval and (ev_now - last_eval[sym]) < poll_interval_sec:
                            continue
                        last_eval[sym] = ev_now
                        _process_payload(payload, msg_i)
                except asyncio.CancelledError:
                    _request_stop("cancelled")
                    break
                except KabuNativeApiError as e:
                    _log_api_error("push_iter", e)
                except Exception as e:
                    _log_api_error("push_unexpected", e)
                if _should_stop():
                    break
                if state.consecutive_api_errors >= max_consecutive_api_errors:
                    break
                if not await _reconnect_push():
                    break
        finally:
            try:
                push.unregister_all()
            except Exception:
                pass

    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        _request_stop("keyboard_interrupt")

    runtime_sec = time.monotonic() - state.started_mono
    positions = _build_positions_snapshot(state.accepted_rows, gate)
    summary = _build_live_summary(
        config=config,
        state=state,
        session_cfg=session_cfg,
        gate=gate,
        full_session=full_session,
        runtime_sec=runtime_sec,
    )
    if observer:
        exit_events = observer.close_all(reason=state.stop_reason or "session_end")
        _dispatch_observer_events(exit_events, discord=discord)
    summary.update(
        build_session_summary_extras(
            accepted_rows=state.accepted_rows,
            bucket_summary=state.bucket_summary,
            observer_stats=_observer_stats_dict(observer),
        )
    )
    if discord and discord.active:
        discord.notify_session_summary(summary=summary)
    writer.finalize_batch(
        events=state.events,
        positions=positions,
        summary=summary,
        pos_fields=pos_fields,
    )
    _write_quality_top_debug(output_dir, state.events)
    return PilotRunResult(
        output_dir=output_dir,
        summary=summary,
        events=state.events,
        accepted=state.accepted_rows,
        rejects=state.reject_rows,
    )


def _observer_stats_dict(observer: Optional[ObserverPositionTracker]) -> Optional[dict[str, Any]]:
    if observer is None:
        return None
    s = observer.stats
    cfg = observer.cfg
    return {
        "entry_count": s.entry_count,
        "exit_count": s.exit_count,
        "hold_notify_count": s.hold_notify_count,
        "take_count": s.take_count,
        "holding_count": observer.open_count(),
        "hold_durations_sec": list(s.hold_durations_sec),
        "structural_exit_policy": cfg.structural_exit_policy,
        "structural_exit_count": s.structural_exit_count,
        "structural_exit_reason_counts": dict(s.structural_exit_reason_counts),
        "virtual_hold_expired_ignored_count": s.virtual_hold_expired_ignored_count,
        "official_exit_count": s.official_exit_count,
        "session_end_exit_count": s.session_end_exit_count,
    }


def _live_session_cfg(
    *,
    config_path: Path,
    config: SmallPaperPilotConfig,
    conn: Mapping[str, Any],
    full_session: bool,
    duration_sec: float,
    poll_interval_sec: float,
    max_polls: Optional[int],
    symbol_count: int,
    session_start: str,
    session_end: str,
    auto_stop: bool,
    heartbeat_sec: float,
) -> dict[str, Any]:
    from small_paper.config import config_file_sha256

    return {
        "phase": 45,
        "generated_at": _now_iso(),
        "config_path": str(config_path),
        "config_sha256": config_file_sha256(config_path),
        "order_enabled": False,
        "paper_only": True,
        "dry_run": True,
        "source": "live",
        "full_session": full_session,
        "duration_sec": duration_sec,
        "poll_interval_sec": poll_interval_sec,
        "max_polls": max_polls,
        "symbol_count": symbol_count,
        "session_start": session_start,
        "session_end": session_end,
        "auto_stop": auto_stop,
        "heartbeat_sec": heartbeat_sec,
        "kabu_connection": dict(conn),
    }


def _write_live_session_meta(
    output_dir: Path,
    *,
    session_cfg: Mapping[str, Any],
    safety_report: Optional[Mapping[str, Any]],
) -> None:
    (output_dir / "live_session_config.json").write_text(
        json.dumps(dict(session_cfg), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if safety_report:
        (output_dir / "live_session_safety_report.json").write_text(
            json.dumps(dict(safety_report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def run_poll_dry_run(
    config: SmallPaperPilotConfig,
    *,
    symbols: Sequence[tuple[str, str, int]],
    output_dir: Path,
    repo_root: Path,
    max_polls: int,
) -> PilotRunResult:
    """
  Poll kabu board snapshots; log observation events only (no ENTRY engine — no new logic).
    Does not place orders.
    """
    from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env

    load_kabu_env(repo_root=repo_root)
    client = KabuNativeRestClient(default_base_url())
    token = client.issue_token_from_env()

    events: list[dict[str, Any]] = []
    for poll in range(max(1, max_polls)):
        poll_ts = _now_iso()
        for sym, sym_key, _ex in symbols:
            try:
                board = client.get_board(sym_key, token=token)
            except Exception as e:
                events.append(
                    {
                        "event_time": poll_ts,
                        "event_type": "poll_error",
                        "symbol": sym,
                        "profile": config.profile,
                        "gate_reject_reason": str(e),
                        "dry_run": True,
                        "source": "poll",
                        "poll_index": poll,
                    }
                )
                continue
            events.append(
                {
                    "event_time": poll_ts,
                    "event_type": "poll_observation",
                    "symbol": sym,
                    "profile": config.profile,
                    "entry_time": board.get("CurrentPriceTime"),
                    "continuation_quality_score": None,
                    "gate_accept": None,
                    "gate_reject_reason": "poll_mode_no_entry_engine",
                    "dry_run": True,
                    "source": "poll",
                    "poll_index": poll,
                    "current_price": board.get("CurrentPrice"),
                }
            )

    summary = {
        "phase": 44,
        "mode": "small_paper_pilot_poll_dry_run",
        "generated_at": _now_iso(),
        "order_enabled": False,
        "source": "poll",
        "max_polls": max_polls,
        "symbol_count": len(symbols),
        "note": "Poll mode logs observations only; use replay source for gate accepted/rejected counts.",
    }
    _write_outputs(output_dir, events=events, accepted=[], rejects=[], positions=[], summary=summary)
    return PilotRunResult(output_dir=output_dir, summary=summary, events=events)


def _count_reasons(rejects: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rejects:
        reason = str(r.get("gate_reject_reason") or "unknown")
        out[reason] = out.get(reason, 0) + 1
    return out


def _build_positions_snapshot(
    accepted: Sequence[Mapping[str, Any]],
    gate: ExposureGate,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = sorted(accepted, key=lambda t: str(t.get("entry_time") or ""))
    for t in ordered:
        decision = gate.evaluate_entry(t)
        if decision.accept:
            gate.record_accepted(t)
            rows.append(
                {
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "exit_time": t.get("exit_time"),
                    "open_slots_after": len(gate.state.open_slots),
                }
            )
    return rows


def _write_outputs(
    output_dir: Path,
    *,
    events: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
    rejects: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "small_paper_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(output_dir / "small_paper_events.csv", EVENT_FIELDS, events)
    with (output_dir / "small_paper_events.jsonl").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    reject_fields = list(EVENT_FIELDS)
    _write_csv(output_dir / "small_paper_rejects.csv", reject_fields, [e for e in events if e.get("event_type") == "rejected"])
    pos_fields = ["symbol", "entry_time", "exit_time", "open_slots_after"]
    _write_csv(output_dir / "small_paper_positions.csv", pos_fields, positions)


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
