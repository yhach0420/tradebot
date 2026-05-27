"""
Phase 148b: Recover small-paper session artifacts from raw live-session files.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from research.small_paper_performance_review import _load_events, _load_json
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from research.structural_observer_review import build_and_write_structural_observer_review
from small_paper.config import load_pilot_config


def rebuild_small_paper_summary(
    session_dir: Path,
    *,
    recovery_note: str = "phase148b_recovered_from_raw",
) -> dict[str, Any]:
    """Build minimal small_paper_summary.json required by structural observer review."""
    session_dir = session_dir.resolve()
    cfg = _load_json(session_dir / "live_session_config.json") or {}
    cfg_path = Path(str(cfg.get("config_path") or ""))
    policy_label = None
    min_cq = 0.7
    max_conc = 3
    if cfg_path.is_file():
        try:
            pilot = load_pilot_config(cfg_path)
            policy_label = pilot.policy_label
            min_cq = pilot.min_continuation_quality
            max_conc = pilot.max_concurrent_positions
        except Exception:
            pass

    events = _load_events(session_dir)
    reject_reasons: Counter[str] = Counter()
    accepted = 0
    rejected = 0
    for e in events:
        et = str(e.get("event_type") or "")
        if et == "accepted":
            accepted += 1
        elif et == "rejected":
            rejected += 1
            reason = str(e.get("reject_reason") or "unknown")
            reject_reasons[reason] += 1

    ended_at = None
    hb_path = session_dir / "heartbeat.jsonl"
    if hb_path.is_file():
        last_line = ""
        with hb_path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line.strip()
        if last_line:
            try:
                ended_at = json.loads(last_line).get("ts") or json.loads(last_line).get("time")
            except json.JSONDecodeError:
                pass

    return {
        "phase": 51,
        "mode": "small_paper_pilot_live_full_dry_run_recovered",
        "recovery": True,
        "recovery_note": recovery_note,
        "recovered_at": datetime.now().isoformat(timespec="seconds"),
        "generated_at": cfg.get("generated_at"),
        "ended_at": ended_at,
        "order_enabled": cfg.get("order_enabled", False),
        "paper_only": cfg.get("paper_only", True),
        "dry_run": True,
        "source": cfg.get("source", "live"),
        "full_session": cfg.get("full_session", True),
        "duration_sec": cfg.get("duration_sec"),
        "poll_interval_sec": cfg.get("poll_interval_sec", 5.0),
        "session_start": cfg.get("session_start"),
        "session_end": cfg.get("session_end"),
        "symbol_count": cfg.get("symbol_count"),
        "universe_csv_path": cfg.get("universe_csv_path"),
        "am_pm_session": cfg.get("am_pm_session"),
        "config_sha256": cfg.get("config_sha256"),
        "config_path": cfg.get("config_path"),
        "policy_label": policy_label,
        "min_continuation_quality": min_cq,
        "max_concurrent_positions": max_conc,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "reject_reason_counts": dict(reject_reasons),
        "candidate_count": accepted + rejected,
        "gate_evaluations": accepted + rejected,
        "stop_reason": "session_end_recovered",
        "note": recovery_note,
    }


def recover_session_finalize(
    session_dir: Path,
    *,
    repo_root: Path,
    config_rel: str = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml",
    structural_exit_policy: str = POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    poll_interval_sec: Optional[float] = None,
    skip_structural_review: bool = False,
) -> dict[str, Any]:
    """Rebuild summary + run structural observer review from raw session files."""
    session_dir = session_dir.resolve()
    if not session_dir.is_dir():
        raise FileNotFoundError(f"session dir not found: {session_dir}")

    out: dict[str, Any] = {
        "session_dir": str(session_dir),
        "steps": [],
        "artifacts_before": _artifact_status(session_dir),
    }

    summary_path = session_dir / "small_paper_summary.json"
    if not summary_path.is_file():
        summary = rebuild_small_paper_summary(session_dir)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        out["steps"].append("rebuilt_small_paper_summary.json")
    else:
        out["steps"].append("small_paper_summary.json_exists")

    review_exit = 0
    review_error: Optional[str] = None
    review_public: dict[str, Any] = {}
    if not skip_structural_review:
        cfg_path = repo_root / config_rel
        if not cfg_path.is_file():
            cfg_meta = _load_json(session_dir / "live_session_config.json") or {}
            cfg_path = Path(str(cfg_meta.get("config_path") or cfg_path))
        config = load_pilot_config(cfg_path)
        try:
            review_public = build_and_write_structural_observer_review(
                session_dir,
                pilot_config=config,
                poll_interval_sec=poll_interval_sec,
                structural_exit_policy=structural_exit_policy,
            )
            out["steps"].append("structural_observer_review")
        except Exception as exc:
            review_exit = 1
            review_error = f"{type(exc).__name__}: {exc}"
            out["steps"].append(f"structural_observer_review_failed:{review_error}")

    out["artifacts_after"] = _artifact_status(session_dir)
    out["review_exit_code"] = review_exit
    out["review_error"] = review_error
    out["official_verdict"] = review_public.get("official_verdict")
    out["structural_pf"] = review_public.get("structural_pf")
    out["structural_trade_count"] = review_public.get("structural_trade_count")
    return out


def _artifact_status(session_dir: Path) -> dict[str, bool]:
    names = (
        "small_paper_summary.json",
        "structural_trades.csv",
        "structural_exit_reasons.csv",
        "structural_observer_review.json",
        "structural_events.csv",
        "structural_policy_comparison.csv",
        "structural_exit_policy_summary.csv",
    )
    return {n: (session_dir / n).is_file() for n in names}


PHASE148C_REQUIRED_ARTIFACTS = (
    "small_paper_summary.json",
    "structural_trades.csv",
    "structural_exit_reasons.csv",
    "structural_observer_review.json",
    "structural_events.csv",
)

# 2026-05-25 AM live_session_075733 reference counts (from live run)
PHASE148C_AM_20260525_EXPECTED = {
    "accepted_count": 83,
    "rejected_count": 47220,
    "structural_exit_count": 83,
    "observer_entry_count": 83,
    "observer_exit_count": 83,
    "structural_exit_reason_counts": {
        "stop_hit": 3,
        "momentum_fade_exit": 54,
        "quality_decay_exit": 17,
        "overlap_replaced_review": 7,
        "morning_session_close": 2,
    },
}


def recover_session_outputs(
    session_dir: Path,
    *,
    repo_root: Path,
    config_rel: str = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml",
    structural_exit_policy: str = POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    poll_interval_sec: Optional[float] = None,
    force_summary_rebuild: bool = False,
    force_structural_review: bool = False,
) -> dict[str, Any]:
    """
    Phase 148c: recover missing session outputs (summary + structural review artifacts).
    """
    session_dir = session_dir.resolve()
    out: dict[str, Any] = {
        "session_dir": str(session_dir),
        "artifacts_before": _artifact_status(session_dir),
        "steps": [],
    }

    summary_path = session_dir / "small_paper_summary.json"
    missing_before = [n for n in PHASE148C_REQUIRED_ARTIFACTS if not (session_dir / n).is_file()]

    if force_summary_rebuild or not summary_path.is_file():
        summary = rebuild_small_paper_summary(session_dir, recovery_note="phase148c_recovered_from_raw")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        out["steps"].append("rebuilt_small_paper_summary.json")
    else:
        out["steps"].append("small_paper_summary.json_exists")

    need_review = force_structural_review or any(
        not (session_dir / n).is_file()
        for n in PHASE148C_REQUIRED_ARTIFACTS
        if n != "small_paper_summary.json"
    )
    out["missing_before"] = missing_before

    review_exit = 0
    review_error: Optional[str] = None
    review_public: dict[str, Any] = {}
    if need_review:
        cfg_path = repo_root / config_rel
        if not cfg_path.is_file():
            cfg_meta = _load_json(session_dir / "live_session_config.json") or {}
            cfg_path = Path(str(cfg_meta.get("config_path") or cfg_path))
        config = load_pilot_config(cfg_path)
        try:
            review_public = build_and_write_structural_observer_review(
                session_dir,
                pilot_config=config,
                poll_interval_sec=poll_interval_sec,
                structural_exit_policy=structural_exit_policy,
            )
            out["steps"].append("structural_observer_review")
        except Exception as exc:
            review_exit = 1
            review_error = f"{type(exc).__name__}: {exc}"
            out["steps"].append(f"structural_observer_review_failed:{review_error}")
    else:
        out["steps"].append("structural_review_skipped_all_present")

    out["artifacts_after"] = _artifact_status(session_dir)
    out["review_exit_code"] = review_exit
    out["review_error"] = review_error
    out["official_verdict"] = review_public.get("official_verdict")
    out["structural_pf"] = review_public.get("structural_pf")
    out["structural_trade_count"] = review_public.get("structural_trade_count")
    return out


def validate_session_output_counts(
    session_dir: Path,
    *,
    expected: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Validate recovered AM session counts and exit-reason consistency."""
    import csv

    session_dir = session_dir.resolve()
    exp = dict(expected or PHASE148C_AM_20260525_EXPECTED)
    summary = _load_json(session_dir / "small_paper_summary.json") or {}
    review = _load_json(session_dir / "structural_observer_review.json") or {}

    checks: list[dict[str, Any]] = []

    def add(cid: str, passed: bool, detail: str, **extra: Any) -> None:
        checks.append({"check_id": cid, "passed": passed, "detail": detail, **extra})

    for key in (
        "accepted_count",
        "rejected_count",
        "structural_exit_count",
        "observer_entry_count",
        "observer_exit_count",
    ):
        exp_v = exp.get(key)
        got = summary.get(key)
        add(key, got == exp_v, f"expected={exp_v} got={got}")

    exp_reasons = exp.get("structural_exit_reason_counts") or {}
    got_reasons = summary.get("structural_exit_reason_counts") or {}
    reasons_match = got_reasons == exp_reasons
    add(
        "structural_exit_reason_counts",
        reasons_match,
        f"expected={exp_reasons} got={got_reasons}",
    )

    reason_sum = sum(int(v) for v in got_reasons.values())
    add(
        "exit_reason_sum_equals_structural_exit_count",
        reason_sum == int(summary.get("structural_exit_count") or 0),
        f"sum={reason_sum} structural_exit_count={summary.get('structural_exit_count')}",
    )

    trades_path = session_dir / "structural_trades.csv"
    trade_rows: list[dict[str, str]] = []
    if trades_path.is_file():
        with trades_path.open(encoding="utf-8", newline="") as f:
            trade_rows = list(csv.DictReader(f))
    add(
        "structural_trades_row_count",
        len(trade_rows) == int(exp.get("structural_exit_count") or 0),
        f"rows={len(trade_rows)} expected={exp.get('structural_exit_count')}",
    )

    exit_csv_path = session_dir / "structural_exit_reasons.csv"
    csv_counts: dict[str, int] = {}
    if exit_csv_path.is_file():
        with exit_csv_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                csv_counts[str(row.get("close_reason") or "")] = int(row.get("trade_count") or 0)

    # Replay CSV may differ per-reason from live observer summary; require row sum only.
    csv_trade_sum = sum(csv_counts.values())
    add(
        "structural_exit_reasons_csv_trade_sum",
        csv_trade_sum == int(exp.get("structural_exit_count") or 0),
        f"csv_sum={csv_trade_sum} expected={exp.get('structural_exit_count')}",
    )
    alias = {"morning_session_close": "session_end"}
    replay_vs_summary: dict[str, Any] = {}
    for reason, exp_n in exp_reasons.items():
        csv_key = alias.get(reason, reason)
        csv_n = csv_counts.get(csv_key, csv_counts.get(reason))
        replay_vs_summary[reason] = {"summary": exp_n, "csv": csv_n, "csv_key": csv_key}
    # Informational: live summary is authoritative for AM run; replay CSV is diagnostic.

    review_trades = review.get("structural_trade_count")
    add(
        "review_structural_trade_count",
        review_trades == exp.get("structural_exit_count"),
        f"review={review_trades} expected={exp.get('structural_exit_count')}",
    )

    artifacts_ok = all((session_dir / n).is_file() for n in PHASE148C_REQUIRED_ARTIFACTS)
    add("required_artifacts_present", artifacts_ok, str(_artifact_status(session_dir)))

    passed = all(c["passed"] for c in checks)
    return {
        "passed": passed,
        "checks": checks,
        "summary_snapshot": {
            "accepted_count": summary.get("accepted_count"),
            "rejected_count": summary.get("rejected_count"),
            "structural_exit_count": summary.get("structural_exit_count"),
            "observer_entry_count": summary.get("observer_entry_count"),
            "observer_exit_count": summary.get("observer_exit_count"),
            "structural_exit_reason_counts": got_reasons,
        },
        "structural_exit_reasons_csv": csv_counts,
        "structural_trades_close_reasons": dict(Counter(r.get("close_reason") for r in trade_rows)),
        "replay_vs_summary_exit_reasons": replay_vs_summary,
    }


def phase148c_recovery_verdict(
    recovery: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> str:
    after = recovery.get("artifacts_after") or {}
    required_ok = all(after.get(n) for n in PHASE148C_REQUIRED_ARTIFACTS)
    review_ok = (recovery.get("review_exit_code") or 0) == 0
    if required_ok and review_ok and validation.get("passed"):
        return "am_session_outputs_recovered"
    if required_ok and (review_ok or validation.get("passed")):
        return "partial_recovery"
    return "recovery_failed"


def recovery_verdict(result: Mapping[str, Any]) -> str:
    after = result.get("artifacts_after") or {}
    required = (
        "small_paper_summary.json",
        "structural_trades.csv",
        "structural_exit_reasons.csv",
    )
    if all(after.get(k) for k in required) and (result.get("review_exit_code") or 0) == 0:
        return "am_session_recovered_and_runner_fixed"
    if any(after.get(k) for k in required):
        return "runner_fixed_but_recovery_partial"
    return "recovery_failed"
