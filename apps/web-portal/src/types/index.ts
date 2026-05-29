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

export type MessageStatus = "sending" | "pending" | "streaming" | "completed" | "failed" | "tool_running" | "tool_completed";

export interface ChatMessage {
  id: string;
  chat_session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  model_name?: string;
  metadata_json?: any;
  status?: MessageStatus;
  error_message?: string;
}

export interface SuggestedAction {
  label: string;
  prompt: string;
  icon: string;
}

export interface WorkflowCardData {
  id: string;
  title: string;
  description: string;
  category: "finance" | "hr" | "operations";
  inputs: Array<{
    name: string;
    label: string;
    type: "date" | "select" | "text";
    options?: string[];
    placeholder?: string;
  }>;
}

export interface AttachedFile {
  file: File;
  id?: string;
  uploading: boolean;
}

export type VoiceState = "idle" | "listening" | "processing" | "unsupported" | "denied";

export type ActiveTab = "workflows" | "chat" | "tasks" | "artifacts" | "connected-accounts" | "audit" | "settings";
