"""NK225/TOPIX futures historical vs live-capture readiness. No purchase. No download."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1 import C1_LAST, DEV_FIRST, DEV_LAST, FV_FIRST, PROSPECTIVE_FROM
from research.causal_driver_pb1.next_driver_selection.isolation import CAPTURE, JQUANTS, NATIVE


NEEDLES = ("nk225mini", "nk225_mini", "topix_fut", "topixfut", "future_ohlc_minute", "nk225")


def _capture_dates() -> list[str]:
    if not CAPTURE.is_dir():
        return []
    out = []
    for p in sorted(CAPTURE.iterdir()):
        if p.is_dir() and p.name.isdigit() and len(p.name) == 8:
            out.append(p.name)
    return out


def _local_hist_hits() -> list[dict[str, Any]]:
    hits = []
    roots = [
        JQUANTS,
        NATIVE / "data" / "datacube",
        NATIVE / "data" / "reference" / "datacube",
        NATIVE / "data" / "reference" / "futures",
        NATIVE / "data" / "reference" / "jquants",
    ]
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            name = p.name.lower()
            rel = str(p).replace("\\", "/")
            if "market_context_capture" in rel:
                continue
            if any(n in name for n in NEEDLES) or ("future" in name and p.suffix.lower() in {".csv", ".parquet", ".jsonl"}):
                hits.append({"path": rel, "size": int(p.stat().st_size), "suffix": p.suffix.lower()})
    return hits[:80]


def _futures_jsonl_n(day: str, code: str) -> int:
    path = CAPTURE / day / "futures" / f"{code}.jsonl"
    if not path.is_file():
        alt = CAPTURE / day / "futures" / code
        if alt.is_dir():
            n = 0
            for part in alt.glob("*.jsonl"):
                n += sum(1 for _ in part.open("r", encoding="utf-8", errors="ignore"))
            return n
        return 0
    return sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))


def audit_futures() -> dict[str, Any]:
    days = _capture_dates()
    dev_cap = [d for d in days if DEV_FIRST <= d <= DEV_LAST]
    c1_cap = [d for d in days if "20251127" <= d <= C1_LAST]
    fv_cap = [d for d in days if FV_FIRST <= d <= "20260911"]
    prosp_cap = [d for d in days if d >= PROSPECTIVE_FROM]
    gap_cap = [d for d in days if C1_LAST < d < PROSPECTIVE_FROM]
    sample = []
    for d in days[-8:]:
        sample.append(
            {
                "date": d,
                "nk225mini_jsonl_n": _futures_jsonl_n(d, "nk225mini"),
                "topix_jsonl_n": _futures_jsonl_n(d, "topix"),
            }
        )
    hist = _local_hist_hits()
    live_only = len(days) > 0 and len(dev_cap) == 0 and len(c1_cap) == 0
    ready = bool(hist) and any("datacube" in (h.get("path") or "").lower() or "future_ohlc_minute" in (h.get("path") or "").lower() for h in hist)
    # Live PUSH is not historical 1m with same semantics over Dev+C1.
    return {
        "historical_1m_source_exists": bool(ready),
        "live_capture_dir_n": len(days),
        "live_capture_dates": days,
        "dev_capture_n": len(dev_cap),
        "c1_capture_n": len(c1_cap),
        "fv_capture_n": len(fv_cap),
        "prospective_capture_n": len(prosp_cap),
        "between_c1_and_prospective_n": len(gap_cap),
        "local_non_capture_hits": hist,
        "live_sample_tail": sample,
        "timestamp_semantics_live": "Kabu_PUSH_received_at_JST",
        "bid_ask_or_trade_live": "board_style_PUSH",
        "available_at_historical_provable": False,
        "live_paper_source_exists": len(days) > 0,
        "runtime_acquisition_possible": True,
        "same_instrument_semantics_hist_and_runtime": False,
        "dev_historical_coverage": False,
        "c1_historical_coverage": False,
        "used_live_days_as_dev_c1_substitute": False,
        "prior_verdict": "HISTORICAL_FUTURES_SOURCE_NOT_AVAILABLE_V1",
        "etf_proxy_1321_1306": "PROXY_NOT_FUTURES_MUST_NOT_REVIVE",
        "datacube_product_exists_but_not_purchased": True,
        "status": "FUTURES_DRIVER_DATA_NOT_READY" if not ready else "READY",
        "ready_as_next_driver": False,
        "reason": (
            "No Development+C1 historical 1m NK225mini/TOPIX series with proven available_at on disk. "
            "Kabu PUSH capture is a few recent sessions only and must not substitute for Dev/C1 history. "
            "J-Quants DataCube is a paid product and is not present locally."
        ),
        "live_only_thin_sample": live_only,
    }
