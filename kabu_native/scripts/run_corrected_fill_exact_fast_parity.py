#!/usr/bin/env python
"""Corrected Passive Fill Exact vs Fast parity. Offline only. Does not start Paper/OPVAL.

Does not reuse P0-3 Exact cache (old fill semantics). Corrected Exact is the instrument.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

for _k in (
    "KABU_V1R_ENTRY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
    "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
    "KABU_SHADOW_DISCORD_WEBHOOK_URL",
    "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
    "KABU_MARKET_CAPTURE_WEBHOOK_URL",
):
    os.environ.pop(_k, None)
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from research.anchor_vs_event_driven.run_comparison import find_capture_dir  # noqa: E402
from run_p0_4_exact_vs_fast_parity import compare_pair, run_replay  # noqa: E402
from small_paper.v1r_primary_runtime import WAIT_SEC  # noqa: E402

OUT = ROOT / "results" / "research" / "passive_fill_corrected_rebase" / "_work_cache"
DAYS = ("20260820", "20260827")
GOLDEN = {"date": "20260827", "symbol": "5801", "anchor": "09:05"}


def compare_semantics(day: str, exact: dict[str, Any], fast: dict[str, Any]) -> dict[str, Any]:
    base = compare_pair(day, exact, fast)
    checks = {
        "PENDING": exact.get("pending_keys") == fast.get("pending_keys"),
        "FILL": exact.get("fill_keys") == fast.get("fill_keys"),
        "EXPIRED": exact.get("expired_keys") == fast.get("expired_keys"),
        "CAP": int(exact.get("cap_blocked") or 0) == int(fast.get("cap_blocked") or 0),
        "same_symbol": int(exact.get("same_symbol_blocked") or 0)
        == int(fast.get("same_symbol_blocked") or 0),
        "ENTRY": int(exact.get("native_admitted") or 0) == int(fast.get("native_admitted") or 0),
        "EXIT": exact.get("primary_exits") == fast.get("primary_exits"),
        "SLOT_RELEASE": exact.get("slot_releases") == fast.get("slot_releases"),
        "trade_ledger": bool(base.get("sha_match")) and int(base.get("trade_mismatch") or 0) == 0,
        "PnL": not bool(base.get("pnl_mismatch")),
        "event_time_fill": all(
            str(r.get("class") or "") == "MATCHED"
            or "fill_time" not in (r.get("fields") or [])
            for r in (base.get("rows") or [])
        ),
        "event_time_exit": all(
            str(r.get("class") or "") == "MATCHED"
            or "exit_time" not in (r.get("fields") or [])
            for r in (base.get("rows") or [])
        ),
    }
    golden_ok = True
    golden_note = ""
    if day == GOLDEN["date"]:
        bad = [
            f
            for f in (exact.get("fills") or []) + (fast.get("fills") or [])
            if str(f.get("symbol") or "") == GOLDEN["symbol"]
            and str(f.get("anchor") or "") == GOLDEN["anchor"]
        ]
        exp = [
            e
            for e in (exact.get("expired") or []) + (fast.get("expired") or [])
            if str(e.get("symbol") or "") == GOLDEN["symbol"]
            and str(e.get("anchor") or "") == GOLDEN["anchor"]
        ]
        golden_ok = not bad
        golden_note = f"fills={len(bad)} expired_hits={len(exp)}"
        checks["GOLDEN_5801_0905_NOT_FILL"] = golden_ok
    fail = [k for k, v in checks.items() if not v]
    return {
        **base,
        "semantic_checks": checks,
        "semantic_fail": fail,
        "semantic_pass": not fail,
        "golden_5801_0905": {"ok": golden_ok, "note": golden_note},
        "WAIT_SEC": WAIT_SEC,
    }


def run_day(day: str, capture: Path, *, determinism: bool) -> dict[str, Any]:
    print(f"PARITY {day} Exact", flush=True)
    exact = run_replay(day, capture, mode="exact")
    print(
        f"  exact trades={len(exact.get('trades') or [])} pnl={exact.get('pnl')} "
        f"fills={exact.get('native_fills')} expired={exact.get('native_expired')} "
        f"sec={exact.get('elapsed_sec')}",
        flush=True,
    )
    print(f"PARITY {day} Fast", flush=True)
    fast = run_replay(day, capture, mode="fast")
    print(
        f"  fast trades={len(fast.get('trades') or [])} pnl={fast.get('pnl')} "
        f"sha={fast.get('ledger_sha')} sec={fast.get('elapsed_sec')}",
        flush=True,
    )
    det = {"pass": True, "note": "not_requested"}
    if determinism:
        print(f"PARITY {day} Fast run2", flush=True)
        fast2 = run_replay(day, capture, mode="fast")
        det = {
            "pass": fast.get("ledger_sha") == fast2.get("ledger_sha")
            and len(fast.get("trades") or []) == len(fast2.get("trades") or []),
            "fast_sha_run1": fast.get("ledger_sha"),
            "fast_sha_run2": fast2.get("ledger_sha"),
        }
        print(f"  determinism={det['pass']}", flush=True)
    cmpd = compare_semantics(day, exact, fast)
    cmpd["determinism"] = det
    slim = {
        "date": day,
        "ok": bool(exact.get("ok") and fast.get("ok") and cmpd.get("semantic_pass") and det.get("pass")),
        "exact": {
            "ok": exact.get("ok"),
            "trades": len(exact.get("trades") or []),
            "pnl": exact.get("pnl"),
            "PF": exact.get("PF"),
            "maxDD": exact.get("maxDD"),
            "native_admitted": exact.get("native_admitted"),
            "native_fills": exact.get("native_fills"),
            "native_expired": exact.get("native_expired"),
            "cap_blocked": exact.get("cap_blocked"),
            "same_symbol_blocked": exact.get("same_symbol_blocked"),
            "anchor_fires": exact.get("anchor_fires"),
            "sequence_holes": exact.get("sequence_holes"),
            "ledger_sha": exact.get("ledger_sha"),
            "elapsed_sec": exact.get("elapsed_sec"),
                    "pending_n": len(exact.get("admits") or []),
                    "slot_release_n": len(exact.get("slot_releases") or []),
                    "primary_exit_n": len(exact.get("primary_exits") or []),
        },
        "fast": {
            "ok": fast.get("ok"),
            "trades": len(fast.get("trades") or []),
            "pnl": fast.get("pnl"),
            "PF": fast.get("PF"),
            "maxDD": fast.get("maxDD"),
            "native_admitted": fast.get("native_admitted"),
            "native_fills": fast.get("native_fills"),
            "native_expired": fast.get("native_expired"),
            "cap_blocked": fast.get("cap_blocked"),
            "same_symbol_blocked": fast.get("same_symbol_blocked"),
            "anchor_fires": fast.get("anchor_fires"),
            "sequence_holes": fast.get("sequence_holes"),
            "ledger_sha": fast.get("ledger_sha"),
            "elapsed_sec": fast.get("elapsed_sec"),
        },
        "cmp": {
            "trade_mismatch": cmpd.get("trade_mismatch"),
            "anchor_mismatch": cmpd.get("anchor_mismatch"),
            "pnl_mismatch": cmpd.get("pnl_mismatch"),
            "sha_match": cmpd.get("sha_match"),
            "semantic_fail": cmpd.get("semantic_fail"),
            "semantic_pass": cmpd.get("semantic_pass"),
            "golden_5801_0905": cmpd.get("golden_5801_0905"),
            "determinism": det,
        },
    }
    return slim


def main() -> int:
    if WAIT_SEC != 1.0:
        print("REFUSE: WAIT_SEC != 1.0", WAIT_SEC, flush=True)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    days_out = []
    for i, day in enumerate(DAYS):
        cap = find_capture_dir(day)
        if cap is None:
            print("MISSING_CAPTURE", day, flush=True)
            return 2
        body = run_day(day, cap, determinism=(i == 0))
        days_out.append(body)
        (OUT / f"parity_{day}.json").write_text(
            json.dumps(body, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        if not body.get("ok"):
            print("PARITY_FAIL", day, body.get("cmp"), flush=True)
            payload = {"verdict": "CORRECTED_EXACT_FAST_PARITY_FAIL", "days": days_out}
            (OUT / "parity_summary.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            return 2
    payload = {
        "verdict": "CORRECTED_EXACT_FAST_PARITY_PASS",
        "WAIT_SEC": WAIT_SEC,
        "reused_p0_3": False,
        "days": days_out,
        "SAFETY": "submit/cancel/live=0/0/0",
    }
    (OUT / "parity_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print("PARITY_PASS", OUT / "parity_summary.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
