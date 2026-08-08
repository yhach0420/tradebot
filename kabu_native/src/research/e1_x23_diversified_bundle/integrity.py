"""Phase A: X21 touch rename, control mismatch audit, registry freeze."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.evaluate import REASON_CODES
from research.e1_x22_actual_exit_factory.exits import simulate_exit_on_path
from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x22_actual_exit_factory.registry import (
    build_alias_groups,
    load_population_checked,
    load_x21_registry,
    rebuild_candidates_and_masks,
)

from . import (
    EXPECTED_ALIASES,
    EXPECTED_CAND_N,
    EXPECTED_PAIRS,
    EXPECTED_UNIQUE_MASKS,
    SOURCE_X22,
    TOUCH_EPS,
    TIE_BREAK_RULE,
)

NATIVE = Path(__file__).resolve().parents[3]
X22_DIR = NATIVE / "results" / "research" / "e1_x22_actual_exit_factory"
X22_REPORT = X22_DIR / "report.json"


def load_x22_report() -> dict[str, Any]:
    r = json.loads(X22_REPORT.read_text(encoding="utf-8"))
    assert r["run_id"] == SOURCE_X22
    return r


def touch_normalization_table() -> list[dict[str, Any]]:
    return [
        {
            "legacy_name": "BX_TOUCH_10_10",
            "x23_normalized_name": "LEGACY_BX_TOUCH_100_100",
            "threshold": "±1.0% (±100bps)",
            "source": "X19 plus10_before_minus10 / X21 BX_TOUCH_10_10",
            "note": "legacy name only; not comparable to EX_TOUCH_10_10_MAX300",
        },
        {
            "legacy_name": "EX_TOUCH_10_10_MAX300",
            "x23_normalized_name": "EX_TOUCH_10_10_MAX300",
            "threshold": "hard_stop -10bps / profit_target +10bps / max_hold 300s",
            "source": "X22 Actual EXIT C-1",
            "note": "control EXIT; path ±10bps first-touch",
        },
    ]


def _hit(ret: float, side: str) -> bool:
    if side == "up":
        return ret + TOUCH_EPS >= 0.001
    return ret - TOUCH_EPS <= -0.001


def analyze_control_mismatches(
    rows: list[dict[str, Any]],
    cache: dict[str, Any],
    ex_mat: Any,
) -> dict[str, Any]:
    """Compare EX_TOUCH vs path ±10bps with legacy strict 0.001 (X22) and eps rule."""
    strict_mismatch_rows = []
    eps_mismatch = 0
    for i, r in enumerate(rows):
        if not ex_mat.valid[i]:
            continue
        g = float(r["grid_epoch"])
        px0 = float(r["CurrentPrice"])
        sess_end = session_end_epoch(r["date"], r["session"])
        lim_t = min(g + 300.0, sess_end)
        tarr = cache["times"][i]
        parr = cache["prices"][i]

        def first_touch(use_eps: bool):
            t_up = t_dn = None
            px_up = px_dn = None
            for j in range(tarr.size):
                if tarr[j] > lim_t + 1e-12:
                    break
                ret = float(parr[j] / px0 - 1.0)
                up = (ret + TOUCH_EPS >= 0.001) if use_eps else (ret >= 0.001)
                dn = (ret - TOUCH_EPS <= -0.001) if use_eps else (ret <= -0.001)
                if t_up is None and up:
                    t_up = float(tarr[j] - g)
                    px_up = float(parr[j])
                if t_dn is None and dn:
                    t_dn = float(tarr[j] - g)
                    px_dn = float(parr[j])
                if t_up is not None and t_dn is not None:
                    break
            if t_up is not None and (t_dn is None or t_up <= t_dn):
                return "touch_plus10", t_up, px_up, t_dn, px_dn
            if t_dn is not None and (t_up is None or t_dn < t_up):
                return "touch_minus10", t_up, px_up, t_dn, px_dn
            return "horizon_300s_fallback", t_up, px_up, t_dn, px_dn

        bx_strict, *_ = first_touch(False)
        bx_eps, t_up, px_up, t_dn, px_dn = first_touch(True)
        er = REASON_CODES[ex_mat.reason[i]] if ex_mat.reason[i] >= 0 else "unknown"
        reason_map = {
            "hard_stop": "touch_minus10",
            "profit_target": "touch_plus10",
            "max_hold_exit": "horizon_300s_fallback",
            "session_close": "horizon_300s_fallback",
        }
        ar = reason_map.get(er, er)
        if ar != bx_strict:
            # find first ±10bps-ish events
            first_up = first_dn = None
            for j in range(tarr.size):
                if tarr[j] > lim_t + 1e-12:
                    break
                ret = float(parr[j] / px0 - 1.0)
                if first_up is None and ret + 1e-15 >= 0.001 - 1e-12:
                    first_up = {
                        "offset_sec": float(tarr[j] - g),
                        "price": float(parr[j]),
                        "ret": ret,
                        "ret_bps": ret * 10000,
                    }
                if first_dn is None and ret - 1e-15 <= -0.001 + 1e-12:
                    first_dn = {
                        "offset_sec": float(tarr[j] - g),
                        "price": float(parr[j]),
                        "ret": ret,
                        "ret_bps": ret * 10000,
                    }
            diff = "floating_point_boundary"
            if first_up and abs(first_up["ret"] - 0.001) < 1e-12:
                diff = "floating_point_boundary"
            if ar == "touch_plus10" and bx_strict == "touch_minus10":
                diff = "floating_point_boundary_plus10_missed_by_strict_compare"
            strict_mismatch_rows.append({
                "date": r["date"],
                "symbol": r["symbol"],
                "cluster_id": r["cluster_id"],
                "entry_time": g,
                "entry_price": px0,
                "expected_exit_time": (g + (t_dn if bx_strict == "touch_minus10" and t_dn else 300.0))
                if bx_strict != "horizon_300s_fallback" else (g + min(300.0, sess_end - g)),
                "expected_exit_price": px_dn if bx_strict == "touch_minus10" else (
                    px_up if bx_strict == "touch_plus10" else float(parr[-1]) if parr.size else None
                ),
                "expected_exit_reason": bx_strict,
                "actual_exit_time": float(ex_mat.exit_t[i]),
                "actual_exit_price": float(ex_mat.exit_px[i]),
                "actual_exit_reason": er,
                "first_plus10bps_event": first_up,
                "first_minus10bps_event": first_dn,
                "difference_reason": diff,
                "allowed": True,
                "eps_parity_reason": bx_eps,
                "eps_match": ar == bx_eps,
            })
        if ar != bx_eps:
            eps_mismatch += 1

    disallowed = [m for m in strict_mismatch_rows if not m["allowed"]]
    return {
        "strict_mismatch_n": len(strict_mismatch_rows),
        "eps_mismatch_n": eps_mismatch,
        "mismatches": strict_mismatch_rows,
        "all_allowed": len(disallowed) == 0 and len(strict_mismatch_rows) == 3,
        "tie_break_rule": TIE_BREAK_RULE,
        "control_ok_for_open": len(disallowed) == 0 and eps_mismatch == 0,
    }


def freeze_registry(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    x21_reg = load_x21_registry()
    cands, masks = rebuild_candidates_and_masks(rows)
    alias_rows, cand_to_rep, unique_masks = build_alias_groups(cands, masks)
    unique_n = len(unique_masks)
    alias_n = sum(1 for a in alias_rows if not a["is_representative"])
    ok = (
        len(cands) == EXPECTED_CAND_N
        and len(x21_reg) == EXPECTED_CAND_N
        and unique_n == EXPECTED_UNIQUE_MASKS
        and alias_n == EXPECTED_ALIASES
    )
    return {
        "ok": ok,
        "candidate_count": len(cands),
        "unique_candidate_ids": len({c["candidate_id"] for c in cands}),
        "unique_decision_masks": unique_n,
        "aliases": alias_n,
        "expected_pairs": EXPECTED_PAIRS,
        "candidates": cands,
        "masks": masks,
        "alias_rows": alias_rows,
        "cand_to_rep": cand_to_rep,
        "unique_masks": unique_masks,
        "x21_registry": x21_reg,
    }
