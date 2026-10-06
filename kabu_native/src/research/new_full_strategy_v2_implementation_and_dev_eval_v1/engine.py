"""Ingress-causal Full Strategy engine for frozen V4. Reuses V3 timer/X1/CAP/flatten. No PnL."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare
from research.new_full_strategy_architecture_construction_v2 import (
    AM_END_HM,
    AM_START_HM,
    EMA_LEVEL_PERIOD,
    HTF_WIDTH_SEC,
    SESSION_FLATTEN_HM,
)
from research.new_full_strategy_architecture_construction_v2.level_semantics import asof_ema21
from research.new_full_strategy_implementation_and_dev_eval_v1.engine import arrival_epoch, timer_epochs
from research.new_full_strategy_implementation_and_dev_eval_v1.quotes import (
    ask_ok,
    ask_px,
    bid_ok,
    bid_px,
    payload_of,
    quote_snap,
)
from research.new_full_strategy_v2_implementation_and_dev_eval_v1 import EXIT_SESSION, EXIT_TECH
from research.simple_full_strategy_discovery_v1 import POSITION_CAP
from research.simple_tech_entry_family.bars import BAR_FIELDS, minute_epoch
from research.simple_tech_entry_family.indicators import ema
from research.simple_tech_entry_family.stages import volume_confirm
from research.simple_tech_entry_family.v7_bars import aggregate_bars

STATE_TESTED = "RESISTANCE_TESTED"
STATE_PENDING = "ENTRY_PENDING"
STATE_OPEN = "OPEN"


def _fin(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x > 0 else None


def _empty_bar_lists() -> dict[str, list[float]]:
    return {
        "minute_epoch": [],
        "open": [],
        "high": [],
        "low": [],
        "close": [],
        "volume": [],
        "n_events": [],
        "first_t": [],
        "last_t": [],
        "finalize_t": [],
    }


class SessionEngine:
    def __init__(self, day: str, universe: list[str], *, debug: bool = False) -> None:
        self.day = str(day)
        self.universe = [_bare(s) for s in universe if _bare(s)]
        self.uni_set = set(self.universe)
        self.debug = bool(debug)
        self.am_start = hm_epoch(day, int(AM_START_HM[0]), int(AM_START_HM[1]))
        self.flatten_t = hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1]))
        self.am_end = hm_epoch(day, int(AM_END_HM[0]), int(AM_END_HM[1]))
        self.timers = timer_epochs(day)
        self.timer_i = 0
        self.last_arrival = float("-inf")
        self.flattened = False
        self.bars: dict[str, dict[str, list[float]]] = {s: _empty_bar_lists() for s in self.universe}
        self.pending_bar: dict[str, Optional[dict[str, float]]] = {s: None for s in self.universe}
        self.last_cum: dict[str, Optional[float]] = {s: None for s in self.universe}
        self.last_rec: dict[str, Optional[dict[str, Any]]] = {s: None for s in self.universe}
        self.last_pay: dict[str, Optional[dict[str, Any]]] = {s: None for s in self.universe}
        self.last_quote_seq: dict[str, int] = {s: -1 for s in self.universe}
        self.htf: dict[str, dict[str, np.ndarray]] = {s: {} for s in self.universe}
        self.published_vid: dict[str, Optional[str]] = {s: None for s in self.universe}
        self.published_level: dict[str, Optional[float]] = {s: None for s in self.universe}
        self.episode: dict[str, Optional[dict[str, Any]]] = {s: None for s in self.universe}
        self.entry_pending: list[dict[str, Any]] = []
        self.positions: dict[str, dict[str, Any]] = {}
        self.trades: list[dict[str, Any]] = []
        self.unfilled_session: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.prior_fills: dict[str, int] = {s: 0 for s in self.universe}
        self.counters = {
            "resistance_test_n": 0,
            "break_confirm_n": 0,
            "episode_expire_new_level_n": 0,
            "entry_pending_expire_new_level_n": 0,
            "entry_nofill_n": 0,
            "cap_reject_n": 0,
            "same_symbol_reject_n": 0,
            "technical_exit_n": 0,
            "session_exit_n": 0,
            "slot_release_n": 0,
            "reentry_n": 0,
            "x1_fill_n": 0,
            "old_level_entry_fill_after_new_level_n": 0,
            "partial_5m_rejected_n": 0,
            "partial_5m_used_n": 0,
        }
        self.flags = {
            "MISSING_ARRIVAL_N": 0,
            "MISSING_SEQUENCE_N": 0,
            "SEQUENCE_DISORDER_N": 0,
            "ARRIVAL_FIELD_RECEIVED_AT_JST_N": 0,
            "ARRIVAL_FIELD_RECEIVED_AT_N": 0,
            "ENTRY_FILL_AFTER_FLATTEN_N": 0,
            "WALKBACK_SESSION_EXIT_N": 0,
            "DUPLICATE_EXIT_FILL_N": 0,
            "SIGNAL_AFTER_FLATTEN_N": 0,
            "CURRENT_PRICE_TIME_AS_ARRIVAL_N": 0,
            "OLD_LEVEL_ENTRY_FILL_AFTER_NEW_LEVEL_N": 0,
            "PARTIAL_5M_USED_N": 0,
        }
        self._last_seq: Optional[int] = None
        self.bar_close: dict[str, dict[float, float]] = {s: {} for s in self.universe} if debug else {}
        self.bar_ohlcv: dict[str, dict[float, dict[str, float]]] = {s: {} for s in self.universe} if debug else {}
        self.asof_log: list[dict[str, Any]] = [] if debug else []
        self.level_pub_log: list[dict[str, Any]] = [] if debug else []
        self.expire_log: list[dict[str, Any]] = [] if debug else []
        self.occupancy_log: list[dict[str, Any]] = [] if debug else []

    def ingest(self, rec: dict[str, Any]) -> None:
        seq_raw = rec.get("sequence")
        if seq_raw is None or seq_raw == "":
            self.flags["MISSING_SEQUENCE_N"] += 1
            return
        seq = int(seq_raw)
        if self._last_seq is not None and seq < int(self._last_seq):
            self.flags["SEQUENCE_DISORDER_N"] += 1
        self._last_seq = seq
        arrival, field = arrival_epoch(rec)
        if arrival is None:
            self.flags["MISSING_ARRIVAL_N"] += 1
            return
        if field == "received_at_jst":
            self.flags["ARRIVAL_FIELD_RECEIVED_AT_JST_N"] += 1
        elif field == "received_at":
            self.flags["ARRIVAL_FIELD_RECEIVED_AT_N"] += 1
        self._fire_due(float(arrival))
        self._apply(rec, float(arrival), seq)
        self.last_arrival = float(arrival)

    def finish(self) -> None:
        while self.timer_i < len(self.timers):
            self._fire_timer(float(self.timers[self.timer_i]))
            self.timer_i += 1
        self._expire_unfilled_exits()

    def result(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "date": self.day,
            "trades": list(self.trades),
            "unfilled_session_exits": list(self.unfilled_session),
            "signals": list(self.signals),
            "flags": dict(self.flags),
            "counters": dict(self.counters),
            "universe_n": len(self.universe),
            "entry_pending_n": len(self.entry_pending),
            "open_n": len(self.positions),
            "signal_n": len(self.signals),
            "x1_fill_n": int(self.counters["x1_fill_n"]),
        }
        if self.debug:
            out["bar_close"] = {s: dict(v) for s, v in self.bar_close.items()}
            out["bar_ohlcv"] = {s: dict(v) for s, v in self.bar_ohlcv.items()}
            out["asof_log"] = list(self.asof_log)
            out["level_pub_log"] = list(self.level_pub_log)
            out["expire_log"] = list(self.expire_log)
            out["occupancy_log"] = list(self.occupancy_log)
            out["episodes"] = {s: (dict(v) if v else None) for s, v in self.episode.items()}
            out["published_vid"] = dict(self.published_vid)
            out["positions"] = {s: dict(v) for s, v in self.positions.items()}
            out["entry_pending"] = list(self.entry_pending)
        return out

    def _fire_due(self, arrival: float) -> None:
        while self.timer_i < len(self.timers):
            t = float(self.timers[self.timer_i])
            if self.last_arrival < t <= float(arrival) + 1e-12:
                self._fire_timer(t)
                self.timer_i += 1
                continue
            break

    def _fire_timer(self, t: float) -> None:
        e = float(t) - 60.0
        for sym in self.universe:
            self._finalize_symbol(sym, e, float(t))
        is_flatten = abs(float(t) - float(self.flatten_t)) <= 1e-9
        if not is_flatten and not self.flattened:
            for sym in self.universe:
                self._evaluate_symbol(sym, e, float(t))
            self._try_standing_entry_fills(float(t))
        self._technical_exits(e, float(t))
        if is_flatten:
            self._on_flatten(float(t))
        self._try_standing_exit_fills(float(t))

    def _raw_1m(self, sym: str) -> dict[str, np.ndarray]:
        b = self.bars[sym]
        n = len(b["minute_epoch"])
        raw: dict[str, np.ndarray] = {}
        for k in BAR_FIELDS:
            if k in b:
                raw[k] = np.asarray(b[k], dtype=float)
            else:
                raw[k] = np.zeros(n, dtype=float)
        return raw

    def _version_id(self, sym: str, htf: dict[str, np.ndarray], idx: int) -> str:
        return f"{sym}:{int(round(float(htf['minute_epoch'][idx])))}"

    def _rebuild_htf(self, sym: str, t: float) -> None:
        raw = self._raw_1m(sym)
        htf, leak = aggregate_bars(
            raw,
            width_sec=float(HTF_WIDTH_SEC),
            am_start=float(self.am_start),
            am_end=float(self.am_end),
        )
        self.htf[sym] = htf
        rejected = int(leak.get("PARTIAL_BUCKET_N") or 0)
        self.counters["partial_5m_rejected_n"] = int(self.counters["partial_5m_rejected_n"]) + rejected
        close = np.asarray(htf.get("close") if htf else [], dtype=float)
        if int(close.size) == 0:
            return
        e21 = ema(close, int(EMA_LEVEL_PERIOD))
        finite = np.where(np.isfinite(e21))[0]
        if int(finite.size) == 0:
            return
        last_i = int(finite[-1])
        vid = self._version_id(sym, htf, last_i)
        lvl = float(e21[last_i])
        prev = self.published_vid.get(sym)
        if prev == vid:
            self.published_level[sym] = lvl
            return
        self.published_vid[sym] = vid
        self.published_level[sym] = lvl
        if self.debug:
            self.level_pub_log.append(
                {
                    "t": float(t),
                    "symbol": sym,
                    "level_version_id": vid,
                    "level": lvl,
                    "htf_finalize_t": float(htf["finalize_t"][last_i]),
                    "prev": prev,
                }
            )
        if prev:
            self._expire_unfilled_for_new_level(sym, prev, vid, float(t))

    def _expire_unfilled_for_new_level(self, sym: str, old_vid: str, new_vid: str, t: float) -> None:
        ep = self.episode.get(sym)
        if ep and str(ep.get("level_version_id") or "") != str(new_vid) and str(ep.get("state") or "") == STATE_TESTED:
            self.counters["episode_expire_new_level_n"] += 1
            if self.debug:
                self.expire_log.append({"t": float(t), "symbol": sym, "kind": "RESISTANCE_TESTED", "old": old_vid, "new": new_vid})
            self.episode[sym] = None
        keep: list[dict[str, Any]] = []
        for p in self.entry_pending:
            if str(p.get("symbol") or "") != sym:
                keep.append(p)
                continue
            if str(p.get("level_version_id") or "") == str(new_vid):
                keep.append(p)
                continue
            self.counters["entry_pending_expire_new_level_n"] += 1
            self.counters["entry_nofill_n"] += 1
            if self.debug:
                self.expire_log.append({"t": float(t), "symbol": sym, "kind": "ENTRY_PENDING", "old": old_vid, "new": new_vid})
        self.entry_pending = keep
        ep2 = self.episode.get(sym)
        if ep2 and str(ep2.get("level_version_id") or "") != str(new_vid) and str(ep2.get("state") or "") != STATE_OPEN:
            self.episode[sym] = None

    def _finalize_symbol(self, sym: str, e: float, t: float) -> None:
        slot = self.pending_bar.get(sym)
        if not slot or abs(float(slot["minute_epoch"]) - float(e)) > 1e-9:
            return
        b = self.bars[sym]
        b["minute_epoch"].append(float(e))
        b["open"].append(float(slot["open"]))
        b["high"].append(float(slot["high"]))
        b["low"].append(float(slot["low"]))
        b["close"].append(float(slot["close"]))
        b["volume"].append(float(slot["volume"]))
        b["n_events"].append(float(slot.get("n") or 1.0))
        b["first_t"].append(float(slot.get("first_t") or e))
        b["last_t"].append(float(slot.get("last_t") or e))
        b["finalize_t"].append(float(t))
        if self.debug:
            self.bar_close[sym][float(e)] = float(slot["close"])
            self.bar_ohlcv[sym][float(e)] = {
                "open": float(slot["open"]),
                "high": float(slot["high"]),
                "low": float(slot["low"]),
                "close": float(slot["close"]),
                "volume": float(slot["volume"]),
                "finalize_t": float(t),
            }
        self.pending_bar[sym] = None
        self._rebuild_htf(sym, float(t))

    def _asof(self, sym: str, start_t: float) -> tuple[Optional[str], Optional[float], Optional[float]]:
        htf = self.htf.get(sym) or {}
        idx, lvl = asof_ema21(htf, float(start_t))
        if idx is None or lvl is None:
            if self.debug:
                self.asof_log.append(
                    {
                        "symbol": sym,
                        "start_t": float(start_t),
                        "idx": idx,
                        "level": lvl,
                        "level_finalize_t": None,
                        "level_version_id": None,
                        "published_vid": self.published_vid.get(sym),
                    }
                )
            return None, None, None
        vid = self._version_id(sym, htf, int(idx))
        fin = float(htf["finalize_t"][int(idx)])
        if self.debug:
            self.asof_log.append(
                {
                    "symbol": sym,
                    "start_t": float(start_t),
                    "idx": int(idx),
                    "level": float(lvl),
                    "level_finalize_t": fin,
                    "level_version_id": vid,
                    "published_vid": self.published_vid.get(sym),
                    "LEVEL_PREEXISTS": fin <= float(start_t) + 1e-12,
                    "SAME_BAR_CONTRIBUTES": fin > float(start_t) + 1e-12,
                }
            )
        return vid, float(lvl), fin

    def _vol_ok(self, sym: str, i: int) -> bool:
        vol = np.asarray(self.bars[sym]["volume"], dtype=float)
        if int(i) < 0 or int(i) >= int(vol.size):
            return False
        return bool(volume_confirm({"volume": vol}, int(i)))

    def _has_pending(self, sym: str) -> bool:
        return any(str(p.get("symbol") or "") == sym for p in self.entry_pending)

    def _evaluate_symbol(self, sym: str, e: float, t: float) -> None:
        if self.flattened:
            self.flags["SIGNAL_AFTER_FLATTEN_N"] += 1
            return
        mins = self.bars[sym]["minute_epoch"]
        if not mins:
            return
        i = None
        for k, m in enumerate(mins):
            if abs(float(m) - float(e)) <= 1e-9:
                i = int(k)
                break
        if i is None:
            return
        vid, lvl, _fin_t = self._asof(sym, float(e))
        pub = self.published_vid.get(sym)
        ep = self.episode.get(sym)
        if (
            ep
            and str(ep.get("state") or "") == STATE_TESTED
            and pub
            and str(ep.get("level_version_id") or "") == str(pub)
            and vid == str(ep.get("level_version_id") or "")
            and int(i) > int(ep["test_bar_i"])
            and sym not in self.positions
            and not self._has_pending(sym)
        ):
            close_i = float(self.bars[sym]["close"][i])
            test_l = float(ep["test_level"])
            if close_i > test_l and self._vol_ok(sym, int(i)):
                break_level = float(test_l)
                self.counters["break_confirm_n"] += 1
                sig = {
                    "symbol": sym,
                    "signal_t0": float(t),
                    "minute_e": float(e),
                    "test_bar_i": int(ep["test_bar_i"]),
                    "break_bar_i": int(i),
                    "level_version_id": str(ep["level_version_id"]),
                    "test_level": test_l,
                    "break_level": break_level,
                }
                self.signals.append(sig)
                self.entry_pending.append(dict(sig))
                ep["state"] = STATE_PENDING
                ep["break_level"] = break_level
                ep["break_bar_i"] = int(i)
                return
        if sym in self.positions or self._has_pending(sym):
            return
        if vid is None or lvl is None or pub is None or vid != pub:
            return
        if ep and str(ep.get("state") or "") == STATE_TESTED and str(ep.get("level_version_id") or "") == vid:
            return
        if int(i) < 1:
            return
        prev_c = float(self.bars[sym]["close"][i - 1])
        high_i = float(self.bars[sym]["high"][i])
        close_i = float(self.bars[sym]["close"][i])
        if prev_c < float(lvl) and high_i >= float(lvl) and close_i <= float(lvl):
            self.episode[sym] = {
                "state": STATE_TESTED,
                "test_level": float(lvl),
                "level_version_id": str(vid),
                "test_bar_i": int(i),
                "test_minute_e": float(e),
                "break_level": None,
            }
            self.counters["resistance_test_n"] += 1

    def _on_flatten(self, t: float) -> None:
        self.counters["entry_nofill_n"] += len(self.entry_pending)
        self.entry_pending.clear()
        self.flattened = True
        for _sym, pos in self.positions.items():
            if pos.get("exit_pending"):
                continue
            pos["exit_pending"] = True
            pos["exit_reason"] = EXIT_SESSION
            pos["exit_fire_t"] = float(t)
            self.counters["session_exit_n"] += 1

    def _technical_exits(self, e: float, t: float) -> None:
        for sym, pos in list(self.positions.items()):
            if pos.get("exit_pending"):
                continue
            if float(t) <= float(pos["fill_t"]) + 1e-12:
                continue
            mins = self.bars[sym]["minute_epoch"]
            i = None
            for k, m in enumerate(mins):
                if abs(float(m) - float(e)) <= 1e-9:
                    i = int(k)
                    break
            if i is None:
                continue
            close_i = float(self.bars[sym]["close"][i])
            br = float(pos["break_level"])
            if close_i < br:
                pos["exit_pending"] = True
                pos["exit_reason"] = EXIT_TECH
                pos["exit_fire_t"] = float(t)
                self.counters["technical_exit_n"] += 1
                if self.debug:
                    self.occupancy_log.append(
                        {
                            "t": float(t),
                            "event": "EXIT_PENDING",
                            "symbol": sym,
                            "occupancy": self._occupancy(),
                            "break_level": br,
                            "close": close_i,
                        }
                    )

    def _occupancy(self) -> int:
        return len(self.positions)

    def _pending_for(self, sym: str, fill_t: float) -> Optional[dict[str, Any]]:
        for p in self.entry_pending:
            if str(p.get("symbol") or "") == sym and float(p["signal_t0"]) <= float(fill_t) + 1e-12:
                return p
        return None

    def _can_entry_fill(self, sym: str, arrival: float, pend: Optional[dict[str, Any]]) -> bool:
        if self.flattened or float(arrival) >= float(self.flatten_t) - 1e-12:
            return False
        if pend is None:
            return False
        pub = self.published_vid.get(sym)
        if not pub or str(pend.get("level_version_id") or "") != str(pub):
            self.counters["old_level_entry_fill_after_new_level_n"] += 1
            self.flags["OLD_LEVEL_ENTRY_FILL_AFTER_NEW_LEVEL_N"] += 1
            return False
        if sym in self.positions:
            self.counters["same_symbol_reject_n"] += 1
            return False
        if self._occupancy() >= int(POSITION_CAP):
            self.counters["cap_reject_n"] += 1
            return False
        return True

    def _fill_entry(self, sym: str, fill_t: float, px: float, seq: int) -> None:
        if self.flattened or float(fill_t) >= float(self.flatten_t) - 1e-12:
            self.flags["ENTRY_FILL_AFTER_FLATTEN_N"] += 1
            return
        pend = self._pending_for(sym, float(fill_t))
        if not self._can_entry_fill(sym, float(fill_t), pend):
            return
        assert pend is not None
        keep: list[dict[str, Any]] = []
        taken = False
        for p in self.entry_pending:
            if (not taken) and p is pend:
                taken = True
                continue
            keep.append(p)
        self.entry_pending = keep
        reentry = int(self.prior_fills.get(sym) or 0) > 0
        if reentry:
            self.counters["reentry_n"] += 1
        self.counters["x1_fill_n"] += 1
        self.prior_fills[sym] = int(self.prior_fills.get(sym) or 0) + 1
        self.positions[sym] = {
            "symbol": sym,
            "fill_t": float(fill_t),
            "fill_px": float(px),
            "signal_t0": float(pend["signal_t0"]),
            "fill_seq": int(seq),
            "level_version_id": str(pend["level_version_id"]),
            "test_level": float(pend["test_level"]),
            "break_level": float(pend["break_level"]),
            "test_bar_i": int(pend.get("test_bar_i") or -1),
            "break_bar_i": int(pend.get("break_bar_i") or -1),
            "exit_pending": False,
            "exit_reason": "",
            "exit_fire_t": None,
            "exit_filled": False,
            "reentry": bool(reentry),
        }
        self.episode[sym] = {
            "state": STATE_OPEN,
            "test_level": float(pend["test_level"]),
            "level_version_id": str(pend["level_version_id"]),
            "test_bar_i": int(pend.get("test_bar_i") or -1),
            "break_level": float(pend["break_level"]),
        }
        if self.debug:
            self.occupancy_log.append(
                {
                    "t": float(fill_t),
                    "event": "ENTRY_FILL",
                    "symbol": sym,
                    "occupancy": self._occupancy(),
                    "signal_t0": float(pend["signal_t0"]),
                    "break_level": float(pend["break_level"]),
                }
            )

    def _fill_exit(self, sym: str, fill_t: float, px: float, seq: int) -> None:
        pos = self.positions.get(sym)
        if not pos or not pos.get("exit_pending"):
            return
        if pos.get("exit_filled"):
            self.flags["DUPLICATE_EXIT_FILL_N"] += 1
            return
        if float(fill_t) >= float(self.am_end) - 1e-12:
            return
        reason = str(pos.get("exit_reason") or "")
        if reason == EXIT_SESSION and float(fill_t) + 1e-9 < float(self.flatten_t):
            self.flags["WALKBACK_SESSION_EXIT_N"] += 1
            return
        pos["exit_filled"] = True
        self.counters["slot_release_n"] += 1
        self.trades.append(
            {
                "date": self.day,
                "symbol": sym,
                "signal_t0": float(pos["signal_t0"]),
                "fill_t": float(pos["fill_t"]),
                "fill_price": float(pos["fill_px"]),
                "exit_t": float(fill_t),
                "exit_price": float(px),
                "exit_reason": reason,
                "fill_seq": int(pos["fill_seq"]),
                "exit_seq": int(seq),
                "hold_sec": float(fill_t) - float(pos["fill_t"]),
                "level_version_id": str(pos.get("level_version_id") or ""),
                "test_level": float(pos.get("test_level") or 0.0),
                "break_level": float(pos.get("break_level") or 0.0),
                "reentry": bool(pos.get("reentry")),
            }
        )
        del self.positions[sym]
        self.episode[sym] = None
        if self.debug:
            self.occupancy_log.append(
                {
                    "t": float(fill_t),
                    "event": "EXIT_FILL",
                    "symbol": sym,
                    "occupancy": self._occupancy(),
                    "exit_reason": reason,
                }
            )

    def _standing_snap(self, sym: str, clock_t: float) -> Optional[dict[str, Any]]:
        rec = self.last_rec.get(sym)
        pay = self.last_pay.get(sym)
        if rec is None or pay is None:
            return None
        return quote_snap(rec, pay, float(clock_t))

    def _try_standing_entry_fills(self, t: float) -> None:
        if self.flattened:
            return
        cands: list[tuple[int, str]] = []
        seen: set[str] = set()
        for p in self.entry_pending:
            sym = str(p.get("symbol") or "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            cands.append((int(self.last_quote_seq.get(sym, -1)), sym))
        cands.sort(key=lambda x: x[0])
        for _seq, sym in cands:
            snap = self._standing_snap(sym, float(t))
            if snap is None or not ask_ok(snap):
                continue
            px = ask_px(snap)
            if px is None:
                continue
            self._fill_entry(sym, float(t), float(px), int(self.last_quote_seq.get(sym, -1)))

    def _try_standing_exit_fills(self, t: float) -> None:
        cands: list[tuple[int, str]] = []
        for sym, pos in self.positions.items():
            if pos.get("exit_pending") and not pos.get("exit_filled"):
                cands.append((int(self.last_quote_seq.get(sym, -1)), sym))
        cands.sort(key=lambda x: x[0])
        for seq, sym in cands:
            snap = self._standing_snap(sym, float(t))
            if snap is None or not bid_ok(snap):
                continue
            px = bid_px(snap)
            if px is None:
                continue
            self._fill_exit(sym, float(t), float(px), int(seq))

    def _apply(self, rec: dict[str, Any], arrival: float, seq: int) -> None:
        pay = payload_of(rec)
        sym = _bare(rec.get("symbol") or pay.get("Symbol"))
        if sym not in self.uni_set:
            return
        self.last_rec[sym] = rec
        self.last_pay[sym] = pay
        self.last_quote_seq[sym] = int(seq)
        if float(arrival) < float(self.am_end) - 1e-12:
            snap = quote_snap(rec, pay, float(arrival))
            if self.flattened:
                pos = self.positions.get(sym)
                if pos and pos.get("exit_pending") and not pos.get("exit_filled") and bid_ok(snap):
                    px = bid_px(snap)
                    if px is not None:
                        self._fill_exit(sym, float(arrival), float(px), int(seq))
            else:
                if any(p.get("symbol") == sym for p in self.entry_pending) and ask_ok(snap):
                    px = ask_px(snap)
                    if px is not None:
                        self._fill_entry(sym, float(arrival), float(px), int(seq))
                pos = self.positions.get(sym)
                if pos and pos.get("exit_pending") and not pos.get("exit_filled") and bid_ok(snap):
                    px = bid_px(snap)
                    if px is not None:
                        self._fill_exit(sym, float(arrival), float(px), int(seq))
        if float(arrival) >= float(self.am_end) - 1e-12:
            return
        m = minute_epoch(float(arrival))
        if m + 60.0 <= self._last_fired_timer() + 1e-12:
            return
        if m + 1e-12 < float(self.am_start):
            return
        px = _fin((pay or {}).get("CurrentPrice"))
        if px is None:
            return
        cum = None
        try:
            cv = float((pay or {}).get("TradingVolume"))
            if cv == cv:
                cum = cv
        except (TypeError, ValueError):
            cum = None
        vol = 0.0
        prev_c = self.last_cum.get(sym)
        if cum is not None:
            if prev_c is not None and cum >= prev_c:
                vol = float(cum - prev_c)
            self.last_cum[sym] = cum
        slot = self.pending_bar.get(sym)
        if slot is None or abs(float(slot["minute_epoch"]) - float(m)) > 1e-9:
            self.pending_bar[sym] = {
                "minute_epoch": float(m),
                "open": float(px),
                "high": float(px),
                "low": float(px),
                "close": float(px),
                "volume": float(vol),
                "n": 1.0,
                "first_t": float(arrival),
                "last_t": float(arrival),
            }
        else:
            slot["high"] = max(float(slot["high"]), float(px))
            slot["low"] = min(float(slot["low"]), float(px))
            slot["close"] = float(px)
            slot["volume"] = float(slot["volume"]) + float(vol)
            slot["n"] = float(slot["n"]) + 1.0
            slot["last_t"] = float(arrival)

    def _last_fired_timer(self) -> float:
        if self.timer_i <= 0:
            return float("-inf")
        return float(self.timers[self.timer_i - 1])

    def _expire_unfilled_exits(self) -> None:
        for sym, pos in list(self.positions.items()):
            if pos.get("exit_filled"):
                continue
            self.unfilled_session.append(
                {
                    "date": self.day,
                    "symbol": sym,
                    "fill_t": float(pos["fill_t"]),
                    "fill_price": float(pos["fill_px"]),
                    "exit_reason": str(pos.get("exit_reason") or ""),
                    "SESSION_EXIT_UNFILLED": True,
                    "break_level": float(pos.get("break_level") or 0.0),
                    "test_level": float(pos.get("test_level") or 0.0),
                    "level_version_id": str(pos.get("level_version_id") or ""),
                }
            )
            del self.positions[sym]


def replay_records(day: str, universe: list[str], records: list[dict[str, Any]], *, debug: bool = False) -> dict[str, Any]:
    ordered = sorted(records, key=lambda r: int(r.get("sequence") or 0))
    eng = SessionEngine(day, universe, debug=debug)
    for rec in ordered:
        eng.ingest(rec)
    eng.finish()
    return eng.result()
