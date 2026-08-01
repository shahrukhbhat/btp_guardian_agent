"""Resolve SAP AI Core credentials from a BTP destination and create an LLM.

Single helper: init_llm_from_destination(). Reads the destination named
AICORE_DESTINATION_NAME (default 'aicore') via the destination service binding
(same binding used by the BTP API clients), pushes AICORE_* env vars that
gen_ai_hub reads, and returns a LangChain BaseChatModel via init_llm.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from gen_ai_hub.proxy import set_proxy_version
from gen_ai_hub.proxy.core import get_proxy_client
from gen_ai_hub.proxy.langchain.init_models import init_llm

from api_client import _DestinationResolver, _first_binding

logger = logging.getLogger(__name__)

set_proxy_version("gen-ai-hub")

AICORE_DESTINATION_ENV = "AICORE_DESTINATION_NAME"
DEFAULT_AICORE_DESTINATION = "aicore"


async def _fetch_destination_raw(name: str) -> dict[str, Any]:
    creds = _first_binding("destination") or {}
    if not creds:
        raise RuntimeError("No 'destination' service binding found in VCAP_SERVICES.")
    resolver = _DestinationResolver()
    token = await resolver._xsuaa_access_token()  # noqa: SLF001
    uri = creds["uri"].rstrip("/")
    url = f"{uri}/destination-configuration/v1/destinations/{name}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if r.status_code >= 400:
        raise RuntimeError(
            f"Destination service returned {r.status_code} for '{name}': {r.text}"
        )
    return r.json()


async def init_llm_from_destination(
    model_name: str,
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    destination_name: str | None = None,
) -> Any:
    name = destination_name or os.environ.get(AICORE_DESTINATION_ENV, DEFAULT_AICORE_DESTINATION)

    if not os.environ.get("AICORE_BASE_URL"):
        payload = await _fetch_destination_raw(name)
        cfg = payload.get("destinationConfiguration") or {}

        base_url = (cfg.get("URL") or "").rstrip("/")
        client_id = cfg.get("clientId") or ""
        client_secret = cfg.get("clientSecret") or ""
        token_url = cfg.get("tokenServiceURL") or ""
        resource_group = (
            cfg.get("URL.headers.AI-Resource-Group")
            or cfg.get("AI_RESOURCE_GROUP")
            or "default"
        )

        if not (base_url and client_id and client_secret and token_url):
            raise RuntimeError(
                f"Destination '{name}' is missing one of URL / clientId / "
                f"clientSecret / tokenServiceURL. Got keys: {sorted(cfg.keys())}"
            )

        os.environ["AICORE_BASE_URL"] = base_url
        os.environ["AICORE_AUTH_URL"] = token_url
        os.environ["AICORE_CLIENT_ID"] = client_id
        os.environ["AICORE_CLIENT_SECRET"] = client_secret
        os.environ["AICORE_RESOURCE_GROUP"] = resource_group
        logger.info(
            "aicore destination '%s' resolved (base=%s, group=%s)",
            name, base_url, resource_group,
        )

    kwargs: dict[str, Any] = {"temperature": temperature}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    # SDK 7.x's init_llm no longer lazily initializes the proxy client when the
    # proxy_client kwarg is omitted (it passes None straight through and crashes).
    # Pass it explicitly — harmless on older SDKs that accepted the kwarg too.
    proxy_client = get_proxy_client()

    # Anthropic/Claude models on Bedrock reject `temperature` and `top_p` together,
    # but the SDK's init_llm always injects BOTH (top_p defaults to 1.0 with no way
    # to suppress it). Construct ChatBedrock directly with a single sampling param.
    if model_name.startswith("anthropic"):
        from gen_ai_hub.proxy.langchain.amazon import ChatBedrock

        deployment = proxy_client.select_deployment(model_name=model_name)
        model_kwargs: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            model_kwargs["max_tokens"] = max_tokens
        return ChatBedrock(
            model_name=deployment.model_name,
            deployment_id=deployment.deployment_id,
            proxy_client=proxy_client,
            model_kwargs=model_kwargs,
        )

    return init_llm(model_name, proxy_client=proxy_client, **kwargs)
