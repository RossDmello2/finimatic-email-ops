# Verba-Style Floating Assistant Replication Guide

Use this file as the instruction document for a coding agent that must recreate a Verba/Varva-style assistant inside another project.

The goal is not to rewrite the target app. The goal is to add a small, isolated assistant widget that sits at the bottom-right corner, opens on click, and provides chat, model switching, file attachments, voice-to-text input, history, copy, delete, minimize, maximize, and close controls.

## 1. Non-Negotiable Rules For The Coding Agent

1. Read the target project before editing.
2. Find the existing frontend entrypoint, state pattern, API calling pattern, styling system, and build/test commands.
3. Do not rewrite the existing app, router, auth, layout, backend, or provider system unless the user explicitly asked for it.
4. Add the assistant as an isolated feature/module with prefixed classes and storage keys.
5. Do not hardcode real API keys.
6. Do not print real API keys, transcripts, uploads, or user files in logs.
7. Reuse existing API/client utilities if the target app already has them.
8. If the target app already has a chat endpoint, call that endpoint instead of inventing a new provider layer.
9. If the target app is static-only, provider calls may be client-side, but keys must be user-entered and stored locally, never committed.
10. If the target app has a backend and provider keys must be secret, use the existing backend pattern or ask the user before adding a new API route.
11. Keep CSS names isolated. Use a prefix such as `va-` or `floating-assistant-`.
12. Do not add global keyboard shortcuts except `Escape` to close the assistant while it is open.
13. Do not block clicks on the host app except inside the assistant panel and launcher.
14. Do not auto-send dictated text. Speech-to-text should paste text into the assistant input box only.
15. If attachment analysis is not supported by the selected model, show a clear UI message instead of pretending the file was analyzed.

## 2. Success Criteria

The implementation is complete only when all items below work:

- A circular assistant launcher is fixed at the bottom-right corner.
- Clicking the launcher opens the assistant panel.
- The panel can be minimized/closed back to the launcher.
- The panel can be maximized and restored.
- The user can type a message and send it.
- The assistant response appears in the message list.
- The user can switch models from a model dropdown.
- The user can attach files with a button.
- The user can drag/drop or paste files if the host app supports browser file APIs.
- The assistant shows attached file names before sending.
- The user can remove pending attachments.
- The microphone button optionally converts speech to text in the input box.
- The speech feature has two possible implementations:
  - Browser `SpeechRecognition` / `webkitSpeechRecognition`.
  - Optional Groq Whisper transcription using `whisper-large-v3-turbo`.
- The user can create a new chat.
- The user can open conversation history.
- The user can select an old conversation.
- The user can delete a conversation from history.
- The user can clear the current assistant thread.
- The user can copy the last assistant answer.
- Conversation history persists across page reloads.
- Draft text persists during the current browser session.
- The widget does not break the existing app layout, scrolling, input fields, routes, or keyboard behavior.
- The assistant works on desktop and mobile widths without clipped buttons or unreadable text.

## 3. Reference Behavior To Recreate

The reference assistant has these major parts:

- A fixed shell that fills the viewport but uses `pointer-events: none`.
- The launcher and panel use `pointer-events: auto`, so the host page remains clickable outside the assistant.
- A circular launcher sits in the bottom-right corner.
- The assistant panel opens above the launcher.
- Header controls include:
  - New chat.
  - Conversation history.
  - Copy last assistant answer.
  - Clear/delete current thread.
  - Minimize.
  - Maximize/restore.
  - Close.
- A history drawer lists prior conversations.
- A message list renders user and assistant bubbles.
- The footer contains:
  - Textarea input.
  - Model selector.
  - Hidden file input.
  - Attach button.
  - Microphone button.
  - Send button.
- State is persisted with browser storage:
  - Conversations in `localStorage`.
  - Current conversation id in `localStorage`.
  - UI open/history/unread state in `localStorage`.
  - Draft input in `sessionStorage`.
- Attachments are kept in memory while pending or in active messages. Do not store large file bytes in `localStorage`.

## 4. Recommended File Layout

Choose the smallest layout that matches the target project.

For a plain HTML/vanilla JavaScript project:

```text
assets/assistant/floating-assistant.js
assets/assistant/floating-assistant.css
```

For a React/Vite/Next project:

```text
src/features/floating-assistant/AssistantWidget.tsx
src/features/floating-assistant/assistantStore.ts
src/features/floating-assistant/assistantApi.ts
src/features/floating-assistant/AssistantWidget.css
```

For any other framework:

```text
Create one isolated assistant component.
Create one isolated assistant state/store module.
Create one isolated provider/API adapter module.
Create one isolated stylesheet or scoped style block.
Mount it once near the app root.
```

Do not scatter assistant logic across unrelated existing files unless the target project already requires that pattern.

## 5. Data Model

Use this exact shape or an equivalent framework type.

```js
const STORAGE = {
  conversations: "va_assistant_conversations",
  currentConversationId: "va_assistant_current",
  ui: "va_assistant_ui",
  model: "va_assistant_model",
  providerKeys: "va_provider_keys"
};

const SESSION = {
  draft: "va_assistant_draft"
};

const assistantState = {
  isOpen: false,
  maximized: false,
  showHistory: false,
  isSending: false,
  isListening: false,
  unread: 0,
  model: "local:default",
  draft: "",
  pendingAttachments: [],
  currentConversationId: "",
  conversations: []
};

// Conversation:
// {
//   id: "chat_...",
//   title: "New chat",
//   createdAt: 1710000000000,
//   updatedAt: 1710000000000,
//   messages: [...]
// }

// Message:
// {
//   role: "user" | "assistant",
//   content: "text",
//   ts: 1710000000000,
//   attachments: [...]
// }

// Attachment metadata:
// {
//   id: "att_...",
//   name: "file.pdf",
//   size: 12345,
//   mimeType: "application/pdf",
//   kind: "image" | "pdf" | "text",
//   file: File // only in memory, never localStorage
// }
```

Rules:

- Store only serializable metadata and message text in `localStorage`.
- Do not store raw `File`, Blob, base64 images, PDF bytes, or large text in `localStorage`.
- Keep raw files in memory until the user sends the message.
- If the page reloads, old messages can keep file names but should not claim the file bytes are still available.

## 6. Model Catalog

Use a model catalog so the dropdown is simple and the agent can route requests predictably.

Adapt the model ids to the target project. The local coding agent should usually be first if it is the main reason for the assistant.

```js
const ASSISTANT_MODELS = [
  {
    id: "local:default",
    provider: "local",
    model: "local-coding-agent",
    label: "Local coding agent",
    meta: "Uses the project's local model or local OpenAI-compatible endpoint",
    supportsImages: false,
    supportsPdf: false,
    supportsTextFiles: true
  },
  {
    id: "groq:openai/gpt-oss-120b",
    provider: "groq",
    model: "openai/gpt-oss-120b",
    label: "Groq - GPT OSS 120B",
    meta: "General text assistant",
    supportsImages: false,
    supportsPdf: false,
    supportsTextFiles: true
  },
  {
    id: "groq:llama-3.3-70b-versatile",
    provider: "groq",
    model: "llama-3.3-70b-versatile",
    label: "Groq - Llama 3.3 70B",
    meta: "Longer answers and reasoning",
    supportsImages: false,
    supportsPdf: false,
    supportsTextFiles: true
  },
  {
    id: "gemini:gemini-2.5-flash-lite",
    provider: "gemini",
    model: "gemini-2.5-flash-lite",
    label: "Gemini - Flash Lite",
    meta: "Optional image/PDF analysis",
    supportsImages: true,
    supportsPdf: true,
    supportsTextFiles: true
  }
];
```

Important:

- Verify provider model ids against the current provider docs before final release.
- If a model does not support images/PDFs, allow text files only.
- If the user chooses "local coding agent", route to the existing local model endpoint or the target app's existing assistant API.

## 7. Minimal DOM Structure

Use this structure for vanilla JavaScript, or map it to equivalent framework components.

```html
<div class="va-shell" id="vaShell">
  <section class="va-panel" id="vaPanel" hidden aria-label="Assistant panel">
    <header class="va-header">
      <div class="va-title">
        <div class="va-avatar" aria-hidden="true">AI</div>
        <div>
          <div class="va-name">Assistant</div>
          <div class="va-meta" id="vaRuntimeMeta">Ready</div>
        </div>
      </div>

      <div class="va-actions">
        <button type="button" id="vaNewChatBtn" title="New chat" aria-label="New chat">+</button>
        <button type="button" id="vaHistoryBtn" title="Conversation history" aria-label="Conversation history">H</button>
        <button type="button" id="vaCopyLastBtn" title="Copy last assistant answer" aria-label="Copy last assistant answer">C</button>
        <button type="button" id="vaClearBtn" title="Clear assistant thread" aria-label="Clear assistant thread">D</button>
        <button type="button" id="vaMinBtn" title="Minimize assistant" aria-label="Minimize assistant">-</button>
        <button type="button" id="vaMaxBtn" title="Maximize assistant" aria-label="Maximize assistant">[]</button>
        <button type="button" id="vaCloseBtn" title="Close assistant" aria-label="Close assistant">x</button>
      </div>
    </header>

    <div class="va-history" id="vaHistoryPanel" hidden>
      <div class="va-history-top">
        <strong>Chats</strong>
        <button type="button" id="vaHistoryNewBtn">New chat</button>
      </div>
      <div class="va-history-list" id="vaHistoryList"></div>
    </div>

    <div class="va-messages" id="vaMessages"></div>

    <footer class="va-footer">
      <div class="va-input-wrap" id="vaInputWrap">
        <textarea id="vaInput" class="va-input" placeholder="Ask the assistant..."></textarea>

        <div class="va-attachment-preview" id="vaAttachmentPreview" hidden>
          <span id="vaAttachmentMeta"></span>
          <button type="button" id="vaAttachmentRemove" aria-label="Remove attachment">x</button>
        </div>

        <div class="va-composer-row">
          <select id="vaModelSelect" aria-label="Assistant model"></select>
          <input type="file" id="vaFileInput" hidden multiple accept="image/png,image/jpeg,image/webp,application/pdf,.pdf,text/plain,.txt,text/markdown,.md,text/csv,.csv,application/json,.json">
          <button type="button" id="vaAttachBtn" title="Add files">Attach</button>
          <button type="button" id="vaMicBtn" title="Voice input">Mic</button>
          <button type="button" id="vaSendBtn" title="Send">Send</button>
        </div>
      </div>
    </footer>
  </section>

  <button class="va-launcher" id="vaLauncher" aria-label="Open assistant" title="Open assistant">
    <span class="va-unread" id="vaUnread" hidden>0</span>
    <span class="va-launcher-face" aria-hidden="true">AI</span>
  </button>
</div>
```

Replace text placeholders like `AI`, `H`, `C`, `D`, `[]`, and `x` with the host project's icon system if it has one.

## 8. Core CSS

Use a prefixed stylesheet. This keeps the assistant from breaking the target app.

```css
.va-shell {
  --va-safe-top: calc(16px + env(safe-area-inset-top, 0px));
  --va-safe-right: calc(18px + env(safe-area-inset-right, 0px));
  --va-safe-bottom: calc(18px + env(safe-area-inset-bottom, 0px));
  --va-safe-left: calc(16px + env(safe-area-inset-left, 0px));
  position: fixed;
  top: var(--va-safe-top);
  right: var(--va-safe-right);
  bottom: var(--va-safe-bottom);
  left: var(--va-safe-left);
  z-index: 9999;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: flex-end;
  gap: 12px;
  pointer-events: none;
  font-family: inherit;
}

.va-shell > * {
  pointer-events: auto;
}

.va-launcher {
  width: 64px;
  height: 64px;
  border: 0;
  border-radius: 999px;
  cursor: pointer;
  background: radial-gradient(circle at 30% 25%, #ffffff, #dbeafe 42%, #60a5fa);
  box-shadow: 0 22px 48px rgba(15, 23, 42, 0.22);
  color: #0f172a;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.va-launcher:focus-visible,
.va-actions button:focus-visible,
.va-composer-row button:focus-visible,
.va-composer-row select:focus-visible,
.va-input:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}

.va-unread {
  position: absolute;
  top: 4px;
  right: 4px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: #ef4444;
  color: white;
  font-size: 11px;
  line-height: 18px;
}

.va-panel {
  width: min(440px, 100%);
  height: min(700px, calc(100dvh - var(--va-safe-top) - var(--va-safe-bottom)));
  max-height: calc(100dvh - var(--va-safe-top) - var(--va-safe-bottom));
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.45);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 28px 70px rgba(15, 23, 42, 0.22);
  opacity: 0;
  transform: translateY(12px) scale(0.98);
  transition: opacity 160ms ease, transform 160ms ease;
}

.va-shell.va-open .va-panel {
  opacity: 1;
  transform: translateY(0) scale(1);
}

.va-shell.va-maximized .va-panel {
  width: min(860px, 100%);
  height: calc(100dvh - var(--va-safe-top) - var(--va-safe-bottom));
}

.va-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
}

.va-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.va-avatar {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: #111827;
  color: white;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex: 0 0 auto;
}

.va-name {
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
}

.va-meta {
  margin-top: 2px;
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 210px;
}

.va-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  flex-wrap: wrap;
}

.va-actions button,
.va-composer-row button {
  min-width: 36px;
  height: 36px;
  border-radius: 10px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  background: #ffffff;
  color: #334155;
  cursor: pointer;
}

.va-history {
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
  padding: 10px 12px;
  background: #f8fafc;
}

.va-history-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.va-history-list {
  max-height: 220px;
  overflow: auto;
  display: grid;
  gap: 8px;
}

.va-history-item {
  width: 100%;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: center;
  padding: 9px 10px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 10px;
  background: white;
  text-align: left;
  cursor: pointer;
}

.va-history-item.active {
  border-color: #2563eb;
  background: #eff6ff;
}

.va-messages {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.va-message {
  display: flex;
  gap: 8px;
}

.va-message.user {
  justify-content: flex-end;
}

.va-bubble {
  max-width: 84%;
  padding: 10px 12px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.va-message.user .va-bubble {
  color: white;
  background: #2563eb;
  border-top-right-radius: 5px;
}

.va-message.assistant .va-bubble {
  color: #0f172a;
  background: #f8fafc;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-top-left-radius: 5px;
}

.va-footer {
  border-top: 1px solid rgba(226, 232, 240, 0.95);
  padding: 10px 12px;
  background: #ffffff;
}

.va-input-wrap {
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 14px;
  padding: 8px;
  background: white;
}

.va-input-wrap.drag-over {
  border-color: #2563eb;
  box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.1);
}

.va-input {
  width: 100%;
  min-height: 56px;
  max-height: 140px;
  resize: vertical;
  border: 0;
  outline: 0;
  font: inherit;
  font-size: 14px;
  line-height: 1.5;
}

.va-attachment-preview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  padding: 7px 9px;
  border-radius: 10px;
  background: #f1f5f9;
  color: #334155;
  font-size: 12px;
}

.va-composer-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.va-composer-row select {
  min-width: 0;
  flex: 1;
  height: 36px;
  border-radius: 10px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  background: white;
}

.va-mic-listening {
  color: #1d4ed8 !important;
  border-color: #2563eb !important;
  background: #eff6ff !important;
}

.va-send-loading {
  opacity: 0.72;
  cursor: wait !important;
}

@media (max-width: 640px) {
  .va-shell {
    --va-safe-top: calc(8px + env(safe-area-inset-top, 0px));
    --va-safe-right: calc(8px + env(safe-area-inset-right, 0px));
    --va-safe-bottom: calc(8px + env(safe-area-inset-bottom, 0px));
    --va-safe-left: calc(8px + env(safe-area-inset-left, 0px));
  }

  .va-launcher {
    width: 54px;
    height: 54px;
  }

  .va-shell.va-open .va-panel {
    width: auto;
    height: min(82dvh, calc(100dvh - 16px));
    max-height: calc(100dvh - 16px);
    border-radius: 14px;
  }

  .va-actions {
    display: grid;
    grid-template-columns: repeat(4, 36px);
  }

  .va-input,
  .va-composer-row select {
    font-size: 16px;
  }

  .va-composer-row {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .va-composer-row select {
    flex-basis: 100%;
  }
}
```

## 9. Core JavaScript Functions

This is a minimal vanilla JavaScript outline. In React/Vue/Svelte, convert the same functions into component state/actions.

```js
function uid(prefix) {
  return prefix + "_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);
}

function safeJsonParse(raw, fallback) {
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[ch]));
}

function createMessage(role, content, extras = {}) {
  return {
    role: role === "user" ? "user" : "assistant",
    content: String(content || "").trim(),
    ts: extras.ts || Date.now(),
    attachments: Array.isArray(extras.attachments) ? extras.attachments.map(attachmentForMessage).filter(Boolean) : []
  };
}

function createConversation(seedMessages) {
  const now = Date.now();
  const messages = seedMessages && seedMessages.length
    ? seedMessages
    : [createMessage("assistant", "Hi. I can help with this project. Ask me anything.")];

  return {
    id: uid("chat"),
    title: deriveTitle(messages),
    createdAt: now,
    updatedAt: now,
    messages
  };
}

function deriveTitle(messages) {
  const firstUser = messages.find((m) => m.role === "user" && m.content);
  if (!firstUser) return "New chat";
  const compact = firstUser.content.replace(/\s+/g, " ").trim();
  return compact.length > 52 ? compact.slice(0, 52).trim() + "..." : compact;
}

function stripFileForStorage(att) {
  if (!att) return null;
  return {
    id: att.id,
    name: att.name,
    size: att.size,
    mimeType: att.mimeType,
    kind: att.kind
  };
}

function attachmentForMessage(att) {
  const clean = stripFileForStorage(att);
  if (!clean) return null;
  if (att.textContent) clean.textContent = String(att.textContent);
  if (att.dataUrl) clean.dataUrl = String(att.dataUrl);
  return clean;
}

function loadAssistantState() {
  const conversations = safeJsonParse(localStorage.getItem(STORAGE.conversations) || "[]", []);
  const currentConversationId = localStorage.getItem(STORAGE.currentConversationId) || "";
  const ui = safeJsonParse(localStorage.getItem(STORAGE.ui) || "{}", {});
  const model = localStorage.getItem(STORAGE.model) || ASSISTANT_MODELS[0].id;
  const draft = sessionStorage.getItem(SESSION.draft) || "";

  assistantState.conversations = Array.isArray(conversations) ? conversations : [];
  if (!assistantState.conversations.length) {
    assistantState.conversations = [createConversation()];
  }

  assistantState.currentConversationId =
    currentConversationId && assistantState.conversations.some((c) => c.id === currentConversationId)
      ? currentConversationId
      : assistantState.conversations[0].id;

  assistantState.isOpen = !!ui.isOpen && !ui.minimized;
  assistantState.maximized = false;
  assistantState.showHistory = !!ui.showHistory;
  assistantState.unread = Math.max(0, Number(ui.unread || 0) || 0);
  assistantState.model = ASSISTANT_MODELS.some((m) => m.id === model) ? model : ASSISTANT_MODELS[0].id;
  assistantState.draft = draft;
}

function getCurrentConversation() {
  return assistantState.conversations.find((c) => c.id === assistantState.currentConversationId);
}

function getMessages() {
  return getCurrentConversation()?.messages || [];
}

function persistConversations() {
  const clean = assistantState.conversations.map((conv) => ({
    ...conv,
    messages: (conv.messages || []).map((msg) => ({
      role: msg.role === "user" ? "user" : "assistant",
      content: String(msg.content || ""),
      ts: Number(msg.ts || Date.now()),
      attachments: (msg.attachments || []).map(stripFileForStorage).filter(Boolean)
    }))
  }));
  localStorage.setItem(STORAGE.conversations, JSON.stringify(clean));
  localStorage.setItem(STORAGE.currentConversationId, assistantState.currentConversationId || "");
}

function persistUi() {
  localStorage.setItem(STORAGE.ui, JSON.stringify({
    isOpen: !!assistantState.isOpen,
    minimized: !assistantState.isOpen,
    maximized: !!assistantState.maximized,
    showHistory: !!assistantState.showHistory,
    unread: Number(assistantState.unread || 0)
  }));
}

function persistDraft(value) {
  assistantState.draft = String(value || "");
  sessionStorage.setItem(SESSION.draft, assistantState.draft);
}
```

## 10. Rendering Logic

```js
function renderAssistant() {
  const shell = document.getElementById("vaShell");
  const panel = document.getElementById("vaPanel");
  const messagesEl = document.getElementById("vaMessages");
  const unreadEl = document.getElementById("vaUnread");
  const input = document.getElementById("vaInput");
  const modelSelect = document.getElementById("vaModelSelect");
  const historyPanel = document.getElementById("vaHistoryPanel");

  shell.classList.toggle("va-open", assistantState.isOpen);
  shell.classList.toggle("va-maximized", assistantState.maximized);
  panel.hidden = !assistantState.isOpen;
  historyPanel.hidden = !assistantState.showHistory;

  unreadEl.hidden = !assistantState.unread;
  unreadEl.textContent = assistantState.unread > 9 ? "9+" : String(assistantState.unread);

  if (document.activeElement !== input) input.value = assistantState.draft || "";

  modelSelect.innerHTML = ASSISTANT_MODELS.map((m) => {
    return `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)} - ${escapeHtml(m.meta)}</option>`;
  }).join("");
  modelSelect.value = assistantState.model;

  messagesEl.innerHTML = getMessages().map((msg) => {
    const role = msg.role === "user" ? "user" : "assistant";
    const attachments = Array.isArray(msg.attachments) && msg.attachments.length
      ? msg.attachments.map((a) => `<div class="va-attachment-line">${escapeHtml(a.kind || "file")}: ${escapeHtml(a.name || "attachment")}</div>`).join("")
      : "";
    return `
      <div class="va-message ${role}">
        <div class="va-bubble">${attachments}${escapeHtml(msg.content).replace(/\n/g, "<br>")}</div>
      </div>
    `;
  }).join("");

  renderHistory();
  renderComposer();
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderHistory() {
  const historyList = document.getElementById("vaHistoryList");
  historyList.innerHTML = assistantState.conversations
    .slice()
    .sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))
    .map((conv) => {
      const active = conv.id === assistantState.currentConversationId ? " active" : "";
      return `
        <div class="va-history-item${active}" role="button" tabindex="0" data-chat-id="${escapeHtml(conv.id)}">
          <span>${escapeHtml(conv.title || "New chat")}</span>
          <span>
            <button type="button" data-delete-chat="${escapeHtml(conv.id)}" aria-label="Delete conversation">x</button>
          </span>
        </div>
      `;
    }).join("");
}

function renderComposer() {
  const sendBtn = document.getElementById("vaSendBtn");
  const micBtn = document.getElementById("vaMicBtn");
  const attachmentPreview = document.getElementById("vaAttachmentPreview");
  const attachmentMeta = document.getElementById("vaAttachmentMeta");

  const hasDraft = !!String(assistantState.draft || "").trim();
  const hasFiles = assistantState.pendingAttachments.length > 0;
  sendBtn.disabled = assistantState.isSending || (!hasDraft && !hasFiles);
  sendBtn.classList.toggle("va-send-loading", assistantState.isSending);

  micBtn.classList.toggle("va-mic-listening", assistantState.isListening);
  micBtn.setAttribute("aria-pressed", assistantState.isListening ? "true" : "false");

  attachmentPreview.hidden = !hasFiles;
  attachmentMeta.textContent = hasFiles
    ? assistantState.pendingAttachments.map((f) => f.name).join(", ")
    : "";
}
```

Note: `.va-history-item` is a `div` because it contains a delete button. Do not nest real buttons inside another button.

## 11. Event Binding

```js
function bindAssistantEvents() {
  const launcher = document.getElementById("vaLauncher");
  const input = document.getElementById("vaInput");
  const modelSelect = document.getElementById("vaModelSelect");
  const fileInput = document.getElementById("vaFileInput");
  const inputWrap = document.getElementById("vaInputWrap");

  launcher.addEventListener("click", () => setAssistantOpen(!assistantState.isOpen));

  document.getElementById("vaCloseBtn").addEventListener("click", () => setAssistantOpen(false));
  document.getElementById("vaMinBtn").addEventListener("click", () => setAssistantOpen(false));
  document.getElementById("vaMaxBtn").addEventListener("click", () => toggleMaximized());
  document.getElementById("vaNewChatBtn").addEventListener("click", () => createNewChat());
  document.getElementById("vaHistoryNewBtn").addEventListener("click", () => createNewChat());
  document.getElementById("vaHistoryBtn").addEventListener("click", () => {
    assistantState.showHistory = !assistantState.showHistory;
    if (!assistantState.isOpen) assistantState.isOpen = true;
    persistUi();
    renderAssistant();
  });

  document.getElementById("vaClearBtn").addEventListener("click", () => clearCurrentThread());
  document.getElementById("vaCopyLastBtn").addEventListener("click", () => copyLastAssistantAnswer());
  document.getElementById("vaSendBtn").addEventListener("click", () => submitAssistantDraft());
  document.getElementById("vaAttachBtn").addEventListener("click", () => fileInput.click());
  document.getElementById("vaAttachmentRemove").addEventListener("click", () => {
    assistantState.pendingAttachments = [];
    fileInput.value = "";
    renderAssistant();
  });

  document.getElementById("vaMicBtn").addEventListener("click", () => {
    if (assistantState.isListening) stopAssistantVoiceInput();
    else startAssistantVoiceInput();
  });

  input.addEventListener("input", () => {
    persistDraft(input.value);
    renderComposer();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitAssistantDraft();
    }
  });

  modelSelect.addEventListener("change", () => {
    assistantState.model = modelSelect.value;
    localStorage.setItem(STORAGE.model, assistantState.model);
    renderAssistant();
  });

  fileInput.addEventListener("change", () => {
    handleAssistantFiles(Array.from(fileInput.files || []));
  });

  ["dragenter", "dragover"].forEach((name) => {
    inputWrap.addEventListener(name, (event) => {
      event.preventDefault();
      inputWrap.classList.add("drag-over");
    });
  });

  ["dragleave", "drop"].forEach((name) => {
    inputWrap.addEventListener(name, () => inputWrap.classList.remove("drag-over"));
  });

  inputWrap.addEventListener("drop", (event) => {
    event.preventDefault();
    handleAssistantFiles(Array.from(event.dataTransfer?.files || []));
  });

  input.addEventListener("paste", (event) => {
    const files = Array.from(event.clipboardData?.items || [])
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter(Boolean);
    if (files.length) {
      event.preventDefault();
      handleAssistantFiles(files);
    }
  });

  document.getElementById("vaHistoryList").addEventListener("click", (event) => {
    const deleteBtn = event.target.closest("[data-delete-chat]");
    if (deleteBtn) {
      event.stopPropagation();
      deleteConversation(deleteBtn.getAttribute("data-delete-chat"));
      return;
    }
    const item = event.target.closest("[data-chat-id]");
    if (item) selectConversation(item.getAttribute("data-chat-id"));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && assistantState.isOpen) {
      setAssistantOpen(false);
    }
  });
}
```

## 12. Open, Close, Maximize, History, Copy, Delete

```js
function setAssistantOpen(open) {
  assistantState.isOpen = !!open;
  if (open) {
    assistantState.unread = 0;
  } else {
    assistantState.maximized = false;
    if (assistantState.isListening) stopBrowserDictation();
  }
  persistUi();
  renderAssistant();
  if (open) {
    setTimeout(() => document.getElementById("vaInput")?.focus(), 60);
  }
}

function toggleMaximized() {
  assistantState.maximized = !assistantState.maximized;
  if (assistantState.maximized) assistantState.isOpen = true;
  persistUi();
  renderAssistant();
}

function createNewChat() {
  const conv = createConversation();
  assistantState.conversations.unshift(conv);
  assistantState.currentConversationId = conv.id;
  assistantState.showHistory = false;
  persistDraft("");
  assistantState.pendingAttachments = [];
  persistConversations();
  persistUi();
  renderAssistant();
}

function selectConversation(id) {
  if (!assistantState.conversations.some((c) => c.id === id)) return;
  assistantState.currentConversationId = id;
  assistantState.showHistory = false;
  persistConversations();
  persistUi();
  renderAssistant();
}

function deleteConversation(id) {
  assistantState.conversations = assistantState.conversations.filter((c) => c.id !== id);
  if (!assistantState.conversations.length) {
    assistantState.conversations = [createConversation()];
  }
  if (!assistantState.conversations.some((c) => c.id === assistantState.currentConversationId)) {
    assistantState.currentConversationId = assistantState.conversations[0].id;
  }
  persistConversations();
  renderAssistant();
}

function clearCurrentThread() {
  const conv = getCurrentConversation();
  if (!conv) return;
  conv.messages = [createMessage("assistant", "Thread cleared. How can I help next?")];
  conv.title = "New chat";
  conv.updatedAt = Date.now();
  assistantState.pendingAttachments = [];
  persistDraft("");
  persistConversations();
  renderAssistant();
}

async function copyLastAssistantAnswer() {
  const last = getMessages().slice().reverse().find((m) => m.role === "assistant" && m.content);
  if (!last) {
    showAssistantToast("No assistant answer to copy.");
    return;
  }
  await copyText(last.content);
  showAssistantToast("Last assistant answer copied.");
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function showAssistantToast(message) {
  // Replace with the target project's toast/notification helper if available.
  console.info("[assistant]", message);
}
```

## 13. Sending Messages

The sending flow must:

1. Read the input text.
2. Read pending attachments.
3. Validate that the selected model supports the attachments.
4. Push the user message into the current conversation.
5. Clear the input and pending attachment preview.
6. Call the provider/local model adapter.
7. Push the assistant response.
8. Persist and render.
9. Show errors as assistant messages or small toasts.

```js
async function submitAssistantDraft() {
  if (assistantState.isSending) return;
  const text = String(document.getElementById("vaInput").value || "").trim();
  const files = assistantState.pendingAttachments.slice();
  if (!text && !files.length) return;

  const modelOption = ASSISTANT_MODELS.find((m) => m.id === assistantState.model) || ASSISTANT_MODELS[0];
  if (!validateAttachmentsForModel(modelOption, files)) return;

  const conv = getCurrentConversation();
  const preparedAttachments = await prepareAttachments(files, modelOption);
  const userMessage = createMessage("user", text || defaultPromptForAttachments(preparedAttachments), {
    attachments: preparedAttachments
  });
  conv.messages.push(userMessage);
  conv.title = deriveTitle(conv.messages);
  conv.updatedAt = Date.now();

  assistantState.pendingAttachments = [];
  persistDraft("");
  document.getElementById("vaInput").value = "";
  assistantState.isSending = true;
  persistConversations();
  renderAssistant();

  try {
    const reply = await callAssistantModel({
      modelOption,
      messages: buildPromptMessages(conv.messages),
      attachments: preparedAttachments
    });
    conv.messages.push(createMessage("assistant", reply || "I could not generate a response."));
    conv.updatedAt = Date.now();
    if (!assistantState.isOpen) assistantState.unread = Math.min(9, assistantState.unread + 1);
  } catch (error) {
    conv.messages.push(createMessage("assistant", "Assistant error: " + (error.message || "request failed")));
  } finally {
    assistantState.isSending = false;
    persistConversations();
    persistUi();
    renderAssistant();
  }
}

function buildPromptMessages(messages) {
  const recent = messages.slice(-10);
  return [
    {
      role: "system",
      content: [
        "You are the in-app coding/project assistant.",
        "Help the user with the current project.",
        "When writing code, be complete and do not use placeholders like 'rest of code here'.",
        "If files are attached, answer only from the provided file content unless the user asks for general knowledge."
      ].join("\n")
    },
    ...recent.map((m) => {
      const attachmentText = (m.attachments || [])
        .filter((att) => att.kind === "text" && att.textContent)
        .map((att) => `Attached file: ${att.name}\n\n${String(att.textContent).slice(0, 200000)}`)
        .join("\n\n");
      return {
        role: m.role,
        content: [String(m.content || ""), attachmentText].filter(Boolean).join("\n\n")
      };
    })
  ];
}
```

## 14. Provider/Local Model Adapter

This is the boundary that must be adapted to the target project.

Preferred order:

1. Use the target app's existing assistant/chat API.
2. Use the user's local coding-agent endpoint if available.
3. Use an OpenAI-compatible provider such as Groq/OpenAI only if the app already permits client-side calls or has a safe backend route.

```js
async function callAssistantModel({ modelOption, messages, attachments }) {
  if (modelOption.provider === "local") {
    return callLocalCodingAgent(modelOption, messages, attachments);
  }

  if (modelOption.provider === "groq") {
    return callOpenAICompatibleProvider({
      endpoint: "https://api.groq.com/openai/v1/chat/completions",
      apiKey: getProviderKey("groq"),
      model: modelOption.model,
      messages
    });
  }

  if (modelOption.provider === "gemini") {
    return callGeminiProvider(modelOption, messages, attachments);
  }

  throw new Error("Unsupported assistant provider: " + modelOption.provider);
}

async function callGeminiProvider(modelOption, messages, attachments) {
  // Implement only if the target project needs Gemini image/PDF analysis.
  // Prefer the target project's existing Gemini route if one exists.
  // If no Gemini integration exists, remove Gemini from ASSISTANT_MODELS instead of leaving a broken option.
  throw new Error("Gemini provider is not implemented in this project.");
}

async function callLocalCodingAgent(modelOption, messages, attachments) {
  // Replace this with the target project's local model route.
  // Common examples:
  // - POST http://localhost:11434/api/chat for Ollama-style APIs.
  // - POST http://localhost:8000/v1/chat/completions for OpenAI-compatible local servers.
  // - POST /api/assistant if the target app already exposes a backend route.

  const endpoint = window.LOCAL_ASSISTANT_ENDPOINT || "/api/assistant";
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: modelOption.model,
      messages,
      attachments: attachments.map(stripFileForStorage)
    })
  });

  if (!response.ok) {
    throw new Error(await response.text().catch(() => "Local assistant request failed"));
  }

  const data = await response.json();
  return data.reply || data.message || data.choices?.[0]?.message?.content || "";
}

async function callOpenAICompatibleProvider({ endpoint, apiKey, model, messages }) {
  if (!apiKey) throw new Error("API key is required.");

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": "Bearer " + apiKey
    },
    body: JSON.stringify({
      model,
      messages,
      temperature: 0.3,
      max_tokens: 8192
    })
  });

  if (!response.ok) {
    throw new Error(await response.text().catch(() => "Provider request failed"));
  }

  const data = await response.json();
  return data.choices?.[0]?.message?.content || "";
}
```

## 15. File Attachment Handling

Support these by default:

- Images: PNG, JPG, JPEG, WEBP.
- PDFs: only if the selected provider/model can process PDFs.
- Text-like files: TXT, MD, CSV, JSON.

```js
const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;

function inferMimeType(file) {
  const type = String(file?.type || "").toLowerCase();
  if (type) return type;
  const name = String(file?.name || "").toLowerCase();
  if (name.endsWith(".pdf")) return "application/pdf";
  if (name.endsWith(".png")) return "image/png";
  if (name.endsWith(".jpg") || name.endsWith(".jpeg")) return "image/jpeg";
  if (name.endsWith(".webp")) return "image/webp";
  if (name.endsWith(".md")) return "text/markdown";
  if (name.endsWith(".txt")) return "text/plain";
  if (name.endsWith(".csv")) return "text/csv";
  if (name.endsWith(".json")) return "application/json";
  return "";
}

function attachmentKind(mimeType) {
  if (mimeType === "application/pdf") return "pdf";
  if (mimeType.startsWith("image/")) return "image";
  return "text";
}

function handleAssistantFiles(files) {
  const allowed = new Set([
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json"
  ]);

  for (const file of files) {
    const mimeType = inferMimeType(file);
    if (!allowed.has(mimeType)) {
      showAssistantToast("Supported files: PNG, JPG, WEBP, PDF, TXT, MD, CSV, JSON.");
      return;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      showAssistantToast("Attachment is too large. Maximum size is 20 MB.");
      return;
    }
    assistantState.pendingAttachments.push({
      id: uid("att"),
      name: file.name || "attachment",
      size: file.size || 0,
      mimeType,
      kind: attachmentKind(mimeType),
      file
    });
  }

  renderAssistant();
}

function validateAttachmentsForModel(modelOption, attachments) {
  const hasImage = attachments.some((a) => a.kind === "image");
  const hasPdf = attachments.some((a) => a.kind === "pdf");
  if (hasPdf && !modelOption.supportsPdf) {
    showAssistantToast("The selected model does not support PDFs.");
    return false;
  }
  if (hasImage && !modelOption.supportsImages) {
    showAssistantToast("The selected model does not support images.");
    return false;
  }
  return true;
}

async function prepareAttachments(attachments, modelOption) {
  const prepared = [];

  for (const att of attachments) {
    if (att.kind === "text") {
      prepared.push({
        ...att,
        textContent: await att.file.text()
      });
      continue;
    }

    if (att.kind === "image" && modelOption.supportsImages) {
      prepared.push({
        ...att,
        dataUrl: await fileToDataUrl(att.file)
      });
      continue;
    }

    if (att.kind === "pdf" && modelOption.supportsPdf) {
      // Use existing target-project PDF handling if available.
      // Otherwise send to a provider that supports PDFs, or show a clear unsupported message.
      prepared.push(att);
    }
  }

  return prepared;
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Could not read file."));
    reader.readAsDataURL(file);
  });
}

function defaultPromptForAttachments(attachments) {
  if (attachments.some((a) => a.kind === "pdf")) return "Please analyze the attached PDF.";
  if (attachments.some((a) => a.kind === "image")) return "Please analyze the attached image.";
  return "Please analyze the attached file.";
}
```

## 16. Browser Speech-To-Text Option

This is the simplest mic option. It works in browsers that expose `SpeechRecognition` or `webkitSpeechRecognition`. It should paste recognized text into the input box. It should not auto-send.

```js
const SpeechRecognitionApi = window.SpeechRecognition || window.webkitSpeechRecognition;
let assistantRecognition = null;
let dictationBase = "";
let dictationFinal = "";

function setupBrowserDictation() {
  if (!SpeechRecognitionApi) return;

  assistantRecognition = new SpeechRecognitionApi();
  assistantRecognition.continuous = true;
  assistantRecognition.interimResults = true;
  assistantRecognition.maxAlternatives = 1;
  assistantRecognition.lang = "en-US";

  assistantRecognition.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const text = String(result?.[0]?.transcript || "").trim();
      if (!text) continue;
      if (result.isFinal) {
        dictationFinal = dictationFinal ? dictationFinal + " " + text : text;
      } else {
        interim = interim ? interim + " " + text : text;
      }
    }

    const next = [dictationBase, dictationFinal, interim].filter(Boolean).join(" ").trim();
    const input = document.getElementById("vaInput");
    input.value = next;
    persistDraft(next);
    renderComposer();
  };

  assistantRecognition.onend = () => {
    assistantState.isListening = false;
    renderAssistant();
  };

  assistantRecognition.onerror = (event) => {
    assistantState.isListening = false;
    renderAssistant();
    if (event.error !== "aborted" && event.error !== "no-speech") {
      showAssistantToast("Voice input is unavailable in this browser.");
    }
  };
}

function startBrowserDictation() {
  if (!assistantRecognition) {
    showAssistantToast("Browser voice input is not supported here.");
    return;
  }

  dictationBase = String(document.getElementById("vaInput").value || "").trim();
  dictationFinal = "";

  try {
    assistantRecognition.start();
    assistantState.isListening = true;
    renderAssistant();
    document.getElementById("vaInput").focus();
  } catch {
    assistantState.isListening = false;
    renderAssistant();
    showAssistantToast("Could not start voice input.");
  }
}

function stopBrowserDictation() {
  assistantState.isListening = false;
  try {
    assistantRecognition?.stop();
  } catch {
    // Ignore stop errors.
  }
  renderAssistant();
}
```

Call `setupBrowserDictation()` once during assistant initialization.

## 17. Optional Groq Whisper Voice-To-Text

Use this only if the target app has Groq API keys and the user wants model-based transcription. This option records short audio and sends it to Groq Whisper.

Model:

```text
whisper-large-v3-turbo
```

Endpoint:

```text
POST https://api.groq.com/openai/v1/audio/transcriptions
```

Implementation rule:

- Do not stream raw microphone forever.
- Start recording when the user presses the mic button.
- Stop recording when the user presses it again or after a safe timeout.
- Send the recorded audio blob to Whisper.
- Put the returned text into the input box.
- Do not auto-send.

```js
let whisperRecorder = null;
let whisperChunks = [];
let whisperStream = null;

async function startGroqWhisperDictation() {
  const groqKey = getProviderKey("groq");
  if (!groqKey) {
    showAssistantToast("Groq key required for Whisper voice input.");
    return;
  }

  whisperStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  whisperChunks = [];
  whisperRecorder = new MediaRecorder(whisperStream);

  whisperRecorder.ondataavailable = (event) => {
    if (event.data && event.data.size) whisperChunks.push(event.data);
  };

  whisperRecorder.onstop = async () => {
    try {
      const blob = new Blob(whisperChunks, { type: "audio/webm" });
      const text = await transcribeWithGroqWhisper(blob, groqKey);
      const input = document.getElementById("vaInput");
      const existing = String(input.value || "").trim();
      const next = [existing, text].filter(Boolean).join(" ").trim();
      input.value = next;
      persistDraft(next);
      renderComposer();
    } finally {
      whisperStream?.getTracks().forEach((track) => track.stop());
      whisperStream = null;
      whisperRecorder = null;
      whisperChunks = [];
      assistantState.isListening = false;
      renderAssistant();
    }
  };

  whisperRecorder.start();
  assistantState.isListening = true;
  renderAssistant();
}

function stopGroqWhisperDictation() {
  if (whisperRecorder && whisperRecorder.state !== "inactive") {
    whisperRecorder.stop();
  }
}

async function transcribeWithGroqWhisper(blob, groqKey) {
  const form = new FormData();
  form.append("file", blob, "assistant-voice.webm");
  form.append("model", "whisper-large-v3-turbo");
  form.append("response_format", "json");

  const response = await fetch("https://api.groq.com/openai/v1/audio/transcriptions", {
    method: "POST",
    headers: {
      "Authorization": "Bearer " + groqKey
    },
    body: form
  });

  if (!response.ok) {
    throw new Error(await response.text().catch(() => "Whisper transcription failed"));
  }

  const data = await response.json();
  return String(data.text || "").trim();
}
```

How to choose between browser dictation and Whisper:

```js
const VOICE_MODE = "browser"; // "browser" or "groq-whisper"

function startAssistantVoiceInput() {
  if (VOICE_MODE === "groq-whisper") return startGroqWhisperDictation();
  return startBrowserDictation();
}

function stopAssistantVoiceInput() {
  if (VOICE_MODE === "groq-whisper") return stopGroqWhisperDictation();
  return stopBrowserDictation();
}
```

## 18. Provider Key Storage

If the app is static and must store user-entered keys in the browser, use localStorage. Never commit keys.

```js
function getProviderKey(provider) {
  const store = safeJsonParse(localStorage.getItem(STORAGE.providerKeys) || "{}", {});
  const key = store?.[provider];
  return typeof key === "string" ? key.trim() : "";
}

function saveProviderKey(provider, key) {
  const store = safeJsonParse(localStorage.getItem(STORAGE.providerKeys) || "{}", {});
  store[provider] = String(key || "").trim();
  localStorage.setItem(STORAGE.providerKeys, JSON.stringify(store));
}
```

If the target app has a backend, prefer a server-side secret store or environment variable and call a project API route from the assistant.

## 19. Mounting The Assistant

For vanilla JavaScript:

```js
function initFloatingAssistant() {
  // 1. Insert DOM if not already present.
  // 2. Load state.
  // 3. Bind events once.
  // 4. Setup browser dictation once.
  // 5. Render.

  loadAssistantState();
  bindAssistantEvents();
  setupBrowserDictation();
  renderAssistant();
}

document.addEventListener("DOMContentLoaded", initFloatingAssistant);
```

For React:

- Mount `<AssistantWidget />` once in `App.tsx`, `RootLayout`, or the closest global shell.
- Use `useEffect` to load persisted state once.
- Use component state or a small store for `assistantState`.
- Use event handlers instead of direct DOM queries.
- Keep the CSS class names the same or convert to CSS modules.

## 20. Accessibility Requirements

Implement these before marking the task complete:

- Every icon-only button needs `aria-label` and `title`.
- Launcher has `aria-label="Open assistant"`.
- Send button has `aria-label="Send assistant message"`.
- Mic button toggles `aria-pressed`.
- History button toggles a visible/hidden history region.
- `Escape` closes the panel when open.
- On open, focus the textarea after a short delay.
- On close, stop voice recognition.
- Text must wrap inside bubbles.
- Buttons must remain at least 44px high on touch devices.

## 21. Integration Checklist For The Coding Agent

Before editing:

- Identify framework: plain HTML, React, Next, Vue, Svelte, Angular, etc.
- Identify root mount file.
- Identify styling system.
- Identify existing API call helper.
- Identify existing auth/secret handling.
- Identify test/build commands.

During implementation:

- Add the assistant module/component.
- Add the assistant stylesheet.
- Mount assistant once near app root.
- Use prefixed class names.
- Add storage keys with a project-specific prefix.
- Wire model dropdown.
- Wire local coding-agent provider first if available.
- Wire optional Groq/OpenAI/Gemini only if appropriate.
- Add file validation.
- Add browser dictation.
- Add optional Groq Whisper only if requested or useful.
- Add history persistence.
- Add copy last assistant answer.
- Add clear/delete controls.
- Add minimize/maximize/close controls.

After implementation:

- Run the project's normal test/build/lint command.
- Open the app in a browser.
- Verify launcher location on desktop.
- Verify launcher location on mobile.
- Open and close assistant repeatedly.
- Type and send a message.
- Switch model and send another message.
- Attach a text file and verify the file name appears.
- Remove the attachment.
- Try unsupported file type and verify a clear error.
- Use mic button and confirm text appears in input.
- Create a new chat.
- Open history and switch chats.
- Delete a history chat.
- Copy last assistant answer and paste it elsewhere.
- Maximize and restore.
- Reload page and verify conversations persist.
- Confirm no existing app workflow broke.

## 22. Common Mistakes To Avoid

- Do not put the assistant inside a layout container that clips `position: fixed`.
- Do not use generic class names like `.panel`, `.button`, `.message`, or `.chat`.
- Do not store `File` objects or base64 image data in `localStorage`.
- Do not auto-send speech text.
- Do not route images/PDFs to text-only models.
- Do not hide provider/key errors.
- Do not swallow network errors without showing the user.
- Do not add a backend proxy without checking the target project's architecture and user approval.
- Do not globally override button, textarea, body, or select styles.
- Do not let the panel cover the whole app on desktop unless maximized.
- Do not let the bottom-right launcher overlap important mobile navigation without adding safe-area/mobile offsets.

## 23. Final Acceptance Test Script

Give this checklist to the coding agent and require a pass/fail result:

```text
PASS/FAIL - App builds without errors.
PASS/FAIL - Existing app primary workflow still works.
PASS/FAIL - Assistant launcher is bottom-right and clickable.
PASS/FAIL - Assistant opens and focuses input.
PASS/FAIL - Minimize closes panel to launcher.
PASS/FAIL - Close closes panel to launcher.
PASS/FAIL - Maximize expands panel; second click restores it.
PASS/FAIL - Message send displays user bubble and assistant bubble.
PASS/FAIL - Model dropdown changes selected model and persists after reload.
PASS/FAIL - File attach button opens file picker.
PASS/FAIL - Drag/drop or paste attachment works if implemented.
PASS/FAIL - Unsupported files show a clear error.
PASS/FAIL - Mic button inserts dictated/transcribed text into input without sending.
PASS/FAIL - New chat creates separate conversation.
PASS/FAIL - History opens, switches chats, and deletes chats.
PASS/FAIL - Clear thread resets current chat.
PASS/FAIL - Copy last assistant answer copies only the latest assistant response.
PASS/FAIL - Reload preserves conversations.
PASS/FAIL - Mobile viewport has no clipped header buttons, input, or send controls.
```

## 24. One-Sentence Implementation Goal

Add an isolated, bottom-right floating assistant widget to the target project, preserving all existing behavior while adding chat, model selection, file attachment, optional voice-to-text, conversation history, copy, delete, minimize, maximize, and close controls through project-compatible state, styling, and API adapters.
