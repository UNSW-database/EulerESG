# Cross Analysis Records JSON Schema (v2)

This project expects the **records JSON** (produced from both table extraction and image-based extraction) to follow the schema below.

## Field meanings

| Field | Meaning |
|---|---|
| `id` | Report id (same as `file_id`) |
| `name` | Company name / report display name |
| `primary_navigation` | Primary Navigation (一级导航) |
| `secondary_navigation` | Secondary Navigation (二级导航) |
| `topic` | Metric topic name (e.g., Scope 1, Scope 2, Total energy, etc.) |
| `sub_topic` | Sub-topic to disambiguate the same `topic` (e.g., location-based vs market-based, different calculation bases, etc.) |
| `page` | Source page number in the report |
| `data` | Extracted value (string); the UI will parse numbers when drawing charts |
| `unit` | Unit for the value |
| `year` | Reporting year (string) |
| `detail` | Interpretation / explanation for the data point |

## Example record

```json
{
  "id": "<REPORT_ID>",
  "name": "Bosch (2024)",
  "primary_navigation": "Environment",
  "secondary_navigation": "GHG Emissions",
  "topic": "Scope 2",
  "sub_topic": "location-based",
  "page": 32,
  "data": "123,456",
  "unit": "tCO2e",
  "year": "2024",
  "detail": "Reported operational electricity-related emissions using the location-based method."
}
```

## Backward compatibility

The UI also contains a small adapter (`src/features/crossAnalysis/recordAdapter.ts`) to normalize legacy responses where:

* `topic` = primary navigation
* `type` = secondary navigation
* `label` = metric name
* `detail` = remark/sub-topic
* `context` = interpretation

However, **the recommended output is the v2 schema above**.
