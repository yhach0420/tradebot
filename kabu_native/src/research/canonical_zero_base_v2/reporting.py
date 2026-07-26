"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.canonical_zero_base_v2.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


def write_xlsx(path: Path, sheets: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    from openpyxl import Workbook

    def _cell(v: Any) -> Any:
        if v is None or isinstance(v, (int, float, bool, str)):
            return v
        if isinstance(v, datetime):
            return v.isoformat()
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name in REQUIRED_SHEETS:
        rows = list(sheets.get(name) or [{"status": "empty"}])
        ws = wb.create_sheet(str(name)[:31])
        keys: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        if not keys:
            ws.append(["empty"])
            continue
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _rows(obj: Any) -> list[dict[str, Any]]:
    if obj is None:
        return [{"status": "empty"}]
    if isinstance(obj, list):
        if not obj:
            return [{"status": "empty"}]
        if isinstance(obj[0], dict):
            return obj
        return [{"value": v} for v in obj]
    if isinstance(obj, dict):
        return [
            {"key": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v}
            for k, v in obj.items()
        ]
    return [{"value": str(obj)}]


def build_sheets(payload: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    lanes = payload.get("lanes") or {}
    sep = payload.get("entry_separation") or []
    return {
        "README": [{"phase": "canonical_zero_base_v2", "run_id": payload.get("run_id")}],
        "SOURCE_AUDIT": _rows(payload.get("source_audit")),
        "DATA_DISCOVERY": _rows(payload.get("discovery", {}).get("audits")),
        "DATA_SPLIT": _rows({
            "warmup": payload.get("discovery", {}).get("warmup"),
            "train": payload.get("discovery", {}).get("train"),
            "validation": payload.get("discovery", {}).get("validation"),
            "strict_oos": payload.get("discovery", {}).get("strict_oos"),
            "insufficient": payload.get("discovery", {}).get("insufficient_oos"),
        }),
        "CANONICAL_COVERAGE": _rows({
            "ask": payload.get("discovery", {}).get("ask_coverage_mean"),
            "bid": payload.get("discovery", {}).get("bid_coverage_mean"),
        }),
        "ANCHOR_INVENTORY": _rows(payload.get("anchor_inventory")),
        "ANCHOR_SAMPLES": _rows(payload.get("anchor_samples")),
        "OUTCOME_LABEL_SPEC": _rows(payload.get("outcome_bounds")),
        "OUTCOME_COUNTS": _rows(payload.get("outcome_counts")),
        "ENTRY_FEATURE_INVENTORY": _rows(payload.get("entry_feature_inventory")),
        "ENTRY_FEATURE_FORMULAS": _rows(payload.get("entry_feature_inventory")),
        "ENTRY_FEATURE_QUALITY": _rows(payload.get("entry_feature_quality")),
        "ENTRY_FEATURE_SEPARATION": sep[:200] if isinstance(sep, list) else _rows(sep),
        "ENTRY_FEATURE_STABILITY": _rows(payload.get("entry_stability")),
        "ENTRY_INTERACTIONS": _rows((payload.get("interactions") or {}).get("top")),
        "ENTRY_REJECTED_FEATURES": _rows(payload.get("entry_rejected")),
        "Z1_EPISODES": _rows((payload.get("episode_quality") or {}).get("Z1")),
        "Z2_EPISODES": _rows((payload.get("episode_quality") or {}).get("Z2")),
        "Z3_EPISODES": _rows((payload.get("episode_quality") or {}).get("Z3")),
        "Z4_EPISODES": _rows((payload.get("episode_quality") or {}).get("Z4")),
        "EPISODE_QUALITY": _rows(payload.get("episode_quality")),
        "Z1_ENTRY_CANDIDATES": _rows((lanes.get("Z1") or {}).get("entry_gate_log")),
        "Z2_ENTRY_CANDIDATES": _rows((lanes.get("Z2") or {}).get("entry_gate_log")),
        "Z3_ENTRY_CANDIDATES": _rows((lanes.get("Z3") or {}).get("entry_gate_log")),
        "Z4_ENTRY_CANDIDATES": _rows((lanes.get("Z4") or {}).get("entry_gate_log")),
        "TRAIN_ENTRY_GATE": _rows({sid: (lanes.get(sid) or {}).get("train_entry_pass_n") for sid in ("Z1", "Z2", "Z3", "Z4")}),
        "VALIDATION_ENTRY_GATE": _rows({sid: (lanes.get(sid) or {}).get("val_entry_pass_n") for sid in ("Z1", "Z2", "Z3", "Z4")}),
        "POST_ENTRY_PATHS": _rows(payload.get("post_entry_summary")),
        "EXIT_FEATURE_INVENTORY": _rows(payload.get("exit_feature_inventory")),
        "EXIT_FEATURE_FORMULAS": _rows(payload.get("exit_feature_inventory")),
        "EXIT_FEATURE_SEPARATION": _rows(payload.get("exit_separation")),
        "EXIT_FEATURE_LEADTIME": _rows(payload.get("exit_leadtime")),
        "EXIT_FEATURE_STABILITY": _rows(payload.get("exit_stability")),
        "FALSE_WARNING": _rows(payload.get("false_warning")),
        "TRUE_INVALIDATION": _rows(payload.get("true_invalidation")),
        "WINNER_RETENTION": _rows(payload.get("winner_retention")),
        "Z1_EXIT_CANDIDATES": _rows({"exit_ids": (lanes.get("Z1") or {}).get("exit_ids")}),
        "Z2_EXIT_CANDIDATES": _rows({"exit_ids": (lanes.get("Z2") or {}).get("exit_ids")}),
        "Z3_EXIT_CANDIDATES": _rows({"exit_ids": (lanes.get("Z3") or {}).get("exit_ids")}),
        "Z4_EXIT_CANDIDATES": _rows({"exit_ids": (lanes.get("Z4") or {}).get("exit_ids")}),
        "ENTRY_EXIT_PAIRS": _rows({sid: (lanes.get(sid) or {}).get("raw_pairs") for sid in ("Z1", "Z2", "Z3", "Z4")}),
        "TRAIN_PAIR_GATE": _rows({sid: (lanes.get(sid) or {}).get("train_pair_pass_n") for sid in ("Z1", "Z2", "Z3", "Z4")}),
        "VALIDATION_PAIR_GATE": _rows({sid: (lanes.get(sid) or {}).get("val_pair_pass_n") for sid in ("Z1", "Z2", "Z3", "Z4")}),
        "STRICT_OOS": _rows({sid: (lanes.get(sid) or {}).get("oos_results") for sid in ("Z1", "Z2", "Z3", "Z4")}),
        "EXECUTION_E0_E5": _rows(payload.get("execution_summary", {}).get("E0_E5")),
        "EXECUTION_S0_S5": _rows(payload.get("execution_summary", {}).get("S0_S5")),
        "LATENCY_SENSITIVITY": _rows(payload.get("execution_summary", {}).get("pairs")),
        "ONE_TICK_ADVERSE": _rows(payload.get("execution_summary", {}).get("one_tick_adverse")),
        "CAP5_Z1": _rows(((lanes.get("Z1") or {}).get("final") or {}).get("cap")),
        "CAP5_Z2": _rows(((lanes.get("Z2") or {}).get("final") or {}).get("cap")),
        "CAP5_Z3": _rows(((lanes.get("Z3") or {}).get("final") or {}).get("cap")),
        "CAP5_Z4": _rows(((lanes.get("Z4") or {}).get("final") or {}).get("cap")),
        "CAP5_INTEGRATED": _rows(payload.get("cap5_integrated")),
        "CAP_BLOCKED": _rows(payload.get("cap_blocked")),
        "SLOT_RECYCLING": _rows(payload.get("slot_recycling")),
        "DAILY_RESULTS": _rows(payload.get("daily_results")),
        "SYMBOL_RESULTS": _rows(payload.get("symbol_results")),
        "DEPENDENCY": _rows(payload.get("dependency_summary")),
        "LEAVE_ONE_OUT": _rows(payload.get("leave_one_out")),
        "OVERFIT_GATES": _rows(payload.get("overfit_gates")),
        "TESTS": _rows((payload.get("tests") or {}).get("rows")),
        "VERDICT": _rows(payload.get("verdict")),
    }


def write_report_md(payload: Mapping[str, Any]) -> str:
    v = payload.get("verdict") or {}
    d = payload.get("discovery") or {}
    kind = payload.get("feature_kind_counts") or {}
    sep = payload.get("entry_separation") or []
    inter = ((payload.get("interactions") or {}).get("top") or [])[:15]
    lines = [
        "# Canonical Zero-Base v2 Full Feature Discovery & Joint ENTRY–EXIT Rebuild",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- final_verdict: **{v.get('final_verdict')}**",
        "",
        "## 1. Why v1 was not yesterday's method",
        "",
        "v1 fixed a shared feature dictionary and T0–T9 group templates, then fitted Z1–Z4 into that grid.",
        "Yesterday's method labels Winner/STOP/NoProgress, mines causal features, ranks separation,",
        "builds strategy-specific state machines, then jointly searches ENTRY×EXIT.",
        "This v2 follows that discovery path and discards v1 templates/thresholds/X0 finals.",
        "",
        f"## 2–9. Feature counts",
        "",
        f"- ENTRY feature candidates: **{payload.get('n_entry_features')}**",
        f"- EXIT feature candidates: **{payload.get('n_exit_features')}**",
        f"- strategy ENTRY feature pools: see lanes entry_rules_n",
        f"- strategy EXIT candidates: see lanes exit_candidates_n",
        f"- snapshot/static: {kind.get('static')}",
        f"- dynamic: {kind.get('dynamic')}",
        f"- sequence: {kind.get('sequence')}",
        f"- state-transition: {kind.get('state-transition', 0)}",
        "",
        "## 10–16. Rankings",
        "",
        "### ENTRY top features",
    ]
    for r in (sep[:15] if isinstance(sep, list) else []):
        lines.append(f"- {r.get('feature')}: score={r.get('score')} d_wn={r.get('d_winner_vs_never')} stab={r.get('stability')}")
    lines += ["", "### STOP / NoProgress separation (via d_winner_vs_early_stop / noprogress)"]
    stop_sorted = sorted(sep, key=lambda r: -abs(r.get("d_winner_vs_early_stop") or 0))[:10] if isinstance(sep, list) else []
    np_sorted = sorted(sep, key=lambda r: -abs(r.get("d_winner_vs_noprogress") or 0))[:10] if isinstance(sep, list) else []
    for r in stop_sorted:
        lines.append(f"- STOP sep: {r.get('feature')} d={r.get('d_winner_vs_early_stop')}")
    for r in np_sorted:
        lines.append(f"- NP sep: {r.get('feature')} d={r.get('d_winner_vs_noprogress')}")
    lines += ["", "### Interactions top"]
    for r in inter:
        lines.append(f"- {r.get('features')} incr={r.get('incremental')} wr={r.get('winner_rate')}")
    lines += [
        "",
        "## 17–23. Strategy state machines & episodes",
        "",
        "- Z1: IMPULSE→PULLBACK→LOW_CONFIRMED→RECLAIM_ATTEMPT→RECLAIM_CONFIRMED→ACTIVE",
        "- Z2: RANGE→BREAKOUT_ATTEMPT→CROSSED→HOLDING→CONFIRMED→ACTIVE",
        "- Z3: WALL→PERSIST→TEST→ABSORPTION→DEPLETE→BROKEN→HOLD→CONFIRMED (cancel≠absorb)",
        "- Z4: COMPRESSION→CONFIRMED→EXPANSION→RANGE_BROKEN→HOLD→CONFIRMED",
        "",
        f"episode_quality: {json.dumps(payload.get('episode_quality'), default=str)[:1200]}",
        "",
        "## 24–32. Gates & pairs",
        "",
    ]
    for sid in ("Z1", "Z2", "Z3", "Z4"):
        lane = (payload.get("lanes") or {}).get(sid) or {}
        lines.append(
            f"- {sid}: status={lane.get('status')} train_entry={lane.get('train_entry_pass_n')} "
            f"val_entry={lane.get('val_entry_pass_n')} raw_pairs={lane.get('raw_pairs')} "
            f"train_pair={lane.get('train_pair_pass_n')} val_pair={lane.get('val_pair_pass_n')} "
            f"oos={lane.get('oos_pairs_n')} judgment={lane.get('judgment')}"
        )
        fin = lane.get("final") or {}
        lines.append(f"  - final ENTRY: {fin.get('rule')}")
        lines.append(f"  - final EXIT: {fin.get('exit')}")
        lines.append(f"  - OOS cap: {fin.get('cap')}")
    exec_s = payload.get("execution_summary") or {}
    lines += [
        "",
        "## 33–36. Execution",
        "",
        f"- E1/S1: {(exec_s.get('pairs') or {}).get('E1/S1')}",
        f"- E2/S2: {(exec_s.get('pairs') or {}).get('E2/S2')}",
        f"- E4/S4: {(exec_s.get('pairs') or {}).get('E4/S4')}",
        f"- 1tick adverse: {exec_s.get('one_tick_adverse')}",
        f"- resolution: {exec_s.get('resolution')}",
        "",
        "## 37–40. Portfolio / next",
        "",
        f"- CAP5 integrated: {payload.get('cap5_integrated')}",
        f"- dependency: {payload.get('dependency_summary')}",
        f"- reject reasons: absolute TRAIN/VAL gates; insufficient OOS forbids EDGE_CONFIRMED",
        "- next: collect >=10 strict OOS days; keep capture-only; no Paper until absolute OOS edge",
        "",
        f"- submit={payload.get('submit')} cancel={payload.get('cancel')} live_order={payload.get('live_order')}",
        f"- train/val/oos: {d.get('train')} / {d.get('validation')} / {d.get('strict_oos')}",
        "",
    ]
    return "\n".join(lines)


def emit_artifacts(out_dir: Path, payload: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in list(out_dir.iterdir()):
        if p.is_file() and p.name not in REQUIRED_ARTIFACTS:
            p.unlink()
    slim = json.loads(json.dumps(payload, default=str))
    for lane in (slim.get("lanes") or {}).values():
        fin = lane.get("final")
        if isinstance(fin, dict) and "trades" in fin:
            fin["trades_n"] = len(fin.get("trades") or [])
            fin.pop("trades", None)
    (out_dir / "report.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(write_report_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    for p in list(out_dir.iterdir()):
        if p.is_file() and p.name not in REQUIRED_ARTIFACTS:
            p.unlink()
