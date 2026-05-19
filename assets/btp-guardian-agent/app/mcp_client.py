"""
MCP Client for Agent Gateway Integration.

Loads MCP tools from Agent Gateway using volume mount or environment variable credentials with mTLS authentication.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)

_MCP_RETRY_ATTEMPTS = 4
_MCP_RETRY_DELAY = 4.0  # seconds


def _is_retryable_error(exc: Exception) -> bool:
    """Return True for transient errors that are worth retrying.

    Excludes client errors (HTTP 4xx) because those indicate a bad request
    that will not succeed on retry.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        # 4xx = client error â not retryable
        return exc.response.status_code < 400 or exc.response.status_code >= 500
    if isinstance(exc, (ExceptionGroup, BaseExceptionGroup)):
        # anyio task-group wraps transport/protocol errors â retryable
        return True
    # Network-level errors, timeouts, unexpected exceptions â retryable
    return True


# Resource name for token request
AGW_RESOURCE_NAME = "agent-gateway"
MCP_MAX_RESPONSE_CHARS = int(os.environ.get("MCP_MAX_RESPONSE_CHARS", 100_000))


@dataclass
class IntegrationDependency:
    """Represents a single MCP server integration dependency."""

    ord_id: str
    global_tenant_id: str

    @classmethod
    def from_dict(cls, data: dict) -> "IntegrationDependency":
        """Create an IntegrationDependency from a dictionary entry."""
        return cls(
            ord_id=data.get("ordId", ""),
            global_tenant_id=data.get("data", {}).get("globalTenantId", ""),
        )


@dataclass
class AgwCredentials:
    """Credentials for Agent Gateway authentication."""

    auth_type: str
    certificate: str
    client_id: str
    expires_at: str
    gateway_url: str
    private_key: str
    token_service_url: str
    uri: str
    integration_dependencies: list[IntegrationDependency] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "AgwCredentials":
        """Create credentials from dictionary."""
        raw_deps = data.get("integrationDependencies", [])
        integration_dependencies = [
            IntegrationDependency.from_dict(dep)
            for dep in raw_deps
            if dep.get("ordId") and dep.get("data", {}).get("globalTenantId")
        ]
        return cls(
            auth_type=data.get("authType", ""),
            certificate=data.get("certificate", ""),
            client_id=data.get("clientid", ""),
            expires_at=data.get("expiresAt", ""),
            gateway_url=data.get("gatewayUrl", ""),
            private_key=data.get("privateKey", ""),
            token_service_url=data.get("tokenServiceUrl", ""),
            uri=data.get("uri", ""),
            integration_dependencies=integration_dependencies,
        )

    def mcp_url(self, dependency: IntegrationDependency) -> str:
        """Build the MCP server URL for a given integration dependency."""
        return f"{self.gateway_url.rstrip('/')}/v1/mcp/{dependency.ord_id}/{dependency.global_tenant_id}"


def _abbreviate_server_name(server_label: str) -> str:
    """Derive a short abbreviation from a server label for use in tool names.

    Strips trailing '_mcp_demo' or '_demo', then takes the first letter of each
    remaining word. E.g. 'business_partner_mcp_demo' -> 'bp'.
    """
    name = server_label
    for suffix in ("_mcp_demo", "_demo"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return "".join(w[0] for w in name.split("_") if w)


@dataclass
class MCPTool:
    """Represents an MCP tool."""
    
    name: str
    server_name: str
    description: str
    input_schema: dict
    url: str
    
    @property
    def namespaced_name(self) -> str:
        """Get namespaced tool name, sanitized to match ^[a-zA-Z0-9-_]+$ and at most 64 chars.

        If the sanitized name exceeds 64 chars, it is truncated to 55 chars and an
        8-char sha256 suffix is appended (total 64), guaranteeing uniqueness.
        """
        raw = f"{self.server_name}__{self.name}"
        sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "_", raw)
        if len(sanitized) <= 64:
            return sanitized
        suffix = hashlib.sha256(sanitized.encode()).hexdigest()[:8]
        return f"{sanitized[:55]}_{suffix}"


# Path where UMS operator mounts credentials
UMS_CREDENTIALS_PATH = "/etc/ums/credentials/credentials"


def load_agw_credentials() -> AgwCredentials | None:
    """
    Load Agent Gateway credentials from volume mount or environment variable.
    
    Tries in order:
        1. Volume mount at /etc/ums/credentials/credentials (UMS operator)
        2. AGW_CREDENTIALS_JSON environment variable (fallback)
        
    Returns:
        AgwCredentials if credentials are present and valid, None otherwise
    """
    data = None
    source = None
    
    # Try volume mount first (UMS operator)
    if os.path.exists(UMS_CREDENTIALS_PATH):
        try:
            with open(UMS_CREDENTIALS_PATH, "r") as f:
                data = json.load(f)
            source = f"volume mount ({UMS_CREDENTIALS_PATH})"
            logger.info(f"Found credentials at {UMS_CREDENTIALS_PATH}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse credentials from {UMS_CREDENTIALS_PATH}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to read credentials from {UMS_CREDENTIALS_PATH}: {e}")
            return None
    
    # Fallback to environment variable
    if data is None:
        credentials_json = os.environ.get("AGW_CREDENTIALS_JSON", "")
        if credentials_json:
            try:
                data = json.loads(credentials_json)
                source = "AGW_CREDENTIALS_JSON environment variable"
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse AGW_CREDENTIALS_JSON: {e}")
                return None
    
    if data is None:
        logger.info("No AGW credentials found (neither volume mount nor environment variable) - MCP tools will not be available")
        return None
    
    try:
        credentials = AgwCredentials.from_dict(data)
        
        # Validate required fields
        if not credentials.gateway_url:
            logger.warning("AGW credentials missing required field (gatewayUrl)")
            return None
        
        if not credentials.client_id:
            logger.warning("AGW credentials missing required field (clientid)")
            return None
        
        if not credentials.certificate or not credentials.private_key or not credentials.token_service_url:
            logger.warning("AGW mTLS credentials incomplete (certificate, privateKey, tokenServiceUrl)")
            return None
        
        logger.info(f"Loaded AGW credentials from {source} for client_id: {credentials.client_id[:8]}...")
        if not credentials.integration_dependencies:
            logger.warning("AGW credentials contain no integrationDependencies - no MCP servers will be available")
        else:
            logger.info(f"Found {len(credentials.integration_dependencies)} integration dependency(ies): "
                        f"{[dep.ord_id for dep in credentials.integration_dependencies]}")
        return credentials
        
    except Exception as e:
        logger.error(f"Failed to load AGW credentials: {e}")
        return None


async def get_oauth_token(credentials: AgwCredentials) -> str:
    """
    Get OAuth token using mTLS authentication.
    
    Args:
        credentials: AGW credentials with certificate and private key
        
    Returns:
        Bearer token string
        
    Raises:
        Exception if token retrieval fails
    """
    # Write certificate and key to temporary files for mTLS
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as cert_file:
        cert_file.write(credentials.certificate)
        cert_path = cert_file.name
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as key_file:
        key_file.write(credentials.private_key)
        key_path = key_file.name
    
    try:
        # Create SSL context with client certificate
        async with httpx.AsyncClient(
            cert=(cert_path, key_path),
            timeout=30.0,
        ) as client:
            response = await client.post(
                credentials.token_service_url,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "client_id": credentials.client_id,
                    "grant_type": "client_credentials",
                    "resource": f"urn:sap:identity:application:provider:name:{AGW_RESOURCE_NAME}",
                },
            )
            response.raise_for_status()
            token_data = response.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise ValueError("No access_token in response")
            logger.info("Successfully obtained OAuth token from IAS")
            return f"Bearer {access_token}"
    finally:
        # Clean up temporary files
        try:
            os.unlink(cert_path)
            os.unlink(key_path)
        except Exception:
            pass


class MCPClient:
    """
    Client for discovering and calling MCP tools via Agent Gateway.
    
    Uses volume mount credentials with mTLS authentication.
    """
    
    def __init__(self, credentials: AgwCredentials | None = None):
        """
        Initialize MCP client.
        
        Args:
            credentials: AGW credentials, or None to load from default path
        """
        self.credentials = credentials or load_agw_credentials()
        self._cached_token: str | None = None
    
    async def _get_auth_header(self) -> str:
        """Get authorization header, fetching a fresh token each time."""
        if not self.credentials:
            raise ValueError("No AGW credentials available")
        
        # Always fetch a fresh token (no caching for now)
        logger.debug("Fetching fresh OAuth token...")
        token = await get_oauth_token(self.credentials)
        logger.debug("OAuth token obtained successfully")
        return token

    async def get_mcp_tools(self, mcp_server_filter: list[str] | None = None) -> list[MCPTool]:
        """
        Discover available MCP tools from all MCP servers in Agent Gateway.

        Args:
            mcp_server_filter: Optional list of ordIds to load. When provided, only
                dependencies whose ordId appears in this list are contacted.
                When None (default), all integration dependencies are loaded.

        Returns:
            List of MCPTool objects from matching integration dependencies
        """
        if not self.credentials:
            logger.warning("No AGW credentials available - skipping MCP tool discovery")
            return []

        if not self.credentials.integration_dependencies:
            logger.warning("No integrationDependencies in credentials - skipping MCP tool discovery")
            return []

        if mcp_server_filter is not None:
            filter_set = set(mcp_server_filter)
            dependencies = [
                dep for dep in self.credentials.integration_dependencies
                if dep.ord_id in filter_set
            ]
            skipped = [
                dep.ord_id for dep in self.credentials.integration_dependencies
                if dep.ord_id not in filter_set
            ]
            if skipped:
                logger.info(f"Skipping {len(skipped)} integration dependency(ies) not in mcp_server_filter: {skipped}")
        else:
            dependencies = self.credentials.integration_dependencies

        all_tools: list[MCPTool] = []

        for dependency in dependencies:
            mcp_url = self.credentials.mcp_url(dependency)
            last_exc: Exception | None = None
            for attempt in range(1 + _MCP_RETRY_ATTEMPTS):
                try:
                    auth_header = await self._get_auth_header()

                    async with httpx.AsyncClient(
                            headers={"Authorization": auth_header},
                            timeout=30.0,
                    ) as http_client:
                        async with streamable_http_client(mcp_url, http_client=http_client) as (read, write, _):
                            async with ClientSession(read, write) as session:
                                init_result = await session.initialize()
                                if init_result is None:
                                    raise RuntimeError(
                                        f"MCP session.initialize() returned None for {dependency.ord_id} "
                                        f"at URL {mcp_url} â server may be unavailable or returned a non-MCP response"
                                    )
                                ord_parts = dependency.ord_id.split(":")
                                server_label = ord_parts[-2] if len(ord_parts) >= 2 else dependency.ord_id
                                server_name = _abbreviate_server_name(server_label)

                                result = await session.list_tools()
                                tools = [
                                    MCPTool(
                                        name=t.name,
                                        server_name=server_name,
                                        description=f"[{server_label}] {t.description or ''}".strip(),
                                        input_schema=t.inputSchema or {},
                                        url=mcp_url,
                                    )
                                    for t in result.tools
                                ]

                    logger.info(f"Discovered {len(tools)} MCP tool(s) from {dependency.ord_id}")
                    all_tools.extend(tools)
                    last_exc = None
                    break

                except Exception as e:
                    if not _is_retryable_error(e):
                        logger.exception(f"Failed to discover MCP tools from {dependency.ord_id} (non-retryable): {e}")
                        last_exc = None  # already logged
                        break
                    last_exc = e
                    if attempt < _MCP_RETRY_ATTEMPTS:
                        logger.warning(
                            f"Failed to discover MCP tools from {dependency.ord_id} "
                            f"(attempt {attempt + 1}/{1 + _MCP_RETRY_ATTEMPTS}), retrying in {_MCP_RETRY_DELAY}s: {e}"
                        )
                        await asyncio.sleep(_MCP_RETRY_DELAY)

            if last_exc is not None:
                logger.exception(
                    f"Failed to discover MCP tools from {dependency.ord_id} after {1 + _MCP_RETRY_ATTEMPTS} attempts",
                    exc_info=last_exc,
                )

        logger.info(f"Total MCP tools discovered across all servers: {len(all_tools)}")
        return all_tools

    async def call_tool(self, tool: MCPTool, **kwargs) -> str:
        """
        Call an MCP tool.
        
        Args:
            tool: The MCPTool to call
            **kwargs: Tool arguments
            
        Returns:
            Tool result as string
        """
        logger.info(f"call_tool START: tool={tool.name}, args={kwargs}")

        if not self.credentials:
            logger.error("call_tool: No AGW credentials available")
            raise ValueError("No AGW credentials available")

        last_exc: Exception | None = None
        for attempt in range(1 + _MCP_RETRY_ATTEMPTS):
            try:
                logger.info("call_tool: Getting auth header...")
                auth_header = await self._get_auth_header()
                logger.info("call_tool: Auth header obtained")

                logger.info(f"call_tool: Creating HTTP client for URL: {tool.url}")
                # Capture result outside context managers so that a server-side
                # connection close during teardown doesn't discard a good result.
                _call_result = None
                try:
                    async with httpx.AsyncClient(
                        headers={"Authorization": auth_header},
                        timeout=60.0,
                    ) as http_client:
                        logger.info("call_tool: Opening streamable_http_client...")
                        async with streamable_http_client(tool.url, http_client=http_client) as (read, write, _):
                            logger.info("call_tool: Creating ClientSession...")
                            async with ClientSession(read, write) as session:
                                logger.info("call_tool: Calling session.initialize()...")
                                init_result = await session.initialize()
                                if init_result is None:
                                    raise RuntimeError(
                                        f"MCP session.initialize() returned None for tool URL {tool.url} â "
                                        "the server may be unavailable or returned a non-MCP response"
                                    )
                                logger.info(f"call_tool: Initialize complete, calling session.call_tool({tool.name}, {kwargs})...")
                                _call_result = await session.call_tool(tool.name, kwargs)
                                logger.info(f"call_tool: session.call_tool returned, result={_call_result}")
                except (ExceptionGroup, BaseExceptionGroup) as eg:
                    # The MCP server closes the connection after sending the
                    # response; anyio wraps that teardown race in an
                    # ExceptionGroup.  If we already captured a result, the
                    # call succeeded â suppress the teardown noise.
                    if _call_result is None:
                        raise
                    logger.debug(
                        f"call_tool: Ignoring ExceptionGroup on teardown for {tool.name} "
                        f"(result already captured): {eg}"
                    )

                if _call_result is None:
                    raise RuntimeError(
                        f"call_tool: session.call_tool returned None for {tool.name} â no result captured"
                    )

                response = str(_call_result.content[0].text if _call_result.content else "")
                if len(response) > MCP_MAX_RESPONSE_CHARS:
                    logger.warning(
                        f"call_tool: Response from {tool.name} truncated from "
                        f"{len(response)} to {MCP_MAX_RESPONSE_CHARS} chars to prevent OOM"
                    )
                    response = response[:MCP_MAX_RESPONSE_CHARS] + """
...[truncated]"""
                logger.info(f"call_tool END: returning response (length={len(response)})")
                return response

            except Exception as e:
                if not _is_retryable_error(e):
                    logger.exception(f"call_tool: Non-retryable error calling {tool.name}: {e}")
                    raise
                last_exc = e
                if attempt < _MCP_RETRY_ATTEMPTS:
                    logger.warning(
                        f"call_tool: Error calling {tool.name} "
                        f"(attempt {attempt + 1}/{1 + _MCP_RETRY_ATTEMPTS}), retrying in {_MCP_RETRY_DELAY}s: {e}"
                    )
                    await asyncio.sleep(_MCP_RETRY_DELAY)

        logger.exception(f"call_tool: Failed to call {tool.name} after {1 + _MCP_RETRY_ATTEMPTS} attempts", exc_info=last_exc)
        raise last_exc  # type: ignore[misc]


class MCPToolConverter:
    """
    Converter for transforming MCP tools to various framework formats.
    
    Provides methods to convert MCPTool objects to tools compatible with
    different agent frameworks like LangChain.
    """
    
    def __init__(self, mcp_client: MCPClient):
        """
        Initialize the converter.
        
        Args:
            mcp_client: MCP client for tool execution
        """
        self.mcp_client = mcp_client
    
    def to_langchain(self, mcp_tool: MCPTool) -> "StructuredTool":
        """
        Convert an MCP tool to a LangChain StructuredTool.
        
        Args:
            mcp_tool: The MCP tool to convert
            
        Returns:
            LangChain StructuredTool
        """
        from langchain_core.tools import StructuredTool
        from pydantic import create_model
        
        mcp_client = self.mcp_client
        
        async def run(**kwargs) -> str:
            return await mcp_client.call_tool(mcp_tool, **kwargs)
        
        # Build args schema from input_schema
        properties = mcp_tool.input_schema.get("properties", {})
        required = set(mcp_tool.input_schema.get("required", []))
        
        fields = {}
        for name, prop in properties.items():
            # Map JSON schema types to Python types
            prop_type = prop.get("type", "string")
            python_type = str  # Default to string
            if prop_type == "integer":
                python_type = int
            elif prop_type == "number":
                python_type = float
            elif prop_type == "boolean":
                python_type = bool
            
            # Required fields use ... (Ellipsis), optional use None default
            if name in required:
                fields[name] = (python_type, ...)
            else:
                fields[name] = (python_type | None, None)
        
        args_schema = create_model(f"{mcp_tool.name}_args", **fields) if fields else None
        
        return StructuredTool.from_function(
            coroutine=run,
            name=mcp_tool.namespaced_name,
            description=mcp_tool.description,
            args_schema=args_schema,
        )
