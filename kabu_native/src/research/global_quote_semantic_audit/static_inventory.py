"""Static reference inventory and A–E classification."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from research.global_quote_semantic_audit.constants import REPO_ROOT, SEARCH_TERMS

# Classification codes per audit spec:
# A: understands kabu raw semantics correctly
# B: normalized Buy1/Sell1 → canonical best_bid/ask
# C: BidPrice used as English best_bid (wrong)
# D: AskPrice used as English best_ask (wrong)
# E: unknown / not evaluable


@dataclass
class RefSite:
    file: str
    function: str
    line: int
    raw_source_field: str
    assumed_semantic: str
    actual_semantic: str
    classification: str
    runtime_reachable: bool
    paper_reachable: bool
    production_reachable: bool
    affected_metric: str
    severity: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")


# Curated high-confidence sites (manual audit). Grep scan supplements counts.
CURATED: list[RefSite] = [
    RefSite(
        "src/universe/filters.py", "calc_spread_bps", 128,
        "BidPrice,AskPrice", "BidPrice=best_bid, AskPrice=best_ask",
        "BidPrice=best_ask(Sell1), AskPrice=best_bid(Buy1)", "C+D",
        True, True, True, "spread_bps", "MEDIUM",
        "abs(ask-bid)/mid → magnitude OK; signed spread inverted",
    ),
    RefSite(
        "src/screening/morning_screen.py", "calc_board_imbalance", 492,
        "BidQty,AskQty,Buy*,Sell*", "BidQty+Buy=bid, AskQty+Sell=ask",
        "BidQty=Sell1Qty(ask); mixes true ask top into bid bucket", "C",
        True, True, True, "entry_order_book_imbalance", "CRITICAL",
        "Also double-counts tops with depth ladders",
    ),
    RefSite(
        "src/small_paper/board_imbalance_shadow.py", "compute_entry_order_book_imbalance_field", 109,
        "via calc_board_imbalance", "English bid pressure high = good",
        "Uses inverted/mixed imbalance", "C",
        True, True, True, "entry_order_book_imbalance,board_mid", "CRITICAL",
    ),
    RefSite(
        "src/small_paper/board_imbalance_shadow.py", "board_mid_token_active", 98,
        "imbalance tertile", "Board:mid from English imb",
        "Token from inverted imb", "C",
        True, True, True, "PBv2 board_mid gate", "CRITICAL",
    ),
    RefSite(
        "src/small_paper/entry_expectancy_score_shadow.py", "board_mid_or_high_required_for_v2", 163,
        "entry_order_book_imbalance", "Board mid/high English",
        "Upstream inverted", "C",
        True, True, True, "entry_score_v2 Board points", "CRITICAL",
    ),
    RefSite(
        "src/small_paper/entry_scan_controller.py", "_spread_bps_from_payload", 0,
        "BidPrice,AskPrice", "spread from labeled bid/ask",
        "abs width OK via calc_spread_bps", "C+D",
        True, True, True, "spread freshness", "LOW",
        "Magnitude-only; direction labels wrong",
    ),
    RefSite(
        "src/small_paper/entry_quality_guard.py", "compute_spread_bps_from_payload", 0,
        "BidPrice,AskPrice", "spread guard English",
        "abs width OK", "C+D",
        True, True, True, "EntryQualityGuard spread", "LOW",
    ),
    RefSite(
        "src/research/exposure_gate.py", "ExposureGate.evaluate_entry", 0,
        "spread_bps", "spread reject English",
        "abs width OK", "C+D",
        True, True, True, "ExposureGate", "LOW",
    ),
    RefSite(
        "src/small_paper/np_pre_entry_feature_logger.py", "extract_board_snap", 0,
        "BidQty,AskQty,calc_board_imbalance", "English imb snap",
        "Inverted/mixed", "C",
        True, True, False, "np_imb_*", "HIGH",
    ),
    RefSite(
        "src/small_paper/market_capture_writer.py", "extract_board_fields", 114,
        "BidPrice→bid, AskPrice→ask", "English bid/ask storage",
        "Stores kabu labels as English names", "C+D",
        True, True, False, "capture bid/ask columns", "HIGH",
    ),
    RefSite(
        "src/small_paper/realtime_board_exit_shadow.py", "_best_bid_ask", 152,
        "BidPrice,AskPrice", "best_bid/ask English",
        "Swapped vs true book", "C+D",
        True, True, False, "shadow EXIT quotes", "HIGH",
    ),
    RefSite(
        "src/small_paper/realtime_board_exit_shadow.py", "calc_bid_ask_imbalance", 134,
        "BidQty,AskQty", "BidQty/(Bid+Ask) = bid share",
        "Equals true ask share (1 - true bid imb)", "C",
        True, True, False, "board_collapse / profit_protect", "CRITICAL",
        "Top-of-book imbalance exactly inverted",
    ),
    RefSite(
        "src/small_paper/board_dynamic_trailing_shadow.py", "trailing_params_for_board_tier", 0,
        "entry_imbalance_percentile", "tier from English imb pct",
        "Upstream inverted imb → tier may flip", "C",
        True, True, True, "Board Dynamic Trailing", "HIGH",
    ),
    RefSite(
        "src/small_paper/live_order_dry_run_adapter.py", "_limit_entry_price", 97,
        "AskPrice", "buy limit at ask",
        "AskPrice=true best bid → wrong side for buy", "D",
        False, False, False, "dry-run limit price", "HIGH",
        "Dry-run only; live_order adapter not production-enabled in audit",
    ),
    RefSite(
        "src/small_paper/recovery_market_price.py", "candidate parse", 139,
        "BidPrice,AskPrice→mid", "mid from English",
        "mid of swapped labels still ≈ mid", "C+D",
        False, True, False, "recovery mid", "LOW",
        "Mid of swapped pair equals true mid",
    ),
    RefSite(
        "src/small_paper/daytrade_suitability.py", "spread_pct_from_payload", 0,
        "BidPrice,AskPrice", "spread pct",
        "abs-like; labels inverted", "C+D",
        True, True, False, "suitability spread", "LOW",
    ),
    RefSite(
        "src/research/volume_confirmed_impulse_entry/push_loader.py", "load PushTick", 153,
        "BidPrice→bid, AskPrice→ask", "English PushTick",
        "Inverted PushTick.bid/ask", "C+D",
        False, False, False, "research PushTick lineage", "CRITICAL",
    ),
    RefSite(
        "src/research/execution_grade_confirmation/board.py", "quote_from_record", 90,
        "Buy1→best_bid, Sell1→best_ask", "canonical English",
        "Matches true book", "B",
        False, False, False, "EGC AtomicQuote", "INFO",
    ),
    RefSite(
        "src/research/board_entry_features.py", "parquet builder", 140,
        "Buy*=bid, Sell*=ask", "English ladder",
        "Correct ladder", "A",
        False, False, False, "bid_pressure/ask_pressure", "INFO",
    ),
    RefSite(
        "src/small_paper/pilot_runner.py", "_stage0_normalize_payload", 4001,
        "passthrough payload", "no remap expected?",
        "No Bid↔Buy remap; downstream inverted", "E",
        True, True, True, "Stage0 normalize", "CRITICAL",
        "Passthrough — classification E for normalizer itself; enables C/D downstream",
    ),
    RefSite(
        "src/small_paper/live_feature_bridge.py", "LiveFeatureBridge.update", 0,
        "CurrentPrice/VWAP only", "N/A quotes",
        "Does not touch bid/ask", "A",
        True, True, True, "none (price-only)", "INFO",
    ),
    RefSite(
        "src/api/push_client.py", "PUSH ingest", 0,
        "raw BidPrice/AskPrice", "stores kabu raw",
        "Preserves kabu field names (correct raw)", "A",
        True, True, True, "raw payload", "INFO",
        "Raw retention is correct; misuse is in consumers",
    ),
    RefSite(
        "src/small_paper/kabu_execution_policy_shadow.py", "BoardSnapshotCompact", 0,
        "best_bid/best_ask params", "English names",
        "Depends on caller-supplied values", "E",
        False, True, False, "shadow fill price", "MEDIUM",
    ),
    RefSite(
        "src/universe/am_pm_universe.py", "board extract", 198,
        "BidQty,AskQty,BidPrice,AskPrice", "English board metrics",
        "Inverted labels", "C+D",
        False, False, False, "universe board metrics", "MEDIUM",
    ),
    RefSite(
        "src/universe/dynamic_build.py", "board metrics", 1002,
        "BidQty,AskQty,calc_spread_bps", "English",
        "Inverted labels; spread abs OK", "C+D",
        False, False, False, "dynamic universe", "MEDIUM",
    ),
]


def scan_term_inventory(src_roots: Iterable[Path] | None = None) -> list[dict[str, Any]]:
    roots = list(src_roots or [
        REPO_ROOT / "src",
        REPO_ROOT / "scripts",
        REPO_ROOT / "tests",
    ])
    rows: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "global_quote_semantic_audit" in str(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for term in SEARCH_TERMS:
                if term not in text:
                    continue
                # count occurrences
                n = len(re.findall(re.escape(term), text))
                if n <= 0:
                    continue
                rows.append({
                    "file": _rel(path),
                    "term": term,
                    "count": n,
                    "area": _area(_rel(path)),
                })
    return rows


def _area(rel: str) -> str:
    if rel.startswith("src/small_paper/"):
        return "runtime_paper"
    if rel.startswith("src/research/"):
        return "research"
    if rel.startswith("src/universe/") or rel.startswith("src/screening/"):
        return "universe_screening"
    if rel.startswith("src/api/"):
        return "api"
    if rel.startswith("tests/"):
        return "tests"
    if rel.startswith("scripts/"):
        return "scripts"
    return "other"


def resolve_line_numbers(sites: list[RefSite]) -> list[RefSite]:
    """Fill missing line numbers via simple search when line==0."""
    out: list[RefSite] = []
    for s in sites:
        if s.line and s.line > 0:
            out.append(s)
            continue
        path = REPO_ROOT / s.file.replace("/", "\\") if "\\" not in s.file else REPO_ROOT / s.file
        path = REPO_ROOT / Path(s.file)
        if not path.exists():
            out.append(s)
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        hit = 0
        needle = s.function.split(".")[-1]
        for i, line in enumerate(text, 1):
            if f"def {needle}" in line or needle in line and ("BidPrice" in line or "AskPrice" in line or "imbalance" in line):
                hit = i
                if f"def {needle}" in line:
                    break
        out.append(RefSite(**{**s.as_dict(), "line": hit or s.line}))
    return out


def classify_summary(sites: list[RefSite]) -> dict[str, Any]:
    def _has(code: str, s: RefSite) -> bool:
        return code in s.classification.split("+") or s.classification == code

    a = sum(1 for s in sites if s.classification == "A")
    b = sum(1 for s in sites if s.classification == "B")
    c = sum(1 for s in sites if _has("C", s))
    d = sum(1 for s in sites if _has("D", s))
    e = sum(1 for s in sites if s.classification == "E")
    runtime = [s for s in sites if s.runtime_reachable]
    runtime_cd = [s for s in runtime if _has("C", s) or _has("D", s)]
    return {
        "total_curated": len(sites),
        "class_A": a,
        "class_B": b,
        "class_C": c,
        "class_D": d,
        "class_E": e,
        "correct_refs": a + b,
        "inverted_refs": c + d,  # C or D sites (C+D counted once each in c and d separately)
        "inverted_site_count": len([s for s in sites if _has("C", s) or _has("D", s)]),
        "unknown_refs": e,
        "runtime_reachable": len(runtime),
        "runtime_reachable_cd": len(runtime_cd),
        "paper_reachable_cd": len([s for s in sites if s.paper_reachable and (_has("C", s) or _has("D", s))]),
        "production_reachable_cd": len([s for s in sites if s.production_reachable and (_has("C", s) or _has("D", s))]),
    }


def build_static_inventory() -> dict[str, Any]:
    sites = resolve_line_numbers(list(CURATED))
    inventory = scan_term_inventory()
    return {
        "search_inventory": inventory,
        "static_references": [s.as_dict() for s in sites],
        "summary": classify_summary(sites),
        "field_semantics": [
            {
                "field": "BidPrice",
                "kabu_meaning": "Sell1.Price = true best ask",
                "english_canonical": "NOT best_bid; map via Sell1 or invert",
                "common_misuse": "treated as best_bid",
            },
            {
                "field": "AskPrice",
                "kabu_meaning": "Buy1.Price = true best bid",
                "english_canonical": "NOT best_ask; map via Buy1 or invert",
                "common_misuse": "treated as best_ask",
            },
            {
                "field": "BidQty",
                "kabu_meaning": "Sell1.Qty = true ask qty",
                "english_canonical": "canonical_ask_qty",
                "common_misuse": "treated as bid_qty",
            },
            {
                "field": "AskQty",
                "kabu_meaning": "Buy1.Qty = true bid qty",
                "english_canonical": "canonical_bid_qty",
                "common_misuse": "treated as ask_qty",
            },
            {
                "field": "Buy1.Price/Qty",
                "kabu_meaning": "true best bid / bid qty",
                "english_canonical": "canonical_best_bid / canonical_bid_qty",
                "common_misuse": "ignored in favor of BidPrice",
            },
            {
                "field": "Sell1.Price/Qty",
                "kabu_meaning": "true best ask / ask qty",
                "english_canonical": "canonical_best_ask / canonical_ask_qty",
                "common_misuse": "ignored in favor of AskPrice",
            },
        ],
    }
