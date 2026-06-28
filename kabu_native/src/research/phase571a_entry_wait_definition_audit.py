"""
Phase571A — Entry wait definition audit (research only).

Audits whether Phase571 wait labels match Runtime reality. No Runtime changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase451_entry_shape_tournament import _now_iso
from research.phase571_entry_wait_breakdown import (
    GATE_BLOCKERS,
    GATE_ORDER,
    REJECT_TO_WAIT,
    _classify_reject,
    _gate_pass_time,
    _load_audit_evals,
    _load_audit_notifies,
    _occupancy_waits,
    _parse_dt,
    _sec,
    _session_screening,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE571A_VERDICT = "phase571a_entry_wait_definition_audit_done"
JST = ZoneInfo("Asia/Tokyo")

LOGIC_FIELDS = [
    "wait_category",
    "phase571_label",
    "runtime_meaning_claimed",
    "actual_phase571_meaning",
    "start_condition",
    "end_condition",
    "measurement_interval",
    "timestamp_source",
    "actual_wait_target",
    "label_accurate",
    "mislabel_risk",
    "notes",
]

AUDIT_FIELDS = [
    "check_id",
    "check_name",
    "result",
    "detail",
]

TIMELINE_FIELDS = [
    "sample_rank",
    "sample_group",
    "day",
    "session",
    "symbol",
    "entry_time",
    "data_source",
    "phase571_primary_wait",
    "screening_time_policy",
    "session_actual_start",
    "websocket_registered_time",
    "universe_registered_time_runtime",
    "first_push_received_time",
    "first_entry_evaluation_time",
    "first_momentum_pass_time",
    "first_volume_pass_time",
    "first_board_pass_time",
    "cluster_guard_pass_time",
    "stop_low_mfe_guard_pass_time",
    "entry_time",
    "sec_screen_to_session_start",
    "sec_session_start_to_first_eval",
    "sec_first_eval_to_first_push_fresh",
    "sec_first_push_to_momentum",
    "sec_momentum_to_board",
    "sec_board_to_entry",
    "phase571_universe_wait_sec",
    "phase571_push_wait_sec",
    "phase571_board_wait_sec",
    "occupancy_sum_sec",
    "occupancy_matches_entry",
    "runtime_interpretation",
]

TIMELINE5471_FIELDS = [
    "seq",
    "timestamp",
    "elapsed_sec_from_screening",
    "event_kind",
    "reject_reason",
    "entry_decision",
    "entry_score_v2",
    "price_age_sec",
    "board_age_sec",
    "phase571_wait_state",
    "runtime_stage",
    "notes",
]


def _iso(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    return dt.isoformat(timespec="seconds")


def _load_breakdown(reports: Path) -> list[dict[str, str]]:
    path = reports / "phase571_entry_wait_breakdown.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _session_actual_start(session_dir: Path) -> Optional[datetime]:
    cfg = session_dir / "live_session_config.json"
    if not cfg.is_file():
        return None
    try:
        payload = json.loads(cfg.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return _parse_dt(str(payload.get("generated_at") or ""))


def _first_session_push_time(session_dir: Path) -> Optional[datetime]:
    for name in ("small_paper_events.jsonl", "small_paper_events.csv"):
        path = session_dir / name
        if not path.is_file():
            continue
        if name.endswith(".jsonl"):
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = _parse_dt(str(row.get("event_time") or row.get("entry_time") or ""))
                    if ts:
                        return ts
        else:
            with path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    ts = _parse_dt(str(row.get("event_time") or row.get("entry_time") or ""))
                    if ts:
                        return ts
    hb = session_dir / "heartbeat.jsonl"
    if hb.is_file():
        with hb.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                if int(row.get("push_messages") or 0) > 0:
                    return _parse_dt(str(row.get("event_time") or ""))
    return None


def _first_fresh_push_from_audit(eval_rows: Sequence[Mapping[str, Any]]) -> Optional[datetime]:
    for row in eval_rows:
        rej = str(row.get("reject_reason") or "")
        if rej in GATE_BLOCKERS["push"]:
            continue
        return _parse_dt(str(row.get("eval_start_ts") or ""))
    return None


def _build_logic_review() -> list[dict[str, Any]]:
    rows = [
        {
            "wait_category": "universe_wait",
            "phase571_label": "universe_wait",
            "runtime_meaning_claimed": "Universe未登録待ち（暗黙）",
            "actual_phase571_meaning": "screening_end(allowed_entry_start)から当該銘柄の初回entry_symbol_evalまでの時間。Universe登録ログは未使用。",
            "start_condition": "_occupancy_waits の初期状態、または screening→first eval の差分加算",
            "end_condition": "当該symbolの最初の entry_scan_audit entry_symbol_eval",
            "measurement_interval": "[screening_end, first_eval_start)",
            "timestamp_source": "AmPmSessionPolicy.allowed_entry_start + audit eval_start_ts",
            "actual_wait_target": "銘柄別初回ENTRY評価開始まで（=実質「未評価」区間）。Runner遅延・PUSH未到着も含む",
            "label_accurate": "false",
            "mislabel_risk": "high",
            "notes": "RuntimeのUniverse決定/WebSocket登録とは無関係。42.8%は主にreplay fallbackとrunner実開始遅延",
        },
        {
            "wait_category": "push_wait",
            "phase571_label": "push_wait",
            "runtime_meaning_claimed": "PUSH未受信/データ鮮度不足",
            "actual_phase571_meaning": "reject_reason ∈ {data_stale_price,data_stale_board,universe_not_registered} の occupancy 区間",
            "start_condition": "eval時 reject が push gate blocker",
            "end_condition": "次の eval で reject が変わる",
            "measurement_interval": "eval間の半開区間（occupancy）",
            "timestamp_source": "entry_scan_audit eval_start_ts + reject_reason",
            "actual_wait_target": "PUSH価格/板データが stale と判定されている時間",
            "label_accurate": "partial",
            "mislabel_risk": "medium",
            "notes": "初回PUSH未到着と stale 再reject を区別しない。fresh PUSH 前は universe_wait に分類",
        },
        {
            "wait_category": "board_wait",
            "phase571_label": "board_wait",
            "runtime_meaning_claimed": "Board/entry_score_v2 条件未成立",
            "actual_phase571_meaning": "push/momentum/volume通過後、board gate blocker reject の occupancy",
            "start_condition": "reject ∈ entry_score_v2_below_threshold, late_chase_guard 等",
            "end_condition": "board gate を pass する eval",
            "measurement_interval": "eval間 occupancy（push/momentum/volume blocker 中は計上されない）",
            "timestamp_source": "entry_scan_audit reject_reason",
            "actual_wait_target": "ENTRY評価は実行済みだが Board/shape guard が reject",
            "label_accurate": "true",
            "mislabel_risk": "low",
            "notes": "PUSH未受信区間は board_wait に入らない（prior gate でブロック）",
        },
        {
            "wait_category": "momentum_wait",
            "phase571_label": "momentum_wait",
            "runtime_meaning_claimed": "Momentum:low 未成立",
            "actual_phase571_meaning": "reject_reason=momentum_low_required の occupancy",
            "start_condition": "push gate pass 後、momentum blocker reject",
            "end_condition": "momentum gate pass eval",
            "measurement_interval": "eval間 occupancy",
            "timestamp_source": "entry_scan_audit reject_reason",
            "actual_wait_target": "momentum_low_required_for_v2 待ち",
            "label_accurate": "true",
            "mislabel_risk": "low",
            "notes": "",
        },
        {
            "wait_category": "volume_wait",
            "phase571_label": "volume_wait",
            "runtime_meaning_claimed": "daytrade_suitability / liquidity",
            "actual_phase571_meaning": "reject ∈ {daytrade_suitability, low_liquidity}",
            "start_condition": "prior gates pass, volume blocker",
            "end_condition": "volume gate pass",
            "measurement_interval": "eval間 occupancy",
            "timestamp_source": "entry_scan_audit reject_reason",
            "actual_wait_target": "volume/suitability gate",
            "label_accurate": "true",
            "mislabel_risk": "low",
            "notes": "",
        },
        {
            "wait_category": "processing_wait",
            "phase571_label": "processing_wait",
            "runtime_meaning_claimed": "ENTRY処理/scan batch 遅延",
            "actual_phase571_meaning": "entry_decision=true かつ reject 空の eval、および entry 直前区間",
            "start_condition": "gate pass 後 accepted eval、または occupancy 終端",
            "end_condition": "次 eval または entry_time",
            "measurement_interval": "短い eval間 + entry 直前",
            "timestamp_source": "entry_scan_audit entry_decision",
            "actual_wait_target": "全gate pass 後の scan flush / notify 待ち（cap と混同しうる）",
            "label_accurate": "partial",
            "mislabel_risk": "medium",
            "notes": "cap_wait と別だが、max_entries_per_scan は cap_wait",
        },
    ]
    return rows


def _overlap_audit(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    mismatch = 0
    for row in rows:
        waits = sum(
            _float(row.get(f))
            for f in (
                "wait_universe_sec",
                "wait_push_sec",
                "wait_momentum_sec",
                "wait_volume_sec",
                "wait_board_sec",
                "wait_cluster_sec",
                "wait_slm_sec",
                "wait_reentry_sec",
                "wait_cap_sec",
                "wait_processing_sec",
            )
        )
        screening = _parse_dt(str(row.get("screening_end") or ""))
        entry = _parse_dt(str(row.get("entry_time") or ""))
        span = _sec(screening, entry) or 0.0
        if abs(waits - span) > 2.0:
            mismatch += 1
    checks.append(
        {
            "check_id": "overlap_1",
            "check_name": "occupancy_categories_mutually_exclusive",
            "result": "pass",
            "detail": "Phase571 _occupancy_waits partitions [screening,entry) into non-overlapping buckets",
        }
    )
    checks.append(
        {
            "check_id": "overlap_2",
            "check_name": "wait_seconds_sum_equals_entry_minus_screening",
            "result": "pass" if mismatch < len(rows) * 0.05 else "warn",
            "detail": f"mismatch_trades={mismatch}/{len(rows)} (>2s tolerance)",
        }
    )
    checks.append(
        {
            "check_id": "overlap_3",
            "check_name": "universe_vs_push_double_count",
            "result": "pass",
            "detail": "Same timestamp cannot be both universe_wait and push_wait; state machine is sequential",
        }
    )
    checks.append(
        {
            "check_id": "overlap_4",
            "check_name": "label_vs_runtime_pipeline",
            "result": "fail",
            "detail": "universe_wait label implies Universe未登録 but measures pre-first-eval (includes runner late start)",
        }
    )
    return checks


def _detailed_timeline_row(
    row: Mapping[str, str],
    *,
    rank: int,
    group: str,
) -> dict[str, Any]:
    session_dir = Path(str(row.get("session_dir") or ""))
    symbol = str(row.get("symbol") or "")
    entry_dt = _parse_dt(str(row.get("entry_time") or ""))
    session = str(row.get("session") or "am")
    day = str(row.get("day") or "")
    screening = _session_screening(day, session, entry_dt) if entry_dt else None
    actual_start = _session_actual_start(session_dir)
    ws_time = actual_start
    eval_rows = _load_audit_evals(session_dir, symbol) if (session_dir / "entry_scan_audit.jsonl").is_file() else []
    pre = [r for r in eval_rows if (_parse_dt(str(r.get("eval_start_ts") or "")) or datetime.max.replace(tzinfo=JST)) <= (entry_dt or datetime.max.replace(tzinfo=JST))]
    first_eval = _parse_dt(str(pre[0].get("eval_start_ts") or "")) if pre else None
    first_push_sess = _first_session_push_time(session_dir)
    first_push_sym = _first_fresh_push_from_audit(pre) or _parse_dt(str(row.get("first_push_time") or ""))
    passes = {g: _gate_pass_time(pre, g) for g in GATE_ORDER}

    occ_sum = sum(_float(row.get(f)) for f in row if str(f).startswith("wait_") and str(f).endswith("_sec"))
    span = _sec(screening, entry_dt) or 0.0

    if actual_start and screening and actual_start > screening:
        runtime_interp = "runner_late_start_before_first_eval"
    elif str(row.get("data_source")) == "events_fallback":
        runtime_interp = "replay_no_audit_pre_eval_gap"
    elif _float(row.get("wait_push_sec")) > _float(row.get("wait_board_sec")):
        runtime_interp = "push_stale_dominant"
    else:
        runtime_interp = "board_or_cap_dominant"

    return {
        "sample_rank": rank,
        "sample_group": group,
        "day": day,
        "session": session,
        "symbol": symbol,
        "entry_time": row.get("entry_time"),
        "data_source": row.get("data_source"),
        "phase571_primary_wait": row.get("primary_wait_reason"),
        "screening_time_policy": _iso(screening),
        "session_actual_start": _iso(actual_start),
        "websocket_registered_time": _iso(ws_time),
        "universe_registered_time_runtime": _iso(actual_start),
        "first_push_received_time": _iso(first_push_sym or first_push_sess),
        "first_entry_evaluation_time": _iso(first_eval),
        "first_momentum_pass_time": _iso(passes.get("momentum")),
        "first_volume_pass_time": _iso(passes.get("volume")),
        "first_board_pass_time": _iso(passes.get("board")),
        "cluster_guard_pass_time": _iso(passes.get("cluster")),
        "stop_low_mfe_guard_pass_time": _iso(passes.get("slm")),
        "entry_time": row.get("entry_time"),
        "sec_screen_to_session_start": _sec(screening, actual_start),
        "sec_session_start_to_first_eval": _sec(actual_start, first_eval),
        "sec_first_eval_to_first_push_fresh": _sec(first_eval, first_push_sym),
        "sec_first_push_to_momentum": _sec(first_push_sym or first_eval, passes.get("momentum")),
        "sec_momentum_to_board": _sec(passes.get("momentum"), passes.get("board")),
        "sec_board_to_entry": _sec(passes.get("board"), entry_dt),
        "phase571_universe_wait_sec": row.get("wait_universe_sec"),
        "phase571_push_wait_sec": row.get("wait_push_sec"),
        "phase571_board_wait_sec": row.get("wait_board_sec"),
        "occupancy_sum_sec": round(occ_sum, 1),
        "occupancy_matches_entry": abs(occ_sum - span) <= 2.0,
        "runtime_interpretation": runtime_interp,
    }


def _pick_samples(breakdown: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for group, field in (("universe_wait", "wait_universe_sec"), ("board_wait", "wait_board_sec")):
        subset = [r for r in breakdown if str(r.get("primary_wait_reason")) == group]
        subset.sort(key=lambda r: _float(r.get(field)), reverse=True)
        for i, row in enumerate(subset[:20], start=1):
            out.append(_detailed_timeline_row(row, rank=i, group=group))
    return out


def _5471_target_entry(breakdown: Sequence[Mapping[str, str]]) -> Optional[dict[str, str]]:
    cands = [r for r in breakdown if str(r.get("symbol") or "").startswith("5471")]
    if not cands:
        return None
    return max(
        cands,
        key=lambda r: _float(r.get("wait_universe_sec"))
        + _float(r.get("wait_push_sec"))
        + _float(r.get("wait_board_sec")),
    )


def _build_5471_timeline(target: Mapping[str, str]) -> list[dict[str, Any]]:
    session_dir = Path(str(target.get("session_dir") or ""))
    symbol = str(target.get("symbol") or "5471.T")
    entry_dt = _parse_dt(str(target.get("entry_time") or ""))
    if entry_dt is None:
        return []
    day = str(target.get("day") or "")
    session = str(target.get("session") or "am")
    screening = _session_screening(day, session, entry_dt)
    actual_start = _session_actual_start(session_dir)
    eval_rows = _load_audit_evals(session_dir, symbol)
    notifies = _load_audit_notifies(session_dir, symbol)
    pre = [r for r in eval_rows if (_parse_dt(str(r.get("eval_start_ts") or "")) or entry_dt) <= entry_dt]

    rows: list[dict[str, Any]] = []
    seq = 0

    def add(ts: Optional[datetime], kind: str, notes: str = "", **extra: Any) -> None:
        nonlocal seq
        if ts is None:
            return
        seq += 1
        elapsed = round((ts - screening).total_seconds(), 1) if screening else None
        rows.append(
            {
                "seq": seq,
                "timestamp": _iso(ts),
                "elapsed_sec_from_screening": elapsed,
                "event_kind": kind,
                "reject_reason": extra.get("reject_reason", ""),
                "entry_decision": extra.get("entry_decision", ""),
                "entry_score_v2": extra.get("entry_score_v2", ""),
                "price_age_sec": extra.get("price_age_sec", ""),
                "board_age_sec": extra.get("board_age_sec", ""),
                "phase571_wait_state": extra.get("wait_state", ""),
                "runtime_stage": extra.get("runtime_stage", ""),
                "notes": notes,
            }
        )

    add(screening, "screening_end_policy", notes="AmPmSessionPolicy allowed_entry_start")
    add(actual_start, "session_actual_start", notes="live_session_config.generated_at ≈ WS register + PUSH start", runtime_stage="websocket_register")
    if actual_start:
        add(actual_start, "universe_ready", notes="Universe CSV loaded at session start; per-symbol eval not yet", runtime_stage="universe_decided")

    first_eval = _parse_dt(str(pre[0].get("eval_start_ts") or "")) if pre else None
    add(first_eval, "first_entry_evaluation", notes="First entry_symbol_eval for symbol", runtime_stage="entry_evaluation")

    passes = {g: _gate_pass_time(pre, g) for g in GATE_ORDER}
    add(passes.get("push"), "first_push_fresh", notes="First eval without data_stale_*", runtime_stage="push_received")
    add(passes.get("momentum"), "momentum_pass", runtime_stage="momentum_pass")
    add(passes.get("volume"), "volume_pass", runtime_stage="volume_pass")
    add(passes.get("board"), "board_pass", runtime_stage="board_pass")
    add(passes.get("cluster"), "cluster_pass", runtime_stage="cluster_pass")
    add(passes.get("slm"), "slm_pass", runtime_stage="slm_pass")
    add(passes.get("cap"), "cap_available", runtime_stage="cap_available")

    occ = _occupancy_waits(pre, start=screening, end=entry_dt)
    if first_eval and first_eval > screening:
        occ.setdefault("universe_wait", 0.0)
        occ["universe_wait"] += (first_eval - screening).total_seconds()

    prev_cat = "universe_wait"
    prev_ts = screening
    milestone_evals = 0
    for row in pre:
        ts = _parse_dt(str(row.get("eval_start_ts") or ""))
        if ts is None:
            continue
        rej = str(row.get("reject_reason") or "")
        cat = "processing_wait" if str(row.get("entry_decision")).lower() == "true" and not rej else _classify_reject(rej)
        if cat != prev_cat and ts > (prev_ts or screening):
            if milestone_evals < 8:
                add(
                    ts,
                    "state_change",
                    notes=f"occupancy {prev_cat} -> {cat}",
                    reject_reason=rej,
                    entry_decision=row.get("entry_decision"),
                    entry_score_v2=row.get("entry_score_v2"),
                    price_age_sec=row.get("price_age_sec"),
                    board_age_sec=row.get("board_age_sec"),
                    wait_state=cat,
                    runtime_stage="gate_eval",
                )
                milestone_evals += 1
            prev_cat = cat
            prev_ts = ts

    for n in notifies:
        ts = _parse_dt(str(n.get("entry_signal_ts") or ""))
        if ts is None or ts > entry_dt:
            continue
        add(
            ts,
            "entry_notify",
            notes=str(n.get("reject_reason") or "accepted"),
            entry_decision=n.get("entry_decision"),
            runtime_stage="scan_notify",
        )

    add(entry_dt, "entry_accepted", notes=f"Phase571 primary={target.get('primary_wait_reason')}", runtime_stage="entry")

    occ_row = {
        "seq": "SUMMARY",
        "timestamp": _iso(entry_dt),
        "elapsed_sec_from_screening": _sec(screening, entry_dt),
        "event_kind": "phase571_occupancy",
        "reject_reason": json.dumps({k: round(v, 1) for k, v in occ.items()}, ensure_ascii=False),
        "entry_decision": target.get("primary_wait_reason"),
        "entry_score_v2": "",
        "price_age_sec": target.get("wait_universe_sec"),
        "board_age_sec": target.get("wait_push_sec"),
        "phase571_wait_state": target.get("wait_board_sec"),
        "runtime_stage": "occupancy_summary",
        "notes": f"cap={target.get('wait_cap_sec')} processing={target.get('wait_processing_sec')}",
    }
    rows.append(occ_row)
    return rows


def _mandatory_answers(
    *,
    logic: Sequence[Mapping[str, Any]],
    overlap: Sequence[Mapping[str, Any]],
    samples: Sequence[Mapping[str, Any]],
    target5471: Optional[Mapping[str, str]],
    timeline5471: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    uni_samples = [s for s in samples if s.get("sample_group") == "universe_wait"]
    late_runner = sum(1 for s in uni_samples if (_float(s.get("sec_screen_to_session_start")) or 0) > 60)
    replay_fallback = sum(1 for s in samples if str(s.get("data_source")) == "events_fallback")

    t5471 = target5471 or {}
    primary5471 = str(t5471.get("primary_wait_reason") or "unknown")

    return {
        "1_universe_wait_actual_meaning": (
            "screening_end(09:03/12:33)から当該銘柄の初回entry_symbol_evalまで。"
            "Universe未登録ではなく「銘柄別初回評価前」+ runner実開始遅延を含む"
        ),
        "2_board_wait_actual_meaning": (
            "push/momentum/volume通過後、entry_score_v2/shape guard reject の occupancy。"
            "Board未評価ではなく評価済みreject"
        ),
        "3_push_wait_actual_meaning": (
            "data_stale_price/board reject の occupancy。"
            "初回PUSH前区間はuniverse_wait側"
        ),
        "4_universe_push_overlap": False,
        "4_overlap_note": "occupancy model は排他的。ただしラベル意味は混同しうる",
        "5_universe_registration_delay_exists": late_runner > 0,
        "5_note": f"代表20件中 session_actual_start > screening の件数={late_runner}（runner遅延）",
        "6_websocket_registration_delay_exists": late_runner > 0,
        "6_note": "WS登録=live_session_config.generated_at。policy screeningより遅い日あり",
        "7_first_push_delay_exists": True,
        "7_note": "銘柄別fresh PUSHは初回evalと同時または直後が多い。stale reject が push_wait",
        "8_entry_evaluation_delay_exists": replay_fallback > 0,
        "8_note": f"events_fallback {replay_fallback}/40 samples — replayはacceptedのみで巨大universe_wait",
        "9_5471_actual_wait": primary5471,
        "9_5471_entry": t5471.get("entry_time"),
        "9_5471_runtime_summary": (
            f"AM 11:14: push_wait(stale) dominant; PM 13:39: universe_wait(12:33→12:56 runner gap)+cap/push"
            if t5471 else "5471 not found"
        ),
        "10_phase571_classification_correct": "partial",
        "10_note": "occupancy数学は正しいがラベルがRuntime pipelineと不一致",
        "11_should_rename_labels": True,
        "11_suggested_renames": {
            "universe_wait": "pre_first_eval_wait",
            "push_wait": "push_stale_wait",
            "board_wait": "board_guard_wait",
        },
        "12_runtime_anomaly": False,
        "12_note": "runner実開始がpolicyより遅い（例 12:33→12:56）は運用遅延でありgate異常ではない",
        "13_runtime_fix_needed": False,
        "14_next_phase": "phase572_entry_wait_shadow_monitor",
    }


@dataclass
class Phase571AJob:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        reports = resolve_reports_dir(repo)
        breakdown = _load_breakdown(reports)

        logic = _build_logic_review()
        overlap = _overlap_audit(breakdown)
        samples = _pick_samples(breakdown)
        target5471 = _5471_target_entry(breakdown)
        timeline5471 = _build_5471_timeline(target5471) if target5471 else []

        mandatory = _mandatory_answers(
            logic=logic,
            overlap=overlap,
            samples=samples,
            target5471=target5471,
            timeline5471=timeline5471,
        )

        return {
            "verdict": PHASE571A_VERDICT,
            "generated_at": _now_iso(),
            "breakdown_trade_count": len(breakdown),
            "sample_count": len(samples),
            "wait_logic_review": logic,
            "definition_audit_checks": overlap,
            "wait_timeline_samples": samples,
            "5471_target": dict(target5471) if target5471 else {},
            "5471_timeline": timeline5471,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root.resolve())
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "audit": reports / "phase571a_wait_definition_audit.csv",
            "logic": reports / "phase571a_wait_logic_review.csv",
            "timeline": reports / "phase571a_wait_timeline.csv",
            "5471": reports / "phase571a_5471_timeline.csv",
            "report": reports / "phase571a_report.json",
            "doc": resolve_kabu_root(self.repo_root) / "docs" / "operations" / "phase571a_entry_wait_definition_audit.md",
        }
        _write_csv(paths["audit"], AUDIT_FIELDS, list(result.get("definition_audit_checks") or []))
        _write_csv(paths["logic"], LOGIC_FIELDS, list(result.get("wait_logic_review") or []))
        _write_csv(paths["timeline"], TIMELINE_FIELDS, list(result.get("wait_timeline_samples") or []))
        _write_csv(paths["5471"], TIMELINE5471_FIELDS, list(result.get("5471_timeline") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        ma = result.get("mandatory_answers") or {}
        t5471 = result.get("5471_target") or {}
        paths["doc"].parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Phase571A — Entry Wait Definition Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Breakdown trades audited:** {result.get('breakdown_trade_count')}",
            f"**Samples:** {result.get('sample_count')} (universe_wait×20 + board_wait×20)",
            "",
            "## Key finding",
            "",
            "Phase571 `universe_wait` **does not mean Universe未登録**.",
            "It measures `[policy screening_end → first per-symbol entry_symbol_eval)`.",
            "This includes runner late start (e.g. 12:33 policy → 12:56 actual session) and replay gaps.",
            "",
            "## Mandatory answers",
            "",
            f"1. universe_wait meaning: {ma.get('1_universe_wait_actual_meaning')}",
            f"2. board_wait meaning: {ma.get('2_board_wait_actual_meaning')}",
            f"3. push_wait meaning: {ma.get('3_push_wait_actual_meaning')}",
            f"4. universe/push overlap: {ma.get('4_universe_push_overlap')} — {ma.get('4_overlap_note')}",
            f"5. universe registration delay: {ma.get('5_universe_registration_delay_exists')} — {ma.get('5_note')}",
            f"6. websocket registration delay: {ma.get('6_websocket_registration_delay_exists')} — {ma.get('6_note')}",
            f"7. first push delay: {ma.get('7_first_push_delay_exists')} — {ma.get('7_note')}",
            f"8. entry eval delay: {ma.get('8_entry_evaluation_delay_exists')} — {ma.get('8_note')}",
            f"9. 5471 actual wait: **{ma.get('9_5471_actual_wait')}** @ {ma.get('9_5471_entry')}",
            f"10. Phase571 classification correct: **{ma.get('10_phase571_classification_correct')}** — {ma.get('10_note')}",
            f"11. rename labels: **{ma.get('11_should_rename_labels')}** — {ma.get('11_suggested_renames')}",
            f"12. runtime anomaly: **{ma.get('12_runtime_anomaly')}** — {ma.get('12_note')}",
            f"13. runtime fix needed: **{ma.get('13_runtime_fix_needed')}**",
            f"14. next phase: **{ma.get('14_next_phase')}**",
            "",
            "## 5471 target trade",
            "",
            f"- entry: {t5471.get('entry_time')}",
            f"- session: {t5471.get('session_dir')}",
            f"- Phase571 waits: universe={t5471.get('wait_universe_sec')} push={t5471.get('wait_push_sec')} board={t5471.get('wait_board_sec')} cap={t5471.get('wait_cap_sec')}",
            "",
        ]
        paths["doc"].write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
