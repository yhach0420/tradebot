"""ResearchObservationUniverse vs RuntimeTradeCandidateSet. Dynamic40 is not the 105."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from research.causal_driver_pb1 import (
    DYNAMIC40_ROLE,
    EXPECTED_UNIVERSE_MANIFEST_SHA256,
    EXPECTED_UNIVERSE_N,
    EXPECTED_UNIVERSE_SYMBOLS_ORDER_SHA256,
)
from research.causal_driver_pb1.contracts.errors import IdentityError
from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.isolation import UNIVERSE_MANIFEST, UNIVERSE_RUNTIME


@dataclass(frozen=True, slots=True)
class ResearchObservationUniverse:
    universe_id: str
    symbol_count: int
    ordered_symbols: tuple[str, ...]
    source_path: str
    source_sha256: str
    symbols_order_sha256: str
    created_from: str

    def __post_init__(self) -> None:
        if self.symbol_count != len(self.ordered_symbols):
            raise IdentityError("universe_count_mismatch")
        if self.symbol_count != EXPECTED_UNIVERSE_N:
            raise IdentityError("universe_not_105")
        object.__setattr__(self, "ordered_symbols", tuple(self.ordered_symbols))


@dataclass(frozen=True, slots=True)
class RuntimeTradeCandidateSet:
    """Session candidates. Never interchangeable with ResearchObservationUniverse."""

    role: str
    symbols: tuple[str, ...]
    not_registered_is_not_no_alpha: bool = True

    def __post_init__(self) -> None:
        if self.role == "RESEARCH_OBSERVATION_UNIVERSE":
            raise IdentityError("runtime_set_must_not_claim_research_universe")
        if self.role == "ALPHA_SOURCE":
            raise IdentityError("runtime_set_must_not_claim_alpha_source")
        object.__setattr__(self, "symbols", tuple(self.symbols))


@dataclass(frozen=True, slots=True)
class Dynamic40Gate:
    role: str
    alpha_source: bool
    research_observation_universe: bool
    module_path: str

    def __post_init__(self) -> None:
        if self.role != DYNAMIC40_ROLE:
            raise IdentityError("dynamic40_role_invalid")
        if self.alpha_source or self.research_observation_universe:
            raise IdentityError("dynamic40_must_not_promote")


def _symbols_from_manifest(doc: dict) -> list[str]:
    raw = doc.get("symbols")
    if isinstance(raw, list) and raw and all(isinstance(x, str) for x in raw):
        return [str(x) for x in raw]
    union = ((doc.get("pool") or {}).get("union")) if isinstance(doc.get("pool"), dict) else None
    if isinstance(union, list):
        out = []
        for row in union:
            if isinstance(row, dict) and row.get("symbol"):
                out.append(str(row["symbol"]))
        if out:
            return out
    raise IdentityError("universe_manifest_missing_symbols")


def load_research_observation_universe(*, path: Path | None = None) -> ResearchObservationUniverse:
    src = path or UNIVERSE_MANIFEST
    raw = src.read_bytes()
    file_sha = sha256_bytes(raw)
    if file_sha != EXPECTED_UNIVERSE_MANIFEST_SHA256:
        raise IdentityError("universe_manifest_sha_mismatch")
    doc = json.loads(raw.decode("utf-8"))
    symbols = _symbols_from_manifest(doc)
    order_sha = sha256_obj(symbols)
    if order_sha != EXPECTED_UNIVERSE_SYMBOLS_ORDER_SHA256:
        raise IdentityError("universe_symbols_order_sha_mismatch")
    return ResearchObservationUniverse(
        universe_id="FIXED_RESEARCH_OBSERVATION_UNIVERSE_105_V1",
        symbol_count=len(symbols),
        ordered_symbols=tuple(symbols),
        source_path="results/research/daytrade_historical_research_foundation_v2/research_pool_manifest.json",
        source_sha256=file_sha,
        symbols_order_sha256=order_sha,
        created_from="daytrade_historical_research_foundation_v2.research_pool_manifest",
    )


def dynamic40_gate() -> Dynamic40Gate:
    return Dynamic40Gate(
        role=DYNAMIC40_ROLE,
        alpha_source=False,
        research_observation_universe=False,
        module_path=str(UNIVERSE_RUNTIME).replace("\\", "/"),
    )


def runtime_from_dynamic40(symbols: list[str]) -> RuntimeTradeCandidateSet:
    return RuntimeTradeCandidateSet(
        role=DYNAMIC40_ROLE,
        symbols=tuple(symbols),
        not_registered_is_not_no_alpha=True,
    )
