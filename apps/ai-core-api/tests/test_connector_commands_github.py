import uuid

import pytest

from app.routers import connector_github
from app.services import connector_commands


def test_write_github_cli_files_creates_hosts_yml(tmp_path):
    connector_commands._write_github_cli_files(str(tmp_path), "gho_test_token", "alden")

    hosts = (tmp_path / "hosts.yml").read_text(encoding="utf-8")

    assert "github.com:" in hosts
    assert 'oauth_token: "gho_test_token"' in hosts
    assert 'user: "alden"' in hosts
    assert "git_protocol: https" in hosts


@pytest.mark.asyncio
async def test_run_github_cli_uses_user_scoped_profile(monkeypatch, tmp_path):
    user_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def fake_retrieve_token(provider, actual_user_id):
        assert provider == "github"
        assert actual_user_id == user_id
        return {"access_token": "gho_test_token", "login": "alden"}

    async def fake_ensure_profile(actual_user_id, token_data):
        assert actual_user_id == user_id
        assert token_data["access_token"] == "gho_test_token"
        return {"ready": True, "login": "alden", "config_dir": str(tmp_path)}

    class Result:
        success = True
        stdout = "alden\n"
        stderr = ""

        def to_dict(self):
            return {
                "stdout": self.stdout,
                "stderr": self.stderr,
                "exit_code": 0,
                "timed_out": False,
                "output_truncated": False,
                "stdout_chars": len(self.stdout),
                "stderr_chars": 0,
                "error": None,
            }

    async def fake_run_command(command, timeout, env, allowed_binaries=None):
        captured["command"] = command
        captured["timeout"] = timeout
        captured["env"] = env
        captured["allowed_binaries"] = allowed_binaries
        return Result()

    monkeypatch.setattr(connector_commands, "retrieve_token", fake_retrieve_token)
    monkeypatch.setattr(connector_commands, "ensure_github_cli_profile", fake_ensure_profile)
    monkeypatch.setattr(connector_commands, "_github_config_dir", lambda _user_id: str(tmp_path))
    monkeypatch.setattr(connector_commands, "run_command", fake_run_command)

    result = await connector_commands.run_github_cli_command("gh repo list", user_id)

    assert result["status"] == "success"
    assert result["auth_method"] == "user_scoped_gh_cli"
    assert captured["env"] == {"GH_CONFIG_DIR": str(tmp_path)}
    assert captured["allowed_binaries"] == connector_commands.GITHUB_ALLOWED_BINARIES


def test_github_oauth_state_round_trip_for_user():
    user_id = uuid.uuid4()
    signing_key = "state-signing-key-with-at-least-32-bytes"
    state = connector_github._sign_state_payload(
        {"user_id": str(user_id), "nonce": "nonce", "exp": 4_102_444_800},
        client_secret=signing_key,
    )

    connector_github._verify_state_payload(state, client_secret=signing_key, user_id=user_id)


def test_github_oauth_state_rejects_wrong_user():
    signing_key = "state-signing-key-with-at-least-32-bytes"
    state = connector_github._sign_state_payload(
        {"user_id": str(uuid.uuid4()), "nonce": "nonce", "exp": 4_102_444_800},
        client_secret=signing_key,
    )

    with pytest.raises(Exception):
        connector_github._verify_state_payload(state, client_secret=signing_key, user_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_github_oauth_config_falls_back_to_key_vault(monkeypatch):
    monkeypatch.setattr(connector_github, "GITHUB_CLIENT_ID", "")
    monkeypatch.setattr(connector_github, "GITHUB_CLIENT_SECRET", "")

    async def fake_get_secret_value(name: str):
        return {
            connector_github.GITHUB_CLIENT_ID_SECRET_NAME: "client-id",
            connector_github.GITHUB_CLIENT_SECRET_SECRET_NAME: "client-secret",
        }[name]

    monkeypatch.setattr(connector_github, "get_secret_value", fake_get_secret_value)

    assert await connector_github._github_oauth_config() == ("client-id", "client-secret")
