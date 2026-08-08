"""Canonical D-MID_D4_H6 score producer for E1_X5 Forward Shadow.

ExtensionBus consumes ScorePacket / MISSING_SCORE; E1_X5 must not recompute.
Uses the same FittedModel definition as offline parity (TRAIN fit on enriched_s1).
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from research.integrated_directional_entry_exit_strategy.constants import (
    ENRICHED_CACHE,
    FIXED_CANDIDATE,
    FIXED_HID,
    FIXED_LABEL,
    FIXED_THRESHOLD,
)
from research.integrated_order_flow_absorption_reversal.loader import (
    Tick,
    classify_trade_side,
    exec_entry_ok,
    parse_ts,
)
from research.upward_edge_identification_audit.constants import (
    MAX_REGULAR_PER_STREAM,
    MAX_STATE_PER_STREAM,
    REGULAR_SAMPLE_SEC,
    STATE_SAMPLE_MIN_GAP_SEC,
)
from research.upward_edge_identification_audit.features import FeatureEngine, features_for_groups
from research.upward_edge_identification_audit.models import _apply_std, _impute, predict_proba
from research.upward_edge_identification_audit.samples import _state_changed
from small_paper.canonical_board import normalize_kabu_board

JST = ZoneInfo("Asia/Tokyo")
REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_CACHE = (
    REPO_ROOT
    / "results"
    / "research"
    / "e1_x5_forward_shadow"
    / "_cache"
    / "dmid_d4_h6_fitted.pkl"
)

KIND_SCORE = "SCORE"
KIND_MISSING = "MISSING_SCORE"
KIND_NO_SAMPLE = "NO_SAMPLE"
KIND_IDENTITY_FAIL = "IDENTITY_FAIL"

NO_EVALUATION_MISSING_SCORE = "NO_EVALUATION_MISSING_SCORE"


@dataclass(frozen=True)
class ScorePacket:
    score: float
    symbol: str
    day: str
    event_time: datetime
    event_sequence: int
    sample_id: str
    snapshot_id: str
    spread_bps: Optional[float]
    bid: float
    ask: float
    mid: Optional[float]
    model_key: str = FIXED_CANDIDATE
    threshold: float = FIXED_THRESHOLD


@dataclass(frozen=True)
class ScoreObserveResult:
    kind: str
    packet: Optional[ScorePacket] = None
    reason: Optional[str] = None
    symbol: str = ""
    event_time: Optional[datetime] = None
    event_sequence: Optional[int] = None
    snapshot_id: str = ""
    sample_type: str = ""  # REGULAR | STATE_CHANGE | "" when not due


@dataclass
class _SymState:
    eng: FeatureEngine = field(default_factory=FeatureEngine)
    prev: Optional[Tick] = None
    last_reg_ts: Optional[datetime] = None
    last_state_ts: Optional[datetime] = None
    n_reg: int = 0
    n_state: int = 0
    tick_idx: int = 0
    last_vol: Optional[float] = None
    last_sess: Optional[str] = None
    last_aq: Optional[float] = None
    last_bq: Optional[float] = None
    last_bp: Optional[float] = None
    day: str = ""


@dataclass
class _MidRow:
    ts: datetime
    mid: float
    buy10: float = 0.0
    sell10: float = 0.0


class DMidD4H6ScoreProvider:
    """Per-process canonical score source for ExtensionBus → E1_X5."""

    def __init__(self, model: Any) -> None:
        self.model = model
        self.model_key = str(getattr(model, "key", FIXED_CANDIDATE) or FIXED_CANDIDATE)
        self._syms: dict[str, _SymState] = {}
        self._mids: dict[str, _MidRow] = {}
        self.ready = model is not None
        self.load_error: Optional[str] = None

    @classmethod
    def maybe_create(cls) -> "DMidD4H6ScoreProvider":
        model, err = load_dmid_d4_h6_model()
        prov = cls(model)
        prov.load_error = err
        if model is None:
            prov.ready = False
        return prov

    def required_feature_lookback_sec(self) -> float:
        """Actual FeatureEngine warmup requirement (not a newly invented constant)."""
        from research.upward_edge_identification_audit.constants import WARMUP_SEC

        return float(WARMUP_SEC)

    def symbol_feature_warmed(self, symbol: str, ts: Any) -> bool:
        sym = _norm_symbol(symbol)
        st = self._syms.get(sym)
        if st is None or st.eng is None or st.eng.stream_start is None:
            return False
        if not isinstance(ts, datetime):
            ts = parse_ts(ts)
        if ts is None:
            return False
        return (ts - st.eng.stream_start).total_seconds() >= self.required_feature_lookback_sec()

    def observe(
        self,
        *,
        symbol: str,
        payload: Mapping[str, Any],
        day: Optional[str] = None,
        event_sequence: Optional[int] = None,
    ) -> ScoreObserveResult:
        sym = _norm_symbol(symbol)
        if not sym:
            return ScoreObserveResult(kind=KIND_MISSING, reason="BAD_SYMBOL")
        tick = self._tick_from_payload(
            symbol=sym,
            payload=payload,
            day=day,
            event_sequence=event_sequence,
        )
        if tick is None:
            return ScoreObserveResult(
                kind=KIND_MISSING,
                reason="TICK_BUILD_FAILED",
                symbol=sym,
            )
        if tick.symbol != sym:
            return ScoreObserveResult(
                kind=KIND_IDENTITY_FAIL,
                reason="SYMBOL_MISMATCH",
                symbol=sym,
                event_time=tick.ts,
                event_sequence=tick.event_seq,
            )

        st = self._syms.setdefault(sym, _SymState())
        if st.day and st.day != tick.day:
            # New trading day: reset stream state (fail-close across days).
            st = _SymState(day=tick.day)
            self._syms[sym] = st
        st.day = tick.day

        st.eng.update(tick)
        self._update_cross_section(sym, tick, st)
        ctx = self._ctx_for(sym, tick.ts)
        for k, v in ctx.items():
            setattr(st.eng, k, v)

        st.tick_idx += 1
        idx = st.tick_idx - 1

        sample_due, sample_type = self._sample_due(st, tick)
        if not sample_due:
            st.prev = tick
            return ScoreObserveResult(
                kind=KIND_NO_SAMPLE,
                symbol=sym,
                event_time=tick.ts,
                event_sequence=tick.event_seq,
                sample_type="",
            )

        if not self.ready or self.model is None:
            st.prev = tick
            return ScoreObserveResult(
                kind=KIND_MISSING,
                reason=self.load_error or "MODEL_UNAVAILABLE",
                symbol=sym,
                event_time=tick.ts,
                event_sequence=tick.event_seq,
                snapshot_id=_snapshot_id(tick.day, sym, tick.event_seq, idx, sample_type),
                sample_type=sample_type,
            )

        try:
            feats = st.eng.snapshot(tick)
            score = float(_score_feature_dict(self.model, feats))
        except Exception as exc:
            st.prev = tick
            return ScoreObserveResult(
                kind=KIND_MISSING,
                reason=f"SCORE_COMPUTE_FAILED:{type(exc).__name__}",
                symbol=sym,
                event_time=tick.ts,
                event_sequence=tick.event_seq,
                snapshot_id=_snapshot_id(tick.day, sym, tick.event_seq, idx, sample_type),
                sample_type=sample_type,
            )

        bid = float(tick.board.canonical_best_bid)
        ask = float(tick.board.canonical_best_ask)
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else None
        sid = _snapshot_id(tick.day, sym, tick.event_seq, idx, sample_type)
        packet = ScorePacket(
            score=score,
            symbol=sym,
            day=tick.day,
            event_time=tick.ts,
            event_sequence=int(tick.event_seq),
            sample_id=sid,
            snapshot_id=sid,
            spread_bps=tick.board.canonical_spread_bps,
            bid=bid,
            ask=ask,
            mid=mid,
            model_key=self.model_key,
            threshold=FIXED_THRESHOLD,
        )
        if sample_type == "REGULAR":
            st.n_reg += 1
            st.last_reg_ts = tick.ts
        else:
            st.n_state += 1
            st.last_state_ts = tick.ts
        st.prev = tick
        return ScoreObserveResult(
            kind=KIND_SCORE,
            packet=packet,
            symbol=sym,
            event_time=tick.ts,
            event_sequence=tick.event_seq,
            snapshot_id=sid,
            sample_type=sample_type,
        )

    def _sample_due(self, st: _SymState, tick: Tick) -> tuple[bool, str]:
        if not st.eng.warmed(tick) or not exec_entry_ok(tick):
            return False, ""
        take_reg = False
        take_state = False
        if st.last_reg_ts is None or (tick.ts - st.last_reg_ts).total_seconds() >= REGULAR_SAMPLE_SEC:
            if st.n_reg < MAX_REGULAR_PER_STREAM:
                take_reg = True
        if st.prev is not None and _state_changed(st.prev, tick):
            if st.last_state_ts is None or (
                tick.ts - st.last_state_ts
            ).total_seconds() >= STATE_SAMPLE_MIN_GAP_SEC:
                if st.n_state < MAX_STATE_PER_STREAM:
                    take_state = True
        if take_reg:
            return True, "REGULAR"
        if take_state:
            return True, "STATE_CHANGE"
        return False, ""

    def _tick_from_payload(
        self,
        *,
        symbol: str,
        payload: Mapping[str, Any],
        day: Optional[str],
        event_sequence: Optional[int],
    ) -> Optional[Tick]:
        if not isinstance(payload.get("Buy1"), dict) or not isinstance(payload.get("Sell1"), dict):
            return None
        board = normalize_kabu_board(payload)
        if board.canonical_best_bid is None or board.canonical_best_ask is None:
            return None
        if board.canonical_best_ask < board.canonical_best_bid:
            return None
        ts = (
            parse_ts(payload.get("CurrentPriceTime"))
            or parse_ts(payload.get("received_at_jst"))
            or parse_ts(payload.get("event_time"))
            or parse_ts(payload.get("received_at"))
        )
        if ts is None:
            # Strategy decision must not use wall-clock; caller should inject CurrentPriceTime.
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=JST)
        else:
            ts = ts.astimezone(JST)
        day_s = day or ts.strftime("%Y%m%d")
        sess = _session(ts)
        if sess == "OTHER":
            return None
        st = self._syms.setdefault(symbol, _SymState(day=day_s))
        px = _f(payload.get("CurrentPrice"))
        cum = _f(payload.get("TradingVolume"))
        vdelta: Optional[float] = None
        if cum is not None:
            prev = st.last_vol
            prev_s = st.last_sess
            if prev_s is not None and prev_s != sess:
                vdelta = None
            elif prev is None or cum < prev:
                vdelta = None
            else:
                vdelta = cum - prev
            st.last_vol = cum
            st.last_sess = sess
        side = classify_trade_side(px, board, vdelta if (vdelta is not None and vdelta > 0) else None)
        aq = board.canonical_ask_qty
        bq = board.canonical_bid_qty
        bp = board.canonical_best_bid
        paq, pbq, pbp = st.last_aq, st.last_bq, st.last_bp
        if aq is not None:
            st.last_aq = aq
        if bq is not None:
            st.last_bq = bq
        if bp is not None:
            st.last_bp = bp
        if event_sequence is not None:
            seq = int(event_sequence)
        else:
            try:
                seq = int(payload.get("sequence")) if payload.get("sequence") is not None else st.tick_idx
            except (TypeError, ValueError):
                seq = st.tick_idx
        return Tick(
            day=day_s,
            symbol=symbol,
            ts=ts,
            px=px,
            cum_vol=cum,
            volume_delta=vdelta,
            board=board,
            event_id=f"{day_s}:{symbol}:{ts.isoformat()}:{seq}",
            session=sess,
            trade_side=side,
            event_seq=seq,
            prev_ask_qty=paq,
            prev_bid_qty=pbq,
            prev_bid_px=pbp,
            idx=st.tick_idx,
        )

    def _update_cross_section(self, symbol: str, tick: Tick, st: _SymState) -> None:
        bid = tick.board.canonical_best_bid
        ask = tick.board.canonical_best_ask
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return
        mid = (bid + ask) / 2.0
        buy = sell = 0.0
        # Approximate 10s flow from FeatureEngine hist if present
        for e in reversed(st.eng.hist):
            if (tick.ts - e.ts).total_seconds() > 10.0:
                break
            if e.qty <= 0:
                continue
            if e.side == "BUY":
                buy += e.qty
            elif e.side == "SELL":
                sell += e.qty
        self._mids[symbol] = _MidRow(ts=tick.ts, mid=mid, buy10=buy, sell10=sell)

    def _ctx_for(self, symbol: str, ts: datetime) -> dict[str, Optional[float]]:
        # Cross-section from symbols touched in last 60s (live proxy of offline 10s timeline).
        rows = []
        for sym, row in self._mids.items():
            if (ts - row.ts).total_seconds() <= 60.0:
                rows.append((sym, row))
        if len(rows) < 2:
            return {}
        # 30s return vs stored mid age — use previous mid if available via hist not stored;
        # live proxy: relative to earliest mid in window for breadth sign only.
        rets = []
        buy_b = sell_b = 0
        for sym, row in rows:
            # Without full history per sym, use mid level vs mean as relative proxy for breadth;
            # ret vs median computed across current mids' demeaned levels is weak — use
            # buy/sell flow breadth which we can compute, and ret=0 if no lookback.
            rets.append((sym, 0.0))
            if row.buy10 > row.sell10:
                buy_b += 1
            elif row.sell10 > row.buy10:
                sell_b += 1
        # Better: compute ret from FeatureEngine if this symbol has hist
        st = self._syms.get(symbol)
        rel_ret = None
        ret_pct = None
        med = None
        if st is not None and st.eng.hist:
            px1 = st.eng.hist[-1].px
            px0 = None
            for e in reversed(st.eng.hist):
                if (ts - e.ts).total_seconds() >= 30.0 and e.px is not None:
                    px0 = e.px
                    break
            if px1 is not None and px0 is not None and px0 > 0:
                my_ret = (px1 - px0) / px0
                # Collect 30s rets for symbols with enough hist
                all_rets = []
                for sym2, st2 in self._syms.items():
                    if not st2.eng.hist:
                        continue
                    p1 = st2.eng.hist[-1].px
                    p0 = None
                    for e in reversed(st2.eng.hist):
                        if (ts - e.ts).total_seconds() >= 30.0 and e.px is not None:
                            p0 = e.px
                            break
                    if p1 is not None and p0 is not None and p0 > 0 and (ts - st2.eng.hist[-1].ts).total_seconds() <= 60:
                        all_rets.append((sym2, (p1 - p0) / p0))
                if all_rets:
                    vals = sorted(r for _, r in all_rets)
                    med = vals[len(vals) // 2]
                    up_r = sum(1 for v in vals if v > 0) / len(vals)
                    dn_r = sum(1 for v in vals if v < 0) / len(vals)
                    rel_ret = my_ret - med
                    ret_pct = sum(1 for v in vals if v <= my_ret) / len(vals)
                    n = len(rows)
                    return {
                        "breadth_up": up_r,
                        "breadth_down": dn_r,
                        "median_ret": med,
                        "rel_ret": rel_ret,
                        "ret_percentile": ret_pct,
                        "flow_percentile": ret_pct,
                        "rank_strength": ret_pct,
                        "mkt_buy_breadth": buy_b / n if n else None,
                        "mkt_sell_breadth": sell_b / n if n else None,
                    }
        n = len(rows)
        return {
            "breadth_up": None,
            "breadth_down": None,
            "median_ret": None,
            "rel_ret": None,
            "ret_percentile": None,
            "flow_percentile": None,
            "rank_strength": None,
            "mkt_buy_breadth": buy_b / n if n else None,
            "mkt_sell_breadth": sell_b / n if n else None,
        }


def _score_feature_dict(model: Any, feats: Mapping[str, Any]) -> float:
    row = features_for_groups(dict(feats), model.groups)
    X = _impute([row], model.keys, model.medians)
    Xs = _apply_std(X, model.means, model.stds)
    return float(predict_proba(Xs, model.w, model.b)[0])


def load_dmid_d4_h6_model() -> tuple[Any, Optional[str]]:
    """Load cached FittedModel; rebuild from enriched TRAIN if needed."""
    if MODEL_CACHE.exists():
        try:
            payload = pickle.loads(MODEL_CACHE.read_bytes())
            model = payload.get("model") if isinstance(payload, dict) else payload
            if model is not None and getattr(model, "key", None) == FIXED_CANDIDATE:
                return model, None
        except Exception as exc:
            # fall through to rebuild
            err = f"MODEL_CACHE_READ_FAILED:{type(exc).__name__}"
    else:
        err = None
    if not ENRICHED_CACHE.exists():
        return None, err or "ENRICHED_CACHE_MISSING"
    try:
        from research.continuous_directional_vs_execution_edge.scoring import fit_dir_candidate

        bundle = pickle.loads(ENRICHED_CACHE.read_bytes())
        model = fit_dir_candidate(bundle["tr"], FIXED_LABEL, FIXED_HID)
        MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        MODEL_CACHE.write_bytes(
            pickle.dumps(
                {
                    "model": model,
                    "candidate": FIXED_CANDIDATE,
                    "label": FIXED_LABEL,
                    "hid": FIXED_HID,
                    "threshold": FIXED_THRESHOLD,
                    "source": str(ENRICHED_CACHE),
                }
            )
        )
        return model, None
    except Exception as exc:
        return None, f"MODEL_FIT_FAILED:{type(exc).__name__}"


def validate_score_identity(
    *,
    packet: ScorePacket,
    symbol: str,
    event_time: datetime,
    event_sequence: Optional[int] = None,
    snapshot_id: Optional[str] = None,
) -> Optional[str]:
    """Fail-close identity checks. Returns reason or None if OK."""
    if packet.symbol != _norm_symbol(symbol):
        return "SYMBOL_MISMATCH"
    if packet.model_key != FIXED_CANDIDATE:
        return "MODEL_KEY_MISMATCH"
    if abs((packet.event_time - event_time).total_seconds()) > 1e-3:
        return "EVENT_TIME_MISMATCH"
    if event_sequence is not None and int(packet.event_sequence) != int(event_sequence):
        return "EVENT_SEQUENCE_MISMATCH"
    if snapshot_id is not None and snapshot_id and packet.snapshot_id != snapshot_id:
        return "SNAPSHOT_MISMATCH"
    return None


def _norm_symbol(symbol: str) -> str:
    s = str(symbol or "").strip()
    if not s:
        return ""
    if not s.endswith(".T") and s.isdigit():
        return f"{s}.T"
    return s


def _snapshot_id(day: str, symbol: str, event_seq: int, idx: int, sample_type: str) -> str:
    return f"{day}|{symbol}|{event_seq}|{idx}|{sample_type}"


def _session(ts: datetime) -> str:
    h = ts.hour
    if 7 <= h < 12:
        return "AM"
    if 12 <= h < 16:
        return "PM"
    return "OTHER"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
