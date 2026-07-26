"""Cost-Aware V2 dataset loader (causal ENTRY features only)."""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
COST_BPS = 0.05
EARLY_STOP_SEC = 300.0
BIG_WIN_YEN = 5000.0
BIG_LOSS_YEN = -5000.0

FEATURE_KEYS_RUNTIME = [
    ("f_rise5", "entry_rise_5min_pct"),
    ("f_rise10", "entry_rise_10min_pct"),
    ("f_r30", "r30_sec"),
    ("f_r60", "r60_sec"),
    ("f_r120", "r120_sec"),
    ("f_mom", "entry_momentum_continuation_score"),
    ("f_mom_alt", "momentum_continuation_score"),
    ("f_near_high", "entry_near_day_high_pct"),
    ("f_vwap", "entry_vwap_dev_pct"),
    ("f_pbv2", "entry_expectancy_score_v2"),
    ("f_spread", "spread_bps"),
    ("f_bounce", "microseq_bounce_from_recent_low"),
    ("f_fall", "microseq_fall_from_recent_high"),
    ("f_slope5", "microseq_slope_5min"),
    ("f_imb", "entry_order_book_imbalance"),
    ("f_imb_pct", "entry_imbalance_percentile"),
    ("f_board_age", "board_age_sec"),
    ("f_tv", "trading_value"),
    ("f_atr", "atr_pct"),
    ("f_tick_ratio", "tick_ratio_pct"),
    ("f_rsi", "rsi14"),
    ("f_rolling_mfe", "rolling_mfe_pct"),
    ("f_rolling_mae", "rolling_mae_pct"),
    ("f_pure_mom", "pure_price_momentum"),
    ("f_rise15", "entry_rise_15min_pct"),
    ("f_rise30", "entry_rise_30min_pct"),
    ("f_r120", "r120_sec"),
    ("f_minutes_since_day_high", "minutes_since_day_high_update"),
    ("f_day_high_from_open", "day_high_minutes_from_open"),
    ("f_price_age", "price_age_sec"),
    ("f_entry_mom_score", "entry_momentum_score"),
]

NP_FEATURE_KEYS: list[tuple[str, str]] = []
for w in (10, 30, 60, 120, 300):
    for stem in (
        "np_ret",
        "np_accel",
        "np_slope",
        "np_imb_chg",
        "np_imb_persist",
        "np_bid_chg",
        "np_ask_chg",
        "np_tv_chg_pct",
        "np_vol_price_sync",
        "np_ticks",
    ):
        NP_FEATURE_KEYS.append((f"f_{stem}_{w}", f"{stem}_{w}s"))


def _f(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None or v == "":
        return default
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except (TypeError, ValueError):
        return default


def _b(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "y")


def cost_yen(entry: float) -> float:
    return round(float(entry) * 100.0 * (COST_BPS / 100.0), 2)


def yen100(entry: float, exit_px: float) -> float:
    return round((exit_px - entry) * 100.0, 2)


@dataclass
class TradeRow:
    day: str
    session: str
    symbol: str
    position_id: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_yen: float
    pnl_5bps: float
    hold_sec: float
    features: dict[str, Optional[float]] = field(default_factory=dict)
    has_board_hist: bool = False
    has_np: bool = False
    causal_ok: bool = True

    @property
    def is_winner(self) -> bool:
        return self.pnl_yen > 0

    @property
    def is_stop(self) -> bool:
        return self.exit_reason == "stop_hit"

    @property
    def is_np(self) -> bool:
        return self.exit_reason == "no_progress_exit"

    @property
    def is_big_win(self) -> bool:
        return self.pnl_yen >= BIG_WIN_YEN

    @property
    def is_big_loss(self) -> bool:
        return self.pnl_yen <= BIG_LOSS_YEN

    @property
    def is_early_stop(self) -> bool:
        return self.is_stop and self.hold_sec <= EARLY_STOP_SEC


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=JST)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=JST)
    except Exception:
        return None


def _iter_events(session_dir: Path) -> Iterable[dict[str, Any]]:
    jl = session_dir / "small_paper_events.jsonl"
    csv_p = session_dir / "small_paper_events.csv"
    if jl.is_file():
        with jl.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
        return
    if csv_p.is_file():
        with csv_p.open(encoding="utf-8", newline="") as fh:
            yield from (dict(r) for r in csv.DictReader(fh))


def _load_np_map(session_dir: Path) -> dict[str, dict[str, Any]]:
    path = session_dir / "np_pre_entry_features.jsonl"
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            pid = str(row.get("position_id") or "")
            if pid:
                out[pid] = row
            else:
                out[f"{row.get('symbol')}|{row.get('entry_time') or row.get('accepted_at')}"] = row
    return out


def _session_kind(session_dir: Path, summary: Mapping[str, Any]) -> str:
    am_pm = summary.get("am_pm_session") if isinstance(summary.get("am_pm_session"), Mapping) else {}
    k = str(am_pm.get("kind") or "").upper()
    if k in ("AM", "PM"):
        return k
    name = session_dir.name
    if any(x in name for x in ("080", "081", "082", "083", "075", "073")):
        return "AM"
    return "PM"


def discover_days(native: Path = NATIVE) -> list[str]:
    root = native / "results" / "small_paper"
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and p.name.isdigit() and not p.name.startswith("2099")
    )


def _pid_from_event(e: Mapping[str, Any]) -> str:
    pid = str(e.get("position_id") or "").strip()
    if pid:
        return pid
    sym = str(e.get("symbol") or "").strip()
    et = str(e.get("entry_time") or e.get("event_time") or "").strip()
    if sym and et:
        return f"{sym}|{et}"
    return ""


def load_day_trades(day: str, *, native: Path = NATIVE) -> tuple[list[TradeRow], dict[str, Any]]:
    root = native / "results" / "small_paper" / day
    meta: dict[str, Any] = {
        "day": day,
        "usable": False,
        "exclude_reason": "",
        "sessions": [],
        "n_accepted": 0,
        "n_joined": 0,
        "n_with_np": 0,
        "n_with_market_capture_l2": 0,
        "has_market_capture": (native / "data" / "market_capture" / day).is_dir(),
    }
    if not root.is_dir():
        meta["exclude_reason"] = "no_small_paper_dir"
        return [], meta

    trades: list[TradeRow] = []
    for session_dir in sorted(root.glob("live_session_*")):
        if "demo" in str(session_dir):
            continue
        summary_path = session_dir / "small_paper_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        sk = _session_kind(session_dir, summary)
        accepts: dict[str, dict] = {}
        exits: dict[str, dict] = {}
        for e in _iter_events(session_dir):
            et = e.get("event_type")
            pid = _pid_from_event(e)
            if not pid:
                continue
            if et == "accepted":
                accepts[pid] = e
            elif et == "observer_exit":
                exits[pid] = e
        np_map = _load_np_map(session_dir)
        meta["sessions"].append(
            {
                "session": session_dir.name,
                "kind": sk,
                "accepted": len(accepts),
                "exits": len(exits),
                "np_rows": len(np_map),
            }
        )
        meta["n_accepted"] += len(accepts)
        for pid, acc in accepts.items():
            ex = exits.get(pid)
            if ex is None:
                # Fallback: match exit by symbol + entry_time when keys diverge slightly
                sym = str(acc.get("symbol") or "")
                et0 = str(acc.get("entry_time") or "")
                for ep, ev in exits.items():
                    if str(ev.get("symbol") or "") == sym and str(ev.get("entry_time") or "") == et0:
                        ex = ev
                        break
            if ex is None:
                continue
            ep = _f(acc.get("entry_price") or acc.get("current_price") or ex.get("entry_price"))
            xp = _f(ex.get("exit_price") or ex.get("current_price"))
            if ep is None or ep <= 0 or xp is None or xp <= 0:
                continue
            pnl = _f(ex.get("actual_pnl_yen_100") or ex.get("pnl_yen_100"))
            if pnl is None:
                pnl_pct = _f(ex.get("pnl_pct"))
                if pnl_pct is not None:
                    pnl = round(float(ep) * (pnl_pct / 100.0) * 100.0, 2)
                else:
                    pnl = yen100(ep, xp)
            et = _parse_ts(acc.get("entry_time") or acc.get("event_time"))
            xt = _parse_ts(ex.get("exit_time") or ex.get("event_time"))
            hold = (xt - et).total_seconds() if et and xt else _f(ex.get("hold_sec"), 0.0) or 0.0
            # Causal: accept-time fields only (never exit / future snapshot fallback).
            feats: dict[str, Optional[float]] = {}
            for out_k, src_k in FEATURE_KEYS_RUNTIME:
                feats[out_k] = _f(acc.get(src_k))
            if feats.get("f_mom") is None:
                feats["f_mom"] = feats.get("f_mom_alt")
            # Scan rank / cap usage / pretrend shape (causal accept fields)
            rank_raw = acc.get("same_scan_rank")
            if isinstance(rank_raw, str) and "/" in rank_raw:
                try:
                    a, b = rank_raw.split("/", 1)
                    feats["f_scan_rank"] = float(a)
                    feats["f_cap_usage"] = float(a) / max(float(b), 1.0)
                except ValueError:
                    feats["f_scan_rank"] = _f(rank_raw)
                    feats["f_cap_usage"] = None
            else:
                feats["f_scan_rank"] = _f(rank_raw)
                feats["f_cap_usage"] = None
            feats["f_cap_mode"] = (
                1.0 if str(acc.get("position_cap_mode")).lower() in ("1", "true", "yes") else 0.0
            )
            shape = str(acc.get("pretrend_shape") or "").upper()
            feats["f_shape_U"] = 1.0 if shape == "U" else 0.0
            feats["f_shape_V"] = 1.0 if shape == "V" else 0.0
            feats["f_shape_N"] = 1.0 if shape in ("N", "FLAT", "") else (0.0 if shape else None)
            np_row = np_map.get(pid) or np_map.get(f"{acc.get('symbol')}|{acc.get('entry_time')}")
            has_np = False
            if np_row and not _b(np_row.get("np_future_leakage")):
                has_np = True
                for out_k, src_k in NP_FEATURE_KEYS:
                    feats[out_k] = _f(np_row.get(src_k))
            rise = feats.get("f_rise5") if feats.get("f_rise5") is not None else feats.get("f_r60")
            spread = feats.get("f_spread")
            near = feats.get("f_near_high")
            vwap = feats.get("f_vwap")
            mom = feats.get("f_mom")
            if None not in (rise, spread, near, vwap, mom):
                feats["f_w54_stop_risk"] = (
                    1.0 * float(rise)
                    + 0.3 * (float(spread) / 10.0)
                    + 0.5 * max(0.0, float(near))
                    + 0.2 * max(0.0, float(vwap))
                    - 0.4 * float(mom)
                )
            else:
                feats["f_w54_stop_risk"] = None
            if rise is not None and near is not None:
                feats["f_chase"] = float(rise) + 0.5 * max(0.0, float(near))
            elif rise is not None:
                feats["f_chase"] = float(rise)
            else:
                feats["f_chase"] = None
            if feats.get("f_np_imb_chg_60") is not None and feats.get("f_np_ret_60") is not None:
                feats["f_div_price_up_board_down"] = float(feats["f_np_ret_60"]) - float(
                    feats["f_np_imb_chg_60"]
                )
            else:
                feats["f_div_price_up_board_down"] = None

            # Normalize exit_reason (older CSV sometimes uses boolean flags)
            exit_reason = str(ex.get("exit_reason") or "")
            if not exit_reason or exit_reason == "live_virtual_hold":
                if _b(ex.get("stop_hit")) or str(ex.get("structural_exit_reason") or "") == "stop_hit":
                    exit_reason = "stop_hit"
                elif _b(ex.get("no_progress_exit")) or str(ex.get("structural_exit_reason") or "") == "no_progress_exit":
                    exit_reason = "no_progress_exit"
                else:
                    exit_reason = str(ex.get("structural_exit_reason") or exit_reason or "")

            trades.append(
                TradeRow(
                    day=day,
                    session=sk,
                    symbol=str(acc.get("symbol") or ""),
                    position_id=pid,
                    entry_time=str(acc.get("entry_time") or ""),
                    exit_time=str(ex.get("exit_time") or ""),
                    entry_price=float(ep),
                    exit_price=float(xp),
                    exit_reason=exit_reason,
                    pnl_yen=float(pnl),
                    pnl_5bps=round(float(pnl) - cost_yen(ep), 2),
                    hold_sec=float(hold),
                    features=feats,
                    # NP pre-entry board history (runtime-causal); not full L2 capture.
                    has_board_hist=has_np,
                    has_np=has_np,
                    causal_ok=True,
                )
            )
            meta["n_joined"] += 1
            if has_np:
                meta["n_with_np"] += 1
            if meta["has_market_capture"]:
                meta["n_with_market_capture_l2"] = meta.get("n_with_market_capture_l2", 0) + 1

    n_acc = int(meta["n_accepted"])
    n_join = int(meta["n_joined"])
    joined_rate = round(n_join / n_acc, 4) if n_acc > 0 else None
    meta["joined_rate"] = joined_rate
    meta["n_with_market_capture_l2"] = int(meta.get("n_with_market_capture_l2") or 0)
    meta["coverage_tier"] = "none"
    if n_join <= 0:
        meta["exclude_reason"] = "no_joined_accept_exit"
        meta["usable"] = False
        meta["coverage_tier"] = "none"
    elif joined_rate is not None and joined_rate < 0.80:
        meta["usable"] = False
        meta["coverage_tier"] = "partial_coverage"
        meta["exclude_reason"] = f"joined_rate_below_80pct ({joined_rate})"
    else:
        meta["usable"] = True
        meta["coverage_tier"] = "formal"
        meta["exclude_reason"] = ""
    return trades, meta


JOINED_RATE_MIN = 0.80


def load_all_trades(
    native: Path = NATIVE,
) -> tuple[list[TradeRow], list[TradeRow], list[dict[str, Any]]]:
    """Return (formal_trades, partial_trades, coverage_rows)."""
    coverage = []
    formal: list[TradeRow] = []
    partial: list[TradeRow] = []
    for d in discover_days(native):
        trades, meta = load_day_trades(d, native=native)
        joined_rate = meta.get("joined_rate")
        tier = meta.get("coverage_tier") or "none"
        coverage.append(
            {
                "day": d,
                "usable": bool(meta["usable"]),
                "coverage_tier": tier,
                "exclude_reason": meta.get("exclude_reason") or "",
                "n_accepted": meta["n_accepted"],
                "n_joined": meta["n_joined"],
                "joined_rate": joined_rate,
                "n_with_np": meta["n_with_np"],
                "n_np_pre_entry_board_history": meta["n_with_np"],
                "has_market_capture": meta["has_market_capture"],
                "n_with_market_capture_l2": meta.get("n_with_market_capture_l2") or 0,
                "sessions": len(meta.get("sessions") or []),
            }
        )
        if tier == "formal":
            formal.extend(trades)
        elif tier == "partial_coverage":
            partial.extend(trades)
    return formal, partial, coverage
