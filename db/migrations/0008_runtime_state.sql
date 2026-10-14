-- =========================================================================
-- Datum — runtime_state (runner state, service role only)
-- =========================================================================
-- Small key/value table for state the nightly sync must carry from one run
-- to the next. First (and today only) row: 'aps_refresh_token', the
-- production Autodesk APS refresh token.
--
-- Why a table: APS refresh tokens are single-use and rotate on every
-- refresh (15-day idle expiry), so the runner must write the new token back
-- on every run, before any API call uses it (aps_client.SupabaseTokenStore).
-- The GitHub Actions runner already holds SUPABASE_SERVICE_ROLE_KEY, so this
-- row needs no new credential (an Infisical identity for write-back would
-- cost a paid seat). Seeded and re-seeded by `datum-aps-consent`.
--
-- Design:
--   - key TEXT PRIMARY KEY: one row per piece of state; writers upsert
--     with on_conflict=key
--   - previous_value keeps the value the row held before the last write,
--     for manual recovery if a write lands but the new token never works
--   - updated_by names the writer ('datum-sync' or 'datum-aps-consent')
--   - RLS ENABLED WITH NO POLICIES: anon and authenticated get nothing;
--     only the service role (which bypasses RLS) can read or write. This
--     is deliberate. Never add an anon/authenticated policy here: the
--     values are live credentials.
--
-- Spec: docs/superpowers/specs/2026-10-14-infisical-and-actions-sync-design.md
--       ("Amendment (Agent B2)"). Issues: #16, #18.
-- =========================================================================

CREATE TABLE public.runtime_state (
  key             TEXT PRIMARY KEY,
  value           TEXT NOT NULL,
  previous_value  TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by      TEXT
);

CREATE TRIGGER runtime_state_set_updated_at
  BEFORE UPDATE ON public.runtime_state
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE public.runtime_state ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.runtime_state IS
  'Runner state carried between nightly sync runs, e.g. the single-use rotating APS refresh token (key aps_refresh_token), written back by aps_client.SupabaseTokenStore on every refresh and seeded by datum-aps-consent. RLS is enabled with no policies on purpose: values are live credentials, so only the service role (which bypasses RLS) may read or write; anon and authenticated see nothing.';

COMMENT ON COLUMN public.runtime_state.previous_value IS
  'Value the row held before the most recent write, kept for manual recovery. For aps_refresh_token it is normally already consumed by Autodesk.';

COMMENT ON COLUMN public.runtime_state.updated_by IS
  'Writer of the most recent value: datum-sync (runner rotation) or datum-aps-consent (human seed).';
