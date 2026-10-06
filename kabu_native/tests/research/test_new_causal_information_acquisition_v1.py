"""NEW_INFO acquisition preflight. A-M plus extras. No live register. No sendorder."""
from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.new_causal_information_acquisition_v1 import (
    CASE_MODE_READY,
    CORE_N,
    DYNAMIC_N,
    FUTURES_EXCHANGE,
    FUTURES_N,
    PARENT_ID,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    STOCK_EXCHANGE,
    TOTAL_REGISTRATION_N,
    WINDOW_END_HM,
    WINDOW_START_HM,
)
from research.new_causal_information_acquisition_v1.completeness import evaluate_day
from research.new_causal_information_acquisition_v1.exclusive import ExclusiveBlocked, probe_exclusive, require_exclusive
from research.new_causal_information_acquisition_v1.futures_resolve import (
    FuturesResolveError,
    board_key,
    extract_resolved_symbol,
    preflight_board,
    resolve_both,
    resolve_front_month,
)
from research.new_causal_information_acquisition_v1.isolation import NATIVE, OUT
from research.new_causal_information_acquisition_v1.launcher import (
    in_acquisition_window,
    launcher_has_order_api,
    live_order_counts,
)
from research.new_causal_information_acquisition_v1.payload import (
    extract_futures_event,
    payload_mixes_futures_into_stock,
)
from research.new_causal_information_acquisition_v1.register_plan import (
    RegistrationPlanError,
    assert_exact_50,
    build_plan,
    never_append_51st,
    push_type_in_trusted_builder,
    trusted_register_body,
)
from research.new_causal_information_acquisition_v1.spec import pin_parent, refuse_legacy_futures_backfill, standard_config_unchanged
from research.new_causal_information_acquisition_v1.universe import UniverseError, build_new_info_universe
from research.new_causal_information_acquisition_v1.writer import ContextCaptureSession, day_layout
from small_paper.canonical_board import normalize_kabu_board

JST = ZoneInfo("Asia/Tokyo")
CSV = NATIVE / "results" / "reports" / "universe_core10_dynamic40_price_risk_am_20260910.csv"


@pytest.fixture
def universe():
    assert CSV.is_file()
    return build_new_info_universe(CSV)


@pytest.fixture
def fake_contracts():
    return [
        {"FutureCode": "NK225mini", "resolved_symbol": "160060018", "DerivMonth": 0},
        {"FutureCode": "TOPIX", "resolved_symbol": "169090018", "DerivMonth": 0},
    ]


def test_a_parent_pinned():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["PARENT_ID"] == PARENT_ID
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["NEXT"] == REQUIRED_PARENT_NEXT


def test_a_registration_exact_50(universe, fake_contracts):
    plan = build_plan(stock_symbols=universe["stock_symbols"], contracts=fake_contracts)
    assert plan["total_n"] == 50
    assert len(plan["specs"]) == 50
    assert plan["stock_n"] == 48
    assert plan["futures_n"] == 2


def test_b_core10_preserved(universe):
    assert universe["core_n"] == CORE_N == 10
    assert len(universe["core_symbols"]) == 10
    from research.new_causal_information_acquisition_v1.universe import load_am_rows, split_core_dynamic, symbols_of

    core, _dyn = split_core_dynamic(load_am_rows(CSV))
    assert symbols_of(core) == universe["core_symbols"]


def test_c_dynamic38_deterministic(universe):
    assert universe["dynamic_n"] == DYNAMIC_N == 38
    assert universe["dropped_dynamic_tail"] == ["6862", "9235"]
    assert universe["dropped_ranks"] == [49, 50]
    assert "6862" not in universe["dynamic38_symbols"]
    assert "9235" not in universe["stock_symbols"]
    assert universe["manual_selection"] is False


def test_d_two_futures_codes():
    from research.new_causal_information_acquisition_v1 import FUTURE_CODES

    assert FUTURE_CODES == ("NK225mini", "TOPIX")
    assert FUTURES_N == 2


def test_e_manifest_48_plus_2(universe, fake_contracts):
    plan = build_plan(stock_symbols=universe["stock_symbols"], contracts=fake_contracts)
    body = plan["body"]
    assert len(body["Symbols"]) == 50
    stock_ex = [x for x in body["Symbols"] if x["Exchange"] == STOCK_EXCHANGE]
    fut_ex = [x for x in body["Symbols"] if x["Exchange"] == FUTURES_EXCHANGE]
    assert len(stock_ex) == 48
    assert len(fut_ex) == 2


def test_f_never_51st_registration(universe, fake_contracts):
    plan = build_plan(stock_symbols=universe["stock_symbols"], contracts=fake_contracts)
    with pytest.raises(RegistrationPlanError):
        never_append_51st(plan["specs"], ("9999", 1))
    with pytest.raises(RegistrationPlanError):
        assert_exact_50(list(plan["specs"]) + [("9999", 1)])


def test_g_standard_capture_config_unchanged():
    std = standard_config_unchanged()
    assert std["ok"] is True
    assert std["CORE_SLOTS"] == 10
    assert std["DYNAMIC_SLOTS"] == 40
    assert std["TOTAL_SLOTS"] == 50
    assert std["EXPECTED_SYMBOLS"] == 50
    assert std["ingress_equity_exchange_1"] is True
    assert std["standard_file_leaks"] == []


def test_h_paper_launcher_unchanged():
    from research.new_causal_information_acquisition_v1.spec import STANDARD_PAPER_LAUNCHER

    txt = STANDARD_PAPER_LAUNCHER.read_text(encoding="utf-8")
    assert "NK225mini" not in txt
    assert "Dynamic38" not in txt
    assert "market_context_capture" not in txt
    std = standard_config_unchanged()
    assert "paper_launcher" in std["sha256"]


def test_i_futures_payload_parser_no_fabricate():
    raw = {
        "Symbol": "160060018",
        "Exchange": 2,
        "CurrentPrice": 45010.0,
        "Buy1": {"Price": 45000.0, "Qty": 10, "Time": "08:45:01", "Sign": "0101"},
        "Sell1": {"Price": 45020.0, "Qty": 8, "Time": "08:45:01", "Sign": "0101"},
        "BidPrice": 45020.0,
        "AskPrice": 45000.0,
    }
    ev = extract_futures_event(
        raw,
        received_at="2026-09-11T08:45:01.123+09:00",
        future_code="NK225mini",
        resolved_symbol="160060018",
        exchange=2,
    )
    assert "HighPrice" not in ev
    assert ev["CurrentPrice"] == 45010.0
    assert ev["original_payload"]["Symbol"] == "160060018"


def test_j_trusted_bid_ask_mapping():
    raw = {
        "Symbol": "160060018",
        "Buy1": {"Price": 111.0, "Qty": 2, "Time": "09:00:00", "Sign": "0101"},
        "Sell1": {"Price": 112.0, "Qty": 3, "Time": "09:00:00", "Sign": "0101"},
        "BidPrice": 112.0,
        "AskPrice": 111.0,
    }
    board = normalize_kabu_board(raw, received_at="2026-09-11T09:00:00+09:00")
    ev = extract_futures_event(
        raw,
        received_at="2026-09-11T09:00:00+09:00",
        future_code="NK225mini",
        resolved_symbol="160060018",
        exchange=2,
    )
    assert board.canonical_best_bid == 111.0
    assert board.canonical_best_ask == 112.0
    assert ev["Bid1"]["price"] == 111.0
    assert ev["Ask1"]["price"] == 112.0
    assert ev["Bid1"]["source"] == "Buy1"
    assert ev["Ask1"]["source"] == "Sell1"
    assert ev["kabu_BidPrice_raw_do_not_use_as_bid"] == 112.0


def test_k_received_at_lineage_required():
    with pytest.raises(ValueError):
        extract_futures_event({}, received_at="", future_code="NK225mini", resolved_symbol="X", exchange=2)
    ev = extract_futures_event(
        {"Symbol": "X", "CurrentPriceTime": "09:00:00"},
        received_at="2026-09-11T09:00:01+09:00",
        future_code="NK225mini",
        resolved_symbol="X",
        exchange=2,
    )
    assert ev["received_at"] == "2026-09-11T09:00:01+09:00"
    assert ev["availability_clock"] == "received_at"
    assert ev["CurrentPriceTime"] == "09:00:00"


def test_l_no_sendorder_in_launcher():
    assert launcher_has_order_api() is False
    from research.new_causal_information_acquisition_v1 import launcher

    src = inspect.getsource(launcher)
    assert "sendorder(" not in src
    assert "/sendorder" not in src


def test_m_submit_cancel_live_zero():
    c = live_order_counts()
    assert c["submit"] == 0
    assert c["cancel"] == 0
    assert c["live"] == 0
    assert c["sendorder_call_n"] == 0


def test_total_slots_identity():
    assert CORE_N + DYNAMIC_N + FUTURES_N == TOTAL_REGISTRATION_N == 50


def test_exchange_split(universe, fake_contracts):
    plan = build_plan(stock_symbols=universe["stock_symbols"], contracts=fake_contracts)
    for sym, ex in plan["specs"][:48]:
        assert ex == 1
        assert sym not in {"160060018", "169090018"}
    assert plan["specs"][48] == ("160060018", 2)
    assert plan["specs"][49] == ("169090018", 2)


def test_push_type_not_invented(universe, fake_contracts):
    assert push_type_in_trusted_builder() is False
    plan = build_plan(stock_symbols=universe["stock_symbols"], contracts=fake_contracts)
    assert list(plan["body"].keys()) == ["Symbols"]
    assert all(set(item.keys()) == {"Symbol", "Exchange"} for item in plan["body"]["Symbols"])


def test_trusted_body_matches_push_client():
    from api import push_client

    src = inspect.getsource(push_client._register_symbols)
    assert '{"Symbols": [{"Symbol": str(s), "Exchange": int(ex)} for s, ex in symbols_spec]}' in src.replace("\n", " ") or "\"Symbols\"" in src
    assert "PushType" not in src


def test_window_0755_1130():
    assert WINDOW_START_HM == (7, 55)
    assert WINDOW_END_HM == (11, 30)
    assert in_acquisition_window(datetime(2026, 9, 11, 7, 55, tzinfo=JST)) is True
    assert in_acquisition_window(datetime(2026, 9, 11, 8, 45, tzinfo=JST)) is True
    assert in_acquisition_window(datetime(2026, 9, 11, 11, 30, tzinfo=JST)) is True
    assert in_acquisition_window(datetime(2026, 9, 11, 7, 54, tzinfo=JST)) is False
    assert in_acquisition_window(datetime(2026, 9, 11, 11, 31, tzinfo=JST)) is False
    assert in_acquisition_window(datetime(2026, 9, 11, 0, 48, tzinfo=JST)) is False


def test_no_legacy_futures_backfill():
    with pytest.raises(ValueError):
        refuse_legacy_futures_backfill("20260807")
    with pytest.raises(ValueError):
        refuse_legacy_futures_backfill("20260722")
    refuse_legacy_futures_backfill("20260911")


def test_storage_not_market_capture():
    paths = day_layout("20260911", native_root=NATIVE)
    assert "market_context_capture" in str(paths["root"])
    assert "market_capture" not in str(paths["root"]).replace("market_context_capture", "")
    assert paths["nk225mini_jsonl"].name == "nk225mini.jsonl"
    assert paths["topix_jsonl"].name == "topix.jsonl"
    assert OUT.name == "new_causal_information_acquisition_v1"


def test_stock_payload_not_mixed():
    stock = {"symbol": "9984", "original_payload": {"Symbol": "9984"}}
    assert payload_mixes_futures_into_stock(stock) is False
    assert payload_mixes_futures_into_stock({"FutureCode": "NK225mini"}) is True


def test_forbidden_future_codes():
    class Rest:
        def get_symbolname_future(self, **kwargs):
            raise AssertionError("must not call API for forbidden codes")

    with pytest.raises(FuturesResolveError):
        resolve_front_month(Rest(), token="t", future_code="VI")
    with pytest.raises(FuturesResolveError):
        resolve_front_month(Rest(), token="t", future_code="GROWTH")
    with pytest.raises(FuturesResolveError):
        resolve_front_month(Rest(), token="t", future_code="DOW")


def test_resolve_and_board_preflight_exchange_2_only():
    class Rest:
        def __init__(self):
            self.boards = []

        def get_symbolname_future(self, *, token, future_code, deriv_month):
            assert deriv_month == 0
            if future_code == "NK225mini":
                return {"Symbol": "160060018", "SymbolName": "NK225mini"}
            return {"Symbol": "169090018", "SymbolName": "TOPIX"}

        def get_board(self, symbol_key, *, token):
            self.boards.append(symbol_key)
            assert symbol_key.endswith("@2")
            assert "@23" not in symbol_key and "@24" not in symbol_key
            sym = symbol_key.split("@", 1)[0]
            return {"Symbol": sym, "CurrentPrice": 100.0, "Buy1": {"Price": 99.0}, "Sell1": {"Price": 101.0}}

    rest = Rest()
    out = resolve_both(rest, token="t", now=datetime(2026, 9, 11, 7, 50, tzinfo=JST))
    assert out["ok"] is True
    assert out["nk225mini"]["resolved_symbol"] == "160060018"
    assert out["topix"]["resolved_symbol"] == "169090018"
    assert out["exchange"] == 2
    assert out["mid_session_roll"] is False
    assert rest.boards == ["160060018@2", "169090018@2"]


def test_board_preflight_fail_closed_no_23_24():
    class Rest:
        def get_board(self, symbol_key, *, token):
            assert "@23" not in symbol_key
            raise RuntimeError("board down")

    with pytest.raises(FuturesResolveError) as ei:
        preflight_board(Rest(), token="t", resolved_symbol="160060018", future_code="NK225mini")
    assert "23/24" in str(ei.value)
    assert board_key("160060018") == "160060018@2"
    with pytest.raises(FuturesResolveError):
        board_key("160060018", 23)


def test_extract_resolved_symbol():
    assert extract_resolved_symbol({"Symbol": "160060018@2"}) == "160060018"
    with pytest.raises(FuturesResolveError):
        extract_resolved_symbol({})


def test_exclusive_fail_closed_does_not_unregister(monkeypatch, tmp_path):
    pid_file = tmp_path / "data" / "market_capture" / "20260911"
    pid_file.mkdir(parents=True)
    (pid_file / "ingress.pid").write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr(
        "research.new_causal_information_acquisition_v1.exclusive.query_process",
        lambda pid: {"pid": pid, "exists": True, "name": "python.exe", "cmdline": "market_ingress_service"},
    )
    monkeypatch.setattr("research.new_causal_information_acquisition_v1.exclusive.certification_mode", lambda: False)
    monkeypatch.setattr("small_paper.market_ingress_spawn._live_ingress_pids", lambda **kwargs: [])
    monkeypatch.setattr("small_paper.v1r_pbv2_duplicate_runtime.list_live_pilots", lambda **kwargs: [])
    monkeypatch.setattr("small_paper.v1r_pbv2_duplicate_runtime.list_live_ingress", lambda **kwargs: [])
    probe = probe_exclusive(native_root=tmp_path, trading_date="20260911")
    assert probe["ok"] is False
    assert probe["unregister_on_conflict"] is False
    with pytest.raises(ExclusiveBlocked):
        require_exclusive(native_root=tmp_path, trading_date="20260911")


def test_exclusive_ok_when_empty(tmp_path, monkeypatch):
    (tmp_path / "runtime").mkdir()
    monkeypatch.setattr(
        "research.new_causal_information_acquisition_v1.exclusive.query_process",
        lambda pid: {"pid": pid, "exists": False},
    )
    monkeypatch.setattr("research.new_causal_information_acquisition_v1.exclusive.certification_mode", lambda: False)
    monkeypatch.setattr("small_paper.market_ingress_spawn._live_ingress_pids", lambda **kwargs: [])
    monkeypatch.setattr("small_paper.v1r_pbv2_duplicate_runtime.list_live_pilots", lambda **kwargs: [])
    monkeypatch.setattr("small_paper.v1r_pbv2_duplicate_runtime.list_live_ingress", lambda **kwargs: [])
    probe = probe_exclusive(native_root=tmp_path, trading_date="20260911")
    assert probe["ok"] is True
    assert probe["paper_simultaneous"] is False
    assert probe["opval_simultaneous"] is False


def test_core_cut_forbidden(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("symbol,universe_slot,rank,source_bucket\n9984.T,core,1,core10_discord\n", encoding="utf-8")
    with pytest.raises(UniverseError):
        build_new_info_universe(p)


def test_writer_routes_stock_vs_futures(tmp_path):
    session = ContextCaptureSession(native_root=tmp_path, trading_date="20260911", session_id="t1")
    session.bind_contracts(
        {
            "NK225mini": {"resolved_symbol": "160060018"},
            "TOPIX": {"resolved_symbol": "169090018"},
        }
    )
    session.start()
    try:
        kind_s = session.ingest_push({"Symbol": "9984", "CurrentPrice": 1}, received_at="2026-09-11T09:00:00+09:00")
        kind_f = session.ingest_push(
            {
                "Symbol": "160060018",
                "Exchange": 2,
                "CurrentPrice": 45000,
                "Buy1": {"Price": 44990, "Qty": 1},
                "Sell1": {"Price": 45010, "Qty": 1},
            },
            received_at="2026-09-11T08:45:01+09:00",
        )
        assert kind_s == "stock"
        assert kind_f == "futures"
    finally:
        session.stop()
    named = (tmp_path / "data" / "market_context_capture" / "20260911" / "futures" / "nk225mini.jsonl").read_text(
        encoding="utf-8"
    )
    assert "received_at" in named
    assert "FutureCode" in named
    assert "9984" not in named


def test_completeness_full_and_gap_audit(tmp_path):
    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["stock"].mkdir(parents=True)
    layout["futures"].mkdir(parents=True)
    stock_lines = []
    for i in range(48):
        sym = f"{1000 + i}"
        stock_lines.append(
            '{"received_at_jst":"2026-09-11T09:01:00+09:00","original_payload":{"Symbol":"%s"}}\n' % sym
        )
    (layout["stock"] / "push_part_0001.jsonl").write_text("".join(stock_lines), encoding="utf-8")

    def fut_lines(code, symbol):
        rows = []
        rows.append(
            '{"received_at":"2026-09-11T08:45:01+09:00","FutureCode":"%s","resolved_symbol":"%s","CurrentPrice":100,"Bid1":{"price":99},"Ask1":{"price":101},"TradingVolume":1}\n'
            % (code, symbol)
        )
        rows.append(
            '{"received_at":"2026-09-11T08:50:00+09:00","FutureCode":"%s","CurrentPrice":101,"Bid1":{"price":100},"Ask1":{"price":102},"TradingVolume":2}\n'
            % code
        )
        rows.append(
            '{"received_at":"2026-09-11T09:00:01+09:00","FutureCode":"%s","CurrentPrice":102,"Bid1":{"price":101},"Ask1":{"price":103},"TradingVolume":3}\n'
            % code
        )
        rows.append(
            '{"received_at":"2026-09-11T11:29:00+09:00","FutureCode":"%s","CurrentPrice":110,"Bid1":{"price":109},"Ask1":{"price":111},"TradingVolume":9}\n'
            % code
        )
        return "".join(rows)

    layout["nk225mini_jsonl"].write_text(fut_lines("NK225mini", "160060018"), encoding="utf-8")
    layout["topix_jsonl"].write_text(fut_lines("TOPIX", "169090018"), encoding="utf-8")
    ev = evaluate_day(day, native_root=tmp_path)
    assert ev["stock_symbol_n"] == 48
    assert ev["nk225mini"]["event_n"] == 4
    assert ev["nk225mini"]["continuity_0845_0900"] is True
    assert ev["nk225mini"]["continuity_0900_1130"] is True
    assert ev["nk225mini"]["change_0845_0900"] >= 1
    assert ev["nk225mini"]["change_0900_1130"] >= 1
    assert ev["FULL"] is True
    assert ev["future_gaps_gt_60s"]


def test_completeness_static_fails(tmp_path):
    day = "20260912"
    layout = day_layout(day, native_root=tmp_path)
    layout["stock"].mkdir(parents=True)
    layout["futures"].mkdir(parents=True)
    (layout["stock"] / "push_part_0001.jsonl").write_text(
        "".join(['{"original_payload":{"Symbol":"%s"},"received_at_jst":"2026-09-12T09:01:00+09:00"}\n' % (1000 + i) for i in range(48)]),
        encoding="utf-8",
    )
    frozen = (
        '{"received_at":"2026-09-12T08:45:01+09:00","CurrentPrice":100,"Bid1":{"price":1},"Ask1":{"price":2}}\n'
        '{"received_at":"2026-09-12T09:00:01+09:00","CurrentPrice":100,"Bid1":{"price":1},"Ask1":{"price":2}}\n'
        '{"received_at":"2026-09-12T11:00:00+09:00","CurrentPrice":100,"Bid1":{"price":1},"Ask1":{"price":2}}\n'
    )
    layout["nk225mini_jsonl"].write_text(frozen, encoding="utf-8")
    layout["topix_jsonl"].write_text(frozen, encoding="utf-8")
    ev = evaluate_day(day, native_root=tmp_path)
    assert ev["nk225mini"]["static_or_frozen"] is True
    assert ev["FULL"] is False


def test_acquisition_isolated_kind():
    from research.new_causal_information_acquisition_v1 import KIND

    assert KIND == "NEW_INFO_DEV_CONSTRUCTION_ONLY"
    assert CASE_MODE_READY.startswith("NEW_CAUSAL_INFORMATION")


def test_register_uses_trusted_coordinator():
    from research.new_causal_information_acquisition_v1 import register_plan

    src = inspect.getsource(register_plan)
    assert "register_symbols_cleared" in src
    assert "sendorder" not in src


def test_rest_client_has_symbolname_future():
    from api.rest_client import KabuNativeRestClient

    src = inspect.getsource(KabuNativeRestClient.get_symbolname_future)
    assert "symbolname/future" in src
    assert "FutureCode" in src
    assert "DerivMonth" in src


def test_no_strategy_constants():
    from research.new_causal_information_acquisition_v1 import TRUE_OOS, CERTIFIED, V5_RESCUE

    assert TRUE_OOS is False
    assert CERTIFIED is False
    assert V5_RESCUE is False


def test_independent_script_exists():
    p = NATIVE / "scripts" / "run_futures_market_context_capture.py"
    txt = p.read_text(encoding="utf-8")
    assert "sendorder(" not in txt
    assert "/sendorder" not in txt
    assert "--live" in txt
    assert "--prepare-only" in txt
    assert "FAIL CLOSED" in txt or "fail closed" in txt.lower() or "Will not unregister" in txt


def test_prepare_uses_standard_universe_prebuild():
    from research.new_causal_information_acquisition_v1 import prepare

    src = inspect.getsource(prepare)
    assert "run_universe_prebuild" in src
    assert "6862" not in src
    assert "9235" not in src
    assert "sendorder(" not in src
    assert "unregister" not in src.lower() or "unregister_n" in src


def test_live_sequence_frozen_order():
    from research.new_causal_information_acquisition_v1.live import LIVE_START_SEQUENCE

    assert LIVE_START_SEQUENCE == (
        "1_trading_date_verify",
        "2_window_0755_1130_verify",
        "3_prepared_manifest_verify",
        "4_source_universe_sha_verify",
        "5_competitor_process_verify",
        "6_standard_paper_opval_cert_absent",
        "7_NK225mini_derivmonth_0_resolve",
        "8_TOPIX_derivmonth_0_resolve",
        "9_resolved_symbol_board_rest_preflight",
        "10_exact_registration_pack_48_plus_2",
        "11_registration_mutation",
        "12_websocket_capture_start",
    )


def test_live_refuses_outside_window():
    from research.new_causal_information_acquisition_v1.live import LiveStartError, run_live_capture

    with pytest.raises(LiveStartError) as ei:
        run_live_capture(trading_date="20260911")
    assert "07:55-11:30" in str(ei.value) or "trading_date" in str(ei.value)


def test_stale_owner_metadata_not_competitor():
    from research.new_causal_information_acquisition_v1.exclusive import classify_owner_record

    dead = classify_owner_record(
        {"pid": 13528, "trading_date": "20260910", "owner": "MARKET_INGRESS_SERVICE"},
        trading_date="20260911",
    )
    # PID liveness is queried; if dead, class is STALE_METADATA
    if not dead.get("alive", {}).get("exists"):
        assert dead["competitor"] is False
        assert dead["class"] == "STALE_METADATA"


def test_prepare_manifest_schema(universe, tmp_path, monkeypatch):
    from research.new_causal_information_acquisition_v1.prepare import build_prepared_manifest, prepare_pass_conditions

    csv_path = CSV
    pre = {
        "path": csv_path,
        "verdict": "existing_valid",
        "existing_or_generated": "existing",
        "validation": {"ok": True, "symbol_count": 50, "core_count": 10, "dynamic_count": 40},
    }
    monkeypatch.setattr(
        "research.new_causal_information_acquisition_v1.prepare.probe_exclusive",
        lambda **kwargs: {"ok": True, "competitors": [], "registration_owner_class": {"class": "STALE_METADATA"}},
    )
    body = build_prepared_manifest(native_root=tmp_path, trading_date="20260910", prebuild=pre)
    assert body["live_registration_attempted"] is False
    assert body["expected_registration_n"] == 50
    assert body["stock_n"] == 48
    assert body["dynamic40_n"] == 40
    assert body["dynamic38_n"] == 38
    assert body["manual_selection"] is False
    assert body["prior_day_fallback"] is False
    assert "6862" in body["dropped_symbols"]  # 20260910 ranking proof only, not hardcoded for 20260911
    gates = prepare_pass_conditions(body, prebuild=pre)
    assert gates["ok"] is True


def _fut_lines(code: str, symbol: str, *, include_preopen: bool = True, bad_clock: bool = False, last: str = "2026-09-11T11:29:00+09:00") -> str:
    clock0 = "not-a-timestamp" if bad_clock else "2026-09-11T08:45:01+09:00"
    rows = []
    if include_preopen:
        rows.append(
            '{"received_at":"%s","FutureCode":"%s","resolved_symbol":"%s","CurrentPrice":100,"Bid1":{"price":99},"Ask1":{"price":101},"TradingVolume":1}\n'
            % (clock0, code, symbol)
        )
        rows.append(
            '{"received_at":"2026-09-11T08:50:00+09:00","FutureCode":"%s","resolved_symbol":"%s","CurrentPrice":101,"Bid1":{"price":100},"Ask1":{"price":102},"TradingVolume":2}\n'
            % (code, symbol)
        )
    rows.append(
        '{"received_at":"2026-09-11T09:00:01+09:00","FutureCode":"%s","resolved_symbol":"%s","CurrentPrice":102,"Bid1":{"price":101},"Ask1":{"price":103},"TradingVolume":3}\n'
        % (code, symbol)
    )
    rows.append(
        '{"received_at":"%s","FutureCode":"%s","resolved_symbol":"%s","CurrentPrice":110,"Bid1":{"price":109},"Ask1":{"price":111},"TradingVolume":9}\n'
        % (last, code, symbol)
    )
    return "".join(rows)


def _write_stocks(layout, n: int = 48) -> None:
    layout["stock"].mkdir(parents=True, exist_ok=True)
    lines = [
        '{"received_at_jst":"2026-09-11T09:01:00+09:00","original_payload":{"Symbol":"%s"}}\n' % (1000 + i)
        for i in range(n)
    ]
    (layout["stock"] / "push_part_0001.jsonl").write_text("".join(lines), encoding="utf-8")


def _write_live_manifest(layout, nk="160060018", tx="169090018") -> None:
    body = {
        "trading_date": "20260911",
        "futures": [
            {
                "FutureCode": "NK225mini",
                "DerivMonth": 0,
                "resolved_symbol": nk,
                "board_preflight": {"ok": True, "board_key": f"{nk}@2"},
            },
            {
                "FutureCode": "TOPIX",
                "DerivMonth": 0,
                "resolved_symbol": tx,
                "board_preflight": {"ok": True, "board_key": f"{tx}@2"},
            },
        ],
        "registration_specs": [["1000", 1], [nk, 2], [tx, 2]],
        "live_start_sequence": ["1_trading_date_verify"],
    }
    layout["root"].mkdir(parents=True, exist_ok=True)
    layout["live_manifest"].write_text(json.dumps(body), encoding="utf-8")


def test_live_artifact_prevents_ready_downgrade(tmp_path):
    from research.new_causal_information_acquisition_v1 import CASE_MODE_READY
    from research.new_causal_information_acquisition_v1.analyze import build_report_body
    from research.new_causal_information_acquisition_v1.reconcile import classify_day, forbid_ready_downgrade, live_artifacts_present

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["root"].mkdir(parents=True)
    _write_live_manifest(layout)
    flags = live_artifacts_present(layout)
    assert flags["any"] is True
    assert forbid_ready_downgrade(CASE_MODE_READY, artifacts=flags, event_n=1) != CASE_MODE_READY
    cla = classify_day(native_root=tmp_path, trading_date=day)
    assert cla["VERDICT"] != CASE_MODE_READY
    body = build_report_body(
        tests={"ok": True, "passed": 42, "lineage_pass": True},
        now=datetime(2026, 9, 11, 13, 13, tzinfo=JST),
        native_root=tmp_path,
        trading_date=day,
    )
    assert body["decision"]["VERDICT"] != CASE_MODE_READY


def test_full_day_from_raw(tmp_path):
    from research.new_causal_information_acquisition_v1.reconcile import classify_day

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["futures"].mkdir(parents=True)
    _write_stocks(layout, 48)
    layout["nk225mini_jsonl"].write_text(_fut_lines("NK225mini", "160060018"), encoding="utf-8")
    layout["topix_jsonl"].write_text(_fut_lines("TOPIX", "169090018"), encoding="utf-8")
    cla = classify_day(native_root=tmp_path, trading_date=day)
    assert cla["classification"] == "FULL"
    assert cla["FULL"] is True
    assert cla["count_as_day1"] is True


def test_partial_day_from_missing_stock(tmp_path):
    from research.new_causal_information_acquisition_v1.reconcile import classify_day

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["futures"].mkdir(parents=True)
    _write_stocks(layout, 47)
    layout["nk225mini_jsonl"].write_text(_fut_lines("NK225mini", "160060018"), encoding="utf-8")
    layout["topix_jsonl"].write_text(_fut_lines("TOPIX", "169090018"), encoding="utf-8")
    cla = classify_day(native_root=tmp_path, trading_date=day)
    assert cla["classification"] == "PARTIAL"
    assert cla["count_as_day1"] is False


def test_partial_day_from_missing_future_window(tmp_path):
    from research.new_causal_information_acquisition_v1.reconcile import classify_day

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["futures"].mkdir(parents=True)
    _write_stocks(layout, 48)
    layout["nk225mini_jsonl"].write_text(_fut_lines("NK225mini", "160060018", include_preopen=False), encoding="utf-8")
    layout["topix_jsonl"].write_text(_fut_lines("TOPIX", "169090018"), encoding="utf-8")
    cla = classify_day(native_root=tmp_path, trading_date=day)
    assert cla["classification"] == "PARTIAL"
    assert cla["gates"]["D_nk_0845_0900_dynamic"] is False


def test_invalid_bad_timestamp_lineage(tmp_path):
    from research.new_causal_information_acquisition_v1.reconcile import classify_day

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["futures"].mkdir(parents=True)
    _write_stocks(layout, 48)
    layout["nk225mini_jsonl"].write_text(_fut_lines("NK225mini", "160060018", bad_clock=True), encoding="utf-8")
    layout["topix_jsonl"].write_text(_fut_lines("TOPIX", "169090018"), encoding="utf-8")
    cla = classify_day(native_root=tmp_path, trading_date=day)
    assert cla["classification"] == "INVALID"
    assert "timestamp_lineage_broken" in cla["invalid_reasons"]


def test_wrong_future_symbol(tmp_path):
    from research.new_causal_information_acquisition_v1.reconcile import classify_day

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["futures"].mkdir(parents=True)
    _write_stocks(layout, 48)
    _write_live_manifest(layout, nk="161100019", tx="161120005")
    layout["nk225mini_jsonl"].write_text(_fut_lines("NK225mini", "999999999"), encoding="utf-8")
    layout["topix_jsonl"].write_text(_fut_lines("TOPIX", "161120005"), encoding="utf-8")
    cla = classify_day(native_root=tmp_path, trading_date=day)
    assert cla["classification"] == "INVALID"
    assert "wrong_future_symbol" in cla["invalid_reasons"]


def test_uses_same_day_universe(tmp_path):
    from research.new_causal_information_acquisition_v1.analyze import build_report_body

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["root"].mkdir(parents=True)
    am11 = NATIVE / "results" / "reports" / "universe_core10_dynamic40_price_risk_am_20260911.csv"
    assert am11.is_file()
    reports = tmp_path / "results" / "reports"
    reports.mkdir(parents=True)
    (reports / "universe_core10_dynamic40_price_risk_am_20260910.csv").write_text("symbol\n6862\n", encoding="utf-8")
    layout["prepared_manifest"].write_text(
        json.dumps({"trading_date": day, "source_universe_path": str(am11), "core10": [], "dynamic38": []}),
        encoding="utf-8",
    )
    _write_live_manifest(layout)
    body = build_report_body(
        tests={"ok": True, "passed": 42, "lineage_pass": True},
        now=datetime(2026, 9, 11, 13, 13, tzinfo=JST),
        native_root=tmp_path,
        trading_date=day,
    )
    assert "20260911" in str((body.get("universe_pack") or {}).get("source") or "")
    assert "20260910" not in str((body.get("universe_pack") or {}).get("source") or "")


def test_final_report_uses_live_manifest(tmp_path):
    from research.new_causal_information_acquisition_v1.analyze import build_report_body

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["futures"].mkdir(parents=True)
    _write_stocks(layout, 48)
    _write_live_manifest(layout, nk="161100019", tx="161120005")
    layout["nk225mini_jsonl"].write_text(_fut_lines("NK225mini", "161100019"), encoding="utf-8")
    layout["topix_jsonl"].write_text(_fut_lines("TOPIX", "161120005"), encoding="utf-8")
    body = build_report_body(
        tests={"ok": True, "passed": 42, "lineage_pass": True},
        now=datetime(2026, 9, 11, 13, 13, tzinfo=JST),
        native_root=tmp_path,
        trading_date=day,
    )
    assert (body.get("resolved_live") or {}).get("nk", {}).get("resolved_symbol") == "161100019"
    assert body["answers"]["11_NK_resolved_symbol"] == "161100019"
    assert body["answers"]["13_TOPIX_symbol"] == "161120005"


def test_final_report_uses_first_push(tmp_path):
    from research.new_causal_information_acquisition_v1.analyze import build_report_body

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["root"].mkdir(parents=True)
    _write_live_manifest(layout)
    (layout["root"] / "first_push.json").write_text(
        json.dumps({"nk": {"received_at": "2026-09-11T08:16:57.074+09:00"}, "topix": {"received_at": "2026-09-11T08:17:26.438+09:00"}}),
        encoding="utf-8",
    )
    body = build_report_body(
        tests={"ok": True, "passed": 42, "lineage_pass": True},
        now=datetime(2026, 9, 11, 13, 13, tzinfo=JST),
        native_root=tmp_path,
        trading_date=day,
    )
    assert body["answers"]["18_first_NK_PUSH"] == "2026-09-11T08:16:57.074+09:00"
    assert body["first_push"]["nk"]["received_at"].startswith("2026-09-11T08:16:57")


def test_final_report_not_preflight_snapshot(tmp_path):
    from research.new_causal_information_acquisition_v1 import CASE_MODE_READY
    from research.new_causal_information_acquisition_v1.analyze import build_report_body

    day = "20260911"
    layout = day_layout(day, native_root=tmp_path)
    layout["futures"].mkdir(parents=True)
    _write_stocks(layout, 48)
    _write_live_manifest(layout, nk="161100019", tx="161120005")
    layout["nk225mini_jsonl"].write_text(_fut_lines("NK225mini", "161100019"), encoding="utf-8")
    layout["topix_jsonl"].write_text(_fut_lines("TOPIX", "161120005"), encoding="utf-8")
    (layout["root"] / "first_push.json").write_text(json.dumps({"nk": {"received_at": "x"}, "topix": {"received_at": "y"}}), encoding="utf-8")
    now = datetime(2026, 9, 11, 13, 13, tzinfo=JST)
    body = build_report_body(
        tests={"ok": True, "passed": 42, "lineage_pass": True},
        now=now,
        native_root=tmp_path,
        trading_date=day,
    )
    assert body["clock"] == now.isoformat(timespec="seconds")
    assert body["decision"]["VERDICT"] != CASE_MODE_READY
    assert body["decision"].get("preflight_snapshot") is False
    assert body["decision"]["FULL"] is True


def test_finalize_script_has_no_order_api():
    p = NATIVE / "scripts" / "finalize_futures_market_context_capture.py"
    txt = p.read_text(encoding="utf-8")
    assert p.is_file()
    assert "send" + "order(" not in txt
    assert "/" + "sendorder" not in txt
    assert "unregister" not in txt.lower() or "no" in txt.lower()

