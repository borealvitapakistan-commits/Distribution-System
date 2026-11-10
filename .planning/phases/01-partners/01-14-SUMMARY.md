# Safe GitHub / Vercel release

Completed: 2026-11-10

## Published release

- Repository: https://github.com/borealvitapakistan-commits/Distribution-System
- Release PR: https://github.com/borealvitapakistan-commits/Distribution-System/pull/1
- Reviewed release head: `4be74d4e60ec628b909dc516f87174ef4c831c36`.
- Application merge on main: `8ec60112411fa65d63a6716b7e9b3f67b2ba48d7`.
- Production: https://distribution-system-six.vercel.app
- Previous working source retained at annotated rollback tag `pre-workbook-release-20261110`, pointing to `07d79f73332e21ed8dc29bf414f8d9ad13113073`.

Fetched the remote before release. Remote main had no new divergence; preserved all histories and used a normal merge, not a force-push. Local main was fast-forwarded to the merged remote. The production source tree matches the checked release head.

## Safety checks and hardening

- Scanned outgoing commits and working changes against actual local secret values (including parsed database password) without printing those values: no matches.
- Confirmed .env, .vercel credentials, local databases, backups, media, workbook exports and ZIP/dump artifacts were not tracked. Added desktop metadata ignores. Build exclusions continue to remove private files and test/development artifacts.
- Vercel Git integration is active; merging main triggers production. Updated deployment documentation to reflect the real Git integration and current CI workflow rather than the outdated manual deployment instructions.
- Hosted workbook uploads now use a 4 MB browser/server limit below Vercel's 4.5 MB request cap. Local uploads remain 100 MB. Large hosted exports are saved privately and delivered via existing expiring signed storage URLs, avoiding oversized function responses. Added regression tests.
- No production data import/reset, credential change or migration was triggered by release preparation/push. Previously authorized logo migration was already applied. No runtime startup/build migration hooks were added.

## Verified checks

- 34 local transfer tests passed after release hardening; pip dependency check passed; whitespace checks passed.
- PR GitHub CI succeeded: fresh PostgreSQL, system/migration checks and all 375 tests. Run: https://github.com/borealvitapakistan-commits/Distribution-System/actions/runs/42794834227
- Vercel preview build succeeded: https://vercel.com/borealvitapakistan-commits-projects/distribution-system/5PATc9FTNX4zSpYK1mKr4J6mTV1F
- Preview URLs redirect to Vercel authentication; public runtime health could not be inspected through that protection. No bypass was attempted. Production health/login were verified before and after release.
- PR was mergeable, CLEAN, with all checks successful. Merged using an exact head-commit match to the checked SHA.
- Main CI succeeded: https://github.com/borealvitapakistan-commits/Distribution-System/actions/runs/42797080433
- Production Vercel deployment succeeded: https://vercel.com/borealvitapakistan-commits-projects/distribution-system/9MuoPynfAHbKg5yMug22F8gQFB3c
- Production `/health/`: HTTP 200, status ok, on the actual production hostname.
- Production `/accounts/login/`: HTTP 200, Sign In · Boreal Vita, on the actual production hostname.

This summary/state follow-up changes only planning metadata; application code remains the exact tested release. A subsequent metadata push may produce another Vercel deployment with identical runtime code.
