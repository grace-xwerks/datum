"""
scripts/aps_consent.py — console script ``datum-aps-consent``
=============================================================
Seed (or re-seed) the production APS refresh token in Infisical.

Run once by a human, and again whenever the nightly sync goes red with
"refresh token rejected — re-seed with datum-aps-consent"::

    datum-aps-consent                       # interactive
    datum-aps-consent --code <code>         # non-interactive (tests)

Steps:

1. Prints the Autodesk authorize URL. Open it, sign in, consent.
2. Autodesk redirects to ``APS_CALLBACK_URL``. The page need not load (Flask
   need not be running, and should not be, or it will consume the code);
   copy the ``code`` from the address bar, or paste the whole URL.
3. Exchanges the code with an in-memory ``FileTokenStore(path=None)``, so
   no ``.aps_tokens.json`` is written.
4. Writes the refresh token to Infisical ``<env>/APS_REFRESH_TOKEN``
   through ``aps_client.infisical_set_secret`` — the same write path
   ``InfisicalTokenStore`` uses (value via a 0600 temp file, never argv).

Infisical auth: if ``INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/SECRET`` are in the
environment, a machine-identity token is minted and passed as ``--token``;
otherwise ``--token`` is omitted and the CLI uses your own ``infisical
login`` session (fine here: this is interactive and one-shot).

Project id: ``--project-id`` > ``DATUM_INFISICAL_PROJECT_ID`` >
``"projectId"`` in ``~/.config/datum/infisical-identity.json``.
Environment: ``--env``, default ``prod`` (deliberately not
``DATUM_INFISICAL_ENV``, which is ``dev`` on a workstation).

Prints ``seeded APS_REFRESH_TOKEN (prefix xxxxxxxx…, len N)``; never prints
the token. Exit 0 on success, 1 on failure with one line on stderr.
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
    APSClient,
    FileTokenStore,
    has_universal_auth_identity,
    infisical_login_universal_auth,
    infisical_set_secret,
)

KEY = "APS_REFRESH_TOKEN"
DEFAULT_ENV = "prod"
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
        description="Seed the APS refresh token in Infisical (prod).",
    )
    p.add_argument("--code", help="authorization code (or full callback URL); "
                   "prompted for when omitted")
    p.add_argument("--project-id", help="Infisical project id "
                   "(default: DATUM_INFISICAL_PROJECT_ID)")
    p.add_argument("--env", default=DEFAULT_ENV,
                   help=f"Infisical environment (default: {DEFAULT_ENV})")
    p.add_argument("--client-id", help="APS client id (default: APS_CLIENT_ID)")
    p.add_argument("--client-secret",
                   help="APS client secret (default: APS_CLIENT_SECRET)")
    return p.parse_args(argv)


def _run(args: argparse.Namespace) -> str:
    """Do the work; return the seeded refresh token (for the summary line)."""
    project_id = _resolve_project_id(args.project_id)

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

    token = infisical_login_universal_auth() if has_universal_auth_identity() else None
    infisical_set_secret(project_id, args.env, KEY, refresh_token, token=token)
    return refresh_token


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    refresh_token: str | None = None
    try:
        refresh_token = _run(args)
    except KeyboardInterrupt:
        print("datum-aps-consent: failed: interrupted", file=sys.stderr)
        return 1
    except (ConsentError, aps_client.APSConfigError, aps_client.APSAuthError,
            aps_client.InfisicalCLIError) as e:
        print(f"datum-aps-consent: failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # one clear line, never a traceback with secrets
        print(f"datum-aps-consent: failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"seeded {KEY} (prefix {refresh_token[:8]}…, len {len(refresh_token)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
