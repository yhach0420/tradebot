"""
Phase186: VWAP shadow reject live observation (review only).

Analyzes sessions with vwap_shadow_reject_candidate logging, or offline baseline
on Phase185 multisession set until live sessions accumulate.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from research.phase181_entry_expectancy_review import _float, _load_events, _mean, _pair_trades, _pf
from research.phase185_vwap_dev_shadow_candidate_multisession_review import (
    REFERENCE_SESSIONS,
    OBSERVER_EXIT_SESSIONS,
    COMPARE_SYMBOLS,
    FOCUS_SYMBOLS,
    discover_sessions,
    load_session_trades,
)
from small_paper.vwap_shadow_reject import VWAP_SHADOW_REJECT_MIN, compute_vwap_shadow_reject_fields

FOCUS = FOCUS_SYMBOLS
COMPARE = COMPARE_SYMBOLS


@dataclass
class ObsTrade:
    session_id: str
    symbol: str
    pnl_pct: float
    exit_reason: str
    entry_vwap_dev_pct: Optional[float]
    vwap_shadow_reject_candidate: bool

    @classmethod
    def from_vwap_review(cls, t: Any) -> "ObsTrade":
        dev = t.entry_vwap_dev_pct
        candidate = dev is not None and dev >= VWAP_SHADOW_REJECT_MIN
        return cls(
            session_id=t.session_id,
            symbol=t.symbol,
            pnl_pct=t.pnl_pct,
            exit_reason=t.exit_reason,
            entry_vwap_dev_pct=dev,
            vwap_shadow_reject_candidate=candidate,
        )

    @classmethod
    def from_event_pair(cls, session_id: str, acc: dict[str, Any], ex: dict[str, Any]) -> "ObsTrade":
        def _boolish(v: Any) -> bool:
            if v is True:
                return True
            return str(v or "").strip().lower() in ("1", "true", "yes")

        candidate = _boolish(acc.get("vwap_shadow_reject_candidate"))
        if not candidate and acc.get("entry_vwap_dev_pct") not in (None, ""):
            dev = _float(acc.get("entry_vwap_dev_pct"))
            candidate = dev is not None and dev >= VWAP_SHADOW_REJECT_MIN
        else:
            dev = _float(acc.get("entry_vwap_dev_pct"))
        pnl = _float(ex.get("pnl_pct"))
        if pnl is None:
            pnl = _float(ex.get("realized_pnl_pct")) or 0.0
        return cls(
            session_id=session_id,
            symbol=str(acc.get("symbol") or ""),
            pnl_pct=float(pnl or 0.0),
            exit_reason=str(ex.get("exit_reason") or ""),
            entry_vwap_dev_pct=dev,
            vwap_shadow_reject_candidate=candidate,
        )


def _summarize(trades: Sequence[ObsTrade]) -> dict[str, Any]:
    if not trades:
        return {"trade_count": 0}
    pnls = [t.pnl_pct for t in trades]
    pf = _pf(pnls)
    return {
        "trade_count": len(trades),
        "total_pnl_pct": round(sum(pnls), 4),
        "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "stop_hit_count": sum(1 for t in trades if t.exit_reason == "stop_hit"),
        "trailing_mfe_exit_count": sum(1 for t in trades if t.exit_reason == "trailing_mfe_exit"),
        "avg_entry_vwap_dev_pct": round(
            _mean([t.entry_vwap_dev_pct for t in trades if t.entry_vwap_dev_pct is not None]) or 0,
            4,
        ),
    }


def _false_positive(trades: Sequence[ObsTrade]) -> dict[str, Any]:
    cand = [t for t in trades if t.vwap_shadow_reject_candidate]
    fp = [t for t in cand if t.pnl_pct > 0]
    return {
        "candidate_count": len(cand),
        "false_positive_count": len(fp),
        "false_positive_rate": round(len(fp) / max(1, len(cand)), 4),
        "false_positive_total_pnl_pct": round(sum(t.pnl_pct for t in fp), 4),
    }


def _symbol_block(trades: Sequence[ObsTrade], symbols: frozenset[str]) -> dict[str, Any]:
    subset = [t for t in trades if t.symbol in symbols]
    cand = [t for t in subset if t.vwap_shadow_reject_candidate]
    non = [t for t in subset if not t.vwap_shadow_reject_candidate]
    by_sym: dict[str, Any] = {}
    for sym in sorted(symbols):
        grp = [t for t in subset if t.symbol == sym]
        if not grp:
            by_sym[sym] = {"trade_count": 0}
            continue
        by_sym[sym] = {
            "trade_count": len(grp),
            "candidate_count": sum(1 for t in grp if t.vwap_shadow_reject_candidate),
            **_summarize(grp),
        }
    return {
        "symbols": sorted(symbols),
        "aggregate": _summarize(subset),
        "candidate": _summarize(cand),
        "non_candidate": _summarize(non),
        "by_symbol": by_sym,
    }


def _session_has_vwap_logging(session_dir: Path) -> bool:
    for name in ("small_paper_events.csv", "small_paper_events.jsonl"):
        path = session_dir / name
        if not path.is_file():
            continue
        if name.endswith(".csv"):
            with path.open(encoding="utf-8", newline="") as f:
                header = f.readline()
            return "vwap_shadow_reject_candidate" in header
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event_type") == "accepted" and "vwap_shadow_reject_candidate" in ev:
                    return True
    return False


def _load_live_trades(session_dir: Path, base: Path) -> list[ObsTrade]:
    sid = str(session_dir.relative_to(base)).replace("\\", "/")
    events_csv = session_dir / "small_paper_events.csv"
    if events_csv.is_file():
        acc_map: dict[tuple[str, str], dict[str, Any]] = {}
        exits: dict[tuple[str, str], dict[str, Any]] = {}
        with events_csv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                et = str(row.get("event_type") or "")
                sym = str(row.get("symbol") or "")
                ent = str(row.get("entry_time") or "")
                key = (sym, ent)
                if et == "accepted":
                    acc_map[key] = dict(row)
                elif et == "observer_exit":
                    exits[key] = dict(row)
        out: list[ObsTrade] = []
        for key, acc in acc_map.items():
            ex = exits.get(key)
            if ex:
                out.append(ObsTrade.from_event_pair(sid, acc, ex))
        return out

    events = _load_events(session_dir)
    pairs = _pair_trades(events)
    return [ObsTrade.from_event_pair(sid, acc, ex) for acc, ex in pairs if "vwap_shadow_reject_candidate" in acc]


def _load_offline_baseline(repo_root: Path, base: Path) -> list[ObsTrade]:
    session_dirs, _ = discover_sessions(base)
    out: list[ObsTrade] = []
    for sdir in session_dirs:
        for t in load_session_trades(sdir, repo_root=repo_root, base=base):
            out.append(ObsTrade.from_vwap_review(t))
    return out


def evaluate_vwap_shadow_reject_observation(*, repo_root: Path) -> dict[str, Any]:
    base = repo_root / "kabu_native" / "results" / "small_paper"
    live_dirs: list[Path] = []
    if base.is_dir():
        for sp in sorted(base.rglob("small_paper_summary.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            sdir = sp.parent
            if _session_has_vwap_logging(sdir):
                live_dirs.append(sdir)

    live_trades: list[ObsTrade] = []
    for sdir in live_dirs:
        live_trades.extend(_load_live_trades(sdir, base))

    offline_trades = _load_offline_baseline(repo_root, base)
    trades = live_trades if live_trades else offline_trades
    mode = "live_logged_sessions" if live_trades else "offline_baseline_phase185_sessions"

    cand = [t for t in trades if t.vwap_shadow_reject_candidate]
    non = [t for t in trades if not t.vwap_shadow_reject_candidate]

    return {
        "phase": 186,
        "mode": mode,
        "hypothesis": "vwap_shadow_reject_candidate marks lower-expectancy entries (shadow only; entry not blocked).",
        "constraints": {
            "hard_reject": False,
            "shadow_logging_only": True,
            "no_prod_yaml_change": True,
            "fixed_threshold_pct": VWAP_SHADOW_REJECT_MIN,
        },
        "live_session_count": len(live_dirs),
        "live_session_ids": [str(p.relative_to(base)).replace("\\", "/") for p in live_dirs],
        "reference_session_set": list(REFERENCE_SESSIONS) + list(OBSERVER_EXIT_SESSIONS),
        "trade_count": len(trades),
        "candidate_vs_non_candidate": {
            "candidate": _summarize(cand),
            "non_candidate": _summarize(non),
            "pf_delta_candidate_minus_non": None,
        },
        "false_positive": _false_positive(trades),
        "focus_symbols": _symbol_block(trades, FOCUS),
        "compare_symbols": _symbol_block(trades, COMPARE),
        "per_live_session": [
            {
                "session_id": sid,
                **_symbol_block([t for t in live_trades if t.session_id == sid], FOCUS),
            }
            for sid in sorted({t.session_id for t in live_trades})
        ]
        if live_trades
        else [],
    }


def finalize_observation_report(report: dict[str, Any]) -> dict[str, Any]:
    cmp_ = report.get("candidate_vs_non_candidate") or {}
    c_pf = (cmp_.get("candidate") or {}).get("profit_factor")
    n_pf = (cmp_.get("non_candidate") or {}).get("profit_factor")
    if isinstance(c_pf, (int, float)) and isinstance(n_pf, (int, float)):
        cmp_["pf_delta_candidate_minus_non"] = round(float(c_pf) - float(n_pf), 4)
    fp = report.get("false_positive") or {}
    report["verdict"] = {
        "candidate_pf_worse_than_non_candidate": (
            isinstance(c_pf, (int, float))
            and isinstance(n_pf, (int, float))
            and float(c_pf) < float(n_pf)
        ),
        "candidate_pf": c_pf,
        "non_candidate_pf": n_pf,
        "false_positive_rate": fp.get("false_positive_rate"),
        "live_data_available": bool(report.get("live_session_count")),
        "ready_for_shadow_reject_review": True,
        "note": "Entry is never blocked; vwap_shadow_reject_candidate is observational only.",
    }
    return report
