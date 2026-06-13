#!/usr/bin/env python3
"""
Phase328: VWAP contraction generalization review (20260518–20260603, 30 sessions).

Same production ENTRY as Phase318; counterfactual EXIT on recorded tick paths.
Output: phase328_vwap_contraction_full_replay_review.json
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
PUSH_ROOT = REPO / "kabu_native" / "data" / "push_jsonl"
OUT = REPO / "kabu_native/results/reports/phase328_vwap_contraction_full_replay_review.json"
PHASE318_CK = REPO / "kabu_native/results/reports/phase318_current_production_logic_replay.checkpoint.json"

DATE_START = 20260518
DATE_END = 20260603
MAX_POS = 3
REJECT_REPLAY_MAX_EVENTS = 500_000
JST = ZoneInfo("Asia/Tokyo")

ENTRY_VWAP_DEV_MIN_PCT = 0.5
CURRENT_VWAP_DEV_LTE_PCT = 0.5
HARD_STOP_PCT = 1.20
TRAILING_ACTIVATE_PCT = 0.80
GIVEBACK_FRAC = 0.50

BASE_HARD_EXCLUDE = frozenset(
    {
        "symbol_cooloff",
        "risk_cluster_block",
        "daily_loss_guard",
        "wrong_profile",
        "outside_allowed_trading_window",
        "low_liquidity_shadow",
        "low_liquidity_shadow_reject",
    }
)
AUX_FILTER = {
    "hard_exclude_extra": frozenset({"daytrade_suitability", "entry_price_risk_guard"}),
    "daytrade_mode": "on",
    "daytrade_percentile": 0.50,
    "price_risk_universe": True,
    "price_risk_guard": True,
}


def _bootstrap() -> Any:
    for p in (REPO / "kabu_native" / "src", REPO / "kabu_native" / "scripts", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    import run_phase270_fast_paper_integration_comparison as p270

    return p270


def _load_p318() -> Any:
    path = REPO / "kabu_native/scripts/run_phase318_current_production_logic_replay.py"
    spec = importlib.util.spec_from_file_location("phase318_p328", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase318_p328"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_p71() -> Any:
    path = REPO / "kabu_native/scripts/run_phase71_split_momentum_fade_review.py"
    spec = importlib.util.spec_from_file_location("phase71_p328", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p328"] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _push_dir(day: str) -> Path:
    return PUSH_ROOT / f"{day[:4]}-{day[4:6]}-{day[6:8]}"


_VWAP_CACHE: dict[tuple[str, str], list[tuple[float, float]]] = {}


def _load_vwap_series_one(push_dir: Path, sym: str) -> list[tuple[float, float]]:
    key = (str(push_dir), sym)
    if key in _VWAP_CACHE:
        return _VWAP_CACHE[key]
    series: list[tuple[float, float]] = []
    if push_dir.is_dir():
        path = push_dir / f"{sym}.jsonl"
        if not path.is_file():
            path = push_dir / f"{sym.replace('.T', '')}.jsonl"
        if path.is_file():
            with path.open(encoding="utf-8") as f:
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
                    vwap = _float(payload.get("VWAP"))
                    if vwap is None or vwap <= 0:
                        continue
                    series.append((ts, float(vwap)))
            series.sort(key=lambda x: x[0])
    _VWAP_CACHE[key] = series
    return series


def _lookup_vwap(series: list[tuple[float, float]], ts: float) -> Optional[float]:
    if not series:
        return None
    times = [t for t, _ in series]
    i = bisect_right(times, ts) - 1
    if i < 0:
        i = 0
    return series[i][1]


def _entry_vwap_dev_pct(entry_px: float, vwap: Optional[float]) -> Optional[float]:
    if vwap is None or vwap <= 0 or entry_px <= 0:
        return None
    return round((entry_px - vwap) / vwap * 100.0, 4)


def _vwap_ref(entry_price: float, entry_vwap_dev_pct: float) -> float:
    return entry_price / (1.0 + entry_vwap_dev_pct / 100.0)


def _current_vwap_dev_pct(price: float, vwap_ref: float) -> float:
    if vwap_ref <= 0:
        return 0.0
    return round((price - vwap_ref) / vwap_ref * 100.0, 4)


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


@dataclass
class PathTrade:
    symbol: str
    day: str
    session_id: str
    stream: str
    entry_time: str
    entry_price: float
    entry_vwap_dev_pct: Optional[float]
    prices: list[float] = field(default_factory=list)


@dataclass
class ExitTrade:
    symbol: str
    day: str
    session_id: str
    stream: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_yen_100: float
    exit_reason: str
    entry_vwap_dev_pct: Optional[float]


def _simulate_exit(
    trade: PathTrade,
    *,
    vwap_contraction: bool,
    fallback_reason: str = "session_close",
) -> ExitTrade:
    from replay.pnl_yen import compute_pnl_yen_100

    entry = trade.entry_price
    stop = entry * (1.0 - HARD_STOP_PCT / 100.0)
    vwap_ref = (
        _vwap_ref(entry, trade.entry_vwap_dev_pct)
        if trade.entry_vwap_dev_pct is not None
        else None
    )
    peak_pnl = 0.0
    path = trade.prices if trade.prices else [entry]

    for i, px in enumerate(path):
        pnl = _pnl_pct(entry, px)
        peak_pnl = max(peak_pnl, pnl)
        if px <= stop:
            return ExitTrade(
                trade.symbol,
                trade.day,
                trade.session_id,
                trade.stream,
                entry,
                px,
                pnl,
                compute_pnl_yen_100(entry, px),
                "stop_hit",
                trade.entry_vwap_dev_pct,
            )
        if peak_pnl >= TRAILING_ACTIVATE_PCT and pnl <= peak_pnl * GIVEBACK_FRAC:
            return ExitTrade(
                trade.symbol,
                trade.day,
                trade.session_id,
                trade.stream,
                entry,
                px,
                pnl,
                compute_pnl_yen_100(entry, px),
                "trailing_mfe_exit",
                trade.entry_vwap_dev_pct,
            )
        if (
            vwap_contraction
            and i > 0
            and vwap_ref is not None
            and trade.entry_vwap_dev_pct is not None
            and trade.entry_vwap_dev_pct > ENTRY_VWAP_DEV_MIN_PCT
            and _current_vwap_dev_pct(px, vwap_ref) <= CURRENT_VWAP_DEV_LTE_PCT
        ):
            return ExitTrade(
                trade.symbol,
                trade.day,
                trade.session_id,
                trade.stream,
                entry,
                px,
                pnl,
                compute_pnl_yen_100(entry, px),
                "vwap_contraction_exit",
                trade.entry_vwap_dev_pct,
            )

    exit_px = float(path[-1])
    pnl = _pnl_pct(entry, exit_px)
    return ExitTrade(
        trade.symbol,
        trade.day,
        trade.session_id,
        trade.stream,
        entry,
        exit_px,
        pnl,
        compute_pnl_yen_100(entry, exit_px),
        fallback_reason,
        trade.entry_vwap_dev_pct,
    )


class EntryPathCollector:
    """Phase318 production entry; collect tick paths without interim structural exit."""

    def __init__(self, p318: Any, p71: Any, p270: Any, *, score_points: dict[str, int], score_min: int):
        self.p318 = p318
        self.p71 = p71
        self.p270 = p270
        self.score_points = score_points
        self.score_min = score_min
        self.sym_states: dict[str, Any] = {}
        self.active: dict[str, PathTrade] = {}
        self.completed_paths: list[PathTrade] = []
        self._pending_time: Optional[str] = None
        self._pending: list[tuple[dict[str, Any], int, list[str]]] = []
        self._day = ""
        self._session_id = ""
        self._stream = ""
        self._universe_syms: set[str] = set()
        self._daytrade_state: Any = None
        self._price_guard = self.p318._price_guard_state()
        self._ring = self.p318.PriceRingTracker()
        self._push_dir = Path(".")

    def begin_session(self, meta: dict[str, Any]) -> None:
        self._day = meta["day"]
        self._session_id = meta["session_id"]
        self._stream = meta.get("stream") or "unknown"
        self.sym_states = {}
        self.active = {}
        self._pending = []
        self._pending_time = None
        self._ring = self.p318.PriceRingTracker()
        self._universe_syms = (
            self.p270._load_universe_symbols(self._day, price_risk=True)
            if AUX_FILTER.get("price_risk_universe")
            else set()
        )
        if AUX_FILTER.get("daytrade_mode") == "on":
            self._daytrade_state = self.p318._daytrade_state(
                self.p270, meta["session_id"], float(AUX_FILTER.get("daytrade_percentile") or 0.50)
            )
        else:
            self._daytrade_state = None
        self._push_dir = _push_dir(self._day)

    def _hard_exclude(self) -> frozenset[str]:
        return BASE_HARD_EXCLUDE | frozenset(AUX_FILTER.get("hard_exclude_extra") or [])

    def _aux_fail(self, ev: dict[str, Any]) -> bool:
        sym = str(ev.get("symbol") or "")
        if AUX_FILTER.get("price_risk_universe") and self._universe_syms and sym not in self._universe_syms:
            return True
        if AUX_FILTER.get("price_risk_guard") and self._price_guard.check(ev).blocked:
            return True
        if self._daytrade_state is not None and self._daytrade_state.check(ev).blocked:
            return True
        return False

    def _flush(self) -> None:
        if not self._pending:
            return
        for item in sorted(self._pending, key=lambda x: int(self.p270._float(x[0].get("message_index")) or 0)):
            self._try_open(item)
        self._pending = []

    def _try_open(self, item: tuple[dict[str, Any], int, list[str]]) -> None:
        ev, score, tokens = item
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = self.p270._float(ev.get("current_price")) or self.p270._float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return
        if score < self.score_min:
            return
        if "Momentum:low" not in tokens:
            return
        if self._aux_fail(ev):
            return
        if sym in self.active:
            return
        if len(self.active) >= MAX_POS:
            return
        ts = self.p71._parse_ts(ent)
        vwap = _lookup_vwap(_load_vwap_series_one(self._push_dir, sym), ts)
        dev = _entry_vwap_dev_pct(float(px), vwap)
        self.active[sym] = PathTrade(
            symbol=sym,
            day=self._day,
            session_id=self._session_id,
            stream=self._stream,
            entry_time=ent,
            entry_price=float(px),
            entry_vwap_dev_pct=dev,
            prices=[float(px)],
        )
        self.p71._components(self.sym_states.setdefault(sym, self.p71.SymState()), ts=ts, price=float(px), ev=ev)

    def _pool_ok(self, ev: dict[str, Any]) -> bool:
        et = str(ev.get("event_type") or "")
        if et == "accepted":
            return True
        if et == "rejected":
            return str(ev.get("gate_reject_reason") or "") not in self._hard_exclude()
        return False

    def on_row(self, ev: dict[str, Any]) -> None:
        self._ring.observe(ev, self.p270)
        et = str(ev.get("event_type") or "")
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = self.p270._float(ev.get("current_price")) or 0.0
        ev_time = str(ev.get("event_time") or "")
        if self._pending_time is None:
            self._pending_time = ev_time
        if ev_time != self._pending_time:
            self._flush()
            self._pending_time = ev_time
        if et == "candidate" and sym in self.active and px > 0:
            self.active[sym].prices.append(float(px))
            ts = self.p71._parse_ts(ent)
            self.p71._components(self.sym_states.setdefault(sym, self.p71.SymState()), ts=ts, price=float(px), ev=ev)
        elif self._pool_ok(ev):
            score, tokens = self.p318._compute_score(
                ev, score_points=self.score_points, ring=self._ring, p270=self.p270
            )
            self._pending.append((ev, score, tokens))

    def finalize(self, session_end: str) -> None:
        self._flush()
        close_reason = (
            "morning_session_close"
            if self._stream == "am"
            else "afternoon_session_close"
            if self._stream == "pm"
            else "session_close"
        )
        for act in self.active.values():
            if act.prices:
                act.prices.append(act.prices[-1])
            self.completed_paths.append(act)
        self.active.clear()


def _discover_sessions(p270: Any, p318: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return p318._discover_sessions(p270)


def _load_checkpoint_trades() -> list[dict[str, Any]]:
    if not PHASE318_CK.is_file():
        return []
    data = json.loads(PHASE318_CK.read_text(encoding="utf-8"))
    return list(data.get("trades") or [])


def _price_match(a: float, b: float) -> bool:
    tol = max(0.05, abs(a) * 0.0005)
    return abs(a - b) <= tol


def _build_path_from_session(
    *,
    day: str,
    symbol: str,
    entry_price: float,
    session_id: str,
    stream: str,
    events: list[dict[str, Any]],
    session_end_ts: float,
    used_entry_keys: set[tuple[str, str, float, str]],
) -> Optional[PathTrade]:
    entry_time = ""
    entry_ts = 0.0
    for ev in events:
        if str(ev.get("event_type") or "") != "accepted":
            continue
        if str(ev.get("symbol") or "") != symbol:
            continue
        px = _float(ev.get("current_price")) or _float(ev.get("entry_price")) or 0.0
        if not _price_match(px, entry_price):
            continue
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        key = (day, symbol, round(entry_price, 4), ent)
        if key in used_entry_keys:
            continue
        entry_time = ent
        entry_ts = _parse_ts(ent)
        used_entry_keys.add(key)
        break
    if not entry_time or entry_ts <= 0:
        return None

    prices = [float(entry_price)]
    for ev in events:
        if str(ev.get("event_type") or "") != "candidate":
            continue
        if str(ev.get("symbol") or "") != symbol:
            continue
        ts = _parse_ts(str(ev.get("entry_time") or ev.get("event_time") or ""))
        if ts < entry_ts or ts > session_end_ts:
            continue
        px = _float(ev.get("current_price"))
        if px is not None and px > 0:
            prices.append(float(px))
    if len(prices) < 2 and prices:
        prices.append(prices[-1])

    vwap = _lookup_vwap(_load_vwap_series_one(_push_dir(day), symbol), entry_ts)
    dev = _entry_vwap_dev_pct(entry_price, vwap)
    return PathTrade(
        symbol=symbol,
        day=day,
        session_id=session_id,
        stream=stream,
        entry_time=entry_time,
        entry_price=float(entry_price),
        entry_vwap_dev_pct=dev,
        prices=prices,
    )


def _collect_paths_from_checkpoint(
    p270: Any,
    p318: Any,
    p71: Any,
    checkpoint: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> tuple[list[PathTrade], list[dict[str, Any]]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in sessions:
        by_day[s["day"]].append(s)

    event_cache: dict[str, list[dict[str, Any]]] = {}
    end_cache: dict[str, float] = {}
    stream_cache: dict[str, str] = {}
    used_keys: set[tuple[str, str, float, str]] = set()
    paths: list[PathTrade] = []
    unmatched: list[dict[str, Any]] = []

    trade_days = sorted({str(t.get("day") or "") for t in checkpoint})
    for day in trade_days:
        for meta in by_day.get(day, []):
            sid = meta["session_id"]
            if sid not in event_cache:
                events = p270._load_events(SMALL_PAPER / sid)
                event_cache[sid] = events
                end_cache[sid] = _parse_ts(p71._session_end(events))
                try:
                    summary = json.loads(
                        (SMALL_PAPER / sid / "small_paper_summary.json").read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    summary = {}
                stream_cache[sid] = p270._session_stream(sid, summary)

    for t in checkpoint:
        day = str(t.get("day") or "")
        sym = str(t.get("symbol") or "")
        entry_px = float(t.get("entry_price") or 0.0)
        found: Optional[PathTrade] = None
        for meta in by_day.get(day, []):
            sid = meta["session_id"]
            events = event_cache.get(sid, [])
            if not events:
                continue
            path = _build_path_from_session(
                day=day,
                symbol=sym,
                entry_price=entry_px,
                session_id=sid,
                stream=stream_cache.get(sid, "unknown"),
                events=events,
                session_end_ts=end_cache.get(sid, 0.0),
                used_entry_keys=used_keys,
            )
            if path is not None:
                found = path
                break
        if found is None:
            unmatched.append(t)
        else:
            paths.append(found)

    return paths, unmatched


def _pf_yen(yens: list[float]) -> Optional[float]:
    wins = [y for y in yens if y > 0]
    losses = [y for y in yens if y < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not sum(wins) else float("inf")
    return round(sum(wins) / gl, 4)


def _summarize_exits(trades: list[ExitTrade], baseline: Optional[list[ExitTrade]] = None) -> dict[str, Any]:
    yens = [t.pnl_yen_100 for t in trades]
    pnls = [t.pnl_pct for t in trades]
    n = len(trades)
    exit_counts = Counter(t.exit_reason for t in trades)
    base_by_key = {}
    if baseline:
        base_by_key = {(t.day, t.symbol, t.entry_price): t for t in baseline}

    premature = 0
    premature_yen = 0.0
    trailing_eroded = 0
    trailing_eroded_yen = 0.0
    for t in trades:
        key = (t.day, t.symbol, t.entry_price)
        b = base_by_key.get(key)
        if not b:
            continue
        delta = t.pnl_yen_100 - b.pnl_yen_100
        if delta < -0.01 and t.exit_reason == "vwap_contraction_exit":
            premature += 1
            premature_yen += delta
        if b.exit_reason == "trailing_mfe_exit" and delta < -0.01:
            trailing_eroded += 1
            trailing_eroded_yen += delta

    return {
        "trade_count": n,
        "total_pnl_yen_100": round(sum(yens), 2) if yens else 0.0,
        "avg_pnl_yen_100": round(statistics.mean(yens), 2) if yens else None,
        "profit_factor_yen_100": _pf_yen(yens),
        "win_rate": round(sum(1 for y in yens if y > 0) / n, 4) if n else None,
        "total_pnl_pct": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl_pct": round(statistics.mean(pnls), 6) if pnls else None,
        "exit_reason_counts": dict(sorted(exit_counts.items())),
        "stop_hit_count": int(exit_counts.get("stop_hit", 0)),
        "trailing_mfe_exit_count": int(exit_counts.get("trailing_mfe_exit", 0)),
        "vwap_contraction_exit_count": int(exit_counts.get("vwap_contraction_exit", 0)),
        "stop_hit_reduction_vs_A": (
            int(Counter(b.exit_reason for b in baseline).get("stop_hit", 0))
            - int(exit_counts.get("stop_hit", 0))
            if baseline
            else None
        ),
        "trailing_mfe_exit_reduction_vs_A": (
            int(Counter(b.exit_reason for b in baseline).get("trailing_mfe_exit", 0))
            - int(exit_counts.get("trailing_mfe_exit", 0))
            if baseline
            else None
        ),
        "premature_profit_taking_count": premature,
        "premature_profit_taking_yen_100": round(premature_yen, 2),
        "existing_trailing_mfe_exit_eroded_count": trailing_eroded,
        "existing_trailing_mfe_exit_eroded_yen_100": round(trailing_eroded_yen, 2),
        "entry_vwap_dev_available_count": sum(1 for t in trades if t.entry_vwap_dev_pct is not None),
    }


def _group_pnl(trades: list[ExitTrade], key_fn: Any) -> list[dict[str, Any]]:
    groups: dict[str, list[ExitTrade]] = defaultdict(list)
    for t in trades:
        groups[str(key_fn(t))].append(t)
    rows = []
    for k, grp in sorted(groups.items()):
        yens = [t.pnl_yen_100 for t in grp]
        rows.append(
            {
                "key": k,
                "trade_count": len(grp),
                "total_pnl_yen_100": round(sum(yens), 2),
                "avg_pnl_yen_100": round(statistics.mean(yens), 2),
            }
        )
    return rows


def _concentration_check(
    paths: list[PathTrade],
    exits_a: list[ExitTrade],
    exits_b: list[ExitTrade],
) -> dict[str, Any]:
    delta_by_day: dict[str, float] = defaultdict(float)
    delta_by_sym: dict[str, float] = defaultdict(float)
    for a, b in zip(exits_a, exits_b):
        d = b.pnl_yen_100 - a.pnl_yen_100
        delta_by_day[a.day] += d
        delta_by_sym[a.symbol] += d
    total_delta = sum(delta_by_day.values())
    if abs(total_delta) < 1e-6:
        return {"single_day_dominance": None, "single_symbol_dominance": None}
    top_day = max(delta_by_day.items(), key=lambda kv: abs(kv[1]))
    top_sym = max(delta_by_sym.items(), key=lambda kv: abs(kv[1]))
    return {
        "improvement_by_day": dict(sorted(delta_by_day.items())),
        "improvement_by_symbol": dict(sorted(delta_by_sym.items(), key=lambda kv: -abs(kv[1]))[:15]),
        "top_day_share_of_improvement": round(top_day[1] / total_delta, 4) if total_delta else None,
        "top_symbol_share_of_improvement": round(top_sym[1] / total_delta, 4) if total_delta else None,
        "single_day_dominance": abs(top_day[1] / total_delta) > 0.8 if total_delta else False,
        "single_symbol_dominance": abs(top_sym[1] / total_delta) > 0.5 if total_delta else False,
    }


def _verdict(metrics_a: dict[str, Any], metrics_b: dict[str, Any], conc: dict[str, Any]) -> dict[str, Any]:
    yen_up = float(metrics_b["total_pnl_yen_100"]) > float(metrics_a["total_pnl_yen_100"])
    pf_a = metrics_a.get("profit_factor_yen_100")
    pf_b = metrics_b.get("profit_factor_yen_100")
    pf_up = pf_b is not None and pf_a is not None and float(pf_b) > float(pf_a)
    stop_down = int(metrics_b["stop_hit_count"]) < int(metrics_a["stop_hit_count"])
    not_concentrated = not conc.get("single_day_dominance") and not conc.get("single_symbol_dominance")
    generalizes = yen_up and pf_up and stop_down and not_concentrated
    return {
        "vwap_contraction_generalizes": generalizes,
        "criteria": {
            "total_pnl_yen_100_improved": yen_up,
            "profit_factor_yen_100_improved": pf_up,
            "stop_hit_reduced": stop_down,
            "not_single_day_or_symbol_dominant": not_concentrated,
        },
        "delta_total_pnl_yen_100": round(
            float(metrics_b["total_pnl_yen_100"]) - float(metrics_a["total_pnl_yen_100"]), 2
        ),
        "conclusion": (
            "VWAP contraction generalizes across replay window"
            if generalizes
            else "VWAP contraction does not fully generalize — review concentration or metric deltas"
        ),
    }


def main() -> int:
    p270 = _bootstrap()
    p318 = _load_p318()
    p71 = _load_p71()

    sessions, skipped = _discover_sessions(p270, p318)
    checkpoint = _load_checkpoint_trades()
    if not checkpoint:
        print("missing phase318 checkpoint", file=sys.stderr)
        return 1

    print(
        f"sessions={len(sessions)} checkpoint_trades={len(checkpoint)} path extraction",
        flush=True,
    )
    paths, unmatched = _collect_paths_from_checkpoint(p270, p318, p71, checkpoint, sessions)
    print(f"paths={len(paths)} unmatched={len(unmatched)}", flush=True)
    close_reason_by_session = {
        (p.day, p.session_id): (
            "morning_session_close"
            if p.stream == "am"
            else "afternoon_session_close"
            if p.stream == "pm"
            else "session_close"
        )
        for p in paths
    }

    exits_a: list[ExitTrade] = []
    exits_b: list[ExitTrade] = []
    for p in paths:
        fb = close_reason_by_session.get((p.day, p.session_id), "session_close")
        exits_a.append(_simulate_exit(p, vwap_contraction=False, fallback_reason=fb))
        exits_b.append(_simulate_exit(p, vwap_contraction=True, fallback_reason=fb))

    metrics_a = _summarize_exits(exits_a)
    metrics_b = _summarize_exits(exits_b, baseline=exits_a)
    metrics_b["vs_scenario_A_total_pnl_yen_improvement"] = round(
        float(metrics_b["total_pnl_yen_100"]) - float(metrics_a["total_pnl_yen_100"]), 2
    )

    conc = _concentration_check(paths, exits_a, exits_b)
    verdict = _verdict(metrics_a, metrics_b, conc)

    report = {
        "phase": 328,
        "title": "vwap_contraction_full_replay_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "analysis only; Phase318 entry; trailing/stop fixed; VWAP contraction counterfactual",
        "replay_window": {"start": DATE_START, "end": DATE_END},
        "entry_logic": "Phase318 production (Momentum:low + Board:mid, score_min=3)",
        "exit_scenarios": {
            "A": {
                "label": "current_trailing_mfe",
                "hard_stop_pct": HARD_STOP_PCT,
                "trailing_mfe_activate_pct": TRAILING_ACTIVATE_PCT,
                "trailing_mfe_giveback_frac": GIVEBACK_FRAC,
            },
            "B": {
                "label": "vwap_contraction_added",
                "entry_vwap_dev_gt_pct": ENTRY_VWAP_DEV_MIN_PCT,
                "current_vwap_dev_lte_pct": CURRENT_VWAP_DEV_LTE_PCT,
                "exit_reason": "vwap_contraction_exit",
            },
        },
        "methodology": {
            "vwap_source": "kabu_native/data/push_jsonl/{YYYY-MM-DD}/{symbol}.jsonl payload.VWAP at entry",
            "entry_cohort": "Phase318 checkpoint (43 trades, production entry replay 20260518–20260603)",
            "path_collection": "candidate tick prices from matched accepted entry to session end",
            "exit_simulation": "stop → trailing_mfe → optional vwap_contraction → session_close fallback",
        },
        "sessions": {"count": len(sessions), "skipped": skipped},
        "phase318_checkpoint": {
            "path": str(PHASE318_CK.relative_to(REPO)).replace("\\", "/"),
            "trade_count": len(checkpoint),
            "matched_path_count": len(paths),
            "unmatched_count": len(unmatched),
        },
        "path_trade_count": len(paths),
        "scenario_A": metrics_a,
        "scenario_B": metrics_b,
        "breakdown": {
            "daily_pnl_A": _group_pnl(exits_a, lambda t: t.day),
            "daily_pnl_B": _group_pnl(exits_b, lambda t: t.day),
            "daily_pnl_delta_B_minus_A": _group_pnl(
                [
                    ExitTrade(
                        t.symbol,
                        t.day,
                        t.session_id,
                        t.stream,
                        t.entry_price,
                        t.exit_price,
                        t.pnl_pct,
                        b.pnl_yen_100 - a.pnl_yen_100,
                        "delta",
                        t.entry_vwap_dev_pct,
                    )
                    for a, b in zip(exits_a, exits_b)
                    for t in (a,)
                ],
                lambda t: t.day,
            ),
            "stream_pnl_A": _group_pnl(exits_a, lambda t: t.stream),
            "stream_pnl_B": _group_pnl(exits_b, lambda t: t.stream),
            "symbol_pnl_A": _group_pnl(exits_a, lambda t: t.symbol),
            "symbol_pnl_B": _group_pnl(exits_b, lambda t: t.symbol),
        },
        "concentration": conc,
        "verdict": verdict,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"paths={len(paths)} A_yen={metrics_a['total_pnl_yen_100']} B_yen={metrics_b['total_pnl_yen_100']} "
        f"generalizes={verdict['vwap_contraction_generalizes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
