from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Literal

from app.ai.gemini_pool import GEMINI_MODEL_DEFAULT
from pydantic import ValidationError

from app.ai.model_ids import normalize_groq_model
from app.ai.prompts import SenderProfile, draft_user_prompt, system_prompt
from app.ai.schema import AIFailure, DraftSuggestion
from app.db.models import Contact, Draft

GROQ_MODEL_DEFAULT = "llama-3.3-70b-versatile"
PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")
SCHEDULING_PLACEHOLDER_RE = re.compile(r"\[[^\]]*(insert|calendly|link|placeholder|todo)[^\]]*\]", re.IGNORECASE)
BANNED_DRAFT_REPLACEMENTS = {
    "leverage": "use",
    "synergy": "fit",
    "cutting-edge": "practical",
    "innovative solution": "system",
    "reach out": "talk",
    "circle back": "continue",
    "touch base": "continue",
    "paradigm": "approach",
    "value-add": "useful note",
    "I wanted to follow up": "I am following up",
    "Just checking in": "Following up",
}
SIGNOFF_LINE_RE = re.compile(r"^(?:best regards|regards|thanks|thank you|sincerely|best)[,.]?$", re.IGNORECASE)
UNGROUNDED_CONTACT_CLAIM_RE = re.compile(
    r"\b(?:from my research,\s*)?(?:it seems\s+)?(?:I\s+)?(?:came across|noticed|saw|found|observed)?\s*"
    r"(?:your profile|your work|your content|your expertise|that you're|you're|you are)[^.]*\.",
    re.IGNORECASE,
)
PLATFORM_RE = re.compile(r"\b(?:Coursera|Udemy)\b", re.IGNORECASE)


def _signature_suffix_pattern_for_lines(lines: list[str]) -> re.Pattern[str] | None:
    if not lines:
        return None
    parts: list[str] = []
    for index, line in enumerate(lines):
        escaped = r"\s+".join(re.escape(piece) for piece in line.split())
        if index == 0 and SIGNOFF_LINE_RE.match(line):
            escaped = f"{escaped}[,.]?"
        parts.append(escaped)
    return re.compile(r"\s*" + r"\s+".join(parts) + r"\s*$", re.IGNORECASE)


def _signature_suffix_patterns(signature: str) -> list[re.Pattern[str]]:
    lines = [line.strip() for line in (signature or "").splitlines() if line.strip()]
    patterns: list[re.Pattern[str]] = []
    for count in range(len(lines), 1, -1):
        pattern = _signature_suffix_pattern_for_lines(lines[:count])
        if pattern is not None:
            patterns.append(pattern)
    sender_name_pattern = _short_sender_signature_pattern(lines)
    if sender_name_pattern is not None:
        patterns.append(sender_name_pattern)
    if not patterns:
        pattern = _signature_suffix_pattern_for_lines(lines)
        if pattern is not None:
            patterns.append(pattern)
    return patterns


def _short_sender_signature_pattern(lines: list[str]) -> re.Pattern[str] | None:
    if len(lines) < 2 or not SIGNOFF_LINE_RE.match(lines[0]):
        return None
    sender_name = lines[1].strip()
    first_name = sender_name.split()[0] if sender_name.split() else sender_name
    names = [sender_name]
    if first_name and first_name.lower() != sender_name.lower():
        names.append(first_name)
    name_pattern = "|".join(r"\s+".join(re.escape(piece) for piece in name.split()) for name in names if name)
    if not name_pattern:
        return None
    return re.compile(
        r"\s*(?:best regards|regards|thanks|thank you|sincerely|best)[,.]?\s+(?:" + name_pattern + r")\s*$",
        re.IGNORECASE,
    )


def _ensure_single_signature(body: str, signature: str) -> str:
    value = (body or "").strip()
    expected = (signature or "").strip()
    if not expected:
        return value

    signature_patterns = _signature_suffix_patterns(expected)
    while signature_patterns:
        for signature_pattern in signature_patterns:
            cleaned = signature_pattern.sub("", value).strip()
            if cleaned != value:
                value = cleaned
                break
        else:
            break

    while True:
        cleaned = re.sub(r"\n*(?:best regards|regards|thanks|thank you|sincerely|best)[,.]?\s*$", "", value, flags=re.IGNORECASE).strip()
        if cleaned == value:
            break
        value = cleaned

    return f"{value}\n\n{expected}".strip()


def _has_public_contact_evidence(contact: Contact) -> bool:
    return any(
        str(value or "").strip()
        for value in (
            contact.website_url,
            contact.personalization,
            contact.notes,
        )
    )


def _soften_ungrounded_contact_claims(body: str) -> tuple[str, bool]:
    value = body or ""
    changed = False
    neutral = "I thought this might be relevant if it maps to your current priorities."

    value, count = UNGROUNDED_CONTACT_CLAIM_RE.subn(neutral, value, count=1)
    changed = changed or count > 0

    value, count = re.subn(r"\bGiven your background,\s*", "If this is relevant, ", value, flags=re.IGNORECASE)
    changed = changed or count > 0

    value, count = re.subn(r"\bAs a\s+(?:Coursera|Udemy)\s+creator\b", "If this is relevant to your work", value, flags=re.IGNORECASE)
    changed = changed or count > 0

    value, count = re.subn(r"\byour\s+(?:Coursera|Udemy)\s+courses?\b", "your work", value, flags=re.IGNORECASE)
    changed = changed or count > 0

    value, count = re.subn(r"\b(?:Coursera|Udemy)\s+courses?\b", "your work", value, flags=re.IGNORECASE)
    changed = changed or count > 0

    value, count = re.subn(r"\bon\s+platforms?\s+(?:like|such as)\s+(?:Coursera|Udemy)\b", "in your work", value, flags=re.IGNORECASE)
    changed = changed or count > 0

    value, count = re.subn(r"\bon\s+(?:Coursera|Udemy)\b", "in your work", value, flags=re.IGNORECASE)
    changed = changed or count > 0

    value, count = PLATFORM_RE.subn("your work", value)
    changed = changed or count > 0

    value = re.sub(r"[ \t]{2,}", " ", value).strip()
    return value, changed


class AIGateway:
    def __init__(
        self,
        groq_keys: list[str],
        gemini_keys: list[str],
        campaign_context: str | None = None,
        sender_profile: SenderProfile | None = None,
        groq_model: str | None = None,
        gemini_model: str | None = None,
    ):
        self.groq_keys = groq_keys
        self.gemini_keys = gemini_keys
        self.campaign_context = campaign_context
        self.sender_profile = sender_profile
        self.groq_model = normalize_groq_model(groq_model, GROQ_MODEL_DEFAULT)
        self.gemini_model = (gemini_model or GEMINI_MODEL_DEFAULT).strip() or GEMINI_MODEL_DEFAULT

    def _parse_suggestion(self, raw: str, provider: str) -> DraftSuggestion | AIFailure:
        try:
            data = json.loads(raw)
            return self._sanitize_suggestion(DraftSuggestion.model_validate(data))
        except (json.JSONDecodeError, ValidationError) as exc:
            return AIFailure(error_code="malformed_output", provider=provider, detail=exc.__class__.__name__)

    def _sanitize_suggestion(self, suggestion: DraftSuggestion) -> DraftSuggestion:
        subject = PLACEHOLDER_RE.sub("", suggestion.subject).strip()
        body = suggestion.body
        warnings = list(suggestion.warnings)

        if SCHEDULING_PLACEHOLDER_RE.search(body):
            cleaned_lines: list[str] = []
            inserted_next_step = False
            for line in body.splitlines():
                if SCHEDULING_PLACEHOLDER_RE.search(line):
                    if not inserted_next_step:
                        cleaned_lines.append(
                            "If you're open to exploring this further, please share two times that work for a brief conversation."
                        )
                        inserted_next_step = True
                    continue
                cleaned_lines.append(line)
            body = "\n".join(cleaned_lines)
            warnings.append("Removed placeholder scheduling link.")
        elif PLACEHOLDER_RE.search(body):
            body = PLACEHOLDER_RE.sub("", body)
            warnings.append("Removed placeholder text.")

        placeholder_title_re = re.compile(r"['\"]Your [^'\"]*Title['\"]", re.IGNORECASE)
        if placeholder_title_re.search(body):
            body = placeholder_title_re.sub("your work", body)
            warnings.append("Removed placeholder title.")

        quoted_course_title_re = re.compile(
            r"((?:solo\s+)?course\s+(?:on|at)\s+[^\n]+?)\s*[-:]\s*['\"][^'\"]+['\"]",
            re.IGNORECASE,
        )
        if quoted_course_title_re.search(body):
            body = quoted_course_title_re.sub(r"\1", body)
            warnings.append("Removed unsupported invented course title.")

        if re.search(r"\b(long-time fan|following your work)\b", body, re.IGNORECASE):
            body = re.sub(
                r"\bI've been following your work(?:\s+in\s+[^.]+)?\.",
                "I came across your work and thought this might be relevant.",
                body,
                flags=re.IGNORECASE,
            )
            body = re.sub(
                r"\bI have been following your work(?:\s+in\s+[^.]+)?\.",
                "I came across your work and thought this might be relevant.",
                body,
                flags=re.IGNORECASE,
            )
            body = re.sub(r"\bI've been a long-time fan of\b", "I came across", body, flags=re.IGNORECASE)
            body = re.sub(r"\bI have been a long-time fan of\b", "I came across", body, flags=re.IGNORECASE)
            body = re.sub(r"\blong-time fan\b", "interested observer", body, flags=re.IGNORECASE)
            warnings.append("Removed unsupported familiarity claim.")

        if re.search(r"\b(impressed by|impressed with|noticed your recent work)\b", body, re.IGNORECASE):
            body = re.sub(
                r"\bI noticed your recent work on\s*(?:and\s*)?I'?m impressed by[^.]*\.",
                "I came across your work and thought this might be relevant.",
                body,
                flags=re.IGNORECASE,
            )
            body = re.sub(
                r"\bI(?:'m| am) impressed by[^.]*\.",
                "I came across your work and thought this might be relevant.",
                body,
                flags=re.IGNORECASE,
            )
            body = re.sub(
                r"\bI noticed your recent work[^.]*\.",
                "I came across your work and thought this might be relevant.",
                body,
                flags=re.IGNORECASE,
            )
            warnings.append("Removed unsupported praise claim.")

        if re.search(r"\bproprietary\b", body, re.IGNORECASE):
            body = re.sub(r"\bproprietary\s+", "", body, flags=re.IGNORECASE)
            warnings.append("Removed unsupported proprietary claim.")

        corrected_body = re.sub(
            r"\bRAG\s*\((?!retrieval-augmented generation\))[^)]*\)",
            "RAG (retrieval-augmented generation)",
            body,
            flags=re.IGNORECASE,
        )
        if corrected_body != body:
            body = corrected_body
            warnings.append("Corrected unsupported RAG acronym expansion.")

        offer_text = " ".join(
            part
            for part in (
                self.campaign_context or "",
                self.sender_profile.sender_offer if self.sender_profile else "",
            )
            if part
        )
        if "video" not in offer_text.lower() and re.search(r"\bvideos?\b", body, re.IGNORECASE):
            body = re.sub(r"\bPDFs?, videos?, and more\b", "provided materials", body, flags=re.IGNORECASE)
            body = re.sub(r"\bvideos?\b", "provided materials", body, flags=re.IGNORECASE)
            warnings.append("Removed unsupported video-processing claim.")

        cleaned_body = body
        for banned, replacement in BANNED_DRAFT_REPLACEMENTS.items():
            cleaned_body = re.sub(rf"\b{re.escape(banned)}\b", replacement, cleaned_body, flags=re.IGNORECASE)
        cleaned_body = re.sub(r"https?://\S+", "please share two suitable times", cleaned_body)
        if cleaned_body != body:
            body = cleaned_body
            warnings.append("Removed banned sales phrasing or unsupported link.")

        if self.sender_profile and self.sender_profile.sender_signature.strip():
            body = _ensure_single_signature(body, self.sender_profile.sender_signature)

        body = re.sub(r"[ \t]{2,}", " ", body)
        return DraftSuggestion(subject=subject, body=body.strip(), warnings=warnings[:10])

    def _sanitize_contact_grounding(self, contact: Contact, suggestion: DraftSuggestion) -> DraftSuggestion:
        if _has_public_contact_evidence(contact):
            return suggestion

        subject, subject_changed = _soften_ungrounded_contact_claims(suggestion.subject)
        body, body_changed = _soften_ungrounded_contact_claims(suggestion.body)
        warnings = list(suggestion.warnings)
        if subject_changed or body_changed:
            warnings.append("Softened unsupported contact-specific claim.")
        if self.sender_profile and self.sender_profile.sender_signature.strip():
            body = _ensure_single_signature(body, self.sender_profile.sender_signature)
        return DraftSuggestion(subject=subject.strip(), body=body.strip(), warnings=warnings[:10])

    async def generate_draft(
        self,
        contact: Contact,
        provider: Literal["groq", "gemini", "auto", "manual", "malformed_test"],
        tone: str = "professional",
        length: str = "medium",
        instruction: str | None = None,
    ) -> DraftSuggestion | AIFailure:
        if provider == "manual":
            return DraftSuggestion(subject="Manual draft", body="Write the email body here.", warnings=[])
        if provider == "malformed_test":
            return self._parse_suggestion("{not valid json", provider)
        if provider == "auto":
            first = await self.generate_draft(contact, "groq", tone, length, instruction)
            if isinstance(first, DraftSuggestion):
                return first
            return await self.generate_draft(contact, "gemini", tone, length, instruction)

        if os.getenv("FINIMATIC_FAKE_AI") == "1":
            name = contact.creator_name or contact.business_name or "there"
            signature = self.sender_profile.sender_signature if self.sender_profile else "Best regards"
            requested = f"\n\nRequested angle: {' '.join(instruction.split())[:200]}" if instruction else ""
            return DraftSuggestion(
                subject=f"Quick idea for {name}",
                body=(
                    f"Hi {name},\n\nI noticed your work and wanted to share a concise Finimatic outreach idea.\n\n"
                    f"{requested}\n\n{signature}"
                ),
                warnings=[],
            )

        if provider == "groq":
            return await self._call_groq(contact, tone, length, instruction)
        if provider == "gemini":
            return await self._call_gemini(contact, tone, length, instruction)
        return AIFailure(error_code="missing_api_key", provider=str(provider), detail="")

    async def rewrite_draft(self, draft: Draft, instruction: str, provider: str) -> DraftSuggestion | AIFailure:
        if not draft.body:
            return AIFailure(error_code="malformed_output", provider=provider, detail="empty draft")
        return DraftSuggestion(subject=draft.subject, body=f"{draft.body}\n\nRevision note: {instruction}", warnings=[])

    async def flag_risks(self, draft: Draft) -> list[str]:
        return []

    def model_for_provider(self, provider: str) -> str | None:
        if provider == "groq":
            return self.groq_model
        if provider == "gemini":
            return self.gemini_model
        return None

    async def _call_groq(self, contact: Contact, tone: str, length: str, instruction: str | None = None) -> DraftSuggestion | AIFailure:
        if not self.groq_keys:
            return AIFailure(error_code="missing_api_key", provider="groq", detail="")

        def call(key: str):
            from groq import Groq

            client = Groq(api_key=key)
            response = client.chat.completions.create(
                model=self.groq_model,
                messages=[
                    {"role": "system", "content": system_prompt(self.campaign_context, self.sender_profile)},
                    {"role": "user", "content": draft_user_prompt(contact, tone, length, instruction)},
                ],
                response_format={"type": "json_object"},
                timeout=float(os.getenv("GROQ_TIMEOUT_S", "30")),
            )
            return response.choices[0].message.content or ""

        rate_limited = 0
        last_error = ""
        for key in self.groq_keys:
            try:
                raw = await asyncio.to_thread(call, key)
            except Exception as exc:
                last_error = exc.__class__.__name__
                if "429" in str(exc) or "rate" in str(exc).lower():
                    rate_limited += 1
                    continue
                return AIFailure(error_code=_groq_error_code(exc), provider="groq", detail=last_error)
            parsed = self._parse_suggestion(raw, "groq")
            if isinstance(parsed, DraftSuggestion):
                return self._sanitize_contact_grounding(contact, parsed)
            last_error = parsed.error_code
        if rate_limited == len(self.groq_keys):
            return AIFailure(error_code="model_unavailable_rate_limited", provider="groq", detail="all_keys_rate_limited")
        return AIFailure(error_code="malformed_output", provider="groq", detail=last_error)

    async def _call_gemini(self, contact: Contact, tone: str, length: str, instruction: str | None = None) -> DraftSuggestion | AIFailure:
        if not self.gemini_keys:
            return AIFailure(error_code="missing_api_key", provider="gemini", detail="")

        def call(key: str):
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=key)
            try:
                response = client.models.generate_content(
                    model=self.gemini_model,
                    contents=draft_user_prompt(contact, tone, length, instruction),
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt(self.campaign_context, self.sender_profile),
                        response_mime_type="application/json",
                    ),
                )
                return response.text or ""
            finally:
                close = getattr(client, "close", None)
                if close:
                    close()

        rate_limited = 0
        last_error = ""
        for key in self.gemini_keys:
            try:
                raw = await asyncio.to_thread(call, key)
            except Exception as exc:
                last_error = exc.__class__.__name__
                text = str(exc)
                if "429" in text or "RESOURCE_EXHAUSTED" in text:
                    rate_limited += 1
                    continue
                return AIFailure(error_code="transport_error", provider="gemini", detail=last_error)
            parsed = self._parse_suggestion(raw, "gemini")
            if isinstance(parsed, DraftSuggestion):
                return self._sanitize_contact_grounding(contact, parsed)
            last_error = parsed.error_code
        if rate_limited == len(self.gemini_keys):
            return AIFailure(error_code="model_unavailable_rate_limited", provider="gemini", detail="all_keys_rate_limited")
        return AIFailure(error_code="malformed_output", provider="gemini", detail=last_error)

    async def generate_subject_variants(self, draft: Draft) -> list[str] | AIFailure:
        if os.getenv("FINIMATIC_FAKE_AI") == "1":
            base = draft.subject or "Quick idea"
            return [f"{base} - option {index}"[:60] for index in range(1, 4)]
        if not self.groq_keys:
            return AIFailure(error_code="missing_api_key", provider="groq", detail="")

        def call():
            from groq import Groq

            client = Groq(api_key=self.groq_keys[0])
            response = client.chat.completions.create(
                model=self.groq_model,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Given this email body, suggest 3 alternative subject lines. "
                            "Each must be under 60 characters. Be specific to the recipient. "
                            "Return JSON array: [\"option1\", \"option2\", \"option3\"]\n\n"
                            f"Email body:\n{draft.body}"
                        ),
                    }
                ],
                timeout=30,
            )
            return response.choices[0].message.content or "[]"

        try:
            raw = await asyncio.to_thread(call)
        except Exception as exc:
            return AIFailure(error_code=_groq_error_code(exc), provider="groq", detail=exc.__class__.__name__)
        variants = self._parse_subject_variants(raw)
        if len(variants) < 3:
            return AIFailure(error_code="malformed_output", provider="groq", detail="too_few_variants")
        return variants[:3]

    def _parse_subject_variants(self, raw: str) -> list[str]:
        text = (raw or "").strip()
        candidates: list[str] = []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    parsed = None
            else:
                parsed = None
        if isinstance(parsed, dict):
            parsed = parsed.get("variants") or parsed.get("subjects") or parsed.get("options")
        if isinstance(parsed, list):
            candidates = [str(item) for item in parsed]
        if not candidates:
            for line in text.splitlines():
                cleaned = line.strip().lstrip("-*0123456789. )").strip().strip('"')
                if cleaned:
                    candidates.append(cleaned)
        seen: set[str] = set()
        variants: list[str] = []
        for item in candidates:
            value = " ".join(str(item).split()).strip().strip('"')
            if not value or value in seen:
                continue
            variants.append(value[:60])
            seen.add(value)
        return variants

    async def enrich_contact(self, contact: Contact) -> str | None:
        if os.getenv("FINIMATIC_FAKE_AI") == "1" and contact.website_url and not contact.personalization:
            name = contact.creator_name or contact.business_name or "This contact"
            return f"{name} appears to publish or sell educational content through {contact.website_url}."
        if not self.groq_keys or not contact.website_url:
            return None
        if contact.personalization:
            return None

        def call():
            from groq import Groq

            client = Groq(api_key=self.groq_keys[0])
            response = client.chat.completions.create(
                model=self.groq_model,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Given website URL: {contact.website_url}\n"
                            f"And name: {contact.creator_name or contact.business_name or 'unknown'}\n"
                            "Infer in 1-2 sentences: what does this person likely do or sell?\n"
                            "Return only the description, no JSON wrapper."
                        ),
                    }
                ],
                timeout=30,
            )
            return (response.choices[0].message.content or "").strip()

        try:
            text = await asyncio.to_thread(call)
        except Exception:
            return None
        return text[:1000] or None


def _groq_error_code(exc: Exception) -> str:
    text = str(exc).lower()
    if "model" in text and any(
        marker in text
        for marker in (
            "not found",
            "not exist",
            "does not exist",
            "invalid",
            "unsupported",
            "decommission",
            "unavailable",
            "400",
        )
    ):
        return "model_not_supported"
    return "transport_error"
