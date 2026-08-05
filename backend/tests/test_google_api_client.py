"""Shared behavior for generally available Google REST integrations."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.agents.google_apis import client as google_client


class _FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.request = AsyncMock(side_effect=lambda *args, **kwargs: next(self.responses))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _response(status: int, body: dict, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, json=body, headers=headers, request=httpx.Request("GET", "https://x")
    )


@pytest.mark.anyio
async def test_idempotent_reads_retry_but_mutations_do_not(monkeypatch):
    read_client = _FakeClient(
        [_response(503, {"error": {"message": "busy"}}), _response(200, {"ok": True})]
    )
    monkeypatch.setattr(google_client.httpx, "AsyncClient", lambda **kwargs: read_client)
    monkeypatch.setattr(google_client.asyncio, "sleep", AsyncMock())

    assert await google_client.GoogleApiClient("secret").request("GET", "https://x") == {"ok": True}
    assert read_client.request.await_count == 2

    write_client = _FakeClient([_response(503, {"error": {"message": "busy"}})])
    monkeypatch.setattr(google_client.httpx, "AsyncClient", lambda **kwargs: write_client)
    with pytest.raises(RuntimeError, match="busy"):
        await google_client.GoogleApiClient("secret").request("POST", "https://x", json={})
    assert write_client.request.await_count == 1


@pytest.mark.anyio
async def test_error_is_sanitized_and_never_contains_access_token(monkeypatch):
    fake = _FakeClient(
        [
            _response(
                403,
                {
                    "error": {
                        "message": "Caller lacks permission",
                        "errors": [{"reason": "forbidden"}],
                    }
                },
            )
        ]
    )
    monkeypatch.setattr(google_client.httpx, "AsyncClient", lambda **kwargs: fake)

    with pytest.raises(RuntimeError) as error:
        await google_client.GoogleApiClient("super-secret-token").request("GET", "https://x")

    assert "Caller lacks permission (forbidden)" in str(error.value)
    assert "super-secret-token" not in str(error.value)


@pytest.mark.anyio
async def test_pages_follows_next_page_token(monkeypatch):
    api = google_client.GoogleApiClient("token")
    api.request = AsyncMock(side_effect=[{"items": [1], "nextPageToken": "next"}, {"items": [2]}])

    pages = [page async for page in api.pages("https://x", params={"pageSize": 1})]

    assert pages == [{"items": [1], "nextPageToken": "next"}, {"items": [2]}]
    assert api.request.await_args_list[1].kwargs["params"]["pageToken"] == "next"


@pytest.mark.anyio
async def test_large_json_responses_are_bounded(monkeypatch):
    fake = _FakeClient([_response(200, {"value": "x" * 100})])
    monkeypatch.setattr(google_client.httpx, "AsyncClient", lambda **kwargs: fake)

    result = await google_client.GoogleApiClient("token").request(
        "GET", "https://x", max_output_chars=40
    )

    assert result["truncated"] is True
    assert len(result["content"]) < 80
