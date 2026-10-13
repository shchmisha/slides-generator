"""Test doubles shared by several test modules."""

from __future__ import annotations

from collections.abc import Iterator

from app.llm.client import Completion, Message


class ScriptedModel:
    """Returns the queued replies in order and records every request."""

    def __init__(self, *replies: str, truncated: bool = False) -> None:
        self.replies: Iterator[str] = iter(replies)
        self.requests: list[list[Message]] = []
        self.truncated = truncated

    def complete(self, messages: list[Message], *, max_tokens: int | None = None) -> Completion:
        self.requests.append(list(messages))
        return Completion(
            text=next(self.replies), model="fake", latency_ms=10, truncated=self.truncated
        )
