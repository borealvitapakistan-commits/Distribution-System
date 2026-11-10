# GitHub integration and data-transfer compatibility

Completed: 2026-11-10

## Integrated source

Fetched origin/main at `07d79f7`, five upstream commits ahead of the previous common base. Preserved all upstream changes: owner-only app and distributor-panel removal, PostgreSQL/Supabase configuration, private S3 uploads, Vercel deployment, database-backed brand logos, new UI, purchasing/inventory improvements, and page/auth/brand caching. Preserved the three local export/import commits. Resolved four conflicts in .gitignore, core URLs, settings and the Settings menu.

Merge commit: `1e177e4` (parents retain both histories).

## Required adaptations

- PostgreSQL import locks all transferred tables before reading the backup, defers FK checks, checks constraints before committing and resets sequences after restoring explicit IDs. Lock acquisition has a 15-second timeout. SQLite obtains its writer lock before the backup read.
- PostgreSQL export obtains SHARE locks to keep the exported tables consistent while concurrent reads remain possible.
- Database-backed BrandLogo bytes are serialized as records, rather than attempting to read a nonexistent logo file. Legacy filesystem logos still round-trip.
- Hosted backups use the configured private default storage under `data-backups/`; local backups retain private filesystem permissions and fsync. Backups remain outside replaced database tables. No hosted backup writes depend on Vercel's ephemeral/read-only project directory.
- Import assigns a fresh persistent cache version and clears this worker's caches after commit, preventing reuse of previously cached pages. Transfer routes bypass page caching. Current-worker cached sessions are cleared along with the deleted database sessions and API tokens.
- Settings menu follows the upstream icon/navigation layout. The transfer page uses the new panel header/body spacing.
- The isolated test settings can start without a production DATABASE_URL. Transfer permission tests now reflect the upstream owner-only role model.

## Verification

- Full isolated SQLite suite: 352 tests passed.
- Full isolated PostgreSQL 17 suite: 352 tests passed, including 12 transfer tests and all migrations on a fresh PostgreSQL test database.
- The test count differs from the earlier 403-test suite because upstream intentionally removed distributor apps/tests and added other tests.
- Transfer checks cover records, FK/M2M relationships, database/legacy logos, file attachments, account/password preservation, private dated backups and repeated backup retention, hosted-storage routing, rollback, invalid/truncated CSV, unsafe paths, missing owner, backup failure, cache refresh, sequence continuation and HTTP flow.
- Browser on isolated sample SQLite database: sign in, Settings dropdown, transfer page, CSV download, upload of that same download, replacement acknowledgement, successful import, private dated backup file, automatic logout and successful sign-in using the restored account.
- Desktop and 390px mobile layouts inspected. No horizontal overflow; adjusted panel spacing to match the new UI. Final 12-test transfer suite passed after template changes.
- Django check, migration drift check and whitespace check passed.

## Runtime boundaries

No live Supabase connection, migration, import or storage operation was performed. Supplied secrets were not committed or copied into test artifacts. The existing .env and original local SQLite database were left unchanged. Actual S3 service access was not exercised; hosted routing used isolated storage in tests, while upstream deployment tests validate signed/private S3 configuration.

PostgreSQL was installed for local verification; the temporary localhost test server was stopped after the suite. An isolated sample preview remains at localhost:8001 with temporary settings/database/media under /tmp/distribution-integration-preview. Real runtime credentials/storage configuration remain a separate deployment concern. No GitHub push or deployment performed.
