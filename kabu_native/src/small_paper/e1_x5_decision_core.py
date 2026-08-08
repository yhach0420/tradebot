"""E1_X5 shared decision core — Offline replay and Runtime Shadow call the same path.

Processing order (characterization-locked):
  1. Build normalized tick / observe (FeatureEngine.update EVERY event)
  2. EXIT monitor if position open (every event; Offline granularity)
  3. Sample-due gate (REGULAR 5s + STATE_CHANGE) → score only
  4. ENTRY try only when SCORE present
  5. CAP / same-symbol inside try_entry
  6. No re-ENTRY on same event without a new SCORE after EXIT

Strategy decision time must be injected (event_time); never wall-clock fallback.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

KIND_SCORE = "SCORE"
KIND_MISSING = "MISSING_SCORE"
KIND_NO_SAMPLE = "NO_SAMPLE"
KIND_IDENTITY_FAIL = "IDENTITY_FAIL"
KIND_TICK_FAIL = "TICK_BUILD_FAILED"
KIND_NO_DECISION_TIME = "NO_EVALUATION_DECISION_TIME_MISSING"


@dataclass
class E1X5EventDecision:
    """One-event result shared by Offline + Runtime."""

    observe_kind: str
    sample_reason: str = ""  # REGULAR_5S | STATE_CHANGE | NOT_DUE | NO_EVALUATION
    feature_updated: bool = False
    exit_monitored: bool = False
    score_evaluated: bool = False
    score: Optional[float] = None
    spread_bps: Optional[float] = None
    feature_hash: str = ""
    sample_id: str = ""
    entry_result: Optional[str] = None  # None=entered, else reject reason
    exit_happened: bool = False
    exit_reason: Optional[str] = None
    missing_reason: Optional[str] = None
    event_time: Optional[datetime] = None
    event_sequence: Optional[int] = None
    symbol: str = ""
    bid: Optional[float] = None
    ask: Optional[float] = None
    position_before: bool = False
    position_after: bool = False
    cap_before: int = 0
    cap_after: int = 0


@dataclass
class E1X5EventLog:
    """Single-session structured eval log (one file)."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    path: Optional[Path] = None

    def append(self, row: dict[str, Any]) -> None:
        self.rows.append(row)

    def flush(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for r in self.rows:
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        self.path = path


def process_e1_x5_event(
    *,
    provider: Any,
    session: Any,
    symbol: str,
    payload: Mapping[str, Any],
    day: Optional[str] = None,
    event_sequence: Optional[int] = None,
    event_id: str = "",
    decision_time: Optional[datetime] = None,
    event_log: Optional[E1X5EventLog] = None,
) -> E1X5EventDecision:
    """Common one-event decision core.

    FeatureEngine update happens inside provider.observe for every valid tick.
    EXIT runs whenever a position is open (SCORE / MISSING / NO_SAMPLE).
    Score + ENTRY only when observe returns SCORE (5s / state_change gate inside provider).
    """
    from small_paper.canonical_board import best_bid_ask_for_mode
    from small_paper.e1_x5_dmid_score_provider import (
        KIND_IDENTITY_FAIL as P_IDFAIL,
        KIND_MISSING as P_MISSING,
        KIND_NO_SAMPLE as P_NOSAMPLE,
        KIND_SCORE as P_SCORE,
        validate_score_identity,
    )

    sym = str(symbol or "")
    if not sym.endswith(".T") and sym:
        sym = f"{sym}.T"

    open_before = sym in getattr(session, "positions", {})
    cap_before = len(getattr(session, "positions", {}))
    exits_before = len(getattr(session, "exits", []))

    # Decision time: prefer explicit injection; else payload CurrentPriceTime; NEVER now().
    if decision_time is None:
        from research.integrated_order_flow_absorption_reversal.loader import parse_ts

        decision_time = (
            parse_ts(payload.get("CurrentPriceTime"))
            or parse_ts(payload.get("received_at_jst"))
            or parse_ts(payload.get("event_time"))
            or parse_ts(payload.get("received_at"))
        )
    if decision_time is not None and decision_time.tzinfo is None:
        decision_time = decision_time.replace(tzinfo=JST)

    bid, ask = best_bid_ask_for_mode(payload, mode="canonical")

    if decision_time is None:
        # Cannot evaluate strategy without stored time — explicit NO EVALUATION.
        _maybe_cancel_pending(session, sym, reason="NO_EVALUATION", seq=event_sequence, ts=None)
        out = E1X5EventDecision(
            observe_kind=KIND_NO_DECISION_TIME,
            sample_reason="NO_EVALUATION",
            feature_updated=False,
            exit_monitored=False,
            missing_reason=KIND_NO_DECISION_TIME,
            symbol=sym,
            bid=bid,
            ask=ask,
            position_before=open_before,
            position_after=open_before,
            cap_before=cap_before,
            cap_after=cap_before,
        )
        _log(event_log, event_id, out, day=day)
        return out

    # Ensure payload carries decision time for provider (no now() fallback needed).
    payload_use: dict[str, Any] = dict(payload)
    if not payload_use.get("CurrentPriceTime"):
        payload_use["CurrentPriceTime"] = decision_time.isoformat()
    if event_sequence is not None and payload_use.get("sequence") is None:
        payload_use["sequence"] = int(event_sequence)

    result = provider.observe(
        symbol=sym,
        payload=payload_use,
        day=day or decision_time.strftime("%Y%m%d"),
        event_sequence=event_sequence,
    )

    out = E1X5EventDecision(
        observe_kind=result.kind,
        feature_updated=True,  # observe updates FE when tick builds; False only on tick fail
        symbol=sym,
        bid=bid,
        ask=ask,
        event_time=result.event_time or decision_time,
        event_sequence=result.event_sequence if result.event_sequence is not None else event_sequence,
        position_before=open_before,
        cap_before=cap_before,
    )

    if result.kind in (P_MISSING,) and result.reason in (
        "TICK_BUILD_FAILED",
        "BAD_SYMBOL",
        "SESSION_OTHER",
    ):
        out.feature_updated = False

    if result.kind == P_SCORE and result.packet is not None:
        pkt = result.packet
        stype = str(getattr(result, "sample_type", "") or "")
        if stype == "REGULAR":
            out.sample_reason = "REGULAR_5S"
        elif stype == "STATE_CHANGE":
            out.sample_reason = "STATE_CHANGE"
        else:
            # Should not happen when SCORE is emitted with sample_type; keep explicit.
            out.sample_reason = "REGULAR_5S" if not stype else stype
        out.score_evaluated = True
        out.score = float(pkt.score)
        out.spread_bps = pkt.spread_bps
        out.sample_id = pkt.sample_id
        out.feature_hash = _packet_hash(pkt)
        out.bid = pkt.bid
        out.ask = pkt.ask
        id_fail = validate_score_identity(
            packet=pkt,
            symbol=sym,
            event_time=pkt.event_time,
            event_sequence=pkt.event_sequence,
            snapshot_id=pkt.snapshot_id,
        )
        if id_fail:
            out.observe_kind = KIND_IDENTITY_FAIL
            out.missing_reason = id_fail
            _maybe_cancel_pending(session, sym, reason="NO_EVALUATION", seq=pkt.event_sequence, ts=pkt.event_time)
            session.on_identity_fail(symbol=sym, ts=pkt.event_time, reason=id_fail, bid=pkt.bid, ask=pkt.ask)
            out.exit_monitored = open_before
        else:
            out.exit_monitored = open_before or (sym in session.positions)
            entry_res = None
            # on_quote: EXIT first, then try_entry when score present
            before_entries = len(session.entries)
            session.on_quote(
                symbol=pkt.symbol,
                ts=pkt.event_time,
                bid=pkt.bid,
                ask=pkt.ask,
                score=float(pkt.score),
                spread_bps=pkt.spread_bps,
                sample_id=pkt.sample_id,
                day=pkt.day,
                mid=pkt.mid,
                event_sequence=pkt.event_sequence,
            )
            # G1 confirmation (no-op on plain ForwardShadow / BASE variant)
            _maybe_confirm_independent_push(
                session,
                symbol=sym,
                ts=pkt.event_time,
                bid=pkt.bid,
                ask=pkt.ask,
                sequence=pkt.event_sequence if pkt.event_sequence is not None else event_sequence,
                observe_kind=KIND_SCORE,
                score=float(pkt.score),
                spread_bps=pkt.spread_bps,
                sample_id=pkt.sample_id,
                day=pkt.day,
                mid=pkt.mid,
            )
            if len(session.entries) > before_entries:
                out.entry_result = None  # entered
            else:
                # last candidate reject if any
                if session.candidates:
                    last = session.candidates[-1]
                    if last.get("symbol") == sym and last.get("entry_decision") != "ENTER":
                        out.entry_result = str(last.get("reject_reason") or "REJECT")
            out.exit_happened = len(session.exits) > exits_before
            if out.exit_happened and session.exits:
                out.exit_reason = session.exits[-1].get("exit_reason")

    elif result.kind == P_MISSING:
        out.sample_reason = "NO_EVALUATION"
        out.missing_reason = result.reason or "NO_EVALUATION_MISSING_SCORE"
        out.exit_monitored = open_before
        session.on_missing_score(
            symbol=sym,
            ts=result.event_time or decision_time,
            bid=bid,
            ask=ask,
            reason=out.missing_reason,
            sample_id=result.snapshot_id or "",
            day=day or decision_time.strftime("%Y%m%d"),
            event_sequence=result.event_sequence,
        )
        out.exit_happened = len(session.exits) > exits_before
        if out.exit_happened and session.exits:
            out.exit_reason = session.exits[-1].get("exit_reason")

    elif result.kind == P_IDFAIL:
        out.sample_reason = "NO_EVALUATION"
        out.missing_reason = result.reason
        out.exit_monitored = open_before
        _maybe_cancel_pending(
            session,
            sym,
            reason="NO_EVALUATION",
            seq=event_sequence,
            ts=result.event_time or decision_time,
        )
        session.on_identity_fail(
            symbol=sym,
            ts=result.event_time or decision_time,
            reason=result.reason or "IDENTITY_FAIL",
            bid=bid,
            ask=ask,
        )

    else:
        # NO_SAMPLE: FE already updated; EXIT monitor only; no score/ENTRY
        out.observe_kind = KIND_NO_SAMPLE
        out.sample_reason = "NOT_DUE"
        out.exit_monitored = open_before
        ts_use = result.event_time or decision_time
        day_use = day or decision_time.strftime("%Y%m%d")
        if open_before:
            session.on_quote(
                symbol=sym,
                ts=ts_use,
                bid=bid,
                ask=ask,
                day=day_use,
                event_sequence=event_sequence,
            )
            out.exit_happened = len(session.exits) > exits_before
            if out.exit_happened and session.exits:
                out.exit_reason = session.exits[-1].get("exit_reason")
        # Independent PUSH without SCORE may cancel/confirm pending G1 arms
        _maybe_confirm_independent_push(
            session,
            symbol=sym,
            ts=ts_use,
            bid=bid,
            ask=ask,
            sequence=event_sequence if event_sequence is not None else result.event_sequence,
            observe_kind=KIND_NO_SAMPLE,
            score=None,
            spread_bps=None,
            day=day_use,
        )
        if not out.exit_happened:
            out.exit_happened = len(session.exits) > exits_before
            if out.exit_happened and session.exits:
                out.exit_reason = session.exits[-1].get("exit_reason")

    out.position_after = sym in session.positions
    out.cap_after = len(session.positions)
    _log(event_log, event_id, out, day=day)
    return out


def _maybe_cancel_pending(
    session: Any,
    symbol: str,
    *,
    reason: str,
    seq: Any = None,
    ts: Any = None,
) -> None:
    pending = getattr(session, "pending", None)
    cancel = getattr(session, "cancel_pending", None)
    if not callable(cancel) or not isinstance(pending, dict):
        return
    if symbol not in pending:
        return
    try:
        cancel(symbol, reason, seq=seq, ts=ts)
    except TypeError:
        cancel(symbol, reason)


def _maybe_confirm_independent_push(session: Any, **kwargs: Any) -> None:
    """Invoke G1 confirmation when session supports it (BASE/ForwardShadow = no-op)."""
    fn = getattr(session, "confirm_on_independent_push", None)
    if not callable(fn):
        return
    fn(**kwargs)


def _packet_hash(pkt: Any) -> str:
    """Legacy helper name — delegates to shared canonical score-identity hash."""
    from small_paper.e1_x5_canonical_feature_hash import canonical_score_identity_hash

    return str(
        canonical_score_identity_hash(
            sample_id=str(getattr(pkt, "sample_id", "") or ""),
            score=getattr(pkt, "score", None),
            spread_bps=getattr(pkt, "spread_bps", None),
            bid=getattr(pkt, "bid", None),
            ask=getattr(pkt, "ask", None),
            event_sequence=getattr(pkt, "event_sequence", None),
        )["feature_hash"]
    )


def _feature_hash_meta(pkt: Any = None, *, feature_hash: str = "") -> dict[str, Any]:
    from small_paper.e1_x5_canonical_feature_hash import (
        FEATURE_HASH_SCHEMA,
        FEATURE_HASH_VERSION,
        canonical_score_identity_hash,
    )

    if pkt is not None:
        return canonical_score_identity_hash(
            sample_id=str(getattr(pkt, "sample_id", "") or ""),
            score=getattr(pkt, "score", None),
            spread_bps=getattr(pkt, "spread_bps", None),
            bid=getattr(pkt, "bid", None),
            ask=getattr(pkt, "ask", None),
            event_sequence=getattr(pkt, "event_sequence", None),
        )
    return {
        "feature_hash": feature_hash or "",
        "feature_hash_schema": FEATURE_HASH_SCHEMA,
        "feature_hash_version": FEATURE_HASH_VERSION,
    }


def _log(event_log: Optional[E1X5EventLog], event_id: str, out: E1X5EventDecision, *, day: Any) -> None:
    if event_log is None:
        return
    meta = {
        "feature_hash_schema": None,
        "feature_hash_version": None,
    }
    if out.feature_hash:
        from small_paper.e1_x5_canonical_feature_hash import FEATURE_HASH_SCHEMA, FEATURE_HASH_VERSION

        meta["feature_hash_schema"] = FEATURE_HASH_SCHEMA
        meta["feature_hash_version"] = FEATURE_HASH_VERSION
    event_log.append(
        {
            "event_id": event_id,
            "day": day,
            "symbol": out.symbol,
            "ingress_sequence": out.event_sequence,
            "event_time": out.event_time.isoformat() if out.event_time else None,
            "observe_kind": out.observe_kind,
            "sample_reason": out.sample_reason,
            "feature_updated": out.feature_updated,
            "exit_monitored": out.exit_monitored,
            "score_evaluated": out.score_evaluated,
            "score": out.score,
            "spread_bps": out.spread_bps,
            "feature_hash": out.feature_hash,
            "feature_hash_schema": meta["feature_hash_schema"],
            "feature_hash_version": meta["feature_hash_version"],
            "sample_id": out.sample_id,
            "entry_result": out.entry_result,
            "exit_happened": out.exit_happened,
            "exit_reason": out.exit_reason,
            "missing_reason": out.missing_reason,
            "position_before": out.position_before,
            "position_after": out.position_after,
            "cap_before": out.cap_before,
            "cap_after": out.cap_after,
            "bid": out.bid,
            "ask": out.ask,
        }
    )


def feed_e1_x5_from_runtime_state(
    state: Any,
    *,
    symbol: str,
    payload: Mapping[str, Any],
) -> Optional[E1X5EventDecision]:
    """Runtime adapter: pull provider+session from live state and process one event."""
    e1x5 = getattr(state, "e1_x5_forward_shadow", None)
    if e1x5 is None or not getattr(e1x5, "enabled", False):
        return None
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider

    provider = getattr(state, "e1_x5_dmid_score_provider", None)
    if provider is None:
        provider = DMidD4H6ScoreProvider.maybe_create()
        try:
            setattr(state, "e1_x5_dmid_score_provider", provider)
        except Exception:
            pass
    log = getattr(state, "e1_x5_event_log", None)
    if log is None:
        log = E1X5EventLog()
        try:
            setattr(state, "e1_x5_event_log", log)
        except Exception:
            pass
    seq = payload.get("sequence")
    try:
        seq_i = int(seq) if seq is not None else None
    except (TypeError, ValueError):
        seq_i = None
    return process_e1_x5_event(
        provider=provider,
        session=e1x5,
        symbol=symbol,
        payload=payload,
        event_sequence=seq_i,
        event_id=str(payload.get("raw_record_id") or ""),
        event_log=log,
    )
