"""Capture writers under data/market_context_capture/. Reuse MarketCaptureWriter part files."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1.isolation import context_day_dir
from research.new_causal_information_acquisition_v1.payload import extract_futures_event
from small_paper.market_capture_writer import MarketCaptureWriter

JST = ZoneInfo("Asia/Tokyo")


def day_layout(day: str, *, native_root: Optional[Path] = None) -> dict[str, Path]:
    root = context_day_dir(str(day), native_root=native_root)
    return {
        "root": root,
        "manifest": root / "manifest.json",
        "stock": root / "stock",
        "futures": root / "futures",
        "nk225mini": root / "futures" / "nk225mini",
        "topix": root / "futures" / "topix",
        "nk225mini_jsonl": root / "futures" / "nk225mini.jsonl",
        "topix_jsonl": root / "futures" / "topix.jsonl",
        "station_state": root / "station_state",
        "prepared_manifest": root / "prepared_manifest.json",
        "live_manifest": root / "live_manifest.json",
        "new_info_pid": root / "new_info.pid",
        "status": root / "status.json",
    }


class ContextCaptureSession:
    """48 stocks + 2 futures. Stock records stay in canonical writer format. No field mix."""

    def __init__(self, *, native_root: Path, trading_date: str, session_id: str) -> None:
        self.native_root = Path(native_root)
        self.trading_date = str(trading_date)
        self.session_id = str(session_id)
        self.paths = day_layout(self.trading_date, native_root=self.native_root)
        for key in ("stock", "nk225mini", "topix", "station_state"):
            self.paths[key].mkdir(parents=True, exist_ok=True)
        self.stock_writer = MarketCaptureWriter(
            output_dir=self.paths["stock"],
            capture_session_id=f"{session_id}_stock",
        )
        self.nk_writer = MarketCaptureWriter(
            output_dir=self.paths["nk225mini"],
            capture_session_id=f"{session_id}_nk225mini",
        )
        self.topix_writer = MarketCaptureWriter(
            output_dir=self.paths["topix"],
            capture_session_id=f"{session_id}_topix",
        )
        self._named = {
            "NK225mini": self.paths["nk225mini_jsonl"].open("a", encoding="utf-8", newline="\n"),
            "TOPIX": self.paths["topix_jsonl"].open("a", encoding="utf-8", newline="\n"),
        }
        self.contract_by_symbol: dict[str, str] = {}

    def bind_contracts(self, by_code: Mapping[str, Mapping[str, Any]]) -> None:
        for code, freeze in by_code.items():
            sym = str(freeze.get("resolved_symbol") or "").split("@", 1)[0]
            if sym:
                self.contract_by_symbol[sym] = str(code)

    def start(self) -> None:
        self.stock_writer.start()
        self.nk_writer.start()
        self.topix_writer.start()

    def stop(self) -> None:
        self.stock_writer.stop()
        self.nk_writer.stop()
        self.topix_writer.stop()
        for fh in self._named.values():
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass

    def ingest_push(self, original_payload: Mapping[str, Any], *, received_at: str, mono_ns: Optional[int] = None) -> str:
        payload = dict(original_payload)
        raw_sym = str(payload.get("Symbol") or "").split("@", 1)[0]
        code = self.contract_by_symbol.get(raw_sym)
        if code:
            event = extract_futures_event(
                payload,
                received_at=received_at,
                future_code=code,
                resolved_symbol=raw_sym,
                exchange=int(payload.get("Exchange") or 2),
            )
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            self._named[code].write(line)
            if code == "NK225mini":
                self.nk_writer.enqueue(payload, mono_ns=mono_ns)
            else:
                self.topix_writer.enqueue(payload, mono_ns=mono_ns)
            return "futures"
        if "FutureCode" in payload:
            raise ValueError("stock ingest received FutureCode; mix forbidden")
        self.stock_writer.enqueue(payload, mono_ns=mono_ns)
        return "stock"

    def write_manifest(self, body: Mapping[str, Any]) -> Path:
        path = self.paths["manifest"]
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(body)
        payload["written_at"] = datetime.now(JST).isoformat(timespec="milliseconds")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
