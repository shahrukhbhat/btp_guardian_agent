"""Unit tests for the BTP REST client (api_client.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_app_path = str(Path(__file__).parent.parent / "app")
if _app_path not in sys.path:
    sys.path.insert(0, _app_path)

import api_client as _mod
from api_client import Client, Destination, _DestinationResolver, _first_binding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VCAP_DEST = json.dumps({
    "destination": [
        {
            "credentials": {
                "clientid": "my-client-id",
                "clientsecret": "my-secret",
                "url": "https://token.example.com",
                "uri": "https://destination-service.example.com",
            }
        }
    ]
})

VCAP_EMPTY = json.dumps({})


def _stub_response(status_code: int, body: dict | str) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    if isinstance(body, dict):
        r.json.return_value = body
        r.text = json.dumps(body)
    else:
        r.json.side_effect = ValueError("not JSON")
        r.text = body
    r.headers = {}
    return r


# ---------------------------------------------------------------------------
# _first_binding
# ---------------------------------------------------------------------------


def test_first_binding_returns_none_when_vcap_empty(monkeypatch):
    monkeypatch.setenv("VCAP_SERVICES", VCAP_EMPTY)
    assert _first_binding("destination") is None


def test_first_binding_returns_none_when_vcap_invalid(monkeypatch):
    monkeypatch.setenv("VCAP_SERVICES", "NOT JSON{{")
    assert _first_binding("destination") is None


def test_first_binding_returns_credentials(monkeypatch):
    monkeypatch.setenv("VCAP_SERVICES", VCAP_DEST)
    creds = _first_binding("destination")
    assert creds["clientid"] == "my-client-id"


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------


def test_build_url_adds_leading_slash():
    c = Client(
        destination=Destination(url="https://api.example.com", auth_type="NoAuthentication"),
        destination_name="TEST",
    )
    assert c._build_url("https://api.example.com", "some/path") == "https://api.example.com/some/path"


def test_build_url_no_double_slash():
    c = Client(
        destination=Destination(url="https://api.example.com", auth_type="NoAuthentication"),
        destination_name="TEST",
    )
    assert c._build_url("https://api.example.com", "/some/path") == "https://api.example.com/some/path"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_auth_returns_none_for_no_auth():
    dest = Destination(url="https://x", auth_type="NoAuthentication")
    c = Client(destination=dest, destination_name="TEST")
    assert c._auth(dest) is None


def test_auth_returns_basic_for_basic_authentication():
    dest = Destination(
        url="https://x",
        auth_type="BasicAuthentication",
        username="user",
        password="pass",
    )
    c = Client(destination=dest, destination_name="TEST")
    auth = c._auth(dest)
    assert auth is not None
    # Verify it is an httpx.BasicAuth instance (credentials are encoded internally)
    import httpx as _httpx
    assert isinstance(auth, _httpx.BasicAuth)


# ---------------------------------------------------------------------------
# X-User-Identity header injection
# ---------------------------------------------------------------------------


def test_base_headers_with_user_identity():
    c = Client(
        destination=Destination(url="https://x", auth_type="NoAuthentication"),
        destination_name="TEST",
    )
    h = c._base_headers("alice@example.com")
    assert h["X-User-Identity"] == "alice@example.com"


def test_base_headers_without_user_identity():
    c = Client(
        destination=Destination(url="https://x", auth_type="NoAuthentication"),
        destination_name="TEST",
    )
    h = c._base_headers(None)
    assert "X-User-Identity" not in h


# ---------------------------------------------------------------------------
# get() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_unwrapped_rest_json():
    dest = Destination(url="https://api.example.com", auth_type="NoAuthentication")
    c = Client(destination=dest, destination_name="TEST")

    mock_resp = _stub_response(200, {"value": [{"id": "sa-001"}]})

    class _StubAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, *_args, **_kwargs):
            return mock_resp

    with patch.object(_mod, "httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = _StubAsyncClient()
        mock_httpx.BasicAuth = __import__("httpx").BasicAuth
        result = await c.get("/accounts/v1/subaccounts")

    assert result == {"value": [{"id": "sa-001"}]}


@pytest.mark.asyncio
async def test_get_returns_error_dict_on_4xx():
    dest = Destination(url="https://api.example.com", auth_type="NoAuthentication")
    c = Client(destination=dest, destination_name="TEST")

    mock_resp = _stub_response(404, "Not Found")

    class _StubAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, *_args, **_kwargs):
            return mock_resp

    with patch.object(_mod, "httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = _StubAsyncClient()
        mock_httpx.BasicAuth = __import__("httpx").BasicAuth
        result = await c.get("/accounts/v1/subaccounts")

    assert result["error"] is True
    assert result["status_code"] == 404


@pytest.mark.asyncio
async def test_get_handles_non_json_2xx():
    dest = Destination(url="https://api.example.com", auth_type="NoAuthentication")
    c = Client(destination=dest, destination_name="TEST")

    mock_resp = _stub_response(200, "NOT JSON BODY")

    class _StubAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, *_args, **_kwargs):
            return mock_resp

    with patch.object(_mod, "httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = _StubAsyncClient()
        mock_httpx.BasicAuth = __import__("httpx").BasicAuth
        result = await c.get("/some/path")

    assert result["error"] is True
    assert result["status_code"] == 200


# ---------------------------------------------------------------------------
# Lazy destination resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lazy_resolution_called_once_across_multiple_calls(monkeypatch):
    monkeypatch.setenv("VCAP_SERVICES", VCAP_DEST)

    dest = Destination(url="https://api.example.com", auth_type="NoAuthentication")
    mock_resolver = MagicMock()
    mock_resolver.resolve = AsyncMock(return_value=dest)

    c = Client(destination_name="BTP_ACCOUNTS", resolver=mock_resolver)

    mock_resp = _stub_response(200, {})

    class _StubAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, *_args, **_kwargs):
            return mock_resp

    with patch.object(_mod, "httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = _StubAsyncClient()
        mock_httpx.BasicAuth = __import__("httpx").BasicAuth
        await c.get("/path1")
        await c.get("/path2")
        await c.get("/path3")

    # Resolver must be called exactly once even for 3 requests
    assert mock_resolver.resolve.call_count == 1


# ---------------------------------------------------------------------------
# post() — no CSRF for BTP REST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_does_not_send_csrf_header():
    dest = Destination(url="https://api.example.com", auth_type="NoAuthentication")
    c = Client(destination=dest, destination_name="TEST")

    captured_headers = {}

    mock_resp = _stub_response(201, {"id": "new-env-001"})

    class _StubAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, *_args, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            return mock_resp

    with patch.object(_mod, "httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = _StubAsyncClient()
        mock_httpx.BasicAuth = __import__("httpx").BasicAuth
        result = await c.post("/provisioning/v1/environments", body={"name": "kyma"})

    assert "x-csrf-token" not in captured_headers
    assert result == {"id": "new-env-001"}
