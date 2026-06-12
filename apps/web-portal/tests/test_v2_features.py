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
            
            # 2. No mock login code in production
            assert "Local Mock Sign In" not in content
            assert "VITE_ENABLE_LOCAL_MOCK_AUTH" not in content
            
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
    chat_controller_path = os.path.join(SRC_DIR, "chat", "useChatController.ts")
    with open(chat_controller_path, "r", encoding="utf-8") as f:
        content = f.read()

    snapshot = "const files = Array.from(e.target.files || []);"
    reset = 'e.currentTarget.value = "";'

    assert snapshot in content
    assert reset in content
    assert content.index(snapshot) < content.index(reset)
    assert "for (const file of files)" in content


def test_chat_session_refresh_preserves_active_local_session():
    chat_controller_path = os.path.join(SRC_DIR, "chat", "useChatController.ts")
    runtime_path = os.path.join(SRC_DIR, "chat", "runtime.ts")
    with open(chat_controller_path, "r", encoding="utf-8") as f:
        chat_controller_content = f.read()
    with open(runtime_path, "r", encoding="utf-8") as f:
        runtime_content = f.read()

    assert "mergeFetchedChatSessions" in chat_controller_content
    assert "export function mergeFetchedChatSessions" in runtime_content
    assert "if (activeSessionId && !byId.has(activeSessionId))" in runtime_content
    assert "return prev;" in chat_controller_content


def test_voice_uses_server_transcription_without_browser_speech_recognition():
    hook_path = os.path.join(SRC_DIR, "hooks", "useSpeechRecognition.ts")
    chat_controller_path = os.path.join(SRC_DIR, "chat", "useChatController.ts")
    with open(hook_path, "r", encoding="utf-8") as f:
        hook_content = f.read()
    with open(chat_controller_path, "r", encoding="utf-8") as f:
        chat_controller_content = f.read()

    assert "AudioContext" in hook_content
    assert "getUserMedia" in hook_content
    assert "transcribeAudio" in hook_content
    assert "audio/wav" in hook_content
    assert "window.SpeechRecognition" not in hook_content
    assert "webkitSpeechRecognition" not in hook_content
    assert "recognition.start()" not in hook_content
    assert "recognitionRef" not in hook_content
    assert "trimAndNormalizeAudio" in hook_content
    assert "downsampleAudio" in hook_content
    assert "/voice/transcribe" in chat_controller_content
    assert "FormData" in chat_controller_content
    assert "Authorization: `Bearer ${accessToken}`" in chat_controller_content


def test_voice_records_wav_then_submits_transcript():
    hook_path = os.path.join(SRC_DIR, "hooks", "useSpeechRecognition.ts")
    with open(hook_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "navigator.mediaDevices.getUserMedia" in content
    assert "createScriptProcessor" in content
    assert "audioChunksRef.current.push" in content
    assert "encodeWav(processedSamples, TARGET_SAMPLE_RATE)" in content
    assert "onTranscriptRef.current(transcript)" in content
    assert "return { voiceState, toggleVoice, interimTranscript: \"\" }" in content


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


def test_connections_page_does_not_load_backend_platform_tools():
    page_path = os.path.join(SRC_DIR, "pages", "ConnectionsPage.tsx")
    with open(page_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "interface PlatformTool" not in content
    assert "fetchPlatformTools" not in content
    assert '`${API_BASE_URL}/tools`' not in content
    assert "APIM_BASE_URL" not in content
    assert "Platform Tools" not in content
    assert "canonicalPlatformTools" not in content


def test_microsoft_native_connectors_use_separate_native_sign_ins():
    page_path = os.path.join(SRC_DIR, "pages", "ConnectionsPage.tsx")
    with open(page_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert 'body: JSON.stringify({ scope_profile: "graph" })' not in content
    assert "MICROSOFT_CONSENT_STEPS" not in content
    assert "Authorize Missing Profiles" not in content
    assert "microsoft_admin" not in content
    assert "/connector/microsoft-native/" in content
    assert "Azure CLI" in content
    assert "Microsoft Graph" in content
    assert "Exchange Online" in content
    assert "Teams Admin" in content
    assert "SharePoint / PnP" in content
    assert "azure_cli" in content
    assert "microsoft_graph" in content
    assert "exchange_online" in content
    assert "teams_admin" in content
    assert "sharepoint_pnp" in content
    assert 'connectorKey === "sharepoint_pnp"' in content
    assert "window.prompt" in content
    assert "site_url" in content
    assert "openMicrosoftDeviceLogin(data.verification_url, authWindow)" in content
    assert 'window.open(data.verification_url, "_blank")' not in content


def test_microsoft_native_device_login_uses_verification_url_directly():
    page_path = os.path.join(SRC_DIR, "pages", "ConnectionsPage.tsx")
    with open(page_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "MICROSOFT_SESSION_RESET_URL" not in content
    assert "https://login.microsoftonline.com/common/oauth2/v2.0/logout" not in content
    assert "MICROSOFT_SESSION_RESET_DELAY_MS" not in content
    assert "openMicrosoftAuthWindow()" in content
    assert "openMicrosoftDeviceLogin" in content
    assert "targetWindow.location.href = targetUrl" in content


def test_pending_activity_uses_plain_user_facing_statuses():
    component_path = os.path.join(SRC_DIR, "components", "chat", "PendingAssistant.tsx")
    with open(component_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "Checking connected apps" in content
    assert "Writing the reply" in content
    assert "token" not in content.lower()
    assert "toolDetail" not in content
    assert "duration_ms" not in content
