import os
import glob
import json
import pytest

BUILD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../dist"))
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
PUBLIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../public"))


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

        # 4. PWA/iOS install metadata for stable home-screen behavior
        assert 'rel="manifest" href="/manifest.webmanifest"' in content
        assert 'rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"' in content
        assert 'name="apple-mobile-web-app-capable" content="yes"' in content
        assert 'name="apple-mobile-web-app-title" content="AI Platform"' in content
        assert 'name="apple-mobile-web-app-status-bar-style" content="black-translucent"' in content


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
    runtime_path = os.path.join(SRC_DIR, "chat", "runtime.ts")
    with open(app_path, "r", encoding="utf-8") as f:
        app_content = f.read()
    with open(runtime_path, "r", encoding="utf-8") as f:
        runtime_content = f.read()

    assert "mergeFetchedChatSessions" in app_content
    assert "export function mergeFetchedChatSessions" in runtime_content
    assert "if (activeSessionId && !byId.has(activeSessionId))" in runtime_content
    assert "return prev;" in app_content


def test_voice_uses_browser_recognition_without_shadow_microphone_stream():
    hook_path = os.path.join(SRC_DIR, "hooks", "useSpeechRecognition.ts")
    with open(hook_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "SpeechRecognition" in content
    assert "micStreamRef" not in content
    assert "getUserMedia" not in content
    assert "recognition.start()" in content
    assert "recognitionRef.current?.stop()" in content


def test_voice_commits_final_results_and_tracks_interim_text():
    hook_path = os.path.join(SRC_DIR, "hooks", "useSpeechRecognition.ts")
    with open(hook_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "const startIndex = Math.max(0, event.resultIndex || 0);" in content
    assert "committedResultIndexesRef.current.add(i)" in content
    assert "spokenTranscriptRef" in content
    assert "emittedTranscriptRef" in content
    assert "const pending = pendingTranscript();" in content
    assert "markTranscriptEmitted(pending)" in content
    assert "restartTimerRef" in content
    assert "if (shouldListenRef.current)" in content
    assert "startRecognition();" in content
    assert "return { voiceState, toggleVoice, interimTranscript }" in content


def test_voice_interim_text_is_visible_in_composer():
    composer_path = os.path.join(SRC_DIR, "components", "chat", "ChatComposer.tsx")
    with open(composer_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "voiceInterimTranscript" in content
    assert "cleanVoiceInterim" in content
    assert "bg-[var(--color-warning)] text-white" in content


def test_ios_pwa_manifest_and_icons_are_declared():
    manifest_path = os.path.join(PUBLIC_DIR, "manifest.webmanifest")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["name"] == "AI Platform"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["theme_color"] == "#08060d"

    icon_sources = {icon["src"]: icon for icon in manifest["icons"]}
    assert icon_sources["/apple-touch-icon.png"]["sizes"] == "180x180"
    assert icon_sources["/pwa-icon-192.png"]["sizes"] == "192x192"
    assert icon_sources["/pwa-icon-512.png"]["sizes"] == "512x512"
    assert os.path.exists(os.path.join(PUBLIC_DIR, "apple-touch-icon.png"))
    assert os.path.exists(os.path.join(PUBLIC_DIR, "pwa-icon-192.png"))
    assert os.path.exists(os.path.join(PUBLIC_DIR, "pwa-icon-512.png"))


def test_auth_session_restores_ios_pwa_accounts_promptlessly_once():
    auth_session_path = os.path.join(SRC_DIR, "authSession.ts")
    auth_hook_path = os.path.join(SRC_DIR, "hooks", "usePortalAuth.ts")
    main_path = os.path.join(SRC_DIR, "main.tsx")
    app_path = os.path.join(SRC_DIR, "App.tsx")

    with open(auth_session_path, "r", encoding="utf-8") as f:
        auth_session = f.read()
    with open(auth_hook_path, "r", encoding="utf-8") as f:
        auth_hook = f.read()
    with open(main_path, "r", encoding="utf-8") as f:
        main = f.read()
    with open(app_path, "r", encoding="utf-8") as f:
        app = f.read()

    assert "AUTH_HINT_COOKIE" in auth_session
    assert "ai_platform_last_account" in auth_session
    assert "prompt: \"none\"" in auth_session
    assert "shouldAttemptPromptlessRestore" in auth_session
    assert "markPromptlessRestoreAttempted" in auth_session
    assert "window.sessionStorage.setItem(key, \"1\")" in auth_session
    assert "const storedHint = readStoredAuthHint();" in auth_hook
    assert "instance.loginRedirect(promptlessLoginRequest(loginRequest, storedHint))" in auth_hook
    assert "rememberAuthAccount(response.account || activeAccount)" in auth_hook
    assert "instance.acquireTokenRedirect(promptlessLoginRequest(loginRequest, activeAccount))" in auth_hook
    assert "clearStoredAuthHint()" in auth_hook
    assert "rememberAuthAccount(redirectResponse.account)" in main
    assert "loginRequestWithAuthHint(loginRequest, readStoredAuthHint())" in app


def test_connections_page_loads_backend_platform_tools():
    page_path = os.path.join(SRC_DIR, "pages", "ConnectionsPage.tsx")
    with open(page_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "interface PlatformTool" in content
    assert "fetchPlatformTools" in content
    assert '`${APIM_BASE_URL}/tools`' in content
    assert "Platform Tools" in content
    assert "canonicalPlatformTools(data)" in content


def test_microsoft_admin_connect_uses_one_interactive_sign_in():
    page_path = os.path.join(SRC_DIR, "pages", "ConnectionsPage.tsx")
    with open(page_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert 'body: JSON.stringify({ scope_profile: "graph" })' not in content
    assert "MICROSOFT_CONSENT_STEPS" not in content
    assert "Authorize Missing Profiles" not in content
    assert "One Microsoft Admin sign-in" in content
    assert "Refresh User Sign-In" in content
    assert "readiness_status" in content
    assert "AuthorizationProfileList" in content
    assert "authorization_profiles" in content


def test_pending_activity_uses_result_keys_not_summary_object_keys():
    component_path = os.path.join(SRC_DIR, "components", "chat", "PendingAssistant.tsx")
    with open(component_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "function meaningfulResultKeys" in content
    assert "stringList(result.keys)" in content
    assert "`Returned: ${keys}`" not in content
