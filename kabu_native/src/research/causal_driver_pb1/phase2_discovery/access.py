"""Stage-aware access ledger. C1 payload before candidate freeze is a hard block."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.time import JST


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S.%f+0900")


class StageLedger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.stage = "STAGE_A"
        self.frozen = False
        self.candidate_list_sha256: str | None = None
        self.c1_rows_read_before_candidate_freeze = 0
        self.c1_rows_read_total = 0
        self.c1_stock_payload_events_before_freeze = 0

    def record(self, kind: str, **payload: Any) -> None:
        row = {"seq": len(self.events), "t": _now(), "stage": self.stage, "kind": kind, **payload}
        self.events.append(row)

    def note_c1_stock_rows(self, n: int) -> None:
        n = int(n)
        if n <= 0:
            return
        if not self.frozen:
            self.c1_rows_read_before_candidate_freeze += n
            self.c1_stock_payload_events_before_freeze += 1
        self.c1_rows_read_total += n
        self.record("C1_STOCK_ROWS", n=n, before_freeze=not self.frozen)

    def freeze_candidates(self, sha: str, n: int) -> None:
        if self.frozen:
            raise RuntimeError("candidate_list_already_frozen")
        self.candidate_list_sha256 = str(sha)
        self.frozen = True
        self.record("CANDIDATE_FREEZE", candidate_list_sha256=sha, candidate_n=int(n))
        self.stage = "STAGE_A_COMPLETE"

    def enter_stage_b(self) -> None:
        if not self.frozen or not self.candidate_list_sha256:
            raise RuntimeError("stage_b_without_candidate_freeze")
        self.stage = "STAGE_B"
        self.record("STAGE_B_OPEN", candidate_list_sha256=self.candidate_list_sha256)

    def snapshot(self) -> dict[str, Any]:
        freeze_seq = next((e["seq"] for e in self.events if e.get("kind") == "CANDIDATE_FREEZE"), None)
        first_c1_seq = next((e["seq"] for e in self.events if e.get("kind") == "C1_STOCK_ROWS"), None)
        return {
            "STAGE_A_COMPLETE": self.frozen,
            "candidate_list_sha256": self.candidate_list_sha256,
            "C1_ROWS_READ_BEFORE_CANDIDATE_FREEZE": self.c1_rows_read_before_candidate_freeze,
            "c1_rows_read_total": self.c1_rows_read_total,
            "freeze_seq": freeze_seq,
            "first_c1_stock_seq": first_c1_seq,
            "c1_after_freeze": (first_c1_seq is None) or (freeze_seq is not None and first_c1_seq > freeze_seq),
            "event_n": len(self.events),
        }
