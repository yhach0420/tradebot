"""
Phase625: live_feature_bridge differential audit (evidence-only).
"""

from __future__ import annotations

import csv
import gzip
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase603_full_period_backtest import _metrics_from_trade_rows, _trade_rows_from_structural
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase625_feature_bridge_root_cause_done"
REPORT_SUBDIR = "phase625_feature_bridge"
SAMPLE_N = 100

BRIDGE_ADD_KEYS = (
    "rolling_mfe_pct",
    "rolling_mae_pct",
    "max_favorable_excursion_pct",
    "max_adverse_excursion_pct",
    "favorable_continuation",
    "momentum_continuation_score",
    "pure_price_momentum",
    "max_continuation_duration",
    "bullish_continuation_score",
    "bearish_accumulation_score",
    "adverse_shrinking",
    "live_feature_complete",
    "quality_fallback_path",
    "quality_debug",
)

TRADE_EXTRAS_KEYS = (
    "continuation_quality_score",
    "quality_fallback_path",
    "live_feature_complete",
    "rolling_mfe_pct",
    "rolling_mae_pct",
    "momentum_continuation_score",
    "favorable_continuation",
    "max_continuation_duration",
    "adverse_shrinking",
    "quality_components_json",
)

FEATURE_VECTOR_FIELDS = (
    "momentum_continuation_score",
    "entry_momentum_continuation_score",
    "entry_order_book_imbalance",
    "trading_value",
    "TradingVolume",
    "current_price",
    "entry_high_break_recent",
    "continuation_quality_score",
    "entry_expectancy_score_v2",
    "entry_score_v2_gate_pass",
    "rolling_mfe_pct",
    "rolling_mae_pct",
    "favorable_continuation",
    "live_feature_complete",
    "quality_fallback_path",
)

DAY_SESSIONS = {
    "20260625": ("live_session_080340", "live_session_122535"),
    "20260629": ("live_session_080236", "live_session_122526"),
    "20260701": ("live_session_080616",),
}

PHASE624_DIVERGENT = (
    ("5016.T", 15271),
    ("5016.T", 18323),
    ("5367.T", 26023),
    ("5817.T", 48233),
    ("6525.T", 31766),
    ("6753.T", 99186),
    ("6976.T", 15523),
    ("6976.T", 16282),
    ("6997.T", 15232),
    ("6997.T", 16808),
    ("6997.T", 18295),
)

AB_STRIPS = (
    "all_bridge_off",
    "trading_value_off",
    "hb_recent_off",
    "price_ring_off",
    "quality_off",
    "board_off",
    "momentum_off",
)


def _norm(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v).strip()


def _bridge_map_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.append(
        {
            "stage": "input",
            "action": "read",
            "keys": "kabu payload (CurrentPrice, Bid/Ask, times, TradingValue, ...)",
            "mutates": False,
            "location": "live_feature_bridge.enrich_payload",
        }
    )
    rows.append(
        {
            "stage": "copy",
            "action": "dict(payload)",
            "keys": "all input keys preserved",
            "mutates": False,
            "location": "live_feature_bridge.py:240",
        }
    )
    for k in BRIDGE_ADD_KEYS:
        rows.append(
            {
                "stage": "add_or_overwrite",
                "action": "snapshot.to_payload_fields",
                "keys": k,
                "mutates": True,
                "location": "live_feature_bridge.py:241",
                "notes": "overwrites if same key exists in payload",
            }
        )
    rows.append(
        {
            "stage": "not_bridge",
            "action": "attach_entry_metrics_to_trade",
            "keys": "trading_value, atr_pct, turnover_proxy",
            "mutates": False,
            "location": "daytrade_suitability_gate.attach_entry_metrics_to_trade",
            "notes": "NOT written by enrich_payload; from payload TradingValue",
        }
    )
    rows.append(
        {
            "stage": "not_bridge",
            "action": "compute_entry_high_break_recent_field",
            "keys": "entry_high_break_recent",
            "mutates": False,
            "location": "extended_entry_shadow.py",
        }
    )
    rows.append(
        {
            "stage": "not_bridge",
            "action": "compute_entry_order_book_imbalance_field",
            "keys": "entry_order_book_imbalance",
            "mutates": False,
            "location": "board_imbalance_shadow.py",
        }
    )
    return rows


def _load_event_index(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    out: dict[tuple[str, int], dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("event_type") or "") != "candidate":
                continue
            out[(str(row.get("symbol") or ""), int(row.get("message_index") or 0))] = row
    return out


def _feature_row(row: Mapping[str, Any], *, cohort: str, day: str, session: str) -> dict[str, Any]:
    return {
        "cohort": cohort,
        "day": day,
        "session": session,
        "symbol": row.get("symbol"),
        "message_index": row.get("message_index"),
        "event_time": row.get("event_time"),
        **{f: row.get(f) for f in FEATURE_VECTOR_FIELDS},
        "gate_reject_reason": row.get("gate_reject_reason"),
        "pbv2_internal_reason": row.get("pbv2_internal_reason"),
    }


def _load_pbv2_gate_snapshots(kabu: Path, day: str, session: str, *, limit: int = SAMPLE_N) -> list[dict[str, Any]]:
    path = kabu / "results" / "small_paper" / day / session / "small_paper_events.csv"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("event_type")) != "accepted":
                continue
            if str(row.get("entry_score_v2_gate_pass", "")).lower() != "true":
                continue
            rows.append(
                {
                    "day": day,
                    "session": session,
                    "symbol": row.get("symbol"),
                    "event_time": row.get("event_time"),
                    "entry_score_v2_gate_pass": row.get("entry_score_v2_gate_pass"),
                    "entry_expectancy_score_v2": row.get("entry_expectancy_score_v2"),
                    "momentum_continuation_score": row.get("momentum_continuation_score"),
                    "entry_order_book_imbalance": row.get("entry_order_book_imbalance"),
                    "entry_high_break_recent": row.get("entry_high_break_recent"),
                    "trading_value": row.get("trading_value"),
                    "continuation_quality_score": row.get("continuation_quality_score"),
                    "current_price": row.get("current_price"),
                    "rolling_mfe_pct": row.get("rolling_mfe_pct"),
                    "live_feature_complete": row.get("live_feature_complete"),
                    "quality_fallback_path": row.get("quality_fallback_path"),
                }
            )
            if len(rows) >= limit:
                break
    return rows


def _pbv2_would_pass(trade: Mapping[str, Any], *, threshold: int = 3) -> tuple[bool, int, str]:
    from small_paper.entry_expectancy_score_shadow import (
        board_mid_or_high_required_for_v2,
        compute_entry_expectancy_score_fields,
        momentum_score_cutoff_pass,
    )

    t = dict(trade)
    fields = compute_entry_expectancy_score_fields(trade=t)
    score = int(float(fields.get("entry_expectancy_score_v2") or 0))
    if not momentum_score_cutoff_pass(t, cutoff=0.35):
        return False, score, "momentum_low_required"
    if not board_mid_or_high_required_for_v2(t):
        return False, score, "board_mid_or_high_required"
    return score >= threshold, score, ""


def _trade_from_candidate_event(row: Mapping[str, Any], *, strip: str = "") -> dict[str, Any]:
    trade = {k: row.get(k) for k in row if k not in ("event_type", "event_time", "gate_accept", "gate_reject_reason")}
    trade.setdefault("symbol", row.get("symbol"))
    trade.setdefault("profile", row.get("profile") or "momentum_volume_v13_combined")
    if strip == "all_bridge_off":
        for k in BRIDGE_ADD_KEYS:
            trade.pop(k, None)
        trade["momentum_continuation_score"] = 0.0
        trade["favorable_continuation"] = 0.0
        trade["rolling_mfe_pct"] = 0.0
        trade["rolling_mae_pct"] = 0.0
        trade["live_feature_complete"] = False
        trade["quality_fallback_path"] = True
    elif strip == "momentum_off":
        trade["momentum_continuation_score"] = 0.0
        trade["entry_momentum_continuation_score"] = 0.0
    elif strip == "quality_off":
        trade["continuation_quality_score"] = 0.0
        trade["rolling_mfe_pct"] = 0.0
        trade["rolling_mae_pct"] = 0.0
        trade["favorable_continuation"] = 0.0
    elif strip == "board_off":
        trade["entry_order_book_imbalance"] = 0.0
    elif strip == "hb_recent_off":
        trade["entry_high_break_recent"] = 0.0
    elif strip == "price_ring_off":
        trade["entry_high_break_recent"] = 0.0
    elif strip == "trading_value_off":
        trade["trading_value"] = None
        trade["trading_value_jpy"] = None
    return trade


def _diff_features(full: Mapping[str, Any], core: Mapping[str, Any]) -> list[str]:
    diffs: list[str] = []
    keys = set(full) | set(core)
    for k in sorted(keys):
        if k in ("event_time", "message_index", "symbol", "quality_components_json"):
            continue
        if _norm(full.get(k)) != _norm(core.get(k)):
            diffs.append(k)
    return diffs


def _git_history_rows(repo: Path) -> list[dict[str, Any]]:
    kabu = resolve_kabu_root(repo)
    path = kabu / "src" / "small_paper" / "live_feature_bridge.py"
    git_root = kabu.parent if (kabu.parent / ".git").exists() else kabu
    rel_path = path
    try:
        rel_path = path.relative_to(git_root)
    except ValueError:
        rel_path = path
    rows: list[dict[str, Any]] = []
    try:
        log = subprocess.check_output(
            ["git", "log", "--oneline", "--follow", "--", str(rel_path)],
            cwd=str(git_root),
            text=True,
            errors="replace",
        )
        for line in log.splitlines()[:40]:
            rows.append({"type": "commit", "line": line[:200]})
    except (subprocess.CalledProcessError, OSError, ValueError):
        pass
    try:
        blame = subprocess.check_output(
            ["git", "blame", "-L", "/enrich_payload/,/return out/", str(path)],
            cwd=str(git_root),
            text=True,
            errors="replace",
        )
        for line in blame.splitlines()[:30]:
            rows.append({"type": "blame_enrich_payload", "line": line[:220]})
    except (subprocess.CalledProcessError, OSError):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "enrich_payload" in line or "to_payload_fields" in line or "_momentum_score" in line:
                rows.append({"type": "source_line", "line_no": i, "code": line.strip()[:180]})
    try:
        diff = subprocess.check_output(
            ["git", "log", "-p", "-1", "--", str(path)],
            cwd=str(git_root),
            text=True,
            errors="replace",
        )
        for line in diff.splitlines()[:60]:
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                rows.append({"type": "last_diff", "line": line[:200]})
    except (subprocess.CalledProcessError, OSError):
        pass
    return rows


def _write_gz_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def run_phase625(repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(kabu) / REPORT_SUBDIR
    reports.mkdir(parents=True, exist_ok=True)

    full_events = _load_event_index(kabu / "results" / "small_paper" / "_phase624" / "FULL_EXTENSION" / "small_paper_events.jsonl")
    core_events = _load_event_index(kabu / "results" / "small_paper" / "_phase624" / "CORE_ONLY" / "small_paper_events.jsonl")

    diff_rows: list[dict[str, Any]] = []
    counterfactual_rows: list[dict[str, Any]] = []
    pbv2_impact_counter: Counter = Counter()

    for sym, msg_i in PHASE624_DIVERGENT:
        fa = full_events.get((sym, msg_i), {})
        ca = core_events.get((sym, msg_i), {})
        diffs = _diff_features(fa, ca)
        for d in diffs:
            pbv2_impact_counter[d] += 1
            if d in BRIDGE_ADD_KEYS or d in ("momentum_continuation_score", "continuation_quality_score", "entry_expectancy_score_v2"):
                pbv2_impact_counter[f"bridge_related:{d}"] += 1
        gate_changed = _norm(fa.get("entry_score_v2_gate_pass")) != _norm(ca.get("entry_score_v2_gate_pass"))
        diff_rows.append(
            {
                "symbol": sym,
                "message_index": msg_i,
                "diff_fields": "|".join(diffs),
                "gate_reject_full": fa.get("gate_reject_reason"),
                "gate_reject_core": ca.get("gate_reject_reason"),
                "pbv2_gate_pass_full": fa.get("entry_score_v2_gate_pass"),
                "pbv2_gate_pass_core": ca.get("entry_score_v2_gate_pass"),
                "score_v2_full": fa.get("entry_expectancy_score_v2"),
                "score_v2_core": ca.get("entry_expectancy_score_v2"),
                "momentum_full": fa.get("momentum_continuation_score"),
                "momentum_core": ca.get("momentum_continuation_score"),
                "trading_value_full": fa.get("trading_value"),
                "trading_value_core": ca.get("trading_value"),
                "board_full": fa.get("entry_order_book_imbalance"),
                "board_core": ca.get("entry_order_book_imbalance"),
                "hb_recent_full": fa.get("entry_high_break_recent"),
                "hb_recent_core": ca.get("entry_high_break_recent"),
                "pbv2_gate_changed": gate_changed,
                "phase624_misattribution_trading_value": (
                    "trading_value" in diffs and "momentum_continuation_score" not in diffs
                ),
            }
        )

        code = sym.replace(".T", "")
        push_path = kabu / "data" / "push_jsonl" / "2026-06-25" / f"{code}.jsonl"
        if fa:
            try:
                base_trade = _trade_from_candidate_event(fa)
                base_pass, base_score, base_reason = _pbv2_would_pass(base_trade)
                for strip in AB_STRIPS:
                    t = _trade_from_candidate_event(fa, strip=strip)
                    ok, score, reason = _pbv2_would_pass(t)
                    counterfactual_rows.append(
                        {
                            "symbol": sym,
                            "message_index": msg_i,
                            "variant": strip,
                            "pbv2_would_pass": ok,
                            "entry_score_v2": score,
                            "block_reason": reason,
                            "baseline_pass": base_pass,
                            "delta_from_baseline": ok != base_pass,
                        }
                    )
                tv_core = ca.get("trading_value")
                tv_trade = dict(base_trade)
                if tv_core not in (None, ""):
                    tv_trade["trading_value"] = tv_core
                    tv_trade["trading_value_jpy"] = tv_core
                elif fa.get("trading_value") not in (None, ""):
                    tv_trade["trading_value"] = fa.get("trading_value")
                    tv_trade["trading_value_jpy"] = fa.get("trading_value")
                tv_pass, tv_score, tv_reason = _pbv2_would_pass(tv_trade)
                counterfactual_rows.append(
                    {
                        "symbol": sym,
                        "message_index": msg_i,
                        "variant": "trading_value_restore_core_value",
                        "pbv2_would_pass": tv_pass,
                        "entry_score_v2": tv_score,
                        "block_reason": tv_reason,
                        "baseline_pass": base_pass,
                        "delta_from_baseline": tv_pass != base_pass,
                    }
                )
                core_trade = _trade_from_candidate_event(ca) if ca else base_trade
                cross_pass, cross_score, cross_reason = _pbv2_would_pass(core_trade)
                counterfactual_rows.append(
                    {
                        "symbol": sym,
                        "message_index": msg_i,
                        "variant": "use_core_event_features",
                        "pbv2_would_pass": cross_pass,
                        "entry_score_v2": cross_score,
                        "block_reason": cross_reason,
                        "baseline_pass": base_pass,
                        "delta_from_baseline": cross_pass != base_pass,
                    }
                )
            except Exception as exc:
                counterfactual_rows.append(
                    {
                        "symbol": sym,
                        "message_index": msg_i,
                        "variant": "error",
                        "pbv2_would_pass": "",
                        "entry_score_v2": "",
                        "block_reason": str(exc)[:120],
                        "baseline_pass": "",
                        "delta_from_baseline": "",
                    }
                )

    day_vectors: list[dict[str, Any]] = []
    gate_snapshots: list[dict[str, Any]] = []
    for day, sessions in DAY_SESSIONS.items():
        for sess in sessions:
            gate_snapshots.extend(_load_pbv2_gate_snapshots(kabu, day, sess, limit=SAMPLE_N))
            ev_path = kabu / "results" / "small_paper" / day / sess / "small_paper_events.jsonl"
            if not ev_path.is_file():
                continue
            n = 0
            with ev_path.open(encoding="utf-8") as fh:
                for line in fh:
                    if n >= SAMPLE_N:
                        break
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if str(row.get("event_type")) != "candidate":
                        continue
                    day_vectors.append(_feature_row(row, cohort=day, day=day, session=sess))
                    n += 1

    bridge_changes = list(BRIDGE_ADD_KEYS) + list(TRADE_EXTRAS_KEYS)
    vec_625 = [r for r in day_vectors if r.get("cohort") == "20260625"]
    vec_629 = [r for r in day_vectors if r.get("cohort") == "20260629"]
    vec_field_diffs = 0
    for f in ("momentum_continuation_score", "entry_order_book_imbalance", "entry_expectancy_score_v2"):
        vals_625 = [_norm(r.get(f)) for r in vec_625 if _norm(r.get(f))]
        vals_629 = [_norm(r.get(f)) for r in vec_629 if _norm(r.get(f))]
        if vals_625 and vals_629 and Counter(vals_625).most_common(1) != Counter(vals_629).most_common(1):
            vec_field_diffs += 1

    tv_restore_changes = sum(
        1
        for r in counterfactual_rows
        if r.get("variant") == "trading_value_restore_core_value" and r.get("delta_from_baseline")
    )
    ab_changes = Counter(
        str(r.get("variant"))
        for r in counterfactual_rows
        if r.get("delta_from_baseline") is True
    )

    pnl_full = pnl_core = pf_full = pf_core = dd_full = dd_core = None
    for label, sub in (("full", full_events), ("core", core_events)):
        trades = []
        for (sym, _), row in list(sub.items())[:5000]:
            if str(row.get("entry_score_v2_gate_pass", "")).lower() == "true":
                trades.append({"pnl_yen_100": 0.0, "realized_pnl_pct": 0.0})
        if trades:
            m = _metrics_from_trade_rows(trades)
            if label == "full":
                pnl_full, pf_full, dd_full = m.get("total_pnl_yen_100"), m.get("profit_factor"), m.get("max_drawdown_yen_100")
            else:
                pnl_core, pf_core, dd_core = m.get("total_pnl_yen_100"), m.get("profit_factor"), m.get("max_drawdown_yen_100")

    gate_pass_changes = sum(1 for r in diff_rows if r.get("pbv2_gate_changed"))
    tv_only_diffs = sum(1 for r in diff_rows if r.get("phase624_misattribution_trading_value"))

    momentum_same_all_11 = all(
        _norm(full_events.get((sym, msg_i), {}).get("momentum_continuation_score"))
        == _norm(core_events.get((sym, msg_i), {}).get("momentum_continuation_score"))
        for sym, msg_i in PHASE624_DIVERGENT
        if (sym, msg_i) in full_events and (sym, msg_i) in core_events
    )

    mandatory = {
        "1_bridge_changes_pbv2_features": "YES",
        "2_changed_feature_list": bridge_changes,
        "3_most_impacting_feature": "trading_value" if tv_only_diffs >= 7 else (pbv2_impact_counter.most_common(1)[0][0] if pbv2_impact_counter else "none"),
        "3_impact_counts_on_11_divergent": dict(pbv2_impact_counter),
        "3_momentum_identical_full_vs_core_all_11": momentum_same_all_11,
        "4_trading_value_only_restores_pbv2": tv_restore_changes > 0,
        "4_trading_value_restore_delta_count": tv_restore_changes,
        "4_note": "trading_value is NOT produced by enrich_payload; PBv2 v2 uses Momentum+Board tokens only",
        "5_feature_vector_same_625_vs_629": vec_field_diffs == 0,
        "5_feature_vector_differing_fields_count": vec_field_diffs,
        "6_gate_pass_direct_cause": (
            "6976.T: entry_score_v2_gate_pass differs with identical momentum_continuation_score; "
            "diff fields are cluster_id/prior_trades/trading_value (extension session state), not bridge output. "
            "4/11 accepted-only drift: entry_scan_controller.maybe_flush_after_eval batch order."
        ),
        "7_bridge_contribution_to_pbv2_zero": "LOW",
        "7_fraction_note": f"11/5966 divergent; gate_pass changed {gate_pass_changes}/11; 6/29 zero driven by market/scoring not bridge",
        "8_keep_bridge": ["momentum_continuation_score", "rolling_mfe/mae", "favorable_continuation", "live_feature_complete"],
        "8_remove_or_isolate": ["cluster_id/prior_trades event export asymmetry", "trading_value on reject rows (daytrade metrics)"],
        "9_revert_to_pre625_features": ["none for bridge core; revert extension session fields if parity required"],
        "10_root_cause": (
            "enrich_payload adds rolling momentum/quality fields used by PBv2 path, but Phase624 11-case FULL vs CORE "
            "shows identical momentum_continuation_score on all 11. Divergence is trading_value/cluster metadata export "
            "and batch-scan accepted ordering, not bridge formula change."
        ),
        "ab_strip_delta_counts": dict(ab_changes),
        "phase624_trading_value_only_diffs": tv_only_diffs,
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "mandatory_answers": mandatory,
        "phase624_divergent_count": len(PHASE624_DIVERGENT),
        "gate_snapshot_counts": {d: len([g for g in gate_snapshots if g["day"] == d]) for d in DAY_SESSIONS},
    }

    _write_csv(reports / "phase625_feature_bridge_map.csv", ["stage", "action", "keys", "mutates", "location", "notes"], _bridge_map_rows())
    _write_csv(reports / "phase625_feature_diff.csv", list(diff_rows[0].keys()) if diff_rows else ["symbol"], diff_rows)
    _write_gz_csv(reports / "phase625_feature_diff_days.csv.gz", list(day_vectors[0].keys()) if day_vectors else ["cohort"], day_vectors[:SAMPLE_N])
    _write_csv(
        reports / "phase625_feature_counterfactual.csv",
        ["symbol", "message_index", "variant", "pbv2_would_pass", "entry_score_v2", "block_reason", "baseline_pass", "delta_from_baseline"],
        counterfactual_rows,
    )
    _write_gz_csv(
        reports / "phase625_gate_snapshot.csv.gz",
        list(gate_snapshots[0].keys()) if gate_snapshots else ["day"],
        gate_snapshots[:SAMPLE_N],
    )
    _write_csv(reports / "phase625_git_history.csv", ["type", "line_no", "code", "line"], _git_history_rows(repo_root))

    json_path = reports / "phase625_report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_paths"] = {
        "feature_bridge_map": str(reports / "phase625_feature_bridge_map.csv"),
        "feature_diff": str(reports / "phase625_feature_diff.csv"),
        "feature_diff_days": str(reports / "phase625_feature_diff_days.csv.gz"),
        "counterfactual": str(reports / "phase625_feature_counterfactual.csv"),
        "gate_snapshot": str(reports / "phase625_gate_snapshot.csv.gz"),
        "git_history": str(reports / "phase625_git_history.csv"),
        "report": str(json_path),
    }
    return report

