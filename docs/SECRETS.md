# Secrets and the nightly sync — operator reference

Where every Datum credential lives, who reads it, how to rotate it, and what
to do when the nightly sync goes red. **No secret values appear in this file or
anywhere else in the repo. Never paste one here.**

Design record: [`docs/superpowers/specs/2026-10-14-infisical-and-actions-sync-design.md`](./superpowers/specs/2026-10-14-infisical-and-actions-sync-design.md)
(approved 2026-10-14, amended the same evening after the Infisical billing
review). Issues: [#16](https://github.com/grace-xwerks/datum/issues/16)
(Infisical), [#18](https://github.com/grace-xwerks/datum/issues/18) (nightly sync
on Actions), [#28](https://github.com/grace-xwerks/datum/issues/28) (follow-up:
keep the Infisical access token off argv). #16 and #18 stay open until the
rollout checklist at the bottom is complete.

## The short version

- **Infisical is the source of truth** for every static credential. Project
  `Grace Engineering` (`8e04285d-bdf4-4836-b1d6-f2d026e1574a`), folder
  `/datum`, environments `dev` and `prod`. Edit values there, nowhere else.
- **Local dev** reads Infisical through `bootstrap.py` with a machine identity
  file. `.env.local` is the fallback.
- **The nightly sync runs on GitHub Actions** and reads plain **repository
  secrets** that Infisical pushes into GitHub. The runner has no Infisical
  CLI and no Infisical identity.
- **The one rotating credential, the APS refresh token, is not in Infisical.**
  It lives in a Supabase row that the runner rewrites on every run.

## Where each secret lives

All Infisical keys sit under folder `/datum` of the Grace Engineering project,
in both `dev` and `prod`. The GitHub secret has the same name as the key.

| Key | Infisical | GitHub secret | Consumer | Notes |
|---|---|---|---|---|
| `PLEX_API_KEY` | `/datum` dev + prod | `PLEX_API_KEY` (required) | `plex_api.py` | Consumer Key of the **`Manage2Plex`** app, shared with plex_mcp (copied from the Grace Engineering project root; Datum no longer uses a separate "Datum" Developer Portal key). One rotation covers both repos. Expires every 31 days ([#14](https://github.com/grace-xwerks/datum/issues/14)). |
| `PLEX_API_SECRET` | `/datum` dev + prod | `PLEX_API_SECRET` (optional) | `plex_api.py` | The `Manage2Plex` secret, if set; Plex authenticates on the key alone today. |
| `PLEX_TENANT_ID` | `/datum` dev + prod | `PLEX_TENANT_ID` (optional) | `plex_api.py` | Not secret. The code default is the Grace tenant. |
| `SUPABASE_URL` | `/datum` dev + prod | `SUPABASE_URL` (required) | `supabase_client.py` | Not secret. The `datum` project (`crimblieyypiyarssoel`). |
| `SUPABASE_SERVICE_ROLE_KEY` | `/datum` dev + prod | `SUPABASE_SERVICE_ROLE_KEY` (required) | `supabase_client.py` | Bypasses RLS. Server-side only; never ship it to a browser. |
| `APS_CLIENT_ID` | `/datum` dev + prod | `APS_CLIENT_ID` (required) | `aps_client.py` | Client ID of the **"Datum" APS app**. Not the root-level `APS_*` keys in Grace Engineering: those are the "Manage Lab" app and fail with `Request error` / `invalid_credentials`. |
| `APS_CLIENT_SECRET` | `/datum` dev + prod | `APS_CLIENT_SECRET` (required) | `aps_client.py` | Secret of the **"Datum" APS app** (same warning as above). |
| `APS_REFRESH_TOKEN` | **not in Infisical** | **not a GitHub secret** | `SupabaseTokenStore` | Row `aps_refresh_token` in `public.runtime_state` (datum Supabase project). See the lifecycle section. |

That is 7 keys in Infisical. Deliberately stored nowhere:

- `PLEX_ALLOW_WRITES` (the production write guard; never set on the runner),
- `PLEX_BASE_URL` (test-only override),
- any `INFISICAL_*` identity credential on the runner.

`PLEX_API_SECRET` and `PLEX_TENANT_ID` are optional for the workflow: an empty
repo secret is dropped before the sync starts so the code defaults apply.

## Identity model (and why there is no new identity)

The Infisical org is on the **Legacy Pro** plan, which bills **$18 per
identity**. On 2026-10-14 the org went from 6 identities to 3:

| Identity | Used for |
|---|---|
| Shane (human) | Infisical UI. |
| `github-actions-capitoltrace` | Another project's CI. Not used by Datum. |
| `grace-workstation-shane` | Machine identity on Shane's workstation (Universal Auth). Covers plex_mcp **and** Datum local dev. This is `plex-mcp-workstation-shane` renamed; the rename is pending on Shane's side. |

3 identities is $54/month. Datum adds **zero** and **no new Infisical project**
(projects are free, identities are not):

- Datum's keys are a folder (`/datum`) of the existing Grace Engineering
  project, so the one workstation identity reads both plex_mcp's and Datum's
  secrets without them sharing a path.
- The runner needs no identity because it does not call Infisical at all. Static
  secrets arrive as repo secrets through the Infisical GitHub sync
  `datum-secret-sync` (source `prod` `/datum`, target repository
  `grace-xwerks/datum`), and the rotating token lives in Supabase. An Infisical
  write-back identity on the runner would have cost a paid seat; a shared
  cross-org OIDC identity is not clean (single bound subject, and wildcarding
  the owner is warned against). That design was superseded on 2026-10-14.

Adding a new Infisical machine identity for Datum is a cost decision. Do not do
it without Shane's say-so.

## Local dev setup

`bootstrap.py` is imported first by every entry point. It loads credentials into
`os.environ` with `setdefault` semantics, so **precedence is
shell env > Infisical > `.env.local`**: a real environment variable always wins,
and a key present in both Infisical and `.env.local` takes the Infisical value.

### 1. Install the Infisical CLI and put the identity file in place

The CLI must be on `PATH`. The identity file is
`~/.config/datum/infisical-identity.json` (on Windows,
`%USERPROFILE%\.config\datum\infisical-identity.json`):

```json
{
  "clientId": "<universal-auth client id>",
  "clientSecret": "<universal-auth client secret>",
  "projectId": "8e04285d-bdf4-4836-b1d6-f2d026e1574a",
  "path": "/datum"
}
```

`path` is optional (default `/datum`). Reuse the client id and secret of
`grace-workstation-shane`: copy `~/.config/plex-mcp/infisical-identity.json` and
add the `projectId`, or point `DATUM_INFISICAL_IDENTITY_FILE` at the plex-mcp
file. Do not create a new identity.

**Never commit the identity file or `.env.local`.** `.env.local` is gitignored;
the identity file lives outside the repo.

### 2. Variables that steer the loader

| Variable | Default | Meaning |
|---|---|---|
| `DATUM_INFISICAL_PROJECT_ID` | `projectId` from the identity file | Infisical project to export from. |
| `DATUM_INFISICAL_ENV` | `dev` | Environment to export. |
| `DATUM_INFISICAL_PATH` | `path` from the identity file, else `/datum` | Folder to export (`--path=`). Keeps plex_mcp's keys out of Datum's environment. |
| `DATUM_INFISICAL_IDENTITY_FILE` | `~/.config/datum/infisical-identity.json` | Alternate identity file. |
| `INFISICAL_UNIVERSAL_AUTH_CLIENT_ID` / `_CLIENT_SECRET` | unset | If both are set they replace the file's `clientId`/`clientSecret`. |
| `DATUM_DISABLE_INFISICAL` | unset | Any value turns the Infisical loader off. Set by the workflow and by `tests/conftest.py`. |

With no identity file and no `INFISICAL_UNIVERSAL_AUTH_*` pair, the Infisical
loader does nothing, silently, and `.env.local` is used. If Infisical is
configured but fails (CLI missing, timeout, bad credentials, bad output), you
get exactly one `WARNING: bootstrap: Infisical secrets not loaded: …` and the
process carries on with whatever the other two sources provide. Importing
`bootstrap` never raises because of Infisical.

### 3. Which source won?

This exists because a stale shell `PLEX_API_KEY` once shadowed `.env.local` for
45 minutes (BRIEFING "History of incorrect hypotheses" §4).

- `py run_dev.py`, then run the **Diagnostics** preset (`/api/diagnostics/tenant`,
  `tenant_whoami`). Its `credential_sources` list gives, per credential, the
  source (`shell`, `infisical`, `.env.local` or `unset`), the first 8
  characters and the length.
- `datum-sync` logs the same table at startup under `Credential sources:`
  (from `bootstrap.format_sources()`).
- Never more than 8 characters of a value are ever shown. Reported names:
  `PLEX_API_KEY`, `PLEX_API_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`,
  `APS_CLIENT_SECRET`, `APS_REFRESH_TOKEN`.

`run_dev.py` still lets `.env.local` override stale shell variables, but it
**skips any key that Infisical supplied**, so a stale `.env.local` can never beat
an Infisical value. It prints the names it skipped.

### 4. Local APS auth is separate from production

Local dev keeps its own consent in `.aps_tokens.json` (gitignored,
`APS_TOKEN_STORE=file`, the default). **Never set `APS_TOKEN_STORE=supabase`
locally.** The production token has exactly one consumer, the nightly runner
(next section).

## APS refresh-token lifecycle

The Autodesk APS **refresh token is single-use and rotates on every refresh**,
and it expires after **15 days idle**. Production's copy is the row
`aps_refresh_token` in `public.runtime_state` (migration
`db/migrations/0008_runtime_state.sql`) in the datum Supabase project. The table
has RLS enabled with **no policies**, so only the service-role key can read or
write it. The row holds `value`, `previous_value` (what it held before the last
write), `updated_at` and `updated_by`.

**Seed** (once, and after any token death), from a workstation:

```powershell
datum-aps-consent
```

(`py scripts/aps_consent.py` works too. `datum-aps-consent` exists after
`pip install -e .`.) It needs `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
`APS_CLIENT_ID` and `APS_CLIENT_SECRET`, which bootstrap supplies from Infisical
`dev`, **so make sure `dev` points at the datum Supabase project.** It reads
the destination first, so a bad configuration never burns an authorization
code. Then it prints the Autodesk authorize URL; open it, sign in, consent. Autodesk
redirects to `APS_CALLBACK_URL` (default
`http://localhost:5000/api/aps/callback`). The page does not need to load, and
the Flask app should **not** be running or it will consume the code. Paste the
`code` value (or the whole URL) at the prompt. The script exchanges it in memory
(no `.aps_tokens.json` is written), upserts the row with
`updated_by="datum-aps-consent"`, and prints
`seeded public.runtime_state[aps_refresh_token] (prefix xxxxxxxx…, len N)`. It
never prints the token. `--code <code>` skips the prompt. `--store infisical`
seeds `prod/APS_REFRESH_TOKEN` in Infisical instead, for the kept-but-unused
`InfisicalTokenStore` (`--project-id`, `--env` apply to that mode only).

**Rotation** (every run, by the runner, `APS_TOKEN_STORE=supabase`):

1. `SupabaseTokenStore` reads the row.
2. `refresh_access_token()` trades it for a new access token and a new refresh
   token. Autodesk consumes the old one at that moment.
3. The new refresh token is upserted into the row (old value into
   `previous_value`) **before** any API call uses it. If that write fails, the
   run stops with `refresh token write-back failed` and no API call is made on
   the new chain.
4. Only then does the hub traversal start.

**Exactly one consumer.** Anything else that refreshes with the production token
(a local run pointed at the Supabase store, a copy pasted into another tool, a
second workflow) consumes it, and the runner's next refresh is rejected. The
local `.aps_tokens.json` flow is a different consent and a different chain.

**A dry run still rotates the token.** `--dry-run` skips the Supabase library
writes, but the refresh that authenticates the run consumes the old token and
writes the new one back. A dispatch with `dry_run=true` is safe in that it
changes no library data; it is not a no-op for the token.

**The 15-day rule.** If the sync does not complete a refresh for 15 days (runs
failing before the APS step, scheduled workflows paused, workflow disabled), the
token dies and the next run is rejected. GitHub also stops scheduled workflows on
a repo with no activity for 60 days, so a long quiet spell needs both a re-seed
and a fresh dispatch. A green nightly run is what keeps the token alive.

**"refresh token rejected — re-seed with datum-aps-consent"** means Autodesk
returned a non-2xx on the refresh: the token is dead (15 idle days) or was
already consumed by something else, or the write-back of a previous run failed
after Autodesk consumed the token (the row then holds a spent value). Recovery:

1. Run `datum-aps-consent` from a workstation (above).
2. Re-run the workflow: `gh workflow run nightly-sync.yml --repo grace-xwerks/datum`.
3. If it keeps happening, look for a second consumer before re-seeding again.

## The nightly workflow

`.github/workflows/nightly-sync.yml` (its header comment carries the same
mapping and failure notes).

- **Schedule:** `cron: "17 5 * * *"`, 05:17 UTC, which is 00:17
  America/Chicago in CDT (23:17 the evening before in CST). Off the top of the
  hour on purpose: GitHub can delay or drop `:00` schedules, and plex_mcp's
  first `:00` schedule was dropped. GitHub may still start a run a few minutes
  late.
- **Manual run:** `workflow_dispatch` with input `dry_run` (boolean, default
  false). A dry run reads and validates everything, writes no libraries to
  Supabase, and still rotates the APS token.
- **Steps:** checkout, Python 3.12, `pip install -e .`, **Credentials present**,
  **Fusion -> Supabase sync** (`datum-sync`), **Tool-qty sync**
  (`datum-sync-inventory`, a warning and exit 0 until tools are linked to Plex, PR #34), **Summary**
  (status plus the last 25 log lines of each step in the run's step summary,
  `user:password@` masked), **Upload logs** (artifact `nightly-sync-logs`, 14
  days).
- **Environment:** the step sets `APS_TOKEN_STORE=supabase` and
  `DATUM_DISABLE_INFISICAL=1`. Plex writes are never enabled and the Plex base
  URL is never overridden. Runs are serialized (`concurrency: nightly-sync`).
- **Exit codes (`datum-sync`):** 0 all libraries synced, 1 some libraries failed
  validation (at the STAGING stage) or upsert (the rest were synced), 2 fatal / nothing processed.

Without `--local`, APS is the **only** source. A dead token or missing config
logs one `CRITICAL` line naming the cause and exits 2; there is no fallback to
local ADC files, because that would hide a dead token.

### Validation stage

`datum-sync` validates each library at the **STAGING** stage (`ValidationStage.STAGING`,
PR #32; spec: [`validate_library_spec.md`](./validate_library_spec.md), "Stages").
At STAGING a missing or duplicate `product-id` and a missing `description` are
warnings, not failures: the Supabase layer dedups on `fusion_guid`, and
`populate_supply_items` skips tools without a `product-id` before anything
reaches Plex. The stricter `PLEX` stage (the default for `validate_library`) is
what a future Plex writeback ([#1](https://github.com/grace-xwerks/datum/issues/1)) will use. So a red `datum-sync` (exit 1)
now means a real STRUCT, guid, type or geometry failure in a library, not a
missing `product-id`; if you see `product-id` errors in a failure, something
is validating at the wrong stage. Every Fusion library has tools without a
`product-id`, so the warnings in the log are expected. Duplicate `product_id`
values reaching `plex_supply_items` are tracked in [#33](https://github.com/grace-xwerks/datum/issues/33).

### Reading a red run

| What you see | Meaning | Fix |
|---|---|---|
| **Credentials present** fails: "Missing repository secret(s): …" | A required repo secret is missing or empty. | Add or fix it in Infisical `prod` `/datum`. `datum-secret-sync` pushes it to GitHub. Do **not** set it by hand in GitHub; the next sync overwrites it. Then re-run. |
| `APS sync failed (config error)` | `APS_CLIENT_ID` or `APS_CLIENT_SECRET` (or Supabase URL/key for the token store) is missing. | Same as above. |
| `APS sync failed (auth error): … is missing or empty — seed it with datum-aps-consent` | The `runtime_state` row was never seeded (or was deleted). | Seed with `datum-aps-consent`. |
| `APS sync failed (auth error): could not read public.runtime_state …` | Supabase read failed: wrong URL or key, project unreachable, or migration 0008 not applied. | Check `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` in Infisical prod; confirm the table exists. |
| `APS sync failed (auth error): refresh token rejected — re-seed with datum-aps-consent` | Dead or consumed token. | Re-seed (lifecycle section), re-run. |
| `APS sync failed (auth error): refresh token write-back failed: …` | Autodesk issued a new token but the Supabase upsert failed, so the row now holds a spent token. The run stopped before using the new chain. | Fix the Supabase problem named in the message, then re-seed with `datum-aps-consent`. |
| `datum-sync` exit 1 | Some libraries failed validation (STAGING: a real STRUCT / guid / type / geometry failure) or upsert. | The failed names are in the step summary; fix the library in Fusion (or the validator) and re-run. |
| **Tool-qty sync** skipped | It only runs when the sync step succeeded. | Fix the sync step first. |
| No run at all for a night | GitHub skipped or delayed the schedule. | Look for a gap in `public.libraries.ingested_at`; dispatch a run by hand. |

### Commands

```powershell
# Run it now
gh workflow run nightly-sync.yml --repo grace-xwerks/datum
# Dry run
gh workflow run nightly-sync.yml --repo grace-xwerks/datum -f dry_run=true
# Watch / inspect
gh run list --repo grace-xwerks/datum --workflow nightly-sync.yml
gh run view <run-id> --repo grace-xwerks/datum --log-failed
# Repo secrets present (names and updated-at only)
gh secret list --repo grace-xwerks/datum
```

Success looks like `public.libraries.ingested_at` advancing every night.

## Rotation runbooks

### Plex Consumer Key (every 31 days, [#14](https://github.com/grace-xwerks/datum/issues/14))

1. developers.plex.com, My Apps, `Datum`: generate the new Consumer Key.
2. Infisical, Grace Engineering, `/datum`: update `PLEX_API_KEY` in **both**
   `dev` and `prod`. If the app also issued a new secret, update
   `PLEX_API_SECRET` the same way.
3. Wait for `datum-secret-sync` to push `prod` to GitHub; confirm with
   `gh secret list --repo grace-xwerks/datum` (check the updated time on
   `PLEX_API_KEY`).
4. Locally, delete any stale `PLEX_API_KEY` from `.env.local` and from your
   shell or user environment (a User-level Windows variable shadows everything).
   Run `py run_dev.py`, open the Diagnostics preset, and confirm
   `credential_sources` shows `PLEX_API_KEY` from `infisical` with the new
   8-character prefix and a `match` of `grace`.
5. Dispatch the workflow with `dry_run=true` and confirm it is green.
6. Update #14 with the date and the next deadline.

### APS client credentials

Static, so no per-night rotation. When rotating on aps.autodesk.com:

1. Change the value in Infisical `/datum` for **both** `dev` and `prod`
   (`APS_CLIENT_SECRET`, and `APS_CLIENT_ID` if the app itself changed), and
   let `datum-secret-sync` push `prod`.
2. A new client id (a different APS app) means the stored refresh token no
   longer belongs to the app: re-seed with `datum-aps-consent` using the new
   values. For a new secret on the same app, do a `dry_run=true` dispatch; if
   it reports `refresh token rejected`, re-seed. (Whether Autodesk invalidates
   existing refresh tokens on a secret change has not been verified in this
   repo.)

### Supabase service-role key

Rotate in the Supabase dashboard, update `SUPABASE_SERVICE_ROLE_KEY` in
Infisical `dev` and `prod`, let the sync push it, then dispatch a dry run. Rotate
the key before re-seeding the APS token if both are due: the seed uses it.

## Infisical-side checklist (Shane, in the UI)

This is the list from the spec. An agent produces it with the exact key list and
never sees a value.

1. In **Grace Engineering**, create folder `/datum` in both `dev` and `prod`.
2. Add the 7 keys from the table above to `/datum` in both environments.
   `APS_REFRESH_TOKEN` is **not** created anywhere in Infisical.
3. Rename machine identity `plex-mcp-workstation-shane` to
   `grace-workstation-shane`; keep its viewer access and confirm it can read
   `/datum` in `dev`. Copy its identity file to
   `~/.config/datum/infisical-identity.json` and add
   `"projectId": "8e04285d-bdf4-4836-b1d6-f2d026e1574a"` (or set
   `DATUM_INFISICAL_IDENTITY_FILE`).
4. Create GitHub secret sync `datum-secret-sync`: source `prod` `/datum`,
   target repository `grace-xwerks/datum`, auto-sync on, deletion protection
   on. Confirm all 7 names appear under the repo's Actions secrets.
5. Nothing else: no CI identity, no `INFISICAL_*` repo secrets.

### Rollout order

1. Code and docs merged (this document is part of that step).
2. Migration `db/migrations/0008_runtime_state.sql` applied to the datum
   Supabase project (overlord, via the Supabase MCP). The checklist above done.
   Then seed the row once with `datum-aps-consent`.
3. `workflow_dispatch` with `dry_run=true` is green; then a real dispatch
   advances `ingested_at`. **Done 2026-10-14:** run 38625485799 succeeded
   (10 libraries, 224 tools, 753 presets) and every `ingested_at` advanced.
4. The first **scheduled** run is green. Then disable the two GRACE144 Task
   Scheduler jobs ("Datum Nightly Sync" 00:00, "Datum Nightly Inventory Sync"
   00:30; neither ever wrote, because the CLI failed validation until PR #32), then delete them along with
   that host's `.env.local` and `.aps_tokens.json`.
5. Close #16 and #18 by hand, and update the Notion Current State block and
   Decision Log.

Open follow-up: [#28](https://github.com/grace-xwerks/datum/issues/28), the
Infisical access token currently travels on the CLI's argv (`--token=…`) for the
length of the export.
