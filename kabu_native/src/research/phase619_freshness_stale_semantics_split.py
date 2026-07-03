"""
Phase619 — Split freshness into event / board / trade stale (research-only, shadow).
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

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase605_entry_cluster_guard_counterfactual import _session_dir
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.entry_scan_controller import (
    compute_entry_freshness,
    evaluate_entry_data_freshness,
)
from storage.intraday_recorder import parse_kabu_time

VERDICT = "phase619_stale_semantics_split_done"
JST = ZoneInfo("Asia/Tokyo")
THRESHOLD_SEC = 3.0
PROD_YAML = "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"

SESSIONS = (
    ("20260625", "live_session_080340", "AM", "GOOD"),
    ("20260625", "live_session_122535", "PM", "GOOD"),
    ("20260629", "live_session_080236", "AM", "BAD"),
    ("20260629", "live_session_122526", "PM", "BAD"),
    ("20260630", "live_session_091118", "AM", "BAD"),
)

PROPOSED_EVENT_REJECT = "event_stale_price"
PROPOSED_TRADE_TAG = "liquidity_stale_trade"
PROPOSED_BOARD_REJECT = "data_stale_board"


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or str(val).strip() == "":
        return None
    return parse_kabu_time(val, fallback=datetime.now(JST))


def _day_push_dir(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}"


def _float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


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

    def latest_before(self, at: datetime) -> tuple[Optional[datetime], Optional[dict[str, Any]]]:
        if not self.recorded_at:
            return None, None
        i = bisect.bisect_right(self.recorded_at, at) - 1
        if i < 0:
            return None, None
        return self.recorded_at[i], self.payloads[i]


def _field_age(payload: Mapping[str, Any], field: str, at: datetime) -> Optional[float]:
    raw = payload.get(field)
    if raw is None or str(raw).strip() == "":
        return None
    tick = _parse_ts(raw)
    if tick is None:
        return None
    return max(0.0, (at - tick).total_seconds())


def _board_age(payload: Mapping[str, Any], at: datetime) -> Optional[float]:
    ages = [_field_age(payload, f, at) for f in ("BidTime", "AskTime")]
    ages = [a for a in ages if a is not None]
    return min(ages) if ages else None


def _stale_flags(
    *,
    event_age: Optional[float],
    board_age: Optional[float],
    trade_age: Optional[float],
    threshold: float = THRESHOLD_SEC,
) -> dict[str, bool]:
    return {
        "event_stale": event_age is None or event_age > threshold,
        "board_stale": board_age is None or board_age > threshold,
        "trade_stale": trade_age is None or trade_age > threshold,
    }


def _proposed_reject(flags: Mapping[str, bool]) -> Optional[str]:
    if flags["event_stale"]:
        return PROPOSED_EVENT_REJECT
    if flags["board_stale"]:
        return PROPOSED_BOARD_REJECT
    return None


def _stale_combo_label(flags: Mapping[str, bool]) -> str:
    parts = []
    if flags["event_stale"]:
        parts.append("event")
    if flags["board_stale"]:
        parts.append("board")
    if flags["trade_stale"]:
        parts.append("trade")
    return "+".join(parts) if parts else "none"


def _load_session_pnl(sp_dir: Path) -> tuple[list[float], int]:
    pnls: list[float] = []
    n = 0
    p = sp_dir / "small_paper_events.jsonl"
    if not p.is_file():
        return pnls, n
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if str(ev.get("event_type") or "") != "accepted":
            continue
        n += 1
        px = _float(ev.get("pnl_pct"))
        if px is not None:
            pnls.append(px)
    return pnls, n


class Phase619Audit:
    def __init__(self, repo_root: Path) -> None:
        self.kabu = resolve_kabu_root(repo_root)
        self.reports = resolve_reports_dir(self.kabu)
        self.sp = self.kabu / "results" / "small_paper"
        self.push_root = self.kabu / "data" / "push_jsonl"
        cfg_path = self.kabu / PROD_YAML
        self.config = load_pilot_config(cfg_path)
        self._push_cache: dict[tuple[str, str], PushIndex] = {}

    def _push(self, day: str, symbol: str) -> PushIndex:
        key = (day, symbol)
        if key not in self._push_cache:
            path = self.push_root / _day_push_dir(day) / f"{symbol}.jsonl"
            self._push_cache[key] = PushIndex.load(path)
        return self._push_cache[key]

    def _analyze_session(
        self, day: str, session: str, period: str, cohort: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        sdir = _session_dir(self.kabu, day, session)
        audit_path = sdir / "entry_scan_audit.jsonl"
        rows_out: list[dict[str, Any]] = []
        if not audit_path.is_file():
            return rows_out, {"day": day, "session": session, "error": "no audit"}

        fc = {
            "max_price_age_sec": float(getattr(self.config, "entry_max_price_age_sec", 3.0) or 3.0),
            "max_board_age_sec": float(getattr(self.config, "entry_max_board_age_sec", 3.0) or 3.0),
            "board_fallback_enabled": bool(
                getattr(self.config, "entry_freshness_board_fallback_enabled", False)
            ),
            "max_fallback_spread_bps": float(
                getattr(self.config, "entry_freshness_board_fallback_max_spread_bps", 50.0) or 50.0
            ),
        }

        counters: Counter = Counter()
        score3_trade_only = 0
        score3_event_ok_trade_stale = 0
        rescued_pbv2 = 0
        p603_rescue = 0
        pbv2_pass_stale_combo: Counter = Counter()

        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("audit_type") != "entry_symbol_eval":
                continue
            sym = str(row.get("symbol") or "")
            eval_ts = _parse_ts(row.get("eval_end_ts") or row.get("eval_start_ts"))
            if eval_ts is None:
                continue
            push_rec, payload = self._push(day, sym).latest_before(eval_ts)
            payload = payload or {}
            event_age = (
                max(0.0, (eval_ts - push_rec).total_seconds()) if push_rec is not None else None
            )
            board_age = _board_age(payload, eval_ts)
            trade_age = _field_age(payload, "CurrentPriceTime", eval_ts)
            flags = _stale_flags(
                event_age=event_age, board_age=board_age, trade_age=trade_age, threshold=THRESHOLD_SEC
            )
            combo = _stale_combo_label(flags)
            counters[combo] += 1
            if flags["event_stale"]:
                counters["flag_event_stale"] += 1
            if flags["board_stale"]:
                counters["flag_board_stale"] += 1
            if flags["trade_stale"]:
                counters["flag_trade_stale"] += 1

            live_reject = str(row.get("reject_reason") or "")
            score_v2 = str(row.get("entry_score_v2") or row.get("entry_expectancy_score_v2") or "")
            entry_decision = bool(row.get("entry_decision"))
            proposed_reject = _proposed_reject(flags)
            proposed_pass = proposed_reject is None
            current_data_stale = live_reject == "data_stale_price"
            only_trade_stale = (
                flags["trade_stale"] and not flags["event_stale"] and not flags["board_stale"]
            )
            trade_only_rescue = current_data_stale and only_trade_stale

            snap = compute_entry_freshness(payload, pipeline_source="live", reference_now=eval_ts)
            p603_dec = evaluate_entry_data_freshness(
                snap,
                payload,
                max_price_age_sec=fc["max_price_age_sec"],
                max_board_age_sec=fc["max_board_age_sec"],
                guard_enabled=True,
                board_fallback_enabled=True,
                max_fallback_spread_bps=fc["max_fallback_spread_bps"],
            )
            p603_would_pass = p603_dec.reject_reason is None
            if current_data_stale and p603_would_pass:
                p603_rescue += 1

            if trade_only_rescue and proposed_pass:
                rescued_pbv2 += 1
            if score_v2 == "3":
                if only_trade_stale and current_data_stale:
                    score3_trade_only += 1
                if not flags["event_stale"] and flags["trade_stale"] and current_data_stale:
                    score3_event_ok_trade_stale += 1

            if entry_decision:
                pbv2_pass_stale_combo[combo] += 1

            rows_out.append(
                {
                    "day": day,
                    "session": session,
                    "period": period,
                    "cohort": cohort,
                    "symbol": sym,
                    "eval_ts": eval_ts.isoformat(timespec="seconds"),
                    "event_age_sec": round(event_age, 3) if event_age is not None else "",
                    "board_age_sec": round(board_age, 3) if board_age is not None else "",
                    "trade_age_sec": round(trade_age, 3) if trade_age is not None else "",
                    "event_stale": flags["event_stale"],
                    "board_stale": flags["board_stale"],
                    "trade_stale": flags["trade_stale"],
                    "stale_combo": combo,
                    "live_reject_reason": live_reject,
                    "entry_decision": entry_decision,
                    "entry_score_v2": score_v2,
                    "proposed_reject": proposed_reject or "",
                    "proposed_liquidity_tag": PROPOSED_TRADE_TAG if flags["trade_stale"] and proposed_pass else "",
                    "trade_only_rescue_pbv2": trade_only_rescue,
                    "p603_fallback_would_pass": current_data_stale and p603_would_pass,
                }
            )

        pnls, accept_n = _load_session_pnl(sdir)
        pf_val = _pf(pnls)
        summary = {
            "day": day,
            "session": session,
            "period": period,
            "cohort": cohort,
            "eval_count": len(rows_out),
            "event_stale_count": counters["flag_event_stale"],
            "board_stale_count": counters["flag_board_stale"],
            "trade_stale_count": counters["flag_trade_stale"],
            "live_data_stale_price": sum(1 for r in rows_out if r["live_reject_reason"] == "data_stale_price"),
            "live_data_stale_board": sum(1 for r in rows_out if r["live_reject_reason"] == "data_stale_board"),
            "trade_only_rescue_pbv2": rescued_pbv2,
            "score3_trade_only_data_stale": score3_trade_only,
            "score3_event_ok_trade_stale_blocked": score3_event_ok_trade_stale,
            "p603_rescue_from_data_stale": p603_rescue,
            "pbv2_accept_audit_count": sum(1 for r in rows_out if r["entry_decision"]),
            "pbv2_pass_stale_combo": dict(pbv2_pass_stale_combo),
            "baseline_accepted_events": accept_n,
            "baseline_pnl_pct_sum": round(sum(pnls), 4) if pnls else 0,
            "baseline_pf": round(float(pf_val), 4) if pf_val is not None else "",
            "virtual_extra_pbv2_reach": rescued_pbv2,
        }
        return rows_out, summary


def run_phase619(repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    job = Phase619Audit(repo)
    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for day, session, period, cohort in SESSIONS:
        rows, sm = job._analyze_session(day, session, period, cohort)
        all_rows.extend(rows)
        summaries.append(sm)

    breakdown_rows: list[dict[str, Any]] = []
    for sm in summaries:
        combo_ctr = Counter()
        for r in all_rows:
            if r["day"] == sm["day"] and r["session"] == sm["session"]:
                combo_ctr[r["stale_combo"]] += 1
        for combo, cnt in combo_ctr.most_common():
            breakdown_rows.append(
                {
                    "day": sm["day"],
                    "session": sm["session"],
                    "cohort": sm["cohort"],
                    "stale_combo": combo,
                    "count": cnt,
                    "pct": round(100.0 * cnt / sm["eval_count"], 2) if sm["eval_count"] else 0,
                }
            )

    good625 = [s for s in summaries if s["day"] == "20260625"]
    bad629630 = [s for s in summaries if s["day"] in ("20260629", "20260630")]

    pbv2_delta_rows = []
    for sm in summaries:
        virtual = int(sm.get("virtual_extra_pbv2_reach") or 0)
        current_blocked = int(sm.get("live_data_stale_price") or 0)
        pbv2_delta_rows.append(
            {
                "day": sm["day"],
                "session": sm["session"],
                "cohort": sm["cohort"],
                "current_data_stale_price_blocks": current_blocked,
                "proposed_liquidity_guard_pbv2_rescue": virtual,
                "p603_board_fallback_rescue": int(sm.get("p603_rescue_from_data_stale") or 0),
                "delta_vs_p603": virtual - int(sm.get("p603_rescue_from_data_stale") or 0),
                "baseline_pbv2_accept_audit": int(sm.get("pbv2_accept_audit_count") or 0),
                "proposed_pbv2_reach_est": int(sm.get("pbv2_accept_audit_count") or 0) + virtual,
            }
        )

    liq_rows = []
    for sm in bad629630:
        liq_rows.append(
            {
                "day": sm["day"],
                "session": sm["session"],
                "score3_event_ok_trade_stale_only": sm.get("score3_event_ok_trade_stale_blocked"),
                "score3_trade_only_data_stale": sm.get("score3_trade_only_data_stale"),
                "liquidity_guard_rescue_pbv2": sm.get("trade_only_rescue_pbv2"),
                "p603_rescue": sm.get("p603_rescue_from_data_stale"),
                "liquidity_better_than_p603": int(sm.get("trade_only_rescue_pbv2") or 0)
                >= int(sm.get("p603_rescue_from_data_stale") or 0),
            }
        )

    good_stale_on_pass = Counter()
    for r in all_rows:
        if r["cohort"] == "GOOD" and r["entry_decision"]:
            good_stale_on_pass[r["stale_combo"]] += 1

    total_rescue = sum(int(s.get("trade_only_rescue_pbv2") or 0) for s in summaries)
    total_p603 = sum(int(s.get("p603_rescue_from_data_stale") or 0) for s in summaries)
    total_score3_rescue = sum(int(s.get("score3_event_ok_trade_stale_blocked") or 0) for s in bad629630)

    mandatory = {
        "1_event_stale_definition": f"eval_ts - push recorded_at > {THRESHOLD_SEC}s",
        "2_board_stale_definition": f"eval_ts - min(BidTime,AskTime) > {THRESHOLD_SEC}s",
        "3_trade_stale_definition": f"eval_ts - CurrentPriceTime > {THRESHOLD_SEC}s or missing (liquidity_stale_trade tag)",
        "4_proposed_event_reject": PROPOSED_EVENT_REJECT,
        "5_proposed_trade_handling": f"{PROPOSED_TRADE_TAG} (non-blocking liquidity guard)",
        "6_625_pbv2_pass_stale_combo": dict(good_stale_on_pass),
        "7_629_630_score3_event_ok_trade_stale_only": total_score3_rescue,
        "8_liquidity_guard_pbv2_rescue_total": total_rescue,
        "9_p603_fallback_rescue_total": total_p603,
        "10_liquidity_guard_vs_p603": "better_or_equal" if total_rescue >= total_p603 else "worse",
        "11_pnl_note": "PnL/PF unchanged in shadow; virtual rescue counts only — full PnL needs replay Phase620",
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "threshold_sec": THRESHOLD_SEC,
        "sessions": summaries,
        "mandatory_answers": mandatory,
        "recommendation": (
            "Split semantics: reject on event_stale_price + data_stale_board only; "
            "demote CurrentPriceTime stale to liquidity_stale_trade guard. "
            f"Virtual PBv2 rescue {total_rescue} evals vs P603 fallback {total_p603} on BAD days."
        ),
        "implementation_note": "No production code change in Phase619 — shadow classification only.",
    }

    rep = job.reports
    _write_csv(
        rep / "phase619_stale_split_summary.csv",
        list(summaries[0].keys()) if summaries else ["day"],
        summaries,
    )
    _write_csv(
        rep / "phase619_event_board_trade_stale_breakdown.csv",
        ["day", "session", "cohort", "stale_combo", "count", "pct"],
        breakdown_rows,
    )
    _write_csv(rep / "phase619_pbv2_reach_delta.csv", list(pbv2_delta_rows[0].keys()) if pbv2_delta_rows else ["day"], pbv2_delta_rows)
    _write_csv(
        rep / "phase619_trade_stale_liquidity_guard_analysis.csv",
        list(liq_rows[0].keys()) if liq_rows else ["day"],
        liq_rows,
    )
    (rep / "phase619_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    import sys
    from pathlib import Path as P

    r = run_phase619(P(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(r["verdict"])
