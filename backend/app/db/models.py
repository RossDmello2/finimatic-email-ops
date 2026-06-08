from __future__ import annotations

import uuid

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    filename: Mapped[str | None] = mapped_column(String)
    format: Mapped[str] = mapped_column(String, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suppressed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    creator_name: Mapped[str | None] = mapped_column(String)
    business_name: Mapped[str | None] = mapped_column(String)
    website_url: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, nullable=False)
    provenance: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)
    personalization: Mapped[str | None] = mapped_column(Text)
    lead_category: Mapped[str | None] = mapped_column(String)
    custom_fields: Mapped[str | None] = mapped_column(Text)
    auto_reply_override: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="imported")
    send_stop_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    import_batch_id: Mapped[str | None] = mapped_column(ForeignKey("import_batches.id"))
    deleted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ImportRow(Base):
    __tablename__ = "import_rows"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    row_num: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_data: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(String)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"))


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    ai_provider: Mapped[str | None] = mapped_column(String)
    ai_model: Mapped[str | None] = mapped_column(String)
    warnings: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String)
    rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    contact: Mapped[Contact] = relationship()


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    subject_template: Mapped[str] = mapped_column(String, nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SendQueue(Base):
    __tablename__ = "send_queue"
    __table_args__ = (UniqueConstraint("contact_id", "sequence_num", name="uq_send_queue_contact_seq"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id"), nullable=False)
    sequence_num: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scheduled_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    schedule_source: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="approval_delay",
        server_default="approval_delay",
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    policy_block_reasons: Mapped[str | None] = mapped_column(Text)
    processing_started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    processing_token: Mapped[str | None] = mapped_column(String, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    contact: Mapped[Contact] = relationship()
    draft: Mapped[Draft] = relationship()


class SendAttempt(Base):
    __tablename__ = "send_attempts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    queue_id: Mapped[str] = mapped_column(String, nullable=False)
    contact_id: Mapped[str] = mapped_column(String, nullable=False)
    draft_id: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String, index=True)
    dispatch_lock_key: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    stop_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    provider_msg_id: Mapped[str | None] = mapped_column(String)
    tracking_message_id: Mapped[str | None] = mapped_column(String)
    smtp_response: Mapped[str | None] = mapped_column(Text)
    configured_transport: Mapped[str | None] = mapped_column(String)
    effective_transport: Mapped[str | None] = mapped_column(String)
    transport_source: Mapped[str | None] = mapped_column(String)
    simulated: Mapped[bool | None] = mapped_column(Boolean)
    provider_contacted: Mapped[bool | None] = mapped_column(Boolean)
    provider_accepted: Mapped[bool | None] = mapped_column(Boolean, index=True)
    provider_response_classification: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    sender_identity: Mapped[str] = mapped_column(String, nullable=False)
    sent_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FollowUpSequence(Base):
    __tablename__ = "follow_up_sequences"
    __table_args__ = (UniqueConstraint("contact_id", "sequence_num", name="uq_followup_contact_seq"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    sequence_num: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("drafts.id"))
    pending_draft_id: Mapped[str | None] = mapped_column(ForeignKey("drafts.id"))
    status: Mapped[str] = mapped_column(String, nullable=False, default="due")
    stop_reason: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    contact: Mapped[Contact] = relationship()


class Suppression(Base):
    __tablename__ = "suppressions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reply(Base):
    __tablename__ = "replies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    classified_as: Mapped[str] = mapped_column(String, nullable=False)
    intent: Mapped[str | None] = mapped_column(String)
    raw_summary: Mapped[str | None] = mapped_column(Text)
    external_message_id: Mapped[str | None] = mapped_column(String, index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str | None] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    auto_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    external_message_id: Mapped[str | None] = mapped_column(String, index=True)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    contact: Mapped[Contact] = relationship()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String)
    entity_id: Mapped[str | None] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String, nullable=False, default="system")
    payload: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthLoginTransaction(Base):
    __tablename__ = "auth_login_transactions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    flow_token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    state_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    nonce: Mapped[str] = mapped_column(String, nullable=False)
    code_verifier_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    return_path: Mapped[str] = mapped_column(String, nullable=False, default="/")
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OperatorSession(Base):
    __tablename__ = "operator_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    subject: Mapped[str] = mapped_column(String, nullable=False, index=True)
    roles_json: Mapped[str] = mapped_column(Text, nullable=False)
    issuer: Mapped[str] = mapped_column(String, nullable=False)
    audience: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProviderHealth(Base):
    __tablename__ = "provider_health"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    last_checked: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String)
    details: Mapped[str | None] = mapped_column(Text)


class CampaignPlan(Base):
    __tablename__ = "campaign_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    goal: Mapped[str | None] = mapped_column(Text)
    target_tags: Mapped[str | None] = mapped_column(String)
    step_1_draft: Mapped[str | None] = mapped_column(Text)
    step_2_draft: Mapped[str | None] = mapped_column(Text)
    step_3_draft: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="draft")
    contacts_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    stopped_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    session_token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    current_goal: Mapped[str | None] = mapped_column(Text)
    slots: Mapped[str | None] = mapped_column(Text)
    active_contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"))
    pending_action_id: Mapped[str | None] = mapped_column(Text)
    context_summary: Mapped[str | None] = mapped_column(Text)
    context_loaded_at: Mapped[str | None] = mapped_column(Text)
    contact_name_map: Mapped[str | None] = mapped_column(Text)
    turn_history: Mapped[str | None] = mapped_column(Text)
    current_channel: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PendingEmailActionRow(Base):
    __tablename__ = "pending_email_actions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(Text, nullable=False, default="email_send_draft")
    capability: Mapped[str] = mapped_column(Text, nullable=False, default="email_send_draft")
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id"), nullable=False)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    params_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_label: Mapped[str] = mapped_column(Text, nullable=False, default="Email Provider")
    confirmation_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consumed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceSource(Base):
    __tablename__ = "evidence_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    url: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String)
    source_type: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    retrieved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    reliability_tier: Mapped[str] = mapped_column(String, nullable=False, default="operator_supplied")
    allowed_use: Mapped[str] = mapped_column(String, nullable=False, default="draft_context")
    raw_excerpt_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeadFact(Base):
    __tablename__ = "lead_facts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    field_key: Mapped[str] = mapped_column(String, nullable=False)
    field_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_sources.id"))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_label: Mapped[str] = mapped_column(String, nullable=False, default="operator")
    source_type: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    fetched_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    extractor: Mapped[str] = mapped_column(String, nullable=False, default="operator")
    raw_snippet_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    contact: Mapped[Contact] = relationship()
    source: Mapped[EvidenceSource | None] = relationship()


class AccountFact(Base):
    __tablename__ = "account_facts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    account_key: Mapped[str | None] = mapped_column(String, index=True)
    field_key: Mapped[str] = mapped_column(String, nullable=False)
    field_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_sources.id"))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_label: Mapped[str] = mapped_column(String, nullable=False, default="operator")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    fetched_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    extractor: Mapped[str] = mapped_column(String, nullable=False, default="operator")
    raw_snippet_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[EvidenceSource | None] = relationship()


class DraftEvidenceCheck(Base):
    __tablename__ = "draft_evidence_checks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("drafts.id"), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    supported_claims_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    missing_evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    stale_facts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    neutral_copy_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    neutral_subject: Mapped[str | None] = mapped_column(Text)
    neutral_body: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    policy_version: Mapped[str] = mapped_column(String, nullable=False, default="evidence_v1")
    draft: Mapped[Draft | None] = relationship()
    contact: Mapped[Contact] = relationship()


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    provider: Mapped[str] = mapped_column(String, nullable=False, default="finimatic_rules")
    provider_status: Mapped[str | None] = mapped_column(String)
    is_role_based: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_disposable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_catch_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mx_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_verified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    raw_response_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    contact: Mapped[Contact | None] = relationship()


class EmailVerificationAttempt(Base):
    __tablename__ = "email_verification_attempts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    verification_id: Mapped[str] = mapped_column(ForeignKey("email_verifications.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    response_code: Mapped[str | None] = mapped_column(String)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_response_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verification: Mapped[EmailVerification] = relationship()


class SenderMailbox(Base):
    __tablename__ = "sender_mailboxes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    domain: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False, default="gmail")
    transport: Mapped[str] = mapped_column(String, nullable=False, default="gmail_api")
    status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    hourly_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    min_delay_s: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    ramp_stage: Mapped[str] = mapped_column(String, nullable=False, default="low_volume")
    last_health_check_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SenderDomainHealth(Base):
    __tablename__ = "sender_domain_health"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    domain: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    spf_status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    dkim_status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    dmarc_status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    alignment_status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    postmaster_status: Mapped[str] = mapped_column(String, nullable=False, default="not_connected")
    spam_rate_bucket: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    reputation_bucket: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    last_checked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DeliverabilityCheck(Base):
    __tablename__ = "deliverability_checks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    sender_email: Mapped[str | None] = mapped_column(String, index=True)
    domain: Mapped[str | None] = mapped_column(String, index=True)
    check_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    severity: Mapped[str] = mapped_column(String, nullable=False, default="info")
    details_redacted: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InboxPlacementTest(Base):
    __tablename__ = "inbox_placement_tests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    sender_email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    seed_email: Mapped[str] = mapped_column(String, nullable=False)
    recipient_domain: Mapped[str] = mapped_column(String, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="planned")
    placement: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    provider_msg_id: Mapped[str | None] = mapped_column(String)
    sent_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    details_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecipientDomainCap(Base):
    __tablename__ = "recipient_domain_caps"
    __table_args__ = (UniqueConstraint("sender_email", "recipient_domain", "window_date", name="uq_recipient_domain_cap_window"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    sender_email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    recipient_domain: Mapped[str] = mapped_column(String, nullable=False, index=True)
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    sent_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    window_date: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Workbook(Base):
    __tablename__ = "workbooks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkbookRow(Base):
    __tablename__ = "workbook_rows"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workbook_id: Mapped[str] = mapped_column(ForeignKey("workbooks.id"), nullable=False, index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    row_key: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    workbook: Mapped[Workbook] = relationship()
    contact: Mapped[Contact | None] = relationship()


class WorkbookColumn(Base):
    __tablename__ = "workbook_columns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workbook_id: Mapped[str] = mapped_column(ForeignKey("workbooks.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    step_type: Mapped[str] = mapped_column(String, nullable=False)
    config_json: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    workbook: Mapped[Workbook] = relationship()


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workbook_id: Mapped[str] = mapped_column(ForeignKey("workbooks.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    active_claim_key: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    execution_hash: Mapped[str | None] = mapped_column(String, index=True)
    lease_token: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String)
    lease_expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    checkpoint_json: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String, nullable=False, default="operator")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    workbook: Mapped[Workbook] = relationship()


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False, index=True)
    workbook_column_id: Mapped[str | None] = mapped_column(ForeignKey("workbook_columns.id"))
    step_type: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    config_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run: Mapped[WorkflowRun] = relationship()
    column: Mapped[WorkbookColumn | None] = relationship()


class WorkflowStepAttempt(Base):
    __tablename__ = "workflow_step_attempts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workflow_step_id: Mapped[str] = mapped_column(ForeignKey("workflow_steps.id"), nullable=False, index=True)
    workbook_row_id: Mapped[str | None] = mapped_column(ForeignKey("workbook_rows.id"), index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    attempt_num: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    input_hash: Mapped[str] = mapped_column(String, nullable=False)
    step_config_hash: Mapped[str] = mapped_column(String, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    step: Mapped[WorkflowStep] = relationship()
    row: Mapped[WorkbookRow | None] = relationship()


class CellOutput(Base):
    __tablename__ = "cell_outputs"
    __table_args__ = (UniqueConstraint("workbook_row_id", "workbook_column_id", "input_hash", "step_config_hash", name="uq_cell_output_hash"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    workbook_id: Mapped[str] = mapped_column(ForeignKey("workbooks.id"), nullable=False, index=True)
    workbook_row_id: Mapped[str] = mapped_column(ForeignKey("workbook_rows.id"), nullable=False, index=True)
    workbook_column_id: Mapped[str] = mapped_column(ForeignKey("workbook_columns.id"), nullable=False, index=True)
    workflow_step_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_steps.id"))
    input_hash: Mapped[str] = mapped_column(String, nullable=False)
    step_config_hash: Mapped[str] = mapped_column(String, nullable=False)
    output_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[str | None] = mapped_column(Text)
    cost_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("provider", "account_label", name="uq_integration_connection_provider_account"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    account_label: Mapped[str] = mapped_column(String, nullable=False, default="default")
    status: Mapped[str] = mapped_column(String, nullable=False, default="not_connected")
    auth_mode: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    scopes_redacted: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntegrationMapping(Base):
    __tablename__ = "integration_mappings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(ForeignKey("integration_connections.id"), nullable=False, index=True)
    local_field: Mapped[str] = mapped_column(String, nullable=False)
    external_field: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False, default="push")
    transform_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncJournal(Base):
    __tablename__ = "sync_journals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("integration_connections.id"))
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String)
    action: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    diff_json_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExternalWritePreview(Base):
    __tablename__ = "external_write_previews"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False, default="upsert")
    diff_json_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending_confirmation")
    active_claim_key: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    execution_hash: Mapped[str | None] = mapped_column(String, index=True)
    lease_token: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String)
    lease_expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExternalWriteAttempt(Base):
    __tablename__ = "external_write_attempts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    preview_id: Mapped[str | None] = mapped_column(ForeignKey("external_write_previews.id"), index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    response_code: Mapped[str | None] = mapped_column(String)
    external_id: Mapped[str | None] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    execution_hash: Mapped[str | None] = mapped_column(String, index=True)
    lease_token: Mapped[str | None] = mapped_column(String, index=True)
    error_code: Mapped[str | None] = mapped_column(String)
    details_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PendingAgentAction(Base):
    __tablename__ = "pending_agent_actions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    capability: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String)
    params_hash: Mapped[str] = mapped_column(String, nullable=False)
    source_label: Mapped[str] = mapped_column(String, nullable=False, default="System")
    confirmation_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    action_snapshot_redacted: Mapped[str | None] = mapped_column(Text)
    policy_snapshot_redacted: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consumed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
