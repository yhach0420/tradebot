# ADR-687W4T — Kabu Token + Read-Only Readiness

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

Forward soak blocked by opaque TOKEN_REQUEST_FAILED. Need fine-grained diagnostics without leaking credentials.

## Decision

1. Add `small_paper.kabu_readonly_readiness` + CLI `python -m small_paper.check_kabu_readonly_readiness`.
2. Classify station/port/password/auth/timeout/invalid/empty/readonly outcomes separately.
3. Bounded retries; AUTH_FAILED never auto-retries.
4. Mask passwords/tokens/account-like digits in all artifacts.
5. Submit/cancel/flatten remain HARD_FAIL and independent of token success.
6. Paper mainline unchanged; capital unknown may block dry-run would-submit only.

## Consequences

- Monday pre-check can fail closed on auth/config without enabling orders.
- Weekend Station-down ≠ password-missing.

## Rollback

Disable CLI usage; keep `live_order_safety_sm_enabled` as-is. Do not enable live trading.
