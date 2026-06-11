"""Native Microsoft tool connectors: Azure CLI, Graph, Exchange, Teams, SharePoint."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import api_key_auth
from app.services.connected_account_state import (
    mark_delegated_account_disconnected,
    record_delegated_diagnosis,
    sync_delegated_account_from_token,
    upsert_delegated_account,
)
from app.services.connectors.microsoft_admin.azure_cli import (
    _list_azure_subscriptions,
    ensure_azure_cli_profile,
    validate_azure_cli_profile,
)
from app.services.connectors.microsoft_admin.constants import (
    AZURE_AUTHORITY_HOST,
    AZURE_CLI_PROVIDER,
    AZURE_TOKEN_ENDPOINT,
    EXCHANGE_ONLINE_PROVIDER,
    MICROSOFT_GRAPH_BASE_URL,
    MICROSOFT_GRAPH_PROVIDER,
    MICROSOFT_NATIVE_CONNECTOR_PROFILES,
    SHAREPOINT_PNP_PROVIDER,
    TEAMS_ADMIN_PROVIDER,
    TENANT_ID,
    microsoft_native_app_name_for_provider,
    microsoft_native_client_id_for_provider,
    microsoft_native_device_scope_string,
    microsoft_native_label_for_provider,
    microsoft_native_profile_for_provider,
    microsoft_native_provider,
    microsoft_native_scope_values,
)
from app.services.connectors.microsoft_admin.graph import _graph_error_details, _graph_response_data
from app.services.connectors.microsoft_admin.tokens import (
    extract_microsoft_admin_username,
    get_microsoft_admin_token,
    microsoft_admin_token_client_error,
    _sharepoint_scope_for_url,
)
from app.services.token_storage import delete_token, retrieve_token, store_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connector/microsoft-native", tags=["Connector"])

DEVICE_CODE_PENDING_ERRORS = {"authorization_pending", "slow_down"}
DEVICE_CODE_TERMINAL_ERRORS = {
    "authorization_declined": "Microsoft sign-in was declined.",
    "bad_verification_code": "Microsoft rejected the sign-in code. Start a new sign-in and enter the newest code.",
    "expired_token": "The Microsoft sign-in code expired before authorization completed. Start a new sign-in.",
}


def _provider_or_404(provider: str) -> str:
    normalized = microsoft_native_provider(provider)
    if not normalized:
        raise HTTPException(status_code=404, detail="Unknown Microsoft connector")
    return normalized


def _connect_unsupported(provider: str) -> dict[str, Any] | None:
    client_id = microsoft_native_client_id_for_provider(provider)
    scopes = microsoft_native_scope_values(provider)
    if not client_id:
        return {
            "status": "error",
            "error": "native_client_not_configured",
            "message": (
                f"{microsoft_native_label_for_provider(provider)} does not expose a configured native public client "
                "in this environment. This connector must use its module-native login path, not the old all-in-one app."
            ),
        }
    if provider != SHAREPOINT_PNP_PROVIDER and not scopes:
        return {
            "status": "error",
            "error": "scopes_not_configured",
            "message": f"{microsoft_native_label_for_provider(provider)} has no delegated scopes configured.",
        }
    return None


def _request_site_url(req: dict[str, Any] | None) -> str:
    return str((req or {}).get("site_url") or (req or {}).get("admin_url") or "").strip()


def _device_scope_for_request(provider: str, req: dict[str, Any] | None) -> tuple[str, str, str | None]:
    if provider != SHAREPOINT_PNP_PROVIDER:
        return (
            microsoft_native_device_scope_string(provider),
            ", ".join(microsoft_native_scope_values(provider)),
            None,
        )
    site_url = _request_site_url(req)
    scope = _sharepoint_scope_for_url(site_url)
    if not scope:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error": "site_url_required",
                "message": "SharePoint/PnP sign-in requires an HTTPS SharePoint site URL.",
            },
        )
    return f"{scope} openid profile offline_access", scope, site_url


@router.post("/{provider}/device-code")
async def start_device_code(
    provider: str,
    req: dict[str, Any] | None = Body(default=None),
    auth: dict = Depends(api_key_auth),
):
    """Start a device-code flow for one native Microsoft tool connector."""
    _ = auth
    provider_key = _provider_or_404(provider)
    unsupported = _connect_unsupported(provider_key)
    request_id = uuid.uuid4().hex[:16]
    if unsupported:
        return {**unsupported, "connector": provider_key, "request_id": request_id}

    client_id = microsoft_native_client_id_for_provider(provider_key)
    scope_string, scope_summary, site_url = _device_scope_for_request(provider_key, req)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/v2.0/devicecode",
                data={"client_id": client_id, "scope": scope_string},
            )
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            logger.warning(
                "Native Microsoft device-code start rejected provider=%s request_id=%s status=%s error=%s description=%s",
                provider_key,
                request_id,
                resp.status_code,
                data.get("error"),
                data.get("error_description"),
            )
            return {
                "status": "error",
                "connector": provider_key,
                "error": data.get("error_description") or data.get("error") or resp.text[:500],
                "error_type": data.get("error") or "device_code_start_failed",
                "request_id": request_id,
            }
        logger.info(
            "Native Microsoft device-code ready provider=%s request_id=%s app=%s expires_in=%s interval=%s scope=%s",
            provider_key,
            request_id,
            microsoft_native_app_name_for_provider(provider_key),
            data.get("expires_in", 900),
            data.get("interval", 5),
            scope_summary,
        )
        return {
            "status": "device_code_ready",
            "connector": provider_key,
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data["verification_uri"],
            "verification_url": data.get("verification_uri", "https://microsoft.com/devicelogin"),
            "interval": data.get("interval", 5),
            "expires_in": data.get("expires_in", 900),
            "expires_at": int(time.time()) + int(data.get("expires_in") or 900),
            "scope_profile": microsoft_native_profile_for_provider(provider_key),
            "scope_label": microsoft_native_label_for_provider(provider_key),
            "scope_summary": scope_summary,
            "site_url": site_url,
            "client_id": client_id,
            "auth_app_name": microsoft_native_app_name_for_provider(provider_key),
            "request_id": request_id,
        }
    except Exception as exc:
        logger.warning("Native Microsoft device-code start failed provider=%s request_id=%s: %s", provider_key, request_id, exc)
        return {
            "status": "error",
            "connector": provider_key,
            "error": "device_code_start_failed",
            "message": "Could not start Microsoft device authentication. Check connector logs with this request_id.",
            "request_id": request_id,
        }


@router.post("/{provider}/token-callback")
async def device_code_callback(
    provider: str,
    req: dict,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    """Poll one native Microsoft device code and store that connector token."""
    provider_key = _provider_or_404(provider)
    user_id = auth.get("user_id")
    device_code = req.get("device_code", "")
    if not device_code or not user_id:
        raise HTTPException(status_code=400, detail="Missing device_code or auth")

    unsupported = _connect_unsupported(provider_key)
    request_id = uuid.uuid4().hex[:16]
    if unsupported:
        return {**unsupported, "connector": provider_key, "request_id": request_id}

    client_id = microsoft_native_client_id_for_provider(provider_key)
    scope_string, scope_summary, site_url = _device_scope_for_request(provider_key, req)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                AZURE_TOKEN_ENDPOINT,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": device_code,
                    "scope": scope_string,
                    "client_info": "1",
                },
            )
        data = resp.json()
        if "error" in data:
            error_code = str(data.get("error") or "token_exchange_failed")
            is_pending = error_code in DEVICE_CODE_PENDING_ERRORS
            if not is_pending:
                logger.warning(
                    "Native Microsoft device-code token exchange rejected provider=%s request_id=%s status=%s error=%s description=%s",
                    provider_key,
                    request_id,
                    resp.status_code,
                    data.get("error"),
                    data.get("error_description"),
                )
            message = DEVICE_CODE_TERMINAL_ERRORS.get(error_code) or data.get("error_description") or error_code
            return {
                "status": "pending" if is_pending else "error",
                "connector": provider_key,
                "error": data.get("error_description", error_code),
                "error_type": error_code,
                "message": message,
                "interval": 10 if error_code == "slow_down" else None,
                "request_id": request_id,
            }

        token_payload = {
            "provider": provider_key,
            "client_id": client_id,
            "token_type": data.get("token_type"),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "scope": data.get("scope"),
            "scope_profile": microsoft_native_profile_for_provider(provider_key),
            "id_token": data.get("id_token"),
            "client_info": data.get("client_info"),
            "expires_in": data.get("expires_in"),
            "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
        }
        if site_url:
            token_payload["site_url"] = site_url
        token_payload["username"] = extract_microsoft_admin_username(token_payload)
        stored = await store_token(provider_key, user_id, token_payload)
        if not stored:
            return {
                "status": "error",
                "connector": provider_key,
                "error": "key_vault_write_failed",
                "message": "Could not store credentials securely.",
                "request_id": request_id,
            }
        await upsert_delegated_account(
            db,
            provider_key,
            user_id,
            token_data=token_payload,
            status="connected",
            username=token_payload.get("username"),
            permission_summary=f"{microsoft_native_label_for_provider(provider_key)} connected with its own native Microsoft sign-in.",
            commit=True,
        )
        return {
            "status": "connected",
            "connector": provider_key,
            "request_id": request_id,
            "scope_profile": token_payload["scope_profile"],
            "scope_label": microsoft_native_label_for_provider(provider_key),
            "scope_summary": scope_summary,
            "site_url": site_url,
            "auth_app_name": microsoft_native_app_name_for_provider(provider_key),
            "message": f"{microsoft_native_label_for_provider(provider_key)} connected.",
        }
    except Exception as exc:
        logger.warning("Native Microsoft device-code callback failed provider=%s request_id=%s: %s", provider_key, request_id, exc)
        return {
            "status": "error",
            "connector": provider_key,
            "error": "device_code_callback_failed",
            "message": "Could not complete Microsoft device authentication. Check connector logs with this request_id.",
            "request_id": request_id,
        }


@router.get("/{provider}/status")
async def microsoft_native_status(
    provider: str,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    provider_key = _provider_or_404(provider)
    user_id = auth.get("user_id")
    return await sync_delegated_account_from_token(db, provider_key, user_id, commit=True) if user_id else {"status": "not_connected"}


@router.post("/{provider}/diagnose")
async def microsoft_native_diagnose(
    provider: str,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    provider_key = _provider_or_404(provider)
    user_id = auth.get("user_id")
    result = await diagnose_microsoft_native_connection(provider_key, user_id)
    if user_id:
        await record_delegated_diagnosis(db, provider_key, user_id, result, commit=True)
    return result


@router.post("/{provider}/validate")
async def microsoft_native_validate(
    provider: str,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    return await microsoft_native_diagnose(provider=provider, auth=auth, db=db)


@router.post("/{provider}/disconnect")
async def microsoft_native_disconnect(
    provider: str,
    auth: dict = Depends(api_key_auth),
    db: AsyncSession = Depends(get_db),
):
    provider_key = _provider_or_404(provider)
    user_id = auth.get("user_id")
    if user_id:
        await delete_token(provider_key, user_id)
        await mark_delegated_account_disconnected(db, provider_key, user_id, commit=True)
    return {"status": "disconnected", "connector": provider_key}


async def diagnose_microsoft_native_connection(provider: str, user_id: Any) -> dict[str, Any]:
    provider_key = _provider_or_404(provider)
    request_id = uuid.uuid4().hex[:16]
    token_data = await retrieve_token(provider_key, user_id) if user_id else None
    if not token_data or not token_data.get("access_token"):
        return {
            "status": "failed",
            "connector": provider_key,
            "request_id": request_id,
            "message": f"{microsoft_native_label_for_provider(provider_key)} is not connected for this user.",
        }
    client_error = microsoft_admin_token_client_error({**token_data, "provider": provider_key})
    if client_error:
        return {
            "status": "failed",
            "connector": provider_key,
            "request_id": request_id,
            "error_type": "wrong_native_client",
            "message": client_error,
        }

    if provider_key == AZURE_CLI_PROVIDER:
        return await _diagnose_azure_cli(user_id, request_id)
    if provider_key == MICROSOFT_GRAPH_PROVIDER:
        return await _diagnose_graph(user_id, request_id)
    if provider_key in {EXCHANGE_ONLINE_PROVIDER, TEAMS_ADMIN_PROVIDER, SHAREPOINT_PNP_PROVIDER}:
        return await _diagnose_workload_token(provider_key, user_id, request_id, token_data)

    return {
        "status": "success",
        "connector": provider_key,
        "request_id": request_id,
        "message": (
            f"{microsoft_native_label_for_provider(provider_key)} has its own token. "
            "Actual command access is still controlled by the signed-in user's Microsoft roles and workload permissions."
        ),
        "provider_username": token_data.get("username"),
    }


async def _diagnose_workload_token(provider_key: str, user_id: Any, request_id: str, token_data: dict[str, Any]) -> dict[str, Any]:
    profile = microsoft_native_profile_for_provider(provider_key)
    context: dict[str, Any] = {}
    if provider_key == SHAREPOINT_PNP_PROVIDER:
        site_url = str(token_data.get("site_url") or "").strip()
        if not site_url:
            return {
                "status": "failed",
                "connector": provider_key,
                "request_id": request_id,
                "message": "SharePoint/PnP is connected without a site URL. Reconnect it with a SharePoint site/admin URL.",
                "error_type": "site_url_required",
            }
        context["site_url"] = site_url
    token = await get_microsoft_admin_token(user_id, profile, **context)
    if not token or not token.get("access_token") or token.get("refresh_error"):
        return {
            "status": "failed",
            "connector": provider_key,
            "request_id": request_id,
            "message": token.get("refresh_error") if isinstance(token, dict) else f"{microsoft_native_label_for_provider(provider_key)} token is not available.",
            "error_type": token.get("error_type") if isinstance(token, dict) else "not_connected",
        }
    return {
        "status": "success",
        "connector": provider_key,
        "request_id": request_id,
        "message": (
            f"{microsoft_native_label_for_provider(provider_key)} token refreshed successfully. "
            "Actual command access is still controlled by the signed-in user's Microsoft roles and workload permissions."
        ),
        "provider_username": token.get("username") or token_data.get("username"),
    }


async def _diagnose_azure_cli(user_id: Any, request_id: str) -> dict[str, Any]:
    token = await get_microsoft_admin_token(user_id, "arm")
    if not token or not token.get("access_token") or token.get("refresh_error"):
        return {
            "status": "failed",
            "connector": AZURE_CLI_PROVIDER,
            "request_id": request_id,
            "message": token.get("refresh_error") if isinstance(token, dict) else "Azure CLI token is not available.",
            "error_type": token.get("error_type") if isinstance(token, dict) else "not_connected",
        }
    subscriptions_result = await _list_azure_subscriptions(token["access_token"])
    if not subscriptions_result.get("ok"):
        return {
            "status": "failed",
            "connector": AZURE_CLI_PROVIDER,
            "request_id": request_id,
            "message": subscriptions_result.get("message", "Azure subscription discovery failed."),
            "stderr": subscriptions_result.get("stderr", ""),
        }
    profile = await ensure_azure_cli_profile(user_id, token, subscriptions_result=subscriptions_result)
    if not profile.get("ready"):
        return {
            "status": "failed",
            "connector": AZURE_CLI_PROVIDER,
            "request_id": request_id,
            "message": profile.get("message") or "Azure CLI profile could not be prepared.",
        }
    validation = await validate_azure_cli_profile(user_id)
    if not validation.get("ready"):
        return {
            "status": "failed",
            "connector": AZURE_CLI_PROVIDER,
            "request_id": request_id,
            "message": validation.get("message") or "Azure CLI profile validation failed.",
            "stderr": validation.get("stderr", ""),
        }
    subscriptions = subscriptions_result.get("subscriptions", [])
    return {
        "status": "success",
        "connector": AZURE_CLI_PROVIDER,
        "request_id": request_id,
        "message": "Azure CLI is connected and the native az profile is ready.",
        "subscriptions_count": len(subscriptions),
        "subscriptions": [
            {
                "subscription_id": sub.get("subscriptionId"),
                "display_name": sub.get("displayName"),
                "state": sub.get("state"),
            }
            for sub in subscriptions[:10]
        ],
    }


async def _diagnose_graph(user_id: Any, request_id: str) -> dict[str, Any]:
    token = await get_microsoft_admin_token(user_id, "graph")
    if not token or not token.get("access_token") or token.get("refresh_error"):
        return {
            "status": "failed",
            "connector": MICROSOFT_GRAPH_PROVIDER,
            "request_id": request_id,
            "message": token.get("refresh_error") if isinstance(token, dict) else "Microsoft Graph token is not available.",
            "error_type": token.get("error_type") if isinstance(token, dict) else "not_connected",
        }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MICROSOFT_GRAPH_BASE_URL.rstrip('/')}/v1.0/me?$select=id,displayName,userPrincipalName,mail",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
    graph_data = _graph_response_data(response)
    error_type, graph_message = _graph_error_details(graph_data, response.status_code)
    if response.status_code >= 400:
        return {
            "status": "failed",
            "connector": MICROSOFT_GRAPH_PROVIDER,
            "request_id": request_id,
            "message": graph_message or "Microsoft Graph validation failed.",
            "error_type": error_type or "graph_validation_failed",
            "status_code": response.status_code,
        }
    return {
        "status": "success",
        "connector": MICROSOFT_GRAPH_PROVIDER,
        "request_id": request_id,
        "message": "Microsoft Graph is connected and /me validation succeeded.",
        "graph_user": graph_data if isinstance(graph_data, dict) else {},
    }
