"""DAY_FIXED_AM_RUNTIME_UNIVERSE_V1 — Ingress same-day AM registration binding."""
from __future__ import annotations

import csv
import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from small_paper.consumer_ack_state import resolve_resume_ack, write_ack_checkpoint
from small_paper.day_fixed_am_registration import (
    STALE_DESIRED_UNIVERSE,
    bind_same_day_am_desired_universe,
    canonical_membership_sha,
    load_am_canonical_50,
)
from small_paper.ingress_control_channel import read_desired_universe, write_desired_universe
from small_paper.market_capture_registration import (
    read_registration_manifest,
    write_registration_manifest,
)
from small_paper.market_ingress_service import MarketIngressService
from small_paper.v1r_native_entry_live import resolve_day_fixed_am_runtime_universe


def _am_syms(n: int = 50, *, start: int = 1000) -> list[str]:
    return [f"{start + i}" for i in range(n)]


def _write_am_csv(root: Path, day: str, symbols: list[str], *, dotted: bool = True) -> Path:
    path = root / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, raw in enumerate(symbols):
        bare = str(raw).split(".")[0]
        slot = "core" if i < 10 else "dynamic"
        rows.append(
            {
                "symbol": f"{bare}.T" if dotted else bare,
                "symbol_key": f"{bare}@1",
                "exchange": "1",
                "passed": "True",
                "source_bucket": "core10_discord" if slot == "core" else "vol_liq_dynamic40",
                "selected_reason": slot,
                "universe_slot": slot,
                "rank": str(i + 1),
                "am_pm_session": "am",
            }
        )
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


class _FakePush:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, int]]] = []

    def register(self, symbols_spec: list[tuple[str, int]]) -> dict:
        self.calls.append(list(symbols_spec))
        return {
            "RegistNum": len(symbols_spec),
            "Symbols": [{"Symbol": s, "Exchange": int(ex)} for s, ex in symbols_spec],
        }

    def unregister_all(self) -> dict:
        return {"RegistNum": 0, "Symbols": []}


def _svc(tmp_path: Path, day: str = "20260813") -> MarketIngressService:
    return MarketIngressService(
        native_root=tmp_path,
        trading_date=day,
        synthetic=False,
        enable_tcp_bus=False,
    )


def test_case_a_same_day_desired_put_membership_pass(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms()
    am_path = _write_am_csv(tmp_path, day, am, dotted=True)
    bind = bind_same_day_am_desired_universe(tmp_path, day)
    assert bind["ok"] is True
    assert bind["symbol_count"] == 50
    svc = _svc(tmp_path, day)
    push = _FakePush()
    svc._push_client = push
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    assert len(push.calls[0]) == 50
    assert [s for s, _ in push.calls[0]] == am
    man = read_registration_manifest(tmp_path)
    assert man["source_trading_date"] == day
    assert man["trading_date"] == day
    assert Path(man["source_path"]).name == am_path.name
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is True
    assert resolved["ingress_match"] is True
    assert resolved["symbol_count"] == 50
    svc.writer.close()
    svc.bus.stop()


def test_case_b_stale_desired_forbids_put(tmp_path: Path) -> None:
    write_desired_universe(
        tmp_path,
        symbols=_am_syms(start=2000),
        generation=99,
        trading_date="20260812",
    )
    svc = _svc(tmp_path, "20260813")
    push = _FakePush()
    svc._push_client = push
    out = svc._apply_desired_from_control_or_am(register=True)
    assert out.get("allow_put") is False
    assert out.get("reason") == STALE_DESIRED_UNIVERSE
    assert push.calls == []
    assert svc.registered_symbols == []
    req = read_desired_universe(tmp_path, requested_trading_date="20260813")
    assert req is not None
    assert req.get("rejected") is True
    assert req.get("reason") == STALE_DESIRED_UNIVERSE
    svc.writer.close()
    svc.bus.stop()


def test_case_c_stale_file_does_not_override_am_sot(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms(start=1000)
    stale = _am_syms(start=2000)
    _write_am_csv(tmp_path, day, am, dotted=True)
    write_desired_universe(tmp_path, symbols=stale, generation=1, trading_date="20260812")
    svc = _svc(tmp_path, day)
    push = _FakePush()
    svc._push_client = push
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    put_syms = [s for s, _ in push.calls[0]]
    assert put_syms == am
    assert put_syms != stale
    desired = json.loads((tmp_path / "runtime" / "ingress_desired_universe.json").read_text(encoding="utf-8"))
    assert desired["trading_date"] == day
    assert desired["symbols"] == am
    svc.writer.close()
    svc.bus.stop()


def test_case_d_same_count_different_membership_fail_closed(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms(start=1000)
    other = _am_syms(start=2000)
    _write_am_csv(tmp_path, day, am, dotted=True)
    write_registration_manifest(
        tmp_path,
        trading_date=day,
        symbols=other,
        generation_id="gen_mismatch",
        verified=True,
        extra={"source_trading_date": day, "actual_symbols": other, "actual_count": 50},
    )
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is False
    assert resolved["reason"] == "am_csv_ingress_membership_mismatch"
    assert resolved["am_count"] == 50
    assert resolved["ingress_count"] == 50


def test_case_e_dot_t_vs_bare_canonical_pass(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms()
    _write_am_csv(tmp_path, day, am, dotted=True)
    write_registration_manifest(
        tmp_path,
        trading_date=day,
        symbols=am,  # bare
        generation_id="gen_canon",
        verified=True,
        extra={"source_trading_date": day, "actual_symbols": am, "actual_count": 50},
    )
    loaded = load_am_canonical_50(tmp_path, day)
    assert loaded["ok"] is True
    assert loaded["symbols"] == am
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is True
    assert resolved["ingress_match"] is True


def test_case_f_prior_day_ack_not_reused(tmp_path: Path) -> None:
    p = tmp_path / "runtime" / "paper_consumer_ack_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_ack_checkpoint(
        tmp_path,
        ingress_session_id="ing_20260812_old",
        trading_date="20260812",
        last_ack_sequence=1031734,
        publisher_last_sequence=1031734,
        path=p,
    )
    ack, src = resolve_resume_ack(
        native_root=tmp_path,
        ingress_session_id="ing_20260813_new",
        trading_date="20260813",
        ingress_hint_ack=0,
        path=p,
    )
    assert ack == 0
    assert src == "stale_date_ignored"
    ack2, src2 = resolve_resume_ack(
        native_root=tmp_path,
        ingress_session_id="",
        trading_date="20260813",
        ingress_hint_ack=0,
        path=p,
    )
    assert ack2 == 0
    assert src2 == "stale_date_ignored"


def test_manifest_refuses_cross_day_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="STALE_DESIRED_UNIVERSE"):
        write_registration_manifest(
            tmp_path,
            trading_date="20260813",
            symbols=_am_syms(),
            generation_id="x",
            extra={"source_trading_date": "20260812"},
        )


def test_write_desired_refuses_mismatched_source_date(tmp_path: Path) -> None:
    out = write_desired_universe(
        tmp_path,
        symbols=_am_syms(),
        trading_date="20260813",
        source_trading_date="20260812",
    )
    assert out["rejected"] is True
    assert out["reason"] == STALE_DESIRED_UNIVERSE
    assert not (tmp_path / "runtime" / "ingress_desired_universe.json").is_file()


def test_canonical_membership_sha_order_invariant() -> None:
    a = _am_syms()
    b = list(reversed(a))
    assert canonical_membership_sha(a) == canonical_membership_sha(b)
    assert canonical_membership_sha(a) == hashlib.sha256(
        ",".join(sorted(a)).encode("utf-8")
    ).hexdigest()
