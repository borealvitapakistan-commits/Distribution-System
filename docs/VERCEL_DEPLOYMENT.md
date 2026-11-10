# Deploying the Distribution System on Vercel

The repository uses Vercel's Git integration. Pushing or merging to the configured production branch (`main`) creates a production deployment. Other branches create previews. GitHub Actions runs Django checks and tests; it does not deploy or apply database migrations.

## Release procedure

1. Fetch `origin/main` and integrate its current changes. Preserve remote history; never force-push production.
2. Keep `.env`, credentials, local databases, media, workbook exports and `data/backups/` out of Git. Build files also exclude those directories.
3. Push a release branch and open a pull request. Wait for the GitHub PostgreSQL tests and Vercel preview check. Preview environment variables must be configured for the preview to run; never substitute missing values with production secrets in source code.
4. Inspect the preview and its `/health/` endpoint where deployment protection permits it. Confirm no pending production migrations. Retain the previous production commit/deployment as the rollback reference.
5. Merge the checked commit into `main`. Verify the production Vercel check and `/health/` after deployment. A green build alone does not verify database readiness.

## Runtime configuration

Configure secrets in Vercel Project Settings, separately for Production and Preview. Use `.env.example` as the list of variables, not as production values.

- `SECRET_KEY`: persistent strong secret; `DEBUG=False` (Vercel also forces debug off).
- `DATABASE_URL`: Supabase PostgreSQL transaction pooler URL for runtime. `DB_TRANSACTION_POOLER=True` avoids unsupported prepared/server cursors; existing settings configure a small connection pool.
- `DATABASE_MIGRATION_URL`: session pooler URL for explicitly approved migrations. Do not print credentials or pass database URLs as command arguments.
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`: the project's actual stable domains. The app also adds its own Vercel deployment hostnames.
- `USE_S3_STORAGE=True`, `S3_ENDPOINT_URL`, `S3_REGION_NAME`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`: persistent private upload storage. Signed URLs protect access to uploaded files. Vercel's project filesystem is not used for permanent files/backups.
- Email and Shopify variables: see `.env.example`. Deployment does not create demo accounts, import workbooks or enable automatic inventory changes.

## Workbook transfers

The Export & Import Data screen uses one business workbook with a sheet per category. It preserves login accounts and permissions, and saves a complete dated private backup before replacing business records.

Vercel Functions limit request/response bodies to 4.5 MB. The hosted upload form therefore allows up to 4 MB, including client/server checks and room for multipart headers. Local development retains its 100 MB limit. Larger hosted exports are saved privately and downloaded through an expiring storage URL, rather than returning a large function response. Large-file uploads beyond 4 MB require a future direct-to-storage upload flow.

Already-missing attachments retain their original paths in an Attachment Issues sheet. Storage permission/network failures still abort. Available files are included; unavailable bytes are not fabricated. Owners receive a notice if backup/import files contain declared missing attachments.

## Migrations and rollback

Production builds and runtime startup do not run migrations automatically. For a pending migration, review its operations, create/verify an appropriate private backup, apply it explicitly using the session pooler, and verify record counts and health. The importer refuses unapplied migrations before changing records.

`core.0007_remove_brand_logo_data` has already been applied to the connected Supabase database. Its preservation step handles missing legacy logos on other databases; redeploying the app does not re-run an applied migration.

For a code regression, restore the previous Vercel production deployment. That changes code, not database state. Do not reverse schema changes or restore data automatically as part of a code rollback. Use a reviewed migration/backup procedure if database recovery is needed.

## Checks

- `/health/`: HTTP 200 and `{"status":"ok"}` means the app can query its database.
- GitHub `Django tests`: fresh isolated PostgreSQL, system check, migration drift check and full test suite.
- Vercel deployment check: successful build and deployment; verify runtime health separately.

References: [Vercel Git integration](https://vercel.com/docs/git), [Function limits](https://vercel.com/docs/functions/limitations), [Environment variables](https://vercel.com/docs/environment-variables), [Deployment promotion](https://vercel.com/docs/deployments/promoting-a-deployment).
