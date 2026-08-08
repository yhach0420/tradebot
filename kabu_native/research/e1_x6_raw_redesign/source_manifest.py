"""P0 source manifest: read-only SHA freeze of raw + canonical-cache inputs.

Inputs are historical only (Paper writes today's live files; those are never in
scope: the 9 target days are all in the past). Nothing is modified.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import sha256_file, sha256_obj

DAYS = (
    "20260721", "20260722", "20260723", "20260724", "20260727",
    "20260728", "20260729", "20260730", "20260731",
)


def day_dash(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def raw_day_dir(native_root: Path, day: str) -> Path:
    return native_root / "data" / "push_jsonl" / day_dash(day)


def canonical_cache_dir() -> Path:
    """Existing durable normalization cache (read-only input)."""
    return Path.home() / "e1x6_research_store" / "e1_x5_norm_cache"


def build_source_manifest(native_root: Path) -> dict[str, Any]:
    days: dict[str, Any] = {}
    for day in DAYS:
        rd = raw_day_dir(native_root, day)
        if not rd.is_dir():
            raise SystemExit(f"FAIL: raw day dir missing {rd}")
        files = {}
        for fp in sorted(rd.glob("*.jsonl")):
            st = fp.stat()
            files[fp.name] = {
                "sha256": sha256_file(fp),
                "size": st.st_size,
                "mtime_epoch": st.st_mtime,
            }
        cache = {}
        cd = canonical_cache_dir()
        for suffix in ("events_slim_v3.pkl.gz", "gap_map.json", "normalize_report.json"):
            cf = cd / f"{day}_{suffix}"
            cache[suffix] = {"sha256": sha256_file(cf), "size": cf.stat().st_size} if cf.is_file() else None
        days[day] = {
            "raw_dir": str(rd),
            "raw_files_n": len(files),
            "raw_bytes": sum(f["size"] for f in files.values()),
            "raw_files": files,
            "canonical_cache": cache,
        }
    manifest = {
        "days": days,
        "read_only": True,
        "note": "raw push_jsonl per-symbol day files + canonical norm cache; no live/appending files",
    }
    manifest["source_manifest_sha256"] = sha256_obj(manifest)
    return manifest
