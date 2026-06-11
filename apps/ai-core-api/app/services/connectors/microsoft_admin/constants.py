"""Constants and scope helpers for native Microsoft tool connectors."""
from __future__ import annotations

import os
import re

AZURE_CLI_PROVIDER = "azure_cli"
MICROSOFT_GRAPH_PROVIDER = "microsoft_graph"
EXCHANGE_ONLINE_PROVIDER = "exchange_online"
TEAMS_ADMIN_PROVIDER = "teams_admin"
SHAREPOINT_PNP_PROVIDER = "sharepoint_pnp"

MICROSOFT_NATIVE_CONNECTOR_PROVIDERS = (
    AZURE_CLI_PROVIDER,
    MICROSOFT_GRAPH_PROVIDER,
    EXCHANGE_ONLINE_PROVIDER,
    TEAMS_ADMIN_PROVIDER,
    SHAREPOINT_PNP_PROVIDER,
)

def _scope_values_from_env(env_name: str, default_values: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default_values
    values = tuple(part for part in re.split(r"[\s,;]+", raw) if part)
    return values or default_values


TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")
AZURE_AUTHORITY_HOST = os.environ.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com")
AZURE_TOKEN_ENDPOINT = f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/v2.0/token"
AZURE_V1_TOKEN_ENDPOINT = f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/token"
AZURE_V1_DEVICE_CODE_ENDPOINT = f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/devicecode"
AZURE_V2_DEVICE_CODE_ENDPOINT = f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/v2.0/devicecode"
AZURE_ARM_SCOPE = os.environ.get("AZURE_ARM_SCOPE", "https://management.azure.com/user_impersonation")
AZURE_ENVIRONMENT_NAME = os.environ.get("AZURE_ENVIRONMENT_NAME", "AzureCloud")
AZURE_CLI_CLIENT_ID = os.environ.get("AZURE_CLI_CLIENT_ID", "04b07795-8ddb-461a-bbee-02f9e1bf7b46")
AZURE_CLI_APP_DISPLAY_NAME = os.environ.get("AZURE_CLI_APP_DISPLAY_NAME", "Microsoft Azure CLI")
AZURE_CLI_ARM_RESOURCE = os.environ.get("AZURE_CLI_ARM_RESOURCE", "https://management.core.windows.net/")
AZURE_CLI_ARM_TARGET = os.environ.get(
    "AZURE_CLI_ARM_TARGET",
    "https://management.core.windows.net//.default https://management.core.windows.net//user_impersonation",
)
MICROSOFT_ADMIN_PRIMARY_SCOPE_PROFILE = "graph"
MICROSOFT_GRAPH_POWERSHELL_CLIENT_ID = os.environ.get(
    "MICROSOFT_GRAPH_POWERSHELL_CLIENT_ID",
    "14d82eec-204b-4c2f-b7e8-296a70dab67e",
)
MICROSOFT_GRAPH_POWERSHELL_APP_DISPLAY_NAME = os.environ.get(
    "MICROSOFT_GRAPH_POWERSHELL_APP_DISPLAY_NAME",
    "Microsoft Graph",
)
DEFAULT_MICROSOFT_GRAPH_SCOPES = (
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/User.Read.All",
    "https://graph.microsoft.com/User.ReadWrite.All",
    "https://graph.microsoft.com/Directory.ReadWrite.All",
    "https://graph.microsoft.com/Group.ReadWrite.All",
    "https://graph.microsoft.com/Organization.Read.All",
    "https://graph.microsoft.com/RoleManagement.ReadWrite.Directory",
    "https://graph.microsoft.com/Application.ReadWrite.All",
    "https://graph.microsoft.com/AppCatalog.ReadWrite.All",
    "https://graph.microsoft.com/DeviceManagementManagedDevices.ReadWrite.All",
    "https://graph.microsoft.com/DeviceManagementConfiguration.ReadWrite.All",
    "https://graph.microsoft.com/DeviceManagementApps.ReadWrite.All",
    "https://graph.microsoft.com/Policy.ReadWrite.ConditionalAccess",
    "https://graph.microsoft.com/Sites.FullControl.All",
    "https://graph.microsoft.com/Reports.Read.All",
    "https://graph.microsoft.com/AuditLog.Read.All",
    "https://graph.microsoft.com/TeamSettings.ReadWrite.All",
    "https://graph.microsoft.com/Channel.Delete.All",
    "https://graph.microsoft.com/ChannelSettings.ReadWrite.All",
    "https://graph.microsoft.com/ChannelMember.ReadWrite.All",
)
MICROSOFT_GRAPH_SCOPES = _scope_values_from_env(
    "MICROSOFT_GRAPH_SCOPES",
    _scope_values_from_env("MICROSOFT_GRAPH_SCOPE", DEFAULT_MICROSOFT_GRAPH_SCOPES),
)
MICROSOFT_GRAPH_SCOPE = " ".join(MICROSOFT_GRAPH_SCOPES)
MICROSOFT_GRAPH_BASE_URL = os.environ.get("MICROSOFT_GRAPH_BASE_URL", "https://graph.microsoft.com")
EXCHANGE_ONLINE_SCOPE = os.environ.get("EXCHANGE_ONLINE_SCOPE", "https://outlook.office365.com/.default")
EXCHANGE_ONLINE_RESOURCE = os.environ.get("EXCHANGE_ONLINE_RESOURCE", "https://outlook.office365.com/")
EXCHANGE_ONLINE_SCOPES = _scope_values_from_env("EXCHANGE_ONLINE_SCOPES", (EXCHANGE_ONLINE_SCOPE,))
EXCHANGE_ONLINE_CLIENT_ID = os.environ.get(
    "EXCHANGE_ONLINE_CLIENT_ID",
    "fb78d390-0c51-40cd-8e17-fdbfab77341b",
).strip()
EXCHANGE_ONLINE_APP_DISPLAY_NAME = os.environ.get("EXCHANGE_ONLINE_APP_DISPLAY_NAME", "Exchange Online PowerShell")
TEAMS_TENANT_ADMIN_SCOPE = os.environ.get(
    "TEAMS_TENANT_ADMIN_SCOPE",
    "48ac35b8-9aa8-4d74-927d-1f4a14a0b239/.default",
)
TEAMS_TENANT_ADMIN_RESOURCE = os.environ.get(
    "TEAMS_TENANT_ADMIN_RESOURCE",
    "48ac35b8-9aa8-4d74-927d-1f4a14a0b239",
)
TEAMS_ADMIN_CLIENT_ID = os.environ.get(
    "TEAMS_ADMIN_CLIENT_ID",
    "12128f48-ec9e-42f0-b203-ea49fb6af367",
).strip()
TEAMS_ADMIN_APP_DISPLAY_NAME = os.environ.get("TEAMS_ADMIN_APP_DISPLAY_NAME", "Microsoft Teams PowerShell")
SHAREPOINT_PNP_CLIENT_ID = os.environ.get("SHAREPOINT_PNP_CLIENT_ID", "").strip()
SHAREPOINT_PNP_APP_DISPLAY_NAME = os.environ.get("SHAREPOINT_PNP_APP_DISPLAY_NAME", "PnP PowerShell")
MS_AZURE_CLI_ALLOWED_BINARIES = {"az"}
MS_POWERSHELL_ALLOWED_BINARIES = {"pwsh"}
MS_ADMIN_FORBIDDEN_COMMAND_RE = re.compile(r"(?i)(^|[\s;&|`])(gh|git)(\.exe)?($|[\s;&|])")
MICROSOFT_NATIVE_CONNECTOR_PROFILES = {
    AZURE_CLI_PROVIDER: {
        "legacy_profile": "arm",
        "label": "Azure CLI",
        "auth_app_name": AZURE_CLI_APP_DISPLAY_NAME,
        "client_id": AZURE_CLI_CLIENT_ID,
        "scopes": (AZURE_ARM_SCOPE,),
        "required": True,
    },
    MICROSOFT_GRAPH_PROVIDER: {
        "legacy_profile": "graph",
        "label": "Microsoft Graph",
        "auth_app_name": MICROSOFT_GRAPH_POWERSHELL_APP_DISPLAY_NAME,
        "client_id": MICROSOFT_GRAPH_POWERSHELL_CLIENT_ID,
        "scopes": MICROSOFT_GRAPH_SCOPES,
        "required": True,
    },
    EXCHANGE_ONLINE_PROVIDER: {
        "legacy_profile": "exchange",
        "label": "Exchange Online",
        "auth_app_name": EXCHANGE_ONLINE_APP_DISPLAY_NAME,
        "client_id": EXCHANGE_ONLINE_CLIENT_ID,
        "scopes": EXCHANGE_ONLINE_SCOPES,
        "required": False,
    },
    TEAMS_ADMIN_PROVIDER: {
        "legacy_profile": "teams",
        "label": "Teams Admin",
        "auth_app_name": TEAMS_ADMIN_APP_DISPLAY_NAME,
        "client_id": TEAMS_ADMIN_CLIENT_ID,
        "scopes": (TEAMS_TENANT_ADMIN_SCOPE,),
        "required": False,
    },
    SHAREPOINT_PNP_PROVIDER: {
        "legacy_profile": "sharepoint",
        "label": "SharePoint / PnP",
        "auth_app_name": SHAREPOINT_PNP_APP_DISPLAY_NAME,
        "client_id": SHAREPOINT_PNP_CLIENT_ID,
        "scopes": (),
        "required": False,
    },
}
MICROSOFT_LEGACY_PROFILE_TO_PROVIDER = {
    str(profile["legacy_profile"]): provider
    for provider, profile in MICROSOFT_NATIVE_CONNECTOR_PROFILES.items()
}
MICROSOFT_PROVIDER_TO_LEGACY_PROFILE = {
    provider: str(profile["legacy_profile"])
    for provider, profile in MICROSOFT_NATIVE_CONNECTOR_PROFILES.items()
}

MICROSOFT_ADMIN_SCOPE_PROFILES = {
    "arm": (AZURE_ARM_SCOPE,),
    "graph": MICROSOFT_GRAPH_SCOPES,
    "exchange": EXCHANGE_ONLINE_SCOPES,
    "teams": (TEAMS_TENANT_ADMIN_SCOPE,),
    "sharepoint": (),
}
MICROSOFT_ADMIN_REQUIRED_SCOPE_PROFILES = ("graph", "arm", "exchange", "teams")
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
    return microsoft_native_client_id_for_provider(microsoft_native_provider_for_profile(profile))


def microsoft_admin_app_name_for_scope_profile(profile: str | None = None) -> str:
    return microsoft_native_app_name_for_provider(microsoft_native_provider_for_profile(profile))


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
    """Return a single-resource device-code scope string for a native connector profile."""
    scope_profile = microsoft_admin_scope_profile(profile)
    return " ".join([*microsoft_admin_scope_values(scope_profile), "openid", "profile", "offline_access"])


def microsoft_native_provider(provider: str | None) -> str:
    normalized = str(provider or "").strip().lower().replace("-", "_")
    aliases = {
        "arm": AZURE_CLI_PROVIDER,
        "azure": AZURE_CLI_PROVIDER,
        "az": AZURE_CLI_PROVIDER,
        "azure_resource_manager": AZURE_CLI_PROVIDER,
        "graph": MICROSOFT_GRAPH_PROVIDER,
        "ms_graph": MICROSOFT_GRAPH_PROVIDER,
        "microsoft_graph": MICROSOFT_GRAPH_PROVIDER,
        "exchange": EXCHANGE_ONLINE_PROVIDER,
        "exchange_online": EXCHANGE_ONLINE_PROVIDER,
        "teams": TEAMS_ADMIN_PROVIDER,
        "teams_admin": TEAMS_ADMIN_PROVIDER,
        "sharepoint": SHAREPOINT_PNP_PROVIDER,
        "sharepoint_pnp": SHAREPOINT_PNP_PROVIDER,
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in MICROSOFT_NATIVE_CONNECTOR_PROFILES else ""


def microsoft_native_provider_for_profile(profile: str | None) -> str:
    return MICROSOFT_LEGACY_PROFILE_TO_PROVIDER.get(microsoft_admin_scope_profile(profile), MICROSOFT_GRAPH_PROVIDER)


def microsoft_native_profile_for_provider(provider: str | None) -> str:
    normalized = microsoft_native_provider(provider)
    return MICROSOFT_PROVIDER_TO_LEGACY_PROFILE.get(normalized, "")


def microsoft_native_scope_values(provider: str | None) -> list[str]:
    normalized = microsoft_native_provider(provider)
    profile = MICROSOFT_NATIVE_CONNECTOR_PROFILES.get(normalized, {})
    return list(profile.get("scopes") or ())


def microsoft_native_oauth_flow_for_provider(provider: str | None) -> str:
    normalized = microsoft_native_provider(provider)
    profile = MICROSOFT_NATIVE_CONNECTOR_PROFILES.get(normalized, {})
    return str(profile.get("oauth_flow") or "v2_scope")


def microsoft_native_resource_for_provider(provider: str | None) -> str:
    normalized = microsoft_native_provider(provider)
    profile = MICROSOFT_NATIVE_CONNECTOR_PROFILES.get(normalized, {})
    return str(profile.get("resource") or "").strip()


def microsoft_native_client_id_for_provider(provider: str | None) -> str:
    normalized = microsoft_native_provider(provider)
    profile = MICROSOFT_NATIVE_CONNECTOR_PROFILES.get(normalized, {})
    return str(profile.get("client_id") or "").strip()


def microsoft_native_app_name_for_provider(provider: str | None) -> str:
    normalized = microsoft_native_provider(provider)
    profile = MICROSOFT_NATIVE_CONNECTOR_PROFILES.get(normalized, {})
    return str(profile.get("auth_app_name") or profile.get("label") or normalized)


def microsoft_native_label_for_provider(provider: str | None) -> str:
    normalized = microsoft_native_provider(provider)
    profile = MICROSOFT_NATIVE_CONNECTOR_PROFILES.get(normalized, {})
    return str(profile.get("label") or normalized)


def microsoft_native_device_scope_string(provider: str | None, scopes: tuple[str, ...] | list[str] | None = None) -> str:
    values = list(scopes or microsoft_native_scope_values(provider))
    return " ".join([*values, "openid", "profile", "offline_access"])
