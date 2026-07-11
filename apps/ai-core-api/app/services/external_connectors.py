"""Manifest-driven bridge between AI Platform and connector packages."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIConnectedAccount, AITool
from app.services.key_vault import get_secret_value, key_vault_uri


logger = logging.getLogger(__name__)

CONNECTOR_ENDPOINTS_ENV = "CONNECTOR_ENDPOINTS_JSON"
DEFAULT_CONNECTOR_KEY_ENV = "CONNECTOR_INTERNAL_API_KEY"
CONNECTOR_ERROR_MAX_CHARS = 1200
_CONNECTOR_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_manifest_cache: dict[str, dict[str, Any]] = {}


class ConnectorRequestError(RuntimeError):
    def __init__(self, status_code: int, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("message") or "Connector request failed."))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ConnectorEndpoint:
    id: str
    base_url: str
    api_key_env: str
    broker_target: str

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    def headers(self, *, json_content: bool = False) -> dict[str, str]:
        headers = {"X-Internal-API-Key": self.api_key}
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def url_for(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


def _configured_connector_map() -> dict[str, ConnectorEndpoint]:
    raw = os.environ.get(CONNECTOR_ENDPOINTS_ENV, "{}").strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Invalid %s: %s", CONNECTOR_ENDPOINTS_ENV, exc)
        return {}
    if not isinstance(payload, dict):
        logger.error("%s must be a JSON object", CONNECTOR_ENDPOINTS_ENV)
        return {}

    result: dict[str, ConnectorEndpoint] = {}
    for raw_id, value in payload.items():
        connector_id = str(raw_id or "").strip().lower()
        if not _CONNECTOR_ID.fullmatch(connector_id):
            logger.warning("Ignoring invalid connector id %r", raw_id)
            continue
        config = {"base_url": value} if isinstance(value, str) else value
        if not isinstance(config, dict):
            continue
        base_url = str(config.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            continue
        result[connector_id] = ConnectorEndpoint(
            id=connector_id,
            base_url=base_url,
            api_key_env=str(config.get("api_key_env") or DEFAULT_CONNECTOR_KEY_ENV),
            broker_target=str(config.get("broker_target") or connector_id),
        )
    return result


def configured_connector_ids() -> tuple[str, ...]:
    return tuple(sorted(_configured_connector_map()))


def configured_connector_tool_names() -> frozenset[str]:
    return frozenset(endpoint.broker_target for endpoint in _configured_connector_map().values())


def connector_endpoint(connector_id: str) -> ConnectorEndpoint | None:
    return _configured_connector_map().get(str(connector_id or "").strip().lower())


def connector_for_tool_name(tool_name: str) -> ConnectorEndpoint | None:
    for endpoint in _configured_connector_map().values():
        if tool_name in {endpoint.id, endpoint.broker_target}:
            return endpoint
    return None


def is_external_connector_tool(tool_name: str) -> bool:
    return connector_for_tool_name(tool_name) is not None


def clear_connector_manifest_cache() -> None:
    _manifest_cache.clear()


def _truncate(value: str, limit: int = CONNECTOR_ERROR_MAX_CHARS) -> str:
    return value if len(value) <= limit else f"{value[:limit]}... [truncated {len(value) - limit} chars]"


def connector_error_payload(raw_detail: Any, default_message: str = "") -> dict[str, Any]:
    detail = raw_detail.get("detail") if isinstance(raw_detail, dict) and "detail" in raw_detail else raw_detail
    if not isinstance(detail, dict):
        return {
            "error_type": "connector_http_error",
            "message": _truncate(str(detail or default_message or "Connector returned an error.")),
        }
    error_type = str(detail.get("error_type") or detail.get("error") or "connector_error")
    raw_message = detail.get("message") or detail.get("detail") or default_message or error_type
    message = json.dumps(raw_message, ensure_ascii=False, default=str) if isinstance(raw_message, (dict, list)) else str(raw_message)
    safe: dict[str, Any] = {"error_type": error_type, "message": _truncate(message)}
    for key in ("field", "suggestion", "correlation_id", "status_code"):
        if detail.get(key) not in (None, ""):
            safe[key] = detail[key]
    return safe


async def load_connector_manifest(connector_id: str, *, force: bool = False) -> dict[str, Any]:
    if not force and connector_id in _manifest_cache:
        return _manifest_cache[connector_id]
    endpoint = _configured_connector_map().get(connector_id)
    if endpoint is None:
        raise RuntimeError(f"Connector package {connector_id!r} is not configured")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(endpoint.url_for("/connector/manifest"), headers=endpoint.headers())
    if response.status_code >= 400:
        raise RuntimeError(connector_error_payload(await _response_json(response), response.text)["message"])
    manifest = await _response_json(response)
    if not isinstance(manifest, dict) or str(manifest.get("id") or "") != connector_id:
        raise RuntimeError(f"Connector package {connector_id!r} returned an invalid manifest")
    if str(manifest.get("broker_target") or connector_id) != endpoint.broker_target:
        raise RuntimeError(f"Connector package {connector_id!r} broker target does not match its registration")
    _manifest_cache[connector_id] = manifest
    return manifest


async def load_connector_manifests() -> dict[str, dict[str, Any]]:
    manifests: dict[str, dict[str, Any]] = {}
    for connector_id in configured_connector_ids():
        try:
            manifests[connector_id] = await load_connector_manifest(connector_id)
        except Exception as exc:
            logger.warning("Could not load connector manifest %s: %s", connector_id, exc)
    return manifests


def _selected_tool_names(tools: list[AITool]) -> set[str]:
    return {str(tool.name or "") for tool in tools}


async def connector_skill_context(
    connected_systems: set[str],
    tools: list[AITool],
    *,
    workspace_tool_name: str,
) -> str:
    if workspace_tool_name not in _selected_tool_names(tools):
        return ""
    manifests = await load_connector_manifests()
    entries: list[str] = []
    for connector_id in sorted(connected_systems):
        manifest = manifests.get(connector_id)
        if not manifest:
            continue
        skill = manifest.get("skill") if isinstance(manifest.get("skill"), dict) else {}
        name = str(skill.get("name") or connector_id)
        description = str(skill.get("description") or manifest.get("subtitle") or "Connected system")
        target = str(manifest.get("broker_target") or connector_id)
        entries.append(f"- {name}: {description} Connector target: `{target}`.")
    if not entries:
        return ""
    return (
        "## Connector Skills\n"
        "Load a relevant connector-owned skill before using that connector. In Workspace, load one with "
        "`call('<connector>', {'operation': 'guidance'})`. The connector package remains the source of truth.\n"
        + "\n".join(entries)
    )


async def _connected_account(db: AsyncSession, user_id: UUID, connector_id: str) -> AIConnectedAccount:
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == connector_id,
            or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise RuntimeError(f"No {connector_id} connected account found for tool execution")
    return account


def _secret_field_names(manifest: dict[str, Any]) -> list[str]:
    fields = manifest.get("connection_fields")
    if not isinstance(fields, list):
        return []
    return [str(field.get("name")) for field in fields if isinstance(field, dict) and field.get("secret") and field.get("name")]


async def resolve_connector_values(
    db: AsyncSession,
    user_id: UUID,
    connector_id: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    account = await _connected_account(db, user_id, connector_id)
    values = dict(account.configuration_json or {})
    secret = ""
    if account.secret_reference and key_vault_uri():
        secret = await get_secret_value(account.secret_reference)
    if not secret:
        raise RuntimeError(f"{connector_id} connected account has no valid credentials")
    try:
        parsed_secret = json.loads(secret)
    except json.JSONDecodeError:
        parsed_secret = None
    if isinstance(parsed_secret, dict):
        values.update(parsed_secret)
    else:
        secret_fields = _secret_field_names(manifest)
        if len(secret_fields) != 1:
            raise RuntimeError(f"{connector_id} stored credentials do not match its manifest")
        values[secret_fields[0]] = secret
    return values


async def _response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"message": response.text}


async def verify_connector_values(connector_id: str, values: dict[str, Any]) -> dict[str, Any]:
    endpoint = connector_endpoint(connector_id)
    if endpoint is None:
        raise RuntimeError(f"Connector package {connector_id!r} is not configured")
    manifest = await load_connector_manifest(connector_id)
    endpoints = manifest.get("endpoints") if isinstance(manifest.get("endpoints"), dict) else {}
    path = str(endpoints.get("verify") or "/connector/verify")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            endpoint.url_for(path),
            json={"values": values},
            headers=endpoint.headers(json_content=True),
        )
    body = await _response_json(response)
    if response.status_code >= 400:
        detail = connector_error_payload(body, response.text)
        raise ConnectorRequestError(response.status_code, detail)
    if not isinstance(body, dict):
        raise RuntimeError(f"Connector package {connector_id!r} returned an invalid verification response")
    return body


async def execute_external_connector_tool(
    db: AsyncSession,
    user_id: UUID,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    endpoint = connector_for_tool_name(tool_name)
    if endpoint is None:
        return {"error": True, "status": "failed", "error_type": "unknown_connector_tool", "message": f"Unknown external connector tool: {tool_name}"}
    try:
        manifest = await load_connector_manifest(endpoint.id)
        endpoints = manifest.get("endpoints") if isinstance(manifest.get("endpoints"), dict) else {}
        operation = str(arguments.get("operation") or "")
        if operation == "guidance":
            path = str(endpoints.get("guidance") or "")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint.url_for(path), headers=endpoint.headers())
        else:
            path = str(endpoints.get("run") or "")
            credentialless = set(manifest.get("credentialless_operations") or [])
            payload = dict(arguments)
            if operation not in credentialless:
                payload = {
                    "credentials": await resolve_connector_values(db, user_id, endpoint.id, manifest),
                    **payload,
                }
            timeout = float(manifest.get("run_timeout_seconds") or 120)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(endpoint.url_for(path), json=payload, headers=endpoint.headers(json_content=True))
        body = await _response_json(response)
        if response.status_code >= 400:
            detail = connector_error_payload(body, response.text)
            return {
                "error": True,
                "status": "failed",
                "status_code": response.status_code,
                "connector_error": detail,
                "error_type": detail["error_type"],
                "message": detail["message"],
            }
        return body
    except Exception as exc:
        logger.warning("Connector call failed | connector=%s error=%s", endpoint.id, exc)
        return {
            "error": True,
            "status": "failed",
            "error_type": type(exc).__name__,
            "message": _truncate(str(exc) or "Connector call failed."),
        }
