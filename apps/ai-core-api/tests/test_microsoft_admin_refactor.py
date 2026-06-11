import importlib.util
import re
import uuid
from pathlib import Path

from app.services.tool_registry import CONNECTOR_TOOLS_BY_SYSTEM, MICROSOFT_NATIVE_CONNECTOR_SYSTEMS


MICROSOFT_NATIVE_TOOLS_BY_SYSTEM = {
    "azure_cli": {"ms_azure_cli", "ms_az_powershell", "ms_bicep"},
    "microsoft_graph": {"ms_graph", "ms_graph_powershell"},
    "exchange_online": {"ms_exchange_powershell"},
    "teams_admin": {"ms_teams_powershell"},
    "sharepoint_pnp": {"ms_sharepoint_pnp_powershell"},
}


def test_connector_commands_module_is_removed_from_active_runtime():
    repo_root = Path(__file__).resolve().parents[3]
    assert not (repo_root / "apps/ai-core-api/app/services/connector_commands.py").exists()

    active_roots = [
        repo_root / "apps/ai-core-api/app",
        repo_root / "apps/ai-core-api/scripts",
    ]
    violations: list[str] = []
    for active_root in active_roots:
        for path in active_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "services.connector_commands" in text:
                violations.append(str(path.relative_to(repo_root)))

    assert violations == []


def test_microsoft_admin_tool_runners_import_from_split_modules():
    from app.services.connectors.microsoft_admin.azure_cli import run_ms_azure_cli_tool
    from app.services.connectors.microsoft_admin.bicep import run_ms_bicep_tool
    from app.services.connectors.microsoft_admin.graph import run_ms_graph_tool
    from app.services.connectors.microsoft_admin.powershell_az import run_ms_az_powershell_tool
    from app.services.connectors.microsoft_admin.powershell_exchange import run_ms_exchange_powershell_tool
    from app.services.connectors.microsoft_admin.powershell_graph import run_ms_graph_powershell_tool
    from app.services.connectors.microsoft_admin.powershell_pnp import run_ms_sharepoint_pnp_powershell_tool
    from app.services.connectors.microsoft_admin.powershell_teams import run_ms_teams_powershell_tool

    assert callable(run_ms_graph_tool)
    assert callable(run_ms_azure_cli_tool)
    assert callable(run_ms_bicep_tool)
    assert callable(run_ms_graph_powershell_tool)
    assert callable(run_ms_exchange_powershell_tool)
    assert callable(run_ms_teams_powershell_tool)
    assert callable(run_ms_sharepoint_pnp_powershell_tool)
    assert callable(run_ms_az_powershell_tool)


def test_tool_registry_uses_split_native_microsoft_connectors():
    assert set(MICROSOFT_NATIVE_CONNECTOR_SYSTEMS) == set(MICROSOFT_NATIVE_TOOLS_BY_SYSTEM)
    assert "microsoft_admin" not in CONNECTOR_TOOLS_BY_SYSTEM
    assert "azure" not in CONNECTOR_TOOLS_BY_SYSTEM
    for system, tools in MICROSOFT_NATIVE_TOOLS_BY_SYSTEM.items():
        assert CONNECTOR_TOOLS_BY_SYSTEM[system] == frozenset(tools)
        assert "ms_admin" not in CONNECTOR_TOOLS_BY_SYSTEM[system]
        assert "ms_powershell" not in CONNECTOR_TOOLS_BY_SYSTEM[system]


def test_removed_microsoft_admin_names_are_not_in_active_source_paths():
    repo_root = Path(__file__).resolve().parents[3]
    active_roots = [
        repo_root / "apps/ai-core-api/app",
        repo_root / "apps/ai-core-api/scripts",
        repo_root / "apps/web-portal/src",
    ]
    banned_patterns = {
        "quoted ms_admin": re.compile(r"['\"]ms_admin['\"]"),
        "quoted ms_powershell": re.compile(r"['\"]ms_powershell['\"]"),
        "legacy default connector tool map": re.compile(r"\bCONNECTOR_TOOL_BY_SYSTEM\b"),
        "legacy Microsoft Admin token top-level keys": re.compile(r"\bAZURE_TOKEN_TOP_LEVEL_KEYS\b"),
        "legacy Microsoft Admin delegated token keys": re.compile(r"\bAZURE_DELEGATED_TOKEN_KEYS\b"),
        "legacy Microsoft Admin token compactor": re.compile(r"\b_compact_azure_token_for_storage\b"),
        "legacy Microsoft Admin delegated token compactor": re.compile(r"\b_azure_delegated_tokens_for_storage\b"),
        "legacy Microsoft Admin token refresher": re.compile(r"\b_get_fresh_azure_token(?:_for_scope)?\b"),
        "legacy Microsoft Admin username extractor": re.compile(r"\bextract_azure_username\b"),
        "legacy Microsoft Admin device scope helper": re.compile(r"\bazure_device_scope_string\b"),
        "provider azure": re.compile(r"\bprovider\b\s*(?:==|=|:)\s*['\"]azure['\"]"),
        "connector_key azure": re.compile(r"\bconnector_key\b\s*(?:==|=|:)\s*['\"]azure['\"]"),
        "target_system azure": re.compile(r"\btarget_system\b\s*(?:==|=|:)\s*['\"]azure['\"]"),
        "connector system microsoft_admin": re.compile(r"\btarget_system\b\s*(?:==|=|:)\s*['\"]microsoft_admin['\"]"),
    }

    violations: list[str] = []
    for active_root in active_roots:
        for path in active_root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8")
            for label, pattern in banned_patterns.items():
                if pattern.search(text):
                    violations.append(f"{path.relative_to(repo_root)} contains {label}")

    assert violations == []


def test_ms_admin_migration_copies_legacy_key_vault_token_secret(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    migration_path = repo_root / "apps/ai-core-api/migrations/versions/010_ms_admin_refactor.py"
    spec = importlib.util.spec_from_file_location("ms_admin_refactor_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(migration)

    user_id = uuid.UUID("e4807f22-97c8-4000-8000-000000000001")
    old_name = f"connector-token-azure-{user_id.hex[:12]}"
    new_name = f"connector-token-microsoft-admin-{user_id.hex[:12]}"

    class FakeMissing(Exception):
        pass

    class FakeSecret:
        def __init__(self, value: str):
            self.value = value

    class FakeSecretClient:
        secrets = {old_name: "stored-token"}

        def __init__(self, vault_url, credential):
            self.vault_url = vault_url
            self.credential = credential

        def get_secret(self, name):
            if name not in self.secrets:
                raise FakeMissing(name)
            return FakeSecret(self.secrets[name])

        def set_secret(self, name, value):
            self.secrets[name] = value

    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return [{"user_id": user_id, "secret_reference": None}]

    class FakeBind:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    monkeypatch.setenv("KEY_VAULT_URI", "https://vault.example")
    monkeypatch.setattr(migration, "ResourceNotFoundError", FakeMissing)
    monkeypatch.setattr(migration, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(migration, "SecretClient", FakeSecretClient)
    monkeypatch.setattr(migration.op, "get_bind", lambda: FakeBind())

    migration._copy_token_secrets("connector-token-azure-", "connector-token-microsoft-admin-", "azure")

    assert FakeSecretClient.secrets[new_name] == "stored-token"
