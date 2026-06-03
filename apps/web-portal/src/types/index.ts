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
  workflow_context?: string;
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
  status?: MessageStatus;
  error_message?: string;
}

export interface SuggestedAction {
  label: string;
  prompt: string;
  icon: string;
}

export interface AttachedFile {
  file: File;
  id?: string;
  uploading: boolean;
}

export type VoiceState = "idle" | "listening" | "processing" | "unsupported" | "denied";

export interface AIMemory {
  id: string;
  type: string;
  title: string;
  summary: string | null;
  body: string | null;
  scope_type: string | null;
  scope_value: string | null;
  confidence: string;
  risk_level: string;
  status: string;
  priority: number;
  conversation_id: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export type ActiveTab = "chat" | "tasks" | "artifacts" | "connected-accounts" | "audit" | "settings" | "admin";
