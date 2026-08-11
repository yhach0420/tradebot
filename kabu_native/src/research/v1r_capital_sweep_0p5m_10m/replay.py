"""Bridge scorers + unlimited identity + capital continuous replay."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from research.e1_x36_joint_allocator.cv import outer_train_test
from research.e1_x36_joint_allocator.metrics import summarize_replay
from research.e1_x36_joint_allocator.models import fit_spec, score_fn_from_fit
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36c1m_capital_diagnostic.analyze import run_capital_continuous
from research.e1_x39b_universe_bridge import X36_SELECTED
from research.e1_x39b_universe_bridge.panel_build import build_am_panel, build_legacy_panel

from . import (
    BRIDGE_ADMITTED,
    BRIDGE_FILLS,
    BRIDGE_HARD_CAP,
    BRIDGE_PF,
    BRIDGE_PNL,
    BRIDGE_POS_DAYS,
)


def build_panels(*, cache_path: Path | None = None) -> dict[str, Any]:
    import pickle

    if cache_path is not None and cache_path.exists():
        print(f"=== load panel cache {cache_path} ===", flush=True)
        with cache_path.open("rb") as f:
            cached = pickle.load(f)
        print(
            f"  legacy signals={cached['legacy']['signals']} "
            f"am signals={cached['am']['signals']}",
            flush=True,
        )
        return cached

    print("=== build legacy panel (train SoT) ===", flush=True)
    legacy = build_legacy_panel()
    legacy_slim = {k: v for k, v in legacy.items() if k != "boards_keys"}
    print(f"  legacy signals={legacy['signals']} fills={legacy['fills']}", flush=True)
    print("=== build AM DAY_FIXED panel (test SoT) ===", flush=True)
    am = build_am_panel()
    am_slim = {k: v for k, v in am.items() if k != "boards_keys"}
    print(
        f"  am signals={am['signals']} fills={am['fills']} kind={am.get('kind')}",
        flush=True,
    )
    out = {"legacy": legacy_slim, "am": am_slim}
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as f:
            pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"  wrote panel cache {cache_path}", flush=True)
    return out


def build_bridge_scorers(legacy_panel: list[dict], *, specs: dict | None = None) -> dict[str, Any]:
    """Fit frozen outer specs on legacy train days; map each Historical14 date → scorer."""
    selected = specs or X36_SELECTED
    score_by_date: dict[str, Callable] = {}
    fold_meta = {}
    for block in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(block)
        train = [e for e in legacy_panel if e["date"] in train_days]
        spec = dict(selected[block])
        fit = fit_spec(train, spec)
        assert fit.get("kind") != "fail", (block, fit)
        sfn = score_fn_from_fit(fit)
        fold_meta[block] = {
            "spec": spec,
            "train_n": len(train),
            "test_days": sorted(test_days),
        }
        for d in test_days:
            score_by_date[d] = sfn
        print(
            f"  fold {block}: train_n={len(train)} test_days={sorted(test_days)} "
            f"spec={spec}",
            flush=True,
        )
    return {"score_by_date": score_by_date, "fold_meta": fold_meta}


def unlimited_bridge_identity(am_panel: list[dict], score_by_date: dict) -> dict[str, Any]:
    """Per-fold independent unlimited replay on AM panel — must match X39B bridge."""
    cross_events: list[dict] = []
    hard_cap = 0
    max_op = 0
    for block in ("A", "B", "C", "D"):
        _, test_days = outer_train_test(block)
        test = [e for e in am_panel if e["date"] in test_days]
        sfn = score_by_date[sorted(test_days)[0]]
        sim = simulate_joint(test, score_fn=sfn)
        cross_events.extend(sim["events"])
        hard_cap += int(sim.get("hard_cap_violations") or 0)
        max_op = max(max_op, int(sim.get("max_open_plus_pending") or 0))
    sm = summarize_replay({
        "events": cross_events,
        "hard_cap_violations": hard_cap,
        "max_open_plus_pending": max_op,
        "occupied_slot_sec": 0.0,
        "max_concurrent_notional_yen": 0.0,
        "p95_concurrent_notional_yen": 0.0,
        "max_pending_reserved_notional_yen": 0.0,
    })
    checks = {
        "pnl": abs(float(sm.get("total_pnl_yen") or 0) - BRIDGE_PNL) < 1.0,
        "pf": abs(float(sm.get("pf") or 0) - BRIDGE_PF) < 1e-9,
        "fills": int(sm.get("fills") or 0) == BRIDGE_FILLS,
        "admitted": int(sm.get("admitted") or 0) == BRIDGE_ADMITTED,
        "positive_days": int(sm.get("positive_days") or 0) == BRIDGE_POS_DAYS,
        "hard_cap": hard_cap == BRIDGE_HARD_CAP,
    }
    return {
        "summary": sm,
        "events": cross_events,
        "checks": checks,
        "pass": all(checks.values()),
        "observed": {
            "pnl": sm.get("total_pnl_yen"),
            "pf": sm.get("pf"),
            "fills": sm.get("fills"),
            "admitted": sm.get("admitted"),
            "positive_days": sm.get("positive_days"),
            "hard_cap": hard_cap,
        },
        "expected": {
            "pnl": BRIDGE_PNL,
            "pf": BRIDGE_PF,
            "fills": BRIDGE_FILLS,
            "admitted": BRIDGE_ADMITTED,
            "positive_days": BRIDGE_POS_DAYS,
            "hard_cap": BRIDGE_HARD_CAP,
        },
    }


def run_capital_level(
    am_panel: list[dict],
    score_by_date: dict,
    *,
    initial_cash: Optional[float],
) -> dict[str, Any]:
    """Full chronological capital-constrained replay (independent from other levels)."""
    return run_capital_continuous(am_panel, score_by_date, initial_cash=initial_cash)
