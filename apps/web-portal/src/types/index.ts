export interface UserProfile {
  id?: string;
  email: string;
  displayName: string;
  roles: string[];
}

export interface ChatSession {
  id: string;
  title: string;
  status: string;
  created_at: string;
  last_message_at: string;
}

type MessageStatus = "sending" | "pending" | "streaming" | "completed" | "failed" | "tool_running" | "tool_completed";

export interface ChatMessage {
  id: string;
  chat_session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  model_name?: string;
  metadata_json?: unknown;
  attachments?: ChatAttachment[];
  status?: MessageStatus;
  error_message?: string;
}

export interface ChatAttachment {
  id: string;
  filename: string;
  mime_type: string;
  artifact_type: string;
}

export interface AttachedFile {
  file: File;
  id?: string;
  artifact?: ChatAttachment;
  uploading: boolean;
  error?: string;
}

export type VoiceState = "idle" | "listening" | "processing" | "unsupported" | "denied";

export type ActiveTab = "chat" | "connected-accounts";
