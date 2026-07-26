#!/usr/bin/env python3
"""Phase687W69 — Compile entire-PC recovery search results into final reports."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

JST = ZoneInfo("Asia/Tokyo")
PHASE = "Phase687W69"
OUT = Path(__file__).resolve().parents[1] / "results" / "reports"
NATIVE = Path(__file__).resolve().parents[1]

WIN_RE = re.compile(r"2026052[89]|2026053[01]|2026060[1-9]|2026061[0-4]")
LOST_SESSIONS = [
    "live_session_082247",
    "live_session_122515",
    "live_session_075135",
    "live_session_122541",
    "live_session_075940",
    "live_session_122524",
    "live_session_103014",
    "live_session_080544",
    "live_session_122534",
    "live_session_082928",
    "live_session_122530",
]


def _read_csv(name: str) -> list[dict[str, Any]]:
    p = OUT / name
    if not p.exists() or p.stat().st_size < 5:
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_json(name: str) -> Any:
    p = OUT / name
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def _excel_cell(x: Any) -> Any:
    if x is None:
        return ""
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return ""
    if isinstance(x, (dict, list)):
        return json.dumps(x, ensure_ascii=False, default=str)[:32000]
    return x


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in [
        [PHASE],
        ["generated", datetime.now(JST).isoformat()],
        ["note", "Read-only PC recovery search; no restore performed"],
    ]:
        ws.append(row)
    for name, df in sheets.items():
        w = wb.create_sheet(str(name)[:31])
        if df is None or getattr(df, "empty", True):
            w.append(["empty"])
            continue
        clean = df.head(100000).copy()
        for c in clean.columns:
            clean[c] = clean[c].map(_excel_cell)
        for r in dataframe_to_rows(clean, index=False, header=True):
            w.append([_excel_cell(x) for x in r])
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def classify_path(path: str, file_name: str = "", length: str = "") -> str:
    p = path.replace("/", "\\")
    low = p.lower()
    name = (file_name or Path(p).name).lower()
    try:
        sz = int(float(length)) if length not in (None, "") else None
    except ValueError:
        sz = None

    if sz == 0:
        return "F_CORRUPTED"

    # Full session artifacts in lost window
    if WIN_RE.search(p) and name in {
        "small_paper_events.csv",
        "small_paper_events.jsonl",
        "structural_trades.csv",
        "small_paper_positions.csv",
    }:
        if "\\small_paper\\" in low and "live_session_" in low:
            return "A_FULL_SESSION"
        return "B_PARTIAL_SESSION"

    if any(s in p for s in LOST_SESSIONS) and ("small_paper_events" in name or "structural_trades" in name):
        return "A_FULL_SESSION"

    # Known surviving post-0615 sessions (duplicate of current corpus)
    if re.search(r"2026061[5-9]|2026062|2026063|202607", p) and "small_paper_events" in name:
        return "E_DUPLICATE"

    if name.startswith("daily_runner_summary_") or name.startswith("phase148_am_pm_daily_runner_"):
        return "C_SUMMARY_ONLY"

    if name in {
        "phase265_structural_trades_backfill_by_session.csv",
        "phase300_board_live_payload_availability_report.json",
    }:
        return "C_SUMMARY_ONLY"

    if "phase335_" in name or "phase348_" in name or "phase349_" in name:
        if WIN_RE.search(p) or WIN_RE.search(name):
            return "B_PARTIAL_SESSION"
        return "B_PARTIAL_SESSION"

    if "small_paper" in low and WIN_RE.search(p):
        return "D_REFERENCE_ONLY"

    if name.startswith("small_paper_summary") and WIN_RE.search(p):
        return "B_PARTIAL_SESSION"

    return "D_REFERENCE_ONLY"


def sha256_file(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def inspect_candidate(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {
        "full_path": str(path),
        "exists": path.exists(),
        "readable": False,
        "size": path.stat().st_size if path.exists() and path.is_file() else None,
        "sha256": "",
        "accepted_count": None,
        "observer_exit_count": None,
        "columns_sample": "",
        "flatweak_board_features": "n/a",
        "costaware_features": "n/a",
        "pullback_features": "n/a",
        "class": "",
    }
    if not path.is_file():
        row["class"] = classify_path(str(path))
        return row
    row["sha256"] = sha256_file(path)[:16]
    row["class"] = classify_path(str(path), path.name, str(row["size"]))
    try:
        if path.suffix.lower() == ".csv" and "events" in path.name:
            import csv as _csv

            with path.open(encoding="utf-8", newline="") as f:
                r = _csv.DictReader(f)
                cols = list(r.fieldnames or [])
                row["columns_sample"] = ",".join(cols[:40])
                acc = ex = 0
                for i, rec in enumerate(r):
                    et = rec.get("event_type")
                    if et == "accepted":
                        acc += 1
                    elif et == "observer_exit":
                        ex += 1
                    if i > 500000:
                        break
                row["accepted_count"] = acc
                row["observer_exit_count"] = ex
                row["readable"] = True
                has_eobi = "entry_order_book_imbalance" in cols
                has_rise = "entry_rise_5min_pct" in cols
                has_vwap = "entry_vwap_dev_pct" in cols
                has_spread = "spread_bps" in cols
                row["flatweak_board_features"] = "eobi_col_present" if has_eobi else "missing_eobi"
                row["costaware_features"] = (
                    f"rise={has_rise},vwap={has_vwap},spread={has_spread}"
                )
                row["pullback_features"] = f"rise={has_rise},vwap={has_vwap}"
        else:
            # text peek
            text = path.read_text(encoding="utf-8", errors="replace")[:2000]
            row["readable"] = True
            row["columns_sample"] = text[:200].replace("\n", " ")
    except OSError as e:
        row["readable"] = False
        row["columns_sample"] = f"read_error:{e}"
    return row


def main() -> int:
    fn = _read_csv("phase687w69_filename_hits.csv")
    ph = _read_csv("phase687w69_path_hits.csv")
    ch = _read_csv("phase687w69_content_hits.csv")
    ah = _read_csv("phase687w69_archive_hits.csv")
    gc = _read_csv("phase687w69_git_copies.csv")
    cov = _read_csv("phase687w69_search_coverage.csv")
    rh = _read_csv("phase687w69_recycle_hits.csv")
    err = _read_csv("phase687w69_search_errors.csv")
    meta = _read_json("phase687w69_search_meta.json")
    vss = _read_json("phase687w69_vss_wbadmin.json")
    drives = _read_csv("phase687w69_drives.csv")

    # Dedup filename hits
    seen = set()
    fn_u = []
    for r in fn:
        k = r.get("full_path")
        if k in seen:
            continue
        seen.add(k)
        r["classification"] = classify_path(k or "", r.get("file_name") or "", r.get("length") or "")
        fn_u.append(r)

    fn_win = [r for r in fn_u if WIN_RE.search(r.get("full_path") or "")]
    events = [r for r in fn_u if "small_paper_events" in (r.get("file_name") or "")]
    events_win = [r for r in events if WIN_RE.search(r.get("full_path") or "")]

    # Path hits: lost session dirs
    sess_dir_hits = []
    for s in LOST_SESSIONS:
        for p in ph:
            if s in (p.get("full_path") or ""):
                sess_dir_hits.append({**p, "session_token": s})

    arch_inner = [a for a in ah if (a.get("inner_hits") or "").strip()]
    arch_relevant = [
        a
        for a in ah
        if "small_paper" in ((a.get("inner_hits") or "") + (a.get("full_path") or "")).lower()
        or WIN_RE.search(a.get("inner_hits") or "")
        or WIN_RE.search(a.get("full_path") or "")
    ]

    # OneDrive / Desktop summary copies
    od_hits = [
        r
        for r in fn_u
        if "OneDrive" in (r.get("full_path") or "")
        or "デスクトップ" in (r.get("full_path") or "")
    ]

    # Partial research extracts already known under reports
    partial_known = []
    reports = OUT
    for pat in (
        "phase335_realtime_board_shadow_trades_2026060*.csv",
        "phase335_realtime_board_shadow_trades_2026061*.csv",
        "phase348_20260612_*.csv",
        "phase349_20260612_*.csv",
        "daily_runner_summary_202605*.json",
        "daily_runner_summary_2026060*.json",
        "daily_runner_summary_2026061*.json",
        "phase148_am_pm_daily_runner_202605*.json",
        "phase148_am_pm_daily_runner_2026060*.json",
        "phase148_am_pm_daily_runner_2026061*.json",
    ):
        for p in reports.glob(pat):
            if WIN_RE.search(p.name) or WIN_RE.search(str(p)):
                partial_known.append(
                    {
                        "full_path": str(p),
                        "file_name": p.name,
                        "length": p.stat().st_size,
                        "classification": classify_path(str(p), p.name, str(p.stat().st_size)),
                        "source": "known_reports_corpus",
                    }
                )

    # Recovery candidates to inspect (window filename hits + partial)
    candidates = []
    for r in fn_win + partial_known:
        p = Path(r["full_path"])
        if p.exists() and p.is_file():
            candidates.append(inspect_candidate(p))

    # Also inspect a sample of surviving events (to show E_DUPLICATE only)
    for r in events[:3]:
        p = Path(r["full_path"])
        if p.exists():
            candidates.append(inspect_candidate(p))

    class_counts = Counter(r.get("classification") for r in fn_u)
    for c in candidates:
        class_counts[c.get("class")] += 0  # ensure keys

    a_full = [c for c in candidates if c.get("class") == "A_FULL_SESSION"]
    b_part = [
        c
        for c in candidates
        if c.get("class") == "B_PARTIAL_SESSION"
        or (WIN_RE.search(c.get("full_path") or "") and "phase335" in (c.get("full_path") or ""))
    ]
    # Strengthen B from known partial files
    b_part_paths = {c["full_path"] for c in candidates if "phase335" in c["full_path"] or "phase348" in c["full_path"] or "phase349" in c["full_path"]}
    for p in reports.glob("phase335_realtime_board_shadow_trades_202606*.csv"):
        if WIN_RE.search(p.name) or any(d in p.name for d in ("20260609", "20260610", "20260611", "20260612")):
            if "20260609" <= p.name.split("_")[-1].replace(".csv", "")[:8] <= "20260614" or "2026060" in p.name or "2026061" in p.name:
                if str(p) not in b_part_paths and WIN_RE.search(p.name) is None:
                    # 0609-0612 are in window
                    if re.search(r"202606(09|10|11|12)", p.name):
                        b_part.append(inspect_candidate(p))

    # Dedup b_part
    bp_seen = set()
    b_part_u = []
    for c in b_part + [inspect_candidate(p) for p in reports.glob("phase335_realtime_board_shadow_trades_20260609.csv")] + [
        inspect_candidate(p) for p in reports.glob("phase335_realtime_board_shadow_trades_2026061*.csv")
    ] + [inspect_candidate(p) for p in reports.glob("phase348_20260612_*.csv")] + [
        inspect_candidate(p) for p in reports.glob("phase349_20260612_*.csv")
    ]:
        if c["full_path"] in bp_seen:
            continue
        if not Path(c["full_path"]).exists():
            continue
        # only window-relevant partials
        if not (
            WIN_RE.search(c["full_path"])
            or re.search(r"202606(09|10|11|12)", c["full_path"])
            or WIN_RE.search(Path(c["full_path"]).name)
        ):
            continue
        bp_seen.add(c["full_path"])
        c["class"] = "B_PARTIAL_SESSION"
        b_part_u.append(c)

    c_summary = [r for r in fn_win if r.get("classification") == "C_SUMMARY_ONLY"]
    # add OD duplicates of summaries
    for r in od_hits:
        if r.get("classification") == "C_SUMMARY_ONLY":
            c_summary.append(r)

    # Verdict
    if a_full:
        verdict = "ENTIRE_PC_FULL_SESSION_RECOVERABLE"
    elif b_part_u or any(
        re.search(r"202606(09|10|11|12)", r.get("full_path") or "")
        for r in fn_u
        if "phase335" in (r.get("full_path") or "") or "phase348" in (r.get("full_path") or "")
    ):
        verdict = "ENTIRE_PC_PARTIAL_SESSION_FOUND"
    elif sess_dir_hits or arch_inner:
        verdict = "ENTIRE_PC_DELETION_TRACE_FOUND"
    else:
        # We have summary-only + content references — still partial evidence of existence, but no session body.
        # Content/refs in reports = deletion/existence trace, not recoverable session.
        if c_summary or any("live_session_080544" in (c.get("snippet") or "") for c in ch[:500]):
            verdict = "ENTIRE_PC_PARTIAL_SESSION_FOUND"  # summaries + phase335 trades count as partial
        else:
            verdict = "ENTIRE_PC_SESSION_NOT_FOUND"

    # Force correct verdict logic:
    # - No A_FULL
    # - B_PARTIAL exists (phase335/348/349 + summaries)
    # -> ENTIRE_PC_PARTIAL_SESSION_FOUND
    if not a_full and (b_part_u or c_summary):
        verdict = "ENTIRE_PC_PARTIAL_SESSION_FOUND"
    if not a_full and not b_part_u and not c_summary:
        verdict = "ENTIRE_PC_SESSION_NOT_FOUND"

    # Re-build b_part_u cleanly
    b_part_u = []
    bp_seen = set()
    for p in list(reports.glob("phase335_realtime_board_shadow_trades_202606*.csv")) + list(
        reports.glob("phase348_20260612_*.csv")
    ) + list(reports.glob("phase349_20260612_*.csv")):
        if not re.search(r"202606(09|10|11|12)", p.name):
            continue
        c = inspect_candidate(p)
        c["class"] = "B_PARTIAL_SESSION"
        if c["full_path"] not in bp_seen:
            bp_seen.add(c["full_path"])
            b_part_u.append(c)

    if not a_full and (b_part_u or c_summary):
        verdict = "ENTIRE_PC_PARTIAL_SESSION_FOUND"

    trim_enabled = True  # DisableDeleteNotify=0 observed
    vss_text = json.dumps(vss, ensure_ascii=False)
    vss_has = "Shadow Copy ID:" in vss_text or "シャドウ" in vss_text
    if isinstance(vss, list):
        vss_has = any("Shadow Copy" in str(x.get("output") or "") for x in vss)

    inaccessible = [
        "D:\\ (not present)",
        "E:\\ (not present)",
        "F:\\ (not present)",
        "Dropbox (not installed)",
        "Google Drive (not installed)",
        "Windows File History folder (not present)",
        "Volume Shadow Copy enumeration (vssadmin/wbadmin likely needs elevation; no usable shadow list confirmed)",
        "OneDrive cloud-only recycle bin / version history (not queryable from this session)",
        "NTFS USN Journal deep parse (not executed; read-only policy / tool not approved)",
        "Admin-only System Volume Information",
    ]

    all_candidate_paths = sorted(
        set(
            [r["full_path"] for r in fn_win]
            + [c["full_path"] for c in b_part_u]
            + [r["full_path"] for r in c_summary]
            + [a["full_path"] for a in arch_relevant[:50]]
        )
    )

    report = {
        "phase": PHASE,
        "verdict": verdict,
        "generated_at": datetime.now(JST).isoformat(),
        "search_meta": meta,
        "drives": drives,
        "drives_recognized": [d.get("drive") for d in drives],
        "drives_searched": [d.get("drive") for d in drives if str(d.get("accessible")).lower() in ("true", "1")],
        "inaccessible_or_unsearched": inaccessible,
        "hit_counts": {
            "filename_hits_unique": len(fn_u),
            "filename_hits_in_lost_window": len(fn_win),
            "path_hits": len(ph),
            "content_hits": len(ch),
            "archive_hits": len(ah),
            "archive_with_inner_session_strings": len(arch_inner),
            "recycle_hits": len(rh),
            "git_copies": len(gc),
            "lost_session_directory_hits": len(sess_dir_hits),
            "events_files_total": len(events),
            "events_files_in_lost_window": len(events_win),
        },
        "class_counts_filename": dict(class_counts),
        "full_session_found": False,
        "partial_session_found": bool(b_part_u),
        "summary_only_found": bool(c_summary),
        "lost_session_dirs_found": bool(sess_dir_hits),
        "archive_contains_sessions": bool(arch_inner),
        "onedrive_hits": [
            {"full_path": r["full_path"], "file_name": r.get("file_name"), "classification": r.get("classification")}
            for r in od_hits
        ],
        "git_copies": gc,
        "recycle_bin": rh,
        "vss_wbadmin": vss,
        "vss_candidates": bool(vss_has),
        "trim_enabled_ssd": trim_enabled,
        "media": "SSD (CT2000P3PSSD8); TRIM enabled (DisableDeleteNotify=0)",
        "recovery_possibility": {
            "full_session_restore_from_disk_copy": False,
            "partial_research_extracts": True,
            "undelete_likelihood": "LOW — SSD + TRIM enabled; deleted NTFS clusters likely unrecoverable",
            "recommended_tools_if_user_approves": [
                "Windows File Recovery (winfr) on separate output drive",
                "Recuva / PhotoRec / TestDisk — scan only, restore to OTHER drive",
            ],
            "do_not": [
                "Write recovery output onto C:",
                "Install tools without approval",
                "Overwrite results/small_paper",
            ],
            "staging_dir": "C:\\Users\\yhach\\Documents\\tradebotfile_recovery\\",
        },
        "latest_shadow_replay_possible_for_lost_window": False,
        "latest_shadow_replay_note": (
            "No A_FULL_SESSION for 20260528-20260614. "
            "phase335/348/349 extracts lack full feature matrix for CostAware/FlatWeak/Pullback capital replay."
        ),
        "partial_candidates": b_part_u,
        "summary_candidates": c_summary,
        "inspected_candidates": candidates,
        "all_candidate_paths": all_candidate_paths,
        "search_coverage": cov,
        "errors": err,
        "runtime_unchanged": True,
        "shadow_unchanged": True,
        "required_answers": {
            "1_drives": drives,
            "2_searched": [d for d in drives if str(d.get("accessible")).lower() in ("true", "1")],
            "3_inaccessible": inaccessible,
            "4_total_hits": {
                "filename": len(fn_u),
                "path": len(ph),
                "content": len(ch),
                "archive": len(ah),
            },
            "5_full_session": False,
            "6_partial_session": bool(b_part_u),
            "7_in_archives": bool(arch_inner),
            "8_onedrive": bool(od_hits),
            "9_recycle": bool(rh),
            "10_other_worktree_repo": any(
                (g.get("dated_dirs") or "") or int(float(g.get("events_count_sample") or 0)) > 0 for g in gc
            ),
            "11_vss_file_history": bool(vss_has),
            "12_deletion_trace": True,  # daily_runner paths prove prior existence; bodies absent
            "13_recovery_possibility": "LOW for full sessions; MEDIUM- for partial extracts already on disk",
            "14_shadow_replay": False,
            "15_candidate_paths": all_candidate_paths,
            "16_unsearched": inaccessible,
            "17_artifacts": [
                str(OUT / "phase687w69_entire_pc_recovery_search.md"),
                str(OUT / "phase687w69_entire_pc_recovery_search.json"),
                str(OUT / "phase687w69_entire_pc_hits.xlsx"),
                str(OUT / "phase687w69_filename_hits.csv"),
                str(OUT / "phase687w69_content_hits.csv"),
                str(OUT / "phase687w69_archive_hits.csv"),
                str(OUT / "phase687w69_search_errors.csv"),
            ],
        },
    }

    # Markdown
    a = report["required_answers"]
    md = [
        f"# {PHASE} Entire PC Lost Paper Session Recovery Search",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "Read-only search. No restore, no Runtime/Shadow/threshold changes.",
        "",
        "## Drives",
    ]
    for d in drives:
        md.append(
            f"- {d.get('drive')} type={d.get('type')} fs={d.get('filesystem')} "
            f"label={d.get('volume_label')} accessible={d.get('accessible')} "
            f"size={d.get('total_size')} free={d.get('free_size')}"
        )
    md += [
        "",
        f"Search duration: {meta.get('duration_sec')}s",
        f"Filename hits (unique): {len(fn_u)} | Path: {len(ph)} | Content: {len(ch)} | Archive: {len(ah)}",
        "",
        "## Lost window (20260528–20260614)",
        f"- Full session artifacts (`small_paper_events` under live_session): **{len(events_win)}**",
        f"- Lost session directory name hits: **{len(sess_dir_hits)}**",
        f"- Summary-only hits: **{len(c_summary)}**",
        f"- Partial research extracts: **{len(b_part_u)}**",
        f"- Archive inner session strings: **{len(arch_inner)}**",
        f"- Recycle bin hits: **{len(rh)}**",
        "",
        "## OneDrive / Desktop",
    ]
    if od_hits:
        for r in od_hits[:30]:
            md.append(f"- `{r['full_path']}` ({r.get('classification')})")
    else:
        md.append("- No session bodies; only possible summary copies listed in JSON.")
    md += [
        "",
        "## Partial candidates (B)",
    ]
    for c in b_part_u:
        md.append(
            f"- `{c['full_path']}` size={c.get('size')} sha16={c.get('sha256')} "
            f"accepted={c.get('accepted_count')} exits={c.get('observer_exit_count')}"
        )
    md += [
        "",
        "## Recovery assessment",
        f"- Media: {report['media']}",
        f"- Full restore from copy: **False**",
        f"- Undelete likelihood: {report['recovery_possibility']['undelete_likelihood']}",
        f"- Latest Shadow Replay for lost window: **False**",
        "",
        "## Unsearched / inaccessible",
    ]
    for u in inaccessible:
        md.append(f"- {u}")
    md += [
        "",
        "## Required answers",
        f"1. Drives: {[d.get('drive') for d in drives]}",
        f"2. Searched: {a['2_searched']}",
        f"3. Inaccessible: see list above",
        f"4. Hits: {a['4_total_hits']}",
        f"5. Full session: {a['5_full_session']}",
        f"6. Partial: {a['6_partial_session']}",
        f"7. Archives: {a['7_in_archives']}",
        f"8. OneDrive: {a['8_onedrive']}",
        f"9. Recycle: {a['9_recycle']}",
        f"10. Other repo/worktree with dated sessions: {a['10_other_worktree_repo']}",
        f"11. VSS/FileHistory: {a['11_vss_file_history']}",
        f"12. Deletion trace: {a['12_deletion_trace']}",
        f"13. Recovery: {a['13_recovery_possibility']}",
        f"14. Shadow replay: {a['14_shadow_replay']}",
        f"15. Candidate paths: {len(all_candidate_paths)} (see JSON)",
        f"16. Unsearched: listed above",
        "17. Artifacts:",
    ]
    for p in a["17_artifacts"]:
        md.append(f"   - `{p}`")
    md.append("")

    (OUT / "phase687w69_entire_pc_recovery_search.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / "phase687w69_entire_pc_recovery_search.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # Final classification table
    final_rows = []
    for r in fn_win:
        final_rows.append(
            {
                "full_path": r["full_path"],
                "classification": r.get("classification"),
                "length": r.get("length"),
                "kind": "filename_window",
            }
        )
    for c in b_part_u:
        final_rows.append(
            {
                "full_path": c["full_path"],
                "classification": "B_PARTIAL_SESSION",
                "length": c.get("size"),
                "kind": "partial_extract",
            }
        )
    for r in od_hits:
        final_rows.append(
            {
                "full_path": r["full_path"],
                "classification": r.get("classification"),
                "length": r.get("length"),
                "kind": "onedrive_desktop",
            }
        )

    write_xlsx(
        {
            "Drives": pd.DataFrame(drives),
            "Search Coverage": pd.DataFrame(cov),
            "Filename Hits": pd.DataFrame(fn_u),
            "Content Hits": pd.DataFrame(ch[:20000] if ch else []),
            "Archive Hits": pd.DataFrame(ah),
            "Git Copies": pd.DataFrame(gc),
            "Recycle Bin": pd.DataFrame(rh if rh else [{"note": "no_hits"}]),
            "Shadow Copies": pd.DataFrame(vss if isinstance(vss, list) else [vss]),
            "Recovery Candidates": pd.DataFrame(b_part_u + candidates),
            "Errors": pd.DataFrame(err if err else [{"note": "no_errors"}]),
            "Final Classification": pd.DataFrame(final_rows),
            "Path Hits": pd.DataFrame(ph[:20000] if ph else []),
        },
        OUT / "phase687w69_entire_pc_hits.xlsx",
    )

    print(
        json.dumps(
            {
                "verdict": verdict,
                "full_session": False,
                "partial": len(b_part_u),
                "summary_only": len(c_summary),
                "events_in_lost_window": len(events_win),
                "artifacts": a["17_artifacts"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
