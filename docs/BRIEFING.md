# Grace Engineering — Datum: Claude Code Briefing

This is the primary context document for AI-assisted development sessions.
Read this first, then read `plex_api.py`, `tool_library_loader.py`, and
`docs/validate_library_spec.md` (the pre-sync validation gate design, #25).

> **File layout note.** As of 2026-04-08, all long-form docs live under
> `docs/`. This file is `docs/BRIEFING.md`, not `./BRIEFING.md`. Siblings:
> `docs/Plex_API_Reference.md`, `docs/Fusion360_Tool_Library_Reference.md`,
> `docs/validate_library_spec.md`, `docs/Postman_Collections.md`.
> `README.md`, `CLAUDE.md`, and `TODO.md` are still at the repo root. See
> PR #24 for the move.

> **Read the "History of incorrect hypotheses" section at the bottom of this
> file before changing anything credential- or tenant-related.** It documents
> four wrong turns this project took that all came down to one root cause
> (see History §1). Do not repeat them.

---

## What this project is

Nightly automation that syncs Autodesk Fusion 360 tool library data into
Rockwell Automation Plex Smart Manufacturing (ERP). Fusion 360 JSON files
on a local network share are the absolute source of truth. The script reads
them and pushes tooling data to Plex via REST API every night at midnight.

**Project name: Datum.** Named for the machining reference point — the fixed datum everything is measured from. Fusion 360 is the datum; Plex and Supabase stay in sync with it.

---

## Repo: https://github.com/grace-xwerks/datum

Grace Engineering's canonical repository. Originated as a fork of
`just-shane/plex-api` (Shane's personal account); **left that fork network on
2026-10-14** and is now a standalone repo. Nothing merges back upstream.

Renamed from `plex-api` → `datum` on 2026-04-09.

Migrated from `grace-shane/Datum` to the `grace-xwerks` org in July 2026 (the
`grace-shane` account is retired; the old URL no longer resolves). Git history
came across intact, but **issue numbers changed** and closed issues were not
migrated. Legacy → current: #3→#1, #4→#2, #5→#3, #6→#4, #7→#5, #8→#6,
#85→#7, #98–#104→#8–#14. Unlinked `#N` / `PR #N` references in this file
(including the session log) use the **legacy** numbering; the PR numbers match
the squash-commit subjects in `git log`. Old clones may still carry an
`upstream` remote pointing at `just-shane/plex-api`; remove it
(`git remote remove upstream`) — it was what made `gh` pick the wrong default
repo before the fork detach.

---

## Notion pages

Live project state and decision log live in Notion, outside the repo.
The repo has the "what" (code, specs, CI); Notion has the "where are we
right now" and the running conversation about trade-offs.

| Page | URL | Purpose |
|---|---|---|
| Grace Engineering | https://www.notion.so/33c3160a3abf813f9db6c5f68bef8bf2 | Parent — all Grace work lives under this page |
| Datum | https://www.notion.so/Grace-Engineering-Fusion2Plex-33c3160a3abf81f1aac0e58101952be5 | **Read this at the start of every session.** Current State block = exactly where to pick up. |

### Session protocol

- **Start of session:** read the Datum Notion page. The Current
  State block at the top tells you phase, next action, and test count
  without having to diff the repo.
- **End of session:** update the Current State block (phase, next
  action, test count) and append one line to the Decision Log describing
  what changed and why.

---

## Current situation (April 2026)

- **App**: `Datum` in the Plex Developer Portal
- **Environment**: `https://connect.plex.com` — **PRODUCTION**, real Grace data
- **Tenant**: `58f781ba-1691-4f32-b1db-381cdb21300c` (`Grace`) — verified
  empirically by `GET /mdm/v1/tenants`
- **Credentials**: Consumer Key + (optional) Secret, loaded by `bootstrap.py`
  at startup from three sources, **shell env > Infisical > `.env.local`**.
  Infisical (Grace Engineering project, folder `/datum`, envs `dev`/`prod`) is
  the source of truth and is read locally through the machine-identity file
  `~/.config/datum/infisical-identity.json`; `.env.local` (gitignored) is the
  local fallback; the nightly GitHub Actions runner uses plain repo secrets
  synced from Infisical `prod`. Rollout status is in the 2026-10-14 snapshot
  below (the Infisical folder, GitHub sync and token seed are Shane's steps and
  were still pending at that snapshot). Full reference: [`docs/SECRETS.md`](./SECRETS.md).
- **Key expires every 31 days** — see [#14](https://github.com/grace-xwerks/datum/issues/14) for rotation cadence
- **Reads work** — `mdm/v1/tenants`, `mdm/v1/parts`, `mdm/v1/suppliers`,
  `purchasing/v1/purchase-orders` all return 200
- **Writes are blocked** at the proxy by default (PR #17 production guard).
  To enable: set `PLEX_ALLOW_WRITES=1` in the environment and restart
- **There is NO test environment for this app.** The Datum Consumer
  Key only authenticates against `connect.plex.com`, not `test.connect.plex.com`.
  Every action you take is against real production data.

### Status snapshot — 2026-10-13

Verified this date after a ~5-month gap (last functional commit: 2026-05-01,
legacy PR #97; `master` has only gained `.gitattributes` since).

| Area | State |
|---|---|
| **Tests** | `pytest`: **440 passed** locally (Python 3.14.3, Flask 3.1.3, pytest 9.1.1). |
| **Web UI** | `npm ci && npm run build` succeeds. `npm run lint` reports **19 pre-existing errors** (10 `react-hooks/static-components`, 6 `no-empty`, 3 `react-refresh/only-export-components`); CI does not run lint. |
| **Plex auth** | `plex_whoami` live check OK, tenant `58f781ba-…` matches, `allow_writes=false`. Key fingerprint differs from the stale shell-var key noted in the session log. Issue [#14](https://github.com/grace-xwerks/datum/issues/14) (rotation "due 2026-05-08") is **still open** even though auth works — rotation bookkeeping was not closed out. |
| **Plex → Supabase mirror** (the `plex` schema behind the Plex MCP) | Partly fresh: `parts`, `supply_items`, `approved_suppliers`, `item_suppliers` and the production-history endpoints refreshed 2026-10-13; `buildings`, `containers`, `customers`, `locations`, `lots`, `operations`, `suppliers`, `workcenters` last ran **2026-08-18**; `/mdm/v1/part-operations` has one failed probe run (2026-06-17). |
| **GitHub** | No open PRs (before this one); 14 open issues (#1–#14). **No branch protection** on `master`, and repo auto-merge is **off**. **GitHub Actions has never produced a run on this repo** (zero workflow runs; no `github-actions` check suite on new commits) although the `tests` workflow is registered and Actions is enabled at repo level — an org-level Actions policy is suspected (needs an org admin to inspect). Until that works, the local `pytest` run is the only test gate, and requiring the `pytest` check would deadlock every merge. |
| **Unmerged work on stale branches** | `claude/awesome-ishizaka`: `scripts/ingest_holders.py` and Scripts-page holder matching — the `holder_catalog` table has **no migration** in `db/migrations/` and no code on `master`. Other `claude/*` and `wip/*` branches are merged or superseded. |
| **Not verified** | The live Supabase `datum` project (`crimblieyypiyarssoel`) — the Supabase MCP in use was bound to a different org. GRACE144 Task Scheduler runs. |

### Status snapshot — 2026-10-14

| Area | State |
|---|---|
| **Supabase `datum` project** | Verified via the Supabase MCP (re-bound to the Grace org). Healthy, Postgres 17, 15 migrations applied (latest three dated 2026-10-13: `item_suppliers`, `approved_suppliers`, `supply_items_widen`). `public`: `libraries` 7, `tools` 155, `cutting_presets` 631, `reference_catalog` 85,306, `holder_catalog` 405, `plex_supply_items` 0. `plex` mirror: all 12 master tables refreshed 2026-10-14 13:25 UTC; production-history tables run daily at 06:00 UTC via pg_cron → edge function, last OK 2026-10-14. The "last ran 2026-08-18" note above is superseded. |
| **Nightly Fusion → Supabase sync** | **Never passed validation; now live on Actions.** The earlier reading here ("dead since 2026-04-10", GRACE144 stopped writing) had the wrong cause. The `libraries.ingested_at` stamp of 2026-04-10 15:26 UTC came from the one manual `/api/aps/sync` run that predates the validation gate. The gate (shipped April 8, legacy PR #28) treats a missing or duplicate `product-id` as FAIL, every Fusion library has such tools, so the nightly CLI never passed validation, on GRACE144 or anywhere. Proven by Actions run 38623375506 (dry run: "0 succeeded, 3 skipped, 10 failed", all on `REQUIRED_FIELD product-id` / `DUPLICATE_PRODUCT_ID`, one `description`). Fixed by PR #32 (staging-stage validation) and PR #34 (see the row below). **First live run on Actions, run 38625485799, succeeded:** 10 libraries, 224 tools, 753 presets; supply-item staging 201 staged / 24 skipped; every `libraries.ingested_at` advanced to 2026-10-14 19:39–19:40 UTC; `source_modified_at` is populated for the first time (878 MAZAK INTEGREX last modified in Fusion 2026-09-30, 848 on 2026-08-21, 879 on 2026-07-09). No tools are linked to Plex yet and `qty_synced_at` is NULL throughout (linking waits on the Plex writeback, [#1](https://github.com/grace-xwerks/datum/issues/1)). Tracked in [#18](https://github.com/grace-xwerks/datum/issues/18). |
| **GitHub Actions** | **Root cause found.** The repo was created on 2026-05-01 as a *fork* of `just-shane/plex-api`, and GitHub disables workflows on forks until a repo admin clicks "enable" on the Actions tab. Not an org policy: repo-level Actions was already enabled with all actions allowed, the `tests` workflow was `active`, and the repo is public (unlimited minutes). Shane enabled the workflows on 2026-10-14; PR #20 (new-repo numbering) was the first run (pull_request 20s, push 15s, both green). **Then set the same day:** branch protection on `master` requiring the `pytest` context (strict=false, no reviews, enforce_admins=false, no force-push/deletion) and repo `allow_auto_merge=true`. Shane then left the fork network the same day; the `upstream` remote was removed from the local clone and the `gh repo set-default` warnings dropped from CLAUDE.md / BRIEFING. |
| **GCP migration (#7)** | **Abandoned.** Project `datum-prod-493615` has billing disabled, its VMs are gone, and neither of Shane's Google accounts has IAM on it; deletion requested of a Grace admin 2026-10-14. `scripts/gcp/` and `docs/NEXT_SESSION.md` removed. See the banner in `docs/GCP_MIGRATION.md`. |
| **Issues opened** | [#16](https://github.com/grace-xwerks/datum/issues/16) Infisical secrets, [#17](https://github.com/grace-xwerks/datum/issues/17) port holder-catalog work from `claude/awesome-ishizaka`, [#18](https://github.com/grace-xwerks/datum/issues/18) nightly sync → GitHub Actions. Follow-up [#28](https://github.com/grace-xwerks/datum/issues/28): keep the Infisical access token off argv. |
| **Infisical + Actions sync (evening)** | **Rollout complete through the first live run.** PRs #23 (design spec), #24 (`bootstrap.load_infisical` + `describe_sources`), #25 (pluggable APS token store), #26 (`sync.py` no silent local fallback; `run_dev.py` skips Infisical-sourced keys; `credential_sources` in `tenant_whoami`), #27 (spec amended after the Infisical billing review), #29 (`.github/workflows/nightly-sync.yml`), #30 (`SupabaseTokenStore`, `datum-aps-consent --store`, `DATUM_INFISICAL_PATH`, migration `0008_runtime_state.sql`). **583 tests** pass (was 440; 621 after PRs #32 and #34). Migration 0008 is applied to the datum Supabase project (`public.runtime_state`, RLS on, no policies). **Billing decision:** Infisical is on Legacy Pro at $18 per identity; identities went 6 → 3 the same day (Shane, `github-actions-capitoltrace`, `grace-workstation-shane`, the last being `plex-mcp-workstation-shane` renamed, rename pending on Shane's side). Datum adds **zero** identities and no new Infisical project: its 7 keys go in folder `/datum` of Grace Engineering (`8e04285d-bdf4-4836-b1d6-f2d026e1574a`), and the rotating APS refresh token lives in a Supabase row instead of Infisical. **Done since (Shane, evening):** `/datum` and its 7 keys exist in `dev` and `prod`, the identity is renamed `grace-workstation-shane`, GitHub secret sync `datum-secret-sync` is live, and the APS refresh token row in `public.runtime_state` was seeded 2026-10-14 19:23 UTC (it has rotated on every run since). **Still to do:** the first *scheduled* run must be green; after that the GRACE144 jobs ("Datum Nightly Sync" 00:00, "Datum Nightly Inventory Sync" 00:30) are disabled, then deleted with that host's `.env.local` and `.aps_tokens.json`. [#16](https://github.com/grace-xwerks/datum/issues/16) and [#18](https://github.com/grace-xwerks/datum/issues/18) are closed by hand once that is confirmed. See [`docs/SECRETS.md`](./SECRETS.md). |
| **Validation stage (PR #32, PR #34)** | PR #32: `validate_library` gained `ValidationStage` (`PLEX` is the default and the old behaviour; `STAGING` makes `product-id` / `description` presence and `product-id` duplicates WARN). `sync.py` validates at STAGING. Rationale: Supabase dedups on `fusion_guid`, already held 57 of 155 tools with an empty `product_id`, and `populate_supply_items` skips tools without one before anything reaches Plex. Spec `docs/validate_library_spec.md` has a "Stages" section. PR #34: `datum-sync-inventory` exits 0 (warning) when no tool is linked, since linking only happens when the Plex writeback ([#1](https://github.com/grace-xwerks/datum/issues/1)) ships; the old exit 2 would have reddened every nightly. Follow-up [#33](https://github.com/grace-xwerks/datum/issues/33): a duplicate `product_id` can still reach `plex_supply_items`, which matters when #1 ships. |
| **Credentials as configured (2026-10-14)** | Grace Engineering Infisical project, folder `/datum`, envs `dev` + `prod`, 7 keys. **Datum now uses the `Manage2Plex` Plex Consumer Key** (plex_mcp's app), copied from the project root, not the separate "Datum" Developer Portal app key the repo used before; one rotation now covers both repos ([#14](https://github.com/grace-xwerks/datum/issues/14)). The APS credentials are the **"Datum" APS app**; the root-level `APS_*` keys in Grace Engineering belong to the "Manage Lab" app and caused a `Request error` / `invalid_credentials` detour. |

---

## Auth — header model

```
X-Plex-Connect-Api-Key:    <key>      # required — identifies the app
X-Plex-Connect-Tenant-Id:  <uuid>     # required — selects the tenant
X-Plex-Connect-Api-Secret: <secret>   # OPTIONAL — Plex authenticates on
                                       # the key alone for this app
```

The Insomnia Generate Code output for a working request shows only the
key + tenant headers. The secret may be needed in some configurations
(future-proof, harmless to send), but is not currently required.

Credentials are loaded by `bootstrap.py` from shell env, Infisical, then
`.env.local` (in that order of precedence; see `docs/SECRETS.md`).
**Never hardcode credentials. Never commit credentials.**

### Tenants

| Name              | Tenant ID                              | Status                              |
|-------------------|----------------------------------------|-------------------------------------|
| **Grace Eng.**    | `58f781ba-1691-4f32-b1db-381cdb21300c` | **CURRENT** — verified live, prod   |
| Grace (stale)     | `a6af9c99-bce5-4938-a007-364dc5603d08` | Dead. Was in earlier docs. Wrong.   |
| G5                | `b406c8c4-cef0-4d62-862c-1758b702cd02` | Another company. Old test app only. |

Tenant IDs are not secrets — they are committed as defaults in
`plex_api.py` (`GRACE_TENANT_ID`) and `plex_diagnostics.py`
(`KNOWN_TENANTS`).

---

## Architecture

```
Fusion 360 tool libraries
  ├── APS cloud (primary, PR #43)  ── aps_client.py                     ─┐
  └── ADC network share (fallback) ── tool_library_loader.py             │
                                                                         ▼
  validate_library.py    pre-sync validation gate (PR #28, spec: docs/validate_library_spec.md)
         │
         ▼
  Supabase staging (PR #32, schema: libraries / tools / cutting_presets)
         ├── enrich.py                 vendor catalog + geometry-based enrichment (PR #48, wired PR #54)
         ├── React UI (PR #41)         tool browser / library browser / scripts / qty indicators — deployed to Cloudflare Workers (PR #70)
         ▼
  transform layer    build_supply_item_payload (in progress, #3 — sprint PRs #82 / #84)
         │
         ▼
  plex_api.py / PlexClient    Plex REST API
         ├── inventory/v1/inventory-definitions/supply-items   cutting tools (category="Tools & Inserts")
         ├── mdm/v1/suppliers                                  resolve vendor UUIDs
         └── production/v1/production-definitions/workcenters  machine setup docs (per-id write — blocked on Classic API, #6)
```

The nightly sync runs via the `sync.py` CLI (PR #44). It was scheduled on GRACE144 Task Scheduler (PR #47) — **but that CLI never passed validation** (the gate treated a missing or duplicate `product-id` as FAIL; see the 2026-10-14 status snapshot), so it never wrote. Its replacement is `.github/workflows/nightly-sync.yml` on GitHub Actions ([#18](https://github.com/grace-xwerks/datum/issues/18)), which validates at the STAGING stage (PR #32), also drops the silent local-ADC fallback, and had its first live run on 2026-10-14 (run 38625485799, success); secrets moved to Infisical ([#16](https://github.com/grace-xwerks/datum/issues/16), live). The GCP migration (#7) was **abandoned on 2026-10-14** — `docs/GCP_MIGRATION.md` is a historical record only. Supabase stays.

### Industry hierarchy (Plex data model)

1. Purchased consumables — cutting tools as bought parts (end mills, drills, etc.)
2. Tool assemblies — consumable + holder paired together
3. Routings / operations — assemblies mapped to machining ops
4. Jobs — ops executed on the shop floor
5. Manufactured parts — end product, with full tool traceability

---

## Plex API access matrix — Datum on production

Verified empirically against `connect.plex.com` with the Grace tenant.

### URL pattern convention

Plex uses two URL shapes for read endpoints:

- **Master data, flat**: `<namespace>/v1/<resource>`
  → `mdm/v1/parts`, `mdm/v1/suppliers`, `mdm/v1/operations`
- **Definitions, nested**: `<namespace>/v1/<namespace>-definitions/<resource>`
  → `production/v1/production-definitions/workcenters`
  → `inventory/v1/inventory-definitions/supply-items`

### Verified working endpoints

Record counts are as of **2026-04-09** unless noted. For the full schema
of every resource + cross-reference discussion, see
[`docs/Plex_API_Reference.md`](./Plex_API_Reference.md) §3.

| Path | Records | What it is |
|---|---:|---|
| `mdm/v1/tenants` | 1 | Grace tenant only. Auth canary. |
| `mdm/v1/parts` | 16,921 | +8 since 2026-04-07. **Finished products + raw materials only.** Tools are NOT here. |
| `mdm/v1/parts/{id}` | — | Per-id verified 2026-04-09. Same fields as list. |
| `mdm/v1/suppliers` | 1,575 | Supplier master. Has `parentSupplierId` self-FK. Mixed material + carrier types. |
| `mdm/v1/suppliers/{id}` | — | Per-id verified 2026-04-09. |
| `mdm/v1/customers` | 109 | 35-field schema. FKs to employees, contacts, suppliers. |
| `mdm/v1/customers/{id}` | — | Per-id verified 2026-04-09. |
| `mdm/v1/contacts` | 299 | |
| `mdm/v1/buildings` | 4 | Provides `buildingCode`/`buildingId` referenced by workcenters. |
| `mdm/v1/employees` | 641 | UUIDs here appear as `createdById`/`modifiedById` across every resource. |
| `mdm/v1/operations` | 122 | Process steps. Minimal schema (4 fields). No FK to tools/parts/routings — see Gotchas. |
| `mdm/v1/operations/{id}` | — | Per-id verified 2026-04-09. |
| `purchasing/v1/purchase-orders` | — | 44.2 MB unfiltered. `?updatedAfter` filter confirmed silent no-op 2026-04-09. |
| `production/v1/production-definitions/workcenters` | 143 | Includes 21 MILLs. **Codes 879/880 = Brother Speedio FTP IPs.** ⚠️ Primary key is `workcenterId`, not `id`. |
| `production/v1/production-definitions/workcenters/{id}` | — | Per-id verified 2026-04-09. |
| `inventory/v1/inventory-definitions/supply-items` | 2,516 | **WHERE TOOLS LIVE.** Filter `category="Tools & Inserts"` for the 1,109 cutting tools. **⚠️ No supplier FK, no cross-references of any kind — identity-only. See §3.5 of Plex_API_Reference.md.** |
| `inventory/v1/inventory-definitions/supply-items/{id}` | — | Per-id verified 2026-04-09. Same 7 fields, no hidden detail. |
| `inventory/v1/inventory-definitions/locations` | 1,270 | Inventory location master. Not referenced from supply-item. |
| `scheduling/v1/jobs` | 114,684 | **Deep-dived 2026-04-10.** 18 fields: `jobNumber, partId, partNumber, buildingId, quantity, dates, status`. **No tool/operation/workcenter FKs.** Does NOT unblock #5. |

### Where tooling data actually lives

Cutting tools and inserts are **`inventory/v1/inventory-definitions/supply-items`**
records with `category="Tools & Inserts"` and `group="Machining"`. There are
already 1,109 tools/inserts tracked in Plex Grace (verified 2026-04-09).
The schema is just 7 fields:

  - `category` (e.g. "Tools & Inserts")
  - `description` (free-text, human-readable)
  - `group` (e.g. "Machining", "Tool Room")
  - `id` (UUID — Plex primary key)
  - `inventoryUnit` (e.g. "Ea")
  - `supplyItemNumber` (**legacy: free-text, not vendor part numbers**)
  - `type` (e.g. "SUPPLY")

**⚠️ CRITICAL: supply-items have NO cross-references to any other resource (verified 2026-04-09).** This is identity-only — Plex stores no link from a tool to:

- its vendor (no `supplierId`)
- its physical location (no `locationId`)
- the part it helps produce (no `partId`)
- the machine it belongs to (no `workcenterId`)
- the operation it performs (no `operationId`)

**Implication for the Datum architecture:** vendor/supplier data for tools MUST live in Supabase as the source of truth. The Fusion JSON carries `vendor` and `product-id` → those get written to the `tools` table in Supabase → and `build_supply_item_payload()` (issue #3) constructs the Plex POST body using only the 7 identity fields listed above. Plex never learns who the vendor is, because Plex doesn't model that relationship for tools.

This kills the "use PO lines as a back-channel for the vendor link" hypothesis that was implicit earlier — `purchasing/v1/purchase-orders-lines` returned 404 on 2026-04-09.

The Fusion sync will write to `inventory/v1/inventory-definitions/supply-items`, not to `mdm/v1/parts` (which is for finished products).

**Sample existing `supplyItemNumber` values captured 2026-04-09** (confirming the legacy free-text nature of these records):

- `"Insert  HM90 AXCR 150508 IC28"` (description, not a part number)
- `"Screw Indexable Face Mill F75"`
- `"Tap #8-32 H3 Spiral Point"`

Fusion writes will use clean vendor part numbers like `"990910"`, so expect ~100% INSERTs with zero collisions on first sync.

### Workcenter ↔ machine mapping

The 21 MILL workcenter records map directly to physical machines via
`workcenterCode` (which equals the machine number / DNC IP last octet):

| Brother Speedio | FTP IP | Plex workcenterCode | Plex workcenterId |
|---|---|---|---|
| 879 | 192.168.25.79 | `879` | `0b6cf62b-2809-4d3d-ab24-369cd0171f62` |
| 880 | 192.168.25.80 | `880` | `8e262d5a-3ce8-4597-8726-d2b979b1b6b7` |

Full mill list: 814, 825, 827, 830, 834-841, 845, 848, 851, 865, 873,
879, 880, DEFLECT.

### How to read 401 vs 404 from Plex

- **401 `REQUEST_NOT_AUTHENTICATED`** — bad credentials OR you're hitting
  a recognized namespace your app isn't subscribed to. Same wire response.
- **404 `RESOURCE_NOT_FOUND`** — Plex's gateway has no route at that path.
  Could mean unknown URL OR subscribed-but-no-resource. Same wire response.
- **The only way to tell apart cleanly** is to compare across many endpoints
  with the same auth, AND ideally compare against a known-good client
  (Insomnia → Generate Code) for ground truth.

### Filter behavior — most query params are silently ignored

Empirically verified: Plex's gateway accepts unknown query parameters
without complaint and just returns the unfiltered set. The only filter
we've seen actually work on `mdm/v1/parts` is `?status=Active` (reduces
19.6 MB → 7.8 MB). The `typeName`, `type`, `category`, `limit` parameters
all return the full unfiltered response. Always assume `limit` does
nothing and use real filters or accept the full DB pull.

---

## Plex Classic Web Services (discovered 2026-04-10)

The REST API at `connect.plex.com` exposes a curated subset of Plex
data. The older **Classic Web Services** at `plexonline.com` can access
virtually the entire Classic schema via Data Sources (parameterized
stored procedures).

- **Endpoint (production):** `POST https://api.plexonline.com/DataSource/Service.asmx`
  (test: `https://testapi.plexonline.com/DataSource/Service.asmx`). The
  originally documented path `plexonline.com/Modules/Xmla/XmlDataSource.asmx`
  is broken / non-functional for Grace — do not use it.
- **WSDL:** append `?WSDL` to the endpoint URL
- **Auth:** HTTP Basic with a dedicated **Web Service User** account
  (username/password; Company Code may be needed). **NOT the Developer Portal
  Consumer Key**, and not an IAM SSO login or the `g5ediadmin` portal admin —
  both of those were tested on 2026-04-10 and **rejected**.
- **Format:** SOAP 1.2/XML, `Content-Type: application/soap+xml`, namespace
  `http://www.plexus-online.com/DataSource`, operation `ExecuteDataSource` with
  an integer `DataSourceKey` (worked example in `docs/Plex_Classic_API_Request.md`)
- **Licensing:** Included with base Plex subscription (no extra cost)
- **ID format:** Integer keys (not UUIDs — mapping needed if mixing with REST API)

**What it can access that REST cannot:**

| Capability | Classic Data Source | REST API status |
|---|---|---|
| Part Operations | `Part_Operation` tables | 4 fields, no FKs |
| Tool-to-operation assignments | Tool Assignment data sources | Does not exist |
| DCS / Attachments | `DCS_v2`, `Attachment_Group_Key` | 404 on all paths |
| Workcenter documents | Workcenter doc data sources | 11 identity fields only |
| Routing / op sequences | Routing data sources | 404 (`manufacturing/v1/routings`) |
| Supply item cross-refs | Full supply item schema | 7 identity fields only |

**Status:** Blocked on a Web Service user account — access request pending
as of the last record (2026-04). See
[`docs/Plex_Classic_API_Request.md`](./Plex_Classic_API_Request.md)
for the request document and the credential test results.

---

## Fusion 360 JSON schema (key fields)

Source file: BROTHER SPEEDIO ALUMINUM.json (28 entries, root "data" array)

| Field                  | Maps to Plex                        | Notes                              |
|------------------------|-------------------------------------|------------------------------------|
| guid                   | External reference key              | Use for dedup on re-sync           |
| type                   | Item sub-category                   | Filter out "holder" and "probe"    |
| description            | Part description                    |                                    |
| product-id             | Part number                         | Vendor part number, key for PO link|
| vendor                 | Supplier (resolve to UUID first)    |                                    |
| post-process.number    | Pocket / turret number              | Critical for workcenter doc update |
| geometry.DC            | Cutting diameter                    |                                    |
| geometry.OAL           | Overall length                      |                                    |
| geometry.NOF           | Number of flutes                    |                                    |
| holder (object)        | Assembly component / BOM link       |                                    |

Tool type distribution in active library:
- flat end mill: 12  |  holder: 6  |  bull nose end mill: 4  |  drill: 2
- face mill: 1  |  form mill: 1  |  slot mill: 1  |  probe: 1

Sync filter: include only `type != "holder" AND type != "probe"`

---

## What's built

### plex_api.py
- `PlexClient` base class with throttling (200 calls/min rate limit)
- Constructor takes `api_key`, `api_secret`, `tenant_id`, `use_test`
- All four config values read from environment variables via `bootstrap.py`
  (`PLEX_API_KEY`, `PLEX_API_SECRET`, `PLEX_TENANT_ID`, `PLEX_USE_TEST`)
- `TENANT_ID` defaults to `GRACE_TENANT_ID` (production Grace)
- `USE_TEST` defaults to `False` (production is the only environment we have)
- `get()` returns parsed JSON or None (legacy)
- `get_envelope()` returns a structured envelope so callers can see HTTP errors
- Extraction helpers: `extract_purchase_orders`, `extract_parts`, `extract_workcenters`
- `discover_all()` endpoint probe utility

### plex_diagnostics.py
- `list_tenants(client)` — GET /mdm/v1/tenants
- `get_tenant(client, id)` — GET /mdm/v1/tenants/{id}
- `tenant_whoami(client, configured_id)` — composite check that compares
  visible tenants against `KNOWN_TENANTS` and returns a structured report
  with `match` enum (`grace`, `g5`, `auth_failed`, `request_failed`,
  `no_data`, `configured`, `other`). Run this first to verify tenant routing.

### tool_library_loader.py
- `load_library(path)` — loads single .json, returns data array
- `load_all_libraries(directory)` — globs all .json files in CAMTools dir
- Stale file guard — aborts if files older than 25h (ADC sync stall detection)
- `PermissionError` and `JSONDecodeError` handling (ADC mid-sync file locks)
- `report_library_contents()` — diagnostic summary
- **Status:** local-ADC path only. `aps_client.py` is the primary source today; this loader is the fallback. Scheduled for removal under the GitHub Actions sync migration ([#18](https://github.com/grace-xwerks/datum/issues/18)); the GCP epic that previously owned this was abandoned 2026-10-14.

### aps_client.py (PR #43)
- OAuth client for Autodesk Platform Services (Fusion Hub online)
- Lists hub projects and downloads tool library JSON over HTTP
- The only source for `sync.py` without `--local`; removes the ADC install requirement on the runtime host
- Pluggable token store, chosen by `APS_TOKEN_STORE` (PRs #25, #30): `file` (default; `.aps_tokens.json`, local dev), `supabase` (production: `SupabaseTokenStore` on the `public.runtime_state` row `aps_refresh_token`, RLS on with no policies so service role only, written back on every rotation before any API call uses the new token, old value kept in `previous_value`), `infisical` (`InfisicalTokenStore`, kept but not default: an Infisical identity on the runner would cost a seat)
- The APS refresh token is **single-use and rotates on every refresh** (15-day idle expiry), so production has exactly one consumer: the nightly runner. A rejected refresh raises "refresh token rejected — re-seed with datum-aps-consent"
- Seeded and re-seeded with `datum-aps-consent` (`scripts/aps_consent.py`; `--store supabase|infisical`, default `supabase`). Runbook: [`docs/SECRETS.md`](./SECRETS.md)

### validate_library.py (PR #28, spec: `docs/validate_library_spec.md`)
- Pre-sync validation gate — FAIL aborts the sync; WARNs surface in verbose/debug
- Three entry points: CLI, programmatic (called from `tool_library_loader.load_library`), Flask `/api/fusion/validate`
- Library-level + per-tool rule tables, cached supplier lookup for vendor validation
- Source-agnostic engine — survives the APS migration with only a CLI default-path change

### supabase_client.py + sync_supabase.py (PR #32)
- Dedicated Supabase project (`datum`, us-east-2): `libraries` / `tools` / `cutting_presets`
- `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` (Infisical `/datum`, `.env.local` fallback) — server-side only, never shipped to the browser
- Full tool record (geometry, holders, pockets) lives here; Plex gets the identity slice via `build_supply_item_payload` (#3)
- PR #34 dropped the `fusion2plex_` table prefix once DB isolation made it redundant
- Was scheduled for replacement by Cloud SQL under the GCP migration epic (#85); that epic was **abandoned 2026-10-14** and Supabase is the permanent staging/cache store

### enrich.py (PR #48, wired in the sync pipeline via PR #54)
- Vendor reference catalog + geometry-based tool enrichment
- Runs before staging writes so enriched records land in Supabase directly

### Plex staging pipeline (sprint PRs #82 / #84, issues #79 / #80 / #81)
- `plex_supply_items` staging table + payload computation
- Feeds the upsert path in #3

### sync.py + nightly deploy (PRs #44, #46, #47, #26, #29, #30)
- `sync.py` CLI entrypoint (`datum-sync`); `--log-file` for persistent logs (PR #46)
- **APS is the only source without `--local`** (PR #26). A config or auth failure logs one `CRITICAL` line naming the cause and exits 2; it no longer falls back to the local ADC files, which hid a dead token. `--local` is explicit and keeps its 25-hour stale-file guard. Exit codes: 0 all synced, 1 some libraries failed, 2 fatal / nothing processed. Since PR #32 it validates at the STAGING stage, so a missing or duplicate `product-id` and a missing `description` are warnings and exit 1 means a real STRUCT / guid / type / geometry failure. Startup logs the "Credential sources:" table
- **Scheduler: GitHub Actions** (`.github/workflows/nightly-sync.yml`, PR #29): cron `17 5 * * *` UTC (00:17 America/Chicago in CDT) plus `workflow_dispatch` with a `dry_run` input. It runs `datum-sync` then `datum-sync-inventory`, reads only repo secrets (synced from Infisical `prod` `/datum` by `datum-secret-sync`), sets `APS_TOKEN_STORE=supabase` and `DATUM_DISABLE_INFISICAL=1`, and has no Infisical CLI or identity. Failures are red runs with a step summary and a 14-day log artifact. A `--dry-run` still rotates the APS refresh token (the refresh consumes the old one)
- **Retired:** the GRACE144 Task Scheduler jobs (PR #47: "Datum Nightly Sync" 00:00, "Datum Nightly Inventory Sync" 00:30). They never wrote: the CLI failed validation on every run (the 2026-04-10 stamp was a manual `/api/aps/sync`). The first live Actions run succeeded on 2026-10-14; to be disabled after the first green *scheduled* Actions run, then deleted together with that host's `.env.local` and `.aps_tokens.json`
- Runbook for red runs, token seeding and rotation: [`docs/SECRETS.md`](./SECRETS.md)

### React UI (PR #41 + successive)
- Tool browser, library browser, Scripts page, last-sync indicator, qty columns
- Deployed to Cloudflare Workers Static Assets (PR #70)

### bootstrap.py
- **Three-source loader** (PRs #24, #30): shell env > Infisical > `.env.local`, all
  via `setdefault`, so real shell env vars always win and a key in both
  Infisical and `.env.local` takes the Infisical value. On import,
  `load_infisical()` runs, then `load_env_local()` (gitignored file)
- `load_infisical()` runs only when `DATUM_DISABLE_INFISICAL` is unset and a
  machine identity exists: `~/.config/datum/infisical-identity.json`
  (`clientId`, `clientSecret`, `projectId`, optional `path`;
  `DATUM_INFISICAL_IDENTITY_FILE` overrides the location) or
  `INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/SECRET` in the environment. Settings:
  `DATUM_INFISICAL_PROJECT_ID`, `DATUM_INFISICAL_ENV` (default `dev`),
  `DATUM_INFISICAL_PATH` (default `/datum`). It shells out to the `infisical`
  CLI (`login`, then `export`) with stdin closed. Any failure is one WARNING
  and a return of 0; import never raises and never writes to stdout
- `describe_sources()` / `format_sources()` report, per credential, which
  source won (`shell`, `infisical`, `.env.local`, `unset`) plus an 8-character
  prefix and length, never more. `sync.py` logs it at startup and
  `tenant_whoami` returns it as `credential_sources`. This implements the
  lesson of History §4
- `run_dev.py` still forces `.env.local` over stale shell variables, but skips
  keys that Infisical supplied
- Imported at the very top of `plex_api.py` so credential reads happen
  AFTER the loaders have run
- Tested in `tests/test_bootstrap.py` (63 tests); `tests/conftest.py` sets
  `DATUM_DISABLE_INFISICAL=1` so no subprocess runs under pytest

### Secrets (summary; full reference in [`docs/SECRETS.md`](./SECRETS.md))

All under folder `/datum` of the Grace Engineering Infisical project, envs
`dev` and `prod`; the GitHub secret has the same name.

| Key | Consumer | Notes |
|---|---|---|
| `PLEX_API_KEY` | `plex_api.py` | Rotates every 31 days (#14) |
| `PLEX_API_SECRET` | `plex_api.py` | Optional today |
| `PLEX_TENANT_ID` | `plex_api.py` | Not secret; code default is the Grace tenant |
| `SUPABASE_URL` | `supabase_client.py` | Not secret |
| `SUPABASE_SERVICE_ROLE_KEY` | `supabase_client.py` | Bypasses RLS; server-side only |
| `APS_CLIENT_ID`, `APS_CLIENT_SECRET` | `aps_client.py` | APS app credentials |
| `APS_REFRESH_TOKEN` | `SupabaseTokenStore` | **Not in Infisical.** Row `aps_refresh_token` in `public.runtime_state` |

Never stored anywhere: `PLEX_ALLOW_WRITES`, `PLEX_BASE_URL`, any `INFISICAL_*`
credential on the runner.

### app.py + templates/static
- Flask endpoint tester UI at http://localhost:5000
- Left rail: Diagnostics (run first), Plex presets, Extractors, Fusion local
- Top: method selector + URL bar + query params + Send (Ctrl/Cmd+Enter)
- Tabbed response pane (Body / Headers / Raw), copy and clear, history
- Env-chip in header shows TEST (amber) or **PROD (red)**, plus
  **READ ONLY** / **WRITES ON** sub-pill
- `/api/plex/raw` proxy lets the UI hit any Plex endpoint via PlexClient
  without exposing credentials to the browser
- **Production write guard** in proxy refuses POST/PUT/PATCH/DELETE
  against `connect.plex.com` unless `PLEX_ALLOW_WRITES=1` is set
- `/api/diagnostics/tenant` runs `tenant_whoami`
- `/api/config` exposes non-secret config including `is_production` and
  `writes_allowed`

### Tests
- `pytest` suite in `tests/`. CI on PRs to `master` via
  `.github/workflows/test.yml`. Branch protection on master requires the
  `pytest` check and repo auto-merge is enabled — **in force as of
  2026-10-14** (strict up-to-date not required, no review requirement,
  `enforce_admins` off so an admin can bypass in an emergency, force-push and
  deletion blocked). Run `pytest` locally before every push regardless.
- Currently **621 tests**, all green (as of 2026-10-14 evening; 583 earlier that day, 440 on 2026-10-13, 262 on 2026-04-17). Run them with the repo venv, `.venv\Scripts\python -m pytest`: bare `py` lacks Flask on Shane's machine.

---

## Immediate TODO (in priority order)

All items below are mirrored as GitHub Issues — see
https://github.com/grace-xwerks/datum/issues for live status.

### Done (historical record — kept for context)

- ~~PlexClient constructor, api_secret header~~
- ~~Find the real Plex tooling endpoint~~ — `inventory/v1/inventory-definitions/supply-items` with `category="Tools & Inserts"`; 1,109 records
- ~~Read baseline tooling inventory from supply-items~~ — PR #21, issue #2
- ~~`validate_library.py` pre-sync validation gate~~ — PR #28, issue #25
- ~~Supabase staging layer (`libraries` / `tools` / `cutting_presets`)~~ — PR #32 + #34, issue #31
- ~~APS cloud integration — no local Fusion install required~~ — PR #43
- ~~Nightly sync CLI entrypoint + packaging~~ — PR #44, issue #9
- ~~Deploy nightly sync to always-on host, scheduled at midnight~~ — PR #47, issues #10 / #11
- ~~Plex API key rotation cadence established~~ — PR #33, issue #12 (next rotation 2026-05-08)
- ~~Vendor reference catalog + geometry-based enrichment~~ — PR #48 / #54
- ~~React UI scaffold + Cloudflare Workers deploy~~ — PRs #41 / #68 / #70
- ~~Plex `plex_supply_items` staging table + qty sync~~ — PRs #77 / #78 / #82 / #84

### Active / next

1. `build_supply_item_payload(fusion_tool: dict) -> dict` — issue #3.
   Reads from Supabase `tools`; maps to a supply-item POST body with
   `category="Tools & Inserts"`, `group="Machining"`,
   `supplyItemNumber=<vendor part-id>`. Staging pipeline (PRs #82 / #84)
   has landed the prerequisites.
2. Match-and-upsert logic by `supplyItemNumber` — issue #3.
   Decide POST (new) vs PUT (update existing) against the 1,109 current
   supply-items. Writes require `PLEX_ALLOW_WRITES=1`.
3. Core sync logic — upsert with `supplyItemNumber` dedup — issue #7.
   Dry-run by default. Calls validate_library gate before every run.
4. Error handling + logging on run failures — issue #8. `--log-file`
   scaffold landed in PR #46; issue remains open for broader error paths.
5. ~~GCP migration (umbrella #7, legacy #85)~~ — **abandoned 2026-10-14**;
   see the banner at the top of `docs/GCP_MIGRATION.md`. Replaced by
   [#16](https://github.com/grace-xwerks/datum/issues/16) (Infisical secrets) and
   [#18](https://github.com/grace-xwerks/datum/issues/18) (nightly sync on
   GitHub Actions).

### Blocked on Plex Classic Web Services access

6. Tool assemblies — issue #4. Classic `Part_Operation` Data Sources likely path.
7. Routing / operation linkage — issue #5. Classic Part Operations + tool assignments.
8. Workcenter doc push — issue #6. Classic DCS_v2. REST workcenter endpoint
   is 11 identity fields, no document/attachment sub-resources.

Access request tracked in `docs/Plex_Classic_API_Request.md`.

### Architectural decisions — #4 and #5 (updated 2026-04-10)

**REST API verdict: blocked.** The `scheduling/v1/jobs` deep-dive
(114,684 records, 18 fields) confirmed zero tool/operation/workcenter
FKs. All 7 document/attachment probe paths returned 404. The Connect
REST API does not expose tool-to-operation relationships, DCS
attachments, or workcenter documents.

**Classic Web Services: viable path.** The older SOAP API at
`plexonline.com/Modules/Xmla/XmlDataSource.asmx` wraps Data Sources
(parameterized stored procs) that CAN access Part Operations, tool
assignments, DCS attachments, and routing data. Requires separate
credentials (Web Service User account + Company Code, NOT the Developer
Portal Consumer Key). Access request doc:
[`docs/Plex_Classic_API_Request.md`](./Plex_Classic_API_Request.md).

- **#4 — Tool Assemblies**: Blocked on REST API. Classic Web Services
  may expose assembly relationships via Part Operation Data Sources.
  Pending Classic API access.
- **#5 — Routing/Operation linkage**: Blocked on REST API. Classic Web
  Services can access Part Operations and tool assignments. Pending
  Classic API access.
- **#6 — Workcenter doc push**: REST workcenter has 11 identity fields
  only, no document/attachment sub-resources. Classic DCS_v2 is the
  path. Pending Classic API access.

---

## Gotchas — read before touching anything

- **EVERY READ HITS PRODUCTION DATA.** There is no test environment for the
  Datum app. Be conscious of rate limits (200/min) and response sizes
  (`mdm/v1/parts` is 19.6 MB unfiltered).
- **Writes are blocked at the proxy by default** (PR #17). To enable:
  `PLEX_ALLOW_WRITES=1` env var. Unset it as soon as you're done.
- **`mdm/v1/parts` and `purchasing/v1/purchase-orders` IGNORE the `limit`
  query param** — empirically verified. `?limit=1` returns the entire
  database (19.6 MB and 44 MB respectively). Always include a real filter
  like `status=Active` and a date range.
- **`PLEX_API_KEY` / `PLEX_API_SECRET` come from Infisical or `.env.local`**
  via `bootstrap.py` (shell env > Infisical > `.env.local`). A real shell env
  var with the same name will OVERRIDE both via `setdefault` semantics — clear
  stale shell vars if you have them, and check `credential_sources` (or
  `bootstrap.format_sources()`) to see which source won. (See History §1 and
  §4 for the painful version of this lesson.)
- **The previously hardcoded API key (`k3SmLW3y…`) is dead.** It's in git
  history but no longer authenticates anywhere.
- **Plex returns 401 `REQUEST_NOT_AUTHENTICATED` for both bad credentials
  AND endpoints under unsubscribed API products.** The only way to tell
  them apart is to compare across multiple endpoints AND against a
  known-good client like Insomnia. See History §2.
- **`l` (lowercase L) and `I` (uppercase i) are visually identical in many
  fonts.** When reading credentials from images, treat them as ambiguous.
  Always paste credentials as text, never read them from a screenshot.
  See History §1.
- **Visible categories in the dev portal ≠ URL prefixes.** "Common APIs,
  Platform APIs, Standalone MES, IIoT" don't 1:1 map to `mdm/`, `purchasing/`,
  `tooling/` etc. The mapping is opaque.
- supplierId in responses is a UUID, not a supplier code (MSC != "MSC001")
- URL-encode spaces in filter strings (`MRO SUPPLIES` -> `MRO%20SUPPLIES`)
- API key must be in header — URL parameter returns 401
- PowerShell: use `Invoke-RestMethod`, not `curl` (alias doesn't pass headers)
- Fusion Tool objects from CAM API are copies, not references
- ADC stale file guard will abort sync if network share files are > 25h old
- `BROTHER SPEEDIO ALUMINUM.json` is committed to repo for reference only —
  sync script must always read from network share, not this file

---

## DNC / machine connections (for future NC program push work)

| Machine              | Protocol       | Address                     |
|----------------------|----------------|-----------------------------|
| Brother Speedio 879  | FTP            | 192.168.25.79               |
| Brother Speedio 880  | FTP            | 192.168.25.80               |
| Citizen / Tsugami    | RS-232 → TCP   | Moxa NPort 5150/5250        |
| Haas VMCs            | Ethernet       | Sigma 5 native              |

---

## History of incorrect hypotheses

This is a postmortem of four wrong turns this project took, written here
so the next agent (or future-me) doesn't repeat them. All four trace back
to one root cause: I misread an API key from a screenshot.

### §1 — The I-vs-l misread (root cause of everything below)

When the user shared a screenshot of the Fusion2Plex Consumer Key from the
Plex Developer Portal, I read the 9th character as `I` (uppercase i) when
it was actually `l` (lowercase L). In most fonts these are visually
indistinguishable. I wrote `AEiK3tYoIfA15wt3x3t0qmILFGAG2NkK` into
`.env.local` instead of the correct `AEiK3tYolfA15wt3x3t0qmILFGAG2NkK`.

Plex's gateway is case-sensitive on the key value, so it returned 401
`REQUEST_NOT_AUTHENTICATED` for everything. That's an entirely generic
"bad credentials" response. From the outside, it looked exactly like a
subscription problem or a tenant scoping problem.

**Lesson**: never read credentials from images. Always have the user paste
the value as text, or use Insomnia "Generate Code" output as ground truth.

### §2 — The "tenant routing" / "subscription" / "more subscription" cycle

Driven by the 401s from §1, I cycled through three wrong hypotheses about
why endpoints were failing:

- **Hypothesis A** (initial): "Tooling endpoints return 403 because IT
  hasn't enabled the Tooling API collection in the dev portal" — sourced
  from the original `Plex_API_Reference.md` written by the previous
  developer. **Plausible but unverified.**
- **Hypothesis B** (my correction in PR #16): "Actually it's tenant
  scoping, not subscription. The 403s will resolve once Courtney completes
  tenant routing." — based on a misread of BRIEFING. **Wrong.**
- **Hypothesis C** (my second correction): "Actually the Plex_API_Reference
  was right, it IS per-product subscription. The Datum app needs more
  product approvals." — based on testing with the wrong key. **Also wrong.**

The actual answer was: **the key value was wrong.** Once the right key
was loaded, every endpoint that was supposedly "blocked" started returning
200. There was no subscription problem and no tenant routing problem.
The whole investigation was an artifact of one character.

**Lesson**: when you have a confusing 401 that resists every hypothesis,
the most likely explanation is that the credential value is wrong, even
if you "verified" it. Verify against a known-good client first.

### §3 — Tooling/manufacturing/production-control 404s

After fixing the key, the working endpoints (`mdm/`, `purchasing/`) all
returned 200. But `tooling/v1/tools`, `manufacturing/v1/operations`, and
`production/v1/control/workcenters` returned 404 `RESOURCE_NOT_FOUND`.

These exact paths were in the original `Plex_API_Reference.md` and worked
for the previous developer with their old credentials on the test
environment. They don't work for the Datum app on production.

There are three possible explanations and we don't yet know which:
- The URL patterns are different in this product set
- Those endpoints aren't included in the Fusion2Plex app's product subscriptions
- The previous developer was on a fundamentally different Plex deployment

**Status**: unresolved. The user will need to share a working Insomnia
URL for one of those endpoints to make progress. Issues #4, #5, #6
remain blocked on this.

### §4 — The stale shell env var

While debugging §1, I wasted ~45 minutes because the user's shell had a
DIFFERENT, also-invalid `PLEX_API_KEY` set as a User-level Windows
environment variable in `HKCU\Environment`. Even when `.env.local` had the
correct value, `bootstrap.setdefault()` correctly refused to override the
shell value, and Flask kept using the wrong key.

The user's stale value was `uP4G8xgHdkoCFcJ00LPgfB5KYILsfdt6` — origin
unknown. Probably set via `setx` or System Properties at some earlier
point in the project's life.

**Lesson**: the very first thing `tenant_whoami` should do is print which
key value (first 8 chars + length + first-source — env var or .env.local)
is being used. We should also probably make `bootstrap.py` log when
`.env.local` is being shadowed by an existing env var.

*Implemented 2026-10-14 (PR #24): `bootstrap.describe_sources()` /
`format_sources()`, surfaced as `credential_sources` in `tenant_whoami` and as
the "Credential sources:" block `sync.py` logs at startup.*

---

## Session log

Reverse chronological. Each entry: what was the goal, what landed, what's left.

### 2026-10-14 — Supabase verified, nightly sync found never-passing, GCP cutover abandoned, Infisical + Actions sync built and run live

**Goal:** Re-orient after the 2026-10-13 docs sweep, verify the Supabase side
now that the MCP is bound to the right org, and decide what to do about the
half-provisioned GCP migration.

**Done:**

- Supabase `datum` project verified live (see the 2026-10-14 status snapshot).
  Found the Fusion → Supabase nightly sync had not written since 2026-04-10 (cause corrected in the evening, below).
- Opened [#16](https://github.com/grace-xwerks/datum/issues/16) (Infisical),
  [#17](https://github.com/grace-xwerks/datum/issues/17) (holder-catalog port),
  [#18](https://github.com/grace-xwerks/datum/issues/18) (sync → GitHub Actions).
- Investigated the GCP cutover: provisioned 2026-04-17, migration never started,
  billing lapsed, project unreachable from Shane's accounts. Decision: abandon.
  Closed #7, removed `scripts/gcp/` + `docs/NEXT_SESSION.md`, banner on
  `docs/GCP_MIGRATION.md`, reframed `docs/REORG_AND_STACK.md`, trimmed the
  GCP steps out of #14.
- Found why Actions was silent (repo created as a fork; workflows disabled
  until a repo admin enables them), enabled it, turned on branch protection
  (`pytest` required) and auto-merge, and left the fork network (see the
  status snapshot).
- Evening: designed and built Infisical secrets + the nightly sync on GitHub
  Actions (spec `docs/superpowers/specs/2026-10-14-infisical-and-actions-sync-design.md`,
  PR #23). An Infisical billing review (Legacy Pro, $18 per identity; identities
  6 → 3) reshaped it: zero new identities, no new project, Datum's 7 keys in
  folder `/datum` of Grace Engineering, and the rotating APS refresh token in a
  Supabase row (spec amended, PR #27). Five code PRs landed: #24 `bootstrap`
  Infisical loader + `describe_sources`, #25 pluggable APS token store +
  `datum-aps-consent`, #26 `sync.py` no silent local fallback + Infisical
  precedence in `run_dev.py` + `credential_sources`, #29
  `.github/workflows/nightly-sync.yml`, #30 `SupabaseTokenStore` +
  `runtime_state` migration (0008, applied) + `datum-aps-consent --store` +
  `DATUM_INFISICAL_PATH`. Tests 440 → 583. New operator doc `docs/SECRETS.md`
  and the credential rules in CLAUDE.md / README updated.
- Late evening: rollout and first live run. The earlier "dead since
  2026-04-10" story was wrong in cause: that stamp was the one manual
  `/api/aps/sync` run, which predates the validation gate (April 8); the gate
  fails any missing or duplicate `product-id`, every Fusion library has such
  tools, so the nightly CLI never passed validation, on GRACE144 or anywhere.
  Proven by Actions run 38623375506 (dry run: 0 succeeded, 3 skipped, 10
  failed). PR #32 added `ValidationStage` (`sync.py` validates at STAGING;
  presence and duplicate `product-id` and `description` are WARN, because
  Supabase dedups on `fusion_guid`, already held 57 of 155 tools with an empty
  `product_id`, and `populate_supply_items` skips them before Plex). PR #34
  made `datum-sync-inventory` exit 0 when no tool is linked. Shane finished the
  Infisical side (folder `/datum`, 7 keys, `datum-secret-sync`, identity renamed
  `grace-workstation-shane`); Datum now uses the `Manage2Plex` Plex key, and the
  "Datum" APS app (the root-level `APS_*` keys are the "Manage Lab" app and
  gave `invalid_credentials`). The APS token row was seeded 19:23 UTC. **First
  live run, Actions run 38625485799, succeeded:** 10 libraries, 224 tools, 753
  presets, supply-item staging 201 staged / 24 skipped, every
  `libraries.ingested_at` advanced (19:39-19:40 UTC), `source_modified_at`
  populated for the first time. Tests 583 -> 621. Filed
  [#33](https://github.com/grace-xwerks/datum/issues/33) (duplicate `product_id` reaching `plex_supply_items`).

**What's left:** the first *scheduled* Actions run must come up green (watch
`libraries.ingested_at`). After that, disable then delete the two GRACE144
Task Scheduler jobs and that host's `.env.local` + `.aps_tokens.json`. Then
close [#16](https://github.com/grace-xwerks/datum/issues/16) and [#18](https://github.com/grace-xwerks/datum/issues/18) by hand and update the
Notion Current State block and Decision Log. [#1](https://github.com/grace-xwerks/datum/issues/1) (Plex
writeback, which is what links tools) and [#14](https://github.com/grace-xwerks/datum/issues/14) (key rotation,
now shared with plex_mcp) stay open. Follow-up:
[#28](https://github.com/grace-xwerks/datum/issues/28) (Infisical access token
off argv).

### 2026-04-09 — Postman buildout + connectivity sweep + supply-item cross-ref finding

**Goal:** Build out the Postman collections to full known scope, then actually run a connectivity sweep to stop hedging about "verified vs unverified" and get ground truth on every endpoint.

**Done:**

- **Postman collections expanded** (PR #38): Plex collection 12 → 33 requests + `[SCHED] List Jobs` + 2 new `[PROBE]` entries = 36 total. Fusion collection 10 → 14 requests. Organized via `[NS]` name prefixes (Postman MCP minimal tier has no folder creation). New `docs/Postman_Collections.md` as the day-to-day reference.
- **Connectivity sweep** (23 requests, 2026-04-09): 18/23 returned 200, 5/23 returned 404, **zero 401s**. Clean ground truth on the full subscription scope.
- **Get-by-ID chain test** (6 requests): all 6 per-id endpoints work. **Every per-id view returns exactly the same fields as the list view** — no hidden detail on any resource.
- **Fresh record counts captured** for every list endpoint (see §3 table above).
- **Full field schemas captured** for every resource (see Plex_API_Reference.md §3).
- **New endpoint discovered:** `scheduling/v1/jobs` returns 200 (15.8s response — large body, schema TBD). Potentially relevant to issue #5.
- **Critical architectural finding:** `inventory/v1/inventory-definitions/supply-items` has **NO cross-references to any other resource**. Supply-items are identity-only — Plex does not model tool→vendor, tool→location, tool→part, tool→workcenter, or tool→operation. This resolves the question the user asked about "how do we get the supplier from a supply-item": **you can't, not from Plex alone.** Vendor data has to live in Supabase as the source of truth. (Also killed the "use PO lines as a back-channel" hypothesis — `purchasing/v1/purchase-orders-lines` returns 404.)
- **Filter no-op confirmed on POs:** unfiltered and `?updatedAfter=2025-01-01` both returned byte-identical 44.2 MB responses. The filter is silently ignored (same behavior as `?limit=N`).
- **Postman descriptions updated** for all 23 sweep endpoints + the 6 per-id endpoints + the 5 `[PROBE]` entries. Historical dates preserved (2026-04-07 initial / 2026-04-09 re-verification).

**Key facts for the next session:**

- The legacy `PLEX_API_KEY=uP4G...` stale shell env var is **still set** in `HKCU\Environment`. It shadows `.env.local` due to `bootstrap.setdefault()` semantics. User cannot permanently unset it in this environment — every session must `unset PLEX_API_KEY && export PLEX_API_KEY='<current>'` before running anything that hits Plex. Document this as a project foot-gun (it's already in History §4 but the env var never got cleaned up).
- This worktree (`charming-hamilton`) does **not** have a `.env.local`. Per the `Bootstrap.py worktree foot-gun` memory, every worktree needs its own until issue #36 lands.
- The `scheduling/v1/jobs` endpoint is the highest-value follow-up — if its records carry tool references, we get the operation→tool mapping that issue #5 is blocked on without needing the `manufacturing/v1/routings` endpoint to ever become available.

**What's left:**

1. Deep-dive `scheduling/v1/jobs` — pull once, sample shape, document fields. Look specifically for `toolId`, `supplyItemId`, `workcenterId`, `operationId` references.
2. Issue #3 — `build_supply_item_payload` reading from Supabase `tools` table, now with full confidence that Plex never needs to know about vendors.
3. Issue #5 / #4 — architectural decisions remain blocked on product questions (not code questions).
4. Clean up the stale shell `PLEX_API_KEY=uP4G...` when the user gets admin access.

### 2026-04-09 — project rename + key rotation

**Goal:** Give the project a real name before it grows further.

**Done:**
- Repo renamed `grace-shane/plex-api` → `grace-shane/datum` (GitHub preserves old URL redirects)
- Plex Developer Portal app renamed `Fusion2Plex` → `Datum`
- New Consumer Key issued and loaded into `.env.local`
- All docs updated: README, CLAUDE.md, TODO.md, docs/BRIEFING.md
- Issue #12 (key rotation) closed

**Next session** (unchanged priority order):
1. Issue #25 — implement `validate_library.py` per `docs/validate_library_spec.md`
2. Issue #3 — `build_supply_item_payload` + match-and-upsert
3. Architectural decisions on #4, #5
4. Issue #6 — workcenter write support

### 2026-04-08 — docs reorg + validate_library spec + drift cleanup

**Started with:**
- All long-form docs (BRIEFING, Plex_API_Reference, Fusion360_Tool_Library_Reference) sitting in the repo root alongside source code
- No design spec for the pre-sync validation gate — the need for one was implicit in #3 and #7 but nothing was written down
- Content drift across docs: architecture diagram in BRIEFING still showed discredited endpoints (`mdm/v1/parts`, `tooling/v1/tool-assemblies`, `production/v1/control/workcenters`), test count frozen at "119+", `docs/Plex_API_Reference.md` Section 4 Target State still pointed at `tooling/v1/tool-assemblies`, line 5 referenced `plexonline.com` (classic UI, not the REST gateway), TODO.md Phase 3 item #1 still `[ ]` despite PR #21 having closed #2
- User had untracked `data/` (Fusion API reference PDFs, ~10 MB) and `outputs/` (CSV extractor snapshot, 154 KB) in the main workspace
- A fresh `docs/validate_library_spec.md` (455 lines) written locally but not yet committed

**Ended with:**
- `docs/` folder created. `BRIEFING.md`, `Plex_API_Reference.md`, `Fusion360_Tool_Library_Reference.md` all moved. Git detected them as 100% renames, so history is preserved — `git log --follow docs/BRIEFING.md` still works.
- `docs/validate_library_spec.md` committed — full design spec for the `validate_library.py` pre-sync validation gate. Three entry points (CLI, programmatic hook in `tool_library_loader`, Flask `/api/fusion/validate`), full library-level + per-tool rule tables, supplier lookup strategy with closest-3 edit-distance hint in debug mode, integration hooks.
- **GitHub issue #25 opened** — `feat: implement validate_library.py pre-sync validation gate`. Blocks #3 and #7. Spec backfilled with the real issue number (was `#XX`).
- `.gitignore` additions: `data/`, `outputs/`, `.claude/worktrees/`
- All 6 drift items fixed (test count, architecture diagram, plexonline, Target State rewrite, TODO checkbox, spec issue number)
- README.md + CLAUDE.md link paths updated to the new `./docs/` prefix
- 156 tests still green — no code changes this session, docs-only

**Pull requests merged this session** (newest first):
- #26 docs: fix stale content drift in BRIEFING, Plex_API_Reference, TODO, spec
- #24 docs: move long-form docs into `docs/`, add validate_library spec, gitignore large dirs

**GitHub issues opened:**
- **#25** feat: implement `validate_library.py` pre-sync validation gate — blocks #3 and #7

**What's left to do next session** (in order):
1. **Issue #25** — implement `validate_library.py` per `docs/validate_library_spec.md`. This is now the highest-priority item since it gates the upsert work. Expect: new module + CLI + Flask routes + loader hook + ~30 pytest cases covering every Rule ID.
2. **Issue #3** — `build_supply_item_payload(fusion_tool)` + match-and-upsert logic, with the validate_library gate called first.
3. **Architectural decisions on #4, #5** — still blocked on a product question, not a code question.
4. **Issue #6** — workcenter doc write support (carefully, with `PLEX_ALLOW_WRITES=1` set deliberately).
5. **Issue #12** — key rotation deadline 2026-05-08.

**Lessons** (follow-ups to "History of incorrect hypotheses" if anything goes sideways the same way):

6. **Worktree gotcha — the painful one this session.** I burned ~30 minutes and a lot of tokens looking for a `docs/` folder the user said they'd added. I kept running `ls` and `git status` from a worktree at `.claude/worktrees/naughty-khayyam/`, not the main workspace at `C:/projects/plex-api/`. Worktrees share the `.git` directory (via `.git` file pointer) but have independent working trees — any new files the user creates in the main workspace are invisible to worktree `ls`. **Rule: when the user says "I added X locally" or "I moved stuff around", the first command is `cd "C:/projects/plex-api" && git status` in the main workspace, not the worktree.** Don't trust the worktree's view of the filesystem for anything the user did in File Explorer.
7. **Git rename detection is automatic.** The user moved files with File Explorer before I got there. Git saw them as deletes + untracked adds. Running `git rm` on the old paths and `git add` on the new paths in the **same commit** lets git's diff-rename detection catch them as 100% renames, preserving history. No special `git mv` step is needed — git is smart about this at commit time, not at stage time. The PR showed them as `rename BRIEFING.md => docs/BRIEFING.md (100%)` without any extra ceremony.
8. **Open issues before writing specs that reference them.** The validate_library spec had `#XX` placeholders for the implementation issue. Cleaner workflow: open the issue first, get the real number, then write the spec with the real number baked in. Otherwise you end up with a two-step commit (add spec with `#XX`, then a follow-up PR to backfill `#25`).
9. **Always re-run `git status` from the correct cwd after a worktree operation.** The shell in the Claude harness runs each Bash command with the cwd reset to the worktree root — which means `cd` inside a Bash call is ephemeral. Chain commands with `&&` when the later ones need to see the earlier `cd` effect. Every `Bash(cd X && git foo)` reminds you of this.

---

### 2026-04-07 — full project bootstrap + Phase 3 read side

**Started with:**
- Hardcoded API key in `plex_api.py` (still in git history)
- Old "gradient/glass dashboard" UI
- TODO.md as the only project tracker
- No tests, no CI, no .env.local concept
- BRIEFING claiming tenant routing was the IT blocker

**Ended with:**
- 11 PRs merged, all via auto-merge after CI passes
- 156 pytest tests, all green, branch protection enforces them on master
- `.env.local` loader (`bootstrap.py`) + dev override (`run_dev.py`)
- Production write guard (`/api/plex/raw` refuses POST/PUT/PATCH/DELETE
  unless `PLEX_ALLOW_WRITES=1`)
- Verified working credentials (`Fusion2Plex` Consumer Key) on
  production with the real Grace tenant `58f781ba-...`
- **Issue #2 closed** — `extract_supply_items()` returns 1,109
  cutting tools and inserts from
  `inventory/v1/inventory-definitions/supply-items` in 1.4s
- Brother Speedio mapping verified — workcenters 879/880 = FTP IPs
  192.168.25.79/.80
- BRIEFING + Plex_API_Reference + TODO all rewritten to match
  empirical reality (with the "History of incorrect hypotheses"
  postmortem above documenting four wrong turns)

**Pull requests merged this session** (newest first):
- #22 fix: stdout UTF-8 reconfigure + ASCII arrows
- #21 feat: extract_supply_items + Fusion testing-harness endpoints (closes #2)
- #20 docs: Plex tooling lives in inventory/v1/inventory-definitions/supply-items
- #19 feat: run_dev.py local launcher
- #18 feat: migrate to PROD Plex environment + verified Grace tenant
- #17 feat: production write guard at the proxy
- #16 docs: correct subscription-not-tenant hypothesis (later corrected by #20)
- #15 fix: surface HTTP errors instead of swallowing them as None
- #14 feat: .env.local loader + Claude Preview launch config
- #13 Endpoint tester UI, tenant diagnostics, env-var credentials, GH issue tracking

**What's left to do tomorrow** (in order):
1. **Issue #3** — `build_supply_item_payload(fusion_tool)` writing to
   `inventory/v1/inventory-definitions/supply-items`. We have the verified
   read path and 1,109 records to learn the schema from.
2. **Architectural decision on issues #4 and #5** — descope or pivot.
   Both are blocked on a real product question, not a code question.
3. **Issue #6** — probe write support on workcenters (carefully, with
   `PLEX_ALLOW_WRITES=1` enabled deliberately).
4. **Issue #12** — key rotation deadline 2026-05-08.

**Lessons** (additions to "History of incorrect hypotheses" if any
session goes sideways the same way again):
1. Never read credentials from images. Always have the user paste
   them as text or via Insomnia "Generate Code" output.
2. Status codes from Plex are misleading on their own. 401 means
   "bad creds OR unsubscribed product"; 404 means "wrong URL OR
   unsubscribed namespace". Compare across endpoints to disambiguate.
3. Plex's URL convention is `<namespace>/v1/<namespace>-definitions/<resource>`
   for definition data — not the bare `<namespace>/v1/<resource>` we
   kept guessing. Tools live at `inventory/v1/inventory-definitions/supply-items`,
   not `tooling/v1/tools` or `mdm/v1/parts`.
4. Server-side filters on Plex endpoints are mostly silently ignored.
   `?limit=1` on `mdm/v1/parts` returns 19.6 MB. Filter client-side.
5. pytest's `capsys` uses UTF-8, so stdout encoding bugs only show
   up under live Flask. Add `sys.stdout.reconfigure(encoding="utf-8")`
   at the top of any process whose stdout might end up captured by
   Flask request handlers on Windows.
