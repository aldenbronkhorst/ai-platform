"""Token retrieval and identity helpers for the Microsoft Admin connector."""
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
    EXCHANGE_ONLINE_SCOPE,
    MICROSOFT_ADMIN_CLIENT_ID,
    MICROSOFT_ADMIN_PROVIDER,
    MICROSOFT_ADMIN_SCOPE_PROFILES,
    MICROSOFT_GRAPH_SCOPE,
    TENANT_ID,
    TEAMS_TENANT_ADMIN_SCOPE,
    microsoft_admin_app_name_for_scope_profile,
    microsoft_admin_client_id_for_scope_profile,
    microsoft_admin_device_scope_string,
    microsoft_admin_scope_label,
    microsoft_admin_scope_profile,
)

logger = logging.getLogger(__name__)

def microsoft_admin_token_client_error(token_data: dict[str, Any] | None) -> str:
    if not token_data:
        return ""
    client_id = str(token_data.get("client_id") or "").strip()
    if client_id == MICROSOFT_ADMIN_CLIENT_ID:
        return ""
    if not client_id:
        return "Stored Microsoft Admin token is missing its application identity. Reconnect Microsoft Admin."
    return "Stored Microsoft Admin token was issued for a retired application. Reconnect Microsoft Admin."


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
        "client_id": token_data.get("client_id") or MICROSOFT_ADMIN_CLIENT_ID,
        "scope_profile": scope_profile,
        "username": token_data.get("username"),
        "refresh_error": message,
        "error_type": error_type,
    }


def _microsoft_admin_oauth_error_type(data: dict[str, Any]) -> str:
    error = str(data.get("error") or "").lower()
    description = str(data.get("error_description") or "").lower()
    if "aadsts65001" in description or "consent" in description:
        return "consent_required"
    if error == "invalid_grant":
        return "authorization_failed"
    return error or "token_refresh_failed"


def _microsoft_admin_oauth_error_message(scope_profile: str | None, data: dict[str, Any], fallback: str) -> str:
    label = microsoft_admin_scope_label(scope_profile) if scope_profile else "requested Microsoft scope"
    error_type = _microsoft_admin_oauth_error_type(data)
    if error_type == "consent_required":
        return (
            f"Tenant admin consent is required for {label}. "
            "Grant consent to the Microsoft Admin app once, then reconnect Microsoft Admin."
        )
    return data.get("error_description") or data.get("error") or fallback

async def _get_fresh_microsoft_admin_token(user_id: Optional[UUID]) -> Optional[dict[str, Any]]:
    if not user_id:
        return None
    token_data = await retrieve_token(MICROSOFT_ADMIN_PROVIDER, user_id)
    if not token_data:
        return None
    client_error = microsoft_admin_token_client_error(token_data)
    if client_error:
        return _invalid_microsoft_admin_token(token_data, client_error)
    expires_on = _expires_on(token_data)
    if token_data.get("access_token") and (not expires_on or expires_on > int(time.time()) + 300):
        return token_data
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return token_data
    scope_profile = microsoft_admin_scope_profile(token_data.get("scope_profile"))
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                AZURE_TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": token_data.get("client_id") or MICROSOFT_ADMIN_CLIENT_ID,
                    "refresh_token": refresh_token,
                    "scope": microsoft_admin_device_scope_string(scope_profile),
                    "client_info": "1",
                },
            )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            return {**token_data, "refresh_error": data.get("error_description") or data.get("error") or response.text[:500]}
        updated = {
            **token_data,
            "client_id": token_data.get("client_id") or MICROSOFT_ADMIN_CLIENT_ID,
            "token_type": data.get("token_type", token_data.get("token_type")),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token", refresh_token),
            "scope": data.get("scope", token_data.get("scope")),
            "scope_profile": scope_profile,
            "id_token": data.get("id_token", token_data.get("id_token")),
            "id_token_claims": _microsoft_identity_claims({"id_token": data.get("id_token")}) or token_data.get("id_token_claims"),
            "client_info": data.get("client_info", token_data.get("client_info")),
            "expires_in": data.get("expires_in"),
            "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
        }
        updated["username"] = extract_microsoft_admin_username(updated)
        await store_token(MICROSOFT_ADMIN_PROVIDER, user_id, updated)
        if scope_profile == "arm":
            from app.services.connectors.microsoft_admin.azure_cli import ensure_azure_cli_profile

            await ensure_azure_cli_profile(user_id, updated)
        return updated
    except Exception as exc:
        logger.warning("Microsoft Admin token refresh failed for user %s: %s", user_id.hex[:12], exc)
        return {**token_data, "refresh_error": "token_refresh_failed"}


async def _get_fresh_microsoft_admin_token_for_scope(
    user_id: Optional[UUID],
    scope: str,
    *,
    require_account_metadata: bool = False,
) -> Optional[dict[str, Any]]:
    """Return a fresh Microsoft token for a requested Microsoft Admin resource."""
    if not user_id:
        return None
    from app.services.connectors.microsoft_admin.azure_cli import _has_azure_cli_account_metadata

    token_data = await retrieve_token(MICROSOFT_ADMIN_PROVIDER, user_id)
    if not token_data:
        return None
    client_error = microsoft_admin_token_client_error(token_data)
    if client_error:
        return _invalid_microsoft_admin_token(token_data, client_error)
    scope_profile = _scope_profile_for_scope(scope)
    if scope_profile and token_data.get("scope_profile") == scope_profile:
        expires_on = _expires_on(token_data)
        if token_data.get("access_token") and (not expires_on or expires_on > int(time.time()) + 300):
            if not require_account_metadata or _has_azure_cli_account_metadata(token_data):
                return token_data
    cached_token = (token_data.get("delegated_tokens") or {}).get(scope_profile) if scope_profile else None
    cached_token_is_current = isinstance(cached_token, dict) and not microsoft_admin_token_client_error(cached_token)
    if scope_profile == "sharepoint" and cached_token_is_current:
        cached_scope = str(cached_token.get("scope") or "")
        cached_token_is_current = scope in cached_scope.split() or cached_scope == scope
    if cached_token_is_current:
        expires_on = _expires_on(cached_token)
        if cached_token.get("access_token") and (not expires_on or expires_on > int(time.time()) + 300):
            merged_token = {**token_data, **cached_token}
            if not require_account_metadata or _has_azure_cli_account_metadata(merged_token):
                return merged_token

    refresh_token = cached_token.get("refresh_token") if cached_token_is_current else None
    refresh_token = refresh_token or token_data.get("refresh_token")
    if not refresh_token:
        profile_name = microsoft_admin_scope_label(scope_profile) if scope_profile else "requested Microsoft scope"
        return _microsoft_admin_scope_unavailable(
            token_data,
            scope_profile,
            f"Stored {profile_name} token has no refresh token. Reconnect Microsoft Admin.",
            "reconnect_required",
        )
    client_id = microsoft_admin_client_id_for_scope_profile(scope_profile) if scope_profile else MICROSOFT_ADMIN_CLIENT_ID
    scope_request = _microsoft_admin_scope_request(scope, scope_profile)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                AZURE_TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "scope": scope_request,
                    "client_info": "1",
                },
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
                "id_token": scoped_token.get("id_token"),
                "id_token_claims": scoped_token.get("id_token_claims"),
                "client_info": scoped_token.get("client_info"),
                "expires_in": scoped_token.get("expires_in"),
                "expires_on": scoped_token.get("expires_on"),
            }
            consented = set(token_data.get("consented_scope_profiles") or [])
            consented.add(scope_profile)
            await store_token(
                MICROSOFT_ADMIN_PROVIDER,
                user_id,
                {
                    **token_data,
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
            "Microsoft Admin scoped token refresh failed. Check connector logs.",
            "token_refresh_failed",
        )


async def warm_microsoft_admin_delegated_tokens(user_id: Optional[UUID]) -> dict[str, Any]:
    """Best-effort silent token warmup for secondary Microsoft Admin resources."""
    if not user_id:
        return {}
    results: dict[str, Any] = {}
    for profile, scope in (
        ("exchange", EXCHANGE_ONLINE_SCOPE),
        ("arm", AZURE_ARM_SCOPE),
        ("teams", TEAMS_TENANT_ADMIN_SCOPE),
    ):
        token = await _get_fresh_microsoft_admin_token_for_scope(user_id, scope)
        results[profile] = {
            "status": "available" if token and token.get("access_token") and not token.get("refresh_error") else "missing",
            "message": token.get("refresh_error") if token else "No token returned.",
        }
    return results


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
    return microsoft_admin_device_scope_string(scope_profile) if scope_profile else f"{scope} openid profile offline_access"


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
    """Return a fresh delegated Microsoft Admin token for one authorization profile."""
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
    return await _get_fresh_microsoft_admin_token_for_scope(
        user_id,
        scope,
        require_account_metadata=bool(context.get("require_account_metadata")),
    )


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
