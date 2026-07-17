"""Multi-day board ENTRY dataset auto-append (research only).

Eligibility: VALID_SESSION + SEALED_VALID only. AM/PM processed as separate session keys.
Never overwrites prior session rows; never copies/deletes raw Capture.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from research.board_entry_features import (
    compute_entry_feature_rows,
    load_accepted_entries,
    stream_slim_board,
)

log = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")

DISK_WARN_PCT = 75.0
DISK_OPS_WARN_PCT = 85.0

# Frozen relative to Phase687W37 board_entry_dataset_20260716.parquet (+ session meta cols).
SCHEMA_VERSION = "board_entry_dataset_v1_w37"
REQUIRED_META_COLS = [
    "trading_date",
    "session_kind",
    "session_id",
    "symbol",
    "symbol_code",
    "entry_time",
    "route",
    "board_sync_ok",
    "board_sync_lag_sec",
    "sync_clock",
]


def native_root_from(path: Path) -> Path:
    p = path.resolve()
    for cand in [p, *p.parents]:
        if (cand / "src" / "small_paper").is_dir() and (cand / "data").is_dir():
            return cand
    return path


def dataset_root(native_root: Path) -> Path:
    return native_root / "results" / "research" / "board_entry_dataset"


def schema_path(root: Path) -> Path:
    return root / "feature_schema.json"


def manifest_path(root: Path) -> Path:
    return root / "board_entry_dataset_manifest.json"


def summary_csv_path(root: Path) -> Path:
    return root / "board_entry_dataset_summary.csv"


def partition_dir(root: Path, trading_date: str) -> Path:
    return root / f"trading_date={trading_date}"


def disk_usage_pct(path: Path) -> float:
    u = shutil.disk_usage(str(path))
    return 100.0 * float(u.used) / float(u.total) if u.total else 0.0


def load_manifest(root: Path) -> dict[str, Any]:
    p = manifest_path(root)
    if not p.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "sessions": {},
            "trading_dates": [],
            "created_at": datetime.now(JST).isoformat(),
        }
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(root: Path, man: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    man["updated_at"] = datetime.now(JST).isoformat()
    man["schema_version"] = SCHEMA_VERSION
    tmp = root / "board_entry_dataset_manifest.json.tmp"
    tmp.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(manifest_path(root))


def ensure_schema(root: Path, sample_df: Optional[pd.DataFrame] = None) -> dict[str, Any]:
    sp = schema_path(root)
    if sp.is_file():
        return json.loads(sp.read_text(encoding="utf-8"))
    cols = list(sample_df.columns) if sample_df is not None and len(sample_df.columns) else list(REQUIRED_META_COLS)
    schema = {
        "schema_version": SCHEMA_VERSION,
        "grain": "1 ENTRY = 1 row",
        "columns": cols,
        "keys": ["trading_date", "session_kind", "session_id", "symbol_code", "entry_time", "route"],
        "prefixes": [
            "board_5s_",
            "board_15s_",
            "board_30s_",
            "board_60s_",
            "board_120s_",
            "board_300s_",
            "board_at_entry_",
        ],
        "frozen_at": datetime.now(JST).isoformat(),
        "note": "Column set frozen after first successful append; later days must align.",
    }
    root.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return schema


def align_to_schema(df: pd.DataFrame, schema: dict[str, Any]) -> tuple[pd.DataFrame, list[str]]:
    cols = list(schema.get("columns") or [])
    if not cols:
        return df, []
    missing = [c for c in cols if c not in df.columns]
    extra = [c for c in df.columns if c not in cols]
    out = df.copy()
    for c in missing:
        out[c] = pd.NA
    # Keep schema order; drop unexpected extras to keep frozen schema
    out = out.reindex(columns=cols)
    return out, extra


def schema_mismatch(df: pd.DataFrame, schema: dict[str, Any]) -> Optional[str]:
    cols = list(schema.get("columns") or [])
    if not cols:
        return None
    # Allow missing filled with NA; hard-fail if required meta absent from input
    for c in REQUIRED_META_COLS:
        if c not in df.columns and c not in cols:
            return f"missing_required_meta:{c}"
    # If schema exists and df has zero overlap with board features → mismatch
    board_cols = [c for c in cols if c.startswith("board_")]
    if board_cols and not any(c in df.columns for c in board_cols[:10]):
        # empty entry days OK
        if len(df) == 0:
            return None
        return "board_feature_columns_absent"
    return None


def detect_session_meta(session_dir: Path, summary: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    summary = dict(summary or {})
    if not summary and (session_dir / "small_paper_summary.json").is_file():
        summary = json.loads((session_dir / "small_paper_summary.json").read_text(encoding="utf-8"))
    seal = {}
    if (session_dir / "session_seal.json").is_file():
        seal = json.loads((session_dir / "session_seal.json").read_text(encoding="utf-8"))
    cfg = {}
    if (session_dir / "live_session_config.json").is_file():
        cfg = json.loads((session_dir / "live_session_config.json").read_text(encoding="utf-8"))
    am_pm = summary.get("am_pm_session") or cfg.get("am_pm_session") or {}
    kind = str(am_pm.get("kind") or "").lower()
    if kind not in ("am", "pm"):
        # fallback from directory time
        name = session_dir.name
        hh = None
        if name.startswith("live_session_") and len(name) >= 18:
            try:
                hh = int(name.split("_")[2][:2])
            except Exception:
                hh = None
        kind = "am" if hh is not None and hh < 12 else ("pm" if hh is not None else "unknown")
    trading_date = str(
        summary.get("trading_date")
        or cfg.get("trading_date")
        or session_dir.parent.name
    )
    if len(trading_date) != 8 or not trading_date.isdigit():
        trading_date = session_dir.parent.name
    return {
        "trading_date": trading_date,
        "session_kind": kind,
        "session_id": session_dir.name,
        "session_validity": str(summary.get("session_validity") or ""),
        "seal_status": str(seal.get("session_seal_status") or summary.get("session_seal_status") or ""),
        "accepted_count": int(summary.get("accepted_count") or 0),
        "push_messages": int(summary.get("push_messages") or 0),
        "summary": summary,
        "seal": seal,
    }


def session_key(meta: Mapping[str, Any]) -> str:
    return f"{meta['trading_date']}|{meta['session_kind']}|{meta['session_id']}"


def is_eligible(meta: Mapping[str, Any]) -> tuple[bool, str]:
    if meta.get("session_validity") != "VALID_SESSION":
        return False, f"not_VALID_SESSION:{meta.get('session_validity')}"
    if meta.get("seal_status") != "SEALED_VALID":
        return False, f"not_SEALED_VALID:{meta.get('seal_status')}"
    if meta.get("session_kind") not in ("am", "pm"):
        return False, f"bad_session_kind:{meta.get('session_kind')}"
    return True, "ok"


def rewrite_summary_csv(root: Path, man: dict[str, Any]) -> None:
    rows = []
    for sk, info in sorted((man.get("sessions") or {}).items()):
        rows.append(
            {
                "session_key": sk,
                "trading_date": info.get("trading_date"),
                "session_kind": info.get("session_kind"),
                "session_id": info.get("session_id"),
                "n_entries": info.get("n_entries"),
                "sync_ok": info.get("sync_ok"),
                "board_level_missing_rate": info.get("board_level_missing_rate"),
                "capture_events": info.get("capture_events"),
                "dups_skipped": info.get("dups_skipped"),
                "disk_used_pct": info.get("disk_used_pct"),
                "status": info.get("status"),
                "ingested_at": info.get("ingested_at"),
            }
        )
    path = summary_csv_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text(
            "session_key,trading_date,session_kind,session_id,n_entries,sync_ok,board_level_missing_rate,"
            "capture_events,dups_skipped,disk_used_pct,status,ingested_at\n",
            encoding="utf-8",
        )
        return
    cols = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def compression_candidates(native_root: Path, trading_date: str) -> list[str]:
    """Suggest compress/archive targets only — never delete."""
    out = []
    cap = native_root / "data" / "market_capture" / trading_date
    if cap.is_dir():
        parts = list(cap.glob("push_part_*.jsonl"))
        nonzero = [p for p in parts if p.stat().st_size > 0]
        if nonzero:
            out.append(
                f"OPTIONAL_ARCHIVE: {cap} ({len(nonzero)} nonempty push_part_*.jsonl) — compress/offline; do NOT auto-delete"
            )
    slim = native_root / "results" / "reports" / "phase687w37_live_board_entry_quality" / "_board_slim_20260716.parquet"
    if slim.is_file():
        out.append(f"OPTIONAL_DELETE_DERIVED: {slim} (rebuildable slim extract)")
    return out


def reanalysis_gate(n_days: int) -> str:
    if n_days >= 20:
        return "READY_FOR_ADOPTION_REVIEW"
    if n_days >= 10:
        return "CANDIDATE_STABILITY_EVAL"
    if n_days >= 5:
        return "INTERIM_CHECK_ONLY"
    return "ACCUMULATING"


def build_session_dataframe(
    *,
    native_root: Path,
    session_dir: Path,
    meta: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    capture_dir = native_root / "data" / "market_capture" / str(meta["trading_date"])
    entries = load_accepted_entries(session_dir)
    quality: dict[str, Any] = {
        "trading_date": meta["trading_date"],
        "session_kind": meta["session_kind"],
        "session_id": meta["session_id"],
        "n_entries": len(entries),
        "capture_dir": str(capture_dir),
        "capture_exists": capture_dir.is_dir(),
    }
    if not capture_dir.is_dir():
        quality["status"] = "CAPTURE_MISSING"
        df = pd.DataFrame(
            [
                {
                    "trading_date": meta["trading_date"],
                    "session_kind": meta["session_kind"],
                    "session_id": meta["session_id"],
                    **e,
                    "board_sync_ok": False,
                    "board_sync_lag_sec": float("nan"),
                    "sync_clock": "received_at_jst_backward",
                }
                for e in entries
            ]
        )
        quality["sync_ok"] = 0
        return df, quality

    cache = native_root / "results" / "research" / "board_entry_dataset" / "_cache" / str(meta["session_id"])
    try:
        board, extract_q = stream_slim_board(capture_dir=capture_dir, entries=entries, cache_dir=cache)
        df = compute_entry_feature_rows(
            entries,
            board,
            trading_date=str(meta["trading_date"]),
            session_kind=str(meta["session_kind"]),
            session_id=str(meta["session_id"]),
        )
        sync_ok = int(df["board_sync_ok"].sum()) if len(df) and "board_sync_ok" in df.columns else 0
        lvl_miss = 0.0
        if len(board) and "bid1_px" in board.columns:
            lvl_miss = float((board["bid1_px"].isna()).mean())
        cap_summary = {}
        if (capture_dir / "capture_summary.json").is_file():
            cap_summary = json.loads((capture_dir / "capture_summary.json").read_text(encoding="utf-8"))
        cap_status = {}
        if (capture_dir / "capture_status.json").is_file():
            cap_status = json.loads((capture_dir / "capture_status.json").read_text(encoding="utf-8"))
        quality.update(
            {
                "status": "OK",
                "sync_ok": sync_ok,
                "board_level_missing_rate": lvl_miss,
                "capture_events": int(cap_status.get("event_count") or cap_summary.get("total_events") or 0),
                "capture_duplicate_payload_count": int(cap_summary.get("duplicate_payload_count") or 0),
                "dups_skipped": int(extract_q.get("dups_skipped") or 0),
                "slim_rows": int(extract_q.get("rows") or 0),
                "malformed": int(cap_summary.get("malformed_payload_count") or 0),
                "dropped": int(cap_status.get("dropped_event_count") or 0),
            }
        )
    finally:
        try:
            if cache.is_dir():
                shutil.rmtree(cache, ignore_errors=True)
        except Exception:
            pass
    return df, quality


def append_session(
    *,
    native_root: Path,
    session_dir: Path,
    summary: Optional[Mapping[str, Any]] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Append one AM or PM session into the multi-day dataset. Idempotent per session_key."""
    native_root = Path(native_root)
    session_dir = Path(session_dir)
    root = dataset_root(native_root)
    root.mkdir(parents=True, exist_ok=True)
    disk_pct = disk_usage_pct(native_root)
    meta = detect_session_meta(session_dir, summary)
    sk = session_key(meta)
    result: dict[str, Any] = {
        "session_key": sk,
        "trading_date": meta["trading_date"],
        "session_kind": meta["session_kind"],
        "disk_used_pct": round(disk_pct, 2),
        "disk_warning": disk_pct >= DISK_WARN_PCT,
        "ops_warn_before_next_capture": disk_pct >= DISK_OPS_WARN_PCT,
        "compression_candidates": compression_candidates(native_root, str(meta["trading_date"])),
    }

    ok, reason = is_eligible(meta)
    if not ok:
        result.update({"status": "SKIPPED_INELIGIBLE", "reason": reason})
        return result

    man = load_manifest(root)
    if sk in (man.get("sessions") or {}) and not force:
        result.update({"status": "SKIPPED_ALREADY_INGESTED", "reason": "session_key_exists"})
        return result

    # Do not wipe an existing day partition; only append new session rows.
    part = partition_dir(root, str(meta["trading_date"]))
    entries_path = part / "entries.parquet"
    quality_path = part / "data_quality.json"

    df, quality = build_session_dataframe(native_root=native_root, session_dir=session_dir, meta=meta)
    quality["disk_used_pct"] = round(disk_pct, 2)
    quality["disk_warning"] = disk_pct >= DISK_WARN_PCT
    quality["ops_warn_before_next_capture"] = disk_pct >= DISK_OPS_WARN_PCT
    quality["compression_candidates"] = result["compression_candidates"]
    quality["auto_delete"] = False
    quality["capture_copied"] = False

    schema = ensure_schema(root, df if len(df.columns) else None)
    mismatch = schema_mismatch(df, schema)
    if mismatch:
        result.update({"status": "DATASET_SCHEMA_MISMATCH", "reason": mismatch})
        return result
    df_aligned, extras = align_to_schema(df, schema)
    quality["schema_extras_dropped"] = extras

    sync_ok = int(quality.get("sync_ok") or 0)
    n_entries = int(quality.get("n_entries") or 0)
    if n_entries > 0 and sync_ok / max(1, n_entries) < 0.5:
        result.update(
            {
                "status": "CAPTURE_SYNC_QUALITY_FAILED",
                "reason": f"sync_ok_ratio={sync_ok}/{n_entries}",
                "quality": quality,
            }
        )
        return result

    if disk_pct >= DISK_OPS_WARN_PCT:
        # Still allow append of slim dataset rows, but mark ops block for Capture growth.
        quality["status"] = quality.get("status") or "OK"
        quality["disk_ops_alert"] = True
    if disk_pct >= 95.0:
        result.update({"status": "DISK_CAPACITY_BLOCKED", "reason": f"disk_used_pct={disk_pct:.1f}", "quality": quality})
        return result

    part.mkdir(parents=True, exist_ok=True)
    if entries_path.is_file():
        prev = pd.read_parquet(entries_path)
        # Guard: do not drop prior sessions
        if "session_id" in prev.columns:
            prev = prev[prev["session_id"] != meta["session_id"]]
        prev_aligned, _ = align_to_schema(prev, schema)
        combined = pd.concat([prev_aligned, df_aligned], ignore_index=True)
    else:
        combined = df_aligned

    # Atomic write
    with tempfile.NamedTemporaryFile(delete=False, suffix=".parquet", dir=str(part)) as tmp:
        tmp_path = Path(tmp.name)
    try:
        combined.to_parquet(tmp_path, index=False)
        tmp_path.replace(entries_path)
    finally:
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except Exception:
                pass

    # Merge quality file (per-session records under day)
    day_q: dict[str, Any] = {"trading_date": meta["trading_date"], "sessions": {}}
    if quality_path.is_file():
        try:
            day_q = json.loads(quality_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    day_q.setdefault("sessions", {})[str(meta["session_id"])] = quality
    day_q["n_entries_total"] = int(len(combined))
    day_q["session_kinds"] = sorted(
        {str(x) for x in combined["session_kind"].unique()} if "session_kind" in combined.columns else []
    )
    day_q["updated_at"] = datetime.now(JST).isoformat()
    quality_path.write_text(json.dumps(day_q, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    man.setdefault("sessions", {})[sk] = {
        "trading_date": meta["trading_date"],
        "session_kind": meta["session_kind"],
        "session_id": meta["session_id"],
        "n_entries": n_entries,
        "sync_ok": sync_ok,
        "board_level_missing_rate": quality.get("board_level_missing_rate"),
        "capture_events": quality.get("capture_events"),
        "dups_skipped": quality.get("dups_skipped"),
        "disk_used_pct": round(disk_pct, 2),
        "status": "INGESTED",
        "ingested_at": datetime.now(JST).isoformat(),
        "partition": str(part),
    }
    dates = sorted({info.get("trading_date") for info in man["sessions"].values() if info.get("trading_date")})
    man["trading_dates"] = dates
    man["n_trading_days"] = len(dates)
    man["n_sessions"] = len(man["sessions"])
    man["n_entries_total"] = sum(int(v.get("n_entries") or 0) for v in man["sessions"].values())
    man["reanalysis_gate"] = reanalysis_gate(len(dates))
    man["disk_used_pct"] = round(disk_pct, 2)
    man["disk_warning"] = disk_pct >= DISK_WARN_PCT
    man["ops_warn_before_next_capture"] = disk_pct >= DISK_OPS_WARN_PCT
    save_manifest(root, man)
    rewrite_summary_csv(root, man)

    result.update(
        {
            "status": "INGESTED",
            "n_entries": n_entries,
            "sync_ok": sync_ok,
            "quality": quality,
            "n_trading_days": man["n_trading_days"],
            "n_entries_total": man["n_entries_total"],
            "reanalysis_gate": man["reanalysis_gate"],
        }
    )
    if disk_pct >= DISK_OPS_WARN_PCT:
        result["status"] = "INGESTED_WITH_DISK_OPS_WARN"
    return result


def maybe_append_session_board_dataset(
    *,
    native_root: Path,
    session_dir: Path,
    summary: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Fail-open wrapper for pilot_runner post-seal hook."""
    try:
        return append_session(native_root=native_root, session_dir=session_dir, summary=summary)
    except Exception as exc:
        log.warning("board_entry_dataset append failed: %s", exc)
        return {"status": "ERROR", "error": str(exc)}


def import_existing_dataframe(
    *,
    native_root: Path,
    df: pd.DataFrame,
    trading_date: str,
    session_kind: str,
    session_id: str,
    quality: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Bootstrap helper: ingest a precomputed W37-compatible dataframe without Capture rescan."""
    native_root = Path(native_root)
    root = dataset_root(native_root)
    root.mkdir(parents=True, exist_ok=True)
    meta = {
        "trading_date": trading_date,
        "session_kind": session_kind,
        "session_id": session_id,
        "session_validity": "VALID_SESSION",
        "seal_status": "SEALED_VALID",
    }
    sk = session_key(meta)
    man = load_manifest(root)
    if sk in (man.get("sessions") or {}):
        return {"status": "SKIPPED_ALREADY_INGESTED", "session_key": sk}
    disk_pct = disk_usage_pct(native_root)
    out = df.copy()
    out["trading_date"] = trading_date
    out["session_kind"] = session_kind
    out["session_id"] = session_id
    schema = ensure_schema(root, out)
    mismatch = schema_mismatch(out, schema)
    if mismatch:
        return {"status": "DATASET_SCHEMA_MISMATCH", "reason": mismatch}
    aligned, extras = align_to_schema(out, schema)
    # Freeze schema columns from this first import if schema was just created with these cols
    if schema.get("columns") == list(REQUIRED_META_COLS) or len(schema.get("columns") or []) < 20:
        schema["columns"] = list(out.columns)
        schema_path(root).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        aligned, extras = align_to_schema(out, schema)

    part = partition_dir(root, trading_date)
    part.mkdir(parents=True, exist_ok=True)
    entries_path = part / "entries.parquet"
    if entries_path.is_file():
        prev = pd.read_parquet(entries_path)
        if "session_id" in prev.columns:
            prev = prev[prev["session_id"] != session_id]
        prev_a, _ = align_to_schema(prev, schema)
        combined = pd.concat([prev_a, aligned], ignore_index=True)
    else:
        combined = aligned
    combined.to_parquet(entries_path, index=False)

    q = dict(quality or {})
    q.update(
        {
            "trading_date": trading_date,
            "session_kind": session_kind,
            "session_id": session_id,
            "n_entries": int(len(aligned)),
            "sync_ok": int(aligned["board_sync_ok"].sum()) if "board_sync_ok" in aligned.columns else 0,
            "status": "IMPORTED_FROM_W37",
            "schema_extras_dropped": extras,
            "disk_used_pct": round(disk_pct, 2),
            "capture_copied": False,
            "auto_delete": False,
        }
    )
    day_q = {"trading_date": trading_date, "sessions": {session_id: q}, "n_entries_total": int(len(combined))}
    (part / "data_quality.json").write_text(
        json.dumps(day_q, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    man.setdefault("sessions", {})[sk] = {
        "trading_date": trading_date,
        "session_kind": session_kind,
        "session_id": session_id,
        "n_entries": q["n_entries"],
        "sync_ok": q["sync_ok"],
        "board_level_missing_rate": q.get("board_level_missing_rate", 0.0),
        "capture_events": q.get("capture_events"),
        "dups_skipped": q.get("dups_skipped"),
        "disk_used_pct": round(disk_pct, 2),
        "status": "INGESTED",
        "ingested_at": datetime.now(JST).isoformat(),
        "source": "w37_import",
    }
    dates = sorted({info.get("trading_date") for info in man["sessions"].values() if info.get("trading_date")})
    man["trading_dates"] = dates
    man["n_trading_days"] = len(dates)
    man["n_sessions"] = len(man["sessions"])
    man["n_entries_total"] = sum(int(v.get("n_entries") or 0) for v in man["sessions"].values())
    man["reanalysis_gate"] = reanalysis_gate(len(dates))
    man["disk_used_pct"] = round(disk_pct, 2)
    man["disk_warning"] = disk_pct >= DISK_WARN_PCT
    man["ops_warn_before_next_capture"] = disk_pct >= DISK_OPS_WARN_PCT
    save_manifest(root, man)
    rewrite_summary_csv(root, man)
    return {
        "status": "INGESTED",
        "session_key": sk,
        "n_entries": q["n_entries"],
        "sync_ok": q["sync_ok"],
        "n_trading_days": man["n_trading_days"],
        "n_entries_total": man["n_entries_total"],
        "disk_used_pct": round(disk_pct, 2),
        "reanalysis_gate": man["reanalysis_gate"],
    }
