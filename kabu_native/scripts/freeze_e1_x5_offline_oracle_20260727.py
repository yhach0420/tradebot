#!/usr/bin/env python3
"""Phase 0: Freeze E1_X5 Offline Oracle for 20260727 Capture BEFORE Runtime path changes.

Writes immutable oracle under:
  results/research/e1_x5_runtime_offline_parity_20260727/oracle_baseline/
Does NOT modify strategy code.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

# Reuse proven Offline-dense iterator from root-cause script (characterization baseline).
from scripts.run_e1_x5_pm_replay_root_cause_20260727 import (  # type: ignore
    SNAP_1240,
    THRESHOLD,
    iter_pm_events,
    load_universe,
    serialize_exits,
)

OUT = REPO / "results" / "research" / "e1_x5_runtime_offline_parity_20260727" / "oracle_baseline"


def _sha_stable(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _feat_hash(feats: dict[str, Any]) -> str:
    # Stable hash of feature vector (floats rounded for numeric stability).
    cleaned = {}
    for k in sorted(feats.keys()):
        v = feats[k]
        if isinstance(v, float):
            cleaned[k] = round(v, 10)
        else:
            cleaned[k] = v
    return _sha_stable(cleaned)


def main() -> int:
    from small_paper.canonical_board import best_bid_ask_for_mode
    from small_paper.e1_x5_dmid_score_provider import (
        KIND_MISSING,
        KIND_NO_SAMPLE,
        KIND_SCORE,
        DMidD4H6ScoreProvider,
    )
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession
    from research.upward_edge_identification_audit.features import features_for_groups
    from research.integrated_directional_entry_exit_strategy.constants import FIXED_HID

    OUT.mkdir(parents=True, exist_ok=True)
    universe = load_universe()
    provider = DMidD4H6ScoreProvider.maybe_create()
    e1 = E1X5ForwardShadowSession(enabled=True)

    event_rows: list[dict[str, Any]] = []
    eval_rows: list[dict[str, Any]] = []
    kind_c: Counter[str] = Counter()
    snap_1240: Optional[dict[str, Any]] = None
    n = 0

    # Characterization: processing order flags recorded per event
    order_spec = [
        "normalize_tick",
        "feature_engine_update",
        "exit_monitor_if_open",
        "sample_due_gate",
        "score_compute_if_due",
        "entry_try_if_score",
        "cap_same_symbol_in_try_entry",
        "no_reentry_same_event_after_exit_without_new_score",
    ]

    for ev in iter_pm_events(universe):
        n += 1
        if snap_1240 is None and ev["recv_ts"] >= SNAP_1240:
            snap_1240 = {
                "entries": len(e1.entries),
                "completed": len(e1.exits),
                "open": len(e1.positions),
                "pnl": float(sum(x["net_pnl_yen_100"] for x in e1.exits)),
            }

        sym = ev["symbol"]
        payload = ev["payload"]
        seq = ev.get("sequence")
        open_before = sym in e1.positions
        cap_before = len(e1.positions)
        exits_before = len(e1.exits)
        entries_before = len(e1.entries)

        # Observe updates FE always (inside provider)
        result = provider.observe(symbol=sym, payload=payload, day="20260727")
        bid, ask = best_bid_ask_for_mode(payload, mode="canonical")

        feat_hash = ""
        sample_reason = "not_due"
        score_v: Any = None
        if result.kind == KIND_SCORE and result.packet is not None:
            kind_c["SCORE"] += 1
            sample_reason = "periodic_or_state_change"
            score_v = float(result.packet.score)
            # Feature hash from engine after update
            st = provider._syms.get(sym)
            if st is not None and result.packet is not None:
                try:
                    # Rebuild snapshot for hash (engine already updated)
                    tick = provider._tick_from_payload(
                        symbol=sym, payload=payload, day="20260727", event_sequence=seq
                    )
                    # Don't double-update; use last hist via snapshot from current eng state
                    # Use packet identity + score as stable feature proxy when snapshot costly
                    feat_hash = _sha_stable(
                        {
                            "sample_id": result.packet.sample_id,
                            "score": round(score_v, 12),
                            "spread_bps": result.packet.spread_bps,
                            "bid": result.packet.bid,
                            "ask": result.packet.ask,
                        }
                    )
                except Exception:
                    feat_hash = ""
            e1.on_quote(
                symbol=result.packet.symbol,
                ts=result.packet.event_time,
                bid=result.packet.bid,
                ask=result.packet.ask,
                score=score_v,
                spread_bps=result.packet.spread_bps,
                sample_id=result.packet.sample_id,
                day=result.packet.day,
                mid=result.packet.mid,
                event_sequence=result.packet.event_sequence,
            )
        elif result.kind == KIND_MISSING:
            kind_c["MISSING"] += 1
            sample_reason = "score_missing"
            e1.on_missing_score(
                symbol=sym,
                ts=result.event_time or ev["recv_ts"],
                bid=bid,
                ask=ask,
                reason=result.reason or "NO_EVALUATION_MISSING_SCORE",
                sample_id=result.snapshot_id or "",
                event_sequence=result.event_sequence,
            )
        else:
            kind_c["NO_SAMPLE"] += 1
            sample_reason = "not_due"
            if open_before:
                e1.on_quote(
                    symbol=sym,
                    ts=result.event_time or ev["recv_ts"],
                    bid=bid,
                    ask=ask,
                    day="20260727",
                )

        exited = len(e1.exits) > exits_before
        entered = len(e1.entries) > entries_before
        row = {
            "event_id": ev["event_id"],
            "ingress_sequence": seq,
            "symbol": sym,
            "recv_ts": ev["recv_ts"].isoformat(),
            "event_time": (result.event_time or ev["recv_ts"]).isoformat()
            if (result.event_time or ev["recv_ts"])
            else None,
            "observe_kind": result.kind,
            "sample_reason": sample_reason,
            "feature_updated": True,  # provider.observe always updates FE when tick builds
            "exit_monitored": bool(open_before),
            "score_evaluated": result.kind == KIND_SCORE,
            "score": score_v,
            "feature_hash": feat_hash,
            "threshold": THRESHOLD,
            "spread_bps": (result.packet.spread_bps if result.packet else None),
            "position_before": open_before,
            "position_after": sym in e1.positions,
            "cap_before": cap_before,
            "cap_after": len(e1.positions),
            "exited": exited,
            "entered": entered,
            "missing_reason": result.reason if result.kind == KIND_MISSING else None,
        }
        event_rows.append(row)
        if result.kind in (KIND_SCORE, KIND_MISSING):
            eval_rows.append(row)

        if n % 100000 == 0:
            print(f"[oracle] n={n} exits={len(e1.exits)}", flush=True)

    if snap_1240 is None:
        snap_1240 = {
            "entries": len(e1.entries),
            "completed": len(e1.exits),
            "open": len(e1.positions),
            "pnl": float(sum(x["net_pnl_yen_100"] for x in e1.exits)),
        }

    trades = serialize_exits(e1.exits)
    pnl = float(sum(x["net_pnl_yen_100"] for x in e1.exits))
    manifest = {
        "oracle_id": "E1_X5_OFFLINE_ORACLE_20260727_PM",
        "frozen_at": datetime.now(JST).isoformat(),
        "capture_session": "session_ing_20260727_11752_1785113581_4db3b030",
        "processing_order": order_spec,
        "n_events": n,
        "observe_kinds": dict(kind_c),
        "trades": len(trades),
        "net_pnl_yen_100": pnl,
        "snap_1240": snap_1240,
        "crosscheck": {
            "trades_expected": 70,
            "pnl_expected": 45023.825,
            "trades_match": len(trades) == 70,
            "pnl_match": abs(pnl - 45023.825) < 0.01,
            "snap_entries_expected": 19,
            "snap_completed_expected": 15,
            "snap_open_expected": 4,
            "snap_pnl_expected": 17276.0,
        },
        "event_manifest_sha256": _sha_stable(
            [{"event_id": r["event_id"], "kind": r["observe_kind"], "score": r["score"]} for r in event_rows]
        ),
        "trade_ledger_sha256": _sha_stable(trades),
        "pm_forward_status": "NOT_ADOPTED",
        "old_live_173_not_target": True,
    }

    (OUT / "oracle_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "oracle_events.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in event_rows) + "\n",
        encoding="utf-8",
    )
    (OUT / "oracle_eval_events.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in eval_rows) + "\n",
        encoding="utf-8",
    )
    (OUT / "oracle_trades.json").write_text(
        json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest["crosscheck"], ensure_ascii=False, indent=2))
    print(f"OUT={OUT}")
    print(f"trades={len(trades)} pnl={pnl}")
    ok = (
        manifest["crosscheck"]["trades_match"]
        and manifest["crosscheck"]["pnl_match"]
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
