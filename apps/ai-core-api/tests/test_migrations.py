import ast
from pathlib import Path


ALEMBIC_VERSION_NUM_MAX_LENGTH = 32
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _migration_string_assignment(module: ast.Module, name: str) -> str | None:
    for node in module.body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    return node.value.value
            continue
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


def test_alembic_revision_ids_fit_production_version_column():
    for path in MIGRATIONS_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        module = ast.parse(path.read_text())
        for field in ("revision", "down_revision"):
            value = _migration_string_assignment(module, field)
            if value is None:
                continue
            assert len(value) <= ALEMBIC_VERSION_NUM_MAX_LENGTH, f"{path.name} {field} is too long"
