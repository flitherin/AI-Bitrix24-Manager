from __future__ import annotations

import logging
from typing import Literal

from app.ai import AIService
from app.bitrix24 import Bitrix24Error, Bitrix24Service
from app.database import Database
from app.models import Conversation, CrmEntity, RequestFields

logger = logging.getLogger(__name__)

EntityMode = Literal["auto", "lead", "deal"]


class RequestManager:
    def __init__(
        self,
        database: Database,
        ai: AIService,
        bitrix24: Bitrix24Service,
        history_limit: int = 12,
        entity_mode: EntityMode = "auto",
    ) -> None:
        self._db = database
        self._ai = ai
        self._bitrix24 = bitrix24
        self._history_limit = history_limit
        self._entity_mode = entity_mode

    async def handle_message(self, conversation: Conversation, text: str) -> str:
        if conversation.is_created:
            return self._existing_request_message(conversation)
        if conversation.qualified:
            return await self._create_crm_request(conversation)

        history = await self._db.get_history(conversation.chat_id, self._history_limit)
        history.append({"role": "user", "content": text})
        turn = await self._ai.generate_turn(history, conversation.fields)
        fields = conversation.fields.merged(turn.fields)

        complete = fields.is_complete
        qualified = turn.qualified and complete
        summary = turn.summary or conversation.summary or self._build_summary(fields)

        if turn.qualified and not complete:
            missing = ";\n• ".join(fields.missing_requirements())
            reply = f"Чтобы передать заявку, уточните, пожалуйста:\n• {missing}"
        else:
            reply = turn.message

        if not qualified:
            await self._db.save_turn(
                conversation.chat_id,
                user_text=text,
                assistant_text=reply,
                fields=fields,
                summary=summary,
                qualified=False,
            )
            return reply

        entity = self._select_entity(turn.crm_entity)
        await self._db.save_turn(
            conversation.chat_id,
            user_text=text,
            assistant_text=None,
            fields=fields,
            summary=summary,
            qualified=True,
            crm_entity=entity,
        )
        updated = await self._get_conversation(conversation.chat_id)
        return await self._create_crm_request(updated)

    async def retry(self, conversation: Conversation) -> str:
        if conversation.is_created:
            return self._existing_request_message(conversation)
        if not conversation.qualified:
            return "Заявка ещё не заполнена. Продолжите диалог и ответьте на вопросы бота."
        return await self._create_crm_request(conversation)

    def status(self, conversation: Conversation) -> str:
        if conversation.is_created:
            entity = "лид" if conversation.crm_entity == "lead" else "сделка"
            return f"Заявка передана: {entity} №{conversation.crm_id} в Битрикс24."
        if conversation.qualified:
            return "Данные собраны, но заявка ещё не передана. Используйте /retry."
        missing = conversation.fields.missing_requirements()
        if not missing:
            return "Данные собраны. Ожидается подтверждение квалификации."
        return "Для заявки осталось указать:\n• " + ";\n• ".join(missing)

    async def _create_crm_request(self, conversation: Conversation) -> str:
        if conversation.is_created:
            return self._existing_request_message(conversation)

        fields = conversation.fields
        summary = conversation.summary or self._build_summary(fields)
        entity: CrmEntity = conversation.crm_entity or "lead"

        try:
            if entity == "lead":
                crm_id = await self._bitrix24.create_lead(
                    fields,
                    summary,
                    conversation.telegram_user_id,
                    conversation.username,
                )
            else:
                contact_id = conversation.crm_contact_id
                if contact_id is None:
                    contact_id = await self._bitrix24.create_contact(
                        fields,
                        summary,
                        conversation.telegram_user_id,
                        conversation.username,
                    )
                    await self._db.set_crm_contact(conversation.chat_id, contact_id)
                crm_id = await self._bitrix24.create_deal(
                    fields,
                    summary,
                    conversation.telegram_user_id,
                    conversation.username,
                    contact_id,
                )

            await self._db.set_crm_created(conversation.chat_id, entity, crm_id)
        except Bitrix24Error:
            logger.exception("Could not create CRM request for chat %s", conversation.chat_id)
            reply = (
                "Данные заявки сохранены, но Битрикс24 временно недоступен. "
                "Попробуйте повторить отправку командой /retry. Повторно заполнять анкету не нужно."
            )
            await self._db.record_message(conversation.chat_id, "assistant", reply)
            return reply

        entity_name = "лид" if entity == "lead" else "сделка"
        reply = (
            f"Спасибо! Заявка передана менеджеру. В Битрикс24 создан {entity_name} "
            f"№{crm_id}. Мы свяжемся с вами по указанному контакту."
        )
        await self._db.record_message(conversation.chat_id, "assistant", reply)
        return reply

    def _select_entity(self, suggested: CrmEntity | None) -> CrmEntity:
        if self._entity_mode != "auto":
            return self._entity_mode
        return suggested or "lead"

    async def _get_conversation(self, chat_id: int) -> Conversation:
        conversation = await self._db.get_conversation(chat_id)
        if conversation is None:
            raise RuntimeError("Conversation disappeared")
        return conversation

    @staticmethod
    def _build_summary(fields: RequestFields) -> str:
        details = [fields.need or "Потребность не указана"]
        if fields.company:
            details.append(f"Компания: {fields.company}")
        if fields.budget:
            details.append(f"Бюджет: {fields.budget}")
        if fields.timeline:
            details.append(f"Сроки: {fields.timeline}")
        return "; ".join(details)

    @staticmethod
    def _existing_request_message(conversation: Conversation) -> str:
        entity = "лид" if conversation.crm_entity == "lead" else "сделка"
        return (
            f"Эта заявка уже передана: {entity} №{conversation.crm_id} в Битрикс24. "
            "Для новой заявки используйте /new."
        )
