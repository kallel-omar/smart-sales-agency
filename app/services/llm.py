import json
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.config import Settings


class LLMError(RuntimeError):
    pass


class LLMClient(ABC):
    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

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
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected response format from LLM provider") from exc


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_mode == "openai_compatible":
        return OpenAICompatibleLLM(settings)
    return DemoLLM()
