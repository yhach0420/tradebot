#!/usr/bin/env python3
"""
Phase265: ENTRY quality in candidate concentration window 09:15-10:15 (review only).

Compare accepted vs reject by quality decile, entry_score, entry_score_v2.
Metrics: PnL, PF, MFE, MAE.

Output: kabu_native/results/reports/phase265_entry_quality_concentration_window.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase265_entry_quality_concentration_window.json"

WINDOW_START_MIN = 9 * 60 + 15  # 09:15
WINDOW_END_MIN = 10 * 60 + 15  # exclusive 10:15
V1_MODE = "legacy"
V1_RATIO = 0.85


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module(name: str, rel: str) -> Any:
    path = REPO / rel
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


def _int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _in_window(iso_ts: str) -> bool:
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    m = dt.hour * 60 + dt.minute
    return WINDOW_START_MIN <= m < WINDOW_END_MIN


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
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


def _enrich_scores(ev: dict[str, Any]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    q = _float(ev.get("continuation_quality_score"))
    v1 = _int(ev.get("entry_expectancy_score"))
    v2 = _int(ev.get("entry_expectancy_score_v2"))
    if v1 is None or v2 is None:
        sf = compute_entry_expectancy_score_fields(trade=ev)
        v1 = _int(sf.get("entry_expectancy_score")) if v1 is None else v1
        v2 = _int(sf.get("entry_expectancy_score_v2")) if v2 is None else v2
    return {"quality": q, "entry_score": v1, "entry_score_v2": v2}


def _final_outcomes(session_dir: Path, events: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, float]]:
    out: dict[tuple[str, str], dict[str, float]] = {}
    st_path = session_dir / "structural_trades.csv"
    if st_path.is_file():
        with st_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sym = str(row.get("symbol") or "")
                ent = str(row.get("entry_time") or "")
                if not sym or not ent or not _in_window(ent):
                    continue
                pnl = _float(row.get("realized_pnl_pct"))
                if pnl is None:
                    continue
                out[(sym, ent)] = {
                    "pnl_pct": float(pnl),
                    "mfe_pct": _float(row.get("mfe_pct")) or 0.0,
                    "mae_pct": abs(_float(row.get("mae_pct")) or 0.0),
                }
        return out

    accepts: dict[tuple[str, str], dict[str, Any]] = {}
    exits: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        et = str(ev.get("event_type") or "")
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if not key[0] or not key[1] or not _in_window(key[1]):
            continue
        if et == "accepted":
            accepts[key] = ev
        elif et == "observer_exit":
            exits[key] = ev
    for key, acc in accepts.items():
        ex = exits.get(key)
        pnl = _float(ex.get("pnl_pct")) if ex else _float(acc.get("pnl_pct"))
        if pnl is None:
            continue
        out[key] = {
            "pnl_pct": float(pnl),
            "mfe_pct": _float(acc.get("peak_mfe_pct")) or _float(acc.get("rolling_mfe_pct")) or 0.0,
            "mae_pct": abs(_float(acc.get("rolling_mae_pct")) or 0.0),
        }
    return out


@dataclass
class Row:
    quality: Optional[float]
    entry_score: Optional[int]
    entry_score_v2: Optional[int]
    pnl_pct: float
    mfe_pct: float
    mae_pct: float
    gate_reason: str = ""


def _replay_reject_metrics(
    p71: Any, events: list[dict[str, Any]], keys: set[tuple[str, str]]
) -> dict[tuple[str, str], dict[str, float]]:
    if not keys:
        return {}
    session_end = p71._session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    metrics: dict[tuple[str, str], dict[str, float]] = {}
    inject: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        key = (sym, ent)
        if key not in keys or key in inject:
            continue
        px = _float(ev.get("current_price"))
        if px and px > 0:
            inject[key] = ev
    injected: set[tuple[str, str]] = set()

    def close_act(act: Any, key: tuple[str, str], *, close_price: float) -> None:
        pnls = [float(t.get("pnl_pct") or 0) for t in act.rich_ticks]
        metrics[key] = {
            "pnl_pct": float(p71._pnl_pct(act.trade.entry_price, close_price)),
            "mfe_pct": max(pnls) if pnls else 0.0,
            "mae_pct": abs(min(pnls)) if pnls else 0.0,
        }

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
                ok = (old.trade.symbol, old.trade.entry_time)
                close_act(old, ok, close_price=price)
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            tr = p71.StructuralTrade(sym, ent, price, float(inject[key].get("continuation_quality_score") or 0))
            active[sym] = p71.ActiveTrade(
                trade=tr,
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": price,
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
        if str(ev.get("event_type") or "") == "candidate" and sym in active:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            act.rich_ticks.append(
                {
                    "price": price,
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, price),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = p71.simulate_combined_split(
                act.rich_ticks, act.trade.entry_price, momentum_mode=V1_MODE, ratio=V1_RATIO, allow_session_end=False
            )
            if sig:
                _, _, _ = sig
                k2 = (act.trade.symbol, act.trade.entry_time)
                close_act(act, k2, close_price=price)
                active.pop(sym, None)
    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        k2 = (act.trade.symbol, act.trade.entry_time)
        close_act(act, k2, close_price=float(last_px))
    return metrics


def _decile_labels(values: list[float]) -> list[float]:
    if len(values) < 10:
        return []
    s = sorted(values)
    n = len(s)
    cuts = []
    for i in range(1, 10):
        idx = int(i * n / 10)
        cuts.append(s[min(idx, n - 1)])
    return cuts


def _quality_decile(q: float, cuts: list[float]) -> int:
    d = 1
    for c in cuts:
        if q > c:
            d += 1
        else:
            break
    return min(d, 10)


def _metrics_rows(rows: list[Row]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "avg_pnl_pct": None,
            "total_pnl_pct": 0.0,
            "avg_mfe_pct": None,
            "avg_mae_pct": None,
            "win_rate": None,
        }
    pnls = [r.pnl_pct for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trade_count": len(rows),
        "profit_factor": _pf(pnls),
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 6),
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_mfe_pct": round(sum(r.mfe_pct for r in rows) / len(rows), 4),
        "avg_mae_pct": round(sum(r.mae_pct for r in rows) / len(rows), 4),
        "win_rate": round(wins / len(pnls), 4),
    }


def _bucket_analysis(rows: list[Row], *, kind: str) -> dict[str, Any]:
    if kind == "quality_decile":
        qs = [r.quality for r in rows if r.quality is not None]
        cuts = _decile_labels(qs)
        groups: dict[str, list[Row]] = defaultdict(list)
        for r in rows:
            if r.quality is None:
                continue
            groups[str(_quality_decile(float(r.quality), cuts))].append(r)
        return {k: _metrics_rows(v) for k, v in sorted(groups.items(), key=lambda x: int(x[0]))}

    if kind == "entry_score":
        groups = defaultdict(list)
        for r in rows:
            if r.entry_score is None:
                continue
            groups[str(int(r.entry_score))].append(r)
        return {k: _metrics_rows(v) for k, v in sorted(groups.items(), key=lambda x: int(x[0]))}

    if kind == "entry_score_v2":
        groups = defaultdict(list)
        for r in rows:
            if r.entry_score_v2 is None:
                continue
            groups[str(int(r.entry_score_v2))].append(r)
        return {k: _metrics_rows(v) for k, v in sorted(groups.items(), key=lambda x: int(x[0]))}

    return {}


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p71 = _load_module("phase71_p265", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")

    accepted_rows: list[Row] = []
    reject_rows_scored: list[Row] = []
    reject_keys_by_session: dict[str, set[tuple[str, str]]] = defaultdict(set)
    quality_pool: list[float] = []
    sessions = 0

    session_dirs = sorted(SMALL_PAPER.rglob("small_paper_summary.json")) if SMALL_PAPER.is_dir() else []

    for summary in session_dirs:
        sdir = summary.parent
        events = _load_events(sdir)
        if not events:
            continue
        sessions += 1
        sid = sdir.relative_to(SMALL_PAPER).as_posix()
        finals = _final_outcomes(sdir, events)

        for ev in events:
            ent = str(ev.get("entry_time") or ev.get("event_time") or "")
            if not _in_window(ent):
                continue
            et = str(ev.get("event_type") or "")
            if et not in ("accepted", "rejected"):
                continue
            sc = _enrich_scores(ev)
            sym = str(ev.get("symbol") or "")
            key = (sym, str(ev.get("entry_time") or ent))
            if sc["quality"] is not None:
                quality_pool.append(float(sc["quality"]))

            if et == "accepted":
                fin = finals.get(key)
                if not fin:
                    continue
                accepted_rows.append(
                    Row(
                        quality=sc["quality"],
                        entry_score=sc["entry_score"],
                        entry_score_v2=sc["entry_score_v2"],
                        pnl_pct=fin["pnl_pct"],
                        mfe_pct=fin["mfe_pct"],
                        mae_pct=fin["mae_pct"],
                    )
                )
            else:
                reject_keys_by_session[sid].add(key)

    for summary in session_dirs:
        sdir = summary.parent
        sid = sdir.relative_to(SMALL_PAPER).as_posix()
        keys = reject_keys_by_session.get(sid)
        if not keys:
            continue
        events = _load_events(sdir)
        vm = _replay_reject_metrics(p71, events, keys)
        seen_keys: set[tuple[str, str]] = set()
        for ev in events:
            ent = str(ev.get("entry_time") or "")
            if not _in_window(ent) or str(ev.get("event_type") or "") != "rejected":
                continue
            key = (str(ev.get("symbol") or ""), ent)
            if key in seen_keys or key not in vm:
                continue
            seen_keys.add(key)
            sc = _enrich_scores(ev)
            m = vm[key]
            reject_rows_scored.append(
                Row(
                    quality=sc["quality"],
                    entry_score=sc["entry_score"],
                    entry_score_v2=sc["entry_score_v2"],
                    pnl_pct=float(m["pnl_pct"]),
                    mfe_pct=float(m["mfe_pct"]),
                    mae_pct=float(m["mae_pct"]),
                    gate_reason=str(ev.get("gate_reject_reason") or ""),
                )
            )

    decile_cuts = _decile_labels(quality_pool)

    def cohort_block(rows: list[Row]) -> dict[str, Any]:
        return {
            "overall": _metrics_rows(rows),
            "by_quality_decile": _bucket_analysis(rows, kind="quality_decile"),
            "by_entry_score": _bucket_analysis(rows, kind="entry_score"),
            "by_entry_score_v2": _bucket_analysis(rows, kind="entry_score_v2"),
        }

    mc_only = [r for r in reject_rows_scored if r.gate_reason == "max_concurrent"]

    report = {
        "phase": 265,
        "mode": "entry_quality_concentration_window",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": {"start": "09:15", "end_exclusive": "10:15", "label": "09:15-10:15"},
        "quality_decile_cutpoints": [round(c, 4) for c in decile_cuts],
        "population": {
            "sessions": sessions,
            "accepted_count": len(accepted_rows),
            "reject_count": len(reject_rows_scored),
            "reject_max_concurrent_count": len(mc_only),
            "sources": "small_paper live + push_replay events",
        },
        "accepted": cohort_block(accepted_rows),
        "reject": cohort_block(reject_rows_scored),
        "reject_max_concurrent_only": cohort_block(mc_only),
        "accepted_vs_reject_spread": {
            "overall_avg_pnl_delta_accept_minus_reject": round(
                (_metrics_rows(accepted_rows).get("avg_pnl_pct") or 0)
                - (_metrics_rows(reject_rows_scored).get("avg_pnl_pct") or 0),
                6,
            ),
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(
        f"accepted={len(accepted_rows)} reject={len(reject_rows_scored)} mc={len(mc_only)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
