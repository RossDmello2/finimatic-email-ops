from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


CAPABILITY_CATALOG: dict[str, dict[str, Any]] = {
    "email_read_inbox": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "optional_slots": ["date_range", "limit"],
        "source_label": "Mailbox",
        "max_results": 25,
    },
    "email_search_thread": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "optional_slots": ["query", "sender", "recipient"],
        "source_label": "Mailbox",
        "max_results": 10,
    },
    "email_read_thread": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["contact_id"],
        "source_label": "Mailbox",
        "max_snippet_chars": 200,
        "max_snippets": 5,
    },
    "email_generate_draft": {
        "class": "draft_local",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["contact_id"],
        "optional_slots": ["reply_goal", "tone"],
        "source_label": "Draft Generator",
    },
    "email_update_draft": {
        "class": "draft_local",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["pending_draft_id", "instruction"],
        "source_label": "Draft Generator",
    },
    "email_send_draft": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["draft_id", "_confirmed_action_id"],
        "source_label": "Email Provider",
    },
    "conversation_send_reply": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["contact_id", "subject", "body"],
        "source_label": "Conversations",
    },
    "auto_reply_approve": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["draft_id"],
        "source_label": "Auto-Reply",
    },
    "auto_reply_enable_autonomous": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": [],
        "source_label": "Auto-Reply",
    },
    "contact_resolve": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["name_or_email"],
        "source_label": "Contacts",
        "max_results": 5,
    },
    "followup_status": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "optional_slots": ["contact_id"],
        "source_label": "Follow-ups",
    },
    "queue_status": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "source_label": "Queue",
    },
    "evidence_read_contact": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["contact_id"],
        "source_label": "Evidence",
    },
    "evidence_check_draft": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["draft_id"],
        "source_label": "Evidence",
    },
    "enrichment_run_contact": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["contact_id"],
        "source_label": "Enrichment",
    },
    "enrichment_run_selected": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["contact_ids"],
        "source_label": "Enrichment",
    },
    "verification_run_contact": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["contact_id"],
        "source_label": "Verification",
    },
    "verification_run_selected": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["contact_ids"],
        "source_label": "Verification",
    },
    "deliverability_read_summary": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "source_label": "Deliverability",
    },
    "deliverability_explain_queue_block": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": ["queue_id"],
        "source_label": "Deliverability",
    },
    "workflow_run_start": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["workflow_id"],
        "source_label": "Workflows",
    },
    "workflow_retry_failed": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["workflow_id"],
        "source_label": "Workflows",
    },
    "crm_preview_sync": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["provider", "contact_id"],
        "source_label": "Integrations",
    },
    "crm_confirm_sync": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["provider", "preview_id"],
        "source_label": "Integrations",
    },
    "campaign_policy_check": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "source_label": "Campaign Policy",
    },
    "campaign_activate": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["campaign_id"],
        "source_label": "Campaigns",
    },
    "sequence_enroll_contacts": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["sequence_id", "contact_ids"],
        "source_label": "Sequences",
    },
    "mailbox_ramp_preview": {
        "class": "private_read",
        "side_effect": False,
        "confirmation_required": False,
        "required_slots": [],
        "source_label": "Deliverability",
    },
    "mailbox_ramp_confirm": {
        "class": "side_effect",
        "side_effect": True,
        "confirmation_required": True,
        "required_slots": ["mailbox_id"],
        "source_label": "Deliverability",
    },
}


CAPABILITY_TIERS: dict[str, set[str]] = {
    "AMBIENT": {
        "campaign_intelligence",
        "context_refresh",
        "fuzzy_search",
        "proactive_surface",
        "static_help",
        "get_campaign_stats",
        "get_reply_list",
        "get_contact_list",
        "get_contact_detail",
        "get_queue_status",
        "get_followup_status",
        "get_send_history",
        "get_conversation_thread",
        "get_audit_summary",
        "search_contacts",
        "template_preview",
    },
    "READ": {
        "email_read_inbox",
        "email_search_thread",
        "email_read_thread",
        "contact_resolve",
        "followup_status",
        "queue_status",
        "evidence_read_contact",
        "evidence_check_draft",
        "deliverability_read_summary",
        "deliverability_explain_queue_block",
        "campaign_policy_check",
        "mailbox_ramp_preview",
        "group_resolve",
        "draft_preview",
    },
    "DRAFT": {
        "email_generate_draft",
        "email_update_draft",
        "template_generate",
        "draft_save",
        "auto_reply_propose",
    },
    "ACTION": {
        "email_send_draft",
        "conversation_send_reply",
        "auto_reply_approve",
        "auto_reply_enable_autonomous",
        "contact_suppress",
        "contact_unsuppress",
        "bulk_approve_drafts",
        "campaign_activate",
        "enrichment_run_contact",
        "enrichment_run_selected",
        "verification_run_contact",
        "verification_run_selected",
        "workflow_run_start",
        "workflow_retry_failed",
        "crm_preview_sync",
        "crm_confirm_sync",
        "sequence_enroll_contacts",
        "mailbox_ramp_confirm",
        "followup_approve",
        "followup_draft_approve",
    },
}


class CapabilityCheckResult(BaseModel):
    allowed: bool
    tier: str
    requires_confirmation: bool = False
    redirect_to: Optional[str] = None
    denial_reason: Optional[str] = None


def check_capability_tiered(capability: str, channel: str) -> CapabilityCheckResult:
    if capability in CAPABILITY_TIERS["AMBIENT"]:
        return CapabilityCheckResult(allowed=True, tier="AMBIENT")

    if channel == "awareness":
        return CapabilityCheckResult(allowed=True, tier="AMBIENT", redirect_to="campaign_intelligence")

    if channel == "task":
        if capability in CAPABILITY_TIERS["READ"]:
            return CapabilityCheckResult(allowed=True, tier="READ")
        if capability in CAPABILITY_TIERS["DRAFT"]:
            return CapabilityCheckResult(allowed=True, tier="DRAFT")
        if capability in CAPABILITY_TIERS["ACTION"]:
            return CapabilityCheckResult(
                allowed=False,
                tier="ACTION",
                denial_reason=f"'{capability}' requires the action channel",
            )
        return CapabilityCheckResult(allowed=True, tier="AMBIENT", redirect_to="campaign_intelligence")

    if channel == "action":
        if capability in CAPABILITY_TIERS["ACTION"]:
            return CapabilityCheckResult(allowed=True, tier="ACTION", requires_confirmation=True)
        if capability in CAPABILITY_TIERS["READ"]:
            return CapabilityCheckResult(allowed=True, tier="READ")
        if capability in CAPABILITY_TIERS["DRAFT"]:
            return CapabilityCheckResult(allowed=True, tier="DRAFT")

    return CapabilityCheckResult(
        allowed=False,
        tier="DENIED",
        denial_reason=f"'{capability}' not recognized for channel '{channel}'",
    )


def validate_capability(value: str) -> str:
    if value not in CAPABILITY_CATALOG:
        raise ValueError(f"capability is not allowed: {value}")
    return value


def get_capability(name: str) -> dict[str, Any] | None:
    return CAPABILITY_CATALOG.get(name)


def source_label_for_capability(name: str) -> str:
    capability = get_capability(name)
    if not capability:
        return "System"
    return str(capability["source_label"])


def required_slots_for_capability(name: str) -> list[str]:
    capability = get_capability(name)
    if not capability:
        return []
    return list(capability.get("required_slots", []))
