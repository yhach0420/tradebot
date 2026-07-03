# Phase618 — Freshness Definition Git History Audit

**Verdict:** `phase618_freshness_definition_git_history_done`

## Question

Is `data_stale_price` freshness defined the same on **6/25 and before** vs **now**?

Target formula (user):

> `freshness ⟺ (eval時刻 − payload.CurrentPriceTime) ≤ 3秒`

Code equivalent: reject when `price_age_sec > entry_max_price_age_sec (3.0)` or `CurrentPriceTime` missing.

---

## Mandatory Answers

| # | Answer |
|---|--------|
| **1 eval時刻** | **`datetime.now(JST)`** at the instant `compute_entry_freshness()` runs inside `_process_push_payload`. **Not** PUSH receive time (`t0_push_received_at`), **not** audit `eval_start_ts`, **not** `recorded_at` on live. (Working tree only: push-replay may use `payload.recorded_at` via `_replay_reference_now`.) |
| **2 CurrentPriceTime** | kabu WebSocket PUSH field **`payload["CurrentPriceTime"]`** — **最終約定時刻** (last trade print). Parsed by `parse_kabu_time`. Missing/null → `price_age_sec=None` → `data_stale_price`. Passed through `enrich_payload` unchanged. |
| **3 比較式の場所** | `entry_scan_controller._field_age_sec` (age), `check_entry_data_freshness` (git HEAD) or `evaluate_entry_data_freshness` (working tree), wired from `pilot_runner._process_push_payload` |
| **4 6/25 vs HEAD 比較式同一** | **Yes** (committed HEAD `924bb1e` ≡ pre-6/25 ref `196a559`) |
| **5 eval時刻取得元の変更** | **No** (live path still `datetime.now(JST)`) |
| **6 CurrentPriceTime取得元の変更** | **No** (still raw PUSH → enriched copy) |
| **7 3秒閾値の変更** | **No** (`entry_max_price_age_sec: 3.0` since `14ad1a9` / 2026-06-13) |
| **8 6/25以前も約定時刻→評価3秒以内** | **Yes** — same rule since freshness guard introduction (6/13) |
| **9 実装変更 vs 設計** | **設計上の問題（kabu feed 仕様）** が主因。実装の core 比較式は 6/25→HEAD committed で **未変更**。未コミット working tree に Phase603 board_fallback + replay `reference_now` あり（prod YAML では fallback OFF）。 |

---

## Code Map (core path)

### Age computation

```92:105:src/small_paper/entry_scan_controller.py
def _field_age_sec(
    payload: Mapping[str, Any],
    field: str,
    *,
    reference_now: Optional[datetime] = None,
) -> tuple[Optional[str], Optional[float]]:
    raw = payload.get(field)
    ...
    now = reference_now if reference_now is not None else datetime.now(JST)
    tick = parse_kabu_time(raw, fallback=now)
    age = max(0.0, (now - tick).total_seconds())
```

```128:129:src/small_paper/entry_scan_controller.py
    price_ts, price_age = _field_age_sec(
        payload, "CurrentPriceTime", reference_now=reference_now
```

### Stale reject (git HEAD committed — what 6/25 live used)

```135:138:src/small_paper/entry_scan_controller.py
    if snap.price_age_sec is None or snap.price_age_sec > float(max_price_age_sec):
        return REJECT_DATA_STALE_PRICE
```

*(At commit `196a559`/`924bb1e` this lives in `check_entry_data_freshness`; working tree routes through `evaluate_entry_data_freshness` with the same numeric test when `board_fallback_enabled=false`.)*

### Call site (eval moment)

```2117:2118:src/small_paper/pilot_runner.py
    eval_start_mono = time.monotonic()
    eval_start_ts = _now_iso()
```

```2284:2309:src/small_paper/pilot_runner.py
        freshness = compute_entry_freshness(
            enriched, pipeline_source=ctx.source, reference_now=_replay_reference_now(ctx, enriched)
        )
        ...
            freshness_decision = evaluate_entry_data_freshness(
                freshness,
                enriched,
                max_price_age_sec=ctx.entry_scan.max_price_age_sec,
```

`eval_start_ts` is **audit-only**; age math uses `datetime.now(JST)` at `compute_entry_freshness` call (microseconds later in same function).

### CurrentPriceTime source

- PUSH: `src/api/push_client.py` `EXPECTED_PUSH_FIELDS_STOCK` includes `CurrentPriceTime`
- Enrich: `live_feature_bridge.enrich_payload` → `dict(payload)` (no overwrite)
- Docs: Phase602 — updates on **trade execution**, not board-only ticks

---

## Git History

| Ref | Date | Freshness core |
|-----|------|----------------|
| `14ad1a9` | 2026-06-13 | **Introduced** `data_stale_price`, `_field_age_sec`, 3.0s YAML |
| `196a559` | 2026-06-22 | Last commit before 6/26 — **6/25 sessions use this or earlier** |
| `924bb1e` HEAD | 2026-06-28 | **`entry_scan_controller.py` diff vs 196a559: empty** |
| Working tree | uncommitted | Phase603: `evaluate_entry_data_freshness`, `reference_now`, board_fallback (YAML **false**) |

**Phase/commit if changed:** Core formula **never changed** after intro. Uncommitted Phase603 adds optional paths only.

---

## Threshold History

| When | `entry_max_price_age_sec` | Notes |
|------|---------------------------|-------|
| 14ad1a9 (6/13) | **3.0** | First introduction |
| 196a559 (pre-6/25) | **3.0** | Unchanged |
| HEAD committed | **3.0** | Unchanged |
| Prod YAML | **3.0** | `entry_freshness_board_fallback_enabled: false` |

---

## Artifacts

- `results/reports/phase618_freshness_definition_code_map.csv`
- `results/reports/phase618_freshness_git_diff.csv`
- `results/reports/phase618_freshness_threshold_history.csv`
- `results/reports/phase618_report.json`

---

## Conclusion

**6/25以前と git HEAD committed では、freshness 比較式は同一**（`now(JST) − CurrentPriceTime ≤ 3s`）。  
629 で stale が増えた主因は **実装変更ではなく**、kabu PUSH 上で `CurrentPriceTime` が約定時のみ更新され board だけ更新される **feed 設計**（Phase602 確認済み）に加え、live では eval 時刻が `datetime.now()` のため **queue 遅延**も age に加算される点。
