from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CrmEntity = Literal["lead", "deal"]

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_RE = re.compile(r"^\+?[0-9\s()\-]+$")


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not _PHONE_RE.fullmatch(value):
        return None
    digits = re.sub(r"\D", "", value)
    if not 10 <= len(digits) <= 15:
        return None
    return ("+" if value.startswith("+") else "") + digits


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL_RE.fullmatch(value):
        return None
    return value


class RequestFields(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    customer_name: str | None = Field(default=None, max_length=200)
    need: str | None = Field(default=None, max_length=4000)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    company: str | None = Field(default=None, max_length=300)
    budget: str | None = Field(default=None, max_length=200)
    timeline: str | None = Field(default=None, max_length=200)
    consent: bool | None = None

    @field_validator(
        "customer_name",
        "need",
        "phone",
        "email",
        "company",
        "budget",
        "timeline",
        mode="before",
    )
    @classmethod
    def empty_string_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        return normalize_phone(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return normalize_email(value)

    def merged(self, incoming: "RequestFields") -> "RequestFields":
        updates = {
            name: getattr(incoming, name)
            for name in type(self).model_fields
            if getattr(incoming, name) is not None
        }
        merged = self.model_copy(update=updates)
        return merged.model_copy(
            update={
                "phone": normalize_phone(merged.phone),
                "email": normalize_email(merged.email),
            }
        )

    @property
    def has_contact(self) -> bool:
        return bool(normalize_phone(self.phone) or normalize_email(self.email))

    @property
    def is_complete(self) -> bool:
        return bool(
            self.customer_name
            and self.need
            and self.has_contact
            and self.consent is True
        )

    def missing_requirements(self) -> list[str]:
        missing: list[str] = []
        if not self.customer_name:
            missing.append("как к вам обращаться")
        if not self.need:
            missing.append("какой товар или услуга вам нужна")
        if not self.has_contact:
            missing.append("телефон или email для связи")
        if self.consent is not True:
            missing.append("согласие на передачу заявки менеджеру")
        return missing


class AssistantTurn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=3500)
    fields: RequestFields = Field(default_factory=RequestFields)
    qualified: bool = False
    crm_entity: CrmEntity | None = None
    summary: str | None = Field(default=None, max_length=1500)

    @field_validator("summary", mode="before")
    @classmethod
    def empty_summary_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@dataclass(slots=True)
class Conversation:
    chat_id: int
    telegram_user_id: int
    username: str | None
    fields: RequestFields
    summary: str | None
    qualified: bool
    crm_entity: CrmEntity | None
    crm_id: int | None
    crm_contact_id: int | None

    @property
    def is_created(self) -> bool:
        return self.crm_id is not None
