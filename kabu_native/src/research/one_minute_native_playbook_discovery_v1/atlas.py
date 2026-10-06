"""Symbol/sector behavior atlas and emergent groups. Discovery only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np


def _mean(xs: list[float]) -> float | None:
    arr = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if arr.size == 0:
        return None
    return float(np.mean(arr))


def _rate(xs: list[bool]) -> float | None:
    if not xs:
        return None
    return float(np.mean(np.asarray(xs, dtype=float)))


def _block_agree(by_block: dict[str, float], *, min_pos: int = 3) -> bool:
    vals = [v for v in by_block.values() if v is not None and np.isfinite(v)]
    if len(vals) < 4:
        return False
    return int(sum(1 for v in vals if v > 0)) >= int(min_pos)


def symbol_profiles(
    *,
    episodes: list[dict[str, Any]],
    opening: list[dict[str, Any]],
    lead_min: list[dict[str, Any]],
    sector_of: dict[str, str],
    symbols: list[str],
) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in episodes:
        by_sym[str(e["symbol"])].append(e)
    open_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in opening:
        open_by[str(r["symbol"])].append(r)
    lead_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in lead_min:
        lead_by[str(r["symbol"])].append(r)
    out = []
    for sym in symbols:
        xs = by_sym.get(sym) or []
        op = open_by.get(sym) or []
        ld = lead_by.get(sym) or []
        imp = [e for e in xs if str(e.get("sequence") or "").startswith("IMPULSE") or e.get("sequence") == "IMPULSE_UP"]
        cont = [e for e in xs if e.get("path_class") in {"CONTINUATION", "CONTINUATION_SECOND_LEG"}]
        rev = [e for e in xs if e.get("path_class") in {"REVERSAL", "FAILURE"}]
        fav = [bool(e.get("favor_first")) for e in xs if e.get("outcomes_attached")]
        lead_n = sum(int(r.get("leading_n") or 0) for r in ld)
        lag_n = sum(int(r.get("lagging_n") or 0) for r in ld)
        present_n = sum(int(r.get("present_n") or 0) for r in ld)
        hold = [bool(r.get("hold_0914")) for r in op if r.get("hold_0914") is not None]
        gap_up = [r for r in op if r.get("gap_up")]
        hold_up = [bool(r.get("hold_0914")) for r in gap_up if r.get("hold_0914") is not None]
        by_block_fav: dict[str, list[bool]] = defaultdict(list)
        by_block_x5: dict[str, list[float]] = defaultdict(list)
        for e in xs:
            b = str(e.get("block") or "")
            if e.get("outcomes_attached"):
                by_block_fav[b].append(bool(e.get("favor_first")))
            if e.get("x0_h5_bps") is not None:
                by_block_x5[b].append(float(e["x0_h5_bps"]))
        fav_block = {k: float(np.mean(vs) - 0.5) for k, vs in by_block_fav.items() if vs}
        seq_counts: dict[str, int] = defaultdict(int)
        seq_fav: dict[str, list[bool]] = defaultdict(list)
        for e in xs:
            seq_counts[str(e.get("sequence"))] += 1
            if e.get("outcomes_attached"):
                seq_fav[str(e.get("sequence"))].append(bool(e.get("favor_first")))
        best_seq = None
        best_p = -1.0
        for seq, vs in seq_fav.items():
            if len(vs) < 20:
                continue
            p = float(np.mean(vs))
            if p > best_p:
                best_p, best_seq = p, seq
        fail_seq = None
        fail_p = 2.0
        for seq, vs in seq_fav.items():
            if len(vs) < 20:
                continue
            p = float(np.mean(vs))
            if p < fail_p:
                fail_p, fail_seq = p, seq
        lead_frac = (lead_n / present_n) if present_n else None
        lag_frac = (lag_n / present_n) if present_n else None
        cont_p = (len(cont) / len(xs)) if xs else None
        rev_p = (len(rev) / len(xs)) if xs else None
        row = {
            "symbol": sym,
            "sector": sector_of.get(sym) or "",
            "episode_n": len(xs),
            "impulse_n": len(imp),
            "continuation_share": cont_p,
            "reversal_share": rev_p,
            "favor_first_p": _rate(fav),
            "mean_mfe_bps": _mean([e.get("mfe_bps") for e in xs]),
            "mean_mae_bps": _mean([e.get("mae_bps") for e in xs]),
            "mean_x0_h5_bps": _mean([e.get("x0_h5_bps") for e in xs]),
            "lead_frac": lead_frac,
            "lag_frac": lag_frac,
            "lead_minus_lag": (None if lead_frac is None or lag_frac is None else float(lead_frac) - float(lag_frac)),
            "opening_hold_p": _rate(hold),
            "opening_gap_up_hold_p": _rate(hold_up),
            "opening_n": len(op),
            "best_sequence": best_seq,
            "best_sequence_favor_p": None if best_seq is None else best_p,
            "failure_sequence": fail_seq,
            "failure_sequence_favor_p": None if fail_seq is None else fail_p,
            "d1_d4_favor_agree": _block_agree(fav_block),
            "block_favor": {k: float(np.mean(vs)) for k, vs in by_block_fav.items() if vs},
            "seq_n": dict(seq_counts),
        }
        out.append(row)
    return out


def sector_profiles(sym_rows: list[dict[str, Any]], episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sec: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in sym_rows:
        if r.get("sector"):
            by_sec[str(r["sector"])].append(r)
    ep_sec: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in episodes:
        if e.get("sector"):
            ep_sec[str(e["sector"])].append(e)
    out = []
    for sec, xs in sorted(by_sec.items(), key=lambda kv: -len(kv[1])):
        if len(xs) < 3:
            continue
        eps = ep_sec.get(sec) or []
        out.append(
            {
                "sector": sec,
                "symbol_n": len(xs),
                "episode_n": len(eps),
                "mean_favor_first_p": _mean([r.get("favor_first_p") for r in xs]),
                "mean_lead_frac": _mean([r.get("lead_frac") for r in xs]),
                "mean_lag_frac": _mean([r.get("lag_frac") for r in xs]),
                "mean_opening_hold_p": _mean([r.get("opening_hold_p") for r in xs]),
                "continuation_share": _mean([r.get("continuation_share") for r in xs]),
                "reversal_share": _mean([r.get("reversal_share") for r in xs]),
                "leaders": [r["symbol"] for r in sorted(xs, key=lambda z: -(z.get("lead_minus_lag") or -9))[:5] if (r.get("lead_minus_lag") or 0) > 0],
                "laggards": [r["symbol"] for r in sorted(xs, key=lambda z: (z.get("lead_minus_lag") or 9))[:5] if (r.get("lead_minus_lag") or 0) < 0],
            }
        )
    return out


def _z(xs: list[float | None]) -> list[float]:
    arr = np.asarray([0.0 if x is None or not np.isfinite(x) else float(x) for x in xs], dtype=float)
    sd = float(np.std(arr))
    mu = float(np.mean(arr))
    if sd <= 1e-12:
        return [0.0] * len(xs)
    return [float((x - mu) / sd) for x in arr]


def behavior_groups(sym_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not sym_rows:
        return {"groups": [], "members": {}, "n": 0}
    cont = _z([r.get("continuation_share") for r in sym_rows])
    rev = _z([r.get("reversal_share") for r in sym_rows])
    lead = _z([r.get("lead_minus_lag") for r in sym_rows])
    openp = _z([r.get("opening_gap_up_hold_p") if r.get("opening_gap_up_hold_p") is not None else r.get("opening_hold_p") for r in sym_rows])
    fav = _z([r.get("favor_first_p") for r in sym_rows])
    labels = []
    members: dict[str, list[str]] = defaultdict(list)
    for i, r in enumerate(sym_rows):
        scores = {
            "CONTINUATION": cont[i] - rev[i],
            "MEAN_REVERSION": rev[i] - cont[i],
            "SECTOR_LEADER": lead[i],
            "SECTOR_LAGGARD": -lead[i],
            "OPENING_MOMENTUM": openp[i],
        }
        # catchup if lagging but favor_first high
        if lead[i] < 0 and fav[i] > 0.25:
            scores["SECTOR_LAGGARD_CATCHUP"] = fav[i] - lead[i]
        best = max(scores.items(), key=lambda kv: kv[1])
        lab = best[0] if best[1] >= 0.45 and bool(r.get("d1_d4_favor_agree") or best[1] >= 0.80) else "IDIOSYNCRATIC"
        if int(r.get("episode_n") or 0) < 20:
            lab = "IDIOSYNCRATIC"
        r["behavior_group"] = lab
        r["group_score"] = best[1]
        labels.append(lab)
        members[lab].append(r["symbol"])
    groups = [{"group": k, "n": len(v), "symbols": v} for k, v in sorted(members.items(), key=lambda kv: -len(kv[1]))]
    return {"groups": groups, "members": dict(members), "n": len(groups), "emerged_from_path_scores": True, "preassigned": False}


def sequence_table(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in episodes:
        by[str(e.get("sequence"))].append(e)
    rows = []
    for seq, xs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        by_block: dict[str, list[float]] = defaultdict(list)
        fav = [bool(e.get("favor_first")) for e in xs if e.get("outcomes_attached")]
        for e in xs:
            if e.get("x0_h5_bps") is not None and e.get("block"):
                by_block[str(e["block"])].append(float(e["x0_h5_bps"]))
        block_mean = {k: float(np.mean(vs)) for k, vs in by_block.items() if vs}
        rows.append(
            {
                "sequence": seq,
                "n": len(xs),
                "day_n": len({e["date"] for e in xs}),
                "symbol_n": len({e["symbol"] for e in xs}),
                "sector_n": len({e.get("sector") for e in xs if e.get("sector")}),
                "favor_first_p": _rate(fav),
                "continuation_p": _rate([e.get("path_class") in {"CONTINUATION", "CONTINUATION_SECOND_LEG"} for e in xs]),
                "reversal_p": _rate([e.get("path_class") in {"REVERSAL", "FAILURE"} for e in xs]),
                "mean_mfe_bps": _mean([e.get("mfe_bps") for e in xs]),
                "mean_mae_bps": _mean([e.get("mae_bps") for e in xs]),
                "mean_x0_h5_bps": _mean([e.get("x0_h5_bps") for e in xs]),
                "block_mean_x0_h5": block_mean,
                "d1_d4_x0_agree": _block_agree(block_mean),
            }
        )
    return rows
