"""
Tests for the pluggable APS token stores in aps_client.py.

  - FileTokenStore: the original JSON-file behaviour (migrated from the
    old ``TokenStore`` tests in test_aps_client.py; ``TokenStore`` is kept
    as an alias).
  - InfisicalTokenStore: reads APS_REFRESH_TOKEN from Infisical via the
    CLI, writes the rotated refresh token back on every ``update()``,
    never puts the token in a process argv, ``clear()`` is a no-op.
  - Store selection via ``APS_TOKEN_STORE`` and ``APSClient(token_store=)``.

``subprocess.run`` and ``requests.Session`` are patched throughout; no real
Infisical CLI or Autodesk call is ever made.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import aps_client
from aps_client import (
    APSAuthError,
    APSClient,
    APSConfigError,
    BaseTokenStore,
    FileTokenStore,
    InfisicalTokenStore,
    TokenStore,
    infisical_set_secret,
    select_token_store,
)

PROJECT_ID = "proj-1234"
OLD_RT = "old-refresh-token-0123456789abcdef"
NEW_RT = "new-refresh-token-fedcba9876543210"
INFISICAL_ACCESS = "infisical-machine-access-token"


# ─────────────────────────────────────────────
# Fake Infisical CLI
# ─────────────────────────────────────────────
class FakeInfisical:
    """
    Stand-in for ``subprocess.run`` that understands the three infisical
    invocations aps_client makes: ``login``, ``secrets get``, ``secrets set``.

    Every call is recorded (args + kwargs). For ``secrets set KEY=@path`` the
    file's contents are read while the call is in flight, so tests can assert
    both what was written and that the temp file is gone afterwards.
    """

    def __init__(self, stored: str | None = OLD_RT):
        self.stored = stored
        self.calls: list[tuple[list[str], dict]] = []
        self.set_files: list[str] = []
        self.login_rc = 0
        self.get_rc = 0
        self.set_rc = 0
        self.set_stderr = "\x1b[31mError:\x1b[0m permission denied"
        self.raise_on: str | None = None  # "login"/"get"/"set" -> FileNotFoundError

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append((args, kwargs))
        verb = args[1] if len(args) > 1 else ""
        sub = args[2] if len(args) > 2 else ""
        if verb == "login":
            if self.raise_on == "login":
                raise FileNotFoundError("infisical")
            return subprocess.CompletedProcess(
                args, self.login_rc,
                stdout=INFISICAL_ACCESS + "\n" if self.login_rc == 0 else "",
                stderr="" if self.login_rc == 0 else "\x1b[31mbad identity\x1b[0m",
            )
        if verb == "secrets" and sub == "get":
            if self.raise_on == "get":
                raise subprocess.TimeoutExpired(args, 60)
            if self.get_rc:
                return subprocess.CompletedProcess(args, self.get_rc, "", "forbidden")
            return subprocess.CompletedProcess(
                args, 0, stdout=(self.stored or "") + "\n", stderr="")
        if verb == "secrets" and sub == "set":
            if self.raise_on == "set":
                raise FileNotFoundError("infisical")
            assignment = args[3]
            key, _, ref = assignment.partition("=")
            assert ref.startswith("@"), "value must be passed by @file, not inline"
            path = ref[1:]
            self.set_files.append(path)
            value = Path(path).read_text(encoding="utf-8")
            if self.set_rc:
                return subprocess.CompletedProcess(
                    args, self.set_rc, "", self.set_stderr)
            self.stored = value
            return subprocess.CompletedProcess(args, 0, "masked table", "")
        raise AssertionError(f"unexpected infisical call: {args}")

    def all_argv_text(self) -> str:
        return "\n".join(" ".join(a) for a, _ in self.calls)

    def verbs(self) -> list[str]:
        return [" ".join(a[1:3]) if a[1] == "secrets" else a[1] for a, _ in self.calls]


@pytest.fixture
def identity_env(monkeypatch):
    monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID", "ua-client-id")
    monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET", "ua-client-secret")
    monkeypatch.setenv("DATUM_INFISICAL_PROJECT_ID", PROJECT_ID)
    monkeypatch.delenv("DATUM_INFISICAL_ENV", raising=False)
    monkeypatch.delenv("APS_TOKEN_STORE", raising=False)


@pytest.fixture
def fake_cli(monkeypatch):
    fake = FakeInfisical()
    monkeypatch.setattr(aps_client.subprocess, "run", fake)
    return fake


# ─────────────────────────────────────────────
# FileTokenStore — in-memory (path=None)
# ─────────────────────────────────────────────
class TestFileTokenStoreMemory:
    def test_alias_is_file_store(self):
        assert TokenStore is FileTokenStore
        assert issubclass(FileTokenStore, BaseTokenStore)

    def test_initially_invalid(self):
        store = FileTokenStore(path=None)
        assert not store.is_valid

    def test_update_makes_valid(self):
        store = FileTokenStore(path=None)
        store.update({
            "access_token": "tok123",
            "refresh_token": "ref456",
            "expires_in": 3600,
        })
        assert store.is_valid
        assert store.access_token == "tok123"
        assert store.refresh_token == "ref456"

    def test_expired_token_is_invalid(self):
        store = FileTokenStore(path=None)
        store.update({"access_token": "tok", "expires_in": 0})
        assert not store.is_valid

    def test_clear_invalidates(self):
        store = FileTokenStore(path=None)
        store.update({"access_token": "tok", "expires_in": 3600})
        store.clear()
        assert not store.is_valid
        assert store.refresh_token is None


# ─────────────────────────────────────────────
# FileTokenStore — file persistence
# ─────────────────────────────────────────────
class TestFileTokenStorePersistence:
    def test_save_and_load(self, tmp_path):
        token_file = tmp_path / ".aps_tokens.json"
        store1 = FileTokenStore(path=token_file)
        store1.update({
            "access_token": "persisted-at",
            "refresh_token": "persisted-rt",
            "expires_in": 3600,
        })
        assert token_file.exists()

        store2 = FileTokenStore(path=token_file)
        assert store2.access_token == "persisted-at"
        assert store2.refresh_token == "persisted-rt"
        assert store2.is_valid

    def test_clear_deletes_file(self, tmp_path):
        token_file = tmp_path / ".aps_tokens.json"
        store = FileTokenStore(path=token_file)
        store.update({"access_token": "tok", "expires_in": 3600})
        assert token_file.exists()
        store.clear()
        assert not token_file.exists()

    def test_missing_file_is_ok(self, tmp_path):
        store = FileTokenStore(path=tmp_path / "nonexistent.json")
        assert not store.is_valid

    def test_corrupt_file_is_ok(self, tmp_path):
        token_file = tmp_path / ".aps_tokens.json"
        token_file.write_text("not json", encoding="utf-8")
        store = FileTokenStore(path=token_file)
        assert not store.is_valid

    def test_no_subprocess_ever(self, tmp_path, monkeypatch):
        boom = MagicMock(side_effect=AssertionError("file store ran a subprocess"))
        monkeypatch.setattr(aps_client.subprocess, "run", boom)
        store = FileTokenStore(path=tmp_path / "t.json")
        store.update({"access_token": "a", "refresh_token": "r", "expires_in": 3600})
        store.clear()
        boom.assert_not_called()


# ─────────────────────────────────────────────
# InfisicalTokenStore — load
# ─────────────────────────────────────────────
class TestInfisicalLoad:
    def test_loads_refresh_token_and_starts_invalid(self, identity_env, fake_cli):
        store = InfisicalTokenStore()
        assert store.refresh_token == OLD_RT
        assert store.access_token is None
        assert not store.is_valid  # first _ensure_token() must refresh
        assert store.project_id == PROJECT_ID
        assert store.env == "prod"
        assert store.key == "APS_REFRESH_TOKEN"

    def test_get_call_shape(self, identity_env, fake_cli):
        InfisicalTokenStore()
        assert fake_cli.verbs() == ["login", "secrets get"]
        login_args, login_kw = fake_cli.calls[0]
        assert login_args == ["infisical", "login", "--method=universal-auth",
                              "--plain", "--silent"]
        get_args, get_kw = fake_cli.calls[1]
        assert "APS_REFRESH_TOKEN" in get_args
        assert "--plain" in get_args
        assert f"--projectId={PROJECT_ID}" in get_args
        assert "--env=prod" in get_args
        assert f"--token={INFISICAL_ACCESS}" in get_args
        for _, kw in fake_cli.calls:
            assert kw["stdin"] is subprocess.DEVNULL
            assert kw["capture_output"] is True
            assert kw["encoding"] == "utf-8"
            assert kw["errors"] == "replace"
            assert kw["timeout"] == 60

    def test_identity_secret_not_in_argv(self, identity_env, fake_cli):
        InfisicalTokenStore()
        assert "ua-client-secret" not in fake_cli.all_argv_text()
        login_kw = fake_cli.calls[0][1]
        assert login_kw["env"]["INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET"] == "ua-client-secret"

    def test_explicit_args_override_env(self, identity_env, fake_cli, monkeypatch):
        monkeypatch.setenv("DATUM_INFISICAL_ENV", "staging")
        store = InfisicalTokenStore(project_id="other-proj", env="qa", key="MY_RT")
        get_args = fake_cli.calls[1][0]
        assert "MY_RT" in get_args
        assert "--projectId=other-proj" in get_args
        assert "--env=qa" in get_args
        assert store.env == "qa"

    def test_env_var_overrides_default_env(self, identity_env, fake_cli, monkeypatch):
        monkeypatch.setenv("DATUM_INFISICAL_ENV", "staging")
        store = InfisicalTokenStore()
        assert store.env == "staging"
        assert "--env=staging" in fake_cli.calls[1][0]

    def test_missing_project_id_is_config_error(self, identity_env, fake_cli, monkeypatch):
        monkeypatch.delenv("DATUM_INFISICAL_PROJECT_ID")
        with pytest.raises(APSConfigError, match="DATUM_INFISICAL_PROJECT_ID"):
            InfisicalTokenStore()
        assert fake_cli.calls == []

    def test_missing_identity_raises_auth_error(self, identity_env, fake_cli, monkeypatch):
        monkeypatch.delenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET")
        with pytest.raises(APSAuthError, match="INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET"):
            InfisicalTokenStore()
        assert fake_cli.calls == []

    def test_login_rejected_raises_auth_error_ansi_stripped(self, identity_env, fake_cli):
        fake_cli.login_rc = 1
        with pytest.raises(APSAuthError) as exc:
            InfisicalTokenStore()
        assert "bad identity" in str(exc.value)
        assert "\x1b" not in str(exc.value)

    def test_cli_missing_raises_auth_error(self, identity_env, fake_cli):
        fake_cli.raise_on = "login"
        with pytest.raises(APSAuthError, match="not on PATH"):
            InfisicalTokenStore()

    def test_get_timeout_raises_auth_error(self, identity_env, fake_cli):
        fake_cli.raise_on = "get"
        with pytest.raises(APSAuthError, match="timed out"):
            InfisicalTokenStore()

    def test_get_nonzero_raises_auth_error(self, identity_env, fake_cli):
        fake_cli.get_rc = 1
        with pytest.raises(APSAuthError, match="forbidden"):
            InfisicalTokenStore()

    @pytest.mark.parametrize("stored", [None, "", "*not found*"])
    def test_empty_secret_says_seed_it(self, identity_env, fake_cli, stored):
        fake_cli.stored = stored
        with pytest.raises(APSAuthError, match="datum-aps-consent"):
            InfisicalTokenStore()


# ─────────────────────────────────────────────
# InfisicalTokenStore — write-back
# ─────────────────────────────────────────────
class TestInfisicalWriteBack:
    def _update(self, store):
        store.update({
            "access_token": "new-access",
            "refresh_token": NEW_RT,
            "expires_in": 3600,
        })

    def test_update_writes_new_refresh_token(self, identity_env, fake_cli):
        store = InfisicalTokenStore()
        self._update(store)
        assert fake_cli.stored == NEW_RT
        assert store.refresh_token == NEW_RT
        assert store.access_token == "new-access"
        assert store.is_valid
        assert fake_cli.verbs() == ["login", "secrets get", "login", "secrets set"]
        set_args = fake_cli.calls[-1][0]
        assert set_args[3].startswith("APS_REFRESH_TOKEN=@")
        assert f"--projectId={PROJECT_ID}" in set_args
        assert "--env=prod" in set_args
        assert f"--token={INFISICAL_ACCESS}" in set_args
        set_kw = fake_cli.calls[-1][1]
        assert set_kw["stdin"] is subprocess.DEVNULL
        assert set_kw["timeout"] == 60

    def test_token_never_in_any_argv(self, identity_env, fake_cli):
        store = InfisicalTokenStore()
        self._update(store)
        argv = fake_cli.all_argv_text()
        assert NEW_RT not in argv
        assert OLD_RT not in argv

    def test_temp_file_deleted_after_write(self, identity_env, fake_cli):
        store = InfisicalTokenStore()
        self._update(store)
        assert len(fake_cli.set_files) == 1
        assert not Path(fake_cli.set_files[0]).exists()

    def test_write_back_happens_before_update_returns(self, identity_env, fake_cli, monkeypatch):
        store = InfisicalTokenStore()
        seen = []
        real = fake_cli.__call__

        def spy(args, **kw):
            res = real(args, **kw)
            seen.append(list(args)[1:3])
            return res

        monkeypatch.setattr(aps_client.subprocess, "run", spy)
        self._update(store)
        # By the time update() has returned, the set has already run.
        assert ["secrets", "set"] in seen

    def test_write_back_failure_raises_and_invalidates(self, identity_env, fake_cli):
        store = InfisicalTokenStore()
        fake_cli.set_rc = 1
        with pytest.raises(APSAuthError, match="refresh token write-back failed") as exc:
            self._update(store)
        assert "permission denied" in str(exc.value)
        assert "\x1b" not in str(exc.value)
        assert NEW_RT not in str(exc.value)
        # The un-persisted access token must not be usable by an API call.
        assert not store.is_valid
        # The new refresh token is kept in memory: it is the only live copy.
        assert store.refresh_token == NEW_RT
        assert not Path(fake_cli.set_files[0]).exists()

    def test_write_back_cli_missing_raises(self, identity_env, fake_cli):
        store = InfisicalTokenStore()
        fake_cli.raise_on = "set"
        with pytest.raises(APSAuthError, match="refresh token write-back failed"):
            self._update(store)
        assert not store.is_valid

    def test_missing_refresh_token_in_response_raises(self, identity_env, fake_cli):
        store = InfisicalTokenStore()
        with pytest.raises(APSAuthError, match="refresh token write-back failed"):
            store.update({"access_token": "a", "expires_in": 3600})
        assert not store.is_valid
        assert "secrets set" not in fake_cli.verbs()

    def test_clear_is_noop_with_warning(self, identity_env, fake_cli, caplog):
        store = InfisicalTokenStore()
        self._update(store)
        n_calls = len(fake_cli.calls)
        with caplog.at_level(logging.WARNING, logger="aps_client"):
            store.clear()
        assert len(fake_cli.calls) == n_calls  # nothing deleted remotely
        assert fake_cli.stored == NEW_RT
        assert store.refresh_token == NEW_RT
        assert any("no-op" in r.getMessage() for r in caplog.records)


# ─────────────────────────────────────────────
# infisical_set_secret — shared write path
# ─────────────────────────────────────────────
class TestInfisicalSetSecret:
    def test_without_token_omits_token_flag(self, fake_cli):
        infisical_set_secret(PROJECT_ID, "prod", "APS_REFRESH_TOKEN", NEW_RT)
        args = fake_cli.calls[-1][0]
        assert not any(a.startswith("--token") for a in args)
        assert NEW_RT not in " ".join(args)
        assert fake_cli.stored == NEW_RT

    def test_with_token(self, fake_cli):
        infisical_set_secret(PROJECT_ID, "prod", "K", NEW_RT, token="tkn")
        assert "--token=tkn" in fake_cli.calls[-1][0]

    def test_refuses_empty_value(self, fake_cli):
        with pytest.raises(aps_client.InfisicalCLIError):
            infisical_set_secret(PROJECT_ID, "prod", "K", "")
        assert fake_cli.calls == []


# ─────────────────────────────────────────────
# Selection + APSClient injection
# ─────────────────────────────────────────────
class TestSelection:
    def test_default_is_file_store(self, monkeypatch, tmp_path):
        monkeypatch.delenv("APS_TOKEN_STORE", raising=False)
        store = select_token_store(tmp_path / "t.json")
        assert isinstance(store, FileTokenStore)
        assert store._path == tmp_path / "t.json"

    def test_apsclient_no_args_no_env_uses_file_store(self, monkeypatch):
        monkeypatch.delenv("APS_TOKEN_STORE", raising=False)
        boom = MagicMock(side_effect=AssertionError("ran a subprocess"))
        monkeypatch.setattr(aps_client.subprocess, "run", boom)
        client = APSClient(token_path=None)
        assert type(client.tokens) is FileTokenStore
        assert client.tokens._path is None
        default = APSClient.__init__.__defaults__
        assert aps_client._DEFAULT_TOKEN_PATH in default
        boom.assert_not_called()

    @pytest.mark.parametrize("value", ["file", "FILE", " file ", ""])
    def test_file_values(self, monkeypatch, value):
        monkeypatch.setenv("APS_TOKEN_STORE", value)
        assert isinstance(select_token_store(None), FileTokenStore)

    def test_token_path_string_honoured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APS_TOKEN_STORE", "file")
        client = APSClient(token_path=str(tmp_path / "x.json"))
        assert client.tokens._path == tmp_path / "x.json"

    def test_infisical_value(self, identity_env, fake_cli, monkeypatch):
        monkeypatch.setenv("APS_TOKEN_STORE", "infisical")
        client = APSClient(client_id="id", client_secret="s")
        assert isinstance(client.tokens, InfisicalTokenStore)
        assert client.tokens.refresh_token == OLD_RT

    def test_invalid_value_is_config_error(self, monkeypatch):
        monkeypatch.setenv("APS_TOKEN_STORE", "vault")
        with pytest.raises(APSConfigError, match="APS_TOKEN_STORE"):
            APSClient(token_path=None)

    def test_injected_store_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("APS_TOKEN_STORE", "vault")  # would be invalid
        store = FileTokenStore(path=None)
        client = APSClient(client_id="id", client_secret="s", token_store=store)
        assert client.tokens is store

    def test_injected_custom_store_is_used_for_refresh(self, monkeypatch):
        class MemoryStore(BaseTokenStore):
            def __init__(self):
                super().__init__()
                self.persisted = []

            def _persist(self):
                self.persisted.append(self.refresh_token)

        store = MemoryStore()
        store.refresh_token = "seed-rt"
        client = APSClient(client_id="id", client_secret="s", token_store=store)
        resp = MagicMock(ok=True)
        resp.json.return_value = {
            "access_token": "at", "refresh_token": "rt2", "expires_in": 3600}
        with patch.object(client._session, "post", return_value=resp):
            client._ensure_token()
        assert store.persisted == ["rt2"]
        assert store.is_valid


# ─────────────────────────────────────────────
# End to end: refresh with the Infisical store
# ─────────────────────────────────────────────
class TestRefreshWithInfisical:
    def test_write_back_precedes_first_api_call(self, identity_env, fake_cli, monkeypatch):
        store = InfisicalTokenStore()
        client = APSClient(client_id="id", client_secret="s", token_store=store)
        order: list[str] = []
        real_run = fake_cli.__call__

        def run_spy(args, **kw):
            order.append("infisical " + " ".join(list(args)[1:3]))
            return real_run(args, **kw)

        refresh_resp = MagicMock(ok=True)
        refresh_resp.json.return_value = {
            "access_token": "fresh-at", "refresh_token": NEW_RT, "expires_in": 3600}

        def post_spy(url, data=None, **kw):
            order.append("autodesk refresh")
            assert data["refresh_token"] == OLD_RT
            return refresh_resp

        hubs_resp = MagicMock(ok=True)
        hubs_resp.json.return_value = {"data": []}

        def get_spy(url, headers=None, **kw):
            order.append("autodesk api")
            assert headers["Authorization"] == "Bearer fresh-at"
            return hubs_resp

        monkeypatch.setattr(aps_client.subprocess, "run", run_spy)
        with patch.object(client._session, "post", side_effect=post_spy), \
             patch.object(client._session, "get", side_effect=get_spy):
            client.get_hubs()

        assert order.index("infisical secrets set") < order.index("autodesk api")
        assert order.index("autodesk refresh") < order.index("infisical secrets set")
        assert fake_cli.stored == NEW_RT

    def test_failed_write_back_blocks_api_call(self, identity_env, fake_cli):
        store = InfisicalTokenStore()
        client = APSClient(client_id="id", client_secret="s", token_store=store)
        fake_cli.set_rc = 1
        refresh_resp = MagicMock(ok=True)
        refresh_resp.json.return_value = {
            "access_token": "fresh-at", "refresh_token": NEW_RT, "expires_in": 3600}
        get = MagicMock()
        with patch.object(client._session, "post", return_value=refresh_resp), \
             patch.object(client._session, "get", get):
            with pytest.raises(APSAuthError, match="write-back failed"):
                client.get_hubs()
        get.assert_not_called()
