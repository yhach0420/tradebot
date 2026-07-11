# Directory Map (Runtime-relevant)

```
tradebotfile/
  run_paper_trade_checked.bat
  run_paper_trade.bat
  kabu_native/
    configs/                          # production YAML + pin + cluster model
    scripts/                          # PS1 launchers, AM/PM CLI, runtime_gate
    src/
      small_paper/                    # paper runtime, capture, seal, safety
      notify/                         # W10 Discord stack
      runner/                         # am_pm_daily_runner
      research/                       # exposure_gate, W4S, structural exits
      universe/                       # intraday refresh
      api/                            # kabu register
    data/market_capture/YYYYMMDD/     # capture parts/seal
    results/small_paper/              # paper sessions
    results/reports/                  # checked runner logs, research reports
    runtime/                          # registration lock/manifest
    tests/                            # runtime_gate_manifest.json + suites
    docs/current_system_design/       # this specification
```
