"""Test helpers: fake BTP API clients for unit tests.

Replace the MCP tool fixtures used on Joule with FakeClient instances that
capture calls and return canned responses. This allows all tool tests to run
without any network access or VCAP_SERVICES binding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FakeCall:
    method: str
    path: str
    params: dict
    user_identity: str | None
    body: dict | None = None
    service_root: str | None = None


@dataclass
class FakeClient:
    responses: dict[str, Any] = field(default_factory=dict)
    calls: list[FakeCall] = field(default_factory=list)

    def _resolve(self, call: FakeCall) -> dict:
        for needle, payload in self.responses.items():
            if needle in call.path:
                if callable(payload):
                    return payload(call)
                return dict(payload) if isinstance(payload, dict) else payload
        return {}

    async def get(
        self,
        service_path: str,
        params: dict[str, Any] | None = None,
        user_identity: str | None = None,
    ) -> dict[str, Any]:
        call = FakeCall("GET", service_path, dict(params or {}), user_identity)
        self.calls.append(call)
        return self._resolve(call)

    async def post(
        self,
        service_path: str,
        body: dict[str, Any],
        service_root: str = "",
        user_identity: str | None = None,
    ) -> dict[str, Any]:
        call = FakeCall("POST", service_path, {}, user_identity, body, service_root)
        self.calls.append(call)
        return self._resolve(call)


def make_fake(responses: dict[str, Any] | None = None) -> FakeClient:
    """Create a FakeClient pre-loaded with canned responses.

    Keys in `responses` are needle strings matched against the service_path
    with `if needle in call.path`. Values may be dicts or callables that
    accept a FakeCall and return a dict.

    Example:
        fake = make_fake({"/accounts/v1/subaccounts": {"value": [{"guid": "sa-001"}]}})
    """
    return FakeClient(responses=dict(responses or {}))
