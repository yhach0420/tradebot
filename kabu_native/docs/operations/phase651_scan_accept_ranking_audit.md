# Phase651: Scan Accept Ranking Audit

Research audit of `max_entries_per_scan` adoption order for PBv2/OR gate-pass candidates.

## Ranking (code)

`entry_scan_controller.candidate_rank_score` then `_flush_locked` stable sort.

```
rank_score =
  v2*1000 + cq*100 + min(tv/1e9,20) + imb*10 + max(vwap_dev,0)*5 + mom*50 - price_age*100
```

Tie-break: embedded in score; exact ties → enqueue order (PUSH eval order within scan window).

Production: `max_entries_per_scan: 1`, `entry_scan_window_sec: 2.0`.

## Run

```bash
python scripts/run_phase651_scan_accept_ranking_audit.py
python -m pytest tests/test_phase651_scan_accept_ranking_audit.py -q
```

## Artifacts

```
results/reports/phase651_scan_accept_ranking_audit/
  phase651_report.json
  phase651_ranking_rule_map.csv
  phase651_blocked_candidate_outcome.csv
  phase651_alternative_ranking_counterfactual.csv
```

## Verdict

`phase651_scan_accept_ranking_audit_done`
