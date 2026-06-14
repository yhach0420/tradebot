"""
Phase246-SectorHeat-Observation: measure predictive power of sector heat continuing to the next day.

Observation only — no Universe or Entry changes.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
PM_WINDOW_START = time(14, 0)
PM_WINDOW_END = time(15, 30)
TOP_SECTOR_COUNT = 3

SECTOR_BY_DAY_FIELDS = [
    "day",
    "sector_33_name",
    "symbol_count",
    "daily_return_pct",
    "trading_value_jpy",
    "trading_value_increase_pct",
    "pm_return_pct_1400_1530",
    "continuation_days",
    "heat_score",
    "heat_rank",
]

TOMORROW_TOP3_FIELDS = [
    "signal_day",
    "validation_day",
    "rank",
    "sector_33_name",
    "heat_score",
    "daily_return_pct",
    "trading_value_increase_pct",
    "pm_return_pct_1400_1530",
    "continuation_days",
]

VALIDATION_BY_DAY_FIELDS = [
    "signal_day",
    "validation_day",
    "predicted_sectors",
    "predicted_sector_trade_count",
    "predicted_sector_pnl_yen_100",
    "predicted_sector_profit_factor",
    "predicted_sector_win_rate",
    "predicted_sector_win_count",
    "predicted_sector_loss_count",
    "all_trade_count",
    "all_pnl_yen_100",
    "all_profit_factor",
    "all_win_rate",
    "predicted_sector_next_day_return_pct_avg",
    "predicted_sector_next_day_continued_positive_count",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> int:
    try:
        if val is None or val == "":
            return 0
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def _norm_symbol(sym: str) -> str:
    s = str(sym or "").strip().upper()
    if not s:
        return ""
    if "." not in s and s.isdigit():
        return f"{s}.T"
    return s


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _win_rate(yens: Sequence[float]) -> Optional[float]:
    if not yens:
        return None
    wins = sum(1 for y in yens if y > 0)
    return round(wins / len(yens), 4)


def _next_trading_day(day: str, available_days: Sequence[str]) -> Optional[str]:
    idx = None
    for i, d in enumerate(available_days):
        if d == day:
            idx = i
            break
    if idx is None or idx + 1 >= len(available_days):
        return None
    return available_days[idx + 1]


def _parse_bar_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt


def _jst_time(raw: str) -> Optional[time]:
    dt = _parse_bar_ts(raw)
    if dt is None:
        return None
    return dt.astimezone(JST).time()


def _in_pm_window(raw: str) -> bool:
    t = _jst_time(raw)
    if t is None:
        return False
    return PM_WINDOW_START <= t <= PM_WINDOW_END


def rank_normalize(values: dict[str, Optional[float]]) -> dict[str, float]:
    valid = [(k, v) for k, v in values.items() if v is not None and not math.isnan(v)]
    if not valid:
        return {k: 0.0 for k in values}
    valid.sort(key=lambda x: x[1])
    n = len(valid)
    out: dict[str, float] = {k: 0.0 for k in values}
    for i, (key, _) in enumerate(valid):
        out[key] = i / max(n - 1, 1)
    return out


def read_jpx_sector_map(repo_root: Path) -> dict[str, str]:
    path = repo_root / "data" / "jpx" / "tradable_symbols.csv"
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm_symbol(row.get("symbol") or "")
            if not sym:
                continue
            sector = str(row.get("sector_33_name") or "").strip() or "unknown"
            out[sym] = sector
    return out


def discover_intraday_days(data_roots: Sequence[Path]) -> list[str]:
    days: set[str] = set()
    for root in data_roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and len(child.name) == 10 and child.name[4] == "-":
                days.add(child.name.replace("-", ""))
    return sorted(days)


def resolve_intraday_day_dir(day: str, data_roots: Sequence[Path]) -> Optional[Path]:
    iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    for root in data_roots:
        candidate = root / iso
        if candidate.is_dir():
            return candidate
    return None


@dataclass
class SymbolDayMetrics:
    symbol: str
    sector: str
    open_price: Optional[float] = None
    close_price: Optional[float] = None
    daily_return_pct: Optional[float] = None
    trading_value_jpy: float = 0.0
    pm_start_price: Optional[float] = None
    pm_end_price: Optional[float] = None
    pm_return_pct_1400_1530: Optional[float] = None


def load_symbol_day_metrics(
    csv_path: Path,
    *,
    sector: str,
) -> Optional[SymbolDayMetrics]:
    sym = _norm_symbol(csv_path.stem)
    if not sym:
        return None
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return None
    if len(rows) < 3:
        return None

    opens: list[float] = []
    closes: list[float] = []
    tv = 0.0
    pm_start: Optional[float] = None
    pm_end: Optional[float] = None

    for row in rows:
        ts = str(row.get("timestamp_utc") or "")
        o = _float(row.get("open"))
        c = _float(row.get("close"))
        v = _float(row.get("volume")) or 0.0
        if o is None or c is None:
            continue
        if not opens:
            opens.append(o)
        closes.append(c)
        tv += v * c
        if _in_pm_window(ts):
            if pm_start is None:
                pm_start = o if o > 0 else c
            if c and c > 0:
                pm_end = c

    if not opens or not closes:
        return None
    open_px = opens[0]
    close_px = closes[-1]
    daily_ret = ((close_px - open_px) / open_px * 100.0) if open_px > 0 else None
    pm_ret = None
    if pm_start and pm_end and pm_start > 0:
        pm_ret = (pm_end - pm_start) / pm_start * 100.0

    return SymbolDayMetrics(
        symbol=sym,
        sector=sector,
        open_price=open_px,
        close_price=close_px,
        daily_return_pct=daily_ret,
        trading_value_jpy=tv,
        pm_start_price=pm_start,
        pm_end_price=pm_end,
        pm_return_pct_1400_1530=pm_ret,
    )


def aggregate_sector_metrics(
    symbol_metrics: Sequence[SymbolDayMetrics],
) -> dict[str, dict[str, Any]]:
    by_sector: dict[str, list[SymbolDayMetrics]] = {}
    for m in symbol_metrics:
        by_sector.setdefault(m.sector, []).append(m)

    out: dict[str, dict[str, Any]] = {}
    for sector, items in by_sector.items():
        rets = [m.daily_return_pct for m in items if m.daily_return_pct is not None]
        pm_rets = [m.pm_return_pct_1400_1530 for m in items if m.pm_return_pct_1400_1530 is not None]
        tv = sum(m.trading_value_jpy for m in items)
        out[sector] = {
            "sector_33_name": sector,
            "symbol_count": len(items),
            "daily_return_pct": round(statistics.median(rets), 4) if rets else None,
            "trading_value_jpy": round(tv, 2),
            "pm_return_pct_1400_1530": round(statistics.median(pm_rets), 4) if pm_rets else None,
        }
    return out


def compute_trading_value_increase(
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    days = sorted(sector_rows_by_day)
    for i, day in enumerate(days):
        prev_day = days[i - 1] if i > 0 else None
        prev_map = sector_rows_by_day.get(prev_day or "", {})
        for sector, row in sector_rows_by_day[day].items():
            cur_tv = _float(row.get("trading_value_jpy")) or 0.0
            prev_tv = _float((prev_map.get(sector) or {}).get("trading_value_jpy"))
            if prev_tv is None or prev_tv <= 0:
                row["trading_value_increase_pct"] = None
            else:
                row["trading_value_increase_pct"] = round((cur_tv - prev_tv) / prev_tv * 100.0, 4)


def compute_continuation_days(
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    days = sorted(sector_rows_by_day)
    streak: dict[str, int] = {}
    for day in days:
        for sector, row in sector_rows_by_day[day].items():
            ret = _float(row.get("daily_return_pct"))
            if ret is not None and ret > 0:
                streak[sector] = streak.get(sector, 0) + 1
            else:
                streak[sector] = 0
            row["continuation_days"] = streak[sector]


def compute_heat_scores(sector_rows: Mapping[str, Mapping[str, Any]]) -> None:
    sectors = sorted(sector_rows)
    if not sectors:
        return
    daily_rank = rank_normalize({s: _float(sector_rows[s].get("daily_return_pct")) for s in sectors})
    tv_rank = rank_normalize(
        {s: _float(sector_rows[s].get("trading_value_increase_pct")) for s in sectors}
    )
    pm_rank = rank_normalize(
        {s: _float(sector_rows[s].get("pm_return_pct_1400_1530")) for s in sectors}
    )
    cont_rank = rank_normalize({s: float(_int(sector_rows[s].get("continuation_days"))) for s in sectors})
    scores: dict[str, float] = {}
    for sector in sectors:
        scores[sector] = round(
            daily_rank[sector] + tv_rank[sector] + pm_rank[sector] + cont_rank[sector],
            6,
        )
        sector_rows[sector]["heat_score"] = scores[sector]

    ranked = sorted(sectors, key=lambda s: (-scores[s], s))
    for rank, sector in enumerate(ranked, start=1):
        sector_rows[sector]["heat_rank"] = rank


def build_tomorrow_top3_rows(
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    available_days: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in sorted(sector_rows_by_day):
        validation_day = _next_trading_day(day, available_days)
        if validation_day is None:
            continue
        day_rows = sector_rows_by_day[day]
        top = sorted(
            day_rows.values(),
            key=lambda r: (-(_float(r.get("heat_score")) or 0.0), str(r.get("sector_33_name"))),
        )[:TOP_SECTOR_COUNT]
        for rank, row in enumerate(top, start=1):
            rows.append(
                {
                    "signal_day": day,
                    "validation_day": validation_day,
                    "rank": rank,
                    "sector_33_name": row.get("sector_33_name"),
                    "heat_score": row.get("heat_score"),
                    "daily_return_pct": row.get("daily_return_pct"),
                    "trading_value_increase_pct": row.get("trading_value_increase_pct"),
                    "pm_return_pct_1400_1530": row.get("pm_return_pct_1400_1530"),
                    "continuation_days": row.get("continuation_days"),
                }
            )
    return rows


def resolve_trade_pnl_yen_100(row: Mapping[str, Any]) -> Optional[float]:
    direct = _float(row.get("pnl_yen_100"))
    if direct is not None:
        return direct
    ep = _float(row.get("entry_price"))
    xp = _float(row.get("exit_price")) or _float(row.get("close_price"))
    if ep is not None and xp is not None:
        return round((xp - ep) * 100.0, 2)
    pct = _float(row.get("realized_pnl_pct"))
    if pct is not None and ep is not None:
        return round(ep * pct / 100.0 * 100.0, 2)
    return None


def load_trades_by_day(repo_root: Path) -> dict[str, list[dict[str, Any]]]:
    import json as _json

    roots = [
        repo_root / "kabu_native" / "results" / "small_paper",
        repo_root / "kabu_native" / "results" / "paper_trade",
    ]
    by_day: dict[str, list[dict[str, Any]]] = {}
    seen_paths: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for csv_path in sorted(root.rglob("structural_trades.csv")):
            key = str(csv_path.resolve())
            if key in seen_paths:
                continue
            seen_paths.add(key)
            sess_dir = csv_path.parent
            day = sess_dir.parent.name
            if not (day.isdigit() and len(day) == 8):
                continue
            summary_path = sess_dir / "small_paper_summary.json"
            if summary_path.is_file():
                try:
                    summary = _json.loads(summary_path.read_text(encoding="utf-8"))
                except (OSError, _json.JSONDecodeError):
                    summary = {}
                if str(summary.get("source") or "") == "push-replay":
                    continue
            try:
                with csv_path.open(encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        sym = _norm_symbol(row.get("symbol") or "")
                        if not sym:
                            continue
                        trade = dict(row)
                        trade["symbol"] = sym
                        trade["day"] = day
                        trade["pnl_yen_100"] = resolve_trade_pnl_yen_100(trade)
                        by_day.setdefault(day, []).append(trade)
            except OSError:
                continue
    return by_day


def build_validation_rows(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    *,
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    sector_map: Mapping[str, str],
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in tomorrow_top3:
        signal_day = str(row.get("signal_day") or "")
        validation_day = str(row.get("validation_day") or "")
        sector = str(row.get("sector_33_name") or "")
        if not signal_day or not validation_day or not sector:
            continue
        grouped.setdefault((signal_day, validation_day), []).append(sector)

    out: list[dict[str, Any]] = []
    for (signal_day, validation_day), predicted_sectors in sorted(grouped.items()):
        predicted_set = set(predicted_sectors)
        all_trades = list(trades_by_day.get(validation_day) or [])
        predicted_trades = [
            t
            for t in all_trades
            if sector_map.get(_norm_symbol(str(t.get("symbol") or ""))) in predicted_set
        ]

        all_yens = [_float(t.get("pnl_yen_100")) or 0.0 for t in all_trades if t.get("pnl_yen_100") is not None]
        pred_yens = [_float(t.get("pnl_yen_100")) or 0.0 for t in predicted_trades if t.get("pnl_yen_100") is not None]

        next_day_rows = sector_rows_by_day.get(validation_day) or {}
        next_returns: list[float] = []
        continued_positive = 0
        for sector in predicted_sectors:
            ret = _float((next_day_rows.get(sector) or {}).get("daily_return_pct"))
            if ret is not None:
                next_returns.append(ret)
                if ret > 0:
                    continued_positive += 1

        out.append(
            {
                "signal_day": signal_day,
                "validation_day": validation_day,
                "predicted_sectors": "|".join(predicted_sectors),
                "predicted_sector_trade_count": len(predicted_trades),
                "predicted_sector_pnl_yen_100": round(sum(pred_yens), 2) if pred_yens else 0.0,
                "predicted_sector_profit_factor": _pf(pred_yens),
                "predicted_sector_win_rate": _win_rate(pred_yens),
                "predicted_sector_win_count": sum(1 for y in pred_yens if y > 0),
                "predicted_sector_loss_count": sum(1 for y in pred_yens if y < 0),
                "all_trade_count": len(all_trades),
                "all_pnl_yen_100": round(sum(all_yens), 2) if all_yens else 0.0,
                "all_profit_factor": _pf(all_yens),
                "all_win_rate": _win_rate(all_yens),
                "predicted_sector_next_day_return_pct_avg": round(
                    statistics.mean(next_returns), 4
                )
                if next_returns
                else None,
                "predicted_sector_next_day_continued_positive_count": continued_positive,
            }
        )
    return out


def summarize_validation(validation_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(validation_rows)
    if not rows:
        return {
            "validation_day_count": 0,
            "predicted_sector_trade_count_total": 0,
            "predicted_sector_pnl_yen_100_total": 0.0,
            "predicted_sector_profit_factor_aggregate": None,
            "predicted_sector_win_rate_aggregate": None,
        }

    pred_yens: list[float] = []
    for row in rows:
        n = _int(row.get("predicted_sector_trade_count"))
        pnl = _float(row.get("predicted_sector_pnl_yen_100")) or 0.0
        if n <= 0:
            continue
        avg = pnl / n
        pred_yens.extend([avg] * n)

    all_yens: list[float] = []
    for row in rows:
        n = _int(row.get("all_trade_count"))
        pnl = _float(row.get("all_pnl_yen_100")) or 0.0
        if n <= 0:
            continue
        avg = pnl / n
        all_yens.extend([avg] * n)

    continued = sum(_int(r.get("predicted_sector_next_day_continued_positive_count")) for r in rows)
    sector_slots = len(rows) * TOP_SECTOR_COUNT
    return {
        "validation_day_count": len(rows),
        "predicted_sector_trade_count_total": sum(_int(r.get("predicted_sector_trade_count")) for r in rows),
        "predicted_sector_pnl_yen_100_total": round(
            sum(_float(r.get("predicted_sector_pnl_yen_100")) or 0.0 for r in rows),
            2,
        ),
        "predicted_sector_profit_factor_aggregate": _pf(pred_yens),
        "predicted_sector_win_rate_aggregate": _win_rate(pred_yens),
        "all_trade_count_total": sum(_int(r.get("all_trade_count")) for r in rows),
        "all_pnl_yen_100_total": round(sum(_float(r.get("all_pnl_yen_100")) or 0.0 for r in rows), 2),
        "all_profit_factor_aggregate": _pf(all_yens),
        "all_win_rate_aggregate": _win_rate(all_yens),
        "next_day_sector_continuation_rate": round(continued / sector_slots, 4) if sector_slots else None,
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    summary = result.get("validation_summary") or {}
    constraints = result.get("constraints") or {}
    lines = [
        "# Phase246 Sector Heat Observation",
        "",
        "翌日継続するセクター地合いの予測力を観測（Universe/Entry 反映なし）。",
        "",
        "## Constraints",
        "",
    ]
    for key, val in constraints.items():
        lines.append(f"- `{key}`: {val}")
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            f"- intraday days: {result.get('intraday_day_count')}",
            f"- sector-day rows: {result.get('sector_day_row_count')}",
            f"- tomorrow top3 signals: {result.get('tomorrow_top3_row_count')}",
            f"- validation pairs: {summary.get('validation_day_count')}",
            "",
            "## Predicted-sector trade performance (aggregate)",
            "",
            f"- entry_count: {summary.get('predicted_sector_trade_count_total')}",
            f"- pnl_yen_100: {summary.get('predicted_sector_pnl_yen_100_total')}",
            f"- profit_factor: {summary.get('predicted_sector_profit_factor_aggregate')}",
            f"- win_rate: {summary.get('predicted_sector_win_rate_aggregate')}",
            "",
            "## Baseline (all trades on validation days)",
            "",
            f"- entry_count: {summary.get('all_trade_count_total')}",
            f"- pnl_yen_100: {summary.get('all_pnl_yen_100_total')}",
            f"- profit_factor: {summary.get('all_profit_factor_aggregate')}",
            f"- win_rate: {summary.get('all_win_rate_aggregate')}",
            "",
            "## Sector continuation (next-day return > 0)",
            "",
            f"- continuation_rate: {summary.get('next_day_sector_continuation_rate')}",
            "",
            "## Verdict",
            "",
            str((result.get("verdict") or {}).get("note") or ""),
            "",
        ]
    )
    return "\n".join(lines)


def flatten_sector_rows(sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in sorted(sector_rows_by_day):
        for sector, row in sorted(
            sector_rows_by_day[day].items(),
            key=lambda kv: (_float((kv[1] or {}).get("heat_rank")) or 9999.0, kv[0]),
        ):
            rows.append({"day": day, **row})
    return rows


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


@dataclass
class MarketSectorHeatObservation:
    repo_root: Path
    reports_dir: Path
    data_roots: Sequence[Path] = ()
    min_day: Optional[str] = None
    max_day: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.data_roots:
            self.data_roots = (
                self.repo_root / "data" / "intraday_1m",
                self.repo_root / "kabu_native" / "data" / "intraday_1m",
            )

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase246_sector_heat_summary.json",
            "by_sector": self.reports_dir / "phase246_sector_heat_by_sector.csv",
            "tomorrow_top3": self.reports_dir / "phase246_sector_heat_tomorrow_top3.csv",
            "validation_by_day": self.reports_dir / "phase246_sector_heat_validation_by_day.csv",
            "report": self.reports_dir / "phase246_sector_heat_report.md",
        }

    def run(self) -> dict[str, Any]:
        sector_map = read_jpx_sector_map(self.repo_root)
        available_days = discover_intraday_days(self.data_roots)
        if self.min_day:
            available_days = [d for d in available_days if d >= self.min_day]
        if self.max_day:
            available_days = [d for d in available_days if d <= self.max_day]

        sector_rows_by_day: dict[str, dict[str, dict[str, Any]]] = {}
        for day in available_days:
            day_dir = resolve_intraday_day_dir(day, self.data_roots)
            if day_dir is None:
                continue
            symbol_metrics: list[SymbolDayMetrics] = []
            for csv_path in sorted(day_dir.glob("*.csv")):
                sym = _norm_symbol(csv_path.stem)
                sector = sector_map.get(sym)
                if not sector:
                    continue
                metrics = load_symbol_day_metrics(csv_path, sector=sector)
                if metrics is not None:
                    symbol_metrics.append(metrics)
            if not symbol_metrics:
                continue
            sector_rows_by_day[day] = aggregate_sector_metrics(symbol_metrics)

        compute_trading_value_increase(sector_rows_by_day)
        compute_continuation_days(sector_rows_by_day)
        for day in sorted(sector_rows_by_day):
            compute_heat_scores(sector_rows_by_day[day])

        by_sector_rows = flatten_sector_rows(sector_rows_by_day)
        tomorrow_top3 = build_tomorrow_top3_rows(sector_rows_by_day, available_days=available_days)
        trades_by_day = load_trades_by_day(self.repo_root)
        validation_rows = build_validation_rows(
            tomorrow_top3,
            trades_by_day=trades_by_day,
            sector_map=sector_map,
            sector_rows_by_day=sector_rows_by_day,
        )
        validation_summary = summarize_validation(validation_rows)

        verdict_note = (
            "Observation only: sector heat top3 is not applied to Universe or Entry. "
            "Compare predicted-sector PF/PnL vs all-trade baseline to assess next-day predictive power."
        )
        if validation_summary.get("validation_day_count", 0) == 0:
            verdict_note += " No overlapping trade validation days yet."

        return {
            "phase": "246-SectorHeat-Observation",
            "title": "Market sector heat observation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "purpose": "Measure predictive power of sector heat continuing to the next day",
            "constraints": {
                "review_only": True,
                "production_changes_forbidden": True,
                "yaml_changes_forbidden": True,
                "runtime_reflected": False,
                "universe_change_forbidden": True,
                "entry_change_forbidden": True,
            },
            "inputs": {
                "data_roots": [str(p) for p in self.data_roots],
                "jpx_master": str(self.repo_root / "data" / "jpx" / "tradable_symbols.csv"),
                "min_day": self.min_day,
                "max_day": self.max_day,
            },
            "intraday_day_count": len(sector_rows_by_day),
            "sector_day_row_count": len(by_sector_rows),
            "tomorrow_top3_row_count": len(tomorrow_top3),
            "validation_summary": validation_summary,
            "verdict": {
                "observation_only": True,
                "note": verdict_note,
            },
            "_by_sector_rows": by_sector_rows,
            "_tomorrow_top3_rows": tomorrow_top3,
            "_validation_rows": validation_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        summary_payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        paths["summary"].write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["by_sector"], SECTOR_BY_DAY_FIELDS, result.get("_by_sector_rows") or [])
        _write_csv(paths["tomorrow_top3"], TOMORROW_TOP3_FIELDS, result.get("_tomorrow_top3_rows") or [])
        _write_csv(
            paths["validation_by_day"],
            VALIDATION_BY_DAY_FIELDS,
            result.get("_validation_rows") or [],
        )
        paths["report"].write_text(build_report_markdown(summary_payload), encoding="utf-8")
        return paths
