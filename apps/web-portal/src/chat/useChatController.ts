import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import type { AttachedFile, ChatAttachment, ChatMessage, ChatSession } from "../types";
import { API_BASE_URL, fetchWithTimeout } from "../hooks/useApi";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import {
  appendActivityEvent,
  appendMessagePartEvent,
  chatFailureFromDetail,
  chatFailureFromNetwork,
  chatFailureFromResponse,
  CHAT_STREAM_COMPLETION_POLL_INTERVAL_MS,
  CHAT_STREAM_COMPLETION_POLL_TIMEOUT_MS,
  CHAT_STREAM_INACTIVITY_TIMEOUT_MS,
  type ChatFailurePayload,
  mergeStreamMetadata,
  mergeFetchedChatSessions,
  messageRequestId,
  normalizeChatMessage,
  parseSseChunk,
  patchChatSession,
  pendingProgressMetadata,
  readCachedChatSessions,
  sortChatSessions,
  uploadFailureFromResponse,
  writeCachedChatSessions,
} from "./runtime";

interface UseChatControllerOptions {
  accessToken: string | null;
  activeUserEmail: string;
  onOpenChat: () => void;
}

function errorMessage(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function mergeChatMessages(persistedMessages: ChatMessage[], localMessages: ChatMessage[]) {
  const normalizedPersisted = persistedMessages.map(normalizeChatMessage);
  if (localMessages.length === 0) return normalizedPersisted;

  const persistedIds = new Set(normalizedPersisted.map(message => message.id));
  const persistedRequestRoles = new Set(
    normalizedPersisted
      .map(message => {
        const requestId = messageRequestId(message);
        return requestId ? `${message.role}:${requestId}` : null;
      })
      .filter((value): value is string => Boolean(value))
  );

  const localOnly = localMessages.filter(message => {
    if (persistedIds.has(message.id)) return false;
    const requestId = messageRequestId(message);
    return !requestId || !persistedRequestRoles.has(`${message.role}:${requestId}`);
  });

  return [...normalizedPersisted, ...localOnly];
}

function removeRequestMessages(messages: ChatMessage[], requestId: string) {
  return messages.filter(message => messageRequestId(message) !== requestId);
}

function replaceOrAppendMessage(messages: ChatMessage[], messageId: string, replacement: ChatMessage) {
  const index = messages.findIndex(message => message.id === messageId);
  if (index === -1) return [...messages, replacement];
  return messages.map(message => message.id === messageId ? replacement : message);
}

function wait(ms: number) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function hasPersistedAssistantMessage(messages: ChatMessage[], requestId: string) {
  return messages.some(message => message.role === "assistant" && messageRequestId(message) === requestId);
}

function isAbortError(err: unknown) {
  return err instanceof DOMException
    ? err.name === "AbortError"
    : err instanceof Error && err.name === "AbortError";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function useChatController({ accessToken, activeUserEmail, onOpenChat }: UseChatControllerOptions) {
  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [isSessionsLoading, setIsSessionsLoading] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isMessagesLoading, setIsMessagesLoading] = useState(false);
  const [isDraftChat, setIsDraftChat] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [sendingSessionIds, setSendingSessionIds] = useState<string[]>([]);
  const [localMessagesBySession, setLocalMessagesBySession] = useState<Record<string, ChatMessage[]>>({});
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const activeSessionId = activeSession?.id ?? null;
  const activeSessionIdRef = useRef<string | null>(activeSessionId);
  const isDraftChatRef = useRef(isDraftChat);
  const localMessagesBySessionRef = useRef<Record<string, ChatMessage[]>>({});
  const streamControllersRef = useRef<Record<string, { controller: AbortController; requestId: string }>>({});
  const stoppedRequestIdsRef = useRef<Set<string>>(new Set());
  const messageLoadSeqRef = useRef(0);

  const handleTranscript = useCallback((transcript: string) => {
    setChatInput(prev => (prev ? prev + " " + transcript : transcript));
  }, []);

  const transcribeVoiceAudio = useCallback(async (audioBlob: Blob) => {
    if (!accessToken) throw new Error("Please sign in again before using voice input.");
    const formData = new FormData();
    const extension = audioBlob.type.includes("ogg")
      ? "ogg"
      : audioBlob.type.includes("mp4")
        ? "m4a"
        : audioBlob.type.includes("wav")
          ? "wav"
          : "webm";
    formData.append("file", audioBlob, `voice-input.${extension}`);
    const res = await fetch(`${API_BASE_URL}/voice/transcribe`, {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
      body: formData,
    });
    if (!res.ok) {
      let message = `Voice transcription failed with HTTP ${res.status}.`;
      try {
        const detail = await res.json();
        const payload = detail?.detail || detail;
        message = payload?.error_message || payload?.message || message;
      } catch {
        // Keep the HTTP status message if the response is not JSON.
      }
      throw new Error(message);
    }
    const data = await res.json() as { transcript?: string };
    return (data.transcript || "").trim();
  }, [accessToken]);

  const {
    voiceState,
    toggleVoice: handleToggleVoice,
    interimTranscript: voiceInterimTranscript,
  } = useSpeechRecognition(handleTranscript, { transcribeAudio: transcribeVoiceAudio });

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    isDraftChatRef.current = isDraftChat;
  }, [isDraftChat]);

  useEffect(() => {
    localMessagesBySessionRef.current = localMessagesBySession;
  }, [localMessagesBySession]);

  const isActiveChatSending = activeSessionId ? sendingSessionIds.includes(activeSessionId) : false;

  const getHeaders = useCallback(() => ({
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  }), [accessToken]);

  const updateLocalChatSession = useCallback((sessionId: string, patch: Partial<ChatSession>) => {
    setChatSessions(prev => {
      let changed = false;
      const next = prev.map(session => {
        if (session.id !== sessionId) return session;
        const updated = patchChatSession(session, patch);
        if (updated !== session) changed = true;
        return updated;
      });

      if (!changed) return prev;
      return patch.last_message_at ? sortChatSessions(next) : next;
    });

    setActiveSession(prev => {
      if (!prev || prev.id !== sessionId) return prev;
      return patchChatSession(prev, patch);
    });
  }, []);

  const upsertChatSession = useCallback((session: ChatSession) => {
    setChatSessions(prev => {
      const exists = prev.some(item => item.id === session.id);
      const next = exists
        ? prev.map(item => item.id === session.id ? session : item)
        : [session, ...prev];
      const sorted = sortChatSessions(next);
      writeCachedChatSessions(activeUserEmail, sorted);
      return sorted;
    });

    setActiveSession(prev => prev?.id === session.id ? session : prev);
  }, [activeUserEmail]);

  const fetchChatSessions = useCallback(async () => {
    if (!accessToken || !activeUserEmail) return;
    setIsSessionsLoading(true);
    try {
      const res = await fetchWithTimeout(`${API_BASE_URL}/chat/sessions`, { headers: getHeaders() });
      if (res.ok) {
        const data = sortChatSessions(await res.json() as ChatSession[]);
        setChatSessions(prev => {
          const merged = mergeFetchedChatSessions(data, prev, activeSessionIdRef.current);
          writeCachedChatSessions(activeUserEmail, merged);
          return merged;
        });
        setActiveSession(prev => {
          if (isDraftChatRef.current) return null;
          if (prev) {
            const updatedActive = data.find(session => session.id === prev.id);
            if (updatedActive) return updatedActive;
            return prev;
          }
          return data.length > 0 ? data[0] : null;
        });
      } else {
        console.error("Failed to fetch sessions:", res.status, await res.text().catch(() => ""));
      }
    } catch (err) {
      console.error("Failed to fetch chat sessions:", err);
    } finally {
      setIsSessionsLoading(false);
    }
  }, [accessToken, activeUserEmail, getHeaders]);

  const refreshChatSession = useCallback(async (sessionId: string) => {
    if (!accessToken) return;
    try {
      const res = await fetchWithTimeout(`${API_BASE_URL}/chat/sessions/${sessionId}`, { headers: getHeaders() });
      if (res.ok) {
        upsertChatSession(await res.json() as ChatSession);
      }
    } catch (err) {
      console.error("Failed to refresh chat session:", err);
    }
  }, [accessToken, getHeaders, upsertChatSession]);

  const touchChatSessionForMessage = useCallback((session: ChatSession) => {
    updateLocalChatSession(session.id, {
      last_message_at: new Date().toISOString(),
    });
  }, [updateLocalChatSession]);

  const renameChatSession = useCallback(async (sessionId: string, title: string) => {
    const cleanTitle = title.trim().replace(/\s+/g, " ");
    if (!cleanTitle) return;

    const previous = chatSessions.find(session => session.id === sessionId);
    updateLocalChatSession(sessionId, { title: cleanTitle });

    try {
      const res = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}`, {
        method: "PATCH",
        headers: getHeaders(),
        body: JSON.stringify({ title: cleanTitle }),
      });
      if (!res.ok) throw new Error(`Rename failed with HTTP ${res.status}`);
      upsertChatSession(await res.json() as ChatSession);
    } catch (err) {
      console.error("Rename session failed:", err);
      if (previous) updateLocalChatSession(sessionId, { title: previous.title });
      alert("Failed to rename chat. Please try again.");
    }
  }, [chatSessions, getHeaders, updateLocalChatSession, upsertChatSession]);

  const addLocalMessages = useCallback((sessionId: string, messages: ChatMessage[]) => {
    setLocalMessagesBySession(prev => ({
      ...prev,
      [sessionId]: [...(prev[sessionId] || []), ...messages],
    }));
  }, []);

  const upsertLocalMessage = useCallback((sessionId: string, message: ChatMessage) => {
    setLocalMessagesBySession(prev => {
      const current = prev[sessionId] || [];
      return {
        ...prev,
        [sessionId]: replaceOrAppendMessage(current, message.id, message),
      };
    });
  }, []);

  const updateLocalMessage = useCallback((sessionId: string, messageId: string, patch: Partial<ChatMessage>) => {
    setLocalMessagesBySession(prev => {
      const current = prev[sessionId] || [];
      if (current.length === 0) return prev;
      return {
        ...prev,
        [sessionId]: current.map(message => message.id === messageId ? { ...message, ...patch } : message),
      };
    });
  }, []);

  const clearLocalRequestMessages = useCallback((sessionId: string, requestId: string) => {
    setLocalMessagesBySession(prev => {
      const current = prev[sessionId] || [];
      if (current.length === 0) return prev;
      const nextMessages = removeRequestMessages(current, requestId);
      if (nextMessages.length === current.length) return prev;

      const next = { ...prev };
      if (nextMessages.length > 0) next[sessionId] = nextMessages;
      else delete next[sessionId];
      return next;
    });
  }, []);

  const removeLocalMessage = useCallback((sessionId: string, messageId: string) => {
    setLocalMessagesBySession(prev => {
      const current = prev[sessionId] || [];
      if (current.length === 0) return prev;
      const nextMessages = current.filter(message => message.id !== messageId);
      if (nextMessages.length === current.length) return prev;

      const next = { ...prev };
      if (nextMessages.length > 0) next[sessionId] = nextMessages;
      else delete next[sessionId];
      return next;
    });
    if (activeSessionIdRef.current === sessionId) {
      setChatMessages(prev => prev.filter(message => message.id !== messageId));
    }
  }, []);

  const markSessionSending = useCallback((sessionId: string) => {
    setSendingSessionIds(prev => prev.includes(sessionId) ? prev : [...prev, sessionId]);
  }, []);

  const unmarkSessionSending = useCallback((sessionId: string) => {
    setSendingSessionIds(prev => prev.filter(id => id !== sessionId));
  }, []);

  const startNewChat = useCallback(() => {
    messageLoadSeqRef.current += 1;
    isDraftChatRef.current = true;
    setIsDraftChat(true);
    setActiveSession(null);
    setChatMessages([]);
    setIsMessagesLoading(false);
    setChatInput("");
    setAttachedFiles([]);
    onOpenChat();
  }, [onOpenChat]);

  const createPersistedChatSession = useCallback(async (): Promise<ChatSession | null> => {
    if (!accessToken) return null;
    try {
      const res = await fetch(`${API_BASE_URL}/chat/sessions`, {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({ title: "New Chat" }),
      });
      if (res.ok) {
        const newSession = await res.json() as ChatSession;
        isDraftChatRef.current = false;
        setIsDraftChat(false);
        upsertChatSession(newSession);
        setActiveSession(newSession);
        onOpenChat();
        return newSession;
      } else {
        const errBody = await res.text().catch(() => "");
        console.error("Failed to create chat:", res.status, errBody);
        alert(`Failed to create new chat (HTTP ${res.status}). The API may be unavailable.`);
      }
    } catch (err) {
      console.error("Failed to create new chat:", err);
      alert("Failed to create new chat. Please check your connection.");
    }
    return null;
  }, [accessToken, getHeaders, onOpenChat, upsertChatSession]);

  const fetchSessionMessages = useCallback(async (sid: string, showLoading = true): Promise<ChatMessage[] | null> => {
    const loadSeq = showLoading ? messageLoadSeqRef.current + 1 : messageLoadSeqRef.current;
    if (showLoading) {
      messageLoadSeqRef.current = loadSeq;
      setChatMessages([]);
      setIsMessagesLoading(true);
    }
    try {
      const res = await fetchWithTimeout(`${API_BASE_URL}/chat/sessions/${sid}/messages`, { headers: getHeaders() });
      if (res.ok) {
        const data = (await res.json() as ChatMessage[]).map(normalizeChatMessage);
        if (activeSessionIdRef.current === sid) {
          setChatMessages(mergeChatMessages(data, localMessagesBySessionRef.current[sid] || []));
        }
        return data;
      }
    } catch (err) {
      console.error("Failed to fetch messages:", err);
    } finally {
      if (showLoading && messageLoadSeqRef.current === loadSeq) {
        setIsMessagesLoading(false);
      }
    }
    return null;
  }, [getHeaders]);

  const deleteChatSession = useCallback(async (sid: string) => {
    try {
      await fetch(`${API_BASE_URL}/chat/sessions/${sid}`, { method: "DELETE", headers: getHeaders() });
      setChatSessions(prev => prev.filter(s => s.id !== sid));
      if (activeSession?.id === sid) setActiveSession(null);
      void fetchChatSessions();
    } catch (err) {
      console.error("Delete session failed:", err);
    }
  }, [activeSession?.id, fetchChatSessions, getHeaders]);

  useEffect(() => {
    const timerId = window.setTimeout(() => {
      if (!activeUserEmail) {
        setChatSessions([]);
        setActiveSession(null);
        setChatMessages([]);
        return;
      }

      const cached = readCachedChatSessions(activeUserEmail);
      isDraftChatRef.current = false;
      setIsDraftChat(false);
      setChatSessions(cached);
      setActiveSession(cached[0] || null);
      setChatMessages([]);
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [activeUserEmail]);

  useEffect(() => {
    if (!accessToken || !activeUserEmail) return;
    const timerId = window.setTimeout(() => {
      void fetchChatSessions();
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [accessToken, activeUserEmail, fetchChatSessions]);

  useEffect(() => {
    const timerId = window.setTimeout(() => {
      if (activeSessionId && accessToken) {
        void fetchSessionMessages(activeSessionId);
      } else {
        messageLoadSeqRef.current += 1;
        setChatMessages([]);
        setIsMessagesLoading(false);
      }
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [activeSessionId, accessToken, fetchSessionMessages]);

  const markAssistantFailed = useCallback((sessionId: string, pendingMessageId: string, failure: ChatFailurePayload) => {
    const patch = { status: "failed" as const, error_message: JSON.stringify(failure) };
    updateLocalMessage(sessionId, pendingMessageId, patch);
    if (activeSessionIdRef.current === sessionId) {
      setChatMessages(prev => prev.map(m =>
        m.id === pendingMessageId ? { ...m, ...patch } : m
      ));
    }
  }, [updateLocalMessage]);

  const waitForPersistedAssistantMessage = useCallback(async (sessionId: string, requestId: string) => {
    const deadline = Date.now() + CHAT_STREAM_COMPLETION_POLL_TIMEOUT_MS;
    while (Date.now() <= deadline) {
      const messages = await fetchSessionMessages(sessionId, false);
      if (messages && hasPersistedAssistantMessage(messages, requestId)) {
        clearLocalRequestMessages(sessionId, requestId);
        return true;
      }
      await wait(CHAT_STREAM_COMPLETION_POLL_INTERVAL_MS);
    }
    return false;
  }, [clearLocalRequestMessages, fetchSessionMessages]);

  const postChatMessage = useCallback(async (
    session: ChatSession,
    content: string,
    artifactIds: string[],
    pendingMessageId: string,
    requestId: string,
  ) => {
    const abortController = new AbortController();
    streamControllersRef.current[session.id] = { controller: abortController, requestId };
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let pendingStreamMessage: ChatMessage | null = null;
    const resetStreamTimeout = () => {
      if (timeoutId) clearTimeout(timeoutId);
      timeoutId = setTimeout(() => abortController.abort(), CHAT_STREAM_INACTIVITY_TIMEOUT_MS);
    };
    const clearStreamTimeout = () => {
      if (!timeoutId) return;
      clearTimeout(timeoutId);
      timeoutId = null;
    };
    resetStreamTimeout();
    markSessionSending(session.id);

    try {
      const res = await fetch(`${API_BASE_URL}/chat/sessions/${session.id}/messages/stream`, {
        method: "POST",
        headers: { ...getHeaders(), "X-Request-ID": requestId },
        body: JSON.stringify({
          content,
          artifact_ids: artifactIds,
        }),
        signal: abortController.signal,
      });

      if (res.ok) {
        if (!res.body) {
          throw new Error("Streaming response did not include a body");
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let finalMessage: ChatMessage | null = null;
        let streamFailure: ChatFailurePayload | null = null;
        const createPendingStreamMessage = (): ChatMessage => ({
          id: pendingMessageId,
          chat_session_id: session.id,
          role: "assistant",
          content: "",
          created_at: new Date().toISOString(),
          status: "pending",
        });
        const updatePendingMessage = (updater: (message: ChatMessage) => ChatMessage) => {
          const localMessage = pendingStreamMessage
            || (localMessagesBySessionRef.current[session.id] || []).find(m => m.id === pendingMessageId)
            || createPendingStreamMessage();
          const updatedMessage = updater(localMessage);
          pendingStreamMessage = updatedMessage;
          upsertLocalMessage(session.id, updatedMessage);
          if (activeSessionIdRef.current === session.id) {
            setChatMessages(prev => replaceOrAppendMessage(prev, pendingMessageId, updatedMessage));
          }
        };

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          resetStreamTimeout();
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseSseChunk(buffer);
          buffer = parsed.rest;

          for (const item of parsed.events) {
            if (item.event === "activity") {
              updatePendingMessage(message => appendActivityEvent(message, item.data));
            } else if (["reasoning.delta", "reasoning.available", "thinking.delta", "tool.start", "tool.complete", "message.delta"].includes(item.event)) {
              updatePendingMessage(message => appendMessagePartEvent(message, item.data));
            } else if (item.event === "message" || item.event === "message.complete") {
              const pendingMessage = pendingStreamMessage
                || (localMessagesBySessionRef.current[session.id] || []).find(m => m.id === pendingMessageId)
                || null;
              finalMessage = mergeStreamMetadata(normalizeChatMessage(item.data as ChatMessage), pendingMessage);
              finalMessage.status = "completed";
            } else if (item.event === "session.title" && isRecord(item.data)) {
              const title = typeof item.data.title === "string" ? item.data.title.trim() : "";
              const titleSessionId = typeof item.data.session_id === "string" ? item.data.session_id : session.id;
              if (title && titleSessionId === session.id) {
                updateLocalChatSession(session.id, { title });
              }
            } else if (item.event === "error") {
              streamFailure = chatFailureFromDetail(item.data, requestId, 502);
            }
          }
        }

        if (streamFailure) {
          if (await waitForPersistedAssistantMessage(session.id, requestId)) return;
          markAssistantFailed(session.id, pendingMessageId, streamFailure);
        } else if (finalMessage) {
          clearLocalRequestMessages(session.id, requestId);
          if (activeSessionIdRef.current === session.id) {
            setChatMessages(prev => replaceOrAppendMessage(prev, pendingMessageId, finalMessage));
          }
        } else {
          if (await waitForPersistedAssistantMessage(session.id, requestId)) return;
          markAssistantFailed(session.id, pendingMessageId, {
            requestId,
            errorType: "stream_error",
            errorMessage: "The AI service finished without returning a response.",
            httpStatus: 0,
          });
        }
      } else {
        markAssistantFailed(session.id, pendingMessageId, await chatFailureFromResponse(res, requestId));
      }
    } catch (err: unknown) {
      if (isAbortError(err) && stoppedRequestIdsRef.current.delete(requestId)) {
        const stoppedPendingMessage = (localMessagesBySessionRef.current[session.id] || [])
          .find(message => message.id === pendingMessageId) || null;
        if (stoppedPendingMessage?.content.trim()) {
          const metadata = isRecord(stoppedPendingMessage.metadata_json) ? stoppedPendingMessage.metadata_json : {};
          const stoppedMessage: ChatMessage = {
            ...stoppedPendingMessage,
            status: "completed",
            metadata_json: {
              ...metadata,
              stopped: true,
            },
          };
          upsertLocalMessage(session.id, stoppedMessage);
          if (activeSessionIdRef.current === session.id) {
            setChatMessages(prev => replaceOrAppendMessage(prev, pendingMessageId, stoppedMessage));
          }
        } else {
          removeLocalMessage(session.id, pendingMessageId);
        }
        return;
      }
      if (await waitForPersistedAssistantMessage(session.id, requestId)) return;
      markAssistantFailed(session.id, pendingMessageId, chatFailureFromNetwork(err, requestId));
    } finally {
      clearStreamTimeout();
      const activeController = streamControllersRef.current[session.id];
      if (activeController?.requestId === requestId) {
        delete streamControllersRef.current[session.id];
      }
      unmarkSessionSending(session.id);
      void refreshChatSession(session.id);
    }
  }, [
    clearLocalRequestMessages,
    getHeaders,
    markAssistantFailed,
    markSessionSending,
    removeLocalMessage,
    refreshChatSession,
    unmarkSessionSending,
    updateLocalChatSession,
    upsertLocalMessage,
    waitForPersistedAssistantMessage,
  ]);

  const handleStopActiveChat = useCallback(() => {
    if (!activeSessionId) return;
    const activeStream = streamControllersRef.current[activeSessionId];
    if (!activeStream) return;
    stoppedRequestIdsRef.current.add(activeStream.requestId);
    activeStream.controller.abort();
  }, [activeSessionId]);

  const handleSendMessage = useCallback(async (e: FormEvent) => {
    e.preventDefault();
    if ((!chatInput.trim() && attachedFiles.length === 0) || !accessToken) return;
    if (activeSessionId && sendingSessionIds.includes(activeSessionId)) return;
    if (attachedFiles.some(file => file.uploading || file.error)) return;

    const content = chatInput;
    const attachedArtifacts: ChatAttachment[] = attachedFiles
      .filter(file => !file.uploading && !file.error && file.id)
      .map(file => file.artifact || {
        id: file.id as string,
        filename: file.file.name,
        mime_type: file.file.type || "application/octet-stream",
      });
    const artifactIds = attachedArtifacts.map(artifact => artifact.id);

    const currentSession = activeSession || await createPersistedChatSession();
    if (!currentSession) return;
    setChatInput("");
    setAttachedFiles([]);
    touchChatSessionForMessage(currentSession);

    const requestId = crypto.randomUUID();
    const pendingMsgId = crypto.randomUUID();
    const createdAt = new Date().toISOString();
    const localTurn: ChatMessage[] = [
      {
        id: crypto.randomUUID(),
        chat_session_id: currentSession.id,
        role: "user",
        content,
        created_at: createdAt,
        status: "completed",
        metadata_json: { request_id: requestId, attachments: attachedArtifacts },
        attachments: attachedArtifacts,
      },
      {
        id: pendingMsgId,
        chat_session_id: currentSession.id,
        role: "assistant",
        content: "",
        created_at: createdAt,
        status: "pending",
        metadata_json: pendingProgressMetadata(requestId, content, artifactIds.length, createdAt),
      },
    ];
    addLocalMessages(currentSession.id, localTurn);
    setChatMessages(prev => [...prev, ...localTurn]);

    await postChatMessage(currentSession, content, artifactIds, pendingMsgId, requestId);
  }, [
    accessToken,
    activeSession,
    activeSessionId,
    addLocalMessages,
    attachedFiles,
    chatInput,
    createPersistedChatSession,
    postChatMessage,
    sendingSessionIds,
    touchChatSessionForMessage,
  ]);

  const handleRetryMessage = useCallback(async (messageId: string) => {
    if (!chatMessages.find(m => m.id === messageId) || !activeSession) return;

    const failedIdx = chatMessages.findIndex(m => m.id === messageId);
    const userMessage = [...chatMessages.slice(0, failedIdx)].reverse()
      .find(m => m.role === "user" && m.status === "completed");
    if (!userMessage) return;

    const requestId = crypto.randomUUID();
    const pendingMsgId = crypto.randomUUID();
    const createdAt = new Date().toISOString();
    const pendingMessage: ChatMessage = {
      id: pendingMsgId,
      chat_session_id: activeSession.id,
      role: "assistant",
      content: "",
      created_at: createdAt,
      status: "pending",
      metadata_json: pendingProgressMetadata(requestId, userMessage.content, 0, createdAt),
    };
    addLocalMessages(activeSession.id, [pendingMessage]);
    setChatMessages(prev => [
      ...prev.filter(m => m.id !== messageId),
      pendingMessage,
    ]);

    await postChatMessage(activeSession, userMessage.content, [], pendingMsgId, requestId);
  }, [activeSession, addLocalMessages, chatMessages, postChatMessage]);

  const handleCopyMessage = useCallback((content: string) => {
    navigator.clipboard.writeText(content).catch(() => {});
  }, []);

  const handleEditResend = useCallback(async (originalMessageId: string, newContent: string) => {
    if (!activeSession || !newContent.trim()) return;

    const editIndex = chatMessages.findIndex(m => m.id === originalMessageId);
    if (editIndex === -1) return;

    const requestId = crypto.randomUUID();
    const pendingMsgId = crypto.randomUUID();
    const createdAt = new Date().toISOString();
    const updatedUserMsg: ChatMessage = {
      ...chatMessages[editIndex],
      content: newContent,
    };
    const pendingMessage: ChatMessage = {
      id: pendingMsgId,
      chat_session_id: activeSession.id,
      role: "assistant",
      content: "",
      created_at: createdAt,
      status: "pending",
      metadata_json: pendingProgressMetadata(requestId, newContent, 0, createdAt),
    };
    addLocalMessages(activeSession.id, [pendingMessage]);

    setChatMessages(prev => {
      const idx = prev.findIndex(m => m.id === originalMessageId);
      if (idx === -1) return prev;
      return [
        ...prev.slice(0, idx),
        updatedUserMsg,
        pendingMessage,
      ];
    });

    await postChatMessage(activeSession, newContent, [], pendingMsgId, requestId);
  }, [activeSession, addLocalMessages, chatMessages, postChatMessage]);

  const handleFileUpload = useCallback(async (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.currentTarget.value = "";
    if (files.length === 0) return;
    if (!accessToken) {
      alert("Please sign in again before uploading files.");
      return;
    }
    const validFiles = files.filter(file => {
      const isValid = file.size <= 15 * 1024 * 1024;
      if (!isValid) alert(`File ${file.name} exceeds 15MB limit.`);
      return isValid;
    });
    const uploads = validFiles.map(file => ({
      file,
      tempId: crypto.randomUUID(),
    }));
    if (uploads.length === 0) return;

    setAttachedFiles(prev => [
      ...prev,
      ...uploads.map(({ file, tempId }) => ({ file, id: tempId, uploading: true })),
    ]);

    await Promise.all(uploads.map(async ({ file, tempId }) => {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const response = await fetch(`${API_BASE_URL}/artifacts`, {
          method: "POST",
          headers: { Authorization: `Bearer ${accessToken}` },
          body: formData,
        });
        if (response.ok) {
          const art = await response.json() as ChatAttachment;
          const attachment: ChatAttachment = {
            id: art.id,
            filename: art.filename || file.name,
            mime_type: art.mime_type || file.type || "application/octet-stream",
          };
          setAttachedFiles(prev => prev.map(f => f.id === tempId ? {
            file,
            id: attachment.id,
            artifact: attachment,
            uploading: false,
          } : f));
        } else {
          const error = await uploadFailureFromResponse(response, `Upload failed with HTTP ${response.status}.`);
          setAttachedFiles(prev => prev.map(f => f.id === tempId ? { ...f, uploading: false, error } : f));
        }
      } catch (err) {
        setAttachedFiles(prev => prev.map(f => f.id === tempId ? {
          ...f,
          uploading: false,
          error: `Upload failed: ${errorMessage(err)}`,
        } : f));
      }
    }));
  }, [accessToken]);

  const handleRemoveFile = useCallback((id: string) => {
    setAttachedFiles(prev => prev.filter(f => f.id !== id));
  }, []);

  const handleOpenAttachment = useCallback(async (attachment: ChatAttachment) => {
    if (!accessToken) return;
    try {
      const response = await fetch(`${API_BASE_URL}/artifacts/${attachment.id}/download`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!response.ok) {
        throw new Error(await uploadFailureFromResponse(response, `Download failed with HTTP ${response.status}.`));
      }

      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const shouldPreview = attachment.mime_type.startsWith("image/")
        || attachment.mime_type === "application/pdf"
        || attachment.mime_type.startsWith("text/");

      if (shouldPreview) {
        window.open(objectUrl, "_blank", "noopener,noreferrer");
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
        return;
      }

      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = attachment.filename || "download";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 5_000);
    } catch (err) {
      console.error("Open attachment failed:", err);
      alert(errorMessage(err));
    }
  }, [accessToken]);

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
