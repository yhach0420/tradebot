# Phase563 — Shadow EXIT Daily Monitor Pilot

**Verdict:** `phase563_shadow_exit_daily_monitor_pilot_ready`

## Config

```yaml
exit_shadow_monitor_enabled: true
exit_shadow_monitor_t2_enabled: true
exit_shadow_monitor_t3_enabled: true
```

Rollback: `exit_shadow_monitor_enabled: false`

## Mandatory answers

1. actual EXIT unchanged: True
2. T3 shadow computed: True
3. T2 shadow computed: True
4. zero-trade safe: True
5. Discord added: True
6. preflight pass: True
7. rollback possible: True
8. tests pass: True
9. paper trade ready: True
10. next phase: phase564_live_exit_shadow_monitor_observation
