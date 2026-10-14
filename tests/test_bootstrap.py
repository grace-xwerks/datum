"""
Tests for bootstrap.py — three-source credential loader.

Verifies the .env.local contract:
  - missing file is a no-op
  - KEY=VALUE pairs are parsed and injected via setdefault
  - existing env vars are NEVER overridden
  - blank lines and # comments are skipped
  - matched surrounding quotes (single or double) are stripped
  - returns the count of injected variables
  - walk-up finds .env.local in parent/grandparent directories
  - explicit path= arg wins over walk-up

and the Infisical / source-reporting contract
(docs/superpowers/specs/2026-10-14-infisical-and-actions-sync-design.md):
  - precedence shell > Infisical > .env.local
  - load_infisical() is a silent no-op when disabled or unconfigured
  - CLI missing / timeout / non-zero exit / bad JSON → one warning, return 0
  - list and dict export shapes
  - describe_sources()/format_sources() never expose more than 8 chars

subprocess.run is ALWAYS patched for the Infisical tests; the real CLI is
never invoked and the real ~/.config/datum identity file is never read.
"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import bootstrap
from bootstrap import load_env_local, _find_env_local


# ─────────────────────────────────────────────
# Missing file behavior
# ─────────────────────────────────────────────
class TestMissingFile:
    def test_missing_file_is_noop(self, tmp_path):
        missing = tmp_path / "does-not-exist.env"
        result = load_env_local(missing)
        assert result == 0

    def test_missing_file_does_not_raise(self, tmp_path):
        # Should not raise even if directory itself does not exist
        nowhere = tmp_path / "nope" / "alsonope" / ".env.local"
        load_env_local(nowhere)  # no exception


# ─────────────────────────────────────────────
# Basic parsing
# ─────────────────────────────────────────────
class TestBasicParsing:
    def test_simple_key_value(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("FOO=bar\n")
        monkeypatch.delenv("FOO", raising=False)
        injected = load_env_local(f)
        assert injected == 1
        assert os.environ["FOO"] == "bar"

    def test_multiple_pairs(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("FOO=bar\nBAZ=qux\nHELLO=world\n")
        for k in ("FOO", "BAZ", "HELLO"):
            monkeypatch.delenv(k, raising=False)
        injected = load_env_local(f)
        assert injected == 3
        assert os.environ["FOO"] == "bar"
        assert os.environ["BAZ"] == "qux"
        assert os.environ["HELLO"] == "world"

    def test_value_can_contain_equals(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("URL=https://example.com/?a=1&b=2\n")
        monkeypatch.delenv("URL", raising=False)
        load_env_local(f)
        assert os.environ["URL"] == "https://example.com/?a=1&b=2"


# ─────────────────────────────────────────────
# Comments and blank lines
# ─────────────────────────────────────────────
class TestCommentsAndBlanks:
    def test_skips_comments(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("# this is a comment\nFOO=bar\n# another comment\n")
        monkeypatch.delenv("FOO", raising=False)
        injected = load_env_local(f)
        assert injected == 1
        assert os.environ["FOO"] == "bar"

    def test_skips_blank_lines(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("\n\nFOO=bar\n\n\nBAZ=qux\n")
        for k in ("FOO", "BAZ"):
            monkeypatch.delenv(k, raising=False)
        injected = load_env_local(f)
        assert injected == 2

    def test_skips_lines_without_equals(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("not-a-pair\nFOO=bar\nalso-not-a-pair\n")
        monkeypatch.delenv("FOO", raising=False)
        injected = load_env_local(f)
        assert injected == 1


# ─────────────────────────────────────────────
# Quote stripping
# ─────────────────────────────────────────────
class TestQuoteStripping:
    def test_strips_double_quotes(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text('FOO="bar baz"\n')
        monkeypatch.delenv("FOO", raising=False)
        load_env_local(f)
        assert os.environ["FOO"] == "bar baz"

    def test_strips_single_quotes(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("FOO='bar baz'\n")
        monkeypatch.delenv("FOO", raising=False)
        load_env_local(f)
        assert os.environ["FOO"] == "bar baz"

    def test_does_not_strip_mismatched_quotes(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("FOO=\"bar'\n")
        monkeypatch.delenv("FOO", raising=False)
        load_env_local(f)
        assert os.environ["FOO"] == "\"bar'"

    def test_preserves_internal_quotes(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text('FOO=bar"baz\n')
        monkeypatch.delenv("FOO", raising=False)
        load_env_local(f)
        assert os.environ["FOO"] == 'bar"baz'


# ─────────────────────────────────────────────
# setdefault semantics — real env always wins
# ─────────────────────────────────────────────
class TestSetdefaultBehavior:
    def test_existing_env_var_is_not_overridden(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("FOO=from-file\n")
        monkeypatch.setenv("FOO", "from-shell")
        injected = load_env_local(f)
        # Was already set, so injected count is 0
        assert injected == 0
        assert os.environ["FOO"] == "from-shell"

    def test_partial_override_only_sets_missing(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("FOO=from-file\nBAZ=from-file\n")
        monkeypatch.setenv("FOO", "from-shell")
        monkeypatch.delenv("BAZ", raising=False)
        injected = load_env_local(f)
        assert injected == 1
        assert os.environ["FOO"] == "from-shell"
        assert os.environ["BAZ"] == "from-file"


# ─────────────────────────────────────────────
# Whitespace handling
# ─────────────────────────────────────────────
class TestWhitespace:
    def test_strips_whitespace_around_key_and_value(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("  FOO  =  bar  \n")
        monkeypatch.delenv("FOO", raising=False)
        load_env_local(f)
        assert os.environ["FOO"] == "bar"

    def test_handles_crlf_line_endings(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_bytes(b"FOO=bar\r\nBAZ=qux\r\n")
        monkeypatch.delenv("FOO", raising=False)
        monkeypatch.delenv("BAZ", raising=False)
        injected = load_env_local(f)
        assert injected == 2
        assert os.environ["FOO"] == "bar"
        assert os.environ["BAZ"] == "qux"


# -------------------------------------------------
# Walk-up directory search for .env.local
# -------------------------------------------------
class TestWalkUp:
    def test_finds_env_local_in_parent(self, tmp_path):
        """Simulates a worktree at tmp/child/ with .env.local at tmp/."""
        (tmp_path / ".env.local").write_text("X=1\n")
        child = tmp_path / "child"
        child.mkdir()

        with patch("bootstrap._PROJECT_ROOT", child):
            found = _find_env_local()
        assert found == tmp_path / ".env.local"

    def test_finds_env_local_in_grandparent(self, tmp_path):
        """Simulates .claude/worktrees/foo with .env.local two levels up."""
        (tmp_path / ".env.local").write_text("X=1\n")
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)

        with patch("bootstrap._PROJECT_ROOT", deep):
            found = _find_env_local()
        assert found == tmp_path / ".env.local"

    def test_returns_none_when_nothing_in_chain(self, tmp_path):
        """No .env.local anywhere — should return None, not raise."""
        empty = tmp_path / "nowhere"
        empty.mkdir()

        with patch("bootstrap._PROJECT_ROOT", empty):
            found = _find_env_local()
        assert found is None

    def test_prefers_closest_ancestor(self, tmp_path):
        """If both parent/ and grandparent/ have .env.local, pick closest."""
        (tmp_path / ".env.local").write_text("LEVEL=root\n")
        mid = tmp_path / "mid"
        mid.mkdir()
        (mid / ".env.local").write_text("LEVEL=mid\n")
        child = mid / "child"
        child.mkdir()

        with patch("bootstrap._PROJECT_ROOT", child):
            found = _find_env_local()
        assert found == mid / ".env.local"

    def test_explicit_path_wins_over_walkup(self, tmp_path, monkeypatch):
        """An explicit path= argument bypasses the walk-up entirely."""
        # Put a .env.local in the walk-up chain
        (tmp_path / ".env.local").write_text("FROM_WALKUP=yes\n")
        child = tmp_path / "child"
        child.mkdir()

        # But pass an explicit file with different content
        explicit = tmp_path / "custom.env"
        explicit.write_text("FROM_EXPLICIT=yes\n")

        monkeypatch.delenv("FROM_WALKUP", raising=False)
        monkeypatch.delenv("FROM_EXPLICIT", raising=False)

        with patch("bootstrap._PROJECT_ROOT", child):
            injected = load_env_local(path=explicit)

        assert injected == 1
        assert os.environ.get("FROM_EXPLICIT") == "yes"
        assert "FROM_WALKUP" not in os.environ

    def test_walkup_default_loads_vars(self, tmp_path, monkeypatch):
        """load_env_local() with no args uses walk-up and loads vars."""
        (tmp_path / ".env.local").write_text("WALKUP_TEST=hello\n")
        child = tmp_path / "child"
        child.mkdir()
        monkeypatch.delenv("WALKUP_TEST", raising=False)

        with patch("bootstrap._PROJECT_ROOT", child):
            injected = load_env_local()

        assert injected == 1
        assert os.environ["WALKUP_TEST"] == "hello"


# =================================================
# Infisical loader + source reporting
# =================================================
_INFISICAL_ENV_VARS = (
    "DATUM_DISABLE_INFISICAL",
    "DATUM_INFISICAL_IDENTITY_FILE",
    "DATUM_INFISICAL_PROJECT_ID",
    "DATUM_INFISICAL_ENV",
    "DATUM_INFISICAL_PATH",
    "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID",
    "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET",
)

_TOKEN = "minted-access-token-0123456789"
_LONG_SECRET = "SECRETVALUE-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789"
_DEFAULT_NAMES = ["PLEX_API_KEY", "PLEX_API_SECRET", "SUPABASE_SERVICE_ROLE_KEY",
                  "APS_CLIENT_SECRET", "APS_REFRESH_TOKEN"]


@pytest.fixture(autouse=True)
def _isolated_sources(monkeypatch):
    """Every test in this file gets a fresh source registry."""
    monkeypatch.setattr(bootstrap, "_SOURCES", {})


@pytest.fixture
def infisical_env(tmp_path, monkeypatch):
    """Enable the loader (conftest disables it) with a clean, isolated config.

    Returns the identity-file path (not yet written) so each test decides
    whether an identity exists.
    """
    for var in _INFISICAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    identity = tmp_path / "infisical-identity.json"
    monkeypatch.setenv("DATUM_INFISICAL_IDENTITY_FILE", str(identity))
    return identity


def _write_identity(identity_path, /, **extra):
    data = {"clientId": "cid-123", "clientSecret": "csecret-456"}
    data.update(extra)
    identity_path.write_text(json.dumps(data), encoding="utf-8")


class FakeInfisical:
    """Stand-in for subprocess.run that answers `infisical login`/`export`."""

    def __init__(self, export_stdout="[]", login_rc=0, export_rc=0,
                 login_stdout=_TOKEN, login_stderr="", export_stderr="",
                 login_exc=None, export_exc=None):
        self.export_stdout = export_stdout
        self.login_rc = login_rc
        self.export_rc = export_rc
        self.login_stdout = login_stdout
        self.login_stderr = login_stderr
        self.export_stderr = export_stderr
        self.login_exc = login_exc
        self.export_exc = export_exc
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        verb = args[1]
        if verb == "login":
            if self.login_exc is not None:
                raise self.login_exc
            return subprocess.CompletedProcess(
                args, self.login_rc, self.login_stdout, self.login_stderr)
        if verb == "export":
            if self.export_exc is not None:
                raise self.export_exc
            return subprocess.CompletedProcess(
                args, self.export_rc, self.export_stdout, self.export_stderr)
        raise AssertionError(f"unexpected infisical verb: {args!r}")


def _warnings(caplog):
    return [r for r in caplog.records
            if r.name == "bootstrap" and r.levelno == logging.WARNING]


# ─────────────────────────────────────────────
# Gating: when the loader must do nothing
# ─────────────────────────────────────────────
class TestInfisicalGating:
    def test_disabled_flag_is_noop_even_with_identity(self, infisical_env, monkeypatch, caplog):
        _write_identity(infisical_env, projectId="proj-1")
        monkeypatch.setenv("DATUM_DISABLE_INFISICAL", "1")
        fake = FakeInfisical()
        with patch("bootstrap.subprocess.run", fake), caplog.at_level(logging.DEBUG):
            assert bootstrap.load_infisical() == 0
        assert fake.calls == []
        assert _warnings(caplog) == []

    def test_no_identity_file_and_no_env_creds_is_silent_noop(self, infisical_env, caplog):
        fake = FakeInfisical()
        with patch("bootstrap.subprocess.run", fake), caplog.at_level(logging.DEBUG):
            assert bootstrap.load_infisical() == 0
        assert fake.calls == []
        assert _warnings(caplog) == []

    def test_only_one_env_cred_is_noop(self, infisical_env, monkeypatch, caplog):
        monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("DATUM_INFISICAL_PROJECT_ID", "proj-1")
        fake = FakeInfisical()
        with patch("bootstrap.subprocess.run", fake), caplog.at_level(logging.DEBUG):
            assert bootstrap.load_infisical() == 0
        assert fake.calls == []
        assert _warnings(caplog) == []

    def test_env_creds_without_identity_file_runs(self, infisical_env, monkeypatch):
        monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID", "env-cid")
        monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET", "env-csecret")
        monkeypatch.setenv("DATUM_INFISICAL_PROJECT_ID", "proj-env")
        monkeypatch.delenv("DATUM_TEST_INF_A", raising=False)
        fake = FakeInfisical(export_stdout=json.dumps({"DATUM_TEST_INF_A": "a"}))
        with patch("bootstrap.subprocess.run", fake):
            assert bootstrap.load_infisical() == 1
        _, login_kwargs = fake.calls[0]
        assert login_kwargs["env"]["INFISICAL_UNIVERSAL_AUTH_CLIENT_ID"] == "env-cid"
        assert login_kwargs["env"]["INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET"] == "env-csecret"
        assert "--projectId=proj-env" in fake.calls[1][0]

    def test_missing_project_id_warns_once_and_returns_zero(self, infisical_env, caplog):
        _write_identity(infisical_env)  # no projectId, no env var
        fake = FakeInfisical()
        with patch("bootstrap.subprocess.run", fake), caplog.at_level(logging.DEBUG):
            assert bootstrap.load_infisical() == 0
        assert fake.calls == []
        warns = _warnings(caplog)
        assert len(warns) == 1
        assert "project" in warns[0].getMessage().lower()

    def test_unreadable_identity_file_warns_once(self, infisical_env, caplog):
        infisical_env.write_text("{not json", encoding="utf-8")
        fake = FakeInfisical()
        with patch("bootstrap.subprocess.run", fake), caplog.at_level(logging.DEBUG):
            assert bootstrap.load_infisical() == 0
        assert fake.calls == []
        assert len(_warnings(caplog)) == 1

    def test_identity_file_without_client_secret_warns_once(self, infisical_env, caplog):
        infisical_env.write_text(json.dumps({"clientId": "c", "projectId": "p"}),
                                 encoding="utf-8")
        fake = FakeInfisical()
        with patch("bootstrap.subprocess.run", fake), caplog.at_level(logging.DEBUG):
            assert bootstrap.load_infisical() == 0
        assert fake.calls == []
        assert len(_warnings(caplog)) == 1


# ─────────────────────────────────────────────
# Happy path: CLI invocation contract + export shapes
# ─────────────────────────────────────────────
class TestInfisicalExport:
    def test_cli_invocation_contract(self, infisical_env):
        _write_identity(infisical_env, projectId="proj-file")
        fake = FakeInfisical(export_stdout="[]")
        with patch("bootstrap.subprocess.run", fake):
            bootstrap.load_infisical()

        assert len(fake.calls) == 2
        (login_args, login_kw), (export_args, export_kw) = fake.calls

        assert login_args[:2] == ["infisical", "login"]
        assert "--method=universal-auth" in login_args
        assert "--plain" in login_args and "--silent" in login_args
        assert login_kw["env"]["INFISICAL_UNIVERSAL_AUTH_CLIENT_ID"] == "cid-123"
        assert login_kw["env"]["INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET"] == "csecret-456"
        # The client secret travels in the child env, never in argv.
        assert not any("csecret-456" in a for a in login_args)

        assert export_args[:2] == ["infisical", "export"]
        assert "--format=json" in export_args and "--silent" in export_args
        assert "--projectId=proj-file" in export_args
        assert "--env=dev" in export_args  # default environment
        assert "--path=/datum" in export_args  # default folder
        assert f"--token={_TOKEN}" in export_args

        for kw in (login_kw, export_kw):
            assert kw["stdin"] is subprocess.DEVNULL
            assert kw["capture_output"] is True
            assert kw["encoding"] == "utf-8"
            assert kw["errors"] == "replace"
            assert kw["timeout"] == 60

    def test_env_vars_override_project_and_environment(self, infisical_env, monkeypatch):
        _write_identity(infisical_env, projectId="proj-file")
        monkeypatch.setenv("DATUM_INFISICAL_PROJECT_ID", "proj-env")
        monkeypatch.setenv("DATUM_INFISICAL_ENV", "prod")
        fake = FakeInfisical(export_stdout="[]")
        with patch("bootstrap.subprocess.run", fake):
            bootstrap.load_infisical()
        export_args = fake.calls[1][0]
        assert "--projectId=proj-env" in export_args
        assert "--env=prod" in export_args

    def test_export_scoped_to_default_folder(self, infisical_env):
        _write_identity(infisical_env, projectId="p")
        fake = FakeInfisical(export_stdout="[]")
        with patch("bootstrap.subprocess.run", fake):
            bootstrap.load_infisical()
        export_args = fake.calls[1][0]
        assert [a for a in export_args if a.startswith("--path")] == ["--path=/datum"]

    def test_path_env_var_overrides_default(self, infisical_env, monkeypatch):
        _write_identity(infisical_env, projectId="p", path="/from-file")
        monkeypatch.setenv("DATUM_INFISICAL_PATH", "/other")
        fake = FakeInfisical(export_stdout="[]")
        with patch("bootstrap.subprocess.run", fake):
            bootstrap.load_infisical()
        export_args = fake.calls[1][0]
        assert [a for a in export_args if a.startswith("--path")] == ["--path=/other"]

    def test_path_falls_back_to_identity_file(self, infisical_env):
        _write_identity(infisical_env, projectId="p", path="/from-file")
        fake = FakeInfisical(export_stdout="[]")
        with patch("bootstrap.subprocess.run", fake):
            bootstrap.load_infisical()
        export_args = fake.calls[1][0]
        assert [a for a in export_args if a.startswith("--path")] == ["--path=/from-file"]

    def test_path_with_env_creds_and_no_identity_file(self, infisical_env, monkeypatch):
        monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID", "env-cid")
        monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET", "env-csecret")
        monkeypatch.setenv("DATUM_INFISICAL_PROJECT_ID", "proj-env")
        fake = FakeInfisical(export_stdout="[]")
        with patch("bootstrap.subprocess.run", fake):
            bootstrap.load_infisical()
        assert "--path=/datum" in fake.calls[1][0]

    def test_path_does_not_change_login(self, infisical_env, monkeypatch):
        _write_identity(infisical_env, projectId="p")
        monkeypatch.setenv("DATUM_INFISICAL_PATH", "/other")
        fake = FakeInfisical(export_stdout="[]")
        with patch("bootstrap.subprocess.run", fake):
            bootstrap.load_infisical()
        login_args = fake.calls[0][0]
        assert not any(a.startswith("--path") for a in login_args)

    def test_list_export_shape(self, infisical_env, monkeypatch):
        _write_identity(infisical_env, projectId="p")
        for k in ("DATUM_TEST_INF_A", "DATUM_TEST_INF_B"):
            monkeypatch.delenv(k, raising=False)
        payload = [{"key": "DATUM_TEST_INF_A", "value": "alpha"},
                   {"key": "DATUM_TEST_INF_B", "value": "beta"}]
        fake = FakeInfisical(export_stdout=json.dumps(payload))
        with patch("bootstrap.subprocess.run", fake):
            assert bootstrap.load_infisical() == 2
        assert os.environ["DATUM_TEST_INF_A"] == "alpha"
        assert os.environ["DATUM_TEST_INF_B"] == "beta"
        assert bootstrap._SOURCES["DATUM_TEST_INF_A"] == "infisical"

    def test_dict_export_shape(self, infisical_env, monkeypatch):
        _write_identity(infisical_env, projectId="p")
        for k in ("DATUM_TEST_INF_A", "DATUM_TEST_INF_B"):
            monkeypatch.delenv(k, raising=False)
        payload = {"DATUM_TEST_INF_A": "alpha", "DATUM_TEST_INF_B": 42}
        fake = FakeInfisical(export_stdout=json.dumps(payload))
        with patch("bootstrap.subprocess.run", fake):
            assert bootstrap.load_infisical() == 2
        assert os.environ["DATUM_TEST_INF_A"] == "alpha"
        assert os.environ["DATUM_TEST_INF_B"] == "42"

    def test_shell_value_is_never_overridden(self, infisical_env, monkeypatch):
        _write_identity(infisical_env, projectId="p")
        monkeypatch.setenv("DATUM_TEST_INF_A", "from-shell")
        monkeypatch.delenv("DATUM_TEST_INF_B", raising=False)
        payload = {"DATUM_TEST_INF_A": "from-infisical", "DATUM_TEST_INF_B": "b"}
        fake = FakeInfisical(export_stdout=json.dumps(payload))
        with patch("bootstrap.subprocess.run", fake):
            assert bootstrap.load_infisical() == 1
        assert os.environ["DATUM_TEST_INF_A"] == "from-shell"
        assert "DATUM_TEST_INF_A" not in bootstrap._SOURCES


# ─────────────────────────────────────────────
# Failure modes: one warning, return 0, never raise
# ─────────────────────────────────────────────
class TestInfisicalFailures:
    def _run(self, infisical_env, caplog, fake):
        _write_identity(infisical_env, projectId="p")
        with patch("bootstrap.subprocess.run", fake), caplog.at_level(logging.DEBUG):
            result = bootstrap.load_infisical()
        assert result == 0
        warns = _warnings(caplog)
        assert len(warns) == 1, [w.getMessage() for w in warns]
        msg = warns[0].getMessage()
        assert len(msg) <= 240
        assert "\x1b" not in msg
        assert "\n" not in msg
        assert _TOKEN not in msg
        assert "csecret-456" not in msg
        return msg

    def test_cli_missing(self, infisical_env, caplog):
        fake = FakeInfisical(login_exc=FileNotFoundError("infisical"))
        msg = self._run(infisical_env, caplog, fake)
        assert "PATH" in msg

    def test_login_timeout(self, infisical_env, caplog):
        fake = FakeInfisical(login_exc=subprocess.TimeoutExpired(
            ["infisical", "login"], 60))
        msg = self._run(infisical_env, caplog, fake)
        assert "timed out" in msg

    def test_export_timeout_does_not_leak_token(self, infisical_env, caplog):
        # TimeoutExpired's str() includes the full argv, which contains
        # --token=…; the diagnostic must not echo it.
        fake = FakeInfisical(export_exc=subprocess.TimeoutExpired(
            ["infisical", "export", f"--token={_TOKEN}"], 60))
        msg = self._run(infisical_env, caplog, fake)
        assert "timed out" in msg

    def test_login_nonzero_exit_strips_ansi_and_truncates(self, infisical_env, caplog):
        noisy = "\x1b[31merror:\x1b[0m   invalid\n\n   client " + ("x" * 600)
        fake = FakeInfisical(login_rc=1, login_stdout="", login_stderr=noisy)
        msg = self._run(infisical_env, caplog, fake)
        assert "error: invalid client" in msg
        assert len(fake.calls) == 1  # export never attempted

    def test_login_empty_token(self, infisical_env, caplog):
        fake = FakeInfisical(login_stdout="   \n")
        self._run(infisical_env, caplog, fake)
        assert len(fake.calls) == 1  # export never attempted

    def test_export_nonzero_exit(self, infisical_env, caplog):
        fake = FakeInfisical(export_rc=1, export_stderr="\x1b[33mforbidden\x1b[0m")
        msg = self._run(infisical_env, caplog, fake)
        assert "forbidden" in msg

    def test_export_bad_json(self, infisical_env, caplog):
        fake = FakeInfisical(export_stdout="this is not json {")
        self._run(infisical_env, caplog, fake)

    def test_export_unexpected_shape(self, infisical_env, caplog):
        fake = FakeInfisical(export_stdout=json.dumps("just a string"))
        self._run(infisical_env, caplog, fake)

    def test_export_list_missing_fields(self, infisical_env, caplog):
        fake = FakeInfisical(export_stdout=json.dumps([{"name": "X"}]))
        self._run(infisical_env, caplog, fake)

    def test_unexpected_exception_is_contained(self, infisical_env, caplog):
        fake = FakeInfisical(login_exc=PermissionError("access denied"))
        self._run(infisical_env, caplog, fake)

    def test_failure_injects_nothing(self, infisical_env, caplog, monkeypatch):
        monkeypatch.delenv("DATUM_TEST_INF_A", raising=False)
        fake = FakeInfisical(export_rc=1,
                             export_stdout=json.dumps({"DATUM_TEST_INF_A": "a"}))
        self._run(infisical_env, caplog, fake)
        assert "DATUM_TEST_INF_A" not in os.environ
        assert bootstrap._SOURCES == {}


# ─────────────────────────────────────────────
# Precedence across all three sources
# ─────────────────────────────────────────────
class TestThreeSourcePrecedence:
    def test_shell_beats_infisical_beats_env_local(self, infisical_env, tmp_path, monkeypatch):
        _write_identity(infisical_env, projectId="p")
        monkeypatch.setenv("DATUM_TEST_P_SHELL", "shell")
        for k in ("DATUM_TEST_P_INF", "DATUM_TEST_P_FILE", "DATUM_TEST_P_UNSET"):
            monkeypatch.delenv(k, raising=False)

        fake = FakeInfisical(export_stdout=json.dumps({
            "DATUM_TEST_P_SHELL": "infisical",
            "DATUM_TEST_P_INF": "infisical",
        }))
        env_file = tmp_path / ".env.local"
        env_file.write_text(
            "DATUM_TEST_P_SHELL=file\nDATUM_TEST_P_INF=file\nDATUM_TEST_P_FILE=file\n")

        # Same order as the import-time auto-load.
        with patch("bootstrap.subprocess.run", fake):
            assert bootstrap.load_infisical() == 1
        assert load_env_local(env_file) == 1

        assert os.environ["DATUM_TEST_P_SHELL"] == "shell"
        assert os.environ["DATUM_TEST_P_INF"] == "infisical"
        assert os.environ["DATUM_TEST_P_FILE"] == "file"

        names = ["DATUM_TEST_P_SHELL", "DATUM_TEST_P_INF", "DATUM_TEST_P_FILE",
                 "DATUM_TEST_P_UNSET"]
        sources = {name: src for name, src, _, _ in bootstrap.describe_sources(names)}
        assert sources == {
            "DATUM_TEST_P_SHELL": "shell",
            "DATUM_TEST_P_INF": "infisical",
            "DATUM_TEST_P_FILE": ".env.local",
            "DATUM_TEST_P_UNSET": "unset",
        }

    def test_env_local_records_its_source(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DATUM_TEST_P_FILE", raising=False)
        env_file = tmp_path / ".env.local"
        env_file.write_text("DATUM_TEST_P_FILE=file\n")
        load_env_local(env_file)
        assert bootstrap._SOURCES["DATUM_TEST_P_FILE"] == ".env.local"

    def test_stale_record_for_removed_var_reports_unset(self, monkeypatch):
        monkeypatch.delenv("DATUM_TEST_P_GONE", raising=False)
        bootstrap._SOURCES["DATUM_TEST_P_GONE"] = "infisical"
        assert bootstrap.describe_sources(["DATUM_TEST_P_GONE"]) == [
            ("DATUM_TEST_P_GONE", "unset", "", 0)]


# ─────────────────────────────────────────────
# Import-time behaviour
# ─────────────────────────────────────────────
class TestAutoLoad:
    def test_autoload_order_is_infisical_then_env_local(self):
        parent = MagicMock()
        with patch("bootstrap.load_infisical", parent.load_infisical), \
             patch("bootstrap.load_env_local", parent.load_env_local):
            bootstrap._autoload()
        assert [c[0] for c in parent.mock_calls] == ["load_infisical", "load_env_local"]

    def test_fresh_import_never_raises_or_prints_to_stdout(self, tmp_path):
        """Import bootstrap in a clean interpreter with a broken CLI.

        subprocess.run is replaced inside the child BEFORE the import, so
        the real CLI is never invoked; the loader must swallow the failure,
        write its one warning to stderr, and leave stdout empty.
        """
        identity = tmp_path / "identity.json"
        _write_identity(identity, projectId="p")
        root = str(Path(bootstrap.__file__).resolve().parent)
        script = (
            "import subprocess, sys\n"
            "def _boom(*a, **k):\n"
            "    raise FileNotFoundError('infisical')\n"
            "subprocess.run = _boom\n"
            f"sys.path.insert(0, {root!r})\n"
            "import bootstrap\n"
        )
        env = {k: v for k, v in os.environ.items() if k not in _INFISICAL_ENV_VARS}
        env["DATUM_INFISICAL_IDENTITY_FILE"] = str(identity)
        result = subprocess.run(
            [sys.executable, "-c", script], env=env, cwd=str(tmp_path),
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert "infisical" in result.stderr.lower()


# ─────────────────────────────────────────────
# describe_sources / format_sources — never more than 8 chars of a value
# ─────────────────────────────────────────────
def _assert_no_9char_window(text, value):
    for i in range(len(value) - 8):
        window = value[i:i + 9]
        assert window not in text, f"9-char slice of a secret leaked: {window!r}"


class TestDescribeSources:
    def test_default_names(self):
        assert [row[0] for row in bootstrap.describe_sources()] == _DEFAULT_NAMES

    def test_row_shape_for_set_value(self, monkeypatch):
        monkeypatch.setenv("DATUM_TEST_DS", _LONG_SECRET)
        [(name, source, prefix, length)] = bootstrap.describe_sources(["DATUM_TEST_DS"])
        assert name == "DATUM_TEST_DS"
        assert source == "shell"
        assert prefix == _LONG_SECRET[:8]
        assert length == len(_LONG_SECRET)

    def test_row_shape_for_unset(self, monkeypatch):
        monkeypatch.delenv("DATUM_TEST_DS", raising=False)
        assert bootstrap.describe_sources(["DATUM_TEST_DS"]) == [
            ("DATUM_TEST_DS", "unset", "", 0)]

    def test_short_value_is_reported_whole(self, monkeypatch):
        monkeypatch.setenv("DATUM_TEST_DS", "abc")
        assert bootstrap.describe_sources(["DATUM_TEST_DS"]) == [
            ("DATUM_TEST_DS", "shell", "abc", 3)]

    def test_infisical_source_is_reported(self, infisical_env, monkeypatch):
        _write_identity(infisical_env, projectId="p")
        monkeypatch.delenv("DATUM_TEST_DS", raising=False)
        fake = FakeInfisical(export_stdout=json.dumps({"DATUM_TEST_DS": _LONG_SECRET}))
        with patch("bootstrap.subprocess.run", fake):
            bootstrap.load_infisical()
        [(_, source, prefix, length)] = bootstrap.describe_sources(["DATUM_TEST_DS"])
        assert source == "infisical"
        assert prefix == _LONG_SECRET[:8]
        assert length == len(_LONG_SECRET)

    def test_describe_never_exposes_more_than_8_chars(self, monkeypatch):
        names = ["DATUM_TEST_DS_A", "DATUM_TEST_DS_B"]
        values = [f"{i}{_LONG_SECRET}" for i in range(len(names))]
        for k, v in zip(names, values):
            monkeypatch.setenv(k, v)
        rows = bootstrap.describe_sources(names)
        rendered = repr(rows) + str(rows)
        for v in values:
            _assert_no_9char_window(rendered, v)
        for _, _, prefix, _ in rows:
            assert len(prefix) <= 8

    def test_format_sources_one_line_per_credential(self, monkeypatch):
        monkeypatch.setenv("DATUM_TEST_DS_A", _LONG_SECRET)
        monkeypatch.delenv("DATUM_TEST_DS_B", raising=False)
        text = bootstrap.format_sources(["DATUM_TEST_DS_A", "DATUM_TEST_DS_B"])
        lines = text.splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("DATUM_TEST_DS_A")
        assert "shell" in lines[0]
        assert _LONG_SECRET[:8] in lines[0]
        assert str(len(_LONG_SECRET)) in lines[0]
        assert lines[1].startswith("DATUM_TEST_DS_B")
        assert "unset" in lines[1]
        _assert_no_9char_window(text, _LONG_SECRET)

    def test_format_sources_default_covers_all_names(self):
        text = bootstrap.format_sources()
        assert len(text.splitlines()) == len(_DEFAULT_NAMES)
        for name in _DEFAULT_NAMES:
            assert name in text
            _assert_no_9char_window(text, os.environ.get(name, ""))
