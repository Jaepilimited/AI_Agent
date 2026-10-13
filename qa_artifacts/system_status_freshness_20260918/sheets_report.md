# Google Sheets sources freshness audit

Observed 2026-09-18 15:40–15:45 KST. Production WAS 10.1.150.5, MariaDB ai on 10.1.200.5, Qdrant Craver. Read-only: no sync, refresh, application edits, deployment or notifications performed. Database sessions used READ ONLY transactions and rollback; Sheets/Drive GET and Qdrant retrieve/count only.

## Result

No content freshness delay was found in the assigned Sheets sources. Five stored sources were compared with current parsed source content. Inventory expiry has three lot-code letter-case differences, with all quantities, dates and other fields matching. BP memory content cannot be hashed from a separate process; source modification time, successful reload and live readiness count support currentness.

All times below are KST on 2026-09-18 unless a different date is shown.

| Source | Stored data | Copy/reload timestamp | Current source evidence | Finding |
|---|---:|---|---|---|
| BP product Q&A | Live memory 923 Q&As | 15:40:02 successful reload | Drive modified 09-18 11:51:38; independently normalized source has 923 Q&As at 15:43 | Source was reloaded after modification. Parent observed live `/health/ready` at 15:43: `cs_loaded=true`, `cs_db_count=923`. Live-memory byte equality not inspected. |
| OP inventory | 15,018 SKU/location rows | 11:20:02 | Live source exact content hash matches 15,018 rows; source label 2026. 9. 18 | Current copy confirmed. Drive metadata unavailable (404); direct source content was compared instead. |
| OP expiry | 3,264 lot rows | 11:20:06 | Current sheet label `9/17 마감 재고 현황`; all 3,264 rows match after normalizing lot letter case | Quantities/dates/current source content match. Three letter-case-only differences documented below. |
| Awards/rankings | 206 rows | 04:40:02 | Exact source/copy hash equality; Drive modified 09-07 15:58:04 | Current copy confirmed. |
| Model rights | 34 models + 301 period records | 04:30:02 | Exact equality in both tables; 3 source tabs; Drive modified 09-15 12:04:54 | Current copy confirmed. |
| Product ingredients | 84 products | 04:00:04 | Exact source/copy hash equality; Drive modified 09-17 17:39:44 | Current copy confirmed. |
| PR dedicated sheet | 356 entries | Manifest 04:50:00; job complete 04:50:07 | Source = local manifest = all 356 owned cloud payloads; exact hashes match. Drive modified 09-16 18:39:43. No pending recovery. | Current local and Qdrant copies confirmed. |

OP inventory and expiry ordinarily read Sheets live for answers. MariaDB is their fallback copy. The expiry source itself reports the prior day's close; this is the source's stated data date, not a failed copy update.

## Job history

All six job types have zero recorded failures in the last seven days. OP ran 14 times, BP ran 168 times, and the other four job types ran seven times each.

| Job | Schedule KST | Latest success completion | Most recent recorded failure |
|---|---|---|---|
| cs_cache_hourly | Every hour :40 | 15:40:02 | 09-09 19:41:00; source reload failed and existing cache retained |
| op_inventory_sync_daily | 11:20 and 16:20 | 11:20:06 | None in retained job history |
| awards_sync_daily | 04:40 | 04:40:02 | None in retained job history |
| model_rights_sync_daily | 04:30 | 04:30:04 | 08-12 04:31:00; read timeout |
| ingredient_sync_daily | 04:00, retry 06:00 unless already successful | 04:00:05 | 08-27 04:01:00; read timeout |
| pr_issues_sync_daily | 04:50 | 04:50:07 | None in retained job history |

## Expiry letter-case detail

Live source `FG021` versus stored `fg021` for SKU EUSKA065, KRSKA037 and USSKA108. The respective quantities are unchanged at 18,447, 16,400 and 4,454, and all three expiry dates are unchanged at 2029-07-10. MariaDB lot column collation is `utf8mb4_general_ci`; the sync updates non-key fields on duplicate keys, so existing letter case in the unique key can remain. This is a representation discrepancy, not evidence that an inventory quantity or date update was missed.

Normalizing only lot letter case yields matching hashes over all 3,264 records, with no missing or extra records. No correction was performed.

## Scope and limits

- This covers BP Q&A, OP inventory/expiry, awards, rights, product ingredients, and the dedicated PR sheet. The main audit handles Notion and BigQuery, including domestic/overseas CS dashboard sources.
- Source content was parsed using the exact production readers. Local and production code hashes match for all seven relevant modules checked. Thus equality is over the fields and tabs consumed by the current application, not arbitrary unused spreadsheet cells.
- Product ingredient-to-BigQuery mapping has 116 rows, also updated 04:00:04. The job reports 248 BigQuery product names, 116 matched and 132 unmatched. That coverage limitation is separate from freshness; the 84 source ingredient products themselves exactly match.
- These are point-in-time observations. Drive metadata is file-level; content hashes establish equality for the application's selected content.

## Evidence files

- `sheets_stats.json`: copy counts/times, job history, manifest state and deployment hash comparison.
- `sheets_inventory.json`: inventory/expiry raw hash comparison.
- `sheets_expiry_details.json`: three letter-case differences.
- `sheets_expiry_normalized.json`: normalized expiry equality and database collation.
- `sheets_awards.json`, `sheets_ingredients.json`, `sheets_rights.json`: source/table hashes.
- `sheets_pr.json`: source/manifest/cloud hashes and exact cloud count.
- `sheets_drive.json`: current source modification timestamps and OP 404.
- `sheets_cs.json`: independently loaded BP source row count/hash.
- `sheets_probe.py`: reproducible read-only probes.

## Supplement: BP product information from Notion

At 15:49 KST, inspected the current Notion source tree and page metadata, with 19 read-only requests. This is a separate source from Google Sheets product Q&A.

- Notion root: `3362b428-3b00-8050-b4b6-c75fe4f7f54a` (`스킨1004 전제품 한 눈에 파악하기`).
- Current source: 8 line rows and 55 product pages. MariaDB `product_info`: 55 products, last copied 2026-09-18 04:20:29 KST.
- No source product pages missing from the copy and no copied pages missing from the source.
- Product title, line and source URL fields match for all 55 products.
- Latest source product-page modification: 2026-04-13 16:05 KST. No source product page was modified after its copy timestamp.
- **Classification: current by source tree and modification metadata; product-body content hashes were not compared.** The original page metadata and copy times do not independently prove byte equality of all nested body blocks.
- Evidence: `sheets_product_info_tree.json`.
