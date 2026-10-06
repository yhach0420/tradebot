#!/usr/bin/env python
"""Post-fix Exact diagnostic on 20261005 Capture. Diagnostic only. Not Actual."""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

DAY = "20261005"
CAPTURE = (
    NATIVE
    / "data"
    / "market_capture"
    / DAY
    / "session_ing_20261005_24756_1791172731_17e86edb"
)
FREEZE_JSON = NATIVE / "runtime" / f"same_day_am_frozen_universe_{DAY}.json"
PM_CSV = NATIVE / "results" / "reports" / f"universe_core10_dynamic40_price_risk_pm_{DAY}.csv"
OUT = NATIVE / "results" / "operations" / "20261005_post_fix_exact_diagnostic"
JST = ZoneInfo("Asia/Tokyo")
RECOVERED_START = datetime(2026, 10, 5, 13, 1, 58, tzinfo=JST)
RECOVERED_END = datetime(2026, 10, 5, 15, 30, 0, tzinfo=JST)


def _bare(symbol: str) -> str:
    text = str(symbol or "").strip()
    if text.endswith(".T"):
        text = text[:-2]
    if "@" in text:
        text = text.split("@", 1)[0]
    return text.strip()


def _load_csv_symbols(path: Path) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            sym = _bare(str(row.get("symbol") or row.get("code") or ""))
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
    return out


def _identities(exe: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in exe.ledger:
        if row.get("event") != "ENTRY":
            continue
        rows.append(
            {
                "signal_uid": str(row.get("signal_uid") or ""),
                "symbol": str(row.get("symbol") or ""),
                "timestamp": float(row.get("signal_timestamp") or row.get("fill_timestamp") or 0.0),
            }
        )
    rows.sort(key=lambda r: (r["timestamp"], r["symbol"], r["signal_uid"]))
    return rows


def _identity_key(row: dict[str, Any]) -> tuple[str, str, float]:
    return (str(row["signal_uid"]), str(row["symbol"]), float(row["timestamp"]))


def _run(*, mode: str, universe: list[str], admission: Optional[Callable] = None) -> dict[str, Any]:
    from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, iter_push
    from small_paper.fixed_support_x1_session import FixedSupportX1SessionExecutor
    from small_paper.paper_session_executor import bind_outer_registration

    start_ts = RECOVERED_START.timestamp()
    end_ts = RECOVERED_END.timestamp()
    exe = FixedSupportX1SessionExecutor()
    if admission is not None:
        exe.admission = admission
        exe.admission_membership = list(universe)
        exe.admission_membership_source = "NO_GATE"
        exe.screening_session_diff = False
    else:
        bind_outer_registration(
            exe,
            native_root=NATIVE,
            trading_date=DAY,
            universe=universe,
        )
    exe.bind_production_schedule(day=DAY, kind="pm")
    scanned = 0
    window = 0
    for rec in iter_push(CAPTURE):
        scanned += 1
        payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else rec.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        when = capture_event_epoch(rec, payload)
        if when is None or when < start_ts or when > end_ts:
            continue
        event = {
            "symbol": _bare(str(rec.get("symbol") or payload.get("Symbol") or "")),
            "payload": payload,
            "received_at": rec.get("received_at"),
            "t0_push_received_at": rec.get("received_at"),
        }
        if event["symbol"]:
            window += 1
            exe.on_market_event(event)
    exe.close_at_runtime_boundary(day=DAY, kind="pm")
    counters = exe.x1_counters()
    identities = _identities(exe)
    return {
        "mode": mode,
        "classification": "DIAGNOSTIC_EXACT_REPLAY",
        "not_actual": True,
        "capture_dir": str(CAPTURE),
        "events_scanned": scanned,
        "events_in_window": window,
        "screening_session_diff": bool(getattr(exe, "screening_session_diff", False)),
        "admission_membership_n": len(getattr(exe, "admission_membership", []) or []),
        "admission_membership_source": str(getattr(exe, "admission_membership_source", "") or ""),
        "deny_reasons": dict(getattr(exe, "admission_denied_reasons", {}) or {}),
        "x1": counters,
        "entry_identities": identities,
        "entry_n": len(identities),
        "submit_cancel_live": "0/0/0",
    }


def main() -> int:
    freeze = json.loads(FREEZE_JSON.read_text(encoding="utf-8"))
    freeze_syms = [_bare(s) for s in (freeze.get("canonical_symbols") or [])]
    pm_syms = _load_csv_symbols(PM_CSV)
    freeze_set = set(freeze_syms)
    pm_set = set(pm_syms)

    freeze_ok = (
        len(freeze_syms) == 50
        and "9223" not in freeze_set
        and "4166" in freeze_set
    )
    freeze_run = _run(mode="freeze_admission_post_fix", universe=pm_syms)
    nogate_run = _run(
        mode="no_admission_gate",
        universe=freeze_syms,
        admission=lambda _s, _t: (True, "PASS"),
    )

    freeze_ids = freeze_run["entry_identities"]
    nogate_ids = nogate_run["entry_identities"]
    freeze_keys = [_identity_key(r) for r in freeze_ids]
    nogate_keys = [_identity_key(r) for r in nogate_ids]
    identity_equal = freeze_keys == nogate_keys
    expected_n = 168
    exact50_mismatch_denies = int((freeze_run.get("deny_reasons") or {}).get("EXACT50_FAIL_CLOSED") or 0)
    membership_mismatch = sorted(freeze_set.symmetric_difference(set(freeze_run.get("admission_membership") or []))) if freeze_run.get("admission_membership") else []

    body = {
        "classification": "DIAGNOSTIC_EXACT_REPLAY",
        "not_actual": True,
        "INVALID_FOR_STRATEGY_EVALUATION": True,
        "trading_date": DAY,
        "capture_dir": str(CAPTURE),
        "recovered_session_window": {
            "start": RECOVERED_START.isoformat(),
            "end": RECOVERED_END.isoformat(),
            "source_session": "live_session_130154",
        },
        "unwindowed_full_capture_note": {
            "entry_n": 222,
            "identity_equal_freeze_vs_nogate": True,
            "note": "Full capture 12:58-15:30 includes pre-recovery events. Recovered-session window is the 168 identity SoT.",
        },
        "freeze_universe": {
            "n": len(freeze_syms),
            "sha": freeze.get("canonical_membership_sha"),
            "9223_absent": "9223" not in freeze_set,
            "4166_present": "4166" in freeze_set,
            "ok": freeze_ok,
        },
        "pm_screening_csv": {
            "n": len(pm_syms),
            "9223_present": "9223" in pm_set,
            "4166_present": "4166" in pm_set,
            "set_equal_freeze": freeze_set == pm_set,
        },
        "freeze_admission_post_fix": {k: v for k, v in freeze_run.items() if k != "entry_identities"},
        "no_admission_gate": {k: v for k, v in nogate_run.items() if k != "entry_identities"},
        "identity_compare": {
            "expected_entry_n": expected_n,
            "freeze_entry_n": len(freeze_ids),
            "nogate_entry_n": len(nogate_ids),
            "count_only_is_not_pass": True,
            "signal_uid_symbol_timestamp_equal": identity_equal,
            "freeze_keys_sample": freeze_keys[:5],
            "missing_in_freeze": [
                {"signal_uid": a, "symbol": b, "timestamp": c}
                for a, b, c in nogate_keys
                if (a, b, c) not in set(freeze_keys)
            ][:20],
            "extra_in_freeze": [
                {"signal_uid": a, "symbol": b, "timestamp": c}
                for a, b, c in freeze_keys
                if (a, b, c) not in set(nogate_keys)
            ][:20],
        },
        "EXACT50_FAIL_CLOSED_from_pm_csv_mismatch": exact50_mismatch_denies,
        "freeze_membership_mismatch_n": len(membership_mismatch),
        "freeze_membership_mismatch": membership_mismatch,
        "submit_cancel_live": "0/0/0",
    }
    body["PASS"] = bool(
        freeze_ok
        and len(freeze_ids) == expected_n
        and len(nogate_ids) == expected_n
        and identity_equal
        and exact50_mismatch_denies == 0
        and not membership_mismatch
        and freeze_run.get("screening_session_diff") is True
    )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "entry_identities_freeze.json").write_text(
        json.dumps(freeze_ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "entry_identities_nogate.json").write_text(
        json.dumps(nogate_ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "report.json").write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# 20261005 post-fix Exact diagnostic",
        "",
        "classification: DIAGNOSTIC_EXACT_REPLAY (not Actual)",
        f"PASS: {body['PASS']}",
        f"freeze n={len(freeze_syms)} 9223_absent={body['freeze_universe']['9223_absent']} 4166_present={body['freeze_universe']['4166_present']}",
        f"freeze entry n={len(freeze_ids)} no-gate entry n={len(nogate_ids)} expected=168",
        f"identity equal: {identity_equal}",
        f"EXACT50_FAIL_CLOSED from PM CSV mismatch: {exact50_mismatch_denies}",
        f"freeze membership mismatch: {len(membership_mismatch)}",
        "submit/cancel/live: 0/0/0",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"PASS": body["PASS"], "freeze_entry_n": len(freeze_ids), "identity_equal": identity_equal, "exact50_fail_closed": exact50_mismatch_denies}, ensure_ascii=False))
    return 0 if body["PASS"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
