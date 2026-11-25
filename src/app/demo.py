from __future__ import annotations

import json
import logging
import re

from app.models import AssistantTurn, RequestFields

logger = logging.getLogger("demo.crm")

_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s()\-]{8,}\d)(?!\d)")
_NAME_RE = re.compile(
    r"(?:меня зовут|мо[её] имя)\s+"
    r"([A-Za-zА-Яа-яЁё-]+(?:\s+[A-Za-zА-Яа-яЁё-]+)?)",
    re.IGNORECASE,
)
_DEAL_WORDS = ("заказать", "купить", "бюджет", "готов заказать", "оформить заказ")


class DemoAIService:
    """Deterministic dialogue for local testing without an AI provider."""

    async def close(self) -> None:
        pass

    async def generate_turn(
        self,
        history: list[dict[str, str]],
        current_fields: RequestFields,
    ) -> AssistantTurn:
        text = history[-1]["content"].strip()
        lowered = text.lower()
        updates: dict[str, object] = {}

        email_match = _EMAIL_RE.search(text)
        phone_match = _PHONE_RE.search(text)
        name_match = _NAME_RE.search(text)
        if email_match:
            updates["email"] = email_match.group(0).strip(".,;:!?)]}")
        if phone_match:
            updates["phone"] = phone_match.group(1)
        if name_match:
            updates["customer_name"] = name_match.group(1).strip()

        is_greeting = lowered.strip(" !?") in {"привет", "здравствуйте", "добрый день"}
        if current_fields.need is None and text and not is_greeting:
            updates["need"] = text
        elif (
            current_fields.need is not None
            and current_fields.customer_name is None
            and "customer_name" not in updates
            and not phone_match
            and not email_match
            and len(text) <= 60
            and not any(word in lowered for word in ("соглас", "разреша", "передава"))
        ):
            candidate = re.sub(r"^(?:я|меня зовут)\s+", "", text, flags=re.IGNORECASE)
            if candidate and not any(char.isdigit() for char in candidate):
                updates["customer_name"] = candidate.strip(" ,.;")

        if any(word in lowered for word in ("не соглас", "не разреша", "не передавай")):
            updates["consent"] = False
        elif any(
            word in lowered
            for word in ("соглас", "разреша", "подтвержда", "передавай", "да,")
        ) or lowered.strip(" !?") == "да":
            updates["consent"] = True

        incoming = RequestFields.model_validate(updates)
        fields = current_fields.merged(incoming)
        qualified = fields.is_complete

        if not fields.need:
            message = "Опишите, пожалуйста, какой товар или услуга вам нужна."
        elif not fields.customer_name:
            message = "Как к вам обращаться?"
        elif not fields.has_contact:
            message = "Оставьте, пожалуйста, телефон или email для связи."
        elif fields.consent is not True:
            message = "Разрешаете передать заявку и контактные данные менеджеру? Ответьте «да»."
        else:
            message = "Спасибо! Все данные для демо-заявки собраны."

        entity = "deal" if any(word in fields.need.lower() for word in _DEAL_WORDS) else "lead"
        return AssistantTurn(
            message=message,
            fields=fields,
            qualified=qualified,
            crm_entity=entity,
            summary=fields.need[:1000] if fields.need else None,
        )


class DemoBitrix24Service:
    """Logs CRM entities locally instead of calling Bitrix24."""

    def __init__(self) -> None:
        self._lead_id = 1000
        self._contact_id = 2000
        self._deal_id = 3000

    async def close(self) -> None:
        pass

    async def create_lead(
        self,
        fields: RequestFields,
        summary: str,
        telegram_user_id: int,
        username: str | None,
    ) -> int:
        self._lead_id += 1
        self._log_entity("LEAD", self._lead_id, fields, summary, telegram_user_id, username)
        return self._lead_id

    async def create_contact(
        self,
        fields: RequestFields,
        summary: str,
        telegram_user_id: int,
        username: str | None,
    ) -> int:
        self._contact_id += 1
        self._log_entity(
            "CONTACT", self._contact_id, fields, summary, telegram_user_id, username
        )
        return self._contact_id

    async def create_deal(
        self,
        fields: RequestFields,
        summary: str,
        telegram_user_id: int,
        username: str | None,
        contact_id: int,
    ) -> int:
        self._deal_id += 1
        self._log_entity(
            "DEAL",
            self._deal_id,
            fields,
            summary,
            telegram_user_id,
            username,
            contact_id=contact_id,
        )
        return self._deal_id

    @staticmethod
    def _log_entity(
        entity: str,
        entity_id: int,
        fields: RequestFields,
        summary: str,
        telegram_user_id: int,
        username: str | None,
        contact_id: int | None = None,
    ) -> None:
        payload = {
            "demo": True,
            "entity": entity,
            "id": entity_id,
            "contact_id": contact_id,
            "summary": summary,
            "customer": fields.model_dump(),
            "telegram_user_id": telegram_user_id,
            "telegram_username": username,
        }
        logger.info("DEMO CRM:\n%s", json.dumps(payload, ensure_ascii=False, indent=2))
