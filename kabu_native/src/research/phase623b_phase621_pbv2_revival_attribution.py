"""
Phase623B: Phase621 PBv2 revival attribution (7/1 AM accepted ticks).
Evidence-only; no runtime changes.
"""

from __future__ import annotations

import bisect
import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_scan_controller import (
    PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE,
    REJECT_DATA_STALE_BOARD,
    REJECT_DATA_STALE_PRICE,
    REJECT_EVENT_STALE_PRICE,
    compute_entry_freshness,
    evaluate_entry_data_freshness,
)
from storage.intraday_recorder import parse_kabu_time

VERDICT = "phase623b_phase621_pbv2_revival_attribution_done"
REPORT_SUBDIR = "phase623b_phase621_pbv2_revival"
JST = ZoneInfo("Asia/Tokyo")

TARGET_DAY = "20260701"
TARGET_SESSION = "live_session_080616"
TARGET_DAY_ISO = "2026-07-01"
MAX_PRICE_AGE_V1 = 3.0
MAX_BOARD_AGE_V1 = 3.0
EVENT_STALE_SEC = 3.0
BOARD_STALE_SEC = 3.0
TRADE_STALE_SEC = 10.0

BASELINE_DAYS = (
    ("20260629", "live_session_080236"),
    ("20260630", "live_session_091118"),
)


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or str(val).strip() == "":
        return None
    return parse_kabu_time(val, fallback=datetime.now(JST))


@dataclass
class PushIndex:
    recorded_at: list[datetime]
    payloads: list[dict[str, Any]]

    @classmethod
    def load_symbol(cls, path: Path) -> "PushIndex":
        recs: list[datetime] = []
        payloads: list[dict[str, Any]] = []
        if not path.is_file():
            return cls([], [])
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            rec_at = _parse_ts(row.get("recorded_at")) or datetime.now(JST)
            payload = dict(row.get("payload") or {})
            if row.get("recorded_at"):
                payload["recorded_at"] = row.get("recorded_at")
            recs.append(rec_at)
            payloads.append(payload)
        return cls(recs, payloads)

    def latest_before(self, at: datetime) -> tuple[Optional[datetime], Optional[dict[str, Any]]]:
        if not self.recorded_at:
            return None, None
        i = bisect.bisect_right(self.recorded_at, at) - 1
        if i < 0:
            return None, None
        return self.recorded_at[i], self.payloads[i]


def _load_audit_by_symbol(session_dir: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    path = session_dir / "entry_scan_audit.jsonl"
    if not path.is_file():
        return out
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("audit_type") != "entry_symbol_eval":
            continue
        sym = str(row.get("symbol") or "")
        out.setdefault(sym, []).append(row)
    for sym in out:
        out[sym].sort(key=lambda r: str(r.get("eval_end_ts") or ""))
    return out


def _build_audit_index(
    audits: dict[str, list[dict[str, Any]]],
) -> dict[str, tuple[list[datetime], list[dict[str, Any]]]]:
    out: dict[str, tuple[list[datetime], list[dict[str, Any]]]] = {}
    for sym, rows in audits.items():
        ts_list: list[datetime] = []
        kept: list[dict[str, Any]] = []
        for row in rows:
            ts = _parse_ts(row.get("eval_end_ts") or row.get("eval_start_ts"))
            if ts is None:
                continue
            ts_list.append(ts)
            kept.append(row)
        out[sym] = (ts_list, kept)
    return out


def _match_audit_indexed(
    audit_index: dict[str, tuple[list[datetime], list[dict[str, Any]]]],
    symbol: str,
    event_time: datetime,
) -> Optional[dict[str, Any]]:
    entry = audit_index.get(symbol)
    if not entry:
        return None
    ts_list, rows = entry
    if not ts_list:
        return None
    i = bisect.bisect_left(ts_list, event_time)
    candidates: list[tuple[float, dict[str, Any]]] = []
    for j in (i - 1, i):
        if 0 <= j < len(rows):
            d = abs((ts_list[j] - event_time).total_seconds())
            if d <= 5.0:
                candidates.append((d, rows[j]))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _load_accepted(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("event_type")) != "accepted":
                continue
            out.append(row)
    return out


def _pbv2_count(session_dir: Path) -> int:
    return sum(
        1
        for row in _load_accepted(session_dir)
        if str(row.get("entry_score_v2_gate_pass", "")).lower() == "true"
    )


def _v1_reject_from_audit(audit: Mapping[str, Any]) -> tuple[Optional[str], str]:
    pa = audit.get("price_age_sec")
    ba = audit.get("board_age_sec")
    if pa is None:
        return REJECT_DATA_STALE_PRICE, "price_age_null"
    if float(pa) > MAX_PRICE_AGE_V1:
        return REJECT_DATA_STALE_PRICE, "price_age_gt_3"
    if ba is None or float(ba) > MAX_BOARD_AGE_V1:
        return REJECT_DATA_STALE_BOARD, "board_age_gt_3"
    return None, "pass"


def _evaluate_counterfactual(
    payload: Mapping[str, Any],
    eval_at: datetime,
) -> dict[str, Any]:
    snap = compute_entry_freshness(payload, pipeline_source="live", reference_now=eval_at)
    v1 = evaluate_entry_data_freshness(
        snap,
        payload,
        max_price_age_sec=MAX_PRICE_AGE_V1,
        max_board_age_sec=MAX_BOARD_AGE_V1,
        board_fallback_enabled=False,
        freshness_semantics_v2_enabled=False,
        reference_now=eval_at,
    )
    v2 = evaluate_entry_data_freshness(
        snap,
        payload,
        max_price_age_sec=MAX_PRICE_AGE_V1,
        max_board_age_sec=MAX_BOARD_AGE_V1,
        board_fallback_enabled=False,
        freshness_semantics_v2_enabled=True,
        event_stale_threshold_sec=EVENT_STALE_SEC,
        board_stale_threshold_sec=BOARD_STALE_SEC,
        trade_stale_threshold_sec=TRADE_STALE_SEC,
        trade_stale_mode="tag_only",
        reference_now=eval_at,
    )
    return {
        "v1_reject_reason": v1.reject_reason or "",
        "v2_reject_reason": v2.reject_reason or "",
        "v2_price_freshness_source": v2.price_freshness_source or "",
        "v1_rescued_from_data_stale_price": v1.reject_reason == REJECT_DATA_STALE_PRICE and v2.reject_reason is None,
    }


def _classify_row(
    accepted: Mapping[str, Any],
    audit: Optional[Mapping[str, Any]],
    counterfactual: Mapping[str, Any],
) -> dict[str, bool]:
    pbv2 = str(accepted.get("entry_score_v2_gate_pass", "")).lower() == "true"
    pa = float(audit.get("price_age_sec") or 0) if audit else None
    pfs = str(audit.get("price_freshness_source") or "") if audit else ""
    v1_reject, _ = _v1_reject_from_audit(audit or {})
    v1_fresh = v1_reject is None

    cat1 = bool(pbv2 and v1_reject == REJECT_DATA_STALE_PRICE)
    cat2 = bool(pbv2 and v1_fresh)
    cat3 = not pbv2
    cat4 = pfs == PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE
    cat5 = not bool(audit.get("event_stale")) and not bool(audit.get("board_stale")) if audit else False

    if cat3:
        primary = "cat3_or_accepted"
    elif cat1 and cat4:
        primary = "cat1_rescued_data_stale_price_via_liquidity_stale_trade_tag"
    elif cat1:
        primary = "cat1_rescued_data_stale_price"
    elif cat2:
        primary = "cat2_v1_fresh_pbv2_accepted"
    else:
        primary = "unclassified"

    return {
        "cat1_rescued_data_stale_price": cat1,
        "cat2_v1_fresh_pbv2_accepted": cat2,
        "cat3_or_accepted": cat3,
        "cat4_liquidity_stale_trade_tag": cat4,
        "cat5_not_event_or_board_stale": cat5,
        "primary_category": primary,
    }


def _attribution_rows(kabu: Path) -> list[dict[str, Any]]:
    session_dir = kabu / "results" / "small_paper" / TARGET_DAY / TARGET_SESSION
    push_dir = kabu / "data" / "push_jsonl" / TARGET_DAY_ISO
    audit_index = _build_audit_index(_load_audit_by_symbol(session_dir))
    push_cache: dict[str, PushIndex] = {}
    rows: list[dict[str, Any]] = []

    for accepted in _load_accepted(session_dir):
        sym = str(accepted.get("symbol") or "")
        event_time = _parse_ts(accepted.get("event_time") or accepted.get("entry_time"))
        if not event_time:
            continue
        audit = _match_audit_indexed(audit_index, sym, event_time)
        eval_at = _parse_ts(audit.get("eval_end_ts") if audit else None) or event_time

        if sym not in push_cache:
            push_cache[sym] = PushIndex.load_symbol(push_dir / f"{sym}.jsonl")
        rec_at, payload = push_cache[sym].latest_before(eval_at)
        payload = payload or {}
        counterfactual = _evaluate_counterfactual(payload, eval_at) if payload else {
            "v1_reject_reason": "",
            "v2_reject_reason": "",
            "v2_price_freshness_source": "",
            "v1_rescued_from_data_stale_price": False,
        }
        flags = _classify_row(accepted, audit, counterfactual)
        v1_audit_reject, v1_audit_detail = _v1_reject_from_audit(audit or {})

        rows.append(
            {
                "symbol": sym,
                "event_time": accepted.get("event_time"),
                "entry_time": accepted.get("entry_time"),
                "pbv2_gate_pass": str(accepted.get("entry_score_v2_gate_pass", "")).lower() == "true",
                "entry_score_v2": accepted.get("entry_expectancy_score_v2"),
                "eval_end_ts": audit.get("eval_end_ts") if audit else "",
                "price_age_sec": audit.get("price_age_sec") if audit else None,
                "board_age_sec": audit.get("board_age_sec") if audit else None,
                "event_age_sec": (
                    round((eval_at - rec_at).total_seconds(), 3) if rec_at and eval_at else None
                ),
                "live_price_freshness_source": audit.get("price_freshness_source") if audit else "",
                "live_event_stale": audit.get("event_stale") if audit else None,
                "live_board_stale": audit.get("board_stale") if audit else None,
                "live_trade_stale": audit.get("trade_stale") if audit else None,
                "live_reject_reason": audit.get("reject_reason") if audit else "",
                "live_entry_decision": audit.get("entry_decision") if audit else "",
                "v1_audit_reject_reason": v1_audit_reject or "",
                "v1_audit_detail": v1_audit_detail,
                "v1_push_reject_reason": counterfactual["v1_reject_reason"],
                "v2_push_reject_reason": counterfactual["v2_reject_reason"],
                "v2_push_price_freshness_source": counterfactual["v2_price_freshness_source"],
                "v1_rescued_push_replay": counterfactual["v1_rescued_from_data_stale_price"],
                "raw_CurrentPriceTime": payload.get("CurrentPriceTime"),
                "push_recorded_at": rec_at.isoformat() if rec_at else "",
                **flags,
            }
        )
    rows.sort(key=lambda r: str(r.get("event_time") or ""))
    return rows


def _score3_fresh_entry_decision_count(kabu: Path, day: str, session: str) -> tuple[int, int]:
    fresh = 0
    entry_true = 0
    path = kabu / "results" / "small_paper" / day / session / "entry_scan_audit.jsonl"
    if not path.is_file():
        return 0, 0
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("audit_type") != "entry_symbol_eval":
            continue
        try:
            score = int(float(row.get("entry_score_v2") or 0))
        except (TypeError, ValueError):
            score = 0
        if score < 3:
            continue
        pa = row.get("price_age_sec")
        if pa is None or float(pa) > MAX_PRICE_AGE_V1:
            continue
        fresh += 1
        if row.get("entry_decision"):
            entry_true += 1
    return fresh, entry_true


def run_phase623b(repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(kabu) / REPORT_SUBDIR
    reports.mkdir(parents=True, exist_ok=True)

    rows = _attribution_rows(kabu)
    session_dir = kabu / "results" / "small_paper" / TARGET_DAY / TARGET_SESSION
    pbv2_701 = sum(1 for r in rows if r.get("pbv2_gate_pass"))
    pbv2_baseline = sum(_pbv2_count(kabu / "results" / "small_paper" / day / sess) for day, sess in BASELINE_DAYS)
    pbv2_increase = pbv2_701 - pbv2_baseline

    pbv2_rows = [r for r in rows if r.get("pbv2_gate_pass")]
    rescued_audit = sum(1 for r in pbv2_rows if r.get("cat1_rescued_data_stale_price"))
    v1_fresh_pbv2 = sum(1 for r in pbv2_rows if r.get("cat2_v1_fresh_pbv2_accepted"))
    or_accepted = sum(1 for r in rows if r.get("cat3_or_accepted"))
    liq_tag = sum(1 for r in rows if r.get("cat4_liquidity_stale_trade_tag"))
    not_event_board = sum(1 for r in rows if r.get("cat5_not_event_or_board_stale"))

    primary_counts = Counter(str(r.get("primary_category") or "") for r in rows)

    fresh_629, entry_629 = _score3_fresh_entry_decision_count(kabu, "20260629", "live_session_080236")
    fresh_701, entry_701 = _score3_fresh_entry_decision_count(kabu, TARGET_DAY, TARGET_SESSION)

    freshness_is_main = rescued_audit >= v1_fresh_pbv2
    explains_629_zero = rescued_audit > 0 and entry_629 <= 1

    mandatory = {
        "pbv2_accepted_701_am": pbv2_701,
        "accepted_total_701_am": len(rows),
        "pbv2_baseline_629_630": pbv2_baseline,
        "pbv2_increase_vs_629_630": pbv2_increase,
        "old_def_would_drop_count_pbv2": rescued_audit,
        "old_def_would_drop_pct_pbv2": round(100.0 * rescued_audit / max(1, pbv2_701), 2),
        "v1_fresh_pbv2_count": v1_fresh_pbv2,
        "or_accepted_count": or_accepted,
        "liquidity_stale_trade_tag_count": liq_tag,
        "not_event_or_board_stale_count": not_event_board,
        "primary_category_counts": dict(primary_counts),
        "main_cause_freshness_change": freshness_is_main,
        "main_cause_verdict": (
            "freshness_change_partial"
            if rescued_audit > 0 and v1_fresh_pbv2 > 0
            else ("freshness_change" if rescued_audit > 0 else "market_regime_or_scoring")
        ),
        "explains_629_630_pbv2_zero_alone": False,
        "explains_629_630_note": (
            f"6/29 score3+fresh={fresh_629} but entry_decision={entry_629}; "
            f"7/1 score3+fresh={fresh_701} entry_decision={entry_701}. "
            "Freshness rescue unlocks candidates but 6/29 zero also required PBv2 scoring failure."
        ),
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "target_day": TARGET_DAY,
        "target_session": TARGET_SESSION,
        "mandatory_answers": mandatory,
        "classification_summary": {
            "cat1_rescued_data_stale_price": rescued_audit,
            "cat2_v1_fresh_pbv2_accepted": v1_fresh_pbv2,
            "cat3_or_accepted": or_accepted,
            "cat4_liquidity_stale_trade_tag": liq_tag,
            "cat5_not_event_or_board_stale": not_event_board,
        },
    }

    csv_path = reports / "phase623b_phase621_revival_attribution.csv"
    _write_csv(csv_path, list(rows[0].keys()) if rows else ["symbol"], rows)
    json_path = reports / "phase623b_report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_paths"] = {
        "attribution_csv": str(csv_path),
        "report_json": str(json_path),
    }
    return report

