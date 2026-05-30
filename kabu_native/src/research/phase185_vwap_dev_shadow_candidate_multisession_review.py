"""
Phase185: Multi-session review of vwap_dev shadow reject candidate (review only).

Fixed scenarios:
  A — current (all trades)
  B — post-hoc exclude entry_vwap_dev_pct >= 2.5%
  C — post-hoc exclude entry_vwap_dev_pct >= 3.0%
  D — diagnostic: entry_vwap_dev_pct >= 2.5% AND r30_sec < 0

No hard reject, no parameter search, no prod YAML changes.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from research.phase181_entry_expectancy_review import (
    _float,
    _load_events,
    _mean,
    _pair_trades,
    _parse_ts,
    _pf,
    _price_at_offset,
    _return_pct,
)
from small_paper.extended_entry_shadow import (
    VWAP_DEV_PCT_MIN,
    append_price_tick,
    compute_entry_shadow_fields,
)

VWAP_DEV_THRESHOLD_B = VWAP_DEV_PCT_MIN  # 2.5 — Phase183/184 fixed
VWAP_DEV_THRESHOLD_C = 3.0  # fixed second scenario (not tuned)

FOCUS_SYMBOLS = frozenset({"6203.T", "6659.T", "9348.T", "4888.T"})
COMPARE_SYMBOLS = frozenset({"3687.T", "4392.T", "7885.T"})

REFERENCE_SESSIONS = (
    "20260519/live_full_session_081047",
    "20260520/live_full_session_080745",
    "20260520/push_replay_001932",
    "20260520/push_replay_231314",
    "20260521/live_full_session_081418",
    "20260522/live_full_session_081229",
    "20260525/live_session_075733",
)

OBSERVER_EXIT_SESSIONS = (
    "20260529/live_session_075135",
    "20260529/live_session_122541",
    "20260529/push_replay_002526",
    "20260529/push_replay_003645",
)


@dataclass
class VwapReviewTrade:
    session_id: str
    day_stamp: str
    symbol: str
    entry_time: str
    entry_ts: float
    pnl_pct: float
    exit_reason: str
    mfe_pct: Optional[float]
    entry_vwap_dev_pct: Optional[float]
    r30_sec: Optional[float]

    def vwap_at_least(self, threshold: float) -> bool:
        v = self.entry_vwap_dev_pct
        return v is not None and v >= threshold

    def is_scenario_d(self) -> bool:
        return self.vwap_at_least(VWAP_DEV_THRESHOLD_B) and self.r30_sec is not None and self.r30_sec < 0


def _day_stamp_from_session(session_dir: Path) -> str:
    return session_dir.parent.name


def _session_id(session_dir: Path, base: Path) -> str:
    return str(session_dir.relative_to(base)).replace("\\", "/")


def _load_bounded_push_ticks(
    push_path: Path,
    *,
    ts_lo: float,
    ts_hi: float,
) -> list[tuple[float, dict[str, Any]]]:
    """Stream push jsonl; retain ticks needed for entry vwap + r30 (bounded window)."""
    if not push_path.is_file():
        return []
    kept: list[tuple[float, dict[str, Any]]] = []
    last_before: Optional[tuple[float, dict[str, Any]]] = None
    with push_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(str(rec.get("recorded_at") or ""))
            payload = rec.get("payload") or {}
            if ts < ts_lo:
                last_before = (ts, payload)
                continue
            if ts > ts_hi:
                break
            if last_before is not None:
                kept.append(last_before)
                last_before = None
            kept.append((ts, payload))
    if last_before is not None and not kept:
        kept.append(last_before)
    elif last_before is not None and kept and kept[0][0] > ts_lo:
        kept.insert(0, last_before)
    return kept


def _push_path_for_symbol(push_dir: Path, symbol: str) -> Path:
    path = push_dir / f"{symbol}.jsonl"
    if path.is_file():
        return path
    return push_dir / f"{symbol.replace('.T', '')}.jsonl"


def _enrich_trade_vwap_r30(
    *,
    entry_ts: float,
    entry_px: float,
    close_ts: float,
    push_ticks: Sequence[tuple[float, dict[str, Any]]],
) -> tuple[Optional[float], Optional[float]]:
    payload = _payload_at_or_before(push_ticks, entry_ts)
    if payload.get("CurrentPrice") is None:
        payload["CurrentPrice"] = entry_px
    vwap_dev = _vwap_dev_from_payload(entry_px, payload)
    if vwap_dev is None:
        ring = _load_price_ring_from_series(push_ticks, up_to_ts=entry_ts)
        if ring:
            shadow = compute_entry_shadow_fields(
                trade={"current_price": entry_px, "rolling_mfe_pct": 0},
                payload=payload,
                price_ring=ring,
                entry_ts=entry_ts,
                session_momentum_samples=[],
            )
            vwap_dev = _float(shadow.get("entry_vwap_dev_pct"))
    price_series = _price_series_from_push(push_ticks)
    r30 = _return_pct(
        entry_px, _price_at_offset(price_series, entry_ts, entry_px, 30, end_ts=close_ts)
    )
    return vwap_dev, round(r30, 4) if r30 is not None else None


def _bounded_ticks_for_trades(
    push_dir: Path,
    symbol: str,
    entry_times: Sequence[float],
) -> list[tuple[float, dict[str, Any]]]:
    if not entry_times:
        return []
    ts_lo = min(entry_times) - 660.0
    ts_hi = max(entry_times) + 120.0
    return _load_bounded_push_ticks(_push_path_for_symbol(push_dir, symbol), ts_lo=ts_lo, ts_hi=ts_hi)


def _payload_at_or_before(series: Sequence[tuple[float, dict[str, Any]]], entry_ts: float) -> dict[str, Any]:
    payload: dict[str, Any] = {"CurrentPrice": None, "VWAP": None, "HighPrice": None}
    for ts, pld in series:
        if ts > entry_ts:
            break
        payload = {
            "CurrentPrice": pld.get("CurrentPrice") or payload.get("CurrentPrice"),
            "VWAP": pld.get("VWAP"),
            "HighPrice": pld.get("HighPrice"),
        }
    return payload


def _load_push_payload_at_entry(push_path: Path, entry_ts: float) -> dict[str, Any]:
    ticks = _load_bounded_push_ticks(push_path, ts_lo=entry_ts - 660.0, ts_hi=entry_ts)
    return _payload_at_or_before(ticks, entry_ts)


def _load_price_ring_from_series(
    series: Sequence[tuple[float, dict[str, Any]]],
    *,
    up_to_ts: float,
) -> list[tuple[float, float]]:
    ring: list[tuple[float, float]] = []
    for ts, payload in series:
        if ts > up_to_ts:
            break
        try:
            px = float(payload.get("CurrentPrice") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if px > 0:
            append_price_tick(ring, ts=ts, px=px)
    return ring


def _load_price_ring(push_path: Path, *, up_to_ts: float) -> list[tuple[float, float]]:
    ticks = _load_bounded_push_ticks(push_path, ts_lo=up_to_ts - 660.0, ts_hi=up_to_ts)
    return _load_price_ring_from_series(ticks, up_to_ts=up_to_ts)


def _vwap_dev_from_payload(entry_px: float, payload: dict[str, Any]) -> Optional[float]:
    vwap = _float(payload.get("VWAP"))
    if vwap and vwap > 0 and entry_px > 0:
        return round((entry_px - vwap) / vwap * 100.0, 4)
    return None


def _load_structural_trades(
    session_dir: Path,
    *,
    repo_root: Path,
    base: Path,
) -> list[VwapReviewTrade]:
    trades_csv = session_dir / "structural_trades.csv"
    if not trades_csv.is_file():
        return []
    day_stamp = _day_stamp_from_session(session_dir)
    sid = _session_id(session_dir, base)
    y = f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}"
    push_dir = repo_root / "kabu_native" / "data" / "push_jsonl" / y

    raw_rows: list[dict[str, Any]] = []
    with trades_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            ent = str(row.get("entry_time") or "").strip()
            if sym and ent:
                raw_rows.append(row)

    by_sym: dict[str, list[dict[str, Any]]] = {}
    for row in raw_rows:
        by_sym.setdefault(str(row["symbol"]).strip(), []).append(row)

    tick_cache: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    for sym, rows in by_sym.items():
        entry_times = [_parse_ts(str(r.get("entry_time") or "")) for r in rows]
        tick_cache[sym] = _bounded_ticks_for_trades(push_dir, sym, entry_times)

    out: list[VwapReviewTrade] = []
    for row in raw_rows:
        sym = str(row.get("symbol") or "").strip()
        ent = str(row.get("entry_time") or "").strip()
        ent_ts = _parse_ts(ent)
        entry_px = _float(row.get("entry_price")) or 0.0
        close_ts = _parse_ts(str(row.get("close_time") or "")) or ent_ts + 300
        push_ticks = tick_cache.get(sym, [])
        vwap_dev, r30 = _enrich_trade_vwap_r30(
            entry_ts=ent_ts,
            entry_px=entry_px,
            close_ts=close_ts,
            push_ticks=push_ticks,
        )
        pnl = _float(row.get("realized_pnl_pct")) or 0.0
        mfe = _float(row.get("mfe_pct"))
        out.append(
            VwapReviewTrade(
                session_id=sid,
                day_stamp=day_stamp,
                symbol=sym,
                entry_time=ent,
                entry_ts=ent_ts,
                pnl_pct=float(pnl),
                exit_reason=str(row.get("close_reason") or ""),
                mfe_pct=mfe,
                entry_vwap_dev_pct=vwap_dev,
                r30_sec=r30,
            )
        )
    return out


def _load_observer_trades(
    session_dir: Path,
    *,
    repo_root: Path,
    base: Path,
) -> list[VwapReviewTrade]:
    if not (session_dir / "small_paper_events.jsonl").is_file():
        return []
    day_stamp = _day_stamp_from_session(session_dir)
    sid = _session_id(session_dir, base)
    y = f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}"
    push_dir = repo_root / "kabu_native" / "data" / "push_jsonl" / y

    events = _load_events(session_dir)
    pairs = _pair_trades(events)
    raw: list[dict[str, Any]] = []
    for acc, ex in pairs:
        sym = str(acc.get("symbol") or "")
        ent = str(acc.get("entry_time") or "")
        ent_ts = _parse_ts(ent)
        raw.append({"acc": acc, "ex": ex, "sym": sym, "ent": ent, "ent_ts": ent_ts})

    by_sym: dict[str, list[dict[str, Any]]] = {}
    for item in raw:
        by_sym.setdefault(item["sym"], []).append(item)

    tick_cache: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    for sym, items in by_sym.items():
        tick_cache[sym] = _bounded_ticks_for_trades(push_dir, sym, [x["ent_ts"] for x in items])

    out: list[VwapReviewTrade] = []
    for item in raw:
        acc, ex = item["acc"], item["ex"]
        sym, ent, ent_ts = item["sym"], item["ent"], item["ent_ts"]
        ext = str(ex.get("exit_time") or "")
        ex_ts = _parse_ts(ext) or ent_ts + 300
        entry_px = _float(ex.get("entry_price")) or _float(acc.get("current_price")) or 0.0
        pnl = _float(ex.get("pnl_pct"))
        if pnl is None and entry_px > 0:
            exit_px = _float(ex.get("exit_price")) or entry_px
            pnl = (exit_px - entry_px) / entry_px * 100.0
        vwap_dev, r30 = _enrich_trade_vwap_r30(
            entry_ts=ent_ts,
            entry_px=entry_px,
            close_ts=ex_ts,
            push_ticks=tick_cache.get(sym, []),
        )
        out.append(
            VwapReviewTrade(
                session_id=sid,
                day_stamp=day_stamp,
                symbol=sym,
                entry_time=ent,
                entry_ts=ent_ts,
                pnl_pct=float(pnl or 0.0),
                exit_reason=str(ex.get("exit_reason") or ""),
                mfe_pct=_float(ex.get("max_mfe_until_exit")),
                entry_vwap_dev_pct=vwap_dev,
                r30_sec=r30,
            )
        )
    return out


def _price_series_from_push(
    push_series: Sequence[tuple[float, dict[str, Any]]],
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    last_px: Optional[float] = None
    for ts, payload in push_series:
        px = _float(payload.get("CurrentPrice")) or _float(payload.get("CalcPrice"))
        if px is None or px <= 0:
            if last_px is not None:
                px = last_px
            else:
                continue
        last_px = float(px)
        out.append((ts, last_px))
    return out


def discover_sessions(base: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    """Return evaluable session dirs and exclusion notes."""
    candidates: list[Path] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()

    for rel in (*REFERENCE_SESSIONS, *OBSERVER_EXIT_SESSIONS):
        p = base / rel
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if not p.is_dir():
            excluded.append({"session_id": rel, "reason": "missing_session_dir"})
            continue
        if rel in REFERENCE_SESSIONS:
            if not (p / "structural_trades.csv").is_file():
                excluded.append({"session_id": rel, "reason": "missing_structural_trades"})
                continue
        elif not (p / "small_paper_events.jsonl").is_file():
            excluded.append({"session_id": rel, "reason": "missing_events_jsonl"})
            continue
        candidates.append(p)

    candidates.sort(key=lambda x: str(x))
    return candidates, excluded


def load_session_trades(
    session_dir: Path,
    *,
    repo_root: Path,
    base: Path,
) -> list[VwapReviewTrade]:
    if (session_dir / "structural_trades.csv").is_file():
        return _load_structural_trades(session_dir, repo_root=repo_root, base=base)
    return _load_observer_trades(session_dir, repo_root=repo_root, base=base)


def _excluded_by_vwap(trades: Sequence[VwapReviewTrade], threshold: float) -> list[VwapReviewTrade]:
    return [t for t in trades if t.vwap_at_least(threshold)]


def _kept_after_vwap_exclude(trades: Sequence[VwapReviewTrade], threshold: float) -> list[VwapReviewTrade]:
    return [t for t in trades if not t.vwap_at_least(threshold)]


def summarize_trades(trades: Sequence[VwapReviewTrade]) -> dict[str, Any]:
    if not trades:
        return {"trade_count": 0}
    pnls = [t.pnl_pct for t in trades]
    pf = _pf(pnls)
    mfe_caps: list[float] = []
    for t in trades:
        if t.mfe_pct is not None and t.mfe_pct > 0:
            mfe_caps.append(t.pnl_pct / t.mfe_pct)
    return {
        "trade_count": len(trades),
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(_mean(pnls) or 0.0, 4),
        "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "stop_hit_count": sum(1 for t in trades if t.exit_reason == "stop_hit"),
        "trailing_mfe_exit_count": sum(1 for t in trades if t.exit_reason == "trailing_mfe_exit"),
        "mfe_capture_avg": round(_mean(mfe_caps), 4) if mfe_caps else None,
        "avg_r30_sec": round(_mean([t.r30_sec for t in trades if t.r30_sec is not None]) or 0, 4),
    }


def _exclusion_stats(
    all_trades: Sequence[VwapReviewTrade],
    excluded: Sequence[VwapReviewTrade],
) -> dict[str, Any]:
    profitable = [t for t in excluded if t.pnl_pct > 0]
    return {
        "excluded_count": len(excluded),
        "excluded_total_pnl_pct": round(sum(t.pnl_pct for t in excluded), 4),
        "false_positive_count": len(profitable),
        "false_positive_total_pnl_pct": round(sum(t.pnl_pct for t in profitable), 4),
        "false_positive_rate": round(len(profitable) / max(1, len(excluded)), 4),
    }


def _symbol_impact(
    trades: Sequence[VwapReviewTrade],
    symbols: frozenset[str],
    *,
    label: str,
) -> dict[str, Any]:
    subset = [t for t in trades if t.symbol in symbols]
    by_sym: dict[str, dict[str, Any]] = {}
    for sym in sorted(symbols):
        grp = [t for t in subset if t.symbol == sym]
        if not grp:
            by_sym[sym] = {"trade_count": 0}
            continue
        by_sym[sym] = summarize_trades(grp)
    return {
        "label": label,
        "symbols": sorted(symbols),
        "aggregate": summarize_trades(subset),
        "by_symbol": by_sym,
    }


def _delta_vs(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in (
        "trade_count",
        "total_pnl_pct",
        "profit_factor",
        "stop_hit_count",
        "trailing_mfe_exit_count",
        "mfe_capture_avg",
    ):
        a, b = base.get(k), other.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[k] = round(float(b) - float(a), 4)
    return out


def _session_scenarios(trades: Sequence[VwapReviewTrade]) -> dict[str, Any]:
    sum_a = summarize_trades(trades)
    ex_b = _excluded_by_vwap(trades, VWAP_DEV_THRESHOLD_B)
    ex_c = _excluded_by_vwap(trades, VWAP_DEV_THRESHOLD_C)
    kept_b = _kept_after_vwap_exclude(trades, VWAP_DEV_THRESHOLD_B)
    kept_c = _kept_after_vwap_exclude(trades, VWAP_DEV_THRESHOLD_C)
    sum_b = summarize_trades(kept_b)
    sum_c = summarize_trades(kept_c)
    d_trades = [t for t in trades if t.is_scenario_d()]
    not_d = [t for t in trades if not t.is_scenario_d()]

    vwap_known = [t for t in trades if t.entry_vwap_dev_pct is not None]
    return {
        "A_current": sum_a,
        "B_exclude_vwap_ge_2p5": {
            **sum_b,
            **_exclusion_stats(trades, ex_b),
            "delta_vs_A": _delta_vs(sum_a, sum_b),
        },
        "C_exclude_vwap_ge_3p0": {
            **sum_c,
            **_exclusion_stats(trades, ex_c),
            "delta_vs_A": _delta_vs(sum_a, sum_c),
        },
        "D_diagnostic_vwap_ge_2p5_and_r30_lt_0": {
            "description": "diagnostic slice only (not an exclusion scenario)",
            "matching_trades": summarize_trades(d_trades),
            "non_matching_trades": summarize_trades(not_d),
            "match_rate": round(len(d_trades) / max(1, len(trades)), 4),
        },
        "vwap_dev_coverage": {
            "trades_with_vwap_dev": len(vwap_known),
            "coverage_rate": round(len(vwap_known) / max(1, len(trades)), 4),
            "vwap_ge_2p5_count": len(ex_b),
            "vwap_ge_3p0_count": len(ex_c),
        },
    }


def _aggregate_verdict(per_session: list[dict[str, Any]], agg: dict[str, Any]) -> dict[str, Any]:
    improved_pf = 0
    improved_pnl = 0
    n = 0
    for row in per_session:
        if not row.get("ok"):
            continue
        sc = row.get("scenarios") or {}
        d_pf = ((sc.get("B_exclude_vwap_ge_2p5") or {}).get("delta_vs_A") or {}).get("profit_factor")
        d_pnl = ((sc.get("B_exclude_vwap_ge_2p5") or {}).get("delta_vs_A") or {}).get("total_pnl_pct")
        if isinstance(d_pf, (int, float)):
            n += 1
            if float(d_pf) > 0:
                improved_pf += 1
        if isinstance(d_pnl, (int, float)) and float(d_pnl) > 0:
            improved_pnl += 1

    agg_b = agg.get("B_exclude_vwap_ge_2p5") or {}
    agg_a = agg.get("A_current") or {}
    delta_pf = (agg_b.get("delta_vs_A") or {}).get("profit_factor")
    delta_pnl = (agg_b.get("delta_vs_A") or {}).get("total_pnl_pct")

    supports_reject = (
        isinstance(delta_pf, (int, float))
        and isinstance(delta_pnl, (int, float))
        and float(delta_pf) > 0
        and float(delta_pnl) > 0
        and improved_pf >= max(1, n // 2)
    )

    return {
        "sessions_evaluated": n,
        "sessions_b_improved_pf": improved_pf,
        "sessions_b_improved_total_pnl": improved_pnl,
        "aggregate_b_delta_pf": delta_pf,
        "aggregate_b_delta_total_pnl_pct": delta_pnl,
        "aggregate_a_pf": agg_a.get("profit_factor"),
        "aggregate_b_pf": agg_b.get("profit_factor"),
        "vwap_dev_shadow_reject_supported": supports_reject,
        "note": (
            "Composite extended_entry_shadow_flag is out of scope; "
            "rolling_mfe-only exclusion is not evaluated."
        ),
    }


def evaluate_vwap_dev_multisession_review(
    *,
    repo_root: Path,
    base: Path | None = None,
) -> dict[str, Any]:
    base = base or (repo_root / "kabu_native" / "results" / "small_paper")
    session_dirs, excluded = discover_sessions(base)

    per_session: list[dict[str, Any]] = []
    all_trades: list[VwapReviewTrade] = []

    for sdir in session_dirs:
        sid = _session_id(sdir, base)
        try:
            trades = load_session_trades(sdir, repo_root=repo_root, base=base)
        except Exception as exc:
            per_session.append({"session_id": sid, "ok": False, "reason": f"load_error:{exc}"})
            continue
        if not trades:
            per_session.append({"session_id": sid, "ok": False, "reason": "no_trades_loaded"})
            continue
        scenarios = _session_scenarios(trades)
        per_session.append(
            {
                "session_id": sid,
                "day_stamp": _day_stamp_from_session(sdir),
                "ok": True,
                "data_source": "structural_trades.csv"
                if (sdir / "structural_trades.csv").is_file()
                else "observer_exit_pairs",
                "scenarios": scenarios,
                "focus_symbols": _symbol_impact(trades, FOCUS_SYMBOLS, label="focus"),
                "compare_symbols": _symbol_impact(trades, COMPARE_SYMBOLS, label="compare"),
            }
        )
        all_trades.extend(trades)

    agg_scenarios = _session_scenarios(all_trades)
    agg_focus_a = _symbol_impact(all_trades, FOCUS_SYMBOLS, label="focus")
    agg_focus_b = _symbol_impact(
        _kept_after_vwap_exclude(all_trades, VWAP_DEV_THRESHOLD_B),
        FOCUS_SYMBOLS,
        label="focus_after_B",
    )
    agg_compare_a = _symbol_impact(all_trades, COMPARE_SYMBOLS, label="compare")
    agg_compare_b = _symbol_impact(
        _kept_after_vwap_exclude(all_trades, VWAP_DEV_THRESHOLD_B),
        COMPARE_SYMBOLS,
        label="compare_after_B",
    )

    verdict = _aggregate_verdict(per_session, agg_scenarios)

    return {
        "phase": 185,
        "mode": "vwap_dev_shadow_candidate_multisession_review",
        "hypothesis": "entry_vwap_dev_pct >= 2.5% marks entries with worse expectancy (vwap_dev-only; not composite extended).",
        "constraints": {
            "hard_reject": False,
            "shadow_review_only": True,
            "no_single_day_optimization": True,
            "no_parameter_search": True,
            "fixed_scenarios_only": True,
            "no_prod_yaml_change": True,
            "composite_extended_excluded": True,
            "rolling_mfe_only_exclusion_excluded": True,
        },
        "fixed_thresholds": {
            "scenario_B_vwap_dev_pct_min": VWAP_DEV_THRESHOLD_B,
            "scenario_C_vwap_dev_pct_min": VWAP_DEV_THRESHOLD_C,
            "scenario_D_vwap_dev_pct_min": VWAP_DEV_THRESHOLD_B,
            "scenario_D_r30_sec_max": 0.0,
        },
        "reference_session_set": list(REFERENCE_SESSIONS),
        "session_count_included": len([r for r in per_session if r.get("ok")]),
        "session_count_excluded": len(excluded) + len([r for r in per_session if not r.get("ok")]),
        "excluded_sessions": excluded,
        "per_session": per_session,
        "aggregate": {
            "scenarios": agg_scenarios,
            "focus_symbols": {
                "A_current": agg_focus_a,
                "B_after_exclude_vwap_ge_2p5": agg_focus_b,
                "delta_B_vs_A": _delta_vs(agg_focus_a.get("aggregate") or {}, agg_focus_b.get("aggregate") or {}),
            },
            "compare_symbols": {
                "A_current": agg_compare_a,
                "B_after_exclude_vwap_ge_2p5": agg_compare_b,
                "delta_B_vs_A": _delta_vs(
                    agg_compare_a.get("aggregate") or {}, agg_compare_b.get("aggregate") or {}
                ),
            },
        },
        "verdict": verdict,
        "reject_candidate": {
            "feature": "entry_vwap_dev_pct",
            "threshold_shadow_review": VWAP_DEV_THRESHOLD_B,
            "proceed_to_shadow_reject_candidate": verdict.get("vwap_dev_shadow_reject_supported"),
            "alternative_scenario_C_threshold": VWAP_DEV_THRESHOLD_C,
        },
    }
