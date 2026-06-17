from __future__ import annotations

import anyio
import httpx
import fastapi.testclient


class _CompatTestClient:
    """Sync test client for environments where Starlette expects httpx2."""

    __test__ = False

    def __init__(self, app, base_url: str = "http://testserver", **_: object) -> None:
        self.app = app
        self.base_url = base_url

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        async def _request() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url=self.base_url,
            ) as client:
                return await client.request(method, url, **kwargs)

        return anyio.run(_request)

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def __enter__(self) -> "_CompatTestClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


fastapi.testclient.TestClient = _CompatTestClient
