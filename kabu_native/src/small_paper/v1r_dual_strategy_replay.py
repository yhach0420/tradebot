"""Independent dual Full-Strategy replay: Arch E Primary + FIXED600 Control.

Shared: universe, anchors, model, features, scores, raw boards.
Independent: pending/open/cap/fills/EXIT/occupancy/later admission.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x34c_passive_deployability.events import build_events
from research.e1_x35_passive_exit.paths import build_path
from research.e1_x36_joint_allocator import LOT_QTY
from research.e1_x36_joint_allocator.panel import enrich_events
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.v1r_exit_v2_asymmetric.states import build_trade_bundle
from small_paper.v1r_day_engine import (
    _load_boards,
    _planned_anchors_retrospective,
    resolve_pre0905_am_universe,
    score_fn_frozen,
)
from small_paper.v1r_exit_v2_contract import patch_panel_exits


def _summarize(events: list[dict]) -> dict[str, Any]:
    acc = [e for e in events if e.get("accepted")]
    pnls = []
    for e in acc:
        yen = e.get("realized_pnl_yen")
        if yen is None:
            bps = e.get("realized_ret_bps") or e.get("canonical_exit_ret_bps") or 0
            px = float(e.get("fill_price") or 0)
            yen = float(LOT_QTY) * px * float(bps) / 10000.0
        pnls.append(float(yen))
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    eq = peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return {
        "n": len(acc),
        "total": float(sum(pnls)) if pnls else 0.0,
        "pf": None if pf == float("inf") else float(pf),
        "worst": float(min(pnls)) if pnls else 0.0,
        "best": float(max(pnls)) if pnls else 0.0,
        "gross_loss": float(gl),
        "gross_profit": float(gp),
        "max_dd": float(max_dd),
        "wins": sum(1 for p in pnls if p > 0),
        "losses": sum(1 for p in pnls if p < 0),
    }


def _fill_key(e: dict) -> tuple:
    return (str(e["date"]), str(e["symbol"]), float(e["fill_time"]))


def compare_divergence(primary_events: list[dict], control_events: list[dict]) -> dict[str, Any]:
    """Separate direct EXIT effects (common fills) vs occupancy-divergence fills."""
    p_acc = { _fill_key(e): e for e in primary_events if e.get("accepted") and e.get("fill_time") is not None }
    c_acc = { _fill_key(e): e for e in control_events if e.get("accepted") and e.get("fill_time") is not None }
    common = sorted(set(p_acc) & set(c_acc))
    only_p = sorted(set(p_acc) - set(c_acc))
    only_c = sorted(set(c_acc) - set(p_acc))

    def _pnl(e: dict) -> float:
        yen = e.get("realized_pnl_yen")
        if yen is not None:
            return float(yen)
        bps = float(e.get("realized_ret_bps") or e.get("canonical_exit_ret_bps") or 0)
        return float(LOT_QTY) * float(e.get("fill_price") or 0) * bps / 10000.0

    direct = []
    saved_loss = foregone = 0.0
    winners_cut = losers_saved = 0
    for k in common:
        pe, ce = p_acc[k], c_acc[k]
        pp, cp = _pnl(pe), _pnl(ce)
        delta = pp - cp
        row = {
            "key": k,
            "primary_pnl": pp,
            "control_pnl": cp,
            "delta": delta,
            "primary_reason": pe.get("canonical_exit_reason"),
            "control_reason": ce.get("canonical_exit_reason"),
            "guard": pe.get("exit_v2_triggered_guard"),
            "extended": pe.get("exit_v2_extended"),
        }
        direct.append(row)
        if cp < 0 and delta > 0:
            losers_saved += 1
            saved_loss += delta
        if cp > 0 and delta < 0:
            winners_cut += 1
            foregone += -delta

    occ_p = sum(_pnl(p_acc[k]) for k in only_p)
    occ_c = sum(_pnl(c_acc[k]) for k in only_c)
    ratio = (saved_loss / foregone) if foregone > 1e-9 else None
    return {
        "common_n": len(common),
        "primary_only_n": len(only_p),
        "control_only_n": len(only_c),
        "direct_exit_effect_pnl": float(sum(r["delta"] for r in direct)),
        "losers_saved": losers_saved,
        "winners_cut": winners_cut,
        "saved_loss_yen": float(saved_loss),
        "foregone_winner_yen": float(foregone),
        "saved_lost_ratio": ratio,
        "occupancy_divergence_effect": {
            "primary_only_pnl": float(occ_p),
            "control_only_pnl": float(occ_c),
            "net": float(occ_p - occ_c),
            "primary_only_keys": only_p[:20],
            "control_only_keys": only_c[:20],
        },
        "guard_trigger_n": sum(1 for e in p_acc.values() if e.get("exit_v2_triggered_guard")),
        "extend_750_n": sum(1 for e in p_acc.values() if e.get("exit_v2_extended")),
        "exit_600_n": sum(
            1 for e in p_acc.values()
            if (not e.get("exit_v2_triggered_guard")) and (not e.get("exit_v2_extended"))
        ),
    }


def build_bundles_for_panel(
    panel: list[dict],
    boards: dict,
) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for e in panel:
        if not (e.get("filled") and e.get("fill_time") is not None):
            continue
        board = boards.get((e["date"], e["symbol"]))
        if board is None:
            continue
        se = session_end_epoch(e["date"], e["session"])
        path = build_path(
            board,
            entry_price=float(e["fill_price"]),
            entry_t=float(e["fill_time"]),
            sess_end=se,
        )
        b = build_trade_bundle(e, path, board)
        out[(e["date"], e["symbol"], float(e["fill_time"]))] = b
    return out


def run_dual_day(
    day: str,
    *,
    universe: Optional[dict[str, Any]] = None,
    label: str = "dual_runtime",
) -> dict[str, Any]:
    """Independent Arch E + FIXED600 Control full-strategy replay for one day."""
    uni = universe or resolve_pre0905_am_universe(day)
    if not uni.get("pass"):
        return {"ok": False, "day": day, "label": label, "universe": uni, "blocked": uni.get("blocked_reason")}

    symbols = list(uni["symbols"])
    planned = _planned_anchors_retrospective(day, symbols)
    boards = _load_boards([(day, s) for s in symbols])
    raw = build_events(planned, boards)
    panel = enrich_events(raw, boards)  # FIXED600 labels initially; overwritten per lane
    sfn = score_fn_frozen()

    bundles = build_bundles_for_panel(panel, boards)
    panel_e = patch_panel_exits(panel, bundles, mode="arch_e")
    panel_c = patch_panel_exits(panel, bundles, mode="fixed600")
    sim_e = simulate_joint(panel_e, score_fn=sfn)
    sim_c = simulate_joint(panel_c, score_fn=sfn)

    div = compare_divergence(sim_e["events"], sim_c["events"])
    return {
        "ok": True,
        "day": day,
        "label": label,
        "universe": {k: uni[k] for k in ("pass", "symbols", "source") if k in uni},
        "primary": {
            "role": "PAPER_PRIMARY",
            "strategy": "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY",
            "exit": "ARCH_E",
            "summary": _summarize(sim_e["events"]),
            "sim": {k: v for k, v in sim_e.items() if k != "events"},
            "events": sim_e["events"],
        },
        "control": {
            "role": "SHADOW_CONTROL",
            "strategy": "PASSIVE_FIXED600_FULL_STRATEGY_V1R",
            "exit": "FIXED600",
            "summary": _summarize(sim_c["events"]),
            "sim": {k: v for k, v in sim_c.items() if k != "events"},
            "events": sim_c["events"],
        },
        "comparison": div,
    }
