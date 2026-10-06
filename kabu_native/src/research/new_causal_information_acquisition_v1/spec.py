"""Parent pin + standard-config freeze. No strategy. No old-day futures backfill."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.new_causal_information_acquisition_v1 import (
    ANALYSIS_ID,
    NEW_INFO_FROM_DAY,
    PARENT_ID,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
)
from research.new_causal_information_acquisition_v1.isolation import NATIVE, PARENT_OUT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "universe.py",
    "futures_resolve.py",
    "exclusive.py",
    "payload.py",
    "register_plan.py",
    "writer.py",
    "completeness.py",
    "launcher.py",
    "analyze.py",
    "interpret.py",
    "publish.py",
    "prepare.py",
    "live.py",
    "__main__.py",
)

PARENT_REPORT = PARENT_OUT / "report.json"
STANDARD_UNIVERSE = NATIVE / "src" / "universe" / "core10_dynamic40.py"
STANDARD_DAY_FIXED = NATIVE / "src" / "small_paper" / "day_fixed_am_registration.py"
STANDARD_INGRESS = NATIVE / "src" / "small_paper" / "market_ingress_service.py"
STANDARD_PAPER_LAUNCHER = NATIVE / "src" / "small_paper" / "v1r_paper_primary_launcher.py"
STANDARD_REGISTER = NATIVE / "src" / "api" / "kabu_register.py"


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def pin_parent() -> dict[str, Any]:
    prev = _load(PARENT_REPORT)
    d = dict(prev.get("decision") or {})
    verdict = str(d.get("VERDICT") or "")
    nxt = str(d.get("NEXT") or "")
    cand_n = d.get("CANDIDATE_STRATEGY_N")
    ok = (
        str(prev.get("ANALYSIS_ID") or "") == PARENT_ID
        and verdict == REQUIRED_PARENT_VERDICT
        and nxt == REQUIRED_PARENT_NEXT
        and cand_n == 0
        and d.get("RUNTIME_SHORT_FORBIDDEN") is True
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": PARENT_ID,
        "VERDICT": verdict,
        "NEXT": nxt,
        "CANDIDATE_STRATEGY_N": d.get("CANDIDATE_STRATEGY_N"),
        "KIND": prev.get("KIND"),
        "CASE": d.get("CASE"),
        "report": str(PARENT_REPORT),
    }


def standard_config_unchanged() -> dict[str, Any]:
    from universe.core10_dynamic40 import CORE_SLOTS, DYNAMIC_SLOTS, TOTAL_SLOTS
    from small_paper.day_fixed_am_registration import EXPECTED_SYMBOLS
    from api.kabu_register import KABU_PUSH_REGISTER_LIMIT

    ingress_txt = STANDARD_INGRESS.read_text(encoding="utf-8")
    paper_txt = STANDARD_PAPER_LAUNCHER.read_text(encoding="utf-8")
    day_txt = STANDARD_DAY_FIXED.read_text(encoding="utf-8")
    uni_txt = STANDARD_UNIVERSE.read_text(encoding="utf-8")
    forbidden_tokens = ("NK225mini", "Dynamic38", "market_context_capture", "NEW_INFO")
    leaks = []
    for label, txt in (
        ("ingress", ingress_txt),
        ("paper_launcher", paper_txt),
        ("day_fixed", day_txt),
        ("universe_slots", uni_txt),
    ):
        for tok in forbidden_tokens:
            if tok in txt:
                leaks.append(f"{label}:{tok}")
    exchange1 = "specs = [(s, 1) for s in self.desired_symbols]" in ingress_txt
    ok = (
        int(CORE_SLOTS) == 10
        and int(DYNAMIC_SLOTS) == 40
        and int(TOTAL_SLOTS) == 50
        and int(EXPECTED_SYMBOLS) == 50
        and int(KABU_PUSH_REGISTER_LIMIT) == 50
        and exchange1
        and not leaks
    )
    return {
        "ok": bool(ok),
        "CORE_SLOTS": int(CORE_SLOTS),
        "DYNAMIC_SLOTS": int(DYNAMIC_SLOTS),
        "TOTAL_SLOTS": int(TOTAL_SLOTS),
        "EXPECTED_SYMBOLS": int(EXPECTED_SYMBOLS),
        "KABU_PUSH_REGISTER_LIMIT": int(KABU_PUSH_REGISTER_LIMIT),
        "ingress_equity_exchange_1": bool(exchange1),
        "standard_file_leaks": leaks,
        "sha256": {
            "universe": file_sha256(STANDARD_UNIVERSE),
            "day_fixed": file_sha256(STANDARD_DAY_FIXED),
            "ingress": file_sha256(STANDARD_INGRESS),
            "paper_launcher": file_sha256(STANDARD_PAPER_LAUNCHER),
            "kabu_register": file_sha256(STANDARD_REGISTER),
        },
    }


def refuse_legacy_futures_backfill(day: str) -> None:
    d = str(day or "").replace("-", "")
    if d < NEW_INFO_FROM_DAY:
        raise ValueError(
            f"legacy futures backfill forbidden for {d}; NEW_INFO starts {NEW_INFO_FROM_DAY}"
        )


assert ANALYSIS_ID == "FUTURES_MARKET_CONTEXT_ACQUISITION_V1"
assert NEW_INFO_FROM_DAY == "20260911"
