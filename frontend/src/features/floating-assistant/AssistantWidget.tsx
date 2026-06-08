import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  Copy,
  History,
  Maximize2,
  Mic,
  Minimize2,
  Minus,
  Paperclip,
  Plus,
  Send,
  Trash2,
  X
} from "lucide-react";
import { toast } from "sonner";
import { AgentModel, assistantApi, getSessionToken, PendingAction } from "./assistantApi";
import {
  AGENT_MODELS,
  AssistantAttachment,
  AssistantConversation,
  createConversation,
  createMessage,
  deriveTitle,
  loadConversations,
  loadModel,
  loadUi,
  saveConversations,
  saveModel,
  saveUi,
  SESSION,
  STORAGE,
  stripAttachment,
  uid
} from "./assistantStore";
import "./AssistantWidget.css";

type SpeechRecognitionEventLike = {
  resultIndex: number;
  results: {
    length: number;
    [index: number]: {
      isFinal: boolean;
      [index: number]: { transcript: string };
    };
  };
};

type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  start: () => void;
  stop: () => void;
};

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;

const initialConversations = loadConversations();
const initialCurrentId =
  localStorage.getItem(STORAGE.currentId) && initialConversations.some((conversation) => conversation.id === localStorage.getItem(STORAGE.currentId))
    ? String(localStorage.getItem(STORAGE.currentId))
    : initialConversations[0].id;

export function AssistantWidget() {
  const [conversations, setConversations] = useState<AssistantConversation[]>(initialConversations);
  const [currentId, setCurrentId] = useState(initialCurrentId);
  const [ui, setUi] = useState(loadUi);
  const [model, setModel] = useState<AgentModel>(loadModel);
  const [draft, setDraft] = useState(() => sessionStorage.getItem(SESSION.draft) ?? "");
  const [pendingAttachments, setPendingAttachments] = useState<AssistantAttachment[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [pendingActionBusy, setPendingActionBusy] = useState<{ id: string; type: "confirm" | "cancel" } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const voiceBaseRef = useRef("");

  const currentConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === currentId) ?? conversations[0],
    [conversations, currentId]
  );

  useEffect(() => saveConversations(conversations, currentId), [conversations, currentId]);
  useEffect(() => saveUi(ui), [ui]);
  useEffect(() => saveModel(model), [model]);
  useEffect(() => sessionStorage.setItem(SESSION.draft, draft), [draft]);
  useEffect(() => {
    if (ui.isOpen) window.setTimeout(() => inputRef.current?.focus(), 60);
  }, [ui.isOpen]);
  useEffect(() => messagesEndRef.current?.scrollIntoView({ block: "end" }), [currentConversation?.messages.length, ui.isOpen]);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && ui.isOpen) {
        closePanel();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [ui.isOpen]);

  function updateConversation(updater: (conversation: AssistantConversation) => AssistantConversation) {
    setConversations((rows) =>
      rows.map((conversation) => {
        if (conversation.id !== currentId) return conversation;
        return updater(conversation);
      })
    );
  }

  function addMessage(role: "user" | "assistant", content: string, extras?: { pendingAction?: PendingAction | null; attachments?: AssistantAttachment[] }) {
    updateConversation((conversation) => {
      const nextMessage = createMessage(role, content, {
        attachments: extras?.attachments?.map(stripAttachment),
        pendingAction: extras?.pendingAction ?? null
      });
      const messages = [...conversation.messages, nextMessage];
      return {
        ...conversation,
        messages,
        title: deriveTitle(messages),
        updatedAt: Date.now()
      };
    });
  }

  function clearPendingAction(actionId?: string) {
    setConversations((rows) =>
      rows.map((conversation) => ({
        ...conversation,
        messages: conversation.messages.map((message) => ({
          ...message,
          pendingAction: !actionId || message.pendingAction?.action_id === actionId ? null : message.pendingAction
        }))
      }))
    );
  }

  function openPanel() {
    setUi((current) => ({ ...current, isOpen: true, unread: 0 }));
  }

  function closePanel() {
    stopVoice();
    setUi((current) => ({ ...current, isOpen: false, maximized: false, showHistory: false }));
  }

  function createNewChat() {
    const conversation = createConversation();
    setConversations((rows) => [conversation, ...rows]);
    setCurrentId(conversation.id);
    setDraft("");
    setPendingAttachments([]);
    setUi((current) => ({ ...current, isOpen: true, showHistory: false, unread: 0 }));
  }

  function deleteConversation(id: string) {
    setConversations((rows) => {
      const remaining = rows.filter((conversation) => conversation.id !== id);
      const next = remaining.length ? remaining : [createConversation()];
      if (!next.some((conversation) => conversation.id === currentId)) setCurrentId(next[0].id);
      return next;
    });
  }

  function clearThread() {
    updateConversation((conversation) => ({
      ...conversation,
      title: "New chat",
      updatedAt: Date.now(),
      messages: [createMessage("assistant", "Thread cleared. What should I check next?")]
    }));
    setDraft("");
    setPendingAttachments([]);
  }

  async function copyLastAnswer() {
    const last = [...(currentConversation?.messages ?? [])].reverse().find((message) => message.role === "assistant" && message.content.trim());
    if (!last) {
      toast("No assistant answer to copy.");
      return;
    }
    await navigator.clipboard.writeText(last.content);
    toast("Last answer copied.");
  }

  async function submitMessage() {
    const text = draft.trim();
    if (isSending || (!text && !pendingAttachments.length)) return;
    const attachments = pendingAttachments.slice();
    addMessage("user", text || "Please review the attached file.", { attachments });
    setDraft("");
    setPendingAttachments([]);
    setIsSending(true);
    try {
      const payload = await assistantApi.chat({
        session_token: getSessionToken(),
        message: text || "Please review the attached file.",
        provider: model,
        attachments: attachments.map(stripAttachment)
      });
      addMessage("assistant", payload.response || "No response.", { pendingAction: payload.pending_action ?? null });
      if (!ui.isOpen) setUi((current) => ({ ...current, unread: Math.min(9, current.unread + 1) }));
    } catch (error) {
      addMessage("assistant", `Assistant error: ${error instanceof Error ? error.message : "request failed"}`);
    } finally {
      setIsSending(false);
    }
  }

  async function confirmPending(action: PendingAction) {
    if (isSending || pendingActionBusy) return;
    setPendingActionBusy({ id: action.action_id, type: "confirm" });
    setIsSending(true);
    try {
      const payload = await assistantApi.confirm(getSessionToken(), action.action_id);
      clearPendingAction(action.action_id);
      addMessage("assistant", payload.response || "Confirmation processed.");
    } catch (error) {
      addMessage("assistant", `Assistant error: ${error instanceof Error ? error.message : "confirmation failed"}`);
    } finally {
      setIsSending(false);
      setPendingActionBusy(null);
    }
  }

  async function cancelPending(action: PendingAction) {
    if (isSending || pendingActionBusy) return;
    setPendingActionBusy({ id: action.action_id, type: "cancel" });
    setIsSending(true);
    try {
      const payload = await assistantApi.cancel(getSessionToken());
      clearPendingAction(action.action_id);
      addMessage("assistant", payload.response || "Cancelled. I did not send anything.");
    } catch (error) {
      addMessage("assistant", `Assistant error: ${error instanceof Error ? error.message : "cancel failed"}`);
    } finally {
      setIsSending(false);
      setPendingActionBusy(null);
    }
  }

  function inferAttachment(file: File): AssistantAttachment | null {
    const mimeType = file.type || mimeFromName(file.name);
    const allowed = ["image/png", "image/jpeg", "image/webp", "application/pdf", "text/plain", "text/markdown", "text/csv", "application/json"];
    if (!allowed.includes(mimeType)) {
      toast("Supported files: PNG, JPG, WEBP, PDF, TXT, MD, CSV, JSON.");
      return null;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      toast("Attachment is too large. Maximum size is 20 MB.");
      return null;
    }
    const kind = mimeType === "application/pdf" ? "pdf" : mimeType.startsWith("image/") ? "image" : "text";
    return { id: uid("att"), name: file.name, size: file.size, mimeType, kind, file };
  }

  function handleFiles(files: File[]) {
    const next = files.map(inferAttachment).filter(Boolean) as AssistantAttachment[];
    if (next.length) setPendingAttachments((current) => [...current, ...next]);
  }

  function startVoice() {
    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Recognition) {
      toast("Browser voice input is not supported here.");
      return;
    }
    const recognition = new Recognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    voiceBaseRef.current = draft.trim();
    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (let index = 0; index < event.results.length; index += 1) {
        const text = event.results[index][0].transcript.trim();
        if (event.results[index].isFinal) finalText += ` ${text}`;
        else interimText += ` ${text}`;
      }
      setDraft([voiceBaseRef.current, finalText.trim(), interimText.trim()].filter(Boolean).join(" ").trim());
    };
    recognition.onend = () => setIsListening(false);
    recognition.onerror = () => {
      setIsListening(false);
      toast("Voice input is unavailable in this browser.");
    };
    recognitionRef.current = recognition;
    setIsListening(true);
    recognition.start();
  }

  function stopVoice() {
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setIsListening(false);
  }

  const messages = currentConversation?.messages ?? [];

  return (
    <div className={`va-shell ${ui.isOpen ? "va-open" : ""} ${ui.maximized ? "va-maximized" : ""}`}>
      {ui.isOpen && (
        <section className="va-panel" aria-label="Email assistant">
          <header className="va-header">
            <div className="va-title">
              <div className="va-avatar" aria-hidden="true">
                <Bot size={18} />
              </div>
              <div>
                <div className="va-name">Assistant</div>
                <div className="va-meta">{isSending ? "Working..." : "Ready"}</div>
              </div>
            </div>
            <div className="va-actions">
              <button className="va-icon-btn" type="button" title="New chat" aria-label="New chat" onClick={createNewChat}>
                <Plus size={16} />
              </button>
              <button className="va-icon-btn" type="button" title="Conversation history" aria-label="Conversation history" onClick={() => setUi((current) => ({ ...current, showHistory: !current.showHistory }))}>
                <History size={16} />
              </button>
              <button className="va-icon-btn" type="button" title="Copy last answer" aria-label="Copy last answer" onClick={copyLastAnswer}>
                <Copy size={16} />
              </button>
              <button className="va-icon-btn" type="button" title="Clear thread" aria-label="Clear thread" onClick={clearThread}>
                <Trash2 size={16} />
              </button>
              <button className="va-icon-btn" type="button" title="Minimize" aria-label="Minimize" onClick={closePanel}>
                <Minus size={16} />
              </button>
              <button className="va-icon-btn" type="button" title={ui.maximized ? "Restore" : "Maximize"} aria-label={ui.maximized ? "Restore" : "Maximize"} onClick={() => setUi((current) => ({ ...current, maximized: !current.maximized }))}>
                {ui.maximized ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              </button>
              <button className="va-icon-btn" type="button" title="Close" aria-label="Close" onClick={closePanel}>
                <X size={16} />
              </button>
            </div>
          </header>

          {ui.showHistory && (
            <div className="va-history">
              <div className="va-history-top">
                <strong>Chats</strong>
                <button className="va-draft-cancel-btn" type="button" onClick={createNewChat}>
                  New chat
                </button>
              </div>
              {conversations.map((conversation) => (
                <div className={conversation.id === currentId ? "va-history-item va-active" : "va-history-item"} key={conversation.id}>
                  <button className="va-history-select" type="button" onClick={() => setCurrentId(conversation.id)}>
                    {conversation.title}
                  </button>
                  <button className="va-icon-btn" type="button" title="Delete conversation" aria-label="Delete conversation" onClick={() => deleteConversation(conversation.id)}>
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="va-messages">
            {messages.map((message) => (
              <div className={message.role === "user" ? "va-message va-user" : "va-message va-assistant"} key={message.id}>
                <div className="va-bubble">
                  {(message.attachments ?? []).map((attachment) => (
                    <div className="va-attachment-line" key={attachment.id}>
                      {attachment.kind}: {attachment.name}
                    </div>
                  ))}
                  {message.role === "assistant" ? <FormattedMessageContent content={message.content} /> : message.content}
                  {message.pendingAction && (
                    <PendingDraftCard
                      action={message.pendingAction}
                      onConfirm={confirmPending}
                      onCancel={cancelPending}
                      disabled={isSending}
                      busyType={pendingActionBusy?.id === message.pendingAction.action_id ? pendingActionBusy.type : null}
                    />
                  )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          <footer className="va-footer">
            <div
              className="va-input-wrap"
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                handleFiles(Array.from(event.dataTransfer.files));
              }}
            >
              <textarea
                ref={inputRef}
                className="va-input"
                placeholder="Ask about replies, threads, drafts, queue..."
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submitMessage();
                  }
                }}
                onPaste={(event) => {
                  const files = Array.from(event.clipboardData.files ?? []);
                  if (files.length) handleFiles(files);
                }}
              />
              {pendingAttachments.length > 0 && (
                <div className="va-attachment-preview">
                  <span>{pendingAttachments.map((attachment) => attachment.name).join(", ")}</span>
                  <button className="va-icon-btn" type="button" aria-label="Remove attachments" title="Remove attachments" onClick={() => setPendingAttachments([])}>
                    <X size={14} />
                  </button>
                </div>
              )}
              {pendingAttachments.length > 0 && <div className="va-attachment-note">Only filename, type, and size are sent. File contents are not processed.</div>}
              <div className="va-composer-row">
                <select className="va-model-select" aria-label="Assistant model" value={model} onChange={(event) => setModel(event.target.value as AgentModel)}>
                  {AGENT_MODELS.map((option) => (
                    <option value={option.id} key={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <input ref={fileInputRef} type="file" hidden multiple accept="image/png,image/jpeg,image/webp,application/pdf,.pdf,text/plain,.txt,text/markdown,.md,text/csv,.csv,application/json,.json" onChange={(event) => handleFiles(Array.from(event.target.files ?? []))} />
                <button className="va-icon-btn" type="button" title="Attach files" aria-label="Attach files" onClick={() => fileInputRef.current?.click()}>
                  <Paperclip size={16} />
                </button>
                <button className={isListening ? "va-icon-btn va-listening" : "va-icon-btn"} type="button" title="Voice input" aria-label="Voice input" aria-pressed={isListening} onClick={() => (isListening ? stopVoice() : startVoice())}>
                  <Mic size={16} />
                </button>
                <button className={isSending ? "va-icon-btn va-sending" : "va-icon-btn"} type="button" title="Send" aria-label="Send assistant message" disabled={isSending || (!draft.trim() && pendingAttachments.length === 0)} onClick={() => void submitMessage()}>
                  <Send size={16} />
                </button>
              </div>
            </div>
          </footer>
        </section>
      )}

      <button className="va-launcher" type="button" aria-label="Open assistant" title="Open assistant" onClick={() => (ui.isOpen ? closePanel() : openPanel())}>
        {ui.unread > 0 && <span className="va-unread">{ui.unread > 9 ? "9+" : ui.unread}</span>}
        <Bot size={24} />
      </button>
    </div>
  );
}

function FormattedMessageContent({ content }: { content: string }) {
  const lines = content.split(/\r?\n/).filter((line) => line.trim());
  if (!lines.length) return null;

  const blocks: ReactNode[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const numbered = line.match(/^\s*\d+\.\s+(.+)/);
    const bulleted = line.match(/^\s*-\s+(.+)/);

    if (numbered || bulleted) {
      const listItems: { main: string; details: string[] }[] = [];
      const ordered = Boolean(numbered);
      while (index < lines.length) {
        const current = lines[index];
        const match = ordered ? current.match(/^\s*\d+\.\s+(.+)/) : current.match(/^\s*-\s+(.+)/);
        if (!match) break;
        const details: string[] = [];
        while (index + 1 < lines.length && /^\s{2,}\S/.test(lines[index + 1]) && !/^\s*(?:\d+\.|-)\s+/.test(lines[index + 1])) {
          index += 1;
          details.push(lines[index].trim());
        }
        listItems.push({ main: match[1], details });
        index += 1;
      }
      index -= 1;
      const Tag = ordered ? "ol" : "ul";
      blocks.push(
        <Tag className="va-message-list" key={`list-${blocks.length}`}>
          {listItems.map((item, itemIndex) => (
            <li key={`${item.main}-${itemIndex}`}>
              <span>
                <RichLine text={item.main} variant="list" />
              </span>
              {item.details.map((detail) => (
                <small key={detail}>
                  <RichLine text={detail} />
                </small>
              ))}
            </li>
          ))}
        </Tag>
      );
      continue;
    }

    blocks.push(
      <p className={blocks.length === 0 ? "va-message-lead" : undefined} key={`line-${blocks.length}`}>
        <RichLine text={line.trim()} variant={blocks.length === 0 ? "lead" : "body"} />
      </p>
    );
  }

  return <div className="va-formatted-message">{blocks}</div>;
}

function RichLine({ text, variant = "body" }: { text: string; variant?: "body" | "lead" | "list" }) {
  const clean = text.trim();
  const labelMatch = clean.match(/^([A-Za-z][A-Za-z /-]{1,38}):\s*(.+)$/);
  if (labelMatch) {
    return (
      <>
        <strong className="va-line-label">{labelMatch[1]}:</strong> {renderInlineText(labelMatch[2])}
      </>
    );
  }

  const contactMatch = clean.match(/^([^()<>:-][^()<>]{1,72}?)(\s+\(([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})\))(.*)$/);
  if (contactMatch) {
    return (
      <>
        <strong className="va-list-name">{contactMatch[1].trim()}</strong>{" "}
        <span className="va-inline-email">({contactMatch[3]})</span>
        {contactMatch[4] ? <span>{renderInlineText(contactMatch[4])}</span> : null}
      </>
    );
  }

  return <>{renderInlineText(clean)}</>;
}

function renderInlineText(text: string) {
  return text.split(/(\*\*[^*]+\*\*|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})/g).map((part, index) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) {
      return (
        <strong className="va-inline-bold" key={`${part}-${index}`}>
          {renderInlineText(part.slice(2, -2))}
        </strong>
      );
    }
    if (/^[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}$/.test(part)) {
      return (
        <span className="va-inline-email" key={`${part}-${index}`}>
          {part}
        </span>
      );
    }
    return part;
  });
}

function PendingDraftCard({ action, onConfirm, onCancel, disabled, busyType }: { action: PendingAction; onConfirm: (action: PendingAction) => void; onCancel: (action: PendingAction) => void; disabled: boolean; busyType: "confirm" | "cancel" | null }) {
  const isEmailDraft = action.capability === "email_send_draft" || action.to || action.subject || action.body;
  return (
    <div className="va-draft-card">
      {isEmailDraft ? (
        <>
          <div>
            <strong>To:</strong> {action.to}
          </div>
          <div>
            <strong>Subject:</strong> {action.subject}
          </div>
          <div className="va-draft-body">{(action.body || "").slice(0, 300)}</div>
        </>
      ) : (
        <>
          <div>
            <strong>Goal:</strong> {action.goal || action.confirmation_prompt}
          </div>
          <div>
            <strong>Capability:</strong> {action.capability}
          </div>
          {action.evidence_summary && (
            <div>
              <strong>Evidence:</strong> {action.evidence_summary}
            </div>
          )}
          {action.policy_result && (
            <div>
              <strong>Policy:</strong> {action.policy_result}
            </div>
          )}
          {action.proposed_side_effect && <div className="va-draft-body">{action.proposed_side_effect}</div>}
        </>
      )}
      <div className="va-draft-actions">
        <button className="va-draft-confirm-btn" type="button" disabled={disabled} onClick={() => onConfirm(action)}>
          {busyType === "confirm" ? "Confirming..." : isEmailDraft ? "Confirm Send" : "Confirm Action"}
        </button>
        <button className="va-draft-cancel-btn" type="button" disabled={disabled} onClick={() => onCancel(action)}>
          {busyType === "cancel" ? "Cancelling..." : "Cancel"}
        </button>
      </div>
    </div>
  );
}

function mimeFromName(name: string) {
  const lower = name.toLowerCase();
  if (lower.endsWith(".pdf")) return "application/pdf";
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".md")) return "text/markdown";
  if (lower.endsWith(".csv")) return "text/csv";
  if (lower.endsWith(".json")) return "application/json";
  return "text/plain";
}
