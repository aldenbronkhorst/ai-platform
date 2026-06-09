"""Constants and scope helpers for the Microsoft Admin connector."""
from __future__ import annotations

import os
import re

MICROSOFT_ADMIN_PROVIDER = "microsoft_admin"

def _scope_values_from_env(env_name: str, default_values: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default_values
    values = tuple(part for part in re.split(r"[\s,;]+", raw) if part)
    return values or default_values


TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")
MICROSOFT_ADMIN_CLIENT_ID = (
    os.environ.get("MICROSOFT_ADMIN_CLIENT_ID")
    or os.environ.get("MS_ADMIN_CLIENT_ID")
    or "8a178920-de9e-41cf-af4e-c3012fc3bbd2"
)
MICROSOFT_ADMIN_APP_DISPLAY_NAME = os.environ.get("MICROSOFT_ADMIN_APP_DISPLAY_NAME", "AI Platform Microsoft Admin")
AZURE_AUTHORITY_HOST = os.environ.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com")
AZURE_TOKEN_ENDPOINT = f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/v2.0/token"
AZURE_ARM_SCOPE = os.environ.get("AZURE_ARM_SCOPE", "https://management.azure.com/user_impersonation")
AZURE_ENVIRONMENT_NAME = os.environ.get("AZURE_ENVIRONMENT_NAME", "AzureCloud")
AZURE_CLI_CLIENT_ID = os.environ.get("AZURE_CLI_CLIENT_ID", "04b07795-8ddb-461a-bbee-02f9e1bf7b46")
AZURE_CLI_ARM_RESOURCE = os.environ.get("AZURE_CLI_ARM_RESOURCE", "https://management.core.windows.net/")
AZURE_CLI_ARM_TARGET = os.environ.get(
    "AZURE_CLI_ARM_TARGET",
    "https://management.core.windows.net//.default https://management.core.windows.net//user_impersonation",
)
MICROSOFT_ADMIN_PRIMARY_SCOPE_PROFILE = "graph"
DEFAULT_MICROSOFT_GRAPH_SCOPES = (
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/User.ReadWrite.All",
    "https://graph.microsoft.com/Directory.ReadWrite.All",
    "https://graph.microsoft.com/Group.ReadWrite.All",
    "https://graph.microsoft.com/Organization.Read.All",
    "https://graph.microsoft.com/RoleManagement.ReadWrite.Directory",
    "https://graph.microsoft.com/Application.ReadWrite.All",
    "https://graph.microsoft.com/DeviceManagementManagedDevices.ReadWrite.All",
    "https://graph.microsoft.com/DeviceManagementConfiguration.ReadWrite.All",
    "https://graph.microsoft.com/DeviceManagementApps.ReadWrite.All",
    "https://graph.microsoft.com/Policy.ReadWrite.ConditionalAccess",
    "https://graph.microsoft.com/Sites.FullControl.All",
    "https://graph.microsoft.com/Reports.Read.All",
    "https://graph.microsoft.com/AuditLog.Read.All",
)
MICROSOFT_GRAPH_SCOPES = _scope_values_from_env(
    "MICROSOFT_GRAPH_SCOPES",
    _scope_values_from_env("MICROSOFT_GRAPH_SCOPE", DEFAULT_MICROSOFT_GRAPH_SCOPES),
)
MICROSOFT_GRAPH_SCOPE = " ".join(MICROSOFT_GRAPH_SCOPES)
MICROSOFT_GRAPH_BASE_URL = os.environ.get("MICROSOFT_GRAPH_BASE_URL", "https://graph.microsoft.com")
EXCHANGE_ONLINE_SCOPE = os.environ.get("EXCHANGE_ONLINE_SCOPE", "https://outlook.office365.com/.default")
EXCHANGE_ONLINE_SCOPES = _scope_values_from_env("EXCHANGE_ONLINE_SCOPES", (EXCHANGE_ONLINE_SCOPE,))
TEAMS_TENANT_ADMIN_SCOPE = os.environ.get(
    "TEAMS_TENANT_ADMIN_SCOPE",
    "48ac35b8-9aa8-4d74-927d-1f4a14a0b239/.default",
)
MS_AZURE_CLI_ALLOWED_BINARIES = {"az"}
MS_POWERSHELL_ALLOWED_BINARIES = {"pwsh"}
MS_BICEP_ALLOWED_BINARIES = {"bicep"}
MS_ADMIN_FORBIDDEN_COMMAND_RE = re.compile(r"(?i)(^|[\s;&|`])(gh|git)(\.exe)?($|[\s;&|])")
MICROSOFT_ADMIN_SCOPE_PROFILES = {
    "arm": (AZURE_ARM_SCOPE,),
    "graph": MICROSOFT_GRAPH_SCOPES,
    "exchange": EXCHANGE_ONLINE_SCOPES,
    "teams": (TEAMS_TENANT_ADMIN_SCOPE,),
    "sharepoint": (),
}
MICROSOFT_ADMIN_SCOPE_PROFILE_LABELS = {
    "arm": "Azure Resource Manager",
    "graph": "Microsoft Graph Admin",
    "exchange": "Exchange Online",
    "teams": "Teams Admin",
    "sharepoint": "SharePoint / PnP",
}
GRAPH_AUTO_PAGE_MAX_PAGES = 20
GRAPH_AUTO_PAGE_MAX_ITEMS = 1000

def microsoft_admin_arm_device_scope_string() -> str:
    return microsoft_admin_device_scope_string("arm")


def microsoft_admin_scope_profile(profile: str | None) -> str:
    normalized = str(profile or MICROSOFT_ADMIN_PRIMARY_SCOPE_PROFILE).strip().lower()
    return normalized if normalized in MICROSOFT_ADMIN_SCOPE_PROFILES else MICROSOFT_ADMIN_PRIMARY_SCOPE_PROFILE


def microsoft_admin_scope_values(profile: str | None = None) -> list[str]:
    scope_profile = microsoft_admin_scope_profile(profile)
    return list(MICROSOFT_ADMIN_SCOPE_PROFILES[scope_profile])


def microsoft_admin_client_id_for_scope_profile(profile: str | None = None) -> str:
    return MICROSOFT_ADMIN_CLIENT_ID


def microsoft_admin_app_name_for_scope_profile(profile: str | None = None) -> str:
    return MICROSOFT_ADMIN_APP_DISPLAY_NAME


def microsoft_admin_scope_label(profile: str | None = None) -> str:
    scope_profile = microsoft_admin_scope_profile(profile)
    return MICROSOFT_ADMIN_SCOPE_PROFILE_LABELS[scope_profile]


def microsoft_admin_scope_summary(profile: str | None = None) -> str:
    scope_profile = microsoft_admin_scope_profile(profile)
    if scope_profile == "sharepoint":
        return f"{microsoft_admin_scope_label(scope_profile)}: target SharePoint site .default"
    scope_names = [
        value.rsplit("/", 1)[-1]
        for value in microsoft_admin_scope_values(scope_profile)
    ]
    return f"{microsoft_admin_scope_label(scope_profile)}: {', '.join(scope_names)}"


def microsoft_admin_device_scope_string(profile: str | None = None) -> str:
    """Return a single-resource device-code scope string for a Microsoft Admin consent profile."""
    scope_profile = microsoft_admin_scope_profile(profile)
    return " ".join([*microsoft_admin_scope_values(scope_profile), "openid", "profile", "offline_access"])


def microsoft_admin_arm_token_request_data() -> dict[str, str]:
    """Return token request fields for the Microsoft Admin ARM profile."""
    return {"scope": microsoft_admin_arm_device_scope_string(), "client_info": "1"}
