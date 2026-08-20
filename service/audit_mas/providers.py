"""LLM providers.

Each returns a `Completion` carrying text plus token usage, so the run manifest's
cost block is populated from what actually happened rather than left empty.

Each satisfies the one-method ``LLMProvider`` protocol. Imports are lazy and
local so the package installs and tests without any vendor SDK present.
"""

from __future__ import annotations

import os

from .agents.base import Completion


class AnthropicProvider:
    """Anthropic Messages API."""

    def __init__(self, api_key: str | None = None):
        import anthropic  # imported lazily so the SDK is an optional dependency

        self.client = anthropic.AsyncAnthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    async def complete(self, *, system: str, prompt: str, model: str, max_tokens: int) -> Completion:
        resp = await self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = getattr(resp, "usage", None)
        return Completion(
            text="".join(b.text for b in resp.content if getattr(b, "type", "") == "text"),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cached_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )


class VertexProvider:
    """Vertex AI / Gemini, matching the codelab's GOOGLE_GENAI_USE_VERTEXAI wiring."""

    def __init__(self, project: str | None = None, location: str | None = None):
        from google import genai

        self.client = genai.Client(
            vertexai=True,
            project=project or os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=location or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )

    async def complete(self, *, system: str, prompt: str, model: str, max_tokens: int) -> Completion:
        from google.genai import types

        resp = await self.client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        usage = getattr(resp, "usage_metadata", None)
        return Completion(
            text=resp.text or "",
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            cached_input_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
        )


class OpenAICompatibleProvider:
    """Any OpenAI-compatible endpoint — vLLM, Ollama, a gateway.

    Useful for the distilled-small-model approach: a 0.6B classifier for triage
    lanes and a larger model only for deep lanes and verification.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        import openai

        self.client = openai.AsyncOpenAI(
            base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
            api_key=api_key or os.environ.get("OPENAI_API_KEY", "not-needed"),
        )

    async def complete(self, *, system: str, prompt: str, model: str, max_tokens: int) -> Completion:
        resp = await self.client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        usage = getattr(resp, "usage", None)
        return Completion(
            text=resp.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


def from_env():
    """Pick a provider from the environment; stub out when nothing is configured."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider()
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") == "true":
        return VertexProvider()
    if os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_KEY"):
        return OpenAICompatibleProvider()
    from .agents.base import StubProvider

    return StubProvider(default="")
