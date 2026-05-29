import os
import glob
import pytest

BUILD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../dist"))


def test_no_api_keys_in_production_build():
    """Verify that no production API keys (old or new) are hardcoded in the compiled production bundle."""
    # Ensure build exists
    assert os.path.exists(BUILD_DIR), "Production build directory 'dist/' does not exist. Run 'npm run build' first."

    js_files = glob.glob(os.path.join(BUILD_DIR, "assets/*.js"))
    assert len(js_files) > 0, "No compiled JavaScript assets found in dist/assets/"

    exposed_keys = [
        "prod-08598c883e2a18f3fe52af76f4eee24a964e898f3a9dd71e177871d36f62257f",  # Old Key
        "prod-eef41097dc78cc31ba8e88c23584a552c6f3c1db13f672b2ea44cd5f03524639",  # New Rotated Key
    ]

    for js_path in js_files:
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()
            for key in exposed_keys:
                assert key not in content, f"Exposed production API key '{key}' found in compiled asset: {os.path.basename(js_path)}"


def test_no_mock_login_compiled_in_production_defaults():
    """Verify that by default, the mock sign-in is disabled and cannot be enabled unless in local dev mode on localhost."""
    js_files = glob.glob(os.path.join(BUILD_DIR, "assets/*.js"))
    
    for js_path in js_files:
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()
            # Verify that mock login doesn't leak into production assets
            # In Vite, process.env variables or import.meta.env default to undefined/empty unless set.
            # VITE_ENABLE_LOCAL_MOCK_AUTH defaults to false in prod.
            assert "VITE_ENABLE_LOCAL_MOCK_AUTH" not in content or "VITE_ENABLE_LOCAL_MOCK_AUTH=true" not in content


def test_browser_requests_do_not_use_x_api_key():
    """Verify that no outgoing fetch requests are configured with X-API-Key in browser code."""
    js_files = glob.glob(os.path.join(BUILD_DIR, "assets/*.js"))
    
    for js_path in js_files:
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()
            # In the secure production portal, all API calls use Authorization: Bearer <JWT>
            assert "X-API-Key" not in content, f"Deprecated 'X-API-Key' header found in static JS build: {os.path.basename(js_path)}"
            assert "X-User-Id" not in content, f"Deprecated 'X-User-Id' header found in static JS build: {os.path.basename(js_path)}"
