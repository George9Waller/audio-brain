"""The audio-brain API, called with MCP_TOKEN so everything Claude does is attributed to "claude"."""

import httpx


class ApiError(Exception):
    pass


class Api:
    def __init__(self, config, transport=None):
        self._client = httpx.AsyncClient(base_url=config.api_url, timeout=120, transport=transport,
                                         headers={"Authorization": f"Bearer {config.mcp_token}"})

    async def get(self, path, **params):
        return await self._send("GET", path, params=params)

    async def post(self, path, body=None):
        return await self._send("POST", path, json=body or {})

    async def patch(self, path, body=None):
        return await self._send("PATCH", path, json=body or {})

    async def delete(self, path):
        return await self._send("DELETE", path)

    async def _send(self, method, path, **kwargs):
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(f"audio-brain said {response.status_code}: {detail}")
        return response.json() if response.content else None
