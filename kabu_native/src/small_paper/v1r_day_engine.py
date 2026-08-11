"""Frozen V1R single-day engine — shared by retrospective replay + production offline transport.

Does NOT mutate strategy/model/universe. Explicit RETROSPECTIVE allow for 20260810 only
inside this module (does not lift global FORBIDDEN_FROM for other research paths).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x28_executable_joint.board import load_board_events
from research.e1_x32_upstream_attribution import CLOCK_POINTS_HM
from research.e1_x32_upstream_attribution.eval_stages import clock_epochs_for_day
from research.e1_x34c_passive_deployability.events import build_events
from research.e1_x36_joint_allocator.panel import enrich_events
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized
from research.e1_x37_prospective.freeze import load_model_artifact, load_v1r
from small_paper.v1r_primary_activation_gate import (
    PRIMARY_STRATEGY,
    build_identity,
    heartbeat_identity_fields,
)
from small_paper.v1r_primary_runtime import (
    CLOCK_GRID,
    LOT_QTY,
    MODEL_ARTIFACT_SHA,
    POSITION_CAP,
    UNIVERSE_CONTRACT,
    V1R_SHA,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[2]

# Explicit allow-list for counterfactual retrospective replay only.
RETROSPECTIVE_ALLOWED_DAYS = frozenset({"20260810"})


def _norm_sym(s: str) -> str:
    s = str(s or "").strip().upper()
    if not s:
        return ""
    return s[:-2] if s.endswith(".T") else s


def resolve_pre0905_am_universe(day: str) -> dict[str, Any]:
    """Prove AM Core10+Dynamic40 membership was fixed before 09:05 using premarket artifacts."""
    assert day in RETROSPECTIVE_ALLOWED_DAYS or day < "20260810"
    pre_path = (
        NATIVE / "results/reports/phase687w15b_auto_universe_prebuild"
        / f"universe_prebuild_{day}.json"
    )
    am_path = (
        NATIVE / "results/reports"
        / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    )
    r1000_path = (
        NATIVE / "results/reports"
        / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day}.csv"
    )
    session_cfg = (
        NATIVE / "results/small_paper" / day / "live_session_075617" / "live_session_config.json"
    )

    evidence: list[str] = []
    symbols: list[str] = []
    blocked_reason = ""

    if not pre_path.exists():
        return {
            "pass": False,
            "blocked_reason": "V1R_20260810_REPLAY_UNIVERSE_CAUSALITY_BLOCKED:missing_prebuild",
            "symbols": [],
            "evidence": [],
        }

    pre = json.loads(pre_path.read_text(encoding="utf-8"))
    pre_started = str(pre.get("generator_started_at") or "")
    pre_finished = str(pre.get("generator_finished_at") or "")
    pre_syms = [
        _norm_sym(s) for s in (pre.get("validation_result") or {}).get("symbols") or []
    ]
    pre_syms = sorted({s for s in pre_syms if s})
    evidence.append(f"prebuild:{pre_path.name}:started={pre_started}:finished={pre_finished}:n={len(pre_syms)}")

    # Must finish before 09:05
    def _before_0905(iso: str) -> bool:
        if not iso:
            return False
        try:
            dt = datetime.fromisoformat(iso)
            return (dt.hour, dt.minute, dt.second) < (9, 5, 0) or "T07:" in iso or "T08:" in iso
        except Exception:
            return "T07:" in iso or "T08:" in iso

    if not (_before_0905(pre_started) and _before_0905(pre_finished)):
        blocked_reason = "V1R_20260810_REPLAY_UNIVERSE_CAUSALITY_BLOCKED:prebuild_not_before_0905"
        return {"pass": False, "blocked_reason": blocked_reason, "symbols": [], "evidence": evidence}

    symbols = pre_syms
    # Cross-check refresh1000 (07:56) and current AM csv membership identity
    import csv

    def _csv_syms(p: Path) -> list[str]:
        if not p.exists():
            return []
        out = []
        with p.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                s = _norm_sym(row.get("symbol") or row.get("code") or "")
                if s:
                    out.append(s)
        return sorted(set(out))

    r1000 = _csv_syms(r1000_path)
    am = _csv_syms(am_path)
    if r1000:
        evidence.append(f"am_refresh1000_mtime_pre0905_file:n={len(r1000)}:equal_pre={set(r1000)==set(pre_syms)}")
        if set(r1000) != set(pre_syms):
            blocked_reason = "V1R_20260810_REPLAY_UNIVERSE_CAUSALITY_BLOCKED:refresh1000_membership_mismatch"
            return {"pass": False, "blocked_reason": blocked_reason, "symbols": [], "evidence": evidence}
    if am:
        evidence.append(f"am_csv_membership_equal_pre={set(am)==set(pre_syms)}:n={len(am)}")
        # AM csv may be overwritten later in the day; membership identity is what matters
        if set(am) != set(pre_syms):
            blocked_reason = "V1R_20260810_REPLAY_UNIVERSE_CAUSALITY_BLOCKED:am_csv_membership_mismatch"
            return {"pass": False, "blocked_reason": blocked_reason, "symbols": [], "evidence": evidence}

    if session_cfg.exists():
        cfg = json.loads(session_cfg.read_text(encoding="utf-8"))
        evidence.append(f"session_universe_path={cfg.get('universe_csv_path')}")
        evidence.append(f"session_generated_at={cfg.get('generated_at')}")

    if len(symbols) != 50:
        blocked_reason = f"V1R_20260810_REPLAY_UNIVERSE_CAUSALITY_BLOCKED:symbol_count={len(symbols)}"
        return {"pass": False, "blocked_reason": blocked_reason, "symbols": symbols, "evidence": evidence}

    return {
        "pass": True,
        "blocked_reason": "",
        "symbols": symbols,
        "n": len(symbols),
        "has_285A": "285A" in symbols,
        "contract": UNIVERSE_CONTRACT,
        "day_fixed": True,
        "refresh_ignored": True,
        "evidence": evidence,
        "source": str(pre_path),
    }


def _planned_anchors_retrospective(day: str, symbols: list[str]) -> list[dict[str, Any]]:
    """Same semantics as planned_neutral_anchors without FORBIDDEN_FROM assert."""
    if day not in RETROSPECTIVE_ALLOWED_DAYS:
        raise RuntimeError(f"retrospective_day_not_allowed:{day}")
    anchors = []
    for epoch, sess in clock_epochs_for_day(day):
        for sym in symbols:
            anchors.append({
                "date": day,
                "symbol": sym,
                "session": sess,
                "grid_epoch": float(epoch),
                "anchor_id": "FIXED_CLOCK_V1",
            })
    return anchors


def _load_boards(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, np.ndarray]]:
    """Load boards via push_jsonl — bypasses load_boards_for_symbols FORBIDDEN assert."""
    cache: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    keys = sorted(set(pairs))
    print(f"  boards {len(keys)} (retrospective)...", flush=True)

    def _one(k):
        return k, load_board_events(k[0], k[1])

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_one, k) for k in keys]
        done = 0
        for fut in as_completed(futs):
            k, b = fut.result()
            cache[k] = b
            done += 1
            if done % 25 == 0 or done == len(keys):
                print(f"    boards {done}/{len(keys)}", flush=True)
    return cache


def score_fn_frozen():
    ser = load_model_artifact()
    assert ser.get("model_artifact_sha256") == MODEL_ARTIFACT_SHA
    v1r = load_v1r()
    assert v1r.get("sha256") == V1R_SHA
    sfn_raw = score_fn_from_serialized(ser)

    def _score(e: dict) -> float:
        feats = {k: e.get(k) for k in (
            "spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s",
            "event_rate_60s", "log_bid_qty",
        )}
        try:
            return float(sfn_raw(feats))
        except Exception:
            return float("-inf")

    return _score


def run_frozen_day(
    day: str,
    *,
    label: str = "canonical_research",
    universe: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Counterfactual frozen V1R replay for one day.
    label: canonical_research | production_offline_transport
    """
    uni = universe or resolve_pre0905_am_universe(day)
    if not uni.get("pass"):
        return {
            "ok": False,
            "label": label,
            "day": day,
            "universe": uni,
            "blocked": uni.get("blocked_reason") or "UNIVERSE_CAUSALITY_BLOCKED",
        }

    symbols = list(uni["symbols"])
    planned = _planned_anchors_retrospective(day, symbols)
    assert len(planned) == 16 * len(symbols)

    pairs = [(day, s) for s in symbols]
    boards = _load_boards(pairs)
    raw = build_events(planned, boards)
    panel = enrich_events(raw, boards)
    sfn = score_fn_frozen()
    sim = simulate_joint([dict(e) for e in panel], score_fn=sfn)
    events = sim["events"]

    # Performance on accepted fills with canonical exit
    fills = [e for e in events if e.get("accepted") and e.get("filled")]
    expired = [e for e in events if e.get("admitted") and e.get("expired")]
    admitted = [e for e in events if e.get("admitted")]

    pnls = []
    for e in fills:
        yen = e.get("realized_pnl_yen")
        if yen is None:
            bps = e.get("realized_ret_bps") or e.get("canonical_exit_ret_bps")
            px = float(e.get("fill_price") or e.get("limit_price") or 0)
            if bps is not None and px:
                yen = float(bps) / 10000.0 * px * LOT_QTY
            else:
                yen = 0.0
        pnls.append(float(yen))
        e["pnl_yen_100"] = float(yen)

    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    flats = sum(1 for p in pnls if p == 0)
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)

    # max realized DD (equity curve chronological by exit)
    fills_sorted = sorted(fills, key=lambda e: float(e.get("canonical_exit_time") or e.get("fill_time") or 0))
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_streak = 0
    for e in fills_sorted:
        p = float(e.get("pnl_yen_100") or 0)
        eq += p
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
        if p < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    # Anchor breakdown
    from collections import defaultdict
    by_anchor: dict[str, dict] = defaultdict(lambda: {
        "signals": 0, "admitted": 0, "fills": 0, "expired": 0,
        "pnl": 0.0, "wins": 0, "losses": 0, "flats": 0,
        "mfe": [], "mae": [],
    })
    for e in events:
        ts = float(e.get("signal_time") or 0)
        dt = datetime.fromtimestamp(ts, JST)
        ak = f"{dt.hour:02d}:{dt.minute:02d}"
        row = by_anchor[ak]
        row["signals"] += 1
        if e.get("admitted"):
            row["admitted"] += 1
        if e.get("accepted") and e.get("filled"):
            row["fills"] += 1
            p = float(e.get("pnl_yen_100") or 0)
            row["pnl"] += p
            if p > 0:
                row["wins"] += 1
            elif p < 0:
                row["losses"] += 1
            else:
                row["flats"] += 1
            if e.get("mfe") is not None:
                row["mfe"].append(float(e["mfe"]))
            if e.get("mae") is not None:
                row["mae"].append(float(e["mae"]))
        if e.get("admitted") and e.get("expired"):
            row["expired"] += 1

    anchor_rows = []
    for h, m in CLOCK_GRID:
        ak = f"{h:02d}:{m:02d}"
        r = by_anchor.get(ak, {})
        mfe_l = r.get("mfe") or []
        mae_l = r.get("mae") or []
        anchor_rows.append({
            "anchor": ak,
            "signals": r.get("signals", 0),
            "admitted": r.get("admitted", 0),
            "fills": r.get("fills", 0),
            "expired": r.get("expired", 0),
            "pnl": r.get("pnl", 0.0),
            "wins": r.get("wins", 0),
            "losses": r.get("losses", 0),
            "flats": r.get("flats", 0),
            "avg_mfe": float(np.mean(mfe_l)) if mfe_l else None,
            "avg_mae": float(np.mean(mae_l)) if mae_l else None,
        })

    # Symbol breakdown
    by_sym: dict[str, dict] = defaultdict(lambda: {
        "signal": 0, "fill": 0, "pnl": 0.0,
    })
    for e in events:
        sym = str(e.get("symbol"))
        by_sym[sym]["signal"] += 1
        if e.get("accepted") and e.get("filled"):
            by_sym[sym]["fill"] += 1
            by_sym[sym]["pnl"] += float(e.get("pnl_yen_100") or 0)
    pos_total = sum(max(0.0, v["pnl"]) for v in by_sym.values()) or 1.0
    sym_rows = []
    for sym, v in sorted(by_sym.items(), key=lambda x: -x[1]["pnl"]):
        gpos = max(0.0, v["pnl"])
        sym_rows.append({
            "symbol": sym,
            "signal": v["signal"],
            "fill": v["fill"],
            "pnl": v["pnl"],
            "gross_positive": gpos,
            "gross_positive_share": gpos / pos_total,
        })

    # ENTRY quality observations (no optimization)
    quality = []
    for e in fills:
        quality.append({
            "symbol": e.get("symbol"),
            "signal_time": e.get("signal_time"),
            "fill_time": e.get("fill_time"),
            "ret_60s": e.get("fill_based_ret_60") or e.get("ret_60"),
            "ret_180s": e.get("fill_based_ret_180") or e.get("ret_180"),
            "ret_300s": e.get("fill_based_ret_300") or e.get("ret_300"),
            "ret_600s": e.get("fill_based_ret_600") or e.get("ret_600") or e.get("canonical_exit_ret_bps"),
            "ret_900s": e.get("fill_based_ret_900") or e.get("ret_900"),
            "mfe": e.get("mfe"),
            "mae": e.get("mae"),
            "pnl_yen_100": e.get("pnl_yen_100"),
            "exit_reason": e.get("canonical_exit_reason"),
            "hold_sec": e.get("canonical_hold_sec"),
        })

    s285 = next((r for r in sym_rows if r["symbol"] == "285A"), None)

    import statistics
    result = {
        "ok": True,
        "label": label,
        "day": day,
        "classification": "RETROSPECTIVE_OPERATIONAL_REFERENCE",
        "replay_kind": "COUNTERFACTUAL_RETROSPECTIVE_REPLAY",
        "strategy": PRIMARY_STRATEGY,
        "strategy_sha": V1R_SHA,
        "model_sha": MODEL_ARTIFACT_SHA,
        "universe": uni,
        "identity": build_identity(),
        "flow": {
            "signals": int(sim.get("signals") or len(events)),
            "candidates": int(sim.get("signals") or len(events)),
            "admitted": int(sim.get("orders_admitted") or len(admitted)),
            "fills": int(sim.get("accepted_fills") or len(fills)),
            "expired": int(sim.get("expired_orders") or len(expired)),
            "fill_rate": float(sim.get("fill_rate_per_admitted") or (
                (len(fills) / len(admitted)) if admitted else 0.0
            )),
            "cap_blocked": int(sim.get("admission_blocked") or sum(1 for e in events if e.get("CAPACITY_BLOCKED"))),
            "closed": int(sim.get("accepted_fills") or len(fills)),
            "session_close_fallback": sum(
                1 for e in fills
                if "SESSION" in str(e.get("canonical_exit_reason") or "").upper()
            ),
            "hard_cap_violations": int(sim.get("hard_cap_violations") or 0),
            "max_open_plus_pending": int(sim.get("max_open_plus_pending") or 0),
        },
        "performance": {
            "wins": wins,
            "losses": losses,
            "flats": flats,
            "win_rate": (wins / len(pnls)) if pnls else 0.0,
            "total_pnl_yen_100": sum(pnls),
            "gross_profit": gp,
            "gross_loss": gl,
            "pf": pf if pf != float("inf") else None,
            "pf_infinite": pf == float("inf"),
            "avg_pnl": statistics.mean(pnls) if pnls else 0.0,
            "median_pnl": statistics.median(pnls) if pnls else 0.0,
            "best": max(pnls) if pnls else 0.0,
            "worst": min(pnls) if pnls else 0.0,
            "max_realized_dd": max_dd,
            "max_losing_streak": max_streak,
        },
        "anchors": anchor_rows,
        "symbols": sym_rows,
        "symbol_285A": {
            "universe_member": uni.get("has_285A"),
            "signals": (s285 or {}).get("signal", 0),
            "fills": (s285 or {}).get("fill", 0),
            "pnl": (s285 or {}).get("pnl", 0.0),
            "gross_positive_share": (s285 or {}).get("gross_positive_share", 0.0),
        },
        "entry_quality_obs": quality,
        "fills_detail": [
            {
                "symbol": e.get("symbol"),
                "signal_time": e.get("signal_time"),
                "fill_time": e.get("fill_time"),
                "fill_price": e.get("fill_price"),
                "limit_price": e.get("limit_price"),
                "rank_score": e.get("alloc_score"),
                "exit_time": e.get("canonical_exit_time"),
                "exit_reason": e.get("canonical_exit_reason"),
                "pnl_yen_100": e.get("pnl_yen_100"),
                "hold_sec": e.get("canonical_hold_sec"),
                "mfe": e.get("mfe"),
                "mae": e.get("mae"),
            }
            for e in fills_sorted
        ],
        "sim_meta": {
            "hard_cap_violations": sim.get("hard_cap_violations"),
            "max_open_plus_pending": sim.get("max_open_plus_pending"),
        },
        "heartbeat_sample": heartbeat_identity_fields(
            current_anchor="15:00", next_anchor=None, open_n=0, pending_n=0,
            extra={"replay": True, "label": label},
        ),
        "submit_cancel_live": "0/0/0",
        "strategy_mutation": False,
    }
    return result


def parity_compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Compare canonical vs production-offline transport results."""
    if not a.get("ok") or not b.get("ok"):
        return {"pass": False, "reason": "one_or_both_blocked", "a_ok": a.get("ok"), "b_ok": b.get("ok")}

    def _fill_keys(res):
        keys = []
        for e in res.get("fills_detail") or []:
            keys.append((
                str(e.get("symbol")),
                round(float(e.get("signal_time") or 0), 3),
                round(float(e.get("fill_time") or 0), 3),
                round(float(e.get("fill_price") or 0), 4),
            ))
        return sorted(keys)

    fa, fb = _fill_keys(a), _fill_keys(b)
    pnl_a = float((a.get("performance") or {}).get("total_pnl_yen_100") or 0)
    pnl_b = float((b.get("performance") or {}).get("total_pnl_yen_100") or 0)
    checks = {
        "fill_identity": fa == fb,
        "fill_count": len(fa) == len(fb),
        "admitted": (a.get("flow") or {}).get("admitted") == (b.get("flow") or {}).get("admitted"),
        "expired": (a.get("flow") or {}).get("expired") == (b.get("flow") or {}).get("expired"),
        "pnl": abs(pnl_a - pnl_b) < 1e-6,
        "pf": (a.get("performance") or {}).get("pf") == (b.get("performance") or {}).get("pf"),
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "fill_n_a": len(fa),
        "fill_n_b": len(fb),
        "pnl_a": pnl_a,
        "pnl_b": pnl_b,
        "verdict": "PASS" if all(checks.values()) else "PRODUCTION_REPLAY_PARITY_FAIL",
    }
