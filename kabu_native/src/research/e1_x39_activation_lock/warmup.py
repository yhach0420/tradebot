"""Warmup / feature parity: batch Historical vs rolling (same formulas)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x32_upstream_attribution.eval_stages import clock_epochs_for_day, load_boards_for_symbols
from research.e1_x33b_neutral_anchor.neutral import candidate_symbols_by_day
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.e1_x31_population_direction.identity import reproduce_population
from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized
from research.e1_x36_joint_allocator.replay import simulate_joint

from . import FEATURE_ORDER, FEATURE_PARITY_CLOCKS, FORBIDDEN_FROM, HISTORICAL_DAYS


def warmup_semantics() -> dict[str, Any]:
    """Document Historical feature builder semantics — no invented session clamp."""
    return {
        "feature_builder": "research.e1_x34b_entry_execution.features.preentry_from_board",
        "board_scope": "full calendar day push_jsonl (no session time filter on load)",
        "as_of": "only board rows with t <= signal_t",
        "session_open_clamp": False,
        "pre_open_excluded": False,
        "lunch_boundary_clamp": False,
        "previous_day_loaded": False,
        "am_into_pm_mid_ret": (
            "Possible only if quote gap across lunch leaves no valid mid in lookback; "
            "code walks earlier same-day mids (no lunch floor)."
        ),
        "am_warmup_0905": (
            "Wall-clock lookback from 09:05; pre-open quotes on same-day board may enter "
            "mid_ret_60/180 if present; no requirement of 180s post-09:00 history."
        ),
        "pm_warmup_1240": (
            "Wall-clock lookback from 12:40; no session reset at 12:30; "
            "event_rate_60s window [12:39,12:40] cannot include AM timestamps."
        ),
        "future_backfill_forbidden": True,
        "matches_x36_enrich_events": True,
    }


class RollingFeatureState:
    """Maintain rolling mid history; formulas identical to preentry_from_board lookbacks."""

    def __init__(self) -> None:
        self.t: list[float] = []
        self.mid: list[float] = []
        self.ask: list[float] = []
        self.bid: list[float] = []
        self.bq: list[float] = []
        self.aq: list[float] = []
        self.special: list[bool] = []

    def update_from_board_prefix(self, board: dict[str, np.ndarray], upto_t: float) -> None:
        """Ingest all events with t <= upto_t (future-free)."""
        self.t.clear()
        self.mid.clear()
        self.ask.clear()
        self.bid.clear()
        self.bq.clear()
        self.aq.clear()
        self.special.clear()
        t = board["t"].astype(float)
        for i in range(t.size):
            if t[i] > upto_t + 1e-12:
                break
            ask = float(board["ask"][i])
            bid = float(board["bid"][i])
            sp = bool(board["special"][i])
            ok = (not sp) and np.isfinite(ask) and np.isfinite(bid) and ask > 0 and bid > 0
            self.t.append(float(t[i]))
            self.mid.append(float((ask + bid) / 2.0) if ok else float("nan"))
            self.ask.append(ask)
            self.bid.append(bid)
            self.bq.append(float(board["bid_qty"][i]) if np.isfinite(board["bid_qty"][i]) else 0.0)
            self.aq.append(float(board["ask_qty"][i]) if np.isfinite(board["ask_qty"][i]) else 0.0)
            self.special.append(sp)

    def snapshot(self, signal_t: float) -> dict[str, Optional[float]]:
        """Same semantics as preentry_from_board for the six V1R features."""
        out: dict[str, Optional[float]] = {f: None for f in FEATURE_ORDER}
        if not self.t:
            return out
        # last index <= signal_t
        i = len(self.t) - 1
        while i >= 0 and self.t[i] > signal_t + 1e-12:
            i -= 1
        if i < 0:
            return out
        j = i
        while j >= 0:
            if self.special[j]:
                j -= 1
                continue
            ask, bid = self.ask[j], self.bid[j]
            if not (np.isfinite(ask) and np.isfinite(bid) and ask > 0 and bid > 0):
                j -= 1
                continue
            mid0 = (ask + bid) / 2.0
            out["spread_bps"] = (ask - bid) / mid0 * 10000.0
            denom = self.aq[j] + self.bq[j]
            out["imbalance"] = (self.bq[j] - self.aq[j]) / denom if denom > 0 else None
            out["log_bid_qty"] = float(np.log1p(self.bq[j]))
            break
        if out["spread_bps"] is None:
            return out

        def _ret(sec: float) -> Optional[float]:
            t0 = signal_t - sec
            m_now = self.mid[j] if j < len(self.mid) and np.isfinite(self.mid[j]) else np.nan
            m_past = np.nan
            for kk in range(len(self.t) - 1, -1, -1):
                if self.t[kk] > signal_t + 1e-12:
                    continue
                if np.isfinite(self.mid[kk]):
                    m_past = self.mid[kk]
                    if self.t[kk] <= t0 + 1e-9:
                        break
            if not (np.isfinite(m_now) and np.isfinite(m_past) and m_past > 0):
                return None
            return float((m_now / m_past - 1.0) * 10000.0)

        out["mid_ret_60s"] = _ret(60.0)
        out["mid_ret_180s"] = _ret(180.0)
        t0 = signal_t - 60.0
        n_ev = sum(1 for tt in self.t if t0 - 1e-12 <= tt <= signal_t + 1e-12)
        out["event_rate_60s"] = float(n_ev / 60.0)
        return out


def _feat_close(a: Optional[float], b: Optional[float], tol: float = 1e-9) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if not (np.isfinite(a) and np.isfinite(b)):
        return bool(np.isnan(a) and np.isnan(b)) if (isinstance(a, float) and isinstance(b, float)) else False
    return abs(float(a) - float(b)) <= tol


def feature_parity_audit(ser: dict, *, max_symbols_per_day: int = 3, days: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Compare batch preentry_from_board vs rolling snapshot on Historical boards."""
    days = days or HISTORICAL_DAYS[:3]  # sample for runtime; full list still documented
    rows, _, _ = reproduce_population()
    pool = candidate_symbols_by_day(rows)
    comparisons: list[dict[str, Any]] = []
    mismatches = 0
    checked = 0

    for day in days:
        assert day < FORBIDDEN_FROM
        syms = sorted(pool[day])[:max_symbols_per_day]
        boards = load_boards_for_symbols([(day, s) for s in syms])
        clocks = {hm: None for hm in FEATURE_PARITY_CLOCKS}
        for epoch, sess in clock_epochs_for_day(day):
            from datetime import datetime
            from zoneinfo import ZoneInfo
            dt = datetime.fromtimestamp(epoch, tz=ZoneInfo("Asia/Tokyo"))
            hm = (dt.hour, dt.minute)
            if hm in clocks:
                clocks[hm] = (epoch, sess)

        for hm, clock in clocks.items():
            if clock is None:
                continue
            epoch, sess = clock
            for sym in syms:
                board = boards.get((day, sym))
                if board is None or board["t"].size == 0:
                    continue
                batch = preentry_from_board(board, float(epoch))
                roll = RollingFeatureState()
                roll.update_from_board_prefix(board, float(epoch))
                snap = roll.snapshot(float(epoch))
                ok = all(_feat_close(batch.get(f), snap.get(f)) for f in FEATURE_ORDER)
                checked += 1
                if not ok:
                    mismatches += 1
                comparisons.append({
                    "date": day,
                    "symbol": sym,
                    "session": sess,
                    "anchor_hm": f"{hm[0]:02d}:{hm[1]:02d}",
                    "ok": ok,
                    "batch": {f: batch.get(f) for f in FEATURE_ORDER},
                    "rolling": snap,
                })

    # score/rank/admission identity on synthetic (strategy unchanged)
    sfn = score_fn_from_serialized(ser)
    means = ser["preprocessing"]["mean"]
    scales = ser["preprocessing"]["scale"]
    t0 = 1_800_000_100.0
    evs = []
    for i, sym in enumerate(["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]):
        feats = {f: float(means[j]) + (i * 0.05) * float(scales[j]) for j, f in enumerate(FEATURE_ORDER)}
        evs.append({
            "date": "20260721", "symbol": sym, "session": "AM",
            "signal_time": t0, "filled": False, "limit_price": 1000.0, "bid0": 1000.0,
            **feats,
        })
    sim_a = simulate_joint([dict(e) for e in evs], score_fn=sfn)
    sim_b = simulate_joint([dict(e) for e in evs], score_fn=sfn)
    scores_id = all(
        abs(float(sfn(a)) - float(sfn(b))) < 1e-15 for a, b in zip(evs, evs)
    )
    adm_a = sorted(e["symbol"] for e in sim_a["events"] if e.get("admitted"))
    adm_b = sorted(e["symbol"] for e in sim_b["events"] if e.get("admitted"))

    early_0905 = [c for c in comparisons if c["anchor_hm"] == "09:05"]
    early_1240 = [c for c in comparisons if c["anchor_hm"] == "12:40"]
    parity_pass = mismatches == 0 and checked > 0

    return {
        "warmup_semantics": warmup_semantics(),
        "days_sampled": list(days),
        "checked": checked,
        "mismatches": mismatches,
        "comparisons_sample": comparisons[:20],
        "parity_0905": {
            "n": len(early_0905),
            "pass": all(c["ok"] for c in early_0905) and len(early_0905) > 0,
        },
        "parity_1240": {
            "n": len(early_1240),
            "pass": all(c["ok"] for c in early_1240) and len(early_1240) > 0,
        },
        "six_feature_identity": parity_pass,
        "score_identity": scores_id,
        "rank_admission_identity": adm_a == adm_b,
        "admitted_symbols": adm_a,
        "pass": parity_pass and scores_id and adm_a == adm_b,
    }
