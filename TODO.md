# Project Roadmap: Datum — Fusion 360 → Plex Tooling Sync

This document outlines the step-by-step implementation plan for the Autodesk Fusion 360 tool library to Plex Manufacturing Cloud synchronization project.

> **Live tracking:** All unchecked items below are mirrored as GitHub Issues.
> See <https://github.com/grace-xwerks/datum/issues> for current status, comments, and blockers.

> **Issue / PR numbering (updated 2026-10-13).** This repo was migrated from
> `grace-shane/Datum` (that account is retired). Open issues were re-created
> with **new numbers**; closed issues were not migrated, and the new repo has
> no PRs of its own. Legacy → current: #3→#1, #4→#2, #5→#3, #6→#4, #7→#5,
> #8→#6, #85→#7, #98–#104→#8–#14. A linked `#N` below is a **current** issue
> (with its legacy number in parentheses); a bare, unlinked `#N` — and every
> `PR #N` — uses the **legacy** numbering and is kept for archaeology (the PR
> numbers match the squash-commit subjects in `git log`).

## Phase 1: API Discovery & Authentication

- [x] Set up Postman and discover relevant Plex API endpoints.
- [x] Obtain API authentication credentials (Client ID/Secret or API Key) for the Plex environment.
- [x] Successfully authenticate via a test script (`plex_api.py`).
- [x] **ACTION ITEM**: Regenerate API Key in the Developer Portal (Previous key was exposed in `.docx` git history).

## Phase 2: Local Data Reading & Parsing

- [x] Identify the permanent network share path for the Fusion 360 tool library JSON files.
- [x] Write a script to consistently read the JSON files from the network share (Fusion files are the absolute Source of Truth).
- [x] Parse the Fusion 360 JSON schema to identify key tooling attributes (Completed in `Fusion360_Tool_Library_Reference.md`).

## Phase 3: Plex API Source-of-Truth Implementation

> **Ordering rule (2026-04-17):** every real write to `connect.plex.com` in
> this phase ships **last** and is gated on the Plex-mimic mock HTTP server
> (legacy #92). The mock itself has **shipped** (PR #96 / #97, in
> `tools/plex_mock/`; hardening follow-ups are #8–#13). Nothing
> POSTs/PUTs/PATCHes against the live tenant until the mimic has run clean for
> a documented validation window — no such window is recorded in the repo yet.
> See `MEMORY.md` in the Claude memory folder for the full rationale.

- [x] **DONE (PR #21).** Implement API call to retrieve current tooling inventory — `extract_supply_items(client)` in `plex_api.py` hits `inventory/v1/inventory-definitions/supply-items` (2,516 records), filters to `category="Tools & Inserts"` (1,109 records), and writes a CSV snapshot to `outputs/`. Verified live: 30 KB response, 1.4s round trip. → #2 *(closed)*
- [ ] Implement API call to upsert supply-items — payload compute, staging, post-sync hook, and UI have all landed (PRs #82 / #84 / #90; issues #79 / #80 / #81 closed). Remaining work is the HTTP POST itself, which ships **last** behind the Plex-mimic mock. → [#1](https://github.com/grace-xwerks/datum/issues/1) (legacy #3) **— gated on the Plex-mimic validation window (mock shipped; see ordering rule)**
- [ ] Implement Tool Assembly handling — **blocked on Classic Web Services access.** Plex REST supply-items are identity-only; Classic `Part_Operation` Data Sources are the likely path. See BRIEFING §"Classic Web Services" and `docs/Plex_Classic_API_Request.md`. → [#2](https://github.com/grace-xwerks/datum/issues/2) (legacy #4)
- [ ] Implement API call to link tools to Routings/Operations — **blocked on Classic Web Services access.** REST `mdm/v1/operations` has no FK to tools; `scheduling/v1/jobs` deep-dive (114,684 records) confirmed zero tool/operation FKs. → [#3](https://github.com/grace-xwerks/datum/issues/3) (legacy #5)
- [ ] Implement API call to update tooling within the specific Workcenter Document — GET workcenter now verified (PR #20) with a Brother Speedio `workcenterCode` → `workcenterId` map. Writes (PUT/PATCH support) are the unknown — investigation happens **against the Plex-mimic mock first, not the live tenant**, then ships last. Classic DCS_v2 remains a separate fallback path. → [#4](https://github.com/grace-xwerks/datum/issues/4) (legacy #6) **— gated on the Plex-mimic validation window (mock shipped; see ordering rule)**
- [x] **IT blocker resolved.** The Datum app on production with the Grace tenant authenticates correctly. The earlier "tenant routing" / "subscription approvals" investigation was a red herring caused by a credential typo. See BRIEFING.md "History of incorrect hypotheses" for the postmortem. → #1

## Phase 4: Data Mapping & Sync Logic

- [x] Create a mapping definition between Fusion 360 data structures and Plex API payload requirements (Completed in `Fusion360_Tool_Library_Reference.md`).
- [ ] Implement the core synchronization logic: → [#5](https://github.com/grace-xwerks/datum/issues/5) (legacy #7)
  - Utilize the Fusion JSON file output as the explicit Source of Truth relative to Plex.
  - Push updates for purchased consumables to the master inventory list.
  - Link those consumables into Tool Assemblies.
  - Ensure those assemblies dynamically flow down to the Routing and then the Job when run in the shop, linking tools directly to manufactured parts.
  - Push final setups to the workcenter documents.
- [ ] Add basic error handling and logging (e.g., logging successful syncs or failed API calls to a text file on the network share). → [#6](https://github.com/grace-xwerks/datum/issues/6) (legacy #8)

## Phase 5: Automation & Deployment

- [x] **DONE (PR #44).** Finalize the synchronization script — `sync.py` nightly CLI entrypoint + `pyproject.toml` packaging. → #9 *(closed)*
- [x] **DONE (PR #47).** Deploy the script — nightly sync runs on an always-on host. → #10 *(closed)* **Superseded 2026-10-14:** the GRACE144 host has not written since 2026-04-10 and is being retired in favour of GitHub Actions ([#18](https://github.com/grace-xwerks/datum/issues/18); `.github/workflows/nightly-sync.yml` has landed, rollout pending). See [`docs/SECRETS.md`](./docs/SECRETS.md).
- [x] **DONE (PR #47).** Schedule the script to run nightly at midnight. → #11 *(closed)* **Superseded 2026-10-14:** the Task Scheduler jobs ("Datum Nightly Sync" 00:00, "Datum Nightly Inventory Sync" 00:30) are to be disabled after the first green scheduled Actions run, then deleted ([#18](https://github.com/grace-xwerks/datum/issues/18)).
- [x] **DONE (PR #33).** Rotate the Plex API key — old key from git history no longer authenticates; the `Datum` Consumer Key is current. Next rotation deadline 2026-05-08 tracked separately. → #12 *(closed)*

---

## Built beyond the original roadmap

Work that has landed since the Phase 1–5 roadmap was written, tracked via GitHub Issues and not part of the original plan:

- **Supabase staging layer (#31, PR #32 + #34)** — `libraries` / `tools` / `cutting_presets` tables on a dedicated Supabase DB. Fusion JSON ingests here first; Plex gets only the identity slice. Table prefix `fusion2plex_` was removed in PR #34 once the DB isolation made it redundant.
- **APS cloud integration (PR #43)** — `aps_client.py` pulls tool libraries from Autodesk Platform Services. `sync.py` is now APS-first with local ADC as fallback; ADC removal is tracked under the GitHub Actions sync migration ([#18](https://github.com/grace-xwerks/datum/issues/18)).
- **Pre-sync validation gate (#25, PR #28)** — `validate_library.py` with CLI / programmatic / Flask entry points per `docs/validate_library_spec.md`. Gates every sync run.
- **React UI (PR #41 + subsequent)** — tool browser, library browser, Scripts page, last-sync indicator. Deployed to Cloudflare Workers (PR #70).
- **Vendor reference catalog + geometry-based enrichment (PR #48)** — `enrich.py`, wired upstream in the sync pipeline (PR #54).
- **Plex `plex_supply_items` staging pipeline (sprint: #79/#80/#81/#67/#76, PRs #82 + #84)** — prerequisite for #3 upsert work.
- **Tool inventory qty sync (#75, PRs #77 + #78)** — Plex → Supabase qty cache.
- **Classic Web Services discovery (PR #42)** — documented the SOAP path that can unblock [#2](https://github.com/grace-xwerks/datum/issues/2) / [#3](https://github.com/grace-xwerks/datum/issues/3) / [#4](https://github.com/grace-xwerks/datum/issues/4). The first-documented `plexonline.com/Modules/Xmla/…` path proved broken; the working endpoint is `api.plexonline.com/DataSource/Service.asmx` (HTTP Basic, needs a dedicated Web Service user — IAM and portal-admin logins were rejected). Access request pending; see `docs/Plex_Classic_API_Request.md`.

## Phase 6: ~~GCP migration~~ — ABANDONED 2026-10-14

Epic #7 (legacy #85) is closed. GCP was provisioned on 2026-04-17 but the migration never started; project billing lapsed and the project is being deleted. Supabase stays as the permanent staging/cache store. Full story and the where-did-each-goal-go table: banner at the top of [`docs/GCP_MIGRATION.md`](./docs/GCP_MIGRATION.md).

Successor work:

- [ ] Secrets → Infisical (replaces the Secret Manager item) → [#16](https://github.com/grace-xwerks/datum/issues/16)
- [ ] Nightly sync → GitHub Actions, drop the local-ADC fallback (replaces the Cloud Scheduler and APS-only loader items) → [#18](https://github.com/grace-xwerks/datum/issues/18)
- [x] Cloudflare DNS `datum.graceops.dev` — already live on Cloudflare Workers since 2026-04-15 (legacy PR #70)
