"""Token retrieval and identity helpers for native Microsoft tool connectors."""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.services.token_storage import retrieve_token, store_token
from app.services.connectors.microsoft_admin.constants import (
    AZURE_ARM_SCOPE,
    AZURE_TOKEN_ENDPOINT,
    AZURE_V1_TOKEN_ENDPOINT,
    EXCHANGE_ONLINE_SCOPE,
    MICROSOFT_ADMIN_SCOPE_PROFILES,
    MICROSOFT_GRAPH_SCOPE,
    TEAMS_TENANT_ADMIN_SCOPE,
    microsoft_native_client_id_for_provider,
    microsoft_native_device_scope_string,
    microsoft_native_oauth_flow_for_provider,
    microsoft_native_provider,
    microsoft_native_provider_for_profile,
    microsoft_native_resource_for_provider,
    microsoft_admin_scope_label,
    microsoft_admin_scope_profile,
)

logger = logging.getLogger(__name__)

def microsoft_admin_token_client_error(token_data: dict[str, Any] | None) -> str:
    if not token_data:
        return ""
    provider = (
        microsoft_native_provider(token_data.get("provider"))
        or microsoft_native_provider_for_profile(token_data.get("scope_profile"))
    )
    expected_client_id = microsoft_native_client_id_for_provider(provider)
    client_id = str(token_data.get("client_id") or "").strip()
    if expected_client_id and client_id == expected_client_id:
        return ""
    if not client_id:
        return "Stored Microsoft token is missing its application identity. Reconnect this Microsoft connector."
    if not expected_client_id:
        return "This Microsoft connector does not have a native public client configured."
    return "Stored Microsoft token was issued for a different native tool. Reconnect this Microsoft connector."


def _invalid_microsoft_admin_token(token_data: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "client_id": token_data.get("client_id"),
        "scope_profile": token_data.get("scope_profile"),
        "username": token_data.get("username"),
        "refresh_error": message,
        "error_type": "reconnect_required",
    }


def _microsoft_admin_scope_unavailable(
    token_data: dict[str, Any],
    scope_profile: str | None,
    message: str,
    error_type: str,
) -> dict[str, Any]:
    return {
        "client_id": token_data.get("client_id"),
        "scope_profile": scope_profile,
        "username": token_data.get("username"),
        "refresh_error": message,
        "error_type": error_type,
    }


def _microsoft_admin_oauth_error_type(data: dict[str, Any]) -> str:
    error = str(data.get("error") or "").lower()
    description = str(data.get("error_description") or "").lower()
    if (
        "aadsts65001" in description
        or "consent" in description
        or "not been consented" in description
    ):
        return "consent_required"
    if error == "invalid_scope" or "aadsts70011" in description:
        return "invalid_scope"
    if error == "invalid_grant":
        return "authorization_failed"
    return error or "token_refresh_failed"


def _microsoft_admin_oauth_error_message(scope_profile: str | None, data: dict[str, Any], default_message: str) -> str:
    label = microsoft_admin_scope_label(scope_profile) if scope_profile else "requested Microsoft scope"
    error_type = _microsoft_admin_oauth_error_type(data)
    if scope_profile == "teams" and error_type in {"consent_required", "invalid_scope"}:
        return (
            "Skype and Teams Tenant Admin API delegated user_impersonation is missing or not consented. "
            "Configure a native Teams Admin client for the Teams connector and grant tenant admin consent."
        )
    if error_type == "consent_required":
        return (
            f"Tenant admin consent is required for {label}. "
            "Grant consent for that native Microsoft connector, then reconnect it."
        )
    return data.get("error_description") or data.get("error") or default_message

async def _get_fresh_microsoft_admin_token_for_scope(
    user_id: Optional[UUID],
    scope: str,
) -> Optional[dict[str, Any]]:
    """Return a fresh Microsoft token for a requested native Microsoft resource."""
    if not user_id:
        return None

    scope_profile = _scope_profile_for_scope(scope)
    provider = microsoft_native_provider_for_profile(scope_profile)
    token_data = await retrieve_token(provider, user_id)
    if not token_data:
        return None
    token_data = {**token_data, "provider": provider, "scope_profile": scope_profile or token_data.get("scope_profile")}
    client_error = microsoft_admin_token_client_error(token_data)
    if client_error:
        return _invalid_microsoft_admin_token(token_data, client_error)
    if scope_profile and token_data.get("scope_profile") == scope_profile:
        if scope_profile == "sharepoint":
            token_scope = str(token_data.get("scope") or "")
            if scope not in token_scope.split() and token_scope != scope:
                token_data = {**token_data, "scope_mismatch": True}
            else:
                token_data = {**token_data, "scope_mismatch": False}
        expires_on = _expires_on(token_data)
        if (
            token_data.get("access_token")
            and not token_data.get("scope_mismatch")
            and (not expires_on or expires_on > int(time.time()) + 300)
        ):
            return token_data
    cached_token = (token_data.get("delegated_tokens") or {}).get(scope_profile) if scope_profile else None
    cached_token_is_current = isinstance(cached_token, dict) and not microsoft_admin_token_client_error({**cached_token, "provider": provider, "scope_profile": scope_profile})
    if scope_profile == "sharepoint" and cached_token_is_current:
        cached_scope = str(cached_token.get("scope") or "")
        cached_token_is_current = scope in cached_scope.split() or cached_scope == scope
    if cached_token_is_current:
        expires_on = _expires_on(cached_token)
        if cached_token.get("access_token") and (not expires_on or expires_on > int(time.time()) + 300):
            return {**token_data, **cached_token}

    refresh_token = cached_token.get("refresh_token") if cached_token_is_current else None
    refresh_token = refresh_token or token_data.get("refresh_token")
    if not refresh_token:
        profile_name = microsoft_admin_scope_label(scope_profile) if scope_profile else "requested Microsoft scope"
        return _microsoft_admin_scope_unavailable(
            token_data,
            scope_profile,
            f"Stored {profile_name} token has no refresh token. Reconnect that native Microsoft connector.",
            "reconnect_required",
        )
    client_id = microsoft_native_client_id_for_provider(provider)
    if not client_id:
        return _microsoft_admin_scope_unavailable(
            token_data,
            scope_profile,
            f"{microsoft_admin_scope_label(scope_profile)} has no native public client configured in this environment.",
            "native_client_not_configured",
        )
    oauth_flow = microsoft_native_oauth_flow_for_provider(provider)
    if oauth_flow == "v1_resource":
        resource = microsoft_native_resource_for_provider(provider)
        endpoint = AZURE_V1_TOKEN_ENDPOINT
        refresh_payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": resource,
        }
    else:
        scope_request = _microsoft_admin_scope_request(scope, scope_profile)
        endpoint = AZURE_TOKEN_ENDPOINT
        refresh_payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": scope_request,
            "client_info": "1",
        }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                endpoint,
                data=refresh_payload,
            )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            error_type = _microsoft_admin_oauth_error_type(data)
            return _microsoft_admin_scope_unavailable(
                token_data,
                scope_profile,
                _microsoft_admin_oauth_error_message(scope_profile, data, response.text[:500]),
                error_type,
            )
        scoped_token = {
            **token_data,
            "client_id": client_id,
            "token_type": data.get("token_type", token_data.get("token_type")),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token", refresh_token),
            "scope": data.get("scope", scope),
            "resource": data.get("resource", token_data.get("resource")),
            "id_token": data.get("id_token", token_data.get("id_token")),
            "id_token_claims": _microsoft_identity_claims({"id_token": data.get("id_token")}) or token_data.get("id_token_claims"),
            "client_info": data.get("client_info", token_data.get("client_info")),
            "expires_in": data.get("expires_in"),
            "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
        }
        if scope_profile:
            delegated_tokens = dict(token_data.get("delegated_tokens") or {})
            delegated_tokens[scope_profile] = {
                "client_id": client_id,
                "token_type": scoped_token.get("token_type"),
                "access_token": scoped_token.get("access_token"),
                "refresh_token": scoped_token.get("refresh_token", refresh_token),
                "scope": scoped_token.get("scope"),
                "resource": scoped_token.get("resource"),
                "id_token": scoped_token.get("id_token"),
                "id_token_claims": scoped_token.get("id_token_claims"),
                "client_info": scoped_token.get("client_info"),
                "expires_in": scoped_token.get("expires_in"),
                "expires_on": scoped_token.get("expires_on"),
            }
            consented = set(token_data.get("consented_scope_profiles") or [])
            consented.add(scope_profile)
            await store_token(
                provider,
                user_id,
                {
                    **token_data,
                    "provider": provider,
                    "delegated_tokens": delegated_tokens,
                    "consented_scope_profiles": sorted(consented),
                },
            )
        return scoped_token
    except Exception as exc:
        logger.warning("Microsoft scoped token refresh failed for user %s scope=%s: %s", user_id.hex[:12], scope, exc)
        return _microsoft_admin_scope_unavailable(
            token_data,
            scope_profile,
            "Microsoft connector token refresh failed. Check connector logs.",
            "token_refresh_failed",
        )


def _scope_profile_for_scope(scope: str) -> str:
    for profile, configured_scope in MICROSOFT_ADMIN_SCOPE_PROFILES.items():
        configured_values = list(configured_scope)
        if scope in configured_values or " ".join(scope.split()) == " ".join(configured_values):
            return profile
    parsed = urlsplit(scope)
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith(".sharepoint.com"):
        if parsed.path.rstrip("/").endswith(".default"):
            return "sharepoint"
    return ""


def _microsoft_admin_scope_request(scope: str, scope_profile: str | None) -> str:
    if scope_profile == "sharepoint":
        return f"{scope} openid profile offline_access"
    provider = microsoft_native_provider_for_profile(scope_profile)
    return microsoft_native_device_scope_string(provider) if scope_profile else f"{scope} openid profile offline_access"


def _sharepoint_scope_for_url(site_url: str | None) -> str:
    parsed = urlsplit(str(site_url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    return f"https://{parsed.hostname}/.default"


async def get_microsoft_admin_token(
    user_id: Optional[UUID],
    profile: str,
    **context: Any,
) -> Optional[dict[str, Any]]:
    """Return a fresh delegated token for one native Microsoft connector profile."""
    scope_profile = microsoft_admin_scope_profile(profile)
    if scope_profile == "arm":
        scope = AZURE_ARM_SCOPE
    elif scope_profile == "exchange":
        scope = EXCHANGE_ONLINE_SCOPE
    elif scope_profile == "teams":
        scope = TEAMS_TENANT_ADMIN_SCOPE
    elif scope_profile == "sharepoint":
        scope = _sharepoint_scope_for_url(context.get("site_url") or context.get("admin_url"))
        if not scope:
            return None
    else:
        scope = MICROSOFT_GRAPH_SCOPE
    return await _get_fresh_microsoft_admin_token_for_scope(user_id, scope)


def _expires_on(token_data: dict[str, Any]) -> int:
    try:
        return int(token_data.get("expires_on") or 0)
    except (TypeError, ValueError):
        return 0


def _token_expired(token_data: dict[str, Any]) -> bool:
    expires_on = _expires_on(token_data)
    return bool(expires_on and expires_on <= int(time.time()))


def extract_microsoft_admin_username(token_data: dict[str, Any]) -> str:
    for claims in _microsoft_claim_sets(token_data):
        for key in ("preferred_username", "email", "upn", "unique_name", "name"):
            value = claims.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    stored_username = token_data.get("username")
    if isinstance(stored_username, str) and stored_username.strip() and stored_username != "azure-user":
        return stored_username.strip()

    client_info = _decode_base64_json(token_data.get("client_info", ""))
    for key in ("uid", "utid"):
        value = client_info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for claims in _microsoft_claim_sets(token_data):
        for key in ("oid", "sub"):
            value = claims.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _microsoft_identity_claims(token_data: dict[str, Any]) -> dict[str, Any]:
    claims = token_data.get("id_token_claims")
    if isinstance(claims, dict):
        return claims
    return _decode_jwt_claims(token_data.get("id_token", ""))


def _microsoft_claim_sets(token_data: dict[str, Any]) -> list[dict[str, Any]]:
    claim_sets = []
    id_claims = _microsoft_identity_claims(token_data)
    if id_claims:
        claim_sets.append(id_claims)
    access_claims = _decode_jwt_claims(token_data.get("access_token", ""))
    if access_claims:
        claim_sets.append(access_claims)
    return claim_sets


def _decode_base64_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        value += "=" * (-len(value) % 4)
        return json.loads(base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}
