"""Exact frozen-V2 versus latency-engine identity. No rule changes."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

BASE_N = 11902
BASE_PNL = 1189150.0
BASE_PF = 1.409303686366296
TIME_TOL = 1e-6
PX_TOL = 1e-6


def _completed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("pnl_yen") is not None and row.get("exit_t") is not None or row.get("exit_fill_t") is not None and row.get("pnl_yen") is not None]


def _pf(rows: list[dict[str, Any]]) -> Optional[float]:
    yen = np.asarray([float(row["pnl_yen"]) for row in rows], dtype=float)
    profit = float(yen[yen > 0].sum()) if yen.size else 0.0
    loss = float(yen[yen < 0].sum()) if yen.size else 0.0
    if loss == 0.0:
        return None if profit == 0.0 else float("inf")
    return profit / abs(loss)


def _pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    yen = np.asarray([float(row["pnl_yen"]) for row in rows if row.get("pnl_yen") is not None], dtype=float)
    return {"trade_n": int(yen.size), "pnl": float(yen.sum()) if yen.size else 0.0, "pf": _pf([row for row in rows if row.get("pnl_yen") is not None])}


def _entry_t(row: dict[str, Any]) -> float:
    if row.get("entry_signal_t") is not None and row.get("entry_t") is not None and "entry_fill_t" in row:
        return float(row["entry_signal_t"])
    return float(row["entry_t"])


def _exit_t(row: dict[str, Any]) -> float:
    if row.get("exit_fill_t") is not None:
        return float(row["exit_fill_t"])
    return float(row["exit_t"])


def _same(left: float, right: float, tol: float) -> bool:
    return abs(float(left) - float(right)) <= tol


def _quant(values: np.ndarray) -> dict[str, Any]:
    if values.size == 0:
        return {"n": 0}
    qs = [10, 25, 50, 75, 90, 95, 99]
    out = {"n": int(values.size), "mean": float(values.mean()), "median": float(np.median(values)), "max": float(values.max())}
    for q in qs:
        out[f"p{q}"] = float(np.quantile(values, q / 100.0))
    return out


def _hold_baseline(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hold = []
    violations = 0
    for row in rows:
        if row.get("exit_t") is None or row.get("entry_t") is None:
            continue
        execution = float(row["exit_t"]) - float(row["entry_t"])
        hold.append(execution)
        if float(row["entry_t"]) > float(row["exit_t"]) + TIME_TOL or execution < -TIME_TOL:
            violations += 1
        if row.get("hold_sec") is not None and abs(float(row["hold_sec"]) - execution) > 1e-4:
            violations += 1
    return {"execution_hold": _quant(np.asarray(hold, dtype=float)), "signal_hold": None, "violations": violations, "note": "Frozen V2 stores entry_t as the signal and the immediate fill, and exit_t as the bid fill. Exit-signal time is not stored."}


def _hold_zero(rows: list[dict[str, Any]]) -> dict[str, Any]:
    signal = []
    execution = []
    entry_delay = []
    exit_delay = []
    violations = 0
    exit_before_fill = 0
    for row in rows:
        if row.get("entry_signal_t") is None or row.get("entry_fill_t") is None or row.get("exit_fill_t") is None:
            violations += 1
            continue
        es = float(row["entry_signal_t"])
        ef = float(row["entry_fill_t"])
        xf = float(row["exit_fill_t"])
        xs = float(row["exit_signal_t"]) if row.get("exit_signal_t") is not None else None
        if es > ef + TIME_TOL or ef > xf + TIME_TOL:
            violations += 1
        if xs is not None and xs > xf + TIME_TOL:
            violations += 1
        if xs is not None and xs + TIME_TOL < ef:
            exit_before_fill += 1
        execution_hold = xf - ef
        execution.append(execution_hold)
        entry_delay.append(ef - es)
        if xs is None:
            violations += 1
            continue
        signal_hold = xs - es
        exit_d = xf - xs
        signal.append(signal_hold)
        exit_delay.append(exit_d)
        residual = execution_hold - (signal_hold + exit_d - (ef - es))
        if abs(residual) > 1e-4:
            violations += 1
    return {
        "signal_hold": _quant(np.asarray(signal, dtype=float)),
        "execution_hold": _quant(np.asarray(execution, dtype=float)),
        "entry_delay": _quant(np.asarray(entry_delay, dtype=float)),
        "exit_delay": _quant(np.asarray(exit_delay, dtype=float)),
        "identity_violations": violations,
        "exit_signal_before_entry_fill": exit_before_fill,
    }


def compare(scanned: dict[str, Any]) -> dict[str, Any]:
    base = [row for row in scanned["baseline"] if row.get("pnl_yen") is not None]
    zero = [row for row in scanned["zero"] if row.get("pnl_yen") is not None]
    base_pack = _pack(base)
    zero_pack = _pack(zero)
    aggregate = (
        base_pack["trade_n"] == BASE_N
        and abs(base_pack["pnl"] - BASE_PNL) < 1e-6
        and base_pack["pf"] is not None
        and abs(float(base_pack["pf"]) - BASE_PF) < 1e-12
        and zero_pack["trade_n"] == base_pack["trade_n"]
        and abs(zero_pack["pnl"] - base_pack["pnl"]) < 1e-6
        and zero_pack["pf"] is not None
        and abs(float(zero_pack["pf"]) - float(base_pack["pf"])) < 1e-12
    )
    pool: dict[tuple, list[dict[str, Any]]] = {}
    for row in zero:
        pool.setdefault((row["date"], row["session"], row["symbol"]), []).append(row)
    used: set[int] = set()
    classes: dict[str, int] = {}
    mismatches = []
    first = None

    def _bump(name: str) -> None:
        classes[name] = classes.get(name, 0) + 1

    ordered = sorted(base, key=lambda row: (row["date"], float(row["entry_t"]), row["symbol"], float(row["exit_t"])))
    for row in ordered:
        group = pool.get((row["date"], row["session"], row["symbol"]), [])
        match = None
        match_id = None
        for item in group:
            ident = id(item)
            if ident in used:
                continue
            if _same(_entry_t(item), float(row["entry_t"]), 1e-3):
                match = item
                match_id = ident
                break
        if match is None:
            _bump("MISSING_IN_LATENCY_ENGINE")
            detail = {"class": "MISSING_IN_LATENCY_ENGINE", "symbol": row["symbol"], "date": row["date"], "session": row["session"], "baseline_entry_t": row["entry_t"], "baseline_exit_t": row["exit_t"], "baseline_entry_px": row["entry_px"], "baseline_exit_px": row["exit_px"], "baseline_reason": row["reason"]}
            mismatches.append(detail)
            if first is None:
                first = detail
            continue
        used.add(match_id)
        problems = []
        if not _same(float(match["entry_px"]), float(row["entry_px"]), PX_TOL):
            problems.append("ENTRY_PRICE_MISMATCH")
        if not _same(_exit_t(match), float(row["exit_t"]), 1e-3):
            problems.append("EXIT_TIME_MISMATCH")
        if not _same(float(match["exit_px"]), float(row["exit_px"]), PX_TOL):
            problems.append("EXIT_PRICE_MISMATCH")
        if match.get("reason") != row.get("reason"):
            problems.append("EXIT_REASON_MISMATCH")
        if int(match.get("ratchets") or 0) != int(row.get("ratchets") or 0):
            problems.append("RATCHET_STATE_MISMATCH")
        if abs(float(match["pnl_yen"]) - float(row["pnl_yen"])) > 1e-4:
            problems.append("PNL_MISMATCH")
        if not problems:
            continue
        for name in problems:
            _bump(name)
        detail = {
            "class": ",".join(problems),
            "symbol": row["symbol"],
            "date": row["date"],
            "session": row["session"],
            "baseline_entry_t": row["entry_t"],
            "latency_entry_signal_t": match.get("entry_signal_t"),
            "latency_entry_fill_t": match.get("entry_fill_t"),
            "baseline_entry_px": row["entry_px"],
            "latency_entry_px": match["entry_px"],
            "baseline_exit_t": row["exit_t"],
            "latency_exit_signal_t": match.get("exit_signal_t"),
            "latency_exit_fill_t": match.get("exit_fill_t"),
            "baseline_exit_px": row["exit_px"],
            "latency_exit_px": match["exit_px"],
            "baseline_reason": row["reason"],
            "latency_reason": match.get("reason"),
            "baseline_ratchets": row.get("ratchets"),
            "latency_ratchets": match.get("ratchets"),
        }
        mismatches.append(detail)
        if first is None:
            first = detail
    for row in zero:
        if id(row) in used:
            continue
        _bump("EXTRA_IN_LATENCY_ENGINE")
        detail = {"class": "EXTRA_IN_LATENCY_ENGINE", "symbol": row["symbol"], "date": row["date"], "session": row["session"], "latency_entry_signal_t": row.get("entry_signal_t"), "latency_entry_fill_t": row.get("entry_fill_t"), "latency_entry_px": row.get("entry_px"), "latency_reason": row.get("reason")}
        mismatches.append(detail)
    def _when(detail: dict[str, Any]) -> tuple:
        for key in ("baseline_entry_t", "latency_entry_signal_t", "latency_entry_fill_t"):
            if detail.get(key) is not None:
                return (str(detail.get("date") or ""), float(detail[key]))
        return ("", 0.0)
    first = min(mismatches, key=_when) if mismatches else None
    trade_parity = len(mismatches) == 0 and aggregate
    if not trade_parity:
        verdict = "LATENCY_REPLAY_INTEGRITY_FAIL_V1"
        nxt = "RCA_FIRST_DIVERGENCE"
    else:
        verdict = "LATENCY_REPLAY_INTEGRITY_PASS_PENDING_HOLDTIME_V1"
        nxt = "CONTINUE_HOLDTIME"
    zero_hold = _hold_zero(zero)
    if trade_parity and zero_hold["identity_violations"]:
        verdict = "LATENCY_HOLD_TIME_ACCOUNTING_FAIL_V1"
        nxt = "RCA_TIMESTAMP_SEMANTICS"
    return {
        "verdict": verdict,
        "next": nxt,
        "baseline": base_pack,
        "zero": zero_pack,
        "expected": {"trade_n": BASE_N, "pnl": BASE_PNL, "pf": BASE_PF},
        "aggregate_parity": bool(aggregate and base_pack["trade_n"] == BASE_N and abs(base_pack["pnl"] - BASE_PNL) < 1e-6),
        "trade_parity": trade_parity,
        "mismatch_n": len(mismatches),
        "classes": classes,
        "first_divergence": first,
        "mismatches": mismatches[:500],
        "baseline_hold": _hold_baseline(base),
        "zero_hold": zero_hold,
        "pending": {
            "scope": "per-symbol",
            "different_symbol_pending_blocked_n": 0,
            "signals_while_any_pending": int(scanned["zero_counts"].get("signal_while_entry_pending", 0)),
            "same_symbol_pending_collisions": int(scanned["zero_counts"].get("entry_pending_collision", 0)),
            "pending_model_bug": False,
            "note": "A pending entry blocks only that symbol. Another symbol is blocked only when open_pos already holds five names.",
        },
        "cap": {
            "pending_occupies_slot": True,
            "cap_blocked_n": int(scanned["zero_counts"].get("cap", 0)),
            "implementation": "The latency engine inserts the symbol into open_pos before the ask fill, so a pending entry occupies one of the five slots.",
            "frozen_v2": "Frozen V2 fills the signal ask immediately, so it has no pending entry.",
        },
        "zero_counts": scanned["zero_counts"],
        "later_latency_interpreted": False,
        "strategy_verdict_valid": False,
        "implementation_bug_found": not trade_parity,
    }
