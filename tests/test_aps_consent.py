"""
Tests for scripts/aps_consent.py (console script ``datum-aps-consent``).

``APSClient.exchange_code`` and ``subprocess.run`` are patched: no browser,
no Autodesk call, no real Infisical CLI. The refresh token must never reach
stdout or stderr.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import aps_client
from aps_client import APSAuthError, APSClient
from scripts import aps_consent

SEED_RT = "seeded-refresh-token-9f8e7d6c5b4a3210"


class FakeCLI:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.stored: dict[str, str] = {}
        self.set_rc = 0
        self.login_rc = 0

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        assert kwargs["stdin"] is subprocess.DEVNULL
        if args[1] == "login":
            if self.login_rc:
                return subprocess.CompletedProcess(args, 1, "", "identity rejected")
            return subprocess.CompletedProcess(args, 0, "ua-access-token\n", "")
        if args[1:3] == ["secrets", "set"]:
            key, _, ref = args[3].partition("=")
            assert ref.startswith("@")
            value = Path(ref[1:]).read_text(encoding="utf-8")
            if self.set_rc:
                return subprocess.CompletedProcess(args, 1, "", "\x1b[31mforbidden\x1b[0m")
            self.stored[key] = value
            return subprocess.CompletedProcess(args, 0, "masked", "")
        raise AssertionError(f"unexpected call {args}")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATUM_INFISICAL_PROJECT_ID", "proj-xyz")
    monkeypatch.delenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("APS_TOKEN_STORE", raising=False)


@pytest.fixture
def cli(monkeypatch):
    fake = FakeCLI()
    monkeypatch.setattr(aps_client.subprocess, "run", fake)
    return fake


@pytest.fixture
def exchange(monkeypatch):
    """Patch exchange_code to fill the client's in-memory store."""
    seen = {}

    def fake_exchange(self, code):
        seen["code"] = code
        seen["store"] = self.tokens
        data = {"access_token": "at", "refresh_token": SEED_RT, "expires_in": 3600}
        self.tokens.update(data)
        return data

    monkeypatch.setattr(APSClient, "exchange_code", fake_exchange)
    return seen


def _out(capsys):
    captured = capsys.readouterr()
    return captured.out, captured.err


class TestHappyPath:
    def test_seeds_with_human_session(self, env, cli, exchange, capsys):
        rc = aps_consent.main(["--code", "abc123", "--client-id", "id",
                               "--client-secret", "s"])
        out, err = _out(capsys)
        assert rc == 0
        assert exchange["code"] == "abc123"
        # In-memory file store: no .aps_tokens.json is written.
        assert isinstance(exchange["store"], aps_client.FileTokenStore)
        assert exchange["store"]._path is None
        assert cli.stored == {"APS_REFRESH_TOKEN": SEED_RT}
        set_args = cli.calls[-1]
        assert "--projectId=proj-xyz" in set_args
        assert "--env=prod" in set_args
        # No identity in env -> no login, no --token (uses `infisical login` session).
        assert [c[1] for c in cli.calls] == ["secrets"]
        assert not any(a.startswith("--token") for a in set_args)
        assert "authorize?" in out
        assert f"seeded APS_REFRESH_TOKEN (prefix {SEED_RT[:8]}…, len {len(SEED_RT)})" in out
        assert SEED_RT not in out
        assert SEED_RT not in err
        assert SEED_RT not in " ".join(" ".join(c) for c in cli.calls)

    def test_seeds_with_machine_identity(self, env, cli, exchange, capsys, monkeypatch):
        monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET", "csecret")
        rc = aps_consent.main(["--code", "abc", "--client-id", "id",
                               "--client-secret", "s"])
        out, err = _out(capsys)
        assert rc == 0
        assert [c[1] for c in cli.calls] == ["login", "secrets"]
        assert "--token=ua-access-token" in cli.calls[-1]
        assert SEED_RT not in out + err

    def test_prompts_for_code_and_accepts_callback_url(self, env, cli, exchange,
                                                        capsys, monkeypatch):
        monkeypatch.setattr(
            "builtins.input",
            lambda prompt="": "http://localhost:5000/api/aps/callback?code=fromurl&state=x",
        )
        rc = aps_consent.main(["--client-id", "id", "--client-secret", "s"])
        assert rc == 0
        assert exchange["code"] == "fromurl"

    def test_project_and_env_flags(self, env, cli, exchange, capsys):
        rc = aps_consent.main(["--code", "c", "--project-id", "p2", "--env", "dev",
                               "--client-id", "id", "--client-secret", "s"])
        assert rc == 0
        assert "--projectId=p2" in cli.calls[-1]
        assert "--env=dev" in cli.calls[-1]


class TestFailurePaths:
    def test_write_failure_exits_1_without_token(self, env, cli, exchange, capsys):
        cli.set_rc = 1
        rc = aps_consent.main(["--code", "c", "--client-id", "id",
                               "--client-secret", "s"])
        out, err = _out(capsys)
        assert rc == 1
        assert "forbidden" in err
        assert "\x1b" not in err
        assert len(err.strip().splitlines()) == 1
        assert SEED_RT not in out
        assert SEED_RT not in err
        assert "seeded" not in out

    def test_exchange_failure_exits_1(self, env, cli, capsys, monkeypatch):
        def boom(self, code):
            raise APSAuthError("Token exchange failed: 400 invalid_grant")

        monkeypatch.setattr(APSClient, "exchange_code", boom)
        rc = aps_consent.main(["--code", "bad", "--client-id", "id",
                               "--client-secret", "s"])
        out, err = _out(capsys)
        assert rc == 1
        assert "invalid_grant" in err
        assert cli.calls == []

    def test_missing_project_id_exits_1(self, env, cli, exchange, capsys, monkeypatch):
        monkeypatch.delenv("DATUM_INFISICAL_PROJECT_ID")
        monkeypatch.setattr(aps_consent, "_identity_file_project_id", lambda: None)
        rc = aps_consent.main(["--code", "c", "--client-id", "id",
                               "--client-secret", "s"])
        out, err = _out(capsys)
        assert rc == 1
        assert "DATUM_INFISICAL_PROJECT_ID" in err
        assert cli.calls == []

    def test_empty_code_exits_1(self, env, cli, exchange, capsys, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "   ")
        rc = aps_consent.main(["--client-id", "id", "--client-secret", "s"])
        out, err = _out(capsys)
        assert rc == 1
        assert "code" in err
        assert "code" not in exchange

    def test_missing_aps_config_exits_1(self, env, cli, exchange, capsys, monkeypatch):
        monkeypatch.setattr(aps_client, "APS_CLIENT_ID", "")
        rc = aps_consent.main(["--code", "c", "--client-id", "", "--client-secret", "s"])
        out, err = _out(capsys)
        assert rc == 1
        assert "APS_CLIENT_ID" in err

    def test_login_failure_exits_1(self, env, cli, exchange, capsys, monkeypatch):
        monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET", "csecret")
        cli.login_rc = 1
        rc = aps_consent.main(["--code", "c", "--client-id", "id",
                               "--client-secret", "s"])
        out, err = _out(capsys)
        assert rc == 1
        assert "identity rejected" in err
        assert SEED_RT not in out + err


class TestIdentityFileProjectId:
    def test_reads_project_id_only(self, tmp_path, monkeypatch):
        f = tmp_path / "infisical-identity.json"
        f.write_text('{"clientId": "a", "clientSecret": "b", "projectId": "from-file"}',
                     encoding="utf-8")
        monkeypatch.setattr(aps_consent, "IDENTITY_FILE", f)
        assert aps_consent._identity_file_project_id() == "from-file"

    def test_missing_or_bad_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(aps_consent, "IDENTITY_FILE", tmp_path / "nope.json")
        assert aps_consent._identity_file_project_id() is None
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        monkeypatch.setattr(aps_consent, "IDENTITY_FILE", bad)
        assert aps_consent._identity_file_project_id() is None
