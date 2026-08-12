"""V1R-native PAPER_PRIMARY ENTRY live runtime (emergency decontamination).

Reuses frozen SoT only — no new ENTRY logic:
  PASSIVE_FILL_ENTRY_V1 / NEUTRAL_FIXED_CLOCK_ANCHOR_V1 /
  allocator model / PASSIVE_BID_CONSERVATIVE / simulate_joint.

PBv2 must NEVER call into this module's Primary occupancy.
Primary fills reach dual-lane ONLY via admit_v1r_fill().
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized
from research.e1_x37_prospective.freeze import load_model_artifact, verify_model_identity
from small_paper.v1r_live_dual_lane import get_dual_lane, live_primary_enabled
from small_paper.v1r_primary_runtime import (
    ANCHOR_SHA,
    BOARD_FRESHNESS_SEC_V1R,
    CLOCK_GRID,
    LOT_QTY,
    MODEL_ARTIFACT_SHA,
    POSITION_CAP,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    WAIT_SEC,
)

JST = ZoneInfo("Asia/Tokyo")
ENTRY_SHA = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
EXEC_SHA = "040fa4b061e575d3f6cdb2a11ffd3f862da5351b298567b31363de923a590869"
FEATURE_ORDER = (
    "spread_bps",
    "imbalance",
    "mid_ret_60s",
    "mid_ret_180s",
    "event_rate_60s",
    "log_bid_qty",
)
NATIVE_ROOT = Path(__file__).resolve().parents[2]

_ENGINE: Optional["V1RNativeEntryLive"] = None


def _now() -> datetime:
    return datetime.now(JST)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_iso_epoch(v: Any) -> Optional[float]:
    """Parse ISO / epoch into JST-aware unix seconds. None if unavailable."""
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            x = float(v)
            return x if x == x else None
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST).timestamp()
    except Exception:
        return None


def board_event_epoch_from_payload(
    payload: Mapping[str, Any] | dict[str, Any] | None,
    *,
    fallback: Optional[float] = None,
) -> float:
    """Causal board/event clock for Passive Fill (Frozen board.t axis).

    Preference matches Capture → research load_board_events recorded_at lineage:
      recorded_at / received_at / __ingress_received_at__
    Never prefer consumer wall clock when an ingress/Capture stamp exists.
    """
    pay = payload or {}
    for key in (
        "recorded_at",
        "received_at",
        "__ingress_received_at__",
        "__ingress_event_time__",
        "event_time",
    ):
        ts = _parse_iso_epoch(pay.get(key))
        if ts is not None:
            return float(ts)
    if fallback is not None:
        return float(fallback)
    return float(time.time())


def extract_board_row(payload: dict[str, Any], event_t: float) -> dict[str, Any]:
    """Board row matching e1_x28 load_board_events quote contract (not a new fill rule)."""
    b1 = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else {}
    s1 = payload.get("Sell1") if isinstance(payload.get("Sell1"), dict) else {}
    bid = _f(b1.get("Price")) if b1 else _f(payload.get("BidPrice"))
    ask = _f(s1.get("Price")) if s1 else _f(payload.get("AskPrice"))
    bq = _f(b1.get("Qty")) if b1 else _f(payload.get("BidQty"))
    aq = _f(s1.get("Qty")) if s1 else _f(payload.get("AskQty"))
    sq = payload.get("SpecialQuote")
    if sq is None:
        sq = payload.get("special_quote")
    special = bool(sq) and str(sq) not in ("", "0", "None", "null", "False", "false")
    # X28: non-positive qty is treated as special (not fill evidence)
    if aq is not None and aq <= 0:
        special = True
    if bq is not None and bq <= 0:
        special = True
    # Freshness vs quote clock — same as load_board_events (recv - CurrentPriceTime)
    fresh = _f(payload.get("board_age_sec"))
    if fresh is None:
        fresh = _f(payload.get("fresh_sec"))
    if fresh is None:
        qt = (
            _parse_iso_epoch(payload.get("CurrentPriceTime"))
            or _parse_iso_epoch(payload.get("AskTime"))
            or _parse_iso_epoch(payload.get("BidTime"))
        )
        fresh = float(event_t - qt) if qt is not None else 0.0
    return {
        "t": float(event_t),
        "bid": bid if bid is not None else float("nan"),
        "ask": ask if ask is not None else float("nan"),
        "bid_qty": bq if bq is not None else float("nan"),
        "ask_qty": aq if aq is not None else float("nan"),
        "special": bool(special),
        "fresh_sec": float(fresh),
    }


@dataclass
class PendingOrder:
    symbol: str
    signal_time: float
    limit_price: float
    score: float
    rank: int
    anchor: str
    session: str
    date: str
    features: dict[str, Any] = field(default_factory=dict)
    notified_entry: bool = False


# Independent SHADOW_ONLY occupancy (classic cap_pbv2 default); never Arch E CAP.
PBV2_SHADOW_CAP_DEFAULT = 4


@dataclass
class ShadowPBv2State:
    """Independent SHADOW_ONLY ledger — never mutates Primary cap/open/pending."""

    accepts: int = 0
    exits: int = 0
    blocked: int = 0
    cap: int = PBV2_SHADOW_CAP_DEFAULT
    open_symbols: set[str] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def open_n(self) -> int:
        return len(self.open_symbols)

    def note_accept(self, *, symbol: str, entry_price: float, entry_time: str) -> dict[str, Any]:
        sym = str(symbol).replace(".T", "")
        if sym in self.open_symbols:
            self.events.append({
                "kind": "PBV2_SHADOW_ACCEPT_DUP",
                "symbol": sym,
                "entry_price": entry_price,
                "entry_time": entry_time,
                "role": "SHADOW_ONLY",
                "admitted": False,
                "reason": "already_open",
            })
            return {
                "admitted": False,
                "reason": "already_open",
                "open_n": self.open_n,
                "cap": self.cap,
            }
        if self.open_n >= int(self.cap):
            self.blocked += 1
            self.events.append({
                "kind": "PBV2_SHADOW_CAP_BLOCKED",
                "symbol": sym,
                "entry_price": entry_price,
                "entry_time": entry_time,
                "role": "SHADOW_ONLY",
                "admitted": False,
                "reason": "shadow_cap",
                "open_n": self.open_n,
                "cap": self.cap,
            })
            return {
                "admitted": False,
                "reason": "shadow_cap",
                "open_n": self.open_n,
                "cap": self.cap,
            }
        self.accepts += 1
        self.open_symbols.add(sym)
        self.events.append({
            "kind": "PBV2_SHADOW_ACCEPT",
            "symbol": sym,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "role": "SHADOW_ONLY",
            "admitted": True,
            "open_n": self.open_n,
            "cap": self.cap,
        })
        return {
            "admitted": True,
            "reason": "",
            "open_n": self.open_n,
            "cap": self.cap,
        }

    def note_exit(self, *, symbol: str, exit_time: str = "", exit_reason: str = "") -> dict[str, Any]:
        sym = str(symbol).replace(".T", "")
        if sym not in self.open_symbols:
            return {"closed": False, "reason": "not_open", "open_n": self.open_n}
        self.open_symbols.discard(sym)
        self.exits += 1
        self.events.append({
            "kind": "PBV2_SHADOW_EXIT",
            "symbol": sym,
            "exit_time": exit_time,
            "exit_reason": exit_reason,
            "role": "SHADOW_ONLY",
            "open_n": self.open_n,
        })
        return {"closed": True, "reason": "", "open_n": self.open_n}

    def snapshot(self) -> dict[str, Any]:
        return {
            "role": "SHADOW_ONLY",
            "accepts": self.accepts,
            "exits": self.exits,
            "blocked": self.blocked,
            "cap": self.cap,
            "open_n": self.open_n,
            "open_symbols": sorted(self.open_symbols),
            "affects_arch_e_occupancy": False,
            "affects_dual_primary": False,
        }


@dataclass
class V1RNativeEntryLive:
    """Live V1R-native Primary ENTRY: anchor → score → admit → pending → passive fill."""

    universe: list[str]
    score_fn: Callable[[dict], float]
    model_ser: dict[str, Any]
    trace_dir: Optional[Path] = None
    boards: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    pending: dict[str, PendingOrder] = field(default_factory=dict)
    open_symbols: set[str] = field(default_factory=set)
    fired_anchors: set[str] = field(default_factory=set)
    shadow_pbv2: ShadowPBv2State = field(default_factory=ShadowPBv2State)
    events: list[dict[str, Any]] = field(default_factory=list)
    notify_sink: list[dict[str, Any]] = field(default_factory=list)
    ready: bool = True
    fail_reason: str = ""
    universe_source: str = ""
    primary_fills: int = 0
    primary_expired: int = 0
    primary_admitted: int = 0
    anchor_fires: int = 0
    last_anchor: Optional[str] = None
    trading_date: str = ""

    @property
    def open_n(self) -> int:
        return len(self.open_symbols)

    @property
    def pending_n(self) -> int:
        return len(self.pending)

    def exposure(self) -> int:
        return self.open_n + self.pending_n

    def identity(self) -> dict[str, Any]:
        return {
            "entry": "PASSIVE_FILL_ENTRY_V1",
            "entry_sha": ENTRY_SHA,
            "anchor": "NEUTRAL_FIXED_CLOCK_ANCHOR_V1",
            "anchor_sha": ANCHOR_SHA,
            "model_sha": MODEL_ARTIFACT_SHA,
            "exec": "PASSIVE_BID_CONSERVATIVE",
            "exec_sha": EXEC_SHA,
            "universe_contract": UNIVERSE_CONTRACT,
            "universe_binding_sha": UNIVERSE_BINDING_SHA,
            "cap": POSITION_CAP,
            "wait_sec": WAIT_SEC,
            "freshness_sec": BOARD_FRESHNESS_SEC_V1R,
            "qty": LOT_QTY,
            "primary_role": "PAPER_PRIMARY",
            "pbv2_role": "SHADOW_ONLY",
            "submit_cancel_live": "0/0/0",
        }

    def _board_arrays(self, symbol: str) -> dict[str, np.ndarray]:
        rows = self.boards.get(symbol) or []
        if not rows:
            return {
                "t": np.asarray([], dtype=float),
                "bid": np.asarray([], dtype=float),
                "ask": np.asarray([], dtype=float),
                "bid_qty": np.asarray([], dtype=float),
                "ask_qty": np.asarray([], dtype=float),
                "special": np.asarray([], dtype=bool),
                "fresh_sec": np.asarray([], dtype=float),
            }
        return {
            "t": np.asarray([r["t"] for r in rows], dtype=float),
            "bid": np.asarray([r["bid"] for r in rows], dtype=float),
            "ask": np.asarray([r["ask"] for r in rows], dtype=float),
            "bid_qty": np.asarray([r["bid_qty"] for r in rows], dtype=float),
            "ask_qty": np.asarray([r["ask_qty"] for r in rows], dtype=float),
            "special": np.asarray([r["special"] for r in rows], dtype=bool),
            "fresh_sec": np.asarray([r["fresh_sec"] for r in rows], dtype=float),
        }

    def ingest_push(self, *, symbol: str, payload: dict[str, Any], event_t: Optional[float] = None) -> None:
        sym = str(symbol).replace(".T", "")
        if self.universe and sym not in self.universe and f"{sym}.T" not in self.universe:
            # still keep board if already pending/open
            if sym not in self.pending and sym not in self.open_symbols and sym not in self.boards:
                return
        # Board.t = causal Capture/ingress recorded_at axis (Frozen load_board_events).
        # Do NOT stamp consumer wall clock when payload carries ingress received_at.
        t = float(
            event_t
            if event_t is not None
            else board_event_epoch_from_payload(payload)
        )
        row = extract_board_row(payload, t)
        self.boards.setdefault(sym, []).append(row)
        # trim memory
        if len(self.boards[sym]) > 5000:
            self.boards[sym] = self.boards[sym][-4000:]

    def note_pbv2_shadow_accept(
        self, *, symbol: str, entry_price: float, entry_time: str
    ) -> dict[str, Any]:
        """SHADOW_ONLY — must not touch Primary open/pending/cap/dual."""
        before = {
            "open": self.open_n,
            "pending": self.pending_n,
            "exposure": self.exposure(),
            "primary_fills": self.primary_fills,
        }
        shadow = self.shadow_pbv2.note_accept(
            symbol=str(symbol).replace(".T", ""),
            entry_price=float(entry_price),
            entry_time=str(entry_time),
        )
        after = {
            "open": self.open_n,
            "pending": self.pending_n,
            "exposure": self.exposure(),
            "primary_fills": self.primary_fills,
        }
        assert before == after, "PBv2 shadow mutated Primary occupancy"
        # Defense: dual Primary must stay untouched even if caller also tried admit.
        dual = get_dual_lane(trace_dir=self.trace_dir) if live_primary_enabled() else None
        dual_open = int(dual.open_n("primary")) if dual is not None else 0
        return {
            "shadow": True,
            "primary_unchanged": True,
            "before": before,
            "after": after,
            "shadow_admit": shadow,
            "dual_primary_open": dual_open,
            "affects_arch_e_occupancy": False,
        }

    def note_pbv2_shadow_exit(
        self, *, symbol: str, exit_time: str = "", exit_reason: str = ""
    ) -> dict[str, Any]:
        """Close SHADOW_ONLY occupancy only — never Primary."""
        before = {
            "open": self.open_n,
            "pending": self.pending_n,
            "exposure": self.exposure(),
            "primary_fills": self.primary_fills,
        }
        shadow = self.shadow_pbv2.note_exit(
            symbol=str(symbol),
            exit_time=str(exit_time or ""),
            exit_reason=str(exit_reason or ""),
        )
        after = {
            "open": self.open_n,
            "pending": self.pending_n,
            "exposure": self.exposure(),
            "primary_fills": self.primary_fills,
        }
        assert before == after, "PBv2 shadow exit mutated Primary occupancy"
        return {
            "shadow": True,
            "primary_unchanged": True,
            "shadow_exit": shadow,
            "affects_arch_e_occupancy": False,
        }

    def _anchor_key(self, dt: datetime) -> Optional[str]:
        hm = (dt.hour, dt.minute)
        if hm not in CLOCK_GRID:
            return None
        return f"{dt.strftime('%Y%m%d')}|{dt.hour:02d}:{dt.minute:02d}"

    def maybe_fire_anchor(self, *, now_t: Optional[float] = None) -> list[dict[str, Any]]:
        """Fire at most once per clock grid minute when wall-clock matches."""
        dt = _now() if now_t is None else datetime.fromtimestamp(float(now_t), JST)
        key = self._anchor_key(dt)
        if key is None or key in self.fired_anchors:
            return []
        # only fire in the first 2s of the minute to avoid late decisions
        if dt.second > 2 and now_t is None:
            return []
        self.fired_anchors.add(key)
        anchor = f"{dt.hour:02d}:{dt.minute:02d}"
        self.last_anchor = anchor
        self.anchor_fires += 1
        t0 = dt.replace(second=0, microsecond=0).timestamp()
        day = dt.strftime("%Y%m%d")
        self.trading_date = day
        return self._run_anchor(anchor=anchor, t0=t0, day=day, session="AM" if dt.hour < 12 else "PM")

    def fire_anchor_at(
        self, *, anchor: str, t0: float, day: str, session: str = "AM"
    ) -> list[dict[str, Any]]:
        """Deterministic anchor fire for demos/tests (bypasses wall-clock gate)."""
        key = f"{day}|{anchor}"
        if key in self.fired_anchors:
            return []
        self.fired_anchors.add(key)
        self.last_anchor = anchor
        self.anchor_fires += 1
        self.trading_date = day
        return self._run_anchor(anchor=anchor, t0=float(t0), day=day, session=session)

    def _run_anchor(
        self, *, anchor: str, t0: float, day: str, session: str
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for sym in list(self.universe):
            s = str(sym).replace(".T", "")
            board = self._board_arrays(s)
            if board["t"].size == 0:
                continue
            feats = preentry_from_board(board, t0)
            if any(feats.get(f) is None or not np.isfinite(feats.get(f)) for f in FEATURE_ORDER):
                continue
            score = float(self.score_fn(feats))
            if not np.isfinite(score):
                continue
            i = int(np.searchsorted(board["t"], t0, side="right") - 1)
            if i < 0:
                continue
            limit = float(board["bid"][i])
            if not np.isfinite(limit) or limit <= 0:
                continue
            events.append({
                "date": day,
                "symbol": s,
                "session": session,
                "signal_time": float(t0),
                "filled": False,
                "limit_price": limit,
                "bid0": limit,
                **{f: feats.get(f) for f in FEATURE_ORDER},
                "score_preview": score,
            })
        if not events:
            self._emit({
                "kind": "ANCHOR_NO_CANDIDATE",
                "anchor": anchor,
                "t0": t0,
                "universe_n": len(self.universe),
                "candidate_n": 0,
            })
            return out

        sim = simulate_joint([dict(e) for e in events], score_fn=self.score_fn)
        for e in sim["events"]:
            if not e.get("admitted"):
                if e.get("CAPACITY_BLOCKED"):
                    self._notify("CAP_BLOCKED", {
                        "symbol": e["symbol"],
                        "reason": "CAPACITY_BLOCKED",
                        "anchor": anchor,
                        "score": e.get("alloc_score"),
                    })
                continue
            # respect live exposure (open+pending)
            if self.exposure() >= POSITION_CAP:
                self._notify("CAP_BLOCKED", {
                    "symbol": e["symbol"],
                    "reason": "CAPACITY_BLOCKED_LIVE",
                    "anchor": anchor,
                })
                continue
            if e["symbol"] in self.pending or e["symbol"] in self.open_symbols:
                continue
            po = PendingOrder(
                symbol=str(e["symbol"]),
                signal_time=float(t0),
                limit_price=float(e["limit_price"]),
                score=float(e.get("alloc_score") if e.get("alloc_score") is not None else e.get("score_preview") or 0.0),
                rank=int(e.get("cohort_rank") or 0),
                anchor=anchor,
                session=session,
                date=day,
                features={f: e.get(f) for f in FEATURE_ORDER},
            )
            self.pending[po.symbol] = po
            self.primary_admitted += 1
            payload = {
                "kind": "V1R_ENTRY_PENDING",
                "symbol": po.symbol,
                "anchor": anchor,
                "score": po.score,
                "rank": po.rank,
                "limit": po.limit_price,
                "wait_sec": WAIT_SEC,
                "open": self.open_n,
                "pending": self.pending_n,
                "cap": POSITION_CAP,
                "entry_mode": "V1R / PASSIVE BID",
                "strategy": "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY",
            }
            self._emit(payload)
            self._notify("ENTRY", payload)
            out.append(payload)
        return out

    def on_tick_fill_check(
        self,
        *,
        event_t: Optional[float] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Evaluate pending passive fills / expiries using frozen ask-cross SoT.

        Time axis = causal ingress/Capture recorded_at (same as find_ask_cross_fill board.t).
        Order matches Frozen offline eval: ask-cross FILL first; EXPIRE only if still
        pending after the inclusive [t0, t0+wait] window has been passed on this clock.
        """
        t_now = float(
            event_t
            if event_t is not None
            else board_event_epoch_from_payload(payload)
        )
        done: list[dict[str, Any]] = []
        for sym, po in list(self.pending.items()):
            board = self._board_arrays(sym)
            sess_end = po.signal_time + 3 * 3600
            # FILL before EXPIRE on the same tick — never expiry-first.
            fill = find_ask_cross_fill(
                board,
                t0=po.signal_time,
                wait_sec=WAIT_SEC,
                limit_price=po.limit_price,
                sess_end=sess_end,
            )
            if fill.get("filled"):
                ev = self._promote_fill(po, fill)
                done.append(ev)
                continue
            lim_t = min(float(po.signal_time) + float(WAIT_SEC), float(sess_end))
            # After FILL scan (which includes ti == lim_t), expire once clock reaches lim_t.
            # Fill-first on this same tick preserves Frozen inclusive-window semantics.
            if t_now >= lim_t - 1e-9:
                del self.pending[sym]
                self.primary_expired += 1
                ev = {
                    "kind": "V1R_EXPIRED",
                    "symbol": sym,
                    "anchor": po.anchor,
                    "limit": po.limit_price,
                    "signal_time": po.signal_time,
                    "expire_time": po.signal_time + WAIT_SEC,
                }
                self._emit(ev)
                self._notify("EXPIRED", ev)
                done.append(ev)
        return done

    def _fill_payload_snapshot(self, sym: str, fill_t: float) -> dict[str, Any]:
        """Market snapshot at passive FILL — bound into dual-lane Primary/Control."""
        rows = self.boards.get(str(sym).replace(".T", "")) or self.boards.get(sym) or []
        row = None
        for r in rows:
            if float(r.get("t") or 0) <= float(fill_t) + 1e-9:
                row = r
        if row is None and rows:
            row = rows[-1]
        if row is None:
            return {}
        bid = row.get("bid")
        ask = row.get("ask")
        bq = row.get("bid_qty")
        aq = row.get("ask_qty")
        imb = None
        try:
            if bq is not None and aq is not None and (float(bq) + float(aq)) > 0:
                imb = (float(bq) - float(aq)) / (float(bq) + float(aq))
        except (TypeError, ValueError):
            imb = None
        mid = None
        try:
            if bid is not None and ask is not None:
                mid = (float(bid) + float(ask)) / 2.0
        except (TypeError, ValueError):
            mid = None
        return {
            "event_time": float(fill_t),
            "CurrentPriceTime": datetime.fromtimestamp(float(fill_t), JST).isoformat(
                timespec="milliseconds"
            ),
            "Buy1": {"Price": bid, "Qty": bq},
            "Sell1": {"Price": ask, "Qty": aq},
            "CurrentPrice": mid if mid is not None else bid,
            "board_age_sec": float(row.get("fresh_sec") or 0.0),
            "fresh_sec": float(row.get("fresh_sec") or 0.0),
            "SpecialQuote": bool(row.get("special")),
            "imbalance": imb,
        }

    def _promote_fill(self, po: PendingOrder, fill: dict[str, Any]) -> dict[str, Any]:
        sym = po.symbol
        del self.pending[sym]
        self.open_symbols.add(sym)
        self.primary_fills += 1
        fill_price = float(fill["fill_price"])
        fill_t = float(fill["fill_t"])
        snap = self._fill_payload_snapshot(sym, fill_t)
        # Dual-lane Primary+Control occupancy from V1R-native fill ONLY
        dual = get_dual_lane(trace_dir=self.trace_dir)
        if dual is not None:
            dual.try_admit_fill(
                symbol=sym,
                fill_price=fill_price,
                fill_time=fill_t,
                payload=snap,
                session=po.session,
                source="v1r_native",
            )
        ev = {
            "kind": "V1R_FILL",
            "symbol": sym,
            "anchor": po.anchor,
            "score": po.score,
            "limit": po.limit_price,
            "fill_price": fill_price,
            "fill_time": fill_t,
            "signal_time": po.signal_time,
            "strategy": "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY",
            "entry_mode": "V1R / PASSIVE BID",
            "open": self.open_n,
            "pending": self.pending_n,
            "cap": POSITION_CAP,
            "source": "v1r_native",
            "fill_snapshot_bound": bool(snap),
            "fill_snapshot": snap,
        }
        self._emit(ev)
        self._notify("FILL", ev)
        return ev

    def note_primary_exit(self, symbol: str) -> None:
        from small_paper.v1r_live_dual_lane import canonical_symbol_key

        self.open_symbols.discard(canonical_symbol_key(symbol))

    def _emit(self, row: dict[str, Any]) -> None:
        row = dict(row)
        row["ts"] = _now().isoformat(timespec="seconds")
        row["identity"] = self.identity()
        self.events.append(row)
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            p = self.trace_dir / "v1r_native_entry_trace.jsonl"
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _notify_status(self, kind: str) -> str:
        return {
            "ENTRY": "PENDING",
            "EXPIRED": "EXPIRED",
            "FILL": "FILL",
            "EXIT": "EXIT",
            "CAP_BLOCKED": "CAP_BLOCKED",
        }.get(str(kind), str(kind))

    def _enrich_notify_payload(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        p = dict(payload)
        # Harden: Primary never says ENTRY方式 PBv2
        p["entry_mode"] = "V1R / PASSIVE BID"
        p.setdefault("qty", LOT_QTY)
        p.setdefault("cap", POSITION_CAP)
        p.setdefault("wait_sec", WAIT_SEC)
        p["source"] = "v1r_native"
        p["role"] = "PAPER_PRIMARY"
        p["status"] = self._notify_status(kind)
        if self.trading_date:
            p.setdefault("date", self.trading_date)
        return p

    def _append_notify_audit(self, filename: str, row: dict[str, Any]) -> None:
        if not self.trace_dir:
            return
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            with (self.trace_dir / filename).open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            # Last-resort stderr — never silent on audit write failure
            print(
                f"[V1R_DISCORD_AUDIT_WRITE_FAIL] file={filename} err={type(exc).__name__}:{exc}",
                flush=True,
            )

    def _notify(self, kind: str, payload: dict[str, Any]) -> None:
        """V1R-native Discord notify — ENTRY/EXPIRED→trade-entry; FILL/EXIT→trade-notify."""
        self.notify_sink.append({"kind": kind, "payload": payload, "ts": time.time()})
        p = self._enrich_notify_payload(kind, payload)
        try:
            from notify.v1r_discord_routing import V1RNotifyKind, publish_v1r

            kind_map = {
                "ENTRY": V1RNotifyKind.ENTRY,
                "FILL": V1RNotifyKind.FILL,
                "EXPIRED": V1RNotifyKind.EXPIRED,
                "EXIT": V1RNotifyKind.EXIT,
                "CAP_BLOCKED": V1RNotifyKind.CAP_BLOCKED,
            }
            nk = kind_map.get(kind)
            if nk is None:
                err = {
                    "ts": _now().isoformat(timespec="seconds"),
                    "event": "V1R_DISCORD_NOTIFY_UNKNOWN_KIND",
                    "kind": kind,
                    "symbol": p.get("symbol"),
                    "source": "v1r_native",
                    "error": f"unknown_kind:{kind}",
                    "call_site": "V1RNativeEntryLive._notify",
                }
                self._append_notify_audit("v1r_discord_errors.jsonl", err)
                self._append_notify_audit(
                    "v1r_discord_delivery.jsonl",
                    {**err, "status": "DROPPED_UNKNOWN_KIND", "channel": "", "queued": False},
                )
                return
            result = publish_v1r(
                nk,
                p,
                test_only=False,
                sync_http=False,
                session_id=f"v1r-native-{self.trading_date or 'na'}",
            )
            delivery = {
                "ts": _now().isoformat(timespec="seconds"),
                "event": "V1R_DISCORD_DELIVERY",
                "kind": result.kind,
                "notify_kind": kind,
                "status": result.status,
                "channel": result.channel,
                "env_key": result.env_key,
                "queued": result.queued,
                "error": result.error or "",
                "notification_id": result.notification_id,
                "http_status": result.http_status,
                "symbol": p.get("symbol"),
                "source": p.get("source"),
                "anchor": p.get("anchor"),
                "limit": p.get("limit"),
                "payload_status": p.get("status"),
                "role": p.get("role"),
                "call_site": "V1RNativeEntryLive._notify",
            }
            self._append_notify_audit("v1r_discord_delivery.jsonl", delivery)
            # Failures / webhook-miss / non-queue must never be silent
            ok_statuses = {"QUEUED", "SENT", "OK", "DELIVERED"}
            if result.error or result.status not in ok_statuses:
                self._append_notify_audit(
                    "v1r_discord_errors.jsonl",
                    {
                        **delivery,
                        "event": "V1R_DISCORD_NOTIFY_FAIL",
                    },
                )
        except Exception as exc:
            err = {
                "ts": _now().isoformat(timespec="seconds"),
                "event": "V1R_DISCORD_NOTIFY_EXCEPTION",
                "kind": kind,
                "symbol": p.get("symbol"),
                "source": "v1r_native",
                "anchor": p.get("anchor"),
                "limit": p.get("limit"),
                "payload_status": p.get("status"),
                "role": p.get("role"),
                "error": f"{type(exc).__name__}:{exc}",
                "call_site": "V1RNativeEntryLive._notify",
                "status": "EXCEPTION",
                "channel": "",
                "queued": False,
            }
            self._append_notify_audit("v1r_discord_delivery.jsonl", err)
            self._append_notify_audit("v1r_discord_errors.jsonl", err)
            print(
                f"[V1R_DISCORD_NOTIFY_EXCEPTION] kind={kind} symbol={p.get('symbol')} "
                f"err={type(exc).__name__}:{exc}",
                flush=True,
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "fail_reason": self.fail_reason,
            "identity": self.identity(),
            "native_universe_count": len(self.universe),
            "universe_contract": UNIVERSE_CONTRACT,
            "universe_source": self.universe_source,
            "open_n": self.open_n,
            "pending_n": self.pending_n,
            "exposure": self.exposure(),
            "open_symbols": sorted(self.open_symbols),
            "pending_symbols": sorted(self.pending),
            "anchor_fires": self.anchor_fires,
            "last_anchor": self.last_anchor,
            "primary_fills": self.primary_fills,
            "primary_expired": self.primary_expired,
            "primary_admitted": self.primary_admitted,
            "trace_dir": str(self.trace_dir) if self.trace_dir else None,
            "shadow_pbv2": self.shadow_pbv2.snapshot(),
            "submit_cancel_live": "0/0/0",
        }

    def heartbeat_fields(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "fail_reason": self.fail_reason,
            "native_universe_count": len(self.universe),
            "universe_source": self.universe_source,
            "anchor_fires": self.anchor_fires,
            "last_anchor": self.last_anchor,
            "open_n": self.open_n,
            "pending_n": self.pending_n,
            "primary_fills": self.primary_fills,
            "primary_expired": self.primary_expired,
            "primary_admitted": self.primary_admitted,
            "trace_dir": str(self.trace_dir) if self.trace_dir else None,
            "submit_cancel_live": "0/0/0",
        }


def _norm_sym(s: Any) -> str:
    return str(s or "").strip().replace(".T", "")


def resolve_day_fixed_am_runtime_universe(
    *,
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve DAY_FIXED_AM_RUNTIME_UNIVERSE_V1 for live Primary.

    SoT:
      - same-day AM Core10+Dynamic40 CSV (not refresh1000 / not PM / not prior-day)
      - same-day Market Ingress registration manifest (must match when present)

    Never invents symbols from the binding manifest (contract-only, no symbols body).
    """
    from small_paper.market_capture_registration import (
        load_symbols_from_universe_csv,
        read_registration_manifest,
        symbols_equal,
    )

    root = Path(native_root or NATIVE_ROOT)
    day = str(trading_date or datetime.now(JST).strftime("%Y%m%d"))
    am_path = (
        root / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    )
    am_syms = (
        [_norm_sym(s) for s in load_symbols_from_universe_csv(am_path)]
        if am_path.is_file()
        else []
    )
    am_syms = [s for s in am_syms if s]

    man = read_registration_manifest(root)
    man_day = str(man.get("trading_date") or "")
    man_syms: list[str] = []
    if man_day == day:
        raw = man.get("registered_symbols") or man.get("actual_symbols") or []
        man_syms = [_norm_sym(s) for s in raw if _norm_sym(s)]

    base: dict[str, Any] = {
        "ok": False,
        "contract": UNIVERSE_CONTRACT,
        "trading_date": day,
        "symbols": [],
        "symbol_count": 0,
        "universe_path": str(am_path) if am_path.is_file() else None,
        "source": "",
        "ingress_match": False,
        "ingress_count": len(man_syms),
        "am_count": len(am_syms),
        "reason": "",
    }

    if not am_syms and not man_syms:
        base["reason"] = "universe_unresolved_same_day"
        return base

    if am_syms and man_syms and not symbols_equal(am_syms, man_syms):
        base["reason"] = "am_csv_ingress_membership_mismatch"
        base["symbols"] = list(am_syms)
        base["symbol_count"] = len(am_syms)
        return base

    if am_syms:
        symbols = list(dict.fromkeys(am_syms))
        source = "am_csv+registration_manifest" if man_syms else "am_csv"
    else:
        # Same-day ingress only (still day-fixed; never prior-day CSV)
        symbols = list(dict.fromkeys(man_syms))
        source = "registration_manifest"

    if len(symbols) != 50:
        base["reason"] = f"symbol_count_{len(symbols)}_expected_50"
        base["symbols"] = symbols
        base["symbol_count"] = len(symbols)
        base["source"] = source
        base["ingress_match"] = bool(man_syms and symbols_equal(symbols, man_syms))
        return base

    return {
        **base,
        "ok": True,
        "symbols": symbols,
        "symbol_count": 50,
        "source": source,
        "ingress_match": bool(man_syms and symbols_equal(symbols, man_syms)),
        "reason": "",
    }


def boot_v1r_native_entry(
    *,
    universe: list[str],
    trace_dir: Optional[Path] = None,
    universe_source: str = "",
) -> V1RNativeEntryLive:
    """Fail-closed boot: model/SHA + non-empty day-fixed universe or NO PAPER PRIMARY."""
    uni = [_norm_sym(s) for s in universe if _norm_sym(s)]
    uni = list(dict.fromkeys(uni))
    try:
        ser = load_model_artifact()
        ident = verify_model_identity(ser)
        if not ident.get("pass"):
            raise RuntimeError(f"model_identity_fail:{ident}")
        if ser.get("model_artifact_sha256") != MODEL_ARTIFACT_SHA:
            raise RuntimeError("model_sha_mismatch")
        sfn = score_fn_from_serialized(ser)
        eng = V1RNativeEntryLive(
            universe=uni,
            score_fn=sfn,
            model_ser=ser,
            trace_dir=Path(trace_dir) if trace_dir else None,
            ready=True,
            universe_source=str(universe_source or ""),
        )
        if not eng.universe:
            eng.ready = False
            eng.fail_reason = "NO_PAPER_PRIMARY:EMPTY_UNIVERSE"
        return eng
    except Exception as exc:
        eng = V1RNativeEntryLive(
            universe=uni,
            score_fn=lambda _f: float("nan"),
            model_ser={},
            trace_dir=Path(trace_dir) if trace_dir else None,
            ready=False,
            fail_reason=f"NO_PAPER_PRIMARY:{type(exc).__name__}:{exc}",
            universe_source=str(universe_source or ""),
        )
        return eng


def get_native_entry() -> Optional[V1RNativeEntryLive]:
    return _ENGINE


def set_native_entry(eng: Optional[V1RNativeEntryLive]) -> None:
    global _ENGINE
    _ENGINE = eng


def reset_native_entry_for_tests() -> None:
    set_native_entry(None)


def ensure_native_entry(
    *,
    universe: Optional[list[str]] = None,
    trace_dir: Optional[Path] = None,
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
    force_rebuild: bool = False,
) -> V1RNativeEntryLive:
    """Boot or repair native ENTRY wiring. Never invents binding-manifest symbols."""
    global _ENGINE
    resolved: Optional[dict[str, Any]] = None
    uni = [_norm_sym(s) for s in (universe or []) if _norm_sym(s)]
    uni = list(dict.fromkeys(uni))
    source = "caller"
    if not uni:
        resolved = resolve_day_fixed_am_runtime_universe(
            native_root=native_root, trading_date=trading_date
        )
        if resolved.get("ok"):
            uni = list(resolved.get("symbols") or [])
            source = str(resolved.get("source") or "day_fixed_am")
        else:
            source = f"unresolved:{resolved.get('reason')}"

    td = Path(trace_dir) if trace_dir else None

    if _ENGINE is None or force_rebuild:
        _ENGINE = boot_v1r_native_entry(
            universe=list(uni),
            trace_dir=td,
            universe_source=source,
        )
        if resolved is not None and not resolved.get("ok") and not uni:
            _ENGINE.ready = False
            _ENGINE.fail_reason = (
                f"NO_PAPER_PRIMARY:EMPTY_UNIVERSE:{resolved.get('reason')}"
            )
        if td is not None:
            try:
                td.mkdir(parents=True, exist_ok=True)
                (td / "v1r_native_universe_wiring.json").write_text(
                    json.dumps(
                        {
                            "ts": _now().isoformat(timespec="seconds"),
                            "resolved": resolved,
                            "engine": {
                                "ready": _ENGINE.ready,
                                "fail_reason": _ENGINE.fail_reason,
                                "native_universe_count": len(_ENGINE.universe),
                                "universe_source": _ENGINE.universe_source,
                                "trace_dir": str(_ENGINE.trace_dir) if _ENGINE.trace_dir else None,
                            },
                        },
                        indent=2,
                        ensure_ascii=False,
                        default=str,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
        return _ENGINE

    # Repair in-place: empty universe / missing trace_dir (wiring only)
    if (not _ENGINE.universe) and uni:
        _ENGINE.universe = list(uni)
        _ENGINE.universe_source = source
        if _ENGINE.fail_reason.startswith("NO_PAPER_PRIMARY:EMPTY_UNIVERSE"):
            _ENGINE.fail_reason = ""
            if _ENGINE.model_ser:
                _ENGINE.ready = True
    if _ENGINE.trace_dir is None and td is not None:
        _ENGINE.trace_dir = td
    if not _ENGINE.universe:
        _ENGINE.ready = False
        if not _ENGINE.fail_reason:
            _ENGINE.fail_reason = "NO_PAPER_PRIMARY:EMPTY_UNIVERSE"
    return _ENGINE
