#!/usr/bin/env python
"""Write OPERATIONAL_VALIDATION_ONLY current-trading-day working identity.

Resolves the currently selected immutable OPVAL runtime candidate from
`active_v1r_opval_runtime_candidate.json` (override: TRADEBOT_OPVAL_RUNTIME_CANDIDATE_SELECTOR).
Does not hardcode Candidate-7/8/9. Does not overwrite freeze manifests or Formal V25.
Working-tree bytes are not trusted until they match the resolved immutable candidate.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

from small_paper.operational_validation import (
    OPVAL_ACTIVATION_ID,
    current_config_sha,
    current_git_head,
    resolve_opval_canonical_trading_date,
)
from small_paper.opval_runtime_candidate import (
    CANDIDATE_ID_PREFIX,
    resolve_current_opval_runtime_candidate,
)
from small_paper.v1r_activation_binding import (
    CANDIDATE_STATUS_OPVAL,
    SELECTOR_SCHEMA,
    V25_ACTIVATION_ID,
    candidate_source_digest,
    inventory_digest,
    manifest_content_sha,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

JST = ZoneInfo("Asia/Tokyo")
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
DEST_DIR = NATIVE / "results" / "research" / "v26g6_opval_launcher"
SELECTOR_DEST = DEST_DIR / "active_v1r_opval_current_trading_day.json"
MANIFEST_DEST = DEST_DIR / f"{OPVAL_ACTIVATION_ID}.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def freeze_identity_snapshot(*, native_root: Path) -> dict[str, str]:
    out = Path(native_root) / "results" / "research" / "v1r_exit_v2_prospective_activation"
    snap: dict[str, str] = {}
    formal_sel = out / "active_v1r_activation.json"
    if formal_sel.is_file():
        snap[str(formal_sel.resolve())] = json.dumps(_load_json(formal_sel), sort_keys=True)
    for p in sorted(out.glob("V1R_EXIT_V2_PAPER_PRIMARY_*.json")):
        snap[str(p.resolve())] = p.read_bytes().hex()
    for p in sorted(out.glob("active_v1r_candidate_*.json")):
        snap[str(p.resolve())] = p.read_bytes().hex()
    return snap


def _refuse_if_freeze_dest(dest: Path, *, native_root: Path) -> str:
    out = (Path(native_root) / "results" / "research" / "v1r_exit_v2_prospective_activation").resolve()
    resolved = dest.resolve()
    if resolved.parent == out:
        return "OPVAL_IDENTITY_MUST_NOT_OVERWRITE_FREEZE_DIR"
    return ""


def write_opval_current_trading_day_identity(
    *,
    native_root: Path,
    trading_date: str,
    dest_dir: Optional[Path] = None,
    runtime_candidate_selector: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    root = Path(native_root)
    out = root / "results" / "research" / "v1r_exit_v2_prospective_activation"
    dest = Path(dest_dir) if dest_dir is not None else (root / "results" / "research" / "v26g6_opval_launcher")
    man_dest = dest / f"{OPVAL_ACTIVATION_ID}.json"
    sel_dest = dest / "active_v1r_opval_current_trading_day.json"
    freeze_before = freeze_identity_snapshot(native_root=root)

    v25_sel_path = out / "active_v1r_activation.json"
    v25_path = out / f"{V25_ACTIVATION_ID}.json"
    c6_path = out / f"{C6_ID}.json"
    if not v25_sel_path.is_file() or not v25_path.is_file() or not c6_path.is_file():
        return {"ok": False, "reason": "OPVAL_FREEZE_IDENTITY_MISSING"}
    v25_sel = _load_json(v25_sel_path)
    v25 = _load_json(v25_path)
    c6 = _load_json(c6_path)
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25.get("sha256") != V25_SHA:
        return {"ok": False, "reason": "V25_SELECTOR_OR_MANIFEST_MUTATED"}
    if c6.get("sha256") != C6_SHA:
        return {"ok": False, "reason": "CANDIDATE6_MANIFEST_MUTATED"}

    blocked = _refuse_if_freeze_dest(man_dest, native_root=root)
    if blocked:
        return {"ok": False, "reason": blocked}

    current = resolve_current_opval_runtime_candidate(
        native_root=root,
        environ=environ,
        selector_path=runtime_candidate_selector,
    )
    if not current.get("ok"):
        return {"ok": False, "reason": str(current.get("reason") or "OPVAL_RUNTIME_CANDIDATE_RESOLVE_FAILED"), "current": current}
    cid = str(current.get("id") or "")
    csha = str(current.get("sha256") or "")
    if not cid.startswith(CANDIDATE_ID_PREFIX) or cid == C6_ID:
        return {"ok": False, "reason": "OPVAL_RUNTIME_CANDIDATE_ID_INVALID", "current": current}
    if not current.get("immutable") or not current.get("self_sha_ok"):
        return {"ok": False, "reason": str(current.get("reason") or "OPVAL_BOUND_CANDIDATE_MANIFEST_INVALID"), "current": current}
    if not current.get("working_tree_matches"):
        return {
            "ok": False,
            "reason": "OPVAL_INVENTORY_MISMATCH",
            "current": current,
            "note": "Working tree must match the resolved immutable candidate before OPVAL identity is written.",
        }

    cand = dict(current.get("manifest") or {})
    inv = dict(cand.get("runtime_file_sha256") or {})
    if not inv:
        return {"ok": False, "reason": "OPVAL_BOUND_CANDIDATE_INVENTORY_MISMATCH", "current": current}
    inv_check = verify_runtime_inventory(cand, native_root=root)
    if not inv_check.get("ok"):
        return {"ok": False, "reason": "OPVAL_INVENTORY_MISMATCH", "inventory": inv_check}

    day = str(trading_date or "").strip().replace("-", "")[:8]
    if len(day) != 8 or not day.isdigit():
        return {"ok": False, "reason": "OPVAL_TRADING_DATE_UNRESOLVED"}

    body = {k: v for k, v in cand.items() if k != "sha256"}
    body.update(
        {
            "manifest_id": OPVAL_ACTIVATION_ID,
            "candidate_id": OPVAL_ACTIVATION_ID,
            "candidate_status": CANDIDATE_STATUS_OPVAL,
            "classification": CANDIDATE_STATUS_OPVAL,
            "formal_paper_allowed": False,
            "prospective_allowed": False,
            "strategy_evaluation_allowed": False,
            "immutable": False,
            "paper_only": True,
            "order_enabled": False,
            "live_trading_enabled": False,
            "submit_cancel_live": "0/0/0",
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "INVALID_FOR_STRATEGY_EVALUATION": True,
            "NOT_PROSPECTIVE_DAY1": True,
            "not_a_freeze_candidate": True,
            "not_candidate_6": True,
            "trading_date": day,
            "bound_current_runtime_candidate": cid,
            "bound_current_runtime_candidate_id": cid,
            "bound_current_runtime_candidate_sha": csha,
            "working_tree_matches_bound_candidate": True,
            "date_policy": "per_run_canonical_trading_session",
            "legacy_opval_pinned_date": "20260817",
            "parent_activation_id": V25_ACTIVATION_ID,
            "parent_activation_sha": V25_SHA,
            "parent_candidate6_id": C6_ID,
            "parent_candidate6_sha": C6_SHA,
            "runtime_code_git_commit": current_git_head(),
            "config_sha256": current_config_sha(),
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": str(cand.get("runtime_inventory_digest") or inventory_digest(inv)),
            "candidate_source_digest": candidate_source_digest(inv, native_root=root),
            "created_at": datetime.now(JST).isoformat(timespec="milliseconds"),
            "hash_policy": {
                "sot": "bound_immutable_candidate_inventory_after_working_tree_match",
                "normalize_newlines": False,
                "selector_excluded_from_inventory": True,
                "binding_module": "small_paper.opval_runtime_candidate",
            },
        }
    )
    body["sha256"] = manifest_content_sha(body)
    dest.mkdir(parents=True, exist_ok=True)
    man_dest.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = _load_json(man_dest)
    ok, got, calc = verify_manifest_self_sha(loaded)
    if not (ok and got == calc == body["sha256"]):
        man_dest.unlink(missing_ok=True)
        return {"ok": False, "reason": "OPVAL_MANIFEST_SHA_MISMATCH"}
    inv_written = verify_runtime_inventory(loaded, native_root=root)
    if not inv_written.get("ok"):
        man_dest.unlink(missing_ok=True)
        return {"ok": False, "reason": "OPVAL_INVENTORY_MISMATCH", "inventory": inv_written}

    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": OPVAL_ACTIVATION_ID,
        "activation_sha": body["sha256"],
        "manifest_relpath": str(man_dest.resolve()).replace("\\", "/"),
        "note": (
            "OPERATIONAL_VALIDATION_ONLY current-trading-day working identity. "
            "Not Formal. Not a freeze. Bound to the current immutable OPVAL runtime candidate."
        ),
        "paper_mode": CANDIDATE_STATUS_OPVAL,
        "formal_paper_allowed": False,
        "prospective_allowed": False,
        "strategy_evaluation_allowed": False,
        "bound_current_runtime_candidate": cid,
        "bound_current_runtime_candidate_sha": csha,
        "trading_date": day,
    }
    sel_dest.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")

    freeze_after = freeze_identity_snapshot(native_root=root)
    if freeze_after != freeze_before:
        return {"ok": False, "reason": "FREEZE_IDENTITY_MUTATED_DURING_OPVAL_WRITE"}
    v25_after = _load_json(v25_path)
    sel_after = _load_json(v25_sel_path)
    c6_after = _load_json(c6_path)
    if v25_after.get("sha256") != V25_SHA or sel_after.get("activation_id") != V25_ACTIVATION_ID:
        return {"ok": False, "reason": "V25_MUTATED_DURING_OPVAL_WRITE"}
    if c6_after.get("sha256") != C6_SHA:
        return {"ok": False, "reason": "CANDIDATE6_MUTATED_DURING_OPVAL_WRITE"}

    return {
        "ok": True,
        "reason": "",
        "activation_id": OPVAL_ACTIVATION_ID,
        "sha256": body["sha256"],
        "trading_date": day,
        "bound_current_runtime_candidate": cid,
        "bound_current_runtime_candidate_sha": csha,
        "runtime_inventory_digest": body["runtime_inventory_digest"],
        "candidate_source_digest": body["candidate_source_digest"],
        "working_tree_matches_bound_candidate": True,
        "entry_sha": body.get("entry_sha"),
        "anchor_sha": body.get("anchor_sha"),
        "exit_sha": body.get("exit_v2_candidate_sha"),
        "strategy_sha": body.get("strategy_sha"),
        "selector_path": str(sel_dest),
        "manifest_path": str(man_dest),
        "identity": loaded,
        "selector": selector,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Write OPVAL current-trading-day identity from the current immutable runtime candidate")
    parser.add_argument("--trading-date", default="", help="YYYYMMDD; default is canonical OPVAL trading date")
    parser.add_argument("--dest-dir", default="", help="Override identity output directory (tests only)")
    parser.add_argument(
        "--runtime-candidate-selector",
        default="",
        help="Override current OPVAL runtime candidate selector (tests only)",
    )
    args = parser.parse_args(argv)
    trading_date = str(args.trading_date or "").strip()
    if not trading_date:
        trading_date, date_reason = resolve_opval_canonical_trading_date()
        if date_reason or not trading_date:
            print(f"REFUSE: {date_reason or 'OPVAL_TRADING_DATE_UNRESOLVED'}")
            return 2
    dest_dir = Path(args.dest_dir) if str(args.dest_dir or "").strip() else DEST_DIR
    sel = Path(args.runtime_candidate_selector) if str(args.runtime_candidate_selector or "").strip() else None
    result = write_opval_current_trading_day_identity(
        native_root=NATIVE,
        trading_date=trading_date,
        dest_dir=dest_dir,
        runtime_candidate_selector=sel,
    )
    if not result.get("ok"):
        print(f"REFUSE: {result.get('reason')}")
        return 2
    print(f"OPVAL_ID={OPVAL_ACTIVATION_ID}")
    print(f"OPVAL_SHA={result['sha256']}")
    print(f"TRADING_DATE={result['trading_date']}")
    print(f"RUNTIME_INVENTORY_DIGEST={result['runtime_inventory_digest']}")
    print(f"SOURCE_DIGEST={result['candidate_source_digest']}")
    print(f"STRATEGY_SHA={result.get('strategy_sha')}")
    print(f"ENTRY_SHA={result.get('entry_sha')}")
    print(f"ANCHOR_SHA={result.get('anchor_sha')}")
    print(f"EXIT_SHA={result.get('exit_sha')}")
    print(f"SELECTOR={result['selector_path']}")
    print(f"MANIFEST={result['manifest_path']}")
    print("V25_UNCHANGED=true")
    print("CANDIDATE6_UNCHANGED=true")
    print(f"BOUND_CURRENT_RUNTIME_CANDIDATE={result['bound_current_runtime_candidate']}")
    print(f"BOUND_CURRENT_RUNTIME_CANDIDATE_SHA={result['bound_current_runtime_candidate_sha']}")
    print(f"WORKING_TREE_MATCHES_BOUND_CANDIDATE={result['working_tree_matches_bound_candidate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
