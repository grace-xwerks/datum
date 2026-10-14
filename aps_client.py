"""
aps_client.py
Autodesk Platform Services (APS) OAuth + Data Management client
Grace Engineering — Datum project
=============================================================
Handles 3-legged OAuth 2.0 with Autodesk and provides methods to
traverse Fusion Team hubs to locate and download cloud tool library
files (.tools / .json).

This eliminates the need for Fusion 360 or Autodesk Desktop Connector
to be installed locally. The pipeline is:

  APS Hub -> download .tools file -> unzip -> parse JSON
  -> same schema as local CAMTools files -> Supabase ingest

Credentials come from environment variables loaded via ``bootstrap.py``:

  APS_CLIENT_ID        App client ID from aps.autodesk.com
  APS_CLIENT_SECRET    App client secret
  APS_CALLBACK_URL     OAuth redirect URI (default: http://localhost:5000/api/aps/callback)

Token state lives in a pluggable token store, chosen by ``APS_TOKEN_STORE``
(or injected via ``APSClient(token_store=...)``):

  file (default)  ``FileTokenStore`` — a local JSON file (``.aps_tokens.json``,
                  gitignored) so tokens survive Flask debug reloads and
                  process restarts. Local dev.
  supabase        ``SupabaseTokenStore`` — the refresh token lives in the
                  ``public.runtime_state`` row ``aps_refresh_token`` of the
                  datum Supabase project (RLS on, no policies: service role
                  only) and is written back on every rotation, previous value
                  kept in ``previous_value``. Production: the nightly sync on
                  GitHub Actions. Needs only ``SUPABASE_URL`` +
                  ``SUPABASE_SERVICE_ROLE_KEY``.
  infisical       ``InfisicalTokenStore`` — the refresh token lives in
                  Infisical (``DATUM_INFISICAL_PROJECT_ID``,
                  ``DATUM_INFISICAL_ENV`` default ``prod``, key
                  ``APS_REFRESH_TOKEN``) and is written back on every
                  rotation. Authenticates with
                  ``INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/SECRET``. Kept for
                  completeness; unused by default (an Infisical identity on
                  the runner costs a seat).

Seed or re-seed the production token with ``datum-aps-consent`` (``--store
supabase`` by default, ``--store infisical`` for the Infisical store).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

import bootstrap  # noqa: F401 — loads .env.local into os.environ on import
from supabase_client import SupabaseClient, SupabaseConfigError

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
APS_CLIENT_ID = os.environ.get("APS_CLIENT_ID", "")
APS_CLIENT_SECRET = os.environ.get("APS_CLIENT_SECRET", "")
APS_CALLBACK_URL = os.environ.get(
    "APS_CALLBACK_URL", "http://localhost:5000/api/aps/callback"
)

APS_AUTH_BASE = "https://developer.api.autodesk.com/authentication/v2"
APS_DM_BASE = "https://developer.api.autodesk.com"

# Scopes needed for reading hub data (tool libraries live in the hub)
DEFAULT_SCOPES = "data:read"

DEFAULT_TIMEOUT = 30  # seconds


# ─────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────
class APSConfigError(RuntimeError):
    """Raised when APS_CLIENT_ID or APS_CLIENT_SECRET is missing."""


class APSAuthError(RuntimeError):
    """Raised when OAuth flow fails (bad code, expired token, etc.)."""


class APSHTTPError(RuntimeError):
    """Raised when an APS API call returns a non-2xx response."""

    def __init__(self, status: int, body: Any, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"APS {status} on {url}: {body}")


# ─────────────────────────────────────────────
# Token stores
# ─────────────────────────────────────────────
# Default token file lives next to .env.local — both are gitignored.
_DEFAULT_TOKEN_PATH = Path(__file__).resolve().parent / ".aps_tokens.json"

#: Env var that picks the token store when ``APSClient(token_store=None)``.
TOKEN_STORE_ENV = "APS_TOKEN_STORE"


class BaseTokenStore:
    """
    In-memory OAuth token state plus a persistence hook.

    Public surface (what ``APSClient`` relies on):

    - ``access_token`` / ``refresh_token`` / ``expires_at`` attributes
    - ``is_valid`` — a non-empty access token that has not expired
    - ``update(data)`` — take an Autodesk token response, then persist
    - ``clear()`` — forget the tokens

    Subclasses override ``_persist()`` (called at the end of ``update()``)
    and ``_discard()`` (called at the end of ``clear()``). Both default to
    no-ops, so ``BaseTokenStore()`` itself is a pure in-memory store.
    """

    def __init__(self) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: float = 0.0  # epoch seconds

    @property
    def is_valid(self) -> bool:
        return bool(self.access_token) and time.time() < self.expires_at

    def update(self, data: dict) -> None:
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        self.expires_at = time.time() + data.get("expires_in", 3600) - 60
        self._persist()

    def clear(self) -> None:
        self.access_token = None
        self.refresh_token = None
        self.expires_at = 0.0
        self._discard()

    def _persist(self) -> None:
        """Hook: store the current tokens somewhere durable."""

    def _discard(self) -> None:
        """Hook: remove durable token state."""


class FileTokenStore(BaseTokenStore):
    """
    Persists OAuth tokens to a local JSON file so they survive Flask
    debug reloads and process restarts. The default for local dev.

    Parameters
    ----------
    path : Path | None
        File to persist tokens to. ``None`` disables persistence
        (pure in-memory, useful for tests and ``datum-aps-consent``).
    """

    def __init__(self, path: Path | None = _DEFAULT_TOKEN_PATH):
        super().__init__()
        self._path = path
        self._load()

    def _persist(self) -> None:
        self._save()

    def _discard(self) -> None:
        if self._path and self._path.exists():
            self._path.unlink()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.write_text(json.dumps({
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
            }), encoding="utf-8")
        except OSError as e:
            log.warning("Could not persist APS tokens: %s", e)

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            self.expires_at = data.get("expires_at", 0.0)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            log.warning("Could not load APS tokens from %s: %s", self._path, e)


#: Backward-compatible name: ``TokenStore`` *is* the file store.
TokenStore = FileTokenStore


# ─────────────────────────────────────────────
# Infisical CLI helpers
# ─────────────────────────────────────────────
# Same subprocess discipline as grace-cam's scripts/plex_mcp_launch.py:
#   - stdin=DEVNULL so an expired human session cannot block on (or eat
#     input for) the CLI's interactive region picker;
#   - stdout/stderr captured, decoded as UTF-8 with errors="replace" (the
#     CLI's ANSI colour escapes are not valid cp1252 and would otherwise
#     raise inside the capture thread on Windows);
#   - a hard timeout; ANSI stripped from stderr before it is surfaced.
INFISICAL_TIMEOUT = 60  # seconds
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_UA_ID = "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID"
_UA_SECRET = "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET"


class InfisicalCLIError(RuntimeError):
    """An ``infisical`` CLI call failed (missing CLI, timeout, non-zero exit)."""


def _tidy(text: str | None, *redact: str | None) -> str:
    """One readable line out of CLI stderr / an error, with values redacted
    (before truncation, so no partial value survives)."""
    out = " ".join(_ANSI.sub("", text or "").split())
    for value in redact:
        if value:
            out = out.replace(value, "<redacted>")
    return out[:240]


def _run_infisical(args: list[str]) -> subprocess.CompletedProcess:
    """Run ``infisical <args>`` with the parent environment."""
    try:
        return subprocess.run(
            ["infisical", *args],
            env=dict(os.environ),
            timeout=INFISICAL_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
    except FileNotFoundError:
        raise InfisicalCLIError("infisical CLI not on PATH") from None
    except subprocess.TimeoutExpired:
        raise InfisicalCLIError(
            f"infisical {args[0]} timed out after {INFISICAL_TIMEOUT}s"
        ) from None


def has_universal_auth_identity() -> bool:
    """True when both ``INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/SECRET`` are set."""
    return bool(os.environ.get(_UA_ID)) and bool(os.environ.get(_UA_SECRET))


def infisical_login_universal_auth() -> str:
    """
    Mint a short-lived Infisical access token from the machine identity in
    ``INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/SECRET``. The client secret travels
    in the child's environment, never in argv.
    """
    res = _run_infisical(["login", "--method=universal-auth", "--plain", "--silent"])
    token = (res.stdout or "").strip()
    if res.returncode != 0 or not token:
        raise InfisicalCLIError(
            f"universal-auth login failed (rc={res.returncode}): {_tidy(res.stderr)}"
        )
    return token


def infisical_get_secret(
    project_id: str, env: str, key: str, token: str | None = None
) -> str | None:
    """
    Read one secret value. Returns ``None`` when it is missing or empty.
    ``token=None`` omits ``--token`` and uses the CLI's own login session.
    """
    args = [
        "secrets", "get", key, "--plain", "--silent", "--expand=false",
        f"--projectId={project_id}", f"--env={env}",
    ]
    if token:
        args.append(f"--token={token}")
    res = _run_infisical(args)
    if res.returncode != 0:
        raise InfisicalCLIError(
            f"infisical secrets get {key} failed (rc={res.returncode}): "
            f"{_tidy(res.stderr)}"
        )
    value = (res.stdout or "").strip()
    if not value or value == "*not found*":
        return None
    return value


def infisical_set_secret(
    project_id: str, env: str, key: str, value: str, token: str | None = None
) -> None:
    """
    Write one secret value without ever putting it in a process argv.

    The value goes into a ``tempfile.mkstemp`` file (created 0600, owner-only)
    and the CLI is called as ``infisical secrets set KEY=@<path>``, the CLI's
    read-value-from-file form. The file is unlinked in ``finally`` as soon as
    the CLI returns, success or failure. The argv therefore carries only the
    key and the temp path. ``token=None`` omits ``--token`` so a human
    ``infisical login`` session works (``datum-aps-consent``).
    """
    if not value:
        raise InfisicalCLIError(f"refusing to write an empty value to {key}")
    fd, path = tempfile.mkstemp(prefix="datum-infisical-", suffix=".secret")
    try:
        try:
            os.chmod(path, 0o600)
            os.write(fd, value.encode("utf-8"))
        finally:
            os.close(fd)
        args = [
            "secrets", "set", f"{key}=@{path}", "--silent",
            f"--projectId={project_id}", f"--env={env}",
        ]
        if token:
            args.append(f"--token={token}")
        res = _run_infisical(args)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if res.returncode != 0:
        raise InfisicalCLIError(
            f"infisical secrets set {key} failed (rc={res.returncode}): "
            f"{_tidy(res.stderr, value)}"
        )


class InfisicalTokenStore(BaseTokenStore):
    """
    Production token store: the APS refresh token lives in Infisical.

    APS refresh tokens are single-use and rotate on every refresh, so the
    only safe home for the production token is one that the runner rewrites
    every time it refreshes.

    - On construction, reads ``key`` from Infisical. No access token is
      persisted, so ``is_valid`` starts False and the first
      ``APSClient._ensure_token()`` refreshes.
    - ``update(data)`` sets the in-memory fields, then writes the new
      refresh token back to Infisical **before returning** (see
      ``infisical_set_secret`` for how the value stays out of argv). Any
      failure raises ``APSAuthError("refresh token write-back failed: …")``
      and invalidates the in-memory access token, so no API call can run on
      a token chain Infisical does not know about.
    - ``clear()`` is a no-op with a warning: code never deletes the
      production token.

    Auth is the machine identity in ``INFISICAL_UNIVERSAL_AUTH_CLIENT_ID``
    / ``INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET``. A fresh Infisical access
    token is minted for each operation (one for the read, one for the
    write-back) rather than cached, so a long run can never hit an expired
    Infisical token on the critical write.

    Parameters
    ----------
    project_id : str | None
        Infisical project id. Defaults to ``DATUM_INFISICAL_PROJECT_ID``.
    env : str | None
        Infisical environment slug. Defaults to ``DATUM_INFISICAL_ENV``,
        then ``"prod"``.
    key : str
        Secret name holding the refresh token.
    """

    def __init__(
        self,
        project_id: str | None = None,
        env: str | None = None,
        key: str = "APS_REFRESH_TOKEN",
    ):
        super().__init__()
        self.project_id = (
            project_id or os.environ.get("DATUM_INFISICAL_PROJECT_ID", "")
        ).strip()
        self.env = (env or os.environ.get("DATUM_INFISICAL_ENV", "")).strip() or "prod"
        self.key = key
        if not self.project_id:
            raise APSConfigError(
                "InfisicalTokenStore needs an Infisical project id: set "
                "DATUM_INFISICAL_PROJECT_ID (APS_TOKEN_STORE=infisical)."
            )
        self._load()

    def _token(self) -> str:
        missing = [n for n in (_UA_ID, _UA_SECRET) if not os.environ.get(n)]
        if missing:
            raise APSAuthError(
                "InfisicalTokenStore needs a machine identity: "
                f"{' and '.join(missing)} not set."
            )
        return infisical_login_universal_auth()

    def _where(self) -> str:
        return f"Infisical {self.env}/{self.key}"

    def _load(self) -> None:
        try:
            value = infisical_get_secret(
                self.project_id, self.env, self.key, token=self._token()
            )
        except InfisicalCLIError as e:
            raise APSAuthError(f"could not read {self._where()}: {e}") from None
        if not value:
            raise APSAuthError(
                f"{self._where()} is empty or missing — seed it with "
                "datum-aps-consent."
            )
        self.refresh_token = value
        log.info("APS refresh token loaded from %s (len %d)", self._where(), len(value))

    def _persist(self) -> None:
        try:
            if not self.refresh_token:
                raise InfisicalCLIError(
                    "Autodesk token response carried no refresh_token"
                )
            infisical_set_secret(
                self.project_id, self.env, self.key, self.refresh_token,
                token=self._token(),
            )
        except (InfisicalCLIError, APSAuthError) as e:
            # Keep refresh_token in memory (it may be the only live copy) but
            # make the access token unusable until a write-back succeeds.
            self.access_token = None
            self.expires_at = 0.0
            raise APSAuthError(f"refresh token write-back failed: {e}") from None
        log.info(
            "APS refresh token written back to %s (len %d)",
            self._where(), len(self.refresh_token),
        )

    def clear(self) -> None:
        log.warning(
            "InfisicalTokenStore.clear() is a no-op: the production APS "
            "refresh token in %s is never deleted from code.", self._where(),
        )


# ─────────────────────────────────────────────
# Supabase runtime_state helpers
# ─────────────────────────────────────────────
#: Table holding runner state (db/migrations/0008_runtime_state.sql). RLS is
#: on with no policies, so only the service-role key can read or write it.
RUNTIME_STATE_TABLE = "runtime_state"
#: Row key of the production APS refresh token in ``public.runtime_state``.
SUPABASE_TOKEN_KEY = "aps_refresh_token"


class RuntimeStateError(RuntimeError):
    """A ``public.runtime_state`` read or write failed. The message is a
    one-line diagnostic with any stored values redacted."""


def _runtime_state_where(key: str) -> str:
    return f"public.{RUNTIME_STATE_TABLE}[{key}]"


def _diagnostic(exc: Exception, *secrets: str | None) -> str:
    """``Type: message`` on one line, with every given secret redacted.

    PostgREST error bodies can echo the failing row (``Failing row contains
    (…)``), so a write error may carry the very token being written.
    """
    return _tidy(f"{type(exc).__name__}: {exc}", *secrets)


def supabase_get_runtime_state(client: Any, key: str) -> str | None:
    """
    Read ``public.runtime_state.value`` for ``key``. Returns ``None`` when
    the row is missing or its value is empty. Any client or HTTP error
    raises ``RuntimeStateError``.
    """
    try:
        rows = client.select(
            RUNTIME_STATE_TABLE, columns="value",
            filters={"key": f"eq.{key}"}, limit=1,
        )
    except Exception as e:  # SupabaseHTTPError, requests errors, bad JSON …
        raise RuntimeStateError(_diagnostic(e)) from None
    if not rows:
        return None
    value = rows[0].get("value") if isinstance(rows[0], dict) else None
    value = str(value).strip() if value else ""
    return value or None


def supabase_set_runtime_state(
    client: Any,
    key: str,
    value: str,
    *,
    previous_value: str | None = None,
    updated_by: str = "datum-aps-consent",
) -> None:
    """
    Upsert one ``public.runtime_state`` row:
    ``{key, value, previous_value, updated_by, updated_at=now (UTC ISO)}``.

    The shared write path for ``SupabaseTokenStore`` (``updated_by=
    "datum-sync"``) and ``datum-aps-consent``. ``previous_value`` is what
    the row held before this write, kept for manual recovery. The upsert asks
    for ``return=minimal`` so the token is not echoed back in the response.
    Any failure raises ``RuntimeStateError`` with both values redacted.
    """
    if not value:
        raise RuntimeStateError(
            f"refusing to write an empty value to {_runtime_state_where(key)}"
        )
    row = {
        "key": key,
        "value": value,
        "previous_value": previous_value or None,
        "updated_by": updated_by,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client.upsert(RUNTIME_STATE_TABLE, row, on_conflict="key", returning="minimal")
    except Exception as e:  # SupabaseHTTPError, requests errors …
        raise RuntimeStateError(_diagnostic(e, value, previous_value)) from None


class SupabaseTokenStore(BaseTokenStore):
    """
    Production token store: the APS refresh token lives in the
    ``public.runtime_state`` row ``key`` of the datum Supabase project.

    APS refresh tokens are single-use and rotate on every refresh, so the
    only safe home for the production token is one the runner rewrites every
    time it refreshes. The runner already holds the service-role key, so the
    row costs no extra credential.

    - On construction, reads the row. Missing or empty → ``APSAuthError``
      naming the table, the key and the ``datum-aps-consent`` remedy. Any
      client/HTTP error → ``APSAuthError("could not read …")``. No access
      token is persisted, so ``is_valid`` starts False and the first
      ``APSClient._ensure_token()`` refreshes.
    - ``update(data)`` sets the in-memory fields, then upserts the new
      refresh token (``previous_value`` = what the row held, ``updated_by``
      ``"datum-sync"``) **before returning**. Any failure raises
      ``APSAuthError("refresh token write-back failed: …")`` and invalidates
      the in-memory access token, so no API call can run on a token chain
      the row does not know about.
    - ``clear()`` is a no-op with a warning: code never deletes the
      production token.

    The token is never logged; only the key and the token's length are.

    Parameters
    ----------
    key : str
        ``runtime_state.key`` of the row holding the refresh token.
    client : SupabaseClient | None
        Client to use. ``None`` builds one from ``SUPABASE_URL`` +
        ``SUPABASE_SERVICE_ROLE_KEY``; if either is missing,
        ``APSConfigError``.
    """

    def __init__(self, key: str = SUPABASE_TOKEN_KEY, client: Any | None = None):
        super().__init__()
        self.key = key
        if client is None:
            try:
                client = SupabaseClient()
            except SupabaseConfigError as e:
                raise APSConfigError(
                    f"SupabaseTokenStore (APS_TOKEN_STORE=supabase): {e}"
                ) from None
        self._client = client
        # What the row currently holds, as far as this process knows. Becomes
        # previous_value on the next write-back; advanced only on success.
        self._stored: str | None = None
        self._load()

    def _where(self) -> str:
        return _runtime_state_where(self.key)

    def _load(self) -> None:
        try:
            value = supabase_get_runtime_state(self._client, self.key)
        except RuntimeStateError as e:
            raise APSAuthError(f"could not read {self._where()}: {e}") from None
        if not value:
            raise APSAuthError(
                f"public.{RUNTIME_STATE_TABLE} row {self.key!r} is missing or "
                "empty — seed it with datum-aps-consent."
            )
        self.refresh_token = value
        self._stored = value
        log.info("APS refresh token loaded from %s (len %d)", self._where(), len(value))

    def _persist(self) -> None:
        # Write-before-use: BaseTokenStore.update() calls this before it
        # returns, and APSClient.refresh_access_token() calls update() before
        # any API call uses the new access token. Autodesk has already
        # consumed the old refresh token, so the new one exists only in
        # memory until this upsert lands. Writing it first means a crash at
        # any later point (mid-traversal, OOM, cancelled job) still leaves the
        # row holding the newest valid token; and if the write fails we stop
        # here, before the new token chain is used, with a log line that says
        # exactly that.
        try:
            if not self.refresh_token:
                raise RuntimeStateError(
                    "Autodesk token response carried no refresh_token"
                )
            supabase_set_runtime_state(
                self._client, self.key, self.refresh_token,
                previous_value=self._stored, updated_by="datum-sync",
            )
        except RuntimeStateError as e:
            # Keep refresh_token in memory (it may be the only live copy) but
            # make the access token unusable until a write-back succeeds.
            self.access_token = None
            self.expires_at = 0.0
            raise APSAuthError(f"refresh token write-back failed: {e}") from None
        self._stored = self.refresh_token
        log.info(
            "APS refresh token written back to %s (len %d)",
            self._where(), len(self.refresh_token),
        )

    def clear(self) -> None:
        log.warning(
            "SupabaseTokenStore.clear() is a no-op: the production APS "
            "refresh token in %s is never deleted from code.", self._where(),
        )


_TOKEN_STORE_CHOICES = "'file' (default), 'supabase' or 'infisical'"


def select_token_store(
    token_path: Path | None | str = _DEFAULT_TOKEN_PATH,
) -> BaseTokenStore:
    """
    Build the token store named by ``APS_TOKEN_STORE``:

    - unset / ``file`` → ``FileTokenStore(path=token_path)``
    - ``supabase`` → ``SupabaseTokenStore()`` (production)
    - ``infisical`` → ``InfisicalTokenStore()``
    - anything else → ``APSConfigError``
    """
    raw = os.environ.get(TOKEN_STORE_ENV, "")
    kind = raw.strip().lower() or "file"
    if kind == "file":
        return FileTokenStore(path=Path(token_path) if token_path else None)
    if kind == "supabase":
        return SupabaseTokenStore()
    if kind == "infisical":
        return InfisicalTokenStore()
    raise APSConfigError(
        f"{TOKEN_STORE_ENV}={raw!r} is not valid; use {_TOKEN_STORE_CHOICES}."
    )


# ─────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────
class APSClient:
    """
    Autodesk Platform Services client for OAuth and Data Management API.

    Parameters
    ----------
    client_id : str | None
        APS app client ID. Defaults to ``APS_CLIENT_ID`` env var.
    client_secret : str | None
        APS app client secret. Defaults to ``APS_CLIENT_SECRET`` env var.
    callback_url : str | None
        OAuth redirect URI. Defaults to ``APS_CALLBACK_URL`` env var.
    timeout : int
        Per-request timeout in seconds.
    token_path : Path | None | str
        File to persist tokens to. Pass ``None`` to disable persistence
        (in-memory only, useful for tests). Defaults to ``.aps_tokens.json``.
        Only used when the file store is selected.
    token_store : BaseTokenStore | None
        Token store instance to use. When ``None`` (the default) the store
        is picked by ``APS_TOKEN_STORE``: ``file`` (default), ``supabase``
        (production) or ``infisical``; any other value raises
        ``APSConfigError``. See ``select_token_store``.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        callback_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        token_path: Path | None | str = _DEFAULT_TOKEN_PATH,
        token_store: BaseTokenStore | None = None,
    ):
        self.client_id = client_id or APS_CLIENT_ID
        self.client_secret = client_secret or APS_CLIENT_SECRET
        self.callback_url = callback_url or APS_CALLBACK_URL
        self.timeout = timeout
        self.tokens: BaseTokenStore = (
            token_store if token_store is not None
            else select_token_store(token_path)
        )
        self._session = requests.Session()

    # ─────────────────────────────────────────
    # Config validation
    # ─────────────────────────────────────────
    def _require_config(self) -> None:
        if not self.client_id:
            raise APSConfigError(
                "APS_CLIENT_ID is not set. Register an app at "
                "https://aps.autodesk.com and add the client ID to .env.local."
            )
        if not self.client_secret:
            raise APSConfigError(
                "APS_CLIENT_SECRET is not set. Add it to .env.local."
            )

    def _require_token(self) -> None:
        if not self.tokens.is_valid:
            raise APSAuthError(
                "No valid APS access token. Complete the OAuth flow first "
                "by visiting /api/aps/login in your browser."
            )

    # ─────────────────────────────────────────
    # OAuth 2.0 — 3-legged flow
    # ─────────────────────────────────────────
    def get_authorize_url(self, scopes: str = DEFAULT_SCOPES) -> str:
        """
        Build the Autodesk authorization URL. Redirect the user's browser here.
        After consent, Autodesk redirects back to ``callback_url`` with a code.
        """
        self._require_config()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.callback_url,
            "scope": scopes,
        }
        return f"{APS_AUTH_BASE}/authorize?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        """
        Exchange an authorization code for access + refresh tokens.
        Called from the OAuth callback handler.
        """
        self._require_config()
        resp = self._session.post(
            f"{APS_AUTH_BASE}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.callback_url,
            },
            timeout=self.timeout,
        )
        if not resp.ok:
            raise APSAuthError(
                f"Token exchange failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        self.tokens.update(data)
        log.info("APS OAuth tokens acquired (expires in %ss)", data.get("expires_in"))
        return data

    def refresh_access_token(self) -> dict:
        """Use the refresh token to get a new access token."""
        self._require_config()
        if not self.tokens.refresh_token:
            raise APSAuthError(
                "No refresh token available. Re-authenticate via /api/aps/login."
            )
        resp = self._session.post(
            f"{APS_AUTH_BASE}/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.tokens.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=self.timeout,
        )
        if not resp.ok:
            raise APSAuthError(
                "refresh token rejected — re-seed with datum-aps-consent "
                f"(HTTP {resp.status_code}: {resp.text[:300]})"
            )
        data = resp.json()
        # Persist BEFORE anything uses the new access token. APS refresh
        # tokens are single-use: Autodesk has already consumed the old one.
        # With InfisicalTokenStore, update() writes the new refresh token
        # back to Infisical and raises if that fails, so no API call runs on
        # a token chain Infisical does not hold. If the process dies after a
        # successful write-back, Infisical still holds the newest valid
        # token and the next run picks up from there.
        self.tokens.update(data)
        log.info("APS tokens refreshed")
        return data

    def _ensure_token(self) -> None:
        """Auto-refresh if the current token is expired but we have a refresh token."""
        if self.tokens.is_valid:
            return
        if self.tokens.refresh_token:
            self.refresh_access_token()
            return
        raise APSAuthError(
            "APS token expired and no refresh token available. "
            "Re-authenticate via /api/aps/login."
        )

    # ─────────────────────────────────────────
    # Authenticated API calls
    # ─────────────────────────────────────────
    def _authed_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tokens.access_token}",
            "Content-Type": "application/json",
        }

    def _get(self, url: str, params: dict | None = None) -> Any:
        """Authenticated GET. Returns parsed JSON."""
        self._ensure_token()
        resp = self._session.get(
            url,
            headers=self._authed_headers(),
            params=params,
            timeout=self.timeout,
        )
        if not resp.ok:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise APSHTTPError(resp.status_code, body, resp.url)
        return resp.json()

    def _get_binary(self, url: str) -> bytes:
        """Authenticated GET returning raw bytes (for file downloads)."""
        self._ensure_token()
        resp = self._session.get(
            url,
            headers={"Authorization": f"Bearer {self.tokens.access_token}"},
            timeout=self.timeout,
        )
        if not resp.ok:
            raise APSHTTPError(resp.status_code, resp.text, resp.url)
        return resp.content

    # ─────────────────────────────────────────
    # Data Management API — hub traversal
    # ─────────────────────────────────────────
    def get_hubs(self) -> list[dict]:
        """List all hubs the authenticated user can access."""
        data = self._get(f"{APS_DM_BASE}/project/v1/hubs")
        return data.get("data", [])

    def get_projects(self, hub_id: str) -> list[dict]:
        """List projects within a hub."""
        data = self._get(f"{APS_DM_BASE}/project/v1/hubs/{hub_id}/projects")
        return data.get("data", [])

    def get_top_folders(self, hub_id: str, project_id: str) -> list[dict]:
        """List top-level folders in a project."""
        data = self._get(
            f"{APS_DM_BASE}/project/v1/hubs/{hub_id}/projects/{project_id}/topFolders"
        )
        return data.get("data", [])

    def get_folder_contents(
        self, project_id: str, folder_id: str
    ) -> list[dict]:
        """List items in a folder."""
        data = self._get(
            f"{APS_DM_BASE}/data/v1/projects/{project_id}/folders/{folder_id}/contents"
        )
        return data.get("data", [])

    def search_folder(
        self, project_id: str, folder_id: str, filter_name: str = ""
    ) -> list[dict]:
        """
        Search within a folder. Useful for finding .tools files by name.
        """
        params = {}
        if filter_name:
            params["filter[displayName]"] = filter_name
        data = self._get(
            f"{APS_DM_BASE}/data/v1/projects/{project_id}/folders/{folder_id}/search",
            params=params,
        )
        return data.get("data", [])

    def get_item_versions(self, project_id: str, item_id: str) -> list[dict]:
        """List versions of an item (to get download links)."""
        data = self._get(
            f"{APS_DM_BASE}/data/v1/projects/{project_id}/items/{item_id}/versions"
        )
        return data.get("data", [])

    def get_item_tip(self, project_id: str, item_id: str) -> dict:
        """Get the latest version (tip) of an item."""
        data = self._get(
            f"{APS_DM_BASE}/data/v1/projects/{project_id}/items/{item_id}/tip"
        )
        return data.get("data", {})

    # ─────────────────────────────────────────
    # File download + parsing
    # ─────────────────────────────────────────
    def download_version(self, storage_url: str) -> bytes:
        """
        Download a file by its storage info.

        Accepts either:
        - An ``urn:adsk.objects:os.object:BUCKET/OBJECT`` URN
          (from ``relationships.storage.data.id``)
        - A legacy ``/oss/v2/buckets/...`` URL
          (from ``relationships.storage.meta.link.href``)

        Uses the signed S3 download endpoint (the old direct OSS v2
        GET is deprecated and returns 403).
        """
        # Parse bucket and object key from URN or URL
        bucket, obj_key = self._parse_storage_id(storage_url)
        if not bucket or not obj_key:
            raise APSHTTPError(
                400,
                f"Cannot parse storage reference: {storage_url}",
                storage_url,
            )

        # Get a signed S3 download URL
        sign_resp = self._get(
            f"{APS_DM_BASE}/oss/v2/buckets/{bucket}/objects/{obj_key}/signeds3download"
        )
        signed_url = sign_resp.get("url")
        if not signed_url:
            raise APSHTTPError(
                500,
                f"No signed URL returned for {bucket}/{obj_key}",
                storage_url,
            )

        # Download from S3 (no auth header needed — the URL is pre-signed)
        resp = self._session.get(signed_url, timeout=self.timeout)
        if not resp.ok:
            raise APSHTTPError(resp.status_code, resp.text, signed_url)
        return resp.content

    @staticmethod
    def _parse_storage_id(ref: str) -> tuple[str, str]:
        """
        Extract (bucket, object_key) from a storage URN or URL.

        URN format: ``urn:adsk.objects:os.object:wip.dm.prod/abc-123.json``
        URL format: ``.../oss/v2/buckets/wip.dm.prod/objects/abc-123.json?...``
        """
        # URN form
        if ref.startswith("urn:adsk.objects:os.object:"):
            path = ref.split("urn:adsk.objects:os.object:")[-1]
            parts = path.split("/", 1)
            if len(parts) == 2:
                return parts[0], parts[1]

        # URL form
        if "/oss/v2/buckets/" in ref:
            # .../oss/v2/buckets/{bucket}/objects/{object}?...
            segment = ref.split("/oss/v2/buckets/")[-1]
            segment = segment.split("?")[0]  # strip query params
            parts = segment.split("/objects/", 1)
            if len(parts) == 2:
                return parts[0], parts[1]

        return "", ""

    def download_tool_library(self, storage_url: str) -> list[dict]:
        """
        Download a .tools file and extract the JSON tool data.

        .tools files are ZIP archives containing a single JSON file
        with the same schema as local Fusion tool library exports:
        ``{"data": [<tool objects>], ...}``

        Returns the list of tool dicts (the "data" array), matching
        the return type of ``tool_library_loader.load_library()``.
        """
        raw_bytes = self.download_version(storage_url)

        # Try ZIP first (.tools files are typically zipped)
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                names = zf.namelist()
                # Find the JSON file inside
                json_name = next(
                    (n for n in names if n.endswith(".json")), names[0]
                )
                with zf.open(json_name) as jf:
                    parsed = json.load(jf)
        except zipfile.BadZipFile:
            # Not a ZIP — might be raw JSON (some exports)
            parsed = json.loads(raw_bytes)

        # Extract the "data" array
        if isinstance(parsed, dict) and "data" in parsed:
            tools = parsed["data"]
        elif isinstance(parsed, list):
            tools = parsed
        else:
            log.warning(
                "Unexpected tool library structure from %s — no 'data' key",
                storage_url,
            )
            return []

        if not isinstance(tools, list):
            return []

        log.info(
            "Downloaded tool library from APS: %d entries", len(tools)
        )
        return tools

    # ─────────────────────────────────────────
    # High-level: find tool libraries in hub
    # ─────────────────────────────────────────
    def find_tool_libraries(
        self, hub_id: str | None = None
    ) -> list[dict]:
        """
        Walk the hub to find .tools files. Returns a list of dicts:

            [{"name": "MyLibrary.tools",
              "item_id": "urn:...",
              "project_id": "...",
              "hub_id": "...",
              "storage_url": "https://..."}, ...]

        If ``hub_id`` is None, searches all accessible hubs.
        """
        results = []
        hubs = [{"id": hub_id}] if hub_id else self.get_hubs()

        for hub in hubs:
            hid = hub["id"]
            projects = self.get_projects(hid)

            for project in projects:
                pid = project["id"]
                try:
                    top_folders = self.get_top_folders(hid, pid)
                except APSHTTPError:
                    log.debug("Skipping project %s — can't list folders", pid)
                    continue

                for folder in top_folders:
                    fid = folder["id"]
                    self._scan_folder_for_tools(
                        hid, pid, fid, results, depth=0
                    )

        return results

    def _scan_folder_for_tools(
        self,
        hub_id: str,
        project_id: str,
        folder_id: str,
        results: list[dict],
        depth: int = 0,
        max_depth: int = 5,
    ) -> None:
        """Recursively scan folders for .tools files."""
        if depth > max_depth:
            return

        try:
            contents = self.get_folder_contents(project_id, folder_id)
        except APSHTTPError:
            return

        for item in contents:
            item_type = item.get("type", "")
            name = item.get("attributes", {}).get("displayName", "")

            if item_type == "folders":
                # Recurse into subfolders
                self._scan_folder_for_tools(
                    hub_id, project_id, item["id"], results, depth + 1
                )

            elif item_type == "items" and (
                name.endswith(".tools") or name.endswith(".json")
            ):
                # Found a tool library file — get its download URL
                try:
                    tip = self.get_item_tip(project_id, item["id"])
                    storage = (
                        tip.get("relationships", {})
                        .get("storage", {})
                        .get("meta", {})
                        .get("link", {})
                        .get("href", "")
                    )
                    results.append({
                        "name": name,
                        "item_id": item["id"],
                        "project_id": project_id,
                        "hub_id": hub_id,
                        "storage_url": storage,
                    })
                except APSHTTPError as e:
                    log.warning("Could not get tip for %s: %s", name, e)
