import os
import pytest
from app.main import app
from app.core.config import get_settings

# Force debug/test override at test runtime to allow anonymous localhost/test bypass
os.environ["DEBUG"] = "true"
get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    """Shared pytest fixture that clears FastAPI dependency overrides before and after every test.

    This ensures that persistent dependency overrides (e.g. mock_get_db) do not leak
    across test modules and cause unexpected side-effects (such as 401 Unauthorized
    or session mismatches) when the full test suite is executed together.
    """
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()
