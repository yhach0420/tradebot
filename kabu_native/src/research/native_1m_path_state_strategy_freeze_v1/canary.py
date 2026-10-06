"""Diagnostic canary: R11 every minute vs frozen event-gated R11. Do not select B."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.native_1m_path_state_strategy_freeze_v1 import ENTRY_CUTOFF
from research.native_1m_path_state_strategy_freeze_v1.r11 import bar_matches_r11
from research.native_1m_path_state_strategy_freeze_v1.replay import frozen_replay
from research.native_path_state_discrimination_v1.outcomes import attach_fwd
from research.native_path_state_discrimination_v1.walk import _sector_of
from research.one_minute_native_playbook_discovery_v1.states import entry_ok, prep_symbol


def _cand_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("date")), str(row.get("symbol")), str(row.get("event_time")))


def jaccard(a: set, b: set) -> float | None:
    if not a and not b:
        return None
    return len(a & b) / float(len(a | b))


def run_every_minute_canary(bind: dict[str, Any], *, med: dict[str, float]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    symbols = list(bind.get("symbols") or [])
    sector_of = _sector_of(bind)
    print(f"CANARY_LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)}", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=disc, forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "reason": "no_minutes"}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    rows: list[dict[str, Any]] = []
    n_days = int(minutes["date"].nunique())
    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        per: dict[str, dict[str, Any]] = {}
        for sym, sg in g.groupby("symbol", sort=False):
            rec = prep_symbol(sg)
            if rec["n"] < 20:
                continue
            rec["symbol"] = str(sym)
            rec["sector"] = sector_of.get(str(sym)) or ""
            per[str(sym)] = rec
        for rec in per.values():
            for i in range(rec["n"]):
                if not bar_matches_r11(rec, i, med=med):
                    continue
                feat_t = rec["t"][i]
                entry = hhmm_add(feat_t, 1)
                if not entry_ok(entry) or str(entry) > ENTRY_CUTOFF:
                    continue
                ep = {
                    "date": str(day),
                    "block": date_to_block.get(str(day)),
                    "symbol": rec["symbol"],
                    "sector": rec.get("sector"),
                    "start": entry,
                    "event_time": entry,
                    "feature_bar": feat_t,
                    "available_at": entry,
                    "hi20": None,
                    "state": {
                        "dist_vwap": None,
                        "mins_from_open": None,
                        "vwap_reclaim": 1.0,
                    },
                }
                attach_fwd(ep, rec, {})
                rows.append(ep)
        if di % 20 == 0 or di == n_days:
            print(f"CANARY DAY {di}/{n_days} cands={len(rows)}", flush=True)
    pack = frozen_replay(rows, med=med, already_matched=True)
    return {
        "ok": True,
        "label": "B_every_minute",
        "candidate_n": pack.get("candidate_n"),
        "trade_n_d2d4": pack.get("trade_n"),
        "trade_n_all": (pack.get("discovery_all") or {}).get("trade_n"),
        "rows": rows,
        "pack": {k: v for k, v in pack.items() if k not in {"trades", "eval_trades"}},
        "official": False,
        "do_not_select": True,
    }


def compare_canary(*, gated_cands: list[dict[str, Any]], gated_trades: list[dict[str, Any]], canary: dict[str, Any]) -> dict[str, Any]:
    a_c = {_cand_key(r) for r in gated_cands}
    b_c = {_cand_key(r) for r in list(canary.get("rows") or [])}
    a_t = {_cand_key(r) for r in gated_trades if str(r.get("block")) in {"D2", "D3", "D4"}}
    b_pack = dict(canary.get("pack") or {})
    return {
        "A_event_gated_candidate_n": len(a_c),
        "B_every_minute_candidate_n": len(b_c),
        "A_trade_n_d2d4": len(a_t),
        "B_trade_n_d2d4": canary.get("trade_n_d2d4"),
        "candidate_overlap_n": len(a_c & b_c),
        "candidate_jaccard": jaccard(a_c, b_c),
        "candidates_equal": a_c == b_c,
        "different_strategy": a_c != b_c or len(a_t) != int(canary.get("trade_n_d2d4") or -1),
        "official_remains": "A",
        "B_not_selected": True,
        "B_econ_ignored_for_selection": True,
        "B_mean_x0_d2d4": b_pack.get("mean_x0_bps"),
    }
