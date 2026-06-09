import re
from pathlib import Path

from app.services.tool_registry import CONNECTOR_TOOLS_BY_SYSTEM


MICROSOFT_ADMIN_TOOLS = {
    "ms_graph",
    "ms_graph_powershell",
    "ms_exchange_powershell",
    "ms_teams_powershell",
    "ms_sharepoint_pnp_powershell",
    "ms_az_powershell",
    "ms_azure_cli",
    "ms_bicep",
}


def test_tool_registry_uses_microsoft_admin_not_azure_connector():
    assert "microsoft_admin" in CONNECTOR_TOOLS_BY_SYSTEM
    assert "azure" not in CONNECTOR_TOOLS_BY_SYSTEM
    assert CONNECTOR_TOOLS_BY_SYSTEM["microsoft_admin"] == frozenset(MICROSOFT_ADMIN_TOOLS)
    assert "ms_admin" not in CONNECTOR_TOOLS_BY_SYSTEM["microsoft_admin"]
    assert "ms_powershell" not in CONNECTOR_TOOLS_BY_SYSTEM["microsoft_admin"]
    assert "azure_cli" not in CONNECTOR_TOOLS_BY_SYSTEM["microsoft_admin"]


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
        "quoted azure_cli": re.compile(r"['\"]azure_cli['\"]"),
        "provider azure": re.compile(r"\bprovider\b\s*(?:==|=|:)\s*['\"]azure['\"]"),
        "connector_key azure": re.compile(r"\bconnector_key\b\s*(?:==|=|:)\s*['\"]azure['\"]"),
        "target_system azure": re.compile(r"\btarget_system\b\s*(?:==|=|:)\s*['\"]azure['\"]"),
    }

    violations: list[str] = []
    for active_root in active_roots:
        for path in active_root.rglob("*"):
            if not path.suffix in {".py", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8")
            for label, pattern in banned_patterns.items():
                if pattern.search(text):
                    violations.append(f"{path.relative_to(repo_root)} contains {label}")

    assert violations == []
