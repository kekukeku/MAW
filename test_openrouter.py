import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

from council.openrouter import parse_retry_after, query_model, OpenRouterError


class TestOpenRouter(unittest.TestCase):

    def test_parse_retry_after_seconds(self):
        self.assertEqual(parse_retry_after("5", 0), 5.0)

    def test_parse_retry_after_fallback(self):
        self.assertEqual(parse_retry_after(None, 2), 8.0)

    def test_parse_retry_after_http_date(self):
        delay = parse_retry_after("Wed, 21 Oct 2030 07:28:00 GMT", 0)
        self.assertGreater(delay, 0)

    def test_query_model_retries_on_503(self):
        async def _run():
            responses = [
                MagicMock(status_code=503, headers={}, text="error"),
                MagicMock(status_code=503, headers={}, text="error"),
                MagicMock(
                    status_code=200,
                    headers={},
                    json=lambda: {"choices": [{"message": {"content": "ok"}}]},
                ),
            ]
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=responses)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
                with patch("council.openrouter.httpx.AsyncClient", return_value=mock_client):
                    with patch("council.openrouter.asyncio.sleep", new_callable=AsyncMock):
                        result = await query_model("openai/gpt-4o", [{"role": "user", "content": "hi"}])
            self.assertEqual(result, "ok")
            self.assertEqual(mock_client.post.call_count, 3)

        asyncio.run(_run())

    def test_query_model_429_with_retry_after_header(self):
        async def _run():
            responses = [
                MagicMock(status_code=429, headers={"Retry-After": "1"}, text="rate limited"),
                MagicMock(
                    status_code=200,
                    headers={},
                    json=lambda: {"choices": [{"message": {"content": "done"}}]},
                ),
            ]
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=responses)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
                with patch("council.openrouter.httpx.AsyncClient", return_value=mock_client):
                    with patch("council.openrouter.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
                        result = await query_model("openai/gpt-4o", [{"role": "user", "content": "hi"}])
            self.assertEqual(result, "done")
            sleep_mock.assert_called()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()