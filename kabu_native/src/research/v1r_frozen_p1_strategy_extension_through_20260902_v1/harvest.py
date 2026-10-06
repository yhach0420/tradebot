"""Inventory, source-pin, activation alias, P1/P0 reuse. No current-source replay."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.isolation import NATIVE, PIN_DIR

_SCRIPTS = str(NATIVE / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from research.anchor_vs_event_driven.run_comparison import _load_json, find_capture_dir
from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.spec import (
    ANCHOR_SHA,
    CONTROL_STRATEGY_SHA,
    ENTRY_SHA,
    ENTRY_V1R_SHA,
    EXIT_SHA,
    EXTENSION_CANDIDATE_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    P1_FULL14_DAYS,
    P1_FULL_PNL,
    P1_FULL_TRADES,
    P1_RUNNER_SHA,
    PINNED_FILES,
    REPLAY_CODE_SHA,
    SIMPLE_TECH_CLOSED_VERDICT,
    STRATEGY_SHA,
    V1R_LIVE_DUAL_LANE_SHA,
    V1R_NATIVE_ENTRY_LIVE_SHA,
)
from small_paper.day_fixed_am_registration import canonical_membership_sha
from small_paper.v1r_exit_v2_activation_gate import CONTROL_STRATEGY_SHA as GATE_CONTROL
from small_paper.v1r_exit_v2_activation_gate import ENTRY_SHA as GATE_ENTRY
from small_paper.v1r_exit_v2_activation_gate import STRATEGY_SHA as GATE_STRATEGY
from small_paper.v1r_native_entry_live import ENTRY_SHA as LIVE_ENTRY
from small_paper.v1r_primary_runtime import ANCHOR_SHA as RT_ANCHOR
from small_paper.v1r_primary_runtime import V1R_SHA

GIT_ROOT = NATIVE.parent


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _file_sha(rel: str) -> str:
    p = NATIVE / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "MISSING"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def current_source_hashes() -> dict[str, Any]:
    rows = []
    all_eq = True
    for name, rel, expected in PINNED_FILES:
        got = _file_sha(rel)
        eq = got == expected
        all_eq = all_eq and eq
        rows.append({"component": name, "path": rel, "expected": expected, "current": got, "equal": eq})
    return {
        "all_equal": bool(all_eq),
        "rows": rows,
        "PIN_MODE": "CURRENT_FILE_HASH_EQUAL_TO_P1" if all_eq else "CURRENT_SOURCE_FORBIDDEN",
    }


def _git_show(rel: str, commit: str) -> Optional[bytes]:
    git_rel = ("kabu_native/" + rel.replace("\\", "/")).replace("\\", "/")
    try:
        return subprocess.check_output(["git", "-C", str(GIT_ROOT), "show", f"{commit}:{git_rel}"], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None


def _variants(blob: bytes) -> dict[str, bytes]:
    out = {"raw": blob, "lf": blob.replace(b"\r\n", b"\n")}
    if b"\r\n" not in blob:
        out["crlf"] = blob.replace(b"\n", b"\r\n")
    else:
        out["crlf"] = blob
    return out


def restore_p1_source() -> dict[str, Any]:
    """Restore P1 exact bytes into research-only PIN_DIR. Never checkout the main tree."""
    cur = current_source_hashes()
    if cur["all_equal"]:
        return {**cur, "restored": True, "PIN_MODE": "CURRENT_FILE_HASH_EQUAL_TO_P1", "ok": True, "blocker": ""}
    recovered: dict[str, Any] = {}
    missing: list[str] = []
    PIN_DIR.mkdir(parents=True, exist_ok=True)
    for row in cur["rows"]:
        if row["equal"]:
            recovered[row["component"]] = {"ok": True, "mode": "current_equals_p1", "sha": row["current"]}
            continue
        rel = str(row["path"])
        expected = str(row["expected"])
        log = subprocess.check_output(
            ["git", "-C", str(GIT_ROOT), "log", "--all", "--pretty=%H", "--", "kabu_native/" + rel.replace("\\", "/")],
            text=True,
            stderr=subprocess.DEVNULL,
        ).split()
        hit = None
        tried = 0
        for commit in log:
            blob = _git_show(rel, commit)
            if blob is None:
                continue
            for vname, data in _variants(blob).items():
                tried += 1
                if _sha_bytes(data) == expected:
                    dest = PIN_DIR / Path(rel).name
                    dest.write_bytes(data)
                    if _sha_bytes(dest.read_bytes()) != expected:
                        continue
                    hit = {"ok": True, "mode": "isolated_git_blob", "commit": commit, "variant": vname, "sha": expected, "path": str(dest)}
                    break
            if hit:
                break
        if not hit:
            missing.append(rel)
            recovered[row["component"]] = {"ok": False, "mode": "unrecoverable", "tried_commits": len(log), "tried_variants": tried, "expected": expected, "current": row["current"]}
        else:
            recovered[row["component"]] = hit
    ok = all(bool((recovered.get(r["component"]) or {}).get("ok")) for r in cur["rows"])
    return {
        **cur,
        "restored": bool(ok),
        "ok": bool(ok),
        "PIN_MODE": "ISOLATED_GIT_BLOB_RESTORE" if ok else "P1_SOURCE_UNRECOVERABLE",
        "components": recovered,
        "missing": missing,
        "blocker": "" if ok else ("P1 exact V1RNativeEntryLive/V1RLiveDualLane bytes are not in git history or current tree. Main checkout forbidden. Isolated restore failed."),
        "main_tree_checkout": False,
        "runtime_source_rewritten": False,
    }


def activation_alias_audit() -> dict[str, Any]:
    """ENTRY_SHA f288 is P1 ENTRY. dfd311 is parent V1R / Control Fixed600, not EXIT-V2 Primary."""
    roles = {
        "P1_ENTRY_component": {
            "sha": ENTRY_SHA,
            "role": "PASSIVE_FILL_ENTRY_V1 contract used by P1 replay / V1RNativeEntryLive.ENTRY_SHA",
            "source": "src/small_paper/v1r_native_entry_live.py ENTRY_SHA and v1r_exit_v2_activation_gate.ENTRY_SHA",
        },
        "Activation_ENTRY_V1R_SHA": {
            "sha": ENTRY_V1R_SHA,
            "role": "Parent Fixed600 V1R strategy identity (v1r_primary_runtime.V1R_SHA). Historical parent, not EXIT V2 Primary STRATEGY_SHA.",
            "source": "runtime_pins.json ENTRY_V1R_SHA / v1r_primary_runtime.V1R_SHA / build_identity entry_v1r_sha",
        },
        "CONTROL_STRATEGY_SHA": {
            "sha": CONTROL_STRATEGY_SHA,
            "role": "Control lane PASSIVE_FIXED600_FULL_STRATEGY_V1R SHADOW_CONTROL. Same hash as V1R_SHA. Not Primary ENTRY.",
            "source": "v1r_exit_v2_activation_gate.CONTROL_STRATEGY_SHA",
        },
        "P1_STRATEGY_SHA": {
            "sha": STRATEGY_SHA,
            "role": "Active Primary PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY",
            "source": "v1r_exit_v2_activation_gate.STRATEGY_SHA",
        },
    }
    same_dfd = ENTRY_V1R_SHA == CONTROL_STRATEGY_SHA == V1R_SHA
    distinct_entry = ENTRY_SHA != ENTRY_V1R_SHA
    gate_ok = GATE_ENTRY == ENTRY_SHA and GATE_STRATEGY == STRATEGY_SHA and GATE_CONTROL == CONTROL_STRATEGY_SHA
    live_ok = LIVE_ENTRY == ENTRY_SHA
    anchor_ok = RT_ANCHOR == ANCHOR_SHA
    conflict = not (same_dfd and distinct_entry and gate_ok and live_ok and anchor_ok)
    return {
        "ok": not conflict,
        "roles": roles,
        "why_p1_uses_f288": (
            "P1 identity.entry_sha is the ENTRY contract hash (PASSIVE_FILL_ENTRY_V1), "
            "identical to V1RNativeEntryLive.ENTRY_SHA and the activation-gate ENTRY_SHA. "
            "Replay economics follow that ENTRY component, not the parent V1R strategy hash."
        ),
        "activation_alias_meaning": (
            "ENTRY_V1R_SHA=dfd311... aliases V1R_SHA / CONTROL_STRATEGY_SHA: the immutable parent Fixed600 V1R "
            "strategy used as SHADOW Control. It is not STRATEGY_SHA (EXIT V2 Primary) and not ENTRY_SHA (passive fill)."
        ),
        "not_the_same_object": True,
        "ENTRY_SHA_eq_ENTRY_V1R_SHA": ENTRY_SHA == ENTRY_V1R_SHA,
        "ENTRY_V1R_SHA_eq_CONTROL_STRATEGY_SHA": ENTRY_V1R_SHA == CONTROL_STRATEGY_SHA,
        "ENTRY_V1R_SHA_eq_V1R_SHA": ENTRY_V1R_SHA == V1R_SHA,
        "gate_constants_match": gate_ok,
        "native_entry_constant_match": live_ok,
        "anchor_constant_match": anchor_ok,
        "semantic_conflict": bool(conflict),
    }


def simple_tech_closure() -> dict[str, Any]:
    from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.isolation import SIMPLE_TECH_REBASE_OUT

    body = _load(SIMPLE_TECH_REBASE_OUT / "report.json")
    req = dict(body.get("required") or {})
    return {
        "ok": str(req.get("VERDICT") or "") == SIMPLE_TECH_CLOSED_VERDICT and str(req.get("CASE") or "") == "C",
        "VERDICT": req.get("VERDICT"),
        "CASE": req.get("CASE"),
        "CURRENT_T3_STACK_CLOSED": req.get("CURRENT_T3_STACK_CLOSED"),
        "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": req.get("CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED"),
        "CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED": req.get("CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED"),
        "reopened": False,
    }


def _peek_seq(capture: Path) -> dict[str, Any]:
    parts = sorted(p for p in capture.glob("push_part_*.jsonl") if p.stat().st_size > 0)
    if not parts:
        return {"first_seq": None, "last_seq": None, "n_parts": 0, "size_bytes": 0, "line_count_scanned": False}
    size = sum(p.stat().st_size for p in parts)
    first_seq = None
    first_t = ""
    with parts[0].open("rb") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
                first_seq = int(rec.get("sequence") or 0) or None
                first_t = str(rec.get("received_at") or rec.get("event_time") or rec.get("persisted_at") or "")
            except Exception:
                first_seq = None
            break
    last_seq = None
    last_t = ""
    with parts[-1].open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        fh.seek(max(0, pos - 262144))
        chunk = fh.read().decode("utf-8", errors="replace")
    for line in reversed(chunk.splitlines()):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            last_seq = int(rec.get("sequence") or 0) or last_seq
            last_t = str(rec.get("received_at") or rec.get("event_time") or rec.get("persisted_at") or last_t)
            break
        except Exception:
            continue
    return {
        "first_seq": first_seq,
        "last_seq": last_seq,
        "first_event_peek": first_t,
        "last_event_peek": last_t,
        "n_parts": len(parts),
        "size_bytes": size,
        "line_count_scanned": False,
    }


def inventory_extension_day(day: str, *, today: str) -> dict[str, Any]:
    from _p1_inventory import classify, resolve_universe

    if str(day) in FORBIDDEN_INPUT_DAYS:
        return {"date": day, "capture_class": "INVALID", "replay_eligible": False, "exclusion_reason": "FORBIDDEN_INPUT", "universe_resolved": False}
    if str(day) > str(MAX_RESEARCH_DATE) or str(day) >= str(today):
        return {"date": day, "capture_class": "INVALID", "replay_eligible": False, "exclusion_reason": "FUTURE_OR_ACTIVE", "universe_resolved": False}
    cap = find_capture_dir(day)
    uni = resolve_universe(day, cap)
    seq = _peek_seq(cap) if cap else {}
    comp = _load_json(cap / "capture_completeness.json") if cap is not None else {}
    summary = _load_json(cap / "capture_summary.json") if cap is not None else {}
    event_hint = int(summary.get("total_events") or comp.get("event_count") or 0)
    if cap is not None and int(seq.get("n_parts") or 0) > 0:
        event_hint = max(event_hint, 1)
    seq_for_class = {
        **seq,
        "line_count": event_hint,
        "first_event": seq.get("first_event_peek") or "",
        "last_event": seq.get("last_event_peek") or "",
    }
    klass = classify(day, cap, uni, seq_for_class)
    first = str(seq.get("first_event_peek") or klass.get("first_event") or comp.get("actual_first_event_at") or "")
    last = str(seq.get("last_event_peek") or klass.get("last_event") or comp.get("actual_last_event_at") or "")
    uni_hash = canonical_membership_sha(list(uni.get("symbols") or [])) if uni.get("resolved") else None
    src = str(uni.get("source") or "")
    freeze_name = f"same_day_am_frozen_universe_{day}.json"
    same_day = bool(uni.get("resolved")) and (
        freeze_name in src
        or src.startswith("frozen:")
        or src.startswith("registration:")
        or src.startswith("am_csv:")
        or day in src
    )
    if not uni.get("resolved"):
        capture_class = "UNIVERSE_UNRESOLVED"
        eligible = False
    else:
        capture_class = str(klass.get("capture_class") or "INVALID")
        eligible = bool(klass.get("usable") and capture_class in {"FULL", "PARTIAL", "DEGRADED"} and same_day)
    return {
        "date": day,
        "jpx_trading_day": klass.get("jpx_trading_day"),
        "capture_exists": cap is not None,
        "capture_path": str(cap) if cap else "",
        "capture_class": capture_class,
        "first_event": first,
        "last_event": last,
        "am_coverage": klass.get("am_coverage"),
        "pm_coverage": klass.get("pm_coverage"),
        "dropped_event_count": klass.get("dropped_event_count"),
        "status": klass.get("status"),
        "seal_pass": klass.get("seal_pass"),
        "raw_vs_seal": bool(comp.get("raw_vs_seal_row_match")) if cap is not None else None,
        "first_seq": seq.get("first_seq"),
        "last_seq": seq.get("last_seq"),
        "size_bytes": seq.get("size_bytes"),
        "n_parts": seq.get("n_parts"),
        "universe_resolved": bool(uni.get("resolved")),
        "UNIVERSE_SOURCE": uni.get("source"),
        "UNIVERSE_N": uni.get("universe_n"),
        "UNIVERSE_HASH": uni_hash,
        "CONTRACT_ID": "DAY_FIXED_AM_RUNTIME_UNIVERSE_V1",
        "SAME_DAY": bool(same_day),
        "universe_reason": uni.get("reason"),
        "universe_notes": uni.get("notes"),
        "replay_eligible": bool(eligible),
        "exclusion_reason": (uni.get("reason") or klass.get("exclusion_reason") or "") if not eligible else "",
        "full": bool(klass.get("full") and uni.get("resolved") and same_day),
        "label": "REUSED_HISTORY_EXTENSION",
        "stress4": str(day) in {"20260828", "20260831", "20260901", "20260902"},
    }


def inventory_extension(*, today: str) -> list[dict[str, Any]]:
    return [inventory_extension_day(d, today=today) for d in EXTENSION_CANDIDATE_DAYS]


def reuse_p0() -> dict[str, Any]:
    from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.isolation import P02_OUT, P03_OUT, P04_OUT

    p03 = _load(P03_OUT / "report.json")
    p04 = _load(P04_OUT / "report.json")
    p02 = _load(P02_OUT / "report.json")
    exact = dict(p03.get("EXACT_RUNTIME") or {})
    return {
        "P0_3_reused": True,
        "P0_4_reused": True,
        "P0_3_verdict": p03.get("verdict") or p03.get("VERDICT"),
        "P0_3_trades": exact.get("trades") or (p03.get("EXACT_RUNTIME") or {}).get("trade_n"),
        "P0_3_pnl": exact.get("pnl"),
        "P0_3_LEDGER_SHA": p03.get("LEDGER_SHA_RUN1") or p03.get("LEDGER_SHA"),
        "P0_3_DIVERGENCE_CLASS": p03.get("DIVERGENCE_CLASS"),
        "P0_4_ALL_DAYS_TRADE_PARITY": p04.get("ALL_DAYS_TRADE_PARITY"),
        "P0_4_ALL_DAYS_ANCHOR_PARITY": p04.get("ALL_DAYS_ANCHOR_PARITY"),
        "P0_4_ALL_DAYS_PNL_PARITY": p04.get("ALL_DAYS_PNL_PARITY"),
        "P0_4_DETERMINISM": p04.get("DETERMINISM"),
        "P0_4_PER_DAY": [
            {
                "date": r.get("date"),
                "exact_trades": r.get("exact_trades"),
                "fast_trades": r.get("fast_trades"),
                "exact_pnl": r.get("exact_pnl"),
                "fast_pnl": r.get("fast_pnl"),
                "exact_sha": r.get("exact_sha"),
                "fast_sha": r.get("fast_sha"),
                "sha_eq": r.get("exact_sha") == r.get("fast_sha"),
            }
            for r in list(p04.get("PER_DAY") or [])
        ],
        "P0_2_ROOT_CAUSE_CLASS": p02.get("ROOT_CAUSE_CLASS"),
        "ACTUAL_20260820": {"label": "ACTUAL_PAPER_WITH_RUNTIME_DEFECT", "trades": 5, "pnl": -32600.0},
        "REPLAY_20260820": {"label": "FROZEN_P1_REPLAY", "trades": 12, "pnl": -19400.0},
        "IS_RUNTIME_DEFECT": True,
        "IS_REPLAY_DEFECT": False,
        "ROOT_CAUSE_CLASS": "FEATURE_STATE_DIFFERENCE",
        "ROOT_CAUSE_NOTE": (
            "Actual 5/-32600 is not Replay. P0-2 Capture ingest hole class=QUEUE_OVERFLOW produced a different live feature state. "
            "P0-3 Exact vs A_FIXED DIVERGENCE_CLASS=NONE (Replay matched A_FIXED). "
            "Protocol classification of Actual vs Replay: FEATURE_STATE_DIFFERENCE caused by runtime defect, not replay defect."
        ),
    }


def _canonical_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in trades:
        out.append(
            {
                "symbol": t.get("symbol"),
                "session": t.get("session"),
                "anchor_time": t.get("anchor_time"),
                "fill_time": round(float(t.get("fill_time") or 0.0), 6),
                "fill_price": t.get("fill_price"),
                "exit_time": round(float(t.get("exit_time") or 0.0), 6),
                "exit_price": t.get("exit_price"),
                "exit_reason": t.get("exit_reason"),
                "pnl_yen_100": round(float(t.get("pnl_yen_100") or 0.0), 4),
            }
        )
    out.sort(key=lambda r: (float(r["fill_time"]), str(r["symbol"])))
    return out


def ledger_sha(trades: list[dict[str, Any]]) -> str:
    blob = json.dumps(_canonical_trades(trades), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def reuse_p1_full14() -> dict[str, Any]:
    from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.isolation import P1_OUT

    body = _load(P1_OUT / "report.json")
    ident = dict(body.get("identity") or {})
    identity_ok = (
        ident.get("strategy_sha") == STRATEGY_SHA
        and ident.get("entry_sha") == ENTRY_SHA
        and ident.get("exit_sha") == EXIT_SHA
        and ident.get("anchor_sha") == ANCHOR_SHA
        and ident.get("replay_code_sha") == REPLAY_CODE_SHA
        and ident.get("V1RNativeEntryLive_sha") == V1R_NATIVE_ENTRY_LIVE_SHA
        and ident.get("V1RLiveDualLane_sha") == V1R_LIVE_DUAL_LANE_SHA
        and ident.get("p1_runner_sha") == P1_RUNNER_SHA
    )
    full = dict(body.get("PRIMARY_FULL") or {})
    days = [str(d) for d in list(full.get("day_list") or [])]
    days_ok = days == list(P1_FULL14_DAYS)
    trades = [dict(t) for t in list(body.get("trades") or []) if str(t.get("date") or "") in set(P1_FULL14_DAYS)]
    daily = {str(r.get("date")): r for r in list(body.get("daily") or [])}
    sha_rows = []
    sha_pass = True
    for day in P1_FULL14_DAYS:
        chunk = [t for t in trades if str(t.get("date")) == day]
        got = ledger_sha(chunk)
        exp = (daily.get(day) or {}).get("ledger_sha")
        ok = got == exp and exp
        sha_pass = sha_pass and bool(ok)
        sha_rows.append({"date": day, "n": len(chunk), "recomputed": got, "stored": exp, "pass": bool(ok)})
    pnl = round(sum(float(t.get("pnl_yen_100") or 0.0) for t in trades), 2)
    n_ok = len(trades) == int(P1_FULL_TRADES) and abs(pnl - float(P1_FULL_PNL)) < 1e-6
    return {
        "identity_ok": bool(identity_ok),
        "day_list_ok": bool(days_ok),
        "PRIOR_REUSED_FULL_DAY_N": 14 if days_ok else len(days),
        "PRIOR_REUSED_TRADE_N": len(trades),
        "reused_pnl": pnl,
        "trade_n_pnl_ok": bool(n_ok),
        "daily_ledger_sha_parity": "PASS" if sha_pass else "FAIL",
        "sha_rows": sha_rows,
        "ok": bool(identity_ok and days_ok and n_ok and sha_pass),
        "restreamed": False,
        "trades": trades,
        "daily": [daily.get(d) or {} for d in P1_FULL14_DAYS],
        "all_daily": list(body.get("daily") or []),
        "reference_day_list": list((body.get("REFERENCE_ALL_USABLE") or {}).get("day_list") or []),
        "reference_trades": [
            dict(t)
            for t in list(body.get("trades") or [])
            if str(t.get("date") or "") in set(str(d) for d in list((body.get("REFERENCE_ALL_USABLE") or {}).get("day_list") or []))
        ],
        "PRIMARY_FULL": {
            "days": 14,
            "trades": int(full.get("trades") or 0),
            "pnl": full.get("pnl"),
            "PF": full.get("PF"),
            "maxDD": full.get("maxDD"),
            "win": full.get("win"),
            "loss": full.get("loss"),
            "draw": full.get("draw"),
            "day_sign": body.get("day_sign"),
            "AM": full.get("AM"),
            "PM": full.get("PM"),
        },
        "REFERENCE_ALL_USABLE": body.get("REFERENCE_ALL_USABLE"),
        "p1_identity": ident,
    }
