"""Multi-candidate FULL canonical event replay via exit-oracle (research-only).

Semantics are exactly the Replay lifecycle contract:
- ENTRY on SCORE samples only (5s + STATE_CHANGE), warmup/session/quote/spread/
  same-symbol/CAP gates identical to the wrapped session ladder.
- EXIT monitored on EVERY canonical board event of the held symbol (bid>0),
  with the frozen `_update_position` ordering: mfe update -> trail arm ->
  STOP -> TARGET -> TRAILING -> MAX_HOLD (tolerances 1e-9, prices entry_ask/exit_bid).
- Orphan opens at partition end -> WINDOW_CENSORED (excluded from completed PnL).
- Trade adoption replicates `_adopt_trade` / `validate_trade_window`
  (lookback pre-check, 120s internal-gap, known gap-interval crossing).

Fidelity is PROVEN (not assumed) by ledger parity against the direct
session-based `replay_partition` for the X5 BASE package before any sweep
economics are read. The engine exists to make the Plan 2.1 "all 200 packages
full canonical replay" requirement computationally feasible; it shares the
candidate-independent SCORE sample stream and per-symbol exit streams captured
during the BASE session pass, then replays each JointStrategyPackage's
independent CAP5 portfolio over every event deterministically.

No Shadow / Runtime / Paper / Live changes from this module.
"""
from __future__ import annotations

import gzip
import hashlib
import pickle
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from research.e1_x6_provisional.cost_contract import net_pnl_yen
from research.e1_x6_provisional.util import JST, parse_ts, progress, sha256_obj

ORACLE_EVALUATION_MODE = "FULL_CANONICAL_EVENT_REPLAY_ORACLE_PARITY_PROVEN"
GAP_THRESHOLD_SEC = 120.0
SPREAD_MAX_BPS = 5.0
CAP = 5

# Candidate-independent ENTRY pre-gate rejects recorded in the BASE pass.
# (warmup / session hours / quote validity / duplicate-eval suppression)
INELIGIBLE_X5_RESULTS = {
    "INVALID_LOOKBACK",
    "SESSION_INVALID",
    "INVALID_QUOTE",
    "DUPLICATE_EVENT",
}

EXIT_REASONS = ("STOP", "TARGET", "TRAILING", "MAX_HOLD")


@dataclass
class ExitParams:
    stop_bps: float
    target_bps: float
    trail_arm_bps: float
    giveback: float
    max_hold_sec: float
    # Optional research EXIT extensions (pre-registered families only)
    invalidation_score_drop: Optional[float] = None  # exit at SCORE sample when
    # sample score <= entry_score - drop (checked at symbol SCORE samples)
    no_progress_sec: Optional[float] = None  # exit when hold>=sec and mfe<=np_mfe
    no_progress_mfe_bps: float = 0.0

    def key(self) -> tuple:
        return (
            round(float(self.stop_bps), 9),
            round(float(self.target_bps), 9),
            round(float(self.trail_arm_bps), 9),
            round(float(self.giveback), 12),
            round(float(self.max_hold_sec), 6),
            None if self.invalidation_score_drop is None else round(float(self.invalidation_score_drop), 9),
            None if self.no_progress_sec is None else round(float(self.no_progress_sec), 6),
            round(float(self.no_progress_mfe_bps), 9),
        )


@dataclass
class PartitionBundle:
    day: str
    am_pm: str
    window_id: str
    mask_meta: dict[str, Any]
    lookback_sec: float
    w0_epoch: Optional[float]
    big_gap_pairs: list[tuple[float, float]]
    gap_intervals: list[tuple[float, float]]
    score_rows: list[dict[str, Any]]
    # per-symbol exit streams: sym -> dict(ts=float64[], seq=int64[], bid=float64[])
    sym_streams: dict[str, dict[str, np.ndarray]]
    # per-symbol SCORE sample streams (for invalidation exits): ts/seq/score arrays
    sym_scores: dict[str, dict[str, np.ndarray]]
    x5_trades: list[dict[str, Any]] = field(default_factory=list)
    x5_censored_symbols: list[str] = field(default_factory=list)
    events_fed: int = 0
    exit_stream_ts_mismatch: int = 0

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wb", compresslevel=1) as fh:
            pickle.dump(self, fh, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def load(path: Path) -> "PartitionBundle":
        with gzip.open(path, "rb") as fh:
            return pickle.load(fh)


def _proc_epoch(e: Any) -> Optional[float]:
    t = parse_ts(getattr(e, "received_at", None)) or parse_ts(getattr(e, "event_time", None))
    return t.timestamp() if t is not None else None


def build_bundle_from_partition(
    *,
    day: str,
    am_pm: str,
    window_id: str,
    part: Any,  # PartitionReplayResult with score_rows + exit_stream collected
    seg_events: Sequence[Any],
    gap_intervals_raw: Sequence[tuple[Any, Any]],
    lookback_sec: float,
) -> PartitionBundle:
    """Convert a BASE session partition replay into an oracle bundle."""
    # adoption context: sorted proc times over ALL seg events (any symbol)
    times = sorted(t for t in (_proc_epoch(e) for e in seg_events) if t is not None)
    big_pairs: list[tuple[float, float]] = []
    for a, b in zip(times, times[1:]):
        if (b - a) > GAP_THRESHOLD_SEC:
            big_pairs.append((a, b))
    w0 = _proc_epoch(seg_events[0]) if seg_events else None

    gaps: list[tuple[float, float]] = []
    for gs, ge in gap_intervals_raw or []:
        a = parse_ts(gs)
        b = parse_ts(ge)
        if a is not None and b is not None:
            gaps.append((a.timestamp(), b.timestamp()))

    # per-symbol exit streams (feed order == sequence order asserted)
    by_sym: dict[str, list[tuple[float, int, float]]] = {}
    prev_seq = None
    non_monotonic = 0
    for sym, ts, seq, bid in part.exit_stream:
        if prev_seq is not None and seq < prev_seq:
            non_monotonic += 1
        prev_seq = seq
        by_sym.setdefault(sym, []).append((ts, seq, bid))
    if non_monotonic:
        progress(f"ORACLE: WARNING non-monotonic sequences n={non_monotonic} {day} {am_pm}")
    sym_streams = {
        s: {
            "ts": np.asarray([r[0] for r in rows], dtype=np.float64),
            "seq": np.asarray([r[1] for r in rows], dtype=np.int64),
            "bid": np.asarray([r[2] for r in rows], dtype=np.float64),
        }
        for s, rows in by_sym.items()
    }

    sym_scores_tmp: dict[str, list[tuple[float, int, float]]] = {}
    for r in part.score_rows:
        sc = r.get("score")
        if sc is None:
            continue
        sym_scores_tmp.setdefault(str(r["symbol"]), []).append(
            (float(r["decision_ts"]), int(r["event_sequence"]), float(sc))
        )
    sym_scores = {
        s: {
            "ts": np.asarray([r[0] for r in rows], dtype=np.float64),
            "seq": np.asarray([r[1] for r in rows], dtype=np.int64),
            "score": np.asarray([r[2] for r in rows], dtype=np.float64),
        }
        for s, rows in sym_scores_tmp.items()
    }

    return PartitionBundle(
        day=day,
        am_pm=am_pm,
        window_id=window_id,
        mask_meta=dict(part.mask_meta or {}),
        lookback_sec=float(lookback_sec),
        w0_epoch=w0,
        big_gap_pairs=big_pairs,
        gap_intervals=gaps,
        score_rows=list(part.score_rows),
        sym_streams=sym_streams,
        sym_scores=sym_scores,
        x5_trades=list(part.completed_trades),
        x5_censored_symbols=[str(s) for s in part.open_at_end_symbols],
        events_fed=int(part.events_fed),
        exit_stream_ts_mismatch=int(part.exit_stream_ts_mismatch),
    )


def adoption_ok(bundle: PartitionBundle, entry_epoch: float, exit_epoch: float) -> tuple[bool, str]:
    """Replicates _adopt_trade + validate_trade_window for oracle trades."""
    lb = entry_epoch - bundle.lookback_sec
    if bundle.w0_epoch is not None and lb < bundle.w0_epoch - 1e-9:
        return False, "INVALID_LOOKBACK"
    for a, b in bundle.big_gap_pairs:
        if lb <= a + 1e-9 and b <= exit_epoch + 1e-9:
            return False, "CROSSES_CAPTURE_GAP_INTERNAL"
    for a, b in bundle.gap_intervals:
        if (a <= entry_epoch <= b) or (a <= exit_epoch <= b) or (entry_epoch <= a and exit_epoch >= b):
            return False, "CROSSES_KNOWN_GAP"
    return True, ""


def oracle_exit(
    bundle: PartitionBundle,
    *,
    symbol: str,
    entry_seq: int,
    entry_epoch: float,
    entry_ask: float,
    entry_score: Optional[float],
    xp: ExitParams,
) -> Optional[tuple[float, int, float, str]]:
    """First exit (ts, seq, bid, reason) after the entry event, else None (censored)."""
    st = bundle.sym_streams.get(symbol)
    if st is None:
        return None
    seq = st["seq"]
    i0 = int(np.searchsorted(seq, entry_seq, side="right"))
    n = seq.shape[0]
    if i0 >= n:
        return None
    ts = st["ts"]
    bid = st["bid"]
    j_mh = int(np.searchsorted(ts, entry_epoch + xp.max_hold_sec - 1e-9, side="left"))
    j_mh = max(j_mh, i0)
    hi = min(j_mh + 1, n)  # inclusive of the MAX_HOLD-triggering event if it exists
    r = (bid[i0:hi] / entry_ask - 1.0) * 10000.0
    c = np.maximum.accumulate(r)
    stop_m = r <= xp.stop_bps + 1e-9
    targ_m = r >= xp.target_bps - 1e-9
    trail_m = (c >= xp.trail_arm_bps - 1e-9) & (r <= c * (1.0 - xp.giveback) + 1e-9)
    any_m = stop_m | targ_m | trail_m

    if xp.no_progress_sec is not None:
        hold = ts[i0:hi] - entry_epoch
        np_m = (hold >= xp.no_progress_sec - 1e-9) & (c <= xp.no_progress_mfe_bps + 1e-9) & (
            r <= 0.0 + 1e-9
        )
        any_m = any_m | np_m
    else:
        np_m = None

    inv_hit: Optional[tuple[float, int]] = None
    if xp.invalidation_score_drop is not None and entry_score is not None:
        ss = bundle.sym_scores.get(symbol)
        if ss is not None:
            sseq = ss["seq"]
            k0 = int(np.searchsorted(sseq, entry_seq, side="right"))
            scores = ss["score"][k0:]
            drop_m = scores <= float(entry_score) - float(xp.invalidation_score_drop) + 1e-12
            if drop_m.any():
                ki = k0 + int(np.argmax(drop_m))
                inv_hit = (float(ss["ts"][ki]), int(sseq[ki]))

    k: Optional[int] = None
    if any_m.any():
        k = int(np.argmax(any_m))

    def _price_exit(kk: int) -> tuple[float, int, float, str]:
        idx = i0 + kk
        if stop_m[kk]:
            reason = "STOP"
        elif targ_m[kk]:
            reason = "TARGET"
        elif trail_m[kk]:
            reason = "TRAILING"
        elif np_m is not None and np_m[kk]:
            reason = "NO_PROGRESS"
        else:
            reason = "MAX_HOLD"
        return (float(ts[idx]), int(seq[idx]), float(bid[idx]), reason)

    price_exit: Optional[tuple[float, int, float, str]] = None
    if k is not None:
        price_exit = _price_exit(k)
    elif j_mh < n:
        price_exit = (float(ts[j_mh]), int(seq[j_mh]), float(bid[j_mh]), "MAX_HOLD")

    if inv_hit is not None:
        # invalidation exit executes at the exit-stream event of that SCORE sample
        # (or the next bid>0 event of the symbol), if it precedes the price exit
        _, iv_seq = inv_hit
        ii = int(np.searchsorted(seq, iv_seq, side="left"))
        if ii < n and (price_exit is None or int(seq[ii]) < price_exit[1]):
            return (float(ts[ii]), int(seq[ii]), float(bid[ii]), "INVALIDATION")
    return price_exit


@dataclass
class PackageAccumulator:
    """Streaming per-package metrics accumulator (memory-bounded sweep)."""
    strategy_id: str
    day_pnl: dict[str, float] = field(default_factory=dict)
    day_n: dict[str, int] = field(default_factory=dict)
    sym_pnl: dict[str, float] = field(default_factory=dict)
    wins_sum: float = 0.0
    loss_sum: float = 0.0
    wins_sum_ex722: float = 0.0
    loss_sum_ex722: float = 0.0
    max_trade_pnl: float = float("-inf")
    stop_loss_total: float = 0.0
    equity: float = 0.0
    peak: float = 0.0
    max_dd: float = 0.0
    n: int = 0
    censored_n: int = 0
    excluded_adoption_n: int = 0
    exit_reason_counts: dict[str, int] = field(default_factory=dict)
    _sha: Any = None

    def sha_update(self, tup: tuple) -> None:
        if self._sha is None:
            self._sha = hashlib.sha256()
        self._sha.update(repr(tup).encode("utf-8"))

    def ledger_sha(self) -> str:
        return self._sha.hexdigest() if self._sha is not None else sha256_obj([])

    def add_trade(self, day: str, symbol: str, pnl: float, reason: str) -> None:
        self.n += 1
        self.day_pnl[day] = self.day_pnl.get(day, 0.0) + pnl
        self.day_n[day] = self.day_n.get(day, 0) + 1
        self.sym_pnl[symbol] = self.sym_pnl.get(symbol, 0.0) + pnl
        if pnl > 0:
            self.wins_sum += pnl
        elif pnl < 0:
            self.loss_sum += pnl
        if day != "20260722":
            if pnl > 0:
                self.wins_sum_ex722 += pnl
            elif pnl < 0:
                self.loss_sum_ex722 += pnl
        self.max_trade_pnl = max(self.max_trade_pnl, pnl)
        if reason == "STOP" and pnl < 0:
            self.stop_loss_total += pnl
        self.equity += pnl
        self.peak = max(self.peak, self.equity)
        self.max_dd = min(self.max_dd, self.equity - self.peak)
        self.exit_reason_counts[reason] = self.exit_reason_counts.get(reason, 0) + 1


def metrics_from_accumulator(acc: "PackageAccumulator", days: Sequence[str]) -> dict[str, Any]:
    """Plan 2.1 metrics dict (day_robust_gates-compatible) from streaming aggregates."""
    from research.e1_x6_provisional.day_robust_gates import (
        ROLLING_CONFIRM_DAYS,
        _median,
        _quantile,
        best_days_desc,
    )

    day_pnl = {d: float(acc.day_pnl.get(d, 0.0)) for d in days}
    day_n = {d: int(acc.day_n.get(d, 0)) for d in days}
    total = float(sum(day_pnl.values()))
    order = best_days_desc(day_pnl)
    best1 = order[0] if order else None
    best2 = order[1] if len(order) > 1 else None
    ex1 = total - (day_pnl.get(best1, 0.0) if best1 else 0.0)
    ex2 = ex1 - (day_pnl.get(best2, 0.0) if best2 else 0.0)
    gross_pos = float(sum(p for p in day_pnl.values() if p > 0))
    t1 = (day_pnl[best1] / gross_pos) if best1 and gross_pos > 0 and day_pnl[best1] > 0 else None
    t2sum = sum(day_pnl[d] for d in (best1, best2) if d is not None and day_pnl[d] > 0)
    t2 = (t2sum / gross_pos) if gross_pos > 0 else None

    def _pf(w: float, l: float, n: int) -> tuple[Optional[float], str]:
        if l < 0:
            return w / abs(l), "OK"
        if w > 0:
            return None, "NO_LOSS"
        return None, "EMPTY" if n == 0 else "NO_WIN"

    pf, pf_status = _pf(acc.wins_sum, acc.loss_sum, acc.n)
    ex722_pnl = float(acc.wins_sum_ex722 + acc.loss_sum_ex722)
    ex722_pf, ex722_pf_status = _pf(acc.wins_sum_ex722, acc.loss_sum_ex722, acc.n)
    top_sym = (
        max(acc.sym_pnl.items(), key=lambda kv: (kv[1], kv[0]))[0] if acc.sym_pnl else None
    )
    confirm = {d: day_pnl.get(d, 0.0) for d in ROLLING_CONFIRM_DAYS if d in day_pnl}
    cvals = list(confirm.values())
    ctotal = float(sum(cvals))
    corder = best_days_desc(confirm)
    cbest = corder[0] if corder else None
    return {
        "n": int(acc.n),
        "total_pnl": total,
        "day_pnl": day_pnl,
        "day_n": day_n,
        "median_day_pnl": _median(list(day_pnl.values())),
        "day_pnl_q25": _quantile(list(day_pnl.values()), 0.25),
        "best1_day": best1,
        "best2_day": best2,
        "ex_best1_day_pnl": ex1,
        "ex_best2_days_pnl": ex2,
        "gross_positive_day_pnl": gross_pos,
        "top1_day_share_of_gross_positive": t1,
        "top2_days_share_of_gross_positive": t2,
        "ex722_pnl": ex722_pnl,
        "ex722_pf": ex722_pf,
        "ex722_pf_status": ex722_pf_status,
        "pf": pf,
        "pf_status": pf_status,
        "ex_top1_trade_pnl": total - (acc.max_trade_pnl if acc.n else 0.0),
        "top1_symbol": top_sym,
        "ex_top1_symbol_pnl": total - (acc.sym_pnl.get(top_sym, 0.0) if top_sym else 0.0),
        "max_dd": float(acc.max_dd),
        "stop_loss_total": float(acc.stop_loss_total),
        "rolling_confirm_day_pnls": confirm,
        "rolling_confirm_total": ctotal,
        "rolling_confirm_median": _median(cvals),
        "rolling_confirm_best_day": cbest,
        "rolling_ex_best_confirm_day": ctotal - (confirm.get(cbest, 0.0) if cbest else 0.0),
        "censored_n": int(acc.censored_n),
        "excluded_adoption_n": int(acc.excluded_adoption_n),
        "exit_reason_counts": dict(acc.exit_reason_counts),
        "ledger_sha256": acc.ledger_sha(),
    }


def entry_signals_for_bundle(bundle: PartitionBundle) -> list[dict[str, Any]]:
    """Eligible ENTRY evaluation points in feed order (dedup by symbol+sequence)."""
    seen: set[tuple[str, int]] = set()
    out: list[dict[str, Any]] = []
    for r in bundle.score_rows:
        if r.get("score") is None:
            continue
        er = r.get("x5_entry_result")
        if er in INELIGIBLE_X5_RESULTS:
            continue
        sp = r.get("spread_bps")
        b = r.get("bid")
        a = r.get("ask")
        if b is None or a is None or float(b) <= 0 or float(a) <= 0 or float(a) < float(b):
            continue
        if sp is None:
            sp = (float(a) - float(b)) / float(a) * 10000.0
        if float(sp) > SPREAD_MAX_BPS + 1e-9:
            continue
        key = (str(r["symbol"]), int(r["event_sequence"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out.sort(key=lambda r: int(r["event_sequence"]))
    return out


def replay_package_on_bundle(
    bundle: PartitionBundle,
    *,
    signals: Sequence[Mapping[str, Any]],
    signal_mask: np.ndarray,
    xp: ExitParams,
    exit_cache: dict,
    collect_trades: bool = False,
    acc: Optional[PackageAccumulator] = None,
) -> Optional[list[dict[str, Any]]]:
    """Independent CAP5 portfolio for one package on one partition.

    signals: eligible entry points (feed order); signal_mask: predicate results.
    """
    positions: dict[str, float] = {}  # sym -> exit_seq (inf = censored)
    raw: list[tuple] = []
    xkey = xp.key()

    for i, row in enumerate(signals):
        if not signal_mask[i]:
            continue
        sym = str(row["symbol"])
        sig_seq = int(row["event_sequence"])
        # free positions whose exit event has been processed (exit_seq <= sig_seq)
        if positions:
            done = [s for s, xs in positions.items() if xs <= sig_seq]
            for s in done:
                del positions[s]
        if sym in positions:
            continue  # SAME_SYMBOL_OPEN
        if len(positions) >= CAP:
            continue  # CAP5_BLOCKED
        entry_ask = float(row["ask"])
        entry_epoch = float(row["decision_ts"])
        ck = (sym, sig_seq, xkey)
        if ck in exit_cache:
            ex = exit_cache[ck]
        else:
            ex = oracle_exit(
                bundle,
                symbol=sym,
                entry_seq=sig_seq,
                entry_epoch=entry_epoch,
                entry_ask=entry_ask,
                entry_score=row.get("score"),
                xp=xp,
            )
            exit_cache[ck] = ex
        if ex is None:
            positions[sym] = float("inf")
            if acc is not None:
                acc.censored_n += 1
            continue
        exit_epoch, exit_seq, exit_bid, reason = ex
        positions[sym] = exit_seq
        ok, _why = adoption_ok(bundle, entry_epoch, exit_epoch)
        if not ok:
            if acc is not None:
                acc.excluded_adoption_n += 1
            continue
        econ = net_pnl_yen(entry_ask, exit_bid)
        raw.append(
            (exit_epoch, exit_seq, sym, sig_seq, entry_epoch, entry_ask, exit_bid, reason, econ)
        )

    # Canonical realized order = exit event order (Metric Contract §7.4)
    raw.sort(key=lambda t: (t[1], t[2]))
    trades: Optional[list[dict[str, Any]]] = [] if collect_trades else None
    for exit_epoch, exit_seq, sym, sig_seq, entry_epoch, entry_ask, exit_bid, reason, econ in raw:
        pnl = float(econ["net_pnl_yen_100"])
        if acc is not None:
            acc.add_trade(bundle.day, sym, pnl, reason)
            acc.sha_update(
                (
                    bundle.day,
                    bundle.am_pm,
                    sym,
                    sig_seq,
                    exit_seq,
                    round(entry_ask, 6),
                    round(exit_bid, 6),
                    reason,
                    round(pnl, 6),
                )
            )
        if collect_trades:
            et = datetime.fromtimestamp(entry_epoch, tz=JST)
            xt = datetime.fromtimestamp(exit_epoch, tz=JST)
            trades.append(
                {
                    "day": bundle.day,
                    "am_pm": bundle.am_pm,
                    "window_id": bundle.window_id,
                    "analysis_mask_id": bundle.mask_meta.get("analysis_mask_id"),
                    "quality_class": bundle.mask_meta.get("quality_class"),
                    "symbol": sym,
                    "entry_time": et.isoformat(),
                    "exit_time": xt.isoformat(),
                    "entry_seq": sig_seq,
                    "exit_seq": exit_seq,
                    "entry_ask": round(entry_ask, 6),
                    "exit_bid": round(exit_bid, 6),
                    "exit_reason": reason,
                    "holding_sec": round(exit_epoch - entry_epoch, 6),
                    "gross_pnl_yen_100": econ["gross_pnl_yen_100"],
                    "cost_yen_100": econ["cost_yen_100"],
                    "net_pnl_yen_100": econ["net_pnl_yen_100"],
                    "net_bps": econ["net_bps"],
                    "evaluation_mode": ORACLE_EVALUATION_MODE,
                    "in_analysis_mask_entry": True,
                    "in_analysis_mask_exit": True,
                }
            )
    return trades


def parity_check_bundle(
    bundle: PartitionBundle,
    *,
    x5_threshold: float,
    xp: ExitParams,
    atol_ts: float = 2e-3,
    atol_pnl: float = 1e-3,
) -> dict[str, Any]:
    """Oracle X5 package vs session-adopted BASE trades of the same partition."""
    signals = entry_signals_for_bundle(bundle)
    mask = np.asarray([float(r["score"]) >= float(x5_threshold) for r in signals], dtype=bool)
    cache: dict = {}
    acc = PackageAccumulator(strategy_id="PARITY_X5")
    trades = replay_package_on_bundle(
        bundle, signals=signals, signal_mask=mask, xp=xp, exit_cache=cache,
        collect_trades=True, acc=acc,
    )
    ref = bundle.x5_trades
    mismatches: list[str] = []
    if len(trades) != len(ref):
        mismatches.append(f"n_trades oracle={len(trades)} session={len(ref)}")
    for i, (o, s) in enumerate(zip(trades, ref)):
        if str(o["symbol"]) != str(s.get("symbol")):
            mismatches.append(f"[{i}] symbol {o['symbol']} != {s.get('symbol')}")
            continue
        et_s = parse_ts(s.get("entry_time"))
        xt_s = parse_ts(s.get("exit_time"))
        et_o = parse_ts(o["entry_time"])
        xt_o = parse_ts(o["exit_time"])
        if et_s is None or abs(et_o.timestamp() - et_s.timestamp()) > atol_ts:
            mismatches.append(f"[{i}] entry_time {o['entry_time']} != {s.get('entry_time')}")
        if xt_s is None or abs(xt_o.timestamp() - xt_s.timestamp()) > atol_ts:
            mismatches.append(f"[{i}] exit_time {o['exit_time']} != {s.get('exit_time')}")
        if abs(float(o["entry_ask"]) - float(s.get("entry_ask") or 0)) > 1e-6:
            mismatches.append(f"[{i}] entry_ask {o['entry_ask']} != {s.get('entry_ask')}")
        if abs(float(o["exit_bid"]) - float(s.get("exit_bid") or 0)) > 1e-6:
            mismatches.append(f"[{i}] exit_bid {o['exit_bid']} != {s.get('exit_bid')}")
        if str(o["exit_reason"]) != str(s.get("exit_reason")):
            mismatches.append(f"[{i}] reason {o['exit_reason']} != {s.get('exit_reason')}")
        if abs(float(o["net_pnl_yen_100"]) - float(s.get("net_pnl_yen_100") or 0)) > atol_pnl:
            mismatches.append(
                f"[{i}] pnl {o['net_pnl_yen_100']} != {s.get('net_pnl_yen_100')}"
            )
    return {
        "day": bundle.day,
        "am_pm": bundle.am_pm,
        "window_id": bundle.window_id,
        "oracle_n": len(trades),
        "session_n": len(ref),
        "oracle_pnl": round(sum(float(t["net_pnl_yen_100"]) for t in trades), 6),
        "session_pnl": round(sum(float(t.get("net_pnl_yen_100") or 0) for t in ref), 6),
        "match": not mismatches,
        "mismatches": mismatches[:40],
        "mismatch_n": len(mismatches),
    }
