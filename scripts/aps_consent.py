"""
scripts/aps_consent.py — console script ``datum-aps-consent``
=============================================================
Seed (or re-seed) the production APS refresh token.

Run once by a human, and again whenever the nightly sync goes red with
"refresh token rejected — re-seed with datum-aps-consent"::

    datum-aps-consent                       # interactive, Supabase row
    datum-aps-consent --code <code>         # non-interactive (tests)
    datum-aps-consent --store infisical     # Infisical instead

Where the token goes (``--store``):

  supabase (default)  the ``public.runtime_state`` row ``aps_refresh_token``
                      in the datum Supabase project, which
                      ``SupabaseTokenStore`` (``APS_TOKEN_STORE=supabase``)
                      reads and rotates on every nightly run. Needs
                      ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY``.
  infisical           Infisical ``<env>/APS_REFRESH_TOKEN`` for
                      ``InfisicalTokenStore``. Kept for completeness.

Steps:

1. Checks the destination first, so a misconfiguration never burns an
   authorization code: for ``supabase``, builds the client and reads the
   existing row (its value becomes ``previous_value``); for ``infisical``,
   resolves the project id.
2. Prints the Autodesk authorize URL. Open it, sign in, consent.
3. Autodesk redirects to ``APS_CALLBACK_URL``. The page need not load (Flask
   need not be running, and should not be, or it will consume the code);
   copy the ``code`` from the address bar, or paste the whole URL.
4. Exchanges the code with an in-memory ``FileTokenStore(path=None)``, so
   no ``.aps_tokens.json`` is written.
5. Writes the refresh token:

   - ``supabase``: ``aps_client.supabase_set_runtime_state`` — the same
     upsert ``SupabaseTokenStore`` uses — with ``previous_value`` carried
     from the existing row and ``updated_by="datum-aps-consent"``.
   - ``infisical``: ``aps_client.infisical_set_secret`` — the same write
     path ``InfisicalTokenStore`` uses (value via a 0600 temp file, never
     argv).

Infisical auth (``--store infisical`` only): if
``INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/SECRET`` are in the environment, a
machine-identity token is minted and passed as ``--token``; otherwise
``--token`` is omitted and the CLI uses your own ``infisical login`` session
(fine here: this is interactive and one-shot). Project id: ``--project-id``
> ``DATUM_INFISICAL_PROJECT_ID`` > ``"projectId"`` in
``~/.config/datum/infisical-identity.json``. Environment: ``--env``, default
``prod`` (deliberately not ``DATUM_INFISICAL_ENV``, which is ``dev`` on a
workstation).

Prints ``seeded <where> (prefix xxxxxxxx…, len N)`` where ``<where>`` is
``public.runtime_state[aps_refresh_token]`` or ``APS_REFRESH_TOKEN``; never
prints the token. Exit 0 on success, 1 on failure with one line on stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Make the repo root importable when running ``py scripts/aps_consent.py``.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aps_client  # noqa: E402
from aps_client import (  # noqa: E402
    SUPABASE_TOKEN_KEY,
    APSClient,
    FileTokenStore,
    RuntimeStateError,
    has_universal_auth_identity,
    infisical_login_universal_auth,
    infisical_set_secret,
    supabase_get_runtime_state,
    supabase_set_runtime_state,
)
from supabase_client import SupabaseClient, SupabaseConfigError  # noqa: E402

KEY = "APS_REFRESH_TOKEN"
DEFAULT_ENV = "prod"
STORE_SUPABASE = "supabase"
STORE_INFISICAL = "infisical"
DEFAULT_STORE = STORE_SUPABASE
UPDATED_BY = "datum-aps-consent"
IDENTITY_FILE = Path.home() / ".config" / "datum" / "infisical-identity.json"


class ConsentError(RuntimeError):
    """A user-facing failure; its message is the one line we print."""


def _identity_file_project_id() -> str | None:
    """``projectId`` from the workstation identity file, if any. Only the
    project id is read; the identity's credentials are not used here."""
    try:
        data = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = data.get("projectId") if isinstance(data, dict) else None
    pid = str(pid).strip() if pid else ""
    return pid or None


def _resolve_project_id(explicit: str | None) -> str:
    pid = (
        (explicit or "").strip()
        or os.environ.get("DATUM_INFISICAL_PROJECT_ID", "").strip()
        or _identity_file_project_id()
    )
    if not pid:
        raise ConsentError(
            "no Infisical project id: pass --project-id or set "
            "DATUM_INFISICAL_PROJECT_ID"
        )
    return pid


def _extract_code(text: str) -> str:
    """Accept a bare code or the full callback URL containing ``code=``."""
    text = (text or "").strip()
    if "code=" in text:
        query = urlparse(text).query or text.split("?", 1)[-1]
        codes = parse_qs(query).get("code")
        if codes:
            return codes[0].strip()
    return text


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="datum-aps-consent",
        description="Seed the production APS refresh token (Supabase "
                    "public.runtime_state by default, or Infisical).",
    )
    p.add_argument("--code", help="authorization code (or full callback URL); "
                   "prompted for when omitted")
    p.add_argument("--store", choices=(STORE_SUPABASE, STORE_INFISICAL),
                   default=DEFAULT_STORE,
                   help=f"where to seed the token (default: {DEFAULT_STORE})")
    p.add_argument("--project-id", help="Infisical project id, --store infisical "
                   "only (default: DATUM_INFISICAL_PROJECT_ID)")
    p.add_argument("--env", default=DEFAULT_ENV,
                   help=f"Infisical environment, --store infisical only "
                   f"(default: {DEFAULT_ENV})")
    p.add_argument("--client-id", help="APS client id (default: APS_CLIENT_ID)")
    p.add_argument("--client-secret",
                   help="APS client secret (default: APS_CLIENT_SECRET)")
    return p.parse_args(argv)


def _run(args: argparse.Namespace) -> tuple[str, str]:
    """Do the work; return (where, seeded refresh token) for the summary line."""
    # 1. Check the destination before consent, so a bad config or an
    #    unreachable/missing table never burns an authorization code.
    if args.store == STORE_SUPABASE:
        sb = SupabaseClient()
        previous = supabase_get_runtime_state(sb, SUPABASE_TOKEN_KEY)
        where = f"public.{aps_client.RUNTIME_STATE_TABLE}[{SUPABASE_TOKEN_KEY}]"
    else:
        project_id = _resolve_project_id(args.project_id)
        where = KEY

    client = APSClient(
        client_id=args.client_id,
        client_secret=args.client_secret,
        token_store=FileTokenStore(path=None),
    )
    url = client.get_authorize_url()
    print("Open this URL in a browser, sign in to Autodesk and consent:")
    print()
    print(f"  {url}")
    print()
    print("Autodesk then redirects to the callback URL. The page does not need")
    print("to load: copy the `code` value from the address bar (or the whole URL).")

    raw = args.code
    if raw is None:
        try:
            raw = input("code: ")
        except EOFError:
            raw = ""
    code = _extract_code(raw)
    if not code:
        raise ConsentError("no authorization code given")

    client.exchange_code(code)
    refresh_token = client.tokens.refresh_token
    if not refresh_token:
        raise ConsentError("Autodesk returned no refresh token")

    if args.store == STORE_SUPABASE:
        supabase_set_runtime_state(
            sb, SUPABASE_TOKEN_KEY, refresh_token,
            previous_value=previous, updated_by=UPDATED_BY,
        )
    else:
        token = infisical_login_universal_auth() if has_universal_auth_identity() else None
        infisical_set_secret(project_id, args.env, KEY, refresh_token, token=token)
    return where, refresh_token


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        where, refresh_token = _run(args)
    except KeyboardInterrupt:
        print("datum-aps-consent: failed: interrupted", file=sys.stderr)
        return 1
    except (ConsentError, aps_client.APSConfigError, aps_client.APSAuthError,
            aps_client.InfisicalCLIError, RuntimeStateError,
            SupabaseConfigError) as e:
        print(f"datum-aps-consent: failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # one clear line, never a traceback with secrets
        print(f"datum-aps-consent: failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"seeded {where} (prefix {refresh_token[:8]}…, len {len(refresh_token)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
