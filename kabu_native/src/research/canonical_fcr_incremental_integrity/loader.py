"""Stride-aware loader with event-count reconciliation (no silent drops)."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.canonical_fcr_exact_method.loader import (
    Tick,
    classify_ask_depletion,
    classify_trade_side,
    parse_ts,
    _f,
    _session,
)
from research.canonical_fcr_incremental_integrity.constants import CAPTURE_ROOT, LOT
from small_paper.canonical_board import normalize_kabu_board

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class SkipCounts:
    raw_lines: int = 0
    empty_line: int = 0
    json_error: int = 0
    no_payload: int = 0
    no_buy1_sell1: int = 0
    missing_quote: int = 0
    crossed_quote: int = 0
    bad_ts: int = 0
    stride_skipped: int = 0
    eligible: int = 0
    processed: int = 0

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


@dataclass
class LoadResult:
    streams: dict[str, list[Tick]]
    counts: SkipCounts
    by_day: dict[str, dict[str, int]] = field(default_factory=dict)
    by_symbol: dict[str, int] = field(default_factory=dict)
    first_last_seq: dict[str, dict[str, Any]] = field(default_factory=dict)
    seq_gaps: list[dict[str, Any]] = field(default_factory=list)


def load_streams_reconciled(
    days: list[str],
    *,
    stride: int = 1,
    max_seq_gap_samples: int = 50,
) -> LoadResult:
    """Load PUSH events. stride>1 skips lines via i % stride — EVENT SAMPLING."""
    counts = SkipCounts()
    streams: dict[str, list[Tick]] = defaultdict(list)
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"raw": 0, "eligible": 0, "processed": 0})
    by_symbol: dict[str, int] = defaultdict(int)
    first_last: dict[str, dict[str, Any]] = {}
    seq_gaps: list[dict[str, Any]] = []

    for day in days:
        day_dir = CAPTURE_ROOT / day
        if not day_dir.exists():
            continue
        last_vol: dict[str, float] = {}
        last_sess: dict[str, str] = {}
        last_aq: dict[str, float] = {}
        last_seq: dict[str, int] = {}
        for fp in sorted(day_dir.glob("push_part_*.jsonl")):
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    counts.raw_lines += 1
                    by_day[day]["raw"] += 1
                    if i % max(1, stride) != 0:
                        counts.stride_skipped += 1
                        continue
                    line = line.strip()
                    if not line:
                        counts.empty_line += 1
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        counts.json_error += 1
                        continue
                    op = rec.get("original_payload")
                    if not isinstance(op, dict):
                        counts.no_payload += 1
                        continue
                    if not isinstance(op.get("Buy1"), dict) or not isinstance(op.get("Sell1"), dict):
                        counts.no_buy1_sell1 += 1
                        continue
                    board = normalize_kabu_board(op)
                    if board.canonical_best_bid is None or board.canonical_best_ask is None:
                        counts.missing_quote += 1
                        continue
                    if board.canonical_best_ask < board.canonical_best_bid:
                        counts.crossed_quote += 1
                        continue
                    ts = parse_ts(rec.get("received_at_jst")) or parse_ts(op.get("CurrentPriceTime"))
                    if ts is None:
                        counts.bad_ts += 1
                        continue
                    counts.eligible += 1
                    by_day[day]["eligible"] += 1
                    sym = str(rec.get("symbol") or op.get("Symbol") or "")
                    if not sym.endswith(".T") and sym:
                        sym = f"{sym}.T"
                    sess = _session(ts)
                    px = _f(op.get("CurrentPrice") if op.get("CurrentPrice") is not None else rec.get("current_price"))
                    cum = _f(op.get("TradingVolume"))
                    vdelta: Optional[float] = None
                    reset = False
                    if cum is not None:
                        prev = last_vol.get(sym)
                        prev_s = last_sess.get(sym)
                        if prev_s is not None and prev_s != sess:
                            reset, vdelta = True, None
                        elif prev is None:
                            vdelta = None
                        elif cum < prev:
                            reset, vdelta = True, None
                        else:
                            vdelta = cum - prev
                        last_vol[sym] = cum
                        last_sess[sym] = sess
                    side, conf = classify_trade_side(px, board, vdelta if (vdelta is not None and vdelta > 0) else None)
                    aq = board.canonical_ask_qty
                    paq = last_aq.get(sym)
                    dep = classify_ask_depletion(paq, aq, side, vdelta)
                    if aq is not None:
                        last_aq[sym] = aq
                    seq_raw = rec.get("sequence")
                    try:
                        seq = int(seq_raw) if seq_raw is not None else i
                    except (TypeError, ValueError):
                        seq = i
                    key = f"{day}|{sym}"
                    if key in last_seq and seq < last_seq[key] and len(seq_gaps) < max_seq_gap_samples:
                        seq_gaps.append({"stream": key, "prev": last_seq[key], "cur": seq, "ts": ts.isoformat()})
                    if key in last_seq and seq > last_seq[key] + 1 and len(seq_gaps) < max_seq_gap_samples:
                        seq_gaps.append({"stream": key, "gap_from": last_seq[key], "gap_to": seq, "ts": ts.isoformat()})
                    last_seq[key] = seq
                    t = Tick(
                        day=day, symbol=sym, ts=ts, px=px, cum_vol=cum, volume_delta=vdelta,
                        board=board, event_id=f"{day}:{sym}:{ts.isoformat()}:{seq}",
                        session=sess, trade_side=side, trade_side_confidence=conf,
                        ask_depletion_class=dep, prev_ask_qty=paq, volume_reset=reset,
                    )
                    # stash sequence on tick via event_id parse; also setattr
                    setattr(t, "event_seq", seq)
                    streams[key].append(t)
                    counts.processed += 1
                    by_day[day]["processed"] += 1
                    by_symbol[sym] += 1

    for key, rows in streams.items():
        rows.sort(key=lambda x: (x.ts, getattr(x, "event_seq", 0)))
        for i, r in enumerate(rows):
            r.idx = i
        if rows:
            first_last[key] = {
                "first_seq": getattr(rows[0], "event_seq", None),
                "last_seq": getattr(rows[-1], "event_seq", None),
                "n": len(rows),
                "first_ts": rows[0].ts.isoformat(),
                "last_ts": rows[-1].ts.isoformat(),
            }

    return LoadResult(
        streams=dict(streams),
        counts=counts,
        by_day={k: dict(v) for k, v in by_day.items()},
        by_symbol=dict(by_symbol),
        first_last_seq=first_last,
        seq_gaps=seq_gaps,
    )


def exec_parts(t: Tick) -> dict[str, bool]:
    aq = t.board.canonical_ask_qty
    return {
        "quote_quality_pass": bool(t.board.canonical_quote_valid and not t.board.canonical_crossed),
        "ask_qty_100_pass": bool(aq is not None and aq >= LOT),
        "liquidity_pass": bool(
            t.board.canonical_quote_valid
            and not t.board.canonical_crossed
            and aq is not None and aq >= LOT
            and t.board.canonical_best_ask and t.board.canonical_best_ask > 0
        ),
    }


def audit_stride_semantics() -> dict[str, Any]:
    """Document what SAMPLE_STRIDE / stride arg means in FCR loader."""
    from pathlib import Path

    from research.canonical_fcr_exact_method import loader as old_loader

    src = Path(old_loader.__file__).read_text(encoding="utf-8")
    sampling = "if i % max(1, stride) != 0" in src
    return {
        "old_stride": 6,
        "mechanism": "jsonl_line_index_modulo_skip",
        "is_event_sampling": sampling,
        "is_batch_unit_only": False,
        "code_locus": "canonical_fcr_exact_method.loader.iter_day_ticks",
        "verdict": "STRIDE_EVENT_SAMPLING_FOUND" if sampling else "STRIDE_NOT_EVENT_SAMPLING",
        "note": "Old run processed every 6th jsonl line; state transitions / hold counts are not formal under sampling.",
    }
