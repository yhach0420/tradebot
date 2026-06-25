"""
Phase500: Post-entry forward shadow logging (research only).

Observes 30/60/120/180s checkpoints after ENTRY. No Runtime / Entry / Exit / Order / YAML impact.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

HORIZONS: tuple[int, ...] = (30, 60, 120, 180)

POST_ENTRY_CSV_FIELDS: tuple[str, ...] = (
    "date",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "mfe_60s",
    "mfe_120s",
    "reclaim_120s",
    "high_update_count_180s",
    "early_failure_shadow_score",
    "exit_reason",
    "flag_e2_no_progress",
    "flag_e3_stall",
    "flag_e4_no_reclaim",
)

SUMMARY_FIELD_KEYS: tuple[str, ...] = (
    "post_entry_shadow_score_ge3_count",
    "post_entry_shadow_score_ge3_pnl",
    "post_entry_shadow_score_ge4_count",
    "post_entry_shadow_score_ge4_pnl",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 6)


def _tick_ts(tick: Mapping[str, Any]) -> float:
    ts = _float(tick.get("ts_epoch"))
    if ts is not None and ts > 0:
        return ts
    raw = str(tick.get("ts") or "").strip()
    if not raw:
        return 0.0
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return 0.0


def _tick_price(tick: Mapping[str, Any], *, entry_price: float) -> float:
    px = _float(tick.get("price"))
    if px is not None and px > 0:
        return px
    pnl = _float(tick.get("pnl_pct"))
    if pnl is not None and entry_price > 0:
        return entry_price * (1.0 + pnl / 100.0)
    return entry_price


def _price_at_horizon(
    rich_ticks: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    horizon_sec: float,
) -> Optional[float]:
    target = entry_ts + horizon_sec
    px_at: Optional[float] = None
    for tick in rich_ticks:
        ts = _tick_ts(tick)
        if ts < entry_ts:
            continue
        if ts > target:
            break
        px_at = _tick_price(tick, entry_price=entry_price)
    return px_at


def compute_metrics_until(
    rich_ticks: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    until_ts: float,
) -> dict[str, Any]:
    """Metrics from entry through ``until_ts`` (Phase499-aligned)."""
    mfe = 0.0
    mae = 0.0
    peak_px = entry_price
    trough_px = entry_price
    below = False
    reclaimed = False
    high_updates = 0
    session_high: Optional[float] = None
    px_at_t: Optional[float] = None

    for tick in rich_ticks:
        ts = _tick_ts(tick)
        if ts < entry_ts:
            continue
        if ts > until_ts:
            break
        px = _tick_price(tick, entry_price=entry_price)
        px_at_t = px
        pnl = _pnl_pct(entry_price, px)
        mfe = max(mfe, pnl)
        mae = min(mae, pnl)
        if px > peak_px:
            peak_px = px
        if px < trough_px:
            trough_px = px
        if px < entry_price:
            below = True
        if below and px >= entry_price:
            reclaimed = True
        if session_high is None:
            session_high = px
        elif px > session_high:
            high_updates += 1
            session_high = px

    if px_at_t is None:
        return {}

    return {
        "pnl_pct": _pnl_pct(entry_price, px_at_t),
        "mfe_pct": round(mfe, 6),
        "mae_pct": round(mae, 6),
        "high_update_count": high_updates,
        "reclaim_entry_price": reclaimed,
        "failed_reclaim": bool(below and not reclaimed),
    }


def compute_post_entry_checkpoints(
    rich_ticks: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
) -> dict[str, Any]:
    """All checkpoint fields at 30/60/120/180s."""
    out: dict[str, Any] = {}
    by_h: dict[int, dict[str, Any]] = {}
    for h in HORIZONS:
        m = compute_metrics_until(
            rich_ticks,
            entry_price=entry_price,
            entry_ts=entry_ts,
            until_ts=entry_ts + h,
        )
        by_h[h] = m
        if not m:
            continue
        out[f"pnl_pct_{h}s"] = m.get("pnl_pct")
        out[f"mfe_pct_{h}s"] = m.get("mfe_pct")
        out[f"mae_pct_{h}s"] = m.get("mae_pct")
        out[f"high_update_count_{h}s"] = m.get("high_update_count")
        out[f"reclaim_{h}s"] = bool(m.get("reclaim_entry_price"))

    m60 = by_h.get(60, {})
    m120 = by_h.get(120, {})
    m180 = by_h.get(180, {})

    flag_e2 = bool(m60.get("mfe_pct") is not None and float(m60["mfe_pct"]) < 0.1)
    flag_e3 = bool(
        m120.get("mfe_pct") is not None
        and m120.get("pnl_pct") is not None
        and float(m120["mfe_pct"]) < 0.2
        and float(m120["pnl_pct"]) < 0
    )
    flag_e4 = bool(m120.get("failed_reclaim"))
    high_180 = int(m180.get("high_update_count") or 0)

    out["flag_e2_no_progress"] = flag_e2
    out["flag_e3_stall"] = flag_e3
    out["flag_e4_no_reclaim"] = flag_e4
    out["early_failure_shadow_score"] = compute_early_failure_shadow_score(
        flag_e2=flag_e2,
        flag_e3=flag_e3,
        flag_e4=flag_e4,
        high_update_count_180s=high_180,
    )
    return out


def compute_early_failure_shadow_score(
    *,
    flag_e2: bool,
    flag_e3: bool,
    flag_e4: bool,
    high_update_count_180s: int,
) -> int:
    score = 0
    if flag_e2:
        score += 1
    if flag_e3:
        score += 1
    if flag_e4:
        score += 1
    if high_update_count_180s == 0:
        score += 1
    return score


def _resolve_pnl_yen_100(row: Mapping[str, Any]) -> float:
    direct = _float(row.get("pnl_yen_100"))
    if direct is not None:
        return round(direct, 2)
    entry = _float(row.get("entry_price")) or 0.0
    pnl_pct = _float(row.get("pnl_pct")) or 0.0
    if entry > 0:
        return round(entry * 100.0 * pnl_pct / 100.0, 2)
    return 0.0


def _day_from_row(row: Mapping[str, Any]) -> str:
    for key in ("date", "day"):
        raw = str(row.get(key) or "").strip()
        if len(raw) >= 8:
            return raw.replace("-", "")[:8]
    entry = str(row.get("entry_time") or "")
    if len(entry) >= 10:
        return entry[:10].replace("-", "")
    return ""


def build_post_entry_csv_row(
    exit_row: Mapping[str, Any],
    *,
    checkpoints: Mapping[str, Any],
) -> dict[str, Any]:
    """One trade → one CSV row for ``small_paper_shadow_post_entry.csv``."""
    m120_reclaim = bool(checkpoints.get("reclaim_120s"))
    return {
        "date": _day_from_row(exit_row),
        "symbol": str(exit_row.get("symbol") or ""),
        "entry_time": str(exit_row.get("entry_time") or ""),
        "exit_time": str(exit_row.get("exit_time") or ""),
        "pnl_yen_100": _resolve_pnl_yen_100(exit_row),
        "mfe_60s": checkpoints.get("mfe_pct_60s"),
        "mfe_120s": checkpoints.get("mfe_pct_120s"),
        "reclaim_120s": m120_reclaim,
        "high_update_count_180s": checkpoints.get("high_update_count_180s"),
        "early_failure_shadow_score": int(checkpoints.get("early_failure_shadow_score") or 0),
        "exit_reason": str(exit_row.get("exit_reason") or ""),
        "flag_e2_no_progress": bool(checkpoints.get("flag_e2_no_progress")),
        "flag_e3_stall": bool(checkpoints.get("flag_e3_stall")),
        "flag_e4_no_reclaim": bool(checkpoints.get("flag_e4_no_reclaim")),
    }


def enrich_exit_post_entry_shadow_fields(
    *,
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    entry_ts: float,
) -> dict[str, Any]:
    """Compute checkpoint fields at observer exit (logging only)."""
    if not rich_ticks or entry_price <= 0:
        return {}
    checkpoints = compute_post_entry_checkpoints(
        rich_ticks,
        entry_price=entry_price,
        entry_ts=entry_ts,
    )
    if not checkpoints:
        return {}
    return {**checkpoints}


@dataclass
class PostEntryForwardShadowSession:
    """Session accumulator for post-entry forward shadow."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def record_exit(self, exit_row: Mapping[str, Any]) -> None:
        checkpoints = {
            k: exit_row.get(k)
            for k in (
                "pnl_pct_30s",
                "pnl_pct_60s",
                "pnl_pct_120s",
                "mfe_pct_30s",
                "mfe_pct_60s",
                "mfe_pct_120s",
                "mae_pct_60s",
                "mae_pct_120s",
                "reclaim_120s",
                "high_update_count_180s",
                "flag_e2_no_progress",
                "flag_e3_stall",
                "flag_e4_no_reclaim",
                "early_failure_shadow_score",
            )
            if exit_row.get(k) is not None
        }
        if "early_failure_shadow_score" not in checkpoints:
            rich = exit_row.get("_rich_ticks")
            entry_px = _float(exit_row.get("entry_price")) or 0.0
            entry_ts = _float(exit_row.get("entry_ts"))
            if isinstance(rich, Sequence) and entry_px > 0 and entry_ts:
                checkpoints = compute_post_entry_checkpoints(
                    rich,
                    entry_price=entry_px,
                    entry_ts=entry_ts,
                )
        if not checkpoints:
            return
        self.rows.append(build_post_entry_csv_row(exit_row, checkpoints=checkpoints))

    def score_slice(self, min_score: int) -> list[dict[str, Any]]:
        return [
            r
            for r in self.rows
            if int(r.get("early_failure_shadow_score") or 0) >= min_score
        ]

    def summary_fields(self) -> dict[str, Any]:
        ge3 = self.score_slice(3)
        ge4 = self.score_slice(4)
        return {
            "post_entry_shadow_score_ge3_count": len(ge3),
            "post_entry_shadow_score_ge3_pnl": round(
                sum(_float(r.get("pnl_yen_100")) or 0.0 for r in ge3), 2
            ),
            "post_entry_shadow_score_ge4_count": len(ge4),
            "post_entry_shadow_score_ge4_pnl": round(
                sum(_float(r.get("pnl_yen_100")) or 0.0 for r in ge4), 2
            ),
        }

    def write_session_csv(self, output_dir: Path) -> Optional[Path]:
        if not self.rows:
            return None
        path = output_dir / "small_paper_shadow_post_entry.csv"
        _write_csv(path, POST_ENTRY_CSV_FIELDS, self.rows)
        return path


def finalize_session_post_entry_shadow(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive session summary from exit events when session object unavailable."""
    session = PostEntryForwardShadowSession()
    for ev in events:
        if str(ev.get("event_type") or "") != "observer_exit":
            continue
        session.record_exit(ev)
    return session.summary_fields()


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
