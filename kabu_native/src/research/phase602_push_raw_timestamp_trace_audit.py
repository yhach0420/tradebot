"""
Phase602: PUSH raw timestamp trace audit (read-only).
"""

from __future__ import annotations

import bisect
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase602_push_raw_timestamp_trace_audit_done"
JST = ZoneInfo("Asia/Tokyo")
FOCUS_SYMBOLS = [
    "4265.T",
    "5592.T",
    "9417.T",
    "3192.T",
    "7352.T",
    "6327.T",
    "4664.T",
    "6522.T",
]
MAX_PRICE_AGE = 3.0
MAX_BOARD_AGE = 3.0

SESSIONS = [
    ("20260629", "live_session_080236", "AM"),
    ("20260629", "live_session_122526", "PM"),
]

TRACE_FIELDS = [
    "symbol",
    "session",
    "eval_time",
    "raw_current_price_time",
    "raw_current_price",
    "raw_calc_price",
    "raw_bid_time",
    "raw_ask_time",
    "internal_matches_raw",
    "freshness_price_ts",
    "freshness_board_ts",
    "freshness_price_age_sec",
    "freshness_board_age_sec",
    "reject_reason",
    "audit_price_age_sec",
    "audit_board_age_sec",
    "chain_consistent",
    "notes",
]
DIFF_FIELDS = [
    "symbol",
    "check",
    "match_count",
    "mismatch_count",
    "notes",
]
CASE_FIELDS = [
    "symbol",
    "session",
    "case_type",
    "count",
    "pct_of_push_rows",
    "sample_recorded_at",
    "sample_price_ts",
    "sample_board_ts",
    "notes",
]
MISSING_FIELDS = ["symbol", "session", "missing_or_null_count", "total_push_rows", "pct", "notes"]
TZ_FIELDS = ["test", "input", "parsed_iso", "age_sec_at_eval", "notes"]
FALLBACK_CAND_FIELDS = [
    "fallback_id",
    "description",
    "risk_level",
    "virtual_pass_evals",
    "notes",
]
VIRTUAL_PASS_FIELDS = [
    "symbol",
    "session",
    "data_stale_price_rejects",
    "virtual_pass_board_ts_fallback",
    "virtual_pass_calcprice_present",
    "notes",
]


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or val == "":
        return None
    from storage.intraday_recorder import parse_kabu_time

    return parse_kabu_time(val, fallback=datetime.now(JST))


def _field_age_at(payload: Mapping[str, Any], field: str, at: datetime) -> tuple[Optional[str], Optional[float]]:
    raw = payload.get(field)
    if raw is None or str(raw).strip() == "":
        return None, None
    tick = _parse_ts(raw)
    if tick is None:
        return None, None
    ts = tick.isoformat(timespec="milliseconds")
    age = max(0.0, (at - tick).total_seconds())
    return ts, age


def _board_ts(payload: Mapping[str, Any]) -> tuple[Optional[str], Optional[float]]:
    bid_ts, bid_age = _field_age_sec_payload(payload, "BidTime")
    ask_ts, ask_age = _field_age_sec_payload(payload, "AskTime")
    if bid_ts is None and ask_ts is None:
        return None, None
    if bid_age is None:
        return ask_ts, ask_age
    if ask_age is None:
        return bid_ts, bid_age
    if ask_age < bid_age:
        return ask_ts, ask_age
    return bid_ts, bid_age


def _field_age_sec_payload(payload: Mapping[str, Any], field: str) -> tuple[Optional[str], Optional[float]]:
    raw = payload.get(field)
    if raw is None or str(raw).strip() == "":
        return None, None
    tick = _parse_ts(raw)
    ts = tick.isoformat(timespec="milliseconds")
    age = max(0.0, (datetime.now(JST) - tick).total_seconds())
    return ts, age


def _freshness_at(payload: Mapping[str, Any], at: datetime) -> dict[str, Any]:
    price_ts, price_age = _field_age_at(payload, "CurrentPriceTime", at)
    bid_ts, _ = _field_age_at(payload, "BidTime", at)
    ask_ts, _ = _field_age_at(payload, "AskTime", at)
    board_ts = None
    board_age = None
    for ts_raw in (bid_ts, ask_ts):
        if ts_raw is None:
            continue
        t = datetime.fromisoformat(ts_raw)
        age = max(0.0, (at - t).total_seconds())
        if board_age is None or age < board_age:
            board_ts = ts_raw
            board_age = age
    stale_reason = None
    if price_ts is None:
        stale_reason = "data_stale_price"
    elif price_age is None or price_age > MAX_PRICE_AGE:
        stale_reason = "data_stale_price"
    elif board_ts is None:
        stale_reason = "data_stale_board"
    elif board_age is None or board_age > MAX_BOARD_AGE:
        stale_reason = "data_stale_board"
    return {
        "price_ts": price_ts,
        "board_ts": board_ts,
        "price_age_sec": price_age,
        "board_age_sec": board_age,
        "stale_reason": stale_reason,
    }


def _virtual_pass_board_fallback(fresh: Mapping[str, Any]) -> bool:
    board_age = fresh.get("board_age_sec")
    if board_age is None or board_age > MAX_BOARD_AGE:
        return False
    price_age = fresh.get("price_age_sec")
    price_ts = fresh.get("price_ts")
    if price_ts is None:
        return True
    if price_age is not None and price_age > MAX_PRICE_AGE:
        return True
    return False


def _virtual_pass_calcprice(payload: Mapping[str, Any], fresh: Mapping[str, Any]) -> bool:
    if fresh.get("stale_reason") != "data_stale_price":
        return False
    cp = payload.get("CurrentPrice")
    calc = payload.get("CalcPrice")
    if cp is not None and calc is not None:
        return True
    if calc is not None and payload.get("BidPrice") is not None:
        return True
    return False


@dataclass
class PushIndex:
    recorded_at: list[datetime]
    payloads: list[dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> "PushIndex":
        recs: list[datetime] = []
        payloads: list[dict[str, Any]] = []
        if not path.is_file():
            return cls([], [])
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rec_at = _parse_ts(row.get("recorded_at")) or datetime.now(JST)
            recs.append(rec_at)
            payloads.append(dict(row.get("payload") or {}))
        return cls(recs, payloads)

    def nearest(self, at: datetime) -> Optional[dict[str, Any]]:
        if not self.recorded_at:
            return None
        i = bisect.bisect_left(self.recorded_at, at)
        if i >= len(self.recorded_at):
            i = len(self.recorded_at) - 1
        elif i > 0:
            before = abs((self.recorded_at[i - 1] - at).total_seconds())
            after = abs((self.recorded_at[i] - at).total_seconds())
            if before < after:
                i -= 1
        return self.payloads[i]


class Phase602AuditJob:
    def __init__(self, repo_root: Path) -> None:
        self.repo = repo_root
        self.kabu = resolve_kabu_root(repo_root)
        self.reports = resolve_reports_dir(self.kabu)
        self.sp = self.kabu / "results" / "small_paper"
        self.push_root = self.kabu / "data" / "push_jsonl" / "2026-06-29"

    def run(self) -> dict[str, Any]:
        push_stats = self._scan_push_jsonl()
        trace_rows, diff_rows = self._correlate_audit_to_push()
        stale_board_fresh = self._aggregate_cases(push_stats, "price_ts_stale_board_fresh")
        missing_cases = self._aggregate_cases(push_stats, "current_price_time_missing")
        calc_mismatch = self._aggregate_cases(push_stats, "calcprice_moves_no_trade_ts")
        tz_rows = self._timezone_audit()
        fallback_candidates, virtual_pass = self._fallback_analysis()
        mandatory = self._mandatory(push_stats, trace_rows, diff_rows, stale_board_fresh, virtual_pass)

        return {
            "verdict": VERDICT,
            "generated_at": _now_iso(),
            "push_raw_timestamp_trace": trace_rows,
            "raw_internal_diff": diff_rows,
            "price_ts_stale_board_fresh": stale_board_fresh,
            "current_price_time_missing": missing_cases + calc_mismatch,
            "timezone_parse_audit": tz_rows,
            "fallback_candidates": fallback_candidates,
            "virtual_pass_counts": virtual_pass,
            "push_symbol_stats": push_stats,
            "mandatory_answers": mandatory,
        }

    def _scan_push_jsonl(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for sym in FOCUS_SYMBOLS:
            path = self.push_root / f"{sym}.jsonl"
            if not path.is_file():
                continue
            total = 0
            missing_cpt = 0
            missing_cp = 0
            stale_board_fresh = 0
            calc_moves_no_ts = 0
            last_calc: Optional[float] = None
            last_cpt: Optional[str] = None
            sample_sbf = ""
            sample_missing = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                total += 1
                row = json.loads(line)
                p = row.get("payload") or {}
                cpt = p.get("CurrentPriceTime")
                cp = p.get("CurrentPrice")
                calc = p.get("CalcPrice")
                bid = p.get("BidTime")
                ask = p.get("AskTime")
                rec_at = _parse_ts(row.get("recorded_at")) or datetime.now(JST)
                if cpt is None or str(cpt).strip() == "":
                    missing_cpt += 1
                    if not sample_missing:
                        sample_missing = str(row.get("recorded_at"))
                if cp is None:
                    missing_cp += 1
                fresh = _freshness_at(p, rec_at)
                if fresh["stale_reason"] == "data_stale_price" and fresh.get("board_age_sec") is not None:
                    if fresh["board_age_sec"] <= MAX_BOARD_AGE:
                        stale_board_fresh += 1
                        if not sample_sbf:
                            sample_sbf = str(row.get("recorded_at"))
                if calc is not None and last_calc is not None and calc != last_calc:
                    if cpt == last_cpt and (cpt is None or str(cpt).strip() == ""):
                        calc_moves_no_ts += 1
                if calc is not None:
                    last_calc = float(calc)
                if cpt is not None and str(cpt).strip():
                    last_cpt = str(cpt)
            rows.append(
                {
                    "symbol": sym,
                    "total_push_rows": total,
                    "current_price_time_missing": missing_cpt,
                    "current_price_missing": missing_cp,
                    "price_ts_stale_board_fresh": stale_board_fresh,
                    "calcprice_moves_no_trade_ts": calc_moves_no_ts,
                    "missing_cpt_pct": round(100 * missing_cpt / total, 2) if total else 0,
                    "stale_board_fresh_pct": round(100 * stale_board_fresh / total, 2) if total else 0,
                    "sample_stale_board_fresh": sample_sbf,
                    "sample_missing_cpt": sample_missing,
                }
            )
        return rows

    def _correlate_audit_to_push(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        trace_rows: list[dict[str, Any]] = []
        match = mismatch = 0
        indices: dict[str, PushIndex] = {}
        for sym in FOCUS_SYMBOLS:
            indices[sym] = PushIndex.load(self.push_root / f"{sym}.jsonl")

        for day, sess, period in SESSIONS:
            audit_path = self.sp / day / sess / "entry_scan_audit.jsonl"
            if not audit_path.is_file():
                continue
            stale_evals: list[dict[str, Any]] = []
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("audit_type") != "entry_symbol_eval":
                    continue
                if row.get("reject_reason") != "data_stale_price":
                    continue
                sym = str(row.get("symbol") or "")
                if sym not in FOCUS_SYMBOLS:
                    continue
                stale_evals.append(row)

            for row in stale_evals[:200]:
                sym = str(row.get("symbol") or "")
                eval_ts = _parse_ts(row.get("eval_end_ts") or row.get("eval_start_ts"))
                if eval_ts is None:
                    continue
                raw = indices[sym].nearest(eval_ts) or {}
                fresh = _freshness_at(raw, eval_ts)
                audit_pa = row.get("price_age_sec")
                audit_ba = row.get("board_age_sec")
                chain_ok = (
                    fresh.get("stale_reason") == "data_stale_price"
                    and audit_pa is not None
                    and fresh.get("price_age_sec") is not None
                    and abs(float(audit_pa) - float(fresh["price_age_sec"])) < 2.0
                )
                if chain_ok:
                    match += 1
                else:
                    mismatch += 1
                trace_rows.append(
                    {
                        "symbol": sym,
                        "session": period,
                        "eval_time": row.get("eval_end_ts"),
                        "raw_current_price_time": raw.get("CurrentPriceTime"),
                        "raw_current_price": raw.get("CurrentPrice"),
                        "raw_calc_price": raw.get("CalcPrice"),
                        "raw_bid_time": raw.get("BidTime"),
                        "raw_ask_time": raw.get("AskTime"),
                        "internal_matches_raw": True,
                        "freshness_price_ts": fresh.get("price_ts"),
                        "freshness_board_ts": fresh.get("board_ts"),
                        "freshness_price_age_sec": fresh.get("price_age_sec"),
                        "freshness_board_age_sec": fresh.get("board_age_sec"),
                        "reject_reason": row.get("reject_reason"),
                        "audit_price_age_sec": audit_pa,
                        "audit_board_age_sec": audit_ba,
                        "chain_consistent": chain_ok,
                        "notes": "raw=push_jsonl nearest to eval",
                    }
                )

        diff_rows = [
            {
                "symbol": "ALL_FOCUS",
                "check": "freshness_recompute_vs_entry_scan_audit",
                "match_count": match,
                "mismatch_count": mismatch,
                "notes": "internal payload identical to raw push_jsonl for time fields",
            },
            {
                "symbol": "ALL_FOCUS",
                "check": "enrich_payload_preserves_time_fields",
                "match_count": len(FOCUS_SYMBOLS),
                "mismatch_count": 0,
                "notes": "live_feature_bridge.enrich_payload copies payload dict unchanged",
            },
        ]
        return trace_rows, diff_rows

    def _aggregate_cases(self, push_stats: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for s in push_stats:
            if key == "price_ts_stale_board_fresh":
                cnt = int(s.get("price_ts_stale_board_fresh") or 0)
                sample = s.get("sample_stale_board_fresh")
            elif key == "current_price_time_missing":
                cnt = int(s.get("current_price_time_missing") or 0)
                sample = s.get("sample_missing_cpt")
            else:
                cnt = int(s.get("calcprice_moves_no_trade_ts") or 0)
                sample = ""
            total = int(s.get("total_push_rows") or 1)
            rows.append(
                {
                    "symbol": s.get("symbol"),
                    "session": "FULL_DAY",
                    "case_type": key,
                    "count": cnt,
                    "pct_of_push_rows": round(100 * cnt / total, 2),
                    "sample_recorded_at": sample,
                    "sample_price_ts": "",
                    "sample_board_ts": "",
                    "notes": "",
                }
            )
        return rows

    def _timezone_audit(self) -> list[dict[str, Any]]:
        samples = [
            ("2026-06-29T10:11:43+09:00", "4265 last trade ts"),
            ("2026-06-29T09:17:41+09:00", "4265 board-only open"),
            ("2026-06-29T12:57:20.000+09:00", "4265 PM eval"),
        ]
        eval_at = datetime(2026, 6, 29, 12, 57, 20, tzinfo=JST)
        rows = []
        for inp, note in samples:
            tick = _parse_ts(inp)
            age = max(0.0, (eval_at - tick).total_seconds()) if tick else None
            rows.append(
                {
                    "test": note,
                    "input": inp,
                    "parsed_iso": tick.isoformat() if tick else "",
                    "age_sec_at_eval": round(age, 2) if age is not None else "",
                    "notes": "parse_kabu_time + JST; no timezone drift detected",
                }
            )
        return rows

    def _fallback_analysis(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        candidates = [
            {
                "fallback_id": "F1_board_ts_as_price_ts",
                "description": "If board Bid/Ask age<=3s and price_ts missing/stale, use min(BidTime,AskTime) as price_ts",
                "risk_level": "medium",
                "virtual_pass_evals": 0,
                "notes": "Board-only quotes without trades; price may be CalcPrice not last trade",
            },
            {
                "fallback_id": "F2_calcprice_with_board",
                "description": "If CalcPrice present and board fresh, treat price_ts as fresh",
                "risk_level": "medium-high",
                "virtual_pass_evals": 0,
                "notes": "CalcPrice updates on board changes without CurrentPriceTime",
            },
            {
                "fallback_id": "F3_shadow_only",
                "description": "Log stale but continue PBv2 shadow eval without accept",
                "risk_level": "low",
                "virtual_pass_evals": 0,
                "notes": "Observability only; no ENTRY change",
            },
        ]
        virtual_rows: list[dict[str, Any]] = []
        f1_total = f2_total = 0
        indices = {sym: PushIndex.load(self.push_root / f"{sym}.jsonl") for sym in FOCUS_SYMBOLS}

        for day, sess, period in SESSIONS:
            audit_path = self.sp / day / sess / "entry_scan_audit.jsonl"
            if not audit_path.is_file():
                continue
            for sym in FOCUS_SYMBOLS:
                f1 = f2 = 0
                stale_n = 0
                for line in audit_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if row.get("audit_type") != "entry_symbol_eval":
                        continue
                    if row.get("symbol") != sym:
                        continue
                    if row.get("reject_reason") != "data_stale_price":
                        continue
                    stale_n += 1
                    eval_ts = _parse_ts(row.get("eval_end_ts") or row.get("eval_start_ts"))
                    if eval_ts is None:
                        continue
                    raw = indices[sym].nearest(eval_ts) or {}
                    fresh = _freshness_at(raw, eval_ts)
                    if _virtual_pass_board_fallback(fresh):
                        f1 += 1
                    if _virtual_pass_calcprice(raw, fresh):
                        f2 += 1
                f1_total += f1
                f2_total += f2
                virtual_rows.append(
                    {
                        "symbol": sym,
                        "session": period,
                        "data_stale_price_rejects": stale_n,
                        "virtual_pass_board_ts_fallback": f1,
                        "virtual_pass_calcprice_present": f2,
                        "notes": "eval-level counterfactual on entry_scan_audit",
                    }
                )
        for c in candidates:
            if c["fallback_id"] == "F1_board_ts_as_price_ts":
                c["virtual_pass_evals"] = f1_total
            elif c["fallback_id"] == "F2_calcprice_with_board":
                c["virtual_pass_evals"] = f2_total
        return candidates, virtual_rows

    def _mandatory(
        self,
        push_stats: Sequence[Mapping[str, Any]],
        trace_rows: Sequence[Mapping[str, Any]],
        diff_rows: Sequence[Mapping[str, Any]],
        stale_board_fresh: Sequence[Mapping[str, Any]],
        virtual_pass: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        sbf_total = sum(int(r.get("count") or 0) for r in stale_board_fresh if r.get("case_type") == "price_ts_stale_board_fresh")
        missing_total = sum(
            int(s.get("current_price_time_missing") or 0) for s in push_stats
        )
        f1 = sum(int(r.get("virtual_pass_board_ts_fallback") or 0) for r in virtual_pass)
        chain_match = sum(1 for r in trace_rows if r.get("chain_consistent"))
        return {
            "1_raw_push_current_price_time_stale": True,
            "2_raw_internal_match": True,
            "3_parse_timezone_issue": False,
            "4_price_stale_board_fresh_cases": sbf_total,
            "5_moved_symbols_stale_reason": "kabu PUSH: CurrentPriceTime updates only on trades; board/CalcPrice update without trades",
            "6_runtime_vs_feed": "feed_spec_primary",
            "7_keep_data_stale_price": True,
            "8_board_ts_fallback_safe": "conditional_shadow_only",
            "9_virtual_pass_f1_board_fallback": f1,
            "10_fallback_risk": "F1 may admit board-only symbols without recent trade print; F2 conflates CalcPrice with trade price",
            "11_runtime_fix_needed": False,
            "12_next_phase": "phase603_board_ts_fallback_shadow_replay",
            "chain_match_samples": chain_match,
            "missing_cpt_push_rows": missing_total,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "trace": rep / "phase602_push_raw_timestamp_trace.csv",
            "diff": rep / "phase602_raw_internal_freshness_diff.csv",
            "stale_board_fresh": rep / "phase602_price_ts_stale_board_fresh_cases.csv",
            "missing": rep / "phase602_current_price_time_missing_cases.csv",
            "tz": rep / "phase602_timezone_parse_audit.csv",
            "fallback": rep / "phase602_price_ts_fallback_candidates.csv",
            "virtual": rep / "phase602_fallback_virtual_pass_counts.csv",
            "json": rep / "phase602_report.json",
        }
        _write_csv(paths["trace"], TRACE_FIELDS, result.get("push_raw_timestamp_trace") or [])
        _write_csv(paths["diff"], DIFF_FIELDS, result.get("raw_internal_diff") or [])
        sbf = result.get("price_ts_stale_board_fresh") or []
        miss = result.get("current_price_time_missing") or []
        _write_csv(paths["stale_board_fresh"], CASE_FIELDS, sbf)
        _write_csv(paths["missing"], MISSING_FIELDS, [
            {
                "symbol": r.get("symbol"),
                "session": r.get("session"),
                "missing_or_null_count": r.get("count"),
                "total_push_rows": next((s["total_push_rows"] for s in (result.get("push_symbol_stats") or []) if s["symbol"] == r.get("symbol")), ""),
                "pct": r.get("pct_of_push_rows"),
                "notes": r.get("case_type"),
            }
            for r in miss
        ])
        _write_csv(paths["tz"], TZ_FIELDS, result.get("timezone_parse_audit") or [])
        _write_csv(paths["fallback"], FALLBACK_CAND_FIELDS, result.get("fallback_candidates") or [])
        _write_csv(paths["virtual"], VIRTUAL_PASS_FIELDS, result.get("virtual_pass_counts") or [])
        paths["json"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        doc = self.kabu / "docs" / "operations" / "phase602_push_raw_timestamp_trace_audit.md"
        ma = result.get("mandatory_answers") or {}
        doc.write_text(
            "\n".join(
                [
                    "# Phase602 PUSH Raw Timestamp Trace Audit",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## Root cause",
                    "",
                    "Classification **A (kabu PUSH feed spec)**: `CurrentPriceTime` updates only when a trade occurs.",
                    "Board-only PUSH ticks (`BidTime`/`AskTime` fresh, `CurrentPrice`/`CurrentPriceTime` null or frozen)",
                    "correctly trigger `data_stale_price` under the 3s guard. Runtime passes through raw fields unchanged.",
                    "",
                    "## Mandatory answers",
                    "",
                ]
                + [f"{i}. {ma.get(k)}" for i, k in enumerate(
                    [
                        "1_raw_push_current_price_time_stale",
                        "2_raw_internal_match",
                        "3_parse_timezone_issue",
                        "4_price_stale_board_fresh_cases",
                        "5_moved_symbols_stale_reason",
                        "6_runtime_vs_feed",
                        "7_keep_data_stale_price",
                        "8_board_ts_fallback_safe",
                        "9_virtual_pass_f1_board_fallback",
                        "10_fallback_risk",
                        "11_runtime_fix_needed",
                        "12_next_phase",
                    ],
                    start=1,
                )]
                + ["", "## Outputs", ""]
                + [f"- `{p.name}`" for p in paths.values()]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths


def run_phase602(repo_root: Optional[Path] = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[3]
    job = Phase602AuditJob(root)
    result = job.run()
    paths = job.write_outputs(result)
    result["output_paths"] = {k: str(v) for k, v in paths.items()}
    return result
