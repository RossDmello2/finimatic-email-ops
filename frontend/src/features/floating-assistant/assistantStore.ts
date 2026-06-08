import { AgentModel, PendingAction } from "./assistantApi";

export const STORAGE = {
  conversations: "va_conversations",
  currentId: "va_current_id",
  ui: "va_ui",
  model: "va_model"
};

export const SESSION = {
  draft: "va_draft"
};

export type AssistantRole = "user" | "assistant";

export type AssistantAttachment = {
  id: string;
  name: string;
  size: number;
  mimeType: string;
  kind: "image" | "pdf" | "text";
  file?: File;
};

export type AssistantMessage = {
  id: string;
  role: AssistantRole;
  content: string;
  ts: number;
  attachments?: Omit<AssistantAttachment, "file">[];
  pendingAction?: PendingAction | null;
};

export type AssistantConversation = {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: AssistantMessage[];
};

export type AssistantUi = {
  isOpen: boolean;
  maximized: boolean;
  showHistory: boolean;
  unread: number;
};

export const AGENT_MODELS: { id: AgentModel; label: string; provider: AgentModel }[] = [
  { id: "auto", label: "Auto (Groq -> Gemini)", provider: "auto" },
  { id: "groq", label: "Groq", provider: "groq" },
  { id: "gemini", label: "Gemini", provider: "gemini" }
];

export function uid(prefix: string) {
  if (crypto.randomUUID) return `${prefix}_${crypto.randomUUID()}`;
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export function createMessage(role: AssistantRole, content: string, extras?: Partial<AssistantMessage>): AssistantMessage {
  return {
    id: extras?.id ?? uid("msg"),
    role,
    content,
    ts: extras?.ts ?? Date.now(),
    attachments: extras?.attachments ?? [],
    pendingAction: extras?.pendingAction ?? null
  };
}

export function createConversation(messages?: AssistantMessage[]): AssistantConversation {
  const now = Date.now();
  const seed = messages?.length ? messages : [createMessage("assistant", "Ask about replies, threads, drafts, queue status, or follow-ups.")];
  return {
    id: uid("chat"),
    title: deriveTitle(seed),
    createdAt: now,
    updatedAt: now,
    messages: seed
  };
}

export function deriveTitle(messages: AssistantMessage[]) {
  const firstUser = messages.find((message) => message.role === "user" && message.content.trim());
  if (!firstUser) return "New chat";
  const compact = firstUser.content.replace(/\s+/g, " ").trim();
  return compact.length > 48 ? `${compact.slice(0, 48).trim()}...` : compact;
}

export function safeJson<T>(raw: string | null, fallback: T): T {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function loadConversations() {
  const rows = safeJson<AssistantConversation[]>(localStorage.getItem(STORAGE.conversations), []);
  return rows.length ? rows : [createConversation()];
}

export function saveConversations(conversations: AssistantConversation[], currentId: string) {
  const clean = conversations.map((conversation) => ({
    ...conversation,
    messages: conversation.messages.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      ts: message.ts,
      attachments: message.attachments ?? [],
      pendingAction: message.pendingAction ?? null
    }))
  }));
  localStorage.setItem(STORAGE.conversations, JSON.stringify(clean));
  localStorage.setItem(STORAGE.currentId, currentId);
}

export function loadUi(): AssistantUi {
  return {
    isOpen: false,
    maximized: false,
    showHistory: false,
    unread: 0,
    ...safeJson<Partial<AssistantUi>>(localStorage.getItem(STORAGE.ui), {})
  };
}

export function saveUi(ui: AssistantUi) {
  localStorage.setItem(STORAGE.ui, JSON.stringify(ui));
}

export function loadModel(): AgentModel {
  const stored = localStorage.getItem(STORAGE.model) as AgentModel | null;
  return stored && AGENT_MODELS.some((model) => model.id === stored) ? stored : "auto";
}

export function saveModel(model: AgentModel) {
  localStorage.setItem(STORAGE.model, model);
}

export function stripAttachment(attachment: AssistantAttachment): Omit<AssistantAttachment, "file"> {
  return {
    id: attachment.id,
    name: attachment.name,
    size: attachment.size,
    mimeType: attachment.mimeType,
    kind: attachment.kind
  };
}
