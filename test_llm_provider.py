"""Tests for unified LLM provider routing."""

import unittest

from council.llm_provider import model_vendor


class TestLLMProvider(unittest.TestCase):

    def test_model_vendor_slash_format(self):
        self.assertEqual(model_vendor("openai/gpt-4o"), "openai")
        self.assertEqual(model_vendor("anthropic/claude-3-5-sonnet"), "anthropic")
        self.assertEqual(model_vendor("kimi/moonshot-v1-8k"), "kimi")
        self.assertEqual(model_vendor("qwen/qwen-turbo"), "qwen")

    def test_model_vendor_bare_name(self):
        self.assertEqual(model_vendor("gpt-4o"), "openai")
        self.assertEqual(model_vendor("claude-3-haiku"), "anthropic")
        self.assertEqual(model_vendor("moonshot-v1-8k"), "kimi")


if __name__ == "__main__":
    unittest.main()