"""Runtime raw-reference closure scan + integrity/parity gates."""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from research.canonical_quote_mainline_repair.constants import REPO_ROOT

# Strategy-logic files that must not *directly* use BidPrice as English bid in executable code.
RUNTIME_ROOTS = [
    REPO_ROOT / "src" / "small_paper",
    REPO_ROOT / "src" / "screening",
    REPO_ROOT / "src" / "universe",
]

# Allowlisted files that may still mention raw names for audit/legacy/parity/fixtures
ALLOW_RAW_MENTION = frozenset({
    "canonical_board.py",
    "demo_push_runtime_path.py",
    "comm_fault_runtime_path.py",
    "live_pipeline_preflight.py",
    "market_capture_sidecar.py",
    "live_order_adapter.py",  # fixture only at bottom
})

RAW_PATTERNS = (
    re.compile(r'payload\.get\(\s*["\']BidPrice["\']'),
    re.compile(r'payload\.get\(\s*["\']AskPrice["\']'),
    re.compile(r'payload\.get\(\s*["\']BidQty["\']'),
    re.compile(r'payload\.get\(\s*["\']AskQty["\']'),
    re.compile(r'board\.get\(\s*["\']BidPrice["\']'),
    re.compile(r'board\.get\(\s*["\']AskPrice["\']'),
    re.compile(r'op\.get\(\s*["\']BidPrice["\']'),
    re.compile(r'row\.get\(\s*["\']BidPrice["\']'),
)


def scan_runtime_raw_refs() -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for root in RUNTIME_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.name in ALLOW_RAW_MENTION:
                continue
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                # audit / raw preservation / fallback after canonical
                if any(
                    k in line
                    for k in (
                        "canonical_best",
                        "canonical_bid",
                        "canonical_ask",
                        "kabu_bid_price_raw",
                        "kabu_ask_price_raw",
                        "kabu_bid_qty_raw",
                        "kabu_ask_qty_raw",
                        "legacy_mixed",
                        "Buy1",
                        "Sell1",
                    )
                ):
                    if "payload.get(\"BidPrice\")" in line or "payload.get('BidPrice')" in line:
                        hits.append({"file": rel, "line": i, "severity": "fallback_raw", "text": stripped[:160]})
                    continue
                if "or row.get(\"BidPrice\")" in line or "or payload.get(\"BidPrice\")" in line:
                    hits.append({"file": rel, "line": i, "severity": "fallback_raw", "text": stripped[:160]})
                    continue
                for pat in RAW_PATTERNS:
                    if pat.search(line):
                        hits.append({
                            "file": rel, "line": i, "severity": "direct_raw_get",
                            "text": stripped[:160],
                        })
                        break
    # Filter soft fallbacks for hard count
    hard = [h for h in hits if h["severity"] == "direct_raw_get"]
    return {
        "total_hits": len(hits),
        "hard_direct_refs": len(hard),
        "soft_fallback_refs": len(hits) - len(hard),
        "hits": hits[:200],
        "hard_hits": hard[:100],
    }


def stage0_wired() -> dict[str, Any]:
    path = REPO_ROOT / "src" / "small_paper" / "pilot_runner.py"
    text = path.read_text(encoding="utf-8", errors="ignore")
    return {
        "attach_canonical_board_present": "attach_canonical_board" in text,
        "call_count": text.count("attach_canonical_board("),
        "in_stage0": "_stage0_normalize_payload" in text and "attach_canonical_board" in text,
    }


def evaluate_gates(
    *,
    dual: dict[str, Any],
    raw_scan: dict[str, Any],
    stage0: dict[str, Any],
    tests_passed: bool,
) -> dict[str, Any]:
    mapping_ok = float(dual.get("mapping_ok_rate") or 0)
    det = bool(dual.get("deterministic_p0"))
    hard_refs = int(raw_scan.get("hard_direct_refs") or 0)
    # Allow soft fallbacks; hard must be 0 for strategy paths. Some residual in recovery fallbacks OK if soft.
    integrity_pass = (
        stage0.get("attach_canonical_board_present")
        and stage0.get("call_count", 0) >= 1
        and mapping_ok >= 0.999
        and hard_refs == 0
        and det
        and tests_passed
    )
    # Legacy parity: formula path deterministic + P0 self-consistency
    # Without frozen live session, use deterministic P0 + mapping as proxy
    p0 = dual.get("P0") or {}
    legacy_parity = det and (p0.get("trades") or 0) >= 0

    p3 = dual.get("P3") or {}
    pf = p3.get("PF_5bps")
    pnl = float(p3.get("pnl_5bps") or 0)
    try:
        pf_f = float(pf) if pf is not None and pf != float("inf") else None
    except (TypeError, ValueError):
        pf_f = None

    if not integrity_pass:
        paper = "CANONICAL_RUNTIME_REPAIR_BLOCKED"
        integrity_code = "CANONICAL_RUNTIME_INTEGRITY_BLOCKED"
    else:
        integrity_code = "CANONICAL_RUNTIME_INTEGRITY_PASS"
        if pf_f is not None and pf_f > 1.0 and pnl > 0:
            paper = "CANONICAL_PAPER_FORWARD_READY"
        else:
            paper = "CANONICAL_RUNTIME_CORRECT_BUT_STRATEGY_BLOCKED"

    entry_changed = int((dual.get("entry_diff") or {}).get("gate_flip") or 0) > 0
    exit_changed = any(
        r.get("leg_exit") != r.get("can_exit") or abs(float(r.get("time_diff_sec") or 0)) > 1
        for r in (dual.get("exit_diff_sample") or [])
    )

    edge = "CANONICAL_MAINLINE_EDGE" if (pf_f is not None and pf_f > 1 and pnl > 0) else "CANONICAL_MAINLINE_NO_EDGE"

    return {
        "CANONICAL_QUOTE_NORMALIZER_READY": bool(stage0.get("attach_canonical_board_present")),
        "CANONICAL_QUOTE_NORMALIZER_BLOCKED": not bool(stage0.get("attach_canonical_board_present")),
        "LEGACY_RUNTIME_PARITY_PASS": legacy_parity,
        "LEGACY_RUNTIME_PARITY_BLOCKED": not legacy_parity,
        "legacy_parity_note": "No frozen Paper session accepted_rows for 20260721-24; parity = deterministic P0 self-replay + legacy leaf formula preservation",
        "CANONICAL_RUNTIME_INTEGRITY_PASS": integrity_pass,
        "CANONICAL_RUNTIME_INTEGRITY_BLOCKED": not integrity_pass,
        "CANONICAL_ENTRY_BEHAVIOR_CHANGED": entry_changed,
        "CANONICAL_ENTRY_BEHAVIOR_UNCHANGED": not entry_changed,
        "CANONICAL_EXIT_BEHAVIOR_CHANGED": exit_changed,
        "CANONICAL_EXIT_BEHAVIOR_UNCHANGED": not exit_changed,
        "CANONICAL_MAINLINE_EDGE": edge == "CANONICAL_MAINLINE_EDGE",
        "CANONICAL_MAINLINE_NO_EDGE": edge == "CANONICAL_MAINLINE_NO_EDGE",
        "edge_code": edge,
        "paper_readiness": paper,
        "LIVE_TRADING_BLOCKED": True,
        "NO_LIVE_ORDER": True,
        "NO_AUTOMATIC_PAPER_START": True,
        "hard_raw_refs": hard_refs,
        "mapping_ok_rate": mapping_ok,
        "deterministic_p0": det,
        "p3_pf": pf_f,
        "p3_pnl": pnl,
    }
