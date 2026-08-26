"""Canonical strategy-trade lifecycle integrity: EXIT ↔ FILL/ADMIT ↔ accepted ENTRY.

Each canonical trade must be traceable by stable identity. Operational validity
may remain VALID_SESSION while strategy_evaluation_eligible=false.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

CLASS_A = "A"
CLASS_B = "B"
CLASS_C = "C"

OWNER_CURRENT = "CURRENT_SESSION"
OWNER_PRE_ATTACH = "PRE_ATTACH_OR_RECOVERED"
OWNER_UNKNOWN = "UNKNOWN"
OWNER_ORPHAN = "ORPHAN"


def _f(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _s(value: Any) -> str:
    return str(value or "").strip()


def _bare(value: Any) -> str:
    try:
        from small_paper.v1r_live_dual_lane import canonical_symbol_key

        return canonical_symbol_key(value)
    except Exception:
        return str(value or "").split(".")[0].split("@")[0].strip().upper()


def load_trace_rows(output_dir: Optional[Path]) -> list[dict[str, Any]]:
    if output_dir is None:
        return []
    path = Path(output_dir) / "v1r_dual_lane_trace.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _primary_exits(traces: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in traces:
        if _s(raw.get("event")) != "EXIT_EXECUTED":
            continue
        if _s(raw.get("lane")) != "primary":
            continue
        out.append(dict(raw))
    return out


def _primary_admits(traces: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in traces:
        if _s(raw.get("event")) != "ADMIT":
            continue
        if _s(raw.get("lane")) != "primary":
            continue
        out.append(dict(raw))
    return out


def _price_key(value: Any) -> str:
    n = _f(value)
    if n is None:
        return ""
    return f"{n:.4f}"


def _ownership_from_row(row: Mapping[str, Any]) -> str:
    blob = " ".join(
        _s(row.get(k))
        for k in (
            "ownership",
            "ownership_class",
            "recovered",
            "source",
            "reason",
            "origin",
        )
    ).lower()
    if any(
        tok in blob
        for tok in (
            "pre_attach",
            "pre-attach",
            "recovered",
            "non_current",
            "other_runtime",
            "prior_session",
        )
    ):
        return OWNER_PRE_ATTACH
    if row.get("recovered") is True or row.get("pre_attach") is True:
        return OWNER_PRE_ATTACH
    return OWNER_CURRENT


def reconcile_canonical_lifecycle(
    traces: Sequence[Mapping[str, Any]],
    *,
    official_entry_count: int = 0,
) -> dict[str, Any]:
    """Trace each primary EXIT_EXECUTED back to ADMIT/FILL. Classify A/B/C."""
    exits = _primary_exits(traces)
    admits = _primary_admits(traces)
    unused = list(admits)
    rows: list[dict[str, Any]] = []
    class_a = class_b = class_c = 0
    unpaired = 0
    orphans = 0
    unknown = 0

    for idx, ex in enumerate(exits, start=1):
        symbol = _bare(ex.get("symbol"))
        fill_p = _f(ex.get("fill_price") if ex.get("fill_price") is not None else ex.get("entry_price"))
        exit_p = _f(ex.get("exit_price"))
        fill_key = _price_key(fill_p)
        match: Optional[dict[str, Any]] = None
        match_i = -1
        for i, ad in enumerate(unused):
            if _bare(ad.get("symbol")) != symbol:
                continue
            if fill_key and _price_key(ad.get("fill_price") if ad.get("fill_price") is not None else ad.get("entry_price")) != fill_key:
                continue
            match = ad
            match_i = i
            break
        if match_i >= 0:
            unused.pop(match_i)

        trade_id = _s(ex.get("trade_id") or ex.get("position_id") or f"{symbol}:{fill_key}:{_s(ex.get('exit_time') or ex.get('ts'))}")
        position_id = _s(ex.get("position_id") or (match or {}).get("position_id") or trade_id)
        ownership = _ownership_from_row(ex)
        if match is not None:
            klass = CLASS_A if ownership == OWNER_CURRENT else CLASS_B
            source_event = "ADMIT"
        elif ownership == OWNER_PRE_ATTACH:
            klass = CLASS_B
            source_event = _s(ex.get("source") or "EXIT_EXECUTED")
        else:
            klass = CLASS_C
            ownership = OWNER_ORPHAN
            source_event = _s(ex.get("event") or "EXIT_EXECUTED")

        if klass == CLASS_A:
            class_a += 1
        elif klass == CLASS_B:
            class_b += 1
            unknown += 0
        else:
            class_c += 1
            orphans += 1
            unpaired += 1

        rows.append(
            {
                "index": idx,
                "class": klass,
                "trade_id": trade_id,
                "position_id": position_id,
                "symbol": symbol,
                "entry_timestamp": _s((match or {}).get("fill_time") or (match or {}).get("ts") or ex.get("fill_time")),
                "exit_timestamp": _s(ex.get("exit_time") or ex.get("ts")),
                "source_event_identity": source_event,
                "ownership": ownership,
                "fill_price": fill_p,
                "exit_price": exit_p,
                "exit_reason": _s(ex.get("reason") or ex.get("exit_reason")),
                "admit_event": _s((match or {}).get("event")),
                "admit_ts": _s((match or {}).get("ts")),
            }
        )

    explainable_zero_official = class_a > 0 and int(official_entry_count or 0) == 0
    integrity_fail_reasons: list[str] = []
    if orphans:
        integrity_fail_reasons.append(f"orphan_exit_count={orphans}")
    if unpaired:
        integrity_fail_reasons.append(f"unpaired_canonical_trades={unpaired}")
    unknown_n = sum(1 for r in rows if r.get("ownership") == OWNER_UNKNOWN)
    if unknown_n:
        integrity_fail_reasons.append(f"unknown_ownership_trades={unknown_n}")
        unknown = unknown_n
    if class_c == 0 and class_a > 0 and int(official_entry_count or 0) == 0:
        # Class A: V1R ADMIT exists; official_entry_count is the old gate pipeline. Not D4.
        pass
    elif int(official_entry_count or 0) == 0 and len(exits) > 0 and class_a == 0 and class_b == 0:
        integrity_fail_reasons.append("canonical_trades_without_explainable_ownership")

    ok = not integrity_fail_reasons
    return {
        "ok": ok,
        "pass": ok,
        "unpaired_canonical_trades": unpaired,
        "orphan_exit_count": orphans,
        "unknown_ownership_trades": unknown,
        "class_A": class_a,
        "class_B": class_b,
        "class_C": class_c,
        "canonical_exit_count": len(exits),
        "v1r_primary_admit_count": len(admits),
        "lifecycle_official_entry_count": class_a,
        "official_entry_count_pipeline": int(official_entry_count or 0),
        "official_entry_count_explainable_class_A": bool(explainable_zero_official),
        "integrity_reasons": integrity_fail_reasons,
        "strategy_metric_exclusion_required": (not ok) or class_c > 0 or class_b > 0,
        "trades": rows,
    }


def attach_lifecycle_integrity(
    summary: dict[str, Any],
    events: Sequence[Mapping[str, Any]] | None = None,
    *,
    output_dir: Optional[Path] = None,
    traces: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    file_traces = list(traces or [])
    file_traces.extend(load_trace_rows(output_dir))
    if output_dir is None:
        try:
            from small_paper.canonical_summary import load_v1r_dual_lane_traces

            # already loaded via file traces if path known
            _ = load_v1r_dual_lane_traces
        except Exception:
            pass
    body = reconcile_canonical_lifecycle(
        file_traces,
        official_entry_count=int(summary.get("official_entry_count") or 0),
    )
    summary["lifecycle_integrity"] = {
        k: body.get(k)
        for k in (
            "ok",
            "pass",
            "unpaired_canonical_trades",
            "orphan_exit_count",
            "unknown_ownership_trades",
            "class_A",
            "class_B",
            "class_C",
            "lifecycle_official_entry_count",
            "official_entry_count_explainable_class_A",
            "integrity_reasons",
            "strategy_metric_exclusion_required",
        )
    }
    summary["lifecycle_integrity_trades"] = body.get("trades")
    summary["v1r_primary_admit_count"] = body.get("v1r_primary_admit_count")
    summary["lifecycle_official_entry_count"] = body.get("lifecycle_official_entry_count")
    if body.get("integrity_reasons"):
        summary["lifecycle_integrity_error"] = {"errors": list(body.get("integrity_reasons") or [])}
    else:
        summary.pop("lifecycle_integrity_error", None)
    return body
