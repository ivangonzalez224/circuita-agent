"""The model side: a small interface, and an implementation for Meta's Model API.

Meta's Model API speaks the OpenAI Chat Completions protocol, so the official `openai` SDK
works with a different base URL. Only `ChatCompletionsModel` knows about the network, which
keeps the agent loop testable without calling any model.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import OpenAI

from circuita_agent.config import Settings


class ModelError(Exception):
    """The model provider failed in a way the agent cannot recover from."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, exactly as the model produced it


@dataclass(frozen=True)
class ModelTurn:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Model(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn: ...


# USD per million tokens (standard mode), from the Model API pricing page on 2026-10-27.
PRICES: dict[str, tuple[float, float]] = {
    "muse-spark-1.3": (1.25, 4.25),
    "muse-spark-1.2": (1.25, 4.25),
    "muse-wonder-1.0": (4.0, 20.0),
}


def estimate_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimated USD cost, or None for a model whose price is not listed here."""
    price = PRICES.get(model_name)
    if price is None:
        return None
    return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000


class ChatCompletionsModel:
    def __init__(self, settings: Settings, client: OpenAI | None = None) -> None:
        if client is None:
            if not settings.model_api_key:
                raise ModelError("MODEL_API_KEY is not set (see .env.example)")
            client = OpenAI(
                api_key=settings.model_api_key, base_url=settings.model_base_url, max_retries=4
            )
        self._client = client
        self._settings = settings

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        try:
            response = self._client.chat.completions.create(
                model=self._settings.model_name,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                extra_body={"reasoning_effort": self._settings.reasoning_effort},
            )
        except openai.APIError as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc
        message = response.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments)
            for c in (message.tool_calls or [])
            if c.type == "function"
        ]
        usage = response.usage
        return ModelTurn(
            content=message.content,
            tool_calls=calls,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )
