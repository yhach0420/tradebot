"""Pre-freeze Kabu symbol eligibility: skip terminal-invalid, refill ranked, then freeze once.

Uses the existing Ingress-owned readonly token path. Never issues a token.
Temporary AUTH / rate-limit / transport failures fail closed (no substitution).
"""
from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from small_paper.day_fixed_am_registration import (
    EXPECTED_SYMBOLS,
    canonical_symbol_key,
    canonical_symbols,
    freeze_same_day_am_universe,
    load_am_canonical_50,
    load_frozen_am_universe,
)

INVALID_SYMBOL = "INVALID_SYMBOL"
VALID_SYMBOL = "VALID_SYMBOL"
AUTH_NOT_READY = "AUTH_NOT_READY"
RATE_LIMIT = "RATE_LIMIT"
TRANSPORT_FAILURE = "TRANSPORT_FAILURE"
TEMPORARY_UNKNOWN = "TEMPORARY_UNKNOWN"
REGISTER_CAPACITY = "REGISTER_CAPACITY"

KABU_SYMBOL_NOT_FOUND = "4002001"
KABU_REGISTER_LIMIT = "4002006"
KABU_REGISTER_LIMIT_ALT = "4001018"
# Depth beyond AM50. Ranker _norm() uses 6098.T; freeze SoT uses bare 6098.
# Extra slots must be large enough that terminal-invalids in the first 50 can
# still fill valid50 from later ranks. Do not cut the validator input at 50.
REFILL_EXTRA_SLOTS = 150
# Space live /board probes so a 50-name walk does not self-trip 4001006.
# Fail-closed on RATE_LIMIT stays; this only reduces avoidable rate hits.
DEFAULT_PROBE_INTERVAL_SEC = 0.25
# Startup-only: first /board after Ingress boot may return TRANSPORT_FAILURE.
# Retry TRANSPORT_FAILURE only. Max 3 attempts. Not AUTH / RATE / INVALID.
LIVE_BOARD_PROBE_MAX_ATTEMPTS = 3
LIVE_BOARD_PROBE_TRANSPORT_SLEEP_SEC = (1.0, 2.0)

ProbeFn = Callable[[str], dict[str, Any]]
_last_live_probe_monotonic: float = 0.0


def _bare(raw: Any) -> str:
    return canonical_symbol_key(raw)


def probe_interval_sec() -> float:
    raw = str(os.environ.get("KABU_PRE_FREEZE_PROBE_INTERVAL_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return float(DEFAULT_PROBE_INTERVAL_SEC)


def _throttle_live_probe() -> None:
    """Causal pacing between live board GETs. No-op when interval is 0."""
    global _last_live_probe_monotonic
    interval = probe_interval_sec()
    if interval <= 0:
        return
    now = time.monotonic()
    wait = float(_last_live_probe_monotonic) + interval - now
    if wait > 0:
        time.sleep(wait)
    _last_live_probe_monotonic = time.monotonic()


def _redact_probe_text(text: Any, *, token: str = "") -> str:
    """Persist str(exc) without the live token string."""
    msg = str(text or "")
    tok = str(token or "").strip()
    if tok:
        msg = msg.replace(tok, "[REDACTED_TOKEN]")
    return msg


def classify_board_probe_error(
    *,
    kabu_code: Any = "",
    error: Any = "",
    http_status: Any = None,
    failure_class: Any = "",
) -> str:
    code = str(kabu_code or "").strip()
    text = str(error or "")
    http = None
    try:
        if http_status is not None:
            http = int(http_status)
    except (TypeError, ValueError):
        http = None
    blob = f"{code} {text}".upper()
    if code == KABU_SYMBOL_NOT_FOUND or KABU_SYMBOL_NOT_FOUND in text or "SYMBOL NOT FOUND" in blob:
        return INVALID_SYMBOL
    if "銘柄が見つからない" in str(error or ""):
        return INVALID_SYMBOL
    # Leftover Station PUSH capacity: GET /board for a symbol not in the
    # current 50 returns 4002006. That is not 4002001 and must not fail-close
    # freeze, or Ingress never reaches its unregister/all recovery.
    if code in {KABU_REGISTER_LIMIT, KABU_REGISTER_LIMIT_ALT} or KABU_REGISTER_LIMIT in text or "レジスト数" in str(
        error or ""
    ):
        return REGISTER_CAPACITY
    try:
        from small_paper.kabu_token_authority import AUTH_INVALID, RATE_LIMIT as AUTH_RATE, classify_kabu_api_error

        cls = classify_kabu_api_error(text, http_status=http)
        if cls == AUTH_INVALID:
            return AUTH_NOT_READY
        if cls == AUTH_RATE:
            return RATE_LIMIT
    except Exception:
        pass
    if http in {401, 403} or "4001009" in text or "4001007" in text:
        return AUTH_NOT_READY
    if http == 429 or "4001006" in text:
        return RATE_LIMIT
    if str(failure_class or "").upper() == "TRANSPORT":
        return TRANSPORT_FAILURE
    if "ネットワークエラー" in str(error or ""):
        return TRANSPORT_FAILURE
    low = text.lower()
    if "timeout" in low or "timed out" in low or "connection" in low or "temporarily" in low:
        return TRANSPORT_FAILURE
    return TEMPORARY_UNKNOWN


def is_temporary_failure(verdict: str) -> bool:
    return str(verdict or "") in {AUTH_NOT_READY, RATE_LIMIT, TRANSPORT_FAILURE, TEMPORARY_UNKNOWN}


def acquire_readonly_token(
    *,
    native_root: Path,
    trading_date: str,
    caller: str = "pre_freeze_kabu_validation",
) -> dict[str, Any]:
    """Reuse Ingress-owned token only. Never POST /token."""
    from small_paper.kabu_token_authority import (
        TokenUnavailable,
        acquire_token_for_readonly,
        read_shared_token,
    )

    try:
        got = acquire_token_for_readonly(
            native_root=Path(native_root),
            trading_date=str(trading_date),
            caller=caller,
        )
        token = str(got.get("token") or "")
        if token:
            return {"ok": True, "token": token, "issued": False, "reused": True, "source": "acquire_token_for_readonly"}
    except Exception as exc:
        if not isinstance(exc, TokenUnavailable) and type(exc).__name__ != "TokenUnavailable":
            return {"ok": False, "reason": AUTH_NOT_READY, "error": type(exc).__name__, "issued": False}
    token = str(read_shared_token(Path(native_root), str(trading_date)) or "")
    if token:
        return {"ok": True, "token": token, "issued": False, "reused": True, "source": "read_shared_token"}
    return {"ok": False, "reason": AUTH_NOT_READY, "issued": False, "token": ""}


def _live_board_probe_once(symbol: str, *, token: str, throttle: bool) -> dict[str, Any]:
    from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url

    bare = _bare(symbol)
    key = f"{bare}@1"
    if throttle:
        _throttle_live_probe()
    client = KabuNativeRestClient(default_base_url(), timeout=5.0, max_retries=1)

    def _evidence(exc: BaseException) -> dict[str, Any]:
        http_status = getattr(exc, "http_status", None)
        if http_status is None:
            http_status = getattr(exc, "http_status", None)
        return {
            "error_type": type(exc).__name__,
            "error_message": _redact_probe_text(exc, token=token),
            "http_status": http_status,
            "kabu_code": str(getattr(exc, "kabu_code", "") or ""),
            "issued_token": False,
        }

    try:
        client.get_board(key, token=token)
        return {
            "ok": True,
            "symbol": bare,
            "verdict": VALID_SYMBOL,
            "kabu_code": "",
            "error_type": "",
            "error_message": "",
            "http_status": None,
            "issued_token": False,
        }
    except KabuNativeApiError as exc:
        code = str(getattr(exc, "kabu_code", "") or "")
        verdict = classify_board_probe_error(
            kabu_code=code,
            error=exc,
            http_status=getattr(exc, "http_status", None),
            failure_class=getattr(exc, "failure_class", ""),
        )
        return {
            "ok": verdict in {VALID_SYMBOL, REGISTER_CAPACITY},
            "symbol": bare,
            "verdict": verdict,
            "kabu_code": code,
            **_evidence(exc),
        }
    except Exception as exc:
        verdict = classify_board_probe_error(
            error=exc,
            failure_class=getattr(exc, "failure_class", ""),
        )
        return {
            "ok": False,
            "symbol": bare,
            "verdict": verdict if verdict != INVALID_SYMBOL else TRANSPORT_FAILURE,
            "kabu_code": "",
            **_evidence(exc),
        }


def live_board_probe(symbol: str, *, token: str) -> dict[str, Any]:
    """Live GET /board. Retry TRANSPORT_FAILURE only, bounded to 3 attempts."""
    max_attempts = int(LIVE_BOARD_PROBE_MAX_ATTEMPTS)
    last: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        got = _live_board_probe_once(symbol, token=token, throttle=(attempt == 1))
        got["attempt"] = attempt
        got["max_attempts"] = max_attempts
        if got.get("ok") or str(got.get("verdict") or "") != TRANSPORT_FAILURE:
            return got
        last = got
        if attempt < max_attempts:
            time.sleep(float(LIVE_BOARD_PROBE_TRANSPORT_SLEEP_SEC[attempt - 1]))
    return last


def make_cached_probe(probe_fn: ProbeFn) -> ProbeFn:
    cache: dict[str, dict[str, Any]] = {}

    def _probe(symbol: str) -> dict[str, Any]:
        key = _bare(symbol)
        if key in cache:
            return dict(cache[key], cached=True)
        got = dict(probe_fn(symbol) or {})
        got.setdefault("symbol", key)
        cache[key] = got
        return dict(got, cached=False)

    _probe.cache = cache  # type: ignore[attr-defined]
    return _probe


def select_valid50_from_ranked(
    ranked: Sequence[str],
    *,
    probe_fn: ProbeFn,
    target_count: int = EXPECTED_SYMBOLS,
) -> dict[str, Any]:
    """Walk ranked candidates. Skip terminal INVALID_SYMBOL. Fail closed on temporary errors."""
    ranked_n = [_bare(s) for s in ranked if _bare(s)]
    seen: set[str] = set()
    ordered: list[str] = []
    for sym in ranked_n:
        if sym in seen:
            continue
        seen.add(sym)
        ordered.append(sym)
    valid: list[str] = []
    excluded: list[dict[str, Any]] = []
    probe = make_cached_probe(probe_fn)
    validated_count = 0
    primary_set = set(ordered[: int(target_count)])

    def _stats(*, extra: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        invalid_syms = [str(e.get("symbol") or "") for e in excluded]
        refill_success = sum(1 for s in valid if s not in primary_set)
        body: dict[str, Any] = {
            "valid_symbols": list(valid),
            "excluded_terminal_invalid": list(excluded),
            "target_count": int(target_count),
            "ranked_count": len(ordered),
            "ranked_candidate_count": len(ordered),
            "validation_candidate_pool_count": len(ordered),
            "validated_count": validated_count,
            "valid_count": len(valid),
            "final_valid_count": len(valid),
            "terminal_invalid_count": len(excluded),
            "terminal_invalid_symbols": invalid_syms,
            "refill_attempt_count": len(excluded),
            "refill_success_count": refill_success,
            "refill_used": bool(excluded),
            "substituted": False,
        }
        if extra:
            body.update(dict(extra))
        return body

    for sym in ordered:
        if len(valid) >= int(target_count):
            break
        got = probe(sym)
        validated_count += 1
        verdict = str(got.get("verdict") or "")
        code = got.get("kabu_code")
        if verdict in {VALID_SYMBOL, REGISTER_CAPACITY} or (
            got.get("ok") is True
            and verdict not in {INVALID_SYMBOL, AUTH_NOT_READY, RATE_LIMIT, TRANSPORT_FAILURE, TEMPORARY_UNKNOWN}
        ):
            valid.append(sym)
            continue
        if verdict == INVALID_SYMBOL:
            excluded.append({"symbol": sym, "verdict": INVALID_SYMBOL, "kabu_code": code})
            continue
        return {
            "ok": False,
            "reason": verdict or TEMPORARY_UNKNOWN,
            "fail_closed": True,
            **_stats(
                extra={
                    "failed_symbol": sym,
                    "first_failure_reason": verdict or TEMPORARY_UNKNOWN,
                    "first_failure_symbol": sym,
                    "first_failure_code": code or "",
                    "temporary_failure_count": 1,
                    "temporary_failure_symbols": [sym],
                    "temporary_failure_codes": [str(code or verdict or "")],
                    "error_type": got.get("error_type") or "",
                    "error_message": got.get("error_message") or "",
                    "http_status": got.get("http_status"),
                    "kabu_code": got.get("kabu_code") or code or "",
                    "attempt": got.get("attempt"),
                    "max_attempts": got.get("max_attempts"),
                }
            ),
        }
    if len(valid) != int(target_count):
        first = excluded[0] if excluded else {}
        return {
            "ok": False,
            "reason": "INSUFFICIENT_VALID_CANDIDATES",
            "fail_closed": True,
            **_stats(
                extra={
                    "first_failure_reason": "INSUFFICIENT_VALID_CANDIDATES",
                    "first_failure_symbol": str(first.get("symbol") or ""),
                    "first_failure_code": str(first.get("kabu_code") or ""),
                    "temporary_failure_count": 0,
                    "temporary_failure_symbols": [],
                    "temporary_failure_codes": [],
                }
            ),
        }
    return {
        "ok": True,
        "reason": "",
        "fail_closed": False,
        **_stats(
            extra={
                "first_failure_reason": "",
                "first_failure_symbol": "",
                "first_failure_code": "",
                "temporary_failure_count": 0,
                "temporary_failure_symbols": [],
                "temporary_failure_codes": [],
            }
        ),
    }


def _load_feature_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{str(k): str(v or "") for k, v in row.items()} for row in csv.DictReader(fh)]


def ranker_exclude_set(symbols: Sequence[str]) -> set[str]:
    """Exclude keys for vol/liq ranker `_norm` (6098.T) and freeze SoT (6098)."""
    out: set[str] = set()
    for raw in symbols:
        bare = _bare(raw)
        if not bare:
            continue
        out.add(bare)
        out.add(f"{bare}.T")
    return out


def load_ranked_candidate_pool(
    native_root: Path,
    trading_date: str,
    *,
    extra_slots: int = REFILL_EXTRA_SLOTS,
) -> dict[str, Any]:
    """AM ranked 50 (CSV order) plus later vol/liq ranks for terminal-invalid refill.

    Must not pass only the already-cut AM50 to Kabu validation. Ranker `_norm`
    uses `6098.T` while AM CSV / freeze SoT use bare `6098`; exclude both forms
    or extras collapse to duplicates and ranked_count stays 50.
    """
    from small_paper.universe_prebuild import features_path

    am = load_am_canonical_50(Path(native_root), str(trading_date))
    primary = canonical_symbols(list(am.get("symbols") or []))
    extras: list[str] = []
    feat_path = features_path(Path(native_root), str(trading_date))
    rows = _load_feature_rows(feat_path)
    if rows and extra_slots > 0:
        try:
            from universe.core10_dynamic40_price_risk import select_dynamic_vol_liq_price_risk

            extra_rows = select_dynamic_vol_liq_price_risk(
                rows,
                exclude=ranker_exclude_set(primary),
                target_count=int(extra_slots),
            )
            extras = canonical_symbols([r.get("symbol") for r in extra_rows])
        except Exception:
            extras = []
    pool = []
    seen: set[str] = set()
    for sym in primary + extras:
        if not sym or sym in seen:
            continue
        seen.add(sym)
        pool.append(sym)
    unique_extras = [s for s in extras if s not in set(primary)]
    return {
        "ok": bool(primary) and len(primary) == EXPECTED_SYMBOLS,
        "primary": primary,
        "extras": unique_extras,
        "ranked": pool,
        "universe_path": str(am.get("universe_path") or ""),
        "universe_sha256": str(am.get("universe_sha256") or ""),
        "reason": "" if primary else str(am.get("reason") or "am_canonical_missing"),
        "ranked_count": len(pool),
        "primary_count": len(primary),
        "extras_count": len(unique_extras),
    }


def freeze_valid50_after_kabu_validation(
    native_root: Path,
    trading_date: str,
    *,
    ranked: Optional[Sequence[str]] = None,
    probe_fn: Optional[ProbeFn] = None,
    skip_if_frozen: bool = True,
    skip_validation: bool = False,
) -> dict[str, Any]:
    """Validate ranked pool, then freeze exactly once with proven valid50."""
    day = str(trading_date)
    root = Path(native_root)
    existing = load_frozen_am_universe(root, day)
    if skip_if_frozen and existing.get("present") and existing.get("ok"):
        symbols = list(existing.get("canonical_symbols") or [])
        return {
            "ok": True,
            "reused": True,
            "reason": "already_frozen",
            "frozen": existing,
            "valid_symbols": symbols,
            "freeze_created": False,
            "freeze_symbol_count": len(symbols),
            "final_valid_count": len(symbols),
        }
    pool = {"ranked": list(ranked or []), "universe_path": "", "universe_sha256": ""}
    if not pool["ranked"]:
        pool = load_ranked_candidate_pool(root, day)
        if not pool.get("ok") and not pool.get("ranked"):
            return {"ok": False, "reason": str(pool.get("reason") or "ranked_pool_unavailable"), "step": "ranked_pool"}
    if skip_validation:
        symbols = canonical_symbols(list(pool.get("primary") or pool.get("ranked") or []))[:EXPECTED_SYMBOLS]
        if len(symbols) != EXPECTED_SYMBOLS:
            return {"ok": False, "reason": "valid50_incomplete_skip_validation", "valid_symbols": symbols}
        selected = {
            "ok": True,
            "valid_symbols": symbols,
            "excluded_terminal_invalid": [],
            "reason": "validation_skipped",
        }
    else:
        live_probe = probe_fn
        token_info: dict[str, Any] = {}
        if live_probe is None:
            token_info = acquire_readonly_token(native_root=root, trading_date=day)
            if not token_info.get("ok"):
                return {
                    "ok": False,
                    "reason": AUTH_NOT_READY,
                    "fail_closed": True,
                    "substituted": False,
                    "token_source": token_info.get("source"),
                    "step": "readonly_token",
                    "first_failure_reason": AUTH_NOT_READY,
                    "first_failure_symbol": "",
                    "first_failure_code": "",
                    "freeze_created": False,
                    "freeze_symbol_count": 0,
                    "temporary_failure_count": 1,
                    "temporary_failure_symbols": [],
                    "temporary_failure_codes": [AUTH_NOT_READY],
                    "error_type": str(token_info.get("error") or ""),
                    "error_message": "",
                    "http_status": None,
                    "kabu_code": "",
                    "attempt": 1,
                    "max_attempts": 1,
                }
            token = str(token_info.get("token") or "")

            def live_probe(symbol: str, _token: str = token) -> dict[str, Any]:
                return live_board_probe(symbol, token=_token)

        selected = select_valid50_from_ranked(list(pool.get("ranked") or []), probe_fn=live_probe)
        selected["token_issued"] = False
        if token_info:
            selected["token_source"] = token_info.get("source")
        selected["primary_count"] = int(pool.get("primary_count") or 0)
        selected["extras_count"] = int(pool.get("extras_count") or 0)
        selected["ranked_candidate_count"] = int(pool.get("ranked_count") or len(pool.get("ranked") or []))
        selected["validation_candidate_pool_count"] = len(pool.get("ranked") or [])
        if not selected.get("ok"):
            return {
                **selected,
                "step": "pre_freeze_validation",
                "freeze_created": False,
                "freeze_symbol_count": 0,
            }
    symbols = list(selected.get("valid_symbols") or [])
    frozen = freeze_same_day_am_universe(
        root,
        day,
        symbols=symbols,
        source_path=str(pool.get("universe_path") or ""),
        source_sha256=str(pool.get("universe_sha256") or ""),
        write_from_symbols=True,
    )
    if not frozen.get("ok"):
        return {
            "ok": False,
            "reason": str(frozen.get("reason") or "freeze_failed"),
            "step": "freeze",
            "frozen": frozen,
            "freeze_created": False,
            "freeze_symbol_count": 0,
            **{k: selected.get(k) for k in (
                "ranked_candidate_count",
                "validation_candidate_pool_count",
                "validated_count",
                "valid_count",
                "terminal_invalid_count",
                "terminal_invalid_symbols",
                "temporary_failure_count",
                "temporary_failure_symbols",
                "temporary_failure_codes",
                "refill_attempt_count",
                "refill_success_count",
                "first_failure_reason",
                "first_failure_symbol",
                "first_failure_code",
            ) if k in selected},
        }
    created = not bool(frozen.get("reused"))
    return {
        "ok": True,
        "reused": bool(frozen.get("reused")),
        "reason": str(frozen.get("reason") or ""),
        "frozen": frozen,
        "valid_symbols": symbols,
        "excluded_terminal_invalid": list(selected.get("excluded_terminal_invalid") or []),
        "pre_freeze_validation": {
            k: selected.get(k)
            for k in (
                "ok",
                "refill_used",
                "ranked_count",
                "valid_count",
                "ranked_candidate_count",
                "validation_candidate_pool_count",
                "validated_count",
                "terminal_invalid_count",
                "terminal_invalid_symbols",
                "refill_attempt_count",
                "refill_success_count",
            )
        },
        "token_issued": False,
        "freeze_created": created,
        "freeze_symbol_count": len(symbols),
        "final_valid_count": len(symbols),
        "ranked_candidate_count": selected.get("ranked_candidate_count") or len(pool.get("ranked") or []),
        "validation_candidate_pool_count": selected.get("validation_candidate_pool_count") or len(pool.get("ranked") or []),
        "validated_count": selected.get("validated_count"),
        "valid_count": selected.get("valid_count") or len(symbols),
        "terminal_invalid_count": selected.get("terminal_invalid_count") or 0,
        "terminal_invalid_symbols": list(selected.get("terminal_invalid_symbols") or []),
        "temporary_failure_count": selected.get("temporary_failure_count") or 0,
        "temporary_failure_symbols": list(selected.get("temporary_failure_symbols") or []),
        "temporary_failure_codes": list(selected.get("temporary_failure_codes") or []),
        "refill_attempt_count": selected.get("refill_attempt_count") or 0,
        "refill_success_count": selected.get("refill_success_count") or 0,
        "first_failure_reason": selected.get("first_failure_reason") or "",
        "first_failure_symbol": selected.get("first_failure_symbol") or "",
        "first_failure_code": selected.get("first_failure_code") or "",
    }
