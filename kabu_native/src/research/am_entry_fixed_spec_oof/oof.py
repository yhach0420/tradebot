"""One frozen spec × 18 outer LODO days. No inner selection. Outer held-out never fits."""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any

from research.am_entry_fixed_spec_oof import SPEC_N
from research.am_entry_profit_improvement import SCORE_KEY, SPEC_N as V2_SPEC_N
from research.am_entry_profit_improvement.metrics import economic_pack
from research.am_entry_profit_improvement.models import contamination_n, fit_spec, score_spec
from research.am_entry_profit_improvement.portfolio import portfolio_replay
from research.canonical_entry_performance_rebase.analyze import session_of
from research.direct_joint_objective import ELIGIBLE_DAYS

assert int(SPEC_N) == int(V2_SPEC_N) == 27


def evaluate_policy(rows: list[dict[str, Any]], days: list[str], *, score_key: str = SCORE_KEY) -> dict[str, Any]:
    port = portfolio_replay(rows, score_key=score_key)
    pack = economic_pack(list(port.get("trades") or []), days)
    admitted = int(port.get("admitted_n") or 0)
    fill_n = int(port.get("fill_n") or 0)
    pack["admitted_n"] = admitted
    pack["expired_n"] = int(port.get("expired_n") or 0)
    pack["fill_n"] = fill_n
    pack["cap_blocked"] = int(port.get("cap_blocked") or 0)
    pack["same_symbol_blocked"] = int(port.get("same_symbol_blocked") or 0)
    pack["open_leftover_n"] = int(port.get("open_leftover_n") or 0)
    pack["fill_rate"] = (float(fill_n) / float(admitted)) if admitted else None
    return pack


def _filter_days(rows: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    want = set(str(d) for d in days)
    return [r for r in rows if str(r.get("date") or "") in want]


def _day_rows(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("date") or "") == str(day)]


def process_fixed_spec(payload: dict[str, Any]) -> dict[str, Any]:
    """Fit spec s on 17 days, apply once to held-out day d. Repeat for all 18 days. Stitch."""
    warnings.filterwarnings("ignore")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    spec = dict(payload.get("spec") or {})
    days = [str(d) for d in (payload.get("days") or list(ELIGIBLE_DAYS))]
    spec_id = str(spec.get("spec_id") or "")
    leak = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "PM_ROWS_USED_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "POSTHOC_SPEC_ADDITION_N": 0,
        "INNER_SELECTION_N": 0,
    }
    leak["TARGET_CONTAMINATION_N"] = contamination_n(list(spec.get("features") or []))
    raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
    rows = list(raw.get("rows") or [])
    for r in rows:
        if session_of(r) != "AM":
            leak["PM_ROWS_USED_N"] += 1
    if leak["PM_ROWS_USED_N"]:
        return {"ok": False, "spec_id": spec_id, "blocker": "PM_ROWS", "integrity": leak}

    trades: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    print(f"  spec {spec_id} outer_days={len(days)}", flush=True)
    for outer in days:
        train_days = [d for d in days if d != outer]
        if outer in train_days or len(train_days) != 17:
            leak["OUTER_HELDOUT_FIT_LEAK_N"] += 1
            return {
                "ok": False,
                "spec_id": spec_id,
                "blocker": "TRAIN_DAY_CONTRACT",
                "integrity": leak,
            }
        train = _filter_days(rows, train_days)
        if any(str(r.get("date") or "") == outer for r in train):
            leak["OUTER_HELDOUT_FIT_LEAK_N"] += 1
            return {
                "ok": False,
                "spec_id": spec_id,
                "blocker": "OUTER_IN_TRAIN",
                "integrity": leak,
            }
        test = _day_rows(rows, outer)
        fit = fit_spec(train, spec)
        scored = score_spec(test, fit)
        pack = evaluate_policy(scored, [outer])
        day_trades = list(pack.get("trades") or [])
        trades.extend(day_trades)
        folds.append(
            {
                "spec_id": spec_id,
                "date": outer,
                "fit_kind": fit.get("kind"),
                "train_n": fit.get("train_n"),
                "pnl_yen_100": pack.get("net_pnl_yen_100"),
                "trade_count": pack.get("trade_count"),
                "admitted_n": pack.get("admitted_n"),
                "fill_n": pack.get("fill_n"),
                "expired_n": pack.get("expired_n"),
                "fill_rate": pack.get("fill_rate"),
                "cap_blocked": pack.get("cap_blocked"),
                "same_symbol_blocked": pack.get("same_symbol_blocked"),
            }
        )
        print(
            f"  spec {spec_id} day={outer} kind={fit.get('kind')} "
            f"pnl={pack.get('net_pnl_yen_100')} trades={pack.get('trade_count')}",
            flush=True,
        )

    stitched = economic_pack(trades, days)
    admitted = sum(int(f.get("admitted_n") or 0) for f in folds)
    fill_n = sum(int(f.get("fill_n") or 0) for f in folds)
    expired_n = sum(int(f.get("expired_n") or 0) for f in folds)
    stitched["admitted_n"] = admitted
    stitched["fill_n"] = fill_n
    stitched["expired_n"] = expired_n
    stitched["fill_rate"] = (float(fill_n) / float(admitted)) if admitted else None
    stitched["cap_blocked"] = sum(int(f.get("cap_blocked") or 0) for f in folds)
    stitched["same_symbol_blocked"] = sum(int(f.get("same_symbol_blocked") or 0) for f in folds)
    slim = {k: v for k, v in stitched.items() if k not in ("trades", "daily")}
    print(
        f"  spec {spec_id} STITCH net={slim.get('net_pnl_yen_100')} "
        f"pf={slim.get('profit_factor')} trades={slim.get('trade_count')}",
        flush=True,
    )
    return {
        "ok": True,
        "spec_id": spec_id,
        "architecture_id": spec.get("architecture_id"),
        "representation_id": spec.get("representation_id"),
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "fold_n": len(folds),
        "pack": slim,
        "daily": list(stitched.get("daily") or []),
        "folds": folds,
        "integrity": leak,
    }
