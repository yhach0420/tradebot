# Live Order Operations

**Scope:** Dry-run / Mock / (future) read-only operations.  
**PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED**

Do not publish real-order enablement steps in this document.

---

## 1. 起動前チェック

1. Confirm `live_trading_enabled=false` and `order_enabled=false` in production YAML.
2. Confirm production config SHA pin matches.
3. Disk free space adequate (see Phase687W1).
4. Design consistency: `python scripts/check_live_order_design_consistency.py`
5. W2/W3 unit tests green.

## 2. Kabu Station起動確認

Do **not** treat process-name detection alone as absolute availability.

Separated fields:

- `station_process_detected` (advisory)
- `api_port_reachable`
- `token_endpoint_reachable`
- `token_acquired`
- `readonly_endpoint_reachable`
- `operational_api_available` (token + read-only preferred)
- `process_detection_warning` when process false but API works

CLI: `python -m small_paper.check_kabu_readonly_readiness`

## 3. API token確認

Operator verifies token validity outside SafetySM. Engine Mock assumes `token_valid=True`; Kabu skeleton reports offline.

## 4. production config SHA確認

`python scripts/check_live_pipeline_preflight.py`

## 5. cache prebuild

Monday AM example (single line):

```
cd C:\Users\yhach\Documents\tradebotfile\kabu_native; python -m small_paper.prebuild_vol_liq_startup_cache --date YYYYMMDD
```

AM→PM cache reuse forbidden (Phase687W1).

## 6. read-only account確認

**Status: NOT_CONNECTED** for `KabuBrokerAdapter` account reads in SafetySM.  
Do not treat Mock equity as live account truth.

## 7. reconciliation

Dry-run:

```
cd C:\Users\yhach\Documents\tradebotfile\kabu_native; $env:PYTHONPATH="src;C:\Users\yhach\Documents\tradebotfile"; python -c "from small_paper.live_order_safety_sm import build_engine, MockBrokerAdapter; from pathlib import Path; b=MockBrokerAdapter(); e=build_engine(output_dir=Path('results/_tmp_recon'), session_id='ops'); print(e.startup_reconciliation(local_positions={}, local_pending={}))"
```

On mismatch: ENTRY blocked; EXIT-only mode; human review. Never blind-resubmit.

## 8. Paper起動

Paper auto-start is **forbidden** from order-safety phases. Start Paper only via existing operator procedures when intended.

## 9. Dry-run確認

```
cd C:\Users\yhach\Documents\tradebotfile\kabu_native; $env:PYTHONPATH="src;C:\Users\yhach\Documents\tradebotfile"; python -m research.phase687w2_live_order_safety
```

Expect verdict `LIVE_ORDER_SAFETY_DRYRUN_READY`, `actual_broker_submit_count=0`.

## 10. kill switch操作

Engine API: `engine.activate_kill_switch("manual")`.  
Effect implemented: new ENTRY blocked.  
Pending ENTRY auto-cancel: **NOT_IMPLEMENTED**.  
EXIT remains allowed.

## 11. exit-only移行

Triggered by `startup_reconciliation` diffs or `RECOVERY_REQUIRED`. Clear only after human confirmation and journal/broker alignment.

## 12. journal破損時対応

1. Stop creating new ENTRY.
2. Quarantine corrupt JSONL.
3. Attempt `restore_from_journal` on intact prefix.
4. If restore incomplete → RECOVERY_REQUIRED; do not resubmit.

## 13. broker only position対応

1. Mark recovery_required / entry_blocked.
2. Discord mock: RECOVERY REQUIRED.
3. Human confirms broker position.
4. EXIT-only until resolved.
5. Never create matching ENTRY to “fix” local ledger.

## 14. pending order不一致対応

Classify `local_only_order` / `broker_only_order`. Reconcile via `get_order_status` / `reconcile_order`. No duplicate submit.

## 15. 正常停止

1. No open UNKNOWN without reconcile note.
2. Collect journals under session output dir.
3. Confirm reservation leak = 0 for closed session.
4. Leave flags false.

## 16. 異常停止

1. Assume possible SUBMIT_PENDING/UNKNOWN.
2. On restart: `restore_from_journal` then broker reconcile (Mock today).
3. Do not resubmit.

## 17. 再起動

1. Restore journal.
2. Startup reconciliation.
3. If mismatch → exit-only.
4. Resume Dry-run only.

## 18. ログ回収

Collect: `order_intents.jsonl`, `order_state_events.jsonl`, `broker_reconciliation.jsonl`, W2/W3 report dirs. Avoid huge raw PUSH dumps.

## 19. 日次終了確認

- Open positions vs broker (when read-only available)
- Kill switch reasons cleared or documented
- Daily loss threshold structure only (no production value auto-set)
- `actual_broker_submit_count == 0`

---

## PRODUCTION ORDER ENABLEMENT

```
NOT AUTHORIZED / NOT IMPLEMENTED
```

Any document or script claiming production submit enablement is out of date and must be rejected.

## Phase687W4 Runtime Dry-Run

Enable flag only (no strategy thresholds): `live_order_safety_sm_enabled: true`

Hooks: `_init_live_order_safety_sm`, `_maybe_record_live_order_safety_entry`, structural EXIT via `_maybe_record_live_order_exit`.

Weekend: if Kabu Station read-only unavailable, record `READONLY_API_WEEKEND_UNAVAILABLE` — do not treat Mock E2E as live readonly PASS.

Forward soak (Mon+ ≥3 sessions) is separate from implementation READY.


## Monday AM pre-check (Phase687W4T)

1. `cd C:\Users\yhach\Documents\tradebotfile\kabu_native; python -m small_paper.prebuild_vol_liq_startup_cache --date YYYYMMDD`
2. Start Kabu Station (manual)
3. `cd C:\Users\yhach\Documents\tradebotfile\kabu_native; $env:PYTHONPATH="src;C:\Users\yhach\Documents\tradebotfile"; python -m small_paper.check_kabu_readonly_readiness`
4. `python scripts/check_live_pipeline_preflight.py`
5. `python scripts/run_production_startup_smoke_test.py`
6. Start Paper normally (do not auto-enable orders)

If readiness fails, session is not counted as W4S readonly-success.
PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED

## Phase687W5 Order Request Contract (no network submit)

1. Build/validate payloads only: `python -m research.phase687w5_kabu_order_contract`
2. Confirm `actual_broker_submit_count=0` and network isolation PASS
3. Do **not** enable production ExecutionPolicy
4. Do **not** POST sendorder / cancel / flatten
5. On `REQUEST_MUTATION_DETECTED` → treat as RECOVERY_REQUIRED; never submit
6. On response timeout → UNKNOWN + reconcile; never auto-resubmit

## Phase687W5A Official Contract

1. `python scripts/check_kabu_sendorder_contract_consistency.py`
2. `python -m research.phase687w5a_official_contract_reconciliation`
3. Mock ENTRY fixtures must use ExchangePolicy SOR or TSE+ (not bare Exchange=1)
4. EXIT must use `REPAY_MATCH_OPEN_POSITION_EXCHANGE` with known open-position exchange
5. Do not select production SOR vs TSE+; cash orders remain NOT_IMPLEMENTED
6. ClosePositionOrder=0 requires explicit production policy review before adoption

## Phase687W5B Capability / Policy Shadow

1. `python -m research.phase687w5b_account_execution_policy_shadow`
2. Collect alongside W4S soak (≥3 sessions before any production policy discussion)
3. Never treat wiring MarginTradeType=3 as VERIFIED without live position observation
4. EXIT repay MTT/Exchange from broker lots only; exact HoldID close preferred
5. Shadow SOR/TSE+ and MARKET/LIMIT only — no production selection
6. Do not log raw HoldID / account numbers / tokens

## Phase687W5B1 Provenance

1. `python -m research.phase687w5b1_capability_provenance`
2. Fixture / synthetic / config never count as VERIFIED_FROM_LIVE_*
3. Monday W4S Forward: require live API provenance fields on soak snapshots
4. Zero positions: endpoint may succeed; MTT remains NOT_VERIFIED
5. Do not use fixture capability results as Execution Policy evidence

## Phase687W6 Production Enablement Gate

**PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED**

1. `python -m small_paper.check_production_enablement_readiness`
2. `python -m research.phase687w6_production_enablement_gate`
3. Exit 0 means technical conditions pass but orders remain NOT_AUTHORIZED — do not enable flags
4. Exit 2 soak不足 / 3 capability·policy / 4 recon·safety / 5 design·config
5. Do not generate APPROVED approval artifacts in this phase
6. Do not run canary; write adapter remains absent; HARD_FAIL submit/cancel

## Phase687W7 Operational Recovery

1. `python -m small_paper.check_live_order_recovery_readiness`
2. `python -m research.phase687w7_operational_recovery`
3. Exit 0 = dry-run recovery ready only — not production order authorization
4. Exit 2 journal·recon / 3 kill·ack / 4 disk·clock / 5 design·config
5. Never auto-clear kill switch / EXIT_ONLY / JOURNAL_RECOVERY without operator ack
6. Partial journal tail: keep original; use recovery copy only
7. Disk critical+: block ENTRY safety path; list cleanup candidates — never auto-delete protected artifacts

## Phase687W7A Stateful Recovery + Session Seal

1. `python -m research.phase687w7a_stateful_recovery`
2. Restart drills must write real journals then `restore_from_journal()` — compare restored objects
3. Runtime session start: real git_commit + config SHA (UNSET/demo → INCOMPLETE, not W4S success)
4. Runtime session end: finalize + full seal; duplicate finalize safe
5. Post-seal mutation → SESSION_SEAL_INVALID / MANUAL_REVIEW_REQUIRED
6. Synthetic PASS ≠ Monday forward market PASS (`FORWARD_SESSION_SEAL_PENDING`)

## Phase687W7A1 Assertion Integrity

1. `python -m research.phase687w7a1_recovery_assertion_integrity`
2. Do not use ambiguous `restored_order_count` for pass decisions
3. capital_reserved: intent=0 / order_aggregate=1 / active_reservation=1
4. kill_switch_active holds reservation (Policy A); release is a separate scenario
5. Negative oracle must FAIL when expected/actual tampered
6. W4S requires recovery_assertion_failure_count=0 and recovery_expected_actual_match=true

## Phase687W7A2 Seal Propagation

1. `python -m research.phase687w7a2_w4s_seal_propagation`
2. Do not treat snapshot seal fields as success unless they match session_seal.json
3. Finalize order: pre-seal snapshot → manifest (+disk) → full seal → propagate → resave
4. Do not rewrite session_manifest.json after seal
5. Synthetic full seal must show snapshot entry_count=14 / required_count=14 / missing=0

## Phase687W8 One-Command Runner

**Preferred daily command (only one):**

```bat
cd C:\Users\yhach\Documents\tradebotfile
.\run_paper_trade_checked.bat
```

Optional: `.\run_paper_trade_checked.bat --no-pause`

This runs disk/cache/Kabu/preflight/smoke/recovery/safety, then existing
`run_paper_trade.bat` once, then W4S forward soak. Do not set PYTHONPATH manually.
Do not auto-start Kabu Station. production enablement NOT_AUTHORIZED does not block Paper.
