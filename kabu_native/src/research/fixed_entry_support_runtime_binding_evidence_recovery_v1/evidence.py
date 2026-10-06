"""One-pass recovery of verified Kabu registration readbacks. No inferred membership."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from small_paper.v1r_native_entry_live import resolve_day_fixed_am_runtime_universe

ROOT = Path(__file__).resolve().parents[3]
CAPTURE = ROOT / "data" / "market_capture"
DATES = (
    "20260722", "20260723", "20260724", "20260727", "20260728", "20260729", "20260730", "20260731",
    "20260803", "20260804", "20260805", "20260806", "20260807", "20260810", "20260812", "20260813",
    "20260817", "20260818", "20260819", "20260820", "20260821", "20260824", "20260825", "20260826",
    "20260827", "20260828", "20260831", "20260901", "20260902", "20260903", "20260904", "20260907",
    "20260908", "20260909", "20260910",
)


def bare(symbol: Any) -> str:
    return str(symbol or "").replace(".T", "").strip().upper()


def _epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()


def _verified_put(record: dict[str, Any]) -> bool:
    return bool(
        record.get("ok")
        and record.get("verified")
        and record.get("put_executed")
        and record.get("actual_symbols")
        and str(record.get("verification_basis") or "") == "kabu_put_response"
    )


def load_windows() -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for day in DATES:
        path = CAPTURE / day / "ingress_register_api_events.jsonl"
        if not path.is_file():
            continue
        successes: list[tuple[datetime, tuple[str, ...]]] = []
        last_on_day: Optional[datetime] = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if str(record.get("trading_date") or "") != day:
                continue
            stamp = str(record.get("executed_at") or "")
            if not stamp:
                continue
            when = datetime.fromisoformat(stamp)
            if when.strftime("%Y%m%d") != day:
                continue
            if last_on_day is None or when > last_on_day:
                last_on_day = when
            if not _verified_put(record):
                continue
            symbols = tuple(sorted({bare(item) for item in record["actual_symbols"] if bare(item)}))
            if symbols:
                successes.append((when, symbols))
        successes.sort(key=lambda item: item[0])
        index = 0
        source = str(path).replace("\\", "/")
        while index < len(successes):
            start, symbols = successes[index]
            end_index = index
            while end_index + 1 < len(successes) and successes[end_index + 1][1] == symbols:
                end_index += 1
            if end_index + 1 < len(successes):
                end = successes[end_index + 1][0]
                inclusive = False
            else:
                end = last_on_day if last_on_day is not None and last_on_day >= successes[end_index][0] else successes[end_index][0]
                inclusive = True
            windows.append(
                {
                    "date": day,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "start_epoch": _epoch(start.isoformat()),
                    "end_epoch": _epoch(end.isoformat()),
                    "end_inclusive": inclusive,
                    "symbols": list(symbols),
                    "symbol_n": len(symbols),
                    "source": source,
                    "confirmations": end_index - index + 1,
                }
            )
            index = end_index + 1
    return windows


def registered_at(windows: list[dict[str, Any]], day: str, when: float) -> Optional[frozenset[str]]:
    for window in windows:
        if window["date"] != day:
            continue
        start = float(window["start_epoch"])
        end = float(window["end_epoch"])
        inside = start <= when <= end if window["end_inclusive"] else start <= when < end
        if inside:
            return frozenset(window["symbols"])
    return None


def dynamic40_by_day() -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    for day in DATES:
        resolved = resolve_day_fixed_am_runtime_universe(native_root=ROOT, trading_date=day)
        out[day] = frozenset(bare(symbol) for symbol in (resolved.get("symbols") or []) if bare(symbol))
    return out


def classify_intervals(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        by_day.setdefault(window["date"], []).append(window)

    def _row(interval: str, status: str, reason: str, source: str = "") -> dict[str, Any]:
        return {"interval": interval, "status": status, "reason": reason, "source": source}

    rows = [
        _row(
            "20260722",
            "IRRECOVERABLE_FROM_EXISTING_EVIDENCE",
            "No kabu_put_response actual_symbols. Capture registration_manifest.registration_verified is false.",
            str(CAPTURE / "20260722" / "registration_manifest.json"),
        ),
        _row("20260723", "IRRECOVERABLE_FROM_EXISTING_EVIDENCE", "No kabu_put_response actual_symbols."),
        _row("20260724", "IRRECOVERABLE_FROM_EXISTING_EVIDENCE", "No kabu_put_response actual_symbols."),
        _row(
            "20260727 before 09:25:26",
            "IRRECOVERABLE_FROM_EXISTING_EVIDENCE",
            "No verified readback before the first kabu_put_response.",
        ),
        _row(
            "20260804 before 09:00:02",
            "IRRECOVERABLE_FROM_EXISTING_EVIDENCE",
            "No verified readback before the first kabu_put_response.",
        ),
        _row(
            "20260812 cross-day executed_at",
            "IRRECOVERABLE_FROM_EXISTING_EVIDENCE",
            "Rows tagged trading_date 20260812 with executed_at on a later calendar day were excluded.",
            str(CAPTURE / "20260812" / "ingress_register_api_events.jsonl"),
        ),
        _row(
            "20260818 before 09:38:10",
            "IRRECOVERABLE_FROM_EXISTING_EVIDENCE",
            "No verified readback before the first kabu_put_response.",
        ),
        _row(
            "20260819",
            "IRRECOVERABLE_FROM_EXISTING_EVIDENCE",
            "Register attempts exist and none has verified actual_symbols.",
            str(CAPTURE / "20260819" / "ingress_register_api_events.jsonl"),
        ),
    ]
    recovered = by_day.get("20260812") or []
    if recovered:
        rows.append(
            _row(
                "20260812 same-calendar kabu_put_response",
                "PARTIAL_RECOVERED",
                f"{len(recovered)} windows whose executed_at falls on 20260812.",
                recovered[0]["source"] + " @ " + recovered[0]["start"],
            )
        )
    return rows
