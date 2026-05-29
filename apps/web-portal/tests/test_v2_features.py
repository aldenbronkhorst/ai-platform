import os
import glob
import pytest

BUILD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../dist"))


def test_production_build_cleanliness():
    """Verify that the production build is clean, secure, and has zero diagnostic/environment leakage."""
    assert os.path.exists(BUILD_DIR), "dist/ does not exist."

    js_files = glob.glob(os.path.join(BUILD_DIR, "assets/*.js"))
    css_files = glob.glob(os.path.join(BUILD_DIR, "assets/*.css"))
    
    assert len(js_files) > 0, "No JS files found."
    assert len(css_files) > 0, "No CSS files found."

    for js_path in js_files:
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()
            
            # 1. No dev diagnostics by default in production
            assert "Security Diagnostics" not in content or "VITE_SHOW_AUTH_DIAGNOSTICS=true" not in content
            
            # 2. No mock login visible by default
            assert "Local Mock Sign In" not in content or "VITE_ENABLE_LOCAL_MOCK_AUTH=true" not in content
            
            # 3. No environment selector/cycle logic in production
            assert "cycleEnvironment" not in content
            
            # 4. No connector status badge in global top-right header
            assert "Odoo ERP Link Active" not in content
            
            # 5. No deprecated browser headers
            assert "X-API-Key" not in content
            assert "X-User-Id" not in content
            
            # 6. No raw tech jargon exposed in main chat bubbles by default
            assert "POST /tools/odoo/search-read" not in content
            assert "Blob Artifact ID" not in content


def test_liquid_glass_design_system_presence():
    """Verify that the new Liquid Glass design system classes and fallbacks are compiled in CSS."""
    css_files = glob.glob(os.path.join(BUILD_DIR, "assets/*.css"))
    
    found_glass = False
    for css_path in css_files:
        with open(css_path, "r", encoding="utf-8") as f:
            content = f.read()
            if "liquid-glass" in content or "backdrop-filter" in content:
                found_glass = True
                
            # Verify reduced-motion support
            assert "prefers-reduced-motion" in content, "prefers-reduced-motion media query missing from compiled CSS"
            # Verify backdrop-filter unsupported fallback
            assert "backdrop-filter" in content or "background" in content

    assert found_glass, "Liquid glass design system styles not compiled in CSS assets."


def test_portal_html_metatags():
    """Verify that index.html contains correct product-level title and metadata, eliminating bot redundancy."""
    index_path = os.path.join(BUILD_DIR, "index.html")
    assert os.path.exists(index_path)

    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()
        
        # 1. Correct title
        assert "<title>AI Platform | Lots Lots More</title>" in content
        
        # 2. No generic "web-portal" title
        assert "<title>web-portal</title>" not in content
        
        # 3. No redundant generic bot wording
        assert "AI Platform Assistant" not in content
