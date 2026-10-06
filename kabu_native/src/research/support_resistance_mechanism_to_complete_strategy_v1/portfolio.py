"""CAP=3 occupancy replay. No delayed CAP or same-symbol entry. Deterministic ties only."""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from research.support_resistance_mechanism_to_complete_strategy_v1 import CAP, MECH_RANK, TIE_SEED


def _sym_key_asc(symbol: str, _date: str) -> str:
    return str(symbol)


def _sym_key_desc(symbol: str, _date: str) -> str:
    return "".join(chr(255 - ord(c)) for c in str(symbol))


def _sym_key_hash(symbol: str, date: str) -> str:
    raw = f"{symbol}|{date}|{TIE_SEED}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


ORDER_FNS: dict[str, Callable[[str, str], str]] = {
    "PRIMARY_SYMBOL_ASC": _sym_key_asc,
    "REVERSE_SYMBOL_ORDER": _sym_key_desc,
    "SEEDED_HASH_ORDER": _sym_key_hash,
}


def _event_sort_key(ev: tuple, order_fn: Callable[[str, str], str]) -> tuple:
    kind, date, t, symbol, mech, _sid = ev[0], ev[1], ev[2], ev[3], ev[4], ev[5]
    # RELEASE before ENTRY at the same timestamp.
    kind_rank = 0 if kind == "RELEASE" else 1
    return (str(date), str(t), kind_rank, order_fn(str(symbol), str(date)), int(MECH_RANK.get(str(mech), 9)), str(symbol))


def replay(
    signals: list[dict[str, Any]],
    *,
    strategy_id: str,
    cap: int = CAP,
    order_name: str = "PRIMARY_SYMBOL_ASC",
) -> dict[str, Any]:
    order_fn = ORDER_FNS[order_name]
    usable = [s for s in signals if s.get("ok") and s.get("entry_t") and s.get("slot_release_t")]
    events: list[tuple] = []
    by_id: dict[str, dict[str, Any]] = {}
    for s in usable:
        sid = str(s["signal_id"])
        by_id[sid] = s
        events.append(("ENTRY", s["date"], s["entry_t"], s["symbol"], s["mechanism"], sid))
        events.append(("RELEASE", s["date"], s["slot_release_t"], s["symbol"], s["mechanism"], sid))
    events.sort(key=lambda ev: _event_sort_key(ev, order_fn))

    open_ids: set[str] = set()
    open_sym: set[str] = set()
    filled_episode: set[str] = set()
    accepted: list[dict[str, Any]] = []
    cap_blocked: list[dict[str, Any]] = []
    same_blocked: list[dict[str, Any]] = []
    reentry_blocked: list[dict[str, Any]] = []
    seen_signal: set[str] = set()
    delayed_cap_n = 0
    delayed_sym_n = 0
    occupancy_obs: list[int] = []
    max_occ = 0
    reentry_n = 0
    symbol_had_fill: dict[str, int] = {}

    for kind, date, _t, symbol, _mech, sid in events:
        s = by_id[sid]
        if kind == "RELEASE":
            if sid in open_ids:
                open_ids.discard(sid)
                open_sym.discard(str(symbol))
            continue
        if sid in seen_signal:
            continue
        seen_signal.add(sid)
        ep = str(s.get("episode_id") or "")
        if ep and ep in filled_episode:
            row = dict(s)
            row["block_reason"] = "REENTRY_BLOCKED"
            reentry_blocked.append(row)
            continue
        if str(symbol) in open_sym:
            row = dict(s)
            row["block_reason"] = "SAME_SYMBOL_BLOCKED"
            same_blocked.append(row)
            continue
        if len(open_ids) >= int(cap):
            row = dict(s)
            row["block_reason"] = "CAP_BLOCKED"
            cap_blocked.append(row)
            continue
        trade = dict(s)
        trade["strategy_id"] = strategy_id
        trade["order_name"] = order_name
        trade["occupancy_at_entry"] = len(open_ids) + 1
        trade["cap_blocked"] = False
        trade["same_symbol_blocked"] = False
        prior = int(symbol_had_fill.get(f"{date}|{symbol}", 0))
        trade["reentry"] = prior > 0
        if prior > 0:
            reentry_n += 1
        symbol_had_fill[f"{date}|{symbol}"] = prior + 1
        open_ids.add(sid)
        open_sym.add(str(symbol))
        if ep:
            filled_episode.add(ep)
        occupancy_obs.append(len(open_ids))
        max_occ = max(max_occ, len(open_ids))
        accepted.append(trade)

    return {
        "strategy_id": strategy_id,
        "order_name": order_name,
        "cap": int(cap),
        "signal_n": len(usable),
        "entry_attempts": len(usable),
        "filled_n": len(accepted),
        "trades": accepted,
        "cap_blocked": cap_blocked,
        "same_symbol_blocked": same_blocked,
        "reentry_blocked": reentry_blocked,
        "cap_blocked_n": len(cap_blocked),
        "same_symbol_blocked_n": len(same_blocked),
        "reentry_blocked_n": len(reentry_blocked),
        "reentry_n": int(reentry_n),
        "delayed_cap_entry_n": int(delayed_cap_n),
        "delayed_same_symbol_entry_n": int(delayed_sym_n),
        "occupancy_mean_at_entry": (sum(occupancy_obs) / len(occupancy_obs)) if occupancy_obs else 0.0,
        "occupancy_max": int(max_occ),
        "same_bar_entry_n": int(sum(1 for t in accepted if t.get("same_bar_entry"))),
        "target_future_leakage_n": int(sum(1 for t in accepted if t.get("target_future_leakage"))),
        "exit_retroactive_n": int(sum(1 for t in accepted if t.get("exit_retroactive"))),
        "target_invalidation_ambiguous_n": int(sum(1 for t in accepted if t.get("target_invalidation_ambiguous"))),
    }
