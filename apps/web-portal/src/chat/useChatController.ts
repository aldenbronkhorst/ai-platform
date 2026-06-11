import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import type { AttachedFile, ChatAttachment, ChatMessage, ChatSession } from "../types";
import { API_BASE_URL, fetchWithTimeout } from "../hooks/useApi";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import {
  appendActivityEvent,
  chatFailureFromDetail,
  chatFailureFromNetwork,
  chatFailureFromResponse,
  CHAT_REQUEST_TIMEOUT_MS,
  type ChatFailurePayload,
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

export function useChatController({ accessToken, activeUserEmail, onOpenChat }: UseChatControllerOptions) {
  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [isSessionsLoading, setIsSessionsLoading] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isMessagesLoading, setIsMessagesLoading] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [sendingSessionIds, setSendingSessionIds] = useState<string[]>([]);
  const [localMessagesBySession, setLocalMessagesBySession] = useState<Record<string, ChatMessage[]>>({});
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const activeSessionId = activeSession?.id ?? null;
  const activeSessionIdRef = useRef<string | null>(activeSessionId);
  const localMessagesBySessionRef = useRef<Record<string, ChatMessage[]>>({});

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

  const markSessionSending = useCallback((sessionId: string) => {
    setSendingSessionIds(prev => prev.includes(sessionId) ? prev : [...prev, sessionId]);
  }, []);

  const unmarkSessionSending = useCallback((sessionId: string) => {
    setSendingSessionIds(prev => prev.filter(id => id !== sessionId));
  }, []);

  const createNewChat = useCallback(async (): Promise<ChatSession | null> => {
    if (!accessToken) return null;
    try {
      const res = await fetch(`${API_BASE_URL}/chat/sessions`, {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({ title: "New Chat" }),
      });
      if (res.ok) {
        const newSession = await res.json() as ChatSession;
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

  const fetchSessionMessages = useCallback(async (sid: string, showLoading = true) => {
    if (showLoading) setIsMessagesLoading(true);
    try {
      const res = await fetchWithTimeout(`${API_BASE_URL}/chat/sessions/${sid}/messages`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json() as ChatMessage[];
        if (activeSessionIdRef.current === sid) {
          setChatMessages(mergeChatMessages(data, localMessagesBySessionRef.current[sid] || []));
        }
      }
    } catch (err) {
      console.error("Failed to fetch messages:", err);
    } finally {
      if (showLoading && activeSessionIdRef.current === sid) {
        setIsMessagesLoading(false);
      }
    }
  }, [getHeaders]);

  const deleteChatSession = useCallback(async (sid: string) => {
    if (!confirm("Archive/delete this chat session?")) return;
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

  const postChatMessage = useCallback(async (
    session: ChatSession,
    content: string,
    artifactIds: string[],
    pendingMessageId: string,
    requestId: string,
  ) => {
    const abortController = new AbortController();
    const timeoutId = setTimeout(() => abortController.abort(), CHAT_REQUEST_TIMEOUT_MS);
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

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseSseChunk(buffer);
          buffer = parsed.rest;

          for (const item of parsed.events) {
            if (item.event === "activity") {
              updateLocalMessage(session.id, pendingMessageId, appendActivityEvent(
                (localMessagesBySessionRef.current[session.id] || []).find(m => m.id === pendingMessageId) || {
                  id: pendingMessageId,
                  chat_session_id: session.id,
                  role: "assistant",
                  content: "",
                  created_at: new Date().toISOString(),
                  status: "pending",
                },
                item.data,
              ));
              if (activeSessionIdRef.current === session.id) {
                setChatMessages(prev => prev.map(m => m.id === pendingMessageId ? appendActivityEvent(m, item.data) : m));
              }
            } else if (item.event === "message") {
              finalMessage = normalizeChatMessage(item.data as ChatMessage);
              finalMessage.status = "completed";
            } else if (item.event === "error") {
              streamFailure = chatFailureFromDetail(item.data, requestId, 502);
            }
          }
        }

        if (streamFailure) {
          markAssistantFailed(session.id, pendingMessageId, streamFailure);
        } else if (finalMessage) {
          clearLocalRequestMessages(session.id, requestId);
          if (activeSessionIdRef.current === session.id) {
            setChatMessages(prev => prev.map(m => m.id === pendingMessageId ? finalMessage : m));
          }
        } else {
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
      markAssistantFailed(session.id, pendingMessageId, chatFailureFromNetwork(err, requestId));
    } finally {
      clearTimeout(timeoutId);
      unmarkSessionSending(session.id);
      void refreshChatSession(session.id);
      if (activeSessionIdRef.current === session.id) {
        window.setTimeout(() => {
          if (activeSessionIdRef.current === session.id) void fetchSessionMessages(session.id, false);
        }, 750);
        window.setTimeout(() => {
          if (activeSessionIdRef.current === session.id) void fetchSessionMessages(session.id, false);
        }, 15_000);
      }
    }
  }, [
    clearLocalRequestMessages,
    fetchSessionMessages,
    getHeaders,
    markAssistantFailed,
    markSessionSending,
    refreshChatSession,
    unmarkSessionSending,
    updateLocalMessage,
  ]);

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

    const currentSession = activeSession || await createNewChat();
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
    createNewChat,
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
    for (const file of files) {
      if (file.size > 15 * 1024 * 1024) {
        alert(`File ${file.name} exceeds 15MB limit.`);
        continue;
      }
      const tempId = crypto.randomUUID();
      setAttachedFiles(prev => [...prev, { file, id: tempId, uploading: true }]);
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
    }
  }, [accessToken]);

  const handleRemoveFile = useCallback((id: string) => {
    setAttachedFiles(prev => prev.filter(f => f.id !== id));
  }, []);

  const selectSession = useCallback((session: ChatSession) => {
    setActiveSession(session);
    onOpenChat();
  }, [onOpenChat]);

  return {
    activeSession,
    attachedFiles,
    chatInput,
    chatMessages,
    chatSessions,
    createNewChat,
    deleteChatSession,
    fileInputRef,
    handleCopyMessage,
    handleEditResend,
    handleFileUpload,
    handleRemoveFile,
    handleRetryMessage,
    handleSendMessage,
    handleToggleVoice,
    isActiveChatSending,
    isMessagesLoading,
    isSessionsLoading,
    renameChatSession,
    selectSession,
    setChatInput,
    voiceInterimTranscript,
    voiceState,
  };
}
