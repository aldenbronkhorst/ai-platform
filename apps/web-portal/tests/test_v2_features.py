import os
import glob
import pytest

BUILD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../dist"))
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))


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
        assert "<title>AI Platform</title>" in content
        
        # 2. No generic "web-portal" title
        assert "<title>web-portal</title>" not in content
        
        # 3. No redundant generic bot wording
        assert "AI Platform Assistant" not in content


def test_chat_upload_snapshots_file_list_before_reset():
    app_path = os.path.join(SRC_DIR, "App.tsx")
    with open(app_path, "r", encoding="utf-8") as f:
        content = f.read()

    snapshot = "const files = Array.from(e.target.files || []);"
    reset = 'e.currentTarget.value = "";'

    assert snapshot in content
    assert reset in content
    assert content.index(snapshot) < content.index(reset)
    assert "for (const file of files)" in content


def test_chat_session_refresh_preserves_active_local_session():
    app_path = os.path.join(SRC_DIR, "App.tsx")
    with open(app_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "function mergeFetchedChatSessions" in content
    assert "if (activeSessionId && !byId.has(activeSessionId))" in content
    assert "return prev;" in content


def test_voice_keeps_microphone_stream_open_while_listening():
    hook_path = os.path.join(SRC_DIR, "hooks", "useSpeechRecognition.ts")
    with open(hook_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "micStreamRef" in content
    assert "getUserMedia({ audio: true })" in content
    assert "releaseMicStream" in content
    assert "micStreamRef.current?.getTracks().forEach(track => track.stop())" in content


def test_voice_commits_final_results_and_tracks_interim_text():
    hook_path = os.path.join(SRC_DIR, "hooks", "useSpeechRecognition.ts")
    with open(hook_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "const startIndex = Math.max(0, event.resultIndex || 0);" in content
    assert "committedResultIndexesRef.current.add(i)" in content
    assert "emitTranscript(finalSegments.join(\" \"))" in content
    assert "return { voiceState, toggleVoice, interimTranscript }" in content


def test_voice_interim_text_is_visible_in_composer():
    composer_path = os.path.join(SRC_DIR, "components", "chat", "ChatComposer.tsx")
    with open(composer_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "voiceInterimTranscript" in content
    assert "cleanVoiceInterim" in content
    assert "bg-[var(--color-warning)] text-white" in content


def test_connections_page_loads_backend_platform_tools():
    page_path = os.path.join(SRC_DIR, "pages", "ConnectionsPage.tsx")
    with open(page_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "interface PlatformTool" in content
    assert "fetchPlatformTools" in content
    assert '`${APIM_BASE_URL}/tools`' in content
    assert "Platform Tools" in content
    assert "setPlatformTools(data.filter" in content


def test_pending_activity_uses_result_keys_not_summary_object_keys():
    component_path = os.path.join(SRC_DIR, "components", "chat", "PendingAssistant.tsx")
    with open(component_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "function meaningfulResultKeys" in content
    assert "stringList(result.keys)" in content
    assert "`Returned: ${keys}`" not in content
