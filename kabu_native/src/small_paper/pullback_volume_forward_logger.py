"""Phase687W57 — Pullback Volume Persistence Forward Logger (observe-only).

Records PullbackMisread Dynamic40 shadow hits with frozen volume_persistence
buckets for Forward validation. Does NOT reject/permit/rank/GateDecision.

Enable: PULLBACK_VOLUME_FORWARD (Paper default ON; elsewhere OFF unless set).
Explicit OFF: PULLBACK_VOLUME_FORWARD=0
"""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.forward_observer_defaults import resolve_pullback_volume_forward
from small_paper.pullback_misread_entry_guard_shadow import (
    would_block_pullback_dynamic40_shadow,
)

JST = ZoneInfo("Asia/Tokyo")
LOGGER_NAME = "pullback_volume_forward"
OWNERSHIP = "RESEARCH"

# Frozen Discovery thresholds from Phase687W56 — DO NOT RETUNE
VOL_PERSISTENCE_HIGH_THR = 0.2782069767789509
VOL_PERSISTENCE_LOW_THR = 0.12710349962769918

DEFAULT_OUT_DIR = Path("results/forward/pullback_volume")
RING_KEEP_SEC = 360.0
RING_MAX_POINTS = 120

JSONL_FIELDS = (
    "candidate_key",
    "trading_date",
    "session",
    "event_time",
    "accepted_at",
    "symbol",
    "universe_slot",
    "sector",
    "entry_price",
    "entry_score_v2",
    "pbv2_internal_reason",
    "official_entry",
    "official_reject",
    "cost_aware_shadow_rank",
    "stop_risk_score",
    "winner_enrichment_score",
    "pullback_misread_shadow_hit",
    "pullback_misread_scope_dynamic40",
    "entry_rise_5min_pct",
    "entry_vwap_dev_pct",
    "vol_persistence_300s",
    "pullback_volume_bucket",
    "volume_acceleration_300s",
    "imbalance_chg_60s",
    "pullback_board_state",
    "pullback_quality_bucket",
    "snap_join_lag_sec",
    "future_leak_flag",
    "return_1m",
    "return_3m",
    "return_5m",
    "return_10m",
    "return_15m",
    "return_30m",
    "mfe_5m",
    "mfe_10m",
    "mfe_30m",
    "mae_5m",
    "mae_10m",
    "mae_30m",
    "hit_stop_1p2",
    "winner_flag",
    "collapse_flag",
    "healthy_pullback_flag",
    "runtime_entry",
    "runtime_exit_reason",
    "runtime_pnl_pct",
    "runtime_pnl_yen_100",
    "label_complete",
    "dq_warn",
)


def logger_enabled(cfg: Any = None) -> bool:
    enabled, _src = resolve_pullback_volume_forward(cfg)
    return enabled


def logger_enabled_with_source(cfg: Any = None) -> tuple[bool, str]:
    return resolve_pullback_volume_forward(cfg)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _pf(xs: Sequence[float]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not vals:
        return None
    gp = sum(max(v, 0.0) for v in vals)
    gl = abs(sum(min(v, 0.0) for v in vals))
    if gl <= 1e-12:
        return 999.0 if gp > 0 else None
    return gp / gl


def compute_vol_persistence_300s(volumes: Sequence[float]) -> Optional[float]:
    """w43c SoT: mean(diff(volume) > 0) over window. Higher = sustaining participation."""
    vals = [float(v) for v in volumes if _f(v) is not None]
    if len(vals) < 4:
        return None
    dv = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
    if not dv:
        return None
    return float(sum(1 for d in dv if d > 0) / len(dv))


def compute_vol_accel_300s(volumes: Sequence[float]) -> Optional[float]:
    vals = [float(v) for v in volumes if _f(v) is not None]
    if len(vals) < 6:
        return None
    mid = len(vals) // 2
    d1 = vals[mid] - vals[0]
    d2 = vals[-1] - vals[mid]
    return float(d2 - d1)


def volume_bucket(vol_persistence: Optional[float]) -> str:
    if vol_persistence is None:
        return "missing"
    if vol_persistence >= VOL_PERSISTENCE_HIGH_THR:
        return "high"
    if vol_persistence <= VOL_PERSISTENCE_LOW_THR:
        return "low"
    return "mid"


def board_state(imbalance_chg_60s: Optional[float]) -> str:
    if imbalance_chg_60s is None:
        return "missing"
    if imbalance_chg_60s > 0:
        return "improving"
    if imbalance_chg_60s < 0:
        return "worsening"
    return "flat"


def quality_bucket(board: str, vol: str) -> str:
    if vol == "missing" or board == "missing":
        if vol == "missing" and board == "missing":
            return "missing"
        if vol == "high":
            return "vol_high_other_board"
        if vol == "low":
            return "vol_low_other_board"
        return "missing" if vol == "missing" else "vol_mid"
    if board == "improving" and vol == "high":
        return "board_up_vol_high"
    if board == "improving" and vol == "low":
        return "board_up_vol_low"
    if board == "worsening" and vol == "high":
        return "board_down_vol_high"
    if board == "worsening" and vol == "low":
        return "board_down_vol_low"
    if vol == "high":
        return "vol_high_other_board"
    if vol == "low":
        return "vol_low_other_board"
    return "vol_mid"


def classify_healthy_collapse(
    *,
    mfe10: Optional[float],
    mfe30: Optional[float],
    mae10: Optional[float],
    pnl_30m: Optional[float],
    hit_stop: bool,
    winner_flag: bool,
) -> tuple[bool, bool]:
    """P12-aligned labels. Returns (healthy, collapse)."""
    healthy = bool(
        (mfe10 is not None and mfe10 >= 0.50)
        or (mfe30 is not None and mfe30 >= 1.00)
        or winner_flag
    )
    collapse = bool(
        hit_stop
        or (
            mfe10 is not None
            and mae10 is not None
            and mfe10 < 0.20
            and mae10 <= -0.50
        )
        or (pnl_30m is not None and pnl_30m < 0)
    )
    return healthy, collapse


def sector_from_symbol(symbol: str) -> str:
    code = str(symbol or "").replace(".T", "")
    return code[:1] if code else ""


def candidate_key(symbol: str, entry_time: str) -> str:
    return f"{symbol}|{entry_time}"


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=JST)
        return v.astimezone(JST)
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if t.tzinfo is None:
            return t.replace(tzinfo=JST)
        return t.astimezone(JST)
    except Exception:
        return None


@dataclass
class PullbackVolumeForwardState:
    enabled: bool = False
    out_dir: Path = field(default_factory=lambda: DEFAULT_OUT_DIR)
    trading_date: str = ""
    session: str = ""
    # lightweight rings: symbol -> [(epoch, value)]
    vol_ring: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    imb_ring: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    px_ring: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    # candidate_key -> row
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    hit_count: int = 0
    duplicate_skipped: int = 0
    future_leak_suspects: int = 0
    written_keys: set[str] = field(default_factory=set)

    def summary_block(self) -> dict[str, Any]:
        # W63: eligible/recorded are Pullback-Volume-local denominators (not Misread hits).
        eligible = int(self.hit_count)
        recorded = int(len(self.rows))
        return {
            "enabled": self.enabled,
            "hits": self.hit_count,
            "rows": len(self.rows),
            "pullback_volume_eligible_count": eligible,
            "pullback_volume_recorded_count": recorded,
            "eligible": eligible,
            "recorded": recorded,
            "duplicate_skipped": self.duplicate_skipped,
            "future_leak_suspects": self.future_leak_suspects,
            "status": "collecting",
            "vol_high_thr": VOL_PERSISTENCE_HIGH_THR,
            "vol_low_thr": VOL_PERSISTENCE_LOW_THR,
        }


def _trim_ring(ring: list[tuple[float, float]], now_epoch: float) -> None:
    cutoff = now_epoch - RING_KEEP_SEC
    while ring and ring[0][0] < cutoff:
        ring.pop(0)
    if len(ring) > RING_MAX_POINTS:
        del ring[: len(ring) - RING_MAX_POINTS]


def note_push(
    state: PullbackVolumeForwardState,
    *,
    symbol: str,
    payload: Mapping[str, Any],
    event_epoch: Optional[float] = None,
) -> None:
    """Observe-only ring update. Never affects GateDecision."""
    if not state.enabled:
        return
    sym = str(symbol or "")
    if not sym:
        return
    now = event_epoch
    if now is None:
        ts = _parse_ts(payload.get("CurrentPriceTime") or payload.get("ReceivedTime"))
        now = ts.timestamp() if ts else datetime.now(JST).timestamp()
    vol = _f(payload.get("TradingVolume") or payload.get("trading_volume"))
    if vol is not None:
        r = state.vol_ring.setdefault(sym, [])
        if not r or r[-1][0] != now:
            r.append((now, vol))
        else:
            r[-1] = (now, vol)
        _trim_ring(r, now)
    # imbalance from board if present (Bid/Ask qty proxy already on trade path via calc)
    imb = _f(
        payload.get("imbalance_l5")
        or payload.get("entry_order_book_imbalance")
        or payload.get("board_imbalance")
    )
    if imb is not None:
        r = state.imb_ring.setdefault(sym, [])
        if not r or abs(r[-1][0] - now) >= 1.0:
            r.append((now, imb))
        _trim_ring(r, now)
    px = _f(payload.get("CurrentPrice"))
    if px is not None and px > 0:
        r = state.px_ring.setdefault(sym, [])
        if not r or r[-1][0] != now:
            r.append((now, px))
        else:
            r[-1] = (now, px)
        _trim_ring(r, now)


def _vol_from_ring(state: PullbackVolumeForwardState, symbol: str, t0: float) -> Optional[float]:
    ring = state.vol_ring.get(symbol) or []
    vals = [v for ts, v in ring if t0 - 300.0 <= ts <= t0]
    return compute_vol_persistence_300s(vals)


def _vol_accel_from_ring(state: PullbackVolumeForwardState, symbol: str, t0: float) -> Optional[float]:
    ring = state.vol_ring.get(symbol) or []
    vals = [v for ts, v in ring if t0 - 300.0 <= ts <= t0]
    return compute_vol_accel_300s(vals)


def _imb_chg_from_ring(state: PullbackVolumeForwardState, symbol: str, t0: float) -> Optional[float]:
    ring = state.imb_ring.get(symbol) or []
    if not ring:
        return None
    cur = None
    past = None
    for ts, v in reversed(ring):
        if ts <= t0 and cur is None:
            cur = v
        if ts <= t0 - 60.0 and past is None:
            past = v
            break
    if cur is None or past is None:
        return None
    return float(cur - past)


def extract_features(
    state: PullbackVolumeForwardState,
    trade: Mapping[str, Any],
    *,
    entry_epoch: Optional[float] = None,
) -> dict[str, Any]:
    sym = str(trade.get("symbol") or "")
    t0 = entry_epoch
    if t0 is None:
        et = _parse_ts(trade.get("entry_time") or trade.get("accepted_at") or trade.get("event_time"))
        t0 = et.timestamp() if et else datetime.now(JST).timestamp()

    vol_p = _f(trade.get("vol_persistence_300s"))
    if vol_p is None:
        vol_p = _vol_from_ring(state, sym, t0)
    vol_acc = _f(trade.get("volume_acceleration_300s") or trade.get("vol_accel_300s"))
    if vol_acc is None:
        vol_acc = _vol_accel_from_ring(state, sym, t0)
    imb_chg = _f(trade.get("imbalance_chg_60s"))
    if imb_chg is None:
        imb_chg = _imb_chg_from_ring(state, sym, t0)
        if imb_chg is None:
            # last resort: single entry imbalance has no chg
            pass

    # join lag: latest ring point at-or-before t0
    lag = None
    future_leak = False
    ring = state.vol_ring.get(sym) or state.imb_ring.get(sym) or []
    if ring:
        before = [ts for ts, _ in ring if ts <= t0 + 1e-6]
        after = [ts for ts, _ in ring if ts > t0 + 1e-6]
        if before:
            lag = float(t0 - before[-1])
        if after and min(after) < t0:  # impossible; defensive
            future_leak = True
        # any ring point used after t0 would be leak — we only use <= t0 above

    vb = volume_bucket(vol_p)
    bs = board_state(imb_chg)
    return {
        "vol_persistence_300s": vol_p,
        "pullback_volume_bucket": vb,
        "volume_acceleration_300s": vol_acc,
        "imbalance_chg_60s": imb_chg,
        "pullback_board_state": bs,
        "pullback_quality_bucket": quality_bucket(bs, vb),
        "snap_join_lag_sec": lag,
        "future_leak_flag": future_leak,
    }


def build_entry_row(
    state: PullbackVolumeForwardState,
    trade: Mapping[str, Any],
    *,
    official_entry: bool,
    official_reject: bool,
    session: str = "",
    trading_date: str = "",
    event_time: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Build row only when Dynamic40 PullbackMisread shadow would hit."""
    if not would_block_pullback_dynamic40_shadow(trade):
        return None
    sym = str(trade.get("symbol") or "")
    et = str(trade.get("entry_time") or trade.get("accepted_at") or event_time or "")
    if not sym or not et:
        return None
    key = candidate_key(sym, et)
    if key in state.rows or key in state.written_keys:
        state.duplicate_skipped += 1
        return None

    feats = extract_features(state, trade)
    if feats.get("future_leak_flag"):
        state.future_leak_suspects += 1

    day = trading_date or state.trading_date or datetime.now(JST).strftime("%Y%m%d")
    sess = session or state.session or ""
    row: dict[str, Any] = {
        "candidate_key": key,
        "trading_date": day,
        "session": sess,
        "event_time": event_time or et,
        "accepted_at": trade.get("accepted_at") or et,
        "symbol": sym,
        "universe_slot": trade.get("universe_slot") or "",
        "sector": sector_from_symbol(sym),
        "entry_price": _f(trade.get("entry_price") or trade.get("current_price")),
        "entry_score_v2": _f(trade.get("entry_expectancy_score_v2")),
        "pbv2_internal_reason": trade.get("pbv2_internal_reason") or trade.get("final_reject_reason") or "",
        "official_entry": bool(official_entry),
        "official_reject": bool(official_reject),
        "cost_aware_shadow_rank": trade.get("cost_aware_shadow_rank"),
        "stop_risk_score": _f(trade.get("stop_risk_score")),
        "winner_enrichment_score": _f(trade.get("winner_enrichment_score")),
        "pullback_misread_shadow_hit": True,
        "pullback_misread_scope_dynamic40": True,
        "entry_rise_5min_pct": _f(trade.get("entry_rise_5min_pct")),
        "entry_vwap_dev_pct": _f(trade.get("entry_vwap_dev_pct")),
        **feats,
        "return_1m": None,
        "return_3m": None,
        "return_5m": None,
        "return_10m": None,
        "return_15m": None,
        "return_30m": None,
        "mfe_5m": None,
        "mfe_10m": None,
        "mfe_30m": None,
        "mae_5m": None,
        "mae_10m": None,
        "mae_30m": None,
        "hit_stop_1p2": False,
        "winner_flag": False,
        "collapse_flag": False,
        "healthy_pullback_flag": False,
        "runtime_entry": bool(official_entry),
        "runtime_exit_reason": "",
        "runtime_pnl_pct": None,
        "runtime_pnl_yen_100": None,
        "label_complete": False,
        "dq_warn": "",
        "_entry_epoch": (_parse_ts(et) or datetime.now(JST)).timestamp(),
        "_entry_px": _f(trade.get("entry_price") or trade.get("current_price")),
    }
    state.rows[key] = row
    state.hit_count += 1
    return row


def update_price_path(
    state: PullbackVolumeForwardState,
    *,
    symbol: str,
    price: float,
    event_epoch: float,
) -> None:
    """Update MFE/MAE/returns from ENTRY-after prices only (no future leak into features)."""
    if not state.enabled or price <= 0:
        return
    for row in state.rows.values():
        if str(row.get("symbol")) != symbol:
            continue
        if row.get("label_complete"):
            continue
        t0 = float(row.get("_entry_epoch") or 0)
        ep = _f(row.get("_entry_px") or row.get("entry_price"))
        if ep is None or ep <= 0 or event_epoch < t0 - 1e-6:
            if event_epoch < t0 - 1e-6:
                # price before entry must not update labels
                continue
            continue
        ret = (price / ep - 1.0) * 100.0
        dt = event_epoch - t0
        # returns at horizons (last price at or after horizon marks)
        for sec, key in (
            (60, "return_1m"),
            (180, "return_3m"),
            (300, "return_5m"),
            (600, "return_10m"),
            (900, "return_15m"),
            (1800, "return_30m"),
        ):
            if dt >= sec - 1.0:
                row[key] = ret
        # MFE/MAE running
        for sec, mk, ak in (
            (300, "mfe_5m", "mae_5m"),
            (600, "mfe_10m", "mae_10m"),
            (1800, "mfe_30m", "mae_30m"),
        ):
            if dt <= sec + 1.0 or row.get(mk) is None:
                prev_mfe = _f(row.get(mk))
                prev_mae = _f(row.get(ak))
                row[mk] = ret if prev_mfe is None else max(prev_mfe, ret)
                row[ak] = ret if prev_mae is None else min(prev_mae, ret)
        if dt >= 1800:
            _finalize_labels(row)


def _finalize_labels(row: dict[str, Any]) -> None:
    mfe10 = _f(row.get("mfe_10m"))
    mfe30 = _f(row.get("mfe_30m"))
    mae10 = _f(row.get("mae_10m"))
    pnl30 = _f(row.get("return_30m"))
    if row.get("runtime_pnl_pct") is not None and pnl30 is None:
        pnl30 = _f(row.get("runtime_pnl_pct"))
    hit_stop = bool(row.get("hit_stop_1p2"))
    winner = bool(
        (mfe30 is not None and mfe30 >= 1.0)
        or (mfe10 is not None and mfe10 >= 0.80)
        or ((_f(row.get("runtime_pnl_pct")) or 0) >= 0.5)
        or ((_f(row.get("runtime_pnl_yen_100")) or 0) > 0 and (_f(row.get("runtime_pnl_pct")) or 0) > 0)
    )
    # align with P12: winner_flag also yen>0 path via runtime
    if (_f(row.get("runtime_pnl_yen_100")) or 0) > 0:
        winner = True
    healthy, collapse = classify_healthy_collapse(
        mfe10=mfe10,
        mfe30=mfe30,
        mae10=mae10,
        pnl_30m=pnl30,
        hit_stop=hit_stop,
        winner_flag=winner,
    )
    row["winner_flag"] = winner
    row["healthy_pullback_flag"] = healthy
    row["collapse_flag"] = collapse and not (healthy and not hit_stop and (pnl30 or 0) >= 0)
    # if both, prefer collapse when stop else healthy when strong winner
    if healthy and collapse:
        if hit_stop or (pnl30 is not None and pnl30 < 0):
            row["healthy_pullback_flag"] = False
            row["collapse_flag"] = True
        else:
            row["collapse_flag"] = False
            row["healthy_pullback_flag"] = True
    row["label_complete"] = row.get("return_30m") is not None or row.get("runtime_pnl_pct") is not None


def note_runtime_exit(
    state: PullbackVolumeForwardState,
    trade: Mapping[str, Any],
) -> None:
    if not state.enabled:
        return
    sym = str(trade.get("symbol") or "")
    et = str(trade.get("entry_time") or "")
    key = candidate_key(sym, et)
    row = state.rows.get(key)
    if row is None:
        return
    reason = str(trade.get("structural_exit_reason") or trade.get("exit_reason") or "")
    row["runtime_exit_reason"] = reason
    row["hit_stop_1p2"] = reason == "stop_hit" or bool(trade.get("stop_hit"))
    row["runtime_pnl_pct"] = _f(trade.get("pnl_pct"))
    yen = _f(trade.get("pnl_yen_100"))
    if yen is None and row.get("runtime_pnl_pct") is not None:
        yen = float(row["runtime_pnl_pct"]) * 100.0
    row["runtime_pnl_yen_100"] = yen
    xp = _f(trade.get("exit_price"))
    ep = _f(row.get("entry_price"))
    xt = _parse_ts(trade.get("exit_time"))
    if xp and ep and xt:
        update_price_path(state, symbol=sym, price=xp, event_epoch=xt.timestamp())
    _finalize_labels(row)


def append_jsonl_day(state: PullbackVolumeForwardState, *, day: Optional[str] = None) -> Path:
    day = day or state.trading_date or datetime.now(JST).strftime("%Y%m%d")
    state.out_dir.mkdir(parents=True, exist_ok=True)
    path = state.out_dir / f"pullback_volume_forward_{day}.jsonl"
    # rewrite day file from in-memory + prior non-dup keys for crash safety
    existing: dict[str, dict[str, Any]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            k = str(obj.get("candidate_key") or "")
            if k:
                existing[k] = obj
    for k, row in state.rows.items():
        clean = {f: row.get(f) for f in JSONL_FIELDS}
        existing[k] = clean
        state.written_keys.add(k)
    with path.open("w", encoding="utf-8") as f:
        for k in sorted(existing.keys()):
            f.write(json.dumps(existing[k], ensure_ascii=False, default=str) + "\n")
    return path


def load_day_rows(out_dir: Path, day: str) -> list[dict[str, Any]]:
    path = out_dir / f"pullback_volume_forward_{day}.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def aggregate_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def subset(pred):
        return [r for r in rows if pred(r)]

    def rates(sub):
        if not sub:
            return {
                "n": 0,
                "healthy_rate": None,
                "collapse_rate": None,
                "mean_pnl_5bps": None,
                "pf_5bps": None,
            }
        pnl = []
        for r in sub:
            p = _f(r.get("runtime_pnl_pct"))
            if p is None:
                p = _f(r.get("return_30m"))
            if p is not None:
                pnl.append(p - 0.05)
        healthy = sum(1 for r in sub if r.get("healthy_pullback_flag"))
        collapse = sum(1 for r in sub if r.get("collapse_flag"))
        return {
            "n": len(sub),
            "healthy_rate": healthy / len(sub),
            "collapse_rate": collapse / len(sub),
            "mean_pnl_5bps": sum(pnl) / len(pnl) if pnl else None,
            "pf_5bps": _pf(pnl),
        }

    hits = list(rows)
    high = subset(lambda r: r.get("pullback_volume_bucket") == "high")
    mid = subset(lambda r: r.get("pullback_volume_bucket") == "mid")
    low = subset(lambda r: r.get("pullback_volume_bucket") == "low")
    out = {
        "total_pullback_hits": len(hits),
        "volume_high_n": len(high),
        "volume_mid_n": len(mid),
        "volume_low_n": len(low),
        "volume_high": rates(high),
        "volume_low": rates(low),
        "volume_mid": rates(mid),
        "board_volume": {},
    }
    for name in (
        "board_up_vol_high",
        "board_up_vol_low",
        "board_down_vol_high",
        "board_down_vol_low",
    ):
        sub = subset(lambda r, n=name: r.get("pullback_quality_bucket") == n)
        out["board_volume"][name] = rates(sub)
    # flat aliases for summary csv
    out["volume_high_healthy_rate"] = out["volume_high"]["healthy_rate"]
    out["volume_high_collapse_rate"] = out["volume_high"]["collapse_rate"]
    out["volume_high_mean_pnl_5bps"] = out["volume_high"]["mean_pnl_5bps"]
    out["volume_high_pf_5bps"] = out["volume_high"]["pf_5bps"]
    out["volume_low_healthy_rate"] = out["volume_low"]["healthy_rate"]
    out["volume_low_collapse_rate"] = out["volume_low"]["collapse_rate"]
    out["volume_low_mean_pnl_5bps"] = out["volume_low"]["mean_pnl_5bps"]
    out["volume_low_pf_5bps"] = out["volume_low"]["pf_5bps"]
    return out


def dq_audit(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0, "ok": True, "warnings": []}
    miss_vol = sum(1 for r in rows if r.get("vol_persistence_300s") is None) / n
    miss_imb = sum(1 for r in rows if r.get("imbalance_chg_60s") is None) / n
    label_ok = sum(1 for r in rows if r.get("label_complete")) / n
    keys = [str(r.get("candidate_key")) for r in rows]
    dup = 1.0 - (len(set(keys)) / max(1, len(keys)))
    leak = sum(1 for r in rows if r.get("future_leak_flag"))
    warns = []
    if miss_vol > 0.05:
        warns.append(f"volume_persistence_missing>{miss_vol:.1%}")
    if label_ok < 0.90:
        warns.append(f"future_label_completion<{label_ok:.1%}")
    if dup > 0.01:
        warns.append(f"duplicate_rate>{dup:.1%}")
    if leak > 0:
        warns.append(f"future_leak_suspects={leak}")
    return {
        "n": n,
        "vol_persistence_missing_rate": miss_vol,
        "imbalance_chg_missing_rate": miss_imb,
        "future_label_completion_rate": label_ok,
        "duplicate_rate": dup,
        "future_leak_suspects": leak,
        "invalidate_day": leak > 0,
        "warnings": warns,
        "ok": leak == 0,
    }


def write_day_summary(out_dir: Path, day: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    agg = aggregate_rows(rows)
    dq = dq_audit(rows)
    payload = {
        "trading_date": day,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "status": "invalidated_future_leak" if dq.get("invalidate_day") else "collecting",
        **agg,
        "dq": dq,
        "thresholds": {
            "vol_persistence_high": VOL_PERSISTENCE_HIGH_THR,
            "vol_persistence_low": VOL_PERSISTENCE_LOW_THR,
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"pullback_volume_forward_summary_{day}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    # flat csv
    flat = {
        "trading_date": day,
        "total_pullback_hits": agg["total_pullback_hits"],
        "volume_high_n": agg["volume_high_n"],
        "volume_mid_n": agg["volume_mid_n"],
        "volume_low_n": agg["volume_low_n"],
        "volume_high_healthy_rate": agg["volume_high_healthy_rate"],
        "volume_high_collapse_rate": agg["volume_high_collapse_rate"],
        "volume_high_mean_pnl_5bps": agg["volume_high_mean_pnl_5bps"],
        "volume_high_pf_5bps": agg["volume_high_pf_5bps"],
        "volume_low_healthy_rate": agg["volume_low_healthy_rate"],
        "volume_low_collapse_rate": agg["volume_low_collapse_rate"],
        "volume_low_mean_pnl_5bps": agg["volume_low_mean_pnl_5bps"],
        "volume_low_pf_5bps": agg["volume_low_pf_5bps"],
        "status": payload["status"],
    }
    for k, v in agg["board_volume"].items():
        flat[f"{k}_n"] = v["n"]
        flat[f"{k}_healthy_rate"] = v["healthy_rate"]
        flat[f"{k}_collapse_rate"] = v["collapse_rate"]
        flat[f"{k}_mean_pnl_5bps"] = v["mean_pnl_5bps"]
        flat[f"{k}_pf_5bps"] = v["pf_5bps"]
    csv_path = out_dir / f"pullback_volume_forward_summary_{day}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(flat.keys()))
        w.writeheader()
        w.writerow(flat)
    return payload


def rebuild_cumulative(out_dir: Path) -> dict[str, Any]:
    days = sorted(
        p.name.replace("pullback_volume_forward_", "").replace(".jsonl", "")
        for p in out_dir.glob("pullback_volume_forward_????????.jsonl")
    )
    all_rows: list[dict[str, Any]] = []
    day_meta = []
    for day in days:
        rows = load_day_rows(out_dir, day)
        dq = dq_audit(rows)
        if dq.get("invalidate_day"):
            day_meta.append({"trading_date": day, "status": "invalidated_future_leak", "n": len(rows)})
            continue
        all_rows.extend(rows)
        day_meta.append({"trading_date": day, "status": "ok", "n": len(rows)})
    agg = aggregate_rows(all_rows)
    symbols = {str(r.get("symbol")) for r in all_rows}
    sectors = [sector_from_symbol(str(r.get("symbol"))) for r in all_rows]
    max_sec = 0.0
    if sectors:
        from collections import Counter

        c = Counter(sectors)
        max_sec = c.most_common(1)[0][1] / len(sectors)
    gate = {
        "volume_high_n": agg["volume_high_n"],
        "volume_low_n": agg["volume_low_n"],
        "trading_days": len([d for d in day_meta if d["status"] == "ok"]),
        "symbols": len(symbols),
        "max_sector_share": max_sec,
        "forward_sample_gate_pass": bool(
            agg["volume_high_n"] >= 50
            and agg["volume_low_n"] >= 50
            and len([d for d in day_meta if d["status"] == "ok"]) >= 10
            and len(symbols) >= 20
            and max_sec <= 0.50
        ),
    }
    payload = {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "days": day_meta,
        **agg,
        "sample_gate": gate,
        "status": "collecting",
        "verdict_hint": "PULLBACK_VOLUME_INSUFFICIENT_SAMPLE"
        if not gate["forward_sample_gate_pass"]
        else "ready_for_reeval",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pullback_volume_forward_cumulative.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    # compact csv
    flat = {
        "total_pullback_hits": agg["total_pullback_hits"],
        "volume_high_n": agg["volume_high_n"],
        "volume_low_n": agg["volume_low_n"],
        "volume_high_healthy_rate": agg["volume_high_healthy_rate"],
        "volume_low_collapse_rate": agg["volume_low_collapse_rate"],
        "trading_days": gate["trading_days"],
        "symbols": gate["symbols"],
        "max_sector_share": max_sec,
        "forward_sample_gate_pass": gate["forward_sample_gate_pass"],
        "status": payload["status"],
    }
    with (out_dir / "pullback_volume_forward_cumulative.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(flat.keys()))
        w.writeheader()
        w.writerow(flat)
    return payload


def cleanup_logger_temp_files(out_dir: Path) -> list[str]:
    """Remove only this logger's temp probes — never other Phase artifacts."""
    removed: list[str] = []
    if not out_dir.is_dir():
        return removed
    for pattern in (".write_probe", "*.tmp", "*.partial"):
        for p in out_dir.glob(pattern):
            try:
                if p.is_file():
                    p.unlink()
                    removed.append(p.name)
            except Exception:
                continue
    return removed


def finalize_session(state: PullbackVolumeForwardState) -> dict[str, Any]:
    if not state.enabled:
        return {"enabled": False}
    # finalize open labels best-effort
    for row in state.rows.values():
        if not row.get("label_complete"):
            _finalize_labels(row)
    day = state.trading_date or datetime.now(JST).strftime("%Y%m%d")
    append_jsonl_day(state, day=day)
    rows = load_day_rows(state.out_dir, day)
    day_sum = write_day_summary(state.out_dir, day, rows)
    cum = rebuild_cumulative(state.out_dir)
    removed = cleanup_logger_temp_files(state.out_dir)
    return {
        "day_summary": day_sum,
        "cumulative": cum,
        "temp_cleaned": removed,
        **state.summary_block(),
    }


def format_discord_lines(summary: Mapping[str, Any]) -> list[str]:
    block = summary.get("pullback_volume_forward")
    if not isinstance(block, Mapping):
        return []
    if not block.get("enabled") and not block.get("hits"):
        return []
    hits = int(block.get("hits") or block.get("total_pullback_hits") or 0)
    vh = block.get("volume_high") if isinstance(block.get("volume_high"), Mapping) else {}
    vl = block.get("volume_low") if isinstance(block.get("volume_low"), Mapping) else {}
    vh_n = int(block.get("volume_high_n") or vh.get("n") or 0)
    vl_n = int(block.get("volume_low_n") or vl.get("n") or 0)
    vh_h = vh.get("healthy_rate")
    vl_c = vl.get("collapse_rate")
    bv = block.get("board_volume") if isinstance(block.get("board_volume"), Mapping) else {}
    down_low = bv.get("board_down_vol_low") if isinstance(bv.get("board_down_vol_low"), Mapping) else {}
    down_n = int(down_low.get("n") or 0)
    vh_h_s = f"{100*vh_h:.0f}%" if isinstance(vh_h, (int, float)) else "n/a"
    vl_c_s = f"{100*vl_c:.0f}%" if isinstance(vl_c, (int, float)) else "n/a"
    return [
        "[Pullback Volume Forward]",
        f"hits: {hits}",
        f"vol_high: {vh_n} / healthy {vh_h_s}",
        f"vol_low: {vl_n} / collapse {vl_c_s}",
        f"board↓×vol_low: {down_n}",
        "status: collecting",
    ]


def disk_usage_pct(path: str = "C:/") -> float:
    try:
        t, u, _f = shutil.disk_usage(path)
        return 100.0 * u / t
    except Exception:
        return -1.0
