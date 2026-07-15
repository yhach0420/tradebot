"""
Phase 155 / 687W30 / 687W31: kabu PUSH register helpers.

W31 contract (Paper runtime):
1) load last local register state / desired set
2) if identical → safe reuse (no PUT)
3) else unregister/all → readback 0 (backoff) → PUT N → readback N + symbol set match
4) on 4002006: never defer clear for fanout Capture; force one safe recovery
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from api.push_client import KabuNativePushClient
from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, load_kabu_env

KABU_PUSH_REGISTER_LIMIT = 50
REGISTER_LIMIT_ERROR_CODES = frozenset({4001018, 4002006})
UNREGISTER_SETTLE_SEC = 0.35
UNREGISTER_ZERO_MAX_ATTEMPTS = 3
PAPER_REGISTER_STATE_REL = Path("runtime") / "paper_register_state.json"


def parse_kabu_error_code(exc: BaseException) -> Optional[int]:
    msg = str(exc)
    for pattern in (
        r'"Code"\s*:\s*(\d+)',
        r"'Code'\s*:\s*(\d+)",
        r"Code[=:]\s*(\d+)",
    ):
        m = re.search(pattern, msg)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def is_register_limit_error(exc: BaseException) -> bool:
    code = parse_kabu_error_code(exc)
    if code in REGISTER_LIMIT_ERROR_CODES:
        return True
    lowered = str(exc).lower()
    return "4002006" in lowered or "レジスト数" in str(exc) or "register_limit" in lowered


def extract_regist_num(resp: Any) -> Optional[int]:
    if not isinstance(resp, dict):
        return None
    for key in ("RegistNum", "registNum", "regist_num", "CurrentRegistNum"):
        if key in resp and resp[key] is not None:
            try:
                return int(resp[key])
            except (TypeError, ValueError):
                continue
    return None


def normalize_symbol_code(sym: Any) -> str:
    s = str(sym or "").strip().upper().replace(".T", "")
    if "@" in s:
        s = s.split("@", 1)[0]
    return s


def extract_symbol_set(resp: Any) -> Optional[set[str]]:
    """Best-effort symbol set from register/unregister response body."""
    if not isinstance(resp, dict):
        return None
    raw = resp.get("Symbols") or resp.get("symbols") or resp.get("RegistList")
    if not isinstance(raw, list) or not raw:
        return None
    out: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            code = normalize_symbol_code(item.get("Symbol") or item.get("symbol"))
        else:
            code = normalize_symbol_code(item)
        if code:
            out.add(code)
    return out or None


def desired_symbol_set(symbols_spec: Sequence[tuple[str, int]]) -> set[str]:
    return {normalize_symbol_code(s) for s, _ in symbols_spec if normalize_symbol_code(s)}


def paper_register_state_path(native_root: Path) -> Path:
    return Path(native_root) / PAPER_REGISTER_STATE_REL


def load_paper_register_state(native_root: Optional[Path]) -> dict[str, Any]:
    if not native_root:
        return {}
    path = paper_register_state_path(Path(native_root))
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def save_paper_register_state(
    native_root: Optional[Path],
    *,
    symbols_spec: Sequence[tuple[str, int]],
    regist_num: Optional[int],
    trading_date: Optional[str] = None,
    response: Any = None,
) -> Optional[Path]:
    if not native_root:
        return None
    path = paper_register_state_path(Path(native_root))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "trading_date": trading_date,
        "symbol_codes": sorted(desired_symbol_set(symbols_spec)),
        "symbol_count": len(symbols_spec),
        "regist_num": regist_num,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "response_regist_num": extract_regist_num(response) if response is not None else regist_num,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def format_register_failure_message(
    exc: BaseException,
    *,
    symbol_count: int,
    clear_first_effective: bool = True,
    clear_was_skipped: bool = False,
    steps: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    code = parse_kabu_error_code(exc)
    skipped = clear_was_skipped or (not clear_first_effective)
    if steps:
        for step in steps:
            if not isinstance(step, dict):
                continue
            if step.get("skipped") and str(step.get("reason") or "") == "CAPTURE_ACTIVE_CLEAR_DEFERRED":
                skipped = True
                break
            if str(step.get("step") or "").startswith("unregister") and step.get("skipped"):
                skipped = True
                break
    if is_register_limit_error(exc):
        if skipped:
            return (
                f"kabu register limit (Code {code or 4002006}): requested {symbol_count} symbols "
                f"(max {KABU_PUSH_REGISTER_LIMIT}). unregister/all was SKIPPED "
                f"(true Capture direct-owner defer). Fanout Capture must not block clear."
            )
        return (
            f"kabu register limit (Code {code or 4002006}): requested {symbol_count} symbols "
            f"(max {KABU_PUSH_REGISTER_LIMIT}). unregister/all + retry once FAILED "
            "(readback and/or register_retry still exceeded limit)."
        )
    return f"kabu register failed for {symbol_count} symbols: {exc}"


def unregister_all_safe(push: KabuNativePushClient) -> dict[str, Any]:
    try:
        resp = push.unregister_all()
        regist = extract_regist_num(resp)
        return {
            "ok": True,
            "response": resp,
            "regist_num": regist,
            "readback_zero": regist == 0 if regist is not None else None,
            "symbol_set": sorted(extract_symbol_set(resp) or []),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "error_type": type(e).__name__}


def unregister_all_until_zero(
    push: KabuNativePushClient,
    *,
    settle_sec: float = UNREGISTER_SETTLE_SEC,
    max_attempts: int = UNREGISTER_ZERO_MAX_ATTEMPTS,
    require_readback: bool = True,
) -> dict[str, Any]:
    """Unregister/all with short backoff until RegistNum==0 (when API returns it)."""
    attempts: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for i in range(max(1, int(max_attempts))):
        last = unregister_all_safe(push)
        last["attempt"] = i + 1
        if settle_sec > 0:
            time.sleep(float(settle_sec))
            last["settle_sec"] = float(settle_sec)
        attempts.append(dict(last))
        if not last.get("ok"):
            return {**last, "attempts": attempts, "readback_zero": False}
        regist = last.get("regist_num")
        if regist is None:
            # API omitted RegistNum — HTTP success is best available signal
            return {
                **last,
                "attempts": attempts,
                "readback_zero": None if require_readback else True,
                "readback_note": "regist_num_absent_http_ok",
            }
        if int(regist) == 0:
            return {**last, "attempts": attempts, "readback_zero": True}
    return {
        **last,
        "ok": False,
        "error": f"unregister readback RegistNum={last.get('regist_num')} != 0 after {max_attempts} attempts",
        "attempts": attempts,
        "readback_zero": False,
    }


def _assert_register_readback(
    resp: Any,
    *,
    expected_n: int,
    expected_symbols: set[str],
    require_symbol_set: bool = True,
) -> dict[str, Any]:
    regist = extract_regist_num(resp)
    got_set = extract_symbol_set(resp)
    out: dict[str, Any] = {
        "regist_num": regist,
        "expected": expected_n,
        "readback_ok": None if regist is None else regist == expected_n,
        "symbol_set": sorted(got_set) if got_set is not None else None,
        "symbol_set_match": None if got_set is None else got_set == expected_symbols,
    }
    if regist is not None and regist != expected_n:
        raise KabuNativeApiError(
            f"register readback mismatch: RegistNum={regist} expected={expected_n} body={resp!r}"
        )
    if require_symbol_set and got_set is not None and got_set != expected_symbols:
        raise KabuNativeApiError(
            f"register symbol set mismatch: got={sorted(got_set)} expected={sorted(expected_symbols)}"
        )
    return out


def register_symbols_cleared(
    push: KabuNativePushClient,
    symbols_spec: Sequence[tuple[str, int]],
    *,
    clear_first: bool = True,
    retry_on_limit: bool = True,
    native_root: Optional[Any] = None,
    trading_date: Optional[str] = None,
    settle_sec: float = UNREGISTER_SETTLE_SEC,
    require_readback: bool = True,
    allow_reuse_if_match: bool = True,
    force_clear_on_limit: bool = True,
    zero_readback_attempts: int = UNREGISTER_ZERO_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """
    Ensure Station registration matches desired symbols_spec.

    Phase687W31:
    - safe reuse when local last state symbol set matches desired
    - otherwise unregister→0→register N with readback
    - 4002006: force clear once (fanout Capture must not defer)
    """
    n = len(symbols_spec)
    if n > KABU_PUSH_REGISTER_LIMIT:
        raise KabuNativeApiError(
            f"register symbol count {n} exceeds kabu limit {KABU_PUSH_REGISTER_LIMIT}"
        )
    desired = desired_symbol_set(symbols_spec)
    root = Path(native_root) if native_root else Path(__file__).resolve().parents[2]
    steps: list[dict[str, Any]] = []

    # Safe reuse: local SoT matches desired (AM→PM / identical refresh).
    if allow_reuse_if_match:
        prev = load_paper_register_state(root)
        prev_codes = {normalize_symbol_code(x) for x in (prev.get("symbol_codes") or [])}
        prev_day_ok = (not trading_date) or str(prev.get("trading_date") or "") in ("", str(trading_date))
        if prev_day_ok and prev_codes and prev_codes == desired and int(prev.get("symbol_count") or 0) == n:
            step = {
                "step": "reuse_existing_registration",
                "ok": True,
                "skipped_put": True,
                "symbol_count": n,
                "reason": "desired_set_matches_local_state",
            }
            steps.append(step)
            return {
                "ok": True,
                "symbol_count": n,
                "steps": steps,
                "response": {"RegistNum": n, "Symbols": [{"Symbol": s, "Exchange": 1} for s in sorted(desired)]},
                "clear_first_effective": False,
                "capture_clear_deferred": False,
                "unregister_called": False,
                "reused_existing": True,
                "recovered_from_register_limit": False,
                "register_recovered": False,
                "symbol_set_match": True,
            }

    effective_clear = bool(clear_first)
    defer_meta: dict[str, Any] = {}
    if effective_clear:
        try:
            from small_paper.registration_lifetime import (
                clear_first_allowed_for_register,
                should_defer_paper_unregister,
            )

            if not clear_first_allowed_for_register(root, trading_date=trading_date):
                effective_clear = False
                defer_meta = should_defer_paper_unregister(root, trading_date=trading_date).to_dict()
        except Exception:
            pass

    def _clear(step_name: str, *, force: bool = False) -> dict[str, Any]:
        nonlocal effective_clear
        do_clear = bool(force or effective_clear)
        if not do_clear:
            step = {
                "step": step_name,
                "ok": True,
                "skipped": True,
                "reason": "CAPTURE_ACTIVE_CLEAR_DEFERRED",
                **({"capture": defer_meta} if defer_meta else {}),
            }
            steps.append(step)
            return step
        unr = unregister_all_until_zero(
            push,
            settle_sec=settle_sec,
            max_attempts=zero_readback_attempts,
            require_readback=require_readback,
        )
        if require_readback and unr.get("ok") and unr.get("regist_num") is not None:
            if int(unr["regist_num"]) != 0:
                unr["ok"] = False
                unr["error"] = unr.get("error") or f"unregister readback RegistNum={unr['regist_num']} != 0"
        steps.append({"step": step_name, "forced": bool(force), **unr})
        return unr

    def _register(step_name: str) -> dict[str, Any]:
        resp = push.register(symbols_spec)
        readback = {}
        if require_readback:
            try:
                readback = _assert_register_readback(
                    resp,
                    expected_n=n,
                    expected_symbols=desired,
                    require_symbol_set=True,
                )
            except KabuNativeApiError:
                steps.append(
                    {
                        "step": step_name,
                        "ok": False,
                        "symbol_count": n,
                        "response": resp,
                        "regist_num": extract_regist_num(resp),
                        "symbol_set": sorted(extract_symbol_set(resp) or []),
                        "error": "register_readback_or_symbol_mismatch",
                    }
                )
                raise
        step = {"step": step_name, "ok": True, "symbol_count": n, "response": resp, **readback}
        steps.append(step)
        save_paper_register_state(
            root,
            symbols_spec=symbols_spec,
            regist_num=extract_regist_num(resp) if extract_regist_num(resp) is not None else n,
            trading_date=trading_date,
            response=resp,
        )
        return resp

    clear1 = _clear("unregister_all_before_register")
    try:
        out = _register("register")
        return {
            "ok": True,
            "symbol_count": n,
            "steps": steps,
            "response": out,
            "clear_first_effective": effective_clear,
            "capture_clear_deferred": bool(defer_meta) and not effective_clear,
            "unregister_called": bool(clear1.get("ok") and not clear1.get("skipped")),
            "reused_existing": False,
            "recovered_from_register_limit": False,
            "register_recovered": False,
            "symbol_set_match": True,
        }
    except KabuNativeApiError as first_err:
        steps.append(
            {
                "step": "register",
                "ok": False,
                "symbol_count": n,
                "error": str(first_err),
                "kabu_code": parse_kabu_error_code(first_err),
            }
        )
        if not retry_on_limit or not is_register_limit_error(first_err):
            raise

        # W31: on 4002006 never leave residual regs because Capture is fanout consumer.
        force = bool(force_clear_on_limit)
        if force:
            effective_clear = True
        clear2 = _clear("unregister_all_retry_after_limit", force=force)
        clear_ran = bool(clear2.get("ok") and not clear2.get("skipped"))
        if clear2.get("ok") is False:
            raise KabuNativeApiError(
                format_register_failure_message(
                    first_err,
                    symbol_count=n,
                    clear_first_effective=True,
                    clear_was_skipped=False,
                    steps=steps,
                )
                + f" unregister_retry_failed={clear2.get('error')}"
            ) from first_err
        if not clear_ran:
            raise KabuNativeApiError(
                format_register_failure_message(
                    first_err,
                    symbol_count=n,
                    clear_first_effective=False,
                    clear_was_skipped=True,
                    steps=steps,
                )
            ) from first_err
        try:
            out = _register("register_retry")
            return {
                "ok": True,
                "symbol_count": n,
                "steps": steps,
                "response": out,
                "recovered_from_register_limit": True,
                "register_recovered": True,
                "clear_first_effective": True,
                "capture_clear_deferred": False,
                "unregister_called": True,
                "reused_existing": False,
                "symbol_set_match": True,
            }
        except KabuNativeApiError as second_err:
            steps.append(
                {
                    "step": "register_retry",
                    "ok": False,
                    "error": str(second_err),
                    "kabu_code": parse_kabu_error_code(second_err),
                }
            )
            raise KabuNativeApiError(
                format_register_failure_message(
                    second_err,
                    symbol_count=n,
                    clear_first_effective=True,
                    clear_was_skipped=False,
                    steps=steps,
                )
            ) from second_err


def push_client_from_repo(repo_root) -> tuple[KabuNativePushClient, KabuNativeRestClient, str]:
    load_kabu_env(repo_root=Path(repo_root))
    rest = KabuNativeRestClient(default_base_url())
    token = rest.issue_token_from_env()
    return KabuNativePushClient(rest, token), rest, token


def clear_register_before_session(repo_root) -> dict[str, Any]:
    try:
        push, _, _ = push_client_from_repo(Path(repo_root))
        unr = unregister_all_until_zero(push)
        return {
            "ok": bool(unr.get("ok")),
            "cleared": bool(unr.get("ok")),
            "unregister_all": unr,
            "register_limit": KABU_PUSH_REGISTER_LIMIT,
            "list_registered_symbols_api": False,
            "note": "kabu API has no GET registered-symbols; use unregister/all + RegistNum readback.",
        }
    except Exception as e:
        return {
            "ok": False,
            "cleared": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "register_limit": KABU_PUSH_REGISTER_LIMIT,
        }


def assess_register_capacity(*, universe_symbol_count: int) -> dict[str, Any]:
    n = int(universe_symbol_count)
    return {
        "universe_symbol_count": n,
        "kabu_register_limit": KABU_PUSH_REGISTER_LIMIT,
        "within_limit": n <= KABU_PUSH_REGISTER_LIMIT,
        "headroom": max(0, KABU_PUSH_REGISTER_LIMIT - n),
        "unregister_all_available": True,
        "per_symbol_unregister_available": False,
        "would_exceed_if_stale_registered": n > 0,
        "risk_note": (
            "If prior session left registrations without unregister/all, "
            "register of 50 new symbols can hit Code 4002006 until cleared."
        ),
    }
