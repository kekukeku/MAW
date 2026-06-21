"""Default council model configuration."""

import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_COUNCIL_MODELS = [
    m.strip()
    for m in os.getenv(
        "DEFAULT_COUNCIL_MODELS",
        "openai/gpt-5.5,anthropic/claude-3-5-sonnet,google/gemini-3.5-flash,deepseek/deepseek-chat,kimi/kimi-k2.7-code",
    ).split(",")
    if m.strip()
]

DEFAULT_CHAIRMAN_MODEL = os.getenv("DEFAULT_CHAIRMAN_MODEL", "openai/gpt-5.5")

AVAILABLE_MODELS = [
    "openai/gpt-5.5",
    "openai/gpt-5.5-fast",
    "openai/gpt-5.5-pro",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/o1-mini",
    "anthropic/claude-3-5-sonnet",
    "anthropic/claude-3-haiku",
    "google/gemini-3.5-flash",
    "google/gemini-2.5-pro",
    "google/gemini-2.0-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-chat",
    "deepseek/deepseek-reasoner",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
    "kimi/kimi-k2.5",
    "kimi/kimi-k2.6",
    "kimi/kimi-k2.7-code",
    "kimi/kimi-k2.7-code-highspeed",
    "kimi/moonshot-v1-32k",
    "kimi/moonshot-v1-8k",
    "grok/grok-4.20-multi-agent",
    "grok/grok-4.3",
    "grok/grok-build-0.1",
    "grok/grok-latest",
]

MOCK_MODE = os.getenv("MAW_MOCK_MODE", "").lower() in ("1", "true", "yes")