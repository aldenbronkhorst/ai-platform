import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import type { AttachedFile, ChatAttachment, ChatMessage, ChatSession } from "../types";
import { API_BASE_URL, fetchWithAuth, fetchWithTimeout, type AccessTokenGetter } from "../hooks/useApi";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import {
  applyChatStreamEvent,
  chatFailureFromResponse,
  CHAT_EVENT_RECONNECT_MS,
  messageRequestId,
  normalizeChatMessage,
  parseSseChunk,
  patchChatSession,
  pendingProgressMetadata,
  sortChatSessions,
  uploadFailureFromResponse,
} from "./runtime";

interface UseChatControllerOptions {
  accessToken: string | null;
  activeUserEmail: string;
  getAccessToken: AccessTokenGetter;
  onOpenChat: () => void;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function wait(ms: number, signal?: AbortSignal) {
  return new Promise<void>(resolve => {
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRunningMessage(message: ChatMessage) {
  return message.role === "assistant" && ["pending", "sending", "streaming", "tool_running"].includes(message.status || "");
}

function failedMessage(requestId: string, message: string): Partial<ChatMessage> {
  return {
    status: "failed",
    error_message: JSON.stringify({
      requestId,
      errorType: "network",
      errorMessage: message,
      httpStatus: 0,
    }),
  };
}

export function useChatController({ accessToken, activeUserEmail, getAccessToken, onOpenChat }: UseChatControllerOptions) {
  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [isSessionsLoading, setIsSessionsLoading] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isMessagesLoading, setIsMessagesLoading] = useState(false);
  const [isDraftChat, setIsDraftChat] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [sendingSessionIds, setSendingSessionIds] = useState<string[]>([]);
  const [streamVersion, setStreamVersion] = useState(0);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const activeSessionId = activeSession?.id ?? null;
  const activeSessionIdRef = useRef<string | null>(activeSessionId);
  const isDraftChatRef = useRef(isDraftChat);
  const eventCursorsRef = useRef<Record<string, number>>({});
  const activeRequestsRef = useRef<Record<string, string>>({});

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    isDraftChatRef.current = isDraftChat;
  }, [isDraftChat]);

  const markSessionSending = useCallback((sessionId: string, requestId?: string) => {
    if (requestId) activeRequestsRef.current[sessionId] = requestId;
    setSendingSessionIds(current => current.includes(sessionId) ? current : [...current, sessionId]);
  }, []);

  const unmarkSessionSending = useCallback((sessionId: string, requestId?: string) => {
    if (requestId && activeRequestsRef.current[sessionId] && activeRequestsRef.current[sessionId] !== requestId) return;
    delete activeRequestsRef.current[sessionId];
    setSendingSessionIds(current => current.filter(id => id !== sessionId));
  }, []);

  const isActiveChatSending = activeSessionId ? sendingSessionIds.includes(activeSessionId) : false;

  const getHeaders = useCallback(async () => {
    const token = await getAccessToken({ redirectOnFailure: true });
    if (!token) throw new Error("Microsoft session expired. Please sign in again.");
    return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  }, [getAccessToken]);

  const updateLocalChatSession = useCallback((sessionId: string, patch: Partial<ChatSession>) => {
    setChatSessions(current => sortChatSessions(current.map(session => session.id === sessionId ? patchChatSession(session, patch) : session)));
    setActiveSession(current => current?.id === sessionId ? patchChatSession(current, patch) : current);
  }, []);

  const upsertChatSession = useCallback((session: ChatSession) => {
    setChatSessions(current => sortChatSessions([
      session,
      ...current.filter(item => item.id !== session.id),
    ]));
    setActiveSession(current => current?.id === session.id ? session : current);
  }, []);

  const fetchChatSessions = useCallback(async () => {
    if (!accessToken || !activeUserEmail) return;
    setIsSessionsLoading(true);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/chat/sessions`, { headers: await getHeaders() });
      if (!response.ok) throw new Error(`Could not load chats (HTTP ${response.status}).`);
      const sessions = sortChatSessions(await response.json() as ChatSession[]);
      setChatSessions(sessions);
      setActiveSession(current => {
        if (isDraftChatRef.current) return null;
        if (current) return sessions.find(session => session.id === current.id) || null;
        return sessions[0] || null;
      });
    } catch (error) {
      console.error("Failed to fetch chat sessions:", error);
    } finally {
      setIsSessionsLoading(false);
    }
  }, [accessToken, activeUserEmail, getHeaders]);

  const refreshChatSession = useCallback(async (sessionId: string) => {
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/chat/sessions/${sessionId}`, { headers: await getHeaders() });
      if (response.ok) upsertChatSession(await response.json() as ChatSession);
    } catch (error) {
      console.error("Failed to refresh chat session:", error);
    }
  }, [getHeaders, upsertChatSession]);

  const fetchSessionMessages = useCallback(async (sessionId: string) => {
    const response = await fetchWithTimeout(`${API_BASE_URL}/chat/sessions/${sessionId}/messages`, { headers: await getHeaders() });
    if (!response.ok) throw new Error(`Could not load messages (HTTP ${response.status}).`);
    const messages = (await response.json() as ChatMessage[]).map(normalizeChatMessage);
    if (activeSessionIdRef.current === sessionId) setChatMessages(messages);
    const running = [...messages].reverse().find(isRunningMessage);
    if (running) markSessionSending(sessionId, messageRequestId(running) || undefined);
    else if (!activeRequestsRef.current[sessionId]) unmarkSessionSending(sessionId);
    return Boolean(running || activeRequestsRef.current[sessionId]);
  }, [getHeaders, markSessionSending, unmarkSessionSending]);

  const handleStreamEvent = useCallback((sessionId: string, event: ReturnType<typeof parseSseChunk>["events"][number]) => {
    if (event.id !== null) eventCursorsRef.current[sessionId] = event.id;
    if (activeSessionIdRef.current === sessionId) {
      setChatMessages(current => applyChatStreamEvent(current, event));
    }
    const payload = isRecord(event.data) ? event.data : {};
    const requestId = typeof payload.request_id === "string" ? payload.request_id : undefined;
    if (event.event === "message.start") {
      markSessionSending(sessionId, requestId);
    } else if (["message.cancelled", "error", "turn.complete"].includes(event.event)) {
      unmarkSessionSending(sessionId, requestId);
      void refreshChatSession(sessionId);
    } else if (event.event === "session.title" && typeof payload.title === "string") {
      updateLocalChatSession(sessionId, { title: payload.title });
    }
  }, [markSessionSending, refreshChatSession, unmarkSessionSending, updateLocalChatSession]);

  useEffect(() => {
    if (!activeSessionId || !accessToken) {
      setChatMessages([]);
      setIsMessagesLoading(false);
      return;
    }

    const controller = new AbortController();
    let mounted = true;
    const followSession = async () => {
      setIsMessagesLoading(true);
      let shouldFollow = false;
      try {
        shouldFollow = await fetchSessionMessages(activeSessionId);
      } catch (error) {
        if (!controller.signal.aborted) console.error("Failed to fetch messages:", error);
      } finally {
        if (mounted) setIsMessagesLoading(false);
      }
      if (!shouldFollow || controller.signal.aborted) return;

      while (!controller.signal.aborted) {
        let turnComplete = false;
        try {
          const cursor = eventCursorsRef.current[activeSessionId];
          const suffix = cursor ? `?after=${cursor}` : "";
          const response = await fetch(`${API_BASE_URL}/chat/sessions/${activeSessionId}/events${suffix}`, {
            headers: await getHeaders(),
            signal: controller.signal,
          });
          if (!response.ok || !response.body) throw new Error(`Chat event stream failed with HTTP ${response.status}.`);

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (!controller.signal.aborted) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parsed = parseSseChunk(buffer);
            buffer = parsed.rest;
            parsed.events.forEach(event => {
              if (event.event === "turn.complete") turnComplete = true;
              handleStreamEvent(activeSessionId, event);
            });
          }
        } catch (error) {
          if (!controller.signal.aborted) console.error("Chat event stream disconnected:", error);
        }
        if (turnComplete) return;
        if (!controller.signal.aborted) await wait(CHAT_EVENT_RECONNECT_MS, controller.signal);
      }
    };

    void followSession();
    return () => {
      mounted = false;
      controller.abort();
    };
  }, [accessToken, activeSessionId, fetchSessionMessages, getHeaders, handleStreamEvent, streamVersion]);

  useEffect(() => {
    if (!activeUserEmail) {
      setChatSessions([]);
      setActiveSession(null);
      setChatMessages([]);
      return;
    }
    setIsDraftChat(false);
    isDraftChatRef.current = false;
    void fetchChatSessions();
  }, [activeUserEmail, fetchChatSessions]);

  useEffect(() => {
    const refresh = () => void fetchChatSessions();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [fetchChatSessions]);

  const startNewChat = useCallback(() => {
    isDraftChatRef.current = true;
    setIsDraftChat(true);
    setActiveSession(null);
    setChatMessages([]);
    setChatInput("");
    setAttachedFiles([]);
    onOpenChat();
  }, [onOpenChat]);

  const createPersistedChatSession = useCallback(async () => {
    const response = await fetch(`${API_BASE_URL}/chat/sessions`, {
      method: "POST",
      headers: await getHeaders(),
      body: JSON.stringify({ title: "New Chat" }),
    });
    if (!response.ok) throw new Error(`Failed to create a chat (HTTP ${response.status}).`);
    const session = await response.json() as ChatSession;
    isDraftChatRef.current = false;
    setIsDraftChat(false);
    upsertChatSession(session);
    setActiveSession(session);
    onOpenChat();
    return session;
  }, [getHeaders, onOpenChat, upsertChatSession]);

  const postChatTurn = useCallback(async (
    session: ChatSession,
    content: string,
    artifactIds: string[],
    requestId: string,
    pendingMessageId: string,
    replaceMessageId?: string,
  ) => {
    markSessionSending(session.id, requestId);
    try {
      const response = await fetch(`${API_BASE_URL}/chat/sessions/${session.id}/turns`, {
        method: "POST",
        headers: { ...(await getHeaders()), "X-Request-ID": requestId },
        body: JSON.stringify({ content, artifact_ids: artifactIds, replace_message_id: replaceMessageId }),
      });
      if (!response.ok) {
        const failure = await chatFailureFromResponse(response, requestId);
        setChatMessages(current => current.map(message => message.id === pendingMessageId ? {
          ...message,
          status: "failed",
          error_message: JSON.stringify(failure),
        } : message));
        unmarkSessionSending(session.id, requestId);
      } else {
        setStreamVersion(current => current + 1);
      }
    } catch (error) {
      setChatMessages(current => current.map(message => message.id === pendingMessageId ? {
        ...message,
        ...failedMessage(requestId, errorMessage(error)),
      } : message));
      unmarkSessionSending(session.id, requestId);
    }
  }, [getHeaders, markSessionSending, unmarkSessionSending]);

  const sendTurn = useCallback(async (
    session: ChatSession,
    content: string,
    attachments: ChatAttachment[],
    replaceMessageId?: string,
  ) => {
    const requestId = crypto.randomUUID();
    const pendingMessageId = crypto.randomUUID();
    const createdAt = new Date().toISOString();
    const localTurn: ChatMessage[] = [
      {
        id: crypto.randomUUID(),
        chat_session_id: session.id,
        role: "user",
        content,
        created_at: createdAt,
        status: "completed",
        metadata_json: { request_id: requestId, attachments },
        attachments,
      },
      {
        id: pendingMessageId,
        chat_session_id: session.id,
        role: "assistant",
        content: "",
        created_at: createdAt,
        status: "pending",
        metadata_json: pendingProgressMetadata(requestId, content, attachments.length, createdAt),
      },
    ];
    setChatMessages(current => [...current, ...localTurn]);
    updateLocalChatSession(session.id, { last_message_at: createdAt });
    await postChatTurn(
      session,
      content,
      attachments.map(attachment => attachment.id),
      requestId,
      pendingMessageId,
      replaceMessageId,
    );
  }, [postChatTurn, updateLocalChatSession]);

  const handleSendMessage = useCallback(async (event: FormEvent) => {
    event.preventDefault();
    if ((!chatInput.trim() && attachedFiles.length === 0) || !accessToken || isActiveChatSending) return;
    if (attachedFiles.some(file => file.uploading || file.error)) return;
    const content = chatInput;
    const attachments = attachedFiles.filter(file => file.artifact).map(file => file.artifact as ChatAttachment);
    setChatInput("");
    setAttachedFiles([]);
    try {
      const session = activeSession || await createPersistedChatSession();
      await sendTurn(session, content, attachments);
    } catch (error) {
      console.error("Failed to send chat message:", error);
      alert(errorMessage(error));
    }
  }, [accessToken, activeSession, attachedFiles, chatInput, createPersistedChatSession, isActiveChatSending, sendTurn]);

  const handleStopActiveChat = useCallback(() => {
    if (!activeSessionId) return;
    const requestId = activeRequestsRef.current[activeSessionId];
    if (!requestId) return;
    void getHeaders().then(headers => fetch(
      `${API_BASE_URL}/chat/sessions/${activeSessionId}/messages/${requestId}/cancel`,
      { method: "POST", headers },
    )).catch(error => console.error("Failed to stop chat turn:", error));
  }, [activeSessionId, getHeaders]);

  const handleRetryMessage = useCallback(async (messageId: string) => {
    if (!activeSession) return;
    const failedIndex = chatMessages.findIndex(message => message.id === messageId);
    const userMessage = [...chatMessages.slice(0, failedIndex)].reverse().find(message => message.role === "user");
    if (!userMessage) return;
    const userIndex = chatMessages.findIndex(message => message.id === userMessage.id);
    setChatMessages(current => current.slice(0, userIndex));
    await sendTurn(activeSession, userMessage.content, userMessage.attachments || [], userMessage.id);
  }, [activeSession, chatMessages, sendTurn]);

  const handleEditResend = useCallback(async (originalMessageId: string, content: string) => {
    if (!activeSession || !content.trim()) return;
    const index = chatMessages.findIndex(message => message.id === originalMessageId);
    if (index < 0) return;
    const attachments = chatMessages[index].attachments || [];
    setChatMessages(current => current.slice(0, index));
    await sendTurn(activeSession, content, attachments, originalMessageId);
  }, [activeSession, chatMessages, sendTurn]);

  const handleCopyMessage = useCallback((content: string) => {
    navigator.clipboard.writeText(content).catch(() => undefined);
  }, []);

  const renameChatSession = useCallback(async (sessionId: string, title: string) => {
    const cleanTitle = title.trim().replace(/\s+/g, " ");
    if (!cleanTitle) return;
    const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}`, {
      method: "PATCH",
      headers: await getHeaders(),
      body: JSON.stringify({ title: cleanTitle }),
    });
    if (!response.ok) throw new Error(`Rename failed with HTTP ${response.status}.`);
    upsertChatSession(await response.json() as ChatSession);
  }, [getHeaders, upsertChatSession]);

  const deleteChatSession = useCallback(async (sessionId: string) => {
    const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}`, { method: "DELETE", headers: await getHeaders() });
    if (!response.ok) throw new Error(`Delete failed with HTTP ${response.status}.`);
    setChatSessions(current => current.filter(session => session.id !== sessionId));
    if (activeSessionIdRef.current === sessionId) startNewChat();
  }, [getHeaders, startNewChat]);

  const handleTranscript = useCallback((transcript: string) => {
    setChatInput(current => current ? `${current} ${transcript}` : transcript);
  }, []);

  const transcribeVoiceAudio = useCallback(async (audioBlob: Blob) => {
    if (!accessToken) throw new Error("Please sign in again before using voice input.");
    const formData = new FormData();
    const extension = audioBlob.type.includes("ogg") ? "ogg" : audioBlob.type.includes("mp4") ? "m4a" : audioBlob.type.includes("wav") ? "wav" : "webm";
    formData.append("file", audioBlob, `voice-input.${extension}`);
    const response = await fetchWithAuth(`${API_BASE_URL}/voice/transcribe`, { method: "POST", body: formData }, getAccessToken, { timeoutMs: 120_000 });
    if (!response.ok) throw new Error(await uploadFailureFromResponse(response, `Voice transcription failed with HTTP ${response.status}.`));
    const data = await response.json() as { transcript?: string };
    return (data.transcript || "").trim();
  }, [accessToken, getAccessToken]);

  const { voiceState, toggleVoice: handleToggleVoice, interimTranscript: voiceInterimTranscript } = useSpeechRecognition(
    handleTranscript,
    { transcribeAudio: transcribeVoiceAudio },
  );

  const handleFileUpload = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.currentTarget.value = "";
    if (!accessToken || files.length === 0) return;
    const uploads = files.filter(file => file.size <= 15 * 1024 * 1024).map(file => ({ file, tempId: crypto.randomUUID() }));
    setAttachedFiles(current => [...current, ...uploads.map(({ file, tempId }) => ({ file, id: tempId, uploading: true }))]);
    await Promise.all(uploads.map(async ({ file, tempId }) => {
      const body = new FormData();
      body.append("file", file);
      try {
        const response = await fetchWithAuth(`${API_BASE_URL}/artifacts`, { method: "POST", body }, getAccessToken, { timeoutMs: 120_000 });
        if (!response.ok) throw new Error(await uploadFailureFromResponse(response, `Upload failed with HTTP ${response.status}.`));
        const artifact = await response.json() as ChatAttachment;
        setAttachedFiles(current => current.map(item => item.id === tempId ? { file, id: artifact.id, artifact, uploading: false } : item));
      } catch (error) {
        setAttachedFiles(current => current.map(item => item.id === tempId ? { ...item, uploading: false, error: errorMessage(error) } : item));
      }
    }));
  }, [accessToken, getAccessToken]);

  const handleRemoveFile = useCallback((id: string) => setAttachedFiles(current => current.filter(file => file.id !== id)), []);

  const handleOpenAttachment = useCallback(async (attachment: ChatAttachment) => {
    const response = await fetchWithAuth(`${API_BASE_URL}/artifacts/${attachment.id}/download`, {}, getAccessToken, { timeoutMs: 120_000 });
    if (!response.ok) throw new Error(await uploadFailureFromResponse(response, `Download failed with HTTP ${response.status}.`));
    const objectUrl = URL.createObjectURL(await response.blob());
    if (attachment.mime_type.startsWith("image/") || attachment.mime_type === "application/pdf" || attachment.mime_type.startsWith("text/")) {
      window.open(objectUrl, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
      return;
    }
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = attachment.filename || "download";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 5_000);
  }, [getAccessToken]);

  const selectSession = useCallback((session: ChatSession) => {
    isDraftChatRef.current = false;
    setIsDraftChat(false);
    setActiveSession(session);
    onOpenChat();
  }, [onOpenChat]);

  return {
    activeSession,
    attachedFiles,
    chatInput,
    chatMessages,
    chatSessions,
    deleteChatSession,
    fileInputRef,
    handleCopyMessage,
    handleEditResend,
    handleFileUpload,
    handleOpenAttachment,
    handleRemoveFile,
    handleRetryMessage,
    handleSendMessage,
    handleStopActiveChat,
    handleToggleVoice,
    isActiveChatSending,
    isMessagesLoading,
    isSessionsLoading,
    renameChatSession,
    selectSession,
    setChatInput,
    startNewChat,
    voiceInterimTranscript,
    voiceState,
  };
}
