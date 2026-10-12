"""Chat completions against any OpenAI-compatible endpoint (vLLM in the client's network,
OpenRouter for testing). Address, model and key come from settings, so switching needs no code.

Only plain chat completions are used: no tool calling and, unless enabled, no response_format,
because the production model's support for them is not guaranteed (see the spec).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict, cast

import openai
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    truncated: bool = False


class LLMError(RuntimeError):
    """The model could not be reached or refused; the message is safe to show to the user."""


class ChatModel(Protocol):
    def complete(self, messages: list[Message], *, max_tokens: int | None = None) -> Completion: ...


class OpenAICompatibleModel:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.llm_model:
            raise LLMError("Модель не настроена: задайте LLM_MODEL")
        self._client = OpenAI(
            base_url=self._settings.llm_base_url,
            api_key=self._settings.llm_api_key.get_secret_value() or "not-set",
            timeout=self._settings.llm_timeout_s,
            max_retries=2,  # the SDK retries timeouts, 429 and 5xx with backoff
        )

    def complete(self, messages: list[Message], *, max_tokens: int | None = None) -> Completion:
        s = self._settings
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=s.llm_model,
                messages=cast("list[ChatCompletionMessageParam]", list(messages)),
                temperature=0.3,
                max_tokens=max_tokens or s.llm_max_tokens,
                extra_body=s.llm_extra_body or None,
                response_format={"type": "json_object"} if s.llm_json_mode else openai.omit,
            )
        except openai.AuthenticationError as exc:
            raise LLMError("Модель отклонила ключ доступа: проверьте LLM_API_KEY") from exc
        except openai.NotFoundError as exc:
            raise LLMError(f"Модель «{s.llm_model}» не найдена по адресу LLM_BASE_URL") from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise LLMError("Модель не ответила вовремя: попробуйте ещё раз") from exc
        except openai.APIError as exc:
            # Status and type only: provider error bodies may echo request details.
            logger.warning("llm error: %s %s", type(exc).__name__, getattr(exc, "status_code", ""))
            raise LLMError("Ошибка обращения к модели") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice else ""
        usage = response.usage
        completion = Completion(
            text=_THINK.sub("", text).strip(),
            model=response.model or s.llm_model,
            latency_ms=latency_ms,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            truncated=choice is not None and choice.finish_reason == "length",
        )
        logger.info(
            "llm call model=%s latency_ms=%.0f prompt_tokens=%s completion_tokens=%s truncated=%s",
            completion.model,
            latency_ms,
            completion.prompt_tokens,
            completion.completion_tokens,
            completion.truncated,
        )
        return completion
