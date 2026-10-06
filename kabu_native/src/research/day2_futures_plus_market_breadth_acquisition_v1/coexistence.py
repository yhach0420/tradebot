"""Coexistence: breadth GET-only vs futures 50-slot NEW_INFO. No register."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.market_breadth_leadership_acquisition_v1.safety import scan_package_source
from research.new_causal_information_acquisition_v1.exclusive import probe_exclusive
from research.new_causal_information_acquisition_v1.spec import standard_config_unchanged

EXCLUSIVE_SRC = Path(__file__).resolve().parents[1] / "new_causal_information_acquisition_v1" / "exclusive.py"


def refuse_research_outcome(day: str) -> None:
    d = str(day or "").replace("-", "")
    if d == "20260912":
        raise ValueError("20260912 weekend probe is schema proof only; forbidden as research outcome")


def coexistence_preflight(*, native_root, trading_date: str) -> dict[str, Any]:
    std = standard_config_unchanged()
    scan = scan_package_source()
    exclusive_txt = EXCLUSIVE_SRC.read_text(encoding="utf-8") if EXCLUSIVE_SRC.is_file() else ""
    exclusive_sees_breadth = "market_breadth_capture" in exclusive_txt or "breadth_collector" in exclusive_txt
    exclusive = probe_exclusive(native_root=native_root, trading_date=trading_date)
    return {
        "standard_paper_50_ok": bool(std.get("ok")),
        "CORE_SLOTS": std.get("CORE_SLOTS"),
        "DYNAMIC_SLOTS": std.get("DYNAMIC_SLOTS"),
        "TOTAL_SLOTS": std.get("TOTAL_SLOTS"),
        "breadth_register_mutation_n": scan.get("register_mutation_n"),
        "breadth_unregister_n": scan.get("unregister_n"),
        "breadth_sendorder_n": scan.get("sendorder_n"),
        "exclusive_treats_breadth_pid_as_competitor": bool(exclusive_sees_breadth),
        "futures_exclusive_ok_note": "breadth PID lives under data/market_breadth_capture; exclusive scans Paper/ingress/market_capture only",
        "exclusive_snapshot": {
            "ok": exclusive.get("ok"),
            "competitor_n": len(exclusive.get("competitors") or []),
            "unregister_on_conflict": exclusive.get("unregister_on_conflict"),
        },
        "concurrent_safe": bool(std.get("ok"))
        and int(scan.get("register_mutation_n") or 0) == 0
        and int(scan.get("sendorder_n") or 0) == 0
        and not exclusive_sees_breadth,
    }
