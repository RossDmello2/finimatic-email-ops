const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string> | undefined) ?? {})
  };
  if (init?.body !== undefined && !("Content-Type" in headers)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

function queryString(params?: Record<string, string | boolean | undefined>) {
  if (!params) return "";
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
}

export const api = {
  getSettings: () => request<SettingsRead>("/api/settings"),
  updateSettings: (payload: Record<string, unknown>) =>
    request<SettingsRead>("/api/settings", { method: "POST", body: JSON.stringify(payload) }),
  verifyEmail: () => request<{ readiness: string; error_code?: string }>("/api/settings/verify-email", { method: "POST" }),
  verifySmtp: () => request<{ readiness: string; error_code?: string }>("/api/settings/verify-smtp", { method: "POST" }),
  startGmailApiOAuth: (payload: { return_url?: string }) =>
    request<GmailApiOAuthStart>("/api/settings/gmail-api/oauth/start", { method: "POST", body: JSON.stringify(payload) }),
  sendCanary: () => request<CanaryResult>("/api/canary/send", { method: "POST" }),
  listProviderHealth: () => request<ListResponse<ProviderHealth>>("/api/provider-health"),
  previewImport: (payload: Record<string, unknown>) =>
    request<ImportPreview>("/api/import/preview", { method: "POST", body: JSON.stringify(payload) }),
  commitImport: (batch_id_temp: string) =>
    request<ImportCommit>("/api/import/commit", { method: "POST", body: JSON.stringify({ batch_id_temp }) }),
  listContacts: () => request<ListResponse<Contact>>("/api/contacts"),
  listRecentlyDeletedContacts: () => request<ListResponse<Contact>>("/api/contacts/recently-deleted"),
  createContact: (payload: Record<string, unknown>) =>
    request<Contact>("/api/contacts", { method: "POST", body: JSON.stringify(payload) }),
  patchContact: (id: string, payload: Record<string, unknown>) =>
    request<Contact>(`/api/contacts/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteContact: (id: string) => request<Contact>(`/api/contacts/${id}`, { method: "DELETE" }),
  restoreContact: (id: string) => request<Contact>(`/api/contacts/${id}/restore`, { method: "POST" }),
  listDrafts: () => request<ListResponse<Draft>>("/api/drafts"),
  createDraft: (payload: Record<string, unknown>) =>
    request<Draft>("/api/drafts", { method: "POST", body: JSON.stringify(payload) }),
  generateDraft: (payload: Record<string, unknown>) =>
    request<Draft>("/api/drafts/generate", { method: "POST", body: JSON.stringify(payload) }),
  updateDraft: (id: string, payload: Record<string, unknown>) =>
    request<Draft>(`/api/drafts/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  approveDraft: (id: string, payload?: { sequence_num?: number }) =>
    request<Draft & { queue_id: string }>(`/api/drafts/${id}/approve`, {
      method: "POST",
      ...(payload ? { body: JSON.stringify(payload) } : {})
    }),
  generateBulkDrafts: (payload: Record<string, unknown>) =>
    request<BulkJob>("/api/drafts/generate-bulk", { method: "POST", body: JSON.stringify(payload) }),
  getBulkDraftStatus: (jobId: string) => request<BulkJob>(`/api/drafts/bulk-status/${jobId}`),
  approveBulkDrafts: (draft_ids: string[]) =>
    request<{ approved: number; queued: number }>("/api/drafts/approve-bulk", { method: "POST", body: JSON.stringify({ draft_ids }) }),
  subjectVariants: (id: string) =>
    request<{ variants: string[]; error_code?: string }>(`/api/drafts/${id}/subject-variants`, { method: "POST" }),
  listTemplates: () => request<ListResponse<TemplateRow>>("/api/templates"),
  createTemplate: (payload: Record<string, unknown>) =>
    request<TemplateRow>("/api/templates", { method: "POST", body: JSON.stringify(payload) }),
  listQueue: () => request<ListResponse<QueueEntry>>("/api/queue"),
  processQueue: () => request<{ processed: number; sent: number; blocked: number; skipped: number; deferred?: number }>("/api/queue/process", { method: "POST" }),
  listFollowups: () => request<ListResponse<Followup>>("/api/followups"),
  processFollowups: () => request<{ processed: number; stopped: number; dispatched: number; skipped: number }>("/api/followups/process", { method: "POST" }),
  approveFollowupDraft: (id: string) => request<{ status: string; queue_id: string }>(`/api/followups/${id}/approve-draft`, { method: "POST" }),
  patchFollowup: (id: string, payload: Record<string, unknown>) =>
    request<Followup>(`/api/followups/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  listCampaigns: () => request<ListResponse<CampaignPlan>>("/api/campaigns"),
  createCampaign: (payload: Record<string, unknown>) =>
    request<CampaignPlan>("/api/campaigns", { method: "POST", body: JSON.stringify(payload) }),
  updateCampaign: (id: string, payload: Record<string, unknown>) =>
    request<CampaignPlan>(`/api/campaigns/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  activateCampaign: (id: string) =>
    request<{ status: string; contacts_count: number; drafts_created: number }>(`/api/campaigns/${id}/activate`, { method: "POST" }),
  listSuppressions: () => request<ListResponse<Suppression>>("/api/suppressions"),
  addSuppression: (payload: Record<string, unknown>) =>
    request<Suppression>("/api/suppressions", { method: "POST", body: JSON.stringify(payload) }),
  deleteSuppression: (id: string) => request<{ deleted: boolean; id: string }>(`/api/suppressions/${id}`, { method: "DELETE" }),
  listReplies: (params?: { include_archived?: boolean; archived_only?: boolean; contact_id?: string; classified_as?: string }) =>
    request<ListResponse<ReplyRow>>(`/api/replies${queryString(params)}`),
  addReply: (payload: Record<string, unknown>) =>
    request<ReplyRow>("/api/replies", { method: "POST", body: JSON.stringify(payload) }),
  archiveReply: (id: string) => request<ReplyRow>(`/api/replies/${id}/archive`, { method: "POST" }),
  restoreReply: (id: string) => request<ReplyRow>(`/api/replies/${id}/restore`, { method: "POST" }),
  deleteReply: (id: string) => request<{ deleted: boolean; id: string }>(`/api/replies/${id}`, { method: "DELETE" }),
  fetchReplies: () =>
    request<{ checked: number; matched: number; inserted: number; duplicates: number; error_code?: string }>("/api/replies/fetch", { method: "POST" }),
  listConversations: () => request<ListResponse<ConversationSummary>>("/api/conversations"),
  getConversation: (contactId: string) => request<ConversationDetail>(`/api/conversations/${contactId}`),
  generateConversationReply: (contactId: string, payload: Record<string, unknown>) =>
    request<ConversationDraft>(`/api/conversations/${contactId}/generate-reply`, { method: "POST", body: JSON.stringify(payload) }),
  sendConversationReply: (contactId: string, payload: Record<string, unknown>) =>
    request<{ status: string; message?: ConversationMessage; provider_msg_id?: string; error_code?: string }>(`/api/conversations/${contactId}/send`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  listAutoReplyPending: () => request<ListResponse<AutoReplyPending>>("/api/auto-reply/pending"),
  listAutoReplyLog: () => request<ListResponse<AuditEvent>>("/api/auto-reply/log"),
  approveAutoReply: (draftId: string) => request<{ status: string; message_id?: string }>(`/api/auto-reply/approve/${draftId}`, { method: "POST" }),
  rejectAutoReply: (draftId: string) => request<{ status: string; draft?: Draft }>(`/api/auto-reply/reject/${draftId}`, { method: "POST" }),
  listAudit: () => request<ListResponse<AuditEvent>>("/api/audit")
};

export type SettingsRead = {
  gmail_user: string;
  email_transport: "smtp" | "gmail_api";
  gmail_app_password_configured: boolean;
  gmail_api_configured: boolean;
  report_recipient: string;
  groq_keys_count: number;
  groq_keys_fingerprints: string[];
  gemini_keys_count: number;
  gemini_keys_fingerprints: string[];
  daily_send_cap: number;
  hourly_send_cap: number;
  send_delay_s: number;
  auto_process_enabled: boolean;
  auto_process_queue_interval_seconds: number;
  auto_process_followup_interval_seconds: number;
  followup_interval_days: number;
  max_followups_per_lead: number;
  campaign_context: string;
  sender_name?: string;
  sender_role?: string;
  sender_offer?: string;
  sender_tone?: string;
  sender_signature?: string;
  groq_model?: string;
  gemini_model?: string;
  follow_up_template_1?: string;
  follow_up_template_2?: string;
  blocked_domains?: string;
  send_window_start?: string;
  send_window_end?: string;
  send_timezone?: string;
  warm_up_mode?: boolean;
  warm_up_start_date?: string;
  warm_up_current_limit?: number;
  imap_fetch_interval_minutes: number;
  auto_reply_enabled: boolean;
  auto_reply_mode: "propose" | "autonomous";
  auto_reply_daily_cap: number;
  auto_reply_min_gap_minutes: number;
  auto_reply_safe_intents: string;
  dry_run: boolean;
  canary_verified: boolean;
  sender_readiness: string;
  mode: "DRY-RUN" | "CANARY" | "LIVE";
};

export type CanaryResult = {
  status: string;
  nonce?: string;
  sent_at?: string;
  sender_identity?: string;
  message_id?: string;
  previous_attempt_id?: string;
};

export type GmailApiOAuthStart = {
  authorization_url: string;
  redirect_uri: string;
  scopes: string[];
};

export type ListResponse<T> = { items: T[]; total: number };
export type Contact = {
  id: string;
  email: string;
  creator_name?: string;
  business_name?: string;
  website_url?: string;
  notes?: string;
  personalization?: string;
  lead_category?: string;
  custom_fields?: { tags?: string[]; [key: string]: unknown };
  auto_reply_override?: "enabled" | "disabled" | "propose" | null;
  status: string;
  source: string;
  deleted_at?: string | null;
};
export type Draft = { id: string; contact_id: string; subject: string; body: string; warnings: string[]; source?: string | null; rejected?: boolean; approved: boolean; approved_at?: string | null; ai_provider?: string | null; ai_model?: string | null; error_code?: string | null };
export type BulkJob = { job_id: string; status: string; total: number; completed: number; generated: number; failed: number; skipped: number; errors?: string[] };
export type TemplateRow = { id: string; name: string; subject_template: string; body_template: string; created_at?: string | null };
export type QueueEntry = { id: string; contact_id: string; contact_email?: string | null; contact_name?: string | null; draft_id: string; draft_subject?: string | null; sequence_num: number; status: string; policy_block_reasons: string[]; scheduled_at: string };
export type PendingFollowupDraft = { id: string; subject: string; body: string; approved: boolean };
export type Followup = { id: string; contact_id: string; contact_email?: string | null; contact_name?: string | null; sequence_num: number; status: string; stop_reason?: string | null; due_at: string; draft_id?: string | null; pending_draft_id?: string | null; pending_draft?: PendingFollowupDraft | null };
export type Suppression = { id: string; email: string; reason: string; source?: string };
export type ReplyRow = { id: string; contact_id: string; contact_email?: string | null; received_at?: string | null; classified_as: string; intent?: string | null; raw_summary?: string; archived_at?: string | null };
export type CampaignStep = { subject: string; body: string; purpose: string };
export type CampaignPlan = { id: string; name: string; goal?: string | null; target_tags?: string | null; step_1_draft: CampaignStep; step_2_draft: CampaignStep; step_3_draft: CampaignStep; status: string; contacts_count: number; sent_count: number; stopped_count: number; created_at?: string | null; updated_at?: string | null };
export type ConversationSummary = { contact_id: string; email: string; name: string; status: string; inbound: number; outbound: number; last_message_at?: string | null; last_direction?: string | null; last_subject?: string | null };
export type ConversationMessage = { id: string; contact_id: string; direction: "inbound" | "outbound"; subject?: string | null; body: string; source: string; auto_sent?: boolean; external_message_id?: string | null; occurred_at?: string | null; created_at?: string | null };
export type ConversationDetail = { contact: ConversationSummary; messages: ConversationMessage[] };
export type ConversationDraft = { subject: string; body: string; reasoning_summary?: string; provider: string; model?: string };
export type AuditEvent = { id: string; event_type: string; entity_type?: string; entity_id?: string; payload: Record<string, unknown>; created_at: string };
export type ProviderHealth = { id: string; provider: string; status: string; last_checked?: string | null; error_code?: string | null; details?: string | null };
export type AutoReplyPending = { id: string; contact_id: string; contact_name?: string; contact_email?: string; their_reply: string; subject: string; body: string; generated_at?: string | null };
export type ImportPreview = { batch_id_temp: string; rows: ImportRow[]; summary: Record<string, number> };
export type ImportCommit = { batch_id: string; rows: ImportRow[]; summary: Record<string, number>; contact_ids?: string[] };
export type ImportRow = { row_num: number; email: string; status: string; reason?: string; parsed_data: Record<string, unknown> };
