# Cross Analysis data inputs

This frontend now reads Cross Analysis records from **`output/all_records.json`**.

## Required

Serve the file at one of these URLs:

- `https://<your-frontend-domain>/output/all_records.json` (recommended: place the file at `public/output/all_records.json`)
- or `NEXT_PUBLIC_API_BASE_URL/output/all_records.json`

The JSON should be an array of records with keys:

- `id`, `name`, `Primary Navigation`, `Secondary Navigation`, `Topic`, `Sub-topic`, `page`, `data`, `unit`, `year`, `detail`

## Optional (preferred)

If your backend can expose the ESGMetrics catalog, the Navigation tree will be sourced from it.
Otherwise, Navigation is derived from `all_records.json`.

The frontend attempts these endpoints (first successful wins):

- `/api/catalog/esgmetrics`
- `/api/catalog/esg_metrics`
- `/api/catalog/ESGMetrics`
- `/api/esgmetrics`
- `/api/esg-metrics`
