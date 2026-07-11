# TradeBot Event / JSONL Schema Overview

## Paper live events
- Producer: `pilot_runner` / observer dispatch
- Typical fields: symbol, accept/reject, gate_reject_reason, entry/exit prices,
  mfe/mae, yen_100, trailing/no_progress flags, board_dynamic_trailing_* 

## Capture push parts
- `data/market_capture/YYYYMMDD/push_part_NNNN.jsonl`
- Append-only; new part via max(existing)+1 + O_CREAT|O_EXCL
- Status: `capture_status.json`, heartbeat, PID file
- Seal: `capture_seal.json` at 15:35

## Registration
- `runtime/market_registration_manifest.json`
- Generation events on refresh

## Discord audit / dead-letter
- Under notify audit modules; secrets redacted

## Session seal
- `session_seal.json` SoT with artifact sha256 + row counts
