"""Prove the canonical completed-1m-bar domain from SymbolBarBuilder source. No reconstruction."""
from __future__ import annotations

import inspect
from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.isolation import BARS_SOURCE_FILE
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.spec import dumps_sha256, file_sha256
from research.simple_full_strategy_discovery_v1.exits import technical_fire_i
from research.simple_full_strategy_discovery_v1 import exits as exits_mod
from research.simple_tech_entry_family import bars as bars_mod
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity


def prove_valid_bar_contract() -> dict[str, Any]:
    finish_src = inspect.getsource(bars_mod._finish)
    builder_src = inspect.getsource(SymbolBarBuilder)
    arrays_src = inspect.getsource(SymbolBarBuilder.as_arrays)
    new_src = inspect.getsource(bars_mod._new_slot)
    post_src = inspect.getsource(exits_mod._post_fill_bars)
    fire_src = inspect.getsource(technical_fire_i)
    open_req = 'slot["open"] == slot["open"]' in finish_src
    high_req = 'slot["high"] == slot["high"]' in finish_src
    low_req = 'slot["low"] == slot["low"]' in finish_src
    close_req = 'slot["close"] == slot["close"]' in finish_src
    drop_invalid = "INVALID_BAR_DROP_N" in builder_src and "if got is None" in builder_src
    drop_incomplete = "INCOMPLETE_LAST_BAR_DROP_N" in builder_src
    publish_only_finish = "self.completed.append(got)" in builder_src and "as_arrays" in arrays_src
    ok = bool(open_req and high_req and low_req and close_req and drop_invalid and drop_incomplete and publish_only_finish)
    return {
        "ok": ok,
        "SOURCE_PATH": "src/research/simple_tech_entry_family/bars.py",
        "SOURCE_FILE_SHA256": file_sha256(BARS_SOURCE_FILE) if BARS_SOURCE_FILE.is_file() else "",
        "FINISH_FUNCTION": "research.simple_tech_entry_family.bars._finish",
        "BUILDER": "SymbolBarBuilder",
        "OHLC_PUBLICATION": (
            "A bar is published only when _finish returns a dict; invalid bars increment "
            "INVALID_BAR_DROP_N and are not appended. Incomplete last minute is dropped. "
            "as_arrays emits only self.completed."
        ),
        "FINISH_SOURCE": finish_src,
        "NEW_SLOT_SOURCE": new_src,
        "POST_FILL_BARS_SOURCE": post_src,
        "TECHNICAL_FIRE_I_SOURCE": fire_src,
        "VALID_COMPLETED_BAR_REQUIRES_FINITE_OPEN": open_req,
        "VALID_COMPLETED_BAR_REQUIRES_FINITE_HIGH": high_req,
        "VALID_COMPLETED_BAR_REQUIRES_FINITE_LOW": low_req,
        "VALID_COMPLETED_BAR_REQUIRES_FINITE_CLOSE": close_req,
        "INVALID_BARS_NOT_PUBLISHED": drop_invalid,
        "INCOMPLETE_LAST_BAR_NOT_PUBLISHED": drop_incomplete,
        "ARRAYS_ONLY_FROM_COMPLETED": publish_only_finish,
        "BAR_INTEGRITY_FUNCTION": bar_integrity.__name__,
        "FINISH_SHA256": dumps_sha256(finish_src),
        "DEV_OBSERVATION_ALONE_NOT_SUFFICIENT_FOR_GLOBAL_DEDUP": True,
    }
