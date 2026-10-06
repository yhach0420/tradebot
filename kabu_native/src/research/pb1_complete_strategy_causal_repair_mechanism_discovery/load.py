"""Load frozen V1 trades and development-exposed minutes. No FV / prospective."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pandas as pd

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20, daily_from_minutes
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.isolation import CONF1_OUT, CS_OUT
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.precommit import FOLDS
from research.pb1_v4_clarified_machine_correction_v4.bind import bind_prior
from research.pb1_v4_complete_strategy_build_and_economic_validation import DEV_FIRST, DEV_LAST
from research.pb1_v4_complete_strategy_build_and_economic_validation.replay import _finite
from research.pb1_v4_complete_strategy_economic_confirmation1 import EVAL_FIRST, EVAL_LAST, FV_FIRST, FV_LAST, PROSPECTIVE_FROM
from research.pb1_v4_complete_strategy_economic_failure_decomposition.path import parse_identity
from research.pb1_v4_frozen_old_confirmation_blind_validation.dates import partition_dates


def _trade_fields(raw: dict[str, Any], *, sample: str) -> dict[str, Any]:
    ident = parse_identity(str(raw.get("execution_id") or ""))
    lost_at = raw.get("THESIS_LOST_AT")
    return {
        "sample": sample,
        "symbol": str(raw.get("symbol") or ""),
        "date": str(raw.get("date") or ""),
        "side": str(raw.get("side") or ""),
        "entry_t": str(raw.get("entry_t") or "")[:5],
        "entry_px": float(raw.get("entry_px") or 0),
        "exit_t": str(raw.get("exit_t") or "")[:5],
        "exit_px": float(raw.get("exit_px") or 0),
        "THESIS_LOST": bool(raw.get("THESIS_LOST")),
        "THESIS_LOST_AT": str(lost_at)[:5] if lost_at else None,
        "THESIS_LOST_REASON": str(raw.get("THESIS_LOST_REASON") or ""),
        "execution_id": str(raw.get("execution_id") or ""),
        "exit_reason": str(raw.get("exit_reason") or ""),
        "gross_pnl_yen": float(raw.get("gross_pnl_yen") or 0),
        "net_pnl_yen": float(raw.get("net_pnl_yen") or 0),
        "execution_cost_yen": float(raw.get("execution_cost_yen") or 0),
        "seed_family": ident.get("seed_family"),
        "location_family": ident.get("location_family"),
        "location_subtype": ident.get("location_subtype"),
        "entry_kind": str(raw.get("entry_type") or raw.get("exec_variant") or ""),
    }


def confirmation_tertiles(conf_dates: list[str]) -> dict[str, str]:
    dates = sorted({str(d) for d in conf_dates if EVAL_FIRST <= str(d) <= EVAL_LAST})
    n = len(dates)
    a = n // 3
    b = n // 3
    mapping: dict[str, str] = {}
    for i, d in enumerate(dates):
        if i < a:
            mapping[d] = "C1_EARLY"
        elif i < a + b:
            mapping[d] = "C1_MIDDLE"
        else:
            mapping[d] = "C1_LATE"
    return mapping


def fold_of(date: str, tertiles: dict[str, str]) -> str:
    ds = str(date)
    if DEV_FIRST <= ds <= DEV_LAST:
        return "DEV"
    return str(tertiles.get(ds) or "C1_LATE")


def load_v1_trades(*, conf_dates: list[str]) -> list[dict[str, Any]]:
    cs = json.loads((CS_OUT / "report.json").read_text(encoding="utf-8"))
    c1 = json.loads((CONF1_OUT / "report.json").read_text(encoding="utf-8"))
    rows = [_trade_fields(t, sample="DEV") for t in list(cs.get("trades") or [])]
    rows.extend(_trade_fields(t, sample="C1") for t in list(c1.get("trades") or []))
    tert = confirmation_tertiles(conf_dates)
    for t in rows:
        t["fold"] = fold_of(str(t["date"]), tert)
        t["month"] = str(t["date"])[:6]
        assert t["fold"] in FOLDS
        assert not (FV_FIRST <= str(t["date"]) <= FV_LAST)
        assert str(t["date"]) < PROSPECTIVE_FROM
    return rows


def _full_rec(sg: pd.DataFrame) -> dict[str, Any]:
    sg = sg.sort_values("time_label")
    times = [str(t)[:5] for t in sg["time_label"].tolist()]

    def _col(name: str) -> list[float]:
        if name not in sg.columns:
            return [float("nan")] * len(times)
        return [float(x) if _finite(x) else float("nan") for x in sg[name].tolist()]

    return {
        "t": times,
        "o": _col("open"),
        "h": _col("high"),
        "l": _col("low"),
        "c": _col("close"),
        "v": _col("volume"),
        "va": _col("trading_value"),
        "n": len(times),
    }


def load_panel(*, symbols: list[str], allowed: list[str], forbidden: list[str]) -> dict[tuple[str, str], dict[str, Any]]:
    allowed_s = set(str(d) for d in allowed)
    forbidden_s = set(str(d) for d in forbidden)
    df = load_minutes(symbols=list(symbols), allowed_dates=allowed_s, forbidden_dates=forbidden_s)
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    if df is None or df.empty:
        return recs
    df = df.sort_values(["symbol", "date", "time_label"])
    for (symbol, date), g in df.groupby(["symbol", "date"], sort=False):
        recs[(str(symbol), str(date))] = _full_rec(g)
    return recs


def daily_history(minutes: dict[tuple[str, str], dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (sym, date), rec in sorted(minutes.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        day = daily_from_minutes(rec, date)
        if day:
            by_sym[sym].append(day)
    return dict(by_sym)


def prior_and_atr(history: dict[str, list[dict[str, Any]]], *, symbol: str, date: str) -> tuple[list[dict[str, Any]], float | None]:
    prior = [d for d in history.get(symbol) or [] if str(d["date"]) < str(date)]
    a = atr20(prior)
    return prior, (float(a) if a == a and a > 0 else None)


def bind_dates() -> dict[str, Any]:
    bind = bind_prior()
    part = partition_dates(bind)
    disc = [d for d in list(part.get("lookback_dates") or [])]
    # Full original development, not only lookback-80.
    split = dict(bind.get("split") or {})
    all_disc = sorted(str(d) for d in list(split.get("discovery_dates") or []) if DEV_FIRST <= str(d) <= DEV_LAST)
    conf = list(part.get("confirmation_dates") or [])
    fv = list(part.get("frozen_validation_dates") or [])
    allowed = sorted(set(all_disc) | set(conf))
    forbidden = sorted(set(fv) | {d for d in allowed if d >= PROSPECTIVE_FROM} | {d for d in allowed if FV_FIRST <= d <= FV_LAST})
    allowed = [d for d in allowed if d not in set(forbidden)]
    return {
        "ok": bool(bind.get("ok") and part.get("ok")),
        "bind": bind,
        "part": part,
        "symbols": list(bind.get("symbols") or []),
        "discovery_dates": all_disc,
        "confirmation_dates": conf,
        "allowed": allowed,
        "forbidden": forbidden,
        "lookback_only_n": len(disc),
    }
