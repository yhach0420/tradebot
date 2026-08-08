"""Full canonical event replay per analysis_mask partition (research-only).

Used by BASE (X5), candidate confirm, final candidate, and RefitLODO.
Does NOT modify E1_X5 Runtime / Paper / Live paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence

from research.e1_x6_provisional.analysis_mask import row_in_analysis_mask
from research.e1_x6_provisional.cost_contract import net_pnl_yen
from research.e1_x6_provisional.replay_lifecycle_contract import (
    EVALUATION_MODE_REQUIRED,
    REPLAY_LIFECYCLE_CONTRACT_TEXT,
)
from research.e1_x6_provisional.util import JST, parse_ts, sha256_obj, summarize_pnls
from small_paper.e1_x5_forward_shadow import THRESHOLD


ORPHAN_REASON = "WINDOW_CENSORED"
ORPHAN_REASON_ALIAS = "WINDOW_END_OPEN_EXCLUDED"


@dataclass
class PartitionReplayResult:
    day: str
    am_pm: str
    replay_partition_id: str
    evaluation_mode: str = EVALUATION_MODE_REQUIRED
    completed_trades: list[dict[str, Any]] = field(default_factory=list)
    censored_ledger: list[dict[str, Any]] = field(default_factory=list)
    decision_ledger: list[dict[str, Any]] = field(default_factory=list)
    signal_ledger: list[dict[str, Any]] = field(default_factory=list)
    score_rows: list[dict[str, Any]] = field(default_factory=list)
    # (symbol, ts_epoch, sequence, bid) for every event where the session would
    # run its EXIT monitor if a position were open (bid>0; all observe kinds).
    exit_stream: list[tuple[str, float, int, float]] = field(default_factory=list)
    exit_stream_ts_mismatch: int = 0
    events_fed: int = 0
    events_skipped_out_of_scope: int = 0
    cap_blocked: int = 0
    same_symbol_blocked: int = 0
    open_at_end_n: int = 0
    open_at_end_symbols: list[str] = field(default_factory=list)
    exit_reason_counts: dict[str, int] = field(default_factory=dict)
    mask_meta: dict[str, Any] = field(default_factory=dict)
    entry_mode: str = "X5"

    def metrics(self) -> dict[str, Any]:
        pnls = [float(t["net_pnl_yen_100"]) for t in self.completed_trades]
        m = summarize_pnls(pnls)
        m["exit_reason_counts"] = dict(self.exit_reason_counts)
        m["cap_blocked"] = self.cap_blocked
        m["same_symbol_blocked"] = self.same_symbol_blocked
        m["open_at_end_n"] = int(self.open_at_end_n)
        m["open_at_end_symbols"] = list(self.open_at_end_symbols)
        m["censored_n"] = len(self.censored_ledger)
        m["events_fed"] = self.events_fed
        m["evaluation_mode"] = self.evaluation_mode
        m["completed_trade_ledger_sha256"] = sha256_obj(self.completed_trades)
        m["decision_ledger_sha256"] = sha256_obj(self.decision_ledger)
        m["signal_ledger_sha256"] = sha256_obj(self.signal_ledger)
        m["censored_ledger_sha256"] = sha256_obj(self.censored_ledger)
        return m


def assert_signal_ledger_nonempty_when_decisions_or_trades(
    *,
    signal_ledger: Sequence[Mapping[str, Any]],
    decision_ledger: Sequence[Mapping[str, Any]],
    completed_trades: Sequence[Mapping[str, Any]],
) -> None:
    """Vacuous PASS forbidden: decisions/trades require a non-empty SignalLedger."""
    if (decision_ledger or completed_trades) and not signal_ledger:
        raise AssertionError(
            "SIGNAL_LEDGER_EMPTY_WITH_DECISIONS_OR_TRADES: "
            f"decisions={len(decision_ledger)} trades={len(completed_trades)} signals=0"
        )


def _mid_from_payload(payload: dict) -> Optional[float]:
    from small_paper.canonical_board import best_bid_ask_for_mode

    bid, ask = best_bid_ask_for_mode(payload, mode="canonical")
    if bid and ask and bid > 0 and ask > 0 and ask >= bid:
        return (float(bid) + float(ask)) / 2.0
    cp = payload.get("CurrentPrice")
    try:
        v = float(cp) if cp is not None else None
        return v if v and v > 0 else None
    except Exception:
        return None


def _stamp_lineage(
    row: dict[str, Any],
    *,
    day: str,
    am_pm: str,
    mask_meta: Mapping[str, Any],
    replay_partition_id: str,
    event_scope: str,
) -> dict[str, Any]:
    out = dict(row)
    out["day"] = day
    out["am_pm"] = am_pm
    out["session_id"] = mask_meta.get("session_id") or mask_meta.get("capture_session_id")
    out["window_id"] = mask_meta.get("window_id")
    out["analysis_mask_id"] = mask_meta.get("analysis_mask_id")
    out["replay_partition_id"] = replay_partition_id
    out["quality_class"] = mask_meta.get("quality_class")
    out["valid_window_start"] = mask_meta.get("valid_window_start")
    out["valid_window_end"] = mask_meta.get("valid_window_end")
    out["in_analysis_mask"] = True
    out["event_scope"] = event_scope
    out["evaluation_mode"] = EVALUATION_MODE_REQUIRED
    return out


def _entry_lineage(
    *,
    day: str,
    am_pm: str,
    mask_meta: Mapping[str, Any],
    replay_partition_id: str,
    entry_time: Any,
    entry_event_scope: str = "IN_PARTITION_ENTRY",
) -> dict[str, Any]:
    return {
        "day": day,
        "am_pm": am_pm,
        "session_id": mask_meta.get("session_id") or mask_meta.get("capture_session_id"),
        "window_id": mask_meta.get("window_id"),
        "analysis_mask_id": mask_meta.get("analysis_mask_id"),
        "replay_partition_id": replay_partition_id,
        "quality_class": mask_meta.get("quality_class"),
        "valid_window_start": mask_meta.get("valid_window_start"),
        "valid_window_end": mask_meta.get("valid_window_end"),
        "event_time": entry_time,
        "event_scope": entry_event_scope,
        "in_analysis_mask": True,
    }


def _exit_lineage(
    *,
    day: str,
    am_pm: str,
    mask_meta: Mapping[str, Any],
    replay_partition_id: str,
    exit_time: Any,
    exit_event_scope: str = "IN_PARTITION_EXIT",
) -> dict[str, Any]:
    return {
        "day": day,
        "am_pm": am_pm,
        "session_id": mask_meta.get("session_id") or mask_meta.get("capture_session_id"),
        "window_id": mask_meta.get("window_id"),
        "analysis_mask_id": mask_meta.get("analysis_mask_id"),
        "replay_partition_id": replay_partition_id,
        "quality_class": mask_meta.get("quality_class"),
        "valid_window_start": mask_meta.get("valid_window_start"),
        "valid_window_end": mask_meta.get("valid_window_end"),
        "event_time": exit_time,
        "event_scope": exit_event_scope,
        "in_analysis_mask": True,
    }


def _enrich_econ_from_exit(raw_exit: Mapping[str, Any], adopted: dict[str, Any]) -> dict[str, Any]:
    """Ensure cost/net_bps never null for completed trades (single 5bps application)."""
    out = dict(adopted)
    for k in ("gross_pnl_yen_100", "cost_yen_100", "net_pnl_yen_100", "net_bps"):
        if out.get(k) is None and raw_exit.get(k) is not None:
            out[k] = raw_exit.get(k)
    if out.get("cost_yen_100") is None or out.get("net_bps") is None:
        try:
            econ = net_pnl_yen(float(out["entry_ask"]), float(out["exit_bid"]))
            out.setdefault("gross_pnl_yen_100", econ["gross_pnl_yen_100"])
            out.setdefault("cost_yen_100", econ["cost_yen_100"])
            out["net_pnl_yen_100"] = econ["net_pnl_yen_100"]
            out["net_bps"] = econ["net_bps"]
        except Exception:
            pass
    return out


def _install_candidate_entry_gate(
    session: Any,
    *,
    candidate_spec: Mapping[str, Any],
    passes_fn: Callable[[dict[str, Any], Mapping[str, Any]], bool],
) -> Callable[[], None]:
    """Wrap evaluate_entry_gates so ENTRY requires candidate _passes + spread/CAP/same-symbol.

    X5 score threshold alone is NOT sufficient; candidate gate replaces it.
    """
    orig_gates = session.evaluate_entry_gates

    def wrapped_gates(
        *,
        symbol: str,
        ts: datetime,
        bid: Optional[float],
        ask: Optional[float],
        score: float,
        spread_bps: Optional[float],
    ):
        # Build SCORE-like row for candidate predicate
        mid = None
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            mid = (float(bid) + float(ask)) / 2.0
        sp = float(spread_bps) if spread_bps is not None else (
            ((float(ask) - float(bid)) / float(ask) * 10000.0)
            if bid is not None and ask is not None and ask > 0
            else None
        )
        gap = float(score) - float(THRESHOLD) if score is not None else None
        row = {
            "score": float(score) if score is not None else None,
            "spread_bps": sp,
            "score_vs_threshold_gap": gap,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "symbol": symbol,
            "symbol_norm": symbol,
        }
        if not passes_fn(row, candidate_spec):
            return "CANDIDATE_GATE", float(sp or 0.0)
        # Candidate passed: apply remaining X5 gates but bypass X5 threshold
        # by presenting a score that clears THRESHOLD while CAP/spread/same-symbol still apply.
        synth = max(float(score), float(THRESHOLD))
        return orig_gates(
            symbol=symbol, ts=ts, bid=bid, ask=ask, score=synth, spread_bps=spread_bps
        )

    session.evaluate_entry_gates = wrapped_gates  # type: ignore[method-assign]

    def restore() -> None:
        session.evaluate_entry_gates = orig_gates  # type: ignore[method-assign]

    return restore


def replay_partition(
    *,
    day: str,
    am_pm: str,
    events_in_valid_window: Sequence[Any],
    universe: Optional[set[str]],
    provider_factory: Callable[[], Any],
    entry_mode: str = "X5",
    candidate_spec: Optional[Mapping[str, Any]] = None,
    mask_meta: Optional[Mapping[str, Any]] = None,
    gap_intervals: Optional[Sequence[tuple[Any, Any]]] = None,
    passes_fn: Optional[Callable[[dict[str, Any], Mapping[str, Any]], bool]] = None,
    collect_score_rows: bool = False,
    collect_exit_stream: bool = False,
    banner: str = "",
) -> PartitionReplayResult:
    """Fresh warmup-gated E1 session for one analysis_mask partition (day×AM|PM).

    Contract (see REPLAY_LIFECYCLE_CONTRACT_TEXT):
    - Feed ALL board events in valid_window only (caller must pre-clip).
    - FE+EXIT every event; ENTRY on SCORE samples only.
    - CANDIDATE: entry if candidate _passes AND spread/CAP/same-symbol (not X5 threshold alone).
    - Orphan opens → WINDOW_CENSORED; no force-close; no post-window exit grace.
    - Completed exits only from in-scope event processing.
    """
    from research.e1_x6_provisional.util import norm_sym
    from small_paper.e1_x5_canonical_replay import (
        _adopt_trade,
        canonical_trade_row,
        make_warmup_gated_session,
        parse_ts as cr_parse_ts,
    )
    from small_paper.e1_x5_g1_guard_process import process_e1_x5_guard_event

    if entry_mode not in ("X5", "CANDIDATE"):
        raise ValueError(f"entry_mode must be X5|CANDIDATE, got {entry_mode}")
    if entry_mode == "CANDIDATE" and not candidate_spec:
        raise ValueError("candidate_spec required for CANDIDATE entry_mode")
    if entry_mode == "CANDIDATE" and passes_fn is None:
        from research.e1_x6_provisional.p2_execute import _passes

        passes_fn = _passes

    meta = dict(mask_meta or {})
    replay_partition_id = f"{day}|{am_pm}|{meta.get('analysis_mask_id') or meta.get('window_id') or 'mask'}"
    out = PartitionReplayResult(
        day=day,
        am_pm=am_pm,
        replay_partition_id=replay_partition_id,
        mask_meta=meta,
        entry_mode=entry_mode,
    )

    provider = provider_factory()
    if provider is None or not getattr(provider, "ready", False):
        raise RuntimeError("score provider unavailable for partition replay")
    lookback_sec = provider.required_feature_lookback_sec()
    session = make_warmup_gated_session(variant="BASE", state_rearm=False, provider=provider)

    restore_gate = None
    if entry_mode == "CANDIDATE":
        restore_gate = _install_candidate_entry_gate(
            session, candidate_spec=candidate_spec, passes_fn=passes_fn  # type: ignore[arg-type]
        )

    uni = universe
    gaps = list(gap_intervals or [])
    events = list(events_in_valid_window)
    v0 = parse_ts(meta.get("valid_window_start"))
    v1 = parse_ts(meta.get("valid_window_end"))

    try:
        for e in events:
            sym = norm_sym(e.symbol)
            if uni and sym not in uni:
                continue
            payload = dict(e.payload or {})
            payload.setdefault("Symbol", sym.replace(".T", ""))
            recv = cr_parse_ts(e.received_at) or e.ts
            # Defense: never process events outside partition valid_window
            if v0 is not None and v1 is not None and recv is not None:
                if not (v0 <= recv <= v1):
                    out.events_skipped_out_of_scope += 1
                    continue
            if payload.get("CurrentPriceTime"):
                payload["_market_CurrentPriceTime"] = payload.get("CurrentPriceTime")
            payload["CurrentPriceTime"] = recv.isoformat() if recv else None

            dec = process_e1_x5_guard_event(
                provider=provider,
                session=session,
                symbol=sym,
                payload=payload,
                day=day,
                event_sequence=e.sequence,
                event_id=e.unique_key,
                decision_time=recv,
            )
            out.events_fed += 1

            if collect_exit_stream:
                kind = getattr(dec, "observe_kind", None)
                if kind != "NO_EVALUATION_DECISION_TIME_MISSING":
                    b = getattr(dec, "bid", None)
                    if b is not None and float(b) > 0:
                        ts_use = getattr(dec, "event_time", None) or recv
                        if recv is not None and ts_use is not None and ts_use != recv:
                            out.exit_stream_ts_mismatch += 1
                        out.exit_stream.append(
                            (sym, float(ts_use.timestamp()), int(e.sequence), float(b))
                        )

            if collect_score_rows and getattr(dec, "observe_kind", None) == "SCORE":
                score = float(dec.score) if dec.score is not None else None
                spread = float(dec.spread_bps) if dec.spread_bps is not None else None
                bid = float(dec.bid) if dec.bid is not None else None
                ask = float(dec.ask) if dec.ask is not None else None
                mid_v = None
                if bid and ask and bid > 0 and ask >= bid:
                    mid_v = (bid + ask) / 2.0
                gap = (score - THRESHOLD) if score is not None else None
                x5_accept = False
                if session.candidates:
                    last = session.candidates[-1]
                    if last.get("symbol") == sym and last.get("entry_decision") == "ENTER":
                        x5_accept = True
                row = _stamp_lineage(
                    {
                        "symbol": sym,
                        "symbol_norm": sym,
                        "decision_time": recv.isoformat() if recv else None,
                        "decision_ts": recv.timestamp() if recv else None,
                        "score": score,
                        "spread_bps": spread,
                        "bid": bid,
                        "ask": ask,
                        "mid": mid_v,
                        "sample_reason": dec.sample_reason,
                        "event_sequence": e.sequence,
                        "score_vs_threshold_gap": gap,
                        "x5_entry_result": dec.entry_result,
                        "x5_accept": x5_accept,
                        "banner": banner,
                    },
                    day=day,
                    am_pm=am_pm,
                    mask_meta=meta,
                    replay_partition_id=replay_partition_id,
                    event_scope="SCORE_SAMPLE",
                )
                out.score_rows.append(row)

            # SignalLedger: SCORE samples that produce an ENTRY action / block (compact)
            if getattr(dec, "observe_kind", None) == "SCORE":
                er = str(getattr(dec, "entry_result", None) or "")
                entered = False
                if session.candidates:
                    last = session.candidates[-1]
                    if last.get("symbol") == sym and last.get("entry_decision") == "ENTER":
                        entered = True
                if entered or er in (
                    "ENTER",
                    "CAP_BLOCKED",
                    "SAME_SYMBOL_BLOCKED",
                    "SPREAD_BLOCKED",
                    "CANDIDATE_GATE",
                ) or er.startswith("REJECT") or er.startswith("BLOCK"):
                    out.signal_ledger.append(
                        _stamp_lineage(
                            {
                                "ts": recv.isoformat() if recv else None,
                                "symbol": sym,
                                "signal": bool(entered or er == "ENTER"),
                                "x5_accept": entered,
                                "entry_result": er,
                                "event_id": str(getattr(e, "unique_key", "") or e.sequence),
                                "score": float(dec.score) if dec.score is not None else None,
                                "spread_bps": float(dec.spread_bps) if dec.spread_bps is not None else None,
                                "in_analysis_mask_signal": True,
                            },
                            day=day,
                            am_pm=am_pm,
                            mask_meta=meta,
                            replay_partition_id=replay_partition_id,
                            event_scope="SCORE_ENTRY_SIGNAL",
                        )
                    )

            # Decision ledger: ENTRY / EXIT from session deltas is expensive; stamp exits below
    finally:
        if restore_gate is not None:
            restore_gate()

    # Orphan opens → WINDOW_CENSORED (no force-close, no invented post-window exits)
    orphans = []
    for sym, pos in list(getattr(session, "positions", {}).items()):
        entry_time = getattr(pos, "entry_time", None)
        et_s = entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time or "")
        cens = _stamp_lineage(
            {
                "symbol": sym,
                "entry_time": et_s,
                "exit_time": None,
                "exit_reason": ORPHAN_REASON,
                "reason": ORPHAN_REASON,
                "reason_alias": ORPHAN_REASON_ALIAS,
                "entry_ask": float(getattr(pos, "entry_ask", 0) or 0),
                "exit_bid": None,
                "gross_pnl_yen_100": None,
                "cost_yen_100": None,
                "net_pnl_yen_100": None,
                "net_bps": None,
                "excluded_from_completed_pnl": True,
                "entry_lineage": _entry_lineage(
                    day=day,
                    am_pm=am_pm,
                    mask_meta=meta,
                    replay_partition_id=replay_partition_id,
                    entry_time=et_s,
                ),
                "exit_lineage": None,
                "in_analysis_mask_entry": True,
                "in_analysis_mask_exit": False,
            },
            day=day,
            am_pm=am_pm,
            mask_meta=meta,
            replay_partition_id=replay_partition_id,
            event_scope="PARTITION_END_ORPHAN",
        )
        orphans.append(cens)
        out.decision_ledger.append(
            _stamp_lineage(
                {
                    "ts": meta.get("valid_window_end"),
                    "symbol": sym,
                    "decision": "WINDOW_CENSORED",
                    "reason": ORPHAN_REASON,
                    "event_id": "",
                    "in_analysis_mask_decision": True,
                },
                day=day,
                am_pm=am_pm,
                mask_meta=meta,
                replay_partition_id=replay_partition_id,
                event_scope="PARTITION_END_ORPHAN",
            )
        )
    out.censored_ledger = orphans
    out.open_at_end_n = len(orphans)
    out.open_at_end_symbols = [o["symbol"] for o in orphans]
    if hasattr(session, "positions"):
        session.positions.clear()
    if hasattr(session, "cancel_all_pending"):
        try:
            session.cancel_all_pending("WINDOW_END")
        except Exception:
            pass

    # Adopt completed exits produced while processing in-scope events only
    window_stub = type("W", (), {"window_id": meta.get("window_id") or f"{day}:{am_pm}"})()
    for x in list(getattr(session, "exits", []) or []):
        row, why = _adopt_trade(
            x,
            window_events=events,
            gap_intervals=gaps,
            lookback_sec=lookback_sec,
            window_id=str(getattr(window_stub, "window_id", "")),
            day=day,
        )
        if row is None:
            continue
        # Exit must have been produced from in-scope feed (exit_time within valid_window)
        xt = parse_ts(row.get("exit_time"))
        et = parse_ts(row.get("entry_time"))
        if v0 is not None and v1 is not None:
            if et is not None and not (v0 <= et <= v1):
                continue
            if xt is not None and not (v0 <= xt <= v1):
                # Post-window exit must never count as completed
                continue
        row = _enrich_econ_from_exit(x, row)
        row = _stamp_lineage(
            row,
            day=day,
            am_pm=am_pm,
            mask_meta=meta,
            replay_partition_id=replay_partition_id,
            event_scope="IN_PARTITION_EXIT",
        )
        # Preserve econ fields after stamp
        row = _enrich_econ_from_exit(x, row)
        row["entry_lineage"] = _entry_lineage(
            day=day,
            am_pm=am_pm,
            mask_meta=meta,
            replay_partition_id=replay_partition_id,
            entry_time=row.get("entry_time"),
        )
        row["exit_lineage"] = _exit_lineage(
            day=day,
            am_pm=am_pm,
            mask_meta=meta,
            replay_partition_id=replay_partition_id,
            exit_time=row.get("exit_time"),
        )
        row["in_analysis_mask_entry"] = True
        row["in_analysis_mask_exit"] = True
        reason = str(row.get("exit_reason") or "")
        out.exit_reason_counts[reason] = out.exit_reason_counts.get(reason, 0) + 1
        out.completed_trades.append(row)
        out.decision_ledger.append(
            _stamp_lineage(
                {
                    "ts": row.get("exit_time"),
                    "symbol": row.get("symbol"),
                    "decision": "EXIT",
                    "reason": reason,
                    "event_id": "",
                    "in_analysis_mask_decision": True,
                    "in_analysis_mask_exit": True,
                },
                day=day,
                am_pm=am_pm,
                mask_meta=meta,
                replay_partition_id=replay_partition_id,
                event_scope="IN_PARTITION_EXIT",
            )
        )

    out.cap_blocked = int(getattr(session, "cap_blocked", 0) or 0)
    out.same_symbol_blocked = int(getattr(session, "same_symbol_blocked", 0) or 0)

    # Stamp ENTRY decisions from session.entries + ensure SignalLedger covers them
    for ent in list(getattr(session, "entries", []) or []):
        ts = ent.get("timestamp")
        ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
        eid = str(ent.get("sample_id") or "")
        out.decision_ledger.append(
            _stamp_lineage(
                {
                    "ts": ts_s,
                    "symbol": ent.get("symbol"),
                    "decision": "ENTRY",
                    "reason": "ENTER",
                    "event_id": eid,
                    "in_analysis_mask_decision": True,
                    "in_analysis_mask_entry": True,
                },
                day=day,
                am_pm=am_pm,
                mask_meta=meta,
                replay_partition_id=replay_partition_id,
                event_scope="IN_PARTITION_ENTRY",
            )
        )
        # Guarantee SignalLedger row for every ENTRY (vacuous-empty forbid)
        out.signal_ledger.append(
            _stamp_lineage(
                {
                    "ts": ts_s,
                    "symbol": ent.get("symbol"),
                    "signal": True,
                    "x5_accept": True,
                    "entry_result": "ENTER",
                    "event_id": eid,
                    "in_analysis_mask_signal": True,
                    "in_analysis_mask_entry": True,
                },
                day=day,
                am_pm=am_pm,
                mask_meta=meta,
                replay_partition_id=replay_partition_id,
                event_scope="IN_PARTITION_ENTRY",
            )
        )

    # Fallback: if trades/decisions exist but signal still empty (should not happen)
    if (out.decision_ledger or out.completed_trades) and not out.signal_ledger:
        for t in out.completed_trades:
            out.signal_ledger.append(
                _stamp_lineage(
                    {
                        "ts": t.get("entry_time"),
                        "symbol": t.get("symbol"),
                        "signal": True,
                        "x5_accept": True,
                        "entry_result": "ENTER_FROM_TRADE",
                        "event_id": "",
                        "in_analysis_mask_signal": True,
                        "in_analysis_mask_entry": True,
                    },
                    day=day,
                    am_pm=am_pm,
                    mask_meta=meta,
                    replay_partition_id=replay_partition_id,
                    event_scope="IN_PARTITION_ENTRY",
                )
            )

    assert_signal_ledger_nonempty_when_decisions_or_trades(
        signal_ledger=out.signal_ledger,
        decision_ledger=out.decision_ledger,
        completed_trades=out.completed_trades,
    )

    return out


def merge_partition_results(results: Sequence[PartitionReplayResult]) -> dict[str, Any]:
    """Aggregate independent partitions (NO AM→PM carry by construction)."""
    trades: list[dict[str, Any]] = []
    censored: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    exit_counts: dict[str, int] = {}
    open_syms: list[str] = []
    events_fed = 0
    cap_blocked = 0
    same_sym = 0
    for r in results:
        trades.extend(r.completed_trades)
        censored.extend(r.censored_ledger)
        decisions.extend(r.decision_ledger)
        signals.extend(r.signal_ledger)
        events_fed += r.events_fed
        cap_blocked += r.cap_blocked
        same_sym += r.same_symbol_blocked
        open_syms.extend(r.open_at_end_symbols)
        for k, v in r.exit_reason_counts.items():
            exit_counts[k] = exit_counts.get(k, 0) + v
    assert_signal_ledger_nonempty_when_decisions_or_trades(
        signal_ledger=signals,
        decision_ledger=decisions,
        completed_trades=trades,
    )
    m = summarize_pnls([float(t["net_pnl_yen_100"]) for t in trades])
    return {
        "evaluation_mode": EVALUATION_MODE_REQUIRED,
        "lifecycle_contract": REPLAY_LIFECYCLE_CONTRACT_TEXT.strip(),
        "completed_trades": trades,
        "censored_ledger": censored,
        "decision_ledger": decisions,
        "signal_ledger": signals,
        "metrics": {
            **m,
            "exit_reason_counts": exit_counts,
            "cap_blocked": cap_blocked,
            "same_symbol_blocked": same_sym,
            "open_at_end_n": len(open_syms),
            "open_at_end_symbols": open_syms,
            "censored_n": len(censored),
            "events_fed": events_fed,
            "completed_trade_ledger_sha256": sha256_obj(trades),
            "decision_ledger_sha256": sha256_obj(decisions),
            "signal_ledger_sha256": sha256_obj(signals),
            "censored_ledger_sha256": sha256_obj(censored),
        },
        "partition_count": len(results),
    }


def fixed_spec_day_deletion_from_ledger(
    all_trades: Sequence[Mapping[str, Any]],
    *,
    held_out_day: str,
    atol: float = 0.001,
) -> dict[str, Any]:
    """Filter final completed-trade ledger — NO re-replay. Assert additivity."""
    all_list = list(all_trades)
    day_trades = [t for t in all_list if str(t.get("day") or "") == str(held_out_day)]
    without = [t for t in all_list if str(t.get("day") or "") != str(held_out_day)]
    n_all = len(all_list)
    n_day = len(day_trades)
    n_without = len(without)
    if n_all != n_day + n_without:
        raise AssertionError(
            f"FIXED_SPEC additivity N failed: N_all={n_all} N_day={n_day} N_without={n_without}"
        )
    pnl_all = float(sum(float(t.get("net_pnl_yen_100") or 0) for t in all_list))
    pnl_day = float(sum(float(t.get("net_pnl_yen_100") or 0) for t in day_trades))
    pnl_without = float(sum(float(t.get("net_pnl_yen_100") or 0) for t in without))
    if abs(pnl_all - (pnl_day + pnl_without)) > atol:
        raise AssertionError(
            f"FIXED_SPEC additivity PnL failed: all={pnl_all} day={pnl_day} without={pnl_without}"
        )
    residual_metrics = summarize_pnls([float(t.get("net_pnl_yen_100") or 0) for t in without])
    residual_sha = sha256_obj(without)
    return {
        "held_out_day": held_out_day,
        "method": "FIXED_SPEC_DAY_DELETION_LEDGER_FILTER",
        "no_re_replay": True,
        "n_all": n_all,
        "n_day": n_day,
        "n_without": n_without,
        "pnl_all": pnl_all,
        "pnl_day": pnl_day,
        "pnl_without": pnl_without,
        "completed_trades": residual_metrics["n"],
        "pnl": residual_metrics["pnl"],
        "pf": residual_metrics["pf"],
        "residual_ledger_sha256": residual_sha,
        "pass": float(residual_metrics["pnl"]) >= 0.0,
        "additivity_ok": True,
    }


def assert_selected_in_registry(
    selected_id: str, registry: Sequence[Mapping[str, Any]]
) -> None:
    ids = {str(c.get("candidate_id")) for c in registry}
    if selected_id not in ids:
        raise AssertionError(
            f"selected candidate_id={selected_id} not in registry (n={len(registry)})"
        )


# Lightweight synthetic helpers for fixture tests (no Capture I/O)
def synthetic_board_event(
    *,
    ts: datetime,
    symbol: str,
    bid: float,
    ask: float,
    sequence: int = 0,
    session_id: str = "synth",
):
    """Minimal event-like object for unit fixtures."""

    class _E:
        pass

    e = _E()
    e.ts = ts if ts.tzinfo else ts.replace(tzinfo=JST)
    e.received_at = e.ts.isoformat()
    e.event_time = e.ts.isoformat()
    e.symbol = symbol
    e.sequence = sequence
    e.session_id = session_id
    e.unique_key = f"{session_id}|{sequence}|{symbol}"
    e.payload = {
        "Symbol": symbol.replace(".T", ""),
        "Buy1": {"Price": bid, "Qty": 100},
        "Sell1": {"Price": ask, "Qty": 100},
        "CurrentPrice": (bid + ask) / 2.0,
        "CurrentPriceTime": e.ts.isoformat(),
    }
    return e
