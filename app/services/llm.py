import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMCompletion:
    """Provider-neutral completion text and optional provider-reported usage.

    Token counts remain ``None`` unless a provider actually returns them.  This
    keeps the transport boundary backward compatible without inventing usage
    metadata for providers which do not expose it.
    """

    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMClient(ABC):
    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

    async def complete_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMCompletion:
        """Return a completion with usage when the concrete provider supplies it.

        Existing clients only need to implement ``complete``; this default
        preserves that contract and deliberately leaves unknown usage unset.
        """

        return LLMCompletion(content=await self.complete(system_prompt, user_prompt))

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        raw = await self.complete(system_prompt, user_prompt)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError("The model did not return valid JSON") from exc


class DemoLLM(LLMClient):
    """Offline-safe placeholder. Agents use deterministic heuristics in demo mode."""

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return (
            "Demo mode is active. Configure LLM_MODE=openai_compatible and provide an API key "
            "to generate model-based responses."
        )


class OpenAICompatibleLLM(LLMClient):
    def __init__(self, settings: Settings):
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY is required in openai_compatible mode")
        self.settings = settings

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return (await self.complete_with_metadata(system_prompt, user_prompt)).content

    async def complete_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMCompletion:
        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected response format from LLM provider") from exc

        usage = body.get("usage") if isinstance(body, dict) else None
        input_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        output_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        if not (
            isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and input_tokens >= 0
            and isinstance(output_tokens, int)
            and not isinstance(output_tokens, bool)
            and output_tokens >= 0
        ):
            input_tokens = None
            output_tokens = None
        return LLMCompletion(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def build_llm(settings: Settings, *, model: str | None = None) -> LLMClient:
    """Build the configured transport, optionally for a resolved model.

    The provider/model choice remains domain policy outside this transport
    factory.  ``AIInvocationGateway`` is the sole application-level consumer
    of this construction seam; agents, routes, and domain services use it.
    """

    if model is not None:
        settings = settings.model_copy(update={"llm_model": model})
    if settings.llm_mode == "openai_compatible":
        return OpenAICompatibleLLM(settings)
    return DemoLLM()
