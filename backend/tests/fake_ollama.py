"""A local fake of the Ollama HTTP API (127.0.0.1, random port) for provider tests."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field

from aiohttp import web


@dataclass
class FakeOllama:
    models: list[str] = field(default_factory=lambda: ["qwen3:4b"])
    chat_content: str = '{"claims": [], "missing_evidence": []}'
    chat_status: int = 200
    done_reason: str = "stop"
    delay_seconds: float = 0.0
    requests: list[dict] = field(default_factory=list)
    url: str = ""

    def start(self) -> FakeOllama:
        ready = threading.Event()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            app = web.Application()
            app.router.add_get("/api/tags", self._tags)
            app.router.add_post("/api/chat", self._chat)
            self._runner = web.AppRunner(app)
            self._loop.run_until_complete(self._runner.setup())
            site = web.TCPSite(self._runner, "127.0.0.1", 0)
            self._loop.run_until_complete(site.start())
            self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        assert ready.wait(10)
        return self

    def stop(self) -> None:
        asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(10)

    async def _tags(self, request):
        return web.json_response({"models": [{"name": m} for m in self.models]})

    async def _chat(self, request):
        body = await request.json()
        self.requests.append(body)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if body.get("model") not in self.models:
            return web.json_response({"error": f"model '{body.get('model')}' not found"}, status=404)
        if self.chat_status != 200:
            return web.json_response({"error": "internal"}, status=self.chat_status)
        return web.json_response({"model": body["model"], "done": True, "done_reason": self.done_reason,
                                  "message": {"role": "assistant", "content": self.chat_content},
                                  "eval_count": 42})

    def set_briefing(self, claims: list[dict], missing: list[str] | None = None) -> None:
        self.chat_content = json.dumps({"claims": claims, "missing_evidence": missing or []})
