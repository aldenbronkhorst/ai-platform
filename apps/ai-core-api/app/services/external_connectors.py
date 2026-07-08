"""External connector package runtime.

AI Core owns orchestration. Connector packages own their skill text and raw
service endpoints. This module is the thin bridge between the two.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIConnectedAccount, AITool
from app.services.key_vault import get_secret_value, key_vault_uri

logger = logging.getLogger(__name__)

CONNECTOR_SKILL_MAX_CHARS = int(os.environ.get("CONNECTOR_SKILL_MAX_CHARS", "24000"))
CONNECTOR_SKILL_TIMEOUT_SECONDS = float(os.environ.get("CONNECTOR_SKILL_TIMEOUT_SECONDS", "8"))
CONNECTOR_ERROR_MAX_CHARS = 1200


@dataclass(frozen=True)
class ExternalConnector:
    id: str
    display_name: str
    broker_target: str
    connected_account_provider: str
    base_url_env: str
    api_key_env: str
    guidance_path: str
    run_path: str
    credentialless_operations: frozenset[str] = frozenset()
    run_timeout_seconds: float = 120.0
    guidance_timeout_seconds: float = CONNECTOR_SKILL_TIMEOUT_SECONDS

    @property
    def base_url(self) -> str:
        return os.environ.get(self.base_url_env, "").rstrip("/")

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    def headers(self, *, json_content: bool = False) -> dict[str, str]:
        headers = {"X-Internal-API-Key": self.api_key}
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def url_for(self, path: str) -> str:
        base_url = self.base_url
        return f"{base_url}{path}" if base_url else ""


EXTERNAL_CONNECTORS: dict[str, ExternalConnector] = {
    "odoo": ExternalConnector(
        id="odoo",
        display_name="Odoo",
        broker_target="odoo",
        connected_account_provider="odoo",
        base_url_env="ODOO_CONNECTOR_URL",
        api_key_env="ODOO_CONNECTOR_API_KEY",
        guidance_path="/odoo/guidance",
        run_path="/odoo/orm/run",
        credentialless_operations=frozenset({"playbook"}),
    ),
}

EXTERNAL_CONNECTOR_TYPES = tuple(EXTERNAL_CONNECTORS)
EXTERNAL_CONNECTOR_DISPLAY_NAMES = {
    connector.id: connector.display_name
    for connector in EXTERNAL_CONNECTORS.values()
}
EXTERNAL_CONNECTOR_TOOL_NAMES = frozenset(
    connector.broker_target
    for connector in EXTERNAL_CONNECTORS.values()
)


def connector_for_tool_name(tool_name: str) -> ExternalConnector | None:
    for connector in EXTERNAL_CONNECTORS.values():
        if tool_name in {connector.id, connector.broker_target}:
            return connector
    return None


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... [truncated {len(value) - limit} chars]"


def _truncate_connector_skill(content: str) -> str:
    if len(content) <= CONNECTOR_SKILL_MAX_CHARS:
        return content
    omitted = len(content) - CONNECTOR_SKILL_MAX_CHARS
    return content[:CONNECTOR_SKILL_MAX_CHARS].rstrip() + f"\n\n[connector skill truncated by {omitted} characters]"


def connector_error_payload(raw_detail: Any, default_message: str = "") -> dict[str, Any]:
    detail = raw_detail.get("detail") if isinstance(raw_detail, dict) and "detail" in raw_detail else raw_detail
    if not isinstance(detail, dict):
        message = str(detail or default_message or "Connector returned an error.")
        return {
            "error_type": "connector_http_error",
            "message": _truncate(message, CONNECTOR_ERROR_MAX_CHARS),
        }

    error_type = str(detail.get("error_type") or detail.get("error") or "connector_error")
    raw_message = detail.get("message") or detail.get("detail") or default_message or error_type
    message = json.dumps(raw_message, ensure_ascii=False, default=str) if isinstance(raw_message, (dict, list)) else str(raw_message)

    safe: dict[str, Any] = {
        "error_type": error_type,
        "message": _truncate(message, CONNECTOR_ERROR_MAX_CHARS),
    }
    for key in ("model", "field", "suggestion", "correlation_id", "status_code"):
        if key in detail and detail[key] not in (None, ""):
            safe[key] = detail[key]
    return safe


def _selected_tool_names(tools: list[AITool]) -> set[str]:
    return {str(tool.name or "") for tool in tools}


def _selected_connector_skill_systems(
    connected_systems: set[str],
    tools: list[AITool],
    *,
    workspace_tool_name: str,
) -> list[str]:
    tool_names = _selected_tool_names(tools)
    uses_workspace = workspace_tool_name in tool_names
    systems: list[str] = []
    for connector in EXTERNAL_CONNECTORS.values():
        if connector.connected_account_provider not in connected_systems:
            continue
        if not uses_workspace and connector.broker_target not in tool_names and connector.id not in tool_names:
            continue
        systems.append(connector.id)
    return systems


async def fetch_connector_skill(connector: ExternalConnector) -> str | None:
    url = connector.url_for(connector.guidance_path)
    if not url:
        return None
    async with httpx.AsyncClient(timeout=connector.guidance_timeout_seconds) as client:
        response = await client.get(url, headers=connector.headers())
    if response.status_code >= 400:
        logger.warning("%s connector skill fetch failed with status %s", connector.id, response.status_code)
        return None
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    version = str(payload.get("version") or "unknown")
    source = str(payload.get("source") or "connector package")
    return (
        f"### {connector.display_name} Connector Skill\n"
        f"Version: {version}\n"
        f"Source: {source}\n\n"
        f"{_truncate_connector_skill(content)}"
    )


async def connector_skill_context(
    connected_systems: set[str],
    tools: list[AITool],
    *,
    workspace_tool_name: str,
) -> str:
    systems = _selected_connector_skill_systems(
        connected_systems,
        tools,
        workspace_tool_name=workspace_tool_name,
    )
    if not systems:
        return ""

    sections: list[str] = []
    for system in systems:
        connector = EXTERNAL_CONNECTORS.get(system)
        if connector is None:
            continue
        try:
            skill = await fetch_connector_skill(connector)
        except Exception as exc:
            logger.warning("Failed to fetch %s connector skill: %s", system, exc)
            skill = None
        if skill:
            sections.append(skill)

    if not sections:
        return ""
    return (
        "## Connector Skills\n"
        "The following skill text is owned by the connector package. Use it with Workspace and the connector broker target; "
        "do not invent connector-specific API flows when the skill gives the raw method flow.\n\n"
        + "\n\n".join(sections)
    )


async def resolve_connector_credentials(
    db: AsyncSession,
    user_id: UUID,
    connector: ExternalConnector,
) -> dict[str, str]:
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == connector.connected_account_provider,
            or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise RuntimeError(f"No {connector.display_name} connected account found for tool execution")

    api_key = ""
    if account.secret_reference and key_vault_uri():
        try:
            api_key = await get_secret_value(account.secret_reference)
        except Exception as exc:
            raise RuntimeError(f"Failed to retrieve {connector.display_name} credentials from Key Vault: {exc}") from exc

    if not api_key:
        raise RuntimeError(f"{connector.display_name} connected account has no valid credentials")

    if connector.id == "odoo":
        odoo_url = (account.odoo_url or "").strip()
        odoo_db = (account.odoo_db or "").strip()
        if not odoo_url or not odoo_db:
            raise RuntimeError("Odoo connected account is missing its saved URL or database")
        logger.info(
            "Resolved %s credentials for tool execution: user=%s host=%s db=%s",
            connector.display_name,
            account.provider_username,
            odoo_url,
            odoo_db,
        )
        return {
            "url": odoo_url,
            "db": odoo_db,
            "username": account.provider_username or "",
            "api_key": api_key,
        }

    raise RuntimeError(f"{connector.display_name} connector credentials are not mapped in AI Core")


async def execute_external_connector_tool(
    db: AsyncSession,
    user_id: UUID,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    connector = connector_for_tool_name(tool_name)
    if connector is None:
        return {
            "error": True,
            "status": "failed",
            "error_type": "unknown_connector_tool",
            "message": f"Unknown external connector tool: {tool_name}",
        }

    if arguments.get("operation") == "guidance":
        url = connector.url_for(connector.guidance_path)
        if not url:
            return {
                "error": True,
                "error_type": "connector_url_not_configured",
                "message": f"{connector.display_name} connector URL is not configured.",
            }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=connector.headers())
        if response.status_code >= 400:
            try:
                raw_detail = response.json()
            except Exception:
                raw_detail = {"error_type": "connector_http_error", "message": response.text}
            detail = connector_error_payload(raw_detail, response.text)
            return {
                "error": True,
                "status_code": response.status_code,
                "connector_error": detail,
                "error_type": detail.get("error_type") or "connector_error",
                "message": detail.get("message") or "Connector returned an error.",
            }
        return response.json()

    if str(arguments.get("operation") or "") in connector.credentialless_operations:
        url = connector.url_for(connector.run_path)
        if not url:
            return {
                "error": True,
                "error_type": "connector_url_not_configured",
                "message": f"{connector.display_name} connector URL is not configured.",
            }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=arguments, headers=connector.headers(json_content=True))
        if response.status_code >= 400:
            try:
                raw_detail = response.json()
            except Exception:
                raw_detail = {"error_type": "connector_http_error", "message": response.text}
            detail = connector_error_payload(raw_detail, response.text)
            return {
                "error": True,
                "status_code": response.status_code,
                "connector_error": detail,
                "error_type": detail.get("error_type") or "connector_error",
                "message": detail.get("message") or "Connector returned an error.",
            }
        return response.json()

    credentials = await resolve_connector_credentials(db, user_id, connector)
    payload = {
        "credentials": credentials,
        "identity_mode": "user-delegated",
        **arguments,
    }
    url = connector.url_for(connector.run_path)
    if not url:
        return {
            "error": True,
            "error_type": "connector_url_not_configured",
            "message": f"{connector.display_name} connector URL is not configured.",
        }

    async with httpx.AsyncClient(timeout=connector.run_timeout_seconds) as client:
        response = await client.post(url, json=payload, headers=connector.headers(json_content=True))
    if response.status_code >= 400:
        try:
            raw_detail = response.json()
        except Exception:
            raw_detail = {"error_type": "connector_http_error", "message": response.text}
        detail = connector_error_payload(raw_detail, response.text)
        return {
            "error": True,
            "status_code": response.status_code,
            "connector_error": detail,
            "error_type": detail.get("error_type") or "connector_error",
            "message": detail.get("message") or "Connector returned an error.",
        }
    return response.json()
