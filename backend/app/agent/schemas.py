from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Capability = Literal[
    "email_read_inbox",
    "email_search_thread",
    "email_read_thread",
    "email_generate_draft",
    "email_update_draft",
    "email_send_draft",
    "conversation_send_reply",
    "auto_reply_approve",
    "auto_reply_enable_autonomous",
    "contact_resolve",
    "followup_status",
    "queue_status",
    "evidence_read_contact",
    "evidence_check_draft",
    "enrichment_run_contact",
    "enrichment_run_selected",
    "verification_run_contact",
    "verification_run_selected",
    "deliverability_read_summary",
    "deliverability_explain_queue_block",
    "workflow_run_start",
    "workflow_retry_failed",
    "crm_preview_sync",
    "crm_confirm_sync",
    "campaign_policy_check",
    "campaign_activate",
    "sequence_enroll_contacts",
    "mailbox_ramp_preview",
    "mailbox_ramp_confirm",
]


class GoalFrame(StrictModel):
    user_goal: str = Field(min_length=1)
    action_class: Literal["informational", "private_read", "draft_local", "side_effect", "clarification", "unsupported"]
    proposed_capability: str = ""
    required_slots: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class IntentDecision(StrictModel):
    intent: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    dialogue_act: Literal["new_intent", "continuation", "correction", "cancel"] = "new_intent"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_confirmation: bool = False
    rationale: str | None = None


class SlotAgentOutput(StrictModel):
    slots_filled: dict[str, Any] = Field(default_factory=dict)
    slots_missing: list[str] = Field(default_factory=list)
    ready_to_execute: bool = False
    clarification_question: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ToolPlan(StrictModel):
    capability: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    side_effect: bool = False
    source_label: str = Field(min_length=1)
    reason: str | None = None


class ChannelDecision(StrictModel):
    channel: Literal["awareness", "task", "action"]
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    routing_reason: str = ""


class CampaignSnapshot(StrictModel):
    content: str
    built_at: str
    char_count: int


class EvidenceEnvelope(StrictModel):
    capability: str
    source_label: str
    status: Literal["success", "empty", "error", "denied"]
    data: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    error_code: str | None = None
    latency_ms: int = Field(default=0, ge=0)


class PendingEmailAction(StrictModel):
    action_id: str
    capability: str = "email_send_draft"
    draft_id: str | None = None
    contact_id: str | None = None
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    goal: str | None = None
    evidence_summary: str | None = None
    policy_result: str | None = None
    proposed_side_effect: str | None = None
    confirmation_prompt: str
    expires_at: datetime

    @field_validator("body")
    @classmethod
    def truncate_preview_body(cls, value: str | None) -> str | None:
        return value[:5000] if value else value


class AgentDraft(StrictModel):
    draft_id: str
    contact_id: str
    to: str
    subject: str
    body: str
    warnings: list[str] = Field(default_factory=list)


class AgentChatRequest(StrictModel):
    session_token: str = Field(min_length=8)
    message: str = Field(min_length=1)
    provider: Literal["auto", "groq", "gemini"] = "auto"
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class AgentConfirmRequest(StrictModel):
    session_token: str = Field(min_length=8)
    action_id: str = Field(min_length=1)


class AgentCancelRequest(StrictModel):
    session_token: str = Field(min_length=8)


class AgentChatResponse(StrictModel):
    response: str
    source: str | None = None
    intent: str | None = None
    channel: Literal["awareness", "task", "action"] | None = None
    is_clarification: bool = False
    draft: AgentDraft | None = None
    pending_action: PendingEmailAction | None = None
    error_code: str | None = None
