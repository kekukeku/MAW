"""Async OpenRouter API client with retries and rate-limit handling."""

import os
import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2.0


class OpenRouterError(Exception):
    """Structured OpenRouter API error."""

    def __init__(self, message: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _get_api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise OpenRouterError("OPENROUTER_API_KEY is not set", retryable=False)
    return key


def parse_retry_after(retry_after: str | None, attempt: int) -> float:
    """Parse Retry-After header (seconds or HTTP-date) with exponential fallback."""
    if not retry_after:
        return BASE_BACKOFF_SECONDS * (2 ** attempt)
    try:
        return float(retry_after)
    except ValueError:
        try:
            dt = parsedate_to_datetime(retry_after)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (dt - now).total_seconds())
        except Exception:
            return BASE_BACKOFF_SECONDS * (2 ** attempt)


async def query_model(
    model_id: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
) -> str:
    """Query a single model via OpenRouter with exponential backoff retries."""
    api_key = _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/maw-workflow",
        "X-Title": "MAW Council",
    }
    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
    }

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)

                if response.status_code == 429:
                    delay = parse_retry_after(response.headers.get("Retry-After"), attempt)
                    logger.warning("Rate limited for %s, waiting %.1fs", model_id, delay)
                    await asyncio.sleep(delay)
                    continue

                if response.status_code >= 500:
                    delay = BASE_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning("Server error %s for %s, retry in %.1fs", response.status_code, model_id, delay)
                    await asyncio.sleep(delay)
                    continue

                if response.status_code != 200:
                    detail = response.text[:500]
                    raise OpenRouterError(
                        f"OpenRouter error {response.status_code}: {detail}",
                        status_code=response.status_code,
                        retryable=False,
                    )

                data = response.json()
                choices = data.get("choices", [])
                if not choices:
                    raise OpenRouterError(f"No choices returned for model {model_id}", retryable=False)
                return choices[0]["message"]["content"]

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                delay = BASE_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning("Network error for %s: %s, retry in %.1fs", model_id, exc, delay)
                await asyncio.sleep(delay)

    raise OpenRouterError(
        f"Failed to query {model_id} after {MAX_RETRIES} retries: {last_error}",
        retryable=True,
    )


async def query_models_parallel(
    model_ids: list[str],
    messages: list[dict[str, str]],
    temperature: float = 0.7,
) -> list[dict[str, Any]]:
    """Query multiple models in parallel, returning structured results."""

    async def _query_one(model_id: str) -> dict[str, Any]:
        try:
            response = await query_model(model_id, messages, temperature)
            return {"model": model_id, "response": response, "error": None}
        except Exception as exc:
            return {"model": model_id, "response": None, "error": str(exc)}

    tasks = [_query_one(mid) for mid in model_ids]
    return await asyncio.gather(*tasks)