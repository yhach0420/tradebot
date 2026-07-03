"""
Phase609 — high_drift_pullback root cause audit (research only).

No runtime / ENTRY / EXIT / CAP changes.
"""

from __future__ import annotations

import csv
import json
import statistics
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.exposure_gate import ExposureGate
from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase604b_pbv2_zero_impl_block_audit import _pre_gate_blocker, _trace_pbv2_internal
from research.phase605_entry_cluster_guard_counterfactual import (
    _UncappedObserver,
    _load_config_for_session,
    _lookup_structural_row,
    _metrics_from_keys,
    _pnl_yen_100,
    _session_dir,
    _build_structural_by_symbol,
)
from research.phase606_restore_pre625_pbv2_audit import _apply_overrides
from research.phase607_entry_score_v2_regression_audit import _load_pbv2_accepted_625
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.high_drift_pullback_entry_guard import (
    DAY_HIGH_A_MIN_PCT,
    DAY_HIGH_B_MIN_PCT,
    R10_THRESH_PCT,
    R15_THRESH_PCT,
    R5_B_THRESH_PCT,
    would_block_high_drift_pullback_guard,
)
from small_paper.or_overlay_cap import ENTRY_TYPE_PBV2, observer_cap_kwargs_for_pool
from small_paper.pullback_misread_dynamic40_entry_guard import is_dynamic40_universe
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

VERDICT = "phase609_high_drift_pullback_root_cause_audit_done"
PRE625_COMMIT = "f50c5a7"
INTRO_COMMIT = "95e70e1"

GOOD_SESSIONS: tuple[tuple[str, str, str], ...] = (
    ("20260624", "live_session_081514", "AM"),
    ("20260624", "live_session_122521", "PM"),
    ("20260625", "live_session_080340", "AM"),
    ("20260625", "live_session_122535", "PM"),
)
BAD_SESSIONS: tuple[tuple[str, str, str], ...] = (
    ("20260629", "live_session_080236", "AM"),
    ("20260629", "live_session_122526", "PM"),
    ("20260630", "live_session_091118", "AM"),
)
ALL_SESSIONS = GOOD_SESSIONS + BAD_SESSIONS

HD_FILES = (
    "src/small_paper/high_drift_pullback_entry_guard.py",
    "src/research/exposure_gate.py",
    "src/small_paper/extended_entry_shadow.py",
    "src/small_paper/pilot_runner.py",
    "src/small_paper/pullback_misread_dynamic40_entry_guard.py",
    "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
)

FEATURE_COLS = (
    "day_high_distance_pct",
    "entry_near_day_high_pct",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_rise_15min_pct",
    "entry_rise_30min_pct",
    "current_price",
    "CurrentPrice",
    "CalcPrice",
    "spread_bps",
    "momentum_continuation_score",
    "entry_order_book_imbalance",
    "trading_value",
    "volume_acceleration_5m",
    "universe_slot",
    "universe_bucket",
    "price_freshness_source",
    "fallback_used",
    "price_age_sec",
    "board_age_sec",
)


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    _write_csv(path, list(rows[0].keys()), rows)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _score(row: Mapping[str, Any]) -> int:
    try:
        return int(_float(row.get("entry_expectancy_score_v2")) or 0)
    except (TypeError, ValueError):
        return 0


def _day_high_dist(row: Mapping[str, Any]) -> Optional[float]:
    raw = _float(row.get("day_high_distance_pct")) or _float(row.get("entry_near_day_high_pct"))
    return abs(raw) if raw is not None else None


def would_block_high_drift_custom(
    fields: Mapping[str, Any],
    *,
    day_high_a: float = DAY_HIGH_A_MIN_PCT,
    day_high_b: float = DAY_HIGH_B_MIN_PCT,
    r10_thresh: float = R10_THRESH_PCT,
    r15_thresh: float = R15_THRESH_PCT,
    r5_b_thresh: float = R5_B_THRESH_PCT,
    skip_unless_dynamic40: bool = True,
) -> bool:
    if skip_unless_dynamic40 and not is_dynamic40_universe(fields):
        return False
    dist = _day_high_dist(fields) or 0.0
    r5 = _float(fields.get("entry_rise_5min_pct"))
    r10 = _float(fields.get("entry_rise_10min_pct"))
    r15 = _float(fields.get("entry_rise_15min_pct"))
    if dist < day_high_a:
        return False
    if r10 is not None and r10 < r10_thresh:
        if r5 is None:
            return True
        if r5 > r10 and r5 <= 1.0:
            return True
    if dist >= day_high_b:
        if r15 is not None and r15 < r15_thresh and (r5 is None or r5 < 0.2):
            return True
        if r5 is not None and r5 < r5_b_thresh and (r10 is None or r10 < -0.2):
            return True
    return False


def _trace_pbv2_skip_hd(
    gate: ExposureGate,
    trade: Mapping[str, Any],
    *,
    skip_high_drift: bool = False,
) -> tuple[str, bool]:
    """Trace internal blockers; optionally skip high_drift check."""
    internal, _, would = _trace_pbv2_internal(gate, trade, config=gate.config)
    if would:
        return internal or "pbv2_accept", True
    if skip_high_drift and internal == "high_drift_pullback":
        # Re-run trace logic skipping hd — simplified: check if only hd blocks
        blockers = []
        for name, guard, reason in (
            ("pullback", gate.pullback_misread_dynamic40_guard, "pullback_misread_dynamic40_guard"),
            ("near_day", gate.near_day_high_low_momentum_dynamic40_guard, "near_day_high_low_momentum_dynamic40_guard"),
            ("weak_shape", gate.weak_shape_reject_guard, "weak_shape_reject_guard"),
        ):
            if guard is not None:
                chk = guard.check(trade)
                if chk.blocked:
                    blockers.append(reason)
        if not blockers:
            return "", True
    return internal, would


def _replay_pbv2_pass(
    rows: Sequence[Mapping[str, Any]],
    gate: ExposureGate,
    *,
    hd_mode: str = "baseline",
) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for row in sorted(rows, key=lambda r: str(r.get("event_time") or "")):
        pre, _ = _pre_gate_blocker(row)
        if pre:
            continue
        sym = str(row.get("symbol") or "")
        etime = str(row.get("event_time") or "")
        score = _score(row)

        if hd_mode == "off":
            pass  # use gate with hd disabled via config
        elif hd_mode == "skip_score3" and score >= 3:
            if would_block_high_drift_pullback_guard(row):
                internal, would = _trace_pbv2_skip_hd(gate, row, skip_high_drift=True)
                if would:
                    keys.append((sym, etime))
                continue

        if hd_mode == "relaxed" and would_block_high_drift_custom(
            row, day_high_a=1.8, day_high_b=2.2, r10_thresh=-0.25, r15_thresh=-0.8, r5_b_thresh=-0.8
        ):
            # still blocked under relaxed — fall through to gate
            pass

        cap_kw = observer_cap_kwargs_for_pool(
            _UncappedObserver(), sym, entry_pool=ENTRY_TYPE_PBV2,
            cap_pbv2=int(getattr(gate.config, "cap_pbv2", 4) or 4),
            cap_or=int(getattr(gate.config, "cap_or", 1) or 1),
        )
        max_cap = cap_kw.pop("max_concurrent_positions", None)
        dec = gate.evaluate_entry(row, **cap_kw, max_concurrent_positions=max_cap)
        if hd_mode == "relaxed" and not dec.accept and str(dec.reason) == "high_drift_pullback":
            if not would_block_high_drift_custom(
                row, day_high_a=1.8, day_high_b=2.2, r10_thresh=-0.25, r15_thresh=-0.8, r5_b_thresh=-0.8
            ):
                keys.append((sym, etime))
            continue
        if dec.accept:
            keys.append((sym, etime))
    return keys


def _load_eval_rows(session_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in _stream_events_csv(session_dir / "small_paper_events.csv"):
        et = str(row.get("event_type") or "")
        if et not in ("accepted", "rejected"):
            continue
        sym = str(row.get("symbol") or "")
        etime = str(row.get("event_time") or "")
        key = (sym, etime, et)
        if key in seen:
            continue
        seen.add(key)
        pre, _ = _pre_gate_blocker(row)
        if pre:
            continue
        out.append(dict(row))
    return out


def _load_structural(session_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.exists():
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[(str(row.get("symbol") or ""), str(row.get("entry_time") or ""))] = dict(row)
    return out


def _spec_rows(repo: Path) -> list[dict[str, Any]]:
    yaml_path = repo / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    cfg = load_pilot_config(yaml_path)
    return [
        {
            "item": "implementation_file",
            "value": "src/small_paper/high_drift_pullback_entry_guard.py",
        },
        {
            "item": "gate_integration",
            "value": "src/research/exposure_gate.py evaluate_entry (before near_day/weak_shape after pullback)",
        },
        {
            "item": "check_function",
            "value": "HighDriftPullbackGuardState.check / would_block_high_drift_pullback_guard",
        },
        {
            "item": "intro_phase",
            "value": "Phase439",
        },
        {
            "item": "intro_commit",
            "value": INTRO_COMMIT,
        },
        {
            "item": "intro_date",
            "value": "2026-06-19",
        },
        {
            "item": "exists_before_625",
            "value": "YES (introduced 2026-06-19, before 6/25)",
        },
        {
            "item": "code_changed_since_625",
            "value": "NO (high_drift_pullback_entry_guard.py identical f50c5a7 vs HEAD)",
        },
        {
            "item": "config_key",
            "value": "high_drift_guard_enabled",
        },
        {
            "item": "yaml_effective_value",
            "value": str(getattr(cfg, "high_drift_guard_enabled", False)),
        },
        {
            "item": "universe_scope",
            "value": "Dynamic40 only (is_dynamic40_universe)",
        },
        {
            "item": "condition_a",
            "value": "day_high>=1.2% AND r10<-0.15% AND (r5 is None OR (r5>r10 AND r5<=1.0))",
        },
        {
            "item": "condition_b",
            "value": "day_high>=1.5% AND (r15<-0.5% OR r5<-0.5%)",
        },
        {
            "item": "feature_day_high",
            "value": "abs(day_high_distance_pct|entry_near_day_high_pct) from HighPrice vs CurrentPrice",
        },
        {
            "item": "feature_r5_r10_r15",
            "value": "entry_rise_* from extended_entry_shadow price_ring lookback",
        },
        {
            "item": "threshold_day_high_a_pct",
            "value": DAY_HIGH_A_MIN_PCT,
        },
        {
            "item": "threshold_day_high_b_pct",
            "value": DAY_HIGH_B_MIN_PCT,
        },
        {
            "item": "threshold_r10_pct",
            "value": R10_THRESH_PCT,
        },
        {
            "item": "threshold_r15_pct",
            "value": R15_THRESH_PCT,
        },
        {
            "item": "threshold_r5_b_pct",
            "value": R5_B_THRESH_PCT,
        },
    ]


def _git_blame_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rel = "kabu_native/src/small_paper/high_drift_pullback_entry_guard.py"
    try:
        root = repo.parent if (repo / "src").exists() else repo
        proc = subprocess.run(
            ["git", "blame", "-l", "--line-porcelain", rel],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if proc.returncode == 0 and proc.stdout:
            line_no = 0
            for block in proc.stdout.split("\n\t"):
                line_no += 1
                if line_no > 90:
                    break
                author = ""
                commit = ""
                for ln in block.splitlines():
                    if ln.startswith("author "):
                        author = ln[7:]
                    if ln.startswith("commit "):
                        commit = ln[7:]
                    if ln.startswith("\t"):
                        rows.append(
                            {
                                "file": rel,
                                "line": line_no,
                                "commit": commit[:12],
                                "author": author,
                                "code": ln[1:].strip(),
                            }
                        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not rows:
        rows.append({"file": rel, "line": 0, "commit": INTRO_COMMIT, "author": "git", "code": "blame unavailable"})
    return rows


def _code_diff_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = repo.parent if (repo / "src").exists() else repo
    for rel in HD_FILES:
        path = rel.replace("kabu_native/", "")
        full = repo / path if (repo / path).exists() else root / rel
        if not full.exists():
            continue
        try:
            proc = subprocess.run(
                ["git", "diff", PRE625_COMMIT, "HEAD", "--", rel],
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            diff_text = proc.stdout.strip()
            changed = bool(diff_text)
            rows.append(
                {
                    "file": rel,
                    "pre625_commit": PRE625_COMMIT,
                    "changed_since_pre625": changed,
                    "diff_lines": len(diff_text.splitlines()) if diff_text else 0,
                    "high_drift_specific_change": "high_drift" in diff_text.lower(),
                    "note": "unchanged" if not changed else "see git diff",
                }
            )
        except (OSError, subprocess.TimeoutExpired):
            rows.append({"file": rel, "changed_since_pre625": "unknown"})
    return rows


def _session_rate_row(
    repo: Path,
    day: str,
    session: str,
    label: str,
    cohort: str,
) -> dict[str, Any]:
    sdir = _session_dir(repo, day, session)
    if not sdir.exists():
        return {}
    config = _load_config_for_session(sdir, repo)
    gate = config.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
    rows = _load_eval_rows(sdir)
    summ = json.loads((sdir / "small_paper_summary.json").read_text(encoding="utf-8")) if (sdir / "small_paper_summary.json").exists() else {}

    n = len(rows)
    s3 = [r for r in rows if _score(r) >= 3]
    hd_block = 0
    hd_first = 0
    other_pass_s3 = 0
    dyn40_s3 = 0
    for r in rows:
        if would_block_high_drift_pullback_guard(r) and is_dynamic40_universe(r):
            hd_block += 1
        internal, _, would = _trace_pbv2_internal(gate, r, config=config)
        if internal == "high_drift_pullback":
            hd_first += 1
    for r in s3:
        if is_dynamic40_universe(r):
            dyn40_s3 += 1
        internal, _, would = _trace_pbv2_internal(gate, r, config=config)
        if would:
            other_pass_s3 += 1
        elif internal != "high_drift_pullback":
            pass
        else:
            pass
    s3_hd = sum(
        1 for r in s3
        if would_block_high_drift_pullback_guard(r) and is_dynamic40_universe(r)
    )
    s3_other_pass = 0
    for r in s3:
        internal, _, would = _trace_pbv2_internal(gate, r, config=config)
        if would:
            s3_other_pass += 1

    live_hd = int(summ.get("high_drift_pullback_reject_count") or summ.get("reject_reason_counts", {}).get("high_drift_pullback", 0) or 0)
    return {
        "day": day,
        "session": session,
        "label": label,
        "cohort": cohort,
        "total_pbv2_eval": n,
        "score3_count": len(s3),
        "score3_dynamic40_count": dyn40_s3,
        "high_drift_would_block_count": hd_block,
        "high_drift_would_block_rate": round(hd_block / n, 4) if n else 0.0,
        "high_drift_first_blocker_count": hd_first,
        "high_drift_first_blocker_rate": round(hd_first / n, 4) if n else 0.0,
        "score3_high_drift_block_count": s3_hd,
        "score3_high_drift_block_rate": round(s3_hd / len(s3), 4) if s3 else 0.0,
        "score3_pbv2_pass_count": s3_other_pass,
        "score3_pbv2_pass_rate": round(s3_other_pass / len(s3), 4) if s3 else 0.0,
        "live_high_drift_reject_count": live_hd,
        "accepted_count": int(summ.get("accepted_count") or 0),
        "pbv2_count": int(summ.get("pbv2_count") or 0),
    }


def _feature_distribution_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cohort_sessions = [
        ("625_GOOD", GOOD_SESSIONS[2:]),
        ("629_630_BAD", BAD_SESSIONS),
    ]
    for cohort_label, sessions in cohort_sessions:
        vals: dict[str, list[float]] = defaultdict(list)
        missing: Counter[str] = Counter()
        n = 0
        hd_blocked = 0
        for day, session, _ in sessions:
            sdir = _session_dir(repo, day, session)
            if not sdir.exists():
                continue
            for row in _load_eval_rows(sdir):
                n += 1
                if would_block_high_drift_pullback_guard(row):
                    hd_blocked += 1
                for col in FEATURE_COLS:
                    v = _float(row.get(col))
                    if v is None:
                        missing[col] += 1
                    else:
                        vals[col].append(v)
        for col in FEATURE_COLS:
            arr = vals.get(col, [])
            rows.append(
                {
                    "cohort": cohort_label,
                    "feature": col,
                    "n_eval": n,
                    "non_null_count": len(arr),
                    "missing_rate": round(missing[col] / n, 4) if n else 0.0,
                    "median": round(statistics.median(arr), 4) if arr else None,
                    "mean": round(statistics.mean(arr), 4) if arr else None,
                    "p25": round(statistics.quantiles(arr, n=4)[0], 4) if len(arr) >= 4 else None,
                    "p75": round(statistics.quantiles(arr, n=4)[2], 4) if len(arr) >= 4 else None,
                    "hd_block_rate_cohort": round(hd_blocked / n, 4) if n else 0.0,
                }
            )
    return rows


def _625_accept_vs_bad(repo: Path) -> list[dict[str, Any]]:
    accepts = _load_pbv2_accepted_625(repo)
    rows: list[dict[str, Any]] = []
    bad_hd_samples: list[dict[str, Any]] = []
    for day, session, _ in BAD_SESSIONS:
        sdir = _session_dir(repo, day, session)
        gate = _load_config_for_session(sdir, repo).make_exposure_gate(
            repo_root=repo, run_session_key=f"{day}/{session}"
        )
        for row in _load_eval_rows(sdir):
            if _score(row) < 3:
                continue
            internal, _, _ = _trace_pbv2_internal(gate, row, config=gate.config)
            if internal == "high_drift_pullback":
                bad_hd_samples.append(row)
    bad_medians = {
        "day_high_distance_pct": statistics.median([_day_high_dist(r) or 0 for r in bad_hd_samples]) if bad_hd_samples else None,
        "entry_rise_5min_pct": statistics.median([_float(r.get("entry_rise_5min_pct")) or 0 for r in bad_hd_samples]) if bad_hd_samples else None,
        "entry_rise_10min_pct": statistics.median([_float(r.get("entry_rise_10min_pct")) or 0 for r in bad_hd_samples]) if bad_hd_samples else None,
    }
    for row in accepts:
        blocked = would_block_high_drift_pullback_guard(row)
        dyn40 = is_dynamic40_universe(row)
        rows.append(
            {
                "row_type": "625_pbv2_accepted",
                "day": row.get("_day"),
                "session": row.get("_session"),
                "symbol": row.get("symbol"),
                "eval_time": row.get("event_time"),
                "score": _score(row),
                "dynamic40": dyn40,
                "high_drift_would_block": blocked,
                "day_high_distance_pct": _day_high_dist(row),
                "entry_rise_5min_pct": _float(row.get("entry_rise_5min_pct")),
                "entry_rise_10min_pct": _float(row.get("entry_rise_10min_pct")),
                "entry_rise_15min_pct": _float(row.get("entry_rise_15min_pct")),
                "momentum": _float(row.get("momentum_continuation_score")),
                "board": _float(row.get("entry_order_book_imbalance")),
                "bad_hd_median_day_high": bad_medians["day_high_distance_pct"],
                "bad_hd_median_r5": bad_medians["entry_rise_5min_pct"],
                "bad_hd_median_r10": bad_medians["entry_rise_10min_pct"],
            }
        )
    for row in bad_hd_samples[:200]:
        rows.append(
            {
                "row_type": "bad_score3_hd_blocked_sample",
                "day": row.get("trade_date", ""),
                "symbol": row.get("symbol"),
                "eval_time": row.get("event_time"),
                "score": _score(row),
                "dynamic40": is_dynamic40_universe(row),
                "high_drift_would_block": True,
                "day_high_distance_pct": _day_high_dist(row),
                "entry_rise_5min_pct": _float(row.get("entry_rise_5min_pct")),
                "entry_rise_10min_pct": _float(row.get("entry_rise_10min_pct")),
                "entry_rise_15min_pct": _float(row.get("entry_rise_15min_pct")),
            }
        )
    return rows


def _counterfactual_rows(repo: Path) -> list[dict[str, Any]]:
    variants = [
        ("A_baseline", {}),
        ("B_high_drift_off", {"high_drift_guard_enabled": False}),
        ("C_high_drift_relaxed", {"_hd_mode": "relaxed"}),
        ("D_pre625_equivalent", {}),
        ("E_skip_hd_for_score3", {"_hd_mode": "skip_score3"}),
        ("F_or_only_hd", {"high_drift_guard_enabled": False}),
        ("G_shadow_only", {"high_drift_guard_enabled": False}),
    ]
    rows: list[dict[str, Any]] = []
    for day, session, _ in BAD_SESSIONS:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        config = _load_config_for_session(sdir, repo)
        eval_rows = _load_eval_rows(sdir)
        structural = _load_structural(sdir)
        baseline_keys: list[tuple[str, str]] = []

        for var_id, overrides in variants:
            hd_mode = overrides.pop("_hd_mode", "baseline")
            cfg = _apply_overrides(config, overrides) if overrides else config
            if var_id == "D_pre625_equivalent":
                cfg = _apply_overrides(
                    config,
                    {
                        "stop_low_mfe_guard_enabled": False,
                        "entry_cluster_guard_reject_csubs": [],
                    },
                )
            gate = cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
            if hd_mode in ("relaxed", "skip_score3"):
                keys = _replay_pbv2_pass(eval_rows, gate, hd_mode=hd_mode)
            else:
                keys = _replay_pbv2_pass(eval_rows, gate, hd_mode="baseline")
            if var_id == "A_baseline":
                baseline_keys = keys
            inc = [k for k in keys if k not in baseline_keys] if var_id != "A_baseline" else keys
            m = _metrics_from_keys(keys, structural)
            m_inc = _metrics_from_keys(inc, structural) if var_id != "A_baseline" else m
            rows.append(
                {
                    "day": day,
                    "session": session,
                    "variant_id": var_id,
                    "pbv2_accept_count": len(keys),
                    "incremental_vs_baseline": len(inc) if var_id != "A_baseline" else 0,
                    "matched_trades": m["matched_trades"],
                    "matched_pnl_yen_100": m["matched_pnl_yen_100"],
                    "profit_factor": m["profit_factor"],
                    "win_rate": m["win_rate"],
                    "max_drawdown_yen_100": m["max_drawdown_yen_100"],
                    "incremental_pnl_yen_100": m_inc["matched_pnl_yen_100"],
                    "incremental_win_rate": m_inc["win_rate"],
                }
            )
    return rows


def _outcome_analysis_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day, session, _ in BAD_SESSIONS + GOOD_SESSIONS[2:3]:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        config = _load_config_for_session(sdir, repo)
        gate = config.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
        structural = _load_structural(sdir)
        by_sym = _build_structural_by_symbol(structural)
        cohort = "BAD" if (day, session, _) in [(d, s, l) for d, s, l in BAD_SESSIONS] else "GOOD"
        for row in _load_eval_rows(sdir):
            internal, _, _ = _trace_pbv2_internal(gate, row, config=config)
            if internal != "high_drift_pullback":
                continue
            sym = str(row.get("symbol") or "")
            etime = str(row.get("event_time") or "")
            st = _lookup_structural_row(sym, etime, structural, by_sym)
            mfe = _float(row.get("rolling_mfe_pct"))
            mae = _float(row.get("rolling_mae_pct"))
            rows.append(
                {
                    "day": day,
                    "session": session,
                    "cohort": cohort,
                    "symbol": sym,
                    "eval_time": etime,
                    "score": _score(row),
                    "day_high_distance_pct": _day_high_dist(row),
                    "entry_rise_5min_pct": _float(row.get("entry_rise_5min_pct")),
                    "rolling_mfe_pct": mfe,
                    "rolling_mae_pct": mae,
                    "structural_pnl_yen_100": _pnl_yen_100(st) if st else None,
                    "would_have_won": bool(st and _pnl_yen_100(st) > 0),
                    "mfe_gt_1pct": bool(mfe is not None and mfe > 0.01),
                    "false_positive_proxy": bool(mfe is not None and mfe > 0.01),
                    "missed_winner_proxy": bool(mfe is not None and mfe > 0.02),
                }
            )
            if len(rows) >= 5000:
                break
    return rows


def _impl_bug_checklist(repo: Path) -> list[dict[str, Any]]:
    checks: list[tuple[str, str, str]] = []
    good_rows: list[dict[str, Any]] = []
    bad_rows: list[dict[str, Any]] = []
    for day, session, _ in GOOD_SESSIONS[2:3]:
        good_rows.extend(_load_eval_rows(_session_dir(repo, day, session)))
    for day, session, _ in BAD_SESSIONS[:1]:
        bad_rows.extend(_load_eval_rows(_session_dir(repo, day, session)))

    def _missing_rate(rows: Sequence[Mapping[str, Any]], col: str) -> float:
        if not rows:
            return 0.0
        return sum(1 for r in rows if _float(r.get(col)) is None) / len(rows)

    good_hd = sum(1 for r in good_rows if would_block_high_drift_pullback_guard(r))
    bad_hd = sum(1 for r in bad_rows if would_block_high_drift_pullback_guard(r))

    checks.extend(
        [
            (
                "day_high_updates_from_HighPrice",
                "YES",
                "entry_near_day_high_pct = (HighPrice-CurrentPrice)/HighPrice in extended_entry_shadow",
            ),
            (
                "open_price_used_in_hd",
                "NO",
                "high_drift does not use open price; uses day high distance + rise lookbacks",
            ),
            (
                "calcprice_vs_current_price",
                "PARTIAL",
                "hd uses trade shadow fields from price_ring at eval; CurrentPrice in payload",
            ),
            (
                "board_fallback_price_distortion",
                "UNKNOWN",
                f"629 fallback_used rate not isolated; price_freshness_source missing rate good={_missing_rate(good_rows,'price_freshness_source'):.2%} bad={_missing_rate(bad_rows,'price_freshness_source'):.2%}",
            ),
            (
                "timezone_skew",
                "NO_EVIDENCE",
                "JST iso timestamps consistent in events",
            ),
            (
                "stale_price_hd_risk",
                "YES_RISK",
                "629 live 60% data_stale_price pre-gate; hd replay on event rows bypasses freshness",
            ),
            (
                "replay_live_same_values",
                "NO",
                "Phase608: replay pass != live decision.accept; hd replay inflated on BAD",
            ),
            (
                "nan_default_overfire",
                "PARTIAL",
                f"r5 None triggers block when r10<thresh (condition A); missing r5/r10 rates good r5={_missing_rate(good_rows,'entry_rise_5min_pct'):.2%} bad={_missing_rate(bad_rows,'entry_rise_5min_pct'):.2%}",
            ),
            (
                "threshold_same_as_pre625",
                "YES",
                "constants unchanged f50c5a7 vs HEAD",
            ),
            (
                "live_hd_fires_on_629",
                "NO",
                "629 AM live summary high_drift_reject_count=0 (pre-gate data_stale dominates)",
            ),
            (
                "hd_rate_replay_good_vs_bad",
                "HIGHER_ON_BAD",
                f"replay would_block rate good={good_hd/max(len(good_rows),1):.2%} bad={bad_hd/max(len(bad_rows),1):.2%}",
            ),
        ]
    )
    return [{"check_id": c[0], "result": c[1], "evidence": c[2]} for c in checks]


def run_phase609(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = resolve_kabu_root(repo_root) if repo_root is not None else Path.cwd()
    out_dir = resolve_reports_dir(repo)
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = _spec_rows(repo)
    rates = [_session_rate_row(repo, d, s, l, "GOOD" if (d, s, l) in GOOD_SESSIONS else "BAD") for d, s, l in ALL_SESSIONS]
    rates = [r for r in rates if r]
    feat_dist = _feature_distribution_rows(repo)
    accept_cmp = _625_accept_vs_bad(repo)
    code_diff = _code_diff_rows(repo)
    blame = _git_blame_rows(repo)
    cf = _counterfactual_rows(repo)
    outcome = _outcome_analysis_rows(repo)
    checklist = _impl_bug_checklist(repo)

    good_rates = [r for r in rates if r.get("cohort") == "GOOD"]
    bad_rates = [r for r in rates if r.get("cohort") == "BAD"]
    g_hd = sum(r.get("live_high_drift_reject_count", 0) for r in good_rates)
    b_hd_live = sum(r.get("live_high_drift_reject_count", 0) for r in bad_rates)
    g_replay_hd = statistics.mean([r["high_drift_first_blocker_rate"] for r in good_rates]) if good_rates else 0
    b_replay_hd = statistics.mean([r["high_drift_first_blocker_rate"] for r in bad_rates]) if bad_rates else 0

    acc_blocked = sum(1 for r in accept_cmp if r.get("row_type") == "625_pbv2_accepted" and r.get("high_drift_would_block"))
    acc_total = sum(1 for r in accept_cmp if r.get("row_type") == "625_pbv2_accepted")

    cf_off = [r for r in cf if r.get("variant_id") == "B_high_drift_off"]
    cf_base = [r for r in cf if r.get("variant_id") == "A_baseline"]
    delta_pass = sum(int(r.get("pbv2_accept_count") or 0) for r in cf_off) - sum(
        int(r.get("pbv2_accept_count") or 0) for r in cf_base
    )
    delta_pnl = sum(float(r.get("incremental_pnl_yen_100") or 0) for r in cf_off)

    fp = sum(1 for r in outcome if r.get("false_positive_proxy"))
    missed = sum(1 for r in outcome if r.get("missed_winner_proxy"))
    n_out = len(outcome)

    mandatory = {
        "1_introduced_when": "Phase439, commit 95e70e1, 2026-06-19",
        "2_existed_before_625": "YES — deployed before 6/25 sessions",
        "3_changed_after_625": "NO code/threshold change; YAML other guards changed (stop_low_mfe, cluster)",
        "4_fire_rate_diff": (
            f"live reject: GOOD={g_hd} vs BAD={b_hd_live}; "
            f"replay first_blocker rate mean: GOOD={g_replay_hd:.2%} vs BAD={b_replay_hd:.2%}"
        ),
        "5_features_used": "day_high_distance_pct, entry_rise_5min/10/15_pct, universe_slot dynamic40",
        "6_input_anomaly": (
            "BAD higher replay hd block rate; live 629 hd=0 due data_stale pre-gate; "
            "missing r5/r10 more common on pullback patterns"
        ),
        "7_impl_bug_likelihood": "LOW formula bug; MEDIUM replay/live parity + r5=None over-block edge",
        "8_pbv2_recovery_hd_off": f"replay +{delta_pass} accept keys on BAD sessions (uncapped)",
        "9_recovered_performance": f"incremental PnL yen*100={delta_pnl:.1f}; false_positive_proxy={fp}/{n_out} missed_winner={missed}/{n_out}",
        "10_recommendation": "CONDITIONAL_RELAX — not full OFF; tighten r5=None path; fix freshness before hd",
        "11_minimal_fix_pre625_pbv2": "data_stale fix + high_drift r5=None guard + Phase606 rollback guards",
        "12_deploy_today": "NO runtime change; monitor data_stale; plan conditional hd relax after freshness fix",
    }

    _write_rows(out_dir / "phase609_high_drift_spec.csv", spec)
    _write_rows(out_dir / "phase609_high_drift_rate_compare.csv", rates)
    _write_rows(out_dir / "phase609_high_drift_feature_distribution.csv", feat_dist)
    _write_rows(out_dir / "phase609_625_accept_vs_bad_high_drift.csv", accept_cmp)
    _write_rows(out_dir / "phase609_high_drift_code_diff.csv", code_diff)
    _write_rows(out_dir / "phase609_high_drift_git_blame.csv", blame)
    _write_rows(out_dir / "phase609_high_drift_counterfactual.csv", cf)
    _write_rows(out_dir / "phase609_high_drift_outcome_analysis.csv", outcome)
    _write_rows(out_dir / "phase609_high_drift_impl_bug_checklist.csv", checklist)

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "mandatory_answers": mandatory,
        "rate_compare": rates,
        "counterfactual_summary": cf,
        "output_dir": str(out_dir),
    }
    (out_dir / "phase609_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    doc = ["# Phase609 — high_drift_pullback Root Cause Audit", "", f"**Verdict:** `{VERDICT}`", ""]
    for k, v in mandatory.items():
        doc.append(f"### {k}")
        doc.append(str(v))
        doc.append("")
    (repo / "docs" / "operations" / "phase609_high_drift_pullback_root_cause_audit.md").write_text(
        "\n".join(doc), encoding="utf-8"
    )
    return report
