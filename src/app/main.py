from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.ai import AIService
from app.bitrix24 import Bitrix24Service
from app.bot import build_dispatcher
from app.config import get_settings
from app.database import Database
from app.demo import DemoAIService, DemoBitrix24Service
from app.manager import RequestManager


async def run_bot() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    database = Database(settings.database_path)
    if settings.demo_mode:
        logging.getLogger(__name__).warning(
            "DEMO_MODE enabled: AI and Bitrix24 are simulated locally"
        )
        ai = DemoAIService()
        bitrix24 = DemoBitrix24Service()
    else:
        if settings.openai_api_key is None or settings.bitrix24_webhook_url is None:
            raise RuntimeError("Production credentials are not configured")
        ai = AIService(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            timeout=settings.request_timeout_seconds,
        )
        bitrix24 = Bitrix24Service(
            webhook_url=settings.bitrix24_webhook_url.get_secret_value(),
            timeout=settings.request_timeout_seconds,
            deal_stage=settings.bitrix24_deal_stage,
            deal_category_id=settings.bitrix24_deal_category_id,
            source_id=settings.bitrix24_source_id,
        )
    manager = RequestManager(
        database=database,
        ai=ai,
        bitrix24=bitrix24,
        history_limit=settings.dialogue_history_limit,
        entity_mode=settings.bitrix24_entity_mode,
    )
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())

    await database.connect()
    try:
        dispatcher = build_dispatcher(manager, database)
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        await bitrix24.close()
        await ai.close()
        await database.close()


def run() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
