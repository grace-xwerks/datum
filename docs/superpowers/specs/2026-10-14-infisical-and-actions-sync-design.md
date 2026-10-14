# Design: Infisical secrets + nightly sync on GitHub Actions

**Date:** 2026-10-14
**Issues:** [#16](https://github.com/grace-xwerks/datum/issues/16) (Infisical),
[#18](https://github.com/grace-xwerks/datum/issues/18) (nightly sync → Actions)
**Status:** approved in conversation 2026-10-14 (Shane); implementation plan at
`docs/superpowers/plans/2026-10-14-infisical-and-actions-sync.md`.

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
| Infisical placement | New project `datum`, envs `dev` + `prod` | Own blast radius and identities; mirrors plex_mcp's layout. Datum's Plex Consumer Key is a different app from plex_mcp's `Manage2Plex` key and the two must not share secrets. |
| Production APS refresh token | Infisical write-back | APS refresh tokens are single-use and rotate on every refresh (15-day idle expiry). One consumer only. Infisical keeps version history. Supabase-row and APS Service Accounts were considered; SSA support for Fusion Team hubs is unverified. |
| Secrets into the runner | Infisical → GitHub secret sync (plex_mcp pattern) | The runner reads plain repo secrets; no Infisical code path in CI except the token write-back. |
| Local dev auth to Infisical | Machine identity (Universal Auth) via identity file | A human `infisical login` expires every 10 days and draws an interactive prompt on stdout (grace-cam, 2026-09-12). |
| Cron | `17 5 * * *` (00:17 America/Chicago in CDT) | plex_mcp's first `:00` schedule was dropped by GitHub under load. |
| Tool-qty sync | Second step in the same workflow | It is a no-op until tools are linked, but it is the second GRACE144 job and should die with the first. |

## Architecture

```
Infisical project `datum`
  ├── env dev   ── machine identity datum-workstation-shane (read) ──▶ bootstrap.py (local dev)
  └── env prod  ── GitHub secret sync datum-secret-sync ──▶ repo secrets ──▶ nightly-sync.yml
                ── machine identity datum-actions (read all; write APS_REFRESH_TOKEN)
                        ▲                               │
                        └── write-back after refresh ───┘  InfisicalTokenStore (runner only)

nightly-sync.yml (schedule 17 5 * * * + workflow_dispatch)
  checkout → python 3.12 → infisical CLI → pip install -e . → credentials present
  → datum-sync --log-file → datum-sync-inventory --log-file → summary + artifact
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
| `APS_REFRESH_TOKEN` | **prod only** | `InfisicalTokenStore` | Rotated by the runner on every run. Never in dev. |
| `INFISICAL_UNIVERSAL_AUTH_CLIENT_ID`, `INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET` | prod (as repo secrets) | workflow → `InfisicalTokenStore` | The `datum-actions` identity. Pushed to GitHub by the sync like any other secret. |

Never stored: `PLEX_ALLOW_WRITES`, `PLEX_BASE_URL` (test-only override).

### Infisical-side setup (Shane, in the UI)

1. Project `datum`; environments `dev`, `prod`.
2. Import the inventory above into both envs (prod gets `APS_REFRESH_TOKEN` as an empty placeholder until consent seeding).
3. Machine identity `datum-workstation-shane`: Universal Auth, role viewer on `dev`. Client id/secret saved to `~/.config/datum/infisical-identity.json` as `{"clientId": ..., "clientSecret": ...}`.
4. Machine identity `datum-actions`: Universal Auth, read on `prod` `/`, write permitted on `prod` `/APS_REFRESH_TOKEN` only. Its client id/secret stored in `prod` under the two `INFISICAL_UNIVERSAL_AUTH_*` keys so the sync carries them to GitHub.
5. GitHub secret sync `datum-secret-sync`: `prod` `/` → repository `grace-xwerks/datum`, auto-sync on, deletion protection on.

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

Today `TokenStore` is a JSON file. It becomes:

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
  - install Infisical CLI (official apt repo or release binary; pinned version)
  - pip install -e .
  - Credentials present  # fail fast; names only, never values
      required: PLEX_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
                APS_CLIENT_ID, APS_CLIENT_SECRET,
                INFISICAL_UNIVERSAL_AUTH_CLIENT_ID, INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET
  - Fusion → Supabase sync
      env: the above + APS_TOKEN_STORE=infisical, DATUM_INFISICAL_PROJECT_ID,
           DATUM_INFISICAL_ENV=prod, DATUM_DISABLE_INFISICAL=1 (bootstrap must
           not also try to export; secrets are already in env), PYTHONUNBUFFERED=1
      run: datum-sync --log-file "$RUNNER_TEMP/sync.log" [--dry-run]
  - Tool-qty sync
      run: datum-sync-inventory --log-file "$RUNNER_TEMP/sync_inventory.log" [--dry-run]
  - Summary (always): status + last 25 lines of each log into $GITHUB_STEP_SUMMARY,
      with any user:password@ masked
  - Upload logs as artifact (always), retention 14 days
```

Secrets are scoped to the steps that use them. `PLEX_ALLOW_WRITES` never
appears. Header comment documents the Infisical key → GitHub secret mapping,
the failure modes, and the manual re-run commands, in the style of plex_mcp's
`sync-cache.yml`. The repo's `tests` workflow is unaffected.

## Data flow (one nightly run)

1. Scheduler fires 05:17 UTC. Repo secrets (synced from Infisical prod) are
   injected into the sync step.
2. `datum-sync` starts; `bootstrap` sees `DATUM_DISABLE_INFISICAL=1` and loads
   nothing; shell env is the only source. `describe_sources()` logs each
   credential as `shell`.
3. `APSClient` builds an `InfisicalTokenStore`, which reads
   `prod/APS_REFRESH_TOKEN` via the `datum-actions` identity.
4. `_ensure_token()` → `refresh_access_token()` → Autodesk returns a new
   access + refresh token → `update()` writes the new refresh token to
   Infisical → only then does the hub traversal begin.
5. Libraries download, validate, upsert into Supabase (unchanged).
   `libraries.ingested_at` and `source_modified_at` advance.
6. `datum-sync-inventory` runs (no-op until tools are linked).
7. Summary and artifact. Exit code decides red/green; red emails Shane.

## Error handling

| Failure | Behaviour |
|---|---|
| Repo secret missing | "Credentials present" step fails, names the secret, points at Infisical prod. Nothing else runs. |
| Infisical identity rejected / CLI absent on runner | `InfisicalTokenStore._load()` raises `APSAuthError`; sync exits 2 with the Infisical stderr (ANSI-stripped) in the log. |
| Refresh token rejected by Autodesk (dead after 15 idle days, or consumed elsewhere) | `APSAuthError` "refresh token rejected — re-seed with datum-aps-consent"; exit 2. |
| Write-back to Infisical fails | `APSAuthError`; exit 2 **before** any API call uses the new token, so the old token in Infisical is at worst already-consumed and the log says exactly that. Human re-seeds. |
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
  `APS_TOKEN_STORE`; `APSClient(token_store=…)` injection.
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
3. Shane: Infisical project, identities, secret sync; run `datum-aps-consent`
   once. Verify repo secrets appeared.
4. `workflow_dispatch` with `dry_run=true` → green. Then a real dispatch →
   `ingested_at` advances.
5. First scheduled run green → Shane disables the two GRACE144 Task Scheduler
   jobs, then deletes them and the host's `.env.local` + `.aps_tokens.json`.
6. Close #16 and #18; update Notion Current State and Decision Log.

## Work breakdown (overlord dispatch)

| Agent | Scope | Files | Model | Depends on |
|---|---|---|---|---|
| A | `bootstrap.load_infisical`, `describe_sources`, tests, conftest guard | `bootstrap.py`, `tests/test_bootstrap.py`, `tests/conftest.py` | Opus | — |
| B | `TokenStore` backends, `APSClient` injection, `datum-aps-consent`, tests | `aps_client.py`, `scripts/aps_consent.py`, `pyproject.toml`, `tests/test_aps_client.py`, `tests/test_aps_token_store.py`, `tests/test_aps_consent.py` | Opus | — |
| C | `sync.py` fallback guard + sources log, tests | `sync.py`, `tests/test_sync.py` | Sonnet | A (for `describe_sources`), B (exception text) |
| D | `nightly-sync.yml` | `.github/workflows/nightly-sync.yml` | Sonnet | B (env var names) |
| E | Docs | `CLAUDE.md`, `docs/BRIEFING.md`, `README.md`, `docs/SECRETS.md` | Sonnet | A–D merged |

A and B run in parallel in separate worktrees. C and D start once A and B's
interfaces are on master. E last. Every PR: `claude/<slug>` branch, `pytest`
green, squash-merge keep branch, no `close`/`fix`/`resolve` next to `#16` or
`#18` (they close at rollout step 6, by hand).
