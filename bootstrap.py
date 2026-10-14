"""
bootstrap.py
Three-source credential loader
==============================
Imported at the very top of plex_api.py, sync.py, aps_client.py,
supabase_client.py and friends so that credentials are in os.environ
before anything reads them.

Precedence (highest first), all via ``os.environ`` setdefault semantics:

    1. shell     — anything already in the real environment ALWAYS wins
    2. infisical — ``infisical export`` of project ``datum`` (machine identity)
    3. .env.local — gitignored dotenv file, the local fallback

On import, ``load_infisical()`` runs first and ``load_env_local()`` second,
so a key present in both Infisical and .env.local gets the Infisical value.

Infisical (``load_infisical``)
------------------------------
- Runs only when ``DATUM_DISABLE_INFISICAL`` is unset AND a machine identity
  is available: the identity file ``~/.config/datum/infisical-identity.json``
  (``{"clientId": ..., "clientSecret": ..., "projectId": ...}``, path
  overridable via ``DATUM_INFISICAL_IDENTITY_FILE``) exists, or both
  ``INFISICAL_UNIVERSAL_AUTH_CLIENT_ID`` and
  ``INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET`` are in the environment.
  Otherwise it is a silent no-op, like a missing .env.local.
- Project id: ``DATUM_INFISICAL_PROJECT_ID``, else the identity file's
  ``projectId``. Environment: ``DATUM_INFISICAL_ENV`` (default ``dev``).
- Mints a short-lived token with ``infisical login --method=universal-auth``
  and reads the secrets with ``infisical export --format=json``. Every
  subprocess runs with stdin on DEVNULL and stdout captured — an Infisical
  CLI that falls back to an interactive prompt must never reach the
  terminal or eat this process's stdin (grace-cam, 2026-09-12).
- Any failure (CLI missing, timeout, non-zero exit, unparseable output)
  logs exactly one WARNING and returns 0. Importing this module never
  raises because of Infisical and never writes to stdout.

.env.local (``load_env_local``)
-------------------------------
- Walks up from the project root to the nearest .env.local, parses
  KEY=VALUE pairs and injects them via setdefault.
- Lines starting with # are comments. Blank lines are ignored.
- Surrounding single or double quotes on values are stripped.
- Missing file is a no-op (no error).

Where did this value come from? (``describe_sources``)
------------------------------------------------------
Both loaders record which source set each variable in ``_SOURCES``;
anything already present before they ran is reported as ``shell``.
``describe_sources()`` / ``format_sources()`` report, per credential, the
source, the first 8 characters and the length — never more than 8
characters of any value. This is the lesson of docs/BRIEFING.md
"History of incorrect hypotheses §4": a stale shell ``PLEX_API_KEY``
silently shadowed .env.local for 45 minutes because nothing said which
source won.

Why setdefault, not direct assignment
-------------------------------------
A real shell environment variable should always override the loaders —
that lets CI, production deployments, and ad-hoc shell exports take
precedence over local dev defaults without anyone having to remember
to delete a file.
"""
import json
import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Project root = directory containing this file (bootstrap.py lives at the root)
_PROJECT_ROOT = Path(__file__).resolve().parent

# Which loader set each variable: "infisical" or ".env.local". A variable in
# os.environ with no entry here was already present (reported as "shell").
_SOURCES: dict[str, str] = {}

SOURCE_SHELL = "shell"
SOURCE_INFISICAL = "infisical"
SOURCE_ENV_LOCAL = ".env.local"
SOURCE_UNSET = "unset"

DEFAULT_CREDENTIAL_NAMES = (
    "PLEX_API_KEY",
    "PLEX_API_SECRET",
    "SUPABASE_SERVICE_ROLE_KEY",
    "APS_CLIENT_SECRET",
    "APS_REFRESH_TOKEN",
)

_DEFAULT_IDENTITY_FILE = Path.home() / ".config" / "datum" / "infisical-identity.json"
_DEFAULT_INFISICAL_ENV = "dev"
_INFISICAL_TIMEOUT = 60
_DIAGNOSTIC_MAX = 240
_PREFIX_LEN = 8

# The Infisical CLI colours its log lines; the escapes are noise in a log.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _find_env_local() -> Path | None:
    """
    Walk up from this file's directory until we find a ``.env.local``,
    or give up at the filesystem root.

    This lets worktrees (e.g. ``.claude/worktrees/foo``) inherit the
    ``.env.local`` from the main repo root without needing their own copy.
    """
    current = _PROJECT_ROOT
    while True:
        candidate = current / ".env.local"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:  # reached filesystem root
            return None
        current = parent


def load_env_local(path: Path | str | None = None) -> int:
    """
    Load KEY=VALUE pairs from a .env.local file into os.environ via setdefault.

    Parameters
    ----------
    path : Path | str | None
        Override the file path. When ``None``, walks up the directory tree
        from the project root to find the nearest ``.env.local``. An
        explicit path always wins over the walk-up search.

    Returns
    -------
    int
        Number of variables actually injected into os.environ
        (i.e. that were not already present).
    """
    if path is None:
        found = _find_env_local()
        if found is None:
            return 0
        path = found
    else:
        path = Path(path)

    if not path.exists():
        return 0

    injected = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Strip matched surrounding quotes (' or ")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value
            _SOURCES[key] = SOURCE_ENV_LOCAL
            injected += 1

    return injected


# ─────────────────────────────────────────────
# Infisical
# ─────────────────────────────────────────────
class _InfisicalError(Exception):
    """A load_infisical() failure; its message is a safe one-line diagnostic."""


def _tidy(text: str) -> str:
    """ANSI-stripped, whitespace-collapsed, length-capped single line."""
    text = _ANSI.sub("", text or "")
    return " ".join(text.split())[:_DIAGNOSTIC_MAX]


def _identity_file() -> Path:
    override = os.environ.get("DATUM_INFISICAL_IDENTITY_FILE")
    return Path(override) if override else _DEFAULT_IDENTITY_FILE


def _run_infisical(args: list[str], env: dict[str, str], what: str):
    """Run one Infisical CLI command with stdin closed and output captured.

    ``encoding``/``errors`` are explicit because the default locale codec
    (cp1252 on Windows) cannot decode the CLI's colour escapes, which would
    lose the diagnostics on exactly the failure paths that need them.
    """
    try:
        return subprocess.run(
            args, env=env, timeout=_INFISICAL_TIMEOUT,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, capture_output=True,
        )
    except FileNotFoundError:
        raise _InfisicalError("infisical CLI not on PATH") from None
    except subprocess.TimeoutExpired:
        # Never include the exception text: its str() echoes argv (--token=…).
        raise _InfisicalError(
            f"infisical {what} timed out after {_INFISICAL_TIMEOUT}s") from None


def _parse_export(stdout: str) -> dict[str, str]:
    try:
        raw = json.loads(stdout)
    except ValueError:
        raise _InfisicalError("infisical export returned unparseable output") from None
    if isinstance(raw, list):
        try:
            return {str(item["key"]): str(item["value"]) for item in raw}
        except (KeyError, TypeError):
            raise _InfisicalError(
                "infisical export list entries lack key/value fields") from None
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    raise _InfisicalError(f"unexpected infisical export shape: {type(raw).__name__}")


def _fetch_infisical() -> tuple[dict[str, str], str] | None:
    """Return (secrets, environment), or None when Infisical is not configured.

    Raises _InfisicalError on any failure once it is configured.
    """
    if "DATUM_DISABLE_INFISICAL" in os.environ:
        return None

    env_id = os.environ.get("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID")
    env_secret = os.environ.get("INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET")
    env_creds = bool(env_id and env_secret)
    identity_path = _identity_file()
    has_file = identity_path.is_file()
    if not has_file and not env_creds:
        return None

    identity: dict = {}
    if has_file:
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise _InfisicalError(
                f"identity file {identity_path} is unreadable or not JSON") from None
        if not isinstance(identity, dict):
            raise _InfisicalError(f"identity file {identity_path} is not a JSON object")

    if env_creds:
        client_id, client_secret = env_id, env_secret
    else:
        client_id = identity.get("clientId")
        client_secret = identity.get("clientSecret")
        if not client_id or not client_secret:
            raise _InfisicalError(
                f"identity file {identity_path} lacks clientId/clientSecret")

    project_id = os.environ.get("DATUM_INFISICAL_PROJECT_ID") or identity.get("projectId")
    if not project_id:
        raise _InfisicalError(
            "no project id: set DATUM_INFISICAL_PROJECT_ID or add projectId "
            "to the identity file")
    environment = os.environ.get("DATUM_INFISICAL_ENV") or _DEFAULT_INFISICAL_ENV

    login_env = dict(os.environ)
    login_env["INFISICAL_UNIVERSAL_AUTH_CLIENT_ID"] = client_id
    login_env["INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET"] = client_secret
    res = _run_infisical(
        ["infisical", "login", "--method=universal-auth", "--plain", "--silent"],
        login_env, "login")
    token = (res.stdout or "").strip()
    if res.returncode != 0 or not token:
        raise _InfisicalError(
            f"universal-auth login failed (rc={res.returncode}): {_tidy(res.stderr)}")

    res = _run_infisical(
        ["infisical", "export", "--format=json", "--silent",
         f"--projectId={project_id}", f"--env={environment}", f"--token={token}"],
        dict(os.environ), "export")
    if res.returncode != 0:
        raise _InfisicalError(
            f"export failed (rc={res.returncode}): {_tidy(res.stderr)}")
    return _parse_export(res.stdout), environment


def load_infisical() -> int:
    """
    Load secrets from Infisical into os.environ via setdefault.

    A silent no-op (returns 0) when ``DATUM_DISABLE_INFISICAL`` is set or no
    machine identity is configured. Any failure once configured logs exactly
    one WARNING and returns 0; this function never raises.

    Returns
    -------
    int
        Number of variables actually injected into os.environ
        (i.e. that were not already present).
    """
    try:
        fetched = _fetch_infisical()
        if fetched is None:
            return 0
        secrets, environment = fetched
        injected = 0
        for key, value in secrets.items():
            if key and key not in os.environ:
                os.environ[key] = value
                _SOURCES[key] = SOURCE_INFISICAL
                injected += 1
        log.info("bootstrap: %d variable(s) loaded from Infisical (env=%s)",
                 injected, environment)
        return injected
    except _InfisicalError as exc:
        reason = str(exc)
    except Exception as exc:  # never let import fail because of Infisical
        reason = f"unexpected {type(exc).__name__}"
    log.warning("%s", _tidy(f"bootstrap: Infisical secrets not loaded: {reason}"))
    return 0


# ─────────────────────────────────────────────
# Source reporting
# ─────────────────────────────────────────────
def describe_sources(names=None) -> list[tuple[str, str, str, int]]:
    """
    Report where each credential came from without revealing it.

    Parameters
    ----------
    names : iterable of str, optional
        Variable names to report. Defaults to ``DEFAULT_CREDENTIAL_NAMES``.

    Returns
    -------
    list of (name, source, prefix8, length)
        ``source`` is one of ``shell``, ``infisical``, ``.env.local``,
        ``unset``. ``prefix8`` is at most the first 8 characters of the value
        (empty when unset); ``length`` is the full value length (0 when unset).
    """
    if names is None:
        names = DEFAULT_CREDENTIAL_NAMES
    rows = []
    for name in names:
        value = os.environ.get(name)
        if value is None:
            rows.append((name, SOURCE_UNSET, "", 0))
        else:
            source = _SOURCES.get(name, SOURCE_SHELL)
            rows.append((name, source, value[:_PREFIX_LEN], len(value)))
    return rows


def format_sources(names=None) -> str:
    """One line per credential (see ``describe_sources``), for ``log.info``."""
    lines = []
    for name, source, prefix, length in describe_sources(names):
        if source == SOURCE_UNSET:
            lines.append(f"{name}: source=unset")
        else:
            lines.append(f"{name}: source={source} prefix={prefix} len={length}")
    return "\n".join(lines)


def _autoload() -> None:
    """Import-time load: Infisical first, then .env.local (both setdefault)."""
    load_infisical()
    load_env_local()


# Auto-load on import — each loader is a no-op when its source is absent.
_autoload()
