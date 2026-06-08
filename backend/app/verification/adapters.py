from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.db.models import Contact


@dataclass(frozen=True)
class VerificationProviderResult:
    provider: str
    status: str
    provider_status: str
    confidence: float
    is_role_based: bool = False
    is_disposable: bool = False
    is_catch_all: bool = False
    mx_present: bool = False
    cost_units: int = 0
    raw_response_redacted: str | None = None


class VerificationProviderAdapter(Protocol):
    name: str

    def verify(self, db: Session, contact: Contact) -> VerificationProviderResult:
        ...
