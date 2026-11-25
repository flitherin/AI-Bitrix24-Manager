from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.models import AssistantTurn, RequestFields

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
Ты — вежливый AI-менеджер входящих заявок. Общайся на языке клиента, по умолчанию
на русском. Твоя задача — естественно выяснить потребность и подготовить заявку для
менеджера, а не выдумывать факты и не обещать невозможное.

Обязательные данные для готовой заявки:
1. customer_name — как обращаться к клиенту;
2. need — конкретная потребность, товар или услуга;
3. phone или email — хотя бы один контакт;
4. consent=true — явное согласие клиента передать заявку и контакты менеджеру.

company, budget и timeline полезны, но необязательны. Задавай не больше двух коротких
вопросов за раз и не спрашивай уже известные данные. Не выдумывай контакты, согласие,
бюджет и другие поля. Возвращай в fields только данные, явно полученные из переписки.
Если контакт выглядит ошибочно, попроси уточнить его.

qualified=true ставь только когда все обязательные данные получены и валидны.
Для crm_entity выбери:
- deal — конкретное намерение купить/заказать, обсуждаются параметры, сроки или бюджет;
- lead — первичный интерес, консультация или намерение пока неопределённо.
Если не уверен, выбери lead. summary — краткое резюме заявки без выдуманных сведений.

Ещё не сообщай, что лид или сделка уже создана: это сделает внешняя система после
твоего ответа. message должен содержать только текст для клиента.

Верни строго один JSON-объект без markdown со схемой:
{
  "message": "текст клиенту",
  "fields": {
    "customer_name": null,
    "need": null,
    "phone": null,
    "email": null,
    "company": null,
    "budget": null,
    "timeline": null,
    "consent": null
  },
  "qualified": false,
  "crm_entity": null,
  "summary": null
}
""".strip()


class AIServiceError(RuntimeError):
    pass


class AIService:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 30,
    ) -> None:
        client_kwargs: dict[str, object] = {
            "api_key": api_key,
            "timeout": timeout,
            "max_retries": 2,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**client_kwargs)
        self._model = model

    async def close(self) -> None:
        await self._client.close()

    async def generate_turn(
        self,
        history: list[dict[str, str]],
        current_fields: RequestFields,
    ) -> AssistantTurn:
        if not history or history[-1]["role"] != "user":
            raise ValueError("History must end with a user message")

        state = current_fields.model_dump_json()
        last_message = history[-1]["content"]
        contextual_message = (
            "Текущее состояние анкеты (это не новое сообщение клиента): "
            f"{state}\n\nНовое сообщение клиента:\n{last_message}"
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            *history[:-1],
            {"role": "user", "content": contextual_message},
        ]

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("Empty AI response")
            payload = json.loads(content)
            return AssistantTurn.model_validate(payload)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Invalid AI response: %s", exc)
            raise AIServiceError("AI returned an invalid response") from exc
        except Exception as exc:
            logger.exception("AI provider request failed")
            raise AIServiceError("AI provider request failed") from exc
