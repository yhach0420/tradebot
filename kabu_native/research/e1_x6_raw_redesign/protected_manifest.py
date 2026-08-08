"""Paper protected manifest: SHA-freeze all Paper-critical files before research.

Recomputed at completion; any before/after difference fails the run gate.
Research never modifies, resets, checks out or overwrites these files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import sha256_file, sha256_obj


def _native(repo_root: Path) -> Path:
    return repo_root / "kabu_native"


def protected_files(repo_root: Path) -> list[Path]:
    """Concrete Paper entrypoints / runner / runtime / config / task files."""
    nat = _native(repo_root)
    files: list[Path] = []
    # repo-root bat entrypoints
    files.extend(sorted(repo_root.glob("run_paper_trade*.bat")))
    # PowerShell launchers
    files.extend(sorted((nat / "scripts").glob("run_paper_trade*.ps1")))
    files.extend(sorted((nat / "scripts").glob("run_market_capture_sidecar.ps1")))
    # production configs (all YAML under configs/)
    files.extend(sorted((nat / "configs").glob("*.yaml")))
    # full Paper runtime package (shared runtime must not change)
    files.extend(sorted((nat / "src" / "small_paper").glob("*.py")))
    return [f for f in files if f.is_file()]


def build_protected_manifest(repo_root: Path) -> dict[str, Any]:
    rows = {}
    for fp in protected_files(repo_root):
        rel = fp.relative_to(repo_root).as_posix()
        st = fp.stat()
        rows[rel] = {"sha256": sha256_file(fp), "size": st.st_size}
    return {
        "files_n": len(rows),
        "files": rows,
        "manifest_sha256": sha256_obj(rows),
    }


def manifests_equal(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, list[str]]:
    diffs: list[str] = []
    bf, af = before.get("files", {}), after.get("files", {})
    for k in sorted(set(bf) | set(af)):
        if k not in bf:
            diffs.append(f"ADDED:{k}")
        elif k not in af:
            diffs.append(f"REMOVED:{k}")
        elif bf[k]["sha256"] != af[k]["sha256"]:
            diffs.append(f"CHANGED:{k}")
    return (not diffs, diffs)
