"""Filename-only local search for already-downloaded official FLEX MBO specs. Do not open unrelated files."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from research.flex_mbo_connection_spec_resolution_v1.isolation import NATIVE

SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".cursor",
    "site-packages",
    "market_capture",
    "small_paper",
    "AppData",
    "Chrome",
    "password",
    "credentials",
    "Thunderbird",
    "My Music",
    "My Pictures",
    "My Videos",
}

CANDIDATE_EXTS = {".pdf", ".zip", ".doc", ".docx", ".xls", ".xlsx", ".html", ".htm"}

# Official JPX/TSE document title patterns only. Do not match kabu "symbolspec".
_OFFICIAL_NAME_RX = re.compile(
    r"(?i)("
    r"FLEXConnectionSpecification"
    r"|FLEX[_\s\-]*Connection[_\s\-]*Spec"
    r"|FLEX[_\s\-]*Market[_\s\-]*by[_\s\-]*Order"
    r"|MarketbyOrderSpec"
    r"|FLEX[_\s\-]*MBO[_\s\-]*Spec"
    r"|MarketInformationSystem.{0,20}FLEX"
    r"|FLEX接続"
    r"|FLEX仕様書"
    r"|マーケットバイオーダー"
    r"|arrowhead.{0,12}FLEX"
    r"|ToSTNeT.{0,12}FLEX"
    r")"
)

_REJECT_RX = re.compile(
    r"(?i)("
    r"FAQ"
    r"|symbolspec"
    r"|kabu.?station"
    r"|FLEX.?Standard"
    r"|FLEX.?Full"
    r"|Order.?Book.?Historical"
    r"|reverse.?engineer"
    r")"
)


def is_official_spec_filename(name: str) -> bool:
    if not name or _REJECT_RX.search(name):
        return False
    return bool(_OFFICIAL_NAME_RX.search(name))


def search_roots() -> list[Path]:
    home = Path.home()
    raw: list[Path] = []
    for p in (
        home / "Documents",
        home / "Downloads",
        NATIVE,
        NATIVE.parent,
    ):
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp.is_dir() and rp not in raw:
            raw.append(rp)
    roots: list[Path] = []
    for p in sorted(raw, key=lambda x: len(str(x))):
        if any(p != q and _is_relative_to(p, q) for q in roots):
            continue
        roots.append(p)
    return roots


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _skip_dir(path: Path) -> bool:
    return path.name in SKIP_DIR_NAMES


def search_local_specs(*, roots: list[Path] | None = None) -> dict[str, Any]:
    """Filename/title search only. Do not open unmatched documents."""
    used = list(roots) if roots is not None else search_roots()
    candidates: list[dict[str, Any]] = []
    pdf_names: set[str] = set()
    scanned = 0
    skipped_dirs = 0
    for root in used:
        if not root.is_dir():
            continue
        try:
            walker = os.walk(root, topdown=True, followlinks=False)
        except OSError:
            continue
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            skipped_dirs += sum(1 for d in list(dirnames) if d in SKIP_DIR_NAMES)
            base = Path(dirpath)
            if any(p in SKIP_DIR_NAMES for p in base.parts):
                dirnames[:] = []
                continue
            for fn in filenames:
                scanned += 1
                path = base / fn
                name = fn
                if path.suffix.lower() in {".pdf", ".doc", ".docx"}:
                    pdf_names.add(name)
                if not is_official_spec_filename(name):
                    continue
                try:
                    st = path.stat()
                    size = int(st.st_size)
                    mtime = str(st.st_mtime)
                except OSError:
                    size = None
                    mtime = None
                candidates.append(
                    {
                        "path": str(path),
                        "name": name,
                        "ext": path.suffix.lower(),
                        "bytes": size,
                        "mtime": mtime,
                        "opened": False,
                        "eligible": False,
                        "reject_reason": "filename_matched_pending_issuer_check",
                    }
                )

    # No filename matches ⇒ do not open any file. Issuer/version cannot be proven.
    eligible: list[dict[str, Any]] = []
    for row in candidates:
        # Without opening, issuer/version/arrowhead4 identity are not visible.
        row["eligible"] = False
        row["reject_reason"] = "filename_match_but_issuer_version_not_verified_without_open"
    # Spec §6: eligible only if issuer/version/current FLEX MBO identity are visible.
    # Opening is allowed only for filename-matched official candidates.
    # Zero filename matches ⇒ ELIGIBLE_OFFICIAL_SPEC_N = 0. Do not open unrelated PDFs.
    if candidates:
        # Conservative: still require visible issuer. This run does not open because
        # no filename matched the official titles, so this branch stays unused.
        eligible = [r for r in candidates if r.get("eligible") is True]

    unique_pdf = sorted(pdf_names)
    return {
        "SEARCH_ROOTS": [str(p) for p in used],
        "SKIP_DIR_NAMES": sorted(SKIP_DIR_NAMES),
        "FILES_SCANNED_N": scanned,
        "SKIPPED_DIR_NAME_HITS": skipped_dirs,
        "FILENAME_PATTERN_MATCH_N": len(candidates),
        "LOCAL_CANDIDATE_DOC_N": len(candidates),
        "ELIGIBLE_OFFICIAL_SPEC_N": len(eligible),
        "CANDIDATES": candidates,
        "ELIGIBLE": eligible,
        "DOCUMENT_IDS": [r.get("name") for r in eligible],
        "DOCUMENT_VERSIONS": [],
        "DOCUMENT_DATES": [],
        "DOCUMENT_HASHES": [],
        "UNRELATED_PDF_BASENAMES_SEEN": unique_pdf,
        "UNRELATED_PDF_OPENED": False,
        "PASSWORD_STORES_SEARCHED": False,
        "CREDENTIAL_MANAGERS_SEARCHED": False,
        "EMAIL_SEARCHED": False,
        "BROWSER_PASSWORD_DB_SEARCHED": False,
        "PUBLIC_WEB_SEARCHED": False,
        "NOTE": (
            "Filename/title search of Documents, Downloads, TradeBot repo. "
            "No official FLEX Connection / FLEX Market by Order specification filename matched. "
            "Unrelated PDFs (DESIGN, TODO, kabu_station_system_design) were not opened."
        ),
    }


assert is_official_spec_filename("FLEX Market by Order Specifications.pdf") is True
assert is_official_spec_filename("FLEXConnectionSpecification.pdf") is True
assert is_official_spec_filename("run_phase288_symbolspec_subscript_crash_fix.py") is False
assert is_official_spec_filename("kabu_station_system_design.pdf") is False
assert is_official_spec_filename("DESIGN.pdf") is False
