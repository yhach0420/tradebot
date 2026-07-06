# Phase645: Pre-session Warmup Register

## Purpose

Complete Kabu connect / token / WebSocket / register / pipeline init **before**
`allowed_entry_start`, so the first gate evaluation can run within 60s of 09:03 (AM)
or 12:33 (PM).

## Config (`live:` block)

```yaml
pre_session_warmup_enabled: true   # rollback: false
pre_session_warmup_am_start: "08:50"
pre_session_warmup_pm_start: "12:15"
```

## Behavior

| Phase | Time | Action |
|-------|------|--------|
| Wait | until 08:50 / 12:15 | `wait_until(warmup_start)` |
| Init | 08:50 / 12:15 | connect, token, register, pipeline init |
| Warmup PUSH | 08:50 → allowed_entry_start | ring update only (no ENTRY gate) |
| Entry eval | from allowed_entry_start | full Stage0–6 pipeline |

Legacy (`pre_session_warmup_enabled=false`): unchanged — `wait_until(session_start)` then init (~15min ready delay).

## Summary fields

- `session_ready_ts` — after successful register
- `first_gate_eval_ts` — first full pipeline eval
- `ready_delay_sec` — `first_gate_eval_ts - allowed_entry_start`
- `pre_session_warmup_ring_push_count`

## Discord

`[System Health]` / Runtime Health includes `pre-session ready delay: X.Xs`.

## Run audit

```bash
python scripts/run_phase645_pre_session_warmup.py
python -m pytest tests/test_phase645_pre_session_warmup.py -q
```

## Rollback

Set `live.pre_session_warmup_enabled: false` in production YAML. **No `run_paper_trade.bat` change.**

## Verdict

`phase645_pre_session_warmup_done`
