#!/usr/bin/env python3
"""
Phase242: max_concurrent reject counterfactual (review only).

Goal:
Compute the "true" expectancy of candidates rejected by max_concurrent by
replaying them as virtual accepted trades using the same replay/exit logic
used for accepted trades (Phase71/83 combined_structural_exit_v1-style).

Target:
- gate_reject_reason == "max_concurrent"

Method:
- Do NOT use rejected events' pnl directly.
- For each session, inject synthetic "accepted" at the first occurrence of a
  max_concurrent candidate (keyed by symbol+entry_time), then replay the
  session event stream and generate realized_pnl_pct and close_reason.

Aggregates:
- trade_count, PF, PnL, win_rate, stop_rate
- Stratify by quality >= 0.75/0.80/0.85/0.90 (entry continuation_quality_score)

Constraints:
- review only
- no production changes
- no YAML changes
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase242_max_concurrent_counterfactual.json"

TARGET_REASON = "max_concurrent"
QUALITY_THRESHOLDS = (0.75, 0.80, 0.85, 0.90)

# Mirror Phase83 replay settings (combined_structural_exit_v1 legacy).
V1_MODE = "legacy"
V1_RATIO = 0.85


def _load_module(name: str, rel_path: str) -> Any:
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _boolish(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    """
    Prefer CSV if present because it contains gate_reject_reason fields reliably
    across older sessions; fall back to JSONL.
    """
    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        out: list[dict[str, Any]] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
    return []


def _discover_sessions(base: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for summary_path in sorted(base.rglob("small_paper_summary.json")):
        sdir = summary_path.parent
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        out.append(
            {
                "session_id": sdir.relative_to(base).as_posix(),
                "session_dir": str(sdir),
                "mode": summary.get("mode"),
                "source": summary.get("source"),
            }
        )
    return out


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _session_end(events: list[dict[str, Any]]) -> str:
    # Phase71 uses max entry_time as end; replicate with event_time fallback.
    best = ""
    best_ts = 0.0
    for ev in events:
        t = str(ev.get("entry_time") or ev.get("event_time") or "")
        ts = _parse_ts(t)
        if ts >= best_ts:
            best_ts = ts
            best = t
    return best


@dataclass
class VirtualTrade:
    symbol: str
    entry_time: str
    entry_price: float
    entry_quality: float
    close_time: str
    close_price: float
    close_reason: str
    realized_pnl_pct: float


def _extract_max_concurrent_entries(events: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """
    Return first-occurrence event per (symbol, entry_time) that was rejected due to max_concurrent.
    Uses candidate/rejected rows (same key) and prefers the first row with a usable current_price.
    """
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        reason = str(ev.get("gate_reject_reason") or "")
        if reason != TARGET_REASON:
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        if not sym or not ent:
            continue
        key = (sym, ent)
        if key in chosen:
            continue
        px = _float(ev.get("current_price"))
        if px is None or px <= 0:
            continue
        chosen[key] = ev
    return chosen


def replay_virtual_max_concurrent(p71: Any, events: list[dict[str, Any]]) -> list[VirtualTrade]:
    """
    Replay a session and create virtual trades for max_concurrent-rejected entries
    using the same per-tick logic used for accepted trades replay (Phase83).
    """
    session_end = _session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[VirtualTrade] = []

    inject = _extract_max_concurrent_entries(events)
    injected: set[tuple[str, str]] = set()

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        tr = act.trade
        pnl = p71._pnl_pct(tr.entry_price, close_price)
        completed.append(
            VirtualTrade(
                symbol=tr.symbol,
                entry_time=tr.entry_time,
                entry_price=float(tr.entry_price),
                entry_quality=float(getattr(tr, "entry_quality", 0.0)),
                close_time=close_time,
                close_price=float(close_price),
                close_reason=reason,
                realized_pnl_pct=float(pnl),
            )
        )

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(ent) if hasattr(p71, "_parse_ts") else _parse_ts(ent)
        price = _float(ev.get("current_price")) or 0.0
        if price <= 0:
            continue

        st = sym_states.setdefault(sym, p71.SymState())

        key = (sym, ent)
        if key in inject and key not in injected:
            injected.add(key)
            if sym in active:
                old = active.pop(sym)
                close_act(old, close_time=ent, close_price=float(price), reason="overlap_replaced_review")

            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
            q = _float(inject[key].get("continuation_quality_score")) or _float(ev.get("continuation_quality_score")) or 0.0
            tr = p71.StructuralTrade(sym, ent, float(price), float(q))
            active[sym] = p71.ActiveTrade(
                trade=tr,
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": float(price),
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

        et = str(ev.get("event_type") or "")
        if et == "candidate" and sym in active:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
            act.rich_ticks.append(
                {
                    "price": float(price),
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, float(price)),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=V1_MODE,
                ratio=V1_RATIO,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                close_act(act, close_time=ent, close_price=float(price), reason=str(reason))
                active.pop(sym, None)

    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return completed


def _metrics(trades: list[VirtualTrade]) -> dict[str, Any]:
    pnls = [float(t.realized_pnl_pct) for t in trades]
    n = len(pnls)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_rate": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for t in trades if str(t.close_reason) == "stop_hit")
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # Load Phase71 replay engine (used by Phase83/217/238 to replay trades).
    p71 = _load_module("phase71_engine_p242", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")

    sessions = _discover_sessions(SMALL_PAPER)
    all_trades: list[VirtualTrade] = []
    per_session: list[dict[str, Any]] = []

    for i, sess in enumerate(sessions, 1):
        sdir = Path(sess["session_dir"])
        events = _load_events(sdir)
        if not events:
            continue
        vt = replay_virtual_max_concurrent(p71, events)
        if vt:
            all_trades.extend(vt)
        per_session.append(
            {
                "session_id": sess["session_id"],
                "virtual_trade_count": len(vt),
                "mode": sess.get("mode"),
                "source": sess.get("source"),
            }
        )
        if i % 25 == 0:
            print(f"  [{i}/{len(sessions)}] scanned...", flush=True)

    def q_filter(thr: float) -> list[VirtualTrade]:
        return [t for t in all_trades if float(t.entry_quality) >= thr]

    report = {
        "phase": 242,
        "mode": "max_concurrent_counterfactual",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
        },
        "target": {"gate_reject_reason": TARGET_REASON},
        "method": {
            "virtual_trade_open": "first max_concurrent occurrence per (symbol, entry_time) injected as accepted",
            "exit_logic": "Phase71 simulate_combined_split (as used by Phase83 replay_trades_from_events)",
            "replay_inputs": "session small_paper_events.csv (preferred) / jsonl (fallback)",
        },
        "population": {
            "sessions_scanned": len(sessions),
            "virtual_trades": len(all_trades),
        },
        "metrics": {
            "all": _metrics(all_trades),
            "quality_stratified": {f"quality_ge_{thr}": _metrics(q_filter(thr)) for thr in QUALITY_THRESHOLDS},
        },
        "by_session_virtual_trade_counts": per_session,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} virtual_trades={len(all_trades)} sessions={len(sessions)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

