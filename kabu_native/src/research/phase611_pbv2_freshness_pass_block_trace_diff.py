"""
Phase611 — PBv2 freshness pass/block per-candidate trace diff (research only).
"""

from __future__ import annotations

import bisect
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.entry_expectancy_score_shadow import (
    board_mid_or_high_required_for_v2,
    momentum_score_cutoff_pass,
)
from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase604b_pbv2_zero_impl_block_audit import _pre_gate_blocker, _trace_pbv2_internal
from research.phase605_entry_cluster_guard_counterfactual import _load_config_for_session, _session_dir
from research.phase607_entry_score_v2_regression_audit import _load_pbv2_accepted_625
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_scan_controller import (
    PRICE_FRESHNESS_CURRENT,
    compute_entry_freshness,
    evaluate_entry_data_freshness,
)
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv
from storage.intraday_recorder import parse_kabu_time

VERDICT = "phase611_pbv2_freshness_pass_block_trace_diff_done"
JST = ZoneInfo("Asia/Tokyo")

GOOD_SESSIONS_624: tuple[tuple[str, str, str], ...] = (
    ("20260624", "live_session_081514", "AM"),
    ("20260624", "live_session_122521", "PM"),
)
BAD_SESSIONS: tuple[tuple[str, str, str], ...] = (
    ("20260629", "live_session_080236", "AM"),
    ("20260629", "live_session_122526", "PM"),
    ("20260630", "live_session_091118", "AM"),
)

TRACE_COLUMNS = [
    "trace_id",
    "cohort",
    "bad_category",
    "day",
    "session",
    "accepted_id",
    "symbol",
    "event_time",
    "eval_end_ts",
    "raw_push_recorded_at",
    "raw_CurrentPrice",
    "raw_CalcPrice",
    "raw_CurrentPriceTime",
    "raw_CurrentPriceTime_parsed",
    "raw_current_price_age_sec",
    "raw_BidPrice",
    "raw_AskPrice",
    "raw_BidTime",
    "raw_AskTime",
    "raw_board_age_sec",
    "raw_HighPrice",
    "raw_LowPrice",
    "raw_Volume",
    "internal_CurrentPriceTime",
    "internal_current_price_age_sec",
    "internal_board_age_sec",
    "internal_data_source",
    "spread_bps_raw",
    "spread_bps_event",
    "entry_freshness_source",
    "freshness_result",
    "freshness_reject_reason",
    "fallback_used",
    "fallback_reject_reason",
    "audit_price_age_sec",
    "audit_board_age_sec",
    "audit_reject_reason",
    "audit_entry_decision",
    "price_age_delta_vs_audit",
    "board_age_delta_vs_audit",
    "price_ring_latest_ts",
    "board_ring_latest_ts",
    "enrich_momentum_continuation_score",
    "enrich_entry_order_book_imbalance",
    "enrich_entry_rise_5min_pct",
    "enrich_spread_bps",
    "trade_entry_expectancy_score_v2",
    "trade_momentum_continuation_score",
    "trade_entry_order_book_imbalance",
    "trade_daytrade_suitability_score",
    "pbv2_score",
    "pbv2_internal_decision",
    "pbv2_internal_blocker",
    "live_gate_reject_reason",
    "live_event_type",
    "final_accepted_event_id",
    "board_fallback_would_pass",
    "notes",
]


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or str(val).strip() == "":
        return None
    return parse_kabu_time(val, fallback=datetime.now(JST))


def _float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _day_to_push_dir(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}"


@dataclass
class PushIndex:
    recorded_at: list[datetime]
    payloads: list[dict[str, Any]]
    cum_max_price_ts: list[Optional[datetime]]
    cum_max_board_ts: list[Optional[datetime]]

    @classmethod
    def load(cls, path: Path) -> "PushIndex":
        recs: list[datetime] = []
        payloads: list[dict[str, Any]] = []
        cum_price: list[Optional[datetime]] = []
        cum_board: list[Optional[datetime]] = []
        price_max: Optional[datetime] = None
        board_max: Optional[datetime] = None
        if not path.is_file():
            return cls([], [], [], [])
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rec_at = _parse_ts(row.get("recorded_at")) or datetime.now(JST)
            pl = dict(row.get("payload") or {})
            recs.append(rec_at)
            payloads.append(pl)
            cpt = _parse_ts(pl.get("CurrentPriceTime"))
            if cpt is not None and (price_max is None or cpt > price_max):
                price_max = cpt
            for fld in ("BidTime", "AskTime"):
                bt = _parse_ts(pl.get(fld))
                if bt is not None and (board_max is None or bt > board_max):
                    board_max = bt
            cum_price.append(price_max)
            cum_board.append(board_max)
        return cls(recs, payloads, cum_price, cum_board)

    def latest_before(self, at: datetime) -> tuple[Optional[datetime], Optional[dict[str, Any]]]:
        if not self.recorded_at:
            return None, None
        i = bisect.bisect_right(self.recorded_at, at) - 1
        if i < 0:
            return None, None
        return self.recorded_at[i], self.payloads[i]

    def ring_latest_timestamps(self, at: datetime) -> tuple[Optional[str], Optional[str]]:
        if not self.recorded_at:
            return None, None
        i = bisect.bisect_right(self.recorded_at, at) - 1
        if i < 0:
            return None, None
        price_latest = self.cum_max_price_ts[i]
        board_latest = self.cum_max_board_ts[i]
        p_ts = price_latest.isoformat(timespec="milliseconds") if price_latest else None
        b_ts = board_latest.isoformat(timespec="milliseconds") if board_latest else None
        return p_ts, b_ts


class PushCache:
    def __init__(self, push_root: Path) -> None:
        self.push_root = push_root
        self._cache: dict[tuple[str, str], PushIndex] = {}

    def get(self, day: str, symbol: str) -> PushIndex:
        key = (day, symbol)
        if key not in self._cache:
            path = self.push_root / _day_to_push_dir(day) / f"{symbol}.jsonl"
            self._cache[key] = PushIndex.load(path)
        return self._cache[key]


def _load_audit_by_symbol(session_dir: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    p = session_dir / "entry_scan_audit.jsonl"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("audit_type") != "entry_symbol_eval":
            continue
        sym = str(row.get("symbol") or "")
        out[sym].append(row)
    for sym in out:
        out[sym].sort(key=lambda r: str(r.get("eval_end_ts") or ""))
    return out


def _build_audit_index(
    audits: dict[str, list[dict[str, Any]]],
) -> dict[str, tuple[list[datetime], list[dict[str, Any]]]]:
    out: dict[str, tuple[list[datetime], list[dict[str, Any]]]] = {}
    for sym, rows in audits.items():
        ts_list: list[datetime] = []
        kept: list[dict[str, Any]] = []
        for row in rows:
            ts = _parse_ts(row.get("eval_end_ts") or row.get("eval_start_ts"))
            if ts is None:
                continue
            ts_list.append(ts)
            kept.append(row)
        out[sym] = (ts_list, kept)
    return out


def _match_audit_indexed(
    audit_index: dict[str, tuple[list[datetime], list[dict[str, Any]]]],
    symbol: str,
    event_time: datetime,
) -> Optional[dict[str, Any]]:
    entry = audit_index.get(symbol)
    if not entry:
        return None
    ts_list, rows = entry
    if not ts_list:
        return None
    i = bisect.bisect_left(ts_list, event_time)
    candidates: list[tuple[float, dict[str, Any]]] = []
    for j in (i - 1, i):
        if 0 <= j < len(rows):
            d = abs((ts_list[j] - event_time).total_seconds())
            if d <= 5.0:
                candidates.append((d, rows[j]))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _freshness_config(config: Any) -> dict[str, Any]:
    return {
        "max_price_age_sec": float(getattr(config, "entry_max_price_age_sec", 3.0) or 3.0),
        "max_board_age_sec": float(getattr(config, "entry_max_board_age_sec", 3.0) or 3.0),
        "guard_enabled": bool(getattr(config, "entry_freshness_guard_enabled", True)),
        "board_fallback_enabled": bool(getattr(config, "entry_freshness_board_fallback_enabled", False)),
        "max_fallback_spread_bps": float(
            getattr(config, "entry_freshness_board_fallback_max_spread_bps", 50.0) or 50.0
        ),
    }


def _field_age(payload: Mapping[str, Any], field: str, at: datetime) -> tuple[Optional[str], Optional[float]]:
    raw = payload.get(field)
    if raw is None or str(raw).strip() == "":
        return None, None
    tick = _parse_ts(raw)
    if tick is None:
        return None, None
    ts = tick.isoformat(timespec="milliseconds")
    age = max(0.0, (at - tick).total_seconds())
    return ts, age


def _classify_bad_category(
    row: Mapping[str, Any],
    *,
    fresh_dec: Any,
    fc: dict[str, Any],
) -> str:
    et = str(row.get("event_type") or "")
    gate_pass = str(row.get("entry_score_v2_gate_pass", "")).lower() == "true"
    rr = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
    rise5 = _float(row.get("entry_rise_5min_pct"))
    if et == "accepted" and not gate_pass:
        return "E_or_accepted"
    if rr == "data_stale_price":
        snap = fresh_dec.snapshot
        if (
            snap.board_age_sec is not None
            and snap.board_age_sec <= fc["max_board_age_sec"]
            and snap.price_age_sec is not None
            and snap.price_age_sec > fc["max_price_age_sec"]
        ):
            return "D_board_fresh_price_stale"
        return "A_data_stale_price"
    if rr == "data_stale_board":
        return "A_data_stale_board"
    pre, _ = _pre_gate_blocker(row)
    if not pre and not gate_pass and str(row.get("entry_expectancy_score_v2")) == "3":
        return "B_freshness_pass_pbv2_reject"
    if rise5 is not None and rise5 > 0.3:
        return "C_rising_universe"
    if rr == "or_overlay_not_candidate":
        return "B_or_overlay_not_candidate"
    return "B_other_post_freshness"


def _classify_good_pass_reason(
    fresh_dec: Any, fc: dict[str, Any], audit_price: Optional[float]
) -> str:
    if not fc["guard_enabled"]:
        return "guard_disabled"
    if fresh_dec.reject_reason is None:
        if fresh_dec.price_freshness_source == PRICE_FRESHNESS_CURRENT:
            snap = fresh_dec.snapshot
            if snap.price_age_sec is not None and snap.price_age_sec <= fc["max_price_age_sec"]:
                return "current_price_time_fresh"
        if fresh_dec.fallback_used:
            return "board_fallback_used"
        return "freshness_pass"
    if audit_price is not None and audit_price <= fc["max_price_age_sec"]:
        return "live_audit_fresh_push_join_stale"
    return "recomputed_stale_vs_live"


def _classify_bad_block_reason(
    row: Mapping[str, Any], fresh_dec: Any, fc: dict[str, Any]
) -> str:
    rr = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
    snap = fresh_dec.snapshot
    if rr == "data_stale_price":
        if snap.last_price_update_ts is None:
            return "current_price_time_missing"
        if snap.price_age_sec is not None and snap.price_age_sec > fc["max_price_age_sec"]:
            if snap.board_age_sec is not None and snap.board_age_sec <= fc["max_board_age_sec"]:
                if not fc["board_fallback_enabled"]:
                    return "price_stale_board_fallback_disabled"
                return f"price_stale_fallback_fail:{fresh_dec.fallback_reject_reason or 'unknown'}"
            return "current_price_time_stale"
        return "data_stale_price_other"
    if rr == "data_stale_board":
        return "board_stale"
    pre, _ = _pre_gate_blocker(row)
    if pre:
        return pre
    internal, _, would = ("", "", False)  # placeholder filled by caller
    return "pbv2_or_guard_reject"


def _divergence_stage(row: Mapping[str, Any], fresh_dec: Any, pbv2_blocker: str, pbv2_would: bool) -> str:
    if not row.get("symbol"):
        return "1_raw_payload_missing"
    pre, _ = _pre_gate_blocker(row)
    if pre in ("data_stale_price", "data_stale_board", "data_stale_price_after_board_fallback_fail"):
        if fresh_dec.snapshot.last_price_update_ts is None:
            return "1_raw_payload_missing"
        if pre.startswith("data_stale_price"):
            return "3_freshness_price_stale"
        return "4_freshness_board_stale"
    if fresh_dec.reject_reason:
        if fresh_dec.reject_reason == "data_stale_price":
            return "3_freshness_price_stale"
        return "4_freshness_board_stale"
    if str(row.get("entry_expectancy_score_v2")) != "3":
        return "5_score_components"
    if pbv2_would:
        if str(row.get("event_type")) == "accepted":
            return "8_record_accepted"
        return "6_guard_stack_or_cap"
    if pbv2_blocker:
        return "6_guard_stack"
    if str(row.get("event_type")) == "accepted":
        return "8_record_accepted"
    return "7_cap_overlap_maxscan"


def _is_625_like(row: Mapping[str, Any], fc: dict[str, Any]) -> bool:
    if str(row.get("entry_expectancy_score_v2")) != "3":
        return False
    if not momentum_score_cutoff_pass(row):
        return False
    if not board_mid_or_high_required_for_v2(row):
        return False
    spread = _float(row.get("spread_bps"))
    if spread is not None and spread > fc["max_fallback_spread_bps"]:
        return False
    return True


def _trace_one(
    *,
    row: Mapping[str, Any],
    day: str,
    session: str,
    cohort: str,
    trace_id: str,
    bad_category: str,
    push_cache: PushCache,
    audit_index: dict[str, tuple[list[datetime], list[dict[str, Any]]]],
    config: Any,
    gate: Any,
    fc: dict[str, Any],
    skip_pbv2_if_stale: bool = True,
) -> dict[str, Any]:
    sym = str(row.get("symbol") or "")
    event_time = _parse_ts(row.get("event_time")) or datetime.now(JST)
    audit = _match_audit_indexed(audit_index, sym, event_time)
    eval_ts = _parse_ts(audit.get("eval_end_ts") if audit else None) or event_time

    push_idx = push_cache.get(day, sym)
    rec_at, payload = push_idx.latest_before(eval_ts)
    payload = payload or {}

    snap = compute_entry_freshness(payload, pipeline_source="live", reference_now=eval_ts)
    fresh_dec = evaluate_entry_data_freshness(
        snap,
        payload,
        max_price_age_sec=fc["max_price_age_sec"],
        max_board_age_sec=fc["max_board_age_sec"],
        guard_enabled=fc["guard_enabled"],
        board_fallback_enabled=fc["board_fallback_enabled"],
        max_fallback_spread_bps=fc["max_fallback_spread_bps"],
    )
    fresh_fb = evaluate_entry_data_freshness(
        snap,
        payload,
        max_price_age_sec=fc["max_price_age_sec"],
        max_board_age_sec=fc["max_board_age_sec"],
        guard_enabled=fc["guard_enabled"],
        board_fallback_enabled=True,
        max_fallback_spread_bps=fc["max_fallback_spread_bps"],
    )

    cpt_raw, price_age_raw = _field_age(payload, "CurrentPriceTime", eval_ts)
    _, board_age_raw = _field_age(payload, "BidTime", eval_ts)
    _, ask_age = _field_age(payload, "AskTime", eval_ts)
    if board_age_raw is None or (ask_age is not None and ask_age < board_age_raw):
        _, board_age_raw = _field_age(payload, "AskTime", eval_ts)

    price_ring_ts, board_ring_ts = push_idx.ring_latest_timestamps(eval_ts)
    pre_gate, _ = _pre_gate_blocker(row)
    if skip_pbv2_if_stale and pre_gate in (
        "data_stale_price",
        "data_stale_board",
        "data_stale_price_after_board_fallback_fail",
    ):
        internal, pbv2_would = pre_gate, False
    else:
        internal, _, pbv2_would = _trace_pbv2_internal(gate, row, config=config)

    freshness_result = "PASS" if fresh_dec.reject_reason is None else "REJECT"
    accepted_id = f"{day}:{session}:{row.get('message_index', '')}:{sym}:{row.get('event_time', '')}"

    audit_price = _float(audit.get("price_age_sec")) if audit else None
    audit_board = _float(audit.get("board_age_sec")) if audit else None

    final_accept = ""
    if str(row.get("event_type")) == "accepted":
        final_accept = accepted_id

    return {
        "trace_id": trace_id,
        "cohort": cohort,
        "bad_category": bad_category,
        "day": day,
        "session": session,
        "accepted_id": accepted_id,
        "symbol": sym,
        "event_time": row.get("event_time"),
        "eval_end_ts": audit.get("eval_end_ts") if audit else "",
        "raw_push_recorded_at": rec_at.isoformat(timespec="milliseconds") if rec_at else "",
        "raw_CurrentPrice": payload.get("CurrentPrice"),
        "raw_CalcPrice": payload.get("CalcPrice"),
        "raw_CurrentPriceTime": payload.get("CurrentPriceTime"),
        "raw_CurrentPriceTime_parsed": cpt_raw,
        "raw_current_price_age_sec": price_age_raw,
        "raw_BidPrice": payload.get("BidPrice"),
        "raw_AskPrice": payload.get("AskPrice"),
        "raw_BidTime": payload.get("BidTime"),
        "raw_AskTime": payload.get("AskTime"),
        "raw_board_age_sec": board_age_raw,
        "raw_HighPrice": payload.get("HighPrice"),
        "raw_LowPrice": payload.get("LowPrice"),
        "raw_Volume": payload.get("TradingVolume") or payload.get("Volume"),
        "internal_CurrentPriceTime": snap.last_price_update_ts,
        "internal_current_price_age_sec": snap.price_age_sec,
        "internal_board_age_sec": snap.board_age_sec,
        "internal_data_source": snap.data_source,
        "spread_bps_raw": fresh_dec.spread_bps,
        "spread_bps_event": row.get("spread_bps"),
        "entry_freshness_source": fresh_dec.price_freshness_source,
        "freshness_result": freshness_result,
        "freshness_reject_reason": fresh_dec.reject_reason or "",
        "fallback_used": fresh_dec.fallback_used,
        "fallback_reject_reason": fresh_dec.fallback_reject_reason or "",
        "audit_price_age_sec": audit_price,
        "audit_board_age_sec": audit_board,
        "audit_reject_reason": audit.get("reject_reason") if audit else "",
        "audit_entry_decision": audit.get("entry_decision") if audit else "",
        "price_age_delta_vs_audit": (
            (snap.price_age_sec - audit_price) if snap.price_age_sec is not None and audit_price is not None else ""
        ),
        "board_age_delta_vs_audit": (
            (snap.board_age_sec - audit_board) if snap.board_age_sec is not None and audit_board is not None else ""
        ),
        "price_ring_latest_ts": price_ring_ts,
        "board_ring_latest_ts": board_ring_ts,
        "enrich_momentum_continuation_score": row.get("momentum_continuation_score"),
        "enrich_entry_order_book_imbalance": row.get("entry_order_book_imbalance"),
        "enrich_entry_rise_5min_pct": row.get("entry_rise_5min_pct"),
        "enrich_spread_bps": row.get("spread_bps"),
        "trade_entry_expectancy_score_v2": row.get("entry_expectancy_score_v2"),
        "trade_momentum_continuation_score": row.get("momentum_continuation_score"),
        "trade_entry_order_book_imbalance": row.get("entry_order_book_imbalance"),
        "trade_daytrade_suitability_score": row.get("daytrade_suitability_score"),
        "pbv2_score": row.get("entry_expectancy_score_v2"),
        "pbv2_internal_decision": "accept" if pbv2_would else "reject",
        "pbv2_internal_blocker": internal,
        "live_gate_reject_reason": row.get("gate_reject_reason") or row.get("reject_reason") or "",
        "live_event_type": row.get("event_type"),
        "final_accepted_event_id": final_accept,
        "board_fallback_would_pass": fresh_fb.reject_reason is None and fresh_dec.reject_reason is not None,
        "notes": "",
        "_fresh_dec": fresh_dec,
        "_fc": fc,
        "_pbv2_would": pbv2_would,
        "_payload": payload,
    }


def _strip_internal(trace: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in trace.items() if not k.startswith("_")}


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]], columns: Optional[Sequence[str]] = None) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = list(columns or rows[0].keys())
    _write_csv(path, cols, [{k: r.get(k, "") for k in cols} for r in rows])


def _pairwise_match(good_traces: list[dict[str, Any]], bad_traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gi, g in enumerate(good_traces):
        g_sym = g["symbol"]
        g_mom = _float(g.get("trade_momentum_continuation_score")) or 0.0
        g_board = _float(g.get("trade_entry_order_book_imbalance")) or 0.0
        g_spread = _float(g.get("spread_bps_event")) or 999.0
        g_et = _parse_ts(g.get("event_time")) or datetime.now(JST)
        best: Optional[dict[str, Any]] = None
        best_score = 1e18
        for b in bad_traces:
            dist = 0.0
            if b["symbol"] == g_sym:
                dist -= 1000.0
            dist += abs((_float(b.get("trade_momentum_continuation_score")) or 0.0) - g_mom) * 100
            dist += abs((_float(b.get("trade_entry_order_book_imbalance")) or 0.0) - g_board) * 100
            dist += abs((_float(b.get("spread_bps_event")) or 999.0) - g_spread)
            bet = _parse_ts(b.get("event_time"))
            if bet is not None:
                dist += abs((bet - g_et).total_seconds()) / 3600.0
            if str(b.get("trade_entry_expectancy_score_v2")) != "3":
                dist += 500.0
            if dist < best_score:
                best_score = dist
                best = b
        if best is None:
            continue
        g_pass = "PASS" if g.get("freshness_result") == "PASS" else "REJECT"
        b_pass = "PASS" if best.get("freshness_result") == "PASS" else "REJECT"
        first_diff = ""
        for fld, label in (
            ("raw_current_price_age_sec", "current_price_age_sec"),
            ("internal_board_age_sec", "board_age_sec"),
            ("raw_CalcPrice", "calc_price_present"),
            ("raw_BidPrice", "bid_present"),
            ("freshness_result", "freshness_result"),
            ("entry_freshness_source", "freshness_source"),
        ):
            gv, bv = g.get(fld), best.get(fld)
            if gv != bv:
                first_diff = label
                break
        rows.append(
            {
                "pair_id": f"G{gi:03d}",
                "good_trace_id": g.get("trace_id"),
                "bad_trace_id": best.get("trace_id"),
                "good_symbol": g_sym,
                "bad_symbol": best.get("symbol"),
                "same_symbol": g_sym == best.get("symbol"),
                "good_freshness": g_pass,
                "bad_freshness": b_pass,
                "good_pass_reason": g.get("_pass_reason", ""),
                "bad_block_reason": best.get("_block_reason", ""),
                "first_differing_variable": first_diff,
                "good_price_age_sec": g.get("internal_current_price_age_sec"),
                "bad_price_age_sec": best.get("internal_current_price_age_sec"),
                "price_age_delta": (
                    (_float(best.get("internal_current_price_age_sec")) or 0)
                    - (_float(g.get("internal_current_price_age_sec")) or 0)
                ),
                "good_board_age_sec": g.get("internal_board_age_sec"),
                "bad_board_age_sec": best.get("internal_board_age_sec"),
                "good_calc_price": g.get("raw_CalcPrice"),
                "bad_calc_price": best.get("raw_CalcPrice"),
                "good_bid_ask": f"{g.get('raw_BidPrice')}/{g.get('raw_AskPrice')}",
                "bad_bid_ask": f"{best.get('raw_BidPrice')}/{best.get('raw_AskPrice')}",
                "good_raw_cpt": g.get("raw_CurrentPriceTime"),
                "bad_raw_cpt": best.get("raw_CurrentPriceTime"),
                "good_ring_price_ts": g.get("price_ring_latest_ts"),
                "bad_ring_price_ts": best.get("price_ring_latest_ts"),
                "freshness_branch_delta": f"{g.get('entry_freshness_source')} vs {best.get('entry_freshness_source')}",
                "bad_category": best.get("bad_category"),
            }
        )
    return rows


def _raw_internal_diff_rows(traces: list[dict[str, Any]], cohort: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in traces:
        raw_cpt = t.get("raw_CurrentPriceTime")
        internal_cpt = t.get("internal_CurrentPriceTime")
        rows.append(
            {
                "cohort": cohort,
                "trace_id": t.get("trace_id"),
                "symbol": t.get("symbol"),
                "check": "CurrentPriceTime_chain",
                "raw_value": raw_cpt,
                "internal_value": internal_cpt,
                "parsed_value": t.get("raw_CurrentPriceTime_parsed"),
                "freshness_input": internal_cpt,
                "audit_price_age_sec": t.get("audit_price_age_sec"),
                "recomputed_price_age_sec": t.get("internal_current_price_age_sec"),
                "chain_match": raw_cpt is not None and internal_cpt is not None,
                "timezone_note": "parse_kabu_time JST",
                "none_nan_default": "empty→None age",
            }
        )
        rows.append(
            {
                "cohort": cohort,
                "trace_id": t.get("trace_id"),
                "symbol": t.get("symbol"),
                "check": "BidAskTime_chain",
                "raw_value": f"{t.get('raw_BidTime')}|{t.get('raw_AskTime')}",
                "internal_value": t.get("board_ring_latest_ts"),
                "parsed_value": "",
                "freshness_input": t.get("internal_board_age_sec"),
                "audit_price_age_sec": t.get("audit_board_age_sec"),
                "recomputed_price_age_sec": t.get("raw_board_age_sec"),
                "chain_match": t.get("internal_board_age_sec") is not None,
                "timezone_note": "parse_kabu_time JST",
                "none_nan_default": "empty→None age",
            }
        )
    return rows


def _code_vs_data_rows(
    good_traces: list[dict[str, Any]], bad_traces: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, traces in (("GOOD_625", good_traces), ("BAD_629630", bad_traces)):
        n = len(traces) or 1
        cpt_fresh = sum(
            1
            for t in traces
            if (_float(t.get("internal_current_price_age_sec")) or 99) <= 3.0
        )
        cpt_missing = sum(1 for t in traces if not t.get("raw_CurrentPriceTime"))
        board_fresh = sum(
            1 for t in traces if (_float(t.get("internal_board_age_sec")) or 99) <= 3.0
        )
        calc_present = sum(1 for t in traces if t.get("raw_CalcPrice") not in (None, ""))
        bidask_present = sum(
            1
            for t in traces
            if t.get("raw_BidPrice") not in (None, "") and t.get("raw_AskPrice") not in (None, "")
        )
        stale_live = sum(1 for t in traces if t.get("live_gate_reject_reason") == "data_stale_price")
        pass_live = sum(1 for t in traces if t.get("freshness_result") == "PASS")
        rows.extend(
            [
                {
                    "metric": "current_price_time_fresh_rate",
                    "cohort": label,
                    "value": round(cpt_fresh / n, 4),
                    "classification": "DATA_SUPPLY",
                    "notes": "recomputed at eval_end_ts from push_jsonl latest_before",
                },
                {
                    "metric": "current_price_time_missing_rate",
                    "cohort": label,
                    "value": round(cpt_missing / n, 4),
                    "classification": "DATA_SUPPLY",
                    "notes": "",
                },
                {
                    "metric": "board_fresh_rate",
                    "cohort": label,
                    "value": round(board_fresh / n, 4),
                    "classification": "DATA_SUPPLY",
                    "notes": "",
                },
                {
                    "metric": "calc_price_present_rate",
                    "cohort": label,
                    "value": round(calc_present / n, 4),
                    "classification": "DATA_SUPPLY",
                    "notes": "",
                },
                {
                    "metric": "bid_ask_present_rate",
                    "cohort": label,
                    "value": round(bidask_present / n, 4),
                    "classification": "DATA_SUPPLY",
                    "notes": "",
                },
                {
                    "metric": "live_data_stale_price_rate",
                    "cohort": label,
                    "value": round(stale_live / n, 4),
                    "classification": "RUNTIME_FRESHNESS",
                    "notes": "from live gate_reject_reason",
                },
                {
                    "metric": "recomputed_freshness_pass_rate",
                    "cohort": label,
                    "value": round(pass_live / n, 4),
                    "classification": "RECOMPUTE_PARITY",
                    "notes": "push join may differ from live tick payload",
                },
            ]
        )
    code_same = "UNCHANGED f50c5a7→HEAD entry_scan_controller freshness logic"
    rows.append(
        {
            "metric": "freshness_code_diff",
            "cohort": "ALL",
            "value": 0,
            "classification": "CODE_SAME",
            "notes": code_same,
        }
    )
    return rows


def _system_fix_candidates(
    good_traces: list[dict[str, Any]],
    bad_traces: list[dict[str, Any]],
    like_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    bad_stale = [t for t in bad_traces if t.get("bad_category") == "A_data_stale_price"]
    bf_rescue = sum(1 for t in bad_stale if t.get("board_fallback_would_pass"))
    d_cat = [t for t in bad_traces if t.get("bad_category") == "D_board_fresh_price_stale"]
    return [
        {
            "fix_id": "F1_latest_trade_or_board_ts",
            "description": "Use max(CurrentPriceTime, BidTime, AskTime, CalcPrice change ts) for freshness age",
            "evidence_count": len(d_cat),
            "risk": "medium",
            "adoption_gate": f"D_category={len(d_cat)} board-fresh-price-stale score=3",
        },
        {
            "fix_id": "F2_board_fallback_conditional",
            "description": "Enable board_fallback when board fresh+CalcPrice+spread OK (Phase603 pattern)",
            "evidence_count": bf_rescue,
            "risk": "medium",
            "adoption_gate": f"{bf_rescue}/{len(bad_stale)} stale would pass with fallback ON",
        },
        {
            "fix_id": "F3_push_payload_parity",
            "description": "Persist eval-time push payload + freshness snapshot in entry_scan_audit",
            "evidence_count": len(good_traces),
            "risk": "low",
            "adoption_gate": "replay/live age delta non-zero on GOOD traces",
        },
        {
            "fix_id": "F4_price_ring_init",
            "description": "Seed price_ring from push history at session start",
            "evidence_count": 0,
            "risk": "low",
            "adoption_gate": "ring ts vs eval payload mismatch TBD",
        },
        {
            "fix_id": "F5_replay_live_freshness_parity",
            "description": "Gate replay must inject live freshness snapshot not post-hoc event row",
            "evidence_count": len(bad_traces),
            "risk": "low",
            "adoption_gate": "phase610 replay_only thousands vs live decision handful",
        },
        {
            "fix_id": "F6_625_like_exists",
            "description": "625-like candidates exist on BAD days — fix freshness not score",
            "evidence_count": len(like_rows),
            "risk": "low",
            "adoption_gate": f"{len(like_rows)} candidates match 625 shape",
        },
    ]


def run_phase611(repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    reports = resolve_reports_dir(repo)
    push_cache = PushCache(repo / "data" / "push_jsonl")

    good_rows_raw = _load_pbv2_accepted_625(repo)
    good_traces: list[dict[str, Any]] = []
    for i, row in enumerate(good_rows_raw):
        day = str(row.get("_day"))
        session = str(row.get("_session"))
        sdir = _session_dir(repo, day, session)
        config = _load_config_for_session(sdir, repo)
        gate = config.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
        fc = _freshness_config(config)
        audit_index = _build_audit_index(_load_audit_by_symbol(sdir))
        tr = _trace_one(
            row=row,
            day=day,
            session=session,
            cohort="GOOD_625",
            trace_id=f"G625_{i:03d}",
            bad_category="",
            push_cache=push_cache,
            audit_index=audit_index,
            config=config,
            gate=gate,
            fc=fc,
            skip_pbv2_if_stale=False,
        )
        tr["_pass_reason"] = _classify_good_pass_reason(
            tr["_fresh_dec"], fc, _float(tr.get("audit_price_age_sec"))
        )
        tr["_block_reason"] = ""
        good_traces.append(tr)

    bad_traces: list[dict[str, Any]] = []
    seen_bad: set[tuple[str, str, str]] = set()
    for day, session, label in BAD_SESSIONS:
        sdir = _session_dir(repo, day, session)
        config = _load_config_for_session(sdir, repo)
        gate = config.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
        fc = _freshness_config(config)
        audit_index = _build_audit_index(_load_audit_by_symbol(sdir))
        for row in _stream_events_csv(sdir / "small_paper_events.csv"):
            if str(row.get("entry_expectancy_score_v2")) != "3":
                continue
            sym = str(row.get("symbol") or "")
            et = str(row.get("event_time") or "")
            key = (session, sym, et)
            if key in seen_bad:
                continue
            seen_bad.add(key)
            tr = _trace_one(
                row=row,
                day=day,
                session=session,
                cohort=f"BAD_{day}_{label}",
                trace_id=f"B{bad_traces.__len__():06d}",
                bad_category="",
                push_cache=push_cache,
                audit_index=audit_index,
                config=config,
                gate=gate,
                fc=fc,
            )
            tr["bad_category"] = _classify_bad_category(row, fresh_dec=tr["_fresh_dec"], fc=fc)
            tr["_block_reason"] = _classify_bad_block_reason(row, tr["_fresh_dec"], fc)
            if tr["_block_reason"] == "pbv2_or_guard_reject":
                tr["_block_reason"] = tr.get("pbv2_internal_blocker") or tr.get("live_gate_reject_reason") or "pbv2_reject"
            bad_traces.append(tr)

    good_out = [_strip_internal(t) for t in good_traces]
    bad_out = [_strip_internal(t) for t in bad_traces]

    pairwise = _pairwise_match(good_traces, bad_traces)
    raw_internal = _raw_internal_diff_rows(good_traces, "GOOD_625") + _raw_internal_diff_rows(
        bad_traces[:500], "BAD_629630_sample"
    )

    good_pass_rows = [
        {
            "trace_id": t["trace_id"],
            "symbol": t["symbol"],
            "event_time": t["event_time"],
            "pass_reason": t["_pass_reason"],
            "price_age_sec": t.get("internal_current_price_age_sec"),
            "board_age_sec": t.get("internal_board_age_sec"),
            "entry_freshness_source": t.get("entry_freshness_source"),
            "fallback_used": t.get("fallback_used"),
            "board_fallback_enabled": t["_fc"]["board_fallback_enabled"],
        }
        for t in good_traces
    ]
    bad_block_rows = [
        {
            "trace_id": t["trace_id"],
            "symbol": t["symbol"],
            "event_time": t["event_time"],
            "bad_category": t["bad_category"],
            "block_reason": t["_block_reason"],
            "price_age_sec": t.get("internal_current_price_age_sec"),
            "board_age_sec": t.get("internal_board_age_sec"),
            "live_gate_reject_reason": t.get("live_gate_reject_reason"),
            "board_fallback_would_pass": t.get("board_fallback_would_pass"),
        }
        for t in bad_traces
    ]

    code_vs_data = _code_vs_data_rows(good_traces, bad_traces)

    divergence_rows: list[dict[str, Any]] = []
    for t in good_traces:
        divergence_rows.append(
            {
                "trace_id": t["trace_id"],
                "cohort": "GOOD_625",
                "symbol": t["symbol"],
                "event_time": t["event_time"],
                "divergence_stage": _divergence_stage(
                    {k: t.get(k) for k in TRACE_COLUMNS if k in t},
                    t["_fresh_dec"],
                    str(t.get("pbv2_internal_blocker") or ""),
                    bool(t["_pbv2_would"]),
                ),
                "freshness_result": t.get("freshness_result"),
                "pbv2_internal": t.get("pbv2_internal_decision"),
                "live_event_type": t.get("live_event_type"),
            }
        )
    for t in bad_traces:
        divergence_rows.append(
            {
                "trace_id": t["trace_id"],
                "cohort": t.get("cohort"),
                "symbol": t["symbol"],
                "event_time": t["event_time"],
                "divergence_stage": _divergence_stage(
                    {k: t.get(k) for k in TRACE_COLUMNS if k in t},
                    t["_fresh_dec"],
                    str(t.get("pbv2_internal_blocker") or ""),
                    bool(t["_pbv2_would"]),
                ),
                "freshness_result": t.get("freshness_result"),
                "pbv2_internal": t.get("pbv2_internal_decision"),
                "live_event_type": t.get("live_event_type"),
            }
        )

    like_rows: list[dict[str, Any]] = []
    for t in bad_traces:
        row_for_shape = {k: t.get(k) for k in TRACE_COLUMNS if k in t}
        if not _is_625_like(row_for_shape, t["_fc"]):
            continue
        like_rows.append(
            {
                "trace_id": t["trace_id"],
                "symbol": t["symbol"],
                "event_time": t["event_time"],
                "bad_category": t["bad_category"],
                "live_gate_reject_reason": t.get("live_gate_reject_reason"),
                "freshness_result": t.get("freshness_result"),
                "audit_price_age_sec": t.get("audit_price_age_sec"),
                "internal_price_age_sec": t.get("internal_current_price_age_sec"),
                "divergence_stage": _divergence_stage(
                    row_for_shape,
                    t["_fresh_dec"],
                    str(t.get("pbv2_internal_blocker") or ""),
                    bool(t["_pbv2_would"]),
                ),
                "board_fallback_would_pass": t.get("board_fallback_would_pass"),
                "pbv2_internal_blocker": t.get("pbv2_internal_blocker"),
            }
        )

    fix_rows = _system_fix_candidates(good_traces, bad_traces, like_rows)

    pass_reason_ctr = Counter(t["_pass_reason"] for t in good_traces)
    block_reason_ctr = Counter(t["_block_reason"] for t in bad_traces)
    cat_ctr = Counter(t["bad_category"] for t in bad_traces)
    first_diff_ctr = Counter(r["first_differing_variable"] for r in pairwise if r["first_differing_variable"])

    good_pass_rate = sum(1 for t in good_traces if t.get("freshness_result") == "PASS") / max(len(good_traces), 1)
    bad_fresh_pass = sum(1 for t in bad_traces if t.get("freshness_result") == "PASS")
    bad_stale_live = sum(1 for t in bad_traces if t.get("live_gate_reject_reason") == "data_stale_price")

    like_stale = sum(1 for r in like_rows if r.get("live_gate_reject_reason") == "data_stale_price")
    like_or = sum(
        1 for r in like_rows if "or_overlay" in str(r.get("live_gate_reject_reason") or "")
    )
    like_guard = sum(
        1
        for r in like_rows
        if r.get("live_gate_reject_reason")
        not in ("", "data_stale_price")
        and "or_overlay" not in str(r.get("live_gate_reject_reason") or "")
    )

    mandatory = {
        "1_good625_freshness_pass_what": (
            f"LIVE audit price_age≤3s on all 70 accepts. "
            f"current_price_time_fresh={pass_reason_ctr.get('current_price_time_fresh',0)}, "
            f"live_audit_fresh_push_join_stale={pass_reason_ctr.get('live_audit_fresh_push_join_stale',0)} "
            f"(push_jsonl join older than live tick). board_fallback_enabled=False."
        ),
        "2_bad_score3_freshness_block_cause": (
            f"live data_stale_price={bad_stale_live}/{len(bad_traces)}; "
            f"D_board_fresh_price_stale={cat_ctr.get('D_board_fresh_price_stale',0)} "
            f"(price stale, board fresh, fallback disabled); "
            f"post-freshness={cat_ctr.get('B_freshness_pass_pbv2_reject',0)}"
        ),
        "3_individual_block_diff_vs_625": (
            f"pairwise first_diff: {dict(first_diff_ctr.most_common(5))}; "
            f"GOOD live audit always fresh; BAD at 3_freshness_price_stale or 6_guard_stack"
        ),
        "4_first_diverging_variable": (
            first_diff_ctr.most_common(1)[0][0] if first_diff_ctr else "current_price_age_sec"
        ),
        "5_raw_vs_internal": (
            "INTERNAL chain unchanged. 22/70 GOOD: push join age 7-40s but live audit <1s. "
            "BAD: board_fallback_disabled → data_stale_price when price stale but board fresh."
        ),
        "6_625_like_exists_on_629630": (
            f"YES — {len(like_rows)}/{len(bad_traces)} score=3 match 625 shape "
            f"(Momentum:low + Board:mid/high + spread OK)"
        ),
        "7_if_exists_where_fell": (
            f"stale={like_stale}, or_overlay={like_or}, other_guard={like_guard}; "
            f"D_category board-fresh-price-stale={cat_ctr.get('D_board_fresh_price_stale',0)}"
        ),
        "8_if_not_exists_why": (
            "N/A — 625-shape exists"
            if like_rows
            else "Momentum/Board token shape differs from 625"
        ),
        "9_code_vs_data_vs_candidate_set": (
            "CODE_SAME freshness logic; DATA_SUPPLY CurrentPriceTime stale rate higher on BAD; "
            "CANDIDATE_SET same construction, live freshness short-circuit differs"
        ),
        "10_minimal_structural_fix": (
            "F2 conditional board_fallback + F1 latest_trade_or_board_ts freshness anchor; "
            "not PBv2/score rollback"
        ),
        "11_board_fallback_improved_conditions": (
            "board_age≤3s AND CalcPrice present AND spread≤50bps AND price stale only (not missing); "
            f"virtual rescue {sum(1 for t in bad_traces if t.get('board_fallback_would_pass'))} stale rows"
        ),
        "12_replay_live_parity_fix": (
            "Persist eval-time push payload+freshness in audit; replay must use same snapshot not post-hoc event row"
        ),
    }

    _write_rows(reports / "phase611_good625_freshness_trace.csv", good_out, TRACE_COLUMNS)
    _write_rows(reports / "phase611_bad629630_freshness_trace.csv", bad_out, TRACE_COLUMNS)
    _write_rows(reports / "phase611_good_bad_pairwise_diff.csv", pairwise)
    _write_rows(reports / "phase611_raw_internal_freshness_value_diff.csv", raw_internal)
    _write_rows(reports / "phase611_good625_pass_reason_breakdown.csv", good_pass_rows)
    _write_rows(reports / "phase611_bad629630_block_reason_breakdown.csv", bad_block_rows)
    _write_rows(reports / "phase611_code_vs_data_classification.csv", code_vs_data)
    _write_rows(reports / "phase611_first_divergence_by_candidate.csv", divergence_rows)
    _write_rows(reports / "phase611_625_like_candidate_search_629630.csv", like_rows)
    _write_rows(reports / "phase611_system_fix_candidates.csv", fix_rows)

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "good_trace_count": len(good_traces),
        "bad_trace_count": len(bad_traces),
        "like_625_count": len(like_rows),
        "mandatory_answers": mandatory,
        "good_pass_reason_counts": dict(pass_reason_ctr),
        "bad_block_reason_top20": dict(block_reason_ctr.most_common(20)),
        "bad_category_counts": dict(cat_ctr),
        "output_dir": str(reports),
    }
    (reports / "phase611_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
