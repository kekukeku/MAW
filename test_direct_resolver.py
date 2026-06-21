"""Tests for Direct API vendor endpoint resolver."""

import json
import os
import tempfile
import shutil
import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import council.direct_resolver as resolver_mod
from council.direct_resolver import (
    resolve_vendor,
    clear_vendor_cache,
    DirectResolverError,
    _key_hash,
)


class TestDirectResolver(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.routes_path = os.path.join(self.tmp, "vendor_routes.json")
        resolver_mod.ROUTES_PATH = self.routes_path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_key_hash_stable(self):
        self.assertEqual(_key_hash("sk-test"), _key_hash("sk-test"))
        self.assertNotEqual(_key_hash("sk-a"), _key_hash("sk-b"))

    def test_resolve_uses_cache(self):
        cache = {
            "routes": {
                "deepseek": {
                    "endpoint_id": "deepseek_main",
                    "base_url": "https://api.deepseek.com/v1",
                    "region": "default",
                    "api_style": "openai_compatible",
                    "resolved_at": "2099-01-01T00:00:00+00:00",
                    "key_hash": _key_hash("sk-deep"),
                    "label": "DeepSeek 主節點",
                }
            }
        }
        with open(self.routes_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)

        async def _run():
            result = await resolve_vendor("deepseek", "sk-deep")
            self.assertTrue(result["ok"])
            self.assertTrue(result["cached"])
            self.assertEqual(result["endpoint_id"], "deepseek_main")

        asyncio.run(_run())

    def test_resolve_probes_endpoints(self):
        async def _run():
            ok_resp = MagicMock(status_code=200, text="ok")
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=ok_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with patch("council.direct_resolver.httpx.AsyncClient", return_value=mock_client):
                result = await resolve_vendor("deepseek", "sk-deep", force_probe=True)
            self.assertTrue(result["ok"])
            self.assertFalse(result["cached"])
            self.assertEqual(result["endpoint_id"], "deepseek_main")

        asyncio.run(_run())

    def test_resolve_all_fail(self):
        async def _run():
            fail_resp = MagicMock(status_code=401, text="unauthorized")
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=fail_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with patch("council.direct_resolver.httpx.AsyncClient", return_value=mock_client):
                with self.assertRaises(DirectResolverError):
                    await resolve_vendor("deepseek", "bad-key", force_probe=True)

        asyncio.run(_run())

    def test_clear_vendor_cache(self):
        with open(self.routes_path, "w", encoding="utf-8") as f:
            json.dump({"routes": {"kimi": {"endpoint_id": "x"}}}, f)
        clear_vendor_cache("kimi")
        with open(self.routes_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("kimi", data.get("routes", {}))


if __name__ == "__main__":
    unittest.main()