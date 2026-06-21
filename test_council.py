import os
import json
import unittest
import asyncio
import tempfile
import shutil

from council.council import run_council, _build_mock_council_result, compute_aggregate_rankings, parse_rankings_from_text
from council.storage import save_conversation, load_conversation, list_conversations, CONVERSATIONS_DIR


class TestCouncil(unittest.TestCase):

    def setUp(self):
        self._orig_dir = CONVERSATIONS_DIR
        self.test_conv_dir = tempfile.mkdtemp()
        import council.storage as storage_mod
        storage_mod.CONVERSATIONS_DIR = self.test_conv_dir

    def tearDown(self):
        import council.storage as storage_mod
        storage_mod.CONVERSATIONS_DIR = self._orig_dir
        shutil.rmtree(self.test_conv_dir, ignore_errors=True)

    def test_mock_council_schema(self):
        result = _build_mock_council_result("Build feature X", ["openai/gpt-4o"], "openai/gpt-4o")
        self.assertIn("stage1", result)
        self.assertIn("stage2", result)
        self.assertIn("stage3", result)
        self.assertIn("metadata", result)
        self.assertTrue(result["stage1"][0]["response"])
        self.assertTrue(result["stage3"]["response"])

    def test_run_council_mock_persists(self):
        conv = asyncio.run(run_council(
            prompt="Implement test feature",
            council_models=["openai/gpt-4o"],
            chairman_model="openai/gpt-4o",
            mock=True,
        ))
        self.assertEqual(len(conv["messages"]), 2)
        assistant = conv["messages"][1]
        self.assertEqual(assistant["role"], "assistant")
        self.assertIn("stage1", assistant)
        self.assertIn("stage2", assistant)
        self.assertIn("stage3", assistant)

        loaded = load_conversation(conv["id"])
        self.assertEqual(loaded["id"], conv["id"])

    def test_run_council_mock_with_context_pack(self):
        context_pack = {
            "version": 1,
            "targetKey": "test",
            "targetPath": "/tmp/test",
            "generatedAt": "2026-01-01T00:00:00Z",
            "policy": {"maxTotalChars": 50000},
            "summary": {"status": "ready", "totalChars": 1000, "truncated": False, "includedFiles": 3, "excludedFiles": 0},
            "blueprint": {"tree": "test/\n└── src", "readme": "# Test", "dependencies": []},
            "files": [],
            "accessIssues": [],
        }
        conv = asyncio.run(run_council(
            prompt="Implement test feature",
            context_pack=context_pack,
            council_models=["openai/gpt-4o"],
            chairman_model="openai/gpt-4o",
            mock=True,
        ))
        self.assertIn("context_pack", conv)
        self.assertEqual(conv["context_pack"]["targetKey"], "test")
        assistant = conv["messages"][1]
        self.assertEqual(assistant["metadata"]["contextPackVersion"], 1)
        self.assertEqual(assistant["metadata"]["contextStatus"], "ready")

    def test_run_council_mock_without_context_pack_marks_unavailable(self):
        conv = asyncio.run(run_council(
            prompt="Implement test feature",
            council_models=["openai/gpt-4o"],
            chairman_model="openai/gpt-4o",
            mock=True,
        ))
        assistant = conv["messages"][1]
        self.assertIsNone(assistant["metadata"]["contextPackVersion"])
        self.assertEqual(assistant["metadata"]["contextStatus"], "unavailable")

    def test_list_conversations(self):
        conv = {"id": "test-123", "created_at": "2026-01-01T00:00:00Z", "title": "Test", "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "stage3": {"model": "m", "response": "ok"}},
        ]}
        save_conversation(conv)
        listed = list_conversations()
        self.assertTrue(any(c["id"] == "test-123" for c in listed))

    def test_ranking_letter_mapping_non_identity(self):
        """Response B ranked first should map to model index 1, not index 0."""
        ranking = "1. Response B\n2. Response A\n3. Response C"
        mapped = parse_rankings_from_text(ranking, 3)
        self.assertEqual(mapped[1], 1)
        self.assertEqual(mapped[0], 2)
        self.assertEqual(mapped[2], 3)

        stage1 = [
            {"model": "model-a", "response": "A"},
            {"model": "model-b", "response": "B"},
            {"model": "model-c", "response": "C"},
        ]
        stage2 = [{"model": "rater", "ranking": ranking}]
        agg = compute_aggregate_rankings(stage1, stage2)
        by_model = {r["model"]: r["average_rank"] for r in agg}
        self.assertLess(by_model["model-b"], by_model["model-a"])
        self.assertLess(by_model["model-a"], by_model["model-c"])


if __name__ == "__main__":
    unittest.main()