# Phase465A — Trend Tournament Audit

**Verdict:** `phase465_invalid`

## 1. r5–r30 count=0

phase464 cache stores high_update/vwap features but return_5/10/15/30min_pct present on only 0/29460 cohort rows. Trend path: {'high_update_only': 29460}. Phase465 Part A reads alias r5-r30 via _rise() → all None.

## 2. Trend 29460 → accepted 0

Replay pool trend=25806. T1 requires r30>0 but r30 non-null in replay=638. Trend+T1 fail (missing r30): 25806. Trend+runtime_core in replay: 7173. Capacity replay accepted=0 when trend_gate_runtime_count=0.

## 4. Cohort identity

Same key set: **True** ({'464': 29460, '465': 29460})

## 5. Trend path (Phase464/465 cohort)

{'high_update_only': 29460}

See `phase465a_trend_gate_funnel.csv` for T1–T10 funnel counts.