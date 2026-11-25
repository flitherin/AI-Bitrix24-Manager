from __future__ import annotations

import logging
from typing import Any

import httpx

from app.models import RequestFields

logger = logging.getLogger(__name__)


class Bitrix24Error(RuntimeError):
    pass


class Bitrix24Service:
    def __init__(
        self,
        webhook_url: str,
        timeout: float = 30,
        deal_stage: str = "NEW",
        deal_category_id: int | None = None,
        source_id: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._webhook_url = webhook_url.rstrip("/") + "/"
        self._deal_stage = deal_stage
        self._deal_category_id = deal_category_id
        self._source_id = source_id
        self._client = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = http_client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_lead(
        self,
        fields: RequestFields,
        summary: str,
        telegram_user_id: int,
        username: str | None,
    ) -> int:
        payload: dict[str, Any] = {
            "TITLE": self._title(fields, summary),
            "NAME": fields.customer_name,
            "COMMENTS": self._comments(fields, summary, telegram_user_id, username),
        }
        self._add_contact_fields(payload, fields)
        self._add_source(payload)
        return await self._call("crm.lead.add", payload)

    async def create_contact(
        self,
        fields: RequestFields,
        summary: str,
        telegram_user_id: int,
        username: str | None,
    ) -> int:
        payload: dict[str, Any] = {
            "NAME": fields.customer_name,
            "COMMENTS": self._comments(fields, summary, telegram_user_id, username),
        }
        self._add_contact_fields(payload, fields)
        self._add_source(payload)
        return await self._call("crm.contact.add", payload)

    async def create_deal(
        self,
        fields: RequestFields,
        summary: str,
        telegram_user_id: int,
        username: str | None,
        contact_id: int,
    ) -> int:
        payload: dict[str, Any] = {
            "TITLE": self._title(fields, summary),
            "STAGE_ID": self._deal_stage,
            "CONTACT_ID": contact_id,
            "COMMENTS": self._comments(fields, summary, telegram_user_id, username),
        }
        if self._deal_category_id is not None:
            payload["CATEGORY_ID"] = self._deal_category_id
        self._add_source(payload)
        return await self._call("crm.deal.add", payload)

    async def _call(self, method: str, fields: dict[str, Any]) -> int:
        try:
            response = await self._client.post(
                f"{self._webhook_url}{method}.json",
                json={"fields": fields},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("Bitrix24 %s returned HTTP %s", method, exc.response.status_code)
            raise Bitrix24Error(f"Bitrix24 HTTP {exc.response.status_code}") from None
        except httpx.HTTPError:
            logger.exception("Bitrix24 %s network error", method)
            raise Bitrix24Error("Bitrix24 network error") from None

        try:
            data = response.json()
        except ValueError:
            logger.error("Bitrix24 %s returned non-JSON response", method)
            raise Bitrix24Error("Bitrix24 invalid response") from None

        if data.get("error"):
            description = str(data.get("error_description") or data["error"])[:300]
            logger.error("Bitrix24 %s API error: %s", method, description)
            raise Bitrix24Error(f"Bitrix24 API error: {description}")

        try:
            return int(data["result"])
        except (KeyError, TypeError, ValueError):
            logger.error("Bitrix24 %s returned no entity ID", method)
            raise Bitrix24Error("Bitrix24 returned no entity ID") from None

    @staticmethod
    def _add_contact_fields(payload: dict[str, Any], fields: RequestFields) -> None:
        if fields.phone:
            payload["PHONE"] = [{"VALUE": fields.phone, "VALUE_TYPE": "WORK"}]
        if fields.email:
            payload["EMAIL"] = [{"VALUE": fields.email, "VALUE_TYPE": "WORK"}]

    def _add_source(self, payload: dict[str, Any]) -> None:
        if self._source_id:
            payload["SOURCE_ID"] = self._source_id
        payload["SOURCE_DESCRIPTION"] = "Telegram AI-менеджер"

    @staticmethod
    def _title(fields: RequestFields, summary: str) -> str:
        text = summary or fields.need or "Заявка из Telegram"
        first_line = next((line for line in text.splitlines() if line.strip()), text)
        return f"Telegram: {first_line.strip()}"[:180]

    @staticmethod
    def _comments(
        fields: RequestFields,
        summary: str,
        telegram_user_id: int,
        username: str | None,
    ) -> str:
        lines = [
            "Заявка подготовлена AI-менеджером Telegram.",
            f"Резюме: {summary}",
            f"Потребность: {fields.need or '—'}",
            f"Компания: {fields.company or '—'}",
            f"Бюджет: {fields.budget or '—'}",
            f"Сроки: {fields.timeline or '—'}",
            f"Телефон: {fields.phone or '—'}",
            f"Email: {fields.email or '—'}",
            f"Telegram user ID: {telegram_user_id}",
            f"Telegram username: @{username}" if username else "Telegram username: —",
            "Клиент согласился на передачу заявки менеджеру.",
        ]
        return "\n".join(lines)
