"""Frozen schema used by revision 0001.

This module is a verbatim model snapshot from commit
57fcf426b3212d0b6b0bd3f62295ca65bace7b57. It must not import the mutable
runtime model metadata.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    policy_block_reasons: Mapped[str | None] = mapped_column(Text)
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
    provider_msg_id: Mapped[str | None] = mapped_column(String)
    smtp_response: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False)
    sender_identity: Mapped[str] = mapped_column(String, nullable=False)
    sent_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String)
    error_detail: Mapped[str | None] = mapped_column(Text)


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
