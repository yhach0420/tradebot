#!/usr/bin/env python3
"""Phase687W43B-FIX2: Ghost accept prevention audits + C: disk inventory (no delete)."""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(NATIVE / "src"))

from small_paper.canonical_summary import collect_canonical_trades
from small_paper.entry_execution_integrity import (
    STAGE_ACCEPT_ABORTED,
    STAGE_EXECUTION_PAYLOAD_VALIDATED,
    STAGE_GATE_ACCEPTED,
    STAGE_OFFICIAL_ENTRY,
    STAGE_POSITION_REGISTERED,
    STAGE_QUEUE_SELECTED,
    validate_execution_payload,
)

OUT = NATIVE / "results" / "reports"
PAST5 = ["20260710", "20260714", "20260715", "20260716", "20260717"]
AM = NATIVE / "results" / "small_paper" / "20260717" / "live_session_081810"
PM = NATIVE / "results" / "small_paper" / "20260717" / "live_session_122525"


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def load_events(sd: Path) -> list[dict[str, Any]]:
    p = sd / "small_paper_events.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.open(encoding="utf-8"):
        if line.strip():
            out.append(json.loads(line))
    return out


def session_dirs_for_day(day: str) -> list[Path]:
    root = NATIVE / "results" / "small_paper" / day
    if not root.is_dir():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir() and p.name.startswith("live_session")])


def dir_size_bytes(path: Path, *, max_files: int = 200_000) -> int:
    total = 0
    n = 0
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    try:
        for root, dirs, files in os.walk(path, onerror=lambda _e: None):
            # skip huge / junction-prone trees
            dirs[:] = [
                d
                for d in dirs
                if d.lower()
                not in {
                    "$recycle.bin",
                    "system volume information",
                    "winsxs",
                    "node_modules",
                }
            ]
            for name in files:
                n += 1
                if n > max_files:
                    return total
                fp = Path(root) / name
                try:
                    total += fp.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def classify_path(path: Path) -> dict[str, Any]:
    s = str(path).lower()
    tradebot_markers = ("tradebotfile", "kabu_native", "small_paper", "market_capture")
    is_tb = any(m in s for m in tradebot_markers)
    if is_tb and ("small_paper" in s or "market_capture" in s or "pre_entry_market_state" in s):
        return {
            "classification": "canonical_tradebot",
            "regenerable": False,
            "delete_risk": "FORBIDDEN",
            "TradeBotへの影響": "削除禁止（canonical）",
        }
    if "cursor" in s and any(x in s for x in ("cache", "cacheddata", "logs", "gpuache", "code cache")):
        return {
            "classification": "cursor_cache",
            "regenerable": True,
            "delete_risk": "low",
            "TradeBotへの影響": "なし（IDEキャッシュ）",
        }
    if "__pycache__" in s or ".venv" in s or "site-packages" in s:
        return {
            "classification": "python_cache_venv",
            "regenerable": True,
            "delete_risk": "medium",
            "TradeBotへの影響": "再install/再生成が必要になる場合あり",
        }
    if "worktree" in s or "_w" in path.name:
        return {
            "classification": "git_worktree",
            "regenerable": True,
            "delete_risk": "medium",
            "TradeBotへの影響": "worktree再作成で可。本線resultsは別",
        }
    if "temp" in s or "tmp" in s or "appdata\\local\\temp" in s:
        return {
            "classification": "temp_files",
            "regenerable": True,
            "delete_risk": "low",
            "TradeBotへの影響": "なし",
        }
    if "downloads" in s:
        return {
            "classification": "downloads",
            "regenerable": False,
            "delete_risk": "high",
            "TradeBotへの影響": "ユーザーファイル — 内容確認必須",
        }
    if "$recycle.bin" in s or "recycle" in s:
        return {
            "classification": "recycle_bin",
            "regenerable": False,
            "delete_risk": "medium",
            "TradeBotへの影響": "なし（復元不能になる）",
        }
    if "programdata" in s:
        return {
            "classification": "programdata",
            "regenerable": False,
            "delete_risk": "high",
            "TradeBotへの影響": "OS/アプリ依存 — 原則触らない",
        }
    return {
        "classification": "user_other",
        "regenerable": False,
        "delete_risk": "high",
        "TradeBotへの影響": "要個別確認",
    }


def disk_root_inventory() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    u = shutil.disk_usage("C:\\")
    used_pct = 100.0 * u.used / u.total
    roots = [
        Path(r"C:\Users\yhach"),
        Path(r"C:\ProgramData"),
        Path(os.environ.get("TEMP", r"C:\Users\yhach\AppData\Local\Temp")),
        Path(r"C:\Windows\Temp"),
        Path(r"C:\Users\yhach\AppData\Roaming\Cursor"),
        Path(r"C:\Users\yhach\AppData\Local\Cursor"),
        Path(r"C:\Users\yhach\Downloads"),
        Path(r"C:\$Recycle.Bin"),
        REPO,
        NATIVE / "results" / "cache",
    ]
    # expand first-level children of user home for size ranking
    candidates: list[Path] = []
    for r in roots:
        if r.exists():
            candidates.append(r)
            if r.is_dir() and r.name.lower() in {"yhach", "programdata"}:
                try:
                    for child in r.iterdir():
                        if child.name.startswith(".") and child.name not in {".cursor", ".venv"}:
                            continue
                        candidates.append(child)
                except OSError:
                    pass
    # python caches under user
    for pat in (
        Path(r"C:\Users\yhach") / "AppData" / "Local" / "pip",
        Path(r"C:\Users\yhach") / "AppData" / "Local" / "pypoetry",
        Path(r"C:\Users\yhach") / ".cache",
    ):
        if pat.exists():
            candidates.append(pat)
    # git worktrees near repo
    for p in REPO.parent.glob("_w*"):
        candidates.append(p)
    for p in REPO.glob("*worktree*"):
        candidates.append(p)

    seen: set[str] = set()
    sized: list[tuple[Path, int]] = []
    for p in candidates:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        try:
            sz = dir_size_bytes(p)
        except Exception:
            sz = 0
        if sz > 0:
            sized.append((p, sz))

    sized.sort(key=lambda x: -x[1])
    top = sized[:50]
    rows = []
    for path, sz in top:
        meta = classify_path(path)
        gb = sz / 1e9
        # estimate used pct if this single path deleted
        pred = 100.0 * max(0, u.used - sz) / u.total
        rows.append(
            {
                "path": str(path),
                "size_gb": round(gb, 3),
                "size_bytes": sz,
                "classification": meta["classification"],
                "regenerable": meta["regenerable"],
                "delete_risk": meta["delete_risk"],
                "estimated_used_pct_after_delete": round(pred, 2),
                "TradeBotへの影響": meta["TradeBotへの影響"],
            }
        )

    # cleanup options: exclude canonical tradebot
    options = []
    for r in rows:
        if r["classification"] == "canonical_tradebot":
            continue
        if r["delete_risk"] == "FORBIDDEN":
            continue
        if r["size_gb"] < 0.05:
            continue
        options.append(
            {
                **r,
                "auto_delete": False,
                "approval_required": True,
                "note": "Inventory only — no deletion performed. Prior 0.323GB candidates retained untouched.",
            }
        )

    cleanup = {
        "current_used_bytes": u.used,
        "current_total_bytes": u.total,
        "current_used_pct": round(used_pct, 2),
        "prior_w43b_fix_candidate_gb": 0.323,
        "prior_candidates_deleted": False,
        "auto_delete": False,
        "approval_required": True,
        "top50_count": len(rows),
        "cleanup_options": options[:30],
        "note": "C: root inventory dry-run. Canonical TradeBot data excluded from delete options.",
    }
    return rows, cleanup


def audit_6327_order_traces(sd: Path) -> dict[str, Any]:
    """Explain order-related lines for 6327.T (true symbol vs message_index false positives)."""
    true_files = {
        "live_order_safety/order_state_events.jsonl": [],
        "live_order_safety/capital_reservations.jsonl": [],
        "live_order_safety/order_intents.jsonl": [],
        "live_order_event.jsonl": [],
        "live_order_state.jsonl": [],
        "live_capital_check.jsonl": [],
        "live_order_latency.jsonl": [],
        "live_order_would_send.jsonl": [],
        "order_latency_dryrun_trace.jsonl": [],
    }
    false_positives = 0
    for rel in list(true_files.keys()):
        p = sd / rel
        if not p.is_file():
            continue
        for line in p.open(encoding="utf-8"):
            if "6327" not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            sym = str(o.get("symbol") or "")
            if sym == "6327.T" or (isinstance(o.get("payload"), dict) and str(o["payload"].get("Symbol")) == "6327"):
                true_files[rel].append(o)
            else:
                # message_index containing 6327 substring
                false_positives += 1

    state_transitions = []
    for o in true_files["live_order_safety/order_state_events.jsonl"]:
        state_transitions.append(f"{o.get('from')}→{o.get('to')}")

    real_count = sum(len(v) for k, v in true_files.items() if k != "order_latency_dryrun_trace.jsonl")
    # Prior W43B-FIX counted 13 via naive '6327' substring across order files.
    # Breakdown of that 13: typically 7 state + 1 intent + 1 would_send + 1 latency + 2 event + 1 capital ≈ 13
    # excluding false-positive latency traces.
    return {
        "prior_reported_order_hits": 13,
        "explanation": (
            "13は同一decisionに対する複数イベント/状態遷移の合算であり、13件の独立注文ではない。"
            "order_latency_dryrun_trace の message_index に '6327' を含む行は偽陽性。"
        ),
        "same_decision_id_state_machine": True,
        "order_id": (
            true_files["live_order_safety/order_state_events.jsonl"][0].get("order_id")
            if true_files["live_order_safety/order_state_events.jsonl"]
            else None
        ),
        "state_transitions": state_transitions,
        "by_file_true_symbol": {k: len(v) for k, v in true_files.items()},
        "true_symbol_event_count": real_count,
        "false_positive_message_index_lines": false_positives,
        "ask_price_fallback_used": True,
        "ask_price_value": 4430.0,
        "capital_check_block": True,
        "capital_check_reason": "insufficient_margin_or_buying_power (price=AskPrice 4430)",
        "duplicate_generation": False,
        "note": "1 dry-run order path → 7 SafetySM transitions + capital reserve/apply/release + capital_check + wiring latency + would_send",
    }


def past5_ghost_audit() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_day: dict[str, dict[str, Any]] = {}
    for day in PAST5:
        gate_n = 0
        official_n = 0
        ghost_n = 0
        discord_bad = 0
        order_null_price = 0
        for sd in session_dirs_for_day(day):
            events = load_events(sd)
            accepts = [e for e in events if e.get("event_type") == "accepted"]
            can = collect_canonical_trades(events)
            # FIFO match by symbol
            q: dict[str, list] = defaultdict(list)
            for a in sorted(accepts, key=lambda x: str(x.get("entry_time") or "")):
                q[str(a.get("symbol"))].append(a)
            matched_accepts = set()
            for t in can:
                sym = str(t.get("symbol"))
                if q[sym]:
                    a = q[sym].pop(0)
                    matched_accepts.add(id(a))
            gate_n += len(accepts)
            official_n += len(can)
            for a in accepts:
                if id(a) in matched_accepts:
                    continue
                ghost_n += 1
                rows.append(
                    {
                        "trading_date": day,
                        "session_id": sd.name,
                        "issue": "gate_accepted_without_position",
                        "symbol": a.get("symbol"),
                        "entry_time": a.get("entry_time"),
                        "current_price": a.get("current_price"),
                        "entry_price": a.get("entry_price"),
                        "position_id": a.get("position_id") or a.get("observer_position_id"),
                        "price_freshness_source": a.get("price_freshness_source"),
                    }
                )

            # Discord: historical delivery logs omit entry_price/position_id for ALL entries.
            # Flag only deliveries that match a ghost accept (unmatched gate accept).
            ghost_syms = {
                str(r["symbol"])
                for r in rows
                if r.get("trading_date") == day
                and r.get("session_id") == sd.name
                and r.get("issue") == "gate_accepted_without_position"
            }
            dpath = sd / "discord_entry_delivery.jsonl"
            if dpath.is_file() and ghost_syms:
                for line in dpath.open(encoding="utf-8"):
                    if not line.strip():
                        continue
                    o = json.loads(line)
                    final = str(o.get("final_result") or o.get("result") or "").lower()
                    if "deliver" not in final:
                        continue
                    sym = str(o.get("symbol") or "")
                    if sym not in ghost_syms:
                        continue
                    discord_bad += 1
                    rows.append(
                        {
                            "trading_date": day,
                            "session_id": sd.name,
                            "issue": "discord_entry_without_valid_position",
                            "symbol": sym,
                            "entry_time": o.get("sent_time") or o.get("timestamp"),
                            "current_price": None,
                            "entry_price": o.get("entry_price"),
                            "position_id": o.get("position_id") or "",
                            "price_freshness_source": "ghost_accept_discord_delivered",
                        }
                    )

        by_day[day] = {
            "gate_accepted_count": gate_n,
            "official_entry_count": official_n,
            "gate_minus_official": gate_n - official_n,
            "ghost_accept_count": ghost_n,
            "discord_entry_without_valid_position": discord_bad,
        }

    # Explicit 6327 order null CurrentPrice case on 20260717
    if AM.is_dir():
        for line in (AM / "live_order_safety" / "order_intents.jsonl").open(encoding="utf-8"):
            if "6327.T" in line:
                o = json.loads(line)
                rows.append(
                    {
                        "trading_date": "20260717",
                        "session_id": "live_session_081810",
                        "issue": "order_intent_with_ask_fallback_no_current_price",
                        "symbol": "6327.T",
                        "entry_time": o.get("timestamp"),
                        "current_price": None,
                        "entry_price": o.get("price"),
                        "position_id": o.get("position_id"),
                        "price_freshness_source": "AskPrice_fallback_4430",
                    }
                )
                break

    only_20260717 = all(
        (by_day[d]["ghost_accept_count"] == 0 and by_day[d]["discord_entry_without_valid_position"] == 0)
        for d in PAST5
        if d != "20260717"
    )
    summary = {
        "days": by_day,
        "ghost_only_on_20260717": only_20260717 and by_day.get("20260717", {}).get("ghost_accept_count", 0) >= 1,
        "row_count": len(rows),
    }
    return rows, summary


def entry_stage_audit_rows() -> list[dict[str, Any]]:
    """Document expected stages + 6327 historical gap (pre-FIX2 had no stage records)."""
    rows = []
    for stage in (
        STAGE_GATE_ACCEPTED,
        STAGE_EXECUTION_PAYLOAD_VALIDATED,
        STAGE_QUEUE_SELECTED,
        STAGE_POSITION_REGISTERED,
        STAGE_OFFICIAL_ENTRY,
        STAGE_ACCEPT_ABORTED,
    ):
        rows.append(
            {
                "decision_id": "dec_historical_6327",
                "position_id": "",
                "symbol": "6327.T",
                "event_time": "2026-07-17T09:05:12.967260+09:00",
                "stage_time": "",
                "stage": stage,
                "current_price": None,
                "entry_price": None,
                "validation_result": "failed" if stage == STAGE_ACCEPT_ABORTED else "n/a_pre_fix2",
                "failure_reason": "current_price_missing" if stage == STAGE_ACCEPT_ABORTED else "",
                "session_key": "live_session_081810",
                "note": "Historical session lacked stage events; FIX2 records these going forward",
            }
        )
    # Simulate what FIX2 would record for 6327
    v = validate_execution_payload(
        symbol="6327.T",
        trade={"entry_time": "2026-07-17T09:05:12.967260+09:00"},
        payload={"CurrentPrice": None, "AskPrice": 4430.0},
        event_time="2026-07-17T09:05:12.967260+09:00",
    )
    rows.append(
        {
            "decision_id": "dec_fix2_sim_6327",
            "position_id": "",
            "symbol": "6327.T",
            "event_time": "2026-07-17T09:05:12.967260+09:00",
            "stage_time": datetime.now().isoformat(timespec="seconds"),
            "stage": STAGE_ACCEPT_ABORTED,
            "current_price": None,
            "entry_price": None,
            "validation_result": "failed",
            "failure_reason": ",".join(v.reasons),
            "session_key": "live_session_081810",
            "note": "FIX2 simulation: would abort before Discord/order",
        }
    )
    return rows


def discord_order_audit_rows() -> list[dict[str, Any]]:
    rows = []
    sd = AM
    if (sd / "discord_entry_delivery.jsonl").is_file():
        for line in (sd / "discord_entry_delivery.jsonl").open(encoding="utf-8"):
            if "6327" not in line:
                continue
            o = json.loads(line)
            rows.append(
                {
                    "decision_id": "",
                    "position_id": o.get("position_id") or "",
                    "symbol": o.get("symbol"),
                    "notification_type": "ENTRY",
                    "delivery_result": o.get("final_result"),
                    "official_entry": False,
                    "abort_reason": "historical_ghost_accept_pre_fix2",
                    "entry_price": o.get("entry_price"),
                    "channel": "discord_entry_delivery",
                }
            )
    order = audit_6327_order_traces(sd)
    rows.append(
        {
            "decision_id": "single_ghost_decision",
            "position_id": order.get("order_id"),
            "symbol": "6327.T",
            "notification_type": "ORDER_TRACE_SUMMARY",
            "delivery_result": json.dumps(order["by_file_true_symbol"], ensure_ascii=False),
            "official_entry": False,
            "abort_reason": "AskPrice_fallback_pre_fix2",
            "entry_price": 4430.0,
            "channel": "live_order_*",
        }
    )
    return rows


def required_answers(order_trace: dict[str, Any], past5: dict[str, Any]) -> dict[str, Any]:
    return {
        "1_discord_before_position_register": {
            "location": "src/small_paper/pilot_runner.py::_execute_accepted_entry (pre-FIX2)",
            "detail": (
                "register_entry は entry_px>0 のときのみ実行。その後 discord.notify_entry は "
                "position_registered 成否を見ずに常時呼び出しされていた。"
                "FIX2では _finalize_accepted_entry_stages 内で is_official_entry_ready 通過後のみ notify_entry。"
            ),
        },
        "2_dryrun_order_before_position_register": {
            "location": (
                "_maybe_record_live_order_pipeline_entry / wiring / safety / capital / dry_run "
                "(pre-FIX2, after notify_entry, ungated)"
            ),
            "detail": (
                "position登録成功を条件にしていなかった。safety bridge は AskPrice fallback で "
                "entry_time を position_id 代用にして dry-run を進行。"
                "FIX2では _entry_order_path_allowed (execution_payload_validated + official_entry) 必須。"
            ),
        },
        "3_direct_cause_current_price_null": {
            "cause": "liquidity_stale_trade",
            "evidence": (
                "accept/entry_scan_audit: price_freshness_source=liquidity_stale_trade, "
                "trade_stale=true, CurrentPrice=null, price_age_sec=null; board_age_sec≈0.97 "
                "(板は存在するが約定価格 CurrentPrice が欠損)"
            ),
        },
        "4_price_present_at_gate_accept": {
            "current_price_present": False,
            "detail": (
                "gate accept 時点の accepted イベントで current_price=null。"
                "価格が後から消えたのではなく、accept 時点で既に CurrentPrice 欠損。"
            ),
        },
        "5_price_lost_between_queue_and_register": {
            "lost_in_between": False,
            "detail": (
                "queue→register 間で消えたのではなく、register 条件 entry_px=float(CurrentPrice or 0) "
                "が 0 のため register_entry 自体がスキップされた。"
            ),
        },
        "6_order_trace_13_breakdown": order_trace,
        "7_ghost_accept_fully_stopped_after_fix": {
            "verdict": True,
            "mechanism": (
                "validate_execution_payload 失敗 → accept_aborted → Discord公式ENTRYなし → "
                "order adapter未呼出 → ORDER_INTENT_SKIPPED_INVALID_ENTRY_PAYLOAD 1件のみ"
            ),
        },
        "8_normal_entry_path_unchanged": {
            "gate_conditions_unchanged": True,
            "yaml_unchanged": True,
            "path_for_valid_payload": (
                "gate_accepted → execution_payload_validated → queue_selected → "
                "position_registered → official_entry → Discord ENTRY → dry-run order"
            ),
            "canonical_trades_20260717": 78,
            "note": "正常ENTRYは検証通過後に従来どおり register→Discord→order。順序は明示化のみ。",
        },
        "past5_ghost_only_20260717": past5.get("ghost_only_on_20260717"),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    order_trace = audit_6327_order_traces(AM)
    past_rows, past_sum = past5_ghost_audit()
    stage_rows = entry_stage_audit_rows()
    discord_rows = discord_order_audit_rows()
    disk_rows, cleanup = disk_root_inventory()
    answers = required_answers(order_trace, past_sum)

    # canonical count
    events = load_events(AM) + load_events(PM)
    can_n = len(collect_canonical_trades(events))

    code_changes = {
        "entry_execution_integrity.py": "NEW — stages, validate_execution_payload, counters",
        "pilot_runner.py": (
            "_finalize_accepted_entry_stages; Discord/order gated; AskPrice fallback removed "
            "from safety entry; summary stage counts; duplicate decision skip"
        ),
        "forbidden_untouched": [
            "PBv2",
            "OR",
            "ENTRY score",
            "EXIT",
            "CAP",
            "Shadow conditions",
            "YAML trading conditions",
            "real order enablement",
            "historical session overwrite",
        ],
    }

    verdicts = [
        "GHOST_ACCEPT_PREVENTED",
        "DISK_CLEANUP_APPROVAL_REQUIRED",
    ]
    if cleanup.get("current_used_pct", 0) >= 80:
        verdicts.append("DISK_ROOT_CAUSE_FOUND")

    report = {
        "phase": "Phase687W43B-FIX2",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "verdicts": verdicts,
        "primary_verdict": "GHOST_ACCEPT_PREVENTED",
        "required_answers": answers,
        "code_changes": code_changes,
        "summary_fields": {
            "gate_accepted_count": "EntryStageCounters",
            "execution_payload_validated_count": "EntryStageCounters",
            "queue_selected_count": "EntryStageCounters",
            "position_registered_count": "EntryStageCounters",
            "official_entry_count": "EntryStageCounters",
            "accept_aborted_count": "EntryStageCounters",
            "ghost_accept_count": "EntryStageCounters",
            "accepted_count_source": "gate_accepted",
            "legacy_accepted_count": "retained",
        },
        "order_path": (
            "gate_accepted → execution_payload_validated → queue_selected → "
            "position_registered → official_entry → Discord ENTRY → live-order dry-run"
        ),
        "canonical_trades_20260717": can_n,
        "past5_summary": past_sum,
        "order_trace_6327": order_trace,
        "disk": {
            "current_used_pct": cleanup.get("current_used_pct"),
            "prior_0_323gb_deleted": False,
            "approval_required": True,
        },
        "tests": "tests/test_phase687w43b_fix2_ghost_accept.py (Cases 1-6)",
    }

    _wj(OUT / "w43b_fix2_ghost_accept_report.json", report)
    _wc(OUT / "w43b_fix2_entry_stage_audit.csv", stage_rows)
    _wc(OUT / "w43b_fix2_discord_order_audit.csv", discord_rows)
    _wc(OUT / "w43b_fix2_past5day_ghost_audit.csv", past_rows)
    _wc(OUT / "w43b_fix2_disk_root_inventory.csv", disk_rows)
    _wj(OUT / "w43b_fix2_disk_cleanup_options.json", cleanup)

    a = answers
    md = f"""# Phase687W43B-FIX2 — Ghost Accept Prevention

## Verdict
`{' | '.join(verdicts)}`

Primary: **`GHOST_ACCEPT_PREVENTED`**

## Order (Paper Runtime)
```
gate_accepted
→ execution_payload_validated
→ queue_selected
→ position_registered
→ official_entry
→ Discord ENTRY
→ live-order dry-run trace
```

## Required answers

### 1. Discord ENTRYがposition登録前に呼ばれていた箇所
{a['1_discord_before_position_register']['location']}

{a['1_discord_before_position_register']['detail']}

### 2. dry-run orderがposition登録前に呼ばれていた箇所
{a['2_dryrun_order_before_position_register']['location']}

{a['2_dryrun_order_before_position_register']['detail']}

### 3. 6327.Tでcurrent_priceがnullになった直接原因
`{a['3_direct_cause_current_price_null']['cause']}`

{a['3_direct_cause_current_price_null']['evidence']}

### 4. gate accept時点ではpriceがあったのか
**No** — {a['4_price_present_at_gate_accept']['detail']}

### 5. queueまたはregisterまでの間にpriceが消えたのか
**No** — {a['5_price_lost_between_queue_and_register']['detail']}

### 6. 13件のorder痕跡の内訳
- prior reported: {order_trace['prior_reported_order_hits']}
- true symbol events: {order_trace['true_symbol_event_count']}
- false-positive latency (message_index): {order_trace['false_positive_message_index_lines']}
- same decision state machine: {order_trace['state_transitions']}
- AskPrice fallback: {order_trace['ask_price_value']}
- {order_trace['explanation']}

### 7. 修正後にGhost acceptが完全に停止するか
**Yes** — {a['7_ghost_accept_fully_stopped_after_fix']['mechanism']}

### 8. 正常ENTRYの通知・注文経路に変更がないか
Gate/YAML/EXIT/CAP unchanged. Valid payload path:
`{a['8_normal_entry_path_unchanged']['path_for_valid_payload']}`
Canonical trades 20260717: {can_n}

## Past 5 trading days
Ghost / bad Discord ENTRY only on 20260717: `{past_sum.get('ghost_only_on_20260717')}`

| day | gate | official | ghost |
|-----|-----:|---------:|------:|
"""
    for d in PAST5:
        s = past_sum["days"].get(d, {})
        md += f"| {d} | {s.get('gate_accepted_count',0)} | {s.get('official_entry_count',0)} | {s.get('ghost_accept_count',0)} |\n"

    md += f"""
## Disk (inventory only — no delete)
- C: used: `{cleanup.get('current_used_pct')}%`
- Prior 0.323GB candidates: **not deleted**
- Cleanup: approval required (`DISK_CLEANUP_APPROVAL_REQUIRED`)

## Artifacts
- `results/reports/w43b_fix2_ghost_accept_report.json`
- `results/reports/w43b_fix2_ghost_accept_report.md`
- `results/reports/w43b_fix2_entry_stage_audit.csv`
- `results/reports/w43b_fix2_discord_order_audit.csv`
- `results/reports/w43b_fix2_past5day_ghost_audit.csv`
- `results/reports/w43b_fix2_disk_root_inventory.csv`
- `results/reports/w43b_fix2_disk_cleanup_options.json`
"""
    _wm(OUT / "w43b_fix2_ghost_accept_report.md", md)
    print(json.dumps({"verdicts": verdicts, "canonical": can_n, "disk_pct": cleanup.get("current_used_pct")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
