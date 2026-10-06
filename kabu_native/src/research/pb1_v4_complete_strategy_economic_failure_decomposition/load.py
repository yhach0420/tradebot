"""Reload Confirmation 1 Complete Strategy replay. Does not change V1 fills. FV sealed."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from research.pb1_v4_clarified_machine_correction_v4.bind import bind_prior
from research.pb1_v4_complete_strategy_build_and_economic_validation import CAP
from research.pb1_v4_complete_strategy_build_and_economic_validation.portfolio import replay_occupancy
from research.pb1_v4_complete_strategy_build_and_economic_validation.replay import _finite
from research.pb1_v4_complete_strategy_economic_confirmation1 import EVAL_FIRST, EVAL_LAST, EXPECTED_OC_E0, EXPECTED_OC_E1
from research.pb1_v4_complete_strategy_economic_confirmation1.replay_conf import build_candidates
from research.pb1_v4_complete_strategy_economic_failure_decomposition import BASELINE
from research.pb1_v4_complete_strategy_economic_failure_decomposition.isolation import CONF1_OUT
from research.pb1_v4_frozen_old_confirmation_blind_validation.dates import partition_dates
from research.pb1_v4_frozen_old_confirmation_blind_validation.walk_conf import (
    filter_confirmation,
    open_confirmation_minutes,
    walk_frozen,
)


def _full_rec(sg: pd.DataFrame) -> dict[str, Any]:
    sg = sg.sort_values("time_label")
    times = [str(t)[:5] for t in sg["time_label"].tolist()]

    def _col(name: str) -> list[float]:
        return [float(x) if _finite(x) else float("nan") for x in sg[name].tolist()]

    va = _col("trading_value") if "trading_value" in sg.columns else [0.0] * len(times)
    return {"t": times, "o": _col("open"), "h": _col("high"), "l": _col("low"), "c": _col("close"), "va": va, "n": len(times)}


def load_parent_report() -> dict[str, Any]:
    path = CONF1_OUT / "report.json"
    if not path.is_file():
        return {"ok": False, "reason": "confirmation1_report_missing"}
    raw = json.loads(path.read_text(encoding="utf-8"))
    ans = dict(raw.get("answers") or {})
    metrics = dict(raw.get("metrics") or {})
    ok = str(ans.get("VERDICT") or "") == "PB1_V4_COMPLETE_STRATEGY_ECONOMIC_CONFIRMATION1_FAIL_V1"
    return {
        "ok": ok,
        "reason": None if ok else "parent_v1_not_fail",
        "answers": ans,
        "metrics": metrics,
        "trades": list(raw.get("trades") or []),
        "COMPLETE_STRATEGY_SHA256": ans.get("COMPLETE_STRATEGY_SHA256"),
        "net_pnl_yen": metrics.get("net_pnl_yen"),
        "trade_n": metrics.get("trade_n"),
        "signal_n": metrics.get("signal_n"),
    }


def baseline_lock(parent: dict[str, Any]) -> dict[str, Any]:
    m = dict(parent.get("metrics") or {})
    checks = {
        "signal_n": abs(int(m.get("signal_n") or 0) - int(BASELINE["signal_n"])) == 0,
        "trade_n": abs(int(m.get("trade_n") or 0) - int(BASELINE["trade_n"])) == 0,
        "gross_pnl_yen": abs(float(m.get("gross_pnl_yen") or 0) - float(BASELINE["gross_pnl_yen"])) < 1.0,
        "execution_cost_yen": abs(float(m.get("execution_cost_yen") or 0) - float(BASELINE["execution_cost_yen"])) < 1.0,
        "net_pnl_yen": abs(float(m.get("net_pnl_yen") or 0) - float(BASELINE["net_pnl_yen"])) < 1.0,
    }
    return {"ok": all(checks.values()), "checks": checks, "baseline": dict(BASELINE), "observed": {k: m.get(k) for k in checks}}


def replay_confirmation_paths(*, bind: dict[str, Any], part: dict[str, Any]) -> dict[str, Any]:
    symbols = list(bind.get("symbols") or [])
    conf_dates = list(part.get("confirmation_dates") or [])
    loaded = open_confirmation_minutes(
        symbols=symbols,
        lookback_dates=list(part.get("lookback_dates") or []),
        confirmation_dates=conf_dates,
        frozen_validation_dates=list(part.get("frozen_validation_dates") or []),
    )
    if not loaded.get("ok"):
        return {"ok": False, "reason": loaded.get("reason") or "load_failed"}
    minutes = loaded["minutes"]
    walked_all = walk_frozen(
        bind=bind,
        minutes=minutes,
        walk_dates=list(part.get("walk_dates") or []),
        symbols=symbols,
    )
    if not walked_all.get("ok"):
        return {"ok": False, "reason": walked_all.get("reason") or "walk_failed"}
    walked = filter_confirmation(walked_all, confirmation_dates=conf_dates)
    e0 = list(walked.get("e0_events") or [])
    e1 = list(walked.get("e1_events") or [])
    if len(e0) != int(EXPECTED_OC_E0) or len(e1) != int(EXPECTED_OC_E1):
        return {"ok": False, "reason": "confirmation_emit_mismatch", "e0_n": len(e0), "e1_n": len(e1)}
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    need = {str(r.get("symbol") or "") for r in e0 + e1}
    conf_set = set(conf_dates)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    for (date, symbol), g in minutes.groupby(["date", "symbol"], sort=False):
        d, s = str(date), str(symbol)
        if d in conf_set and s in need:
            recs[(d, s)] = _full_rec(g)
    built = build_candidates(walked=walked, recs=recs)
    occ = replay_occupancy(list(built.get("candidates") or []), cap=CAP)
    trades = list(occ.get("trades") or [])
    blocked = [r for r in list(occ.get("rows") or []) if not r.get("admitted")]
    funnel = {(str(r.get("symbol") or ""), str(r.get("date") or "")): r for r in list(walked.get("funnel_days") or [])}
    setups = {(str(r.get("symbol") or ""), str(r.get("date") or "")): r for r in list(walked.get("setups") or [])}
    return {
        "ok": True,
        "e0_n": len(e0),
        "e1_n": len(e1),
        "signal_n": len(e0) + len(e1),
        "trades": trades,
        "blocked_rows": blocked,
        "candidates": list(built.get("candidates") or []),
        "recs": recs,
        "funnel": funnel,
        "setups": setups,
        "occupancy": {k: v for k, v in occ.items() if k not in {"trades", "rows"}},
        "cap_blocked_n": int(occ.get("cap_blocked_n") or 0),
        "same_symbol_blocked_n": int(occ.get("same_symbol_blocked_n") or 0),
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
    }


def bind_and_partition() -> dict[str, Any]:
    bind = bind_prior()
    part = partition_dates(bind)
    return {"bind": bind, "part": part, "ok": bool(bind.get("ok") and part.get("ok"))}
