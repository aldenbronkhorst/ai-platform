"""Diagnostics for the Microsoft Admin connector."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional
from uuid import UUID

import httpx

from app.services.token_storage import retrieve_token
from app.services.connectors.microsoft_admin.azure_cli import (
    _list_azure_subscriptions,
    ensure_azure_cli_profile,
    validate_azure_cli_profile,
)
from app.services.connectors.microsoft_admin.constants import (
    AZURE_ARM_SCOPE,
    MICROSOFT_ADMIN_PROVIDER,
    MICROSOFT_ADMIN_SCOPE_PROFILES,
    MICROSOFT_GRAPH_BASE_URL,
    MICROSOFT_GRAPH_SCOPE,
    microsoft_admin_app_name_for_scope_profile,
    microsoft_admin_client_id_for_scope_profile,
    microsoft_admin_scope_label,
    microsoft_admin_scope_profile,
    microsoft_admin_scope_summary,
)
from app.services.connectors.microsoft_admin.graph import _graph_error_details, _graph_response_data
from app.services.connectors.microsoft_admin.tokens import (
    _get_fresh_microsoft_admin_token,
    _get_fresh_microsoft_admin_token_for_scope,
    _token_expired,
    warm_microsoft_admin_delegated_tokens,
)

logger = logging.getLogger(__name__)

async def _microsoft_admin_status(user_id: Optional[UUID], request_id: str) -> dict[str, Any]:
    diagnosis = await diagnose_microsoft_admin_connection(user_id)
    token_data = await retrieve_token(MICROSOFT_ADMIN_PROVIDER, user_id) if user_id else None
    consented_profiles = set((token_data or {}).get("consented_scope_profiles") or [])
    primary_profile = (token_data or {}).get("scope_profile")
    if primary_profile:
        consented_profiles.add(microsoft_admin_scope_profile(primary_profile))
    return {
        **diagnosis,
        "connector": "microsoft_admin",
        "mode": "status",
        "request_id": request_id,
        "auth_profiles": {
            profile: {
                "label": microsoft_admin_scope_label(profile),
                "auth_app_name": microsoft_admin_app_name_for_scope_profile(profile),
                "client_id": microsoft_admin_client_id_for_scope_profile(profile),
                "scope_summary": microsoft_admin_scope_summary(profile),
                "consented": profile in consented_profiles,
            }
            for profile in MICROSOFT_ADMIN_SCOPE_PROFILES
        },
        "tooling": {
            "powershell_7": "pwsh",
            "graph_powershell": "Microsoft.Graph",
            "exchange_online_powershell": "ExchangeOnlineManagement",
            "teams_powershell": "MicrosoftTeams",
            "pnp_powershell": "PnP.PowerShell",
            "az_powershell": "Az",
            "azure_resource_manager_cli": "az",
            "bicep_cli": "bicep",
            "direct_graph": "https://graph.microsoft.com",
            "powershell_helpers": [
                "Connect-AIPlatformAz",
                "Connect-AIPlatformGraph",
                "Connect-AIPlatformExchange",
                "Connect-AIPlatformTeams",
            ],
        },
        "notes": [
            "GitHub CLI is intentionally excluded; use the GitHub connector.",
            "PowerShell module access is controlled by the signed-in Microsoft user's permissions and consented scopes.",
        ],
    }

async def diagnose_microsoft_admin_connection(user_id: Optional[UUID]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:16]
    token_data = await _get_fresh_microsoft_admin_token(user_id) if user_id else None
    if not token_data or not token_data.get("access_token"):
        return {
            "status": "failed",
            "connector": "microsoft_admin",
            "request_id": request_id,
            "message": "Microsoft Admin is not connected for this user.",
        }
    if _token_expired(token_data):
        return {
            "status": "failed",
            "connector": "microsoft_admin",
            "request_id": request_id,
            "message": "Microsoft Admin token is expired. Reconnect Microsoft Admin for this user.",
        }

    try:
        graph_token = await _get_fresh_microsoft_admin_token_for_scope(user_id, MICROSOFT_GRAPH_SCOPE)
        if not graph_token or not graph_token.get("access_token") or graph_token.get("refresh_error"):
            return {
                "status": "failed",
                "connector": "microsoft_admin",
                "request_id": request_id,
                "message": (
                    graph_token.get("refresh_error")
                    if graph_token
                    else "Microsoft Graph token is not available. Reconnect Microsoft Admin."
                ),
                "graph_status": "failed",
            }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{MICROSOFT_GRAPH_BASE_URL.rstrip('/')}/v1.0/me?$select=id,displayName,userPrincipalName,mail",
                headers={"Authorization": f"Bearer {graph_token['access_token']}"},
            )
        graph_data = _graph_response_data(response)
        error_type, graph_message = _graph_error_details(graph_data, response.status_code)
        if response.status_code >= 400:
            return {
                "status": "failed",
                "connector": "microsoft_admin",
                "request_id": request_id,
                "message": graph_message or "Microsoft Graph validation failed.",
                "error_type": error_type or "graph_validation_failed",
                "graph_status": "failed",
                "status_code": response.status_code,
            }

        secondary = await warm_microsoft_admin_delegated_tokens(user_id)
        arm_details: dict[str, Any] = {}
        arm_token = await _get_fresh_microsoft_admin_token_for_scope(user_id, AZURE_ARM_SCOPE)
        if arm_token and arm_token.get("access_token") and not arm_token.get("refresh_error"):
            subscriptions_result = await _list_azure_subscriptions(arm_token["access_token"])
            if subscriptions_result.get("ok"):
                subscriptions = subscriptions_result.get("subscriptions", [])
                arm_details = {
                    "status": "available",
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
                if user_id:
                    cli_profile = await ensure_azure_cli_profile(
                        user_id,
                        arm_token,
                        subscriptions_result=subscriptions_result,
                    )
                    if cli_profile.get("ready"):
                        cli_validation = await validate_azure_cli_profile(user_id)
                        if cli_validation.get("ready"):
                            arm_details["cli_status"] = "available"
                        else:
                            arm_details.update({
                                "status": "limited",
                                "cli_status": "failed",
                                "message": cli_validation.get("message") or "Azure CLI profile validation failed.",
                                "stderr": cli_validation.get("stderr", ""),
                            })
                    else:
                        arm_details.update({
                            "status": "limited",
                            "cli_status": "failed",
                            "message": cli_profile.get("message") or "Azure CLI profile could not be prepared.",
                        })
            else:
                arm_details = {
                    "status": "limited",
                    "message": subscriptions_result.get("message"),
                    "stderr": subscriptions_result.get("stderr", ""),
                }
        else:
            arm_details = {
                "status": "missing",
                "message": arm_token.get("refresh_error") if arm_token else "Azure Resource Manager token is not available.",
            }

        authorization_profiles = {
            "graph": {"status": "available", "label": microsoft_admin_scope_label("graph")},
            "exchange": {"label": microsoft_admin_scope_label("exchange"), **secondary.get("exchange", {})},
            "arm": {"label": microsoft_admin_scope_label("arm"), **arm_details},
            "teams": {"label": microsoft_admin_scope_label("teams"), **secondary.get("teams", {})},
            "sharepoint": {
                "status": "not_checked",
                "label": microsoft_admin_scope_label("sharepoint"),
                "message": "SharePoint/PnP tokens are target-site scoped and are acquired when ms_sharepoint_pnp_powershell is run with a site_url or admin_url.",
            },
        }
        status, message = _microsoft_admin_diagnostic_summary(authorization_profiles)

        return {
            "status": status,
            "connector": "microsoft_admin",
            "request_id": request_id,
            "message": message,
            "graph_status": "available",
            "graph_user": graph_data if isinstance(graph_data, dict) else {},
            "authorization_profiles": authorization_profiles,
        }
    except Exception as exc:
        logger.warning("Microsoft Admin diagnostics failed for request_id=%s: %s", request_id, exc)
        return {
            "status": "failed",
            "connector": "microsoft_admin",
            "request_id": request_id,
            "message": "Microsoft Admin diagnostics failed. Check connector logs with this request_id.",
        }


def _microsoft_admin_diagnostic_summary(
    authorization_profiles: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    problem_statuses = {"missing", "failed", "error", "limited"}
    problem_labels = [
        str(profile.get("label") or key)
        for key, profile in authorization_profiles.items()
        if profile.get("status") in problem_statuses
    ]
    if problem_labels:
        return (
            "partial",
            (
                "Microsoft Admin core connection is valid, but these authorization profiles need attention: "
                f"{', '.join(problem_labels)}."
            ),
        )
    return (
        "success",
        "Microsoft Admin is connected. Microsoft Graph, Azure Resource Manager, Exchange Online, and Teams validation succeeded.",
    )
