"""
Phase374: Dynamic40 universe quality review from historical paper/small_paper sessions.

Universe selection quality evaluation only — no trading logic changes.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase365_production_stack_validation import stack_blocked
from research.phase366_stophit_reclassification import production_kept_trades
from small_paper.discord_symbol_names import get_cached_symbol_name_map
from small_paper.near_day_high_low_momentum_dynamic40_entry_guard import (
    REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
)
from small_paper.pullback_misread_dynamic40_entry_guard import (
    REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
)
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

JST = ZoneInfo("Asia/Tokyo")
PRODUCTION_STACK_MIN_DAY = "20260529"

GUARD_REJECT_REASONS = frozenset(
    {
        REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
        REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
    }
)

QUALITY_CLASSIFICATION_RULES: dict[str, dict[str, Any]] = {
    "A_profitable_core": {
        "label": "profitable_core",
        "conditions": {
            "entry_count_gte": 3,
            "profit_factor_gt": 1.2,
            "total_pnl_yen_100_gt": 0,
        },
    },
    "B_neutral_watch": {
        "label": "neutral_watch",
        "conditions": {
            "entry_count_gte": 3,
            "profit_factor_gte": 0.8,
            "profit_factor_lte": 1.2,
        },
    },
    "C_harmful_watch": {
        "label": "harmful_watch",
        "conditions": {
            "entry_count_gte": 3,
            "profit_factor_lt": 0.8,
            "total_pnl_yen_100_lt": 0,
        },
    },
    "D_dead_watch": {
        "label": "dead_watch",
        "conditions": {
            "session_count_monitored_gte": 2,
            "entry_count_eq": 0,
        },
    },
    "E_low_quality_watch": {
        "label": "low_quality_watch",
        "conditions": {
            "entry_count_gte": 1,
            "stop_hit_rate_gte": 0.5,
            "avg_mfe_pct_lt": 0.3,
            "avg_hold_minutes_gte": 30.0,
            "total_pnl_yen_100_lt": 0,
        },
    },
}

CLASSIFICATION_PRIORITY = (
    "D_dead_watch",
    "A_profitable_core",
    "C_harmful_watch",
    "B_neutral_watch",
    "E_low_quality_watch",
)

RANK_BUCKETS = (
    ("rank_1_10", 1, 10),
    ("rank_11_20", 11, 20),
    ("rank_21_30", 21, 30),
    ("rank_31_40", 31, 40),
    ("rank_unknown", None, None),
)

SYMBOL_FIELDS = [
    "symbol",
    "name",
    "universe_group",
    "quality_class",
    "rank_bucket_mode",
    "session_count_monitored",
    "entry_count",
    "production_stack_entry_count",
    "win_count",
    "loss_count",
    "win_rate",
    "total_pnl_pct",
    "total_pnl_yen_100",
    "production_stack_total_pnl_yen_100",
    "avg_pnl_pct",
    "avg_pnl_yen_100",
    "profit_factor",
    "avg_mfe_pct",
    "avg_mae_pct",
    "stop_hit_count",
    "stop_hit_rate",
    "avg_hold_minutes",
    "max_concurrent_reject_count",
    "low_liquidity_flag_count",
    "guard_reject_count",
    "first_seen_session",
    "last_seen_session",
]

RANK_BUCKET_FIELDS = [
    "rank_bucket",
    "monitored_symbol_count",
    "entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "avg_mfe_pct",
    "stop_hit_rate",
    "avg_hold_minutes",
    "harmful_watch_count",
    "profitable_core_count",
    "production_stack_entry_count",
    "production_stack_total_pnl_yen_100",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _event_reject_reason(row: Mapping[str, Any]) -> str:
    return str(row.get("gate_reject_reason") or row.get("reject_reason") or "").strip()


def _norm_symbol(sym: str) -> str:
    s = str(sym or "").strip().upper()
    if not s:
        return ""
    if "." not in s and s.isdigit():
        return f"{s}.T"
    return s


def resolve_pnl_yen_100(row: Mapping[str, Any]) -> Optional[float]:
    direct = _float(row.get("pnl_yen_100"))
    if direct is not None:
        return direct
    shadow = _float(row.get("shadow_pnl_yen_100"))
    if shadow is not None:
        return shadow
    ep = _float(row.get("entry_price"))
    xp = _float(row.get("exit_price"))
    if ep is not None and xp is not None:
        return round((xp - ep) * 100.0, 2)
    return None


def resolve_pnl_pct(row: Mapping[str, Any]) -> Optional[float]:
    return _float(row.get("pnl_pct"))


def resolve_mae_pct(row: Mapping[str, Any]) -> Optional[float]:
    return _float(row.get("max_adverse_excursion_pct")) or _float(row.get("rolling_mae_pct"))


def discover_session_roots(repo_root: Path) -> list[Path]:
    roots: list[Path] = []
    for rel in ("kabu_native/results/small_paper", "kabu_native/results/paper_trade"):
        p = repo_root / rel
        if p.is_dir():
            roots.append(p)
    return roots


def discover_sessions_for_phase374(
    roots: Sequence[Path],
    *,
    min_day: Optional[str] = None,
    max_day: Optional[str] = None,
    recent_days: Optional[int] = None,
    all_available: bool = False,
) -> list[dict[str, Any]]:
    import json as _json

    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _session_source_label,
    )

    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        for summary_path in sorted(root.rglob("small_paper_summary.json")):
            sess_dir = summary_path.parent
            key = str(sess_dir.resolve())
            if key in seen:
                continue
            seen.add(key)
            day = sess_dir.parent.name
            if not day.isdigit() or len(day) != 8:
                continue
            if min_day and day < min_day:
                continue
            if max_day and day > max_day:
                continue
            if not (sess_dir / "small_paper_events.csv").is_file():
                continue
            try:
                summary = _json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, _json.JSONDecodeError):
                continue
            if not all_available and str(summary.get("source") or "") == "push-replay":
                continue
            kind = _infer_session_kind(sess_dir, summary)
            sessions.append(
                {
                    "session_id": f"{day}/{sess_dir.name}",
                    "day_key": day,
                    "day": day,
                    "session_dir": str(sess_dir),
                    "session_kind": kind,
                    "session_source": _session_source_label(sess_dir),
                    "summary": summary,
                }
            )
    sessions.sort(key=lambda s: s["session_id"])
    if recent_days is not None and recent_days > 0:
        days = sorted({str(s["day_key"]) for s in sessions}, reverse=True)[:recent_days]
        day_set = set(days)
        sessions = [s for s in sessions if str(s["day_key"]) in day_set]
    return sessions


def _universe_path_candidates(
    day: str, session_kind: str, summary: Mapping[str, Any], reports_dir: Path
) -> list[Path]:
    from small_paper.limit_up_proximity_entry_guard_shadow import _universe_path_for_session

    paths: list[Path] = []
    refresh = summary.get("intraday_refresh_csv")
    if refresh:
        paths.append(Path(str(refresh)))
    paths.append(_universe_path_for_session(day, session_kind, dict(summary), reports_dir))
    for pattern in (
        f"universe_core10_dynamic40_price_risk_{session_kind}_{day}.csv",
        f"universe_core10_dynamic40_{session_kind}_{day}.csv",
        f"universe_core10_dynamic40_price_risk_{session_kind}_refresh*_{day}.csv",
    ):
        if "*" in pattern:
            paths.extend(reports_dir.glob(pattern))
        else:
            paths.append(reports_dir / pattern)
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            out.append(p)
    return out


def load_universe_csv(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {str(r.get("symbol") or ""): dict(r) for r in csv.DictReader(f)}


def dynamic40_monitored_from_universe(
    universe: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, Any]]:
    dynamic_rows = [
        row
        for row in universe.values()
        if str(row.get("universe_slot") or "").lower() == "dynamic"
    ]
    dynamic_rows.sort(key=lambda r: _float(r.get("rank")) or 9999.0)
    out: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(dynamic_rows, start=1):
        sym = _norm_symbol(str(row.get("symbol") or ""))
        if not sym:
            continue
        overall_rank = _float(row.get("rank"))
        dynamic_rank: Optional[int] = i
        out[sym] = {
            "symbol": sym,
            "universe_slot": "dynamic",
            "universe_group": "dynamic40",
            "overall_rank": int(overall_rank) if overall_rank is not None else None,
            "dynamic_rank": dynamic_rank,
            "rank_bucket": rank_bucket(dynamic_rank),
            "volatility_liquidity_score": _float(row.get("volatility_liquidity_score")),
            "close_price": _float(row.get("close_price")),
        }
    return out


def rank_bucket(dynamic_rank: Optional[int]) -> str:
    if dynamic_rank is None:
        return "rank_unknown"
    for bucket_id, lo, hi in RANK_BUCKETS:
        if lo is None:
            continue
        if lo <= dynamic_rank <= hi:
            return bucket_id
    if dynamic_rank > 40:
        return "rank_31_40"
    return "rank_unknown"


def classify_symbol_quality(row: Mapping[str, Any]) -> str:
    entry = int(row.get("entry_count") or 0)
    monitored = int(row.get("session_count_monitored") or 0)
    pf = _float(row.get("profit_factor"))
    total_yen = _float(row.get("total_pnl_yen_100"))
    stop_rate = _float(row.get("stop_hit_rate"))
    avg_mfe = _float(row.get("avg_mfe_pct"))
    avg_hold = _float(row.get("avg_hold_minutes"))

    checks: dict[str, bool] = {
        "D_dead_watch": monitored >= 2 and entry == 0,
        "A_profitable_core": entry >= 3 and pf is not None and pf > 1.2
        and total_yen is not None
        and total_yen > 0,
        "C_harmful_watch": entry >= 3 and pf is not None and pf < 0.8
        and total_yen is not None
        and total_yen < 0,
        "B_neutral_watch": entry >= 3 and pf is not None and 0.8 <= pf <= 1.2,
        "E_low_quality_watch": entry >= 1
        and stop_rate is not None
        and stop_rate >= 0.5
        and avg_mfe is not None
        and avg_mfe < 0.3
        and avg_hold is not None
        and avg_hold >= 30.0
        and total_yen is not None
        and total_yen < 0,
    }
    for class_id in CLASSIFICATION_PRIORITY:
        if checks.get(class_id):
            return QUALITY_CLASSIFICATION_RULES[class_id]["label"]
    return "unclassified"


def _metrics_from_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    yens: list[float] = []
    pcts: list[float] = []
    mfes: list[float] = []
    maes: list[float] = []
    holds: list[float] = []
    wins = losses = stops = 0
    for t in trades:
        yen = _float(t.get("pnl_yen_100"))
        pct = _float(t.get("pnl_pct"))
        if yen is not None:
            yens.append(float(yen))
            if yen > 0:
                wins += 1
            elif yen < 0:
                losses += 1
        if pct is not None:
            pcts.append(float(pct))
        mfe = _float(t.get("peak_mfe_pct"))
        if mfe is not None:
            mfes.append(float(mfe))
        mae = _float(t.get("mae_pct"))
        if mae is not None:
            maes.append(float(mae))
        hold = _float(t.get("hold_sec"))
        if hold is not None:
            holds.append(float(hold))
        if str(t.get("exit_reason_canonical") or "") == "stop_hit":
            stops += 1
    n = len(trades)
    total_yen = round(sum(yens), 2) if yens else None
    return {
        "entry_count": n,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wins / n, 4) if n else None,
        "total_pnl_yen_100": total_yen,
        "total_pnl_pct": round(sum(pcts), 4) if pcts else None,
        "avg_pnl_yen_100": round(total_yen / n, 2) if total_yen is not None and n else None,
        "avg_pnl_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
        "profit_factor": _pf(yens),
        "avg_mfe_pct": round(sum(mfes) / len(mfes), 4) if mfes else None,
        "avg_mae_pct": round(sum(maes) / len(maes), 4) if maes else None,
        "stop_hit_count": stops,
        "stop_hit_rate": round(stops / n, 4) if n else None,
        "avg_hold_minutes": round(sum(holds) / len(holds) / 60.0, 2) if holds else None,
    }


def load_session_phase374(
    session_meta: Mapping[str, Any],
    *,
    reports_dir: Path,
    streaming: bool = True,
) -> dict[str, Any]:
    from research.phase357_actual_exit_audit import _trade_row_from_exit, _universe_group
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _load_session_summary,
    )

    del streaming
    sess_dir = Path(str(session_meta["session_dir"]))
    events_path = sess_dir / "small_paper_events.csv"
    if not events_path.is_file():
        return {"error": "missing_events_csv", "session_meta": dict(session_meta)}

    summary = session_meta.get("summary") or _load_session_summary(sess_dir)
    session_kind = str(session_meta.get("session_kind") or _infer_session_kind(sess_dir, summary))
    day = str(session_meta.get("day_key") or session_meta.get("day") or sess_dir.parent.name)

    universe_path = None
    universe_all: dict[str, dict[str, str]] = {}
    for candidate in _universe_path_candidates(day, session_kind, summary, reports_dir):
        universe_all = load_universe_csv(candidate)
        if universe_all:
            universe_path = candidate
            break

    dynamic_monitored = dynamic40_monitored_from_universe(universe_all)

    reject_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "max_concurrent_reject_count": 0,
            "low_liquidity_flag_count": 0,
            "guard_reject_count": 0,
            "rejected_count": 0,
        }
    )

    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(events_path):
        et = str(row.get("event_type") or "")
        sym = _norm_symbol(str(row.get("symbol") or ""))
        if et == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row
        elif et == "rejected" and sym:
            reason = _event_reject_reason(row)
            reject_stats[sym]["rejected_count"] += 1
            if reason == "max_concurrent":
                reject_stats[sym]["max_concurrent_reject_count"] += 1
            if reason in ("trading_value_below_min", "turnover_proxy_below_min", "low_liquidity"):
                reject_stats[sym]["low_liquidity_flag_count"] += 1
            if reason in GUARD_REJECT_REASONS:
                reject_stats[sym]["guard_reject_count"] += 1

    rejects_path = sess_dir / "small_paper_rejects.csv"
    if rejects_path.is_file():
        for row in csv.DictReader(rejects_path.open(encoding="utf-8", newline="")):
            sym = _norm_symbol(str(row.get("symbol") or ""))
            if not sym:
                continue
            reason = _event_reject_reason(row)
            if reason == "max_concurrent":
                reject_stats[sym]["max_concurrent_reject_count"] += 1
            if reason in GUARD_REJECT_REASONS:
                reject_stats[sym]["guard_reject_count"] += 1

    trades: list[dict[str, Any]] = []
    for row in _stream_events_csv(events_path):
        if row.get("event_type") != "observer_exit" or row.get("pnl_pct") in (None, ""):
            continue
        key = (row.get("symbol", ""), row.get("entry_time", ""))
        acc = accepted.get(key, {})
        trade = _trade_row_from_exit(
            session_meta={**session_meta, "session_kind": session_kind},
            acc=acc,
            ex=row,
            universe=universe_all,
            kept=None,
        )
        trade["pnl_yen_100"] = resolve_pnl_yen_100(row)
        trade["pnl_pct"] = resolve_pnl_pct(row)
        trade["mae_pct"] = resolve_mae_pct(row)
        trade["universe_group"] = _universe_group(trade)
        sym = _norm_symbol(str(trade.get("symbol") or ""))
        mon = dynamic_monitored.get(sym, {})
        trade["dynamic_rank"] = mon.get("dynamic_rank")
        trade["rank_bucket"] = mon.get("rank_bucket", "rank_unknown")
        trade["population"] = "all_observer_exit"
        trades.append(trade)

    production_trades: list[dict[str, Any]] = []
    if day >= PRODUCTION_STACK_MIN_DAY:
        from research.phase365_production_stack_validation import (
            load_session_production_stack_trades,
        )

        stack_base = load_session_production_stack_trades(
            session_meta, reports_dir=reports_dir
        )
        if not stack_base.get("error"):
            for t in production_kept_trades(stack_base):
                row = dict(t)
                row["population"] = "production_stack"
                sym = _norm_symbol(str(row.get("symbol") or ""))
                mon = dynamic_monitored.get(sym, {})
                row["dynamic_rank"] = mon.get("dynamic_rank")
                row["rank_bucket"] = mon.get("rank_bucket", "rank_unknown")
                if row.get("mae_pct") is None:
                    row["mae_pct"] = resolve_mae_pct(row)
                production_trades.append(row)

    return {
        "session_meta": dict(session_meta),
        "session_kind": session_kind,
        "day_key": day,
        "universe_path": str(universe_path) if universe_path else None,
        "dynamic_monitored": dynamic_monitored,
        "reject_stats": dict(reject_stats),
        "trades": trades,
        "production_trades": production_trades,
        "error": "",
    }


def build_selection_logic_markdown(day_stamp: str) -> str:
    return f"""# Phase374 Dynamic40 Selection Logic Review ({day_stamp})

## Primary implementation files

| File | Functions | Role |
|------|-----------|------|
| `kabu_native/src/universe/core10_dynamic40.py` | `build_am_universe`, `build_pm_universe`, `select_dynamic_vol_liq`, `build_dynamic_rows` | Core10 + Dynamic40 builder |
| `kabu_native/src/universe/core10_dynamic40_price_risk.py` | `build_am_universe_price_risk`, `select_dynamic_vol_liq_price_risk` | Price/tick filter on dynamic slots (production default) |
| `kabu_native/src/universe/opening_screen.py` | `volatility_liquidity_score` | Base ranking score |
| `kabu_native/src/universe/am_pm_universe.py` | `build_pm_universe_rows`, `compute_pm_composite_scores` | PM composite ranking |
| `kabu_native/src/runner/am_pm_daily_runner.py` | `build_am_universe`, `build_pm_universe`, `build_intraday_refresh_universes` | Daily orchestration |
| `kabu_native/src/universe/intraday_refresh.py` | refresh merge | 10:00 / 14:30 universe refresh |

## Input data

- `features_YYYYMMDD.csv` — prior-day OHLCV via yfinance (`atr_pct`, `trading_value`, `volatility_liquidity_score`)
- Discord Core10 watchlist (`core_watchlist.py` / `watchlist.json`) — fixed 10 core slots
- PUSH JSONL (`kabu_native/data/push_jsonl/`) — PM composite intraday metrics
- JPX symbol master — exchange / symbol_key metadata

## Composition

- **Core10**: `universe_slot=core`, `source_bucket=core10_discord`, ranks 1–10
- **Dynamic40**: `universe_slot=dynamic`, `source_bucket=vol_liq_dynamic40`, ranks 11–50 (up to 40 dynamic names)
- Total register cap: **50 symbols**

## Filters (dynamic40 path)

| Filter | Location | Condition |
|--------|----------|-----------|
| Core exclusion | `select_dynamic_vol_liq` | Skip Discord Core10 symbols |
| Missing vol_liq | `select_dynamic_vol_liq` | Skip rows without `volatility_liquidity_score` |
| Price floor (production) | `price_risk_filter.py` | Dynamic: `close >= 300` yen |
| Tick ratio (production) | `price_risk_filter.py` | Dynamic: `tick_ratio_pct <= 5%` |

## Ranking score

### AM dynamic40
```
volatility_liquidity_score = atr_pct × log10(max(trading_value_jpy, 1))
```
Rank all feature rows descending; take top N non-core (N ≤ 40).

### PM dynamic40
Weighted percentile composite (`compute_pm_composite_scores`):
- Previous-day vol_liq: 30%
- Morning trading value: 20%
- Morning range %: 15%
- Morning volume: 10%
- PM trading value: 15%
- PM board liquidity proxy: 10%

Backfill with AM vol_liq if PM composite pool is short.

## Conditions explicitly NOT in dynamic40 selection

- **VWAP** — not used in universe ranking (VWAP appears only at ENTRY evaluation)
- **Sector / market index** — no sector filter in `core10_dynamic40*`
- **Board REST** — not used in core10_dynamic40 (PUSH bid/ask used for PM composite only)
- **Direct day-trade capital concentration metric** — none; only implicit via `trading_value` inside vol_liq score

## Intraday refresh

- AM 10:00 → `universe_core10_dynamic40_price_risk_am_refresh1000_YYYYMMDD.csv`
- PM 14:30 → `universe_core10_dynamic40_price_risk_pm_refresh1430_YYYYMMDD.csv`
- Open positions preserved; hard cap 50 total

## watch_symbols derivation

No separate watch list builder. `load_symbols_from_universe()` reads `passed=True` rows from universe CSV; daily runner and pilot register those symbols for PUSH.
"""


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase374Dynamic40UniverseQualityReview:
    reports_dir: Path
    repo_root: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)
    day_stamp: str = ""

    def paths(self, day_stamp: str) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / f"phase374_dynamic40_universe_quality_review_{day_stamp}.json",
            "symbol_csv": self.reports_dir / f"phase374_dynamic40_symbol_quality_{day_stamp}.csv",
            "rank_bucket_csv": self.reports_dir
            / f"phase374_dynamic40_rank_bucket_{day_stamp}.csv",
            "recommendation_md": self.reports_dir
            / f"phase374_dynamic40_recommendation_{day_stamp}.md",
            "selection_logic_md": self.reports_dir
            / f"phase374_dynamic40_selection_logic_{day_stamp}.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.session_results.append(dict(result))

    def _aggregate(self) -> dict[str, Any]:
        name_map = get_cached_symbol_name_map()

        monitored_sessions: dict[str, set[str]] = defaultdict(set)
        monitored_rank_bucket: dict[str, str] = {}
        reject_agg: dict[str, dict[str, int]] = defaultdict(
            lambda: {
                "max_concurrent_reject_count": 0,
                "low_liquidity_flag_count": 0,
                "guard_reject_count": 0,
            }
        )
        trades_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        prod_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        first_seen: dict[str, str] = {}
        last_seen: dict[str, str] = {}
        rank_bucket_mode: dict[str, str] = {}

        for sr in self.session_results:
            sid = str((sr.get("session_meta") or {}).get("session_id") or "")
            for sym, meta in (sr.get("dynamic_monitored") or {}).items():
                monitored_sessions[sym].add(sid)
                monitored_rank_bucket[sym] = str(meta.get("rank_bucket") or "rank_unknown")
            for sym, stats in (sr.get("reject_stats") or {}).items():
                for k in ("max_concurrent_reject_count", "low_liquidity_flag_count", "guard_reject_count"):
                    reject_agg[sym][k] += int(stats.get(k) or 0)
            for t in sr.get("trades") or []:
                sym = _norm_symbol(str(t.get("symbol") or ""))
                if not sym:
                    continue
                trades_by_symbol[sym].append(t)
                if sym not in first_seen or sid < first_seen[sym]:
                    first_seen[sym] = sid
                if sym not in last_seen or sid > last_seen[sym]:
                    last_seen[sym] = sid
                if t.get("rank_bucket"):
                    rank_bucket_mode[sym] = str(t.get("rank_bucket"))
            for t in sr.get("production_trades") or []:
                sym = _norm_symbol(str(t.get("symbol") or ""))
                if sym:
                    prod_by_symbol[sym].append(t)

        all_monitored = set(monitored_sessions.keys())
        all_traded = set(trades_by_symbol.keys())
        symbols = sorted(all_monitored | all_traded)

        symbol_rows: list[dict[str, Any]] = []
        class_counts: dict[str, int] = defaultdict(int)

        for sym in symbols:
            all_trades = [t for t in trades_by_symbol.get(sym, []) if t.get("universe_group") == "dynamic40"]
            prod_trades = [t for t in prod_by_symbol.get(sym, []) if t.get("universe_group") == "dynamic40"]
            metrics = _metrics_from_trades(all_trades)
            prod_metrics = _metrics_from_trades(prod_trades)
            monitored = len(monitored_sessions.get(sym, set()))
            row_base = {
                "symbol": sym,
                "name": name_map.get(sym) or None,
                "universe_group": "dynamic40" if sym in all_monitored or all_trades else "other",
                "session_count_monitored": monitored,
                "production_stack_entry_count": prod_metrics["entry_count"],
                "production_stack_total_pnl_yen_100": prod_metrics["total_pnl_yen_100"],
                "max_concurrent_reject_count": reject_agg[sym]["max_concurrent_reject_count"] or None,
                "low_liquidity_flag_count": reject_agg[sym]["low_liquidity_flag_count"] or None,
                "guard_reject_count": reject_agg[sym]["guard_reject_count"] or None,
                "first_seen_session": first_seen.get(sym),
                "last_seen_session": last_seen.get(sym),
                "rank_bucket_mode": rank_bucket_mode.get(sym) or monitored_rank_bucket.get(sym, "rank_unknown"),
                **metrics,
            }
            row_base["quality_class"] = classify_symbol_quality(row_base)
            class_counts[row_base["quality_class"]] += 1
            symbol_rows.append(row_base)

        core10_rows: list[dict[str, Any]] = []
        for sym in sorted(set(trades_by_symbol.keys()) - all_monitored):
            all_trades = [t for t in trades_by_symbol[sym] if t.get("universe_group") == "core10"]
            if not all_trades:
                continue
            metrics = _metrics_from_trades(all_trades)
            prod_trades = [t for t in prod_by_symbol.get(sym, []) if t.get("universe_group") == "core10"]
            prod_metrics = _metrics_from_trades(prod_trades)
            row = {
                "symbol": sym,
                "name": name_map.get(sym) or None,
                "universe_group": "core10",
                "session_count_monitored": 0,
                "production_stack_entry_count": prod_metrics["entry_count"],
                "production_stack_total_pnl_yen_100": prod_metrics["total_pnl_yen_100"],
                "max_concurrent_reject_count": reject_agg[sym]["max_concurrent_reject_count"] or None,
                "low_liquidity_flag_count": reject_agg[sym]["low_liquidity_flag_count"] or None,
                "guard_reject_count": reject_agg[sym]["guard_reject_count"] or None,
                "first_seen_session": first_seen.get(sym),
                "last_seen_session": last_seen.get(sym),
                "rank_bucket_mode": None,
                "quality_class": classify_symbol_quality(
                    {**metrics, "session_count_monitored": 0, "entry_count": metrics["entry_count"]}
                ),
                **metrics,
            }
            class_counts[row["quality_class"]] += 1
            core10_rows.append(row)

        dynamic_trades = [
            t
            for sr in self.session_results
            for t in sr.get("trades") or []
            if t.get("universe_group") == "dynamic40"
        ]
        dynamic_prod = [
            t
            for sr in self.session_results
            for t in sr.get("production_trades") or []
            if t.get("universe_group") == "dynamic40"
        ]

        rank_bucket_rows: list[dict[str, Any]] = []
        monitored_by_bucket: dict[str, set[str]] = defaultdict(set)
        for sym, bucket in monitored_rank_bucket.items():
            monitored_by_bucket[bucket].add(sym)

        symbol_by_sym = {r["symbol"]: r for r in symbol_rows}
        for bucket_id, _, _ in RANK_BUCKETS:
            bucket_trades = [t for t in dynamic_trades if t.get("rank_bucket") == bucket_id]
            bucket_prod = [t for t in dynamic_prod if t.get("rank_bucket") == bucket_id]
            bm = _metrics_from_trades(bucket_trades)
            pm = _metrics_from_trades(bucket_prod)
            harmful = sum(
                1
                for s in monitored_by_bucket.get(bucket_id, set())
                if symbol_by_sym.get(s, {}).get("quality_class") == "harmful_watch"
            )
            profitable = sum(
                1
                for s in monitored_by_bucket.get(bucket_id, set())
                if symbol_by_sym.get(s, {}).get("quality_class") == "profitable_core"
            )
            rank_bucket_rows.append(
                {
                    "rank_bucket": bucket_id,
                    "monitored_symbol_count": len(monitored_by_bucket.get(bucket_id, set())),
                    "entry_count": bm["entry_count"],
                    "total_pnl_yen_100": bm["total_pnl_yen_100"],
                    "profit_factor": bm["profit_factor"],
                    "avg_mfe_pct": bm["avg_mfe_pct"],
                    "stop_hit_rate": bm["stop_hit_rate"],
                    "avg_hold_minutes": bm["avg_hold_minutes"],
                    "harmful_watch_count": harmful,
                    "profitable_core_count": profitable,
                    "production_stack_entry_count": pm["entry_count"],
                    "production_stack_total_pnl_yen_100": pm["total_pnl_yen_100"],
                }
            )

        rank_unknown_share = (
            len(monitored_by_bucket.get("rank_unknown", set())) / len(all_monitored)
            if all_monitored
            else None
        )

        return {
            "symbol_rows": symbol_rows,
            "core10_rows": core10_rows,
            "rank_bucket_rows": rank_bucket_rows,
            "class_counts": dict(class_counts),
            "dynamic40_metrics": _metrics_from_trades(dynamic_trades),
            "dynamic40_production_metrics": _metrics_from_trades(dynamic_prod),
            "monitored_symbol_count": len(all_monitored),
            "rank_unknown_share": rank_unknown_share,
            "sessions_evaluated": len(self.session_results),
        }

    def _counterfactual_candidates(
        self, dynamic_trades: Sequence[Mapping[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        def subset(pred: Callable[[Mapping[str, Any]], bool]) -> list[dict[str, Any]]:
            return [dict(t) for t in dynamic_trades if pred(t)]

        def summarize(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
            return _metrics_from_trades(trades)

        all_t = list(dynamic_trades)
        top20 = subset(lambda t: (_float(t.get("dynamic_rank")) or 999) <= 20)
        top30 = subset(lambda t: (_float(t.get("dynamic_rank")) or 999) <= 30)
        bottom20 = subset(lambda t: (_float(t.get("dynamic_rank")) or 0) >= 21)
        backup20 = subset(lambda t: 21 <= (_float(t.get("dynamic_rank")) or 0) <= 40)

        return {
            "A_keep_dynamic40_improve_ranking": summarize(all_t),
            "B_dynamic30": summarize(top30),
            "C_entry_top20_only": summarize(top20),
            "D_core_trade_candidates_20": summarize(top20),
            "D_backup_watch_20": summarize(backup20),
            "E_exclude_bottom20_counterfactual": summarize(top20),
            "baseline_all_dynamic40": summarize(all_t),
            "bottom_rank_21_40": summarize(bottom20),
        }

    def _verdicts(self, agg: Mapping[str, Any], candidates: Mapping[str, Any]) -> dict[str, Any]:
        dyn = agg["dynamic40_metrics"]
        rb = {r["rank_bucket"]: r for r in agg["rank_bucket_rows"]}
        class_counts = agg["class_counts"]
        dead = class_counts.get("dead_watch", 0)
        harmful = class_counts.get("harmful_watch", 0)
        profitable = class_counts.get("profitable_core", 0)
        monitored = agg["monitored_symbol_count"] or 1

        bottom_pnl = _float((rb.get("rank_31_40") or {}).get("total_pnl_yen_100")) or 0.0
        top_pnl = sum(
            _float((rb.get(b) or {}).get("total_pnl_yen_100")) or 0.0
            for b in ("rank_1_10", "rank_11_20")
        )
        bottom_bad = bottom_pnl < 0 and abs(bottom_pnl) > abs(top_pnl) * 0.5

        dead_heavy = dead / monitored >= 0.3
        harmful_persistent = harmful >= 5

        shrink_ok = (
            (_float(candidates["B_dynamic30"].get("profit_factor")) or 0)
            >= (_float(dyn.get("profit_factor")) or 0)
            and (_float(candidates["B_dynamic30"].get("total_pnl_yen_100")) or 0)
            >= (_float(dyn.get("total_pnl_yen_100")) or 0) * 0.9
        )
        ranking_ok = not bottom_bad and harmful_persistent

        rank_unknown_share = agg.get("rank_unknown_share")
        rank_insufficient = rank_unknown_share is not None and rank_unknown_share > 0.15

        next_validation = "C_entry_top20_only"
        if shrink_ok and bottom_bad:
            next_validation = "B_dynamic30"
        elif ranking_ok:
            next_validation = "A_keep_dynamic40_improve_ranking"
        elif rank_insufficient:
            next_validation = "fix_rank_snapshot_coverage"

        return {
            "dynamic40_too_many_symbols": dead_heavy and not bottom_bad,
            "problem_is_symbol_count_vs_rank_quality": (
                "rank_quality" if bottom_bad and not dead_heavy else (
                    "symbol_count" if dead_heavy and not bottom_bad else "mixed"
                )
            ),
            "lower_rank_buckets_are_loss_source": bottom_bad,
            "dead_watch_heavy": dead_heavy,
            "harmful_watch_persistent": harmful_persistent,
            "dynamic20_or_30_shrink_reasonable": shrink_ok or bottom_bad,
            "dynamic40_keep_and_improve_ranking_reasonable": ranking_ok or not shrink_ok,
            "rank_info_insufficient_items": (
                ["rank_bucket_coverage", "dynamic_rank_assignment"]
                if rank_insufficient
                else []
            ),
            "next_universe_validation": next_validation,
            "dead_watch_count": dead,
            "harmful_watch_count": harmful,
            "profitable_core_count": profitable,
            "rank_unknown_share": rank_unknown_share,
        }

    def build_recommendation_markdown(
        self, agg: Mapping[str, Any], candidates: Mapping[str, Any], verdicts: Mapping[str, Any]
    ) -> str:
        dyn = agg["dynamic40_metrics"]
        lines = [
            f"# Phase374 Dynamic40 Universe Quality Recommendation ({self.day_stamp})",
            "",
            "## Executive verdict",
            "",
            f"- **dynamic40 too many?** {verdicts['dynamic40_too_many_symbols']}",
            f"- **Root cause (count vs rank quality):** {verdicts['problem_is_symbol_count_vs_rank_quality']}",
            f"- **Lower rank buckets loss source?** {verdicts['lower_rank_buckets_are_loss_source']}",
            f"- **dead_watch heavy?** {verdicts['dead_watch_heavy']} ({verdicts['dead_watch_count']} symbols)",
            f"- **harmful_watch persistent?** {verdicts['harmful_watch_persistent']} ({verdicts['harmful_watch_count']} symbols)",
            f"- **Shrink to dynamic20/30 reasonable?** {verdicts['dynamic20_or_30_shrink_reasonable']}",
            f"- **Keep dynamic40 + improve ranking?** {verdicts['dynamic40_keep_and_improve_ranking_reasonable']}",
            f"- **Next validation:** {verdicts['next_universe_validation']}",
            "",
            "## Dynamic40 aggregate",
            "",
            f"- Monitored symbols: {agg['monitored_symbol_count']}",
            f"- Entries: {dyn.get('entry_count')}",
            f"- Total PnL (yen/100): {dyn.get('total_pnl_yen_100')}",
            f"- Profit factor: {dyn.get('profit_factor')}",
            f"- Stop-hit rate: {dyn.get('stop_hit_rate')}",
            "",
            "## Quality class counts",
            "",
        ]
        for label in (
            "profitable_core",
            "neutral_watch",
            "harmful_watch",
            "dead_watch",
            "low_quality_watch",
            "unclassified",
        ):
            lines.append(f"- {label}: {agg['class_counts'].get(label, 0)}")
        lines.extend(["", "## Rank bucket PnL", ""])
        for row in agg["rank_bucket_rows"]:
            lines.append(
                f"- {row['rank_bucket']}: entries={row['entry_count']} "
                f"pnl={row['total_pnl_yen_100']} pf={row['profit_factor']} "
                f"monitored={row['monitored_symbol_count']}"
            )
        lines.extend(["", "## Candidate comparison (counterfactual, no logic change)", ""])
        for key in (
            "A_keep_dynamic40_improve_ranking",
            "B_dynamic30",
            "C_entry_top20_only",
            "D_core_trade_candidates_20",
            "D_backup_watch_20",
            "E_exclude_bottom20_counterfactual",
        ):
            m = candidates[key]
            lines.append(
                f"- **{key}**: entries={m.get('entry_count')} "
                f"pnl={m.get('total_pnl_yen_100')} pf={m.get('profit_factor')} "
                f"stop_rate={m.get('stop_hit_rate')}"
            )
        if verdicts.get("rank_info_insufficient_items"):
            lines.extend(
                [
                    "",
                    "## Rank coverage gaps",
                    "",
                    f"- rank_unknown_share: {verdicts.get('rank_unknown_share')}",
                    f"- insufficient: {', '.join(verdicts['rank_info_insufficient_items'])}",
                ]
            )
        return "\n".join(lines) + "\n"

    def finalize_outputs(
        self,
        *,
        wall_runtime_sec: float,
        sessions_discovered: int,
        min_day: Optional[str],
        max_day: Optional[str],
    ) -> dict[str, Path]:
        if not self.day_stamp:
            days = [str(sr.get("day_key") or "") for sr in self.session_results if sr.get("day_key")]
            self.day_stamp = max(days) if days else datetime.now(JST).strftime("%Y%m%d")

        paths = self.paths(self.day_stamp)
        agg = self._aggregate()
        dynamic_trades = [
            t
            for sr in self.session_results
            for t in sr.get("trades") or []
            if t.get("universe_group") == "dynamic40"
        ]
        candidates = self._counterfactual_candidates(dynamic_trades)
        verdicts = self._verdicts(agg, candidates)

        summary = {
            "phase": 374,
            "title": "Dynamic40 Universe Quality Review",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": {
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": agg["sessions_evaluated"],
                "min_day": min_day,
                "max_day": max_day,
                "production_stack_min_day": PRODUCTION_STACK_MIN_DAY,
            },
            "quality_classification_rules": QUALITY_CLASSIFICATION_RULES,
            "dynamic40_summary": {
                "monitored_symbol_count": agg["monitored_symbol_count"],
                **agg["dynamic40_metrics"],
                "production_stack": agg["dynamic40_production_metrics"],
            },
            "quality_class_counts": agg["class_counts"],
            "rank_bucket_summary": agg["rank_bucket_rows"],
            "counterfactual_candidates": candidates,
            "verdicts": verdicts,
            "core10_symbol_count": len(agg["core10_rows"]),
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "output_note": "Universe quality review only; ENTRY/EXIT/Discord/canonical PnL unchanged.",
        }

        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_csv(paths["symbol_csv"], agg["symbol_rows"] + agg["core10_rows"], SYMBOL_FIELDS)
        _write_csv(paths["rank_bucket_csv"], agg["rank_bucket_rows"], RANK_BUCKET_FIELDS)
        paths["selection_logic_md"].write_text(
            build_selection_logic_markdown(self.day_stamp), encoding="utf-8"
        )
        paths["recommendation_md"].write_text(
            self.build_recommendation_markdown(agg, candidates, verdicts), encoding="utf-8"
        )
        return paths
