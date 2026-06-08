const API_URL = "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string> | undefined) ?? {})
  };
  const method = (init?.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers["X-Finimatic-CSRF"] = "1";
  if (init?.body !== undefined && !("Content-Type" in headers)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    credentials: "same-origin"
  });
  if (!response.ok) {
    throw new ApiError(await response.text(), response.status);
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
  getSecurityStatus: () => request<SecurityStatus>("/api/security/status"),
  getAuthSession: () => request<AuthSession>("/api/auth/session"),
  logout: () => request<{ authenticated: false }>("/api/auth/logout", { method: "POST" }),
  getSettings: () => request<SettingsRead>("/api/settings"),
  updateSettings: (payload: Record<string, unknown>) =>
    request<SettingsRead>("/api/settings", { method: "POST", body: JSON.stringify(payload) }),
  verifyEmail: () => request<{ readiness: string; error_code?: string }>("/api/settings/verify-email", { method: "POST" }),
  verifySmtp: () => request<{ readiness: string; error_code?: string }>("/api/settings/verify-smtp", { method: "POST" }),
  startGmailApiOAuth: (payload: { return_url?: string }) =>
    request<GmailApiOAuthStart>("/api/settings/gmail-api/oauth/start", { method: "POST", body: JSON.stringify(payload) }),
  sendCanary: () => request<CanaryResult>("/api/canary/send", { method: "POST" }),
  listProviderHealth: () => request<ListResponse<ProviderHealth>>("/api/provider-health"),
  getDeliverabilitySummary: () => request<DeliverabilitySummary>("/api/deliverability/summary"),
  runDeliverabilityCheck: () => request<DeliverabilitySummary>("/api/deliverability/check", { method: "POST" }),
  createInboxPlacementTest: (payload: { seed_email: string; subject?: string }) =>
    request<InboxPlacementTest>("/api/deliverability/inbox-placement-test", { method: "POST", body: JSON.stringify(payload) }),
  previewImport: (payload: Record<string, unknown>) =>
    request<ImportPreview>("/api/import/preview", { method: "POST", body: JSON.stringify(payload) }),
  commitImport: (batch_id_temp: string) =>
    request<ImportCommit>("/api/import/commit", { method: "POST", body: JSON.stringify({ batch_id_temp }) }),
  listContacts: () => request<ListResponse<Contact>>("/api/contacts"),
  listRecentlyDeletedContacts: () => request<ListResponse<Contact>>("/api/contacts/recently-deleted"),
  getEnrichmentSummary: () => request<EnrichmentSummary>("/api/enrichment/summary"),
  getEnrichmentWorkbook: () => request<EnrichmentWorkbook>("/api/enrichment/workbook"),
  getContactEvidence: (contactId: string) => request<ContactEvidence>(`/api/enrichment/contacts/${contactId}`),
  seedContactEvidence: (contactId: string) =>
    request<ListResponse<LeadFact>>(`/api/enrichment/contacts/${contactId}/seed`, { method: "POST" }),
  checkContactEvidence: (contactId: string, payload?: { draft_id?: string; subject?: string; body?: string }) =>
    request<EvidenceCheck>(`/api/enrichment/contacts/${contactId}/evidence-check`, {
      method: "POST",
      ...(payload ? { body: JSON.stringify(payload) } : {})
    }),
  listVerifications: () => request<ListResponse<EmailVerification> & { summary: VerificationSummary }>("/api/verification"),
  getContactVerification: (contactId: string) => request<ContactVerificationDetail>(`/api/verification/contact/${contactId}`),
  runContactVerification: (contactId: string) =>
    request<ContactVerificationDetail>(`/api/verification/contact/${contactId}/run`, { method: "POST" }),
  runSelectedVerification: (contact_ids: string[]) =>
    request<ListResponse<EmailVerification> & { summary: VerificationSummary }>("/api/verification/run-selected", { method: "POST", body: JSON.stringify({ contact_ids }) }),
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
    request<ApprovalResponse>(`/api/drafts/${id}/approve`, {
      method: "POST",
      ...(payload ? { body: JSON.stringify(payload) } : {})
    }),
  generateBulkDrafts: (payload: Record<string, unknown>) =>
    request<BulkJob>("/api/drafts/generate-bulk", { method: "POST", body: JSON.stringify(payload) }),
  getBulkDraftStatus: (jobId: string) => request<BulkJob>(`/api/drafts/bulk-status/${jobId}`),
  approveBulkDrafts: (draft_ids: string[]) =>
    request<BulkApprovalResponse>("/api/drafts/approve-bulk", { method: "POST", body: JSON.stringify({ draft_ids }) }),
  approveBulkDraftsAndSend: (draft_ids: string[]) =>
    request<BulkApprovalResponse>("/api/drafts/approve-bulk-and-send", { method: "POST", body: JSON.stringify({ draft_ids }) }),
  subjectVariants: (id: string) =>
    request<{ variants: string[]; error_code?: string }>(`/api/drafts/${id}/subject-variants`, { method: "POST" }),
  listTemplates: () => request<ListResponse<TemplateRow>>("/api/templates"),
  createTemplate: (payload: Record<string, unknown>) =>
    request<TemplateRow>("/api/templates", { method: "POST", body: JSON.stringify(payload) }),
  listQueue: () => request<ListResponse<QueueEntry>>("/api/queue"),
  clearQueue: () => request<QueueClearResult>("/api/queue", { method: "DELETE" }),
  processQueue: () => request<QueueProcessResult>("/api/queue/process", { method: "POST" }),
  sendQueueNow: (queueId: string) => request<QueueSendNowResult>(`/api/queue/${queueId}/send-now`, { method: "POST" }),
  retryQueue: (queueId: string) => request<QueueEntry>(`/api/queue/${queueId}/retry`, { method: "POST" }),
  cancelQueue: (queueId: string) => request<QueueEntry>(`/api/queue/${queueId}/cancel`, { method: "POST" }),
  deleteQueueEntry: (queueId: string) => request<QueueEntry>(`/api/queue/${queueId}`, { method: "DELETE" }),
  reconcileQueue: (queueId: string, action: "cancel" | "finalize_provider_accepted") =>
    request<QueueEntry>(`/api/queue/${queueId}/reconcile`, {
      method: "POST",
      body: JSON.stringify({ action })
    }),
  listFollowups: () => request<ListResponse<Followup>>("/api/followups"),
  processFollowups: () => request<{ processed: number; stopped: number; dispatched: number; skipped: number }>("/api/followups/process", { method: "POST" }),
  approveFollowupDraft: (id: string) => request<FollowupApprovalResponse>(`/api/followups/${id}/approve-draft`, { method: "POST" }),
  patchFollowup: (id: string, payload: Record<string, unknown>) =>
    request<Followup>(`/api/followups/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  listCampaigns: () => request<ListResponse<CampaignPlan>>("/api/campaigns"),
  createCampaign: (payload: Record<string, unknown>) =>
    request<CampaignPlan>("/api/campaigns", { method: "POST", body: JSON.stringify(payload) }),
  updateCampaign: (id: string, payload: Record<string, unknown>) =>
    request<CampaignPlan>(`/api/campaigns/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  activateCampaign: (id: string) =>
    request<{ status: string; contacts_count: number; drafts_created: number }>(`/api/campaigns/${id}/activate`, { method: "POST" }),
  listWorkflows: () => request<ListResponse<WorkflowSummary>>("/api/workflows"),
  getWorkflow: (id: string) => request<WorkflowDetail>(`/api/workflows/${id}`),
  runWorkflow: (id: string, payload?: WorkflowRunRequest) =>
    request<WorkflowRun>(`/api/workflows/${id}/run`, { method: "POST", body: payload ? JSON.stringify(payload) : undefined }),
  retryWorkflow: (id: string, payload?: WorkflowRunRequest) =>
    request<WorkflowRun>(`/api/workflows/${id}/retry-failed`, { method: "POST", body: payload ? JSON.stringify(payload) : undefined }),
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
    request<GovernedActionResponse>(`/api/conversations/${contactId}/send`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  confirmConversationReply: (contactId: string, payload: Record<string, unknown>) =>
    request<{ status: string; message?: ConversationMessage; provider_msg_id?: string; error_code?: string }>(`/api/conversations/${contactId}/confirm-send`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  listGovernedActions: (sessionToken: string) =>
    request<ListResponse<GovernedAction>>(`/api/governed-actions/pending${queryString({ session_token: sessionToken })}`),
  cancelGovernedAction: (actionId: string, sessionToken: string) =>
    request<{ status: string; action_id: string }>(`/api/governed-actions/${actionId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ session_token: sessionToken })
    }),
  listAutoReplyPending: () => request<ListResponse<AutoReplyPending>>("/api/auto-reply/pending"),
  listAutoReplyLog: () => request<ListResponse<AuditEvent>>("/api/auto-reply/log"),
  approveAutoReply: (draftId: string, sessionToken: string) =>
    request<GovernedActionResponse>(`/api/auto-reply/approve/${draftId}`, {
      method: "POST",
      body: JSON.stringify({ session_token: sessionToken })
    }),
  confirmAutoReply: (draftId: string, actionId: string, sessionToken: string) =>
    request<{ status: string; message_id?: string; reason?: string }>(`/api/auto-reply/confirm/${draftId}`, {
      method: "POST",
      body: JSON.stringify({ session_token: sessionToken, action_id: actionId })
    }),
  prepareAutonomousAutoReply: (sessionToken: string) =>
    request<GovernedActionResponse>("/api/auto-reply/autonomous/prepare", {
      method: "POST",
      body: JSON.stringify({ session_token: sessionToken })
    }),
  confirmAutonomousAutoReply: (actionId: string, sessionToken: string) =>
    request<{ status: string }>("/api/auto-reply/autonomous/confirm", {
      method: "POST",
      body: JSON.stringify({ session_token: sessionToken, action_id: actionId })
    }),
  killAutoReply: () => request<{ status: string }>("/api/auto-reply/kill", { method: "POST" }),
  rejectAutoReply: (draftId: string) => request<{ status: string; draft?: Draft }>(`/api/auto-reply/reject/${draftId}`, { method: "POST" }),
  listIntegrations: () => request<IntegrationsSummary>("/api/integrations"),
  previewIntegrationSync: (provider: string, contact_id: string) =>
    request<ExternalWritePreview>(`/api/integrations/${provider}/preview-sync`, { method: "POST", body: JSON.stringify({ contact_id }) }),
  confirmIntegrationSync: (provider: string, preview_id: string) =>
    request<SyncJournal>(`/api/integrations/${provider}/confirm-sync`, { method: "POST", body: JSON.stringify({ preview_id }) }),
  cancelIntegrationSync: (provider: string, preview_id: string) =>
    request<ExternalWritePreview>(`/api/integrations/${provider}/cancel-sync`, { method: "POST", body: JSON.stringify({ preview_id }) }),
  listAudit: () => request<ListResponse<AuditEvent>>("/api/audit")
};

export type SecurityStatus = {
  checker_configured: boolean;
  authentication_enforced: boolean;
  authorization_enforced: boolean;
  interactive_login_configured: boolean;
  session_authentication_enabled: boolean;
  mode: string;
  release_blocked: boolean;
  release_block_reason?: string | null;
};

export type AuthSession = {
  authenticated: boolean;
  authorized: boolean;
  interactive_login_configured: boolean;
  subject: string | null;
  roles: string[];
};

export function authenticationLoginUrl(returnPath = "/") {
  return `/api/auth/login?${new URLSearchParams({ return_path: returnPath }).toString()}`;
}

export type SettingsRead = {
  gmail_user: string;
  email_transport: "smtp" | "gmail_api";
  configured_transport: string;
  effective_transport: string;
  transport_source: string;
  transport_simulated: boolean;
  transport_mismatch: boolean;
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
  auto_process_effective: boolean;
  auto_process_block_reason?: string | null;
  scheduler_enabled: boolean;
  hosting_mode?: string;
  automation_reliability?: string;
  smtp_available?: boolean;
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
  auto_reply_autonomous_authorized: boolean;
  auto_reply_kill_switch: boolean;
  auto_reply_daily_cap: number;
  auto_reply_min_gap_minutes: number;
  auto_reply_safe_intents: string;
  dry_run: boolean;
  canary_verified: boolean;
  sender_readiness: string;
  mode: "DRY-RUN" | "CANARY" | "LIVE";
  api_security_mode: string;
  api_security_enforced: boolean;
  release_blocked: boolean;
  release_block_reason?: string | null;
};

export type SendOutcome = {
  status: string;
  attempt_status: string;
  configured_transport: string;
  effective_transport: string;
  transport_source: string;
  simulated: boolean;
  provider_contacted: boolean;
  provider_accepted: boolean;
  provider_message_id?: string | null;
  tracking_message_id?: string | null;
  provider_response_classification?: string | null;
  error_code?: string | null;
  error_detail_redacted?: string | null;
  idempotency_key?: string | null;
  attempt_id?: string | null;
};

export type CanaryResult = Partial<SendOutcome> & {
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
export type BulkGenerationMode = "ai" | "template_fill" | "template_ai";
export type BulkDraftMode = "ai_only" | "template_only" | "template_plus_ai";
export type BulkJobResult = {
  contact_id: string;
  email?: string;
  status: string;
  mode: BulkGenerationMode | BulkDraftMode;
  draft_id?: string;
  action?: "created" | "updated";
  reason?: string;
  provider?: string;
};
export type BulkJob = {
  job_id: string;
  status: string;
  total: number;
  completed: number;
  generated: number;
  created?: number;
  updated?: number;
  failed: number;
  skipped: number;
  mode?: BulkGenerationMode | BulkDraftMode;
  errors?: string[];
  results?: BulkJobResult[];
};
export type TemplateRow = { id: string; name: string; subject_template: string; body_template: string; created_at?: string | null };
export type QueueProcessResult = {
  processed: number;
  eligible_count?: number;
  provider_accepted: number;
  sent: number;
  blocked: number;
  simulated: number;
  skipped: number;
  failed: number;
  reconciliation_required: number;
  deferred?: number;
  policy_rescheduled?: number;
  future_scheduled_count?: number;
  next_due_at?: string | null;
  blocked_reasons?: Record<string, number>;
  scheduler_effective?: boolean;
  zero_work_reason?: string | null;
};
export type QueueClearResult = {
  cancelled: number;
  already_cancelled: number;
  preserved_accepted: number;
  preserved_uncertain: number;
  skipped: number;
};
export type BulkApprovalItem = {
  draft_id: string;
  status: string;
  queue_id?: string;
  reason?: unknown;
};
export type BulkApprovalResponse = QueueProcessResult & {
  selected: number;
  approved: number;
  queued: number;
  dispatch_requested: boolean;
  items: BulkApprovalItem[];
};
export type DeliveryStatus = "provider_accepted" | "sent" | "deferred" | "blocked" | "simulated" | "dry_run_blocked" | "reconciliation_required" | "queued_for_bulk" | "queued" | "failed" | string;
export type PolicyTrace = { gate: string; passed: boolean; reason_code?: string | null; details?: Record<string, unknown> };
export type QueueEntry = {
  id: string;
  contact_id: string;
  contact_email?: string | null;
  contact_name?: string | null;
  draft_id: string;
  draft_subject?: string | null;
  sequence_num: number;
  status: string;
  schedule_source?: string;
  stored_status?: string | null;
  classification_note?: string | null;
  policy_block_reasons: string[];
  policy_trace?: PolicyTrace[];
  latest_attempt?: SendOutcome | null;
  scheduled_at: string;
  last_attempt_status?: string | null;
  last_attempt_error_code?: string | null;
  last_attempt_error_detail?: string | null;
};
export type ApprovalResponse = Draft & {
  queue_id?: string | null;
  queue?: QueueEntry | null;
  delivery_status?: DeliveryStatus;
  delivery_result?: QueueProcessResult;
};
export type FollowupApprovalResponse = {
  status: string;
  queue_id: string;
  queue?: QueueEntry | null;
  delivery_status?: DeliveryStatus;
  delivery_result?: QueueProcessResult;
};
export type QueueSendNowResult = {
  eligible: boolean;
  reasons: string[];
  queue?: QueueEntry | null;
  result: QueueProcessResult;
  delivery_status: DeliveryStatus;
};
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
export type GovernedAction = {
  action_id: string;
  capability: string;
  entity_type?: string;
  entity_id?: string;
  contact_id?: string;
  draft_id?: string;
  to?: string;
  subject?: string;
  body?: string;
  goal?: string;
  evidence_summary?: string;
  policy_result?: string;
  proposed_side_effect?: string;
  activation_details?: {
    mailbox?: string;
    daily_cap?: number;
    minimum_gap_minutes?: number;
    safe_intents?: string;
    stop_conditions?: string;
  };
  confirmation_prompt: string;
  expires_at: string;
  consumed?: boolean;
};
export type GovernedActionResponse = { status: "pending_confirmation"; pending_action: GovernedAction };
export type AuditEvent = {
  id: string;
  event_type: string;
  entity_type?: string;
  entity_id?: string;
  payload: Record<string, unknown>;
  created_at: string;
  event_label?: string | null;
  entity_label?: string | null;
  contact_name?: string | null;
  contact_email?: string | null;
  detail?: string | null;
};
export type ProviderHealth = { id: string; provider: string; status: string; last_checked?: string | null; error_code?: string | null; details?: string | null };
export type AutoReplyPending = { id: string; contact_id: string; contact_name?: string; contact_email?: string; their_reply: string; subject: string; body: string; generated_at?: string | null };
export type ImportPreview = { batch_id_temp: string; rows: ImportRow[]; summary: Record<string, number> };
export type ImportCommit = { batch_id: string; rows: ImportRow[]; summary: Record<string, number>; contact_ids?: string[] };
export type ImportRow = { row_num: number; email: string; status: string; reason?: string; parsed_data: Record<string, unknown> };
export type EvidenceSource = { id: string; url?: string | null; publisher?: string | null; source_type: string; reliability_tier: string; allowed_use: string; retrieved_at?: string | null; raw_excerpt_redacted?: string | null };
export type LeadFact = { id: string; contact_id: string; field_key: string; field_value: string; source_id?: string | null; source_label: string; source_url?: string | null; source_type?: string | null; confidence: number; status: string; freshness?: string; usable?: boolean; fetched_at?: string | null; expires_at?: string | null; raw_snippet_redacted?: string | null; source?: EvidenceSource | null };
export type AccountFact = { id: string; contact_id?: string | null; field_key: string; field_value: string; source_id?: string | null; source_label: string; source_url?: string | null; confidence: number; status: string; freshness?: string; usable?: boolean; fetched_at?: string | null; expires_at?: string | null; raw_snippet_redacted?: string | null; source?: EvidenceSource | null };
export type EvidenceCheck = { contact_id?: string | null; status: string; supported_claims: LeadFact[]; missing_evidence: string[]; stale_facts: LeadFact[]; neutral_copy_required: boolean; neutral_subject?: string | null; neutral_body?: string | null; policy_version?: string | null };
export type ContactEvidence = { contact_id: string; contact_email: string; lead_facts: LeadFact[]; account_facts: AccountFact[]; evidence_check: EvidenceCheck };
export type EnrichmentSummary = { contacts: number; contacts_with_evidence: number; lead_facts: number; evidence_sources: number; stale_facts: number };
export type EnrichmentWorkbookRow = { contact_id: string; email: string; name: string; company?: string | null; website?: string | null; evidence_status: string; supported_claims: number; stale_facts: number; draft_readiness: string };
export type EnrichmentWorkbook = { items: EnrichmentWorkbookRow[]; total: number; summary: EnrichmentSummary };
export type EmailVerification = { id?: string | null; contact_id?: string | null; email: string; status: string; confidence: number; provider: string; provider_status?: string | null; is_role_based: boolean; is_disposable: boolean; is_catch_all: boolean; mx_present: boolean; last_verified_at?: string | null; expires_at?: string | null; raw_response_redacted?: string | null };
export type EmailVerificationAttempt = { id: string; provider: string; status: string; response_code?: string | null; latency_ms: number; cost_units: number; raw_response_redacted?: string | null; created_at?: string | null };
export type VerificationSummary = { total_contacts: number; verified_or_valid: number; warnings: number; blocked: number; unchecked: number; counts: Record<string, number> };
export type ContactVerificationDetail = { verification: EmailVerification; attempts: EmailVerificationAttempt[]; policy: { status: string; severity: string; reason_code?: string | null; allowed: boolean } };
export type DeliverabilityPolicyCheck = { allowed: boolean; reason_code?: string | null; status?: string; [key: string]: unknown };
export type DeliverabilitySummary = { sender: string; domain: string; mailbox?: SenderMailbox | null; domain_health: SenderDomainHealth; checks: DeliverabilityCheck[]; inbox_tests: InboxPlacementTest[]; recipient_domain_caps: RecipientDomainCap[]; policy: { allowed?: boolean; reasons?: string[]; checks?: Record<string, DeliverabilityPolicyCheck> } };
export type SenderMailbox = { id: string; email: string; domain: string; provider: string; transport: string; status: string; daily_cap: number; hourly_cap: number; min_delay_s: number; ramp_stage: string; last_health_check_at?: string | null };
export type SenderDomainHealth = { id?: string | null; domain: string; spf_status: string; dkim_status: string; dmarc_status: string; alignment_status: string; postmaster_status: string; spam_rate_bucket: string; reputation_bucket: string; last_checked_at?: string | null };
export type DeliverabilityCheck = { id: string; sender_email?: string | null; domain?: string | null; check_type: string; status: string; severity: string; details_redacted?: string | null; checked_at?: string | null };
export type InboxPlacementTest = { id: string; sender_email: string; seed_email: string; recipient_domain: string; subject: string; status: string; placement: string; provider_msg_id?: string | null; sent_at?: string | null; checked_at?: string | null; details_redacted?: string | null };
export type RecipientDomainCap = { id: string; sender_email: string; recipient_domain: string; daily_cap: number; sent_today: number; last_sent_at?: string | null; window_date: string; status: string };
export type WorkflowSummary = { id: string; name: string; description?: string | null; status: string };
export type WorkflowColumn = { id: string; key: string; label: string; step_type: string; position: number };
export type WorkflowRunRequest = { cost_cap_units?: number | null };
export type WorkflowCell = { status: string; output?: Record<string, unknown> | null; evidence_refs: string[]; cost_units: number; input_hash?: string | null; step_config_hash?: string | null; created_at?: string | null };
export type WorkflowRow = { id: string; contact_id?: string | null; email: string; name: string; status: string; cells: Record<string, WorkflowCell> };
export type WorkflowStep = { id: string; run_id: string; column_id?: string | null; column_key?: string | null; step_type: string; position: number; status: string; config_hash: string; created_at?: string | null };
export type WorkflowAttempt = { id: string; run_id?: string | null; step_type?: string | null; column_key?: string | null; column_label?: string | null; row_id?: string | null; contact_email?: string | null; status: string; attempt_num: number; input_hash: string; step_config_hash: string; latency_ms: number; cost_units: number; error_code?: string | null; created_at?: string | null };
export type WorkflowRun = { id: string; workbook_id: string; status: string; started_at?: string | null; completed_at?: string | null; created_by: string; created_at?: string | null; total_cost_units: number; steps: WorkflowStep[] };
export type WorkflowDetail = { id: string; name: string; description?: string | null; status: string; columns: WorkflowColumn[]; rows: WorkflowRow[]; runs: WorkflowRun[]; attempts: WorkflowAttempt[] };
export type IntegrationConnection = { id: string; provider: string; account_label: string; status: string; auth_mode: string; scopes_redacted?: string | null; last_checked_at?: string | null };
export type IntegrationMapping = { id: string; provider: string; connection_id: string; local_field: string; external_field: string; direction: string; status: string; created_at?: string | null };
export type ExternalWritePreview = { id: string; provider: string; entity_type: string; entity_id: string; action: string; diff: { fields?: Array<{ field?: string; local_field?: string; external_field?: string; before: unknown; after: unknown; status: string }>; policy?: Record<string, unknown>; connection?: Record<string, unknown>; object_type?: string; provider_label?: string }; idempotency_key: string; status: string; expires_at?: string | null; created_at?: string | null };
export type SyncJournal = { id: string; provider: string; connection_id?: string | null; entity_type: string; entity_id: string; external_id?: string | null; action: string; status: string; idempotency_key: string; diff: Record<string, unknown>; created_at?: string | null };
export type ExternalWriteAttempt = { id: string; preview_id?: string | null; provider: string; status: string; response_code?: string | null; external_id?: string | null; idempotency_key: string; error_code?: string | null; details?: Record<string, unknown>; created_at?: string | null };
export type IntegrationsSummary = { connections: IntegrationConnection[]; mappings: IntegrationMapping[]; previews: ExternalWritePreview[]; journals: SyncJournal[]; attempts: ExternalWriteAttempt[] };
