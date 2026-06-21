"""Unified LLM provider: LiteLLM, OpenRouter, or Direct API."""

import os
import logging
from typing import Any

import httpx
from dotenv import load_dotenv

from council.openrouter import query_model as openrouter_query
from council.openrouter import OpenRouterError
from council.direct_resolver import (
    load_vendors,
    resolve_vendor,
    get_resolved_route,
    clear_vendor_cache,
    DirectResolverError,
)

load_dotenv()
logger = logging.getLogger(__name__)


class LLMProviderError(Exception):
    def __init__(self, message: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def get_llm_provider() -> str:
    return os.getenv("LLM_PROVIDER", "litellm").strip().lower()


def model_vendor(model_id: str) -> str | None:
    if "/" in model_id:
        prefix = model_id.split("/", 1)[0].lower()
        aliases = {
            "openai": "openai",
            "anthropic": "anthropic",
            "google": "google",
            "gemini": "google",
            "deepseek": "deepseek",
            "kimi": "kimi",
            "moonshot": "kimi",
            "qwen": "qwen",
            "grok": "grok",
            "xai": "grok",
        }
        return aliases.get(prefix, prefix if prefix in load_vendors() else None)

    lower = model_id.lower()
    if lower.startswith("gpt-") or lower.startswith("o1") or lower.startswith("o3"):
        return "openai"
    if lower.startswith("claude"):
        return "anthropic"
    if lower.startswith("gemini"):
        return "google"
    if lower.startswith("deepseek"):
        return "deepseek"
    if lower.startswith("moonshot") or lower.startswith("kimi"):
        return "kimi"
    if lower.startswith("qwen"):
        return "qwen"
    if lower.startswith("grok"):
        return "grok"
    return None


def _api_model_name(model_id: str) -> str:
    if "/" in model_id:
        return model_id.split("/", 1)[1]
    return model_id


def _vendor_api_key(vendor_id: str) -> str:
    vendors = load_vendors()
    vendor = vendors.get(vendor_id)
    if not vendor:
        raise LLMProviderError(f"Unknown vendor '{vendor_id}'")
    env_key = vendor["env_key"]
    key = os.getenv(env_key, "").strip()
    if not key:
        raise LLMProviderError(f"{env_key} is not set")
    return key


async def _ensure_direct_route(vendor_id: str, api_key: str) -> dict[str, Any]:
    cached = get_resolved_route(vendor_id, api_key)
    if cached:
        return cached
    resolved = await resolve_vendor(vendor_id, api_key)
    return {
        "base_url": resolved["base_url"],
        "api_style": resolved["api_style"],
        "endpoint_id": resolved["endpoint_id"],
    }


async def _query_direct_openai(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": temperature}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code in (401, 403):
        raise LLMProviderError(f"Direct API auth error {resp.status_code}", status_code=resp.status_code)
    if resp.status_code != 200:
        raise LLMProviderError(f"Direct API error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


async def _query_direct_anthropic(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
) -> str:
    url = f"{base_url.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": messages,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code in (401, 403):
        raise LLMProviderError(f"Anthropic auth error {resp.status_code}", status_code=resp.status_code)
    if resp.status_code != 200:
        raise LLMProviderError(f"Anthropic error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    blocks = data.get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


async def _query_litellm(
    model_id: str,
    messages: list[dict[str, str]],
    temperature: float,
) -> str:
    base = os.getenv("LITELLM_API_BASE", "http://localhost:4000").rstrip("/")
    api_key = os.getenv("LITELLM_API_KEY", "").strip() or "sk-no-key"
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    api_model = _api_model_name(model_id)
    if "kimi" in api_model.lower() or "moonshot" in api_model.lower():
        temperature = 1.0
    payload = {"model": api_model, "messages": messages, "temperature": temperature}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise LLMProviderError(f"LiteLLM error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


async def query_model(
    model_id: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
) -> str:
    provider = get_llm_provider()

    if provider == "openrouter":
        try:
            return await openrouter_query(model_id, messages, temperature)
        except OpenRouterError as exc:
            raise LLMProviderError(str(exc), status_code=exc.status_code, retryable=exc.retryable) from exc

    if provider == "litellm":
        return await _query_litellm(model_id, messages, temperature)

    if provider == "direct":
        vendor_id = model_vendor(model_id)
        if not vendor_id:
            raise LLMProviderError(f"Cannot determine vendor for model '{model_id}'")
        api_key = _vendor_api_key(vendor_id)
        try:
            route = await _ensure_direct_route(vendor_id, api_key)
        except DirectResolverError as exc:
            raise LLMProviderError(str(exc)) from exc
        api_model = _api_model_name(model_id)
        try:
            if route.get("api_style") == "anthropic_messages":
                return await _query_direct_anthropic(
                    route["base_url"], api_key, api_model, messages, temperature
                )
            return await _query_direct_openai(
                route["base_url"], api_key, api_model, messages, temperature
            )
        except LLMProviderError as exc:
            if exc.status_code in (401, 403):
                clear_vendor_cache(vendor_id)
            raise

    raise LLMProviderError(f"Unknown LLM_PROVIDER '{provider}'")


async def query_models_parallel(
    model_ids: list[str],
    messages: list[dict[str, str]],
    temperature: float = 0.7,
) -> list[dict[str, Any]]:
    import asyncio

    async def _one(model_id: str) -> dict[str, Any]:
        try:
            response = await query_model(model_id, messages, temperature)
            return {"model": model_id, "response": response, "error": None}
        except Exception as exc:
            return {"model": model_id, "response": None, "error": str(exc)}

    return await asyncio.gather(*[_one(mid) for mid in model_ids])