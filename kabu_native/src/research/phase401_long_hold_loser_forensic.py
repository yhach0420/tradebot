"""
Phase401: Long hold loser forensic audit (research only).

Forensic analysis of Phase400 p90+ long hold losers (-¥118,740 cohort).
No Runtime / YAML / Entry / Exit changes.
"""

from __future__ import annotations

import bisect
import csv
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key, _write_csv
from research.phase400_holding_time_audit import (
    enrich_trade,
    hold_seconds,
    load_phase399_trades,
    normalize_exit_reason,
)
from research.research_exit_criteria import _as_float
from research.runtime_pilot_policy_review import _build_price_index
from research.small_paper_performance_review import _load_events
from small_paper.board_dynamic_trailing_shadow import board_tier_from_percentile

JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"
PERIOD_END = "20260615"
TIME_CHECKPOINTS_MIN = (5, 10, 20, 30)

FORENSIC_FIELDS = [
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "pnl_yen_100",
    "exit_reason_bucket",
    "max_mfe_pct",
    "mfe_category",
    "mfe_at_5m_pct",
    "mfe_at_10m_pct",
    "mfe_at_20m_pct",
    "mfe_reach_time_min",
    "trajectory_class",
    "price_change_5m_pct",
    "price_change_10m_pct",
    "price_change_20m_pct",
    "price_change_30m_pct",
    "vwap_dev_5m_proxy",
    "vwap_dev_10m_proxy",
    "vwap_dev_20m_proxy",
    "vwap_dev_30m_proxy",
    "high_update_count_30m",
    "entry_vwap_dev_pct",
    "entry_imbalance_percentile",
    "board_tier",
    "universe_bucket",
    "time_exit_would_save",
    "entry_guard_would_help",
    "board_exit_signal",
]

CLUSTER_FIELDS = [
    "feature",
    "count",
    "share",
    "total_pnl_yen_100",
    "avg_hold_sec",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _mfe_category(mfe: float) -> str:
    if mfe < 0.2:
        return "A_mfe_lt_0p2"
    if mfe < 0.5:
        return "B_mfe_0p2_0p5"
    if mfe < 1.0:
        return "C_mfe_0p5_1p0"
    return "D_mfe_gte_1p0"


def _price_at_offset(
    series: Sequence[tuple[float, float]],
    entry_ts: float,
    offset_sec: float,
) -> Optional[float]:
    if not series:
        return None
    target = entry_ts + offset_sec
    times = [t for t, _ in series]
    idx = bisect.bisect_left(times, target)
    if idx >= len(series):
        return series[-1][1]
    if idx == 0:
        return series[0][1]
    prev_ts, prev_px = series[idx - 1]
    next_ts, next_px = series[idx]
    if next_ts == prev_ts:
        return prev_px
    if abs(target - prev_ts) <= abs(next_ts - target):
        return prev_px
    return next_px


def _mfe_up_to(
    series: Sequence[tuple[float, float]],
    entry_ts: float,
    entry_px: float,
    until_ts: float,
) -> float:
    if entry_px <= 0:
        return 0.0
    peak = 0.0
    for ts, px in series:
        if ts < entry_ts:
            continue
        if ts > until_ts:
            break
        peak = max(peak, (px - entry_px) / entry_px * 100.0)
    return round(peak, 4)


def _mfe_reach_time_min(
    series: Sequence[tuple[float, float]],
    entry_ts: float,
    entry_px: float,
    max_mfe: float,
) -> Optional[float]:
    if entry_px <= 0 or max_mfe <= 0:
        return None
    running = 0.0
    for ts, px in series:
        if ts < entry_ts:
            continue
        running = max(running, (px - entry_px) / entry_px * 100.0)
        if running >= max_mfe * 0.99:
            return round((ts - entry_ts) / 60.0, 2)
    return None


def _high_update_count(
    series: Sequence[tuple[float, float]],
    entry_ts: float,
    until_ts: float,
) -> int:
    high = None
    count = 0
    for ts, px in series:
        if ts < entry_ts or ts > until_ts:
            continue
        if high is None:
            high = px
            continue
        if px > high:
            count += 1
            high = px
    return count


def _classify_trajectory(
    *,
    max_mfe: float,
    mfe_5m: float,
    mfe_10m: float,
    mfe_20m: float,
    price_20m: Optional[float],
    price_30m: Optional[float],
    entry_px: float,
    exit_px: Optional[float],
) -> str:
    if max_mfe < 0.2 and mfe_5m < 0.15 and mfe_10m < 0.2:
        return "dead_from_start"
    if max_mfe >= 0.5:
        return "rose_then_faded"
    if entry_px > 0 and price_20m is not None:
        ch20 = abs((price_20m - entry_px) / entry_px * 100.0)
        if ch20 < 0.3 and max_mfe < 0.5:
            return "flat"
    if entry_px > 0 and exit_px and price_30m:
        p30 = (price_30m - entry_px) / entry_px * 100.0
        pexit = (exit_px - entry_px) / entry_px * 100.0
        if p30 > 0.1 and pexit < p30 - 0.2:
            return "late_collapse"
    if max_mfe >= 0.2:
        return "rose_then_faded"
    return "dead_from_start"


def select_long_hold_losers(
    trades: Sequence[Mapping[str, Any]],
    *,
    p90_hold_sec: float,
) -> list[dict[str, Any]]:
    losers: list[dict[str, Any]] = []
    for row in trades:
        t = enrich_trade(row)
        if not t.get("position_cap_accepted_bool"):
            continue
        if not t.get("is_loser"):
            continue
        if float(t.get("hold_sec") or 0) < p90_hold_sec:
            continue
        losers.append(t)
    losers.sort(key=lambda r: (str(r.get("day")), str(r.get("entry_time") or "")))
    return losers


def _load_structural_lookup(session_dir: Path) -> dict[str, dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = _position_key({"symbol": row.get("symbol"), "entry_time": row.get("entry_time")})
            out[key] = dict(row)
    return out


def _accepted_lookup(session_dir: Path) -> dict[tuple[str, str], dict[str, str]]:
    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return {}
    out: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("event_type") != "accepted":
                continue
            out[(str(row.get("symbol") or ""), str(row.get("entry_time") or ""))] = dict(row)
    return out


def _infer_universe_bucket(acc: Mapping[str, str]) -> str:
    bucket = str(acc.get("universe_bucket") or acc.get("source_bucket") or "").strip().lower()
    if bucket in ("core10", "core_10", "core"):
        return "core10"
    if bucket in ("dynamic40", "dynamic_40", "dynamic"):
        return "dynamic40"
    if _as_float(acc.get("near_day_high_low_momentum_dynamic40_guard_candidate")):
        return "dynamic40"
    if _as_float(acc.get("pullback_misread_dynamic40_guard_candidate")):
        return "dynamic40"
    slot = str(acc.get("universe_slot") or "").lower()
    if "dynamic" in slot:
        return "dynamic40"
    if "core" in slot:
        return "core10"
    return "unknown"


def _session_dir(repo_root: Path, day: str, session: str) -> Path:
    return repo_root / "results" / "small_paper" / day / session


def enrich_loser_forensic(
    trade: Mapping[str, Any],
    *,
    repo_root: Path,
    session_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    day = str(trade.get("day") or "")
    session = str(trade.get("session") or "")
    sym = str(trade.get("symbol") or "")
    entry_time = str(trade.get("entry_time") or "")
    cache_key = f"{day}/{session}"

    if cache_key not in session_cache:
        sdir = _session_dir(repo_root, day, session)
        events = _load_events(sdir) if sdir.is_dir() else []
        session_cache[cache_key] = {
            "structural": _load_structural_lookup(sdir),
            "accepted": _accepted_lookup(sdir),
            "price_index": _build_price_index(events),
        }

    cache = session_cache[cache_key]
    pos_key = _position_key({"symbol": sym, "entry_time": entry_time})
    struct = cache["structural"].get(pos_key, {})
    acc = cache["accepted"].get((sym, entry_time), {})

    entry_px = _float(struct.get("entry_price")) or _float(acc.get("current_price")) or _float(acc.get("entry_price"))
    exit_px = _float(struct.get("close_price") or struct.get("exit_price"))
    ent_ts = _parse_ts(entry_time)
    ex_ts = _parse_ts(str(trade.get("exit_time") or struct.get("close_time") or ""))
    series = cache["price_index"].get(sym, []) if ent_ts else []

    max_mfe = _float(struct.get("mfe_pct")) or 0.0
    if entry_px and ent_ts and series:
        until = ex_ts or (ent_ts + float(trade.get("hold_sec") or 0))
        replay_mfe = _mfe_up_to(series, ent_ts.timestamp(), entry_px, until.timestamp())
        if replay_mfe > max_mfe:
            max_mfe = replay_mfe

    mfe_5m = mfe_10m = mfe_20m = 0.0
    price_changes: dict[int, Optional[float]] = {}
    vwap_proxies: dict[int, Optional[float]] = {}
    entry_vwap = _float(acc.get("entry_vwap_dev_pct"))
    high_updates_30m = 0

    if entry_px and ent_ts:
        ent_epoch = ent_ts.timestamp()
        for mins in TIME_CHECKPOINTS_MIN:
            off = mins * 60.0
            px = _price_at_offset(series, ent_epoch, off)
            mfe_val = _mfe_up_to(series, ent_epoch, entry_px, ent_epoch + off)
            if mins == 5:
                mfe_5m = mfe_val
            elif mins == 10:
                mfe_10m = mfe_val
            elif mins == 20:
                mfe_20m = mfe_val
            if px:
                ch = round((px - entry_px) / entry_px * 100.0, 4)
                price_changes[mins] = ch
                if entry_vwap is not None:
                    vwap_proxies[mins] = round(ch - entry_vwap, 4)
            else:
                price_changes[mins] = None
                vwap_proxies[mins] = None
        high_updates_30m = _high_update_count(series, ent_epoch, ent_epoch + 1800.0)

    mfe_reach_min = _mfe_reach_time_min(series, ent_ts.timestamp(), entry_px or 0.0, max_mfe) if ent_ts and entry_px else None
    trajectory = _classify_trajectory(
        max_mfe=max_mfe,
        mfe_5m=mfe_5m,
        mfe_10m=mfe_10m,
        mfe_20m=mfe_20m,
        price_20m=(entry_px * (1 + (price_changes.get(20) or 0) / 100.0)) if entry_px and price_changes.get(20) is not None else None,
        price_30m=(entry_px * (1 + (price_changes.get(30) or 0) / 100.0)) if entry_px and price_changes.get(30) is not None else None,
        entry_px=entry_px or 0.0,
        exit_px=exit_px,
    )

    pnl = float(trade.get("pnl_yen_100_float") or 0.0)
    pnl_20m = 0.0
    pnl_30m = 0.0
    if entry_px and entry_px > 0:
        if price_changes.get(20) is not None:
            pnl_20m = round(entry_px * 100.0 * price_changes[20] / 100.0, 2)
        if price_changes.get(30) is not None:
            pnl_30m = round(entry_px * 100.0 * price_changes[30] / 100.0, 2)
    time_exit_would_save = (pnl_20m > pnl + 50) or (pnl_30m > pnl + 50)

    imb = _float(acc.get("entry_imbalance_percentile"))
    board_tier = str(acc.get("board_dynamic_trailing_tier") or acc.get("shadow_board_dynamic_tier") or board_tier_from_percentile(imb))
    universe = _infer_universe_bucket(acc)
    entry_guard_would_help = max_mfe < 0.2 or trajectory == "dead_from_start" or (
        entry_vwap is not None and entry_vwap < 0 and max_mfe < 0.5
    )
    board_exit_signal = board_tier in ("low", "very_low") or (imb is not None and imb < 30.0)

    return {
        "day": day,
        "session": session,
        "symbol": sym,
        "entry_time": entry_time,
        "exit_time": trade.get("exit_time"),
        "hold_sec": trade.get("hold_sec"),
        "pnl_yen_100": pnl,
        "exit_reason_bucket": trade.get("exit_reason_bucket") or normalize_exit_reason(str(trade.get("exit_reason") or "")),
        "max_mfe_pct": round(max_mfe, 4),
        "mfe_category": _mfe_category(max_mfe),
        "mfe_at_5m_pct": mfe_5m,
        "mfe_at_10m_pct": mfe_10m,
        "mfe_at_20m_pct": mfe_20m,
        "mfe_reach_time_min": mfe_reach_min,
        "trajectory_class": trajectory,
        "price_change_5m_pct": price_changes.get(5),
        "price_change_10m_pct": price_changes.get(10),
        "price_change_20m_pct": price_changes.get(20),
        "price_change_30m_pct": price_changes.get(30),
        "vwap_dev_5m_proxy": vwap_proxies.get(5),
        "vwap_dev_10m_proxy": vwap_proxies.get(10),
        "vwap_dev_20m_proxy": vwap_proxies.get(20),
        "vwap_dev_30m_proxy": vwap_proxies.get(30),
        "high_update_count_30m": high_updates_30m,
        "entry_vwap_dev_pct": entry_vwap,
        "entry_imbalance_percentile": imb,
        "board_tier": board_tier,
        "universe_bucket": universe,
        "time_exit_would_save": time_exit_would_save,
        "entry_guard_would_help": entry_guard_would_help,
        "board_exit_signal": board_exit_signal,
    }


def build_clusters(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    n = len(rows)
    if n == 0:
        return []

    def _feat(name: str, pred) -> dict[str, Any]:
        matched = [r for r in rows if pred(r)]
        return {
            "feature": name,
            "count": len(matched),
            "share": round(len(matched) / n, 4),
            "total_pnl_yen_100": round(sum(float(r.get("pnl_yen_100") or 0) for r in matched), 2),
            "avg_hold_sec": round(statistics.mean(float(r.get("hold_sec") or 0) for r in matched), 2) if matched else 0.0,
        }

    features = [
        _feat("mfe_lt_0p2", lambda r: float(r.get("max_mfe_pct") or 0) < 0.2),
        _feat("mfe_lt_0p5", lambda r: float(r.get("max_mfe_pct") or 0) < 0.5),
        _feat("dead_from_start", lambda r: r.get("trajectory_class") == "dead_from_start"),
        _feat("rose_then_faded", lambda r: r.get("trajectory_class") == "rose_then_faded"),
        _feat("late_collapse", lambda r: r.get("trajectory_class") == "late_collapse"),
        _feat("vwap_below_entry", lambda r: _float(r.get("entry_vwap_dev_pct")) is not None and _float(r.get("entry_vwap_dev_pct")) < 0),
        _feat("dynamic40", lambda r: r.get("universe_bucket") == "dynamic40"),
        _feat("core10", lambda r: r.get("universe_bucket") == "core10"),
        _feat("board_low", lambda r: str(r.get("board_tier") or "").lower() in ("low", "very_low")),
        _feat("high_update_none", lambda r: int(r.get("high_update_count_30m") or 0) == 0),
        _feat("stop_hit_exit", lambda r: r.get("exit_reason_bucket") == "stop_hit"),
        _feat("session_close_exit", lambda r: r.get("exit_reason_bucket") == "session_close"),
        _feat("time_exit_would_save", lambda r: bool(r.get("time_exit_would_save"))),
        _feat("entry_guard_would_help", lambda r: bool(r.get("entry_guard_would_help"))),
    ]
    features.sort(key=lambda r: (-int(r["count"]), str(r["feature"])))
    return features


def _recommendation(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"recommendation": "D_no_action", "scores": {}}
    scores = {
        "A_time_exit": sum(1 for r in rows if r.get("time_exit_would_save")),
        "B_entry_guard": sum(1 for r in rows if r.get("entry_guard_would_help")),
        "C_board_exit": sum(1 for r in rows if r.get("board_exit_signal")),
        "D_no_action": 0,
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < n * 0.35:
        best = "D_no_action"
    return {"recommendation": best, "scores": scores}


def run_phase401_forensic(
    *,
    repo_root: Path,
    trades_path: Optional[Path] = None,
    phase400_summary_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    period_start: str = PERIOD_START,
    period_end: str = PERIOD_END,
) -> dict[str, Any]:
    reports = output_dir or (repo_root / "results" / "reports")
    trades_path = trades_path or (reports / "phase399_historical_position_cap_backfill_trades.csv")
    p400_path = phase400_summary_path or (reports / "phase400_holding_time_summary.json")

    trades = [
        r
        for r in load_phase399_trades(trades_path)
        if period_start <= str(r.get("day") or "") <= period_end
    ]
    p90 = 1290.6
    if p400_path.is_file():
        p400 = json.loads(p400_path.read_text(encoding="utf-8"))
        p90 = float((p400.get("hold_duration_sec") or {}).get("p90_hold_sec") or p90)

    losers = select_long_hold_losers(trades, p90_hold_sec=p90)
    session_cache: dict[str, dict[str, Any]] = {}
    forensic_rows = [
        enrich_loser_forensic(t, repo_root=repo_root, session_cache=session_cache) for t in losers
    ]

    mfe_counts = Counter(str(r.get("mfe_category") or "") for r in forensic_rows)
    exit_stats: dict[str, dict[str, Any]] = {}
    for r in forensic_rows:
        bucket = str(r.get("exit_reason_bucket") or "other")
        s = exit_stats.setdefault(bucket, {"count": 0, "total_pnl_yen_100": 0.0})
        s["count"] += 1
        s["total_pnl_yen_100"] = round(s["total_pnl_yen_100"] + float(r.get("pnl_yen_100") or 0), 2)

    universe_counts = Counter(str(r.get("universe_bucket") or "unknown") for r in forensic_rows)
    clusters = build_clusters(forensic_rows)
    rec = _recommendation(forensic_rows)

    count_mfe_lt_02 = sum(1 for r in forensic_rows if float(r.get("max_mfe_pct") or 0) < 0.2)
    count_mfe_lt_05 = sum(1 for r in forensic_rows if float(r.get("max_mfe_pct") or 0) < 0.5)
    dead_from_start = sum(1 for r in forensic_rows if r.get("trajectory_class") == "dead_from_start")
    had_profit_once = sum(1 for r in forensic_rows if float(r.get("max_mfe_pct") or 0) >= 0.2)
    time_exit_saves = sum(1 for r in forensic_rows if r.get("time_exit_would_save"))
    entry_guard_helps = sum(1 for r in forensic_rows if r.get("entry_guard_would_help"))

    headline = (
        "長時間負け27件は、"
        f"{'最初から死んでいた' if dead_from_start >= len(forensic_rows) / 2 else '途中で崩れた・戻された'}"
        f"（dead_from_start={dead_from_start}/{len(forensic_rows)}、"
        f"MFE<0.2%={count_mfe_lt_02}件）が主因。"
    )

    mandatory = {
        "count_mfe_lt_0p2": count_mfe_lt_02,
        "count_mfe_lt_0p5": count_mfe_lt_05,
        "dead_from_start_share": round(dead_from_start / len(forensic_rows), 4) if forensic_rows else 0.0,
        "had_profit_once_share": round(had_profit_once / len(forensic_rows), 4) if forensic_rows else 0.0,
        "time_exit_would_save_count": time_exit_saves,
        "entry_guard_would_help_count": entry_guard_helps,
        "recommendation": rec["recommendation"],
    }

    verdict = "PASS" if len(forensic_rows) == 27 and forensic_rows else "FAIL"

    summary = {
        "phase": 401,
        "generated_at": _now_iso(),
        "period_start": period_start,
        "period_end": period_end,
        "p90_hold_sec": p90,
        "cohort_count": len(forensic_rows),
        "cohort_total_pnl_yen_100": round(sum(float(r.get("pnl_yen_100") or 0) for r in forensic_rows), 2),
        "headline_one_liner": headline,
        "mfe_category_counts": dict(mfe_counts),
        "exit_reason_stats": exit_stats,
        "universe_bucket_counts": dict(universe_counts),
        "mandatory_answers": mandatory,
        "recommendation": rec,
        "clusters_top": clusters[:10],
        "verdict": verdict,
    }

    reports.mkdir(parents=True, exist_ok=True)
    _write_csv(reports / "phase401_long_hold_loser_forensic.csv", forensic_rows, FORENSIC_FIELDS)
    _write_csv(reports / "phase401_long_hold_loser_clusters.csv", clusters, CLUSTER_FIELDS)
    (reports / "phase401_long_hold_loser_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    docs = repo_root / "docs" / "operations"
    docs.mkdir(parents=True, exist_ok=True)
    report_path = docs / "phase401_long_hold_loser_forensic_report.md"
    report_path.write_text(
        _build_report(summary=summary, forensic_rows=forensic_rows, clusters=clusters),
        encoding="utf-8",
    )

    return {"summary": summary, "forensic_rows": forensic_rows, "clusters": clusters, "report_path": str(report_path)}


def _build_report(
    *,
    summary: Mapping[str, Any],
    forensic_rows: Sequence[Mapping[str, Any]],
    clusters: Sequence[Mapping[str, Any]],
) -> str:
    ans = summary.get("mandatory_answers") or {}
    mfe_counts = summary.get("mfe_category_counts") or {}
    exit_stats = summary.get("exit_reason_stats") or {}
    uni = summary.get("universe_bucket_counts") or {}
    rec = summary.get("recommendation") or {}

    lines = [
        "# Phase401 — Long Hold Loser Forensic Audit",
        "",
        f"Generated: {summary.get('generated_at')}",
        "",
        f"## {summary.get('headline_one_liner')}",
        "",
        f"Verdict: **{summary.get('verdict')}**",
        "",
        f"Cohort: **{summary.get('cohort_count')}** trades (hold ≥ p90={summary.get('p90_hold_sec')}s, pnl<0)",
        f"Total PnL: **¥{summary.get('cohort_total_pnl_yen_100')}**",
        "",
        "## 必須回答",
        "",
        f"1. MFE < 0.2%: **{ans.get('count_mfe_lt_0p2')}** / {summary.get('cohort_count')}",
        f"2. MFE < 0.5%: **{ans.get('count_mfe_lt_0p5')}** / {summary.get('cohort_count')}",
        f"3. 最初から上昇しなかった（dead_from_start）: **{float(ans.get('dead_from_start_share') or 0)*100:.1f}%**",
        f"4. 一度は利益が出た（max_mfe≥0.2%）: **{float(ans.get('had_profit_once_share') or 0)*100:.1f}%**",
        f"5. 時間EXITが救えた推定: **{ans.get('time_exit_would_save_count')}** 件",
        f"6. Entry改善が効く推定: **{ans.get('entry_guard_would_help_count')}** 件",
        f"7. 推奨: **{ans.get('recommendation')}** (scores: {rec.get('scores')})",
        "",
        "### MFEカテゴリ",
        "",
        f"- A (MFE<0.2%): {mfe_counts.get('A_mfe_lt_0p2', 0)}",
        f"- B (0.2–0.5%): {mfe_counts.get('B_mfe_0p2_0p5', 0)}",
        f"- C (0.5–1.0%): {mfe_counts.get('C_mfe_0p5_1p0', 0)}",
        f"- D (≥1.0%): {mfe_counts.get('D_mfe_gte_1p0', 0)}",
        "",
        "### EXIT理由別",
        "",
        "| bucket | count | total_pnl |",
        "|--------|-------|-----------|",
    ]
    for bucket, st in sorted(exit_stats.items()):
        lines.append(f"| {bucket} | {st.get('count')} | ¥{st.get('total_pnl_yen_100')} |")

    lines.extend(
        [
            "",
            "### Dynamic40 / Core10",
            "",
            f"- dynamic40: {uni.get('dynamic40', 0)}",
            f"- core10: {uni.get('core10', 0)}",
            f"- unknown: {uni.get('unknown', 0)}",
            "",
            "### 共通特徴 Top",
            "",
            "| feature | count | share | total_pnl |",
            "|---------|-------|-------|-----------|",
        ]
    )
    for row in clusters[:12]:
        lines.append(
            f"| {row.get('feature')} | {row.get('count')} | {row.get('share')} | ¥{row.get('total_pnl_yen_100')} |"
        )

    lines.extend(
        [
            "",
            "### CAP占有時間 Top10（本次コホート）",
            "",
            "| symbol | hold_sec | pnl | mfe | trajectory | exit |",
            "|--------|----------|-----|-----|------------|------|",
        ]
    )
    for r in sorted(forensic_rows, key=lambda x: -float(x.get("hold_sec") or 0))[:10]:
        lines.append(
            f"| {r.get('symbol')} | {r.get('hold_sec')} | ¥{r.get('pnl_yen_100')} | "
            f"{r.get('max_mfe_pct')}% | {r.get('trajectory_class')} | {r.get('exit_reason_bucket')} |"
        )

    lines.extend(
        [
            "",
            "## 推奨解釈",
            "",
            "- **A 時間EXIT**: rose_then_faded + time_exit_would_save が多い場合",
            "- **B Entry Guard**: MFE<0.2% + dead_from_start が過半の場合",
            "- **C Board Exit**: board_low + VWAP下が集中する場合",
            "- **D 何もしない**: 分散して主因が特定できない場合",
            "",
            "## 成果物",
            "",
            "- `results/reports/phase401_long_hold_loser_forensic.csv`",
            "- `results/reports/phase401_long_hold_loser_clusters.csv`",
            "- `results/reports/phase401_long_hold_loser_summary.json`",
            "",
        ]
    )
    return "\n".join(lines)
