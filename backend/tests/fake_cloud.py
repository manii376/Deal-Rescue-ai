"""A local fake of the Hindsight Cloud HTTP API (a real HTTP server on 127.0.0.1).

It implements only the endpoints the app uses, with the documented paths
(/v1/default/banks/...) and response shapes of hindsight-client 0.10.1, records every
request (method, path, Authorization header) and can be told to fail or stall.
It never talks to the internet.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from dataclasses import dataclass, field

from aiohttp import web


@dataclass
class Recorded:
    method: str
    path: str
    authorization: str | None


@dataclass
class FakeCloud:
    requests: list[Recorded] = field(default_factory=list)
    banks: dict[str, dict[str, dict]] = field(default_factory=dict)  # bank -> document_id -> record
    fail_status: int | None = None          # respond with this status to every /v1 request
    fail_count: int | None = None           # ...only for the next N requests (None = always)
    delay_seconds: float = 0.0              # stall /v1 requests (timeouts)
    expected_key: str | None = None         # if set, anything else gets 401
    url: str = ""
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    # -- lifecycle --------------------------------------------------------------------

    def start(self) -> FakeCloud:
        ready = threading.Event()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            app = web.Application(middlewares=[self._middleware])
            app.router.add_route("*", "/{tail:.*}", self._handle)
            self._runner = web.AppRunner(app)
            self._loop.run_until_complete(self._runner.setup())
            site = web.TCPSite(self._runner, "127.0.0.1", 0)
            self._loop.run_until_complete(site.start())
            port = site._server.sockets[0].getsockname()[1]
            self.url = f"http://127.0.0.1:{port}"
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        assert ready.wait(10), "fake cloud did not start"
        return self

    def stop(self) -> None:
        async def shutdown():
            await self._runner.cleanup()

        asyncio.run_coroutine_threadsafe(shutdown(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(10)

    # -- request handling ----------------------------------------------------------------

    @web.middleware
    async def _middleware(self, request, handler):
        self.requests.append(Recorded(request.method, request.path, request.headers.get("Authorization")))
        if request.path.startswith("/v1"):
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if self.expected_key is not None and request.headers.get("Authorization") != f"Bearer {self.expected_key}":
                return web.json_response({"detail": "Invalid API key"}, status=401)
            if self.fail_status is not None and (self.fail_count is None or self.fail_count > 0):
                if self.fail_count is not None:
                    self.fail_count -= 1
                return web.json_response({"detail": f"fake failure {self.fail_status}"}, status=self.fail_status)
        return await handler(request)

    async def _handle(self, request: web.Request):
        parts = [p for p in request.path.split("/") if p]
        if request.path in ("/health", "/health/ready"):
            return web.json_response({"status": "healthy"})
        if request.path == "/version":
            return web.json_response({"api_version": "fake-cloud", "features": {}})
        if parts[:3] != ["v1", "default", "banks"]:
            return web.json_response({"detail": "not found"}, status=404)
        if len(parts) == 3 and request.method == "GET":
            return web.json_response({"banks": [], "total": 0, "limit": 1, "offset": 0})
        bank = parts[3]
        rest = parts[4:]
        if not rest and request.method == "PUT":
            self.banks.setdefault(bank, {})
            return web.json_response({"bank_id": bank, "name": bank, "mission": "",
                                      "disposition": {"skepticism": 3, "literalism": 3, "empathy": 3}})
        if not rest and request.method == "DELETE":
            self.banks.pop(bank, None)
            return web.json_response({"success": True})
        docs = self.banks.setdefault(bank, {})
        if rest == ["memories"] and request.method == "POST":
            body = await request.json()
            for item in body.get("items", []):
                doc = item.get("document_id") or f"doc-{next(self._ids)}"
                docs[doc] = {"id": f"mem-{next(self._ids)}", "text": item["content"], "fact_type": "world",
                             "document_id": doc, "metadata": item.get("metadata") or {},
                             "tags": item.get("tags") or [], "context": item.get("context")}
            return web.json_response({"success": True, "bank_id": bank, "items_count": len(body.get("items", [])),
                                      "async": False, "usage": {"input_tokens": 42}})
        if rest == ["memories", "list"] and request.method == "GET":
            doc = request.query.get("document_id")
            items = [r for d, r in docs.items() if doc is None or d == doc]
            return web.json_response({"items": items, "total": len(items), "limit": 100, "offset": 0})
        if rest == ["memories", "recall"] and request.method == "POST":
            results = [{"id": r["id"], "text": r["text"], "type": "world", "document_id": r["document_id"],
                        "metadata": r["metadata"], "tags": r["tags"]} for r in docs.values()]
            return web.json_response({"results": results})
        if rest[:1] == ["documents"] and request.method == "DELETE":
            existed = docs.pop(rest[1], None) is not None
            return web.json_response({"success": existed, "message": "ok", "document_id": rest[1],
                                      "memory_units_deleted": int(existed)}, status=200 if existed else 404)
        return web.json_response({"detail": "unsupported by fake"}, status=404)

    # -- helpers ----------------------------------------------------------------------

    def v1_requests(self) -> list[Recorded]:
        return [r for r in self.requests if r.path.startswith("/v1")]
