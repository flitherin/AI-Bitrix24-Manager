from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.ai import AIServiceError
from app.database import Database
from app.manager import RequestManager

logger = logging.getLogger(__name__)

router = Router(name="request-manager")
router.message.filter(F.chat.type == "private")
_chat_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

_GREETING = (
    "Здравствуйте! Я AI-менеджер заявок. Опишите, какой товар или услуга вам нужна. "
    "Я уточню детали, попрошу имя и телефон или email, а с вашего согласия передам "
    "заявку менеджеру в Битрикс24.\n\n"
    "Команды: /new — новая заявка, /status — статус, /retry — повторить отправку."
)

_HELP = (
    "Просто опишите вашу потребность обычным сообщением. Для создания заявки нужны "
    "имя, описание задачи, телефон или email и согласие на передачу данных менеджеру.\n\n"
    "/new — начать новую заявку\n"
    "/status — показать статус текущей заявки\n"
    "/retry — повторить отправку в Битрикс24 после ошибки"
)


def build_dispatcher(manager: RequestManager, database: Database) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher["manager"] = manager
    dispatcher["database"] = database
    return dispatcher


@router.message(CommandStart())
async def start(message: Message, database: Database) -> None:
    if message.from_user is None:
        return
    async with _chat_locks[message.chat.id]:
        conversation = await database.get_or_create_conversation(
            message.chat.id,
            message.from_user.id,
            message.from_user.username,
        )
        if conversation.is_created:
            entity = "лид" if conversation.crm_entity == "lead" else "сделка"
            reply = (
                f"Ваша заявка уже передана: {entity} №{conversation.crm_id}. "
                "Для новой заявки отправьте /new."
            )
        else:
            reply = _GREETING
        await database.record_message(conversation.chat_id, "assistant", reply)
    await message.answer(reply)


@router.message(Command("new"))
async def new_request(message: Message, database: Database) -> None:
    if message.from_user is None:
        return
    async with _chat_locks[message.chat.id]:
        conversation = await database.reset_conversation(
            message.chat.id,
            message.from_user.id,
            message.from_user.username,
        )
        await database.record_message(conversation.chat_id, "assistant", _GREETING)
    await message.answer(_GREETING)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(_HELP)


@router.message(Command("status"))
async def status(
    message: Message,
    manager: RequestManager,
    database: Database,
) -> None:
    if message.from_user is None:
        return
    async with _chat_locks[message.chat.id]:
        conversation = await database.get_or_create_conversation(
            message.chat.id,
            message.from_user.id,
            message.from_user.username,
        )
        reply = manager.status(conversation)
    await message.answer(reply)


@router.message(Command("retry"))
async def retry(
    message: Message,
    manager: RequestManager,
    database: Database,
) -> None:
    if message.from_user is None:
        return
    async with _chat_locks[message.chat.id]:
        conversation = await database.get_or_create_conversation(
            message.chat.id,
            message.from_user.id,
            message.from_user.username,
        )
        reply = await manager.retry(conversation)
    await message.answer(reply)


@router.message(F.text, ~F.text.startswith("/"))
async def customer_message(
    message: Message,
    manager: RequestManager,
    database: Database,
) -> None:
    if message.from_user is None:
        return
    async with _chat_locks[message.chat.id]:
        conversation = await database.get_or_create_conversation(
            message.chat.id,
            message.from_user.id,
            message.from_user.username,
        )
        try:
            reply = await manager.handle_message(conversation, message.text or "")
        except AIServiceError:
            logger.exception("AI request failed for chat %s", message.chat.id)
            reply = (
                "Не удалось обработать сообщение: сервис ИИ временно недоступен. "
                "Пожалуйста, отправьте его ещё раз чуть позже."
            )
        except Exception:
            logger.exception("Unexpected message handling error for chat %s", message.chat.id)
            reply = "Произошла техническая ошибка. Попробуйте ещё раз или отправьте /new."
    await message.answer(reply)


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer("Неизвестная команда. Используйте /help.")
