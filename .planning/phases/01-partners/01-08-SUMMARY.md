# Settings export/import with dated backups

Completed: 2026-11-10

- Owner Settings now includes Export & Import Data at `/owner/settings/data/`.
- One UTF-8 CSV contains an ordered, versioned manifest, all persistent installed-model records (including accounts, groups/permissions, audit history and explicit/automatic relations), and base64-encoded referenced uploads. JSON field payloads retain decimals, Unicode, multiline values and IDs. Login sessions and API tokens are excluded.
- Import accepts an unchanged export up to 100 MB, checks schema, completeness/checksum, field decoding, file references and active-owner availability, and requires an explicit replacement acknowledgement.
- Before deletion, writes and fsyncs a dated private CSV backup under `data/backups/data-imports/`, with exclusive filename creation and file mode 0600. Backup failure aborts replacement. Backups are filesystem artifacts outside the replaced records and remain after later imports.
- Replaces all persistent tables and automatic many-to-many tables within one SQLite IMMEDIATE transaction; validates foreign keys before commit. A failed restore rolls back the database and removes new attachment copies. Original attachment files are preserved; successful imports use new file paths.
- Raw fixture saves now bypass user-account signals to preserve the exported group membership without accidental side effects. Successful imports invalidate sessions and API tokens; owners sign in with imported credentials.
- Backup browsing remains deferred per the user's request. No migration required.

## Verification

- Full Django suite: 403 tests passed, including 8 data-transfer tests.
- Round trip, protected product/manufacturer links, audit actor, user groups/password, embedded attachments, repeat-backup retention and private permissions verified.
- Truncation/checksum failure, unsafe file path, no active owner, backup storage failure, simulated late constraint failure and actual invalid foreign key verified without losing current records.
- Owner-only page/download/upload access and explicit replacement acknowledgement verified through HTTP tests.
- Existing real database was exported and validated read-only: 1,142 records, 3 attachments, 1,298,227 bytes. Imported into an isolated temporary SQLite copy and exported again: all counts and file contents preserved. Live data unchanged.
- Django system check, migration drift check and git diff whitespace check passed.

## Commits

- `ecae692` — complete CSV snapshots and transactional replacement backups.
- `1d1af38` — Settings UI/endpoints and data-transfer tests.
