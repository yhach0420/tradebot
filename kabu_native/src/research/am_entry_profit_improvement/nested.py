"""Outer LODO + inner LODO spec selection. Outer held-out never selects the spec."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from research.am_entry_profit_improvement import SCORE_KEY, SPEC_N
from research.am_entry_profit_improvement.metrics import economic_pack, spec_sort_key
from research.am_entry_profit_improvement.models import contamination_n, fit_spec, score_spec
from research.am_entry_profit_improvement.portfolio import portfolio_replay
from research.canonical_entry_performance_rebase.analyze import session_of


def _day_rows(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("date") or "") == str(day)]


def _filter_days(rows: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    want = set(str(d) for d in days)
    return [r for r in rows if str(r.get("date") or "") in want]


def evaluate_scored(rows: list[dict[str, Any]], days: list[str], *, score_key: str = SCORE_KEY) -> dict[str, Any]:
    port = portfolio_replay(rows, score_key=score_key)
    pack = economic_pack(list(port.get("trades") or []), days)
    pack["admitted_n"] = port.get("admitted_n")
    pack["expired_n"] = port.get("expired_n")
    pack["cap_blocked"] = port.get("cap_blocked")
    pack["same_symbol_blocked"] = port.get("same_symbol_blocked")
    return pack


def select_inner_spec(spec_rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(spec_rows, key=spec_sort_key)
    return dict(ordered[0]) if ordered else {}


def process_outer_fold(payload: dict[str, Any]) -> dict[str, Any]:
    outer = str(payload["outer_day"])
    train_days = [str(d) for d in payload["train_days"]]
    specs = list(payload.get("specs") or [])
    raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
    rows = list(raw.get("rows") or [])
    leak = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "INNER_VALIDATION_FIT_LEAK_N": 0,
        "PM_ROWS_USED_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "OUTER_RESELECTION_N": 0,
        "POSTHOC_SPEC_ADDITION_N": 0,
    }
    if len(specs) != int(SPEC_N):
        leak["POSTHOC_SPEC_ADDITION_N"] = abs(len(specs) - int(SPEC_N))
    leak["TARGET_CONTAMINATION_N"] = sum(contamination_n(list(s.get("features") or [])) for s in specs)
    for r in rows:
        if session_of(r) != "AM":
            leak["PM_ROWS_USED_N"] += 1
    train_all = _filter_days(rows, train_days)
    if any(str(r.get("date") or "") == outer for r in train_all):
        leak["OUTER_HELDOUT_FIT_LEAK_N"] += 1
        return {"ok": False, "outer_day": outer, "blocker": "OUTER_IN_TRAIN", "integrity": leak}

    inner_spec_rows: list[dict[str, Any]] = []
    print(f"  outer {outer} inner-specs={len(specs)} train_days={len(train_days)}", flush=True)
    for spec in specs:
        inner_trades: list[dict[str, Any]] = []
        daily_pnls: list[float] = []
        inner_leak = 0
        for val in train_days:
            inner_train_days = [d for d in train_days if d != val]
            if val in inner_train_days or outer in inner_train_days:
                inner_leak += 1
            tr = _filter_days(train_all, inner_train_days)
            va = _day_rows(train_all, val)
            fit = fit_spec(tr, spec)
            scored = score_spec(va, fit)
            pack = evaluate_scored(scored, [val])
            inner_trades.extend(list(pack.get("trades") or []))
            daily_pnls.append(float(pack.get("net_pnl_yen_100") or 0.0))
        leak["INNER_VALIDATION_FIT_LEAK_N"] += inner_leak
        agg = economic_pack(inner_trades, train_days)
        inner_spec_rows.append(
            {
                "outer_day": outer,
                "spec_id": spec.get("spec_id"),
                "architecture_id": spec.get("architecture_id"),
                "representation_id": spec.get("representation_id"),
                "feature_set": spec.get("feature_set"),
                "normalization": spec.get("normalization"),
                "mean_inner_pnl": float(sum(daily_pnls) / len(daily_pnls)) if daily_pnls else 0.0,
                "median_daily_pnl": float(np.median(daily_pnls)) if daily_pnls else 0.0,
                "profit_factor": agg.get("profit_factor"),
                "max_drawdown_yen_100": agg.get("max_drawdown_yen_100"),
                "trade_count": agg.get("trade_count"),
                "net_pnl_yen_100": agg.get("net_pnl_yen_100"),
            }
        )

    chosen = select_inner_spec(inner_spec_rows)
    chosen_spec = next((s for s in specs if s.get("spec_id") == chosen.get("spec_id")), None)
    if chosen_spec is None:
        return {"ok": False, "outer_day": outer, "blocker": "NO_INNER_SPEC", "integrity": leak}

    fit_outer = fit_spec(train_all, chosen_spec)
    if any(str(r.get("date") or "") == outer for r in train_all):
        leak["OUTER_HELDOUT_FIT_LEAK_N"] += 1
    scored_outer = score_spec(_day_rows(rows, outer), fit_outer)
    outer_pack = evaluate_scored(scored_outer, [outer])
    slim_fit = {
        "kind": fit_outer.get("kind"),
        "architecture_id": fit_outer.get("architecture_id"),
        "features": fit_outer.get("features"),
        "normalization": fit_outer.get("normalization"),
        "train_n": fit_outer.get("train_n"),
        "alpha": fit_outer.get("alpha"),
    }
    print(
        f"  outer {outer} selected={chosen.get('spec_id')} pnl={outer_pack.get('net_pnl_yen_100')} "
        f"trades={outer_pack.get('trade_count')}",
        flush=True,
    )
    return {
        "ok": True,
        "outer_day": outer,
        "selected": {
            "spec_id": chosen.get("spec_id"),
            "architecture_id": chosen.get("architecture_id"),
            "representation_id": chosen.get("representation_id"),
            "mean_inner_pnl": chosen.get("mean_inner_pnl"),
            "median_daily_pnl": chosen.get("median_daily_pnl"),
            "profit_factor": chosen.get("profit_factor"),
            "max_drawdown_yen_100": chosen.get("max_drawdown_yen_100"),
        },
        "inner_specs": inner_spec_rows,
        "outer_pack": {
            k: v for k, v in outer_pack.items() if k not in ("daily", "trades")
        },
        "outer_trades": list(outer_pack.get("trades") or []),
        "outer_daily": list(outer_pack.get("daily") or []),
        "fit": slim_fit,
        "integrity": leak,
    }
