# Config migration: paper_trade Tier1 / Tier2 dynamic watchlist

## When

- After upgrading to the version that introduces **Tier1 lightweight screening** and **Tier2 focused watch** for `paper_trade`.

## What to add (optional)

All keys live under the replay JSON top-level **`paper_trade`** object (same file as `max_signal_notify_lag_sec`, etc.). If you omit them, built-in defaults apply (Tier1/Tier2 **off**).

### `opening_light_mode` (extended)

| Key | Old behavior | New default |
|-----|----------------|-------------|
| `suppress_discord_signal_notify` | N/A | `false`. If `true` **and** opening-light window is active, **signal embeds are not sent** (CSV / summary still record; skip reason `OPENING_LIGHT_DISCORD_SUPPRESSED`). |

Existing configs that only set `enabled` / `until_hhmm` remain valid.

### `dynamic_watchlist` (new)

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `false` | When `true`, **Tier1** runs on a timer (`refresh_sec`) with **`fetch_quote` only**; **Tier2** runs the existing heavy path (1m + VWAP + signals + Discord) on **`max_symbols`** symbols selected from Tier1 + sticky rules. |
| `refresh_sec` | `300` | Minimum wall time between Tier1 full passes. |
| `max_symbols` | `15` | Tier2 watch size cap. |
| `sticky_sec` | `300` | After a **crossed_true** signal on a symbol, keep it sticky for this many seconds (watchlist merge prefers sticky symbols). |
| `max_symbols_opening_light` | `8` | Further cap on Tier2 size during **opening_light** window. |
| `tier1_per_symbol_timeout_sec` | `2.5` | Per-symbol timeout for Tier1 `fetch_quote`. |
| `tier1_total_timeout_sec` | `30` | Wall budget for one Tier1 pass across the universe. |

### `tier1_score_weights` (new)

Weighted subscores (each roughly 0–1) for Tier1 ranking. Keys must match the implementation (`volume_spike_score`, `vwap_distance_score`, `high_proximity_score`, `momentum_score`, `relative_strength_score`, `gap_score`). Omit to use code defaults.

## CLI overrides

- `--paper-trade-dynamic-watchlist` → sets `dynamic_watchlist.enabled` to **true** (merged with file).
- `--paper-trade-opening-light` → sets only `opening_light_mode.enabled` to **true** (no longer overwrites `until_hhmm`; file value kept).

## Artifacts

- `results/paper_trade/YYYYMMDD/paper_trade_tier1_snapshot.json` — written when Tier1 refresh runs.
- `paper_trade_runtime_state.json` — now may include `paper_tier2_symbols`, `paper_signal_sticky_until`, `paper_tier1_vol_ema`, rotation counters, etc.

## Backward compatibility

- **Default `dynamic_watchlist.enabled` is `false`**: behavior matches the previous single-loop over the full watchlist.

## paper_trade 候補状態通知（別途追加）

`paper_trade_log.csv` に列が増える場合があります（`previous_entry` / `previous_stop` / `previous_take` / `change_pct` / `invalidated_reason`）。既存 CSV と列が一致しない場合は起動時に **ヘッダ退避** されます。

追加キー（すべて `paper_trade` 直下・省略可）:

- `candidate_state_notify_enabled`（既定 `true`）
- `price_change_notify_threshold_pct`（既定 `0.5`）
- `symbol_notify_cooldown_sec`（既定 `180`）
- `candidate_vwap_break_invalidate_pct`（既定 `-0.5`）
