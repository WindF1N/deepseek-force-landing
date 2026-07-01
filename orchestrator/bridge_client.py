"""Async client to the DeepSeek Bridge (port 8001).

The Bridge exposes an OpenAI-compatible /v1/chat/completions that routes the
request through a free Chrome-extension session pool to chat.deepseek.com.

This client adds:
  - health check
  - a bounded-concurrency gate (so we never ask the Bridge for more parallel
    sessions than there are Chrome windows connected)
  - fan-out helper for self-consistency voting
  - persistent chat context management (NEW)
"""
import asyncio
import logging
import sys
from typing import List, Optional

import httpx

from config import settings

logger = logging.getLogger("orchestrator.bridge")


class BridgeClient:
    def __init__(self):
        self._sem = asyncio.Semaphore(settings.MAX_PARALLEL_SESSIONS)
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=settings.BRIDGE_TIMEOUT)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> dict:
        try:
            c = await self._http()
            r = await c.get(settings.BRIDGE_HEALTH, timeout=5)
            return r.json()
        except Exception as e:
            return {"status": "error", "error": str(e), "extension_connected": False}

    async def complete(self, prompt: str, *, model: str = "deepseek-chat",
                       search_enabled: Optional[bool] = None,
                       images: Optional[List[dict]] = None) -> str:
        """Single completion through the Bridge. Returns raw assistant text.

        We pack the whole context into one user message because the Bridge
        flattens messages anyway and creates a fresh DeepSeek chat session
        per request. A system role is still sent so the Bridge's own
        formatting keeps it labelled.
        """
        # model name carries the mode hint for the Bridge (chat/expert/vision)
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if images:
            body["images"] = images
        last_err = None
        async with self._sem:
            c = await self._http()
            for attempt in range(3):
                try:
                    r = await c.post(settings.BRIDGE_COMPLETIONS, json=body)
                    if r.status_code == 200:
                        data = r.json()
                        return data["choices"][0]["message"]["content"]
                    # 502/503/500: transient on the DeepSeek side, back off & retry
                    last_err = f"Bridge {r.status_code}: {r.text[:200]}"
                    if r.status_code == 503:
                        # no extension at all — retrying won't help fast
                        raise RuntimeError(last_err)
                except httpx.RequestError as e:
                    last_err = f"Bridge request error: {e}"
                await asyncio.sleep(0.6 * (attempt + 1))
            raise RuntimeError(last_err or "Bridge failed")

    async def fan_out(self, prompt: str, n: int, *, model: str = "deepseek-chat",
                      search_enabled: Optional[bool] = None,
                      images: Optional[List[dict]] = None) -> List[str]:
        """Fire n parallel completions (for self-consistency voting).

        Failures are returned as empty strings so the caller can vote over
        whatever succeeded instead of crashing the whole turn.
        """
        if n <= 1:
            return [await self.complete(prompt, model=model, search_enabled=search_enabled, images=images)]

        async def one():
            try:
                return await self.complete(prompt, model=model, search_enabled=search_enabled, images=images)
            except Exception as e:
                logger.warning("fan_out branch failed: %s", e)
                return ""

        return await asyncio.gather(*[one() for _ in range(n)])


    async def get_or_create_context(self, user_id: str):
        """Get or create persistent chat context for a user via HTTP API."""
        c = await self._http()
        try:
            r = await c.get(f"{settings.BRIDGE_URL}/api/contexts/{user_id}")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Failed to get context for {user_id}: {e}")
            raise


bridge = BridgeClient()
