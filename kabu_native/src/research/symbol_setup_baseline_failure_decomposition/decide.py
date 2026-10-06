"""Frozen attribution rules. Applied after the scan. No parameter is chosen from the seven trades."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.symbol_setup_baseline_failure_decomposition import (
    BAR_HORIZONS,
    CASE_IDENTIFIED,
    CASE_UNKNOWN,
    NEXT_GAP,
    NEXT_REPAIR,
    QUOTE_HORIZONS,
)

COVERAGE_FLOOR = 0.5
CELL_FLOOR = 5
ALIGNED_SAMPLE_FLOOR = 10


def _dist(values: list[float], population: int) -> dict[str, Any]:
    if population <= 0:
        return {
            "n": 0, "population": 0, "coverage": None, "mean": None, "median": None,
            "q25": None, "q75": None, "positive_fraction": None, "negative_fraction": None,
        }
    arr = np.asarray(values, dtype=float)
    n = int(arr.size)
    if n == 0:
        return {
            "n": 0, "population": population, "coverage": 0.0, "mean": None, "median": None,
            "q25": None, "q75": None, "positive_fraction": None, "negative_fraction": None,
        }
    return {
        "n": n,
        "population": population,
        "coverage": n / float(population),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "q25": float(np.percentile(arr, 25)),
        "q75": float(np.percentile(arr, 75)),
        "positive_fraction": float(np.mean(arr > 0.0)),
        "negative_fraction": float(np.mean(arr < 0.0)),
    }


def _kind(row: dict[str, Any]) -> str:
    cov = row.get("coverage")
    if cov is None or float(cov) < COVERAGE_FLOOR or row.get("mean") is None or row.get("median") is None:
        return "inconclusive"
    if float(row["mean"]) <= 0.0 and float(row["median"]) <= 0.0:
        return "nonpositive"
    if float(row["mean"]) > 0.0 and float(row["median"]) > 0.0:
        return "positive"
    return "mixed"


def _pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"population": len(rows)}
    for h in QUOTE_HORIZONS:
        vals = [float(r["quote_bps"][str(h)]) for r in rows if r["quote_bps"].get(str(h)) is not None]
        out[f"quote_{h}s"] = _dist(vals, len(rows))
        out[f"quote_{h}s"]["kind"] = _kind(out[f"quote_{h}s"])
    for h in BAR_HORIZONS:
        vals = [float(r["bar_bps"][str(h)]) for r in rows if r["bar_bps"].get(str(h)) is not None]
        out[f"bar_{h}m"] = _dist(vals, len(rows))
        out[f"bar_{h}m"]["kind"] = _kind(out[f"bar_{h}m"])
    return out


def _count_kind(pack: dict[str, Any], prefix: str, keys: tuple[int, ...], kind: str) -> int:
    return sum(1 for h in keys if pack.get(f"{prefix}{h}" if prefix.endswith("_") else f"{prefix}_{h}", pack.get(f"{prefix}{h}", {})).get("kind") == kind)


def _quote_kinds(pack: dict[str, Any]) -> list[str]:
    return [pack[f"quote_{h}s"]["kind"] for h in QUOTE_HORIZONS]


def _bar_kinds(pack: dict[str, Any]) -> list[str]:
    return [pack[f"bar_{h}m"]["kind"] for h in BAR_HORIZONS]


def _majority(kinds: list[str], label: str, need: int) -> bool:
    return sum(1 for k in kinds if k == label) >= need


def _diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Optional[float]]:
    if a.get("mean") is None or b.get("mean") is None:
        return {"mean": None, "median": None}
    return {"mean": float(a["mean"]) - float(b["mean"]), "median": float(a["median"]) - float(b["median"])}


def _bag_pack(bag_n: int, values: dict[str, list[float]]) -> dict[str, Any]:
    out: dict[str, Any] = {"population": bag_n}
    for h in QUOTE_HORIZONS:
        key = f"quote_{h}s_bps"
        out[f"quote_{h}s"] = _dist(list(values.get(key, [])), bag_n)
        out[f"quote_{h}s"]["kind"] = _kind(out[f"quote_{h}s"])
    for h in BAR_HORIZONS:
        key = f"bar_{h}m_bps"
        out[f"bar_{h}m"] = _dist(list(values.get(key, [])), bag_n)
        out[f"bar_{h}m"]["kind"] = _kind(out[f"bar_{h}m"])
    return out


def path_label(trade: dict[str, Any]) -> str:
    """Order is frozen: session close, clean winner, dip then recovery, profit then failure, immediate adverse, flat."""
    if trade.get("exit_reason") == "SESSION_FAIL_CLOSE":
        return "SESSION_STALE"
    realized = trade.get("realized_bps")
    mfe = trade.get("mfe_bps")
    giveback = trade.get("giveback_bps")
    t_mfe = trade.get("time_to_mfe")
    t_mae = trade.get("time_to_mae")
    if realized is None:
        return "OTHER"
    if float(realized) > 0.0 and giveback is not None and float(giveback) <= 1e-6:
        return "CLEAN_WINNER"
    if float(realized) > 0.0 and t_mae is not None and t_mfe is not None and float(t_mae) < float(t_mfe):
        return "DIP_THEN_RECOVERY"
    if float(realized) > 0.0:
        return "OTHER"
    if float(realized) < 0.0 and mfe is not None and float(mfe) > 0.0:
        return "PROFIT_THEN_FAILURE"
    if float(realized) < 0.0:
        return "IMMEDIATE_ADVERSE"
    if float(realized) == 0.0:
        return "NO_PROGRESS"
    return "OTHER"


def _subset_pack(signals: list[dict[str, Any]], *, lineage: Optional[str] = None, fold: Optional[str] = None, board: Optional[bool] = None, drop_winner: bool = False) -> dict[str, Any]:
    rows = signals
    if lineage is not None:
        rows = [r for r in rows if r["lineage"] == lineage]
    if fold is not None:
        rows = [r for r in rows if r["fold"] == fold]
    if board is not None:
        rows = [r for r in rows if bool(r["board_pass"]) is board]
    if drop_winner:
        rows = [r for r in rows if not (r["date"] == "20260821" and r["symbol"] == "285A")]
    return _pack(rows)


def _pending_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _pack(rows)


def _mean_field(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return float(np.mean(vals))


def _early(trade: dict[str, Any]) -> bool:
    if trade.get("thesis_loss_reason") not in {"FAST_SLOW_CROSS_LOSS", "SLOW_TREND_SLOPE_LOSS", "BOTH_TREND_COMPONENTS_LOST"}:
        return False
    mfe = trade.get("mfe_bps")
    recovered = (trade.get("recovery") or {}).get("recovered_within_3")
    post = (trade.get("post_exit") or {}).get("post_exit_180s_bps")
    if post is None:
        post = (trade.get("post_exit") or {}).get("post_exit_300s_bps")
    return bool(mfe is not None and float(mfe) > 0.0 and recovered is True and post is not None and float(post) > 0.0)


def _htf_clear(signals: list[dict[str, Any]]) -> dict[str, Any]:
    obs = [r for r in signals if r.get("htf_observable")]
    aligned = [r for r in obs if r.get("htf_class") == "HTF_TREND_ALIGNED"]
    other = [r for r in obs if r.get("htf_class") != "HTF_TREND_ALIGNED"]
    a = _pack(aligned)
    b = _pack(other)
    bar_better = 0
    bar_positive = 0
    for h in BAR_HORIZONS:
        da, db = a[f"bar_{h}m"], b[f"bar_{h}m"]
        if da.get("median") is not None and db.get("median") is not None and float(da["median"]) > float(db["median"]):
            bar_better += 1
        if da.get("kind") == "positive":
            bar_positive += 1
    folds = {}
    supportive = 0
    for fold in ("FOLD_A", "FOLD_B", "FOLD_C"):
        fa = [r for r in aligned if r["fold"] == fold]
        fb = [r for r in other if r["fold"] == fold]
        fp = _pack(fa)
        folds[fold] = {"aligned_n": len(fa), "not_aligned_observable_n": len(fb), "aligned": fp}
        if len(fa) >= CELL_FLOOR and len(fb) >= CELL_FLOOR:
            pos = sum(1 for h in BAR_HORIZONS if fp[f"bar_{h}m"]["kind"] == "positive")
            if pos >= 2:
                supportive += 1
    clear = bool(len(aligned) >= ALIGNED_SAMPLE_FLOOR and bar_better == 3 and bar_positive >= 2 and supportive >= 2)
    return {
        "observable_n": len(obs),
        "aligned_n": len(aligned),
        "not_aligned_observable_n": len(other),
        "unobservable_n": len(signals) - len(obs),
        "aligned": a,
        "not_aligned_observable": b,
        "bar_horizons_aligned_median_above_other": bar_better,
        "bar_horizons_aligned_positive": bar_positive,
        "folds_with_cell_and_positive_majority": supportive,
        "clear_and_repeatable": clear,
        "folds": folds,
    }


def _lineage_view(signals: list[dict[str, Any]], pending: list[dict[str, Any]], trades: list[dict[str, Any]], lineage: str, drop_winner: bool) -> dict[str, Any]:
    def keep(row: dict[str, Any]) -> bool:
        if row.get("lineage") != lineage:
            return False
        if drop_winner and row.get("date") == "20260821" and row.get("symbol") == "285A":
            return False
        return True

    sig = [r for r in signals if keep(r)]
    pend = [r for r in pending if keep(r)]
    filled_rows = [r for r in pend if r.get("fill_or_expire") == "FILLED"]
    done = [r for r in trades if keep(r)]
    return {
        "candidate_signal": _pack(sig),
        "board_pass": _pack([r for r in sig if r.get("board_pass")]),
        "pending": _pack(pend),
        "fill_signal_response": _pack(filled_rows),
        "fill_trade_n": len(done),
        "fill_net_pnl_yen": float(sum(float(r["pnl_yen_100"]) for r in done)),
    }


def analyze(scanned: dict[str, Any]) -> dict[str, Any]:
    signals = scanned["signals"]
    pending = scanned["pending"]
    trades = scanned["trades"]
    for trade in trades:
        trade["path_label"] = path_label(trade)
        trade["exit_too_early_conjunction"] = _early(trade)
    signal_pack = _pack(signals)
    groups = {
        "ORIGINAL18": _subset_pack(signals, lineage="ORIGINAL18"),
        "EXTENSION17": _subset_pack(signals, lineage="EXTENSION17"),
        "FOLD_A": _subset_pack(signals, fold="FOLD_A"),
        "FOLD_B": _subset_pack(signals, fold="FOLD_B"),
        "FOLD_C": _subset_pack(signals, fold="FOLD_C"),
    }
    board_pass = [r for r in signals if r["board_pass"]]
    board_veto = [r for r in signals if not r["board_pass"]]
    pass_pack = _pack(board_pass)
    veto_pack = _pack(board_veto)
    board_diff = {
        f"quote_{h}s": _diff(pass_pack[f"quote_{h}s"], veto_pack[f"quote_{h}s"]) for h in QUOTE_HORIZONS
    }
    board_diff.update({f"bar_{h}m": _diff(pass_pack[f"bar_{h}m"], veto_pack[f"bar_{h}m"]) for h in BAR_HORIZONS})
    filled = [r for r in pending if r["fill_or_expire"] == "FILLED"]
    expired = [r for r in pending if r["fill_or_expire"] == "EXPIRED"]
    filled_pack = _pending_pack(filled)
    expired_pack = _pending_pack(expired)
    fill_diff = {f"quote_{h}s": _diff(filled_pack[f"quote_{h}s"], expired_pack[f"quote_{h}s"]) for h in QUOTE_HORIZONS}
    stages = []
    for name, bags in scanned["stages"].items():
        inp = _bag_pack(bags["input"].n, bags["input"].values)
        pas = _bag_pack(bags["pass"].n, bags["pass"].values)
        stages.append(
            {
                "stage": name,
                "input_event_n": bags["input"].n,
                "pass_event_n": bags["pass"].n,
                "pass_rate": (bags["pass"].n / bags["input"].n) if bags["input"].n else None,
                "input": inp,
                "pass": pas,
                "pass_minus_input_median_quote_180s": _diff(pas["quote_180s"], inp["quote_180s"])["median"],
                "pass_minus_input_median_bar_3m": _diff(pas["bar_3m"], inp["bar_3m"])["median"],
            }
        )
    htf = _htf_clear(signals)
    bar_weak = _majority(_bar_kinds(signal_pack), "nonpositive", 2)
    quote_weak = _majority(_quote_kinds(signal_pack), "nonpositive", 3)
    bar_usable = _majority(_bar_kinds(signal_pack), "positive", 2)
    folds_bar_positive = sum(1 for name in ("FOLD_A", "FOLD_B", "FOLD_C") if _majority(_bar_kinds(groups[name]), "positive", 2))
    edge_met = bool(bar_weak and quote_weak and folds_bar_positive < 2)
    board_quote_not_better = sum(
        1 for h in QUOTE_HORIZONS if board_diff[f"quote_{h}s"]["median"] is not None and float(board_diff[f"quote_{h}s"]["median"]) <= 0.0
    )
    board_bar_not_better = sum(
        1 for h in BAR_HORIZONS if board_diff[f"bar_{h}m"]["median"] is not None and float(board_diff[f"bar_{h}m"]["median"]) <= 0.0
    )
    board_does_not_improve = bool(board_quote_not_better >= 3 and board_bar_not_better >= 2)
    upstream_ok = bool(bar_usable and not edge_met)
    board_met = bool(upstream_ok and board_does_not_improve)
    pass_usable = _majority(_quote_kinds(pass_pack), "positive", 3)
    worse = 0
    comparable = 0
    for h in QUOTE_HORIZONS:
        d = fill_diff[f"quote_{h}s"]
        if d["mean"] is None or d["median"] is None:
            continue
        comparable += 1
        if float(d["mean"]) < 0.0 and float(d["median"]) < 0.0:
            worse += 1
    if comparable < 3:
        adverse = "insufficient"
    elif worse >= 3:
        adverse = "true"
    elif worse == 0:
        adverse = "false"
    else:
        adverse = "insufficient"
    passive_pattern = adverse == "true"
    passive_met = bool(upstream_ok and not board_met and pass_usable and passive_pattern)
    thesis_losses = [t for t in trades if t.get("thesis_loss_reason")]
    early_ids = [f"{t['date']}:{t['symbol']}" for t in thesis_losses if t["exit_too_early_conjunction"]]
    early_pattern = bool(thesis_losses and len(early_ids) * 2 >= len(thesis_losses))
    early_met = bool(upstream_ok and not board_met and not passive_met and pass_usable and early_pattern)
    session = [t for t in trades if t.get("exit_reason") == "SESSION_FAIL_CLOSE"]
    session_loss = sum(float(t["pnl_yen_100"]) for t in session)
    gross_loss = sum(float(t["pnl_yen_100"]) for t in trades if float(t["pnl_yen_100"]) < 0.0)
    late_pattern = bool(gross_loss < 0.0 and abs(session_loss) > abs(gross_loss) * 0.5)
    late_met = bool(upstream_ok and not board_met and not passive_met and not early_met and late_pattern)
    timeframe_met = bool(bar_weak and htf["clear_and_repeatable"])
    # Earliest sufficient layer. A repeatable 5-minute split is the specific reading of a weak 1-minute population.
    met: list[str] = []
    if timeframe_met:
        met.append("TIMEFRAME_MIXING")
    elif edge_met:
        met.append("SYMBOL_SETUP_EDGE_NOT_ESTABLISHED")
    elif board_met:
        met.append("BOARD_FILTER_NOT_HELPFUL")
    elif passive_met:
        met.append("PASSIVE_FILL_ADVERSE_SELECTION")
    elif early_met:
        met.append("THESIS_EXIT_TOO_EARLY")
    elif late_met:
        met.append("THESIS_EXIT_TOO_LATE_OR_INCOMPLETE")
    competing = {
        "SYMBOL_SETUP_EDGE_NOT_ESTABLISHED": edge_met,
        "BOARD_FILTER_NOT_HELPFUL": board_met,
        "PASSIVE_FILL_ADVERSE_SELECTION": passive_met,
        "THESIS_EXIT_TOO_EARLY": early_met,
        "THESIS_EXIT_TOO_LATE_OR_INCOMPLETE": late_met,
        "TIMEFRAME_MIXING": timeframe_met,
        "STRUCTURE_LAYER_MISSING": False,
    }
    if not met:
        primary = "PRIMARY_DEFICIENCY_NOT_IDENTIFIED"
        verdict, nxt = CASE_UNKNOWN, NEXT_GAP
    else:
        primary = met[0]
        verdict, nxt = CASE_IDENTIFIED, NEXT_REPAIR
    slope = [t for t in trades if t.get("exit_reason") == "SLOW_TREND_SLOPE_LOSS"]
    return {
        "verdict": verdict,
        "next": nxt,
        "primary_deficiency": primary,
        "criterion_met": competing,
        "selected_by_priority": met,
        "signal_response": signal_pack,
        "signal_by_group": groups,
        "stages": stages,
        "board": {"pass": pass_pack, "veto": veto_pack, "pass_minus_veto": board_diff, "veto_reasons": scanned["veto_reasons"]},
        "pending": {
            "filled": filled_pack,
            "expired": expired_pack,
            "filled_minus_expired": fill_diff,
            "filled_mean_spread_bps": _mean_field(filled, "signal_spread_bps"),
            "expired_mean_spread_bps": _mean_field(expired, "signal_spread_bps"),
            "filled_mean_ask_bid_qty_ratio": _mean_field(filled, "ask_bid_qty_ratio"),
            "expired_mean_ask_bid_qty_ratio": _mean_field(expired, "ask_bid_qty_ratio"),
            "adverse_selection_supported": adverse,
            "adverse_horizons_worse": worse,
            "adverse_horizons_comparable": comparable,
            "adverse_selection_sample_size": "filled_n=7; a consistent gap is reported, and it is not treated as a precise effect size",
        },
        "lineage": {
            "ORIGINAL18": _lineage_view(signals, pending, trades, "ORIGINAL18", False),
            "EXTENSION17": _lineage_view(signals, pending, trades, "EXTENSION17", False),
            "EXTENSION17_excluding_285A_20260821": _lineage_view(signals, pending, trades, "EXTENSION17", True),
        },
        "htf": htf,
        "patterns": {
            "bar_weak": bar_weak,
            "quote_weak": quote_weak,
            "bar_usable": bar_usable,
            "folds_with_positive_bar_majority": folds_bar_positive,
            "board_does_not_improve": board_does_not_improve,
            "board_pass_quote_usable": pass_usable,
            "passive_fill_worse_pattern": passive_pattern,
            "exit_early_pattern": early_pattern,
            "exit_early_n": len(early_ids),
            "thesis_loss_n": len(thesis_losses),
            "exit_late_pattern": late_pattern,
            "timeframe_clear": bool(htf["clear_and_repeatable"]),
        },
        "early_trade_ids": early_ids,
        "slope_temporary_recovery_n": sum(1 for t in slope if (t.get("recovery") or {}).get("recovered_within_3") is True),
        "session_loss_yen": session_loss,
        "gross_loss_yen": gross_loss,
        "late_met": late_met,
        "structure_status": "STRUCTURE_DIAGNOSTIC_NOT_RUN",
    }
