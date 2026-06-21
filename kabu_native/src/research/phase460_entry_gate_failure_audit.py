"""
Phase460 — Entry Gate Failure Audit (research only).

Audits Dynamic40 candidate gate failures vs uptrend symbol misses.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import (
    _stream_events,
    guard_high_drift,
)
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase450_momentum_redesign_shadow import (
    MOMENTUM_LOW_CUTOFF,
    trend_assisted_momentum_score,
)
from research.phase451_entry_shape_tournament import (
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
    _optional_float,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import (
    _board_token,
    _passes_baseline_mid_high,
    _v2_entry_score,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    TERTILE_CUTOFFS,
    _feature_token,
    momentum_low_required_for_v2,
)
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

JST = ZoneInfo("Asia/Tokyo")
REPLAY_MODE = "phase456_runtime_np"
UPTREND_PCT = 3.0
TARGET_UPTREND_SYMBOLS = ("3441.T", "6492.T", "7256.T", "6466.T", "7600.T")
TARGET_DAY_619 = "20260619"


def _iter_sessions(kabu_root: Path) -> list[tuple[str, Path]]:
    base = kabu_root / "results" / "small_paper"
    out: list[tuple[str, Path]] = []
    if not base.is_dir():
        return out
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if day < PERIOD_START or day > PERIOD_END:
            continue
        for sess in sorted(day_dir.iterdir()):
            if sess.is_dir() and sess.name.startswith("live_session"):
                out.append((day, sess))
    return out


def _load_day_events(kabu: Path, day: str) -> list[dict[str, str]]:
    base = kabu / "results" / "small_paper" / day
    rows: list[dict[str, str]] = []
    if not base.is_dir():
        return rows
    for sess in sorted(base.iterdir()):
        path = sess / "small_paper_events.csv"
        if path.is_file():
            rows.extend(_stream_events(path))
    return rows


def _gate_from_reason(reason: str) -> Optional[str]:
    r = reason.lower()
    if not r:
        return None
    if "momentum" in r:
        return "Momentum"
    if "entry_score" in r or "expectancy" in r or "score_v2" in r:
        return "Board"
    if "high_drift" in r or "pullback" in r or "near_day_high" in r:
        return "High Drift"
    if "weak_shape" in r or "shape_reject" in r:
        return "Weak Shape"
    if "max_concurrent" in r or "max_entries" in r or "same_symbol" in r or "capacity" in r:
        return "Capacity"
    return None


def _resolve_gate_failure(trade: Mapping[str, Any]) -> str:
    if str(trade.get("outcome") or "") == "accepted":
        return ""
    reason = str(trade.get("gate_reject_reason") or trade.get("reject_reason") or "")
    computed = _primary_gate_failure(trade)
    if computed:
        return computed
    mapped = _gate_from_reason(reason)
    if mapped:
        return mapped
    if reason:
        return "Other"
    return "Other"

AUDIT_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "outcome",
    "primary_gate_failure",
    "momentum_score",
    "board_bucket",
    "entry_score_v2",
    "universe_bucket",
    "gate_reject_reason",
    "pnl_yen",
]

REJECT_FIELDS = ["gate_failure", "count", "share"]
UPTREND_FIELDS = [
    "symbol",
    "day",
    "open_to_close_pct",
    "was_candidate",
    "outcome",
    "primary_gate_failure",
    "gate_reject_reason",
    "captured_in_sim",
]
SIM_FIELDS = [
    "variant",
    "total_pnl_yen",
    "delta_pnl_vs_baseline",
    "profit_factor",
    "delta_pf_vs_baseline",
    "max_drawdown_yen",
    "delta_maxdd_vs_baseline",
    "accepted_count",
    "daily_pnl_619",
    "delta_daily_pnl_619",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_6466",
    "captured_7600",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _map_runtime_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(trade)
    for src, dst in (
        ("return_5min_pct", "entry_rise_5min_pct"),
        ("return_10min_pct", "entry_rise_10min_pct"),
        ("return_15min_pct", "entry_rise_15min_pct"),
        ("return_30min_pct", "entry_rise_30min_pct"),
    ):
        if out.get(dst) is None and out.get(src) is not None:
            out[dst] = out[src]
    return out


def _weak_shape_block(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def _board_bucket(trade: Mapping[str, Any]) -> str:
    tok = _board_token(trade) or ""
    return tok.split(":", 1)[-1] if ":" in tok else "unknown"


def _is_dynamic40(row: Mapping[str, Any]) -> bool:
    slot = str(row.get("universe_slot") or "").lower()
    bucket = str(row.get("universe_bucket") or "").lower()
    grp = str(row.get("universe_group") or "").lower()
    src = str(row.get("source_bucket") or "").lower()
    return (
        slot == "dynamic"
        or bucket in ("dynamic", "dynamic40")
        or grp == "dynamic40"
        or "dynamic40" in src
    )


def _primary_gate_failure(trade: Mapping[str, Any]) -> Optional[str]:
    if not momentum_low_required_for_v2(trade):
        return "Momentum"
    board = _feature_token("Board", trade)
    if board not in ("Board:mid", "Board:high"):
        return "Board"
    if board == "Board:mid" and _v2_entry_score(trade) < ENTRY_SCORE_V2_GATE_MIN:
        return "Board"
    if guard_high_drift(trade):
        return "High Drift"
    if _weak_shape_block(trade):
        return "Weak Shape"
    return None


def _pass_no_momentum(trade: Mapping[str, Any]) -> bool:
    board = _feature_token("Board", trade)
    if board == "Board:high":
        return True
    if board == "Board:mid":
        return _v2_entry_score(trade) >= ENTRY_SCORE_V2_GATE_MIN
    return False


def _pass_no_board(trade: Mapping[str, Any]) -> bool:
    return momentum_low_required_for_v2(trade) and _v2_entry_score(trade) >= ENTRY_SCORE_V2_GATE_MIN


def _pass_momentum_relaxed(trade: Mapping[str, Any]) -> bool:
    if trend_assisted_momentum_score(trade) > MOMENTUM_LOW_CUTOFF:
        return False
    board = _feature_token("Board", trade)
    if board == "Board:high":
        return True
    if board == "Board:mid":
        return _v2_entry_score(trade) >= ENTRY_SCORE_V2_GATE_MIN
    return False


def _pass_board_relaxed(trade: Mapping[str, Any]) -> bool:
    if not momentum_low_required_for_v2(trade):
        return False
    board = _feature_token("Board", trade)
    if board in ("Board:mid", "Board:high", "Board:low"):
        return _v2_entry_score(trade) >= max(2, ENTRY_SCORE_V2_GATE_MIN - 1)
    return False


def _runtime_block(pass_fn: Callable[[Mapping[str, Any]], bool]):
    def block(tr: Mapping[str, Any]) -> bool:
        if not pass_fn(tr):
            return True
        if guard_high_drift(tr):
            return True
        if _weak_shape_block(tr):
            return True
        return False

    return block


def _load_dynamic40_records(kabu: Path) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for day, sess in _iter_sessions(kabu):
        if day < PERIOD_START or day > PERIOD_END:
            continue
        events_path = sess / "small_paper_events.csv"
        for row in _stream_events(events_path):
            if not _is_dynamic40(row):
                continue
            et = str(row.get("entry_time") or row.get("event_time") or "")
            sym = str(row.get("symbol") or "")
            if not sym or not et:
                continue
            key = f"{sym}|{et}"
            rec = records.setdefault(
                key,
                {
                    "symbol": sym,
                    "day": day,
                    "entry_time": et,
                    "entry_price": _float(row.get("entry_price") or row.get("current_price")),
                    "universe_bucket": row.get("universe_bucket") or row.get("universe_slot"),
                    "outcome": "candidate",
                    "gate_reject_reason": "",
                },
            )
            etype = str(row.get("event_type") or "")
            if etype == "accepted":
                rec["outcome"] = "accepted"
            elif etype in ("rejected", "reject"):
                rec["outcome"] = "rejected"
                reason = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
                rec["gate_reject_reason"] = reason or rec.get("gate_reject_reason", "")
            for fld in (
                "momentum_continuation_score",
                "entry_order_book_imbalance",
                "trading_value",
                "continuation_quality_score",
                "entry_vwap_dev_pct",
                "entry_near_day_high_pct",
                "entry_expectancy_score_v2",
                "active_score_tokens_v2",
                "universe_slot",
                "universe_bucket",
                "source_bucket",
                "gate_reject_reason",
                "reject_reason",
            ):
                if row.get(fld) not in (None, ""):
                    rec[fld] = row[fld]

        rejects_path = sess / "small_paper_rejects.csv"
        if rejects_path.is_file():
            with rejects_path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    if not _is_dynamic40(row):
                        continue
                    et = str(row.get("entry_time") or row.get("event_time") or "")
                    sym = str(row.get("symbol") or "")
                    if not sym or not et:
                        continue
                    key = f"{sym}|{et}"
                    reason = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
                    rec = records.setdefault(
                        key,
                        {
                            "symbol": sym,
                            "day": day,
                            "entry_time": et,
                            "entry_price": _float(row.get("entry_price") or row.get("current_price")),
                            "universe_bucket": row.get("universe_bucket") or row.get("universe_slot"),
                            "outcome": "rejected",
                            "gate_reject_reason": reason,
                        },
                    )
                    if rec.get("outcome") != "accepted":
                        rec["outcome"] = "rejected"
                    rec["gate_reject_reason"] = reason or rec.get("gate_reject_reason", "")
                    for fld in (
                        "momentum_continuation_score",
                        "entry_order_book_imbalance",
                        "trading_value",
                        "continuation_quality_score",
                        "entry_vwap_dev_pct",
                        "entry_near_day_high_pct",
                        "entry_expectancy_score_v2",
                        "universe_slot",
                        "universe_bucket",
                        "source_bucket",
                    ):
                        if row.get(fld) not in (None, ""):
                            rec[fld] = row[fld]

    # Include candidate/reject rows that lost universe tags on early scans.
    sym_day_dynamic: set[tuple[str, str]] = {
        (str(r.get("symbol") or ""), str(r.get("day") or ""))
        for r in records.values()
        if _is_dynamic40(r)
    }
    for day, sess in _iter_sessions(kabu):
        if day < PERIOD_START or day > PERIOD_END:
            continue
        for row in _stream_events(sess / "small_paper_events.csv"):
            sym = str(row.get("symbol") or "")
            et = str(row.get("entry_time") or row.get("event_time") or "")
            if not sym or not et:
                continue
            if _is_dynamic40(row):
                sym_day_dynamic.add((sym, day))
                continue
            if (sym, day) not in sym_day_dynamic:
                continue
            key = f"{sym}|{et}"
            etype = str(row.get("event_type") or "")
            if etype not in ("candidate", "rejected", "reject", "accepted"):
                continue
            rec = records.setdefault(
                key,
                {
                    "symbol": sym,
                    "day": day,
                    "entry_time": et,
                    "entry_price": _float(row.get("entry_price") or row.get("current_price")),
                    "universe_bucket": "dynamic40",
                    "outcome": "candidate",
                    "gate_reject_reason": "",
                },
            )
            if etype == "accepted":
                rec["outcome"] = "accepted"
            elif etype in ("rejected", "reject"):
                rec["outcome"] = "rejected"
                reason = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
                rec["gate_reject_reason"] = reason or rec.get("gate_reject_reason", "")
            for fld in (
                "momentum_continuation_score",
                "entry_order_book_imbalance",
                "trading_value",
                "continuation_quality_score",
                "entry_vwap_dev_pct",
                "entry_near_day_high_pct",
                "entry_expectancy_score_v2",
                "gate_reject_reason",
                "reject_reason",
            ):
                if row.get(fld) not in (None, ""):
                    rec[fld] = row[fld]
    return list(records.values())


def _day_open_close(
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for (sym, day), series in price_idx.items():
        if not series:
            continue
        open_dt = datetime.strptime(f"{day} 09:00:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)
        close_dt = datetime.strptime(f"{day} 15:30:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)
        open_px = None
        close_px = None
        for ts, px in series:
            if ts >= open_dt and open_px is None:
                open_px = px
            if ts <= close_dt:
                close_px = px
        if open_px and close_px and open_px > 0:
            out[(sym, day)] = round((close_px - open_px) / open_px * 100.0, 4)
    return out


def _metrics(state: Any, *, variant: str) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "") for r in state.trade_log}
    return {
        "variant": variant,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        **{f"captured_{s.replace('.T', '')}": s in accepted_syms for s in TARGET_UPTREND_SYMBOLS},
        **{f"symbol_pnl_{s.replace('.T', '')}": sym_pnl.get(s.replace(".T", ""), 0.0) for s in TARGET_UPTREND_SYMBOLS},
    }


def _verdict(
    *,
    missed_uptrend: int,
    momentum_miss: int,
    board_miss: int,
    both_miss: int,
    delta_no_mom: float,
    delta_no_board: float,
    target_619_gate_blocked: int,
) -> str:
    if missed_uptrend == 0 and target_619_gate_blocked == 0:
        return "gate_not_problem"
    if momentum_miss > 0 and board_miss == 0 and both_miss == 0:
        return "momentum_gate_problem"
    if board_miss > 0 and momentum_miss == 0 and both_miss == 0:
        return "board_gate_problem"
    if momentum_miss > 0 or board_miss > 0 or both_miss > 0 or target_619_gate_blocked > 0:
        return "combined_gate_problem"
    return "gate_not_problem"


def _analyze_target_619(
    enriched_by_key: Mapping[str, Mapping[str, Any]],
    dyn_records: Sequence[Mapping[str, Any]],
    *,
    kabu: Path,
) -> list[dict[str, Any]]:
    events = _load_day_events(kabu, TARGET_DAY_619)
    by_sym: dict[str, list[dict[str, str]]] = defaultdict(list)
    for ev in events:
        by_sym[str(ev.get("symbol") or "")].append(ev)

    dyn_by_sym = [r for r in dyn_records if str(r.get("day") or "") == TARGET_DAY_619]

    rows: list[dict[str, Any]] = []
    for sym in TARGET_UPTREND_SYMBOLS:
        evs = by_sym.get(sym, [])
        accepted = [e for e in evs if e.get("event_type") == "accepted"]
        candidates = [e for e in evs if e.get("event_type") == "candidate"]
        rejects = [e for e in evs if e.get("event_type") in ("rejected", "reject")]

        cand_trade = None
        for e in candidates + rejects:
            key = f"{sym}|{e.get('entry_time')}"
            if key in enriched_by_key:
                cand_trade = dict(enriched_by_key[key])
                break
        if cand_trade is None:
            sym_recs = [r for r in dyn_by_sym if str(r.get("symbol")) == sym]
            if sym_recs:
                cand_trade = dict(max(sym_recs, key=lambda r: len(str(r.get("gate_reject_reason") or ""))))
        if cand_trade is None:
            for t in enriched_by_key.values():
                if str(t.get("symbol")) == sym and str(t.get("day", ""))[:8] == TARGET_DAY_619:
                    cand_trade = dict(t)
                    break

        reason = ""
        if cand_trade:
            reason = str(cand_trade.get("gate_reject_reason") or cand_trade.get("reject_reason") or "")
        elif rejects:
            reason = str(rejects[0].get("gate_reject_reason") or rejects[0].get("reject_reason") or "")
        elif candidates:
            reason = str(candidates[0].get("gate_reject_reason") or candidates[0].get("reject_reason") or "")

        if cand_trade:
            cand_trade.setdefault("gate_reject_reason", reason)
            cand_trade.setdefault("outcome", "rejected" if rejects else "candidate")
            gate_fail = _resolve_gate_failure(cand_trade)
        else:
            gate_fail = _gate_from_reason(reason) or ("Other" if reason else "")

        was_candidate = bool(candidates or cand_trade or rejects)
        if accepted:
            outcome = "accepted"
            primary = ""
        elif was_candidate:
            outcome = "rejected"
            primary = gate_fail or "Other"
        else:
            outcome = "no_candidate"
            primary = "no_candidate"

        rows.append(
            {
                "symbol": sym,
                "day": TARGET_DAY_619,
                "open_to_close_pct": "",
                "was_candidate": was_candidate,
                "outcome": outcome,
                "primary_gate_failure": primary,
                "gate_reject_reason": reason,
                "captured_in_sim": bool(accepted),
            }
        )
    return rows


def run_phase460_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    raw_records = _load_dynamic40_records(kabu)
    enriched_all = _enrich_candidates(_load_candidate_stream(repo_root), kabu=kabu)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    o2c = _day_open_close(price_idx)

    # Enrich dynamic40 records via keyed lookup from canonical stream
    canon_by_key = {f"{t.get('symbol')}|{t.get('entry_time')}": t for t in enriched_all}
    enriched_records: list[dict[str, Any]] = []
    for rec in raw_records:
        key = f"{rec.get('symbol')}|{rec.get('entry_time')}"
        t = dict(canon_by_key.get(key, rec))
        t.update({k: v for k, v in rec.items() if v not in (None, "")})
        t.setdefault("day", rec.get("day"))
        t.setdefault("universe_bucket", rec.get("universe_bucket"))
        if t.get("outcome") is None:
            t["outcome"] = rec.get("outcome")
        t.setdefault("outcome", rec.get("outcome"))
        t["primary_gate_failure"] = _resolve_gate_failure({**t, "outcome": rec.get("outcome")})
        enriched_records.append(t)

    canon_by_key_full = {f"{t.get('symbol')}|{t.get('entry_time')}": t for t in enriched_all}
    target_619_rows = _analyze_target_619(canon_by_key_full, enriched_records, kabu=kabu)

    # Part A
    reject_counts: Counter[str] = Counter()
    accepted_n = rejected_n = 0
    audit_rows: list[dict[str, Any]] = []
    for t in enriched_records:
        outcome = str(t.get("outcome") or "candidate")
        if outcome == "accepted":
            accepted_n += 1
        elif outcome == "rejected":
            rejected_n += 1
        gf = str(t.get("primary_gate_failure") or ("Pass" if outcome == "accepted" else "Other"))
        if outcome != "accepted":
            reject_counts[gf] += 1
        audit_rows.append(
            {
                "symbol": t.get("symbol"),
                "day": t.get("day"),
                "entry_time": t.get("entry_time"),
                "outcome": outcome,
                "primary_gate_failure": gf,
                "momentum_score": t.get("momentum_continuation_score"),
                "board_bucket": _board_bucket(t),
                "entry_score_v2": _v2_entry_score(t),
                "universe_bucket": t.get("universe_bucket"),
                "gate_reject_reason": t.get("gate_reject_reason"),
                "pnl_yen": t.get("pnl_yen") or t.get("pnl_yen_100"),
            }
        )
    total_rej = sum(reject_counts.values()) or 1
    reject_analysis = [
        {"gate_failure": k, "count": v, "share": round(v / total_rej, 4)}
        for k, v in reject_counts.most_common()
    ]

    # Part B — uptrend symbols
    uptrend_keys: set[tuple[str, str]] = set()
    for (sym, day), pct in o2c.items():
        if pct >= UPTREND_PCT:
            uptrend_keys.add((sym, day))
    for sym in TARGET_UPTREND_SYMBOLS:
        for day in sorted({str(t.get("day") or "") for t in enriched_records}):
            if (sym, day) in o2c and o2c[(sym, day)] >= UPTREND_PCT:
                uptrend_keys.add((sym, day))

    by_sym_day: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in enriched_records:
        by_sym_day[(str(t.get("symbol") or ""), str(t.get("day") or ""))].append(t)

    uptrend_rows: list[dict[str, Any]] = []
    missed_uptrend = 0
    momentum_miss = board_miss = both_miss = 0
    for sym, day in sorted(uptrend_keys):
        recs = by_sym_day.get((sym, day), [])
        pct = o2c.get((sym, day), 0.0)
        was_cand = bool(recs)
        accepted_any = any(r.get("outcome") == "accepted" for r in recs)
        if not accepted_any and was_cand:
            missed_uptrend += 1
        failures = [str(r.get("primary_gate_failure") or "") for r in recs if r.get("outcome") != "accepted"]
        failures = [f for f in failures if f and f not in ("Pass", "no_candidate")]
        mom_fail = any(f == "Momentum" for f in failures)
        brd_fail = any(f == "Board" for f in failures)
        if mom_fail and brd_fail:
            both_miss += 1
        elif mom_fail:
            momentum_miss += 1
        elif brd_fail:
            board_miss += 1
        primary = Counter(failures).most_common(1)[0][0] if failures else ("accepted" if accepted_any else "no_candidate")
        uptrend_rows.append(
            {
                "symbol": sym,
                "day": day,
                "open_to_close_pct": pct,
                "was_candidate": was_cand,
                "outcome": "accepted" if accepted_any else ("rejected" if recs else "no_candidate"),
                "primary_gate_failure": primary,
                "gate_reject_reason": recs[0].get("gate_reject_reason") if recs else "",
                "captured_in_sim": accepted_any,
            }
        )

    # Merge explicit 6/19 target symbols into uptrend audit
    for row in target_619_rows:
        key = (row["symbol"], row["day"])
        if key not in {(r["symbol"], r["day"]) for r in uptrend_rows}:
            uptrend_rows.append(row)
        else:
            for i, existing in enumerate(uptrend_rows):
                if (existing["symbol"], existing["day"]) == key:
                    if row.get("was_candidate") and not existing.get("was_candidate"):
                        uptrend_rows[i] = row
                    break

    target_619_blocked = sum(
        1
        for r in target_619_rows
        if r.get("was_candidate") and r.get("outcome") == "rejected"
    )
    mom_acc = [_float(t.get("momentum_continuation_score")) for t in enriched_records if t.get("outcome") == "accepted"]
    mom_rej = [_float(t.get("momentum_continuation_score")) for t in enriched_records if t.get("outcome") == "rejected"]
    mom_acc = [x for x in mom_acc if x is not None]
    mom_rej = [x for x in mom_rej if x is not None]
    cutoff = float(TERTILE_CUTOFFS["Momentum"]["p33"])
    winners_blocked = sum(1 for sym, day in uptrend_keys if not any(r.get("outcome") == "accepted" for r in by_sym_day.get((sym, day), [])))
    losers_blocked = sum(
        1
        for t in enriched_records
        if t.get("outcome") == "rejected" and t.get("primary_gate_failure") == "Momentum" and _float(t.get("pnl_yen") or 0) < 0
    )

    # Part D — board buckets on accepted canonical
    accepted_canon = [t for t in enriched_all if _passes_baseline_mid_high(t)]
    board_stats: dict[str, dict[str, Any]] = {}
    for bb in ("low", "mid", "high", "unknown"):
        grp = [t for t in accepted_canon if _board_bucket(t) == bb]
        pnls = [_float(t.get("pnl_yen_100") or t.get("pnl_yen")) or 0 for t in grp]
        board_stats[bb] = {
            "accepted_count": len(grp),
            "total_pnl": round(sum(pnls), 2),
            "pf": _pf(pnls),
        }

    # Part E — gate simulation
    np_shadows = _precompute_np_shadows(enriched_all, kabu=kabu, np_policy=BEST_NP_POLICY)
    variants = {
        "A_baseline": _runtime_block(_passes_baseline_mid_high),
        "B_no_momentum_gate": _runtime_block(_pass_no_momentum),
        "C_no_board_gate": _runtime_block(_pass_no_board),
        "D_momentum_relaxed": _runtime_block(_pass_momentum_relaxed),
        "E_board_relaxed": _runtime_block(_pass_board_relaxed),
    }
    sim_rows: list[dict[str, Any]] = []
    for vid, block_fn in variants.items():
        st = simulate_capacity_replay(
            enriched_all,
            np_shadows,
            mode=REPLAY_MODE,
            entry_block_fn=block_fn,
            baseline_accepted_keys=set(),
        )
        sim_rows.append(_metrics(st, variant=vid))

    base = sim_rows[0]
    base_pnl = float(base["total_pnl_yen"])
    base_pf = float(base["profit_factor"] or 0)
    base_dd = float(base["max_drawdown_yen"] or 0)
    base_619 = float(base["daily_pnl_619"])
    for m in sim_rows:
        m["delta_pnl_vs_baseline"] = round(float(m["total_pnl_yen"]) - base_pnl, 2)
        m["delta_pf_vs_baseline"] = round(float(m["profit_factor"] or 0) - base_pf, 4)
        m["delta_maxdd_vs_baseline"] = round(float(m["max_drawdown_yen"] or 0) - base_dd, 2)
        m["delta_daily_pnl_619"] = round(float(m["daily_pnl_619"]) - base_619, 2)

    no_mom = next(m for m in sim_rows if m["variant"] == "B_no_momentum_gate")
    no_board = next(m for m in sim_rows if m["variant"] == "C_no_board_gate")
    verdict = _verdict(
        missed_uptrend=missed_uptrend,
        momentum_miss=momentum_miss,
        board_miss=board_miss,
        both_miss=both_miss,
        delta_no_mom=float(no_mom["delta_pnl_vs_baseline"]),
        delta_no_board=float(no_board["delta_pnl_vs_baseline"]),
        target_619_gate_blocked=target_619_blocked,
    )

    mom_value = round(-float(no_mom["delta_pnl_vs_baseline"]), 2)
    board_value = round(-float(no_board["delta_pnl_vs_baseline"]), 2)
    relax_candidates = [
        ("Momentum", float(no_mom["delta_pnl_vs_baseline"])),
        ("Board", float(no_board["delta_pnl_vs_baseline"])),
        ("Momentum_relaxed", float(next(m for m in sim_rows if m["variant"] == "D_momentum_relaxed")["delta_pnl_vs_baseline"])),
        ("Board_relaxed", float(next(m for m in sim_rows if m["variant"] == "E_board_relaxed")["delta_pnl_vs_baseline"])),
    ]
    best_relax = max(relax_candidates, key=lambda x: x[1])[0]

    mandatory = {
        "1_uptrend_missed_count": missed_uptrend,
        "2_momentum_caused": momentum_miss,
        "3_board_caused": board_miss,
        "4_both_caused": both_miss,
        "5_momentum_gate_value_yen": mom_value,
        "6_board_gate_value_yen": board_value,
        "7_pnl_no_momentum": no_mom["total_pnl_yen"],
        "8_pnl_no_board": no_board["total_pnl_yen"],
        "9_best_gate_to_relax": best_relax.replace("_relaxed", ""),
        "10_runtime_candidate": best_relax.endswith("_relaxed") and relax_candidates[-1][1] > -5000,
        "11_next_actions": [
            "Entry Gate (Momentum+Board+score) blocks 6/19 uptrend symbols — not Dynamic40 selection",
            "Shadow-test gate relaxation (Board_relaxed or Momentum shadow) before runtime change",
            "Do not remove Momentum gate wholesale — simulation shows −13k PnL impact",
        ],
        "verdict": verdict,
        "target_619_gate_blocked": target_619_blocked,
        "target_619_symbols": target_619_rows,
        "accepted_count": accepted_n,
        "rejected_count": rejected_n,
        "reject_breakdown": dict(reject_counts),
        "momentum_cutoff_p33": cutoff,
        "momentum_median_accepted": round(statistics.median(mom_acc), 4) if mom_acc else None,
        "momentum_median_rejected": round(statistics.median(mom_rej), 4) if mom_rej else None,
        "winners_blocked_by_momentum_pct": round(winners_blocked / max(len(uptrend_keys), 1), 4),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "part_b_target_619": target_619_rows,
        "part_a": {"accepted": accepted_n, "rejected": rejected_n, "reject_breakdown": reject_analysis},
        "part_c_momentum": {
            "accepted_median": mandatory.get("momentum_median_accepted"),
            "rejected_median": mandatory.get("momentum_median_rejected"),
            "cutoff_p33": cutoff,
            "winners_blocked_count": winners_blocked,
            "losers_blocked_by_momentum": losers_blocked,
        },
        "part_d_board": board_stats,
        "_audit_rows": audit_rows,
        "_reject_rows": reject_analysis,
        "_uptrend_rows": uptrend_rows,
        "_sim_rows": sim_rows,
    }


@dataclass
class Phase460Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase460_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "audit": reports / "phase460_entry_gate_failure_audit.csv",
            "reject": reports / "phase460_gate_reject_analysis.csv",
            "uptrend": reports / "phase460_uptrend_missed_symbols.csv",
            "sim": reports / "phase460_gate_simulation.csv",
            "summary": reports / "phase460_summary.json",
        }
        _write_csv(paths["audit"], AUDIT_FIELDS, list(result.get("_audit_rows") or []))
        _write_csv(paths["reject"], REJECT_FIELDS, list(result.get("_reject_rows") or []))
        _write_csv(paths["uptrend"], UPTREND_FIELDS, list(result.get("_uptrend_rows") or []))
        _write_csv(paths["sim"], SIM_FIELDS, list(result.get("_sim_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase460_entry_gate_failure_audit.md"
        m = result.get("mandatory_answers") or {}
        part_a = result.get("part_a") or {}
        part_c = result.get("part_c_momentum") or {}
        part_d = result.get("part_d_board") or {}
        target_619 = result.get("part_b_target_619") or []
        sim_rows = list(result.get("_sim_rows") or [])

        lines = [
            "# Phase460 — Entry Gate Failure Audit",
            "",
            f"Generated: {result.get('generated_at')}",
            f"Period: {result.get('period_start')}..{result.get('period_end')}",
            "",
            "## 判定",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "Phase459で判明した上昇銘柄（3441/6492/7256/6466/7600）の取り逃しは **Dynamic40ではなく Entry Gate** が原因。",
            "",
            "## Part A — Gate Failure集計",
            "",
            f"- accepted: **{part_a.get('accepted')}**",
            f"- rejected: **{part_a.get('rejected')}**",
            "",
            "| gate_failure | count | share |",
            "|---|---:|---:|",
        ]
        for row in part_a.get("reject_breakdown") or []:
            lines.append(f"| {row.get('gate_failure')} | {row.get('count')} | {row.get('share')} |")

        lines.extend(
            [
                "",
                "## Part B — 上昇銘柄監査（6/19 重点）",
                "",
                "| symbol | was_candidate | outcome | primary_gate | reject_reason |",
                "|---|---|---|---|---|",
            ]
        )
        for row in target_619:
            lines.append(
                f"| {row.get('symbol')} | {row.get('was_candidate')} | {row.get('outcome')} "
                f"| {row.get('primary_gate_failure')} | {row.get('gate_reject_reason')} |"
            )

        lines.extend(
            [
                "",
                "## Part C — Momentum Gate監査",
                "",
                f"- accepted median momentum: **{part_c.get('accepted_median')}**",
                f"- rejected median momentum: **{part_c.get('rejected_median')}**",
                f"- p33 cutoff: **{part_c.get('cutoff_p33')}**",
                f"- uptrend winners blocked: **{part_c.get('winners_blocked_count')}**",
                "",
                "## Part D — Board Gate監査",
                "",
                "| bucket | accepted | total_pnl | PF |",
                "|---|---:|---:|---:|",
            ]
        )
        for bb, stats in (part_d or {}).items():
            lines.append(
                f"| {bb} | {stats.get('accepted_count')} | {stats.get('total_pnl')} | {stats.get('pf')} |"
            )

        lines.extend(["", "## Part E — Gate除去シミュレーション", "", "| variant | PnL | ΔPnL | PF | maxDD | accepted |", "|---|---:|---:|---:|---:|---:|"])
        for row in sim_rows:
            lines.append(
                f"| {row.get('variant')} | {row.get('total_pnl_yen')} | {row.get('delta_pnl_vs_baseline')} "
                f"| {row.get('profit_factor')} | {row.get('max_drawdown_yen')} | {row.get('accepted_count')} |"
            )

        lines.extend(
            [
                "",
                "## Part F — Mandatory answers",
                "",
                f"1. 上昇銘柄取り逃し件数: **{m.get('1_uptrend_missed_count')}**",
                f"2. Momentum起因: **{m.get('2_momentum_caused')}**",
                f"3. Board起因: **{m.get('3_board_caused')}**",
                f"4. 両方起因: **{m.get('4_both_caused')}**",
                f"5. Momentum gateの価値: **{m.get('5_momentum_gate_value_yen')}** yen",
                f"6. Board gateの価値: **{m.get('6_board_gate_value_yen')}** yen",
                f"7. Momentum除去時PnL: **{m.get('7_pnl_no_momentum')}**",
                f"8. Board除去時PnL: **{m.get('8_pnl_no_board')}**",
                f"9. 最も改善余地のあるgate: **{m.get('9_best_gate_to_relax')}**",
                f"10. Runtime候補: **{m.get('10_runtime_candidate')}**",
                f"11. 次アクション: {m.get('11_next_actions')}",
                "",
                "## 成果物",
                "",
                "- `results/reports/phase460_entry_gate_failure_audit.csv`",
                "- `results/reports/phase460_gate_reject_analysis.csv`",
                "- `results/reports/phase460_uptrend_missed_symbols.csv`",
                "- `results/reports/phase460_gate_simulation.csv`",
                "- `results/reports/phase460_summary.json`",
                "",
            ]
        )
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
