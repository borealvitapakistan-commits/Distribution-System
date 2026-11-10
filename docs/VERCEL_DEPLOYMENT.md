# Deploy Boreal Vita to Vercel with GitHub Actions

The Django pages, APIs, admin, PDFs, and static assets run in one Vercel project.
Supabase keeps the PostgreSQL database and uploaded files. GitHub Actions tests,
builds, and publishes the app. Vercel's automatic Git deployments are disabled.

**Plan eligibility:** Vercel Hobby permits personal, non-commercial use. A personal
test/demo may fit Hobby; running Boreal Vita's business operations needs an eligible
commercial plan. This configuration works on Hobby and Pro. Neither a free Vercel
account nor GitHub Actions makes database/storage usage unlimited. See
[Hobby terms](https://vercel.com/docs/plans/hobby) and
[Vercel pricing](https://vercel.com/pricing).

No code has been pushed and no deployment has been created as part of preparing
these files. Your current `.env` and database have not been migrated or replaced.

## 1. Keep the existing database and create private upload storage

Use your **existing Supabase project** if you want the same accounts, products,
inventory, orders, and settings online. Do not create an empty database unless
you want a fresh installation. Keep a database backup before the first release.

In Supabase:

1. Open **Connect** and copy the **Transaction pooler** PostgreSQL URL, port
   `6543`. This becomes `DATABASE_URL` for Vercel requests.
2. Copy the **Session pooler** URL, port `5432`, as `DATABASE_MIGRATION_URL` for
   migrations. Copy the actual host from Supabase; do not guess it from the region.
   URL-encode special characters in the password in both URLs.
3. Under **Storage**, create a bucket named `distribution-media`. Leave
   **Public bucket OFF**. Product images, logos, quotes, and payment proofs all
   use this bucket, with signed download links. Do not make proofs public.
4. In Storage's **S3 configuration**, generate an S3 access key pair and copy
   its **endpoint, region, access key ID, and secret access key**. These are S3
   credentials, not the Supabase anonymous key or the database password.

Existing database file fields keep their existing paths. Step 5 copies the
actual files from your laptop. A PostgreSQL backup alone does not contain images
or uploaded PDFs.

## 2. Create and link a separate Vercel project

In PowerShell, from this project directory:

```powershell
npx --yes vercel@65.10.0 login
npx --yes vercel@65.10.0 link
```

Choose your Vercel account/team, create a **new project** such as
`boreal-vita-distribution`, and use the current directory (`./`). Do not link
to the existing `manufacturing-app` unless you intend to replace that app.
`link` creates the project association without deploying this code. You do not
need to connect the repository to Vercel's Git integration.

In Vercel **Project Settings**:

| Setting | Value |
| --- | --- |
| Framework preset | Django |
| Root directory | Repository root, where `manage.py` lives |
| Build command | Leave the override OFF; native Django build |
| Install command | Leave the override OFF; `requirements.txt` |
| Output directory | Leave the override OFF |
| Fluid compute | Enabled (needed for the configured 300-second duration) |

Python 3.12 is selected by `.python-version`. Vercel finds `manage.py` and
`config/wsgi.py`, runs `collectstatic`, and serves `/static/` through its CDN.
There is no frontend-only build or separate API deployment to configure.

## 3. Enter production environment variables in Vercel

Open **Project Settings → Environment Variables**. Add these to **Production**.
Use `.env.example` as the complete reference, substituting your real values.
Do not upload your `.env` file to GitHub.

| Name | What to enter |
| --- | --- |
| `SECRET_KEY` | A long random Django secret; keep it unchanged across releases |
| `DEBUG` | `False` (Vercel also forces debug off) |
| `ALLOWED_HOSTS` | Your assigned domain, e.g. `boreal-vita-distribution.vercel.app` |
| `CSRF_TRUSTED_ORIGINS` | The same domain with `https://`, e.g. `https://boreal-vita-distribution.vercel.app` |
| `DATABASE_URL` | Supabase Transaction pooler URL, port `6543` |
| `DATABASE_MIGRATION_URL` | Supabase Session pooler URL, port `5432` |
| `DB_TRANSACTION_POOLER` | `True` |
| `DB_POOL_MIN` | `0` |
| `DB_POOL_MAX` | `1` |
| `USE_S3_STORAGE` | `True` |
| `S3_ENDPOINT_URL` | Exact S3 endpoint copied from Supabase |
| `S3_REGION_NAME` | Region copied from Supabase's S3 settings |
| `S3_BUCKET_NAME` | `distribution-media` |
| `S3_ACCESS_KEY_ID` | Generated Supabase S3 access key ID |
| `S3_SECRET_ACCESS_KEY` | Generated Supabase S3 secret access key |

For a new secret, run this locally after installing the requirements:

```powershell
.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

For password reset emails, also enter your SMTP provider's `EMAIL_HOST`,
`EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`,
`EMAIL_USE_SSL`, and `DEFAULT_FROM_EMAIL`. The example uses TLS on port 587;
for SSL on 465, set TLS to `False` and SSL to `True`.

If you use Shopify or Slack, copy the corresponding integration values from
your existing local environment. Keep `SHOPIFY_INVENTORY_SYNC=False` until you
intend to sync this installation's stock with the live Shopify store. No cron
or recurring sync is created by the deployment workflow.

For a custom domain, add both domains as comma-separated entries in
`ALLOWED_HOSTS` and add both HTTPS origins to `CSRF_TRUSTED_ORIGINS`. Hostnames
have no scheme or path; CSRF origins include `https://`. The app automatically
accepts its own Vercel deployment URLs without trusting other Vercel projects.

## 4. Add three GitHub secrets and one variable

Create a Vercel access token from **Vercel Account Settings → Tokens**. Scope it
to the account/team containing this new project.

Open `.vercel/project.json` locally after `vercel link`. Its `orgId` and
`projectId` identify the linked project. This file is ignored by Git.

In the GitHub repository, open **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
| --- | --- | --- |
| Repository secret | `VERCEL_TOKEN` | Your Vercel access token |
| Repository secret | `VERCEL_ORG_ID` | `orgId` from `.vercel/project.json` |
| Repository secret | `VERCEL_PROJECT_ID` | `projectId` from `.vercel/project.json` |
| Repository variable | `PRODUCTION_URL` | Your stable HTTPS address, e.g. `https://boreal-vita-distribution.vercel.app` |

App credentials remain in Vercel. GitHub downloads them into the ephemeral
runner using `vercel pull`; it does not publish them as an artifact. Tests use
an isolated PostgreSQL service with disposable credentials, not Supabase.

If production is protected by Vercel Authentication, optionally add the
repository secret `VERCEL_AUTOMATION_BYPASS_SECRET` from Vercel's
**Protection Bypass for Automation** setting so the health check can run.
Otherwise it is unnecessary. Use the stable production domain for
`PRODUCTION_URL`, not a unique deployment or preview URL.

## 5. Copy existing uploads before going live

Run these locally, with the existing `media` folder still present:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
npx --yes vercel@65.10.0 pull --yes --environment=production
.venv\Scripts\python.exe scripts/vercel_manage.py upload_media
.venv\Scripts\python.exe scripts/vercel_manage.py upload_media --apply
```

The first upload command is a dry run. The `--apply` command uploads missing
files while preserving names such as `brand/logos/logo_Boreal.png`, so existing
database references continue working. Existing objects are skipped rather than
overwritten. Re-running it should report zero new uploads. Run it again if new
files were uploaded locally during the transition.

These commands use the production settings downloaded into
`.vercel/.env.production.local`; they do not rewrite your local `.env`.
Keep the bucket private. The app serves its saved brand logo through its
existing logo endpoint; other uploads get temporary signed URLs.

For a fresh database, create an owner after the initial migrations have run:

```powershell
.venv\Scripts\python.exe scripts/vercel_manage.py createsuperuser
```

Skip that command if your existing owner account is already in the database.
No demo accounts or default passwords are created during deployment.

## 6. Push yourself, then run the deployment

Push all intended application changes to your GitHub repository, including
the new templates, static images, migration files, `.python-version`,
`vercel.json`, `.env.example`, `scripts/`, and `.github/workflows/vercel.yml`.
Leave `.env`, `.vercel/`, local databases, database dumps, and `media/` out of Git.

The workflow uses the `main` branch. If your production branch differs, update
the branch names and the deployment `if` condition in the workflow first.

On a normal push to `main`, GitHub Actions:

1. Checks for missing migrations and runs the entire test suite on disposable
   PostgreSQL using Python 3.12.
2. Downloads Vercel's production settings and builds the Python function plus
   static assets on the Linux runner.
3. Runs Django's production security checks and verifies that the database
   has no unapplied migrations.
4. Deploys the prebuilt output and checks `/health/` on the production domain.

**First deployment or a release with pending migrations:** inspect the plan:

```powershell
.venv\Scripts\python.exe scripts/vercel_manage.py showmigrations --plan
.venv\Scripts\python.exe scripts/vercel_manage.py migrate --plan
```

This repository already contains a migration
`accounts/0002_remove_distributor_panel.py` that drops old distributor tables
and removes distributor accounts. If your target database still contains that
data, confirm you have the intended database and a backup before applying it.
Those changes predate the Vercel preparation; no migration is applied by merely
building the app.

When ready, go to **GitHub → Actions → Test and deploy to Vercel → Run workflow**:
choose `main`, tick **Apply pending database migrations**, and run it. That run
builds first, applies pending migrations once, then publishes. Ordinary pushes
never apply them automatically. If there are no pending migrations, leave the
option off. A failed test, build, security check, or migration prevents publish.

Look for a green run and a deployment link in its summary. Open the stable
production URL, sign in, and check inventory, warehouses, the saved logo,
product images, a payment proof, and a PDF export.

## Operating limits and maintenance

- **Persistent data:** PostgreSQL and private Storage are external to Vercel.
  The function's filesystem is temporary. Only temporary PDF/upload buffers use
  it; migrations never run on cold starts or during `collectstatic`.
- **Uploads and PDF responses:** Vercel Functions have a 4.5 MB request/response
  limit. Keep browser uploads comfortably below 4 MB total per form submission.
  Large combined order-record PDFs can exceed the response limit. Supporting
  larger files requires direct storage uploads/downloads and a separate change;
  changing a Django size setting cannot raise Vercel's limit.
- **Long requests:** Fluid compute allows the configured 300-second maximum on
  Hobby. Large Shopify imports/syncs or long PDF jobs can still time out. This
  deployment does not add a persistent worker. If imports grow beyond that
  duration, run the existing management sync command outside HTTP requests.
- **Free-tier capacity:** watch Vercel, Supabase, and GitHub Actions usage. Free
  database projects may pause when inactive. Private GitHub repositories have
  included Actions minutes, not unlimited runners.
- **Releases:** no preview branch writes to production. Pull requests run tests
  only. Production deployments are serialized to avoid concurrent migrations.
- **Rollback:** Vercel can restore a previous deployment, but that does not
  undo database migrations or recover deleted data.
- **Configuration changes:** change app variables in Vercel and run the workflow
  again. Local `.env` edits do not change the deployed app. Storage credentials
  and the Django secret must remain available at runtime as well as build time.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Workflow asks for a missing secret/variable | Complete step 4 in this repository, using the new project's IDs |
| Publish stops at the schema check | Review `migrate --plan`, then use the manual migration option |
| Database connection/prepared-statement errors | Use the copied pooler URLs, correct password encoding, and the pool settings above |
| `DisallowedHost` or CSRF errors | Correct the domain and HTTPS origin lists; rerun the workflow |
| Saved logo/images/proofs are missing | Complete the media copy and verify the S3 bucket/keys reference the same Supabase project |
| Storage upload gets `AccessDenied` | Use generated S3 credentials and the exact region/endpoint; leave the bucket private |
| Health check returns 401/403 | Use the stable production URL or configure the optional protection bypass secret |
| Health check returns 503 | Check Vercel runtime logs and Supabase database availability |
| Request returns 413 | Reduce upload/PDF size; Vercel's payload limit is reached |
| Password reset email does not arrive | Configure a working SMTP provider and its verified sender |

References: [Django on Vercel](https://vercel.com/docs/frameworks/full-stack/django),
[GitHub Actions deployment](https://vercel.com/kb/guide/how-can-i-use-github-actions-with-vercel),
[Python runtime](https://vercel.com/docs/functions/runtimes/python),
[function limits](https://vercel.com/docs/functions/limitations),
[Supabase database connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[Supabase S3 credentials](https://supabase.com/docs/guides/storage/s3/authentication).
