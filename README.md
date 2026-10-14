# Datum — Fusion 360 → Plex tooling sync for Grace Engineering

Nightly automation that syncs Autodesk Fusion 360 tool library data into Rockwell Automation Plex
Manufacturing Cloud (ERP). Fusion 360 JSON files on a local network share are the absolute source
of truth. As of the April 2026 architecture pivot, Fusion data lands first in a Supabase
database (enriched source of truth — geometry, holder pairings, pocket assignments) and then an
identity slice (vendor part number + description) is pushed on to Plex's `supply-items` endpoint.
The React UI reads from Supabase; Plex gets only what its schema can accept.

## Status

| | |
|---|---|
| **Plex environment** | `connect.plex.com` (production) — there is no test environment for this app |
| **Plex app** | `Datum` Consumer Key, expires every 31 days (next rotation: 2026-05-08, issue #12) |
| **Plex tenant** | `58f781ba-1691-4f32-b1db-381cdb21300c` (Grace Engineering) |
| **Tooling endpoint** | `inventory/v1/inventory-definitions/supply-items` filtered to `category="Tools & Inserts"` (1,109 records) |
| **Workcenters** | `production/v1/production-definitions/workcenters` (143 records, including 21 mills mapping directly to Brother Speedio FTP IPs) |
| **Supabase** | dedicated `datum` project (us-east-2), 3 tables — `libraries`, `tools`, `cutting_presets` |
| **Phase** | **Phase B complete** — `validate_library.py` pre-sync gate landed (#25). Phase A-Python (Supabase upsert layer) is next. |
| **Tests** | 583 pytest tests, all green (2026-10-14). CI on PRs to master via GitHub Actions. Branch protection requires the check to pass. |
| **Nightly sync** | Runs on GitHub Actions (`.github/workflows/nightly-sync.yml`, 00:17 America/Chicago in CDT). Rollout in progress; see [Deployment and operations](#deployment-and-operations). |

## Architecture

```
Fusion 360 JSON (network share, via Autodesk Desktop Connector)
        │
        ▼
  validate_library.py          ← pre-sync gate: abort if library is invalid (#25)
        │
        ▼
  sync_supabase.py             ← upsert full tool records into Supabase  [Phase A-Python]
        │
        ├──▶  Supabase (bulletforge)   ← enriched source of truth
        │           │
        │           └──▶  React UI     ← tool library browser  [Phase D+]
        │
        └──▶  sync_plex.py             ← identity slice only → supply-items  [Phase C]
```

Why the pivot: Plex's `supply-items` schema is identity-only — vendor part number and description,
nothing else. Geometry, holder pairings, and pocket assignments have no home in Plex. Supabase
holds the full record; Plex gets the slice it can accept.

The original plan to write to `mdm/v1/parts` and `tooling/v1/tool-assemblies` was incorrect — see
[BRIEFING.md "History of incorrect hypotheses"](./docs/BRIEFING.md) for the postmortem.

## Quick start (local development)

1. **Clone and set up credentials**

   ```powershell
   git clone https://github.com/grace-xwerks/datum.git
   cd datum
   ```

   Credentials load through `bootstrap.py` from three sources, highest
   precedence first: **shell environment, Infisical, `.env.local`**.

   - **Infisical (preferred).** Install the Infisical CLI and put a machine
     identity file at `~/.config/datum/infisical-identity.json`
     (`clientId`, `clientSecret`, `projectId`). Datum's keys are folder
     `/datum` of the Grace Engineering Infisical project (`dev` env by
     default). Setup, variables and the identity model are in
     [`docs/SECRETS.md`](./docs/SECRETS.md).
   - **`.env.local` (fallback).** `copy .env.example .env.local` and fill in
     your Datum Consumer Key + Secret from
     [developers.plex.com](https://developers.plex.com/) → My Apps → Datum.

   `.env.local` and the identity file are never committed. With neither
   present, nothing loads and the code runs unconfigured.

2. **Install dependencies**

   ```powershell
   py -m venv .venv
   .venv\Scripts\python -m pip install -r requirements-dev.txt
   ```

3. **Run the local endpoint tester**

   ```powershell
   py run_dev.py
   ```

   Opens on http://localhost:5000. The left rail has buttons for:
   - **Diagnostics** — `tenant_whoami` (run this first to verify connection)
   - **Plex presets** — verified Plex API URLs as one-click hits
   - **Extractors** — `extract_supply_items` (1,109 cutting tools), `extract_parts`, `extract_purchase_orders`, etc.
   - **Fusion 360 local** — `tools_stats` and `consumables_only` for verifying the local Fusion library load

   `run_dev.py` overrides shell environment variables with `.env.local` (the opposite of
   `bootstrap.py`'s production-safe `setdefault` semantics), so a stale system env var won't
   silently shadow your real key. It skips any key Infisical supplied, so a stale `.env.local`
   can't beat Infisical either. To see which source supplied each credential, use the
   Diagnostics preset (`credential_sources`) or
   `.venv\Scripts\python -c "import bootstrap; print(bootstrap.format_sources())"`.

4. **Run tests**

   ```powershell
   .venv\Scripts\python -m pytest
   ```

   Use the venv: bare `py` may lack Flask and fail at collection.

5. **Validate a Fusion library before syncing**

   ```powershell
   # Production mode — PASS/FAIL only, exit code 0 or 1
   py validate_library.py --file "BROTHER SPEEDIO ALUMINUM.json" --no-api

   # Verbose — shows WARN issues too
   py validate_library.py --file "BROTHER SPEEDIO ALUMINUM.json" --no-api --verbose

   # With live Plex supplier lookup for VENDOR_NOT_IN_PLEX checks
   py validate_library.py --file "BROTHER SPEEDIO ALUMINUM.json" --verbose
   ```

   The validator catches duplicate product-ids, missing required fields, non-positive geometry,
   unknown tool types, and vendors that won't resolve to a Plex supplier. The sync layer gates on
   a PASS; FAILs abort the sync before anything touches Supabase or Plex. Full rule table in
   [`docs/validate_library_spec.md`](./docs/validate_library_spec.md).

## Deployment and operations

The **nightly sync runs on GitHub Actions**, not on a local machine. It replaces the two
GRACE144 Windows Task Scheduler jobs, which never got a library past the validation gate
after 2026-04-10 and are being retired ([#18](https://github.com/grace-xwerks/datum/issues/18));
the first live Actions run succeeded on 2026-10-14.

- **Workflow:** `.github/workflows/nightly-sync.yml`. Cron `17 5 * * *` UTC (00:17
  America/Chicago in CDT). It runs `datum-sync` (Fusion tool libraries from Autodesk APS into
  Supabase), then `datum-sync-inventory`. A failure is a red run with the log tail in the step
  summary and the full log as a 14-day artifact.
- **Run it by hand:** `gh workflow run nightly-sync.yml --repo grace-xwerks/datum`, or add
  `-f dry_run=true` to validate without writing libraries. A dry run still rotates the APS
  refresh token.
- **Secrets:** plain repository secrets that Infisical syncs into GitHub (`prod`, `/datum`).
  The runner has no Infisical access. The rotating APS refresh token is not a secret in
  either place: it lives in the Supabase table `public.runtime_state` and is rewritten on every
  run. Seed or re-seed it with `datum-aps-consent`.
- **Local runs** (`py sync.py`, `datum-sync`) use APS as the only source and read the same
  `bootstrap.py` sources as dev. The old fallback to local Fusion files is now explicit-only
  (`--local`).

Everything an operator needs (where each secret lives, the token lifecycle, what each failure
message means, rotation runbooks, the Infisical setup checklist) is in
[`docs/SECRETS.md`](./docs/SECRETS.md).

## Production safety

This codebase reads from real Grace Engineering production data on every API call. Two guard rails
protect against accidental writes:

- **`PlexClient.get_envelope()`** returns structured success/error envelopes so HTTP failures
  are visible (PR #15 fixed an earlier "swallow on error" bug).
- **`/api/plex/raw` proxy refuses POST/PUT/PATCH/DELETE** when running against
  `connect.plex.com` unless `PLEX_ALLOW_WRITES=1` is set in the environment (PR #17). Read-only
  is always allowed. To enable writes:

   ```powershell
   $env:PLEX_ALLOW_WRITES = "1"
   py run_dev.py
   ```

  The UI shows a red `WRITES ON` chip when the guard is disabled. Rotate the env var off as soon
  as you're done.

## Key references

- [`docs/BRIEFING.md`](./docs/BRIEFING.md) — primary context document for AI-assisted dev sessions and the
  source of truth for current status, current credentials, gotchas, and project history
- [`docs/SECRETS.md`](./docs/SECRETS.md) — credentials, Infisical, the APS refresh-token lifecycle,
  and the nightly sync runbook
- [`docs/Plex_API_Reference.md`](./docs/Plex_API_Reference.md) — verified endpoint access matrix and URL
  pattern conventions
- [`docs/Fusion360_Tool_Library_Reference.md`](./docs/Fusion360_Tool_Library_Reference.md) — Fusion JSON
  schema and field-to-Plex mapping
- [`docs/validate_library_spec.md`](./docs/validate_library_spec.md) — design spec for the pre-sync
  validation gate; implemented as `validate_library.py` (#25)
- [`TODO.md`](./TODO.md) — project roadmap mirrored to GitHub Issues
- [GitHub Issues](https://github.com/grace-xwerks/datum/issues) — live status of every phase work
  item with dependencies and blockers
- [Plex Manufacturing Cloud API docs](https://www.rockwellautomation.com/en-us/support/plex-manufacturing-cloud/api.html)

## Contributing workflow

1. Branch from `master`
2. Push to a `claude/<short-name>` branch (or any branch — naming is convention, not enforced)
3. Open a PR to `master`
4. CI runs `pytest` automatically
5. Branch protection blocks merge until the check is green
6. Use `gh pr merge --auto --squash` to enable auto-merge — it lands the PR the moment CI passes

## License

Internal Grace Engineering project. Originated as a fork of
`just-shane/plex-api`; standalone since 2026-10-14.
