"""Prequential / walk-forward behavior groups. No full-Discovery static label leakage."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.behavior_group_sequence_mechanism_v1 import (
    GROUP_SCORE_FLOOR,
    GROUP_SCORE_STRONG,
    MIN_PROFILE_EPISODE_N,
    TRAIN_BLOCK,
)


def _mean(xs: list[Any]) -> float | None:
    arr = np.asarray([float(x) for x in xs if x is not None and np.isfinite(float(x))], dtype=float)
    if arr.size == 0:
        return None
    return float(np.mean(arr))


def _z(xs: list[float | None]) -> list[float]:
    arr = np.asarray([0.0 if x is None or not np.isfinite(x) else float(x) for x in xs], dtype=float)
    sd = float(np.std(arr))
    mu = float(np.mean(arr))
    if sd <= 1e-12:
        return [0.0] * len(xs)
    return [float((x - mu) / sd) for x in arr]


def aggregate_profiles(daily: list[dict[str, Any]], *, end_date: str) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in daily:
        if str(r.get("date") or "") > str(end_date):
            continue
        by[str(r["symbol"])].append(r)
    out = []
    for sym, xs in sorted(by.items()):
        lead_n = sum(int(r.get("leading_n") or 0) for r in xs)
        lag_n = sum(int(r.get("lagging_n") or 0) for r in xs)
        pres = sum(int(r.get("present_n") or 0) for r in xs)
        imp_n = sum(int(r.get("impulse_n") or 0) for r in xs)
        cont_n = sum(int(r.get("continuation_n") or 0) for r in xs)
        rev_n = sum(int(r.get("reversal_n") or 0) for r in xs)
        fav_n = sum(int(r.get("favor_first_n") or 0) for r in xs)
        open_n = sum(int(r.get("opening_n") or 0) for r in xs)
        hold_n = sum(int(r.get("opening_hold_n") or 0) for r in xs)
        gap_up_n = sum(int(r.get("gap_up_n") or 0) for r in xs)
        gap_up_hold_n = sum(int(r.get("gap_up_hold_n") or 0) for r in xs)
        lead_frac = (lead_n / pres) if pres else None
        lag_frac = (lag_n / pres) if pres else None
        out.append(
            {
                "symbol": sym,
                "sector": xs[-1].get("sector"),
                "train_day_n": len(xs),
                "profile_training_end": end_date,
                "episode_n": imp_n,
                "lead_frac": lead_frac,
                "lag_frac": lag_frac,
                "lead_minus_lag": (None if lead_frac is None or lag_frac is None else float(lead_frac) - float(lag_frac)),
                "continuation_share": (cont_n / imp_n) if imp_n else None,
                "reversal_share": (rev_n / imp_n) if imp_n else None,
                "favor_first_p": (fav_n / imp_n) if imp_n else None,
                "opening_hold_p": (hold_n / open_n) if open_n else None,
                "opening_gap_up_hold_p": (gap_up_hold_n / gap_up_n) if gap_up_n else None,
            }
        )
    return out


def assign_groups(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    if not profiles:
        return {"groups": [], "members": {}, "n": 0, "by_symbol": {}}
    cont = _z([r.get("continuation_share") for r in profiles])
    rev = _z([r.get("reversal_share") for r in profiles])
    lead = _z([r.get("lead_minus_lag") for r in profiles])
    openp = _z([r.get("opening_gap_up_hold_p") if r.get("opening_gap_up_hold_p") is not None else r.get("opening_hold_p") for r in profiles])
    fav = _z([r.get("favor_first_p") for r in profiles])
    members: dict[str, list[str]] = defaultdict(list)
    by_symbol: dict[str, dict[str, Any]] = {}
    for i, r in enumerate(profiles):
        scores = {
            "CONTINUATION": cont[i] - rev[i],
            "MEAN_REVERSION": rev[i] - cont[i],
            "SECTOR_LEADER": lead[i],
            "SECTOR_LAGGARD": -lead[i],
            "OPENING_MOMENTUM": openp[i],
        }
        if lead[i] < 0 and fav[i] > 0.25:
            scores["SECTOR_LAGGARD_CATCHUP"] = fav[i] - lead[i]
        best = max(scores.items(), key=lambda kv: kv[1])
        lab = "IDIOSYNCRATIC"
        if int(r.get("episode_n") or 0) >= MIN_PROFILE_EPISODE_N and float(best[1]) >= GROUP_SCORE_FLOOR:
            lab = best[0]
        # no d1_d4_favor_agree: that would require future blocks
        row = {
            **r,
            "behavior_group": lab,
            "group_score": float(best[1]),
            "best_label": best[0],
            "used_future_block_agree": False,
            "strong_score": float(best[1]) >= GROUP_SCORE_STRONG,
            "scores": {k: float(v) for k, v in scores.items()},
        }
        members[lab].append(r["symbol"])
        by_symbol[str(r["symbol"])] = row
    groups = [{"group": k, "n": len(v), "symbols": v} for k, v in sorted(members.items(), key=lambda kv: -len(kv[1]))]
    return {
        "groups": groups,
        "members": dict(members),
        "n": len(groups),
        "by_symbol": by_symbol,
        "preassigned": False,
        "prequential": True,
        "full_discovery_static": False,
        "future_block_agree_used": False,
    }


def maps_for_blocks(
    *,
    daily: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """D1 history → D2; D1+D2 → D3; D1+D2+D3 → D4. D1 is characterization only."""
    rows = []
    by_eval: dict[str, dict[str, str]] = {}
    defs = []
    for i, blk in enumerate(blocks):
        bid = str(blk["block_id"])
        if i == 0:
            by_eval[bid] = {}
            defs.append(
                {
                    "eval_block": bid,
                    "role": "characterization_only",
                    "profile_training_end": None,
                    "train_blocks": [],
                    "members": {},
                }
            )
            continue
        train = blocks[:i]
        end_date = str(train[-1]["last"])
        profiles = aggregate_profiles(daily, end_date=end_date)
        packed = assign_groups(profiles)
        mapping = {sym: str((packed["by_symbol"].get(sym) or {}).get("behavior_group") or "IDIOSYNCRATIC") for sym in packed["by_symbol"]}
        by_eval[bid] = mapping
        for sym, rec in packed["by_symbol"].items():
            rows.append(
                {
                    "eval_block": bid,
                    "symbol": sym,
                    "behavior_group_at_block_start": rec.get("behavior_group"),
                    "profile_training_end": end_date,
                    "train_day_n": rec.get("train_day_n"),
                    "group_score": rec.get("group_score"),
                    "episode_n_train": rec.get("episode_n"),
                    "lead_minus_lag": rec.get("lead_minus_lag"),
                    "continuation_share": rec.get("continuation_share"),
                    "reversal_share": rec.get("reversal_share"),
                    "favor_first_p": rec.get("favor_first_p"),
                    "opening_gap_up_hold_p": rec.get("opening_gap_up_hold_p"),
                    "full_discovery_label_used": False,
                }
            )
        defs.append(
            {
                "eval_block": bid,
                "role": "eval",
                "profile_training_end": end_date,
                "train_blocks": [str(b["block_id"]) for b in train],
                "members": packed.get("members"),
                "n_groups": packed.get("n"),
            }
        )
    return {
        "rows": rows,
        "by_eval_block": by_eval,
        "definitions": defs,
        "d1_characterization_only": True,
        "train_block": TRAIN_BLOCK,
        "full_discovery_group_leakage": False,
        "future_used_in_profile": False,
    }


def group_at(*, by_eval_block: dict[str, dict[str, str]], block: str, symbol: str) -> str | None:
    if str(block) == TRAIN_BLOCK:
        return None
    got = (by_eval_block.get(str(block)) or {}).get(str(symbol))
    return str(got) if got else None
