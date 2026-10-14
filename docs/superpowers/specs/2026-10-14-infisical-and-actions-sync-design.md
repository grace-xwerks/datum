# Design: Infisical secrets + nightly sync on GitHub Actions

**Date:** 2026-10-14
**Issues:** [#16](https://github.com/grace-xwerks/datum/issues/16) (Infisical),
[#18](https://github.com/grace-xwerks/datum/issues/18) (nightly sync → Actions)
**Status:** approved in conversation 2026-10-14 (Shane). **Amended 2026-10-14
(evening)** after the Infisical billing review: Infisical bills $18 per identity
on the org's Legacy Pro plan, so the design now adds **zero** identities and no
new project, and the rotating APS refresh token lives in a Supabase row instead
of Infisical. Agents A, B and C landed before the amendment; B2 reconciles the
token store, D and E follow the amended text. The work-breakdown table at the end
is the implementation plan.

## Goal

The Fusion → Supabase nightly sync runs again, on GitHub Actions, with every
secret sourced from Infisical instead of hand-copied `.env.local` files, and
with failures that are visible (a red run and an email) instead of a local log
file nobody reads. GRACE144's two Task Scheduler jobs are retired.

Success: `public.libraries.ingested_at` advances every night; a dead APS token,
a missing secret, or a validation FAIL produces a red scheduled run; Shane can
rotate the Plex key in one place.

## Non-goals

- Changing what the sync does. `validate_library` gate, dry-run semantics,
  Supabase writes, and the post-sync `plex_supply_items` staging are unchanged.
- Plex writes. `PLEX_ALLOW_WRITES` is never stored in Infisical or set on the
  runner.
- Rotating the Plex key itself (#14).
- The `datum/` package reorg or SQLAlchemy swap (`docs/REORG_AND_STACK.md`).

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Infisical placement | Folder `/datum` in the **existing Grace Engineering project** (`8e04285d-bdf4-4836-b1d6-f2d026e1574a`), envs `dev` + `prod` | Projects are free but identities are $18/month each on Legacy Pro; one Grace project lets one workstation identity cover plex_mcp and Datum. Datum's keys sit in their own folder so plex_mcp's `Manage2Plex` keys and Datum's Consumer Key never share a path. (Superseded: "new project `datum`".) |
| Production APS refresh token | **Supabase row** (`public.runtime_state`, service-role only) | APS refresh tokens are single-use and rotate on every refresh (15-day idle expiry); one consumer only. Infisical write-back would need a machine identity on the runner: a shared cross-org OIDC identity is not clean (single bound subject; wildcarding the owner is warned against) and a dedicated one is $18/month. The runner already holds the service-role key, so the row costs nothing and adds no credential. Previous value kept in a column for recovery. (Superseded: Infisical write-back; `InfisicalTokenStore` stays in the code, unused by default.) |
| Identities | **None new.** `plex-mcp-workstation-shane` → renamed `grace-workstation-shane`, granted read on `/datum` in `dev`. No CI identity: the runner needs no Infisical call at all. | Org identity count stays at 3 (Shane, `github-actions-capitoltrace`, `grace-workstation-shane`) = $54/month on Legacy Pro. |
| Secrets into the runner | Infisical → GitHub secret sync (plex_mcp pattern) | The runner reads plain repo secrets; **no Infisical code path in CI at all** now that the token lives in Supabase. The Infisical CLI is not installed on the runner. |
| Local dev auth to Infisical | Machine identity (Universal Auth) via identity file | A human `infisical login` expires every 10 days and draws an interactive prompt on stdout (grace-cam, 2026-09-12). |
| Cron | `17 5 * * *` (00:17 America/Chicago in CDT) | plex_mcp's first `:00` schedule was dropped by GitHub under load. |
| Tool-qty sync | Second step in the same workflow | It is a no-op until tools are linked, but it is the second GRACE144 job and should die with the first. |

## Architecture

```
Infisical project "Grace Engineering" (8e04285d…), folder /datum
  ├── env dev   ── machine identity grace-workstation-shane (read /datum) ──▶ bootstrap.py (local dev)
  └── env prod  ── GitHub secret sync datum-secret-sync (/datum) ──▶ repo secrets ──▶ nightly-sync.yml

Supabase project datum (crimblieyypiyarssoel)
  └── public.runtime_state  ◀── SupabaseTokenStore: read APS refresh token at run start,
        (RLS on, no policies;      write the rotated one back before any API call uses it
         service role only)

nightly-sync.yml (schedule 17 5 * * * + workflow_dispatch)
  checkout → python 3.12 → pip install -e . → credentials present
  → datum-sync --log-file → datum-sync-inventory --log-file → summary + artifact
  (no Infisical CLI, no Infisical identity on the runner)
```

### Secrets inventory

| Key | Env | Consumer | Notes |
|---|---|---|---|
| `PLEX_API_KEY` | dev, prod | `plex_api.py` | Rotates every 31 days (#14) |
| `PLEX_API_SECRET` | dev, prod | `plex_api.py` | Optional today |
| `PLEX_TENANT_ID` | dev, prod | `plex_api.py` | Not secret; code default is the Grace tenant |
| `SUPABASE_URL` | dev, prod | `supabase_client.py` | Not secret |
| `SUPABASE_SERVICE_ROLE_KEY` | dev, prod | `supabase_client.py` | Bypasses RLS; server-side only |
| `APS_CLIENT_ID`, `APS_CLIENT_SECRET` | dev, prod | `aps_client.py` | APS app credentials |
| `APS_REFRESH_TOKEN` | **not in Infisical** | `SupabaseTokenStore` | Lives in `public.runtime_state` in the datum Supabase project, rotated by the runner on every run. Seeded by `datum-aps-consent`. |

All of the above live under the `/datum` folder of the Grace Engineering
project. Never stored anywhere: `PLEX_ALLOW_WRITES`, `PLEX_BASE_URL` (test-only
override), and no `INFISICAL_*` identity credentials reach the runner.

### Infisical-side setup (Shane, in the UI)

1. In the existing **Grace Engineering** project, create folder `/datum` in both `dev` and `prod`.
2. Add the inventory above (7 keys) to `/datum` in both envs. `APS_REFRESH_TOKEN` is **not** created anywhere in Infisical.
3. Rename machine identity `plex-mcp-workstation-shane` → `grace-workstation-shane`; keep its existing viewer access and make sure it can read `/datum` in `dev` (project-level viewer already covers it). Reuse its existing client id/secret: copy `~/.config/plex-mcp/infisical-identity.json` to `~/.config/datum/infisical-identity.json` and add `"projectId": "8e04285d-bdf4-4836-b1d6-f2d026e1574a"` (or point `DATUM_INFISICAL_IDENTITY_FILE` at the plex-mcp file). No new identity.
4. GitHub secret sync `datum-secret-sync`: source `prod` `/datum` → repository `grace-xwerks/datum`, auto-sync on, deletion protection on. Confirm the 7 keys appear under the repo's Actions secrets.
5. Nothing else: no CI identity, no `INFISICAL_*` repo secrets.

An agent produces this checklist with the exact key list; it never sees values.

## Components

### `bootstrap.py` — three-source loader

Precedence, all via `os.environ.setdefault`: **shell env > Infisical > `.env.local`**.

- `load_env_local()` unchanged.
- New `load_infisical()`:
  - Runs only when `DATUM_DISABLE_INFISICAL` is unset **and** either
    `~/.config/datum/infisical-identity.json` exists or both
    `INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/SECRET` are in the environment.
    Otherwise it returns 0 silently (the no-config no-op, same as a missing
    `.env.local`).
  - Project id and environment come from `DATUM_INFISICAL_PROJECT_ID` and
    `DATUM_INFISICAL_ENV` (default `dev`); the project id is also read from the
    identity file (`"projectId"`) so local dev needs no extra env var.
    **Amendment (B2):** the export is scoped to a folder, `DATUM_INFISICAL_PATH`
    (default `/datum`), passed as `--path=` to `infisical export`, because
    Datum's keys live in a folder of the shared Grace Engineering project.
  - Mints a short-lived token with `infisical login --method=universal-auth
    --plain --silent`, then `infisical export --format=json --silent
    --projectId=… --env=… --token=…`. Both subprocesses run with
    `stdin=DEVNULL`, `capture_output=True`, `encoding="utf-8",
    errors="replace"`, timeout 60s. Missing CLI, timeout, non-zero exit or
    unparseable output → one `log.warning`, return 0. Never raises on import.
  - Export shape may be a list of `{key, value}` or a dict; both handled.
- New `describe_sources()` returns `[(name, source, prefix8, length)]` for the
  credential names in a fixed list (`PLEX_API_KEY`, `PLEX_API_SECRET`,
  `SUPABASE_SERVICE_ROLE_KEY`, `APS_CLIENT_SECRET`, `APS_REFRESH_TOKEN`), where
  source ∈ {`shell`, `infisical`, `.env.local`, `unset`}. `sync.py` logs this at
  INFO on startup; `plex_diagnostics.tenant_whoami` includes it. Values are
  never logged beyond the first 8 characters. This is the lesson from BRIEFING
  History §4 that was never implemented.
- Auto-load order on import: `load_infisical()` then `load_env_local()`.
  `tests/conftest.py` sets `DATUM_DISABLE_INFISICAL=1` so no subprocess runs
  under pytest.

### `aps_client.py` — pluggable `TokenStore`

> **Amendment (Agent B2).** The production backend is `SupabaseTokenStore`,
> not `InfisicalTokenStore`. `InfisicalTokenStore` (merged in PR #25) stays in
> the module, selectable with `APS_TOKEN_STORE=infisical`, but nothing uses it
> by default and the workflow never sets it.
>
> `SupabaseTokenStore(key="aps_refresh_token")`:
> - Backed by `public.runtime_state (key TEXT PRIMARY KEY, value TEXT NOT NULL,
>   previous_value TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
>   updated_by TEXT)` in the datum Supabase project, migration
>   `db/migrations/0008_runtime_state.sql`. RLS enabled with **no policies**, so
>   only the service-role key can read or write it; a comment on the table says
>   why. Applied by the overlord via the Supabase MCP after the PR merges.
> - Uses the existing `SupabaseClient` (`select` / `upsert`), so it needs only
>   `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`, which the runner already has.
> - `_load()` reads the row; missing or empty → `APSAuthError("… seed it with
>   datum-aps-consent")`.
> - `update(data)` sets in-memory fields, then upserts `value=<new>`,
>   `previous_value=<old>`, `updated_by="datum-sync"` **before returning**; any
>   failure → `APSAuthError("refresh token write-back failed: …")` and the
>   in-memory access token is invalidated (same contract as the Infisical store).
> - `clear()` is a no-op with a warning.
> - Selection: `APS_TOKEN_STORE=file|supabase|infisical`, default `file`.
>
> `datum-aps-consent` gains `--store supabase|infisical` (default `supabase`)
> and seeds the Supabase row through the same upsert path; the Infisical path is
> kept for completeness.

The pre-amendment design follows. Today `TokenStore` is a JSON file. It becomes:

- `BaseTokenStore` with the current public surface (`access_token`,
  `refresh_token`, `expires_at`, `is_valid`, `update(data)`, `clear()`).
- `FileTokenStore(path)` — the existing behaviour, renamed. Default for local
  dev. `TokenStore = FileTokenStore` alias kept so existing imports and tests
  keep working.
- `InfisicalTokenStore(project_id, env="prod", key="APS_REFRESH_TOKEN")`:
  - `_load()` reads the refresh token from Infisical via the CLI using the
    `INFISICAL_UNIVERSAL_AUTH_*` identity in the environment. No access token
    is persisted; every run starts with a refresh.
  - `update(data)` sets the in-memory fields, then **writes the new refresh
    token back to Infisical before returning** (`infisical secrets set
    APS_REFRESH_TOKEN=<value> --projectId=… --env=prod --token=…`, value passed
    via argv is avoided: use `--file`/stdin form or the `secrets set` syntax
    that reads from stdin, so the token never appears in a process list). A
    failed write-back raises `APSAuthError("refresh token write-back failed …")`.
    Because `update()` is called from `refresh_access_token()` before any API
    call uses the new access token, a crash after a successful write-back
    leaves Infisical holding the newest, still-valid token.
  - `clear()` is a no-op with a warning (never delete the production token
    from code).
- Selection: `APSClient(token_store=...)` accepts an instance; when `None`,
  `APS_TOKEN_STORE=file|infisical` (default `file`) picks one. `token_path`
  stays for backward compatibility and only applies to the file store.
- `refresh_access_token()` is unchanged in contract. A 400/401 from Autodesk on
  refresh raises `APSAuthError` with the text "refresh token rejected — re-seed
  with datum-aps-consent" so the workflow log says what to do.

### `scripts/aps_consent.py` → console script `datum-aps-consent`

Interactive, run by Shane once and after any token death:

1. Prints `APSClient().get_authorize_url()`; Shane completes consent in a
   browser and pastes the `code` from the callback URL.
2. `exchange_code(code)` with an in-memory `FileTokenStore(path=None)`.
3. Writes the resulting refresh token to Infisical `prod/APS_REFRESH_TOKEN`
   through the same CLI path `InfisicalTokenStore` uses, authenticating with
   whatever Infisical identity is in the environment (Shane's own `infisical
   login` session is acceptable here because it is interactive and one-shot).
4. Prints "seeded" and the token's first 8 characters. Never prints the token.

### `sync.py` — no silent fallback

Today `main()` falls back to `sync_from_local()` on **any** APS exception, so a
dead refresh token on a runner would read "Local sync failed" (exit 2) and
point at the wrong cause.

- Without `--local`: APS is the only source. `APSConfigError`/`APSAuthError`
  log one `CRITICAL` line naming the cause (config vs auth vs write-back) and
  return exit 2. Other exceptions also exit 2 with the exception text. No
  fallback.
- With `--local`: the ADC loader runs exactly as today, including the 25-hour
  stale-file guard.
- Startup logs `bootstrap.describe_sources()` at INFO.
- Exit codes stay 0 / 1 (some libraries failed) / 2 (nothing processed or
  fatal).

### `.github/workflows/nightly-sync.yml`

```yaml
on:
  schedule: [{cron: "17 5 * * *"}]
  workflow_dispatch:
    inputs: {dry_run: {type: boolean, default: false}}
concurrency: {group: nightly-sync, cancel-in-progress: false}
permissions: {contents: read}
jobs.sync: ubuntu-latest, timeout-minutes 30
  - actions/checkout@v4 (persist-credentials: false)
  - actions/setup-python@v5 (3.12, pip cache on requirements*.txt)
  - pip install -e .            # no Infisical CLI on the runner (amended)
  - Credentials present  # fail fast; names only, never values
      required: PLEX_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
                APS_CLIENT_ID, APS_CLIENT_SECRET
      optional: PLEX_API_SECRET, PLEX_TENANT_ID (code default is the Grace tenant)
  - Fusion → Supabase sync
      env: the above + APS_TOKEN_STORE=supabase,
           DATUM_DISABLE_INFISICAL=1 (bootstrap must not try to export; secrets
           are already in env), PYTHONUNBUFFERED=1
      run: datum-sync --log-file "$RUNNER_TEMP/sync.log" [--dry-run]
  - Tool-qty sync
      run: datum-sync-inventory --log-file "$RUNNER_TEMP/sync_inventory.log" [--dry-run]
  - Summary (always): status + last 25 lines of each log into $GITHUB_STEP_SUMMARY,
      with any user:password@ masked
  - Upload logs as artifact (always), retention 14 days
```

Secrets are scoped to the steps that use them. `PLEX_ALLOW_WRITES` never
appears and no `INFISICAL_*` name exists in the workflow. Header comment
documents the Infisical `/datum` key → GitHub secret mapping,
the failure modes, and the manual re-run commands, in the style of plex_mcp's
`sync-cache.yml`. The repo's `tests` workflow is unaffected.

## Data flow (one nightly run)

1. Scheduler fires 05:17 UTC. Repo secrets (synced from Infisical prod) are
   injected into the sync step.
2. `datum-sync` starts; `bootstrap` sees `DATUM_DISABLE_INFISICAL=1` and loads
   nothing; shell env is the only source. `describe_sources()` logs each
   credential as `shell`.
3. `APSClient` builds a `SupabaseTokenStore`, which reads the
   `aps_refresh_token` row from `public.runtime_state` with the service-role key.
4. `_ensure_token()` → `refresh_access_token()` → Autodesk returns a new
   access + refresh token → `update()` upserts the new refresh token into the
   row (old value kept in `previous_value`) → only then does the hub traversal
   begin.
5. Libraries download, validate, upsert into Supabase (unchanged).
   `libraries.ingested_at` and `source_modified_at` advance.
6. `datum-sync-inventory` runs (no-op until tools are linked).
7. Summary and artifact. Exit code decides red/green; red emails Shane.

## Error handling

| Failure | Behaviour |
|---|---|
| Repo secret missing | "Credentials present" step fails, names the secret, points at Infisical prod. Nothing else runs. |
| Supabase row missing / unreadable on runner | `SupabaseTokenStore._load()` raises `APSAuthError` naming the table and the `datum-aps-consent` remedy; sync exits 2. |
| Refresh token rejected by Autodesk (dead after 15 idle days, or consumed elsewhere) | `APSAuthError` "refresh token rejected — re-seed with datum-aps-consent"; exit 2. |
| Write-back to Supabase fails | `APSAuthError`; exit 2 **before** any API call uses the new token, so the row at worst still holds the already-consumed previous token and the log says exactly that. Human re-seeds with `datum-aps-consent`. |
| `validate_library` FAIL on a library | Unchanged: that library is skipped and counted as failed; exit 1; the failed names are in the summary. |
| Supabase write error | Unchanged: per-library failure, exit 1. |
| Scheduled run skipped by GitHub | Visible as a gap in `ingested_at`; Libraries page "Last sync" shows it. Off-the-hour cron reduces the chance. |

## Testing

- `tests/test_bootstrap.py`: precedence (shell > infisical > `.env.local`);
  Infisical no-op when disabled / no identity / CLI missing / timeout /
  non-zero / bad JSON; list and dict export shapes; `describe_sources()`
  output never contains more than 8 characters of a value. `subprocess.run`
  is patched; no real CLI calls.
- New `tests/test_aps_token_store.py`: `FileTokenStore` keeps the old
  behaviour (migrate existing `TokenStore` tests); `InfisicalTokenStore` loads
  the refresh token, writes back on `update()`, raises on write-back failure,
  never writes the token to argv, `clear()` is a no-op; selection via
  `APS_TOKEN_STORE`; `APSClient(token_store=…)` injection. **B2 adds**
  `SupabaseTokenStore`: load from row, missing row → `APSAuthError` naming the
  remedy, write-back on `update()` with `previous_value` carried, write-back
  failure raises before return and invalidates the access token, `clear()`
  no-op, `APS_TOKEN_STORE=supabase` selection, `SupabaseClient` fully patched.
- `tests/test_sync.py`: without `--local`, `APSAuthError` → exit 2 and no
  call to `sync_from_local`; with `--local`, APS is never constructed; startup
  logs the sources line.
- `tests/test_aps_consent.py`: happy path with patched `exchange_code` and
  patched CLI; token never in stdout.
- Workflow: a `workflow_dispatch` run with `dry_run=true` after Shane's
  Infisical setup is the acceptance test. It is the one step that needs real
  credentials.
- Existing 440 tests stay green throughout.

## Docs

- `CLAUDE.md`: hard rule "credentials live in `.env.local`" → "credentials
  come from Infisical (project `datum`); `.env.local` is the local fallback";
  add the machine-identity file location; quick command for
  `datum-aps-consent`.
- `docs/BRIEFING.md`: Credentials section, "sync.py + nightly deploy" section
  (Actions replaces GRACE144), new Secrets subsection with the inventory table,
  2026-10-14 status snapshot row update once the first scheduled run is green.
- `README.md`: deployment section.
- New `docs/SECRETS.md`: the Infisical setup checklist (identities, sync,
  consent seeding, rotation runbook, what to do when a run goes red).

## Rollout order

1. Code lands behind defaults that change nothing: `APS_TOKEN_STORE` defaults
   to `file`; `load_infisical()` is a no-op without an identity. The 440 tests
   plus new ones stay green. (Agents A–D.)
2. Docs land. (Agent E.)
3. Overlord applies `db/migrations/0008_runtime_state.sql` to the datum
   Supabase project via the Supabase MCP. Shane: `/datum` folder + 7 keys in
   Grace Engineering (both envs), identity rename, `datum-secret-sync`; verify
   the repo secrets appeared; run `datum-aps-consent` once to seed the row.
4. `workflow_dispatch` with `dry_run=true` → green. Then a real dispatch →
   `ingested_at` advances.
5. First scheduled run green → Shane disables the two GRACE144 Task Scheduler
   jobs, then deletes them and the host's `.env.local` + `.aps_tokens.json`.
6. Close #16 and #18; update Notion Current State and Decision Log.

## Work breakdown (overlord dispatch)

| Agent | Scope | Files | Model | Depends on |
|---|---|---|---|---|
| A | `bootstrap.load_infisical`, `describe_sources`, tests, conftest guard | `bootstrap.py`, `tests/test_bootstrap.py`, `tests/conftest.py` | Opus | — |
| B | `TokenStore` backends, `APSClient` injection, `datum-aps-consent`, tests | `aps_client.py`, `scripts/aps_consent.py`, `pyproject.toml`, `tests/test_aps_client.py`, `tests/test_aps_token_store.py`, `tests/test_aps_consent.py` | Opus | — (merged PR #25) |
| B2 | `SupabaseTokenStore` + `APS_TOKEN_STORE=supabase`, `datum-aps-consent --store`, migration `0008_runtime_state.sql`, `DATUM_INFISICAL_PATH` in `bootstrap.load_infisical`, tests | `aps_client.py`, `scripts/aps_consent.py`, `bootstrap.py`, `db/migrations/0008_runtime_state.sql`, `tests/test_aps_token_store.py`, `tests/test_aps_consent.py`, `tests/test_bootstrap.py` | Opus | B |
| C | `sync.py` fallback guard + sources log, `run_dev.py` Infisical precedence, `tenant_whoami` credential sources, tests | `sync.py`, `run_dev.py`, `plex_diagnostics.py`, tests | Sonnet | A, B (merged PR #26) |
| D | `nightly-sync.yml` | `.github/workflows/nightly-sync.yml` | Sonnet | B2's env contract (`APS_TOKEN_STORE=supabase`; no Infisical on the runner) |
| E | Docs | `CLAUDE.md`, `docs/BRIEFING.md`, `README.md`, `docs/SECRETS.md` | Sonnet | A–D merged |

A and B run in parallel in separate worktrees. C and D start once A and B's
interfaces are on master. E last. Every PR: `claude/<slug>` branch, `pytest`
green, squash-merge keep branch, no `close`/`fix`/`resolve` next to `#16` or
`#18` (they close at rollout step 6, by hand).
