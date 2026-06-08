import { Fragment, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  Archive,
  ArchiveX,
  Bot,
  Check,
  ChevronRight,
  Database,
  FileUp,
  HeartPulse,
  Inbox,
  KeyRound,
  Library,
  ListChecks,
  LogIn,
  LogOut,
  Mail,
  Megaphone,
  MessageSquare,
  PauseCircle,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Trash2,
  Users,
  X
} from "lucide-react";
import { toast } from "sonner";
import {
  api,
  AuditEvent,
  AutoReplyPending,
  BulkJob,
  CampaignPlan,
  CampaignStep,
  Contact,
  ContactEvidence,
  ConversationDraft,
  ConversationSummary,
  DeliverabilitySummary,
  Draft,
  AccountFact,
  EmailVerification,
  EnrichmentWorkbook,
  EvidenceCheck,
  ExternalWritePreview,
  Followup,
  FollowupApprovalResponse,
  GovernedAction,
  ImportCommit,
  ImportPreview,
  IntegrationsSummary,
  LeadFact,
  QueueEntry,
  QueueProcessResult,
  ReplyRow,
  SecurityStatus,
  SettingsRead,
  Suppression,
  TemplateRow,
  ProviderHealth,
  ApprovalResponse,
  BulkApprovalResponse,
  AuthSession,
  VerificationSummary,
  WorkflowDetail,
  WorkflowSummary,
  authenticationLoginUrl
} from "./api/client";
import { AssistantWidget } from "./features/floating-assistant/AssistantWidget";
import { getSessionToken } from "./features/floating-assistant/assistantApi";

const surfaces = [
  ["setup", "Setup", ShieldCheck],
  ["health", "Provider Health", HeartPulse],
  ["deliverability", "Deliverability", ShieldCheck],
  ["import", "Import", FileUp],
  ["contacts", "Contacts", Users],
  ["enrichment", "Enrichment", Search],
  ["drafts", "Drafts", Sparkles],
  ["templates", "Templates", Library],
  ["campaigns", "Campaigns", Megaphone],
  ["workflows", "Workflows", Activity],
  ["queue", "Queue", ListChecks],
  ["followups", "Follow-ups", RefreshCw],
  ["replies", "Replies/Stops", Inbox],
  ["conversations", "Conversations", MessageSquare],
  ["autoReply", "Auto-Reply", Bot],
  ["suppressions", "Suppressions", ArchiveX],
  ["audit", "Audit Logs", Database],
  ["errors", "Errors", AlertTriangle],
  ["integrations", "Integrations", KeyRound],
  ["settings", "Settings", Settings]
] as const;

type Surface = (typeof surfaces)[number][0];
type BulkDraftMode = "ai_only" | "template_only" | "template_plus_ai";

function useAppData(surface: Surface, operationalEnabled: boolean) {
  const repliesVisible = surface === "replies";
  const conversationsVisible = surface === "conversations";
  const autoReplyVisible = surface === "autoReply";
  const deliverabilityVisible = surface === "deliverability";
  const enrichmentVisible = surface === "enrichment";
  const verificationVisible = ["contacts", "enrichment", "drafts", "queue", "deliverability"].includes(surface);
  const workflowsVisible = surface === "workflows";
  const integrationsVisible = surface === "integrations";
  const providerHealth = useQuery({
    queryKey: ["provider-health"],
    queryFn: api.listProviderHealth,
    enabled: operationalEnabled,
    refetchInterval: operationalEnabled ? 30000 : false
  });
  return {
    settings: useQuery({ queryKey: ["settings"], queryFn: api.getSettings, enabled: operationalEnabled, retry: false }),
    providerHealth,
    deliverability: useQuery({ queryKey: ["deliverability"], queryFn: api.getDeliverabilitySummary, enabled: operationalEnabled && deliverabilityVisible }),
    contacts: useQuery({ queryKey: ["contacts"], queryFn: api.listContacts, enabled: operationalEnabled }),
    recentlyDeletedContacts: useQuery({ queryKey: ["contacts", "recently-deleted"], queryFn: api.listRecentlyDeletedContacts, enabled: operationalEnabled }),
    enrichmentWorkbook: useQuery({ queryKey: ["enrichment-workbook"], queryFn: api.getEnrichmentWorkbook, enabled: operationalEnabled && enrichmentVisible }),
    verifications: useQuery({ queryKey: ["verifications"], queryFn: api.listVerifications, enabled: operationalEnabled && verificationVisible }),
    drafts: useQuery({ queryKey: ["drafts"], queryFn: api.listDrafts, enabled: operationalEnabled }),
    queue: useQuery({ queryKey: ["queue"], queryFn: api.listQueue, enabled: operationalEnabled }),
    followups: useQuery({ queryKey: ["followups"], queryFn: api.listFollowups, enabled: operationalEnabled }),
    suppressions: useQuery({ queryKey: ["suppressions"], queryFn: api.listSuppressions, enabled: operationalEnabled }),
    replies: useQuery({ queryKey: ["replies"], queryFn: () => api.listReplies({ include_archived: true }), enabled: operationalEnabled && repliesVisible, refetchInterval: operationalEnabled && repliesVisible ? 5000 : false }),
    conversations: useQuery({ queryKey: ["conversations"], queryFn: api.listConversations, enabled: operationalEnabled && conversationsVisible, refetchInterval: operationalEnabled && conversationsVisible ? 5000 : false }),
    templates: useQuery({ queryKey: ["templates"], queryFn: api.listTemplates, enabled: operationalEnabled }),
    campaigns: useQuery({ queryKey: ["campaigns"], queryFn: api.listCampaigns, enabled: operationalEnabled }),
    workflows: useQuery({ queryKey: ["workflows"], queryFn: api.listWorkflows, enabled: operationalEnabled && workflowsVisible }),
    integrations: useQuery({ queryKey: ["integrations"], queryFn: api.listIntegrations, enabled: operationalEnabled && integrationsVisible }),
    autoReplyPending: useQuery({ queryKey: ["auto-reply-pending"], queryFn: api.listAutoReplyPending, enabled: operationalEnabled && autoReplyVisible, refetchInterval: operationalEnabled && autoReplyVisible ? 5000 : false }),
    autoReplyLog: useQuery({ queryKey: ["auto-reply-log"], queryFn: api.listAutoReplyLog, enabled: operationalEnabled && autoReplyVisible, refetchInterval: operationalEnabled && autoReplyVisible ? 5000 : false }),
    audit: useQuery({ queryKey: ["audit"], queryFn: api.listAudit, enabled: operationalEnabled })
  };
}

function invalidateAll(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries();
}

async function refreshReplyViews(queryClient: ReturnType<typeof useQueryClient>, contactId?: string) {
  const [
    replies,
    conversations,
    contacts,
    followups,
    providerHealth,
    autoReplyPending,
    autoReplyLog
  ] = await Promise.all([
    api.listReplies({ include_archived: true }),
    api.listConversations(),
    api.listContacts(),
    api.listFollowups(),
    api.listProviderHealth(),
    api.listAutoReplyPending(),
    api.listAutoReplyLog()
  ]);
  queryClient.setQueryData(["replies"], replies);
  queryClient.setQueryData(["conversations"], conversations);
  queryClient.setQueryData(["contacts"], contacts);
  queryClient.setQueryData(["followups"], followups);
  queryClient.setQueryData(["provider-health"], providerHealth);
  queryClient.setQueryData(["auto-reply-pending"], autoReplyPending);
  queryClient.setQueryData(["auto-reply-log"], autoReplyLog);
  if (contactId) {
    try {
      queryClient.setQueryData(["conversation", contactId], await api.getConversation(contactId));
    } catch {
      // The contact may have disappeared while a fetch was in flight.
    }
  }
}

function ModeLabel({ settings }: { settings?: SettingsRead }) {
  const mode = settings?.mode ?? "DRY-RUN";
  const liveBlocked = mode === "LIVE" && Boolean(
    settings?.transport_simulated || settings?.transport_mismatch || settings?.release_blocked
  );
  const className =
    mode === "LIVE" && !liveBlocked
      ? "border-emerald-700 bg-emerald-700 text-white"
      : mode === "CANARY"
        ? "border-amber-500 bg-amber-100 text-amber-900"
        : "border-slate-400 bg-slate-100 text-slate-800";
  const label = liveBlocked ? "LIVE BLOCKED" : mode;
  return <span className={`inline-flex h-8 items-center rounded border px-3 text-xs font-semibold ${className}`}>{label}</span>;
}

function ListeningIndicator({ settings }: { settings?: SettingsRead }) {
  const label = !settings?.auto_reply_enabled
    ? "AUTO-REPLY OFF"
    : settings.auto_reply_mode === "autonomous" && settings.auto_reply_autonomous_authorized && !settings.auto_reply_kill_switch
      ? "AUTONOMOUS"
      : "PROPOSAL ONLY";
  return <span className="listening-indicator"><span /> {label}</span>;
}

function Panel({ title, icon: Icon, children, action }: { title: string; icon: typeof Activity; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <div className="flex items-center gap-3">
          <Icon className="h-5 w-5 text-accent" />
          <h2>{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function StatusPill({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const badStatuses = new Set(["failed", "blocked", "reconciliation_required", "missing", "rejected", "inactive", "not_allowed", "unsafe", "invalid", "hard_bounce", "suppressed"]);
  const goodStatuses = new Set(["verified", "valid", "sent", "provider_accepted", "passed", "fresh", "source_backed", "active", "smtp_verified", "provider_verified", "canary_verified"]);
  const bad = badStatuses.has(normalized);
  const good = goodStatuses.has(normalized);
  const tone = bad ? "bad" : good ? "good" : "neutral";
  return <span className={`pill ${tone}`}>{value}</span>;
}

function TransportTruth({ settings, compact = false }: { settings?: SettingsRead; compact?: boolean }) {
  if (!settings) return null;
  const identityKind = settings.gmail_user?.toLowerCase().endsWith(".test")
    ? "synthetic"
    : settings.gmail_user
      ? "configured"
      : "not_configured";
  return (
    <div className={compact ? "notice" : "notice mt-4"}>
      <strong>Transport truth</strong>
      <div>identity={identityKind}</div>
      <div>configured={settings.configured_transport} effective={settings.effective_transport}</div>
      <div>source={settings.transport_source} simulated={settings.transport_simulated ? "yes" : "no"}</div>
      {settings.transport_mismatch && <div className="text-danger">Configured and effective transport differ.</div>}
    </div>
  );
}

function AuthenticationGate({
  security,
  authenticated,
  rejected,
  checking,
  onSignIn,
  onRetry
}: {
  security?: SecurityStatus;
  authenticated: boolean;
  rejected: boolean;
  checking: boolean;
  onSignIn: () => void;
  onRetry: () => void;
}) {
  const backendBlocked = security?.release_blocked === true;
  const heading = backendBlocked
    ? "Sign-in setup required"
    : rejected
      ? "Your session has expired"
      : checking
        ? "Opening Finimatic"
        : "Sign in to continue";
  return (
    <section className="auth-gate" role="status">
      <ShieldCheck className="h-8 w-8 text-accent" />
      <div>
        <h2>{heading}</h2>
        <p>
          {backendBlocked
            ? "Configure the hosted sign-in provider before using this deployment."
            : authenticated
              ? "Finimatic is loading your workspace."
              : "Sign in to open your Finimatic workspace."}
        </p>
      </div>
      <div className="auth-actions">
        {!backendBlocked && !authenticated && (
          <button className="button primary" disabled={checking} onClick={onSignIn}>
            <LogIn className="h-4 w-4" /> Sign In
          </button>
        )}
        <button className="button secondary" disabled={checking} onClick={onRetry}>
          <RefreshCw className={checking ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Retry
        </button>
      </div>
    </section>
  );
}

type VerificationPolicy = { status: string; severity: string; reason_code?: string | null; allowed: boolean };

function verificationMaps(verifications: EmailVerification[]) {
  return {
    byContactId: new Map(verifications.filter((row) => row.contact_id).map((row) => [row.contact_id as string, row])),
    byEmail: new Map(verifications.map((row) => [row.email.toLowerCase(), row]))
  };
}

function verificationForContact(contact: Contact | undefined, verifications: EmailVerification[]) {
  if (!contact) return undefined;
  const maps = verificationMaps(verifications);
  return maps.byContactId.get(contact.id) ?? maps.byEmail.get(contact.email.toLowerCase());
}

function verificationSeverity(status: string, policy?: VerificationPolicy) {
  if (policy?.severity) return policy.severity;
  if (["verified", "valid"].includes(status)) return "allow";
  if (["invalid", "disposable", "stale", "hard_bounce", "suppressed"].includes(status)) return "block";
  return "warn";
}

function VerificationBadge({ verification, policy }: { verification?: EmailVerification; policy?: VerificationPolicy }) {
  const status = policy?.status || verification?.status || "unknown";
  const severity = verificationSeverity(status, policy);
  const title = `Recipient verification only. This does not prove inbox placement. ${policy?.reason_code ?? verification?.provider_status ?? ""}`.trim();
  return <span className={`verification-badge ${severity}`} title={title}>{status}</span>;
}

function VerificationDetailPanel({
  detail,
  isLoading
}: {
  detail?: { verification: EmailVerification; attempts: { provider: string; status: string; response_code?: string | null; latency_ms: number; cost_units: number; created_at?: string | null }[]; policy: VerificationPolicy };
  isLoading?: boolean;
}) {
  if (isLoading) return <div className="notice compact">Loading recipient verification...</div>;
  if (!detail) return <div className="notice compact">No recipient verification check has run yet.</div>;
  return (
    <div className="verification-detail">
      <div className="verification-detail-head">
        <div>
          <h3>Recipient Verification</h3>
          <p>Address validity signals only, not inbox placement.</p>
        </div>
        <VerificationBadge verification={detail.verification} policy={detail.policy} />
      </div>
      <div className="status-list">
        <span>Provider <strong>{detail.verification.provider}</strong></span>
        <span>Result <strong>{detail.verification.provider_status || "not checked"}</strong></span>
        <span>Confidence <strong>{Math.round((detail.verification.confidence || 0) * 100)}%</strong></span>
        <span>Fresh until <strong>{detail.verification.expires_at ? new Date(detail.verification.expires_at).toLocaleDateString() : "unknown"}</strong></span>
        <span>Queue gate <strong>{detail.policy.allowed ? detail.policy.severity : detail.policy.reason_code || "blocked"}</strong></span>
      </div>
      <DataTable
        columns={["provider", "status", "response", "latency", "created"]}
        rows={detail.attempts.map((row) => [
          row.provider,
          row.status,
          row.response_code ?? "",
          `${row.latency_ms} ms`,
          row.created_at ? new Date(row.created_at).toLocaleString() : ""
        ])}
      />
    </div>
  );
}

function IntentBadge({ intent }: { intent: string }) {
  const normalized = intent || "unknown";
  return <span className={`intent-badge intent-${normalized}`}>{normalized}</span>;
}

function apiErrorMessage(error: unknown, fallback: string) {
  if (!(error instanceof Error)) return fallback;
  try {
    const parsed = JSON.parse(error.message);
    if (Array.isArray(parsed.detail?.blocked)) {
      return `${fallback}: ${parsed.detail.blocked.map(formatBlockReason).join(", ")}`;
    }
    if (typeof parsed.detail === "string") {
      return `${fallback}: ${parsed.detail}`;
    }
    if (typeof parsed.detail?.reason === "string") {
      if (typeof parsed.detail.message === "string") {
        return `${fallback}: ${parsed.detail.message}`;
      }
      const reason = parsed.detail.reason.replaceAll("_", " ");
      const status = parsed.detail.status ? ` (${parsed.detail.status})` : "";
      if (parsed.detail.reason === "sequence_already_sent" && parsed.detail.next_sequence_num) {
        return `${fallback}: sequence 1 is already sent. Approve as follow-up #${parsed.detail.next_sequence_num}.`;
      }
      return `${fallback}: ${reason}${status}`;
    }
  } catch {
    return error.message || fallback;
  }
  return error.message || fallback;
}

function deliveryToastMessage(result: ApprovalResponse | FollowupApprovalResponse, fallback: string) {
  const reasons = result.queue?.policy_block_reasons?.filter(Boolean).map(formatBlockReason).join(", ");
  const lastError = [
    result.queue?.latest_attempt?.error_code,
    result.queue?.latest_attempt?.error_detail_redacted,
    result.queue?.last_attempt_error_code,
    result.queue?.last_attempt_error_detail
  ].filter(Boolean).join(": ");
  switch (result.delivery_status) {
    case "provider_accepted":
    case "sent":
      return "Email provider accepted the message.";
    case "deferred":
      return reasons ? `Approved. Deferred: ${reasons}.` : "Approved. Deferred until the send window or delay clears.";
    case "blocked":
      return reasons ? `Approved, but blocked: ${reasons}.` : "Approved, but blocked by delivery policy.";
    case "simulated":
    case "dry_run_blocked":
      return "Dry Run is on. No provider was contacted.";
    case "reconciliation_required":
      return lastError ? `Provider outcome is uncertain and requires reconciliation: ${lastError}.` : "Provider outcome is uncertain and requires reconciliation.";
    case "failed":
      return lastError ? `Approved, but delivery failed: ${lastError}.` : "Approved, but delivery failed.";
    case "queued_for_bulk":
    case "queued":
      return fallback;
    default:
      return result.delivery_status ? `Delivery status: ${result.delivery_status}.` : fallback;
  }
}

function queueProcessToast(result: QueueProcessResult) {
  if (!result.processed && result.future_scheduled_count) {
    const due = result.next_due_at ? ` Next due: ${new Date(result.next_due_at).toLocaleString()}.` : "";
    const scheduler = result.scheduler_effective === false ? " Scheduler is not running." : "";
    return `No queue rows were due. ${result.future_scheduled_count} future row(s) remain.${due}${scheduler}`;
  }
  if (!result.processed) {
    return "No eligible queue rows were due; nothing was dispatched.";
  }
  return `Queue processed: ${result.processed} processed, ${result.provider_accepted} provider accepted, ${result.simulated} simulated, ${result.blocked} blocked, ${result.reconciliation_required} reconciliation, ${result.deferred ?? 0} deferred`;
}

function bulkApprovalToast(result: BulkApprovalResponse) {
  if (result.dispatch_requested) {
    return `Bulk send: ${result.processed} processed, ${result.provider_accepted} provider accepted, ${result.deferred ?? 0} deferred, ${result.blocked} blocked.`;
  }
  const scheduler = result.scheduler_effective ? "Automatic processing may run later." : "Automatic processing is not running.";
  return `${result.queued} of ${result.selected} selected drafts approved and queued only. ${scheduler}`;
}

function queueReason(row: QueueEntry) {
  const reasons = row.policy_block_reasons?.filter(Boolean);
  if (reasons?.length) return reasons.map(formatBlockReason).join(", ");
  const attempt = [
    row.latest_attempt?.attempt_status,
    row.latest_attempt?.error_code,
    row.latest_attempt?.error_detail_redacted,
    row.last_attempt_status,
    row.last_attempt_error_code,
    row.last_attempt_error_detail
  ].filter(Boolean);
  return attempt.length ? attempt.join(": ") : "none";
}

function hasQueueAcceptanceEvidence(attempt?: QueueEntry["latest_attempt"]) {
  if (
    !attempt
    || attempt.provider_accepted !== true
    || attempt.provider_contacted !== true
    || attempt.simulated !== false
  ) {
    return false;
  }
  if (attempt.effective_transport === "gmail_api") {
    const providerMessageId = attempt.provider_message_id?.trim() ?? "";
    return /^[0-9a-fA-F]{12,32}$/.test(providerMessageId);
  }
  return attempt.effective_transport === "smtp"
    && attempt.provider_response_classification === "smtp_transaction_completed";
}

const blockReasonLabels: Record<string, string> = {
  BOUNCE_RATE_HIGH: "Bounce rate is above the sender safety threshold",
  COMPLAINT_RATE_HIGH: "Complaint rate is above the sender safety threshold",
  DAILY_CAP_EXCEEDED: "Daily sender cap has been reached",
  DRAFT_NOT_APPROVED: "Draft is not approved",
  HOURLY_CAP_EXCEEDED: "Hourly sender cap has been reached",
  MAILBOX_RAMP_CAP_EXCEEDED: "Mailbox ramp cap has been reached",
  RECENT_DEFERRAL_SPIKE: "Recent provider deferrals are elevated",
  RECIPIENT_BOUNCED: "Recipient has a prior bounce or complaint",
  RECIPIENT_DOMAIN_CAP_BLOCKED: "Recipient domain is blocked by sender cap policy",
  RECIPIENT_DOMAIN_DAILY_CAP_EXCEEDED: "Recipient domain daily cap has been reached",
  RECIPIENT_EMAIL_DISPOSABLE: "Recipient email is disposable",
  RECIPIENT_EMAIL_HARD_BOUNCE: "Recipient email has a prior hard bounce",
  RECIPIENT_EMAIL_INVALID: "Recipient email is invalid",
  RECIPIENT_EMAIL_STALE: "Recipient verification is stale",
  RECIPIENT_EMAIL_SUPPRESSED: "Recipient email is suppressed",
  RECIPIENT_EMAIL_UNSAFE: "Recipient verification is not safe to queue",
  RECIPIENT_EMAIL_UNKNOWN: "Recipient verification is unknown",
  RECIPIENT_REPLIED: "Recipient already replied",
  RECIPIENT_SUPPRESSED: "Recipient is suppressed",
  CANARY_NOT_VERIFIED: "Waiting for the one-time Gmail test email",
  SEND_WINDOW_NOT_ELAPSED: "Send window or spacing has not elapsed",
  SENDER_DOMAIN_UNHEALTHY: "Sender domain health is blocking this send",
  SENDER_MAILBOX_UNHEALTHY: "Sender mailbox health is blocking this send",
  SENDER_NOT_VERIFIED: "Sender is not verified",
  UNSUPPORTED_PERSONALIZATION: "Draft personalization is not source-backed"
};

const deliverabilityGateNames = new Set([
  "sender_domain_healthy",
  "per_domain_daily_cap",
  "per_mailbox_ramp_stage",
  "bounce_rate_under_threshold",
  "complaint_rate_under_threshold",
  "no_recent_deferral_spike"
]);

function formatBlockReason(reason?: string | null) {
  if (!reason) return "none";
  return blockReasonLabels[reason] ?? reason.toLowerCase().replace(/_/g, " ");
}

function formatGateName(gate: string) {
  return gate.replace(/_/g, " ");
}

function formatPolicyDetails(details?: Record<string, unknown>) {
  if (!details) return "";
  const parts = ["status", "used", "daily_cap", "sent_today", "effective_cap", "count", "rate", "threshold", "window_days"]
    .filter((key) => details[key] !== undefined && details[key] !== null)
    .map((key) => `${key.replace(/_/g, " ")}: ${String(details[key])}`);
  return parts.join("; ");
}

function App() {
  const [surface, setSurface] = useState<Surface>("setup");
  const queryClient = useQueryClient();
  const security = useQuery({ queryKey: ["security-status"], queryFn: api.getSecurityStatus, retry: false });
  const authEnforced = security.data?.authentication_enforced === true;
  const authSession = useQuery({
    queryKey: ["api-auth-session"],
    queryFn: api.getAuthSession,
    enabled: authEnforced,
    retry: false
  });
  const authenticated = authSession.data?.authenticated === true && authSession.data?.authorized === true;
  const authProbe = useQuery({
    queryKey: ["api-auth-probe"],
    queryFn: api.listProviderHealth,
    enabled: security.isSuccess && (!authEnforced || authenticated),
    retry: false,
    refetchInterval: security.isSuccess && (!authEnforced || authenticated) ? 30000 : false
  });
  const operationalEnabled = security.isSuccess && (!authEnforced || authenticated) && authProbe.isSuccess;
  const data = useAppData(surface, operationalEnabled);
  const settings = data.settings.data;
  const verifications = data.verifications.data?.items ?? [];
  const hasQueryError = [
    data.settings,
    data.providerHealth,
    data.deliverability,
    data.contacts,
    data.recentlyDeletedContacts,
    data.enrichmentWorkbook,
    data.verifications,
    data.drafts,
    data.queue,
    data.followups,
    data.suppressions,
    data.replies,
    data.conversations,
    data.templates,
    data.campaigns,
    data.workflows,
    data.integrations,
    data.autoReplyPending,
    data.autoReplyLog,
    data.audit
  ].some((query) => query.isError);
  const authRejected = authEnforced && (authSession.isError || (authenticated && authProbe.isError));
  const authChecking = security.isPending || (authEnforced && (authSession.isPending || (authenticated && authProbe.isPending)));
  const retryAuthentication = () => {
    void security.refetch();
    void authSession.refetch().then((result) => {
      if (result.data?.authenticated) void authProbe.refetch();
    });
  };
  const signIn = () => {
    window.location.assign(authenticationLoginUrl(window.location.pathname));
  };
  const signOut = useMutation({
    mutationFn: api.logout,
    onSuccess: async () => {
      setSurface("setup");
      queryClient.clear();
      await Promise.all([security.refetch(), authSession.refetch()]);
    },
    onError: (error) => toast(apiErrorMessage(error, "Sign out failed"))
  });

  useEffect(() => {
    const url = new URL(window.location.href);
    const oauthStatus = url.searchParams.get("gmail_api_oauth");
    if (!oauthStatus) return;
    setSurface("settings");
    const messages: Record<string, string> = {
      connected: "gmail_api_connected",
      connected_unverified: "gmail_api_connected_but_verification_failed",
      failed: "gmail_api_oauth_failed",
      refresh_token_missing: "gmail_api_refresh_token_missing"
    };
    toast(messages[oauthStatus] ?? oauthStatus);
    invalidateAll(queryClient);
    url.searchParams.delete("gmail_api_oauth");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, [queryClient]);

  return (
    <div className="app-root bg-[#f4f6f9] text-ink">
      <header className="topbar">
        <div>
          <div className="text-xl font-semibold">Finimatic</div>
          <div className="text-sm text-muted">{settings?.gmail_user || "Sender not configured"}</div>
        </div>
        <div className="flex items-center gap-3">
          <StatusPill value={settings?.sender_readiness ?? "not_configured"} />
          <ModeLabel settings={settings} />
          <ListeningIndicator settings={settings} />
          {authEnforced && authSession.data?.authenticated && (
            <button className="button secondary" disabled={signOut.isPending} onClick={() => signOut.mutate()}>
              <LogOut className="h-4 w-4" /> {signOut.isPending ? "Signing Out..." : "Sign Out"}
            </button>
          )}
        </div>
      </header>

      <div className="app-shell">
        <aside className="sidebar">
          {surfaces.map(([id, label, Icon]) => (
            <button key={id} className={surface === id ? "nav active" : "nav"} disabled={!operationalEnabled} onClick={() => setSurface(id)}>
              <Icon className="h-4 w-4" />
              <span>{label}</span>
              <ChevronRight className="ml-auto h-4 w-4 opacity-50" />
            </button>
          ))}
        </aside>

        <main className="content">
          {authEnforced && !operationalEnabled && (
            <AuthenticationGate
              security={security.data}
              authenticated={authenticated}
              rejected={authRejected}
              checking={authChecking}
              onSignIn={signIn}
              onRetry={retryAuthentication}
            />
          )}
          {operationalEnabled && data.settings.isPending && <div className="notice compact">Loading server settings...</div>}
          {operationalEnabled && hasQueryError && (
            <div className="notice warning compact" role="alert">
              Some server data could not be loaded. Empty tables may be incomplete; retry after checking the backend connection.
            </div>
          )}
          {operationalEnabled && surface === "setup" && <SetupPanel settings={settings} />}
          {operationalEnabled && surface === "health" && <HealthPanel settings={settings} providerHealth={data.providerHealth.data?.items ?? []} />}
          {operationalEnabled && surface === "deliverability" && <DeliverabilityPanel summary={data.deliverability.data} />}
          {operationalEnabled && surface === "import" && <ImportPanel contactsTotal={data.contacts.data?.total ?? 0} />}
          {operationalEnabled && surface === "contacts" && <ContactsPanel contacts={data.contacts.data?.items ?? []} recentlyDeletedContacts={data.recentlyDeletedContacts.data?.items ?? []} templates={data.templates.data?.items ?? []} settings={settings} navigate={setSurface} verifications={verifications} />}
          {operationalEnabled && surface === "enrichment" && <EnrichmentPanel contacts={data.contacts.data?.items ?? []} workbook={data.enrichmentWorkbook.data} verifications={verifications} verificationSummary={data.verifications.data?.summary} />}
          {operationalEnabled && surface === "drafts" && <DraftsPanel contacts={data.contacts.data?.items ?? []} drafts={data.drafts.data?.items ?? []} templates={data.templates.data?.items ?? []} queue={data.queue.data?.items ?? []} verifications={verifications} />}
          {operationalEnabled && surface === "templates" && <TemplatesPanel templates={data.templates.data?.items ?? []} />}
          {operationalEnabled && surface === "campaigns" && <CampaignsPanel campaigns={data.campaigns.data?.items ?? []} contacts={data.contacts.data?.items ?? []} navigate={setSurface} />}
          {operationalEnabled && surface === "workflows" && <WorkflowsPanel workflows={data.workflows.data?.items ?? []} />}
          {operationalEnabled && surface === "queue" && <QueuePanel queue={data.queue.data?.items ?? []} contacts={data.contacts.data?.items ?? []} drafts={data.drafts.data?.items ?? []} verifications={verifications} />}
          {operationalEnabled && surface === "followups" && <FollowupsPanel followups={data.followups.data?.items ?? []} navigate={setSurface} />}
          {operationalEnabled && surface === "replies" && <RepliesPanel contacts={data.contacts.data?.items ?? []} replies={data.replies.data?.items ?? []} />}
          {operationalEnabled && surface === "conversations" && <ConversationsPanel contacts={data.contacts.data?.items ?? []} summaries={data.conversations.data?.items ?? []} settings={settings} providerHealth={data.providerHealth.data?.items ?? []} />}
          {operationalEnabled && surface === "autoReply" && <AutoReplyPanel pending={data.autoReplyPending.data?.items ?? []} log={data.autoReplyLog.data?.items ?? []} navigate={setSurface} settings={settings} />}
          {operationalEnabled && surface === "suppressions" && <SuppressionsPanel suppressions={data.suppressions.data?.items ?? []} />}
          {operationalEnabled && surface === "audit" && <AuditPanel audit={data.audit.data?.items ?? []} />}
          {operationalEnabled && surface === "errors" && <ErrorsPanel audit={data.audit.data?.items ?? []} />}
          {operationalEnabled && surface === "integrations" && <IntegrationsPanel summary={data.integrations.data} contacts={data.contacts.data?.items ?? []} />}
          {operationalEnabled && surface === "settings" && <SettingsPanel settings={settings} ready={data.settings.isSuccess} />}
        </main>
      </div>
      {operationalEnabled && <AssistantWidget />}
    </div>
  );
}

function SetupPanel({ settings }: { settings?: SettingsRead }) {
  const queryClient = useQueryClient();
  const [showCanary, setShowCanary] = useState(false);
  const [canary, setCanary] = useState<{ status: string; nonce?: string; sent_at?: string; sender_identity?: string } | null>(null);
  const verify = useMutation({
    mutationFn: api.verifyEmail,
    onSuccess: (result) => {
      toast(result.readiness);
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "Email provider verification failed"))
  });
  const canarySend = useMutation({
    mutationFn: api.sendCanary,
    onSuccess: (result) => {
      setCanary({
        status: result.status,
        nonce: result.nonce,
        sent_at: result.sent_at,
        sender_identity: result.sender_identity
      });
      setShowCanary(false);
      toast(result.status);
      invalidateAll(queryClient);
    }
  });
  return (
    <Panel
      title="Setup"
      icon={ShieldCheck}
      action={
        <div className="flex gap-2">
          <button className={`button secondary${verify.isPending ? " is-loading" : ""}`} disabled={verify.isPending} aria-busy={verify.isPending} onClick={() => verify.mutate()}>
            <Check className={verify.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> <span>{verify.isPending ? "Verifying..." : "Verify Email"}</span>
          </button>
          <button className="button primary" onClick={() => setShowCanary(true)} disabled={!settings?.report_recipient}>
            <Send className="h-4 w-4" /> Send Test Email
          </button>
        </div>
      }
    >
      <div className="metrics">
        <Metric label="Sender" value={settings?.gmail_user || "missing"} />
        <Metric label="Email Provider" value={settings?.sender_readiness ?? "not_configured"} />
        <Metric label="Canary" value={settings?.canary_verified ? "verified" : "not verified"} />
        <Metric label="Auto processing" value={settings?.auto_process_effective ? "running" : "paused"} />
        <Metric label="Dry run" value={settings?.dry_run ? "enabled" : "disabled"} />
        <Metric label="Configured transport" value={settings?.configured_transport ?? "unknown"} />
        <Metric label="Effective transport" value={settings?.effective_transport ?? "unknown"} />
        {settings?.warm_up_mode && <Metric label="Warm-up cap" value={`${settings.warm_up_current_limit ?? settings.daily_send_cap}/day`} />}
      </div>
      <TransportTruth settings={settings} />
      {!settings?.auto_process_effective && (
        <div className="notice warning mt-4">
          <strong>Automatic queue processing is paused.</strong>
          <div>{settings?.auto_process_block_reason?.split("_").join(" ") ?? "Enable automatic processing in Settings."}</div>
        </div>
      )}
      {settings?.release_blocked && (
        <div className="notice mt-4">
          <strong>Release blocked</strong>
          <div>Operational API authentication is not configured. Mode: {settings.api_security_mode}.</div>
        </div>
      )}
      {canary && (
        <div className="notice">
          <div>status={canary.status}</div>
          {canary.nonce && <div>nonce={canary.nonce}</div>}
          {canary.sent_at && <div>sent_at={canary.sent_at}</div>}
          {canary.sender_identity && <div>sender_identity={canary.sender_identity}</div>}
        </div>
      )}
      {showCanary && (
        <div className="modal-backdrop">
          <div className="modal">
            <h3>Confirm Canary</h3>
            <p>{settings?.report_recipient}</p>
            <div className="flex justify-end gap-2">
              <button className="button secondary" onClick={() => setShowCanary(false)}>
                Cancel
              </button>
              <button className={`button primary${canarySend.isPending ? " is-loading" : ""}`} disabled={canarySend.isPending} onClick={() => canarySend.mutate()}>
                <Send className={canarySend.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> <span>{canarySend.isPending ? "Sending..." : "Confirm"}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

function HealthPanel({ settings, providerHealth }: { settings?: SettingsRead; providerHealth: ProviderHealth[] }) {
  const imap = providerHealth.find((row) => row.provider === "imap");
  return (
    <Panel title="Provider Health" icon={HeartPulse}>
      <div className="grid gap-4 lg:grid-cols-4">
        <ProviderBlock title="Gmail" status={settings?.sender_readiness ?? "unknown"} detail={`${settings?.gmail_user || "missing"} | configured ${settings?.configured_transport ?? "unknown"} | effective ${settings?.effective_transport ?? "unknown"}`} />
        <ProviderBlock title="Groq" status={settings?.groq_keys_count ? "configured" : "missing"} detail={`${settings?.groq_keys_count ?? 0} keys`} fingerprints={settings?.groq_keys_fingerprints ?? []} />
        <ProviderBlock title="Gemini" status={settings?.gemini_keys_count ? "configured" : "missing"} detail={`${settings?.gemini_keys_count ?? 0} keys`} fingerprints={settings?.gemini_keys_fingerprints ?? []} />
        <ProviderBlock title="IMAP" status={imap?.status ?? "unknown"} detail={imap?.details || "No fetch recorded"} fingerprints={imap?.last_checked ? [`checked ${new Date(imap.last_checked).toLocaleString()}`] : []} />
      </div>
    </Panel>
  );
}

function ProviderBlock({ title, status, detail, fingerprints = [] }: { title: string; status: string; detail: string; fingerprints?: string[] }) {
  return (
    <div className="provider">
      <div className="flex items-center justify-between">
        <h3>{title}</h3>
        <StatusPill value={status} />
      </div>
      <div className="text-sm text-muted">{detail}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        {fingerprints.map((item) => (
          <span className="fingerprint" key={item}>{item}</span>
        ))}
      </div>
    </div>
  );
}

function DeliverabilityPanel({ summary }: { summary?: DeliverabilitySummary }) {
  const queryClient = useQueryClient();
  const [seedEmail, setSeedEmail] = useState("");
  const [seedSubject, setSeedSubject] = useState("Finimatic inbox placement seed");
  const runCheck = useMutation({
    mutationFn: api.runDeliverabilityCheck,
    onSuccess: () => {
      toast("deliverability check updated");
      void queryClient.invalidateQueries({ queryKey: ["deliverability"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "deliverability check failed"))
  });
  const createSeed = useMutation({
    mutationFn: () => api.createInboxPlacementTest({ seed_email: seedEmail, subject: seedSubject }),
    onSuccess: () => {
      setSeedEmail("");
      toast("inbox placement test planned");
      void queryClient.invalidateQueries({ queryKey: ["deliverability"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "seed test failed"))
  });
  const mailbox = summary?.mailbox;
  const domain = summary?.domain_health;
  const policyChecks = summary?.policy.checks ?? {};
  return (
    <Panel
      title="Deliverability"
      icon={ShieldCheck}
      action={
        <button className={`button primary${runCheck.isPending ? " is-loading" : ""}`} disabled={runCheck.isPending} onClick={() => runCheck.mutate()}>
          <RefreshCw className={runCheck.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Check
        </button>
      }
    >
      <div className="metrics">
        <Metric label="Sender" value={summary?.sender || "missing"} />
        <Metric label="Domain" value={summary?.domain || "unknown"} />
        <Metric label="Mailbox Health" value={mailbox?.status || "unknown"} />
        <Metric label="Ramp Stage" value={mailbox?.ramp_stage || "low_volume"} />
      </div>
      <div className="notice compact">
        Gmail API success means provider acceptance only. Recipient verification is address validity risk, seed placement is telemetry, and deliverability health is operational risk signals. None of these proves inbox placement.
      </div>
      <div className="ops-grid mt-4">
        <div className="ops-card">
          <h3>Domain Authentication</h3>
          <div className="status-list">
            <span>SPF <StatusPill value={domain?.spf_status || "unknown"} /></span>
            <span>DKIM <StatusPill value={domain?.dkim_status || "unknown"} /></span>
            <span>DMARC <StatusPill value={domain?.dmarc_status || "unknown"} /></span>
            <span>Alignment <StatusPill value={domain?.alignment_status || "unknown"} /></span>
          </div>
        </div>
        <div className="ops-card">
          <h3>Inbox Placement Seed</h3>
          <div className="form-grid single">
            <label>Seed Email<input value={seedEmail} onChange={(event) => setSeedEmail(event.target.value)} /></label>
            <label>Subject<input value={seedSubject} onChange={(event) => setSeedSubject(event.target.value)} /></label>
          </div>
          <button className="button secondary mt-3" disabled={!seedEmail || createSeed.isPending} onClick={() => createSeed.mutate()}>
            <Send className="h-4 w-4" /> Plan Test
          </button>
        </div>
      </div>
      <DataTable
        columns={["gate", "status", "reason", "details"]}
        rows={Object.entries(policyChecks).map(([gate, check]) => [
          formatGateName(gate),
          check.allowed ? "pass" : "block",
          formatBlockReason(check.reason_code),
          formatPolicyDetails(check)
        ])}
      />
      <DataTable
        columns={["check", "status", "severity", "details", "checked"]}
        rows={(summary?.checks ?? []).map((row) => [
          row.check_type,
          row.status,
          row.severity,
          row.details_redacted ?? "",
          row.checked_at ? new Date(row.checked_at).toLocaleString() : ""
        ])}
      />
      <DataTable
        columns={["seed", "domain", "subject", "status", "placement"]}
        rows={(summary?.inbox_tests ?? []).map((row) => [row.seed_email, row.recipient_domain, row.subject, row.status, row.placement])}
      />
      <DataTable
        columns={["recipient domain", "used", "daily cap", "status", "last sent"]}
        rows={(summary?.recipient_domain_caps ?? []).map((row) => [
          row.recipient_domain,
          String(row.sent_today),
          String(row.daily_cap),
          row.status,
          row.last_sent_at ? new Date(row.last_sent_at).toLocaleString() : ""
        ])}
      />
    </Panel>
  );
}

function EnrichmentPanel({
  contacts,
  workbook,
  verifications,
  verificationSummary
}: {
  contacts: Contact[];
  workbook?: EnrichmentWorkbook;
  verifications: EmailVerification[];
  verificationSummary?: VerificationSummary;
}) {
  const queryClient = useQueryClient();
  const [selectedContactId, setSelectedContactId] = useState("");
  useEffect(() => {
    if (!selectedContactId && contacts[0]) setSelectedContactId(contacts[0].id);
  }, [contacts, selectedContactId]);
  const evidence = useQuery({
    queryKey: ["contact-evidence", selectedContactId],
    queryFn: () => api.getContactEvidence(selectedContactId),
    enabled: Boolean(selectedContactId)
  });
  const selectedVerification = useQuery({
    queryKey: ["contact-verification", selectedContactId],
    queryFn: () => api.getContactVerification(selectedContactId),
    enabled: Boolean(selectedContactId)
  });
  const seedEvidence = useMutation({
    mutationFn: () => api.seedContactEvidence(selectedContactId),
    onSuccess: async () => {
      toast("evidence seeded from contact fields");
      await Promise.all([
        queryClient.refetchQueries({ queryKey: ["contact-evidence", selectedContactId] }),
        queryClient.refetchQueries({ queryKey: ["enrichment-workbook"] })
      ]);
    },
    onError: (error) => toast(apiErrorMessage(error, "evidence seed failed"))
  });
  const verifyContact = useMutation({
    mutationFn: () => api.runContactVerification(selectedContactId),
    onSuccess: () => {
      toast("verification completed");
      void queryClient.invalidateQueries({ queryKey: ["verifications"] });
      void queryClient.invalidateQueries({ queryKey: ["contact-verification", selectedContactId] });
      void queryClient.invalidateQueries({ queryKey: ["enrichment-workbook"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "verification failed"))
  });
  const selectedEvidence: ContactEvidence | undefined = evidence.data;
  const verificationLookup = verificationMaps(verifications);
  return (
    <Panel
      title="Enrichment"
      icon={Search}
      action={
        <div className="flex flex-wrap gap-2">
          <button className="button secondary" disabled={!selectedContactId || seedEvidence.isPending} onClick={() => seedEvidence.mutate()}>
            <Sparkles className="h-4 w-4" /> Seed Evidence
          </button>
          <button className="button primary" disabled={!selectedContactId || verifyContact.isPending} onClick={() => verifyContact.mutate()}>
            <Check className="h-4 w-4" /> Verify Email
          </button>
        </div>
      }
    >
      <div className="metrics">
        <Metric label="Contacts" value={String(workbook?.summary.contacts ?? contacts.length)} />
        <Metric label="With Evidence" value={String(workbook?.summary.contacts_with_evidence ?? 0)} />
        <Metric label="Verified/Valid" value={String(verificationSummary?.verified_or_valid ?? 0)} />
        <Metric label="Blocked Verification" value={String(verificationSummary?.blocked ?? 0)} />
      </div>
      <div className="ops-grid mt-4">
        <label>Contact<select value={selectedContactId} onChange={(event) => setSelectedContactId(event.target.value)}>{contacts.map((contact) => <option value={contact.id} key={contact.id}>{contact.creator_name || contact.business_name || contact.email}</option>)}</select></label>
        <div className="ops-card">
          <h3>Evidence Check</h3>
          <div className="status-list">
            <span>Status <StatusPill value={selectedEvidence?.evidence_check.status || "unknown"} /></span>
            <span>Supported claims <strong>{selectedEvidence?.evidence_check.supported_claims.length ?? 0}</strong></span>
            <span>Missing <strong>{selectedEvidence?.evidence_check.missing_evidence.join(", ") || "none"}</strong></span>
          </div>
        </div>
        <div className="ops-card verification-card">
          <VerificationDetailPanel detail={selectedVerification.data} isLoading={selectedVerification.isFetching} />
        </div>
      </div>
      <DataTable
        columns={["contact", "verification", "evidence", "stale", "draft readiness", "website"]}
        rows={(workbook?.items ?? []).map((row) => [
          row.email,
          verificationLookup.byContactId.get(row.contact_id)?.status ?? verificationLookup.byEmail.get(row.email.toLowerCase())?.status ?? "unknown",
          String(row.supported_claims),
          String(row.stale_facts),
          row.draft_readiness,
          row.website ?? ""
        ])}
      />
      {selectedEvidence && (
        <DataTable
          columns={["field", "value", "source", "confidence", "status"]}
          rows={selectedEvidence.lead_facts.map((fact) => [fact.field_key, truncateDisplay(fact.field_value, 100), fact.source_label, String(fact.confidence), fact.status])}
        />
      )}
    </Panel>
  );
}

type EvidenceFact = LeadFact | AccountFact;

function EvidenceFactList({ title, facts }: { title: string; facts: EvidenceFact[] }) {
  return (
    <div className="evidence-fact-block">
      <h4>{title}</h4>
      {!facts.length && <div className="notice compact">No facts recorded.</div>}
      <div className="evidence-fact-list">
        {facts.map((fact) => {
          const sourceUrl = fact.source_url || fact.source?.url || "";
          return (
            <div className="evidence-fact" key={fact.id}>
              <div className="evidence-fact-head">
                <strong>{fact.field_key}</strong>
                <StatusPill value={fact.freshness || fact.status} />
              </div>
              <div>{truncateDisplay(fact.field_value, 220)}</div>
              <div className="evidence-meta-row">
                <span>Source: <strong>{fact.source_label}</strong></span>
                <span>Confidence: <strong>{Math.round((fact.confidence ?? 0) * 100)}%</strong></span>
                <span>Status: <strong>{fact.status}</strong></span>
              </div>
              <div className="evidence-meta-row">
                <span>Fetched: <strong>{fact.fetched_at ? new Date(fact.fetched_at).toLocaleDateString() : "unknown"}</strong></span>
                <span>Expires: <strong>{fact.expires_at ? new Date(fact.expires_at).toLocaleDateString() : "none"}</strong></span>
              </div>
              {sourceUrl && <div className="evidence-source-url">{truncateDisplay(sourceUrl, 140)}</div>}
              {fact.raw_snippet_redacted && <div className="evidence-snippet">{truncateDisplay(fact.raw_snippet_redacted, 240)}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EvidenceCheckSummary({ check }: { check?: EvidenceCheck }) {
  if (!check) return <div className="notice compact">Evidence check unavailable.</div>;
  return (
    <div className="evidence-check-summary">
      <div className="status-list">
        <span>Status <StatusPill value={check.status} /></span>
        <span>Supported claims <strong>{check.supported_claims.length}</strong></span>
        <span>Missing <strong>{check.missing_evidence.join(", ") || "none"}</strong></span>
        <span>Stale/rejected facts <strong>{check.stale_facts.length}</strong></span>
      </div>
    </div>
  );
}

function DraftEvidencePanel({
  check,
  isLoading,
  onUseNeutral
}: {
  check?: EvidenceCheck;
  isLoading: boolean;
  onUseNeutral: () => void;
}) {
  const warning = Boolean(check?.neutral_copy_required);
  const panelClass = `evidence-panel${warning ? " warning" : check?.status === "passed" ? " good" : ""}`;
  return (
    <div className={panelClass}>
      <div className="evidence-panel-head">
        <div>
          <h3>Evidence Check</h3>
          <p>{isLoading ? "Checking draft evidence..." : check?.policy_version || "evidence_v1"}</p>
        </div>
        <StatusPill value={check?.status || (isLoading ? "checking" : "unknown")} />
      </div>
      {check && <EvidenceCheckSummary check={check} />}
      {warning && (
        <div className="notice warning compact">
          Unsupported personalization requires neutral copy before approval.
          <div className="mt-2">
            <button className="button secondary compact" type="button" onClick={onUseNeutral}>Use Neutral Copy</button>
          </div>
        </div>
      )}
      {check?.supported_claims.length ? <EvidenceFactList title="Usable Claims" facts={check.supported_claims.slice(0, 3)} /> : null}
      {check?.stale_facts.length ? <EvidenceFactList title="Stale Or Rejected" facts={check.stale_facts.slice(0, 3)} /> : null}
    </div>
  );
}

function workflowOutputSummary(output?: Record<string, unknown> | null): string {
  if (!output) return "";
  if (typeof output.error_code === "string") return output.error_code;
  if (typeof output.score === "number") return `score ${output.score}${typeof output.grade === "string" ? ` ${output.grade}` : ""}`;
  const verification = output.verification;
  if (verification && typeof verification === "object" && "status" in verification) {
    return `verification ${String((verification as { status?: unknown }).status ?? "")}`;
  }
  const draftPreview = output.draft_preview;
  if (draftPreview && typeof draftPreview === "object" && "subject" in draftPreview) {
    return `draft ${String((draftPreview as { subject?: unknown }).subject ?? "")}`;
  }
  if (typeof output.status === "string") return output.status;
  return truncateDisplay(JSON.stringify(output), 120);
}

function workflowEvidenceSummary(refs: string[]): string {
  if (!refs.length) return "none";
  const visible = refs.slice(0, 3).join(", ");
  return refs.length > 3 ? `${visible} +${refs.length - 3}` : visible;
}

function shortHash(value?: string | null): string {
  return value ? value.slice(0, 8) : "";
}

function WorkflowsPanel({ workflows }: { workflows: WorkflowSummary[] }) {
  const queryClient = useQueryClient();
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  useEffect(() => {
    if (!selectedWorkflowId && workflows[0]) setSelectedWorkflowId(workflows[0].id);
  }, [workflows, selectedWorkflowId]);
  const detail = useQuery({
    queryKey: ["workflow-detail", selectedWorkflowId],
    queryFn: () => api.getWorkflow(selectedWorkflowId),
    enabled: Boolean(selectedWorkflowId)
  });
  const run = useMutation({
    mutationFn: () => api.runWorkflow(selectedWorkflowId),
    onSuccess: (result) => {
      toast(`workflow ${result.status}`);
      void queryClient.invalidateQueries({ queryKey: ["workflow-detail", selectedWorkflowId] });
    },
    onError: (error) => toast(apiErrorMessage(error, "workflow run failed"))
  });
  const retry = useMutation({
    mutationFn: () => api.retryWorkflow(selectedWorkflowId),
    onSuccess: (result) => {
      toast(`workflow retry ${result.status}`);
      void queryClient.invalidateQueries({ queryKey: ["workflow-detail", selectedWorkflowId] });
    },
    onError: (error) => toast(apiErrorMessage(error, "workflow retry failed"))
  });
  const workflow: WorkflowDetail | undefined = detail.data;
  const workflowRows = workflow?.rows ?? [];
  const visibleWorkflowRows = workflowRows.slice(0, 25);
  const cellDetails = workflow
    ? visibleWorkflowRows.flatMap((row) =>
        workflow.columns.map((column) => ({
          row,
          column,
          cell: row.cells[column.key]
        }))
      )
    : [];
  return (
    <Panel
      title="Workflows"
      icon={Activity}
      action={
        <div className="flex flex-wrap gap-2">
          <button className="button secondary" disabled={!selectedWorkflowId || retry.isPending} onClick={() => retry.mutate()}>
            <RotateCcw className="h-4 w-4" /> Retry Failed
          </button>
          <button className="button primary" disabled={!selectedWorkflowId || run.isPending} onClick={() => run.mutate()}>
            <RefreshCw className={run.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Run
          </button>
        </div>
      }
    >
      <div className="grid gap-4">
        <label className="max-w-sm">Workbook<select value={selectedWorkflowId} onChange={(event) => setSelectedWorkflowId(event.target.value)}>{workflows.map((workflow) => <option value={workflow.id} key={workflow.id}>{workflow.name}</option>)}</select></label>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>contact</th>
                <th>row status</th>
                {(workflow?.columns ?? []).map((column) => <th key={column.id}>{column.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {visibleWorkflowRows.map((row) => (
                <tr key={row.id}>
                  <td>{row.email}</td>
                  <td><StatusPill value={row.status} /></td>
                  {(workflow?.columns ?? []).map((column) => {
                    const cell = row.cells[column.key];
                    return (
                      <td key={column.id}>
                        <div className="grid gap-1">
                          <StatusPill value={cell?.status ?? "pending"} />
                          <span>{cell ? `${cell.cost_units} cost` : "0 cost"}</span>
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <DataTable
          columns={["contact", "step", "status", "cost", "evidence refs", "output"]}
          rows={cellDetails.slice(0, 18).map(({ row, column, cell }) => [
            row.email,
            column.step_type,
            cell?.status ?? "pending",
            String(cell?.cost_units ?? 0),
            workflowEvidenceSummary(cell?.evidence_refs ?? []),
            workflowOutputSummary(cell?.output)
          ])}
        />
        <DataTable
          columns={["run", "status", "cost", "steps", "started", "completed"]}
          rows={(workflow?.runs ?? []).map((row) => [
            row.id.slice(0, 8),
            row.status,
            String(row.total_cost_units ?? 0),
            String(row.steps?.length ?? 0),
            row.started_at ? new Date(row.started_at).toLocaleString() : "",
            row.completed_at ? new Date(row.completed_at).toLocaleString() : ""
          ])}
        />
        <DataTable
          columns={["attempt", "contact", "step", "status", "cost", "error", "hashes"]}
          rows={(workflow?.attempts ?? []).slice(0, 20).map((row) => [
            `${row.id.slice(0, 8)} #${row.attempt_num}`,
            row.contact_email ?? "",
            row.step_type ?? row.column_label ?? "",
            row.status,
            String(row.cost_units),
            row.error_code ?? "",
            `${shortHash(row.input_hash)} / ${shortHash(row.step_config_hash)}`
          ])}
        />
      </div>
    </Panel>
  );
}

function IntegrationsPanel({ summary, contacts }: { summary?: IntegrationsSummary; contacts: Contact[] }) {
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState("google_sheets");
  const [contactId, setContactId] = useState("");
  const [lastPreview, setLastPreview] = useState<ExternalWritePreview | null>(null);
  useEffect(() => {
    if (!contactId && contacts[0]) setContactId(contacts[0].id);
  }, [contacts, contactId]);
  const preview = useMutation({
    mutationFn: () => api.previewIntegrationSync(provider, contactId),
    onSuccess: (result) => {
      setLastPreview(result);
      toast("diff preview created");
      void queryClient.invalidateQueries({ queryKey: ["integrations"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "integration preview failed"))
  });
  const activePreview = (
    lastPreview?.status === "pending_confirmation"
    && lastPreview.provider === provider
    && lastPreview.entity_id === contactId
      ? lastPreview
      : summary?.previews?.find(
          (row) => row.status === "pending_confirmation" && row.provider === provider && row.entity_id === contactId
        )
  ) ?? null;
  const confirm = useMutation({
    mutationFn: () => api.confirmIntegrationSync(activePreview?.provider ?? provider, activePreview?.id ?? ""),
    onSuccess: () => {
      setLastPreview(null);
      toast("sync journal recorded");
      void queryClient.invalidateQueries({ queryKey: ["integrations"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "integration confirmation failed"))
  });
  const cancel = useMutation({
    mutationFn: () => api.cancelIntegrationSync(activePreview?.provider ?? provider, activePreview?.id ?? ""),
    onSuccess: () => {
      setLastPreview(null);
      toast("preview cancelled");
      void queryClient.invalidateQueries({ queryKey: ["integrations"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "integration cancel failed"))
  });
  const previewRows = (activePreview?.diff.fields ?? []).map((field) => [
    field.local_field ?? field.field ?? "",
    field.external_field ?? "",
    field.before == null ? "" : String(field.before),
    field.after == null ? "" : String(field.after),
    field.status
  ]);
  const activePolicy = activePreview?.diff.policy ?? {};
  return (
    <Panel
      title="Integrations"
      icon={KeyRound}
      action={
        <div className="flex flex-wrap gap-2">
          <button className="button secondary" disabled={!contactId || preview.isPending} onClick={() => preview.mutate()}>
            <Search className="h-4 w-4" /> Preview Sync
          </button>
          <button className="button primary" disabled={!activePreview || confirm.isPending} onClick={() => confirm.mutate()}>
            <Check className="h-4 w-4" /> Confirm Dry-Run
          </button>
          <button className="button danger" disabled={!activePreview || cancel.isPending} onClick={() => cancel.mutate()}>
            <X className="h-4 w-4" /> Cancel Preview
          </button>
        </div>
      }
    >
      <div className="form-grid">
        <label>Provider<select value={provider} onChange={(event) => setProvider(event.target.value)}><option value="google_sheets">Google Sheets</option><option value="hubspot">HubSpot</option><option value="salesforce">Salesforce</option></select></label>
        <label>Contact<select value={contactId} onChange={(event) => setContactId(event.target.value)}>{contacts.map((contact) => <option value={contact.id} key={contact.id}>{contact.creator_name || contact.business_name || contact.email}</option>)}</select></label>
      </div>
      <div className="notice">
        Local dry-run only until OAuth/provider credentials are connected. Preview creates a diff without writing externally; confirm records an idempotent journal and attempt for audit.
      </div>
      <DataTable
        columns={["provider", "account", "status", "auth mode", "credential scope"]}
        rows={(summary?.connections ?? []).map((row) => [row.provider, row.account_label, row.status, row.auth_mode, row.scopes_redacted ?? "backend only"])}
      />
      <DataTable
        columns={["provider", "local field", "external field", "direction", "status"]}
        rows={(summary?.mappings ?? []).map((row) => [row.provider, row.local_field, row.external_field, row.direction, row.status])}
      />
      {activePreview && (
        <div className="notice">
          Active diff: {activePreview.provider} {activePreview.action} for {activePreview.entity_type}. Confirmation required: {String(activePolicy.requires_confirmation)}. External write in this phase: {activePolicy.dry_run_only ? "local dry-run only" : "backend-managed"}.
        </div>
      )}
      <DataTable columns={["local field", "external field", "before", "after", "status"]} rows={previewRows} />
      <DataTable
        columns={["provider", "entity", "action", "status", "idempotency"]}
        rows={(summary?.previews ?? []).map((row) => [row.provider, row.entity_type, row.action, row.status, row.idempotency_key.slice(0, 12)])}
      />
      <DataTable
        columns={["provider", "status", "response", "error", "external id", "idempotency"]}
        rows={(summary?.attempts ?? []).map((row) => [row.provider, row.status, row.response_code ?? "", row.error_code ?? "", row.external_id ?? "", row.idempotency_key.slice(0, 12)])}
      />
      <DataTable
        columns={["provider", "entity", "action", "status", "external id", "idempotency"]}
        rows={(summary?.journals ?? []).map((row) => [row.provider, row.entity_type, row.action, row.status, row.external_id ?? "", row.idempotency_key.slice(0, 12)])}
      />
    </Panel>
  );
}

function ImportPanel({ contactsTotal }: { contactsTotal: number }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [notes, setNotes] = useState("");
  const [tags, setTags] = useState("");
  const [info, setInfo] = useState("");
  const [paste, setPaste] = useState("");
  const [fileName, setFileName] = useState("");
  const [fileContent, setFileContent] = useState("");
  const [fileFormat, setFileFormat] = useState<"csv" | "txt" | "">("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [committed, setCommitted] = useState<ImportCommit | null>(null);
  const buildPreviewPayload = () => ({
    format: fileContent ? fileFormat : paste ? "paste" : "manual",
    rows: fileContent || paste
      ? undefined
      : [{ email, creator_name: name, website_url: website, notes, tags, personalization: info, source: "manual" }],
    content: fileContent || paste || undefined,
    filename: fileName || undefined
  });
  const previewMutation = useMutation({
    mutationFn: (payload?: Record<string, unknown>) => api.previewImport(payload ?? buildPreviewPayload()),
    onSuccess: (result) => {
      setPreview(result);
      setCommitted(null);
    },
    onError: (error) => toast(apiErrorMessage(error, "import preview failed"))
  });
  async function handleFileSelect(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    const format = file.name.toLowerCase().endsWith(".csv") ? "csv" : "txt";
    setFileName(file.name);
    setFileContent(text);
    setFileFormat(format);
    setPaste("");
    previewMutation.mutate({ format, content: text, filename: file.name });
  }
  const commit = useMutation({
    mutationFn: (id: string) => api.commitImport(id),
    onSuccess: (result) => {
      setPreview(null);
      setCommitted(result);
      toast(importSummaryToast(result.summary));
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "import commit failed"))
  });
  return (
    <Panel title="Import" icon={FileUp} action={<span className="text-sm text-muted">{contactsTotal} contacts</span>}>
      <div className="form-grid">
        <label>Email<input value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Creator Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>Website<input value={website} onChange={(e) => setWebsite(e.target.value)} /></label>
        <label>Notes<input value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
        <label>Tags<input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="udemy-creator, coach" /></label>
        <label>Info / AI Context<input value={info} onChange={(e) => setInfo(e.target.value)} placeholder="What should the AI know about this creator?" /></label>
      </div>
      <label className="mt-4 block">
        Upload CSV/TXT
        <input type="file" accept=".csv,.txt" onChange={handleFileSelect} />
      </label>
      {fileName && <div className="mt-2 text-sm text-muted">Loaded {fileName}</div>}
      <label className="mt-4 block">Paste<textarea rows={4} value={paste} onChange={(e) => setPaste(e.target.value)} /></label>
      <div className="mt-4 flex gap-2">
        <button className={`button secondary${previewMutation.isPending ? " is-loading" : ""}`} disabled={previewMutation.isPending || commit.isPending} onClick={() => previewMutation.mutate(undefined)}>
          <RefreshCw className={previewMutation.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> <span>{previewMutation.isPending ? "Previewing..." : "Preview"}</span>
        </button>
        <button className={`button primary${commit.isPending ? " is-loading" : ""}`} disabled={!preview || commit.isPending || previewMutation.isPending} onClick={() => preview && commit.mutate(preview.batch_id_temp)}>
          <Check className={commit.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> <span>{commit.isPending ? "Committing..." : "Commit"}</span>
        </button>
        <button className="button secondary" disabled={!preview || previewMutation.isPending || commit.isPending} onClick={() => setPreview(null)}>
          <X className="h-4 w-4" /> Cancel Preview
        </button>
      </div>
      {committed && <ImportSummary summary={committed.summary} />}
      {(committed ?? preview) && <ImportRows rows={(committed ?? preview)!.rows} />}
    </Panel>
  );
}

function tagsFor(contact: Contact): string[] {
  return Array.isArray(contact.custom_fields?.tags) ? contact.custom_fields.tags : [];
}

function autoReplyLabel(value?: Contact["auto_reply_override"]) {
  if (value === "enabled") return "On";
  if (value === "disabled") return "Off";
  if (value === "propose") return "Propose";
  return "Inherit";
}

function autoReplyClass(value?: Contact["auto_reply_override"]) {
  if (value === "enabled") return "auto-reply-badge on";
  if (value === "disabled") return "auto-reply-badge off";
  if (value === "propose") return "auto-reply-badge propose";
  return "auto-reply-badge inherit";
}

const AUTO_REPLY_OPTIONS = [
  { value: "", label: "Inherit" },
  { value: "enabled", label: "On" },
  { value: "disabled", label: "Off" },
  { value: "propose", label: "Propose" }
] as const;

type AutoReplyOptionValue = (typeof AUTO_REPLY_OPTIONS)[number]["value"];
type AutoReplyControlOption = { value: AutoReplyOptionValue; label: string };
const AUTO_REPLY_BINARY_OPTIONS: readonly AutoReplyControlOption[] = [
  { value: "enabled", label: "On" },
  { value: "disabled", label: "Off" }
];

function AutoReplyButtons({
  value,
  disabled,
  label,
  onChange,
  options = AUTO_REPLY_OPTIONS
}: {
  value: AutoReplyOptionValue;
  disabled?: boolean;
  label: string;
  onChange: (value: AutoReplyOptionValue) => void;
  options?: readonly AutoReplyControlOption[];
}) {
  return (
    <div className="auto-reply-control" role="group" aria-label={`Auto-reply setting for ${label}`}>
      {options.map((option) => (
        <button
          key={option.value || "inherit"}
          type="button"
          className={value === option.value ? "active" : ""}
          data-value={option.value || "inherit"}
          disabled={disabled}
          aria-pressed={value === option.value}
          title={`Set auto-reply ${option.label.toLowerCase()} for ${label}`}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function deletionWindowText(deletedAt?: string | null) {
  if (!deletedAt) return "7 days";
  const until = new Date(new Date(deletedAt).getTime() + 7 * 24 * 60 * 60 * 1000);
  return until.toLocaleString();
}

function ContactsPanel({
  contacts,
  recentlyDeletedContacts,
  templates,
  settings,
  navigate,
  verifications
}: {
  contacts: Contact[];
  recentlyDeletedContacts: Contact[];
  templates: TemplateRow[];
  settings?: SettingsRead;
  navigate: (surface: Surface) => void;
  verifications: EmailVerification[];
}) {
  const queryClient = useQueryClient();
  const verificationLookup = verificationMaps(verifications);
  const allTags = Array.from(new Set(contacts.flatMap(tagsFor))).sort();
  const [tagFilter, setTagFilter] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkProvider, setBulkProvider] = useState("groq");
  const [bulkTone, setBulkTone] = useState(settings?.sender_tone ?? "Professional");
  const [bulkTag, setBulkTag] = useState("");
  const [bulkMode, setBulkMode] = useState<BulkDraftMode>("ai_only");
  const [bulkTemplateId, setBulkTemplateId] = useState("");
  const [bulkInstruction, setBulkInstruction] = useState("");
  const [bulkJob, setBulkJob] = useState<BulkJob | null>(null);
  const [selectedContactIds, setSelectedContactIds] = useState<string[]>([]);
  const [deleteConfirmContacts, setDeleteConfirmContacts] = useState<Contact[]>([]);
  const [evidenceContactId, setEvidenceContactId] = useState<string | null>(null);
  useEffect(() => {
    setBulkTone(settings?.sender_tone ?? "Professional");
  }, [settings?.sender_tone]);
  const filteredContacts = tagFilter ? contacts.filter((contact) => tagsFor(contact).includes(tagFilter)) : contacts;
  const filteredContactIds = filteredContacts.map((contact) => contact.id);
  const selectedContacts = contacts.filter((contact) => selectedContactIds.includes(contact.id));
  const allVisibleSelected = filteredContacts.length > 0 && filteredContactIds.every((id) => selectedContactIds.includes(id));
  const bulkDraftStatuses = new Set(["imported", "draft_needed", "draft_ready"]);
  const draftableContacts = contacts.filter((contact) => bulkDraftStatuses.has(contact.status));
  const selectedDraftableContacts = selectedContacts.filter((contact) => bulkDraftStatuses.has(contact.status));
  const selectedExcludedCount = Math.max(0, selectedContacts.length - selectedDraftableContacts.length);
  const bulkSourceContacts = selectedDraftableContacts.length ? selectedDraftableContacts : draftableContacts;
  const bulkContacts = bulkSourceContacts.filter((contact) => !bulkTag || tagsFor(contact).includes(bulkTag));
  const bulkSourceLabel = selectedDraftableContacts.length ? `${selectedDraftableContacts.length} selected contact${selectedDraftableContacts.length === 1 ? "" : "s"}` : "all draftable contacts";
  const needsBulkTemplate = bulkMode !== "ai_only";
  const bulkTemplateMissing = needsBulkTemplate && !bulkTemplateId;
  const selectedBulkTemplate = templates.find((template) => template.id === bulkTemplateId);
  const selectedDraftButtonTitle = selectedContacts.length && !selectedDraftableContacts.length
    ? "Selected contacts are already sent, paused, blocked, deleted, or otherwise not draftable."
    : selectedExcludedCount
      ? `${selectedExcludedCount} selected contact${selectedExcludedCount === 1 ? "" : "s"} will be excluded because their status is not draftable.`
      : "Generate drafts for selected draftable contacts.";
  const evidenceContact = contacts.find((contact) => contact.id === evidenceContactId);
  const evidenceDrawer = useQuery({
    queryKey: ["contact-evidence-drawer", evidenceContactId],
    queryFn: () => api.getContactEvidence(evidenceContactId || ""),
    enabled: Boolean(evidenceContactId)
  });
  const verificationDrawer = useQuery({
    queryKey: ["contact-verification", evidenceContactId],
    queryFn: () => api.getContactVerification(evidenceContactId || ""),
    enabled: Boolean(evidenceContactId)
  });
  useEffect(() => {
    setSelectedContactIds((current) => current.filter((id) => contacts.some((contact) => contact.id === id)));
  }, [contacts]);
  const updateAutoReply = useMutation({
    mutationFn: ({ id, value }: { id: string; value: AutoReplyOptionValue }) => api.patchContact(id, { auto_reply_override: value || null }),
    onSuccess: () => {
      toast("contact auto-reply updated");
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "auto-reply update failed"))
  });
  const deleteContact = useMutation({
    mutationFn: async (items: Contact[]) => {
      await Promise.all(items.map((item) => api.deleteContact(item.id)));
      return items.length;
    },
    onSuccess: (count) => {
      toast(`${count} contact${count === 1 ? "" : "s"} moved to recently deleted`);
      setDeleteConfirmContacts([]);
      setSelectedContactIds([]);
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "contact delete failed"))
  });
  const restoreContact = useMutation({
    mutationFn: (id: string) => api.restoreContact(id),
    onSuccess: () => {
      toast("contact restored");
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "contact restore failed"))
  });
  const verifySelected = useMutation({
    mutationFn: () => api.runSelectedVerification(selectedContactIds),
    onSuccess: () => {
      toast(`${selectedContactIds.length} contact${selectedContactIds.length === 1 ? "" : "s"} verified`);
      void queryClient.invalidateQueries({ queryKey: ["verifications"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "selected verification failed"))
  });
  const startBulk = useMutation({
    mutationFn: () => api.generateBulkDrafts({
      contact_ids: bulkContacts.map((contact) => contact.id),
      provider: bulkProvider,
      tone: bulkTone,
      mode: bulkMode,
      template_id: needsBulkTemplate ? bulkTemplateId : undefined,
      instruction: bulkInstruction.trim() || undefined
    }),
    onSuccess: (job) => {
      setBulkJob(job);
      toast(`Generating drafts... ${job.completed} / ${job.total} complete`);
    },
    onError: (error) => toast(apiErrorMessage(error, "bulk generation failed"))
  });
  useEffect(() => {
    if (!bulkJob || bulkJob.status !== "running") return;
    const timer = window.setInterval(async () => {
      const next = await api.getBulkDraftStatus(bulkJob.job_id);
      setBulkJob(next);
      if (next.status === "completed") {
        window.clearInterval(timer);
        toast(`${next.generated} drafts generated: ${next.created ?? 0} created, ${next.updated ?? 0} updated, ${next.skipped} skipped, ${next.failed} failed.`);
        setBulkOpen(false);
        invalidateAll(queryClient);
        navigate("drafts");
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [bulkJob, navigate, queryClient]);
  return (
    <Panel
      title="Contacts"
      icon={Users}
      action={
        draftableContacts.length >= 2 ? (
          <button className="button primary" onClick={() => { setBulkJob(null); setBulkOpen(true); }}>
            <Sparkles className="h-4 w-4" /> Generate Cluster Drafts
          </button>
        ) : null
      }
    >
      <div className="mb-4 max-w-xs">
        <label>Tag Filter<select value={tagFilter} onChange={(e) => setTagFilter(e.target.value)}><option value="">All tags</option>{allTags.map((tag) => <option key={tag}>{tag}</option>)}</select></label>
      </div>
      <div className="contacts-selection-bar">
        <span>{selectedContacts.length} selected</span>
        <button
          className="button secondary"
          type="button"
          disabled={!filteredContacts.length}
          onClick={() => setSelectedContactIds(allVisibleSelected ? selectedContactIds.filter((id) => !filteredContactIds.includes(id)) : Array.from(new Set([...selectedContactIds, ...filteredContactIds])))}
        >
          {allVisibleSelected ? "Clear visible" : "Select visible"}
        </button>
        <button
          className="button secondary"
          type="button"
          disabled={!selectedContactIds.length}
          onClick={() => setSelectedContactIds([])}
        >
          Clear all
        </button>
        <button
          className="button primary"
          type="button"
          disabled={!selectedDraftableContacts.length || startBulk.isPending || bulkJob?.status === "running"}
          title={selectedDraftButtonTitle}
          onClick={() => {
            setBulkTag("");
            setBulkJob(null);
            setBulkOpen(true);
          }}
        >
          <Sparkles className="h-4 w-4" /> Generate selected
        </button>
        <button
          className="button secondary"
          type="button"
          disabled={!selectedContactIds.length || verifySelected.isPending}
          onClick={() => verifySelected.mutate()}
        >
          <Check className={verifySelected.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Verify selected
        </button>
        <button
          className="button danger"
          type="button"
          disabled={!selectedContacts.length || deleteContact.isPending}
          onClick={() => setDeleteConfirmContacts(selectedContacts)}
        >
          <Trash2 className="h-4 w-4" /> Delete selected
        </button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>
                <input
                  aria-label="Select all visible contacts"
                  checked={allVisibleSelected}
                  disabled={!filteredContacts.length}
                  type="checkbox"
                  onChange={() => setSelectedContactIds(allVisibleSelected ? selectedContactIds.filter((id) => !filteredContactIds.includes(id)) : Array.from(new Set([...selectedContactIds, ...filteredContactIds])))}
                />
              </th>
              <th>email</th><th>verification</th><th>name</th><th>website</th><th>info</th><th>status</th><th>source</th><th>tags</th><th>auto-reply</th><th>actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredContacts.map((row) => (
              <tr key={row.id}>
                <td className="selection-cell">
                  <input
                    aria-label={`Select ${row.email}`}
                    checked={selectedContactIds.includes(row.id)}
                    type="checkbox"
                    onChange={(event) => {
                      setSelectedContactIds((current) => event.target.checked ? Array.from(new Set([...current, row.id])) : current.filter((id) => id !== row.id));
                    }}
                  />
                </td>
                <td>{row.email}</td>
                <td><VerificationBadge verification={verificationLookup.byContactId.get(row.id) ?? verificationLookup.byEmail.get(row.email.toLowerCase())} /></td>
                <td>{row.creator_name || row.business_name || ""}</td>
                <td>{truncateDisplay(row.website_url ?? "", 80)}</td>
                <td>{truncateDisplay(row.personalization || row.notes || "", 120)}</td>
                <td>{row.status}</td>
                <td>{row.source}</td>
                <td>{tagsFor(row).join(", ")}</td>
                <td>
                  <div className="contact-auto-cell">
                    <span className={autoReplyClass(row.auto_reply_override)}>{autoReplyLabel(row.auto_reply_override)}</span>
                    <AutoReplyButtons
                      value={row.auto_reply_override ?? ""}
                      disabled={updateAutoReply.isPending}
                      label={row.email}
                      onChange={(value) => updateAutoReply.mutate({ id: row.id, value })}
                    />
                  </div>
                </td>
                <td>
                  <button
                    className="button secondary"
                    type="button"
                    title={`Open evidence for ${row.email}`}
                    onClick={() => setEvidenceContactId(row.id)}
                  >
                    <Database className="h-4 w-4" /> Evidence
                  </button>
                  <button
                    className="button danger"
                    type="button"
                    disabled={deleteContact.isPending}
                    title={`Move ${row.email} to Recently Deleted for 7 days`}
                    onClick={() => setDeleteConfirmContacts([row])}
                  >
                    <Trash2 className="h-4 w-4" /> Delete
                  </button>
                </td>
              </tr>
            ))}
            {!filteredContacts.length && (
              <tr>
                <td colSpan={11}>No active contacts match this filter.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {deleteConfirmContacts.length > 0 && (
        <div className="modal-backdrop">
          <div className="modal contact-delete-modal" role="dialog" aria-modal="true" aria-labelledby="contact-delete-title">
            <h3 id="contact-delete-title">Move contacts to Recently Deleted?</h3>
            <p>
              {deleteConfirmContacts.length} contact{deleteConfirmContacts.length === 1 ? "" : "s"} will leave the active Contacts list and remain in Recently Deleted for 7 days.
              Pending queue entries and due follow-ups for them will be stopped.
            </p>
            <div className="delete-contact-list">
              {deleteConfirmContacts.slice(0, 8).map((contact) => (
                <div key={contact.id}>{contact.email}</div>
              ))}
              {deleteConfirmContacts.length > 8 && <div>...and {deleteConfirmContacts.length - 8} more</div>}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button className="button secondary" type="button" disabled={deleteContact.isPending} onClick={() => setDeleteConfirmContacts([])}>Cancel</button>
              <button className="button danger" type="button" disabled={deleteContact.isPending} onClick={() => deleteContact.mutate(deleteConfirmContacts)}>
                <Trash2 className="h-4 w-4" /> Delete {deleteConfirmContacts.length === 1 ? "contact" : "contacts"}
              </button>
            </div>
          </div>
        </div>
      )}
      {evidenceContactId && (
        <div className="evidence-drawer-backdrop" role="presentation" onClick={() => setEvidenceContactId(null)}>
          <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="contact-evidence-title" onClick={(event) => event.stopPropagation()}>
            <div className="evidence-drawer-head">
              <div>
                <h3 id="contact-evidence-title">Evidence Ledger</h3>
                <p>{evidenceContact?.email ?? "Contact"}</p>
              </div>
              <button className="button secondary compact" type="button" title="Close evidence drawer" onClick={() => setEvidenceContactId(null)}>
                <X className="h-4 w-4" />
              </button>
            </div>
            {evidenceDrawer.isLoading && <div className="notice compact">Loading evidence...</div>}
            {evidenceDrawer.isError && <div className="notice warning compact">Evidence could not be loaded.</div>}
            {evidenceDrawer.data && (
              <div className="evidence-drawer-body">
                <VerificationDetailPanel detail={verificationDrawer.data} isLoading={verificationDrawer.isFetching} />
                <EvidenceCheckSummary check={evidenceDrawer.data.evidence_check} />
                <EvidenceFactList title="Lead Facts" facts={evidenceDrawer.data.lead_facts} />
                <EvidenceFactList title="Account Facts" facts={evidenceDrawer.data.account_facts} />
              </div>
            )}
          </aside>
        </div>
      )}
      <div className="deleted-section">
        <div className="section-head compact">
          <div>
            <h3>Recently Deleted</h3>
            <p>Contacts stay here for 7 days before they leave this view.</p>
          </div>
          <span className="pill neutral">{recentlyDeletedContacts.length} contacts</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>email</th><th>name</th><th>deleted</th><th>available until</th><th>actions</th></tr>
            </thead>
            <tbody>
              {recentlyDeletedContacts.map((row) => (
                <tr key={row.id}>
                  <td>{row.email}</td>
                  <td>{row.creator_name || row.business_name || ""}</td>
                  <td>{row.deleted_at ? new Date(row.deleted_at).toLocaleString() : ""}</td>
                  <td>{deletionWindowText(row.deleted_at)}</td>
                  <td>
                    <button
                      className="button secondary"
                      type="button"
                      disabled={restoreContact.isPending}
                      title={`Restore ${row.email}`}
                      onClick={() => restoreContact.mutate(row.id)}
                    >
                      <RotateCcw className="h-4 w-4" /> Restore
                    </button>
                  </td>
                </tr>
              ))}
              {!recentlyDeletedContacts.length && (
                <tr>
                  <td colSpan={5}>No contacts deleted in the last 7 days.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      {bulkOpen && (
        <div className="modal-backdrop">
          <div className="modal bulk-draft-modal">
            <h3>Generate Cluster Drafts</h3>
            <div className="grid gap-3">
              <label>Mode
                <select value={bulkMode} onChange={(e) => setBulkMode(e.target.value as BulkDraftMode)}>
                  <option value="ai_only">AI only</option>
                  <option value="template_only">Template only</option>
                  <option value="template_plus_ai">Template + AI</option>
                </select>
              </label>
              {needsBulkTemplate && (
                <label>Template
                  <select value={bulkTemplateId} onChange={(e) => setBulkTemplateId(e.target.value)}>
                    <option value="">Select a template</option>
                    {templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
                  </select>
                </label>
              )}
              <label>Provider<select value={bulkProvider} onChange={(e) => setBulkProvider(e.target.value)}><option>groq</option><option>gemini</option><option>auto</option></select></label>
              <label>Tone Override<select value={bulkTone} onChange={(e) => setBulkTone(e.target.value)}><option>Professional</option><option>Friendly</option><option>Casual</option><option>Direct</option><option>Storytelling</option></select></label>
              <label>Tag Filter<select value={bulkTag} onChange={(e) => setBulkTag(e.target.value)}><option value="">All in cluster</option>{allTags.map((tag) => <option key={tag}>{tag}</option>)}</select></label>
              <label>Instruction
                <textarea
                  rows={3}
                  value={bulkInstruction}
                  onChange={(e) => setBulkInstruction(e.target.value)}
                  placeholder="Optional angle, constraint, or CTA for this batch"
                />
              </label>
              <div className="notice">Cluster source: {bulkSourceLabel}</div>
              {selectedExcludedCount > 0 && <div className="notice warning">{selectedExcludedCount} selected contact{selectedExcludedCount === 1 ? "" : "s"} excluded because their status is not draftable.</div>}
              {bulkTemplateMissing && <div className="notice warning">Choose a template before starting this bulk mode.</div>}
              {selectedBulkTemplate && (
                <div className="notice compact">
                  Template: {selectedBulkTemplate.name}. Tokens such as {"{{first_name}}"}, {"{{full_name}}"}, {"{{website}}"}, notes, tags, and custom fields resolve per contact.
                </div>
              )}
              <div className="notice">Will generate drafts for {bulkContacts.length} contacts. Existing unapproved drafts are skipped.</div>
              {bulkJob && <div className="notice">Generating drafts... {bulkJob.completed} / {bulkJob.total} complete. {bulkJob.generated} generated, {bulkJob.skipped} skipped.</div>}
              {bulkJob?.errors?.length ? <div className="notice warning">Recent job notices: {bulkJob.errors.slice(0, 3).join(", ")}</div> : null}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button className="button secondary" onClick={() => setBulkOpen(false)}>Cancel</button>
              <button className="button primary" disabled={!bulkContacts.length || bulkTemplateMissing || startBulk.isPending || bulkJob?.status === "running"} onClick={() => startBulk.mutate()}>
                <Sparkles className={startBulk.isPending || bulkJob?.status === "running" ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Start Generation
              </button>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

function tokenText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean).join(", ");
  if (["string", "number", "boolean"].includes(typeof value)) return String(value).trim();
  return "";
}

function emailLocalName(email?: string): string {
  return (email ?? "").split("@", 1)[0].replace(/[._+-]+/g, " ").trim();
}

function resolveTokensForContact(text: string, contact?: Contact): string {
  if (!contact) return text;
  const displayName = tokenText(contact.creator_name) || tokenText(contact.business_name) || emailLocalName(contact.email) || "there";
  const firstName = displayName.split(" ", 1)[0] || "there";
  const values: Record<string, string> = {
    email: tokenText(contact.email),
    first_name: firstName,
    full_name: displayName,
    name: displayName,
    creator_name: tokenText(contact.creator_name) || displayName,
    business_name: tokenText(contact.business_name),
    company: tokenText(contact.business_name),
    website: tokenText(contact.website_url) || "your work",
    website_url: tokenText(contact.website_url) || "your work",
    niche: tokenText(contact.lead_category) || "your work",
    lead_category: tokenText(contact.lead_category) || "your work",
    notes: tokenText(contact.notes),
    personalization: tokenText(contact.personalization),
    source: tokenText(contact.source)
  };
  Object.entries(contact.custom_fields ?? {}).forEach(([key, value]) => {
    const normalizedKey = key.trim().toLowerCase();
    if (normalizedKey && !(normalizedKey in values)) values[normalizedKey] = tokenText(value);
  });
  return (text || "").replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (match, token: string) => {
    const key = token.toLowerCase();
    return key in values ? values[key] : match;
  });
}

function DraftsPanel({ contacts, drafts, templates, queue, verifications }: { contacts: Contact[]; drafts: Draft[]; templates: TemplateRow[]; queue: QueueEntry[]; verifications: EmailVerification[] }) {
  const queryClient = useQueryClient();
  const [contactId, setContactId] = useState(contacts[0]?.id ?? "");
  const [provider, setProvider] = useState("auto");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [instruction, setInstruction] = useState("");
  const [activeDraftId, setActiveDraftId] = useState("");
  const [selectedDrafts, setSelectedDrafts] = useState<string[]>([]);
  const [generateMessage, setGenerateMessage] = useState("");
  const [evidenceDraft, setEvidenceDraft] = useState({ subject, body });
  useEffect(() => {
    if (!contactId && contacts[0]?.id) setContactId(contacts[0].id);
  }, [contacts, contactId]);
  useEffect(() => {
    const timeout = window.setTimeout(() => setEvidenceDraft({ subject, body }), 350);
    return () => window.clearTimeout(timeout);
  }, [subject, body]);
  useEffect(() => {
    if (!generateMessage) return undefined;
    const timeout = window.setTimeout(() => setGenerateMessage(""), 5000);
    return () => window.clearTimeout(timeout);
  }, [generateMessage]);
  const selectedContact = contacts.find((item) => item.id === contactId);
  const activeDraft = activeDraftId ? drafts.find((draft) => draft.id === activeDraftId) : undefined;
  const verificationLookup = verificationMaps(verifications);
  const selectedVerificationRow = selectedContact ? verificationLookup.byContactId.get(selectedContact.id) ?? verificationLookup.byEmail.get(selectedContact.email.toLowerCase()) : undefined;
  const selectedVerification = useQuery({
    queryKey: ["contact-verification", contactId],
    queryFn: () => api.getContactVerification(contactId),
    enabled: Boolean(contactId)
  });
  const runDraftVerification = useMutation({
    mutationFn: () => api.runContactVerification(contactId),
    onSuccess: () => {
      toast("recipient verification updated");
      void queryClient.invalidateQueries({ queryKey: ["contact-verification", contactId] });
      void queryClient.invalidateQueries({ queryKey: ["verifications"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "recipient verification failed"))
  });
  const draftEvidence = useQuery({
    queryKey: ["draft-evidence-check", contactId, evidenceDraft.subject, evidenceDraft.body],
    queryFn: () => api.checkContactEvidence(contactId, evidenceDraft),
    enabled: Boolean(contactId && (evidenceDraft.subject.trim() || evidenceDraft.body.trim())),
    staleTime: 1000
  });
  const unapproved = drafts.filter((draft) => !draft.approved);
  const selectedAll = unapproved.length > 0 && unapproved.every((draft) => selectedDrafts.includes(draft.id));
  const create = useMutation({
    mutationFn: () =>
      activeDraftId
        ? api.updateDraft(activeDraftId, { subject, body })
        : api.createDraft({ contact_id: contactId, subject, body }),
    onSuccess: (draft) => {
      setActiveDraftId(draft.id);
      setSubject(resolveTokensForContact(draft.subject, selectedContact));
      setBody(resolveTokensForContact(draft.body, selectedContact));
      toast(activeDraftId ? "draft updated" : "draft saved");
      invalidateAll(queryClient);
    }
  });
  const generate = useMutation({
    mutationFn: () => api.generateDraft({ contact_id: contactId, provider, instruction: instruction.trim() || undefined }),
    onMutate: () => {
      setGenerateMessage("");
    },
    onSuccess: (draft) => {
      if (draft.error_code) toast(draft.error_code);
      else {
        setActiveDraftId(draft.id);
        setSubject(resolveTokensForContact(draft.subject, selectedContact));
        setBody(resolveTokensForContact(draft.body, selectedContact));
        setGenerateMessage("Draft generated in the editor.");
      }
      invalidateAll(queryClient);
    },
    onError: () => {
      setGenerateMessage("Draft generation failed. Check the provider settings and try again.");
      toast("draft generation failed");
    }
  });
  const approve = useMutation({
    mutationFn: ({ draftId, sequenceNum }: { draftId: string; sequenceNum?: number }) => api.approveDraft(draftId, sequenceNum ? { sequence_num: sequenceNum } : undefined),
    onSuccess: (result, variables) => {
      toast(deliveryToastMessage(result, variables.sequenceNum ? `Follow-up #${variables.sequenceNum} approved for delivery.` : "Draft approved for delivery."));
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "draft approval failed"))
  });
  const approveEditorDraft = useMutation({
    mutationFn: async () => {
      if (!activeDraftId) throw new Error("no active draft");
      await api.updateDraft(activeDraftId, { subject, body });
      return api.approveDraft(activeDraftId, editorApproveSequence ? { sequence_num: editorApproveSequence } : undefined);
    },
    onSuccess: (result) => {
      toast(deliveryToastMessage(result, editorApproveSequence ? `Follow-up #${editorApproveSequence} saved and approved for delivery.` : "Draft saved and approved for delivery."));
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "draft approval failed"))
  });
  const updateAutoReply = useMutation({
    mutationFn: ({ id, value }: { id: string; value: AutoReplyOptionValue }) => api.patchContact(id, { auto_reply_override: value || null }),
    onSuccess: () => {
      toast("contact auto-reply updated");
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["auto-reply-pending"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "auto-reply update failed"))
  });
  const approveBulk = useMutation({
    mutationFn: () => api.approveBulkDrafts(selectedDrafts),
    onSuccess: (result) => {
      toast(bulkApprovalToast(result));
      setSelectedDrafts([]);
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "bulk approval failed"))
  });
  const approveBulkAndSend = useMutation({
    mutationFn: () => api.approveBulkDraftsAndSend(selectedDrafts),
    onSuccess: (result) => {
      toast(bulkApprovalToast(result));
      setSelectedDrafts([]);
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "bulk approve and send failed"))
  });
  function toggleAllUnapproved(checked: boolean) {
    setSelectedDrafts(checked ? unapproved.map((draft) => draft.id) : []);
  }
  function applyTemplate(id: string, targetContactId: string) {
    const template = templates.find((item) => item.id === id);
    if (!template) return;
    const contact = contacts.find((item) => item.id === targetContactId);
    setSubject(resolveTokensForContact(template.subject_template, contact));
    setBody(resolveTokensForContact(template.body_template, contact));
  }
  function changeContact(id: string) {
    setContactId(id);
    setActiveDraftId("");
  }
  function loadTemplate(id: string) {
    setTemplateId(id);
    setActiveDraftId("");
  }
  useEffect(() => {
    if (templateId) applyTemplate(templateId, contactId);
  }, [templateId, contactId, templates, contacts]);
  function nextSequenceForContact(targetContactId: string) {
    const contactQueue = queue.filter((row) => row.contact_id === targetContactId);
    const hasSentInitial = contactQueue.some((row) => row.sequence_num === 1 && row.status === "provider_accepted");
    if (!hasSentInitial) return undefined;
    const bySequence = new Map(contactQueue.map((row) => [row.sequence_num, row]));
    let sequenceNum = 2;
    while (true) {
      const row = bySequence.get(sequenceNum);
      if (!row) return sequenceNum;
      if (["failed", "blocked", "skipped", "simulated", "cancelled"].includes(row.status)) return sequenceNum;
      if (["provider_accepted", "sent"].includes(row.status)) {
        sequenceNum += 1;
        continue;
      }
      return undefined;
    }
  }
  const activeDraftApproved = Boolean(activeDraft?.approved);
  const editorApproveSequence = contactId ? nextSequenceForContact(contactId) : undefined;
  const editorApproveLabel = editorApproveSequence ? `Approve Follow-up #${editorApproveSequence}` : "Approve & Send";
  const evidenceBlocksApproval = Boolean(draftEvidence.data?.neutral_copy_required);
  const verificationBlocksApproval = selectedVerification.data?.policy.allowed === false;
  function applyNeutralCopy() {
    if (!draftEvidence.data) return;
    if (draftEvidence.data.neutral_subject) setSubject(draftEvidence.data.neutral_subject);
    if (draftEvidence.data.neutral_body) setBody(draftEvidence.data.neutral_body);
    setGenerateMessage("Neutral copy applied from the evidence check.");
  }
  return (
    <Panel
      title="Drafts"
      icon={Sparkles}
      action={
        <div className="flex flex-wrap items-center gap-2">
          <label className="toggle"><input type="checkbox" checked={selectedAll} onChange={(e) => toggleAllUnapproved(e.target.checked)} /> Select All Unapproved</label>
          <button className="button secondary" disabled={!selectedDrafts.length || approveBulk.isPending || approveBulkAndSend.isPending} onClick={() => approveBulk.mutate()}>
            <Check className={approveBulk.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Approve Selected
          </button>
          <button className="button primary" disabled={!selectedDrafts.length || approveBulk.isPending || approveBulkAndSend.isPending} onClick={() => approveBulkAndSend.mutate()}>
            <Mail className={approveBulkAndSend.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Approve & Send Selected
          </button>
        </div>
      }
    >
      <div className="drafts-layout">
        <section className="draft-composer" aria-label="Draft composer">
          <div className="form-grid draft-form-grid">
            <label>Contact<select value={contactId} onChange={(e) => changeContact(e.target.value)}>{contacts.map((item) => <option key={item.id} value={item.id}>{item.email}</option>)}</select></label>
            <label>Provider<select value={provider} onChange={(e) => setProvider(e.target.value)}><option>manual</option><option>auto</option><option>groq</option><option>gemini</option></select></label>
            <label>Load Template<select value={templateId} onChange={(e) => loadTemplate(e.target.value)}><option value="">No template</option>{templates.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          </div>
          <div className="segmented contact-segmented mt-3" aria-label="Contact">
            {contacts.map((item) => (
              <button key={item.id} className={contactId === item.id ? "active" : ""} onClick={() => changeContact(item.id)}>{item.email}</button>
            ))}
          </div>
          <div className="draft-editor-bar">
            <div className="segmented" aria-label="Provider">
              {["manual", "auto", "groq", "gemini"].map((item) => (
                <button key={item} className={provider === item ? "active" : ""} onClick={() => setProvider(item)}>{item}</button>
              ))}
            </div>
            {selectedContact && (
              <div className="draft-auto-reply">
                <span className={autoReplyClass(selectedContact.auto_reply_override)}>Auto-reply {autoReplyLabel(selectedContact.auto_reply_override)}</span>
                <AutoReplyButtons
                  value={selectedContact.auto_reply_override ?? ""}
                  disabled={updateAutoReply.isPending}
                  label={selectedContact.email}
                  options={AUTO_REPLY_BINARY_OPTIONS}
                  onChange={(value) => updateAutoReply.mutate({ id: selectedContact.id, value })}
                />
              </div>
            )}
          </div>
          <div className={`verification-gate ${verificationBlocksApproval ? "block" : "open"}`}>
            <div>
              <h3>Recipient Verification</h3>
              <p>Address validity gate only, not inbox placement.</p>
            </div>
            <div className="verification-gate-actions">
              <VerificationBadge verification={selectedVerification.data?.verification ?? selectedVerificationRow} policy={selectedVerification.data?.policy} />
              {selectedVerification.data?.policy.reason_code && <span className="fingerprint">{selectedVerification.data.policy.reason_code}</span>}
              <button className="button secondary compact" type="button" disabled={!contactId || runDraftVerification.isPending} onClick={() => runDraftVerification.mutate()}>
                <RefreshCw className={runDraftVerification.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Check
              </button>
            </div>
          </div>
          <label className="draft-instruction">
            Draft instruction
            <textarea
              rows={2}
              placeholder="Example: focus on the configured offer, keep it short, mention the relevant constraint"
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  if (contactId && !generate.isPending) generate.mutate();
                }
              }}
            />
          </label>
          <div className="draft-editor-fields">
            <input placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
            <textarea rows={9} placeholder="Body" value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
          <DraftEvidencePanel check={draftEvidence.data} isLoading={draftEvidence.isFetching} onUseNeutral={applyNeutralCopy} />
          <div className="draft-composer-actions">
            <button className={`button secondary${generate.isPending ? " is-loading" : ""}`} disabled={!contactId || generate.isPending} aria-busy={generate.isPending} onClick={() => generate.mutate()}>
              <Sparkles className={generate.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> <span>{generate.isPending ? "Generating..." : "Generate"}</span>
            </button>
            <button className="button primary" disabled={!contactId || create.isPending} onClick={() => create.mutate()}><Mail className={create.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Save Draft</button>
            <button className="button primary" disabled={!activeDraftId || activeDraftApproved || approveEditorDraft.isPending || evidenceBlocksApproval || verificationBlocksApproval} onClick={() => approveEditorDraft.mutate()}>
              <Check className={approveEditorDraft.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> {editorApproveLabel}
            </button>
          </div>
          {generate.isPending && (
            <div className="notice draft-generation-notice" role="status">
              Generating a draft with {provider}. This can take a few seconds.
              <div className="progress-strip" aria-hidden="true"><span /></div>
            </div>
          )}
          {!generate.isPending && generateMessage && <div className={`notice${generate.isError ? " warning" : ""}`} role="status">{generateMessage}</div>}
        </section>
        <section className="draft-library" aria-label="Draft library">
          <div className="draft-library-head">
            <div>
              <h3>Draft Library</h3>
              <p>{drafts.length} total, {unapproved.length} unapproved</p>
            </div>
            {selectedDrafts.length > 0 && <span className="fingerprint">{selectedDrafts.length} selected</span>}
          </div>
          <div className="draft-list-grid">
            {[...drafts].reverse().map((draft) => (
              <DraftRow
                key={draft.id}
                draft={draft}
                contact={contacts.find((contact) => contact.id === draft.contact_id)}
                selected={selectedDrafts.includes(draft.id)}
                onSelect={(checked) => setSelectedDrafts((current) => checked ? [...new Set([...current, draft.id])] : current.filter((id) => id !== draft.id))}
                approve={(draftId) => approve.mutate({ draftId, sequenceNum: nextSequenceForContact(draft.contact_id) })}
                approvePending={approve.isPending && approve.variables?.draftId === draft.id}
                approveSequence={nextSequenceForContact(draft.contact_id)}
                verification={verificationForContact(contacts.find((contact) => contact.id === draft.contact_id), verifications)}
                loadIntoComposer={(draftToLoad) => {
                  const contact = contacts.find((item) => item.id === draftToLoad.contact_id);
                  setContactId(draftToLoad.contact_id);
                  setActiveDraftId(draftToLoad.id);
                  setSubject(resolveTokensForContact(draftToLoad.subject, contact));
                  setBody(resolveTokensForContact(draftToLoad.body, contact));
                  setTemplateId("");
                }}
              />
            ))}
          </div>
        </section>
      </div>
    </Panel>
  );
}

function DraftRow({ draft, contact, selected, onSelect, approve, approvePending, approveSequence, verification, loadIntoComposer }: { draft: Draft; contact?: Contact; selected: boolean; onSelect: (checked: boolean) => void; approve: (draftId: string) => void; approvePending: boolean; approveSequence?: number; verification?: EmailVerification; loadIntoComposer: (draft: Draft) => void }) {
  const queryClient = useQueryClient();
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(resolveTokensForContact(draft.body, contact));
  const [variants, setVariants] = useState<string[]>([]);
  useEffect(() => {
    setSubject(resolveTokensForContact(draft.subject, contact));
    setBody(resolveTokensForContact(draft.body, contact));
  }, [draft.id, draft.subject, draft.body, contact]);
  const save = useMutation({
    mutationFn: () => api.updateDraft(draft.id, { subject, body }),
    onSuccess: () => {
      toast("draft saved");
      invalidateAll(queryClient);
    }
  });
  const suggest = useMutation({
    mutationFn: () => api.subjectVariants(draft.id),
    onSuccess: (result) => {
      if (result.error_code) toast(result.error_code);
      setVariants(result.variants);
    }
  });
  const saveTemplate = useMutation({
    mutationFn: (name: string) => api.createTemplate({ name, draft_id: draft.id }),
    onSuccess: () => {
      toast("template saved");
      invalidateAll(queryClient);
    }
  });
  function promptTemplateName() {
    const name = window.prompt("Template name");
    if (name?.trim()) saveTemplate.mutate(name.trim());
  }
  return (
    <div className="draft-card">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <label className="toggle"><input type="checkbox" disabled={draft.approved} checked={selected} onChange={(e) => onSelect(e.target.checked)} /> Select</label>
          <StatusPill value={draft.approved ? "approved" : "unapproved"} />
          {draft.ai_model && <span className="fingerprint">{draft.ai_model}</span>}
        </div>
        <button className="button secondary compact" type="button" onClick={() => loadIntoComposer(draft)}>Load</button>
      </div>
      <div className="draft-card-contact">
        <span>{contact?.email ?? "unknown contact"}{draft.approved_at ? ` - approved ${draft.approved_at}` : ""}</span>
        <VerificationBadge verification={verification} />
      </div>
      <label>Subject<input value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
      <div className="flex flex-wrap gap-2">
        <button className="button secondary" disabled={!draft.body || suggest.isPending} onClick={() => suggest.mutate()}>
          <Sparkles className={suggest.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Suggest 3 Subject Lines
        </button>
        {variants.map((variant) => (
          <button className="chip" key={variant} onClick={() => setSubject(variant)}>{variant}</button>
        ))}
      </div>
      <label>Body<textarea rows={4} value={body} onChange={(e) => setBody(e.target.value)} /></label>
      <div className="flex flex-wrap gap-2">
        <button className="button secondary" onClick={() => save.mutate()}>Save</button>
        <button className="button secondary" disabled={!draft.approved || saveTemplate.isPending} onClick={promptTemplateName}>Save as Template</button>
        <button className={`button primary${approvePending ? " is-loading" : ""}`} disabled={draft.approved || approvePending} onClick={() => approve(draft.id)}>
          <Check className={approvePending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> <span>{approvePending ? "Sending..." : approveSequence ? `Approve & Send Follow-up #${approveSequence}` : "Approve & Send"}</span>
        </button>
      </div>
    </div>
  );
}

function TemplatesPanel({ templates }: { templates: TemplateRow[] }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const create = useMutation({
    mutationFn: () => api.createTemplate({ name, subject_template: subject, body_template: body }),
    onSuccess: () => {
      toast("template saved");
      setName("");
      setSubject("");
      setBody("");
      invalidateAll(queryClient);
    }
  });
  return (
    <Panel title="Templates" icon={Library}>
      <div className="form-grid">
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>Subject Template<input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Idea for {{full_name}}" /></label>
      </div>
      <label className="mt-4 block">Body Template<textarea rows={5} value={body} onChange={(e) => setBody(e.target.value)} placeholder="Hi {{first_name}}, I noticed {{website}}..." /></label>
      <button className="button primary mt-4" disabled={!name.trim() || !subject.trim() || !body.trim() || create.isPending} onClick={() => create.mutate()}>
        <Library className={create.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Save Template
      </button>
      <DataTable columns={["name", "subject", "body"]} rows={templates.map((row) => [row.name, row.subject_template, row.body_template])} />
    </Panel>
  );
}

function CampaignsPanel({ campaigns, contacts, navigate }: { campaigns: CampaignPlan[]; contacts: Contact[]; navigate: (surface: Surface) => void }) {
  const queryClient = useQueryClient();
  const emptyStep = (purpose: string): CampaignStep => ({ subject: "", body: "", purpose });
  const [activeId, setActiveId] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string>("");
  const [activateTarget, setActivateTarget] = useState<CampaignPlan | null>(null);
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [targetTags, setTargetTags] = useState("");
  const [steps, setSteps] = useState<[CampaignStep, CampaignStep, CampaignStep]>([
    emptyStep("initial outreach"),
    emptyStep("value-add follow-up"),
    emptyStep("polite breakup email")
  ]);
  const activePlan = campaigns.find((campaign) => campaign.id === activeId);
  const hasStepContent = steps.some((step) => step.subject.trim() || step.body.trim() || step.purpose.trim());

  function loadPlan(plan: CampaignPlan) {
    setActiveId(plan.id);
    setName(plan.name);
    setGoal(plan.goal ?? "");
    setTargetTags(plan.target_tags ?? "");
    setSteps([plan.step_1_draft, plan.step_2_draft, plan.step_3_draft]);
  }

  function updateStep(index: number, patch: Partial<CampaignStep>) {
    setSteps((current) => {
      const next = [...current] as [CampaignStep, CampaignStep, CampaignStep];
      next[index] = { ...next[index], ...patch };
      return next;
    });
  }

  function tagSet(tags: string) {
    return new Set(tags.split(",").map((item) => item.trim().toLowerCase()).filter(Boolean));
  }

  function contactMatchesTags(contact: Contact, tags: string) {
    const required = tagSet(tags);
    if (!required.size) return true;
    const customFields = contact.custom_fields as Record<string, unknown> | undefined;
    const rawTags = customFields?.tags;
    const contactTags = Array.isArray(rawTags)
      ? rawTags.map((tag) => String(tag).toLowerCase())
      : typeof rawTags === "string"
        ? rawTags.split(",").map((tag) => tag.trim().toLowerCase())
        : [];
    return contactTags.some((tag) => required.has(tag));
  }

  function matchingContactCount(plan: CampaignPlan | null) {
    if (!plan) return 0;
    return contacts.filter((contact) => contactMatchesTags(contact, plan.target_tags ?? "")).length;
  }

  const create = useMutation({
    mutationFn: () => api.createCampaign({ name, goal, target_tags: targetTags }),
    onSuccess: (plan) => {
      toast("campaign sequence generated");
      loadPlan(plan);
      invalidateAll(queryClient);
    },
    onError: () => toast("campaign generation failed")
  });
  const save = useMutation({
    mutationFn: () =>
      api.updateCampaign(activeId, {
        name,
        goal,
        target_tags: targetTags,
        step_1_draft: steps[0],
        step_2_draft: steps[1],
        step_3_draft: steps[2],
        status: activePlan?.status ?? "draft"
      }),
    onSuccess: (plan) => {
      toast("campaign saved");
      loadPlan(plan);
      invalidateAll(queryClient);
    }
  });
  const activate = useMutation({
    mutationFn: (planId: string) => api.activateCampaign(planId),
    onSuccess: (result) => {
      toast(`${result.drafts_created} campaign drafts created`);
      setActivateTarget(null);
      invalidateAll(queryClient);
      navigate("drafts");
    }
  });

  return (
    <Panel title="Campaigns" icon={Megaphone}>
      <div className="campaign-layout">
        <section className="campaign-create">
          <h3>Create Campaign</h3>
          <div className="form-grid">
            <label>Name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
            <label>Target Tags<input value={targetTags} onChange={(event) => setTargetTags(event.target.value)} placeholder="tag_one, tag_two" /></label>
            <label>Status<input value={activePlan?.status ?? "draft"} readOnly /></label>
          </div>
          <label className="mt-4">Campaign Goal<textarea rows={4} value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
          <div className="mt-4 flex flex-wrap gap-2">
            <button className="button secondary" disabled={!name.trim() || !goal.trim() || create.isPending} onClick={() => create.mutate()}>
              <Sparkles className={create.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Generate 3-Step Sequence
            </button>
            <button className="button secondary" disabled={!activeId || save.isPending} onClick={() => save.mutate()}>
              Save Plan
            </button>
            <button className="button primary" disabled={!activeId || !steps[0].subject.trim() || !steps[0].body.trim() || activate.isPending} onClick={() => activePlan && setActivateTarget(activePlan)}>
              Activate Campaign
            </button>
          </div>
          {hasStepContent && (
            <div className="sequence-grid">
              {[
                ["Initial Email", 0],
                ["Follow-up 1", 1],
                ["Breakup Email", 2]
              ].map(([label, index]) => {
                const stepIndex = Number(index);
                const step = steps[stepIndex];
                return (
                  <article className="sequence-card" key={label}>
                    <div className="flex items-center justify-between gap-2">
                      <h3>{label}</h3>
                      <span className="fingerprint">{step.purpose || "operator fill"}</span>
                    </div>
                    <label>Subject<input value={step.subject} onChange={(event) => updateStep(stepIndex, { subject: event.target.value })} /></label>
                    <label>Body<textarea rows={7} value={step.body} onChange={(event) => updateStep(stepIndex, { body: event.target.value })} /></label>
                  </article>
                );
              })}
            </div>
          )}
        </section>
        <section className="campaign-list">
          <h3>Active Campaigns</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Name</th><th>Status</th><th>Contacts</th><th>Sent</th><th>Stopped</th><th>Created</th></tr>
              </thead>
              <tbody>
                {campaigns.map((campaign) => (
                  <Fragment key={campaign.id}>
                    <tr className="campaign-row" key={campaign.id} onClick={() => setExpandedId(expandedId === campaign.id ? "" : campaign.id)}>
                      <td>{campaign.name}</td>
                      <td><StatusPill value={campaign.status} /></td>
                      <td>{campaign.contacts_count}</td>
                      <td>{campaign.sent_count}</td>
                      <td>{campaign.stopped_count}</td>
                      <td>{campaign.created_at ?? ""}</td>
                    </tr>
                    {expandedId === campaign.id && (
                      <tr key={`${campaign.id}-expanded`}>
                        <td colSpan={6}>
                          <div className="campaign-expanded">
                            {[campaign.step_1_draft, campaign.step_2_draft, campaign.step_3_draft].map((step, index) => (
                              <button className="campaign-step-preview" key={`${campaign.id}-${index}`} onClick={() => loadPlan(campaign)}>
                                <strong>{step.subject || `Step ${index + 1}`}</strong>
                                <span>{step.purpose}</span>
                                <p>{step.body.slice(0, 200)}</p>
                              </button>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      {activateTarget && (
        <div className="modal-backdrop">
          <div className="modal campaign-activate-modal">
            <h3>Activate Campaign</h3>
            <p>This will create drafts for {matchingContactCount(activateTarget)} contacts. Proceed?</p>
            <div className="flex flex-wrap gap-2">
              <button className="button primary" disabled={activate.isPending} onClick={() => activate.mutate(activateTarget.id)}>Proceed</button>
              <button className="button secondary" onClick={() => setActivateTarget(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

function QueuePanel({ queue, contacts, drafts, verifications }: { queue: QueueEntry[]; contacts: Contact[]; drafts: Draft[]; verifications: EmailVerification[] }) {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("all");
  const [pendingAction, setPendingAction] = useState("");
  const contactById = new Map(contacts.map((contact) => [contact.id, contact]));
  const draftById = new Map(drafts.map((draft) => [draft.id, draft]));
  const verificationLookup = verificationMaps(verifications);
  const filteredQueue = statusFilter === "all" ? queue : queue.filter((row) => row.status === statusFilter);
  const statusCounts = queue.reduce<Record<string, number>>((acc, row) => {
    acc[row.status] = (acc[row.status] ?? 0) + 1;
    return acc;
  }, {});
  const process = useMutation({
    mutationFn: api.processQueue,
    onSuccess: (result) => {
      toast(queueProcessToast(result));
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "Queue processing failed"))
  });
  const clear = useMutation({
    mutationFn: api.clearQueue,
    onSuccess: (result) => {
      toast(`Queue cleared: ${result.cancelled} cancelled, ${result.preserved_accepted} accepted preserved, ${result.preserved_uncertain} uncertain preserved`);
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "Queue clear failed"))
  });
  const rowAction = useMutation({
    mutationFn: async ({ queueId, action }: { queueId: string; action: "send_now" | "retry" | "cancel" | "remove" | "cancel_uncertain" | "finalize_provider_accepted" }) => {
      setPendingAction(`${queueId}:${action}`);
      if (action === "send_now") return api.sendQueueNow(queueId);
      if (action === "retry") return api.retryQueue(queueId);
      if (action === "cancel") return api.cancelQueue(queueId);
      if (action === "remove") return api.deleteQueueEntry(queueId);
      return api.reconcileQueue(queueId, action === "cancel_uncertain" ? "cancel" : action);
    },
    onSuccess: (result, variables) => {
      const label = variables.action === "send_now"
        ? "send-now evaluated"
        : variables.action === "retry"
        ? "retry scheduled"
        : variables.action === "remove"
          ? "removed"
        : variables.action === "cancel" || variables.action === "cancel_uncertain"
          ? "cancelled"
          : "provider acceptance finalized";
      if (variables.action === "send_now" && "result" in result) {
        toast(queueProcessToast(result.result));
      } else {
        toast(`Queue ${label}: ${"status" in result ? result.status : result.delivery_status}`);
      }
      invalidateAll(queryClient);
    },
    onError: (error, variables) => toast(apiErrorMessage(error, `Queue ${variables.action.replace(/_/g, " ")} failed`)),
    onSettled: () => setPendingAction("")
  });
  return (
    <Panel
      title="Queue"
      icon={ListChecks}
      action={
        <div className="flex flex-wrap gap-2">
          <button
            className="button danger"
            disabled={clear.isPending || process.isPending || rowAction.isPending || !queue.some((row) => ["pending", "skipped", "simulated", "blocked", "failed"].includes(row.status))}
            onClick={() => window.confirm("Clear all cancellable Queue work? Accepted and uncertain provider records will be preserved.") && clear.mutate()}
          >
            <Trash2 className={clear.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Clear Queue
          </button>
          <button className="button primary" disabled={process.isPending || rowAction.isPending || clear.isPending} onClick={() => process.mutate()}>
            <RefreshCw className={process.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Process
          </button>
        </div>
      }
    >
      <div className="queue-toolbar">
        <div className="metrics queue-summary">
          {["pending", "processing", "simulated", "provider_accepted", "sent", "blocked", "failed", "reconciliation_required", "cancelled"].map((status) => (
            <Metric key={status} label={status} value={String(statusCounts[status] ?? 0)} />
          ))}
        </div>
        <label className="queue-filter">Status
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">All statuses</option>
            {Object.keys(statusCounts).sort().map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
      </div>
      <div className="notice compact">
        Pending items wait for the canary, send window, and spacing automatically. Retry is for failed or permanently blocked items where the provider was not contacted.
      </div>
      {!filteredQueue.length && <div className="notice">No queue entries match this status.</div>}
      <div className="queue-list">
        {filteredQueue.map((row) => {
          const contact = contactById.get(row.contact_id);
          const email = row.contact_email ?? contact?.email ?? row.contact_id;
          const verification = verificationLookup.byContactId.get(row.contact_id) ?? verificationLookup.byEmail.get(email.toLowerCase());
          const failedTrace = (row.policy_trace ?? []).filter((gate) => !gate.passed);
          const deliverabilityGate = failedTrace.find((gate) => deliverabilityGateNames.has(gate.gate));
          const failedTraceLabels = failedTrace.map((gate) => {
            const details = formatPolicyDetails(gate.details);
            return `${formatBlockReason(gate.reason_code)}${details ? ` (${details})` : ""}`;
          });
          const blockExplanation = row.classification_note || (failedTrace.length
            ? failedTraceLabels.join("; ")
            : row.policy_block_reasons.map(formatBlockReason).join("; ") || "none");
          const attempt = row.latest_attempt;
          const sendNowEligible = ["pending", "skipped", "simulated", "failed", "blocked", "cancelled"].includes(row.status) && (!attempt || attempt.provider_contacted === false) && attempt?.provider_accepted !== true;
          const retryable = ["failed", "blocked"].includes(row.status) && (!attempt || attempt.provider_contacted === false) && attempt?.provider_accepted !== true;
          const reconcilable = row.status === "reconciliation_required";
          const cancellable = ["pending", "skipped", "simulated", "blocked", "failed"].includes(row.status);
          const acceptanceEvidence = hasQueueAcceptanceEvidence(attempt);
          const canCancelReconciliation = reconcilable && !acceptanceEvidence;
          const isBusy = rowAction.isPending && pendingAction.startsWith(`${row.id}:`);
          const runAction = (action: "send_now" | "retry" | "cancel" | "remove" | "cancel_uncertain" | "finalize_provider_accepted") => {
            const requiresConfirmation = action !== "retry";
            const confirmed = !requiresConfirmation || window.confirm(
              action === "remove"
                ? "Remove this cancellable item from active Queue work? Its record will be retained as cancelled."
              : action === "cancel" || action === "cancel_uncertain"
                ? "Cancel this queue item? Finimatic will not retry it."
                : action === "send_now"
                  ? "Reevaluate current policy and send this queue item now if it passes?"
                : "Finalize this item as provider accepted using the persisted provider evidence?"
            );
            if (confirmed) rowAction.mutate({ queueId: row.id, action });
          };
          return (
            <article className="queue-card" key={row.id}>
              <div className="queue-card-head">
                <div>
                  <StatusPill value={row.status} />
                  {row.stored_status && row.stored_status !== row.status && <small>stored: {row.stored_status}</small>}
                </div>
                <div className="queue-card-title">
                  <strong>{email}</strong>
                  <span>{row.draft_subject ?? draftById.get(row.draft_id)?.subject ?? row.draft_id}</span>
                </div>
                <div className="queue-card-meta">
                  <span>Sequence <strong>{row.sequence_num}</strong></span>
                  <span>Scheduled <strong>{new Date(row.scheduled_at).toLocaleString()}</strong></span>
                  <span>Schedule source <strong>{row.schedule_source ?? "unknown"}</strong></span>
                  <span>Verification <strong>{verification?.status ?? "unknown"}</strong></span>
                </div>
              </div>
              <div className="queue-truth-grid">
                <span>Attempt status<strong>{attempt?.attempt_status ?? "not attempted"}</strong></span>
                <span>Attempt ID<strong>{attempt?.attempt_id ?? "none"}</strong></span>
                <span>Transport<strong>{attempt ? `${attempt.configured_transport} -> ${attempt.effective_transport}` : "not selected"}</strong></span>
                <span>Source<strong>{attempt?.transport_source ?? "none"}</strong></span>
                <span>Simulated<strong>{attempt ? (attempt.simulated ? "yes" : "no") : "unknown"}</strong></span>
                <span>Provider contacted<strong>{attempt ? (attempt.provider_contacted ? "yes" : "no") : "unknown"}</strong></span>
                <span>Provider accepted<strong>{attempt ? (attempt.provider_accepted ? "yes" : "no") : "unknown"}</strong></span>
                <span>Provider message ID<strong>{attempt?.provider_message_id ?? "none"}</strong></span>
                <span>Provider response<strong>{attempt?.provider_response_classification ?? "none"}</strong></span>
                <span>Idempotency key<strong>{attempt?.idempotency_key ?? "none"}</strong></span>
              </div>
              <div className="queue-card-detail">
                <span>Deliverability gate<strong>{deliverabilityGate ? `${formatGateName(deliverabilityGate.gate)}: ${formatBlockReason(deliverabilityGate.reason_code)}` : "pass"}</strong></span>
                <span>Reason<strong>{blockExplanation || queueReason(row)}</strong></span>
                {attempt?.error_code && <span>Error<strong>{attempt.error_code}: {attempt.error_detail_redacted ?? "no additional detail"}</strong></span>}
              </div>
              <div className="queue-actions">
                <button className="button primary compact" disabled={!sendNowEligible || rowAction.isPending || process.isPending} onClick={() => runAction("send_now")}>
                  <Send className={isBusy && pendingAction.endsWith(":send_now") ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Reevaluate & Send Now
                </button>
                <button className="button secondary compact" disabled={!retryable || rowAction.isPending || process.isPending} onClick={() => runAction("retry")}>
                  <RotateCcw className={isBusy && pendingAction.endsWith(":retry") ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Retry
                </button>
                <button
                  className="button danger compact"
                  disabled={(!cancellable && !canCancelReconciliation) || rowAction.isPending || process.isPending}
                  onClick={() => runAction(reconcilable ? "cancel_uncertain" : "cancel")}
                >
                  <X className="h-4 w-4" /> {reconcilable ? "Cancel uncertain" : "Cancel"}
                </button>
                <button
                  className="button secondary compact"
                  disabled={!cancellable || rowAction.isPending || process.isPending || clear.isPending}
                  onClick={() => runAction("remove")}
                >
                  <Trash2 className="h-4 w-4" /> Remove from Queue
                </button>
                <button
                  className="button primary compact"
                  disabled={!reconcilable || !acceptanceEvidence || rowAction.isPending || process.isPending}
                  title={acceptanceEvidence ? "Finalize persisted provider acceptance" : "Persisted provider acceptance evidence is incomplete"}
                  onClick={() => runAction("finalize_provider_accepted")}
                >
                  <Check className="h-4 w-4" /> Finalize accepted
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </Panel>
  );
}

function FollowupsPanel({ followups, navigate }: { followups: Followup[]; navigate: (surface: Surface) => void }) {
  const queryClient = useQueryClient();
  const [reviewOpen, setReviewOpen] = useState(false);
  const pendingApprovals = followups.filter((row) => row.status === "pending_approval" && row.pending_draft);
  const process = useMutation({
    mutationFn: api.processFollowups,
    onSuccess: (result) => {
      toast(`Follow-ups processed: ${result.dispatched} dispatched, ${result.stopped} stopped, ${result.skipped} proposed`);
      invalidateAll(queryClient);
    }
  });
  const approve = useMutation({
    mutationFn: api.approveFollowupDraft,
    onSuccess: (result) => {
      toast(deliveryToastMessage(result, "Follow-up approved for delivery."));
      invalidateAll(queryClient);
    }
  });
  const skip = useMutation({
    mutationFn: (id: string) => api.patchFollowup(id, { status: "skipped", stop_reason: "OPERATOR_SKIPPED" }),
    onSuccess: () => {
      toast("follow-up skipped");
      invalidateAll(queryClient);
    }
  });
  return (
    <Panel
      title="Follow-ups"
      icon={RefreshCw}
      action={
        <button className="button primary" disabled={process.isPending} onClick={() => process.mutate()}>
          <RefreshCw className={process.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Process
        </button>
      }
    >
      {pendingApprovals.length > 0 && (
        <div className="approval-banner">
          <strong>{pendingApprovals.length} follow-up drafts are awaiting your review</strong>
          <button className="button primary" onClick={() => setReviewOpen(true)}>Review Pending Drafts</button>
        </div>
      )}
      <DataTable columns={["status", "sequence", "due", "contact", "stop reason"]} rows={followups.map((row) => [row.status, String(row.sequence_num), row.due_at, row.contact_email ?? row.contact_id, row.stop_reason ?? ""])} />
      {reviewOpen && (
        <div className="modal-backdrop">
          <div className="modal approval-modal">
            <div className="flex items-center justify-between gap-3">
              <h3>Review Follow-up Drafts</h3>
              <button className="button secondary" onClick={() => setReviewOpen(false)}>Close</button>
            </div>
            <div className="approval-list">
              {pendingApprovals.map((row) => (
                <article className="approval-item" key={row.id}>
                  <div className="flex items-center justify-between gap-3">
                    <strong>{row.contact_name || row.contact_email || row.contact_id}</strong>
                    <StatusPill value={`seq ${row.sequence_num}`} />
                  </div>
                  <div className="reply-date">{row.pending_draft?.subject}</div>
                  <p>{(row.pending_draft?.body ?? "").slice(0, 200)}</p>
                  <div className="flex flex-wrap gap-2">
                    <button className="button primary" disabled={approve.isPending} onClick={() => approve.mutate(row.id)}>Approve</button>
                    <button className="button secondary" onClick={() => navigate("drafts")}>Edit</button>
                    <button className="button secondary" disabled={skip.isPending} onClick={() => skip.mutate(row.id)}>Skip</button>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

function RepliesPanel({ contacts, replies }: { contacts: Contact[]; replies: ReplyRow[] }) {
  const queryClient = useQueryClient();
  const [contactId, setContactId] = useState(contacts[0]?.id ?? "");
  const [classifiedAs, setClassifiedAs] = useState("reply");
  const [view, setView] = useState<"active" | "archived" | "all">("active");
  const [classFilter, setClassFilter] = useState("");
  const [intentFilter, setIntentFilter] = useState("");
  const [contactFilter, setContactFilter] = useState("");
  const [search, setSearch] = useState("");
  const replyViewContactId = contactFilter || undefined;
  useEffect(() => {
    if (!contactId && contacts[0]?.id) setContactId(contacts[0].id);
  }, [contactId, contacts]);
  const fetchNow = useMutation({
    mutationFn: api.fetchReplies,
    onSuccess: async (result) => {
      if (result.error_code) {
        toast(result.error_code);
      } else {
        toast(`Replies fetched: ${result.checked} checked, ${result.matched} matched, ${result.inserted} inserted, ${result.duplicates} duplicates`);
      }
      await refreshReplyViews(queryClient, replyViewContactId);
    }
  });
  const add = useMutation({
    mutationFn: () => api.addReply({ contact_id: contactId, classified_as: classifiedAs, raw_summary: "manual UI mark" }),
    onSuccess: async () => {
      toast("marked");
      await refreshReplyViews(queryClient, contactId);
    },
    onError: (error) => {
      if (error instanceof Error && error.message.includes("Already marked")) {
        toast("Already marked");
      } else {
        toast("reply mark failed");
      }
    }
  });
  const archive = useMutation({
    mutationFn: api.archiveReply,
    onSuccess: () => {
      toast("reply archived");
      invalidateAll(queryClient);
    }
  });
  const restore = useMutation({
    mutationFn: api.restoreReply,
    onSuccess: () => {
      toast("reply restored");
      invalidateAll(queryClient);
    }
  });
  const remove = useMutation({
    mutationFn: api.deleteReply,
    onSuccess: () => {
      toast("reply deleted");
      invalidateAll(queryClient);
    }
  });
  const classOptions = ["reply", "unsubscribe", "bounce", "auto_reply", "complaint", "unknown"];
  const activeReplies = replies.filter((row) => !row.archived_at);
  const archivedReplies = replies.filter((row) => row.archived_at);
  const shownReplies = replies.filter((row) => {
    if (view === "active" && row.archived_at) return false;
    if (view === "archived" && !row.archived_at) return false;
    if (classFilter && row.classified_as !== classFilter) return false;
    if (intentFilter && (row.intent ?? "unknown") !== intentFilter) return false;
    if (contactFilter && row.contact_id !== contactFilter) return false;
    const haystack = `${row.contact_email ?? row.contact_id} ${row.classified_as} ${row.intent ?? ""} ${row.raw_summary ?? ""}`.toLowerCase();
    return haystack.includes(search.trim().toLowerCase());
  });
  function deleteReply(row: ReplyRow) {
    const label = row.contact_email ?? row.contact_id;
    if (window.confirm(`Delete this reply record for ${label}? This cannot be undone.`)) {
      remove.mutate(row.id);
    }
  }
  const intentOptions = ["positive_interest", "objection", "question", "negative_no", "auto_reply", "unknown"];
  return (
    <Panel
      title="Replies/Stops"
      icon={Inbox}
      action={
        <button className="button secondary" disabled={fetchNow.isPending} onClick={() => fetchNow.mutate()}>
          <RefreshCw className={fetchNow.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Fetch Now
        </button>
      }
    >
      <div className="reply-stats">
        <Metric label="Active" value={String(activeReplies.length)} />
        <Metric label="Archived" value={String(archivedReplies.length)} />
        <Metric label="Visible" value={String(shownReplies.length)} />
      </div>
      <div className="reply-filters">
        <label>
          Search
          <span className="input-with-icon">
            <Search className="h-4 w-4" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="email, class, summary" />
          </span>
        </label>
        <label>View<select value={view} onChange={(e) => setView(e.target.value as "active" | "archived" | "all")}><option value="active">Active only</option><option value="archived">Archived only</option><option value="all">All records</option></select></label>
        <label>Class<select value={classFilter} onChange={(e) => setClassFilter(e.target.value)}><option value="">All classes</option>{classOptions.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Intent<select value={intentFilter} onChange={(e) => setIntentFilter(e.target.value)}><option value="">All intents</option>{intentOptions.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Contact<select value={contactFilter} onChange={(e) => setContactFilter(e.target.value)}><option value="">All contacts</option>{contacts.map((item) => <option key={item.id} value={item.id}>{item.email}</option>)}</select></label>
      </div>
      <div className="reply-layout">
        <div className="reply-mark-box">
          <h3>Manual Stop</h3>
          <label>Contact<select value={contactId} onChange={(e) => setContactId(e.target.value)}>{contacts.map((item) => <option key={item.id} value={item.id}>{item.email}</option>)}</select></label>
          <label>Class<select value={classifiedAs} onChange={(e) => setClassifiedAs(e.target.value)}>{classOptions.filter((item) => item !== "auto_reply" && item !== "unknown").map((item) => <option key={item}>{item}</option>)}</select></label>
          <button className="button primary" disabled={!contactId || add.isPending} onClick={() => add.mutate()}><PauseCircle className="h-4 w-4" /> Mark</button>
        </div>
        <div className="reply-list">
          {shownReplies.length === 0 ? (
            <div className="empty-state">No reply records match these filters.</div>
          ) : (
            shownReplies.map((row) => (
              <article className={row.archived_at ? "reply-item archived" : "reply-item"} key={row.id}>
                <div className="reply-item-head">
                  <div>
                    <strong>{row.contact_email ?? row.contact_id}</strong>
                    <div className="reply-date">{row.received_at ?? "no received timestamp"}</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <StatusPill value={row.classified_as} />
                    <IntentBadge intent={row.intent ?? "unknown"} />
                    {row.archived_at && <span className="pill neutral">archived</span>}
                  </div>
                </div>
                <p className="reply-summary">{row.raw_summary || "No summary captured."}</p>
                <div className="reply-actions">
                  {row.archived_at ? (
                    <button className="button secondary" disabled={restore.isPending} onClick={() => restore.mutate(row.id)}><RotateCcw className="h-4 w-4" /> Restore</button>
                  ) : (
                    <button className="button secondary" disabled={archive.isPending} onClick={() => archive.mutate(row.id)}><Archive className="h-4 w-4" /> Archive</button>
                  )}
                  <button className="button danger" disabled={remove.isPending} onClick={() => deleteReply(row)}><Trash2 className="h-4 w-4" /> Delete</button>
                </div>
              </article>
            ))
          )}
        </div>
      </div>
    </Panel>
  );
}

function ConversationsPanel({ contacts, summaries, settings, providerHealth }: { contacts: Contact[]; summaries: ConversationSummary[]; settings?: SettingsRead; providerHealth: ProviderHealth[] }) {
  const queryClient = useQueryClient();
  const [contactId, setContactId] = useState(summaries[0]?.contact_id ?? contacts[0]?.id ?? "");
  const [search, setSearch] = useState("");
  const [view, setView] = useState<"needs_reply" | "all">("needs_reply");
  const [provider, setProvider] = useState("gemini");
  const [language, setLanguage] = useState("match recipient");
  const defaultInstruction = "Answer the latest reply and move toward one practical next step.";
  const [draftsByContact, setDraftsByContact] = useState<Record<string, { subject: string; body: string; reasoning: string; instruction: string }>>({});

  useEffect(() => {
    const rawDraft = window.sessionStorage.getItem("autoReplyEditDraft");
    if (!rawDraft) return;
    try {
      const draft = JSON.parse(rawDraft) as AutoReplyPending;
      if (!draft.contact_id) return;
      setContactId(draft.contact_id);
      setDraftsByContact((current) => ({
        ...current,
        [draft.contact_id]: {
          subject: draft.subject,
          body: draft.body,
          reasoning: "Auto-reply proposed draft",
          instruction: defaultInstruction
        }
      }));
    } catch {
      // Ignore malformed session state and let the normal composer load.
    } finally {
      window.sessionStorage.removeItem("autoReplyEditDraft");
    }
  }, []);

  useEffect(() => {
    if (!contactId && (summaries[0]?.contact_id || contacts[0]?.id)) {
      setContactId(summaries[0]?.contact_id ?? contacts[0]?.id ?? "");
    }
  }, [contactId, contacts, summaries]);

  const conversation = useQuery({
    queryKey: ["conversation", contactId],
    queryFn: () => api.getConversation(contactId),
    enabled: Boolean(contactId)
  });
  const contactById = new Map(contacts.map((contact) => [contact.id, contact]));
  const sortedSummaries = [...summaries].sort((a, b) => {
    const aNeedsReply = a.last_direction === "inbound" ? 1 : 0;
    const bNeedsReply = b.last_direction === "inbound" ? 1 : 0;
    if (aNeedsReply !== bNeedsReply) return bNeedsReply - aNeedsReply;
    return new Date(b.last_message_at ?? 0).getTime() - new Date(a.last_message_at ?? 0).getTime();
  });
  const filteredSummaries = sortedSummaries.filter((item) => {
    if (view === "needs_reply" && item.last_direction !== "inbound") return false;
    const haystack = `${item.email} ${item.name} ${item.status} ${item.last_subject ?? ""}`.toLowerCase();
    return haystack.includes(search.trim().toLowerCase());
  });
  const activeSummary = summaries.find((item) => item.contact_id === contactId);
  const activeContact = contactById.get(contactId);
  const messages = conversation.data?.messages ?? [];
  const activeDraft = draftsByContact[contactId] ?? { subject: "", body: "", reasoning: "", instruction: defaultInstruction };
  const needsReplyCount = summaries.filter((item) => item.last_direction === "inbound").length;
  const imapHealth = providerHealth.find((row) => row.provider === "imap");
  const autoMode = activeContact?.auto_reply_override === "disabled"
    ? "off"
    : activeContact?.auto_reply_override === "propose"
      ? "propose"
      : settings?.auto_reply_enabled
        ? settings.auto_reply_mode
        : "off";
  function updateActiveDraft(patch: Partial<typeof activeDraft>) {
    if (!contactId) return;
    setDraftsByContact((current) => ({
      ...current,
      [contactId]: { ...(current[contactId] ?? { subject: "", body: "", reasoning: "", instruction: defaultInstruction }), ...patch }
    }));
  }
  const updateAutoReply = useMutation({
    mutationFn: ({ id, value }: { id: string; value: AutoReplyOptionValue }) => api.patchContact(id, { auto_reply_override: value || null }),
    onSuccess: () => {
      toast("email auto-reply setting updated");
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["auto-reply-pending"] });
      void queryClient.invalidateQueries({ queryKey: ["auto-reply-log"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "auto-reply update failed"))
  });

  const fetchNow = useMutation({
    mutationFn: api.fetchReplies,
    onSuccess: async (result) => {
      toast(`Replies fetched: ${result.inserted} new, ${result.duplicates} duplicate`);
      await refreshReplyViews(queryClient, contactId);
    }
  });
  const generate = useMutation({
    mutationFn: (targetContactId: string) =>
      api.generateConversationReply(targetContactId, {
        provider,
        language,
        instruction: draftsByContact[targetContactId]?.instruction ?? defaultInstruction
      }),
    onSuccess: (draft: ConversationDraft, targetContactId) => {
      setDraftsByContact((current) => ({
        ...current,
        [targetContactId]: {
          ...(current[targetContactId] ?? { subject: "", body: "", reasoning: "", instruction: defaultInstruction }),
          subject: draft.subject,
          body: draft.body,
          reasoning: draft.reasoning_summary ?? ""
        }
      }));
      toast(`${draft.provider} reply drafted`);
    },
    onError: () => toast("reply generation failed")
  });
  const sendReply = useMutation({
    mutationFn: (payload: { targetContactId: string; subject: string; body: string }) =>
      api.sendConversationReply(payload.targetContactId, {
        session_token: getSessionToken(),
        subject: payload.subject,
        body: payload.body
      }),
    onSuccess: () => {
      toast("conversation reply is waiting for confirmation");
      void queryClient.invalidateQueries({ queryKey: ["governed-actions"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "send failed"))
  });
  const governedActions = useQuery({
    queryKey: ["governed-actions"],
    queryFn: () => api.listGovernedActions(getSessionToken())
  });
  const pendingConversationAction = governedActions.data?.items.find(
    (action) => action.capability === "conversation_send_reply" && action.contact_id === contactId
  );
  const confirmReply = useMutation({
    mutationFn: (action: GovernedAction) =>
      api.confirmConversationReply(contactId, {
        session_token: getSessionToken(),
        action_id: action.action_id,
        subject: action.subject,
        body: action.body
      }),
    onSuccess: (result) => {
      toast(result.status === "provider_accepted" ? "conversation reply provider accepted" : `conversation reply ${result.status}`);
      setDraftsByContact((current) => ({
        ...current,
        [contactId]: { subject: "", body: "", reasoning: "", instruction: current[contactId]?.instruction ?? defaultInstruction }
      }));
      void queryClient.invalidateQueries({ queryKey: ["governed-actions"] });
      void queryClient.invalidateQueries({ queryKey: ["conversation", contactId] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "confirmation failed"))
  });
  const cancelReply = useMutation({
    mutationFn: (action: GovernedAction) => api.cancelGovernedAction(action.action_id, getSessionToken()),
    onSuccess: () => {
      toast("conversation send cancelled");
      void queryClient.invalidateQueries({ queryKey: ["governed-actions"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "cancel failed"))
  });

  return (
    <Panel
      title="Conversations"
      icon={MessageSquare}
      action={
        <button className="button secondary" disabled={fetchNow.isPending} onClick={() => fetchNow.mutate()}>
          <RefreshCw className={fetchNow.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Fetch Replies
        </button>
      }
    >
      <div className="conversation-toolbar">
        <label>
          Search
          <span className="input-with-icon">
            <Search className="h-4 w-4" />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="email, name, status" />
          </span>
        </label>
        <label>View<select value={view} onChange={(event) => setView(event.target.value as "needs_reply" | "all")}><option value="needs_reply">Needs reply ({needsReplyCount})</option><option value="all">All conversations</option></select></label>
        <label>Contact<select value={contactId} onChange={(event) => setContactId(event.target.value)}>{sortedSummaries.map((item) => <option key={item.contact_id} value={item.contact_id}>{item.email}</option>)}</select></label>
      </div>
      <div className="conversation-layout">
        <div className="conversation-list">
          {filteredSummaries.length === 0 ? (
            <div className="empty-state">No contacts match this search.</div>
          ) : (
            filteredSummaries.map((item) => {
              const contact = contactById.get(item.contact_id);
              return (
                <div key={item.contact_id} className={`${contactId === item.contact_id ? "conversation-row-wrap active" : "conversation-row-wrap"} ${item.last_direction === "inbound" ? "needs-reply" : ""}`}>
                  <button type="button" className="conversation-row" onClick={() => setContactId(item.contact_id)}>
                    <span className="conversation-row-main">
                      <strong>{item.email}</strong>
                      <span>{item.name || item.status}</span>
                    </span>
                    <span className="conversation-row-meta">
                      <StatusPill value={item.status} />
                      {item.last_direction === "inbound" && <span className="pill good">needs reply</span>}
                      <span>{item.inbound} in / {item.outbound} out</span>
                    </span>
                  </button>
                  <div className="conversation-row-auto">
                    <span className={autoReplyClass(contact?.auto_reply_override)}>Auto-reply {autoReplyLabel(contact?.auto_reply_override)}</span>
                    <AutoReplyButtons
                      value={contact?.auto_reply_override ?? ""}
                      disabled={updateAutoReply.isPending}
                      label={item.email}
                      options={AUTO_REPLY_BINARY_OPTIONS}
                      onChange={(value) => updateAutoReply.mutate({ id: item.contact_id, value })}
                    />
                  </div>
                </div>
              );
            })
          )}
        </div>
        <div className="conversation-main">
          <div className="conversation-context">
            <Metric label="Prospect" value={activeSummary?.name || activeContact?.creator_name || activeContact?.business_name || "unknown"} />
            <Metric label="Website" value={activeContact?.website_url || "not provided"} />
            <Metric label="Status" value={activeSummary?.status || activeContact?.status || "unknown"} />
            <Metric label="Auto-reply" value={autoMode === "autonomous" ? "Auto-reply ON" : autoMode === "propose" ? "Propose mode" : "Auto-reply OFF"} />
            <Metric label="Last checked" value={imapHealth?.last_checked ? new Date(imapHealth.last_checked).toLocaleString() : "not checked"} />
          </div>
          <div className="conversation-thread">
            {conversation.isLoading ? (
              <div className="empty-state">Loading conversation...</div>
            ) : messages.length === 0 ? (
              <div className="empty-state">No conversation messages yet.</div>
            ) : (
              messages.map((message) => (
                <article className={`conversation-message ${message.direction}`} key={message.id}>
                  <div className="conversation-message-head">
                    <strong>{message.direction === "outbound" ? "You" : activeSummary?.email || "Recipient"}</strong>
                    <span className="message-badges">
                      {message.auto_sent && <span className="pill good">AUTO</span>}
                      {message.source === "auto_reply_proposed" && <span className="pill neutral">PENDING APPROVAL</span>}
                      <span>{message.occurred_at ? new Date(message.occurred_at).toLocaleString() : ""}</span>
                    </span>
                  </div>
                  {message.subject && <div className="conversation-subject">{message.subject}</div>}
                  <p>{message.body}</p>
                </article>
              ))
            )}
          </div>
          <div className="conversation-composer">
            <div className="form-grid">
              <label>Provider<select value={provider} onChange={(event) => setProvider(event.target.value)}><option>auto</option><option>groq</option><option>gemini</option></select></label>
              <label>Language<input value={language} onChange={(event) => setLanguage(event.target.value)} /></label>
              <label>Subject<input value={activeDraft.subject} onChange={(event) => updateActiveDraft({ subject: event.target.value })} /></label>
            </div>
            <label className="mt-3">Instruction<textarea rows={2} value={activeDraft.instruction} onChange={(event) => updateActiveDraft({ instruction: event.target.value })} /></label>
            <label className="mt-3">Reply Body<textarea rows={8} value={activeDraft.body} onChange={(event) => updateActiveDraft({ body: event.target.value })} /></label>
            {activeDraft.reasoning && <div className="notice">{activeDraft.reasoning}</div>}
            <div className="mt-4 flex flex-wrap gap-2">
              <button className="button secondary" disabled={!contactId || generate.isPending || messages.length === 0} onClick={() => generate.mutate(contactId)}>
                <Sparkles className={generate.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Generate Reply
              </button>
              <button className="button primary" disabled={!contactId || !activeDraft.subject.trim() || !activeDraft.body.trim() || sendReply.isPending || Boolean(pendingConversationAction)} onClick={() => sendReply.mutate({ targetContactId: contactId, subject: activeDraft.subject, body: activeDraft.body })}>
                <Send className={sendReply.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Review Send
              </button>
            </div>
            {pendingConversationAction && (
              <GovernedActionCard
                action={pendingConversationAction}
                busy={confirmReply.isPending || cancelReply.isPending}
                onConfirm={() => confirmReply.mutate(pendingConversationAction)}
                onCancel={() => cancelReply.mutate(pendingConversationAction)}
              />
            )}
          </div>
        </div>
      </div>
    </Panel>
  );
}

function GovernedActionCard({ action, busy, onConfirm, onCancel }: { action: GovernedAction; busy: boolean; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div className="notice warning mt-4" data-action-id={action.action_id}>
      <strong>Confirmation required</strong>
      <div>{action.confirmation_prompt}</div>
      {action.to && <div><strong>Target:</strong> {action.to}</div>}
      {action.subject && <div><strong>Subject:</strong> {action.subject}</div>}
      {action.body && <div className="snippet-cell">{action.body.slice(0, 300)}</div>}
      {action.evidence_summary && <div><strong>Evidence:</strong> {action.evidence_summary}</div>}
      {action.proposed_side_effect && <div><strong>Effect:</strong> {action.proposed_side_effect}</div>}
      {action.activation_details && (
        <div className="mt-3">
          <div><strong>Mailbox:</strong> {action.activation_details.mailbox || "not configured"}</div>
          <div><strong>Daily cap:</strong> {action.activation_details.daily_cap ?? "not configured"}</div>
          <div><strong>Minimum gap:</strong> {action.activation_details.minimum_gap_minutes ?? "not configured"} minutes</div>
          <div><strong>Allowed intents:</strong> {action.activation_details.safe_intents || "none configured"}</div>
          <div><strong>Stop conditions:</strong> {action.activation_details.stop_conditions || "none configured"}</div>
        </div>
      )}
      {action.policy_result && <div><strong>Policy:</strong> {action.policy_result}</div>}
      <div><strong>Expires:</strong> {new Date(action.expires_at).toLocaleString()}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="button primary" disabled={busy} onClick={onConfirm}>Confirm</button>
        <button className="button secondary" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

type AutoReplyModeSelection = "off" | "propose" | "autonomous";

function autoReplyModeSelection(settings?: SettingsRead): AutoReplyModeSelection {
  if (!settings?.auto_reply_enabled) return "off";
  return settings.auto_reply_mode === "autonomous" ? "autonomous" : "propose";
}

function autoReplyModePayload(mode: AutoReplyModeSelection) {
  return mode === "off" ? { auto_reply_enabled: false } : { auto_reply_enabled: true, auto_reply_mode: mode };
}

function AutoReplyPanel({ pending, log, navigate, settings }: { pending: AutoReplyPending[]; log: AuditEvent[]; navigate: (surface: Surface) => void; settings?: SettingsRead }) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<"pending" | "log">("pending");
  const [logFilter, setLogFilter] = useState("");
  const savedMode = autoReplyModeSelection(settings);
  const [selectedMode, setSelectedMode] = useState<AutoReplyModeSelection>(savedMode);
  const governedActions = useQuery({
    queryKey: ["governed-actions"],
    queryFn: () => api.listGovernedActions(getSessionToken())
  });
  const autonomousAction = governedActions.data?.items.find((action) => action.capability === "auto_reply_enable_autonomous");
  const updateMode = useMutation({
    mutationFn: async (mode: AutoReplyModeSelection) => {
      if (mode === "off") {
        await api.killAutoReply();
        return { mode, settings: await api.getSettings() };
      }
      if (mode === "autonomous") {
        const pendingAction = await api.prepareAutonomousAutoReply(getSessionToken());
        return { mode, pendingAction };
      }
      return { mode, settings: await api.updateSettings(autoReplyModePayload(mode)) };
    },
    onMutate: (mode) => {
      setSelectedMode(mode);
    },
    onSuccess: (result) => {
      if (result.pendingAction) {
        toast("autonomous activation is waiting for confirmation");
        void queryClient.invalidateQueries({ queryKey: ["governed-actions"] });
        return;
      }
      if (result.settings) {
        queryClient.setQueryData(["settings"], result.settings);
        setSelectedMode(autoReplyModeSelection(result.settings));
        toast(result.mode === "off" ? "auto-reply stopped" : "approval mode enabled");
      }
    },
    onError: (error) => {
      setSelectedMode(savedMode);
      toast(apiErrorMessage(error, "auto-reply mode update failed"));
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["auto-reply-pending"] });
      void queryClient.invalidateQueries({ queryKey: ["auto-reply-log"] });
    }
  });
  useEffect(() => {
    if (!updateMode.isPending && !autonomousAction) setSelectedMode(savedMode);
  }, [autonomousAction, savedMode, updateMode.isPending]);
  const modeCopy = selectedMode === "autonomous" ? "Autonomous send" : selectedMode === "propose" ? "Approval required" : "Off";
  const modeButtonClass = (mode: AutoReplyModeSelection) =>
    [selectedMode === mode ? "active" : "", updateMode.isPending && selectedMode === mode ? "saving" : ""].filter(Boolean).join(" ");
  const modeButtonDisabled = (mode: AutoReplyModeSelection) => !settings || updateMode.isPending || selectedMode === mode;
  const fetchNow = useMutation({
    mutationFn: api.fetchReplies,
    onSuccess: async (result) => {
      toast(`Fetched. ${result.inserted} new replies found.`);
      await refreshReplyViews(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "fetch failed"))
  });
  const approve = useMutation({
    mutationFn: (draftId: string) => api.approveAutoReply(draftId, getSessionToken()),
    onSuccess: () => {
      toast("Auto-Reply is waiting for confirmation");
      void queryClient.invalidateQueries({ queryKey: ["governed-actions"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "approve failed"))
  });
  const confirmAction = useMutation({
    mutationFn: async (action: GovernedAction) => {
      if (action.capability === "auto_reply_enable_autonomous") {
        return api.confirmAutonomousAutoReply(action.action_id, getSessionToken());
      }
      if (!action.draft_id) throw new Error("draft_id_missing");
      return api.confirmAutoReply(action.draft_id, action.action_id, getSessionToken());
    },
    onSuccess: (result) => {
      toast(result.status === "provider_accepted" ? "reply provider accepted" : result.status.replace(/_/g, " "));
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["governed-actions"] });
      void queryClient.invalidateQueries({ queryKey: ["auto-reply-pending"] });
      void queryClient.invalidateQueries({ queryKey: ["auto-reply-log"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "confirmation failed"))
  });
  const cancelAction = useMutation({
    mutationFn: (action: GovernedAction) => api.cancelGovernedAction(action.action_id, getSessionToken()),
    onSuccess: () => {
      toast("pending action cancelled");
      setSelectedMode(savedMode);
      void queryClient.invalidateQueries({ queryKey: ["governed-actions"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "cancel failed"))
  });
  const reject = useMutation({
    mutationFn: api.rejectAutoReply,
    onSuccess: () => {
      toast("reply rejected");
      void queryClient.invalidateQueries({ queryKey: ["auto-reply-pending"] });
      void queryClient.invalidateQueries({ queryKey: ["auto-reply-log"] });
    },
    onError: (error) => toast(apiErrorMessage(error, "reject failed"))
  });
  const filteredLog = log.filter((row) => !logFilter || row.event_type === logFilter);
  const logTypes = Array.from(new Set(log.map((row) => row.event_type))).sort();
  function editDraft(row: AutoReplyPending) {
    window.sessionStorage.setItem("autoReplyEditDraft", JSON.stringify(row));
    navigate("conversations");
  }

  return (
    <Panel
      title="Auto-Reply"
      icon={Bot}
      action={
        <button className="button secondary" disabled={fetchNow.isPending} onClick={() => fetchNow.mutate()}>
          <RefreshCw className={fetchNow.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Check for New Replies Now
        </button>
      }
    >
      <div className="auto-reply-mode-row">
        <div className="mode-copy">
          <strong>Mode</strong>
          <span>{modeCopy}</span>
          {updateMode.isPending && <small aria-live="polite">Saving...</small>}
        </div>
        <div className={updateMode.isPending ? "segmented auto-reply-mode-control saving" : "segmented auto-reply-mode-control"} aria-label="Auto reply mode" aria-busy={updateMode.isPending}>
          <button
            className={modeButtonClass("off")}
            disabled={modeButtonDisabled("off")}
            aria-pressed={selectedMode === "off"}
            onClick={() => updateMode.mutate("off")}
          >
            <PauseCircle className="h-4 w-4" /> Stop Now
          </button>
          <button
            className={modeButtonClass("propose")}
            disabled={modeButtonDisabled("propose")}
            aria-pressed={selectedMode === "propose"}
            onClick={() => updateMode.mutate("propose")}
          >
            <Check className="h-4 w-4" /> Approval required
          </button>
          <button
            className={modeButtonClass("autonomous")}
            disabled={modeButtonDisabled("autonomous")}
            aria-pressed={selectedMode === "autonomous"}
            onClick={() => updateMode.mutate("autonomous")}
          >
            <Send className="h-4 w-4" /> Autonomous send
          </button>
        </div>
      </div>
      {autonomousAction && (
        <GovernedActionCard
          action={autonomousAction}
          busy={confirmAction.isPending || cancelAction.isPending}
          onConfirm={() => confirmAction.mutate(autonomousAction)}
          onCancel={() => cancelAction.mutate(autonomousAction)}
        />
      )}
      {(selectedMode === "autonomous" || autonomousAction) && (
        <div className="notice warning mt-4">
          Autonomous sends remain subject to the kill switch, reply and suppression stops, caps, allowed intents, transport checks, and final provider-boundary policy validation.
        </div>
      )}
      <div className="segmented">
        <button className={tab === "pending" ? "active" : ""} onClick={() => setTab("pending")}>Pending Approval</button>
        <button className={tab === "log" ? "active" : ""} onClick={() => setTab("log")}>Activity Log</button>
      </div>
      {tab === "pending" ? (
        pending.length === 0 ? (
          <div className="empty-state mt-4">No replies waiting for approval</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Contact</th><th>Their Reply</th><th>AI Reply</th><th>Generated</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {pending.map((row) => {
                  const pendingApproval = governedActions.data?.items.find(
                    (action) => action.capability === "auto_reply_approve" && action.draft_id === row.id
                  );
                  return (
                    <Fragment key={row.id}>
                      <tr data-draft-id={row.id}>
                        <td><strong>{row.contact_name || row.contact_email}</strong><div className="reply-date">{row.contact_email}</div></td>
                        <td className="snippet-cell">{row.their_reply}</td>
                        <td className="snippet-cell"><strong>{row.subject}</strong><br />{row.body.slice(0, 200)}</td>
                        <td>{row.generated_at ? new Date(row.generated_at).toLocaleString() : ""}</td>
                        <td>
                          <div className="reply-actions">
                            <button className="button primary" disabled={approve.isPending || Boolean(pendingApproval)} onClick={() => approve.mutate(row.id)}>Review Approval</button>
                            <button className="button secondary" onClick={() => editDraft(row)}>Edit</button>
                            <button className="button danger" disabled={reject.isPending} onClick={() => reject.mutate(row.id)}>Reject</button>
                          </div>
                        </td>
                      </tr>
                      {pendingApproval && (
                        <tr>
                          <td colSpan={5}>
                            <GovernedActionCard
                              action={pendingApproval}
                              busy={confirmAction.isPending || cancelAction.isPending}
                              onConfirm={() => confirmAction.mutate(pendingApproval)}
                              onCancel={() => cancelAction.mutate(pendingApproval)}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )
      ) : (
        <div>
          <div className="mb-4 max-w-xs">
            <label>Action Filter<select value={logFilter} onChange={(event) => setLogFilter(event.target.value)}><option value="">All actions</option>{logTypes.map((item) => <option key={item}>{item}</option>)}</select></label>
          </div>
          <DataTable
            columns={["time", "contact", "action", "subject", "result"]}
            rows={filteredLog.map((row) => [
              row.created_at ? new Date(row.created_at).toLocaleString() : "",
              String(row.entity_id ?? row.payload.contact_id ?? ""),
              row.event_type.replace("auto_reply.", "").toUpperCase(),
              String(row.payload.subject ?? ""),
              String(row.payload.reason ?? row.payload.error_code ?? row.payload.draft_id ?? "")
            ])}
          />
        </div>
      )}
    </Panel>
  );
}

function SuppressionsPanel({ suppressions }: { suppressions: Suppression[] }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const add = useMutation({
    mutationFn: () => api.addSuppression({ email, reason: "manual", source: "ui" }),
    onSuccess: () => {
      setEmail("");
      invalidateAll(queryClient);
    }
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteSuppression(id),
    onSuccess: () => invalidateAll(queryClient)
  });
  return (
    <Panel title="Suppressions" icon={ArchiveX}>
      <div className="flex gap-2">
        <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="email@example.com" />
        <button className="button primary" onClick={() => add.mutate()}>Add</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>email</th><th>reason</th><th>source</th><th>actions</th></tr>
          </thead>
          <tbody>
            {suppressions.map((row) => (
              <tr key={row.id}>
                <td>{row.email}</td>
                <td>{row.reason}</td>
                <td>{row.source ?? ""}</td>
                <td><button className="button danger" disabled={remove.isPending} onClick={() => remove.mutate(row.id)}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function AuditPanel({ audit }: { audit: AuditEvent[] }) {
  const rows = [...audit].reverse().map((row) => [
    row.created_at,
    row.event_label ?? humanizeAuditEvent(row.event_type),
    auditWho(row),
    row.detail ?? auditFallbackDetail(row),
    auditTransportTruth(row)
  ]);
  return (
    <Panel title="Audit Logs" icon={Database}>
      <DataTable columns={["time", "what happened", "who", "details", "transport truth"]} rows={rows} />
    </Panel>
  );
}

function auditTransportTruth(row: AuditEvent): string {
  return ["attempt_status", "effective_transport", "simulated", "provider_contacted", "provider_accepted", "attempt_id"]
    .filter((key) => key in row.payload)
    .map((key) => `${key}=${String(row.payload[key])}`)
    .join(" | ") || "n/a";
}

function auditWho(row: AuditEvent): string {
  if (row.contact_name && row.contact_email) return `${row.contact_name} (${row.contact_email})`;
  return row.contact_name ?? row.contact_email ?? row.entity_label ?? row.entity_type ?? "";
}

function auditFallbackDetail(row: AuditEvent): string {
  const payload = Object.entries(row.payload ?? {})
    .filter(([key]) => !/password|secret|token|credential|key|hash/i.test(key))
    .slice(0, 3)
    .map(([key, value]) => `${humanizeAuditEvent(key)}: ${previewValue(Array.isArray(value) ? value.join(", ") : value, 60)}`)
    .join(", ");
  const base = row.event_label ?? humanizeAuditEvent(row.event_type);
  return payload ? `${base} (${payload}).` : `${base}.`;
}

function humanizeAuditEvent(value: string): string {
  const compact = value.replace(/[_.]+/g, " ").trim();
  return compact ? compact.charAt(0).toUpperCase() + compact.slice(1) : "";
}

function ErrorsPanel({ audit }: { audit: AuditEvent[] }) {
  const errors = audit.filter((row) => row.event_type.includes("failed") || row.event_type.includes("blocked") || row.event_type.includes("exhausted"));
  return (
    <Panel title="Errors" icon={AlertTriangle}>
      <div className="notice compact">This view is an audit-event filter, not a complete independent error store.</div>
      <DataTable columns={["time", "event", "payload"]} rows={errors.map((row) => [row.created_at, row.event_type, JSON.stringify(row.payload)])} />
    </Panel>
  );
}

function SettingsPanel({ settings, ready }: { settings?: SettingsRead; ready: boolean }) {
  const queryClient = useQueryClient();
  const [gmailUser, setGmailUser] = useState(settings?.gmail_user ?? "");
  const [emailTransport, setEmailTransport] = useState<"smtp" | "gmail_api">(settings?.email_transport ?? "gmail_api");
  const [password, setPassword] = useState("");
  const [gmailApiClientId, setGmailApiClientId] = useState("");
  const [gmailApiClientSecret, setGmailApiClientSecret] = useState("");
  const [gmailApiRefreshToken, setGmailApiRefreshToken] = useState("");
  const [recipient, setRecipient] = useState(settings?.report_recipient ?? "");
  const [groq, setGroq] = useState("");
  const [gemini, setGemini] = useState("");
  const [dailyCap, setDailyCap] = useState(settings?.daily_send_cap ?? 50);
  const [hourlyCap, setHourlyCap] = useState(settings?.hourly_send_cap ?? 10);
  const [delay, setDelay] = useState(settings?.send_delay_s ?? 60);
  const [autoProcessEnabled, setAutoProcessEnabled] = useState(settings?.auto_process_enabled ?? false);
  const [autoProcessQueueInterval, setAutoProcessQueueInterval] = useState(settings?.auto_process_queue_interval_seconds ?? 5);
  const [autoProcessFollowupInterval, setAutoProcessFollowupInterval] = useState(settings?.auto_process_followup_interval_seconds ?? 60);
  const [followupDays, setFollowupDays] = useState(settings?.followup_interval_days ?? 3);
  const [maxFollowups, setMaxFollowups] = useState(settings?.max_followups_per_lead ?? 2);
  const [campaignContext, setCampaignContext] = useState(settings?.campaign_context ?? "");
  const [senderName, setSenderName] = useState(settings?.sender_name ?? "");
  const [senderRole, setSenderRole] = useState(settings?.sender_role ?? "");
  const [senderOffer, setSenderOffer] = useState(settings?.sender_offer ?? "");
  const [senderTone, setSenderTone] = useState(settings?.sender_tone ?? "Professional");
  const [senderSignature, setSenderSignature] = useState(settings?.sender_signature ?? "");
  const [groqModel, setGroqModel] = useState(settings?.groq_model ?? "llama-3.3-70b-versatile");
  const [geminiModel, setGeminiModel] = useState("gemini-2.5-flash");
  const [followTemplate1, setFollowTemplate1] = useState(settings?.follow_up_template_1 ?? "");
  const [followTemplate2, setFollowTemplate2] = useState(settings?.follow_up_template_2 ?? "");
  const [blockedDomains, setBlockedDomains] = useState(settings?.blocked_domains ?? "");
  const [sendWindowStart, setSendWindowStart] = useState(settings?.send_window_start ?? "09:00");
  const [sendWindowEnd, setSendWindowEnd] = useState(settings?.send_window_end ?? "17:00");
  const [sendTimezone, setSendTimezone] = useState(settings?.send_timezone ?? "Asia/Kolkata");
  const [warmUpMode, setWarmUpMode] = useState(settings?.warm_up_mode ?? false);
  const [imapInterval, setImapInterval] = useState(settings?.imap_fetch_interval_minutes ?? 10);
  const [autoReplyEnabled, setAutoReplyEnabled] = useState(settings?.auto_reply_enabled ?? false);
  const [autoReplyMode, setAutoReplyMode] = useState<"propose" | "autonomous">(settings?.auto_reply_mode ?? "propose");
  const [autoReplyDailyCap, setAutoReplyDailyCap] = useState(settings?.auto_reply_daily_cap ?? 20);
  const [autoReplyMinGap, setAutoReplyMinGap] = useState(settings?.auto_reply_min_gap_minutes ?? 60);
  const [autoReplySafeIntents, setAutoReplySafeIntents] = useState(settings?.auto_reply_safe_intents ?? "positive_interest,objection,question");
  const [dryRun, setDryRun] = useState(settings?.dry_run ?? true);
  useEffect(() => {
    if (!settings) return;
    setGmailUser(settings.gmail_user);
    setEmailTransport(settings.email_transport ?? "gmail_api");
    setRecipient(settings.report_recipient);
    setDailyCap(settings.daily_send_cap);
    setHourlyCap(settings.hourly_send_cap);
    setDelay(settings.send_delay_s);
    setAutoProcessEnabled(settings.auto_process_enabled ?? false);
    setAutoProcessQueueInterval(settings.auto_process_queue_interval_seconds ?? 5);
    setAutoProcessFollowupInterval(settings.auto_process_followup_interval_seconds ?? 60);
    setFollowupDays(settings.followup_interval_days);
    setMaxFollowups(settings.max_followups_per_lead);
    setCampaignContext(settings.campaign_context);
    setSenderName(settings.sender_name ?? "");
    setSenderRole(settings.sender_role ?? "");
    setSenderOffer(settings.sender_offer ?? "");
    setSenderTone(settings.sender_tone ?? "Professional");
    setSenderSignature(settings.sender_signature ?? "");
    setGroqModel(settings.groq_model ?? "llama-3.3-70b-versatile");
    setGeminiModel("gemini-2.5-flash");
    setFollowTemplate1(settings.follow_up_template_1 ?? "");
    setFollowTemplate2(settings.follow_up_template_2 ?? "");
    setBlockedDomains(settings.blocked_domains ?? "");
    setSendWindowStart(settings.send_window_start ?? "09:00");
    setSendWindowEnd(settings.send_window_end ?? "17:00");
    setSendTimezone(settings.send_timezone ?? "Asia/Kolkata");
    setWarmUpMode(settings.warm_up_mode ?? false);
    setImapInterval(settings.imap_fetch_interval_minutes);
    setAutoReplyEnabled(Boolean(settings.auto_reply_enabled));
    setAutoReplyMode(settings.auto_reply_mode ?? "propose");
    setAutoReplyDailyCap(settings.auto_reply_daily_cap ?? 20);
    setAutoReplyMinGap(settings.auto_reply_min_gap_minutes ?? 60);
    setAutoReplySafeIntents(settings.auto_reply_safe_intents ?? "positive_interest,objection,question");
    setDryRun(settings.dry_run);
  }, [settings]);
  function toggleSafeIntent(intent: string, checked: boolean) {
    const next = new Set(autoReplySafeIntents.split(",").map((item) => item.trim()).filter(Boolean));
    if (checked) next.add(intent);
    else next.delete(intent);
    setAutoReplySafeIntents(Array.from(next).join(","));
  }
  function hasSafeIntent(intent: string) {
    return (autoReplySafeIntents || "").split(",").map((item) => item.trim()).includes(intent);
  }
  const payload = () => ({
    gmail_user: gmailUser,
    email_transport: emailTransport,
    gmail_app_password: password || undefined,
    gmail_api_client_id: gmailApiClientId || undefined,
    gmail_api_client_secret: gmailApiClientSecret || undefined,
    gmail_api_refresh_token: gmailApiRefreshToken || undefined,
    report_recipient: recipient,
    groq_keys: groq || undefined,
    gemini_keys: gemini || undefined,
    daily_send_cap: dailyCap,
    hourly_send_cap: hourlyCap,
    send_delay_s: delay,
    auto_process_enabled: autoProcessEnabled,
    auto_process_queue_interval_seconds: autoProcessQueueInterval,
    auto_process_followup_interval_seconds: autoProcessFollowupInterval,
    followup_interval_days: followupDays,
    max_followups_per_lead: maxFollowups,
    campaign_context: campaignContext,
    sender_name: senderName,
    sender_role: senderRole,
    sender_offer: senderOffer,
    sender_tone: senderTone,
    sender_signature: senderSignature,
    groq_model: groqModel,
    gemini_model: geminiModel,
    follow_up_template_1: followTemplate1,
    follow_up_template_2: followTemplate2,
    blocked_domains: blockedDomains,
    send_window_start: sendWindowStart,
    send_window_end: sendWindowEnd,
    send_timezone: sendTimezone,
    warm_up_mode: warmUpMode,
    imap_fetch_interval_minutes: imapInterval,
    auto_reply_enabled: autoReplyEnabled,
    auto_reply_mode: autoReplyMode,
    auto_reply_daily_cap: autoReplyDailyCap,
    auto_reply_min_gap_minutes: autoReplyMinGap,
    auto_reply_safe_intents: autoReplySafeIntents,
    dry_run: dryRun
  });
  const save = useMutation({
    mutationFn: () =>
      api.updateSettings(payload()),
    onSuccess: () => {
      setPassword("");
      setGmailApiClientId("");
      setGmailApiClientSecret("");
      setGmailApiRefreshToken("");
      setGroq("");
      setGemini("");
      toast("settings saved");
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "settings save failed"))
  });
  const saveVerify = useMutation({
    mutationFn: async () => {
      await api.updateSettings(payload());
      return api.verifyEmail();
    },
    onSuccess: (result) => {
      setPassword("");
      setGmailApiClientId("");
      setGmailApiClientSecret("");
      setGmailApiRefreshToken("");
      setGroq("");
      setGemini("");
      toast(result.readiness);
      invalidateAll(queryClient);
    },
    onError: (error) => toast(apiErrorMessage(error, "settings save and email verification failed"))
  });
  const connectGmailApi = useMutation({
    mutationFn: async () => {
      await api.updateSettings(payload());
      return api.startGmailApiOAuth({ return_url: `${window.location.origin}${window.location.pathname}` });
    },
    onSuccess: (result) => {
      window.location.assign(result.authorization_url);
    },
    onError: (error) => toast(apiErrorMessage(error, "gmail api oauth start failed"))
  });
  return (
    <Panel
      title="Settings"
      icon={Settings}
      action={
        <div className="flex gap-2">
          {emailTransport === "gmail_api" && (
            <button className={`button secondary${connectGmailApi.isPending ? " is-loading" : ""}`} disabled={!ready || saveVerify.isPending || save.isPending || connectGmailApi.isPending} aria-busy={connectGmailApi.isPending} onClick={() => connectGmailApi.mutate()}>
              <KeyRound className={connectGmailApi.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> <span>{connectGmailApi.isPending ? "Connecting..." : "Connect Gmail API"}</span>
            </button>
          )}
          <button className={`button secondary${saveVerify.isPending ? " is-loading" : ""}`} disabled={!ready || saveVerify.isPending || save.isPending || connectGmailApi.isPending} aria-busy={saveVerify.isPending} onClick={() => saveVerify.mutate()}>
            <Check className={saveVerify.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> <span>{saveVerify.isPending ? "Verifying..." : "Save & Verify Email"}</span>
          </button>
          <button className={`button primary${save.isPending ? " is-loading" : ""}`} disabled={!ready || save.isPending || saveVerify.isPending || connectGmailApi.isPending} onClick={() => save.mutate()}>
            <KeyRound className={save.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> <span>{save.isPending ? "Saving..." : "Save"}</span>
          </button>
        </div>
      }
    >
      <TransportTruth settings={settings} compact />
      {settings?.release_blocked && <div className="notice mt-4"><strong>Release blocker:</strong> operational APIs have an injectable authorization boundary, but production identity enforcement is not configured.</div>}
      {!settings?.auto_process_effective && (
        <div className="notice warning mt-4">
          <strong>Automatic queue processing is not running.</strong>
          <div>Configured: {settings?.auto_process_enabled ? "on" : "off"}; runtime: {settings?.auto_process_block_reason?.split("_").join(" ") ?? "paused"}.</div>
        </div>
      )}
      {settings?.hosting_mode === "render_free" && (
        <div className="notice warning mt-4">
          <strong>Render Free automation is best effort.</strong>
          <div>Manual Approve &amp; Send works synchronously after the service wakes. Scheduled Queue and Auto-Reply work can be delayed while Render sleeps and is not guaranteed to run continuously.</div>
        </div>
      )}
      <div className="form-grid">
        <label>Gmail User<input value={gmailUser} onChange={(e) => setGmailUser(e.target.value)} /></label>
        <label>Email Transport<select value={emailTransport} onChange={(e) => setEmailTransport(e.target.value as "smtp" | "gmail_api")}><option value="smtp" disabled={settings?.smtp_available === false}>Gmail SMTP{settings?.smtp_available === false ? " (local only)" : ""}</option><option value="gmail_api">Gmail API</option></select></label>
        <label>Gmail App Password (SMTP/IMAP)<input type="password" disabled={settings?.smtp_available === false} value={password} onChange={(e) => setPassword(e.target.value)} placeholder={settings?.gmail_app_password_configured ? "configured" : ""} /></label>
        <label>Report Recipient<input value={recipient} onChange={(e) => setRecipient(e.target.value)} /></label>
        {emailTransport === "gmail_api" && (
          <>
            <label>Gmail API Client ID<input type="password" value={gmailApiClientId} onChange={(e) => setGmailApiClientId(e.target.value)} placeholder={settings?.gmail_api_configured ? "configured" : ""} /></label>
            <label>Gmail API Client Secret<input type="password" value={gmailApiClientSecret} onChange={(e) => setGmailApiClientSecret(e.target.value)} placeholder={settings?.gmail_api_configured ? "configured" : ""} /></label>
            <label>Gmail API Refresh Token<input type="password" value={gmailApiRefreshToken} onChange={(e) => setGmailApiRefreshToken(e.target.value)} placeholder={settings?.gmail_api_configured ? "configured" : ""} /></label>
            <div className="notice">
              Hosted Gmail sends use Google OAuth. SMTP/IMAP app passwords are not used by Gmail API.
            </div>
          </>
        )}
        <label>Daily Cap<input type="number" value={dailyCap} onChange={(e) => setDailyCap(Number(e.target.value))} /></label>
        <label>Hourly Cap<input type="number" value={hourlyCap} onChange={(e) => setHourlyCap(Number(e.target.value))} /></label>
        <label>Send Delay<input type="number" value={delay} onChange={(e) => setDelay(Number(e.target.value))} /></label>
        <label>Queue Check Interval<input type="number" min={5} value={autoProcessQueueInterval} disabled={!autoProcessEnabled} onChange={(e) => setAutoProcessQueueInterval(Number(e.target.value))} /></label>
        <label>Follow-up Check Interval<input type="number" min={30} value={autoProcessFollowupInterval} disabled={!autoProcessEnabled} onChange={(e) => setAutoProcessFollowupInterval(Number(e.target.value))} /></label>
        <label>Follow-up Days<input type="number" value={followupDays} onChange={(e) => setFollowupDays(Number(e.target.value))} /></label>
        <label>Max Follow-ups<input type="number" value={maxFollowups} onChange={(e) => setMaxFollowups(Number(e.target.value))} /></label>
        <label>IMAP Fetch Interval (minutes)<input type="number" min={1} value={imapInterval} onChange={(e) => setImapInterval(Number(e.target.value))} /></label>
      </div>
      <div className="settings-section">
        <h3>About You</h3>
        <div className="form-grid">
          <label>Your Name<input value={senderName} onChange={(e) => setSenderName(e.target.value)} /></label>
          <label>Your Role/Title<input value={senderRole} onChange={(e) => setSenderRole(e.target.value)} /></label>
          <label>Writing Tone<select value={senderTone} onChange={(e) => setSenderTone(e.target.value)}><option>Professional</option><option>Friendly</option><option>Casual</option><option>Direct</option><option>Storytelling</option></select></label>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          <label>What You Offer<textarea rows={4} value={senderOffer} onChange={(e) => setSenderOffer(e.target.value)} placeholder="Describe the specific service, offer, or outcome you want the email to explain." /></label>
          <label>Email Signature<textarea rows={4} value={senderSignature} onChange={(e) => setSenderSignature(e.target.value)} placeholder={"Best regards\nYour name\nYour role"} /></label>
        </div>
      </div>
      <div className="settings-section">
        <h3>Autonomous Reply</h3>
        <div className={!autoReplyEnabled ? "settings-disabled" : ""}>
          <div className="mt-4 flex flex-wrap gap-4">
            <label className="toggle"><input type="checkbox" checked={autoReplyEnabled} onChange={(e) => setAutoReplyEnabled(e.target.checked)} /> Enable Auto-Reply System</label>
          </div>
          <div className="form-grid mt-4">
            <label>Reply Mode<select value={autoReplyMode} disabled={!autoReplyEnabled || autoReplyMode === "autonomous"} onChange={(e) => setAutoReplyMode(e.target.value as "propose" | "autonomous")}><option value="propose">Propose (confirmation required)</option><option value="autonomous" disabled>Autonomous (enable from Auto-Reply panel)</option></select></label>
            <label>Auto-Reply Daily Cap<input type="number" min={0} disabled={!autoReplyEnabled} value={autoReplyDailyCap} onChange={(e) => setAutoReplyDailyCap(Number(e.target.value))} /></label>
            <label>Minimum Gap Between Replies (minutes)<input type="number" min={0} disabled={!autoReplyEnabled} value={autoReplyMinGap} onChange={(e) => setAutoReplyMinGap(Number(e.target.value))} /></label>
          </div>
          {autoReplyMode === "autonomous" && autoReplyEnabled && (
            <div className="notice warning">Autonomous mode was governed-confirmed in the Auto-Reply panel. Use Stop Now there to disable provider execution immediately.</div>
          )}
          <div className="mt-4">
            <div className="text-sm font-semibold text-muted">Safe Intents</div>
            <div className="mt-2 flex flex-wrap gap-4">
              <label className="toggle"><input type="checkbox" disabled={!autoReplyEnabled} checked={hasSafeIntent("positive_interest")} onChange={(e) => toggleSafeIntent("positive_interest", e.target.checked)} /> Interested</label>
              <label className="toggle"><input type="checkbox" disabled={!autoReplyEnabled} checked={hasSafeIntent("objection")} onChange={(e) => toggleSafeIntent("objection", e.target.checked)} /> Objection</label>
              <label className="toggle"><input type="checkbox" disabled={!autoReplyEnabled} checked={hasSafeIntent("question")} onChange={(e) => toggleSafeIntent("question", e.target.checked)} /> Question</label>
              <label className="toggle"><input type="checkbox" disabled={!autoReplyEnabled} checked={hasSafeIntent("unknown")} onChange={(e) => toggleSafeIntent("unknown", e.target.checked)} /> Other</label>
            </div>
          </div>
        </div>
      </div>
      <label className="mt-4 block">
        What are you selling / why are you reaching out?
        <textarea
          rows={4}
          placeholder="Describe what you are selling, who you want to reach, and the goal of the campaign."
          value={campaignContext}
          onChange={(e) => setCampaignContext(e.target.value)}
        />
      </label>
      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <label>Groq Keys (comma-separated)<input type="password" autoComplete="new-password" value={groq} onChange={(e) => setGroq(e.target.value)} /></label>
        <label>Gemini Keys (comma-separated)<input type="password" autoComplete="new-password" value={gemini} onChange={(e) => setGemini(e.target.value)} /></label>
      </div>
      <div className="settings-section">
        <h3>AI Model Configuration</h3>
        <div className="form-grid">
          <label>Groq Model<select value={groqModel} onChange={(e) => setGroqModel(e.target.value)}><option value="llama-3.3-70b-versatile">Llama 3.3 70B Versatile</option><option value="llama-3.1-8b-instant">Llama 3.1 8B Instant</option><option value="gemma2-9b-it">Gemma2 9B IT</option><option value="mixtral-8x7b-32768">Mixtral 8x7B 32768</option><option value="openai/gpt-oss-120b">OpenAI GPT-OSS 120B</option><option value="openai/gpt-oss-20b">OpenAI GPT-OSS 20B</option></select></label>
          <label>Gemini Model<select value={geminiModel} onChange={(e) => setGeminiModel(e.target.value)}><option>gemini-2.5-flash</option></select></label>
        </div>
      </div>
      <div className="settings-section">
        <h3>Follow-up Templates</h3>
        <div className="grid gap-3 lg:grid-cols-2">
          <label>Follow-up Template 1<textarea rows={4} value={followTemplate1} onChange={(e) => setFollowTemplate1(e.target.value)} /></label>
          <label>Follow-up Template 2<textarea rows={4} value={followTemplate2} onChange={(e) => setFollowTemplate2(e.target.value)} /></label>
        </div>
      </div>
      <div className="settings-section">
        <h3>Suppression & Sending</h3>
        <label>Blocked Domains<textarea rows={3} value={blockedDomains} onChange={(e) => setBlockedDomains(e.target.value)} placeholder={"competitor.com\ndonotcontact.org"} /></label>
        <div className="form-grid mt-4">
          <label>Send Window Start<input type="time" value={sendWindowStart} onChange={(e) => setSendWindowStart(e.target.value)} /></label>
          <label>Send Window End<input type="time" value={sendWindowEnd} onChange={(e) => setSendWindowEnd(e.target.value)} /></label>
          <label>Timezone<input value={sendTimezone} onChange={(e) => setSendTimezone(e.target.value)} /></label>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-4">
        <label className="toggle"><input type="checkbox" checked={autoProcessEnabled} onChange={(e) => setAutoProcessEnabled(e.target.checked)} /> Best-effort automation</label>
        <label className="toggle"><input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> Dry run</label>
        <label className="toggle"><input type="checkbox" checked={warmUpMode} onChange={(e) => setWarmUpMode(e.target.checked)} /> Warm-up Mode</label>
        {warmUpMode && <span className="fingerprint">Ramp limit: {settings?.warm_up_current_limit ?? dailyCap}/day</span>}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        {(settings?.groq_keys_fingerprints ?? []).map((item) => <span className="fingerprint" key={item}>{item}</span>)}
        {(settings?.gemini_keys_fingerprints ?? []).map((item) => <span className="fingerprint" key={item}>{item}</span>)}
      </div>
    </Panel>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ImportSummary({ summary }: { summary: Record<string, number> }) {
  const entries = [
    ["Imported", summary.accepted ?? 0],
    ["Restored", summary.restored ?? 0],
    ["Skipped", summary.rejected ?? 0],
    ["Duplicate", summary.duplicate ?? 0],
    ["Suppressed", summary.suppressed ?? 0],
    ["Total", summary.total ?? 0]
  ];
  return (
    <div className="metrics import-summary" aria-label="Import result summary">
      {entries.map(([label, value]) => (
        <Metric key={String(label)} label={String(label)} value={String(value)} />
      ))}
    </div>
  );
}

function importSummaryToast(summary: Record<string, number>) {
  const imported = summary.accepted ?? 0;
  const restored = summary.restored ?? 0;
  const skipped = summary.rejected ?? 0;
  return `Import committed: ${imported} imported, ${restored} restored, ${skipped} skipped`;
}

function ImportRows({ rows }: { rows: ImportPreview["rows"] }) {
  return (
    <DataTable
      columns={["row", "email", "creator", "website", "notes", "tags", "info", "source", "status", "reason"]}
      rows={rows.map((row) => {
        const data = row.parsed_data ?? {};
        return [
          String(row.row_num),
          row.email,
          previewValue(data.creator_name ?? data.business_name),
          previewValue(data.website_url),
          previewValue(data.notes, 140),
          previewValue(Array.isArray(data.tags) ? data.tags.join(", ") : data.tags),
          previewValue(data.personalization ?? data.info, 160),
          previewValue(data.source),
          row.status,
          row.reason ?? ""
        ];
      })}
    />
  );
}

function previewValue(value: unknown, maxLength = 90): string {
  if (value === null || value === undefined) return "";
  return truncateDisplay(String(value), maxLength);
}

function truncateDisplay(value: string, maxLength: number): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > maxLength ? `${compact.slice(0, maxLength - 1)}...` : compact;
}

function DataTable({ columns, rows }: { columns: string[]; rows: string[][] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={`${idx}-${row.join("-")}`}>
              {row.map((cell, cellIdx) => <td key={`${cellIdx}-${cell}`}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default App;
