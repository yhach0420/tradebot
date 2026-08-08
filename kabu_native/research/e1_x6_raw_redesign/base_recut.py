"""E1_X5 BASE re-cut onto the X6 analysis mask (Phase A-R2 §9).

The frozen stage-1 X5 trade ledger (PartitionBundle.x5_trades, read-only) is
mechanically re-aggregated under EXACTLY the X6 mask geometry: mask-included
windows, warmup anchor max(session_start, valid_start)+300s, and the 600s
decision horizon (entry_evaluable_until). No X6-candidate economics are
generated; only the fixed BASE ledger is re-aggregated. If the ledger cannot
be reconciled with the frozen stage-1 base, the result is NOT_COMPARABLE_BASE
=> P1_R2_BLOCKED.
"""
from __future__ import annotations

import gzip
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any

from .features import WARMUP_SEC
from .store import sha256_file, sha256_obj

BUNDLE_DIR = (
    Path.home() / "e1x6_research_store" / "oracle_bundles"
    / "e1x6_p21_20260802_204337_49eabae8"
)
STAGE1_BASE_N = 1058  # frozen stage-1 aggregate (reconciliation target)

DD_FORMULA = (
    "max drawdown of cumulative net_pnl_yen_100 in exit order "
    "(sort key: exit_time, then symbol) — mirrors day_robust_gates.realized_sequence_max_dd"
)
STOP_LOSS_FORMULA = "sum of negative net_pnl_yen_100 over exit_reason==STOP trades"


def _max_dd(trades: list[dict[str, Any]]) -> float:
    rows = sorted(trades, key=lambda t: (str(t.get("exit_time") or ""),
                                         str(t.get("symbol") or "")))
    eq = peak = dd = 0.0
    for t in rows:
        eq += float(t.get("net_pnl_yen_100") or 0.0)
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd


def _stop_loss_total(trades: list[dict[str, Any]]) -> float:
    return sum(
        p for t in trades
        if str(t.get("exit_reason") or "") == "STOP"
        and (p := float(t.get("net_pnl_yen_100") or 0.0)) < 0
    )


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def recut_base(mask: dict[str, Any]) -> dict[str, Any]:
    """Re-aggregate the frozen X5 ledger under the X6 mask. Read-only inputs."""
    if not BUNDLE_DIR.is_dir():
        return {"comparable": False, "reason": "NOT_COMPARABLE_BASE: bundle dir missing"}

    files = sorted(BUNDLE_DIR.glob("*.pkl.gz"))
    if not files:
        return {"comparable": False, "reason": "NOT_COMPARABLE_BASE: no partition bundles"}

    all_trades: list[dict[str, Any]] = []
    bundle_shas: dict[str, str] = {}
    partitions: list[str] = []
    for fp in files:
        bundle_shas[fp.name] = sha256_file(fp)
        with gzip.open(fp, "rb") as fh:
            b = pickle.load(fh)
        partitions.append(f"{b.day}_{b.am_pm}")
        all_trades.extend(b.x5_trades)

    if len(all_trades) != STAGE1_BASE_N:
        return {
            "comparable": False,
            "reason": (
                f"NOT_COMPARABLE_BASE: ledger reconciliation failed "
                f"({len(all_trades)} != frozen stage-1 n {STAGE1_BASE_N})"
            ),
        }

    kept: list[dict[str, Any]] = []
    excluded = {"WINDOW_NOT_INCLUDED": 0, "BEFORE_WARMUP": 0,
                "AFTER_ENTRY_HORIZON": 0, "BAD_ROW": 0}
    kept_by_window: dict[str, int] = {}
    for t in all_trades:
        wid = f"{t.get('day')}_{t.get('am_pm')}"
        row = (mask.get("windows") or {}).get(wid)
        if row is None or not row.get("included"):
            excluded["WINDOW_NOT_INCLUDED"] += 1
            continue
        try:
            t_e = _epoch(str(t["entry_time"]))
        except (KeyError, ValueError):
            excluded["BAD_ROW"] += 1
            continue
        vs = row.get("valid_start_epoch")
        anchor = max(row["expected_start_epoch"], vs if vs is not None else -1e18)
        if t_e < anchor + WARMUP_SEC:
            excluded["BEFORE_WARMUP"] += 1
            continue
        until = row.get("entry_evaluable_until_epoch")
        if until is not None and t_e > until + 1e-9:
            excluded["AFTER_ENTRY_HORIZON"] += 1
            continue
        kept.append(t)
        kept_by_window[wid] = kept_by_window.get(wid, 0) + 1

    artifact = {
        "kind": "E1_X5_BASE_RECUT_TO_X6_MASK",
        "analysis_mask_id": mask.get("analysis_mask_id"),
        "warmup_rule": f"entry_time >= max(session_start, valid_start) + {WARMUP_SEC}s",
        "horizon_rule": "entry_time <= entry_evaluable_until_epoch (600s horizon mask)",
        "source_bundles": bundle_shas,
        "source_partitions": sorted(partitions),
        "original_base": {"n": STAGE1_BASE_N},
        "excluded_counts": excluded,
        "kept_by_window": dict(sorted(kept_by_window.items())),
        "recut_metrics": {
            "completed_trades": len(kept),
            "pnl": round(sum(float(t.get("net_pnl_yen_100") or 0.0) for t in kept), 2),
            "max_dd": round(_max_dd(kept), 2),
            "stop_loss_total": round(_stop_loss_total(kept), 2),
        },
        "dd_formula": DD_FORMULA,
        "stop_loss_formula": STOP_LOSS_FORMULA,
        "note": (
            "fixed BASE ledger re-aggregated only; no X6 candidate economics "
            "were generated or referenced"
        ),
    }
    artifact["artifact_sha256"] = sha256_obj(artifact)
    return {"comparable": True, "reason": "", "artifact": artifact}
