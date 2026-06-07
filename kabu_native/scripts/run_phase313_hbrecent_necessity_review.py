#!/usr/bin/env python3
"""
Phase313 (review): HBRecent:no necessity — A (current) vs B (no HBRecent, min=3).

Output: phase313_hbrecent_necessity_review.json
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase313_hbrecent_necessity_review.json"
CHECKPOINT = REPO / "kabu_native/results/reports/phase313_hbrecent_necessity_review.checkpoint.json"

DATE_START = 20260518
DATE_END = 20260603
MAX_POS = 3
V1_MODE = "legacy"
V1_RATIO = 0.85
REJECT_REPLAY_MAX_EVENTS = 500_000
JST = ZoneInfo("Asia/Tokyo")

POINTS_A: dict[str, int] = {
    "HBRecent:no": 2,
    "Momentum:low": 2,
    "Board:mid": 1,
}
POINTS_B: dict[str, int] = {
    "Momentum:low": 2,
    "Board:mid": 1,
}

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


def _load_p71() -> Any:
    path = REPO / "kabu_native/scripts/run_phase71_split_momentum_fade_review.py"
    spec = importlib.util.spec_from_file_location("phase71_p313hb", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p313hb"] = mod
    spec.loader.exec_module(mod)
    return mod


def _day_from_sid(sid: str) -> Optional[str]:
    parts = sid.replace("\\", "/").split("/")
    if parts and len(parts[0]) == 8 and parts[0].isdigit():
        return parts[0]
    return None


def _day_in_range(day: str) -> bool:
    try:
        return DATE_START <= int(day) <= DATE_END
    except ValueError:
        return False


def _skip_session(sid: str, event_count: int) -> Optional[str]:
    low = sid.lower()
    if "phase282_discord_flow" in low:
        return "phase282_test_harness"
    if "phase284_resim" in low or "phase285_resim" in low:
        return "phase284_285_resim_harness"
    if event_count > REJECT_REPLAY_MAX_EVENTS:
        return f"event_count>{REJECT_REPLAY_MAX_EVENTS}"
    return None


def _discover_sessions(p270: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    found: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for summary_path in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
        sid = summary_path.parent.relative_to(SMALL_PAPER).as_posix()
        day = _day_from_sid(sid)
        if not day or not _day_in_range(day):
            continue
        events = p270._load_events(summary_path.parent)
        if not events:
            continue
        skip = _skip_session(sid, len(events))
        if skip:
            skipped.append({"session_id": sid, "day": day, "event_count": len(events), "reason": skip})
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        found.append({"session_id": sid, "day": day, "stream": p270._session_stream(sid, summary)})
    return found, skipped


@dataclass
class ScenarioConfig:
    scenario_id: str
    label: str
    score_points: dict[str, int]
    score_min: int
    require_momentum_low: bool


def _scenario_defs() -> dict[str, ScenarioConfig]:
    return {
        "A": ScenarioConfig("A", "現行 (HBRecent+Momentum+Board min=5)", dict(POINTS_A), 5, True),
        "B": ScenarioConfig("B", "HBRecent除外 (Momentum+Board min=3)", dict(POINTS_B), 3, True),
    }


class PriceRingTracker:
    def __init__(self) -> None:
        self.rings: dict[str, list[tuple[float, float]]] = {}

    def observe(self, ev: dict[str, Any], p270: Any) -> None:
        from small_paper.extended_entry_shadow import append_price_tick
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        if not sym or px <= 0 or not ent:
            return
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        append_price_tick(self.rings.setdefault(sym, []), ts=ts, px=px)

    def hbrecent(self, ev: dict[str, Any], p270: Any) -> bool:
        from small_paper.extended_entry_shadow import compute_entry_high_break_recent_field
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        if not sym or not ent:
            return False
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        return bool(
            compute_entry_high_break_recent_field(
                trade=ev,
                payload={"CurrentPrice": px},
                price_ring=self.rings.get(sym, []),
                entry_ts=ts,
            )["entry_high_break_recent"]
        )


def _board_from_event(ev: dict[str, Any]) -> Optional[float]:
    from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field

    payload: dict[str, Any] = {}
    for key in ("BidQty", "AskQty"):
        if ev.get(key) is not None:
            payload[key] = ev[key]
    if payload:
        return compute_entry_order_book_imbalance_field(payload=payload).get("entry_order_book_imbalance")
    logged = ev.get("entry_order_book_imbalance")
    if logged is None:
        return None
    try:
        return float(logged)
    except (TypeError, ValueError):
        return None


def _compute_score(
    ev: dict[str, Any],
    *,
    score_points: dict[str, int],
    ring: PriceRingTracker,
    p270: Any,
) -> tuple[int, list[str]]:
    from small_paper.entry_expectancy_score_shadow import _feature_token

    work = dict(ev)
    work["entry_high_break_recent"] = ring.hbrecent(work, p270)
    imb = _board_from_event(ev)
    if imb is not None:
        work["entry_order_book_imbalance"] = imb
    active: list[str] = []
    score = 0
    for token, pts in score_points.items():
        if pts <= 0:
            continue
        lbl = token.split(":", 1)[0]
        if lbl == "HBRecent":
            hb = work.get("entry_high_break_recent")
            if hb is None:
                continue
            tok = f"HBRecent:{'yes' if str(hb).lower() in ('true', '1', 'yes') else 'no'}"
        else:
            tok = _feature_token(lbl, work)
        if tok == token:
            active.append(token)
            score += pts
    return score, active


def _price_guard_state() -> Any:
    from small_paper.entry_price_risk_guard import EntryPriceRiskGuardConfig, EntryPriceRiskGuardState

    return EntryPriceRiskGuardState(
        config=EntryPriceRiskGuardConfig(
            enabled=True, min_entry_price=50.0, max_tick_ratio_pct=5.0, shadow_only=True
        )
    )


_DAYTRADE_CACHE: dict[tuple[str, float], Any] = {}


def _daytrade_state(p270: Any, session_id: str, percentile: float) -> Any:
    key = (session_id, round(float(percentile), 4))
    if key in _DAYTRADE_CACHE:
        return _DAYTRADE_CACHE[key]
    from small_paper.daytrade_suitability import percentile_value
    from small_paper.daytrade_suitability_gate import (
        DaytradeSuitabilityConfig,
        DaytradeSuitabilityState,
        discover_sessions_for_suitability_prior,
        prior_vol_liq_scores,
    )

    base = REPO / "kabu_native/results/small_paper"
    sources = discover_sessions_for_suitability_prior(base, before_session_key=session_id)
    scores, used = prior_vol_liq_scores(sources, repo_root=REPO)
    th = percentile_value(scores, percentile) if scores else None
    state = DaytradeSuitabilityState(
        config=DaytradeSuitabilityConfig(enabled=True),
        run_session_key=session_id,
        source_sessions=used,
        vol_liq_threshold=round(th, 6) if th is not None else None,
        prior_quality_trade_count=len(scores),
    )
    _DAYTRADE_CACHE[key] = state
    return state


@dataclass
class CompletedTrade:
    pnl_pct: float
    symbol: str
    day: str


class ScenarioSim:
    def __init__(self, cfg: ScenarioConfig, p71: Any, p270: Any):
        self.cfg = cfg
        self.p71 = p71
        self.p270 = p270
        self.sym_states: dict[str, Any] = {}
        self.active: dict[str, Any] = {}
        self.completed: list[CompletedTrade] = []
        self.max_concurrent_reject_count = 0
        self._pending_time: Optional[str] = None
        self._pending: list[tuple[dict[str, Any], int, list[str]]] = []
        self._day = ""
        self._universe_syms: set[str] = set()
        self._daytrade_state: Any = None
        self._price_guard = _price_guard_state()
        self._ring = PriceRingTracker()

    def _hard_exclude(self) -> frozenset[str]:
        return BASE_HARD_EXCLUDE | frozenset(AUX_FILTER.get("hard_exclude_extra") or [])

    def _aux_fail(self, ev: dict[str, Any]) -> Optional[str]:
        sym = str(ev.get("symbol") or "")
        if AUX_FILTER.get("price_risk_universe") and self._universe_syms and sym not in self._universe_syms:
            return "outside_price_risk_universe"
        if AUX_FILTER.get("price_risk_guard") and self._price_guard.check(ev).blocked:
            return "entry_price_risk_guard"
        if self._daytrade_state is not None and self._daytrade_state.check(ev).blocked:
            return "daytrade_suitability"
        return None

    def _try_open(self, item: tuple[dict[str, Any], int, list[str]]) -> None:
        ev, score, tokens = item
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = self.p270._float(ev.get("current_price")) or self.p270._float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return
        if score < self.cfg.score_min:
            return
        if self.cfg.require_momentum_low and "Momentum:low" not in tokens:
            return
        if self._aux_fail(ev):
            return
        if sym in self.active:
            return
        if len(self.active) >= MAX_POS:
            self.max_concurrent_reject_count += 1
            return
        ts = self.p71._parse_ts(ent)
        st = self.sym_states.setdefault(sym, self.p71.SymState())
        comps = self.p71._components(st, ts=ts, price=float(px), ev=ev)
        q = self.p270._float(ev.get("continuation_quality_score")) or 0.0
        tr = self.p71.StructuralTrade(sym, ent, float(px), float(q))
        act = self.p71.ActiveTrade(
            trade=tr,
            entry_ts=ts,
            rich_ticks=[
                {
                    "price": float(px),
                    "pnl_pct": 0.0,
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            ],
        )
        self.active[sym] = act

    def _close(self, act: Any, *, close_price: float) -> None:
        pnl = float(self.p71._pnl_pct(act.trade.entry_price, close_price))
        self.completed.append(CompletedTrade(pnl_pct=pnl, symbol=str(act.trade.symbol), day=self._day))

    def _flush(self) -> None:
        if not self._pending:
            return
        for item in sorted(self._pending, key=lambda x: int(self.p270._float(x[0].get("message_index")) or 0)):
            self._try_open(item)
        self._pending = []

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
        if et == "candidate":
            if sym not in self.active or px <= 0 or not ent:
                return
            ts = self.p71._parse_ts(ent)
            st = self.sym_states.setdefault(sym, self.p71.SymState())
            act = self.active[sym]
            comps = self.p71._components(st, ts=ts, price=float(px), ev=ev)
            act.rich_ticks.append(
                {
                    "price": float(px),
                    "pnl_pct": self.p71._pnl_pct(act.trade.entry_price, float(px)),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = self.p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=V1_MODE,
                ratio=V1_RATIO,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                self._close(act, close_price=float(px))
                self.active.pop(sym, None)
        elif self._pool_ok(ev):
            score, tokens = _compute_score(
                ev, score_points=self.cfg.score_points, ring=self._ring, p270=self.p270
            )
            self._pending.append((ev, score, tokens))

    def finalize(self, session_end: str) -> None:
        self._flush()
        for _, act in list(self.active.items()):
            last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
            self._close(act, close_price=float(last_px))
        self.active.clear()

    def begin_session(self, meta: dict[str, Any]) -> None:
        self._day = meta["day"]
        self.sym_states = {}
        self.active = {}
        self._pending = []
        self._pending_time = None
        self._ring = PriceRingTracker()
        self._universe_syms = (
            self.p270._load_universe_symbols(self._day, price_risk=True)
            if AUX_FILTER.get("price_risk_universe")
            else set()
        )
        if AUX_FILTER.get("daytrade_mode") == "on":
            self._daytrade_state = _daytrade_state(
                self.p270, meta["session_id"], float(AUX_FILTER.get("daytrade_percentile") or 0.50)
            )
        else:
            self._daytrade_state = None


def _pf(pnls: list[float]) -> Any:
    wins = sum(p for p in pnls if p > 0)
    loss = abs(sum(p for p in pnls if p < 0))
    if loss <= 0:
        return None if wins <= 0 else "inf"
    return round(wins / loss, 4)


def _metrics(trades: list[CompletedTrade], reject_mc: int) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    n = len(trades)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "win_rate": None,
            "max_concurrent_reject_count": reject_mc,
        }
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trade_count": n,
        "profit_factor": _pf(pnls),
        "total_pnl_pct": round(sum(pnls), 4),
        "win_rate": round(wins / n, 4),
        "max_concurrent_reject_count": reject_mc,
    }


def _zero_trade_rates(calendar_days: list[str], daily: dict[str, dict[str, int]], sid: str) -> dict[str, Any]:
    z = sum(1 for d in calendar_days if daily.get(d, {}).get(sid, 0) == 0)
    return {
        "zero_trade_days": z,
        "calendar_days": len(calendar_days),
        "zero_trade_day_rate": round(z / len(calendar_days), 4) if calendar_days else None,
    }


def _pf_num(pf: Any) -> float:
    if pf is None:
        return -1.0
    if pf == "inf":
        return 99.0
    try:
        return float(pf)
    except (TypeError, ValueError):
        return -1.0


def _verdict(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    a_pf, b_pf = _pf_num(a.get("profit_factor")), _pf_num(b.get("profit_factor"))
    a_pnl, b_pnl = float(a.get("total_pnl_pct") or 0), float(b.get("total_pnl_pct") or 0)
    a_better = a_pf > b_pf and a_pnl > b_pnl
    tie_close = abs(a_pnl - b_pnl) < 0.5 and abs(a_pf - b_pf) < 0.1
    required = a_better or (tie_close and int(a.get("trade_count") or 0) <= int(b.get("trade_count") or 0))
    return {
        "HBRecent_required": required,
        "rationale": [
            f"A PF={a.get('profit_factor')} PnL={a_pnl} tc={a.get('trade_count')}",
            f"B PF={b.get('profit_factor')} PnL={b_pnl} tc={b.get('trade_count')}",
            "HBRecent_required=true when A (with HBRecent:no) clearly beats B on PF and PnL, or ties with fewer/equal trades.",
        ],
    }


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions, skipped = _discover_sessions(p270)
    defs = _scenario_defs()
    calendar_days = sorted({s["day"] for s in sessions})

    overall: dict[str, dict[str, Any]] = {}
    daily_trades: dict[str, dict[str, int]] = defaultdict(dict)

    if CHECKPOINT.is_file():
        try:
            ck = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
            overall.update(ck.get("overall") or {})
            daily_trades = defaultdict(dict, ck.get("daily_trades") or {})
            print("loaded checkpoint", flush=True)
        except (OSError, json.JSONDecodeError):
            pass

    need_replay = [sid for sid in ("A", "B") if sid not in overall]
    if need_replay:
        sims = {sid: ScenarioSim(defs[sid], p71, p270) for sid in need_replay}
        print(f"replay scenarios={need_replay}", flush=True)
        for i, meta in enumerate(sessions, 1):
            events = p270._load_events(SMALL_PAPER / meta["session_id"])
            if not events:
                continue
            session_end = p71._session_end(events)
            ordered = sorted(
                events,
                key=lambda e: (
                    p270._parse_ts(str(e.get("event_time") or "")),
                    int(p270._float(e.get("message_index")) or 0),
                ),
            )
            for sim in sims.values():
                sim.begin_session(meta)
            for ev in ordered:
                for sim in sims.values():
                    sim.on_row(ev)
            for sim in sims.values():
                sim.finalize(session_end)
            if i % 5 == 0 or i == len(sessions):
                print(f"  [{i}/{len(sessions)}]", flush=True)
        for sid, sim in sims.items():
            overall[sid] = _metrics(sim.completed, sim.max_concurrent_reject_count)
            for day in calendar_days:
                daily_trades[day][sid] = sum(1 for t in sim.completed if t.day == day)
        CHECKPOINT.write_text(
            json.dumps({"overall": overall, "daily_trades": dict(daily_trades)}, indent=2),
            encoding="utf-8",
        )

    for sid in ("A", "B"):
        if sid in overall:
            zr = _zero_trade_rates(calendar_days, daily_trades, sid)
            overall[sid]["zero_trade_day_rate"] = zr["zero_trade_day_rate"]
            overall[sid]["zero_trade_days"] = zr["zero_trade_days"]

    verdict = _verdict(overall.get("A", {}), overall.get("B", {}))

    report = {
        "phase": "313-hbrecent-review",
        "title": "hbrecent_necessity_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "review only; no production changes",
        "replay_window": {"start": DATE_START, "end": DATE_END},
        "fixed_conditions": {
            "daytrade": True,
            "price_risk": True,
            "hbrecent_pregate": True,
            "board_pregate": True,
            "momentum_low_required": True,
        },
        "scenarios": {
            sid: {
                "id": sid,
                "label": defs[sid].label,
                "score_points": defs[sid].score_points,
                "entry_score_min": defs[sid].score_min,
            }
            for sid in ("A", "B")
        },
        "sessions": {"count": len(sessions), "skipped": skipped},
        "comparison": {sid: overall[sid] for sid in ("A", "B") if sid in overall},
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    a, b = overall.get("A", {}), overall.get("B", {})
    print(
        f"A tc={a.get('trade_count')} PF={a.get('profit_factor')} PnL={a.get('total_pnl_pct')} | "
        f"B tc={b.get('trade_count')} PF={b.get('profit_factor')} PnL={b.get('total_pnl_pct')} | "
        f"HBRecent_required={verdict['HBRecent_required']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
