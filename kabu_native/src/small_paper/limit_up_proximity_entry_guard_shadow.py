"""
Phase351: Limit-up proximity ENTRY guard production shadow (no hard reject).

Blocks shadow counterfactual when:
  - distance_to_limit_up_pct <= 0.5%
  - OR day_high_near_limit = true
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from universe.am_pm_universe import estimate_daily_limit_prices, limit_status_from_prices

GUARD_VARIANT = "A_limit_up_proximity_guard"
NEAR_LIMIT_PCT = 0.5

ENTRY_FIELD_KEYS = (
    "limit_up_proximity_guard_shadow_blocked",
    "limit_up_proximity_guard_shadow_reason",
    "distance_to_limit_up_pct",
    "day_high_near_limit",
    "daily_limit_up_price",
    "limit_up_proximity_prev_close_used",
)

EXIT_FIELD_KEYS = ENTRY_FIELD_KEYS + (
    "limit_up_proximity_shadow_pnl_yen_100",
    "limit_up_proximity_shadow_delta_yen",
)

SUMMARY_FIELD_KEYS = (
    "limit_up_proximity_guard_shadow_enabled",
    "limit_up_proximity_guard_shadow_blocked_count",
    "limit_up_proximity_guard_shadow_kept_count",
    "limit_up_proximity_guard_shadow_actual_total_pnl_yen_100",
    "limit_up_proximity_guard_shadow_total_pnl_yen_100",
    "limit_up_proximity_guard_shadow_delta_yen",
    "limit_up_proximity_guard_shadow_skipped_trade_pnl_actual",
    "limit_up_proximity_guard_shadow_stop_hit_reduction_count",
    "limit_up_proximity_guard_shadow_improved_vs_actual",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _pf(yens: list[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _day_high_near_limit(
    *,
    limit_up: Optional[float],
    entry_px: float,
    entry_near_day_high_pct: Optional[float],
    board_high: Optional[float],
) -> bool:
    if limit_up and limit_up > 0:
        if board_high and board_high > 0:
            return (limit_up - board_high) / limit_up * 100.0 <= NEAR_LIMIT_PCT
        if entry_near_day_high_pct is not None and entry_near_day_high_pct < 100 and entry_px > 0:
            implied_day_high = entry_px / (1.0 - entry_near_day_high_pct / 100.0)
            return (limit_up - implied_day_high) / limit_up * 100.0 <= NEAR_LIMIT_PCT
    return False


def compute_limit_up_proximity_guard_fields(
    *,
    entry_px: float,
    prev_close: Optional[float] = None,
    entry_near_day_high_pct: Optional[float] = None,
    board_high: Optional[float] = None,
) -> dict[str, Any]:
    """Shadow-only limit-up proximity guard at ENTRY (does not block actual entry)."""
    lim_up, lim_down, _ = estimate_daily_limit_prices(prev_close)
    lim = limit_status_from_prices(
        current=entry_px if entry_px > 0 else None,
        limit_up=lim_up,
        limit_down=lim_down,
        bid_qty=None,
        ask_qty=None,
    )
    dist_up = _float(lim.get("distance_to_limit_up_pct"))
    day_high_near = _day_high_near_limit(
        limit_up=lim_up,
        entry_px=entry_px,
        entry_near_day_high_pct=entry_near_day_high_pct,
        board_high=board_high,
    )
    blocked = False
    reasons: list[str] = []
    if dist_up is not None and dist_up <= NEAR_LIMIT_PCT:
        blocked = True
        reasons.append("distance_to_limit_up")
    if day_high_near:
        blocked = True
        reasons.append("day_high_near_limit")
    return {
        "limit_up_proximity_guard_shadow_blocked": blocked,
        "limit_up_proximity_guard_shadow_reason": "|".join(reasons),
        "distance_to_limit_up_pct": dist_up,
        "day_high_near_limit": day_high_near,
        "daily_limit_up_price": lim_up,
        "limit_up_proximity_prev_close_used": prev_close is not None and prev_close > 0,
    }


def would_block_limit_up_proximity_guard(fields: Mapping[str, Any]) -> bool:
    dist = _float(fields.get("distance_to_limit_up_pct"))
    if dist is not None and dist <= NEAR_LIMIT_PCT:
        return True
    return _bool(fields.get("day_high_near_limit"))


def enrich_exit_limit_up_proximity_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
) -> dict[str, Any]:
    """Counterfactual shadow PnL if blocked ENTRY had not occurred (logging only)."""
    from replay.pnl_yen import compute_pnl_yen_100

    blocked = would_block_limit_up_proximity_guard(entry_shadow)
    actual_yen = round(compute_pnl_yen_100(entry_price, exit_price), 2)
    shadow_yen = 0.0 if blocked else actual_yen
    return {
        "limit_up_proximity_guard_shadow_blocked": blocked,
        "limit_up_proximity_guard_shadow_reason": entry_shadow.get(
            "limit_up_proximity_guard_shadow_reason", ""
        ),
        "distance_to_limit_up_pct": entry_shadow.get("distance_to_limit_up_pct"),
        "day_high_near_limit": entry_shadow.get("day_high_near_limit"),
        "daily_limit_up_price": entry_shadow.get("daily_limit_up_price"),
        "limit_up_proximity_prev_close_used": entry_shadow.get(
            "limit_up_proximity_prev_close_used"
        ),
        "limit_up_proximity_shadow_pnl_yen_100": shadow_yen,
        "limit_up_proximity_shadow_delta_yen": round(shadow_yen - actual_yen, 2),
        "stop_hit": exit_reason == "stop_hit",
    }


@dataclass
class LimitUpProximityEntryGuardShadowCounters:
    limit_up_proximity_guard_shadow_blocked_count: int = 0
    limit_up_proximity_guard_shadow_kept_count: int = 0
    actual_total_pnl_yen_100: float = 0.0
    shadow_total_pnl_yen_100: float = 0.0
    skipped_trade_pnl_actual: float = 0.0
    stop_hit_count_actual: int = 0
    stop_hit_count_shadow: int = 0

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        if would_block_limit_up_proximity_guard(fields):
            self.limit_up_proximity_guard_shadow_blocked_count += 1
        else:
            self.limit_up_proximity_guard_shadow_kept_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        delta = _float(row.get("limit_up_proximity_shadow_delta_yen")) or 0.0
        shadow_yen = _float(row.get("limit_up_proximity_shadow_pnl_yen_100")) or 0.0
        actual_yen = round(shadow_yen - delta, 2)
        blocked = would_block_limit_up_proximity_guard(row)
        self.actual_total_pnl_yen_100 = round(self.actual_total_pnl_yen_100 + actual_yen, 2)
        self.shadow_total_pnl_yen_100 = round(self.shadow_total_pnl_yen_100 + shadow_yen, 2)
        if blocked:
            self.skipped_trade_pnl_actual = round(self.skipped_trade_pnl_actual + actual_yen, 2)
        if _bool(row.get("stop_hit")):
            self.stop_hit_count_actual += 1
            if not blocked:
                self.stop_hit_count_shadow += 1

    def summary_fields(self) -> dict[str, Any]:
        delta = round(self.shadow_total_pnl_yen_100 - self.actual_total_pnl_yen_100, 2)
        return {
            "limit_up_proximity_guard_shadow_enabled": True,
            "limit_up_proximity_guard_shadow_blocked_count": (
                self.limit_up_proximity_guard_shadow_blocked_count
            ),
            "limit_up_proximity_guard_shadow_kept_count": (
                self.limit_up_proximity_guard_shadow_kept_count
            ),
            "limit_up_proximity_guard_shadow_actual_total_pnl_yen_100": (
                self.actual_total_pnl_yen_100
            ),
            "limit_up_proximity_guard_shadow_total_pnl_yen_100": self.shadow_total_pnl_yen_100,
            "limit_up_proximity_guard_shadow_delta_yen": delta,
            "limit_up_proximity_guard_shadow_skipped_trade_pnl_actual": (
                self.skipped_trade_pnl_actual
            ),
            "limit_up_proximity_guard_shadow_stop_hit_reduction_count": (
                self.stop_hit_count_actual - self.stop_hit_count_shadow
            ),
            "limit_up_proximity_guard_shadow_improved_vs_actual": delta > 0,
        }


def _entry_key(sym: str, ent: str) -> str:
    return f"{sym}|{ent}"


def _load_universe(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {str(r.get("symbol") or ""): r for r in csv.DictReader(f)}


def _universe_path_for_session(
    day: str, session_kind: str, summary: dict[str, Any], reports_dir: Path
) -> Path:
    p = summary.get("intraday_refresh_csv")
    if p:
        path = Path(str(p))
        if path.is_file():
            return path
    if session_kind == "am":
        return reports_dir / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day}.csv"
    return reports_dir / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day}.csv"


def _stream_events_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            yield row


def enrich_trade_features_for_review(
    acc: dict[str, str],
    ex: dict[str, str],
    universe: dict[str, dict[str, str]],
) -> dict[str, Any]:
    sym = str(ex.get("symbol") or "")
    u = universe.get(sym, {})
    prev_close = _float(u.get("close_price"))
    entry_px = _float(ex.get("entry_price")) or _float(acc.get("current_price"))
    near_high = _float(acc.get("entry_near_day_high_pct") or ex.get("entry_near_day_high_pct"))

    guard_fields = compute_limit_up_proximity_guard_fields(
        entry_px=entry_px or 0.0,
        prev_close=prev_close,
        entry_near_day_high_pct=near_high,
    )

    ep, xp = _float(ex.get("entry_price")), _float(ex.get("exit_price"))
    yen = round((xp - ep) * 100.0, 2) if ep is not None and xp is not None else None
    reason = str(ex.get("structural_exit_reason") or ex.get("exit_reason") or "")
    blocked = would_block_limit_up_proximity_guard(guard_fields)
    shadow_yen = 0.0 if blocked else (yen or 0.0)

    return {
        "trade_key": _entry_key(sym, str(ex.get("entry_time") or "")),
        "symbol": sym,
        "entry_time": ex.get("entry_time"),
        "exit_time": ex.get("exit_time"),
        "entry_price": entry_px,
        "exit_price": xp,
        "pnl_yen_100": yen,
        "is_stop_hit": reason == "stop_hit",
        "exit_reason": reason,
        "universe_slot": u.get("universe_slot", ""),
        "source_bucket": u.get("source_bucket", ""),
        "limit_up_proximity_guard_shadow_blocked": blocked,
        "limit_up_proximity_guard_shadow_reason": guard_fields.get(
            "limit_up_proximity_guard_shadow_reason", ""
        ),
        "distance_to_limit_up_pct": guard_fields.get("distance_to_limit_up_pct"),
        "day_high_near_limit": guard_fields.get("day_high_near_limit"),
        "limit_up_proximity_shadow_pnl_yen_100": shadow_yen,
        "limit_up_proximity_shadow_delta_yen": round((shadow_yen - (yen or 0.0)), 2)
        if yen is not None
        else None,
    }


def _load_session_summary(sess_dir: Path) -> dict[str, Any]:
    summ_path = sess_dir / "small_paper_summary.json"
    if not summ_path.is_file():
        return {}
    return json.loads(summ_path.read_text(encoding="utf-8"))


def _infer_session_kind(
    sess_dir: Path,
    summary: Mapping[str, Any],
    *,
    events_csv: Optional[Path] = None,
) -> str:
    start = str(summary.get("session_start") or "")
    if start:
        return "am" if start < "12:00" else "pm"
    ev_path = events_csv or (sess_dir / "small_paper_events.csv")
    if ev_path.is_file():
        with ev_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                et = str(row.get("event_type") or "")
                if et not in ("accepted", "observer_exit"):
                    continue
                ts = str(row.get("entry_time") or row.get("event_time") or "")
                if len(ts) >= 16:
                    hhmm = ts[11:16]
                    return "am" if hhmm < "12:00" else "pm"
                break
    name = sess_dir.name.lower()
    if "pm" in name or "1430" in name:
        return "pm"
    return "am"


def _session_source_label(sess_dir: Path) -> str:
    name = sess_dir.name.lower()
    if name.startswith("live_session"):
        return "live"
    if name.startswith("push_replay"):
        return "push_replay"
    if name.startswith("push_"):
        return "push_replay"
    for prefix in ("phase", "replay"):
        if name.startswith(prefix):
            return name.split("_")[0] if "_" in name else prefix
    return "other"


def evaluate_session(session_meta: Mapping[str, Any], *, reports_dir: Path) -> dict[str, Any]:
    sess_dir = Path(str(session_meta["session_dir"]))
    summary = _load_session_summary(sess_dir)
    session_kind = str(session_meta.get("session_kind") or _infer_session_kind(sess_dir, summary))
    universe = _load_universe(
        _universe_path_for_session(
            str(session_meta["day"]),
            session_kind,
            summary,
            reports_dir,
        )
    )
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") != "observer_exit" or row.get("pnl_pct") in (None, ""):
            continue
        key = (row.get("symbol", ""), row.get("entry_time", ""))
        acc = accepted.get(key, {})
        t = enrich_trade_features_for_review(acc, row, universe)
        t["session_id"] = session_meta["session_id"]
        t["day"] = session_meta["day"]
        t["session_kind"] = session_kind
        trades.append(t)

    actual_yens = [float(t["pnl_yen_100"]) for t in trades if t.get("pnl_yen_100") is not None]
    shadow_yens = [
        float(t["limit_up_proximity_shadow_pnl_yen_100"])
        for t in trades
        if t.get("limit_up_proximity_shadow_pnl_yen_100") is not None
    ]
    skipped = [t for t in trades if t.get("limit_up_proximity_guard_shadow_blocked")]
    skipped_pnl = [
        float(t["pnl_yen_100"]) for t in skipped if t.get("pnl_yen_100") is not None
    ]
    stops_actual = sum(1 for t in trades if t.get("is_stop_hit"))
    stops_shadow = sum(1 for t in trades if t.get("is_stop_hit") and not t.get("limit_up_proximity_guard_shadow_blocked"))
    dyn_yens = [
        float(t["limit_up_proximity_shadow_pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "dynamic" and t.get("limit_up_proximity_shadow_pnl_yen_100") is not None
    ]
    core_yens = [
        float(t["limit_up_proximity_shadow_pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "core" and t.get("limit_up_proximity_shadow_pnl_yen_100") is not None
    ]
    dyn_actual_yens = [
        float(t["pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "dynamic" and t.get("pnl_yen_100") is not None
    ]
    core_actual_yens = [
        float(t["pnl_yen_100"])
        for t in trades
        if t.get("universe_slot") == "core" and t.get("pnl_yen_100") is not None
    ]
    actual_total = round(sum(actual_yens), 2) if actual_yens else 0.0
    shadow_total = round(sum(shadow_yens), 2) if shadow_yens else 0.0
    delta = round(shadow_total - actual_total, 2)

    return {
        "session_meta": dict(session_meta),
        "actual_total_pnl_yen_100": actual_total,
        "shadow_total_pnl_yen_100": shadow_total,
        "delta_yen": delta,
        "profit_factor_yen_100": _pf(shadow_yens),
        "actual_profit_factor_yen_100": _pf(actual_yens),
        "trade_count_actual": len(trades),
        "trade_count_shadow": len(trades) - len(skipped),
        "skipped_trade_count": len(skipped),
        "skipped_trade_pnl_actual": round(sum(skipped_pnl), 2) if skipped_pnl else 0.0,
        "stop_hit_count_actual": stops_actual,
        "stop_hit_count_shadow": stops_shadow,
        "stop_hit_reduction_count": stops_actual - stops_shadow,
        "improved_vs_actual": delta > 0,
        "dynamic40_actual_pnl_yen_100": round(sum(dyn_actual_yens), 2) if dyn_actual_yens else 0.0,
        "dynamic40_shadow_pnl_yen_100": round(sum(dyn_yens), 2) if dyn_yens else 0.0,
        "dynamic40_delta_yen": round(
            (sum(dyn_yens) if dyn_yens else 0.0) - (sum(dyn_actual_yens) if dyn_actual_yens else 0.0),
            2,
        ),
        "dynamic40_trade_count_shadow": sum(
            1 for t in trades if t.get("universe_slot") == "dynamic" and not t.get("limit_up_proximity_guard_shadow_blocked")
        ),
        "core10_actual_pnl_yen_100": round(sum(core_actual_yens), 2) if core_actual_yens else 0.0,
        "core10_shadow_pnl_yen_100": round(sum(core_yens), 2) if core_yens else 0.0,
        "core10_delta_yen": round(
            (sum(core_yens) if core_yens else 0.0) - (sum(core_actual_yens) if core_actual_yens else 0.0),
            2,
        ),
        "core10_trade_count_shadow": sum(
            1 for t in trades if t.get("universe_slot") == "core" and not t.get("limit_up_proximity_guard_shadow_blocked")
        ),
        "session_source": str(session_meta.get("session_source") or _session_source_label(sess_dir)),
        "affected_symbols": sorted({str(t["symbol"]) for t in skipped}),
        "trades": trades,
    }
