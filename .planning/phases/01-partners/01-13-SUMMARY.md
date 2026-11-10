# Missing attachments no longer block faithful record backups

Completed: 2026-11-10

## Diagnosis and recovery

Supabase records referenced 88 external file paths. None of those objects existed in the configured private bucket, whose only initial object was the previously created database dump. There were no matching full-path suffixes elsewhere in that bucket. 74 lookups returned missing; 14 non-ASCII names returned provider HTTP 400 rather than 404. Exactly one original path had a matching local file.

Restored `products/WhatsAppImage2026-09-29at20.32.46.jpg` (74,995 bytes) from the exact local path to the absent Supabase key, verified SHA256 readback, and did not overwrite another object or modify database references. 87 original external attachments remain unavailable in connected storage/local media; their bytes were not invented or claimed as backed up.

Private diagnostic/recovery manifests: `data/backups/attachment-recovery/diagnostic.json`, `recovered.json`.

## Fixes

- Full backups and business exports include all record data and available attachments. Already-absent files are explicitly listed in an Attachment Issues sheet, preserving their original paths and record references. Reads/restores accept only these declared omissions; missing/corrupted parts of files that were actually included still fail.
- Missing source files no longer prevent backing up records before replacement import. Backups preserve the previous missing state, rather than clearing file fields. Incoming available files restore normally; import confirmations report unavailable attachments in the backup/import.
- S3 directory listings verify missing keys and avoid unreliable HEAD responses for absent non-ASCII names. Directory listings are cached per storage/directory. List permission denial falls back to reading the file; permission/network/read failures remain blocking and identify the exact model/record/field/path. They are never classified as confirmed absence.
- Live verification exposed historical `DISTRIBUTOR` stock choice values. Hidden original-value metadata preserves unchanged exported historical choices while changed/new invalid values still fail validation; scalar types/validators remain enforced.
- Live business exports also exposed populated invoice-approval account links. All account FKs are now server-managed in business mode. Prior delivered workbooks containing nullable approval-user columns remain accepted, but those source account IDs are ignored; existing current account links are preserved.

Implementation commit: `225dfa7`.

## Actual backup/export verified

Created and read back a private current-record workbook successfully:

- Local: `data/backups/attachment-recovery/records-with-attachment-report-20261110-143226.xlsx`.
- Hosted: `data-backups/records-with-attachment-report-20261110-143226.xlsx`.
- SHA256 of hosted readback matched the local workbook.
- 1,544 records retained; one available external attachment plus the existing database-backed logo; 87 missing external references declared explicitly.
- Current business export also generated and parsed successfully: 783 business rows, with the same 87 declared missing attachments.
- Full verification manifest: `data/backups/attachment-recovery/verified-backup.json`.

No live workbook import, business record replacement/deletion or database schema change occurred during this fix. Recovering the remaining file CONTENTS requires their original uploads; current record references remain preserved for later recovery.

## Tests and runtime

- Full SQLite suite: 373 passed.
- Full isolated PostgreSQL suite: 373 passed.
- 32 transfer tests cover explicit missing-file round trips, restoring incoming healthy files despite an existing missing reference, unchanged record paths, permission/network failures, Unicode S3 absence, public import notices, unchanged historical values, invalid edits and old approval-column compatibility, as well as prior atomic restore/backup protections.
- Migration drift/system/whitespace checks passed. No new migration required.
- Supabase-connected main app restarted with the fix at localhost:8001, session 72652; health HTTP 200 / status ok. Temporary PostgreSQL server stopped.
