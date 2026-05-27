"""
Phase 110: Hero-symbol coverage backtest for static27 vs opening_dynamic50.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

FOCUS_SYMBOLS = ("3905.T", "6613.T")
HERO_TOP_N = 20
HERO_TOP10 = 10


def _norm_symbol(code: str) -> str:
    c = str(code).strip().upper().split("@")[0]
    if not c:
        return ""
    return c if c.endswith(".T") else f"{c}.T"


def load_symbol_set_from_csv(path: Path, *, col: str = "symbol") -> set[str]:
    if not path.is_file():
        return set()
    out: set[str] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm_symbol(str(row.get(col) or ""))
            if sym:
                out.add(sym)
    return out


def load_static27(native_root: Path) -> set[str]:
    p = native_root / "data" / "universe" / "universe_intraday_full.csv"
    return load_symbol_set_from_csv(p)


def fetch_yfinance_day_metrics(
    symbols: Sequence[str],
    trade_date: date,
    *,
    max_symbols: int = 1200,
) -> dict[str, dict[str, Any]]:
    try:
        import pandas as pd
        import yfinance as yf
    except ImportError:
        return {}

    syms = list(symbols)[:max_symbols]
    if not syms:
        return {}

    start = trade_date - timedelta(days=12)
    end = trade_date + timedelta(days=1)
    out: dict[str, dict[str, Any]] = {}
    chunk = 60

    for i in range(0, len(syms), chunk):
        batch = syms[i : i + chunk]
        try:
            data = yf.download(
                batch,
                start=start.isoformat(),
                end=end.isoformat(),
                interval="1d",
                group_by="ticker",
                progress=False,
                threads=False,
                auto_adjust=False,
            )
        except Exception:
            continue
        if data is None or data.empty:
            continue

        for sym in batch:
            try:
                if len(batch) == 1:
                    df = data.copy()
                    if isinstance(df.columns, pd.MultiIndex):
                        df = data.xs(sym, axis=1, level=0)
                elif isinstance(data.columns, pd.MultiIndex):
                    if sym in data.columns.get_level_values(0):
                        df = data.xs(sym, axis=1, level=0)
                    elif sym in data.columns.get_level_values(1):
                        df = data.xs(sym, axis=1, level=1)
                    else:
                        continue
                else:
                    continue
                df = df.dropna(how="all")
                if len(df) < 2:
                    continue
                row = df.iloc[-1]
                prev = df.iloc[-2]
                c = float(row["Close"])
                pc = float(prev["Close"])
                h, l = float(row["High"]), float(row["Low"])
                vol = float(row["Volume"]) if row["Volume"] == row["Volume"] else 0.0
                if c <= 0 or pc <= 0:
                    continue
                change_pct = (c - pc) / pc * 100.0
                range_pct = (h - l) / c * 100.0
                tv = vol * c
                vols = [
                    float(df.iloc[j]["Volume"])
                    for j in range(max(0, len(df) - 6), len(df) - 1)
                    if df.iloc[j]["Volume"] == df.iloc[j]["Volume"]
                ]
                avg5 = statistics.mean(vols) if vols else None
                surge = vol / avg5 if avg5 and avg5 > 0 else None
                ysym = _norm_symbol(sym)
                out[ysym] = {
                    "change_pct": round(change_pct, 4),
                    "trading_value_proxy": round(tv, 2),
                    "range_pct": round(range_pct, 4),
                    "volume_surge_5": round(surge, 4) if surge else None,
                    "data_source": "yfinance_daily",
                }
            except Exception:
                continue
    return out


def push_eod_metrics(push_day_dir: Path) -> dict[str, dict[str, Any]]:
    """Last PUSH snapshot per symbol on trade date (proxy for session metrics)."""
    out: dict[str, dict[str, Any]] = {}
    if not push_day_dir.is_dir():
        return out
    for path in push_day_dir.glob("*.jsonl"):
        sym = _norm_symbol(path.stem)
        last_payload: Optional[dict] = None
        last_ts = ""
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rec = str(row.get("recorded_at") or "")
                payload = row.get("payload")
                if isinstance(payload, dict) and rec >= last_ts:
                    last_ts = rec
                    last_payload = payload
        if not last_payload:
            continue
        chg = last_payload.get("ChangePreviousClosePer")
        tv = last_payload.get("TradingValue")
        hi = last_payload.get("HighPrice")
        lo = last_payload.get("LowPrice")
        cl = last_payload.get("CurrentPrice") or last_payload.get("CalcPrice")
        try:
            c = float(cl) if cl is not None else None
            h = float(hi) if hi is not None else c
            l = float(lo) if lo is not None else c
            range_pct = (h - l) / c * 100.0 if c and h and l else None
        except (TypeError, ValueError):
            range_pct = None
        out[sym] = {
            "change_pct": float(chg) if chg is not None else None,
            "trading_value_proxy": float(tv) if tv is not None else None,
            "range_pct": round(range_pct, 4) if range_pct is not None else None,
            "volume_surge_5": None,
            "data_source": "push_jsonl_eod",
        }
    return out


def top_n_by_metric(metrics: Mapping[str, Mapping[str, Any]], field: str, n: int, *, reverse: bool = True) -> list[str]:
    vals = [(s, m.get(field)) for s, m in metrics.items() if m.get(field) is not None]
    vals.sort(key=lambda x: x[1] or 0, reverse=reverse)
    return [s for s, _ in vals[:n]]


@dataclass
class HeroDefinition:
    trade_date: str
    day_stamp: str
    metrics_by_symbol: dict[str, dict[str, Any]] = field(default_factory=dict)
    hero_top10: set[str] = field(default_factory=set)
    hero_top20: set[str] = field(default_factory=set)
    hero_sources: dict[str, list[str]] = field(default_factory=dict)
    proxy_notes: list[str] = field(default_factory=list)
    session_candidate_top20: set[str] = field(default_factory=set)
    session_accepted_symbols: set[str] = field(default_factory=set)


def build_hero_definition(
    *,
    trade_date: date,
    master_symbols: Sequence[str],
    push_day_dir: Path,
    yfinance_max: int = 1200,
) -> HeroDefinition:
    day_stamp = trade_date.strftime("%Y%m%d")
    notes: list[str] = []
    metrics: dict[str, dict[str, Any]] = {}

    yf = fetch_yfinance_day_metrics(master_symbols, trade_date, max_symbols=yfinance_max)
    metrics.update(yf)
    if yf:
        notes.append(f"yfinance_daily:{len(yf)} symbols")
    else:
        notes.append("yfinance_daily:unavailable")

    push_m = push_eod_metrics(push_day_dir)
    for sym, m in push_m.items():
        if sym not in metrics:
            metrics[sym] = m
        else:
            metrics[sym] = {**metrics[sym], **{k: v for k, v in m.items() if v is not None}}
    if push_m:
        notes.append(f"push_jsonl_eod:{len(push_m)} symbols")

    sources: dict[str, list[str]] = {}
    sets_top20: list[set[str]] = []
    for field, rev in (
        ("change_pct", True),
        ("trading_value_proxy", True),
        ("range_pct", True),
        ("volume_surge_5", True),
    ):
        top20 = set(top_n_by_metric(metrics, field, HERO_TOP_N, reverse=rev))
        top10 = set(top_n_by_metric(metrics, field, HERO_TOP10, reverse=rev))
        sources[f"top20_{field}"] = sorted(top20)
        sets_top20.append(top20)
        if field == "change_pct":
            sources[f"top10_{field}"] = sorted(top10)

    hero_top20: set[str] = set()
    for s in sets_top20:
        hero_top20 |= s
    hero_top10: set[str] = set()
    for field in ("change_pct", "trading_value_proxy"):
        hero_top10 |= set(top_n_by_metric(metrics, field, HERO_TOP10))

    return HeroDefinition(
        trade_date=trade_date.isoformat(),
        day_stamp=day_stamp,
        metrics_by_symbol=metrics,
        hero_top10=hero_top10,
        hero_top20=hero_top20,
        hero_sources=sources,
        proxy_notes=notes,
    )


def coverage_vs_universe(
    hero_set: set[str],
    universe_set: set[str],
) -> dict[str, Any]:
    hit = hero_set & universe_set
    missed = hero_set - universe_set
    extra = universe_set - hero_set
    n = max(len(hero_set), 1)
    return {
        "hero_count": len(hero_set),
        "universe_count": len(universe_set),
        "hit_count": len(hit),
        "missed_count": len(missed),
        "hit_rate": round(len(hit) / n, 4),
        "hits": sorted(hit),
        "missed_heroes": sorted(missed),
        "universe_not_hero": len(extra),
    }


def compare_universes(
    *,
    hero_top20: set[str],
    hero_top10: set[str],
    static27: set[str],
    opening50: set[str],
) -> dict[str, Any]:
    cov_s = coverage_vs_universe(hero_top20, static27)
    cov_o = coverage_vs_universe(hero_top20, opening50)
    cov_s10 = coverage_vs_universe(hero_top10, static27)
    cov_o10 = coverage_vs_universe(hero_top10, opening50)
    dyn_only = opening50 - static27
    static_only = static27 - opening50
    overlap = static27 & opening50
    focus = {}
    for sym in FOCUS_SYMBOLS:
        focus[sym] = {
            "in_hero_top20": sym in hero_top20,
            "in_static27": sym in static27,
            "in_opening_dynamic50": sym in opening50,
            "in_static_not_opening": sym in static27 and sym not in opening50,
            "in_opening_not_static": sym in opening50 and sym not in static27,
        }
    return {
        "hero_top10": {
            "static27": cov_s10,
            "opening_dynamic50": cov_o10,
        },
        "hero_top20": {
            "static27": cov_s,
            "opening_dynamic50": cov_o,
        },
        "hero_top10_hit_count": {
            "static27": cov_s10["hit_count"],
            "opening_dynamic50": cov_o10["hit_count"],
        },
        "hero_top20_hit_count": {
            "static27": cov_s["hit_count"],
            "opening_dynamic50": cov_o["hit_count"],
        },
        "hit_rate": {
            "static27": cov_s["hit_rate"],
            "opening_dynamic50": cov_o["hit_rate"],
        },
        "overlap_count": len(overlap),
        "dynamic_only_hits": sorted((opening50 - static27) & hero_top20),
        "static_only_hits": sorted((static27 - opening50) & hero_top20),
        "dynamic_only_symbols": sorted(dyn_only),
        "static_only_symbols": sorted(static_only),
        "focus_diagnostics": focus,
    }


def load_session_activity(session_dir: Path) -> dict[str, Any]:
    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        path = session_dir / "small_paper_events.jsonl"
        if not path.is_file():
            return {"session_dir": str(session_dir), "found": False}
    candidates: Counter[str] = Counter()
    accepted: Counter[str] = Counter()
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                sym = _norm_symbol(str(row.get("symbol") or ""))
                et = str(row.get("event_type") or "")
                if sym and et == "candidate":
                    candidates[sym] += 1
                if sym and et == "accepted":
                    accepted[sym] += 1
    else:
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sym = _norm_symbol(str(row.get("symbol") or ""))
                et = str(row.get("event_type") or "")
                if sym and et == "candidate":
                    candidates[sym] += 1
                if sym and et == "accepted":
                    accepted[sym] += 1
    cand_top = [s for s, _ in candidates.most_common(HERO_TOP_N)]
    acc_top = [s for s, _ in accepted.most_common(HERO_TOP_N)]
    return {
        "found": True,
        "session_dir": str(session_dir),
        "candidate_unique": len(candidates),
        "accepted_unique": len(accepted),
        "candidate_top20": cand_top,
        "accepted_top20": acc_top,
        "accepted_symbols": sorted(accepted.keys()),
    }


def augment_hero_with_session(hero: HeroDefinition, activity: dict[str, Any]) -> None:
    """Merge live-session candidate/accepted frequency into hero sets (proxy)."""
    if not activity.get("found"):
        return
    cand = set(activity.get("candidate_top20") or [])
    acc = set(activity.get("accepted_top20") or [])
    if cand:
        hero.hero_top20 |= cand
        hero.hero_sources["session_candidate_top20"] = sorted(cand)
        hero.proxy_notes.append(f"session_candidate_top20:{len(cand)}")
    if acc:
        hero.hero_top20 |= acc
        hero.hero_top10 |= acc
        hero.hero_sources["session_accepted_top20"] = sorted(acc)
        hero.proxy_notes.append(f"session_accepted_top20:{len(acc)}")
    hero.session_candidate_top20 = cand
    hero.session_accepted_symbols = set(activity.get("accepted_symbols") or [])


def link_session_to_opening(
    activity: dict[str, Any],
    opening50: set[str],
    static27: set[str],
) -> dict[str, Any]:
    cand_top = set(activity.get("candidate_top20") or [])
    activity["opening50_in_candidate_top20"] = sorted(cand_top & opening50)
    activity["static27_in_candidate_top20"] = sorted(cand_top & static27)
    activity["candidate_top20_not_in_opening50"] = sorted(cand_top - opening50)
    activity["candidate_top20_not_in_static27"] = sorted(cand_top - static27)
    return activity
