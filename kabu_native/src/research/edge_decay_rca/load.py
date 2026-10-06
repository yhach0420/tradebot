"""Load existing REFERENCE / UNIFORM10 ledgers. No Runtime."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.edge_decay_rca import DEVELOPMENT_DAYS, POST_DAYS
from research.anchor_economic_sensitivity.load import period_of

NATIVE = Path(__file__).resolve().parents[3]
C10 = NATIVE / "results" / "research" / "anchor_10min_opportunity" / "_work_cache"
C_ROB = NATIVE / "results" / "research" / "anchor_timing_robustness" / "_work_cache"


def _load_day(fp: Path) -> dict[str, Any] | None:
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    return body if body.get("ok") else None


def load_portfolios() -> dict[str, list[dict[str, Any]]]:
    ref: list[dict[str, Any]] = []
    uni: list[dict[str, Any]] = []
    admits: list[dict[str, Any]] = []
    daily_pack: list[dict[str, Any]] = []
    for fp in sorted(C10.glob("*.json")):
        body = _load_day(fp)
        if not body:
            continue
        day = str(body.get("date"))
        per = period_of(day)
        ports = body.get("portfolios") or {}
        for variant, dest in (("REFERENCE", ref), ("UNIFORM10", uni)):
            pack = ports.get(variant) or {}
            for t in pack.get("trades") or []:
                rec = dict(t)
                rec["variant"] = variant
                rec["period"] = per
                dest.append(rec)
        rpack = ports.get("REFERENCE") or {}
        upack = ports.get("UNIFORM10") or {}
        for a in rpack.get("admits") or []:
            rec = dict(a)
            rec["period"] = per
            rec["variant"] = "REFERENCE"
            admits.append(rec)
        daily_pack.append(
            {
                "date": day,
                "period": per,
                "ref_pnl": rpack.get("pnl"),
                "ref_n": rpack.get("trade_n"),
                "ref_selected_n": rpack.get("selected_n"),
                "ref_fills": rpack.get("fills"),
                "ref_fires": rpack.get("anchor_fires"),
                "ref_cap_blocked": rpack.get("cap_blocked"),
                "ref_same_symbol_blocked": rpack.get("same_symbol_blocked"),
                "uni_pnl": upack.get("pnl"),
                "uni_n": upack.get("trade_n"),
            }
        )
    return {"REFERENCE": ref, "UNIFORM10": uni, "admits": admits, "daily_pack": daily_pack}


def attach_isolated_to_portfolio(trades: list[dict[str, Any]], iso: list[dict[str, Any]]) -> None:
    """Copy independent MFE/MAE/book snap onto matching portfolio fills. Does not change PnL."""
    by: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in iso:
        by[(str(r.get("date")), str(r.get("anchor")), str(r.get("symbol")))] = r
    keys = (
        "mfe_yen_100",
        "mae_yen_100",
        "spread_bps",
        "imbalance",
        "bid_at_anchor",
        "ask_at_anchor",
        "ret_1m_bps",
        "ret_5m_bps",
    )
    for t in trades:
        r = by.get((str(t.get("date")), str(t.get("anchor_time")), str(t.get("symbol"))))
        if r is None:
            continue
        for k in keys:
            if t.get(k) is None and r.get(k) is not None:
                t[k] = r.get(k)


def load_feature_rows(cache_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not cache_dir.is_dir():
        return out
    for fp in sorted(cache_dir.glob("*.json")):
        body = _load_day(fp)
        if not body:
            continue
        day = str(body.get("date") or fp.stem)
        per = period_of(day)
        for r in body.get("feature_rows") or []:
            rec = dict(r)
            rec["date"] = rec.get("date") or day
            rec["period"] = rec.get("period") or per
            out.append(rec)
    return out


def load_isolated_reference() -> list[dict[str, Any]]:
    out = []
    for fp in sorted(C_ROB.glob("*.json")):
        body = _load_day(fp)
        if not body:
            continue
        day = str(body.get("date"))
        per = period_of(day)
        for t in (body.get("primary") or {}).get("isolated_trades") or []:
            if str(t.get("shift_key")) != "REFERENCE":
                continue
            rec = dict(t)
            rec["period"] = per
            out.append(rec)
    return out


def load_rank_rows() -> list[dict[str, Any]]:
    out = []
    for fp in sorted(C_ROB.glob("*.json")):
        body = _load_day(fp)
        if not body:
            continue
        day = str(body.get("date"))
        per = period_of(day)
        for t in (body.get("primary") or {}).get("rank_rows") or []:
            if str(t.get("shift_key")) != "REFERENCE":
                continue
            rec = dict(t)
            rec["period"] = per
            rec["date"] = rec.get("date") or day
            out.append(rec)
    return out


def load_universes() -> dict[str, list[str]]:
    from research.anchor_timing_robustness.inventory import build_inventory

    inv = build_inventory()
    out = {}
    for r in inv:
        day = str(r.get("date"))
        if day in DEVELOPMENT_DAYS or day in POST_DAYS:
            out[day] = [str(s) for s in (r.get("universe_symbols") or [])]
    return out
