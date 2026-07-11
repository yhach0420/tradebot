# Operations Runbook — Monday Paper

## Command
```
cd C:\Users\yhach\Documents\tradebotfile && .\run_paper_trade_checked.bat
```

## Prerequisites
- Kabu Station running, logged in, API available
- PC sleep disabled
- Free disk space
- Windows time sync
- `.env` present with Discord webhooks configured (values not logged)
- Production YAML + sha256 pin aligned

## Start checks
- Capture started / CAPTURE_ONLINE
- Registration expected count matches
- Paper started

## AM end
- Paper AM finalized
- Capture still running
- unregister_all == 0 while capture active

## PM end
- Summary + Shadow Summary
- W4S evaluation ran once

## 15:35
- Capture finalized
- Seal valid
- Review drops / registration mismatch metrics
