"""Official tick-class resolution with effective-date evidence (Phase A-R3 §5).

Master CSV remains the primary source. Symbols missing from the dated master
(581A/584A/593A/598A) are resolved ONLY via frozen official JPX evidence:
new-listings page (domestic common stock, listing date, market segment) plus
the official tick-unit rule page (TOPIX500 = TOPIX100+Mid400 get NARROW;
otherwise OTHER). Empirical increments are cross-check only and never fill a
missing official class. No 0.1-yen fallback.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .store import sha256_file
from .tick_official import (
    TOPIX500_SCALES,
    empirical_check,
    load_master,
    master_path,
    official_class,
)
from .tick_resolver import CLASS_NARROW, CLASS_OTHER

# Frozen official evidence (fetched once; SHA locked). Not derived from ticks.
EVIDENCE_DIR = (
    Path.home() / "e1x6_research_store" / "raw_feature_redesign"
    / "official_tick_evidence_r3"
)

# Effective-date rows for the 4 Growth IPOs, from JPX 新規上場銘柄一覧.
# TOPIX500 applicability: none — Growth new listings are not TOPIX100/Mid400
# constituents; JPX tick-unit page applies NARROW only to TOPIX500構成銘柄.
# Periodic tick-table add/remove notices (2025-10-31, 2026-05-18) do not list
# these codes.
SUPPLEMENTAL_OFFICIAL: dict[str, dict[str, Any]] = {
    "581A": {
        "name": "GO（株）",
        "security_type": "内国普通株式",
        "market_segment": "グロース",
        "listing_date": "2026-06-16",
        "approval_date": "2026-05-14",
        "topix500_applicable": False,
        "topix500_apply_start": None,
        "topix500_apply_end": None,
        "class": CLASS_OTHER,
        "class_reason": (
            "OFFICIAL_JPX_NEW_LISTING:Growth domestic common stock; "
            "NOT a TOPIX500 (TOPIX100/Mid400) constituent on any evaluation day "
            "=> OTHER tick table"
        ),
        "effective_from": "2026-06-16",
        "effective_to": None,  # still listed as of evidence fetch
        "evidence_keys": ["jpx_new_listings", "jpx_tick_units",
                          "jpx_tick_change_20260518", "jpx_tick_change_20251031"],
    },
    "584A": {
        "name": "LiNKX（株）",
        "security_type": "内国普通株式",
        "market_segment": "グロース",
        "listing_date": "2026-06-23",
        "approval_date": "2026-05-21",
        "topix500_applicable": False,
        "topix500_apply_start": None,
        "topix500_apply_end": None,
        "class": CLASS_OTHER,
        "class_reason": (
            "OFFICIAL_JPX_NEW_LISTING:Growth domestic common stock; "
            "NOT a TOPIX500 constituent => OTHER tick table"
        ),
        "effective_from": "2026-06-23",
        "effective_to": None,
        "evidence_keys": ["jpx_new_listings", "jpx_tick_units",
                          "jpx_tick_change_20260518", "jpx_tick_change_20251031"],
    },
    "593A": {
        "name": "（株）ティアフォー",
        "security_type": "内国普通株式",
        "market_segment": "グロース",
        "listing_date": "2026-07-22",
        "approval_date": "2026-06-29",
        "topix500_applicable": False,
        "topix500_apply_start": None,
        "topix500_apply_end": None,
        "class": CLASS_OTHER,
        "class_reason": (
            "OFFICIAL_JPX_NEW_LISTING:Growth domestic common stock; "
            "NOT a TOPIX500 constituent => OTHER tick table"
        ),
        "effective_from": "2026-07-22",
        "effective_to": None,
        "evidence_keys": ["jpx_new_listings", "jpx_tick_units",
                          "jpx_tick_change_20260518", "jpx_tick_change_20251031"],
    },
    "598A": {
        "name": "チャットプラス（株）",
        "security_type": "内国普通株式",
        "market_segment": "グロース",
        "listing_date": "2026-07-15",
        "approval_date": "2026-06-11",
        "topix500_applicable": False,
        "topix500_apply_start": None,
        "topix500_apply_end": None,
        "class": CLASS_OTHER,
        "class_reason": (
            "OFFICIAL_JPX_NEW_LISTING:Growth domestic common stock; "
            "NOT a TOPIX500 constituent => OTHER tick table"
        ),
        "effective_from": "2026-07-15",
        "effective_to": None,
        "evidence_keys": ["jpx_new_listings", "jpx_tick_units",
                          "jpx_tick_change_20260518", "jpx_tick_change_20251031"],
    },
}


def load_evidence_manifest() -> dict[str, Any]:
    fp = EVIDENCE_DIR / "manifest.json"
    if not fp.is_file():
        raise SystemExit(f"FAIL: official tick evidence missing: {fp}")
    return json.loads(fp.read_text(encoding="utf-8"))


def verify_evidence_integrity(manifest: dict[str, Any]) -> list[str]:
    """Return list of integrity failures (empty => OK)."""
    fails = []
    for key, row in (manifest.get("sources") or {}).items():
        fp = Path(row["local_path"])
        if not fp.is_file():
            fails.append(f"MISSING_FILE:{key}")
            continue
        if sha256_file(fp) != row["sha256"]:
            fails.append(f"SHA_MISMATCH:{key}")
    for code, spec in SUPPLEMENTAL_OFFICIAL.items():
        src = manifest["sources"].get("jpx_new_listings") or {}
        if not src.get("codes_mentioned", {}).get(code):
            fails.append(f"CODE_NOT_IN_NEW_LISTINGS_PAGE:{code}")
        # tick-change pages must NOT list these codes as TOPIX500 tick targets
        for k in ("jpx_tick_change_20260518", "jpx_tick_change_20251031"):
            if (manifest["sources"].get(k) or {}).get("codes_mentioned", {}).get(code):
                fails.append(f"UNEXPECTED_TOPIX500_TICK_NOTICE:{code}:{k}")
    return fails


def class_on_day(spec: dict[str, Any], day: str) -> tuple[Optional[str], str]:
    """Resolve class for an evaluation day using effective dates (YYYYMMDD)."""
    day_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    if day_iso < spec["listing_date"]:
        return None, f"BEFORE_LISTING:{spec['listing_date']}"
    if spec["effective_to"] is not None and day_iso > spec["effective_to"]:
        return None, f"AFTER_EFFECTIVE_TO:{spec['effective_to']}"
    if spec.get("security_type") != "内国普通株式":
        return None, "SECURITY_TYPE_UNKNOWN_OR_NOT_COMMON"
    if spec.get("topix500_applicable"):
        return CLASS_NARROW, spec["class_reason"]
    return CLASS_OTHER, spec["class_reason"]


def classify_universe_r3(
    repo_root: Path,
    universe: list[str],
    tick_evidence: dict[str, dict[str, list]],
    evaluation_days: list[str],
) -> dict[str, Any]:
    master = load_master(repo_root)
    manifest = load_evidence_manifest()
    integrity = verify_evidence_integrity(manifest)
    rows: dict[str, Any] = {}
    unresolved: list[str] = []

    for sym in sorted(universe):
        obs = tick_evidence.get(sym, {})
        obs_n = int(sum(v[1] for v in obs.values())) if obs else 0
        master_row = master.get(sym)
        per_day: dict[str, Any] = {}

        if master_row is not None:
            cls, reason = official_class(master_row)
            if cls is None:
                check_msg = "NOT_EVALUATED_OFFICIAL_CLASS_UNRESOLVED"
                unresolved.append(sym)
                final = None
            else:
                if obs:
                    check_ok, check_msg = empirical_check(
                        cls, {k: v[0] for k, v in obs.items()}
                    )
                    if not check_ok:
                        unresolved.append(sym)
                        final = None
                    else:
                        final = cls
                else:
                    check_msg = "NO_OBSERVATIONS"
                    final = cls
            for day in evaluation_days:
                per_day[day] = {"class": final, "reason": reason}
            rows[sym] = {
                "class": final,
                "official_reason": reason,
                "empirical_check": check_msg,
                "observations": obs_n,
                "source": "master_csv",
                "per_day": per_day,
                "evidence": None,
            }
            continue

        # Supplemental official evidence path
        spec = SUPPLEMENTAL_OFFICIAL.get(sym)
        if spec is None:
            unresolved.append(sym)
            rows[sym] = {
                "class": None,
                "official_reason": "NOT_IN_MASTER_AND_NO_SUPPLEMENTAL_EVIDENCE",
                "empirical_check": "NOT_EVALUATED_OFFICIAL_CLASS_UNRESOLVED",
                "observations": obs_n,
                "source": "none",
                "per_day": {d: {"class": None, "reason": "UNRESOLVED"}
                            for d in evaluation_days},
                "evidence": None,
            }
            continue

        day_classes = []
        for day in evaluation_days:
            cls, reason = class_on_day(spec, day)
            per_day[day] = {"class": cls, "reason": reason}
            if cls is not None:
                day_classes.append(cls)
        resolved = set(day_classes)
        if len(resolved) == 1:
            final = next(iter(resolved))
            reason = spec["class_reason"]
        elif len(resolved) == 0:
            final = None
            reason = "NO_EVALUATION_DAY_ON_OR_AFTER_LISTING"
        else:
            final = None
            reason = f"INCONSISTENT_EFFECTIVE_CLASS:{sorted(resolved)}"

        if final is None:
            check_msg = "NOT_EVALUATED_OFFICIAL_CLASS_UNRESOLVED"
            unresolved.append(sym)
        elif obs:
            check_ok, check_msg = empirical_check(
                final, {k: v[0] for k, v in obs.items()}
            )
            if not check_ok:
                unresolved.append(sym)
                final = None
        else:
            check_msg = "NO_OBSERVATIONS"

        ev_sources = {
            k: {
                "url": manifest["sources"][k]["url"],
                "local_path": manifest["sources"][k]["local_path"],
                "sha256": manifest["sources"][k]["sha256"],
                "fetched_at_jst": manifest["fetched_at_jst"],
            }
            for k in spec["evidence_keys"] if k in manifest["sources"]
        }
        rows[sym] = {
            "class": final,
            "official_reason": reason,
            "empirical_check": check_msg,
            "observations": obs_n,
            "source": "jpx_official_supplemental",
            "security_type": spec["security_type"],
            "market_segment": spec["market_segment"],
            "listing_date": spec["listing_date"],
            "topix500_applicable": spec["topix500_applicable"],
            "topix500_apply_start": spec["topix500_apply_start"],
            "topix500_apply_end": spec["topix500_apply_end"],
            "effective_from": spec["effective_from"],
            "effective_to": spec["effective_to"],
            "per_day": per_day,
            "evidence": ev_sources,
        }

    return {
        "master_path": str(master_path(repo_root)),
        "master_sha256": sha256_file(master_path(repo_root)),
        "evidence_manifest_sha256": sha256_file(EVIDENCE_DIR / "manifest.json"),
        "evidence_fetched_at_jst": manifest["fetched_at_jst"],
        "evidence_integrity_failures": integrity,
        "rule": (
            "master scale_category decides when present; missing symbols require "
            "frozen JPX official evidence with listing date, market segment, "
            "security type, and TOPIX500 applicability (effective-dated). "
            "Empirical increments are cross-check only. No tick fallback. "
            "ETF/REIT/unknown/missing evidence => UNRESOLVED => P1_R3_BLOCKED."
        ),
        "symbol_classes": rows,
        "unresolved": unresolved,
        "supplemental_codes": sorted(SUPPLEMENTAL_OFFICIAL),
    }
