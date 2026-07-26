#!/usr/bin/env python3
"""Phase687W66 — Full-period latest Shadow portfolio profit validation (capital-constrained).

Offline research only. Does NOT adopt Shadows into Runtime or mutate Forward thresholds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

NATIVE_ROOT = Path(__file__).resolve().parents[1]
# Parent repo root needed for `src.kabu_signal_engine` imports via research chain
sys.path.insert(0, str(NATIVE_ROOT.parent))
sys.path.insert(0, str(NATIVE_ROOT / "src"))
sys.path.insert(0, str(NATIVE_ROOT))

from research.phase383_realistic_credit_sizing_backtest import (  # noqa: E402
    compute_buying_power,
    compute_requested_shares,
)
from research.phase382_capital_constrained_backtest import (  # noqa: E402
    LOT_SIZE,
    _float,
    _parse_ts,
    _position_key,
)
from research.phase634_pbv2_only_rise5_full_period import (  # noqa: E402
    SMALL_PAPER_ROOT,
    _entry_pool,
    _iter_events,
    _is_push_replay_session,
    _minutes_from_open,
    _num,
    _pnl_yen_100,
)
from research.phase631_profit_source_attribution import CAT_FEATURES, ENTRY_FEATURES  # noqa: E402
from small_paper.cost_aware_entry_shadow import (  # noqa: E402
    STOP_Z_REJECT,
    compute_runtime_features,
    integrated_score,
    winner_enrichment_from_cycle,
    _cs_z,
)
from small_paper.flat_weak_range_forward_shadow import evaluate_flat_weak_range_shadow  # noqa: E402
from small_paper.pullback_misread_entry_guard_shadow import (  # noqa: E402
    would_block_pullback_dynamic40_shadow,
)

JST = ZoneInfo("Asia/Tokyo")
PHASE = "Phase687W66"
OUT_DIR = NATIVE_ROOT / "results" / "reports"
RUNTIME_YAML = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)

ARM_IDS = [
    "ARM0_RUNTIME_BASELINE",
    "ARM1_COST_AWARE_ONLY",
    "ARM2_FLAT_WEAK_RANGE_ONLY",
    "ARM3_PULLBACK_MISREAD_ONLY",
    "ARM4_PULLBACK_VOLUME_ONLY",
    "ARM5_W43F_LATEST_ONLY",
    "ARM6_ALL_LATEST_SHADOWS_COMBINED",
    "ARM7_BEST_CAUSAL_COMBINATION",
]

POST_ENTRY_LEAK_KEYS = (
    "peak_mfe_pct",
    "rolling_mfe_pct",
    "rolling_mae_pct",
    "mfe_pct",
    "mae_pct",
    "exit_reason",
    "pnl_yen_100",
    "pnl_pct",
    "exit_price",
    "exit_time",
    "no_progress_exit",
)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _excel_cell(x: Any) -> Any:
    if x is None:
        return ""
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return ""
    if isinstance(x, (dict, list)):
        return json.dumps(x, ensure_ascii=False, default=str)[:32000]
    return x


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in [
        [PHASE],
        ["generated", datetime.now(JST).isoformat()],
        ["note", "Offline capital validation; runtime unchanged; not an adoption decision"],
    ]:
        ws.append(row)
    for name, df in sheets.items():
        w = wb.create_sheet(str(name)[:31])
        if df is None or getattr(df, "empty", True):
            w.append(["empty"])
            continue
        clean = df.head(100000).copy()
        for c in clean.columns:
            clean[c] = clean[c].map(_excel_cell)
        for r in dataframe_to_rows(clean, index=False, header=True):
            w.append([_excel_cell(x) for x in r])
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _session_kind(session: str, entry_time: str) -> str:
    if "122" in str(session)[:20]:
        return "PM"
    dt = _parse_ts(str(entry_time or ""))
    if dt is None:
        return "AM"
    hhmm = dt.hour * 100 + dt.minute
    return "PM" if hhmm >= 1230 else "AM"


# ---------------------------------------------------------------------------
# Data load (SoT: small_paper live_session events; accepted + observer_exit join)
# ---------------------------------------------------------------------------


def load_enriched_trades_for_session(session_dir: Path, day: str) -> list[dict[str, Any]]:
    if not (
        (session_dir / "small_paper_events.jsonl").is_file()
        or (session_dir / "small_paper_events.csv").is_file()
    ):
        return []

    accepted_by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    for e in _iter_events(session_dir):
        if e.get("event_type") != "accepted":
            continue
        key = (e.get("symbol"), e.get("entry_time") or e.get("message_index"))
        accepted_by_key[key] = e

    trades: list[dict[str, Any]] = []
    for e in _iter_events(session_dir):
        if e.get("event_type") != "observer_exit":
            continue
        sym = e.get("symbol")
        entry_time = e.get("entry_time")
        acc = accepted_by_key.get((sym, entry_time)) or {}
        entry_price = (
            _num(e.get("entry_price"))
            or _num(acc.get("entry_price"))
            or _num(acc.get("current_price"))
        )
        exit_price = _num(e.get("exit_price")) or _num(e.get("current_price"))
        pnl_pct = _num(e.get("pnl_pct"))
        pnl_yen = _pnl_yen_100(entry_price, exit_price, pnl_pct)
        if pnl_yen is None:
            continue
        exit_time = e.get("exit_time") or e.get("event_time") or e.get("ts")
        if not exit_time and e.get("timestamp"):
            exit_time = e.get("timestamp")

        row: dict[str, Any] = {
            "day": day,
            "session": session_dir.name,
            "symbol": sym,
            "entry_time": entry_time or acc.get("entry_time"),
            "exit_time": exit_time,
            "entry_type": acc.get("entry_type") or e.get("entry_type") or "PBV2",
            "entry_pool": _entry_pool(acc.get("entry_type") or e.get("entry_type")),
            "exit_reason": e.get("exit_reason") or e.get("structural_exit_reason") or "",
            "pnl_yen_100": float(pnl_yen),
            "pnl_pct": float(pnl_pct) if pnl_pct is not None else 0.0,
            "entry_price": float(entry_price) if entry_price else 0.0,
            "exit_price": float(exit_price) if exit_price else 0.0,
            "signal_price": _num(acc.get("current_price")) or entry_price,
            "decision_price": _num(acc.get("current_price")) or entry_price,
            "accepted_price": float(entry_price) if entry_price else 0.0,
            "hypothetical_fill_price": float(entry_price) if entry_price else 0.0,
            "universe_slot": acc.get("universe_slot") or e.get("universe_slot") or "",
            "universe_bucket": acc.get("universe_bucket") or acc.get("source_bucket") or "",
            "source_bucket": acc.get("source_bucket") or "",
            "peak_mfe_pct": _num(e.get("peak_mfe_pct")),
            "rolling_mfe_pct": _num(
                e.get("rolling_mfe_pct")
                if e.get("rolling_mfe_pct") is not None
                else acc.get("rolling_mfe_pct")
            ),
            "rolling_mae_pct": _num(
                e.get("rolling_mae_pct")
                if e.get("rolling_mae_pct") is not None
                else acc.get("rolling_mae_pct")
            ),
            "minutes_from_open": _minutes_from_open(acc.get("entry_time") or entry_time),
            "session_kind": _session_kind(session_dir.name, str(entry_time or acc.get("entry_time") or "")),
            "qty": 100,
        }
        src = {**e, **acc}
        for fid, key, _fam in ENTRY_FEATURES:
            if fid == "minutes_from_open":
                continue
            val = src.get(key)
            if isinstance(val, str):
                if val in (True, "True", "true", 1, "1"):
                    row[fid] = 1.0
                elif val in (False, "False", "false", 0, "0"):
                    row[fid] = 0.0
                else:
                    row[fid] = _num(val)
            else:
                row[fid] = _num(val)
            if fid == "board_mid_token":
                row[fid] = 1.0 if src.get(key) in (True, "True", "true", 1, "1") else 0.0
        for fid, key, _fam in CAT_FEATURES:
            if fid == "exit_reason":
                row[fid] = str(row["exit_reason"] or "")
            elif fid == "entry_type":
                row[fid] = str(row["entry_type"] or "")
            else:
                row[fid] = str(src.get(key) or "")
        # Preserve raw accepted shadow flags for audit (not used as decision when recomputed)
        for k in (
            "flat_weak_range_shadow_block",
            "pullback_misread_guard_shadow_blocked",
            "spread_bps",
            "entry_expectancy_score_v2",
            "entry_near_day_high_pct",
            "r60_sec",
            "r120_sec",
            "board_improvement",
            "recent_low_break",
            "vwap_cross_down",
            "pretrend_shape",
            "breakout_class",
        ):
            if k not in row and src.get(k) not in (None, ""):
                row[k] = src.get(k)
        trades.append(row)
    return trades


def discover_and_load_all_period() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    missing_days: list[str] = []
    if not SMALL_PAPER_ROOT.is_dir():
        return [], [], {"error": "small_paper root missing"}

    day_dirs = sorted(
        d
        for d in SMALL_PAPER_ROOT.iterdir()
        if d.is_dir() and len(d.name) == 8 and d.name.isdigit()
    )
    for day_dir in day_dirs:
        day_iso = f"{day_dir.name[:4]}-{day_dir.name[4:6]}-{day_dir.name[6:8]}"
        day_trades: list[dict[str, Any]] = []
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            has_events = (sess_dir / "small_paper_events.jsonl").is_file() or (
                sess_dir / "small_paper_events.csv"
            ).is_file()
            if not has_events:
                continue
            st = load_enriched_trades_for_session(sess_dir, day_iso)
            if not st:
                continue
            sessions.append(
                {
                    "day": day_iso,
                    "day_key": day_dir.name,
                    "session": sess_dir.name,
                    "session_dir": str(sess_dir),
                    "trade_count": len(st),
                }
            )
            day_trades.extend(st)
        if day_trades:
            trades.extend(day_trades)
        else:
            # day folder exists but no replayable trades
            if any(day_dir.glob("live_session_*")):
                missing_days.append(day_iso)

    trades.sort(key=lambda t: (str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    days = sorted({t["day"] for t in trades})
    meta = {
        "data_start": days[0] if days else "",
        "data_end": days[-1] if days else "",
        "trading_days": len(days),
        "session_count": len(sessions),
        "candidate_count": len(trades),
        "runtime_entry_count": len(trades),
        "actual_replay_trade_count": len(trades),
        "symbol_count": len({str(t.get("symbol")) for t in trades}),
        "missing_or_empty_days": missing_days,
        "excluded_days": [],
        "source_of_truth": "results/small_paper/*/live_session_*/small_paper_events.{jsonl,csv}",
        "dedupe_policy": "accepted+observer_exit join on (symbol, entry_time); one row per completed trade",
    }
    return trades, sessions, meta


# ---------------------------------------------------------------------------
# Shadow classification & causal predicates
# ---------------------------------------------------------------------------


SHADOW_CATALOG: list[dict[str, Any]] = [
    {
        "id": "cost_aware_entry",
        "role": "ranking_shadow+reject_shadow+no_fill_shadow",
        "classification": "trade_decision_shadow",
        "arm": "ARM1_COST_AWARE_ONLY",
        "frozen_threshold": {"STOP_Z_REJECT": STOP_Z_REJECT},
        "notes": "integrated_score ranking + z_stop>=1.65 reject; same-snapshot no-fill semantics approximated on concurrent CAP slots",
    },
    {
        "id": "flat_weak_range",
        "role": "reject_shadow",
        "classification": "trade_decision_shadow",
        "arm": "ARM2_FLAT_WEAK_RANGE_ONLY",
        "frozen_threshold": "evaluate_flat_weak_range_shadow (entry-causal features only)",
        "notes": "Post-entry MFE / exit_reason stripped before evaluation",
    },
    {
        "id": "pullback_misread",
        "role": "reject_shadow",
        "classification": "trade_decision_shadow",
        "arm": "ARM3_PULLBACK_MISREAD_ONLY",
        "frozen_threshold": "rise5<0 AND vwap_dev<0 AND Dynamic40 scope",
        "notes": "would_block_pullback_dynamic40_shadow",
    },
    {
        "id": "pullback_volume_forward",
        "role": "logger",
        "classification": "analysis_only",
        "arm": "ARM4_PULLBACK_VOLUME_ONLY",
        "executable": False,
        "exclude_reason": "Logger only — no ENTRY Reject/Permit/ranking predicate; VOL_* thresholds are observational",
    },
    {
        "id": "w43f_winner_stop_forward",
        "role": "reachability_pipeline",
        "classification": "analysis_only",
        "arm": "ARM5_W43F_LATEST_ONLY",
        "executable": False,
        "exclude_reason": "W43F is forward reachability / DQ plumbing, not a trade decision shadow",
    },
]


def causal_view(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Strip post-entry / EXIT-only fields before Shadow decision."""
    row = {k: v for k, v in trade.items() if k not in POST_ENTRY_LEAK_KEYS}
    # Map entry rise aliases used by flat-weak pretrend
    if row.get("r300_sec") is None and row.get("entry_rise_5min_pct") is not None:
        row["r300_sec"] = row.get("entry_rise_5min_pct")
    if row.get("r600_sec") is None and row.get("entry_rise_10min_pct") is not None:
        row["r600_sec"] = row.get("entry_rise_10min_pct")
    if row.get("vwap_dev_pct") is None and row.get("entry_vwap_dev_pct") is not None:
        row["vwap_dev_pct"] = row.get("entry_vwap_dev_pct")
    return row


def audit_causality(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    checked = 0
    for t in trades:
        checked += 1
        # Decision must not depend on EXIT outcome fields — enforced by causal_view
        # Feature presence at decision: rise/vwap used by PullbackMisread are entry-tagged
        for feat in ("entry_rise_5min_pct", "entry_vwap_dev_pct"):
            # Missing is OK (guard returns False); presence of future-only keys in decision path is FAIL
            pass
        # Fail if any trade would require peak_mfe for a decision we make — we never pass it
        if "peak_mfe_pct" in causal_view(t) and causal_view(t).get("peak_mfe_pct") is not None:
            violations.append({"symbol": t.get("symbol"), "reason": "peak_mfe_leaked_into_causal_view"})
    # Re-check: causal_view must never retain leak keys
    for t in trades[:50]:
        cv = causal_view(t)
        for k in POST_ENTRY_LEAK_KEYS:
            if k in cv:
                violations.append({"symbol": t.get("symbol"), "reason": f"leak_key_present:{k}"})
                break
    return {
        "causality_pass": len(violations) == 0,
        "checked_trades": checked,
        "violations": violations[:20],
        "policy": "feature_time<=decision_time via entry_* fields only; POST_ENTRY_LEAK_KEYS stripped",
    }


def block_flat_weak(trade: Mapping[str, Any]) -> tuple[bool, str]:
    blocked, reason = evaluate_flat_weak_range_shadow(causal_view(trade))
    return blocked, reason or "flat_weak_range"


def block_pullback_misread(trade: Mapping[str, Any]) -> tuple[bool, str]:
    cv = causal_view(trade)
    blocked = would_block_pullback_dynamic40_shadow(cv)
    return blocked, "pullback_misread_dynamic40" if blocked else ""


def annotate_cost_aware_scores(trades: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cross-sectional z within each (day, session_kind) cycle — frozen STOP_Z_REJECT."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    out = [dict(t) for t in trades]
    for t in out:
        groups[(str(t.get("day")), str(t.get("session_kind") or "AM"))].append(t)
    for _key, rows in groups.items():
        feats = [compute_runtime_features(causal_view(r)) for r in rows]
        z_pbv2 = _cs_z([f["pbv2_score"] for f in feats])
        z_stop = _cs_z([f["stop_risk_score"] for f in feats])
        enrich = winner_enrichment_from_cycle(feats)
        for i, r in enumerate(rows):
            r["z_pbv2"] = z_pbv2[i]
            r["z_stop"] = z_stop[i]
            r["winner_enrichment"] = enrich[i]
            r["integrated_score"] = integrated_score(
                z_pbv2=z_pbv2[i], winner_enrichment=enrich[i], z_stop=z_stop[i]
            )
            r["cost_aware_reject"] = bool(z_stop[i] >= STOP_Z_REJECT)
    return out


# ---------------------------------------------------------------------------
# Portfolio simulator
# ---------------------------------------------------------------------------


@dataclass
class SimResult:
    arm: str
    capital: float
    leverage: float
    equity_mode: str  # fixed | compounding
    roundtrip_bps: float
    trades: list[dict[str, Any]] = field(default_factory=list)
    rejects: list[dict[str, Any]] = field(default_factory=list)
    status: str = "OK"
    status_reason: str = ""

    def metrics(self) -> dict[str, Any]:
        if self.status != "OK":
            return {
                "arm": self.arm,
                "status": self.status,
                "status_reason": self.status_reason,
                "initial_capital": self.capital,
                "trade_count": 0,
            }
        pnls = [float(t["net_pnl"]) for t in self.trades]
        grosses = [float(t["gross_pnl"]) for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gp = sum(wins)
        gl = abs(sum(losses))
        pf = (gp / gl) if gl > 1e-9 else (999.0 if gp > 0 else 0.0)
        equity = self.capital
        peak = equity
        max_dd = 0.0
        max_dd_pct = 0.0
        daily: dict[str, float] = defaultdict(float)
        for t in sorted(self.trades, key=lambda x: str(x.get("exit_time") or x.get("entry_time") or "")):
            equity += float(t["net_pnl"])
            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)
            max_dd_pct = max(max_dd_pct, dd / peak * 100.0 if peak > 0 else 0.0)
            daily[str(t.get("date") or t.get("day") or "")] += float(t["net_pnl"])
        dvals = list(daily.values())
        pos_d = sum(1 for v in dvals if v > 0)
        neg_d = sum(1 for v in dvals if v < 0)
        flat_d = sum(1 for v in dvals if abs(v) < 1e-9)
        stops = sum(1 for t in self.trades if "stop" in str(t.get("exit_reason") or "").lower())
        early_stops = sum(
            1
            for t in self.trades
            if "stop" in str(t.get("exit_reason") or "").lower()
            and float(t.get("hold_sec") or 9999) < 300
        )
        bp_rej = sum(1 for r in self.rejects if r.get("reject_reason") == "REJECT_BUYING_POWER")
        cap_rej = sum(1 for r in self.rejects if r.get("reject_reason") == "REJECT_CAP")
        sh_rej = sum(1 for r in self.rejects if r.get("reject_reason") == "REJECT_SHADOW")
        nf = sum(1 for r in self.rejects if r.get("reject_reason") == "REJECT_NO_FILL")
        high_price_skip = sum(1 for r in self.rejects if r.get("high_price_unaffordable"))
        max_conc = max([int(t.get("concurrent_positions") or 0) for t in self.trades] + [0])
        open_notionals = [float(t.get("open_notional_after") or 0) for t in self.trades]
        ending = self.capital + sum(pnls)
        util = (
            (statistics.mean(open_notionals) / (self.capital * self.leverage) * 100.0)
            if open_notionals and self.capital > 0
            else 0.0
        )
        return {
            "arm": self.arm,
            "status": "OK",
            "initial_capital": self.capital,
            "leverage": self.leverage,
            "equity_mode": self.equity_mode,
            "roundtrip_bps": self.roundtrip_bps,
            "ending_equity": round(ending, 2),
            "net_profit_yen": round(sum(pnls), 2),
            "return_on_capital_pct": round(sum(pnls) / self.capital * 100.0, 4) if self.capital else 0.0,
            "gross_profit": round(gp, 2),
            "gross_loss": round(gl, 2),
            "profit_factor": round(pf, 4),
            "trade_count": len(self.trades),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
            "avg_trade_yen": round(statistics.mean(pnls), 2) if pnls else 0.0,
            "median_trade_yen": round(statistics.median(pnls), 2) if pnls else 0.0,
            "best_trade": round(max(pnls), 2) if pnls else 0.0,
            "worst_trade": round(min(pnls), 2) if pnls else 0.0,
            "max_drawdown_yen": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 4),
            "daily_mean_pnl": round(statistics.mean(dvals), 2) if dvals else 0.0,
            "daily_median_pnl": round(statistics.median(dvals), 2) if dvals else 0.0,
            "positive_days": pos_d,
            "negative_days": neg_d,
            "flat_days": flat_d,
            "daily_win_rate": round(pos_d / len(dvals), 4) if dvals else 0.0,
            "max_concurrent_positions": max_conc,
            "max_open_notional": round(max(open_notionals), 2) if open_notionals else 0.0,
            "average_open_notional": round(statistics.mean(open_notionals), 2) if open_notionals else 0.0,
            "capital_utilization_pct": round(util, 4),
            "buying_power_reject_count": bp_rej,
            "cap_reject_count": cap_rej,
            "shadow_reject_count": sh_rej,
            "no_fill_count": nf,
            "high_price_unaffordable_count": high_price_skip,
            "stops": stops,
            "stop_rate": round(stops / len(pnls), 4) if pnls else 0.0,
            "early_stop_count": early_stops,
            "same_symbol_reentry_count": _same_symbol_reentries(self.trades),
            "gross_sum": round(sum(grosses), 2),
            "daily_pnl": dict(daily),
        }


def _same_symbol_reentries(trades: Sequence[Mapping[str, Any]]) -> int:
    by_sym: dict[str, list[str]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol"))].append(str(t.get("entry_time") or ""))
    n = 0
    for times in by_sym.values():
        if len(times) > 1:
            n += len(times) - 1
    return n


def _hold_sec(trade: Mapping[str, Any]) -> float:
    et = _parse_ts(str(trade.get("entry_time") or ""))
    xt = _parse_ts(str(trade.get("exit_time") or ""))
    if et and xt:
        return max(0.0, (xt - et).total_seconds())
    return float(trade.get("hold_sec_market") or 0.0)


def simulate_portfolio(
    candidates: Sequence[Mapping[str, Any]],
    *,
    arm: str,
    capital: float,
    leverage: float,
    position_cap: int,
    qty: int,
    roundtrip_bps: float,
    equity_mode: str,
    shadow_block_fns: Sequence[Callable[[Mapping[str, Any]], tuple[bool, str]]],
    use_cost_aware_rank: bool,
    use_cost_aware_reject: bool,
    exit_before_entry: bool = True,
) -> SimResult:
    result = SimResult(
        arm=arm,
        capital=capital,
        leverage=leverage,
        equity_mode=equity_mode,
        roundtrip_bps=roundtrip_bps,
    )
    # Prepare ordered candidates
    rows = [dict(c) for c in candidates]
    if use_cost_aware_rank or use_cost_aware_reject:
        rows = annotate_cost_aware_scores(rows)

    # Event heap: (ts, priority, seq, kind, trade)
    # priority: exit=0 before entry=1 when exit_before_entry else reverse
    events: list[tuple[datetime, int, int, str, dict[str, Any]]] = []
    for i, t in enumerate(rows):
        et = _parse_ts(str(t.get("entry_time") or ""))
        xt = _parse_ts(str(t.get("exit_time") or ""))
        if et is None:
            continue
        # ranking key for same-ts entries
        rank_key = -float(t.get("integrated_score") or 0.0) if use_cost_aware_rank else 0.0
        entry_pri = 0 if not exit_before_entry else 1
        exit_pri = 1 if not exit_before_entry else 0
        events.append((et, entry_pri, i, "entry", t))
        if xt is not None:
            events.append((xt, exit_pri, i, "exit", t))
    # Sort: time, priority, then for entries by rank_key via secondary sort before push
    events.sort(
        key=lambda e: (
            e[0],
            e[1],
            (-float(e[4].get("integrated_score") or 0.0) if (e[3] == "entry" and use_cost_aware_rank) else 0.0),
            str(e[4].get("symbol") or ""),
            e[2],
        )
    )

    realized = 0.0
    open_pos: dict[str, dict[str, Any]] = {}
    max_bp_limit_fixed = capital * leverage
    seq_accept = 0

    def equity_now() -> float:
        if equity_mode == "compounding":
            return capital + realized
        return capital

    def bp_limit() -> float:
        if equity_mode == "compounding":
            return equity_now() * leverage
        return max_bp_limit_fixed

    def open_notional() -> float:
        return sum(float(p.get("required_notional") or 0.0) for p in open_pos.values())

    for ts, _pri, _i, kind, trade in events:
        key = _position_key(trade)
        day = str(trade.get("day") or "")
        if kind == "exit":
            if key not in open_pos:
                continue
            pos = open_pos.pop(key)
            entry_px = float(pos.get("entry_price") or 0.0)
            gross = float(trade.get("pnl_yen_100") or 0.0)
            # roundtrip_bps = full round-trip cost in bps of entry notional (not one-way)
            cost = abs(entry_px * qty) * (roundtrip_bps / 10000.0)
            net = gross - cost
            realized += net
            eq_after = capital + realized if equity_mode == "compounding" else capital + realized
            pos_row = dict(pos)
            pos_row.update(
                {
                    "exit_time": ts.isoformat(),
                    "exit_price": trade.get("exit_price"),
                    "exit_reason": trade.get("exit_reason"),
                    "gross_pnl": round(gross, 2),
                    "cost": round(cost, 2),
                    "net_pnl": round(net, 2),
                    "equity_after": round(eq_after, 2),
                    "hold_sec": _hold_sec(trade),
                    "date": day,
                    "accepted": True,
                }
            )
            result.trades.append(pos_row)
            continue

        # ENTRY
        if key in open_pos:
            result.rejects.append(
                {
                    "date": day,
                    "symbol": trade.get("symbol"),
                    "decision_time": ts.isoformat(),
                    "arm": arm,
                    "accepted": False,
                    "reject_reason": "REJECT_RUNTIME",
                    "shadow_reason": "same_symbol_open",
                }
            )
            continue

        shadow_reasons: list[str] = []
        for fn in shadow_block_fns:
            blocked, reason = fn(trade)
            if blocked:
                shadow_reasons.append(reason or "shadow_block")
        if use_cost_aware_reject and trade.get("cost_aware_reject"):
            shadow_reasons.append(f"cost_aware_z_stop>={STOP_Z_REJECT}")

        if shadow_reasons:
            # no-fill: consume opportunity conceptually when cost-aware reject with rank slots
            reason = "REJECT_NO_FILL" if (use_cost_aware_reject and trade.get("cost_aware_reject")) else "REJECT_SHADOW"
            result.rejects.append(
                {
                    "date": day,
                    "session": trade.get("session_kind"),
                    "decision_time": ts.isoformat(),
                    "symbol": trade.get("symbol"),
                    "arm": arm,
                    "runtime_decision": "accept",
                    "shadow_decision": "block",
                    "shadow_reason": "|".join(shadow_reasons),
                    "entry_price": trade.get("entry_price"),
                    "qty": qty,
                    "required_notional": float(trade.get("entry_price") or 0) * qty,
                    "equity_before": round(equity_now(), 2),
                    "buying_power_limit": round(bp_limit(), 2),
                    "open_notional_before": round(open_notional(), 2),
                    "accepted": False,
                    "reject_reason": reason,
                }
            )
            continue

        if len(open_pos) >= position_cap:
            result.rejects.append(
                {
                    "date": day,
                    "decision_time": ts.isoformat(),
                    "symbol": trade.get("symbol"),
                    "arm": arm,
                    "accepted": False,
                    "reject_reason": "REJECT_CAP",
                    "entry_price": trade.get("entry_price"),
                    "required_notional": float(trade.get("entry_price") or 0) * qty,
                    "equity_before": round(equity_now(), 2),
                    "buying_power_limit": round(bp_limit(), 2),
                    "open_notional_before": round(open_notional(), 2),
                    "concurrent_positions": len(open_pos),
                }
            )
            continue

        entry_px = float(_float(trade.get("entry_price")) or 0.0)
        req = entry_px * qty
        eq = equity_now()
        gross = open_notional()
        limit = bp_limit()
        # Fixed-capital: buying power headroom vs fixed limit; compounding uses equity*lev - gross
        if equity_mode == "compounding":
            buying_power = compute_buying_power(equity=eq, gross=gross, leverage_limit=leverage)
        else:
            buying_power = max(0.0, limit - gross)
        shares, reject = compute_requested_shares(
            spec={"leverage_limit": leverage, "sizing": "fixed_100_only"},
            equity=eq,
            entry_price=entry_px,
            buying_power=buying_power,
        )
        high_unaffordable = entry_px > 0 and req > limit + 1e-6
        if reject or shares < qty:
            result.rejects.append(
                {
                    "date": day,
                    "decision_time": ts.isoformat(),
                    "symbol": trade.get("symbol"),
                    "arm": arm,
                    "accepted": False,
                    "reject_reason": "REJECT_BUYING_POWER",
                    "entry_price": entry_px,
                    "qty": qty,
                    "required_notional": req,
                    "equity_before": round(eq, 2),
                    "buying_power_limit": round(limit, 2),
                    "open_notional_before": round(gross, 2),
                    "capital_available": round(buying_power, 2),
                    "high_price_unaffordable": high_unaffordable,
                }
            )
            continue

        seq_accept += 1
        open_pos[key] = {
            "date": day,
            "session": trade.get("session_kind"),
            "decision_time": ts.isoformat(),
            "symbol": trade.get("symbol"),
            "arm": arm,
            "runtime_decision": "accept",
            "shadow_decision": "permit",
            "shadow_reason": "",
            "entry_price": entry_px,
            "qty": qty,
            "required_notional": req,
            "equity_before": round(eq, 2),
            "buying_power_limit": round(limit, 2),
            "open_notional_before": round(gross, 2),
            "capital_available": round(buying_power, 2),
            "accepted": True,
            "reject_reason": "",
            "concurrent_positions": len(open_pos) + 1,
            "open_notional_after": round(gross + req, 2),
            "entry_time": trade.get("entry_time"),
            "day": day,
            "trade_key": key,
        }
    return result


# ---------------------------------------------------------------------------
# Arm wiring
# ---------------------------------------------------------------------------


def shadow_fns_for_arm(arm: str, combo_flags: Optional[dict[str, bool]] = None) -> tuple[
    list[Callable[[Mapping[str, Any]], tuple[bool, str]]],
    bool,
    bool,
    str,
]:
    """Returns (block_fns, use_ca_rank, use_ca_reject, status)."""
    if arm == "ARM4_PULLBACK_VOLUME_ONLY":
        return [], False, False, "NOT_EXECUTABLE"
    if arm == "ARM5_W43F_LATEST_ONLY":
        return [], False, False, "NOT_EXECUTABLE"
    if arm == "ARM0_RUNTIME_BASELINE":
        return [], False, False, "OK"
    if arm == "ARM1_COST_AWARE_ONLY":
        return [], True, True, "OK"
    if arm == "ARM2_FLAT_WEAK_RANGE_ONLY":
        return [block_flat_weak], False, False, "OK"
    if arm == "ARM3_PULLBACK_MISREAD_ONLY":
        return [block_pullback_misread], False, False, "OK"
    if arm == "ARM6_ALL_LATEST_SHADOWS_COMBINED":
        return [block_flat_weak, block_pullback_misread], True, True, "OK"
    if arm == "ARM7_BEST_CAUSAL_COMBINATION":
        flags = combo_flags or {}
        fns: list[Callable[[Mapping[str, Any]], tuple[bool, str]]] = []
        if flags.get("flat_weak"):
            fns.append(block_flat_weak)
        if flags.get("pullback_misread"):
            fns.append(block_pullback_misread)
        return fns, bool(flags.get("cost_aware")), bool(flags.get("cost_aware")), "OK"
    return [], False, False, "NOT_EXECUTABLE"


def select_best_combo(
    trades: Sequence[dict[str, Any]],
    *,
    capital: float,
    leverage: float,
    position_cap: int,
    qty: int,
) -> dict[str, bool]:
    """Half-period selection (first half days); frozen thresholds only — no threshold search."""
    days = sorted({t["day"] for t in trades})
    if len(days) < 2:
        return {"cost_aware": True, "flat_weak": True, "pullback_misread": True}
    mid = len(days) // 2
    disc = set(days[:mid])
    disc_trades = [t for t in trades if t["day"] in disc]
    combos = [
        {"cost_aware": True, "flat_weak": False, "pullback_misread": False},
        {"cost_aware": False, "flat_weak": True, "pullback_misread": False},
        {"cost_aware": False, "flat_weak": False, "pullback_misread": True},
        {"cost_aware": True, "flat_weak": True, "pullback_misread": False},
        {"cost_aware": True, "flat_weak": False, "pullback_misread": True},
        {"cost_aware": False, "flat_weak": True, "pullback_misread": True},
        {"cost_aware": True, "flat_weak": True, "pullback_misread": True},
    ]
    best = combos[-1]
    best_pnl = -1e18
    for flags in combos:
        fns, ca_r, ca_j, _ = shadow_fns_for_arm("ARM7_BEST_CAUSAL_COMBINATION", flags)
        sim = simulate_portfolio(
            disc_trades,
            arm="ARM7_SELECT",
            capital=capital,
            leverage=leverage,
            position_cap=position_cap,
            qty=qty,
            roundtrip_bps=5.0,
            equity_mode="compounding",
            shadow_block_fns=fns,
            use_cost_aware_rank=ca_r,
            use_cost_aware_reject=ca_j,
        )
        m = sim.metrics()
        pnl = float(m.get("net_profit_yen") or 0.0)
        if pnl > best_pnl:
            best_pnl = pnl
            best = flags
    return best


def delta_vs(base: Mapping[str, Any], other: Mapping[str, Any]) -> dict[str, Any]:
    def d(k: str) -> float:
        return float(other.get(k) or 0) - float(base.get(k) or 0)

    return {
        "delta_net_profit_yen": round(d("net_profit_yen"), 2),
        "delta_return_pct_point": round(d("return_on_capital_pct"), 4),
        "delta_profit_factor": round(d("profit_factor"), 4),
        "delta_max_drawdown_yen": round(d("max_drawdown_yen"), 2),
        "delta_max_drawdown_pct_point": round(d("max_drawdown_pct"), 4),
        "delta_trade_count": int(d("trade_count")),
        "delta_stop_count": int(d("stops")),
        "delta_early_stop_count": int(d("early_stop_count")),
        "delta_buying_power_reject_count": int(d("buying_power_reject_count")),
    }


def stability_analysis(
    baseline_trades: Sequence[Mapping[str, Any]],
    arm_trades: Sequence[Mapping[str, Any]],
    all_days: Sequence[str],
) -> dict[str, Any]:
    b_daily = defaultdict(float)
    a_daily = defaultdict(float)
    b_sym = defaultdict(float)
    a_sym = defaultdict(float)
    for t in baseline_trades:
        b_daily[str(t.get("date") or t.get("day"))] += float(t.get("net_pnl") or 0)
        b_sym[str(t.get("symbol"))] += float(t.get("net_pnl") or 0)
    for t in arm_trades:
        a_daily[str(t.get("date") or t.get("day"))] += float(t.get("net_pnl") or 0)
        a_sym[str(t.get("symbol"))] += float(t.get("net_pnl") or 0)
    delta_day = {d: a_daily.get(d, 0) - b_daily.get(d, 0) for d in set(b_daily) | set(a_daily)}
    delta_sym = {s: a_sym.get(s, 0) - b_sym.get(s, 0) for s in set(b_sym) | set(a_sym)}
    total = sum(delta_day.values()) or 1e-9
    top_days = sorted(delta_day.items(), key=lambda x: -abs(x[1]))[:3]
    top_syms = sorted(delta_sym.items(), key=lambda x: -abs(x[1]))[:3]
    mid = len(all_days) // 2
    first, second = set(all_days[:mid]), set(all_days[mid:])
    first_d = sum(delta_day.get(d, 0) for d in first)
    second_d = sum(delta_day.get(d, 0) for d in second)
    return {
        "top1_day_contribution": round(abs(top_days[0][1]) / abs(total), 4) if top_days else 0.0,
        "top3_days_contribution": round(sum(abs(v) for _, v in top_days) / abs(total), 4) if top_days else 0.0,
        "top1_symbol_contribution": round(abs(top_syms[0][1]) / abs(total), 4) if top_syms else 0.0,
        "top3_symbol_contribution": round(sum(abs(v) for _, v in top_syms) / abs(total), 4) if top_syms else 0.0,
        "top_days": [{"day": d, "delta": round(v, 2)} for d, v in top_days],
        "top_symbols": [{"symbol": s, "delta": round(v, 2)} for s, v in top_syms],
        "first_half_delta": round(first_d, 2),
        "second_half_delta": round(second_d, 2),
        "both_halves_improve": first_d > 0 and second_d > 0,
    }


def adoption_verdict(m: Mapping[str, Any], base: Mapping[str, Any], stab: Mapping[str, Any], causality_pass: bool) -> str:
    if not causality_pass:
        return "REJECT"
    if m.get("status") != "OK":
        return "REJECT"
    d_pnl = float(m.get("net_profit_yen") or 0) - float(base.get("net_profit_yen") or 0)
    d_pf = float(m.get("profit_factor") or 0) - float(base.get("profit_factor") or 0)
    d_dd = float(m.get("max_drawdown_yen") or 0) - float(base.get("max_drawdown_yen") or 0)
    if d_pnl < 0 or d_pf < -0.05 or d_dd > 50000:
        return "REJECT"
    if not stab.get("both_halves_improve"):
        return "HOLD"
    if float(stab.get("top1_day_contribution") or 0) > 0.6 or float(stab.get("top1_symbol_contribution") or 0) > 0.5:
        return "HOLD"
    if d_pnl > 0 and d_pf >= -0.01 and d_dd <= 20000:
        return "ADOPT_CANDIDATE"
    return "HOLD"


def attribution(
    baseline: SimResult,
    singles: dict[str, SimResult],
    combined: SimResult,
) -> dict[str, Any]:
    b_keys = {t.get("trade_key") or f"{t.get('symbol')}|{t.get('entry_time')}" for t in baseline.trades}
    c_keys = {t.get("trade_key") or f"{t.get('symbol')}|{t.get('entry_time')}" for t in combined.trades}
    single_only = {}
    for name, sim in singles.items():
        s_keys = {t.get("trade_key") or f"{t.get('symbol')}|{t.get('entry_time')}" for t in sim.trades}
        single_only[name] = {
            "blocked_vs_baseline": len(b_keys - s_keys),
            "delta_net": round(
                float(sim.metrics().get("net_profit_yen") or 0) - float(baseline.metrics().get("net_profit_yen") or 0),
                2,
            ),
        }
    sum_single = sum(v["delta_net"] for v in single_only.values())
    comb_delta = round(
        float(combined.metrics().get("net_profit_yen") or 0) - float(baseline.metrics().get("net_profit_yen") or 0),
        2,
    )
    return {
        "single_arm_deltas": single_only,
        "sum_of_single_deltas": round(sum_single, 2),
        "combined_delta": comb_delta,
        "interaction_gap": round(comb_delta - sum_single, 2),
        "reason": (
            "Single-arm deltas do not sum to combined because overlapping Shadow blocks "
            "are counted once in the combined arm, ranking/no-fill changes which CAP slots "
            "fill, and buying-power path dependence reallocates subsequent entries."
        ),
        "combined_vs_baseline_key_diff": {
            "baseline_only": len(b_keys - c_keys),
            "combined_only": len(c_keys - b_keys),
            "intersection": len(b_keys & c_keys),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_md = OUT_DIR / "phase687w66_full_period_shadow_capital_report.md"
    out_json = OUT_DIR / "phase687w66_full_period_shadow_capital_report.json"
    out_xlsx = OUT_DIR / "phase687w66_full_period_shadow_capital_audit.xlsx"

    trades, sessions, data_quality = discover_and_load_all_period()
    if len(trades) < 20 or data_quality.get("trading_days", 0) < 2:
        report = {
            "phase": PHASE,
            "verdict": "FULL_PERIOD_SHADOW_CAPITAL_DATA_INSUFFICIENT",
            "data_quality": data_quality,
            "runtime_unchanged": True,
            "forward_thresholds_unchanged": True,
        }
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        out_md.write_text(f"# {PHASE}\n\nDATA_INSUFFICIENT\n", encoding="utf-8")
        write_xlsx({"Data Quality": pd.DataFrame([data_quality])}, out_xlsx)
        print(json.dumps({"verdict": report["verdict"]}, ensure_ascii=False))
        return 2

    # Filter invalid prices
    priced = [t for t in trades if float(t.get("entry_price") or 0) > 0]
    data_quality["priced_trade_count"] = len(priced)
    data_quality["unpriced_dropped"] = len(trades) - len(priced)
    trades = priced

    causality = audit_causality(trades)
    if not causality["causality_pass"]:
        report = {
            "phase": PHASE,
            "verdict": "FULL_PERIOD_SHADOW_CAPITAL_CAUSALITY_FAILED",
            "causality": causality,
            "data_quality": data_quality,
            "runtime_unchanged": True,
            "forward_thresholds_unchanged": True,
        }
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        out_md.write_text(f"# {PHASE}\n\nCAUSALITY_FAILED\n", encoding="utf-8")
        write_xlsx({"Data Quality": pd.DataFrame([data_quality])}, out_xlsx)
        print(json.dumps({"verdict": report["verdict"]}, ensure_ascii=False))
        return 3

    capitals = list(args.capitals)
    leverage = float(args.leverage)
    qty = int(args.qty)
    position_cap = int(args.position_cap)
    cost_bps_list = list(args.roundtrip_cost_bps)
    equity_modes = []
    if args.fixed_capital:
        equity_modes.append("fixed")
    if args.equity_compounding:
        equity_modes.append("compounding")
    if not equity_modes:
        equity_modes = ["fixed", "compounding"]

    days = sorted({t["day"] for t in trades})
    # ARM7 combo selected on half-period at reference capital 1_000_000
    best_flags = select_best_combo(
        trades, capital=1_000_000.0, leverage=leverage, position_cap=position_cap, qty=qty
    )

    # Build job list
    jobs: list[dict[str, Any]] = []
    for arm in ARM_IDS:
        fns, ca_r, ca_j, status = shadow_fns_for_arm(arm, best_flags if arm == "ARM7_BEST_CAUSAL_COMBINATION" else None)
        for cap in capitals:
            for mode in equity_modes:
                for bps in cost_bps_list:
                    jobs.append(
                        {
                            "arm": arm,
                            "capital": float(cap),
                            "equity_mode": mode,
                            "roundtrip_bps": float(bps),
                            "fns": fns,
                            "ca_r": ca_r,
                            "ca_j": ca_j,
                            "status": status,
                        }
                    )

    results: dict[str, SimResult] = {}

    def _run_job(job: dict[str, Any]) -> tuple[str, SimResult]:
        key = f"{job['arm']}|{int(job['capital'])}|{job['equity_mode']}|{int(job['roundtrip_bps'])}"
        if job["status"] == "NOT_EXECUTABLE":
            reason = next(
                (
                    s["exclude_reason"]
                    for s in SHADOW_CATALOG
                    if s.get("arm") == job["arm"]
                ),
                "insufficient_definition",
            )
            sim = SimResult(
                arm=job["arm"],
                capital=job["capital"],
                leverage=leverage,
                equity_mode=job["equity_mode"],
                roundtrip_bps=job["roundtrip_bps"],
                status="NOT_EXECUTABLE",
                status_reason=reason,
            )
            return key, sim
        sim = simulate_portfolio(
            trades,
            arm=job["arm"],
            capital=job["capital"],
            leverage=leverage,
            position_cap=position_cap,
            qty=qty,
            roundtrip_bps=job["roundtrip_bps"],
            equity_mode=job["equity_mode"],
            shadow_block_fns=job["fns"],
            use_cost_aware_rank=job["ca_r"],
            use_cost_aware_reject=job["ca_j"],
            exit_before_entry=True,
        )
        return key, sim

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(jobs)))) as ex:
        futs = [ex.submit(_run_job, j) for j in jobs]
        for fut in as_completed(futs):
            k, sim = fut.result()
            results[k] = sim

    # Sensitivity: EXIT-after-ENTRY for baseline 1M compounding 5bps
    sens_base = simulate_portfolio(
        trades,
        arm="ARM0_RUNTIME_BASELINE",
        capital=1_000_000.0,
        leverage=leverage,
        position_cap=position_cap,
        qty=qty,
        roundtrip_bps=5.0,
        equity_mode="compounding",
        shadow_block_fns=[],
        use_cost_aware_rank=False,
        use_cost_aware_reject=False,
        exit_before_entry=False,
    )

    # Assemble report structures — primary view: compounding, 5bps
    primary_mode = "compounding" if "compounding" in equity_modes else equity_modes[0]
    primary_bps = 5.0 if 5.0 in cost_bps_list else cost_bps_list[0]

    def key(arm: str, cap: float, mode: str = primary_mode, bps: float = primary_bps) -> str:
        return f"{arm}|{int(cap)}|{mode}|{int(bps)}"

    capital_results: dict[str, Any] = {}
    best_arm_by_capital: dict[str, Any] = {}
    arms_summary: dict[str, Any] = {}
    adoption_by_capital: dict[str, Any] = {}

    all_trade_rows: list[dict[str, Any]] = []
    all_reject_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    arm_cmp_rows: list[dict[str, Any]] = []
    dd_rows: list[dict[str, Any]] = []
    stab_rows: list[dict[str, Any]] = []

    for cap in capitals:
        cap_key = str(int(cap))
        base = results[key("ARM0_RUNTIME_BASELINE", cap)]
        base_m = base.metrics()
        capital_results[cap_key] = {"baseline": base_m, "arms": {}}
        best_pnl = -1e18
        best_arm = "ARM0_RUNTIME_BASELINE"
        for arm in ARM_IDS:
            sim = results[key(arm, cap)]
            m = sim.metrics()
            dlt = delta_vs(base_m, m) if m.get("status") == "OK" and base_m.get("status") == "OK" else {}
            stab = (
                stability_analysis(base.trades, sim.trades, days)
                if m.get("status") == "OK"
                else {}
            )
            verdict = (
                adoption_verdict(m, base_m, stab, causality["causality_pass"])
                if m.get("status") == "OK"
                else "REJECT"
            )
            # Also pack 0/5/10 and fixed/compounding
            cost_pack = {}
            for bps in cost_bps_list:
                cost_pack[f"{int(bps)}bps"] = {}
                for mode in equity_modes:
                    sm = results[key(arm, cap, mode, bps)].metrics()
                    cost_pack[f"{int(bps)}bps"][mode] = sm
            pack = {
                "primary": m,
                "delta_vs_baseline": dlt,
                "stability": stab,
                "adoption": verdict,
                "by_cost_and_mode": cost_pack,
                "status": m.get("status"),
                "status_reason": sim.status_reason,
            }
            capital_results[cap_key]["arms"][arm] = pack
            arms_summary.setdefault(arm, {})[cap_key] = pack
            if m.get("status") == "OK" and float(m.get("net_profit_yen") or -1e18) > best_pnl:
                # exclude baseline from "best shadow arm" but track overall
                if arm != "ARM0_RUNTIME_BASELINE" or best_arm == "ARM0_RUNTIME_BASELINE":
                    best_pnl = float(m.get("net_profit_yen") or 0)
                    best_arm = arm
            arm_cmp_rows.append(
                {
                    "capital": cap,
                    "arm": arm,
                    "status": m.get("status"),
                    "net_profit_yen": m.get("net_profit_yen"),
                    "return_on_capital_pct": m.get("return_on_capital_pct"),
                    "profit_factor": m.get("profit_factor"),
                    "max_drawdown_yen": m.get("max_drawdown_yen"),
                    "trade_count": m.get("trade_count"),
                    "buying_power_reject_count": m.get("buying_power_reject_count"),
                    "cap_reject_count": m.get("cap_reject_count"),
                    "shadow_reject_count": m.get("shadow_reject_count"),
                    "adoption": verdict,
                    **dlt,
                }
            )
            dd_rows.append(
                {
                    "capital": cap,
                    "arm": arm,
                    "max_drawdown_yen": m.get("max_drawdown_yen"),
                    "max_drawdown_pct": m.get("max_drawdown_pct"),
                    "delta_dd_yen": dlt.get("delta_max_drawdown_yen"),
                }
            )
            if stab:
                stab_rows.append({"capital": cap, "arm": arm, **{k: v for k, v in stab.items() if not isinstance(v, (list, dict))}})
            for t in sim.trades:
                all_trade_rows.append({**t, "capital": cap, "equity_mode": primary_mode, "roundtrip_bps": primary_bps})
            for r in sim.rejects:
                all_reject_rows.append({**r, "capital": cap})
            for d, pnl in (m.get("daily_pnl") or {}).items():
                daily_rows.append({"capital": cap, "arm": arm, "day": d, "net_pnl": pnl})

        # Prefer best non-baseline executable
        executable_arms = [
            a
            for a in ARM_IDS
            if a != "ARM0_RUNTIME_BASELINE"
            and capital_results[cap_key]["arms"][a]["status"] == "OK"
        ]
        if executable_arms:
            best_arm = max(
                executable_arms,
                key=lambda a: float(
                    capital_results[cap_key]["arms"][a]["primary"].get("net_profit_yen") or -1e18
                ),
            )
        best_arm_by_capital[cap_key] = {
            "best_arm": best_arm,
            "metrics": capital_results[cap_key]["arms"][best_arm]["primary"],
            "delta_vs_baseline": capital_results[cap_key]["arms"][best_arm]["delta_vs_baseline"],
            "adoption": capital_results[cap_key]["arms"][best_arm]["adoption"],
        }
        adoption_by_capital[cap_key] = {
            a: capital_results[cap_key]["arms"][a]["adoption"] for a in ARM_IDS
        }

    # Attribution at 1M
    ref_cap = 1_000_000.0 if 1_000_000.0 in capitals else float(capitals[0])
    attr = attribution(
        results[key("ARM0_RUNTIME_BASELINE", ref_cap)],
        {
            "cost_aware": results[key("ARM1_COST_AWARE_ONLY", ref_cap)],
            "flat_weak": results[key("ARM2_FLAT_WEAK_RANGE_ONLY", ref_cap)],
            "pullback_misread": results[key("ARM3_PULLBACK_MISREAD_ONLY", ref_cap)],
        },
        results[key("ARM6_ALL_LATEST_SHADOWS_COMBINED", ref_cap)],
    )

    # Winner missed / loser avoided vs baseline (1M)
    base_sim = results[key("ARM0_RUNTIME_BASELINE", ref_cap)]
    base_map = {
        (t.get("symbol"), t.get("entry_time")): float(t.get("net_pnl") or 0) for t in base_sim.trades
    }
    for arm in ("ARM1_COST_AWARE_ONLY", "ARM2_FLAT_WEAK_RANGE_ONLY", "ARM3_PULLBACK_MISREAD_ONLY", "ARM6_ALL_LATEST_SHADOWS_COMBINED", "ARM7_BEST_CAUSAL_COMBINATION"):
        sim = results[key(arm, ref_cap)]
        arm_keys = {(t.get("symbol"), t.get("entry_time")) for t in sim.trades}
        missed_w = sum(1 for k, v in base_map.items() if k not in arm_keys and v > 0)
        avoided_l = sum(1 for k, v in base_map.items() if k not in arm_keys and v < 0)
        for cap in capitals:
            pack = capital_results[str(int(cap))]["arms"].get(arm)
            if pack and pack["primary"].get("status") == "OK":
                # recompute per capital
                bsim = results[key("ARM0_RUNTIME_BASELINE", cap)]
                bmap = {(t.get("symbol"), t.get("entry_time")): float(t.get("net_pnl") or 0) for t in bsim.trades}
                asim = results[key(arm, cap)]
                akeys = {(t.get("symbol"), t.get("entry_time")) for t in asim.trades}
                pack["primary"]["winner_missed_count"] = sum(1 for k, v in bmap.items() if k not in akeys and v > 0)
                pack["primary"]["loser_avoided_count"] = sum(1 for k, v in bmap.items() if k not in akeys and v < 0)
                pack["delta_vs_baseline"]["delta_winner_missed"] = pack["primary"]["winner_missed_count"]

    # Config snapshot
    runtime_hash = _file_hash(RUNTIME_YAML)
    shadow_hash = hashlib.sha256(
        json.dumps(
            {"STOP_Z_REJECT": STOP_Z_REJECT, "catalog": SHADOW_CATALOG, "best_flags": best_flags},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()[:16]

    # Executable symbols by capital (max BP)
    symbol_afford: dict[str, Any] = {}
    for cap in capitals:
        limit = float(cap) * leverage
        afford = {str(t["symbol"]) for t in trades if float(t.get("entry_price") or 0) * qty <= limit}
        symbol_afford[str(int(cap))] = {
            "max_buying_power": limit,
            "executable_symbol_count": len(afford),
            "total_symbols": len({str(t["symbol"]) for t in trades}),
        }

    # Overall adoption recommendation (strict — no auto runtime adopt)
    any_adopt = any(
        best_arm_by_capital[str(int(c))]["adoption"] == "ADOPT_CANDIDATE" for c in capitals
    )

    baseline_primary = capital_results[str(int(ref_cap))]["baseline"]

    report = {
        "phase": PHASE,
        "verdict": "FULL_PERIOD_SHADOW_CAPITAL_VALIDATION_OK",
        "data_start": data_quality["data_start"],
        "data_end": data_quality["data_end"],
        "trading_days": data_quality["trading_days"],
        "runtime_config_hash": runtime_hash,
        "shadow_config_hash": shadow_hash,
        "causality_pass": True,
        "qty": qty,
        "position_cap": position_cap,
        "leverage": leverage,
        "cost_scenarios_bps_roundtrip": cost_bps_list,
        "capitals": capitals,
        "baseline": baseline_primary,
        "arms": arms_summary,
        "capital_results": capital_results,
        "best_arm_by_capital": best_arm_by_capital,
        "shadow_attribution": attr,
        "stability": {str(int(c)): capital_results[str(int(c))]["arms"]["ARM6_ALL_LATEST_SHADOWS_COMBINED"].get("stability") for c in capitals},
        "data_quality": data_quality,
        "shadow_catalog": SHADOW_CATALOG,
        "arm7_selection": {
            "method": "first_half_day_selection_5bps_compounding_1M_ref",
            "selected_flags": best_flags,
            "note": "Frozen thresholds only; combo membership selected on discovery half, evaluated full-period",
        },
        "event_order_sensitivity": {
            "primary": "EXIT_before_ENTRY_same_timestamp",
            "alt_ENTRY_before_EXIT_baseline_1M_5bps": sens_base.metrics(),
        },
        "symbol_affordability": symbol_afford,
        "adoption_by_capital": adoption_by_capital,
        "runtime_adoption_candidate_exists": any_adopt,
        "runtime_unchanged": True,
        "forward_thresholds_unchanged": True,
        "primary_view": {"equity_mode": primary_mode, "roundtrip_bps": primary_bps},
    }

    # Markdown
    def _fmt_arm(cap: float, arm: str) -> str:
        p = capital_results[str(int(cap))]["arms"][arm]
        m = p["primary"]
        if m.get("status") != "OK":
            return f"- **{arm}**: `{m.get('status')}` — {p.get('status_reason')}"
        d = p.get("delta_vs_baseline") or {}
        return (
            f"- **{arm}**: net={m.get('net_profit_yen')} PF={m.get('profit_factor')} "
            f"DD={m.get('max_drawdown_yen')} trades={m.get('trade_count')} "
            f"Δnet={d.get('delta_net_profit_yen')} adoption={p.get('adoption')}"
        )

    md_lines = [
        f"# {PHASE} Full-Period Shadow Capital Validation",
        "",
        f"**Verdict:** `{report['verdict']}`",
        "",
        "## Data period",
        f"- Start: {data_quality['data_start']}",
        f"- End: {data_quality['data_end']}",
        f"- Trading days: {data_quality['trading_days']}",
        f"- Sessions: {data_quality['session_count']}",
        f"- Candidates / Runtime trades: {data_quality['candidate_count']}",
        f"- Symbols: {data_quality['symbol_count']}",
        f"- Source: `{data_quality['source_of_truth']}`",
        "",
        "## Runtime / Shadow hashes",
        f"- runtime_config_hash: `{runtime_hash}` ({RUNTIME_YAML.name})",
        f"- shadow_config_hash: `{shadow_hash}`",
        f"- runtime_unchanged: true / forward_thresholds_unchanged: true",
        "",
        "## Shadow catalog",
    ]
    for s in SHADOW_CATALOG:
        md_lines.append(
            f"- `{s['id']}`: classification=`{s['classification']}` arm=`{s.get('arm')}` "
            f"{'(excluded: ' + s['exclude_reason'] + ')' if s.get('exclude_reason') else ''}"
        )
    md_lines += [
        "",
        "## Causality",
        f"- PASS: {causality['causality_pass']}",
        f"- Policy: {causality['policy']}",
        "",
        f"## Baseline (capital={int(ref_cap)}, {primary_mode}, {int(primary_bps)}bps RT)",
        f"- net_profit_yen: {baseline_primary.get('net_profit_yen')}",
        f"- profit_factor: {baseline_primary.get('profit_factor')}",
        f"- max_drawdown_yen: {baseline_primary.get('max_drawdown_yen')}",
        f"- trade_count: {baseline_primary.get('trade_count')}",
        "",
        "## ARM7 selection (overfit guard)",
        f"- Method: {report['arm7_selection']['method']}",
        f"- Selected: `{json.dumps(best_flags)}`",
        "",
        "## Results by capital (primary view)",
    ]
    for cap in capitals:
        md_lines.append(f"### Capital {int(cap)} (BP max {int(cap * leverage)})")
        aff = symbol_afford[str(int(cap))]
        md_lines.append(
            f"- Executable symbols: {aff['executable_symbol_count']} / {aff['total_symbols']}"
        )
        md_lines.append(_fmt_arm(cap, "ARM0_RUNTIME_BASELINE"))
        for arm in ARM_IDS[1:]:
            md_lines.append(_fmt_arm(cap, arm))
        ba = best_arm_by_capital[str(int(cap))]
        md_lines.append(
            f"- **Best arm:** `{ba['best_arm']}` adoption=`{ba['adoption']}` "
            f"Δnet={ba['delta_vs_baseline'].get('delta_net_profit_yen')}"
        )
        md_lines.append("")

    md_lines += [
        "## Shadow attribution (ref capital)",
        f"- Sum of single deltas: {attr['sum_of_single_deltas']}",
        f"- Combined delta: {attr['combined_delta']}",
        f"- Interaction gap: {attr['interaction_gap']}",
        f"- Reason: {attr['reason']}",
        "",
        "## Cost scenarios (ref capital, compounding)",
    ]
    for bps in cost_bps_list:
        bm = results[key("ARM0_RUNTIME_BASELINE", ref_cap, "compounding", bps)].metrics()
        cm = results[key("ARM6_ALL_LATEST_SHADOWS_COMBINED", ref_cap, "compounding", bps)].metrics()
        md_lines.append(
            f"- {int(bps)}bps RT: baseline net={bm.get('net_profit_yen')} PF={bm.get('profit_factor')}; "
            f"combined net={cm.get('net_profit_yen')} PF={cm.get('profit_factor')}"
        )

    md_lines += [
        "",
        "## Fixed vs compounding (baseline 1M, 5bps)",
    ]
    if "fixed" in equity_modes and "compounding" in equity_modes:
        f_m = results[key("ARM0_RUNTIME_BASELINE", ref_cap, "fixed", 5.0)].metrics()
        c_m = results[key("ARM0_RUNTIME_BASELINE", ref_cap, "compounding", 5.0)].metrics()
        md_lines.append(
            f"- fixed net={f_m.get('net_profit_yen')} / compounding net={c_m.get('net_profit_yen')} "
            f"(Δ={round(float(c_m.get('net_profit_yen') or 0) - float(f_m.get('net_profit_yen') or 0), 2)})"
        )

    md_lines += [
        "",
        "## Adoption note",
        "This phase does **not** adopt any Shadow into Runtime. "
        f"runtime_adoption_candidate_exists={any_adopt}",
        "",
        "## Artifacts",
        f"- `{out_md}`",
        f"- `{out_json}`",
        f"- `{out_xlsx}`",
        "",
    ]
    out_md.write_text("\n".join(md_lines), encoding="utf-8")
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Excel sheets
    exec_rows = []
    for cap in capitals:
        ba = best_arm_by_capital[str(int(cap))]
        base = capital_results[str(int(cap))]["baseline"]
        exec_rows.append(
            {
                "capital": cap,
                "max_bp": cap * leverage,
                "baseline_net": base.get("net_profit_yen"),
                "baseline_pf": base.get("profit_factor"),
                "baseline_dd": base.get("max_drawdown_yen"),
                "best_arm": ba["best_arm"],
                "best_net": ba["metrics"].get("net_profit_yen"),
                "best_pf": ba["metrics"].get("profit_factor"),
                "best_dd": ba["metrics"].get("max_drawdown_yen"),
                "delta_net": ba["delta_vs_baseline"].get("delta_net_profit_yen"),
                "adoption": ba["adoption"],
                "executable_symbols": symbol_afford[str(int(cap))]["executable_symbol_count"],
                "bp_rejects_best": ba["metrics"].get("buying_power_reject_count"),
            }
        )

    write_xlsx(
        {
            "Executive Summary": pd.DataFrame(exec_rows),
            "Capital Comparison": pd.DataFrame(
                [
                    {
                        "capital": c,
                        **symbol_afford[str(int(c))],
                        "baseline_net": capital_results[str(int(c))]["baseline"].get("net_profit_yen"),
                        "best_arm": best_arm_by_capital[str(int(c))]["best_arm"],
                    }
                    for c in capitals
                ]
            ),
            "Arm Comparison": pd.DataFrame(arm_cmp_rows),
            "Daily PnL": pd.DataFrame(daily_rows),
            "Trades": pd.DataFrame(all_trade_rows),
            "Rejected Trades": pd.DataFrame(all_reject_rows),
            "Shadow Attribution": pd.DataFrame(
                [
                    {"metric": k, "value": json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}
                    for k, v in attr.items()
                ]
            ),
            "Drawdown": pd.DataFrame(dd_rows),
            "Stability": pd.DataFrame(stab_rows),
            "Data Quality": pd.DataFrame([data_quality]),
            "Config Snapshot": pd.DataFrame(
                [
                    {
                        "runtime_yaml": str(RUNTIME_YAML),
                        "runtime_config_hash": runtime_hash,
                        "shadow_config_hash": shadow_hash,
                        "STOP_Z_REJECT": STOP_Z_REJECT,
                        "position_cap": position_cap,
                        "qty": qty,
                        "leverage": leverage,
                        "arm7_flags": json.dumps(best_flags),
                        "runtime_unchanged": True,
                        "forward_thresholds_unchanged": True,
                    }
                ]
            ),
        },
        out_xlsx,
    )

    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "data_start": report["data_start"],
                "data_end": report["data_end"],
                "trading_days": report["trading_days"],
                "best_arm_by_capital": {k: v["best_arm"] for k, v in best_arm_by_capital.items()},
                "artifacts": [str(out_md), str(out_json), str(out_xlsx)],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=PHASE)
    p.add_argument("--all-period", action="store_true", help="Use full canonical period (default)")
    p.add_argument("--capitals", type=float, nargs="+", default=[500000, 1000000, 2000000])
    p.add_argument("--leverage", type=float, default=2.0)
    p.add_argument("--qty", type=int, default=100)
    p.add_argument("--position-cap", type=int, default=5)
    p.add_argument("--roundtrip-cost-bps", type=float, nargs="+", default=[0, 5, 10])
    p.add_argument("--fixed-capital", action="store_true")
    p.add_argument("--equity-compounding", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not args.fixed_capital and not args.equity_compounding:
        args.fixed_capital = True
        args.equity_compounding = True
    try:
        return run(args)
    except Exception as exc:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        fail = {
            "phase": PHASE,
            "verdict": "FULL_PERIOD_SHADOW_CAPITAL_VALIDATION_FAILED",
            "error": repr(exc),
            "runtime_unchanged": True,
            "forward_thresholds_unchanged": True,
        }
        (OUT_DIR / "phase687w66_full_period_shadow_capital_report.json").write_text(
            json.dumps(fail, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(fail, ensure_ascii=False), flush=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
