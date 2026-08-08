"""Atomic publish of report.json / report.md / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from research.e1_x6_provisional.constants import (
    ARTIFACT_DIR_REL,
    CANDIDATE_CAP,
    FINAL_BANNER,
    PROVISIONAL_BANNER,
)
from research.e1_x6_provisional.util import (
    atomic_replace_dir_files,
    progress,
    repo_root,
    sha256_file,
    sha256_obj,
    temp_work_root,
    write_json,
)

# Excel practical row budget (header + data). Split deterministically when exceeded.
EXCEL_MAX_DATA_ROWS = 1_000_000


def _banner_of(report: dict[str, Any], override: Optional[str] = None) -> str:
    if override:
        return override
    return str(report.get("banner") or PROVISIONAL_BANNER)


def _run_id_of(report: dict[str, Any]) -> str:
    return str(
        report.get("final_run_id")
        or report.get("provisional_run_id")
        or report.get("run_id")
        or ""
    )


def _write_report_md(report: dict[str, Any], path: Path, *, banner: str) -> None:
    rid = _run_id_of(report)
    lines = [
        f"# E1_X6 Report — `{rid}`",
        "",
        f"**{banner}**",
        "",
        f"- generated_at_jst: {report.get('generated_at_jst')}",
        f"- P0: {report.get('status', {}).get('P0')}",
        f"- P1: {report.get('status', {}).get('P1')}",
        f"- P2: {report.get('status', {}).get('P2')}",
        f"- verdict: {report.get('verdict') or report.get('status', {}).get('verdict')}",
        f"- NEXT_PHASE: {report.get('NEXT_PHASE')}",
        f"- p1_lock_sha256: `{((report.get('p1') or {}).get('p1_lock_sha256'))}`",
        f"- source_manifest_sha256: `{((report.get('source_manifest') or {}).get('source_manifest_sha256'))}`",
        "",
        "## Superseded runs",
        "",
    ]
    for s in report.get("superseded_runs") or []:
        shas = s.get("shas") or {}
        lines.append(f"- `{s.get('run_id')}` — {s.get('disposition') or s.get('reason')}")
        for k in ("report.json", "report.md", "audit.xlsx"):
            if k in shas:
                lines.append(f"  - {k}: `{shas[k]}`")
    lines += ["", "## Quality layers", ""]
    base = report.get("base") or {}
    ql = base.get("quality_layers") or {}
    for k in ("CORE_VALID", "PARTIAL_VALID_WINDOW", "STRESS_RECOVERABLE", "ALL_USABLE", "INVALID_SOURCE"):
        row = ql.get(k) or base.get(k) or {}
        lines.append(f"- {k}: status={row.get('status')} trades={row.get('trades_n')} pnl={row.get('pnl')}")
    lines.append(f"- ALL_USABLE trades: {base.get('ALL_USABLE_trades_n')} pnl={base.get('ALL_USABLE_pnl')}")
    lines += ["", "## Folds (portfolio confirm)", ""]
    for fid, f in (report.get("folds") or {}).items():
        sel = (f.get("selected_candidate") or {}).get("candidate_id")
        cp = f.get("confirm_portfolio") or {}
        lines.append(
            f"- {fid}: {f.get('status')} selected={sel} "
            f"trades={cp.get('completed_trades')} pnl={cp.get('pnl')}"
        )
    er = report.get("entry_robustness") or {}
    if er:
        lines += ["", "## Entry robustness", ""]
        lines.append(f"- verdict: {er.get('verdict')}")
        lines.append(f"- NEXT_PHASE: {er.get('NEXT_PHASE')}")
        fc = er.get("final_candidate") or {}
        lines.append(f"- final_candidate: {fc.get('candidate_id')}")
    lines += ["", "## Determinism", ""]
    det = report.get("determinism") or {}
    lines.append(f"- match: {det.get('match')}")
    lines.append(f"- dataset_sha A/B: `{det.get('dataset_sha_a')}` / `{det.get('dataset_sha_b')}`")
    lines += ["", "## Safety", ""]
    safety = report.get("safety") or {}
    lines.append(f"- submit/cancel/live = {safety.get('submit', 0)}/{safety.get('cancel', 0)}/{safety.get('live', 0)}")
    lines += ["", "## Next single step", "", str(report.get("next_single_step") or ""), ""]
    lines += ["", f"**{banner}**", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _append_index(idx, sheet: str, rows: int, sha_or_note: str, *, range_note: str = "") -> None:
    idx.append([sheet, rows, sha_or_note, range_note])


def _excel_cell(value: Any) -> Any:
    """Coerce values openpyxl cannot store (dict/list/tuple/set) to stable JSON text."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _sheet_from_rows(wb, name: str, headers: Sequence[str], rows: Sequence[Sequence[Any]]):
    # Excel sheet name max 31 chars
    safe = name[:31]
    ws = wb.create_sheet(safe)
    ws.append([_excel_cell(h) for h in headers])
    for r in rows:
        ws.append([_excel_cell(c) for c in r])
    return ws


def _write_audit_xlsx(report: dict[str, Any], path: Path, work: Path) -> dict[str, Any]:
    """Full ledgers in-sheet when within Excel limits; deterministic splits otherwise.

    Never treat a 5k sample as the complete ledger.
    """
    try:
        from openpyxl import Workbook
    except ImportError as e:
        raise RuntimeError("openpyxl required for audit.xlsx") from e

    banner = _banner_of(report)
    wb = Workbook()
    idx = wb.active
    idx.title = "Index"
    idx.append(["sheet", "rows", "sha256_or_note", "row_range"])
    counts: dict[str, int] = {}

    _append_index(idx, "report_run_id", 1, _run_id_of(report))
    _append_index(idx, "banner", 1, banner)
    _append_index(idx, "p1_lock_sha256", 1, str((report.get("p1") or {}).get("p1_lock_sha256") or ""))
    _append_index(
        idx,
        "source_manifest_sha256",
        1,
        str((report.get("source_manifest") or {}).get("source_manifest_sha256") or ""),
    )

    # Summary
    st = report.get("status") or {}
    srows = [
        ["banner", banner],
        ["run_id", _run_id_of(report)],
        ["P0", st.get("P0")],
        ["P1", st.get("P1")],
        ["P2", st.get("P2")],
        ["verdict", report.get("verdict") or st.get("verdict")],
        ["NEXT_PHASE", report.get("NEXT_PHASE") or st.get("NEXT_PHASE")],
        ["determinism_match", (report.get("determinism") or {}).get("match")],
        ["safety_submit", (report.get("safety") or {}).get("submit", 0)],
        ["safety_cancel", (report.get("safety") or {}).get("cancel", 0)],
        ["safety_live", (report.get("safety") or {}).get("live", 0)],
    ]
    _sheet_from_rows(wb, "Summary", ["key", "value"], srows)
    _append_index(idx, "Summary", len(srows), sha256_obj(srows), range_note=f"1..{len(srows)}")
    counts["Summary"] = len(srows)

    # SourceWindows
    windows = (report.get("source_manifest") or {}).get("windows") or []
    headers = [
        "day",
        "am_pm",
        "selected_session_id",
        "quality_class",
        "research_label",
        "seal_pass",
        "coverage",
        "has_usable_overlap",
        "session_raw_event_count",
        "window_raw_event_count",
        "normalized_event_count",
        "universe_count",
        "universe_sha256",
        "analysis_mask_id",
        "include_in_core_base",
        "exclusion_reasons",
        "entry_block_reason",
    ]
    rows = [
            [
                w.get("day"),
                w.get("am_pm"),
                w.get("selected_session_id"),
                w.get("quality_class"),
                w.get("research_label"),
                w.get("seal_pass"),
                w.get("coverage"),
            w.get("has_usable_overlap"),
            w.get("session_raw_event_count", w.get("raw_event_count")),
            w.get("window_raw_event_count", "UNKNOWN"),
            w.get("normalized_event_count", "UNKNOWN"),
                w.get("universe_count"),
                w.get("universe_sha256"),
                w.get("analysis_mask_id"),
            w.get("include_in_core_base"),
                "|".join(w.get("exclusion_reasons") or []),
                w.get("entry_block_reason"),
            ]
        for w in windows
    ]
    _sheet_from_rows(wb, "SourceWindows", headers, rows)
    _append_index(idx, "SourceWindows", len(rows), sha256_obj(rows), range_note=f"1..{len(rows)}")
    counts["SourceWindows"] = len(rows)

    # DataQuality
    qwc = (report.get("source_manifest") or {}).get("quality_window_counts") or {}
    dq_rows = [[k, v] for k, v in sorted(qwc.items())]
    _sheet_from_rows(wb, "DataQuality", ["quality_class", "window_count"], dq_rows)
    _append_index(idx, "DataQuality", len(dq_rows), sha256_obj(dq_rows), range_note=f"1..{len(dq_rows)}")
    counts["DataQuality"] = len(dq_rows)

    # QualityLayerSummary
    ql = ((report.get("base") or {}).get("quality_layers")) or {}
    qrows = []
    for k in ("CORE_VALID", "PARTIAL_VALID_WINDOW", "STRESS_RECOVERABLE", "ALL_USABLE", "INVALID_SOURCE"):
        row = ql.get(k) or {}
        qrows.append(
            [
                k,
                row.get("status"),
                row.get("trades_n"),
                row.get("pnl"),
                row.get("reason"),
                row.get("include_in_core_base"),
            ]
        )
    _sheet_from_rows(
        wb,
        "QualityLayerSummary",
        ["layer", "status", "trades_n", "pnl", "reason", "include_in_core_base"],
        qrows,
    )
    _append_index(idx, "QualityLayerSummary", len(qrows), sha256_obj(qrows))
    counts["QualityLayerSummary"] = len(qrows)

    # EvaluationRows / Labels — full labeled dataset (split if needed)
    ds_dir = work / "run_a" / "labels"
    eval_cols = [
                "day",
                "am_pm",
                "symbol_norm",
                "decision_time",
                "score",
                "spread_bps",
                "score_vs_threshold_gap",
                "mid",
                "sample_reason",
                "x5_accept",
                "post_5bps_expectancy_h300",
                "censor_reason",
        "yen_roundtrip_cost_at_mid",
                "MISSED_WINNER",
                "UNNECESSARY_ENTRY",
        "window_id",
        "analysis_mask_id",
        "replay_partition_id",
        "quality_class",
        "valid_window_start",
        "valid_window_end",
        "in_analysis_mask",
        "event_scope",
    ]
    all_eval: list[list[Any]] = []
    if ds_dir.is_dir():
        for day_file in sorted(ds_dir.glob("*_labeled.jsonl")):
            with open(day_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    all_eval.append([r.get(c) for c in eval_cols])
    report_eval_n = int(((report.get("dataset") or {}).get("rows")) or ((report.get("labels") or {}).get("rows")) or 0)
    sheet_i = 1
    offset = 0
    while offset < len(all_eval) or (offset == 0 and not all_eval):
        chunk = all_eval[offset : offset + EXCEL_MAX_DATA_ROWS]
        name = f"EvaluationRows_{sheet_i:03d}" if len(all_eval) > EXCEL_MAX_DATA_ROWS else "EvaluationRows"
        if not all_eval and sheet_i == 1:
            name = "EvaluationRows"
        _sheet_from_rows(wb, name, eval_cols, chunk)
        end = offset + len(chunk)
        _append_index(
            idx,
            name,
            len(chunk),
            sha256_obj(chunk),
            range_note=f"{offset + 1}..{end}" if chunk else "empty",
        )
        counts[name] = len(chunk)
        # Labels sheet mirrors EvaluationRows columns (label-focused alias)
        if name == "EvaluationRows" or name.startswith("EvaluationRows_"):
            lname = name.replace("EvaluationRows", "Labels")
            _sheet_from_rows(wb, lname, eval_cols, chunk)
            _append_index(idx, lname, len(chunk), sha256_obj(chunk), range_note=f"{offset + 1}..{end}" if chunk else "empty")
            counts[lname] = len(chunk)
        sheet_i += 1
        offset = end
        if not all_eval:
            break
    counts["EvaluationRows_total"] = len(all_eval)
    counts["EvaluationRows_report_json"] = report_eval_n

    # CandidateRegistry — full 200 (primary) + per-fold registries
    reg_path = work / "run_a" / "candidates" / "registry.json"
    cands = []
    if reg_path.is_file():
        cands = json.loads(reg_path.read_text(encoding="utf-8"))
    if not cands:
        final_reg = work / "run_a" / "candidates" / "registry_final.json"
        if final_reg.is_file():
            cands = json.loads(final_reg.read_text(encoding="utf-8"))
    if not cands:
        cands = (report.get("candidates") or {}).get("rows") or []
    crow_headers = [
        "candidate_id",
        "family",
        "features",
        "direction",
        "threshold_code",
        "build_support",
        "build_expectancy_proxy",
    ]
    crows = [
        [
            c.get("candidate_id"),
            c.get("family"),
            ",".join(c.get("features") or []),
            c.get("direction"),
            c.get("threshold_code"),
            c.get("build_support"),
            c.get("build_expectancy_proxy"),
        ]
        for c in cands[:CANDIDATE_CAP]
    ]
    _sheet_from_rows(wb, "CandidateRegistry", crow_headers, crows)
    # Index uses CandidateRegistry SoT SHA (full registry object), not truncated-row digest
    reg_sot_sha = (report.get("candidates") or {}).get("registry_sha256") or sha256_obj(cands[:CANDIDATE_CAP])
    _append_index(
        idx,
        "CandidateRegistry",
        len(crows),
        reg_sot_sha,
        range_note=f"1..{len(crows)}; sot_sha=report.candidates.registry_sha256",
    )
    counts["CandidateRegistry"] = len(crows)
    counts["CandidateRegistry_sot_sha256"] = reg_sot_sha

    for fid in ("F1", "F2", "F3", "F4", "F5"):
        fold_reg_p = work / "run_a" / "folds" / fid / "candidate_registry.json"
        if not fold_reg_p.is_file():
            continue
        fcands = json.loads(fold_reg_p.read_text(encoding="utf-8"))
        frows = [
            [
                c.get("candidate_id"),
                c.get("family"),
                ",".join(c.get("features") or []),
                c.get("direction"),
                c.get("threshold_code"),
                c.get("build_support"),
                c.get("build_expectancy_proxy"),
            ]
            for c in fcands[:CANDIDATE_CAP]
        ]
        sname = f"FoldReg_{fid}"
        _sheet_from_rows(wb, sname, crow_headers, frows)
        _append_index(idx, sname, len(frows), sha256_obj(frows), range_note=f"1..{len(frows)}")
        counts[sname] = len(frows)

    # BaselineTrades (alias BaseTradeLedger)
    base_dir = work / "run_a" / "base"
    base_trades: list[dict[str, Any]] = []
    if base_dir.is_dir():
        for p in sorted(base_dir.glob("*_trades.json")):
            base_trades.extend(json.loads(p.read_text(encoding="utf-8")))
    b_headers = [
        "day",
        "am_pm",
        "symbol",
        "entry_time",
        "exit_time",
        "exit_reason",
        "entry_ask",
        "exit_bid",
        "net_pnl_yen_100",
        "gross_pnl_yen_100",
        "net_bps",
        "cost_yen_100",
        "holding_sec",
        "window_id",
        "analysis_mask_id",
        "replay_partition_id",
        "quality_class",
        "valid_window_start",
        "valid_window_end",
        "in_analysis_mask",
        "event_scope",
    ]
    brows = [
        [
            t.get("day"),
            t.get("am_pm"),
            t.get("symbol"),
            t.get("entry_time"),
            t.get("exit_time"),
            t.get("exit_reason"),
            t.get("entry_ask"),
            t.get("exit_bid"),
            t.get("net_pnl_yen_100"),
            t.get("gross_pnl_yen_100"),
            t.get("net_bps"),
            t.get("cost_yen_100"),
            t.get("holding_sec"),
            t.get("window_id"),
            t.get("analysis_mask_id"),
            t.get("replay_partition_id"),
            t.get("quality_class"),
            t.get("valid_window_start"),
            t.get("valid_window_end"),
            t.get("in_analysis_mask"),
            t.get("event_scope"),
        ]
        for t in base_trades
    ]
    _sheet_from_rows(wb, "BaselineTrades", b_headers, brows)
    _append_index(idx, "BaselineTrades", len(brows), sha256_obj(brows), range_note=f"1..{len(brows)}")
    counts["BaselineTrades"] = len(brows)

    # FoldSummary + per-fold portfolio ledgers
    fs_headers = [
        "fold",
        "status",
        "selected_candidate_id",
        "completed_trades",
        "pnl",
        "pf",
        "wins",
        "losses",
        "draws",
        "max_dd",
        "cap_blocked",
        "duplicate_open_symbol_reject",
        "open_at_end_n",
        "open_at_end_symbols",
        "analysis_mask_ids",
        "signal_ledger_sha256",
        "portfolio_decision_ledger_sha256",
        "completed_trade_ledger_sha256",
        "X5_KEEP",
        "X5_REMOVED",
        "X6_ADDED",
        "BOTH_REJECT",
    ]
    fs_rows = []
    for fid, f in (report.get("folds") or {}).items():
        cp = f.get("confirm_portfolio") or {}
        na = cp.get("noise_audit") or {}
        fs_rows.append(
            [
                fid,
                f.get("status"),
                (f.get("selected_candidate") or {}).get("candidate_id"),
                cp.get("completed_trades"),
                cp.get("pnl"),
                cp.get("pf"),
                cp.get("wins"),
                cp.get("losses"),
                cp.get("draws"),
                cp.get("max_dd"),
                cp.get("cap_blocked"),
                cp.get("duplicate_open_symbol_reject"),
                cp.get("open_at_end_n"),
                ",".join(cp.get("open_at_end_symbols") or []),
                "|".join(f.get("analysis_mask_ids") or []),
                cp.get("signal_ledger_sha256"),
                cp.get("portfolio_decision_ledger_sha256"),
                cp.get("completed_trade_ledger_sha256"),
                na.get("X5_KEEP"),
                na.get("X5_REMOVED"),
                na.get("X6_ADDED"),
                na.get("BOTH_REJECT"),
            ]
        )
        fold_dir = work / "run_a" / "folds" / fid
        decisions = cp.get("decision_ledger")
        if decisions is None and (fold_dir / "decision_ledger.json").is_file():
            decisions = json.loads((fold_dir / "decision_ledger.json").read_text(encoding="utf-8"))
        trades = cp.get("completed_trades_detail")
        if trades is None and (fold_dir / "completed_trades.json").is_file():
            trades = json.loads((fold_dir / "completed_trades.json").read_text(encoding="utf-8"))
        signals = None
        if (fold_dir / "signal_ledger.json").is_file():
            signals = json.loads((fold_dir / "signal_ledger.json").read_text(encoding="utf-8"))

        drows = [
            [
                d.get("ts"),
                d.get("symbol"),
                d.get("decision"),
                d.get("reason"),
                d.get("event_id"),
                d.get("day"),
                d.get("am_pm"),
                d.get("session_id"),
                d.get("window_id"),
                d.get("analysis_mask_id"),
                d.get("quality_class"),
                d.get("valid_window_start"),
                d.get("valid_window_end"),
                d.get("event_scope"),
                d.get("in_analysis_mask_decision"),
                d.get("in_analysis_mask_entry"),
                d.get("in_analysis_mask_exit"),
            ]
            for d in (decisions or [])
        ]
        d_headers = [
            "ts",
            "symbol",
            "decision",
            "reason",
            "event_id",
            "day",
            "am_pm",
            "session_id",
            "window_id",
            "analysis_mask_id",
            "quality_class",
            "valid_window_start",
            "valid_window_end",
            "event_scope",
            "in_analysis_mask_decision",
            "in_analysis_mask_entry",
            "in_analysis_mask_exit",
        ]
        sheet_name = f"FoldPort_{fid}"
        _sheet_from_rows(wb, sheet_name, d_headers, drows)
        _append_index(idx, sheet_name, len(drows), sha256_obj(drows), range_note=f"1..{len(drows)}")
        counts[sheet_name] = len(drows)

        # PortfolioDecisionLedger alias
        pd_name = f"PortDec_{fid}"
        _sheet_from_rows(wb, pd_name, d_headers, drows)
        _append_index(idx, pd_name, len(drows), sha256_obj(drows), range_note=f"1..{len(drows)}")
        counts[pd_name] = len(drows)

        trows = [
            [
                t.get("symbol"),
                t.get("entry_time"),
                t.get("exit_time"),
                t.get("exit_reason"),
                t.get("net_pnl_yen_100"),
                t.get("cost_yen_100"),
                t.get("day"),
                t.get("am_pm"),
                t.get("session_id"),
                t.get("window_id"),
                t.get("analysis_mask_id"),
                t.get("quality_class"),
                t.get("valid_window_start"),
                t.get("valid_window_end"),
                t.get("event_scope"),
                t.get("in_analysis_mask_entry"),
                t.get("in_analysis_mask_exit"),
                json.dumps(t.get("entry_lineage") or {}, ensure_ascii=False, default=str),
                json.dumps(t.get("exit_lineage") or {}, ensure_ascii=False, default=str),
            ]
            for t in (trades or [])
        ]
        tname = f"FoldTrades_{fid}"
        _sheet_from_rows(
            wb,
            tname,
            [
                "symbol",
                "entry_time",
                "exit_time",
                "exit_reason",
                "net_pnl_yen_100",
                "cost_yen_100",
                "day",
                "am_pm",
                "session_id",
                "window_id",
                "analysis_mask_id",
                "quality_class",
                "valid_window_start",
                "valid_window_end",
                "event_scope",
                "in_analysis_mask_entry",
                "in_analysis_mask_exit",
                "entry_lineage",
                "exit_lineage",
            ],
            trows,
        )
        _append_index(idx, tname, len(trows), sha256_obj(trows), range_note=f"1..{len(trows)}")
        counts[tname] = len(trows)

        if signals is None:
            signals = cp.get("signal_ledger")
        srows_sig = [
            [
                s.get("ts"),
                s.get("symbol"),
                s.get("signal"),
                s.get("event_id"),
                s.get("day"),
                s.get("am_pm"),
                s.get("session_id"),
                s.get("window_id"),
                s.get("analysis_mask_id"),
                s.get("quality_class"),
                s.get("valid_window_start"),
                s.get("valid_window_end"),
                s.get("event_scope"),
                s.get("in_analysis_mask_signal"),
                s.get("in_analysis_mask_entry"),
            ]
            for s in (signals or [])
        ]
        sname = f"SigLed_{fid}"
        _sheet_from_rows(
            wb,
            sname,
            [
                "ts",
                "symbol",
                "signal",
                "event_id",
                "day",
                "am_pm",
                "session_id",
                "window_id",
                "analysis_mask_id",
                "quality_class",
                "valid_window_start",
                "valid_window_end",
                "event_scope",
                "in_analysis_mask_signal",
                "in_analysis_mask_entry",
            ],
            srows_sig,
        )
        _append_index(idx, sname, len(srows_sig), sha256_obj(srows_sig), range_note=f"1..{len(srows_sig)}")
        counts[sname] = len(srows_sig)
        if (trades or decisions) and not srows_sig:
            raise AssertionError(
                f"SIGNAL_LEDGER_EMPTY_WITH_DECISIONS_OR_TRADES fold={fid} "
                f"trades={len(trades or [])} decisions={len(decisions or [])}"
            )

    _sheet_from_rows(wb, "FoldSummary", fs_headers, fs_rows)
    _append_index(idx, "FoldSummary", len(fs_rows), sha256_obj(fs_rows))
    counts["FoldSummary"] = len(fs_rows)

    # FinalCandidate — include ledger SHAs
    er = report.get("entry_robustness") or {}
    fc = er.get("final_candidate") or {}
    fc_rows = [
        [k, json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v]
        for k, v in fc.items()
    ]
    for sha_key in (
        "decision_ledger_sha256",
        "completed_trade_ledger_sha256",
    ):
        if sha_key in fc and not any(r[0] == sha_key for r in fc_rows):
            fc_rows.append([sha_key, fc.get(sha_key)])
    if er.get("final_candidate_decision_ledger_sha256") and not any(
        r[0] == "decision_ledger_sha256" for r in fc_rows
    ):
        fc_rows.append(
            ["decision_ledger_sha256", er.get("final_candidate_decision_ledger_sha256")]
        )
    if er.get("final_candidate_trade_ledger_sha256") and not any(
        r[0] == "completed_trade_ledger_sha256" for r in fc_rows
    ):
        fc_rows.append(
            ["completed_trade_ledger_sha256", er.get("final_candidate_trade_ledger_sha256")]
        )
    if not fc_rows:
        fc_rows = [["status", "NO_FINAL_CANDIDATE"]]
    _sheet_from_rows(wb, "FinalCandidate", ["key", "value"], fc_rows)
    _append_index(idx, "FinalCandidate", len(fc_rows), sha256_obj(fc_rows))
    counts["FinalCandidate"] = len(fc_rows)

    # FinalCandidate ledgers — full rows (incl. SignalLedger)
    er_dir = work / "run_a" / "entry_robustness"
    for label, fname in (
        ("FinalCandTrades", "final_completed_trades.json"),
        ("FinalCandDecisions", "final_decision_ledger.json"),
        ("FinalCandSignals", "final_signal_ledger.json"),
        ("FinalCandCensored", "final_censored_ledger.json"),
    ):
        fp = er_dir / fname
        rows_fc: list[list[Any]] = []
        headers_fc: list[str] = ["raw_json"]
        if fp.is_file():
            data = json.loads(fp.read_text(encoding="utf-8"))
            if data and isinstance(data, list) and isinstance(data[0], dict):
                headers_fc = sorted({k for r in data for k in r.keys()})
                rows_fc = [[r.get(h) for h in headers_fc] for r in data]
            else:
                rows_fc = [[json.dumps(data, ensure_ascii=False, default=str)]]
        _sheet_from_rows(wb, label, headers_fc, rows_fc)
        _append_index(idx, label, len(rows_fc), sha256_obj(rows_fc), range_note=f"1..{len(rows_fc)}")
        counts[label] = len(rows_fc)
    # Vacuous SignalLedger forbid for final
    fdec_n = counts.get("FinalCandDecisions") or 0
    ftr_n = counts.get("FinalCandTrades") or 0
    fsig_n = counts.get("FinalCandSignals") or 0
    if (fdec_n or ftr_n) and not fsig_n:
        raise AssertionError(
            f"SIGNAL_LEDGER_EMPTY_WITH_DECISIONS_OR_TRADES final "
            f"trades={ftr_n} decisions={fdec_n} signals={fsig_n}"
        )

    # FixedSpecDayDeletion (additive ledger filter)
    fixed = (er.get("fixed_spec_day_deletion") or {}).get("rows") or []
    fx_headers = [
        "held_out_day",
        "n_all",
        "n_day",
        "n_without",
        "pnl_all",
        "pnl_day",
        "pnl_without",
        "completed_trades",
        "pnl",
        "pf",
        "residual_ledger_sha256",
        "additivity_ok",
        "pass",
        "status",
    ]
    fx_rows = [
        [
            r.get("held_out_day"),
            r.get("n_all"),
            r.get("n_day"),
            r.get("n_without"),
            r.get("pnl_all"),
            r.get("pnl_day"),
            r.get("pnl_without"),
            r.get("completed_trades"),
            r.get("pnl"),
            r.get("pf"),
            r.get("residual_ledger_sha256"),
            r.get("additivity_ok"),
            r.get("pass"),
            r.get("status"),
        ]
        for r in fixed
    ]
    _sheet_from_rows(wb, "FixedSpecDayDeletion", fx_headers, fx_rows)
    _append_index(idx, "FixedSpecDayDeletion", len(fx_rows), sha256_obj(fx_rows), range_note=f"1..{len(fx_rows)}")
    counts["FixedSpecDayDeletion"] = len(fx_rows)

    # RefitLODO summary + per-held-out ledger SHAs
    lodo = (er.get("refit_lodo") or {}).get("rows") or []
    lo_headers = [
        "held_out_day",
        "refit_candidate_id",
        "refit_family",
        "refit_direction",
        "same_candidate_id",
        "same_family_direction",
        "held_out_pnl",
        "held_out_trades",
        "held_out_pf",
        "registry_sha256",
        "selected_spec_sha256",
        "signal_ledger_sha256",
        "decision_ledger_sha256",
        "completed_trade_ledger_sha256",
        "status",
    ]
    lo_rows = [
        [
            r.get("held_out_day"),
            r.get("refit_candidate_id"),
            r.get("refit_family"),
            r.get("refit_direction"),
            r.get("same_candidate_id"),
            r.get("same_family_direction"),
            r.get("held_out_pnl"),
            r.get("held_out_trades"),
            r.get("held_out_pf"),
            r.get("registry_sha256"),
            r.get("selected_spec_sha256"),
            r.get("signal_ledger_sha256"),
            r.get("decision_ledger_sha256"),
            r.get("completed_trade_ledger_sha256"),
            r.get("status"),
        ]
        for r in lodo
    ]
    _sheet_from_rows(wb, "RefitLODO", lo_headers, lo_rows)
    _append_index(idx, "RefitLODO", len(lo_rows), sha256_obj(lo_rows), range_note=f"1..{len(lo_rows)}")
    counts["RefitLODO"] = len(lo_rows)

    # Per held-out LODO ledgers (compact Index pointers; full rows when present)
    for r in lodo:
        held = str(r.get("held_out_day") or "")
        if not held:
            continue
        ldir = er_dir / "lodo" / held
        for label_suffix, fname in (
            ("Trades", "completed_trades.json"),
            ("Decisions", "decision_ledger.json"),
            ("Signals", "signal_ledger.json"),
        ):
            label = f"LODO_{held[-4:]}_{label_suffix}"[:31]
            fp = ldir / fname
            rows_l: list[list[Any]] = []
            headers_l: list[str] = ["raw_json"]
            if fp.is_file():
                data = json.loads(fp.read_text(encoding="utf-8"))
                if data and isinstance(data, list) and isinstance(data[0], dict):
                    headers_l = sorted({k for row in data for k in row.keys()})
                    rows_l = [[row.get(h) for h in headers_l] for row in data]
            _sheet_from_rows(wb, label, headers_l, rows_l)
            _append_index(idx, label, len(rows_l), sha256_obj(rows_l), range_note=f"1..{len(rows_l)}")
            counts[label] = len(rows_l)

    # 7/22 exclude — candidate vs BASE namespaces as separate sheets
    cand_ex = er.get("candidate_ex722") or {}
    base_ex = (er.get("ex_20260722_layer_metrics") or {}).get("ALL_USABLE") or {}
    ex_cand_rows = [
        ["namespace", cand_ex.get("namespace") or "candidate_ex722"],
        ["not_base_metrics", cand_ex.get("not_base_metrics")],
        ["n", cand_ex.get("n")],
        ["pnl", cand_ex.get("pnl")],
        ["pf", cand_ex.get("pf")],
        ["pass", cand_ex.get("pass")],
        ["source", cand_ex.get("source")],
    ]
    _sheet_from_rows(wb, "Ex722_Candidate", ["key", "value"], ex_cand_rows)
    _append_index(idx, "Ex722_Candidate", len(ex_cand_rows), sha256_obj(ex_cand_rows))
    counts["Ex722_Candidate"] = len(ex_cand_rows)
    bm = base_ex.get("metrics") or {}
    ex_base_rows = [
        ["namespace", "base_ex722_ALL_USABLE"],
        ["trades_n", base_ex.get("trades_n")],
        ["pnl", bm.get("pnl")],
        ["pf", bm.get("pf")],
        ["status", base_ex.get("status")],
    ]
    _sheet_from_rows(wb, "Ex722_BASE", ["key", "value"], ex_base_rows)
    _append_index(idx, "Ex722_BASE", len(ex_base_rows), sha256_obj(ex_base_rows))
    counts["Ex722_BASE"] = len(ex_base_rows)

    # AM_PM
    ampm_rows = [
        [w.get("day"), w.get("am_pm"), w.get("quality_class"), w.get("coverage"), w.get("has_usable_overlap")]
        for w in windows
    ]
    _sheet_from_rows(wb, "AM_PM", ["day", "am_pm", "quality_class", "coverage", "has_usable_overlap"], ampm_rows)
    _append_index(idx, "AM_PM", len(ampm_rows), sha256_obj(ampm_rows))
    counts["AM_PM"] = len(ampm_rows)

    # Symbols — universe per day
    sym_rows = []
    for w in windows:
        if w.get("am_pm") != "AM":
            continue
        for s in w.get("universe_symbols") or []:
            sym_rows.append([w.get("day"), s, w.get("universe_sha256")])
    _sheet_from_rows(wb, "Symbols", ["day", "symbol", "universe_sha256"], sym_rows)
    _append_index(idx, "Symbols", len(sym_rows), sha256_obj(sym_rows), range_note=f"1..{len(sym_rows)}")
    counts["Symbols"] = len(sym_rows)

    # Exits — BASE exit reason counts
    from collections import Counter

    exit_c = Counter(str(t.get("exit_reason") or "") for t in base_trades)
    ex_rows = [[k, v] for k, v in sorted(exit_c.items())]
    _sheet_from_rows(wb, "Exits", ["exit_reason", "count"], ex_rows)
    _append_index(idx, "Exits", len(ex_rows), sha256_obj(ex_rows))
    counts["Exits"] = len(ex_rows)

    # Concentration
    conc = er.get("concentration") or (er.get("gates") or {}).get("concentration") or {}
    conc_rows = [[k, v] for k, v in sorted(conc.items()) if k != "pass"]
    if not conc_rows:
        conc_rows = [["status", "NOT_EVALUABLE"]]
    _sheet_from_rows(wb, "Concentration", ["metric", "value"], conc_rows)
    _append_index(idx, "Concentration", len(conc_rows), sha256_obj(conc_rows))
    counts["Concentration"] = len(conc_rows)

    # AcceptanceGates
    gates = ((report.get("p1") or {}).get("acceptance_gates_11_1")) or []
    grows = [[g.get("gate"), g.get("condition")] for g in gates]
    # Attach evaluated gate pass flags when present
    eg = (er.get("gates") or {})
    for gname, gval in eg.items():
        if isinstance(gval, dict) and "pass" in gval:
            grows.append([f"evaluated:{gname}", f"pass={gval.get('pass')}"])
    _sheet_from_rows(wb, "AcceptanceGates", ["gate", "condition"], grows)
    _append_index(idx, "AcceptanceGates", len(grows), sha256_obj(grows))
    counts["AcceptanceGates"] = len(grows)

    # Parity / Determinism
    det = report.get("determinism") or {}
    parity_rows = [[k, v] for k, v in sorted(det.items())]
    _sheet_from_rows(wb, "Parity", ["key", "value"], parity_rows)
    _append_index(idx, "Parity", len(parity_rows), sha256_obj(parity_rows))
    counts["Parity"] = len(parity_rows)

    # Tests — ALL fixture tests (name, expected, actual, PASS/FAIL, optional code sha)
    tests = report.get("tests")
    if isinstance(tests, list) and tests:
        test_rows = [
            [
                t.get("test_name") or t.get("name"),
                t.get("expected") if "expected" in t else t.get("assertion"),
                t.get("actual") if "actual" in t else t.get("count"),
                t.get("result") or t.get("PASS_FAIL") or t.get("pass"),
                t.get("code_sha"),
            ]
            for t in tests
        ]
        test_headers = ["test_name", "expected", "actual", "PASS_FAIL", "code_sha"]
    elif isinstance(tests, dict):
        test_rows = [[k, v, None, None, None] for k, v in sorted(tests.items())]
        test_headers = ["test_name", "expected", "actual", "PASS_FAIL", "code_sha"]
    else:
        test_rows = [["fixture_contracts", "see pytest", None, "N/A", None]]
        test_headers = ["test_name", "expected", "actual", "PASS_FAIL", "code_sha"]
    _sheet_from_rows(wb, "Tests", test_headers, test_rows)
    _append_index(idx, "Tests", len(test_rows), sha256_obj(test_rows))
    counts["Tests"] = len(test_rows)

    # Safety
    safety = report.get("safety") or {"submit": 0, "cancel": 0, "live": 0}
    safety_rows = [[k, safety.get(k, 0)] for k in ("submit", "cancel", "live")]
    _sheet_from_rows(wb, "Safety", ["action", "count"], safety_rows)
    _append_index(idx, "Safety", len(safety_rows), sha256_obj(safety_rows))
    counts["Safety"] = len(safety_rows)

    # ChangeLog
    superseded = report.get("superseded_runs") or []
    prior_id = superseded[0].get("run_id") if superseded else None
    cl_rows = [
        ["banner", banner],
        ["superseded_prior", prior_id],
        ["DAYS", "20260721-20260731"],
        ["F5", "enabled"],
        ["note", "FINAL_9DAY publish; no EXIT/Forward/Runtime"],
    ]
    _sheet_from_rows(wb, "ChangeLog", ["key", "value"], cl_rows)
    _append_index(idx, "ChangeLog", len(cl_rows), sha256_obj(cl_rows))
    counts["ChangeLog"] = len(cl_rows)

    wb.save(path)
    return counts


def publish_artifacts(
    report: dict[str, Any],
    *,
    run_id: str,
    banner: Optional[str] = None,
) -> dict[str, Any]:
    use_banner = _banner_of(report, banner)
    progress(f"Publish: staging report.json / report.md / audit.xlsx banner={use_banner}")
    work = temp_work_root(run_id)
    staging = work / "publish_staging"
    staging.mkdir(parents=True, exist_ok=True)
    # Ensure banner stamped on report before write
    report = dict(report)
    report["banner"] = use_banner
    if use_banner == FINAL_BANNER:
        # Strip provisional selection banner from final outputs
        report.pop("PROVISIONAL_NOT_FOR_SELECTION", None)
        report["FINAL_9DAY_INTERNAL_RESEARCH_NOT_FORWARD"] = True
    write_json(staging / "report.json", report)
    _write_report_md(report, staging / "report.md", banner=use_banner)
    audit_counts = _write_audit_xlsx(report, staging / "audit.xlsx", work)
    report["audit_sheet_counts"] = audit_counts

    # Final banner: refuse publish if provisional selection string leaked
    if use_banner == FINAL_BANNER:
        for name in ("report.json", "report.md"):
            text = (staging / name).read_text(encoding="utf-8")
            if PROVISIONAL_BANNER in text:
                raise RuntimeError(
                    f"FINAL publish blocked: {PROVISIONAL_BANNER} found in {name}"
                )

    dest = repo_root() / ARTIFACT_DIR_REL
    atomic_replace_dir_files(staging, dest, ("report.json", "report.md", "audit.xlsx"))
    paths = {
        "report.json": str(dest / "report.json"),
        "report.md": str(dest / "report.md"),
        "audit.xlsx": str(dest / "audit.xlsx"),
    }
    progress(
        "Publish: done "
        + ", ".join(f"{k} sha={sha256_file(Path(v))[:12]}" for k, v in paths.items())
    )
    return {"paths": paths, "audit_sheet_counts": audit_counts}
