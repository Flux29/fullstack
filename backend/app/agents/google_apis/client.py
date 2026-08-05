"""Shared, token-safe HTTP client for direct Google REST APIs."""

from __future__ import annotations

import asyncio
import json as json_lib
from collections.abc import AsyncIterator
from typing import Any

import httpx

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_RETRYABLE = {429, 500, 502, 503, 504}


class GoogleApiClient:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        expect_bytes: bool = False,
        max_output_chars: int = 200_000,
    ) -> Any:
        request_headers = {"Authorization": f"Bearer {self.access_token}"}
        if headers:
            request_headers.update(headers)
        method = method.upper()
        attempts = 3 if method in {"GET", "HEAD"} else 1
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for attempt in range(attempts):
                response = await client.request(
                    method,
                    url,
                    headers=request_headers,
                    params=params,
                    json=json,
                    content=content,
                )
                if response.status_code not in _RETRYABLE or attempt + 1 >= attempts:
                    break
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 5.0) if retry_after else 0.25 * 2**attempt
                except ValueError:
                    delay = 0.25 * 2**attempt
                await asyncio.sleep(delay)
        if response.is_error:
            detail: str | None = None
            try:
                error = response.json().get("error", {})
                detail = error.get("message")
                reason = next(
                    (item.get("reason") for item in error.get("errors", []) if item.get("reason")),
                    None,
                )
                if reason and detail:
                    detail = f"{detail} ({reason})"
            except Exception:
                pass
            raise RuntimeError(detail or f"Google API returned HTTP {response.status_code}")
        if expect_bytes:
            return response.content
        if not response.content:
            return {}
        result = response.json()
        encoded = json_lib.dumps(result, ensure_ascii=False, default=str)
        if len(encoded) > max_output_chars:
            return {
                "truncated": True,
                "content": trim_text(encoded, max_output_chars),
            }
        return result

    async def pages(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        max_pages: int = 20,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield bounded Google list pages while following nextPageToken."""
        page_params = dict(params or {})
        for _ in range(clamp(max_pages, 1, 100)):
            page = await self.request("GET", url, params=page_params)
            if not isinstance(page, dict):
                return
            yield page
            token = page.get("nextPageToken")
            if not token:
                return
            page_params["pageToken"] = token


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def trim_text(value: str, limit: int = 50_000) -> str:
    return value if len(value) <= limit else value[:limit] + "\n…[truncated]"
