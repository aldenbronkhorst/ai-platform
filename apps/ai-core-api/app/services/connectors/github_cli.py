"""GitHub CLI connector helpers."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import uuid
from typing import Any, Optional
from uuid import UUID

import httpx

from app.services.ops_command_runner import run_command
from app.services.token_storage import retrieve_token

GITHUB_HOST = os.environ.get("GITHUB_HOST", "github.com")
GITHUB_ALLOWED_BINARIES = {"gh", "git", "jq", "rg", "which"}

def _atomic_write(path: Path, content: str, mode: int) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.chmod(tmp_path, mode)
    tmp_path.replace(path)

async def run_github_cli_command(command: str, user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:16]
    token_data = await retrieve_token("github", user_id) if user_id else None
    access_token = token_data.get("access_token") if token_data else None
    normalized = command.strip()
    if not access_token:
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "timed_out": False,
            "output_truncated": False,
            "stdout_chars": 0,
            "stderr_chars": 0,
            "error": "GitHub is not connected for this user.",
            "command": normalized,
            "connector": "github_cli",
            "request_id": request_id,
            "status": "failed",
            "auth_method": "not_connected",
        }

    profile = await ensure_github_cli_profile(user_id, token_data) if user_id else {"ready": False}
    if not profile.get("ready"):
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "timed_out": False,
            "output_truncated": False,
            "stdout_chars": 0,
            "stderr_chars": 0,
            "error": profile.get("message", "GitHub CLI profile could not be prepared for this user."),
            "command": normalized,
            "connector": "github_cli",
            "request_id": request_id,
            "status": "failed",
            "auth_method": "user_scoped_gh_cli",
        }

    env: dict[str, str] = {"GH_CONFIG_DIR": _github_config_dir(user_id)}

    result = await run_command(
        normalized,
        timeout=timeout,
        env=env,
        allowed_binaries=GITHUB_ALLOWED_BINARIES,
    )
    output = result.to_dict()
    output.update({
        "command": normalized,
        "connector": "github_cli",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "user_scoped_gh_cli",
    })
    return output


def _github_config_dir(user_id: UUID) -> str:
    base = os.environ.get("GITHUB_CLI_USER_CONFIG_ROOT", "/tmp/ai-platform-github-cli")
    path = os.path.join(base, user_id.hex)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


async def _fetch_github_user(access_token: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if response.status_code >= 400:
            return {
                "ok": False,
                "message": f"GitHub token check failed with HTTP {response.status_code}.",
                "stderr": response.text[:1000],
            }
        user = response.json()
        return {
            "ok": True,
            "login": user.get("login"),
            "scopes": response.headers.get("X-OAuth-Scopes", ""),
        }
    except Exception as exc:
        return {"ok": False, "message": f"GitHub diagnostics failed: {exc}"}


async def ensure_github_cli_profile(
    user_id: UUID,
    token_data: dict[str, Any],
    login: Optional[str] = None,
) -> dict[str, Any]:
    access_token = token_data.get("access_token")
    if not access_token:
        return {"ready": False, "message": "GitHub is not connected for this user."}

    login = login or token_data.get("login") or token_data.get("username")
    if not login:
        user_result = await _fetch_github_user(access_token)
        if not user_result.get("ok"):
            return {"ready": False, "message": user_result.get("message", "GitHub token check failed.")}
        login = user_result.get("login")

    await asyncio.to_thread(_write_github_cli_files, _github_config_dir(user_id), access_token, login or "")
    return {"ready": True, "login": login, "config_dir": _github_config_dir(user_id)}


def _write_github_cli_files(config_dir: str, access_token: str, login: str) -> None:
    path = Path(config_dir)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    hosts = (
        f"{GITHUB_HOST}:\n"
        f"    oauth_token: {json.dumps(access_token)}\n"
        f"    user: {json.dumps(login)}\n"
        "    git_protocol: https\n"
    )
    _atomic_write(path / "hosts.yml", hosts, mode=0o600)


async def validate_github_cli_profile(user_id: UUID, timeout: int = 20) -> dict[str, Any]:
    env = {"GH_CONFIG_DIR": _github_config_dir(user_id)}
    result = await run_command(
        f"gh api user --jq .login --hostname {GITHUB_HOST}",
        timeout=timeout,
        env=env,
        allowed_binaries=GITHUB_ALLOWED_BINARIES,
    )
    if result.success:
        return {"ready": True, "login": result.stdout.strip()}
    output = result.to_dict()
    return {
        "ready": False,
        "message": output.get("error") or output.get("stderr") or "GitHub CLI profile validation failed.",
        "stderr": output.get("stderr", ""),
        "exit_code": output.get("exit_code"),
    }


async def diagnose_github_connection(user_id: Optional[UUID]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:16]
    token_data = await retrieve_token("github", user_id) if user_id else None
    access_token = token_data.get("access_token") if token_data else None
    if not access_token:
        return {
            "status": "failed",
            "connector": "github_cli",
            "request_id": request_id,
            "message": "GitHub is not connected for this user.",
        }

    user_result = await _fetch_github_user(access_token)
    if not user_result.get("ok"):
        return {
            "status": "failed",
            "connector": "github_cli",
            "request_id": request_id,
            "message": user_result.get("message", "GitHub token check failed."),
            "stderr": user_result.get("stderr", ""),
        }

    if user_id:
        profile = await ensure_github_cli_profile(user_id, token_data, login=user_result.get("login"))
        if not profile.get("ready"):
            return {
                "status": "failed",
                "connector": "github_cli",
                "request_id": request_id,
                "message": profile.get("message", "GitHub CLI profile could not be prepared for this user."),
                "cli_profile_ready": False,
                "login": user_result.get("login"),
                "scopes": user_result.get("scopes", ""),
            }
        cli_check = await validate_github_cli_profile(user_id)
        if not cli_check.get("ready"):
            return {
                "status": "failed",
                "connector": "github_cli",
                "request_id": request_id,
                "message": cli_check.get("message", "GitHub CLI profile validation failed."),
                "stderr": cli_check.get("stderr", ""),
                "cli_profile_ready": False,
                "login": user_result.get("login"),
                "scopes": user_result.get("scopes", ""),
            }

    return {
        "status": "success",
        "connector": "github_cli",
        "request_id": request_id,
        "message": f"GitHub token and CLI profile are valid for {user_result.get('login', 'the connected user')}.",
        "login": user_result.get("login"),
        "scopes": user_result.get("scopes", ""),
        "cli_profile_ready": True,
    }
