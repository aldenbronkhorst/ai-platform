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
            assert "Security Diagnostics" not in content
            
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


def test_simplified_design_system_presence():
    """Verify that the simplified token-based design system is compiled in CSS."""
    css_files = glob.glob(os.path.join(BUILD_DIR, "assets/*.css"))

    found_tokens = False
    for css_path in css_files:
        with open(css_path, "r", encoding="utf-8") as f:
            content = f.read()
            if "--color-surface" in content and ".surface-panel" in content:
                found_tokens = True

            # Verify reduced-motion support
            assert "prefers-reduced-motion" in content, "prefers-reduced-motion media query missing from compiled CSS"
            assert "liquid-glass" not in content
            assert "glass-composer" not in content
            assert "glass-panel" not in content

    assert found_tokens, "Simplified design system styles not compiled in CSS assets."


def test_app_shell_is_edge_flush_without_outer_bubble():
    """The mounted app shell should match Hermes' edge-flush shell, not the old rounded glass frame."""
    app_shell_path = os.path.join(SRC_DIR, "components", "layout", "AppShell.tsx")
    css_path = os.path.join(SRC_DIR, "index.css")
    with open(app_shell_path, "r", encoding="utf-8") as f:
        app_shell = f.read()
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    assert 'data-slot="app-shell" className="flex h-full min-h-0 w-full' in app_shell
    assert 'className="flex h-screen min-h-0 w-screen' not in app_shell
    assert 'className="flex h-full min-h-0 w-full overflow-hidden"' not in app_shell
    assert "rounded-none" not in app_shell
    assert "[data-slot='app-shell']" not in css
    assert "#root {\n    isolation: isolate;" not in css
    assert "html,\n  body,\n  #root" in css
    assert "margin: 0;" in css
    assert "overflow: hidden;" in css


def test_sidebar_uses_hermes_codicon_row_chrome_not_old_nav_items():
    sidebar_path = os.path.join(SRC_DIR, "components", "layout", "SidebarPanel.tsx")
    css_path = os.path.join(SRC_DIR, "index.css")
    with open(sidebar_path, "r", encoding="utf-8") as f:
        sidebar = f.read()
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    assert 'import { Codicon } from "../ui/Codicon";' in sidebar
    assert "lucide-react" not in sidebar
    assert "nav-item" not in sidebar
    assert ".nav-item" not in css
    assert "sidebar-nav-row" in sidebar
    assert "sidebar-session-row" in sidebar
    assert 'name="robot"' in sidebar
    assert 'icon: "plug"' in sidebar
    assert 'icon: "symbol-misc"' in sidebar
    assert 'name="kebab-vertical"' in sidebar
    assert "Pinned" not in sidebar
    assert "Shift-click a chat to pin" not in sidebar
    assert 'name="pin"' not in sidebar
    assert "sidebar-pinned-empty" not in css
    assert "sidebar-kbd" not in sidebar
    assert "sidebar-kbd" not in css
    assert "⌘" not in sidebar
    assert "sidebar-section-label" in css
    assert "sidebar-session-dot" not in sidebar
    assert "sidebar-session-dot" not in css


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
    assert "const uploads = validFiles.map(file => ({" in content
    assert "await Promise.all(uploads.map(async" in content


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


def test_new_chat_button_opens_draft_before_backend_session_create():
    app_path = os.path.join(SRC_DIR, "App.tsx")
    chat_controller_path = os.path.join(SRC_DIR, "chat", "useChatController.ts")
    with open(app_path, "r", encoding="utf-8") as f:
        app_content = f.read()
    with open(chat_controller_path, "r", encoding="utf-8") as f:
        controller = f.read()

    start_index = controller.index("const startNewChat = useCallback")
    persist_index = controller.index("const createPersistedChatSession = useCallback")
    start_body = controller[start_index:persist_index]

    assert "startNewChat" in app_content
    assert "void createNewChat();" not in app_content
    assert "isDraftChatRef.current = true;" in start_body
    assert "setIsDraftChat(true);" in start_body
    assert "setActiveSession(null);" in start_body
    assert "setChatMessages([]);" in start_body
    assert "fetch(`${API_BASE_URL}/chat/sessions`" not in start_body
    assert "const currentSession = activeSession || await createPersistedChatSession();" in controller
    assert "setIsDraftChat(false);" in controller[persist_index:]
    assert "if (isDraftChatRef.current) return null;" in controller


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
    assert "fetchWithAuth" in chat_controller_content
    assert "getAccessToken" in chat_controller_content


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
    assert "bg-[var(--ui-control-active-background)] text-foreground" in content
    assert "text-[var(--ui-text-secondary)]" in content


def test_ai_provider_page_shows_active_items_first_with_add_more_sections():
    page_path = os.path.join(SRC_DIR, "pages", "AIProvidersPage.tsx")
    css_path = os.path.join(SRC_DIR, "index.css")
    with open(page_path, "r", encoding="utf-8") as f:
        page = f.read()
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    assert "function isActiveProvider" in page
    assert 'provider.api_key_status === "saved"' in page
    assert "const allModelRows = useMemo" in page
    assert "const activeProviderRows = useMemo" in page
    assert "const availableProviderRows = useMemo" in page
    assert "const activeModelRows = useMemo" in page
    assert "const inactiveModelRows = useMemo" in page
    assert "return providerRows.filter(row => isActiveProvider(row.provider));" in page
    assert "return providerRows.filter(row => !isActiveProvider(row.provider));" in page
    assert "return allModelRows.filter(row => isActiveProvider(row.provider) && boolValue(row.model.enabled));" in page
    assert "return allModelRows.filter(row => !isActiveProvider(row.provider) || !boolValue(row.model.enabled));" in page
    assert "Add more providers" in page
    assert "Show inactive models" in page
    assert "filteredActiveProviderRows.map(row => renderProviderRow(row))" in page
    assert "filteredAvailableProviderRows.map(row => renderProviderRow(row))" in page
    assert "filteredActiveModelRows.map(row => renderModelRow(row))" in page
    assert "filteredInactiveModelRows.map(row => renderModelRow(row))" in page
    assert ".settings-secondary-section" in css
    assert ".settings-disclosure-row" in css
    assert ".settings-count" in css


def test_chat_composer_focuses_like_hermes_on_session_change():
    composer_path = os.path.join(SRC_DIR, "components", "chat", "ChatComposer.tsx")
    view_path = os.path.join(SRC_DIR, "components", "chat", "ChatView.tsx")
    with open(composer_path, "r", encoding="utf-8") as f:
        composer = f.read()
    with open(view_path, "r", encoding="utf-8") as f:
        view = f.read()

    assert "focusKey?: string | null" in composer
    assert "const focusInput = useCallback" in composer
    assert "el.focus({ preventScroll: true })" in composer
    assert "window.requestAnimationFrame(focus)" in composer
    assert "window.setTimeout(focus, 0)" in composer
    assert "}, [focusInput, focusKey]);" in composer
    assert 'focusKey={activeSession?.id ?? "new"}' in view


def test_chat_composer_stacks_from_measured_wrap_like_hermes():
    composer_path = os.path.join(SRC_DIR, "components", "chat", "ChatComposer.tsx")
    with open(composer_path, "r", encoding="utf-8") as f:
        composer = f.read()

    assert "const COMPOSER_STACK_BREAKPOINT_PX = 320;" in composer
    assert "const COMPOSER_SINGLE_LINE_MAX_PX = 36;" in composer
    assert "const rootRef = useRef<HTMLDivElement>(null);" in composer
    assert "const surfaceRef = useRef<HTMLDivElement>(null);" in composer
    assert "const lastTightRef = useRef<boolean | null>(null);" in composer
    assert "const isStacked = isComposerExpanded || isComposerTight;" in composer
    assert "data-stacked={isStacked ? \"\" : undefined}" in composer
    assert "new ResizeObserver(syncComposerMetrics)" in composer
    assert "textarea.scrollHeight > COMPOSER_SINGLE_LINE_MAX_PX" in composer
    assert "chatInput.trimEnd().includes(\"\\n\")" in composer
    assert "chatInput.length >" not in composer
    assert "hasWrappedText" not in composer


def test_attachment_filename_hover_uses_portal_tooltip_not_native_title():
    attachment_path = os.path.join(SRC_DIR, "components", "chat", "FileAttachmentTile.tsx")
    tooltip_path = os.path.join(SRC_DIR, "components", "ui", "tooltip.tsx")
    css_path = os.path.join(SRC_DIR, "index.css")
    with open(attachment_path, "r", encoding="utf-8") as f:
        attachment = f.read()
    with open(tooltip_path, "r", encoding="utf-8") as f:
        tooltip = f.read()
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    assert 'import { Tip } from "../ui/tooltip";' in attachment
    assert "const tooltip = (" in attachment
    assert "<Tip label={tooltip} side=\"top\">" in attachment
    assert "TooltipPrimitive.Portal" in tooltip
    assert "bg-[var(--color-surface-raised)]" in tooltip
    assert "text-[var(--color-text)]" in tooltip
    assert "border-[var(--color-border)]" in tooltip
    assert "text-[var(--color-bg)]" not in tooltip
    assert "text-background" not in tooltip
    assert "title={filename}" not in attachment
    assert "artifactType === \"chat-generated\"" in attachment
    assert "file-attachment-tile-generated" in attachment
    assert "aria-disabled={!interactive || undefined}" in attachment
    assert "file-attachment-tooltip" not in attachment
    assert ".file-attachment-tooltip" not in css
    assert ".file-attachment-tip" in css


def test_user_message_attachments_scroll_below_sticky_bubble():
    messages_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    with open(messages_path, "r", encoding="utf-8") as f:
        content = f.read()

    root_start = content.index('<MessagePrimitive.Root\n        className="conversation-turn conversation-user-turn')
    root_end = content.index("</MessagePrimitive.Root>", root_start)
    sticky_root = content[root_start:root_end]

    assert "function UserMessageAttachments" in content
    assert 'data-slot="aui_user-message-attachments"' in content
    assert "conversation-attachments" not in sticky_root
    assert "<UserMessageAttachments attachments={attachments} onOpenAttachment={onOpenAttachment} />" in content[root_end:]


def test_markdown_renderer_uses_streamdown_with_preprocessing():
    renderer_path = os.path.join(SRC_DIR, "components", "chat", "MarkdownRenderer.tsx")
    with open(renderer_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "@assistant-ui/react-streamdown" in content
    assert "StreamdownTextPrimitive" in content
    assert "TextMessagePartProvider" in content
    assert "preprocessMarkdown(text)" in content
    assert "tailBoundedRemend" in content
    assert "parseMarkdownIntoBlocksCached" in content


def test_markdown_preprocess_does_not_patch_pipe_table_delimiters():
    preprocess_path = os.path.join(SRC_DIR, "lib", "markdown-preprocess.ts")
    assistant_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    compact_markdown_path = os.path.join(SRC_DIR, "components", "chat", "CompactMarkdown.tsx")
    with open(preprocess_path, "r", encoding="utf-8") as f:
        preprocess = f.read()
    with open(assistant_path, "r", encoding="utf-8") as f:
        assistant = f.read()
    with open(compact_markdown_path, "r", encoding="utf-8") as f:
        compact_markdown = f.read()

    assert "repairPipeTableDelimiters" not in preprocess
    assert "TABLE_DELIMITER_CELL_RE" not in preprocess
    assert "CompactMarkdown" in assistant
    assert "Streamdown" in compact_markdown
    assert "MarkdownTable" in compact_markdown


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
    assert "rememberAuthAccount(response.account || account)" in auth_hook
    assert "TOKEN_REFRESH_INTERVAL_MS" in auth_hook
    assert "window.addEventListener(\"focus\", refreshWhenActive)" in auth_hook
    assert "instance.acquireTokenRedirect(promptlessLoginRequest(loginRequest, account))" in auth_hook
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


def test_connections_page_is_odoo_only_and_has_no_removed_connector_flows():
    page_path = os.path.join(SRC_DIR, "pages", "ConnectionsPage.tsx")
    with open(page_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "OdooConnectorSection" in content
    assert "MICROSOFT_CONSENT_STEPS" not in content
    assert "Authorize Missing Profiles" not in content
    assert "microsoft_admin" not in content
    assert "CONNECTOR_FALLBACKS" not in content
    assert "CONNECTOR_FALLBACK_BY_KEY" not in content
    assert "connectorDefinitions(meta: Record<string, ConnectorMeta> | null)" in content
    assert "/connector/microsoft-native/" not in content
    assert "/connector/github/" not in content
    assert "azure_cli" not in content
    assert "microsoft_graph" not in content
    assert "exchange_online" not in content
    assert "teams_admin" not in content
    assert "sharepoint_pnp" not in content
    assert "github" not in content.lower()
    assert "window.prompt" not in content


def test_connections_shared_types_have_no_device_code_contract():
    page_path = os.path.join(SRC_DIR, "pages", "ConnectionsPage.tsx")
    with open(page_path, "r", encoding="utf-8") as f:
        page = f.read()
    shared_path = os.path.join(SRC_DIR, "components", "connections", "connectionShared.ts")
    with open(shared_path, "r", encoding="utf-8") as f:
        shared = f.read()

    combined = page + shared
    assert "MicrosoftNativeDeviceCode" not in combined
    assert "openMicrosoftAuthWindow" not in combined
    assert "openMicrosoftDeviceLogin" not in combined
    assert "verification_url" not in combined


def test_chat_uses_assistant_ui_message_parts_not_local_tool_trail():
    assistant_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    chat_view_path = os.path.join(SRC_DIR, "components", "chat", "ChatView.tsx")
    runtime_path = os.path.join(SRC_DIR, "chat", "runtime.ts")
    with open(assistant_path, "r", encoding="utf-8") as f:
        assistant = f.read()
    with open(chat_view_path, "r", encoding="utf-8") as f:
        chat_view = f.read()
    with open(runtime_path, "r", encoding="utf-8") as f:
        runtime = f.read()

    assert "MessageBubble" not in chat_view
    assert "ToolTrail" not in chat_view + assistant
    assert "PendingAssistant" not in chat_view + assistant
    assert "AssistantRuntimeProvider" in assistant
    assert "useIncrementalExternalStoreRuntime" in assistant
    assert "ExportedMessageRepository.fromBranchableArray" in assistant
    assert "ExportedMessageRepository.fromArray" not in assistant
    assert "useExternalStoreRuntime" not in assistant
    assert "ThreadPrimitive.MessageByIndex" in assistant
    assert "useStickToBottom" in assistant
    assert "MessagePrimitive.Parts" in assistant
    assert "ReasoningGroup" in assistant
    assert "ToolGroup" in assistant
    assert "tools: { Fallback: ToolFallback }" in assistant
    assert "message_parts" in runtime
    assert "agent_trail" not in runtime
    assert 'reasoning: ""' not in runtime
    assert "appendReasoningPart" in runtime
    assert "upsertToolCallPart" in runtime
    assert "ThinkingDisclosure" in assistant
    assert "ToolNode" not in assistant
    assert "trail_events" not in runtime
    assert "stream_work_items" not in runtime + assistant
    assert "stream_reasoning" not in runtime
    assert "reasoning_content" not in assistant
    assert "stream_reasoning" not in assistant
    assert "Checking connected apps" not in assistant
    assert "Writing the reply" not in assistant
    assert "toolDetail" not in assistant


def test_completed_assistant_messages_use_canonical_markdown_content():
    assistant_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    with open(assistant_path, "r", encoding="utf-8") as f:
        assistant = f.read()

    assert 'if (!running && message.content.trim())' in assistant
    assert '...parts.filter(part => part.type !== "text")' in assistant
    assert '{ type: "text", text: message.content }' in assistant


def test_reasoning_stream_matches_hermes_message_part_rules():
    assistant_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    runtime_path = os.path.join(SRC_DIR, "chat", "runtime.ts")
    with open(assistant_path, "r", encoding="utf-8") as f:
        assistant = f.read()
    with open(runtime_path, "r", encoding="utf-8") as f:
        runtime = f.read()

    assert "THINKING_STATUS_PREFIX_RE" in runtime
    assert "EMPTY_THINKING_PLACEHOLDER_RE" in runtime
    assert "function coerceThinkingText" in runtime
    assert "function appendTextPart" in runtime
    assert "function mergeStreamText" in runtime
    assert "content = mergeStreamText(content, delta)" in runtime
    assert 'part.type === "tool-call"' in runtime
    assert 'type === "thinking.delta"' in runtime
    assert 'type === "reasoning.delta" || type === "reasoning.available"' in runtime
    assert "status chrome, not visible reasoning" in runtime
    assert "coerceThinkingText(event.text ?? event.delta)" in runtime
    assert "appendReasoningPart(messageParts, delta)" in runtime
    assert 'status = "streaming";' in runtime
    assert "replaceReasoningPart" in runtime
    assert "function runtimePartsFromMetadata" in assistant
    assert "text(part.text)" in assistant
    assert "rawText(raw.reasoning)" not in assistant
    assert "rawText(raw.thinking)" not in assistant
    assert "trail.reasoning" not in assistant
    assert "const displayText = value.trimStart();" in assistant
    assert "live && index === orderedParts.length - 1" not in assistant


def test_thread_message_list_uses_hermes_part_count_signature():
    assistant_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    with open(assistant_path, "r", encoding="utf-8") as f:
        assistant = f.read()

    assert "function contentWeight" not in assistant
    assert "partText.length / 400" not in assistant
    assert "message.content?.length ?? 1" in assistant
    assert "MessageRenderBoundary resetKey={messageSignature}" in assistant


def test_thinking_disclosure_body_matches_hermes_conditional_mount():
    assistant_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    with open(assistant_path, "r", encoding="utf-8") as f:
        assistant = f.read()

    start = assistant.index("function ThinkingDisclosure")
    end = assistant.index("function ReasoningGroup", start)
    thinking = assistant[start:end]

    assert "{open && (" in thinking
    assert 'isPreview && "thinking-preview max-h-40"' in thinking
    assert "useDisclosureOpen(disclosureId, Boolean(pending))" in thinking
    assert "setOpen(true)" in thinking
    assert "setOpen(value => !value)" in thinking
    assert "userOpen" not in thinking
    assert "userOpen ?? Boolean(pending)" not in thinking
    assert "aria-hidden={!open}" not in thinking
    assert '!open && "hidden"' not in thinking


def test_reasoning_blocks_keep_independent_live_state():
    assistant_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    with open(assistant_path, "r", encoding="utf-8") as f:
        assistant = f.read()

    assert 'timerKey={`reasoning:${messageId}:${startIndex}-${endIndex}`}' in assistant
    assert 'const isRunning = status?.type === "running";' in assistant
    assert 'status?.type === "running" || messageRunning' not in assistant


def test_tool_and_thinking_disclosures_use_stable_row_state():
    assistant_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    with open(assistant_path, "r", encoding="utf-8") as f:
        assistant = f.read()

    assert "const disclosureStates = new Map<string, boolean>();" in assistant
    assert "function useDisclosureOpen" in assistant
    assert "useSyncExternalStore(" in assistant
    assert "DISCLOSURE_STATE_LIMIT = 240" in assistant
    assert 'disclosureId={`reasoning:${messageId}:${startIndex}-${endIndex}`}' in assistant
    assert 'const disclosureId = `tool-entry:${messageId}:${toolCallId || `${toolName}:${stableDisclosureHash(safeJson(args))}`}`;' in assistant
    assert "const [open, setOpen] = useState(false);" not in assistant


def test_live_reasoning_reveal_keeps_hermes_ref_sync():
    renderer_path = os.path.join(SRC_DIR, "components", "chat", "MarkdownRenderer.tsx")
    animation_path = os.path.join(SRC_DIR, "lib", "use-enter-animation.ts")
    with open(renderer_path, "r", encoding="utf-8") as f:
        renderer = f.read()
    with open(animation_path, "r", encoding="utf-8") as f:
        animation = f.read()

    assert "const [displayed, setDisplayed] = useState(text);" in renderer
    assert 'useState(isRunning ? "" : text)' not in renderer
    assert "shownRef.current = displayed;" in renderer
    assert "targetRef.current = text;" in renderer
    assert "function commonPrefixLength" in renderer
    assert "targetRef.current.slice(0, prefixLength)" in renderer
    assert "}, [text, isRunning]);" in renderer
    assert "}, [text, isRunning, displayed]);" not in renderer
    assert "const revealed = useSmoothReveal(text, isRunning)" in renderer
    assert "isRunning || revealed !== text" in renderer
    assert "const enabledRef = useRef(enabled);" in animation
    assert "const keyRef = useRef(animationKey);" in animation
    assert "enabledRef.current = enabled;" in animation
    assert "keyRef.current = animationKey;" in animation
    assert "}, []);" in animation
    assert "}, [animationKey, enabled]);" not in animation


def test_stream_updates_apply_each_chunk_once():
    controller_path = os.path.join(SRC_DIR, "chat", "useChatController.ts")
    runtime_path = os.path.join(SRC_DIR, "chat", "runtime.ts")
    with open(controller_path, "r", encoding="utf-8") as f:
        controller = f.read()
    with open(runtime_path, "r", encoding="utf-8") as f:
        runtime = f.read()

    assert "let pendingStreamMessage: ChatMessage | null = null;" in controller
    assert "const updatedMessage = updater(localMessage);" in controller
    assert "pendingStreamMessage = updatedMessage;" in controller
    assert "replaceOrAppendMessage(prev, pendingMessageId, updatedMessage)" in controller
    assert "upsertLocalMessage(session.id, updatedMessage)" in controller
    assert "/cancel`" in controller
    assert "keepalive: true" in controller
    assert "messagePartsFrom(metadata.message_parts)" in runtime
    assert "metadata.message_parts = messageParts" in runtime
    assert "stream_work_items" not in runtime


def test_markdown_code_blocks_use_hermes_code_card_renderer():
    markdown_path = os.path.join(SRC_DIR, "components", "chat", "MarkdownRenderer.tsx")
    highlighter_path = os.path.join(SRC_DIR, "components", "chat", "ShikiHighlighter.tsx")
    code_card_path = os.path.join(SRC_DIR, "components", "chat", "CodeCard.tsx")
    css_path = os.path.join(SRC_DIR, "index.css")

    with open(markdown_path, "r", encoding="utf-8") as f:
        markdown = f.read()
    with open(highlighter_path, "r", encoding="utf-8") as f:
        highlighter = f.read()
    with open(code_card_path, "r", encoding="utf-8") as f:
        code_card = f.read()
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    assert 'import { SyntaxHighlighter } from "./ShikiHighlighter";' in markdown
    assert "type SyntaxHighlighterProps" in markdown
    assert "SyntaxHighlighter: (props: SyntaxHighlighterProps)" in markdown
    assert "<SyntaxHighlighter {...props} defer={isStreaming} />" in markdown
    assert "react-shiki" in highlighter
    assert 'theme={SHIKI_THEME}' in highlighter
    assert 'defaultColor="light-dark()"' in highlighter
    assert "PlainCode" in highlighter
    assert "isLikelyProseCodeBlock" in highlighter
    assert 'data-slot="code-card"' in code_card
    assert 'data-streamdown=\'code-block\'' in css
    assert "[data-slot='code-card']" in css
    assert ".aui-shiki" in css


def test_user_messages_use_hermes_sticky_bubble_path_without_local_overlay():
    assistant_path = os.path.join(SRC_DIR, "components", "chat", "AssistantMessages.tsx")
    actions_path = os.path.join(SRC_DIR, "components", "chat", "MessageActions.tsx")
    chat_view_path = os.path.join(SRC_DIR, "components", "chat", "ChatView.tsx")
    css_path = os.path.join(SRC_DIR, "index.css")

    with open(assistant_path, "r", encoding="utf-8") as f:
        assistant = f.read()
    with open(actions_path, "r", encoding="utf-8") as f:
        actions = f.read()
    with open(chat_view_path, "r", encoding="utf-8") as f:
        chat_view = f.read()
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    assert "ResizeObserver" in assistant
    assert "clampInnerRef" in assistant
    assert 'className="sticky-human-clamp"' in assistant
    assert 'data-slot="aui_user-message-root"' in assistant
    assert 'bg-[var(--ui-chat-surface-background)]' in assistant
    assert "conversation-attachments" in assistant
    assert 'aria-label="Edit message"' in assistant
    assert 'role="user"' not in assistant.replace('data-role="user"', "")
    assert "role === \"user\"" not in actions
    assert "Pencil" not in actions
    assert "isUserScrolledUp" not in chat_view
    assert 'data-following={isAtBottom ? "true" : "false"}' in assistant
    assert 'data-editing={editingMessageId ? "true" : undefined}' in assistant
    assert "padding: 0.5rem 2.25rem" not in css
    assert ".conversation-user-bubble:hover" in css
