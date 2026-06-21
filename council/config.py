"""Default council model configuration."""

import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_COUNCIL_MODELS = [
    m.strip()
    for m in os.getenv(
        "DEFAULT_COUNCIL_MODELS",
        "openai/gpt-4o,anthropic/claude-3-5-sonnet,google/gemini-3.5-flash,deepseek/deepseek-chat,kimi/moonshot-v1-32k",
    ).split(",")
    if m.strip()
]

DEFAULT_CHAIRMAN_MODEL = os.getenv("DEFAULT_CHAIRMAN_MODEL", "openai/gpt-4o")

AVAILABLE_MODELS = [
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
    "kimi/moonshot-v1-32k",
    "kimi/moonshot-v1-8k",
]

MOCK_MODE = os.getenv("MAW_MOCK_MODE", "").lower() in ("1", "true", "yes")